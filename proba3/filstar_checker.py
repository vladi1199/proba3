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

base_path = os.path.dirname(os.path.abspath(__file__))

# ---------------------------
# WebDriver
# ---------------------------
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--lang=bg-BG,bg")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36")
    # лек anti-bot
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=options)

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

def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "").replace("-", "")

def _variants(sku: str):
    a = _norm(sku)
    b = a.lstrip("0") or a
    return [a] if a == b else [a, b]

# -----------------------------------------
# Намираме URL на продукта по SKU
# -----------------------------------------
def find_product_url(driver, sku):
    """
    Търси в /search?term=<SKU>, събира линкове от картите,
    обхожда ги и връща първата продуктова страница, където
    има ред за конкретния SKU (клас table-row-<sku> или <td> със SKU).
    """
    SEARCH_URLS = [
        "https://filstar.com/search?term={q}",
        "https://filstar.com/bg/search?term={q}",
    ]

    # много широк набор от селектори за линкове в резултатите
    LINK_SELECTORS = [
        ".search-results a[href]",
        ".products a[href]",
        ".product-item a[href]",
        "a.product-item-link",
        "a[href^='/products/']",
        "a[href*='/products/']",
        "a[href^='https://filstar.com/products/']",
    ]

    def collect_links():
        hrefs, seen = [], set()
        for sel in LINK_SELECTORS:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href = el.get_attribute("href") or ""
                if not href:
                    continue
                if any(bad in href for bad in ["/products/new", "/search?term="]):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                hrefs.append(href)
        return hrefs

    # валидира, че продуктовата страница съдържа ред с търсения SKU
    def page_matches(driver, q):
        # 1) старият клас
        if driver.find_elements(By.CSS_SELECTOR, f"tr[class*='table-row-{q}']"):
            return True
        # 2) клетка с „КОД“ съдържаща SKU
        if driver.find_elements(By.XPATH, f"//tr[.//td[contains(normalize-space(),'{q}')]]"):
            return True
        return False

    for q in _variants(sku):
        for tmpl in SEARCH_URLS:
            driver.get(tmpl.format(q=q))
            click_cookies_if_any(driver)
            try:
                WebDriverWait(driver, 12).until(
                    EC.any_of(
                        EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".search-results, .products, .product-item")),
                        EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Няма резултати') or contains(.,'няма резултати')]"))
                    )
                )
            except Exception:
                time.sleep(1.0)

            links = collect_links()
            if not links:
                continue

            # приоритизирай линковете, които съдържат кода
            prio = [h for h in links if q in h]
            ordered = prio + [h for h in links if h not in prio]

            # отвори до 20 кандидата и валидирай
            for href in ordered[:20]:
