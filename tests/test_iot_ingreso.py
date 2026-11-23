"""Ingreso de lecturas por HTTP (ESP32 / ChirpStack): lo que se escribe en las tablas del Monitoreo IoT.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_ingreso -v
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

import iot_ingreso
import iot_origenes
import iot_sensores

AHORA = datetime(2026, 11, 25, 12, 0, 0)
CATALOGO = {"temp_corral": (-10, 45), "hum_corral": (0, 100)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.invertidas = {}
        for p in (mock.patch.object(iot_origenes, "_RUTA", os.path.join(self.dir.name, "iot_origenes.json")),
                  mock.patch.object(iot_sensores, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json")),
                  mock.patch.object(iot_ingreso, "RUTA_DB", os.path.join(self.dir.name, "iot_sensores.db")),
                  mock.patch.object(iot_ingreso.iot_lavado, "entradas_invertidas", lambda: self.invertidas)):
            p.start()
            self.addCleanup(p.stop)
        self.origen, _ = iot_origenes.crear("esp32", "generico", 120)
        self.id = self.origen["id"]
        self.fijar_creado()
        self.canales([
            {"clave": "temp", "grupo": "entrada_analogica", "nombre": "Temp", "sensor": "temp_corral"},
            {"clave": "hum", "grupo": "entrada_analogica", "nombre": "Humedad (cruda)", "factor": 0.1, "offset": 5},
            {"clave": "puerta", "grupo": "entrada_digital", "nombre": "Puerta"},
        ])

    def fijar_creado(self, creado="2026-01-01T00:00:00"):
        """Las pruebas usan un «ahora» inventado: los orígenes se crearon antes, en una fecha fija."""
        with open(iot_origenes._RUTA, encoding="utf-8") as f:
            datos = json.load(f)
        for o in datos["origenes"]:
            o["creado"] = creado
        with open(iot_origenes._RUTA, "w", encoding="utf-8") as f:
            json.dump(datos, f)

    def canales(self, canales, id_=None, formato=None):
        iot_origenes.guardar_origen(id_ or self.id, "esp32", formato or iot_origenes.obtener(id_ or self.id)["formato"], 120,
                                    canales, sensores_analogicos=CATALOGO, sensores_ocupados=set(), entradas_en_uso=set())

    def recibir(self, lecturas, hora=None, ahora=AHORA, **kw):
        cuerpo = {"lecturas": lecturas}
        if hora is not None:
            cuerpo["hora"] = hora
        return iot_ingreso.recibir(self.id, cuerpo, ahora=ahora, **kw)

    def filas(self, sql, *args):
        con = sqlite3.connect(iot_ingreso.RUTA_DB)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def lecturas(self, sensor="temp_corral"):
        return self.filas("SELECT fecha_hora, valor FROM lecturas_sensor WHERE sensor=? ORDER BY fecha_hora", sensor)

    def eventos(self, canal=None):
        return self.filas("SELECT fecha_hora, estado FROM eventos_di WHERE canal=? ORDER BY id", canal or f"{self.id}.puerta")

    def estado_di(self, canal=None):
        return self.filas("SELECT fecha_hora, estado FROM digitales_estado WHERE canal=?", canal or f"{self.id}.puerta")


class TestAnalogicos(Base):
    def test_guarda_la_lectura_del_sensor_con_la_hora_del_servidor(self):
        r = self.recibir({"temp": 21.4})
        self.assertEqual(r, {"recibidas": 1, "ignorados": 0})
        self.assertEqual(self.lecturas(), [("2026-11-25T12:00:00", 21.4)])

    def test_factor_y_offset(self):
        self.recibir({"hum": 500})
        self.assertEqual(iot_ingreso.ultimo_de_canal(self.id, "hum")["valor"], 55)

    def test_un_canal_sin_sensor_solo_anota_el_ultimo_valor(self):
        self.recibir({"hum": 500})
        self.assertEqual(self.filas("SELECT COUNT(*) FROM lecturas_sensor"), [(0,)])
        self.assertEqual(iot_ingreso.ultimo_de_canal(self.id, "hum"),
                         {"valor": 55, "fecha_hora": "2026-11-25T12:00:00"})

    def test_ultimo_de_canal_sin_lecturas(self):
        self.assertIsNone(iot_ingreso.ultimo_de_canal(self.id, "temp"))

    def test_un_canal_no_declarado_se_ignora(self):
        self.assertEqual(self.recibir({"temp": 20, "otra_cosa": 5}), {"recibidas": 1, "ignorados": 1})

    def test_lecturas_basura_no_se_guardan(self):
        for malo in (float("nan"), float("inf"), float("-inf"), "21", True, False, None, [1], {"a": 1}):
            with self.subTest(valor=malo):
                self.assertEqual(self.recibir({"temp": malo}), {"recibidas": 0, "ignorados": 1})
        self.assertEqual(self.lecturas(), [])
        self.assertIsNone(iot_ingreso.ultimo_de_canal(self.id, "temp"))

    def test_una_basura_no_frena_a_las_lecturas_buenas_del_mismo_envio(self):
        self.assertEqual(self.recibir({"temp": "x", "hum": 100}), {"recibidas": 1, "ignorados": 1})

    def test_dos_lecturas_a_menos_de_30_segundos_guardan_una(self):
        self.recibir({"temp": 20}, hora=(AHORA - timedelta(seconds=10)).isoformat())
        self.recibir({"temp": 21}, hora=AHORA.isoformat())
        self.assertEqual(len(self.lecturas()), 1)

    def test_con_31_segundos_guarda_las_dos(self):
        self.recibir({"temp": 20}, hora=(AHORA - timedelta(seconds=31)).isoformat())
        self.recibir({"temp": 21}, hora=AHORA.isoformat())
        self.assertEqual(len(self.lecturas()), 2)

    def test_la_vieja_que_llega_despues_tambien_cuenta_como_cercana(self):
        self.recibir({"temp": 21}, hora=AHORA.isoformat())
        self.recibir({"temp": 20}, hora=(AHORA - timedelta(seconds=10)).isoformat())
        self.assertEqual(len(self.lecturas()), 1)

    def test_el_ultimo_valor_no_retrocede_si_llega_una_lectura_vieja(self):
        self.recibir({"hum": 600}, hora=AHORA.isoformat())
        self.recibir({"hum": 100}, hora=(AHORA - timedelta(hours=1)).isoformat())
        self.assertEqual(iot_ingreso.ultimo_de_canal(self.id, "hum")["valor"], 65)


class TestDigitales(Base):
    def test_primer_estado_escribe_el_latido_y_un_evento(self):
        self.assertEqual(self.recibir({"puerta": 1}), {"recibidas": 1, "ignorados": 0})
        self.assertEqual(self.estado_di(), [("2026-11-25T12:00:00", 1)])
        self.assertEqual(self.eventos(), [("2026-11-25T12:00:00", 1)])

    def test_el_mismo_estado_solo_late(self):
        self.recibir({"puerta": 1})
        self.recibir({"puerta": True}, ahora=AHORA + timedelta(minutes=5))
        self.assertEqual(len(self.eventos()), 1)
        self.assertEqual(self.estado_di(), [("2026-11-25T12:05:00", 1)])

    def test_un_cambio_agrega_un_evento(self):
        self.recibir({"puerta": 1})
        self.recibir({"puerta": 0}, ahora=AHORA + timedelta(minutes=1))
        self.assertEqual(self.eventos(), [("2026-11-25T12:00:00", 1), ("2026-11-25T12:01:00", 0)])

    def test_cualquier_numero_distinto_de_cero_es_activo(self):
        self.recibir({"puerta": 2.5})
        self.assertEqual(self.estado_di()[0][1], 1)
        self.recibir({"puerta": 0.0}, ahora=AHORA + timedelta(minutes=1))
        self.assertEqual(self.estado_di()[0][1], 0)

    def test_basura_en_una_digital(self):
        for malo in ("1", "on", "true", None, float("nan"), float("inf"), [1]):
            with self.subTest(valor=malo):
                self.assertEqual(self.recibir({"puerta": malo}), {"recibidas": 0, "ignorados": 1})
        self.assertEqual(self.estado_di(), [])
        self.assertEqual(self.eventos(), [])

    def test_el_contacto_normalmente_cerrado_invierte_el_estado(self):
        self.invertidas = {f"{self.id}.puerta": True}
        self.recibir({"puerta": 1})
        self.assertEqual(self.estado_di()[0][1], 0)
        self.assertEqual(self.eventos()[0][1], 0)

    def test_lectura_anterior_al_estado_guardado_se_ignora(self):
        self.recibir({"puerta": 1}, hora=AHORA.isoformat())
        r = self.recibir({"puerta": 0}, hora=(AHORA - timedelta(minutes=10)).isoformat(), ahora=AHORA + timedelta(seconds=5))
        self.assertEqual(r, {"recibidas": 0, "ignorados": 1})
        self.assertEqual(self.estado_di(), [("2026-11-25T12:00:00", 1)])
        self.assertEqual(len(self.eventos()), 1)

    def test_dos_envios_a_la_vez_con_el_mismo_cambio_dejan_un_solo_evento(self):
        errores = []

        def llamar():
            try:
                self.recibir({"puerta": 1})
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos = [threading.Thread(target=llamar) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(self.eventos(), [("2026-11-25T12:00:00", 1)])


class TestHora(Base):
    def test_hora_en_el_futuro_se_reemplaza_por_la_del_servidor(self):
        self.recibir({"temp": 20}, hora=(AHORA + timedelta(days=1)).isoformat())
        self.assertEqual(self.lecturas()[0][0], "2026-11-25T12:00:00")

    def test_hora_invalida_se_reemplaza_por_la_del_servidor(self):
        for mala in ("basura", "", 12345, {"a": 1}, "2026-13-45T99:00:00"):
            with self.subTest(hora=mala):
                self.recibir({"puerta": 1}, hora=mala)
                self.assertEqual(self.estado_di()[0][0], "2026-11-25T12:00:00")

    def test_hora_con_zona_se_convierte_a_hora_local_sin_zona(self):
        esperada = datetime.fromisoformat("2026-11-25T08:00:00-03:00").astimezone().replace(tzinfo=None)
        self.recibir({"temp": 20}, hora="2026-11-25T08:00:00-03:00", ahora=AHORA + timedelta(days=2))
        self.assertEqual(self.lecturas()[0][0], esperada.isoformat(timespec="seconds"))

    def test_hora_con_Z(self):
        esperada = datetime.fromisoformat("2026-11-25T08:00:00+00:00").astimezone().replace(tzinfo=None)
        self.recibir({"temp": 20}, hora="2026-11-25T08:00:00Z", ahora=AHORA + timedelta(days=2))
        self.assertEqual(self.lecturas()[0][0], esperada.isoformat(timespec="seconds"))

    def test_hora_sin_zona_es_hora_local_del_servidor(self):
        self.recibir({"temp": 20}, hora="2026-11-25T09:30:15.789")
        self.assertEqual(self.lecturas()[0][0], "2026-11-25T09:30:15")

    def test_una_hora_anterior_a_la_creacion_del_origen_es_imposible_y_se_reemplaza(self):
        # Un ESP32 sin NTP manda la hora de arranque (1970): no puede ser anterior a la existencia del origen.
        for vieja in ("1970-01-01T00:00:12", "2025-12-31T23:59:59"):
            with self.subTest(hora=vieja):
                self.recibir({"puerta": 1}, hora=vieja)
                self.assertEqual(self.estado_di()[0][0], "2026-11-25T12:00:00")

    def test_hora_pasada_se_respeta(self):
        self.recibir({"temp": 20}, hora="2026-11-20T03:00:00")
        self.assertEqual(self.lecturas()[0][0], "2026-11-20T03:00:00")


class TestCuerpoMalFormado(Base):
    def test_lecturas_que_no_es_un_objeto(self):
        for malo in ([1, 2], "texto", None, 5):
            with self.subTest(lecturas=malo):
                with self.assertRaises(iot_ingreso.IngresoError):
                    iot_ingreso.recibir(self.id, {"lecturas": malo}, ahora=AHORA)
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir(self.id, {}, ahora=AHORA)

    def test_cuerpo_que_no_es_un_objeto(self):
        for malo in ([], "x", None, 3):
            with self.subTest(cuerpo=malo):
                with self.assertRaises(iot_ingreso.IngresoError):
                    iot_ingreso.recibir(self.id, malo, ahora=AHORA)

    def test_origen_inexistente(self):
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir("no_existe", {"lecturas": {}}, ahora=AHORA)
        self.assertEqual(iot_ingreso.estado("no_existe")["ultima_recepcion"], None)

    def test_el_error_queda_en_el_estado(self):
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir(self.id, {"lecturas": [1]}, ahora=AHORA)
        e = iot_ingreso.estado(self.id)
        self.assertIn("lecturas", e["ultimo_error"])
        self.assertEqual(e["error_fecha"], "2026-11-25T12:00:00")

    def test_anotar_error_directo(self):
        iot_ingreso.anotar_error(self.id, "el cuerpo no es JSON", ahora=AHORA)
        self.assertEqual(iot_ingreso.estado(self.id)["ultimo_error"], "el cuerpo no es JSON")

    def test_lecturas_vacias_es_valido(self):
        self.assertEqual(self.recibir({}), {"recibidas": 0, "ignorados": 0})


class TestOlvidar(Base):
    def test_olvidar_borra_el_estado_derivado_de_ese_origen_y_no_toca_a_otros(self):
        self.recibir({"temp": 20, "hum": 100, "puerta": 1})
        otro, _ = iot_origenes.crear("esp32x", "generico", 60)     # su id empieza igual: no se puede borrar por prefijo suelto
        self.fijar_creado()
        iot_origenes.guardar_origen(otro["id"], "esp32x", "generico", 60, [{"clave": "puerta", "grupo": "entrada_digital"}],
                                    sensores_analogicos={}, sensores_ocupados=set(), entradas_en_uso=set())
        iot_ingreso.recibir(otro["id"], {"lecturas": {"puerta": 1}}, ahora=AHORA)
        iot_ingreso.olvidar(self.id)
        self.assertIsNone(iot_ingreso.estado(self.id)["ultima_recepcion"])
        self.assertIsNone(iot_ingreso.ultimo_de_canal(self.id, "hum"))
        self.assertEqual(self.estado_di(), [])
        self.assertEqual(self.estado_di(f"{otro['id']}.puerta"), [("2026-11-25T12:00:00", 1)])
        self.assertEqual(len(self.eventos()), 1, "el historial de cambios no se borra")
        self.assertIsNotNone(iot_ingreso.estado(otro["id"])["ultima_recepcion"])


class TestEstado(Base):
    def test_sin_nada_todo_es_none(self):
        self.assertEqual(iot_ingreso.estado(self.id),
                         {"ultima_recepcion": None, "recibidas": None, "ignorados": None, "ultimo_error": None,
                          "error_fecha": None})

    def test_tras_una_llamada_buena(self):
        self.recibir({"temp": 20, "otra": 1})
        self.recibir({"temp": 21}, ahora=AHORA + timedelta(minutes=1))
        e = iot_ingreso.estado(self.id)
        self.assertEqual((e["ultima_recepcion"], e["recibidas"], e["ignorados"], e["ultimo_error"]),
                         ("2026-11-25T12:01:00", 2, 1, None))


class TestChirpStack(Base):
    def setUp(self):
        super().setUp()
        self.origen_cs, _ = iot_origenes.crear("Nodo lora", "chirpstack", 90)
        self.cs = self.origen_cs["id"]
        self.fijar_creado()
        iot_origenes.guardar_origen(self.cs, "Nodo lora", "chirpstack", 90, [
            {"clave": "temperature", "grupo": "entrada_analogica", "sensor": "hum_corral"},
            {"clave": "humidity", "grupo": "entrada_analogica"},
            {"clave": "door", "grupo": "entrada_digital"},
        ], sensores_analogicos=CATALOGO, sensores_ocupados=set(), entradas_en_uso=set())

    def up(self, objeto, tiempo="2026-11-25T11:00:00+00:00"):
        c = {"deviceInfo": {"devEui": "abc"}, "object": objeto}
        if tiempo:
            c["time"] = tiempo
        return c

    def test_un_up_guarda_los_valores_del_objeto_con_la_hora_del_evento(self):
        r = iot_ingreso.recibir(self.cs, self.up({"temperature": 21.4, "humidity": 55}), evento="up",
                                ahora=AHORA + timedelta(days=1))
        self.assertEqual(r, {"recibidas": 2, "ignorados": 0})
        esperada = datetime.fromisoformat("2026-11-25T11:00:00+00:00").astimezone().replace(tzinfo=None)
        self.assertEqual(self.lecturas("hum_corral"), [(esperada.isoformat(timespec="seconds"), 21.4)])

    def test_un_valor_booleano_y_los_anidados(self):
        r = iot_ingreso.recibir(self.cs, self.up({"door": True, "temperature": {"a": 1}}), evento="up",
                                ahora=AHORA + timedelta(days=1))
        self.assertEqual(r, {"recibidas": 1, "ignorados": 1})

    def test_otros_eventos_se_ignoran_sin_error(self):
        for evento in ("join", "status", "ack", "txack", "log", "location"):
            with self.subTest(evento=evento):
                self.assertEqual(iot_ingreso.recibir(self.cs, {"deviceInfo": {}}, evento=evento, ahora=AHORA),
                                 {"recibidas": 0, "ignorados": 0})
        self.assertIsNone(iot_ingreso.estado(self.cs)["ultimo_error"])

    def test_los_eventos_sin_lecturas_no_tocan_el_estado_del_origen(self):
        for evento in ("join", "status"):
            iot_ingreso.recibir(self.cs, {"deviceInfo": {}}, evento=evento, ahora=AHORA)
        self.assertIsNone(iot_ingreso.estado(self.cs)["ultima_recepcion"])

    def test_un_up_de_solo_comandos_mac_no_es_un_error(self):
        # fPort 0: el uplink lleva comandos de la red LoRaWAN, no datos del nodo: nunca trae `object`.
        r = iot_ingreso.recibir(self.cs, {"deviceInfo": {}, "fPort": 0}, evento="up", ahora=AHORA)
        self.assertEqual(r, {"recibidas": 0, "ignorados": 0})
        self.assertIsNone(iot_ingreso.estado(self.cs)["ultimo_error"])

    def test_sin_evento_pero_con_deviceinfo_se_toma_como_up(self):
        r = iot_ingreso.recibir(self.cs, self.up({"humidity": 40}), ahora=AHORA + timedelta(days=1))
        self.assertEqual(r, {"recibidas": 1, "ignorados": 0})

    def test_sin_evento_ni_deviceinfo_es_un_error_legible(self):
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir(self.cs, {"lecturas": {"humidity": 3}}, ahora=AHORA)

    def test_un_up_sin_object_es_un_error_con_el_texto_de_la_causa(self):
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir(self.cs, {"deviceInfo": {}, "data": "AQID"}, evento="up", ahora=AHORA)
        self.assertEqual(iot_ingreso.estado(self.cs)["ultimo_error"],
                         "el nodo no manda el payload decodificado (falta el códec en ChirpStack)")

    def test_un_up_sin_time_usa_la_hora_del_servidor(self):
        iot_ingreso.recibir(self.cs, self.up({"temperature": 3}, tiempo=None), evento="up", ahora=AHORA)
        self.assertEqual(self.lecturas("hum_corral")[0][0], "2026-11-25T12:00:00")


if __name__ == "__main__":
    unittest.main()
