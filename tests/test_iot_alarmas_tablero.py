"""Tarjeta «Alarmas de monitoreo IoT» del Tablero de Diagnóstico: cuántos sensores están en rojo, amarillo y verde.

Sale de las zonas de los vúmetros (iot_vumetros): un sensor sin límites cargados no cuenta (no hay con qué compararlo), y uno
con límites pero sin una lectura reciente tampoco es «verde»: un sensor que dejó de reportar no está bien, está mudo.
«Sin dato no es cero»: si no hay ningún sensor medible, la tarjeta lo dice y NO muestra un 0 que se lea como «todo bien».

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_alarmas_tablero -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import iot_lavado
import iot_monitoreo
import iot_vumetros as iv
import tablero

AHORA = datetime.datetime(2026, 11, 22, 12, 0, 0)
SENSORES = set(iot_monitoreo.sensores_con_ith())


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

    def lectura(self, sensor, valor, hace_min=1):
        cuando = AHORA - datetime.timedelta(minutes=hace_min)
        self.con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                         (sensor, cuando.isoformat(timespec="seconds"), valor))
        self.con.commit()

    def limites(self, **por_sensor):
        iv.guardar({s: {"estilo": "digital", **lim} for s, lim in por_sensor.items()}, SENSORES)


class TestConteo(Base):
    def test_cuenta_los_sensores_por_color_de_su_zona(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6},
                     temp_lavado={"amarillo_alto": 70, "rojo_alto": 85},
                     temp_ambiente={"amarillo_alto": 28, "rojo_alto": 35},
                     hum_ambiente={"amarillo_alto": 75, "rojo_alto": 85})
        self.lectura("temp_leche", 7.0)          # rojo
        self.lectura("temp_lavado", 75.0)        # amarillo
        self.lectura("temp_ambiente", 20.0)      # verde
        self.lectura("hum_ambiente", 60.0)       # verde
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["amarillo"], c["verde"]), (1, 1, 2))
        self.assertEqual(c["en_rojo"], ["Temp. leche"])
        self.assertEqual(c["en_amarillo"], ["Temp. lavado"])

    def test_un_sensor_sin_limites_no_cuenta_por_color_aunque_tenga_lectura(self):
        self.lectura("temp_leche", 99.0)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["amarillo"], c["verde"], c["sin_datos"]), (0, 0, 0, 0))
        self.assertEqual(c["sin_lectura"], [])

    def test_un_sensor_con_limites_y_sin_lectura_es_sin_datos_y_no_verde(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["amarillo"], c["verde"], c["sin_datos"]), (0, 0, 0, 1))
        self.assertEqual(c["sin_lectura"], ["Temp. leche"])

    def test_una_lectura_vieja_no_es_una_lectura(self):
        # Un sensor que dejó de reportar hace horas no está «en verde»: está mudo.
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        self.lectura("temp_leche", 2.0, hace_min=iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN + 1)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["verde"], c["sin_datos"]), (0, 1))
        self.assertEqual(c["sin_lectura"], ["Temp. leche"])

    def test_un_sensor_instalado_que_enmudecio_es_alarma_aunque_no_tenga_limites(self):
        # Que dejó de mandar datos no depende de con qué se lo compare: el caso real (sensores sin límites cargados todavía).
        self.lectura("temp_leche", 5.0, hace_min=120)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual(c["sin_datos"], 1)
        self.assertEqual(c["sin_lectura"], ["Temp. leche"])

    def test_un_sensor_que_nunca_leyo_y_no_tiene_limites_esta_sin_instalar_y_no_alarma(self):
        self.assertEqual(iot_monitoreo.conteo_alarmas(AHORA)["sin_datos"], 0)

    def test_una_lectura_justo_dentro_de_la_ventana_cuenta(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        self.lectura("temp_leche", 2.0, hace_min=iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN)
        self.assertEqual(iot_monitoreo.conteo_alarmas(AHORA)["verde"], 1)

    def test_el_ith_cuenta_solo_si_la_temperatura_y_la_humedad_son_recientes(self):
        self.limites(ith={"amarillo_alto": 68, "rojo_alto": 80})
        self.lectura("temp_ambiente", 30.0)
        self.lectura("hum_ambiente", 70.0, hace_min=iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN + 5)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertIn("ITH (estrés calórico)", c["sin_lectura"])
        self.lectura("hum_ambiente", 70.0, hace_min=1)
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual(c["rojo"], 1)                       # ITH de 30 °C y 70 % ≈ 81

    def test_sin_nada_configurado_todo_es_cero(self):
        c = iot_monitoreo.conteo_alarmas(AHORA)
        self.assertEqual((c["rojo"], c["amarillo"], c["verde"], c["sin_lectura"]), (0, 0, 0, []))
        self.assertEqual(c["con_limites"], 0)


class TestValorParaElTablero(Base):
    def test_sin_ningun_sensor_medible_la_tarjeta_dice_por_que_y_no_muestra_un_cero(self):
        d = app._iot_alarmas_tablero(AHORA)
        self.assertIsNone(d["valor"])
        self.assertIn("límites", d["falta"])

    def test_con_limites_pero_todos_mudos_es_una_alarma_y_no_un_sin_dato(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        d = app._iot_alarmas_tablero(AHORA)
        self.assertEqual((d["valor"], d["nivel"]), (1, "atencion"))
        self.assertEqual(d["conteo"]["sin_datos"], 1)
        self.assertIn("Sin datos: Temp. leche", d["detalle"])

    def test_un_sensor_que_enmudecio_suma_a_las_alarmas_y_no_deja_la_tarjeta_en_verde(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6}, temp_lavado={"amarillo_alto": 70, "rojo_alto": 85})
        self.lectura("temp_leche", 2.0)                                    # verde
        self.lectura("temp_lavado", 60.0, hace_min=240)                    # hablaba bien hace 4 horas y se calló
        d = app._iot_alarmas_tablero(AHORA)
        self.assertEqual((d["valor"], d["nivel"]), (1, "atencion"))
        self.assertEqual((d["conteo"]["verde"], d["conteo"]["sin_datos"]), (1, 1))
        self.assertIn("Sin datos: Temp. lavado", d["detalle"])

    def test_un_sensor_rojo_gana_sobre_los_sin_datos(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        self.lectura("temp_leche", 7.0)
        self.lectura("temp_lavado", 60.0, hace_min=240)
        d = app._iot_alarmas_tablero(AHORA)
        self.assertEqual((d["valor"], d["nivel"]), (2, "mal"))

    def test_el_valor_son_las_alarmas_activas_rojas_mas_amarillas(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6}, temp_lavado={"amarillo_alto": 70, "rojo_alto": 85},
                     temp_ambiente={"amarillo_alto": 28, "rojo_alto": 35})
        self.lectura("temp_leche", 7.0)
        self.lectura("temp_lavado", 75.0)
        self.lectura("temp_ambiente", 20.0)
        d = app._iot_alarmas_tablero(AHORA)
        self.assertEqual(d["valor"], 2)
        self.assertEqual((d["conteo"]["rojo"], d["conteo"]["amarillo"], d["conteo"]["verde"]), (1, 1, 1))

    def test_el_nivel_lo_decide_el_sensor_mas_grave(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6}, temp_lavado={"amarillo_alto": 70, "rojo_alto": 85})
        self.lectura("temp_lavado", 75.0)                                   # amarillo
        self.assertEqual(app._iot_alarmas_tablero(AHORA)["nivel"], "atencion")
        self.lectura("temp_leche", 7.0)                                     # y uno rojo
        self.assertEqual(app._iot_alarmas_tablero(AHORA)["nivel"], "mal")

    def test_todos_en_verde_es_un_cero_valido_y_nivel_bien(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6})
        self.lectura("temp_leche", 2.0)
        d = app._iot_alarmas_tablero(AHORA)
        self.assertEqual((d["valor"], d["nivel"]), (0, "bien"))

    def test_el_detalle_nombra_los_sensores_en_alarma_y_los_mudos(self):
        self.limites(temp_leche={"amarillo_alto": 4, "rojo_alto": 6}, temp_lavado={"amarillo_alto": 70, "rojo_alto": 85},
                     temp_ambiente={"amarillo_alto": 28, "rojo_alto": 35})
        self.lectura("temp_leche", 7.0)
        self.lectura("temp_lavado", 75.0)                                  # temp_ambiente no reporta
        det = app._iot_alarmas_tablero(AHORA)["detalle"]
        self.assertIn("Temp. leche", det)
        self.assertIn("Temp. lavado", det)
        self.assertIn("Temp. ambiente", det)


class TestTarjeta(unittest.TestCase):
    def armar(self, dato, lecturas=None):
        return {t["clave"]: t for t in tablero.armar({"iot_alarmas": dato}, lecturas=lecturas)["tarjetas"]}["iot_alarmas"]

    CONTEO = {"rojo": 1, "amarillo": 1, "verde": 3, "sin_datos": 2, "sin_lectura": ["Temp. lavado", "Temp. leche"], "en_rojo": ["Temp. leche"],
              "en_amarillo": ["Temp. lavado"], "con_limites": 5, "sin_limites": 7}

    def test_el_color_sale_del_nivel_propio_y_no_de_los_limites_verde_y_rojo_del_tablero(self):
        for nivel, css in (("mal", "rgb(228, 0, 43)"), ("atencion", "rgb(242, 169, 0)"), ("bien", "rgb(0, 135, 90)")):
            with self.subTest(nivel=nivel):
                t = self.armar({"valor": 1, "nivel": nivel, "conteo": self.CONTEO})
                self.assertEqual((t["nivel"], t["color"]), (nivel, css))
                self.assertIsNone(t["posicion"])             # no hay marca en una escala verde-rojo

    def test_viaja_el_conteo_para_que_la_pantalla_dibuje_los_tres_contadores(self):
        t = self.armar({"valor": 2, "nivel": "mal", "conteo": self.CONTEO})
        self.assertEqual(t["conteo"]["rojo"], 1)

    def test_un_cero_valido_no_es_sin_dato(self):
        t = self.armar({"valor": 0, "nivel": "bien", "conteo": self.CONTEO})
        self.assertEqual((t["valor"], t["nivel"]), (0, "bien"))

    def test_sin_dato_no_se_rellena_con_la_ultima_lectura_vieja(self):
        # Es del gateway local: una cuenta vieja de alarmas mostrada como actual sería peor que decir que no hay dato.
        previa = {"iot_alarmas": {"valor": 3, "leido": "2026-11-20T10:00:00", "detalle": "x"}}
        t = self.armar({"valor": None, "falta": "Sin lectura reciente"}, lecturas=previa)
        self.assertIsNone(t["valor"])
        self.assertFalse(t["viejo"])
        self.assertEqual(t["nivel"], "sin_dato")

    def test_el_catalogo_avisa_que_el_color_es_propio(self):
        c = {i["clave"]: i for i in tablero.catalogo()}
        self.assertTrue(c["iot_alarmas"]["color_propio"])
        self.assertFalse(c["costo_litro"]["color_propio"])

    def test_el_resumen_por_mensaje_dice_cuantos_hay_de_cada_color(self):
        armado = tablero.armar({"iot_alarmas": {"valor": 2, "nivel": "mal", "conteo": self.CONTEO}},
                               config={"iot_alarmas": {"incluir_resumen": True}})
        texto = tablero.texto_resumen(armado, "La Ponderosa")
        self.assertIn("1 en rojo", texto)
        self.assertIn("1 en amarillo", texto)
        self.assertIn("3 en verde", texto)
        self.assertIn("2 sin datos", texto)
        self.assertIn("🔴", texto)
        self.assertIn("1 en rojo", tablero.html_resumen(armado, "La Ponderosa"))


if __name__ == "__main__":
    unittest.main()
