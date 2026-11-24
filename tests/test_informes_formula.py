"""Fórmulas de los campos calculados (informes_formula.traducir): una cuenta escrita por el usuario con campos entre corchetes
([SevenDayAverage] * 7) se traduce a una expresión SQL.

Es la única parte del sistema donde un texto del usuario se vuelve SQL, así que lo que importa es que SOLO puedan salir de acá
números, los campos del catálogo, + - * / y paréntesis: nada de nombres sueltos, comillas ni punto y coma. Cada campo entra como
float (un `int / int` de SQL Server divide en enteros: 7 / 2 = 3) y cada división protege contra el cero.

    C:\\Python314\\python.exe -m unittest tests.test_informes_formula -v
"""
import random
import re
import unittest

import informes_formula as fo
from informes_catalogo import Campo

CAMPOS = {
    "A": Campo("A", "Producción", "X", "num", "t.a"),
    "B": Campo("B", "Días", "X", "num", "t.b"),
    "T": Campo("T", "Nombre", "X", "txt", "t.t"),
    "F": Campo("F", "Fecha", "X", "fecha", "t.f"),
}
FA = "CAST((t.a) AS float)"
FB = "CAST((t.b) AS float)"


def sql(formula):
    return fo.traducir(formula, CAMPOS)[0]


class TestTraducir(unittest.TestCase):
    def test_un_campo(self):
        self.assertEqual(fo.traducir("[A]", CAMPOS), (FA, ["A"]))

    def test_las_constantes_son_float_para_que_100_sobre_7_no_de_14(self):
        self.assertEqual(sql("[A] * 100"), f"({FA} * CAST(100 AS float))")
        self.assertIn("CAST(0.5 AS float)", sql("[A] * 0,5"))             # coma decimal
        self.assertIn("CAST(0.5 AS float)", sql("[A] * 0.5"))

    def test_precedencia(self):
        self.assertEqual(sql("[A] + [B] * 2"), f"({FA} + ({FB} * CAST(2 AS float)))")
        self.assertEqual(sql("([A] + [B]) * 2"), f"(({FA} + {FB}) * CAST(2 AS float))")

    def test_asociatividad_a_la_izquierda(self):
        self.assertEqual(sql("[A] - [B] - 1"), f"(({FA} - {FB}) - CAST(1 AS float))")
        self.assertEqual(sql("[A] / [B] / 2"), f"(({FA} / NULLIF({FB}, 0)) / NULLIF(CAST(2 AS float), 0))")

    def test_la_division_no_explota_con_cero(self):
        self.assertEqual(sql("[A] / [B]"), f"({FA} / NULLIF({FB}, 0))")
        self.assertIn("NULLIF(CAST(0 AS float), 0)", sql("[A] / 0"))

    def test_menos_unario(self):
        self.assertEqual(sql("-[A]"), f"(-{FA})")
        self.assertEqual(sql("[A] - -[B]"), f"({FA} - (-{FB}))")
        self.assertEqual(sql("-(1 + [A])"), f"(-(CAST(1 AS float) + {FA}))")

    def test_los_simbolos_de_la_pantalla(self):
        self.assertEqual(sql("[A] × [B] ÷ 2 − 1"), sql("[A] * [B] / 2 - 1"))

    def test_los_espacios_no_importan(self):
        self.assertEqual(sql("[A]*[B]"), sql("  [A]   *   [B] "))

    def test_dos_menos_seguidos_son_una_resta_de_un_negativo_y_no_un_comentario(self):
        out = sql("[A] -- 1")
        self.assertEqual(out, f"({FA} - (-CAST(1 AS float)))")
        self.assertNotIn("--", out)

    def test_campos_usados_sin_repetir_y_en_orden(self):
        self.assertEqual(fo.traducir("[B] + [A] * [B]", CAMPOS)[1], ["B", "A"])


class TestRechazos(unittest.TestCase):
    def malo(self, formula, mensaje=None):
        with self.assertRaises(fo.FormulaError) as cm:
            fo.traducir(formula, CAMPOS)
        if mensaje:
            self.assertIn(mensaje, str(cm.exception))

    def test_vacia(self):
        for f in ("", "   ", None, 5):
            self.malo(f)

    def test_campo_que_no_existe_o_no_es_numerico(self):
        self.malo("[Nada] + 1", "Nada")
        self.malo("[T] + 1", "Nombre")
        self.malo("[F] + 1", "Fecha")

    def test_hace_falta_al_menos_un_campo(self):
        self.malo("2 * 3", "campo")

    def test_caracteres_que_no_son_de_una_cuenta(self):
        for f in ("[A]; DROP TABLE x", "[A] + 'x'", "[A] /* x */", "SELECT 1", "abc", "[A] + b", "[A]x", "[a b]",
                  "[A] % 2", "[A] ^ 2", "[A] + @x", "[A] = 1", "[A] + 1e5", "[A] + \x00", "[A]\n+ 1", "[A] & [B]", "[A] , [B]",
                  "{A}", "[A] + [B", "[A + B]", "[]", "[ A ]"):
            self.malo(f)

    def test_estructura(self):
        for f in ("([A]", "[A])", "()", "[A] +", "* [A]", "[A] [B]", "[A] ++ [B]", "[A] * / [B]", "[A] + (", "+ [A]", "[A] 2"):
            self.malo(f)

    def test_demasiado_larga_o_anidada_o_con_numeros_enormes(self):
        self.malo("[A] + " + "1 + " * 200 + "1")
        self.malo("(" * 80 + "[A]" + ")" * 80)
        self.malo("[A] * 99999999999999999999")
        self.malo("[A] * 1.1234567890123456789")

    def test_el_mensaje_no_repite_el_texto_peligroso_sin_limite(self):
        try:
            fo.traducir("[A] + " + "x" * 500, CAMPOS)
        except fo.FormulaError as e:
            self.assertLess(len(str(e)), 300)


class TestSoloSalenPiezasPermitidas(unittest.TestCase):
    """Con textos al azar: o es un error de fórmula, o el SQL está hecho únicamente de las piezas permitidas."""
    PERMITIDO = re.compile(r"(?:\s|CAST\(|AS float|NULLIF\(|\(|\)|,|\d+(?:\.\d+)?|t\.a|t\.b|[+\-*/])*")

    @staticmethod
    def valida(rnd, d=0):
        r = rnd.random()
        if d > 3 or r < .35:
            return rnd.choice(["[A]", "[B]", str(rnd.randint(0, 99)), f"{rnd.randint(0, 9)},{rnd.randint(0, 99)}"])
        if r < .5:
            return "(" + TestSoloSalenPiezasPermitidas.valida(rnd, d + 1) + ")"
        if r < .6:
            return "-" + TestSoloSalenPiezasPermitidas.valida(rnd, d + 1)
        return (TestSoloSalenPiezasPermitidas.valida(rnd, d + 1) + " " + rnd.choice("+-*/×÷") + " "
                + TestSoloSalenPiezasPermitidas.valida(rnd, d + 1))

    def revisar(self, f):
        out, usados = fo.traducir(f, CAMPOS)
        self.assertRegex(out, self.PERMITIDO, f)
        self.assertNotIn("--", out, f)
        self.assertTrue(set(usados) <= {"A", "B"}, f)

    def test_texto_al_azar(self):
        rnd = random.Random(20261123)
        trozos = ["[A]", "[B]", "[T]", "[Z]", "1", "2,5", "0", " ", "+", "-", "*", "/", "(", ")", "×", "÷", "'", ";", "--", "/*", "x",
                  "SELECT", "[", "]", "e", ".", "%", "\n", "DROP", "="]
        for _ in range(3000):
            f = "".join(rnd.choice(trozos) for _ in range(rnd.randint(1, 14)))
            try:
                self.revisar(f)
            except fo.FormulaError:
                pass

    def test_cuentas_validas_siempre_se_traducen(self):
        rnd = random.Random(7)
        for _ in range(1500):
            self.revisar("[A] + (" + self.valida(rnd) + ")")

    def test_cuentas_validas_con_un_estropicio(self):
        rnd = random.Random(11)
        junk = ["'", ";", "--", "/*", "x", "[", "]", "(", ")", "%", "\n", "DROP", "=", "[Z]", "[T]"]
        for _ in range(3000):
            f = "[A] + (" + self.valida(rnd) + ")"
            i = rnd.randint(0, len(f))
            f = f[:i] + rnd.choice(junk) + f[i + rnd.choice([0, 0, 1]):]
            try:
                self.revisar(f)
            except fo.FormulaError:
                pass


if __name__ == "__main__":
    unittest.main()
