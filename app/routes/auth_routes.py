import logging

from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, session, url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from app.db.db import get_db
from app.services.email_service import EmailService
from app.utils.security import home_endpoint_for, login_required

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

HASH_PREFIXES = ("scrypt:", "pbkdf2:", "argon2")


def _is_hashed(value):
    return isinstance(value, str) and value.startswith(HASH_PREFIXES)


def verify_password(stored, given):
    """Acepta contraseñas con hash (recomendado) y, por compatibilidad,
    contraseñas antiguas guardadas en texto plano."""
    if not stored or given is None:
        return False
    if _is_hashed(stored):
        try:
            return check_password_hash(stored, given)
        except ValueError:
            return False
    return stored == given


def _safe_next(target):
    # solo rutas internas, para evitar redirecciones a otros sitios
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


# =========================================================
# Página principal
# =========================================================

@auth_bp.route("/")
def index():
    return render_template("index.html")


# =========================================================
# Login
# =========================================================

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and session.get("user_id"):
        return redirect(url_for(home_endpoint_for()))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Escribe usuario y contraseña.", "error")
            return render_template("login.html"), 400

        conn = get_db(bypass=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT u.*, b.name AS business_name, b.is_active AS business_active
                    FROM users u LEFT JOIN businesses b ON b.id = u.business_id
                    WHERE LOWER(u.username) = LOWER(%s) LIMIT 1
                """, (username,))
                user = cur.fetchone()

                if user and verify_password(user["password"], password):
                    if user.get("active") is False:
                        flash("Tu usuario está desactivado. Contacta al administrador.", "error")
                        return render_template("login.html"), 403
                    if user["role"] != "superadmin":
                        if not user.get("business_id"):
                            flash("Tu usuario no tiene una tienda asignada. Contacta al administrador.", "error")
                            return render_template("login.html"), 403
                        if user.get("business_active") is False:
                            flash("Esta tienda está desactivada. Contacta al administrador del sistema.", "error")
                            return render_template("login.html"), 403

                    # Migración silenciosa: si la clave estaba en texto plano, se guarda con hash
                    if not _is_hashed(user["password"]):
                        cur.execute(
                            "UPDATE users SET password = %s WHERE id = %s",
                            (generate_password_hash(password), user["id"]),
                        )
                        conn.commit()

                    session.clear()
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["username"] = user["username"]
                    session["role"] = user["role"]
                    if user["role"] != "superadmin":
                        session["business_id"] = user["business_id"]
                        session["business_name"] = user["business_name"]

                    next_url = _safe_next(request.args.get("next"))
                    if next_url:
                        return redirect(next_url)
                    # Antes el admin iba a cajero.reportes, que renderizaba reportes.html
                    # sin las variables que necesita y fallaba.
                    return redirect(url_for(home_endpoint_for()))
        finally:
            conn.close()

        flash("Usuario o contraseña incorrectos", "error")
        return render_template("login.html"), 401

    return render_template("login.html")


# =========================================================
# Cerrar sesión
# =========================================================

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("auth.login"))


# =========================================================
# Cambiar mi contraseña (usuario logueado)
# =========================================================

@auth_bp.route("/mi-cuenta/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if len(new) < 6:
            flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        elif new != confirm:
            flash("Las contraseñas no coinciden.", "error")
        else:
            conn = get_db(bypass=True)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, password FROM users WHERE id = %s", (session["user_id"],))
                    user = cur.fetchone()
                    if not user or not verify_password(user["password"], current):
                        flash("La contraseña actual no es correcta.", "error")
                    else:
                        cur.execute(
                            "UPDATE users SET password = %s WHERE id = %s",
                            (generate_password_hash(new), user["id"]),
                        )
                        conn.commit()
                        flash("Contraseña actualizada.", "success")
                        return redirect(url_for(home_endpoint_for()))
            finally:
                conn.close()

    return render_template("change_password.html")


# =========================================================
# Recuperar contraseña por correo
# =========================================================

def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _make_reset_token(user):
    # Se incluye un trozo del hash actual: al cambiar la clave, el enlace deja de servir
    return _serializer().dumps({"uid": user["id"], "p": (user["password"] or "")[-12:]})


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        if identifier:
            conn = get_db(bypass=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, username, email, password FROM users
                        WHERE (LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s))
                          AND COALESCE(active, TRUE)
                        LIMIT 1
                        """,
                        (identifier, identifier),
                    )
                    user = cur.fetchone()
            finally:
                conn.close()

            if user and user.get("email"):
                link = url_for("auth.reset_password", token=_make_reset_token(user), _external=True)
                minutes = current_app.config["RESET_TOKEN_MINUTES"]
                body = (
                    f"Hola {user['username']},\n\n"
                    f"Para crear una nueva contraseña en StockFlow abre este enlace "
                    f"(vence en {minutes} minutos):\n\n{link}\n\n"
                    "Si no lo solicitaste, ignora este mensaje.\n"
                )
                if not EmailService.send_email(user["email"], "Recuperar contraseña - StockFlow", body):
                    logger.error("No se pudo enviar el correo de recuperación a %s", user["username"])
            elif user:
                logger.warning("El usuario %s pidió recuperar contraseña pero no tiene email.", user["username"])

        # Mismo mensaje siempre, para no revelar qué usuarios existen
        flash("Si el usuario existe y tiene correo registrado, enviamos un enlace para restablecer la contraseña. "
              "Si no tienes correo, pide al administrador que la cambie.", "success")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    max_age = current_app.config["RESET_TOKEN_MINUTES"] * 60
    try:
        data = _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        flash("El enlace venció. Solicita uno nuevo.", "error")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("El enlace no es válido.", "error")
        return redirect(url_for("auth.forgot_password"))

    conn = get_db(bypass=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, password FROM users WHERE id = %s", (data.get("uid"),))
            user = cur.fetchone()
            if not user or (user["password"] or "")[-12:] != data.get("p"):
                flash("El enlace ya fue usado o no es válido.", "error")
                return redirect(url_for("auth.forgot_password"))

            if request.method == "POST":
                new = request.form.get("new_password") or ""
                confirm = request.form.get("confirm_password") or ""
                if len(new) < 6:
                    flash("La contraseña debe tener al menos 6 caracteres.", "error")
                elif new != confirm:
                    flash("Las contraseñas no coinciden.", "error")
                else:
                    cur.execute(
                        "UPDATE users SET password = %s WHERE id = %s",
                        (generate_password_hash(new), user["id"]),
                    )
                    conn.commit()
                    flash("Contraseña actualizada. Ya puedes ingresar.", "success")
                    return redirect(url_for("auth.login"))
    finally:
        conn.close()

    return render_template("reset_password.html", username=user["username"])
