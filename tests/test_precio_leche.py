"""Precio de la leche cargado en ⚙ Configuración (por tambo, en $ por litro).

Manda sobre el de la planilla de precios (que suele venir marcado ESTIMADO) en todo lo que valúa leche:
Balance de leche, Alimentación, Rentabilidad y el informe de RCS. Vacío = se usa la planilla, como antes.
Más adelante podría salir de una API; mientras tanto lo carga el tambo.

    C:\\Python314\\python.exe -m unittest discover -s tests -v
"""
import inspect
import os
import tempfile
import unittest
from unittest import mock

import app
import configuracion_tambo
import precios_alimentos

app._warmup = lambda *a, **k: None   # el calentamiento de caché consultaría la base real

PLANILLA = {"precio_litro": 525.0, "leche_via": "usd", "leche_estimada": True, "leche_usd_litro": 0.35,
            "tipo_cambio": 1500.0, "precios": {"maiz": 1}, "ruta": "x.xlsx", "error": None}


class Base(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(configuracion_tambo, "_RUTA", os.path.join(tmp.name, "cfg.json"))
        p.start()
        self.addCleanup(p.stop)


class TestConfigPrecioLeche(Base):
    def test_sin_cargar_es_none(self):
        self.assertIsNone(configuracion_tambo.config_de("t")["precio_leche"])

    def test_guarda_con_coma_o_punto(self):
        self.assertEqual(configuracion_tambo.guardar("t", {"precio_leche": "612,5"})["precio_leche"], 612.5)
        self.assertEqual(configuracion_tambo.guardar("t", {"precio_leche": 700})["precio_leche"], 700.0)
        self.assertEqual(configuracion_tambo.config_de("t")["precio_leche"], 700.0)

    def test_vacio_vuelve_a_usar_la_planilla(self):
        configuracion_tambo.guardar("t", {"precio_leche": 700})
        for vacio in ("", None):
            configuracion_tambo.guardar("t", {"precio_leche": vacio})
            self.assertIsNone(configuracion_tambo.config_de("t")["precio_leche"])

    def test_un_precio_que_no_es_precio_se_rechaza_sin_pisar_lo_guardado(self):
        configuracion_tambo.guardar("t", {"precio_leche": 700})
        for malo in (0, -5, "abc", "nan", "inf", "1.250,50"):
            with self.assertRaises(ValueError, msg=repr(malo)):
                configuracion_tambo.guardar("t", {"precio_leche": malo})
        self.assertEqual(configuracion_tambo.config_de("t")["precio_leche"], 700.0)

    def test_guardar_otros_campos_no_pierde_el_precio(self):
        configuracion_tambo.guardar("t", {"precio_leche": 700})
        configuracion_tambo.guardar("t", {"personas": 4})
        self.assertEqual(configuracion_tambo.config_de("t")["precio_leche"], 700.0)


class TestAplicarPrecioLeche(unittest.TestCase):
    def test_el_precio_configurado_manda_y_ya_no_es_estimado(self):
        r = precios_alimentos.aplicar_precio_leche(PLANILLA, 700.0)
        self.assertEqual((r["precio_litro"], r["leche_via"], r["leche_estimada"], r["leche_usd_litro"]),
                         (700.0, "configuracion", False, None))
        self.assertEqual(r["precios"], PLANILLA["precios"])          # los ingredientes no se tocan
        self.assertEqual(r["tipo_cambio"], 1500.0)

    def test_no_modifica_el_dato_cacheado_de_la_planilla(self):
        original = dict(PLANILLA)
        precios_alimentos.aplicar_precio_leche(PLANILLA, 700.0)
        self.assertEqual(PLANILLA, original)

    def test_sin_precio_configurado_devuelve_la_planilla_tal_cual(self):
        for nada in (None, 0, ""):
            self.assertIs(precios_alimentos.aplicar_precio_leche(PLANILLA, nada), PLANILLA)

    def test_sin_planilla_el_precio_configurado_igual_vale(self):
        r = precios_alimentos.aplicar_precio_leche({"error": "No se encontró", "precios": {}, "precio_litro": None}, 700.0)
        self.assertEqual(r["precio_litro"], 700.0)
        self.assertEqual(r["precios"], {})


class TestPreciosDelTambo(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: PLANILLA)
        p.start()
        self.addCleanup(p.stop)

    def test_sin_configurar_es_la_planilla(self):
        self.assertEqual(app._precios_del_tambo("t")["precio_litro"], 525.0)
        self.assertIs(app._precios_del_tambo("t")["leche_estimada"], True)

    def test_configurado_manda_sobre_la_planilla(self):
        configuracion_tambo.guardar("t", {"precio_leche": 700})
        r = app._precios_del_tambo("t")
        self.assertEqual((r["precio_litro"], r["leche_via"], r["leche_estimada"]), (700.0, "configuracion", False))

    def test_otro_tambo_no_hereda_el_precio(self):
        configuracion_tambo.guardar("a", {"precio_leche": 700})
        self.assertEqual(app._precios_del_tambo("b")["precio_litro"], 525.0)

    def test_planilla_ilegible_con_precio_configurado_no_corta(self):
        configuracion_tambo.guardar("t", {"precio_leche": 700})
        with mock.patch.object(app.precios_alimentos, "leer", side_effect=RuntimeError("abierto en Excel")):
            self.assertEqual(app._precios_del_tambo("t")["precio_litro"], 700.0)

    def test_planilla_ilegible_sin_precio_configurado_lanza_como_antes(self):
        with mock.patch.object(app.precios_alimentos, "leer", side_effect=RuntimeError("abierto en Excel")):
            with self.assertRaises(RuntimeError):
                app._precios_del_tambo("t")

    def test_todo_lo_que_valua_leche_pasa_por_el_mismo_lugar(self):
        """Si alguien vuelve a leer la planilla directo, ese lugar ignoraría el precio de Configuración."""
        self.assertEqual(inspect.getsource(app).count("precios_alimentos.leer("), 1)


class TestConfiguracionApi(Base):
    def setUp(self):
        super().setUp()
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_el_precio_viaja_por_la_api_de_configuracion(self):
        r = self.client.post("/api/configuracion?tambo=lamartina_local", json={"precio_leche": "612,5"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["config"]["precio_leche"], 612.5)
        r = self.client.get("/api/configuracion?tambo=lamartina_local")
        self.assertEqual(r.get_json()["config"]["precio_leche"], 612.5)

    def test_un_precio_invalido_es_un_400_que_lo_explica(self):
        r = self.client.post("/api/configuracion?tambo=lamartina_local", json={"precio_leche": "-3"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("precio_leche", r.get_json()["error"])

    def test_cambiar_el_precio_tira_lo_calculado_con_el_precio_viejo(self):
        """Alimentación y Rentabilidad cachean 6 horas el costo con el precio de la leche adentro."""
        app._cache["lamartina_local:__costo_mixer__a_b"] = (0, {"precio_litro": 525})
        app._cache["otro_tambo:__costo_mixer__a_b"] = (0, {"precio_litro": 525})
        r = self.client.post("/api/configuracion?tambo=lamartina_local", json={"precio_leche": "700"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("lamartina_local:__costo_mixer__a_b", app._cache)
        self.assertIn("otro_tambo:__costo_mixer__a_b", app._cache)      # solo el de este tambo

    def test_guardar_el_mismo_precio_no_borra_el_cache(self):
        configuracion_tambo.guardar("lamartina_local", {"precio_leche": 700})
        app._cache["lamartina_local:__costo_mixer__a_b"] = (0, {"precio_litro": 700})
        self.client.post("/api/configuracion?tambo=lamartina_local", json={"precio_leche": "700"})
        self.assertIn("lamartina_local:__costo_mixer__a_b", app._cache)


if __name__ == "__main__":
    unittest.main()
