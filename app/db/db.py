"""Conexión a PostgreSQL con aislamiento por tienda (multi-tienda).

Cada tienda es una fila de `businesses`. Las tablas de datos (products, sales, …)
tienen `business_id` y una política de Row Level Security (RLS): PostgreSQL solo
deja ver/crear filas de la tienda indicada en `app.business_id`.

get_db()               -> conexión de la tienda de la sesión actual (lo normal)
get_db(business_id=5)  -> conexión de una tienda concreta (webhooks, tareas)
get_db(bypass=True)    -> conexión SIN aislamiento (solo migraciones, login y
                          panel del súper administrador)
"""
import logging
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

APP_ROLE = "stockflow_app"
_AUTO = object()


def _connect():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            port=os.getenv("DB_PORT", "5432"),
            cursor_factory=psycopg2.extras.RealDictCursor,
            options="-c client_encoding=UTF8",
        )
    except Exception as e:
        logger.error("❌ Error conectando a la base de datos: %s", e)
        raise


def _session_business_id():
    try:
        from flask import has_request_context, session
        if has_request_context():
            return session.get("business_id")
    except Exception:
        pass
    return None


def get_db(business_id=_AUTO, bypass=False):
    """Retorna una conexión con cursor tipo diccionario (RealDictCursor)."""
    conn = _connect()
    if bypass:
        return conn

    if business_id is _AUTO:
        business_id = _session_business_id()

    try:
        with conn.cursor() as cur:
            # Rol sin privilegios de superusuario: así PostgreSQL SÍ aplica el
            # aislamiento por tienda aunque el .env use el usuario 'postgres'.
            cur.execute(f"SET ROLE {APP_ROLE}")
            cur.execute("SELECT set_config('app.business_id', %s, false)",
                        (str(int(business_id)) if business_id else "",))
        conn.commit()
    except Exception:
        conn.close()
        logger.critical("No se pudo activar el aislamiento por tienda (¿se ejecutó scripts/schema.sql?).")
        raise
    return conn
