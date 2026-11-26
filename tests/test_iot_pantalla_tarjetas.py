"""Las tarjetas de la pantalla ESP32: las mismas de ⚙ Monitoreo IoT, armadas en el servidor (`iot_pantalla.py`).

La pantalla no decide nada: recibe cada tarjeta con sus textos, su color y su dial ya calculados, para que no haya dos criterios.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_pantalla_tarjetas -v
"""
import datetime
import json
import os
import sqlite3
import unittest
from unittest import mock

import iot_canales
import iot_lavado
import iot_monitoreo
import iot_pantalla
import iot_sensores
import iot_vumetros
import lavado_programa
import voz_comandos
from tests.test_iot_origenes_api import Base as BaseApi

AHORA = datetime.datetime(2026, 11, 26, 10, 0, 30)          # jueves
TUNEL = {"CF-Connecting-IP": "203.0.113.9"}


def analogico(clave, label, unidad="°C", minimo=0, maximo=40, **mas):
    return dict({"clave": clave, "label": label, "tipo": "analogico", "unidad": unidad, "min": minimo, "max": maximo}, **mas)


def digital(clave, label, entrada=None, **mas):
    return dict({"clave": clave, "label": label, "tipo": "digital", "entrada": entrada}, **mas)


class Base(BaseApi):
    def setUp(self):
        super().setUp()
        ruta = lambda n: os.path.join(self.dir.name, n)
        self.gateway = ruta("gateway.db")
        iot_lavado._conectar_db(self.gateway).close()
        self.sostenidas, self.pendientes, self.en_lavado = {}, {}, set()
        for p in (mock.patch.object(iot_canales, "_RUTA", ruta("nombres.json")),
                  mock.patch.object(lavado_programa, "_RUTA_CONFIG", ruta("lavado.json")),
                  mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(self.gateway)),
                  mock.patch.object(iot_monitoreo, "_ordeno_activo", return_value=False),
                  mock.patch.object(voz_comandos, "estado", lambda: dict(self.sostenidas)),
                  mock.patch.object(voz_comandos, "apagados_pendientes", lambda: dict(self.pendientes)),
                  mock.patch.object(voz_comandos, "_en_uso_por_lavado", lambda c: c in self.en_lavado)):
            p.start()
            self.addCleanup(p.stop)

    def catalogo(self, *sensores):
        """Escribe el archivo del catálogo con estas claves (`iot_sensores.guardar` inventa las de los sensores nuevos)."""
        with open(iot_sensores._RUTA, "w", encoding="utf-8") as f:
            json.dump({"version": iot_sensores.VERSION, "sensores": list(sensores)}, f, ensure_ascii=False)

    def leer(self, clave, valor, hace_min=0):
        con = sqlite3.connect(iot_monitoreo.RUTA_DB)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS lecturas_sensor (id INTEGER PRIMARY KEY AUTOINCREMENT, sensor TEXT NOT NULL, "
                        "fecha_hora TEXT NOT NULL, valor REAL NOT NULL)")
            con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                        (clave, (AHORA - datetime.timedelta(minutes=hace_min)).isoformat(timespec="seconds"), valor))
            con.commit()
        finally:
            con.close()

    def latido(self, canal, estado, hace_min=0):
        con = sqlite3.connect(iot_monitoreo.RUTA_DB)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS digitales_estado (canal TEXT PRIMARY KEY, fecha_hora TEXT NOT NULL, estado INTEGER NOT NULL)")
            con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                        (canal, (AHORA - datetime.timedelta(minutes=hace_min)).isoformat(timespec="seconds"), int(estado)))
            con.commit()
        finally:
            con.close()

    def limites(self, clave, estilo="digital", **limites):
        iot_vumetros.guardar({clave: dict({"estilo": estilo}, **limites)}, {clave})

    def nombrar(self, nombres):
        iot_canales.guardar(nombres)

    def lavado(self, *etapas):
        lavado_programa.guardar_etapas([{"reles": list(r), "duracion_s": 60} for r in etapas])

    def tarjeta(self, clave):
        por_clave = {t["clave"]: t for t in iot_pantalla.tarjetas(AHORA)["sensores"]}
        return por_clave[clave]


class TestSensores(Base):
    def test_son_los_de_la_web_en_su_orden_y_el_ith_al_final(self):
        self.catalogo(digital("porton", "Portón", "di_3"), analogico("temp_leche", "Temp. leche"), analogico("vacio", "Vacío", "kPa"))
        claves = [t["clave"] for t in iot_pantalla.tarjetas(AHORA)["sensores"]]
        self.assertEqual(claves, ["porton", "temp_leche", "vacio", "ith"])

    def test_un_analogico_que_nunca_leyo_dice_sin_instalar(self):
        self.catalogo(analogico("temp_leche", "Temp. leche"))
        t = self.tarjeta("temp_leche")
        self.assertEqual((t["tipo"], t["label"], t["valor"], t["detalle"]), ("analogico", "Temp. leche", "—", "sin instalar"))
        self.assertEqual((t["chip"], t["zona"], t["sin_datos"], t["historial"], t["dial"]), ("", None, False, True, None))
        self.assertEqual(t["unidad"], "°C")                          # la usa el gráfico del historial de la pantalla

    def test_una_lectura_fresca_con_limites(self):
        self.catalogo(analogico("temp_leche", "Temp. leche"))
        self.limites("temp_leche", amarillo_alto=6, rojo_alto=10)
        self.leer("temp_leche", 4.25)
        t = self.tarjeta("temp_leche")
        self.assertEqual(t["valor"], "4,3°C")                          # como la web: coma, un decimal, sin espacio
        self.assertEqual((t["zona"], t["chip"], t["detalle"]), ("verde", "Normal", "2026-11-26 10:00"))

    def test_los_miles_y_los_enteros_se_escriben_como_en_la_web(self):
        self.catalogo(analogico("caudal", "Caudal", "L", 0, 5000), analogico("vacio", "Vacío", "kPa"))
        self.leer("caudal", 1234.56)
        self.leer("vacio", 42.0)
        self.assertEqual((self.tarjeta("caudal")["valor"], self.tarjeta("vacio")["valor"]), ("1.234,6L", "42kPa"))

    def test_los_textos_de_cada_zona(self):
        self.catalogo(analogico("a", "A"), analogico("r", "R"))
        iot_vumetros.guardar({c: {"estilo": "digital", "amarillo_alto": 6, "rojo_alto": 10} for c in ("a", "r")}, {"a", "r"})
        self.leer("a", 8)
        self.leer("r", 12)
        self.assertEqual((self.tarjeta("a")["zona"], self.tarjeta("a")["chip"]), ("amarillo", "Atención"))
        self.assertEqual((self.tarjeta("r")["zona"], self.tarjeta("r")["chip"]), ("rojo", "Fuera de rango"))

    def test_un_sensor_mudo_no_muestra_su_ultimo_valor_como_actual(self):
        self.catalogo(analogico("temp_leche", "Temp. leche"))
        self.leer("temp_leche", 7, hace_min=180)
        t = self.tarjeta("temp_leche")
        self.assertEqual((t["valor"], t["chip"], t["sin_datos"], t["zona"]), ("—", "Sin datos", True, None))
        self.assertEqual(t["detalle"], "último dato: jue 26/11 07:00 (7°C)")

    def test_uno_con_limites_que_nunca_mando_nada(self):
        self.catalogo(analogico("temp_leche", "Temp. leche"))
        self.limites("temp_leche", rojo_alto=10)
        t = self.tarjeta("temp_leche")
        self.assertEqual((t["chip"], t["detalle"]), ("Sin datos", "nunca mandó datos"))

    def test_el_dial_solo_con_estilo_analogico_y_con_la_aguja_y_las_zonas_como_fracciones(self):
        self.catalogo(analogico("temp_leche", "Temp. leche", minimo=0, maximo=40))
        self.limites("temp_leche", estilo="analogico", amarillo_alto=10, rojo_alto=20)
        self.leer("temp_leche", 8)
        d = self.tarjeta("temp_leche")["dial"]
        self.assertEqual((d["aguja"], d["min_texto"], d["max_texto"]), (0.2, "0", "40"))
        self.assertEqual(d["tramos"], [[0.0, 0.25, "verde"], [0.25, 0.5, "amarillo"], [0.5, 1.0, "rojo"]])

    def test_el_dial_sin_limites_y_sin_lectura(self):
        self.catalogo(analogico("temp_leche", "Temp. leche", minimo=-10, maximo=30))
        self.limites("temp_leche", estilo="analogico")
        d = self.tarjeta("temp_leche")["dial"]
        self.assertEqual((d["aguja"], d["tramos"], d["min_texto"]), (None, [[0.0, 1.0, None]], "-10"))

    def test_la_aguja_no_se_sale_de_la_escala(self):
        self.catalogo(analogico("temp_leche", "Temp. leche", minimo=0, maximo=40))
        self.limites("temp_leche", estilo="analogico")
        self.leer("temp_leche", 90)
        self.assertEqual(self.tarjeta("temp_leche")["dial"]["aguja"], 1.0)

    def test_el_tamano_viaja_con_la_tarjeta(self):
        self.catalogo(analogico("temp_leche", "Temp. leche", tamano="grande"), digital("porton", "Portón", "di_3", tamano="chico"))
        self.assertEqual((self.tarjeta("temp_leche")["tamano"], self.tarjeta("porton")["tamano"]), ("grande", "chico"))

    def test_el_ith_con_temperatura_y_humedad(self):
        self.catalogo(analogico("temp_ambiente", "Temp. ambiente"), analogico("hum_ambiente", "Humedad ambiente", "%", 0, 100))
        self.leer("temp_ambiente", 30)
        self.leer("hum_ambiente", 60)
        t = self.tarjeta("ith")
        self.assertEqual((t["label"], t["valor"], t["historial"]), ("ITH (estrés calórico)", "79,8", True))

    def test_digital_sin_entrada(self):
        self.catalogo(digital("porton", "Portón"))
        t = self.tarjeta("porton")
        self.assertEqual((t["tipo"], t["valor"], t["detalle"], t["historial"]), ("digital", "—", "sin entrada asignada", False))

    def test_digital_con_lectura_muestra_el_texto_y_el_color_del_tambo(self):
        self.catalogo(digital("porton", "Portón", "di_3", rol="indicador", texto_activo="Abierto", texto_inactivo="Cerrado",
                              color_activo="amarillo", color_inactivo="verde"))
        self.latido("di_3", True)
        t = self.tarjeta("porton")
        self.assertEqual((t["valor"], t["zona"], t["chip"], t["detalle"], t["dial"]), ("Abierto", "amarillo", "", "2026-11-26 10:00", None))

    def test_digital_con_entrada_y_sin_ninguna_lectura(self):
        self.catalogo(digital("porton", "Portón", "di_3"))             # sin color: no es «sin datos», todavía no llegó nada
        self.assertEqual(self.tarjeta("porton")["detalle"], "esperando lectura")

    def test_digital_mudo_dice_cual_fue_el_ultimo_estado(self):
        self.catalogo(digital("alarma", "Falla bomba", "di_3", rol="alarma"))
        self.latido("di_3", True, hace_min=90)
        t = self.tarjeta("alarma")
        self.assertEqual((t["valor"], t["chip"], t["sin_datos"], t["zona"]), ("—", "Sin datos", True, None))
        self.assertEqual(t["detalle"], "último dato: jue 26/11 08:30 (ALARMA)")


class TestSalidas(Base):
    def salidas(self):
        return iot_pantalla.tarjetas(AHORA)["salidas"]

    def test_sin_nombres_ni_lavado_no_hay_ninguna(self):
        self.assertEqual(self.salidas(), [])

    def test_las_nombradas_y_las_del_lavado_en_orden_de_numero(self):
        self.nombrar({"do_11": "Bomba de agua", "di_3": "Portón", "ai_1": "Sonda"})
        self.lavado(["do_2", "do_1"], ["do_2"])
        self.assertEqual([(s["clave"], s["label"]) for s in self.salidas()],
                         [("do_1", "Actuador 1"), ("do_2", "Actuador 2"), ("do_11", "Bomba de agua")])
        self.assertEqual([s["ocupada"] for s in self.salidas()], [None, None, None])

    def test_una_prendida_por_voz_se_ve_aunque_no_tenga_nombre(self):
        """Un relé encendido no puede quedar escondido de la pantalla."""
        self.sostenidas = {"do_7": "2026-11-26T09:00:00"}
        (s,) = self.salidas()
        self.assertEqual(s["clave"], "do_7")
        self.assertIn("voz", s["ocupada"])

    def test_una_que_usa_el_lavado_en_curso_dice_que_esta_ocupada(self):
        self.lavado(["do_1"])
        self.en_lavado = {"do_1"}
        self.assertIn("lavado", self.salidas()[0]["ocupada"])


class TestEndpoint(Base):
    def test_es_publico_y_trae_el_estado_los_sensores_y_las_salidas(self):
        self.catalogo(analogico("temp_leche", "Temp. leche"))
        self.nombrar({"do_3": "Bomba"})
        r = self.publico.get("/api/iot/pantalla/tarjetas?tambo=ponderosa")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        j = r.get_json()
        self.assertEqual(sorted(j), ["desde", "estado", "salidas", "sensores"])
        self.assertEqual((j["estado"], j["desde"]), ("APAGADO", ""))
        self.assertEqual([t["clave"] for t in j["sensores"]], ["temp_leche", "ith"])
        self.assertEqual([s["label"] for s in j["salidas"]], ["Bomba"])

    def test_la_hora_del_estado_se_escribe_como_en_la_web(self):
        with mock.patch.object(iot_monitoreo, "estado_sistema", return_value={"estado": "LAVANDO", "desde": "2026-11-26T09:15:42"}):
            j = self.publico.get("/api/iot/pantalla/tarjetas").get_json()
        self.assertEqual((j["estado"], j["desde"]), ("LAVANDO", "2026-11-26 09:15"))

    def test_entra_en_el_buffer_de_la_pantalla_con_el_catalogo_lleno(self):
        """La pantalla reserva 64 KB para esta respuesta: con 59 sensores con dial (el máximo es 60) y 32 salidas tiene que entrar."""
        self.catalogo(*[analogico("s%d" % i, "Sensor de nombre bien largo %02d" % i) for i in range(59)])
        iot_vumetros.guardar({"s%d" % i: {"estilo": "analogico", "rojo_bajo": 1, "amarillo_bajo": 2, "amarillo_alto": 30, "rojo_alto": 35} for i in range(59)},
                             {"s%d" % i for i in range(59)})
        self.nombrar({"do_%d" % i: "Salida con un nombre largo %02d" % i for i in range(1, 33)})
        r = self.publico.get("/api/iot/pantalla/tarjetas")
        self.assertLess(len(r.get_data()), 60 * 1024)


class TestPulsoDesdeLaPantalla(Base):
    def pedidos(self):
        con = sqlite3.connect(self.gateway)
        try:
            return [f[0] for f in con.execute("SELECT canal FROM comandos_actuador ORDER BY id")]
        finally:
            con.close()

    def pulsar(self, canal, **kw):
        return self.publico.post("/api/iot/pantalla/actuador", json={"canal": canal}, **kw)

    def test_una_salida_libre_se_encola(self):
        self.assertEqual(self.pulsar("do_1").status_code, 202)
        self.assertEqual(self.pedidos(), ["do_1"])

    def test_una_salida_ocupada_no_se_pulsa(self):
        """Un pulso es «prender, esperar, apagar»: en una salida que el lavado o la voz tienen prendida, la apagaría."""
        self.en_lavado = {"do_1"}
        self.sostenidas = {"do_2": "2026-11-26T09:00:00"}
        for canal in ("do_1", "do_2"):
            r = self.pulsar(canal)
            self.assertEqual(r.status_code, 409, canal)
            self.assertTrue(r.get_json()["error"])
        self.assertEqual(self.pedidos(), [])

    def test_sigue_sin_poder_llamarse_desde_internet(self):
        self.assertEqual(self.pulsar("do_1", headers=TUNEL).status_code, 403)
        self.assertEqual(self.pedidos(), [])

    def test_un_canal_que_no_es_salida_sigue_dando_400(self):
        self.assertEqual(self.pulsar("di_1").status_code, 400)


if __name__ == "__main__":
    unittest.main()
