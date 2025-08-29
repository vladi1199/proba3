import csv
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- пътища ---
BASE = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE, "results_filstar.csv")
NF_CSV  = os.path.join(BASE, "not_found_filstar.csv")

# --- WebDriver ---
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--lang=bg-BG,bg")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    # по-„човешък“ профил
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=opts)

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

def norm(s): return (s or "").strip().replace(" ", "").replace("-", "")

# --- Емулация на АВТОСУГЕСТ ---
def open_product_via_autosuggest(driver, sku):
    q = norm(sku)
    driver.get("https://filstar.com/")
    click_cookies_if_any(driver)

    # намери полето за търсене (пробваме няколко селектора)
    search = None
    for how, sel in [
        (By.CSS_SELECTOR, "input[type='search']"),
        (By.CSS_SELECTOR, "input[name='term']"),
        (By.CSS_SELECTOR, "input[name='q']"),
        (By.XPATH, "//input[contains(@placeholder,'търси') or contains(@placeholder,'Търси')]"),
    ]:
        try:
            search = WebDriverWait(driver, 6).until(EC.presence_of_element_located((how, sel)))
            break
        except Exception:
            continue
    if not search:
        return False

    # въведи SKU бавно (имитира човек), за да се появи падащото меню
    search.clear()
    for ch in q:
        search.send_keys(ch)
        time.sleep(0.05)

    # чакаме да се покажат предложенията и избираме линка с ?sku=<код>
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href]"))
        )
    except Exception:
        pass

    # търсим в целия DOM линк от автосугеста към конкретното SKU
    target = None
    for el in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        href = el.get_attribute("href") or ""
        if f"?sku={q}" in href:
            target = el
            break
    # ако няма директно ?sku= — вземи първия линк от предложението
    if not target:
        # ограничаваме до елементите видими под полето (дропдаун)
        try:
            target = driver.find_elements(By.CSS_SELECTOR, "a[href]")[0]
        except Exception:
            target = None

    if not target:
        return False

    driver.execute_script("arguments[0].click();", target)
    # кратко изчакване за зареждане на продукта
    time.sleep(0.7)
    return True

# --- Четене на реда за SKU и цена/количество ---
def find_row_for_sku(driver, sku):
    q = norm(sku)
    # 1) стария клас table-row-<SKU>
    try:
        return driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']")
    except Exception:
        pass
    # 2) ред с клетка, която съдържа SKU
    try:
        return driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{q}')]]")
    except Exception:
        pass
    # 3) всяка клетка/елемент, съдържащ SKU → най-близкия <tr>
    try:
        cell = driver.find_element(By.XPATH, f"//*[contains(normalize-space(),'{q')]")
        return cell.find_element(By.XPATH, "./ancestor::tr")
    except Exception:
        return None

def extract_qty_and_price(row):
    # количество
    qty = 0
    try:
        inp = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
        mx = inp.get_attribute("data-max-qty-1") or inp.get_attribute("max")
        if mx and mx.isdigit():
            qty = int(mx)
    except Exception:
        pass
    status = "Наличен" if qty > 0 else "Изчерпан"

    # цена
    price = None
    try:
        price_holder = row.find_element(By.CSS_SELECTOR, "div.custom-tooltip-holder")
        try:
            strike = price_holder.find_element(By.TAG_NAME, "strike").text
            m = re.search(r"(\d+[.,]\d{2})", strike)
            if m: price = m.group(1).replace(",", ".")
        except Exception:
            m = re.search(r"(\d+[.,]\d{2})", price_holder.text)
            if m: price = m.group(1).replace(",", ".")
    except Exception:
        # fallback: вземи число + лв от самия ред
        m = re.search(r"(\d+[.,]\d{2})\s*лв", row.text.replace("\xa0", " "))
        if m: price = m.group(1).replace(",", ".")
    return status, qty, price

# --- CSV I/O ---
def read_sku_codes(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f); next(r, None)
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
        w = csv.writer(f); w.writerow(["SKU"])
        for s in skus: w.writerow([s])

# --- main ---
def main():
    skus = read_sku_codes(SKU_CSV)
    driver = create_driver()
    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")

            if not open_product_via_autosuggest(driver, sku):
                print(f"❌ Не успях да отворя продукт за {sku} през автосугест")
                not_found.append(sku)
                continue

            # леко изчакване/скрол — таблицата понякога се дорендерира
            driver.execute_script("window.scrollBy(0, 400);")
            time.sleep(0.4)

            row = find_row_for_sku(driver, sku)
            if not row:
                print(f"❌ Не беше намерен ред за SKU {sku}")
                not_found.append(sku)
                continue

            status, qty, price = extract_qty_and_price(row)
            if price is None:
                print(f"❌ Няма цена в реда за SKU {sku}")
                not_found.append(sku)
                continue

            print(f"  ✅ Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
            results.append([sku, status, qty, price])

    finally:
        driver.quit()

    write_results(results, RES_CSV)
    write_not_found(not_found, NF_CSV)
    print(f"✅ Запазени резултати: {RES_CSV}")
    print(f"❌ Ненамерени SKU кодове: {NF_CSV}")

if __name__ == "__main__":
    main()
