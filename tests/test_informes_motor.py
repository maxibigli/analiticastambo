"""Compilador de informes (informes_motor.py): una definición en JSON -> una consulta SELECT segura contra DDM.

Lo que importa: que NUNCA salga SQL que el usuario haya escrito (todo sale de una lista cerrada de campos), que ningún valor
pueda romper la consulta, que se una solo lo que las columnas elegidas necesitan, y que cada consulta pase la validación de
solo lectura que ya usa toda la app (`db.validate_sql`).

    C:\\Python314\\python.exe -m unittest tests.test_informes_motor -v
"""
import unittest

import db
import informes_motor as im
import rebano
from informes_catalogo import Bloque, Campo

CAT = {
    "Number": Campo("Number", "Número del animal", "General", "txt", "b.Number"),
    "GroupName": Campo("GroupName", "Nombre del grupo", "General", "txt", "ag.Name", bloque="grupo", agrupable=True),
    "Lact": Campo("Lact", "Nº Lactación", "General", "num", "rep.lact", bloque="rep", agrupable=True),
    "RepN": Campo("RepN", "Nº de partos", "Reproducción", "num", "rep.n", bloque="rep", decimales=0),
    "Sin": Campo("Sin", "Sin verificar", "General", "num", "ord.sv", bloque="ord", verificado=False),
    "Dim": Campo("Dim", "Días en ordeño", "General", "num", "ord.dim", bloque="ord", decimales=0),
    "P24": Campo("P24", "Producción 24 h", "Ordeño", "num", "ord.p24", bloque="ord", unidad="kg", decimales=1,
                 resumenes=("sum", "avg", "min", "max", "count")),
    "Parto": Campo("Parto", "Fecha del último parto", "Reproducción", "fecha", "rep.parto", bloque="rep"),
}
BLOQUES = {
    "ord": Bloque("ord", "LEFT JOIN (SELECT d.BasicAnimal AS animal, MAX(d.DIM) AS dim, MAX(d.TotalYield) AS p24 "
                         "FROM AnimalDaily d GROUP BY d.BasicAnimal) ord ON ord.animal = b.OID"),
    "rep": Bloque("rep", "LEFT JOIN (SELECT r.BasicAnimal AS animal, MAX(r.LastLactationChangeDate) AS parto, "
                         "MAX(r.LactationNumber) AS lact, COUNT(*) AS n "
                         "FROM AnimalReproduction r GROUP BY r.BasicAnimal) rep ON rep.animal = b.OID"),
}


def comp(**kw):
    d = {"columnas": [{"campo": "Number"}]}
    d.update(kw)
    return im.compilar(d, CAT, BLOQUES, herd=1)


class TestCompilar(unittest.TestCase):
    def test_lo_minimo(self):
        r = comp()
        self.assertIn("FROM BasicAnimal b", r["sql"])
        self.assertIn("b.GCRecord IS NULL", r["sql"])
        self.assertIn("b.Number AS c0", r["sql"])
        self.assertEqual([c["id"] for c in r["columnas"]], ["Number"])

    def test_siempre_filtra_por_rebano(self):
        self.assertIn("g_tb.Herd = 1", comp()["sql"])

    def test_el_sql_pasa_la_validacion_de_solo_lectura_de_la_app(self):
        r = comp(columnas=[{"campo": "Number"}, {"campo": "P24"}, {"campo": "Parto"}],
                 condiciones=[{"campo": "P24", "op": "<", "valor": 30}], orden={"campo": "P24", "dir": -1})
        db.validate_sql(r["sql"])

    def test_une_solo_los_bloques_que_hacen_falta(self):
        self.assertNotIn("LEFT JOIN (", comp()["sql"])
        r = comp(columnas=[{"campo": "Number"}, {"campo": "P24"}])
        self.assertEqual(r["sql"].count("LEFT JOIN (SELECT"), 1)
        self.assertIn(" ord ON ord.animal = b.OID", r["sql"])
        self.assertNotIn(" rep ON ", r["sql"])

    def test_un_bloque_que_solo_usa_una_condicion_tambien_se_une(self):
        r = comp(condiciones=[{"campo": "P24", "op": ">", "valor": 1}])
        self.assertIn(" ord ON ord.animal = b.OID", r["sql"])

    def test_un_bloque_que_depende_de_otro_lo_trae_antes(self):
        bl = dict(BLOQUES, dep=Bloque("dep", "LEFT JOIN OtraTabla dep ON dep.x = rep.parto", requiere=("rep",)))
        cat = dict(CAT, Dep=Campo("Dep", "Dependiente", "X", "num", "dep.v", bloque="dep"))
        sql = im.compilar({"columnas": [{"campo": "Dep"}]}, cat, bl, herd=1)["sql"]
        self.assertLess(sql.index(" rep ON "), sql.index("LEFT JOIN OtraTabla dep"))
        self.assertEqual(sql.count(" rep ON "), 1)

    def test_el_grupo_se_une_al_nucleo_solo_si_se_pide(self):
        self.assertNotIn("AbstractGroup", comp()["sql"])
        self.assertIn("AbstractGroup ag", comp(columnas=[{"campo": "GroupName"}])["sql"])

    def test_descartados_ubican_a_los_que_se_fueron(self):
        self.assertIn("AnimalDaily d_tb", comp(incluir_descartados=True)["sql"])
        self.assertNotIn("AnimalDaily d_tb", comp()["sql"])

    def test_metadatos_de_las_columnas_para_la_pantalla(self):
        c = comp(columnas=[{"campo": "P24"}])["columnas"][0]
        self.assertEqual((c["etiqueta"], c["tipo"], c["unidad"], c["decimales"], c["alias"]), ("Producción 24 h", "num", "kg", 1, "c0"))

    def test_tope_de_filas(self):
        """Se pide UNA fila de más: así se sabe si había más que el tope (si no, un tope exacto parecería «completo»)."""
        self.assertIn("TOP (2001)", comp()["sql"])
        self.assertIn("TOP (51)", comp(limite=50)["sql"])
        with self.assertRaises(im.InformeError):
            comp(limite=im.LIMITE_MAX + 1)
        with self.assertRaises(im.InformeError):
            comp(limite=0)


class TestFechaDeReferencia(unittest.TestCase):
    """«Días en ordeño» y los promedios se miden contra el «hoy» DE LOS DATOS (la base es una copia: con el reloj de la PC
    una copia de hace tres días daría tres días de más). Por defecto es el reloj; para comparar se fija una fecha."""
    cat = dict(CAT, Del=Campo("Del", "Días en ordeño", "General", "num", "DATEDIFF(day, rep.parto, @HOY)", bloque="rep"))

    def sql(self, **kw):
        return im.compilar({"columnas": [{"campo": "Del"}]}, self.cat, BLOQUES, herd=1, **kw)["sql"]

    def test_por_defecto_es_el_reloj(self):
        self.assertIn("DATEDIFF(day, rep.parto, CAST(GETDATE() AS date))", self.sql())

    def test_con_fecha_fija(self):
        self.assertIn("DATEDIFF(day, rep.parto, CONVERT(date, '2026-07-30', 23))", self.sql(hoy="2026-07-30"))

    def test_fecha_invalida(self):
        for malo in ("ayer", "2026-13-40", "2026-07-30'; DROP", 20260730):
            with self.assertRaises(im.InformeError):
                self.sql(hoy=malo)

    def test_tambien_se_reemplaza_dentro_de_un_bloque(self):
        bl = dict(BLOQUES, rep=Bloque("rep", "LEFT JOIN (SELECT 1 AS animal, @HOY AS h) rep ON rep.animal = b.OID"))
        sql = im.compilar({"columnas": [{"campo": "Parto"}]}, CAT, bl, herd=1, hoy="2026-07-30")["sql"]
        self.assertIn("CONVERT(date, '2026-07-30', 23) AS h", sql)

    def test_un_valor_del_usuario_con_la_marca_no_se_toca(self):
        sql = im.compilar({"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Number", "op": "contiene", "valor": "@HOY"}]},
                          self.cat, BLOQUES, herd=1, hoy="2026-07-30")["sql"]
        self.assertIn("LIKE N'%@HOY%'", sql)


class TestRechazos(unittest.TestCase):
    def malo(self, **kw):
        with self.assertRaises(im.InformeError):
            comp(**kw)

    def test_campo_desconocido_o_sin_columnas(self):
        self.malo(columnas=[{"campo": "NoExiste"}])
        self.malo(columnas=[])
        self.malo(columnas=[{"campo": "Number"}, {"campo": "Number"}])
        self.malo(columnas=[{"campo": "Number"}] * 0)

    def test_demasiadas_columnas(self):
        self.malo(columnas=[{"campo": "Number"}] * (im.COLUMNAS_MAX + 1))

    def test_el_id_del_campo_no_es_sql(self):
        self.malo(columnas=[{"campo": "Number; DROP TABLE BasicAnimal"}])
        self.malo(columnas=[{"campo": "b.Number"}])

    def test_condiciones(self):
        self.malo(condiciones=[{"campo": "NoExiste", "op": "=", "valor": 1}])
        self.malo(condiciones=[{"campo": "P24", "op": "LIKE", "valor": 1}])           # operador fuera de la lista
        self.malo(condiciones=[{"campo": "P24", "op": "contiene", "valor": 1}])      # «contiene» no es de números
        self.malo(condiciones=[{"campo": "Number", "op": "<", "valor": "a"}])        # «<» no es de texto
        self.malo(condiciones=[{"campo": "P24", "op": "<", "valor": "treinta"}])
        self.malo(condiciones=[{"campo": "P24", "op": "<", "valor": float("nan")}])
        self.malo(condiciones=[{"campo": "P24", "op": "<", "valor": float("inf")}])
        self.malo(condiciones=[{"campo": "Parto", "op": "<", "valor": "ayer"}])      # fecha inválida
        self.malo(condiciones=[{"campo": "P24", "op": "<", "valor": True}])          # un booleano no es un número
        self.malo(condiciones=[{"campo": "P24", "op": "<", "valor": 1}] * (im.CONDICIONES_MAX + 1))

    def test_valores_de_texto_peligrosos(self):
        for v in ("x'; DROP TABLE BasicAnimal--", "a;b", "a--b", "/*", "a\nb", "x" * 61, "\x00"):
            self.malo(condiciones=[{"campo": "Number", "op": "contiene", "valor": v}])

    def test_orden_invalido(self):
        self.malo(orden={"campo": "P24", "dir": -1})              # no está entre las columnas
        self.malo(orden={"campo": "Number", "dir": 2})
        self.malo(orden={"campo": "Number; --", "dir": 1})

    def test_definicion_que_no_es_un_diccionario(self):
        for malo in (None, [], "x", 3):
            with self.assertRaises(im.InformeError):
                im.compilar(malo, CAT, BLOQUES, herd=1)

    def test_un_campo_que_pide_un_bloque_inexistente_es_un_error_de_catalogo(self):
        cat = dict(CAT, Raro=Campo("Raro", "Raro", "X", "num", "zz.v", bloque="zz"))
        with self.assertRaises(im.InformeError):
            im.compilar({"columnas": [{"campo": "Raro"}]}, cat, BLOQUES, herd=1)


class TestValores(unittest.TestCase):
    def test_numero_literal(self):
        self.assertIn("ord.p24 < 30", comp(columnas=[{"campo": "P24"}], condiciones=[{"campo": "P24", "op": "<", "valor": 30}])["sql"])
        self.assertIn("ord.p24 >= 30.5", comp(columnas=[{"campo": "P24"}], condiciones=[{"campo": "P24", "op": ">=", "valor": "30,5"}])["sql"])

    def test_texto_con_comilla_se_duplica(self):
        sql = comp(condiciones=[{"campo": "GroupName", "op": "contiene", "valor": "Rodeo 4 - Baja"}])["sql"]
        self.assertIn("ag.Name LIKE N'%Rodeo 4 - Baja%'", sql)
        sql = comp(condiciones=[{"campo": "GroupName", "op": "es", "valor": "O'Brien"}])["sql"]
        self.assertIn("ag.Name = N'O''Brien'", sql)

    def test_los_comodines_de_like_no_se_cuelan(self):
        sql = comp(condiciones=[{"campo": "Number", "op": "contiene", "valor": "10%"}])["sql"]
        self.assertIn("LIKE N'%10[%]%'", sql)

    def test_fecha(self):
        sql = comp(columnas=[{"campo": "Parto"}], condiciones=[{"campo": "Parto", "op": ">=", "valor": "2025-09-16"}])["sql"]
        self.assertIn("CAST(rep.parto AS date) >= CONVERT(date, '2025-09-16', 23)", sql)

    def test_varias_condiciones_se_unen_con_and(self):
        sql = comp(columnas=[{"campo": "P24"}, {"campo": "Dim"}],
                   condiciones=[{"campo": "P24", "op": "<", "valor": 33}, {"campo": "Dim", "op": "<=", "valor": 60}])["sql"]
        self.assertIn("AND ord.p24 < 33\n  AND ord.dim <= 60", sql)


class TestEsUnoDe(unittest.TestCase):
    """El operador «en» (es uno de): la condición más común de los informes de DelPro («rodeo 1, 2 o 3») y de lo que se pide en palabras.
    Los valores se validan UNO por UNO con las mismas reglas que los de «=» y «es»: nada de lo que viene en la lista llega crudo a la consulta."""

    def sql(self, **kw):
        return comp(**kw)["sql"]

    def test_numeros(self):
        sql = self.sql(condiciones=[{"campo": "Lact", "op": "en", "valor": [1, 2]}])
        self.assertIn("rep.lact IN (1, 2)", sql)

    def test_numeros_como_texto_con_coma_decimal(self):
        self.assertIn("rep.n IN (1.5, 2)", self.sql(condiciones=[{"campo": "RepN", "op": "en", "valor": ["1,5", "2"]}]))

    def test_texto_con_comillas_se_duplican(self):
        sql = self.sql(condiciones=[{"campo": "GroupName", "op": "en", "valor": ["Rodeo 1", "O'Brien"]}])
        self.assertIn("ag.Name IN (N'Rodeo 1', N'O''Brien')", sql)

    def test_un_solo_valor_tambien_vale(self):
        self.assertIn("rep.lact IN (3)", self.sql(condiciones=[{"campo": "Lact", "op": "en", "valor": [3]}]))

    def test_los_repetidos_se_sacan_y_el_orden_se_conserva(self):
        self.assertIn("rep.lact IN (2, 1)", self.sql(condiciones=[{"campo": "Lact", "op": "en", "valor": [2, 1, 2, "1"]}]))

    def test_el_sql_pasa_la_validacion_de_solo_lectura_de_la_app(self):
        db.validate_sql(self.sql(condiciones=[{"campo": "GroupName", "op": "en", "valor": ["Rodeo 1", "Rodeo 2"]}]))

    def test_se_combina_con_otras_condiciones(self):
        sql = self.sql(columnas=[{"campo": "P24"}], condiciones=[{"campo": "Lact", "op": "en", "valor": [1, 2]}, {"campo": "P24", "op": "<", "valor": 30}])
        self.assertIn("AND rep.lact IN (1, 2)\n  AND ord.p24 < 30", sql)

    def test_la_poblacion_del_grafico_tambien_lo_lleva(self):
        sql = im.poblacion_sql({"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Lact", "op": "en", "valor": [1, 2]}]}, CAT, BLOQUES, herd=1)
        self.assertIn("rep.lact IN (1, 2)", sql)

    def malo(self, **cond):
        with self.assertRaises(im.InformeError):
            comp(condiciones=[dict({"campo": "Lact", "op": "en"}, **cond)])

    def test_la_lista_tiene_que_ser_una_lista_con_algo(self):
        for v in (5, "1, 2", None, {"a": 1}, [], ()):
            self.malo(valor=v)

    def test_demasiados_valores(self):
        self.malo(valor=list(range(im.LISTA_MAX + 1)))
        comp(condiciones=[{"campo": "Lact", "op": "en", "valor": list(range(im.LISTA_MAX))}])             # justo el tope: sirve

    def test_cada_valor_se_valida_como_un_valor_suelto(self):
        self.malo(valor=[1, "dos"])
        self.malo(valor=[1, True])
        self.malo(valor=[1, float("nan")])
        self.malo(valor=[1, [2]])
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "GroupName", "op": "en", "valor": ["Rodeo 1", "x'; DROP TABLE BasicAnimal--"]}])
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "GroupName", "op": "en", "valor": ["Rodeo 1", 7]}])                # un número en un campo de texto
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "GroupName", "op": "en", "valor": ["a" * 61]}])

    def test_una_fecha_no_admite_el_operador(self):
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "Parto", "op": "en", "valor": ["2025-09-16"]}])

    def test_los_otros_operadores_siguen_pidiendo_un_valor_suelto(self):
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "Lact", "op": "=", "valor": [1, 2]}])
        with self.assertRaises(im.InformeError):
            comp(condiciones=[{"campo": "GroupName", "op": "es", "valor": ["Rodeo 1"]}])

    def test_describir(self):
        d = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Lact", "op": "en", "valor": [1, 2]},
                                                                {"campo": "GroupName", "op": "en", "valor": ["Rodeo 1", "Rodeo 2"]}]}
        self.assertEqual(im.describir_condiciones(d, CAT), ["Nº Lactación es uno de 1, 2", "Nombre del grupo es uno de «Rodeo 1», «Rodeo 2»"])

    def test_describir_no_se_rompe_con_una_lista_mal_formada(self):
        d = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Lact", "op": "en", "valor": "1, 2"}]}
        self.assertIsInstance(im.describir_condiciones(d, CAT), list)


class TestLimiteDeCondiciones(unittest.TestCase):
    def test_hasta_el_tope_sirve_y_uno_mas_no(self):
        una = {"campo": "P24", "op": "<", "valor": 1}
        comp(condiciones=[una] * im.CONDICIONES_MAX)
        with self.assertRaises(im.InformeError):
            comp(condiciones=[una] * (im.CONDICIONES_MAX + 1))

    def test_alcanza_para_traducir_un_filtro_de_delpro(self):
        self.assertGreaterEqual(im.CONDICIONES_MAX, 10)


class TestOrden(unittest.TestCase):
    def test_nulos_al_final_en_los_dos_sentidos(self):
        for d, palabra in ((1, "ASC"), (-1, "DESC")):
            sql = comp(columnas=[{"campo": "P24"}], orden={"campo": "P24", "dir": d})["sql"]
            self.assertIn("ORDER BY CASE WHEN ord.p24 IS NULL THEN 1 ELSE 0 END, ord.p24 " + palabra, sql)

    def test_sin_orden_se_ordena_por_el_primer_campo_para_que_el_resultado_sea_estable(self):
        self.assertIn("ORDER BY CASE WHEN b.Number IS NULL", comp()["sql"])


class TestResumenesPorColumna(unittest.TestCase):
    """Suma, promedio, mínimo, máximo y conteo por columna (los de DelPro), calculados en SQL sobre TODOS los animales del filtro:
    así el total es exacto aunque la lista se corte en el tope de filas."""

    def c(self, columnas, **kw):
        return comp(columnas=columnas, **kw)

    def test_sin_resumenes_no_hay_consulta_de_resumen(self):
        r = self.c([{"campo": "P24"}])
        self.assertIsNone(r["sql_resumen"])
        self.assertEqual(r["columnas"][0]["resumen"], "none")

    def test_cada_resumen(self):
        casos = {"sum": "SUM(CAST((ord.p24) AS float))", "avg": "AVG(CAST((ord.p24) AS float))",
                 "min": "MIN(CAST((ord.p24) AS float))", "max": "MAX(CAST((ord.p24) AS float))", "count": "COUNT(ord.p24)"}
        for kind, esperado in casos.items():
            r = self.c([{"campo": "P24", "resumen": kind}])
            self.assertIn(f"{esperado} AS s0", r["sql_resumen"], kind)
            self.assertIn("COUNT(*) AS n", r["sql_resumen"])
            self.assertEqual(r["columnas"][0]["resumen"], kind)

    def test_el_promedio_es_en_float_porque_avg_de_enteros_trunca(self):
        """AVG(int) de SQL Server devuelve un entero: el promedio de lactancias 1 y 2 daría 1 en vez de 1,5."""
        self.assertIn("AVG(CAST(", self.c([{"campo": "Dim", "resumen": "avg"}])["sql_resumen"])

    def test_fechas_solo_minimo_maximo_y_conteo_y_sin_convertir_a_float(self):
        r = self.c([{"campo": "Parto", "resumen": "min"}])
        self.assertIn("MIN(rep.parto) AS s0", r["sql_resumen"])
        for malo in ("sum", "avg"):
            with self.assertRaises(im.InformeError):
                self.c([{"campo": "Parto", "resumen": malo}])

    def test_texto_solo_conteo(self):
        self.assertIn("COUNT(b.Number)", self.c([{"campo": "Number", "resumen": "count"}])["sql_resumen"])
        for malo in ("sum", "avg", "min", "max"):
            with self.assertRaises(im.InformeError):
                self.c([{"campo": "Number", "resumen": malo}])

    def test_un_resumen_que_no_existe(self):
        for malo in ("mediana", "", "AVG", None, 3, "avg; DROP"):
            with self.assertRaises(im.InformeError):
                self.c([{"campo": "P24", "resumen": malo}])

    def test_la_consulta_de_resumen_comparte_filtros_y_une_solo_lo_que_necesita(self):
        r = self.c([{"campo": "Number"}, {"campo": "Parto"}, {"campo": "P24", "resumen": "avg"}],
                   condiciones=[{"campo": "Dim", "op": ">=", "valor": 0}], incluir_descartados=True)
        res, det = r["sql_resumen"], r["sql"]
        self.assertIn(" ord ON ord.animal = b.OID", res)
        self.assertNotIn(" rep ON ", res)                         # «Parto» no tiene resumen: el resumen no lo necesita
        self.assertIn(" rep ON ", det)
        for pieza in ("b.GCRecord IS NULL", "b.Number > 0", "ord.dim >= 0", rebano.filtro_del_animal("b.OID", 1)):
            self.assertIn(pieza, res)
            self.assertIn(pieza, det)
        self.assertNotIn("TOP (", res)                              # ni tope ni orden propios (el de «incluir descartados» va adentro de su subconsulta)
        self.assertNotIn("\nORDER BY", res)

    def test_pasa_la_validacion_de_solo_lectura(self):
        r = self.c([{"campo": "P24", "resumen": "avg"}, {"campo": "Parto", "resumen": "max"}, {"campo": "Number", "resumen": "count"}],
                   agrupar_por=["GroupName", "Lact"])
        db.validate_sql(r["sql"])
        db.validate_sql(r["sql_resumen"])


class TestAgrupar(unittest.TestCase):
    def c(self, agrupar, columnas=None, **kw):
        return comp(columnas=columnas or [{"campo": "Number"}, {"campo": "P24", "resumen": "avg"}], agrupar_por=agrupar, **kw)

    def test_un_nivel(self):
        r = self.c(["GroupName"])
        res = r["sql_resumen"]
        self.assertIn("ag.Name AS g0, GROUPING(ag.Name) AS gr0", res)
        self.assertIn("GROUP BY ROLLUP(ag.Name)", res)
        self.assertIn("AbstractGroup ag", res)
        self.assertEqual([g["id"] for g in r["agrupar"]], ["GroupName"])
        self.assertEqual(r["agrupar"][0]["alias"], "g0")

    def test_dos_niveles(self):
        r = self.c(["GroupName", "Lact"])
        self.assertIn("GROUP BY ROLLUP(ag.Name, rep.lact)", r["sql_resumen"])
        self.assertIn("rep.lact AS g1, GROUPING(rep.lact) AS gr1", r["sql_resumen"])

    def test_el_detalle_trae_la_clave_de_cada_grupo_y_se_ordena_por_ella_primero(self):
        r = self.c(["GroupName"], orden={"campo": "P24", "dir": -1})
        self.assertIn("ag.Name AS g0", r["sql"])
        self.assertIn("ORDER BY CASE WHEN ag.Name IS NULL THEN 1 ELSE 0 END, ag.Name, CASE WHEN ord.p24 IS NULL", r["sql"])

    def test_sin_agrupar_el_detalle_no_trae_claves(self):
        r = comp(columnas=[{"campo": "P24", "resumen": "avg"}])
        self.assertNotIn(" AS g0", r["sql"])
        self.assertEqual(r["agrupar"], [])
        self.assertNotIn("ROLLUP", r["sql_resumen"])

    def test_solo_se_agrupa_por_campos_agrupables(self):
        for malo in (["P24"], ["Number"], ["NoExiste"], ["GroupName", "Lact", "RepN"], ["GroupName", "GroupName"], "GroupName", [3],
                     ["GroupName; --"]):
            with self.assertRaises(im.InformeError):
                self.c(malo)

    def test_sin_detalle(self):
        r = self.c(["GroupName"], detalle=False)
        self.assertIsNone(r["sql"])
        self.assertIsNotNone(r["sql_resumen"])
        r = comp(columnas=[{"campo": "P24", "resumen": "avg"}], detalle=False)
        self.assertIsNone(r["sql"])

    def test_sin_detalle_hace_falta_algo_que_resumir(self):
        with self.assertRaises(im.InformeError):
            comp(columnas=[{"campo": "P24"}], detalle=False)
        with self.assertRaises(im.InformeError):
            comp(detalle="no")

    def test_agrupar_sin_resumenes_igual_cuenta_animales(self):
        r = comp(columnas=[{"campo": "Number"}], agrupar_por=["GroupName"])
        self.assertIn("COUNT(*) AS n", r["sql_resumen"])


class TestCalculados(unittest.TestCase):
    CALC = {"id": "calc_1", "etiqueta": "Kg por día en ordeño", "formula": "[P24] / [Dim]", "decimales": 2, "unidad": "kg"}

    def c(self, calculados=None, columnas=None, **kw):
        return comp(calculados=[self.CALC] if calculados is None else calculados,
                    columnas=columnas or [{"campo": "Number"}, {"campo": "calc_1"}], **kw)

    def test_se_traduce_a_sql_en_la_columna(self):
        r = self.c()
        self.assertIn("(CAST((ord.p24) AS float) / NULLIF(CAST((ord.dim) AS float), 0)) AS c1", r["sql"])

    def test_metadatos(self):
        m = self.c()["columnas"][1]
        self.assertEqual((m["id"], m["etiqueta"], m["tipo"], m["unidad"], m["decimales"], m["calculado"]),
                         ("calc_1", "Kg por día en ordeño", "num", "kg", 2, True))
        self.assertFalse(self.c()["columnas"][0]["calculado"])

    def test_trae_los_bloques_de_todos_los_campos_que_usa(self):
        r = self.c(calculados=[dict(self.CALC, formula="[P24] / [RepN]")])
        self.assertEqual(r["sql"].count(" ord ON "), 1)
        self.assertEqual(r["sql"].count(" rep ON "), 1)

    def test_puede_usarse_en_condiciones_orden_y_resumenes(self):
        r = self.c(columnas=[{"campo": "Number"}, {"campo": "calc_1", "resumen": "avg"}],
                   condiciones=[{"campo": "calc_1", "op": ">", "valor": 1}], orden={"campo": "calc_1", "dir": -1})
        self.assertIn("NULLIF(CAST((ord.dim) AS float), 0)) > 1", r["sql"])
        self.assertIn("AVG(CAST(((CAST((ord.p24) AS float) / NULLIF(CAST((ord.dim) AS float), 0))) AS float))", r["sql_resumen"])
        self.assertIn("ORDER BY CASE WHEN (CAST((ord.p24) AS float)", r["sql"])

    def test_se_puede_resumir_un_calculado_por_rodeo_sin_detalle(self):
        r = self.c(columnas=[{"campo": "calc_1", "resumen": "avg"}], agrupar_por=["GroupName"], detalle=False)
        db.validate_sql(r["sql_resumen"])

    def test_hereda_la_advertencia_de_un_campo_sin_verificar(self):
        self.assertTrue(self.c()["columnas"][1]["verificado"])
        r = self.c(calculados=[dict(self.CALC, formula="[Sin] * 2")])
        self.assertFalse(r["columnas"][1]["verificado"])

    def test_el_que_no_se_usa_igual_se_valida(self):
        with self.assertRaises(im.InformeError):
            self.c(calculados=[dict(self.CALC, formula="[NoExiste] * 2")], columnas=[{"campo": "Number"}])

    def test_ids_y_cantidades(self):
        malos = [dict(self.CALC, id="P24"), dict(self.CALC, id="calc_"), dict(self.CALC, id="calc_1; x"), dict(self.CALC, id="otro"),
                 dict(self.CALC, id="Number"), dict(self.CALC, id="calc_" + "x" * 30)]
        for m in malos:
            with self.assertRaises(im.InformeError):
                self.c(calculados=[m], columnas=[{"campo": "Number"}])
        with self.assertRaises(im.InformeError):
            self.c(calculados=[self.CALC, dict(self.CALC)], columnas=[{"campo": "Number"}])                   # repetido
        muchos = [dict(self.CALC, id=f"calc_{i}") for i in range(im.CALCULADOS_MAX + 1)]
        with self.assertRaises(im.InformeError):
            self.c(calculados=muchos, columnas=[{"campo": "Number"}])
        for no_lista in ("x", {"id": "calc_1"}, 5):
            with self.assertRaises(im.InformeError):
                self.c(calculados=no_lista, columnas=[{"campo": "Number"}])

    def test_etiqueta_decimales_y_unidad(self):
        for malo in (dict(self.CALC, etiqueta=""), dict(self.CALC, etiqueta="x" * 41), dict(self.CALC, etiqueta="con\nsalto"),
                     dict(self.CALC, etiqueta=None), dict(self.CALC, decimales=7), dict(self.CALC, decimales=-1),
                     dict(self.CALC, decimales="2"), dict(self.CALC, decimales=True), dict(self.CALC, unidad="x" * 13),
                     dict(self.CALC, unidad="kg'; DROP"), dict(self.CALC, unidad=5)):
            with self.assertRaises(im.InformeError):
                self.c(calculados=[malo], columnas=[{"campo": "Number"}])
        m = self.c(calculados=[{"id": "calc_2", "etiqueta": "Sin extras", "formula": "[P24] * 2"}], columnas=[{"campo": "calc_2"}])["columnas"][0]
        self.assertEqual((m["decimales"], m["unidad"]), (2, ""))

    def test_un_calculado_no_puede_usar_otro_calculado(self):
        otro = {"id": "calc_2", "etiqueta": "Otro", "formula": "[calc_1] * 2"}
        with self.assertRaises(im.InformeError):
            self.c(calculados=[self.CALC, otro], columnas=[{"campo": "calc_2"}])

    def test_un_calculado_no_se_agrupa(self):
        with self.assertRaises(im.InformeError):
            self.c(agrupar_por=["calc_1"])

    def test_una_formula_mala_dice_por_que_y_de_cual_campo_calculado_habla(self):
        with self.assertRaises(im.InformeError) as cm:
            self.c(calculados=[dict(self.CALC, formula="[P24] +")], columnas=[{"campo": "Number"}])
        self.assertIn("incompleta", str(cm.exception))
        self.assertIn("Kg por día en ordeño", str(cm.exception))

    def test_pasa_la_validacion_de_solo_lectura(self):
        r = self.c(condiciones=[{"campo": "calc_1", "op": ">", "valor": 1}], orden={"campo": "calc_1", "dir": 1})
        db.validate_sql(r["sql"])


class TestDescribirConCalculados(unittest.TestCase):
    def test_una_condicion_sobre_un_calculado_usa_su_nombre_y_se_dice_como_se_agrupa(self):
        d = {"columnas": [{"campo": "calc_1"}], "calculados": [{"id": "calc_1", "etiqueta": "Kg por día", "formula": "[P24] / [Dim]"}],
             "condiciones": [{"campo": "calc_1", "op": ">", "valor": 1}], "agrupar_por": ["GroupName"]}
        self.assertEqual(im.describir_condiciones(d, CAT), ["Kg por día > 1", "Agrupado por Nombre del grupo"])


class TestEjecutarConResumenes(unittest.TestCase):
    """El ejecutor corre el detalle y/o el resumen y arma {filas, claves_filas, grupos, totales}."""

    def correr(self, defn, detalle=None, resumen=None):
        llamadas = []

        def falso(sql, validate=True, tambo=None, max_rows=None):
            es_detalle = " AS c0" in sql
            llamadas.append("detalle" if es_detalle else "resumen")
            return {"columns": [], "rows": (detalle if es_detalle else resumen) or [], "truncated": False}
        r = im.ejecutar(defn, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        return r, llamadas

    def test_un_nivel_con_totales(self):
        defn = {"columnas": [{"campo": "Number"}, {"campo": "P24", "resumen": "avg"}], "agrupar_por": ["GroupName"]}
        det = [["3250", 30.0, "Rodeo 1"], ["4300", 20.0, "Rodeo 1"], ["4317", 40.0, "Rodeo 10"], ["4400", None, "Rodeo 2"]]
        res = [["Rodeo 2", 0, 1, None], ["Rodeo 10", 0, 1, 40.0], ["Rodeo 1", 0, 2, 25.0], [None, 1, 4, 30.0]]
        r, llamadas = self.correr(defn, det, res)
        self.assertEqual(llamadas, ["detalle", "resumen"])
        self.assertEqual(r["filas"], [["3250", 30.0], ["4300", 20.0], ["4317", 40.0], ["4400", None]])
        self.assertEqual(r["claves_filas"], [["Rodeo 1"], ["Rodeo 1"], ["Rodeo 10"], ["Rodeo 2"]])
        self.assertEqual([(g["nivel"], g["claves"], g["n"]) for g in r["grupos"]],
                         [(1, ["Rodeo 1"], 2), (1, ["Rodeo 2"], 1), (1, ["Rodeo 10"], 1)])            # orden natural: 2 antes que 10
        self.assertEqual(r["grupos"][0]["resumenes"], {"c1": 25.0})
        self.assertEqual(r["totales"], {"n": 4, "resumenes": {"c1": 30.0}})
        self.assertEqual(r["agrupar"][0]["id"], "GroupName")

    def test_dos_niveles_con_subtotales(self):
        defn = {"columnas": [{"campo": "P24", "resumen": "sum"}], "agrupar_por": ["GroupName", "Lact"], "detalle": False}
        res = [["R1", 0, 1, 0, 3, 30.0], ["R1", 0, 2, 0, 1, 10.0], ["R1", 0, None, 1, 4, 40.0], ["R2", 0, 1, 0, 2, 5.0],
               ["R2", 0, None, 1, 2, 5.0], [None, 1, None, 1, 6, 45.0]]
        r, llamadas = self.correr(defn, None, res)
        self.assertEqual(llamadas, ["resumen"])                                   # sin detalle: una sola consulta
        self.assertEqual(r["filas"], [])
        self.assertEqual([(g["nivel"], g["claves"], g["n"]) for g in r["grupos"]],
                         [(1, ["R1"], 4), (2, ["R1", 1], 3), (2, ["R1", 2], 1), (1, ["R2"], 2), (2, ["R2", 1], 2)])
        self.assertEqual(r["totales"]["n"], 6)

    def test_un_grupo_sin_dato_va_al_final_de_sus_hermanos(self):
        defn = {"columnas": [{"campo": "P24", "resumen": "sum"}], "agrupar_por": ["Lact"], "detalle": False}
        res = [[None, 0, 2, 5.0], [1, 0, 3, 7.0], [2, 0, 1, 1.0], [None, 1, 6, 13.0]]
        r, _ = self.correr(defn, None, res)
        self.assertEqual([g["claves"] for g in r["grupos"]], [[1], [2], [None]])

    def test_sin_resumenes_ni_grupos_solo_corre_el_detalle(self):
        r, llamadas = self.correr({"columnas": [{"campo": "Number"}]}, [["3250"]])
        self.assertEqual(llamadas, ["detalle"])
        self.assertEqual((r["grupos"], r["totales"], r["claves_filas"]), ([], None, None))

    def test_si_la_consulta_de_resumen_no_trae_la_fila_de_totales_son_cero_animales(self):
        defn = {"columnas": [{"campo": "P24", "resumen": "avg"}]}
        r, _ = self.correr(defn, [], [])
        self.assertEqual(r["totales"], {"n": 0, "resumenes": {"c0": None}})

    def test_sin_agrupar_los_totales_son_la_unica_fila(self):
        defn = {"columnas": [{"campo": "P24", "resumen": "avg"}]}
        r, _ = self.correr(defn, [[10.0], [20.0]], [[2, 15.0]])
        self.assertEqual(r["totales"], {"n": 2, "resumenes": {"c0": 15.0}})
        self.assertEqual(r["grupos"], [])

    def test_dice_si_el_resultado_trae_la_lista_de_animales(self):
        # Un informe «solo resumen» y uno con detalle pero sin ningún animal tienen las dos `filas` vacías: el Excel y la pantalla
        # necesitan saber cuál es cuál para armar la hoja correcta.
        r, _ = self.correr({"columnas": [{"campo": "P24", "resumen": "sum"}], "detalle": False}, None, [[3, 9.0]])
        self.assertIs(r["detalle"], False)
        r, _ = self.correr({"columnas": [{"campo": "Number"}]}, [["3250"]])
        self.assertIs(r["detalle"], True)
        r, _ = self.correr({"columnas": [{"campo": "P24", "resumen": "avg"}]}, [], [])
        self.assertIs(r["detalle"], True)

    def test_el_tope_corta_el_detalle_pero_no_los_totales(self):
        defn = {"columnas": [{"campo": "P24", "resumen": "sum"}], "limite": 2, "agrupar_por": ["GroupName"]}
        det = [[1.0, "A"], [2.0, "A"], [3.0, "B"]]                                 # llegó una de más: hay más que el tope
        res = [["A", 0, 5, 6.0], ["B", 0, 4, 9.0], [None, 1, 9, 15.0]]
        r, _ = self.correr(defn, det, res)
        self.assertTrue(r["truncado"])
        self.assertEqual(len(r["filas"]), 2)
        self.assertEqual(len(r["claves_filas"]), 2)
        self.assertEqual(r["totales"]["n"], 9)                                    # el total cuenta TODOS los animales, no solo los 2 que se ven


class TestPoblacion(unittest.TestCase):
    """`poblacion_sql`: los animales del informe como subconsulta (`SELECT b.OID ...`), para el gráfico. Aplica las condiciones, el
    rebaño y los bloques igual que `compilar`: no puede haber dos criterios distintos de «los animales de este informe»."""

    def pob(self, **kw):
        d = {"columnas": [{"campo": "Number"}]}
        d.update(kw)
        return im.poblacion_sql(d, CAT, BLOQUES, herd=1)

    def test_es_una_subconsulta_de_oid_lista_para_un_in(self):
        sql = self.pob()
        self.assertTrue(sql.startswith("SELECT b.OID\nFROM BasicAnimal b"))
        for no_va in ("TOP", "ORDER BY", " AS c0", "GROUP BY"):
            self.assertNotIn(no_va, sql)                                  # dentro de un IN (...) no puede haber TOP ni ORDER BY
        for va in ("b.GCRecord IS NULL", "b.Number > 0", "g_tb.Herd = 1"):
            self.assertIn(va, sql)

    def test_sin_condiciones_no_une_nada(self):
        self.assertNotIn("LEFT JOIN", self.pob())

    def test_una_condicion_trae_su_bloque_y_solo_ese(self):
        sql = self.pob(condiciones=[{"campo": "P24", "op": "<", "valor": 30}])
        self.assertIn(" ord ON ord.animal = b.OID", sql)
        self.assertNotIn(" rep ON ", sql)
        self.assertIn("ord.p24 < 30", sql)

    def test_ignora_las_columnas_el_orden_y_el_limite(self):
        sql = self.pob(columnas=[{"campo": "Number"}, {"campo": "Parto"}, {"campo": "P24", "resumen": "avg"}], orden={"campo": "P24", "dir": -1},
                       limite=10, agrupar_por=["GroupName"], detalle=False)
        self.assertNotIn("LEFT JOIN", sql)

    def test_una_condicion_sobre_un_campo_calculado(self):
        sql = self.pob(calculados=[{"id": "calc_1", "etiqueta": "Doble", "formula": "[P24] * 2"}],
                       condiciones=[{"campo": "calc_1", "op": ">", "valor": 10}])
        self.assertIn(" ord ON ord.animal = b.OID", sql)
        self.assertIn("* CAST(2 AS float)", sql)

    def test_los_descartados_ubican_a_los_que_se_fueron(self):
        self.assertIn("AnimalDaily d_tb", self.pob(incluir_descartados=True))
        self.assertNotIn("AnimalDaily d_tb", self.pob())

    def test_la_fecha_de_referencia_llega_a_las_condiciones(self):
        cat = dict(CAT, Del=Campo("Del", "Días en ordeño", "General", "num", "DATEDIFF(day, rep.parto, @HOY)", bloque="rep"))
        sql = im.poblacion_sql({"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Del", "op": ">=", "valor": 300}]}, cat, BLOQUES,
                               herd=1, hoy="2026-07-30")
        self.assertIn("DATEDIFF(day, rep.parto, CONVERT(date, '2026-07-30', 23)) >= 300", sql)

    def test_valida_lo_mismo_que_compilar(self):
        malos = [{"condiciones": [{"campo": "NoExiste", "op": "=", "valor": 1}]},
                 {"condiciones": [{"campo": "P24", "op": "like", "valor": 1}]},
                 {"condiciones": [{"campo": "Number", "op": "es", "valor": "x'; DROP TABLE a;--"}]},
                 {"condiciones": [{"campo": "P24", "op": "<", "valor": 1}] * (im.CONDICIONES_MAX + 1)},
                 {"incluir_descartados": "si"}]
        for malo in malos:
            with self.assertRaises(im.InformeError, msg=str(malo)[:60]):
                self.pob(**malo)
            with self.assertRaises(im.InformeError, msg=str(malo)[:60]):
                comp(**malo)

    def test_pasa_la_validacion_de_solo_lectura(self):
        db.validate_sql(self.pob(condiciones=[{"campo": "P24", "op": "<", "valor": 30}], incluir_descartados=True))

    def test_no_es_una_definicion(self):
        for malo in (None, "texto", [], 5):
            with self.assertRaises(im.InformeError):
                im.poblacion_sql(malo, CAT, BLOQUES, herd=1)


class TestDescribir(unittest.TestCase):
    def test_las_condiciones_en_palabras_para_el_excel_y_la_pantalla(self):
        d = {"columnas": [{"campo": "Number"}], "incluir_descartados": True,
             "condiciones": [{"campo": "P24", "op": "<", "valor": 30}, {"campo": "GroupName", "op": "contiene", "valor": "Rodeo"},
                             {"campo": "Parto", "op": ">=", "valor": "2025-09-16"}]}
        self.assertEqual(im.describir_condiciones(d, CAT),
                         ["Producción 24 h < 30", "Nombre del grupo contiene «Rodeo»", "Fecha del último parto ≥ 16/09/2025",
                          "Incluye animales descartados"])

    def test_las_fechas_pueden_llevar_el_dia_de_la_semana(self):
        d = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Parto", "op": ">=", "valor": "2026-08-27"}]}
        self.assertEqual(im.describir_condiciones(d, CAT), ["Fecha del último parto ≥ 27/08/2026"])               # lo de siempre no cambia
        self.assertEqual(im.describir_condiciones(d, CAT, con_dia=True), ["Fecha del último parto ≥ jue 27/08/2026"])

    def test_con_dia_una_fecha_mal_formada_no_rompe(self):
        d = {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "Parto", "op": ">=", "valor": "2026-13-45"}]}
        self.assertIsInstance(im.describir_condiciones(d, CAT, con_dia=True), list)

    def test_sin_condiciones(self):
        self.assertEqual(im.describir_condiciones({"columnas": [{"campo": "Number"}]}, CAT), [])

    def test_ignora_lo_que_no_entiende_sin_romper(self):
        self.assertEqual(im.describir_condiciones({"condiciones": [{"campo": "NoExiste", "op": "=", "valor": 1}, 5]}, CAT), [])


class TestEjecutar(unittest.TestCase):
    def test_mide_cuanto_tardo(self):
        falso = lambda sql, validate=True, tambo=None, max_rows=None: {"columns": ["c0"], "rows": [], "truncated": False}
        r = im.ejecutar({"columnas": [{"campo": "Number"}]}, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        self.assertIsInstance(r["segundos"], float)
        self.assertGreaterEqual(r["segundos"], 0)

    def test_devuelve_columnas_y_filas(self):
        visto = {}

        def falso(sql, validate=True, tambo=None, max_rows=None):
            visto.update(sql=sql, tambo=tambo, max_rows=max_rows)
            return {"columns": ["c0", "c1"], "rows": [["3250", 25.1], ["4300", None]], "truncated": False}
        r = im.ejecutar({"columnas": [{"campo": "Number"}, {"campo": "P24"}]}, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        self.assertEqual(r["filas"], [["3250", 25.1], ["4300", None]])
        self.assertEqual([c["id"] for c in r["columnas"]], ["Number", "P24"])
        self.assertFalse(r["truncado"])
        self.assertEqual(visto["tambo"], "ponderosa")
        self.assertIn("FROM BasicAnimal b", visto["sql"])

    def test_si_hay_mas_filas_que_el_tope_lo_dice_y_devuelve_solo_el_tope(self):
        falso = lambda sql, validate=True, tambo=None, max_rows=None: {"columns": ["c0"], "rows": [[str(i)] for i in range(4)], "truncated": False}
        r = im.ejecutar({"columnas": [{"campo": "Number"}], "limite": 3}, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        self.assertTrue(r["truncado"])
        self.assertEqual(len(r["filas"]), 3)

    def test_justo_en_el_tope_no_es_truncado(self):
        falso = lambda sql, validate=True, tambo=None, max_rows=None: {"columns": ["c0"], "rows": [["1"], ["2"], ["3"]], "truncated": False}
        r = im.ejecutar({"columnas": [{"campo": "Number"}], "limite": 3}, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        self.assertFalse(r["truncado"])
        self.assertEqual(len(r["filas"]), 3)

    def test_no_ejecuta_nada_si_la_definicion_es_mala(self):
        llamado = []
        falso = lambda *a, **k: llamado.append(1)
        with self.assertRaises(im.InformeError):
            im.ejecutar({"columnas": [{"campo": "No"}]}, "ponderosa", CAT, BLOQUES, run=falso, herd=1)
        self.assertEqual(llamado, [])


class TestOrdenNumericoDelNumero(unittest.TestCase):
    """Con el catálogo REAL: el Número del animal se muestra como texto pero se ordena como número («1, 2, 10», no «1, 10, 2»), que es el orden de DelPro.
    45 de los 58 informes de animales del tambo salen ordenados por ese campo (con orden explícito o por ser la primera columna)."""

    def sql(self, **kw):
        d = {"columnas": [{"campo": "Number"}]}
        d.update(kw)
        return im.compilar(d, herd=1)["sql"]

    def test_el_orden_por_defecto_es_numerico(self):
        sql = self.sql()
        self.assertIn("ORDER BY CASE WHEN b.Number IS NULL THEN 1 ELSE 0 END, b.Number ASC", sql)
        self.assertIn("CAST(b.Number AS nvarchar(20)) AS c0", sql)                       # lo que se muestra sigue siendo el texto

    def test_el_orden_explicito_tambien(self):
        sql = self.sql(orden={"campo": "Number", "dir": -1})
        self.assertIn("b.Number DESC", sql)
        self.assertNotIn("CAST(b.Number AS nvarchar(20)) DESC", sql)

    def test_un_campo_sin_expresion_de_orden_se_ordena_con_la_suya(self):
        self.assertIn("ag.Name ASC", self.sql(columnas=[{"campo": "AnimalGroupName"}]))

    def test_dentro_de_un_grupo_el_orden_del_numero_sigue_siendo_numerico(self):
        sql = self.sql(columnas=[{"campo": "Number"}, {"campo": "AnimalGroupName"}], agrupar_por=["AnimalGroupName"])
        self.assertIn("ag.Name, CASE WHEN b.Number IS NULL THEN 1 ELSE 0 END, b.Number ASC", sql)
        db.validate_sql(sql)


class TestUnaFechaSeComparaPorDia(unittest.TestCase):
    """Revisión final (24/11/2026, ia #1): la fecha del último parto es `MAX(DateAndTime)`, o sea que puede traer HORA. Contra «CONVERT(date, ...)» un datetime se compara con las
    00:00 de ese día: «=» solo calzaba con partos a medianoche exacta y «<=» dejaba afuera los partos de ese día. Para cualquier campo de tipo fecha se compara el DÍA."""

    def sql(self, op, valor="2026-08-15", **kw):
        return comp(columnas=[{"campo": "Parto"}], condiciones=[{"campo": "Parto", "op": op, "valor": valor}], **kw)["sql"]

    def test_los_cinco_operadores_comparan_el_dia_del_campo(self):
        for op in ("=", "<", "<=", ">", ">="):
            self.assertIn(f"CAST(rep.parto AS date) {op} CONVERT(date, '2026-08-15', 23)", self.sql(op), op)

    def test_la_condicion_no_deja_el_campo_pelado(self):
        for op in ("=", "<=", ">"):
            donde = self.sql(op).split("WHERE", 1)[1].split("ORDER BY", 1)[0]
            self.assertNotIn(f"AND rep.parto {op} ", donde, op)

    def test_el_resto_del_informe_no_se_toca(self):
        sql = self.sql("<=")
        self.assertIn("rep.parto AS c0", sql)                                           # la columna que se muestra sigue siendo el campo tal cual
        self.assertIn("ORDER BY CASE WHEN rep.parto IS NULL THEN 1 ELSE 0 END, rep.parto ASC", sql)

    def test_el_grafico_sigue_a_la_misma_poblacion_con_la_misma_comparacion(self):
        pob = im.poblacion_sql({"columnas": [{"campo": "Parto"}], "condiciones": [{"campo": "Parto", "op": "=", "valor": "2026-08-15"}]}, CAT, BLOQUES, herd=1)
        self.assertIn("CAST(rep.parto AS date) = CONVERT(date, '2026-08-15', 23)", pob)

    def test_el_sql_pasa_la_validacion_de_solo_lectura(self):
        for op in ("=", "<", "<=", ">", ">="):
            db.validate_sql(self.sql(op))

    def test_un_numero_o_un_texto_se_comparan_como_siempre(self):
        self.assertIn("ord.p24 <= 30", comp(columnas=[{"campo": "P24"}], condiciones=[{"campo": "P24", "op": "<=", "valor": 30}])["sql"])
        self.assertIn("ag.Name = N'Rodeo 1'", comp(condiciones=[{"campo": "GroupName", "op": "es", "valor": "Rodeo 1"}])["sql"])

    def test_con_el_catalogo_real_la_fecha_del_ultimo_parto_se_compara_por_dia(self):
        sql = im.compilar({"columnas": [{"campo": "LastCalvingDate"}], "condiciones": [{"campo": "LastCalvingDate", "op": "<=", "valor": "2026-08-15"}]}, herd=1)["sql"]
        self.assertIn("CAST(pa.DateAndTime AS date) <= CONVERT(date, '2026-08-15', 23)", sql)


class TestElLimiteLoPidioElInforme(unittest.TestCase):
    """Revisión final (regresión #2): el corte de la lista puede ser el tope de 2.000 (un aviso) o el límite que eligió el informe («Listar hasta», «las 10 vacas que más producen»,
    que es lo que PIDIÓ). El resultado dice cuál de los dos: el Excel y el mensaje programado no pueden decir «se cortó en el tope» de algo que se pidió."""

    def correr(self, defn, filas=4):
        falso = lambda sql, validate=True, tambo=None, max_rows=None: {"columns": ["c0"], "rows": [[str(i)] for i in range(filas)], "truncated": False}
        return im.ejecutar(dict({"columnas": [{"campo": "Number"}]}, **defn), "ponderosa", CAT, BLOQUES, run=falso, herd=1)

    def test_con_limite_en_la_definicion_el_corte_es_pedido(self):
        r = self.correr({"limite": 3})
        self.assertTrue(r["truncado"])
        self.assertIs(r["limite_pedido"], True)

    def test_sin_limite_el_corte_es_el_tope(self):
        r = self.correr({})
        self.assertIs(r["limite_pedido"], False)

    def test_aunque_no_se_corte_dice_si_hubo_limite(self):
        r = self.correr({"limite": 10}, 2)
        self.assertFalse(r["truncado"])
        self.assertIs(r["limite_pedido"], True)


class TestDescribirConElLimite(unittest.TestCase):
    D = {"columnas": [{"campo": "Number"}], "limite": 10, "condiciones": [{"campo": "P24", "op": "<", "valor": 30}]}

    def test_con_con_limite_dice_hasta_donde_se_lista(self):
        self.assertEqual(im.describir_condiciones(self.D, CAT, con_limite=True), ["Producción 24 h < 30", "Lista limitada a las primeras 10 filas"])

    def test_una_sola_fila_va_en_singular(self):
        self.assertEqual(im.describir_condiciones(dict(self.D, limite=1), CAT, con_limite=True)[-1], "Lista limitada a la primera fila")

    def test_por_defecto_no_cambia_lo_de_siempre(self):
        self.assertEqual(im.describir_condiciones(self.D, CAT), ["Producción 24 h < 30"])           # «Se lee así» de la IA ya dice su propia línea de límite

    def test_sin_limite_o_con_uno_que_no_sirve_no_agrega_nada(self):
        for limite in (None, 0, -3, True, "10", 1.5):
            self.assertEqual(im.describir_condiciones(dict(self.D, limite=limite), CAT, con_limite=True), ["Producción 24 h < 30"], repr(limite))
        sin = {k: v for k, v in self.D.items() if k != "limite"}
        self.assertEqual(im.describir_condiciones(sin, CAT, con_limite=True), ["Producción 24 h < 30"])

    def test_va_antes_del_agrupado_y_de_los_descartados_o_despues_sin_romper_el_orden_de_las_demas(self):
        d = dict(self.D, incluir_descartados=True, agrupar_por=["GroupName"])
        r = im.describir_condiciones(d, CAT, con_limite=True)
        self.assertEqual(r[0], "Producción 24 h < 30")
        self.assertIn("Lista limitada a las primeras 10 filas", r)
        self.assertIn("Incluye animales descartados", r)


class TestDescartadosSonLosDelTambo(unittest.TestCase):
    """«Incluir descartados» suma SOLO a los del tambo que se fueron (`rebano.filtro_del_animal`: el rodeo de hoy o, sin rodeo, el del último registro diario) y nunca saca a los de hoy. Hasta el
    27/11/2026 se unía `filtro_historico` («alguna vez tuvo un registro en el rebaño»), que sumaba animales vivos de otros rebaños. Quiénes entran se prueba en `test_informes_descartados`."""

    def test_con_la_opcion_es_el_filtro_que_ubica_a_los_que_se_fueron(self):
        sql = comp(incluir_descartados=True)["sql"]
        self.assertIn(rebano.filtro_del_animal("b.OID", 1), sql)
        self.assertNotIn(" OR EXISTS (SELECT 1 FROM AnimalDaily d_tb", sql)
        db.validate_sql(sql)

    def test_sin_la_opcion_son_solo_los_de_hoy(self):
        sql = comp()["sql"]
        self.assertIn(rebano.filtro("b", 1), sql)
        self.assertNotIn("AnimalDaily d_tb", sql)

    def test_no_se_come_las_otras_condiciones(self):
        sql = comp(incluir_descartados=True, condiciones=[{"campo": "Dim", "op": ">=", "valor": 0}])["sql"]
        self.assertIn("ord.dim >= 0\n  AND " + rebano.filtro_del_animal("b.OID", 1), sql)

    def test_el_grafico_sigue_a_la_misma_poblacion(self):
        pob = im.poblacion_sql({"columnas": [{"campo": "Number"}], "incluir_descartados": True}, CAT, BLOQUES, herd=1)
        self.assertIn(rebano.filtro_del_animal("b.OID", 1), pob)


if __name__ == "__main__":
    unittest.main()
