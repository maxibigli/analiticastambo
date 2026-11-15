# Vigilante Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LactIA detecta sola, apenas termina cada ordeño, que salió claramente peor que lo normal (o que se cortaron los datos) y manda por los canales actuales un aviso corto con la causa probable.

**Architecture:** Un hilo liviano evalúa cada ~15 min reglas puras sobre datos por ordeño; solo si una se dispara busca la causa (consulta pesada, de a una) y la IA redacta sobre hechos medidos, con texto determinístico de respaldo. Estado (dedup, tope, retenidos) en SQLite propio; configuración en JSON propio.

**Tech Stack:** Python 3.14, Flask, SQL Server DDM vía `db.run_query`, SQLite, `unittest`, API de Anthropic (ya usada por `agente.py`).

**Spec:** `docs/superpowers/specs/2026-11-14-vigilante-design.md` (aprobado el 14/11/2026).

## Global Constraints

- Tests: `C:\Python314\python.exe -m unittest discover -s tests -p "test_vigilante_*.py" -v` desde la raíz del repo. Antes de cerrar cada tarea, correr toda la suite: `C:\Python314\python.exe -m unittest discover -s tests`.
- **Solo lectura** sobre DelPro, MixerOne y todo lo externo. Ninguna escritura.
- Constantes (propuestas del spec, a calibrar en la Tarea 7): `Z_FUERTE = 2.0` (de `veredicto_baja.py`), baja mínima **4 %**, vacas ausentes **1 %** de los kg de la base (`PCT_VACAS_AUSENTES`), comida `ALIM_RETRASO_MIN = 60`, base **7 días completos** y mínimo **3**, ordeño terminado tras **45 min** sin filas nuevas, pasada cada **15 min**, tope **3 avisos/día**, silencio **22:00–05:00**, sin datos tras **4 h**.
- El z se calcula con `diagnostico_baja._z(valor, serie)` (no una segunda versión).
- Un ordeño pertenece al día en que **empezó**. Clave de dedup: `f"{fecha_inicio}|{sesion}|{condicion}"`.
- Textos: fechas concretas con día de la semana (`veredicto_baja.fecha_larga`), nunca «ayer»; «rodeo» con el nombre que da DelPro; el aviso dice «ordeño N, día en curso», nunca habla del día entero.
- Cada chequeo en su propio `try/except`: una fuente caída es una línea de advertencia, nunca corta las demás ni el hilo.
- No se modifica `diagnostico_baja.py` ni `veredicto_baja.py` (solo se los importa).
- Sin variables de entorno nuevas (si aparece una, va en `panel_despliegue.CATALOGO`: hay un test).
- Los `.py` del repo usan en su mayoría CRLF: conservar el fin de línea del archivo que se edita.
- **Cada paso "Commit":** correr `git fetch origin` y `git status -sb`, mostrar el diff al usuario y **esperar su aprobación explícita en el chat**; recién ahí `git add` de los archivos nombrados (nunca los de otras sesiones: hoy hay `restaurar_backup.*` sin trackear que NO son de este trabajo) y commit en español terminado en `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Un ordeño que cruza la medianoche cuenta para el día en que empezó, y su dedup no se rompe al cambiar de fecha (Tareas 2 y 5).
2. Base con menos de 3 días completos (tambo recién conectado, días incompletos descartados): no avisa, no inventa (Tarea 2).
3. Reinicio del servidor a mitad del día: el dedup y el estado «sin datos» sobreviven; no se repite el aviso ni el «vuelven los datos» (Tarea 3).
4. La IA devuelve un número inventado, falla, tarda o no hay `ANTHROPIC_API_KEY`: el aviso sale igual con el texto determinístico (Tarea 4).
5. «Probar» no gasta nada: ni dedup, ni tope, ni retenidos (Tareas 3 y 5).

---

### Task 1: Medir si los ordeños llegan en tiempo real (decide si se sigue)

**Files:**
- Create: `medir_retraso_ordenos.py`, `tests/test_vigilante_medicion.py`
- Modify: `.gitignore` (agregar `retraso_ordenos.csv`, `vigilante.db`, `vigilante_config.json`)

**Interfaces:**
- Produces: `calcular_retrasos(muestras: list[dict]) -> list[dict]` con `muestras = [{"cuando": datetime, "max_fin": datetime|None, "filas": int, "segundos": float}]` (una por consulta) y resultado `[{"fin": datetime, "visto": datetime, "retraso_min": float}]` por ordeño. CLI: `python medir_retraso_ordenos.py [tambo] [--cada 300] [--analizar]`.

El retraso NO se puede medir mirando la base una vez: hay que mirarla cada pocos minutos durante un día y comparar cuándo apareció cada fila con su `EndTime`.

- [ ] **Step 1: Test que falla** `test_retraso_de_un_ordeno_es_la_diferencia_entre_cuando_se_vio_y_su_fin`: muestras a las 06:00 (`max_fin` 05:40), 06:05 (`max_fin` 05:40), 06:10 (`max_fin` 06:02, `filas` creciendo), 06:15 (`max_fin` 06:02, mismas filas); esperado: un ordeño con `fin` 06:02, `visto` 06:10, `retraso_min == 8`. Otro test: `test_sin_filas_nuevas_no_inventa_ordenos` (todas con el mismo `max_fin` → una sola entrada).
- [ ] **Step 2:** Correr y ver `FAIL` («`calcular_retrasos` no existe»).
- [ ] **Step 3:** Implementar `calcular_retrasos` (puro) y el modo registro: cada `--cada` segundos hace `SELECT MAX(sm.EndTime), COUNT(*) FROM SessionMilkYield sm WHERE sm.BeginTime >= <hoy 00:00 menos 1 día>` con `db.run_query(..., tambo=tambo)` midiendo los segundos, y agrega una línea a `retraso_ordenos.csv`. `--analizar` lee el CSV, llama a `calcular_retrasos` e imprime por ordeño el retraso, el máximo, la mediana y el tiempo medio de consulta. Estilo y encabezado de `medir_despachos_delpro.py` (solo lectura, no imprime credenciales).
- [ ] **Step 4:** Tests en verde + suite completa.
- [ ] **Step 5: Commit** («Vigilante: script que mide el retraso de los ordeños en DelPro»).
- [ ] **Step 6: PUNTO DE CONTROL con el usuario.** Pedirle que corra el registro en SERVER-DELPRO **al menos 24 h seguidas (que incluyan el ordeño de la noche)** y que traiga la salida de `--analizar`. Las Tareas 2, 3 y 4 son puras y se pueden hacer mientras tanto; **la Tarea 5 no empieza hasta tener ese resultado.** Regla del spec: hasta 60 min de retraso, seguir; entre 60 min y unas horas, los avisos dicen «con los datos al momento»; horas o ordeños partidos, **detenerse y hablar con el usuario**.

### Task 2: Reglas puras de detección

**Files:**
- Create: `vigilante_reglas.py`, `tests/test_vigilante_reglas.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Ordeno(fecha: str, sesion: int, vacas: int, kg: float, inicio: datetime, fin: datetime)` (`fecha` = día de `inicio`, ISO).
  - `ordeno_terminado(ordeno: Ordeno, ahora: datetime, quieto_min: int = 45) -> bool` (terminó si `ahora - ordeno.fin >= quieto_min` minutos, es decir, no entraron filas nuevas en ese lapso).
  - `baja_ordeno(actual: Ordeno, base: list[Ordeno]) -> dict | None` → `{"kg", "media", "z", "pct", "dias_base"}`; `None` si hay menos de 3 en `base` o no se cumplen **a la vez** `z <= -2.0` y `pct <= -4.0`.
  - `vacas_faltantes(presentes: set[int], habituales: dict[int, float], kg_base: float) -> dict | None` → `{"faltan", "kg", "pct", "numeros"}`; `habituales` = vaca → kg medio por ese ordeño; `None` si lo que pesan las ausentes es menos del 1 % de `kg_base`.
  - `sin_datos(ultima_fila: datetime | None, ahora: datetime, horas: float, ventana: tuple[int, int]) -> bool` (`ventana` = horas del día en que se suele ordeñar; fuera de ella nunca es «sin datos»).
  - `comida_atrasada(r: dict | None, hoy: date, minimo: int = 60) -> dict | None` sobre el resultado de `alimentacion_dia.ultima_comida` (`fecha`, `primera`, `habitual`, `retraso`); solo si `r["fecha"] == hoy.isoformat()` y `r["retraso"] >= minimo`.

- [ ] **Step 1: Tests que fallan.** Base `[1000, 1010, 990, 1005, 995, 1000, 1002]` kg (mismo `sesion`): `actual=900` → dispara (`pct ≈ -10`); `actual=985` → `None` (z pasa de −2 pero `pct` es −1,5 %); base `[1000, 1100, 900, 1050, 950, 1000, 1000]` y `actual=950` → `None` (z chico). `test_base_corta_no_avisa` (2 ordeños de base → `None`). `test_ordeno_que_cruza_medianoche_cuenta_para_el_dia_que_empezo` (inicio 23:50 del 13/11, fin 01:30 del 14/11 → el helper del test que arma un `Ordeno` desde `inicio` deja `fecha == "2026-11-13"`; y `ordeno_terminado` lo da por terminado a las 02:20 del 14/11, no antes de las 02:15). `test_vacas_faltantes_bajo_1_porciento_no_avisa`. `test_sin_datos_fuera_de_la_ventana_de_ordeno_no_avisa` (ventana (5, 20), ahora 02:00, última fila 20:30 → `False`; ahora 09:30, última fila 04:30, `horas=4` → `True`; `ultima_fila=None` dentro de la ventana → `True`). `test_comida_de_otro_dia_no_dispara`.
- [ ] **Step 2:** Correr y ver `FAIL`.
- [ ] **Step 3:** Implementar. Usar `diagnostico_baja._z`; las constantes del bloque «Global Constraints» van como constantes del módulo con nombre (`BAJA_MIN_PCT`, `MIN_BASE`, `QUIETO_MIN`, etc.) para que la Tarea 7 las calibre en un solo lugar.
- [ ] **Step 4:** Tests en verde + suite completa.
- [ ] **Step 5: Commit** («Vigilante: reglas puras de detección por ordeño»).

### Task 3: Estado, configuración y silencio

**Files:**
- Create: `vigilante_estado.py`, `tests/test_vigilante_estado.py`

**Interfaces:**
- Produces:
  - `class Estado(ruta: str = RUTA_DB)` (SQLite, `vigilante.db` junto al código; tablas creadas al abrir):
    `ya_avisado(clave: str) -> bool`; `registrar_aviso(clave: str, ahora: datetime) -> None`; `avisos_del_dia(dia: date) -> int` (cuenta una vez por mensaje enviado, no por clave); `registrar_envio(dia: date) -> None`; `retener(linea: str, ahora: datetime) -> None`; `tomar_retenidos() -> list[str]` (devuelve y vacía); `estado_datos() -> str` (`"ok"` o `"sin_datos"`, por defecto `"ok"`); `set_estado_datos(valor: str) -> None`.
  - `config() -> dict` con `{"activo": False, "pasada_min": 15, "tope_dia": 3, "silencio_desde": "22:00", "silencio_hasta": "05:00", "sin_datos_horas": 4}` (JSON `vigilante_config.json`, mismo patrón que `config_alertas.py`; **apagado por defecto**: se enciende cuando el usuario lo decide después de la Tarea 7); `set_config(**campos) -> None` valida (`ValueError` con mensaje en español: horas `HH:MM`, `tope_dia` 1–20, `pasada_min` 5–120, `sin_datos_horas` 1–24).
  - `en_silencio(ahora: datetime, cfg: dict) -> bool` (maneja el tramo que cruza la medianoche).

- [ ] **Step 1: Tests que fallan:** `test_dedup_sobrevive_a_un_reinicio` (abrir `Estado`, registrar, cerrar, reabrir en la misma ruta temporal → `ya_avisado` es `True`); `test_estado_datos_sobrevive_a_un_reinicio`; `test_tomar_retenidos_vacia_la_lista`; `test_avisos_del_dia_cuenta_mensajes_no_claves`; `en_silencio` en 23:00, 02:00 → `True`, 05:00, 12:00 → `False` (el límite `05:00` ya no es silencio); `set_config(tope_dia=0)` y `set_config(silencio_desde="25:00")` → `ValueError`; `config()` con archivo ausente o corrupto devuelve los valores por defecto.
- [ ] **Step 2:** Correr y ver `FAIL`.
- [ ] **Step 3:** Implementar con `sqlite3` y bloqueo por hilo; los tests usan un directorio temporal (no tocan `vigilante.db` real).
- [ ] **Step 4:** Tests en verde + suite completa.
- [ ] **Step 5: Commit** («Vigilante: estado en SQLite, configuración y horario silencioso»).

### Task 4: El aviso: texto determinístico y redacción con IA

**Files:**
- Create: `vigilante_aviso.py`, `tests/test_vigilante_aviso.py`

**Interfaces:**
- Consumes: `diagnostico_baja`/`veredicto_baja` solo por `veredicto_baja.fecha_larga(iso)`.
- Produces:
  - Un **hallazgo** es un `dict`: `{"tipo": "baja_ordeno"|"vacas_faltantes"|"comida_atrasada"|"sin_datos"|"datos_vuelven", "clave": str, "datos": dict, "causas": list[str]}`.
  - `texto_deterministico(hallazgos: list[dict], ahora: datetime, con_aviso_demora: bool = False) -> str` (un solo mensaje, máximo 6 líneas por hallazgo, cada número con su unidad, fecha con día de la semana; `con_aviso_demora` agrega «con los datos al momento»).
  - `numeros_del_texto(texto: str) -> list[float]` y `redactar(hallazgos, ahora, con_aviso_demora=False, cliente=None) -> str`: si hay `ANTHROPIC_API_KEY` pide a `agente.MODEL` un texto corto; **valida que cada número del resultado figure** (tolerancia de redondeo, mismo criterio que `agente._esta_entre`) **entre los números de `hallazgos`**; si alguno no figura, si la API falla, tarda más de 20 s o no hay clave, devuelve `texto_deterministico(...)`. `cliente` es inyectable para los tests.

- [ ] **Step 1: Tests que fallan:** `test_la_ia_con_un_numero_inventado_se_descarta` (cliente falso devuelve «bajó 37 %» cuando el dato es 10,2 → el resultado es el determinístico); `test_si_la_ia_falla_sale_el_deterministico` (cliente que levanta excepción); `test_sin_clave_no_llama_a_la_ia` (sin `ANTHROPIC_API_KEY` el cliente falso no se invoca); `test_texto_usa_fecha_con_dia_de_la_semana_y_nunca_ayer` (el texto contiene «sábado» para 14/11/2026 y no contiene «ayer»); `test_varios_hallazgos_van_en_un_solo_mensaje`; `test_sin_datos_y_datos_vuelven_tienen_su_texto`.
- [ ] **Step 2:** Correr y ver `FAIL`.
- [ ] **Step 3:** Implementar. El prompt le da a la IA solo los hallazgos en JSON, le prohíbe agregar cifras o causas que no estén, y pide seis líneas como máximo: qué pasó, cuánto, causa probable, qué mirar.
- [ ] **Step 4:** Tests en verde + suite completa.
- [ ] **Step 5: Commit** («Vigilante: texto del aviso con respaldo determinístico»).

> **Punto de control:** no seguir con la Tarea 5 sin el resultado de la Tarea 1, Step 6.

### Task 5: Datos, causas y la pasada

**Files:**
- Create: `vigilante.py`, `tests/test_vigilante.py`
- Modify (solo si hace falta, sin cambiar comportamiento): ninguno de los módulos ya verificados.

**Interfaces:**
- Consumes: `vigilante_reglas.*`, `vigilante_estado.Estado/config/en_silencio`, `vigilante_aviso.redactar`, `diagnostico_baja.sql_sesiones/sql_bocas/_del_rebano`, `alimentacion_dia.desde_el_proveedor`, `db.run_query(sql, tambo=, max_rows=diagnostico_baja.TOPE_FILAS)`.
- Produces:
  - `leer_ordenos(tambo: str, desde: date, hasta: date) -> list[Ordeno]` (usa `diagnostico_baja.sql_sesiones`; `fecha` sale del `inicio`, no de `AnimalDaily.Date`; el builder compara los kg contra la pantalla donde se ven los ordeños —probable Rendimiento Sala— y deja anotada cualquier diferencia).
  - `base_de(ordeno: Ordeno, historial: list[Ordeno]) -> list[Ordeno]`: mismo `sesion`, los 7 días anteriores, **sin el día en curso ni los días incompletos** (reusar el criterio con el que `diagnostico_baja` elige sus días de base).
  - `buscar_causas(tambo: str, ordeno: Ordeno, historial: list[Ordeno]) -> list[str]`: rodeos y vacas que faltaron en ese ordeño, bocas casi sin leche, ITH, comida del mixer; cada una como frase con su dato, ordenadas por peso con las reglas de `veredicto_baja`. Una causa que no se puede medir se omite (no se inventa).
  - `pasada(tambo: str, ahora: datetime | None = None, registrar: bool = True, enviar: Callable[[str], None] | None = None, estado: Estado | None = None, leer=leer_ordenos, causas=buscar_causas) -> dict` → `{"estado": "apagado"|"ok"|"sin_datos"|"silencio", "hallazgos": list[dict], "mensaje": str | None, "enviado": bool, "retenidos": int}`.
  - `iniciar_hilo(tambo: str, enviar: Callable[[str], None]) -> None` (bucle cada `pasada_min`; cada pasada dentro de su `try/except`; no hace nada mientras `activo` sea `False`).

Orden dentro de `pasada`: (1) apagado y `registrar` → salir; (2) leer hoy + 8 días; (3) evaluar `sin_datos` (la `ventana` sale de los ordeños de los últimos 7 días: hora mínima de inicio y hora máxima de fin) y su transición contra `estado_datos()` (cuando está en `sin_datos` **no** se analiza producción); (4) por cada ordeño terminado de hoy sin aviso previo, `baja_ordeno` y `vacas_faltantes`; una vez por día `comida_atrasada`; (5) si hay hallazgos de producción, `causas`; (6) en silencio **no se registra nada** (queda para la próxima pasada después de las 05:00); con el tope alcanzado los hallazgos van a `retener(...)` y se registran como avisados (`sin_datos` y `datos_vuelven` no cuentan para el tope); (7) enviar **un solo mensaje** y recién después registrar claves y envío. Con `registrar=False` no se escribe nada en `Estado` y no se envía.

- [ ] **Step 1: Tests que fallan** (con `leer` y `causas` falsos y un `Estado` temporal): `test_un_ordeno_malo_manda_un_solo_mensaje_y_no_se_repite_en_la_pasada_siguiente`; `test_dos_hallazgos_en_la_misma_pasada_van_en_un_mensaje`; `test_sin_datos_avisa_una_vez_y_los_datos_vuelven_avisa_una_vez` (tres pasadas seguidas sin filas → 1 envío; después filas nuevas → 1 envío de «vuelven»); `test_sin_datos_no_analiza_produccion` (la baja aparente de un día sin filas no genera «baja del ordeño»); `test_en_silencio_no_registra_y_avisa_despues_de_las_05` (misma entrada a las 02:00 → `estado == "silencio"`, `enviado False`, `avisos_del_dia == 0`; a las 05:10 → se envía); `test_tope_alcanzado_retiene_en_vez_de_mandar` (3 avisos ya hechos → `retenidos == 1`, nada enviado, `tomar_retenidos()` trae la línea); `test_probar_no_gasta_nada` (`registrar=False` dos veces devuelve el mismo mensaje y deja `Estado` vacío); `test_una_fuente_caida_no_corta_las_demas` (`causas` levanta → el aviso sale igual sin causas, con una línea de advertencia); `test_ordeno_que_cruza_medianoche_no_se_duplica_al_cambiar_la_fecha`; `test_apagado_no_hace_nada`.
- [ ] **Step 2:** Correr y ver `FAIL`.
- [ ] **Step 3:** Implementar `leer_ordenos`, `base_de`, `buscar_causas`, `pasada`, `iniciar_hilo`. Si el resultado de la Tarea 1 pidió «con los datos al momento», pasar `con_aviso_demora=True` a `redactar`.
- [ ] **Step 4:** Tests en verde + suite completa.
- [ ] **Step 5: Commit** («Vigilante: pasada que detecta, busca la causa y avisa»).

### Task 6: Integración con la app

**Files:**
- Modify: `app.py` (rutas nuevas junto a `/api/alertas/*`, ~línea 6583; `_construir_mensaje_alertas` ~6477; arranque del hilo junto a `_bucle_alertas_whatsapp` ~6580), `templates/index.html` (pestaña en ⚙ Configuración, mismo patrón que `ia_whatsapp`: botón ~3835, tarjeta ~4221, `hidden` ~15260, JS ~15397), `.gitignore` (si la Tarea 1 no lo hizo), `CLAUDE.md` (sección nueva fechada), `docs/superpowers/specs/2026-11-14-vigilante-design.md` (anotar lo medido en la Tarea 1).
- Test: `tests/test_vigilante_api.py`

**Interfaces:**
- Produces (todas `@auth.requiere_rol("admin")`): `GET/POST /api/vigilante/config` (lee/guarda `vigilante_estado.config()`, `400` con el mensaje del `ValueError`); `GET /api/vigilante/estado` (`{"estado_datos", "avisos_hoy", "ultima_pasada", "retenidos"}`); `POST /api/vigilante/probar` (llama `vigilante.pasada(..., registrar=False, enviar=None)` y devuelve `{"mensaje", "hallazgos"}`; con `"enviar": true` en el body lo manda por `_enviar_resumen_a_canales_activos`, sin registrar).

- [ ] **Step 1: Tests que fallan:** `test_config_roundtrip_y_validacion` (POST válido guarda, POST con `tope_dia: 0` → 400); `test_rutas_exigen_admin` (sin sesión admin → 401/403); `test_probar_no_gasta_dedup_ni_tope` (dos llamadas iguales devuelven el mismo mensaje); `test_mensaje_de_las_8_incluye_los_retenidos_y_probar_ciclo_no_los_consume` (`_construir_mensaje_alertas(tambo, registrar=True)` los incluye como línea «el vigilante retuvo N aviso(s)» y los vacía; con `registrar=False` los muestra pero **no** los vacía).
- [ ] **Step 2:** Correr y ver `FAIL`.
- [ ] **Step 3:** Implementar las rutas, el hook en `_construir_mensaje_alertas`, el arranque `vigilante.iniciar_hilo(tambos.DEFAULT_TAMBO, lambda t: _enviar_a_canales_activos(t))` y la pestaña «👁 Vigilante» (encendido, frecuencia de la pasada, tope por día, horario silencioso, horas de «sin datos», botón Probar que muestra el texto). Escribir en `CLAUDE.md` la sección «Vigilante» con fecha, qué mira, qué decidió el diseño y lo medido en la Tarea 1.
- [ ] **Step 4:** Tests en verde + suite completa; arrancar la app en la PC de desarrollo y verificar que la pestaña carga y guarda (captura de pantalla).
- [ ] **Step 5: Commit** («Vigilante: rutas, pestaña de Configuración y retenidos en el mensaje de las 08:00»).

### Task 7: Reproducción histórica, calibración y prueba real

**Files:**
- Create: `reproducir_vigilante.py`
- Modify: `vigilante_reglas.py` (solo las constantes), `tests/test_vigilante_reglas.py` (valores calibrados)

**Interfaces:**
- Produces: `python reproducir_vigilante.py [tambo] [--dias 60]`: recorre los últimos días ordeño por ordeño con las **mismas** `vigilante_reglas` y `leer_ordenos`/`base_de` (solo lectura, no toca `Estado`), imprime los avisos que habría mandado, cuántos por semana, y si el 07/11/2026 (sábado) habría disparado.

- [ ] **Step 1:** Escribir el script (sin lógica propia de detección; solo orquesta las funciones de la Tarea 2 y 5). Una prueba chica con ordeños falsos: un día malo entre días normales aparece en la salida.
- [ ] **Step 2: Con el usuario, en SERVER-DELPRO** (la copia local de DDM corta el 30/07): correr `python reproducir_vigilante.py ponderosa --dias 60` y traer la salida.
- [ ] **Step 3: Calibrar.** Objetivo: **≤ 1 aviso por semana en condiciones normales Y que el 07/11/2026 sí dispare.** Si chocan, **cede el límite semanal, no la detección**: bajar los umbrales hasta que el 07/11 salte; si ningún ordeño suelto alcanza, sumar la comparación acumulada del día (litros hasta esa hora contra los mismos ordeños de la base) como regla nueva en `vigilante_reglas.py` con su test. **Informar al usuario cuántos avisos por semana resulta y dejar que decida.**
- [ ] **Step 4:** Tests en verde + suite completa. Commit («Vigilante: reproducción histórica y umbrales calibrados»).
- [ ] **Step 5: Prueba real de punta a punta (con el usuario).** Deploy en SERVER-DELPRO (`git pull`, `actualizar.bat` como Administrador, verificar con `Get-CimInstance` que hay un solo `servidor.py` nuevo), encender el vigilante en la pestaña, apretar «Probar» con «enviar» y comprobar que **llega un aviso real por un canal**. Las alertas anteriores se probaron solo con mocks; esta no puede quedar así. Anotar el resultado en `CLAUDE.md`.
