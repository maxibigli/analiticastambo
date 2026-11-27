"""Tasa de preñez de NOVILLAS: es novilla la que en ese ciclo todavía no había empezado su primera lactancia, aunque haya parido después (27/11/2026).

Antes la consulta pedía lactancia 0 HOY: toda novilla que después parió desaparecía de los ciclos viejos, y son justamente las que quedaron preñadas. La primera lactancia sale del historial de DelPro
(`HistoryAnimalLactationInfo`, unido por `HistoryAnimal.ReferenceId`). Un parto cargado antes del final del ciclo la descarta, y a la que hoy figura sin partos (lactancia 0) se le cree a DelPro.

Se corre el SQL REAL de `tasa_prenez.sql_embudo` para novillas contra una base SQLite en memoria: la única diferencia es que los ciclos (`VALUES`, que SQLite no admite así) van en una tabla.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_tasa_prenez_novillas -v
"""
import re
import sqlite3
import unittest

import db
import tasa_prenez

VENTANAS = [{"n": 1, "desde": "2025-06-10", "hasta": "2025-06-30"}, {"n": 2, "desde": "2025-07-01", "hasta": "2025-07-21"}]
EDAD, PEV = 447, 53
TAMBO, OTRO = 1, 6
GRUPOS = [(10, TAMBO), (20, OTRO)]
# BasicAnimal: (OID, [Group], BirthDate, ExitDate). Lactancia de HOY aparte (None = sin fila de reproducción, como los machos).
ANIMALES = [
    (101, 10, "2023-01-01", None),          # novilla hoy
    (102, 10, "2023-01-01", None),          # se preñó en el ciclo 1 y parió en 2026
    (103, 10, "2022-06-01", None),          # parió antes de los ciclos
    (104, 10, "2022-06-01", None),          # parió durante el ciclo 1
    (105, 10, "2021-01-01", None),          # comprada: de su primera lactancia no hay nada
    (106, 10, "2021-01-01", None),          # su primera lactancia figura sin fecha
    (107, 10, "2023-01-01", None),          # hoy sin partos, aunque el historial tiene una lactancia 1 vieja (pasa en la base real)
    (108, 10, "2023-01-01", None),          # dato contradictorio: parto cargado antes de los ciclos, lactancia 1 «después»
    (109, 10, "2024-05-01", None),          # cumple 447 días justo al final del ciclo 2; parió en 2026
    (110, 10, "2023-01-01", None),          # macho
    (111, None, "2023-01-01", "2025-06-25"),  # se fue en el ciclo 1
    (112, 20, "2023-01-01", None),          # de otro rebaño, parió después
    (113, 10, "2023-01-01", None),          # parió después; abortó la preñez del ciclo 2
]
LACTANCIA_HOY = {101: 0, 102: 1, 103: 1, 104: 1, 105: 2, 106: 2, 107: 0, 108: 1, 109: 1, 111: 0, 112: 1, 113: 1}
# historial: (animal, lactancia, inicio). El OID del historial es 1000 + animal, nunca el del animal.
LACTANCIAS = [
    (101, 0, "2023-01-01"),
    (102, 1, "2026-03-20 06:00:00"),
    (103, 1, "2025-01-10 06:00:00"),
    (104, 1, "2025-06-20 06:00:00"),
    (105, 2, "2025-12-05 06:00:00"),
    (106, 1, None), (106, 2, "2025-12-05 06:00:00"),
    (107, 1, "2025-03-01 00:00:00"),
    (108, 1, "2025-12-01 06:00:00"),
    (109, 1, "2026-04-01 06:00:00"),
    (112, 1, "2026-01-01 06:00:00"),
    (113, 1, "2026-09-01 06:00:00"),
]
# eventos: (tabla, animal, fecha)
EVENTOS = [
    ("EventCalving", 102, "2026-03-20 06:00:00"), ("EventCalving", 103, "2025-01-10 06:00:00"), ("EventCalving", 104, "2025-06-20 06:00:00"),
    ("EventCalving", 105, "2025-12-05 06:00:00"), ("EventCalving", 106, "2025-12-05 06:00:00"), ("EventCalving", 108, "2025-05-01 06:00:00"),
    ("EventCalving", 112, "2026-01-01 06:00:00"), ("EventCalving", 113, "2026-09-01 06:00:00"),
    ("EventHeat", 102, "2025-06-14 08:00:00"), ("EventHeat", 101, "2025-07-05 08:00:00"),
    ("EventAbortion", 113, "2025-10-01 07:00:00"),
]
# servicios con su chequeo positivo: (animal, servicio, chequeo)
PRENECES = [(102, "2025-06-15 08:00:00", "2025-07-25 08:00:00"), (113, "2025-07-10 08:00:00", "2025-08-15 08:00:00")]


def base_de_prueba() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE ventanas (n INTEGER, desde TEXT, hasta TEXT);
        CREATE TABLE AnimalGroup (OID INTEGER PRIMARY KEY, Herd INTEGER);
        CREATE TABLE BasicAnimal (OID INTEGER PRIMARY KEY, Number INTEGER, [Group] INTEGER, BirthDate TEXT, ExitDate TEXT, GCRecord INTEGER);
        CREATE TABLE AnimalDaily (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, [Date] TEXT, AnimalGroup INTEGER, GCRecord INTEGER);
        CREATE TABLE AnimalReproductionInfo (Animal INTEGER, LactationNumber INTEGER, GCRecord INTEGER);
        CREATE TABLE HistoryAnimal (OID INTEGER PRIMARY KEY, ReferenceId INTEGER);
        CREATE TABLE HistoryAnimalLactationInfo (OID INTEGER PRIMARY KEY, Animal INTEGER, LactationNumber INTEGER, StartDate TEXT);
        CREATE TABLE AbstractAnimalEvent (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, DateAndTime TEXT, GCRecord INTEGER);
        CREATE TABLE EventCalving (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventHeat (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventInsemination (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventPregCheck (OID INTEGER PRIMARY KEY, Result INTEGER, EffectiveInsemination INTEGER);
        CREATE TABLE EventAbortion (OID INTEGER PRIMARY KEY);
    """)
    con.executemany("INSERT INTO ventanas VALUES (?, ?, ?)", [(v["n"], v["desde"], v["hasta"]) for v in VENTANAS])
    con.executemany("INSERT INTO AnimalGroup VALUES (?, ?)", GRUPOS)
    con.executemany("INSERT INTO BasicAnimal VALUES (?, ?, ?, ?, ?, NULL)", [(o, o, g, nac, salida) for o, g, nac, salida in ANIMALES])
    con.execute("INSERT INTO AnimalDaily VALUES (1, 111, '2025-06-24', 10, NULL)")
    con.executemany("INSERT INTO AnimalReproductionInfo VALUES (?, ?, NULL)", sorted(LACTANCIA_HOY.items()))
    con.executemany("INSERT INTO HistoryAnimal VALUES (?, ?)", [(1000 + o, o) for o, *_ in ANIMALES])
    # trampa: un historial cuyo OID coincide con el del animal 113 pero es de OTRO animal, con una lactancia 1 vieja
    con.execute("INSERT INTO HistoryAnimal VALUES (113, 999)")
    con.executemany("INSERT INTO HistoryAnimalLactationInfo (Animal, LactationNumber, StartDate) VALUES (?, ?, ?)",
                    [(1000 + a, n, f) for a, n, f in LACTANCIAS] + [(113, 1, "2025-01-01 00:00:00")])
    eventos = iter(range(1, 10_000))
    for tabla, animal, fecha in EVENTOS:
        oid = next(eventos)
        con.execute("INSERT INTO AbstractAnimalEvent VALUES (?, ?, ?, NULL)", (oid, animal, fecha))
        con.execute(f"INSERT INTO {tabla} VALUES (?)", (oid,))
    for animal, servicio, chequeo in PRENECES:
        s, c = next(eventos), next(eventos)
        con.execute("INSERT INTO AbstractAnimalEvent VALUES (?, ?, ?, NULL)", (s, animal, servicio))
        con.execute("INSERT INTO EventInsemination VALUES (?)", (s,))
        con.execute("INSERT INTO AbstractAnimalEvent VALUES (?, ?, ?, NULL)", (c, animal, chequeo))
        con.execute("INSERT INTO EventPregCheck VALUES (?, 1, ?)", (c, s))
    return con


def a_sqlite(sql: str) -> str:
    """Lo mínimo para correr la consulta de novillas en SQLite (los ciclos salen de la tabla `ventanas`)."""
    sql = re.sub(r"WITH ventanas\(n, desde, hasta\) AS \(.*?\), partos AS", "WITH partos AS", sql, flags=re.S)
    sql = re.sub(r"OPTION\s*\([^)]*\)\s*$", "", sql.strip())
    sql = re.sub(r"\(SELECT TOP 1 (.+?) (ORDER BY [^)]+)\)", r"(SELECT \1 \2 LIMIT 1)", sql, flags=re.S)
    sql = re.sub(r"DATEADD\(day,\s*(\d+),\s*([\w.]+)\)", r"datetime(\2, '+\1 days')", sql)
    return re.sub(r"DATEDIFF\(day,\s*([\w.]+),\s*(datetime\([^)]*\))\)", r"CAST(julianday(date(\2)) - julianday(date(\1)) AS INTEGER)", sql)


class TestLasNovillasDeCadaCiclo(unittest.TestCase):
    def setUp(self):
        self.con = base_de_prueba()
        self.addCleanup(self.con.close)

    def sql(self, herd=TAMBO) -> str:
        sql = tasa_prenez.sql_embudo(VENTANAS, tasa_prenez.TIPO_NOVILLA, PEV, EDAD, [herd])
        db.validate_sql(sql)
        return sql

    def aptas(self, herd=TAMBO) -> dict:
        sql = a_sqlite(self.sql(herd))
        principal = re.search(r"SELECT v\.n,\s*\(SELECT COUNT\(\*\) FROM aptas", sql).start()
        out = {v["n"]: set() for v in VENTANAS}
        for n, animal in self.con.execute(sql[:principal] + "SELECT n, animal FROM aptas").fetchall():
            out[n].add(animal)
        return out

    def test_las_que_despues_parieron_siguen_siendo_novillas_en_los_ciclos_de_antes(self):
        """La 102 y la 113 parieron en 2026 y la 109 también (en el ciclo 1 todavía no tenía la edad). Antes del arreglo no figuraba ninguna de las tres."""
        a = self.aptas()
        self.assertEqual(a[1], {101, 102, 107, 111, 113})
        self.assertEqual(a[2], {101, 107, 109, 113})                  # la 102 ya estaba preñada y la 111 se había ido

    def test_no_son_novillas_las_que_ya_habian_parido_ni_las_de_primera_lactancia_desconocida(self):
        a = self.aptas()
        for n in a:
            for animal in (103, 104, 105, 106, 108, 110):
                self.assertNotIn(animal, a[n], (n, animal))

    def test_cada_rebano_ve_las_suyas(self):
        self.assertEqual(self.aptas(OTRO), {1: {112}, 2: {112}})

    def test_el_embudo_cuenta_celos_servicios_prenadas_y_abortos_de_las_que_despues_parieron(self):
        cur = self.con.execute(a_sqlite(self.sql()))
        columnas = [d[0] for d in cur.description]
        filas = {f[0]: dict(zip(columnas, f)) for f in cur.fetchall()}
        self.assertEqual([filas[1][k] for k in ("aptas", "con_celo", "inseminadas", "prenadas", "abortos")], [5, 1, 1, 1, 0])
        self.assertEqual([filas[2][k] for k in ("aptas", "con_celo", "inseminadas", "prenadas", "abortos")], [4, 1, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
