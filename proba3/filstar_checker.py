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
    # малко „деавтоматизация“ за по-малко блокиране
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def click_cookies_if_any(driver):
    """Приема бисквитки, ако се появи банер (не е фатално ако няма)."""
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
    """
    Отива на /search?term=<SKU> и взима линка към първия реален продукт
    от селектора .product-item-wapper a.product-name (динамично съдържание).
    """
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
            # чакаме да се дорендерират резултатите
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


# ---------------- Извличане на цена/наличност ----------------
AVAIL_HINTS = [
    (r"изчерпан|няма|out of stock", "Изчерпан"),
    (r"в наличност|наличен|in stock", "Наличен"),
]

def text_status_guess(text: str) -> str:
    t = (text or "").lower()
    for pat, status in AVAIL_HINTS:
        if re.search(pat, t):
            return status
    return "Unknown"


def extract_normal_price_bgn_from_dom(driver) -> str | None:
    """
    Взима НЕНАМАЛЕНАТА цена в лева от <strike> в таблицата с цени.
    Пример HTML:
    <td class="scrollable-td ">
      <div>
        <strike>26.10 лв.</strike>
        <div>14.36 лв. <br> 7.34 €</div>
      </div>
    </td>
    """
    try:
        # 1) търсим всеки <strike> в страницата и вадим число преди 'лв.'
        strikes = driver.find_elements(By.TAG_NAME, "strike")
        for st in strikes:
            raw = st.text.replace("\xa0", " ").strip()
            # вземи само българската цена (преди "лв.")
            if "лв" in raw:
                # вземи първото число с десетична запетая/точка
                m = re.search(r"(\d+[.,]?\d*)\s*лв", raw, flags=re.IGNORECASE)
                if m:
                    return m.group(1).replace(",", ".")
        # 2) алтернатива – търсим strike само в клетки с цени (ако има класове)
        tds = driver.find_elements(By.CSS_SELECTOR, "td.scrollable-td strike, td strike")
        for st in tds:
            raw = st.text.replace("\xa0", " ").strip()
            if "лв" in raw:
                m = re.search(r"(\d+[.,]?\d*)\s*лв", raw, flags=re.IGNORECASE)
                if m:
                    return m.group(1).replace(",", ".")
    except Exception:
        pass

    # 3) fallback: ако няма strike, пробвай да вземеш видима цена в лева от други елементи
    try:
        html = driver.page_source.replace("\xa0", " ")
        m = re.search(r"(\d+[.,]?\d*)\s*лв", html, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    except Exception:
        pass

    return None


def extract_availability_qty_from_dom(driver):
    """
    Опитва да намери наличност/бройка.
    Ако няма яснота, статусът е Unknown и qty=0 (или 1, ако текстът подсказва „наличен“).
    """
    status = "Unknown"
    qty = 0

    # 1) текстови индикатори на страницата
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").get_attribute("innerText")
        status = text_status_guess(body_text)
    except Exception:
        pass

    # 2) често qty е атрибут на input за количество
    qty_inputs_selectors = [
        "input[type='number']",
        "input.quantity, input.qty, input[name*='qty']",
        "td input, .quantity-plus-minus input",
    ]
    for sel in qty_inputs_selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                for attr in ["data-max-qty-1", "data-max-qty", "data-available-qty", "data-stock", "max"]:
                    v = el.get_attribute(attr)
                    if v and v.isdigit():
                        qty = max(qty, int(v))
        except Exception:
            continue

    if status == "Unknown" and qty > 0:
        status = "Наличен"
    if status == "Наличен" and qty == 0:
        # ако текстът казва наличен, но не виждаме бройки – приеми минимум 1
        qty = 1

    return status, qty


def check_product_page(driver, product_url, sku):
    """
    Зарежда продукт страницата, извлича НОРМАЛНА цена (от <strike>) и статус/qty.
    """
    driver.get(product_url)
    click_cookies_if_any(driver)
    time.sleep(1.2)

    # малко скрол за дорендер
    try:
        driver.execute_script("window.scrollBy(0, 300);")
    except Exception:
        pass
    time.sleep(0.3)

    price = extract_normal_price_bgn_from_dom(driver)
    status, qty = extract_availability_qty_from_dom(driver)
    return status, qty, price


# ---------------- CSV I/O ----------------
def read_sku_codes(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        return [row[0].strip() for row in reader if row and row[0].strip()]


def save_results(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU", "Наличност", "Бройки", "Цена (нормална)"])
        writer.writerows(rows)


def save_not_found(skus, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU"])
        for s in skus:
            writer.writerow([s])


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

            print(f"  ✅ Продукт: {product_url}")
            status, qty, price = check_product_page(driver, product_url, sku)
            print(f"     → Статус: {status} | Бройки: {qty} | Цена: {price if price else '—'}")

            if price:
                results.append([sku, status, qty, price])
            else:
                # ако няма цена, отбелязваме като ненамерен (или можеш да го оставиш в results с празна цена)
                not_found.append(sku)

            time.sleep(0.4)

    finally:
        driver.quit()

    save_results(results, RES_CSV)
    save_not_found(not_found, NF_CSV)
    print(f"\n✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")


if __name__ == "__main__":
    main()
