import csv
import os
import re
import time
from urllib.parse import urljoin

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ---------------- Константи и пътища ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127 Safari/537.36",
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}


# ---------------- WebDriver ----------------
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1800")
    opts.add_argument("--lang=bg-BG,bg,en-US,en")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    return driver


def click_cookies_if_any(driver):
    candidates = [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(.,'Приемам')]"),
        (By.XPATH, "//*[contains(.,'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(.,'Accept')]"),
    ]
    for how, sel in candidates:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, sel))).click()
            break
        except Exception:
            pass


def norm(s): 
    return str(s).strip()


def save_debug_html_text(text, sku, tag):
    try:
        path = os.path.join(BASE_DIR, f"debug_{sku}_{tag}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"   🐞 Записах HTML за {sku}: {path}")
    except Exception:
        pass


# ---------------- SKU → линкове кандидати ----------------
def direct_sku_links(sku):
    q = norm(sku)
    return [
        f"https://filstar.com/product?sku={q}",
        f"https://filstar.com/bg/product?sku={q}",
        f"https://filstar.com/en/product?sku={q}",
    ]


def get_search_candidates(driver, sku, limit=20):
    """Взима до 'limit' продуктови линка от /search?term=<SKU> (bg, en)."""
    q = norm(sku)
    search_urls = [
        f"https://filstar.com/search?term={q}",
        f"https://filstar.com/bg/search?term={q}",
        f"https://filstar.com/en/search?term={q}",
    ]
    seen, out = set(), []
    for surl in search_urls:
        try:
            driver.get(surl)
            click_cookies_if_any(driver)
            WebDriverWait(driver, 12).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-item-wapper a.product-name"))
            )
            anchors = driver.find_elements(By.CSS_SELECTOR, ".product-item-wapper a.product-name")
            for a in anchors:
                href = (a.get_attribute("href") or "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    href = urljoin("https://filstar.com", href)
                if href not in seen:
                    seen.add(href)
                    out.append(href)
                    if len(out) >= limit:
                        return out
        except Exception:
            continue
    return out


# ---------------- Lazy-load скрол до SKU ред ----------------
def scroll_until_row_with_sku(driver, sku, max_steps=24, step_px=900, pause=0.35):
    q = norm(sku)
    xp = f"//table[@id='fast-order-table']//tr[td[contains(@class,'td-sky')][contains(normalize-space(),'{q}')]]"
    for _ in range(max_steps):
        try:
            row = driver.find_elements(By.XPATH, xp)
            if row:
                return True
        except Exception:
            pass
        try:
            driver.execute_script(f"window.scrollBy(0, {step_px});")
            driver.execute_script("window.dispatchEvent(new Event('scroll'));")
        except Exception:
            pass
        time.sleep(pause)
    # финално – до дъното
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        driver.execute_script("window.dispatchEvent(new Event('scroll'));")
    except Exception:
        pass
    time.sleep(pause + 0.4)
    try:
        row = driver.find_elements(By.XPATH, xp)
        return bool(row)
    except Exception:
        return False


# ---------------- Търсене на ред в таблицата ----------------
def find_row_by_sku_in_table(driver, sku):
    q = norm(sku)
    try:
        tbody = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#fast-order-table tbody"))
        )
    except Exception:
        return None, None

    rows = tbody.find_elements(By.CSS_SELECTOR, "tr")
    # print(f"   🔎 DEBUG: намерени редове в таблицата: {len(rows)}")

    for idx, row in enumerate(rows, start=1):
        try:
            code_td = row.find_element(By.CSS_SELECTOR, "td.td-sky")
            code_text = (code_td.text or "").replace("\xa0", " ").strip()
            code_digits = re.sub(r"\D+", "", code_text)
            if code_digits == q:
                return row, code_text
        except Exception:
            continue
    return None, None


# ---------------- Цена/статус от ред ----------------
def extract_price_from_row_via_selenium(row_el):
    # 1) нормалната (стара) цена в <strike>
    try:
        for st in row_el.find_elements(By.TAG_NAME, "strike"):
            raw = (st.text or "").replace("\xa0", " ")
            m = re.search(r"(\d+[.,]?\d*)\s*лв", raw, flags=re.IGNORECASE)
            if m:
                return m.group(1).replace(",", ".")
    except Exception:
        pass
    # 2) „ЦЕНА НА ДРЕБНО“
    try:
        price_td = row_el.find_element(By.XPATH, ".//td[.//span[contains(.,'ЦЕНА НА ДРЕБНО')]]")
        txt = (price_td.text or "").replace("\xa0", " ")
        m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
        html = price_td.get_attribute("innerHTML") or ""
        m2 = re.search(r"(\d+[.,]?\d*)\s*(?:&nbsp;)?лв", html, flags=re.IGNORECASE)
        if m2:
            return m2.group(1).replace(",", ".")
    except Exception:
        pass
    # 3) fallback – първата „… лв.“ в целия ред
    try:
        txt = (row_el.text or "").replace("\xa0", " ")
        m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
        html = row_el.get_attribute("innerHTML") or ""
        m2 = re.search(r"(\d+[.,]?\d*)\s*(?:&nbsp;)?лв", html, flags=re.IGNORECASE)
        if m2:
            return m2.group(1).replace(",", ".")
    except Exception:
        pass
    return None


def extract_status_from_row_via_selenium(row_el):
    """Наличност без логнат потребител: ако има кошница → Наличен; ако има текст „Изчерпан“ → Изчерпан; иначе Unknown."""
    try:
        # кошница в клетка .cart-data
        cart = row_el.find_elements(By.CSS_SELECTOR, "td.cart-data img[alt='Add to cart']")
        if cart:
            return "Наличен", None
    except Exception:
        pass
    try:
        # tooltip с „Изчерпан продукт!“
        if "Изчерпан" in (row_el.text or ""):
            return "Изчерпан", 0
        html = row_el.get_attribute("innerHTML") or ""
        if re.search(r"Изчерпан", html, flags=re.I):
            return "Изчерпан", 0
    except Exception:
        pass
    return "Unknown", None


# ---------------- Fallback: requests (суров HTML) ----------------
def extract_price_via_requests(product_url, sku):
    try:
        r = requests.get(product_url, headers=HTTP_HEADERS, timeout=20)
        r.raise_for_status()
        html = r.text

        m_tbody = re.search(
            r'<table[^>]*id=["\']fast-order-table["\'][^>]*>.*?<tbody>(.*?)</tbody>',
            html, re.S | re.I
        )
        if not m_tbody:
            return None
        tbody = m_tbody.group(1)

        rows = re.findall(r'<tr[^>]*class=["\']table-row-scroll["\'][^>]*>(.*?)</tr>',
                          tbody, re.S | re.I)
        for row_html in rows:
            # КОД
            m_code = re.search(r'class=["\'][^"\']*td-sky[^"\']*["\'][^>]*>(.*?)</td>',
                               row_html, re.S | re.I)
            if not m_code:
                continue
            code_text = re.sub(r"<[^>]*>", "", m_code.group(1))
            code_digits = re.sub(r"\D+", "", code_text)
            if code_digits != str(sku):
                continue

            # цена (нормална): strike, иначе първата '... лв'
            m_strike = re.search(r"<strike[^>]*>(.*?)</strike>", row_html, re.S | re.I)
            if m_strike:
                s_txt = re.sub(r"<[^>]*>", "", m_strike.group(1))
                m_p = re.search(r"(\d+[.,]?\d*)\s*лв", s_txt, re.I)
                if m_p:
                    return m_p.group(1).replace(",", ".")

            m_any = re.search(r"(\d+[.,]?\d*)\s*(?:&nbsp;)?лв", row_html, re.I)
            if m_any:
                return m_any.group(1).replace(",", ".")

        return None
    except Exception:
        return None


# ---------------- Отваряне и извличане от продуктова страница ----------------
def scrape_product_page(driver, product_url, sku):
    driver.get(product_url)
    click_cookies_if_any(driver)

    # изчакай таблицата (ако има)
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "fast-order-table")))
    except Exception:
        pass

    # скрол (lazy-load)
    scroll_until_row_with_sku(driver, sku)

    # намери ред
    row, _ = find_row_by_sku_in_table(driver, sku)
    price = None
    status, qty = "Unknown", None

    if row:
        price = extract_price_from_row_via_selenium(row)
        status, qty = extract_status_from_row_via_selenium(row)

    # fallback за цена
    if price is None:
        price = extract_price_via_requests(product_url, sku)

    # нормализирай qty
    qty = qty if isinstance(qty, int) else 0
    return status, qty, price


# ---------------- Високо ниво: за този SKU намери „неговата“ страница ----------------
def resolve_product_for_sku(driver, sku):
    """
    1) Пробва директни /product?sku=<SKU> линкове (bg/en).
    2) Ако не стане, търси и обхожда всички кандидати, докато намери ред с този SKU.
    Връща (status, qty, price, product_url) или (None, None, None, None).
    """
    # 1) директни линкове
    for link in direct_sku_links(sku):
        try:
            resp = requests.get(link, headers=HTTP_HEADERS, timeout=15)
            if resp.status_code == 200 and "fast-order-table" in resp.text:
                status, qty, price = scrape_product_page(driver, link, sku)
                if price is not None or status != "Unknown":
                    return status, qty, price, link
        except Exception:
            continue

    # 2) търсене и обхождане
    candidates = get_search_candidates(driver, sku, limit=20)
    for link in candidates:
        status, qty, price = scrape_product_page(driver, link, sku)
        if price is not None or status != "Unknown":
            return status, qty, price, link

    return None, None, None, None


# ---------------- CSV I/O ----------------
def read_sku_codes(path):
    skus = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        _ = next(r, None)  # пропускаме хедъра
        for row in r:
            if not row: 
                continue
            val = (row[0] or "").strip()
            if not val or val.lower() == "sku":
                continue
            skus.append(val)
    if skus:
        print(f"   🧾 SKUs loaded ({len(skus)}): {', '.join(skus[:5])}{' ...' if len(skus)>5 else ''}")
    else:
        print("   🧾 No SKUs loaded from CSV.")
    return skus


def save_results(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Наличност", "Бройки", "Цена (нормална лв.)", "ProductURL"])
        w.writerows(rows)


def save_not_found(skus, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU"])
        for s in skus:
            w.writerow([s])


# ---------------- main ----------------
def main():
    skus = read_sku_codes(SKU_CSV)
    driver = create_driver()
    results, not_found = [], []

    try:
        for sku in skus:
            print(f"\n➡️ Обработвам SKU: {sku}")
            status, qty, price, link = resolve_product_for_sku(driver, sku)

            if link:
                print(f"  ✅ Страница: {link}")
            if price is not None or status not in (None, "Unknown"):
                print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")
                results.append([sku, status or "Unknown", qty or 0, price or "", link or ""])
            else:
                print(f"❌ Не намерих SKU {sku} в подходяща продуктова страница.")
                not_found.append(sku)

            time.sleep(0.25)  # учтиво забавяне, за да не ни баннат

    finally:
        driver.quit()

    save_results(results, RES_CSV)
    save_not_found(not_found, NF_CSV)
    print(f"\n✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")


if __name__ == "__main__":
    main()
