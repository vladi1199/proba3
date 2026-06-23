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


# ---------------- ПЪТИЩА ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV = os.path.join(BASE_DIR, "not_found_filstar.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")

os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URL = "https://filstar.com/search?term={q}"


# ---------------- НАСТРОЙКИ ----------------
REQUEST_WAIT = 0.5
BETWEEN_SKU = 0.6
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
    except Exception:
        pass


# ---------------- DRIVER ----------------
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,2200")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    return driver


# ---------------- FILES ----------------
def init_result_files():
    with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU", "Наличност", "Бройки", "Цена"])

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
    driver.get(SEARCH_URL.format(q=sku))
    time.sleep(REQUEST_WAIT)

    links = []

    try:
        for a in driver.find_elements(By.CSS_SELECTOR, ".product-item-wapper a.product-name"):
            href = a.get_attribute("href")
            if href:
                links.append(urljoin("https://filstar.com", href))
    except Exception:
        pass

    try:
        for a in driver.find_elements(By.CSS_SELECTOR, ".product-title a"):
            href = a.get_attribute("href")
            if href:
                links.append(urljoin("https://filstar.com", href))
    except Exception:
        pass

    uniq = []
    for h in links:
        if h not in uniq:
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

    if target is None:
        return None, None, None

    # --- PRICE ---
    price = None
    try:
        strike_el = target.find_element(By.TAG_NAME, "strike")
        m = re.search(r"(\d+[.,]?\d*)\s*€", strike_el.text)
        if m:
            price = m.group(1).replace(",", ".")
    except Exception:
        try:
            m2 = re.search(r"(\d+[.,]?\d*)\s*€", target.text)
            if m2:
                price = m2.group(1).replace(",", ".")
        except Exception:
            pass

    # --- STATUS ---
    status = "Наличен"

    try:
        target.find_element(By.CSS_SELECTOR, "[data-target='#send-request']")
        status = "Изчерпан"
    except Exception:
        if "Изчерпан продукт!" in target.text:
            status = "Изчерпан"

    return status, "-", price


# ---------------- SKU PROCESS ----------------
def process_one_sku(driver, sku: str):
    candidates = get_search_candidates(driver, sku)

    if not candidates:
        append_nf(sku)
        return

    for link in candidates:
        try:
            driver.get(link)
            time.sleep(REQUEST_WAIT)

            status, qty, price = extract_from_product_page(driver, sku)

            if price is not None:
                append_result([sku, status, qty, price])
                return

        except Exception:
            continue

    append_nf(sku)


# ---------------- MAIN ----------------
def main():
    if not os.path.exists(SKU_CSV):
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
