from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import json
import re

app = Flask(__name__)

def has_pagination(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup.select_one('.swiper-button-next') is not None
    except:
        return False

def extract_price_from_html(contenedor):
    """Intenta extraer el precio por múltiples métodos"""
    
    # Método 1: data-product-price en js-price-display
    precio_tag = contenedor.find(class_='js-price-display')
    if precio_tag:
        precio = precio_tag.get('data-product-price', '0')
        if precio and precio != '0':
            return precio
    
    # Método 2: data-price en cualquier elemento
    for tag in contenedor.find_all(attrs={'data-price': True}):
        precio = tag.get('data-price', '0')
        if precio and precio != '0':
            return precio
    
    # Método 3: buscar en scripts JSON de variantes
    scripts = contenedor.find_all('script')
    for script in scripts:
        if script.string and 'price' in script.string.lower():
            try:
                # Buscar patrones de precio en JSON
                match = re.search(r'"price"\s*:\s*(\d+)', script.string)
                if match:
                    return match.group(1)
            except:
                pass
    
    # Método 4: buscar texto con formato de precio
    precio_tag = contenedor.find(class_=re.compile(r'price|precio', re.I))
    if precio_tag:
        texto = precio_tag.get_text(strip=True)
        # Extraer números del texto de precio
        numeros = re.findall(r'[\d\.]+', texto.replace(',', '.'))
        if numeros:
            precio_limpio = numeros[0].replace('.', '')
            if len(precio_limpio) > 2:  # evitar precios de 1-2 dígitos
                return precio_limpio + '00'  # convertir a centavos
    
    return '0'

def scrape_page_static(url, headers):
    res = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(res.text, 'html.parser')
    productos = []
    base_url = f"https://{url.split('/')[2]}"
    
    # Intentar extraer precios del JSON global de la página
    precios_json = {}
    for script in soup.find_all('script'):
        if script.string:
            # Buscar objeto products o catalog en scripts
            match = re.search(r'var\s+products\s*=\s*(\[.*?\]);', script.string, re.DOTALL)
            if not match:
                match = re.search(r'"variants"\s*:\s*(\[.*?\])', script.string, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    for item in data if isinstance(data, list) else [data]:
                        if isinstance(item, dict) and 'id' in item and 'price' in item:
                            precios_json[str(item['id'])] = str(item['price'])
                except:
                    pass
    
    for contenedor in soup.select('.js-item-product'):
        nombre_tag = contenedor.find(class_='js-item-name')
        if not nombre_tag:
            continue
        nombre = nombre_tag.get_text(strip=True)
        
        link_tag = contenedor.find('a')
        link = link_tag.get('href', '') if link_tag else ''
        if link and not link.startswith('http'):
            link = f"{base_url}{link}"
        
        # Extraer precio con múltiples métodos
        precio = extract_price_from_html(contenedor)
        
        # Si precio sigue siendo 0, buscar en data attributes del contenedor
        if precio == '0':
            product_id = contenedor.get('data-product-id', '')
            if product_id and product_id in precios_json:
                precio = precios_json[product_id]
        
        stock_tag = contenedor.find(class_='js-addtocart')
        stock = 'InStock' if stock_tag else 'OutOfStock'
        
        if nombre:
            productos.append({'name': nombre, 'price': precio or '0', 'availability': stock, 'url': link or url})
    
    next_page = soup.select_one('.swiper-button-next')
    return productos, next_page

def scrape_with_pagination(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    todos = []
    page = 1
    base_url = url.rstrip('/')
    
    while page <= 25:
        page_url = f"{base_url}/page/{page}/" if page > 1 else url
        productos, next_page = scrape_page_static(page_url, headers)
        if not productos:
            break
        todos.extend(productos)
        if not next_page:
            break
        page += 1
    
    return todos

def scrape_with_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_timeout(2000)
        
        # Scroll para cargar productos
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
        # Intentar extraer precios del JSON global de la página
        precios_json = {}
        try:
            scripts = page.query_selector_all('script')
            for script in scripts:
                content = script.inner_text()
                if content and ('price' in content.lower() or 'precio' in content.lower()):
                    # Buscar variantes con precios
                    matches = re.findall(r'"price"\s*:\s*(\d+)', content)
                    ids = re.findall(r'"id"\s*:\s*(\d+)', content)
                    if matches and ids:
                        for i, pid in enumerate(ids):
                            if i < len(matches):
                                precios_json[pid] = matches[i]
        except:
            pass
        
        productos = []
        base_url = f"https://{url.split('/')[2]}"
        items = page.query_selector_all('.js-item-product')
        
        for item in items:
            nombre_el = item.query_selector('.js-item-name')
            nombre = nombre_el.inner_text().strip() if nombre_el else ''
            
            link_el = item.query_selector('a')
            link = link_el.get_attribute('href') if link_el else ''
            if link and not link.startswith('http'):
                link = f"{base_url}{link}"
            
            # Método 1: data-product-price
            precio = '0'
            precio_el = item.query_selector('.js-price-display')
            if precio_el:
                precio = precio_el.get_attribute('data-product-price') or '0'
            
            # Método 2: buscar data-price en cualquier elemento hijo
            if precio == '0':
                try:
                    precio_data = item.evaluate("""el => {
                        const priceEl = el.querySelector('[data-price]');
                        if (priceEl) return priceEl.getAttribute('data-price');
                        const priceEl2 = el.querySelector('[data-product-price]');
                        if (priceEl2) return priceEl2.getAttribute('data-product-price');
                        return '0';
                    }""")
                    if precio_data and precio_data != '0':
                        precio = precio_data
                except:
                    pass
            
            # Método 3: buscar en JSON embebido del producto
            if precio == '0':
                try:
                    precio_json = item.evaluate("""el => {
                        const scripts = el.querySelectorAll('script');
                        for (const s of scripts) {
                            const match = s.textContent.match(/"price"\\s*:\\s*(\\d+)/);
                            if (match) return match[1];
                        }
                        return '0';
                    }""")
                    if precio_json and precio_json != '0':
                        precio = precio_json
                except:
                    pass
            
            # Método 4: buscar por product_id en JSON global
            if precio == '0':
                try:
                    product_id = item.evaluate("el => el.getAttribute('data-product-id') || ''")
                    if product_id and product_id in precios_json:
                        precio = precios_json[product_id]
                except:
                    pass
            
            # Método 5: clickear primera variante si existe y capturar precio
            if precio == '0':
                try:
                    variante = item.query_selector('.js-product-variants li:first-child, .js-variation-option:first-child, [data-variant-id]:first-child')
                    if variante:
                        variante.click()
                        page.wait_for_timeout(500)
                        precio_el2 = item.query_selector('.js-price-display')
                        if precio_el2:
                            precio = precio_el2.get_attribute('data-product-price') or '0'
                except:
                    pass
            
            stock_el = item.query_selector('.js-addtocart')
            stock = 'InStock' if stock_el else 'OutOfStock'
            
            if nombre:
                productos.append({'name': nombre, 'price': precio or '0', 'availability': stock, 'url': link or url})
        
        browser.close()
        return productos

@app.route('/scrape', methods=['GET'])
def scrape():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL requerida'}), 400
    
    try:
        if has_pagination(url):
            productos = scrape_with_pagination(url)
        else:
            productos = scrape_with_playwright(url)
        return jsonify(productos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
