"""Compilador de informes (informes_motor.py): una definición en JSON -> una consulta SELECT segura contra DDM.

Lo que importa: que NUNCA salga SQL que el usuario haya escrito (todo sale de una lista cerrada de campos), que ningún valor
pueda romper la consulta, que se una solo lo que las columnas elegidas necesitan, y que cada consulta pase la validación de
solo lectura que ya usa toda la app (`db.validate_sql`).

    C:\\Python314\\python.exe -m unittest tests.test_informes_motor -v
"""
import unittest

import db
import informes_motor as im
from informes_catalogo import Bloque, Campo

CAT = {
    "Number": Campo("Number", "Número del animal", "General", "txt", "b.Number"),
    "GroupName": Campo("GroupName", "Nombre del grupo", "General", "txt", "ag.Name", bloque="grupo"),
    "Dim": Campo("Dim", "Días en ordeño", "General", "num", "ord.dim", bloque="ord", decimales=0),
    "P24": Campo("P24", "Producción 24 h", "Ordeño", "num", "ord.p24", bloque="ord", unidad="kg", decimales=1,
                 resumenes=("sum", "avg", "min", "max", "count")),
    "Parto": Campo("Parto", "Fecha del último parto", "Reproducción", "fecha", "rep.parto", bloque="rep"),
}
BLOQUES = {
    "ord": Bloque("ord", "LEFT JOIN (SELECT d.BasicAnimal AS animal, MAX(d.DIM) AS dim, MAX(d.TotalYield) AS p24 "
                         "FROM AnimalDaily d GROUP BY d.BasicAnimal) ord ON ord.animal = b.OID"),
    "rep": Bloque("rep", "LEFT JOIN (SELECT r.BasicAnimal AS animal, MAX(r.LastLactationChangeDate) AS parto "
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

    def test_descartados_usan_el_filtro_historico(self):
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
        self.assertIn("rep.parto >= CONVERT(date, '2025-09-16', 23)", sql)

    def test_varias_condiciones_se_unen_con_and(self):
        sql = comp(columnas=[{"campo": "P24"}, {"campo": "Dim"}],
                   condiciones=[{"campo": "P24", "op": "<", "valor": 33}, {"campo": "Dim", "op": "<=", "valor": 60}])["sql"]
        self.assertIn("AND ord.p24 < 33\n  AND ord.dim <= 60", sql)


class TestOrden(unittest.TestCase):
    def test_nulos_al_final_en_los_dos_sentidos(self):
        for d, palabra in ((1, "ASC"), (-1, "DESC")):
            sql = comp(columnas=[{"campo": "P24"}], orden={"campo": "P24", "dir": d})["sql"]
            self.assertIn("ORDER BY CASE WHEN ord.p24 IS NULL THEN 1 ELSE 0 END, ord.p24 " + palabra, sql)

    def test_sin_orden_se_ordena_por_el_primer_campo_para_que_el_resultado_sea_estable(self):
        self.assertIn("ORDER BY CASE WHEN b.Number IS NULL", comp()["sql"])


class TestDescribir(unittest.TestCase):
    def test_las_condiciones_en_palabras_para_el_excel_y_la_pantalla(self):
        d = {"columnas": [{"campo": "Number"}], "incluir_descartados": True,
             "condiciones": [{"campo": "P24", "op": "<", "valor": 30}, {"campo": "GroupName", "op": "contiene", "valor": "Rodeo"},
                             {"campo": "Parto", "op": ">=", "valor": "2025-09-16"}]}
        self.assertEqual(im.describir_condiciones(d, CAT),
                         ["Producción 24 h < 30", "Nombre del grupo contiene «Rodeo»", "Fecha del último parto ≥ 16/09/2025",
                          "Incluye animales descartados"])

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


if __name__ == "__main__":
    unittest.main()
