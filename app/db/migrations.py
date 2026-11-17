"""Aplica scripts/schema.sql al arrancar y crea el súper administrador si no existe."""
import logging
import os
import re
import secrets

from werkzeug.security import generate_password_hash

from app.db.db import get_db

logger = logging.getLogger(__name__)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "schema.sql")


def split_statements(sql_text):
    """Parte el script por ';' respetando los bloques $$ ... $$ (DO / funciones)."""
    sql_text = re.sub(r"--[^\n]*", "", sql_text)
    statements, buf, in_dollar = [], [], False
    i = 0
    while i < len(sql_text):
        if sql_text.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql_text[i]
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_migrations():
    try:
        with open(os.path.abspath(SCHEMA_PATH), encoding="utf-8") as fh:
            statements = split_statements(fh.read())
    except OSError as e:
        logger.error("No se encontró scripts/schema.sql: %s", e)
        return False

    try:
        conn = get_db(bypass=True)
    except Exception:
        logger.error("Migraciones omitidas: no hay conexión a la base de datos.")
        return False

    conn.autocommit = True
    failed = 0
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                try:
                    cur.execute(stmt)
                except Exception as e:  # una sentencia mala no debe tumbar las demás
                    failed += 1
                    logger.warning("Migración: no se pudo ejecutar «%s…»: %s", stmt[:70].replace("\n", " "), e)
            _seed_superadmin(cur)
    finally:
        conn.close()

    if failed:
        logger.warning("Migraciones aplicadas con %s advertencia(s).", failed)
    else:
        logger.info("Esquema de base de datos verificado (multi-tienda).")
    return failed == 0


def _seed_superadmin(cur):
    cur.execute("SELECT 1 FROM users WHERE role = 'superadmin' LIMIT 1")
    if cur.fetchone():
        return
    password = os.getenv("SUPERADMIN_PASSWORD") or secrets.token_urlsafe(9)
    cur.execute(
        """
        INSERT INTO users (username, password, role, email, full_name, business_id)
        VALUES ('superadmin', %s, 'superadmin', %s, 'Súper administrador', NULL)
        ON CONFLICT (username) DO NOTHING
        """,
        (generate_password_hash(password), os.getenv("ADMIN_EMAIL") or None),
    )
    if not os.getenv("SUPERADMIN_PASSWORD"):
        logger.warning("Se creó el usuario 'superadmin' con la contraseña temporal: %s  (cámbiala al entrar)", password)
