# -*- coding: utf-8 -*-
"""Qué se sabe de un envío que falló: ¿el mensaje salió o no? (lo usan `whatsapp`, `telegram_bot` y `correo`, y de ahí el envío programado de informes).

Un envío que falla DESPUÉS de que el canal aceptó el mensaje (una consulta de estado, un tiempo de lectura, un corte de la conexión) no prueba que no
salió: reintentarlo duplica el mensaje (el tambo ya tuvo un pico de decenas de mensajes repetidos). Por eso cada error de canal lleva dos marcas:

  * `no_salio`: se SABE que no salió nada (no se pudo conectar, el servidor lo rechazó, faltan las credenciales).
  * `reintentable`: además tiene sentido volver a intentarlo más tarde (no se pudo conectar; el límite de velocidad): reintentar no puede duplicar.

Sin marca (un tiempo de lectura, una conexión cortada a mitad de camino, un 5xx) el resultado es INCIERTO: se avisa y no se reenvía solo.
"""
import requests
import urllib3.exceptions


def no_llego(exc) -> bool:
    """True si el pedido ni siquiera llegó al servidor (no se pudo resolver el nombre, ni conectar): ahí reintentar no puede duplicar nada. Un tiempo de espera al
    LEER o una conexión cortada después de mandar el pedido NO entran: el servidor pudo haberlo recibido."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        causa = exc.args[0] if exc.args else None
        razon = getattr(causa, "reason", None)                                      # MaxRetryError.reason: por qué no se pudo conectar
        return isinstance(razon, (urllib3.exceptions.NewConnectionError, urllib3.exceptions.ConnectTimeoutError))
    return False


def marcas(no_salio: bool, reintentable: bool = False) -> dict:
    """Los argumentos de un error de canal (`no_salio` y `reintentable`; lo segundo solo si lo primero)."""
    return {"no_salio": bool(no_salio), "reintentable": bool(no_salio and reintentable)}
