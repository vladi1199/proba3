import csv
import os
import re
import time
import pathlib
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Зареждаме променливите от .env файл (ако има)
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ART_DIR = pathlib.Path(BASE_DIR) / "artifacts"
ART_DIR.mkdir(exist_ok=True)

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
    # anti-bot дреболии
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=options)

def save_debug(driver, name):
    try:
        png = ART_DIR / f"{name}.png"
        html = ART_DIR / f"{name}.html"
        driver.save_screenshot(str(png))
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass

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

def _slow_scroll(driver, steps=6, pause=0.25):
    for i in range(steps):
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight/%s);" % steps)
        time.sleep(pause)

# -----------------------------------------
# Намираме URL на продукта по SKU
# -----------------------------------------
def find_product_url(driver, sku):
    SEARCH_URLS = [
        "https://filstar.com/search?term={q}",
        "https://filstar.com/bg/search?term={q}",
    ]

    LINK_SELECTORS = [
        ".search-results a[href]",
        ".products a[href]",
        ".product-item a[href]",
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
                if any(bad in href for bad in ["/products/new", "/search?term="]):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                hrefs.append(href)
        return hrefs[:50]

    def page_matches(driver, q):
        # 1) старият клас
        if driver.find_elements(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']"):
            return True
        # 2) “КОД” в таблица/клетка
        xpath = f"//tr[.//td[contains(normalize-space(),'{q}')]] | //*[contains(translate(normalize-space(), 'код', 'КОД'), 'КОД')][contains(., '{q}')]"
        if driver.find_elements(By.XPATH, xpath):
            return True
        return False

    for q in _variants(sku):
        for tmpl in SEARCH_URLS:
            driver.get(tmpl.format(q=q))
            click_cookies_if_any(driver)

            # изчакай + скрол, защото плочките често се дорендерират
            try:
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".search-results, .products, .product-item"))
                )
            except Exception:
                pass
            _slow_scroll(driver, steps=6, pause=0.25)

            links = collect_links()
            if not links:
                save_debug(driver, f"search_no_links_{q}")
                continue

            # приоритизирай линковете, които съдържат кода
            prio = [h for h in links if q in h]
            ordered = prio + [h for h in links if h not in prio]

            # отвори до 20 кандидата и валидирай
            for href in ordered[:20]:
                try:
                    driver.get(href)
                    time.sleep(0.7)
                    _slow_scroll(driver, steps=4, pause=0.2)
                    if page_matches(driver, q):
                        return href
                except Exception:
                    continue

            # нищо не пасна – запази debug от последната продуктова страница
            save_debug(driver, f"product_no_match_{q}")

    return None

# ---------------------------------------------------
# Проверка на наличността, бройката и цената
# ---------------------------------------------------
def check_availability_and_price(driver, sku):
    try:
        row = None
        # 1) старият селектор по клас
        try:
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception:
            # 2) fallback – ред/клетка, съдържащи SKU (колона „КОД“)
            try:
                row = driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{_norm(sku)}')]]")
            except Exception as e2:
                print(f"❌ Не беше намерен ред с SKU {sku}: {e2}")
                return None, 0, None

        # наличност
        qty = 0
        try:
            qty_input = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
            mx = qty_input.get_attribute("data-max-qty-1") or qty_input.get_attribute("max")
            if mx and mx.isdigit():
                qty = int(mx)
        except Exception:
            pass
        status = "Наличен" if qty > 0 else "Изчерпан"

        # цена (твоята логика + fallback)
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
        reader = csv.reader(f)
        next(reader, None)
        return [row[0].strip() for row in reader if row]

def save_results(results, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU', 'Наличност', 'Бройки', 'Цена'])
        writer.writerows(results)

def save_not_found(skus_not_found, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU'])
        for sku in skus_not_found:
            writer.writerow([sku])

# -----------------------
# main
# -----------------------
def main():
    sku_file = os.path.join(BASE_DIR, 'sku_list_filstar.csv')
    result_file = os.path.join(BASE_DIR, 'results_filstar.csv')
    not_found_file = os.path.join(BASE_DIR, 'not_found_filstar.csv')

    skus = read_sku_codes(sku_file)
    driver = create_driver()
    results, not_found = [], []

    for sku in skus:
        print(f"➡️ Обработвам SKU: {sku}")
        product_url = find_product_url(driver, sku)
        if product_url:
            print(f"  ✅ Намерен продукт: {product_url}")
            driver.get(product_url)
            time.sleep(0.6)
            _slow_scroll(driver, steps=4, pause=0.2)
            status, qty, price = check_availability_and_price(driver, sku)
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  📦 Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
        else:
            print(f"❌ Няма валиден продукт за SKU {sku}")
            save_debug(driver, f"no_valid_product_{_norm(sku)}")
            not_found.append(sku)

    driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)

    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == '__main__':
    main()
