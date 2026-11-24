# Orígenes de datos — Etapa 2: más de un equipo Modbus (solo lectura) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el tambo declare desde la web otros equipos Modbus TCP (otro PLC o gateway, con su IP, puerto y mapa) y que sus entradas digitales y analógicas aparezcan en el Monitoreo IoT como cualquier sensor, sin tocar código ni el equipo principal (M300).

**Architecture:** los equipos Modbus nuevos son un segundo `tipo` de origen dentro de `iot_origenes.json` (los HTTP de la etapa 3 siguen igual). `iot_lavado.py` conserva SU cliente del principal tal cual y suma un `ConexionesModbus` que, en cada ciclo y DESPUÉS del principal, lee cada equipo con su propio cliente, con reintento espaciado si está caído. Escribe en las mismas tablas (`digitales_estado`, `eventos_di`, `lecturas_sensor`), así que el panel, el historial, la ventana de «Sin datos» por origen y los avisos no cambian.

**Tech Stack:** Python 3.12, Flask, SQLite, pymodbus 3.14 (ya instalado), `unittest`. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-11-23-iot-origenes-design.md` (secciones 1, 2, 3, 6 y 7; la 4 es la etapa 3 ya hecha, la 5 es la etapa 4). Etapas 1 y 3 ya están en `master`: leer `iot_mapa.py` (mapa del principal) e `iot_origenes.py` / `iot_ingreso.py` (orígenes HTTP) antes de empezar.

**Correr tests** (uno por módulo; `discover` no sirve, ver CLAUDE.md):
`C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_modbus -v`

## Global Constraints

- Sin ningún origen Modbus guardado, el gateway hace EXACTAMENTE las llamadas de hoy al principal (misma función, dirección y unit ID): lo fijan `tests.test_iot_lavado_mapa` (`TestSinMapa`) y las de la etapa 3, que tienen que seguir verdes sin tocarlas.
- Un equipo Modbus nuevo es **solo de lectura** en esta etapa: canales `entrada_digital` y `entrada_analogica`; un canal `salida` se rechaza con «los equipos Modbus adicionales todavía no manejan salidas». El lavado automático, la voz y la pantalla ESP32 siguen siendo del principal.
- Claves de canal: `<id_origen>.<clave>` (clave `^[A-Za-z0-9_-]{1,40}$`); el `id` no cambia nunca, sin `.`, distinto de `principal`. Los canales del principal no se tocan.
- **Sin valores inventados**: `ventana_lectura_min` (entero ≥ 1), `host` y la `direccion` de cada canal son obligatorios y sin defecto; el `tipo_dato` de un canal analógico también (depende del equipo). Solo se aceptan como defectos los de protocolo que el código ya usa: puerto 502 (`iot_conexion.PUERTO_DEFECTO`) y unit ID 1 (`iot_mapa.UNIT_ID_DEFECTO`). `factor` 1 y `offset` 0 son la identidad.
- Topes técnicos (no del tambo): ≤ 20 orígenes en total (HTTP + Modbus), ≤ 32 canales por origen, host `^[A-Za-z0-9._-]{1,253}$` (sin espacios, sin `://`), puerto 1–65535, unit ID 1–247, función de un canal analógico `input_register` o `holding_register`, `tipo_dato` en `uint16`, `int16`, `float32_ab_cd` (dos registros, el alto primero: el formato de los AI del M300 que ya decodifica `iot_lavado._decimal_32_bits`).
- Direcciones en notación Modicon (10001, 30001, 40001) se aceptan y se convierten como en la etapa 1 (reusar `iot_mapa._direccion`, `_unit_id`, `FUNCIONES_ENTRADA`).
- Un equipo caído no puede frenar al principal: conexión con `timeout` corto y reintento solo cada `REINTENTO_ORIGEN_CAIDO_S` (tope técnico, no umbral del tambo).
- Textos de la app: fechas concretas con día de la semana (nunca «ayer»); nombres de rodeo de DelPro. No hacer commit ni push sin que el usuario lo pida. Nunca borrar archivos (dudosos a `porlasdudas`). Pruebas con un cliente Modbus de mentira; ningún test toca la red.

## Review Focus

Entradas o condiciones que la spec no nombra y que más probablemente muerden; cada una tiene su test en la tarea que dice.

1. Un equipo apagado, sin red o que no responde NO frena el ciclo del principal ni de los demás equipos, no se reintenta en cada ciclo de 3 s, se reconecta solo al volver, y sus sensores pasan a «Sin datos» recién pasada su ventana (T2).
2. Una dirección que el equipo rechaza («Illegal Data Address») en un canal no tumba a los otros canales del mismo equipo, y nunca se toma por «apagado» ni por 0 (T2).
3. Registros: `int16` negativo, `uint16` en 65535, `float32` con NaN/infinito/basura, y una respuesta con MENOS registros que los pedidos: no se guarda nada (T2).
4. Dos orígenes con el mismo host:puerto (también contra el principal de `iot_conexion`), o dos canales del mismo equipo en el mismo punto (unit ID + función + dirección): se rechaza al guardar con un mensaje que nombra los dos (T1).
5. Host con espacios o `http://`, vacío, de 300 letras; puerto 0 o 70000; unit ID 0 o 248; dirección negativa o 70000; ventana ausente: 400, nada se guarda (T1, T3).
6. Cambiar host/puerto, sacar un canal o borrar el equipo mientras el gateway corre: lo toma en el ciclo siguiente sin reiniciar, cierra el cliente viejo, y un canal en uso por un sensor no se puede sacar (T1, T2).
7. Un POST de ingreso HTTP (con token) dirigido a un equipo Modbus contesta el mismo 403 que un origen inexistente, y «Nuevo token» sobre uno es un 400 legible (T3).
8. Un `iot_origenes.json` con equipos HTTP y Modbus mezclados: lo de la etapa 3 (ventanas, entradas, sensores asignados, token) no cambia (T1, T3).

## Estructura de archivos

- Modificar `iot_origenes.py` (tipo `modbus_tcp`; sigue sin importar `iot_sensores`/`iot_monitoreo`/`iot_lavado`; sí puede importar `iot_mapa` e `iot_conexion`, que no lo importan a él: el segundo da el host y puerto del principal para rechazar un equipo repetido).
- Modificar `iot_lavado.py` (`ConexionesModbus`, lectura analógica, tablas de estado) e `iot_ingreso.py` (usa lo que se mueve a `iot_lavado`).
- Modificar `app.py` (rutas de orígenes), `templates/index.html` (la tarjeta de la etapa 3 aprende el tipo), `CLAUDE.md`, `INSTALL.md`, `docs/iot-ingreso-http.md` (una línea que remite al otro tipo).
- Tests nuevos: `tests/test_iot_origenes_modbus.py` (T1), `tests/test_iot_modbus_poller.py` (T2), `tests/test_iot_origenes_modbus_api.py` (T3).

---

### Task 1: `iot_origenes.py` aprende el tipo `modbus_tcp`

**Files:** Modify `iot_origenes.py`; Create `tests/test_iot_origenes_modbus.py`.

**Interfaces:**
- Produces:
  - Constantes `TIPOS = ("http", "modbus_tcp")`, `FUNCIONES_ANALOGICA = ("input_register", "holding_register")`, `TIPOS_DATO = ("uint16", "int16", "float32_ab_cd")`, `PUERTO_DEFECTO = 502`, `UNIT_ID_DEFECTO = 1`.
  - `crear_modbus(nombre, host, port, unit_id, ventana_lectura_min) -> origen` (sin token). `port` y `unit_id` vacíos/`None` = los defectos de protocolo.
  - `guardar_modbus(id, nombre, host, port, unit_id, ventana_lectura_min, canales, *, sensores_analogicos, sensores_ocupados, entradas_en_uso) -> None`. Mismo contrato que `guardar_origen` (valida TODO antes de escribir; un canal digital en uso no se puede sacar o cambiar de grupo).
  - Un origen Modbus de `cargar()/obtener()` es `{id, nombre, tipo: "modbus_tcp", host, port, unit_id, ventana_lectura_min, creado, canales}` (sin `token_hash` ni `formato`); cada canal: `{clave, grupo, nombre, funcion, direccion, unit_id (None = el del equipo), tipo_dato (solo analógico), factor, offset, invertido (solo digital), sensor}`.
  - `guardar_origen`, `regenerar_token` y `token_valido` sobre un origen Modbus: los dos primeros `ValueError` («es un equipo Modbus…»), `token_valido` `False` (haciendo igual la comparación de relleno). `guardar_modbus` sobre uno HTTP: `ValueError`.
  - `entradas_digitales()`, `sensores_asignados()`, `ventanas()` incluyen a los dos tipos; `eliminar` aplica la misma regla de «en uso». `MAX_ORIGENES` cuenta los dos tipos juntos.

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_origenes_modbus.py`, con `_RUTA` en un directorio temporal y `iot_conexion._RUTA` apuntando a un archivo con el host del principal:
  - Alta feliz: `crear_modbus("PLC sala de máquinas", "192.168.1.60", None, None, 15)` da `id == "plc_sala_de_maquinas"`, `port == 502`, `unit_id == 1`; no hay clave `token_hash`; el archivo es sano.
  - Validaciones de `crear_modbus` (todas `ValueError` y el archivo no cambia): host vacío, con espacios, `http://x`, de 300 letras; puerto `0`, `70000`, `"abc"`, `True`; unit ID `0`, `248`; ventana ausente, `0`, `1.5`, texto. El origen 21 (entre HTTP y Modbus) se rechaza.
  - **Review Focus 4**: dos equipos con el mismo host y puerto → error que nombra los dos; el mismo host con OTRO puerto es válido; el host:puerto del principal (`iot_conexion.config()`) también se rechaza; `guardar_modbus` cambiando el host a uno ya usado, también.
  - Canales digitales válidos en las cuatro funciones; la dirección `10001` con `discrete_input` se guarda como `0`, `40001` con `holding_register` como `0`; `unit_id` del canal vacío queda `None`; `invertido` es bool.
  - Canales analógicos: `funcion` fuera de `FUNCIONES_ANALOGICA` → error; `tipo_dato` ausente o desconocido → error; `factor` 0, texto o `NaN` → error; vacíos = 1 y 0; `sensor` con las mismas reglas de la etapa 3 (catálogo, ocupado por el M300, repetido dentro o entre orígenes, sensor en un canal digital).
  - Canal `salida` → «todavía no manejan salidas». Dirección vacía, negativa o `70000` → error. Dos canales del mismo equipo en el mismo punto (unit ID efectivo, función, dirección) → error que nombra los dos; el mismo punto en OTRO equipo es válido.
  - **Review Focus 6**: sacar un canal digital que está en `entradas_en_uso` o pasarlo a analógico, y `eliminar` con uno en uso → `ValueError` que nombra la entrada; un guardado inválido deja el archivo igual byte a byte.
  - **Review Focus 8**: con un origen HTTP y uno Modbus guardados, `entradas_digitales()`, `sensores_asignados()` y `ventanas()` traen lo de los dos; `token_valido(id_modbus, "lo que sea")` es `False` y `regenerar_token(id_modbus)` / `guardar_origen(id_modbus, …)` dan `ValueError`; `guardar_modbus(id_http, …)` también.
  - Un archivo con un origen Modbus inválido a mano (host roto) se saltea sin tirar a los demás; las suites de la etapa 3 (`tests.test_iot_origenes`) siguen verdes.
- [ ] **Step 2: correr y ver que fallan** (`AttributeError: crear_modbus`).
- [ ] **Step 3: implementar** las firmas de arriba en `iot_origenes.py`. Reusar `_escribir`, `_hash`, `_id_libre`, el reintento de lectura y el apartado de archivo roto de la etapa 3, y `iot_mapa._direccion/_unit_id` para las direcciones. `_origen_del_archivo` ramifica por `tipo` (un registro sin `tipo` es HTTP: los archivos de la etapa 3 no lo traen).
- [ ] **Step 4: correr hasta verde**, y `tests.test_iot_origenes`, `tests.test_iot_mapa`. **Step 5:** `git add iot_origenes.py tests/test_iot_origenes_modbus.py`.

---

### Task 2: `iot_lavado.py` lee los equipos Modbus nuevos

**Files:** Modify `iot_lavado.py`, `iot_ingreso.py`; Create `tests/test_iot_modbus_poller.py`.

**Interfaces:**
- Consumes: `iot_origenes.cargar()` (origen Modbus de T1), `iot_mapa._direccion`.
- Produces (en `iot_lavado`):
  - `_conectar_db` crea también `origenes_estado` y `origenes_ultimo` (hoy las crea `iot_ingreso._conectar`; este pasa a llamar solo a `iot_lavado._conectar_db`) y `anotar_ultimo(con, canal, marca, valor)` (el upsert que no retrocede, hoy `iot_ingreso._anotar_ultimo`; `iot_ingreso` lo usa de acá). Misma esquema, sin migración.
  - `leer_analogica(client, funcion, direccion, unit_id, tipo_dato) -> float | None`: `None` ante error, respuesta corta, excepción (cierra el cliente, como `_leer_estado`), NaN o infinito. `uint16`/`int16` leen 1 registro; `float32_ab_cd` 2.
  - `REINTENTO_ORIGEN_CAIDO_S = 30`, `TIMEOUT_ORIGEN_S = 2`.
  - `class ConexionesModbus`: `__init__(self, fabrica=ModbusTcpClient)`; `ciclo(self, con, origenes: list, ahora=None) -> None`. Mantiene por origen: su cliente, `anteriores`, `ultimo_latido`, `ultimo_guardado`, cuándo reintentar. Cada origen va en su propio `try/except`.
  - En `main()`: `conexiones = ConexionesModbus()` y, en cada vuelta, DESPUÉS de `procesar_comandos_voz`, `conexiones.ciclo(con, [o for o in iot_origenes.cargar() if o["tipo"] == "modbus_tcp"])`.

**Reglas de `ciclo`** (las fijan los tests):
- Un origen que no estaba se conecta; uno que ya no está o cuyo host/puerto cambió cierra su cliente y, si cambió, se reconecta al ciclo siguiente. Se lee TODO lo que el equipo declara (no hay `canales_a_leer`: el tambo ya lo acotó).
- Digital: `_leer_estado(client, direccion, invertido_del_canal != invertido_del_sensor, funcion, unit_id_efectivo)` con `entradas_invertidas()` (igual que el principal); el resultado va a `latir` y `registrar_si_cambio` con la clave global `<id>.<clave>`.
- Analógico: `valor = crudo × factor + offset` (ignorar si no es finito); siempre `anotar_ultimo`; si el canal tiene sensor, `lecturas_sensor` como mucho una por `INTERVALO_GUARDADO_ANALOGICAS_S`.
- Un canal que no responde se saltea sin afectar a los otros. Si el equipo no conecta, o NINGÚN canal respondió, el origen queda «caído»: `ultimo_error` («No se pudo conectar a host:puerto» o «El equipo no respondió a ninguna lectura») y `error_fecha` en `origenes_estado` (solo si el texto cambió; no se reescribe cada 3 s) y no se reintenta hasta pasar `REINTENTO_ORIGEN_CAIDO_S`. Si algún canal respondió: `ultima_recepcion` (a lo sumo cada `INTERVALO_LATIDO_DI_S`) y el reintento vuelve al ritmo normal.

- [ ] **Step 1: tests (fallan)** en `tests/test_iot_modbus_poller.py` con un `ClienteFalso` por dirección (patrón de `tests/test_iot_lavado_mapa.py`) y una `fabrica` que entrega uno por (host, puerto) y cuenta las conexiones; reloj inyectado por `ahora`:
  - Digital de cada una de las cuatro funciones: la llamada Modbus es la esperada (función, dirección, unit ID del canal o del equipo); el estado va a `digitales_estado` y un cambio a `eventos_di`; `invertido` del canal y del sensor se combinan (los dos tildados se anulan).
  - Analógico `uint16` (65535), `int16` (`[0xFFFF]` = −1), `float32_ab_cd` (21.5 en dos registros) con `factor`/`offset`; el sensor recibe una fila y la segunda lectura a 10 s no agrega otra, la de 31 s sí. **Review Focus 3**: float NaN/infinito, respuesta con 1 registro cuando se pidieron 2, error de lectura → no se guarda nada y el canal no pasa a 0.
  - **Review Focus 2**: de tres canales, el del medio contesta error: los otros dos se guardan; el digital con error conserva su estado anterior.
  - **Review Focus 1**: dos equipos, el primero no conecta (la fábrica lanza o `connected` queda `False`) y el segundo sí → el segundo se lee en el mismo ciclo, `origenes_estado` del primero tiene el error con la fecha, y un segundo `ciclo` 3 s después NO intenta conectar de nuevo; pasados 30 s sí, y si ahora responde se guarda su lectura y el reintento vuelve a normal. El principal (`iot_lavado.procesar_entradas` con el mismo ciclo de `main`) no se afecta: sus llamadas son las mismas con y sin equipos caídos.
  - **Review Focus 6**: sin equipos, `ciclo` no abre ningún cliente; un equipo que desaparece del archivo cierra su cliente; uno con host nuevo cierra el viejo y abre uno al nuevo; sacar un canal deja de leerlo en el ciclo siguiente.
  - El estado de un equipo sano: `ultima_recepcion` se actualiza pero no más de una vez cada 30 s; el error no se borra al recuperarse (queda con su fecha).
  - Los tests de `iot_ingreso` (40+) y `test_iot_lavado_mapa` siguen verdes tras mover `anotar_ultimo` y las tablas.
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar.** **Step 4: verde + `tests.test_iot_ingreso`, `tests.test_iot_lavado_mapa`, `tests.test_iot_analogicas`, `tests.test_iot_digitales`.**
- [ ] **Step 5:** `git add iot_lavado.py iot_ingreso.py tests/test_iot_modbus_poller.py`.

---

### Task 3: rutas y sensores

**Files:** Modify `app.py`; Create `tests/test_iot_origenes_modbus_api.py`.

**Interfaces:**
- Consumes: T1 (`crear_modbus`, `guardar_modbus`), lo de la etapa 3 (`_contexto_de_origenes`, `_origen_para_pantalla`, `iot_ingreso.estado/ultimo_de_canal/olvidar`).
- Produces:
  - `POST /api/iot/origenes` acepta `{"tipo": "modbus_tcp", "nombre", "host", "port", "unit_id", "ventana_lectura_min"}` (sin `tipo` = HTTP, como hoy) y devuelve `{"origen": …}` (sin `token`). `POST /api/iot/origenes/<id>` despacha por el tipo guardado: Modbus recibe `{nombre, host, port, unit_id, ventana_lectura_min, canales}`. `POST …/token` sobre un Modbus: `400 {"error": "Un equipo Modbus no usa token."}`. `DELETE` igual que hoy.
  - `GET /api/iot/origenes`: cada origen trae `tipo`; los HTTP, `formato` y `url_ingreso` como hoy; los Modbus, `host`, `port`, `unit_id` y los campos Modbus de cada canal, sin `url_ingreso`; todos con `estado` y `ultimo` por canal. Nunca `token_hash`. Suma `funciones_entrada`, `funciones_analogica` y `tipos_dato` para armar los desplegables.
  - `GET /api/iot/sensores` suma a `entradas` las digitales de los equipos Modbus (`origen`, `origen_nombre`, igual que los HTTP) y `asignados_origen` cubre los dos tipos.
  - `POST /api/iot/ingreso/<id_modbus>` → el mismo 403 (cuerpo idéntico) que un origen inexistente, y suma un intento fallido.

- [ ] **Step 1: tests (fallan)** con el `test_client` (patrón de `tests/test_iot_origenes_api.py`; copiar su `Base`): alta Modbus feliz y que aparece en el listado con `tipo` y sin `url_ingreso` ni `token`; **Review Focus 5**: host/puerto/unit ID/ventana inválidos → 400 y la lista queda igual; guardar canales con un error → 400, nada cambia; sin sesión 401 y operario 403 en las rutas nuevas; **Review Focus 7**: el 403 idéntico, el 400 del token y que los orígenes HTTP de la etapa 3 siguen funcionando mezclados con uno Modbus; sacar un canal digital en uso o eliminar el equipo con un sensor atado → 400 que nombra la entrada; el sensor digital asignado a `<id>.<canal>` de un equipo Modbus se guarda y, si el equipo se borra a mano del JSON, el sensor sigue en el catálogo y en el panel como «sin datos» (misma garantía de la etapa 3); la ventana del equipo Modbus aplica a sus sensores (uno de 90 min sin lectura no está mudo con ventana 120, y el del M300 sí a los 31).
- [ ] **Step 2: correr y ver que fallan.** **Step 3: implementar.** **Step 4: verde + `tests.test_iot_origenes_api`, `tests.test_iot_origenes_integracion`, `tests.test_ups_api`.**
- [ ] **Step 5:** `git add app.py tests/test_iot_origenes_modbus_api.py`.

---

### Task 4: pantalla, documentación y prueba contra un servidor Modbus

**Files:** Modify `templates/index.html`, `CLAUDE.md`, `INSTALL.md`, `docs/iot-ingreso-http.md`.

**Interfaces:** consume `GET/POST /api/iot/origenes*` de T3. La tarjeta `#config-iot-origenes` de la etapa 3 pasa a decir «Otros equipos que mandan o responden datos» y su alta pide el **tipo** (HTTP o Modbus TCP).

- [ ] **Step 1: la tarjeta.** Alta: nombre, tipo, ventana (obligatoria) y, para Modbus, host (obligatorio), puerto y unit ID (vacíos = 502 y 1, en gris como ejemplo). Un equipo Modbus muestra: nombre, host, puerto, unit ID y ventana editables; el estado («Última lectura: lunes 23/11/2026 10:15», «Todavía no leyó nada» o «Último error (fecha): No se pudo conectar a …»); la tabla de canales con **Clave, Tipo, Nombre, Función, Dirección, Unit ID, Tipo de dato (solo analógico), Factor y Offset (solo analógico), NC (solo digital), Sensor, Último valor** y alta/baja de filas; «Guardar equipo» y «Eliminar equipo» (sin token, sin URL, sin `curl`). Texto de ayuda: las direcciones se pueden cargar como las muestra el equipo (10001, 30001, 40001) o como las usa el protocolo; el gateway lo lee solo, sin reiniciar; un equipo que no responde no frena a los demás; un valor analógico es `crudo × factor + offset`; los registros float32 se leen «AB CD»; si el equipo da 4-20 mA en un registro, el factor y el offset los calcula el tambo. Reglas del archivo: nada de `{{`, `{%` ni `{#` en template literals de JS (Jinja); datos de cada fila en atributos `data-`; todo texto pasa por `escHtml`; los números mal escritos NO se convierten en vacío (se mandan como texto y el servidor los rechaza).
- [ ] **Step 2: el editor «Sensores del tambo»** ya agrupa las entradas por `origen` (etapa 3): comprobar que las del equipo Modbus salen en su propio grupo y que un sensor analógico asignado se muestra «asignado en <equipo>».
- [ ] **Step 3: `CLAUDE.md`** — sección «Más de un equipo Modbus (etapa 2)»: diseño, límites (solo lectura, `float32_ab_cd` único orden de bytes, un cliente por equipo, reintento cada 30 s, `ConexionesModbus` corre después del principal en el mismo ciclo: un equipo con respuesta lenta atrasa el ciclo hasta `TIMEOUT_ORIGEN_S`), lo NO probado contra un equipo real y los comandos de las suites. **`INSTALL.md`** — para que `iot_lavado.py` tome la lógica nueva hay que reiniciarlo UNA vez (no en pleno lavado); después los equipos nuevos se toman solos; abrir el firewall/ruta entre la PC y el equipo. **`docs/iot-ingreso-http.md`**: una línea al principio que remite al tipo Modbus.
- [ ] **Step 4: verificar contra un servidor Modbus de verdad** (no solo con el cliente de mentira): levantar con `pymodbus.server` un servidor TCP en `127.0.0.1` (puerto alto, fuera del repo) con un `float32`, un `int16`, una entrada discreta y una bobina; correr `iot_lavado.ConexionesModbus` con estado aislado (carpeta temporal y los `_RUTA` parcheados, igual que en la etapa 3) y comprobar los valores en `lecturas_actuales`; cambiar el valor del servidor y ver que cambia; apagar el servidor y ver el error, el «Sin datos» pasada la ventana y la recuperación al volver. Después, en el navegador (servidor de desarrollo aislado): alta de un equipo Modbus, canales, sensor, estado y consola sin errores, y una captura de la tarjeta.
- [ ] **Step 5: suites completas del IoT** (las de la etapa 3 más `test_iot_origenes_modbus`, `test_iot_modbus_poller`, `test_iot_origenes_modbus_api`) y `git add` de lo tocado. No commitear sin pedido.

---

## Preguntas abiertas para el usuario (cambian lo que se construye)

1. **¿Hay que COMANDAR salidas del segundo equipo?** El plan lo deja afuera a propósito: escribir bobinas en un equipo físico cuyo mapa nadie probó es el riesgo grande de este proyecto (el apagado de seguridad, el lavado y la voz están pensados para UN equipo). Si lo querés, es una etapa aparte con su propio apagado de arranque por equipo. Recomiendo solo lectura ahora.
2. **¿Qué equipo es?** El plan soporta `uint16`, `int16` y `float32` con el orden de bytes del M300 (`AB CD`). Un equipo que mande el decimal con otro orden (`CD AB`) o enteros de 32 bits necesita un tipo de dato más; conviene saber la marca y el modelo antes de ejecutar y revisar su tabla de registros.
3. **Ventana obligatoria**, como en la etapa 3: cada equipo exige que cargues cada cuántos minutos tiene que haber una lectura; un valor mío inventado taparía un equipo caído o marcaría mudo a uno lento.

## Self-review

- Cobertura de la spec: §1 (tipo `modbus_tcp` con `host`/`port`/`unit_id`, ventana) → T1/T3; §2 (canales `<id>.<canal>` con función, dirección, unit ID, `tipo_dato`, factor, offset, invertido; Modicon) → T1/T2; §3 (sensores validan contra todas las entradas de todos los orígenes, un analógico por canal) → T1/T3 (el código de la etapa 3 ya lo hace; se prueba con los dos tipos); §6 (pantalla de orígenes y mapa por origen) → T4; §7 (un cliente por origen, un caído no frena a los demás, se relee cada ciclo, salvo que acá también se toma el host/puerto en caliente porque el costo es cero) → T2. Fuera a propósito: salidas Modbus de otros equipos y modo `sostenida` (pregunta 1), el botón «Cargar mapa PUSR M300» (la tabla del principal ya tiene «Volver a los valores del M300» de la etapa 1), y la tabla Modbus del principal por origen (el principal sigue con su `iot_mapa`).
- Tipos y nombres cruzados verificados: `crear_modbus`/`guardar_modbus` (T1) son lo que llama T3; los campos de canal de T1 son lo que lee `ConexionesModbus.ciclo` (T2); `anotar_ultimo` y las tablas se mueven en T2 y `iot_ingreso` las consume de ahí (sus tests de la etapa 3 lo cubren).
- Proporción: la spec cubre las cuatro etapas en ~150 líneas; este plan es de ~190 con las firmas y los casos y casi ningún cuerpo de función.
