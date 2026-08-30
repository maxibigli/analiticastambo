# -*- coding: utf-8 -*-
"""Dirección del gateway PUSR M300 en la red del tambo -- configurable desde
⚙ Configuración para no tener que tocar código si el tambo le cambia la IP
al gateway.

`iot_lavado.py` la lee UNA SOLA VEZ al arrancar, no en caliente: es un dato
que cambia poquísimo (un gateway de este tipo normalmente se configura con
IP fija en la red del tambo, a diferencia de la IP de esta PC que sí es por
DHCP), así que no vale la pena una recarga en caliente -- si se cambia acá,
hay que reiniciar ese proceso (`iniciar_iot.bat`) para que tome el valor
nuevo, mismo criterio que cualquier otro cambio de configuración de un
proceso que no es la app Flask.
"""
import json
import os

_RUTA = os.path.join(os.path.dirname(__file__), "iot_conexion.json")
HOST_DEFECTO = "192.168.1.1"
PUERTO_DEFECTO = 502


def config() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return {
            "host": datos.get("host") or HOST_DEFECTO,
            "port": int(datos.get("port") or PUERTO_DEFECTO),
        }
    except (FileNotFoundError, ValueError):
        return {"host": HOST_DEFECTO, "port": PUERTO_DEFECTO}


def guardar(host: str, port) -> None:
    host = (host or "").strip()
    if not host:
        raise ValueError("La IP no puede estar vacía.")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("El puerto tiene que ser un número.")
    if not (1 <= port <= 65535):
        raise ValueError("El puerto tiene que estar entre 1 y 65535.")
    with open(_RUTA, "w", encoding="utf-8") as f:
        json.dump({"host": host, "port": port}, f, ensure_ascii=False, indent=2)
