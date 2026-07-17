from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import time
import re

app = Flask(__name__)

from tiendanube_oauth import tiendanube_bp
app.register_blueprint(tiendanube_bp)

from spyreport_api import api_bp
app.register_blueprint(api_bp)


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
    """Convierte el texto visible de un precio (ej. '$39.000' o 'R$89,90')
    a un entero de pesos/reales. El texto visible SIEMPRE viene en la moneda
    ya mostrada, nunca en centavos, así que acá NO se divide por 100.
    - Con coma decimal ('89,90' o '1.234,56'): la coma es decimal, el punto
      es separador de miles. Nos quedamos con la parte entera (89, 1234).
    - Sin coma ('39.000'): el punto es separador de miles (39000).
    """
    if not texto:
        return '0'
    limpio = texto.replace('$', '').replace('R', '').replace(' ', '').strip()
    if ',' in limpio:
        parte_entera = limpio.split(',')[0]
        parte_entera = parte_entera.replace('.', '')
        limpio = parte_entera
    else:
        limpio = limpio.replace('.', '')
    return limpio if limpio.isdigit() else '0'


def price_from_attribute(precio_attr):
    """El atributo data-product-price / data-price de Tiendanube viene en
    CENTAVOS (ej. '3900000' = $39.000). Se divide por 100 para pasarlo a la
    moneda visible. Maneja '3900000' y '3900000.00'."""
    try:
        centavos = float(str(precio_attr).replace(',', '.'))
        return str(int(centavos // 100))
    except:
        return '0'


def extract_price(contenedor, dominio):
    # 1) Fuente preferida: atributo data-product-price (viene en CENTAVOS)
    precio_tag = contenedor.find(class_='js-price-display')
    if precio_tag:
        precio_attr = precio_tag.get('data-product-price', '0')
        if precio_attr and precio_attr not in ('0', 'None'):
            resultado = price_from_attribute(precio_attr)
            if resultado != '0':
                return resultado
        texto = precio_tag.get_text(strip=True)
        if texto and '$' in texto:
            return text_to_price(texto)

    # 2) Clase item-price: texto visible, ya en la moneda mostrada
    precio_tag2 = contenedor.find(class_='item-price')
    if precio_tag2:
        texto = precio_tag2.get_text(strip=True)
        if texto and '$' in texto:
            return text_to_price(texto)

    # 3) Atributo data-price genérico: viene en CENTAVOS
    for tag in contenedor.find_all(attrs={'data-price': True}):
        precio_attr = tag.get('data-price', '0')
        if precio_attr and precio_attr != '0':
            resultado = price_from_attribute(precio_attr)
            if resultado != '0':
                return resultado

    # 4) Último recurso: cualquier tag con 'price'/'precio' en la clase → texto
    for tag in contenedor.find_all(True):
        clases = ' '.join(tag.get('class', []))
        if 'price' in clases.lower() or 'precio' in clases.lower():
            texto = tag.get_text(strip=True)
            if texto and '$' in texto:
                resultado = text_to_price(texto)
                if resultado != '0':
                    return resultado

    return '0'


def get_nombre(contenedor):
    nombre_tag = contenedor.find(class_='js-item-name')
    if nombre_tag:
        return nombre_tag.get_text(strip=True)
    nombre_tag = contenedor.find(class_='item-name')
    if nombre_tag:
        return nombre_tag.get_text(strip=True)
    return ''


def parse_productos(soup, url, dominio):
    productos = []
    base_url = f"https://{url.split('/')[2]}"
    contenedores = soup.select('.js-item-product, .item-product')
    for contenedor in contenedores:
        nombre = get_nombre(contenedor)
        if not nombre:
            continue
        link_tag = contenedor.find('a')
        link = link_tag.get('href', '') if link_tag else ''
        if link and not link.startswith('http'):
            link = f"{base_url}{link}"
        precio = extract_price(contenedor, dominio)
        stock_tag = contenedor.find(class_='js-addtocart') or contenedor.find(class_='item-actions')
        stock = 'InStock' if stock_tag else 'OutOfStock'
        productos.append({
            'name': nombre,
            'price': precio or '0',
            'availability': stock,
            'url': link or url
        })
    return productos


def scrape_page_static(url, headers, dominio):
    res = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(res.text, 'html.parser')
    productos = parse_productos(soup, url, dominio)
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
        page.goto(url, timeout=60000)
        try:
            page.wait_for_selector('.js-item-product, .item-product', timeout=15000)
        except:
            pass
        page.wait_for_timeout(3000)
        for _ in range(5):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
        soup = BeautifulSoup(html, 'html.parser')
        return parse_productos(soup, url, dominio)


def is_dynamic_site(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        return '__NEXT_DATA__' in res.text or 'next/dist' in res.text
    except:
        return False


def scrape_ads_playwright(pagina):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        url = f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=AR&q={pagina}&search_type=keyword_unordered"
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
        soup = BeautifulSoup(html, 'html.parser')
        texto_completo = soup.get_text()
        patron = r'Library ID:\s*(\d+)Started running on ([^\n]+?(?:20\d{2}))'
        matches = list(re.finditer(patron, texto_completo))
        anuncios = []
        for match in matches:
            ad_id = match.group(1).strip()
            fecha = match.group(2).strip()
            inicio = match.end()
            fragmento = texto_completo[inicio:inicio+500]
            copy = fragmento.replace('Platforms', '').replace('Open Dropdown', '').replace('See ad details', '').replace('This ad has multiple versions', '').replace('See summary details', '').replace('2 ads', '').strip()
            copy = re.sub(r'\s+', ' ', copy)[:250]
            fragmento_antes = texto_completo[max(0, match.start()-200):match.start()]
            nombre_pagina = pagina
            anuncios.append({
                'id': ad_id,
                'url': f"https://www.facebook.com/ads/library/?id={ad_id}",
                'inicio': fecha,
                'copy': copy
            })
        return anuncios[:10]


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


@app.route('/ads', methods=['GET'])
def scrape_ads():
    pagina = request.args.get('pagina')
    if not pagina:
        return jsonify({'error': 'Parámetro pagina requerido'}), 400
    try:
        anuncios = scrape_ads_playwright(pagina)
        return jsonify({
            'pagina': pagina,
            'cantidad': len(anuncios),
            'anuncios': anuncios
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'SpyReport Scraper OK'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
