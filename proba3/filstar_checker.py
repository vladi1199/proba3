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


# ---------------- Помощни: цена/наличност от РЕД ----------------
def extract_normal_price_from_row(row_el):
    """От реда на таблицата взима НЕНАМАЛЕНАТА цена от <strike>… лв.; fallback – друга лв. цена в реда."""
    # 1) <strike> … лв.
    try:
        strikes = row_el.find_elements(By.TAG_NAME, "strike")
        for st in strikes:
            raw = st.text.replace("\xa0", " ")
            m = re.search(r"(\d+[.,]?\d*)\s*лв", raw, flags=re.IGNORECASE)
            if m:
                return m.group(1).replace(",", ".")
    except Exception:
        pass
    # 2) друга цена в реда (лв.)
    try:
        txt = row_el.text.replace("\xa0", " ")
        m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    except Exception:
        pass
    return None


def extract_qty_status_from_row(row_el):
    """Изважда qty/status от реда по типични атрибути и текст."""
    status = "Unknown"
    qty = 0

    # текстови индикатори
    try:
        t = row_el.text.lower()
        if any(x in t for x in ["изчерпан", "няма", "out of stock"]):
            status = "Изчерпан"
        if any(x in t for x in ["в наличност", "наличен", "in stock"]):
            status = "Наличен"
    except Exception:
        pass

    # атрибути по input/елементи
    try:
        elements = row_el.find_elements(By.CSS_SELECTOR, "input, button, div, span")
        for el in elements[:400]:
            for attr in ["data-max-qty-1", "data-max-qty", "data-available-qty", "data-stock", "max", "data-qty"]:
                v = el.get_attribute(attr)
                if v and v.isdigit():
                    qty = max(qty, int(v))
    except Exception:
        pass

    if status == "Unknown" and qty > 0:
        status = "Наличен"
    if status == "Наличен" and qty == 0:
        qty = 1
    return status, qty


# ---------------- Работа с продукт страницата ----------------
def maybe_expand_variants(driver):
    """Кликва бутони/линкове, които показват таблица с разновидности (ако има такива)."""
    texts = ["разновид", "вариант", "виж всички", "покажи", "повече"]
    for sel in ["button", "a", "[role='button']"]:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                try:
                    label = (el.text or "").strip().lower()
                    if any(t in label for t in texts):
                        el.click()
                        time.sleep(0.5)
                except Exception:
                    continue
        except Exception:
            pass


def find_variant_row_by_sku(driver, sku):
    """Намира <tr>, чийто текст съдържа SKU (с normalize-space)."""
    q = norm(sku)
    xps = [
        f"//tr[.//td[contains(normalize-space(),'{q}')]]",
        f"//tr[contains(normalize-space(),'{q}')]",
    ]
    for xp in xps:
        try:
            return driver.find_element(By.XPATH, xp)
        except Exception:
            continue
    try:
        cell = driver.find_element(By.XPATH, f"//*[contains(normalize-space(),'{q}')]")
        try:
            return cell.find_element(By.XPATH, "./ancestor::tr")
        except Exception:
            pass
    except Exception:
        pass
    return None


def save_debug_html(driver, sku):
    try:
        path = os.path.join(BASE_DIR, f"debug_{sku}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"   🐞 Записах HTML за {sku}: {path}")
    except Exception:
        pass


def scrape_product_page(driver, product_url, sku):
    """Зарежда продукта, показва разновидности (ако има), намира реда по SKU и вади цена/наличност от него."""
    driver.get(product_url)
    click_cookies_if_any(driver)
    time.sleep(1.0)
    try:
        driver.execute_script("window.scrollBy(0, 400);")
    except Exception:
        pass
    time.sleep(0.4)

    print(f"   🔎 TITLE: {driver.title.strip()[:120]}")
    print(f"   🔎 URL:   {driver.current_url}")

    maybe_expand_variants(driver)
    time.sleep(0.4)

    row = find_variant_row_by_sku(driver, sku)
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
            if not val:
                continue
            if val.lower() == "sku":
                continue
            skus.append(val)
    # Debug: покажи първите няколко прочетени SKU
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

            # записваме реда дори без цена, за да имаш статус/qty
            results.append([sku, status, qty, price or ""])

            time.sleep(0.4)

    finally:
        driver.quit()

    save_results(results, RES_CSV)
    save_not_found(not_found, NF_CSV)
    print(f"\n✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")


if __name__ == "__main__":
    main()
