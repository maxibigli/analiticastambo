# Analítica DelPro

Aplicación web para consultar la base **DDM** de DelPro (SQL Server, instancia
`localhost\DELPRO`), hacer preguntas en lenguaje natural y generar gráficas y
reportes para la toma de decisiones del tambo.

## Requisitos

- Windows con la instancia SQL Server `DELPRO` corriendo (autenticación de Windows).
- Driver **ODBC Driver 18 for SQL Server** (ya instalado en esta máquina).
- Python 3.12 con `flask`, `pyodbc` y `anthropic` (`pip install -r requirements.txt`).

## Ejecutar

**Producción** (servidor waitress, es el que hay que usar en el tambo):

```powershell
python servidor.py     # o doble clic en iniciar.bat
```

**Desarrollo** (servidor de Flask, solo para probar cambios):

```powershell
python app.py
```

Luego abrir <http://localhost:5310>.

> 📦 **Para instalar en una PC nueva**, seguí la guía paso a paso: **[INSTALL.md](INSTALL.md)**
> (requisitos, dependencias, usuario de solo lectura, arranque automático y acceso desde la red).

## Tambos (multi-tambo)

En el encabezado hay un listbox **Tambo** que filtra los datos por tambo. Cada
tambo de DelPro es una base DDM independiente. Hoy está cargado **La Ponderosa**.

Para **agregar otro tambo** (p. ej. Don Germán) editá `tambos.py` y copiá el
bloque de plantilla, completando servidor y base:

```python
"don_german": {
    "nombre": "Don Germán",
    "server": "192.168.1.20\\DELPRO",   # IP o nombre de la PC del tambo en la red
    "database": "DDM",
    "auth": "windows",                   # o "sql" con user/password
},
```

Al reiniciar la app, el nuevo tambo aparece solo en el listbox. Requiere que la
base de ese tambo sea accesible por red desde esta PC (o restaurar su `.bak` aquí
como otra base). No hace falta tocar ningún otro archivo.

## Ordeño en la rotativa (tiempo real)

Sección al tope de la página que muestra las vacas de la **última sesión de
ordeño**, ordenadas por posición en la rotativa, con: posición, RP (número),
grupo, días en leche, permiso de ordeño, tratamiento, producción (kg), estado
reproductivo y si debe apartarse. Las vacas con alerta (sin permiso "NO ordeñar",
con tratamiento, o a apartar) se resaltan; hay filtro "Solo con alerta" y búsqueda
por RP. Se refresca solo cada 25 segundos.

**Vista Rotativa (gráfica):** simulación circular de la rotativa de 80 puestos.
Estilo **DeLaval**: cada puesto es una **cuña (gajo)** con el **RP de la vaca
adentro** y coloreada por estado (verde = ordeñando OK, rojo = alarma, naranja =
retirada del equipo, amarillo = tratamiento/apartar, gris = vacío). Una **barra
blanca** en el borde externo de cada cuña muestra el progreso de ordeño (kg reales
vs esperados).

A la derecha, un **panel con pestañas Alarmas / Ordeño** (como el central de
DeLaval): "Alarmas" lista Plaza · Animal · Alarma usando los flags oficiales de
`CMSMilkYield` (`ConductivityAlarm`, `LowYieldAlarm`, `BloodAlarm`, `ForcedRetract`)
más la **baja producción** (real vs esperada, con kg y %); "Ordeño" lista la
producción de las 80 plazas. Debajo, las tarjetas de detalle del rango en foco. Con el control
"Ver puestos … a …" se elige un rango (por defecto **60 a 70**, donde está la
persona que aplica tratamientos): esos puestos se resaltan en el aro y su
información detallada aparece en tarjetas a la derecha. Cada tarjeta muestra RP,
grupo, días, kg, estado reproductivo y alertas; y si la vaca tiene un **tratamiento
activo**, un recuadro "💊 A tratar" con el tratamiento a aplicar, el diagnóstico,
el fin de tratamiento, y los períodos de **retiro de leche** y **no faenar** (con
los días restantes) — todo lo que el operario necesita ver del animal a tratar.
El botón **Tabla** vuelve a la vista de lista completa. Cada tarjeta incluye:
RP, grupo, días, producción, estado reproductivo, **último parto**, **células
somáticas** (con alerta de color: ámbar >200, rojo >400) y **conductividad**;
**último tratamiento** (histórico, 24 meses); chips de **sin permiso**,
**apartar**, **2ª vuelta** y **fin anormal**; y —si hay tratamiento activo— el
recuadro "💊 A tratar" con droga, dosis, vía de aplicación, diagnóstico y los
retiros de leche y carne.

**Orientación del gráfico** — se ajusta con dos constantes en `index.html`:
`ROT_INICIO_DEG` (ángulo donde se dibuja el puesto 1: 0°=derecha, 90°=abajo,
180°=izquierda, 270°=arriba) y `ROT_SENTIDO` (`+1` horario, `-1` antihorario).

## Incidencias del equipo por puesto (detección de unidades falladas)

La tabla **`CMSMilkYield`** guarda, por cada ordeño, las incidencias reales del
equipo: `Slips` (deslizamientos de pezoneras), `KickOffs` (patadas),
`Blocks` (bloqueos) y `NoOfReattaches` (recolocaciones), además de flujos
(`AverageFlow`, `PeakFlow`) y alarmas. Se une a la pasada por
`CMSMilkYield.MilkingDeviceVisit = MilkingDeviceVisit.OID`, lo que permite
atribuir cada incidencia a su **puesto** (`Place`).

La app suma esas incidencias **por puesto en las últimas 24 h** (`DIA_HORAS` en
`ordeno.py`) y las muestra:

- **Resumen del día** arriba del gráfico: totales por tipo y los puestos a revisar.
- **Chips por tipo** en la tarjeta de cada puesto (ej. "desliz ×58", "bloqueos ×14").
- **Anillo en el gráfico**: ámbar o rojo según cuánto se desvía el puesto.

> **Umbral relativo, no fijo:** en una rotativa sana es normal que cada puesto
> tenga varios deslizamientos por día (mediana ~9 en La Ponderosa). Por eso el
> marcado no usa un número fijo sino la **desviación respecto de la mediana** de
> los 80 puestos: ámbar a partir de 1,5× la mediana y rojo a partir de 2,5×. Así
> solo se resaltan las unidades realmente fuera de lo normal.

**Toggle "En vivo (girando ahora)"** (en la vista Tabla): cambia entre dos vistas:

- **Sesión completa** (por defecto): todas las vacas del último ordeño.
- **En vivo:** solo las vacas que están sobre la plataforma **girando en este
  momento** (la última visita de cada posición dentro de la última vuelta,
  ventana de 20 min). Es la vista para mirar durante el ordeño. Se refresca cada
  8 segundos. Si no hay un ordeño en curso (el último dato es viejo), avisa
  "No hay ordeño en curso" y muestra la última vuelta registrada.

**Sobre el "tiempo real":** la consulta toma siempre el ordeño más reciente
registrado. Un cartel indica el estado:

- **EN VIVO** (rojo) si el último dato tiene menos de 20 minutos → la app está
  viendo un ordeño en curso.
- **Último ordeño** (gris) con la fecha y "hace cuánto" si el dato es más viejo.

⚠️ **Importante:** para que sea realmente en vivo, la app debe apuntar a la base
DDM que DelPro **escribe durante el ordeño** (la base de producción de la PC
conectada a la rotativa). Si apunta a una copia/respaldo, mostrará el último
ordeño que tenga esa copia. En esta instalación, el dato más reciente es del
2026-07-17 — es decir, esta base es una copia que no se está actualizando en
vivo. Ver `ordeno.py` para la ventana de sesión (3 h) y la definición de cada campo.

## Problemas podales — cámaras en la rotativa (EXPERIMENTAL)

Módulo para detectar renguera con dos cámaras que miran el mismo corredor de
**salida** de la rotativa:

- **Cámara "posición"**: vista amplia del punto de salida. No reconoce a la
  vaca por imagen — solo confirma el instante en que algo cruza por ahí.
- **Cámara "marcha"**: vista lateral del mismo corredor, mide cómo camina
  (curvatura del lomo mientras cruza el cuadro) y da un **score 1 (normal) a
  5 (renguera severa)**.

La identidad del animal **no** sale de reconocerlo por imagen (caravana,
RFID visual): sale de cruzar el instante detectado por cámara con
`CMSMilkYield.MilkConfirmTime` de DelPro, que ya sabe qué vaca terminó de
ordeñarse y cuándo (`podal.resolver_rp`). Los scores se guardan en una base
SQLite **local** (`podal.db`, no va al repo — la base DDM de DelPro es de
solo lectura para esta app, no se le puede escribir nada).

⚠️ **Es un heurístico v1, sin calibrar**: no hay cámaras instaladas todavía
en ningún tambo, así que los umbrales (`podal_vision.py`) salen de la
bibliografía de "locomotion scoring" (lomo arqueado al caminar = señal
clásica de dolor podal), no de casos reales de este rodeo. Antes de confiar
en los scores hay que validarlos a campo (comparar contra el diagnóstico del
veterinario), igual que se hizo con el índice experimental de `salud.py`.

**Para activar las cámaras de un tambo** (por defecto están deshabilitadas):

```powershell
setx PODAL_CAM_POSICION_PONDEROSA "rtsp://usuario:clave@192.168.1.30/stream1"
setx PODAL_CAM_MARCHA_PONDEROSA   "rtsp://usuario:clave@192.168.1.31/stream1"
```

y agregar el id del tambo a `PODAL_TAMBOS` en `config_podal.py`. Con eso
configurado, en **Salud del rodeo → Problemas podales** aparece:

- Estado de conexión de cada cámara y un botón para iniciar/detener la captura.
- **Snapshot en vivo** de las dos cámaras (se actualiza solo cada 4 s), con
  un indicador "detectando…" mientras un animal está cruzando.
- **Actividad en tiempo real**: últimas pasadas detectadas, identificadas o
  no, con su score y motivo — se actualiza sola sin recargar la página.
- **Vacas con alerta**: promedio reciente de score vs. el historial propio de
  cada vaca, con gráfico de tendencia individual (buscar por RP).

## Tareas pendientes (estilo To-Do de DelPro)

DelPro no guarda las tareas en una tabla (la tabla `Task` está vacía): las
**deriva** del estado reproductivo y sanitario del rodeo. La app reconstruye las
categorías principales en tarjetas con contador; al hacer clic se ve el listado de
animales y se puede exportar a CSV. Categorías:

- **Chequeos de preñez pendientes** — inseminadas sin confirmar, con fecha de
  chequeo ya alcanzada (inseminación de los últimos 120 días).
- **Vacas para inseminar** — paridas hace más de 50 días, vacías, sin inseminar
  y fuera de secado.
- **Vacas para secar** — preñadas marcadas en proceso de secado.
- **Tratamientos en curso** — tratamientos sanitarios activos sin finalizar.
- **Retiro de leche vigente** — vacas cuya leche NO debe enviarse (período de
  retiro por tratamiento aún vigente).

Las definiciones y umbrales (p. ej. los 50 días de espera voluntaria post-parto)
están en `tareas.py` y son **aproximaciones** de la configuración de DelPro: si el
tambo usa otros valores, se ajustan ahí. No reproducen el algoritmo exacto de la
plataforma (que depende de reportes y parámetros configurados en DelPro), pero
cubren las mismas categorías de trabajo diario.

## Funciones

- **Dashboard**: KPIs (kg de ayer, vacas en ordeño, ordeños, animales activos,
  preñadas), producción de los últimos 30 días y estado reproductivo del rodeo.
- **Consultas predefinidas** (no requieren IA): producción, top de vacas, curva de
  lactancia, reproducción, inseminaciones, partos, alertas de conductividad
  (posible mastitis), tratamientos y diagnósticos frecuentes.
- **Preguntas libres con IA**: escribe una pregunta en español; Claude
  (`claude-haiku-4-5`, el modelo más económico) la traduce a SQL de solo lectura,
  se ejecuta contra DDM y devuelve gráfica, tabla y un análisis breve orientado a
  decisiones. El modelo se configura en `ai.py` (constante `MODEL`).
- **Reportes**: exportación a CSV y impresión/PDF de cualquier resultado.

## Habilitar las preguntas con IA

Define la clave de la API de Anthropic antes de arrancar la app:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # solo para esa sesión
# o de forma permanente:
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Sin la clave, la aplicación funciona igualmente con el dashboard y las
consultas predefinidas.

## Seguridad

- La app solo ejecuta sentencias `SELECT` (validación en `db.py`): se rechazan
  INSERT/UPDATE/DELETE/EXEC, múltiples sentencias, `SELECT ... INTO`, etc.
- Resultados limitados a 5000 filas y 60 s de tiempo de consulta.
- El servidor escucha solo en `127.0.0.1` (no accesible desde la red).
