 #!/usr/bin/env python3
# -*- coding: utf-8 -*-

# === Selenium вариант — без проверка за бройки ===
# - На всяко пускане обхожда всички SKU от CSV (без resume).
# - Търси през /search?term=<sku> и събира кандидат продуктови линкове.
# - Отваря продуктите, намира точния ред по "КОД" и:
#     * Цена: нормалната (от <strike> ако има; иначе първата „... лв.“ в реда)
#     * Наличност: ако редът съдържа tooltip "Изчерпан продукт!" / Email иконата за нотификация → "Изчерпан", иначе "Наличен"
# - Не чете и не записва бройки (пише "-" за колона „Бройки“).
# - Серийно и щадящо (леки паузи).

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

# ---------------- ПЪТИЩА ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKU_CSV = os.path.join(BASE_DIR, "sku_list_filstar.csv")
RES_CSV = os.path.join(BASE_DIR, "results_filstar.csv")
NF_CSV  = os.path.join(BASE_DIR, "not_found_filstar.csv")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

SEARCH_URL = "https://filstar.com/search?term={q}"

# ---------------- НАСТРОЙКИ ----------------
REQUEST_WAIT = 0.5      # пауза след зареждане на страница
BETWEEN_SKU  = 0.6      # пауза между SKU
PAGE_TIMEOUT = 60       # WebDriverWait timeout
MAX_CANDIDATES = 12     # максимум кандидат-линкове от търсене
REQUEST_WAIT = 0.5
BETWEEN_SKU  = 0.6
PAGE_TIMEOUT = 20
MAX_CANDIDATES = 12

# ---------------- ПОМОЩНИ ----------------
def only_digits(s: str) -> str:
return re.sub(r"\D+", "", s or "")

def save_debug_html(driver, sku: str, tag: str):
try:
path = os.path.join(DEBUG_DIR, f"debug_{sku}_{tag}.html")
with open(path, "w", encoding="utf-8") as f:
f.write(driver.page_source)
print(f"   🐞 Debug HTML записан: {path}")
except Exception:
pass

def create_driver() -> webdriver.Chrome:
opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1280,2200")
driver = webdriver.Chrome(options=opts)
driver.set_page_load_timeout(PAGE_TIMEOUT)
return driver

def init_result_files():
with open(RES_CSV, "w", newline="", encoding="utf-8") as f:
csv.writer(f).writerow(["SKU", "Наличност", "Бройки", "Цена (лв.)"])
with open(NF_CSV, "w", newline="", encoding="utf-8") as f:
csv.writer(f).writerow(["SKU"])

def append_result(row):
with open(RES_CSV, "a", newline="", encoding="utf-8") as f:
csv.writer(f).writerow(row)

def append_nf(sku: str):
with open(NF_CSV, "a", newline="", encoding="utf-8") as f:
csv.writer(f).writerow([sku])

def read_skus(path: str):
out = []
with open(path, newline="", encoding="utf-8") as f:
r = csv.reader(f)
        _ = next(r, None)  # хедър
        _ = next(r, None)
for row in r:
            if not row: continue
            if not row:
                continue
v = (row[0] or "").strip()
if v and v.lower() != "sku":
out.append(v)
return out

# ---------------- ТЪРСЕНЕ ----------------
def get_search_candidates(driver, sku: str):
url = SEARCH_URL.format(q=sku)
driver.get(url)
time.sleep(REQUEST_WAIT)

try:
WebDriverWait(driver, PAGE_TIMEOUT).until(
EC.presence_of_element_located((By.CSS_SELECTOR, "main"))
)
except Exception:
pass

links = []

    # a) .product-item-wapper a.product-name
try:
for a in driver.find_elements(By.CSS_SELECTOR, ".product-item-wapper a.product-name"):
href = (a.get_attribute("href") or "").strip()
if href:
if href.startswith("/"):
href = urljoin("https://filstar.com", href)
links.append(href)
except Exception:
pass

    # b) .product-title a (алтернативен шаблон)
try:
for a in driver.find_elements(By.CSS_SELECTOR, ".product-title a"):
href = (a.get_attribute("href") or "").strip()
if href:
if href.startswith("/"):
href = urljoin("https://filstar.com", href)
links.append(href)
except Exception:
pass

    # премахни дубли, ограничи брой
seen, uniq = set(), []
for h in links:
if h not in seen:
seen.add(h)
uniq.append(h)

return uniq[:MAX_CANDIDATES]

# ---------------- ПРОДУКТОВА СТРАНИЦА ----------------
def extract_from_product_page(driver, sku: str):
    """
    Намира реда по 'КОД' (точно SKU) в #fast-order-table.
    Връща (status, qty_placeholder, price_lv), като qty_placeholder = "-"
    Наличност:
      - ако в реда има блок за изчерпан продукт (Email икона + tooltip / data-target="#send-request") → "Изчерпан"
      - иначе → "Наличен"
    """
try:
WebDriverWait(driver, PAGE_TIMEOUT).until(
EC.presence_of_element_located((By.CSS_SELECTOR, "#fast-order-table tbody"))
)
except Exception:
return None, None, None

tbody = driver.find_element(By.CSS_SELECTOR, "#fast-order-table tbody")
    rows  = tbody.find_elements(By.CSS_SELECTOR, "tr")
    rows = tbody.find_elements(By.CSS_SELECTOR, "tr")
target = None

    # 1) намери ред по td.td-sky == SKU
for row in rows:
try:
code_td = row.find_element(By.CSS_SELECTOR, "td.td-sky")
            code_text = code_td.text.strip()
            if only_digits(code_text) == str(sku):
            if only_digits(code_td.text.strip()) == str(sku):
target = row
break
except Exception:
continue

    # 2) fallback: търси SKU като чист текст в целия ред
if target is None:
for row in rows:
try:
                txt = row.text
                if re.search(rf"\b{re.escape(str(sku))}\b", txt):
                if re.search(rf"\b{re.escape(str(sku))}\b", row.text):
target = row
break
except Exception:
continue

if target is None:
return None, None, None

    # --- Цена (нормална, лв.) ---
    # --- Цена (евро, ненамалена ако има) ---
price = None
try:
        # ако има <strike> → нормална цена
strike_el = target.find_element(By.TAG_NAME, "strike")
        txt = strike_el.text.strip()
        m = re.search(r"(\d+[.,]?\d*)\s*лв", txt, re.I)
        m = re.search(r"(\d+[.,]?\d*)\s*€", strike_el.text)
if m:
price = m.group(1).replace(",", ".")
except Exception:
        # иначе взимаме първата "… лв." от реда
        pass

    if price is None:
try:
            m2 = re.search(r"(\d+[.,]?\d*)\s*лв", target.text, re.I)
            m2 = re.search(r"(\d+[.,]?\d*)\s*€", target.text)
if m2:
price = m2.group(1).replace(",", ".")
except Exception:
pass

# --- Наличност само по tooltip/email (без бройки) ---
    status = "Наличен"  # по подразбиране
    status = "Наличен"
try:
        # Варианти, по които разпознаваме „Изчерпан“:
        # 1) има елемент с data-target="#send-request" (бутон „известете ме“)
target.find_element(By.CSS_SELECTOR, "[data-target='#send-request']")
status = "Изчерпан"
except Exception:
        # 2) има tooltip със „Изчерпан продукт!“ в текста
try:
if "Изчерпан продукт!" in target.text:
status = "Изчерпан"
else:
                # 3) има иконка Email (alt="Shopping cart") вътре в custom-tooltip-holder
                emails = target.find_elements(By.CSS_SELECTOR, ".custom-tooltip-holder img[alt='Shopping cart']")
                emails = target.find_elements(
                    By.CSS_SELECTOR,
                    ".custom-tooltip-holder img[alt='Shopping cart']"
                )
if emails:
status = "Изчерпан"
except Exception:
pass

    qty_placeholder = "-"  # вече НЕ четем бройки
    qty_placeholder = "-"
return status, qty_placeholder, price

# ---------------- ОБРАБОТКА НА 1 SKU ----------------
def process_one_sku(driver, sku: str):
print(f"\n➡️ Обработвам SKU: {sku}")

candidates = get_search_candidates(driver, sku)
if not candidates:
save_debug_html(driver, sku, "search_no_results")
        print(f"❌ Не намерих резултати за {sku}")
append_nf(sku)
return

for link in candidates:
try:
driver.get(link)
time.sleep(REQUEST_WAIT)
status, qty_ph, price = extract_from_product_page(driver, sku)
if price is not None:
print(f"  ✅ {sku} → {price} лв. | {status} | {link}")
append_result([sku, status or "Наличен", qty_ph, price])
return
except Exception:
continue

save_debug_html(driver, sku, "no_price_or_row")
    print(f"❌ Не намерих SKU {sku} в продуктови страници")
append_nf(sku)

# ---------------- MAIN ----------------
def main():
if not os.path.exists(SKU_CSV):
print(f"❌ Липсва {SKU_CSV}")
return

init_result_files()
skus = read_skus(SKU_CSV)
print(f"🧾 Общо SKU в CSV: {len(skus)}")

driver = create_driver()
try:
for sku in skus:
process_one_sku(driver, sku)
            time.sleep(BETWEEN_SKU)  # щадяща пауза
            time.sleep(BETWEEN_SKU)
finally:
driver.quit()

print(f"\n✅ Резултати: {RES_CSV}")
print(f"📄 Not found: {NF_CSV}")

if __name__ == "__main__":
main()
