# -*- coding: utf-8 -*-
"""Envío de alertas por email vía SMTP (gratis, ej. con una cuenta de Gmail).

Credenciales SOLO por variable de entorno (nunca en el código):
  SMTP_HOST      -- ej. "smtp.gmail.com"
  SMTP_PORT      -- 587 por defecto (STARTTLS)
  SMTP_USUARIO   -- cuenta que envía
  SMTP_PASSWORD  -- con Gmail: una "contraseña de aplicación" (no la contraseña
                    normal de la cuenta; se genera en myaccount.google.com)
  SMTP_DESTINO   -- dirección que recibe las alertas
"""
import os
import smtplib
from email.mime.text import MIMEText

DEFAULT_PORT = 587


class CorreoError(Exception):
    pass


def configurado() -> bool:
    return bool(
        os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USUARIO")
        and os.environ.get("SMTP_PASSWORD") and os.environ.get("SMTP_DESTINO")
    )


def enviar(mensaje: str) -> None:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", str(DEFAULT_PORT)))
    usuario = os.environ.get("SMTP_USUARIO")
    password = os.environ.get("SMTP_PASSWORD")
    destino = os.environ.get("SMTP_DESTINO")
    if not (host and usuario and password and destino):
        raise CorreoError("Faltan SMTP_HOST / SMTP_USUARIO / SMTP_PASSWORD / SMTP_DESTINO (ver INSTALL.md).")
    msg = MIMEText(mensaje, "plain", "utf-8")
    msg["Subject"] = "LactIA — Alerta"
    msg["From"] = usuario
    msg["To"] = destino
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(usuario, password)
            s.sendmail(usuario, [destino], msg.as_string())
    except Exception as exc:  # noqa: BLE001
        raise CorreoError(str(exc)) from exc
