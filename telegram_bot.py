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

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramError(Exception):
    pass


def configurado() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def enviar(mensaje: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise TelegramError("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (ver INSTALL.md).")
    r = requests.post(TELEGRAM_API_URL.format(token=token),
                       data={"chat_id": chat_id, "text": mensaje}, timeout=15)
    if r.status_code != 200:
        raise TelegramError(f"Telegram respondió {r.status_code}: {r.text[:300]}")
