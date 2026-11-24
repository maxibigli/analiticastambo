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
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import envio_red

DEFAULT_PORT = 587
TIMEOUT_S = 15                 # un mensaje de texto
TIMEOUT_ADJUNTO_S = 60         # con un archivo adjunto (un Excel de hasta ~1 MB) hace falta más tiempo
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_NEGRITA_RE = re.compile(r"\*([^*\n]+)\*")

_EMOJI_A_TEXTO = {
    "🟢": "[OK]",
    "🟠": "[ATENCION]",
    "🔴": "[MAL]",
    "⚪": "[SIN DATO]",
}


class CorreoError(Exception):
    """Un error de correo. `no_salio`: se SABE que el mensaje no salió; `reintentable`: además se puede volver a intentar sin riesgo de duplicarlo (ver `envio_red`)."""

    def __init__(self, mensaje="", no_salio=False, reintentable=False):
        super().__init__(mensaje)
        self.__dict__.update(envio_red.marcas(no_salio, reintentable))


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
    try:
        port = int(os.environ.get("SMTP_PORT") or DEFAULT_PORT)
    except ValueError:
        raise CorreoError("SMTP_PORT tiene que ser un número (ver INSTALL.md).", no_salio=True) from None
    usuario = os.environ.get("SMTP_USUARIO")
    password = os.environ.get("SMTP_PASSWORD")
    destino = os.environ.get("SMTP_DESTINO")
    if not (host and usuario and password and destino):
        raise CorreoError("Faltan SMTP_HOST / SMTP_USUARIO / SMTP_PASSWORD / SMTP_DESTINO (ver INSTALL.md).", no_salio=True)
    return host, port, usuario, password, destino


# Lo que el servidor contestó diciendo que NO: el mensaje no salió, y volver a probar da lo mismo.
_RECHAZOS = (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError,
             smtplib.SMTPHeloError, smtplib.SMTPNotSupportedError)


def _mandar(msg, host, port, usuario, password, destino, timeout=TIMEOUT_S):
    """Manda el mensaje. Un error ANTES de pedirle al servidor que lo reciba (conectar, STARTTLS, entrar) es seguro que no salió; uno DESPUÉS (la conexión que se
    corta mientras se manda) es incierto; y si el servidor ya aceptó el mensaje, un error al cerrar la conexión no lo desmanda."""
    antes, aceptado = True, False
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.starttls()
            s.login(usuario, password)
            antes = False
            s.sendmail(usuario, [destino], msg.as_string())
            aceptado = True
    except Exception as exc:  # noqa: BLE001
        if aceptado:
            return
        no_salio = antes or isinstance(exc, _RECHAZOS)
        raise CorreoError(str(exc), no_salio=no_salio, reintentable=antes and not isinstance(exc, smtplib.SMTPAuthenticationError)) from exc


def enviar(mensaje: str, asunto: str | None = None) -> None:
    """Un mensaje de texto. El asunto es el de una alerta salvo que se pase otro (el envío programado de un informe usa el del informe)."""
    host, port, usuario, password, destino = _credenciales()
    msg = MIMEText(_texto_plano(mensaje), "plain", "utf-8")
    msg["Subject"] = asunto or "LactIA — Alerta"
    msg["From"] = usuario
    msg["To"] = destino
    _mandar(msg, host, port, usuario, password, destino)


def enviar_con_adjunto(asunto: str, texto: str, nombre: str, datos: bytes, mime: str = MIME_XLSX) -> None:
    """Un correo con el texto y un archivo adjunto (el Excel de un informe programado). El texto pasa por `_texto_plano`, como los demás mensajes."""
    host, port, usuario, password, destino = _credenciales()
    msg = MIMEMultipart("mixed")
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = destino
    cuerpo = MIMEMultipart("alternative")
    cuerpo.attach(MIMEText(_texto_plano(texto), "plain", "utf-8"))
    msg.attach(cuerpo)
    tipo, _, subtipo = mime.partition("/")
    parte = MIMEBase(tipo, subtipo or "octet-stream")
    parte.set_payload(datos)
    encoders.encode_base64(parte)
    parte.add_header("Content-Disposition", "attachment", filename=nombre if nombre.isascii() else ("utf-8", "", nombre))
    msg.attach(parte)
    _mandar(msg, host, port, usuario, password, destino, timeout=TIMEOUT_ADJUNTO_S)


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
