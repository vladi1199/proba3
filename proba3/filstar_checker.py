import csv
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Зареждаме променливите от .env файл
load_dotenv()

# Откриване на base_path спрямо локацията на текущия файл
base_path = os.path.dirname(os.path.abspath(__file__))

# ---------------------------
# Конфигурация на драйвъра
# ---------------------------
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,768")
    # лек UA (някои сайтове ограничават headless)
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    options.add_argument("--lang=bg-BG,bg")
    return webdriver.Chrome(options=options)

def click_cookies_if_any(driver):
    candidates = [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (By.XPATH, "//button[contains(., 'Приемам')]"),
        (By.XPATH, "//*[contains(., 'Приемам бисквитките')]"),
        (By.XPATH, "//button[contains(., 'Accept')]"),
    ]
    for how, what in candidates:
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((how, what))).click()
            break
        except Exception:
            pass

def norm(s):
    return (s or "").strip().replace(" ", "").replace("-", "")

def variants(sku):
    a = norm(sku)
    b = a.lstrip("0") or a
    return [a] if a == b else [a, b]

# -----------------------------------------
# Намираме URL на продукта по SKU (обход)
# -----------------------------------------
def find_product_url(driver, sku):
    """
    Търси в /search?term=SKU, взима всички линкове към продукти,
    филтрира /products/new и отваря кандидатите един по един,
    докато намери страница, на която присъства редът за SKU.
    """
    def gather_links():
        sels = [
            ".search-results .product-item a[href]",
            ".products .product-item a[href]",
            ".product-item a[href]",
            "a.product-item-link",
            "a[href*='/products/']",
        ]
        found = []
        for sel in sels:
            found.extend(driver.find_elements(By.CSS_SELECTOR, sel))
        # уникализирай
        uniq, seen = [], set()
        for el in found:
            href = el.get_attribute("href") or ""
            if not href:
                continue
            if href in seen:
                continue
            seen.add(href)
            uniq.append(href)
        # изключи общи/неподходящи
        uniq = [h for h in uniq if "/products/new" not in h]
        return uniq

    for q in variants(sku):
        search_url = f"https://filstar.com/search?term={q}"
        driver.get(search_url)
        click_cookies_if_any(driver)

        try:
            WebDriverWait(driver, 12).until(
                EC.any_of(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-item, .products, .search-results")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                )
            )
        except Exception:
            time.sleep(1.0)

        candidates = gather_links()
        if not candidates:
            continue

        # 1) Опитай кандидати, чиито href/текст съдържат SKU
        prioritized = [h for h in candidates if q in h]
        others = [h for h in candidates if h not in prioritized]
        ordered = prioritized + others

        # 2) Отваряй последователно и валидирай, че има ред за SKU
        for href in ordered[:10]:
            try:
                driver.get(href)
                time.sleep(0.6)
                # директно по стария клас:
                if driver.find_elements(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']"):
                    return href
                # fallback: търси клетка <td> с текста на SKU (колоната "КОД")
                if driver.find_elements(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{q}')]]"):
                    return href
            except Exception:
                continue

    return None

# ---------------------------------------------------
# Проверка на наличността, бройката и цената (леко)
# ---------------------------------------------------
def check_availability_and_price(driver, sku):
    try:
        # 1) твоя стар селектор
        try:
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception:
            # 2) минимален fallback – ред с клетка, която съдържа SKU (колона "КОД")
            try:
                row = driver.find_element(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{sku}')]]")
            except Exception as e2:
                print(f"❌ Не беше намерен ред с SKU {sku}: {e2}")
                return None, 0, None
        
        # qty
        qty = 0
        try:
            qty_input = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
            max_qty_attr = qty_input.get_attribute("data-max-qty-1") or qty_input.get_attribute("max")
            if max_qty_attr and max_qty_attr.isdigit():
                qty = int(max_qty_attr)
        except Exception:
            pass
        status = "Наличен" if qty > 0 else "Изчерпан"

        # price (твоята логика)
        price = None
        try:
            price_element = row.find_element(By.CSS_SELECTOR, "div.custom-tooltip-holder")
            try:
                # стара/намалена цена
                normal_price_el = price_element.find_element(By.TAG_NAME, "strike")
                raw_price = normal_price_el.text.strip()
                price = re.findall(r"\d+[.,]\d{2}", raw_price)[0].replace(",", ".")
            except Exception:
                price_text = price_element.text.strip()
                m = re.search(r"(\d+[.,]\d{2})", price_text)
                if m:
                    price = m.group(1).replace(",", ".")
        except Exception:
            # ако елементът го няма, опитай да извадиш цена от целия ред
            m = re.search(r"(\d+[.,]\d{2})\s*лв", row.text.replace("\xa0", " "))
            if m:
                price = m.group(1).replace(",", ".")

        return status, qty, price

    except Exception as e:
        print(f"❌ Грешка при проверка на наличността и цената за SKU {sku}: {e}")
        return None, 0, None

# -----------------------
# CSV вход/изход
# -----------------------
def read_sku_codes(path):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        return [row[0].strip() for row in reader if row]

def save_results(results, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU', 'Наличност', 'Бройки', 'Цена'])
        writer.writerows(results)

def save_not_found(skus_not_found, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU'])
        for sku in skus_not_found:
            writer.writerow([sku])

# -----------------------
# main
# -----------------------
def main():
    sku_file = os.path.join(base_path, 'sku_list_filstar.csv')
    result_file = os.path.join(base_path, 'results_filstar.csv')
    not_found_file = os.path.join(base_path, 'not_found_filstar.csv')

    skus = read_sku_codes(sku_file)
    driver = create_driver()
    results = []
    not_found = []

    for sku in skus:
        print(f"➡️ Обработвам SKU: {sku}")
        product_url = find_product_url(driver, sku)
        if product_url:
            print(f"  ✅ Намерен продукт: {product_url}")
            driver.get(product_url)
            time.sleep(0.5)  # кратко изчакване за JS
            status, qty, price = check_availability_and_price(driver, sku)
            
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  📦 Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
        else:
            print(f"❌ Няма валиден продукт за SKU {sku}")
            not_found.append(sku)

    driver.quit()
    
    save_results(results, result_file)
    save_not_found(not_found, not_found_file)

    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == '__main__':
    main()
