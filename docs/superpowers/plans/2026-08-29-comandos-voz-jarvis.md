# Comandos de voz "Jarvis" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** La pantalla ESP32 de LactIA escucha "Jarvis" y ejecuta por voz lo
que hoy solo se puede tocar: iniciar/cancelar el Lavado Automático y
prender/apagar actuadores individuales por su nombre configurado.

**Architecture:** El ESP32 detecta la wake word "Jarvis" localmente y sin
red (WakeNet9, gratis, incluido en ESP-SR). Al detectarla, graba unos
segundos y se los manda una vez a un endpoint nuevo de Flask, que transcribe
el audio en español con un modelo local (Vosk, sin nube),
interpreta el texto contra un vocabulario cerrado y chico, dispara la
acción reusando el motor de comandos que ya existe (`comandos_actuador`,
`ciclo_lavado_estado`), y devuelve un WAV de confirmación que la pantalla
reproduce por su parlante.

**Tech Stack:** Python/Flask/SQLite (ya en uso), `vosk` (nuevo),
System.Speech de Windows vía PowerShell (ya en uso, para TTS), ESP-IDF 5.5.5
+ LVGL 9.5 (ya en uso), `espressif/esp-sr` (nuevo), `esp_codec_dev` (ya
vendorizado por el BSP de Waveshare, sin usar hasta ahora).

**Spec:** [docs/superpowers/specs/2026-08-29-comandos-voz-jarvis-design.md](../specs/2026-08-29-comandos-voz-jarvis-design.md)

## Global Constraints

- Nunca escribir Modbus fuera de `iot_lavado.py` (único dueño de la
  conexión al M300) — Flask y los módulos nuevos solo encolan pedidos en
  SQLite, igual que `lavado_programa.py`/`comandos_actuador` hoy.
- Módulos de estado/config nuevos (`voz_comandos.py`) son **hoja**: no
  importan `iot_lavado` (para no armar un ciclo de imports), mismo criterio
  que `lavado_programa.py`/`iot_conexion.py`/`iot_canales.py`.
- `POST /api/iot/pantalla/*` que activan algo real se bloquean si el pedido
  llegó por el túnel de Cloudflare (`_pedido_via_tunel()`) — mismo criterio
  que `/actuador` y `/lavado/iniciar` ya usan.
- Si un actuador está en uso por la etapa ACTUAL de un Lavado Automático
  activo, un pedido de voz sobre ese mismo actuador se ignora por completo
  (no se encola para "después") — confirmado con el tambo en el spec.
- Al arrancar `iot_lavado.py`, los actuadores controlables por voz quedan
  forzados a apagado por Modbus, sin asumir que siguen como antes del
  reinicio (ver spec, sección "Arranque y reinicio").
- Este proyecto no tiene una carpeta `tests/` con pytest — la convención
  real (ver `lavado_programa.py`/`iot_conexion.py` en `CLAUDE.md`) es
  scripts de verificación con `assert`, ejecutados una vez a mano desde el
  scratchpad, no committeados. Los pasos de test de este plan siguen ese
  mismo criterio.

---

## Task 1: `voz_comandos.py` — interpretar el texto + estado de actuadores sostenidos

**Files:**
- Create: `delpro-analitica/voz_comandos.py`
- Test: `<scratchpad>/test_voz_comandos.py`

**Interfaces:**
- Produces: `ACTUADORES_VALIDOS: set[str]`, `interpretar(texto: str) -> dict`,
  `estado() -> dict`, `solicitar_encendido(clave: str) -> bool`,
  `solicitar_apagado(clave: str) -> bool`, `limpiar_estado(clave: str) -> None`
- Consumes: `iot_canales.nombres() -> dict` (ya existe),
  `lavado_programa.etapas() -> list` y `lavado_programa.estado() -> dict`
  (ya existen)

- [ ] **Step 1: Escribir el script de verificación (va a fallar: el módulo no existe todavía)**

Crear `<scratchpad>/test_voz_comandos.py`:

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile, json
sys.path.insert(0, r"C:\Users\MAXI\CLAUDE\delpro-analitica")

tmp_dir = tempfile.mkdtemp()

import iot_canales
iot_canales._RUTA = os.path.join(tmp_dir, "iot_canales_nombres_test.json")
iot_canales.guardar({"do_1": "Bomba de Agua", "do_2": "Bomba de Espuma"})

import lavado_programa
lavado_programa._RUTA_CONFIG = os.path.join(tmp_dir, "lavado_programa_test.json")
lavado_programa.RUTA_DB = os.path.join(tmp_dir, "test.db")

import voz_comandos
voz_comandos.RUTA_DB = os.path.join(tmp_dir, "test.db")

print("--- frases fijas ---")
assert voz_comandos.interpretar("iniciar lavado") == {"tipo": "lavado_iniciar"}
assert voz_comandos.interpretar("arrancar lavado") == {"tipo": "lavado_iniciar"}
assert voz_comandos.interpretar("cancelar") == {"tipo": "lavado_cancelar"}
assert voz_comandos.interpretar("parar") == {"tipo": "lavado_cancelar"}

print("--- actuador por nombre configurado ---")
assert voz_comandos.interpretar("prender bomba de agua") == {"tipo": "actuador", "clave": "do_1", "prender": True}
assert voz_comandos.interpretar("encender bomba de agua") == {"tipo": "actuador", "clave": "do_1", "prender": True}
assert voz_comandos.interpretar("apagar bomba de espuma") == {"tipo": "actuador", "clave": "do_2", "prender": False}

print("--- tolera error chico de transcripcion ---")
assert voz_comandos.interpretar("prender bomba de agu") == {"tipo": "actuador", "clave": "do_1", "prender": True}

print("--- actuador SIN nombre configurado (do_3) no se reconoce ---")
assert voz_comandos.interpretar("prender salida 3") == {"tipo": "desconocido"}

print("--- texto random no matchea nada ---")
assert voz_comandos.interpretar("che como andas") == {"tipo": "desconocido"}
assert voz_comandos.interpretar("") == {"tipo": "desconocido"}

print("--- encendido sostenido: estado ---")
assert voz_comandos.estado() == {}
assert voz_comandos.solicitar_encendido("do_1") is True
assert "do_1" in voz_comandos.estado()
assert voz_comandos.solicitar_apagado("do_1") is True
assert voz_comandos.estado() == {}

print("--- clave invalida ---")
try:
    voz_comandos.solicitar_encendido("do_99")
    raise SystemExit("no debio aceptar")
except ValueError:
    print("OK, rechazado")

print("--- bloqueado si el lavado activo usa ese rele ---")
lavado_programa.guardar_etapas([{"reles": ["do_1"], "duracion_s": 60}])
con = lavado_programa._conectar_db()
con.execute("INSERT INTO ciclo_lavado_estado (id, comando, activo, etapa_actual, etapa_inicio) "
            "VALUES (1, NULL, 1, 0, '2026-08-29T10:00:00') "
            "ON CONFLICT(id) DO UPDATE SET activo=1, etapa_actual=0, etapa_inicio='2026-08-29T10:00:00'")
con.commit()
con.close()
assert voz_comandos.solicitar_encendido("do_1") is False
assert voz_comandos.estado() == {}
# do_2 no esta en uso por el lavado (solo usa do_1) -> si se puede
assert voz_comandos.solicitar_encendido("do_2") is True

print("--- limpiar_estado no chequea el lavado (lo usa el propio motor de lavado) ---")
voz_comandos.limpiar_estado("do_2")
assert voz_comandos.estado() == {}

print("\nTODO OK")
```

- [ ] **Step 2: Correr el script y confirmar que falla (ModuleNotFoundError: voz_comandos)**

Run: `python <scratchpad>/test_voz_comandos.py`
Expected: `ModuleNotFoundError: No module named 'voz_comandos'`

- [ ] **Step 3: Implementar `voz_comandos.py`**

```python
# -*- coding: utf-8 -*-
"""Interpreta el texto transcripto de un comando de voz (ver
delpro-analitica/docs/superpowers/specs/2026-08-29-comandos-voz-jarvis-design.md)
contra un vocabulario CERRADO y CHICO: frases fijas de Lavado Automático +
"prender/encender/apagar <nombre de actuador>", usando los nombres que el
tambo configuró en iot_canales (si le cambia el nombre a una salida, esto
se adapta solo, sin tocar código).

También guarda el estado de los actuadores SOSTENIDOS por voz (distinto
del pulso de 0,5s que ya usa el panel de Actuadores, que sigue igual) --
la ejecución real de Modbus la hace iot_lavado.procesar_comandos_voz.

Deliberadamente SIN import de iot_lavado (que sí importa este módulo),
mismo criterio que lavado_programa.py/iot_conexion.py. SÍ importa
lavado_programa (que a su vez tampoco importa iot_lavado) para saber si un
actuador está en uso por la etapa activa del ciclo automático."""
import datetime
import difflib
import sqlite3
import threading

import iot_canales
import lavado_programa

RUTA_DB = "iot_sensores.db"
UMBRAL_CONFIANZA = 0.72
ACTUADORES_VALIDOS = {"do_1", "do_2", "do_3", "do_4", "do_5", "do_6", "do_7", "do_8"}

FRASES_INICIAR = ["iniciar lavado", "arrancar lavado", "empezar lavado", "iniciar el lavado"]
FRASES_CANCELAR = ["cancelar", "cancelar lavado", "parar", "detener", "detener lavado"]

_lock = threading.Lock()


def _conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS voz_actuadores_estado (
            clave TEXT PRIMARY KEY,
            encendido_desde TEXT NOT NULL
        )
    """)
    con.commit()
    return con


def _normalizar(texto: str) -> str:
    return " ".join((texto or "").strip().lower().split())


def _candidatos():
    """[(frase_normalizada, tipo, datos), ...] -- datos es (clave, prender)
    para tipo == "actuador", None para el resto."""
    candidatos = [(f, "lavado_iniciar", None) for f in FRASES_INICIAR]
    candidatos += [(f, "lavado_cancelar", None) for f in FRASES_CANCELAR]
    nombres = iot_canales.nombres()
    for clave in sorted(ACTUADORES_VALIDOS):
        nombre = nombres.get(clave)
        if not nombre:
            continue   # sin nombre propio, no es natural decirlo en voz alta
        nombre_norm = _normalizar(nombre)
        candidatos.append((f"prender {nombre_norm}", "actuador", (clave, True)))
        candidatos.append((f"encender {nombre_norm}", "actuador", (clave, True)))
        candidatos.append((f"apagar {nombre_norm}", "actuador", (clave, False)))
    return candidatos


def interpretar(texto: str) -> dict:
    """{"tipo": "lavado_iniciar"|"lavado_cancelar"|"actuador"|"desconocido",
    "clave": ..., "prender": ...} -- las dos últimas solo si tipo == "actuador"."""
    texto_norm = _normalizar(texto)
    if not texto_norm:
        return {"tipo": "desconocido"}
    candidatos = _candidatos()
    frases = [c[0] for c in candidatos]
    mejor = difflib.get_close_matches(texto_norm, frases, n=1, cutoff=UMBRAL_CONFIANZA)
    if not mejor:
        return {"tipo": "desconocido"}
    _, tipo, datos = next(c for c in candidatos if c[0] == mejor[0])
    if tipo == "actuador":
        clave, prender = datos
        return {"tipo": "actuador", "clave": clave, "prender": prender}
    return {"tipo": tipo}


def _en_uso_por_lavado(clave: str) -> bool:
    estado_lavado = lavado_programa.estado()
    if not estado_lavado.get("activo"):
        return False
    programa = lavado_programa.etapas()
    etapa_actual = estado_lavado["etapa_actual"]
    if etapa_actual >= len(programa):
        return False
    return clave in programa[etapa_actual]["reles"]


def estado() -> dict:
    """clave -> encendido_desde (ISO) para lo sostenido por voz ahora mismo."""
    con = _conectar_db()
    try:
        filas = con.execute("SELECT clave, encendido_desde FROM voz_actuadores_estado").fetchall()
    finally:
        con.close()
    return dict(filas)


def solicitar_encendido(clave: str) -> bool:
    """True si quedó registrado. False si ese actuador está en uso por una
    etapa activa de Lavado Automático (se ignora, no se toca nada)."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        con = _conectar_db()
        try:
            con.execute(
                "INSERT INTO voz_actuadores_estado (clave, encendido_desde) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET encendido_desde = excluded.encendido_desde",
                (clave, ahora),
            )
            con.commit()
        finally:
            con.close()
    return True


def solicitar_apagado(clave: str) -> bool:
    """Mismo criterio que solicitar_encendido: False (ignorado) si está en
    uso por el lavado automático."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    limpiar_estado(clave)
    return True


def limpiar_estado(clave: str) -> None:
    """Borra el estado sostenido de `clave` SIN chequear si está en uso por
    el lavado -- lo llama el propio motor de Lavado Automático
    (iot_lavado.procesar_ciclo_lavado) cuando apaga un relé como parte de
    su propia secuencia, para que la próxima vuelta de
    procesar_comandos_voz no intente prenderlo de nuevo."""
    with _lock:
        con = _conectar_db()
        try:
            con.execute("DELETE FROM voz_actuadores_estado WHERE clave = ?", (clave,))
            con.commit()
        finally:
            con.close()
```

- [ ] **Step 4: Correr el script de nuevo y confirmar que pasa todo**

Run: `python <scratchpad>/test_voz_comandos.py`
Expected: termina imprimiendo `TODO OK`, sin ningún `AssertionError`.

- [ ] **Step 5: Commit**

```bash
git add voz_comandos.py
git commit -m "Agrega voz_comandos.py: interpretación de comandos de voz y actuadores sostenidos"
```

---

## Task 2: `voz_sintesis.py` — texto a WAV (confirmación hablada)

**Files:**
- Create: `delpro-analitica/voz_sintesis.py`
- Test: `<scratchpad>/test_voz_sintesis.py`

**Interfaces:**
- Produces: `sintetizar_wav(texto: str) -> bytes`

- [ ] **Step 1: Escribir el script de verificación**

```python
# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"C:\Users\MAXI\CLAUDE\delpro-analitica")

import voz_sintesis

audio = voz_sintesis.sintetizar_wav("Bomba de agua encendida")
assert isinstance(audio, bytes)
assert len(audio) > 1000          # un WAV real, no un archivo vacío
assert audio[:4] == b"RIFF"       # cabecera WAV
assert audio[8:12] == b"WAVE"
print(f"OK, {len(audio)} bytes generados")
```

- [ ] **Step 2: Correr y confirmar que falla (módulo no existe)**

Run: `python <scratchpad>/test_voz_sintesis.py`
Expected: `ModuleNotFoundError: No module named 'voz_sintesis'`

- [ ] **Step 3: Implementar `voz_sintesis.py`**

```python
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
```

- [ ] **Step 4: Correr de nuevo y confirmar que pasa**

Run: `python <scratchpad>/test_voz_sintesis.py`
Expected: imprime `OK, N bytes generados` (N > 1000).

- [ ] **Step 5: Commit**

```bash
git add voz_sintesis.py
git commit -m "Agrega voz_sintesis.py: texto a WAV para la confirmación hablada"
```

---

## Task 3: `voz_stt.py` — WAV a texto en español (local, sin nube)

**Files:**
- Modify: `delpro-analitica/requirements.txt`
- Create: `delpro-analitica/voz_stt.py`
- Test: `<scratchpad>/test_voz_stt.py`

**Interfaces:**
- Produces: `transcribir(audio_wav: bytes) -> str`

**POR QUÉ VOSK Y NO WHISPER (decisión ya tomada y verificada, no reabrir):**
el primer intento de este task usó `faster-whisper` y quedó BLOQUEADO en
esta PC: `faster_whisper` importa incondicionalmente PyAV (`av`), cuyo
binario nativo `av\container\core.pyd` es bloqueado por **Windows Smart App
Control** (confirmado con eventos de Code Integrity 3077/3118, reproducido
en las dos instalaciones de Python de esta máquina). SAC no tiene excepción
por archivo: la única forma de permitirlo sería desactivarlo por completo,
algo **irreversible sin reinstalar Windows** -- y esta PC no es la de
producción, así que pagar ese precio no se justificaba.

**Vosk ya fue verificado funcionando en esta PC**, con el mismo WAV
sintetizado que usa el test de abajo: transcribió exactamente
`'iniciar lavado'`. Además es más liviano que Whisper (modelo de español
~38MB contra ~150MB), lo que ayuda con la poca RAM de este entorno (ver
`CLAUDE.md`, "Entorno de desarrollo"). La interfaz del módulo
(`transcribir(bytes) -> str`) es la misma que iba a tener con Whisper, así
que si en producción se prefiere otro motor, se cambia solo este archivo.

- [ ] **Step 1: Instalar la dependencia nueva**

```bash
pip install vosk
```

Agregar a `requirements.txt`:
```
vosk>=0.3.45
```

- [ ] **Step 2: Generar el WAV de prueba con `voz_sintesis` (Task 2)**

NO grabar nada a mano ni pedirle al usuario que grabe: el audio de prueba
se genera con el módulo del task anterior, ya mergeado en esta rama. Correr
una vez, desde la raíz del worktree:

```python
import voz_sintesis
with open(r"<scratchpad>\prueba_iniciar_lavado.wav", "wb") as f:
    f.write(voz_sintesis.sintetizar_wav("iniciar lavado"))
```

Ese archivo lo usa el test de este task y también el Task 5 (endpoint).

- [ ] **Step 3: Escribir el script de verificación**

```python
# -*- coding: utf-8 -*-
import voz_stt

with open(r"<scratchpad>\prueba_iniciar_lavado.wav", "rb") as f:
    audio = f.read()

texto = voz_stt.transcribir(audio)
print(f"Transcripto: {texto!r}")
assert isinstance(texto, str)
assert "lavado" in texto.lower()   # no exige match exacto, solo que reconoció la palabra clave del dominio
print("OK")
```

(sin `sys.path.insert`: se corre con el working directory en la raíz del
worktree, que es donde vive `voz_stt.py`)

- [ ] **Step 4: Correr y confirmar que falla (módulo no existe)**

Run: `python <scratchpad>/test_voz_stt.py`
Expected: `ModuleNotFoundError: No module named 'voz_stt'`

- [ ] **Step 5: Implementar `voz_stt.py`**

```python
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
import wave

import vosk

vosk.SetLogLevel(-1)   # sin el log de Kaldi por stderr en cada pedido

_modelo = None


def _cargar_modelo() -> "vosk.Model":
    global _modelo
    if _modelo is None:
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
```

- [ ] **Step 6: Correr de nuevo y confirmar que pasa**

Run: `python <scratchpad>/test_voz_stt.py`
Expected: imprime `Transcripto: 'iniciar lavado'` y termina en `OK`. La
primera corrida tarda más porque descarga el modelo (~38MB).

- [ ] **Step 7: Commit**

```bash
git add voz_stt.py requirements.txt
git commit -m "Agrega voz_stt.py: transcripción de voz a texto en español, local con Vosk"
```

---

## Task 4: `iot_lavado.py` — ejecutar los comandos de voz sobre el M300

**Files:**
- Modify: `delpro-analitica/iot_lavado.py`
- Test: `<scratchpad>/test_iot_lavado_voz.py`

**Interfaces:**
- Consumes: `voz_comandos.ACTUADORES_VALIDOS`, `voz_comandos.estado()`,
  `voz_comandos.limpiar_estado(clave)`, `voz_comandos.solicitar_encendido()`
  (Task 1)
- Produces: `procesar_comandos_voz(con, client, anteriores_voz: dict) -> dict`,
  `apagar_actuadores_voz_al_arrancar(client)`

- [ ] **Step 1: Escribir el script de verificación (con un ModbusTcpClient falso, mismo criterio que ya se usó para probar `procesar_ciclo_lavado` sin hardware real)**

```python
# -*- coding: utf-8 -*-
import sys, os, tempfile
sys.path.insert(0, r"C:\Users\MAXI\CLAUDE\delpro-analitica")

tmp_dir = tempfile.mkdtemp()
tmp_db = os.path.join(tmp_dir, "test.db")

import lavado_programa
lavado_programa.RUTA_DB = tmp_db
lavado_programa._RUTA_CONFIG = os.path.join(tmp_dir, "lavado_programa_test.json")

import voz_comandos
voz_comandos.RUTA_DB = tmp_db

import iot_lavado
iot_lavado.RUTA_DB = tmp_db


class ClientFalso:
    """Reemplaza ModbusTcpClient: solo anota qué se escribió, no habla con
    ningún gateway real."""
    def __init__(self):
        self.connected = True
        self.escrituras = []  # [(direccion, valor), ...]

    def connect(self):
        self.connected = True
        return True

    def close(self):
        pass

    def write_coil(self, address, value, device_id=1):
        self.escrituras.append((address, value))


con = iot_lavado._conectar_db(tmp_db)
client = ClientFalso()

print("--- prender do_1 (direccion 0) ---")
voz_comandos.solicitar_encendido("do_1")
anteriores = {clave: False for clave in voz_comandos.ACTUADORES_VALIDOS}
anteriores = iot_lavado.procesar_comandos_voz(con, client, anteriores)
assert client.escrituras == [(0, True)], client.escrituras
assert anteriores["do_1"] is True

print("--- no reescribe si no cambio nada ---")
client.escrituras.clear()
anteriores = iot_lavado.procesar_comandos_voz(con, client, anteriores)
assert client.escrituras == [], client.escrituras

print("--- apagar do_1 ---")
voz_comandos.solicitar_apagado("do_1")
anteriores = iot_lavado.procesar_comandos_voz(con, client, anteriores)
assert client.escrituras == [(0, False)], client.escrituras
assert anteriores["do_1"] is False

print("--- al arrancar, fuerza apagado de todos los actuadores controlables por voz ---")
voz_comandos.solicitar_encendido("do_3")   # simula que algo habia quedado prendido antes
client.escrituras.clear()
iot_lavado.apagar_actuadores_voz_al_arrancar(client)
direcciones_apagadas = {d for d, v in client.escrituras if v is False}
assert direcciones_apagadas == set(iot_lavado.ACTUADORES.values()), direcciones_apagadas

con.close()
print("\nTODO OK")
```

- [ ] **Step 2: Correr y confirmar que falla (las funciones nuevas no existen)**

Run: `python <scratchpad>/test_iot_lavado_voz.py`
Expected: `AttributeError: module 'iot_lavado' has no attribute 'procesar_comandos_voz'`

- [ ] **Step 3: Agregar el import de `voz_comandos` en `iot_lavado.py`**

En `iot_lavado.py`, junto a los imports existentes de `iot_conexion` y
`lavado_programa`:

```python
import iot_conexion
import lavado_programa
import voz_comandos
```

- [ ] **Step 4: Agregar `procesar_comandos_voz` y `apagar_actuadores_voz_al_arrancar`**

Agregar después de `procesar_ciclo_lavado` (antes de `_anunciar_voz`):

```python
def procesar_comandos_voz(con: sqlite3.Connection, client: ModbusTcpClient, anteriores_voz: dict) -> dict:
    """Aplica por Modbus los cambios en voz_comandos.estado() (actuadores
    sostenidos por voz) desde la última vuelta -- solo escribe cuando algo
    CAMBIÓ, mismo criterio que registrar_si_cambio/procesar_ciclo_lavado.
    anteriores_voz: clave -> bool aplicado la vuelta pasada; devuelve el
    dict actualizado para pasarlo de nuevo en la próxima vuelta."""
    deseado = voz_comandos.estado()  # clave -> encendido_desde, solo las que están ON
    nuevo = {}
    for clave in voz_comandos.ACTUADORES_VALIDOS:
        on = clave in deseado
        if on != anteriores_voz.get(clave, False):
            _escribir_reles(client, [clave], on)
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"voz: {clave} -> {'encendido' if on else 'apagado'}")
        nuevo[clave] = on
    return nuevo


def apagar_actuadores_voz_al_arrancar(client: ModbusTcpClient) -> None:
    """Al arrancar el proceso no sabemos qué relés quedaron físicamente
    prendidos (el M300 mantiene su propio estado, independiente de este
    proceso) -- por seguridad, se fuerza apagado de todos los actuadores
    controlables por voz. Si algo se apaga así por error (por ejemplo
    alguien lo había prendido a mano desde la web del propio M300), hay que
    volver a prenderlo -- es una limitación aceptada, ver el spec."""
    _escribir_reles(client, sorted(voz_comandos.ACTUADORES_VALIDOS), False)
```

- [ ] **Step 5: Enganchar la limpieza de estado cuando el lavado apaga un relé por su cuenta**

En `procesar_ciclo_lavado`, agregar `voz_comandos.limpiar_estado(clave)`
junto a los dos lugares que ya apagan relés (cancelar, y fin de etapa) --
así el ciclo programado siempre manda, y un actuador sostenido por voz que
el lavado apaga de paso no se vuelve a prender solo en la próxima vuelta.

```python
    if comando == "cancelar":
        if activo and etapa_actual is not None and etapa_actual < len(programa):
            _escribir_reles(client, programa[etapa_actual]["reles"], False)
            for clave in programa[etapa_actual]["reles"]:
                voz_comandos.limpiar_estado(clave)
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 0, "
                    "etapa_actual = NULL, etapa_inicio = NULL WHERE id = 1")
        con.commit()
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: cancelado")
        return
```

```python
    _escribir_reles(client, etapa_cfg["reles"], False)
    for clave in etapa_cfg["reles"]:
        voz_comandos.limpiar_estado(clave)
    siguiente = etapa_actual + 1
```

(el resto de `procesar_ciclo_lavado` queda igual)

- [ ] **Step 6: Enganchar todo en `main()`**

```python
def main():
    con = _conectar_db()
    cfg = iot_conexion.config()
    client = ModbusTcpClient(cfg["host"], port=cfg["port"])
    anteriores = {canal: None for canal in CANALES}
    anteriores_voz = {clave: False for clave in voz_comandos.ACTUADORES_VALIDOS}
    print(f"Conectando a {cfg['host']}:{cfg['port']}... (Ctrl+C para salir)")
    try:
        if not client.connected:
            client.connect()
        apagar_actuadores_voz_al_arrancar(client)
        while True:
            if not client.connected:
                client.connect()
            for canal, cfg in CANALES.items():
                estado = _leer_estado(client, cfg["direccion"], cfg["invertido"])
                if estado is None:
                    continue
                arranco = estado and estado != anteriores[canal]
                anteriores[canal] = registrar_si_cambio(con, canal, estado, anteriores[canal])
                if arranco and AUDIO_ACTIVADO and canal in MENSAJES_VOZ:
                    _anunciar_voz(MENSAJES_VOZ[canal])
            ejecutar_comandos_pendientes(con, client)
            procesar_ciclo_lavado(con, client)
            anteriores_voz = procesar_comandos_voz(con, client, anteriores_voz)
            time.sleep(INTERVALO_POLL_S)
    except KeyboardInterrupt:
        print("Cortado por el usuario.")
    finally:
        client.close()
        con.close()
```

- [ ] **Step 7: Correr el script de verificación de nuevo y confirmar que pasa**

Run: `python <scratchpad>/test_iot_lavado_voz.py`
Expected: termina en `TODO OK`, sin `AssertionError`.

- [ ] **Step 8: Chequeo de sintaxis del archivo completo**

Run: `python -c "import py_compile; py_compile.compile('iot_lavado.py', doraise=True); print('OK sintaxis')"`
Expected: `OK sintaxis`

- [ ] **Step 9: Commit**

```bash
git add iot_lavado.py
git commit -m "iot_lavado: ejecuta comandos de voz sostenidos y los reconcilia con el lavado automático"
```

---

## Task 5: `app.py` — endpoint `POST /api/iot/pantalla/voz`

**Files:**
- Modify: `delpro-analitica/app.py`
- Test: manual con `curl` contra el servidor real corriendo (no hay forma
  de unit-testear esto sin un WAV real y sin STT, ver Step 4)

**Interfaces:**
- Consumes: `voz_stt.transcribir(bytes) -> str` (Task 3),
  `voz_comandos.interpretar(str) -> dict`, `voz_comandos.solicitar_encendido/apagado`
  (Task 1), `voz_sintesis.sintetizar_wav(str) -> bytes` (Task 2),
  `lavado_programa.solicitar_inicio()/solicitar_cancelacion()` (ya existen),
  `iot_canales.nombres()` (ya existe)

- [ ] **Step 1: Agregar los imports nuevos**

Junto a los imports existentes de `iot_canales`/`iot_conexion`/
`iot_monitoreo`/`lavado_programa`:

```python
import iot_canales
import iot_conexion
import iot_monitoreo
import lavado_programa
import voz_comandos
import voz_sintesis
import voz_stt
```

- [ ] **Step 2: Agregar la ruta a `_RUTAS_PUBLICAS`**

```python
_RUTAS_PUBLICAS = {"/login", "/webhook/whatsapp", "/api/iot/pantalla", "/api/iot/pantalla/historico",
                    "/api/iot/pantalla/io", "/api/iot/pantalla/actuador", "/api/iot/pantalla/lavado",
                    "/api/iot/pantalla/lavado/iniciar", "/api/iot/pantalla/lavado/cancelar",
                    "/api/iot/pantalla/voz"}
```

- [ ] **Step 3: Agregar el endpoint, después de `api_iot_pantalla_lavado_cancelar`**

```python
def _ejecutar_comando_voz(interpretado: dict) -> str:
    """Dispara la acción reconocida y devuelve el texto de confirmación a
    sintetizar. Nunca toca Modbus directo -- solo encola pedidos, mismo
    criterio que el resto de /api/iot/pantalla*."""
    tipo = interpretado.get("tipo")
    if tipo == "lavado_iniciar":
        if lavado_programa.solicitar_inicio():
            return "Lavado iniciado"
        return "No puedo, revisá si ya hay un lavado en curso o si falta configurar las etapas"
    if tipo == "lavado_cancelar":
        lavado_programa.solicitar_cancelacion()
        return "Lavado cancelado"
    if tipo == "actuador":
        clave, prender = interpretado["clave"], interpretado["prender"]
        nombre = iot_canales.nombres().get(clave, clave)
        if prender:
            ok = voz_comandos.solicitar_encendido(clave)
        else:
            ok = voz_comandos.solicitar_apagado(clave)
        if ok:
            return f"{nombre} {'encendida' if prender else 'apagada'}"
        return "No puedo, hay un lavado en curso"
    return "No entendí, repetí"


@app.post("/api/iot/pantalla/voz")
def api_iot_pantalla_voz():
    """Comando de voz "Jarvis" pedido desde la pantalla ESP32: recibe el
    audio grabado DESPUÉS de la wake word (WAV, 16kHz mono), lo transcribe,
    lo interpreta (voz_comandos.interpretar) y devuelve un WAV con la
    confirmación hablada para que la pantalla lo reproduzca por su
    parlante. Mismo criterio de seguridad que /actuador y /lavado/iniciar:
    bloqueado si el pedido llega por el túnel de Cloudflare."""
    if _pedido_via_tunel():
        return jsonify({"error": "No se puede usar comandos de voz desde fuera de la red del tambo"}), 403
    audio = request.get_data()
    texto = voz_stt.transcribir(audio)
    interpretado = voz_comandos.interpretar(texto)
    confirmacion = _ejecutar_comando_voz(interpretado)
    wav = voz_sintesis.sintetizar_wav(confirmacion)
    return Response(wav, mimetype="audio/wav")
```

- [ ] **Step 4: Probar a mano contra el servidor real, con el WAV grabado en el Task 3**

Con `servidor.py` corriendo (ver `CLAUDE.md`, "Cómo correr el servidor"):

```bash
curl -s -X POST --data-binary @<scratchpad>/prueba_iniciar_lavado.wav \
  -H "Content-Type: audio/wav" \
  http://127.0.0.1:5310/api/iot/pantalla/voz -o <scratchpad>/respuesta.wav
```

Expected: el archivo `respuesta.wav` se crea, pesa más de 1000 bytes y
empieza con `RIFF` (mismo chequeo que en el Task 2). Verificar además en
la base (`ciclo_lavado_estado`) que quedó `comando = 'iniciar'` -- prueba
de que el comando realmente se encoló:

```bash
python -c "
import sqlite3
con = sqlite3.connect('iot_sensores.db')
print(con.execute('SELECT comando, activo FROM ciclo_lavado_estado WHERE id=1').fetchone())
"
```

Después, limpiar ese comando de prueba para no dejarlo pendiente:
```bash
python -c "
import sqlite3
con = sqlite3.connect('iot_sensores.db')
con.execute(\"UPDATE ciclo_lavado_estado SET comando=NULL WHERE id=1\")
con.commit()
"
```

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "Agrega POST /api/iot/pantalla/voz: transcribe, interpreta y confirma comandos de voz"
```

---

## Parte B — Firmware (ESP32, requiere hardware real)

A partir de acá cada paso necesita la placa física conectada y probarse
escuchando/mirando de verdad -- no hay forma de automatizar esto, mismo
criterio que se usó para validar los relés físicos del M300 en la sesión
anterior.

## Task 6: Validar que el micrófono y el parlante de ESTA placa realmente andan

Antes de meter ESP-SR encima, hay que confirmar que el hardware de audio
(nunca usado en este proyecto) funciona en esta placa específica -- si algo
falla acá, es más fácil de diagnosticar sin WakeNet en el medio.

**Files:**
- Modify: `esp32-pantalla-lactia/main/main.c`

- [ ] **Step 1: Agregar el include del BSP de audio**

`esp32_p4_wifi6_touch_lcd_7b.h` (el header del BSP) ya se incluye
indirectamente vía el resto del firmware; confirmar que expone
`bsp_audio_codec_microphone_init`/`bsp_audio_codec_speaker_init`
(`grep -n "bsp_audio_codec" managed_components/waveshare__esp32_p4_wifi6_touch_lcd_7b/*.h`)
y agregar `#include "esp_codec_dev.h"` al principio de `main.c`.

- [ ] **Step 2: Función de prueba: grabar 3 segundos y reproducirlos**

Agregar una función de prueba temporal, llamada una sola vez desde
`app_main()` (se saca en el Task 9 una vez validado):

```c
static void prueba_audio_loopback(void)
{
    esp_codec_dev_handle_t mic = bsp_audio_codec_microphone_init();
    esp_codec_dev_handle_t spk = bsp_audio_codec_speaker_init();
    if (!mic || !spk) {
        ESP_LOGE(TAG, "prueba_audio: no se pudo inicializar mic/parlante");
        return;
    }

    esp_codec_dev_sample_info_t fs = {
        .sample_rate = 16000,
        .channel = 1,
        .bits_per_sample = 16,
    };
    ESP_ERROR_CHECK(esp_codec_dev_open(mic, &fs));
    esp_codec_dev_set_in_gain(mic, 30.0f);

    const int muestras = 16000 * 3;  // 3 segundos a 16kHz mono
    int16_t *buffer = malloc(muestras * sizeof(int16_t));
    assert(buffer);

    ESP_LOGI(TAG, "prueba_audio: grabando 3 segundos...");
    int leido = esp_codec_dev_read(mic, buffer, muestras * sizeof(int16_t));
    ESP_LOGI(TAG, "prueba_audio: leidos %d bytes", leido);
    esp_codec_dev_close(mic);

    ESP_ERROR_CHECK(esp_codec_dev_open(spk, &fs));
    ESP_LOGI(TAG, "prueba_audio: reproduciendo...");
    esp_codec_dev_write(spk, buffer, muestras * sizeof(int16_t));
    esp_codec_dev_close(spk);
    free(buffer);
    ESP_LOGI(TAG, "prueba_audio: listo");
}
```

Llamarla una vez al final de `app_main()`, después de que la pantalla ya
esté armada (`construir_pantalla_lavado();` u otra línea similar ya
existente):

```c
    prueba_audio_loopback();
```

- [ ] **Step 3: Compilar y flashear**

```bash
idf.py build
idf.py -p COM4 flash monitor
```

- [ ] **Step 4: Verificación manual**

Cuando aparezca `prueba_audio: grabando 3 segundos...` en el log, decir
algo en voz alta cerca de la placa. Esperar a que reproduzca. **Resultado
esperado:** se escucha por el parlante de la placa lo que se dijo,
reconocible, sin ser puro ruido/silencio.

**Si sale silencio o puro ruido:** el ES7210 de esta placa probablemente
expone más de 1 canal físico de micrófono y hay que ajustar `channel`
en `fs` (probar `2` y, si hace falta, `channel_mask` para elegir un canal
específico) -- confirmarlo mirando cuántos micrófonos tiene la placa
físicamente. Si no se escucha NADA por el parlante (ni ruido), revisar que
`esp_codec_dev_write` no haya devuelto error y que el volumen de salida
(`esp_codec_dev_set_out_vol`) no esté en 0.

- [ ] **Step 5: Commit (dejar la prueba en el código todavía, se retira en el Task 9)**

```bash
git add main/main.c   # este repo no tiene git -- si esp32-pantalla-lactia sigue sin inicializar, omitir este paso y solo dejar el archivo guardado
```

(Nota: `esp32-pantalla-lactia` no es un repositorio git en este proyecto --
este Step 5 no aplica a menos que eso haya cambiado; en ese caso, avisar y
decidir si conviene inicializarlo antes de seguir.)

---

## Task 7: Detectar la wake word "Jarvis" (WakeNet9), solo por log serie

**Files:**
- Modify: `esp32-pantalla-lactia/main/idf_component.yml`
- Modify: `esp32-pantalla-lactia/main/main.c`

- [ ] **Step 1: Agregar la dependencia de ESP-SR**

En `main/idf_component.yml`, agregar junto a las dependencias existentes:

```yaml
  espressif/esp-sr:
    version: "*"
```

- [ ] **Step 2: Correr `idf.py reconfigure` para bajar el componente**

```bash
idf.py reconfigure
```

Confirmar que no tira error de versión incompatible con IDF 5.5.5 ni con
el target `esp32p4`. Si tira error de compatibilidad, revisar en
https://github.com/espressif/esp-sr qué versión fijar en el `version:` de
arriba (reemplazar `"*"` por esa versión exacta) y repetir este paso.

- [ ] **Step 3: Seleccionar el modelo "Jarvis" por menuconfig**

```bash
idf.py menuconfig
```

Navegar a la sección de configuración de ESP-SR/WakeNet (aparece bajo
`Component config` una vez que el componente está agregado) y seleccionar
el modelo de wake word `wn9_jarvis` (o el nombre equivalente que liste el
menú para "Jarvis" en WakeNet9) en vez del que venga por defecto. Guardar
y salir.

- [ ] **Step 4: Reemplazar la prueba de audio del Task 6 por la detección de wake word**

Sacar la llamada a `prueba_audio_loopback()` del Task 6 (queda la función
por si hace falta volver a usarla para diagnosticar) y agregar, adaptado
del ejemplo oficial de Espressif (`esp-skainet/examples/wake_word_detection/afe`)
para usar `esp_codec_dev` en vez de las funciones de placa de referencia
que trae ese ejemplo:

```c
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_afe_sr_models.h"
#include "model_path.h"

static const esp_afe_sr_iface_t *afe_handle = NULL;
static esp_codec_dev_handle_t mic_dev = NULL;

static void tarea_alimentar_afe(void *arg)
{
    esp_afe_sr_data_t *afe_data = arg;
    int chunk = afe_handle->get_feed_chunksize(afe_data);
    int canales = afe_handle->get_feed_channel_num(afe_data);
    int16_t *buff = malloc(chunk * canales * sizeof(int16_t));
    assert(buff);
    while (1) {
        esp_codec_dev_read(mic_dev, buff, chunk * canales * sizeof(int16_t));
        afe_handle->feed(afe_data, buff);
    }
}

static void tarea_detectar_jarvis(void *arg)
{
    esp_afe_sr_data_t *afe_data = arg;
    while (1) {
        afe_fetch_result_t *res = afe_handle->fetch(afe_data);
        if (!res || res->ret_value == ESP_FAIL) {
            continue;
        }
        if (res->wakeup_state == WAKENET_DETECTED) {
            ESP_LOGI(TAG, "Jarvis detectado!");
        }
    }
}

static void iniciar_wake_word(void)
{
    mic_dev = bsp_audio_codec_microphone_init();
    assert(mic_dev);
    esp_codec_dev_sample_info_t fs = { .sample_rate = 16000, .channel = 1, .bits_per_sample = 16 };
    ESP_ERROR_CHECK(esp_codec_dev_open(mic_dev, &fs));
    esp_codec_dev_set_in_gain(mic_dev, 30.0f);

    srmodel_list_t *models = esp_srmodel_init("model");
    afe_config_t *afe_config = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    afe_handle = esp_afe_handle_from_config(afe_config);
    esp_afe_sr_data_t *afe_data = afe_handle->create_from_config(afe_config);
    afe_config_free(afe_config);

    xTaskCreatePinnedToCore(&tarea_alimentar_afe, "afe_feed", 8192, afe_data, 5, NULL, 0);
    xTaskCreatePinnedToCore(&tarea_detectar_jarvis, "afe_detect", 4096, afe_data, 5, NULL, 1);
}
```

Llamar `iniciar_wake_word();` una vez desde `app_main()`, en el mismo
lugar donde estaba `prueba_audio_loopback();`.

- [ ] **Step 5: Compilar, flashear y probar**

```bash
idf.py build
idf.py -p COM4 flash monitor
```

Decir "Jarvis" en voz alta cerca de la placa. **Resultado esperado:**
aparece `Jarvis detectado!` en el log serie. Probar también SIN decir
nada durante un minuto para confirmar que no aparecen falsos positivos
seguidos con ruido ambiente normal.

**Si hay muchos falsos positivos/negativos:** ajustar el umbral con
`afe_handle->set_wakenet_threshold(afe_data, 1, valor)` (valor entre 0 y 1,
más alto = menos falsos positivos pero más falsos negativos) -- no hace
falta cambiar nada más del diseño.

- [ ] **Step 6: Commit**

```bash
git add main/idf_component.yml main/main.c   # si esp32-pantalla-lactia sigue sin git, omitir
```

---

## Task 8: Al detectar "Jarvis", grabar, mandar a Flask y reproducir la respuesta

**Files:**
- Modify: `esp32-pantalla-lactia/main/main.c`

**Interfaces:**
- Consumes: `POST /api/iot/pantalla/voz` (Task 5) — request body: WAV
  16kHz mono; response body: WAV de confirmación.

- [ ] **Step 1: Agregar el códec de parlante y el estado de "escuchando"**

Junto a las variables globales de audio del Task 7:

```c
static esp_codec_dev_handle_t spk_dev = NULL;
static volatile bool jarvis_escuchando = false;
```

Inicializar `spk_dev = bsp_audio_codec_speaker_init();` dentro de
`iniciar_wake_word()`, junto a la inicialización de `mic_dev`.

- [ ] **Step 2: Grabar N segundos a un buffer WAV, con cabecera**

```c
#define VOZ_GRABACION_SEGUNDOS 4
#define VOZ_SAMPLE_RATE 16000

static void escribir_cabecera_wav(uint8_t *buf, uint32_t datos_bytes)
{
    uint32_t byte_rate = VOZ_SAMPLE_RATE * 2;
    memcpy(buf, "RIFF", 4);
    uint32_t chunk_size = 36 + datos_bytes;
    memcpy(buf + 4, &chunk_size, 4);
    memcpy(buf + 8, "WAVEfmt ", 8);
    uint32_t fmt_size = 16; memcpy(buf + 16, &fmt_size, 4);
    uint16_t audio_fmt = 1; memcpy(buf + 20, &audio_fmt, 2);
    uint16_t canales = 1; memcpy(buf + 22, &canales, 2);
    uint32_t sample_rate = VOZ_SAMPLE_RATE; memcpy(buf + 24, &sample_rate, 4);
    memcpy(buf + 28, &byte_rate, 4);
    uint16_t block_align = 2; memcpy(buf + 32, &block_align, 2);
    uint16_t bits = 16; memcpy(buf + 34, &bits, 2);
    memcpy(buf + 36, "data", 4);
    memcpy(buf + 40, &datos_bytes, 4);
}
```

- [ ] **Step 3: Tarea que graba, hace el POST y reproduce la respuesta**

```c
static void tarea_comando_voz(void *arg)
{
    const uint32_t muestras = VOZ_SAMPLE_RATE * VOZ_GRABACION_SEGUNDOS;
    const uint32_t datos_bytes = muestras * sizeof(int16_t);
    uint8_t *wav = malloc(44 + datos_bytes);
    assert(wav);
    escribir_cabecera_wav(wav, datos_bytes);

    jarvis_escuchando = true;
    esp_codec_dev_read(mic_dev, wav + 44, datos_bytes);
    jarvis_escuchando = false;

    char url[192];
    snprintf(url, sizeof(url), "%s/api/iot/pantalla/voz", base_url);
    esp_http_client_config_t config = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 15000,   // la transcripcion + sintesis puede tardar unos segundos
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "audio/wav");
    esp_http_client_open(client, 44 + datos_bytes);
    esp_http_client_write(client, (const char *)wav, 44 + datos_bytes);
    free(wav);

    int status = esp_http_client_fetch_headers(client);
    if (status < 0 || esp_http_client_get_status_code(client) != 200) {
        ESP_LOGW(TAG, "comando de voz: fallo el pedido a Flask");
        esp_http_client_cleanup(client);
        vTaskDelete(NULL);
        return;
    }

    int total = esp_http_client_get_content_length(client);
    uint8_t *respuesta = malloc(total);
    if (respuesta) {
        int leido = esp_http_client_read(client, (char *)respuesta, total);
        if (leido > 44) {
            esp_codec_dev_sample_info_t fs = { .sample_rate = VOZ_SAMPLE_RATE, .channel = 1, .bits_per_sample = 16 };
            esp_codec_dev_open(spk_dev, &fs);
            esp_codec_dev_write(spk_dev, respuesta + 44, leido - 44);
            esp_codec_dev_close(spk_dev);
        }
        free(respuesta);
    }
    esp_http_client_cleanup(client);
    vTaskDelete(NULL);
}
```

- [ ] **Step 4: Disparar la tarea desde `tarea_detectar_jarvis` en vez de solo loguear**

```c
        if (res->wakeup_state == WAKENET_DETECTED) {
            ESP_LOGI(TAG, "Jarvis detectado!");
            xTaskCreate(&tarea_comando_voz, "comando_voz", 8192, NULL, 5, NULL);
        }
```

- [ ] **Step 5: Compilar, flashear y probar fin a fin**

```bash
idf.py build
idf.py -p COM4 flash monitor
```

Con `servidor.py` corriendo, decir "Jarvis, iniciar lavado" (con etapas ya
configuradas en la web) o "Jarvis, prender bomba de agua" (con ese nombre
ya configurado). **Resultado esperado:** el log muestra `Jarvis
detectado!`, después de unos segundos se escucha la confirmación hablada
por el parlante de la placa, y el relé correspondiente responde
físicamente (mismo criterio de verificación física que se usó para
validar los relés del M300: mirar/escuchar el equipo real).

- [ ] **Step 6: Commit**

```bash
git add main/main.c   # si esp32-pantalla-lactia sigue sin git, omitir
```

---

## Task 9: Indicador visual "Escuchando..." y limpieza final

**Files:**
- Modify: `esp32-pantalla-lactia/main/main.c`

- [ ] **Step 1: Sacar `prueba_audio_loopback` del Task 6 si sigue en el código**

Confirmar que no queda ninguna llamada a esa función en `app_main()` (la
función puede quedar definida por si hace falta para diagnosticar más
adelante, documentada como tal con un comentario corto).

- [ ] **Step 2: Agregar un label overlay simple**

Crear un label global `lbl_jarvis_escuchando`, hijo de la pantalla activa
en cada momento (reparentado, mismo patrón que la retícula de diagnóstico
de touch documentada en `CLAUDE.md`), oculto por defecto:

```c
static lv_obj_t *lbl_jarvis_escuchando;
```

Crearlo una vez en `app_main()` después de armar todas las pantallas:

```c
    lbl_jarvis_escuchando = lv_label_create(lv_layer_top());
    lv_label_set_text(lbl_jarvis_escuchando, "Jarvis: escuchando...");
    lv_obj_set_style_bg_color(lbl_jarvis_escuchando, COLOR_ACENTO, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(lbl_jarvis_escuchando, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_text_color(lbl_jarvis_escuchando, lv_color_white(), LV_PART_MAIN);
    lv_obj_set_style_pad_all(lbl_jarvis_escuchando, 10, LV_PART_MAIN);
    lv_obj_set_style_radius(lbl_jarvis_escuchando, 8, LV_PART_MAIN);
    lv_obj_align(lbl_jarvis_escuchando, LV_ALIGN_TOP_MID, 0, 4);
    lv_obj_add_flag(lbl_jarvis_escuchando, LV_OBJ_FLAG_HIDDEN);
```

`lv_layer_top()` lo dibuja arriba de cualquier pantalla activa sin
necesidad de reparentarlo cada vez que se cambia de tab.

- [ ] **Step 3: Mostrarlo/ocultarlo según `jarvis_escuchando`**

En `tarea_comando_voz` (Task 8), envolver el bloque de grabación:

```c
    bsp_display_lock(-1);
    lv_obj_clear_flag(lbl_jarvis_escuchando, LV_OBJ_FLAG_HIDDEN);
    bsp_display_unlock();

    jarvis_escuchando = true;
    esp_codec_dev_read(mic_dev, wav + 44, datos_bytes);
    jarvis_escuchando = false;

    bsp_display_lock(-1);
    lv_obj_add_flag(lbl_jarvis_escuchando, LV_OBJ_FLAG_HIDDEN);
    bsp_display_unlock();
```

(la variable `jarvis_escuchando` del Step 1 del Task 8 puede sacarse si
no se usa en ningún otro lado más que para mostrar/ocultar este label --
en ese caso, dejar solo el show/hide de arriba y borrar la variable suelta
para no dejar código muerto)

- [ ] **Step 4: Compilar, flashear y probar**

```bash
idf.py build
idf.py -p COM4 flash monitor
```

Decir "Jarvis" y confirmar que aparece el cartel "Jarvis: escuchando..."
arriba de la pantalla que esté activa en ese momento, y que desaparece
solo cuando termina de grabar.

- [ ] **Step 5: Commit**

```bash
git add main/main.c   # si esp32-pantalla-lactia sigue sin git, omitir
```

---

## Self-Review (completado antes de entregar este plan)

- **Cobertura del spec:** wake word gratis (Task 7), STT local en español
  (Task 3), vocabulario cerrado + nombres configurados (Task 1), actuadores
  sostenidos (Task 1 + 4), bloqueo si el lavado usa el relé (Task 1),
  apagado de seguridad al arrancar (Task 4), confirmación hablada (Task 2 +
  5 + 8), indicador visual (Task 9), limitación de no barge-in (aceptada,
  no requiere código: el mic no se lee mientras se reproduce la respuesta).
  Sin gaps encontrados.
- **Placeholders:** ninguno — cada step tiene código completo o un comando
  concreto para correr.
- **Consistencia de tipos:** `interpretar()` devuelve siempre `tipo` +
  (`clave`,`prender`) solo para `"actuador"` — usado igual en Task 1 (test),
  Task 5 (`_ejecutar_comando_voz`). `procesar_comandos_voz(con, client,
  anteriores_voz) -> dict` con la misma firma en su definición (Task 4) y
  en `main()` (Task 4, Step 6). `ACTUADORES_VALIDOS` definido una sola vez
  en `voz_comandos.py` (Task 1) y consumido por nombre calificado
  (`voz_comandos.ACTUADORES_VALIDOS`) en Task 4 y Task 1-test — nunca
  redeclarado con otro nombre.
