"""P04 · Inventario por talla y color (pruebas funcionales de integridad de datos)."""
from conftest import create_product, login, product_by_name, query, variants_of


def _mover(client, pid, tipo, qty, size="40", color="Negro"):
    return client.post(f"/bodega/product/{pid}/move",
                       data={"movement_type": tipo, "size": size, "color": color, "quantity": str(qty)},
                       follow_redirects=True)


def test_p04_01_crear_producto_con_stock_inicial_crea_variante_y_movimiento(client, stores):
    login(client, "bodega_a")
    create_product(client, "Bota Inventario", stock="8", size="41", color="cafe")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    assert p["stock"] == 8 and p["barcode"].startswith("PROD-")
    vs = variants_of(p["id"], bid)
    assert len(vs) == 1 and vs[0]["size"] == "41" and vs[0]["color"] == "Cafe" and vs[0]["stock"] == 8
    mov = query("SELECT movement_type, quantity FROM inventory_movements WHERE product_id=%s", (p["id"],))
    assert mov == [{"movement_type": "entrada", "quantity": 8}]


def test_p04_02_entrada_nueva_talla_y_stock_total_es_la_suma(client, stores):
    login(client, "bodega_a")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    _mover(client, p["id"], "entrada", 5, size="42", color="Cafe")
    assert product_by_name("Bota Inventario", bid)["stock"] == 13
    assert sum(v["stock"] for v in variants_of(p["id"], bid)) == 13


def test_p04_03_salida_mayor_al_disponible_es_rechazada(client, stores):
    login(client, "bodega_a")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    r = _mover(client, p["id"], "salida", 99, size="42", color="Cafe")
    assert "No puedes sacar 99" in r.get_data(as_text=True)
    assert product_by_name("Bota Inventario", bid)["stock"] == 13


def test_p04_04_salida_valida_descuenta(client, stores):
    login(client, "bodega_a")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    _mover(client, p["id"], "salida", 2, size="42", color="cafe")
    assert product_by_name("Bota Inventario", bid)["stock"] == 11


def test_p04_05_cantidades_invalidas_rechazadas(client, stores):
    login(client, "bodega_a")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    for qty in ("0", "-5", "abc", "1.5"):
        r = _mover(client, p["id"], "entrada", qty)
        assert "mayor a 0" in r.get_data(as_text=True)
    assert product_by_name("Bota Inventario", bid)["stock"] == 11


def test_p04_06_tipo_de_movimiento_invalido(client, stores):
    login(client, "bodega_a")
    p = product_by_name("Bota Inventario", stores["a"]["business_id"])
    r = _mover(client, p["id"], "ajuste_libre", 3)
    assert "Tipo de movimiento inválido" in r.get_data(as_text=True)


def test_p04_07_precio_y_stock_negativos_rechazados(client, stores):
    login(client, "bodega_a")
    r = create_product(client, "Producto Negativo", price="-10")
    assert product_by_name("Producto Negativo", stores["a"]["business_id"]) is None
    create_product(client, "Producto Negativo 2", stock="-3")
    assert product_by_name("Producto Negativo 2", stores["a"]["business_id"]) is None
    assert r.status_code == 302


def test_p04_08_codigo_duplicado_en_la_misma_tienda(client, stores):
    login(client, "bodega_a")
    create_product(client, "Dup 1", barcode="DUP-777")
    r = create_product(client, "Dup 2", barcode="DUP-777")
    assert r.status_code == 302
    assert product_by_name("Dup 2", stores["a"]["business_id"]) is None


def test_p04_09_formatos_de_precio_colombianos(client, stores):
    login(client, "bodega_a")
    create_product(client, "Precio Miles", price="120.000")
    assert float(product_by_name("Precio Miles", stores["a"]["business_id"])["price"]) == 120000.0


def test_p04_10_busqueda_por_codigo_qr_de_variante(client, stores):
    login(client, "bodega_a")
    bid = stores["a"]["business_id"]
    p = product_by_name("Bota Inventario", bid)
    v = variants_of(p["id"], bid)[0]
    r = client.get(f"/bodega/api/product-by-barcode?barcode={p['barcode']}%23V{v['id']}")
    data = r.get_json()
    assert r.status_code == 200 and data["product"]["selected_variant_id"] == v["id"]
