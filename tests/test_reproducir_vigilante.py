"""Vigilante, Tarea 7: la reproducción histórica usa las MISMAS reglas que la pasada en vivo, sin tocar el estado.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_reproducir_vigilante.py" -v
"""
import datetime
import unittest
from unittest import mock

import reproducir_vigilante as rv
import vigilante_estado
import vigilante_reglas as reglas

HASTA = datetime.date(2026, 11, 14)
KG = [1000, 1010, 990, 1005, 995, 1002, 998, 1001, 999, 1004]


def ord_(fecha, ses, kg, vacas=1000):
    ini = datetime.datetime.combine(fecha, datetime.time(5 + 8 * (ses - 1), 0))
    return reglas.armar_ordeno(ses, vacas, kg, ini, ini + datetime.timedelta(hours=2))


def dias_normales(dias=30, malos=None):
    """Ordeños 1 de `dias` días hasta HASTA; `malos` = {fecha: kg} reemplaza los kg de ese día."""
    malos = malos or {}
    out = []
    for i in range(dias):
        f = HASTA - datetime.timedelta(days=dias - 1 - i)
        out.append(ord_(f, 1, malos.get(f, KG[i % len(KG)])))
    return out


SIN_VACAS = lambda tambo, d0, d1: []   # noqa: E731


class TestReproducir(unittest.TestCase):
    def test_un_dia_malo_entre_dias_normales_aparece_en_la_salida(self):
        malo = datetime.date(2026, 11, 7)                             # sábado
        ords = dias_normales(30, malos={malo: 880})
        r = rv.reproducir("ponderosa", ords, SIN_VACAS, HASTA - datetime.timedelta(days=20), HASTA)
        self.assertEqual([(h["datos"]["fecha"], h["datos"]["sesion"], h["tipo"]) for h in r["hallazgos"]],
                         [("2026-11-07", 1, "baja_ordeno")])
        texto = rv.formatear(r)
        self.assertIn("sábado 07/11", texto)
        self.assertIn("DISPARÓ", texto)

    def test_sin_dias_malos_no_hay_avisos_y_lo_dice(self):
        r = rv.reproducir("ponderosa", dias_normales(30), SIN_VACAS, HASTA - datetime.timedelta(days=20), HASTA)
        self.assertEqual(r["hallazgos"], [])
        self.assertIn("0 avisos", rv.formatear(r))

    def test_el_dia_de_referencia_se_muestra_aunque_no_dispare(self):
        malo = datetime.date(2026, 11, 7)
        ords = dias_normales(30, malos={malo: 995})                    # apenas bajo: no alcanza para avisar
        r = rv.reproducir("ponderosa", ords, SIN_VACAS, HASTA - datetime.timedelta(days=20), HASTA)
        self.assertEqual(r["hallazgos"], [])
        texto = rv.formatear(r)
        self.assertIn("NO disparó", texto)
        self.assertIn("995", texto)                                    # con cuánto faltó se ve en el detalle

    def test_la_tabla_de_sensibilidad_es_monotona(self):
        malo = datetime.date(2026, 11, 7)
        ords = dias_normales(30, malos={malo: 900, malo - datetime.timedelta(days=3): 985})
        r = rv.reproducir("ponderosa", ords, SIN_VACAS, HASTA - datetime.timedelta(days=20), HASTA)
        g = r["sensibilidad"]
        for z in rv.GRILLA_Z:
            fila = [g[(z, p)] for p in rv.GRILLA_PCT]
            self.assertEqual(fila, sorted(fila, reverse=True))        # más exigente en % -> no más avisos
        for p in rv.GRILLA_PCT:
            col = [g[(z, p)] for z in rv.GRILLA_Z]
            self.assertEqual(col, sorted(col, reverse=True))          # más exigente en z -> no más avisos

    def test_la_sensibilidad_cuenta_con_las_dos_condiciones_a_la_vez(self):
        d = datetime.date
        ords = dias_normales(30, malos={d(2026, 11, 7): 880, d(2026, 11, 4): 975})   # -12 % y -2,5 %
        r = rv.reproducir("ponderosa", ords, SIN_VACAS, HASTA - datetime.timedelta(days=20), HASTA)
        g = r["sensibilidad"]
        self.assertEqual(g[(2.0, 2.0)], 2)         # los dos pasan 2 desvíos y 2 %
        self.assertEqual(g[(2.0, 4.0)], 1)         # el de 2,5 % no llega al 4 %: con una sola condición contaría 2
        self.assertEqual(g[(2.0, 8.0)], 1)

    def test_la_grilla_no_deja_cambiadas_las_constantes_de_las_reglas(self):
        z, p = reglas.Z_FUERTE, reglas.BAJA_MIN_PCT
        rv.reproducir("ponderosa", dias_normales(30, malos={datetime.date(2026, 11, 7): 880}), SIN_VACAS,
                      HASTA - datetime.timedelta(days=20), HASTA)
        self.assertEqual((reglas.Z_FUERTE, reglas.BAJA_MIN_PCT), (z, p))

    def test_no_toca_el_estado_ni_la_configuracion(self):
        with mock.patch.object(vigilante_estado, "Estado", side_effect=AssertionError("tocó el estado")), \
                mock.patch.object(vigilante_estado, "set_config", side_effect=AssertionError("tocó la config")):
            rv.reproducir("ponderosa", dias_normales(30, malos={datetime.date(2026, 11, 7): 880}), SIN_VACAS,
                          HASTA - datetime.timedelta(days=20), HASTA)

    def test_avisos_por_semana_cuenta_los_ordenos_con_aviso_sobre_las_semanas_del_periodo(self):
        malos = {datetime.date(2026, 11, 7): 880, datetime.date(2026, 11, 10): 870}
        r = rv.reproducir("ponderosa", dias_normales(30, malos=malos), SIN_VACAS,
                          HASTA - datetime.timedelta(days=13), HASTA)       # 14 días = 2 semanas
        self.assertEqual(r["avisos"], 2)
        self.assertAlmostEqual(r["por_semana"], 1.0)


class TestArgumentos(unittest.TestCase):
    def test_valores_por_defecto(self):
        a = rv.leer_argumentos([])
        self.assertEqual((a.dias, a.sin_vacas), (60, False))
        a = rv.leer_argumentos(["san_jose", "--dias", "30", "--sin-vacas"])
        self.assertEqual((a.tambo, a.dias, a.sin_vacas), ("san_jose", 30, True))


if __name__ == "__main__":
    unittest.main()
