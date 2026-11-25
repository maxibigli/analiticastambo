"""La lactancia de un animal en el historial de DelPro se une por `HistoryAnimal.ReferenceId` (25/11/2026).

`HistoryAnimalLactationInfo.Animal` apunta a `HistoryAnimal.OID`, que NO es el OID del animal: el animal es `HistoryAnimal.ReferenceId`. Los dos números son distintos en 2.344 de los
11.548 históricos de La Ponderosa y en 2.911 de 2.925 de San José (copias del 20/11/2026; `ReferenceId` no se repite en ninguna). Dos pantallas unían `h.Animal` con `BasicAnimal.OID` y decidían
si la lactancia era del rebaño mirando a OTRO animal:

  * el Tablero de Diagnóstico, «Días abiertos promedio» (`app._tablero_dias_abiertos`);
  * el Análisis reproductivo, «% de preñez por días en leche» (`reproduccion.sql_prenez_por_del`).

Estas pruebas corren el SQL REAL de las dos consultas (sin reescribirlo) contra una base SQLite en memoria con el caso que rompía: el historial de una vaca del rebaño lleva el OID de una vaca de OTRO
rebaño, y al revés. Hay una capa mínima para pasar de T-SQL a SQLite (`a_sqlite`: sacar el `OPTION (...)` del final y el `DATEADD(day, n, fecha)`); nada más. Que la unión vieja daba otra cosa
también está probado, para que la base de la prueba no pueda dejar de distinguir las dos uniones sin que se note.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_historia_lactancia -v
"""
import re
import sqlite3
import threading
import unittest
from unittest import mock

import app
import db
import rebano
import reproduccion

TAMBO = 1          # el rebaño del tambo
OTRO = 6           # el de otro tambo que comparte la base (Don Germán en La Ponderosa)

# Cuatro vacas. Las vacas A y B tienen el historial «cruzado»: el OID del historial de A es el OID de la vaca B, y el de B el de la vaca A (en la base real el OID del historial no tiene
# nada que ver con el del animal: por eso se ve «cruzado»). C y D son las que coinciden.
#   animal (BasicAnimal.OID, grupo)   historial (HistoryAnimal.OID -> ReferenceId)
#   A = 100, rebaño del tambo         200 -> 100
#   B = 200, OTRO rebaño              100 -> 200
#   C = 300, rebaño del tambo         300 -> 300
#   D = 400, OTRO rebaño              400 -> 400
GRUPOS = [(10, TAMBO), (20, OTRO)]
ANIMALES = [(100, 10), (200, 20), (300, 10), (400, 20)]
HISTORIALES = [(200, 100), (100, 200), (300, 300), (400, 400)]
# (OID, Animal = OID DEL HISTORIAL, lactancia, inicio, días abiertos)
LACTANCIAS = [
    (1, 200, 1, "2025-03-01", 90),      # de A (rebaño del tambo): preñada a los 90 días
    (2, 100, 1, "2025-04-01", 300),     # de B (OTRO rebaño)
    (3, 300, 2, "2025-05-01", 120),     # de C (tambo)
    (4, 400, 2, "2025-06-01", 200),     # de D (OTRO rebaño)
    (5, 200, 2, "2026-02-01", 0),       # de A, otro año: 0 = todavía no concibió
    (6, 300, 1, "2025-07-01", None),    # de C: sin días abiertos cargados (cuenta de denominador, no de preñez)
    (7, 300, 3, "2025-08-01", 450),     # de C: arriba de 400 son cargas viejas o erróneas
]


def a_sqlite(sql: str) -> str:
    """Lo mínimo para correr el T-SQL de estas dos consultas en SQLite: sin `OPTION (...)` y con `date()` en vez de `DATEADD`."""
    sql = re.sub(r"OPTION\s*\([^)]*\)\s*$", "", sql.strip())
    return re.sub(r"DATEADD\(day,\s*(-?\d+),\s*('[^']*')\)", lambda m: f"date({m.group(2)}, '{int(m.group(1)):+d} day')", sql)


def base_de_prueba() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE AnimalGroup (OID INTEGER PRIMARY KEY, Herd INTEGER);
        CREATE TABLE BasicAnimal (OID INTEGER PRIMARY KEY, [Group] INTEGER);
        CREATE TABLE HistoryAnimal (OID INTEGER PRIMARY KEY, ReferenceId INTEGER);
        CREATE TABLE HistoryAnimalLactationInfo (OID INTEGER PRIMARY KEY, Animal INTEGER, LactationNumber INTEGER, StartDate TEXT, OpenDays INTEGER);
    """)
    con.executemany("INSERT INTO AnimalGroup VALUES (?, ?)", GRUPOS)
    con.executemany("INSERT INTO BasicAnimal VALUES (?, ?)", ANIMALES)
    con.executemany("INSERT INTO HistoryAnimal VALUES (?, ?)", HISTORIALES)
    con.executemany("INSERT INTO HistoryAnimalLactationInfo VALUES (?, ?, ?, ?, ?)", LACTANCIAS)
    return con


class ConBase(unittest.TestCase):
    def setUp(self):
        self.con = base_de_prueba()
        self.addCleanup(self.con.close)
        self.consultas = []
        original, hilo = db.run_query, threading.get_ident()

        def run_query(sql, *args, **kwargs):
            if threading.get_ident() != hilo:           # `import app` arranca un hilo de calentamiento que también llama a db.run_query: lo suyo sigue como estaba
                return original(sql, *args, **kwargs)
            if kwargs.get("validate", True):
                sql = db.validate_sql(sql)             # la misma regla de «solo SELECT» que la app
            self.consultas.append(sql)
            cur = self.con.execute(a_sqlite(sql))
            return {"columns": [d[0] for d in cur.description], "rows": [list(f) for f in cur.fetchall()], "truncated": False}

        p = mock.patch.object(app.db, "run_query", run_query)
        p.start()
        self.addCleanup(p.stop)

    def sql_enviado(self):
        """La consulta que armó la pantalla (la que lee el historial de lactancias)."""
        return " ".join(next(s for s in self.consultas if "HistoryAnimalLactationInfo" in s).split())

    def filas(self, sql):
        cur = self.con.execute(a_sqlite(sql))
        columnas = [d[0] for d in cur.description]
        return {f[0]: dict(zip(columnas, f)) for f in cur.fetchall()}


class TestDiasAbiertosDelTablero(ConBase):
    """Con el historial cruzado, el promedio sale de las lactancias de las vacas del rebaño, no de las de otras."""

    def test_promedia_los_dias_abiertos_de_las_lactancias_de_las_vacas_del_rebano(self):
        # A (90) y C (120): 105. Con la unión vieja salía la lactancia de B (300), porque el OID de su historial es el de la vaca A (del tambo): 210.
        self.assertEqual(app._tablero_dias_abiertos("x", [TAMBO]), {"dias_abiertos": 105.0, "detalle": "2 lactancias"})

    def test_el_rebano_se_filtra_sobre_el_animal_real_tambien_del_otro_lado(self):
        # B (300) y D (200): 250. Con la unión vieja salía la de A (90) en lugar de la de B.
        self.assertEqual(app._tablero_dias_abiertos("x", [OTRO]), {"dias_abiertos": 250.0, "detalle": "2 lactancias"})

    def test_sin_lactancias_con_dias_abiertos_avisa_que_faltan(self):
        self.assertIn("falta", app._tablero_dias_abiertos("x", [99]))

    def test_el_sql_pasa_por_el_historial_y_solo_usa_h_animal_para_eso(self):
        app._tablero_dias_abiertos("x", [TAMBO])
        sql = self.sql_enviado()
        self.assertIn("JOIN HistoryAnimal ha ON ha.OID = h.Animal", sql)
        self.assertIn("JOIN BasicAnimal b ON b.OID = ha.ReferenceId", sql)
        self.assertEqual(sql.count("h.Animal"), 1, "h.Animal solo sirve para llegar al historial (ha.OID), no para unir con BasicAnimal")
        self.assertNotIn("b.OID = h.Animal", sql)

    def test_la_union_vieja_daba_otra_cosa(self):
        """La base de la prueba tiene que distinguir las dos uniones: con la vieja salían 210 días (la lactancia de B y la de C) en vez de 105."""
        vieja = f"""
            SELECT CAST(AVG(CAST(h.OpenDays AS float)) AS decimal(6,1)) AS dias, COUNT(*) AS lactancias
            FROM HistoryAnimalLactationInfo h
            JOIN BasicAnimal b ON b.OID = h.Animal
            WHERE h.OpenDays > 0 AND h.OpenDays < 400
              AND {rebano.filtro('b', [TAMBO])}
        """
        self.assertEqual(self.con.execute(a_sqlite(vieja)).fetchone(), (210.0, 2))


class TestPrenezPorDel(ConBase):
    """`sql_prenez_por_del` cuenta, de las lactancias iniciadas en el rango, las del rebaño; el rebaño es el del animal real."""

    def correr(self, herd, desde="2025-01-01", hasta="2025-12-31"):
        return self.filas(reproduccion.sql_prenez_por_del(desde, hasta, herd))

    def test_cuenta_las_lactancias_de_las_vacas_del_rebano(self):
        r = self.correr([TAMBO])
        # 1ra lactancia: la de A (90 días) y la de C (sin días abiertos: solo cuenta de denominador).
        self.assertEqual(r["l1"]["n"], 2)
        for tramo in ("p100", "p130", "p150", "p200", "p300"):
            self.assertEqual(r["l1"][tramo], 50.0, tramo)
        # 2da y más: la 2da de C (120 días) y su 3ra (450 días: cuenta como lactancia, pero no como preñez a ningún tramo).
        self.assertEqual(r["l2"]["n"], 2)
        self.assertEqual((r["l2"]["p100"], r["l2"]["p130"], r["l2"]["p300"]), (0.0, 50.0, 50.0))

    def test_el_rebano_se_filtra_sobre_el_animal_real_tambien_del_otro_lado(self):
        r = self.correr([OTRO])
        # B (300 días, 1ra lactancia) y D (200 días, 2da). Con la unión vieja salía la lactancia de A.
        self.assertEqual((r["l1"]["n"], r["l1"]["p200"], r["l1"]["p300"]), (1, 0.0, 100.0))
        self.assertEqual((r["l2"]["n"], r["l2"]["p150"], r["l2"]["p200"]), (1, 0.0, 100.0))

    def test_respeta_el_rango_de_fechas(self):
        self.assertEqual(self.correr([TAMBO], "2025-01-01", "2025-04-30")["l1"]["n"], 1)          # solo la de A (marzo)
        self.assertEqual(self.correr([TAMBO], "2026-01-01", "2026-12-31")["l2"]["n"], 1)          # la de febrero 2026, sin preñez cargada
        self.assertEqual(self.correr([TAMBO], "2026-01-01", "2026-12-31")["l2"]["p300"], 0.0)

    def test_el_sql_pasa_por_el_historial_y_el_rebano_se_mira_en_el_animal_real(self):
        sql = " ".join(reproduccion.sql_prenez_por_del("2025-01-01", "2025-12-31", [TAMBO]).split())
        self.assertIn("JOIN HistoryAnimal ha ON ha.OID = h.Animal", sql)
        self.assertIn("b_tb.OID = ha.ReferenceId", sql)                                 # el filtro de rebaño mira al animal de verdad
        self.assertEqual(sql.count("h.Animal"), 1, "h.Animal solo sirve para llegar al historial (ha.OID), no para filtrar el rebaño")

    def test_la_union_vieja_daba_otra_cosa(self):
        """Con el filtro de rebaño sobre `h.Animal` (el OID del historial), la 1ra lactancia del tambo era la de B (300 días): ni una preñez a los 200 días."""
        vieja = f"""
            SELECT CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END AS grupo, COUNT(*) AS n,
                   100.0 * SUM(CASE WHEN h.OpenDays > 0 AND h.OpenDays <= 100 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS p100
            FROM HistoryAnimalLactationInfo h
            WHERE h.LactationNumber >= 1 AND h.StartDate >= '2025-01-01' AND h.StartDate < date('2025-12-31', '+1 day')
              AND {rebano.filtro_por_animal('h.Animal', [TAMBO])}
            GROUP BY CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END
        """
        r = {f[0]: f for f in self.con.execute(a_sqlite(vieja)).fetchall()}
        self.assertEqual(r["l1"][2], 0.0)                       # con el arreglo es 50,0 (`test_cuenta_las_lactancias_de_las_vacas_del_rebano`)


class TestNingunModuloVuelveAUnirPorElOidDelAnimal(unittest.TestCase):
    """Los módulos de la carpeta principal que leen `HistoryAnimalLactationInfo` (menos los `informes_*`, que tienen su propia prueba) tienen que pasar por `HistoryAnimal.ReferenceId`."""

    def test_los_modulos_que_la_leen_pasan_por_el_historial(self):
        import glob
        import os
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for ruta in sorted(glob.glob(os.path.join(raiz, "*.py"))):
            nombre = os.path.basename(ruta)
            if nombre.startswith("informes_"):
                continue
            with open(ruta, encoding="utf-8") as f:
                texto = f.read()
            if "HistoryAnimalLactationInfo" in texto:
                # assertTrue y no assertIn: si falla, assertIn volcaría el archivo entero en el mensaje.
                self.assertTrue("JOIN HistoryAnimal ha ON ha.OID = h.Animal" in texto,
                                f"{nombre} lee HistoryAnimalLactationInfo: tiene que unirla con HistoryAnimal (ver la nota de CLAUDE.md, «Trampas del esquema DDM»)")


if __name__ == "__main__":
    unittest.main()
