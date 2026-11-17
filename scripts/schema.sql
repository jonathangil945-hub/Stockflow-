-- =====================================================================
-- StockFlow - Esquema de base de datos (PostgreSQL)
-- ---------------------------------------------------------------------
-- Es idempotente: se puede ejecutar muchas veces sin dañar datos.
-- La app lo ejecuta sola al arrancar (AUTO_MIGRATE=true en .env).
-- Manual:  psql -U postgres -d stockflow -f scripts/schema.sql
-- =====================================================================

-- ---------- USUARIOS ----------
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(80)  NOT NULL UNIQUE,
    password    VARCHAR(255) NOT NULL,
    role        VARCHAR(20)  NOT NULL DEFAULT 'cajero',
    email       VARCHAR(150),
    full_name   VARCHAR(150),
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS email      VARCHAR(150);
ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name  VARCHAR(150);
ALTER TABLE users ADD COLUMN IF NOT EXISTS active     BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
-- los hashes de contraseña (scrypt) miden ~160 caracteres
ALTER TABLE users ALTER COLUMN password TYPE TEXT;

-- ---------- PRODUCTOS ----------
CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    barcode     VARCHAR(64) UNIQUE,
    name        VARCHAR(150) NOT NULL,
    model       VARCHAR(150),
    category    VARCHAR(80),
    size        TEXT,
    color       TEXT,
    price       NUMERIC(12,2) NOT NULL DEFAULT 0,
    stock       INTEGER NOT NULL DEFAULT 0,
    location    TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE products ADD COLUMN IF NOT EXISTS location   TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
-- tallas/colores/ubicaciones se guardan resumidas ("38, 39, 40"): deben ser TEXT
ALTER TABLE products ALTER COLUMN size     TYPE TEXT;
ALTER TABLE products ALTER COLUMN color    TYPE TEXT;
ALTER TABLE products ALTER COLUMN location TYPE TEXT;

-- ---------- VARIANTES (talla + color) ----------
CREATE TABLE IF NOT EXISTS product_variants (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    size        VARCHAR(20),
    color       VARCHAR(50),
    stock       INTEGER NOT NULL DEFAULT 0,
    location    TEXT
);
ALTER TABLE product_variants ADD COLUMN IF NOT EXISTS location TEXT;
CREATE INDEX IF NOT EXISTS idx_variants_product ON product_variants(product_id);

-- ---------- MOVIMIENTOS DE INVENTARIO ----------
CREATE TABLE IF NOT EXISTS inventory_movements (
    id             SERIAL PRIMARY KEY,
    product_id     INTEGER NOT NULL,
    variant_id     INTEGER,
    movement_type  VARCHAR(20) NOT NULL,   -- entrada | salida | ajuste
    quantity       INTEGER NOT NULL,
    note           TEXT,
    location       TEXT,
    user_id        INTEGER,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS variant_id INTEGER;
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS location   TEXT;
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS user_id    INTEGER;
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_movements_product ON inventory_movements(product_id, created_at DESC);

-- ---------- VENTAS ----------
CREATE TABLE IF NOT EXISTS sales (
    id                    SERIAL PRIMARY KEY,
    receipt_number        VARCHAR(40),
    reference             VARCHAR(60),
    customer_name         VARCHAR(150),
    customer_phone        VARCHAR(40),
    payment_method        VARCHAR(40),
    payment_provider      VARCHAR(30),
    status                VARCHAR(20) DEFAULT 'PAID',
    note                  TEXT,
    subtotal              NUMERIC(12,2) NOT NULL DEFAULT 0,
    discount_total        NUMERIC(12,2) NOT NULL DEFAULT 0,
    total                 NUMERIC(12,2) NOT NULL DEFAULT 0,
    seller_user_id        INTEGER,
    wompi_transaction_id  VARCHAR(80),
    paid_at               TIMESTAMP,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS reference            VARCHAR(60);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS payment_provider     VARCHAR(30);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS status               VARCHAR(20) DEFAULT 'PAID';
ALTER TABLE sales ADD COLUMN IF NOT EXISTS wompi_transaction_id VARCHAR(80);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS paid_at              TIMESTAMP;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS seller_user_id       INTEGER;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS note                 TEXT;
CREATE INDEX IF NOT EXISTS idx_sales_reference ON sales(reference);
CREATE INDEX IF NOT EXISTS idx_sales_created   ON sales(created_at);

-- Ventas manuales antiguas quedaban sin estado/fecha de pago y no salían en reportes
UPDATE sales SET status = 'PAID'
 WHERE status IS NULL AND payment_provider IS NULL;
UPDATE sales SET paid_at = created_at
 WHERE paid_at IS NULL AND status = 'PAID';

CREATE TABLE IF NOT EXISTS sale_items (
    id                 SERIAL PRIMARY KEY,
    sale_id            INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    product_id         INTEGER NOT NULL,
    variant_id         INTEGER,
    product_name       VARCHAR(150),
    product_model      VARCHAR(150),
    product_size       VARCHAR(40),
    product_color      VARCHAR(60),
    product_barcode    VARCHAR(64),
    quantity           INTEGER NOT NULL,
    original_price     NUMERIC(12,2) NOT NULL DEFAULT 0,
    sale_price         NUMERIC(12,2) NOT NULL DEFAULT 0,
    discount_per_unit  NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal           NUMERIC(12,2) NOT NULL DEFAULT 0
);
ALTER TABLE sale_items ADD COLUMN IF NOT EXISTS variant_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);

-- ---------- EVENTOS DE PAGO (Wompi) ----------
CREATE TABLE IF NOT EXISTS payment_events (
    id                    SERIAL PRIMARY KEY,
    provider              VARCHAR(30),
    event_type            VARCHAR(60),
    reference             VARCHAR(60),
    wompi_transaction_id  VARCHAR(80),
    payload               JSONB,
    created_at            TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ---------- CONSECUTIVO DE RECIBOS ----------
CREATE SEQUENCE IF NOT EXISTS receipt_daily_seq START 1;

-- =====================================================================
-- MULTI-TIENDA
-- Cada negocio/local es una fila de `businesses`. Todas las tablas de datos
-- llevan business_id y PostgreSQL (Row Level Security) solo deja ver y crear
-- filas de la tienda activa en la conexión (app.business_id).
-- =====================================================================

CREATE TABLE IF NOT EXISTS businesses (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(150) NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS nit             VARCHAR(40);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS phone           VARCHAR(40);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS address         VARCHAR(200);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email           VARCHAR(150);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS receipt_counter INTEGER NOT NULL DEFAULT 0;

-- Si no hay ninguna tienda, los datos existentes pasan a "Tienda principal"
INSERT INTO businesses (name, slug)
SELECT 'Tienda principal', 'tienda-principal'
WHERE NOT EXISTS (SELECT 1 FROM businesses);

-- El consecutivo de recibos ahora es por tienda: la primera tienda continúa
-- desde donde iba el consecutivo global anterior
DO $$
BEGIN
    IF to_regclass('public.receipt_daily_seq') IS NOT NULL THEN
        UPDATE businesses
           SET receipt_counter = (SELECT last_value FROM receipt_daily_seq)
         WHERE id = (SELECT MIN(id) FROM businesses) AND receipt_counter = 0;
    END IF;
END $$;

-- Usuarios: business_id NULL solo para el súper administrador
ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id INTEGER;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_business_fk') THEN
        ALTER TABLE users ADD CONSTRAINT users_business_fk
            FOREIGN KEY (business_id) REFERENCES businesses(id);
    END IF;
END $$;
UPDATE users SET business_id = (SELECT MIN(id) FROM businesses)
 WHERE business_id IS NULL AND role <> 'superadmin';
CREATE INDEX IF NOT EXISTS idx_users_business ON users(business_id);

-- business_id obligatorio + valor automático + aislamiento en cada tabla de datos
DO $$
DECLARE
    t            TEXT;
    default_biz  INTEGER;
    cur_setting  CONSTANT TEXT := 'NULLIF(current_setting(''app.business_id'', true), '''')::integer';
BEGIN
    SELECT MIN(id) INTO default_biz FROM businesses;
    FOREACH t IN ARRAY ARRAY['products', 'product_variants', 'inventory_movements',
                             'sales', 'sale_items', 'payment_events'] LOOP
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS business_id INTEGER', t);
        EXECUTE format('UPDATE %I SET business_id = %s WHERE business_id IS NULL', t, default_biz);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN business_id SET DEFAULT %s', t, cur_setting);
        EXECUTE format('ALTER TABLE %I ALTER COLUMN business_id SET NOT NULL', t);
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (business_id)', 'idx_' || t || '_business', t);
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = t || '_business_fk') THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (business_id) REFERENCES businesses(id)',
                           t, t || '_business_fk');
        END IF;
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (business_id = %s) WITH CHECK (business_id = %s)',
                       t, cur_setting, cur_setting);
    END LOOP;
END $$;

-- El código de barras es único dentro de cada tienda (no entre tiendas)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_barcode_key') THEN
        ALTER TABLE products DROP CONSTRAINT products_barcode_key;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'products_business_barcode_key') THEN
        ALTER TABLE products ADD CONSTRAINT products_business_barcode_key UNIQUE (business_id, barcode);
    END IF;
END $$;

-- Rol con el que trabaja la app (sin superusuario, para que se aplique el aislamiento)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stockflow_app') THEN
        CREATE ROLE stockflow_app NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO stockflow_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO stockflow_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO stockflow_app;
DO $$
BEGIN
    -- el usuario de conexión debe poder "convertirse" en stockflow_app
    EXECUTE format('GRANT stockflow_app TO %I', current_user);
EXCEPTION WHEN others THEN NULL;
END $$;
