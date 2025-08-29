import csv
import os
import re
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- пътища ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")

# ========================
# WebDriver
# ========================
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--lang=bg-BG,bg")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=opts)

def click_cookies_if_any(driver):
    for how, sel in [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(.,'Приемам')]"),
        (By.XPATH, "//*[contains(.,'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(.,'Accept')]"),
    ]:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, sel))).click()
            break
        except Exception:
            pass

def norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

# ========================
# Валидиране/четене от страница на продукт
# ========================
def find_row_for_sku(driver, sku):
    q = norm(sku)
    try:
        return driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']")
    except Exception:
        pass
    try:
        return driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{q}')]]")
    except Exception:
        pass
    try:
        cell = driver.find_element(By.XPATH, f"//*[contains(normalize-space(),'{q}')]")
        return cell.find_element(By.XPATH, "./ancestor::tr")
    except Exception:
        return None

def extract_qty_and_price(row):
    qty = 0
    try:
        inp = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
        mx = inp.get_attribute("data-max-qty-1") or inp.get_attribute("max")
        if mx and mx.isdigit():
            qty = int(mx)
    except Exception:
        pass
    status = "Наличен" if qty > 0 else "Изчерпан"

    price = None
    try:
        holder = row.find_element(By.CSS_SELECTOR, "div.custom-tooltip-holder")
        try:
            strike = holder.find_element(By.TAG_NAME, "strike").text
            m = re.search(r"(\d+[.,]\d{2})", strike)
            if m:
                price = m.group(1).replace(",", ".")
        except Exception:
            m = re.search(r"(\d+[.,]\d{2})", holder.text)
            if m:
                price = m.group(1).replace(",", ".")
    except Exception:
        m = re.search(r"(\d+[.,]\d{2})\s*лв", row.text.replace("\xa0", " "))
        if m:
            price = m.group(1).replace(",", ".")
    return status, qty, price

def page_has_sku_and_extract(driver, sku):
    q = norm(sku)

    # опит 1: таблица
    row = find_row_for_sku(driver, sku)
    if row:
        return extract_qty_and_price(row)

    # опит 2: нов шаблон – детайлна страница
    try:
        code_el = driver.find_element(By.XPATH, "//*[contains(text(),'КОД') or contains(text(),'Code')]")
        code_text = code_el.text
        if q not in code_text.replace(" ", ""):
            return None, 0, None

        # цена
        price = None
        try:
            price_el = driver.find_element(By.CSS_SELECTOR, ".price, .product-price, .price-value")
            m = re.search(r"(\d+[.,]\d{2})", price_el.text)
            if m:
                price = m.group(1).replace(",", ".")
        except Exception:
            pass

        # наличност
        status = "Наличен"
        try:
            avail = driver.find_element(By.XPATH, "//*[contains(text(),'наличност') or contains(text(),'Наличност')]").text
            if "няма" in avail.lower() or "изчерпан" in avail.lower():
                status = "Изчерпан"
        except Exception:
            pass

        return status, 1 if status == "Наличен" else 0, price
    except Exception:
        return None, 0, None

# ========================
# Опити за отваряне на продукт
# ========================
def open_direct_with_param(driver, sku) -> bool:
    q = norm(sku)
    candidates = [
        f"https://filstar.com/products?sku={q}",
        f"https://filstar.com/bg/products?sku={q}",
        f"https://filstar.com/product?sku={q}",
        f"https://filstar.com/bg/product?sku={q}",
    ]
    for url in candidates:
        driver.get(url)
        click_cookies_if_any(driver)
        time.sleep(0.7)
        status, qty, price = page_has_sku_and_extract(driver, sku)
        if status is not None:
            print(f"  ✅ Намерен продукт (директно): {url}")
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
            return True
    return False

# ========================
# CSV I/O
# ========================
def read_sku_codes(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        return [row[0].strip() for row in r if row and row[0].strip()]

def write_results(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Наличност", "Бройки", "Цена"])
        w.writerows(rows)

def write_not_found(skus, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU"])
        for s in skus:
            w.writerow([s])

# ========================
# main
# ========================
def main():
    skus = read_sku_codes(SKU_CSV)
    driver = create_driver()
    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")

            if open_direct_with_param(driver, sku):
                status, qty, price = page_has_sku_and_extract(driver, sku)
                results.append([sku, status, qty, price])
                continue

            print(f"❌ Не намерих валиден продукт за {sku}")
            not_found.append(sku)

    finally:
        driver.quit()

    write_results(results, RES_CSV)
    write_not_found(not_found, NF_CSV)
    print(f"✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")

if __name__ == "__main__":
    main()
