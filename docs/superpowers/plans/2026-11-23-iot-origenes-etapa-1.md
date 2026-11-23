# Orígenes IoT — Etapa 1: mapa Modbus por canal del equipo principal — Plan de implementación

> **Para quien lo ejecute:** REQUIRED SUB-SKILL: usar superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para llevar este plan tarea por tarea. Los pasos usan casillas (`- [ ]`).

**Objetivo:** que el tambo vea y edite desde la web, por cada entrada digital y cada salida del equipo principal (M300), la función Modbus, la dirección, el unit ID, el contacto invertido y el largo del pulso, sin tocar código y sin cambiar nada si no edita.

**Arquitectura:** un módulo nuevo `iot_mapa.py` guarda **solo lo que el tambo cambió** (JSON fuera de git) y devuelve el mapa completo, con los valores de siempre como defecto. `iot_lavado.py` deja de usar direcciones y `device_id=1` escritos en el código y las pide a `iot_mapa` en cada ciclo (se toman sin reiniciar). La web suma un bloque con dos tablas (entradas digitales y salidas) que habla con `GET/POST /api/iot/mapa`.

**Tecnologías:** Python 3.12, Flask, pymodbus (`ModbusTcpClient`), SQLite, `unittest`, `index.html` con JS plano.

**Spec:** [docs/superpowers/specs/2026-11-23-iot-origenes-design.md](../specs/2026-11-23-iot-origenes-design.md) (etapa 1 de las 4; las etapas 2 a 4 no se tocan acá).

## Restricciones globales

* Claves de canal que **no cambian nunca**: entradas digitales `lavado_rotativa`, `barrido_rotativa`, `di_3`..`di_32`; analógicas `ai_1`..`ai_32`; salidas `do_1`..`do_32` (están en el historial y en los sensores guardados).
* Sin archivo `iot_mapa.json` el comportamiento es **idéntico al de hoy**: entrada n = `discrete_input` en la dirección n−1, salida n = `coil` en la n−1, unit ID 1, pulso de 0,5 s. Una prueba lo fija (mismas llamadas, mismos argumentos).
* `iot_lavado.py` sigue siendo el único dueño de la conexión Modbus; Flask nunca escribe Modbus.
* Lo que se guarda en la web lo toma el gateway **sin reiniciar** (se vuelve a leer en cada ciclo). La IP y el puerto no se tocan.
* Las analógicas del principal siguen calibrándose en `iot_analogicas`: **no se tocan** en esta etapa.
* Sin valores inventados: un campo en blanco = valor de fábrica; no se agregan umbrales del tambo.
* Textos de la app en español rioplatense; fechas concretas con día de la semana si hubiera alguna; nombres de rodeo los de DelPro (no aplica acá).
* `iot_mapa.py` **no importa ningún otro módulo del proyecto** salvo `iot_canales` (lo importan `iot_lavado` y `iot_monitoreo`: un import de vuelta armaría un ciclo).
* El archivo de estado se escribe a un temporal y se reemplaza (`os.replace`), como `iot_analogicas`: `iot_lavado.py` lo lee en cada ciclo y no puede ver un archivo a medias.
* **No se commitea** hasta que el usuario lo pida («commiteá, mergeá a master y pusheá»). Por eso este plan no tiene pasos de commit.
* Cuidado con `index.html`: `{{` o `{%` dentro de un template literal de JS lo lee Jinja y la página entera devuelve 500 (poner un espacio o un ternario).

## Foco de revisión

Entradas que la spec implica y que ninguna prueba de funcionamiento normal ejercita; cada una tiene su prueba en la tarea que es dueña del código.

1. **Una dirección editada que pisa a otro canal.** El tambo pone `di_5` en la dirección 0 y `lavado_rotativa` ya la usa de fábrica: se rechaza al guardar, con un mensaje que nombra los dos canales (Tarea 1).
2. **`iot_mapa.json` roto o a medias** (JSON inválido, una lista en vez de un objeto, una clave desconocida): el gateway sigue con los valores de fábrica y no se cae (Tarea 1).
3. **Entrada leída por registro que contesta error o un registro cortado**: no se toma como «apagado»; el canal conserva su estado anterior, igual que hoy con un error de `discrete_input` (Tarea 2).
4. **Un pulso de 0, negativo, `NaN`, booleano o enorme**: se rechaza; el pulso bloquea el ciclo de 3 s del gateway mientras dura (Tarea 1).
5. **Editar una fila y después «Volver a valores del M300»**: el archivo no debe quedar con filas iguales a las de fábrica (si no, un cambio futuro del defecto no se vería) (Tareas 1 y 4).

## Estructura de archivos

* **Crear** `iot_mapa.py`: mapa Modbus de las entradas digitales y las salidas del principal (defectos, validación, guardado atómico). Una sola responsabilidad: *dónde está cada canal en el equipo*.
* **Crear** `tests/test_iot_mapa.py`: pruebas de `iot_mapa` y de `/api/iot/mapa`.
* **Crear** `tests/test_iot_lavado_mapa.py`: pruebas de que `iot_lavado` usa el mapa y de que sin mapa llama igual que antes.
* **Modificar** `iot_lavado.py`: `_leer_estado`, nuevo `procesar_entradas`, `ejecutar_comandos_pendientes`, `_escribir_reles`, `main`.
* **Modificar** `app.py`: rutas `GET` y `POST /api/iot/mapa` junto a las de `/api/iot/canales` (~línea 3325).
* **Modificar** `templates/index.html`: bloque de las dos tablas dentro de `#config-iot-canales-card` (después del botón `#config-iot-canales-guardar`, ~línea 4475) y su JS junto a `cargarCanalesGateway` (~línea 16891).
* **Modificar** `.gitignore` (agregar `iot_mapa.json`) y `CLAUDE.md` (sección corta).

---

### Tarea 1: `iot_mapa.py` — el mapa y sus reglas

**Archivos:**
- Crear: `iot_mapa.py`
- Crear: `tests/test_iot_mapa.py`
- Modificar: `.gitignore` (junto a `iot_analogicas.json`, línea 141)

**Interfaces:**
- Consume: `iot_canales.ENTRADAS_DIGITALES`, `iot_canales.SALIDAS`, `iot_canales.numero(clave) -> int`.
- Produce (todo lo usan las Tareas 2, 3 y 4):
  * `FUNCIONES_ENTRADA = ("discrete_input", "coil", "input_register", "holding_register")`
  * `UNIT_ID_DEFECTO = 1`, `PULSO_DEFECTO_S = 0.5`, `PULSO_MAX_S = 10`
  * `de_fabrica_entrada(clave: str) -> dict` → `{"funcion": "discrete_input", "direccion": n-1, "unit_id": 1, "invertido": False}`
  * `de_fabrica_salida(clave: str) -> dict` → `{"direccion": n-1, "unit_id": 1, "pulso_s": 0.5}`
  * `entrada(clave: str) -> dict` y `salida(clave: str) -> dict`: la fila efectiva (fábrica + cambios del tambo).
  * `entradas() -> dict` y `salidas() -> dict`: `{clave: fila efectiva}` de las 32; leen el archivo **una vez**.
  * `cambios() -> dict` → `{"entradas": {clave: fila completa}, "salidas": {clave: fila completa}}`: solo lo guardado (para marcar filas modificadas).
  * `guardar(entradas: dict, salidas: dict) -> None`: reemplaza todo lo guardado; levanta `ValueError` con mensaje en español y **no toca el archivo** si algo no valida.
  * `_RUTA`: ruta de `iot_mapa.json` (las pruebas la parchan).

- [ ] **Paso 1: Escribir las pruebas que fallan** en `tests/test_iot_mapa.py`. Clase base `Base` que parcha `iot_mapa._RUTA` a un archivo en un `TemporaryDirectory` (mismo patrón que `tests/test_iot_analogicas.py::Base`). Pruebas, con estos valores exactos:
  * `test_sin_archivo_todo_es_de_fabrica`: `iot_mapa.entrada("lavado_rotativa") == {"funcion": "discrete_input", "direccion": 0, "unit_id": 1, "invertido": False}`; `entrada("di_32")["direccion"] == 31`; `salida("do_1") == {"direccion": 0, "unit_id": 1, "pulso_s": 0.5}`; `salida("do_32")["direccion"] == 31`; `len(entradas()) == 32` y `len(salidas()) == 32`.
  * `test_guardar_y_leer_un_cambio`: `guardar({"di_5": {"funcion": "coil", "direccion": 40, "unit_id": 3, "invertido": True}}, {"do_2": {"direccion": 9, "unit_id": 2, "pulso_s": 1.5}})` → `entrada("di_5")` y `salida("do_2")` devuelven eso; `entrada("di_6")` sigue de fábrica.
  * `test_solo_se_guarda_lo_que_difiere_de_fabrica`: guardar `di_5` con exactamente los valores de fábrica más `do_2` con un cambio → `cambios()["entradas"] == {}` y `cambios()["salidas"]` tiene solo `do_2` (Foco 5).
  * `test_un_campo_en_blanco_vuelve_al_valor_de_fabrica`: `guardar({"di_5": {"funcion": "", "direccion": "", "unit_id": None, "invertido": False}}, {})` → no queda guardado nada.
  * `test_direccion_modicon_se_convierte`: `di_3` con `funcion="discrete_input"`, `direccion=10003` → `entrada("di_3")["direccion"] == 2`; `input_register` 30005 → 4; `holding_register` 40001 → 0; `coil` con 10 se queda en 10 (los coils no se convierten).
  * `test_direccion_pisada_por_otra_entrada_se_rechaza` (Foco 1): `guardar({"di_5": {"direccion": 0}}, {})` (y `lavado_rotativa` de fábrica en 0, misma función y unit ID) → `ValueError` cuyo mensaje contiene `"di_5"` y `"lavado_rotativa"`; el archivo no existe después. La misma dirección con **otra función** (`coil`) o **otro unit ID** (2) **sí** se acepta.
  * `test_dos_salidas_en_la_misma_bobina_se_rechazan`: `guardar({}, {"do_2": {"direccion": 0}})` → `ValueError` con `"do_2"` y `"do_1"`.
  * `test_valores_invalidos_se_rechazan_sin_tocar_lo_anterior`: primero un `guardar` válido; después, por separado, cada uno de estos da `ValueError` y `cambios()` sigue igual al válido: función `"hola"`; dirección `-1`, `65536`, `1.5`, `"abc"`, `True`; unit ID `0`, `248`, `2.5`; clave `"di_99"`; pulso `0`, `-1`, `float("nan")`, `float("inf")`, `True`, `11` (Foco 4); pulso `10` sí se acepta.
  * `test_archivo_roto_cae_a_fabrica` (Foco 2): escribir en `_RUTA` `"no es json"`, luego `"[1, 2]"`, luego `{"entradas": [], "salidas": 3}`, luego `{"entradas": {"di_99": {}, "di_4": "x"}}`: `entrada("di_4")` es de fábrica en los cuatro casos y no se levanta ninguna excepción.
  * `test_un_cambio_guardado_a_medias_en_el_archivo_se_completa_con_fabrica`: archivo con `{"entradas": {"di_4": {"direccion": 7}}}` → `entrada("di_4") == {"funcion": "discrete_input", "direccion": 7, "unit_id": 1, "invertido": False}`.

- [ ] **Paso 2: Correr las pruebas y ver que fallan**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_mapa -v`
  Expected: error de importación (`No module named 'iot_mapa'`).

- [ ] **Paso 3: Implementar `iot_mapa.py`** con las firmas de arriba.
  * Estructura del archivo: `{"entradas": {clave: {funcion, direccion, unit_id, invertido}}, "salidas": {clave: {direccion, unit_id, pulso_s}}}`.
  * La fila efectiva = fábrica actualizada con los campos presentes y válidos de lo guardado. Lectura tolerante (cualquier forma rara cae a fábrica; se descartan claves desconocidas).
  * `guardar`: por cada fila recibida, campo vacío (`None` o texto en blanco) = fábrica; validar tipo y rango (dirección entero 0 a 65535, sin `bool`; unit ID entero 1 a 247; `pulso_s` número finito mayor que 0 y como mucho `PULSO_MAX_S`; `funcion` en `FUNCIONES_ENTRADA`); convertir Modicon **antes** de validar el rango (`discrete_input` 10001–19999 → −10001, `input_register` 30001–39999 → −30001, `holding_register` 40001–49999 → −40001; `coil` y las salidas no se convierten, porque `1` a `9999` son direcciones válidas); guardar solo las filas que difieren de fábrica.
  * El chequeo de pisadas se hace sobre el mapa **efectivo completo** (fábrica + lo recién recibido): entradas por `(unit_id, funcion, direccion)`, salidas por `(unit_id, direccion)`. El mensaje nombra los dos canales y dice cuál es la dirección.
  * Reusar `iot_analogicas.numero` **no**: ese módulo no puede importarse desde acá sin ampliar el import permitido; escribir un validador chico propio.
  * Escritura atómica (`tempfile.mkstemp` en la misma carpeta + `os.replace`, con `threading.Lock`), igual que `iot_analogicas.guardar`.

- [ ] **Paso 4: Correr las pruebas y ver que pasan**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_mapa -v`
  Expected: todas en `ok`, sin `FAIL` ni `ERROR`.

- [ ] **Paso 5: Agregar `iot_mapa.json` al `.gitignore`**, en la línea siguiente a `iot_analogicas.json`.

---

### Tarea 2: `iot_lavado.py` usa el mapa (y sin mapa llama igual que antes)

**Archivos:**
- Modificar: `iot_lavado.py` (`_leer_estado` ~141, `ejecutar_comandos_pendientes` ~257, `_escribir_reles` ~320, `main` ~633)
- Crear: `tests/test_iot_lavado_mapa.py`

**Interfaces:**
- Consume (Tarea 1): `iot_mapa.entradas() -> dict`, `iot_mapa.salida(clave) -> dict`, `iot_mapa.PULSO_DEFECTO_S`.
- Produce:
  * `_leer_estado(client, direccion: int, invertido: bool, funcion: str = "discrete_input", unit_id: int = 1) -> bool | None` (los dos parámetros nuevos tienen defecto: quien lo llame como antes sigue andando).
  * `procesar_entradas(con, client, anteriores: dict, ultimo_latido: dict) -> None`: el cuerpo del `for canal in canales_a_leer()` de `main`, sacado a una función para poder probarlo.
  * `DURACION_PULSO_S = iot_mapa.PULSO_DEFECTO_S` (sigue existiendo como nombre; `CANALES` y `ACTUADORES` también quedan, como la forma de fábrica: las pruebas de `test_iot_canales` los leen).

- [ ] **Paso 1: Escribir las pruebas que fallan** en `tests/test_iot_lavado_mapa.py`. Un `ClienteFalso` que registra cada llamada como tupla `(método, address, count_o_value, device_id)` en `self.llamadas`: `read_discrete_inputs`, `read_coils`, `read_input_registers`, `read_holding_registers` (devuelven un objeto con `.bits = [True]` / `.registers = [valor]` e `isError()`), `write_coil(address, value, device_id=1)`, `connected = True`, `close()`. Parchar `iot_mapa._RUTA` a un archivo temporal y `iot_lavado.time.sleep` para registrar el pulso. Pruebas:
  * `test_sin_mapa_las_entradas_se_leen_como_siempre`: con `lavado_rotativa` y `di_3` nombrados (parchar `iot_canales.nombres` o su `_RUTA`), `procesar_entradas` hace exactamente `[("read_discrete_inputs", 0, 1, 1), ("read_discrete_inputs", 1, 1, 1), ("read_discrete_inputs", 2, 1, 1)]` (lavado y barrido se leen siempre, `di_3` por estar nombrada) — dirección n−1, `count=1`, `device_id=1`.
  * `test_sin_mapa_un_pulso_es_el_de_siempre`: encolar un `do_3` en `comandos_actuador` (`ejecutado=0`) y llamar `ejecutar_comandos_pendientes` → llamadas `[("write_coil", 2, True, 1), ("write_coil", 2, False, 1)]`, `sleep` llamado con `0.5`, resultado `"ok"` en la fila.
  * `test_sin_mapa_prender_y_apagar_un_rele_es_como_siempre`: `_escribir_reles(client, ["do_1"], True)` → `[("write_coil", 0, True, 1)]` y devuelve `set()`.
  * `test_una_entrada_con_otro_unit_id_y_otra_funcion`: mapa con `di_3 = {funcion: "input_register", direccion: 7, unit_id: 4}` → `procesar_entradas` llama `read_input_registers(7, 1, 4)`; un registro con valor `5` es activo y con `0` es inactivo; `digitales_estado` queda con `1` y `0` respectivamente.
  * `test_una_entrada_invertida_en_el_mapa_y_en_el_sensor_se_anulan`: mapa `di_3` con `invertido=True` y un sensor digital con `entrada="di_3"`, `invertido=True` (usar `iot_sensores` parchado como hace `tests/test_iot_digitales.py`) → el bit leído `True` queda **sin invertir** (`1`).
  * `test_un_registro_con_error_no_cambia_el_estado` (Foco 3): `read_input_registers` devuelve error, o `registers == []` → no se escribe `digitales_estado` ni `eventos_di` para ese canal (y no hay excepción).
  * `test_un_pulso_con_mapa_usa_su_unit_id_su_bobina_y_su_duracion`: mapa `do_3 = {direccion: 40, unit_id: 2, pulso_s: 1.5}` → `[("write_coil", 40, True, 2), ("write_coil", 40, False, 2)]` y `sleep(1.5)`.
  * `test_prender_un_rele_con_mapa_usa_su_unit_id_y_su_bobina`: `_escribir_reles(client, ["do_3"], False)` → `[("write_coil", 40, False, 2)]`.
  * `test_el_mapa_se_toma_sin_reiniciar`: dos llamadas seguidas a `procesar_entradas`, con un `iot_mapa.guardar` en el medio → la segunda usa la dirección nueva.

- [ ] **Paso 2: Correr las pruebas y ver que fallan**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_lavado_mapa -v`
  Expected: `AttributeError: module 'iot_lavado' has no attribute 'procesar_entradas'` (y las que dependen del mapa fallan).

- [ ] **Paso 3: Implementar en `iot_lavado.py`.**
  * `import iot_mapa` junto a los otros; `DURACION_PULSO_S = iot_mapa.PULSO_DEFECTO_S`.
  * `_leer_estado(client, direccion, invertido, funcion="discrete_input", unit_id=1)`: despachar por función con `count=1, device_id=unit_id` (`read_discrete_inputs`, `read_coils`, `read_input_registers`, `read_holding_registers`). Los dos primeros leen `resultado.bits[0]`; los registros, `bool(resultado.registers[0])` (distinto de 0 = activo). Un error, una respuesta sin bits o sin registros, o una excepción devuelven `None` (con `client.close()` en el caso de excepción, como ahora). Los argumentos de la llamada de fábrica tienen que quedar **idénticos** a los de hoy: `address=direccion, count=1, device_id=1`.
  * `procesar_entradas(con, client, anteriores, ultimo_latido)`: leer `mapa = iot_mapa.entradas()` y `invertidas = entradas_invertidas()` **una vez**, recorrer `canales_a_leer()` y hacer lo que hoy hace el cuerpo del `for` de `main` (latido, `registrar_si_cambio`, aviso por voz con `AUDIO_ACTIVADO`/`MENSAJES_VOZ`), pidiendo a `_leer_estado` la dirección, función y unit ID del mapa y `invertido = mapa[canal]["invertido"] != invertidas.get(canal, False)`. Un canal que no está en `anteriores` se inicia en `None`.
  * `main`: reemplazar el `for` por `procesar_entradas(con, client, anteriores, ultimo_latido)`; el resto queda igual.
  * `ejecutar_comandos_pendientes`: `if canal not in ACTUADORES` para el canal desconocido (mismo mensaje de error de hoy); si no, `cfg = iot_mapa.salida(canal)` y `write_coil(address=cfg["direccion"], value=..., device_id=cfg["unit_id"])` con `time.sleep(cfg["pulso_s"])` en el medio. Los mensajes de error y la alarma por fin de pulso sin confirmar **no cambian**; el de la dirección en el log pasa a usar `cfg["direccion"]`.
  * `_escribir_reles`: lo mismo para la dirección y el unit ID.

- [ ] **Paso 4: Correr las pruebas de esta tarea y las que ya existían del gateway**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_lavado_mapa tests.test_iot_canales tests.test_iot_digitales tests.test_iot_analogicas tests.test_iot_sensores tests.test_iot_estados -v`
  Expected: todas en `ok`.

---

### Tarea 3: `GET` y `POST /api/iot/mapa`

**Archivos:**
- Modificar: `app.py` (después de `api_iot_canales_guardar`, ~línea 3349)
- Modificar: `tests/test_iot_mapa.py` (agregar la clase `TestApi`)

**Interfaces:**
- Consume (Tarea 1): `iot_mapa.entradas()`, `iot_mapa.salidas()`, `iot_mapa.cambios()`, `iot_mapa.de_fabrica_entrada/salida`, `iot_mapa.guardar`, `iot_mapa.FUNCIONES_ENTRADA`; `iot_canales.nombre(clave)`, `iot_canales.numero(clave)`.
- Produce (lo usa la Tarea 4):
  * `GET /api/iot/mapa` → `{"funciones": ["discrete_input", ...], "pulso_max_s": 10, "entradas": [{"clave", "numero", "nombre", "funcion", "direccion", "unit_id", "invertido", "defecto": {funcion, direccion, unit_id, invertido}, "modificada": bool}], "salidas": [{"clave", "numero", "nombre", "direccion", "unit_id", "pulso_s", "defecto": {...}, "modificada": bool}]}` con las 32 de cada una, en orden.
  * `POST /api/iot/mapa` con `{"entradas": {clave: fila}, "salidas": {clave: fila}}` → `{"ok": true}`, o `400` con `{"error": "<mensaje de iot_mapa>"}` si no valida o el formato es inválido (`entradas`/`salidas` que no sean objetos).
  * Ambas con `@auth.requiere_rol("admin")`.

- [ ] **Paso 1: Escribir las pruebas que fallan** (clase `TestApi` en `tests/test_iot_mapa.py`, con la sesión de admin como en `tests/test_iot_analogicas.py` ~línea 371: `auth._intentos_fallidos.clear()` y `session_transaction` con `rol="admin"`):
  * `test_get_trae_las_32_de_cada_una_con_su_defecto`: 200, `len(entradas) == 32`, `len(salidas) == 32`, `entradas[2]["clave"] == "di_3"`, `entradas[2]["defecto"]["direccion"] == 2`, `modificada` falso en todas.
  * `test_post_guarda_y_get_lo_marca_modificada`: POST de un cambio en `di_5` → 200; GET: esa fila con `modificada` verdadero y el valor nuevo, y su `defecto` intacto.
  * `test_post_con_una_direccion_pisada_da_400_con_el_mensaje`: `{"entradas": {"di_5": {"direccion": 0}}}` → 400 y `"lavado_rotativa"` dentro de `error`; GET sigue sin modificadas.
  * `test_post_con_formato_invalido_da_400`: `{"entradas": [1], "salidas": {}}` → 400.
  * `test_sin_sesion_de_admin_no_entra`: GET y POST de un cliente anónimo no dan 200.
  * `test_el_nombre_es_el_que_puso_el_tambo`: con `iot_canales.guardar({"di_5": "Térmico bomba"})` (parchando `iot_canales._RUTA`), la fila de `di_5` trae `nombre == "Térmico bomba"`; sin nombre, `"Entrada 5"`.

- [ ] **Paso 2: Correr y ver que fallan**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_mapa.TestApi -v`
  Expected: `404` en el GET (la ruta no existe).

- [ ] **Paso 3: Implementar las dos rutas en `app.py`**, `import iot_mapa` junto a los otros `import iot_*` (~línea 49). El `GET` arma las filas combinando `iot_mapa.entradas()/salidas()` (efectivo), `de_fabrica_*` y `cambios()` (`modificada = clave in cambios[...]`). El `POST` toma `entradas` y `salidas` del cuerpo (ausentes = `{}`), exige que sean objetos y llama `iot_mapa.guardar`, devolviendo `400` con el texto del `ValueError` (mismo patrón que `api_iot_canales_guardar`).

- [ ] **Paso 4: Correr y ver que pasan**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_mapa -v`
  Expected: todas en `ok`.

---

### Tarea 4: las dos tablas en ⚙ Configuración › 🔌 Entradas/Salidas

**Archivos:**
- Modificar: `templates/index.html` (HTML ~4475, JS ~16175 y ~16891)
- Modificar: `CLAUDE.md` (sección corta nueva)

**Interfaces:**
- Consume (Tarea 3): `GET/POST /api/iot/mapa` con la forma de arriba; helpers que ya existen en `index.html`: `pedirJSON(url, opciones)`, `escHtml(texto)`.
- Produce: nada que use otra tarea (es la última).

- [ ] **Paso 1: HTML.** Dentro de `#config-iot-canales-card`, **después** del `<div>` con `#config-iot-canales-guardar` y **antes** del bloque «Sensores del tambo», un bloque con el mismo estilo de borde que los vecinos: título «Dónde está cada canal en el equipo (mapa Modbus)», un párrafo `explica`, `#config-iot-mapa-estado` (línea de estado), dos tablas `class="tabla"` con ids `config-iot-mapa-entradas` y `config-iot-mapa-salidas` (cada una en un `div` con `overflow:auto` y `max-height:420px`), y dos botones: `#config-iot-mapa-fabrica` («Volver a los valores del M300», clase `secundario`) y `#config-iot-mapa-guardar` («Guardar mapa», `disabled` al inicio). El texto explicativo tiene que decir, en lenguaje del tambo: que en blanco o igual al placeholder significa «el valor de fábrica del M300» (el que se usa hoy); que las direcciones del M300 se pueden cargar como las muestra el equipo (10001, 30001, 40001) o como las usa el protocolo (0, 2…), y que `10001` se guarda como `0`; que las bobinas (salidas y entradas tipo «bobina») se cargan tal cual; que «Contacto NC» se suma al del sensor y los dos se anulan; que el pulso bloquea el ciclo del gateway mientras dura (máximo `pulso_max_s` segundos); que los cambios los toma el gateway solos, sin reiniciar.

- [ ] **Paso 2: JS.** Junto a `cargarCanalesGateway`:
  * Estado: `mapaFilas = {entradas: [], salidas: []}`, `mapaActual = {}` (clave → fila editada, solo campos), `mapaOriginal = ''` (JSON para saber si cambió), `mapaFunciones = []`, `mapaPulsoMax`.
  * `cargarMapaModbus()`: `pedirJSON('/api/iot/mapa')`, arma `mapaActual` con `funcion/direccion/unit_id/invertido` (entradas) y `direccion/unit_id/pulso_s` (salidas) **como texto**, deja en blanco lo que es igual a `defecto`, pinta y deshabilita Guardar. Se llama desde donde hoy se llama `cargarCanalesGateway()` (~línea 16175).
  * `pintarMapaModbus()`: tabla de entradas con columnas N°, Canal (`nombre`), Función (`<select>` con las 4 funciones y una opción «de fábrica»), Dirección, Unit ID, «Contacto NC» (checkbox); tabla de salidas con N°, Canal, Bobina, Unit ID, Pulso (s). Los `placeholder` muestran el valor de `defecto`. Una fila con `modificada` (o con algo escrito distinto del defecto) se resalta con la misma variable de color que usen las otras tablas (buscar una existente; no inventar una paleta).
  * Un solo `addEventListener('input'/'change')` por tabla, delegado, que actualiza `mapaActual[clave][campo]` y recalcula si Guardar está habilitado. Los `data-*` llevan clave y campo (`data-mapa-campo`, `data-mapa-clave`).
  * «Volver a los valores del M300»: deja `mapaActual` en blanco para todas las filas (con `confirm`), repinta y habilita Guardar.
  * «Guardar mapa»: `POST /api/iot/mapa` con `{entradas: {...}, salidas: {...}}`; números como `Number` o `null` si están en blanco; en éxito vuelve a llamar `cargarMapaModbus()` y muestra `✔ Guardado.`; en error muestra el mensaje del servidor tal cual (`Error: ...`) y **no pierde lo escrito**.
  * Cuidado con Jinja: ningún `{{` ni `{%` dentro de los template literals nuevos.

- [ ] **Paso 3: Verificar en el navegador** (`preview_start` con el servidor del proyecto; si no hay configuración de arranque, `python servidor.py` en la PC de desarrollo). En ⚙ Configuración › 🔌 Entradas/Salidas con un admin:
  1. Se ven las dos tablas con 32 filas cada una y los valores de fábrica como placeholder; «Guardar mapa» está deshabilitado.
  2. Cambiar `Entrada 5` a dirección `0`: «Guardar mapa» muestra el error que nombra `lavado_rotativa` y lo escrito sigue ahí.
  3. Cambiar `Entrada 5` a dirección `100` y la salida 3 a unit ID `2`: guardar → `✔ Guardado.`; recargar la página; esas dos filas aparecen resaltadas y con esos valores; el archivo `iot_mapa.json` tiene exactamente esas dos filas.
  4. «Volver a los valores del M300» → guardar → `iot_mapa.json` queda `{"entradas": {}, "salidas": {}}` (o sin filas) y nada resaltado.
  5. `read_console_messages` sin errores; el resto de la tarjeta (nombres, sensores, analógicas, vúmetros) sigue andando.
  6. Anotar que no se probó contra un M300 real (ver Tarea 4, Paso 5).

- [ ] **Paso 4: Suite completa**

  Run: `C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest discover -s tests -t .`
  Expected: todas en `ok`; cualquier fallo se informa por nombre, sea o no de este cambio.

- [ ] **Paso 5: Documentar en `CLAUDE.md`.** Una sección corta «Mapa Modbus por canal del equipo principal (23/11/2026)» junto a las de IoT: qué hace `iot_mapa`, que solo guarda lo que el tambo cambió, los defectos de siempre, la conversión Modicon, el tope del pulso (`PULSO_MAX_S`, tope técnico porque el pulso bloquea el ciclo de 3 s), que **no se probó contra el M300 real** (hacerlo con un jumper en una entrada y mirando el cambio en la tarjeta, y con un pulso en una salida sin nada conectado), y que las etapas 2 a 4 (más equipos Modbus, origen HTTP, comandar un ESP32) siguen el spec `2026-11-23-iot-origenes-design.md`.

---

## Decisiones que tomé en el plan (para que las mires)

* **`modo` (`pulso`/`sostenida`) de la spec no entra en la etapa 1.** Hoy no hay ningún botón de salida «sostenida» (lo sostenido lo manejan la voz y el Lavado Automático por otra vía); entra en la etapa 4, con las órdenes `prender`/`apagar` del ESP32.
* **Las salidas solo escriben bobinas (`write_coil`).** Una salida por registro de retención (`write_register`) queda para cuando haga falta un equipo que lo pida.
* **`PULSO_MAX_S = 10`** es un tope técnico, no un umbral del tambo: el pulso se hace con `time.sleep` dentro del ciclo de 3 s del gateway y frena el lavado y la voz mientras dura.
* **Las entradas digitales por registro se leen como «distinto de 0 = activo»**, un solo registro. Sin `factor`/`offset`/tipos de dato: eso es de las analógicas y de las etapas siguientes.
* **Las pruebas viejas no parchan `iot_mapa._RUTA`**: si en la PC de desarrollo existiera un `iot_mapa.json` con cambios, podrían leerlo. Sin archivo (el caso normal hasta que se edite), no hay efecto.

## Autorrevisión contra la spec

* §2 Canal (Modbus, principal): función, dirección, unit ID, invertido, pulso → Tareas 1 y 2; Modicon → Tarea 1; solo se guarda lo cambiado y «Cargar mapa PUSR M300» → Tareas 1 y 4. `tipo_dato`, `factor`/`offset` y `modo`: **fuera de la etapa 1** (ver Decisiones).
* §6 Pantalla: las tablas de digitales y salidas → Tarea 4. La lista de orígenes y el estado de cada uno son de la etapa 2.
* §7 `iot_lavado`: el mapa leído en cada ciclo → Tarea 2. Un cliente por origen es de la etapa 2.
* «Pruebas que no se negocian» de la etapa 1 (mismas llamadas sin archivo; un canal repetido se rechaza con mensaje claro) → Tareas 1 y 2.
