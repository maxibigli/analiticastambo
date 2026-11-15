# Panel de despliegue: pestaña «Base de datos» (actualizar DDM y dar acceso a delpro_lectura)

Diseño corto (cambio chico). Pedido del usuario el 15/11/2026.

## Para qué

Cuando llega un backup nuevo de DelPro hay que restaurarlo en SERVER-DELPRO y, después, devolverle el acceso al usuario de solo
lectura `delpro_lectura` (un restore lo pierde SIEMPRE; sin eso LactIA no lee la base, ver `CLAUDE.md`, «Dónde quedamos
(05/08/2026)»). Hoy son pasos manuales. El usuario quiere hacerlo desde el panel de despliegue (`iniciar_panel.bat`), dándole
a cada tambo la carpeta donde están sus bases.

## Qué se construye

Una pestaña nueva **«Base de datos»** en `panel_despliegue.py`, con una fila por tambo:

```
La Ponderosa   [D:\bases\ponderosa      ] [Examinar…]
   backup más nuevo: 14/11 03:10 (completo) · restaurado: 12/11     [Actualizar base] [Dar acceso a delpro_lectura]
San José       [sin carpeta]              [Examinar…]
```

- **Una carpeta por tambo, elegible** con «Examinar…» y recordada en los ajustes del panel (`panel_despliegue.json`, clave
  nueva `carpetas_bases`: `{id de tambo: ruta}`). La carpeta contiene SOLO backups de ese tambo (un `.zip`/`.7z` o las partes
  `DelPro.N.bak`); el panel toma el más nuevo y completo, con la misma lógica que ya tiene `restaurar_backup.buscar`.
- **Qué tambos aparecen** (mi decisión): los de `tambos.TAMBOS` cuya base está en la instancia SQL de este servidor, sin repetir
  la misma base (`ponderosa` y `ponderosa_local` apuntan las dos a `DDM`: sale una sola fila, con el nombre del que no es
  `_local`).
- **«Actualizar base»**: pide confirmación (dice qué backup, de qué fecha, y que la base actual NO se borra: queda como
  `<base>_ANT_<fecha>`), y entonces:
  1. si LactIA estaba corriendo, **la para** (`Despliegue.parar_lactia`); si ya estaba parada no la toca;
  2. restaura (`restaurar_backup.restaurar_tambo`: base paralela `_NUEVA`, `delpro_lectura` ya devuelto antes del
     intercambio, comprobación de que trae animales, intercambio de nombres);
  3. **vuelve a iniciar LactIA** (`iniciar_lactia`) si la había parado, **siempre**, incluso si el restore falló (en ese caso la
     base actual quedó intacta y LactIA tiene que seguir sirviendo);
  4. todo va al log del panel; un fallo se muestra como error, no se traga.
- **«Dar acceso a delpro_lectura»** (por fila): corre solo el script de `restaurar_backup.sql_usuario_lectura` sobre la base
  ACTUAL del tambo, para el caso de que la base se haya restaurado a mano (SSMS) o el acceso se haya perdido. Es idempotente.
  No para LactIA.
- Solo una acción a la vez (el panel ya tiene el candado `ocupado`); los botones se deshabilitan mientras corre.

## Lo que NO se hace

- No se reescribe la lógica de restauración (ver Enfoques).
- No se borran las bases `_ANT_<fecha>`: acumulan disco y queda un aviso en el log con las que hay; limpiarlas es decisión del
  usuario (regla «Nunca borrar»).
- No se para `iot_lavado.py` ni Cloudflare: no leen DDM.
- No hay contraseñas: SQL Server se abre con autenticación de Windows (`sqlcmd -E`), el panel ya corre como Administrador.
- No se restaura desde fuera del servidor (la instancia es `localhost\DELPRO`, la que ya usa `restaurar_backup.py`).

## Enfoques considerados

- **A. Usar `restaurar_backup.py` como biblioteca desde el panel (elegido).** Sin duplicar; ya tiene 395 líneas de pruebas.
  Cambio necesario en ese archivo: `restaurar_tambo` hoy recibe una carpeta raíz y le agrega el nombre del tambo; tiene que
  poder recibir directamente la carpeta del tambo, y el registro `restauraciones.json` se guarda en esa carpeta. La línea de
  comandos (`--carpeta`, subcarpeta por tambo) tiene que seguir funcionando igual.
- **B. Lanzarlo como proceso aparte** con una opción por tambo. Aísla un cuelgue, pero hay que leer su salida y cancelar; el
  mismo cambio en `restaurar_backup.py`. Descartado por plomería sin beneficio claro.
- **C. Reescribir dentro del panel.** Duplica 468 líneas de cuidados (reintentos del 5030, deshacer el intercambio si falla).
  Descartado.

## Restricciones y cuidados

- `restaurar_backup.py`, `restaurar_backup.bat` y `tests/test_restaurar_backup.py` hoy están **sin commitear** (de una sesión
  anterior, hoy inactiva). El trabajo los incluye y los commitea junto con el panel, con aprobación explícita del usuario.
- El panel nunca debe quedar con LactIA parada por un error: el paso 3 va en un `finally`.
- Pruebas con un `ejecutar` falso (sin SQL Server) y un `Despliegue` falso (sin tocar procesos), como las de
  `tests/test_panel_despliegue.py`: orden de las acciones, LactIA reiniciada ante un fallo, no se toca LactIA si estaba parada,
  tambo sin carpeta, carpeta sin backup completo.
- No se prueba contra el SQL Server real de producción desde acá: la primera corrida real es en SERVER-DELPRO con «solo ver»
  (ya existe en `restaurar_tambo`) y confirmación del usuario.

## Para el que construye

- Detalles de pantalla (anchos, textos exactos, cómo se muestra «completo»/«todavía bajando») los resuelve el que construye, en
  el estilo de las pestañas existentes.
- Cuántas bases `_ANT_` hay para el aviso: una consulta a `sys.databases` con `LIKE '<base>_ANT_%'`.
