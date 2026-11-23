# -*- coding: utf-8 -*-
"""Los canales del gateway PUSR M300 (hasta 32 entradas analógicas, 32 digitales y 32 salidas) y los nombres que el tambo
les pone desde ⚙ Configuración › 🔌 Entradas/Salidas.

Es la única lista de canales que existe: el gateway (`iot_lavado`), el catálogo de sensores (`iot_sensores`), las entradas
analógicas (`iot_analogicas`), el lavado automático y los comandos de voz la toman de acá. 32 es el tope que se puede
nombrar y asignar, no lo que el hardware tiene hoy (2 digitales, 6 analógicas y 14 salidas con los módulos de
expansión): el gateway solo consulta lo que está asignado a un sensor o tiene nombre (ver `iot_lavado.canales_a_leer`).

Claves (no cambian nunca: están en el historial y en los sensores guardados):
  * entradas digitales 1..32: `lavado_rotativa` (1), `barrido_rotativa` (2), `di_3` .. `di_32`;
  * entradas analógicas 1..32: `ai_1` .. `ai_32`;
  * salidas 1..32: `do_1` .. `do_32`.

Los nombres se guardan en un archivo JSON chico junto al código, mismo criterio que alertas_canales.json: es contenido propio
de cada instalación, no del código. Deliberadamente SIN import de ningún módulo del proyecto (todos los demás importan a éste):
así no se arma un ciclo.
"""
import json
import os
import threading
import unicodedata

_RUTA = os.path.join(os.path.dirname(__file__), "iot_canales_nombres.json")
_lock = threading.Lock()

CANTIDAD = 32

ENTRADAS_DIGITALES = tuple(
    {1: "lavado_rotativa", 2: "barrido_rotativa"}.get(i, f"di_{i}") for i in range(1, CANTIDAD + 1))
ENTRADAS_ANALOGICAS = tuple(f"ai_{i}" for i in range(1, CANTIDAD + 1))
SALIDAS = tuple(f"do_{i}" for i in range(1, CANTIDAD + 1))

CLAVES_VALIDAS = set(ENTRADAS_DIGITALES) | set(ENTRADAS_ANALOGICAS) | set(SALIDAS)

# Nombre genérico (el que se ve mientras el tambo no pone uno), por clave.
_GENERICOS = {
    **{c: f"Entrada {i}" for i, c in enumerate(ENTRADAS_DIGITALES, 1)},
    "lavado_rotativa": "Entrada 1 (lavado)", "barrido_rotativa": "Entrada 2 (barrido)",
    **{c: f"Analógica {i}" for i, c in enumerate(ENTRADAS_ANALOGICAS, 1)},
    **{c: f"Actuador {i}" for i, c in enumerate(SALIDAS, 1)},
}


def generico(clave: str) -> str:
    return _GENERICOS[clave]


def numero(clave: str) -> int:
    """1..32: el lugar del canal dentro de su grupo (entrada digital 3, salida 12…)."""
    for grupo in (ENTRADAS_DIGITALES, ENTRADAS_ANALOGICAS, SALIDAS):
        if clave in grupo:
            return grupo.index(clave) + 1
    raise KeyError(clave)


def _grupo(clave: str):
    return next(g for g in (ENTRADAS_DIGITALES, ENTRADAS_ANALOGICAS, SALIDAS) if clave in g)


def nombres() -> dict:
    """clave -> nombre personalizado. Solo trae lo que el tambo cambió; lo que no está acá se queda con el nombre genérico."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return {c: n for c, n in datos.items() if c in CLAVES_VALIDAS and isinstance(n, str) and n.strip()} \
        if isinstance(datos, dict) else {}


def nombre(clave: str) -> str:
    """El nombre con que se ve un canal: el del tambo, o el genérico."""
    return nombres().get(clave) or generico(clave)


def _comparable(nombre: str) -> str:
    """Minúsculas, sin tildes y con los espacios colapsados -- así "Bomba de
    Agua" y "bomba de agua" cuentan como el MISMO nombre. Es la misma
    normalización que usa voz_comandos para matchear lo que se dice en voz
    alta; se repite acá (chiquita) en vez de importarla para no romper la
    regla de que este módulo no importa a nadie del proyecto."""
    nombre = unicodedata.normalize("NFD", nombre.lower())
    nombre = "".join(c for c in nombre if unicodedata.category(c) != "Mn")
    return " ".join(nombre.split())


def guardar(nombres_nuevos: dict) -> None:
    limpio = {}
    vistos = {}   # (grupo, nombre comparable) -> primera clave que lo usó
    for clave, nombre in (nombres_nuevos or {}).items():
        if clave not in CLAVES_VALIDAS:
            raise ValueError(f"Canal desconocido: {clave!r}.")
        nombre = str(nombre).strip()
        if nombre:   # vacío = "usar el nombre generico", no hace falta guardarlo
            # Nombres repetidos DENTRO de un mismo grupo NO se aceptan: los
            # comandos de voz eligen el actuador por su nombre ("prender bomba
            # de agua"), y con dos salidas llamadas igual no hay forma de saber
            # cuál se pidió -- antes de este chequeo se activaba siempre la
            # primera, en silencio (ver voz_comandos._buscar_actuador). En las
            # entradas el mismo nombre dos veces haría ilegible el desplegable.
            llave = (id(_grupo(clave)), _comparable(nombre))
            repetido = vistos.get(llave)
            if repetido:
                raise ValueError(
                    f"El nombre {nombre!r} está repetido ({repetido} y {clave}). "
                    "Cada canal del mismo tipo necesita un nombre distinto: los "
                    "comandos de voz eligen el actuador por su nombre."
                )
            vistos[llave] = clave
            limpio[clave] = nombre
    with _lock:
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=2)
