"""Tanda 2 de campos del catálogo (24/11/2026): los que salen de los punteros del índice de DelPro (inseminación efectiva, último control de preñez, última decisión de descarte), el
último recuento de células y los promedios de leche de 14, 21 y 30 días.

Se eligieron por lo que cuestan: se midieron con la base real (muestra de 600 animales). El último recuento y los punteros del índice son baratos; el último «corte» de la puerta de
separación y el último diagnóstico recorren decenas de páginas por animal (80 y 340) y quedaron para otra tanda.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda2 -v
"""
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im

FECHAS = ("EffectiveInseminationDate", "LastPregnancyCheckDate", "LastCullDecisionDate")
NUMEROS = ("DaysPregnant", "LatestSCC", "CMSFourteenDayAverage", "CMSTwentyOneDayAverage", "CMSThirtyDayAverage")
PROMEDIOS = {"CMSFourteenDayAverage": 14, "CMSTwentyOneDayAverage": 21, "CMSThirtyDayAverage": 30}


def compilar(campos, **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy="2026-07-30")
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


class TestElCatalogo(unittest.TestCase):
    def test_estan_con_el_nombre_que_les_da_delpro(self):
        for id_ in FECHAS + NUMEROS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_tipos(self):
        for id_ in FECHAS:
            self.assertEqual(cat.CATALOGO[id_].tipo, "fecha", id_)
        for id_ in NUMEROS:
            self.assertEqual(cat.CATALOGO[id_].tipo, "num", id_)

    def test_los_punteros_del_indice_de_delpro_son_verificados_y_las_cuentas_nuestras_no(self):
        for id_ in ("EffectiveInseminationDate", "LastPregnancyCheckDate"):
            self.assertTrue(cat.CATALOGO[id_].verificado, id_)
        for id_ in ("LastCullDecisionDate", "DaysPregnant", "LatestSCC") + tuple(PROMEDIOS):
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)

    def test_cada_uno_dice_de_donde_sale(self):
        for id_ in FECHAS + NUMEROS:
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)

    def test_el_recuento_de_celulas_va_en_miles_como_lo_guarda_delpro(self):
        c = cat.CATALOGO["LatestSCC"]
        self.assertEqual((c.decimales, c.etiqueta), (0, "Último recuento de células somáticas"))
        self.assertIn("mil", c.unidad)

    def test_los_promedios_se_llaman_como_el_de_7_dias(self):
        base = cat.CATALOGO["SevenDayAverage"]
        for id_, n in PROMEDIOS.items():
            c = cat.CATALOGO[id_]
            self.assertEqual(c.etiqueta, f"Promedio producción diaria de leche {n} d.")
            self.assertEqual((c.unidad, c.decimales), (base.unidad, base.decimales))


class TestSeCompilan(unittest.TestCase):
    def test_cada_campo_solo_pasa_la_validacion_de_solo_lectura(self):
        for id_ in FECHAS + NUMEROS:
            db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}, {"campo": id_}]}, herd=1, hoy="2026-07-30")["sql"])

    def test_los_promedios_miran_la_ventana_correcta_y_solo_dias_con_leche_valida(self):
        for id_, n in PROMEDIOS.items():
            sql = compilar([id_])
            self.assertIn(f"DATEADD(day, -{n}, ", sql, id_)
            self.assertIn("IsYieldValid = 1", sql, id_)
            self.assertIn("TotalYield > 0", sql, id_)

    def test_el_de_7_dias_sigue_igual(self):
        sql = compilar(["SevenDayAverage"])
        self.assertIn("DATEADD(day, -7, ", sql)
        self.assertNotIn("-14", sql)

    def test_los_punteros_comparten_el_indice_y_lo_unen_una_sola_vez(self):
        sql = compilar(["EffectiveInseminationDate", "LastPregnancyCheckDate", "LastCullDecisionDate", "LastInseminationDate", "LastCalvingDate"])
        self.assertEqual(sql.count("LEFT JOIN AnimalLatestHistoryIndex"), 1)
        db.validate_sql(im.compilar({"columnas": [{"campo": c} for c in ("EffectiveInseminationDate", "LastPregnancyCheckDate", "LastCullDecisionDate")]}, herd=1, hoy="2026-07-30")["sql"])

    def test_el_ultimo_recuento_busca_por_animal_y_no_agrupa_la_tabla_entera(self):
        sql = compilar(["LatestSCC"]).upper()
        self.assertIn("TOP 1", sql)
        self.assertNotIn("GROUP BY", sql)

    def test_los_dias_de_preñez_cuentan_desde_la_inseminacion_efectiva_de_este_ciclo(self):
        sql = compilar(["DaysPregnant"])
        self.assertIn("EffectiveInsemination", sql)
        self.assertIn("IsPregnant = 1", sql)

    def test_con_condiciones_resumenes_y_orden(self):
        db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}, {"campo": "LatestSCC"}], "condiciones": [{"campo": "LatestSCC", "op": ">", "valor": 400},
                                     {"campo": "CMSThirtyDayAverage", "op": ">=", "valor": 20}, {"campo": "EffectiveInseminationDate", "op": ">=", "valor": "2026-01-01"}],
                                     "orden": {"campo": "LatestSCC", "dir": -1}}, herd=1, hoy="2026-07-30")["sql"])
        im.compilar({"columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "LatestSCC", "resumen": "avg"}, {"campo": "CMSFourteenDayAverage", "resumen": "avg"},
                                  {"campo": "EffectiveInseminationDate", "resumen": "max"}], "detalle": False}, herd=1, hoy="2026-07-30")


class TestElImportador(unittest.TestCase):
    def test_las_propiedades_de_la_reproduccion_son_alias(self):
        res = dl.resolver_por_nombre(cat.CATALOGO)
        for ruta, id_ in (("ReproductionInfo.DaysPregnant", "DaysPregnant"), ("ReproductionInfo.EffectiveInseminationDate", "EffectiveInseminationDate"),
                          ("ReproductionInfo.LastPregnancyCheckDate", "LastPregnancyCheckDate")):
            self.assertEqual(res(ruta), (id_, id_), ruta)

    def test_un_filtro_de_recuento_y_de_promedio_se_trae(self):
        cs, pend = dl.condiciones_de("LatestSCC > 400 And CMSThirtyDayAverage >= 20 And LatestSCC Is Not Null", dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, 10)
        self.assertEqual(pend, [])
        self.assertIn({"campo": "LatestSCC", "op": ">", "valor": 400}, cs)
        self.assertIn({"campo": "CMSThirtyDayAverage", "op": ">=", "valor": 20}, cs)


if __name__ == "__main__":
    unittest.main()
