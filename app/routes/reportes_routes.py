from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request

from app.db.db import get_db
from app.utils.security import role_required

reportes_bp = Blueprint('reportes', __name__, url_prefix='/reportes')


# ── helpers ────────────────────────────────────────────────────────────────────

# Fecha efectiva de la venta. Antes se usaba solo s.paid_at y las ventas en
# efectivo (sin paid_at) nunca salían en los reportes.
SALE_DATE = "COALESCE(s.paid_at, s.created_at)"
# Solo ventas pagadas (las de Wompi pendientes/rechazadas no cuentan)
PAID_ONLY = "AND COALESCE(s.status, 'PAID') = 'PAID'"


def _safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _pct_change(current, previous):
    if not previous:
        return 0
    return round((current - previous) / previous * 100, 1)


def _fetchone(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


def _fetchall(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _scalar(conn, sql, params=None):
    row = _fetchone(conn, sql, params)
    if row is None:
        return 0
    return list(row.values())[0]


def _to_date(val):
    """Convierte string ISO o date a objeto date de forma segura."""
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return date.today()
    return date.today()


def _build_report_data(conn, fecha_ini, fecha_fin, categoria, agrupacion):
    fecha_ini_date = _to_date(fecha_ini)
    fecha_fin_date = _to_date(fecha_fin)

    if fecha_ini_date > fecha_fin_date:
        fecha_ini_date, fecha_fin_date = fecha_fin_date, fecha_ini_date
    fecha_ini = fecha_ini_date.isoformat()
    fecha_fin = fecha_fin_date.isoformat()

    cat_filter = "AND p.category = %(cat)s" if categoria else ""

    def _params(extra=None):
        base = {'d1': fecha_ini, 'd2': fecha_fin}
        if categoria:
            base['cat'] = categoria
        if extra:
            base.update(extra)
        return base

    # ── KPIs ──────────────────────────────────────────────────────────────────
    today = date.today()

    def _sales_sum(date_from, date_to):
        val = _scalar(conn, f"""
            SELECT COALESCE(SUM(si.subtotal), 0)
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN products p ON p.id = si.product_id
            WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
            {cat_filter}
        """, _params({'d1': date_from, 'd2': date_to}))
        return float(val or 0)

    hoy     = today
    ayer    = hoy - timedelta(days=1)
    lun_sem = hoy - timedelta(days=hoy.weekday())
    lun_ant = lun_sem - timedelta(days=7)
    mes_ini = hoy.replace(day=1)
    mes_ant = (mes_ini - timedelta(days=1)).replace(day=1)

    v_hoy   = _sales_sum(hoy, hoy)
    v_ayer  = _sales_sum(ayer, ayer)
    v_sem   = _sales_sum(lun_sem, hoy)
    v_sem_a = _sales_sum(lun_ant, lun_ant + timedelta(days=6))
    v_mes   = _sales_sum(mes_ini, hoy)
    v_mes_a = _sales_sum(mes_ant, mes_ini - timedelta(days=1))

    kpi = {
        'ventas_hoy':    v_hoy,
        'ventas_semana': v_sem,
        'ventas_mes':    v_mes,
        'trend_hoy':     _pct_change(v_hoy,  v_ayer),
        'trend_semana':  _pct_change(v_sem,  v_sem_a),
        'trend_mes':     _pct_change(v_mes,  v_mes_a),
    }

    # ── Resumen del periodo ────────────────────────────────────────────────────
    resumen_row = _fetchone(conn, f"""
        SELECT
            COALESCE(SUM(si.subtotal), 0)                                           AS total_ventas,
            COUNT(DISTINCT si.sale_id)                                              AS num_transacciones,
            COALESCE(SUM(si.quantity), 0)                                           AS unidades_vendidas,
            COALESCE(SUM(si.subtotal) / NULLIF(COUNT(DISTINCT si.sale_id), 0), 0)  AS ticket_promedio
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
    """, _params())

    mejor_dia_row = _fetchone(conn, f"""
        SELECT ({SALE_DATE})::date AS dia, SUM(si.subtotal) AS monto
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY dia
        ORDER BY monto DESC
        LIMIT 1
    """, _params())

    num_dias = max((fecha_fin_date - fecha_ini_date).days + 1, 1)

    mejor_dia_str   = '—'
    mejor_dia_monto = 0.0
    if mejor_dia_row and mejor_dia_row['dia']:
        mejor_dia_str   = _to_date(mejor_dia_row['dia']).strftime('%d/%m/%Y')
        mejor_dia_monto = float(mejor_dia_row['monto'] or 0)

    resumen = {
        'total_ventas':      float(resumen_row['total_ventas'] or 0),
        'num_transacciones': _safe_int(resumen_row['num_transacciones']),
        'unidades_vendidas': _safe_int(resumen_row['unidades_vendidas']),
        'ticket_promedio':   float(resumen_row['ticket_promedio'] or 0),
        'promedio_unidades': round(_safe_int(resumen_row['unidades_vendidas']) / num_dias, 1),
        'mejor_dia':         mejor_dia_str,
        'mejor_dia_monto':   mejor_dia_monto,
    }

    # ── Ventas por día ─────────────────────────────────────────────────────────
    rows_dia = _fetchall(conn, f"""
        SELECT ({SALE_DATE})::date AS fecha, COALESCE(SUM(si.subtotal), 0) AS total
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY fecha
        ORDER BY fecha
    """, _params())

    ventas_dia = {
        'labels': [_to_date(r['fecha']).strftime('%d/%m') for r in rows_dia],
        'values': [float(r['total']) for r in rows_dia],
    }

    # ── Ventas por semana ──────────────────────────────────────────────────────
    rows_sem = _fetchall(conn, f"""
        SELECT
            DATE_TRUNC('week', {SALE_DATE})::date AS semana,
            COALESCE(SUM(si.subtotal), 0)        AS total
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY semana
        ORDER BY semana
    """, _params())

    ventas_semana = {
        'labels': ['Sem ' + _to_date(r['semana']).strftime('%d/%m') for r in rows_sem],
        'values': [float(r['total']) for r in rows_sem],
    }

    # ── Ventas por mes ─────────────────────────────────────────────────────────
    rows_mes = _fetchall(conn, f"""
        SELECT
            TO_CHAR(DATE_TRUNC('month', {SALE_DATE}), 'Mon YYYY') AS mes,
            COALESCE(SUM(si.subtotal), 0)                        AS total
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY DATE_TRUNC('month', {SALE_DATE}), mes
        ORDER BY DATE_TRUNC('month', {SALE_DATE})
    """, _params())

    ventas_mes = {
        'labels': [r['mes'] for r in rows_mes],
        'values': [float(r['total']) for r in rows_mes],
    }

    # ── Top / Bottom productos ─────────────────────────────────────────────────
    def _product_rank(order='DESC'):
        return _fetchall(conn, f"""
            SELECT
                si.product_name                   AS name,
                p.category,
                si.product_model                  AS model,
                COALESCE(SUM(si.quantity), 0)     AS units_sold,
                COALESCE(SUM(si.subtotal), 0)     AS revenue
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN products p ON p.id = si.product_id
            WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
            {cat_filter}
            GROUP BY si.product_id, si.product_name, p.category, si.product_model
            ORDER BY units_sold {order}
            LIMIT 10
        """, _params())

    top_products    = _product_rank('DESC')
    bottom_products = _product_rank('ASC')

    # ── Distribución por categoría / talla / color ────────────────────────────
    def _dist(group_field):
        rows = _fetchall(conn, f"""
            SELECT {group_field}, COALESCE(SUM(si.quantity), 0) AS total
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN products p ON p.id = si.product_id
            WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
            AND {group_field} IS NOT NULL
            {cat_filter}
            GROUP BY {group_field}
            ORDER BY total DESC
            LIMIT 10
        """, _params())
        return {
            'labels': [str(list(r.values())[0]) for r in rows],
            'values': [int(list(r.values())[1]) for r in rows],
        }

    categorias_data = _dist('p.category')
    tallas_data     = _dist('si.product_size')
    colores_data    = _dist('si.product_color')

    # ── Heatmap hora × día de semana ───────────────────────────────────────────
    heatmap_rows = _fetchall(conn, f"""
        SELECT
            EXTRACT(HOUR FROM {SALE_DATE})::int       AS hora,
            EXTRACT(ISODOW FROM {SALE_DATE})::int - 1 AS dow,
            COUNT(DISTINCT s.id)                      AS ventas
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY hora, dow
    """, _params())

    heatmap_data = [[0] * 7 for _ in range(16)]
    for r in heatmap_rows:
        hi = int(r['hora']) - 6
        di = int(r['dow'])
        if 0 <= hi < 16 and 0 <= di < 7:
            heatmap_data[hi][di] = int(r['ventas'])

    # ── Stock crítico ──────────────────────────────────────────────────────────
    limit = int(current_app.config.get("LOW_STOCK_LIMIT", 4))
    stock_critico = _fetchall(conn, """
        SELECT name, model, category, size, color, location, stock
        FROM products
        WHERE stock <= %(lim)s
        ORDER BY stock ASC, name ASC
        LIMIT 30
    """, {'lim': limit})

    sc_labels = [p['name'][:15] for p in stock_critico[:10]]
    sc_values = [p['stock'] for p in stock_critico[:10]]
    stock_critico_chart = {'labels': sc_labels, 'values': sc_values}

    # ── Rotación por categoría ─────────────────────────────────────────────────
    semanas = max(num_dias // 7, 1)
    rot_rows = _fetchall(conn, f"""
        SELECT
            p.category,
            COALESCE(SUM(si.quantity), 0)::numeric / GREATEST(%(semanas)s, 1) AS rotacion
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY p.category
        ORDER BY rotacion DESC
    """, _params({'semanas': semanas}))

    rotacion_data = {
        'labels': [r['category'] or 'Sin categoría' for r in rot_rows],
        'values': [round(float(r['rotacion']), 1) for r in rot_rows],
    }

    # ── Rentabilidad por categoría ─────────────────────────────────────────────
    rent_rows = _fetchall(conn, f"""
        SELECT
            p.category,
            COALESCE(SUM(si.subtotal), 0)  AS ingresos,
            COALESCE(SUM(si.quantity), 0)  AS unidades
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        GROUP BY p.category
        ORDER BY ingresos DESC
    """, _params())

    rentabilidad_data = {
        'labels':   [r['category'] or 'Sin categoría' for r in rent_rows],
        'ingresos': [float(r['ingresos']) for r in rent_rows],
        'unidades': [int(r['unidades']) for r in rent_rows],
    }

    # ── Detalle de ventas ──────────────────────────────────────────────────────
    ventas_detalle_raw = _fetchall(conn, f"""
        SELECT
            {SALE_DATE}                                        AS fecha,
            si.product_name,
            p.category,
            si.product_size                                    AS size,
            si.product_color                                   AS color,
            si.quantity,
            si.sale_price                                      AS unit_price,
            si.subtotal                                        AS total,
            COALESCE(u.username, s.seller_user_id::text, '—') AS seller
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN products p ON p.id = si.product_id
        LEFT JOIN users u ON u.id = s.seller_user_id
        WHERE ({SALE_DATE})::date BETWEEN %(d1)s AND %(d2)s {PAID_ONLY}
        {cat_filter}
        ORDER BY {SALE_DATE} DESC
        LIMIT 200
    """, _params())

    # FIX: los Row de psycopg2 no son JSON-serializables.
    # Se convierten a dict aquí para que jsonify en api_reportes() no explote.
    # Los campos datetime se convierten a string ISO por el mismo motivo.
    ventas_detalle = []
    for r in ventas_detalle_raw:
        row = dict(r)
        if row.get('fecha') and hasattr(row['fecha'], 'isoformat'):
            row['fecha'] = row['fecha'].isoformat()
        ventas_detalle.append(row)

    productos_agotados = _scalar(conn, """
        SELECT COUNT(*) FROM products WHERE stock <= 0
    """)

    productos_stock_bajo = _scalar(conn, """
        SELECT COUNT(*) FROM products WHERE stock > 0 AND stock <= %(lim)s
    """, {'lim': limit})

    total_productos = _scalar(conn, """
        SELECT COUNT(*) FROM products
    """)

    return dict(
        fecha_ini=fecha_ini,
        fecha_fin=fecha_fin,
        kpi=kpi,
        resumen=resumen,
        ventas_dia=ventas_dia,
        ventas_semana=ventas_semana,
        ventas_mes=ventas_mes,
        top_products=top_products,
        bottom_products=bottom_products,
        categorias_data=categorias_data,
        tallas_data=tallas_data,
        colores_data=colores_data,
        heatmap_data=heatmap_data,
        stock_critico=stock_critico,
        stock_critico_chart=stock_critico_chart,
        rotacion_data=rotacion_data,
        rentabilidad_data=rentabilidad_data,
        ventas_detalle=ventas_detalle,
        productos_agotados=productos_agotados,
        productos_stock_bajo=productos_stock_bajo,
        total_productos=total_productos,
    )


# ── ruta principal ──────────────────────────────────────────────────────────────

@reportes_bp.route('/')
@role_required('admin')
def dashboard():
    today      = date.today()
    fecha_ini  = request.args.get('fecha_inicio', (today - timedelta(days=29)).isoformat())
    fecha_fin  = request.args.get('fecha_fin',    today.isoformat())
    categoria  = request.args.get('categoria',    '')
    agrupacion = request.args.get('agrupacion',   'dia')

    filters = {
        'fecha_inicio': fecha_ini,
        'fecha_fin':    fecha_fin,
        'categoria':    categoria,
        'agrupacion':   agrupacion,
    }

    conn = get_db()
    try:
        data = _build_report_data(conn, fecha_ini, fecha_fin, categoria, agrupacion)
    finally:
        conn.close()

    return render_template('reportes.html', filters=filters, **data)


# ── API JSON ────────────────────────────────────────────────────────────────────

@reportes_bp.route('/api/reportes')
@role_required('admin')
def api_reportes():
    today      = date.today()
    fecha_ini  = request.args.get('fecha_inicio', (today - timedelta(days=29)).isoformat())
    fecha_fin  = request.args.get('fecha_fin',    today.isoformat())
    categoria  = request.args.get('categoria',    '')
    agrupacion = request.args.get('agrupacion',   'dia')

    conn = get_db()
    try:
        data = _build_report_data(conn, fecha_ini, fecha_fin, categoria, agrupacion)
    finally:
        conn.close()

    return jsonify({
        'ventas_dia':          data['ventas_dia'],
        'ventas_semana':       data['ventas_semana'],
        'ventas_mes':          data['ventas_mes'],
        'categorias':          data['categorias_data'],
        'tallas':              data['tallas_data'],
        'colores':             data['colores_data'],
        'heatmap':             data['heatmap_data'],
        'stock_critico_chart': data['stock_critico_chart'],
        'rotacion':            data['rotacion_data'],
        'rentabilidad':        data['rentabilidad_data'],
    })