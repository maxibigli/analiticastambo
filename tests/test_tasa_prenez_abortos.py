"""Tasa de preñez: los abortos son de las preñeces logradas en el ciclo por las vacas APTAS, igual que las preñadas (26/11/2026).

Antes la cuenta de abortos no miraba las aptas ni el rebaño: contaba abortos de cualquier animal de la base, incluidos los de otros tambos. En el ciclo del 10/06/2025 daba 77 contra los 33
del informe de DelPro; contando solo las aptas, 31.

La consulta completa usa `CROSS APPLY` y `VALUES`, que SQLite no tiene: acá se sacan del SQL REAL las subconsultas de preñadas y de abortos y se corren contra una base SQLite en memoria en la
que `aptas` y `prenez` (que en la consulta son CTE) son tablas puestas a mano.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_tasa_prenez_abortos -v
"""
import re
import sqlite3
import unittest

import db
import tasa_prenez

# Dos ciclos de 21 días
VENTANAS = [{"n": 1, "desde": "2025-06-10", "hasta": "2025-06-30"}, {"n": 2, "desde": "2025-07-01", "hasta": "2025-07-21"}]
# aptas: (ciclo, animal)
APTAS = [(1, 100), (1, 400), (1, 500), (1, 600), (2, 300), (2, 700)]
# preñeces: (animal, concepción)
PRENECES = [
    (100, "2025-06-15 08:00:00"),       # apta en el 1: aborta -> cuenta en el 1
    (200, "2025-06-16 08:00:00"),       # NO apta (otro tambo, por ejemplo): aborta -> no cuenta
    (300, "2025-06-17 08:00:00"),       # apta solo en el 2, concebida en el 1: no cuenta en ninguno
    (400, "2025-06-18 08:00:00"),       # apta: el aborto cae 400 días después, es de otra preñez -> no cuenta
    (600, "2025-06-30 23:00:00"),       # apta, concebida el último día a la noche, dos abortos -> cuenta una vez
    (700, "2025-07-05 08:00:00"),       # apta en el 2 -> cuenta en el 2
]
# abortos: (animal, fecha). El 500 es apta pero no concibió en el ciclo.
ABORTOS = [
    (100, "2025-09-01 07:00:00"), (200, "2025-09-02 07:00:00"), (300, "2025-09-03 07:00:00"), (400, "2026-07-23 07:00:00"),
    (500, "2025-09-04 07:00:00"), (600, "2025-08-01 07:00:00"), (600, "2025-10-01 07:00:00"), (700, "2025-10-02 07:00:00"),
]


def base_de_prueba() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE ventanas (n INTEGER, desde TEXT, hasta TEXT);
        CREATE TABLE aptas (n INTEGER, animal INTEGER);
        CREATE TABLE prenez (animal INTEGER, concep TEXT, parto TEXT);
        CREATE TABLE AbstractAnimalEvent (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, DateAndTime TEXT, GCRecord INTEGER);
        CREATE TABLE EventAbortion (OID INTEGER PRIMARY KEY);
    """)
    con.executemany("INSERT INTO ventanas VALUES (?, ?, ?)", [(v["n"], v["desde"], v["hasta"]) for v in VENTANAS])
    con.executemany("INSERT INTO aptas VALUES (?, ?)", APTAS)
    con.executemany("INSERT INTO prenez VALUES (?, ?, NULL)", PRENECES)
    for oid, (animal, fecha) in enumerate(ABORTOS, 1):
        con.execute("INSERT INTO AbstractAnimalEvent VALUES (?, ?, ?, NULL)", (oid, animal, fecha))
        con.execute("INSERT INTO EventAbortion VALUES (?)", (oid,))
    return con


def a_sqlite(sql: str) -> str:
    """Lo mínimo para correr estas subconsultas en SQLite: `DATEADD(day, k, x)` pasa a `datetime(x, '+k day')`."""
    return re.sub(r"DATEADD\(day,\s*(\d+),\s*([\w.]+)\)", r"datetime(\2, '+\1 day')", sql)


def subconsulta(sql: str, desde: str, nombre: str) -> str:
    """El texto de la subconsulta de la consulta real que arranca en `desde` y termina en `) AS nombre`."""
    i = sql.index(desde)
    j = sql.index(f") AS {nombre}", i)
    return sql[i:j + 1]


class TestLosAbortosSonDeLasAptas(unittest.TestCase):
    def setUp(self):
        self.con = base_de_prueba()
        self.addCleanup(self.con.close)

    def contar(self, tipo: str, desde: str, nombre: str) -> dict:
        sql = tasa_prenez.sql_embudo(VENTANAS, tipo, 53, 447, [1])
        db.validate_sql(sql)
        consulta = f"SELECT v.n, {subconsulta(sql, desde, nombre)} AS {nombre} FROM ventanas v ORDER BY v.n"
        return dict(self.con.execute(a_sqlite(consulta)).fetchall())

    def abortos(self, tipo=tasa_prenez.TIPO_VACA) -> dict:
        return self.contar(tipo, "(SELECT COUNT(DISTINCT ae.BasicAnimal) FROM EventAbortion", "abortos")

    def test_solo_cuentan_los_abortos_de_las_aptas_que_concibieron_en_el_ciclo(self):
        # Ciclo 1: la 100 y la 600 (una sola vez). Ni la 200 (no apta), ni la 300 (apta en otro ciclo), ni la 400 (fuera de la gestación), ni la 500 (no concibió).
        self.assertEqual(self.abortos(), {1: 2, 2: 1})

    def test_en_los_tres_tipos_de_animal(self):
        for tipo in tasa_prenez.TIPOS:
            self.assertEqual(self.abortos(tipo), {1: 2, 2: 1}, tipo)

    def test_nunca_hay_mas_abortos_que_preneces(self):
        prenadas = self.contar(tasa_prenez.TIPO_VACA, "(SELECT COUNT(DISTINCT pz.animal) FROM prenez", "prenadas")
        self.assertEqual(prenadas, {1: 3, 2: 1})                       # 100, 400 y 600 en el 1; la 700 en el 2
        for n, abortos in self.abortos().items():
            self.assertLessEqual(abortos, prenadas[n], n)


if __name__ == "__main__":
    unittest.main()
