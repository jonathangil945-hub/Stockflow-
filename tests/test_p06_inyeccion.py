"""P06 · Inyección SQL y XSS (OWASP A03)."""
import pytest

from conftest import create_product, login, product_by_name, query

SQLI = ["' OR '1'='1", "admin'--", "'; DROP TABLE users;--", "1' UNION SELECT username, password FROM users--"]


@pytest.mark.parametrize("payload", SQLI)
def test_p06_01_sqli_en_login_no_autentica(client, stores, payload):
    r = login(client, payload, payload)
    assert r.status_code == 401
    assert query("SELECT count(*) AS n FROM users")[0]["n"] > 0


@pytest.mark.parametrize("payload", SQLI)
def test_p06_02_sqli_en_busqueda_de_bodega(client, stores, payload):
    login(client, "bodega_a")
    r = client.get("/bodega", query_string={"search": payload, "category": payload, "size": payload})
    assert r.status_code == 200
    assert "scrypt:" not in r.get_data(as_text=True)


@pytest.mark.parametrize("payload", SQLI)
def test_p06_03_sqli_en_api_por_codigo(client, stores, payload):
    login(client, "cajero_a")
    r = client.get("/cajero/api/product-by-barcode", query_string={"barcode": payload})
    assert r.status_code == 404 and r.is_json


def test_p06_04_sqli_en_filtros_de_reportes(client, stores):
    login(client, "admin_a")
    r = client.get("/reportes/api/reportes", query_string={
        "fecha_inicio": "2026-01-01' OR 1=1--", "categoria": "x' OR '1'='1", "agrupacion": "dia;DROP"})
    assert r.status_code in (200, 400)


XSS = '<script>alert("xss")</script>'


def test_p06_05_xss_almacenado_en_nombre_de_producto_se_escapa(client, stores):
    login(client, "bodega_a")
    create_product(client, XSS, barcode="XSS-001")
    assert product_by_name(XSS, stores["a"]["business_id"]) is not None
    html = client.get("/bodega", query_string={"search": "XSS-001"}).get_data(as_text=True)
    assert XSS not in html
    assert "&lt;script&gt;" in html


def test_p06_06_xss_reflejado_en_busqueda(client, stores):
    login(client, "bodega_a")
    html = client.get("/bodega", query_string={"search": XSS}).get_data(as_text=True)
    assert XSS not in html


def test_p06_07_xss_en_nombre_de_tienda_panel_superadmin(app, stores):
    from conftest import SUPERADMIN
    c = app.test_client()
    login(c, *SUPERADMIN)
    c.post("/superadmin/tiendas/crear", data={"name": XSS, "admin_username": "admin_xss",
                                             "admin_password": "Clave-123456"})
    html = c.get("/superadmin/tiendas").get_data(as_text=True)
    assert XSS not in html
