# Orígenes de datos — Etapa 4: comandar un ESP32 (salidas de un origen HTTP) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el tambo pueda activar desde LactIA una salida de un equipo que manda por HTTP (un ESP32 con un relé): LactIA deja una **orden**, el ESP32 la **consulta**, la ejecuta y **confirma**; una orden que nadie levantó a tiempo **vence** y nunca se ejecuta tarde, y todas terminan en un estado que se ve.

**Architecture:** una tabla propia `ordenes_origen` (NO la `comandos_actuador` del M300) con el ciclo de vida de cada orden, un módulo `iot_ordenes.py` que lo maneja bajo un lock, y tres rutas: las dos del ESP32 (`GET …/ordenes`, `POST …/ordenes/<id>/ok`, con el mismo token y las mismas defensas que el ingreso) y una de administración que encola. `iot_lavado.py` no se toca.

**Tech Stack:** Python 3.12, Flask, SQLite, `unittest`. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-11-23-iot-origenes-design.md` (sección 5; la 2 para el canal). Etapas 1, 2 y 3 ya están en `master`: leer `iot_origenes.py`, `iot_ingreso.py` y la ruta `api_iot_ingreso` en `app.py` antes de empezar.

**Correr tests** (uno por módulo; `discover` no sirve, ver CLAUDE.md):
`C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_ordenes -v`

## Cambio respecto de la spec (y por qué)

La spec dice sumarle `origen` y `vence` a `comandos_actuador`. **Este plan usa una tabla aparte.** Hoy `iot_lavado.ejecutar_comandos_pendientes` lee TODA fila con `ejecutado = 0` y marca como «error: canal desconocido» la que no es del M300: con las órdenes de un ESP32 en esa tabla, un `iot_lavado.py` viejo (la web se actualiza con `git pull`, el proceso del gateway solo cuando alguien lo reinicia) **se las comería a todas** sin que nadie lo note. Una tabla aparte hace imposible ese cruce y deja intacto el camino del M300 (lo fijan las pruebas existentes).

## Global Constraints

- Sin ningún origen que `puede_comandar`, TODO se comporta como hoy; `comandos_actuador`, `iot_lavado.py`, la pantalla ESP32 y el lavado/voz no se tocan.
- **Solo un origen HTTP de formato `generico` puede comandar** (`puede_comandar`). ChirpStack no: sus downlinks esperan al próximo uplink del nodo (no-objetivo de la spec). Los canales `salida` de un origen que no puede comandar se rechazan.
- **`vencimiento_orden_s` es OBLIGATORIO al tildar «puede comandar», entero ≥ 1, sin defecto** (depende de cada cuánto consulta su ESP32: tiene que ser mayor). Sin valores inventados para umbrales; el único tope técnico nuevo es `PULSO_MAX_ORIGEN_S = 60` (un error de tipeo no puede dejar un relé una hora), cambiable en una constante.
- Una salida de un origen tiene `modo`: `pulso` (con `pulso_s` obligatorio, 0 < pulso_s ≤ `PULSO_MAX_ORIGEN_S`) o `sostenida` (se prende y se apaga con órdenes separadas, **sin tope de tiempo encendida**: como las sostenidas por voz, ese número es del tambo). Acciones: `pulso` solo en modo pulso; `prender`/`apagar` solo en modo sostenida.
- **Una orden se entrega UNA sola vez y nunca vencida.** Estados: `pendiente` → `entregada` → `ejecutada` | `error`; `vencida` (nadie la levantó antes de `vence`) y `sin_confirmar` (se entregó y no hubo confirmación pasado `vencimiento_orden_s`). Todo estado no terminal se resuelve solo; nada queda «enviada» para siempre. Una orden entregada cuya respuesta se perdió NO se reenvía (el error seguro es no disparar dos veces).
- A lo sumo UNA orden `pendiente` o `entregada` por canal: apretar dos veces el botón no apila pulsos.
- Encolar es solo del administrador con sesión; ninguna ruta pública (ni la del ESP32) puede crear una orden. Token, bloqueo por IP y el mismo 403 para token malo/origen inexistente/origen Modbus: los de la etapa 3, compartidos (no copiados).
- Textos de la app: fechas con día de la semana; nombres de rodeo de DelPro. No hacer commit ni push sin que el usuario lo pida. Nunca borrar archivos. Ningún test toca la red.

## Review Focus

1. Dos consultas a la vez no entregan la misma orden dos veces; una orden vencida nunca sale, ni por la consulta ni por un reloj atrasado del ESP32 (T2).
2. El token de un origen no sirve para consultar ni confirmar las órdenes de otro (la orden de otro origen contesta igual que una que no existe) (T2, T3).
3. Confirmación basura: orden inexistente, repetida, de otro origen, ya vencida, cuerpo que no es objeto, `error` larguísimo o que no es texto: 4xx, nunca 500 ni un estado inconsistente (T2, T3).
4. El ESP32 que vuelve a los 20 minutos: lo pendiente quedó `vencida`, lo entregado sin confirmar `sin_confirmar`; el panel no muestra ninguna orden «enviada» eterna (T2).
5. Apretar el botón dos veces seguidas, o tres administradores a la vez: una sola orden viva por canal (T2, T3).
6. Sacar el canal, apagar «puede comandar» o eliminar el origen con órdenes vivas: esas órdenes se cancelan y no se entregan después (T1, T3).
7. Un `iot_lavado.py` viejo que sigue corriendo no toca las órdenes (tabla aparte) (T2).
8. Una salida `sostenida` no se presenta como estado real del relé (LactIA no lo sabe): se muestra «última orden: prender, ejecutada el …» (T3, T4).

## Estructura de archivos

- Crear `iot_ordenes.py` (tabla, ciclo de vida, lock). Importa `iot_lavado` (para `_conectar_db`/`RUTA_DB`) e `iot_origenes`.
- Modificar `iot_origenes.py` (`puede_comandar`, `vencimiento_orden_s`, canales `salida`, `salidas()`), `app.py` (rutas; la autenticación del ingreso pasa a un helper compartido), `templates/index.html` (tarjeta), `CLAUDE.md`, `INSTALL.md`, `docs/iot-ingreso-http.md`.
- Tests nuevos: `tests/test_iot_origenes_comando.py` (T1), `tests/test_iot_ordenes.py` (T2), `tests/test_iot_ordenes_api.py` (T3).

---

### Task 1: `iot_origenes.py` — puede comandar y canales de salida

**Files:** Modify `iot_origenes.py`; Create `tests/test_iot_origenes_comando.py`.

**Interfaces:**
- Produces:
  - Constantes `MODOS_SALIDA = ("pulso", "sostenida")`, `PULSO_MAX_ORIGEN_S = 60`.
  - Un origen HTTP lleva `puede_comandar: bool` (falta en el archivo = `False`) y `vencimiento_orden_s: int | None`. `guardar_origen(id, nombre, formato, ventana, canales, *, sensores_analogicos, sensores_ocupados, entradas_en_uso, puede_comandar=False, vencimiento_orden_s=None)` (los dos nuevos con defecto para no romper a los llamadores de la etapa 3).
  - Canal `{grupo: "salida", clave, nombre, modo, pulso_s}` (`pulso_s` solo en modo `pulso`; los demás campos analógicos/sensor no aplican). Solo se acepta si `puede_comandar`.
  - `salidas() -> tuple[str]`: claves globales de todos los canales `salida`. `entradas_digitales()`, `sensores_asignados()` y `ventanas()` NO incluyen salidas. Un origen Modbus nunca tiene salidas (se rechazan como hoy).

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_origenes_comando.py` (base de `tests/test_iot_origenes.py`, `_RUTA` temporal):
  - Guardado feliz: `puede_comandar=True, vencimiento_orden_s=120`, un canal `salida` modo `pulso` con `pulso_s=1.5` y otro `sostenida`; `obtener` los devuelve; `salidas()` los nombra; sin archivo o con un origen de la etapa 3 (sin las claves nuevas) `puede_comandar` es `False`.
  - **Review Focus de validación**: `puede_comandar=True` sin vencimiento, con `0`, `-3`, `1.5`, `"120"`, `True`, `None` → `ValueError` y el archivo no cambia byte a byte; `puede_comandar=True` en un origen `chirpstack` → error legible («ChirpStack no se comanda…»); un canal `salida` con `puede_comandar=False` → «tildá “puede comandar”»; modo ausente o desconocido; `pulso_s` ausente, `0`, `NaN`, `61`, texto, o presente en modo `sostenida` → error; salida con `sensor`/`factor` → error; clave repetida con una entrada → error.
  - Apagar `puede_comandar` o sacar una salida son válidos aunque haya órdenes (las cancela la capa de arriba); un canal `salida` no cuenta como entrada digital ni se puede elegir en un sensor.
  - Los 49 tests de `tests.test_iot_origenes` y los de Modbus siguen verdes.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar** (`_canal` acepta `grupo == "salida"` solo si el origen lo permite; el chequeo de formato `generico` vive en `guardar_origen`). **Step 4: verde + suites de orígenes.** **Step 5:** `git add iot_origenes.py tests/test_iot_origenes_comando.py`.

---

### Task 2: `iot_ordenes.py` — el ciclo de vida de una orden

**Files:** Create `iot_ordenes.py`, `tests/test_iot_ordenes.py`.

**Interfaces:**
- Consumes: `iot_origenes.obtener`, `iot_lavado._conectar_db`/`RUTA_DB`.
- Produces:
  - `RUTA_DB = iot_lavado.RUTA_DB` (patcheable), `ACCIONES = ("pulso","prender","apagar")`, `ESTADOS`, `MAX_RESULTADO = 300`, `MAX_POR_CONSULTA = 10`, `class OrdenError(ValueError)` con `.codigo` (HTTP sugerido: 400, 404, 409).
  - Tabla `ordenes_origen(id INTEGER PK AUTOINCREMENT, origen, canal, accion, duracion_s REAL, creada, vence, estado, entregada, resuelta, resultado, usuario)`; se crea en `_conectar()` de este módulo.
  - `crear(origen: dict, canal: str, accion: str, usuario: str, ahora=None) -> dict` (`{id, canal, accion, duracion_s, estado, creada, vence}`): valida que el origen puede comandar, que `canal` es una `salida` suya, que la acción sirve para su modo, y que no hay otra orden `pendiente`/`entregada` del canal; `duracion_s` sale del canal; `vence = creada + vencimiento_orden_s`.
  - `entregar(origen_id, ahora=None) -> list[dict]`: primero resuelve lo vencido; después marca `entregada` (con la hora) y devuelve a lo sumo `MAX_POR_CONSULTA` órdenes `pendiente` no vencidas, más viejas primero, como `{id, canal, accion, duracion_s}` (canal = clave LOCAL, la que conoce el firmware). Todo bajo un lock de módulo y en una transacción.
  - `confirmar(origen_id, orden_id, error=None, ahora=None) -> dict`: solo sobre una orden de ESE origen en estado `entregada` (o `sin_confirmar`, que pasa a lo que realmente fue); `error` (texto o `None`) decide `ejecutada`/`error`; repetir la misma confirmación es idempotente; cualquier otra cosa → `OrdenError`.
  - `resolver_vencidas(origen_id=None, ahora=None) -> int`; `cancelar(origen_id, canal=None, motivo="") -> int` (pasa a `vencida` lo `pendiente` con ese `resultado`, y marca `sin_confirmar` lo `entregada`); `listar(origen_id, limite=20, ahora=None) -> list[dict]` (resuelve antes de leer; más nuevas primero); `ultima_por_canal(origen_id) -> {clave: {accion, estado, resuelta}}`.

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_ordenes.py` (origen `generico` de prueba con una salida `pulso` y otra `sostenida`, `ahora` inyectado, base y `_RUTA` temporales):
  - `crear` feliz y todas sus negativas: origen sin `puede_comandar`, canal inexistente o que no es salida, pulso en una sostenida, `prender` en una de pulso, acción desconocida, segunda orden mientras hay una `pendiente` o `entregada` (el mensaje la nombra); con la anterior ya `ejecutada` sí se puede.
  - `entregar`: devuelve la orden con la forma exacta; una segunda llamada no la devuelve; **Review Focus 1** dos hilos llamando a la vez con 5 órdenes pendientes de canales distintos entregan cada una exactamente una vez; una orden con `vence` en el pasado no sale y queda `vencida` (aunque `ahora` vuelva para atrás); más de 10 pendientes se entregan de a 10.
  - `confirmar`: feliz (`ejecutada`), con `error` (`error`, texto cortado a `MAX_RESULTADO`); **Review Focus 3** inexistente → 404, de otro origen → el MISMO 404, `pendiente` (todavía no entregada) → 409, ya `vencida` → 409, repetida igual → idempotente, repetida distinta → 409; `error` que no es texto → se ignora como texto (`str`) sin romper.
  - **Review Focus 4**: entregada y sin confirmar pasado `vencimiento_orden_s` desde la entrega → `sin_confirmar` en `listar`; una confirmación tardía de una `sin_confirmar` la pasa a `ejecutada`/`error`; ninguna orden queda `entregada` más allá de ese plazo.
  - `cancelar` por canal y por origen; `ultima_por_canal` devuelve lo más reciente de cada canal; **Review Focus 7**: `iot_lavado.ejecutar_comandos_pendientes` con una orden viva no la toca (no comparten tabla).
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar** (una conexión por llamada, el patrón de `iot_ingreso`). **Step 4: verde + `tests.test_iot_ingreso`, `tests.test_iot_lavado_mapa`.** **Step 5:** `git add iot_ordenes.py tests/test_iot_ordenes.py`.

---

### Task 3: rutas del ESP32 y de administración

**Files:** Modify `app.py`; Create `tests/test_iot_ordenes_api.py`.

**Interfaces:**
- Consumes: T1 (`guardar_origen` con los campos nuevos), T2 (`crear/entregar/confirmar/listar/cancelar/ultima_por_canal`).
- Produces:
  - Helper `_autenticar_ingreso(origen_id) -> (origen, respuesta | None)` con TODO lo que hoy hace `api_iot_ingreso` antes de mirar el cuerpo (bloqueo por IP y balde, `X-Token`, `?token=` solo ChirpStack, el mismo 403, limpieza de intentos); `api_iot_ingreso` pasa a usarlo sin cambiar de comportamiento.
  - `GET /api/iot/ingreso/<id>/ordenes` (token) → `200 {"ordenes": [{id, canal, accion, duracion_s}…]}`; un origen que no puede comandar → `409 {"error": "Este origen no recibe órdenes."}`. Es público por el prefijo ya abierto.
  - `POST /api/iot/ingreso/<id>/ordenes/<int:orden>/ok` (token), cuerpo opcional `{"error": "…"}` → `200 {"ok": true, "estado": …}`; `OrdenError` → su código con `{"error": …}`; tope de 65.536 bytes como el ingreso; cuerpo no JSON o no objeto = confirmación sin error si está vacío, 400 si trae algo que no es un objeto.
  - `POST /api/iot/origenes/<id>/ordenes` (admin) `{canal, accion}` → `202 {"orden": …}`, anota el usuario de la sesión; `OrdenError`/`ValueError` → 400/409.
  - `GET /api/iot/origenes` suma, a cada origen que puede comandar, `puede_comandar`, `vencimiento_orden_s`, `ordenes` (las últimas 10 con su estado) y, a cada canal `salida`, `ultima_orden`. `POST /api/iot/origenes/<id>` acepta `puede_comandar` y `vencimiento_orden_s`; al apagar «puede comandar», sacar una salida o eliminar el origen se llama a `iot_ordenes.cancelar`.

- [ ] **Step 1: tests (fallan)** con el `test_client` (copiar el `Base` de `tests/test_iot_origenes_api.py`; un origen que puede comandar con una salida `pulso`):
  - Flujo feliz de punta a punta: el admin encola → 202; el ESP32 consulta con `X-Token` y recibe la orden; consulta otra vez y recibe `[]`; confirma → `ejecutada`; el listado de orígenes la muestra con su estado y `ultima_orden` en el canal.
  - Sin sesión → 401 y operario → 403 al encolar; la ruta del ESP32 sin token, con token malo, con el de OTRO origen y con un origen inexistente: el mismo 403 idéntico (y cuentan para el bloqueo); un origen Modbus también; **Review Focus 2** el token de A no confirma una orden de B (404 idéntico al de una inexistente).
  - **Review Focus 5**: dos encolados seguidos del mismo canal → 202 y 409 (mensaje que nombra la orden viva).
  - **Review Focus 3**: confirmación con cuerpo `[1]`, `"x"`, `{"error": 5}`, de 70.000 bytes (413), orden de otro origen, repetida; ninguna da 500.
  - **Review Focus 6**: apagar «puede comandar», sacar la salida y eliminar el origen cancelan las órdenes vivas (la consulta siguiente no las trae; `listar` las muestra `vencida` con su motivo); `puede_comandar` en un origen ChirpStack → 400.
  - Una orden que pasó `vence`: la consulta no la entrega y el listado la marca `vencida`.
  - Los tests de la etapa 3 (`test_iot_origenes_api`, `test_iot_origenes_modbus_api`) siguen verdes: `api_iot_ingreso` se comporta igual tras el helper.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar.** **Step 4: verde + `tests.test_iot_origenes_api`, `tests.test_iot_origenes_modbus_api`, `tests.test_ups_api`.**
- [ ] **Step 5:** `git add app.py tests/test_iot_ordenes_api.py`.

---

### Task 4: pantalla, documentación y prueba con un «ESP32» simulado

**Files:** Modify `templates/index.html`, `CLAUDE.md`, `INSTALL.md`, `docs/iot-ingreso-http.md`.

**Interfaces:** consume los endpoints de T3. La tarjeta `#config-iot-origenes` (equipos HTTP) suma el bloque de comandos.

- [ ] **Step 1: la tarjeta.** En un equipo HTTP formato genérico: casilla «Puede recibir órdenes», que al tildarse pide **«Vence a los (segundos)»** (obligatorio, sin valor inicial; ayuda: tiene que ser mayor que cada cuánto consulta tu ESP32) y habilita agregar canales **Salida** (nombre, modo pulso/sostenida, pulso en segundos). Por salida, botones **«Pulso»** o **«Prender» / «Apagar»** (habilitados solo si el equipo está guardado y sin cambios), con `confirm` («Esto activa un equipo real. ¿Seguir?»); y el texto de su **última orden** («última orden: pulso, ejecutada el lunes 23/11/2026 10:15» o «pendiente», «vencida», «sin confirmar», «con error: …») **sin** decir que el relé está prendido/apagado (LactIA no lo sabe). Debajo, la tabla de las últimas órdenes con estado y fecha. Ayuda: el ESP32 tiene que apagar sus salidas al arrancar y consultar sus órdenes cada tanto; una orden que se entregó y no se confirmó NO se reenvía. Reglas del archivo: nada de `{{`, `{%` ni `{#` en template literals de JS (Jinja); datos en atributos `data-`; todo por `escHtml`; los números como texto, que el servidor rechace los malos.
- [ ] **Step 2: `docs/iot-ingreso-http.md`** — sección «Recibir órdenes (salidas)»: las dos rutas con `curl`, la forma de una orden, qué es cada estado, el vencimiento, «apagá tus salidas al arrancar», «consultá más seguido que el vencimiento», y una **referencia mínima de firmware** (el bucle consultar → ejecutar → confirmar, en pseudocódigo corto). **`CLAUDE.md`** — «Comandar un ESP32 (etapa 4)»: diseño, por qué la tabla aparte, límites (sostenida sin tope ni estado real, un solo canal vivo, ChirpStack no) y lo no probado con hardware. **`INSTALL.md`** — no requiere reiniciar `iot_lavado.py` (las órdenes viven en la web); reiniciar el servidor web.
- [ ] **Step 3: verificar con un ESP32 simulado.** Servidor de desarrollo aislado (carpeta temporal y los `_RUTA` parcheados, como en las etapas 2 y 3) y un script que haga de firmware: consulta cada 2 s con el token, «ejecuta» (imprime) y confirma. Probar en el navegador: tildar «puede recibir órdenes», cargar el vencimiento, agregar una salida pulso y una sostenida, apretar «Pulso» y ver `pendiente` → `entregada` → `ejecutada`; apagar el script, apretar otra vez y esperar el vencimiento (poniendo uno de 10 s) para ver `vencida`; frenar el script entre «entregar» y «confirmar» para ver `sin_confirmar`; dos clics seguidos (el segundo rechazado). Consola sin errores y una captura de la tarjeta.
- [ ] **Step 4: suites completas** (las de las etapas 1 a 3 y `test_iot_origenes_comando`, `test_iot_ordenes`, `test_iot_ordenes_api`) y `git add` de lo tocado. No commitear sin pedido.

---

## Preguntas abiertas para el usuario (cambian lo que se construye)

1. **¿Salidas `sostenida` (prender/apagar) o solo pulsos?** La spec incluye las dos. Una sostenida deja un relé encendido en un equipo que LactIA no ve: si el ESP32 se reinicia o se cae la red, el estado real lo sabe solo el ESP32, y **no hay tope de tiempo encendida** (el mismo problema ya anotado para las sostenidas por voz). Recomiendo construir las dos pero que tu firmware apague todo al arrancar; si preferís empezar más seguro, se deja solo `pulso` y la sostenida va después.
2. **Tope del pulso:** el plan pone 60 s como tope técnico (un error de tipeo no puede dejar una bomba una hora). Si alguna salida necesita pulsos más largos, usá «sostenida», o decime otro tope.
3. **El vencimiento es obligatorio y sin defecto**, como la ventana: lo tenés que cargar vos (cada cuánto consulta tu ESP32).
4. **Tabla aparte en vez de `comandos_actuador`** (ver arriba): decisión mía por seguridad con un `iot_lavado.py` sin reiniciar; no cambia nada de lo que ves.
5. **La pantalla táctil ESP32 sigue mostrando solo las 8 salidas del M300**: las salidas de un ESP32 se comandan desde la web (su firmware tiene 16 tarjetas fijas).

## Self-review

- Cobertura de la spec §5: `GET …/ordenes` y `POST …/ok` con token → T3; botón que encola → T3/T4; vencimiento obligatorio sin defecto → T1/T2; estados pendiente/ejecutada/error/vencida y «nunca queda enviada» → T2 (`sin_confirmar`); `puede_comandar` y ChirpStack de solo lectura → T1. Cambio explícito: tabla aparte. Fuera a propósito: comandar salidas de un equipo Modbus adicional (etapa aparte), la pantalla ESP32 y que el lavado/voz usen estas salidas (no-objetivo de la spec).
- Tipos y nombres cruzados verificados: `crear/entregar/confirmar/listar/cancelar/ultima_por_canal` (T2) son lo que llama T3; los campos de canal y origen de T1 son lo que lee T2 (`modo`, `pulso_s`, `vencimiento_orden_s`).
- Proporción: ~190 líneas con firmas y casos, sin cuerpos de función.
