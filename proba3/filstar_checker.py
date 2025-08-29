#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import os
import re
import sys
import time
from typing import List, Tuple, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------- Конфигурация ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")

RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")

DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URL = "https://filstar.com/search?term={q}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127 Safari/537.36",
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------- Помощни ----------------
def norm(s: str) -> str:
    return (s or "").strip()

def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def save_debug_html(html: str, sku: str, tag: str):
    path = os.path.join(DEBUG_DIR, f"debug_{sku}_{tag}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"   🐞 Debug HTML записан: {path}")

# ---------------- I/O ----------------
def read_skus(path: str) -> List[str]:
    skus = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        _ = next(r, None)  # хедър
        for row in r:
            if not row: continue
            v = norm(row[0])
            if v and v.lower() != "sku":
                skus.append(v)
    return skus

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

# ---------------- Търсене ----------------
def search_candidates(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select(".product-item-wapper a.product-name"):
        href = (a.get("href") or "").strip()
        if href.startswith("/"):
            href = urljoin("https://filstar.com", href)
        out.append(href)
    return list(dict.fromkeys(out))

# ---------------- Продуктова страница ----------------
def extract_row_data(html: str, sku: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    soup = BeautifulSoup(html, "lxml")
    tbody = soup.select_one("#fast-order-table tbody")
    if not tbody:
        return None, None, None

    for row in tbody.select("tr"):
        code_td = row.select_one("td.td-sky")
        code_digits = only_digits(code_td.get_text(" ", strip=True)) if code_td else ""
        if code_digits == str(sku):
            # цена
            price = None
            strike = row.find("strike")
            if strike:
                m = re.search(r"(\d+[.,]?\d*)\s*лв", strike.get_text(" ", strip=True))
                if m:
                    price = m.group(1).replace(",", ".")
            if not price:
                txt = row.get_text(" ", strip=True)
                m2 = re.search(r"(\d+[.,]?\d*)\s*лв", txt)
                if m2:
                    price = m2.group(1).replace(",", ".")

            # бройки
            qty = 0
            status = "Unknown"
            inp = row.select_one(".counter-box input[type='text']")
            if inp:
                val = (inp.get("value") or "").strip()
                if val.isdigit():
                    qty = int(val)
                    status = "Наличен" if qty > 0 else "Изчерпан"

            return status, qty, price
    return None, None, None

# ---------------- Основна логика ----------------
def process_one_sku(sku: str):
    print(f"\n➡️ Обработвам SKU: {sku}")
    url = SEARCH_URL.format(q=sku)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"❌ Search error {r.status_code}")
            append_nf(sku)
            return
        candidates = search_candidates(r.text)
        if not candidates:
            save_debug_html(r.text, sku, "search_no_results")
            print(f"❌ Не намерих резултати за {sku}")
            append_nf(sku)
            return
    except Exception as e:
        print(f"❌ HTTP error {e}")
        append_nf(sku)
        return

    for link in candidates:
        try:
            pr = requests.get(link, headers=HEADERS, timeout=20)
            if pr.status_code != 200: continue
            status, qty, price = extract_row_data(pr.text, sku)
            if price:
                print(f"  ✅ {sku} → {price} лв. | {status} ({qty} бр.) | {link}")
                append_result([sku, status or "Unknown", qty or 0, price])
                return
        except Exception:
            continue

    save_debug_html(pr.text if 'pr' in locals() else r.text, sku, "no_price_or_row")
    print(f"❌ Не намерих SKU {sku}")
    append_nf(sku)

def main():
    if not os.path.exists(SKU_CSV):
        print(f"❌ Липсва {SKU_CSV}")
        sys.exit(1)

    init_result_files()
    all_skus = read_skus(SKU_CSV)
    print(f"🧾 Общо SKU в CSV: {len(all_skus)}")

    for sku in all_skus:
        process_one_sku(sku)
        time.sleep(0.5)  # пауза да не пада сайта

    print(f"\n✅ Резултати: {RES_CSV}")
    print(f"📄 Not found: {NF_CSV}")

if __name__ == "__main__":
    main()
