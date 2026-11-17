import logging

from flask import (
    Blueprint, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from app.db.db import get_db
from app.services.inventory_alert_service import InventoryAlertService
from app.services.inventory_service import (
    build_variant_groups, capitalize_words, ensure_legacy_variant,
    get_variant_summary, log_movement, low_stock_limit, normalize_text,
    parse_scan_code, recalc_product_summary, stock_status, table_exists,
)
from app.utils.security import role_required

logger = logging.getLogger(__name__)

bodega_bp = Blueprint("bodega", __name__)

DEFAULT_SIZES = ["34", "35", "36", "37", "38", "39", "40", "41", "42", "43", "44", "45"]
DEFAULT_COLORS = ["Negro", "Blanco", "Azul", "Rojo", "Verde", "Gris", "Café", "Beige", "Multicolor"]
DEFAULT_CATEGORIES = ["Calzado", "Ropa", "Accesorios"]

# Alias por compatibilidad con código que importaba estos helpers desde aquí
_row_status = stock_status
_table_exists = table_exists
_get_variant_summary = get_variant_summary
_normalize_text = normalize_text
_capitalize_words = capitalize_words


def _merge_unique(defaults, extra):
    result = list(defaults)
    for value in extra:
        value = (value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _distinct_values(cur, sql):
    cur.execute(sql)
    return [list(r.values())[0] for r in cur.fetchall()]


# =========================================================
# PANEL DE BODEGA
# =========================================================

@bodega_bp.route("/bodega")
@role_required("bodega")
def dashboard():
    search = (request.args.get("search") or "").strip()
    category = (request.args.get("category") or "").strip()
    size = (request.args.get("size") or "").strip()
    stock_filter = (request.args.get("stock") or "").strip()
    variant_id = request.args.get("variant_id", type=int)
    product_id = request.args.get("product_id", type=int)
    search, scanned_variant = parse_scan_code(search)   # QR de etiqueta con talla/color
    variant_id = variant_id or scanned_variant
    limit = low_stock_limit()

    conn = get_db()
    try:
        cur = conn.cursor()
        has_variants = table_exists(cur, "product_variants")

        query = """
            SELECT id, barcode, name, model, category, size, color, price, stock, location
            FROM products
            WHERE 1=1
        """
        params = []

        if search:
            query += """
                AND (barcode ILIKE %s OR name ILIKE %s OR model ILIKE %s OR location ILIKE %s)
            """
            like = f"%{search}%"
            params.extend([like, like, like, like])

        if category:
            query += " AND category = %s"
            params.append(category)

        if size:
            if has_variants:
                query += """
                    AND (EXISTS (SELECT 1 FROM product_variants pv
                                 WHERE pv.product_id = products.id AND pv.size = %s AND pv.stock > 0)
                         OR size = %s)
                """
                params.extend([size, size])
            else:
                query += " AND size = %s"
                params.append(size)

        if stock_filter == "available":
            query += " AND stock > %s"
            params.append(limit)
        elif stock_filter == "low":
            query += " AND stock > 0 AND stock <= %s"
            params.append(limit)
        elif stock_filter == "out":
            query += " AND stock <= 0"

        query += " ORDER BY id DESC"
        cur.execute(query, tuple(params))
        products = cur.fetchall()

        for p in products:
            p["status"] = stock_status(p["stock"])

        cur.execute("""
            SELECT COUNT(*) AS total_products,
                   COALESCE(SUM(stock), 0) AS total_units,
                   COUNT(*) FILTER (WHERE stock > 0 AND stock <= %s) AS low_stock,
                   COUNT(*) FILTER (WHERE stock <= 0) AS out_of_stock
            FROM products
        """, (limit,))
        totals = cur.fetchone()

        # ---------- producto seleccionado ----------
        selected_product = None
        select_sql = """
            SELECT id, barcode, name, model, category, size, color, price, stock, location
            FROM products WHERE {cond} LIMIT 1
        """
        if product_id:
            cur.execute(select_sql.format(cond="id = %s"), (product_id,))
            selected_product = cur.fetchone()
        if not selected_product and variant_id and has_variants:
            cur.execute(select_sql.format(
                cond="id = (SELECT product_id FROM product_variants WHERE id = %s)"), (variant_id,))
            selected_product = cur.fetchone()
        if not selected_product and search:
            cur.execute(select_sql.format(cond="barcode = %s"), (search,))
            selected_product = cur.fetchone()
        if not selected_product and products:
            selected_product = products[0]

        movements = []
        if selected_product:
            sp = selected_product
            if has_variants:
                sizes_d, colors_d, locs_d, variants = get_variant_summary(cur, sp["id"])
                sp["sizes_display"] = sizes_d or sp.get("size") or ""
                sp["colors_display"] = colors_d or sp.get("color") or ""
                sp["locations_display"] = locs_d or sp.get("location") or ""
                sp["variants"] = variants
                sp["variant_groups"] = build_variant_groups(cur, sp["id"])
                selected_variant = None
                if variant_id:
                    selected_variant = next((v for v in variants if v["id"] == variant_id), None)
                sp["selected_variant"] = selected_variant or (variants[0] if variants else None)
            else:
                sp["sizes_display"] = sp.get("size") or ""
                sp["colors_display"] = sp.get("color") or ""
                sp["locations_display"] = sp.get("location") or ""
                sp["variants"] = []
                sp["variant_groups"] = []
                sp["selected_variant"] = None

            if table_exists(cur, "inventory_movements"):
                cur.execute("""
                    SELECT m.created_at, m.movement_type, m.quantity, m.note, m.location,
                           pv.size, pv.color, u.username
                    FROM inventory_movements m
                    LEFT JOIN product_variants pv ON pv.id = m.variant_id
                    LEFT JOIN users u ON u.id = m.user_id
                    WHERE m.product_id = %s
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 15
                """, (sp["id"],))
                movements = cur.fetchall()

        known_sizes = DEFAULT_SIZES
        known_colors = DEFAULT_COLORS
        if has_variants:
            known_sizes = _merge_unique(DEFAULT_SIZES, _distinct_values(
                cur, "SELECT DISTINCT size FROM product_variants WHERE size IS NOT NULL"))
            known_colors = _merge_unique(DEFAULT_COLORS, _distinct_values(
                cur, "SELECT DISTINCT color FROM product_variants WHERE color IS NOT NULL"))
        categories = _merge_unique(DEFAULT_CATEGORIES, _distinct_values(
            cur, "SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY 1"))

        cur.close()
    finally:
        conn.close()

    return render_template(
        "bodega.html",
        products=products,
        total_products=totals["total_products"],
        total_units=totals["total_units"],
        low_stock=totals["low_stock"],
        out_of_stock=totals["out_of_stock"],
        selected_product=selected_product,
        movements=movements,
        known_sizes=known_sizes,
        known_colors=known_colors,
        categories=categories,
        filters={"search": search, "category": category, "size": size, "stock": stock_filter},
    )


# =========================================================
# ENTRADAS / SALIDAS DE STOCK
# =========================================================

@bodega_bp.route("/bodega/product/<int:product_id>/move", methods=["POST"])
@role_required("bodega")
def move_product_stock(product_id):
    movement_type = normalize_text(request.form.get("movement_type")).lower()
    size = normalize_text(request.form.get("size") or request.form.get("size_variant"))
    color = capitalize_words(request.form.get("color") or request.form.get("color_variant"))
    quantity_raw = normalize_text(request.form.get("quantity"))
    location = normalize_text(request.form.get("location")) or None
    note = normalize_text(request.form.get("note")) or None

    back = redirect(url_for("bodega.dashboard", product_id=product_id))

    if movement_type not in ("entrada", "salida"):
        flash("Tipo de movimiento inválido.", "error")
        return back
    if not size:
        flash("Debes seleccionar o escribir una talla.", "error")
        return back
    if not color:
        flash("Debes seleccionar o escribir un color.", "error")
        return back
    try:
        quantity = int(quantity_raw)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        flash("La cantidad debe ser un número entero mayor a 0.", "error")
        return back

    alert = None
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, barcode, name, size, color, stock, location
            FROM products WHERE id = %s FOR UPDATE
        """, (product_id,))
        product = cur.fetchone()
        if not product:
            flash("Producto no encontrado.", "error")
            conn.rollback()
            return redirect(url_for("bodega.dashboard"))

        # Stock que el producto tenía antes de usar variantes -> se convierte en variante
        ensure_legacy_variant(cur, product)

        cur.execute("""
            SELECT id, size, color, stock, location
            FROM product_variants
            WHERE product_id = %s AND size = %s AND LOWER(color) = LOWER(%s)
            FOR UPDATE
        """, (product_id, size, color))
        variant = cur.fetchone()

        if movement_type == "entrada":
            if variant:
                variant_id = variant["id"]
                movement_location = location or variant["location"]
                new_variant_stock = int(variant["stock"] or 0) + quantity
                cur.execute(
                    "UPDATE product_variants SET stock = %s, location = %s WHERE id = %s",
                    (new_variant_stock, movement_location, variant_id),
                )
            else:
                movement_location = location or product["location"]
                if movement_location and "," in movement_location:
                    movement_location = None  # el resumen de varias ubicaciones no es una ubicación
                new_variant_stock = quantity
                cur.execute("""
                    INSERT INTO product_variants (product_id, size, color, stock, location)
                    VALUES (%s, %s, %s, %s, %s) RETURNING id
                """, (product_id, size, color, quantity, movement_location))
                variant_id = cur.fetchone()["id"]
        else:
            if not variant:
                flash(f"No existe stock para talla {size} y color {color}.", "error")
                conn.rollback()
                return back
            current_stock = int(variant["stock"] or 0)
            if quantity > current_stock:
                flash(f"No puedes sacar {quantity} unidades. Disponible en talla {size} color {color}: "
                      f"{current_stock}.", "error")
                conn.rollback()
                return back
            variant_id = variant["id"]
            movement_location = variant["location"]
            new_variant_stock = current_stock - quantity
            cur.execute("UPDATE product_variants SET stock = %s WHERE id = %s",
                        (new_variant_stock, variant_id))

        total_stock = recalc_product_summary(cur, product_id)
        log_movement(cur, product_id, movement_type, quantity, variant_id=variant_id,
                     note=note, location=movement_location, user_id=session.get("user_id"))
        conn.commit()

        if movement_type == "salida":
            alert = dict(product_name=product["name"], current_stock=new_variant_stock,
                         size=size, color=color, location=movement_location or "")

        extra = f" | ubicación {movement_location}" if movement_location else ""
        flash(f"Movimiento registrado: {product['name']} | {movement_type} | talla {size} | color {color} | "
              f"cantidad {quantity}{extra} | stock total {total_stock}", "success")

    except Exception as e:
        conn.rollback()
        logger.error("Error registrando movimiento: %s", e, exc_info=True)
        flash(f"Error registrando movimiento: {e}", "error")
    finally:
        cur.close()
        conn.close()

    # El correo se envía DESPUÉS del commit y en segundo plano
    if alert:
        alert["admin_email"] = InventoryAlertService.admin_email_for_business(session.get("business_id"))
        InventoryAlertService.check_low_stock_async(alert.pop("product_name"), alert.pop("current_stock"), **alert)

    return back


# =========================================================
# API: PRODUCTO POR CÓDIGO
# =========================================================

@bodega_bp.route("/bodega/api/product-by-barcode")
@role_required("bodega")
def product_by_barcode():
    barcode_value, scanned_variant = parse_scan_code(request.args.get("barcode"))
    if not barcode_value:
        return jsonify({"ok": False, "message": "Barcode requerido"}), 400

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
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "product": {
            "id": product["id"],
            "barcode": product["barcode"],
            "name": product["name"],
            "model": product["model"],
            "category": product["category"],
            "size": product["size"],
            "color": product["color"],
            "sizes_display": sizes_display,
            "colors_display": colors_display,
            "price": float(product["price"] or 0),
            "stock": product["stock"],
            "status": stock_status(product["stock"]),
            "variants": [{"id": v["id"], "size": v["size"], "color": v["color"], "stock": v["stock"]}
                         for v in variants],
            "selected_variant_id": scanned_variant if any(v["id"] == scanned_variant for v in variants) else None,
        },
    })


# Los códigos de los productos se muestran e imprimen solo como QR: el QR se
# dibuja en el navegador (static/js/qr_codes.js y la página de Etiquetas).

# =========================================================
# CREAR / EDITAR PRODUCTO
# =========================================================

def _parse_price(raw):
    raw = (raw or "").strip().replace(" ", "")
    if not raw:
        return 0.0
    # admite 120000, 120.000 y 120000,50
    if raw.count(".") >= 1 and "," not in raw and len(raw.split(".")[-1]) == 3:
        raw = raw.replace(".", "")
    return float(raw.replace(",", "."))


@bodega_bp.route("/products/create", methods=["POST"])
@role_required("bodega")
def create_product():
    name = (request.form.get("name") or "").strip()
    model = (request.form.get("model") or "").strip()
    category = (request.form.get("category") or "").strip()
    size = (request.form.get("size") or "").strip()
    color = capitalize_words(request.form.get("color"))
    location = (request.form.get("location") or "").strip()
    custom_barcode = (request.form.get("barcode") or "").strip()

    if not name:
        flash("El nombre del producto es obligatorio.", "error")
        return redirect(url_for("bodega.dashboard"))

    try:
        price = _parse_price(request.form.get("price"))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Precio inválido.", "error")
        return redirect(url_for("bodega.dashboard"))

    try:
        stock = int((request.form.get("stock") or "0").strip() or 0)
        if stock < 0:
            raise ValueError
    except ValueError:
        flash("Stock inválido.", "error")
        return redirect(url_for("bodega.dashboard"))

    if stock > 0 and (not size or not color):
        flash("Para registrar stock inicial debes indicar talla y color "
              "(así el stock queda en su variante y se puede vender).", "error")
        return redirect(url_for("bodega.dashboard"))

    conn = get_db()
    cur = conn.cursor()
    try:
        if custom_barcode:
            cur.execute("SELECT 1 FROM products WHERE barcode = %s", (custom_barcode,))
            if cur.fetchone():
                flash(f"Ya existe un producto con el código {custom_barcode}.", "error")
                return redirect(url_for("bodega.dashboard"))

        cur.execute("SELECT nextval(pg_get_serial_sequence('products', 'id')) AS new_id")
        new_id = int(cur.fetchone()["new_id"])
        barcode_val = custom_barcode or f"PROD-{new_id:06d}"

        cur.execute("""
            INSERT INTO products (id, barcode, name, model, category, size, color, price, stock, location)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (new_id, barcode_val, name, model or None, category or None, size or None,
              color or None, price, stock, location or None))

        if stock > 0 and table_exists(cur, "product_variants"):
            cur.execute("""
                INSERT INTO product_variants (product_id, size, color, stock, location)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
            """, (new_id, size, color, stock, location or None))
            variant_id = cur.fetchone()["id"]
            log_movement(cur, new_id, "entrada", stock, variant_id=variant_id,
                         note="Stock inicial", location=location or None, user_id=session.get("user_id"))
            recalc_product_summary(cur, new_id)

        conn.commit()
        flash(f"Producto creado correctamente con código {barcode_val}", "success")
        return redirect(url_for("bodega.dashboard", product_id=new_id))

    except Exception as e:
        conn.rollback()
        logger.error("Error creando producto: %s", e, exc_info=True)
        flash(f"Error creando producto: {e}", "error")
        return redirect(url_for("bodega.dashboard"))
    finally:
        cur.close()
        conn.close()


@bodega_bp.route("/bodega/product/<int:product_id>/edit", methods=["POST"])
@role_required("bodega")
def edit_product(product_id):
    name = (request.form.get("name") or "").strip()
    model = (request.form.get("model") or "").strip()
    category = (request.form.get("category") or "").strip()

    if not name:
        flash("El nombre del producto es obligatorio.", "error")
        return redirect(url_for("bodega.dashboard", product_id=product_id))
    try:
        price = _parse_price(request.form.get("price"))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Precio inválido.", "error")
        return redirect(url_for("bodega.dashboard", product_id=product_id))

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products SET name = %s, model = %s, category = %s, price = %s
                WHERE id = %s
            """, (name, model or None, category or None, price, product_id))
            updated = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Error actualizando producto: {e}", "error")
        return redirect(url_for("bodega.dashboard", product_id=product_id))
    finally:
        conn.close()

    flash("Producto actualizado." if updated else "Producto no encontrado.",
          "success" if updated else "error")
    return redirect(url_for("bodega.dashboard", product_id=product_id))
