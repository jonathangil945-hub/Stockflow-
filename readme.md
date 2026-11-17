# StockFlow — Sistema de inventario y ventas

Aplicación Flask + PostgreSQL para una tienda de calzado/ropa: inventario por **talla y color**,
ventas con recibo (efectivo, transferencia o **Wompi**: Nequi, Daviplata, PSE, tarjeta),
reportes con gráficos, alertas de stock bajo por correo y gestión de usuarios.

## Multi-tienda

Cada negocio/local es una **tienda** (tabla `businesses`). Productos, variantes, ventas,
movimientos y pagos llevan `business_id`, y PostgreSQL aplica **Row Level Security**:
cada conexión solo puede ver y crear datos de su tienda, aunque una consulta olvide filtrar.
La app trabaja con el rol `stockflow_app` (sin superusuario) para que el aislamiento se
cumpla aunque el `.env` use `postgres`.

## Roles

| Rol          | Puede usar                                                        |
|--------------|-------------------------------------------------------------------|
| `superadmin` | Panel **Tiendas**: crear tiendas y su admin, ver resumen, entrar a cualquier tienda |
| `admin`      | Todo dentro de SU tienda: Bodega, Ventas, Reportes y Usuarios     |
| `cajero`     | Ventas de su tienda                                               |
| `bodega`     | Inventario de su tienda                                           |

Los nombres de usuario son únicos en todo el sistema (así el login no pide tienda).

## Instalación (Windows / PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # y edita los valores
python main.py
```

Abre http://localhost:5000

* Al arrancar, la app **crea/actualiza las tablas** automáticamente (`scripts/schema.sql`).
  Se puede desactivar con `AUTO_MIGRATE=false` y ejecutarlo a mano:
  `psql -U postgres -d stockflow -f scripts/schema.sql`
* Si no existe un súper administrador se crea el usuario **superadmin** con la contraseña
  `SUPERADMIN_PASSWORD` del `.env` (o una temporal que se muestra en la consola).
  Cámbiala en «👤 Mi cuenta».
* Las contraseñas antiguas guardadas en texto plano siguen funcionando y se convierten
  a hash automáticamente la primera vez que el usuario ingresa.

## Estructura

```
main.py                      # crea la app, registra módulos, errores, migración
config.py                    # configuración (lee .env)
app/
  db/db.py                   # conexión PostgreSQL
  db/migrations.py           # aplica scripts/schema.sql al arrancar
  routes/auth_routes.py      # login, logout, cambiar y recuperar contraseña
  routes/bodega_routes.py    # inventario, movimientos, productos, códigos de barras
  routes/cajero_routes.py    # ventas, recibos, Wompi (checkout, retorno, webhook)
  routes/reportes_routes.py  # reportes y API de gráficos (solo admin)
  routes/admin_routes.py     # gestión de usuarios de la tienda (admin)
  routes/superadmin_routes.py # tiendas (súper administrador)
  services/inventory_service.py       # lógica de stock por variante (compartida)
  services/wompi_service.py           # integración Wompi
  services/email_service.py           # envío de correos SMTP
  services/inventory_alert_service.py # alertas de stock bajo
  utils/security.py          # decoradores de rol
scripts/schema.sql           # esquema de base de datos (idempotente)
templates/                   # páginas HTML
```

## Cómo funciona el stock

* Cada producto puede tener **variantes** (talla + color) en `product_variants`.
* `products.stock` es siempre la **suma** de sus variantes; tallas/colores del producto
  son un resumen.
* Bodega registra **entradas/salidas** por variante; cada venta descuenta la variante
  vendida. Todo queda en `inventory_movements` (se ve en «Últimos movimientos»).
* Stock bajo: entre 1 y `LOW_STOCK_LIMIT` unidades. Al quedar bajo, se envía un correo
  a `ADMIN_EMAIL` (en segundo plano).

## Wompi

1. Pon las llaves en `.env`. Con llaves `pub_test_`/`prv_test_` deja `WOMPI_SANDBOX=true`.
2. `BASE_URL` debe ser la URL con la que se abre la app (Wompi devuelve al cliente a
   `BASE_URL/cajero/payment-return`).
3. URL de eventos (webhook) en el panel de Wompi: `BASE_URL/cajero/wompi-webhook`
   (también funciona la antigua `/webhook/wompi`). En localhost Wompi no puede llamar
   al webhook, pero no importa: al volver del checkout la app consulta la transacción
   a Wompi, y en «Ventas de hoy» hay un botón **Verificar** para pagos pendientes.

## Correo (alertas y recuperar contraseña)

Con Gmail usa una **contraseña de aplicación** (no la normal) en `SMTP_PASSWORD`.
Para recuperar la contraseña, el usuario debe tener correo registrado en «Usuarios».

## Comandos útiles

```powershell
pip freeze > requirements.txt   # actualizar dependencias
python hash.py MiClave          # generar un hash de contraseña manualmente
deactivate                      # salir del entorno virtual
```

## Etiquetas QR (mini impresora térmica)

> Desde nov-2026 el sistema usa **solo códigos QR** (se eliminaron los códigos de barras). Todas las etiquetas tienen el mismo diseño: QR a la izquierda; tienda, producto, talla · color, precio grande y código a la derecha.

Menú **🏷️ Etiquetas** (bodega, cajero y admin) o botón **🏷️ Etiqueta** en el detalle de un producto en Bodega.

* Genera etiquetas PNG en blanco y negro puro al tamaño del rollo (50×30, 40×30, 40×40 mm, etc.) a 203 dpi,
  con QR (o código de barras), nombre, talla/color, precio y nombre de la tienda.
* Probado para la **Label Printer P1** (Shenzhen Tuofengda, app **ToPrint**, PIN 0000): abrir la página en el
  celular, descargar el PNG y en ToPrint insertar **Imagen** en una etiqueta del mismo tamaño e imprimir.
* El QR de una talla/color contiene `CODIGO#V<id>`: al escanearlo en **Ventas** se agrega directamente esa
  talla al carrito, y en **Bodega** abre esa variante.
* El QR se dibuja con `qrcode-generator` (CDN jsDelivr). Sin internet se usa código de barras automáticamente.

## Colores de la página (🎨)
En todas las pantallas hay un botón redondo 🎨 abajo a la izquierda para elegir:
modo **Oscuro / Claro**, **color principal** (8 colores o cualquiera con "Otro") y **fondo** (Noche, Carbón, Bosque, Vino).
La elección se guarda en el navegador de cada equipo (`localStorage`, clave `stockflow_theme_v1`).
Archivos: `static/css/theme.css` y `static/js/theme.js`; las plantillas usan variables CSS (`--primary`, `--primary-rgb`, `--fg-rgb`, `--surface-rgb`, …).

## Pruebas (marco Stock Flow)

La carpeta `tests/` contiene 115 pruebas automáticas (funcionales y de seguridad) organizadas
por bloques P01–P08: autenticación, control de acceso, aislamiento entre tiendas (RLS),
inventario, ventas, inyección SQL/XSS, pagos Wompi y configuración de seguridad.
Corren contra un PostgreSQL real usando una base **desechable** (`stockflow_test`, se borra en
cada ejecución; nunca toca la base `stockflow`).

```powershell
pip install -r requirements.txt -r requirements-dev.txt
$env:TEST_DB_PASS="tu_password_de_postgres"   # usuario postgres por defecto
pytest -v
```

Las pruebas marcadas **XFAIL** documentan hallazgos de seguridad abiertos (H-01…H-08);
cuando se corrija uno, su prueba pasará y pytest pedirá quitar la marca.
Resultados y hallazgos: `resultados/RESULTADOS.md`. En GitHub, el flujo
`.github/workflows/tests.yml` ejecuta las pruebas en cada push.
