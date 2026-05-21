from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

def scrape_page(url, headers):
    res = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(res.text, 'html.parser')
    productos = []
    for contenedor in soup.select('.js-item-product'):
        nombre_tag = contenedor.find(class_='js-item-name')
        if not nombre_tag:
            continue
        nombre = nombre_tag.get_text(strip=True)
        precio_tag = contenedor.find(class_='js-price-display')
        precio = precio_tag.get('data-product-price', '0') if precio_tag else '0'
        stock_tag = contenedor.find(class_='js-addtocart')
        stock = 'InStock' if stock_tag else 'OutOfStock'
        if nombre:
            productos.append({'name': nombre, 'price': precio, 'availability': stock, 'url': url})
    
    next_page = soup.select_one('.swiper-button-next')
    return productos, next_page

@app.route('/scrape', methods=['GET'])
def scrape():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL requerida'}), 400
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        todos = []
        page = 1
        base_url = url.rstrip('/')
        
        while page <= 21:
            page_url = f"{base_url}/page/{page}/" if page > 1 else url
            productos, next_page = scrape_page(page_url, headers)
            if not productos:
                break
            todos.extend(productos)
            if not next_page:
                break
            page += 1
        
        return jsonify(todos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
