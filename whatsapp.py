# -*- coding: utf-8 -*-
"""Envío de alertas por WhatsApp vía la API oficial de Meta (WhatsApp Cloud
API, nivel gratuito) -- reemplaza la integración anterior con Twilio.

Credenciales SOLO por variable de entorno (nunca en el código):
  WHATSAPP_CLOUD_TOKEN       -- token de acceso (permanente, de un Usuario
                                 del sistema de Meta Business -- ver INSTALL.md)
  WHATSAPP_PHONE_NUMBER_ID   -- ID del número de WhatsApp Business (NO es el
                                 número de teléfono, es un ID interno de Meta)
  WHATSAPP_TELEFONO          -- número destino, con código de país, sin "+"
                                 ni espacios (ej. "5493411234567")
  WHATSAPP_TEMPLATE_NOMBRE   -- nombre de la plantilla aprobada por Meta
                                 (default: "alerta_lactia")
  WHATSAPP_TEMPLATE_IDIOMA   -- código de idioma de la plantilla
                                 (default: "es")

POR QUÉ PLANTILLA Y NO TEXTO LIBRE: WhatsApp Cloud API solo deja mandar
texto libre dentro de las 24hs de que el destinatario le escribió primero al
número de WhatsApp Business. Estas alertas se disparan solas (a las 8:00/
20:00, sin que nadie escriba antes), así que necesitan una PLANTILLA
aprobada por Meta -- esa sí se puede mandar en cualquier momento, sin
depender de una conversación abierta. Ver INSTALL.md para cómo crearla.
"""
import os
import re

import requests

API_URL = "https://graph.facebook.com/v21.0/{phone_id}/messages"

_NEGRITA_RE = re.compile(r"\*([^*\n]+)\*")


class WhatsappError(Exception):
    pass


def _texto_para_plantilla(mensaje: str) -> str:
    """El valor de una variable de plantilla de Meta no admite saltos de
    línea (ni el *negrita* pensado para un mensaje de texto libre) -- se
    aplana a una sola línea legible, separando lo que era cada renglón con
    "·"."""
    texto = _NEGRITA_RE.sub(r"\1", mensaje)
    return " · ".join(linea.strip() for linea in texto.splitlines() if linea.strip())


def configurado() -> bool:
    return bool(
        os.environ.get("WHATSAPP_CLOUD_TOKEN") and os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
        and os.environ.get("WHATSAPP_TELEFONO")
    )


def enviar(mensaje: str) -> None:
    token = os.environ.get("WHATSAPP_CLOUD_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    destino = os.environ.get("WHATSAPP_TELEFONO")
    plantilla = os.environ.get("WHATSAPP_TEMPLATE_NOMBRE", "alerta_lactia")
    idioma = os.environ.get("WHATSAPP_TEMPLATE_IDIOMA", "es")
    if not (token and phone_id and destino):
        raise WhatsappError("Faltan WHATSAPP_CLOUD_TOKEN / WHATSAPP_PHONE_NUMBER_ID / "
                             "WHATSAPP_TELEFONO (ver INSTALL.md).")
    body = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": idioma},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": _texto_para_plantilla(mensaje)}],
            }],
        },
    }
    r = requests.post(
        API_URL.format(phone_id=phone_id),
        headers={"Authorization": f"Bearer {token}"},
        json=body, timeout=15,
    )
    if r.status_code not in (200, 201):
        raise WhatsappError(f"WhatsApp Cloud API respondió {r.status_code}: {r.text[:300]}")
