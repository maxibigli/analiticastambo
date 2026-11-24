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
        r = im.compilar({"columnas": [{"campo": i} for i in cat.CATALOGO]}, herd=1)
        db.validate_sql(r["sql"])
        self.assertIn("GETDATE()", r["sql"])

    def test_los_bloques_no_multiplican_filas(self):
        """Un LEFT JOIN a algo que puede devolver varias filas por animal duplicaría animales en el informe. Cada bloque
        o agrupa por animal o une por una clave única de una fila por animal (AnimalReproductionInfo, AnimalGroup...)."""
        for b in cat.BLOQUES.values():
            ok = "GROUP BY" in b.join.upper() or b.id in ("repro", "lactprev")
            self.assertTrue(ok, b.id)

    def test_lo_que_no_se_comparo_contra_delpro_queda_marcado(self):
        sin = {c.id for c in cat.CATALOGO.values() if not c.verificado}
        self.assertEqual(sin, {"CurrentReproductionStatus", "PeakYieldLastLactation"})
        for c in cat.CATALOGO.values():
            if not c.verificado:
                self.assertTrue(c.nota, f"{c.id} sin verificar y sin nota que diga por qué")

    def test_los_ids_siguen_el_nombre_de_delpro(self):
        # No es un capricho: sirve para importar después los informes que el tambo ya tiene en DelPro.
        for esperado in ("Number", "AnimalGroupName", "CurrentLactationNumber", "CurrentDIM", "SevenDayAverage",
                         "LastCalvingDate", "CurrentReproductionStatus", "DaysToPeakLastLactation", "PeakYieldLastLactation"):
            self.assertIn(esperado, cat.CATALOGO)


if __name__ == "__main__":
    unittest.main()
