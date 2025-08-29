import csv
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
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
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127 Safari/537.36"
    )
    return webdriver.Chrome(options=options)

def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

# -----------------------------------------
# Намираме URL на продукта по SKU (директно)
# -----------------------------------------
def find_product_url(sku: str) -> str:
    # Важно: slug не е задължителен, стига да имаме ?sku=...
    return f"https://filstar.com/products?sku={_norm(sku)}"

# ---------------------------------------------------
# Проверка на наличността, бройката и цената
# ---------------------------------------------------
def check_availability_and_price(driver, sku):
    try:
        row = None
        try:
            # 1) директен селектор
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception:
            # 2) резервен – ред в таблица, съдържащ SKU в колоната „КОД“
            try:
                row = driver.find_element(
                    By.XPATH,
                    f"//tr[.//td[contains(normalize-space(),'{_norm(sku)}')]]"
                )
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

        # цена
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
            product_url = find_product_url(sku)
            driver.get(product_url)
            time.sleep(0.6)

            status, qty, price = check_availability_and_price(driver, sku)
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  ✅ {product_url} | Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
    finally:
        driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)
    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == "__main__":
    main()
