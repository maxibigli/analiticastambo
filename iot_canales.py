# -*- coding: utf-8 -*-
"""Nombres personalizados de las 8 entradas + 8 salidas del gateway PUSR
M300 (ver iot_monitoreo.ENTRADAS_PANEL/SALIDAS_PANEL), cargados por el tambo
desde ⚙ Configuración › 🔌 Entradas/Salidas -- reemplazan el "Entrada 3"/
"Actuador 1" genérico tanto en el panel de Actuadores de la pantalla ESP32
como en cualquier otra pantalla que use iot_monitoreo.panel_io().

Se guarda en un archivo JSON chico junto al código, mismo criterio que
alertas_canales.json/whatsapp_ia_autorizados.json: es contenido propio de
cada instalación, no del código.

Deliberadamente SIN import de iot_monitoreo/iot_lavado (que sí importan este
módulo para pintar los nombres): la lista de claves válidas se declara acá
misma para no armar un import circular.
"""
import json
import os
import threading
import unicodedata

_RUTA = os.path.join(os.path.dirname(__file__), "iot_canales_nombres.json")
_lock = threading.Lock()

CLAVES_VALIDAS = {
    "lavado_rotativa", "barrido_rotativa",
    "di_3", "di_4", "di_5", "di_6", "di_7", "di_8",
    "do_1", "do_2", "do_3", "do_4", "do_5", "do_6", "do_7", "do_8",
}


def nombres() -> dict:
    """clave -> nombre personalizado. Solo trae lo que el tambo cambió; lo
    que no está acá se queda con la etiqueta genérica default (ver
    iot_monitoreo.ENTRADAS_PANEL/SALIDAS_PANEL)."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


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
    vistos = {}   # nombre comparable -> primera clave que lo usó
    for clave, nombre in (nombres_nuevos or {}).items():
        if clave not in CLAVES_VALIDAS:
            raise ValueError(f"Canal desconocido: {clave!r}.")
        nombre = str(nombre).strip()
        if nombre:   # vacío = "usar el nombre generico", no hace falta guardarlo
            # Nombres repetidos NO se aceptan: los comandos de voz eligen el
            # actuador por su nombre ("prender bomba de agua"), y con dos
            # salidas llamadas igual no hay forma de saber cuál se pidió --
            # antes de este chequeo se activaba siempre la primera, en
            # silencio (ver voz_comandos._buscar_actuador).
            repetido = vistos.get(_comparable(nombre))
            if repetido:
                raise ValueError(
                    f"El nombre {nombre!r} está repetido ({repetido} y {clave}). "
                    "Cada entrada/salida necesita un nombre distinto: los "
                    "comandos de voz eligen el actuador por su nombre."
                )
            vistos[_comparable(nombre)] = clave
            limpio[clave] = nombre
    with _lock:
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=2)
