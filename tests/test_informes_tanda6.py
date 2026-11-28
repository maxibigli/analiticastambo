"""Tanda 6 de campos (28/11/2026): los que quedaban en «Campos que faltan» y se pueden calcular.

  * La leche en los primeros 2 minutos de cada ordeño (kg y % del ordeño), de hoy y de ayer, por número de ordeño: la fórmula que DelPro guarda en `DDMReportItem` (ítems 310 y 285).
  * Las columnas propias del tambo («si… entonces…», `Iif` de DelPro) y el promedio de leche entre dos fechas: dos tipos nuevos de campo calculado (ver `test_informes_reglas`).

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda6 -v
"""
import re
import sqlite3
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_guardados as ig
import informes_motor as im
from tests.test_informes_delpro import CLASES, columna, informe

ITEMS_2MIN = ("YieldFirst2MinTodayMilking", "YieldFirst2MinPercentageTodayMilking", "YieldFirst2MinYesterdayMilking", "YieldFirst2MinPercentageYesterdayMilking")
PRIMEROS_2MIN = tuple(f"{item}_{n}" for item in ITEMS_2MIN for n in (1, 2, 3))
HOY, AYER = "2026-11-18", "2026-11-17"


def compilar(campos, hoy=HOY, **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy=hoy)
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


def a_sqlite(sql: str) -> str:
    """Una capa mínima para correr en SQLite el T-SQL de un bloque: `ISNULL`, `DATEADD(day, n, 'fecha')` y `DATEDIFF(second, a, b)`."""
    sql = sql.replace("ISNULL(", "IFNULL(")
    sql = re.sub(r"DATEADD\(day, (-?\d+), ('[0-9-]+')\)", lambda m: f"date({m.group(2)}, '{m.group(1)} day')", sql)
    return re.sub(r"DATEDIFF\(second, ([\w.]+), ([\w.]+)\)", r"(strftime('%s', \2) - strftime('%s', \1))", sql)


def primeros_2min(flujos):
    """El ítem 310 de DelPro (`YieldFirstTwoMinutes`), escrito en Python: los flujos en kg/min por los minutos de cada tramo."""
    if all(f is None for f in flujos):
        return None
    f0, f15, f30, f60 = (f or 0.0 for f in flujos)
    return f0 * 0.25 + f15 * 0.25 + f30 * 0.5 + f60


# (OID del ordeño, animal, día del registro diario, número de ordeño de la vaca, leche total, (flujo 0-15, 15-30, 30-60, 60-120))
ORDENOS = [
    (1, 10, HOY, 1, 14.0, (1.2, 3.0, 4.0, 3.5)),
    (2, 10, HOY, 2, 12.0, (None, 2.0, 3.0, 3.0)),          # un tramo sin dato cuenta como 0
    (3, 10, HOY, 2, 13.0, (2.0, 2.0, 4.0, 4.0)),           # dos ordeños con el mismo número: el mayor
    (4, 10, HOY, 3, 0.0, (0.5, 0.5, 0.5, 0.5)),            # sin leche: los kg cuentan (DelPro no pide leche), el % no
    (5, 10, AYER, 1, 15.0, (1.0, 2.0, 3.0, 4.0)),
    (6, 20, HOY, 1, 11.0, (None, None, None, None)),       # sin curva: no hay dato
    (7, 30, "2026-11-10", 1, 9.0, (1.0, 1.0, 1.0, 1.0)),   # de otro día: no cuenta
]


def base_de_prueba():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE BasicAnimal (OID INTEGER);
        CREATE TABLE AnimalDaily (OID INTEGER, GCRecord INTEGER, Date TEXT);
        CREATE TABLE SessionMilkYield (OID INTEGER, BasicAnimal INTEGER, AnimalDaily INTEGER, MilkingNumber INTEGER, SessionNo INTEGER, TotalYield REAL, BeginTime TEXT, EndTime TEXT, MilkingDevice INTEGER);
        CREATE TABLE CMSMilkYield (OID INTEGER, MilkingDeviceVisit INTEGER, PeakFlow REAL, TakeOffFlow REAL, AverageFlow REAL, ForcedRetract INTEGER, Flow0To15 REAL, Flow15To30 REAL,
                                   Flow30To60 REAL, Flow60To120 REAL, LowFlowDurationInSec INTEGER, BlockOverride INTEGER);
        CREATE TABLE MilkingDeviceVisit (OID INTEGER, IDTime TEXT);
        CREATE TABLE CMSMpcDevice (OID INTEGER, MpcNumber INTEGER);
    """)
    c.executemany("INSERT INTO BasicAnimal VALUES (?)", [(10,), (20,), (30,)])
    for oid, animal, dia, numero, total, (f0, f15, f30, f60) in ORDENOS:
        c.execute("INSERT INTO AnimalDaily VALUES (?, NULL, ?)", (oid, dia))
        c.execute("INSERT INTO SessionMilkYield VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)", (oid, animal, oid, numero, numero, total, f"{dia} 05:00:00", f"{dia} 05:05:00"))
        c.execute("INSERT INTO CMSMilkYield VALUES (?, NULL, 5, 1, 3, 0, ?, ?, ?, ?, 10, 0)", (oid, f0, f15, f30, f60))
    return c


def valores_del_bloque(id_bloque, dia, columnas):
    """Corre en SQLite el SQL REAL del bloque de ordeños de un día y devuelve {animal: {columna: valor}}."""
    join = a_sqlite(cat.BLOQUES[id_bloque].join.replace("@HOY", f"'{HOY}'"))
    alias = "oh" if id_bloque == "ord_hoy" else "oy"
    c = base_de_prueba()
    sql = "SELECT b.OID, " + ", ".join(f"{alias}.{x}" for x in columnas) + " FROM BasicAnimal b " + join + " ORDER BY b.OID"
    return {f[0]: dict(zip(columnas, f[1:])) for f in c.execute(sql).fetchall()}


class TestLosPrimerosDosMinutos(unittest.TestCase):
    def test_estan_las_doce_variantes_por_numero_de_ordeno(self):
        self.assertEqual(len(PRIMEROS_2MIN), 12)
        for item in ITEMS_2MIN:
            for n in (1, 2, 3):
                c = cat.CATALOGO[f"{item}_{n}"]
                self.assertEqual((c.item, c.parametros), (item, (("MilkingNumber", str(n)),)))
                self.assertEqual(c.bloque, "ord_hoy" if "Today" in item else "ord_ayer")
                self.assertEqual((c.tipo, c.unidad, c.decimales), ("num", "%", 1) if "Percentage" in item else ("num", "kg", 2))
                self.assertFalse(c.verificado)
                self.assertIn("primeros 2 minutos", c.nota)

    def test_cada_uno_compila_y_pasa_la_validacion(self):
        for id_ in PRIMEROS_2MIN:
            db.validate_sql(compilar([id_]))

    def test_los_kg_son_la_formula_de_delpro_por_numero_de_ordeno_y_dia(self):
        hoy = valores_del_bloque("ord_hoy", HOY, ["p21", "p22", "p23"])
        self.assertAlmostEqual(hoy[10]["p21"], primeros_2min((1.2, 3.0, 4.0, 3.5)))
        self.assertAlmostEqual(hoy[10]["p22"], max(primeros_2min((None, 2.0, 3.0, 3.0)), primeros_2min((2.0, 2.0, 4.0, 4.0))))
        self.assertAlmostEqual(hoy[10]["p23"], primeros_2min((0.5, 0.5, 0.5, 0.5)))      # DelPro no pide que el ordeño tenga leche
        self.assertIsNone(hoy[20]["p21"])                                                  # sin curva de flujo
        self.assertIsNone(hoy[30]["p21"])                                                  # sin ordeños ese día
        ayer = valores_del_bloque("ord_ayer", AYER, ["p21"])
        self.assertAlmostEqual(ayer[10]["p21"], primeros_2min((1.0, 2.0, 3.0, 4.0)))

    def test_el_porcentaje_pide_leche_y_es_sobre_el_total_del_ordeno(self):
        hoy = valores_del_bloque("ord_hoy", HOY, ["pp21", "pp22", "pp23"])
        self.assertAlmostEqual(hoy[10]["pp21"], primeros_2min((1.2, 3.0, 4.0, 3.5)) * 100 / 14.0)
        self.assertAlmostEqual(hoy[10]["pp22"], max(primeros_2min((None, 2.0, 3.0, 3.0)) * 100 / 12.0, primeros_2min((2.0, 2.0, 4.0, 4.0)) * 100 / 13.0))
        self.assertIsNone(hoy[10]["pp23"])                                                # 0 kg: sin porcentaje (la fórmula de DelPro pide leche)
        self.assertIsNone(hoy[20]["pp21"])

    def test_el_importador_elige_la_variante_por_el_numero_de_ordeno(self):
        variantes = {(c.item, c.parametros): c.id for c in cat.CATALOGO.values() if c.item}
        for item in ITEMS_2MIN:
            for n in (1, 2, 3):
                self.assertEqual(variantes[(item, (("MilkingNumber", str(n)),))], f"{item}_{n}")


REGLAS = {"id": "calc_1", "etiqueta": "Grupo por leche", "tipo": "reglas", "formula": "Iif([SevenDayAverage] >= 40, 'alta', 'baja')"}
PROMEDIO = {"id": "calc_2", "etiqueta": "Leche prom. 01/09 a 15/09", "tipo": "promedio_leche", "desde": "2026-09-01", "hasta": "2026-09-15"}


def informe_con(calculados, **extra):
    return dict({"columnas": [{"campo": "Number"}] + [{"campo": c["id"]} for c in calculados], "calculados": calculados}, **extra)


class TestElMotorConLasReglas(unittest.TestCase):
    def test_son_un_texto_que_se_agrupa_ordena_resume_y_filtra(self):
        defn = informe_con([REGLAS], agrupar_por=["calc_1"], condiciones=[{"campo": "calc_1", "op": "es", "valor": "alta"}], orden={"campo": "calc_1", "dir": 1})
        defn["columnas"][1]["resumen"] = "count"
        c = im.compilar(defn, herd=1, hoy=HOY)
        caso = "CASE WHEN p7.prom7 >= 40 THEN N'alta' ELSE N'baja' END"
        meta = c["columnas"][1]
        self.assertEqual((meta["id"], meta["tipo"], meta["calculado"], meta["verificado"], meta["resumen"]), ("calc_1", "txt", True, True, "count"))
        self.assertIn(f"{caso} AS c1", c["sql"])
        self.assertIn("AS prom7 FROM AnimalDaily d".replace("AS prom7", "AVG(d.TotalYield) AS prom7"), c["sql"])        # el bloque del campo que la regla mira
        self.assertIn(f"{caso} = N'alta'", c["sql"])
        self.assertIn(f"GROUP BY ROLLUP({caso})", c["sql_resumen"])
        for q in (c["sql"], c["sql_resumen"], im.poblacion_sql(defn, herd=1, hoy=HOY)):
            db.validate_sql(q)
            self.assertNotIn("@HOY", q)

    def test_con_un_campo_sin_comparar_la_columna_queda_sin_comparar(self):
        r = dict(REGLAS, formula="Iif([DaysToCalving] <= 45, 'cerca', '')")
        c = im.compilar(informe_con([r]), herd=1, hoy=HOY)
        self.assertFalse(c["columnas"][1]["verificado"])
        self.assertIn("CONVERT(date, '2026-11-18', 23)", c["sql"])                                                         # el «hoy» de los datos llega a los campos de la regla

    def test_unas_reglas_que_no_se_pueden_usar_dicen_en_que_campo(self):
        for formula, parte in (("Iif([Peso] > 1, 'a', 'b')", "«Peso» todavía no existe"), ("", "Faltan las reglas"), ("Iif([SevenDayAverage] > 1, '@HOY', 'b')", "@")):
            with self.assertRaises(im.InformeError) as e:
                im.compilar(informe_con([dict(REGLAS, formula=formula)]), herd=1)
            self.assertIn("Campo calculado «Grupo por leche»", str(e.exception))
            self.assertIn(parte, str(e.exception))

    def test_unas_reglas_no_pueden_mirar_otro_campo_calculado(self):
        r = dict(REGLAS, id="calc_3", formula="Iif([calc_1] = 'alta', 'a', 'b')")
        with self.assertRaises(im.InformeError):
            im.compilar(informe_con([REGLAS, r]), herd=1)

    def test_en_palabras_el_valor_de_una_condicion_va_entre_comillas(self):
        defn = informe_con([REGLAS], condiciones=[{"campo": "calc_1", "op": "es", "valor": "alta"}], agrupar_por=["calc_1"])
        self.assertEqual(im.describir_condiciones(defn), ["Grupo por leche es «alta»", "Agrupado por Grupo por leche"])


class TestElMotorConElPromedioEntreFechas(unittest.TestCase):
    def test_lee_la_leche_diaria_de_esos_dias_como_delpro(self):
        c = im.compilar(informe_con([PROMEDIO], condiciones=[{"campo": "calc_2", "op": ">", "valor": 30}]), herd=1, hoy=HOY)
        meta = c["columnas"][1]
        self.assertEqual((meta["tipo"], meta["unidad"], meta["decimales"], meta["verificado"]), ("num", "kg", 1, False))
        self.assertIn("LEFT JOIN (SELECT d.BasicAnimal AS animal, AVG(d.TotalYield) AS prom FROM AnimalDaily d WHERE d.GCRecord IS NULL AND d.Date >= CONVERT(date, '2026-09-01', 23) "
                      "AND d.Date < DATEADD(day, 1, CONVERT(date, '2026-09-15', 23)) AND d.TotalYield > 0 GROUP BY d.BasicAnimal) calc_2_l ON calc_2_l.animal = b.OID", c["sql"])
        self.assertIn("calc_2_l.prom > 30", c["sql"])
        db.validate_sql(c["sql"])

    def test_el_sql_real_da_el_promedio_de_los_dias_con_leche_dentro_de_las_fechas(self):
        c = im.compilar(informe_con([PROMEDIO]), herd=1, hoy=HOY)
        join = re.search(r"LEFT JOIN \(SELECT d\.BasicAnimal.*?calc_2_l\.animal = b\.OID", c["sql"]).group(0)
        join = re.sub(r"CONVERT\(date, ('[0-9-]+'), 23\)", r"\1", join)
        join = re.sub(r"DATEADD\(day, 1, ('[0-9-]+')\)", r"date(\1, '1 day')", join)
        s = sqlite3.connect(":memory:")
        s.executescript("CREATE TABLE BasicAnimal (OID INTEGER); CREATE TABLE AnimalDaily (BasicAnimal INTEGER, GCRecord INTEGER, Date TEXT, TotalYield REAL);")
        s.executemany("INSERT INTO BasicAnimal VALUES (?)", [(1,), (2,)])
        s.executemany("INSERT INTO AnimalDaily VALUES (?, ?, ?, ?)", [
            (1, None, "2026-08-31", 99.0), (1, None, "2026-09-01", 30.0), (1, None, "2026-09-10", 0.0), (1, None, "2026-09-12", None),
            (1, 7, "2026-09-13", 500.0), (1, None, "2026-09-15", 40.0), (1, None, "2026-09-16", 99.0), (2, None, "2026-08-01", 20.0)])
        self.assertEqual(s.execute("SELECT b.OID, calc_2_l.prom FROM BasicAnimal b " + join + " ORDER BY b.OID").fetchall(), [(1, 35.0), (2, None)])

    def test_dos_promedios_distintos_no_se_pisan(self):
        otro = dict(PROMEDIO, id="calc_3", etiqueta="Del 20/10", desde="2026-10-20", hasta="2026-10-20")
        c = im.compilar(informe_con([PROMEDIO, otro]), herd=1, hoy=HOY)
        self.assertIn("calc_2_l.prom AS c1", c["sql"])
        self.assertIn("calc_3_l.prom AS c2", c["sql"])
        self.assertIn("CONVERT(date, '2026-10-20', 23)", c["sql"])

    def test_fechas_que_no_sirven(self):
        for cambio, parte in (({"desde": "2026-09-16"}, "no puede ser posterior"), ({"desde": "01/09/2026"}, "fecha"), ({"hasta": "2026-02-30"}, "fecha"),
                              ({"hasta": None}, "fecha"), ({"desde": "2020-01-01"}, "3 años"), ({"desde": "2026-09-01'; DROP"}, "fecha")):
            with self.assertRaises(im.InformeError, msg=cambio) as e:
                im.compilar(informe_con([dict(PROMEDIO, **cambio)]), herd=1)
            self.assertIn(parte, str(e.exception), cambio)

    def test_los_decimales_se_pueden_elegir(self):
        c = im.compilar(informe_con([dict(PROMEDIO, decimales=2)]), herd=1)
        self.assertEqual(c["columnas"][1]["decimales"], 2)
        with self.assertRaises(im.InformeError):
            im.compilar(informe_con([dict(PROMEDIO, decimales=9)]), herd=1)


class TestLosDemasCalculadosSiguenIgual(unittest.TestCase):
    def test_una_cuenta_sin_tipo_o_con_tipo_cuenta(self):
        cuenta = {"id": "calc_1", "etiqueta": "Doble", "formula": "[SevenDayAverage] * 2"}
        for c in (cuenta, dict(cuenta, tipo="cuenta")):
            r = im.compilar(informe_con([c]), herd=1)
            self.assertEqual((r["columnas"][1]["tipo"], r["columnas"][1]["verificado"]), ("num", True))

    def test_un_tipo_que_no_existe(self):
        with self.assertRaises(im.InformeError) as e:
            im.compilar(informe_con([dict(REGLAS, tipo="magia")]), herd=1)
        self.assertIn("tipo", str(e.exception))


# ------------------------------------------------------------------------------------------ el importador de DelPro

REGLA = "Iif([<AnimalExtended>SevenDayAverage] >= 40.0f, 'alta', [<AnimalExtended>ReproductionInfo.IsPregnant] = True, 'preñada', 'baja')"
TICKS_15_09 = "639250272000000000"                                   # 15/09/2026 como lo guarda DelPro (ticks de .NET), el valor real de la columna del informe del tambo
TICKS_01_09 = str(int(TICKS_15_09) - 14 * 864_000_000_000)


def propia(oid, indice, nombre, formula, **kw):
    return columna(oid, 1, indice, nombre, FormulaPropia=formula, **kw)


def numero(**kw):
    return columna(11, 1, 1, "Number", **kw)


def traer(columnas, parametros=(), **del_informe):
    d = dl.armar_datos([informe(1, **del_informe)], columnas, [], [{"OID": k, "TypeName": v} for k, v in CLASES.items()], list(parametros))
    return dl.traducir_informe(d, 1)


def param(columna_oid, nombre, valor):
    return {"columna": columna_oid, "nombre": nombre, "valor": valor}


def textos(r):
    return " | ".join(d["texto"] + " " + " ".join(d.get("detalle") or []) for d in r["diferencias"])


class TestElImportadorTraeLasColumnasPropias(unittest.TestCase):
    def test_se_trae_como_reglas_con_su_agrupacion(self):
        r = traer([numero(), propia(12, 2, "Mi regla", REGLA, GroupIndex=0)])
        d = r["definicion"]
        self.assertEqual(d["calculados"], [{"id": "calc_1", "etiqueta": "Mi regla", "tipo": "reglas", "formula": REGLA}])
        self.assertEqual(d["columnas"], [{"campo": "Number"}, {"campo": "calc_1"}])
        self.assertEqual(d["agrupar_por"], ["calc_1"])
        self.assertEqual((r["estado"], r["faltan"]), ("completo", []), textos(r))
        self.assertIn("«Mi regla»", textos(r))
        self.assertIn("volver a traerlo", textos(r))
        im.compilar(d, herd=1)

    def test_la_misma_columna_dos_veces_es_un_solo_campo_con_su_resumen(self):
        r = traer([numero(), propia(12, 2, "Mi regla", REGLA), propia(13, 3, "Mi regla", REGLA, SummaryType=3, SummaryTypes="3")])
        self.assertEqual(len(r["definicion"]["calculados"]), 1)
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}, {"campo": "calc_1", "resumen": "count"}])

    def test_un_filtro_de_la_lista_sobre_la_columna_propia(self):
        r = traer([numero(), propia(12, 2, "Mi regla", REGLA)], CustomFilter="[C12] = 'alta'")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "calc_1", "op": "es", "valor": "alta"}], textos(r))
        self.assertEqual(r["estado"], "completo")

    def test_unas_reglas_que_todavia_no_se_calculan_quedan_en_lo_que_falta_con_el_motivo(self):
        r = traer([numero(), propia(12, 2, "Rara", "Iif([<AnimalExtended>Peso] > 1, 'a', 'b')", GroupIndex=0)])
        self.assertEqual((r["faltan"], r["estado"]), (["Rara"], "parcial"))
        self.assertNotIn("calculados", r["definicion"])
        self.assertIn("«Peso» todavía no existe", textos(r))

    def test_una_columna_que_no_es_propia_no_se_calcula_aunque_traiga_formula(self):
        r = traer([numero(), columna(12, 1, 2, "CampoRaro")])
        self.assertEqual(r["faltan"], ["CampoRaro"])
        self.assertNotIn("calculados", r["definicion"])

    def test_entran_hasta_diez_campos_calculados(self):
        cols = [numero()] + [propia(20 + i, 2 + i, f"Regla {i}", f"Iif([SevenDayAverage] > {i}, 'sí', 'no')") for i in range(11)]
        r = traer(cols)
        self.assertEqual(len(r["definicion"]["calculados"]), im.CALCULADOS_MAX)
        self.assertEqual(r["faltan"], ["Regla 10"])
        self.assertIn("10 campos calculados", textos(r))
        im.compilar(r["definicion"], herd=1)

    def test_los_nombres_se_acortan_y_no_se_repiten(self):
        largo = "Una columna con un nombre larguísimo que no entra"
        r = traer([numero(), propia(12, 2, largo, REGLA), propia(13, 3, largo, "Iif([SevenDayAverage] > 1, 'a', 'b')")])
        nombres = [c["etiqueta"] for c in r["definicion"]["calculados"]]
        self.assertEqual(len(set(nombres)), 2)
        self.assertTrue(all(len(n) <= im.ETIQUETA_MAX for n in nombres), nombres)
        self.assertTrue(nombres[0].startswith("Una columna con un nombre"))


class TestUnaColumnaPropiaConNombreDeCampoDelCatalogo(unittest.TestCase):
    def test_manda_la_formula_del_tambo_y_no_el_campo_de_aca(self):
        """El nombre de una columna que armó el tambo puede coincidir con un campo del catálogo: traer el campo sería mostrar OTRO dato con el mismo nombre."""
        r = traer([numero(), propia(12, 2, "InMilk", REGLA)])
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}, {"campo": "calc_1"}])
        self.assertEqual(r["definicion"]["calculados"][0]["etiqueta"], "InMilk")

    def test_tampoco_se_toma_por_un_alias_de_otro_campo(self):
        """`DIM` de la reproducción es el alias de «Días en ordeño», pero no si el tambo armó una columna con ese nombre."""
        clases = {**CLASES, 50: "DeLaval.DDM.CommonApp.Module.BasicFeatures.Reproduction.AnimalReproductionInfo"}
        d = dl.armar_datos([informe(1)], [numero(), propia(12, 2, "DIM", REGLA, ClaseItem=50)], [], [{"OID": k, "TypeName": v} for k, v in clases.items()])
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}, {"campo": "calc_1"}])

    def test_y_si_su_formula_no_se_puede_calcular_queda_faltando(self):
        r = traer([numero(), propia(12, 2, "InMilk", "Iif([<AnimalExtended>Peso] > 1, 'a', 'b')")])
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}])
        self.assertEqual(r["faltan"], ["InMilk (del tambo)"])
        self.assertIn("«Peso» todavía no existe", textos(r))


class TestSeGuardaYSeVuelveACorrer(unittest.TestCase):
    def test_un_informe_con_los_dos_tipos_pasa_el_control_de_guardado(self):
        defn = informe_con([REGLAS, PROMEDIO], agrupar_por=["calc_1"], condiciones=[{"campo": "calc_2", "op": ">", "valor": 20}])
        limpia = ig.validar_definicion(dict(defn, otra_clave=1))
        self.assertEqual(limpia["calculados"], [REGLAS, PROMEDIO])
        self.assertNotIn("otra_clave", limpia)
        with self.assertRaises(ig.InformeGuardadoError):
            ig.validar_definicion(informe_con([dict(REGLAS, formula="Iif([Peso] > 1, 'a', 'b')")]))


class TestElImportadorTraeElPromedioEntreFechas(unittest.TestCase):
    def test_las_fechas_salen_de_los_parametros_de_la_columna(self):
        r = traer([numero(), columna(12, 1, 2, "AvgDailyYieldDates")], [param(12, "FromDate", TICKS_15_09), param(12, "ToDate", TICKS_15_09)])
        self.assertEqual(r["definicion"]["calculados"], [{"id": "calc_1", "etiqueta": "Leche prom. del 15/09/2026", "tipo": "promedio_leche", "desde": "2026-09-15", "hasta": "2026-09-15"}])
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}, {"campo": "calc_1"}])
        self.assertEqual((r["estado"], r["faltan"]), ("completo", []), textos(r))
        self.assertIn("15/09/2026", textos(r))
        im.compilar(r["definicion"], herd=1)

    def test_un_periodo_y_dos_columnas_distintas(self):
        r = traer([numero(), columna(12, 1, 2, "AvgDailyYieldDates"), columna(13, 1, 3, "AvgDailyYieldDates")],
                  [param(12, "FromDate", TICKS_01_09), param(12, "ToDate", TICKS_15_09), param(13, "FromDate", TICKS_15_09), param(13, "ToDate", TICKS_15_09)])
        c = r["definicion"]["calculados"]
        self.assertEqual([(x["desde"], x["hasta"]) for x in c], [("2026-09-01", "2026-09-15"), ("2026-09-15", "2026-09-15")])
        self.assertEqual(c[0]["etiqueta"], "Leche prom. 01/09/2026 a 15/09/2026")
        self.assertEqual([x["campo"] for x in r["definicion"]["columnas"]], ["Number", "calc_1", "calc_2"])

    def test_sin_fechas_legibles_sigue_faltando(self):
        for parametros in ((), (param(12, "FromDate", TICKS_15_09),), (param(12, "FromDate", "ayer"), param(12, "ToDate", TICKS_15_09)),
                           (param(12, "FromDate", str(int(TICKS_15_09) + 3600 * 10_000_000)), param(12, "ToDate", TICKS_15_09)),
                           (param(12, "FromDate", TICKS_15_09), param(12, "ToDate", TICKS_01_09)), (param(12, "FromDate", "9" * 30), param(12, "ToDate", "9" * 30))):
            r = traer([numero(), columna(12, 1, 2, "AvgDailyYieldDates")], list(parametros))
            self.assertEqual(r["faltan"], ["AvgDailyYieldDates"], parametros)
            self.assertIn("fechas", textos(r))


class TestLaConsultaDelImportador(unittest.TestCase):
    def test_pide_la_formula_solo_de_los_campos_propios_del_tambo(self):
        sql = dl.CONSULTAS["columnas"]
        self.assertIn("CASE WHEN it.IsUserDefined = 1 THEN it.Expression END AS FormulaPropia", sql)
        db.validate_sql(sql)


if __name__ == "__main__":
    unittest.main()
