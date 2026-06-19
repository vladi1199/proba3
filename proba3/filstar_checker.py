#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
from selenium.common.exceptions import TimeoutException, WebDriverException

# ---------------- PATHS ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")

os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URL = "https://filstar.com/search?term={q}"

# ---------------- SETTINGS ----------------
REQUEST_WAIT = 1
BETWEEN_SKU = 0.8
PAGE_TIMEOUT = 60
MAX_CANDIDATES = 10
RETRIES = 3

# ---------------- HELPERS ----------------
def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def save_debug_html(driver, sku: str, tag: str):
    try:
        path = os.path.join(DEBUG_DIR, f"debug_{sku}_{tag}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass

# ---------------- DRIVER ----------------
def create_driver():
    opts = Options()

    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")

    # 🔥 stability boost
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-features=Translate,BackForwardCache")

    # 🔥 critical fix
    opts.page_load_strategy = "eager"

    driver = webdriver.Chrome(options=opts)

    driver.set_page_load_timeout(PAGE_TIMEOUT)
    driver.set_script_timeout(PAGE_TIMEOUT)

    return driver

# ---------------- SAFE GET ----------------
def safe_get(driver, url):
    for i in range(RETRIES):
        try:
            driver.get(url)
            return True
        except (TimeoutException, WebDriverException) as e:
            print(f"⚠️ Retry {i+1}/{RETRIES}: {url}")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            time.sleep(2)
    return False

# ---------------- CSV ----------------
def init_result_files():
    with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU", "Наличност", "Бройки", "Цена (лв.)"])

    with open(NF_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU"])

def append_result(row):
    with open(RES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def append_nf(sku):
    with open(NF_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([sku])

def read_skus(path):
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if row and row[0].strip():
                out.append(row[0].strip())
    return out

# ---------------- SEARCH ----------------
def get_search_candidates(driver, sku):
    url = SEARCH_URL.format(q=sku)

    if not safe_get(driver, url):
        return []

    time.sleep(REQUEST_WAIT)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except Exception:
        pass

    links = []

    selectors = [
        ".product-item-wapper a.product-name",
        ".product-title a"
    ]

    for sel in selectors:
        try:
            for a in driver.find_elements(By.CSS_SELECTOR, sel):
                href = a.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        href = urljoin("https://filstar.com", href)
                    links.append(href)
        except Exception:
            continue

    # deduplicate
    seen = set()
    uniq = []
    for l in links:
        if l not in seen:
            seen.add(l)
            uniq.append(l)

    return uniq[:MAX_CANDIDATES]

# ---------------- PRODUCT PAGE ----------------
def extract_from_product_page(driver, sku):
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#fast-order-table tbody"))
        )
    except Exception:
        return None, None, None

    rows = driver.find_elements(By.CSS_SELECTOR, "#fast-order-table tbody tr")
    target = None

    for row in rows:
        try:
            code = row.find_element(By.CSS_SELECTOR, "td.td-sky").text.strip()
            if only_digits(code) == str(sku):
                target = row
                break
        except Exception:
            continue

    if not target:
        return None, None, None

    # price
    price = None
    try:
        strike = target.find_element(By.TAG_NAME, "strike").text
        m = re.search(r"(\d+[.,]?\d*)", strike)
        if m:
            price = m.group(1).replace(",", ".")
    except Exception:
        pass

    if not price:
        m = re.search(r"(\d+[.,]?\d*)", target.text)
        if m:
            price = m.group(1).replace(",", ".")

    # status
    status = "Наличен"
    try:
        if "Изчерпан продукт" in target.text:
            status = "Изчерпан"
    except Exception:
        pass

    return status, "-", price

# ---------------- SKU PROCESS ----------------
def process_one_sku(driver, sku):
    print(f"\n➡️ SKU: {sku}")

    candidates = get_search_candidates(driver, sku)

    if not candidates:
        save_debug_html(driver, sku, "no_search")
        append_nf(sku)
        return

    for link in candidates:
        try:
            if not safe_get(driver, link):
                continue

            time.sleep(REQUEST_WAIT)

            status, qty, price = extract_from_product_page(driver, sku)

            if price:
                print(f"  ✅ {sku} → {price} | {status}")
                append_result([sku, status, qty, price])
                return

        except Exception:
            continue

    save_debug_html(driver, sku, "no_match")
    append_nf(sku)

# ---------------- MAIN ----------------
def main():
    if not os.path.exists(SKU_CSV):
        print("Missing SKU file")
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
