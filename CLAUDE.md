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

## HASTA DÓNDE LLEGAN LOS DATOS (leer antes de cualquier análisis histórico)

**La producción arranca en DICIEMBRE 2025.** `AnimalDaily` con leche: 0 en
ago/sep/oct-2025, 1 en nov, 29.944 en dic. O sea que hay ~8 meses de datos de
leche, no más. Todo lo que cruce producción —conversión, IOFC, potencial por
animal, curvas de lactancia— no puede mirar más atrás de diciembre 2025.

**Marzo y abril tienen CERO inseminaciones y CERO celos, los dos años**
(2025 y 2026), mientras los chequeos y los partos siguen normales.
CONFIRMADO POR EL TAMBO: en ese período no se insemina. Es una parada de
servicio deliberada, no un hueco de carga.

Consecuencia para cualquier análisis de estacionalidad: el rodeo tiene un
patrón de servicio ESTACIONAL. La caída de servicios del verano es en parte
un plan, no una falla. Los meses de parada hay que excluirlos o marcarlos, y
no se puede comparar "servicios de verano contra servicios de invierno" como
si ambos fueran períodos de servicio pleno.

**Los eventos reproductivos de 2024 y principios de 2025 son de volumen
bajo** (~100-350 inseminaciones/mes contra 300-1.000 después de mayo-2025).
Puede ser rodeo más chico o carga parcial. NO comparar performance entre
2024-25 y 2025-26 sin resolver esto primero.

## Lo que sigue: ITH histórico cruzado con reproducción

Hipótesis del tambo, y los datos la respaldan: los baches de preñez del verano
son estrés calórico, no manejo. Falta cruzarlo y mostrarlo.

**La fuente ya está probada y funciona.** Open-Meteo, gratis, sin credenciales,
histórico horario desde 1940:

    https://archive-api.open-meteo.com/v1/archive
      ?latitude=-36.001618&longitude=-62.778799
      &start_date=...&end_date=...
      &hourly=temperature_2m,relative_humidity_2m
      &timezone=America%2FArgentina%2FBuenos_Aires

Devuelve las coordenadas exactas del tambo (elevación 92 m). El ITH se calcula
con la fórmula que ya está en `iot_monitoreo.calcular_ith()`. Lo que manda para
estrés es el **ITH MÁXIMO diario**, no el promedio.

Medido en enero 2026: **26 de 31 días con ITH máx ≥ 72** (estrés moderado), y
el 11 y 12 llegó a 80,4 y 81,7 (severo). En el mismo mes, la pestaña Tasa de
Preñez da 8% de preñadas y 27% de concepción, contra 27% y 47% en agosto.

**YA SE ANALIZÓ. La hipótesis simple NO se sostiene, y hay dos trampas en el
dato que hay que resolver antes de graficar nada.**

TRAMPA 1 — los últimos meses están censurados. Un servicio se chequea ~35 días
después, así que los servicios recientes todavía no tienen resultado. Medido:
jun-2026 da 11,4% de concepción y jul-2026 da 0,0%. NO ES REAL. El gráfico
tiene que EXCLUIR o marcar como incompletos los últimos ~2 meses; si no,
muestra un derrumbe inventado.

TRAMPA 2 — los dos veranos dan distinto con el mismo ITH:

    verano 2024-25 (dic/ene/feb):  51,1%  39,2%  43,1%   (436 servicios)
    verano 2025-26 (dic/ene/feb):  20,9%  30,4%  39,9%   (851 servicios)

Promediarlos hace que se cancelen: por eso un primer análisis por tramos de
ITH dio PLANO (36,3% sin estrés contra 36,7% con estrés severo, y el estrés
leve arriba de todo, que es absurdo — mide la estación, no el calor).

OJO: el verano 2024-25 tiene la MITAD de servicios, así que esa diferencia
puede ser carga incompleta y no performance (ver la sección de hasta dónde
llegan los datos). Y como la producción arranca en dic-2025, en la práctica
hay UN SOLO verano medible: con uno solo no se puede separar el calor de
cualquier otra cosa que haya pasado ese año.

LO QUE SÍ MUESTRA EL DATO, y es lo que le sirve al tambo: en verano se
DERRUMBAN LOS SERVICIOS, no la concepción.

    may-2026  ITH 61,0   429 servicios   48,5%
    dic-2025  ITH 76,4    67 servicios   20,9%

67 servicios contra 429. La tasa de preñez es servicios × concepción, así que
eso solo explica el 8% de preñadas de enero. Es una decisión de manejo.

**EL GRÁFICO QUE HAY QUE ARMAR** (por mes, no por ciclo): barras de servicios,
línea de % concepción, línea de ITH en eje derecho, los últimos 2 meses en
gris con la aclaración de que faltan chequeos, y las bandas de ITH marcadas.
Que se lea de un vistazo que el bache de verano es de servicios.

Se probaron cuatro ventanas de desfasaje (-40 a +7, -21 a 0, 0 a +7, -60 a
-21) y ninguna da señal limpia. NO insistir por ahí sin controlar por año.

Umbrales: >68 leve, >72 moderado, >80 severo. Usar el ITH MÁXIMO diario.

**Los parámetros de MilkMetric también tienen que ser editables** (hoy están
hardcodeados en la constante `MILKMETRIC` del template). No están en DDM, así
que van al mismo `parametros_reproductivos.json` por tambo, con el mecanismo
que ya existe. Ahí viven la latitud y longitud que alimentan lo de arriba.

## Después: costo de alimentación y potencial por animal

El objetivo es saber cuánto rinde cada vaca: ingreso por sólidos menos costo
de alimento (IOFC), eficiencia de conversión (kg de sólidos por kg de materia
seca) y potencial sin explotar (su pico contra el de su grupo y lactancia).

**EFICIENCIA DE CONVERSIÓN: HECHA** (`alimentacion.py`, pestaña "Eficiencia de
conversión"). Medido sobre las cuatro semanas al 21/07/2026:

    Rodeo 2   23,3 kg MS → 3,81 kg sólidos → 0,163
    Rodeo 3   22,3 kg MS → 3,14 kg sólidos → 0,141
    Rodeo 1   23,8 kg MS → 3,24 kg sólidos → 0,136  (frescas, DIM 23)
    Rodeo 5   25,0 kg MS → 2,87 kg sólidos → 0,114  ← come más y convierte peor
    tambo                                    0,140

**Es una medida de GRUPO, no de vaca**, y hay que repetirlo cada vez: sin
comederos individuales a todas las vacas del corral se les imputa la MISMA
materia seca, así que ordenar por conversión DENTRO de un grupo es ordenar por
kg de sólidos con otro nombre. Los sólidos sí son individuales y medidos (del
control lechero mensual: 1.506 vacas controladas en julio, 93% del rodeo).

**La materia seca hay que calcularla**, Haasten no la da: `kgHeads` del lote es
el objetivo configurado (24,3 constante), no lo entregado. Cada operación del
mixer tiene UNA receta (314 de 314) y las cargas traen el %MS por ingrediente,
así que `%MS de la receta × kg descargados` da la MS real. Las cargas y
descargas de una operación cierran dentro del 4% en los casos normales.

**El denominador sale de `AnimalDaily` por día, no de un conteo fijo.** En el
grupo de frescas pasaron 868 vacas distintas en 28 días teniendo ~400 a la vez:
con un conteo estático la MS por vaca da la mitad y parece que pasan hambre.

**Guarda de plausibilidad (`MS_MIN_PLAUSIBLE`, 10 kg).** El lote "Enfermeria"
(grupo Rodeo 9) tiene 3.142 kg registrados en cuatro semanas para 35 vacas —
7,5 kg de MS por vaca — y con eso la conversión daba **0,349**, el doble del
mejor rodeo y por encima de lo que permite la biología. Encabezaba el ranking
justamente por estar mal. Los grupos fuera de banda se muestran con el motivo
pero no entran al total ni al gráfico ni a la conversión por vaca.

**Dos hallazgos para el tambo, no del código:** las descargas de "Enfermeria"
no se están registrando completas, y **"Rodeo 4 - Baja" (120 cabezas) no tiene
NINGUNA descarga registrada** en el período pese a tener lote asignado.

**CONCILIACIÓN DE GRUPOS: HECHA** (pestaña "🌾 Alimentación"). Módulos:
`conciliacion.py` (dominio y mapeo guardado), `proveedores/haasten.py`.

**El diagnóstico anterior estaba MAL, y conviene saber por qué.** Decía que
Haasten tenía 4 lotes con 1.219 cabezas, que no tenía Rodeo 5 ni Rodeo 9, y
que faltaban ~400 vacas de un lado. Nada de eso era cierto: se había mirado
una pantalla filtrada. Contra la API, el mixer tiene **72 lotes (24 activos,
4.069 cabezas)**, incluido Rodeo 5. Los rodeos de ordeñe concilian bien:

    Haasten            DelPro              dif
    Rodeo 1  392       Rodeo 1        410   -18   ok
    Rodeo 2  347       Rodeo 2        325   +22   revisar
    Rodeo 3  358       Rodeo 3        354    +4   ok
    Rodeo 4  122       Rodeo 4-Baja   120    +2   ok
    Rodeo 5  359       Rodeo 5        347   +12   ok
    Enfermeria 39      Rodeo 9         65   -26   ← el "faltante"

La coincidencia 347 = 347 era casualidad: Haasten tiene Rodeo 2 con 347 Y
Rodeo 5 con 359, son lotes distintos. Los números NO están corridos.

**`associatedMilkerIndex` es el mapeo declarado por el propio tambo dentro de
Haasten**, y apunta al `AbstractGroup.Number` de DelPro. Está cargado en 8
lotes y es el criterio más fuerte de la pantalla — es lo que emparejó el lote
"Enfermeria" con el grupo "Rodeo 9", que por nombre no se encontraba nunca.
Cuando aparezca un proveedor que no lo tenga, se cae a coincidencia de nombre.

**NO se sugiere por cantidad de cabezas.** Se probó y es puro ruido: con 25
grupos y tolerancia ±10, "Chiquitas 2 LAP" (73) daba cuatro candidatos. Una
sugerencia así es una moneda al aire disfrazada de respuesta. Las cabezas se
muestran en el selector para comparar a ojo, pero elige el tambo.

Lo que queda por mapear a mano son los lotes de recría y vaquillonas
(Chiquitas 1-5, Servicio 1-2, Preñadas 1-5): 14 lotes sin grupo. Y hay
diferencias grandes para revisar del lado del tambo, no del código: Recria 1
(505 en Haasten contra 779 en DelPro), Preparto Vacas (94 contra 51).

**La base DDM está atrasada, y desparejo.** Al 26/07: ordeños al 22/07,
eventos al 25/07, bajas al 20/07. Y dos cosas medidas al armar la
conciliación: **`AnimalDaily` está completo solo hasta el 21/07** (el 22 trae
420 filas contra ~1.600 normales, y del 23 al 25 quedan restos de 20 a 40), y
**`AnimalDaily` solo cubre vacas en ordeñe** — para secas, recría y crianza no
hay ninguna fila. Por eso las cabezas por grupo salen de la membresía actual
(`BasicAnimal.[Group]`), no de `AnimalDaily`. El último cambio de grupo
registrado también es del 21/07: las cabezas de DelPro son de hace cinco días
y la pantalla lo dice. Haasten tiene datos de hoy. Al cruzar hay que comparar
SIEMPRE el mismo período, nunca "lo último de cada uno".

**Arquitectura** — el proveedor de alimentación es intercambiable (mañana
MixerOne, o DelPro si el tambo no tiene mixer):

    proveedores/__init__.py  la interfaz y qué proveedor usa cada tambo
    proveedores/haasten.py   implementación actual (haasten.io)
    conciliacion.py          el mapeo lote ↔ grupo (hecho)
    alimentacion.py          FALTA: consumo, MS, costo, conversión

El proveedor expone siempre `ingredientes()`, `lotes()`, `consumos(desde, hasta)`.

**Haasten es una API REST con JSON**, no un sitio para scrapear (credenciales
en `HASTEN_USUARIO` / `HASTEN_PASSWORD` — con UNA "a", aunque el sitio tenga
dos). `POST /api/login` con `{username, password}` devuelve `{token, user}`; el
token va en la cabecera `authorization` tal cual, sin "Bearer". **El login ya
trae todo lo de los lotes**: `user.devices[].lots`, `.sipnStock` (ingredientes)
y `.sipnConfiguration.lotCategories`. `GET /api/deviceData/get/unloads/{serie}`
y `.../loads/{serie}` con `minDate`/`maxDate` traen descargas por lote y cargas
por ingrediente. **`GET /api/device/all` se cuelga: no usarlo.** De los cinco
equipos de la cuenta, el único mixer es el SIP-N 202616012; los GAC son de
combustible y el DELPROSIPN no manda datos desde junio.

**48 de los 72 lotes son de relleno** ("Corral 23" a "Corral 70", con
`kgHeads = 0` y 100 cabezas fijas). Se marcan `activo=False` por el criterio
físico de que sin kg de materia seca por cabeza no se alimenta a nadie — no
por el nombre, y no se ocultan.

**Los 70 ingredientes siguen con `price: 0`** — el tambo no los cargó (se hace
desde "Editar ingrediente" en Haasten). El proveedor los traduce a `None`, no a
0: un cero se propaga como costo real y miente. Sin precios no hay costo, pero
la eficiencia de conversión SÍ se puede calcular: es una relación física.

**En DelPro no hay nada de alimentación**: de 7.025 lactancias, 0 tienen
consumo o costo cargado. Todo tiene que venir del proveedor externo.

**El costo por animal es un REPARTO, no una medición.** El TMR se entrega al
corral. Sin comederos individuales solo se puede repartir el costo del grupo,
ponderado por consumo estimado. Tiene que decirlo la pantalla, para que nadie
descarte una vaca creyendo que es un dato medido.

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
