"""P03 · Aislamiento multi-tienda (Row Level Security) (Dominio D03/D04 · OWASP A01, IDOR)."""
import pytest

from conftest import create_product, login, product_by_name, query


@pytest.fixture(scope="module")
def producto_a(app, stores):
    c = app.test_client()
    login(c, "bodega_a")
    create_product(c, "Tenis Aislamiento A", barcode="ISO-A-001")
    return product_by_name("Tenis Aislamiento A", stores["a"]["business_id"])


def test_p03_01_tienda_b_no_ve_productos_de_a_en_bd(stores, producto_a):
    rows = query("SELECT id FROM products WHERE id = %s", (producto_a["id"],),
                 business_id=stores["b"]["business_id"], bypass=False)
    assert rows == []


def test_p03_02_bodega_b_no_encuentra_producto_de_a_por_codigo(client, stores, producto_a):
    login(client, "bodega_b")
    r = client.get("/bodega/api/product-by-barcode?barcode=ISO-A-001")
    assert r.status_code == 404


def test_p03_03_bodega_b_no_puede_mover_stock_de_a(client, stores, producto_a):
    login(client, "bodega_b")
    client.post(f"/bodega/product/{producto_a['id']}/move",
                data={"movement_type": "salida", "size": "40", "color": "Negro", "quantity": "5"})
    stock = query("SELECT stock FROM products WHERE id=%s", (producto_a["id"],))[0]["stock"]
    assert stock == 10


def test_p03_04_bodega_b_no_puede_editar_producto_de_a(client, stores, producto_a):
    login(client, "bodega_b")
    client.post(f"/bodega/product/{producto_a['id']}/edit", data={"name": "HACKEADO", "price": "1"})
    row = query("SELECT name, price FROM products WHERE id=%s", (producto_a["id"],))[0]
    assert row["name"] == "Tenis Aislamiento A"


def test_p03_05_cajero_b_no_puede_vender_producto_de_a(client, stores, producto_a):
    import json
    login(client, "cajero_b")
    v = query("SELECT id FROM product_variants WHERE product_id=%s", (producto_a["id"],))[0]
    client.post("/cajero", data={"payment_method": "efectivo", "sale_items_json": json.dumps(
        [{"product_id": producto_a["id"], "variant_id": v["id"], "quantity": 1, "sale_price": 1}])})
    assert query("SELECT stock FROM products WHERE id=%s", (producto_a["id"],))[0]["stock"] == 10


def test_p03_06_admin_b_no_puede_cambiar_clave_de_usuario_de_a(client, stores):
    login(client, "admin_b")
    uid = query("SELECT id FROM users WHERE username='cajero_a'")[0]["id"]
    antes = query("SELECT password FROM users WHERE id=%s", (uid,))[0]["password"]
    client.post(f"/admin/usuarios/{uid}/password", data={"password": "Robada-123"})
    assert query("SELECT password FROM users WHERE id=%s", (uid,))[0]["password"] == antes


def test_p03_07_rls_impide_insertar_en_otra_tienda(stores):
    from app.db.db import get_db
    conn = get_db(business_id=stores["b"]["business_id"])
    try:
        with conn.cursor() as cur:
            with pytest.raises(Exception):
                cur.execute("INSERT INTO products (name, price, stock, business_id) VALUES ('X', 1, 0, %s)",
                            (stores["a"]["business_id"],))
    finally:
        conn.rollback()
        conn.close()


def test_p03_08_conexion_sin_tienda_no_ve_datos(stores, producto_a):
    from app.db.db import get_db
    conn = get_db(business_id=None)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM products")
            assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


def test_p03_09_mismo_codigo_permitido_en_tiendas_distintas(client, stores, producto_a):
    login(client, "bodega_b")
    create_product(client, "Tenis Codigo Repetido B", barcode="ISO-A-001")
    assert product_by_name("Tenis Codigo Repetido B", stores["b"]["business_id"]) is not None
