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

# Конфигурация на драйвъра
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,768")
    # лек UA, защото някои сайтове режат headless
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    options.add_argument("--lang=bg-BG,bg")
    return webdriver.Chrome(options=options)

# Затваряне на cookie банера (ако има)
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

# --- ПОМЕНЕНО САМО ТУК ---
# Намираме URL на продукта по SKU (новата търсачка + по-широки селектори)
def find_product_url(driver, sku):
    def variants(s):
        s = (s or "").strip().replace(" ", "").replace("-", "")
        v2 = s.lstrip("0") or s
        return [s] if v2 == s else [s, v2]

    def get_candidate_links():
        # събира възможни линкове от плочките/резултатите
        selectors = [
            ".product-item a[href]",
            "a.product-item-link",
            "a[href*='/products/']",
            "a[href*='/product']",
            "a[href*='/bg/']",
        ]
        links = []
        for sel in selectors:
            links.extend(driver.find_elements(By.CSS_SELECTOR, sel))
        # махни дубликати
        uniq = []
        seen = set()
        for el in links:
            href = el.get_attribute("href") or ""
            if href and href not in seen:
                seen.add(href)
                uniq.append(el)
        return uniq

    for q in variants(sku):
        search_url = f"https://filstar.com/search?term={q}"
        driver.get(search_url)
        click_cookies_if_any(driver)

        # изчакай нещо смислено на страницата
        try:
            WebDriverWait(driver, 12).until(
                EC.any_of(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-item, .products, .search-results")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                )
            )
        except Exception:
            # последен шанс – кратко изчакване
            time.sleep(1.0)

        links = get_candidate_links()
        if not links:
            # понякога картите имат бутон „Преглед“ – пробвай да кликнеш първия
            try:
                btn = driver.find_element(By.XPATH, "//a[contains(.,'Преглед') or contains(.,'Виж')]")
                href = btn.get_attribute("href")
                if href: 
                    return href
            except Exception:
                pass
            continue

        # 1) предпочети линк, който съдържа SKU (или без водещи нули) в href/текста
        for el in links:
            href = (el.get_attribute("href") or "")
            txt = (el.text or "").strip()
            if q in href or q in txt:
                return href

        # 2) иначе върни първия резултат
        return links[0].get_attribute("href")

    return None
# --- КРАЙ НА ПРОМЯНАТА ---

# Проверка на наличността, бройката и цената на продукта (старият ти код)
def check_availability_and_price(driver, sku):
    try:
        try:
            row = driver.find_element(By.CSS_SELECTOR, f"tr[class*='table-row-{sku}']")
        except Exception as e:
            print(f"❌ Не беше намерен ред с SKU {sku}: {e}")
            return None, 0, None
        
        qty_input = row.find_element(By.CSS_SELECTOR, "td.quantity-plus-minus input")
        max_qty_attr = qty_input.get_attribute("data-max-qty-1")
        max_qty = int(max_qty_attr) if max_qty_attr and max_qty_attr.isdigit() else 0
        status = "Наличен" if max_qty > 0 else "Изчерпан"

        price_element = row.find_element(By.CSS_SELECTOR, "div.custom-tooltip-holder")

        try:
            # Вземаме нормалната цена от <strike>
            normal_price_el = price_element.find_element(By.TAG_NAME, "strike")
            raw_price = normal_price_el.text.strip()
            price = re.findall(r'\d+\.\d+', raw_price)[0]
        except Exception:
            # Ако няма <strike>, взимаме стандартната цена
            price_text = price_element.text.strip()
            price_parts = price_text.split()
            price = re.findall(r'\d+\.\d+', price_parts[-2])[0]

        return status, max_qty, price

    except Exception as e:
        print(f"❌ Грешка при проверка на наличността и цената за SKU {sku}: {e}")
        return None, 0, None

# Четене на SKU кодове от CSV
def read_sku_codes(path):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        return [row[0].strip() for row in reader if row]

# Записване на резултатите в CSV
def save_results(results, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU', 'Наличност', 'Бройки', 'Цена'])
        writer.writerows(results)

# Записване на ненамерени SKU кодове
def save_not_found(skus_not_found, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['SKU'])
        for sku in skus_not_found:
            writer.writerow([sku])

# Основна функция
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
            # кратко изчакване за JS
            time.sleep(0.5)
            status, qty, price = check_availability_and_price(driver, sku)
            
            if status is None or price is None:
                print(f"❌ SKU {sku} не съдържа валидна информация.")
                not_found.append(sku)
            else:
                print(f"  📦 Статус: {status} | Бройки: {qty} | Цена: {price} лв.")
                results.append([sku, status, qty, price])
        else:
            not_found.append(sku)

    driver.quit()
    
    save_results(results, result_file)
    save_not_found(not_found, not_found_file)

    print(f"✅ Запазени резултати: {result_file}")
    print(f"❌ Ненамерени SKU кодове: {not_found_file}")

if __name__ == '__main__':
    main()
