"""Lógica de inventario compartida entre Bodega y Cajero.

Modelo:
  - products.stock es el TOTAL del producto.
  - Si el producto tiene filas en product_variants (talla + color), el total
    SIEMPRE se recalcula como la suma de sus variantes.
  - products.size / color / location guardan un resumen ("38, 39, 40").
"""
from flask import current_app


VARIANT_CODE_SEPARATOR = "#V"


def build_scan_code(barcode, variant_id=None):
    """Texto que va dentro del QR de una etiqueta.
    Producto: 'PROD-000011'   Variante (talla/color): 'PROD-000011#V23'"""
    barcode = (barcode or "").strip()
    return f"{barcode}{VARIANT_CODE_SEPARATOR}{int(variant_id)}" if variant_id else barcode


def parse_scan_code(value):
    """Inverso de build_scan_code: devuelve (barcode, variant_id o None)."""
    value = (value or "").strip()
    if VARIANT_CODE_SEPARATOR in value:
        code, _, tail = value.rpartition(VARIANT_CODE_SEPARATOR)
        if tail.isdigit() and code:
            return code.strip(), int(tail)
    return value, None


def low_stock_limit():
    try:
        return int(current_app.config.get("LOW_STOCK_LIMIT", 4))
    except RuntimeError:  # fuera de contexto de app
        return 4


def stock_status(stock) -> str:
    stock = int(stock or 0)
    if stock <= 0:
        return "Agotado"
    if stock <= low_stock_limit():
        return "Stock bajo"
    return "Disponible"


def normalize_text(value):
    return (value or "").strip()


def capitalize_words(value) -> str:
    return " ".join(word.capitalize() for word in normalize_text(value).split())


def table_exists(cur, table_name: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        ) AS exists
        """,
        (table_name,),
    )
    row = cur.fetchone()
    return bool(row["exists"]) if row else False


def _size_sort_key(value):
    value = str(value or "")
    return (0, int(value), "") if value.isdigit() else (1, 0, value)


def get_variants(cur, product_id, only_in_stock=False):
    sql = """
        SELECT id, product_id, size, color, stock, location
        FROM product_variants
        WHERE product_id = %s
    """
    if only_in_stock:
        sql += " AND stock > 0"
    cur.execute(sql, (product_id,))
    rows = cur.fetchall()
    rows.sort(key=lambda r: (str(r.get("color") or ""), _size_sort_key(r.get("size")), r["id"]))
    return rows


def get_variant_summary(cur, product_id):
    """Devuelve (tallas, colores, ubicaciones, variantes) del producto."""
    rows = get_variants(cur, product_id)
    if not rows:
        return "", "", "", []

    sizes, colors, locations = [], [], []
    for row in rows:
        s = str(row.get("size") or "").strip()
        c = str(row.get("color") or "").strip()
        loc = str(row.get("location") or "").strip()
        if s and s not in sizes:
            sizes.append(s)
        if c and c not in colors:
            colors.append(c)
        if loc and loc not in locations:
            locations.append(loc)

    sizes.sort(key=_size_sort_key)
    return ", ".join(sizes), ", ".join(colors), ", ".join(locations), rows


def build_variant_groups(cur, product_id):
    """Agrupa las variantes por color para mostrarlas en tarjetas."""
    grouped = {}
    for row in get_variants(cur, product_id):
        color = (row.get("color") or "Sin color").strip()
        g = grouped.setdefault(
            color,
            {
                "variant_id": row["id"],
                "color": color,
                "location": row.get("location"),
                "stock_total": 0,
                "sizes": [],
                "items": [],
            },
        )
        stock_value = int(row.get("stock") or 0)
        size_value = row.get("size") or "—"
        g["stock_total"] += stock_value
        if str(size_value) not in g["sizes"]:
            g["sizes"].append(str(size_value))
        g["items"].append(
            {"id": row["id"], "size": size_value, "stock": stock_value, "location": row.get("location")}
        )

    groups = []
    for g in grouped.values():
        g["sizes_display"] = ", ".join(g["sizes"]) if g["sizes"] else "—"
        groups.append(g)
    return groups


def count_variants(cur, product_id) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM product_variants WHERE product_id = %s", (product_id,))
    return int(cur.fetchone()["n"] or 0)


def ensure_legacy_variant(cur, product):
    """Si el producto tiene stock pero aún no tiene variantes, convierte ese stock
    en una variante para no perderlo cuando se recalculen los totales."""
    if count_variants(cur, product["id"]) > 0:
        return None
    stock = int(product.get("stock") or 0)
    if stock <= 0:
        return None

    size = normalize_text(product.get("size"))
    color = capitalize_words(product.get("color"))
    # Si el resumen tenía varias tallas/colores no se puede saber cuál es cuál
    if not size or "," in size:
        size = "Única"
    if not color or "," in color:
        color = "Sin color"

    cur.execute(
        """
        INSERT INTO product_variants (product_id, size, color, stock, location)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (product["id"], size, color, stock, product.get("location")),
    )
    return cur.fetchone()["id"]


def recalc_product_summary(cur, product_id):
    """Recalcula products.stock/size/color/location desde sus variantes.
    Si el producto no tiene variantes no toca nada. Devuelve el stock total."""
    if count_variants(cur, product_id) == 0:
        cur.execute("SELECT stock FROM products WHERE id = %s", (product_id,))
        row = cur.fetchone()
        return int(row["stock"] or 0) if row else 0

    cur.execute(
        "SELECT COALESCE(SUM(stock), 0) AS total FROM product_variants WHERE product_id = %s",
        (product_id,),
    )
    total = int(cur.fetchone()["total"] or 0)

    sizes, colors, locations = [], [], []
    for row in get_variants(cur, product_id, only_in_stock=True):
        s = normalize_text(row.get("size"))
        c = capitalize_words(row.get("color"))
        loc = normalize_text(row.get("location"))
        if s and s not in sizes:
            sizes.append(s)
        if c and c not in colors:
            colors.append(c)
        if loc and loc not in locations:
            locations.append(loc)
    sizes.sort(key=_size_sort_key)

    cur.execute(
        """
        UPDATE products
        SET stock = %s, size = %s, color = %s, location = %s
        WHERE id = %s
        """,
        (total, ", ".join(sizes) or None, ", ".join(colors) or None, ", ".join(locations) or None, product_id),
    )
    return total


def log_movement(cur, product_id, movement_type, quantity, *, variant_id=None, note=None, location=None, user_id=None):
    cur.execute(
        """
        INSERT INTO inventory_movements
            (product_id, variant_id, movement_type, quantity, note, location, user_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (product_id, variant_id, movement_type, quantity, note, location, user_id),
    )
