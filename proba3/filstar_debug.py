import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# Скрипт за debug: извежда всички <input> и <a> елементи след зареждане на резултатите

def create_driver():
    options = Options()
    # Ако искаш браузърът да е видим, коментирай следния ред:
    # options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)

def debug_search_elements(sku):
    driver = create_driver()
    try:
        url = f"https://filstar.com/bg/products/search/?q={sku}"
        print(f"Loading {url}...")
        driver.get(url)
        time.sleep(5)  # изчакваме JS-рeNDER

        # Събираме всички <input> и <a> елементи
        inputs = driver.find_elements(By.TAG_NAME, "input")
        anchors = driver.find_elements(By.TAG_NAME, "a")

        # Пишем към файл
        out_file = os.path.join(os.getcwd(), "debug_elements.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("=== INPUT ELEMENTS ===\n")
            for inp in inputs:
                f.write(inp.get_attribute("outerHTML") + "\n")
            f.write("\n=== ANCHOR ELEMENTS ===\n")
            for a in anchors:
                f.write(a.get_attribute("outerHTML") + "\n")
        print(f"✅ debug_elements.txt записан в: {out_file}")
    finally:
        driver.quit()

if __name__ == "__main__":
    # Замени с желания SKU код
    debug_search_elements("960837")
