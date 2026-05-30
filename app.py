from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import time

app = Flask(__name__)

TIENDAS_CENTAVOS = [
    'esenzzia.com.ar',
    'perfumistas.com.ar',
    'benitoboutique.com.ar',
    'midway.com.ar',
    'bombaproject.com.ar',
    'fraganciadecant.com.ar',
    'minianima.com.ar',
    'janasports.com.ar',
]

def get_dominio(url):
    try:
        return url.split('/')[2].replace('www.', '')
    except:
        return ''

def has_pagination(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup.select_one('.swiper-button-next') is not None
    except:
        return False

def text_to_price(texto):
    if not texto:
        return '0'
    limpio = texto.replace('$', '').replace(' ', '').strip()
    if ',' in limpio:
        limpio = limpio.replace('.', '').replace(',', '')
    else:
        limpio = limpio.replace('.', '')
    return limpio if limpio.isdigit() else '0'

def normalize_price(precio_str, dominio):
    try:
        precio = int(precio_str)
        if dominio in TIENDAS_CENTAVOS:
            return str(precio // 100)
        return str(precio)
    except:
        return '0'

def extract_price(contenedor, dominio):
    precio_tag = contenedor.find(class_='js-price-display')
    if precio_tag:
        precio = precio_tag.get('data-product-price', '0')
        if precio and precio != '0' and precio != 'None':
            return normalize_price(precio, dominio)
        texto = precio_tag.get_text(strip=True)
        if texto and '$' in texto:
            raw = text_to_price(texto)
            return normalize_price(raw, dominio)
    
    precio_tag2 = contenedor.find(class_='item-price')
    if precio_tag2:
        texto = precio_tag2.get_text(strip=True)
        if texto and '$' in texto:
            raw = text_to_price(texto)
            return normalize_price(raw, dominio)
    
    for tag in contenedor.find_all(attrs={'data-price': True}):
        precio = tag.get('data-price', '0')
        if precio and precio != '0':
            return normalize_price(precio, dominio)

    return '0'

def scrape_page_static(url, headers, dominio):
    res = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(res.text, 'html.parser')
    productos = []
    base_url = f"https://{url.split('/')[2]}"
    
    for contenedor in soup.select('.js-item-product'):
        nombre_tag = contenedor.find(class_='js-item-name')
        if not nombre_tag:
            continue
        nombre = nombre_tag.get_text(strip=True)
        
        link_tag = contenedor.find('a')
        link = link_tag.get('href', '') if link_tag else ''
        if link and not link.startswith('http'):
            link = f"{base_url}{link}"
        
        precio = extract_price(contenedor, dominio)
        
        stock_tag = contenedor.find(class_='js-addtocart')
        stock = 'InStock' if stock_tag else 'OutOfStock'
        
        if nombre:
            productos.append({'name': nombre, 'price': precio or '0', 'availability': stock, 'url': link or url})
    
    next_page = soup.select_one('.swiper-button-next')
    return productos, next_page

def scrape_with_pagination(url, dominio):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    todos = []
    page = 1
    base_url = url.rstrip('/')
    start_time = time.time()
    MAX_SECONDS = 80

    while page <= 25:
        if time.time() - start_time > MAX_SECONDS:
            break
        
        page_url = f"{base_url}/page/{page}/" if page > 1 else url
        try:
            productos, next_page = scrape_page_static(page_url, headers, dominio)
        except:
            break
        
        if not productos:
            break
        todos.extend(productos)
        if not next_page:
            break
        page += 1
    
    return todos

def scrape_with_playwright(url, dominio):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)
        
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        
        html = page.content()
        browser.close()
        
        soup = BeautifulSoup(html, 'html.parser')
        productos = []
        base_url = f"https://{url.split('/')[2]}"
        
        for contenedor in soup.select('.js-item-product'):
            nombre_tag = contenedor.find(class_='js-item-name')
            if not nombre_tag:
                continue
            nombre = nombre_tag.get_text(strip=True)
            
            link_tag = contenedor.find('a')
            link = link_tag.get('href', '') if link_tag else ''
            if link and not link.startswith('http'):
                link = f"{base_url}{link}"
            
            precio = extract_price(contenedor, dominio)
            
            stock_tag = contenedor.find(class_='js-addtocart')
            stock = 'InStock' if stock_tag else 'OutOfStock'
            
            if nombre:
                productos.append({'name': nombre, 'price': precio or '0', 'availability': stock, 'url': link or url})
        
        return productos

def is_dynamic_site(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        return '__NEXT_DATA__' in res.text or 'next/dist' in res.text
    except:
        return False

@app.route('/scrape', methods=['GET'])
def scrape():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL requerida'}), 400
    
    try:
        dominio = get_dominio(url)
        dinamico = is_dynamic_site(url)
        
        if dinamico:
            productos = scrape_with_playwright(url, dominio)
        elif has_pagination(url):
            productos = scrape_with_pagination(url, dominio)
        else:
            productos = scrape_with_playwright(url, dominio)
            
        return jsonify(productos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
