"""Vigilante, Tarea 2: las reglas puras de detección por ordeño.

Corre sin base: todo son funciones sobre datos ya armados.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_vigilante_reglas.py" -v
"""
import datetime
import unittest

import vigilante_reglas as r

D = datetime.date(2026, 11, 14)          # sábado


def a(dia_offset, h, mi=0):
    return datetime.datetime.combine(D + datetime.timedelta(days=dia_offset), datetime.time(h, mi))


def ordeno(kg, sesion=1, vacas=300, inicio=None, fin=None):
    inicio = inicio or a(0, 5)
    return r.armar_ordeno(sesion, vacas, kg, inicio, fin or inicio + datetime.timedelta(hours=2))


def base(kgs, sesion=1):
    return [ordeno(k, sesion, inicio=a(-i - 1, 5)) for i, k in enumerate(kgs)]


BASE_PAREJA = [1000, 1010, 990, 1005, 995, 1000, 1002]
BASE_DISPAREJA = [1000, 1100, 900, 1050, 950, 1000, 1000]


class TestBajaOrdeno(unittest.TestCase):
    def test_un_ordeno_muy_por_debajo_dispara(self):
        res = r.baja_ordeno(ordeno(900), base(BASE_PAREJA))
        self.assertIsNotNone(res)
        self.assertEqual(res["dias_base"], 7)
        self.assertAlmostEqual(res["pct"], -10.0, delta=0.1)
        self.assertLess(res["z"], -2.0)
        self.assertAlmostEqual(res["media"], 1000.3, delta=0.1)

    def test_baja_chica_aunque_pase_los_dos_desvios_no_dispara(self):
        # z ~ -2,5 pero apenas 1,5 % por debajo: hacen falta las dos condiciones a la vez
        self.assertIsNone(r.baja_ordeno(ordeno(985), base(BASE_PAREJA)))

    def test_baja_grande_pero_dentro_de_lo_que_el_ordeno_varia_no_dispara(self):
        # 5 % abajo, pero esta base varía tanto que no llega a 2 desvíos
        self.assertIsNone(r.baja_ordeno(ordeno(950), base(BASE_DISPAREJA)))

    def test_base_corta_no_avisa(self):
        self.assertIsNone(r.baja_ordeno(ordeno(500), base([1000, 1010])))

    def test_un_ordeno_mejor_que_lo_normal_no_dispara(self):
        self.assertIsNone(r.baja_ordeno(ordeno(1200), base(BASE_PAREJA)))


class TestOrdenoTerminado(unittest.TestCase):
    def test_ordeno_que_cruza_medianoche_cuenta_para_el_dia_que_empezo(self):
        o = r.armar_ordeno(3, 280, 900.0, a(-1, 23, 50), a(0, 1, 30))     # 13/11 23:50 a 14/11 01:30
        self.assertEqual(o.fecha, "2026-11-13")
        self.assertFalse(r.ordeno_terminado(o, a(0, 2, 14)))
        self.assertTrue(r.ordeno_terminado(o, a(0, 2, 20)))

    def test_el_limite_es_de_45_minutos_sin_filas_nuevas(self):
        o = ordeno(1000, fin=a(0, 7, 0))
        self.assertFalse(r.ordeno_terminado(o, a(0, 7, 44)))
        self.assertTrue(r.ordeno_terminado(o, a(0, 7, 45)))


class TestVacasFaltantes(unittest.TestCase):
    HABITUALES = {1: 20.0, 2: 25.0, 3: 30.0, 4: 22.0, 5: 28.0}

    def test_faltan_vacas_que_pesan_mas_del_uno_por_ciento(self):
        res = r.vacas_faltantes({1, 2, 3}, self.HABITUALES, kg_base=1000.0)    # faltan la 4 y la 5: 50 kg = 5 %
        self.assertEqual(res["faltan"], 2)
        self.assertEqual(res["numeros"], [4, 5])
        self.assertAlmostEqual(res["kg"], 50.0)
        self.assertAlmostEqual(res["pct"], 5.0)

    def test_vacas_faltantes_bajo_1_porciento_no_avisa(self):
        self.assertIsNone(r.vacas_faltantes({1, 2, 3, 4}, self.HABITUALES, kg_base=3000.0))   # 28 kg = 0,93 %

    def test_nadie_falta_no_avisa(self):
        self.assertIsNone(r.vacas_faltantes(set(self.HABITUALES), self.HABITUALES, kg_base=1000.0))

    def test_sin_kg_de_base_no_divide_por_cero(self):
        self.assertIsNone(r.vacas_faltantes({1}, self.HABITUALES, kg_base=0.0))


class TestSinDatos(unittest.TestCase):
    VENTANA = (5, 20)

    def test_fuera_de_la_ventana_de_ordeno_no_avisa(self):
        self.assertFalse(r.sin_datos(a(-1, 20, 30), a(0, 2, 0), 4, self.VENTANA))

    def test_dentro_de_la_ventana_y_pasadas_las_horas_avisa(self):
        self.assertTrue(r.sin_datos(a(0, 4, 30), a(0, 9, 30), 4, self.VENTANA))

    def test_a_poco_de_abrir_la_ventana_no_cuenta_la_noche_entera(self):
        # la última fila es de anoche (9 h atrás) pero la ventana abrió hace media hora: es normal
        self.assertFalse(r.sin_datos(a(-1, 20, 30), a(0, 5, 30), 4, self.VENTANA))

    def test_sin_ninguna_fila_dentro_de_la_ventana_avisa(self):
        self.assertTrue(r.sin_datos(None, a(0, 12, 0), 4, self.VENTANA))

    def test_con_filas_recientes_no_avisa(self):
        self.assertFalse(r.sin_datos(a(0, 11, 0), a(0, 12, 0), 4, self.VENTANA))


class TestComidaAtrasada(unittest.TestCase):
    def fila(self, fecha="2026-11-14", retraso=75):
        return {"fecha": fecha, "primera": 525, "habitual": 450, "retraso": retraso}

    def test_dispara_si_es_de_hoy_y_pasa_la_hora(self):
        res = r.comida_atrasada(self.fila(), D)
        self.assertEqual(res["retraso"], 75)

    def test_la_comida_de_otro_dia_no_dispara(self):
        self.assertIsNone(r.comida_atrasada(self.fila(fecha="2026-11-13"), D))

    def test_poco_retraso_no_dispara(self):
        self.assertIsNone(r.comida_atrasada(self.fila(retraso=59), D))

    def test_sin_dato_de_comida_no_dispara(self):
        self.assertIsNone(r.comida_atrasada(None, D))


class TestSinDatosDesdeApertura(unittest.TestCase):
    AHORA = datetime.datetime(2026, 11, 14, 5, 10)

    def test_corte_de_la_noche_se_ve_al_abrir_el_ordeno_aunque_la_ventana_ya_cerro(self):
        ultima = datetime.datetime(2026, 11, 13, 20, 30)
        self.assertTrue(r.sin_datos_desde_apertura(ultima, self.AHORA, 4, [0, 8, 16]))

    def test_con_filas_desde_la_apertura_no_hay_corte(self):
        ultima = datetime.datetime(2026, 11, 14, 4, 40)
        self.assertFalse(r.sin_datos_desde_apertura(ultima, self.AHORA, 4, [0]))

    def test_una_apertura_que_todavia_no_llego_no_cuenta(self):
        ultima = datetime.datetime(2026, 11, 13, 20, 30)
        self.assertFalse(r.sin_datos_desde_apertura(ultima, self.AHORA, 4, [8, 16]))

    def test_necesita_horas_completas_desde_la_apertura(self):
        ultima = datetime.datetime(2026, 11, 13, 20, 30)
        self.assertFalse(r.sin_datos_desde_apertura(ultima, datetime.datetime(2026, 11, 14, 3, 59), 4, [0]))
        self.assertTrue(r.sin_datos_desde_apertura(ultima, datetime.datetime(2026, 11, 14, 4, 0), 4, [0]))

    def test_sin_ninguna_fila_y_con_la_apertura_pasada_es_sin_datos(self):
        self.assertTrue(r.sin_datos_desde_apertura(None, self.AHORA, 4, [0]))


if __name__ == "__main__":
    unittest.main()
