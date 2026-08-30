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
pedidos -- cargarlo de nuevo en cada pedido tardaría varios segundos.

`import vosk` va ADENTRO de _cargar_modelo, no arriba, a propósito: app.py
importa este módulo al arrancar, así que un vosk ausente, roto o bloqueado
por Windows Smart App Control en la PC de producción tiraría el ImportError
al importar app.py y dejaría a LactIA ENTERA sin arrancar (servidor.py
importa app) por una función opcional. Adentro de la función, ese mismo
error viaja como excepción de transcribir(), que el endpoint ya maneja bien
(contesta "No entendí, repetí" y no toca ningún relé)."""
import io
import json
import threading
import wave

# Modelo de reconocimiento. `lang="es"` deja que Vosk elija el modelo chico
# de español de su catálogo online (hoy vosk-model-small-es-0.42, ~38 MB) y
# lo BAJE la primera vez que se usa, DENTRO del hilo del pedido HTTP y sin
# timeout -- por eso en producción hay que precalentarlo desde una consola
# antes del primer comando de voz (ver CLAUDE.md e INSTALL.md). Queda como
# constante con nombre para que se vea que es una decisión y no un detalle:
# NO está pinneado a una versión, si Vosk publica otro modelo chico de
# español la próxima instalación limpia se baja ese. Para pinnearlo hay que
# pasar `model_name="vosk-model-small-es-0.42"` en vez de `lang`, y confirmar
# primero que ese nombre exista en el catálogo desde la máquina que lo baja.
MODELO_IDIOMA = "es"

_vosk = None
_modelo = None
_lock_modelo = threading.Lock()


def _cargar_modelo():
    global _modelo, _vosk
    if _modelo is None:
        with _lock_modelo:
            if _modelo is None:   # revalidar adentro del lock: otro hilo pudo haberlo cargado mientras esperábamos
                import vosk

                vosk.SetLogLevel(-1)   # sin el log de Kaldi por stderr en cada pedido
                modelo = vosk.Model(lang=MODELO_IDIOMA)
                # _vosk se publica ANTES que _modelo: el chequeo rápido de
                # arriba (fuera del lock) mira _modelo, así que si se
                # asignara primero, otro hilo podría verlo cargado con _vosk
                # todavía en None.
                _vosk = vosk
                _modelo = modelo
    return _modelo


def transcribir(audio_wav: bytes) -> str:
    """audio_wav: bytes de un archivo WAV (PCM 16 bit mono). Devuelve el
    texto reconocido en español ("" si no reconoció nada)."""
    modelo = _cargar_modelo()
    with wave.open(io.BytesIO(audio_wav)) as w:
        # La frecuencia sale del propio WAV: la pantalla graba a 16 kHz, pero
        # el audio sintetizado con el que se prueba esto viene a 22.05 kHz.
        reconocedor = _vosk.KaldiRecognizer(modelo, w.getframerate())
        reconocedor.SetWords(False)
        while True:
            datos = w.readframes(4000)
            if not datos:
                break
            reconocedor.AcceptWaveform(datos)
        return json.loads(reconocedor.FinalResult()).get("text", "").strip()
