from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

@app.route('/scrape', methods=['GET'])
def scrape():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL requerida'}), 400
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        productos = []
        for item in soup.select('.js-item-name'):
            nombre = item.get_text(strip=True)
            contenedor = item.find_parent('div', class_=lambda c: c and 'item' in c)
            if not contenedor:
                contenedor = item.find_parent()
            
            precio_tag = contenedor.find(class_='js-price-display') if contenedor else None
            precio = precio_tag.get('data-product-price', '0') if precio_tag else '0'
            
            stock_tag = contenedor.find(class_='js-addtocart') if contenedor else None
            stock = 'InStock' if stock_tag else 'OutOfStock'
            
            if nombre:
                productos.append({
                    'name': nombre,
                    'price': precio,
                    'availability': stock,
                    'url': url
                })
        
        return jsonify(productos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
