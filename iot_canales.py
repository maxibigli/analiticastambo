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


def guardar(nombres_nuevos: dict) -> None:
    limpio = {}
    for clave, nombre in (nombres_nuevos or {}).items():
        if clave not in CLAVES_VALIDAS:
            raise ValueError(f"Canal desconocido: {clave!r}.")
        nombre = str(nombre).strip()
        if nombre:   # vacío = "usar el nombre generico", no hace falta guardarlo
            limpio[clave] = nombre
    with _lock:
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=2)
