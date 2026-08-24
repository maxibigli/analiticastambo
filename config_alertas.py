# -*- coding: utf-8 -*-
"""Qué canales de alerta están tildados/activados en la interfaz. Se guarda en
un archivo JSON junto al código para que sobreviva a un reinicio del servidor
(a diferencia de las credenciales, que van por variable de entorno, esto es
una preferencia del usuario, no un secreto)."""
import json
import os
import re
import threading

_RUTA = os.path.join(os.path.dirname(__file__), "alertas_canales.json")
CANALES = ("whatsapp", "telegram", "correo")
MAX_HORARIOS = 5
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


_HORA_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def horario() -> dict:
    """{"dias": [0..6] (lunes=0, igual que datetime.weekday()), "horas":
    ["08:00", "20:00"]} -- todos los días a las 8:00 y 20:00 por defecto (el
    comportamiento de antes de que esto fuera configurable), si el usuario
    todavía no lo tocó o guardó algo inválido."""
    guardado = _leer().get("horario") or {}
    dias = guardado.get("dias")
    horas = guardado.get("horas")
    if not isinstance(dias, list) or not dias or not all(isinstance(d, int) and 0 <= d <= 6 for d in dias):
        dias = list(range(7))
    if not isinstance(horas, list) or not horas or not all(isinstance(h, str) and _HORA_RE.match(h) for h in horas):
        horas = ["08:00", "20:00"]
    return {"dias": sorted(set(dias)), "horas": sorted(set(horas))}


def set_horario(dias: list, horas: list) -> None:
    try:
        dias = sorted({int(d) for d in dias})
    except (TypeError, ValueError):
        raise ValueError("Los días tienen que ser números.")
    horas = sorted({str(h) for h in horas})
    if not dias or any(d < 0 or d > 6 for d in dias):
        raise ValueError("Elegí al menos un día de la semana.")
    if not horas:
        raise ValueError("Elegí al menos un horario.")
    if not all(_HORA_RE.match(h) for h in horas):
        raise ValueError("Los horarios tienen que tener formato HH:MM.")
    if len(horas) > MAX_HORARIOS:
        raise ValueError(f"Como máximo {MAX_HORARIOS} horarios por día.")
    with _lock:
        actual = _leer()
        actual["horario"] = {"dias": dias, "horas": horas}
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(actual, f)


def checklist_resumen_activo() -> bool:
    """Si las novedades del check-list (fallas abiertas/resueltas) van en el
    resumen periódico. No tildado por defecto, mismo criterio que los
    indicadores del Tablero (`incluir_resumen`): el tambo elige qué mandar."""
    return bool(_leer().get("checklist_resumen", False))


def set_checklist_resumen(valor: bool) -> None:
    with _lock:
        actual = _leer()
        actual["checklist_resumen"] = bool(valor)
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(actual, f)
