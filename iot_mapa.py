# -*- coding: utf-8 -*-
"""Dónde está cada entrada digital y cada salida del equipo Modbus principal (PUSR M300): función, dirección, unit ID,
contacto invertido y largo del pulso.

Solo se guarda lo que el tambo cambió (`iot_mapa.json`, estado propio de cada instalación, fuera de git). Todo lo demás es el
valor de siempre: la entrada n es `discrete_input` en la dirección n-1, la salida n es `coil` en la n-1, unit ID 1 y pulso de
0,5 s. Sin archivo el gateway hace exactamente las mismas llamadas Modbus que antes de que existiera este módulo.

`iot_lavado.py` lo lee en cada ciclo, así que un cambio se toma sin reiniciar. Importa solo `iot_canales` (lo importan
`iot_lavado` y `iot_monitoreo`: un import de vuelta armaría un ciclo). Las entradas analógicas se calibran en
`iot_analogicas`, no acá.
"""
import json
import math
import os
import tempfile
import threading

import iot_canales

_RUTA = os.path.join(os.path.dirname(__file__), "iot_mapa.json")
_lock = threading.Lock()

FUNCIONES_ENTRADA = ("discrete_input", "coil", "input_register", "holding_register")
UNIT_ID_DEFECTO = 1
PULSO_DEFECTO_S = 0.5
# Tope técnico, no un umbral del tambo: el pulso se hace con time.sleep dentro del ciclo de 3 s del gateway y lo frena
# (lavado automático y voz incluidos) mientras dura.
PULSO_MAX_S = 10

# Notación Modicon que muestra el equipo -> dirección del protocolo. Los coils no se convierten: 1 a 9999 son direcciones válidas.
_MODICON = {"discrete_input": (10001, 19999, 10001), "input_register": (30001, 39999, 30001),
            "holding_register": (40001, 49999, 40001)}

_CAMPOS_ENTRADA = ("funcion", "direccion", "unit_id", "invertido")
_CAMPOS_SALIDA = ("direccion", "unit_id", "pulso_s")


def de_fabrica_entrada(clave: str) -> dict:
    n = iot_canales.numero(clave)
    return {"funcion": "discrete_input", "direccion": n - 1, "unit_id": UNIT_ID_DEFECTO, "invertido": False}


def de_fabrica_salida(clave: str) -> dict:
    n = iot_canales.numero(clave)
    return {"direccion": n - 1, "unit_id": UNIT_ID_DEFECTO, "pulso_s": PULSO_DEFECTO_S}


# ---- validación de cada campo (la usan guardar y la lectura tolerante) ----

def _vacio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _entero(v, nombre: str) -> int:
    if isinstance(v, bool):
        raise ValueError(f"{nombre}: tiene que ser un número entero.")
    if isinstance(v, str):
        try:
            v = float(v.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"{nombre}: «{v}» no es un número.") from None
    if isinstance(v, float):
        if not math.isfinite(v) or v != int(v):
            raise ValueError(f"{nombre}: tiene que ser un número entero.")
        v = int(v)
    if not isinstance(v, int):
        raise ValueError(f"{nombre}: tiene que ser un número entero.")
    return v


def _direccion(v, funcion: str | None) -> int:
    n = _entero(v, "La dirección")
    if funcion in _MODICON:
        desde, hasta, resta = _MODICON[funcion]
        if desde <= n <= hasta:
            n -= resta
    if not 0 <= n <= 65535:
        raise ValueError("La dirección tiene que estar entre 0 y 65535.")
    return n


def _unit_id(v) -> int:
    n = _entero(v, "El unit ID")
    if not 1 <= n <= 247:
        raise ValueError("El unit ID tiene que estar entre 1 y 247.")
    return n


def _pulso(v) -> float:
    if isinstance(v, bool):
        raise ValueError("El pulso tiene que ser un número de segundos.")
    if isinstance(v, str):
        try:
            v = float(v.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"El pulso: «{v}» no es un número.") from None
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("El pulso tiene que ser un número de segundos.")
    if not 0 < v <= PULSO_MAX_S:
        raise ValueError(f"El pulso tiene que ser mayor que 0 y de {PULSO_MAX_S} segundos como máximo "
                         "(mientras dura, el gateway no atiende otra cosa).")
    return float(v)


def _invertido(v) -> bool:
    if _vacio(v):
        return False
    if isinstance(v, bool):
        return v
    raise ValueError("«Contacto NC» tiene que ser sí o no.")


# ---- lectura ----

def _leer_archivo() -> dict:
    """Lo guardado, tolerante: cualquier forma rara cae a «nada guardado» (el gateway sigue con los valores de fábrica)."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return {"entradas": {}, "salidas": {}}
    if not isinstance(datos, dict):
        return {"entradas": {}, "salidas": {}}
    return {"entradas": datos["entradas"] if isinstance(datos.get("entradas"), dict) else {},
            "salidas": datos["salidas"] if isinstance(datos.get("salidas"), dict) else {}}


def _efectiva(fabrica: dict, guardada, campos: tuple) -> dict:
    """La fábrica pisada con los campos presentes y válidos de lo guardado."""
    fila = dict(fabrica)
    if not isinstance(guardada, dict):
        return fila
    for campo in campos:
        if campo not in guardada:
            continue
        try:
            if campo == "funcion":
                if guardada[campo] in FUNCIONES_ENTRADA:
                    fila[campo] = guardada[campo]
            elif campo == "direccion":
                fila[campo] = _direccion(guardada[campo], None)   # lo guardado ya está convertido
            elif campo == "unit_id":
                fila[campo] = _unit_id(guardada[campo])
            elif campo == "pulso_s":
                fila[campo] = _pulso(guardada[campo])
            elif campo == "invertido":
                fila[campo] = _invertido(guardada[campo])
        except ValueError:
            pass   # un campo roto vuelve al de fábrica, no tira abajo al gateway
    return fila


def entradas() -> dict:
    """{clave: fila efectiva} de las 32 entradas digitales. Lee el archivo una sola vez."""
    guardado = _leer_archivo()["entradas"]
    return {c: _efectiva(de_fabrica_entrada(c), guardado.get(c), _CAMPOS_ENTRADA) for c in iot_canales.ENTRADAS_DIGITALES}


def salidas() -> dict:
    """{clave: fila efectiva} de las 32 salidas. Lee el archivo una sola vez."""
    guardado = _leer_archivo()["salidas"]
    return {c: _efectiva(de_fabrica_salida(c), guardado.get(c), _CAMPOS_SALIDA) for c in iot_canales.SALIDAS}


def entrada(clave: str) -> dict:
    return _efectiva(de_fabrica_entrada(clave), _leer_archivo()["entradas"].get(clave), _CAMPOS_ENTRADA)


def salida(clave: str) -> dict:
    return _efectiva(de_fabrica_salida(clave), _leer_archivo()["salidas"].get(clave), _CAMPOS_SALIDA)


def cambios() -> dict:
    """Solo lo guardado (filas completas), para marcar en la web cuáles se modificaron."""
    todas_e, todas_s = entradas(), salidas()
    guardado = _leer_archivo()
    return {"entradas": {c: todas_e[c] for c in guardado["entradas"] if c in todas_e and todas_e[c] != de_fabrica_entrada(c)},
            "salidas": {c: todas_s[c] for c in guardado["salidas"] if c in todas_s and todas_s[c] != de_fabrica_salida(c)}}


# ---- guardado ----

def _fila_entrada(clave: str, fila) -> dict:
    if not isinstance(fila, dict):
        raise ValueError(f"{clave}: los datos de la entrada no tienen el formato esperado.")
    base = de_fabrica_entrada(clave)
    funcion = fila.get("funcion")
    if not _vacio(funcion):
        if funcion not in FUNCIONES_ENTRADA:
            raise ValueError(f"{clave}: la función «{funcion}» no existe (usar una de {', '.join(FUNCIONES_ENTRADA)}).")
        base["funcion"] = funcion
    if not _vacio(fila.get("direccion")):
        base["direccion"] = _direccion(fila["direccion"], base["funcion"])
    if not _vacio(fila.get("unit_id")):
        base["unit_id"] = _unit_id(fila["unit_id"])
    base["invertido"] = _invertido(fila.get("invertido"))
    return base


def _fila_salida(clave: str, fila) -> dict:
    if not isinstance(fila, dict):
        raise ValueError(f"{clave}: los datos de la salida no tienen el formato esperado.")
    base = de_fabrica_salida(clave)
    if not _vacio(fila.get("direccion")):
        base["direccion"] = _direccion(fila["direccion"], None)   # las bobinas no se convierten
    if not _vacio(fila.get("unit_id")):
        base["unit_id"] = _unit_id(fila["unit_id"])
    if not _vacio(fila.get("pulso_s")):
        base["pulso_s"] = _pulso(fila["pulso_s"])
    return base


def _etiqueta(clave: str) -> str:
    return f"{iot_canales.nombre(clave)} ({clave})"


def _chequear_pisadas(entradas_ef: dict, salidas_ef: dict) -> None:
    usadas = {}
    for clave, f in entradas_ef.items():
        llave = (f["unit_id"], f["funcion"], f["direccion"])
        if llave in usadas:
            raise ValueError(f"{_etiqueta(usadas[llave])} y {_etiqueta(clave)} usan el mismo punto del equipo "
                             f"(unit ID {f['unit_id']}, {f['funcion']}, dirección {f['direccion']}). Cada entrada tiene que tener el suyo.")
        usadas[llave] = clave
    usadas = {}
    for clave, f in salidas_ef.items():
        llave = (f["unit_id"], f["direccion"])
        if llave in usadas:
            raise ValueError(f"{_etiqueta(usadas[llave])} y {_etiqueta(clave)} usan la misma bobina "
                             f"(unit ID {f['unit_id']}, dirección {f['direccion']}). Cada salida tiene que tener la suya.")
        usadas[llave] = clave


def guardar(entradas_nuevas: dict, salidas_nuevas: dict) -> None:
    """Reemplaza todo lo guardado. Un campo en blanco es el valor de fábrica; solo se guardan las filas que difieren.
    Si algo no valida levanta ValueError y no toca el archivo."""
    ent, sal = {}, {}
    for clave, fila in (entradas_nuevas or {}).items():
        if clave not in iot_canales.ENTRADAS_DIGITALES:
            raise ValueError(f"Entrada desconocida: {clave!r}.")
        ent[clave] = _fila_entrada(clave, fila)
    for clave, fila in (salidas_nuevas or {}).items():
        if clave not in iot_canales.SALIDAS:
            raise ValueError(f"Salida desconocida: {clave!r}.")
        sal[clave] = _fila_salida(clave, fila)
    ent_ef = {c: ent.get(c, de_fabrica_entrada(c)) for c in iot_canales.ENTRADAS_DIGITALES}
    sal_ef = {c: sal.get(c, de_fabrica_salida(c)) for c in iot_canales.SALIDAS}
    _chequear_pisadas(ent_ef, sal_ef)
    datos = {"entradas": {c: f for c, f in ent.items() if f != de_fabrica_entrada(c)},
             "salidas": {c: f for c, f in sal.items() if f != de_fabrica_salida(c)}}
    with _lock:
        carpeta = os.path.dirname(_RUTA) or "."
        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=carpeta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _RUTA)   # el gateway lo lee en cada ciclo: nunca puede ver un archivo a medias
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
