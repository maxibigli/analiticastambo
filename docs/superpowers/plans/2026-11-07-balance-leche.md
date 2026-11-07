# Balance de leche Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un menú "Balance de leche" que compara por día sala (DelPro) → tanque (Milky) → camión (La Serenísima), con factor kg→litros editable y alerta cuando un tramo se desvía de ±3 %.

**Architecture:** Un módulo nuevo y puro, `balance_leche.py`, hace todo el cálculo (días cerrados, desvíos, fecha corrida, evaluación de alertas, registro de lo ya avisado). `app.py` solo trae los datos (DelPro, Milky, comprador), expone dos endpoints y engancha la alerta. `index.html` suma una tarjeta nueva a la página "Entrega de Leche".

**Tech Stack:** Python 3.14 (`C:\Python314\python.exe`, fuera del PATH), Flask, pyodbc/SQL Server, Chart.js ya incluido en `index.html`, `unittest` (pytest no está instalado).

**Spec:** `docs/superpowers/specs/2026-11-07-balance-leche-design.md`

## Global Constraints

- Factor por defecto **1,05 L por kg**, editable, guardado por tambo, solo lo cambia un admin; rango válido 0,5 a 2,0 (decisión del plan, es un tope de sentido común).
- Umbral **±3 %** = `cicla.UMBRAL_DIF_PCT`, para los dos tramos. No inventar otros umbrales.
- Día de DelPro cerrado = vacas con leche ≥ **90 % de la mediana** de vacas de los días del período.
- Días se comparan en el **mismo día**, sin desfase.
- Kg de DelPro = `AnimalDaily.TotalYield` con `IsYieldValid = 1`, **incluida** la leche sin identificar (`BasicAnimal.Number = 0`). Vacas = vacas identificadas (`Number > 0`) con leche. Misma forma de consulta que `resumen.SQL_PRODUCCION_DIARIA` (sin filtro de rebaño: así se midió el 1,05).
- Tanque = todas las cargas de Milky del día (`lts_cicla`); camión se compara contra las cargas de **venta** (`cicla.solo_ventas`).
- Fecha corrida: dos días seguidos, desvío de signo opuesto, y la suma de las diferencias **en litros** queda dentro de ±3 % del volumen de referencia de los dos días juntos.
- Alerta: un día se evalúa recién cuando el día siguiente también tiene dato del tramo; una sola vez por día y por tramo, con registro en `balance_leche_avisos.json` (fuera de git). Sin registro previo, solo se evalúa el último día evaluable. Un par de fecha corrida no alerta.
- Cada fuente caída en la alerta = una línea de advertencia; nunca corta las demás líneas.
- Reglas del proyecto: no commitear `rutina.py`, `salas/convencional.py` ni `diagnostico_salud.py`; **antes de cada commit se revisa el diff (`git fetch origin`, `git status -sb`) y se espera el "dale" explícito del usuario**; mensaje de commit detallado en español, terminado en `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; nunca borrar archivos (mover a `porlasdudas`); credenciales solo por variables de entorno.
- No hay datos de DelPro en esta PC (SERVER-DELPRO no se alcanza; su copia local termina el 31/07/2026): las pruebas usan datos inyectados. En `index.html` no puede haber `{{`, `{%` ni `{#` dentro del JS.
- SQL Express con poca RAM: consulta acotada por fechas, cacheada 600 s, sin paralelismo.

## Review Focus

1. Un día de DelPro a medio cargar (el 03/11: 95 kg, 0 vacas) no produce desvío ni alerta, y los días futuros sin fila tampoco rompen nada. → Task 1.
2. Un día con DelPro pero sin Milky (antes del 17/10, o Milky caído) o con Milky pero sin DelPro: desvío vacío, sin excepción. → Task 1.
3. Sin credenciales o sin dato del comprador: se omite el tramo camión sin advertencia; sin credenciales de Milky: error claro que nombra `MILKY_*`. → Task 4.
4. Dos ciclos de alerta seguidos no repiten el aviso, y el botón "Probar" no lo consume. → Tasks 3 y 5.
5. Un factor inválido (0, negativo, texto, 1e9) se rechaza y no pisa el guardado. → Task 2.

---

### Task 1: Cálculo del balance

**Files:**
- Create: `balance_leche.py`
- Create: `tests/test_balance_leche.py`

**Interfaces:**
- Produces (constantes): `FACTOR_DEFECTO = 1.05`, `FACTOR_DENSIDAD_TIPICO = 1 / 1.03` (≈ 0,971, referencia para mostrar la diferencia), `UMBRAL_PCT = cicla.UMBRAL_DIF_PCT`, `PISO_VACAS = 0.90`.
- Produces: `sql_kg_diarios(desde: datetime.date, hasta: datetime.date) -> str` — una fila por día (`fecha`, `kg`, `vacas`) entre las fechas inclusive, forma de `resumen.SQL_PRODUCCION_DIARIA` pero sin `HAVING` y con `OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)`.
- Produces: `filas_kg(data: dict) -> list[dict]` — convierte el `{columns, rows}` de `db.run_query` en `[{"fecha": "AAAA-MM-DD", "kg": float, "vacas": int}]`.
- Produces: `litros_milky_por_dia(cargas: list[dict]) -> dict[str, dict]` — `{"AAAA-MM-DD": {"venta": float, "total": float}}` a partir de las cargas de `cicla.obtener_cargas` (fecha `dd/mm/aaaa hh:mm:ss`, campos `lts_cicla`, `es_venta`); ignora `lts_cicla` `None`.
- Produces: `litros_comprador_por_dia(entregas: list[dict]) -> dict[str, float]` — de las entregas de `laserenisima` (campos `fecha` `dd/mm/aaaa`, `litros`).
- Produces: `calcular(kg_dia: list[dict], milky: dict, comprador: dict | None, factor: float, umbral: float = UMBRAL_PCT) -> dict` con la forma:
  `{"factor", "umbral", "comprador_disponible": bool, "piso_vacas": float, "dias": [...], "totales": {...}, "tramo_mayor": "sala_tanque" | "tanque_camion" | None}`.
  Cada día: `fecha, kg, vacas, cerrado, lts_delpro, lts_milky, lts_venta, lts_comprador, desvio_sala_tanque_pct, desvio_tanque_camion_pct, alerta_sala_tanque, alerta_tanque_camion, corrida_sala_tanque, corrida_tanque_camion` (los litros y desvíos que no se pueden calcular van en `None`; desvíos redondeados a 1 decimal). `totales`: `lts_delpro, lts_milky, lts_venta, lts_comprador, desvio_sala_tanque_pct, desvio_tanque_camion_pct`, sumando solo los días donde el par del tramo existe. `tramo_mayor` = el tramo con mayor `abs(desvío total)`.

- [ ] **Step 1: Escribir los tests** en `tests/test_balance_leche.py` (`unittest.TestCase`), con estas aserciones:
  - `test_dia_sin_cerrar`: vacas `[1640, 1650, 1660, 1600, 0]` (último con 95 kg) → mediana de los días con vacas = 1645, `piso_vacas == 1480.5`, el último `cerrado is False` y sus desvíos `None`.
  - `test_desvio_sala_tanque`: 02/11 con kg 68402, Milky total 74127, factor 1.05 → `lts_delpro == 71822` (redondeado), `desvio_sala_tanque_pct == 3.2`, `alerta_sala_tanque is True`; 21/10 con kg 68729 y Milky 73319 → `1.6`, sin alerta.
  - `test_desvio_tanque_camion`: venta 71000 contra comprador 70500 → `0.7`, sin alerta.
  - `test_fecha_corrida_25_26_oct`: venta 72690 y 69949 contra comprador 47800 y 95800 → desvíos `52.1` y `-27.0`, `corrida_tanque_camion` verdadero en los dos y `alerta_tanque_camion` falso en los dos.
  - `test_desvio_grande_sin_par_no_es_corrida`: +52,1 % seguido de un día en 0,1 % → el primero `alerta_tanque_camion is True` y no corrida.
  - `test_sin_milky_o_sin_delpro`: día con kg y sin Milky, y día con Milky y sin kg → desvíos `None`, sin excepción.
  - `test_sin_comprador`: `comprador=None` → `comprador_disponible is False`, desvío camión `None` en todos los días.
  - `test_litros_milky_por_dia`: dos cargas de venta y una de guachera el mismo día → `venta` = suma de las dos, `total` = suma de las tres; fecha convertida a ISO.
  - `test_totales_y_tramo_mayor`: con un conjunto donde sala→tanque total es +1,0 % y tanque→camión −0,2 % → `tramo_mayor == "sala_tanque"`.
- [ ] **Step 2: Correr y ver que falla.** Run: `C:\Python314\python.exe -m unittest tests.test_balance_leche -v` desde la carpeta del proyecto. Expected: error de importación (`balance_leche` no existe).
- [ ] **Step 3: Implementar** `balance_leche.py` con las constantes y funciones de Interfaces. `calcular` ordena por fecha, calcula la mediana de vacas con `statistics.median` sobre los días con `vacas > 0`, y detecta fecha corrida recorriendo pares de fechas consecutivas (diferencia exacta de un día) del mismo tramo; la referencia en litros es `lts_delpro` para sala→tanque y `lts_comprador` para tanque→camión. Cuando un día queda marcado como corrida, su `alerta_*` pasa a falso.
- [ ] **Step 4: Correr los tests.** Expected: todos OK.
- [ ] **Step 5: Commit** (esperar el "dale" del usuario): `git add balance_leche.py tests/test_balance_leche.py`.

### Task 2: Factor guardado por tambo

**Files:**
- Modify: `configuracion_tambo.py` (DEFAULT, `guardar`)
- Modify: `balance_leche.py`
- Test: `tests/test_balance_leche.py` (clase nueva `TestFactor`)

**Interfaces:**
- Consumes: `configuracion_tambo.config_de(tambo_id) -> dict`, `configuracion_tambo.guardar(tambo_id, datos) -> dict`.
- Produces: campo `factor_kg_litros` (float o `None`) en `configuracion_tambo.DEFAULT` y un `_CAMPOS_FLOAT = ("factor_kg_litros",)` aceptado por `guardar`, que acepta coma o punto decimal, `""`/`None` = sin override, y lanza `ValueError` si no es un número finito.
- Produces: `balance_leche.factor_de(tambo: str) -> float` (el guardado o `FACTOR_DEFECTO`) y `balance_leche.guardar_factor(tambo: str, valor) -> float` (valida 0,5 ≤ valor ≤ 2,0 con `ValueError`; devuelve el valor guardado).

- [ ] **Step 1: Tests** (apuntando `configuracion_tambo._RUTA` a un archivo temporal con `unittest.mock.patch.object`): sin guardado → `factor_de == 1.05`; `guardar_factor("t", "1,06")` → `1.06` y `factor_de("t") == 1.06`; rechazan `0`, `-1`, `"abc"`, `1e9`, `float("nan")` con `ValueError` y el guardado anterior queda igual; `configuracion_tambo.guardar` con otros campos no pierde el factor.
- [ ] **Step 2: Correr y ver que falla.**
- [ ] **Step 3: Implementar** el campo en `configuracion_tambo.py` (rama nueva de `guardar` junto a `_CAMPOS_INT`) y las dos funciones en `balance_leche.py`.
- [ ] **Step 4: Correr los tests.** Expected: OK, y también los de Task 1.
- [ ] **Step 5: Commit** (con "dale"): `git add configuracion_tambo.py balance_leche.py tests/test_balance_leche.py`.

### Task 3: Evaluación de alertas y registro de lo avisado

**Files:**
- Modify: `balance_leche.py`
- Modify: `.gitignore` (agregar `balance_leche_avisos.json` con un comentario de una línea, igual que las otras)
- Test: `tests/test_balance_leche.py` (clase `TestAlertas`)

**Interfaces:**
- Consumes: la salida de `calcular` (Task 1).
- Produces: `evaluar_alertas(dias: list[dict], avisados: dict[str, str], umbral: float = UMBRAL_PCT) -> tuple[list[dict], dict[str, str]]`. Tramos `"sala_tanque"` y `"tanque_camion"`. Un día es evaluable en un tramo si su desvío existe **y** el día calendario siguiente también tiene desvío en ese tramo. Candidatos = evaluables con fecha posterior a `avisados.get(tramo)`; si el tramo no tiene registro, solo el último evaluable. Eventos = candidatos con `alerta_<tramo>` verdadera y sin `corrida_<tramo>`: `{"tramo", "fecha", "desvio_pct"}`. El `avisados` nuevo pone en cada tramo la fecha del último evaluable (sin cambios si no hay).
- Produces: `texto_alerta(eventos: list[dict]) -> list[str]` — una línea por tramo, coma decimal, fechas `dd/mm`, formato `"⚖️ Balance de leche: sala → tanque se desvió +3,2 % el 02/11 (umbral ±3 %)."` (varios días en el mismo tramo se listan en la misma línea).
- Produces: `leer_avisados(tambo: str) -> dict[str, str]` y `guardar_avisados(tambo: str, avisados: dict[str, str]) -> None`, sobre `_RUTA_AVISOS` (módulo, `balance_leche_avisos.json` junto al código), `{tambo: {tramo: "AAAA-MM-DD"}}`, con lock y tolerante a archivo ausente o corrupto.

- [ ] **Step 1: Tests**:
  - día +3,2 % en `sala_tanque` con el día siguiente también presente → un evento; sin día siguiente → ningún evento y `avisados` sin cambios para ese tramo.
  - segunda llamada con el `avisados` devuelto → lista vacía (no repite).
  - sin registro y tres días fuera de umbral → solo el último evaluable.
  - con registro, dos días nuevos fuera de umbral → los dos eventos.
  - par de fecha corrida (los datos del 25/26-oct de Task 1) → sin eventos.
  - `texto_alerta` contiene `"+3,2 %"` y `"02/11"`.
  - `guardar_avisados`/`leer_avisados` ida y vuelta con `_RUTA_AVISOS` temporal; archivo corrupto → `{}`.
- [ ] **Step 2: Correr y ver que falla.**
- [ ] **Step 3: Implementar** las cuatro funciones.
- [ ] **Step 4: Correr los tests.** Expected: OK.
- [ ] **Step 5: Commit** (con "dale"): `git add balance_leche.py tests/test_balance_leche.py .gitignore`.

### Task 4: Datos y endpoints

**Files:**
- Modify: `app.py` (cerca de `api_entregas_comparar`, ~línea 5530)
- Test: `tests/test_balance_leche_api.py`

**Interfaces:**
- Consumes: `balance_leche.*` (Tasks 1 y 2), `cicla.credenciales()`, `cicla.obtener_cargas`, `_laser_traer`, `_cache_get/_cache_set/_clave`, `_tambo_del_request()`, `auth.requiere_rol("admin")`.
- Produces: `_cargas_cicla(desde, hasta, usuario, password) -> dict` y `_entregas_laser(usuario, password) -> dict`, extraídas de `api_entregas_comparar` con las mismas claves de caché (`cicla:{desde}:{hasta}`, `laser:actual`) y los mismos TTL; `api_entregas_comparar` pasa a usarlas sin cambiar su respuesta.
- Produces: `_balance_leche(tambo: str, desde: date, hasta: date) -> dict` — arma la salida de `balance_leche.calcular` (consulta DelPro cacheada con `_clave(tambo, f"balance_kg:{desde}:{hasta}")` a 600 s) y le suma `cicla_incompleto`, `factor_defecto`, `factor_densidad`. Lanza `BalanceError(mensaje, status)` (clase nueva en `app.py`) si falta `MILKY_*` (400, el mensaje nombra `MILKY_USUARIO`/`MILKY_PASSWORD`), si falla Milky (502, `"CICLA: ..."`) o si falla DelPro (502, `"DelPro: ..."`). Sin credenciales o sin datos del comprador no es error: `comprador=None`. Si el comprador está configurado pero falla, `comprador=None` también (no corta el balance).
- Produces: `GET /api/balance/leche?desde&hasta` (admin): fechas `AAAA-MM-DD`, por defecto `hasta` = hoy y `desde` = hoy − 20 días; 400 si son inválidas (mismo mensaje que `/api/entregas/comparar`); si `desde > hasta` se invierten. `POST /api/balance/factor` (admin): `{"factor": ...}`, devuelve `{"factor": float}`; 400 con el `ValueError` si es inválido.

- [ ] **Step 1: Antes de tocar nada**, escribir en `tests/test_balance_leche_api.py` el test `test_comparar_sin_cambios`: con `cicla.obtener_cargas` y `app._laser_traer` reemplazados por datos fijos (usar `unittest.mock.patch`, `app._warmup = lambda *a, **k: None`, sesión admin con `session_transaction` poniendo `rol='admin'` y `usuario='x'`, variables `MILKY_USUARIO/MILKY_PASSWORD/LASER_USUARIO/LASER_PASSWORD` falsas, y `app._cache.clear()`), guardar el JSON de `/api/entregas/comparar`. Correrlo: debe pasar contra el código actual (sirve de red para el refactor).
- [ ] **Step 2: Tests nuevos** (mismo armado, y `db.run_query` reemplazado por una función que devuelve `{"columns": ["fecha","kg","vacas"], "rows": [...]}`): `test_balance_ok` (tres tramos con los números de Task 1; `comprador_disponible` verdadero); `test_balance_sin_comprador` (sin `LASER_*` → 200, `comprador_disponible` falso); `test_balance_sin_milky` (sin `MILKY_*` → 400 con `MILKY_USUARIO` en el mensaje); `test_balance_delpro_caido` (`db.run_query` lanza → 502 con `DelPro`); `test_balance_fechas_invalidas` (400); `test_balance_requiere_admin` (sin sesión de admin → rechazado igual que `/api/entregas/comparar`); `test_factor_post_y_efecto` (POST `1.06` → el GET siguiente usa `factor == 1.06`; POST `0` → 400 y el factor no cambia; el archivo de config apuntado a uno temporal con `patch.object(configuracion_tambo, "_RUTA", ...)`).
- [ ] **Step 3: Correr y ver que fallan** los nuevos (y que `test_comparar_sin_cambios` pasa). Run: `C:\Python314\python.exe -m unittest tests.test_balance_leche_api -v`.
- [ ] **Step 4: Implementar** las helpers extraídas, `BalanceError`, `_balance_leche` y los dos endpoints.
- [ ] **Step 5: Correr todos los tests** (`C:\Python314\python.exe -m unittest discover -s tests -v`). Expected: OK, incluido `test_comparar_sin_cambios`.
- [ ] **Step 6: Commit** (con "dale"): `git add app.py tests/test_balance_leche_api.py`. Ojo: `app.py` puede tener cambios ajenos; revisar el diff antes.

### Task 5: Alerta en el aviso de alertas puntuales

**Files:**
- Modify: `app.py` (`_lineas_alertas_puntuales`, `_construir_mensaje_alertas`, `_revisar_alertas_whatsapp`, ~líneas 5862-6043)
- Test: `tests/test_balance_leche_api.py` (clase `TestAlerta`)

**Interfaces:**
- Consumes: `_balance_leche` (Task 4), `balance_leche.evaluar_alertas/texto_alerta/leer_avisados/guardar_avisados` (Task 3).
- Produces: `_lineas_alerta_balance(tambo: str, registrar: bool) -> list[str]` — si no hay `MILKY_*` devuelve `[]` sin avisar; arma el balance de los últimos 21 días; evalúa; si `registrar`, guarda el `avisados` nuevo; devuelve `texto_alerta(...)`. Cualquier excepción pasa a una línea `"⚠️ Balance de leche: no se pudo revisar (...)."`.
- Produces: parámetro `registrar: bool = False` en `_lineas_alertas_puntuales(tambo, registrar=False)` y `_construir_mensaje_alertas(tambo, registrar=False)`; `_revisar_alertas_whatsapp` llama con `registrar=True`; el botón "Probar" (`api_alertas_probar_ciclo`) queda con el defecto `False`.

- [ ] **Step 1: Tests**: con `_balance_leche` reemplazado por un balance fijo con +3,2 % evaluable → `_lineas_alerta_balance("t", registrar=False)` devuelve una línea con `"+3,2 %"` y **no** escribe el registro; llamarla dos veces con `registrar=True` → la segunda devuelve `[]`; `_balance_leche` lanzando → una línea que empieza con `"⚠️ Balance de leche"`; sin `MILKY_*` → `[]`; `_lineas_alertas_puntuales` con el balance roto sigue devolviendo las demás líneas (parchar `cicla.obtener_cargas` para dar una carga de venta a 6 °C y comprobar que la línea de temperatura sale igual). Registro temporal con `patch.object(balance_leche, "_RUTA_AVISOS", ...)`.
- [ ] **Step 2: Correr y ver que fallan.**
- [ ] **Step 3: Implementar** `_lineas_alerta_balance`, el bloque en `_lineas_alertas_puntuales` (en su propio `try/except`, junto al chequeo de CICLA) y el paso del parámetro.
- [ ] **Step 4: Correr todos los tests.** Expected: OK.
- [ ] **Step 5: Commit** (con "dale"): `git add app.py tests/test_balance_leche_api.py`.

### Task 6: Pantalla "Balance de leche"

**Files:**
- Modify: `templates/index.html` (tarjeta nueva después de `comparar-card`, ~línea 1949; JS junto a `cargarComparar`, ~línea 7862; `ANCLAS` en ~15156)

**Interfaces:**
- Consumes: `GET /api/balance/leche`, `POST /api/balance/factor` (Task 4); helpers de la página: `pedirJSON`, `graficaUmbral`/`dibujar`, `css`, `fmt`, `hoyISO`, `menosDias`.
- Produces: `<section class="card" id="balance-card">`, `cargarBalance()` y el ítem `['Balance de leche', 'balance-card']` en `ANCLAS.entregas`, entre "Caudalímetro vs. comprador" y "Calidad de leche".

- [ ] **Step 1: Estructura.** La tarjeta, con el mismo encabezado que `comparar-card` (título, fechas Desde/Hasta, botón Recalcular) y, en orden: texto "Qué muestra / Cómo se calcula" (con el factor y la aclaración de que es una calibración medida, ≈ 8 % por encima de la conversión por densidad, causa desconocida); editor del factor (campo numérico + botón "Guardar factor"); gráfico; tres tarjetas del período (sala, tanque, camión, con el porcentaje entre cada par y la línea del tramo que más se desvía); tabla por día con casilleros de color.
- [ ] **Step 2: Gráfico.** Una función nueva `graficaBalance(canvasId, chartRef, dias)` con dos series (sala→tanque y tanque→camión) alrededor de 0 %, banda de ±3 % (patrón de `graficaUmbral`, con `dibujar` y colores `--accent`, `--warning`, `--critical`); puntos fuera de banda en `--critical`; los marcados como corrida, en un color distinto con su etiqueta en la tabla.
- [ ] **Step 3: Tabla.** Una fila por día: día, DelPro L, Milky L, camión, dos casilleros (sala→tanque y tanque→camión) con `ok` / `−1,4 %` / `+3,2 %` / `fecha corrida`; los días no cerrados se muestran atenuados con "sin cerrar" y sin casillero. Sin comprador, la columna y el tramo camión no aparecen y la tarjeta lo dice.
- [ ] **Step 4: Lógica.** `cargarBalance()` con las banderas de "cargando" y el botón deshabilitado del mismo patrón que `cargarComparar`; mismos manejo y texto de error; el guardado del factor llama al POST, vuelve a calcular y muestra el error del servidor si lo rechaza. Se dispara donde hoy se dispara la carga de `comparar-card` (revisar cómo se invoca desde `irAPagina`/`cargarCicla`) y con Recalcular.
- [ ] **Step 5: Verificar en el navegador.** Con un script temporal en el scratchpad (no se commitea), servir la app en el puerto 5311 con `db.run_query`, `cicla.obtener_cargas` y `_laser_traer` reemplazados por los números de los tests (incluido el par 25/26-oct y el 02/11 con +3,2 %), cookie de sesión admin firmada con `SecureCookieSessionInterface().get_signing_serializer(app.app).dumps({'rol': 'admin', 'usuario': 'x'})`. Con `preview_start` o `navigate`: `read_console_messages` sin errores; screenshot de escritorio y de ancho 375; el menú lateral muestra "Balance de leche" y salta a la tarjeta; guardar un factor inválido muestra el error; cambiar el factor recalcula. Volver el tamaño a escritorio y apagar el servidor con `Stop-Process`.
- [ ] **Step 6: Commit** (con "dale"): `git add templates/index.html`.

### Task 7: Documentación y script de medición

**Files:**
- Modify: `CLAUDE.md` (sección nueva "Balance de leche: sala → tanque → camión (07/11/2026)")
- Modify: `medir_factor_litros.py`
- Modify: `INSTALL.md` (una línea: el archivo `balance_leche_avisos.json` es estado propio de la instalación)

- [ ] **Step 1: `medir_factor_litros.py`** usa el mismo criterio de día cerrado (`balance_leche.PISO_VACAS` sobre la mediana de vacas del período) en vez de excluir solo el día de hoy; la fila del 03/11 (95 kg, 0 vacas) ya no entra a las estadísticas. Verificar que compila (`C:\Python314\python.exe -m py_compile medir_factor_litros.py`).
- [ ] **Step 2: `CLAUDE.md`**: qué mide cada escalón, el factor 1,05 y por qué es una calibración (medido en 17 días, 1,0506 acumulado, desvío 1,1 % por día; ≈ 8 % sobre 0,971), el hallazgo del corrimiento de fecha (las cargas de Milky caen entre 00:00 y 00:02, y el comprador las asigna al día anterior o al siguiente: 25 y 26/10), por qué se espera un día para alertar, el registro de avisos, y que las pruebas están en `tests/` (`C:\Python314\python.exe -m unittest discover -s tests`).
- [ ] **Step 3: Correr todos los tests.** Expected: OK.
- [ ] **Step 4: Commit** (con "dale"): `git add CLAUDE.md INSTALL.md medir_factor_litros.py`. Después del push, en SERVER-DELPRO: `git pull` + `actualizar.bat` como Administrador (cambian `.py`); medir de nuevo el factor con `medir_factor_litros.py` cuando haya más días.
