"""Vigilante, Tarea 6: rutas de Configuración, «Probar» y los avisos retenidos dentro del mensaje de las 08:00/20:00.

Todo contra archivos temporales (nunca toca `vigilante.db` ni `vigilante_config.json` reales) y sin red.

    C:\\Python314\\python.exe -m unittest tests.test_vigilante_api -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import tambos
import vigilante
import vigilante_estado as ve

TAMBO = tambos.DEFAULT_TAMBO
AHORA = datetime.datetime(2026, 11, 14, 9, 30)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta_db = os.path.join(self.dir.name, "v.db")
        for p in (mock.patch.object(ve, "RUTA_DB", self.ruta_db),
                  mock.patch.object(ve, "RUTA_CONFIG", os.path.join(self.dir.name, "v.json"))):
            p.start()
            self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.anonimo = app.app.test_client()


class TestConfig(Base):
    def test_config_roundtrip_y_validacion(self):
        r = self.admin.get("/api/vigilante/config")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["activo"])                       # viene apagado
        r = self.admin.post("/api/vigilante/config", json={"activo": True, "tope_dia": 5, "silencio_desde": "21:30"})
        self.assertEqual(r.status_code, 200)
        cfg = self.admin.get("/api/vigilante/config").get_json()
        self.assertEqual((cfg["activo"], cfg["tope_dia"], cfg["silencio_desde"], cfg["pasada_min"]),
                         (True, 5, "21:30", 15))
        r = self.admin.post("/api/vigilante/config", json={"tope_dia": 0})
        self.assertEqual(r.status_code, 400)
        self.assertIn("tope", r.get_json()["error"].lower())
        self.assertEqual(self.admin.get("/api/vigilante/config").get_json()["tope_dia"], 5)   # no se guardó a medias

    def test_post_sin_cuerpo_valido_es_400_y_no_un_500(self):
        r = self.admin.post("/api/vigilante/config", json={"inexistente": 1})
        self.assertEqual(r.status_code, 400)

    def test_rutas_exigen_admin(self):
        pedidos = (("get", "/api/vigilante/config"), ("post", "/api/vigilante/config"),
                   ("get", "/api/vigilante/estado"), ("post", "/api/vigilante/probar"))
        for metodo, ruta in pedidos:
            with self.subTest(ruta=ruta, quien="operario"):
                self.assertEqual(getattr(self.operario, metodo)(ruta, json={}).status_code, 403)
            with self.subTest(ruta=ruta, quien="anonimo"):
                self.assertIn(getattr(self.anonimo, metodo)(ruta, json={}).status_code, (302, 401, 403))


class TestEstado(Base):
    def test_estado_vacio_y_con_datos(self):
        e = self.admin.get("/api/vigilante/estado").get_json()
        self.assertEqual(e, {"estado_datos": "ok", "avisos_hoy": 0, "ultima_pasada": None, "retenidos": []})
        est = ve.Estado(self.ruta_db)
        est.registrar_envio(datetime.date.today())
        est.retener("baja del ordeño 1", AHORA)
        est.set_ultima_pasada(AHORA, "ok")
        e = self.admin.get("/api/vigilante/estado").get_json()
        self.assertEqual((e["avisos_hoy"], e["retenidos"]), (1, ["baja del ordeño 1"]))
        self.assertEqual(e["ultima_pasada"], {"cuando": AHORA.isoformat(), "resultado": "ok"})
        self.assertEqual(est.retenidos(), ["baja del ordeño 1"])         # mirar no los gasta


class TestProbar(Base):
    RESULTADO = {"estado": "ok", "mensaje": "👁 Vigilante de LactIA — sábado 14/11 09:30\n\nBaja en el ordeño 1",
                 "enviado": False, "retenidos": 0,
                 "hallazgos": [{"tipo": "baja_ordeno", "clave": "2026-11-14|1|baja_ordeno", "datos": {}, "causas": []}]}

    def test_probar_no_gasta_dedup_ni_tope_ni_envia(self):
        with mock.patch.object(vigilante, "pasada", return_value=self.RESULTADO) as p, \
                mock.patch.object(app, "_enviar_resumen_a_canales_activos") as envio:
            r1 = self.admin.post("/api/vigilante/probar", json={})
            r2 = self.admin.post("/api/vigilante/probar", json={})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.get_json(), r2.get_json())
        self.assertEqual(r1.get_json()["mensaje"], self.RESULTADO["mensaje"])
        self.assertEqual(r1.get_json()["hallazgos"], [{"tipo": "baja_ordeno", "clave": "2026-11-14|1|baja_ordeno"}])
        for llamada in p.call_args_list:
            self.assertIs(llamada.kwargs["registrar"], False)
            self.assertIsNone(llamada.kwargs["enviar"])
        envio.assert_not_called()
        est = ve.Estado(self.ruta_db)
        self.assertEqual((est.avisos_del_dia(datetime.date.today()), est.retenidos()), (0, []))

    def test_probar_con_enviar_lo_manda_por_los_canales_pero_no_registra(self):
        with mock.patch.object(vigilante, "pasada", return_value=self.RESULTADO), \
                mock.patch.object(app, "_enviar_resumen_a_canales_activos") as envio:
            r = self.admin.post("/api/vigilante/probar", json={"enviar": True})
        self.assertEqual(r.status_code, 200)
        envio.assert_called_once_with(self.RESULTADO["mensaje"], None)
        self.assertEqual(ve.Estado(self.ruta_db).avisos_del_dia(datetime.date.today()), 0)

    def test_probar_con_enviar_y_nada_para_avisar_no_manda_y_lo_dice(self):
        vacio = {"estado": "ok", "mensaje": None, "enviado": False, "retenidos": 0, "hallazgos": []}
        with mock.patch.object(vigilante, "pasada", return_value=vacio), \
                mock.patch.object(app, "_enviar_resumen_a_canales_activos") as envio:
            r = self.admin.post("/api/vigilante/probar", json={"enviar": True})
        self.assertEqual(r.status_code, 400)
        envio.assert_not_called()

    def test_probar_si_la_lectura_falla_devuelve_el_error_y_no_levanta(self):
        malo = {"estado": "error", "mensaje": None, "enviado": False, "retenidos": 0, "hallazgos": [],
                "error": "LecturaError: sin conexion"}
        with mock.patch.object(vigilante, "pasada", return_value=malo):
            r = self.admin.post("/api/vigilante/probar", json={})
        self.assertEqual(r.status_code, 502)
        self.assertIn("sin conexion", r.get_json()["error"])


class TestRetenidosEnElMensajeDeLas8(Base):
    def _construir(self, registrar):
        with mock.patch.object(app, "_valores_tablero", return_value={}), \
                mock.patch.object(app.tablero, "armar", return_value={}), \
                mock.patch.object(app.tablero, "texto_resumen", return_value=None), \
                mock.patch.object(app, "_lineas_alertas_puntuales", return_value=[]), \
                mock.patch.object(app.config_alertas, "checklist_resumen_activo", return_value=False):
            return app._construir_mensaje_alertas(TAMBO, registrar=registrar)

    def test_el_mensaje_incluye_los_retenidos_y_el_ciclo_real_los_vacia(self):
        est = ve.Estado(self.ruta_db)
        est.retener("Baja en el ordeño 1 (sábado 14/11): 920 kg", AHORA)
        est.retener("Faltaron 24 vacas en el ordeño 2", AHORA)
        texto, html = self._construir(registrar=True)
        self.assertIn("vigilante retuvo 2 aviso(s)", texto)
        self.assertIn("Baja en el ordeño 1", texto)
        self.assertIn("Faltaron 24 vacas", html)
        self.assertEqual(est.retenidos(), [])
        texto2, _ = self._construir(registrar=True)
        self.assertIsNone(texto2)                                      # ya se dijeron: no se repiten

    def test_probar_ciclo_los_muestra_pero_no_los_consume(self):
        est = ve.Estado(self.ruta_db)
        est.retener("Baja en el ordeño 1", AHORA)
        texto, _ = self._construir(registrar=False)
        self.assertIn("vigilante retuvo 1 aviso(s)", texto)
        self.assertEqual(est.retenidos(), ["Baja en el ordeño 1"])
        texto_otra_vez, _ = self._construir(registrar=False)
        self.assertEqual(texto, texto_otra_vez)

    def test_sin_base_del_vigilante_no_la_crea_ni_agrega_lineas(self):
        self.assertFalse(os.path.exists(self.ruta_db))
        texto, _ = self._construir(registrar=True)
        self.assertIsNone(texto)
        self.assertFalse(os.path.exists(self.ruta_db))

    def test_otro_tambo_no_recibe_los_retenidos_de_ponderosa(self):
        est = ve.Estado(self.ruta_db)
        est.retener("Baja en el ordeño 1", AHORA)
        otro = next(t for t in tambos.TAMBOS if t != tambos.DEFAULT_TAMBO)
        with mock.patch.object(app, "_valores_tablero", return_value={}), \
                mock.patch.object(app.tablero, "armar", return_value={}), \
                mock.patch.object(app.tablero, "texto_resumen", return_value=None), \
                mock.patch.object(app, "_lineas_alertas_puntuales", return_value=[]), \
                mock.patch.object(app.config_alertas, "checklist_resumen_activo", return_value=False):
            texto, _ = app._construir_mensaje_alertas(otro, registrar=True)
        self.assertIsNone(texto)
        self.assertEqual(est.retenidos(), ["Baja en el ordeño 1"])


class TestHiloDelVigilante(Base):
    def test_importar_app_no_arranca_el_vigilante(self):
        import threading
        self.assertNotIn("vigilante", [t.name for t in threading.enumerate()])

    def test_iniciar_vigilante_arranca_el_hilo_con_el_tambo_y_el_envio_por_canales(self):
        with mock.patch.object(vigilante, "iniciar_hilo") as ih:
            app.iniciar_vigilante()
        tambo, enviar = ih.call_args.args
        self.assertEqual(tambo, TAMBO)
        with mock.patch.object(app, "_enviar_a_canales_activos") as env:
            enviar("hola")
        env.assert_called_once_with("hola")

    def test_servidor_lo_arranca_una_sola_vez_y_despues_de_tomar_el_candado(self):
        # el candado de instancia unica esta en servidor.py: otro proceso que importe `app` (precalentar, los
        # diagnosticos) no tiene que tener su propio vigilante mandando avisos por los canales reales
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servidor.py")
        src = open(ruta, encoding="utf-8").read()
        self.assertEqual(src.count("iniciar_vigilante()"), 1)
        self.assertLess(src.index("_tomar_lock_de_instancia_unica():"), src.index("iniciar_vigilante()"))


if __name__ == "__main__":
    unittest.main()
