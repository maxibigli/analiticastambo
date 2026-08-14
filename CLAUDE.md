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

## Dónde quedamos (05/08/2026)

**Restaurar DDM en SERVER-DELPRO DEJA A LA APP SIN LEER LA BASE.** El backup
viene de la PC de DelPro del tambo (`DESKTOP-0QE9PNB`) y trae adentro SUS
usuarios de base: al pisar DDM, el usuario `delpro_lectura` que vivía dentro se
va con ella. El login sigue existiendo a nivel servidor —conecta a `master` sin
problema— pero DDM lo rechaza con el 4060, *"Cannot open database DDM requested
by the login"*. **Pasa en cada restore.** Se arregla en SERVER-DELPRO, con
SSMS por autenticación de Windows o `sqlcmd -E`, como Administrador:

    USE DDM;
    IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'delpro_lectura')
        ALTER USER delpro_lectura WITH LOGIN = delpro_lectura;   -- quedó huérfano
    ELSE
        CREATE USER delpro_lectura FOR LOGIN delpro_lectura;     -- se fue con el restore
    ALTER ROLE db_datareader ADD MEMBER delpro_lectura;

**Retiradas forzadas por rodeo y sesión: tabla nueva al final de Rendimiento
Sala, y calca a DelPro.** Ahí se descubrió que **el reporte de DelPro agrupa
por `BasicAnimal.[Group]`, el rodeo de HOY**, no por el rodeo del día. Detalle
y números abajo, en "Los DOS criterios de rodeo".

**Base local: sigue al 31/07.** El backup del 05/08 está extraído, concatenado
y verificado (`RESTORE VERIFYONLY` OK) en
`...\Documents\delaval\la ponderosa\05-08-26\DelPro_full.bak`, pero NO se
restauró: la producción ya se actualizó desde DelPro. Extraer siempre a una
carpeta propia por fecha, para no pisar los `.bak` partidos del backup
anterior.

## SenseHub / Allflex: dónde está el dato (06/08/2026)

Investigación previa a cruzar salud y celo del tambo de **Bernardo Etchevers
(Trenque Lauquen)** — OTRO tambo, otros animales, no La Ponderosa. Lo que hay
en `C:\Users\MAXI\Documents\delaval\bernardo\SenseHub Tools` es la aplicación
cliente, **no una base**: un Chromium empaquetado (CefSharp) más un servicio de
integración. Al 06/08 el backup del tambo todavía no estaba en esa carpeta.

**EL DATO VIVE EN EL CONTROLADOR**, un equipo en la red del tambo, no en la PC.
Es un servidor Java (**WildFly 10 / Undertow**, visto en las cabeceras HTTP) que
sirve la app web Angular (`/app/`), la API REST (`/rest/api/...`) y tiene su
base adentro, **sin exponer**. En la PC no queda nada: el caché del navegador no
tiene Local Storage ni IndexedDB y las cookies están vacías.

**NO HAY BASE A LA QUE CONECTARSE** como con DDM. Allflex no publica el motor ni
abre un puerto. La interfaz soportada es la API REST, y eso es "la base" para
nosotros. Tres niveles:

1. **Export de terceros, YA ANDANDO** (usuario `ThirdParty`, contraseña de
   fábrica — vale saberlo, cualquiera en esa red lo lee):

       GET /rest/api/thirdparty/export?exportDataType=healthIndex
       GET /rest/api/thirdparty/export?exportDataType=systemHeats

   El servicio los baja cada ~2 h y los escribe en `C:/DIRSA/SCR`
   (`healthindex.txt`, `heatdetect.txt`). Campos medidos:
   healthIndex → `cowNumber`, `healthIndex`; systemHeats → `cowNumber`,
   `peakHeatIndex`, `currentHeatIndex`, `breedingWindowStartDateTime` (epoch),
   `breedingWindowValue`.

   **LIMITACIÓN QUE DEFINE TODO: ese export trae SOLO LA LISTA DE ALERTA, no el
   rodeo.** Máximo 13-14 vacas por respuesta contra 686 collares. Sirve para
   "quién está marcado hoy", no para la serie de todo el rodeo.

2. **La API completa del web client** (requiere login, `/rest/api/v4/auth/login`).
   Las rutas salieron del caché del navegador:

       /rest/api/v3/server/sync?lastUpdateTime=<epoch>   <- sync incremental
       /rest/api/animals/{animalId}/details | /events | /graphs/{n}
       /rest/api/alerts/system | /alerts/farm | /dashboardkpis | /system/kpis
       /rest/api/v2/reports/{id} | /v5/reports | /v3/groups/{id} | /farm/reproduction

   `server/sync` es el mejor punto de integración: devuelve los cambios desde un
   timestamp, sin machacar el controlador.

**DOS CLAVES DE IDENTIDAD, no una.** `animalName` es el RP ("4116") y
`animalId` es el id interno de SenseHub (1320). El export de terceros usa
`cowNumber`, que **es el `animalName`**; los endpoints por animal piden el
`animalId`. Hay que mantener el mapa entre los dos.

**LAS BANDERAS DE SALUD NO ESTÁN EN EL EXPORT.** Son `isActivityAlert` e
`isRuminationAlert`, y viven en `/animals/{id}/details` (nivel 2). El esquema
por animal, tal cual cacheado:

    {"animalId":1320,"animalName":"4116","groupName":"Rodeo 1","groupId":5,
     "status":"Inseminated","isActivityAlert":true,"isRuminationAlert":false,...}
    {"lactationStatus":"Inseminated","dim":106,"lactationNumber":3,
     "breedingNumber":1,"pregnancyCheckResult":null,"breedingDate":1749697200}

Los grupos se llaman igual que en DelPro ("Rodeo 1", "Rodeo 3"), lo que ayuda a
conciliar — falta confirmarlo contra la base de ese tambo.

**Reconstruido de los logs** (rotan cada 100 MB; quedan ~3 semanas útiles), del
14/07 al 05/08/2026: 132 vacas distintas alertadas por salud, 218 por celo, 40
en las dos listas; índice de salud de las alertadas entre 28 y 86, mediana 80;
la 6695 con 15 días distintos de alerta.

**OJO CON LA IP: cambió.** El caché de julio 2025 muestra el controlador en
`192.168.0.19` y la configuración de hoy dice `192.168.0.11`.

## Check-list de control: la mini app del celular (05/08/2026)

Las dos planillas de papel del tambo (Control Diario, 11 puntos; Control
Semanal, 9) pasaron al teléfono. `checklist.py` + `templates/checklist.html`,
ruta `/checklist/`, instalable como PWA.

**Es la MISMA app Flask, no un servicio aparte.** Se ve como una app propia
—ícono, pantalla completa, el operario no ve la analítica— pero separarla de
verdad duplicaría login, usuarios, deploy, túnel y backup, y los datos tienen
que volver acá igual para cruzarlos con el ordeñe. El rol `operario` ya
existía en `auth.py`; la página de estadísticas es solo para `admin`.

**Se guarda en SQLite propio (`checklist.db`), NUNCA en DDM.** Además de que
DDM es de solo lectura, es la base de DeLaval: lo que se escriba ahí se pierde
en el próximo restore. Las fotos van al disco (`checklist_fotos/AAAA/MM/`), no
como blobs: hacen crecer la base y vuelven impracticable el backup.

**La plantilla se versiona y NO se edita en el lugar.** Al agregar o sacar una
tarea se crea una versión nueva; las corridas viejas siguen apuntando a lo que
se preguntó ese día. Si no, un "95% de cumplimiento" del mes pasado pasaría a
calcularse sobre preguntas que entonces no existían.

**Tres frecuencias, no dos.** Los 11 puntos diarios completos en cada ordeñe
son 33 checks por día: eso termina en tildar OK sin mirar. Quedaron 6 por
ordeñe, 5 diarios y 9 semanales, como punto de partida. **Se edita desde
⚙ Configuración → Check-list de control**, sin tocar código: ahí se agregan,
sacan, reordenan tareas y se les cambia la frecuencia, y cada guardado crea una
versión nueva.

**Cumplimiento y adherencia van SIEMPRE juntos.** El primero dice, de lo que se
cargó, cuánto dio OK; el segundo, cuántas de las cargas esperadas se hicieron.
Un 100% sobre el 40% de las sesiones no vale nada, y es el error clásico de
estos tableros: el número queda hermoso porque casi no se carga.

**El tiempo de resolución se mide POR DÍA, no por ordeñe.** Como el check se
llena en cada ordeñe, lo normal es que algo dé NO a la mañana y OK al mediodía:
cerrando la falla en el primer OK, TODO daba "resuelto en 0 días" y la medida
no servía (se probó, daba eso). Un día está mal si tuvo al menos un NO, los
días malos seguidos son UN problema, y se cierra el primer día posterior con
carga sin ningún NO. Un día sin cargar no corta la racha: no saber no es lo
mismo que estar bien. Contra: algo arreglado entre ordeñes figura como 1 día.

**Trampa de Flask que costó encontrar:** `_tambo_del_request()` hacía
`request.json` en TODO POST, y con un body multipart eso devuelve **415** sin
llegar al endpoint. Ahora mira el body solo si `request.is_json`. Se sigue
usando `.json` y no `get_json(silent=True)` a propósito: un JSON mal formado
tiene que fallar fuerte, no caer callado al tambo por defecto.

El service worker tiene scope `/checklist/`, **no la raíz**: con scope `/`
también interceptaría la app principal y un caché viejo ahí se ve exactamente
como "el deploy no subió". La plantilla no se cachea nunca.

## Antes (01/08/2026)

**Rendimiento Sala tenía un error de fondo, ya corregido**: la consulta que
alimenta toda esa pantalla descartaba en silencio ~5% de los ordeños del día, y
además leía cuatro campos "parecidos" en vez de los correctos. Ahora la tabla
replica el reporte de DelPro con 162 de 162 campos exactos sobre tres días.
El detalle está abajo en "Rendimiento de ordeño: qué campo es cada cosa", y
conviene leerlo antes de tocar `rutina.py`: los errores no daban excepción, solo
números malos que parecían plausibles.

**Base local actualizada al 31/07/2026** (antes llegaba al 21/07). El backup
partido de DelPro viene en tres archivos que hay que concatenar (`copy /b`)
antes de restaurar; el `RESTORE` se corre conectado a `master`, no a `DDM`.

## Antes (27/07/2026)

**Alimentación quedó implementada**: conciliación de grupos y eficiencia de
conversión, más el gráfico de ITH reconectado. Detalle abajo en sus secciones.
Lo que sigue es el costo por vaca (IOFC), y está **bloqueado por dos precios
que no están en ningún sistema conectado**: los 70 ingredientes de Haasten
tienen `price: 0` y La Serenísima solo publica datos físicos, sin importes.
Los carga el tambo, no el código. Mientras tanto la conversión física ya
ordena el rodeo y el IOFC se va a apoyar en lo que calcula `alimentacion.py`.

**Dos cosas para arreglar en Haasten, no en el código.** El lote "Rodeo 4" no
recibe una descarga desde julio (120 cabezas) y "Enfermeria" las registra
incompletas (7,5 kg de MS por vaca, imposible). Por eso la conversión cubre el
89% del rodeo en ordeñe y no el 100%. La pantalla lo dice con nombre y motivo.

**CUIDADO CON DOS SESIONES A LA VEZ.** El 26/07 dos sesiones editaron `app.py`
e `index.html` en paralelo y se pisaron: una commiteó por error el trabajo de
ITH de la otra, la otra revirtió los dos archivos a un estado viejo —borrando
su propio endpoint y el trabajo ajeno— y dejó `clima.py` como código muerto.
Costó tres commits de arreglo. Si se trabaja en paralelo, que sea sobre
archivos distintos, y **nunca `git add` de un archivo entero**: mirar el diff
antes de commitear.

## Cómo quedó (26/07/2026)

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

**Un lote que no recibe comida no es un lote.** De los 72 que declara el mixer,
48 son de relleno ("Corral 23" a "Corral 70", `kgHeads = 0`) y otros 14 tienen
ración configurada pero **no vieron un kg en cuatro meses** (Secas, Chiquitas
1-5, Servicio 1-2, Preñadas 1-5). **Quedan 10 lotes reales**: los cinco rodeos
de ordeñe, enfermería, secas, recría y los dos de preparto — que es lo que
tiene un tambo de verdad. Pedirle un grupo a los otros generaba catorce alertas
de algo que no es un problema, y catorce alertas falsas tapan las verdaderas.
El criterio es el dato, no una lista: se miran las descargas de los últimos 30
días (`CONCILIACION_DIAS_USO`). Los que no se usan van a una sección plegada,
no se ocultan. Un lote MAPEADO que dejó de recibir sigue a la vista: es
justamente el caso que hay que ver (le pasó a "Rodeo 4").

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

## Agente de IA: preguntas del tambo en lenguaje natural (14/08/2026)

`agente.py` + `POST /api/agente/preguntar` (gateado admin). Responde
encadenando herramientas hasta `MAX_TURNOS` veces, no escribiendo SQL suelto
contra DDM como hacía `/api/preguntar`.

**LA DECISIÓN QUE IMPORTA: las herramientas son los mismos endpoints `/api/...`
que ya usa cada pantalla**, llamadas en proceso con el `test_client` de Flask
(sesión admin sintética, solo para poder llegar a rutas con
`@auth.requiere_rol` — no filtra hacia afuera: `/api/agente/preguntar` tiene el
mismo gate). Motivo medido, no supuesto: la lista de trampas de este mismo
documento —filtrar por rebaño, `MilkTest` por `AnimalHistoricalData`, los
tramos de flujo de Alpro ×100, `SCC` en miles— es EXACTAMENTE lo que un modelo
escribiendo SQL desde cero pisaría, con un número que sale mal y no avisa.
Reusar los endpoints ya verificados evita duplicar esa lógica una segunda vez
y GARANTIZA que el agente diga lo mismo que la pantalla.

El SQL libre (`ai.py`, ya existía para `/api/preguntar`) queda de ÚLTIMO
RECURSO, con dos candados: el mismo bloqueo de `tambos.es_produccion()` que ya
tenía `/api/preguntar` (nunca corre SQL de IA contra una base de producción en
vivo), y la respuesta tiene que avisar que ese número no pasó por ninguna
pantalla verificada — está en el `system` prompt y en el propio resultado de
la herramienta (`"AVISO": "..."`).

**SOLO LECTURA en tres capas independientes**, ninguna depende de que el
agente "se porte bien": el mapa de herramientas solo tiene rutas GET (ninguna
ruta que escribe existe ahí, no es una regla que se pueda saltear); el SQL
libre pasa por `db.validate_sql` igual que cualquier otra consulta; y la
conexión a DDM es del usuario `delpro_lectura` con `ApplicationIntent=ReadOnly`
— aunque las dos capas de arriba fallaran, SQL Server rechaza la escritura.

**Bug encontrado construyendo esto, no relacionado con el agente en sí**: al
implementar `sql_identificacion` para la convencional (ver más abajo, tarea
del 13/08) nunca se probó `/api/rutina/rendimiento` con esos datos reales.
`api_rutina_rendimiento` llamaba a `rutina.armar_identificacion` fijo, que
espera las columnas de la ROTATIVA (`ordenos`/`desconocidos`); la consulta de
la convencional trae otras (`visitas`/`sin_duenio`/`sin_lectura`/`desconocido`)
y tiraba `KeyError: 'ordenos'` — la pantalla de Rendimiento Sala de La Martina
estaba rota en producción sin que nadie lo hubiera notado. Se agregó
`armar_identificacion` propio a cada sala (rotativa delega a `rutina.py`,
convencional arma su propio shape con el mismo contrato de salida más el
detalle de las dos causas separadas) y `api_rutina_rendimiento` ahora
despacha por `salas.de(tambo)` en vez de llamar fijo. Lo encontró el
`test_client` del agente ejercitando el endpoint de verdad — un valor
concreto de construir herramientas contra la app real y no contra mocks.

**Los payloads de dos herramientas hay que podarlos, y por el mismo motivo.**
`rutina_dia` (`/api/rutina`) y `rendimiento_sala` (`/api/rutina/rendimiento`)
traen campos que son para el GRÁFICO de la pantalla: `visitas` (un renglón
por ordeño, 300 a 2.400 según la sesión) y `grupos`/`retiradas_grupo_actual`
(un renglón por CADA bloque contiguo de rodeo — en la convencional un rodeo
puede entrar en 100-200 bloques por vacas sueltas, medido en CLAUDE.md más
abajo). Sin podarlos, una sola sesión de La Martina mide 20.000+ caracteres,
más que el resto de la respuesta junta. `_recortar()` en `agente.py` los saca
y deja el agregado entero (`detalle`, `hallazgos`, las métricas de
throughput) — es lo mismo que necesitaría un humano leyendo la pantalla para
explicar el día, sin la lista renglón por renglón.

**SQL Express, primer toque de un tambo en un proceso nuevo, dispara una
avalancha de warmup en serie** (`_tambo_del_request` → `_warmup`, ya
documentado en el propio `app.py`): dashboard, salud, reproducción,
rendimiento, alimentación, todo en UNA cola porque `db.py` serializa las
consultas por servidor con un `Semaphore(1)`. Medido probando el agente con
procesos Python nuevos (cada uno arranca con el caché de `app.py` vacío):
una herramienta fría puede tardar 90s, y si se piden VARIAS herramientas
distintas en ráfaga para un tambo recién tocado, se encolan entre sí y algunas
superan los 160s de reintento. **Esto es un costo de una sola vez por proceso,
no por pregunta** — la app real queda corriendo y sirve rápido después del
primer warmup; el costo alto aparece solo al testear con procesos nuevos
repetidos, o si el servidor se acaba de reiniciar. `CALENTANDO_REINTENTOS`
(20) × `CALENTANDO_ESPERA_S` (8s) = 160s por herramienta es generoso a
propósito: el agente no es una pantalla en vivo, una respuesta que tarda un
par de minutos por Telegram es aceptable.

**Pendiente, y es una decisión, no un descuido**: conectar esto a Telegram
requiere un webhook de ENTRADA, y `telegram_bot.py` hoy solo envía (no hay
ruta pública que reciba mensajes). Abrir una ruta pública nueva en el túnel
de SERVER-DELPRO tiene implicancias de seguridad (quién puede escribirle al
bot, cómo se mapea un chat de Telegram a un tambo) que conviene decidir con
el tambo antes de exponerlo, no resolverlas en el camino. Mientras tanto el
agente se prueba por `POST /api/agente/preguntar` (mismo gate admin que el
resto de la gestión).

## El score de rutina de una sala convencional (13/08/2026)

La espina de pescado tenía 4 de 7 componentes en "sin datos" y no llegaba a
calificar. Ahora son OCHO componentes, siete vivos, y **no son los de la
rotativa con otro reparto: son otras preguntas**, porque la sala es otra.

**LA VACA SE IDENTIFICA AL ENTRAR, NO EN EL PUESTO.** De ahí sale casi todo lo
demás. El tramo `IdTimestamp → BeginTime` NO es el tiempo de colocación de la
pezonera: incluye la caminata, la espera a que se llene la mangada y recién al
final la preparación. Medido del 05 al 11/08 sobre 12.926 ordeños con ID:

    p05 152s   p25 227s   p50 281s   p75 341s   p95 497s
    negativos 135 (1,0%)   más de 30 min 49 (0,4%)

O sea que **el dato es medible y está limpio** — el diagnóstico anterior ("es
ruido") salía de mirar solo el mínimo (−434s) de una muestra chica. Lo que no
sirve es el objetivo: contra los 90s de DelPro daban 109 de 12.926 en hora
(0,8%). Ese 0% no acusa a la rutina, acusa a la regla. **El objetivo lo carga el
tambo en ⚙ Configuración (`umbral_prep_s`) y vacío = no se puntúa**: elegirlo
nosotros —la mediana, por ejemplo— sería calificar a la sala contra sí misma y
cualquier tambo daría 50. Misma regla que los umbrales de retirada.

**EL 17% DE LOS ORDEÑOS NO TENÍAN DUEÑO Y NO SE VEÍAN.** El comodín es UN animal
con `BasicAnimal.Number = 0` (igual que en la rotativa) y se lleva 2.728 de
15.665 en una semana. Estaba oculto por partida DOBLE: `sql_rutina` filtraba
`IdTimestamp IS NOT NULL` (y 2.677 de esos ordeños no tienen sello), y el filtro
por rodeo lo tiraba de nuevo porque **una vaca sin identificar no tiene rodeo**.
Con las dos cosas arregladas el componente da 81% la mañana del 11/08 contra 97%
y 96% las otras dos sesiones de ESE MISMO DÍA — no es un problema constante de
antena, es algo que pasa en una sesión puntual. En La Ponderosa el mismo
componente da 100% (2 de 1.618), por eso allá pesa 0.

Contra conocida: si se filtra a UN rodeo, esos ordeños entran igual (no se sabe
de qué rodeo eran), así que el % sin identificar de esa vista es el de la sala
entera. Está en el código, `rutina.analizar_dia(incluir_sin_grupo=...)`.

**LOS RODEOS SÍ ENTRAN EN BLOQUE; LAS TANDAS NO.** `_huecos_tandas` cortaba por
(`SideNo`, `BatchNo`) y quedó inservible porque esa numeración se fragmenta
(112 de 143 cambios son reapariciones). Pero cortando por RODEO el dato cierra:
el 11/08, en 796 ordeños hay corridas de hasta 122 vacas de un mismo rodeo, y
las sueltas del medio son el ~11% que ya mide `mezcla_rodeos`. El cambio de
rodeo en esa sala cuesta **3, 3, 4, 6 y 85 segundos**: no hay tiempo muerto
entre rodeos, y eso ahora se puede afirmar en vez de dejarlo en "sin datos".

**UN HUECO ENTRE VACAS SOLO ES UNA DEMORA SI HABÍA DÓNDE PONER UNA VACA.** Es la
regla que hace que "manejo de corral" mida algo en esta sala, y las dos
condiciones son físicas, no umbrales elegidos: cuenta solo si durante todo el
hueco el lado tuvo un puesto libre **y** al menos una vaca puesta.

    lado LLENO    no hay dónde enganchar, nadie está demorando nada
    lado VACÍO    la mangada se está dando vuelta -> lo mide el otro componente

Sin eso el componente daba **0 en las tres sesiones**. Los 20 huecos
intra-rodeo de más de 60s del 11/08 tenían el lado lleno (15 a 29 de 30
puestos) o vaciándose (0 a 4): **ninguno era manejo de corral**, y sumaban
13.911s de pérdida inventada. Con la regla quedan 4.162s y el componente pasa a
**35 · 64 · 91**, que sí distingue una sesión de otra.

Ojo con el camino que NO funcionó, para no repetirlo: subir el piso
(`UMBRAL_HUECO_MIN_S`) de 20s a 180s movía el componente de 20 a 47 y del score
apenas 2 puntos. El problema nunca fue cuán largo era el hueco, sino QUÉ ERA.
La capacidad del lado se toma del pico observado en esa sesión, no de la
configuración: el 11/08 dio 33 y 31 a la mañana contra 30 y 30 en las otras dos,
por solapes de un segundo entre el fin de una vaca y el enganche de la siguiente.

**HAY UN TERCER TIPO DE HUECO QUE NO ES NI UNO NI OTRO: el cambio de mangada.**
Los huecos dentro de un mismo rodeo son BIMODALES — mediana 5s (vaca tras vaca)
con una cola de 78 huecos de 240 a 983s (la mangada que se vació). Con los dos
en la misma bolsa la mediana es la chica, la cola entera queda marcada como
anormal y daba **13.911s "perdidos" en una sesión de 5,4 h**: casi cuatro horas
de pérdida inventadas por la estructura de la sala. Los huecos en que el lado
quedó vacío se sacan de ahí y tienen su propio componente.

**LOS DOS LADOS ALTERNAN, y está medido minuto a minuto** (11/08): el lado 1
sube a 30 vacas, baja a 0 y se queda en 0 mientras el lado 2 sube a 30. Son 60
puestos (`MPCNo` 1-30 y 31-60) de los que **la mitad está vacía por diseño todo
el tiempo**. Por eso no se puede puntuar como una rotativa ni contra los 60
puestos: cada lado pasa ~35% de su turno sin una sola vaca y eso es normal. Lo
que se puntúa es el cambio de mangada que se ESTIRA, contra la mediana de la
propia sesión. Da 100 en un día normal, y para eso está: cae cuando un lado se
traba.

Resultado, 11/08/2026 con el objetivo del tambo en 300s — **La Martina 79 · 79 ·
82** (antes: sin calificar) y
**La Ponderosa 84 · 82 · 78, sin moverse un punto**, que es el invariante que
hay que revisar después de tocar `rutina.py`.

El editor de pesos del frontend ya no tiene la lista de componentes escrita:
sale del propio análisis (`componentesDelScore`), que trae clave/label/peso de
la sala real. La lista fija mandaba los pesos de la rotativa a la convencional.

## Flujos en sala convencional: lo que NO se puede medir (13/08/2026)

La pantalla de Flujos ya anda en La Martina (Alpro). Se portaron las cinco
consultas menos una cosa, y la excepción es el punto importante:

**LA MARTINA NO PUBLICA SU UMBRAL DE RETIRADA, así que no hay retirada
prematura ni tardía.** No existe `CMSMpcSetting`, y no hay NINGUNA columna
`TakeoffLimit`/`LowFlowLimit` en todo el esquema (buscado en `sys.columns`).
El flujo al que se soltó cada pezonera SÍ está (`TakeOffFlow`, 31.266 filas,
0 a 4,7 kg/min), pero sin el límite configurado no hay contra qué compararlo.
`salas.convencional.PUBLICA_UMBRAL_RETIRADA = False` deja esas dos métricas en
NULL, `app.py` anula además los tres valores del payload y el frontend saca las
tarjetas y las series en vez de dibujarlas en cero. **El respaldo de 0,80 →
banda 0,60-1,00 es de La Ponderosa: mostrarlo acá sería inventar el umbral de
otra máquina.** Es la regla de "no inventar umbrales" aplicada al caso que esa
regla no contemplaba, que la base no lo tenga.

**`ForcedRetract` da 0 en las 32.051 filas de toda la base** (28/07 al 11/08).
En la MISMA tabla `ManualMode` (12,7%) y `ManualDetach` (1,6%) sí varían, así
que el campo se escribe: o el equipo no usa el concepto, o no lo registra. Con
15 días de un solo tambo no se puede distinguir. **OJO CON LEER ese 0% como un
logro** —en La Ponderosa la retirada forzada es 9,99%— hasta tener más historia.

**"Tiempo entre ordeños" NO es comparable entre tambos, y la etiqueta promete
más de lo que mide.** La consulta suma solo los huecos *dentro del mismo día
calendario* (`CAST(inicio_anterior AS date) = CAST(inicio AS date)`). Una vaca
con sus tres ordeños del lado de acá de medianoche aporta dos huecos (~16 h);
si el primero le cayó antes de las 00:00, aporta uno (~8 h). Por eso dos tambos
de 3 ordeños/día dan La Ponderosa 15h29 y La Martina 12h07: no es que una pase
más tiempo afuera, es dónde le cae el corte del día. Sirve para seguir a UN
tambo en el tiempo, no para comparar dos.

Dos equivalencias que NO se forzaron al portar: `IsoDuration` no existe y se
calcula `EndTime - BeginTime` (que es lo que esa columna guarda en la rotativa,
ya verificado); y `LowFlowDurationInSec` (segundos) contra
`LowMilkFlowPercentage` (porcentaje) **no son la misma medida**, así que el
tiempo de colocación va NULL en vez de convertir una en otra suponiendo cuál es
la duración. El frontend ahora borra del gráfico toda serie que venga entera en
NULL: si no, la leyenda anuncia una medida que la sala no registra.

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
  colocación" e `IsoDuration` la duración del ordeño, ambos en segundos
  (`IsoDuration` = `SessionMilkYield.EndTime` − `.BeginTime`, verificado).
- **`CMSMilkYield.MilkConfirmTime` NO es el fin del ordeño**, es cuándo se
  confirma el registro: cae unos 6 minutos después. Ver la sección
  "Rendimiento de ordeño: qué campo es cada cosa".
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

## Rendimiento de ordeño: qué campo es cada cosa (01/08/2026)

Se replicó el reporte **"Rendimiento de ordeño"** de DelPro (el de una fila por
sesión, con rotaciones/horarios/producción/identificación). Quedó verificado
campo por campo contra el reporte real de tres días: **162 campos, 162
coinciden**. El script queda en el scratchpad (`verificar_delpro.py`) con los
tres días cargados: conviene volver a correrlo después de tocar `rutina.py`.

Los cuatro errores que hubo que corregir para llegar ahí — todos por leer un
campo "parecido" en vez del correcto. Ninguno daba error, solo números malos:

- **NO filtrar `IDTime IS NOT NULL` en `sql_rendimiento`.** Ese filtro
  descartaba en silencio las visitas cuya identificación falló del todo (no
  llegan a tener hora de ID, pero son ordeños REALES con leche). Eran 71 de
  1.508 en una sesión: faltaban ordeños, visitas, kg, y sobre todo los
  "desconocidos" daban 2 contra los 69 reales — la app parecía identificar
  mucho mejor de lo que identifica. Como esa consulta alimenta TODA la pantalla
  Rendimiento Sala, todos los gráficos venían subcontando ~5%. Para ubicarlas
  en el tiempo se usa `CreationTime` de respaldo (cae a 7,5s de `IDTime` en
  promedio) y viajan marcadas con `sin_id`, para no medir con ellas nada que
  arranque en la identificación.
- **Duración del ordeño = `SessionMilkYield.BeginTime` → `.EndTime`**, NO
  `CMSDeviceVisit.VerifiedTime` → `CMSMilkYield.MilkConfirmTime`.
  `MilkConfirmTime` es cuándo se CONFIRMA el registro, no cuándo terminó el
  ordeño: cae unos 6 minutos después. Con los campos viejos daba 11:19 contra
  los 05:17 del reporte, el doble. (`BeginTime`→`EndTime` coincide con
  `CMSMilkYield.IsoDuration`, que promedia los mismos 317s.)
- **Inicio y fin de sesión** son el primer `BeginTime` y el último `EndTime`,
  no la primera identificación ni la última confirmación. Daba ~10 minutos de
  más, y la duración se usa para todos los promedios por hora: ese error los
  corría a todos.
- **Rotaciones: NO estimarlas.** `CMSDeviceVisit.BatchOrRotation` es el número
  de vuelta que graba la propia máquina; contar sus valores distintos da el
  número exacto. La estimación vieja (duración ÷ mediana del tramo ID→retiro)
  daba 28 contra 22: ese tramo es más corto que la vuelta completa de la
  plataforma. `CMSDeviceVisit.ParlorSession` identifica la sesión sin
  heurísticas, por si alguna vez conviene usarlo en vez del corte por hueco.

`VerifiedTime` SÍ es el campo correcto en `sql_rutina` (score de calidad): ahí
se mide cuándo se COLOCÓ la pezonera, no cuándo empezó a bajar la leche. Son
dos preguntas distintas sobre los mismos datos, y por eso esa consulta sigue
exigiendo `IDTime` (sin identificación no hay tramo que puntuar).

**Las dos formas de quedar "sin dueño"**, que el reporte separa en columnas y
acá dan exacto: *vacas no identificadas* = nunca se leyó nada (sin hora de ID);
*transponders desconocidos* = SÍ se leyó un collar, pero no es de ninguna vaca
del rodeo (hay hora de ID y el animal igual resuelve al comodín). Y las *vacas
identificadas* del reporte NO son vacas distintas: son visitas con dueño
(1.508 − 67 − 2 = 1.439). Las vacas distintas son otra cuenta, y es la que
usan vacas/puesto y vacas/persona.

**DelPro TRUNCA los tiempos, no los redondea** (04:39:55 para una sesión de
16.795,66s). Redondear daba 1 segundo de más en 3 de las 9 sesiones probadas.
Y en su fila de totales **promedia** algunas columnas en vez de sumarlas: los
4.213 kg/h son el promedio de 4.686, 3.998 y 3.954, no los kg del día sobre
las horas del día.

**LAS SESIONES SALEN DE `CMSDeviceVisit.ParlorSession`**, no de cortar por
hueco. El criterio viejo (cortar donde hay un hueco > `GAP_SESION_MIN` y
después volver a unir con `_fusionar_hasta` hasta el tope de ordeños/día) se
equivocaba en los dos sentidos: partía una sesión con una pausa larga adentro,
y al reunirlas podía pegar dos rondas REALES en una (medido el 13/07/2026: una
"sesión" de 11,5 h con 46 rotaciones, que eran dos ordeños distintos). Con
`ParlorSession` las sesiones coinciden exacto con el reporte. El corte por
hueco queda solo de respaldo, para la sala convencional y para `sql_rutina`,
que no traen ese campo.

## Ordeños/hora POR RODEO: se mide con el rodeo del DÍA, no el de hoy

> Vale para medir la RUTINA de un día. Para REPLICAR un reporte de DelPro el
> criterio es el otro — ver "Los DOS criterios de rodeo" acá abajo.

Cada rodeo tiene su velocidad y sí se puede medir, pero hay que agrupar por
`CMSDeviceVisit.VisitedInGroup` — **el rodeo con el que la vaca pasó ese día**.
Agrupando por `BasicAnimal.[Group]` (el rodeo que tiene HOY) todo se rompe: una
vaca que cambió de rodeo desde entonces queda mal asignada en un ordeño viejo,
y las 22 rotaciones del 06/07/2026 salían "con vacas de varios rodeos", como si
la sala los mezclara. Con el campo correcto son bloques limpios —Rodeo 4 → 1 →
2 → 3 → 5 → 9— y solo se comparten las vueltas de transición (5 de 22).

Ese error mandó a dos callejones sin salida, ambos ya descartados:

- **Ventana entrada→salida del rodeo**: daba ~250 min de una sesión de 279 (o
  sea casi toda), porque las "rezagadas" mal asignadas la estiraban → 32
  ordeños/hora, absurdo.
- **Descontar los huecos "anormales"** (`_duracion_activa_grupo`): 422 a 462
  ordeños/hora, por ENCIMA del máximo físico de la sala.

**Cómo se calcula ahora** (`_grupos_sesion`):

1. Cada vuelta se asigna al turno que la ocupa, por mayoría de
   `VisitedInGroup`. Hace falta porque ese campo viene NULL en ~19% de las
   visitas identificadas; resolviendo la vuelta entera, esas visitas caen en el
   turno que de verdad estaba pasando y **no se pierde ningún ordeño**
   (verificado: los 1.460 de esa sesión quedan asignados).
2. El tiempo del turno es la SUMA de lo que duraron sus vueltas, y la duración
   de una vuelta va de su arranque **al arranque de la siguiente**. No de su
   arranque a su propio fin: ese tramo incluye el ordeño de la última vaca, que
   sigue mientras la vuelta siguiente ya empezó, y contarlo dos veces inflaba
   los tiempos un 50% (los rodeos de una sesión de 279 min sumaban 415). Medido
   así, las vueltas suman la sesión.
3. Tampoco sirve medir de la primera vaca del rodeo a la última: alcanza con
   que una vuelta suelta del final le toque por mayoría para estirar la ventana
   a toda la sesión (pasó el 13/07/2026: bloques de 347 min en una sesión de
   347).

Resultado: los rodeos se reparten alrededor del valor de la sala y la
comparación sirve. En julio de 2026, Rodeo 3 promedia 371 ordeños/hora y el de
enfermería 164.

## Los DOS criterios de rodeo, y cuándo va cada uno (05/08/2026)

Lo de arriba sigue valiendo para MEDIR la rutina de un día. Pero al comparar la
tabla nueva de retiradas forzadas contra el reporte real del tambo apareció el
otro lado del asunto:

**EL REPORTE DE DELPRO AGRUPA POR `BasicAnimal.[Group]`, EL RODEO DE HOY.**
Medido del 28/07 al 04/08/2026, rodeos 2 y 3, sesión por sesión (64 celdas):

    criterio                        celdas exactas
    VisitedInGroup (rodeo del día)   ningún día cierra, cada celda a ±1-4
    BasicAnimal.[Group] (hoy)        37/64, y 4 de los 8 días IDÉNTICOS

Los cuatro días que dan clavados son los recientes (31/07, 02/08, 03/08,
04/08); los totales del día coinciden en 11 de 16 (rodeo × día). Por eso ahora
conviven las dos cuentas y **no hay que "unificarlas"**:

- `_grupos_sesion` → agrupa por `_rodeo` (VisitedInGroup, resuelto por vuelta).
  Alimenta ordeños/hora, tiempo en sala, horas/día. Deja FIJO lo que pasó.
- `_retiradas_grupo_actual` → agrupa por el rodeo de hoy. Alimenta SOLO la
  tabla de retiradas forzadas, que existe para compararse contra DelPro.

**La contra del criterio de DelPro, medida: reescribe el pasado.** Si mañana se
mueven vacas de rodeo, el número de una fecha vieja cambia solo. Se ve en los
dos días que no cierran:

- **28/07**: Rodeo 3 da 41 contra los 79 de DelPro. Ese día el **12,9% de las
  visitas tienen un rodeo de hoy distinto del que pasaron** (contra 5,0% de un
  día normal como el 31/07): hubo un movimiento grande de vacas justo ahí, y
  con eso ni DelPro corriendo el reporte hoy daría lo que dio al exportarse.
- **29/07 y 01/08**: el día cierra exacto y lo que se corre son ±2 vacas entre
  la 1ª y la 2ª sesión. Es el CORTE entre sesiones: acá manda `ParlorSession`,
  el número que graba la máquina, y DelPro corta un par de vacas distinto. Sin
  su algoritmo no se puede afinar más; no vale la pena seguir tirando de ahí.

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
- **Hay días a los que les faltan ordeños enteros, y se detectan por el número
  de sesión de la máquina** (`CMSDeviceVisit.ParlorSession`, que es correlativo
  y global). El 27/07/2026 va de la sesión **925 a la 927: la 926 no existe**, y
  ese día quedaron 674 visitas contra las ~4.800 de un día normal (una sola
  sesión, de 00:22 a 02:11). No es un problema del cálculo ni del restore — la
  propia planilla del tambo, hecha desde DelPro, saltea el 27-jul. Antes de
  investigar un día que "da raro", mirar si sus `ParlorSession` son
  correlativas con las del día anterior.

## Entorno de desarrollo (esta PC)

Python no está en el PATH (`C:\Users\MAXI\AppData\Local\Programs\Python\Python312\`).
SQL Server Express local con poca RAM — consultas pesadas necesitan
`OPTION (MAXDOP 1, MAX_GRANT_PERCENT ...)` para no colgarse en
`RESOURCE_SEMAPHORE`. Detalle completo en la memoria de Claude de este usuario
(`delpro-entorno.md`, `delpro-deploy-produccion.md`) — son point-in-time,
verificar contra el código actual antes de asumir vigentes.
