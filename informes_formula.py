"""Fórmulas de los campos calculados de los informes.

El usuario escribe una cuenta con campos del catálogo entre corchetes —`[SevenDayAverage] * ([Fat] + [Protein]) / 100`— y esto la
traduce a una expresión SQL. Es la ÚNICA parte del sistema donde un texto escrito por el usuario se vuelve SQL, así que se lee con un
analizador propio (no con `eval`, ni reemplazando texto) y de acá solo pueden salir:

  * números (validados y reescritos, nunca copiados tal cual),
  * los campos del catálogo (su expresión SQL ya conocida),
  * + - * / y paréntesis.

Dos cuidados de SQL Server que se resuelven acá y no se le dejan al usuario:
  * cada campo y cada número entra como float: `7 / 2` entre enteros da 3 y no 3,5;
  * cada división protege contra el cero (`NULLIF(divisor, 0)`): da «sin dato», no un error que tire el informe entero.
"""
import re
from decimal import Decimal

MAX_LARGO = 300
MAX_PIEZAS = 120
MAX_ANIDADO = 20
MAX_DIGITOS = 12
MAX_DECIMALES = 6

_REF = re.compile(r"\[([A-Za-z][A-Za-z0-9_]{0,60})\]")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_OPERADORES = {"+": "+", "-": "-", "*": "*", "/": "/", "(": "(", ")": ")", "×": "*", "÷": "/", "−": "-"}


class FormulaError(ValueError):
    """La fórmula no se puede usar. El mensaje se le muestra al usuario."""


def _leer(formula: str) -> list:
    """Parte la fórmula en piezas: ("num", "2,5") ("ref", "SevenDayAverage") ("op", "+")."""
    piezas, i, n = [], 0, len(formula)
    while i < n:
        c = formula[i]
        if c in " \t":
            i += 1
            continue
        if c in _OPERADORES:
            piezas.append(("op", _OPERADORES[c]))
            i += 1
        elif c == "[":
            m = _REF.match(formula, i)
            if not m:
                raise FormulaError("Un campo se escribe entre corchetes, con su nombre exacto: [SevenDayAverage].")
            piezas.append(("ref", m.group(1)))
            i = m.end()
        elif c.isdigit():
            m = _NUM.match(formula, i)
            piezas.append(("num", m.group(0)))
            i = m.end()
        else:
            visto = c if c.isprintable() else "un carácter no permitido"
            raise FormulaError(f"No entiendo «{visto}» en la fórmula: solo se pueden usar números, campos entre corchetes, + − × ÷ y paréntesis.")
        if len(piezas) > MAX_PIEZAS:
            raise FormulaError("La fórmula es demasiado larga.")
    return piezas


def _numero(texto: str) -> str:
    entero, _, dec = texto.replace(",", ".").partition(".")
    if len(entero.lstrip("0") or "0") + len(dec) > MAX_DIGITOS or len(dec) > MAX_DECIMALES:
        raise FormulaError(f"El número {texto[:20]} es demasiado grande o tiene demasiados decimales.")
    return format(Decimal(texto.replace(",", ".")).normalize(), "f")


def traducir(formula, campos: dict):
    """Devuelve (sql, ids de los campos usados, sin repetir y en el orden en que aparecen).

    `campos`: {id: Campo} de los campos que se pueden nombrar (solo los numéricos sirven en una cuenta)."""
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError("Escribí una fórmula.")
    if len(formula) > MAX_LARGO:
        raise FormulaError(f"La fórmula es demasiado larga (hasta {MAX_LARGO} caracteres).")
    piezas = _leer(formula)
    usados: list = []
    pos = [0]

    def mirar():
        return piezas[pos[0]] if pos[0] < len(piezas) else (None, None)

    def tomar():
        p = mirar()
        pos[0] += 1
        return p

    def expresion(nivel):
        if nivel > MAX_ANIDADO:
            raise FormulaError("La fórmula tiene demasiados paréntesis anidados.")
        izq = termino(nivel)
        while mirar() in (("op", "+"), ("op", "-")):
            op = tomar()[1]
            izq = f"({izq} {op} {termino(nivel)})"
        return izq

    def termino(nivel):
        izq = factor(nivel)
        while mirar() in (("op", "*"), ("op", "/")):
            op = tomar()[1]
            der = factor(nivel)
            izq = f"({izq} * {der})" if op == "*" else f"({izq} / NULLIF({der}, 0))"
        return izq

    def factor(nivel):
        tipo, valor = tomar()
        if tipo == "num":
            return f"CAST({_numero(valor)} AS float)"
        if tipo == "ref":
            f = campos.get(valor)
            if f is None:
                raise FormulaError(f"El campo [{valor}] no existe en el catálogo.")
            if f.tipo != "num":
                raise FormulaError(f"El campo «{f.etiqueta}» no es numérico: no se puede usar en una cuenta.")
            if valor not in usados:
                usados.append(valor)
            return f"CAST(({f.expr}) AS float)"
        if (tipo, valor) == ("op", "-"):
            return f"(-{factor(nivel)})"
        if (tipo, valor) == ("op", "("):
            dentro = expresion(nivel + 1)
            if tomar() != ("op", ")"):
                raise FormulaError("Falta cerrar un paréntesis.")
            return dentro
        if tipo is None:
            raise FormulaError("La fórmula está incompleta: falta un número o un campo al final.")
        raise FormulaError(f"No se esperaba «{valor}» en ese lugar de la fórmula.")

    sql = expresion(0)
    if pos[0] < len(piezas):
        tipo, valor = piezas[pos[0]]
        if (tipo, valor) == ("op", ")"):
            raise FormulaError("Sobra un paréntesis que cierra.")
        raise FormulaError("Falta un operador (+ − × ÷) entre dos partes de la fórmula.")
    if not usados:
        raise FormulaError("La fórmula tiene que usar al menos un campo, por ejemplo [SevenDayAverage].")
    return sql, usados
