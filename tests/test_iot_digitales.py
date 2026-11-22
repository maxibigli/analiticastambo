"""Sensores digitales del Monitoreo IoT (alarma / indicador) y el catálogo dinámico dentro de `lecturas_actuales`.

El estado de una entrada digital viene del latido que deja `iot_lavado.py` en cada lectura del gateway
(`digitales_estado`), no solo de los cambios: con solo los cambios un contacto que no se mueve en una semana no se
distinguiría de un gateway apagado.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_digitales -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import iot_lavado
import iot_monitoreo
import iot_sensores as sen
import iot_vumetros as iv

AHORA = datetime.datetime(2026, 11, 22, 19, 0, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(sen, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", os.path.join(self.dir.name, "iot_vumetros.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", os.path.join(self.dir.name, "iot.db"))):
            p.start()
            self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(os.path.join(self.dir.name, "iot.db"))
        self.addCleanup(self.con.close)

    def latido(self, canal, activo, hace_min=1):
        cuando = AHORA - datetime.timedelta(minutes=hace_min)
        self.con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                         (canal, cuando.isoformat(timespec="seconds"), int(activo)))
        self.con.commit()

    def sensor(self, clave):
        return {s["clave"]: s for s in iot_monitoreo.lecturas_actuales(AHORA)}[clave]

    def agregar(self, **kw):
        base = {"label": "Térmico bomba", "tipo": "digital", "rol": "alarma", "entrada": "di_3"}
        sen.guardar(sen.cargar() + [{**base, **kw}])
        return sen.cargar()[-1]["clave"]


class TestLatido(Base):
    def test_el_latido_se_guarda_la_primera_vez_y_cuando_cambia_y_cada_tanto_pero_no_en_cada_ciclo(self):
        ultimo = {}
        t = AHORA
        iot_lavado.latir(self.con, "di_3", False, ultimo, t)
        iot_lavado.latir(self.con, "di_3", False, ultimo, t + datetime.timedelta(seconds=3))
        fila = self.con.execute("SELECT fecha_hora, estado FROM digitales_estado WHERE canal='di_3'").fetchone()
        self.assertEqual(fila[0], t.isoformat(timespec="seconds"))                       # el segundo ciclo no escribió
        iot_lavado.latir(self.con, "di_3", True, ultimo, t + datetime.timedelta(seconds=6))      # cambió: escribe ya
        self.assertEqual(self.con.execute("SELECT estado FROM digitales_estado WHERE canal='di_3'").fetchone()[0], 1)
        iot_lavado.latir(self.con, "di_3", True, ultimo, t + datetime.timedelta(seconds=6 + iot_lavado.INTERVALO_LATIDO_DI_S))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM digitales_estado").fetchone()[0], 1)   # una fila por canal

    def test_el_contacto_normalmente_cerrado_se_toma_de_la_ficha_del_sensor(self):
        sen.guardar(sen.cargar() + [{"label": "NC", "tipo": "digital", "entrada": "di_4", "invertido": True},
                                    {"label": "NA", "tipo": "digital", "entrada": "di_5"}])
        self.assertEqual(iot_lavado.entradas_invertidas(), {"di_4": True, "di_5": False})

    def test_sin_sensores_digitales_nadie_esta_invertido(self):
        self.assertEqual(iot_lavado.entradas_invertidas(), {})


class TestAlarma(Base):
    def test_activa_es_roja_con_su_texto_e_inactiva_es_verde(self):
        clave = self.agregar()
        self.latido("di_3", True)
        s = self.sensor(clave)
        self.assertEqual((s["activo"], s["estado"], s["zona"], s["sin_datos"]), (True, "ALARMA", "rojo", False))
        self.latido("di_3", False)
        s = self.sensor(clave)
        self.assertEqual((s["activo"], s["estado"], s["zona"]), (False, "Normal", "verde"))

    def test_si_el_gateway_dejo_de_latir_es_sin_datos_y_no_el_ultimo_estado(self):
        clave = self.agregar()
        self.latido("di_3", False, hace_min=120)
        s = self.sensor(clave)
        self.assertTrue(s["sin_datos"])
        self.assertEqual((s["activo"], s["estado"], s["zona"]), (None, None, None))
        self.assertEqual(s["ultimo_estado"], "Normal")

    def test_una_alarma_con_entrada_que_nunca_latio_es_sin_datos(self):
        self.assertTrue(self.sensor(self.agregar())["sin_datos"])

    def test_una_alarma_sin_entrada_es_sin_instalar_no_una_falla(self):
        s = self.sensor(self.agregar(entrada=""))
        self.assertFalse(s["sin_datos"])
        self.assertIsNone(s["activo"])

    def test_una_alarma_aparece_en_el_conteo_del_tablero(self):
        self.agregar()
        self.latido("di_3", True)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["en_rojo"]), (1, ["Térmico bomba"]))


class TestIndicador(Base):
    def test_un_indicador_con_colores_muestra_su_texto_y_su_color(self):
        clave = self.agregar(label="Portón", rol="indicador", texto_activo="Abierto", texto_inactivo="Cerrado",
                             color_activo="amarillo", color_inactivo="verde")
        self.latido("di_3", True)
        s = self.sensor(clave)
        self.assertEqual((s["estado"], s["zona"], s["con_color"]), ("Abierto", "amarillo", True))

    def test_un_indicador_neutro_no_tiene_color_ni_cuenta(self):
        clave = self.agregar(label="Libre", rol="indicador")
        self.latido("di_3", True)
        s = self.sensor(clave)
        self.assertEqual((s["estado"], s["zona"], s["con_color"]), ("Activo", None, False))
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["amarillo"], c["verde"]), (0, 0, 0))

    def test_un_indicador_neutro_que_dejo_de_latir_tambien_esta_mudo(self):
        clave = self.agregar(label="Libre", rol="indicador")
        self.latido("di_3", True, hace_min=120)
        self.assertTrue(self.sensor(clave)["sin_datos"])

    def test_un_indicador_neutro_que_nunca_latio_es_sin_instalar(self):
        self.assertFalse(self.sensor(self.agregar(label="Libre", rol="indicador"))["sin_datos"])


class TestCatalogoDinamico(Base):
    def test_un_analogico_nuevo_aparece_en_las_lecturas_y_en_lo_asignable(self):
        sen.guardar(sen.cargar() + [{"label": "Temp. silo", "tipo": "analogico", "unidad": "°C", "min": 0, "max": 60}])
        self.assertIn("temp_silo", [s["clave"] for s in iot_monitoreo.lecturas_actuales(AHORA)])
        self.assertEqual(iot_monitoreo.sensores_asignables()["temp_silo"], (0, 60))
        self.assertIn("temp_silo", iot_monitoreo.sensores_con_ith())

    def test_un_digital_no_se_puede_asignar_a_una_entrada_analogica_ni_tiene_vumetro(self):
        clave = self.agregar()
        self.assertNotIn(clave, iot_monitoreo.sensores_asignables())
        self.assertNotIn(clave, iot_monitoreo.sensores_con_ith())
        self.assertNotIn(clave, [s["clave"] for s in iot_monitoreo.sensores_para_vumetros()])

    def test_sin_temperatura_ni_humedad_ambiente_el_ith_no_rompe(self):
        sen.guardar([s for s in sen.cargar() if s["clave"] not in ("temp_ambiente", "hum_ambiente")])
        ith = self.sensor("ith")
        self.assertIsNone(ith["valor"])
        self.assertFalse(ith["sin_datos"])

    def test_un_sensor_sacado_de_la_lista_desaparece_del_panel(self):
        sen.guardar([s for s in sen.cargar() if s["clave"] != "temp_corral"])
        self.assertNotIn("temp_corral", [s["clave"] for s in iot_monitoreo.lecturas_actuales(AHORA)])


class TestPantallaEsp32(Base):
    def test_los_digitales_van_aparte_para_no_confundir_al_firmware_que_espera_numeros(self):
        clave = self.agregar()
        self.con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES ('di_3', ?, 1)",
                         (datetime.datetime.now().isoformat(timespec="seconds"),))
        self.con.commit()
        with mock.patch.object(app.iot_monitoreo, "estado_sistema", return_value={"estado": "APAGADO", "desde": None}):
            d = app.app.test_client().get("/api/iot/pantalla").get_json()
        self.assertNotIn(clave, d["sensores"])
        self.assertEqual(d["digitales"][clave]["estado"], "ALARMA")
        self.assertEqual(d["digitales"][clave]["zona"], "rojo")


if __name__ == "__main__":
    unittest.main()
