# Comandos de voz "Jarvis" para la pantalla LactIA

Fecha: 2026-08-29
Estado: aprobado para pasar a plan de implementación

## Contexto

La pantalla táctil ESP32-P4 (`esp32-pantalla-lactia/`) ya controla actuadores
individuales y el ciclo de Lavado Automático (ver
`delpro-analitica/CLAUDE.md`, secciones "Lavado Automático" y "M300 necesita
mapeo explícito"). El tambo pidió poder manejar esos mismos comandos con las
manos ocupadas o mojadas, usando una palabra de activación ("Jarvis") en vez
de tocar la pantalla.

## Objetivo (v1)

1. Decir "Jarvis" activa la escucha en la pantalla, sin tocar nada.
2. Comandos soportados:
   - Iniciar el ciclo de Lavado Automático ("iniciar lavado" y variantes).
   - Cancelarlo ("cancelar", "parar").
   - Prender o apagar un actuador individual por su **nombre configurado**
     en ⚙ Configuración › Entradas/Salidas (ej. "prender bomba de agua"),
     de forma **sostenida** (queda encendido hasta que se pida apagarlo —
     no es el pulso de 0,5s que ya usa el botón táctil de Actuadores, que
     sigue existiendo tal cual está).
3. La pantalla confirma por voz lo que hizo (o que no entendió), usando su
   parlante.

## Fuera de alcance (v1)

- Reconocimiento de habla abierto/libre (solo un vocabulario cerrado y
  chico: las frases fijas + los nombres de actuadores ya configurados).
- "Barge-in" (interrumpir a la pantalla mientras está hablando la
  confirmación) — hay que esperar a que termine antes del próximo "Jarvis".
- Reanudar automáticamente el estado sostenido de un actuador después de
  reiniciar `iot_lavado.py` (ver "Arranque y reinicio" más abajo).
- Wake word personalizada pagada: no hace falta, "Jarvis" ya es uno de los
  modelos gratuitos que trae WakeNet9 de Espressif (ver Investigación).

## Investigación de viabilidad (ya hecha)

- El BSP oficial de la placa (`waveshare__esp32_p4_wifi6_touch_lcd_7b`) ya
  trae `bsp_audio_codec_microphone_init()` (códec **ES7210**, entrada) y
  `bsp_audio_codec_speaker_init()` (códec **ES8311**, salida) — hoy sin usar
  en `main.c`. La placa tiene PSRAM de sobra (32MB, modo HEX) para correr
  WakeNet9.
- **"Jarvis" es un modelo de wake word gratuito e incluido** en WakeNet9 de
  Espressif (ESP-SR), con soporte confirmado para ESP32-P4. No hace falta
  pagar ni entrenar nada custom
  ([lista de modelos](https://components.espressif.com/components/espressif/esp-sr/versions/2.1.4)).
- **MultiNet** (el reconocedor de comandos de Espressif que sigue a la wake
  word) **solo soporta inglés y chino** — no sirve para reconocer "iniciar
  lavado" en español. Por eso el diseño manda el audio posterior a la wake
  word a la PC para transcribirlo, en vez de intentar reconocer el comando
  entero dentro del ESP32.
- Riesgo pendiente de confirmar en la implementación: que el micrófono y el
  parlante estén realmente poblados/conectados en esta placa física (el BSP
  los soporta a nivel de software, pero no se probó nunca en este
  proyecto) — primer paso de la implementación, antes de construir nada
  arriba.

## Arquitectura

```
ESP32 (siempre escuchando "Jarvis", local, sin red)
  │  detecta "Jarvis" → pantalla: "Escuchando..." → graba ~4s por el mic (ES7210)
  ▼
POST /api/iot/pantalla/voz   (WAV 16kHz mono, LAN-only, mismo guard que el resto)
  │
  ▼
Flask (PC del tambo)
  │  1) transcribe con Vosk local (español, CPU, sin nube)
  │  2) voz_comandos.interpretar(texto) → matchea contra frases fijas +
  │     nombres de actuadores configurados (iot_canales)
  │  3) según el match: lavado_programa.solicitar_inicio()/cancelacion(),
  │     o voz_comandos.solicitar_encendido()/apagado()
  │  4) arma un texto de confirmación y lo sintetiza a WAV
  ▼
Respuesta: WAV de confirmación (bytes, sin JSON intermedio)
  │
  ▼
ESP32: reproduce el WAV por el parlante (ES8311) → vuelve a escuchar "Jarvis"
```

La ejecución real de relés sigue pasando SOLO por `iot_lavado.py` (único
dueño de la conexión Modbus al M300), igual que hoy con los pulsos de
Actuadores y el ciclo de Lavado Automático — Flask solo encola pedidos en
SQLite, nunca escribe Modbus directo.

## Componentes nuevos

**`delpro-analitica/voz_comandos.py`** (nuevo, módulo hoja como
`lavado_programa.py`/`iot_conexion.py`, sin importar `iot_lavado`):
- `interpretar(texto: str) -> dict` — matchea contra la lista de frases fijas
  ("iniciar lavado", "arrancar lavado", "cancelar", "parar", ...) y contra
  "prender/encender <nombre>" / "apagar <nombre>" para cada actuador que
  tenga un nombre custom configurado en `iot_canales`. Usa comparación
  difusa (tolera errores chicos de transcripción) con un umbral de
  confianza; por debajo del umbral devuelve `{"tipo": "desconocido"}`.
- Tabla nueva `voz_actuadores_estado` (clave, encendido_desde) — actuadores
  sostenidos por voz, independiente del pulso de 0,5s existente.
- `solicitar_encendido(clave)` / `solicitar_apagado(clave)` / `estado()` —
  mismo patrón que `lavado_programa.solicitar_inicio()`.

**`delpro-analitica/voz_sintesis.py`** (nuevo, módulo hoja):
- `sintetizar_wav(texto: str) -> bytes` — reutiliza el mismo motor de voz de
  Windows que ya usa `_anunciar_voz` en `iot_lavado.py`, pero grabando a un
  archivo WAV en vez de reproducir por los parlantes de la PC.

**`iot_lavado.py`** (agrega, mismo patrón que `procesar_ciclo_lavado`):
- `procesar_comandos_voz(con, client)`, en el mismo loop de sondeo de 3s:
  antes de prender/apagar un relé pedido por voz, chequea si ese relé
  pertenece a la etapa ACTUAL de un Lavado Automático activo
  (`lavado_programa.estado()`) — si es así, lo descarta (queda registrado
  como rechazado, no se toca Modbus).
- Al arrancar el proceso: vacía `voz_actuadores_estado` (no reafirma "on"
  para nada — ver "Arranque y reinicio").

**`app.py`**: `POST /api/iot/pantalla/voz` (agregado a las rutas
LAN-only) — recibe el WAV, hace los pasos 1-4 de arriba, devuelve el WAV de
confirmación como body de la respuesta (`Content-Type: audio/wav`).

**`esp32-pantalla-lactia/main/main.c`**: nueva dependencia
`espressif/esp-sr` (a validar versión compatible con IDF 5.5.5 + target
P4 durante la implementación); tarea de fondo con el pipeline de audio de
ESP-SR (WakeNet9, modelo "Jarvis"); al detectar la wake word, pausa esa
tarea, graba a buffer, hace el POST, reproduce la respuesta, reanuda.
Indicador visual simple ("Escuchando...") superpuesto a la pantalla que
esté activa en ese momento.

## Arranque y reinicio (seguridad)

`iot_lavado.py` no sabe qué relés quedaron físicamente prendidos si se
reinicia (el M300 mantiene el estado de sus relés de forma independiente a
nuestro proceso Python). Por eso el diseño es explícito y conservador: al
arrancar, `iot_lavado.py` **apaga por Modbus los relés que son
controlables por voz** (el mismo set de 8 salidas que ya usa Actuadores),
sin importar cómo hayan quedado antes — así el estado conocido después de
un reinicio es siempre "apagado", en vez de asumir (potencialmente mal) que
sigue como estaba. Si algo se apagó así por error (por ejemplo alguien lo
había prendido manualmente desde la web del propio M300, no por voz), hay
que volver a prenderlo a mano — se documenta como limitación aceptada, no
como bug.

## Manejo de errores / casos límite

- **No se entiende el comando** (confianza baja o transcripción vacía): no
  se toca ningún relé; la confirmación hablada es "No entendí, repetí".
- **Relé pedido en uso por un Lavado Automático activo**: se ignora el
  comando; confirmación "No puedo, hay un lavado en curso" (decisión ya
  confirmada con el tambo).
- **Falla el POST a Flask** (sin red, servidor caído): la pantalla no tiene
  nada que reproducir; se resigna y vuelve a escuchar "Jarvis" en silencio
  (mismo criterio que ya usan las otras tareas de red de este firmware:
  loguear con `ESP_LOGW` y seguir).
- **La transcripción tarda demasiado o la PC está sobrecargada**: se acepta como
  riesgo conocido (ver más abajo), no se resuelve en v1 con timeouts
  agresivos que corten la respuesta a mitad de camino.

## Plan de pruebas

- `voz_comandos.interpretar()`: pruebas unitarias con una batería de textos
  "transcriptos" (incluyendo variantes con errores típicos de STT) para
  validar el umbral de confianza y que no haya falsos positivos entre
  actuadores con nombres parecidos.
- `POST /api/iot/pantalla/voz`: prueba con un WAV grabado a mano
  diciendo cada frase soportada, verificando que encola el comando
  correcto en la base y que responde con audio.
- Firmware: prueba manual en el hardware real (decir "Jarvis" a distintas
  distancias/volumen, con ruido de fondo del tambo) — no hay forma de
  automatizar esto, mismo criterio que se usó para validar los relés
  físicos del M300.
- Fin a fin: repetir la misma verificación física que se hizo con los
  pulsos de Actuadores (mirar/escuchar el relé de verdad) antes de dar por
  terminada la función de voz.

## Riesgos conocidos

- **RAM/CPU de la PC** (ya se sabe que corre con poca RAM por el SQL
  Express local): se usa Vosk con su modelo chico de español (~38MB), que
  alcanza de sobra para un vocabulario cerrado y fijo (no dictado libre).
  Si hiciera falta cambiar de motor, la interfaz de `voz_stt.transcribir`
  no cambia -- se toca solo ese archivo.
- **Windows Smart App Control puede bloquear binarios nativos de Python.**
  Ya pasó en la PC de desarrollo: `faster-whisper` (el motor elegido
  originalmente) no se puede ni importar acá porque SAC bloquea el binario
  de PyAV del que depende, y SAC no tiene excepción por archivo -- solo se
  puede desactivar por completo, lo cual es irreversible sin reinstalar
  Windows. Vosk no tiene esa dependencia y sí funciona (verificado). **Al
  desplegar en la PC de producción hay que confirmar que Vosk importe ahí
  también** antes de dar la feature por terminada; si esa máquina tuviera
  una política parecida y más estricta, este es el punto que va a fallar.
- **Ruido de tambo** (motores, agua corriendo) puede afectar la tasa de
  falsos positivos/negativos de WakeNet — el umbral de detección es
  ajustable sin rediseñar nada.
- **Hardware de audio nunca probado en este proyecto**: hay que confirmar
  temprano en la implementación que el mic y el parlante de esta placa
  específica funcionan antes de construir el resto arriba.
