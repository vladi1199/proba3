#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import xml.etree.ElementTree as ET
import os
import re
from dotenv import load_dotenv

CHUNK_SIZE = 1400  # колко продукта на XML файл

# ================== ПЪТИЩА ==================
load_dotenv()
if os.getenv('GITHUB_ACTIONS') == 'true':
    base_path = os.getcwd()
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

csv_file_path = os.path.join(base_path, 'results_filstar.csv')

# ================== УТИЛИТИ ==================
def norm(s):
    return (s or '').strip()

def first_existing(row, keys):
    """Върни първата непразна стойност от дадени ключове (с толеранс към липсващи колони)."""
    for k in keys:
        if k in row and norm(row[k]) != '':
            return row[k]
    return ''

def extract_lv_price(text):
    """Взима число-цена (в лева) от произволен текст."""
    t = norm(text)
    m = re.search(r'(\d+[.,]?\d*)\s*лв', t, flags=re.I)
    if not m:
        m = re.search(r'(\d+[.,]?\d*)', t)
    if m:
        return m.group(1).replace(',', '.')
    return ''

def normalize_qty(q):
    """
    Ако е '-', празно или няма число → връща '' (празно).
    В противен случай връща само числовата стойност.
    """
    q = norm(q)
    if q == '-' or q == '':
        return ''
    m = re.search(r'\d+', q)
    return m.group(0) if m else ''

def availability_from_text(txt):
    val = norm(txt).lower()
    return 'in_stock' if val == 'наличен' else 'out_of_stock'

# ================== ЧЕТЕНЕ НА CSV ==================
if not os.path.exists(csv_file_path):
    print(f"❌ Не откривам results_filstar.csv: {csv_file_path}")
    raise SystemExit(1)

with open(csv_file_path, mode='r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    products = []
    for row in reader:
        row = { (k or '').strip(): (v or '').strip() for k, v in row.items() }

        sku_val = first_existing(row, ['SKU', 'sku'])
        price_val = first_existing(row, ['Цена (лв.)', 'Цена', 'price', 'Price'])
        qty_val = first_existing(row, ['Бройки', 'quantity', 'Qty', 'qty'])
        avail_val = first_existing(row, ['Наличност', 'availability', 'Availability'])

        price_val = extract_lv_price(price_val)
        qty_val = normalize_qty(qty_val)
        availability = availability_from_text(avail_val)

        # Ако SKU е празно → пропускаме реда
        if norm(sku_val) == '':
            continue

        products.append({
            'sku': sku_val,
            'price': price_val,
            'quantity': qty_val,   # може да е ''
            'availability': availability
        })

print(f"➡️ Заредени продукти: {len(products)}")

# ================== ЗАПИС НА XML ==================
def write_chunk_to_xml(product_chunk, index):
    root = ET.Element('products')
    for p in product_chunk:
        item = ET.SubElement(root, 'item')
        ET.SubElement(item, 'sku').text = p['sku']
        ET.SubElement(item, 'price').text = p['price']
        ET.SubElement(item, 'quantity').text = p['quantity']  # празно ако няма
        ET.SubElement(item, 'availability').text = p['availability']

    file_name = f"filstar_xml_{index}.xml"
    file_path = os.path.join(base_path, file_name)
    tree = ET.ElementTree(root)
    tree.write(file_path, encoding='utf-8', xml_declaration=True)
    print(f"✅ Записан файл: {file_path} ({len(product_chunk)} продукта)")

if not products:
    print("ℹ️ Няма продукти за писане в XML.")
else:
    for i in range(0, len(products), CHUNK_SIZE):
        chunk = products[i:i + CHUNK_SIZE]
        index = (i // CHUNK_SIZE) + 1
        write_chunk_to_xml(chunk, index)
