"""La lactancia de un animal en el historial de DelPro: se une por `HistoryAnimal.ReferenceId` y el rebaño cuenta también a los animales que ya se fueron (25/11/2026).

1) `HistoryAnimalLactationInfo.Animal` apunta a `HistoryAnimal.OID`, que NO es el OID del animal: el animal es `HistoryAnimal.ReferenceId`. Los dos números son distintos en 2.344 de los
11.548 históricos de La Ponderosa y en 2.911 de 2.925 de San José (copias del 20/11/2026; `ReferenceId` no se repite en ninguna). Dos pantallas unían `h.Animal` con `BasicAnimal.OID` y decidían
si la lactancia era del rebaño mirando a OTRO animal: el Tablero de Diagnóstico, «Días abiertos promedio» (`app._tablero_dias_abiertos`), y el Análisis reproductivo, «% de preñez por días en
leche» (`reproduccion.sql_prenez_por_del`).

2) Un animal que se da de baja pierde su `[Group]`, y `rebano.filtro_por_animal` (el grupo de hoy) lo dejaba afuera: las dos cuentas quedaban con las vacas que SIGUEN en el tambo y sin las que se
fueron, que en buena parte son las que no quedaron preñadas. `rebano.filtro_del_animal` agrega al que ya no tiene grupo el rebaño de su ÚLTIMO registro diario (`AnimalDaily`).

3) El Tablero promediaba también las filas con `LactationNumber = 0` (las novillas antes de parir: 353 a 399 «días abiertos» que no son los de una vaca).

Estas pruebas corren el SQL REAL de las consultas (sin reescribirlo) contra una base SQLite en memoria. Hay una capa mínima para pasar de T-SQL a SQLite (`a_sqlite`: sacar el `OPTION (...)`
del final, `date()` en vez de `DATEADD` y `LIMIT 1` en vez de `SELECT TOP 1`); nada más. Que lo viejo daba otra cosa también está probado, para que la base de la prueba no pueda dejar de
distinguirlo sin que se note.

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

# Animales (BasicAnimal.OID, grupo de hoy) y su historial (HistoryAnimal.OID -> ReferenceId). El OID del historial no tiene nada que ver con el del animal; en A y B se ve «cruzado»:
# el historial de A tiene el OID de la vaca B y el de B el de la vaca A.
#   A = 100  vive en el tambo                  200 -> 100
#   B = 200  vive en OTRO rebaño               100 -> 200
#   C = 300  vive en el tambo                  300 -> 300
#   D = 400  vive en OTRO rebaño               400 -> 400
#   E = 500  se fue (sin grupo); registro diario en el tambo                        5000 -> 500
#   F = 600  se fue; registro diario en OTRO rebaño                                 6000 -> 600
#   G = 700  se fue y NO tiene registro diario (uno borrado y otro sin grupo no valen)  7000 -> 700
#   H = 800  vive en OTRO rebaño pero tiene un registro viejo en el tambo (se mudó)  8000 -> 800
#   I = 900  se fue; primero estuvo en el tambo y el ÚLTIMO registro es de OTRO rebaño   9000 -> 900
#   J = 1000 se fue; primero estuvo en OTRO rebaño y el ÚLTIMO registro es del tambo     10000 -> 1000
#   (1100: el animal ya no está en BasicAnimal, solo su historial)                  11000 -> 1100
GRUPOS = [(10, TAMBO), (20, OTRO)]
ANIMALES = [(100, 10), (200, 20), (300, 10), (400, 20), (500, None), (600, None), (700, None), (800, 20), (900, None), (1000, None)]
HISTORIALES = [(200, 100), (100, 200), (300, 300), (400, 400), (5000, 500), (6000, 600), (7000, 700), (8000, 800), (9000, 900), (10000, 1000), (11000, 1100)]
# (OID, animal, fecha, grupo, GCRecord)
REGISTRO_DIARIO = [
    (1, 500, "2025-01-10", 10, None), (2, 500, "2025-06-10", 10, None),
    (3, 600, "2025-03-01", 20, None),
    (4, 800, "2024-05-01", 10, None), (5, 800, "2025-05-01", 20, None),
    (6, 900, "2024-01-01", 10, None), (7, 900, "2025-06-01", 20, None),
    (8, 1000, "2024-01-01", 20, None), (9, 1000, "2025-06-01", 10, None),
    (10, 700, "2025-05-01", 10, 1),            # dado de baja lógica: no vale
    (11, 700, "2025-06-01", None, None),       # sin grupo: no dice nada
]
# (OID, Animal = OID DEL HISTORIAL, lactancia, inicio, días abiertos)
LACTANCIAS = [
    (1, 200, 1, "2025-03-01", 90),        # de A: preñada a los 90 días
    (2, 100, 1, "2025-04-01", 300),       # de B
    (3, 300, 2, "2025-05-01", 120),       # de C
    (4, 400, 2, "2025-06-01", 200),       # de D
    (5, 200, 2, "2026-02-01", 0),         # de A, otro año: 0 = todavía no concibió
    (6, 300, 1, "2025-07-01", None),      # de C: sin días abiertos cargados (cuenta de denominador, no de preñez)
    (7, 300, 3, "2025-08-01", 450),       # de C: arriba de 400 son cargas viejas o erróneas
    (8, 5000, 2, "2025-09-01", 60),       # de E (se fue; era del tambo)
    (9, 6000, 1, "2025-09-10", 250),      # de F (se fue; era de OTRO rebaño)
    (10, 7000, 1, "2025-09-15", 30),      # de G (se fue; no se sabe de qué rebaño era)
    (11, 8000, 2, "2025-10-01", 70),      # de H (vive en OTRO rebaño)
    (12, 9000, 1, "2025-10-10", 80),      # de I (se fue; el último registro es de OTRO rebaño)
    (13, 10000, 2, "2025-10-20", 110),    # de J (se fue; el último registro es del tambo)
    (14, 200, 0, "2025-02-01", 380),      # una «lactancia 0» de A (la novilla antes de parir): no es una vaca
    (15, 11000, 1, "2025-11-01", 50),     # del animal borrado
]


def a_sqlite(sql: str) -> str:
    """Lo mínimo para correr el T-SQL de estas consultas en SQLite: sin `OPTION (...)`, `date()` en vez de `DATEADD` y `LIMIT 1` en vez de `SELECT TOP 1 ... ORDER BY ...`."""
    sql = re.sub(r"OPTION\s*\([^)]*\)\s*$", "", sql.strip())
    sql = re.sub(r"\(SELECT TOP 1 (.+?) (ORDER BY [^)]+)\)", r"(SELECT \1 \2 LIMIT 1)", sql, flags=re.S)
    return re.sub(r"DATEADD\(day,\s*(-?\d+),\s*('[^']*')\)", lambda m: f"date({m.group(2)}, '{int(m.group(1)):+d} day')", sql)


def base_de_prueba() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE AnimalGroup (OID INTEGER PRIMARY KEY, Herd INTEGER);
        CREATE TABLE BasicAnimal (OID INTEGER PRIMARY KEY, [Group] INTEGER);
        CREATE TABLE AnimalDaily (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, [Date] TEXT, AnimalGroup INTEGER, GCRecord INTEGER);
        CREATE TABLE HistoryAnimal (OID INTEGER PRIMARY KEY, ReferenceId INTEGER);
        CREATE TABLE HistoryAnimalLactationInfo (OID INTEGER PRIMARY KEY, Animal INTEGER, LactationNumber INTEGER, StartDate TEXT, OpenDays INTEGER);
    """)
    con.executemany("INSERT INTO AnimalGroup VALUES (?, ?)", GRUPOS)
    con.executemany("INSERT INTO BasicAnimal VALUES (?, ?)", ANIMALES)
    con.executemany("INSERT INTO AnimalDaily VALUES (?, ?, ?, ?, ?)", REGISTRO_DIARIO)
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

    def animales_de(self, filtro):
        """Los animales (por su OID de BasicAnimal) del historial que pasan un filtro de rebaño."""
        return {f[0] for f in self.con.execute(a_sqlite(f"SELECT ha.ReferenceId FROM HistoryAnimal ha WHERE {filtro('ha.ReferenceId')}")).fetchall()}


class TestElRebanoDeUnAnimalQueYaSeFue(ConBase):
    """`rebano.filtro_del_animal`: el grupo de hoy y, si ya no tiene, el de su último registro diario."""

    def test_los_que_siguen_en_un_grupo_son_del_rebano_de_ese_grupo(self):
        self.assertEqual(self.animales_de(lambda c: rebano.filtro_del_animal(c, [TAMBO])) & {100, 300}, {100, 300})
        self.assertEqual(self.animales_de(lambda c: rebano.filtro_del_animal(c, [OTRO])) & {200, 400}, {200, 400})

    def test_el_que_ya_se_fue_es_del_rebano_de_su_ultimo_registro_diario(self):
        tambo = self.animales_de(lambda c: rebano.filtro_del_animal(c, [TAMBO]))
        otro = self.animales_de(lambda c: rebano.filtro_del_animal(c, [OTRO]))
        self.assertIn(500, tambo)                       # E: todos sus registros son del tambo
        self.assertIn(600, otro)                        # F: su único registro es de OTRO rebaño
        self.assertIn(1000, tambo)                      # J: primero estuvo en OTRO y el último registro es del tambo
        self.assertNotIn(1000, otro)
        self.assertIn(900, otro)                        # I: primero estuvo en el tambo y el último registro es de OTRO
        self.assertNotIn(900, tambo)

    def test_el_que_vive_en_otro_rebano_no_cuenta_por_un_registro_viejo(self):
        """H se mudó: hoy vive en OTRO rebaño. Contarlo también en el tambo por su registro de 2024 lo duplicaría (1.840 animales de La Ponderosa estuvieron en más de un rebaño)."""
        self.assertNotIn(800, self.animales_de(lambda c: rebano.filtro_del_animal(c, [TAMBO])))
        self.assertIn(800, self.animales_de(lambda c: rebano.filtro_del_animal(c, [OTRO])))

    def test_sin_grupo_ni_registro_diario_no_es_de_ningun_rebano(self):
        """G solo tiene un registro dado de baja y otro sin grupo; el 1100 ya no está en BasicAnimal. Se quedan afuera, y de los dos lados."""
        for herd in ([TAMBO], [OTRO]):
            self.assertFalse(self.animales_de(lambda c: rebano.filtro_del_animal(c, herd)) & {700, 1100})

    def test_cada_animal_es_de_un_solo_rebano(self):
        tambo = self.animales_de(lambda c: rebano.filtro_del_animal(c, [TAMBO]))
        otro = self.animales_de(lambda c: rebano.filtro_del_animal(c, [OTRO]))
        self.assertFalse(tambo & otro)
        self.assertEqual(tambo | otro, {100, 200, 300, 400, 500, 600, 800, 900, 1000})

    def test_todos_los_rebanos_no_filtra(self):
        self.assertEqual(rebano.filtro_del_animal("x.y", rebano.TODOS), "1 = 1")

    def test_el_filtro_de_antes_dejaba_afuera_a_los_que_se_fueron(self):
        """La base de la prueba tiene que distinguirlo: `filtro_por_animal` (el grupo de hoy) solo ve a los que siguen."""
        self.assertEqual(self.animales_de(lambda c: rebano.filtro_por_animal(c, [TAMBO])), {100, 300})
        self.assertEqual(self.animales_de(lambda c: rebano.filtro_por_animal(c, [OTRO])), {200, 400, 800})


class TestDiasAbiertosDelTablero(ConBase):
    """El promedio sale de las lactancias (de la 1.ª en adelante) de los animales del rebaño, estén o no todavía en el tambo."""

    def test_promedia_los_dias_abiertos_de_las_lactancias_de_las_vacas_del_rebano_y_de_las_que_se_fueron(self):
        # A 90, C 120, E 60 (se fue) y J 110 (se fue): 95. Antes (el grupo de hoy y sin la lactancia 0): solo A y C, más la lactancia 0 de A (380): 196,7.
        self.assertEqual(app._tablero_dias_abiertos("x", [TAMBO]), {"dias_abiertos": 95.0, "detalle": "4 lactancias"})

    def test_el_rebano_se_filtra_sobre_el_animal_real_tambien_del_otro_lado(self):
        # B 300, D 200, F 250, H 70 e I 80: 180. Con la unión vieja (por el OID del historial) salían las lactancias de otras vacas.
        self.assertEqual(app._tablero_dias_abiertos("x", [OTRO]), {"dias_abiertos": 180.0, "detalle": "5 lactancias"})

    def test_la_lactancia_cero_de_una_novilla_no_es_un_dia_abierto(self):
        """A tiene una fila con `LactationNumber = 0` y 380 días: con ella el promedio del tambo subiría de 95 a 152."""
        self.assertEqual(app._tablero_dias_abiertos("x", [TAMBO])["dias_abiertos"], 95.0)
        self.assertIn("h.LactationNumber >= 1", self.sql_enviado())

    def test_sin_lactancias_con_dias_abiertos_avisa_que_faltan(self):
        self.assertIn("falta", app._tablero_dias_abiertos("x", [99]))

    def test_el_sql_pasa_por_el_historial_y_solo_usa_h_animal_para_eso(self):
        app._tablero_dias_abiertos("x", [TAMBO])
        sql = self.sql_enviado()
        self.assertIn("JOIN HistoryAnimal ha ON ha.OID = h.Animal", sql)
        self.assertIn("ha.ReferenceId", sql)
        self.assertEqual(sql.count("h.Animal"), 1, "h.Animal solo sirve para llegar al historial (ha.OID), no para unir con BasicAnimal")
        self.assertNotIn("b.OID = h.Animal", sql)

    def test_la_union_vieja_daba_otra_cosa(self):
        """La base de la prueba tiene que distinguir la unión vieja (por el OID del historial): sacaba la lactancia de B y la de C (210 días) en vez de las del tambo."""
        vieja = f"""
            SELECT CAST(AVG(CAST(h.OpenDays AS float)) AS decimal(6,1)) AS dias, COUNT(*) AS lactancias
            FROM HistoryAnimalLactationInfo h
            JOIN BasicAnimal b ON b.OID = h.Animal
            WHERE h.OpenDays > 0 AND h.OpenDays < 400
              AND {rebano.filtro('b', [TAMBO])}
        """
        self.assertEqual(self.con.execute(a_sqlite(vieja)).fetchone(), (210.0, 2))


class TestPrenezPorDel(ConBase):
    """`sql_prenez_por_del` cuenta, de las lactancias iniciadas en el rango, las del rebaño: el del animal real, esté o no todavía en el tambo."""

    def correr(self, herd, desde="2025-01-01", hasta="2025-12-31"):
        return self.filas(reproduccion.sql_prenez_por_del(desde, hasta, herd))

    def test_cuenta_las_lactancias_de_las_vacas_del_rebano_y_de_las_que_se_fueron(self):
        r = self.correr([TAMBO])
        # 1.ª lactancia: la de A (90 días) y la de C (sin días abiertos: solo cuenta de denominador).
        self.assertEqual(r["l1"]["n"], 2)
        for tramo in ("p100", "p130", "p150", "p200", "p300"):
            self.assertEqual(r["l1"][tramo], 50.0, tramo)
        # 2.ª y más: la 2.ª de C (120), su 3.ª (450: cuenta como lactancia, pero no como preñez a ningún tramo), la de E (60, se fue) y la de J (110, se fue).
        self.assertEqual(r["l2"]["n"], 4)
        self.assertEqual((r["l2"]["p100"], r["l2"]["p130"], r["l2"]["p200"], r["l2"]["p300"]), (25.0, 75.0, 75.0, 75.0))

    def test_el_rebano_se_filtra_sobre_el_animal_real_tambien_del_otro_lado(self):
        r = self.correr([OTRO])
        # 1.ª: B (300), F (250, se fue) e I (80, se fue). 2.ª y más: D (200) y H (70, vive en OTRO rebaño).
        self.assertEqual(r["l1"]["n"], 3)
        for tramo in ("p100", "p130", "p150", "p200"):
            self.assertAlmostEqual(r["l1"][tramo], 33.3, places=1, msg=tramo)             # solo la de I (80 días)
        self.assertEqual(r["l1"]["p300"], 100.0)
        self.assertEqual((r["l2"]["n"], r["l2"]["p100"], r["l2"]["p150"], r["l2"]["p200"]), (2, 50.0, 50.0, 100.0))

    def test_los_que_no_tienen_rebano_conocido_ni_las_lactancias_cero_cuentan(self):
        """G (se fue sin registro diario), el animal borrado y la lactancia 0 de A no están en ninguno de los dos rebaños: de las 14 lactancias que empiezan en 2025 entran 11."""
        contadas = sum(r["n"] for herd in ([TAMBO], [OTRO]) for r in self.correr(herd).values())
        de_2025 = self.con.execute("SELECT COUNT(*) FROM HistoryAnimalLactationInfo WHERE StartDate >= '2025-01-01' AND StartDate < '2026-01-01'").fetchone()[0]
        self.assertEqual((contadas, de_2025), (2 + 4 + 3 + 2, 14))

    def test_respeta_el_rango_de_fechas(self):
        self.assertEqual(self.correr([TAMBO], "2025-01-01", "2025-04-30")["l1"]["n"], 1)          # solo la de A (marzo)
        self.assertEqual(self.correr([TAMBO], "2026-01-01", "2026-12-31")["l2"]["n"], 1)          # la de febrero 2026, sin preñez cargada
        self.assertEqual(self.correr([TAMBO], "2026-01-01", "2026-12-31")["l2"]["p300"], 0.0)

    def test_el_sql_pasa_por_el_historial_y_el_rebano_se_mira_en_el_animal_real(self):
        sql = " ".join(reproduccion.sql_prenez_por_del("2025-01-01", "2025-12-31", [TAMBO]).split())
        self.assertIn("JOIN HistoryAnimal ha ON ha.OID = h.Animal", sql)
        self.assertIn("b_tb.OID = ha.ReferenceId", sql)                                 # el filtro de rebaño mira al animal de verdad
        self.assertIn("FROM AnimalDaily d_tb", sql)                                     # y a los que ya se fueron los ubica su registro diario
        self.assertEqual(sql.count("h.Animal"), 1, "h.Animal solo sirve para llegar al historial (ha.OID), no para filtrar el rebaño")

    def test_lo_viejo_daba_otra_cosa(self):
        """Con el filtro de rebaño sobre `h.Animal` (el OID del historial), la 1.ª lactancia del tambo era la de B (300 días): ni una preñez a los 100 días."""
        vieja = f"""
            SELECT CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END AS grupo, COUNT(*) AS n,
                   100.0 * SUM(CASE WHEN h.OpenDays > 0 AND h.OpenDays <= 100 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS p100
            FROM HistoryAnimalLactationInfo h
            WHERE h.LactationNumber >= 1 AND h.StartDate >= '2025-01-01' AND h.StartDate < date('2025-12-31', '+1 day')
              AND {rebano.filtro_por_animal('h.Animal', [TAMBO])}
            GROUP BY CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END
        """
        r = {f[0]: f for f in self.con.execute(a_sqlite(vieja)).fetchall()}
        self.assertEqual(r["l1"][2], 0.0)                       # con el arreglo es 50,0 (`test_cuenta_las_lactancias_de_las_vacas_del_rebano_y_de_las_que_se_fueron`)


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
