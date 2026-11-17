"""Módulo de etiquetas QR para impresoras térmicas.

Pensado para la mini impresora Bluetooth "Label Printer P1" (Shenzhen Tuofengda,
app ToPrint) y cualquier impresora de etiquetas similar: la etiqueta se dibuja en
el navegador al tamaño exacto del rollo (203 dpi) y se descarga/comparte como
imagen PNG en blanco y negro puro, que la app de la impresora imprime tal cual.

Rutas:
  GET /etiquetas                      página para diseñar e imprimir etiquetas
"""
import logging
import socket

from flask import Blueprint, render_template, request, session

from app.db.db import get_db
from app.services.inventory_service import build_scan_code, get_variants, table_exists
from app.utils.security import role_required

logger = logging.getLogger(__name__)

etiquetas_bp = Blueprint("etiquetas", __name__, url_prefix="/etiquetas")

# Tamaños de rollo más comunes (ancho x alto en mm)
LABEL_SIZES = [
    (50, 30), (40, 30), (50, 25), (40, 40), (50, 50), (60, 40), (70, 50), (30, 20), (40, 20), (57, 30),
]


@etiquetas_bp.route("")
@role_required("bodega", "cajero")
def page():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, barcode, name, model, category, size, color, price, stock
                FROM products ORDER BY name, model
            """)
            products = cur.fetchall()
            has_variants = table_exists(cur, "product_variants")
            data = []
            for p in products:
                variants = get_variants(cur, p["id"]) if has_variants else []
                data.append({
                    "id": p["id"],
                    "barcode": (p["barcode"] or "").strip(),
                    "name": p["name"] or "",
                    "model": p["model"] or "",
                    "category": p["category"] or "",
                    "size": p["size"] or "",
                    "color": p["color"] or "",
                    "price": float(p["price"] or 0),
                    "stock": int(p["stock"] or 0),
                    "code": build_scan_code(p["barcode"]),
                    "variants": [{
                        "id": v["id"],
                        "size": v["size"] or "",
                        "color": v["color"] or "",
                        "stock": int(v["stock"] or 0),
                        "code": build_scan_code(p["barcode"], v["id"]),
                    } for v in variants],
                })
    finally:
        conn.close()

    return render_template(
        "etiquetas.html",
        products=data,
        label_sizes=LABEL_SIZES,
        preselect={
            "product_id": request.args.get("product_id", type=int),
            "variant_id": request.args.get("variant_id", type=int),
        },
        store_name=session.get("business_name") or "",
        phone_url=_phone_url(),
    )


def _phone_url():
    """Dirección con la que el celular (misma Wi-Fi) puede abrir esta página."""
    host = request.host.split(":")[0]
    port = request.host.split(":")[1] if ":" in request.host else ""
    if host in ("localhost", "127.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))     # no envía nada: solo averigua la IP de la red local
                host = s.getsockname()[0]
        except OSError:
            host = "IP-DEL-PC"
    return f"http://{host}{':' + port if port else ''}/etiquetas"
