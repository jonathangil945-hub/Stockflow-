# app/services/inventory_alert_service.py

import os
import logging
import threading

from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

# FIX 4: parseo seguro — si LOW_STOCK_LIMIT="abc" en .env no revienta al importar
def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class InventoryAlertService:

    # FIX 4: parseo seguro aplicado al atributo de clase
    LOW_STOCK_LIMIT = _safe_int(os.getenv("LOW_STOCK_LIMIT"), default=4)

    @staticmethod
    def check_low_stock(
        product_name:  str,
        current_stock: int,
        *,
        # FIX 2: size/color/location opcionales para que el email incluya
        # información de variante (esencial en zapatería)
        size:     str = "",
        color:    str = "",
        location: str = "",
        admin_email: str = None,
    ) -> bool:
        """
        Verifica si el producto tiene stock bajo y envía alerta al admin.

        Retorna True si la alerta fue enviada, False en cualquier otro caso.
        """
        # FIX 6: validar product_name para evitar "⚠️ Stock bajo: None" en el asunto
        product_name = (product_name or "Producto desconocido").strip()

        admin_email = (admin_email or os.getenv("ADMIN_EMAIL") or "").strip()

        if not admin_email:
            # FIX 5: logging en lugar de print()
            logger.warning(
                "ADMIN_EMAIL no configurado — no se puede enviar alerta para '%s'.",
                product_name
            )
            return False

        # Stock suficiente — sin alerta (límite es inclusivo: <= LOW_STOCK_LIMIT alerta)
        if current_stock > InventoryAlertService.LOW_STOCK_LIMIT:
            return False

        try:
            sent = EmailService.send_low_stock_alert(
                product_name  = product_name,
                current_stock = current_stock,
                admin_email   = admin_email,
                size          = size,
                color         = color,
                location      = location,
                threshold     = InventoryAlertService.LOW_STOCK_LIMIT,
            )

            if sent:
                # FIX 5: logging en lugar de print()
                logger.info(
                    "Alerta de stock bajo enviada: '%s' | stock=%s | talla=%s | color=%s",
                    product_name, current_stock, size or "—", color or "—"
                )
            else:
                logger.warning(
                    "No se pudo enviar alerta para '%s' (stock=%s).",
                    product_name, current_stock
                )

            return sent

        except Exception as e:
            # FIX 5: logging en lugar de print()
            logger.error(
                "Error enviando alerta de stock para '%s': %s",
                product_name, e, exc_info=True
            )
            return False

    @staticmethod
    def admin_email_for_business(business_id):
        """Correo al que van las alertas de una tienda: su admin, o el correo de
        la tienda, o ADMIN_EMAIL del .env como último recurso."""
        if not business_id:
            return None
        try:
            from app.db.db import get_db
            conn = get_db(bypass=True)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COALESCE(
                            (SELECT email FROM users WHERE business_id = %s AND role = 'admin'
                               AND COALESCE(active, TRUE) AND COALESCE(email, '') <> '' ORDER BY id LIMIT 1),
                            (SELECT NULLIF(email, '') FROM businesses WHERE id = %s)
                        ) AS email
                    """, (business_id, business_id))
                    row = cur.fetchone()
            finally:
                conn.close()
            return row["email"] if row else None
        except Exception as e:
            logger.error("No se pudo obtener el correo del admin de la tienda %s: %s", business_id, e)
            return None

    @staticmethod
    def check_low_stock_async(product_name: str, current_stock: int, **kwargs) -> None:
        """Igual que check_low_stock pero en segundo plano: el envío SMTP puede
        tardar varios segundos y no debe bloquear la venta ni el movimiento."""
        try:
            current_stock = int(current_stock or 0)
        except (TypeError, ValueError):
            return
        if current_stock > InventoryAlertService.LOW_STOCK_LIMIT:
            return
        threading.Thread(
            target=InventoryAlertService.check_low_stock,
            args=(product_name, current_stock),
            kwargs=kwargs,
            daemon=True,
        ).start()

    @staticmethod
    def check_multiple_products(products: list) -> dict:
        """
        Recibe una lista de productos y verifica cuáles necesitan alerta.

        Cada elemento puede tener:
            name     — nombre del producto (requerido)
            stock    — stock actual (requerido)
            size     — talla (opcional)
            color    — color (opcional)
            location — ubicación en bodega (opcional)

        Ejemplo:
            [
                {"name": "Nike Air Max", "stock": 2, "size": "42", "color": "Negro"},
                {"name": "Adidas Samba", "stock": 10},
            ]

        Retorna un dict con:
            sent    — alertas enviadas exitosamente
            failed  — alertas que intentaron enviarse pero fallaron
            skipped — productos con stock suficiente (sin alerta)
            errors  — lista de nombres de productos que fallaron
        """
        # FIX 3: resultado detallado en lugar de solo el conteo
        result = {
            "sent":    0,
            "failed":  0,
            "skipped": 0,
            "errors":  [],
        }

        for product in products:
            name  = (product.get("name") or "").strip() or "Producto desconocido"
            stock = product.get("stock", 0)

            # Saltar productos con stock suficiente sin ni siquiera intentar
            if stock > InventoryAlertService.LOW_STOCK_LIMIT:
                result["skipped"] += 1
                continue

            sent = InventoryAlertService.check_low_stock(
                product_name  = name,
                current_stock = stock,
                size          = product.get("size",     ""),
                color         = product.get("color",    ""),
                location      = product.get("location", ""),
            )

            if sent:
                result["sent"] += 1
            else:
                result["failed"] += 1
                result["errors"].append(name)

        logger.info(
            "check_multiple_products: %d enviadas, %d fallidas, %d sin alerta.",
            result["sent"], result["failed"], result["skipped"]
        )

        return result