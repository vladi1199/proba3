import csv
import os
import re
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Зареждаме променливите от .env файл (ако има)
load_dotenv()

base_path = os.path.dirname(os.path.abspath(__file__))

# ---------------------------
# WebDriver
# ---------------------------
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--lang=bg-BG,bg")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    return webdriver.Chrome(options=options)

def click_cookies_if_any(driver):
    for how, what in [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(., 'Приемам')]"),
        (By.XPATH, "//*[contains(., 'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(., 'Accept')]"),
    ]:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, what))).click()
            break
        except Exception:
            pass

def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

def _variants(sku: str):
    a = _norm(sku)
    b = a.lstrip("0") or a
    return [a] if a == b else [a, b]

# -----------------------------------------
# Намираме URL на продукта по SKU
# -----------------------------------------
def find_product_url(driver, sku):
    """
    1) Търси директно <a href="... ?sku=<код>"> и връща него.
    2) Ако няма, взема продуктови линкове от резултатите и за всеки:
       - пробва href както е;
       - пробва href със зададен query параметър ?sku=<код> / &sku=<код>;
       - валидира на страницата, че се вижда ред/клетка за този SKU.
    """
    SEARCH_URLS = [
        "https://filstar.com/search?term={q}",
        "https://filstar.com/bg/search?term={q}",
    ]

    LINK_SELECTORS = [
        "a[href*='?sku=']",
        ".product-item a[href]",
        ".products a[href]",
        ".search-results a[href]",
        "a.product-item-link",
        "a[href^='/products/']",
        "a[href*='/products/']",
        "a[href^='https://filstar.com/products/']",
    ]

    def collect_links():
        hrefs, seen = [], set()
        for sel in LINK_SELECTORS:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href = el.get_attribute("href") or ""
                if not href:
                    continue
                if "/search?term=" in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                hrefs.append(href)
        return hrefs

    def page_matches(driver, q):
        # 1) старият клас
        if driver.find_elements(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']"):
            return True
        # 2) клетка/таблица с „КОД“ съдържаща SKU
        if driver.find_elements(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{q}')]]"):
            return True
        return False

    def with_sku_param(href, q):
        """Добавя/заменя ?sku=<q> в даден URL."""
        u = urlparse(href)
        qs = parse_qs(u.query)
        qs["sku"] = [q]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))

    for q in _variants(sku):
        for tmpl in SEARCH_URLS:
            driver.get(tmpl.format(q=q))
            click_cookies_if_any(driver)

            # изчакай резултати/карти
            try:
                WebDriverWait(driver, 12).until(
                    EC.any_of(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".search-results, .products, .product-item, a[href*='?sku=']")),
                        EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                    )
                )
            except Exception:
                time.sleep(1.0)

            links = collect_links()
            if not links:
                continue

            # 1) ако имаме директно линк с ?sku=<q> — вземи него
            for h in links:
                if f"?sku={q}" in h or f"&sku={q}" in h:
                    return h

            # 2) иначе обходи кандидати и валидирай
            #    (пробвай и същия href, и href със зададен ?sku=)
            for href in links[:30]:
                for candidate in (href, with_sku_param(href, q)):
                    try:
                        driver.get(candidate)
                        time.sleep(0.6)
                        if page_matches(driver, q):
                            return candidate
                    except Exception:
                        continue

    return None

# ---------------------------------------------------
# Проверка на наличността, бройката и цената
# ---------------------------------------------------
def check_availability_and_price(driver, sku):
    try:
        row = None
        try:
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception:
            try:
                row = driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{_norm(sku)}')]]")
            except Exception as e2:
                print(f"❌ Не беше намерен ред с SKU {sku}: {e2}")
                return None, 0, None

        qty = 0
        try:
            qty_input = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
            mx = qty_input.get_attribute("data-max-qty-1") or qty_input.get_attribute("max")
            if mx and mx.isdigit():
                qty = int(mx)
        except Exception:
            pass
        status = "Наличен" if qty > 0 else "Изчерпан"

        price = None
        try:
            price_element = row.find_element(By.CSS_SELECTOR, "div.custom-tooltip-holder")
            try:
                strike = price_element.find_element(By.TAG_NAME, "strike")
                m = re.search(r"(\d+[.,]\d{2})", strike.text)
                if m:
                    price = m.group(1).replace(",", ".")
            except Exception:
                m = re.search(r"(\d+[.,]\d{2})", price_element.text)
                if m:
                    price = m.group(1).replace(",", ".")
        except Exception:
            m = re.search(r"(\d+[.,]\d{2})\s*лв", row.text.replace("\xa0", " "))
            if m:
                price = m.group(1).replace(",", ".")

        return status, qty, price

    except Exception as e:
        print(f"❌ Грешка при проверка на наличността и цената за SKU {sku}: {e}")
        return None, 0, None

# -----------------------
# CSV I/O
# -----------------------
def read_sku_codes(path):
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        next(r, None)
        return [row[0].strip() for row in r if row and row[0].strip()]

def save_results(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Наличност", "Бройки", "Цена"])
        w.writerows(rows)

def save_not_found(skus, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU"])
        for s in skus:
            w.writerow([s])

# -----------------------
# main
# -----------------------
def main():
    sku_file = os.path.join(base_path, "sku_list_filstar.csv")
    result_file = os.path.join(base_path, "results_filstar.csv")
    not_found_file = os.path.join(base_path, "not_found_filstar.csv")

    skus = read_sku_codes(sku_file)
    driver = create_driver()

    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")
            product_url = find_product_url(driver, sku)
            if not product_url:
                print(f"❌ Няма валиден продукт за SKU {sku}")
                not_found.append(sku)
                continue

            print(f"  ✅ Намерен продукт: {product_url}")
            driver.get(product_url)
            time.sleep(0.6)
            status, qty, price = check_availability_and_price(driver, sku)
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  📦 Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
    finally:
        driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)
    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == "__main__":
    main()
