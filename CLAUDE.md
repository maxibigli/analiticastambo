# LactIA (antes "Analítica DelPro")

Web app en Flask que analiza la base DDM (SQL Server) de DelPro/DeLaval de un
tambo (La Ponderosa, Argentina): ordeño en vivo, rutina, evolución, salud del
rodeo, tareas pendientes y consultas por IA.

## Dos instalaciones, mismo repo

- **PC de desarrollo**: `C:\Users\MAXI\CLAUDE\delpro-analitica\`.
- **SERVER-DELPRO**: producción real, expuesta en `www.analiticastambo.com`
  vía Cloudflare Tunnel. Se actualiza con `git pull` (o doble clic en
  `actualizar.bat`, que además para y vuelve a levantar el proceso).

**`actualizar.bat` en SERVER-DELPRO debe correrse como Administrador** — si
no, no logra matar el proceso Python viejo (falla silenciosa), y queda
corriendo código anterior aunque los archivos ya se hayan actualizado. Los
síntomas típicos de esto: el diseño/HTML se ve actualizado (Flask relee las
plantillas en cada request) pero faltan funciones nuevas del backend.

Archivos que NO están en git (estado propio de cada instalación, no se
comparten): `usuarios.json`, `secret_key.txt`, `alertas_canales.json`.
Cambios en `.py` requieren reiniciar el proceso; cambios en `templates/*.html`
se aplican solos (`TEMPLATES_AUTO_RELOAD=True`).

## Credenciales

Nunca hardcodear contraseñas/tokens en el código. Todo sale de variables de
entorno (`DELPRO_PWD_<TAMBO>`, `CICLA_PASSWORD`, `TELEGRAM_BOT_TOKEN`, etc. —
ver `tambos.py` e `INSTALL.md`). Si aparece un secreto en texto plano en
algún archivo, es un error: sacarlo y pedirle al usuario que lo setee él
mismo con `setx`.

## Dónde quedamos (26/07/2026)

**NUNCA hardcodear parámetros reproductivos.** Salen de DelPro
(`ReproductionSetting`) vía `parametros.valor(clave, tambo)`. Este tambo tiene
gestación 280, secado **50** (el defecto de DelPro es 60, y usarlo corría diez
días la fecha de secado de cada vaca), espera voluntaria **53** y ciclo 21.
Las constantes de módulo quedan solo como respaldo si falla la consulta.

**Hallazgo sin resolver, del lado del tambo:** las gestaciones reales promedian
**276,8 días** (1.532 partos) contra los 280 configurados. Son 3,2 días de
corrimiento en todas las fechas de parto y secado proyectadas. Se corrige
cambiando el parámetro en DelPro, no en el código. La pestaña "Análisis de
Gestación" lo muestra como advertencia.

**Pendiente:** pestaña "Parámetros Reproductivos", PRIMERA de Análisis
Reproductivo. El endpoint `/api/reproduccion/parametros` ya existe y devuelve
los 20 parámetros con valor/defecto/mín/máx/activo y cuáles cambió el tambo;
falta la página. Sumar también el bloque MilkMetric (descarte 24% anual,
lat/long del tambo: -36.001618 / -62.778799), que NO está en la base.

## Trampas del esquema DDM (ya corregidas, no reintroducir)

- `CMSGroupMilkSetting.EnableMilking = 1` es la única forma correcta de saber
  qué `[Group]` son grupos de ordeñe reales — no inferir por estado
  reproductivo (incluye grupos de OTROS tambos que comparten la base).
- Nombres de grupo reales: `AbstractGroup.Name`/`Number` (join por OID
  compartido con `AnimalGroup`). El OID en `BasicAnimal.[Group]` NO es el
  número que muestra DelPro.
- `MilkTest` se une a través de `AnimalHistoricalData` (`h.OID = MilkTest.OID`
  → `h.BasicAnimal`, `h.DateAndTime`), NUNCA vía `MilkingTestAnimal` (colisión
  de OIDs, 0% de coincidencia real, `SampleDateTime` NULL en toda la base).
- `MilkTest.SCC` está en MILES de células/ml (multiplicar ×1000 para mostrar).
- `RelativeConductivity`: solo el MÁXIMO de la ventana discrimina enfermedad;
  el promedio está invertido. Filtrar `> 0` (0 = sin lectura, no un valor real).
- `ForcedRetract` (y otros flags de `CMSMilkYield`) son `bit` — `SUM()` sobre
  ellos requiere `CAST(... AS int)` primero (SQL Server no suma bits directo).
- Sesiones de ordeño: el tope de fusión de bloques usa
  `CMSGroupMilkSetting.NumberOfMilkings` (real para este tambo: 3/día), nunca
  un número fijo asumido.
- Flujos de ordeño: la curva viene resumida en `CMSMilkYield` en cuatro tramos
  promediados (`Flow0To15`/`Flow15To30`/`Flow30To60`/`Flow60To120`), NO segundo
  a segundo. Por eso la bimodalidad calculada acá da bastante más baja que la
  de DelPro (la tendencia sí coincide). `LowFlowDurationInSec` es el "tiempo de
  colocación" e `IsoDuration` la duración del ordeño, ambos en segundos.
- Umbrales de retirada: NO inventarlos ni hacerlos editables. Salen de
  `CMSMpcSetting.TakeoffLimit` (0,80 en este tambo) y la banda del informe es
  ±25% de ese valor → 0,60 y 1,00, que son las tres tarjetas de DelPro.
- **La base la comparten TRES tambos** (`Herd` tiene 3 filas):
  rebaño 1 = La Ponderosa (grupos `... LP` + Rodeo 1..9), 6 = Don Germán
  (`... DG`), 7 = SB. Toda consulta de rodeo DEBE filtrar con
  `rebano.filtro()` / `rebano.filtro_por_animal()`, que deducen el rebaño del
  tambo de dónde están los grupos de ordeñe (nunca hardcodear el 1). Sin
  filtrar hay 3.253 vacas lactantes; La Ponderosa sola tiene 1.621.
  Las consultas que filtran por `CMSGroupMilkSetting.EnableMilking = 1` ya
  quedan acotadas al tambo de rebote, porque solo el rebaño 1 tiene grupos de
  ordeñe — pero no hay que confiarse en eso.
- Los informes de DelPro que sirvieron de referencia estaban en "Todos los
  rebaños": por eso sus números son ~2x los de La Ponderosa sola.
- Proyección de rebaños: la ecuación es
  `lactantes[m] = lactantes[m-1] + partos - secados - salidas` y la producción
  `lactantes × kg/vaca/día del año pasado × días del mes` (ambas verificadas
  exactas). Los partos previstos NO se pueden replicar: DelPro simula preñeces
  futuras. Detalle completo en `proyeccion.py`.

## Problemas de datos en DDM (no son bugs del código)

- `EventPregCheck.DaysFromInsemination` viene en 0 en TODA la base: el parto
  esperado no se puede sacar del chequeo de preñez, sale de la inseminación
  efectiva más la gestación. Es lo que deja vacía la Tasa de Concepción (en el
  informe de DelPro también sale vacía).
- **Antes de dar por roto un dato, FILTRAR POR REBAÑO.** Sin filtrar parecía
  que 796 de 1.715 preñeces estaban mal cargadas (46%) y que las
  inseminaciones de 2026 iban a la mitad del ritmo. Filtrando a La Ponderosa
  son 19 de 920 (2%), con 176 días de preñez promedio, y las inseminaciones
  van 4.298 en 2025 contra 2.205 en 7 meses de 2026. Los datos del tambo están
  sanos; el desastre era de los otros dos tambos de la base.
- Faltan algunos eventos de parto: contar lactantes desde los partos da menos
  que el estado reproductivo. Por eso el histórico se reconstruye despejando
  el balance y se grafica al lado el conteo medido.

## Entorno de desarrollo (esta PC)

Python no está en el PATH (`C:\Users\MAXI\AppData\Local\Programs\Python\Python312\`).
SQL Server Express local con poca RAM — consultas pesadas necesitan
`OPTION (MAXDOP 1, MAX_GRANT_PERCENT ...)` para no colgarse en
`RESOURCE_SEMAPHORE`. Detalle completo en la memoria de Claude de este usuario
(`delpro-entorno.md`, `delpro-deploy-produccion.md`) — son point-in-time,
verificar contra el código actual antes de asumir vigentes.
