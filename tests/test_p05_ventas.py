"""P05 · Ventas, recibos y lógica de negocio (OWASP A04 · manipulación de parámetros)."""
import json

import pytest

from conftest import create_product, login, product_by_name, query, variants_of


@pytest.fixture(scope="module")
def zapato(app, stores):
    c = app.test_client()
    login(c, "bodega_a")
    create_product(c, "Zapato Ventas", price="50000", stock="5", size="38", color="Blanco")
    bid = stores["a"]["business_id"]
    p = product_by_name("Zapato Ventas", bid)
    return {"p": p, "v": variants_of(p["id"], bid)[0], "bid": bid}


def _vender(client, items, metodo="efectivo"):
    return client.post("/cajero", data={"payment_method": metodo, "sale_items_json": json.dumps(items)},
                       follow_redirects=True)


def test_p05_01_venta_en_efectivo_descuenta_variante_y_registra(client, zapato):
    login(client, "cajero_a")
    r = _vender(client, [{"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"],
                          "quantity": 2, "sale_price": 50000}])
    assert "Venta registrada correctamente" in r.get_data(as_text=True)
    assert variants_of(zapato["p"]["id"], zapato["bid"])[0]["stock"] == 3
    sale = query("SELECT * FROM sales ORDER BY id DESC LIMIT 1")[0]
    assert sale["status"] == "PAID" and float(sale["total"]) == 100000.0


def test_p05_02_precio_manipulado_al_alza_se_ignora(client, zapato):
    """El navegador no puede subir el precio; el precio base sale de la BD."""
    login(client, "cajero_a")
    _vender(client, [{"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"],
                      "quantity": 1, "sale_price": 999999}])
    item = query("SELECT original_price, sale_price FROM sale_items ORDER BY id DESC LIMIT 1")[0]
    assert float(item["sale_price"]) == float(item["original_price"]) == 50000.0


def test_p05_03_stock_insuficiente(client, zapato):
    login(client, "cajero_a")
    r = _vender(client, [{"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"],
                          "quantity": 50, "sale_price": 50000}])
    assert "Stock insuficiente" in r.get_data(as_text=True)


@pytest.mark.parametrize("item,mensaje", [
    ({"quantity": 0}, "cantidad"),
    ({"quantity": -1}, "cantidad"),
    ({"sale_price": -100}, "negativo"),
    ({"product_id": "abc"}, "no es válido"),
])
def test_p05_04_entradas_invalidas_rechazadas(client, zapato, item, mensaje):
    login(client, "cajero_a")
    base = {"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"], "quantity": 1, "sale_price": 1}
    base.update(item)
    stock = variants_of(zapato["p"]["id"], zapato["bid"])[0]["stock"]
    r = _vender(client, [base])
    assert mensaje in r.get_data(as_text=True)
    assert variants_of(zapato["p"]["id"], zapato["bid"])[0]["stock"] == stock


def test_p05_05_json_malformado(client, zapato):
    login(client, "cajero_a")
    r = client.post("/cajero", data={"payment_method": "efectivo", "sale_items_json": "{no-json"},
                    follow_redirects=True)
    assert "no son válidos" in r.get_data(as_text=True)


def test_p05_06_metodo_de_pago_no_valido(client, zapato):
    login(client, "cajero_a")
    r = _vender(client, [{"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"],
                          "quantity": 1, "sale_price": 1}], metodo="bitcoin")
    assert "Método de pago no válido" in r.get_data(as_text=True)


def test_p05_07_producto_con_variantes_exige_talla(client, zapato):
    login(client, "cajero_a")
    r = _vender(client, [{"product_id": zapato["p"]["id"], "quantity": 1, "sale_price": 50000}])
    assert "Selecciona la talla" in r.get_data(as_text=True)


def test_p05_08_numeros_de_recibo_consecutivos_y_unicos(client, zapato):
    rows = query("SELECT receipt_number FROM sales WHERE business_id=%s", (zapato["bid"],))
    nums = [r["receipt_number"] for r in rows]
    assert len(nums) == len(set(nums)) and len(nums) >= 2


def test_p05_09_reportes_incluyen_ventas_pagadas(client, zapato):
    login(client, "admin_a")
    r = client.get("/reportes/api/reportes")
    assert r.status_code == 200 and r.is_json


@pytest.mark.xfail(strict=True, reason="Hallazgo H-07: el cajero puede vender a precio 0 (descuento sin límite ni autorización)")
def test_p05_10_descuento_requiere_limite(client, zapato):
    login(client, "cajero_a")
    _vender(client, [{"product_id": zapato["p"]["id"], "variant_id": zapato["v"]["id"],
                      "quantity": 1, "sale_price": 0}])
    item = query("SELECT sale_price FROM sale_items ORDER BY id DESC LIMIT 1")[0]
    assert float(item["sale_price"]) > 0
