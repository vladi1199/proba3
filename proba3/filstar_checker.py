import csv
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from dotenv import load_dotenv

# Зареждаме променливите от .env файл (ако има)
load_dotenv()

# Път спрямо текущия файл
base_path = os.path.dirname(os.path.abspath(__file__))

# --------- WebDriver ---------
def create_driver():
    options = Options()
    # По-стабилен headless режим в GitHub Actions
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1365,1000")
    return webdriver.Chrome(options=options)

def _wait(driver, sec=10):
    return WebDriverWait(driver, sec)

def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

def _variants(sku: str):
    a = _norm(sku)
    b = a.lstrip("0") or a
    return [a] if a == b else [a, b]

# --------- Търсене на продукт ---------
def find_product_url(driver, sku):
    """Връща URL на продуктова страница за дадено SKU (ползва новия /search?term=...)."""
    for q in _variants(sku):
        search_url = f"https://filstar.com/search?term={q}"
        driver.get(search_url)

        # Изчакай да се появят резултати (плочки) или текст за липса
        try:
            _wait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".product-item a[href], a.product-item-link")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(.,'няма резултати') or contains(.,'Няма резултати')]"))
                )
            )
        except TimeoutException:
            continue

        links = driver.find_elements(By.CSS_SELECTOR, ".product-item a[href], a.product-item-link")
        if not links:
            continue

        # Предпочети линк, който съдържа SKU във href или текста
        for el in links:
            href = (el.get_attribute("href") or "")
            txt = (el.text or "").strip()
            if q in href or q in txt:
                return href

        # Иначе – първия резултат (после валидираме по "КОД" в таблицата)
        return links[0].get_attribute("href")

    return None

# --------- Парсване от продуктова страница ---------
def _extract_price(text: str):
    if not text:
        return None
    m = re.search(r"(\d+[.,]\d{2})\s*лв", text.replace("\xa0", " "))
    return m.group(1).replace(",", ".") if m else None

def check_availability_and_price(driver, sku):
    """Намира реда по „КОД“ и връща (статус, бройки, цена)."""
    row = None
    for v in _variants(sku):
        try:
            # Намираме <tr>, който има <td> съдържащ SKU-то
            row = driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{v}')]]")
            break
        except Exception:
            continue

    if not row:
        print(f"❌ Не беше намерен ред с SKU {sku}")
        return None, 0, None

    # Бройки (qty) – първо пробваме input атрибут, иначе число от текста
    qty = 0
    try:
        inp = row.find_element(By.CSS_SELECTOR, "input[name*='quantity'], input[data-max-qty-1]")
        mx = inp.get_attribute("data-max-qty-1") or inp.get_attribute("max")
        if mx and mx.isdigit():
            qty = int(mx)
    except Exception:
        m = re.search(r"\b(\d+)\b", row.text)
        if m:
            qty = int(m.group(1))

    status = "Наличен" if qty > 0 else "Изчерпан"

    # Цена – клетка, която съдържа „лв“, иначе целия ред
    price = None
    try:
        price_cell = row.find_element(By.XPATH, ".//td[contains(.,'лв')]")
        price = _extract_price(price_cell.text)
    except Exception:
        pass
    if not price:
        price = _extract_price(row.text)

    if not price:
        print(f"❌ Не успях да извадя цена за SKU {sku}")

    return status, qty, price

# --------- CSV I/O ---------
def read_sku_codes(path):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)  # пропусни хедъра
        return [row[0].strip() for row in reader if row and row[0].strip()]

def save_results(results, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['SKU', 'Наличност', 'Бройки', 'Цена'])
        w.writerows(results)

def save_not_found(skus_not_found, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['SKU'])
        for sku in skus_not_found:
            w.writerow([sku])

# --------- main ---------
def main():
    sku_file = os.path.join(base_path, 'sku_list_filstar.csv')
    result_file = os.path.join(base_path, 'results_filstar.csv')
    not_found_file = os.path.join(base_path, 'not_found_filstar.csv')

    skus = read_sku_codes(sku_file)
    driver = create_driver()
    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")
            product_url = find_product_url(driver, sku)
            if not product_url:
                print(f"❌ Пропускам SKU {sku} (няма резултат в търсачката)")
                not_found.append(sku)
                continue

            print(f"  ✅ Намерен продукт: {product_url}")
            driver.get(product_url)
            # късо изчакване JS да дорендерира таблицата
            time.sleep(0.6)

            status, qty, price = check_availability_and_price(driver, sku)
            if status is None or price is None:
                print(f"❌ SKU {sku} няма валидна информация (статус/цена).")
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

if __name__ == '__main__':
    main()
