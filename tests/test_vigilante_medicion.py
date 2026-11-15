"""Vigilante, Tarea 1: el cálculo del retraso con que los ordeños aparecen en DelPro.

Corre sin base: `calcular_retrasos` es puro, trabaja sobre muestras ya tomadas.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_vigilante_medicion.py" -v
"""
import datetime
import os
import tempfile
import unittest

import medir_retraso_ordenos as m

D = datetime.date(2026, 11, 14)


def hm(h, mi):
    return datetime.datetime.combine(D, datetime.time(h, mi))


def muestra(cuando, max_fin, filas, segundos=1.0):
    return {"cuando": cuando, "max_fin": max_fin, "filas": filas, "segundos": segundos}


class TestRetrasos(unittest.TestCase):
    def test_retraso_de_un_ordeno_es_la_diferencia_entre_cuando_se_vio_y_su_fin(self):
        r = m.calcular_retrasos([
            muestra(hm(6, 0), hm(5, 40), 100),
            muestra(hm(6, 5), hm(5, 40), 100),
            muestra(hm(6, 10), hm(6, 2), 130),
            muestra(hm(6, 15), hm(6, 2), 130),
        ])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["fin"], hm(6, 2))
        self.assertEqual(r[0]["visto"], hm(6, 10))
        self.assertEqual(r[0]["retraso_min"], 8)

    def test_sin_filas_nuevas_no_inventa_ordenos(self):
        # La primera muestra es la línea de base: no se sabe cuándo apareció, no se puede medir.
        r = m.calcular_retrasos([muestra(hm(6, c), hm(5, 40), 100) for c in (0, 5, 10, 15)])
        self.assertEqual(r, [])

    def test_un_ordeno_largo_con_varios_avances_cuenta_una_sola_vez(self):
        r = m.calcular_retrasos([
            muestra(hm(5, 0), hm(4, 0), 100),
            muestra(hm(6, 10), hm(6, 2), 130),    # empezó a entrar
            muestra(hm(6, 20), hm(6, 15), 160),
            muestra(hm(6, 25), hm(6, 21), 170),   # la última fila del ordeño
            muestra(hm(6, 30), hm(6, 21), 170),
        ])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["fin"], hm(6, 21))
        self.assertEqual(r[0]["visto"], hm(6, 25))
        self.assertEqual(r[0]["retraso_min"], 4)

    def test_dos_ordenos_separados_son_dos_entradas(self):
        r = m.calcular_retrasos([
            muestra(hm(5, 0), hm(4, 0), 100),
            muestra(hm(6, 10), hm(6, 2), 130),
            muestra(hm(14, 0), hm(6, 2), 130),
            muestra(hm(16, 12), hm(16, 4), 260),
        ])
        self.assertEqual([x["retraso_min"] for x in r], [8, 8])

    def test_sin_fin_en_la_base_se_ignora(self):
        r = m.calcular_retrasos([muestra(hm(6, 0), None, 0), muestra(hm(6, 5), None, 0),
                                 muestra(hm(6, 10), hm(6, 2), 30)])
        self.assertEqual(r, [])    # la primera con dato es la línea de base

    def test_filas_que_llegan_sin_fin_nuevo_se_cuentan_como_llegadas_tardias(self):
        n = m.filas_sin_fin_nuevo([
            muestra(hm(6, 0), hm(5, 40), 100),
            muestra(hm(6, 5), hm(5, 40), 100),
            muestra(hm(6, 10), hm(5, 40), 115),   # entraron filas viejas de golpe
            muestra(hm(6, 15), hm(6, 2), 130),    # esto sí es un ordeño nuevo
        ])
        self.assertEqual(n, 1)

    def test_resumen_da_maximo_mediana_y_tiempo_de_consulta(self):
        muestras = [muestra(hm(5, 0), hm(4, 0), 100, 2.0), muestra(hm(6, 10), hm(6, 2), 130, 4.0),
                    muestra(hm(14, 0), hm(6, 2), 130, 6.0), muestra(hm(16, 20), hm(16, 4), 260, 4.0)]
        res = m.resumir(m.calcular_retrasos(muestras), muestras)
        self.assertEqual(res["ordenos"], 2)
        self.assertEqual(res["maximo_min"], 16)
        self.assertEqual(res["mediana_min"], 12)
        self.assertEqual(res["consulta_media_s"], 4.0)


class TestCsv(unittest.TestCase):
    def test_ida_y_vuelta_del_csv(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = os.path.join(carpeta, "r.csv")
            m.agregar_muestra(ruta, muestra(hm(6, 0), hm(5, 40), 100, 1.5))
            m.agregar_muestra(ruta, muestra(hm(6, 5), None, 0, 0.5))
            leidas = m.leer_muestras(ruta)
        self.assertEqual(leidas, [muestra(hm(6, 0), hm(5, 40), 100, 1.5), muestra(hm(6, 5), None, 0, 0.5)])


if __name__ == "__main__":
    unittest.main()
