"""Exporta un informe a Excel (.xlsx).

Dos hojas: «Informe» (los datos, con números y fechas de verdad para poder ordenar y sumar en Excel) y «Condiciones» (qué
informe es, cuándo se generó y con qué filtros, y si se cortó por el tope de filas).

Ningún valor se escribe como fórmula: un texto que empiece con «=», «+», «-» o «@» se guarda como TEXTO (si no, un nombre de
grupo o una nota con «=» se ejecutaría al abrir el archivo).
"""
import datetime
import io
import re

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_PELIGROSO = ("=", "+", "-", "@", "\t", "\r")
ANCHO_MAX = 42


def _texto(ws, fila: int, col: int, valor: str):
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


def a_xlsx(resultado: dict, nombre: str, condiciones=None, generado=None) -> bytes:
    """`resultado` = lo que devuelve `informes_motor.ejecutar` ({columnas, filas, truncado})."""
    cols, filas = resultado["columnas"], resultado["filas"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Informe"
    cabecera = PatternFill("solid", fgColor="16232E")
    for j, c in enumerate(cols, start=1):
        celda = ws.cell(row=1, column=j, value=c["etiqueta"] + (f" ({c['unidad']})" if c.get("unidad") else ""))
        celda.font = Font(bold=True, color="EAF1F5")
        celda.fill = cabecera
        celda.alignment = Alignment(wrap_text=True, vertical="bottom", horizontal="right" if c["tipo"] == "num" else "left")
    anchos = [len(ws.cell(row=1, column=j).value or "") for j in range(1, len(cols) + 1)]
    for i, fila in enumerate(filas, start=2):
        for j, (c, v) in enumerate(zip(cols, fila), start=1):
            if v is None:
                continue
            if c["tipo"] == "num" and isinstance(v, (int, float)) and not isinstance(v, bool):
                cel = ws.cell(row=i, column=j, value=float(v))
                cel.number_format = _formato_num(c.get("decimales", 0))
                largo = len(f"{v:,.{c.get('decimales', 0)}f}")
            elif c["tipo"] == "fecha" and _fecha(v) is not None:
                cel = ws.cell(row=i, column=j, value=_fecha(v))
                cel.number_format = "dd/mm/yyyy"
                largo = 10
            else:
                s = str(v)
                _texto(ws, i, j, s)
                largo = len(s)
            anchos[j - 1] = max(anchos[j - 1], largo)
    for j, a in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(j)].width = min(ANCHO_MAX, max(10, a + 2))
    ws.freeze_panes = "A2"
    if cols:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(1, len(filas) + 1)}"

    info = wb.create_sheet("Condiciones")
    info.column_dimensions["A"].width = 26
    info.column_dimensions["B"].width = 70
    generado = generado or datetime.datetime.now()
    filas_info = [("Informe", nombre), ("Generado", generado.strftime("%d/%m/%Y %H:%M")),
                  ("Filas", len(filas))]
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
        info.cell(row=n, column=2, value="La lista se cortó en el tope de filas: hay más animales que los que figuran acá.")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def nombre_de_archivo(nombre: str, dia: datetime.date) -> str:
    """Un nombre de archivo sin caracteres que Windows no admita."""
    limpio = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", nombre or "")
    limpio = re.sub(r"\s+", " ", limpio).strip().strip(".")
    return f"{limpio or 'Informe'} {dia.isoformat()}.xlsx"
