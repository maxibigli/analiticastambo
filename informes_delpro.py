"""Traer a LactIA los informes que el tambo ya armó en DelPro (Fase 4a de los informes personalizables).

DelPro guarda su propio diseñador de informes dentro de DDM, así que los informes del tambo se pueden LEER (nunca se escribe nada):

    DDMReport              un informe (o una carpeta: el tipo sale de `XPObjectType.TypeName`, no del OID, que cambia de una base a otra)
    DDMReportColumn        una columna del informe: orden (`Index`), grupo (`GroupIndex`), orden de la lista (`SortDirection`, `SortIndex`),
                           resúmenes (`SummaryType`, `GroupSummaryType`, `SummaryTypes`) y filtro propio (`FilterString`)
    DDMReportItemInstance  → DDMReportItem: qué campo es la columna (`DDMReportItem.Name`; los ids del catálogo de LactIA son esos mismos)
    Filter                 el filtro de servidor del informe (`DDMReport.ServerFilter`, texto en el lenguaje de criterios de DevExpress)

`DDMReport.CustomFilter` es el filtro de la grilla: llama a cada columna `C` + su OID (`[C2511]`), comprobado contra la base. Los
códigos de resumen son los `SummaryItemType` de DevExpress: 0 suma, 1 mínimo, 2 máximo, 3 conteo, 4 promedio, 5/6 ninguno; sobre los campos reales
tienen sentido (el conteo cae en `Number`, el promedio en los días en ordeño y las producciones).

LO QUE NO SE ADIVINA: de cada informe se trae lo que el catálogo de LactIA (`informes_catalogo`) sabe calcular y lo demás se DICE, con el motivo.
Un campo que no existe acá no se inventa, y un filtro que no se puede traer cambia QUÉ animales aparecen, así que se avisa distinto
(gravedad «contenido») que una columna que falta. Solo se traen informes de animales (`BasicAnimal` y `AnimalExtended`).

Los textos de DelPro son DATOS: pasan por un traductor propio del lenguaje de criterios (sin `eval`, sin SQL) que solo entiende una conjunción
de comparaciones simples sobre campos del catálogo, y cada condición que sale se valida con `informes_motor.compilar`: lo que no aguanta esa
validación (un valor con `;`, `--`, `/*`) se descarta y se avisa. La definición que devuelve `traducir_informe` SIEMPRE se puede correr.
"""
import datetime
import logging
import math
import re
import threading
import time
import unicodedata

import informes_catalogo
import informes_motor

log = logging.getLogger(__name__)

TTL_CACHE_S = 600
NOMBRE_MAX = 60                                  # igual que `informes_guardados.NOMBRE_MAX`
FALTANTES_TOP = 40
TEXTO_FILTRO_MAX = 4000
PROFUNDIDAD_MAX = 40
MUESTRA_MAX = 8                                  # cuántos nombres se listan en un aviso antes de decir «y N más»

MAX_OPERACIONES = 200                            # cuentas (+ - * /) seguidas que acepta el lector: más es un filtro roto, no una cuenta
CLASES_DE_ANIMAL = {"BasicAnimal": "Animal", "AnimalExtended": "Animal extendido"}
# Cómo se llaman en castellano los campos de DelPro que más faltan: se muestran como «En ordeño (InMilk)», para que se reconozcan. Solo los que no tienen duda.
NOMBRE_EN_DELPRO = {"InMilk": "En ordeño", "ToBeCulled": "Para descarte", "IsPregnant": "Preñada", "IsDryingOff": "En secado", "IsExited": "Ya salió del tambo", "GroupNumber": "Número del rodeo",
                    "Sex": "Sexo", "NumberOfInseminations": "Cantidad de inseminaciones", "DaysToCalving": "Días para el parto",
                    "ExpectedCalvingDate": "Fecha esperada de parto", "DaysToDryOff": "Días para el secado", "ExpectedDryOffDate": "Fecha esperada de secado",
                    "DaysSinceLastInsemination": "Días desde la última inseminación", "LastInseminationDate": "Fecha de la última inseminación",
                    "BirthDate": "Fecha de nacimiento", "ExitDate": "Fecha de salida", "DaysDry": "Días en seca", "LastAbortionDate": "Fecha del último aborto",
                    "AgeInMonths": "Edad en meses", "LatestSCC": "Último recuento de células somáticas"}
# Una ruta de DelPro que es EXACTAMENTE un campo del catálogo (misma columna de la base): se traduce sin dudar.
ALIAS_DE_RUTAS = {"ReproductionInfo.LactationNumber": "CurrentLactationNumber"}
# Las propiedades de la reproducción que DelPro usa en sus filtros (`ReproductionInfo.IsPregnant = True`) y que el catálogo tiene con el MISMO nombre y el mismo dato: columnas de la
# reproducción o cuentas sobre ella. `ReproductionInfo.LastCalvingDate`, en cambio, NO es la «Fecha del último parto» de acá (ver `_campo_de`).
ALIAS_DE_RUTAS.update({"ReproductionInfo." + n: n for n in ("IsPregnant", "IsDryingOff", "DaysToCalving", "DaysToDryOff", "NumberOfInseminations", "DaysSinceLastInsemination",
                                                              "ExpectedCalvingDate", "ExpectedDryOffDate", "LastInseminationDate", "LastAbortionDate",
                                                              "DaysPregnant", "EffectiveInseminationDate", "LastPregnancyCheckDate", "IsBred")})
NOMBRES_DE_NIVEL = {"SessionMilkYield": "Ordeño por sesión", "AnimalGroup": "Rodeos", "DiagnosisTreatmentEvent": "Diagnósticos y tratamientos",
                    "BcsDailyData": "Condición corporal diaria", "RationCalculationLog": "Cálculo de ración",
                    "AnimalFeedDailyConsumption": "Consumo diario de alimento", "AnimalFeedConsumed": "Alimento consumido",
                    "AnimalLactationSummary": "Resumen de lactancia", "ServiceTimer": "Servicios", "CMSMilkSamplingEvent": "Muestreo de leche",
                    "SCNGSortResultEvent": "Resultado de separación"}
RESUMEN_DE_CODIGO = {0: "sum", 1: "min", 2: "max", 3: "count", 4: "avg"}
SIN_RESUMEN = (None, 5, 6)
ORDEN_DE_ESTADO = {"completo": 0, "parcial": 1, "no_disponible": 2}

# Cuatro lecturas fijas: no entra nada del usuario ni de DelPro en el SQL.
CONSULTAS = {
    "informes": ("SELECT r.OID, r.Name, r.ParentFolder, r.IsPredefined, r.IsHidden, x.TypeName, r.IncludeCulledAndExitedAnimals, r.ReportClass, "
                 "r.ServerFilter, r.CustomFilter, r.Description, r.Herd, "
                 "CASE WHEN r.FormatRule IS NULL OR DATALENGTH(r.FormatRule) = 0 THEN 0 ELSE 1 END AS ConColores "      # si tiene reglas de color, no el XML de las reglas
                 "FROM DDMReport r LEFT JOIN XPObjectType x ON x.OID = r.ObjectType WHERE r.GCRecord IS NULL"),
    "columnas": ("SELECT c.OID, c.Report, c.[Index], it.Name AS item, c.GroupIndex, c.SortDirection, c.SortIndex, c.SummaryType, "
                 "c.GroupSummaryType, c.SummaryTypes, c.FilterString "
                 "FROM DDMReportColumn c JOIN DDMReportItemInstance ii ON ii.OID = c.OID AND ii.GCRecord IS NULL "
                 "JOIN DDMReportItem it ON it.OID = ii.ReportItem AND it.GCRecord IS NULL "
                 "JOIN DDMReport r ON r.OID = c.Report AND r.GCRecord IS NULL"),
    "filtros": "SELECT f.OID, f.Name, f.FilterText FROM Filter f WHERE f.GCRecord IS NULL",
    "clases": "SELECT c.OID, x.TypeName FROM DDMReportClass c LEFT JOIN XPObjectType x ON x.OID = c.ClassType",
}


class CriterioError(ValueError):
    """Un texto de filtro de DelPro que este traductor no sabe leer."""


class DelProNoDisponible(RuntimeError):
    """No se pudieron leer los informes de DelPro de este tambo. El mensaje se le muestra al usuario (sin detalles del servidor).
    `vacio`: el tambo simplemente no tiene informes de DelPro (no es una falla): la pantalla lo dice como un estado vacío y no como un error."""

    def __init__(self, mensaje, vacio=False):
        super().__init__(mensaje)
        self.vacio = vacio


class InformeNoEncontrado(KeyError):
    """El número no es el de un informe de DelPro (no existe, o es una carpeta). Es un `KeyError` a propósito: así se distingue de un error de la traducción."""


class _NoTraducible(Exception):
    """Un término del filtro que acá no existe. El mensaje es el motivo, en palabras."""


# ================================================================================================ criterios de DelPro

_TOKENS = re.compile(r"""
    (?P<esp>\s+)
  | (?P<enum>\#\#Enum\#[^#]*\#)
  | (?P<fecha>\#[^#]*\#)
  | (?P<cad>'(?:[^']|'')*')
  | (?P<prop>\[[^\]]*\])
  | (?P<num>[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?P<suf>[A-Za-z]{0,2})(?![A-Za-z0-9_])
  | (?P<op><>|<=|>=|==|!=|&&|\|\||[=<>+\-*/%!(),?])
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
""", re.X)
_PALABRAS = {"and", "or", "not", "in", "between", "is", "null", "like", "true", "false"}
_SUFIJOS = {"", "b", "s", "i", "l", "u", "f", "d", "m", "ub", "us", "ui", "ul", "sb"}
_SUFIJOS_DECIMALES = {"f", "d", "m"}
_COMPARADORES = {"=": "=", "==": "=", "<>": "<>", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_VUELTA = {"=": "=", "<>": "<>", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
_FECHA_LIT = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.0+)?)?$")      # solo dígitos ASCII (un «٢٠٢٦» no es un año)
_HORA_EN_FECHA = re.compile(r"[0-9]{1,2}:[0-9]{2}")
_ID_COLUMNA = re.compile(r"^(UN-)?C\d+$")


def _tokenizar(texto: str) -> list:
    if len(texto) > TEXTO_FILTRO_MAX:
        raise CriterioError("el filtro es demasiado largo")
    out, pos = [], 0
    while pos < len(texto):
        m = _TOKENS.match(texto, pos)
        if m is None:
            raise CriterioError(f"no se entiende «{texto[pos:pos + 12]}»")
        k = m.lastgroup if m.lastgroup != "suf" else "num"
        if m.group("esp") is None:
            if m.group("num") is not None:
                if m.group("suf").lower() not in _SUFIJOS:
                    raise CriterioError(f"número con un sufijo desconocido: «{m.group(0)}»")
                out.append(("num", (m.group("num"), m.group("suf").lower()), m.start(), m.end()))
            elif m.group("id") is not None:
                palabra = m.group("id")
                es_palabra = "." not in palabra and palabra.lower() in _PALABRAS
                out.append(("kw" if es_palabra else "id", palabra.lower() if es_palabra else palabra, m.start(), m.end()))
            else:
                out.append((k, m.group(k), m.start(), m.end()))
        pos = m.end()
    return out


def _n(t, ini, fin, **kw) -> dict:
    d = {"t": t, "ini": ini, "fin": fin}
    d.update(kw)
    return d


def _fecha_de(texto: str):
    """`#2026-01-07#` -> '2026-01-07'. Con hora distinta de 00:00:00 (o en otro formato) no se puede representar: None."""
    m = _FECHA_LIT.match(texto.strip())
    if not m:
        return None
    if m.group(4) is not None and (m.group(4), m.group(5), m.group(6)) != ("00", "00", "00"):
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


class _Lector:
    """Lee el lenguaje de criterios de DevExpress (el que usa DelPro para sus filtros): un árbol, sin evaluar nada."""

    def __init__(self, texto):
        self.texto = texto
        self.tok = _tokenizar(texto)
        self.i = 0
        self.nivel = 0
        self.operaciones = 0                                    # las cuentas de TODO el filtro (no de cada paréntesis): ver `suma`

    # --- utilidades
    def ver(self, k=0):
        return self.tok[self.i + k] if self.i + k < len(self.tok) else None

    def tomar(self):
        t = self.ver()
        if t is None:
            raise CriterioError("el filtro termina antes de tiempo")
        self.i += 1
        return t

    def es_kw(self, *nombres, k=0):
        t = self.ver(k)
        return t is not None and t[0] == "kw" and t[1] in nombres

    def es_op(self, *ops, k=0):
        t = self.ver(k)
        return t is not None and t[0] == "op" and t[1] in ops

    def esperar_op(self, op):
        t = self.tomar()
        if t[0] != "op" or t[1] != op:
            raise CriterioError(f"falta «{op}»")
        return t

    def bajar(self):
        self.nivel += 1
        if self.nivel > PROFUNDIDAD_MAX:
            raise CriterioError("el filtro tiene demasiados niveles")

    def subir(self):
        self.nivel -= 1

    # --- gramática (de menor a mayor precedencia: o, y, no, comparación, suma, unario, primario)
    def leer(self):
        if not self.tok:
            raise CriterioError("el filtro está vacío")
        n = self.o()
        if self.ver() is not None:
            raise CriterioError("sobra texto al final del filtro")
        return n

    def o(self):
        partes = [self.y()]
        while self.es_kw("or") or self.es_op("||"):
            self.tomar()
            partes.append(self.y())
        return partes[0] if len(partes) == 1 else _n("or", partes[0]["ini"], partes[-1]["fin"], items=partes)

    def y(self):
        partes = [self.no()]
        while self.es_kw("and") or self.es_op("&&"):
            self.tomar()
            partes.append(self.no())
        return partes[0] if len(partes) == 1 else _n("and", partes[0]["ini"], partes[-1]["fin"], items=partes)

    def no(self):
        if self.es_kw("not") or self.es_op("!"):
            t = self.tomar()
            self.bajar()
            x = self.no()
            self.subir()
            return _n("not", t[2], x["fin"], x=x)
        return self.comparacion()

    def comparacion(self):
        izq = self.suma()
        t = self.ver()
        if t is None:
            return izq
        if t[0] == "op" and t[1] in _COMPARADORES:
            self.tomar()
            der = self.suma()
            return _n("cmp", izq["ini"], der["fin"], op=_COMPARADORES[t[1]], izq=izq, der=der)
        if self.es_kw("like"):
            self.tomar()
            der = self.suma()
            return _n("like", izq["ini"], der["fin"], x=izq, patron=der)
        if self.es_kw("in"):
            self.tomar()
            self.esperar_op("(")
            valores = [self.suma()]
            while self.es_op(","):
                self.tomar()
                valores.append(self.suma())
            cierre = self.esperar_op(")")
            return _n("in", izq["ini"], cierre[3], x=izq, valores=valores)
        if self.es_kw("between"):
            self.tomar()
            self.esperar_op("(")
            a = self.suma()
            self.esperar_op(",")
            b = self.suma()
            cierre = self.esperar_op(")")
            return _n("between", izq["ini"], cierre[3], x=izq, a=a, b=b)
        if self.es_kw("is"):
            self.tomar()
            negado = False
            if self.es_kw("not"):
                self.tomar()
                negado = True
            if not self.es_kw("null"):
                raise CriterioError("después de «Is» tiene que venir «Null» o «Not Null»")
            return _n("isnull", izq["ini"], self.tomar()[3], x=izq, negado=negado)
        return izq

    def suma(self):
        izq = self.unario()
        while self.es_op("+", "-", "*", "/", "%"):
            # El tope es de TODO el filtro: contado por cada llamada, 10 grupos de 190 cuentas entre paréntesis (3,8 KB) pasaban y armaban un árbol de 1.900
            # niveles, que agotaba la pila al recorrerlo (`RecursionError`) y dejaba al informe entero «no se pudo leer» en vez de «parcial».
            self.operaciones += 1
            if self.operaciones > MAX_OPERACIONES:
                raise CriterioError("demasiadas cuentas en el filtro")
            op = self.tomar()
            der = self.unario()
            izq = _n("arit", izq["ini"], der["fin"], op=op[1], izq=izq, der=der)
        return izq

    def unario(self):
        if self.es_op("-"):
            t = self.tomar()
            self.bajar()
            x = self.unario()
            self.subir()
            if x["t"] == "lit" and x["k"] == "num":
                return _n("lit", t[2], x["fin"], k="num", v=-x["v"])
            return _n("arit", t[2], x["fin"], op="-", izq=None, der=x)
        return self.primario()

    def primario(self):
        t = self.tomar()
        k, v, ini, fin = t
        if k == "prop":
            return _n("prop", ini, fin, path=v[1:-1].strip())
        if k == "cad":
            return _n("lit", ini, fin, k="str", v=v[1:-1].replace("''", "'"))
        if k == "num":
            digitos, sufijo = v
            decimal = "." in digitos or "e" in digitos.lower() or sufijo in _SUFIJOS_DECIMALES
            x = float(digitos) if decimal else int(digitos)
            if isinstance(x, float):
                if not math.isfinite(x):
                    raise CriterioError("un número fuera de rango")
                if x == int(x) and abs(x) < 1e15:
                    x = int(x)
            elif abs(x) > 10 ** 300:
                raise CriterioError("un número fuera de rango")
            return _n("lit", ini, fin, k="num", v=x)
        if k == "fecha":
            return _n("lit", ini, fin, k="fecha", v=_fecha_de(v[1:-1]), raw=v[1:-1].strip())
        if k == "enum":
            cuerpo = v[len("##Enum#"):-1]
            return _n("lit", ini, fin, k="enum", v=cuerpo.rsplit(",", 1)[-1].strip())
        if k == "kw" and v in ("true", "false"):
            return _n("lit", ini, fin, k="bool", v=(v == "true"))
        if k == "kw" and v == "null":
            return _n("lit", ini, fin, k="null", v=None)
        if k == "op" and v == "(":
            self.bajar()
            n = self.o()
            self.subir()
            self.esperar_op(")")
            return n
        if k == "op" and v == "?":
            nxt = self.ver()
            if nxt is not None and nxt[0] == "id" and nxt[2] == fin:
                self.tomar()
            return _n("param", ini, fin)
        if k == "id" and not self.es_op("("):
            return _n("prop", ini, fin, path=v)                      # un campo escrito sin corchetes: `ReproductionInfo.IsDryingOff`
        if k == "id" or (k == "kw" and v in ("between", "in") and self.es_op("(")):
            nombre = v if k == "id" else v.capitalize()
            self.esperar_op("(")
            args = []
            if not self.es_op(")"):
                self.bajar()
                args.append(self.o())
                while self.es_op(","):
                    self.tomar()
                    args.append(self.o())
                self.subir()
            cierre = self.esperar_op(")")
            return _n("func", ini, cierre[3], nombre=nombre, args=args)
        raise CriterioError(f"no se entiende «{self.texto[ini:fin]}»")


def analizar(texto) -> dict:
    """El texto de un filtro de DelPro como árbol. `CriterioError` si no es algo que este traductor entienda."""
    if not isinstance(texto, str):
        raise CriterioError("el filtro no es un texto")
    try:
        return _Lector(texto).leer()
    except CriterioError:
        raise
    except (RecursionError, OverflowError, ValueError):
        raise CriterioError("el filtro es demasiado complejo") from None


def _sin_calificador(path: str) -> str:
    """La ruta de un campo sin el calificador de tipo (`<AnimalExtended>`). El patrón no puede cruzar un «<» ni un «>»: es lineal aun con miles de «<»."""
    return re.sub(r"<[^<>]*>", "", path[:300]).strip()


DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _fecha_legible(iso: str) -> str:
    """'2026-08-27' -> 'jue 27/08/2026': las fechas de los textos de la app llevan el día de la semana."""
    return f"{DIAS[datetime.date.fromisoformat(iso).weekday()]} {iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def _mostrar_literal(n) -> str:
    k, v = n["k"], n["v"]
    if k == "str":
        return "'" + v.replace("'", "''") + "'"
    if k == "num":
        return repr(v) if isinstance(v, float) else str(v)               # sin redondear: el umbral que se muestra es el de DelPro
    if k == "fecha":
        return _fecha_legible(v) if v else (n.get("raw") or "una fecha")
    if k == "bool":
        return "Verdadero" if v else "Falso"
    if k == "null":
        return "sin dato"
    return str(v)


def mostrar(nodo, nombre_de=None) -> str:
    """Un término de filtro de DelPro en palabras, para decirle al usuario QUÉ no se pudo traer. `nombre_de(ruta)` da el nombre a mostrar de
    un campo (por defecto, la ruta sin el calificador de tipo)."""
    nombre_de = nombre_de or _sin_calificador

    def m(n, ant=0) -> str:
        t = n["t"]
        if t == "prop":
            return nombre_de(n["path"])
        if t == "lit":
            return _mostrar_literal(n)
        if t == "cmp":
            return f"{m(n['izq'])} {n['op']} {m(n['der'])}"
        if t == "in":
            return f"{m(n['x'])} es uno de ({', '.join(m(x) for x in n['valores'])})"
        if t == "between":
            return f"{m(n['x'])} entre {m(n['a'])} y {m(n['b'])}"
        if t == "isnull":
            return f"{m(n['x'])} " + ("con dato" if n["negado"] else "sin dato")
        if t == "like":
            return f"{m(n['x'])} parecido a {m(n['patron'])}"
        if t == "func":
            if n["nombre"].lower() == "indaterange" and len(n["args"]) == 3:
                return f"{m(n['args'][0])} entre {m(n['args'][1])} y {m(n['args'][2])} (sin incluir el último día)"
            return f"{n['nombre']}({', '.join(m(a) for a in n['args'])})"
        if t == "and":
            return " y ".join(m(x, 1) for x in n["items"])
        if t == "or":
            s = " o ".join(m(x, 1) for x in n["items"])
            return f"({s})" if ant else s
        if t == "not":
            return f"no ({m(n['x'])})"
        if t == "arit":
            return _mostrar_cuenta(n, m)
        return "?"
    return m(nodo)


_PRECEDENCIA = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2}


def _mostrar_cuenta(n: dict, m) -> str:
    """Una cuenta con los paréntesis que hagan falta para que se lea igual que como se evalúa."""
    def hijo(h, derecho: bool) -> str:
        s = m(h)
        if h["t"] != "arit":
            return s
        if h["izq"] is None:                                        # un «menos» adelante: -(A + B)
            return s
        q, p = _PRECEDENCIA[h["op"]], _PRECEDENCIA[n["op"]]
        return f"({s})" if q < p or (q == p and derecho) else s
    if n["izq"] is None:
        der = m(n["der"])
        return f"-({der})" if n["der"]["t"] == "arit" and n["der"]["izq"] is not None else f"-{der}"
    return f"{hijo(n['izq'], False)} {n['op']} {hijo(n['der'], True)}"


# ================================================================================================ de un término a condiciones

def resolver_por_nombre(catalogo):
    """Para el filtro de servidor: los campos van por su nombre (`[<AnimalExtended>CurrentDIM]`). Devuelve (nombre, id del catálogo o None).
    Una ruta con puntos (`ReproductionInfo.IsPregnant`) no es un campo suelto: solo `ReproductionInfo.` se cuenta como un campo del animal."""
    def res(path):
        limpio = _sin_calificador(path)
        alias = ALIAS_DE_RUTAS.get(limpio)
        if alias in catalogo:
            return alias, alias
        if re.fullmatch(r"[A-Za-z_]\w*", limpio):
            return limpio, (limpio if limpio in catalogo else None)
        if limpio.startswith("ReproductionInfo.") and re.fullmatch(r"[A-Za-z_]\w*", limpio.split(".", 1)[1]):
            suelto = limpio.split(".", 1)[1]
            # Si el catálogo YA tiene un campo con ese nombre, el de la reproducción es otra definición (`_campo_de` lo explica): se queda con su ruta, para
            # que ni el aviso ni el ranking de «campos que faltan» digan que «LastCalvingDate todavía no existe acá».
            return (limpio if suelto in catalogo else suelto), None
        return limpio, None
    return res


def resolver_por_columnas(columnas, catalogo):
    """Para el filtro de la grilla (`CustomFilter`): cada columna se llama `C` + su OID. Devuelve (nombre del campo en DelPro, id del catálogo o None)."""
    por_oid = {c["OID"]: c["item"] for c in columnas}

    def res(path):
        limpio = _sin_calificador(path)
        m = re.fullmatch(r"(?:UN-)?C([0-9]+)", limpio)                              # DelPro también escribe `UN-C2971`: es la misma columna
        if m and int(m.group(1)) in por_oid:
            item = por_oid[int(m.group(1))]
            return item, (item if item in catalogo else None)
        return limpio, None
    return res


def _aplanar_y(n) -> list:
    if n["t"] == "and":
        out = []
        for x in n["items"]:
            out += _aplanar_y(x)
        return out
    return [n]


def _props(n, out) -> None:
    """Los campos (`prop`) que usa un árbol, en el orden en que se leen. SIN recursión: el árbol viene de un texto de DelPro y puede ser mucho más profundo que
    la pila de Python (una recursión acá dejaba al informe entero «no se pudo leer»)."""
    pila = [n]
    while pila:
        x = pila.pop()
        if x is None:
            continue
        if x["t"] == "prop":
            out.append(x)
            continue
        hijos = [x[k] for k in ("izq", "der", "x", "a", "b", "patron") if k in x]
        for k in ("items", "valores", "args"):
            hijos += list(x.get(k, ()))
        pila.extend(reversed(hijos))                                     # al revés, para sacarlos en el mismo orden de siempre


def _faltantes_de(termino, resolver) -> list:
    """Los campos de DelPro que usa un término y que el catálogo no tiene (sin las columnas sueltas que no se pudieron identificar)."""
    props, out = [], []
    _props(termino, props)
    for p in props:
        nombre, id_ = resolver(p["path"])
        if id_ is None and nombre not in out and "." not in nombre and not _ID_COLUMNA.match(nombre):
            out.append(nombre)
    return out


def _cond(campo, op, valor) -> dict:
    return {"campo": campo, "op": op, "valor": valor}


def _campo_de(n, resolver, catalogo):
    if n["t"] != "prop":
        raise _NoTraducible("Compara algo que no es un campo.")
    nombre, id_ = resolver(n["path"])
    if id_ is None:
        suelto = nombre.split(".", 1)[1] if nombre.startswith("ReproductionInfo.") else None
        if suelto in catalogo:                                                       # existe, pero NO es el mismo dato: decir «no existe» sería mentira
            e = catalogo[suelto].etiqueta
            raise _NoTraducible(f"Es la fecha guardada en la reproducción, distinta de la «{e}» de acá." if catalogo[suelto].tipo == "fecha"
                                else f"Es el dato guardado en la reproducción, distinto de «{e}» de acá.")
        if _ID_COLUMNA.match(nombre):                                                # una columna que no es de este informe: no hay nombre que mostrar
            raise _NoTraducible(f"No se pudo identificar a qué campo de DelPro corresponde «{nombre}».")
        raise _NoTraducible(f"El campo «{nombre}» todavía no existe acá.")
    return catalogo[id_]


def _sin_tildes(texto) -> str:
    """Para comparar un texto sin importar mayúsculas, tildes ni espacios de los costados («prenada» = «Preñada»)."""
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if not unicodedata.combining(c)).casefold().strip()


def _de_la_lista(campo, texto, parcial=False):
    """Un campo de texto CERRADO (`Campo.valores`: el estado de la reproducción) solo puede valer lo que dice su lista. Un código de DelPro («1»), un nombre en
    inglés («Open») o cualquier otra cosa no es ninguno de esos: traerlo tal cual daba un informe «completo» que no devuelve ningún animal. Se devuelve el valor
    con el nombre de la lista (sin tildes ni mayúsculas se reconoce igual). `parcial`: para «contiene», que alcanza con un pedazo de alguno."""
    if not campo.valores:
        return texto
    buscado = _sin_tildes(texto)
    if not parcial:                                                                  # el código o el nombre en inglés que DelPro escribe en sus filtros («Sex = '2'», «'Uncertain'»)
        for k, v in campo.equivale:
            if _sin_tildes(k) == buscado:
                return v
    if parcial:
        if buscado and any(buscado in _sin_tildes(v) for v in campo.valores):
            return texto
    else:
        for v in campo.valores:
            if _sin_tildes(v) == buscado:
                return v
    raise _NoTraducible("Compara con un código de DelPro que acá no existe.")


def _es_si_no(campo) -> bool:
    """Un campo «sí/no» (`En ordeño`, `Preñada`…): de texto con la lista cerrada («No», «Sí»). DelPro lo compara con `True` o `False`."""
    return campo.tipo == "txt" and tuple(campo.valores) == informes_catalogo.SI_NO


_OPUESTO = {"Sí": "No", "No": "Sí"}


def _valor_de(campo, lit, parcial=False):
    if lit["t"] != "lit":
        raise _NoTraducible("Compara con algo que no es un valor fijo.")
    k = lit["k"]
    if _es_si_no(campo):                                                             # un sí/no solo se compara con verdadero o falso, no con un número ni con un texto
        if k != "bool":
            raise _NoTraducible(f"«{campo.etiqueta}» se compara con verdadero o falso.")
        return "Sí" if lit["v"] else "No"
    if k in ("null", "bool", "enum"):
        raise _NoTraducible("Compara con un estado o un valor especial de DelPro que acá no se puede traer.")
    if campo.tipo == "num" and k == "num":
        return lit["v"]
    if campo.tipo == "txt" and k == "str":
        return _de_la_lista(campo, lit["v"], parcial)
    if campo.tipo == "txt" and k == "num" and campo.equivale and not parcial:        # `Sex = 2` sin comillas: el mismo código
        return _de_la_lista(campo, str(int(lit["v"])) if float(lit["v"]) == int(lit["v"]) else str(lit["v"]))
    if campo.tipo == "fecha" and k == "fecha":
        if lit["v"] is None:
            raise _NoTraducible("La fecha tiene una hora: acá se compara solo por día." if _HORA_EN_FECHA.search(lit.get("raw") or "")
                                else "No se entiende cómo está escrita la fecha.")
        return lit["v"]
    raise _NoTraducible(f"El valor no es del tipo de «{campo.etiqueta}».")


def _traducir_termino(n, resolver, catalogo):
    """Un término del filtro -> (condiciones de LactIA, id del campo que tiene que tener dato o None, ¿un animal SIN dato pasa este término en DelPro?).
    `_NoTraducible` si no se puede. Un «menor que» o un «distinto de» evaluado EN MEMORIA (el filtro de la lista) deja pasar a los nulos; en SQL no."""
    t = n["t"]
    if t == "cmp":
        izq, der, op = n["izq"], n["der"], n["op"]
        if izq["t"] == "lit" and der["t"] == "prop":
            izq, der, op = der, izq, _VUELTA[op]
        campo = _campo_de(izq, resolver, catalogo)
        valor = _valor_de(campo, der)
        if campo.tipo == "txt":
            if _es_si_no(campo) and op in ("=", "<>"):                               # `X <> True` es «No»; un sí/no nunca queda vacío (sin reproducción es «No»), así que no hay animales sin dato
                return [_cond(campo.id, "es", valor if op == "=" else _OPUESTO[valor])], None, False
            if op != "=":
                raise _NoTraducible("De un texto solo se puede traer «es igual a».")
            return [_cond(campo.id, "es", valor)], None, False
        if campo.tipo == "fecha" and op == "<>":
            raise _NoTraducible("De una fecha no se puede traer «distinto de».")
        return [_cond(campo.id, op, valor)], None, op in ("<", "<=", "<>")
    if t == "in":
        campo = _campo_de(n["x"], resolver, catalogo)
        if campo.tipo not in ("num", "txt"):
            raise _NoTraducible(f"«{campo.etiqueta}» no admite «es uno de».")
        if len(n["valores"]) > informes_motor.LISTA_MAX:
            raise _NoTraducible(f"«Es uno de» admite hasta {informes_motor.LISTA_MAX} valores.")
        return [_cond(campo.id, "en", [_valor_de(campo, v) for v in n["valores"]])], None, False
    if t == "between" or (t == "func" and n["nombre"].lower() == "between" and len(n["args"]) == 3):
        x, a, b = (n["x"], n["a"], n["b"]) if t == "between" else tuple(n["args"])
        campo = _campo_de(x, resolver, catalogo)
        if campo.tipo not in ("num", "fecha"):
            raise _NoTraducible(f"«{campo.etiqueta}» no admite «entre».")
        return [_cond(campo.id, ">=", _valor_de(campo, a)), _cond(campo.id, "<=", _valor_de(campo, b))], None, False
    if t == "func":
        nombre, args = n["nombre"], n["args"]
        if nombre.lower() == "indaterange" and len(args) == 3:
            campo = _campo_de(args[0], resolver, catalogo)
            if campo.tipo != "fecha":
                raise _NoTraducible(f"«{campo.etiqueta}» no es una fecha.")
            # InDateRange de DevExpress es [desde, hasta): incluye el primer día y no el último.
            return [_cond(campo.id, ">=", _valor_de(campo, args[1])), _cond(campo.id, "<", _valor_de(campo, args[2]))], None, False
        if nombre.lower() == "contains" and len(args) == 2:
            campo = _campo_de(args[0], resolver, catalogo)
            if campo.tipo != "txt":
                raise _NoTraducible(f"«{campo.etiqueta}» no es un texto.")
            return [_cond(campo.id, "contiene", _valor_de(campo, args[1], parcial=True))], None, False
        raise _NoTraducible(f"Usa «{nombre}», que acá no existe.")
    if t == "isnull":
        campo = _campo_de(n["x"], resolver, catalogo)
        if n["negado"]:
            return [], campo.id, False
        raise _NoTraducible("Pide los animales SIN dato en ese campo: acá no se puede.")
    motivos = {"or": "Combina condiciones con «o»: acá todas se cumplen a la vez.", "not": "Usa «no», que acá no existe.",
               "like": "Usa «parecido a», que acá no existe.", "arit": "Hace cuentas adentro del filtro.",
               "prop": "No es una condición.", "lit": "No es una condición.", "param": "Pide un valor que se elige al abrir el informe."}
    raise _NoTraducible(motivos.get(t, "No se entiende."))


def _invalida(condicion, catalogo):
    """Una condición ya armada, probada con el compilador de informes Y con la validación de solo lectura de la app: el motivo si no la acepta, o None.
    Lo que viene de DelPro es un dato: un valor con `;`, `--`, `/*` o una palabra como «Backup» (que `validate_sql` rechaza hasta dentro de un texto) no llega a una consulta."""
    import db
    try:
        c = informes_motor.compilar({"columnas": [{"campo": condicion["campo"]}], "condiciones": [condicion]}, campos=catalogo, herd=1)
        db.validate_sql(c["sql"])
    except informes_motor.InformeError as e:
        return str(e)
    except db.UnsafeQueryError:
        return "El valor lleva una palabra que no se puede usar en una consulta."
    except (ArithmeticError, ValueError):
        return "El valor no es válido."
    return None


def _partir_en_y(texto: str) -> list:
    """El texto cortado en los términos unidos por «y» de PRIMER NIVEL (fuera de comillas, corchetes, #fechas# y paréntesis), para que un término que no se
    entiende no se lleve puestos a los demás. Si no se puede partir con seguridad (un «o» de primer nivel cambiaría el sentido por la precedencia, o hay
    una comilla, un corchete o un paréntesis sin cerrar) devuelve [texto]."""
    partes, ini, nivel, i, n = [], 0, 0, 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "'":
            j = i + 1
            while j < n:
                if texto[j] == "'":
                    if j + 1 < n and texto[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            else:
                return [texto]
            i = j + 1
            continue
        if c == "[":
            j = texto.find("]", i)
            if j < 0:
                return [texto]
            i = j + 1
            continue
        if c == "#":
            j = texto.find("#", i + 7) if texto.startswith("##Enum#", i) else texto.find("#", i + 1)
            if j < 0:
                return [texto]
            i = j + 1
            continue
        if c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
            if nivel < 0:
                return [texto]
        elif nivel == 0 and texto.startswith("||", i):
            return [texto]
        elif nivel == 0 and texto.startswith("&&", i):
            partes.append(texto[ini:i])
            ini = i + 2
            i += 2
            continue
        elif nivel == 0 and (c.isalpha() or c == "_") and (i == 0 or not (texto[i - 1].isalnum() or texto[i - 1] in "_.")):
            m = re.match(r"[A-Za-z_][A-Za-z0-9_.]*", texto[i:])
            palabra = (m.group(0) if m else c).lower()
            if palabra == "or":
                return [texto]
            if palabra == "and":
                partes.append(texto[ini:i])
                ini = i + 3
            i += len(m.group(0)) if m else 1
            continue
        i += 1
    if nivel != 0:
        return [texto]
    partes.append(texto[ini:])
    return [p.strip() for p in partes if p.strip()]


def _corto(texto: str, largo: int = 300) -> str:
    return re.sub(r"\s+", " ", str(texto)).strip()[:largo]


def _para_mostrar(nombre: str) -> str:
    """El nombre de un campo de DelPro en un aviso: «En ordeño (InMilk)» si se sabe cómo se llama en castellano; si no, tal cual (una ruta no se toca)."""
    return f"{NOMBRE_EN_DELPRO[nombre]} ({nombre})" if nombre in NOMBRE_EN_DELPRO else nombre


def _pendiente(texto: str, motivo: str, campos=()) -> dict:
    return {"texto": texto, "motivo": motivo, "campos": list(campos)}


def _texto_de_termino(nodo: dict, fuente: str, nombre_de) -> str:
    try:
        return mostrar(nodo, nombre_de)
    except (RecursionError, ValueError, KeyError, TypeError):                 # un árbol que no se puede escribir en palabras: el texto original, acotado
        return _corto(fuente[nodo["ini"]:nodo["fin"]], 200)


def condiciones_de(texto, resolver, catalogo, maximo, previas=(), en_memoria=False):
    """El texto de un filtro de DelPro -> (condiciones de LactIA, lo que NO se pudo traer).

    Se trae una conjunción («y») de comparaciones simples sobre campos del catálogo, un «es uno de», un «entre» (dos condiciones) o un rango de
    fechas. Cada término que no se puede traer queda en la segunda lista como {"texto", "motivo", "campos"} (campos = los de DelPro que acá no
    existen). Si faltan términos, el informe va a mostrar MÁS animales que en DelPro, nunca menos. Un término que ni se puede leer no se lleva puestos a los
    otros: el texto se parte en sus «y» de primer nivel. `maximo` = cuántas condiciones caben EN TOTAL, y `previas` las que ya se aceptaron de otra fuente (lo
    repetido no ocupa lugar). `en_memoria`: el filtro lo evalúa DelPro en memoria (el de la lista), donde un animal SIN dato pasa un «menor que» o un
    «distinto de»; SQL lo deja afuera, así que esos términos solo se traen si el mismo campo pide «con dato» o ya tiene otra condición que deja afuera a los nulos."""
    if texto is None or not str(texto).strip():
        return [], []
    texto = str(texto)
    nombre_de = lambda p: _para_mostrar(resolver(p)[0])
    nodos = []                                                                      # (nodo, texto de donde salió); nodo None = un término ilegible
    try:
        nodos = [(n, texto) for n in _aplanar_y(analizar(texto))]
    except CriterioError:
        partes = _partir_en_y(texto)
        if len(partes) < 2:
            return [], [_pendiente(_corto(texto), "No se pudo leer este filtro de DelPro.")]
        for parte in partes:
            try:
                nodos += [(n, parte) for n in _aplanar_y(analizar(parte))]
            except CriterioError:
                nodos.append((None, parte))
    pend, items = [], []
    for nodo, fuente in nodos:
        if nodo is None:
            pend.append(_pendiente(_corto(fuente), "No se pudo leer esta condición de DelPro."))
            continue
        try:
            nuevas, exige, nulos = _traducir_termino(nodo, resolver, catalogo)
            for c in nuevas:
                motivo = _invalida(c, catalogo)
                if motivo:
                    raise _NoTraducible(motivo)
        except _NoTraducible as e:
            pend.append(_pendiente(_texto_de_termino(nodo, fuente, nombre_de), str(e), _faltantes_de(nodo, resolver)))
            continue
        except (ArithmeticError, RecursionError, ValueError):
            pend.append(_pendiente(_texto_de_termino(nodo, fuente, nombre_de), "No se pudo leer esta condición de DelPro."))
            continue
        items.append((nodo, fuente, nuevas, exige, nulos))
    # Un campo está «cubierto» si DelPro ya deja afuera a sus animales sin dato por otro lado: un «con dato» de la misma lista, una comparación de otro tipo sobre el
    # mismo campo (un «mayor que», un «igual», un «entre», «es uno de»: un rango escrito con dos comparaciones) o una condición que ya se aceptó antes (`previas`,
    # que en DelPro también dejó afuera a los nulos). Un «menor que» o un «distinto de» no cubre a otro: los dos dejan pasar al animal sin dato.
    cubiertos = {exige for _, _, _, exige, _ in items if exige}
    cubiertos |= {c["campo"] for _, _, nuevas, exige, nulos in items if not exige and not nulos for c in nuevas}
    cubiertos |= {c["campo"] for c in previas}
    conds, previos = [], list(previas)
    for nodo, fuente, nuevas, exige, nulos in items:
        if exige:
            continue
        if en_memoria and nulos and not all(c["campo"] in cubiertos for c in nuevas):
            pend.append(_pendiente(_texto_de_termino(nodo, fuente, nombre_de),
                                   "En DelPro los animales sin dato también pasan este filtro: traerlo solo mostraría menos animales."))
            continue
        nuevas = [c for c in nuevas if c not in previos and c not in conds]
        if len(previos) + len(conds) + len(nuevas) > maximo:
            pend.append(_pendiente(_texto_de_termino(nodo, fuente, nombre_de), f"Un informe admite hasta {maximo} condiciones y ya tiene {len(previos) + len(conds)}."))
            continue
        conds += nuevas
    for nodo, fuente, nuevas, exige, nulos in items:
        if exige and not any(c["campo"] == exige for c in previos + conds):        # «con dato» sobra si ya hay una comparación del mismo campo: SQL deja afuera a los nulos
            pend.append(_pendiente(_texto_de_termino(nodo, fuente, nombre_de), "Pide que el campo tenga dato: acá no se puede pedir solo eso."))
    return conds, pend


# ================================================================================================ la lectura de la base

_CACHE: dict = {}
_LOCK = threading.Lock()


def armar_datos(informes, columnas, filtros, clases) -> dict:
    """Las filas de las cuatro tablas (listas de dicts) como un solo bloque de datos para traducir."""
    por_informe: dict = {}
    for c in columnas:
        por_informe.setdefault(c["Report"], []).append(c)
    for lista in por_informe.values():
        lista.sort(key=lambda c: (c["Index"] if c["Index"] is not None else 10 ** 9, c["OID"]))
    return {"informes": {r["OID"]: r for r in informes}, "columnas": por_informe,
            "filtros": {f["OID"]: {"nombre": f["Name"], "texto": f["FilterText"]} for f in filtros},
            "clases": {c["OID"]: (c["TypeName"] or "").rsplit(".", 1)[-1] for c in clases}}


FILAS_MAX = 20000


def _leer(run, sql, tambo) -> list:
    r = run(sql, validate=True, tambo=tambo, max_rows=FILAS_MAX)
    if r.get("truncated"):                                                          # una lista cortada a la mitad se vería «completa» y no lo es
        raise DelProNoDisponible("Este tambo tiene demasiados informes o columnas de DelPro para traerlos a la vez: no se muestra una lista a medias.")
    return [dict(zip(r["columns"], fila)) for fila in r["rows"]]


def cargar(tambo, run=None, refrescar=False) -> dict:
    """Lee (solo lectura) los informes de DelPro del tambo y los guarda en memoria `TTL_CACHE_S` segundos. `DelProNoDisponible` si no se puede."""
    with _LOCK:
        guardado = _CACHE.get(tambo)
        if guardado and not refrescar and time.monotonic() - guardado[0] < TTL_CACHE_S:
            return guardado[1]
    if run is None:
        import db
        run = db.run_query
    try:
        filas = {k: _leer(run, sql, tambo) for k, sql in CONSULTAS.items()}
    except DelProNoDisponible:
        raise
    except Exception as e:  # noqa: BLE001 -- el detalle (servidor, usuario) no se le muestra a nadie
        if type(e).__name__ == "TablaNoDisponibleError" or "Invalid object name" in str(e):
            raise DelProNoDisponible("Este tambo no tiene informes de DelPro para traer.", vacio=True) from e
        raise DelProNoDisponible("No se pudieron leer los informes de DelPro. Tocá «Volver a leer» para intentar de nuevo; "
                                 "si sigue igual, avisale a quien administra LactIA.") from e
    datos = armar_datos(filas["informes"], filas["columnas"], filas["filtros"], filas["clases"])
    with _LOCK:
        _CACHE[tambo] = (time.monotonic(), datos)
    return datos


def vaciar_cache() -> None:
    with _LOCK:
        _CACHE.clear()


# ================================================================================================ un informe

def _tipo(fila) -> str:
    tn = fila.get("TypeName") or ""
    if tn.endswith(".DDMReportFolder"):
        return "carpeta"
    return "informe" if tn.endswith(".DDMReport") else "otro"


def _limpio(texto, largo=NOMBRE_MAX) -> str:
    """Un texto de DelPro para mostrar: los espacios, tabulaciones y saltos de línea quedan en un solo espacio y se sacan los caracteres de control."""
    s = re.sub(r"\s+", " ", str(texto or ""))
    return "".join(ch for ch in s if ord(ch) >= 32).strip()[:largo]


def _carpeta(datos, fila) -> str:
    nombres, visto, oid = [], set(), fila.get("ParentFolder")
    while oid is not None and oid not in visto and oid in datos["informes"] and len(nombres) < 12:
        visto.add(oid)
        f = datos["informes"][oid]
        nombres.append(_limpio(f.get("Name")))
        oid = f.get("ParentFolder")
    return " › ".join(reversed(nombres))


def _lista(nombres, tope=MUESTRA_MAX) -> str:
    nombres = list(nombres)
    return ", ".join(nombres[:tope]) + (f" y {len(nombres) - tope} más" if len(nombres) > tope else "")


def _nombre_de_campo(item) -> str:
    """«En ordeño (InMilk)» si se sabe cómo se llama en castellano; si no, el nombre interno de DelPro."""
    n = _limpio(item, 60)
    return f"{NOMBRE_EN_DELPRO[item]} ({n})" if item in NOMBRE_EN_DELPRO else n


def _lista_de_campos(items) -> str:
    return _lista(_nombre_de_campo(x) for x in items)


def _nombre_resumen(codigo) -> str:
    k = RESUMEN_DE_CODIGO.get(codigo)
    return informes_motor.NOMBRE_RESUMEN[k].lower() if k else f"un resumen desconocido (código {codigo})"


def _codigos(col) -> tuple:
    """(códigos del TOTAL en el orden de DelPro, código de los GRUPOS o None) de una columna. `SummaryTypes` es la lista del total («4,0»); si no se entiende,
    se usa `SummaryType`. 5 y 6 quieren decir «ninguno»."""
    total = []
    s = col.get("SummaryTypes")
    if isinstance(s, str) and s.strip():
        total = [int(x) for x in re.split(r"[,;\s]+", s.strip()) if re.fullmatch(r"[0-9]{1,3}", x)]
    if not total and col.get("SummaryType") not in SIN_RESUMEN:
        total = [col["SummaryType"]]
    g = col.get("GroupSummaryType")
    return [c for c in total if c not in SIN_RESUMEN], (None if g in SIN_RESUMEN else g)


def _resumen_de_campo(dups: list, campo, hay_grupos: bool = True) -> tuple:
    """(resumen elegido o None, frase de lo que se pierde o None, ¿se veía solo en el total o solo en los grupos?). `dups` = las columnas de DelPro que son
    este mismo campo (repetir una columna es cómo DelPro muestra dos resúmenes del mismo campo; acá una columna lleva uno solo).
    `hay_grupos`: ¿el informe de DelPro tenía alguna columna agrupada? Si no, el resumen «de grupo» no se muestra NUNCA: su 0 («suma») es el valor por defecto de
    la columna (1.450 de las 2.244 columnas de informes sin grupos del volcado real; en los que sí tienen grupos casi nunca es 0) y no una elección: se ignora."""
    totales, grupos = [], []
    for d in dups:
        t, g = _codigos(d)
        totales += [x for x in t if x not in totales]
        if hay_grupos and g is not None and g not in grupos:
            grupos.append(g)
    posibles = [c for c in totales + grupos if c in RESUMEN_DE_CODIGO and RESUMEN_DE_CODIGO[c] in informes_motor.RESUMENES[campo.tipo]]
    elegido = posibles[0] if posibles else None
    todos = totales + [g for g in grupos if g not in totales]
    if any(c != elegido for c in todos):
        tenia = " y ".join(dict.fromkeys(_nombre_resumen(c) for c in todos))
        if elegido is not None:
            frase = f"«{campo.etiqueta}»: en DelPro tenía {tenia}; acá cada columna lleva un solo resumen y se usa {_nombre_resumen(elegido)}"
        else:
            frase = f"«{campo.etiqueta}»: en DelPro tenía {tenia}, que acá no se puede calcular para ese campo"
        return (RESUMEN_DE_CODIGO[elegido] if elegido is not None else None), frase, False
    asimetria = hay_grupos and elegido is not None and bool(totales) != bool(grupos)
    return (RESUMEN_DE_CODIGO[elegido] if elegido is not None else None), None, asimetria


def _dif(tipo, gravedad, texto, detalle=None) -> dict:
    d = {"tipo": tipo, "gravedad": gravedad, "texto": texto}
    if detalle:
        d["detalle"] = detalle
    return d


def _aviso_de_filtro(origen, pend) -> dict:
    """Lo que no se pudo traer de un filtro. El texto es corto; cada término y su motivo van en `detalle` (la pantalla los muestra debajo)."""
    n = len(pend)
    cuerpo = "tiene una condición que no se pudo traer" if n == 1 else f"tiene {n} condiciones que no se pudieron traer"
    detalle = [f"{p['texto']} — {p['motivo']}" for p in pend[:12]]
    if n > 12:
        detalle.append(f"…y {n - 12} más.")
    return _dif("filtro", "contenido", f"{origen} {cuerpo}. Sin {'ella' if n == 1 else 'ellas'} este informe puede mostrar más animales que en DelPro.", detalle)


def traducir_informe(datos, oid, catalogo=None, rebanos=None) -> dict:
    """Un informe de DelPro como definición de LactIA, más todo lo que no se pudo traer.

    {"oid", "nombre", "carpeta", "nivel", "del_tambo", "descripcion", "estado" (completo | parcial | no_disponible), "motivo" (si no_disponible),
     "definicion" (None si no_disponible), "columnas": {"traidas", "total"}, "diferencias": [{"tipo", "gravedad", "texto", "detalle"?}],
     "faltan": [campos de DelPro que el catálogo no tiene]}. Gravedad: «contenido» (cambia qué animales o qué totales), «columnas» (falta algo del diseño),
    «presentacion» (orden, colores) o «info». `rebanos`: los rebaños de este tambo (un informe de otro rebaño de la base no se trae). `KeyError` si no es un informe."""
    catalogo = informes_catalogo.CATALOGO if catalogo is None else catalogo
    fila = datos["informes"].get(oid)
    if fila is None or _tipo(fila) != "informe":
        raise InformeNoEncontrado(oid)
    clase = datos["clases"].get(fila.get("ReportClass"), "")
    nombre = _limpio(fila.get("Name")) or "Informe de DelPro"
    salida = {"oid": oid, "nombre": nombre, "carpeta": _carpeta(datos, fila), "del_tambo": not fila.get("IsPredefined"),
              "nivel": CLASES_DE_ANIMAL.get(clase) or NOMBRES_DE_NIVEL.get(clase) or "Otro tipo",
              "descripcion": _limpio(fila.get("Description"), 300), "definicion": None, "diferencias": [], "faltan": [],
              "columnas": {"traidas": 0, "total": len(datos["columnas"].get(oid, []))}}
    if clase not in CLASES_DE_ANIMAL:
        salida.update(estado="no_disponible", motivo=f"Es un informe de {salida['nivel'].lower()}, no de animales: por ahora LactIA solo trae los informes de animales.")
        return salida
    herd = fila.get("Herd")
    if rebanos and isinstance(herd, int) and not isinstance(herd, bool) and herd > 0 and herd not in rebanos:
        salida.update(estado="no_disponible", motivo="Es un informe de otro tambo de esta base de datos (otro rebaño).")
        return salida

    cols = datos["columnas"].get(oid, [])
    faltan, difs = [], []

    def falta(nombre_campo):
        n = _limpio(nombre_campo, 60)
        if n and n not in faltan:
            faltan.append(n)

    # --- columnas
    elegidas, sin_campo, repetidas, de_mas = [], [], [], []
    for c in cols:
        item = c["item"]
        if item not in catalogo:
            sin_campo.append(item)
            falta(item)
        elif any(item == e["item"] for e in elegidas):
            repetidas.append(item)
        elif len(elegidas) >= informes_motor.COLUMNAS_MAX:
            de_mas.append(item)
        else:
            elegidas.append(c)
    if sin_campo:
        n = len(sin_campo)
        difs.append(_dif("columna", "columnas", (f"No se trajo 1 columna porque ese campo todavía no existe acá: {_lista_de_campos(sin_campo)}." if n == 1 else
                                                  f"No se trajeron {n} columnas porque esos campos todavía no existen acá: {_lista_de_campos(sin_campo)}.")))
    if de_mas:
        difs.append(_dif("columna", "columnas", f"Un informe admite hasta {informes_motor.COLUMNAS_MAX} columnas: no se trajeron {_lista_de_campos(de_mas)}."))
    salida["columnas"]["traidas"] = len(elegidas)
    salida["faltan"] = faltan
    if not elegidas:
        salida.update(estado="no_disponible", diferencias=difs,
                      motivo=("Ninguna de sus columnas existe todavía acá" + (f" ({_lista_de_campos(sin_campo)})" if sin_campo else "") + "."
                              if cols else "No tiene columnas."))
        return salida
    usados = {e["item"] for e in elegidas}
    etiqueta = lambda campo_id: catalogo[campo_id].etiqueta

    # --- agrupar
    agrupar = []
    for c in sorted((c for c in cols if (c.get("GroupIndex") if c.get("GroupIndex") is not None else -1) >= 0), key=lambda c: c["GroupIndex"]):
        item = c["item"]
        if item not in catalogo:
            difs.append(_dif("agrupacion", "columnas", f"Agrupaba por «{_nombre_de_campo(item)}», que todavía no existe acá."))
        elif not catalogo[item].agrupable:
            difs.append(_dif("agrupacion", "columnas", f"Agrupaba por «{etiqueta(item)}»: acá no se puede agrupar por ese campo (tiene demasiados valores distintos)."))
        elif item in agrupar:
            continue
        elif len(agrupar) >= informes_motor.AGRUPAR_MAX:
            difs.append(_dif("agrupacion", "columnas", f"Agrupaba en más de {informes_motor.AGRUPAR_MAX} niveles: acá se trae hasta el segundo; falta «{etiqueta(item)}»."))
        else:
            agrupar.append(item)
            if c.get("SortDirection") == 2:
                difs.append(_dif("orden", "presentacion", f"Los grupos de «{etiqueta(item)}» se ordenaban de mayor a menor; acá salen de menor a mayor."))

    # --- orden de la lista: acá hay una sola columna de orden
    claves = sorted((c for c in cols if c.get("SortDirection") in (1, 2) and (c.get("SortIndex") if c.get("SortIndex") is not None else -1) >= 0
                     and (c.get("GroupIndex") if c.get("GroupIndex") is not None else -1) < 0), key=lambda c: c["SortIndex"])
    orden = None
    if claves:
        primera = claves[0]
        if primera["item"] in usados:
            orden = {"campo": primera["item"], "dir": 1 if primera["SortDirection"] == 1 else -1}
        else:
            difs.append(_dif("orden", "presentacion", f"Ordenaba por «{_nombre_de_campo(primera['item'])}», que no se trajo: acá la lista sale ordenada por la primera columna."))
        resto = [c["item"] for c in claves[1:]]
        if resto:
            difs.append(_dif("orden", "presentacion", f"También ordenaba por {_lista_de_campos(resto)}: acá se puede ordenar por una sola columna."))

    # --- resúmenes por columna (una columna repetida es un resumen más del mismo campo)
    columnas_def, perdidos, asimetria = [], [], False
    hay_grupos = any((c.get("GroupIndex") if c.get("GroupIndex") is not None else -1) >= 0 for c in cols)       # de cualquier campo, se lo traiga o no
    for e in elegidas:
        campo, col_def = catalogo[e["item"]], {"campo": e["item"]}
        resumen, frase, asim = _resumen_de_campo([c for c in cols if c["item"] == e["item"]], campo, hay_grupos)
        if resumen:
            col_def["resumen"] = resumen
        if frase:
            perdidos.append(frase)
        asimetria = asimetria or asim
        columnas_def.append(col_def)
    if repetidas and not perdidos:
        n = len(set(repetidas))
        difs.append(_dif("columna", "info", f"{'Estaba repetida' if n == 1 else 'Estaban repetidas'} y se trajo una sola vez: {_lista_de_campos(dict.fromkeys(repetidas))}."))
    if perdidos:
        texto = ("No se pudo traer igual un resumen: " + "; ".join(perdidos) + "." if len(perdidos) <= 2
                 else f"En {len(perdidos)} columnas el resumen no se pudo traer igual que en DelPro.")
        difs.append(_dif("resumen", "columnas", texto, [p + "." for p in perdidos]))
    if asimetria:
        difs.append(_dif("resumen", "info", "En DelPro algunos resúmenes se veían solo en el total o solo en los grupos; acá se ven en los dos."))

    # --- filtros: el de la lista (grilla; DelPro lo evalúa en memoria), el propio de cada columna si no hay de lista, y el de servidor (SQL)
    condiciones, maximo = [], informes_motor.CONDICIONES_MAX
    por_columna = dict(((c["OID"], c) for c in cols))
    resolver_grilla = resolver_por_columnas(cols, catalogo)
    fuentes = []
    if str(fila.get("CustomFilter") or "").strip():
        fuentes.append(("El filtro de la lista de DelPro (el de las columnas)", fila["CustomFilter"], resolver_grilla, True))
    else:
        for c in cols:
            if str(c.get("FilterString") or "").strip():
                fuentes.append((f"El filtro de la columna «{_nombre_de_campo(c['item'])}» de DelPro", str(c["FilterString"]).replace("[{0}]", f"[C{c['OID']}]"), resolver_grilla, True))
    sf = datos["filtros"].get(fila.get("ServerFilter"))
    if sf and sf.get("texto"):
        fuentes.append((f"El filtro «{_limpio(sf.get('nombre'), 60) or 'sin nombre'}» de DelPro", sf["texto"], resolver_por_nombre(catalogo), False))
    for origen, texto, resolver, en_memoria in fuentes:
        cs, pend = condiciones_de(texto, resolver, catalogo, maximo, previas=condiciones, en_memoria=en_memoria)
        condiciones += cs
        if pend:
            difs.append(_aviso_de_filtro(origen, pend))
            for p in pend:
                for f in p["campos"]:
                    falta(f)

    # --- alcance: DelPro puede mirar TODOS los rebaños de la base; acá siempre es el de este tambo. Es lo que el tambo quiere (no ver los animales de los otros tambos
    # de la base) y el caso habitual (26 de 58 informes de animales del tambo), así que es un dato («info») y no vuelve «parcial» al informe.
    if herd == 0:
        difs.append(_dif("alcance", "info", "En DelPro este informe mira todos los rebaños de la base de datos (también los animales de otros tambos). "
                         "Acá solo salen los animales de este tambo, así que los totales pueden ser menores que los de DelPro."))
    if fila.get("IncludeCulledAndExitedAnimals"):
        # Acá «incluir descartados» suma a los animales que alguna vez ordeñaron (`rebano.filtro_historico` mira `AnimalDaily`): las terneras y la recría que ya
        # salieron sin haber ordeñado nunca no están. Es un cambio de QUÉ animales aparecen (menos, a diferencia de un filtro sin traer), así que es «contenido».
        difs.append(_dif("alcance", "contenido", "En DelPro este informe incluía también los animales descartados y los que ya salieron del tambo (terneras y recría "
                         "incluidas); acá solo se suman los que alguna vez ordeñaron, así que pueden salir menos animales."))
    if fila.get("ConColores"):
        difs.append(_dif("formato", "presentacion", "En DelPro este informe pintaba celdas con reglas de color; acá no se traen."))

    definicion = {"columnas": columnas_def, "incluir_descartados": bool(fila.get("IncludeCulledAndExitedAnimals"))}
    if agrupar:
        definicion["agrupar_por"] = agrupar
    if condiciones:
        definicion["condiciones"] = condiciones
    if orden:
        definicion["orden"] = orden
    try:
        informes_motor.compilar(definicion, campos=catalogo, herd=1)
    except informes_motor.InformeError as e:            # no debería pasar: cada pieza se validó. Si pasa, no se ofrece un informe que no corre.
        salida.update(estado="no_disponible", diferencias=difs, motivo=f"No se pudo armar el informe: {e}")
        return salida
    sin_verificar = [catalogo[c].etiqueta for c in dict.fromkeys([c["campo"] for c in columnas_def] + agrupar + [c["campo"] for c in condiciones]) if not catalogo[c].verificado]
    if sin_verificar:
        difs.append(_dif("verificacion", "info", "Incluye campos que todavía no se compararon con DelPro (⚠): " + ", ".join(sin_verificar) + ". Sus valores pueden diferir."))
    salida.update(definicion=definicion, diferencias=difs, faltan=faltan,
                  estado="parcial" if any(d["gravedad"] in ("contenido", "columnas") for d in difs) else "completo")
    return salida


# ================================================================================================ el listado

def _informe_ilegible(datos, oid, fila) -> dict:
    """Lo que se muestra de un informe que no se pudo traducir por un error inesperado: no se cae la lista por uno solo."""
    return {"oid": oid, "nombre": _limpio(fila.get("Name")) or "Informe de DelPro", "carpeta": _carpeta(datos, fila), "del_tambo": not fila.get("IsPredefined"),
            "nivel": "Otro tipo", "descripcion": "", "estado": "no_disponible", "motivo": "No se pudo leer este informe de DelPro.", "definicion": None,
            "diferencias": [], "faltan": [], "columnas": {"traidas": 0, "total": 0}}


def listar(datos, solo_tambo=False, catalogo=None, rebanos=None) -> dict:
    """Todos los informes de DelPro (menos las carpetas, los fijos y los ocultos) con su estado de importación, y los campos que más faltan (de los informes de animales)."""
    catalogo = informes_catalogo.CATALOGO if catalogo is None else catalogo
    items, cuenta, faltantes = [], {"completo": 0, "parcial": 0, "no_disponible": 0}, {}
    for oid, fila in datos["informes"].items():
        if _tipo(fila) != "informe" or fila.get("IsHidden") or (solo_tambo and fila.get("IsPredefined")):
            continue
        try:
            t = traducir_informe(datos, oid, catalogo, rebanos)
        except Exception as exc:  # noqa: BLE001 -- un informe raro de DelPro no tumba la lista de los demás
            log.warning("informe de DelPro %s: no se pudo traducir (%s)", oid, type(exc).__name__)
            t = _informe_ilegible(datos, oid, fila)
        cuenta[t["estado"]] += 1
        for f in t["faltan"]:
            faltantes[f] = faltantes.get(f, 0) + 1
        items.append({"oid": oid, "nombre": t["nombre"], "carpeta": t["carpeta"], "nivel": t["nivel"], "del_tambo": t["del_tambo"],
                      "estado": t["estado"], "motivo": t.get("motivo", ""), "columnas": t["columnas"], "descripcion": t["descripcion"],
                      # «filtro sin traer: puede mostrar MÁS animales»: solo un filtro. Lo de los animales descartados también es «contenido» pero muestra MENOS.
                      "filtro_sin_traer": any(d["tipo"] == "filtro" and d["gravedad"] == "contenido" for d in t["diferencias"]), "n_diferencias": len(t["diferencias"]),
                      # lo que le falta al catálogo para ESTE informe: con eso la pantalla calcula qué le falta a los que se eligieron
                      "faltan": list(t["faltan"])})
    items.sort(key=lambda i: (ORDEN_DE_ESTADO[i["estado"]], not i["del_tambo"], i["nombre"].casefold()))
    top = sorted(faltantes.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:FALTANTES_TOP]
    return {"informes": items, "cuenta": cuenta, "faltantes": [{"campo": k, "informes": v, "nombre": NOMBRE_EN_DELPRO.get(k)} for k, v in top]}


# ================================================================================================ traer varios de una vez

TRAER_MAX = 200                                  # cuántos informes se traen en un pedido (un tambo tiene unas decenas)
ORIGEN_TITULO = "Traído de DelPro"


def origen_de(t):
    """La «nota de origen» de un informe traído: las diferencias que cambian QUÉ animales aparecen (un filtro que no se trajo, los animales descartados...), o None si no hay ninguna.
    Es lo que `informes_guardados` recuerda con el informe, para avisarlo al reabrirlo, al programar su envío y en cada mensaje."""
    avisos = [d["texto"] for d in t.get("diferencias", []) if d.get("gravedad") == "contenido"]
    if not avisos:
        return None
    return {"titulo": ORIGEN_TITULO, "sub": "«" + str(t.get("nombre") or "") + "»" + (" · " + str(t["carpeta"]) if t.get("carpeta") else ""), "avisos": avisos}


def traer_varios(datos, oids, guardar, catalogo=None, rebanos=None) -> list:
    """Traduce y GUARDA varios informes de DelPro, uno por uno, y dice qué pasó con cada uno: [{"oid", "nombre", "estado", "id"? , "motivo"?}].

    `guardar(nombre, definicion, origen)` -> ("traido", id) o ("ya_estaba", None): lo pone quien llama (acá no se sabe dónde se guarda). Estados: traido, ya_estaba, no_se_puede
    (con su motivo), no_existe y error. **No ejecuta nada contra la base**: solo traduce y guarda la definición. Un informe que falla no frena a los demás, y de un error nunca se
    devuelve el texto (puede traer datos de la base o una clave)."""
    salida, vistos = [], set()
    for oid in oids:
        if oid in vistos:
            continue
        vistos.add(oid)
        try:
            t = traducir_informe(datos, oid, catalogo, rebanos)
        except InformeNoEncontrado:
            salida.append({"oid": oid, "nombre": "", "estado": "no_existe"})
            continue
        except Exception as exc:  # noqa: BLE001 -- un informe raro de DelPro no frena a los otros
            log.warning("informe de DelPro %s: no se pudo traducir para traerlo (%s)", oid, type(exc).__name__)
            salida.append({"oid": oid, "nombre": "", "estado": "error", "motivo": "No se pudo leer este informe de DelPro."})
            continue
        if t["definicion"] is None:
            salida.append({"oid": oid, "nombre": t["nombre"], "estado": "no_se_puede", "motivo": t.get("motivo") or "Este informe no se puede traer todavía."})
            continue
        try:
            estado, id_ = guardar(t["nombre"], t["definicion"], origen_de(t))
        except Exception as exc:  # noqa: BLE001
            log.warning("informe de DelPro %s: no se pudo guardar (%s)", oid, type(exc).__name__)
            salida.append({"oid": oid, "nombre": t["nombre"], "estado": "error", "motivo": "No se pudo guardar este informe."})
            continue
        fila = {"oid": oid, "nombre": t["nombre"], "estado": estado}
        if id_ is not None:
            fila["id"] = id_
        salida.append(fila)
    return salida
