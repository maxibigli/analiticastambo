# -*- coding: utf-8 -*-
"""Orígenes de datos del Monitoreo IoT, de dos tipos: `http` (un ESP32 o un servidor LoRaWAN como ChirpStack que MANDA lecturas a
LactIA) y `modbus_tcp` (otro PLC o gateway que LactIA CONSULTA: lo lee `iot_lavado.ConexionesModbus`; sin token, con host, puerto y
unit ID, y canales con función, dirección y tipo de dato). Todo lo de abajo vale para los dos salvo donde se dice «HTTP».

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

import iot_conexion
import iot_mapa

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iot_origenes.json")
_lock = threading.RLock()

PRINCIPAL_ID = "principal"
VENTANA_PRINCIPAL_MIN = 30        # la del equipo principal de siempre (`iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN`)
FORMATOS = ("generico", "chirpstack")
GRUPOS = ("entrada_digital", "entrada_analogica")
TIPOS = ("http", "modbus_tcp")
# Equipos Modbus: un canal analógico lee registros (dos para el decimal de 32 bits «AB CD» que usan los AI del M300).
FUNCIONES_ANALOGICA = ("input_register", "holding_register")
TIPOS_DATO = ("uint16", "int16", "float32_ab_cd")
PUERTO_DEFECTO = iot_conexion.PUERTO_DEFECTO      # los dos defectos de PROTOCOLO que el código ya usa; todo lo demás es obligatorio
UNIT_ID_DEFECTO = iot_mapa.UNIT_ID_DEFECTO
# Salidas de un equipo que manda por HTTP (un relé en un ESP32): se comandan con órdenes que el equipo consulta (`iot_ordenes`).
MODOS_SALIDA = ("pulso", "sostenida")
PULSO_MAX_ORIGEN_S = 60       # tope TÉCNICO, no del tambo: un error de tipeo no puede dejar una bomba prendida una hora
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
_RE_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
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


def _vencimiento(valor) -> int:
    """Segundos que una orden puede esperar a que el equipo la levante (y a que la confirme). Entero ≥ 1, sin defecto: depende de
    cada cuánto consulta el equipo, y lo sabe el tambo."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError("Falta el vencimiento de las órdenes: cuántos segundos puede esperar una orden a que el equipo la levante "
                         "(un número entero, desde 1; tiene que ser mayor que cada cuánto consulta tu equipo).")
    if isinstance(valor, float) and (not math.isfinite(valor) or not valor.is_integer()):
        raise ValueError("El vencimiento de las órdenes tiene que ser un número entero de segundos, desde 1.")
    if int(valor) < 1:
        raise ValueError("El vencimiento de las órdenes tiene que ser de 1 segundo o más.")
    return int(valor)


def _pulso_origen(valor, clave) -> float:
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: el pulso tiene que ser un número de segundos.")
    try:
        n = float(valor.strip().replace(",", ".")) if isinstance(valor, str) else float(valor)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{clave}: una salida de pulso necesita su duración en segundos (llegó {valor!r}).") from None
    if not math.isfinite(n) or not 0 < n <= PULSO_MAX_ORIGEN_S:
        raise ValueError(f"{clave}: el pulso tiene que ser mayor que 0 y de {PULSO_MAX_ORIGEN_S} segundos como máximo "
                         "(para dejarla prendida más tiempo, usá una salida «sostenida»).")
    return int(n) if n.is_integer() else n


def _salida(c, clave, nombre) -> dict:
    """Un canal de salida (un relé del equipo que LactIA comanda con órdenes). Sin sensor, sin factor."""
    if c.get("sensor") not in (None, "") or c.get("factor") not in (None, "", 1) or c.get("offset") not in (None, "", 0):
        raise ValueError(f"{clave}: una salida no se asigna a un sensor ni tiene factor.")
    modo = c.get("modo")
    if modo not in MODOS_SALIDA:
        raise ValueError(f"{clave}: falta el modo de la salida («pulso»: se activa un rato y se suelta sola; «sostenida»: queda "
                         "prendida hasta que se la apaga).")
    if modo == "pulso":
        pulso = _pulso_origen(c.get("pulso_s"), clave)
    else:
        if c.get("pulso_s") not in (None, ""):
            raise ValueError(f"{clave}: una salida sostenida no tiene duración de pulso.")
        pulso = None
    return {"clave": clave, "grupo": "salida", "nombre": nombre, "factor": 1, "offset": 0, "sensor": None,
            "modo": modo, "pulso_s": pulso}


def _canal(c, estricto, sensores_analogicos=None, ocupados=None, usados=None, puede_comandar=False):
    """Valida UN canal. `estricto=False` (al leer el archivo) salta lo que depende del catálogo."""
    if not isinstance(c, dict):
        raise ValueError("Cada canal tiene que ser un objeto.")
    clave = c.get("clave")
    if not isinstance(clave, str) or not _RE_CLAVE.match(clave):
        raise ValueError(f"La clave de un canal tiene que tener de 1 a 40 letras, números, «_» o «-» (llegó {clave!r}): "
                         "sin puntos ni espacios.")
    grupo = c.get("grupo")
    nombre = c.get("nombre")
    nombre = "" if nombre is None else str(nombre).strip()
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"{clave}: el nombre no puede tener más de {MAX_NOMBRE} letras.")
    if grupo == "salida":
        if not puede_comandar:
            raise ValueError(f"{clave}: para agregar salidas tildá «Puede recibir órdenes» en este equipo.")
        return _salida(c, clave, nombre)
    if grupo not in GRUPOS:
        raise ValueError(f"{clave}: el tipo de canal tiene que ser «entrada_digital», «entrada_analogica» o «salida».")
    sensor = c.get("sensor")
    sensor = sensor.strip() if isinstance(sensor, str) else sensor
    sensor = sensor or None
    if grupo == "entrada_digital":
        if sensor is not None and estricto:
            raise ValueError(f"{clave}: un canal digital no se asigna a un sensor analógico (el sensor digital elige la entrada).")
        return {"clave": clave, "grupo": grupo, "nombre": nombre, "factor": 1, "offset": 0, "sensor": None}
    factor = _numero(c.get("factor"), "factor", 1, clave, distinto_de_cero=True)
    offset = _numero(c.get("offset"), "offset", 0, clave)
    _validar_sensor(clave, sensor, estricto, sensores_analogicos, ocupados, usados)
    return {"clave": clave, "grupo": grupo, "nombre": nombre, "factor": factor, "offset": offset,
            "sensor": sensor if isinstance(sensor, str) else None}


def _validar_sensor(clave, sensor, estricto, sensores_analogicos, ocupados, usados) -> None:
    """El sensor del catálogo que alimenta un canal analógico (lo comparten los orígenes HTTP y los Modbus)."""
    if sensor is None or not estricto:
        return
    if not isinstance(sensor, str) or sensor not in sensores_analogicos:
        raise ValueError(f"{clave}: el sensor {sensor!r} no existe o no es analógico.")
    if sensor in ocupados:
        raise ValueError(f"{clave}: el sensor {sensor!r} ya está asignado a una entrada del equipo principal.")
    if sensor in usados:
        raise ValueError(f"{clave}: el sensor {sensor!r} ya lo tiene el canal {usados[sensor]}: cada sensor lee de un solo lugar.")
    usados[sensor] = clave


# ---- equipos Modbus TCP ----

def _host(valor) -> str:
    t = valor.strip() if isinstance(valor, str) else ""
    if not _RE_HOST.match(t):
        raise ValueError("Falta la dirección del equipo (su IP o nombre, sin espacios ni «http://» ni puerto): "
                         f"llegó {valor!r}.")
    return t


def _puerto(valor) -> int:
    if iot_mapa._vacio(valor):
        return PUERTO_DEFECTO
    n = iot_mapa._entero(valor, "El puerto")
    if not 1 <= n <= 65535:
        raise ValueError("El puerto tiene que estar entre 1 y 65535.")
    return n


def _unit_equipo(valor) -> int:
    return UNIT_ID_DEFECTO if iot_mapa._vacio(valor) else iot_mapa._unit_id(valor)


def _canal_modbus(c, estricto, sensores_analogicos=None, ocupados=None, usados=None):
    """Valida UN canal de un equipo Modbus. `unit_id` None = el del equipo. `estricto=False` (al leer el archivo) salta lo que
    depende del catálogo."""
    if not isinstance(c, dict):
        raise ValueError("Cada canal tiene que ser un objeto.")
    clave = c.get("clave")
    if not isinstance(clave, str) or not _RE_CLAVE.match(clave):
        raise ValueError(f"La clave de un canal tiene que tener de 1 a 40 letras, números, «_» o «-» (llegó {clave!r}): "
                         "sin puntos ni espacios.")
    grupo = c.get("grupo")
    if grupo == "salida":
        raise ValueError(f"{clave}: los equipos Modbus adicionales todavía no manejan salidas.")
    if grupo not in GRUPOS:
        raise ValueError(f"{clave}: el tipo de canal tiene que ser «entrada_digital» o «entrada_analogica».")
    nombre = c.get("nombre")
    nombre = "" if nombre is None else str(nombre).strip()
    if len(nombre) > MAX_NOMBRE:
        raise ValueError(f"{clave}: el nombre no puede tener más de {MAX_NOMBRE} letras.")
    digital = grupo == "entrada_digital"
    funciones = iot_mapa.FUNCIONES_ENTRADA if digital else FUNCIONES_ANALOGICA
    funcion = c.get("funcion")
    if funcion not in funciones:
        raise ValueError(f"{clave}: la función {funcion!r} no sirve para este canal (usar una de {', '.join(funciones)}).")
    try:
        direccion = iot_mapa._direccion(c.get("direccion"), funcion)
        unit_id = None if iot_mapa._vacio(c.get("unit_id")) else iot_mapa._unit_id(c.get("unit_id"))
        invertido = iot_mapa._invertido(c.get("invertido")) if digital else False
    except ValueError as exc:
        raise ValueError(f"{clave}: {exc}") from None
    salida = {"clave": clave, "grupo": grupo, "nombre": nombre, "funcion": funcion, "direccion": direccion,
              "unit_id": unit_id, "tipo_dato": None, "factor": 1, "offset": 0, "invertido": invertido, "sensor": None}
    sensor = c.get("sensor")
    sensor = sensor.strip() if isinstance(sensor, str) else sensor
    sensor = sensor or None
    if digital:
        if sensor is not None and estricto:
            raise ValueError(f"{clave}: un canal digital no se asigna a un sensor analógico (el sensor digital elige la entrada).")
        return salida
    tipo_dato = c.get("tipo_dato")
    if tipo_dato not in TIPOS_DATO:
        raise ValueError(f"{clave}: falta el tipo de dato del registro (usar uno de {', '.join(TIPOS_DATO)}).")
    salida.update(tipo_dato=tipo_dato, factor=_numero(c.get("factor"), "factor", 1, clave, distinto_de_cero=True),
                  offset=_numero(c.get("offset"), "offset", 0, clave))
    _validar_sensor(clave, sensor, estricto, sensores_analogicos, ocupados, usados)
    salida["sensor"] = sensor if isinstance(sensor, str) else None
    return salida


def _origen_del_archivo(crudo):
    """Un origen leído del archivo, o None si no sirve. Lenient: un origen roto no tira a los demás."""
    if not isinstance(crudo, dict):
        return None
    id_ = crudo.get("id")
    if not isinstance(id_, str) or not _RE_ID.match(id_) or id_ == PRINCIPAL_ID:
        return None
    modbus = crudo.get("tipo") == "modbus_tcp"        # un registro sin `tipo` es HTTP: los archivos de la etapa 3 no lo traen
    try:
        nombre = _texto(crudo.get("nombre"), "Nombre")
        ventana = _ventana(crudo.get("ventana_lectura_min"))
        if modbus:
            host, puerto, unit_id = _host(crudo.get("host")), _puerto(crudo.get("port")), _unit_equipo(crudo.get("unit_id"))
        else:
            formato = _formato(crudo.get("formato"))
    except ValueError:
        return None
    puede_comandar, vencimiento = False, None
    if not modbus:
        token_hash = crudo.get("token_hash")
        if not isinstance(token_hash, str) or not token_hash:
            return None
        if crudo.get("puede_comandar") is True and formato == "generico":
            try:
                vencimiento = _vencimiento(crudo.get("vencimiento_orden_s"))
                puede_comandar = True
            except ValueError:
                pass           # sin un vencimiento válido no se puede garantizar que una orden venza: no se comanda
    canales, claves = [], set()
    for c in crudo.get("canales") if isinstance(crudo.get("canales"), list) else []:
        try:
            n = _canal_modbus(c, estricto=False) if modbus else _canal(c, estricto=False, puede_comandar=puede_comandar)
        except ValueError:
            continue
        if n["clave"] not in claves:
            claves.add(n["clave"])
            canales.append(n)
    creado = crudo.get("creado")
    creado = creado if isinstance(creado, str) else ""
    if modbus:
        return {"id": id_, "nombre": nombre, "tipo": "modbus_tcp", "host": host, "port": puerto, "unit_id": unit_id,
                "ventana_lectura_min": ventana, "creado": creado, "canales": canales}
    return {"id": id_, "nombre": nombre, "tipo": "http", "formato": formato, "ventana_lectura_min": ventana,
            "puede_comandar": puede_comandar, "vencimiento_orden_s": vencimiento,
            "token_hash": token_hash, "creado": creado, "canales": canales}


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
        _chequear_cupo(existentes)
        token = secrets.token_urlsafe(32)
        origen = {"id": _id_libre(nombre, {o["id"] for o in existentes}), "nombre": nombre, "tipo": "http",
                  "formato": formato, "ventana_lectura_min": ventana, "puede_comandar": False,
                  "vencimiento_orden_s": None, "token_hash": _hash(token),
                  "creado": datetime.now().isoformat(timespec="seconds"), "canales": []}
        _escribir(existentes + [origen])
    return origen, token


def _chequear_cupo(existentes: list) -> None:
    if len(existentes) >= MAX_ORIGENES:
        raise ValueError(f"Ya hay {MAX_ORIGENES} orígenes: ese es el máximo.")


def _host_canonico(host: str) -> str:
    """La forma con que se comparan dos direcciones: sin mayúsculas, sin el punto final, `localhost` = 127.0.0.1 y una IPv4 sin ceros
    de más (192.168.001.060 es 192.168.1.60). No se resuelven nombres (sin red al guardar): un nombre y su IP no se reconocen."""
    h = host.strip().lower().rstrip(".")
    if h == "localhost":
        return "127.0.0.1"
    partes = h.split(".")
    if len(partes) == 4 and all(p.isdigit() for p in partes):
        return ".".join(str(int(p)) for p in partes)
    return h


def _chequear_equipo_nuevo(todos: list, host: str, puerto: int, ignorar_id=None) -> None:
    """Un equipo, un origen: dos con el mismo host y puerto (o uno con los del equipo principal) se pisarían leyendo lo mismo.
    Para varios unit ID de un mismo gateway se usa el unit ID de cada canal."""
    llave = (_host_canonico(host), puerto)
    principal = iot_conexion.config()
    if llave == (_host_canonico(str(principal["host"])), principal["port"]):
        raise ValueError(f"{host}:{puerto} es la dirección del equipo principal (el gateway de siempre): ya se lee, "
                         "no se agrega como otro equipo.")
    for o in todos:
        if o["id"] != ignorar_id and o["tipo"] == "modbus_tcp" and (_host_canonico(o["host"]), o["port"]) == llave:
            raise ValueError(f"{host}:{puerto} ya es el equipo «{o['nombre']}»: un equipo es un solo origen (para otro unit ID "
                             "del mismo equipo, usá el unit ID de cada canal).")


def crear_modbus(nombre, host, port, unit_id, ventana_lectura_min) -> dict:
    """Alta de un equipo Modbus TCP sin canales. `port` y `unit_id` vacíos son 502 y 1 (los defectos de protocolo)."""
    nombre = _texto(nombre, "Nombre")
    host, port, unit_id = _host(host), _puerto(port), _unit_equipo(unit_id)
    ventana = _ventana(ventana_lectura_min)
    with _lock:
        existentes = cargar()
        _chequear_cupo(existentes)
        _chequear_equipo_nuevo(existentes, host, port)
        origen = {"id": _id_libre(nombre, {o["id"] for o in existentes}), "nombre": nombre, "tipo": "modbus_tcp", "host": host,
                  "port": port, "unit_id": unit_id, "ventana_lectura_min": ventana,
                  "creado": datetime.now().isoformat(timespec="seconds"), "canales": []}
        _escribir(existentes + [origen])
    return origen


def guardar_modbus(origen_id, nombre, host, port, unit_id, ventana_lectura_min, canales, *, sensores_analogicos: dict,
                   sensores_ocupados: set, entradas_en_uso: set) -> None:
    """Como `guardar_origen`, para un equipo Modbus: valida TODO antes de escribir."""
    with _lock:
        todos = cargar()
        actual = next((o for o in todos if o["id"] == origen_id), None)
        if actual is None:
            raise ValueError(f"No existe el origen {origen_id!r}.")
        if actual["tipo"] != "modbus_tcp":
            raise ValueError(f"«{actual['nombre']}» es un equipo que manda sus datos por HTTP, no un equipo Modbus.")
        nombre = _texto(nombre, "Nombre")
        host, port, unit_id = _host(host), _puerto(port), _unit_equipo(unit_id)
        ventana = _ventana(ventana_lectura_min)
        _chequear_equipo_nuevo(todos, host, port, ignorar_id=origen_id)
        if not isinstance(canales, list):
            raise ValueError("Los canales tienen que ser una lista.")
        if len(canales) > MAX_CANALES:
            raise ValueError(f"Un equipo puede tener hasta {MAX_CANALES} canales.")
        usados = {c["sensor"]: canal_global(o["id"], c["clave"]) for o in todos if o["id"] != origen_id
                  for c in o["canales"] if c["sensor"]}
        nuevos, claves, puntos = [], set(), {}
        for c in canales:
            n = _canal_modbus(c, True, sensores_analogicos or {}, sensores_ocupados or set(), usados)
            if n["clave"] in claves:
                raise ValueError(f"La clave {n['clave']!r} está repetida: cada canal necesita una distinta.")
            claves.add(n["clave"])
            # Un decimal de 32 bits ocupa DOS registros: otro canal en el segundo se pisaría con él.
            cantidad = 2 if n["tipo_dato"] == "float32_ab_cd" else 1
            if n["direccion"] + cantidad - 1 > 65535:
                raise ValueError(f"{n['clave']}: un decimal de 32 bits ocupa dos registros y no entra en la dirección {n['direccion']}.")
            for direccion in range(n["direccion"], n["direccion"] + cantidad):
                punto = (n["unit_id"] or unit_id, n["funcion"], direccion)
                if punto in puntos:
                    raise ValueError(f"Los canales «{puntos[punto]}» y «{n['clave']}» leen el mismo punto del equipo "
                                     f"(unit ID {punto[0]}, {punto[1]}, dirección {punto[2]}). Cada canal tiene que tener el suyo.")
                puntos[punto] = n["clave"]
            nuevos.append(n)
        _sin_entradas_en_uso_perdidas(origen_id, actual, nuevos, entradas_en_uso)
        _escribir([{**actual, "nombre": nombre, "host": host, "port": port, "unit_id": unit_id,
                    "ventana_lectura_min": ventana, "canales": nuevos} if o["id"] == origen_id else o for o in todos])


def _sin_entradas_en_uso_perdidas(origen_id, actual, nuevos, entradas_en_uso) -> None:
    digitales_nuevas = {c["clave"] for c in nuevos if c["grupo"] == "entrada_digital"}
    for c in actual["canales"]:
        g = canal_global(origen_id, c["clave"])
        if c["grupo"] == "entrada_digital" and c["clave"] not in digitales_nuevas and g in (entradas_en_uso or set()):
            raise ValueError(f"La entrada {g} la usa un sensor del tambo: cambiale la entrada a ese sensor "
                             "antes de sacar el canal.")


def guardar_origen(origen_id, nombre, formato, ventana_lectura_min, canales, *, sensores_analogicos: dict,
                   sensores_ocupados: set, entradas_en_uso: set, puede_comandar=False, vencimiento_orden_s=None) -> None:
    """Valida TODO antes de escribir: un guardado que falla deja el archivo como estaba.

    `sensores_analogicos`: clave -> (min, max) del catálogo; `sensores_ocupados`: los ya asignados a una entrada del
    equipo principal; `entradas_en_uso`: claves globales de este módulo que un sensor digital tiene asignadas.
    `puede_comandar` (solo formato `generico`): el equipo consulta órdenes para sus canales `salida`; entonces
    `vencimiento_orden_s` es obligatorio (se guarda solo en ese caso). Ver `iot_ordenes`."""
    with _lock:
        todos = cargar()
        actual = next((o for o in todos if o["id"] == origen_id), None)
        if actual is None:
            raise ValueError(f"No existe el origen {origen_id!r}.")
        if actual["tipo"] != "http":
            raise ValueError(f"«{actual['nombre']}» es un equipo Modbus: se guarda con su propio formulario.")
        nombre = _texto(nombre, "Nombre")
        formato = _formato(formato)
        ventana = _ventana(ventana_lectura_min)
        if not isinstance(puede_comandar, bool):
            raise ValueError("«Puede recibir órdenes» tiene que ser sí o no.")
        vencimiento = None
        if puede_comandar:
            if formato != "generico":
                raise ValueError("Un origen ChirpStack no se puede comandar (los mensajes hacia un nodo LoRaWAN esperan a que el "
                                 "nodo hable): solo los equipos de formato genérico, como un ESP32.")
            vencimiento = _vencimiento(vencimiento_orden_s)
        if not isinstance(canales, list):
            raise ValueError("Los canales tienen que ser una lista.")
        if len(canales) > MAX_CANALES:
            raise ValueError(f"Un origen puede tener hasta {MAX_CANALES} canales.")
        # Los sensores que ya tienen OTROS orígenes cuentan como ocupados.
        usados = {c["sensor"]: canal_global(o["id"], c["clave"]) for o in todos if o["id"] != origen_id
                  for c in o["canales"] if c["sensor"]}
        nuevos, claves = [], set()
        for c in canales:
            n = _canal(c, True, sensores_analogicos or {}, sensores_ocupados or set(), usados, puede_comandar=puede_comandar)
            if n["clave"] in claves:
                raise ValueError(f"La clave {n['clave']!r} está repetida: cada canal necesita una distinta.")
            claves.add(n["clave"])
            nuevos.append(n)
        _sin_entradas_en_uso_perdidas(origen_id, actual, nuevos, entradas_en_uso)
        _escribir([{**actual, "nombre": nombre, "formato": formato, "ventana_lectura_min": ventana, "canales": nuevos,
                    "puede_comandar": puede_comandar, "vencimiento_orden_s": vencimiento}
                   if o["id"] == origen_id else o for o in todos])


def regenerar_token(origen_id) -> str:
    """Token nuevo; el anterior deja de servir. Se muestra una sola vez."""
    with _lock:
        todos = cargar()
        actual = next((o for o in todos if o["id"] == origen_id), None)
        if actual is None:
            raise ValueError(f"No existe el origen {origen_id!r}.")
        if actual["tipo"] != "http":
            raise ValueError(f"«{actual['nombre']}» es un equipo Modbus: no usa token.")
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
    if origen is not None and origen["tipo"] != "http":      # un equipo Modbus no recibe datos por HTTP: igual que si no existiera
        origen = None
    esperado = origen["token_hash"] if origen else _HASH_DE_RELLENO
    coincide = hmac.compare_digest(_hash(candidato), esperado)
    return bool(coincide and origen is not None and isinstance(candidato, str) and candidato != "")


def entradas_digitales() -> tuple:
    """Claves globales de los canales digitales de todos los orígenes."""
    return tuple(canal_global(o["id"], c["clave"]) for o in cargar() for c in o["canales"] if c["grupo"] == "entrada_digital")


def salidas() -> tuple:
    """Claves globales de los canales de salida (relés que se comandan con órdenes) de todos los orígenes HTTP."""
    return tuple(canal_global(o["id"], c["clave"]) for o in cargar() for c in o["canales"] if c["grupo"] == "salida")


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
