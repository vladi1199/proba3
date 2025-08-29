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


# ---------------- Пътища ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")


# ---------------- WebDriver ----------------
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1440,1000")
    opts.add_argument("--lang=bg-BG,bg,en-US,en")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
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


# ---------------- Търсене на продукт ----------------
def find_product_link_via_search(driver, sku) -> str | None:
    """Отваря /search?term=<SKU> и взима първия реален продукт от .product-item-wapper a.product-name"""
    q = norm(sku)
    search_urls = [
        f"https://filstar.com/search?term={q}",
        f"https://filstar.com/bg/search?term={q}",
        f"https://filstar.com/en/search?term={q}",
    ]
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
                return href
        except Exception:
            continue
    return None


# ---------------- Помощни: намиране на ред и цена ----------------
def wait_variant_row_by_sku(driver, sku, timeout=12):
    """
    Изчаква реда (tr) за конкретното SKU:
      КОД е в <td class="scrollable-td td-sky"> <span>КОД</span> 360947 </td>
    """
    q = norm(sku)
    xps = [
        f"//table[@id='fast-order-table']//tr[td[contains(@class,'td-sky')][contains(normalize-space(),'{q}')]]",
        f"//tr[td[contains(@class,'td-sky')][contains(normalize-space(),'{q}')]]",
    ]
    end = time.time() + timeout
    last_err = None
    while time.time() < end:
        for xp in xps:
            try:
                return driver.find_element(By.XPATH, xp)
            except Exception as e:
                last_err = e
        time.sleep(0.2)
    if last_err:
        raise last_err
    return None


def extract_normal_price_from_row(row_el):
    """
    Взима НОРМАЛНАТА цена в лева от реда:
      1) ако има <strike>… лв. → връща него (стара цена);
      2) иначе обхожда всички td.scrollable-td в реда и взима ПОСЛЕДНАТА „… лв.“.
         (избягва числата от колони „Размер / Дължина / Тест“)
    """
    # 1) strike
    try:
        strikes = row_el.find_elements(By.TAG_NAME, "strike")
        for st in strikes:
            raw = st.text.replace("\xa0", " ")
            m = re.search(r"(\d+[.,]?\d*)\s*лв", raw, flags=re.IGNORECASE)
            if m:
                return m.group(1).replace(",", ".")
    except Exception:
        pass

    # 2) последната лв. в някой от 'td.scrollable-td'
    try:
        tds = row_el.find_elements(By.CSS_SELECTOR, "td.scrollable-td")
        last_bgn = None
        for td in tds:
            txt = (td.text or "").replace("\xa0", " ")
            m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, flags=re.IGNORECASE)
            if m:
                last_bgn = m.group(1).replace(",", ".")
        if last_bgn:
            return last_bgn
    except Exception:
        pass

    # 3) fallback: всяка „… лв.“ в целия ред
    try:
        txt = row_el.text.replace("\xa0", " ")
        m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    except Exception:
        pass

    return None


def extract_qty_status_from_row(row_el):
    """
    Количествата/статусът не са видими за анонимни потребители в този шаблон.
    Все пак търсим текстови индикатори; иначе връщаме Unknown/0.
    """
    status = "Unknown"
    qty = 0

    try:
        t = row_el.text.lower()
        if any(x in t for x in ["изчерпан", "няма", "out of stock"]):
            status = "Изчерпан"
        if any(x in t for x in ["в наличност", "наличен", "in stock"]):
            status = "Наличен"
    except Exception:
        pass

    return status, qty


def save_debug_html(driver, sku):
    try:
        path = os.path.join(BASE_DIR, f"debug_{sku}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"   🐞 Записах HTML за {sku}: {path}")
    except Exception:
        pass


def scrape_product_page(driver, product_url, sku):
    """Зарежда продукта, изчаква таблицата и конкретния ред за SKU, вади цена/статус."""
    driver.get(product_url)
    click_cookies_if_any(driver)

    # 1) изчакай таблицата да се появи
    try:
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.ID, "fast-order-table")))
    except Exception:
        # някои продукти нямат таблица (без варианти) — продължаваме да търсим ред по SKU
        pass

    # 2) изчакай конкретния ред за SKU
    try:
        row = wait_variant_row_by_sku(driver, sku, timeout=12)
    except Exception:
        row = None

    print(f"   🔎 TITLE: {driver.title.strip()[:120]}")
    print(f"   🔎 URL:   {driver.current_url}")

    if not row:
        save_debug_html(driver, sku)
        return "Unknown", 0, None

    price = extract_normal_price_from_row(row)
    status, qty = extract_qty_status_from_row(row)
    return status, qty, price


# ---------------- CSV I/O ----------------
def read_sku_codes(path):
    skus = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r, None)  # пропускаме хедъра
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
        w.writerow(["SKU", "Наличност", "Бройки", "Цена (нормална лв.)"])
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

            product_url = find_product_link_via_search(driver, sku)
            if not product_url:
                print(f"❌ Не намерих продукт за {sku} в търсачката.")
                not_found.append(sku)
                continue

            print(f"  ✅ Продукт линк: {product_url}")
            status, qty, price = scrape_product_page(driver, product_url, sku)
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")

            results.append([sku, status, qty, price or ""])
            time.sleep(0.2)

    finally:
        driver.quit()

    save_results(results, RES_CSV)
    save_not_found(not_found, NF_CSV)
    print(f"\n✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")


if __name__ == "__main__":
    main()
