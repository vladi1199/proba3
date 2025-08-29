#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Filstar checker — серийно и щадящо, с по-широка логика за търсене на продуктови линкове.
На всяко пускане обработва всички SKU от CSV и презаписва results/not_found.

- За всеки SKU:
    1) GET /search?term=<sku> (bg/en също)
    2) Вади кандидат-линкове с няколко селектора + regex fallback
    3) За всеки кандидат: парсва таблицата #fast-order-table, намира ред по "КОД"
    4) Взима нормална цена (лв.) и бройка (от counter-box input[type=text])

- Debug:
    - Записва search HTML, ако няма кандидати
    - Записва product HTML, ако не намери ред/цена

ВНИМАНИЕ: без асинхронност/нишки. Има паузи между заявките.
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

# Щадящи настройки
REQUEST_DELAY = 0.45      # сек. пауза между HTTP заявки
DELAY_BETWEEN_SKUS = 0.9  # сек. пауза между SKU
TIMEOUT = 20              # таймаут
RETRIES = 3               # ретраии
MAX_CANDIDATES = 20       # максимум кандидат линкове

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
    # ВИНАГИ презаписваме заглавките (нов run = нови резултати)
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

# ---------------- Търсене: кандидати ----------------
def parse_search_candidates(html: str) -> List[str]:
    """Извлича продуктови линкове от search HTML с няколко селектора + regex fallback."""
    soup = BeautifulSoup(html, "lxml")
    out = []

    # 1) Класическият: .product-item-wapper a.product-name
    for a in soup.select(".product-item-wapper a.product-name"):
        href = (a.get("href") or "").strip()
        if href:
            if href.startswith("/"):
                href = urljoin("https://filstar.com", href)
            out.append(href)

    # 2) Понякога заглавията са в .product-title a
    for a in soup.select(".product-title a"):
        href = (a.get("href") or "").strip()
        if href:
            if href.startswith("/"):
                href = urljoin("https://filstar.com", href)
            out.append(href)

    # 3) Fallback: regex за <a href="/Some-Product-1234">
    if not out:
        for m in re.finditer(r'href="(/[^"]*?-?\d+)"', html):
            href = m.group(1)
            if "/search" in href or "term=" in href:
                continue
            href_full = urljoin("https://filstar.com", href)
            out.append(href_full)

    # премахване на дубли и ограничение
    seen, uniq = set(), []
    for h in out:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq[:MAX_CANDIDATES]

# ---------------- Продуктова страница: ред по КОД ----------------
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
    target_row = None

    # A) Опитай с td.td-sky (където е "КОД")
    for row in rows:
        code_td = row.select_one("td.td-sky")
        code_digits = only_digits(code_td.get_text(" ", strip=True)) if code_td else ""
        if code_digits == str(sku):
            target_row = row
            break

    # B) Ако не сработи, обхождаме всички td и търсим чисто съвпадение
    if target_row is None:
        for row in rows:
            tds = row.find_all("td")
            for td in tds:
                if only_digits(td.get_text(" ", strip=True)) == str(sku):
                    target_row = row
                    break
            if target_row is not None:
                break

    if target_row is None:
        # C) Последен шанс: търсене по текст в реда
        for row in rows:
            txt = row.get_text(" ", strip=True)
            if re.search(rf"\b{re.escape(str(sku))}\b", txt):
                target_row = row
                break

    if target_row is None:
        return None, None, None

    # --- Цена (нормална) ---
    price = None
    strike = target_row.find("strike")
    if strike:
        m = re.search(r"(\d+[.,]?\d*)\s*лв", strike.get_text(" ", strip=True), re.I)
        if m:
            price = m.group(1).replace(",", ".")
    if not price:
        price_td = None
        for td in target_row.find_all("td"):
            if td.find(string=lambda t: isinstance(t, str) and "ЦЕНА НА ДРЕБНО" in t):
                price_td = td
                break
        txt = price_td.get_text(" ", strip=True) if price_td else target_row.get_text(" ", strip=True)
        m2 = re.search(r"(\d+[.,]?\d*)\s*лв", txt, re.I)
        if m2:
            price = m2.group(1).replace(",", ".")

    # --- Наличност/бройка (counter-box input[type=text]) ---
    qty = 0
    status = "Unknown"
    inp = target_row.select_one(".counter-box input[type='text']")
    if inp:
        val = (inp.get("value") or "").strip()
        if val.isdigit():
            qty = int(val)
            status = "Наличен" if qty > 0 else "Изчерпан"

    return status, qty, price

# ---------------- Логика за 1 SKU (серийно) ----------------
def process_one_sku(sku: str):
    q = only_digits(sku) or sku
    print(f"\n➡️ Обработвам SKU: {q}")

    candidates = []
    search_html_saved = False

    # 1) търсене
    for su in SEARCH_URLS:
        url = su.format(q=q)
        try:
            r = HTTP.get(url)
            if r.status_code == 200 and r.text:
                c = parse_search_candidates(r.text)
                if c:
                    candidates.extend(c)
                else:
                    # запази search HTML само веднъж за дебъг
                    if not search_html_saved:
                        save_debug_html_text(r.text, q, "search_no_candidates")
                        search_html_saved = True
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
    saved_product_debug = False
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
                if not saved_product_debug:
                    save_debug_html_text(r.text, q, "no_price_or_row")
                    saved_product_debug = True
        except Exception:
            continue

    print(f"❌ Не намерих SKU {q} в {len(candidates)} резултата.")
    append_nf(q)

# ---------------- main (серийно) ----------------
def main():
    if not os.path.exists(SKU_CSV):
        print(f"❌ Липсва {SKU_CSV}")
        sys.exit(1)

    # ВИНАГИ зануляваме резултатите на всяко пускане
    init_result_files()

    all_skus = read_skus(SKU_CSV)
    print(f"🧾 Общо SKU в CSV: {len(all_skus)}")

    count = 0
    for sku in all_skus:
        process_one_sku(sku)
        count += 1
        time.sleep(DELAY_BETWEEN_SKUS)  # щадяща пауза между SKU
        if count % 100 == 0:
            print(f"📦 Прогрес: {count}/{len(all_skus)} готови")

    print(f"\n✅ Резултати: {RES_CSV}")
    print(f"📄 Not found: {NF_CSV}")

if __name__ == "__main__":
    main()
