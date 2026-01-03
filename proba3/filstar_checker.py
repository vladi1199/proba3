#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# === Selenium вариант — без проверка за бройки ===
# - На всяко пускане обхожда всички SKU от CSV (без resume).
# - Търси през /search?term=<sku> и събира кандидат продуктови линкове.
# - Отваря продуктите, намира точния ред по "КОД" и:
#     * Цена: нормалната (от <strike> ако има; иначе първата „... лв.“ в реда)
#     * Наличност: ако редът съдържа tooltip "Изчерпан продукт!" / Email иконата за нотификация → "Изчерпан", иначе "Наличен"
# - Не чете и не записва бройки (пише "-" за колона „Бройки“).
# - Серийно и щадящо (леки паузи).

import csv
import os
import re
import time
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------------- ПЪТИЩА ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URL = "https://filstar.com/search?term={q}"

# ---------------- НАСТРОЙКИ ----------------
REQUEST_WAIT = 0.5
BETWEEN_SKU  = 0.6
PAGE_TIMEOUT = 20
MAX_CANDIDATES = 12

# ---------------- ПОМОЩНИ ----------------
def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def save_debug_html(driver, sku: str, tag: str):
    try:
        path = os.path.join(DEBUG_DIR, f"debug_{sku}_{tag}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"   🐞 Debug HTML записан: {path}")
    except Exception:
        pass

def create_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,2200")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    return driver

def init_result_files():
    with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU", "Наличност", "Бройки", "Цена (лв.)"])
    with open(NF_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU"])

def append_result(row):
    with open(RES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def append_nf(sku: str):
    with open(NF_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([sku])

def read_skus(path: str):
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        _ = next(r, None)
        for row in r:
            if not row:
                continue
            v = (row[0] or "").strip()
            if v and v.lower() != "sku":
                out.append(v)
    return out

# ---------------- ТЪРСЕНЕ ----------------
def get_search_candidates(driver, sku: str):
    url = SEARCH_URL.format(q=sku)
    driver.get(url)
    time.sleep(REQUEST_WAIT)

    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "main"))
        )
    except Exception:
        pass

    links = []

    try:
        for a in driver.find_elements(By.CSS_SELECTOR, ".product-item-wapper a.product-name"):
            href = (a.get_attribute("href") or "").strip()
            if href:
                if href.startswith("/"):
                    href = urljoin("https://filstar.com", href)
                links.append(href)
    except Exception:
        pass

    try:
        for a in driver.find_elements(By.CSS_SELECTOR, ".product-title a"):
            href = (a.get_attribute("href") or "").strip()
            if href:
                if href.startswith("/"):
                    href = urljoin("https://filstar.com", href)
                links.append(href)
    except Exception:
        pass

    seen, uniq = set(), []
    for h in links:
        if h not in seen:
            seen.add(h)
            uniq.append(h)

    return uniq[:MAX_CANDIDATES]

# ---------------- ПРОДУКТОВА СТРАНИЦА ----------------
def extract_from_product_page(driver, sku: str):
    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#fast-order-table tbody"))
        )
    except Exception:
        return None, None, None

    tbody = driver.find_element(By.CSS_SELECTOR, "#fast-order-table tbody")
    rows = tbody.find_elements(By.CSS_SELECTOR, "tr")
    target = None

    for row in rows:
        try:
            code_td = row.find_element(By.CSS_SELECTOR, "td.td-sky")
            if only_digits(code_td.text.strip()) == str(sku):
                target = row
                break
        except Exception:
            continue

    if target is None:
        for row in rows:
            try:
                if re.search(rf"\b{re.escape(str(sku))}\b", row.text):
                    target = row
                    break
            except Exception:
                continue

    if target is None:
        return None, None, None

    # --- Цена (евро, ненамалена ако има) ---
    price = None
    try:
        strike_el = target.find_element(By.TAG_NAME, "strike")
        m = re.search(r"(\d+[.,]?\d*)\s*€", strike_el.text)
        if m:
            price = m.group(1).replace(",", ".")
    except Exception:
        pass

    if price is None:
        try:
            m2 = re.search(r"(\d+[.,]?\d*)\s*€", target.text)
            if m2:
                price = m2.group(1).replace(",", ".")
        except Exception:
            pass

    # --- Наличност само по tooltip/email (без бройки) ---
    status = "Наличен"
    try:
        target.find_element(By.CSS_SELECTOR, "[data-target='#send-request']")
        status = "Изчерпан"
    except Exception:
        try:
            if "Изчерпан продукт!" in target.text:
                status = "Изчерпан"
            else:
                emails = target.find_elements(
                    By.CSS_SELECTOR,
                    ".custom-tooltip-holder img[alt='Shopping cart']"
                )
                if emails:
                    status = "Изчерпан"
        except Exception:
            pass

    qty_placeholder = "-"
    return status, qty_placeholder, price

# ---------------- ОБРАБОТКА НА 1 SKU ----------------
def process_one_sku(driver, sku: str):
    print(f"\n➡️ Обработвам SKU: {sku}")

    candidates = get_search_candidates(driver, sku)
    if not candidates:
        save_debug_html(driver, sku, "search_no_results")
        append_nf(sku)
        return

    for link in candidates:
        try:
            driver.get(link)
            time.sleep(REQUEST_WAIT)
            status, qty_ph, price = extract_from_product_page(driver, sku)
            if price is not None:
                print(f"  ✅ {sku} → {price} лв. | {status} | {link}")
                append_result([sku, status or "Наличен", qty_ph, price])
                return
        except Exception:
            continue

    save_debug_html(driver, sku, "no_price_or_row")
    append_nf(sku)

# ---------------- MAIN ----------------
def main():
    if not os.path.exists(SKU_CSV):
        print(f"❌ Липсва {SKU_CSV}")
        return

    init_result_files()
    skus = read_skus(SKU_CSV)

    driver = create_driver()
    try:
        for sku in skus:
            process_one_sku(driver, sku)
            time.sleep(BETWEEN_SKU)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
