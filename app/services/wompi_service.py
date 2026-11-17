import hashlib
import hmac
import logging
import os
import uuid
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# =========================
# ENV
# =========================

WOMPI_PUBLIC_KEY = os.getenv("WOMPI_PUBLIC_KEY", "").strip()
WOMPI_PRIVATE_KEY = os.getenv("WOMPI_PRIVATE_KEY", "").strip()
WOMPI_INTEGRITY_SECRET = os.getenv("WOMPI_INTEGRITY_SECRET", "").strip()
WOMPI_EVENTS_SECRET = os.getenv("WOMPI_EVENTS_SECRET", "").strip()
BASE_URL = os.getenv("BASE_URL", "").strip().rstrip("/")

# Antes estaba fijo en producción aunque WOMPI_SANDBOX=true: las consultas con
# llaves de prueba (pub_test_/prv_test_) fallaban siempre.
WOMPI_SANDBOX = os.getenv("WOMPI_SANDBOX", "true").strip().lower() in ("1", "true", "yes", "si", "sí")
WOMPI_BASE = "https://sandbox.wompi.co/v1" if WOMPI_SANDBOX else "https://production.wompi.co/v1"
WOMPI_CHECKOUT = "https://checkout.wompi.co/p/"

# Ruta a la que Wompi devuelve al cliente después de pagar
RETURN_PATH = "/cajero/payment-return"


def missing_config():
    """Lista de variables obligatorias que faltan (vacía si todo está bien).
    Antes se lanzaba un ValueError al importar el módulo y TODA la app dejaba de
    arrancar si faltaba una llave de Wompi."""
    required = {
        "WOMPI_PUBLIC_KEY": WOMPI_PUBLIC_KEY,
        "WOMPI_PRIVATE_KEY": WOMPI_PRIVATE_KEY,
        "WOMPI_INTEGRITY_SECRET": WOMPI_INTEGRITY_SECRET,
        "BASE_URL": BASE_URL,
    }
    return [k for k, v in required.items() if not v]


# =========================
# HELPERS
# =========================

def wompi_amount_in_cents(amount):
    """Convierte pesos COP a centavos. Ej: 15000 => 1500000"""
    value = (Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(value)


def wompi_generate_reference():
    """Referencia única. Antes usaba solo el segundo actual y dos ventas
    en el mismo segundo chocaban."""
    return f"SALE-{uuid.uuid4().hex[:16].upper()}"


def wompi_generate_signature(reference, amount_in_cents, currency="COP"):
    raw = f"{reference}{amount_in_cents}{currency}{WOMPI_INTEGRITY_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =========================
# CHECKOUT
# =========================

def create_checkout_data(total, reference=None):
    missing = missing_config()
    if missing:
        raise ValueError(f"Faltan variables de entorno de Wompi: {', '.join(missing)}")

    reference = reference or wompi_generate_reference()
    amount_in_cents = wompi_amount_in_cents(total)
    signature = wompi_generate_signature(reference, amount_in_cents)

    params = {
        "public-key": WOMPI_PUBLIC_KEY,
        "currency": "COP",
        "amount-in-cents": amount_in_cents,
        "reference": reference,
        "signature:integrity": signature,
        "redirect-url": f"{BASE_URL}{RETURN_PATH}",
    }
    checkout_url = WOMPI_CHECKOUT + "?" + urlencode(params)

    return {
        "reference": reference,
        "amount_in_cents": amount_in_cents,
        "signature": signature,
        "checkout_url": checkout_url,
    }


# =========================
# CONSULTAR TRANSACCIÓN
# =========================

def get_transaction(transaction_id):
    """Consulta la transacción directamente a Wompi (fuente de verdad)."""
    if not transaction_id:
        return None
    url = f"{WOMPI_BASE}/transactions/{transaction_id}"
    headers = {"Authorization": f"Bearer {WOMPI_PRIVATE_KEY}"} if WOMPI_PRIVATE_KEY else {}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            logger.warning("Wompi respondió %s consultando %s", res.status_code, transaction_id)
            return None
        return (res.json() or {}).get("data")
    except (requests.RequestException, ValueError) as e:
        logger.error("Error consultando transacción Wompi %s: %s", transaction_id, e)
        return None


def find_transaction_by_reference(reference):
    """Busca la transacción de una referencia (sirve cuando el webhook no llega,
    p. ej. trabajando en localhost). Prioriza una aprobada si hay varias."""
    if not reference or not WOMPI_PRIVATE_KEY:
        return None
    try:
        res = requests.get(
            f"{WOMPI_BASE}/transactions",
            params={"reference": reference},
            headers={"Authorization": f"Bearer {WOMPI_PRIVATE_KEY}"},
            timeout=15,
        )
        if res.status_code != 200:
            logger.warning("Wompi respondió %s buscando la referencia %s", res.status_code, reference)
            return None
        items = (res.json() or {}).get("data") or []
    except (requests.RequestException, ValueError) as e:
        logger.error("Error buscando referencia Wompi %s: %s", reference, e)
        return None
    if not items:
        return None
    approved = [t for t in items if t.get("status") == "APPROVED"]
    return (approved or items)[0]


# =========================
# VALIDAR WEBHOOK (eventos)
# =========================

def _dig(data, dotted_path):
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return "" if current is None else current


def verify_event(payload, header_checksum=""):
    """Valida la firma de un evento de Wompi.

    Wompi firma así: SHA256( valores de signature.properties (tomados de data)
    + timestamp + WOMPI_EVENTS_SECRET ). El resultado viene en
    payload.signature.checksum y en el header X-Event-Checksum.
    (La versión anterior usaba HMAC del cuerpo, que nunca coincide.)
    """
    if not WOMPI_EVENTS_SECRET:
        logger.warning("WOMPI_EVENTS_SECRET no configurado: no se puede validar la firma del evento.")
        return False

    signature = (payload or {}).get("signature") or {}
    properties = signature.get("properties") or []
    timestamp = (payload or {}).get("timestamp", "")
    received = (signature.get("checksum") or header_checksum or "").strip().lower()
    if not properties or not received:
        return False

    data = (payload or {}).get("data") or {}
    concatenated = "".join(str(_dig(data, prop)) for prop in properties)
    concatenated += f"{timestamp}{WOMPI_EVENTS_SECRET}"
    computed = hashlib.sha256(concatenated.encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, received)


def parse_event(payload):
    transaction = ((payload or {}).get("data") or {}).get("transaction") or {}
    return {
        "event": (payload or {}).get("event"),
        "transaction_id": transaction.get("id"),
        "reference": transaction.get("reference"),
        "status": transaction.get("status"),
        "amount_in_cents": transaction.get("amount_in_cents"),
        "payment_method_type": transaction.get("payment_method_type"),
    }
