# -*- coding: utf-8 -*-
"""Cómo se ve cada sensor del panel IoT: estilo (digital o analógico), escala del dial y zonas de color.

Cada sensor tiene hasta 4 límites, TODOS opcionales:
    rojo_bajo      rojo si baja de este valor
    amarillo_bajo  amarillo si baja de este valor
    amarillo_alto  amarillo si sube de este valor
    rojo_alto      rojo si sube de este valor
El verde es lo que queda en el medio. Sirve para «la leche no debe pasar de 4 °C» (solo límites de arriba) y para
«el vacío tiene que estar entre 48 y 52 kPa» (los cuatro).

**No hay valores por defecto, a propósito**: sin ningún límite cargado la tarjeta no tiene color. Es la misma regla de
la app para los umbrales de retirada o de preparación: un límite inventado marcaría en rojo algo que está bien.

Se guarda en un JSON chico junto al código (estado propio de cada instalación, no va a git), como iot_canales_nombres.json.
Deliberadamente SIN import de iot_monitoreo (que es quien lo importa a él).
"""
import json
import os
import tempfile
import threading

from iot_analogicas import numero as _numero

_RUTA = os.path.join(os.path.dirname(__file__), "iot_vumetros.json")
_lock = threading.Lock()

ESTILOS = ("digital", "analogico")
LIMITES = ("rojo_bajo", "amarillo_bajo", "amarillo_alto", "rojo_alto")   # de menor a mayor
_NOMBRES = {"rojo_bajo": "Rojo si baja de", "amarillo_bajo": "Amarillo si baja de",
            "amarillo_alto": "Amarillo si sube de", "rojo_alto": "Rojo si sube de"}   # los de la pantalla
_NUMEROS = ("min", "max") + LIMITES
_VACIO = {"estilo": "digital", **{k: None for k in _NUMEROS}}


def cargar() -> dict:
    """clave -> configuración, solo de los sensores que el tambo tocó; {} si no hay archivo (o está roto: antes
    mostrar las tarjetas sin color que dejar el panel sin andar)."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(datos, dict):
        return {}
    salida = {}
    for clave, c in datos.items():
        if not isinstance(c, dict):
            continue
        estilo = c.get("estilo") if c.get("estilo") in ESTILOS else "digital"
        salida[clave] = {"estilo": estilo, **{k: c.get(k) for k in _NUMEROS}}
    return salida


def config_de(clave: str, configuradas: dict = None) -> dict:
    """La configuración de UN sensor, con los valores por defecto (digital, sin escala propia, sin límites)."""
    configuradas = cargar() if configuradas is None else configuradas
    return {**_VACIO, **(configuradas.get(clave) or {})}


def guardar(nuevo: dict, sensores_validos) -> None:
    """Valida TODO antes de tocar el archivo: un guardado que falla no pisa lo anterior."""
    limpio = {}
    for clave, c in (nuevo or {}).items():
        if clave not in sensores_validos:
            raise ValueError(f"Sensor desconocido: {clave!r}.")
        c = c or {}
        estilo = (c.get("estilo") or "digital")
        if estilo not in ESTILOS:
            raise ValueError(f"{clave}: el estilo tiene que ser «digital» o «analogico» (llegó {estilo!r}).")
        valores = {k: _numero(c.get(k), k, clave) for k in _NUMEROS}

        if valores["min"] is not None and valores["max"] is not None and valores["min"] >= valores["max"]:
            raise ValueError(f"{clave}: el principio de la escala tiene que ser menor que el final.")
        cargados = [(k, valores[k]) for k in LIMITES if valores[k] is not None]
        for (k1, v1), (k2, v2) in zip(cargados, cargados[1:]):
            if v1 > v2:
                raise ValueError(
                    f"{clave}: los límites tienen que ir de menor a mayor. «{_NOMBRES[k1]}» ({v1:g}) no puede ser "
                    f"mayor que «{_NOMBRES[k2]}» ({v2:g}).")

        entrada = {"estilo": estilo, **valores}
        if entrada != _VACIO:   # una fila que no se apartó de lo habitual no hace falta guardarla
            limpio[clave] = entrada

    with _lock:
        # A un temporal y se reemplaza: la app lo lee en cada pedido y no debe ver un archivo a medias.
        carpeta = os.path.dirname(_RUTA) or "."
        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=carpeta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(limpio, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _RUTA)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def zona(valor, cfg: dict):
    """«verde», «amarillo» o «rojo»; None si no hay lectura o el sensor no tiene ningún límite cargado.

    Justo en un límite todavía es la zona de adentro: con «amarillo si sube de 4», 4,0 es verde y 4,1 amarillo."""
    if valor is None:
        return None
    rojo_bajo, amarillo_bajo, amarillo_alto, rojo_alto = (cfg.get(k) for k in LIMITES)
    if all(v is None for v in (rojo_bajo, amarillo_bajo, amarillo_alto, rojo_alto)):
        return None
    if (rojo_bajo is not None and valor < rojo_bajo) or (rojo_alto is not None and valor > rojo_alto):
        return "rojo"
    if (amarillo_bajo is not None and valor < amarillo_bajo) or (amarillo_alto is not None and valor > amarillo_alto):
        return "amarillo"
    return "verde"
