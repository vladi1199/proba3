#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Filstar checker — серийно, без пропускане/резюме.
ВАЖНО: На ВСЯКО пускане обработва АБСОЛЮТНО всички SKU от CSV.
- За всеки SKU: /search?term=<sku> -> кандидати -> продукт -> ред "КОД" -> цена/бройка
- Щадящо: пауза между заявки и между SKU (регулира се долу).
- Нормална цена (лв.): взима <strike> ако има намаление, иначе първата '... лв.'.
- Наличност: от колоната с брояча `.counter-box input[type='text']` (видима и без логин).
- Няма никакво "resume" и "skip" — всеки път генерира нови results/not_found.
requirements.txt:
    requests
    beautifulsoup4
    lxml
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

DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URLS = [
    "https://filstar.com/search?term={q}",
    "https://filstar.com/bg/search?term={q}",
    "https://filstar.com/en/search?term={q}",
]

# Щадящи настройки (увеличи, ако искаш още по-бавно)
REQUEST_DELAY = 0.4       # сек. пауза между HTTP заявки
DELAY_BETWEEN_SKUS = 0.8  # сек. пауза между SKU
TIMEOUT = 20              # сек. таймаут на заявка
RETRIES = 3               # ретраии на заявка
MAX_CANDIDATES = 15       # до колко продуктови линка да проверим от търсачката (на SKU)

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
        last_exc = None
        for attempt in range(1, RETRIES + 1):
            try:
                r = self.s.get(url, timeout=TIMEOUT, allow_redirects=True)
                self._last_ts = time.time()
                return r
            except requests.RequestException as e:
                last_exc = e
                time.sleep(0.6 * attempt)
        raise last_exc or RuntimeError("HTTP GET failed")

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

def init_result_files():
    # ВИНАГИ презаписвай заглавките (зануляване на предишни резултати)
    with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU", "Наличност", "Бройки", "Цена (нормална лв.)"])
    with open(NF_CSV, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["SKU"])

def append_result(row):
    with open(RES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def append_nf(sku: str):
    with open(NF_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([sku])

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
    - Нормална цена: от <strike> ако има намаление, иначе първата '... лв.' в ценовата клетка/реда.
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
        return

    # 2) обхождаме кандидат продуктите последователно
    saved_debug = False
    for link in candidates:
        try:
            r = HTTP.get(link)
            if r.status_code != 200 or not r.text:
                continue
            status, qty, price = extract_row_data_from_product_html(r.text, q)
            if price is not None:
                print(f"  ✅ {q} → {price} лв. | {status} ({qty} бр.) | {link}")
                append_result([q, status or "Unknown", qty or 0, price])
                return
            else:
                if not saved_debug:
                    save_debug_html_text(r.text, q, "no_price_or_row")
                    saved_debug = True
        except Exception:
            continue

    print(f"❌ Не намерих SKU {q} в {len(candidates)} резултата.")
    append_nf(q)

# ---------------- main (серийно) ----------------
def main():
    if not os.path.exists(SKU_CSV):
        print(f"❌ Липсва {SKU_CSV}")
        sys.exit(1)

    # ВИНАГИ занулявай резултатите на всяко пускане:
    init_result_files()

    all_skus = read_skus(SKU_CSV)
    print(f"🧾 Общо SKU в CSV: {len(all_skus)}")

    count = 0
    for sku in all_skus:
        process_one_sku(sku)
        count += 1
        # щадяща пауза между SKU
        time.sleep(DELAY_BETWEEN_SKUS)
        if count % 100 == 0:
            print(f"📦 Прогрес: {count}/{len(all_skus)} готови")

    print(f"\n✅ Резултати: {RES_CSV}")
    print(f"📄 Not found: {NF_CSV}")

if __name__ == "__main__":
    main()
