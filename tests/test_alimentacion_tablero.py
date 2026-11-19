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


def _rodeos_de_prueba():
    import entrega
    return entrega.por_rodeo([
        {"fecha": f"2026-11-1{i}", "minuto": 424, "rodeo": "RODEO 1", "kg_real": 1000.0, "kg_teorico": 1000.0}
        for i in range(0, 4)] + [
        {"fecha": "2026-11-12", "minuto": 424, "rodeo": "RODEO 2", "kg_real": 500.0, "kg_teorico": 500.0}])


class ProveedorConEntrega(ProveedorConTablero):
    def __init__(self, error=None):
        super().__init__({}, error)

    def entregas_por_rodeo(self):
        self.llamadas += 1
        if self.error:
            raise self.error
        return {"proveedor": "MixerOne", "ventana": {"desde": "2026-11-10", "hasta": "2026-11-13"},
                "rodeos": _rodeos_de_prueba(), "avisos": []}


class TestEntregaVsProduccion(Base):
    """`/api/alimentacion/entrega-vs-produccion`: la entrega de comida de cada rodeo (del proveedor) al lado de la leche por
    vaca de ese rodeo (DelPro), cruzadas con el mapeo de Conciliación de grupos."""

    DIAS = {"columns": ["fecha", "animales"], "rows": [["2026-11-13", 800], ["2026-11-12", 810], ["2026-11-11", 805],
                                                        ["2026-11-10", 800]]}
    # fecha, grupo, nombre, vacas, kg_leche
    PROD = {"columns": ["fecha", "grupo", "nombre", "vacas", "kg_leche"], "rows": [
        ["2026-11-10", 101, "Rodeo 1", 400, 19600.0], ["2026-11-11", 101, "Rodeo 1", 400, 20000.0],
        ["2026-11-12", 101, "Rodeo 1", 400, 20400.0], ["2026-11-13", 101, "Rodeo 1", 120, 1000.0],
        ["2026-11-12", 102, "Rodeo 2", 300, 12000.0], ["2026-11-12", 103, "Rodeo 2b", 100, 4000.0]]}

    def setUp(self):
        super().setUp()
        self.mapeo = {101: "Rodeo 1", 102: "Rodeo 2", 103: "Rodeo 2"}      # el guardado, con los nombres de Haasten
        self.sql = []

        def run_query(sql, tambo=None, max_rows=None):
            self.sql.append(sql)
            if "AS animales" in sql:
                return self.DIAS
            if "AS kg_leche" in sql:
                return self.PROD
            raise AssertionError("consulta inesperada")
        for p in (mock.patch.object(app.conciliacion, "lote_de_grupo", lambda tambo: self.mapeo),
                  mock.patch.object(app.db, "run_query", run_query)):
            p.start()
            self.addCleanup(p.stop)

    def get(self, extra=""):
        return self.client.get("/api/alimentacion/entrega-vs-produccion" + extra)

    def dias(self, d, rodeo):
        return {x["fecha"]: x for x in next(r for r in d["rodeos"] if r["rodeo"] == rodeo)["dias"]}

    def test_pega_la_leche_por_vaca_de_cada_rodeo_aunque_el_mapeo_tenga_otros_nombres(self):
        self.con_proveedor(ProveedorConEntrega())
        d = self.get().get_json()
        self.assertTrue(d["disponible"])
        r1 = self.dias(d, "RODEO 1")
        self.assertEqual((r1["2026-11-10"]["leche_vaca"], r1["2026-11-10"]["vacas"]), (49.0, 400))
        self.assertEqual(r1["2026-11-10"]["cumplimiento"], 100.0)

    def test_varios_grupos_de_un_mismo_rodeo_se_suman(self):
        self.con_proveedor(ProveedorConEntrega())
        r2 = self.dias(self.get().get_json(), "RODEO 2")["2026-11-12"]
        self.assertEqual((r2["vacas"], r2["leche_vaca"]), (400, 40.0))          # (12.000 + 4.000) / (300 + 100)

    def test_el_ultimo_dia_a_medio_cargar_no_lleva_leche(self):
        self.con_proveedor(ProveedorConEntrega())
        d = self.get().get_json()
        self.assertEqual(d["ultimo_dia_completo"], "2026-11-13")                # 800 de 810 vacas: completo
        self.DIAS = {"columns": ["fecha", "animales"], "rows": [["2026-11-13", 90], ["2026-11-12", 810],
                                                                ["2026-11-11", 805], ["2026-11-10", 800]]}
        app._cache.clear()
        d = self.get().get_json()
        self.assertEqual(d["ultimo_dia_completo"], "2026-11-12")
        self.assertIsNone(self.dias(d, "RODEO 1")["2026-11-13"]["leche_vaca"])

    def test_sin_mapeo_la_entrega_sale_sin_leche_y_manda_a_conciliacion(self):
        self.mapeo = {}
        self.con_proveedor(ProveedorConEntrega())
        d = self.get().get_json()
        self.assertTrue(d["rodeos"])
        self.assertFalse(d["rodeos"][0]["con_leche"])
        self.assertTrue(any("Conciliación" in a for a in d["avisos"]), d["avisos"])
        self.assertEqual(self.sql, [])                      # sin mapeo ni se consulta la base

    def test_si_la_base_falla_la_entrega_sale_igual_y_no_se_guarda(self):
        prov = self.con_proveedor(ProveedorConEntrega())

        def falla(*a, **k):
            raise RuntimeError("timeout")
        with mock.patch.object(app.db, "run_query", falla):
            r = self.get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(any("DelPro" in a for a in d["avisos"]), d["avisos"])
        self.assertTrue(d["rodeos"])
        self.get()
        self.assertEqual(prov.llamadas, 2)                  # un fallo de la base no queda guardado diez minutos

    def test_un_proveedor_sin_entrega_lo_dice_sin_error(self):
        self.con_proveedor(ProveedorSinTablero())
        r = self.get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d["disponible"])
        self.assertIn("Haasten", d["mensaje"])

    def test_si_el_proveedor_falla_devuelve_el_motivo(self):
        self.con_proveedor(ProveedorConEntrega(error=mixerone.MixerOneError("HTTP 500")))
        r = self.get()
        self.assertEqual(r.status_code, 502)
        self.assertIn("HTTP 500", r.get_json()["error"])

    def test_se_guarda_y_refrescar_lo_vuelve_a_pedir(self):
        prov = self.con_proveedor(ProveedorConEntrega())
        self.get()
        self.get()
        self.assertEqual(prov.llamadas, 1)
        self.get("?refrescar=1")
        self.assertEqual(prov.llamadas, 2)

    def test_solo_el_administrador(self):
        self.con_proveedor(ProveedorConEntrega())
        operario = app.app.test_client()
        with operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.assertIn(operario.get("/api/alimentacion/entrega-vs-produccion").status_code, (401, 403))


class TestCopiaDeAlertas(Base):
    """`/api/alimentacion/alertas-copia`: qué hay copiado de las alertas del mixer (ver `alertas_guardadas.py`)."""

    def setUp(self):
        super().setUp()
        import os
        import tempfile
        import alertas_guardadas
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(alertas_guardadas, "RUTA_DB", os.path.join(self.dir.name, "a.db"))
        p.start()
        self.addCleanup(p.stop)
        self.ag = alertas_guardadas

    def test_sin_nada_copiado_dice_cero(self):
        d = self.client.get("/api/alimentacion/alertas-copia").get_json()
        self.assertEqual((d["total"], d["desde"], d["ultima_sincronizacion"]), (0, None, None))

    def test_dice_cuantas_hay_desde_cuando_y_la_ultima_sincronizacion(self):
        import datetime

        class Prov:
            NOMBRE = "MixerOne"

            def disponible(self):
                return True, ""

            def alertas(self, desde, hasta, crudo=False):
                return [{"id": "1", "codigo": "N07", "fecha": "2026-11-04", "hora": "10:00", "crudo": {}}]
        self.ag.sincronizar("ponderosa", Prov(), datetime.date(2026, 11, 18), datetime.datetime(2026, 11, 18, 9, 0))
        d = self.client.get("/api/alimentacion/alertas-copia?tambo=ponderosa").get_json()
        self.assertEqual((d["total"], d["desde"]), (1, "2026-11-04"))
        self.assertEqual(d["ultima_sincronizacion"]["nuevas"], 1)

    def test_cada_tambo_ve_lo_suyo(self):
        self.ag.guardar("san_jose", [{"id": "1", "codigo": "N07", "fecha": "2026-11-04", "hora": "1", "crudo": {}}])
        self.assertEqual(self.client.get("/api/alimentacion/alertas-copia?tambo=ponderosa").get_json()["total"], 0)

    def test_solo_el_administrador(self):
        operario = app.app.test_client()
        with operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.assertIn(operario.get("/api/alimentacion/alertas-copia").status_code, (401, 403))

    def test_el_hilo_de_la_copia_no_arranca_al_importar_app(self):
        """Lo arranca `servidor.py` con el candado de instancia única: si arrancara al importar, cada proceso que
        importa `app` (precalentar, los diagnósticos, las pruebas) tendría su propio hilo."""
        import threading
        self.assertNotIn("copia-alertas", [t.name for t in threading.enumerate()])
        self.assertTrue(callable(app.iniciar_copia_alertas))


if __name__ == "__main__":
    unittest.main()
