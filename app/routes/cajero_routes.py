import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from app.db.db import get_db
from app.services.inventory_alert_service import InventoryAlertService
from app.services.inventory_service import (
    count_variants, get_variant_summary, log_movement, parse_scan_code,
    recalc_product_summary, stock_status, table_exists,
)
from app.services.wompi_service import (
    create_checkout_data, find_transaction_by_reference, get_transaction,
    parse_event, verify_event, wompi_amount_in_cents,
)
from app.utils.security import role_required

logger = logging.getLogger(__name__)

cajero_bp = Blueprint("cajero", __name__)

WOMPI_METHODS = {"nequi", "daviplata", "pse", "tarjeta"}
MANUAL_METHODS = {"efectivo", "transferencia_manual", "mixto"}

PAYMENT_LABELS = {
    "efectivo": "Efectivo",
    "transferencia_manual": "Transferencia manual",
    "mixto": "Mixto",
    "nequi": "Nequi",
    "daviplata": "Daviplata",
    "pse": "PSE",
    "tarjeta": "Tarjeta",
    "card": "Tarjeta",
    "bancolombia_transfer": "Botón Bancolombia",
}


# =========================================================
# HELPERS
# =========================================================

def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value, default="0"):
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _get_table_columns(cur, table_name):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
    """, (table_name,))
    return {row["column_name"] for row in cur.fetchall()}


def _has_columns(cur, table_name, needed_columns):
    return set(needed_columns).issubset(_get_table_columns(cur, table_name))


def _generate_receipt_number(cur):
    """Consecutivo propio de cada tienda (la fila de la tienda queda bloqueada
    hasta el commit, así dos cajas no sacan el mismo número)."""
    cur.execute("""
        UPDATE businesses SET receipt_counter = receipt_counter + 1
        WHERE id = NULLIF(current_setting('app.business_id', true), '')::integer
        RETURNING receipt_counter
    """)
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No hay una tienda activa en la sesión.")
    return f"VENTA-{datetime.now():%Y%m%d}-{row['receipt_counter']:04d}"


def _parse_and_validate_items(raw_items):
    if not raw_items or not isinstance(raw_items, list):
        return False, "Debes agregar al menos un producto a la venta.", []

    merged = {}
    for item in raw_items:
        if not isinstance(item, dict):
            return False, "Los datos de la venta no son válidos.", []
        product_id = _to_int(item.get("product_id"))
        variant_id = _to_int(item.get("variant_id")) or None
        quantity = _to_int(item.get("quantity"))
        sale_price = _to_decimal(item.get("sale_price"), "0")

        if product_id <= 0:
            return False, "Uno de los productos enviados no es válido.", []
        if quantity <= 0:
            return False, "La cantidad de un producto no es válida.", []
        if sale_price < 0:
            return False, "El precio de venta no puede ser negativo.", []

        key = (product_id, variant_id)
        if key in merged:  # misma talla/color repetida -> se suman cantidades
            merged[key]["quantity"] += quantity
        else:
            merged[key] = {
                "product_id": product_id,
                "variant_id": variant_id,
                "quantity": quantity,
                "sale_price": sale_price,
            }

    # orden fijo para tomar los bloqueos siempre igual (evita deadlocks)
    items = sorted(merged.values(), key=lambda i: (i["product_id"], i["variant_id"] or 0))
    return True, None, items


def _lock_and_validate_products(cur, cleaned_items):
    """Bloquea productos/variantes y verifica stock. Los precios originales salen
    SIEMPRE de la base de datos, nunca del navegador."""
    validated = []
    has_variant_table = table_exists(cur, "product_variants")

    for item in cleaned_items:
        cur.execute("""
            SELECT id, name, model, size, color, barcode, price, stock
            FROM products WHERE id = %s FOR UPDATE
        """, (item["product_id"],))
        product = cur.fetchone()
        if not product:
            return False, f"No existe el producto ID {item['product_id']}.", []

        name = product.get("name") or "producto"
        qty = item["quantity"]
        variant = None

        if has_variant_table and count_variants(cur, product["id"]) > 0:
            if not item["variant_id"]:
                return False, f"Selecciona la talla y el color de «{name}».", []
            cur.execute("""
                SELECT id, size, color, stock, location
                FROM product_variants WHERE id = %s AND product_id = %s FOR UPDATE
            """, (item["variant_id"], product["id"]))
            variant = cur.fetchone()
            if not variant:
                return False, f"La talla/color elegida de «{name}» ya no existe.", []
            current_stock = _to_int(variant.get("stock"))
            size, color = variant.get("size") or "", variant.get("color") or ""
        else:
            current_stock = _to_int(product.get("stock"))
            size, color = product.get("size") or "", product.get("color") or ""

        if current_stock < qty:
            detail = f" talla {size} {color}".rstrip() if variant else ""
            return False, (f"Stock insuficiente para {name}{detail} "
                           f"(disponible: {current_stock}, solicitado: {qty})."), []

        original_price = _to_decimal(product.get("price"), "0")
        sale_price = min(item["sale_price"], original_price)
        discount_per_unit = max(original_price - sale_price, Decimal("0"))

        validated.append({
            "product_id": product["id"],
            "variant_id": variant["id"] if variant else None,
            "location": variant.get("location") if variant else None,
            "name": name,
            "model": product.get("model") or "",
            "size": size,
            "color": color,
            "barcode": product.get("barcode") or "",
            "quantity": qty,
            "stock_before": current_stock,
            "stock_after": current_stock - qty,
            "original_price": original_price,
            "sale_price": sale_price,
            "discount_per_unit": discount_per_unit,
            "line_subtotal": sale_price * qty,
        })

    return True, None, validated


def _calculate_totals(validated_items):
    subtotal = sum((i["original_price"] * i["quantity"] for i in validated_items), Decimal("0"))
    total = sum((i["line_subtotal"] for i in validated_items), Decimal("0"))
    return subtotal, max(subtotal - total, Decimal("0")), total


def _insert_sale_items(cur, sale_id, validated_items):
    for item in validated_items:
        cur.execute("""
            INSERT INTO sale_items (
                sale_id, product_id, variant_id, product_name, product_model,
                product_size, product_color, product_barcode,
                quantity, original_price, sale_price, discount_per_unit, subtotal
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            sale_id, item["product_id"], item["variant_id"], item["name"], item["model"],
            item["size"], item["color"], item["barcode"], item["quantity"],
            item["original_price"], item["sale_price"], item["discount_per_unit"], item["line_subtotal"],
        ))


def _discount_stock_and_log_movements(cur, items, receipt_or_reference, payment_method, user_id=None):
    """Descuenta stock de la VARIANTE vendida (antes solo se descontaba de products
    y el siguiente movimiento de bodega "devolvía" las unidades vendidas)."""
    touched = set()
    for item in items:
        if item.get("variant_id"):
            cur.execute("UPDATE product_variants SET stock = stock - %s WHERE id = %s",
                        (item["quantity"], item["variant_id"]))
            touched.add(item["product_id"])
        else:
            cur.execute("UPDATE products SET stock = stock - %s WHERE id = %s",
                        (item["quantity"], item["product_id"]))
        log_movement(
            cur, item["product_id"], "salida", item["quantity"],
            variant_id=item.get("variant_id"), location=item.get("location"), user_id=user_id,
            note=f"Venta {receipt_or_reference} - {PAYMENT_LABELS.get(payment_method, payment_method)}",
        )
    for product_id in touched:
        recalc_product_summary(cur, product_id)


def _send_low_stock_alerts(items, business_id=None):
    if not items:
        return
    admin_email = InventoryAlertService.admin_email_for_business(business_id or session.get("business_id"))
    for item in items:
        InventoryAlertService.check_low_stock_async(
            item["name"], item["stock_after"], size=item.get("size") or "", color=item.get("color") or "",
            location=item.get("location") or "", admin_email=admin_email,
        )


def _json_safe(row):
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# =========================================================
# CAJERO / VENTAS
# =========================================================

def _store_info(cur):
    """Datos de la tienda activa para el encabezado del recibo."""
    info = {"name": session.get("business_name") or ""}
    business_id = session.get("business_id")
    if not business_id:
        return info
    try:
        cur.execute("SELECT name, nit, phone, address, email FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
        if row:
            info.update({k: (row[k] or "") for k in ("name", "nit", "phone", "address", "email")})
    except Exception as e:  # el recibo no debe romper la página
        logger.warning("No se pudieron leer los datos de la tienda: %s", e)
    return info


@cajero_bp.route("/cajero", methods=["GET", "POST"])
@role_required("cajero")
def dashboard():
    if request.method == "POST":
        return _register_manual_sale()

    search = (request.args.get("search") or "").strip()
    sale_id = _to_int(request.args.get("sale_id"))

    conn = get_db()
    try:
        cur = conn.cursor()
        if search:
            like = f"%{search}%"
            cur.execute("""
                SELECT id, name, model, category, size, color, price, stock, barcode
                FROM products
                WHERE COALESCE(name,'') ILIKE %s OR COALESCE(model,'') ILIKE %s OR COALESCE(barcode,'') ILIKE %s
                ORDER BY name ASC, model ASC
            """, (like, like, like))
        else:
            cur.execute("""
                SELECT id, name, model, category, size, color, price, stock, barcode
                FROM products WHERE COALESCE(stock, 0) > 0
                ORDER BY name ASC, model ASC
            """)
        products = cur.fetchall()

        # Variantes con stock de cada producto, para elegir talla/color al vender
        variants_map = {}
        if products and table_exists(cur, "product_variants"):
            cur.execute("""
                SELECT id, product_id, size, color, stock
                FROM product_variants
                WHERE stock > 0 AND product_id = ANY(%s)
                ORDER BY product_id, color, size
            """, ([p["id"] for p in products],))
            for v in cur.fetchall():
                variants_map.setdefault(str(v["product_id"]), []).append(
                    {"id": v["id"], "size": v["size"] or "", "color": v["color"] or "", "stock": int(v["stock"] or 0)})

        receipt = None
        if sale_id > 0:
            cur.execute("""
                SELECT s.id, s.receipt_number, s.customer_name, s.customer_phone, s.payment_method,
                       s.note, s.subtotal, s.discount_total, s.total, s.status,
                       COALESCE(s.paid_at, s.created_at) AS fecha, u.username AS seller
                FROM sales s LEFT JOIN users u ON u.id = s.seller_user_id
                WHERE s.id = %s
            """, (sale_id,))
            sale = cur.fetchone()
            if sale:
                cur.execute("""
                    SELECT product_name, product_model, product_size, product_color, quantity,
                           original_price, sale_price, subtotal
                    FROM sale_items WHERE sale_id = %s ORDER BY id
                """, (sale_id,))
                receipt = _json_safe(sale)
                receipt["payment_label"] = PAYMENT_LABELS.get(sale["payment_method"], sale["payment_method"])
                receipt["items"] = [_json_safe(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT s.id, s.receipt_number, s.reference, s.customer_name, s.payment_method,
                   s.total, COALESCE(s.status, 'PAID') AS status, s.created_at, u.username AS seller
            FROM sales s LEFT JOIN users u ON u.id = s.seller_user_id
            WHERE s.created_at::date = CURRENT_DATE
            ORDER BY s.created_at DESC
            LIMIT 50
        """)
        today_sales = cur.fetchall()
        store = _store_info(cur)
        today_total = sum((Decimal(s["total"] or 0) for s in today_sales if s["status"] == "PAID"), Decimal("0"))
        cur.close()
    except Exception as e:
        logger.error("Error cargando módulo cajero: %s", e, exc_info=True)
        flash(f"Error cargando el módulo de ventas: {e}", "error")
        products, variants_map, receipt, today_sales, today_total = [], {}, None, [], 0
        store = {"name": session.get("business_name") or ""}
    finally:
        conn.close()

    for p in products:
        p["status"] = stock_status(p["stock"])

    return render_template(
        "cajero.html",
        products=products,
        variants_map=variants_map,
        receipt=receipt,
        today_sales=today_sales,
        today_total=today_total,
        payment_labels=PAYMENT_LABELS,
        filters={"search": search},
        store=store,
    )


def _register_manual_sale():
    customer_name = (request.form.get("customer_name") or "").strip()
    customer_phone = (request.form.get("customer_phone") or "").strip()
    payment_method = (request.form.get("payment_method") or "").strip().lower()
    sale_note = (request.form.get("sale_note") or "").strip()

    if not payment_method:
        flash("Debes seleccionar un método de pago.", "error")
        return redirect(url_for("cajero.dashboard"))
    if payment_method in WOMPI_METHODS:
        flash("Ese método se procesa por Wompi. Debes iniciarlo desde el checkout en línea.", "warning")
        return redirect(url_for("cajero.dashboard"))
    if payment_method not in MANUAL_METHODS:
        flash("Método de pago no válido.", "error")
        return redirect(url_for("cajero.dashboard"))

    try:
        items = json.loads(request.form.get("sale_items_json") or "[]")
    except (ValueError, TypeError):
        flash("Los datos de la venta no son válidos.", "error")
        return redirect(url_for("cajero.dashboard"))

    ok, error_message, cleaned_items = _parse_and_validate_items(items)
    if not ok:
        flash(error_message, "error")
        return redirect(url_for("cajero.dashboard"))

    conn = get_db()
    cur = conn.cursor()
    try:
        ok, error_message, validated = _lock_and_validate_products(cur, cleaned_items)
        if not ok:
            conn.rollback()
            flash(error_message, "error")
            return redirect(url_for("cajero.dashboard"))

        subtotal, discount_total, total = _calculate_totals(validated)
        receipt_number = _generate_receipt_number(cur)

        # status/paid_at: sin esto las ventas en efectivo NO aparecían en los reportes
        cur.execute("""
            INSERT INTO sales (
                receipt_number, customer_name, customer_phone, payment_method, note,
                subtotal, discount_total, total, seller_user_id, status, paid_at, created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'PAID',NOW(),NOW())
            RETURNING id
        """, (receipt_number, customer_name or None, customer_phone or None, payment_method,
              sale_note or None, subtotal, discount_total, total, session.get("user_id")))
        sale_id = cur.fetchone()["id"]

        _insert_sale_items(cur, sale_id, validated)
        _discount_stock_and_log_movements(cur, validated, receipt_number, payment_method, session.get("user_id"))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Error registrando venta: %s", e, exc_info=True)
        flash(f"Error procesando la venta: {e}", "error")
        return redirect(url_for("cajero.dashboard"))
    finally:
        cur.close()
        conn.close()

    _send_low_stock_alerts(validated)
    flash(f"Venta registrada correctamente. Recibo: {receipt_number}", "success")
    return redirect(url_for("cajero.dashboard", sale_id=sale_id))


# Compatibilidad: antes existía /reportes aquí y chocaba con el módulo de reportes
@cajero_bp.route("/cajero/reportes")
@role_required("admin")
def reportes():
    return redirect(url_for("reportes.dashboard"))


# =========================================================
# API PRODUCTO POR CÓDIGO
# =========================================================

@cajero_bp.route("/cajero/api/product-by-barcode", methods=["GET"])
@role_required("cajero")
def product_by_barcode():
    barcode_value, scanned_variant = parse_scan_code(request.args.get("barcode"))
    if not barcode_value:
        return jsonify({"ok": False, "message": "Código del producto requerido"}), 400

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, barcode, name, model, category, size, color, price, stock
            FROM products WHERE TRIM(barcode) = %s LIMIT 1
        """, (barcode_value,))
        product = cur.fetchone()
        if not product:
            return jsonify({"ok": False, "message": "Producto no encontrado"}), 404

        sizes_display = product.get("size") or ""
        colors_display = product.get("color") or ""
        variants = []
        if table_exists(cur, "product_variants"):
            v_sizes, v_colors, _locs, variants = get_variant_summary(cur, product["id"])
            sizes_display = v_sizes or sizes_display
            colors_display = v_colors or colors_display
    except Exception as e:
        logger.error("Error consultando producto: %s", e, exc_info=True)
        return jsonify({"ok": False, "message": "Error consultando producto"}), 500
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "product": {
            "id": product["id"],
            "barcode": product.get("barcode") or "",
            "name": product.get("name") or "",
            "model": product.get("model") or "",
            "category": product.get("category") or "",
            "size": product.get("size") or "",
            "color": product.get("color") or "",
            "sizes_display": sizes_display,
            "colors_display": colors_display,
            "price": float(product.get("price") or 0),
            "stock": int(product.get("stock") or 0),
            "status": stock_status(product.get("stock")),
            "variants": [
                {"id": v["id"], "size": v.get("size") or "", "color": v.get("color") or "",
                 "stock": int(v.get("stock") or 0)}
                for v in variants if int(v.get("stock") or 0) > 0
            ],
            # si se escaneó la etiqueta de una talla/color, el cajero la agrega directo
            "selected_variant_id": scanned_variant if any(
                v["id"] == scanned_variant and int(v.get("stock") or 0) > 0 for v in variants) else None,
        },
    })


# =========================================================
# WOMPI
# =========================================================

@cajero_bp.route("/cajero/create-checkout", methods=["POST"])
@role_required("cajero")
def create_checkout():
    data = request.get_json(silent=True) or {}
    customer_name = (data.get("customer_name") or "").strip()
    customer_phone = (data.get("customer_phone") or "").strip()
    payment_method = (data.get("payment_method") or "").strip().lower()
    note = (data.get("sale_note") or "").strip()

    if payment_method not in WOMPI_METHODS:
        return jsonify({"ok": False, "message": "Método no válido para checkout Wompi"}), 400

    ok, error_message, cleaned_items = _parse_and_validate_items(data.get("items", []))
    if not ok:
        return jsonify({"ok": False, "message": error_message}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        if not _has_columns(cur, "sales", {"reference", "status", "payment_provider",
                                           "wompi_transaction_id", "paid_at"}):
            return jsonify({"ok": False, "message": "La tabla sales no tiene las columnas de Wompi. "
                                                    "Ejecuta scripts/schema.sql."}), 500

        ok, error_message, validated = _lock_and_validate_products(cur, cleaned_items)
        if not ok:
            conn.rollback()
            return jsonify({"ok": False, "message": error_message}), 400

        subtotal, discount_total, total = _calculate_totals(validated)
        if total <= 0:
            conn.rollback()
            return jsonify({"ok": False, "message": "El total debe ser mayor a 0 para pagar con Wompi."}), 400

        receipt_number = _generate_receipt_number(cur)
        checkout = create_checkout_data(total)

        cur.execute("""
            INSERT INTO sales (
                receipt_number, reference, customer_name, customer_phone, payment_method,
                payment_provider, status, note, subtotal, discount_total, total, seller_user_id, created_at
            )
            VALUES (%s,%s,%s,%s,%s,'wompi','PENDING',%s,%s,%s,%s,%s,NOW())
            RETURNING id
        """, (receipt_number, checkout["reference"], customer_name or None, customer_phone or None,
              payment_method, note or None, subtotal, discount_total, total, session.get("user_id")))
        sale_id = cur.fetchone()["id"]
        _insert_sale_items(cur, sale_id, validated)
        conn.commit()
    except ValueError as e:  # configuración de Wompi incompleta
        conn.rollback()
        return jsonify({"ok": False, "message": str(e)}), 500
    except Exception as e:
        conn.rollback()
        logger.error("Error create_checkout: %s", e, exc_info=True)
        return jsonify({"ok": False, "message": f"No se pudo crear el checkout: {e}"}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"ok": True, "reference": checkout["reference"], "sale_id": sale_id,
                    "checkout_url": checkout["checkout_url"]})


def _apply_wompi_transaction(transaction, event_payload=None, event_type=None):
    """Aplica el resultado de una transacción de Wompi a la venta (idempotente).
    Lo usan el webhook, el retorno del checkout y el botón "Verificar pago".
    Devuelve (sale_id, estado_final) o (None, None) si no hay venta."""
    reference = transaction.get("reference")
    transaction_id = transaction.get("id")
    status = (transaction.get("status") or "").upper()
    method_type = (transaction.get("payment_method_type") or "").lower()
    amount_in_cents = transaction.get("amount_in_cents")

    if not reference:
        return None, None

    # La venta puede ser de cualquier tienda (el webhook no trae sesión):
    # primero se averigua su tienda y luego se trabaja aislado en ella.
    lookup = get_db(bypass=True)
    try:
        with lookup.cursor() as c:
            c.execute("SELECT business_id FROM sales WHERE reference = %s LIMIT 1", (reference,))
            found = c.fetchone()
    finally:
        lookup.close()
    if not found:
        logger.warning("Wompi: referencia %s no corresponde a ninguna venta.", reference)
        return None, None
    business_id = found["business_id"]

    conn = get_db(business_id=business_id)
    cur = conn.cursor()
    alerts = []
    try:
        if event_payload is not None and table_exists(cur, "payment_events"):
            cur.execute("""
                INSERT INTO payment_events (provider, event_type, reference, wompi_transaction_id, payload)
                VALUES ('wompi', %s, %s, %s, %s::jsonb)
            """, (event_type, reference, transaction_id, json.dumps(event_payload)))

        cur.execute("""
            SELECT id, status, receipt_number, payment_method, total, seller_user_id
            FROM sales WHERE reference = %s LIMIT 1 FOR UPDATE
        """, (reference,))
        sale = cur.fetchone()
        if not sale:
            conn.commit()
            return None, None

        final_status = sale["status"]

        if status == "APPROVED" and sale["status"] not in ("PAID", "STOCK_ERROR", "AMOUNT_MISMATCH"):
            expected = wompi_amount_in_cents(sale["total"] or 0)
            if amount_in_cents is not None and int(amount_in_cents) != expected:
                logger.error("Monto distinto en %s: Wompi=%s esperado=%s", reference, amount_in_cents, expected)
                cur.execute("UPDATE sales SET status = 'AMOUNT_MISMATCH', wompi_transaction_id = %s WHERE id = %s",
                            (transaction_id, sale["id"]))
                conn.commit()
                return sale["id"], "AMOUNT_MISMATCH"

            cur.execute("""
                SELECT product_id, variant_id, product_name AS name, product_size AS size,
                       product_color AS color, quantity, sale_price
                FROM sale_items WHERE sale_id = %s ORDER BY product_id, variant_id
            """, (sale["id"],))
            items = cur.fetchall()

            cleaned = [{"product_id": i["product_id"], "variant_id": i["variant_id"],
                        "quantity": _to_int(i["quantity"]), "sale_price": _to_decimal(i["sale_price"])}
                       for i in items]
            ok, message, validated = _lock_and_validate_products(cur, cleaned)

            if not ok:
                cur.execute("UPDATE sales SET status = 'STOCK_ERROR', wompi_transaction_id = %s WHERE id = %s",
                            (transaction_id, sale["id"]))
                conn.commit()
                logger.warning("STOCK_ERROR en %s: %s", reference, message)
                return sale["id"], "STOCK_ERROR"

            cur.execute("""
                UPDATE sales
                SET status = 'PAID', wompi_transaction_id = %s,
                    payment_method = COALESCE(NULLIF(%s, ''), payment_method),
                    paid_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (transaction_id, method_type, sale["id"]))
            _discount_stock_and_log_movements(
                cur, validated, sale.get("receipt_number") or reference,
                method_type or sale.get("payment_method") or "wompi", sale.get("seller_user_id"))
            final_status = "PAID"
            alerts = validated

        elif status in ("DECLINED", "VOIDED", "ERROR") and sale["status"] == "PENDING":
            cur.execute("UPDATE sales SET status = %s, wompi_transaction_id = %s WHERE id = %s",
                        (status, transaction_id, sale["id"]))
            final_status = status

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    _send_low_stock_alerts(alerts, business_id)
    return sale["id"], final_status


@cajero_bp.route("/cajero/payment-return", methods=["GET"])
def payment_return():
    """Wompi redirige aquí con ?id=<transaction_id>. Se consulta la transacción a
    Wompi (no se confía en la URL) y se aplica, así funciona aunque el webhook
    no llegue (por ejemplo en localhost)."""
    transaction_id = (request.args.get("id") or "").strip()
    reference = (request.args.get("reference") or "").strip()

    transaction = get_transaction(transaction_id) if transaction_id else None
    if not transaction and reference:
        transaction = find_transaction_by_reference(reference)

    if not transaction:
        flash("No se pudo confirmar el pago con Wompi. Usa «Verificar» en las ventas de hoy.", "warning")
        return redirect(url_for("cajero.dashboard"))

    try:
        sale_id, status = _apply_wompi_transaction(transaction)
    except Exception as e:
        logger.error("Error aplicando retorno de Wompi: %s", e, exc_info=True)
        flash("Error registrando el resultado del pago.", "error")
        return redirect(url_for("cajero.dashboard"))

    return _flash_payment_result(sale_id, status, transaction.get("reference"))


def _flash_payment_result(sale_id, status, reference):
    if not sale_id:
        flash(f"No se encontró la venta de la referencia {reference}.", "error")
        return redirect(url_for("cajero.dashboard"))
    if status == "PAID":
        flash("Pago confirmado correctamente.", "success")
        return redirect(url_for("cajero.dashboard", sale_id=sale_id))
    if status == "PENDING":
        flash(f"El pago de {reference} sigue pendiente. Verifícalo en unos minutos.", "warning")
    elif status == "STOCK_ERROR":
        flash(f"El pago de {reference} fue aprobado pero ya no hay stock suficiente. Revisa la venta "
              "y gestiona el reembolso o cambio con el cliente.", "error")
    elif status == "AMOUNT_MISMATCH":
        flash(f"El monto pagado en {reference} no coincide con el total de la venta. Revísalo en Wompi.", "error")
    else:
        flash(f"El pago terminó en estado {status}.", "error")
    return redirect(url_for("cajero.dashboard"))


@cajero_bp.route("/cajero/payment-status/<reference>", methods=["GET"])
@role_required("cajero")
def payment_status(reference):
    """Consulta a Wompi el estado de una venta pendiente (botón «Verificar»)."""
    transaction = find_transaction_by_reference(reference)
    if not transaction:
        flash(f"Wompi aún no registra un pago para {reference}.", "warning")
        return redirect(url_for("cajero.dashboard"))
    try:
        sale_id, status = _apply_wompi_transaction(transaction)
    except Exception as e:
        logger.error("Error verificando pago %s: %s", reference, e, exc_info=True)
        flash("Error verificando el pago.", "error")
        return redirect(url_for("cajero.dashboard"))
    return _flash_payment_result(sale_id, status, reference)


@cajero_bp.route("/cajero/wompi-webhook", methods=["POST"])
def wompi_webhook():
    payload = request.get_json(force=True, silent=True) or {}
    event = parse_event(payload)

    if not event.get("reference"):
        return jsonify({"ok": False, "message": "Webhook sin referencia"}), 400

    if verify_event(payload, request.headers.get("X-Event-Checksum", "")):
        transaction = (payload.get("data") or {}).get("transaction") or {}
    else:
        # Sin firma válida solo se acepta lo que confirme la API de Wompi con la llave privada
        transaction = get_transaction(event.get("transaction_id"))
        if not transaction:
            logger.warning("Webhook rechazado: firma inválida para %s", event.get("reference"))
            return jsonify({"ok": False, "message": "Firma inválida"}), 401

    try:
        _apply_wompi_transaction(transaction, event_payload=payload, event_type=event.get("event"))
    except Exception as e:
        logger.error("Error wompi_webhook: %s", e, exc_info=True)
        return jsonify({"ok": False, "message": "Error interno"}), 500

    # Siempre 200 para que Wompi no reintente indefinidamente eventos ya procesados
    return jsonify({"ok": True}), 200
