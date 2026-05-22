from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright

app = Flask(__name__)

def scrape_with_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        
        # Scrollear hasta abajo para cargar todos los productos
        prev_height = 0
        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
            curr_height = page.evaluate("document.body.scrollHeight")
            if curr_height == prev_height:
                break
            prev_height = curr_height
        
        productos = []
        items = page.query_selector_all('.js-item-product')
        
        for item in items:
            nombre_el = item.query_selector('.js-item-name')
            nombre = nombre_el.inner_text().strip() if nombre_el else ''
            
            precio_el = item.query_selector('.js-price-display')
            precio = precio_el.get_attribute('data-product-price') if precio_el else '0'
            
            stock_el = item.query_selector('.js-addtocart')
            stock = 'InStock' if stock_el else 'OutOfStock'
            
            if nombre:
                productos.append({
                    'name': nombre,
                    'price': precio or '0',
                    'availability': stock,
                    'url': url
                })
        
        browser.close()
        return productos

@app.route('/scrape', methods=['GET'])
def scrape():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL requerida'}), 400
    
    try:
        productos = scrape_with_playwright(url)
        return jsonify(productos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
