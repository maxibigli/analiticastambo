"""Serie diaria de un informe (informes_serie.py): la producción por día de los animales del informe, para graficarla en un rango de fechas.

Lo que importa: que la configuración del gráfico se valide contra listas cerradas (lo guardado siempre se puede volver a correr), que
ninguna fecha ni valor llegue a la consulta sin validar, que «los animales del informe» sean los MISMOS que filtra la lista, que cada
consulta pase la validación de solo lectura, y que los datos que salen estén alineados día por día para que la pantalla los agrupe.

    C:\\Python314\\python.exe -m unittest tests.test_informes_serie -v
"""
import datetime
import unittest
from unittest import mock

import db
import informes_motor as im
import informes_serie as isr

HOY = "2026-07-30"
INFORME = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "CurrentDIM", "op": ">=", "valor": 300}]}


def comp(informe=None, **g):
    return isr.compilar_serie(INFORME if informe is None else informe, g, hoy=HOY, herd=1)


class TestValidar(unittest.TestCase):
    def test_sin_nada_son_los_valores_de_siempre(self):
        self.assertEqual(isr.validar(None), isr.GRAFICO_DEFECTO)
        self.assertEqual(isr.validar({}), isr.GRAFICO_DEFECTO)

    def test_lo_valido_pasa_tal_cual(self):
        g = {"medida": "kg_total", "resumen": "sum", "serie": "lactancia", "intervalo": "semana", "forma": "barras", "periodo": "180",
             "desde": None, "hasta": None, "solo_informe": False}
        self.assertEqual(isr.validar(g), g)

    def test_deja_afuera_lo_que_no_conoce(self):
        r = isr.validar({"sql": "DROP TABLE x", "medida": "vacas"})
        self.assertNotIn("sql", r)
        self.assertEqual(r["medida"], "vacas")

    def test_cada_valor_sale_de_una_lista_cerrada(self):
        for clave, malo in (("medida", "litros"), ("resumen", "mediana"), ("serie", "sala"), ("intervalo", "hora"), ("forma", "torta"),
                            ("periodo", "7"), ("medida", 5), ("serie", None), ("solo_informe", "si"), ("solo_informe", 1)):
            with self.assertRaises(isr.SerieError, msg=f"{clave}={malo!r}"):
                isr.validar({clave: malo})

    def test_no_es_un_diccionario(self):
        for malo in ("texto", [], 5):
            with self.assertRaises(isr.SerieError):
                isr.validar(malo)

    def test_fechas_a_mano(self):
        r = isr.validar({"periodo": "fechas", "desde": "2026-05-01", "hasta": "2026-06-30"})
        self.assertEqual((r["desde"], r["hasta"]), ("2026-05-01", "2026-06-30"))

    def test_fechas_a_mano_mal_puestas(self):
        malos = [{"periodo": "fechas"}, {"periodo": "fechas", "desde": "2026-05-01"},
                 {"periodo": "fechas", "desde": "ayer", "hasta": "2026-06-30"},
                 {"periodo": "fechas", "desde": "2026-13-01", "hasta": "2026-06-30"},
                 {"periodo": "fechas", "desde": "2026-06-30", "hasta": "2026-05-01"},
                 {"periodo": "fechas", "desde": "2026-05-01'; DROP TABLE x;--", "hasta": "2026-06-30"},
                 {"periodo": "fechas", "desde": "2020-01-01", "hasta": "2026-06-30"}]          # más de dos años
        for malo in malos:
            with self.assertRaises(isr.SerieError, msg=str(malo)):
                isr.validar(malo)

    def test_el_tope_de_dias_es_inclusivo(self):
        d = datetime.date(2026, 6, 30)
        justo = {"periodo": "fechas", "desde": (d - datetime.timedelta(days=isr.DIAS_MAX - 1)).isoformat(), "hasta": d.isoformat()}
        isr.validar(justo)
        pasado = dict(justo, desde=(d - datetime.timedelta(days=isr.DIAS_MAX)).isoformat())
        with self.assertRaises(isr.SerieError):
            isr.validar(pasado)

    def test_las_fechas_se_ignoran_si_el_periodo_no_es_a_mano(self):
        r = isr.validar({"periodo": "90", "desde": "2026-05-01", "hasta": "2026-06-30"})
        self.assertEqual((r["desde"], r["hasta"]), (None, None))


class TestPeriodo(unittest.TestCase):
    def test_los_ultimos_n_dias_cuentan_el_ultimo(self):
        self.assertEqual(isr.periodo(isr.validar({"periodo": "90"}), HOY), (datetime.date(2026, 5, 2), datetime.date(2026, 7, 30)))
        self.assertEqual(isr.periodo(isr.validar({"periodo": "30"}), HOY), (datetime.date(2026, 7, 1), datetime.date(2026, 7, 30)))

    def test_todo_es_el_tope_de_dias(self):
        d, h = isr.periodo(isr.validar({"periodo": "todo"}), HOY)
        self.assertEqual((h - d).days + 1, isr.DIAS_MAX)
        self.assertEqual(h, datetime.date(2026, 7, 30))

    def test_a_mano(self):
        g = isr.validar({"periodo": "fechas", "desde": "2026-05-01", "hasta": "2026-06-30"})
        self.assertEqual(isr.periodo(g, HOY), (datetime.date(2026, 5, 1), datetime.date(2026, 6, 30)))

    def test_sin_fecha_de_referencia_es_el_reloj(self):
        with mock.patch.object(isr, "_hoy_reloj", lambda: datetime.date(2026, 11, 24)):
            self.assertEqual(isr.periodo(isr.validar({"periodo": "30"}), None)[1], datetime.date(2026, 11, 24))

    def test_fecha_de_referencia_invalida(self):
        for malo in ("ayer", "2026-13-40", "2026-07-30'; DROP", 20260730):
            with self.assertRaises(isr.SerieError):
                isr.periodo(isr.validar({}), malo)


class TestCompilar(unittest.TestCase):
    def test_por_rodeo_del_dia(self):
        c = comp(serie="rodeo", periodo="90")
        sql = c["sql"]
        self.assertIn("BETWEEN '2026-05-02' AND '2026-07-30'", sql)
        self.assertIn("JOIN AbstractGroup g", sql)
        self.assertIn("ad.AnimalGroup", sql)                          # el rodeo DE ESE DÍA, no el de hoy
        self.assertIn("GROUP BY ad.Date, ad.AnimalGroup, g.Name", sql)
        self.assertIn("ag.Herd = 1", sql)                              # el rebaño sale del grupo de ese día
        self.assertIn("ad.GCRecord IS NULL", sql)
        self.assertIn("bd.Number > 0", sql)

    def test_por_lactancia_junta_la_tercera_en_adelante(self):
        sql = comp(serie="lactancia")["sql"]
        self.assertIn("CASE WHEN ad.LactationNumber >= 3 THEN 3 ELSE ad.LactationNumber END", sql)
        self.assertNotIn("AbstractGroup", sql)

    def test_sin_dividir(self):
        sql = comp(serie="ninguna")["sql"]
        self.assertNotIn("AbstractGroup", sql)
        self.assertNotIn("CASE WHEN ad.LactationNumber", sql)

    def test_lo_que_se_pide_a_la_base(self):
        sql = comp()["sql"]
        for col in ("AS fecha", "AS sid", "AS snombre", "AS vacas", "AS kg_valido", "AS kg_todo", "AS vacas_todas"):
            self.assertIn(col, sql)
        self.assertIn("ad.IsYieldValid = 1", sql)                       # las vacas y el kg «por vaca» cuentan solo lecturas válidas…
        self.assertIn("ad.TotalYield > 0", sql)                         # …y el total, toda la leche medida (como DelPro)

    def test_la_poblacion_del_informe_va_como_subconsulta(self):
        c = comp(solo_informe=True)
        self.assertTrue(c["restringida"])
        self.assertIn("ad.BasicAnimal IN (SELECT b.OID", c["sql"])
        self.assertIn(">= 300", c["sql"])                              # la condición del informe
        self.assertIn("CONVERT(date, '2026-07-30', 23)", c["sql"])     # con el «hoy» de los datos
        self.assertIn("SELECT COUNT(*) AS n FROM (SELECT b.OID", c["sql_poblacion"])

    def test_sin_condiciones_es_todo_el_tambo_con_su_historia(self):
        """Sin condiciones no se restringe: los animales que ya salieron del rebaño también produjeron leche esos días."""
        c = comp({"columnas": [{"campo": "Number"}]}, solo_informe=True)
        self.assertFalse(c["restringida"])
        self.assertNotIn("IN (SELECT", c["sql"])
        self.assertIsNone(c["sql_poblacion"])

    def test_se_puede_pedir_todo_el_tambo_aunque_el_informe_filtre(self):
        c = comp(solo_informe=False)
        self.assertFalse(c["restringida"])
        self.assertNotIn("IN (SELECT", c["sql"])

    def test_las_condiciones_malas_se_rechazan(self):
        malo = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "NoExiste", "op": "=", "valor": 1}]}
        with self.assertRaises(im.InformeError):
            comp(malo)

    def test_un_informe_sin_definicion_es_todo_el_tambo(self):
        self.assertFalse(isr.compilar_serie(None, {}, hoy=HOY, herd=1)["restringida"])
        self.assertFalse(isr.compilar_serie({}, {}, hoy=HOY, herd=1)["restringida"])

    def test_todas_las_combinaciones_pasan_la_validacion_de_solo_lectura(self):
        for serie in isr.SERIES:
            for solo in (True, False):
                for periodo in ("30", "todo"):
                    c = comp(serie=serie, solo_informe=solo, periodo=periodo)
                    db.validate_sql(c["sql"])
                    if c["sql_poblacion"]:
                        db.validate_sql(c["sql_poblacion"])

    def test_las_fechas_que_llegan_a_la_consulta_son_las_validadas(self):
        c = comp(periodo="fechas", desde="2026-05-01", hasta="2026-06-30")
        self.assertIn("BETWEEN '2026-05-01' AND '2026-06-30'", c["sql"])
        self.assertEqual((c["desde"], c["hasta"]), ("2026-05-01", "2026-06-30"))

    def test_sin_limite_de_rebano_declarado_se_deduce(self):
        sql = isr.compilar_serie(INFORME, {}, hoy=HOY)["sql"]
        self.assertIn("ag.Herd = (", sql)


def fila(fecha, sid, nombre, vacas, kg_valido, kg_todo=None, vacas_todas=None):
    return [fecha, sid, nombre, vacas, kg_valido, kg_valido if kg_todo is None else kg_todo, vacas if vacas_todas is None else vacas_todas]


class TestArmar(unittest.TestCase):
    def armar(self, filas, desde="2026-07-01", hasta="2026-07-10", serie="rodeo", restringida=False):
        return isr.armar(filas, datetime.date.fromisoformat(desde), datetime.date.fromisoformat(hasta), serie, restringida)

    def dias(self, n, desde=datetime.date(2026, 7, 1)):
        return [(desde + datetime.timedelta(days=i)).isoformat() for i in range(n)]

    def test_todos_los_dias_del_rango_aunque_no_haya_datos(self):
        r = self.armar([fila("2026-07-03", "1", "Rodeo 1", 100, 4000.0)])
        self.assertEqual(r["fechas"], self.dias(10))
        s = r["series"][0]
        self.assertEqual(len(s["vacas"]), 10)
        self.assertEqual(s["vacas"][2], 100)
        self.assertEqual(s["kg_valido"][2], 4000.0)
        self.assertEqual(s["kg"][2], 4000.0)
        self.assertIsNone(s["vacas"][0])                                # un día sin datos es vacío, no cero
        self.assertIsNone(s["kg"][9])

    def test_acepta_fechas_como_fecha_o_como_texto(self):
        r = self.armar([fila(datetime.date(2026, 7, 3), "1", "Rodeo 1", 100, 4000.0), fila("2026-07-04T00:00:00", "1", "Rodeo 1", 90, 3500.0)])
        self.assertEqual(r["series"][0]["vacas"][2:4], [100, 90])

    def test_el_kg_total_incluye_lo_no_valido_y_el_valido_no(self):
        r = self.armar([fila("2026-07-03", "1", "Rodeo 1", 100, 4000.0, kg_todo=4150.0)])
        self.assertEqual((r["series"][0]["kg_valido"][2], r["series"][0]["kg"][2]), (4000.0, 4150.0))

    def test_los_rodeos_salen_en_orden_natural(self):
        filas = [fila("2026-07-03", str(i), n, 50, 1000.0) for i, n in enumerate(["Rodeo 10", "Rodeo 2", "Rodeo 1", "Rodeo 4 - Baja"])]
        self.assertEqual([s["etiqueta"] for s in self.armar(filas)["series"]], ["Rodeo 1", "Rodeo 2", "Rodeo 4 - Baja", "Rodeo 10"])

    def test_muchas_series_se_juntan_en_otros(self):
        filas = [fila("2026-07-03", str(i), f"Rodeo {i:02d}", 10, 100.0 * (i + 1)) for i in range(12)]
        r = self.armar(filas)
        self.assertEqual(len(r["series"]), isr.SERIES_MAX)
        self.assertEqual(r["series"][-1]["etiqueta"], "Otros")
        # Se quedan los de más leche (los números altos) y los 5 de menos leche se suman en «Otros»: nada se pierde.
        self.assertEqual(r["series"][-1]["vacas"][2], 50)
        self.assertEqual(r["series"][-1]["kg"][2], 100.0 + 200.0 + 300.0 + 400.0 + 500.0)
        self.assertEqual(sum(s["kg"][2] for s in r["series"]), sum(100.0 * (i + 1) for i in range(12)))

    def test_otros_no_aparece_si_entran_todos(self):
        filas = [fila("2026-07-03", str(i), f"Rodeo {i}", 10, 100.0) for i in range(isr.SERIES_MAX)]
        self.assertNotIn("Otros", [s["etiqueta"] for s in self.armar(filas)["series"]])

    def test_lactancia(self):
        filas = [fila("2026-07-03", "3", "", 5, 100.0), fila("2026-07-03", "1", "", 5, 100.0), fila("2026-07-03", None, "", 2, 10.0),
                 fila("2026-07-03", "2", "", 5, 100.0)]
        self.assertEqual([s["etiqueta"] for s in self.armar(filas, serie="lactancia")["series"]],
                         ["1ª lactancia", "2ª lactancia", "3ª o más", "Sin dato"])

    def test_sin_dividir_la_etiqueta_dice_de_que_se_trata(self):
        f = [fila("2026-07-03", "1", None, 5, 100.0)]
        self.assertEqual(self.armar(f, serie="ninguna")["series"][0]["etiqueta"], "Todo el tambo")
        self.assertEqual(self.armar(f, serie="ninguna", restringida=True)["series"][0]["etiqueta"], "Animales del informe")

    def test_un_dia_a_medio_cargar_se_marca(self):
        filas = [fila(d, "1", "Rodeo 1", 100, 4000.0) for d in self.dias(10)]
        filas[9] = fila("2026-07-10", "1", "Rodeo 1", 30, 1200.0)             # el último día, a medio consolidar
        self.assertEqual(self.armar(filas)["incompletos"], ["2026-07-10"])

    def test_un_dia_con_muchas_lecturas_no_validas_no_es_un_dia_a_medio_cargar(self):
        """El 25/05/2026 casi la mitad de las vacas quedó con el total del día no válido (154 válidas de ~335), pero la leche está medida:
        no se oculta. Un día a medio cargar es el que le FALTAN lecturas (el 27/07 tiene 8.596 kg contra ~61.000)."""
        filas = [fila(d, "1", "Rodeo 1", 100, 4000.0, kg_todo=4100.0) for d in self.dias(10)]
        filas[4] = fila("2026-07-05", "1", "Rodeo 1", 40, 1600.0, kg_todo=3900.0, vacas_todas=100)
        self.assertEqual(self.armar(filas)["incompletos"], [])
        filas[4] = fila("2026-07-05", "1", "Rodeo 1", 15, 600.0, kg_todo=1500.0, vacas_todas=38)         # ahora sí faltan lecturas
        self.assertEqual(self.armar(filas)["incompletos"], ["2026-07-05"])

    def test_las_vacas_de_la_salida_son_las_validas_y_no_se_filtra_el_dato_interno(self):
        r = self.armar([fila("2026-07-03", "1", "Rodeo 1", 40, 1600.0, kg_todo=3900.0, vacas_todas=100)])
        s = r["series"][0]
        self.assertEqual(s["vacas"][2], 40)
        self.assertEqual(sorted(s), ["etiqueta", "id", "kg", "kg_valido", "vacas"])

    def test_el_piso_es_sobre_el_total_de_todas_las_series(self):
        filas = []
        for d in self.dias(10):
            filas += [fila(d, "1", "Rodeo 1", 60, 2400.0), fila(d, "2", "Rodeo 2", 40, 1600.0)]
        filas[-2:] = [fila("2026-07-10", "1", "Rodeo 1", 35, 1400.0), fila("2026-07-10", "2", "Rodeo 2", 25, 1000.0)]    # 60 de 100: justo el piso
        self.assertEqual(self.armar(filas)["incompletos"], [])

    def test_con_pocos_dias_no_se_juzga(self):
        filas = [fila("2026-07-01", "1", "Rodeo 1", 100, 4000.0), fila("2026-07-02", "1", "Rodeo 1", 10, 400.0)]
        self.assertEqual(self.armar(filas)["incompletos"], [])

    def test_un_dia_sin_filas_no_es_un_dia_a_medio_cargar(self):
        filas = [fila(d, "1", "Rodeo 1", 100, 4000.0) for d in self.dias(10) if d != "2026-07-05"]
        self.assertEqual(self.armar(filas)["incompletos"], [])

    def test_sin_datos(self):
        r = self.armar([])
        self.assertEqual((r["series"], r["incompletos"]), ([], []))
        self.assertEqual(len(r["fechas"]), 10)


class TestEjecutar(unittest.TestCase):
    def correr(self, informe=None, filas=None, conteo=None, **g):
        llamadas = []

        def falso(sql, validate=True, tambo=None, max_rows=None):
            llamadas.append((sql, tambo, max_rows))
            if "COUNT(*) AS n" in sql:
                return {"columns": ["n"], "rows": [[conteo if conteo is not None else 0]], "truncated": False}
            return {"columns": [], "rows": filas or [], "truncated": False}
        r = isr.ejecutar_serie(INFORME if informe is None else informe, g, "ponderosa", hoy=HOY, run=falso, herd=1)
        return r, llamadas

    def test_una_consulta_si_no_se_restringe(self):
        r, llamadas = self.correr(solo_informe=False, filas=[fila("2026-07-30", "1", "Rodeo 1", 100, 4000.0)])
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(llamadas[0][1], "ponderosa")
        self.assertEqual(r["poblacion"], {"restringida": False, "animales": None})
        self.assertEqual(r["desde"], "2026-05-02")
        self.assertEqual(r["hasta"], "2026-07-30")
        self.assertEqual(len(r["fechas"]), 90)
        self.assertEqual(r["series"][0]["vacas"][-1], 100)
        self.assertIsInstance(r["segundos"], float)

    def test_restringida_cuenta_los_animales_del_informe(self):
        r, llamadas = self.correr(solo_informe=True, conteo=412, filas=[fila("2026-07-30", "1", "Rodeo 1", 100, 4000.0)])
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(r["poblacion"], {"restringida": True, "animales": 412})

    def test_la_consulta_trae_filas_de_sobra_pero_acotadas(self):
        _, llamadas = self.correr(solo_informe=False)
        self.assertEqual(llamadas[0][2], isr.FILAS_MAX)

    def test_una_palabra_prohibida_en_un_valor_es_un_error_del_informe(self):
        def falso(sql, validate=True, tambo=None, max_rows=None):
            raise db.UnsafeQueryError("no")
        with self.assertRaises(im.InformeError):
            isr.ejecutar_serie(INFORME, {}, "ponderosa", hoy=HOY, run=falso, herd=1)

    def test_otros_errores_de_la_base_siguen_de_largo(self):
        def falso(sql, validate=True, tambo=None, max_rows=None):
            raise RuntimeError("login timeout")
        with self.assertRaises(RuntimeError):
            isr.ejecutar_serie(INFORME, {}, "ponderosa", hoy=HOY, run=falso, herd=1)


if __name__ == "__main__":
    unittest.main()
