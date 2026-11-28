"""Las reglas «si… entonces…» (`Iif` de DelPro) como campo calculado de un informe (`informes_reglas`, tanda 6).

Lo central es que den LO MISMO que la regla leída a mano: cada fórmula se corre como SQL REAL en SQLite contra cientos de combinaciones de valores (incluidos los vacíos) y se compara con un evaluador
escrito acá, aparte del traductor, con la lógica de tres valores de SQL (un animal sin el dato no cumple ni «≤ 5» ni «no ≤ 5»). Las fórmulas son inventadas, del estilo de las del tambo.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_reglas -v
"""
import itertools
import re
import sqlite3
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_reglas as rg

C = cat.Campo
CAMPOS = {
    "Prom": C("Prom", "Promedio", "X", "num", "t.prom"),
    "DEL": C("DEL", "Días en leche", "X", "num", "t.dias"),
    "Prenada": C("Prenada", "Preñada", "X", "txt", cat._si_no("t.pren = 1"), valores=cat.SI_NO),
    "Rodeo": C("Rodeo", "Rodeo", "X", "txt", "t.rodeo"),
    "Fecha": C("Fecha", "Fecha", "X", "fecha", "t.fecha"),
}
VALORES = {"prom": (None, 20.0, 39.9, 40.0, 46.0, 47.0, 60.0), "dias": (None, 5, 20, 21, 45, 46, 100), "pren": (None, 0, 1), "rodeo": (None, "Rodeo 1", "Rodeo 9"),
           "fecha": (None, "2026-01-01", "2026-06-30", "2026-12-31")}

FORMULAS = (
    "Iif(Not [Prenada] = True And [Prom] >= 47.0f Or [Prenada] = True And (Not [DEL] <= 45 And [Prom] >= 47.0f Or [DEL] <= 45 And [Prom] >= 46.0f And [Prom] >= 47.0f), 'a Rodeo 1', "
    "Not [Prenada] = True And Not [Prom] >= 47.0f And [Prom] >= 40.0f, 'a Rodeo 2', 'Ninguna acción')",
    "Iif([Prom] <= 40.0f And [DEL] <= 20, '1.1 Fresca Flaca', [Prom] <= 40.0f And Not [DEL] <= 20, '1. Flaca', Not [Prom] <= 40.0f And [DEL] <= 20, '3.2', '')",
    "Iif([<AnimalExtended>Rodeo] In ('Rodeo 1', 'Rodeo 2'), 'x', [Rodeo] <> 'Rodeo 9', 'y', 'z')",
    "Iif([Fecha] Between(#2026-01-01#, #2026-06-30#), 'a', [Fecha] > #2026-06-30#, 'b', [Fecha] Is Null, 'sin fecha', 'otra')",
    "Iif([Prenada], Iif([DEL] >= 46, 'preñada tardía', 'preñada'), Iif([Prom] Is Not Null, 'con dato', null))",
    "Iif(40 <= [Prom] And Not ([DEL] > 20 Or [Prenada] <> True), 'sí', 'no')",
    "Iif(Not [Prenada] <> True And [DEL] Between(20, 45), 'ventana', [Rodeo] = 'Rodeo 1' Or [Fecha] < #2026-06-30#, 'otro', 'nada')",
    "Iif([Prom] > 40, True, False)",
    "Iif([Prom] > 40, 1, [Prom] > 20, 2.5, 3)",
)


def a_sqlite(sql: str) -> str:
    sql = re.sub(r"CONVERT\(date, ('[0-9-]+'), 23\)", r"\1", sql)
    sql = re.sub(r"CAST\((t\.\w+) AS date\)", r"date(\1)", sql)
    return sql.replace("N'", "'")


def en_sqlite(sql: str) -> list:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (i INTEGER, prom REAL, dias INTEGER, pren INTEGER, rodeo TEXT, fecha TEXT)")
    filas = list(itertools.product(*VALORES.values()))
    c.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", [(i,) + f for i, f in enumerate(filas)])
    r = [x[0] for x in c.execute(f"SELECT {a_sqlite(sql)} FROM t ORDER BY i").fetchall()]
    return list(zip(filas, r))


# ------------------------------------------------ el evaluador de referencia (aparte del traductor)

NOMBRES = {"Prom": "prom", "DEL": "dias", "Prenada": "pren", "Rodeo": "rodeo", "Fecha": "fecha"}


def _valor(n, fila):
    if n["t"] == "prop":
        nombre = n["path"].split(">")[-1]
        v = fila[NOMBRES[nombre]]
        return ("Sí" if v == 1 else "No") if nombre == "Prenada" else v
    v = n["v"]
    return ("Sí" if v else "No") if n["k"] == "bool" else v


def _cond(n, fila):
    t = n["t"]
    if t in ("and", "or"):
        vs = [_cond(x, fila) for x in n["items"]]
        corta = (t == "or")
        if corta in vs:
            return corta
        return None if None in vs else not corta
    if t == "not":
        v = _cond(n["x"], fila)
        return None if v is None else not v
    if t == "cmp":
        a, b = _valor(n["izq"], fila), _valor(n["der"], fila)
        if a is None or b is None:
            return None
        return {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[n["op"]]
    if t == "in":
        a = _valor(n["x"], fila)
        return None if a is None else a in [_valor(v, fila) for v in n["valores"]]
    if t == "between":
        x, a, b = (_valor(n[k], fila) for k in ("x", "a", "b"))
        return None if x is None else a <= x <= b
    if t == "isnull":
        return (_valor(n["x"], fila) is None) != n["negado"]
    if t == "prop":
        return _valor(n, fila) == "Sí"
    raise AssertionError(t)


def _resultado(n, fila):
    if n["t"] == "func":
        return evaluar(n, fila)
    if n["k"] == "null":
        return None
    if n["k"] == "bool":
        return "Sí" if n["v"] else "No"
    if n["k"] == "num":
        return format(float(n["v"]), ".10g")
    return n["v"]


def evaluar(n, fila):
    a = n["args"]
    for i in range(0, len(a) - 1, 2):
        if _cond(a[i], fila) is True:
            return _resultado(a[i + 1], fila)
    return _resultado(a[-1], fila)


class TestDanLoMismoQueLaReglaLeidaAMano(unittest.TestCase):
    def test_cada_formula_en_cientos_de_combinaciones_con_vacios(self):
        for formula in FORMULAS:
            sql, usados, _ = rg.traducir(formula, CAMPOS)
            arbol = dl.analizar(formula)
            filas = en_sqlite(sql)
            self.assertGreater(len(filas), 1000)
            for valores, obtenido in filas:
                fila = dict(zip(VALORES, valores))
                self.assertEqual(obtenido, evaluar(arbol, fila), f"{formula}\n{fila}")
            self.assertTrue(usados, formula)

    def test_la_comparacion_con_el_evaluador_detecta_un_error_a_proposito(self):
        """La prueba anterior no puede pasar siempre: si el SQL cambia un «≤» por un «<», difiere."""
        formula = FORMULAS[1]
        sql, _, _ = rg.traducir(formula, CAMPOS)
        roto = sql.replace("t.prom <= 40", "t.prom < 40")
        self.assertNotEqual(roto, sql)
        arbol = dl.analizar(formula)
        self.assertTrue(any(obtenido != evaluar(arbol, dict(zip(VALORES, v))) for v, obtenido in en_sqlite(roto)))


class TestLoQueDevuelve(unittest.TestCase):
    def test_los_campos_usados_sin_repetir_y_los_resultados_posibles(self):
        sql, usados, resultados = rg.traducir("Iif([Prom] > 40 And [DEL] < 10, 'alta', [Prom] > 20, 'media', 'baja')", CAMPOS)
        self.assertEqual(usados, ["Prom", "DEL"])
        self.assertEqual(resultados, ["alta", "media", "baja"])
        self.assertEqual(sql, "CASE WHEN (t.prom > 40 AND t.dias < 10) THEN N'alta' WHEN t.prom > 20 THEN N'media' ELSE N'baja' END")

    def test_los_resultados_si_no_son_un_si_no(self):
        _, _, resultados = rg.traducir("Iif([Prom] > 40, True, False)", CAMPOS)
        self.assertEqual(resultados, ["Sí", "No"])

    def test_las_comillas_de_un_texto_se_escapan(self):
        sql, _, resultados = rg.traducir("Iif([Rodeo] = 'O''Higgins', 'de O''Higgins', 'no')", CAMPOS)
        self.assertIn("t.rodeo = N'O''Higgins'", sql)
        self.assertIn("THEN N'de O''Higgins'", sql)
        self.assertEqual(resultados[0], "de O'Higgins")

    def test_todo_sin_resultado_sigue_siendo_sql_valido(self):
        sql, _, _ = rg.traducir("Iif([Prom] > 1, null, null)", CAMPOS)
        self.assertEqual({v for _, v in en_sqlite(sql)}, {None})


class TestConElCatalogoDeVerdad(unittest.TestCase):
    def test_los_nombres_como_los_escribe_delpro(self):
        formula = ("Iif([<AnimalExtended>ReproductionInfo.IsPregnant] = True And [<AnimalExtended>ReproductionInfo.DaysToCalving] <= 45, 'cerca', "
                   "[<AnimalExtended>InMilk] = True And [<AnimalExtended>CMSTwentyOneDayAverage] >= 40.0f, 'ordeño', '')")
        sql, usados, _ = rg.traducir(formula, cat.CATALOGO)
        self.assertEqual(usados, ["IsPregnant", "DaysToCalving", "InMilk", "CMSTwentyOneDayAverage"])
        db.validate_sql("SELECT " + sql)

    def test_los_dias_en_ordeno_sin_lactancia_valen_cero_dentro_de_una_regla_como_en_delpro(self):
        sql, _, _ = rg.traducir("Iif([<AnimalExtended>CurrentDIM] <= 20, 'fresca', '')", cat.CATALOGO)
        dim = cat.CATALOGO["CurrentDIM"].expr
        self.assertEqual(sql, f"CASE WHEN (CASE WHEN r.LastLactationChangeDate IS NULL THEN 0 ELSE ({dim}) END) <= 20 THEN N'fresca' ELSE N'' END")


class TestLoQueNoSeAcepta(unittest.TestCase):
    def rechaza(self, formula, parte, campos=CAMPOS):
        with self.assertRaises(rg.ReglasError) as e:
            rg.traducir(formula, campos)
        self.assertIn(parte, str(e.exception), formula)

    def test_que_no_sea_un_iif(self):
        self.rechaza("[Prom] > 40", "Iif(")
        self.rechaza("", "Faltan las reglas")
        self.rechaza(None, "Faltan las reglas")
        self.rechaza("Iif([Prom] > 40, 'a')", "resultado final")
        self.rechaza("Iif([Prom] > 40, 'a', [Prom] > 20, 'b')", "resultado final")

    def test_campos_que_no_existen(self):
        self.rechaza("Iif([Peso] > 40, 'a', 'b')", "«Peso» todavía no existe")
        self.rechaza("Iif([Prom] > 40 And [<AnimalExtended>ReproductionInfo.Otro] = 1, 'a', 'b')", "todavía no existe")

    def test_lo_que_no_se_calcula_todavia(self):
        self.rechaza("Iif(Contains([Rodeo], '1'), 'a', 'b')", "Contains")
        self.rechaza("Iif([Prom] + [DEL] > 40, 'a', 'b')", "cuentas")
        self.rechaza("Iif([Prom] > [DEL], 'a', 'b')", "valor fijo")
        self.rechaza("Iif([Prom], 'a', 'b')", "sí/no")
        self.rechaza("Iif([Prom] > 40, [DEL], 'b')", "resultado")
        self.rechaza("Iif([Prom] > 40, Upper('a'), 'b')", "resultado")
        self.rechaza("Iif([Prom] Like 'a%', 'a', 'b')", "parecido")
        self.rechaza("Iif(Not [Prom] > 1 And ?x, 'a', 'b')", "sí/no")

    def test_comparaciones_de_otro_tipo(self):
        self.rechaza("Iif([Prenada] = 1, 'a', 'b')", "verdadero o falso")
        self.rechaza("Iif([Prom] = 'a', 'a', 'b')", "tipo")
        self.rechaza("Iif([Rodeo] < 'a', 'a', 'b')", "igual")
        self.rechaza("Iif([Fecha] In (#2026-01-01#), 'a', 'b')", "es uno de")

    def test_textos_que_romperian_la_consulta(self):
        self.rechaza("Iif([Prom] > 1, 'a;b', 'c')", "no se pueden usar")
        self.rechaza("Iif([Prom] > 1, 'a--b', 'c')", "no se pueden usar")
        self.rechaza("Iif([Rodeo] = 'x/*', 'a', 'c')", "no se pueden usar")
        self.rechaza("Iif([Prom] > 1, '@HOY', 'c')", "@")
        self.rechaza("Iif([Rodeo] = '@HOY', 'a', 'c')", "@")
        self.rechaza("Iif([Prom] > 1, '" + "x" * 61 + "', 'c')", "largo")

    def test_limites(self):
        self.rechaza("Iif([Prom] > 1, '" + "a', [Prom] > 1, '" * 600 + "b', 'c')", "demasiado larg")
        anidada = "'x'"
        for _ in range(rg.ANIDADO_MAX + 1):
            anidada = f"Iif([Prom] > 1, {anidada}, 'y')"
        self.rechaza(anidada, "dentro de otro")
        rg.traducir("Iif([Prom] > 1, 'a', 'b')", CAMPOS)                                        # el límite no molesta a una regla común

    def test_una_regla_larga_como_las_del_tambo_entra(self):
        formula = "Iif(" + ", ".join(f"[Prom] > {i} And [DEL] < {i * 3} And Not [Prenada] = True, 'grupo {i}'" for i in range(1, 80)) + ", 'ninguno')"
        self.assertGreater(len(formula), 4000)
        rg.traducir(formula, CAMPOS)


if __name__ == "__main__":
    unittest.main()
