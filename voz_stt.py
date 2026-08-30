# -*- coding: utf-8 -*-
"""Transcribe a texto en español el audio grabado por la pantalla ESP32
DESPUÉS de la wake word "Jarvis" (el matching contra el vocabulario cerrado
lo hace voz_comandos.interpretar). Corre 100% local en esta PC (Vosk, CPU)
-- no manda audio a ningún servicio externo.

Vosk y no Whisper a propósito: el binario nativo de PyAV, del que depende
faster-whisper, lo bloquea Windows Smart App Control en esta máquina (ver
el plan de implementación de esta feature). Vosk además es más liviano
(~38MB el modelo de español), lo que ayuda con la poca RAM de este entorno.

El modelo se descarga una sola vez la primera vez que se usa (necesita
internet esa vez) y después queda cacheado en disco, sin depender de red
nunca más. Se carga en memoria una sola vez (lazy) y se reusa entre
pedidos -- cargarlo de nuevo en cada pedido tardaría varios segundos."""
import io
import json
import threading
import wave

import vosk

vosk.SetLogLevel(-1)   # sin el log de Kaldi por stderr en cada pedido

_modelo = None
_lock_modelo = threading.Lock()


def _cargar_modelo() -> "vosk.Model":
    global _modelo
    if _modelo is None:
        with _lock_modelo:
            if _modelo is None:   # revalidar adentro del lock: otro hilo pudo haberlo cargado mientras esperábamos
                _modelo = vosk.Model(lang="es")
    return _modelo


def transcribir(audio_wav: bytes) -> str:
    """audio_wav: bytes de un archivo WAV (PCM 16 bit mono). Devuelve el
    texto reconocido en español ("" si no reconoció nada)."""
    modelo = _cargar_modelo()
    with wave.open(io.BytesIO(audio_wav)) as w:
        # La frecuencia sale del propio WAV: la pantalla graba a 16 kHz, pero
        # el audio sintetizado con el que se prueba esto viene a 22.05 kHz.
        reconocedor = vosk.KaldiRecognizer(modelo, w.getframerate())
        reconocedor.SetWords(False)
        while True:
            datos = w.readframes(4000)
            if not datos:
                break
            reconocedor.AcceptWaveform(datos)
        return json.loads(reconocedor.FinalResult()).get("text", "").strip()
