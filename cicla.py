# -*- coding: utf-8 -*-
"""Integración con el caudalímetro de la rotativa (equipo CER_001, cliente 21,
Cereigido): cargas con litros medidos, litros declarados y temperatura de
entrega, para detectar desvíos frente al proveedor.

DESDE 07/11/2026 SE LEE DE LA API DE MILKY (https://milky.controllac.com.ar), no
del sitio viejo. El sitio viejo (cicla.sisclac.com, scraping de un formulario
ASP.NET) dejó de servir el informe: el login pasa pero la página de cargas ya no
se puede leer. Su código sigue más abajo como `obtener_cargas_sitio_viejo`, sin
usarse, por si hay que consultarlo.

Credenciales SOLO por variable de entorno (nunca en el código):
  MILKY_USUARIO, MILKY_PASSWORD
Se leen del entorno del proceso y, si no están, del REGISTRO de Windows (donde las deja `setx`):
así una variable recién cargada se ve sin reiniciar el servidor (ver `_variable`).
(NO se reusan CICLA_USUARIO/CICLA_PASSWORD: son del sitio viejo, y mandarlas a otro
servidor sería regalarle una credencial a quien no corresponde.)

API de Milky (documento "APIs compartidas v1.1"):
  - POST /api/Auth/Login {userName, password} -> access_token (bearer, dura 8 h).
  - GET  /api/Query/Cargas?fechaDesde&fechaHasta&page&pageSize, header
    Authorization: Bearer <token>. Cada usuario ve solo lo de su cliente.
  - Las cargas vienen paginadas (items, total, page, pageSize): se recorren TODAS, así
    que ya no existe el "incompleto" del sitio viejo, que no dejaba pasar de la 1ª página.

CÓMO SE TRADUCE CADA CAMPO al formato que ya usaba la app (verificado contra 126
cargas reales del 17/10 al 07/11/2026):
  lts_cicla        <- LitrosTotal. LitrosFact es IGUAL en las 126 cargas (dicen lo mismo).
  lts_declarados   <- LitrosEstimados. SUPUESTO SIN VERIFICAR: el sitio viejo ya no
                      responde y no se pudo comparar una misma carga en los dos. Son
                      números redondos (25.000/26.000/27.000, o 500 en la guachera), o
                      sea lo que se estima al cargar, no una medición. Consecuencia
                      medida: contra eso, las cargas de camión dan mediana -7% y las de
                      guachera +31%, así que con el umbral de 3% casi todas salen con
                      "alerta de diferencia". Si "declarados" era otra cosa, esas
                      alertas y el promedio de diferencia no sirven.
  nro_cliente      <- Tambo (mismos códigos 5003/4613/23 que ya mapeaba DESTINOS).
  temperatura      <- TempPromedio.
  ph               <- no existe en la API: queda en None.
  fecha            <- FechaIni (es la que filtra la API) como 'dd/mm/aaaa hh:mm:ss'.
  carga            <- Id de la carga en Milky (el "Nro Carga" viejo era otra numeración).
  Las cargas con Anulado = 1 se descartan.

Formulario ASP.NET WebForms del sitio viejo, verificado a mano (2026-07-23):
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
import os
import re
import sys
import threading
import time

import requests
from bs4 import BeautifulSoup

_RE_PAGINA = re.compile(r"Page\$(\d+)")

BASE = "http://cicla.sisclac.com"
REPORTE_URL = BASE + "/Application/Informes/EquiposTambo/"

UMBRAL_DIF_PCT = 3.0   # % de diferencia Lts Cicla vs Declarados que dispara alerta
UMBRAL_TEMP_C = 4.0    # temperatura de entrega (°C) que dispara alerta

# --- Destino de cada carga ---------------------------------------------------
# No todas las cargas que mide el caudalímetro se venden: algunas van a la
# guachera (leche para los terneros). Para comparar contra la liquidación del
# comprador hay que quedarse SOLO con las que salen a La Serenísima.
#
# CICLA no guarda el nombre del destino: lo único que trae es el código
# numérico de la columna "Nro Cliente". Por eso el mapeo vive acá.
#
# Los códigos se identificaron por el tamaño de carga (3 semanas de julio 2026):
#   5003 → 33 cargas, promedio  9.287 lts
#   4613 → 31 cargas, promedio  8.724 lts
#    702 →  7 cargas, promedio  5.389 lts
#     23 → 31 cargas, promedio    653 lts   ← carga de guachera
#     22 → 18 cargas, promedio    742 lts   ← carga de guachera
# y el reparto entre venta y guachera lo confirmó el tambo.
DESTINOS = {
    "5003": "serenisima",
    "4613": "serenisima",
    "702": "serenisima",
    "22": "guachera",
    "23": "guachera",
}

# Un código que no esté en el mapeo NO se cuenta como venta. Es a propósito:
# sumarlo por defecto inflaría la comparación contra el comprador y taparía
# justamente el problema que se quiere detectar. Aparecen listados en el
# resumen (`destinos_sin_clasificar`) para poder agregarlos acá.
DESTINO_DESCONOCIDO = "sin_clasificar"

ETIQUETAS_DESTINO = {
    "serenisima": "La Serenísima",
    "guachera": "Guachera",
    DESTINO_DESCONOCIDO: "Sin clasificar",
}


def destino_de(nro_cliente) -> str:
    """Destino de una carga según su 'Nro Cliente' de CICLA."""
    return DESTINOS.get(str(nro_cliente or "").strip(), DESTINO_DESCONOCIDO)

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


def obtener_cargas_sitio_viejo(desde: datetime.date, hasta: datetime.date, usuario: str, password: str) -> tuple[list[dict], bool]:
    """[YA NO SE USA — el sitio viejo no sirve más el informe; ver el docstring del módulo.]
    Trae y parsea el detalle de cargas de CICLA entre dos fechas (incluidas).

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
        destino = destino_de(tds[6])
        cargas.append({
            "turno": tds[0], "carga": tds[1], "fecha": tds[2], "cliente": tds[3],
            "equipo": tds[4], "remito": tds[5], "nro_cliente": tds[6],
            "destino": destino,
            "destino_label": ETIQUETAS_DESTINO.get(destino, destino),
            "es_venta": destino == "serenisima",
            "lts_cicla": lts_cicla, "lts_declarados": lts_decl, "diferencia": diferencia,
            "diferencia_pct": dif_pct, "temperatura": temp,
            "ph": _num(tds[11]) if len(tds) > 11 else None,
            "alerta_diferencia": dif_pct is not None and abs(dif_pct) > UMBRAL_DIF_PCT,
            "alerta_temp": temp is not None and temp > UMBRAL_TEMP_C,
        })
    return cargas, incompleto


# --- API de Milky --------------------------------------------------------------

MILKY_BASE = "https://milky.controllac.com.ar"
PAGE_SIZE = 200          # registros por página que se piden (el defecto de la API es 50)
MAX_PAGINAS = 50         # tope de seguridad: 10.000 cargas por consulta, mucho más que cualquier rango real
TOKEN_VIDA_S = 7.5 * 3600   # el token dura 8 h: se renueva un poco antes de que venza
TIMEOUT_S = 30

_token_lock = threading.Lock()
_token_cache = {"usuario": None, "token": None, "vence": 0.0}


def _variable(nombre: str) -> str | None:
    """Valor de una variable de entorno, primero del proceso y si no está, del REGISTRO de Windows.

    `setx` guarda la variable en el registro pero NO la agrega a los procesos que ya existen, ni a
    los que lance una ventana, un acceso directo o una tarea anterior al `setx`: los procesos
    heredan el entorno de quien los crea. En SERVER-DELPRO eso pasó en la práctica (07/11/2026): el
    servidor arrancó sin ver MILKY_USUARIO/MILKY_PASSWORD aunque estaban bien guardadas, y hubo que
    matarlo y relanzarlo copiando las variables a mano. Leyendo el registro cada vez, una variable
    cargada con `setx` se ve al toque, sin reiniciar nada.

    Orden: el entorno del proceso manda (sirve para pisar el valor en una prueba), después las del
    usuario (HKCU\\Environment, lo que escribe `setx`) y por último las de la máquina
    (`setx /M`). OJO: si el servidor corre con OTRO usuario de Windows que el que hizo el `setx`,
    el registro de ese otro usuario no la tiene: ahí hace falta `setx /M` o repetirlo con ese usuario."""
    valor = os.environ.get(nombre)
    if valor:
        return valor
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    ubicaciones = ((winreg.HKEY_CURRENT_USER, "Environment"),
                   (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
    for raiz, ruta in ubicaciones:
        try:
            with winreg.OpenKey(raiz, ruta) as clave:
                valor, tipo = winreg.QueryValueEx(clave, nombre)
        except OSError:
            continue   # no existe en esta ubicación (o no se puede leer): se prueba la siguiente
        if tipo == winreg.REG_EXPAND_SZ:
            valor = os.path.expandvars(valor)
        if valor:
            return str(valor)
    return None


def credenciales() -> tuple[str | None, str | None]:
    """(usuario, contraseña) de la API de Milky, solo de las variables MILKY_* (ver `_variable`)."""
    return _variable("MILKY_USUARIO"), _variable("MILKY_PASSWORD")


def _mensaje_error(r) -> str:
    try:
        d = r.json()
        return str(d.get("detail") or d.get("message") or d)[:200]
    except Exception:  # noqa: BLE001
        return (r.text or "")[:200]


def _token(usuario: str, password: str, forzar: bool = False) -> str:
    """Token vigente, reutilizado hasta que vence (la API pide no loguearse por consulta)."""
    with _token_lock:
        c = _token_cache
        if (not forzar and c["token"] and c["usuario"] == usuario and time.time() < c["vence"]):
            return c["token"]
        try:
            r = requests.post(MILKY_BASE + "/api/Auth/Login",
                              json={"userName": usuario, "password": password}, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise CiclaError(f"No se pudo conectar con Milky: {exc}") from exc
        if r.status_code == 401:
            raise CiclaError("Milky rechazó el usuario/contraseña (revisá MILKY_USUARIO/MILKY_PASSWORD).")
        if r.status_code != 200:
            raise CiclaError(f"Milky respondió {r.status_code} al iniciar sesión: {_mensaje_error(r)}")
        token = (r.json() or {}).get("access_token")
        if not token:
            raise CiclaError("Milky no devolvió un token de acceso.")
        c.update(usuario=usuario, token=token, vence=time.time() + TOKEN_VIDA_S)
        return token


def _get(path: str, params: dict, usuario: str, password: str) -> dict:
    """GET autenticado. Si el token fue rechazado (venció o lo invalidaron) se pide uno nuevo UNA vez."""
    for intento in (0, 1):
        token = _token(usuario, password, forzar=bool(intento))
        try:
            r = requests.get(MILKY_BASE + path, params=params, timeout=TIMEOUT_S,
                             headers={"Authorization": "Bearer " + token})
        except requests.RequestException as exc:
            raise CiclaError(f"No se pudo conectar con Milky: {exc}") from exc
        if r.status_code == 401 and intento == 0:
            continue
        if r.status_code == 422:
            raise CiclaError(f"Milky no aceptó los parámetros: {_mensaje_error(r)}")
        if r.status_code != 200:
            raise CiclaError(f"Milky respondió {r.status_code}: {_mensaje_error(r)}")
        return r.json()
    raise CiclaError("Milky rechazó el token dos veces seguidas.")


def _fecha_ar(iso: str | None) -> str:
    """'2026-11-07T11:01:35' -> '07/11/2026 11:01:35' (el formato que la app ya espera: el día
    se toma con split(' ')[0] y se pasa a ISO con _fecha_ar_a_iso)."""
    if not iso:
        return ""
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return str(iso)


def _carga_desde_api(it: dict) -> dict:
    """Una carga de Milky con las claves que ya usaban `resumen()`, app.py y las pantallas."""
    lts_cicla = it.get("LitrosTotal")
    lts_decl = it.get("LitrosEstimados")
    diferencia = (lts_cicla - lts_decl) if lts_cicla is not None and lts_decl is not None else None
    dif_pct = round(diferencia / lts_decl * 100, 1) if diferencia is not None and lts_decl else None
    nro_cliente = str(it.get("Tambo") or "").strip()
    destino = destino_de(nro_cliente)
    temp = it.get("TempPromedio")
    return {
        "turno": "", "carga": str(it.get("Id") or ""), "fecha": _fecha_ar(it.get("FechaIni")),
        "cliente": str(it.get("IdCliente") or ""), "equipo": it.get("IdEq") or "",
        "remito": str(it.get("Remito") or ""), "nro_cliente": nro_cliente,
        "destino": destino,
        "destino_label": ETIQUETAS_DESTINO.get(destino, destino),
        "es_venta": destino == "serenisima",
        "lts_cicla": lts_cicla, "lts_declarados": lts_decl, "diferencia": diferencia,
        "diferencia_pct": dif_pct, "temperatura": temp,
        "ph": None,   # la API de Milky no lo trae
        "alerta_diferencia": dif_pct is not None and abs(dif_pct) > UMBRAL_DIF_PCT,
        "alerta_temp": temp is not None and temp > UMBRAL_TEMP_C,
    }


def obtener_cargas(desde: datetime.date, hasta: datetime.date, usuario: str, password: str) -> tuple[list[dict], bool]:
    """Cargas del caudalímetro entre dos fechas (incluidas), de la API de Milky.

    Devuelve (cargas, incompleto), la misma forma que tenía con el sitio viejo para no tocar a
    quien la llama. `incompleto` solo es True si se llegó al tope de seguridad de páginas sin
    cubrir `total` (no debería pasar); ya no hay "más páginas que no se pudieron traer".
    Una carga cuenta en el día en que EMPEZÓ (es el criterio del filtro de la API)."""
    if not usuario or not password:
        raise CiclaError("Faltan las variables de entorno MILKY_USUARIO / MILKY_PASSWORD (ver INSTALL.md).")
    params = {"fechaDesde": f"{desde.isoformat()}T00:00:00",
              "fechaHasta": f"{hasta.isoformat()}T23:59:59", "pageSize": PAGE_SIZE}
    items, total, incompleto = [], None, False
    with _lock:
        for pagina in range(1, MAX_PAGINAS + 1):
            d = _get("/api/Query/Cargas", {**params, "page": pagina}, usuario, password)
            lote = d.get("items") or []
            items.extend(lote)
            total = d.get("total", len(items))
            if not lote or len(items) >= total:
                break
        else:
            incompleto = len(items) < (total or 0)
    # Sin repetidas (si entra una carga nueva mientras se recorren las páginas, el corrimiento
    # puede mostrar una dos veces), sin anuladas, y en orden cronológico (la API devuelve de la
    # más reciente a la más antigua).
    unicas = {}
    for it in items:
        unicas[it.get("Id", id(it))] = it
    items = [it for it in unicas.values() if it.get("Anulado") != 1]
    items.sort(key=lambda it: it.get("FechaIni") or "")
    return [_carga_desde_api(it) for it in items], incompleto


def solo_ventas(cargas: list[dict]) -> list[dict]:
    """Cargas que salieron a La Serenísima — las únicas comparables contra la
    liquidación del comprador."""
    return [c for c in cargas if c.get("es_venta")]


MAX_PCT_PROMEDIABLE = 100  # cargas con % disparatado (carga parcial/error) igual
                           # se marcan como alerta, pero no arruinan el promedio


def resumen(cargas: list[dict]) -> dict:
    con_dif = [c for c in cargas if c["diferencia_pct"] is not None]
    razonables = [c for c in con_dif if abs(c["diferencia_pct"]) <= MAX_PCT_PROMEDIABLE]
    con_temp = [c for c in cargas if c["temperatura"] is not None]

    # Reparto por destino: cuánto de lo que midió el caudalímetro se vende y
    # cuánto se queda en el tambo.
    por_destino = {}
    for c in cargas:
        d = por_destino.setdefault(c.get("destino", DESTINO_DESCONOCIDO), {
            "destino": c.get("destino", DESTINO_DESCONOCIDO),
            "label": c.get("destino_label", "?"),
            "cargas": 0, "lts_cicla": 0.0, "codigos": set(),
        })
        d["cargas"] += 1
        d["lts_cicla"] += c["lts_cicla"] or 0
        d["codigos"].add(str(c.get("nro_cliente") or "?"))

    total_lts = sum(d["lts_cicla"] for d in por_destino.values())
    destinos = sorted(
        ({**d, "codigos": sorted(d["codigos"]),
          "pct": round(100.0 * d["lts_cicla"] / total_lts, 1) if total_lts else None}
         for d in por_destino.values()),
        key=lambda d: -d["lts_cicla"])

    ventas = solo_ventas(cargas)
    return {
        "total_cargas": len(cargas),
        "alertas_diferencia": sum(1 for c in cargas if c["alerta_diferencia"]),
        "alertas_temperatura": sum(1 for c in cargas if c["alerta_temp"]),
        "dif_pct_promedio": round(sum(c["diferencia_pct"] for c in razonables) / len(razonables), 1) if razonables else None,
        "temp_promedio": round(sum(c["temperatura"] for c in con_temp) / len(con_temp), 1) if con_temp else None,
        "lts_total": round(total_lts),
        "lts_venta": round(sum(c["lts_cicla"] or 0 for c in ventas)),
        "cargas_venta": len(ventas),
        "destinos": destinos,
        "destinos_sin_clasificar": sorted(
            {str(c.get("nro_cliente") or "?") for c in cargas
             if c.get("destino") == DESTINO_DESCONOCIDO}),
    }
