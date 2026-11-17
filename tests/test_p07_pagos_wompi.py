"""P07 · Integración de pagos Wompi: firma de eventos y checkout (OWASP A08)."""
import hashlib
import json

import pytest

from conftest import create_product, login, product_by_name, query, variants_of


def _firmar(payload, secret="test_events_dummy"):
    props = payload["signature"]["properties"]
    data = payload["data"]
    vals = ""
    for p in props:
        cur = data
        for part in p.split("."):
            cur = cur[part]
        vals += str(cur)
    payload["signature"]["checksum"] = hashlib.sha256(f"{vals}{payload['timestamp']}{secret}".encode()).hexdigest()
    return payload


def _evento(reference, status="APPROVED", amount=5000000):
    return {
        "event": "transaction.updated",
        "data": {"transaction": {"id": "tx-test-1", "reference": reference, "status": status,
                                 "amount_in_cents": amount, "payment_method_type": "NEQUI"}},
        "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                      "checksum": ""},
        "timestamp": 1794500000,
    }


def _checkout(app, stores, nombre):
    c = app.test_client()
    login(c, "bodega_a")
    create_product(c, nombre, price="50000", stock="3", size="37", color="Rojo")
    bid = stores["a"]["business_id"]
    p = product_by_name(nombre, bid)
    v = variants_of(p["id"], bid)[0]
    cc = app.test_client()
    login(cc, "cajero_a")
    r = cc.post("/cajero/create-checkout", json={"payment_method": "nequi", "items": [
        {"product_id": p["id"], "variant_id": v["id"], "quantity": 1, "sale_price": 50000}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    return {"reference": data["reference"], "sale_id": data["sale_id"], "p": p, "v": v, "bid": bid,
            "checkout_url": data["checkout_url"]}


@pytest.fixture(scope="module")
def venta_pendiente(app, stores):
    return _checkout(app, stores, "Sandalia Wompi")


@pytest.fixture(scope="module")
def venta_monto_alterado(app, stores):
    return _checkout(app, stores, "Sandalia Wompi Monto")


def test_p07_01_checkout_crea_venta_pendiente_sin_descontar_stock(venta_pendiente):
    sale = query("SELECT status, total FROM sales WHERE id=%s", (venta_pendiente["sale_id"],))[0]
    assert sale["status"] == "PENDING" and float(sale["total"]) == 50000.0
    assert variants_of(venta_pendiente["p"]["id"], venta_pendiente["bid"])[0]["stock"] == 3
    assert "signature%3Aintegrity" in venta_pendiente["checkout_url"] or "signature" in venta_pendiente["checkout_url"]


def test_p07_02_webhook_sin_referencia(client):
    assert client.post("/cajero/wompi-webhook", json={}).status_code == 400


def test_p07_03_webhook_con_firma_falsa_es_rechazado(client, venta_pendiente):
    ev = _evento(venta_pendiente["reference"])
    ev["signature"]["checksum"] = "0" * 64
    r = client.post("/cajero/wompi-webhook", json=ev)
    assert r.status_code == 401
    assert query("SELECT status FROM sales WHERE id=%s", (venta_pendiente["sale_id"],))[0]["status"] == "PENDING"


def test_p07_04_webhook_con_monto_distinto_no_aprueba(client, venta_monto_alterado):
    v = venta_monto_alterado
    ev = _firmar(_evento(v["reference"], amount=100))
    client.post("/cajero/wompi-webhook", json=ev)
    assert query("SELECT status FROM sales WHERE id=%s", (v["sale_id"],))[0]["status"] == "AMOUNT_MISMATCH"
    assert variants_of(v["p"]["id"], v["bid"])[0]["stock"] == 3


def test_p07_05_webhook_firmado_aprueba_y_descuenta_una_sola_vez(client, venta_pendiente):
    ev = _firmar(_evento(venta_pendiente["reference"]))
    for _ in range(3):  # reintentos de Wompi: debe ser idempotente
        assert client.post("/cajero/wompi-webhook", data=json.dumps(ev),
                           content_type="application/json").status_code == 200
    assert query("SELECT status FROM sales WHERE id=%s", (venta_pendiente["sale_id"],))[0]["status"] == "PAID"
    assert variants_of(venta_pendiente["p"]["id"], venta_pendiente["bid"])[0]["stock"] == 2


def test_p07_06_checkout_rechaza_metodo_manual(client, stores):
    login(client, "cajero_a")
    r = client.post("/cajero/create-checkout", json={"payment_method": "efectivo", "items": []})
    assert r.status_code == 400
