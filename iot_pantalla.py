# -*- coding: utf-8 -*-
"""Las tarjetas de la pantalla ESP32: las mismas de la página «Monitoreo IoT», armadas acá.

La pantalla no decide nada. Recibe cada sensor con sus textos, su color y su dial ya calculados, y cada salida con su nombre. Los
textos y las reglas son los de la web (`templates/index.html`, `iotTarjetaHTML`, `iotTarjetaDigitalHTML` y `dialIotSVG`): si se
cambia una tarjeta allá, se cambia acá.

  * Sensores: todos los del catálogo, en su orden, y el ITH. Igual que la web, también los «sin instalar».
  * Salidas: las que tienen nombre y las que usa una etapa del lavado; además, cualquiera que esté prendida o con un apagado
    pendiente por voz (un relé encendido no puede quedar escondido).
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP

import iot_canales
import iot_monitoreo
import iot_vumetros
import lavado_programa
import voz_comandos

ZONA_TEXTO = {"verde": "Normal", "amarillo": "Atención", "rojo": "Fuera de rango"}
_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def numero(v) -> str:
    """Como `fmt` de la web: coma decimal, punto de miles y hasta un decimal."""
    d = Decimal(v).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    texto = f"{d:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if texto.endswith(",0"):
        texto = texto[:-2]
    return "0" if texto == "-0" else texto


def fecha_hora(iso) -> str:
    """'2026-11-26T10:00:30' -> '2026-11-26 10:00'; sin dato, ''."""
    return str(iso).replace("T", " ")[:16] if iso else ""


def _ultimo_dato(s: dict) -> str:
    if not s["fecha_hora"]:
        return "nunca mandó datos"
    d = datetime.datetime.fromisoformat(s["fecha_hora"])
    if s["tipo"] == "digital":
        ultimo = s.get("ultimo_estado") or ""
    else:
        ultimo = "" if s.get("ultimo_valor") is None else numero(s["ultimo_valor"]) + (s.get("unidad") or "")
    return f"último dato: {_DIAS[d.weekday()]} {d:%d/%m %H:%M}" + (f" ({ultimo})" if ultimo else "")


def _dial(s: dict):
    """El dial con todo en fracciones de la escala (0 a 1): los tramos de color y dónde va la aguja (None sin lectura)."""
    minimo, maximo = s["min"], s["max"]
    if maximo <= minimo:
        return None
    fraccion = lambda v: round(min(1.0, max(0.0, (v - minimo) / (maximo - minimo))), 4)
    limites = s["limites"]
    cortes = sorted({v for v in limites.values() if v is not None and minimo < v < maximo})
    bordes = [minimo, *cortes, maximo]
    tramos = [[fraccion(a), fraccion(b), iot_vumetros.zona((a + b) / 2, limites)] for a, b in zip(bordes, bordes[1:])]
    return {"aguja": None if s["valor"] is None else fraccion(s["valor"]), "tramos": tramos,
            "min_texto": numero(minimo), "max_texto": numero(maximo)}


def _tarjeta(s: dict) -> dict:
    base = {"clave": s["clave"], "tipo": s["tipo"], "label": s["label"], "tamano": s.get("tamano") or "mediano",
            "zona": s.get("zona"), "sin_datos": s["sin_datos"]}
    chip = "Sin datos" if s["sin_datos"] else ""
    if s["tipo"] == "digital":
        if s["sin_datos"]:
            detalle = _ultimo_dato(s)
        elif not s.get("entrada"):
            detalle = "sin entrada asignada"
        else:
            detalle = fecha_hora(s["fecha_hora"]) or "esperando lectura"
        # Sin historial: la pantalla solo grafica valores medidos, no los cambios de un contacto.
        return {**base, "valor": s.get("estado") or "—", "chip": chip, "detalle": detalle, "historial": False, "dial": None}
    sin_valor = s["valor"] is None
    if s["sin_datos"]:
        detalle = _ultimo_dato(s)
    else:
        detalle = "sin instalar" if sin_valor else fecha_hora(s["fecha_hora"])
    return {**base, "valor": "—" if sin_valor else numero(s["valor"]) + (s.get("unidad") or ""),
            "unidad": s.get("unidad") or "", "chip": chip or ZONA_TEXTO.get(s.get("zona"), ""), "detalle": detalle,
            "historial": True, "dial": _dial(s) if s.get("estilo") == "analogico" else None}


def _salidas() -> list:
    nombres = iot_canales.nombres()
    de_lavado = {r for etapa in lavado_programa.etapas() for r in etapa.get("reles", [])}
    por_voz = set(voz_comandos.estado()) | set(voz_comandos.apagados_pendientes())
    elegidas = set(nombres) | de_lavado | por_voz
    return [{"clave": c, "label": nombres.get(c) or iot_canales.generico(c),
             "ocupada": iot_monitoreo.motivo_salida_ocupada(c)}
            for c in iot_canales.SALIDAS if c in elegidas]


def tarjetas(ahora: datetime.datetime = None) -> dict:
    """{"sensores": [...], "salidas": [...]} para la pantalla, con las lecturas de `ahora`."""
    return {"sensores": [_tarjeta(s) for s in iot_monitoreo.lecturas_actuales(ahora)], "salidas": _salidas()}
