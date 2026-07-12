"""
SpyReport — API para la app embebida en el panel de Tiendanube
===============================================================
Endpoints que usa el frontend (React) que ve el comerciante:

  GET    /api/tiendas/<store_id>/competidores      → lista sus competidores
  POST   /api/tiendas/<store_id>/competidores      → agrega uno {"url": "..."}
  DELETE /api/tiendas/<store_id>/competidores/<id> → borra uno
  GET    /api/tiendas/<store_id>/comparacion       → productos propios (API
                                                     oficial) + productos de
                                                     cada competidor (scraper)

Cómo enchufarlo (igual que el anterior):

    from spyreport_api import api_bp
    app.register_blueprint(api_bp)

Requiere en requirements.txt:  flask-cors
(para que el frontend en Vercel pueda llamar a este backend en Railway)
"""

import os
from flask import Blueprint, request, jsonify
from flask_cors import CORS

# Reutilizamos el cliente de Supabase y el helper de la API de Tiendanube
from tiendanube_oauth import sb, _api_get

api_bp = Blueprint("spyreport_api", __name__)
CORS(api_bp)  # permite llamadas desde el frontend (Vercel / panel Tiendanube)


def _store_token(store_id):
    """Devuelve el access_token de una tienda instalada, o None."""
    try:
        row = (
            sb()
            .table("tiendanube_stores")
            .select("access_token")
            .eq("store_id", store_id)
            .single()
            .execute()
        )
        return row.data["access_token"] if row.data else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Competidores de una tienda
# ---------------------------------------------------------------------------
@api_bp.route("/api/tiendas/<int:store_id>/competidores", methods=["GET"])
def listar_competidores(store_id):
    if not _store_token(store_id):
        return jsonify({"error": "Tienda no instalada"}), 404

    rows = (
        sb()
        .table("tiendanube_competidores")
        .select("id, url, nombre, creado_en")
        .eq("store_id", store_id)
        .order("creado_en")
        .execute()
    )
    return jsonify(rows.data or [])


@api_bp.route("/api/tiendas/<int:store_id>/competidores", methods=["POST"])
def agregar_competidor(store_id):
    if not _store_token(store_id):
        return jsonify({"error": "Tienda no instalada"}), 404

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url.startswith("http"):
        url = "https://" + url
    if "." not in url:
        return jsonify({"error": "URL inválida"}), 400

    # Máximo 3 competidores en el plan inicial
    existentes = (
        sb()
        .table("tiendanube_competidores")
        .select("id")
        .eq("store_id", store_id)
        .execute()
    )
    if existentes.data and len(existentes.data) >= 3:
        return jsonify({"error": "Máximo 3 competidores en este plan"}), 400

    nombre = url.split("//")[-1].split("/")[0].replace("www.", "")
    row = (
        sb()
        .table("tiendanube_competidores")
        .insert({"store_id": store_id, "url": url, "nombre": nombre})
        .execute()
    )
    return jsonify(row.data[0] if row.data else {}), 201


@api_bp.route(
    "/api/tiendas/<int:store_id>/competidores/<int:comp_id>", methods=["DELETE"]
)
def borrar_competidor(store_id, comp_id):
    sb().table("tiendanube_competidores").delete().eq("id", comp_id).eq(
        "store_id", store_id
    ).execute()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Comparación: productos propios (API oficial) vs competidores (scraper)
# Este es el endpoint del "momento wow" de los primeros 5 minutos.
# ---------------------------------------------------------------------------
@api_bp.route("/api/tiendas/<int:store_id>/comparacion", methods=["GET"])
def comparacion(store_id):
    token = _store_token(store_id)
    if not token:
        return jsonify({"error": "Tienda no instalada"}), 404

    # 1. Productos propios por la API oficial
    propios_raw = _api_get(store_id, token, "products", {"per_page": 50}) or []
    propios = [
        {
            "nombre": (p.get("name") or {}).get("es") or "",
            "precio": float((p.get("variants") or [{}])[0].get("price") or 0),
        }
        for p in propios_raw
    ]

    # 2. Productos de cada competidor, con el scraper existente
    #    (import adentro de la función para evitar import circular con app.py)
    from app import scrape_with_playwright, scrape_with_pagination, is_dynamic_site, get_dominio

    comps = (
        sb()
        .table("tiendanube_competidores")
        .select("id, url, nombre")
        .eq("store_id", store_id)
        .execute()
    )

    competidores = []
    for c in comps.data or []:
        try:
            dominio = get_dominio(c["url"])
            if is_dynamic_site(c["url"]):
                productos = scrape_with_playwright(c["url"], dominio)
            else:
                productos = scrape_with_pagination(c["url"], dominio)
            competidores.append(
                {
                    "id": c["id"],
                    "nombre": c["nombre"],
                    "url": c["url"],
                    "productos": [
                        {
                            "nombre": p["name"],
                            "precio": float(p["price"] or 0),
                            "stock": p["availability"],
                        }
                        for p in (productos or [])[:50]
                    ],
                }
            )
        except Exception as e:
            competidores.append(
                {
                    "id": c["id"],
                    "nombre": c["nombre"],
                    "url": c["url"],
                    "productos": [],
                    "error": str(e),
                }
            )

    # 3. Resumen simple para mostrar arriba de todo
    precios_propios = [p["precio"] for p in propios if p["precio"] > 0]
    resumen = {
        "mis_productos": len(propios),
        "mi_precio_promedio": round(
            sum(precios_propios) / len(precios_propios), 2
        )
        if precios_propios
        else 0,
        "competidores": [],
    }
    for c in competidores:
        precios = [p["precio"] for p in c["productos"] if p["precio"] > 0]
        resumen["competidores"].append(
            {
                "nombre": c["nombre"],
                "productos": len(c["productos"]),
                "precio_promedio": round(sum(precios) / len(precios), 2)
                if precios
                else 0,
            }
        )

    return jsonify(
        {"resumen": resumen, "mis_productos": propios, "competidores": competidores}
    )
