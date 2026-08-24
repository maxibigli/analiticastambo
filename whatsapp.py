# -*- coding: utf-8 -*-
"""Envío de alertas por WhatsApp vía Twilio (sandbox o número propio de WhatsApp).

Credenciales SOLO por variable de entorno (nunca en el código):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN  -- de la consola de Twilio
  TWILIO_WHATSAPP_FROM                   -- ej. "whatsapp:+14155238886" (sandbox)
  WHATSAPP_TELEFONO                      -- tu número destino, con código de país
                                             (ej. "+549341XXXXXXX"), el mismo que
                                             activó el sandbox por WhatsApp.
"""
import os
import time

import requests

TWILIO_MSG_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TWILIO_MSG_ESTADO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{msg_sid}.json"


class WhatsappError(Exception):
    pass


def configurado() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_WHATSAPP_FROM") and os.environ.get("WHATSAPP_TELEFONO")
    )


def enviar(mensaje: str) -> None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    origen = os.environ.get("TWILIO_WHATSAPP_FROM")
    destino = os.environ.get("WHATSAPP_TELEFONO")
    if not (sid and token and origen and destino):
        raise WhatsappError("Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM / "
                             "WHATSAPP_TELEFONO (ver INSTALL.md).")
    destino_wa = destino if destino.startswith("whatsapp:") else f"whatsapp:{destino}"
    r = requests.post(TWILIO_MSG_URL.format(sid=sid), auth=(sid, token),
                       data={"From": origen, "To": destino_wa, "Body": mensaje}, timeout=15)
    if r.status_code not in (200, 201):
        raise WhatsappError(f"Twilio respondió {r.status_code}: {r.text[:300]}")

    # Que Twilio haya aceptado el pedido (200/201) solo significa "encolado":
    # no confirma que WhatsApp lo haya entregado. Se consulta el estado real
    # un instante después para poder avisar el motivo si falló.
    msg_sid = r.json().get("sid")
    if not msg_sid:
        return
    time.sleep(2.5)
    r2 = requests.get(TWILIO_MSG_ESTADO_URL.format(sid=sid, msg_sid=msg_sid), auth=(sid, token), timeout=15)
    if r2.status_code != 200:
        return  # no se pudo confirmar el estado; no es razón para reportar error
    info = r2.json()
    estado = info.get("status")
    if estado in ("failed", "undelivered"):
        raise WhatsappError(
            f"Twilio encoló el mensaje pero no se entregó (estado: {estado}). "
            f"Código {info.get('error_code')}: {info.get('error_message')}"
        )
