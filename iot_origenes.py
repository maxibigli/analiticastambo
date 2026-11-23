# -*- coding: utf-8 -*-
"""Orígenes HTTP de datos para el Monitoreo IoT: un ESP32 (o un servidor LoRaWAN como ChirpStack) que MANDA lecturas a LactIA.

El equipo principal (el gateway PUSR M300, `iot_lavado`) es el origen `principal` y no se guarda acá: este archivo solo tiene los
orígenes que el tambo agrega desde ⚙ Configuración › 🔌 Entradas/Salidas. Cada uno tiene:

  * `id`: interno, no cambia nunca (sale del nombre al crearlo; sin puntos, no puede ser `principal`);
  * `formato`: `generico` (el cuerpo `{"lecturas": {...}}` que arma un ESP32) o `chirpstack` (el evento `up` de ChirpStack);
  * `ventana_lectura_min`: cada cuántos minutos tiene que llegar algo para que no figure «sin datos». OBLIGATORIA y sin valor
    por defecto: lo sabe el tambo (cada cuánto manda su nodo), un número nuestro marcaría mudo a un nodo lento o taparía uno caído;
  * `token_hash`: el SHA-256 del token. El token se muestra UNA vez, al crear o regenerar, y no queda en ningún archivo;
  * `canales`: lo que el origen manda. Cada uno es una entrada digital o una analógica (solo entradas: los orígenes HTTP todavía
    no manejan salidas). La clave global de un canal es `<id>.<clave>` y es la que usa el catálogo de sensores.

Se guarda en `iot_origenes.json` (estado propio de cada instalación, fuera de git). Deliberadamente SIN import de
`iot_sensores`, `iot_monitoreo` ni `iot_lavado` (que son los que lo importan a él): lo que necesita saber del catálogo se lo
pasan por parámetro, igual que `iot_analogicas.guardar`.
"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from datetime import datetime

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iot_origenes.json")
_lock = threading.RLock()

PRINCIPAL_ID = "principal"
VENTANA_PRINCIPAL_MIN = 30        # la del equipo principal de siempre (`iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN`)
FORMATOS = ("generico", "chirpstack")
GRUPOS = ("entrada_digital", "entrada_analogica")
VERSION = 1

# Topes técnicos (no del tambo).
_REINTENTOS = 5                    # leer/reemplazar el archivo cuando Windows lo tiene tomado un instante
_ESPERA_REINTENTO_S = 0.05
MAX_ORIGENES = 20
MAX_CANALES = 32
MAX_NOMBRE = 40
MAX_ID = 24
_RE_CLAVE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_RE_ID = re.compile(r"^[a-z0-9_]{1,%d}$" % MAX_ID)
_HASH_DE_RELLENO = hashlib.sha256(b"origen-inexistente").hexdigest()


def _hash(token) -> str:
    return hashlib.sha256((token if isinstance(token, str) else "").encode("utf-8")).hexdigest()


def _texto(valor, nombre) -> str:
    t = "" if valor is None else str(valor).strip()
    if not t:
        raise ValueError(f"Falta «{nombre}».")
    if len(t) > MAX_NOMBRE:
        raise ValueError(f"«{nombre}» no puede tener más de {MAX_NOMBRE} letras.")
    return t


def _ventana(valor) -> int:
    """Entero ≥ 1. Sin valor por defecto: tiene que cargarlo el tambo."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError("Falta la ventana: cada cuántos minutos tiene que llegar una lectura (un número entero, desde 1).")
    if isinstance(valor, float) and (not math.isfinite(valor) or not valor.is_integer()):
        raise ValueError("La ventana tiene que ser un número entero de minutos, desde 1.")
    if int(valor) < 1:
        raise ValueError("La ventana tiene que ser de 1 minuto o más.")
    return int(valor)


def _formato(valor) -> str:
    if valor not in FORMATOS:
        raise ValueError(f"El formato tiene que ser «generico» o «chirpstack» (llegó {valor!r}).")
    return valor


def _numero(valor, nombre, defecto, clave, distinto_de_cero=False):
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return defecto
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: «{nombre}» tiene que ser un número.")
    try:
        n = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{clave}: «{nombre}» tiene que ser un número (llegó {valor!r}).") from None
    if not math.isfinite(n):
        raise ValueError(f"{clave}: «{nombre}» no es un número válido.")
    if distinto_de_cero and n == 0:
        raise ValueError(f"{clave}: «{nombre}» no puede ser 0 (la lectura quedaría siempre igual).")
    return int(n) if n.is_integer() else n


def canal_global(origen_id: str, clave: str) -> str:
    return f"{origen_id}.{clave}"


def _canal(c, estricto, sensores_analogicos=None, ocupados=None, usados=None):
    """Valida UN canal. `estricto=False` (al leer el archivo) salta lo que depende del catálogo."""
    if not isinstance(c, dict):
        raise ValueError("Cada canal tiene que ser un objeto.")
    clave = c.get("clave")
    if not isinstance(clave, str) or not _RE_CLAVE.match(clave):
        raise ValueError(f"La clave de un canal tiene que tener de 1 a 40 letras, números, «_» o «-» (llegó {clave!r}): "
                         "sin puntos ni espacios.")
    grupo = c.get("grupo")
    if grupo == "salida":
        raise ValueError(f"{clave}: los orígenes HTTP todavía no manejan salidas.")
    if grupo not in GRUPOS:
        raise ValueError(f"{clave}: el tipo de canal tiene que ser «entrada_digital» o «entrada_analogica».")
    nombre = c.get("nombre")
    nombre = "" if nombre is None else str(nombre).strip()
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"{clave}: el nombre no puede tener más de {MAX_NOMBRE} letras.")
    sensor = c.get("sensor")
    sensor = sensor.strip() if isinstance(sensor, str) else sensor
    sensor = sensor or None
    if grupo == "entrada_digital":
        if sensor is not None and estricto:
            raise ValueError(f"{clave}: un canal digital no se asigna a un sensor analógico (el sensor digital elige la entrada).")
        return {"clave": clave, "grupo": grupo, "nombre": nombre, "factor": 1, "offset": 0, "sensor": None}
    factor = _numero(c.get("factor"), "factor", 1, clave, distinto_de_cero=True)
    offset = _numero(c.get("offset"), "offset", 0, clave)
    if sensor is not None and estricto:
        if not isinstance(sensor, str) or sensor not in sensores_analogicos:
            raise ValueError(f"{clave}: el sensor {sensor!r} no existe o no es analógico.")
        if sensor in ocupados:
            raise ValueError(f"{clave}: el sensor {sensor!r} ya está asignado a una entrada del equipo principal.")
        if sensor in usados:
            raise ValueError(f"{clave}: el sensor {sensor!r} ya lo tiene el canal {usados[sensor]}: cada sensor lee de un solo lugar.")
        usados[sensor] = clave
    return {"clave": clave, "grupo": grupo, "nombre": nombre, "factor": factor, "offset": offset,
            "sensor": sensor if isinstance(sensor, str) else None}


def _origen_del_archivo(crudo):
    """Un origen leído del archivo, o None si no sirve. Lenient: un origen roto no tira a los demás."""
    if not isinstance(crudo, dict):
        return None
    id_ = crudo.get("id")
    if not isinstance(id_, str) or not _RE_ID.match(id_) or id_ == PRINCIPAL_ID:
        return None
    try:
        nombre = _texto(crudo.get("nombre"), "Nombre")
        formato = _formato(crudo.get("formato"))
        ventana = _ventana(crudo.get("ventana_lectura_min"))
    except ValueError:
        return None
    token_hash = crudo.get("token_hash")
    if not isinstance(token_hash, str) or not token_hash:
        return None
    canales, claves = [], set()
    for c in crudo.get("canales") if isinstance(crudo.get("canales"), list) else []:
        try:
            n = _canal(c, estricto=False)
        except ValueError:
            continue
        if n["clave"] not in claves:
            claves.add(n["clave"])
            canales.append(n)
    creado = crudo.get("creado")
    return {"id": id_, "nombre": nombre, "tipo": "http", "formato": formato, "ventana_lectura_min": ventana,
            "token_hash": token_hash, "creado": creado if isinstance(creado, str) else "", "canales": canales}


def cargar() -> list:
    """Los orígenes guardados (copias). Sin archivo, o roto: []. Uno inválido se saltea."""
    datos = None
    for intento in range(_REINTENTOS):
        try:
            with open(_RUTA, encoding="utf-8") as f:
                datos = json.load(f)
            break
        except PermissionError:      # en Windows, mientras otro hilo lo reemplaza: un instante, no «no hay orígenes»
            if intento == _REINTENTOS - 1:
                return []
            time.sleep(_ESPERA_REINTENTO_S)
        except (OSError, ValueError):
            return []
    lista = datos.get("origenes") if isinstance(datos, dict) else None
    if not isinstance(lista, list):
        return []
    salida, ids = [], set()
    for crudo in lista:
        o = _origen_del_archivo(crudo)
        if o and o["id"] not in ids:
            ids.add(o["id"])
            salida.append(o)
    return salida


def obtener(origen_id):
    return next((o for o in cargar() if o["id"] == origen_id), None)


def _apartar_si_esta_roto() -> None:
    """Un archivo que no se puede leer (editado a mano, cortado) no se pisa: se aparta con su fecha, nunca se borra."""
    try:
        with open(_RUTA, encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, dict) and isinstance(datos.get("origenes"), list):
            return
    except OSError:
        return                       # no existe (o está tomado: lo resuelve el reintento de abajo)
    except ValueError:
        pass
    os.replace(_RUTA, f"{_RUTA}.roto-{datetime.now().strftime('%Y%m%d-%H%M%S')}")


def _escribir(origenes: list) -> None:
    with _lock:
        _apartar_si_esta_roto()
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_RUTA), prefix=".iot_origenes_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION, "origenes": origenes}, f, ensure_ascii=False, indent=2)
            for intento in range(_REINTENTOS):
                try:
                    os.replace(tmp, _RUTA)
                    break
                except PermissionError:      # Windows: otro hilo lo está leyendo justo ahora
                    if intento == _REINTENTOS - 1:
                        raise
                    time.sleep(_ESPERA_REINTENTO_S)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def _slug(nombre: str) -> str:
    t = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_") or "origen"


def _id_libre(nombre: str, usados: set) -> str:
    base = _slug(nombre)[:MAX_ID].strip("_") or "origen"
    id_, n = base, 2
    while id_ in usados or id_ == PRINCIPAL_ID:
        sufijo = f"_{n}"
        id_, n = base[:MAX_ID - len(sufijo)] + sufijo, n + 1
    return id_


def crear(nombre, formato, ventana_lectura_min):
    """Alta de un origen sin canales. Devuelve (origen, token): el token es la ÚNICA vez que se ve."""
    nombre = _texto(nombre, "Nombre")
    formato = _formato(formato)
    ventana = _ventana(ventana_lectura_min)
    with _lock:
        existentes = cargar()
        if len(existentes) >= MAX_ORIGENES:
            raise ValueError(f"Ya hay {MAX_ORIGENES} orígenes: ese es el máximo.")
        token = secrets.token_urlsafe(32)
        origen = {"id": _id_libre(nombre, {o["id"] for o in existentes}), "nombre": nombre, "tipo": "http",
                  "formato": formato, "ventana_lectura_min": ventana, "token_hash": _hash(token),
                  "creado": datetime.now().isoformat(timespec="seconds"), "canales": []}
        _escribir(existentes + [origen])
    return origen, token


def guardar_origen(origen_id, nombre, formato, ventana_lectura_min, canales, *, sensores_analogicos: dict,
                   sensores_ocupados: set, entradas_en_uso: set) -> None:
    """Valida TODO antes de escribir: un guardado que falla deja el archivo como estaba.

    `sensores_analogicos`: clave -> (min, max) del catálogo; `sensores_ocupados`: los ya asignados a una entrada del
    equipo principal; `entradas_en_uso`: claves globales de este módulo que un sensor digital tiene asignadas."""
    with _lock:
        todos = cargar()
        actual = next((o for o in todos if o["id"] == origen_id), None)
        if actual is None:
            raise ValueError(f"No existe el origen {origen_id!r}.")
        nombre = _texto(nombre, "Nombre")
        formato = _formato(formato)
        ventana = _ventana(ventana_lectura_min)
        if not isinstance(canales, list):
            raise ValueError("Los canales tienen que ser una lista.")
        if len(canales) > MAX_CANALES:
            raise ValueError(f"Un origen puede tener hasta {MAX_CANALES} canales.")
        # Los sensores que ya tienen OTROS orígenes cuentan como ocupados.
        usados = {c["sensor"]: canal_global(o["id"], c["clave"]) for o in todos if o["id"] != origen_id
                  for c in o["canales"] if c["sensor"]}
        nuevos, claves = [], set()
        for c in canales:
            n = _canal(c, True, sensores_analogicos or {}, sensores_ocupados or set(), usados)
            if n["clave"] in claves:
                raise ValueError(f"La clave {n['clave']!r} está repetida: cada canal necesita una distinta.")
            claves.add(n["clave"])
            nuevos.append(n)
        digitales_nuevas = {c["clave"] for c in nuevos if c["grupo"] == "entrada_digital"}
        for c in actual["canales"]:
            g = canal_global(origen_id, c["clave"])
            if c["grupo"] == "entrada_digital" and c["clave"] not in digitales_nuevas and g in (entradas_en_uso or set()):
                raise ValueError(f"La entrada {g} la usa un sensor del tambo: cambiale la entrada a ese sensor "
                                 "antes de sacar el canal.")
        _escribir([{**actual, "nombre": nombre, "formato": formato, "ventana_lectura_min": ventana, "canales": nuevos}
                   if o["id"] == origen_id else o for o in todos])


def regenerar_token(origen_id) -> str:
    """Token nuevo; el anterior deja de servir. Se muestra una sola vez."""
    with _lock:
        todos = cargar()
        if not any(o["id"] == origen_id for o in todos):
            raise ValueError(f"No existe el origen {origen_id!r}.")
        token = secrets.token_urlsafe(32)
        _escribir([{**o, "token_hash": _hash(token)} if o["id"] == origen_id else o for o in todos])
    return token


def eliminar(origen_id, entradas_en_uso: set) -> None:
    with _lock:
        todos = cargar()
        actual = next((o for o in todos if o["id"] == origen_id), None)
        if actual is None:
            raise ValueError(f"No existe el origen {origen_id!r}.")
        for c in actual["canales"]:
            g = canal_global(origen_id, c["clave"])
            if c["grupo"] == "entrada_digital" and g in (entradas_en_uso or set()):
                raise ValueError(f"La entrada {g} la usa un sensor del tambo: cambiale la entrada a ese sensor "
                                 "antes de eliminar el origen.")
        _escribir([o for o in todos if o["id"] != origen_id])


def token_valido(origen_id, candidato) -> bool:
    """¿Es el token de ese origen? Siempre hace UNA comparación de hashes (con un hash de relleno si el origen no existe o el
    candidato no es texto) para que un origen inexistente no se distinga por el tiempo de respuesta."""
    origen = obtener(origen_id) if isinstance(origen_id, str) else None
    esperado = origen["token_hash"] if origen else _HASH_DE_RELLENO
    coincide = hmac.compare_digest(_hash(candidato), esperado)
    return bool(coincide and origen is not None and isinstance(candidato, str) and candidato != "")


def entradas_digitales() -> tuple:
    """Claves globales de los canales digitales de todos los orígenes."""
    return tuple(canal_global(o["id"], c["clave"]) for o in cargar() for c in o["canales"] if c["grupo"] == "entrada_digital")


def sensores_asignados() -> set:
    """Sensores analógicos que un canal de origen alimenta."""
    return {c["sensor"] for o in cargar() for c in o["canales"] if c["sensor"]}


def ventanas() -> dict:
    """Cuántos minutos puede pasar sin lecturas antes de «sin datos»: por entrada digital (clave global) y por sensor."""
    entradas, sensores = {}, {}
    for o in cargar():
        for c in o["canales"]:
            if c["grupo"] == "entrada_digital":
                entradas[canal_global(o["id"], c["clave"])] = o["ventana_lectura_min"]
            elif c["sensor"]:
                sensores[c["sensor"]] = o["ventana_lectura_min"]
    return {"entradas": entradas, "sensores": sensores}
