# -*- coding: utf-8 -*-
"""Modo televisor: token de acceso y preferencias de la pantalla.

Una pantalla colgada en el tambo NO puede iniciar sesión como un usuario, así
que entra con un token en la URL (decisión del tambo, ver CLAUDE.md). Este
módulo es lo único que sabe del token; quién lo valida y qué se muestra vive
en `app.py`.

DELIBERADAMENTE NO IMPORTA NADA DE LA APP (ni `app`, ni `tablero`, ni
`rutina`): lo importan ellos a él. Mismo criterio que `iot_canales.py` — así no
hay forma de armar un ciclo de imports.

EL TOKEN ES UNA LLAVE, no un identificador: quien tenga la URL ve los datos del
tambo. Por eso:
  * se GENERA (32 bytes al azar), no se elige;
  * se compara con `hmac.compare_digest`, no con `==`, para no filtrar en
    cuánto coincide por el tiempo que tarda;
  * se puede rotar desde ⚙ Configuración, y al rotarlo la pantalla vieja deja
    de funcionar en el próximo refresco;
  * la pantalla lo canjea por una cookie y limpia la URL, así no queda a la
    vista de quien pase por delante del televisor ni en el historial.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pantalla_tv.json")

# Cuánto dura cada vista antes de pasar a la siguiente. 20s es lo medido como
# cómodo para leer de lejos sin que la pantalla se sienta estática; el tambo lo
# puede cambiar.
SEGUNDOS_POR_VISTA = 20
SEGUNDOS_MIN, SEGUNDOS_MAX = 5, 300

# Las vistas que rota la pantalla, en orden. La clave viaja al frontend.
VISTAS = ("ordeno", "produccion", "grupo", "rutina", "alertas")
VISTAS_LABEL = {
    "ordeno": "Ordeño en vivo",
    "produccion": "Producción del día",
    "grupo": "Producción por grupo",
    "rutina": "Calidad de rutina",
    "alertas": "Alertas y pendientes",
}

# Botón "Transmitir a Chromecast" en ⚙ Configuración › Modo televisor. El
# Application ID sale de la consola de Google Cast (cast.google.com/publish,
# $5 únicos) al registrar el receiver apuntando a la URL de `pantalla_tv_vista`
# -- NO es un secreto (viaja igual en el JS del sender, a la vista de
# cualquiera), así que alcanza con una constante acá. En `None` el botón de
# Configuración queda oculto: todavía no se registró ninguna aplicación.
CAST_APP_ID = "CFA16559"


def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _guardar(d: dict) -> None:
    with open(_RUTA, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def token() -> str:
    """El token vigente. Lo crea la primera vez que se lo pide."""
    d = _leer()
    t = d.get("token")
    if not isinstance(t, str) or len(t) < 20:
        return rotar()
    return t


def rotar() -> str:
    """Genera un token nuevo. La pantalla que estaba andando deja de andar."""
    d = _leer()
    d["token"] = secrets.token_urlsafe(32)
    _guardar(d)
    return d["token"]


def valido(candidato: str | None) -> bool:
    """Compara en tiempo constante. Un `==` filtra, por lo que tarda, cuántos
    caracteres coinciden — con eso un token se adivina de a uno."""
    if not candidato or not isinstance(candidato, str):
        return False
    return hmac.compare_digest(candidato, token())


def segundos_por_vista() -> int:
    d = _leer()
    v = d.get("segundos_por_vista")
    if isinstance(v, int) and SEGUNDOS_MIN <= v <= SEGUNDOS_MAX:
        return v
    return SEGUNDOS_POR_VISTA


def vistas_activas() -> list[str]:
    """Qué vistas rota la pantalla. Vacío o inválido = todas."""
    d = _leer()
    v = d.get("vistas")
    if isinstance(v, list):
        elegidas = [x for x in VISTAS if x in v]      # respeta el orden de VISTAS
        if elegidas:
            return elegidas
    return list(VISTAS)


def guardar_preferencias(segundos=None, vistas=None) -> None:
    d = _leer()
    if segundos is not None:
        try:
            s = int(segundos)
        except (TypeError, ValueError):
            raise ValueError("Los segundos por vista tienen que ser un número.")
        if not (SEGUNDOS_MIN <= s <= SEGUNDOS_MAX):
            raise ValueError(f"Los segundos por vista van de {SEGUNDOS_MIN} a {SEGUNDOS_MAX}.")
        d["segundos_por_vista"] = s
    if vistas is not None:
        if not isinstance(vistas, list):
            raise ValueError("Las vistas tienen que venir como lista.")
        elegidas = [x for x in VISTAS if x in vistas]
        if not elegidas:
            raise ValueError("Hay que dejar al menos una vista activa.")
        d["vistas"] = elegidas
    _guardar(d)
