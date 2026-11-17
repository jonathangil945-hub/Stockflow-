# Resultados de pruebas — StockFlow (marco Stock Flow v0.1)

* **Fecha de ejecución:** 17-nov-2026
* **Objeto evaluado:** `Proyecto_Inventario` (Flask 3 + PostgreSQL 16, multi-tienda con RLS)
* **Entorno:** base de datos desechable `stockflow_test`, datos sintéticos (2 tiendas, 6 usuarios), sin correo ni pagos reales (simulados)
* **Comando:** `pytest -v --junitxml=resultados/junit.xml`
* **Evidencias:** `resultados/junit.xml`, `resultados/pytest_salida.txt`

## Resumen

| Casos ejecutados | Superados | Hallazgos confirmados (xfail) | Fallos inesperados | Cobertura del plan |
|---|---|---|---|---|
| 115 | 107 | 8 | 0 | 100 % (115/115) |

| ID | Bloque | Casos | Superados | Hallazgos |
|---|---|---|---|---|
| P01 | Autenticación y sesión | 14 | 14 | 0 |
| P02 | Control de acceso por rol | 34 | 34 | 0 |
| P03 | Aislamiento entre tiendas (RLS / IDOR) | 9 | 9 | 0 |
| P04 | Inventario por talla y color | 10 | 10 | 0 |
| P05 | Ventas y lógica de negocio | 13 | 12 | 1 (H-07) |
| P06 | Inyección SQL y XSS | 16 | 16 | 0 |
| P07 | Pagos Wompi (firma, idempotencia) | 6 | 6 | 0 |
| P08 | Recuperación de clave y configuración | 13 | 6 | 7 (H-01…H-06, H-08) |

## Hallazgos

| ID | Hallazgo | OWASP Top 10 (2021) | Severidad | Prueba | Recomendación |
|---|---|---|---|---|---|
| H-01 | Formularios POST sin token CSRF | A01 | Alta | test_p08_10 | Flask-WTF `CSRFProtect` + `{{ csrf_token() }}` en cada formulario y cabecera en `fetch` |
| H-02 | Sin límite de intentos de inicio de sesión | A07 | Alta | test_p08_11 | Flask-Limiter (p. ej. 5/min por IP y usuario) y bloqueo temporal |
| H-03 | Enlace de recuperación construido con la cabecera Host | A05 | Media | test_p08_12 | Construir el enlace con `BASE_URL` del `.env` / `SERVER_NAME` fijo |
| H-04 | Faltan cabeceras de seguridad (CSP, X-Frame-Options, nosniff) | A05 | Media | test_p08_13 | `after_request` con cabeceras o Flask-Talisman |
| H-05 | `FLASK_DEBUG=true` por defecto con `host=0.0.0.0` | A05 | Alta | test_p08_14 | Valor por defecto `false`; servir con Waitress/Gunicorn en producción |
| H-06 | Los mensajes de error muestran la excepción interna | A05 | Baja | test_p08_15 | Mensaje genérico al usuario; detalle solo en el log |
| H-07 | El cajero puede vender a precio 0 (descuento sin límite) | A04 | Media | test_p05_10 | Tope de descuento por rol y autorización del admin por encima del tope |
| H-08 | Se aceptan contraseñas heredadas en texto plano | A02 | Baja | test_p08_16 | Migrar las claves restantes y retirar la compatibilidad |

Controles verificados como efectivos: consultas parametrizadas (sin SQLi), escape de HTML (sin XSS),
aislamiento por tienda con RLS, control de acceso por rol, hash de contraseñas (scrypt), cookies
`HttpOnly`/`SameSite=Lax`, protección contra redirección abierta, firma SHA-256 de eventos Wompi e
idempotencia del webhook, precio de venta tomado de la base de datos, token de recuperación de un solo uso.

> Una ejecución satisfactoria no implica ausencia de vulnerabilidades: la cobertura depende de los casos definidos.
