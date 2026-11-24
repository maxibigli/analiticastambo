"""Catálogo real de campos de los informes: que esté bien armado (sin base de datos) y que cada campo compile a una consulta
que pase la validación de solo lectura.

    C:\\Python314\\python.exe -m unittest tests.test_informes_catalogo -v
"""
import re
import unittest

import db
import informes_catalogo as cat
import informes_motor as im

TIPOS = {"num", "txt", "fecha"}


class TestCatalogo(unittest.TestCase):
    def test_ids_validos_y_tipos_conocidos(self):
        for id_, c in cat.CATALOGO.items():
            self.assertEqual(id_, c.id)
            self.assertRegex(id_, r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
            self.assertIn(c.tipo, TIPOS, id_)
            self.assertTrue(c.etiqueta and c.categoria and c.expr, id_)

    def test_los_bloques_que_piden_los_campos_existen(self):
        todos = dict(cat.BLOQUES_NUCLEO, **cat.BLOQUES)
        for c in cat.CATALOGO.values():
            if c.bloque:
                self.assertIn(c.bloque, todos, c.id)
        for b in todos.values():
            for r in b.requiere:
                self.assertIn(r, todos, f"{b.id} requiere {r}")

    def test_cada_campo_compila_solo_y_pasa_la_validacion_de_solo_lectura(self):
        for id_ in cat.CATALOGO:
            r = im.compilar({"columnas": [{"campo": id_}]}, herd=1, hoy="2026-07-30")
            db.validate_sql(r["sql"])
            self.assertNotIn("@HOY", r["sql"], id_)

    def test_todos_juntos_compilan_y_pasan_la_validacion(self):
        # Un informe admite hasta `COLUMNAS_MAX` columnas: el catálogo entero se prueba en tandas de ese tamaño.
        ids = list(cat.CATALOGO)
        sqls = []
        for i in range(0, len(ids), im.COLUMNAS_MAX):
            sqls.append(im.compilar({"columnas": [{"campo": x} for x in ids[i:i + im.COLUMNAS_MAX]]}, herd=1)["sql"])
            db.validate_sql(sqls[-1])
        self.assertTrue(any("GETDATE()" in q for q in sqls))

    def test_cada_campo_de_fecha_se_compara_por_dia_con_cada_operador_y_pasa_la_validacion(self):
        """Revisión final (ia #1): un campo de fecha del catálogo puede traer hora (`MAX(DateAndTime)`): «=», «<=» y «>» comparan el DÍA."""
        fechas = [c for c in cat.CATALOGO.values() if c.tipo == "fecha"]
        self.assertTrue(fechas)
        for c in fechas:
            for op in im.OPERADORES["fecha"]:
                r = im.compilar({"columnas": [{"campo": c.id}], "condiciones": [{"campo": c.id, "op": op, "valor": "2026-08-15"}]}, herd=1, hoy="2026-08-27")
                self.assertIn(f"CAST({c.expr} AS date) {op} CONVERT(date, '2026-08-15', 23)", r["sql"], f"{c.id} {op}")
                db.validate_sql(r["sql"])
                p = im.poblacion_sql({"columnas": [{"campo": c.id}], "condiciones": [{"campo": c.id, "op": op, "valor": "2026-08-15"}]}, herd=1, hoy="2026-08-27")
                db.validate_sql(p)

    def test_los_bloques_no_multiplican_filas(self):
        """Un LEFT JOIN a algo que puede devolver varias filas por animal duplicaría animales en el informe. Cada bloque
        o agrupa por animal o une por una clave única de una fila por animal (AnimalReproductionInfo, AnimalGroup...)."""
        # Medido en la copia local (30/07/2026): `AnimalLatestHistoryIndex` tiene UNA fila por animal (0 repetidos en 10.530), `HistoryAnimalLactationInfo` una por (animal, lactancia)
        # (0 repetidos en 23.949), los eventos se unen por su OID y `ajustes` es un agregado sin grupo: una sola fila.
        for b in cat.BLOQUES.values():
            # Un `OUTER APPLY (SELECT TOP 1 ...)` devuelve a lo sumo UNA fila por construcción. `cambio_grupo` une tres tablas por su OID y `lact_ant` es una fila por (animal, lactancia).
            ok = ("GROUP BY" in b.join.upper() or "OUTER APPLY (SELECT TOP 1" in b.join
                  or b.id in ("repro", "lactprev", "parto", "indice", "ult_insem", "ult_aborto", "insem_efectiva", "insem_lact", "ajustes", "ult_control", "ult_descarte",
                              "leche_ayer", "leche_hoy", "cambio_grupo", "lact_ant"))
            self.assertTrue(ok, b.id)

    def test_lo_que_no_se_comparo_contra_delpro_queda_marcado(self):
        sin = {c.id for c in cat.CATALOGO.values() if not c.verificado}
        self.assertEqual(sin, {"CurrentReproductionStatus", "PeakYieldLastLactation",
                               # tanda 1: lo que sale de una cuenta nuestra y no de un dato que DelPro guarda
                               "InMilk", "AgeInMonths", "DaysSinceLastInsemination", "ExpectedCalvingDate", "DaysToCalving", "ExpectedDryOffDate", "DaysToDryOff",
                               # tanda 2
                               "LastCullDecisionDate", "DaysPregnant", "LatestSCC", "CMSFourteenDayAverage", "CMSTwentyOneDayAverage", "CMSThirtyDayAverage", "YieldYesterday", "YieldToday",
                               # tanda 3a
                               "LatestSCCTestDate", "LatestFat", "LatestFatDate", "LatestProtein", "LatestMilkTestYield", "LatestMilkTestYieldDate", "AgeInDays", "Sex",
                               "DaysSinceLastGroupChange", "PreviousGroupNumber", "PrevCalvingDate", "LastBcsValue", "LastBcsDryOffValue", "LastBcsCalvingValue", "LastBcsValue190DaysPregnant"})
        for c in cat.CATALOGO.values():
            if not c.verificado:
                self.assertTrue(c.nota, f"{c.id} sin verificar y sin nota que diga por qué")

    def test_los_valores_de_un_texto_cerrado_son_los_que_el_campo_devuelve(self):
        """`valores` es lo que la IA ve y lo único que acepta: tiene que ser EXACTAMENTE lo que escribe la expresión del campo, ni más ni menos."""
        con_valores = [c for c in cat.CATALOGO.values() if c.valores]
        self.assertIn("CurrentReproductionStatus", [c.id for c in con_valores])
        for c in con_valores:
            self.assertEqual(c.tipo, "txt", c.id)
            self.assertEqual(set(re.findall(r"THEN '([^']+)'|ELSE '([^']+)'", c.expr)), {(v, "") if c.expr.count(f"THEN '{v}'") else ("", v) for v in c.valores}, c.id)

    def test_el_orden_numerico_del_numero_del_animal(self):
        self.assertEqual(cat.CATALOGO["Number"].orden_expr, "b.Number")
        self.assertEqual([c.id for c in cat.CATALOGO.values() if c.orden_expr], ["Number"])

    def test_los_ids_siguen_el_nombre_de_delpro(self):
        # No es un capricho: sirve para importar después los informes que el tambo ya tiene en DelPro.
        for esperado in ("Number", "AnimalGroupName", "CurrentLactationNumber", "CurrentDIM", "SevenDayAverage",
                         "LastCalvingDate", "CurrentReproductionStatus", "DaysToPeakLastLactation", "PeakYieldLastLactation"):
            self.assertIn(esperado, cat.CATALOGO)


class TestInformesAgrupadosSobreElCatalogoReal(unittest.TestCase):
    """Fase 2 con el catálogo REAL (no uno de mentira): cada combinación de resumen y de agrupación compila a consultas que pasan
    el validador de solo lectura de db.py, que es el que las dejaría correr en producción."""

    def validar(self, defn):
        c = im.compilar(defn, herd=1, hoy="2026-07-30")
        for sql in (c["sql"], c["sql_resumen"]):
            if sql is not None:
                db.validate_sql(sql)
                self.assertNotIn("@HOY", sql)
        return c

    def tandas_con_un_resumen_por_campo(self):
        """Todos los campos del catálogo con un resumen cada uno, en tandas del máximo de columnas que admite un informe."""
        todos = [{"campo": c.id, "resumen": im.RESUMENES[c.tipo][0]} for c in cat.CATALOGO.values()]
        return [todos[i:i + im.COLUMNAS_MAX] for i in range(0, len(todos), im.COLUMNAS_MAX)]

    def test_solo_se_agrupa_por_campos_de_pocas_categorias(self):
        self.assertEqual({c.id for c in cat.CATALOGO.values() if c.agrupable},
                         {"AnimalGroupName", "AnimalGroupNumber", "CurrentLactationNumber", "CurrentReproductionStatus",
                          "InMilk", "ToBeCulled", "IsPregnant", "IsDryingOff",                           # los sí/no: dos valores
                          "Sex"})                                                                        # hembra o macho

    def test_cada_resumen_de_cada_campo(self):
        for c in cat.CATALOGO.values():
            for kind in im.RESUMENES[c.tipo]:
                r = self.validar({"columnas": [{"campo": c.id, "resumen": kind}], "detalle": False})
                self.assertIsNone(r["sql"], c.id)
                self.assertIn("COUNT(*) AS n", r["sql_resumen"], f"{c.id} {kind}")

    def test_agrupar_por_cada_campo_agrupable_con_un_resumen_en_cada_columna(self):
        for g in (c.id for c in cat.CATALOGO.values() if c.agrupable):
            for columnas in self.tandas_con_un_resumen_por_campo():
                r = self.validar({"columnas": columnas, "agrupar_por": [g]})
                self.assertIn("ROLLUP(", r["sql_resumen"], g)
                self.assertIn("GROUPING(", r["sql_resumen"], g)

    def test_dos_niveles_en_cualquier_orden(self):
        ags = [c.id for c in cat.CATALOGO.values() if c.agrupable]
        for a in ags:
            for b in ags:
                if a != b:
                    for columnas in self.tandas_con_un_resumen_por_campo():
                        self.validar({"columnas": columnas, "agrupar_por": [a, b]})
                        self.validar({"columnas": columnas, "agrupar_por": [a, b], "detalle": False})

    def test_un_campo_calculado_sobre_campos_reales_trae_solo_los_bloques_que_usa(self):
        defn = {"columnas": [{"campo": "Number"}, {"campo": "calc_1", "resumen": "avg"}],
                "calculados": [{"id": "calc_1", "etiqueta": "Kg por día de ordeño", "formula": "[SevenDayAverage] / ([CurrentDIM] + 1) * 100"}],
                "condiciones": [{"campo": "calc_1", "op": ">", "valor": 0.5}], "orden": {"campo": "calc_1", "dir": -1},
                "agrupar_por": ["AnimalGroupName"]}
        r = self.validar(defn)
        for sql in (r["sql"], r["sql_resumen"]):
            self.assertIn("AnimalDaily", sql)                 # el promedio de 7 días
            self.assertIn("AnimalReproductionInfo", sql)      # los días en ordeño
            self.assertNotIn("EventCalving", sql)             # el último parto no entra en la fórmula
        self.assertIn("NULLIF(", r["sql_resumen"])            # la división no explota con un cero

    def test_una_formula_con_un_campo_sin_verificar_marca_la_columna(self):
        r = self.validar({"columnas": [{"campo": "calc_1"}],
                          "calculados": [{"id": "calc_1", "etiqueta": "Pico doble", "formula": "[PeakYieldLastLactation] * 2"}]})
        self.assertFalse(r["columnas"][0]["verificado"])
        r = self.validar({"columnas": [{"campo": "calc_1"}],
                          "calculados": [{"id": "calc_1", "etiqueta": "Días doble", "formula": "[CurrentDIM] * 2"}]})
        self.assertTrue(r["columnas"][0]["verificado"])


if __name__ == "__main__":
    unittest.main()
