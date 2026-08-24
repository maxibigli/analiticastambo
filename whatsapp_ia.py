# -*- coding: utf-8 -*-
"""Números de WhatsApp autorizados a preguntarle a la IA de LactIA por
WhatsApp (ver app.py::webhook_whatsapp). Se guarda en un archivo JSON junto
al código, mismo criterio que alertas_canales.json: es una lista que arma el
propio tambo desde la interfaz, no un secreto -- las credenciales de Twilio
siguen yendo por variable de entorno.

Cada número queda atado a UN tambo fijo (no se elige por mensaje): así no
hace falta que quien pregunta sepa/escriba a qué tambo se refiere, y no hay
forma de que una pregunta se cuele para el tambo equivocado.
"""
import json
import os
import re
import threading

import tambos

_RUTA = os.path.join(os.path.dirname(__file__), "whatsapp_ia_autorizados.json")
_lock = threading.Lock()
_NUM_RE = re.compile(r"^\+\d{8,15}$")


def _leer() -> list:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def listar() -> list:
    """[{"numero": "+549...", "nombre": "...", "tambo": "ponderosa"}, ...]"""
    return _leer()


def tambo_autorizado(numero: str) -> str | None:
    """El tambo al que puede preguntar este número, o None si no está
    autorizado (número no encontrado en la lista)."""
    numero = (numero or "").strip()
    for item in _leer():
        if item.get("numero") == numero:
            return item.get("tambo")
    return None


def guardar(items: list) -> None:
    limpio = []
    vistos = set()
    for it in items:
        numero = str((it or {}).get("numero", "")).strip()
        tambo = str((it or {}).get("tambo", "")).strip()
        nombre = str((it or {}).get("nombre", "")).strip()
        if not _NUM_RE.match(numero):
            raise ValueError(f"Número inválido: {numero!r} (formato +5491122334455, sin espacios).")
        if not tambos.existe(tambo):
            raise ValueError(f"Tambo desconocido para {numero}: {tambo!r}.")
        if numero in vistos:
            raise ValueError(f"Número repetido: {numero}.")
        vistos.add(numero)
        limpio.append({"numero": numero, "nombre": nombre, "tambo": tambo})
    with _lock:
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=2)
