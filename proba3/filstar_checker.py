import csv
import os
import re
import time
from urllib.parse import urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------ Пътища ------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")

# ------------ WebDriver ------------
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
    # по-"човешки" профил
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver

def click_cookies_if_any(driver):
    for how, sel in [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(.,'Приемам')]"),
        (By.XPATH, "//*[contains(.,'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(.,'Accept')]"),
    ]:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, sel))).click()
            break
        except Exception:
            pass

def norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

def parse_sku_from_url(url: str):
    try:
        q = parse_qs(urlparse(url).query)
        v = q.get("sku", [])
        if v:
            return norm(v[0])
    except Exception:
        pass
    return None

# ------------ Извличане от продуктова страница ------------
PRICE_SELECTORS = [
    ".discount-price", ".regular-price", ".price", ".product-price", ".final-price",
    "[class*='price'] span", "[class*='price']"
]

AVAIL_PATTERNS = [
    (r"изчерпан|няма|out of stock", "Изчерпан"),
    (r"в наличност|наличен|in stock", "Наличен"),
]

def extract_price_from_dom(driver):
    # 1) селектори
    for sel in PRICE_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            txt = el.text.strip().replace("\xa0", " ")
            m = re.search(r"(\d+[.,]\d{2})", txt)
            if m:
                return m.group(1).replace(",", ".")
        except Exception:
            continue
    # 2) regex върху целия HTML
    try:
        html = driver.page_source.replace("\xa0", " ")
        m = re.search(r"(\d+[.,]\d{2})\s*(лв|bgn|lv)", html, re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    except Exception:
        pass
    return None

def extract_availability_from_dom(driver):
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return "Unknown"
    for pat, status in AVAIL_PATTERNS:
        if re.search(pat, body):
            return status
    return "Unknown"

def extract_from_product_page(driver, expected_sku):
    # лек скрол за дорендер
    try:
        driver.execute_script("window.scrollBy(0, 300);")
    except Exception:
        pass
    time.sleep(0.4)

    # ако URL има ?sku= и съвпада, приемаме правилна вариация
    url_sku = parse_sku_from_url(driver.current_url)
    if url_sku and url_sku != norm(expected_sku):
        return None, 0, None

    price = extract_price_from_dom(driver)
    status = extract_availability_from_dom(driver)
    qty = 1 if status == "Наличен" else 0
    return status, qty, price

# ------------ Търсене: изчакай JS и вземи <a href*='sku='> ------------
def find_product_link_via_search(driver, sku) -> str | None:
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
            # Изчакай JS да дорендерира линковете
            WebDriverWait(driver, 12).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href]"))
            )
            # пробвай директно да намериш котва с ?sku=
            anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='sku=']")
            for a in anchors:
                href = a.get_attribute("href") or ""
                if f"sku={q}" in href:
                    return href

            # fallback: ако няма видими ?sku=, опитай да извадиш от вътрешни onclick/данни
            # (някои сайтове добавят ?sku по JS при клик; тогава се пробва да добавим ръчно параметъра)
            # вземи първия смислен продуктов линк и добави ?sku=
            for a in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
                href = a.get_attribute("href") or ""
                if not href:
                    continue
                if "/product" in href or "/products" in href:
                    sep = "&" if "?" in href else "?"
                    candidate = f"{href}{sep}sku={q}"
                    return candidate

        except Exception:
            continue
    return None

# ------------ CSV I/O ------------
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

# ------------ main ------------
def main():
    skus = read_sku_codes(SKU_CSV)
    driver = create_driver()
    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")

            # 1) Намери реалния линк през search (динамичен DOM)
            link = find_product_link_via_search(driver, sku)
            if not link:
                print(f"❌ Не намерих продуктова страница с ?sku= за {sku}")
                not_found.append(sku)
                continue

            # 2) Отвори продукта
            driver.get(link)
            time.sleep(0.8)
            print(f"   🌐 Product URL: {driver.current_url}")

            status, qty, price = extract_from_product_page(driver, sku)
            if status is None:
                print(f"❌ Не успях да извадя данни от {driver.current_url}")
                not_found.append(sku)
                continue

            print(f"  ✅ Продукт: {driver.current_url}")
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")
            results.append([sku, status, qty, price if price else ""])

            # учтиво темпо
            time.sleep(0.5)

    finally:
        driver.quit()

    write_results(results, RES_CSV)
    write_not_found(not_found, NF_CSV)
    print(f"✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")

if __name__ == "__main__":
    main()
