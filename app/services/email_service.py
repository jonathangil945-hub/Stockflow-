# app/services/email_service.py

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

# Umbral de stock bajo — se puede sobreescribir con la variable de entorno
try:
    LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD") or os.getenv("LOW_STOCK_LIMIT") or "4")
except ValueError:
    LOW_STOCK_THRESHOLD = 4


class EmailService:

    @staticmethod
    def _get_smtp_config():
        """
        Lee y valida las variables de entorno SMTP.
        Lanza ValueError con mensaje claro si falta alguna.
        """
        # FIX 2: validar variables antes de intentar conectar
        smtp_server = os.getenv("SMTP_SERVER", "").strip()
        smtp_user   = os.getenv("SMTP_USER",   "").strip()
        # Las contraseñas de aplicación de Gmail se muestran como "abcd efgh ijkl mnop":
        # los espacios no son parte de la clave
        smtp_pass   = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")

        # FIX 5: parseo seguro del puerto con fallback
        try:
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
        except (ValueError, TypeError):
            smtp_port = 587

        missing = [name for name, val in [
            ("SMTP_SERVER",   smtp_server),
            ("SMTP_USER",     smtp_user),
            ("SMTP_PASSWORD", smtp_pass),
        ] if not val]

        if missing:
            raise ValueError(
                f"Faltan variables de entorno para el correo: {', '.join(missing)}. "
                "Configúralas en el archivo .env antes de enviar emails."
            )

        return smtp_server, smtp_port, smtp_user, smtp_pass

    @staticmethod
    def send_email(to_email: str, subject: str, body: str) -> bool:
        """
        Envía un correo plain-text.
        Retorna True si se envió, False si falló.
        """
        if not to_email or not to_email.strip():
            logger.warning("send_email: destinatario vacío, correo no enviado.")
            return False

        try:
            smtp_server, smtp_port, smtp_user, smtp_pass = EmailService._get_smtp_config()
        except ValueError as e:
            logger.error("Configuración SMTP incompleta: %s", e)
            return False

        message = MIMEMultipart()
        message["From"]    = smtp_user
        message["To"]      = to_email
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain", "utf-8"))

        try:
            # FIX 1: context manager garantiza que la conexión se cierra
            # aunque falle login() o send_message()
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.send_message(message)

            # FIX 3: logging en lugar de print()
            logger.info("Correo enviado a %s | Asunto: %s", to_email, subject)
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(
                "Error de autenticación SMTP — verifica SMTP_USER y SMTP_PASSWORD."
            )
            return False

        except smtplib.SMTPConnectError:
            logger.error(
                "No se pudo conectar al servidor SMTP %s:%s.", smtp_server, smtp_port
            )
            return False

        except Exception as e:
            logger.error("Error enviando correo a %s: %s", to_email, e, exc_info=True)
            return False

    @staticmethod
    def send_low_stock_alert(
        product_name:  str,
        current_stock: int,
        admin_email:   str,
        *,
        # FIX 6: talla y color opcionales para dar contexto en tienda de zapatos
        size:     str = "",
        color:    str = "",
        location: str = "",
        threshold: int = LOW_STOCK_THRESHOLD,
    ) -> bool:
        """
        Envía alerta de stock bajo al administrador del local.

        Parámetros opcionales:
            size      — talla del producto (ej: "42")
            color     — color de la variante (ej: "Negro")
            location  — ubicación en bodega (ej: "Estante A-3")
            threshold — límite mínimo de stock (por defecto LOW_STOCK_THRESHOLD)
        """
        variant_info = ""
        if size or color:
            parts = []
            if size:     parts.append(f"Talla: {size}")
            if color:    parts.append(f"Color: {color}")
            if location: parts.append(f"Ubicación: {location}")
            variant_info = "\nVariante afectada:\n  " + " | ".join(parts) + "\n"

        subject = f"⚠️ Stock bajo: {product_name}"
        if size or color:
            subject += f" ({' / '.join(filter(None, [size, color]))})"

        body = f"""\
Hola,

El siguiente producto ha alcanzado un nivel bajo de inventario en StockFlow:

Producto : {product_name}{variant_info}
Stock actual : {current_stock} unidad(es)
Límite mínimo: {threshold} unidad(es)

Se recomienda realizar reposición lo antes posible para evitar
quiebres de stock.

———
Mensaje generado automáticamente por StockFlow.
No responder a este correo.
"""

        return EmailService.send_email(admin_email, subject, body)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER PARA bodega_routes.py
# ─────────────────────────────────────────────────────────────────────────────

def check_and_notify_low_stock(
    product_id:   int,
    product_name: str,
    total_stock:  int,
    cur,
    *,
    size:     str = "",
    color:    str = "",
    location: str = "",
) -> None:
    """
    Llama a esta función después de cada movimiento de inventario en
    bodega_routes.move_product_stock(), justo antes del conn.commit().

    Ejemplo de uso en bodega_routes.py:

        from app.services.email_service import check_and_notify_low_stock

        # Al final de move_product_stock(), antes del commit:
        check_and_notify_low_stock(
            product_id   = product_id,
            product_name = product["name"],
            total_stock  = total_stock,
            cur          = cur,
            size         = size,
            color        = color,
            location     = movement_location or "",
        )
        conn.commit()

    La función busca el email del admin en la tabla 'users' con role='admin'.
    Si no hay admin o el stock no está bajo, no hace nada.
    """
    if total_stock >= LOW_STOCK_THRESHOLD:
        return  # Stock suficiente, no hay nada que notificar

    try:
        # Buscar el email del admin del local
        cur.execute("""
            SELECT email
            FROM users
            WHERE role = 'admin'
              AND email IS NOT NULL
              AND email <> ''
            LIMIT 1
        """)
        row = cur.fetchone()

        if not row:
            logger.warning(
                "Stock bajo en producto %s (id=%s) pero no hay admin con email configurado.",
                product_name, product_id
            )
            return

        admin_email = row["email"] if isinstance(row, dict) else row[0]

        EmailService.send_low_stock_alert(
            product_name  = product_name,
            current_stock = total_stock,
            admin_email   = admin_email,
            size          = size,
            color         = color,
            location      = location,
        )

    except Exception as e:
        # No propagar — un fallo de email no debe romper la transacción
        logger.error(
            "Error verificando stock bajo para producto %s: %s",
            product_name, e, exc_info=True
        )

def send_test_email(to_email: str):
    """Envía un correo de prueba y devuelve (ok, mensaje legible) con el motivo
    exacto si falla, para mostrarlo en pantalla."""
    if not to_email:
        return False, "No hay correo destino: agrega un correo al administrador de la tienda o ADMIN_EMAIL en .env."
    try:
        smtp_server, smtp_port, smtp_user, smtp_pass = EmailService._get_smtp_config()
    except ValueError as e:
        return False, str(e)

    message = MIMEMultipart()
    message["From"] = smtp_user
    message["To"] = to_email
    message["Subject"] = "✅ Prueba de alertas - StockFlow"
    message.attach(MIMEText(
        "Este es un correo de prueba de StockFlow.\n\n"
        "Si lo recibiste, las alertas de stock bajo llegarán a esta dirección.\n", "plain", "utf-8"))
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.send_message(message)
        return True, f"Correo de prueba enviado a {to_email}. Revisa la bandeja de entrada (y spam)."
    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rechazó el usuario/contraseña (SMTP_USER / SMTP_PASSWORD). Debe ser una "
                       "«contraseña de aplicación» de Google con verificación en 2 pasos activa.")
    except (smtplib.SMTPConnectError, OSError) as e:
        return False, f"No se pudo conectar a {smtp_server}:{smtp_port} ({e})."
    except Exception as e:
        return False, f"Error enviando el correo: {e}"
