# -*- coding: utf-8 -*-
"""Sintetiza texto a un WAV en memoria, para la confirmación hablada de un
comando de voz (ver voz_comandos.py). Reusa la misma voz de Windows
(System.Speech, vía PowerShell) que ya usa iot_lavado._anunciar_voz, pero
grabando a un archivo en vez de reproducir por los parlantes de la PC --
esto corre DENTRO de un pedido HTTP y necesita el audio listo antes de
poder responder, así que es bloqueante (a diferencia de _anunciar_voz)."""
import os
import subprocess
import tempfile

VOZ_PREFERIDA = "Microsoft Helena Desktop"  # si no está instalada, usa la voz por defecto


def sintetizar_wav(texto: str) -> bytes:
    texto_ps = texto.replace("'", "''")  # escapar comillas simples para PowerShell
    fd, ruta = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        comando = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"try {{ $s.SelectVoice('{VOZ_PREFERIDA}') }} catch {{}}; "
            f"$s.SetOutputToWaveFile('{ruta}'); "
            f"$s.Speak('{texto_ps}'); "
            "$s.SetOutputToDefaultAudioDevice()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", comando],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        check=True, timeout=15)
        with open(ruta, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass
