"""`/api/alimentacion/tablero`: el tablero del mixer (tarjetas, kg por día con alertas, puntaje de operarios) que da el
proveedor de alimentación del tambo, si lo tiene.

Sin red ni base: el proveedor va de mentira.

    C:/Python314/python.exe -m unittest tests.test_alimentacion_tablero -v
"""
import unittest
from unittest import mock

import app
from proveedores import mixerone

TABLERO = {
    "proveedor": "MixerOne",
    "kpis": {"vacas": 1864, "leche_vaca": 41.15, "ms_vaca": 22.82, "conversion": 1.8},
    "viajes": [{"fecha": "2026-11-03", "teorica": 91673.0, "carga_real": 95462.36, "descarga_real": 88255.0,
                "alertas": 0}],
    "operarios": {"puntaje": 8.74, "porcentajes": {"bien": 64.93, "regular": 25.19, "mal": 9.88}, "lista": []},
    "avisos": [],
}


class ProveedorConTablero:
    NOMBRE = "MixerOne"

    def __init__(self, resultado=None, error=None):
        self.resultado, self.error, self.llamadas = resultado or TABLERO, error, 0

    def tablero(self):
        self.llamadas += 1
        if self.error:
            raise self.error
        return self.resultado


class ProveedorSinTablero:
    NOMBRE = "Haasten"


class Base(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        self.addCleanup(app._cache.clear)
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def con_proveedor(self, proveedor):
        p = mock.patch.object(app.proveedores, "de", lambda tambo: proveedor)
        p.start()
        self.addCleanup(p.stop)
        return proveedor


class TestTablero(Base):
    def test_devuelve_lo_del_proveedor(self):
        self.con_proveedor(ProveedorConTablero())
        r = self.client.get("/api/alimentacion/tablero")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["disponible"])
        self.assertEqual(d["kpis"]["conversion"], 1.8)
        self.assertEqual(d["proveedor"], "MixerOne")
        self.assertEqual(d["viajes"][0]["fecha"], "2026-11-03")

    def test_un_proveedor_sin_tablero_lo_dice_sin_error(self):
        self.con_proveedor(ProveedorSinTablero())
        r = self.client.get("/api/alimentacion/tablero")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d["disponible"])
        self.assertEqual(d["proveedor"], "Haasten")
        self.assertIn("Haasten", d["mensaje"])

    def test_si_el_proveedor_falla_devuelve_el_motivo(self):
        self.con_proveedor(ProveedorConTablero(error=mixerone.MixerOneError("MixerOne devolvió HTTP 500")))
        r = self.client.get("/api/alimentacion/tablero")
        self.assertEqual(r.status_code, 502)
        self.assertIn("HTTP 500", r.get_json()["error"])

    def test_el_resultado_se_guarda_un_rato_y_refrescar_lo_vuelve_a_pedir(self):
        prov = self.con_proveedor(ProveedorConTablero())
        self.client.get("/api/alimentacion/tablero")
        self.client.get("/api/alimentacion/tablero")
        self.assertEqual(prov.llamadas, 1)
        self.client.get("/api/alimentacion/tablero?refrescar=1")
        self.assertEqual(prov.llamadas, 2)

    def test_un_error_no_se_guarda(self):
        prov = self.con_proveedor(ProveedorConTablero(error=mixerone.MixerOneError("cortado")))
        self.client.get("/api/alimentacion/tablero")
        self.client.get("/api/alimentacion/tablero")
        self.assertEqual(prov.llamadas, 2)

    def test_cada_tambo_tiene_su_propio_cache(self):
        prov = self.con_proveedor(ProveedorConTablero())
        self.client.get("/api/alimentacion/tablero?tambo=ponderosa")
        self.client.get("/api/alimentacion/tablero?tambo=san_jose")
        self.assertEqual(prov.llamadas, 2)

    def test_solo_el_administrador(self):
        self.con_proveedor(ProveedorConTablero())
        operario = app.app.test_client()
        with operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.assertIn(operario.get("/api/alimentacion/tablero").status_code, (401, 403))
        self.assertIn(app.app.test_client().get("/api/alimentacion/tablero").status_code, (302, 401, 403))


OPERARIOS = {
    "proveedor": "MixerOne", "ventana": {"desde": "2026-11-03", "hasta": "2026-11-18"},
    "operarios": [{"nombre": "Daniel Monteros", "alertas": 267}], "sin_operario": {"total": 75, "por_tipo": {}},
    "alertas": [], "tipos": {}, "primeras_comidas": [], "avisos": [],
    "resumen": {"alertas": 430, "atribuidas": 355, "sin_atribuir": 75, "puntaje": 8.75, "porcentajes": {}},
}


class ProveedorConOperarios(ProveedorConTablero):
    def __init__(self, resultado=None, error=None):
        super().__init__(resultado or OPERARIOS, error)

    def operarios(self):
        return self.tablero()


class TestOperarios(Base):
    """`/api/alimentacion/operarios`: los operarios del mixer con las alertas que detecta el sistema. Misma idea que el
    tablero: opcional en la interfaz del proveedor, caché propio, solo el administrador."""

    def test_devuelve_lo_del_proveedor(self):
        self.con_proveedor(ProveedorConOperarios())
        r = self.client.get("/api/alimentacion/operarios")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d["disponible"])
        self.assertEqual(d["operarios"][0]["nombre"], "Daniel Monteros")
        self.assertEqual(d["resumen"]["alertas"], 430)

    def test_un_proveedor_sin_operarios_lo_dice_sin_error(self):
        self.con_proveedor(ProveedorSinTablero())
        r = self.client.get("/api/alimentacion/operarios")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["disponible"])
        self.assertIn("Haasten", r.get_json()["mensaje"])

    def test_si_el_proveedor_falla_devuelve_el_motivo_y_no_lo_guarda(self):
        prov = self.con_proveedor(ProveedorConOperarios(error=mixerone.MixerOneError("HTTP 500")))
        r = self.client.get("/api/alimentacion/operarios")
        self.assertEqual(r.status_code, 502)
        self.assertIn("HTTP 500", r.get_json()["error"])
        self.client.get("/api/alimentacion/operarios")
        self.assertEqual(prov.llamadas, 2)

    def test_cache_propio_distinto_del_del_tablero(self):
        prov = self.con_proveedor(ProveedorConOperarios())
        self.client.get("/api/alimentacion/tablero")
        self.client.get("/api/alimentacion/operarios")
        self.client.get("/api/alimentacion/operarios")
        self.assertEqual(prov.llamadas, 2)                 # el tablero y los operarios se guardan por separado
        self.client.get("/api/alimentacion/operarios?refrescar=1")
        self.assertEqual(prov.llamadas, 3)

    def test_solo_el_administrador(self):
        self.con_proveedor(ProveedorConOperarios())
        operario = app.app.test_client()
        with operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.assertIn(operario.get("/api/alimentacion/operarios").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
