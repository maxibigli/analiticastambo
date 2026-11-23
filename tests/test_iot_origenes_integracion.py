"""Los orígenes HTTP dentro del catálogo de sensores, el panel («sin datos» por origen) y las asignaciones en uso.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_integracion -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_analogicas
import iot_lavado
import iot_monitoreo
import iot_origenes
import iot_sensores as sen
import iot_vumetros as iv

AHORA = datetime.datetime(2026, 11, 25, 12, 0, 0)
CATALOGO = {"temp_corral": (-10, 45), "temp_leche": (0, 40)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ruta = lambda n: os.path.join(self.dir.name, n)  # noqa: E731
        for p in (mock.patch.object(sen, "_RUTA", ruta("iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", ruta("iot_vumetros.json")),
                  mock.patch.object(iot_analogicas, "_RUTA", ruta("iot_analogicas.json")),
                  mock.patch.object(iot_origenes, "_RUTA", ruta("iot_origenes.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", ruta("iot.db"))):
            p.start()
            self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(ruta("iot.db"))
        self.addCleanup(self.con.close)
        self.origen, _ = iot_origenes.crear("esp32", "generico", 120)
        self.id = self.origen["id"]
        self.canales([{"clave": "temp", "grupo": "entrada_analogica", "sensor": "temp_corral"},
                      {"clave": "puerta", "grupo": "entrada_digital"}])
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)

    def canales(self, canales, en_uso=()):
        iot_origenes.guardar_origen(self.id, "esp32", "generico", 120, canales, sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(), entradas_en_uso=set(en_uso))

    def alarma(self, entrada=None, **kw):
        base = {"label": "Puerta del galpón", "tipo": "digital", "rol": "alarma", "entrada": entrada or f"{self.id}.puerta"}
        sen.guardar(sen.cargar() + [{**base, **kw}])
        return sen.cargar()[-1]["clave"]

    def latido(self, canal, activo, hace_min):
        cuando = AHORA - datetime.timedelta(minutes=hace_min)
        self.con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                         (canal, cuando.isoformat(timespec="seconds"), int(activo)))
        self.con.commit()

    def lectura(self, sensor, valor, hace_min):
        cuando = AHORA - datetime.timedelta(minutes=hace_min)
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         (sensor, cuando.isoformat(timespec="seconds"), valor))
        self.con.commit()

    def sensor(self, clave):
        return {s["clave"]: s for s in iot_monitoreo.lecturas_actuales(AHORA)}[clave]


class TestCatalogo(Base):
    def test_un_sensor_digital_acepta_la_entrada_de_un_origen_que_existe(self):
        clave = self.alarma()
        self.assertEqual({s["clave"]: s for s in sen.cargar()}[clave]["entrada"], f"{self.id}.puerta")

    def test_la_entrada_de_un_origen_que_no_existe_no_se_guarda(self):
        for entrada in ("fantasma.puerta", f"{self.id}.otra", f"{self.id}.temp", "di_99"):
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValueError):
                    self.alarma(entrada=entrada)

    def test_las_entradas_del_m300_siguen_andando(self):
        clave = self.alarma(entrada="di_3")
        self.assertEqual({s["clave"]: s for s in sen.cargar()}[clave]["entrada"], "di_3")

    def test_dos_sensores_en_la_misma_entrada_de_origen(self):
        self.alarma()
        with self.assertRaises(ValueError) as cm:
            self.alarma(label="Otro")
        self.assertIn("cada entrada va a un solo sensor", str(cm.exception))

    def test_si_el_origen_desaparece_el_sensor_no_desaparece_del_catalogo_ni_del_panel(self):
        clave = self.alarma()
        self.latido(f"{self.id}.puerta", True, 1)
        self.assertEqual(self.sensor(clave)["estado"], "ALARMA")
        os.remove(iot_origenes._RUTA)
        self.assertIn(clave, [s["clave"] for s in sen.cargar()])
        s = self.sensor(clave)
        self.assertIn("sin_datos", s)
        self.assertEqual(s["entrada"], f"{self.id}.puerta")

    def test_si_el_origen_desaparece_un_sensor_sin_latido_queda_sin_datos(self):
        clave = self.alarma()
        os.remove(iot_origenes._RUTA)
        self.assertTrue(self.sensor(clave)["sin_datos"])

    def test_un_guardado_estricto_igual_rechaza_la_entrada_perdida(self):
        clave = self.alarma()
        os.remove(iot_origenes._RUTA)
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar())      # el tambo tiene que arreglar la entrada antes de guardar
        self.assertIn(clave, [s["clave"] for s in sen.cargar()])

    def test_una_entrada_con_punto_que_no_es_de_ningun_origen_tampoco_se_cuela_al_guardar(self):
        with self.assertRaises(ValueError):
            self.alarma(entrada="a.b.c")


class TestVentanaPorOrigen(Base):
    def test_un_origen_con_ventana_120_no_esta_mudo_a_los_90_minutos_y_si_a_los_130(self):
        clave = self.alarma()
        self.latido(f"{self.id}.puerta", True, 90)
        self.assertFalse(self.sensor(clave)["sin_datos"])
        self.latido(f"{self.id}.puerta", True, 130)
        self.assertTrue(self.sensor(clave)["sin_datos"])

    def test_un_sensor_analogico_asignado_en_un_origen_usa_la_ventana_del_origen(self):
        self.lectura("temp_corral", 21.0, 90)
        s = self.sensor("temp_corral")
        self.assertFalse(s["sin_datos"])
        self.assertEqual(s["valor"], 21.0)
        self.lectura("temp_corral", 22.0, 130)           # la más reciente sigue siendo la de hace 90
        self.con.execute("DELETE FROM lecturas_sensor WHERE valor = 21.0")
        self.con.commit()
        self.assertTrue(self.sensor("temp_corral")["sin_datos"])

    def test_los_sensores_del_m300_siguen_con_su_ventana_de_30(self):
        self.lectura("temp_leche", 4.0, 31)
        self.assertTrue(self.sensor("temp_leche")["sin_datos"])
        self.lectura("temp_leche", 4.0, 29)
        self.assertFalse(self.sensor("temp_leche")["sin_datos"])

    def test_las_entradas_del_m300_siguen_con_su_ventana_de_30(self):
        clave = self.alarma(entrada="di_3", label="Térmico")
        self.latido("di_3", False, 31)
        self.assertTrue(self.sensor(clave)["sin_datos"])
        self.latido("di_3", False, 5)
        self.assertFalse(self.sensor(clave)["sin_datos"])

    def test_el_ith_conserva_la_ventana_de_siempre(self):
        self.lectura("temp_ambiente", 25.0, 31)
        self.lectura("hum_ambiente", 60.0, 31)
        self.assertTrue(self.sensor("ith")["sin_datos"])

    def asignar_ambiente(self, ventana=120, solo=("temp_ambiente", "hum_ambiente")):
        iot_origenes.guardar_origen(self.id, "esp32", "generico", ventana,
                                    [{"clave": s, "grupo": "entrada_analogica", "sensor": s} for s in solo],
                                    sensores_analogicos={"temp_ambiente": (0, 1), "hum_ambiente": (0, 1)},
                                    sensores_ocupados=set(), entradas_en_uso=set())

    def test_el_ith_usa_la_ventana_del_origen_cuando_sus_dos_datos_vienen_de_ahi(self):
        self.asignar_ambiente()
        self.lectura("temp_ambiente", 25.0, 90)
        self.lectura("hum_ambiente", 60.0, 90)
        self.assertFalse(self.sensor("ith")["sin_datos"])
        self.assertIsNotNone(self.sensor("ith")["valor"])
        self.con.execute("DELETE FROM lecturas_sensor")           # ahora las dos lecturas más nuevas son de hace 130 min
        self.con.commit()
        self.lectura("temp_ambiente", 25.0, 130)
        self.lectura("hum_ambiente", 60.0, 130)
        self.assertTrue(self.sensor("ith")["sin_datos"])

    def test_el_ith_figura_mudo_si_cualquiera_de_sus_dos_datos_esta_mudo(self):
        # La temperatura viene de un origen con ventana de 120 (hace 90 min: vigente) y la humedad del M300 (ventana 30,
        # hace 45 min: muda): el ITH sale de las dos, así que está mudo.
        self.asignar_ambiente(solo=("temp_ambiente",))
        self.lectura("temp_ambiente", 25.0, 90)
        self.lectura("hum_ambiente", 60.0, 45)
        self.assertFalse(self.sensor("temp_ambiente")["sin_datos"])
        self.assertTrue(self.sensor("hum_ambiente")["sin_datos"])
        self.assertTrue(self.sensor("ith")["sin_datos"])

    def test_el_ith_vigente_si_los_dos_datos_estan_vigentes_en_su_propio_origen(self):
        self.asignar_ambiente(solo=("temp_ambiente",))
        self.lectura("temp_ambiente", 25.0, 90)
        self.lectura("hum_ambiente", 60.0, 10)
        self.assertFalse(self.sensor("ith")["sin_datos"])

    def test_las_ventanas_se_piden_una_sola_vez_por_llamada(self):
        self.alarma()
        with mock.patch.object(iot_origenes, "ventanas", wraps=iot_origenes.ventanas) as v:
            iot_monitoreo.lecturas_actuales(AHORA)
        self.assertEqual(v.call_count, 1)

    def test_sin_origenes_todo_da_lo_de_siempre(self):
        os.remove(iot_origenes._RUTA)
        self.lectura("temp_leche", 4.0, 29)
        self.latido("lavado_rotativa", True, 3)
        self.lectura("temp_corral", 20.0, 31)
        con_archivo_ausente = iot_monitoreo.lecturas_actuales(AHORA)
        with mock.patch.object(iot_origenes, "ventanas", return_value={"entradas": {}, "sensores": {}}):
            self.assertEqual(iot_monitoreo.lecturas_actuales(AHORA), con_archivo_ausente)


class TestEnUso(Base):
    def post(self, ruta, cuerpo):
        return self.admin.post(ruta, json=cuerpo)

    def test_un_sensor_asignado_en_un_origen_no_se_asigna_a_una_entrada_del_m300(self):
        r = self.post("/api/iot/analogicas", {"canales": {"ai_1": {"sensor": "temp_corral"}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("temp_corral", r.get_json()["error"])
        self.assertEqual(iot_analogicas.cargar(), {})

    def test_un_sensor_que_no_esta_en_un_origen_se_sigue_pudiendo_asignar(self):
        r = self.post("/api/iot/analogicas", {"canales": {"ai_1": {"sensor": "temp_leche"}}})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_un_sensor_asignado_en_un_origen_no_se_borra_del_catalogo(self):
        lista = [s for s in sen.cargar() if s["clave"] != "temp_corral"]
        antes = sen.cargar()
        r = self.post("/api/iot/sensores", {"sensores": lista})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Temp. corral", r.get_json()["error"])
        self.assertEqual(sen.cargar(), antes)

    def test_un_sensor_sin_asignar_se_borra_igual_que_siempre(self):
        lista = [s for s in sen.cargar() if s["clave"] != "temp_leche"]
        self.assertEqual(self.post("/api/iot/sensores", {"sensores": lista}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
