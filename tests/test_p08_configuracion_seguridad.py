"""P08 · Recuperación de contraseña y configuración de seguridad (OWASP A02, A05, A07).

Las pruebas marcadas xfail(strict=True) documentan HALLAZGOS abiertos: comprueban
el comportamiento seguro esperado y hoy fallan. Cuando se corrija el hallazgo,
la prueba pasará y pytest avisará (XPASS estricto) para quitar la marca.
"""
import os
import re

import pytest

from conftest import ROOT, login, query


def _token_de(sent):
    m = re.search(r"/reset-password/(\S+)", sent[-1]["body"])
    return m.group(1)


def test_p08_01_recuperacion_no_revela_si_el_usuario_existe(client, stores, no_side_effects):
    r1 = client.post("/forgot-password", data={"identifier": "no_existe_abc"}, follow_redirects=True)
    r2 = client.post("/forgot-password", data={"identifier": "cajero_a"}, follow_redirects=True)
    assert r1.status_code == r2.status_code == 200
    assert "Si el usuario existe" in r1.get_data(as_text=True) and "Si el usuario existe" in r2.get_data(as_text=True)


def test_p08_02_token_alterado_es_rechazado(client, stores, no_side_effects):
    client.post("/forgot-password", data={"identifier": "cajero_a"})
    token = _token_de(no_side_effects)
    r = client.get(f"/reset-password/{token[:-3]}abc", follow_redirects=True)
    assert "no es válido" in r.get_data(as_text=True)


def test_p08_03_token_de_un_solo_uso(client, stores, no_side_effects):
    client.post("/forgot-password", data={"identifier": "bodega_b"})
    token = _token_de(no_side_effects)
    r = client.post(f"/reset-password/{token}", data={"new_password": "Nueva-Clave-1",
                                                      "confirm_password": "Nueva-Clave-1"})
    assert r.status_code == 302
    r2 = client.get(f"/reset-password/{token}", follow_redirects=True)
    assert "ya fue usado" in r2.get_data(as_text=True)
    query("UPDATE users SET password=(SELECT password FROM users WHERE username='bodega_a') WHERE username='bodega_b'")


def test_p08_04_paginas_de_error_no_exponen_trazas(client):
    r = client.get("/ruta-que-no-existe")
    assert r.status_code == 404 and "Traceback" not in r.get_data(as_text=True)


def test_p08_05_env_excluido_del_control_de_versiones():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh]
    assert ".env" in lines


def test_p08_06_sin_secretos_en_el_codigo_fuente():
    patron = re.compile(r"(prv_prod_[A-Za-z0-9]+|pub_prod_[A-Za-z0-9]+|prod_events_[A-Za-z0-9]+|"
                        r"prod_integrity_[A-Za-z0-9]+|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")
    hallados = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "venv", "__pycache__", "_respaldo_original",
                                                "node_modules", ".pytest_cache")]
        for f in files:
            if f.endswith((".py", ".html", ".js", ".sql", ".md", ".txt", ".example")):
                with open(os.path.join(base, f), encoding="utf-8", errors="ignore") as fh:
                    if patron.search(fh.read()):
                        hallados.append(f)
    assert hallados == []


# ---------------------------- hallazgos abiertos ----------------------------

@pytest.mark.xfail(strict=True, reason="Hallazgo H-01: los formularios POST no tienen token CSRF")
def test_p08_10_formularios_con_token_csrf(app, stores):
    c = app.test_client()
    login(c, "admin_a")
    html = c.get("/admin/usuarios").get_data(as_text=True)
    assert re.search(r'name="csrf_token"', html)


@pytest.mark.xfail(strict=True, reason="Hallazgo H-02: sin límite de intentos de inicio de sesión (fuerza bruta)")
def test_p08_11_bloqueo_tras_intentos_fallidos(client, stores):
    for _ in range(10):
        login(client, "cajero_a", "mala")
    assert login(client, "cajero_a").status_code in (403, 429)


@pytest.mark.xfail(strict=True, reason="Hallazgo H-03: el enlace de recuperación usa la cabecera Host (envenenamiento)")
def test_p08_12_enlace_de_recuperacion_no_usa_host_del_atacante(client, stores, no_side_effects):
    client.post("/forgot-password", data={"identifier": "cajero_a"},
                headers={"Host": "atacante.example.com"})
    assert "atacante.example.com" not in no_side_effects[-1]["body"]


@pytest.mark.xfail(strict=True, reason="Hallazgo H-04: faltan cabeceras de seguridad HTTP (CSP, X-Frame-Options, nosniff)")
def test_p08_13_cabeceras_de_seguridad(client):
    r = client.get("/login")
    for h in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"):
        assert h in r.headers


@pytest.mark.xfail(strict=True, reason="Hallazgo H-05: FLASK_DEBUG=true por defecto y host 0.0.0.0 (consola Werkzeug en la red local)")
def test_p08_14_debug_desactivado_por_defecto():
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert 'os.getenv("FLASK_DEBUG", "true")' not in src


@pytest.mark.xfail(strict=True, reason="Hallazgo H-06: los mensajes de error muestran la excepción interna al usuario")
def test_p08_15_errores_internos_no_se_muestran(app, stores, monkeypatch):
    from app.routes import admin_routes
    c = app.test_client()
    login(c, "admin_a")

    class Boom:
        def cursor(self):
            raise RuntimeError("detalle-interno-de-la-bd")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(admin_routes, "get_db", lambda *a, **k: Boom())
    html = c.post("/admin/usuarios/crear", data={"username": "nuevo_x", "password": "Clave-123456",
                                                 "role": "cajero"}, follow_redirects=False)
    monkeypatch.undo()
    page = c.get("/admin/usuarios").get_data(as_text=True)
    assert html.status_code == 302 and "detalle-interno-de-la-bd" not in page


@pytest.mark.xfail(strict=True, reason="Hallazgo H-08: se aceptan claves en texto plano heredadas (compatibilidad)")
def test_p08_16_no_se_aceptan_claves_en_texto_plano():
    from app.routes.auth_routes import verify_password
    assert verify_password("ClavePlana1", "ClavePlana1") is False
