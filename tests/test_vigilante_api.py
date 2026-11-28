"""Los avisos retenidos dentro del mensaje de las 08:00/20:00 y el arranque del hilo de avisos. Las rutas de
configuración son las de Vigilat y se prueban en `test_vigilat_api.py`.

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
import vigilante_estado as ve
import vigilat_motor

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
        self.assertIn("Vigilat retuvo 2 aviso(s)", texto)
        self.assertIn("Baja en el ordeño 1", texto)
        self.assertIn("Faltaron 24 vacas", html)
        self.assertEqual(est.retenidos(), [])
        texto2, _ = self._construir(registrar=True)
        self.assertIsNone(texto2)                                      # ya se dijeron: no se repiten

    def test_probar_ciclo_los_muestra_pero_no_los_consume(self):
        est = ve.Estado(self.ruta_db)
        est.retener("Baja en el ordeño 1", AHORA)
        texto, _ = self._construir(registrar=False)
        self.assertIn("Vigilat retuvo 1 aviso(s)", texto)
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
        with mock.patch.object(vigilat_motor, "iniciar_hilo") as ih:
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
