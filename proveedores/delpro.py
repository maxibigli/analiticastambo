# -*- coding: utf-8 -*-
"""Proveedor de alimentación: "DelPro" — el tambo no tiene un mixer con
computadora propia, así que no hay ningún sistema externo que consultar.

En DelPro mismo no hay nada de alimentación (de 7.025 lactancias, 0 tienen
consumo o costo cargado — DelPro no lo trackea). Este módulo existe solo para
que un tambo pueda declarar explícitamente "no tengo proveedor de
alimentación" en la página "⚙ Configuración" en vez de quedar sin elegir
nada, con el mismo patrón honesto que `mixerone.py`: informa el motivo en vez
de fallar en silencio o mostrar datos de otro tambo.
"""

NOMBRE = "DelPro"

_MOTIVO = ("Este tambo no tiene un proveedor de alimentación externo configurado "
           "(DelPro no registra consumo/costo de alimentación).")


class DelProAlimentacionError(Exception):
    pass


def disponible() -> tuple[bool, str]:
    return False, _MOTIVO


def equipos() -> list:
    raise DelProAlimentacionError(_MOTIVO)


def lotes() -> list:
    raise DelProAlimentacionError(_MOTIVO)


def ingredientes() -> list:
    raise DelProAlimentacionError(_MOTIVO)


def consumos(desde, hasta) -> dict:
    raise DelProAlimentacionError(_MOTIVO)
