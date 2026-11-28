# -*- coding: utf-8 -*-
"""Vigilat: lo que sale al mundo. Mandar un mensaje a una persona (WhatsApp, Telegram, correo), sonar la alarma (un
pulso en una salida del gateway) y decir el aviso por los parlantes de la sala.

* **Alarma.** El pulso lo ejecuta `iot_lavado.py`, único dueño de la conexión con el gateway. Si en `ESPERA_ALARMA_S` no
  lo tomó, se CANCELA: una sirena que suena horas después, cuando vuelve el gateway, es peor que ninguna. El largo del
  pulso es el que la salida tiene en ⚙ Configuración › Entradas/Salidas (hasta 10 s).
* **Audio en la sala.** Habla la PC que corre LactIA (la misma voz de Windows que anuncia el lavado): sus parlantes
  tienen que estar en la sala, y el programa tiene que correr con un usuario con la sesión abierta. El texto viaja en una
  variable de entorno y no pegado al comando: lleva nombres que salen de la base.
"""
import os
import subprocess
import time

import correo
import telegram_bot
import whatsapp

ESPERA_ALARMA_S = 15
TIEMPO_AUDIO_S = 90
MAX_AUDIO = 600                                   # letras que se dicen como máximo en una tanda
LARGO_MAXIMO = {"whatsapp": 1500, "telegram": 4000}      # el tope de cada canal, con margen
VOZ = "Microsoft Helena Desktop"                  # si no está instalada se usa la voz por defecto

_COMANDO_VOZ = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    f"try {{ $s.SelectVoice('{VOZ}') }} catch {{}}; "
    "try { [console]::beep(880,350); [console]::beep(660,350); [console]::beep(880,350) } catch {}; "
    "$s.Speak($env:LACTIA_TEXTO_VOZ)"
)


# --------------------------------------------------------------------------- mensajes a personas
def disponibles() -> dict:
    """Qué canales tienen cargadas las credenciales para escribirle a una persona (el destino lo pone Vigilat)."""
    e = os.environ.get
    return {
        "whatsapp": bool(e("TWILIO_ACCOUNT_SID") and e("TWILIO_AUTH_TOKEN") and e("TWILIO_WHATSAPP_FROM")),
        "telegram": bool(e("TELEGRAM_BOT_TOKEN")),
        "correo": bool(e("SMTP_HOST") and e("SMTP_USUARIO") and e("SMTP_PASSWORD")),
    }


def partir(texto: str, largo: int) -> list:
    """El texto en pedazos de hasta `largo`, cortando por renglones (un renglón más largo que el tope se corta)."""
    partes, actual = [], ""
    for renglon in texto.split("\n"):
        while len(renglon) > largo:
            if actual:
                partes.append(actual)
                actual = ""
            partes.append(renglon[:largo])
            renglon = renglon[largo:]
        candidato = f"{actual}\n{renglon}" if actual else renglon
        if len(candidato) > largo:
            partes.append(actual)
            actual = renglon
        else:
            actual = candidato
    if actual.strip():
        partes.append(actual)
    return partes or [texto]


def enviar(canal: str, destino: str, mensaje: str) -> None:
    """Manda `mensaje` a una persona. Levanta el error del canal (con sus marcas `no_salio` / `reintentable`). Si el
    mensaje va en varias partes y falla una que no es la primera, algo ya salió: el error deja de ser «no salió», para
    no repetir lo que la persona ya recibió."""
    modulo = {"whatsapp": whatsapp, "telegram": telegram_bot, "correo": correo}[canal]
    partes = partir(mensaje, LARGO_MAXIMO[canal]) if canal in LARGO_MAXIMO else [mensaje]
    for i, parte in enumerate(partes):
        try:
            if canal == "correo":
                modulo.enviar(parte, asunto="Vigilat — aviso del tambo", destino=destino)
            else:
                modulo.enviar(parte, destino=destino)
        except Exception as exc:
            if i:
                exc.no_salio, exc.reintentable = False, False
            raise


# --------------------------------------------------------------------------- en el tambo
def sonar_alarma(salida: str, espera_s: float = ESPERA_ALARMA_S, dormir=time.sleep, gateway=None) -> tuple:
    """`(salió bien, qué pasó)`. `gateway` es `iot_monitoreo` (en las pruebas, uno de mentira)."""
    if not salida:
        return False, "no hay ninguna salida elegida para la alarma"
    if gateway is None:
        import iot_monitoreo as gateway
    try:
        pedido = gateway.probar_salida(salida)
    except gateway.SalidaOcupada as exc:
        return False, f"la salida está ocupada: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudo pedir el pulso ({type(exc).__name__}: {exc})"
    esperado = 0.0
    while esperado < espera_s:
        dormir(1)
        esperado += 1
        r = gateway.resultado_pulso(pedido)
        if r and r["ejecutado"]:
            return (True, "sonó") if r["resultado"] == "ok" else (False, f"el gateway contestó: {r['resultado']}")
    if gateway.cancelar_pulso(pedido):
        return False, "el programa del gateway (iot_lavado.py) no lo tomó a tiempo: se canceló"
    r = gateway.resultado_pulso(pedido)
    if r and r["resultado"] == "ok":
        return True, "sonó"
    return False, "el gateway tomó el pedido pero todavía no confirmó que sonara"


def hablar(texto: str, correr=subprocess.run) -> tuple:
    """`(salió bien, qué pasó)`. Que salga bien quiere decir que Windows terminó de hablar sin error, no que se haya
    escuchado en la sala: eso depende de los parlantes."""
    texto = " ".join(str(texto).split())[:MAX_AUDIO]
    if not texto:
        return False, "no había nada que decir"
    try:
        r = correr(["powershell", "-NoProfile", "-NonInteractive", "-Command", _COMANDO_VOZ],
                   env={**os.environ, "LACTIA_TEXTO_VOZ": texto}, capture_output=True, timeout=TIEMPO_AUDIO_S,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudo reproducir ({type(exc).__name__})"
    if r.returncode != 0:
        return False, "Windows no pudo reproducir el audio (revisá la salida de sonido de la PC)"
    return True, "se reprodujo en la PC de LactIA"
