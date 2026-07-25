# -*- coding: utf-8 -*-
"""Qué canales de alerta están tildados/activados en la interfaz. Se guarda en
un archivo JSON junto al código para que sobreviva a un reinicio del servidor
(a diferencia de las credenciales, que van por variable de entorno, esto es
una preferencia del usuario, no un secreto)."""
import json
import os
import threading

_RUTA = os.path.join(os.path.dirname(__file__), "alertas_canales.json")
CANALES = ("whatsapp", "telegram", "correo")
_lock = threading.Lock()


def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def estado() -> dict:
    """{"whatsapp": True/False, "telegram": ..., "correo": ...} -- activado por
    defecto si el usuario todavía no tocó el tilde."""
    guardado = _leer()
    return {c: guardado.get(c, True) for c in CANALES}


def activo(canal: str) -> bool:
    return estado().get(canal, True)


def set_activo(canal: str, valor: bool) -> None:
    if canal not in CANALES:
        raise ValueError(f"Canal desconocido: {canal}")
    with _lock:
        actual = _leer()
        actual[canal] = bool(valor)
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(actual, f)
