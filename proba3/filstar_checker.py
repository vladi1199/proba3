import csv
import os
import re
import time
from urllib.parse import urlparse, parse_qs

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
    opts.add_argument("--lang=bg-BG,bg,en-US,en")
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
# Помощни извличания
# ========================
PRICE_SELECTORS = [
    ".price", ".product-price", ".price-value", ".final-price", ".current-price",
    "[class*='price'] span", "[class*='price']"
]

AVAIL_HINTS = [
    ("изчерпан", "Изчерпан"),
    ("няма", "Изчерпан"),
    ("out of stock", "Изчерпан"),
    ("in stock", "Наличен"),
    ("наличен", "Наличен"),
    ("наличност", "Наличен"),
]

def parse_sku_from_url(url: str):
    try:
        q = parse_qs(urlparse(url).query)
        v = q.get("sku", [])
        if v:
            return norm(v[0])
    except Exception:
        pass
    return None

def extract_price_generic(driver):
    # 1) по-често срещани селектори
    for sel in PRICE_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip()
            m = re.search(r"(\d+[.,]\d{2})", txt.replace("\xa0", " "))
            if m:
                return m.group(1).replace(",", ".")
        except Exception:
            continue
    # 2) регекс от целия HTML
    try:
        html = driver.page_source
        m = re.search(r"(\d+[.,]\d{2})\s*(лв|lv|BGN|bgn)", html, re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    except Exception:
        pass
    return None

def extract_availability_generic(driver):
    # Проверка по ключови думи в видим текст
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
        for needle, status in AVAIL_HINTS:
            if needle in body_text:
                return status
    except Exception:
        pass
    # Ако няма ясен сигнал – не гърмим, приемаме Unknown → ще изкараме qty 0
    return "Unknown"

# ========================
# Старите селектори (ако има таблица)
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

def extract_qty_and_price_from_row(row):
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

# ========================
# Централна функция за извличане от произволна продуктова страница
# ========================
def extract_from_product_page(driver, sku):
    q = norm(sku)

    # лек скрол – понякога е нужно за дорендер
    try:
        driver.execute_script("window.scrollBy(0, 400);")
    except Exception:
        pass
    time.sleep(0.4)

    # 1) ако има таблица/ред – ползвай стария начин
    row = find_row_for_sku(driver, sku)
    if row:
        status, qty, price = extract_qty_and_price_from_row(row)
        return status, qty, price

    # 2) ако URL съдържа ?sku=<КОД>, приемаме че е вярната вариация
    url_sku = parse_sku_from_url(driver.current_url)
    if url_sku and url_sku == q:
        price = extract_price_generic(driver)
        status = extract_availability_generic(driver)
        qty = 1 if status == "Наличен" else 0
        return status, qty, price

    # 3) иначе – опитай да намериш елементи "КОД/Code/Tackle Code" и да вземеш следващата стойност
    try:
        label = None
        for xp in [
            "//*[contains(translate(., 'кодCODE', 'КОДcode'), 'КОД')]",
            "//*[contains(text(),'Code')]",
            "//*[contains(text(),'Tackle Code')]",
        ]:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                label = els[0]
                break
        if label:
            # потърси текстово съдържание в следващия sibling или родителски блок
            val_text = ""
            try:
                sib = label.find_element(By.XPATH, "following-sibling::*[1]")
                val_text = sib.text.strip()
            except Exception:
                pass
            if not val_text:
                try:
                    parent = label.find_element(By.XPATH, "./parent::*")
                    val_text = parent.text.strip()
                except Exception:
                    pass
            if q in val_text.replace(" ", ""):
                price = extract_price_generic(driver)
                status = extract_availability_generic(driver)
                qty = 1 if status == "Наличен" else 0
                return status, qty, price
    except Exception:
        pass

    # Нищо надеждно
    return None, 0, None

# ========================
# Отваряне по директни URL варианти
# ========================
def open_direct_with_param(driver, sku):
    q = norm(sku)
    candidates = [
        f"https://filstar.com/products?sku={q}",
        f"https://filstar.com/product?sku={q}",
        f"https://filstar.com/bg/products?sku={q}",
        f"https://filstar.com/bg/product?sku={q}",
    ]
    for url in candidates:
        driver.get(url)
        click_cookies_if_any(driver)
        time.sleep(0.8)
        status, qty, price = extract_from_product_page(driver, sku)
        if status is not None:
            print(f"  ✅ Отворен продукт: {url}")
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")
            return status, qty, price
    return None, 0, None

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

            status, qty, price = open_direct_with_param(driver, sku)
            if status is None:
                print(f"❌ Не намерих валиден продукт за {sku}")
                not_found.append(sku)
                continue

            results.append([sku, status, qty, price if price else ""])
    finally:
        driver.quit()

    write_results(results, RES_CSV)
    write_not_found(not_found, NF_CSV)
    print(f"✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")

if __name__ == "__main__":
    main()
