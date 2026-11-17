"""P01 · Autenticación y sesión (Dominio D03/D04 · OWASP A07)."""
from werkzeug.security import generate_password_hash

from conftest import PASSWORD, SUPERADMIN, login, query


def test_p01_01_login_correcto_redirige_al_inicio_del_rol(client, stores):
    r = login(client, "cajero_a")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/cajero")


def test_p01_02_login_con_clave_incorrecta_es_rechazado(client, stores):
    r = login(client, "cajero_a", "clave-equivocada")
    assert r.status_code == 401
    assert "Usuario o contraseña incorrectos" in r.get_data(as_text=True)


def test_p01_03_mensaje_identico_si_el_usuario_no_existe(client, stores):
    """No se revela si un usuario existe (enumeración de usuarios)."""
    r1 = login(client, "no_existe_xyz", "x")
    r2 = login(client, "cajero_a", "x")
    assert r1.status_code == r2.status_code == 401
    assert ("Usuario o contraseña incorrectos" in r1.get_data(as_text=True)
            and "Usuario o contraseña incorrectos" in r2.get_data(as_text=True))


def test_p01_04_campos_vacios(client):
    assert login(client, "", "").status_code == 400


def test_p01_05_usuario_desactivado_no_ingresa(client, stores):
    query("UPDATE users SET active = FALSE WHERE username = 'bodega_b'")
    try:
        assert login(client, "bodega_b").status_code == 403
    finally:
        query("UPDATE users SET active = TRUE WHERE username = 'bodega_b'")


def test_p01_06_tienda_desactivada_bloquea_a_sus_usuarios(client, stores):
    bid = stores["b"]["business_id"]
    query("UPDATE businesses SET is_active = FALSE WHERE id = %s", (bid,))
    try:
        assert login(client, "cajero_b").status_code == 403
    finally:
        query("UPDATE businesses SET is_active = TRUE WHERE id = %s", (bid,))


def test_p01_07_contrasenas_se_guardan_con_hash(stores):
    rows = query("SELECT username, password FROM users")
    assert rows and all(r["password"].startswith(("scrypt:", "pbkdf2:")) for r in rows)


def test_p01_08_clave_antigua_en_texto_plano_se_migra_a_hash(client, stores):
    query("""INSERT INTO users (username, password, role, business_id)
             VALUES ('legado_a', 'Plano-123', 'cajero', %s)""", (stores["a"]["business_id"],))
    try:
        assert login(client, "legado_a", "Plano-123").status_code == 302
        stored = query("SELECT password FROM users WHERE username='legado_a'")[0]["password"]
        assert stored.startswith(("scrypt:", "pbkdf2:"))
    finally:
        query("DELETE FROM users WHERE username='legado_a'")


def test_p01_09_redireccion_abierta_bloqueada(client, stores):
    """?next=//sitio-externo no debe sacar al usuario de la aplicación."""
    r = login(client, "cajero_a", next_url="//evil.example.com/robar")
    assert r.status_code == 302
    assert "evil.example.com" not in r.headers["Location"]


def test_p01_10_cookie_de_sesion_httponly_y_samesite(client, stores):
    r = login(client, "cajero_a")
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_p01_11_logout_cierra_la_sesion(client, stores):
    login(client, "cajero_a")
    client.get("/logout")
    r = client.get("/cajero")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_p01_12_cambiar_contrasena_exige_la_actual(client, stores):
    login(client, "bodega_a")
    r = client.post("/mi-cuenta/password", data={"current_password": "mala", "new_password": "Nueva-1234",
                                                 "confirm_password": "Nueva-1234"}, follow_redirects=True)
    assert "no es correcta" in r.get_data(as_text=True)
    assert login(client.application.test_client(), "bodega_a").status_code == 302


def test_p01_13_politica_minima_de_longitud(client, stores):
    login(client, "bodega_a")
    r = client.post("/mi-cuenta/password", data={"current_password": PASSWORD, "new_password": "123",
                                                 "confirm_password": "123"}, follow_redirects=True)
    assert "al menos 6" in r.get_data(as_text=True)


def test_p01_14_superadmin_creado_por_migracion(stores):
    row = query("SELECT role, business_id FROM users WHERE username = %s", (SUPERADMIN[0],))[0]
    assert row["role"] == "superadmin" and row["business_id"] is None
    _ = generate_password_hash  # evita import sin uso en linters
