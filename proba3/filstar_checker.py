import csv
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Зареждаме променливите от .env файл (ако има)
load_dotenv()

# Откриване на base_path спрямо локацията на текущия файл
base_path = os.path.dirname(os.path.abspath(__file__))

# ---------------------------
# Конфигурация на драйвъра
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

# Затваряне на cookie банера (ако има)
def click_cookies_if_any(driver):
    candidates = [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(., 'Приемам')]"),
        (By.XPATH, "//*[contains(., 'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(., 'Accept')]"),
    ]
    for how, what in candidates:
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
# Намираме URL на продукта по SKU (търсачка)
# -----------------------------------------
def find_product_url(driver, sku):
    """
    Отваря /search?term=<SKU>, взима линковете от картите и връща най-подходящия.
    """
    SEARCH_URLS = [
        "https://filstar.com/search?term={q}",
        "https://filstar.com/bg/search?term={q}",
    ]

    def collect_links():
        # покрива картите в резултатите
        sels = [
            ".search-results a[href]",
            ".products a[href]",
            ".product-item a[href]",
            "a.product-item-link",
        ]
        seen, hrefs = set(), []
        for sel in sels:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href = el.get_attribute("href") or ""
                if not href or href in seen:
                    continue
                seen.add(href)
                hrefs.append(href)
        # махаме общи/листови
        hrefs = [h for h in hrefs if "/products/new" not in h and "/search?" not in h]
        return hrefs

    for q in _variants(sku):
        for tmpl in SEARCH_URLS:
            driver.get(tmpl.format(q=q))
            click_cookies_if_any(driver)

            try:
                WebDriverWait(driver, 12).until(
                    EC.any_of(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".search-results, .products, .product-item")),
                        EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                    )
                )
            except Exception:
                time.sleep(1.0)

            links = collect_links()
            if not links:
                continue

            # първо линкове, съдържащи SKU (или без водещи нули)
            prio = [h for h in links if q in h]
            ordered = prio + [h for h in links if h not in prio]

            # върни първия кандидат; парсингът ще валидира на самата страница
            return ordered[0]

    return None

# ---------------------------------------------------
# Проверка на наличността, бройката и цената
# ---------------------------------------------------
def check_availability_and_price(driver, sku):
    try:
        # 1) старият ти селектор по клас table-row-<sku>
        row = None
        try:
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception:
            # 2) fallback – ред, в който някоя <td> съдържа SKU (колона „КОД“)
            try:
                row = driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{_norm(sku)}')]]")
            except Exception as e2:
                print(f"❌ Не беше намерен ред с SKU {sku}: {e2}")
                return None, 0, None

        # наличност (qty)
        qty = 0
        try:
            qty_input = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
            mx = qty_input.get_attribute("data-max-qty-1") or qty_input.get_attribute("max")
            if mx and mx.isdigit():
                qty = int(mx)
        except Exception:
            pass
        status = "Наличен" if qty > 0 else "Изчерпан"

        # цена (твоята логика + малък fallback)
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
            # ако липсва контейнерът – извади от текста на реда
            m = re.search(r"(\d+[.,]\d{2})\s*лв", row.text.replace("\xa0", " "))
            if m:
                price = m.group(1).replace(",", ".")

        return status, qty, price

    except Exception as e:
        print(f"❌ Грешка при проверка на наличността и цената за SKU {sku}: {e}")
        return None, 0, None

# -----------------------
# CSV вход/изход
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
    sku_file = os.path.join(base_path, 'sku_list_filstar.csv')
    result_file = os.path.join(base_path, 'results_filstar.csv')
    not_found_file = os.path.join(base_path, 'not_found_filstar.csv')

    skus = read_sku_codes(sku_file)
    driver = create_driver()
    results, not_found = [], []

    for sku in skus:
        print(f"➡️ Обработвам SKU: {sku}")
        product_url = find_product_url(driver, sku)
        if product_url:
            print(f"  ✅ Намерен продукт: {product_url}")
            driver.get(product_url)
            time.sleep(0.6)  # кратко изчакване за JS
            status, qty, price = check_availability_and_price(driver, sku)
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  📦 Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
        else:
            print(f"❌ Няма валиден продукт за SKU {sku}")
            not_found.append(sku)

    driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)

    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == '__main__':
    main()
