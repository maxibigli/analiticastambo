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
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

DEFAULT_PORT = 587

_NEGRITA_RE = re.compile(r"\*([^*\n]+)\*")

_EMOJI_A_TEXTO = {
    "🟢": "[OK]",
    "🟠": "[ATENCION]",
    "🔴": "[MAL]",
    "⚪": "[SIN DATO]",
}


class CorreoError(Exception):
    pass


def _texto_plano(mensaje: str) -> str:
    """Las alertas se arman en un estilo pensado para WhatsApp/Telegram:
    *negrita* con asterisco (ahí se interpreta) y semáforo con emoji de
    color. En un mail de texto plano el asterisco queda literal, y los
    círculos de color (🟢🟠🔴), al ser un emoji más nuevo que uno como ✅,
    no los renderiza bien la fuente de todos los clientes de correo -- se
    cambian por texto plano para que se lea igual en cualquier lector."""
    texto = _NEGRITA_RE.sub(r"\1", mensaje)
    for emoji, reemplazo in _EMOJI_A_TEXTO.items():
        texto = texto.replace(emoji, reemplazo)
    return texto


def configurado() -> bool:
    return bool(
        os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USUARIO")
        and os.environ.get("SMTP_PASSWORD") and os.environ.get("SMTP_DESTINO")
    )


def _credenciales():
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", str(DEFAULT_PORT)))
    usuario = os.environ.get("SMTP_USUARIO")
    password = os.environ.get("SMTP_PASSWORD")
    destino = os.environ.get("SMTP_DESTINO")
    if not (host and usuario and password and destino):
        raise CorreoError("Faltan SMTP_HOST / SMTP_USUARIO / SMTP_PASSWORD / SMTP_DESTINO (ver INSTALL.md).")
    return host, port, usuario, password, destino


def _mandar(msg, host, port, usuario, password, destino):
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(usuario, password)
            s.sendmail(usuario, [destino], msg.as_string())
    except Exception as exc:  # noqa: BLE001
        raise CorreoError(str(exc)) from exc


def enviar(mensaje: str) -> None:
    host, port, usuario, password, destino = _credenciales()
    msg = MIMEText(_texto_plano(mensaje), "plain", "utf-8")
    msg["Subject"] = "LactIA — Alerta"
    msg["From"] = usuario
    msg["To"] = destino
    _mandar(msg, host, port, usuario, password, destino)


def enviar_html(texto_plano: str, html: str) -> None:
    """Como enviar(), pero manda un mail multipart/alternative: una versión
    HTML (con badges de color reales, usada hoy para el resumen del Tablero
    de Diagnóstico) y una de texto plano de respaldo, para el cliente que no
    pueda mostrar HTML."""
    host, port, usuario, password, destino = _credenciales()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "LactIA — Alerta"
    msg["From"] = usuario
    msg["To"] = destino
    msg.attach(MIMEText(_texto_plano(texto_plano), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    _mandar(msg, host, port, usuario, password, destino)
