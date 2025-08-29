import csv
import os
import re
import time
import pathlib
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

base_path = os.path.dirname(os.path.abspath(__file__))
ART_DIR = pathlib.Path(base_path) / "artifacts"
ART_DIR.mkdir(exist_ok=True)

# ---------------------------
# WebDriver
# ---------------------------
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--lang=bg-BG,bg")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    return webdriver.Chrome(options=opts)

def save_debug(driver, name):
    try:
        driver.save_screenshot(str(ART_DIR / f"{name}.png"))
        with open(ART_DIR / f"{name}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass

def click_cookies_if_any(driver):
    for how, what in [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(., 'Приемам')]"),
        (By.XPATH, "//*[contains(., 'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(., 'Accept')]"),
    ]:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, what))).click()
            break
        except Exception:
            pass

def norm(s): 
    return (s or "").strip().replace(" ", "").replace("-", "")

# ---------------------------
# Парс от картата в търсачката
# ---------------------------
def search_card_extract(driver, sku):
    """
    Връща (url, price_text) от първата карта в резултатите.
    """
    s = norm(sku)
    urls = [
        f"https://filstar.com/search?term={s}",
        f"https://filstar.com/bg/search?term={s}",
    ]

    for u in urls:
        driver.get(u)
        click_cookies_if_any(driver)

        # изчакай да се появят карти или текст "няма резултати"
        try:
            WebDriverWait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".search-results, .products, .product-item")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                )
            )
        except Exception:
            time.sleep(0.8)

        # малък скрол – често картите се дорендерират
        driver.execute_script("window.scrollBy(0, 200);")
        time.sleep(0.2)

        # вземи първата карта/линк и текста за цена в картата
        card_link = None
        for sel in [".product-item a[href]", ".products a[href]", ".search-results a[href]"]:
            links = driver.find_elements(By.CSS_SELECTOR, sel)
            if links:
                # игнорирай общи страници
                for el in links:
                    href = el.get_attribute("href") or ""
                    if href and "/products/new" not in href and "/search?term=" not in href:
                        card_link = href
                        break
            if card_link:
                break

        if not card_link:
            save_debug(driver, f"search_no_card_{s}")
            continue

        # цена на картата – често е в елемент, който съдържа "лв"
        price_text = None
        try:
            price_el = driver.find_element(By.XPATH, "//*[contains(.,'лв') and ancestor::*[contains(@class,'product')]][1]")
            price_text = price_el.text.strip()
        except Exception:
            # fallback: първи елемент с "лв"
            try:
                price_el = driver.find_element(By.XPATH, "//*[contains(.,'лв')][1]")
                price_text = price_el.text.strip()
            except Exception:
                price_text = None

        return card_link, price_text

    return None, None

def extract_price_number(text):
    if not text:
        return None
    m = re.search(r"(\d+[.,]\d{2})", text.replace("\xa0", " "))
    return m.group(1).replace(",", ".") if m else None

# ---------------------------
# CSV I/O
# ---------------------------
def read_sku_codes(path):
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        next(r, None)
        return [row[0].strip() for row in r if row and row[0].strip()]

def save_results(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Наличност", "Бройки", "Цена", "URL"])
        w.writerows(rows)

def save_not_found(skus, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SKU"])
        for s in skus:
            w.writerow([s])

# ---------------------------
# main
# ---------------------------
def main():
    sku_file = os.path.join(base_path, "sku_list_filstar.csv")
    result_file = os.path.join(base_path, "results_filstar.csv")
    not_found_file = os.path.join(base_path, "not_found_filstar.csv")

    skus = read_sku_codes(sku_file)
    driver = create_driver()

    results, not_found = [], []

    try:
        for sku in skus:
            print(f"➡️ Обработвам SKU: {sku}")
            url, card_price_text = search_card_extract(driver, sku)
            if not url:
                print(f"❌ Няма резултат в търсачката за {sku}")
                save_debug(driver, f"no_result_{norm(sku)}")
                not_found.append(sku)
                continue

            price_num = extract_price_number(card_price_text)
            # няма инфо за наличност/брой на картата → по подразбиране Unknown/0
            status = "Unknown"
            qty = 0

            print(f"  ✅ Намерен линк: {url} | Карта цена: {card_price_text or '—'}")
            results.append([sku, status, qty, price_num or "", url])
    finally:
        driver.quit()

    save_results(results, result_file)
    save_not_found(not_found, not_found_file)
    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == "__main__":
    main()
