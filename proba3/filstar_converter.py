import csv
import xml.etree.ElementTree as ET
import os
from dotenv import load_dotenv

# Зареждаме .env ако съществува
load_dotenv()

# Определяме пътя според средата
if os.getenv('GITHUB_ACTIONS') == 'true':
    base_path = os.getcwd()
else:
    base_path = '/Users/vladimir/Desktop/Python/Филстар'  # <-- Смени ако е нужно

# Път към CSV файла
csv_file_path = os.path.join(base_path, 'results_filstar.csv')

CHUNK_SIZE = 1400

# Четене на CSV файла
with open(csv_file_path, mode='r', encoding='utf-8') as file:
    reader = csv.DictReader(file, delimiter=',')
    products = [
        {key.strip(): value.strip() for key, value in row.items()}
        for row in reader
    ]

print(f"➡️ Заредени продукти: {len(products)}")

# Функция за писане на XML chunk
def write_chunk_to_xml(product_chunk, index):
    root = ET.Element('products')
    for product in product_chunk:
        try:
            item = ET.SubElement(root, 'item')
            ET.SubElement(item, 'sku').text = product['SKU']
            ET.SubElement(item, 'price').text = product['Цена']
            ET.SubElement(item, 'quantity').text = product['Бройки']
            availability = 'in_stock' if product['Наличност'] == 'Наличен' else 'out_of_stock'
            ET.SubElement(item, 'availability').text = availability
        except KeyError as e:
            print(f"⚠️ Пропуснат продукт поради липсваща колона: {e} → {product}")

    file_name = f"filstar_xml_{index}.xml"
    file_path = os.path.join(base_path, file_name)
    tree = ET.ElementTree(root)
    tree.write(file_path, encoding='utf-8', xml_declaration=True)
    print(f"✅ Записан файл: {file_path} ({len(product_chunk)} продукта)")

# Разделяне и записване на файлове
for i in range(0, len(products), CHUNK_SIZE):
    chunk = products[i:i + CHUNK_SIZE]
    index = (i // CHUNK_SIZE) + 1
    write_chunk_to_xml(chunk, index)
