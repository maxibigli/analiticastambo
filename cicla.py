# -*- coding: utf-8 -*-
"""Integración con CICLA/SISCLAC (http://cicla.sisclac.com): informe de cargas
del caudalímetro instalado en la rotativa (equipo CER_001, responsable
Cereigido) para comparar litros medidos por CICLA vs los declarados, y la
temperatura de entrega, y así detectar desvíos frente al proveedor.

Credenciales SOLO por variable de entorno (nunca en el código):
  CICLA_USUARIO, CICLA_PASSWORD

Formulario ASP.NET WebForms verificado a mano (2026-07-23):
  - Login: POST a Application/Login/Default.aspx con
    ctl00$MainContent$txtUser / ctl00$MainContent$txtpass.
  - Informe: GET Application/Informes/EquiposTambo/, filtro por
    __EVENTTARGET=ctl00$MainContent$btnBuscar (es un <a> con __doPostBack,
    no un input submit) + rango de fechas dd/mm/aaaa.
  - Tabla de detalle: <table id="ctl00_MainContent_grdGrillaCargas"> con
    columnas Nro Turno, Nro Carga, Fecha Carga, Cliente, Equipo, Remito,
    Nro Cliente, Lts Cicla, Lts Declarados, Diferencia (LC-LD), Temp. Prom.,
    PH Prom.
"""
import datetime
import re
import threading

import requests
from bs4 import BeautifulSoup

_RE_PAGINA = re.compile(r"Page\$(\d+)")

BASE = "http://cicla.sisclac.com"
REPORTE_URL = BASE + "/Application/Informes/EquiposTambo/"

UMBRAL_DIF_PCT = 3.0   # % de diferencia Lts Cicla vs Declarados que dispara alerta
UMBRAL_TEMP_C = 4.0    # temperatura de entrega (°C) que dispara alerta

# Serializa el acceso: un solo login/consulta a CICLA a la vez, para no
# golpear su servidor con pedidos concurrentes.
_lock = threading.Lock()


class CiclaError(Exception):
    pass


def _campos_ocultos(form) -> dict:
    d = {}
    for inp in form.find_all("input"):
        nombre = inp.get("name")
        if nombre:
            d[nombre] = inp.get("value") or ""
    return d


def _num(txt):
    """'20.374' o '8,19' (formato AR) -> float. Vacío -> None."""
    if txt is None:
        return None
    txt = txt.strip().replace(".", "").replace(",", ".")
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _iniciar_sesion(usuario: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    r1 = s.get(BASE, timeout=20)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    form1 = soup1.find("form")
    if form1 is None:
        raise CiclaError("No se pudo leer la página de login de CICLA.")
    datos = _campos_ocultos(form1)
    datos["ctl00$MainContent$txtUser"] = usuario
    datos["ctl00$MainContent$txtpass"] = password
    url_post = requests.compat.urljoin(r1.url, form1.get("action") or "")
    r2 = s.post(url_post, data=datos, timeout=20)
    if "txtpass" in r2.text:
        raise CiclaError("CICLA rechazó el usuario/contraseña (revisá CICLA_USUARIO/CICLA_PASSWORD).")
    return s


def _filas_de_pagina(soup):
    """Filas de datos reales de la grilla (excluye encabezado y la fila del
    paginador, que trae un <td colspan> sin las 12+ columnas esperadas)."""
    tabla = soup.find("table", {"id": "ctl00_MainContent_grdGrillaCargas"})
    if tabla is None:
        return [], None
    return tabla.find_all("tr")[1:], tabla


def _paginas_disponibles(soup):
    """Números de página que aparecen como link __doPostBack(...,'Page$N')."""
    pags = set()
    for a in soup.find_all("a", href=True):
        m = _RE_PAGINA.search(a["href"])
        if m:
            pags.add(int(m.group(1)))
    return pags


def obtener_cargas(desde: datetime.date, hasta: datetime.date, usuario: str, password: str) -> tuple[list[dict], bool]:
    """Trae y parsea el detalle de cargas de CICLA entre dos fechas (incluidas).

    La grilla pagina de a ~40 filas. Se intentó seguir las páginas siguientes
    (postback __doPostBack(grdGrillaCargas, 'Page$N')) pero el sitio devuelve
    la página vacía y luego un error 500 propio de CICLA — no es algo que
    convenga forzar contra su servidor. Por eso se trae SOLO la primera
    página y se avisa con el segundo valor (incompleto=True) si había más
    páginas disponibles, en vez de fingir que el resultado está completo.
    Para rangos largos, conviene pedir de a semanas."""
    with _lock:
        s = _iniciar_sesion(usuario, password)

        r3 = s.get(REPORTE_URL, timeout=20)
        soup3 = BeautifulSoup(r3.text, "html.parser")
        form3 = soup3.find("form")
        if form3 is None:
            raise CiclaError("No se pudo leer el informe de CICLA (¿sesión vencida?).")

        datos3 = _campos_ocultos(form3)
        datos3["ctl00$MainContent$txtFiltroFechaDesde"] = desde.strftime("%d/%m/%Y")
        datos3["ctl00$MainContent$txtFiltroFechaHasta"] = hasta.strftime("%d/%m/%Y")
        datos3["ctl00$MainContent$ddlFiltroCliente"] = "21"  # Cereigido = La Ponderosa
        datos3["ctl00$MainContent$ddlFiltroEquipo"] = "0"    # todos los equipos de ese cliente
        datos3["__EVENTTARGET"] = "ctl00$MainContent$btnBuscar"
        datos3["__EVENTARGUMENT"] = ""
        url_post = requests.compat.urljoin(r3.url, form3.get("action") or "")
        r4 = s.post(url_post, data=datos3, timeout=30)
        soup = BeautifulSoup(r4.text, "html.parser")

        todas_las_filas, _tabla = _filas_de_pagina(soup)
        incompleto = bool(_paginas_disponibles(soup))

    cargas = []
    for tr in todas_las_filas:
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 12:
            continue
        lts_cicla, lts_decl = _num(tds[7]), _num(tds[8])
        diferencia, temp = _num(tds[9]), _num(tds[10])
        dif_pct = round(diferencia / lts_decl * 100, 1) if diferencia is not None and lts_decl else None
        cargas.append({
            "turno": tds[0], "carga": tds[1], "fecha": tds[2], "cliente": tds[3],
            "equipo": tds[4], "remito": tds[5], "nro_cliente": tds[6],
            "lts_cicla": lts_cicla, "lts_declarados": lts_decl, "diferencia": diferencia,
            "diferencia_pct": dif_pct, "temperatura": temp,
            "ph": _num(tds[11]) if len(tds) > 11 else None,
            "alerta_diferencia": dif_pct is not None and abs(dif_pct) > UMBRAL_DIF_PCT,
            "alerta_temp": temp is not None and temp > UMBRAL_TEMP_C,
        })
    return cargas, incompleto


MAX_PCT_PROMEDIABLE = 100  # cargas con % disparatado (carga parcial/error) igual
                           # se marcan como alerta, pero no arruinan el promedio


def resumen(cargas: list[dict]) -> dict:
    con_dif = [c for c in cargas if c["diferencia_pct"] is not None]
    razonables = [c for c in con_dif if abs(c["diferencia_pct"]) <= MAX_PCT_PROMEDIABLE]
    con_temp = [c for c in cargas if c["temperatura"] is not None]
    return {
        "total_cargas": len(cargas),
        "alertas_diferencia": sum(1 for c in cargas if c["alerta_diferencia"]),
        "alertas_temperatura": sum(1 for c in cargas if c["alerta_temp"]),
        "dif_pct_promedio": round(sum(c["diferencia_pct"] for c in razonables) / len(razonables), 1) if razonables else None,
        "temp_promedio": round(sum(c["temperatura"] for c in con_temp) / len(con_temp), 1) if con_temp else None,
    }
