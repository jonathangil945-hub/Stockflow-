"""Panel del súper administrador: crear y gestionar tiendas (negocios)."""
import logging
import re
import unicodedata

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from app.db.db import get_db
from app.utils.security import superadmin_required

logger = logging.getLogger(__name__)

superadmin_bp = Blueprint("superadmin", __name__, url_prefix="/superadmin")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80] or "tienda"


def _back():
    return redirect(url_for("superadmin.stores"))


@superadmin_bp.route("/tiendas")
@superadmin_required
def stores():
    # conexión sin aislamiento: el súper admin ve el resumen de todas las tiendas
    conn = get_db(bypass=True)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.id, b.name, b.slug, b.nit, b.phone, b.address, b.email, b.is_active, b.created_at,
                       (SELECT COUNT(*) FROM users u WHERE u.business_id = b.id) AS users,
                       (SELECT string_agg(u.username, ', ' ORDER BY u.username) FROM users u
                         WHERE u.business_id = b.id AND u.role = 'admin') AS admins,
                       (SELECT COUNT(*) FROM products p WHERE p.business_id = b.id) AS products,
                       (SELECT COALESCE(SUM(p.stock), 0) FROM products p WHERE p.business_id = b.id) AS units,
                       (SELECT COUNT(*) FROM sales s WHERE s.business_id = b.id
                          AND COALESCE(s.status, 'PAID') = 'PAID') AS sales,
                       (SELECT COALESCE(SUM(s.total), 0) FROM sales s WHERE s.business_id = b.id
                          AND COALESCE(s.status, 'PAID') = 'PAID'
                          AND COALESCE(s.paid_at, s.created_at) >= date_trunc('month', CURRENT_DATE)) AS month_total
                FROM businesses b
                ORDER BY b.is_active DESC, b.name
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return render_template("superadmin_tiendas.html", stores=rows)


@superadmin_bp.route("/tiendas/crear", methods=["POST"])
@superadmin_required
def create_store():
    f = request.form
    name = (f.get("name") or "").strip()
    admin_username = (f.get("admin_username") or "").strip()
    admin_password = f.get("admin_password") or ""
    admin_email = (f.get("admin_email") or "").strip() or None
    extra = {k: (f.get(k) or "").strip() or None for k in ("nit", "phone", "address", "email")}

    if len(name) < 2:
        flash("Escribe el nombre de la tienda.", "error")
        return _back()
    if not USERNAME_RE.match(admin_username):
        flash("El usuario administrador debe tener de 3 a 40 caracteres (letras, números, . _ -).", "error")
        return _back()
    if len(admin_password) < 6:
        flash("La contraseña del administrador debe tener al menos 6 caracteres.", "error")
        return _back()
    for value in (admin_email, extra["email"]):
        if value and not EMAIL_RE.match(value):
            flash("Hay un correo inválido.", "error")
            return _back()

    conn = get_db(bypass=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(%s)", (admin_username,))
            if cur.fetchone():
                flash(f"El usuario «{admin_username}» ya existe. Elige otro.", "error")
                return _back()

            base_slug = slug = _slugify(name)
            n = 2
            while True:
                cur.execute("SELECT 1 FROM businesses WHERE slug = %s", (slug,))
                if not cur.fetchone():
                    break
                slug = f"{base_slug}-{n}"
                n += 1

            cur.execute("""
                INSERT INTO businesses (name, slug, nit, phone, address, email)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (name, slug, extra["nit"], extra["phone"], extra["address"], extra["email"]))
            business_id = cur.fetchone()["id"]

            cur.execute("""
                INSERT INTO users (username, password, role, email, full_name, business_id, active)
                VALUES (%s, %s, 'admin', %s, %s, %s, TRUE)
            """, (admin_username, generate_password_hash(admin_password), admin_email,
                  f"Administrador {name}", business_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Error creando tienda: %s", e, exc_info=True)
        flash(f"Error creando la tienda: {e}", "error")
        return _back()
    finally:
        conn.close()

    flash(f"Tienda «{name}» creada. Su administrador es «{admin_username}».", "success")
    return _back()


@superadmin_bp.route("/tiendas/<int:business_id>/editar", methods=["POST"])
@superadmin_required
def edit_store(business_id):
    f = request.form
    name = (f.get("name") or "").strip()
    if len(name) < 2:
        flash("El nombre de la tienda es obligatorio.", "error")
        return _back()
    email = (f.get("email") or "").strip() or None
    if email and not EMAIL_RE.match(email):
        flash("El correo de la tienda no es válido.", "error")
        return _back()

    conn = get_db(bypass=True)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE businesses SET name = %s, nit = %s, phone = %s, address = %s, email = %s, is_active = %s
                WHERE id = %s
            """, (name, (f.get("nit") or "").strip() or None, (f.get("phone") or "").strip() or None,
                  (f.get("address") or "").strip() or None, email, f.get("is_active") == "on", business_id))
        conn.commit()
    finally:
        conn.close()

    if session.get("business_id") == business_id:
        session["business_name"] = name
    flash("Tienda actualizada.", "success")
    return _back()


@superadmin_bp.route("/tiendas/<int:business_id>/entrar")
@superadmin_required
def enter_store(business_id):
    conn = get_db(bypass=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM businesses WHERE id = %s", (business_id,))
            store = cur.fetchone()
    finally:
        conn.close()
    if not store:
        flash("Tienda no encontrada.", "error")
        return _back()
    session["business_id"] = store["id"]
    session["business_name"] = store["name"]
    flash(f"Estás trabajando en «{store['name']}». Todo lo que veas y registres es de esta tienda.", "success")
    return redirect(url_for("reportes.dashboard"))


@superadmin_bp.route("/salir-tienda")
@superadmin_required
def leave_store():
    session.pop("business_id", None)
    session.pop("business_name", None)
    return _back()
