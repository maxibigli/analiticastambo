"""Aviso por los canales de alerta (WhatsApp, etc.) cuando un sensor del Monitoreo IoT se queda sin mandar datos.

Se avisa UNA vez por sensor cuando se calla y otra cuando vuelve a hablar; no cada pasada. El estado (a quién ya se avisó) se
guarda en disco: si se reinicia el servidor un sensor que sigue mudo no vuelve a avisar. Si el envío falla no se anota nada,
así la pasada siguiente reintenta.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_avisos -v
"""
import datetime
import os
import tempfile
import threading
import unittest
from unittest import mock

import iot_avisos
import iot_lavado
import iot_monitoreo
import iot_vumetros as iv

AHORA = datetime.datetime(2026, 11, 22, 19, 0, 0)       # domingo
SENSORES = set(iot_monitoreo.sensores_con_ith())


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(iv, "_RUTA", os.path.join(self.dir.name, "iot_vumetros.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", os.path.join(self.dir.name, "iot.db")),
                  mock.patch.object(iot_avisos, "RUTA_ESTADO", os.path.join(self.dir.name, "iot_avisos.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(os.path.join(self.dir.name, "iot.db"))
        self.addCleanup(self.con.close)
        self.enviados = []

    def enviar(self, texto):
        self.enviados.append(texto)

    def lectura(self, sensor, valor, hace_min=1):
        cuando = AHORA - datetime.timedelta(minutes=hace_min)
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         (sensor, cuando.isoformat(timespec="seconds"), valor))
        self.con.commit()

    def revisar(self, enviar=None):
        return iot_avisos.revisar(enviar or self.enviar, AHORA)


class TestRevisar(Base):
    def test_sin_ningun_sensor_mudo_no_manda_nada(self):
        self.lectura("temp_leche", 5.0)
        self.assertIsNone(self.revisar())
        self.assertEqual(self.enviados, [])

    def test_un_sensor_que_se_callo_avisa_con_su_nombre_y_su_ultimo_dato(self):
        self.lectura("temp_leche", 7.5, hace_min=95)               # 17:25 del domingo 22/11
        self.revisar()
        self.assertEqual(len(self.enviados), 1)
        t = self.enviados[0]
        self.assertIn("Temp. leche", t)
        self.assertIn("dom 22/11 17:25", t)                        # fecha con día de la semana, no «hace un rato»
        self.assertIn("7,5", t)

    def test_no_avisa_dos_veces_del_mismo_sensor_mudo(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.revisar()
        self.revisar()
        self.assertEqual(len(self.enviados), 1)

    def test_un_reinicio_no_repite_el_aviso(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.revisar()
        self.assertTrue(os.path.exists(iot_avisos.RUTA_ESTADO))   # el estado vive en disco, no en memoria
        self.revisar()
        self.assertEqual(len(self.enviados), 1)

    def test_varios_sensores_mudos_salen_en_un_solo_mensaje(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.lectura("temp_lavado", 60.0, hace_min=200)
        self.revisar()
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("Temp. leche", self.enviados[0])
        self.assertIn("Temp. lavado", self.enviados[0])

    def test_un_sensor_nuevo_que_se_calla_avisa_solo_el_nuevo(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.revisar()
        self.lectura("temp_lavado", 60.0, hace_min=100)
        self.revisar()
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("Temp. lavado", self.enviados[1])
        self.assertNotIn("Temp. leche", self.enviados[1])

    def test_cuando_vuelve_a_hablar_avisa_que_volvio_y_despues_se_puede_volver_a_callar(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.revisar()
        self.lectura("temp_leche", 6.0, hace_min=1)                # volvió
        self.revisar()
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("Volvieron", self.enviados[1])
        self.assertIn("Temp. leche", self.enviados[1])
        self.revisar()                                             # ya volvió: no repite
        self.assertEqual(len(self.enviados), 2)
        iot_avisos_hoy = AHORA + datetime.timedelta(hours=3)       # y más tarde se vuelve a callar
        iot_avisos.revisar(self.enviar, iot_avisos_hoy)
        self.assertEqual(len(self.enviados), 3)
        self.assertIn("sin datos", self.enviados[2].lower())

    def test_si_el_envio_falla_no_se_anota_y_la_pasada_siguiente_reintenta(self):
        self.lectura("temp_leche", 7.5, hace_min=95)

        def roto(texto):
            raise RuntimeError("sin canal")
        with self.assertRaises(RuntimeError):
            self.revisar(roto)
        self.revisar()
        self.assertEqual(len(self.enviados), 1)

    def test_el_ith_no_avisa_aparte_porque_sale_de_la_temperatura_y_la_humedad(self):
        self.lectura("temp_ambiente", 28.0, hace_min=95)
        self.lectura("hum_ambiente", 70.0, hace_min=95)
        self.revisar()
        t = self.enviados[0]
        self.assertIn("Temp. ambiente", t)
        self.assertNotIn("ITH", t)

    def test_un_sensor_con_limites_que_nunca_hablo_avisa_que_nunca_mando_datos(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": 6}}, SENSORES)
        self.revisar()
        self.assertIn("nunca", self.enviados[0].lower())

    def test_un_sensor_sin_instalar_no_avisa(self):
        self.assertIsNone(self.revisar())

    def test_el_texto_no_usa_palabras_relativas(self):
        self.lectura("temp_leche", 7.5, hace_min=95)
        self.revisar()
        t = self.enviados[0].lower()
        for palabra in ("ayer", "hoy", "mañana", "hace "):
            self.assertNotIn(palabra, t)


class TestHilo(Base):
    def test_el_hilo_revisa_y_manda_y_se_puede_parar(self):
        hace = datetime.datetime.now() - datetime.timedelta(hours=3)
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         ("temp_leche", hace.isoformat(timespec="seconds"), 7.5))
        self.con.commit()
        parar = threading.Event()
        hecho = threading.Event()

        def enviar(texto):
            self.enviados.append(texto)
            hecho.set()
        hilo = iot_avisos.iniciar_hilo(enviar, parar=parar, espera_s=0.05, primera_espera_s=0)
        self.assertTrue(hecho.wait(5))
        parar.set()
        hilo.join(5)
        self.assertFalse(hilo.is_alive())
        self.assertEqual(len(self.enviados), 1)

    def test_un_envio_que_falla_no_mata_el_hilo(self):
        hace = datetime.datetime.now() - datetime.timedelta(hours=3)
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         ("temp_leche", hace.isoformat(timespec="seconds"), 7.5))
        self.con.commit()
        parar = threading.Event()
        llamadas = []

        def enviar(texto):
            llamadas.append(texto)
            if len(llamadas) == 1:
                raise RuntimeError("sin canal")
        hilo = iot_avisos.iniciar_hilo(enviar, parar=parar, espera_s=0.05, primera_espera_s=0)
        for _ in range(100):
            if len(llamadas) >= 2:
                break
            parar.wait(0.05)
        parar.set()
        hilo.join(5)
        self.assertGreaterEqual(len(llamadas), 2)          # reintentó después del error


class TestCableado(unittest.TestCase):
    def test_servidor_lo_arranca_una_sola_vez_y_despues_del_candado_de_instancia_unica(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servidor.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count("iniciar_avisos_iot()"), 1)
        self.assertLess(src.index("_tomar_lock_de_instancia_unica():"), src.index("iniciar_avisos_iot()"))

    def test_el_hilo_manda_por_los_canales_activos(self):
        import app
        with mock.patch.object(app.iot_avisos, "iniciar_hilo") as ini,                 mock.patch.object(app, "_enviar_a_canales_activos") as env:
            app.iniciar_avisos_iot()
            ini.call_args.args[0]("hola")
        env.assert_called_once_with("hola")


if __name__ == "__main__":
    unittest.main()
