"""
SpyReport — Integración OAuth con Tiendanube
=============================================
Blueprint de Flask para el flujo de instalación de la app #33732.

Flujo:
  1. El comerciante instala la app desde la tienda de aplicaciones
     (o entra a /oauth/install que lo manda a autorizar)
  2. Tiendanube redirige a /oauth/callback?code=XXX
  3. Intercambiamos el code por un access_token (no expira, dura
     hasta que desinstalen la app)
  4. Guardamos token + datos de la tienda en Supabase
  5. Listo: el scraper puede usar la API oficial en vez de scrapear

Cómo enchufarlo a tu app Flask existente:

    from tiendanube_oauth import tiendanube_bp
    app.register_blueprint(tiendanube_bp)

Variables de entorno (Railway → Variables):
    TIENDANUBE_APP_ID=33732
    TIENDANUBE_CLIENT_SECRET=...   # el secret del panel, NUNCA en el código
    SUPABASE_URL=...               # ya las tenés
    SUPABASE_KEY=...               # service_role key
    APP_BASE_URL=https://tu-app.railway.app

En el panel de partners, configurá la URL de redirección como:
    https://tu-app.railway.app/oauth/callback
"""

import os
import requests
from flask import Blueprint, request, redirect, jsonify
from supabase import create_client

tiendanube_bp = Blueprint("tiendanube", __name__)

APP_ID = os.environ.get("TIENDANUBE_APP_ID", "33732")
CLIENT_SECRET = os.environ["TIENDANUBE_CLIENT_SECRET"]
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")

# Tiendanube exige User-Agent con nombre de app y un mail de contacto
USER_AGENT = "SpyReport (contacto@spyreport.app)"

AUTH_URL = f"https://www.tiendanube.com/apps/{APP_ID}/authorize"
TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"
API_BASE = "https://api.tiendanube.com/v1"

_supabase = None


def sb():
    """Cliente de Supabase (lazy, una sola instancia)."""
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
        )
    return _supabase


# ---------------------------------------------------------------------------
# 1. Punto de entrada opcional: manda al comerciante a autorizar la app.
#    Útil para links en tu landing ("Instalar en mi Tiendanube").
#    Desde la tienda de aplicaciones, Tiendanube inicia el flujo solo.
# ---------------------------------------------------------------------------
@tiendanube_bp.route("/oauth/install")
def install():
    return redirect(AUTH_URL)


# ---------------------------------------------------------------------------
# 2. Callback: Tiendanube redirige acá con ?code=XXX tras la autorización
# ---------------------------------------------------------------------------
@tiendanube_bp.route("/oauth/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Falta el parámetro 'code'"}), 400

    # 3. Intercambiar el code por el access_token
    resp = requests.post(
        TOKEN_URL,
        json={
            "client_id": APP_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    if resp.status_code != 200:
        return (
            jsonify({"error": "Fallo el intercambio de token", "detalle": resp.text}),
            502,
        )

    data = resp.json()
    access_token = data.get("access_token")
    store_id = data.get("user_id")  # Tiendanube llama "user_id" al ID de la tienda
    scope = data.get("scope", "")

    if not access_token or not store_id:
        return jsonify({"error": "Respuesta de token incompleta", "detalle": data}), 502

    # 4. Traer datos básicos de la tienda (nombre, URL, mail)
    store_info = _api_get(store_id, access_token, "store") or {}
    store_name = (store_info.get("name") or {}).get("es") or str(store_id)

    # 5. Guardar en Supabase (upsert: si reinstala, se actualiza el token)
    sb().table("tiendanube_stores").upsert(
        {
            "store_id": store_id,
            "access_token": access_token,
            "scope": scope,
            "store_name": store_name,
            "store_url": store_info.get("original_domain"),
            "email": store_info.get("email"),
        },
        on_conflict="store_id",
    ).execute()

    # 6. Devolver al comerciante a su panel (después: onboarding de SpyReport)
    return redirect(f"https://{store_info.get('original_domain', 'www.tiendanube.com')}/admin")


# ---------------------------------------------------------------------------
# Helper: GET autenticado contra la API de Tiendanube
# OJO: el header es "Authentication" (así, con N), no "Authorization".
# Es una particularidad de la API de Tiendanube.
# ---------------------------------------------------------------------------
def _api_get(store_id, token, endpoint, params=None):
    resp = requests.get(
        f"{API_BASE}/{store_id}/{endpoint}",
        headers={
            "Authentication": f"bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
        params=params or {},
        timeout=15,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


# ---------------------------------------------------------------------------
# Webhooks de privacidad (OBLIGATORIOS para publicar en la tienda de apps)
# Tiendanube los llama por POST. Como SpyReport no guarda datos de
# consumidores finales (solo productos/precios públicos), la lógica es simple:
#   - store/redact: la tienda desinstaló la app → borramos su fila y token
#   - customers/redact y customers/data_request: no almacenamos datos de
#     clientes finales, respondemos 200 confirmando que no hay nada que borrar
# ---------------------------------------------------------------------------
@tiendanube_bp.route("/webhooks/store-redact", methods=["POST"])
def webhook_store_redact():
    payload = request.get_json(silent=True) or {}
    store_id = payload.get("store_id")
    if store_id:
        sb().table("tiendanube_stores").delete().eq("store_id", store_id).execute()
    return jsonify({"ok": True}), 200


@tiendanube_bp.route("/webhooks/customers-redact", methods=["POST"])
def webhook_customers_redact():
    # SpyReport no almacena datos de consumidores finales.
    return jsonify({"ok": True, "detail": "no customer data stored"}), 200


@tiendanube_bp.route("/webhooks/customers-data-request", methods=["POST"])
def webhook_customers_data_request():
    # SpyReport no almacena datos de consumidores finales.
    return jsonify({"ok": True, "detail": "no customer data stored"}), 200


# ---------------------------------------------------------------------------
# Endpoint de prueba: lista los productos de una tienda instalada.
# Usalo para verificar que el flujo completo funciona.
#   GET /oauth/test/<store_id>
# ---------------------------------------------------------------------------
@tiendanube_bp.route("/oauth/test/<int:store_id>")
def test_products(store_id):
    row = (
        sb()
        .table("tiendanube_stores")
        .select("access_token")
        .eq("store_id", store_id)
        .single()
        .execute()
    )
    if not row.data:
        return jsonify({"error": "Tienda no instalada"}), 404

    products = _api_get(
        store_id, row.data["access_token"], "products", {"per_page": 5}
    )
    if products is None:
        return jsonify({"error": "La API no respondió"}), 502

    return jsonify(
        [
            {
                "id": p["id"],
                "nombre": (p.get("name") or {}).get("es"),
                "precio": (p.get("variants") or [{}])[0].get("price"),
            }
            for p in products
        ]
    )
