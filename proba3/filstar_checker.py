#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Filstar checker — СЕРИЙНО (без асинхронност, без нишки)
- За всеки SKU: /search?term=<sku> -> кандидати -> продукт -> ред "КОД" -> цена/бройка
- Щадящо към сайта: пауза между заявки и между SKU
- Resume: прескача вече обработени (results/not_found/processed.txt)
- Debug: записва HTML при проблем
"""

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
STATE_FILE = os.path.join(BASE_DIR, "processed.txt")

DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URLS = [
    "https://filstar.com/search?term={q}",
    "https://filstar.com/bg/search?term={q}",
    "https://filstar.com/en/search?term={q}",
]

# Щадящи настройки (можеш да увеличиш паузите при нужда)
REQUEST_DELAY = 0.4       # сек. пауза между HTTP заявки
DELAY_BETWEEN_SKUS = 0.8  # сек. пауза между SKU
TIMEOUT = 20              # сек. таймаут на заявка
RETRIES = 3               # ретраии на заявка
MAX_CANDIDATES = 15       # до колко продуктови линка да проверим от търсачката

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127 Safari/537.36",
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

# ---------------- Помощни ----------------
def norm(s: str) -> str:
    return (s or "").strip()

def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def save_debug_html_text(text: str, sku: str, tag: str):
    try:
        path = os.path.join(DEBUG_DIR, f"debug_{sku}_{tag}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"   🐞 Debug HTML записан: {path}")
    except Exception:
        pass

# ---------------- HTTP клиент (серийно, с пауза) ----------------
class Http:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self._last_ts = 0.0

    def get(self, url: str) -> requests.Response:
        # щадяща пауза между заявки
        elapsed = time.time() - self._last_ts
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        for attempt in range(1, RETRIES + 1):
            try:
                r = self.s.get(url, timeout=TIMEOUT, allow_redirects=True)
                self._last_ts = time.time()
                return r
            except requests.RequestException as e:
                if attempt == RETRIES:
                    raise
                time.sleep(0.6 * attempt)

HTTP = Http()

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

def ensure_result_headers():
    if not os.path.exists(RES_CSV):
        with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["SKU", "Наличност", "Бройки", "Цена (нормална лв.)"])
    if not os.path.exists(NF_CSV):
        with open(NF_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["SKU"])

def append_result(row):
    with open(RES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def append_nf(sku: str):
    with open(NF_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([sku])

def load_done_sets() -> set:
    done = set()
    if os.path.exists(RES_CSV):
        with open(RES_CSV, newline="", encoding="utf-8") as f:
            r = csv.reader(f); _ = next(r, None)
            for row in r:
                if row: done.add(norm(row[0]))
    if os.path.exists(NF_CSV):
        with open(NF_CSV, newline="", encoding="utf-8") as f:
            r = csv.reader(f); _ = next(r, None)
            for row in r:
                if row: done.add(norm(row[0]))
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            for line in f:
                if norm(line): done.add(norm(line))
    return done

def append_state(sku: str):
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(sku + "\n")

# ---------------- Парсене ----------------
def parse_search_candidates(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select(".product-item-wapper a.product-name"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = urljoin("https://filstar.com", href)
        out.append(href)
    # премахни дубли и режи до MAX_CANDIDATES
    seen, uniq = set(), []
    for h in out:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq[:MAX_CANDIDATES]

def extract_row_data_from_product_html(html: str, sku: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    Връща (status, qty, normal_price_lv) за точния ред по 'КОД'.
    - Нормална цена: от <strike> ако има, иначе първата '... лв.' в ценовата клетка/реда.
    - Бройка: от counter-box input[type=text] value (видима и без логин).
    """
    soup = BeautifulSoup(html, "lxml")
    tbody = soup.select_one("#fast-order-table tbody")
    if not tbody:
        return None, None, None

    rows = tbody.select("tr")
    for row in rows:
        code_td = row.select_one("td.td-sky")
        code_digits = only_digits(code_td.get_text(" ", strip=True)) if code_td else ""
        if code_digits != str(sku):
            continue

        # Цена (нормална)
        price = None
        strike = row.find("strike")
        if strike:
            m = re.search(r"(\d+[.,]?\d*)\s*лв", strike.get_text(" ", strip=True), re.I)
            if m:
                price = m.group(1).replace(",", ".")
        if not price:
            price_td = None
            for td in row.find_all("td"):
                if td.find(string=lambda t: isinstance(t, str) and "ЦЕНА НА ДРЕБНО" in t):
                    price_td = td
                    break
            txt = price_td.get_text(" ", strip=True) if price_td else row.get_text(" ", strip=True)
            m2 = re.search(r"(\d+[.,]?\d*)\s*лв", txt, re.I)
            if m2:
                price = m2.group(1).replace(",", ".")

        # Наличност (counter-box)
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

# ---------------- Логика за 1 SKU (серийно) ----------------
def process_one_sku(sku: str):
    q = only_digits(sku) or sku
    print(f"\n➡️ Обработвам SKU: {q}")

    # 1) търсене
    candidates = []
    for su in SEARCH_URLS:
        url = su.format(q=q)
        try:
            r = HTTP.get(url)
            if r.status_code == 200 and r.text:
                c = parse_search_candidates(r.text)
                if c:
                    candidates.extend(c)
        except Exception:
            pass
        if len(candidates) >= MAX_CANDIDATES:
            break

    # уникализирай
    seen, uniq = set(), []
    for h in candidates:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    candidates = uniq[:MAX_CANDIDATES]

    if not candidates:
        print(f"❌ Няма резултати в търсачката за {q}")
        append_nf(q)
        append_state(q)
        return

    # 2) обхождаме кандидат продуктите последователно
    for link in candidates:
        try:
            r = HTTP.get(link)
            if r.status_code != 200 or not r.text:
                continue
            status, qty, price = extract_row_data_from_product_html(r.text, q)
            if price is not None:
                print(f"  ✅ {q} → {price} лв. | {status} ({qty} бр.) | {link}")
                append_result([q, status or "Unknown", qty or 0, price])
                append_state(q)
                return
            else:
                # запази дебъг HTML само веднъж (от първия кандидат)
                save_debug_html_text(r.text, q, "no_price_or_row")
        except Exception:
            continue

    print(f"❌ Не намерих SKU {q} в {len(candidates)} резултата.")
    append_nf(q)
    append_state(q)

# ---------------- main (серийно) ----------------
def main():
    if not os.path.exists(SKU_CSV):
        print(f"❌ Липсва {SKU_CSV}")
        sys.exit(1)

    ensure_result_headers()

    all_skus = read_skus(SKU_CSV)
    print(f"🧾 Общо SKU в CSV: {len(all_skus)}")

    already = load_done_sets()
    todo = [s for s in all_skus if norm(s) not in already]

    print(f"⏩ Прескачам вече обработени: {len(already)}")
    print(f"🚶 Серийно за обработка сега: {len(todo)}")

    count = 0
    for sku in todo:
        process_one_sku(sku)
        count += 1
        # щадяща пауза между отделните SKU
        time.sleep(DELAY_BETWEEN_SKUS)
        if count % 100 == 0:
            print(f"📦 Прогрес: {count}/{len(todo)} готови")

    print(f"\n✅ Резултати: {RES_CSV}")
    print(f"📄 Not found: {NF_CSV}")
    print(f"🧷 State: {STATE_FILE}")

if __name__ == "__main__":
    main()
