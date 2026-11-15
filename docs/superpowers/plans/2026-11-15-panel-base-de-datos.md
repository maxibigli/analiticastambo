# Panel de despliegue: pestaña «Base de datos» — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desde el panel de despliegue, elegir una carpeta de backups por tambo y, con un botón, restaurar la base en SERVER-DELPRO (parando y reiniciando LactIA solo) y devolverle el acceso a `delpro_lectura`.

**Architecture:** `restaurar_backup.py` (hoy sin commitear) se usa como biblioteca: se le agrega «carpeta propia por tambo», un resumen de lo que hay en la carpeta y dos ayudas SQL. `Despliegue` (en `panel_despliegue.py`) orquesta parar → restaurar → iniciar con `finally`. `Panel` suma la pestaña con una fila por tambo.

**Tech Stack:** Python 3.14, tkinter, `sqlcmd -E` (autenticación de Windows), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-15-panel-base-de-datos-design.md`

## Global Constraints

- Tests: `C:/Python314/python.exe -m unittest discover -s tests` desde `C:\Users\MAXI\CLAUDE\delpro-analitica` (`python3` no existe; baseline 688 pruebas OK).
- **Rama `panel-base-datos` en la carpeta principal, NO en un worktree**: `restaurar_backup.py`, `restaurar_backup.bat` y `tests/test_restaurar_backup.py` están sin trackear y no viajan a un worktree. No tocar nada más sin trackear que aparezca.
- Commits solo con la aprobación del usuario (el diseño aprobado incluye commitear los `restaurar_backup.*`); `git add` solo de archivos nombrados, mirando `git diff` antes; mensajes en español terminados en `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Sin push.
- Nunca borrar bases ni archivos: las `<base>_ANT_<fecha>` se conservan. Sin contraseñas ni secretos en `panel_despliegue.json` (no es privado).
- Textos en español rioplatense. Los `print`/log de `restaurar_backup.py` van en ASCII (consola cp1252: nada de «⚠» ni «→»).
- Los tests no tocan SQL Server real ni procesos reales: `ejecutar`, `restaurar`, `parar_lactia`/`iniciar_lactia` van falsos.
- `Panel` guarda ajustes llamando `guardar_ajustes(self.aj)` (nombre de módulo, para poder parchearlo): los tests NUNCA deben escribir el `panel_despliegue.json` real.

## Review Focus

Entradas/condiciones que el diseño implica y ningún test obvio cubre; cada una tiene su test en la tarea dueña del código:

1. El restore falla a mitad → LactIA se vuelve a iniciar y se informa el error (Task 3: `test_si_el_restore_falla_igual_reinicia_lactia`).
2. LactIA ya estaba parada → el panel no la arranca (Task 3: `test_si_lactia_estaba_parada_no_la_toca`).
3. No hay backup nuevo (carpeta vacía, archivo todavía bajando, o ya restaurado) → LactIA NO se para (Task 3: `test_sin_backup_o_al_dia_no_para_lactia`).
4. La misma base aparece en dos tambos (`ponderosa` y `ponderosa_local`) → una sola fila (Task 2).
5. Carpeta sin configurar o que ya no existe → error claro, nada se toca (Task 3).

---

### Task 1: `restaurar_backup.py` — carpeta propia, resumen y ayudas SQL

**Files:**
- Modify: `restaurar_backup.py` (`restaurar_tambo`; funciones nuevas junto a `sql_usuario_lectura`)
- Test: `tests/test_restaurar_backup.py` (clases nuevas al final, antes del `if __name__`, si lo hay)
- Commit también (sin cambios previos, en un commit aparte): `restaurar_backup.bat`

**Interfaces:**
- Produces:
  - `restaurar_tambo(tambo, base, carpeta, ejecutar, ..., carpeta_propia=False)`: con `carpeta_propia=True`, `carpeta` ES la carpeta de los backups del tambo (no se le agrega `/<tambo>`), y `restauraciones.json` y `_trabajo` quedan ahí. Con `False` todo igual que hoy (la línea de comandos no cambia).
  - `resumen_carpeta(carpeta_tambo, tambo, ahora=None, forzar=False) -> dict` con `estado` (`"sin_backup"` | `"al_dia"` | `"nuevo"`), `backupdate` (datetime | None), `nombre` (str | None, del archivo elegido), `restaurado` (ISO | None, el `backupdate` ya restaurado según el registro), `saltados` (list[str], `"<archivo>: <motivo>"`). No levanta excepciones por carpeta inexistente: da `sin_backup`.
  - `bases_anteriores(base, ejecutar) -> list[str]`: nombres `<base>_ANT_*` ordenados.
  - `dar_acceso_lectura(base, ejecutar) -> None`: corre `sql_usuario_lectura(base)`; `RestauracionError` si el nombre de base no es válido.

- [ ] **Step 1: Línea base.** `git switch -c panel-base-datos`; correr `C:/Python314/python.exe -m unittest tests.test_restaurar_backup` (Expected: OK). Commit aparte con los tres archivos tal cual están: `git add restaurar_backup.py restaurar_backup.bat tests/test_restaurar_backup.py` (+ el spec y este plan en `docs/superpowers/`) → «Restaurar backup: script y pruebas (de una sesión anterior, sin cambios)».

- [ ] **Step 2: Tests que fallan.** En `tests/test_restaurar_backup.py` (reusar `Base`, `_zip`, `_tocar`, `SqlFalso`, `XML`, `AHORA`):
  - `TestCarpetaPropia.test_usa_la_carpeta_dada_sin_agregar_el_nombre_del_tambo`: el zip está directo en `self.raiz`; `restaurar_tambo("laponderosa","DDM",self.raiz,sql,ahora=AHORA,log=lambda *_:None,carpeta_propia=True)` → `estado == "restaurado"`, existe `self.raiz/"restauraciones.json"` con la clave `laponderosa`.
  - `test_sin_carpeta_propia_busca_en_la_subcarpeta`: el zip en `self.tambo` y `carpeta_propia` omitido → `"restaurado"` (guarda el comportamiento actual).
  - `TestResumen`: `sin_backup` con carpeta vacía y con carpeta inexistente (sin excepción); `nuevo` con el zip de `XML 20261105 011231` → `backupdate == datetime.datetime(2026,11,5,1,12,31)`, `nombre == "b.zip"`; `al_dia` después de restaurarlo (mismo `ahora`); `forzar=True` sobre ese caso → `nuevo`; un zip tocado hace 10 s → `sin_backup` y `len(saltados) == 1`.
  - `TestAyudas.test_bases_anteriores_filtra_y_ordena`: `SqlFalso(existentes={"DDM","DDM_ANT_20261105_0600","DDM_ANT_20261101_0600","OTRA"})` → `["DDM_ANT_20261101_0600","DDM_ANT_20261105_0600"]` (el falso ignora el `WHERE`: el filtro también va en Python). `test_dar_acceso_lectura_corre_el_script_sobre_esa_base`: `sql.hubo("USE [DDM]")` y `sql.hubo("delpro_lectura")`. `test_dar_acceso_rechaza_nombres_raros`: `"DDM; DROP"` → `RestauracionError`.

- [ ] **Step 3: Verlos fallar.** `C:/Python314/python.exe -m unittest tests.test_restaurar_backup` → los nuevos fallan (`TypeError`/`AttributeError`), los viejos siguen verdes.

- [ ] **Step 4: Implementar** en `restaurar_backup.py`: el parámetro `carpeta_propia` en `restaurar_tambo` (define `carpeta_tambo` y la carpeta donde van `_leer_registro`/`_guardar_registro`; el resto igual); `resumen_carpeta` apoyada en `buscar`, `elegir_ultimo`, `_leer_registro` (misma regla de «al día» que `restaurar_tambo`); `bases_anteriores` con `LIKE N'<base>\_ANT\_%' ESCAPE '\'` (el nombre pasa por `_nombre`) y filtro `startswith(f"{base}_ANT_")`; `dar_acceso_lectura`.

- [ ] **Step 5: Verlos pasar.** Mismo comando → OK. Después la suite completa → OK.

- [ ] **Step 6: Commit** (con aprobación): `git add restaurar_backup.py tests/test_restaurar_backup.py` → «Restaurar backup: carpeta propia por tambo, resumen de la carpeta y ayudas SQL».

---

### Task 2: Ajustes del panel y qué tambos aparecen

**Files:**
- Modify: `panel_despliegue.py` (`AJUSTES_DEFECTO`, `cargar_ajustes`, función nueva `tambos_del_servidor`)
- Test: `tests/test_panel_base_datos.py` (nuevo)

**Interfaces:**
- Produces:
  - `AJUSTES_DEFECTO["carpetas_bases"] = {}` (`{id de tambo: ruta}`).
  - `tambos_del_servidor(config=None, equipo=None) -> list[dict]` con `{"id", "nombre", "base"}`. `config`: `{id: conexión}` (por defecto `{i: tambos.conexion(i) for i in tambos.TAMBOS}`, import adentro de la función); `equipo`: nombre de esta PC (por defecto `socket.gethostname()`).

- [ ] **Step 1: Tests que fallan** (`tests/test_panel_base_datos.py`, `sys.path.insert` al directorio padre como en `tests/test_panel_despliegue.py`):
  - `test_carpetas_bases_no_se_comparte_entre_cargas`: `a = pd.cargar_ajustes(ruta_inexistente)`; `a["carpetas_bases"]["x"] = "y"`; `pd.cargar_ajustes(ruta_inexistente)["carpetas_bases"] == {}` y `pd.AJUSTES_DEFECTO["carpetas_bases"] == {}`.
  - `test_carpetas_bases_ida_y_vuelta_y_basura`: guarda `{"ponderosa": r"D:\bases\p"}` y lo lee igual; un JSON con `"carpetas_bases": ["no", "es", "dict"]` vuelve a `{}`.
  - `test_tambos_del_servidor` con `config` sintética (`ponderosa`: server `SERVER-DELPRO\DELPRO` / DDM; `ponderosa_local`: `localhost\DELPRO` / DDM; `lamartina_local`: `localhost\DELPRO` / DDM_LAMARTINA; `remoto`: `192.168.1.20\DELPRO` / DDM; `sin_base`: sin `database`): con `equipo="SERVER-DELPRO"` los ids son `["ponderosa","lamartina_local"]` (mismo `DDM` dos veces: gana el que no termina en `_local`); con `equipo="MAXI-PC"` son `["ponderosa_local","lamartina_local"]`; `localhost,1433` y `LOCALHOST` también son locales; el orden es el de `config`.

- [ ] **Step 2: Verlos fallar** — `C:/Python314/python.exe -m unittest tests.test_panel_base_datos` (`KeyError`/`AttributeError`).

- [ ] **Step 3: Implementar.** `cargar_ajustes` hoy copia solo las listas (`list(v)`): hay que copiar también los `dict` (`dict(v)`), si no el dict por defecto queda compartido. Local = host (lo anterior a `\` o `,`, en minúsculas) en `{"localhost","127.0.0.1",".","(local)", equipo.lower()}`. Dedup por `base.lower()` quedándose con el id que no termina en `_local` (si hay solo `_local`, ese).

- [ ] **Step 4: Verlos pasar** + `tests.test_panel_despliegue` (que `TestAjustes` siga verde) → OK.

- [ ] **Step 5: Commit:** `git add panel_despliegue.py tests/test_panel_base_datos.py` → «Panel de despliegue: carpetas de bases por tambo y qué tambos aparecen».

---

### Task 3: `Despliegue.actualizar_base`, `dar_acceso_lectura`, `info_base`

**Files:**
- Modify: `panel_despliegue.py` (métodos nuevos en `Despliegue`, después de `actualizar`)
- Test: `tests/test_panel_base_datos.py`

**Interfaces:**
- Consumes: Task 1 (`resumen_carpeta`, `restaurar_tambo(..., carpeta_propia=True)`, `bases_anteriores`, `dar_acceso_lectura`, `sqlcmd`, `INSTANCIA`, `RestauracionError`) — `import restaurar_backup as rb` DENTRO de los métodos; Task 2 (`aj["carpetas_bases"]`).
- Produces (inyectables para los tests: `resumen`, `restaurar`, `anteriores`, `ejecutar`; por defecto las de `rb` y `rb.sqlcmd(rb.INSTANCIA)`):
  - `info_base(tambo_id, resumen=None) -> dict`: `{"estado": "sin_carpeta"}` | `{"estado": "carpeta_inexistente", "carpeta": ruta}` | lo de `resumen_carpeta(carpeta, tambo_id)`.
  - `actualizar_base(tambo_id, base, solo_ver=False, forzar=False, *, ejecutar=None, resumen=None, restaurar=None, anteriores=None) -> bool`.
  - `dar_acceso_lectura(base, ejecutar=None) -> bool`.

**Flujo de `actualizar_base`** (decidido en el diseño): carpeta configurada y existente, si no `ErrorPanel("… carpeta …")` · `resumen` → `sin_backup`: loguea los `saltados` y devuelve `False`; `al_dia`: loguea y devuelve `True`; en ambos NO se toca LactIA · `solo_ver`: llama `restaurar(..., solo_ver=True)`, sin confirmar ni parar nada, devuelve `True` · si no, `self.confirmar(...)` con el texto de qué backup (`dd/mm/aaaa hh:mm`), qué base, que LactIA se para y que la base actual queda como `<base>_ANT_<fecha>`; negado → `"Cancelado"`, `False` · `habia = bool(self._del_script(self.aj["script_lactia"]))`; si `habia` y `parar_lactia()` falla → no se restaura, `False` · `try: restaurar(tambo_id, base, carpeta, ejecutar, forzar=forzar, log=self.log, carpeta_propia=True)` / `except rb.RestauracionError` → loguea `ERROR: …`, resultado `False` / `finally: if habia: iniciar_lactia()` · tras un éxito, loguea cuántas `_ANT_` hay (si falla el conteo, solo lo dice) · devuelve `restauró y (no había o inició)`.

- [ ] **Step 1: Tests que fallan** (`TestActualizarBase`; `d = pd.Despliegue(dict(pd.AJUSTES_DEFECTO, carpetas_bases={"ponderosa": carpeta_tmp}), entorno=mock.Mock(), log=log.append, confirmar=lambda m: True, carpeta=tmp)`; `d._del_script`, `d.parar_lactia`, `d.iniciar_lactia` reemplazados por falsos que anotan en una lista `orden`; `resumen`/`restaurar`/`anteriores` falsos inyectados):
  - `test_para_restaura_y_reinicia_en_ese_orden`: LactIA corriendo → `orden == ["parar","restaurar","iniciar"]`, devuelve `True`, `restaurar` recibió `carpeta_propia=True`, la carpeta y `"DDM"`; el mensaje de `confirmar` contiene `14/11/2026 03:10` y `DDM`.
  - `test_si_el_restore_falla_igual_reinicia_lactia`: `restaurar` levanta `rb.RestauracionError("falla simulada")` → `orden == ["parar","restaurar","iniciar"]`, devuelve `False`, el log contiene `falla simulada`. Variante con `RuntimeError`: también reinicia y la excepción se propaga.
  - `test_si_lactia_estaba_parada_no_la_toca`: `_del_script` → `[]` → `orden == ["restaurar"]`, devuelve `True`.
  - `test_sin_backup_o_al_dia_no_para_lactia`: `sin_backup` → `False`, y `al_dia` → `True`; en ambos `orden == []`.
  - `test_si_no_se_puede_parar_lactia_no_se_restaura`: `parar_lactia` devuelve `False` → `restaurar` no se llamó, devuelve `False`.
  - `test_confirmacion_negada_no_hace_nada`: `confirmar → False` → `orden == []`, devuelve `False`, el log contiene `Cancelado`.
  - `test_solo_ver_no_confirma_ni_toca_lactia`: `solo_ver=True` → `restaurar` llamado con `solo_ver=True`, `confirmar` no se llamó, `orden == ["restaurar"]` sin parar/iniciar.
  - `test_sin_carpeta_o_carpeta_inexistente_da_error_claro`: sin clave y con una ruta que no existe → `pd.ErrorPanel` con «carpeta» en el mensaje; `orden == []`.
  - `test_avisa_cuantas_bases_anteriores_quedan`: `anteriores` → 2 nombres → el log los menciona (cantidad 2) y no hay ninguna sentencia de borrado.
  - `test_dar_acceso_lectura`: `ejecutar` falso anota; `d.dar_acceso_lectura("DDM", ejecutar)` → `True`, la sentencia lleva `USE [DDM]`, `parar_lactia` no se llamó; si `ejecutar` levanta `rb.RestauracionError` → `False` y queda en el log.
  - `test_info_base`: sin clave → `sin_carpeta`; ruta inexistente → `carpeta_inexistente`; carpeta real + `resumen` falso → lo que devuelve el falso.

- [ ] **Step 2: Verlos fallar** (`AttributeError: 'Despliegue' object has no attribute 'actualizar_base'`).
- [ ] **Step 3: Implementar** los tres métodos según el flujo de arriba.
- [ ] **Step 4: Verlos pasar** + `tests.test_panel_despliegue` → OK.
- [ ] **Step 5: Commit:** `git add panel_despliegue.py tests/test_panel_base_datos.py` → «Panel de despliegue: actualizar la base parando y reiniciando LactIA».

---

### Task 4: La pestaña «Base de datos» en el panel

**Files:**
- Modify: `panel_despliegue.py` (`Panel._armar`, `_armar_base_datos`, `_bombear`, `_botones`, `_limpiar_log`)
- Test: `tests/test_panel_base_datos.py` (`TestPantallaBase`, mismo patrón que `TestPantalla` de `tests/test_panel_despliegue.py:465`: clave de registro temporal copiada del helper `clave()` de `_ConRegistroTemporal` (líneas 38-53), `difundir_cambio` parcheado, `skipTest` si no hay tkinter; además `mock.patch.object(pd, "guardar_ajustes")` y `mock.patch.object(pd, "tambos_del_servidor", return_value=[…2 tambos…])` durante la construcción)

**Interfaces:**
- Consumes: Task 2 (`tambos_del_servidor`), Task 3 (`Despliegue.info_base/actualizar_base/dar_acceso_lectura`), `Panel.correr`, `Panel.cola`.
- Produces: `Panel.filas_base: dict[id, {"var", "info", "actualizar", "acceso"}]` (StringVar de la carpeta, Label de info, los dos botones, todos dentro de `botones_accion`); `Panel.var_solo_ver` (BooleanVar); `_elegir_carpeta_base(id)`, `_actualizar_base(id)`, `_dar_acceso(id)`, `_refrescar_bases()` (hilo → `cola.put(("base", id, info))`), `_pintar_info_base(id, info)`; un segundo log `txt_base` que recibe las mismas líneas.

**Pantalla:** una fila por tambo (nombre, entrada de carpeta de solo lectura, «Examinar…», línea de info, «Actualizar base», «Dar acceso a delpro_lectura»); arriba una casilla «Solo ver (no cambia nada)» y «Buscar backups»; abajo el log propio. Textos de info: `sin carpeta` · `la carpeta no existe` · `sin backup completo` · `backup más nuevo: dd/mm hh:mm (<archivo>) · restaurado: dd/mm` / `… · ya restaurado`. Al armar la pestaña y al elegir carpeta se llama `_refrescar_bases()`.

- [ ] **Step 1: Tests que fallan:**
  - `test_hay_una_fila_por_tambo`: `set(panel.filas_base) == {ids parcheados}`.
  - `test_elegir_carpeta_la_guarda_y_cancelar_no`: con `tkinter.filedialog.askdirectory` parcheado devolviendo una carpeta temporal → `panel.aj["carpetas_bases"][id] == os.path.normpath(carpeta)` y `guardar_ajustes` llamado una vez; devolviendo `""` → sin cambios y `guardar_ajustes` sin llamar.
  - `test_actualizar_base_usa_correr_con_la_base_y_solo_ver`: `panel.correr` parcheado captura `(titulo, fn)`; `panel._actualizar_base(id)` → el título contiene «Actualizar base»; ejecutar `fn()` con `panel.d.actualizar_base` parcheado → llamado con `(id, base, solo_ver=False)`; con `panel.var_solo_ver.set(True)` → `solo_ver=True`. `_dar_acceso(id)` análogo con `panel.d.dar_acceso_lectura(base)`.
  - `test_los_botones_de_base_se_deshabilitan_mientras_corre`: `panel._botones(False)` → los botones de cada fila quedan `disabled`; con `True`, `normal`.
  - `test_la_info_de_la_fila_se_pinta`: `_pintar_info_base(id, {"estado":"nuevo","backupdate":datetime(2026,11,14,3,10),"nombre":"b.zip","restaurado":"2026-11-12T06:00:00","saltados":[]})` → el texto del Label contiene `14/11` y `12/11`; con `{"estado":"sin_carpeta"}` contiene `sin carpeta`.
  - `test_buscar_backups_pinta_lo_que_trae_el_hilo`: `panel.d.info_base` parcheado; `_refrescar_bases()`, bombear con `panel.raiz.update()` (como el test del log, hasta 20 vueltas) → el Label cambia.
  - `test_el_log_llega_a_las_dos_pestanas`: `panel._log("x")` → aparece en `panel.txt` y `panel.txt_base`.

- [ ] **Step 2: Verlos fallar** (`AttributeError: … 'filas_base'`).
- [ ] **Step 3: Implementar.** `_armar` agrega `self._armar_base_datos(ttk.Frame(self.cuaderno))` después del Servidor. `_bombear`: la rama `"log"` escribe en `txt` y `txt_base`; nueva rama `("base", id, info)` → `_pintar_info_base`. `_limpiar_log` limpia las dos. Elegir carpeta: `tkinter.filedialog.askdirectory(parent=self.raiz, initialdir=carpeta actual o CARPETA)` (importar adentro, para poder parchearlo). Un `_actualizar_base` con la casilla tildada pasa `solo_ver=True`.
- [ ] **Step 4: Verlos pasar** + `tests.test_panel_despliegue.TestPantalla` + suite completa → OK.
- [ ] **Step 5: Mirada a ojo, una sola vez:** abrir el panel con `python panel_despliegue.py` (pide permisos de Administrador) y ver la pestaña; no pulsar «Actualizar base» sin «Solo ver» en esta PC (restauraría sobre la instancia local).
- [ ] **Step 6: Commit:** `git add panel_despliegue.py tests/test_panel_base_datos.py` → «Panel de despliegue: pestaña Base de datos».

---

### Task 5: Documentación y cierre

**Files:**
- Modify: `CLAUDE.md` (sección corta nueva «Panel de despliegue: actualizar la base de datos desde una carpeta (15/11/2026)»)

- [ ] **Step 1:** Escribir la sección: qué hace la pestaña, que cada tambo tiene su carpeta, que LactIA se para y reinicia sola (y no si ya estaba parada ni si no hay backup nuevo), que las `_ANT_` no se borran y ocupan disco, que «Solo ver» existe y que la primera corrida real en SERVER-DELPRO se hace con él, y que `restaurar_backup.bat` sigue funcionando para `C:\backupsdelpro\<tambo>\`.
- [ ] **Step 2:** `C:/Python314/python.exe -m unittest discover -s tests` → OK (688 + las nuevas, 0 fallas).
- [ ] **Step 3: Commit:** `git add CLAUDE.md` → «CLAUDE.md: panel de despliegue, pestaña Base de datos».
- [ ] **Step 4 (lo hace el usuario en SERVER-DELPRO):** `git pull`, abrir el panel, elegir la carpeta del tambo, tildar «Solo ver» y pulsar «Actualizar base»: debe decir qué backup usaría y no tocar nada ni parar LactIA; después, sin «Solo ver», la actualización real.
