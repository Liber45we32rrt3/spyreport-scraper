from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests

app = Flask(__name__)

def has_pagination(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup.select_one('.swiper-button-next') is not None
    except:
        return False

def scrape_page_static(url, headers):
    res = requests.get(url, headers=headers, timeout=15)
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
        
        precio_tag = contenedor.find(class_='js-price-display')
        precio = precio_tag.get('data-product-price', '0') if precio_tag else '0'
        
        stock_tag = contenedor.find(class_='js-addtocart')
        stock = 'InStock' if stock_tag else 'OutOfStock'
        
        if nombre:
            productos.append({'name': nombre, 'price': precio, 'availability': stock, 'url': link or url})
    
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
        
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
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
            
            precio_el = item.query_selector('.js-price-display')
            precio = precio_el.get_attribute('data-product-price') if precio_el else '0'
            
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
