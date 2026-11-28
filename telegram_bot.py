# -*- coding: utf-8 -*-
"""Envío de alertas por Telegram (gratis, sin límite de mensajes y sin el
problema de "opt-in" del sandbox de WhatsApp).

Credenciales SOLO por variable de entorno (nunca en el código):
  TELEGRAM_BOT_TOKEN  -- token del bot, lo da @BotFather al crearlo
  TELEGRAM_CHAT_ID    -- id del chat destino. Se obtiene hablándole una vez al
                         bot (cualquier mensaje) y consultando
                         https://api.telegram.org/bot<token>/getUpdates
"""
import os

import requests

import envio_red

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_DOC_URL = "https://api.telegram.org/bot{token}/sendDocument"
TELEGRAM_UPDATES_URL = "https://api.telegram.org/bot{token}/getUpdates"
CAPTION_MAX = 1024                  # el límite de Telegram para el texto que acompaña a un archivo
TIMEOUT_DOC_S = 60
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TelegramError(Exception):
    """Un error de Telegram. `no_salio`: se SABE que el mensaje no salió; `reintentable`: además se puede volver a intentar sin riesgo de duplicarlo (ver `envio_red`)."""

    def __init__(self, mensaje="", no_salio=False, reintentable=False):
        super().__init__(mensaje)
        self.__dict__.update(envio_red.marcas(no_salio, reintentable))


def configurado() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def enviar(mensaje: str, destino: str | None = None) -> None:
    """`destino` opcional: el chat de una persona (Vigilat). Sin él va al chat de siempre, TELEGRAM_CHAT_ID."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = destino or os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ver INSTALL.md).", no_salio=True)
    try:
        r = requests.post(TELEGRAM_API_URL.format(token=token),
                           data={"chat_id": chat_id, "text": mensaje}, timeout=15)
    except Exception as exc:  # noqa: BLE001 -- el texto de una excepción de `requests` lleva la URL de la API, o sea el token del bot
        sin_llegar = envio_red.no_llego(exc)
        raise TelegramError(f"No se pudo conectar con Telegram ({type(exc).__name__}).", no_salio=sin_llegar, reintentable=sin_llegar) from None
    if r.status_code != 200:
        raise TelegramError(f"Telegram respondió {r.status_code}: {r.text[:300]}", no_salio=400 <= r.status_code < 500, reintentable=r.status_code == 429)       # 429: límite de velocidad


def enviar_documento(nombre: str, datos: bytes, texto: str = "", mime: str = MIME_XLSX) -> None:
    """Manda un archivo (el Excel de un informe programado) con un texto al pie, que Telegram limita a `CAPTION_MAX` letras. Ningún error lleva
    el texto de la excepción ni de la respuesta: la URL de la API contiene el token del bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ver INSTALL.md).", no_salio=True)
    pie = texto if len(texto) <= CAPTION_MAX else texto[:CAPTION_MAX - 1].rstrip() + "…"
    try:
        r = requests.post(TELEGRAM_DOC_URL.format(token=token), data={"chat_id": chat_id, "caption": pie},
                          files={"document": (nombre, datos, mime)}, timeout=TIMEOUT_DOC_S)
    except Exception as exc:  # noqa: BLE001
        sin_llegar = envio_red.no_llego(exc)
        raise TelegramError(f"No se pudo conectar con Telegram ({type(exc).__name__}).", no_salio=sin_llegar, reintentable=sin_llegar) from None
    if r.status_code != 200:
        raise TelegramError(f"Telegram respondió {r.status_code}.", no_salio=400 <= r.status_code < 500, reintentable=r.status_code == 429)


def chats_recientes() -> list:
    """Quiénes le escribieron al bot hace poco, para saber el número de chat de cada persona:
    `[{"id": "123", "nombre": "Juan Pérez"}]`. Solo lee (`getUpdates`, sin confirmar los mensajes)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramError("Falta TELEGRAM_BOT_TOKEN (ver INSTALL.md).", no_salio=True)
    try:
        r = requests.get(TELEGRAM_UPDATES_URL.format(token=token), timeout=15)
    except Exception as exc:  # noqa: BLE001 -- la URL lleva el token: nunca el texto de la excepción
        raise TelegramError(f"No se pudo conectar con Telegram ({type(exc).__name__}).", no_salio=True) from None
    if r.status_code != 200:
        raise TelegramError(f"Telegram respondió {r.status_code}.", no_salio=True)
    try:
        novedades = r.json()["result"]
    except Exception:  # noqa: BLE001
        raise TelegramError("Telegram contestó algo que no se entiende.", no_salio=True) from None
    chats = {}
    for u in novedades:
        mensaje = u.get("message") or u.get("edited_message") or u.get("my_chat_member") or {}
        chat = mensaje.get("chat") or {}
        if "id" not in chat:
            continue
        nombre = (chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                  or chat.get("username") or "(sin nombre)")
        chats[str(chat["id"])] = nombre
    return [{"id": i, "nombre": n} for i, n in chats.items()]
