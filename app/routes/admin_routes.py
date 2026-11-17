import logging
import os
import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from app.db.db import get_db
from app.services.email_service import send_test_email
from app.services.inventory_alert_service import InventoryAlertService
from app.utils.security import ROLES, role_required

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ROLE_LABELS = {"admin": "Administrador", "cajero": "Cajero", "bodega": "Bodega"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")


def _back():
    return redirect(url_for("admin.users"))


@admin_bp.route("/usuarios")
@role_required("admin")
def users():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, role, email, full_name, COALESCE(active, TRUE) AS active, created_at
                FROM users WHERE business_id = %s
                ORDER BY active DESC, role, username
            """, (session["business_id"],))
            rows = cur.fetchall()
    finally:
        conn.close()
    alert_email = (InventoryAlertService.admin_email_for_business(session["business_id"])
                   or os.getenv("ADMIN_EMAIL") or "")
    return render_template("admin_usuarios.html", users=rows, roles=ROLES, role_labels=ROLE_LABELS,
                           alert_email=alert_email, low_stock_limit=InventoryAlertService.LOW_STOCK_LIMIT)


@admin_bp.route("/correo-prueba", methods=["POST"])
@role_required("admin")
def test_email():
    to = (InventoryAlertService.admin_email_for_business(session["business_id"])
          or os.getenv("ADMIN_EMAIL") or "").strip()
    ok, message = send_test_email(to)
    flash(message, "success" if ok else "error")
    return _back()


@admin_bp.route("/usuarios/crear", methods=["POST"])
@role_required("admin")
def create_user():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or "").strip()
    email = (request.form.get("email") or "").strip() or None
    full_name = (request.form.get("full_name") or "").strip() or None

    if not USERNAME_RE.match(username):
        flash("El usuario debe tener de 3 a 40 caracteres (letras, números, punto, guion o guion bajo).", "error")
        return _back()
    if len(password) < 6:
        flash("La contraseña debe tener al menos 6 caracteres.", "error")
        return _back()
    if role not in ROLES:
        flash("Rol inválido.", "error")
        return _back()
    if email and not EMAIL_RE.match(email):
        flash("El correo no es válido.", "error")
        return _back()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE LOWER(username) = LOWER(%s)", (username,))
            if cur.fetchone():
                flash(f"Ya existe el usuario «{username}».", "error")
                return _back()
            cur.execute("""
                INSERT INTO users (username, password, role, email, full_name, active, business_id)
                VALUES (%s, %s, %s, %s, %s, TRUE, %s)
            """, (username, generate_password_hash(password), role, email, full_name, session["business_id"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Error creando usuario: %s", e, exc_info=True)
        flash(f"Error creando usuario: {e}", "error")
        return _back()
    finally:
        conn.close()

    flash(f"Usuario «{username}» creado.", "success")
    return _back()


@admin_bp.route("/usuarios/<int:user_id>/editar", methods=["POST"])
@role_required("admin")
def edit_user(user_id):
    role = (request.form.get("role") or "").strip()
    email = (request.form.get("email") or "").strip() or None
    full_name = (request.form.get("full_name") or "").strip() or None
    active = request.form.get("active") == "on"

    if role not in ROLES:
        flash("Rol inválido.", "error")
        return _back()
    if email and not EMAIL_RE.match(email):
        flash("El correo no es válido.", "error")
        return _back()
    if user_id == session.get("user_id") and (role != "admin" or not active):
        flash("No puedes quitarte el rol de administrador ni desactivar tu propio usuario.", "error")
        return _back()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET role = %s, email = %s, full_name = %s, active = %s
                WHERE id = %s AND business_id = %s AND role <> 'superadmin'
            """, (role, email, full_name, active, user_id, session["business_id"]))
            updated = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Error actualizando usuario: {e}", "error")
        return _back()
    finally:
        conn.close()

    flash("Usuario actualizado." if updated else "Usuario no encontrado en esta tienda.",
          "success" if updated else "error")
    return _back()


@admin_bp.route("/usuarios/<int:user_id>/password", methods=["POST"])
@role_required("admin")
def reset_user_password(user_id):
    password = request.form.get("password") or ""
    if len(password) < 6:
        flash("La contraseña debe tener al menos 6 caracteres.", "error")
        return _back()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET password = %s
                WHERE id = %s AND business_id = %s AND role <> 'superadmin'
                RETURNING username
            """, (generate_password_hash(password), user_id, session["business_id"]))
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if row:
        flash(f"Contraseña de «{row['username']}» actualizada.", "success")
    else:
        flash("Usuario no encontrado en esta tienda.", "error")
    return _back()
