"""Un origen HTTP que puede recibir órdenes: la casilla, el vencimiento y los canales de salida.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_comando -v
"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import iot_ingreso
import iot_origenes

CATALOGO = {"temp_corral": (-10, 45)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "iot_origenes.json")
        for p in (mock.patch.object(iot_origenes, "_RUTA", self.ruta),
                  mock.patch.object(iot_ingreso, "RUTA_DB", os.path.join(self.dir.name, "iot.db"))):
            p.start()
            self.addCleanup(p.stop)
        self.origen, self.token = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.id = self.origen["id"]

    def leer_bytes(self):
        with open(self.ruta, "rb") as f:
            return f.read()

    def guardar(self, canales, id_=None, formato="generico", puede=True, vence=120, ocupados=(), en_uso=(), **kw):
        iot_origenes.guardar_origen(id_ or self.id, "ESP32 del corral", formato, 120, canales, sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(ocupados), entradas_en_uso=set(en_uso),
                                    puede_comandar=puede, vencimiento_orden_s=vence, **kw)

    def assertRechaza(self, canales, texto=None, **kw):
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar(canales, **kw)
        if texto:
            self.assertIn(texto, str(cm.exception))
        self.assertEqual(self.leer_bytes(), antes, "un guardado inválido tiene que dejar el archivo igual")


def salida(clave="bomba", **kw):
    return {"clave": clave, "grupo": "salida", "nombre": clave, "modo": "pulso", "pulso_s": 1.5, **kw}


def sostenida(clave="calefaccion", **kw):
    return {"clave": clave, "grupo": "salida", "nombre": clave, "modo": "sostenida", **kw}


class TestGuardar(Base):
    def test_por_defecto_un_origen_no_puede_comandar(self):
        self.assertEqual((self.origen["puede_comandar"], self.origen["vencimiento_orden_s"]), (False, None))
        o = iot_origenes.obtener(self.id)
        self.assertEqual((o["puede_comandar"], o["vencimiento_orden_s"]), (False, None))

    def test_guardado_feliz_con_una_salida_de_cada_modo(self):
        self.guardar([salida(), sostenida(), {"clave": "puerta", "grupo": "entrada_digital"}])
        o = iot_origenes.obtener(self.id)
        self.assertEqual((o["puede_comandar"], o["vencimiento_orden_s"]), (True, 120))
        c = {x["clave"]: x for x in o["canales"]}
        self.assertEqual((c["bomba"]["grupo"], c["bomba"]["modo"], c["bomba"]["pulso_s"]), ("salida", "pulso", 1.5))
        self.assertEqual((c["calefaccion"]["modo"], c["calefaccion"]["pulso_s"]), ("sostenida", None))
        self.assertEqual(iot_origenes.salidas(), (f"{self.id}.bomba", f"{self.id}.calefaccion"))

    def test_las_salidas_no_son_entradas_ni_sensores(self):
        self.guardar([salida(), {"clave": "puerta", "grupo": "entrada_digital"},
                      {"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_corral"}])
        self.assertEqual(iot_origenes.entradas_digitales(), (f"{self.id}.puerta",))
        self.assertEqual(iot_origenes.sensores_asignados(), {"temp_corral"})
        self.assertEqual(iot_origenes.ventanas(), {"entradas": {f"{self.id}.puerta": 120}, "sensores": {"temp_corral": 120}})

    def test_un_archivo_de_la_etapa_3_sin_los_campos_nuevos_se_lee_igual(self):
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        for o in datos["origenes"]:
            o.pop("puede_comandar", None)
            o.pop("vencimiento_orden_s", None)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        o = iot_origenes.obtener(self.id)
        self.assertEqual((o["puede_comandar"], o["vencimiento_orden_s"]), (False, None))

    def test_el_vencimiento_es_obligatorio_y_entero(self):
        for malo in (None, 0, -3, 1.5, "120", True, float("nan"), float("inf"), ""):
            with self.subTest(vence=malo):
                self.assertRechaza([salida()], vence=malo)

    def test_puede_comandar_tiene_que_ser_si_o_no(self):
        for malo in ("si", 1, None, "true"):
            with self.subTest(puede=malo):
                self.assertRechaza([], puede=malo)

    def test_chirpstack_no_se_comanda(self):
        cs, _ = iot_origenes.crear("Nodo lora", "chirpstack", 90)
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar([], id_=cs["id"], formato="chirpstack")
        self.assertIn("ChirpStack", str(cm.exception))
        self.assertEqual(self.leer_bytes(), antes)

    def test_un_origen_que_no_puede_comandar_rechaza_las_salidas(self):
        self.assertRechaza([salida()], "Puede recibir órdenes", puede=False, vence=None)
        self.guardar([{"clave": "puerta", "grupo": "entrada_digital"}], puede=False, vence=None)       # sin salidas es lo de siempre

    def test_el_vencimiento_se_guarda_solo_si_puede_comandar(self):
        self.guardar([], puede=False, vence=300)
        self.assertIsNone(iot_origenes.obtener(self.id)["vencimiento_orden_s"])

    def test_modo_y_pulso(self):
        for malo in (salida(modo=None), salida(modo=""), salida(modo="rafaga"), salida(pulso_s=None), salida(pulso_s=""),
                     salida(pulso_s=0), salida(pulso_s=-1), salida(pulso_s="abc"), salida(pulso_s=float("nan")),
                     salida(pulso_s=float("inf")), salida(pulso_s=True),
                     salida(pulso_s=iot_origenes.PULSO_MAX_ORIGEN_S + 1), sostenida(pulso_s=2)):
            with self.subTest(canal=str(malo)):
                self.assertRechaza([malo])
        self.guardar([salida(pulso_s=iot_origenes.PULSO_MAX_ORIGEN_S), salida("otra", pulso_s="0,5")])
        c = {x["clave"]: x["pulso_s"] for x in iot_origenes.obtener(self.id)["canales"]}
        self.assertEqual(c, {"bomba": 60, "otra": 0.5})

    def test_una_salida_no_lleva_sensor_ni_factor(self):
        self.assertRechaza([salida(sensor="temp_corral")])
        self.assertRechaza([salida(factor=2)])

    def test_clave_repetida_entre_una_entrada_y_una_salida(self):
        self.assertRechaza([salida("x"), {"clave": "x", "grupo": "entrada_digital"}])

    def test_el_tope_de_canales_cuenta_las_salidas(self):
        self.assertRechaza([salida(f"s{i}") for i in range(33)])
        self.guardar([salida(f"s{i}") for i in range(32)])

    def test_apagar_la_casilla_o_sacar_una_salida_es_valido(self):
        self.guardar([salida()])
        self.guardar([], puede=False, vence=None)
        self.assertEqual(iot_origenes.salidas(), ())

    def test_un_equipo_modbus_sigue_sin_salidas(self):
        mb = iot_origenes.crear_modbus("PLC", "10.0.0.5", None, None, 15)
        with self.assertRaises(ValueError):
            iot_origenes.guardar_modbus(mb["id"], "PLC", "10.0.0.5", 502, 1, 15,
                                        [{"clave": "r", "grupo": "salida", "funcion": "coil", "direccion": 1}],
                                        sensores_analogicos={}, sensores_ocupados=set(), entradas_en_uso=set())

    def test_un_salida_en_el_archivo_de_un_origen_que_no_puede_comandar_se_saltea(self):
        self.guardar([salida()])
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        datos["origenes"][0]["puede_comandar"] = False
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        self.assertEqual(iot_origenes.obtener(self.id)["canales"], [])
        self.assertEqual(iot_origenes.salidas(), ())

    def test_la_eliminacion_y_el_cambio_de_entradas_siguen_igual(self):
        self.guardar([salida(), {"clave": "puerta", "grupo": "entrada_digital"}])
        with self.assertRaises(ValueError):
            self.guardar([salida()], en_uso={f"{self.id}.puerta"})
        iot_origenes.eliminar(self.id, set())
        self.assertIsNone(iot_origenes.obtener(self.id))


class TestIngresoIgnoraLasSalidas(Base):
    def test_una_lectura_dirigida_a_una_salida_se_ignora_y_no_se_guarda(self):
        # Un canal `salida` no es un analógico ni un digital: lo que el ESP32 mande con esa clave no puede convertirse en lectura.
        self.guardar([salida(), {"clave": "puerta", "grupo": "entrada_digital"}])
        r = iot_ingreso.recibir(self.id, {"lecturas": {"bomba": 1, "puerta": 1}}, ahora=datetime(2026, 11, 25, 12, 0, 0))
        self.assertEqual(r, {"recibidas": 1, "ignorados": 1})
        self.assertIsNone(iot_ingreso.ultimo_de_canal(self.id, "bomba"))


if __name__ == "__main__":
    unittest.main()
