# -*- coding: utf-8 -*-
"""Proveedor de alimentación: Haasten (https://haasten.io).

API REST con JSON y token. Verificada a mano el 26/07/2026:

  - `POST /api/login` con `{username, password}` → `{token, user}`.
  - El token viaja en la cabecera `authorization` TAL CUAL, sin "Bearer".
  - **El login ya trae todo lo que hace falta para los lotes**: `user.devices`
    es la lista de equipos y cada equipo trae sus `lots`, su `sipnStock`
    (ingredientes) y su `sipnConfiguration` (categorías de lote). No hay que
    pedir nada más. Ojo: `GET /api/device/all` existe pero se cuelga —no usarlo.
  - `GET /api/deviceData/get/unloads/{serial}?minDate&maxDate` → descargas
    (kg entregados por lote). `.../loads/{serial}` → cargas por ingrediente.

CREDENCIALES: variables de entorno `HASTEN_USUARIO` y `HASTEN_PASSWORD`. Sí,
con UNA SOLA "a", aunque el sitio se llame "haasten" — así están seteadas en el
servidor y así hay que dejarlas. Nunca en el código.

QUÉ EQUIPO MIRA. La cuenta tiene cinco equipos y uno solo es el mixer: el SIP-N
"MIxer Camión Volvo LP" (serie 202616012). Los GAC son de otra cosa y el
DELPROSIPN no manda datos desde junio. Se toman los equipos de tipo mixer, sin
hardcodear la serie, para que siga andando si cambian el equipo.

LOTES ACTIVOS Y LOTES DE RELLENO. El mixer declara 72 lotes pero solo 24 tienen
`kgHeads > 0`: los otros 48 son "Corral 23" a "Corral 70" con 0 kg de materia
seca por cabeza y 100 cabezas de relleno. No se alimentan, así que no pueden
llevar costo. Se devuelven igual, marcados `activo=False`, en vez de filtrarlos
en silencio: si mañana el tambo empieza a usar uno, tiene que aparecer.

PRECIOS EN CERO. Los 20 ingredientes tienen `price: 0` — el tambo no los cargó
(se hace desde "Editar ingrediente" en Haasten). Se traducen a `None`, no a 0,
porque un cero se propagaría como un costo real y mentiría. Sin precios no se
puede calcular costo, pero la eficiencia de conversión sí: es física.
"""
import datetime
import os
import threading
import time

import requests

NOMBRE = "Haasten"
BASE = "https://haasten.io/api"

VAR_USUARIO = "HASTEN_USUARIO"
VAR_PASSWORD = "HASTEN_PASSWORD"

# Tipos de equipo que son mixers (los que tienen lotes y descargas). Los GAC son
# de gestión de combustible y el DELPROSIPN es el puente con DelPro.
TIPOS_MIXER = ("SIP-N", "SIP-T")

TIMEOUT_S = 45

# La sesión se reusa: cada login son unos segundos y los lotes casi no cambian.
_TTL_SESION_S = 300
_cache: dict = {}
_lock = threading.Lock()


class HaastenError(Exception):
    pass


def disponible() -> tuple[bool, str]:
    """(True, "") si están las credenciales; si no, (False, por qué)."""
    if os.environ.get(VAR_USUARIO) and os.environ.get(VAR_PASSWORD):
        return True, ""
    return False, (
        f"Faltan las variables de entorno {VAR_USUARIO} / {VAR_PASSWORD} "
        f"(ver INSTALL.md). Si ya las cargaste con setx, tené en cuenta que "
        f"los procesos que estaban abiertos no las ven: hay que reiniciar la "
        f"aplicación para que las tome.")


def _sesion() -> tuple[requests.Session, dict]:
    """(sesión autenticada, respuesta del login). Cacheada unos minutos."""
    with _lock:
        guardado = _cache.get("sesion")
        if guardado and time.time() - guardado[0] < _TTL_SESION_S:
            return guardado[1], guardado[2]

    ok, motivo = disponible()
    if not ok:
        raise HaastenError(motivo)

    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    try:
        r = s.post(f"{BASE}/login",
                   json={"username": os.environ[VAR_USUARIO],
                         "password": os.environ[VAR_PASSWORD]},
                   timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise HaastenError(f"No se pudo conectar con Haasten: {exc}")
    if r.status_code != 200:
        raise HaastenError(f"Haasten rechazó el login (HTTP {r.status_code}). "
                           f"Revisá {VAR_USUARIO} / {VAR_PASSWORD}.")
    try:
        datos = r.json()
    except ValueError:
        raise HaastenError("Haasten devolvió una respuesta que no es JSON.")
    token = datos.get("token")
    if not token:
        raise HaastenError("Haasten no devolvió token: usuario o contraseña incorrectos.")
    s.headers["authorization"] = token

    with _lock:
        _cache["sesion"] = (time.time(), s, datos)
    return s, datos


def _pedir(ruta: str, params: dict = None):
    s, _ = _sesion()
    try:
        r = s.get(f"{BASE}/{ruta}", params=params or {}, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise HaastenError(f"No se pudo consultar Haasten ({ruta}): {exc}")
    if r.status_code != 200:
        raise HaastenError(f"Haasten devolvió HTTP {r.status_code} en {ruta}.")
    try:
        return r.json()
    except ValueError:
        raise HaastenError(f"Haasten devolvió algo que no es JSON en {ruta}.")


def equipos() -> list:
    """Los mixers de la cuenta, con su serie, alias y última señal."""
    _s, datos = _sesion()
    todos = (datos.get("user") or {}).get("devices") or []
    return [{"serial": e.get("serialNumber"), "tipo": e.get("type"),
             "alias": (e.get("alias") or "").strip(),
             "ultimo_dato": e.get("lastDataRecievedDate"),
             "_crudo": e}
            for e in todos if e.get("type") in TIPOS_MIXER]


def _categorias(equipo_crudo: dict) -> dict:
    """{id: nombre} de las categorías de lote que configuró el tambo."""
    cfg = equipo_crudo.get("sipnConfiguration") or {}
    return {c.get("id"): c.get("name") for c in (cfg.get("lotCategories") or [])}


def lotes() -> list:
    """Los lotes de todos los mixers, en el formato común de `proveedores`."""
    salida = []
    for eq in equipos():
        crudo = eq["_crudo"]
        cats = _categorias(crudo)
        for l in crudo.get("lots") or []:
            kg_ms = l.get("kgHeads")
            indice = l.get("associatedMilkerIndex")
            salida.append({
                "lote": (l.get("name") or "").strip(),
                "id": l.get("id"),
                "cabezas": int(l.get("headsCount") or 0),
                "kg_ms_cabeza": kg_ms,
                "categoria": cats.get(l.get("categoryId")),
                "categoria_id": l.get("categoryId"),
                # El mapeo que el tambo ya declaró DENTRO de Haasten.
                "indice_ordene": int(indice) if indice not in (None, "") else None,
                # Sin kg de materia seca por cabeza no se alimenta: es un lote
                # de relleno, no una boca que consuma.
                "activo": bool(kg_ms),
                "pct_alimentacion": l.get("realFeedingPercentage"),
                "equipo": eq["serial"],
                "equipo_alias": eq["alias"],
            })
    return salida


def ingredientes() -> list:
    """Ingredientes con su %MS y su precio por kg (None si no está cargado)."""
    salida = []
    for eq in equipos():
        stock = (eq["_crudo"].get("sipnStock") or {}).get("ingredients") or []
        for i in stock:
            precio = i.get("price")
            ms = i.get("dryMatterPercentage")
            salida.append({
                "nombre": (i.get("name") or "").strip(),
                "id": i.get("id"),
                # Viene como fracción (0,88); se muestra en %.
                "ms_pct": round(ms * 100, 1) if isinstance(ms, (int, float)) else None,
                # 0 = no lo cargaron. No es un precio: es la ausencia de precio.
                "precio": precio if precio else None,
                "stock": i.get("stock"),
                "equipo": eq["serial"],
            })
    return salida


def _fecha(v) -> str:
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else str(v)


def consumos(desde, hasta) -> dict:
    """Descargas por lote y cargas por ingrediente entre dos fechas.

    `descargas`: cada entrega del mixer a un lote (`downloaded` son los kg tal
    como salieron de la tolva). `cargas`: cada ingrediente cargado, con su %MS
    del momento y el precio con el que se cargó.
    """
    salida = {"descargas": [], "cargas": []}
    params = {"minDate": _fecha(desde), "maxDate": _fecha(hasta)}
    for eq in equipos():
        for d in _pedir(f"deviceData/get/unloads/{eq['serial']}", params) or []:
            salida["descargas"].append({
                "fecha": d.get("date") or d.get("serverTime"),
                "lote": (d.get("lot") or "").strip() or None,
                "lote_id": d.get("curLot"),
                "kg": d.get("downloaded"),
                "cabezas": d.get("headsCount"),
                "kg_ms_cabeza": d.get("msCab") or d.get("kgHeads"),
                "operacion": d.get("operationID"),
                "usuario": d.get("user"),
                "equipo": eq["serial"],
            })
        for c in _pedir(f"deviceData/get/loads/{eq['serial']}", params) or []:
            precio = c.get("price")
            ms = c.get("ms")
            salida["cargas"].append({
                "fecha": c.get("date") or c.get("serverTime"),
                "ingrediente": (c.get("ingredient") or "").strip() or None,
                "receta": (c.get("recipe") or "").strip() or None,
                "kg": c.get("loaded"),
                # Acá el %MS ya viene en porcentaje (90), no en fracción.
                "ms_pct": ms if isinstance(ms, (int, float)) else None,
                "precio": precio if precio else None,
                "operacion": c.get("operationID"),
                "usuario": c.get("user"),
                "equipo": eq["serial"],
            })
    return salida
