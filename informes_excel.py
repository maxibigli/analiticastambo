"""Exporta un informe a Excel (.xlsx).

Dos hojas: «Informe» (los datos, con números y fechas de verdad para poder ordenar y sumar en Excel) y «Condiciones» (qué
informe es, cuándo se generó y con qué filtros, y si se cortó por el tope de filas).

La hoja «Informe» tiene tres formas, según lo que pida la definición:
  * lista: un animal por fila, con una fila de totales al final si alguna columna tiene resumen;
  * agrupada: lo mismo, con un encabezado por grupo (y por subgrupo) que lleva su cantidad de animales y sus resúmenes. Cada
    grupo se puede plegar en Excel con el «−» de la izquierda;
  * solo resumen: una fila por grupo (y subgrupo) con la cantidad de animales y los resúmenes, sin la lista de animales.

Los totales, subtotales y resúmenes ya vienen calculados por la base sobre TODOS los animales del informe, aunque la lista se
haya cortado en el tope de filas: acá solo se escriben, nunca se recalculan.

Ningún valor se escribe como fórmula: un texto que empiece con «=», «+», «-» o «@» se guarda como TEXTO (si no, un nombre de
grupo, una nota o el nombre que el usuario le puso a un campo calculado con «=» se ejecutaría al abrir el archivo).
"""
import datetime
import io
import math
import re

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import Outline, PageSetupProperties

_PELIGROSO = ("=", "+", "-", "@", "\t", "\r")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")          # Excel no admite estos caracteres (el tabulador y el salto de línea sí): `openpyxl` levanta si se los escribe
ANCHO_MAX = 42
SIN_DATO = "(sin dato)"
SIN_GRUPO = "Sin grupo identificado"
_SIN_CLAVE = ("\x00",)                   # la clave de una fila de la que no se sabe el grupo: no iguala a ningún grupo real

# Cómo se nombra cada resumen: corto, dentro del número («Prom. 27,5»), y largo, en el encabezado de la hoja de solo resumen.
_CORTO = {"sum": "Suma", "avg": "Prom.", "min": "Mín.", "max": "Máx.", "count": "Conteo"}
_LARGO = {"sum": "Suma", "avg": "Promedio", "min": "Mínimo", "max": "Máximo", "count": "Conteo"}

_AZUL = "16232E"
_CABECERA = PatternFill("solid", fgColor=_AZUL)
_FONDO_NIVEL = {1: PatternFill("solid", fgColor="D5E3F0"), 2: PatternFill("solid", fgColor="E9F0F7")}
_BORDE_TOTAL = Border(top=Side(style="medium", color=_AZUL))


def _texto(ws, fila: int, col: int, valor: str):
    valor = _CONTROL.sub("", valor)                       # un texto pegado desde Word puede traer un tabulador vertical: no tiene que tumbar la exportación (ni el envío programado)
    c = ws.cell(row=fila, column=col, value=valor)
    if valor and valor[0] in _PELIGROSO:
        c.data_type = "s"          # texto aunque empiece con «=»
    return c


def _formato_num(decimales: int) -> str:
    return "#,##0" + ("." + "0" * decimales if decimales > 0 else "")


def _fecha(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v if isinstance(v, datetime.datetime) else datetime.datetime(v.year, v.month, v.day)
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", ""))
    except ValueError:
        return None


def _es_numero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _miles(n) -> str:
    return f"{int(n or 0):,}".replace(",", ".")


def _animales(n) -> str:
    return f"{_miles(n)} animal" if n == 1 else f"{_miles(n)} animales"


def _etiqueta(c: dict) -> str:
    return c["etiqueta"] + (f" ({c['unidad']})" if c.get("unidad") else "")


def _meta_resumen(c: dict, kind: str) -> tuple:
    """(tipo, decimales) de la cifra de un resumen: un conteo es siempre un entero, un promedio lleva al menos un decimal (el
    promedio de días enteros casi nunca es entero) y lo demás conserva el tipo y los decimales de su columna."""
    dec = c.get("decimales", 0)
    if kind == "count":
        return "num", 0
    if kind == "avg":
        return "num", max(dec, 1)
    return c["tipo"], dec


def _celda(ws, fila: int, col: int, tipo: str, decimales: int, v, prefijo: str = "") -> int:
    """Escribe `v` con su tipo real (número, fecha o texto) y devuelve el largo que ocupa, para el ancho de la columna. Un vacío
    no escribe nada. `prefijo` se muestra delante de la cifra («Prom. 27,5») sin dejar de ser un número."""
    if v is None:
        return 0
    pre = f'"{prefijo} "' if prefijo else ""
    extra = len(prefijo) + 1 if prefijo else 0
    if tipo == "num" and isinstance(v, (int, float)) and not isinstance(v, bool):
        if not math.isfinite(v):
            return 0                 # un NaN o un infinito corrompería el archivo
        cel = ws.cell(row=fila, column=col, value=float(v))
        cel.number_format = pre + _formato_num(decimales)
        return len(f"{v:,.{decimales}f}") + extra
    f = _fecha(v) if tipo == "fecha" else None
    if f is not None:
        cel = ws.cell(row=fila, column=col, value=f)
        cel.number_format = pre + "dd/mm/yyyy"
        return 10 + extra
    s = str(v)
    _texto(ws, fila, col, s)
    return len(s)


def _cabecera(ws, textos: list, derechas: list) -> list:
    """La fila 1. Devuelve el largo de cada encabezado (el ancho mínimo de su columna)."""
    for j, (t, der) in enumerate(zip(textos, derechas), start=1):
        c = _texto(ws, 1, j, t)
        c.font = Font(bold=True, color="EAF1F5")
        c.fill = _CABECERA
        c.alignment = Alignment(wrap_text=True, vertical="bottom", horizontal="right" if der else "left")
    return [len(t) for t in textos]


def _anchos(ws, anchos: list) -> None:
    for j, a in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(j)].width = min(ANCHO_MAX, max(10, a + 2))


def _texto_clave(v, meta: dict) -> str:
    """Cómo se nombra un valor de agrupación en un encabezado de grupo."""
    if v is None:
        return SIN_DATO
    if meta.get("tipo") == "fecha":
        f = _fecha(v)
        return f.strftime("%d/%m/%Y") if f else str(v)
    if _es_numero(v):
        if float(v).is_integer():
            return str(int(v))
        return f"{v:.{max(meta.get('decimales', 0), 1)}f}".replace(".", ",")
    return str(v)


def _norm(v):
    """La clave de un grupo tal como la compara SQL Server al agrupar: sin distinguir mayúsculas ni espacios al final, y 2 igual a
    2.0. El detalle trae cada grafía tal cual está en la base; el grupo, una sola."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        f = _fecha(v)
        return f.isoformat()
    return str(v).rstrip().casefold()


def _items(r: dict) -> list:
    """Lo que va en la hoja de detalle, en orden: ('grupo', g), ('fila', valores) y ('sin_grupo', None).

    Las filas se reparten por su clave de grupo, y los grupos salen en el orden de `grupos` (el que arma el motor: «Rodeo 2» antes
    que «Rodeo 10»), no en el que devolvió SQL. Una fila cuyo grupo no figura no se pierde: va al final, bajo su propio encabezado."""
    filas, agr = r["filas"], r.get("agrupar") or []
    if not agr:
        return [("fila", f) for f in filas]
    n, claves, por_clave = len(agr), r.get("claves_filas") or [], {}
    for i, f in enumerate(filas):
        k = claves[i] if i < len(claves) else None
        por_clave.setdefault(tuple(_norm(v) for v in k[:n]) if k else _SIN_CLAVE, []).append(f)
    items, usadas = [], set()
    for g in r.get("grupos") or []:
        items.append(("grupo", g))
        if g["nivel"] == n:
            clave = tuple(_norm(v) for v in g["claves"])
            usadas.add(clave)
            items += [("fila", f) for f in por_clave.get(clave, [])]
    resto = [f for k, fs in por_clave.items() if k not in usadas for f in fs]
    if resto:
        items.append(("sin_grupo", None))
        items += [("fila", f) for f in resto]
    return items


def _cifras(ws, fila: int, desde: int, cols: list, valores: dict, anchos: list) -> None:
    """Las cifras de resumen de una fila de grupo o de totales, cada una en la columna de su campo y con el nombre del resumen."""
    for j, c in enumerate(cols):
        kind = c.get("resumen", "none")
        if kind == "none":
            continue
        tipo, dec = _meta_resumen(c, kind)
        largo = _celda(ws, fila, desde + j + 1, tipo, dec, valores.get(c.get("alias")), _CORTO[kind])
        anchos[desde + j] = max(anchos[desde + j], largo)


def _estilo_fila(ws, fila: int, n_cols: int, fondo=None, negrita: bool = False, borde=None) -> None:
    for j in range(1, n_cols + 1):
        c = ws.cell(row=fila, column=j)
        if negrita:
            c.font = Font(bold=True)
        if fondo is not None:
            c.fill = fondo
        if borde is not None:
            c.border = borde


def _hoja_detalle(ws, r: dict) -> None:
    """Un animal por fila; con grupos, un encabezado por grupo; con resúmenes, una fila de totales."""
    cols, agr, tot = r["columnas"], r.get("agrupar") or [], r.get("totales")
    con_rotulos = bool(agr) or tot is not None
    d = 1 if con_rotulos else 0                       # las columnas de datos empiezan en la d+1
    textos = (["Grupo" if agr else ""] if con_rotulos else []) + [_etiqueta(c) for c in cols]
    derechas = ([False] if con_rotulos else []) + [c["tipo"] == "num" for c in cols]
    anchos = _cabecera(ws, textos, derechas)
    n_cols, fila = len(textos), 1
    for clase, x in _items(r):
        fila += 1
        if clase == "grupo":
            nivel, meta = x["nivel"], agr[x["nivel"] - 1]
            rotulo = f"{meta['etiqueta']}: {_texto_clave(x['claves'][-1], meta)} · {_animales(x['n'])}"
            _texto(ws, fila, 1, rotulo)
            _cifras(ws, fila, d, cols, x.get("resumenes") or {}, anchos)
            _estilo_fila(ws, fila, n_cols, _FONDO_NIVEL.get(nivel, _FONDO_NIVEL[2]), True)
            ws.cell(row=fila, column=1).alignment = Alignment(indent=nivel - 1)
            if nivel > 1:
                ws.row_dimensions[fila].outlineLevel = nivel - 1
            anchos[0] = max(anchos[0], len(rotulo) + 2 * (nivel - 1))
        elif clase == "sin_grupo":
            _texto(ws, fila, 1, SIN_GRUPO)
            _estilo_fila(ws, fila, n_cols, _FONDO_NIVEL[1], True)
            anchos[0] = max(anchos[0], len(SIN_GRUPO))
        else:
            for j, (c, v) in enumerate(zip(cols, x)):
                anchos[d + j] = max(anchos[d + j], _celda(ws, fila, d + j + 1, c["tipo"], c.get("decimales", 0), v))
            if agr:
                ws.row_dimensions[fila].outlineLevel = len(agr)
    if tot is not None:
        fila += 1
        rotulo = f"Total · {_animales(tot['n'])}"
        _texto(ws, fila, 1, rotulo)
        _cifras(ws, fila, d, cols, tot.get("resumenes") or {}, anchos)
        _estilo_fila(ws, fila, n_cols, _FONDO_NIVEL[1], True, _BORDE_TOTAL)
        anchos[0] = max(anchos[0], len(rotulo))
    _anchos(ws, anchos)
    ws.freeze_panes = "A2"
    if agr:
        ws.sheet_properties.outlinePr = Outline(summaryBelow=False)       # el «−» queda en el encabezado del grupo, no abajo
    elif cols:
        # Ordenar con el filtro no debe arrastrar la fila de totales ni la columna de rótulos: se acota a los animales.
        ws.auto_filter.ref = f"{get_column_letter(d + 1)}1:{get_column_letter(n_cols)}{max(1, len(r['filas']) + 1)}"


def _celda_clave(ws, fila: int, col: int, v, meta) -> int:
    """La clave de un grupo en la hoja de solo resumen. `meta` None = un rótulo («Subtotal», «Total»)."""
    if meta is None:
        _texto(ws, fila, col, v)
        return len(v)
    if v is None:
        _texto(ws, fila, col, SIN_DATO)
        return len(SIN_DATO)
    dec = meta.get("decimales", 0)
    largo = _celda(ws, fila, col, meta["tipo"], dec, v)
    if meta["tipo"] == "num" and _es_numero(v):
        ws.cell(row=fila, column=col).number_format = "0" + ("." + "0" * dec if dec > 0 else "")     # un código no lleva separador de miles
    return largo


def _hoja_resumen(ws, r: dict) -> None:
    """Una fila por grupo y subgrupo con la cantidad de animales y los resúmenes; sin la lista de animales."""
    cols, agr = r["columnas"], r.get("agrupar") or []
    tot = r.get("totales") or {"n": 0, "resumenes": {}}
    con = [c for c in cols if c.get("resumen", "none") != "none"]
    nk = len(agr)
    textos = [g["etiqueta"] for g in agr] + ["Animales"] + [f"{_etiqueta(c)} · {_LARGO[c['resumen']]}" for c in con]
    derechas = ([g["tipo"] == "num" for g in agr] + [True] +
                [_meta_resumen(c, c["resumen"])[0] == "num" for c in con])
    anchos = _cabecera(ws, textos, derechas)
    n_cols = len(textos)

    def escribir(fila, rotulos, n, valores):
        for j, (v, meta) in enumerate(rotulos):
            anchos[j] = max(anchos[j], _celda_clave(ws, fila, j + 1, v, meta))     # una clave None es el grupo «(sin dato)»
        anchos[nk] = max(anchos[nk], _celda(ws, fila, nk + 1, "num", 0, n))
        for k, c in enumerate(con):
            tipo, dec = _meta_resumen(c, c["resumen"])
            anchos[nk + 1 + k] = max(anchos[nk + 1 + k], _celda(ws, fila, nk + 2 + k, tipo, dec, valores.get(c.get("alias"))))

    fila = 1
    for g in r.get("grupos") or []:
        fila += 1
        rotulos = [(v, agr[k]) for k, v in enumerate(g["claves"])]
        subtotal = g["nivel"] < nk                                          # un grupo de primer nivel que tiene subgrupos
        if subtotal:
            rotulos.append(("Subtotal", None))
        escribir(fila, rotulos, g["n"], g.get("resumenes") or {})
        if subtotal:
            _estilo_fila(ws, fila, n_cols, _FONDO_NIVEL[2], True)
    fila += 1
    escribir(fila, [("Total", None)] if nk else [], tot["n"], tot.get("resumenes") or {})
    _estilo_fila(ws, fila, n_cols, _FONDO_NIVEL[1], True, _BORDE_TOTAL)
    _anchos(ws, anchos)
    ws.freeze_panes = "A2"
    if nk == 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{fila - 1}"      # los grupos se ordenan; el total queda afuera


def _impresion(ws) -> None:
    """Para imprimir un informe largo: apaisado, todas las columnas en una hoja de ancho y el encabezado en cada página."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_title_rows = "1:1"


def _hoja_condiciones(wb, resultado: dict, nombre: str, condiciones, generado) -> None:
    info = wb.create_sheet("Condiciones")
    info.column_dimensions["A"].width = 26
    info.column_dimensions["B"].width = 70
    generado = generado or datetime.datetime.now()
    tot = resultado.get("totales")
    filas_info = [("Informe", nombre), ("Generado", generado.strftime("%d/%m/%Y %H:%M"))]
    if resultado.get("detalle", True):
        filas_info.append(("Filas", len(resultado["filas"])))
    if tot is not None:
        filas_info.append(("Animales en el informe", tot["n"]))
    for k, (a, b) in enumerate(filas_info, start=1):
        info.cell(row=k, column=1, value=a).font = Font(bold=True)
        _texto(info, k, 2, str(b)) if isinstance(b, str) else info.cell(row=k, column=2, value=b)
    r = len(filas_info) + 2
    info.cell(row=r, column=1, value="Condiciones").font = Font(bold=True)
    for k, c in enumerate(condiciones or ["(ninguna)"]):
        _texto(info, r + k, 2, str(c))
    if resultado.get("truncado"):
        n = r + len(condiciones or ["x"]) + 1
        info.cell(row=n, column=1, value="Aviso").font = Font(bold=True, color="B00020")
        aviso = "La lista se cortó en el tope de filas: hay más animales que los que figuran acá."
        if tot is not None:
            aviso += " Los totales y resúmenes cuentan a todos los animales del informe."
        info.cell(row=n, column=2, value=aviso)


def a_xlsx(resultado: dict, nombre: str, condiciones=None, generado=None) -> bytes:
    """`resultado` = lo que devuelve `informes_motor.ejecutar` ({columnas, filas, truncado, detalle, agrupar, claves_filas, grupos,
    totales}). Los últimos cinco son de la Fase 2 y son opcionales: sin ellos el informe es una lista simple."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Informe"
    if resultado.get("detalle", True):
        _hoja_detalle(ws, resultado)
    else:
        _hoja_resumen(ws, resultado)
    _impresion(ws)
    _hoja_condiciones(wb, resultado, nombre, condiciones, generado)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def nombre_de_archivo(nombre: str, dia: datetime.date) -> str:
    """Un nombre de archivo sin caracteres que Windows no admita."""
    limpio = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", nombre or "")
    limpio = re.sub(r"\s+", " ", limpio).strip().strip(".")
    return f"{limpio or 'Informe'} {dia.isoformat()}.xlsx"
