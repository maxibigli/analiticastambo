"""Las cuentas sobre el PASADO incluyen a los animales que se dieron de baja (25-26/11/2026).

Un animal que se va pierde su `[Group]`: un filtro por el grupo de HOY (`rebano.filtro` / `filtro_por_animal`) lo borra de los servicios, abortos, partos, preñeces, la composición histórica, la
producción de meses pasados... Ahora esas cuentas usan `rebano.filtro_del_animal` (el grupo de hoy o, sin grupo, el del último registro diario). Las nacidas muertas no tienen ni grupo ni registro
(DelPro les pone número -1 y motivo «Death»): su rebaño es el de la MADRE (`rebano.filtro_del_ternero`); sin eso la mortandad de terneros no contaba ninguna.

Estas pruebas corren el SQL real de las consultas contra una base SQLite en memoria (con la capa mínima `a_sqlite` para pasar de T-SQL); las que usan `CROSS APPLY` o `VALUES` se prueban por el
filtro que arman. Las consultas de lo que PASA HOY (secar, partos esperados, conciliación) no cambian: un animal dado de baja no va en esas listas.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_dados_de_baja -v
"""
import re
import sqlite3
import unittest

import app
import conversion_historica
import db
import diagnostico_baja
import gestacion
import performance
import preneces
import proyeccion
import rebano
import reproduccion
import tasa_prenez

TAMBO, OTRO = 1, 6
MARCA = "FROM AnimalDaily d_tb"          # lo que delata a un filtro que sabe ubicar a un animal que ya se fue

# BasicAnimal: (OID, Number, [Group], BirthDate, ExitDate, BirthEvent)
#   100 A  vive en el tambo                         500 N  nacida muerta, hija de C (se fue; tambo): sin grupo ni registro
#   200 B  vive en OTRO rebaño                      700 O  nacida muerta, hija de D (se fue; OTRO)
#   300 C  se fue; último registro en el tambo      800 P  ternero que salió a los 5 días, con registro en el tambo
#   400 D  se fue; último registro en OTRO          900 Q  salió a los 3 días, sin grupo, registro ni parto de nacimiento: no se sabe de quién es
#                                                  950 R  salió a los 2 días sin grupo ni registro, hijo de A
ANIMALES = [
    (100, 11, 10, "2021-01-01", None, None), (200, 12, 20, "2021-01-01", None, None),
    (300, 13, None, "2021-01-01", "2025-09-30", None), (400, 14, None, "2021-01-01", "2025-09-30", None),
    (500, -1, None, "2025-06-01", "2025-06-01", 31), (700, -1, None, "2025-06-02", "2025-06-02", 41),
    (800, 18, None, "2025-06-03", "2025-06-08", None), (900, 19, None, "2025-06-04", "2025-06-07", None),
    (950, 20, None, "2025-06-05", "2025-06-07", 11),
]
GRUPOS = [(10, TAMBO), (20, OTRO)]
NOMBRES = [(10, "Rodeo 1", 1), (20, "VO Don German", 60)]
# AnimalDaily: (OID, BasicAnimal, Date, AnimalGroup, TotalYield, DIM, LactationNumber, IsYieldValid)
DIARIO = [
    (1, 100, "2025-06-10", 10, 30.0, 20, 2, 1), (2, 100, "2025-07-10", 10, 32.0, 50, 2, 1),
    (3, 200, "2025-06-10", 20, 10.0, 20, 2, 1),
    (4, 300, "2025-06-10", 10, 40.0, 20, 2, 1), (5, 300, "2025-07-10", 10, 44.0, 50, 2, 1), (6, 300, "2025-08-10", 10, 36.0, 80, 2, 1),
    (7, 300, "2025-09-10", 10, 36.0, 110, 2, 1), (8, 300, "2025-09-11", 10, 36.0, 111, 2, 1),
    (9, 400, "2025-06-10", 20, 12.0, 20, 2, 1),
    (10, 800, "2025-06-04", 10, 0.0, 0, 0, 0),
]
# AbstractAnimalEvent: (OID, BasicAnimal, DateAndTime, LactationNumber); las tablas de cada tipo cuelgan del mismo OID
EVENTOS = [
    # partos: A (11) y C (31) del tambo; B (21) y D (41) de OTRO
    (11, 100, "2025-05-30 08:00:00", 2), (21, 200, "2025-05-30 09:00:00", 2), (31, 300, "2025-06-01 07:00:00", 2), (41, 400, "2025-06-02 07:00:00", 2),
    # servicios: uno de cada vaca
    (12, 100, "2025-08-01 08:00:00", 2), (22, 200, "2025-08-01 09:00:00", 2), (32, 300, "2025-08-02 07:00:00", 2), (42, 400, "2025-08-02 07:00:00", 2),
    # chequeos positivos sobre esos servicios
    (13, 100, "2025-09-05 08:00:00", 2), (23, 200, "2025-09-05 09:00:00", 2), (33, 300, "2025-09-06 07:00:00", 2), (43, 400, "2025-09-06 07:00:00", 2),
    # un aborto de C
    (34, 300, "2025-09-20 07:00:00", 2),
    # salidas
    (35, 300, "2025-09-30 07:00:00", 2), (45, 400, "2025-09-30 07:00:00", 2),
    (51, 500, "2025-06-01 07:30:00", 0), (71, 700, "2025-06-02 07:30:00", 0), (81, 800, "2025-06-08 07:30:00", 0),
    (91, 900, "2025-06-07 07:30:00", 0), (96, 950, "2025-06-07 07:30:00", 0),
    # cambios de rodeo: uno real de C y el «a ningún rodeo» del día de su salida
    (36, 300, "2025-09-15 07:00:00", 2), (37, 300, "2025-09-30 07:00:00", 2),
    # un tratamiento con retiro de leche de C
    (38, 300, "2025-09-15 10:00:00", 2),
]
PARTOS = [11, 21, 31, 41]
SERVICIOS = [12, 22, 32, 42]
CHEQUEOS = [(13, 1, 12), (23, 1, 22), (33, 1, 32), (43, 1, 42)]
ABORTOS = [34]
SALIDAS = [(35, 1), (45, 1), (51, 50), (71, 50), (81, 2), (91, 2), (96, 2)]
CAMBIOS = [(36, 10, 20), (37, None, 10)]      # (OID, NewGroup, OldGroup)
TRATAMIENTOS = [(38, "2025-09-20")]


def _llamadas(sql: str, nombre: str, cambiar) -> str:
    """Reemplaza cada `NOMBRE(args)` por `cambiar(args)` (None = dejarla), de la más interna a la más externa."""
    for m in reversed(list(re.finditer(r"\b" + nombre + r"\s*\(", sql, re.I))):
        i, nivel, ini, args, comilla = m.end(), 1, m.end(), [], False
        while nivel and i < len(sql):
            c = sql[i]
            if comilla:
                comilla = c != "'"
            elif c == "'":
                comilla = True
            elif c == "(":
                nivel += 1
            elif c == ")":
                nivel -= 1
            elif c == "," and nivel == 1:
                args.append(sql[ini:i])
                ini = i + 1
            i += 1
        args.append(sql[ini:i - 1])
        nuevo = cambiar([a.strip() for a in args])
        if nuevo is not None:
            sql = sql[:m.start()] + nuevo + sql[i:]
    return sql


def a_sqlite(sql: str) -> str:
    """Lo mínimo para correr el T-SQL de estas consultas en SQLite."""
    sql = re.sub(r"OPTION\s*\([^)]*\)\s*$", "", sql.strip())
    sql = re.sub(r"\(SELECT TOP 1 (.+?) (ORDER BY [^)]+)\)", r"(SELECT \1 \2 LIMIT 1)", sql, flags=re.S)
    sql = _llamadas(sql, "DATEDIFF", lambda a: f"CAST(julianday(date({a[2]})) - julianday(date({a[1]})) AS INTEGER)")
    sql = _llamadas(sql, "DATEADD", lambda a: f"date({a[2]}, ({a[1]}) || ' {a[0].lower()}s')")
    sql = _llamadas(sql, "CAST", lambda a: f"date({a[0][:-8]})" if a[0].lower().endswith(" as date") else None)
    sql = _llamadas(sql, "FORMAT", lambda a: f"strftime('%Y-%m', {a[0]})")
    sql = _llamadas(sql, "CONVERT", lambda a: f"substr({a[1]}, 1, 10)")
    sql = _llamadas(sql, "LEFT", lambda a: f"substr({a[0]}, 1, {a[1]})")
    return re.sub(r"\bISNULL\s*\(", "ifnull(", sql, flags=re.I)


def base_de_prueba() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE AnimalGroup (OID INTEGER PRIMARY KEY, Herd INTEGER);
        CREATE TABLE AbstractGroup (OID INTEGER PRIMARY KEY, Name TEXT, Number INTEGER, GCRecord INTEGER);
        CREATE TABLE BasicAnimal (OID INTEGER PRIMARY KEY, Number INTEGER, [Group] INTEGER, BirthDate TEXT, ExitDate TEXT, BirthEvent INTEGER, GCRecord INTEGER);
        CREATE TABLE AnimalDaily (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, [Date] TEXT, AnimalGroup INTEGER, TotalYield REAL, DIM INTEGER, LactationNumber INTEGER, IsYieldValid INTEGER, GCRecord INTEGER);
        CREATE TABLE AbstractAnimalEvent (OID INTEGER PRIMARY KEY, BasicAnimal INTEGER, DateAndTime TEXT, LactationNumber INTEGER, GCRecord INTEGER);
        CREATE TABLE EventCalving (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventInsemination (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventPregCheck (OID INTEGER PRIMARY KEY, Result INTEGER, EffectiveInsemination INTEGER);
        CREATE TABLE EventAbortion (OID INTEGER PRIMARY KEY);
        CREATE TABLE EventExit (OID INTEGER PRIMARY KEY, ExitReason INTEGER);
        CREATE TABLE EventGroupChange (OID INTEGER PRIMARY KEY, NewGroup INTEGER, OldGroup INTEGER);
        CREATE TABLE DiagnosisTreatmentEvent (OID INTEGER PRIMARY KEY, Diagnosis INTEGER, MilkWithholdEndDate TEXT, DumpMilkEndDate TEXT);
        CREATE TABLE Diagnosis (OID INTEGER PRIMARY KEY, DiagnosisName INTEGER);
        CREATE TABLE TextLookupItem (OID INTEGER PRIMARY KEY, ItemValue TEXT);
        CREATE TABLE AnimalLactationSummary (OID INTEGER PRIMARY KEY, Animal INTEGER, LactationNumber INTEGER, StartDate TEXT, PeakYield REAL, DaysToPeak INTEGER, GCRecord INTEGER);
    """)
    con.executemany("INSERT INTO AnimalGroup VALUES (?, ?)", GRUPOS)
    con.executemany("INSERT INTO AbstractGroup VALUES (?, ?, ?, NULL)", NOMBRES)
    con.executemany("INSERT INTO BasicAnimal VALUES (?, ?, ?, ?, ?, ?, NULL)", ANIMALES)
    con.executemany("INSERT INTO AnimalDaily VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)", DIARIO)
    con.executemany("INSERT INTO AbstractAnimalEvent VALUES (?, ?, ?, ?, NULL)", EVENTOS)
    con.executemany("INSERT INTO EventCalving VALUES (?)", [(i,) for i in PARTOS])
    con.executemany("INSERT INTO EventInsemination VALUES (?)", [(i,) for i in SERVICIOS])
    con.executemany("INSERT INTO EventPregCheck VALUES (?, ?, ?)", CHEQUEOS)
    con.executemany("INSERT INTO EventAbortion VALUES (?)", [(i,) for i in ABORTOS])
    con.executemany("INSERT INTO EventExit VALUES (?, ?)", SALIDAS)
    con.executemany("INSERT INTO EventGroupChange VALUES (?, ?, ?)", CAMBIOS)
    con.executemany("INSERT INTO DiagnosisTreatmentEvent VALUES (?, 1, ?, NULL)", TRATAMIENTOS)
    con.execute("INSERT INTO Diagnosis VALUES (1, 3)")
    con.executemany("INSERT INTO TextLookupItem VALUES (?, ?)", [(1, "DESC. X OTRAS CAUSAS"), (2, "Diarrea"), (3, "Mastitis"), (50, "Death")])
    con.executemany("INSERT INTO AnimalLactationSummary VALUES (?, ?, 2, '2025-06-01', ?, 60, NULL)", [(1, 100, 40.0), (2, 300, 50.0), (3, 200, 20.0)])
    return con


class ConBase(unittest.TestCase):
    def setUp(self):
        self.con = base_de_prueba()
        self.addCleanup(self.con.close)

    def filas(self, sql) -> list:
        db.validate_sql(sql)                                       # la misma regla de «solo SELECT» que la app
        cur = self.con.execute(a_sqlite(sql))
        columnas = [d[0] for d in cur.description]
        return [dict(zip(columnas, f)) for f in cur.fetchall()]

    def animales_de(self, filtro) -> set:
        return {f[0] for f in self.con.execute(a_sqlite(f"SELECT b.OID FROM BasicAnimal b WHERE {filtro('b.OID')}")).fetchall()}


class TestElRebanoDeUnTernero(ConBase):
    def test_una_nacida_muerta_es_del_rebano_de_su_madre(self):
        tambo = self.animales_de(lambda c: rebano.filtro_del_ternero(c, [TAMBO]))
        otro = self.animales_de(lambda c: rebano.filtro_del_ternero(c, [OTRO]))
        self.assertEqual(tambo, {100, 300, 500, 800, 950})        # N (hija de C) y R (hijo de A) por la madre; P por su registro
        self.assertEqual(otro, {200, 400, 700})                    # O por la madre; Q (sin madre ni registro) no es de nadie
        self.assertEqual(rebano.filtro_del_ternero("x.y", rebano.TODOS), "1 = 1")

    def test_la_madre_solo_cuenta_si_el_ternero_no_tiene_grupo_ni_registro(self):
        """Si el ternero tiene su propio rebaño (grupo o registro), vale el suyo, aunque la madre sea de otro."""
        self.con.execute("INSERT INTO AbstractAnimalEvent VALUES (99, 200, '2025-06-03 06:00:00', 2, NULL)")       # P pasa a ser hijo de B (OTRO)
        self.con.execute("UPDATE BasicAnimal SET BirthEvent = 99 WHERE OID = 800")
        self.assertIn(800, self.animales_de(lambda c: rebano.filtro_del_ternero(c, [TAMBO])))
        self.assertNotIn(800, self.animales_de(lambda c: rebano.filtro_del_ternero(c, [OTRO])))


class TestAnalisisReproductivo(ConBase):
    def test_partos_del_periodo_cuentan_a_las_madres_que_se_fueron(self):
        r = self.filas(reproduccion.sql_partos_periodo("2025-05-01", "2025-07-01", [TAMBO]))
        self.assertEqual(r[0]["partos"], 2)                                   # A y C (antes: solo A)

    def test_la_mortandad_cuenta_las_nacidas_muertas(self):
        r = self.filas(reproduccion.sql_bajas_terneros("2025-06-01", "2025-07-01", 90, [TAMBO]))
        self.assertEqual(sorted((f["rp"], f["edad_dias"]) for f in r), [(-1, 0), (18, 5), (20, 2)])
        datos = reproduccion.armar_bajas_terneros(list(r[0]), [list(f.values()) for f in r], 4, "2025-06-01", "2025-07-01", 90)
        self.assertEqual((datos["resumen"]["bajas"], datos["resumen"]["nacidas_muertas"], datos["resumen"]["pct"]), (3, 1, 75.0))
        muerta = next(f for f in datos["filas"] if f["rp"] is None)
        self.assertEqual(muerta["motivo"], "Death (nacida muerta)")

    def test_los_abortos_cuentan_los_de_las_vacas_que_se_fueron(self):
        r = {f["grupo"]: f for f in self.filas(reproduccion.sql_abortos("2025-05-01", "2025-12-31", [TAMBO]))}
        self.assertEqual((r["l2"]["abortos"], r["l2"]["partos"]), (1, 2))

    def test_los_servicios_y_prenos_por_ciclo(self):
        r = self.filas(reproduccion.sql_servicios_por_ciclo("2025-10-01", 4, [TAMBO], 21))
        self.assertEqual((sum(f["servicios"] for f in r), sum(f["preneces"] for f in r)), (2, 2))

    def test_las_concepciones(self):
        r = self.filas(preneces.sql_concepciones("2025-07-01", "2025-09-30", preneces.TIPO_AMBAS, "mes", [TAMBO]))
        self.assertEqual(sum(f["l2"] for f in r), 2)

    def test_los_servicios_del_grafico_de_calor(self):
        r = self.filas(app.sql_servicios_mensuales("2025-01-01", "2025-12-31", [TAMBO]))
        self.assertEqual([(f["mes"], f["servicios"], f["concepciones"]) for f in r], [("2025-08", 2, 2)])

    def test_la_composicion_de_una_fecha_pasada(self):
        """El 15/07/2025 C todavía estaba: la composición de ese día la cuenta aunque después se haya ido."""
        r = self.filas(reproduccion.sql_inventario_historico(["2025-07-15"], [TAMBO]))
        self.assertEqual((r[0]["total_vacas"], r[0]["vacas_ordeno"]), (2, 2))

    def test_gestacion_y_tasa_de_prenez_arman_el_filtro_que_ubica_a_los_que_se_fueron(self):
        for sql in (gestacion.sql_por_mes("2025-01-01", "2025-12-31", [TAMBO]), gestacion.sql_distribucion("2025-01-01", "2025-12-31", [TAMBO])):
            self.assertIn(MARCA, sql)
        for tipo in tasa_prenez.TIPOS:
            sql = tasa_prenez.sql_embudo([{"n": 1, "desde": "2025-08-01", "hasta": "2025-08-21"}], tipo, 53, 447, [TAMBO])
            self.assertEqual(sql.count(MARCA), 2 if tipo == tasa_prenez.TIPO_AMBAS else 1, tipo)
            self.assertNotIn(rebano.filtro("b", [TAMBO]), sql, tipo)
            db.validate_sql(sql)


class TestProyeccionYProduccion(ConBase):
    def test_los_partos_reales_y_el_kg_por_vaca(self):
        r = {f["mes"]: f for f in self.filas(proyeccion.sql_partos_reales("2025-05", "2025-06", [TAMBO]))}
        self.assertEqual((r["2025-05"]["vacas"], r["2025-06"]["vacas"]), (1, 1))
        r = {f["mes"]: f for f in self.filas(proyeccion.sql_kg_por_vaca("2025-06", "2025-06", [TAMBO]))}
        self.assertEqual(round(r["2025-06"]["kg_vaca_dia"], 2), 35.0)                  # A 30 y C 40

    def test_las_lactantes_medidas_arman_el_filtro_que_ubica_a_las_que_se_fueron(self):
        sql = proyeccion.sql_lactantes_historico("2025-01", "2025-06", [TAMBO])
        self.assertIn(MARCA, sql)
        db.validate_sql(sql)

    def test_la_curva_el_pico_y_la_concentracion(self):
        r = self.filas(performance.sql_curva("2025-06-01", "2025-06-30", [TAMBO]))
        self.assertEqual(sum(f["vacas"] for f in r), 2)
        r = self.filas(performance.sql_peak("2025-06-01", "2025-07-31", [TAMBO]))
        self.assertEqual(sum(f["vacas"] for f in r), 2)                                   # A y C tienen arranque y pico
        r = self.filas(performance.sql_concentracion("2025-06-01", "2025-09-30", [TAMBO]))
        self.assertEqual(sum(f["vacas"] for f in r), 1)                                   # solo C tiene 5 días medidos

    def test_la_distribucion_por_rodeo_pone_aparte_a_las_dadas_de_baja(self):
        r = {f["segmento"]: f for f in self.filas(performance.sql_distribucion("2025-06-01", "2025-06-30", "grupo", [TAMBO]))}
        self.assertEqual(set(r), {"Rodeo 1", "Dadas de baja"})
        self.assertEqual((r["Dadas de baja"]["vacas"], r["Dadas de baja"]["kg_total"]), (1, 40.0))
        r = self.filas(performance.sql_distribucion("2025-06-01", "2025-06-30", "lactancia", [TAMBO]))
        self.assertEqual(sum(f["vacas"] for f in r), 2)

    def test_los_solidos_historicos_arman_el_filtro_que_ubica_a_las_que_se_fueron(self):
        sql = conversion_historica.sql_solidos_por_control("2025-01-01", "2025-12-31", [TAMBO])
        self.assertIn(MARCA, sql)
        db.validate_sql(sql)


class TestDiagnosticoDeBaja(ConBase):
    def setUp(self):
        super().setUp()
        from unittest import mock
        p = mock.patch.object(diagnostico_baja.rebano, "por_defecto", lambda tambo: [TAMBO])
        p.start()
        self.addCleanup(p.stop)

    def test_los_eventos_de_los_dias_analizados_cuentan_a_las_que_se_fueron(self):
        r = self.filas(diagnostico_baja.sql_eventos("2025-09-01", "2025-09-30", "x"))
        eventos = {(f["tipo"], f["fecha"]): f["n"] for f in r}
        self.assertEqual(eventos[("tratamiento", "2025-09-15")], 1)
        self.assertEqual(eventos[("cambio de grupo", "2025-09-15")], 1)
        self.assertNotIn(("cambio de grupo", "2025-09-30"), eventos)               # «a ningún rodeo» es la salida, no una mudanza
        r = self.filas(diagnostico_baja.sql_eventos("2025-05-30", "2025-06-01", "x"))
        self.assertEqual(sum(f["n"] for f in r if f["tipo"] == "parto"), 2)

    def test_las_tratadas_y_el_retiro(self):
        self.assertEqual([f["animal"] for f in self.filas(diagnostico_baja.sql_tratadas("2025-09-15", "x"))], [300])
        self.assertEqual([f["vacas"] for f in self.filas(diagnostico_baja.sql_retiro("2025-09-01", "2025-09-30", "x"))], [1])


if __name__ == "__main__":
    unittest.main()
