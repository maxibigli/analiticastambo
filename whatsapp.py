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

import envio_red

TWILIO_MSG_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TWILIO_MSG_ESTADO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages/{msg_sid}.json"


# Los códigos de Twilio que se conocen, y el motivo (de una lista CERRADA, nunca el texto de Twilio) con que se los cuenta. Cualquier otro código es «no_entregado».
_MOTIVO_DE_CODIGO = {"63016": "ventana_24h",           # texto libre fuera de la ventana de 24 h desde que el destinatario escribió
                     "63015": "sandbox", "63007": "sandbox"}      # el número de destino no está unido al sandbox / el remitente no existe en esa cuenta


class WhatsappError(Exception):
    """Un error de WhatsApp (Twilio). `no_salio`: se SABE que el mensaje no salió; `reintentable`: además se puede volver a intentar sin riesgo de duplicarlo (ver `envio_red`).
    `motivo`: por qué no se entregó un mensaje que Twilio aceptó (`ventana_24h`, `sandbox`, `no_entregado`), o None."""

    def __init__(self, mensaje="", no_salio=False, reintentable=False, motivo=None):
        super().__init__(mensaje)
        self.__dict__.update(envio_red.marcas(no_salio, reintentable))
        self.motivo = motivo


def configurado() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_WHATSAPP_FROM") and os.environ.get("WHATSAPP_TELEFONO")
    )


def enviar(mensaje: str, destino: str | None = None) -> None:
    """`destino` opcional: si no se pasa, usa WHATSAPP_TELEFONO (el número fijo
    de las alertas). Se puede pasar otro para responderle a quien escribió
    -- ver app.py::webhook_whatsapp, que le contesta a quien preguntó, no
    siempre al número fijo de alertas."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    origen = os.environ.get("TWILIO_WHATSAPP_FROM")
    destino = destino or os.environ.get("WHATSAPP_TELEFONO")
    if not (sid and token and origen and destino):
        raise WhatsappError("Faltan TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM / "
                             "WHATSAPP_TELEFONO (ver INSTALL.md).", no_salio=True)
    destino_wa = destino if destino.startswith("whatsapp:") else f"whatsapp:{destino}"
    try:
        r = requests.post(TWILIO_MSG_URL.format(sid=sid), auth=(sid, token),
                           data={"From": origen, "To": destino_wa, "Body": mensaje}, timeout=15)
    except Exception as exc:  # noqa: BLE001 -- el texto de una excepción de `requests` lleva la URL (con el SID de la cuenta)
        sin_llegar = envio_red.no_llego(exc)
        raise WhatsappError(f"No se pudo conectar con Twilio ({type(exc).__name__}).", no_salio=sin_llegar, reintentable=sin_llegar) from None
    if r.status_code not in (200, 201):
        raise WhatsappError(f"Twilio respondió {r.status_code}: {r.text[:300]}", no_salio=400 <= r.status_code < 500, reintentable=r.status_code == 429)

    # Que Twilio haya aceptado el pedido (200/201) solo significa "encolado":
    # no confirma que WhatsApp lo haya entregado. Se consulta el estado real
    # un instante después para poder avisar el motivo si falló. Desde que Twilio
    # aceptó el mensaje, un fallo de ESTA consulta no es un fallo del envío: el
    # mensaje ya está en camino, y avisar un error haría que quien llama lo reintente (y se duplique).
    try:
        msg_sid = r.json().get("sid")
    except Exception:  # noqa: BLE001
        return
    if not msg_sid:
        return
    time.sleep(2.5)
    try:
        r2 = requests.get(TWILIO_MSG_ESTADO_URL.format(sid=sid, msg_sid=msg_sid), auth=(sid, token), timeout=15)
        if r2.status_code != 200:
            return  # no se pudo confirmar el estado; no es razón para reportar error
        info = r2.json()
    except Exception:  # noqa: BLE001
        return
    estado = info.get("status") if isinstance(info, dict) else None
    if estado in ("failed", "undelivered"):
        raise WhatsappError(
            f"Twilio encoló el mensaje pero no se entregó (estado: {estado}). "
            f"Código {info.get('error_code')}: {info.get('error_message')}", no_salio=True,
            motivo=_MOTIVO_DE_CODIGO.get(str(info.get("error_code")), "no_entregado")
        )
