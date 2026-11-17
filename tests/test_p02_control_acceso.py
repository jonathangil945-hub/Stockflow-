"""P02 · Control de acceso por rol (Dominio D03/D04 · OWASP A01)."""
import pytest

from conftest import SUPERADMIN, login

# (ruta, roles que SÍ pueden entrar). admin siempre puede entrar a su tienda.
RUTAS = [
    ("/cajero", {"cajero", "admin"}),
    ("/bodega", {"bodega", "admin"}),
    ("/etiquetas", {"bodega", "cajero", "admin"}),
    ("/reportes/", {"admin"}),
    ("/reportes/api/reportes", {"admin"}),
    ("/admin/usuarios", {"admin"}),
    ("/superadmin/tiendas", {"superadmin"}),
]
USUARIOS = {"cajero": "cajero_a", "bodega": "bodega_a", "admin": "admin_a"}


@pytest.mark.parametrize("ruta,_permitidos", RUTAS)
def test_p02_01_sin_sesion_redirige_a_login(client, ruta, _permitidos):
    r = client.get(ruta)
    assert r.status_code in (302, 401)
    if r.status_code == 302:
        assert "/login" in r.headers["Location"]


@pytest.mark.parametrize("rol", ["cajero", "bodega", "admin"])
@pytest.mark.parametrize("ruta,permitidos", RUTAS)
def test_p02_02_matriz_rol_ruta(app, stores, rol, ruta, permitidos):
    c = app.test_client()
    assert login(c, USUARIOS[rol]).status_code == 302
    r = c.get(ruta)
    if rol in permitidos:
        assert r.status_code == 200, f"{rol} debería entrar a {ruta}"
    else:
        assert r.status_code in (302, 403), f"{rol} NO debería entrar a {ruta}"


@pytest.mark.parametrize("api", ["/cajero/api/product-by-barcode?barcode=X",
                                 "/bodega/api/product-by-barcode?barcode=X"])
def test_p02_03_api_sin_sesion_responde_401_json(client, api):
    r = client.get(api)
    assert r.status_code == 401
    assert r.is_json and r.get_json()["ok"] is False


def test_p02_04_api_de_bodega_prohibida_al_cajero(client, stores):
    login(client, "cajero_a")
    r = client.get("/bodega/api/product-by-barcode?barcode=X")
    assert r.status_code == 403 and r.is_json


def test_p02_05_admin_no_puede_crear_superadmin(client, stores):
    login(client, "admin_a")
    client.post("/admin/usuarios/crear", data={"username": "falso_super", "password": "Clave-123456",
                                               "role": "superadmin"})
    from conftest import query
    assert not query("SELECT 1 FROM users WHERE username='falso_super'")


def test_p02_06_superadmin_debe_entrar_a_una_tienda_antes_de_operar(client, stores):
    login(client, *SUPERADMIN)
    assert client.get("/cajero").status_code == 302
    client.get(f"/superadmin/tiendas/{stores['a']['business_id']}/entrar")
    assert client.get("/cajero").status_code == 200


def test_p02_07_admin_no_puede_auto_degradarse(client, stores):
    from conftest import query
    login(client, "admin_a")
    uid = query("SELECT id FROM users WHERE username='admin_a'")[0]["id"]
    client.post(f"/admin/usuarios/{uid}/editar", data={"role": "cajero", "active": "on"})
    assert query("SELECT role FROM users WHERE id=%s", (uid,))[0]["role"] == "admin"
