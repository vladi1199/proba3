import csv
import os
import re
import time
import requests
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


# Конфигурация на драйвъра
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def norm(s):
    return str(s).strip()


def click_cookies_if_any(driver):
    """Затваря cookie popup ако се появи"""
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button#rcc-confirm-button"))
        )
        btn.click()
    except Exception:
        pass


# Търсене на продукт в търсачката и извличане на линк
def find_product_link_via_search(driver, sku) -> str | None:
    q = norm(sku)
    search_urls = [
        f"https://filstar.com/search?term={q}",
        f"https://filstar.com/bg/search?term={q}",
        f"https://filstar.com/en/search?term={q}",
    ]
    for surl in search_urls:
        try:
            print(f"   🌐 Search URL: {surl}")
            r = requests.get(surl, timeout=10)
            print(f"      → status {r.status_code}")
        except Exception as e:
            print(f"      ✖ неуспешна заявка: {e}")

        try:
            driver.get(surl)
            click_cookies_if_any(driver)
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-item-wapper a.product-name"))
            )
            anchors = driver.find_elements(By.CSS_SELECTOR, ".product-item-wapper a.product-name")
            for a in anchors:
                href = a.get_attribute("href") or ""
                if href:
                    if href.startswith("/"):
                        href = "https://filstar.com" + href
                    return href
        except Exception:
            continue
    return None


# Проверка на наличност и цена на продуктова страница
def check_availability_and_price(driver, url, sku):
    try:
        driver.get(url)
        click_cookies_if_any(driver)
        time.sleep(2)

        # Цена
        price = None
        try:
            price_el = driver.find_element(By.CSS_SELECTOR, ".discount-price, .regular-price")
            raw_price = price_el.text.strip()
            match = re.search(r"(\d+[.,]?\d*)", raw_price.replace(",", "."))
            if match:
                price = match.group(1)
        except Exception:
            pass

        # Наличност/бройка (тук трябва да се нагласи според реалния HTML на продукта)
        qty = 0
        status = "Unknown"
        try:
            qty_el = driver.find_element(By.CSS_SELECTOR, "input.quantity-field")
            max_qty_attr = qty_el.get_attribute("max")
            if max_qty_attr and max_qty_attr.isdigit():
                qty = int(max_qty_attr)
                status = "Наличен" if qty > 0 else "Изчерпан"
        except Exception:
            pass

        return status, qty, price
    except Exception as e:
        print(f"❌ Грешка при проверка на {sku}: {e}")
        return None, 0, None


# Четене на SKU кодове от CSV
def read_sku_codes(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        return [row[0].strip() for row in reader if row]


# Записване на резултатите в CSV
def save_results(results, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU", "Наличност", "Бройки", "Цена"])
        writer.writerows(results)


# Записване на ненамерени SKU кодове
def save_not_found(skus_not_found, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU"])
        for sku in skus_not_found:
            writer.writerow([sku])


# Основна функция
def main():
    sku_file = os.path.join(base_path, "sku_list_filstar.csv")
    result_file = os.path.join(base_path, "results_filstar.csv")
    not_found_file = os.path.join(base_path, "not_found_filstar.csv")

    skus = read_sku_codes(sku_file)
    driver = create_driver()
    results = []
    not_found = []

    for sku in skus:
        print(f"\n➡️ Обработвам SKU: {sku}")
        product_url = find_product_link_via_search(driver, sku)
        if product_url:
            print(f"  ✅ Продукт: {product_url}")
            status, qty, price = check_availability_and_price(driver, product_url, sku)
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price or '—'}")
            if price:
                results.append([sku, status, qty, price])
            else:
                not_found.append(sku)
        else:
            print(f"❌ Не намерих продуктова страница за {sku}")
            not_found.append(sku)

    driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)

    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")


if __name__ == "__main__":
    main()
