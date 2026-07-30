# -*- coding: utf-8 -*-
"""Proveedor de alimentación: MixerOne — TODAVÍA NO CONECTADO.

San José usa MixerOne (no Haasten) para su mixer, pero conectar la integración
real se dejó para más adelante (2026-07-28, a pedido del tambo). Este módulo
existe para que `proveedores.de("san_jose")` declare el proveedor REAL en vez
de caer al de otro tambo (Haasten, el mixer de La Ponderosa) por defecto —que
terminaría cruzando los grupos de DelPro de San José contra el mixer de otro
tambo, sin ningún sentido.

Mientras no esté conectado, toda función informa con `disponible()` que falta
la integración (mismo patrón que `haasten.disponible()` cuando faltan las
credenciales): la pantalla de Alimentación sigue mostrando el lado DelPro sin
romperse, con el motivo a la vista en vez de un error crudo o datos de otro
tambo. Cuando se conecte MixerOne de verdad, este archivo se reemplaza por la
integración real — mismo lugar, mismo nombre de módulo, nada más para tocar.
"""

NOMBRE = "MixerOne"

_MOTIVO = ("El proveedor MixerOne todavía no está conectado para este tambo "
           "(integración pendiente).")


class MixerOneError(Exception):
    pass


def disponible() -> tuple[bool, str]:
    """(True, "") si está conectado; si no, (False, por qué). Ver la nota del
    módulo: hoy siempre False."""
    return False, _MOTIVO


def equipos() -> list:
    raise MixerOneError(_MOTIVO)


def lotes() -> list:
    raise MixerOneError(_MOTIVO)


def ingredientes() -> list:
    raise MixerOneError(_MOTIVO)


def consumos(desde, hasta) -> dict:
    raise MixerOneError(_MOTIVO)
