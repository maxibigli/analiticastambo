"""Dólar cargado en ⚙ Configuración (a mano o traído de DolarHoy) y los botones "Buscar" del precio de la leche y el dólar.

El tipo de cambio configurado manda sobre el de la planilla de precios en todo lo que la planilla convierte de dólares
(insumos en USD/tonelada y la leche en USD/litro); un precio de la leche en pesos cargado a mano sigue mandando sobre todo.

    C:\\Python314\\python.exe -m unittest tests.test_tipo_cambio -v
"""
import os
import tempfile
import unittest
from unittest import mock

import app
import configuracion_tambo
import cotizaciones_externas as ce
import precios_alimentos

app._warmup = lambda *a, **k: None


def _planilla():
    return {"precio_litro": 525.0, "leche_via": "usd", "leche_estimada": True, "leche_usd_litro": 0.35,
            "tipo_cambio": 1500.0, "error": None, "ruta": "x.xlsx",
            "precios": {"maiz": {"nombre": "Maíz", "precio_kg": 300.0, "usd_tn": 200.0, "via": "usd", "estimado": False},
                        "sal": {"nombre": "Sal", "precio_kg": 80.0, "usd_tn": None, "via": "manual", "estimado": False}}}


class Base(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        ce._cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(configuracion_tambo, "_RUTA", os.path.join(tmp.name, "cfg.json"))
        p.start()
        self.addCleanup(p.stop)


class TestConfigTipoCambio(Base):
    def test_sin_cargar_es_none(self):
        c = configuracion_tambo.config_de("t")
        self.assertEqual((c["tipo_cambio"], c["tipo_cambio_origen"], c["precio_leche_origen"]), (None, None, None))

    def test_guarda_con_coma_y_el_origen(self):
        c = configuracion_tambo.guardar("t", {"tipo_cambio": "1.570,5".replace(".", ""), "tipo_cambio_origen": " DolarHoy · oficial "})
        self.assertEqual((c["tipo_cambio"], c["tipo_cambio_origen"]), (1570.5, "DolarHoy · oficial"))

    def test_vacio_vuelve_a_la_planilla(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570})
        for vacio in ("", None):
            configuracion_tambo.guardar("t", {"tipo_cambio": vacio})
            self.assertIsNone(configuracion_tambo.config_de("t")["tipo_cambio"])

    def test_un_dolar_que_no_es_un_dolar_se_rechaza_sin_pisar_lo_guardado(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570})
        for malo in (0, -5, "abc", "nan", "inf"):
            with self.assertRaises(ValueError, msg=repr(malo)):
                configuracion_tambo.guardar("t", {"tipo_cambio": malo})
        self.assertEqual(configuracion_tambo.config_de("t")["tipo_cambio"], 1570.0)

    def test_guardar_otros_campos_no_pierde_el_dolar(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570, "precio_leche_origen": "OCLA SEP/2026"})
        configuracion_tambo.guardar("t", {"personas": 4})
        c = configuracion_tambo.config_de("t")
        self.assertEqual((c["tipo_cambio"], c["precio_leche_origen"]), (1570.0, "OCLA SEP/2026"))


class TestAplicarTipoCambio(unittest.TestCase):
    def test_recalcula_lo_que_viene_en_dolares(self):
        r = precios_alimentos.aplicar_tipo_cambio(_planilla(), 1570.0)
        self.assertEqual(r["tipo_cambio"], 1570.0)
        self.assertAlmostEqual(r["precios"]["maiz"]["precio_kg"], 314.0)          # 200 USD/tn x 1570 / 1000
        self.assertAlmostEqual(r["precio_litro"], 549.5)                           # 0,35 USD/litro x 1570

    def test_lo_cargado_a_mano_en_pesos_no_se_toca(self):
        r = precios_alimentos.aplicar_tipo_cambio(_planilla(), 1570.0)
        self.assertEqual(r["precios"]["sal"]["precio_kg"], 80.0)
        p = _planilla()
        p["leche_via"], p["precio_litro"] = "manual", 600.0
        self.assertEqual(precios_alimentos.aplicar_tipo_cambio(p, 1570.0)["precio_litro"], 600.0)

    def test_no_modifica_el_dato_cacheado_de_la_planilla(self):
        original = _planilla()
        precios_alimentos.aplicar_tipo_cambio(original, 1570.0)
        self.assertEqual(original, _planilla())

    def test_sin_dolar_configurado_devuelve_la_planilla_tal_cual(self):
        p = _planilla()
        for nada in (None, 0, ""):
            self.assertIs(precios_alimentos.aplicar_tipo_cambio(p, nada), p)

    def test_planilla_sin_dolar_pero_con_leche_en_usd_igual_calcula_la_leche(self):
        p = _planilla()
        p.update(tipo_cambio=None, precio_litro=None, leche_via=None)
        r = precios_alimentos.aplicar_tipo_cambio(p, 1570.0)
        self.assertAlmostEqual(r["precio_litro"], 549.5)
        self.assertEqual(r["leche_via"], "usd")


class TestPreciosDelTambo(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: _planilla())
        p.start()
        self.addCleanup(p.stop)

    def test_el_dolar_configurado_manda_sobre_el_de_la_planilla(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570})
        r = app._precios_del_tambo("t")
        self.assertEqual(r["tipo_cambio"], 1570.0)
        self.assertAlmostEqual(r["precios"]["maiz"]["precio_kg"], 314.0)
        self.assertAlmostEqual(r["precio_litro"], 549.5)

    def test_un_precio_de_leche_en_pesos_manda_sobre_dolar_x_usd(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570, "precio_leche": 543.01})
        r = app._precios_del_tambo("t")
        self.assertEqual((r["precio_litro"], r["leche_via"]), (543.01, "configuracion"))
        self.assertAlmostEqual(r["precios"]["maiz"]["precio_kg"], 314.0)           # los insumos siguen con el dólar nuevo

    def test_sin_nada_configurado_es_la_planilla(self):
        r = app._precios_del_tambo("t")
        self.assertEqual((r["tipo_cambio"], r["precio_litro"]), (1500.0, 525.0))

    def test_planilla_ilegible_con_dolar_configurado_no_corta_la_leche_en_pesos(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570, "precio_leche": 543.01})
        with mock.patch.object(app.precios_alimentos, "leer", side_effect=RuntimeError("abierto en Excel")):
            self.assertEqual(app._precios_del_tambo("t")["precio_litro"], 543.01)


class TestOrigenEnElBalance(Base):
    """El Balance dice de dónde salió el precio con el que valúa la diferencia de litros."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: _planilla())
        p.start()
        self.addCleanup(p.stop)

    def test_precio_en_pesos_cargado_trae_su_origen(self):
        configuracion_tambo.guardar("t", {"precio_leche": 543.01, "precio_leche_origen": "OCLA SEP/2026"})
        p = app._precio_leche_balance("t")
        self.assertEqual((p["valor"], p["via"], p["origen"]), (543.01, "configuracion", "OCLA SEP/2026"))

    def test_leche_en_dolares_trae_el_origen_del_dolar(self):
        configuracion_tambo.guardar("t", {"tipo_cambio": 1570, "tipo_cambio_origen": "DolarHoy · oficial (venta)"})
        p = app._precio_leche_balance("t")
        self.assertEqual((p["via"], p["tipo_cambio"], p["origen"]), ("usd", 1570.0, "DolarHoy · oficial (venta)"))

    def test_sin_nada_configurado_no_hay_origen(self):
        self.assertIsNone(app._precio_leche_balance("t")["origen"])


class TestApiConfiguracion(Base):
    def setUp(self):
        super().setUp()
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_el_dolar_viaja_por_la_api_de_configuracion(self):
        r = self.client.post("/api/configuracion?tambo=lamartina_local",
                             json={"tipo_cambio": "1570", "tipo_cambio_origen": "DolarHoy · oficial (venta)"})
        self.assertEqual(r.status_code, 200)
        c = self.client.get("/api/configuracion?tambo=lamartina_local").get_json()["config"]
        self.assertEqual((c["tipo_cambio"], c["tipo_cambio_origen"]), (1570.0, "DolarHoy · oficial (venta)"))

    def test_cambiar_el_dolar_tira_lo_calculado_con_el_dolar_viejo(self):
        app._cache["lamartina_local:__costo_mixer__a_b"] = (0, {"x": 1})
        app._cache["otro_tambo:__costo_mixer__a_b"] = (0, {"x": 1})
        self.client.post("/api/configuracion?tambo=lamartina_local", json={"tipo_cambio": "1570"})
        self.assertNotIn("lamartina_local:__costo_mixer__a_b", app._cache)
        self.assertIn("otro_tambo:__costo_mixer__a_b", app._cache)

    def test_guardar_el_mismo_dolar_no_borra_el_cache(self):
        configuracion_tambo.guardar("lamartina_local", {"tipo_cambio": 1570})
        app._cache["lamartina_local:__costo_mixer__a_b"] = (0, {"x": 1})
        self.client.post("/api/configuracion?tambo=lamartina_local", json={"tipo_cambio": "1570"})
        self.assertIn("lamartina_local:__costo_mixer__a_b", app._cache)

    def test_el_estado_de_la_planilla_muestra_lo_que_rige_no_lo_que_dice_el_excel(self):
        """La línea «leche $x/litro · TC $y» de Configuración tiene que coincidir con lo que usa el resto de la app."""
        configuracion_tambo.guardar("lamartina_local", {"tipo_cambio": 1570, "precio_leche": 543.01})
        with mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: _planilla()):
            p = self.client.get("/api/configuracion?tambo=lamartina_local").get_json()["precios"]
        self.assertEqual((p["precio_litro"], p["tipo_cambio"], p["leche_via"]), (543.01, 1570.0, "configuracion"))
        self.assertFalse(p["leche_estimada"])               # lo cargó el tambo: ya no es el valor de ejemplo

    def test_sin_nada_configurado_el_estado_es_el_de_la_planilla(self):
        with mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: _planilla()):
            p = self.client.get("/api/configuracion?tambo=lamartina_local").get_json()["precios"]
        self.assertEqual((p["precio_litro"], p["tipo_cambio"], p["leche_estimada"]), (525.0, 1500.0, True))

    def test_dolar_invalido_es_400(self):
        r = self.client.post("/api/configuracion?tambo=lamartina_local", json={"tipo_cambio": "-3"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("tipo_cambio", r.get_json()["error"])


class TestApiBuscar(Base):
    def setUp(self):
        super().setUp()
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_buscar_el_dolar(self):
        res = {"valor": 1570.0, "compra": 1520.0, "tipo": "oficial", "tipo_rotulo": "Dólar oficial (venta)",
               "fuente": "DolarHoy", "actualizado": "09/11/26 01:03 AM", "aviso": ""}
        with mock.patch.object(ce, "dolar", return_value=res) as f:
            r = self.client.get("/api/cotizaciones/dolar?tipo=mep")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["valor"], 1570.0)
        f.assert_called_once_with("mep")

    def test_buscar_la_leche(self):
        res = {"valor": 543.01, "periodo": "2026-09", "etiqueta": "SEP/2026", "fuente": "OCLA", "aviso": "",
               "antiguedad_meses": 2}
        with mock.patch.object(ce, "precio_leche", return_value=res):
            r = self.client.get("/api/cotizaciones/leche")
        self.assertEqual((r.status_code, r.get_json()["valor"], r.get_json()["etiqueta"]), (200, 543.01, "SEP/2026"))

    def test_si_la_fuente_falla_es_un_502_con_el_motivo(self):
        with mock.patch.object(ce, "dolar", side_effect=ce.CotizacionError("No se pudo traer el dólar. Cargalo a mano.")):
            r = self.client.get("/api/cotizaciones/dolar")
        self.assertEqual(r.status_code, 502)
        self.assertIn("a mano", r.get_json()["error"])

    def test_tipo_de_dolar_desconocido_es_400(self):
        r = self.client.get("/api/cotizaciones/dolar?tipo=cripto")
        self.assertEqual(r.status_code, 400)

    def test_sin_sesion_no_entra(self):
        self.assertEqual(app.app.test_client().get("/api/cotizaciones/leche").status_code, 401)


if __name__ == "__main__":
    unittest.main()
