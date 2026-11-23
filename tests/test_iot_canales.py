"""Nombres de las salidas del gateway (actuadores) y nombre de las entradas (el de su sensor).

Las entradas ya no llevan nombre propio: se ven con el del sensor que el tambo les ató, o «Entrada N» si no tienen ninguno.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_canales -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import iot_canales
import iot_lavado
import iot_monitoreo
import iot_sensores as sen


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ruta_db = os.path.join(self.dir.name, "iot.db")
        for p in (mock.patch.object(iot_canales, "_RUTA", os.path.join(self.dir.name, "nombres.json")),
                  mock.patch.object(sen, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", ruta_db),
                  mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(ruta_db)),
                  mock.patch.object(iot_monitoreo.voz_comandos, "estado", return_value={})):
            p.start()
            self.addCleanup(p.stop)


class TestNombres(Base):
    def test_las_salidas_se_guardan_y_se_leen(self):
        iot_canales.guardar({"do_1": "Bomba de agua", "do_2": ""})
        self.assertEqual(iot_canales.nombres(), {"do_1": "Bomba de agua"})

    def test_un_nombre_de_entrada_no_se_guarda(self):
        iot_canales.guardar({"di_3": "Ordeño", "do_1": "Bomba de agua"})
        self.assertEqual(iot_canales.nombres(), {"do_1": "Bomba de agua"})

    def test_un_archivo_viejo_con_nombres_de_entrada_se_lee_sin_ellos(self):
        with open(iot_canales._RUTA, "w", encoding="utf-8") as f:
            json.dump({"di_3": "ghfgjhfgjhg", "do_4": "Espuma"}, f)
        self.assertEqual(iot_canales.nombres(), {"do_4": "Espuma"})

    def test_un_canal_inexistente_se_sigue_rechazando(self):
        with self.assertRaises(ValueError):
            iot_canales.guardar({"do_99": "x"})

    def test_dos_salidas_no_pueden_llamarse_igual(self):
        with self.assertRaises(ValueError):
            iot_canales.guardar({"do_1": "Bomba de agua", "do_2": "bomba de agua"})


class TestEntradasConNombreDeSensor(Base):
    def entradas(self):
        return {e["clave"]: e["label"] for e in iot_monitoreo.panel_io()["entradas"]}

    def test_una_entrada_sin_sensor_dice_su_nombre_generico(self):
        self.assertEqual(self.entradas()["di_5"], "Entrada 5")

    def test_una_entrada_con_sensor_lleva_el_nombre_del_sensor(self):
        sen.guardar(sen.cargar() + [{"label": "Portón de la sala", "tipo": "digital", "entrada": "di_5"}])
        self.assertEqual(self.entradas()["di_5"], "Portón de la sala")

    def test_lavado_y_barrido_llevan_el_nombre_de_sus_sensores_precargados(self):
        self.assertEqual(self.entradas()["lavado_rotativa"], "Lavado de la rotativa")

    def test_un_nombre_viejo_de_entrada_no_se_ve(self):
        with open(iot_canales._RUTA, "w", encoding="utf-8") as f:
            json.dump({"di_5": "ghfgjhfgjhg"}, f)
        self.assertEqual(self.entradas()["di_5"], "Entrada 5")


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_el_editor_trae_solo_las_salidas(self):
        d = self.cli.get("/api/iot/canales").get_json()
        self.assertNotIn("entradas", d)
        self.assertEqual(len(d["salidas"]), 8)

    def test_el_menu_de_entradas_del_editor_de_sensores_usa_los_nombres_genericos(self):
        with open(iot_canales._RUTA, "w", encoding="utf-8") as f:
            json.dump({"di_3": "ghfgjhfgjhg"}, f)
        etiquetas = {e["clave"]: e["label"] for e in self.cli.get("/api/iot/sensores").get_json()["entradas"]}
        self.assertEqual(etiquetas["di_3"], "Entrada 3")


if __name__ == "__main__":
    unittest.main()
