"""«Incluir animales descartados» en los informes suma SOLO a los animales del tambo que se fueron (27/11/2026).

Antes se unía «rodeo de hoy» con «alguna vez tuvo un registro diario en el rebaño», y eso sumaba animales VIVOS que hoy están en otros rebaños de la misma base (en la copia de La Ponderosa del
20/11/2026: 1.838). Ahora se usa `rebano.filtro_del_animal`, como las demás cuentas sobre el pasado: el rodeo de hoy y, a quien ya no tiene, el de su último registro diario.

Las pruebas corren el SQL real de la población (`informes_motor.poblacion_sql`) contra una base SQLite en memoria.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_descartados -v
"""
import sqlite3
import unittest

import db
import informes_motor as im
import rebano
from tests.test_dados_de_baja import a_sqlite

TAMBO, OTRO = 1, 6
GRUPOS = [(10, TAMBO), (20, OTRO)]
# BasicAnimal: (OID, Number, [Group], ExitDate, GCRecord)
ANIMALES = [
    (100, 11, 10, None, None),             # vive en el tambo, con registros
    (110, 12, 10, None, None),             # vaquillona viva en el tambo, sin ningún registro
    (200, 13, 20, None, None),             # vive HOY en otro rebaño; antes estuvo en el tambo (la que entraba mal)
    (210, 14, 20, None, None),             # vive en otro rebaño y nunca estuvo en el tambo
    (300, 15, None, "2025-09-30", None),   # se fue DESDE el tambo (antes había estado en el otro)
    (400, 16, None, "2025-09-30", None),   # se fue desde el OTRO rebaño (antes había estado en el tambo)
    (500, 17, None, "2025-09-30", None),   # se fue y no tiene ningún registro: no se sabe de qué rebaño era
    (600, 0, 10, None, None),              # la vaca comodín (número 0)
    (700, 18, 10, None, 1),                # borrada
]
# AnimalDaily: (OID, BasicAnimal, Date, AnimalGroup, GCRecord)
DIARIO = [
    (1, 100, "2025-06-10", 10, None),
    (2, 200, "2025-06-10", 10, None), (3, 200, "2025-08-10", 20, None),
    (4, 300, "2025-01-10", 20, None), (5, 300, "2025-06-10", 10, None),
    (6, 400, "2025-01-10", 10, None), (7, 400, "2025-07-10", 20, None), (8, 400, "2025-09-10", 10, 1),      # el último registro está borrado: no cuenta
    (9, 600, "2025-06-10", 10, None),
    (10, 700, "2025-06-10", 10, None),
]


class ConBase(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.executescript("""
            CREATE TABLE AnimalGroup (OID INTEGER PRIMARY KEY, Herd INTEGER);
            CREATE TABLE BasicAnimal (OID INTEGER PRIMARY KEY, Number INTEGER, [Group] INTEGER, ExitDate TEXT, GCRecord INTEGER);
            CREATE TABLE AnimalDaily (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, [Date] TEXT, AnimalGroup INTEGER, GCRecord INTEGER);
        """)
        self.con.executemany("INSERT INTO AnimalGroup VALUES (?, ?)", GRUPOS)
        self.con.executemany("INSERT INTO BasicAnimal VALUES (?, ?, ?, ?, ?)", ANIMALES)
        self.con.executemany("INSERT INTO AnimalDaily VALUES (?, ?, ?, ?, ?)", DIARIO)

    def poblacion(self, incluir, herd=(TAMBO,)) -> set:
        sql = im.poblacion_sql({"columnas": [{"campo": "Number"}], "incluir_descartados": incluir}, herd=list(herd) if isinstance(herd, tuple) else herd)
        db.validate_sql(sql)                                                  # la misma regla de «solo SELECT» que la app
        return {f[0] for f in self.con.execute(a_sqlite(sql)).fetchall()}


class TestQuienesEntran(ConBase):
    def test_con_la_opcion_son_los_del_tambo_y_los_que_se_fueron_de_el(self):
        self.assertEqual(self.poblacion(True), {100, 110, 300})              # ni la que hoy está en otro rebaño (200) ni la que se fue desde otro (400)

    def test_sin_la_opcion_son_solo_los_de_hoy(self):
        self.assertEqual(self.poblacion(False), {100, 110})

    def test_la_opcion_nunca_saca_a_nadie(self):
        self.assertTrue(self.poblacion(False) <= self.poblacion(True))

    def test_con_los_dos_rebanos_entran_todos_los_que_se_pueden_ubicar(self):
        self.assertEqual(self.poblacion(True, (TAMBO, OTRO)), {100, 110, 200, 210, 300, 400})

    def test_sin_filtro_de_rebano_entran_todos_los_numerados(self):
        self.assertEqual(self.poblacion(True, rebano.TODOS), {100, 110, 200, 210, 300, 400, 500})


class TestLaMismaPoblacionEnTodos(unittest.TestCase):
    """La lista, los resúmenes y el gráfico usan exactamente el mismo filtro, escrito como conjunto (`b.OID IN (...)`), no como una condición por fila."""

    def test_lista_resumenes_y_grafico(self):
        defn = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM", "resumen": "avg"}], "incluir_descartados": True}
        c = im.compilar(defn, herd=[TAMBO], hoy="2026-11-20")
        filtro = rebano.filtro_del_animal("b.OID", [TAMBO])
        self.assertTrue(filtro.startswith("b.OID IN ("))
        for sql in (c["sql"], c["sql_resumen"], im.poblacion_sql(defn, herd=[TAMBO], hoy="2026-11-20")):
            self.assertIn(filtro, sql)
            self.assertNotIn("OR EXISTS (SELECT 1 FROM AnimalDaily", sql)


if __name__ == "__main__":
    unittest.main()
