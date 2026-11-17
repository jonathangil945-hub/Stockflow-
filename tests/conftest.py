"""Configuración de pruebas de StockFlow (marco Stock Flow, fase 03 Identificación).

Las pruebas corren contra un PostgreSQL REAL (para comprobar también el
aislamiento por tienda con Row Level Security). Se usa una base de datos
desechable cuyo nombre DEBE terminar en "_test"; se borra y se crea en cada
ejecución, así que nunca apunta a la base de datos de trabajo.

Variables (opcionales):
    TEST_DB_HOST  (localhost)   TEST_DB_PORT (5432)
    TEST_DB_USER  (postgres)    TEST_DB_PASS ("")
    TEST_DB_NAME  (stockflow_test)

Uso:
    pip install -r requirements.txt -r requirements-dev.txt
    pytest
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

TEST_DB = {
    "DB_HOST": os.getenv("TEST_DB_HOST", "localhost"),
    "DB_PORT": os.getenv("TEST_DB_PORT", "5432"),
    "DB_USER": os.getenv("TEST_DB_USER", "postgres"),
    "DB_PASS": os.getenv("TEST_DB_PASS", ""),
    "DB_NAME": os.getenv("TEST_DB_NAME", "stockflow_test"),
}
if not TEST_DB["DB_NAME"].endswith("_test"):
    raise RuntimeError("TEST_DB_NAME debe terminar en '_test' (la base se borra en cada ejecución).")

# Variables ANTES de importar la app (config.py y los servicios leen el entorno al importar).
# setdefault no sirve: load_dotenv() no pisa variables ya definidas, así el .env real no se usa.
os.environ.update(TEST_DB)
os.environ.update({
    "SECRET_KEY": "clave-solo-para-pruebas-0123456789",
    "SUPERADMIN_PASSWORD": "Super-Admin-2026",
    "AUTO_MIGRATE": "true",
    "ADMIN_EMAIL": "",
    "SMTP_USER": "", "SMTP_PASSWORD": "",
    "WOMPI_PUBLIC_KEY": "pub_test_dummy",
    "WOMPI_PRIVATE_KEY": "prv_test_dummy",
    "WOMPI_INTEGRITY_SECRET": "test_integrity_dummy",
    "WOMPI_EVENTS_SECRET": "test_events_dummy",
    "WOMPI_SANDBOX": "true",
    "BASE_URL": "http://localhost:5000",
    "LOW_STOCK_LIMIT": "4",
})


def _recreate_database():
    import psycopg2
    conn = psycopg2.connect(host=TEST_DB["DB_HOST"], port=TEST_DB["DB_PORT"], user=TEST_DB["DB_USER"],
                            password=TEST_DB["DB_PASS"], database="postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB["DB_NAME"]}"')
        cur.execute(f'CREATE DATABASE "{TEST_DB["DB_NAME"]}" ENCODING \'UTF8\' TEMPLATE template0')
    conn.close()


_recreate_database()

import main  # noqa: E402  (crea la app y aplica scripts/schema.sql)

SUPERADMIN = ("superadmin", "Super-Admin-2026")
PASSWORD = "Clave-Prueba-123"


@pytest.fixture(scope="session")
def app():
    main.app.config.update(TESTING=True)
    return main.app


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """Sin correos reales ni llamadas reales a Wompi durante las pruebas."""
    sent = []
    from app.services import email_service, inventory_alert_service, wompi_service
    from app.routes import auth_routes, cajero_routes

    def fake_send(to_email, subject, body):
        sent.append({"to": to_email, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(email_service.EmailService, "send_email", staticmethod(fake_send))
    monkeypatch.setattr(auth_routes.EmailService, "send_email", staticmethod(fake_send))
    monkeypatch.setattr(inventory_alert_service.InventoryAlertService, "check_low_stock_async",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(wompi_service, "get_transaction", lambda *_a, **_k: None)
    monkeypatch.setattr(cajero_routes, "get_transaction", lambda *_a, **_k: None, raising=False)
    return sent


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, username, password=PASSWORD, follow=False, next_url=None):
    url = "/login" + (f"?next={next_url}" if next_url else "")
    return client.post(url, data={"username": username, "password": password}, follow_redirects=follow)


def db(business_id=None, bypass=False):
    from app.db.db import get_db
    return get_db(business_id=business_id, bypass=bypass) if not bypass else get_db(bypass=True)


def query(sql, params=None, business_id=None, bypass=True):
    conn = db(business_id=business_id, bypass=bypass)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture(scope="session")
def stores(app):
    """Dos tiendas (A y B) con admin, cajero y bodega cada una, creadas por la interfaz."""
    c = app.test_client()
    assert login(c, *SUPERADMIN).status_code == 302
    for key in ("a", "b"):
        r = c.post("/superadmin/tiendas/crear", data={
            "name": f"Tienda {key.upper()} Pruebas", "admin_username": f"admin_{key}",
            "admin_password": PASSWORD, "admin_email": f"admin_{key}@example.com",
        })
        assert r.status_code == 302
    c.get("/logout")

    info = {}
    for key in ("a", "b"):
        ca = app.test_client()
        assert login(ca, f"admin_{key}").status_code == 302
        for role in ("cajero", "bodega"):
            ca.post("/admin/usuarios/crear", data={"username": f"{role}_{key}", "password": PASSWORD,
                                                   "role": role, "email": f"{role}_{key}@example.com"})
        bid = query("SELECT business_id FROM users WHERE username = %s", (f"admin_{key}",))[0]["business_id"]
        info[key] = {"business_id": bid}
    return info


def create_product(client, name, price="100000", stock="10", size="40", color="Negro", barcode="", **extra):
    data = {"name": name, "price": price, "stock": stock, "size": size, "color": color,
            "barcode": barcode, "category": extra.get("category", "Calzado"), "location": "A1"}
    return client.post("/products/create", data=data)


def product_by_name(name, business_id):
    rows = query("SELECT * FROM products WHERE name = %s", (name,), business_id=business_id, bypass=False)
    return rows[0] if rows else None


def variants_of(product_id, business_id):
    return query("SELECT * FROM product_variants WHERE product_id = %s ORDER BY id", (product_id,),
                 business_id=business_id, bypass=False)
