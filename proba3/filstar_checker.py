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

# Зареждаме променливите от .env файл (ако има)
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
    options.add_argument("--window-size=1366,900")
    options.add_argument("--lang=bg-BG,bg")
    # малко по-"човешки" UA
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
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

def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

def _variants(sku: str):
    a = _norm(sku)
    b = a.lstrip("0") or a
    return [a] if a == b else [a, b]

# -----------------------------------------
# Намираме URL на продукта по SKU (търсачка)
# -----------------------------------------
def find_product_url(driver, sku):
    """
    Отваря /search?term=<SKU>, взима линковете от картите и връща най-подходящия.
    """
    SEARCH_URLS = [
        "https://filstar.com/search?term={q}",
        "https://filstar.com/bg/search?term={q}",
    ]

    def collect_links():
        # покрива картите в резултатите (по скрийншота)
        sels = [
            ".search-results a[href]",
            ".products a[href]",
            ".product-item a[href]",
            "a.product-item-link",
        ]
        seen, hrefs = set(), []
        for sel in sels:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href = el.get_attribute("href") or ""
                if not href or href in seen:
                    continue
                seen.add(href)
                hrefs.append(href)
        # махаме общи/листови
        hrefs = [h for h in hrefs if "/products/new" not in h and "/search?" not in h]
        return hrefs

    for q in _variants(sku):
        for tmpl in SEARCH_URLS:
            driver.get(tmpl.format(q=q))
            click_cookies_if_any(driver)

            try:
                WebDriverWait(driver, 12).until(
