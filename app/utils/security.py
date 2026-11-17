"""Helpers de autenticación y roles.

Roles:
    superadmin -> dueño del sistema: crea y administra tiendas. Puede "entrar"
                  a cualquier tienda y ahí actúa como admin.
    admin      -> administrador de UNA tienda (ventas, bodega, reportes, usuarios)
    cajero     -> ventas de su tienda
    bodega     -> inventario de su tienda
"""
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

ROLES = ("admin", "cajero", "bodega")          # roles que puede asignar el admin de una tienda
ALL_ROLES = ("superadmin",) + ROLES

ROLE_HOME = {
    "admin": "reportes.dashboard",
    "cajero": "cajero.dashboard",
    "bodega": "bodega.dashboard",
}


def is_superadmin():
    return session.get("role") == "superadmin"


def effective_role():
    """Rol con el que se trabaja dentro de una tienda (el súper admin cuenta como admin)."""
    role = session.get("role")
    if role == "superadmin":
        return "admin" if session.get("business_id") else "superadmin"
    return role


def current_role():
    return effective_role()


def has_role(*roles):
    if not session.get("user_id"):
        return False
    role = effective_role()
    if role == "superadmin":
        return "superadmin" in roles
    if not session.get("business_id"):
        return False
    return role == "admin" or role in roles


def home_endpoint_for(role=None):
    role = effective_role() if role is None or role == "superadmin" else role
    if role == "superadmin":
        return "superadmin.stores"
    return ROLE_HOME.get(role, "auth.login")


def _wants_json():
    return (
        request.path.startswith(("/cajero/api", "/bodega/api", "/reportes/api"))
        or request.is_json
        or request.accept_mimetypes.best == "application/json"
    )


def role_required(*roles):
    """Exige sesión iniciada, una tienda activa y uno de los roles (admin siempre pasa)."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                if _wants_json():
                    return jsonify({"ok": False, "message": "Sesión expirada. Vuelve a ingresar."}), 401
                flash("Debes iniciar sesión.", "warning")
                return redirect(url_for("auth.login", next=request.path))

            if not has_role(*roles):
                if _wants_json():
                    return jsonify({"ok": False, "message": "No autorizado"}), 403
                if effective_role() == "superadmin":
                    flash("Primero entra a una tienda.", "warning")
                else:
                    flash("No tienes permisos para acceder a esa sección.", "error")
                return redirect(url_for(home_endpoint_for()))

            return view(*args, **kwargs)

        return wrapper

    return decorator


login_required = role_required(*ALL_ROLES)


def superadmin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        if not is_superadmin():
            flash("Solo el súper administrador puede gestionar tiendas.", "error")
            return redirect(url_for(home_endpoint_for()))
        return view(*args, **kwargs)

    return wrapper
