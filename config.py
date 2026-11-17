import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


class Config:
    # 🔐 Clave secreta (en producción SIEMPRE debe venir del .env)
    SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_key_cambiar")

    # 🗄️ Base de datos (la conexión real está en app/db/db.py)
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_NAME = os.getenv("DB_NAME", "stockflow")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASS = os.getenv("DB_PASS", "")
    DB_PORT = os.getenv("DB_PORT", "5432")

    # 📦 Inventario: un producto/variante está en "stock bajo" si 0 < stock <= LOW_STOCK_LIMIT
    LOW_STOCK_LIMIT = _env_int("LOW_STOCK_LIMIT", 4)

    # 🍪 Sesión
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_env_int("SESSION_HOURS", 10))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # 🛠️ Crear/actualizar tablas automáticamente al arrancar
    AUTO_MIGRATE = _env_bool("AUTO_MIGRATE", True)

    # 🔑 Recuperación de contraseña: validez del enlace (minutos)
    RESET_TOKEN_MINUTES = _env_int("RESET_TOKEN_MINUTES", 30)
