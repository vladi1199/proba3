import csv
import os
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# ------------ Настройки / пътища ------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
}

SEARCH_PATHS = [
    "https://filstar.com/search?term={q}",
    "https://filstar.com/bg/search?term={q}",
    "https://filstar.com/en/search?term={q}",
]

PRICE_PATTERNS = [
    re.compile(r"(\d+[.,]\d{2})\s*(лв|bgn|lv)", re.IGNORECASE),
]

AVAIL_HINTS = [
    ("изчерпан", "Изчерпан"),
    ("няма", "Изчерпан"),
    ("out of stock", "Изчерпан"),
    ("наличен", "Наличен"),
    ("в наличност", "Наличен"),
    ("in stock", "Наличен"),
    ("availability", "Unknown"),
    ("наличност", "Unknown"),
]


# ------------ Помощни функции ------------
def norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")


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


def parse_sku_from_url(url: str):
    try:
        q = parse_qs(urlparse(url).query)
        v = q.get("sku", [])
        if v:
            return norm(v[0])
    except Exception:
        pass
    return None


def find_product_link_by_sku(session: requests.Session, sku: str) -> str | None:
    q = norm(sku)
    for tpl in SEARCH_PATHS:
        url = tpl.format(q=q)
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            print(f"   🌐 Search URL: {url} (status {resp.status_code})")
        except requests.RequestException as e:
            print(f"   ⚠️ Search request error for {url}: {e}")
            continue
        if resp.status_code != 200 or not resp.text:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if "sku=" not in href:
                continue
            full = urljoin(url, href)
            sku_in_href = parse_sku_from_url(full)
            if sku_in_href == q:
                return full
    return None


def extract_price(html: str, soup: BeautifulSoup) -> str | None:
    for sel in [".price", ".product-price", ".final-price", "[class*='price']"]:
        el = soup.select_one(sel)
        if not el:
            continue
        txt = el.get_text(" ", strip=True)
        for pat in PRICE_PATTERNS:
            m = pat.search(txt.replace("\xa0", " "))
            if m:
                return m.group(1).replace(",", ".")
    for pat in PRICE_PATTERNS:
        m = pat.search(html.replace("\xa0", " "))
        if m:
            return m.group(1).replace(",", ".")
    return None


def extract_availability(text: str) -> str:
    t = text.lower()
    for needle, status in AVAIL_HINTS:
        if needle in t:
            return status
    return "Unknown"


def fetch_product_info(session: requests.Session, product_url: str, sku: str):
    try:
        r = session.get(product_url, headers=HEADERS, timeout=20)
        print(f"   🌐 Product URL: {product_url} (status {r.status_code})")
    except requests.RequestException as e:
        print(f"   ⚠️ Product request error: {e}")
        return None, 0, None
    if r.status_code != 200 or not r.text:
        return None, 0, None

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    url_sku = parse_sku_from_url(product_url)
    if url_sku and url_sku != norm(sku):
        return None, 0, None

    price = extract_price(html, soup)
    full_text = soup.get_text(" ", strip=True)
    status = extract_availability(full_text)
    qty = 1 if status == "Наличен" else 0

    return status, qty, price


# ------------ main ------------
def main():
    skus = read_sku_codes(SKU_CSV)
    results, not_found = [], []

    with requests.Session() as session:
        session.headers.update(HEADERS)

        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")
            product_url = find_product_link_by_sku(session, sku)

            if not product_url:
                print(f"❌ Не намерих продуктова страница за {sku}")
                not_found.append(sku)
                continue

            status, qty, price = fetch_product_info(session, product_url, sku)
            if status is None:
                print(f"❌ Не успях да извадя данни от {product_url}")
                not_found.append(sku)
                continue

            print(f"  ✅ Продукт: {product_url}")
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")
            results.append([sku, status, qty, price if price else ""])
            time.sleep(0.5)

    write_results(results, RES_CSV)
    write_not_found(not_found, NF_CSV)
    print(f"✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")


if __name__ == "__main__":
    main()
