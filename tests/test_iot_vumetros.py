"""Vúmetros del panel IoT: estilo (digital o analógico), zonas de color por sensor y el historial al hacer clic.

Cada sensor tiene 4 límites OPCIONALES: rojo si baja de X, amarillo si baja de Y, amarillo si sube de Z, rojo si sube de W.
El verde es lo que queda en el medio. Sin ningún límite cargado la tarjeta no tiene color: no se inventan umbrales.

Sin red ni hardware: las bases son archivos temporales.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_vumetros -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_lavado
import iot_monitoreo
import iot_vumetros as iv

SENSORES = {"temp_leche", "temp_ambiente", "hum_ambiente", "vacio_general", "ith"}
AHORA = datetime.datetime(2026, 11, 22, 12, 0, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(iv, "_RUTA", os.path.join(self.dir.name, "iot_vumetros.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", os.path.join(self.dir.name, "iot.db"))):
            p.start()
            self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(os.path.join(self.dir.name, "iot.db"))
        self.addCleanup(self.con.close)

    def lectura(self, sensor, valor, cuando):
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         (sensor, cuando.isoformat(timespec="seconds"), valor))
        self.con.commit()


class TestZona(unittest.TestCase):
    TEMP_LECHE = {"amarillo_alto": 4, "rojo_alto": 6}                       # solo límites de arriba
    VACIO = {"rojo_bajo": 44, "amarillo_bajo": 48, "amarillo_alto": 52, "rojo_alto": 56}

    def test_sin_ningun_limite_no_hay_color(self):
        self.assertIsNone(iv.zona(5, {}))
        self.assertIsNone(iv.zona(5, {"estilo": "analogico", "min": 0, "max": 10}))

    def test_sin_lectura_no_hay_zona(self):
        self.assertIsNone(iv.zona(None, self.VACIO))

    def test_solo_limites_superiores(self):
        self.assertEqual(iv.zona(2, self.TEMP_LECHE), "verde")
        self.assertEqual(iv.zona(4, self.TEMP_LECHE), "verde")       # justo en el límite todavía es verde
        self.assertEqual(iv.zona(4.1, self.TEMP_LECHE), "amarillo")
        self.assertEqual(iv.zona(6, self.TEMP_LECHE), "amarillo")
        self.assertEqual(iv.zona(6.1, self.TEMP_LECHE), "rojo")

    def test_un_valor_muy_bajo_no_es_rojo_si_no_hay_limite_inferior(self):
        self.assertEqual(iv.zona(-30, self.TEMP_LECHE), "verde")

    def test_banda_completa_con_los_cuatro_limites(self):
        casos = [(40, "rojo"), (43.9, "rojo"), (44, "amarillo"), (47.9, "amarillo"), (48, "verde"), (50, "verde"),
                 (52, "verde"), (52.1, "amarillo"), (56, "amarillo"), (56.1, "rojo"), (70, "rojo")]
        for valor, esperada in casos:
            with self.subTest(valor=valor):
                self.assertEqual(iv.zona(valor, self.VACIO), esperada)

    def test_con_un_solo_limite_amarillo_no_hay_rojo(self):
        self.assertEqual(iv.zona(99, {"amarillo_alto": 10}), "amarillo")


class TestGuardar(Base):
    def test_guardar_y_cargar_dan_lo_mismo(self):
        cfg = {"estilo": "analogico", "min": 0, "max": 10, "rojo_bajo": None, "amarillo_bajo": None,
               "amarillo_alto": 4, "rojo_alto": 6}
        iv.guardar({"temp_leche": cfg}, SENSORES)
        self.assertEqual(iv.cargar()["temp_leche"], cfg)

    def test_sin_archivo_no_hay_nada_configurado(self):
        self.assertEqual(iv.cargar(), {})

    def test_config_de_un_sensor_sin_configurar_es_digital_y_sin_limites(self):
        c = iv.config_de("temp_leche")
        self.assertEqual(c["estilo"], "digital")
        self.assertTrue(all(c[k] is None for k in ("min", "max", "rojo_bajo", "amarillo_bajo", "amarillo_alto",
                                                   "rojo_alto")))

    def test_una_fila_igual_a_los_valores_por_defecto_no_se_guarda(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "min": "", "max": "", "amarillo_alto": ""}}, SENSORES)
        self.assertEqual(iv.cargar(), {})

    def test_los_numeros_llegan_como_texto_del_formulario(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "amarillo_alto": "4", "rojo_alto": "6.5"}}, SENSORES)
        c = iv.cargar()["temp_leche"]
        self.assertEqual((c["amarillo_alto"], c["rojo_alto"]), (4, 6.5))

    def test_el_ith_se_puede_configurar(self):
        iv.guardar({"ith": {"estilo": "analogico", "amarillo_alto": 72, "rojo_alto": 80}}, SENSORES)
        self.assertEqual(iv.cargar()["ith"]["rojo_alto"], 80)

    def test_sensor_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            iv.guardar({"no_existe": {"estilo": "digital", "rojo_alto": 5}}, SENSORES)

    def test_estilo_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            iv.guardar({"temp_leche": {"estilo": "barra"}}, SENSORES)

    def test_limites_fuera_de_orden_se_rechazan(self):
        malos = [{"amarillo_alto": 6, "rojo_alto": 4},                       # el rojo de arriba antes que el amarillo
                 {"rojo_bajo": 50, "amarillo_bajo": 40},                    # el rojo de abajo por encima del amarillo
                 {"amarillo_bajo": 10, "amarillo_alto": 5}]                 # la banda verde al revés
        for m in malos:
            with self.subTest(m=m), self.assertRaises(ValueError):
                iv.guardar({"temp_leche": {"estilo": "digital", **m}}, SENSORES)

    def test_el_error_de_limites_habla_con_los_nombres_de_la_pantalla(self):
        with self.assertRaises(ValueError) as cm:
            iv.guardar({"temp_leche": {"estilo": "digital", "amarillo_alto": 30, "rojo_alto": 25}}, SENSORES)
        self.assertIn("«Amarillo si sube de» (30)", str(cm.exception))
        self.assertIn("«Rojo si sube de» (25)", str(cm.exception))

    def test_limites_iguales_son_validos(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "amarillo_alto": 4, "rojo_alto": 4}}, SENSORES)
        self.assertEqual(iv.cargar()["temp_leche"]["rojo_alto"], 4)

    def test_escala_con_minimo_mayor_o_igual_al_maximo_se_rechaza(self):
        with self.assertRaises(ValueError):
            iv.guardar({"temp_leche": {"estilo": "analogico", "min": 10, "max": 10}}, SENSORES)

    def test_un_numero_que_no_es_numero_se_rechaza(self):
        with self.assertRaises(ValueError):
            iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": "abc"}}, SENSORES)

    def test_un_guardado_que_falla_no_pisa_lo_anterior(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": 6}}, SENSORES)
        with self.assertRaises(ValueError):
            iv.guardar({"temp_leche": {"estilo": "barra"}}, SENSORES)
        self.assertEqual(iv.cargar()["temp_leche"]["rojo_alto"], 6)


class TestLecturasActuales(Base):
    def test_cada_sensor_trae_su_estilo_limites_y_zona(self):
        self.lectura("temp_leche", 5.0, AHORA)
        iv.guardar({"temp_leche": {"estilo": "analogico", "amarillo_alto": 4, "rojo_alto": 6}}, SENSORES)
        s = {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}["temp_leche"]
        self.assertEqual((s["estilo"], s["zona"]), ("analogico", "amarillo"))
        self.assertEqual((s["limites"]["amarillo_alto"], s["limites"]["rojo_alto"]), (4, 6))

    def test_un_sensor_sin_configurar_es_digital_sin_zona(self):
        self.lectura("temp_ambiente", 20.0, AHORA)
        s = {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}["temp_ambiente"]
        self.assertEqual((s["estilo"], s["zona"]), ("digital", None))

    def test_un_sensor_sin_lectura_no_tiene_zona_aunque_tenga_limites(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": 6}}, SENSORES)
        s = {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}["temp_leche"]
        self.assertIsNone(s["valor"])
        self.assertIsNone(s["zona"])

    def test_la_escala_propia_pisa_la_del_sensor_y_sin_ella_queda_la_de_siempre(self):
        iv.guardar({"temp_leche": {"estilo": "analogico", "min": 2, "max": 12}}, SENSORES)
        lect = {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}
        self.assertEqual((lect["temp_leche"]["min"], lect["temp_leche"]["max"]), (2, 12))
        self.assertEqual((lect["temp_lavado"]["min"], lect["temp_lavado"]["max"]), (0, 90))

    def test_el_ith_tambien_trae_estilo_y_zona(self):
        self.lectura("temp_ambiente", 28.0, AHORA)
        self.lectura("hum_ambiente", 70.0, AHORA)
        iv.guardar({"ith": {"estilo": "analogico", "amarillo_alto": 68, "rojo_alto": 80}}, SENSORES)
        ith = {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}["ith"]
        self.assertEqual(ith["estilo"], "analogico")
        self.assertEqual(ith["valor"], 78.4)             # ITH de 28 °C y 70 %: entre 68 y 80
        self.assertEqual(ith["zona"], "amarillo")


class TestSensorMudo(Base):
    """Un sensor que dejó de mandar datos NO muestra su última medición: «Sin datos» y alerta."""

    def sensor(self, clave="temp_leche"):
        return {x["clave"]: x for x in iot_monitoreo.lecturas_actuales(AHORA)}[clave]

    def test_con_una_lectura_reciente_funciona_normal(self):
        self.lectura("temp_leche", 5.0, AHORA - datetime.timedelta(minutes=2))
        s = self.sensor()
        self.assertEqual((s["valor"], s["sin_datos"]), (5.0, False))

    def test_pasada_la_ventana_el_valor_desaparece_y_el_ultimo_queda_solo_para_informar(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": 6}}, SENSORES)
        hace = iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN + 1
        self.lectura("temp_leche", 7.5, AHORA - datetime.timedelta(minutes=hace))
        s = self.sensor()
        self.assertIsNone(s["valor"])
        self.assertIsNone(s["zona"])                       # tampoco se pinta de rojo con un valor viejo
        self.assertTrue(s["sin_datos"])
        self.assertEqual((s["ultimo_valor"], s["sin_datos_hace_min"]), (7.5, hace))

    def test_justo_en_el_limite_de_la_ventana_todavia_vale(self):
        self.lectura("temp_leche", 5.0, AHORA - datetime.timedelta(minutes=iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN))
        self.assertFalse(self.sensor()["sin_datos"])

    def test_sin_lectura_ni_limites_es_sin_instalar_no_una_falla(self):
        s = self.sensor()
        self.assertEqual((s["valor"], s["sin_datos"]), (None, False))

    def test_sin_lectura_pero_con_limites_es_sin_datos(self):
        iv.guardar({"temp_leche": {"estilo": "digital", "rojo_alto": 6}}, SENSORES)
        self.assertTrue(self.sensor()["sin_datos"])

    def test_el_ith_esta_mudo_si_lo_esta_la_temperatura_o_la_humedad(self):
        self.lectura("temp_ambiente", 28.0, AHORA)
        self.lectura("hum_ambiente", 70.0, AHORA - datetime.timedelta(hours=3))
        ith = self.sensor("ith")
        self.assertTrue(ith["sin_datos"])
        self.assertIsNone(ith["valor"])

    def test_la_pantalla_esp32_avisa_que_el_sensor_esta_mudo(self):
        self.lectura("temp_leche", 7.5, datetime.datetime.now() - datetime.timedelta(hours=3))
        with mock.patch.object(app.iot_monitoreo, "estado_sistema", return_value={"estado": "ORDENIO", "desde": None}):
            d = app.app.test_client().get("/api/iot/pantalla").get_json()
        self.assertEqual((d["sensores"]["temp_leche"]["valor"], d["sensores"]["temp_leche"]["sin_datos"]), (None, True))


class TestHistorial(Base):
    def test_rango_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            iot_monitoreo.historial("temp_leche", "siglo", AHORA)

    def test_un_dia_son_baldes_de_10_minutos_con_promedio_minimo_y_maximo(self):
        base = AHORA - datetime.timedelta(hours=2)
        for i, v in enumerate([4.0, 6.0, 8.0]):           # tres lecturas dentro del mismo balde de 10 min
            self.lectura("temp_leche", v, base + datetime.timedelta(minutes=i))
        self.lectura("temp_leche", 10.0, base + datetime.timedelta(minutes=30))
        h = iot_monitoreo.historial("temp_leche", "dia", AHORA)
        self.assertEqual(h["balde_s"], 600)
        self.assertEqual(len(h["puntos"]), 2)
        p = h["puntos"][0]
        self.assertEqual((p["prom"], p["min"], p["max"]), (6.0, 4.0, 8.0))
        self.assertEqual(h["resumen"], {"min": 4.0, "prom": 7.0, "max": 10.0, "n": 4})

    def test_los_puntos_salen_en_orden_cronologico(self):
        for h in (5, 1, 3):
            self.lectura("temp_leche", float(h), AHORA - datetime.timedelta(hours=h))
        ts = [p["t"] for p in iot_monitoreo.historial("temp_leche", "dia", AHORA)["puntos"]]
        self.assertEqual(ts, sorted(ts))

    def test_lo_de_otro_sensor_o_fuera_del_rango_no_entra(self):
        self.lectura("temp_leche", 5.0, AHORA - datetime.timedelta(hours=1))
        self.lectura("temp_leche", 99.0, AHORA - datetime.timedelta(days=2))     # fuera de "dia"
        self.lectura("temp_ambiente", 77.0, AHORA - datetime.timedelta(hours=1))
        h = iot_monitoreo.historial("temp_leche", "dia", AHORA)
        self.assertEqual(h["resumen"]["n"], 1)
        self.assertEqual(h["resumen"]["max"], 5.0)

    def test_semana_mes_y_anio_agrupan_por_hora_seis_horas_y_dia(self):
        self.lectura("temp_leche", 5.0, AHORA - datetime.timedelta(hours=1))
        baldes = {r: iot_monitoreo.historial("temp_leche", r, AHORA)["balde_s"]
                  for r in ("dia", "semana", "mes", "anio")}
        self.assertEqual(baldes, {"dia": 600, "semana": 3600, "mes": 6 * 3600, "anio": 86400})

    def test_un_anio_entero_de_lecturas_cada_30_segundos_se_resume_en_un_punto_por_dia(self):
        # El caso real que justifica agrupar en la base: ~1 millón de filas por sensor y por año.
        filas = [("temp_leche", (AHORA - datetime.timedelta(days=d, seconds=s)).isoformat(timespec="seconds"), 4.0)
                 for d in range(0, 20) for s in range(0, 3600, 30)]
        self.con.executemany("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)", filas)
        self.con.commit()
        h = iot_monitoreo.historial("temp_leche", "anio", AHORA)
        self.assertLessEqual(len(h["puntos"]), 21)
        self.assertEqual(h["resumen"]["n"], len(filas))

    def test_sin_lecturas_devuelve_listas_vacias_y_resumen_nulo(self):
        h = iot_monitoreo.historial("temp_leche", "dia", AHORA)
        self.assertEqual(h["puntos"], [])
        self.assertEqual(h["resumen"], {"min": None, "prom": None, "max": None, "n": 0})

    def test_el_ith_se_calcula_con_temperatura_y_humedad_del_mismo_balde(self):
        t = AHORA - datetime.timedelta(hours=1)
        self.lectura("temp_ambiente", 30.0, t)
        self.lectura("hum_ambiente", 70.0, t)
        self.lectura("temp_ambiente", 25.0, AHORA - datetime.timedelta(hours=5))     # sin humedad: no hay ITH
        h = iot_monitoreo.historial("ith", "dia", AHORA)
        self.assertEqual(len(h["puntos"]), 1)
        self.assertEqual(h["puntos"][0]["prom"], iot_monitoreo.calcular_ith(30.0, 70.0))
        self.assertIsNone(h["puntos"][0]["min"])        # el ITH solo tiene el promedio de cada balde


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.ruta_db = os.path.join(self.dir.name, "iot.db")
        p = mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(self.ruta_db))
        p.start()
        self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_get_trae_todos_los_sensores_con_su_configuracion_y_la_escala_por_defecto(self):
        r = self.cli.get("/api/iot/vumetros")
        self.assertEqual(r.status_code, 200)
        s = {x["clave"]: x for x in r.get_json()["sensores"]}
        self.assertIn("ith", s)
        self.assertEqual((s["temp_lavado"]["escala_defecto"]["min"], s["temp_lavado"]["escala_defecto"]["max"]),
                         (0, 90))
        self.assertEqual(s["temp_lavado"]["estilo"], "digital")

    def test_post_guarda_y_get_lo_devuelve(self):
        r = self.cli.post("/api/iot/vumetros", json={"sensores": {
            "temp_leche": {"estilo": "analogico", "amarillo_alto": 4, "rojo_alto": 6}}})
        self.assertEqual(r.status_code, 200)
        s = {x["clave"]: x for x in self.cli.get("/api/iot/vumetros").get_json()["sensores"]}["temp_leche"]
        self.assertEqual((s["estilo"], s["amarillo_alto"], s["rojo_alto"]), ("analogico", 4, 6))

    def test_post_con_datos_invalidos_da_400_con_el_motivo_y_no_guarda(self):
        r = self.cli.post("/api/iot/vumetros", json={"sensores": {
            "temp_leche": {"estilo": "digital", "amarillo_alto": 6, "rojo_alto": 4}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(iv.cargar(), {})

    def test_post_con_formato_invalido_da_400(self):
        self.assertEqual(self.cli.post("/api/iot/vumetros", json={"sensores": [1]}).status_code, 400)

    def test_historial_trae_puntos_resumen_y_la_configuracion_del_sensor(self):
        self.lectura("temp_leche", 5.0, datetime.datetime.now() - datetime.timedelta(hours=1))
        iv.guardar({"temp_leche": {"estilo": "digital", "amarillo_alto": 4, "rojo_alto": 6}}, SENSORES)
        r = self.cli.get("/api/iot/historial?sensor=temp_leche&rango=dia")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["resumen"]["n"], 1)
        self.assertEqual(d["limites"]["rojo_alto"], 6)
        self.assertEqual((d["label"], d["unidad"]), ("Temp. leche", "°C"))

    def test_historial_con_sensor_o_rango_invalido_da_400(self):
        self.assertEqual(self.cli.get("/api/iot/historial?sensor=nada&rango=dia").status_code, 400)
        self.assertEqual(self.cli.get("/api/iot/historial?sensor=temp_leche&rango=siglo").status_code, 400)

    def test_sin_sesion_de_admin_no_entra(self):
        anonimo = app.app.test_client()
        for ruta in ("/api/iot/vumetros", "/api/iot/historial?sensor=temp_leche&rango=dia"):
            self.assertNotEqual(anonimo.get(ruta).status_code, 200)
        self.assertNotEqual(anonimo.post("/api/iot/vumetros", json={"sensores": {}}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
