# Orígenes de datos — Etapa 3: origen HTTP de solo lectura (ESP32 / ChirpStack) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que un ESP32 (o un servidor LoRaWAN como ChirpStack) mande lecturas por HTTP a LactIA y aparezcan en el Monitoreo IoT como cualquier sensor, con su historial, colores y avisos, sin tocar el equipo Modbus.

**Architecture:** un módulo nuevo `iot_origenes.py` guarda los orígenes HTTP (`iot_origenes.json`, fuera de git, con el token como hash) y sus canales; `iot_ingreso.py` recibe un cuerpo ya autenticado y escribe en las MISMAS tablas de `iot_sensores.db` que usa `iot_lavado.py` (`digitales_estado`, `eventos_di`, `lecturas_sensor`), así que el panel, el historial y los avisos no cambian. `POST /api/iot/ingreso/<id>` es público y se defiende solo con el token. La ventana de «sin datos» pasa a ser por origen.

**Tech Stack:** Python 3.12, Flask, SQLite, `unittest`. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-11-23-iot-origenes-design.md` (secciones 1 a 4 y 6; la 5, comandar, es la etapa 4).

**Correr tests** (uno por módulo; `discover` no sirve, ver CLAUDE.md):
`C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes -v`

## Global Constraints

- Sin `iot_origenes.json`, TODO se comporta como hoy: `iot_sensores.cargar()`, `lecturas_actuales()` y las llamadas Modbus no cambian (lo fijan las suites ya existentes, que tienen que seguir verdes).
- Las claves de los canales del equipo principal no se tocan. Las de un origen son `<id_origen>.<clave_del_canal>`; el `id` no cambia nunca y no puede contener `.` ni ser `principal`.
- El token: `secrets.token_urlsafe(32)`, se muestra UNA vez al crearlo o regenerarlo, en disco solo su SHA-256, comparación con `hmac.compare_digest`, token incorrecto u origen inexistente = el mismo `403` sin más datos, nunca se escribe en un log. Cabecera `X-Token`.
- **Sin valores inventados**: `ventana_lectura_min` de un origen nuevo es OBLIGATORIA (entero ≥ 1, sin defecto); el principal conserva 30 (`iot_monitoreo.VENTANA_LECTURA_RECIENTE_MIN`). `factor` 1 y `offset` 0 son la identidad, no un umbral.
- Topes técnicos (no del tambo): cuerpo de ingreso ≤ 65.536 bytes (413), ≤ 32 canales por origen, ≤ 20 orígenes, clave de canal `^[A-Za-z0-9_-]{1,40}$`.
- Solo entradas: un canal con grupo `salida` se rechaza con «los orígenes HTTP todavía no manejan salidas».
- Textos de la app: fechas concretas con día de la semana (nunca «ayer»); nombres de rodeo de DelPro.
- No hacer commit ni push sin que el usuario lo pida. Nunca borrar archivos (dudosos a `porlasdudas`).

## Review Focus

Entradas o condiciones que la spec no nombra y que más probablemente muerden; cada una tiene su test en la tarea que dice.

1. Token malo, origen inexistente y origen sin token comparan igual y contestan lo mismo; un cliente con token malo repetido se bloquea por IP (T4).
2. Lecturas basura: `NaN`, `Infinity`, texto, `true` en un canal analógico, `"1"` en uno digital, claves sin declarar: nada se guarda ni rompe (T2).
3. `hora` inválida, sin zona, con zona, en el futuro, o anterior a lo ya guardado de una entrada digital: un reloj roto del ESP32 no puede dejar el «último estado» clavado en el futuro (T2).
4. Dos POST a la vez con el mismo cambio de estado: un solo `eventos_di` (T2).
5. Sacar un canal u origen que un sensor está usando, o perder `iot_origenes.json`: el sensor NO desaparece en silencio del panel (T1, T3).
6. Un nodo que manda cada 60 min con ventana de 120 no aparece mudo, y los sensores del M300 siguen mudos a los 30 (T3).
7. Cuerpo enorme, no-JSON, `lecturas` que no es un objeto: 4xx, nunca 500, y el error queda visible en el panel (T4).

## Estructura de archivos

- Crear `iot_origenes.py` — orígenes, canales, token, búsquedas. Importa solo `iot_canales` (no puede importar `iot_sensores`: este lo importa a él).
- Crear `iot_ingreso.py` — `recibir(...)`, estado por origen. Importa `iot_lavado` (para `_conectar_db`, `RUTA_DB`), `iot_origenes`, `iot_sensores`.
- Modificar `iot_sensores.py` (entradas de origen), `iot_monitoreo.py` (ventana por origen), `app.py` (rutas), `templates/index.html` (tarjeta), `.gitignore`, `CLAUDE.md`, `INSTALL.md`.
- Crear `docs/iot-ingreso-http.md` (protocolo y ejemplo `curl`).
- Tests: `tests/test_iot_origenes.py`, `tests/test_iot_ingreso.py`, `tests/test_iot_origenes_integracion.py`, `tests/test_iot_origenes_api.py`.

---

### Task 1: `iot_origenes.py` — orígenes, canales y token

**Files:** Create `iot_origenes.py`, `tests/test_iot_origenes.py`; Modify `.gitignore` (`iot_origenes.json`).

**Interfaces:**
- Produces (todo sin importar otros módulos del proyecto salvo `iot_canales`):
  - `_RUTA` (patcheable en tests), `PRINCIPAL_ID = "principal"`, `FORMATOS = ("generico", "chirpstack")`, `GRUPOS = ("entrada_digital", "entrada_analogica")`.
  - `cargar() -> list[dict]`: orígenes guardados. Sin archivo o roto → `[]` (un origen inválido del archivo se saltea). Cada uno: `{id, nombre, tipo: "http", formato, ventana_lectura_min, token_hash, creado, canales: [{clave, grupo, nombre, factor, offset, sensor}]}`.
  - `obtener(id) -> dict | None`.
  - `crear(nombre, formato, ventana_lectura_min) -> (origen, token)`. Genera el `id` desde el nombre (minúsculas sin tildes, `[a-z0-9_]`, ≤ 24; si ya existe o es `principal`, sufijo `_2`…).
  - `guardar_origen(id, nombre, formato, ventana_lectura_min, canales, *, sensores_analogicos: dict, sensores_ocupados: set, entradas_en_uso: set) -> None`. `sensores_analogicos` es `clave -> (min, max)` del catálogo; `sensores_ocupados` son los sensores ya asignados a una entrada del M300 (`iot_analogicas`); `entradas_en_uso` son las claves globales de origen que un sensor digital tiene asignadas. Valida TODO antes de escribir; escritura atómica (tempfile + `os.replace`, lock).
  - `regenerar_token(id) -> str`; `eliminar(id, entradas_en_uso: set) -> None`.
  - `token_valido(id, candidato) -> bool`: siempre hace la comparación de hashes, también con un origen inexistente (contra un hash de relleno).
  - `canal_global(id, clave) -> str`; `entradas_digitales() -> tuple[str]` (claves globales de todos los canales digitales); `sensores_asignados() -> set[str]` (sensores analógicos asignados en canales de origen).
  - `ventanas() -> {"entradas": {clave_global: min}, "sensores": {sensor: min}}` y `VENTANA_PRINCIPAL_MIN = 30`.

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_origenes.py`, con `_RUTA` en un directorio temporal:
  - `crear` devuelve un token de ≥ 43 caracteres; el archivo NO contiene el token (solo su SHA-256); `token_valido` acepta el token y rechaza uno distinto, vacío y `None`; `token_valido("no_existe", x)` es `False` sin excepción.
  - `crear("ESP32 del corral", "generico", 120)` da `id == "esp32_del_corral"`; un segundo igual da `esp32_del_corral_2`; el nombre «Principal» da un id distinto de `principal`.
  - `crear` sin `ventana_lectura_min`, con `0`, con `-5`, con `1.5` o con texto → `ValueError`. Formato desconocido → `ValueError`. El origen 21 → `ValueError`.
  - `guardar_origen`: clave de canal con `.` o con espacio → `ValueError`; dos canales con la misma clave → error; grupo `salida` → error con el texto «todavía no manejan salidas»; más de 32 canales → error; `sensor` en un canal digital → error; `sensor` inexistente en el catálogo → error; sensor que ya está en `sensores_ocupados` → error; el mismo sensor en dos canales de origen → error; `factor` 0 o no numérico → error; `offset`/`factor` vacíos → 1 y 0.
  - Sacar un canal digital que está en `entradas_en_uso` → `ValueError` que nombra la entrada; `eliminar` de un origen cuyo canal está en uso → `ValueError`; un guardado inválido deja el archivo igual que antes (byte a byte).
  - `entradas_digitales()` devuelve `("esp32_del_corral.puerta",)`; `sensores_asignados()` y `ventanas()` reflejan lo guardado; sin archivo, `ventanas()` es `{"entradas": {}, "sensores": {}}`.
  - Archivo roto (basura) → `cargar() == []`.
- [ ] **Step 2: correr y ver que fallan** (`ModuleNotFoundError`).
- [ ] **Step 3: implementar `iot_origenes.py`** con las firmas de arriba. El patrón de guardado atómico y lock es el de `iot_sensores.guardar`.
- [ ] **Step 4: correr hasta verde**; **Step 5:** `git add iot_origenes.py tests/test_iot_origenes.py .gitignore`.

---

### Task 2: `iot_ingreso.py` — recibir lecturas

**Files:** Create `iot_ingreso.py`, `tests/test_iot_ingreso.py`.

**Interfaces:**
- Consumes: `iot_origenes.obtener/canal_global`, `iot_sensores.digitales()/analogicos()`, `iot_lavado._conectar_db`, `iot_lavado.INTERVALO_GUARDADO_ANALOGICAS_S`.
- Produces:
  - `RUTA_DB = iot_lavado.RUTA_DB` (patcheable); `class IngresoError(ValueError)`.
  - `recibir(origen_id: str, cuerpo: dict, evento: str | None = None, ahora: datetime | None = None) -> dict` → `{"recibidas": n, "ignorados": m}`. Solo la usan rutas ya autenticadas. Lanza `IngresoError` por un cuerpo mal formado (y deja el error en el estado del origen).
  - `estado(origen_id) -> dict`: `{ultima_recepcion, recibidas, ignorados, ultimo_error, error_fecha}` (todo `None` si nunca llegó nada) y `ultimo_de_canal(origen_id, clave) -> {valor, fecha_hora} | None`.
  - Tablas que crea si faltan (junto a las de `_conectar_db`): `origenes_estado(origen PK, ultima_recepcion, recibidas, ignorados, ultimo_error, error_fecha)` y `origenes_ultimo(canal PK, fecha_hora, valor REAL)`.

**Reglas de `recibir`** (los tests las fijan):
- `generico`: `cuerpo["lecturas"]` tiene que ser un objeto `{clave_de_canal: valor}`; si no, `IngresoError`. `hora` opcional.
- `chirpstack`: solo se procesa `evento == "up"` (o sin `evento` pero con `deviceInfo`); cualquier otro evento (`join`, `status`…) devuelve `{"recibidas": 0, "ignorados": 0}` sin error. Las lecturas salen de los valores de primer nivel de `cuerpo["object"]` (números y booleanos; lo anidado se ignora); la hora, de `cuerpo["time"]`. Un `up` sin `object` es un error legible en el estado: «el nodo no manda el payload decodificado (falta el códec en ChirpStack)».
- Un canal no declarado en el origen → `ignorados += 1`. Un valor que no sirve → `ignorados += 1` y no se guarda: analógico = número finito (no `bool`, no `NaN`/`inf`, no texto); digital = `bool` o número (≠ 0 es activo), nunca texto.
- **Analógico**: `valor = crudo × factor + offset`. Siempre se anota en `origenes_ultimo`. Si el canal tiene `sensor`, se inserta en `lecturas_sensor` salvo que ya haya una fila de ese sensor a menos de `INTERVALO_GUARDADO_ANALOGICAS_S` de la hora de la lectura (en cualquier sentido: no depende del orden de llegada).
- **Digital**: se aplica el `invertido` del sensor del catálogo que tenga esa entrada (como hace `iot_lavado.entradas_invertidas`). Se escribe `digitales_estado` (latido) y, si el estado difiere del que había, una fila en `eventos_di`. Si la hora de la lectura es anterior a la del `digitales_estado` actual de ese canal, se ignora (cuenta en `ignorados`).
- **Hora**: ISO 8601; con zona → se pasa a hora local sin zona (todo el código usa hora local naíve, `isoformat(timespec="seconds")`); inválida → la del servidor; **en el futuro respecto del servidor → la del servidor**; anterior → se respeta (un ESP32 que reenvía lo que guardó en una tarjeta).
- Todo el bloque de escritura va bajo un `threading.Lock` del módulo.
- `ultima_recepcion` y los contadores se actualizan en cada llamada con origen válido.

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_ingreso.py` (base con `iot_origenes._RUTA`, `iot_sensores._RUTA` y `iot_ingreso.RUTA_DB` temporales; un origen `esp32` con `temp` analógico → sensor `temp_corral`, `puerta` digital atado a un sensor alarma). Un test por regla de arriba, además de:
  - Review Focus 2: `NaN`, `Infinity`, `"21"`, `True` en `temp`; `"1"` y `"on"` en `puerta` → `ignorados == 1` cada uno y ninguna fila nueva.
  - Review Focus 3: hora futura (+1 día) → se guarda con la hora del servidor; hora `"basura"` → hora del servidor; hora `…-03:00` → convertida a local; lectura digital con hora anterior a la ya guardada → ignorada y el estado actual no cambia.
  - Review Focus 4: dos hilos llamando a `recibir` con `puerta: 1` a la vez → exactamente 1 fila de `eventos_di` en `1`.
  - Dos lecturas de `temp` con 10 s de diferencia → una sola fila en `lecturas_sensor`; con 31 s → dos; en orden inverso (la vieja llega después) → también una.
  - ChirpStack: un `up` con `object {"temperature": 21.4, "humidity": 55}` y canales `temperature`/`humidity` declarados guarda ambos con la hora de `time`; `join` → 0 y 0 sin error; `up` sin `object` → `IngresoError` y `estado()["ultimo_error"]` con el texto de arriba.
  - `estado()` tras una llamada buena tiene `ultima_recepcion` y `recibidas`; tras una mala, `ultimo_error` y `error_fecha`.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar `iot_ingreso.py`.** **Step 4: verde.** **Step 5:** `git add iot_ingreso.py tests/test_iot_ingreso.py`.

---

### Task 3: que el catálogo, el panel y la ventana vean los orígenes

**Files:** Modify `iot_sensores.py`, `iot_monitoreo.py`, `app.py` (solo `api_iot_analogicas_guardar`, `api_iot_sensores_guardar`); Create `tests/test_iot_origenes_integracion.py`.

**Interfaces:**
- Consumes: `iot_origenes.entradas_digitales()`, `.sensores_asignados()`, `.ventanas()`.
- Produces:
  - `iot_sensores.normalizar(s, estricto=True)`: acepta como `entrada` las del M300 **y** `iot_origenes.entradas_digitales()`. `cargar()` llama con `estricto=False`: una entrada con forma de origen (`contiene "."`) se conserva aunque su origen ya no exista (el sensor queda «sin datos», visible), en vez de saltearse. `guardar()` es estricto.
  - `iot_monitoreo._sin_datos_si_esta_mudo(sensor, con_limites, ahora, ventana_min=None)`: `None` = `VENTANA_LECTURA_RECIENTE_MIN`. `lecturas_actuales` pide `iot_origenes.ventanas()` UNA vez y usa `["sensores"]` para los analógicos y `["entradas"]` para los digitales; el ITH queda con la ventana de siempre.
  - Un sensor analógico asignado a un canal de origen cuenta como «en uso»: no se puede borrar del catálogo (`en_uso` de `api_iot_sensores_guardar` suma `iot_origenes.sensores_asignados()`) ni asignar a una entrada del M300 (`api_iot_analogicas_guardar` le saca esos sensores a `sensores_asignables()` para que `iot_analogicas.guardar` los rechace con su mensaje de siempre).

- [ ] **Step 1: tests (fallan)**:
  - Un sensor digital con `entrada="esp32.puerta"` se guarda si el origen existe y falla si no; con el origen borrado a mano del JSON, `iot_sensores.cargar()` lo sigue devolviendo (Review Focus 5) y `lecturas_actuales` lo marca `sin_datos`.
  - Dos sensores en la misma entrada de origen → el error de siempre.
  - Review Focus 6: con la ventana del origen en 120, un `digitales_estado` de `esp32.puerta` de hace 90 min NO está mudo y uno de hace 130 sí; un `lecturas_sensor` del sensor `temp_corral` asignado en el origen de hace 90 min no está mudo; el sensor `temp_leche` (M300) de hace 31 min sigue mudo.
  - Sin `iot_origenes.json`, `lecturas_actuales` devuelve lo mismo que antes (comparar contra una corrida con `iot_origenes.ventanas` parcheado a vacío).
  - Un sensor asignado en un canal de origen no se puede asignar a `ai_1` ni borrar del catálogo.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar.**
- [ ] **Step 4: verde, y además las suites existentes** `tests.test_iot_sensores`, `test_iot_digitales`, `test_iot_alarmas_tablero`, `test_iot_avisos`, `test_iot_estados`, `test_iot_analogicas`, `test_iot_vumetros`, `test_iot_mapa`, `test_iot_lavado_mapa`, `test_iot_canales`.
- [ ] **Step 5:** `git add iot_sensores.py iot_monitoreo.py app.py tests/test_iot_origenes_integracion.py`.

---

### Task 4: rutas HTTP (ingreso público y administración)

**Files:** Modify `app.py`; Create `tests/test_iot_origenes_api.py`.

**Interfaces:**
- Produces:
  - `POST /api/iot/ingreso/<id_origen>` — **público** (en `_requerir_login`: `or request.path.startswith("/api/iot/ingreso/")`, más una prueba de que `/api/iot/ingresoX` NO queda público). Orden: (1) `auth.login_bloqueado("iot:" + auth.ip_cliente())` → 429; (2) token de la cabecera `X-Token` (para `formato == "chirpstack"` también `?token=`, ver Pregunta abierta 1); (3) `iot_origenes.token_valido` falso → `auth.registrar_intento_fallido(...)` y `403 {"error": "Token inválido."}` idéntico para origen inexistente; correcto → `auth.limpiar_intentos`; (4) `request.max_content_length = 65536` y `content_length` mayor → 413; (5) `request.get_json(silent=True)` que no sea objeto → se anota el error en `iot_ingreso` y `400`; (6) `iot_ingreso.recibir(id, cuerpo, request.args.get("event"))` → `200 {"ok": true, "recibidas": n, "ignorados": m}`; `IngresoError` → `400 {"error": str}`. El token no se loguea ni se devuelve.
  - Administración (todas `@auth.requiere_rol("admin")`): `GET /api/iot/origenes` → `{"principal": {id, nombre, tipo: "modbus_tcp", ventana_lectura_min: 30}, "origenes": [{id, nombre, tipo, formato, ventana_lectura_min, url_ingreso: "/api/iot/ingreso/<id>", canales: [{…, "ultimo": {valor, fecha_hora} | null}], "estado": {…}}], "sensores_analogicos": [{clave, label, unidad}]}` (nunca `token_hash`); `POST /api/iot/origenes` (crear → `{"origen": …, "token": "…"}`); `POST /api/iot/origenes/<id>` (guardar nombre, formato, ventana y canales); `POST /api/iot/origenes/<id>/token` (regenerar → `{"token": "…"}`); `DELETE /api/iot/origenes/<id>`. `ValueError` → `400 {"error": …}`. Cada una arma `sensores_analogicos`, `sensores_ocupados` (los de `iot_analogicas.cargar()`) y `entradas_en_uso` (las `entrada` de `iot_sensores.digitales()`) para pasárselos a `iot_origenes`.
  - `GET /api/iot/sensores` suma a `entradas` las de los orígenes: `{"clave": "esp32.puerta", "numero": null, "label": "<Origen> · <nombre>", "origen": "esp32"}`.

- [ ] **Step 1: tests (fallan)** con el `test_client` (patrón de `tests/test_ups_api.py`: `auth._intentos_fallidos.clear()`, cliente sin sesión y cliente admin):
  - Ingreso feliz con `X-Token` → 200 y la lectura se ve en `lecturas_actuales`.
  - Review Focus 1: token malo, sin token y origen inexistente → los tres 403 con EL MISMO cuerpo; tras 10 intentos malos seguidos desde la misma IP la respuesta pasa a 429 (usar el límite real de `auth`, leerlo del módulo) y un login de usuario no se ve afectado.
  - Review Focus 7: cuerpo de 70.000 bytes → 413; `lecturas` como lista, texto o `null`; cuerpo `[]`; cuerpo que no es JSON → 400, nunca 500, y `GET /api/iot/origenes` muestra el `ultimo_error`.
  - Sin sesión, `GET /api/iot/origenes` → 401; con sesión de operario → 403; el JSON de administración no contiene `token_hash` ni el token.
  - Crear devuelve el token una vez; `GET` posterior no lo trae; regenerar invalida el anterior (403 con el viejo, 200 con el nuevo).
  - `eliminar` con un sensor usando un canal → 400 con el nombre de la entrada.
  - `?token=` solo funciona en un origen `chirpstack` (en uno `generico` → 403).
  - Con `event=join` en un origen `chirpstack` → 200 con `recibidas: 0`.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar.** **Step 4: verde + `tests.test_ups_api` + `tests.test_iot_origenes_integracion`.**
- [ ] **Step 5:** `git add app.py tests/test_iot_origenes_api.py`.

---

### Task 5: pantalla, documentación y prueba en el navegador

**Files:** Modify `templates/index.html`, `CLAUDE.md`, `INSTALL.md`; Create `docs/iot-ingreso-http.md`.

**Interfaces:** consume los endpoints de la Tarea 4. Tarjeta nueva `#config-iot-origenes` en ⚙ Configuración › 🔌 Entradas/Salidas, entre «Dónde está cada canal en el equipo (mapa Modbus)» y «Sensores del tambo»; se carga dentro de `cargarConfiguracion` junto a `cargarMapaModbus()`.

- [ ] **Step 1: la tarjeta.** Una fila por origen con: nombre, formato, ventana (minutos), la URL completa (`location.origin` + `url_ingreso`, copiable) y un ejemplo `curl` de solo lectura con `<TOKEN>` literal; el estado («Última recepción: lunes 23/11/2026 10:15 · 14 recibidas · 0 ignoradas», o «Todavía no llegó nada», o el último error con su fecha; reutilizar el helper de fecha con día de la semana que ya usa `index.html`); la tabla de canales (clave, tipo, nombre, factor, offset, sensor para los analógicos, último valor recibido) con alta y baja de filas; botones «Guardar origen», «Nuevo token» y «Eliminar origen» (con confirmación). «Agregar origen» pide nombre, formato y ventana (obligatoria, sin valor inicial). El token aparece una sola vez en un recuadro destacado con botón «Copiar» y el aviso «No se vuelve a mostrar». Texto de ayuda: un ESP32 dentro de la red del tambo tiene que usar la IP de la PC, no el nombre público. Reglas del archivo: nada de `{{`, `{%` ni `{#` dentro de template literals de JS (Jinja); los datos de cada fila viajan en atributos `data-` (las claves llevan un `.`: no usarlas en `id` ni en selectores CSS).
- [ ] **Step 2: el editor «Sensores del tambo»**: la lista de entradas digitales agrupa las del M300 y las de cada origen (`<optgroup>` por origen usando el campo `origen`); un sensor analógico asignado en un origen se muestra como «asignado en <origen>».
- [ ] **Step 3: `docs/iot-ingreso-http.md`**: URL, cabecera `X-Token`, cuerpo genérico, ejemplo `curl` que se pueda copiar y probar, respuestas (`200`, `400`, `403`, `413`, `429`), cómo pensar la ventana (la ventana tiene que ser mayor que cada cuánto manda el equipo), y la configuración de la HTTP integration de ChirpStack con las dos advertencias de la Pregunta abierta 1.
- [ ] **Step 4: `CLAUDE.md`** — sección «Orígenes HTTP (etapa 3)» con el diseño, el límite conocido (solo entradas; el ESP32 no se comanda hasta la etapa 4), lo no probado contra un ChirpStack real, y el comando para correr las suites. **`INSTALL.md`** — reiniciar `iot_lavado.py` no es obligatorio para recibir datos (el ingreso lo hace el servidor web) pero sí para que el poller tome el catálogo nuevo; no hacerlo en pleno lavado.
- [ ] **Step 5: verificar en el navegador** (servidor de desarrollo, base temporal si se puede): crear un origen, copiar el token, mandar un `curl` con una temperatura y una puerta, ver que la tarjeta de estado y el último valor se actualizan, asignar el sensor y verlo vivo en Monitoreo IoT; esperar a que pase la ventana (poniendo una de 1 minuto) y ver «Sin datos»; regenerar el token y confirmar que el viejo da 403. Mirar la consola por errores y mirar una captura de la tarjeta.
- [ ] **Step 6: suites completas del IoT** (las de la Tarea 3 más `test_iot_origenes*`, `test_iot_ingreso`, `test_ups_api`) y `git add` de los archivos tocados. No commitear sin pedido.

---

## Preguntas abiertas para el usuario (cambian lo que se construye)

1. **ChirpStack no está confirmado.** La documentación oficial de la HTTP integration no muestra cómo mandar una cabecera propia (`X-Token`) ni lista el campo `object` (payload ya decodificado) en el evento `up`; solo muestra `data` en base64. Por eso el plan acepta `?token=` en la URL SOLO para orígenes `chirpstack` (el token queda en la URL, y un registro intermedio como el de Cloudflare lo vería: conviene que ChirpStack hable con LactIA por la red local) y deja un error legible si falta `object`. Se prueba con un ChirpStack real antes de usarlo; hasta entonces el formato `generico` (el del ESP32) es lo confirmado.
2. **Ventana sin valor por defecto.** Cada origen nuevo exige que cargues cada cuántos minutos debería llegar una lectura (la ventana de «sin datos»): vos sabés cada cuánto manda tu ESP32; un valor mío inventado marcaría mudo un nodo lento o taparía uno caído.
3. **Hora del equipo.** Una hora en el futuro se reemplaza por la del servidor y una pasada se respeta. No hay un máximo de antigüedad: un ESP32 con el reloj roto en el pasado dejaría lecturas con fecha vieja (no rompe el «último valor» analógico porque se ordena por fecha, pero sí aparecerían tarde en el historial).

## Self-review

- Cobertura de la spec: §1 (orígenes, ventana) → T1/T3; §2 (canales `<id>.<clave>`, factor/offset) → T1/T2; §3 (sensores validan contra todos los orígenes, analógico por canal) → T3; §4 (ingreso, token, `ignorados`, formatos) → T2/T4; §6 (pantalla de orígenes) → T5. Fuera de esta etapa a propósito: §5, el mapa por origen Modbus y «Cargar mapa PUSR M300» (etapa 2).
- Tipos y nombres cruzados entre tareas verificados: `guardar_origen(..., sensores_analogicos, sensores_ocupados, entradas_en_uso)` (T1) es lo que arma T4; `ventanas()` (T1) es lo que consume T3; `recibir`/`estado`/`ultimo_de_canal` (T2) son lo que usa T4.
- Proporción: la spec tiene ~140 líneas; el plan, el triple, con las firmas y los tests y casi ningún cuerpo de función.
