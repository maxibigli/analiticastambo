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

**"Reinicié y sigue igual" en SERVER-DELPRO: NO asumir que el reinicio
funcionó, verificarlo.** Pasó repetidas veces esta sesión (código nuevo,
variables de entorno de Twilio) que cerrar la ventana y volver a abrir
`iniciar.bat` no mata el proceso anterior — queda un `servidor.py` viejo
sirviendo el puerto 5310 en paralelo o en su lugar, con el código/las
variables de ANTES. Antes de seguir investigando "por qué no cambió nada",
chequear:

    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -like '*servidor.py*' } |
      Select-Object ProcessId, CreationDate, CommandLine

Si `CreationDate` es de ANTES del cambio que se esperaba ver, o si aparece
más de un PID, ese es el problema — no un bug en el código. Se soluciona con
`Stop-Process -Id <PID> -Force` explícito sobre CADA proceso encontrado
(cerrar la ventana sola no siempre alcanza) y recién ahí levantar uno nuevo.

Archivos que NO están en git (estado propio de cada instalación, no se
comparten): `usuarios.json`, `secret_key.txt`, `alertas_canales.json`.
Cambios en `.py` requieren reiniciar el proceso; cambios en `templates/*.html`
se aplican solos (`TEMPLATES_AUTO_RELOAD=True`).

## Credenciales

Nunca hardcodear contraseñas/tokens en el código. Todo sale de variables de
entorno (`DELPRO_PWD_<TAMBO>`, `MILKY_PASSWORD`, `TELEGRAM_BOT_TOKEN`, etc. —
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

## San José (nuevo backup): dos bugs de "solo funciona con datos completos" (20/08/2026)

Al restaurar el backup nuevo de San José (convencional, sin cámara BCS) se
encontraron dos pantallas que se rompían enteras por faltar UNA tabla/forma
de columna — mismo patrón de fondo, dos causas distintas:

**Tablero de Diagnóstico → ORDEÑO daba "Error al leer Rendimiento Sala:
'ordenos'" en las 7 tarjetas de esa fila.** `/api/rutina/rendimiento` ya
tenía este bug arreglado (dispatch de `armar_identificacion` por sala, no
`rutina.armar_identificacion` fijo — las columnas de convencional son
`visitas`/`sin_duenio`, no `ordenos`/`desconocidos`), pero el Tablero de
Diagnóstico duplica esa lógica para su propio caché (lee las mismas consultas
crudas y las vuelve a analizar) y esa copia se quedó con la llamada vieja.
Arreglado en `app.py` (línea con `id_an = salas.de(tambo).armar_identificacion(...)`,
antes `rutina.armar_identificacion(...)` a secas). Como el error tumbaba TODO
el bloque `try`, hasta tarjetas sin relación con identificación (vacas por
puesto, litros/hora) quedaban en error — no eran 7 bugs, era 1.

**"Atención vacas EXPERIMENTAL" daba "esta sala no tiene los datos
necesarios".** `salud.sql_atencion_v2` pedía `BcsDailyData` (cámara BCS, add-on
de hardware — NO depende del tipo de sala, ver nota de `sql_bcs_vacas`) sin
condición, así que faltar esa UNA tabla tiraba `TablaNoDisponibleError` para
el índice ENTERO, aunque caída de leche y conductividad —lo que San José sí
tiene— estuvieran perfectas. Mismo critero que ya existía para las alarmas
propias de la rotativa (`con_alarmas_rotativa`): ahora `con_bcs` arma la
consulta con `CAST(NULL AS float)` en vez de la tabla cuando no está, y
`app.py::_tiene_bcs_de` decide eso chequeando `OBJECT_ID('BcsDailyData')` una
sola vez por proceso (se cachea para siempre, el esquema no cambia en
caliente). El motor de evidencia (`calcular_atencion_v2`) YA manejaba BCS
nulo por vaca sin cambios — con la tabla entera ausente, cada vaca simplemente
no aporta evidencia de ese sistema.

Verificado sin regresión: La Ponderosa (con BCS y con alarmas de rotativa)
sigue devolviendo la consulta completa igual que antes.

## BCS: curva objetivo por DEL en vez de un umbral parejo (17/08/2026)

`salud.BCS_BAJO`/`BCS_ALTO` (2,5 a 4,25 fijos, cualquier DEL) quedan
reemplazados por una curva objetivo por días desde el parto —lo que el propio
código ya avisaba como limitación conocida: *"no tenemos la curva objetivo
interna de DelPro"*. El usuario aportó una curva de referencia (imagen con
tres líneas: banda superior/objetivo/inferior contra DEL) y CONFIRMÓ los
valores leídos antes de tocar el código — no se inventó ni se leyó la imagen
a ojo sin chequear, por la misma razón que rige los umbrales de retirada: un
número equivocado acá no es un dato incompleto, es marcar como enferma a una
vaca sana o al revés.

**La curva confirmada** (`salud._OBJETIVO_BCS_PUNTOS`, interpolación lineal
entre puntos, constante antes del primero y después del último):

    DEL     objetivo
    ≤0      3,50   (preparto/seca)
    30      3,00
    100     2,75   (mínimo fisiológico, pico de producción)
    200     3,00
    300     3,30
    ≥350    3,50   (de vuelta al objetivo de seca)

**Tolerancia ±0,25 fija** (confirmada, no independiente por punto — por eso es
UNA curva objetivo más un margen constante, no tres curvas digitalizadas por
separado). `salud.objetivo_bcs(dim)` calcula el objetivo de cualquier DEL;
`/api/salud/bcs_vacas` anota cada vaca con su `objetivo`/`banda_inf`/
`banda_sup`/`fuera_de_rango` (None si no tiene DEL — sin eso no hay con qué
comparar) y devuelve además la curva completa (49 puntos, cada 10 DEL) para
que el gráfico la dibuje como referencia.

**Consecuencia medida, no un bug**: con la banda vieja (1,75 puntos de ancho)
casi ninguna vaca caía fuera; con la nueva (±0,25, mucho más angosta) La
Ponderosa da **761 de 1.668 vacas fuera de rango (46%)**. Es lo esperable de
pasar a un criterio mucho más estricto — vale saberlo antes de mirarlo en
producción para no leerlo como que "de golpe la mitad del rodeo se enfermó".

Los controles manuales "Score mínimo/máximo" de la pantalla se sacaron (ya no
tienen sentido: el rango ahora es por vaca, no un número que se pueda escribir
una vez para todo el rodeo). El filtro por estado reproductivo se mantiene
igual que antes.

**Tolerancia de vista, exploratoria (18/08/2026)**: el ±0,25 sigue siendo la
referencia oficial y fija (`salud.TOLERANCIA_BCS`, confirmada por el usuario)
— la tarjeta resumen (`vu-bcs-val`, KPI arriba de la pantalla) SIEMPRE la usa
sin importar nada más. Pero dentro de la tarjeta de detalle se agregó un
input "Tolerancia (± puntos)" que recalcula todo del lado del cliente
(clasificación por vaca, bandas del gráfico, orden de la tabla) sin pegarle
de nuevo al backend — ya viaja el `objetivo` de cada vaca en la respuesta, así
que ensanchar o achicar el margen es una resta, no una consulta nueva. Sirve
para explorar ("¿quiénes están MUY lejos del objetivo?" achicando el número)
sin tocar el criterio oficial que usa el resto de la app. Si el valor elegido
difiere de 0,25 se lo aclara en el texto de la tarjeta para que no se confunda
con el número de arriba.

## Rediseño visual: modo oscuro fijo + pulido de tarjetas/tipografía (23/08/2026)

El usuario pidió "fuentes más modernas, fondos claros, letras azules y tonos
DeLaval" y armamos una muestra (Artifact) para probar la dirección. Al ir a
aplicarla encontramos que la PALETA **ya estaba hecha**:
`static/css/lactia-tokens.css` tiene una escala clara Y una oscura completas
(azul DeLaval real, `--lac-blue-500: #0072CE`), con `index.html` ya cableado
a esos tokens (`--accent: var(--lac-accent)`, etc.) — nunca se veía distinto
porque la app sigue `prefers-color-scheme` del sistema operativo. Lo que
NO estaba aplicado a la pantalla real era el PULIDO de `lactia-components.css`
(`.lac-card`, `.lac-metric`, tipografía con `--lac-font-display`): esa hoja
existe con un sistema de componentes más prolijo, pero `index.html` tiene su
propio CSS ad-hoc en paralelo (`.card`, `.tile`) que solo toma los *colores*
de los tokens, no la sombra/espaciado/tipografía de `.lac-*`.

**Primero se probó forzar modo claro** (`<html data-theme="light">`,
aprovechando el guard `:not([data-theme="light"])` que ya tiene
`lactia-tokens.css` en su `@media (prefers-color-scheme: dark)` — pensado
exactamente para esto). Verificado funcionando: con el navegador forzado a
`dark`, la app se quedaba en claro igual.

**El usuario vio la muestra en oscuro (así la renderizó su navegador) y
prefirió ESE look — pidió oscuro fijo, no claro.** Se revirtió a
`data-theme="dark"` (mismo mecanismo, en el otro sentido) y en cambio se
llevó el PULIDO de la muestra a las clases reales de `index.html`:
  - `.tile .label` / `.card h2`: mayúsculas con tracking, `--lac-font-display`.
  - `.tile .value`: `--lac-font-display`, `font-variant-numeric: tabular-nums`.
  - `.tile`/`.card`: `box-shadow` de elevación, radio de `--lac-radius-card`.
  - `body`: `font-family` pasa a `var(--lac-font-body)` en vez de un stack
    hardcodeado — mismo resultado visual hoy (no hay webfonts cargadas, ver
    abajo), pero ahora sale de un solo lugar.
  - `.card h2` se mantuvo en `var(--accent)` (azul) a propósito — el pedido
    original decía explícitamente "letras azules", no se debe perder en
    futuros ajustes de esta sección.

**Deliberado: NO se agregó `<link>` a Google Fonts para Archivo/Inter.**
`lactia-tokens.css` ya lo explica en su comentario: en un tambo con internet
intermitente, depender de una red para tipografía es un riesgo que no vale
la pena — los `--lac-font-*` ya declaran system-ui/Helvetica Neue/Arial como
fallback, que es lo que efectivamente se usa hoy. Si en algún momento se
quiere Archivo/Inter de verdad, la forma correcta es empaquetar los archivos
de fuente localmente (WOFF2 en `static/`), no un `<link>` a Google Fonts.

Si en algún momento se quiere un TOGGLE (que cada usuario elija en vez de
quedar fijo), la base ya está: solo faltaría un botón que cambie
`data-theme` entre `"light"`/`"dark"` y lo guarde en localStorage.

## Resumen del Tablero por WhatsApp/Telegram/Email (23/08/2026)

Pedido del usuario: además de las alertas puntuales que ya existían (temp.
caudalímetro, U.F.C., score de rutina, incidencias — cada una avisa UNA VEZ
por condición nueva), quería un resumen PERIÓDICO con indicadores elegibles
del Tablero de Diagnóstico.

**Diseño**: se reusa el registro de `tablero.py` (`INDICADORES`/`config_de`/
`guardar`) en vez de crear un sistema de configuración aparte — cada
indicador ya tenía `activo` (se ve en el tablero); se le sumó
`incluir_resumen` (va en el resumen), con el MISMO patrón de guardado/merge.
Ninguno viene tildado por defecto: el tambo elige qué mandar.

`tablero.texto_resumen(armado, nombre_tambo)` arma el mensaje a partir del
`armar()` que YA se calculaba para /api/tablero — no dispara una consulta
nueva. Agrupa por `grupo` (Economía/Alimentación/Ordeño/Reproducción/
Sanidad/Instalación), con el mismo emoji de semáforo que el color CSS
(`bien`→🟢, `atencion`→🟠, `mal`→🔴, sin dato→⚪), y aclara "(dato viejo)"
cuando corresponde. Devuelve `None` si no hay nada tildado, para que el
llamador no mande un mensaje vacío.

`app.py::_revisar_resumen_tablero` se sumó a la MISMA lista de chequeos que
ya corre a las 8:00/20:00 (`_revisar_alertas_whatsapp`) — mismo horario, sin
agregar un segundo scheduler. A diferencia de `_avisar_si_nuevo` (una vez por
condición hasta que se resuelve), el resumen se manda SIEMPRE que el horario
toca y hay algo tildado, tenga o no algo fuera de rango — es un resumen, no
una alerta de umbral.

Botón "Probar resumen ahora" en ⚙ Configuración › Tablero
(`/api/tablero/config/probar_resumen`): guarda lo tildado en la tabla antes
de mandar (si no, probaría la config vieja) y lo manda ya mismo, mismo
criterio que "Probar envío" de Alertas.

## Rutina de ordeño: "Vacas identificadas" siempre 100%, y curva de score (20/08/2026)

Reportado por el usuario con capturas reales: en "Rutina de ordeño" (sala
convencional, La Martina) el componente "Vacas identificadas" daba SIEMPRE
100% — con peso (0%) en el detalle, señal de que el usuario ya lo había
apagado a mano en algún momento por desconfiar del número. Mientras tanto,
otras pantallas con el MISMO criterio (`BasicAnimal.Number = 0`) daban un
número real y variable (96,91% con 150 sin identificar el 14/08).

**Investigación, no se tocó nada hasta entender la causa.** Se armó un script
de diagnóstico de solo lectura (`diagnostico_identificacion.py`, ya borrado)
para comparar `sql_rutina` contra `sql_identificacion` fila por fila. Contra
la copia local (`lamartina_local`, 11/08) el resultado fue: `sql_rutina` SÍ
trae los comodines correctamente (368 de 3141 filas, dentro del margen de
±6h que usa para cortar sesiones) — el JOIN/WHERE no tiene el bug. La causa
de fondo en la base de PRODUCCIÓN nunca se identificó (no se pudo correr el
diagnóstico ahí antes de resolver el síntoma por otra vía) — queda pendiente
si en algún momento vuelve a fallar.

**Solución adoptada: la sesión ya no calcula su propio % de identificación.**
En vez de seguir dependiendo del conteo por sesión de `sql_rutina` (el que
venía fallando en producción por una causa no confirmada), "Rutina de
ordeño"/"Evolución" ahora usan el % REAL DEL DÍA COMPLETO que da
`sql_identificacion` — la misma fuente que ya se sabía confiable — y lo
aplican a las tres sesiones del día por igual. `app.py::_identificacion_pct_de`
cachea esto por rango de fechas (mismo patrón `allow_stale` + refresco async
que el resto de la app: no bloquea la pantalla, la primera carga puede seguir
mostrando el número por sesión hasta que el caché de identificación
calienta).

**Actualización (20/08/2026): el mismo bug estaba en la rotativa, en OTRA
consulta.** En un principio esto se dejó limitado a sala convencional (la
rotativa "no reportó el problema"), pero el usuario lo encontró también en
La Ponderosa: `rutina.sql_rutina` (rotativa) TODAVÍA filtra
`m.IDTime IS NOT NULL` — el mismo filtro que ya se había sacado de
`sql_rendimiento` por descartar en silencio los ordeños sin identificar (ver
el docstring de esa función). Nunca se tocó en `sql_rutina` porque el
síntoma pasó desapercibido hasta que se lo comparó a propósito contra
"Identificación de ordeños" (97,65% real contra el 100% fijo de "Rutina de
ordeño", mismo día). `_identificacion_pct_de`/`_refresh_identificacion_async`
ahora aplican a las DOS salas — `salas/rotativa.py` pasa `identificacion_pct`
en vez de ignorarlo. Verificado en `ponderosa_local` (30/07): 100% → 97,7%
real → score 97.

El texto de "info" de la tarjeta ahora aclara las dos cosas: el % del día
completo (el que decide el score) y el % de esa sesión puntual (para saber
qué franja horaria mirar si hay que revisar antenas/collares).

**Curva de score, pedida explícitamente por el usuario**: el score YA NO es
1 a 1 con el % real. Interpolación lineal entre estos puntos
(`rutina._CREDITO_IDENTIFICACION_PUNTOS`):

    % identificado    score
    100               100
    90                85
    80                30
    0                 0

Entre 100-90% baja suave (una falla aislada no es grave); por debajo del 90%
se desploma a propósito — cruzar ese piso tiene que gritarlo el número, no
acompañar la caída despacio. Mismo estilo que `_credito_prep` (colocación
≤90s), no es un criterio nuevo en el código.

## Salud del rodeo: número viejo pegado en pantalla, y tope configurable (17/08/2026)

Reportado como "el análisis da igual en cada tambo" — investigado a fondo antes
de tocar nada, porque hay una diferencia enorme entre "está mal calculado" y
"la pantalla no se actualizó". Fue lo segundo, más un hallazgo real de datos:

**"Atención (clásico)" y "Atención (experimental)" mostrando 15 en los dos
tambos NO es sospechoso por sí solo.** `TOP_ATENCION = 15` es un TECHO de
pantalla — las dos listas siempre muestran como máximo 15, sea cual sea el
tambo, mientras haya 15 candidatas o más. Verificado con datos reales: son 15
vacas DISTINTAS en cada tambo (RP 752, 474, 824... en La Martina, ningún
número en común con La Ponderosa).

**RCS > 300.000 y Casos nuevos RCS SÍ daban un dato real, y era 0 en La
Martina — no "el mismo que Ponderosa".** `MilkTest` tiene CERO filas en
`DDM_LAMARTINA`: no hay ningún control lechero cargado para ese tambo en la
base restaurada. No es un bug de código, es que falta cargar esa planilla en
DelPro para este tambo.

**"Fuera de score (BCS)" y el índice experimental correctamente dicen "no
disponible"** en vez de inventar un número: `BcsDailyData` no existe en el
esquema de La Martina (no tiene cámara BCS instalada). El mecanismo de
`TablaNoDisponibleError` → `_errores_tabla` → `{"no_disponible": true}` ya
existe y funciona bien acá (se armó para otro caso, `sql_bcs_vacas`, y el
índice experimental lo reusa igual sin cambios).

**LO QUE SÍ ERA UN BUG DE VERDAD**: `cargarSalud()` no limpiaba las tarjetas
grandes (RCS, BCS, Atención) al cambiar de tambo — se quedaban con el último
número pintado hasta que la consulta nueva terminaba. Con una base lenta
(SQL Express, ver más abajo) eso puede tardar minutos, y en el medio la
pantalla se lee como "da lo mismo en los dos tambos" cuando en realidad son
los números viejos del tambo anterior, todavía sin refrescar. Se arregló
limpiando el grid de tarjetas y cada sección al entrar a la pantalla, cambiar
de tambo o tocar "Actualizar" — pero NO en el reintento automático de cada 8s,
para no hacer parpadear las secciones que ya cargaron bien mientras una sola
sección lenta todavía no terminó.

**Tope de "Atención" configurable por tambo** (⚙ Configuración → Salud del
rodeo → "Vacas a mostrar en Atención", 5-300, vacío = 15). `calcular_atencion`/
`calcular_atencion_v2` ya aceptaban un parámetro `top` desde que se escribieron
— no hizo falta tocar el cálculo ni el caché, que sigue guardando TODAS las
filas evaluadas: el recorte a "las peores N" pasa después de leer el caché, así
que cambiar el tope no dispara ningún recálculo de la base.

## Salud del rodeo: pestaña "Análisis de RCS" (06/10/2026)

"Salud del rodeo" tiene una pestaña por sección (`#salud-tabs`, mismo patrón
que `#rutina-tabs`; el mapa pestaña → tarjetas es `SALUD_TABS` en index.html):
Resumen (composición del rodeo), Atención vacas, Atención (experimental),
**Células somáticas (RCS)**, Condición corporal, Conductividad, Producción de
leche y Problemas podales.

**El Resumen son tarjetas, una por sección, con SU porcentaje, y cada una abre
la pestaña de esa sección** (`pintarSaludResumen`). Qué mide cada porcentaje:
atención (clásico y experimental) = vacas que entran en la lista / vacas
evaluadas; células somáticas = vacas con RCS > 300.000 en su último control;
condición corporal = vacas fuera de su rango para su DEL; conductividad =
ordeños con conductividad relativa > 115 del último día COMPLETO (se descarta un
último día a medio cargar, con el mismo criterio del 60% que usa la estadística
de producción); producción = variación del kg por ordeño del tambo contra el
día anterior (ponderada por ordeños de cada rodeo). **Problemas podales va con
la cantidad y no con un porcentaje**: la cámara solo lista las vacas con alerta
y no informa cuántas evaluó, así que cualquier % sería inventado.

**Sin colores de semáforo en esas tarjetas, a propósito**: ninguno de esos
porcentajes tiene un umbral confirmado por el tambo, y pintar verde/rojo un
número sin umbral es inventarlo (misma regla que los umbrales de retirada).
La barra solo muestra la proporción.

Para el porcentaje de atención, `/api/salud/atencion` y `/atencion_v2` ahora
devuelven además `evaluadas` y `con_alerta`: la lista que se muestra está
recortada al tope de pantalla (15 por defecto, ⚙ Configuración), y ese tope es
un techo, no una medida: el viejo indicador "15 vacas a revisar" era siempre 15.
OJO al leer el experimental en la copia local de desarrollo: dio 77%
(1.282 de 1.656) contra 11% del clásico; con los datos de esa copia (cortan el
30/07) no se pudo confirmar si es un artefacto de la foto o el comportamiento
real del índice. Mirarlo en producción antes de interpretarlo.

Tres cosas que salen de tener las tarjetas en pestañas ocultas: (1) **un
gráfico que se dibuja con su tarjeta oculta queda de 0x0** (medido), así que
al cambiar de pestaña se le pide `resize()` a todos; (2) **las cámaras de
Problemas podales refrescan cada 4 s solo mientras se mira SU pestaña**, no
mientras se mira cualquier cosa de Salud; (3) los datos de todas las secciones
se siguen cargando juntos (los indicadores de arriba los necesitan), solo cambia
qué se ve.

La pestaña de **Células somáticas** junta el último control por rodeo, las
vacas con RCS > 300.000 y el **Análisis de RCS**: el RCS de los controles
lecheros en el tiempo, por rodeo, con rango de fechas elegible y comparación
contra otro período (el anterior de igual duración, otro rango, o ninguno).

**EL RODEO ES EL DEL DÍA DEL CONTROL, no el de hoy.** Medido en La Ponderosa:
los 10.840 controles tienen su `AnimalDaily` enlazado, y **solo el 20% de las
vacas sigue hoy en el rodeo donde estaba cuando se controló**. Agrupar por
`BasicAnimal.[Group]` —lo correcto para "último control", que mira el estado de
hoy— movería el 80% de las muestras a un rodeo equivocado y haría pasar un
cambio de rodeo por un cambio de RCS. El rodeo del día sale de
`MilkTest → AnimalHistoricalData.AnimalDaily → AnimalDaily.AnimalGroup`
(`salud.sql_rcs_historico`). Es el mismo criterio de "rodeo del día" ya
documentado para la rutina de ordeño.

**El backend manda agregados, no promedios**: un renglón por (control, rodeo)
con cantidad de vacas y SUMA de RCS. El frontend promedia cualquier rango
sumando (promedio de un período = suma/cantidad, no promedio de promedios), así
que cambiar fechas o comparar no vuelve a la base. Una sola consulta cacheada
(36 meses, ~45 renglones).

**FORCE ORDER no es decorativo.** La primera versión (joins directos) tardó
159 s en SQL Express: el optimizador cruzaba la subconsulta de rodeos —que
cuenta animales— contra los 10.840 controles. Agregando primero en una CTE
(0,3 s por sí sola) y forzando el orden, baja a 0,6 s en frío liviano; bajo
carga del propio warmup de la app midió 47 s. Sigue siendo el patrón de
caché asíncrono (202 y reintento), como el resto de Salud.

Dos cosas de lectura, deliberadas: en el gráfico **no se dibuja el promedio de
un rodeo en un control con menos de 10 vacas** (con 1-2 vacas "el promedio" es
esa vaca: Rodeo 5 dio 495.000 y 5.000 en controles seguidos y estiraba el eje;
la tabla sí las suma y lo avisa en la leyenda), y en la tabla un período con
menos de 30 muestras lleva ⚠. Los datos de RCS arrancan en noviembre de 2025
(un control por mes), así que el "período anterior" por defecto cae en parte
vacío si se mira hacia atrás de eso.

### Análisis de RCS: lactancia, DEL y dinámica entre controles (06/10/2026)

**Agrupar por lactancia o por días en leche, y la matriz rodeo × lactancia.**
Es el cruce que más cambia la lectura: el RCS sube con la edad de la vaca y con
los DEL, y los rodeos de este tambo se arman justamente por eso. Medido en la
copia local (7.000 muestras, 6 controles): **0-30 DEL da 229.887 y 18,9% de
vacas altas, contra 97.496 y 6,7% en 201-305 DEL**; Rodeo 1 en 1ª lactancia da
210.498 contra 112.341 en 2ª. Un rodeo que "se ve peor" muchas veces solo tiene
más vacas frescas o de fin de lactancia. `sql_rcs_historico` ahora devuelve un
renglón por (control, rodeo del día, lactancia 1/2/3+, tramo de DEL); los
tramos son 0-30, 31-100, 101-200, 201-305 y más de 305 (`salud.BANDAS_DEL`).
Todo sigue siendo sumable: cambiar la agrupación no vuelve a la base.

**Dinámica entre controles consecutivos (`salud.sql_rcs_dinamica`).** Cada vaca
contra SU control anterior, con el mismo 300.000: sana→alta es infección nueva,
alta→sana curación, alta→alta crónica. Dos situaciones que NO se mezclan:
`intra` (misma lactancia, ~31 días entre controles) y `seco` (la lactancia
subió: pasó por el seco y parió, ~86 días). Medido en la copia local:

    intra   infecciones nuevas 7,0% por control (527 de 7.540 sanas)
            curación 70% (544 de 777 altas)  ·  crónicas 233
    seco    curación durante el seco 80% (44 de 55 altas)
            infecciones nuevas al parir 13,5% (63 de 468 sanas)

Los pares `intra` con más de 60 días entre sí se descartan
(`MAX_GAP_INTRA_DIAS`): se salteó un control y un mes sin medir se leería como
curación. El rodeo es el del control ACTUAL. Las tasas de un período salen de
SUMAR los pares de sus controles, no de promediar tasas.

Reglas de lectura, deliberadas: una tasa lleva ⚠ si su base (el denominador)
tiene menos de 30 vacas; **no se muestra "cambio" si alguno de los dos lados
tiene menos de 30** (con 2 vacas, 0% contra 13% no es un cambio, es ruido); y
solo se colorea un cambio de al menos 1 punto Y 10% relativo, porque estas tasas
son más ruidosas que un promedio. En el gráfico no se dibujan puntos con menos
de 10 vacas en la base. Para infecciones nuevas y crónicas "mejor" es bajar;
para curación, subir.

### Análisis de RCS: cruce con producción de leche (06/10/2026)

Sección "RCS y producción de leche" en la misma pestaña: cuánta leche cuesta el
RCS alto. **La producción es `MilkTest.Yield`** (kg/día del día del control):
está en el 99,7% de los controles, contra el 67% de `AnimalDaily.TotalYield`
válido, y promedia 40,5 kg. No hay precio: el valor de la leche y la
bonificación por calidad (La Serenísima) no están en ningún sistema conectado,
los carga el tambo.

Dos miradas, y conviene leer las dos (no tienen por qué dar lo mismo):
- **Entre vacas** (`rcspAnalizar`): cada vaca contra el promedio de vacas SANAS
  de su mismo rodeo, lactancia y tramo de DEL. Medido en la copia local:
  producción −2,4 kg/día en 101-200.000, −3,4 en 301-500.000 y −4,7 en más de
  1.000.000; las vacas con RCS > 300.000 (10,2%) producen ~3,8 kg/día menos
  (−9,9%), unos 454 kg/día en el tambo. Es una asociación: una vaca que ya venía
  produciendo menos por otra razón también tiende a tener más RCS.
- **Dentro de la misma vaca** (`rcspEfectoDentro`, usa `dkg`/`n_dkg` de
  `sql_rcs_dinamica`): cuánto cambia su producción entre controles consecutivos
  cuando pasa de sana a alta, contra las que siguen sanas, **estandarizado por
  tramo de DEL**. Una infección nueva cuesta ~2,8 kg/día (368 casos). Sin
  estandarizar por DEL se subestima: las infecciones nuevas se juntan en las
  frescas, que todavía están subiendo su producción. Es la mejor estimación de
  lo que cuesta una infección; la de arriba, de cuánta leche falta hoy.

**La referencia tiene que ser del MISMO RODEO** (medido): la primera versión
comparaba contra las vacas sanas de todo el tambo y "Rodeo 4 - Baja" salía
perdiendo 15 kg por vaca alta, porque es un rodeo que se arma por baja
producción y el RCS no tenía nada que ver. Con la referencia por (rodeo,
lactancia, DEL) da 2 kg. Si en una celda hay menos de 10 vacas sanas
(`RCSP_MIN_REF`) se cae al promedio del tambo para esa lactancia y DEL. El
usuario elige si "sana" es RCS ≤ 100.000 o ≤ 200.000 (por defecto 200.000, el
más conservador: con 100.000 la pérdida estimada sube de 454 a 525 kg/día). La
pérdida por rodeo se divide por los controles del PERÍODO, no por los que tuvo
ese rodeo, para que la suma de los rodeos dé la del tambo (verificado: 454 y 454).

OJO al agregar SQL con `{{` o `{%` dentro de un template literal de JS en
`index.html`: Jinja lo lee como sintaxis propia y la página entera devuelve
500 (`${{a: 1}[k]}` rompió el render). Poner un espacio o usar un ternario.

## Caudalímetro de la rotativa: de scraping de CICLA a la API de Milky (07/11/2026)

`cicla.py` leía el caudalímetro (equipo `CER_001`, cliente 21) scrapeando el sitio viejo
`cicla.sisclac.com`. **Ese sitio dejó de servir el informe**: el login pasa pero la página de
cargas no se puede leer ("¿sesión vencida?"). Controllac compartió la API de Milky
(`https://milky.controllac.com.ar`, documento "APIs compartidas v1.1": login POST con token de 8 h,
consultas GET con `Authorization: Bearer`, paginadas) y `obtener_cargas` ahora la usa, con el
**mismo formato de salida**, así que `app.py` solo cambió de dónde saca las credenciales
(`cicla.credenciales()`). El código del sitio viejo quedó como `obtener_cargas_sitio_viejo`, sin usar.

**Credenciales: `MILKY_USUARIO` / `MILKY_PASSWORD`, en CADA instalación** (PC de desarrollo y
SERVER-DELPRO) con `setx`. **Se leen del REGISTRO de Windows cuando el proceso no las tiene**
(`cicla._variable`: entorno del proceso, después `HKCU\Environment`, después `HKLM`), en cada
consulta, así que no hace falta reiniciar. Esto salió de un problema real: `setx` guarda en el
registro pero no cambia los procesos ya creados ni los que lanza una ventana/tarea anterior al
`setx`, y el servidor de SERVER-DELPRO arrancó sin verlas aunque estaban bien guardadas (la tarjeta
decía "faltan las variables"); hubo que matarlo y relanzarlo copiándolas a mano. **Solo cubre las
MILKY_\***: `LASER_*`, `TWILIO_*`, `HASTEN_*`, `ANTHROPIC_API_KEY` y el resto siguen leyéndose del
entorno del proceso y siguen necesitando reinicio. Si el servidor corre con OTRO usuario de Windows
que el del `setx`, el registro de ese usuario no las tiene: usar `setx /M`. NO se reusan `CICLA_USUARIO/CICLA_PASSWORD`: la
primera prueba mandó esas a Milky por error de configuración y dio 401; son del sitio viejo y no
corresponde enviárselas a otro servidor. Se recorren TODAS las páginas (ya no existe el aviso de
"incompleto" del sitio viejo), se deduplica por `Id` (una carga nueva a mitad de la paginación
corre las páginas) y se descartan las anuladas.

**Qué campo es cada cosa, medido sobre 126 cargas reales (17/10 al 07/11/2026):**
  * `lts_cicla` = `LitrosTotal`; `LitrosFact` es IGUAL en las 126 (dicen lo mismo).
  * `nro_cliente` = `Tambo`, con los mismos códigos de siempre: 5003 y 4613 a La Serenísima
    (~23.500 L por carga), 23 a la guachera (~660 L). `DESTINOS` no cambió.
  * `temperatura` = `TempPromedio`. El **pH ya no existe** (la API no lo trae).
  * Una carga cuenta en el día en que EMPEZÓ (`FechaIni`, que es lo que filtra la API). Dura de
    minutos a ~4,5 h (mediana 2 h 13 min).

**`lts_declarados` = `LitrosEstimados` es un SUPUESTO SIN VERIFICAR.** Lo eligió el usuario
cuando no se pudo comparar una misma carga con el sitio viejo. Son números redondos (25.000 /
26.000 / 27.000, o 500 en la guachera): lo que se estima al cargar. Medido, contra eso las
cargas de camión dan mediana −7% y **47 de 65 salen con "alerta de diferencia" (umbral 3%)**; las
de guachera dan +30% y 64 de 65. Si "declarados" era otra cosa, la tarjeta "Entregas de leche
(CICLA)" muestra alertas que no significan nada. **No afecta la comparación contra La Serenísima**
(`/api/entregas/comparar` usa solo `lts_cicla`).

**La alerta de temperatura de las 8:00/20:00 cuenta SOLO las cargas de venta** (`es_venta`). De 50
cargas sobre 5 °C, 49 eran de guachera (mediana 6,6 °C, la leche de los terneros) y solo 1 de las
65 que se venden: contándolas todas, el aviso sonaba todos los días por leche que no llega al
comprador. **La tarjeta "Entregas de leche (CICLA)" sigue contando TODAS** en sus "Alertas de
temperatura (>4°C)" y en el listado de cargas fuera de rango (muestra el registro completo del
caudalímetro); allí la guachera sigue apareciendo.

**"Caudalímetro vs. Comprador" tiene sus propias fechas y un botón "Recalcular"** (07/11/2026).
Estaba fija en "los últimos 7 días": al cambiar el período en la tarjeta de CICLA la comparación
quedaba en el anterior y no había forma de recalcularla. Ahora arranca con el rango de CICLA (o la
última semana), "Aplicar" en CICLA le pasa el período y, si ya se había calculado, la recalcula sola;
si todavía no, sigue esperando su "Calcular" a pedido. El servidor ya aceptaba `desde`/`hasta`.

## Agente de IA: gráficos, tablas e informes (07/11/2026)

El chat "💬 Preguntale a LactIA" (`agente.py`) ahora puede dibujar y armar informes.
Hay un selector **Automático / Gráfico / Informe / Tabla** al lado del cuadro de texto
(`formato` en el body de `/api/agente/preguntar`). **Antes del cambio, las únicas consultas
con gráfico eran las de "Preguntar con IA" (SQL libre), que está BLOQUEADA en bases de
producción** (`tambos.es_produccion`); el agente, que sí corre en producción, contestaba solo
texto. Por WhatsApp no cambia nada: ahí no se ofrece `graficar` ni se aplica el formato.

**`graficar` es una herramienta de PRESENTACIÓN: no consulta nada.** El modelo la usa con
datos que YA le devolvieron las otras herramientas, así que no abre ninguna vía nueva a la
base (las tres capas de solo-lectura de la sección de abajo siguen igual). El servidor la
valida (tipo `line`/`bar`/`pie`, hasta 60 puntos y 6 series, un valor por etiqueta) y dos
reglas que salen de corridas reales con el modelo, no de suponer:
  * **Cada valor se busca entre los números que de verdad devolvieron las herramientas de
    ESA pregunta** (`_esta_entre`, tolerancia de redondeo). Si alguno no está, el gráfico sale
    con un aviso naranja "no figuran tal cual en los datos consultados". No prueba que el
    valor esté mal —un promedio calculado por el modelo también queda marcado—, pero obliga a
    decirlo. Un gráfico marcado que el texto ya no menciona (el modelo lo corrigió con otra
    llamada) se descarta (`_graficos_finales`).
  * **Series con escalas muy distintas (más de 20 veces) en un mismo eje se rechazan.** El
    modelo puso "RCS promedio (cél/ml)" de 92.312 a 157.743 junto a "% de vacas altas" de 6,9 a
    12,3: la segunda quedaba como una línea pegada al cero. Es una instrucción de un gráfico por
    unidad Y además un control en el servidor, porque la instrucción sola no alcanzó.

**El modelo escribió marcadores `[[g1]]` sin llamar nunca a `graficar`** (medido, formato
Informe). Los gráficos se ubican en el texto con esos marcadores en una línea sola. Ahora, si el
usuario eligió Gráfico o Informe y no hay ninguno, se le reclama UNA vez que los arme
(`exigio_graficos`) y los marcadores huérfanos se eliminan del texto. Con "Automático" no se
exige nada: una pregunta de un dato suelto no necesita gráfico.

**Informe = documento para leer e imprimir**, con secciones fijas en el prompt (Resumen, Cómo
evolucionó, Dónde está el problema, Impacto económico, Qué conviene mirar, Datos y límites).
Usa más tokens (`MAX_TOKENS_INFORME` 5.000 contra 2.000) y avisa si la respuesta se cortó por
largo. El botón **🖨 Imprimir / guardar PDF** abre una ventana en tema CLARO con los gráficos
redibujados como imagen: los lienzos de la pantalla usan los colores del tema oscuro y sobre
papel blanco los textos no se verían (verificado mirando el render). Las tablas son markdown
(`mdSimple` las dibuja, con las celdas numéricas a la derecha).

### Herramienta `salud_rcs_informe` y el impacto económico del RCS

La pestaña "Análisis de RCS" calcula todo en el NAVEGADOR (`rcspAnalizar` y compañía); el
agente no tiene navegador, así que `salud_rcs_informe.py` es un **porte a Python** del mismo
cálculo, servido en `/api/salud/rcs_informe` a partir de los mismos agregados que ya mandan
`/rcs_historico` y `/rcs_dinamica` (se extrajeron `_rcs_historico_filas` y `_rcs_dinamica_filas`
en `app.py` para que pantalla e informe lean exactamente lo mismo). **Verificado contra el
JavaScript original con datos reales de La Ponderosa: 66 cifras comparadas (por rodeo, por
tramo de RCS, efecto de una infección nueva, dinámica intra y seco), todas idénticas.** Si se
toca el cálculo de RCS en `index.html`, hay que tocarlo también acá y volver a comparar.

**La plata es lo único que no sale de la base, y tiene tres reglas:**
  * **Precio de la leche = el de la planilla de precios** (`precios_alimentos`, efectivo por
    litro). Sin precio NO hay pesos: el informe lo dice y da la pérdida en kg. Nunca un precio
    por defecto.
  * **La planilla que genera la app trae el precio de la leche marcado "← ESTIMADO"** (USD 0,35 ×
    tipo de cambio 1.500 = $525/litro) y su encabezado dice que los precios son estimaciones del
    sistema, no del tambo. Hasta ahora ninguna pantalla lo distinguía. `precios_alimentos.leer`
    ahora devuelve `leche_estimada` (hay una nota con "estimad" en la fila de la leche), y el
    informe lo pone primero (`AVISO_PRECIO_ESTIMADO`) con la orden de destacarlo junto a cada
    cifra en pesos. **Mientras esa nota siga en la planilla, los $ del informe son de ejemplo.**
    Se arregla cargando el precio real y borrando la nota «ESTIMADO», igual que el "SI" de los
    insumos. (La ruta de la planilla es la misma para todos los tambos salvo que se configure otra
    en `ruta_precios`: un tambo sin planilla propia usa el precio de La Ponderosa.)
  * **La pérdida se extrapola SOLO a los días que cubren los controles** (`dias_cubiertos`: cada
    control vale por el intervalo hasta el siguiente), no a todo el período pedido. Primera
    versión extrapolaba a los 191 días pedidos con un último control de hace cuatro meses:
    $73,9 millones contra $31,7 millones correctos. Si el último control es de hace más de 45
    días, el informe lo avisa. Es la leche que NO se produjo: no incluye bonificaciones ni
    castigos de calidad del comprador (no están en ningún sistema conectado) ni tratamientos ni
    descarte, y toma 1 kg = 1 litro, como Rentabilidad.

Los controles lecheros son MENSUALES: un período de menos de 2 meses trae un solo punto.

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

## WhatsApp: se probó migrar de Twilio a la Cloud API de Meta, y se volvió atrás (23-24/08/2026)

Se había reemplazado `whatsapp.py` (Twilio, de pago) por la API oficial de
Meta (WhatsApp Cloud API, nivel gratuito) — mismo canal "whatsapp" en
`_CANALES_MOD`, misma interfaz (`configurado()`/`enviar()`/`WhatsappError`).
El plan era mandar por PLANTILLA (no texto libre) porque Cloud API solo deja
texto libre dentro de las 24hs de que el destinatario escribe primero, y
estas alertas se disparan solas — sin plantilla fallarían casi siempre.

**Se volvió a Twilio.** El bloqueo fue ANTES de llegar a probar nada del
código: en la consola de Meta for Developers (WhatsApp → Paso 1. Probar), el
botón **"Solicitar número de prueba"** no hacía nada — sin error, sin
spinner, sin número asignado, incluso esperando el minuto que la propia
página dice que tarda y refrescando después. Se investigó bastante: la
consola tira errores de CSP por conexiones bloqueadas a dominios ajenos a
Facebook (`*.a.run.app`, `*.on.aws`, típico de una extensión del navegador
inyectando scripts) — pero el mismo problema persistió en una ventana de
incógnito sin extensiones, así que NO era eso. La causa real nunca se
identificó (¿la cuenta empresarial "Lactia" recién creada necesita algún
paso de verificación antes de poder pedir un número de prueba? ¿un problema
puntual del lado de Meta? no se llegó a confirmar). Se decidió no seguir
perdiendo tiempo ahí y volver a Twilio, que ya se sabe que funciona.

**Si se retoma en el futuro**: el código de la integración con Cloud API
(plantilla, `whatsapp._texto_para_plantilla()`, endpoint de Graph API) está
en el commit `a47c4ab` — se puede recuperar de ahí en vez de rehacerlo. Vale
la pena probar el camino de **"Paso 2. Configuración de producción"**
directamente (cargar un número propio, sin pasar por el número de prueba
gratuito), que es un flujo distinto en la consola y podría no toparse con el
mismo bloqueo. `whatsapp.py`, `app.py` y INSTALL.md ya están revertidos a
Twilio (commit siguiente a este).

## Resumen del Tablero en HTML por correo (23/08/2026)

El resumen periódico (`tablero.texto_resumen`) usa `*negrita*` y emoji de
semáforo (🟢🟠🔴⚪) — funciona bien en WhatsApp/Telegram (ahí sí se
interpreta el asterisco como negrita) pero en un mail de texto plano se veía
mal: el asterisco queda literal, y los círculos de color son un emoji más
nuevo que no todos los clientes de correo renderizan con color (se ven
huecos/grises aunque el dato no sea "sin dato").

**Dos arreglos separados:**
- `correo._texto_plano()` le saca los asteriscos y cambia los círculos por
  `[OK]`/`[ATENCION]`/`[MAL]`/`[SIN DATO]` — se aplica a CUALQUIER mensaje
  que se manda por correo (texto plano), no solo al resumen.
- `tablero.html_resumen(armado, nombre_tambo)` arma una versión HTML del
  MISMO resumen (mismo `armado`, no dispara consultas nuevas) con badges de
  color reales (`<span>` con `background-color`, no emoji) — el color se ve
  siempre, sin depender de la fuente del cliente. `correo.enviar_html(texto,
  html)` manda un mail `multipart/alternative` con las dos versiones; el
  cliente que no puede mostrar HTML cae al texto plano de respaldo.
  `app.py::_enviar_resumen_a_canales_activos` es la única función que llama
  a `enviar_html` — a WhatsApp/Telegram les sigue llegando el texto de
  siempre. Esto es SOLO para el resumen periódico, no para las alertas
  puntuales (temp./UFC/score/incidencias).

**Deliberado: el HTML es de fondo CLARO, no oscuro como el dashboard.** Un
mail HTML oscuro corre el riesgo de que el cliente le aplique su propio modo
oscuro encima y quede con mal contraste — el fondo claro es la opción segura
entre clientes de correo. Sigue usando el azul de marca (`#0072CE`) y los
mismos colores de semáforo que el dashboard (`--lac-ok`/`--lac-warn`/
`--lac-danger` de `lactia-tokens.css`) para mantener la identidad.

## Instancia única de servidor.py (23/08/2026)

En SERVER-DELPRO se detectó un pico de ~85 mensajes de Twilio en un rato
corto, sin que nadie lo disparara a mano. Se investigó a fondo del lado de
Windows (estado stale del Programador de tareas, tareas duplicadas —
revisando tanto `Execute` como `Arguments`—, carpeta de Startup, reinicio
por falla, trigger repetitivo, contenido de `iniciar.bat` byte a byte contra
el repo) y en ningún punto se encontró una causa concluyente: se vieron dos
procesos `servidor.py` vivos al mismo tiempo, arrancados casi en el mismo
segundo. Se decidió no seguir persiguiendo la causa exacta de Windows y
proteger la app a nivel de aplicación: si ya hay una instancia corriendo,
la nueva se cierra sola en vez de arrancar un segundo ciclo de alertas.

**Primer intento (DESCARTADO): bindear un socket propio sin `SO_REUSEADDR`
como prueba de "puerto ocupado".** Funciona en Linux pero NO es confiable en
Windows: se probó en vivo con dos instancias sobre un puerto descartable y la
"segunda" instancia no solo no detectó el conflicto — **le robó el puerto a
la primera** (quedó escuchando ella, la primera dejó de responder). Es un
comportamiento de Windows conocido: sin `SO_EXCLUSIVEADDRUSE` en el socket
que ya está en `LISTEN`, otro proceso puede bindear (y hasta reemplazar) ese
mismo puerto aunque ninguno de los dos use `SO_REUSEADDR`. Un probe de socket
normal, que es la técnica típica en Linux/mac, no sirve acá.

**Solución final: lock de archivo con `msvcrt.locking`** (`servidor.py`,
`_tomar_lock_de_instancia_unica`) — abre (o crea) `.servidor.lock` en la
carpeta del proyecto y le pide un lock exclusivo no bloqueante
(`LK_NBLCK`). Si otro proceso ya lo tiene abierto, tira `OSError` al toque.
El descriptor se guarda en una variable global y NUNCA se cierra a propósito
mientras el proceso vive — Windows libera el lock solo cuando el proceso
termina (incluso si se lo mata con `Stop-Process -Force`), así que no hace
falta borrar el archivo a mano entre reinicios. `.servidor.lock` está en
`.gitignore` (es puro estado de runtime, no contenido).

**Orden del chequeo, también importante**: el lock se toma ANTES de
`from waitress import serve` / `from app import app`, no después. Ese import
es pesado (Flask entero) y arranca hilos de fondo al cargarse — entre ellos
el ciclo de alertas de las 8:00/20:00 (`_bucle_alertas_whatsapp`). Si el
chequeo fuera después del import, una instancia duplicada quedaría viva ese
rato largo con su propio hilo de alertas ya corriendo antes de detectar el
conflicto y cerrarse — exactamente el problema que esto tiene que evitar,
solo que retrasado en vez de prevenido.

Verificado en la práctica (puerto descartable, no el 5310): segunda
instancia se cierra en ~140ms con el mensaje correcto, sin tocar el puerto
de la primera; al matar la primera el lock se libera y una tercera instancia
arranca normal.

La causa de fondo del lado de Windows (por qué se lanzaban dos procesos casi
al mismo tiempo) sigue sin identificarse — esto es una mitigación a nivel de
aplicación, no un diagnóstico. Si vuelve a pasar, revisar si el lock evitó
el doble envío (buscar en el log si hubo un intento rechazado).

## Parámetros Reproductivos y Calibración de Objetivos: movidos a Configuración (24/08/2026)

Pedido del usuario: esas dos pestañas, dentro de "🧬 Análisis Reproductivo",
eran configuración del tambo (parámetros que se pisan una vez y quedan; metas
que se cargan una vez y quedan), no un análisis que se mire seguido — no
tenían por qué competir por lugar con las siete pestañas de análisis de
verdad (Resultados, Indicadores de Preñez, Performance, etc.).

Se movieron sin tocar el backend ni las funciones que las llenan
(`cargarParametros`/`cargarMetas`, `pm-*`/`repro-metas-*` por id): solo
cambiaron de padre en el HTML, de `#page-repro` a `#page-configuracion`, y
pasaron a ser dos pestañas más de `#config-tabs` (⚙ Configuración / 🚦
Tablero / 📋 Check-list / 🧬 Parámetros Reproductivos / 🎯 Calibración de
Objetivos) en vez de paneles de `#repro-tabs`. Como las otras tres tarjetas
de Configuración, cargan sus datos de una sola vez al entrar a la página
(`cargarConfiguracion()`), no al tocar la pestaña — mismo patrón ya usado
ahí, distinto del de Análisis Reproductivo (que carga cada pestaña recién al
seleccionarla, por ser consultas pesadas).

**Análisis Reproductivo ahora arranca en "Resultados"** (antes arrancaba en
Parámetros porque era la única consulta liviana —20 filas— y abría la
página al instante; las demás son pesadas y quedan detrás del botón "▶
Calcular..." de `activarAPedido`). Resultados usa el mismo mecanismo de
carga diferida que ya tenían las otras pestañas pesadas, así que la página
sigue abriendo al instante — ya no por tener una pestaña liviana adelante,
sino porque ninguna pestaña que queda carga sola al entrar.

## Horario de alertas configurable: días de la semana + hasta 5 avisos por día (24/08/2026)

Antes `ALERTA_HORARIOS = (8, 20)` era una constante fija en `app.py`: revisaba
(y mandaba resumen del Tablero) todos los días a las 8:00 y 20:00, sin
excepción. El usuario pidió poder elegir qué días de la semana y hasta 5
horarios por día. Se agregó a `config_alertas.py` (el mismo archivo
`alertas_canales.json`, gitignored, que ya guardaba qué canal está tildado)
una clave `"horario"`: `{"dias": [0..6], "horas": ["08:00", "20:00", ...]}`
— `dias` con `0=lunes` igual que `datetime.weekday()`, `horas` como strings
`HH:MM` (no solo la hora: se probó en el navegador y truncar a la hora
redondeaba silenciosamente "06:30" a "06:00", un bug real que se corrigió
antes de darlo por terminado). Sin guardar nada, o con algo inválido,
`config_alertas.horario()` cae al comportamiento de siempre (todos los días,
8:00 y 20:00).

**Se aplica a las DOS cosas que ya compartían el mismo ciclo de fondo**
(alertas de umbral — temp./UFC/score/incidencias — y el resumen periódico
del Tablero): es el mismo horario para ambas, a propósito, para no armar un
segundo scheduler — mismo criterio que ya se había usado al sumar el resumen
del Tablero al ciclo existente en vez de uno aparte.

**La tarjeta "🔔 Alertas" entera se movió del Dashboard a ⚙ Configuración ›
🚦 Tablero de Diagnóstico** (24/08/2026, pedido explícito del usuario después
de ver el control por primera vez): canales (WhatsApp/Telegram/Email),
"Probar envío" y el horario, todo junto, como una segunda tarjeta debajo de
los límites del Tablero — no una pestaña nueva, las dos tarjetas comparten
la pestaña "tablero" de `#config-tabs`. El Dashboard ya no muestra el estado
de alertas. Se carga igual que las demás tarjetas de Configuración, de una
sola vez en `cargarConfiguracion()` (antes se cargaba al entrar al
Dashboard). El texto de arriba de la tarjeta ("Revisa todos los días a
las...") sigue siendo dinámico, armado con lo guardado.

**El ciclo de fondo se despierta solo cuando cambia el horario**
(`_horario_alertas_cambiado`, un `threading.Event`), en vez de esperar a que
se cumpla el horario VIEJO para recién ahí notar el cambio y calcular el
nuevo. Sin esto, guardar un horario nuevo a las 9:00 con el horario viejo
durmiendo hasta las 20:00 no habría tenido efecto hasta el día siguiente —
el mismo tipo de "guardé y no pasó nada" que ya avisan las notas de
`servidor.py`/Programador de tareas más arriba, evitado acá desde el
diseño en vez de parcheado después.

`_proximo_horario_alertas()` ahora mira hasta 8 días adelante (antes solo
hoy/mañana): con un solo día de la semana habilitado, el próximo horario
válido puede caer casi una semana después.

## Novedades del check-list de control en el resumen de WhatsApp/Telegram/Email (24/08/2026)

Pedido del usuario: poder mandar por el resumen periódico qué fallas y
novedades hubo en el check-list de control (⚙ Configuración › 📋 Check-list
de control tenía cumplimiento/adherencia/ranking en pantalla, pero nada
salía por los canales de alerta).

**No se inventó un criterio nuevo de "qué es una falla"**: `checklist.novedades()`
reusa `checklist._fallas()` (el mismo motor que ya usa el panel en pantalla,
con la regla ya documentada ahí — se mide por DÍA no por sesión, una racha de
días malos SEGUIDOS es UN problema, se cierra con el primer día posterior CON
CARGA sin ningún NO). Separa el resultado en dos listas:

  - **`abiertas`**: fallas sin resolver TODAVÍA. Mira 90 días para atrás (no
    la ventana corta del resumen) para no recortar mal una falla vieja que
    sigue abierta — si se cortara a pocos días, una falla de hace 40 días se
    mostraría como si tuviera 5.
  - **`resueltas`**: se resolvieron en los últimos 2 días (`dias_resueltas`).
    Una falla resuelta hace un mes ya no es noticia — sin este filtro, CADA
    resumen volvería a listar TODO lo que alguna vez se arregló.

`checklist.texto_novedades(datos, nombre_tambo)` / `html_novedades(...)`
son el mismo patrón de par texto+HTML que `tablero.texto_resumen`/
`html_resumen` (badges de color reales en el HTML, texto con emoji para
WhatsApp/Telegram, `None` si no hay nada que contar).

**Un solo tilde, no uno por tarea.** A diferencia del Tablero (donde cada
indicador tiene su propio "📲 Resumen"), acá no tendría sentido: no son
indicadores independientes, es UN informe de fallas. El tilde vive en
`config_alertas.py` (`checklist_resumen_activo()`/`set_checklist_resumen()`,
mismo archivo `alertas_canales.json` que ya guarda canales y horario) y el
control está en ⚙ Configuración › 📋 Check-list de control, con su propio
"Probar novedades ahora" (`/api/checklist/config/probar_resumen`, mismo
criterio que el de Tablero).

`app.py::_revisar_novedades_checklist` se sumó a la MISMA lista de chequeos
del ciclo de alertas (junto con `_revisar_resumen_tablero`) — mismo horario
configurable, sin un tercer scheduler. `checklist.novedades()` lee de
`checklist.db` (SQLite propio), no de DDM, así que no hay caché pesada que
cuidar acá como sí la hay para rutina/incidencias.

Probado con datos sintéticos (sin tocar `checklist.db` ni mandar nada real):
una falla abierta hace 6 días aparece con su comentario; una resuelta hace 2
días aparece con "2 días abierta"; una resuelta hace 4 días queda afuera
(más vieja que la ventana de 2 días); el caso sin nada que contar da `None`
en vez de mandar un mensaje vacío.

## "Preguntale a IA" por WhatsApp (24/08/2026)

Pedido del usuario: además de mandar alertas, poder PREGUNTARLE algo a la IA
de LactIA por WhatsApp — "solo yo por ahora", pero con una lista de números
autorizados armable desde la interfaz para el día que sean más.

**Reusa el agente, no el SQL-a-ciegas.** `/api/preguntar` (SQL generado por
IA) está bloqueado a propósito para tambos de producción (`tambos.es_produccion`).
`/api/agente/preguntar` (`agente.py`, que encadena las mismas herramientas
que ya usa cada pantalla) SÍ corre en producción — es el que se reusa acá
(`agente.responder(pregunta, tambo)`), así que preguntar por WhatsApp
funciona también en La Ponderosa en vivo.

**Cada número autorizado queda atado a UN tambo fijo** (`whatsapp_ia.py`,
JSON gitignored como `alertas_canales.json`/`tablero_umbrales.json`): quien
pregunta no tiene que aclarar de qué tambo habla, y no hay forma de que la
pregunta se cuele para el tambo equivocado. Se eligió esto en vez de "elegís
el tambo en el mensaje" por simplicidad — ver conversación si se quiere
cambiar. Pantalla de alta: ⚙ Configuración › 🤖 IA por WhatsApp (mismo
patrón de tabla editable que 📋 Check-list, pero sin versionado — es una
lista simple que se guarda entera).

**El webhook (`app.py::webhook_whatsapp`, POST `/webhook/whatsapp`) es
público a propósito** (agregado a `_RUTAS_PUBLICAS`, si no el
`before_request` de login lo redirige a `/login` y Twilio nunca recibe una
respuesta útil) — Twilio no tiene sesión. Su seguridad son dos cosas: (1) la
firma `X-Twilio-Signature` (valida con el paquete oficial
`twilio.request_validator.RequestValidator` — la propia documentación de
Twilio dice explícitamente "no implementes tu propia validación de firma", y
con razón: un HMAC mal armado a mano puede fallar en silencio y abrir un
agujero) y (2) que el número de origen esté en la lista de autorizados.

**La firma se valida contra la URL pública fija
(`https://www.analiticastambo.com/webhook/whatsapp`, override por
`LACTIA_URL_PUBLICA`), NO contra `request.url`.** La app corre detrás de
Cloudflare Tunnel sin `ProxyFix` ni manejo de `X-Forwarded-*`, así que
`request.url` ve la URL interna que arma waitress, no la que Twilio
realmente llamó — usarla habría hecho fallar la validación siempre.

**La respuesta le llega a quien preguntó, no al número fijo de alertas.**
`whatsapp.enviar()` ganó un segundo parámetro opcional `destino` (si no se
pasa, sigue usando `WHATSAPP_TELEFONO` como hasta ahora, para no romper las
alertas existentes). El webhook responde en un hilo de fondo
(`threading.Thread`, no en el propio request) porque el agente puede tardar
varios segundos —Twilio no necesita esperar la respuesta real, solo un 204
rápido confirmando que se recibió el mensaje.

**Nueva dependencia: `twilio>=9.0`** (solo por `RequestValidator` — el envío
sigue siendo `requests` directo, sin el SDK, como todo el resto del código).
Hace falta `pip install -r requirements.txt` en cada instalación después de
este cambio, si no el proceso no arranca (`ModuleNotFoundError`).

**Probado end-to-end con mocks** (Flask `test_client`, `agente.responder`/
`whatsapp.enviar`/`agente.api_disponible` reemplazados, sin gastar tokens de
IA reales ni mandar WhatsApp real): firma inválida → 403; firma válida con
número no autorizado → 204 sin llamar al agente; firma válida con número
autorizado → 204, agente llamado con el tambo y la pregunta correctos, y la
respuesta se manda al número que preguntó (no al fijo de alertas). Falta
probar en producción con un mensaje real, una vez cargado el webhook en la
consola de Twilio (ver INSTALL.md).

## Alertas: un solo mensaje por ciclo, no uno por condición (24/08/2026)

Pedido del usuario tras ver ~20 mensajes de WhatsApp/mails en un solo ciclo
de las 8:00: cada carga de CICLA fuera de rango, cada puesto de la rotativa
con incidencias, etc. mandaba su PROPIO mensaje (vía `_avisar_si_nuevo`,
dedup por condición individual — "puesto 21 con incidencias" era una clave
distinta de "puesto 30 con incidencias"). Un día con varios problemas a la
vez eran decenas de avisos sueltos.

**Se sacó `_avisar_si_nuevo` y las cuatro funciones que lo llamaban**
(`_revisar_cicla_whatsapp`/`_revisar_laser_whatsapp`/`_revisar_rutina_whatsapp`/
`_revisar_incidencias_whatsapp`, junto con `_alertas_avisadas`/`_alertas_lock`,
que quedaron sin uso) **y se reemplazaron por `_lineas_alertas_puntuales`**:
UNA línea de texto por tipo (no por condición individual), con la cuenta y
el rango de hoy — "🌡️ CICLA: 3 carga(s) hoy con temperatura sobre el umbral
(7,5 – 17,2°C, umbral 5,0°C)" en vez de tres mensajes, uno por carga.

**Esto cambia la semántica de fondo, a propósito**: antes era "avisame la
PRIMERA vez que aparece cada problema, no de nuevo hasta que se resuelva".
Ahora es un snapshot del día completo en CADA ciclo (8:00/20:00), mismo
criterio que ya tenía el resumen del Tablero y las novedades del Check-list
— si un problema sigue activo, vuelve a aparecer en el próximo ciclo (no se
"pierde" entre el resumen y la alerta puntual), pero no hay forma de saber
del mensaje solo si es nuevo o viene de antes. Fue lo que pidió el usuario
explícitamente (ver conversación) al priorizar UN mensaje por sobre "avisame
solo lo nuevo".

**`_revisar_alertas_whatsapp` arma UN mensaje combinado**, en el orden
pedido: Tablero de Diagnóstico → alertas puntuales (CICLA/La Serenísima/
rutina/incidencias) → Check-list. Si una sección no tiene nada que contar,
se omite (no dice "sin novedades" para cada una) — si NINGUNA tiene nada,
no se manda nada. Un solo `_enviar_resumen_a_canales_activos(texto, html)`
al final, no un envío por sección. El HTML del mail concatena los bloques
de `tablero.html_resumen`/`_html_alertas_puntuales` (tarjeta nueva, mismo
estilo visual: badges de color, fondo claro)/`checklist.html_novedades` —
cada uno ya es un `<div>` autocontenido, así que apilarlos no rompe nada.

Probado con mocks (sin tocar CICLA/La Serenísima/DDM reales): agrega
correctamente varias cargas en una sola línea con el rango de temperatura;
un solo valor no muestra un rango redundante ("55" en vez de "55 – 55");
las tres secciones aparecen en el orden correcto en un solo envío; si no hay
nada que avisar, no se manda nada; si el check-list está desactivado, esa
sección se omite sin afectar al resto.

## Bitácora de incidentes y reparaciones (24/08/2026)

Pedido del usuario, surgido de la propia conversación sobre las alertas de
incidencias: un lugar donde CUALQUIER empleado pueda registrar un incidente
o una reparación, a cualquier hora, fácil desde el celular — a diferencia
del Check-list de control, que tiene agenda fija (por ordeñe/diario/
semanal). `bitacora.py` + `templates/bitacora.html`, mismo patrón que
`/checklist/`: página propia (no vive dentro de `index.html`), mismo login
que el resto de la app pero sin el menú de admin, instalable como PWA
(`/bitacora/manifest.webmanifest` + `/bitacora/sw.js`, scope propio para no
pisar el caché de la app principal ni el del check-list), cola offline en
`localStorage` para cuando no hay señal en la sala, fotos redimensionadas en
el celular antes de subir. Base propia `bitacora.db` + `bitacora_fotos/`,
gitignored, mismo criterio que `checklist.db`.

**A propósito NO tiene la agenda ni el versionado del Check-list**: es una
lista simple (tipo, sector/equipo, puesto opcional, descripción, foto) que
cualquiera agrega en cualquier momento, con un estado abierto/resuelto
explícito (un botón "Marcar resuelto"), no derivado de "la próxima vez que
esa tarea dé OK" como las fallas del check-list.

**Se cruza con la alerta de incidencias de la rotativa** (pedido explícito
del usuario, no una idea mía sin confirmar):
`bitacora.abiertos_por_puesto(tambo)` da `{puesto: fecha del reporte más
viejo abierto}`, y `app.py::_lineas_alertas_puntuales` lo usa para agregar
"ya reportado el DD/MM" al lado del puesto en la línea de incidencias — así
la alerta no repite "puesto 21 roto" como si fuera nuevo cada vez que se
dispara, si ya hay alguien atendiéndolo. Solo cruza por `puesto` (entero);
un registro sin puesto (Usher, Piatinero, etc.) no tiene con qué cruzarse y
no aparece en `abiertos_por_puesto`.

**Sin rol de admin para usarla** (`/bitacora/` y sus `/api/bitacora/*` NO
llevan `@auth.requiere_rol("admin")`, mismo criterio que `/checklist/` y
`/api/checklist/plantilla`/`corrida`): cualquier usuario logueado, admin u
operario, puede cargar y resolver — es justamente pensada para que el
empleado no necesite una cuenta especial.

Probado: validaciones (tipo/sector/descripción/puesto inválidos, todas
rechazadas con `ValueError`/400), dedup por `offline_id` (mismo criterio que
`checklist.guardar_corrida`), foto subida y servida byte a byte igual a la
original, abrir → aparece en `/api/bitacora/abiertos` → resolver → ya no
aparece, doble resolución rechazada, y el cruce con la alerta de
incidencias (puesto con reporte abierto muestra "ya reportado el ...",
puesto sin reporte no) — todo con rutas HTTP reales via el test client de
Flask, no solo las funciones sueltas. Probado también a mano en el
navegador: cargar un registro, verlo aparecer en "Abiertos", resolverlo.

## /api/iot/pantalla: endpoint público para una pantalla ESP32 (25/08/2026)

El usuario quiere mostrar el estado del gateway IoT (lavado/barrido de la
rotativa + sensores de temperatura/humedad) en una pantalla táctil externa
(Waveshare ESP32-P4-WIFI6-Touch-LCD-7B) — un microcontrolador que hace un
`GET` periódico por WiFi, no un navegador con sesión.

**Público a propósito** (sumado a `_RUTAS_PUBLICAS`, mismo criterio que
`/webhook/whatsapp`): un ESP32 no puede iniciar sesión como un usuario. Se
expone lo MÍNIMO — el mismo estado que ya muestra el panel de Monitoreo IoT
(`iot_monitoreo.estado_sistema`/`lecturas_actuales`), sin agregar ninguna
consulta nueva — reempaquetado en un JSON chico y plano (`{estado, desde,
sensores: {clave: {valor, unidad, label}}}`), pensado para parsearse fácil
con la poca memoria de un microcontrolador (`ArduinoJson` o el `cJSON` de
ESP-IDF). Nada de lo que devuelve es sensible (ni animales, ni plata): es
exactamente el mismo dato que ya se ve en el dashboard sin login.

Si el ESP32 termina viviendo en la red del tambo, conviene que apunte a la
IP local de la PC en vez de salir a internet y volver por Cloudflare — más
simple y no depende de que ande la conexión a internet del tambo para ver
un dato que está ahí mismo, en la red local.

## Actuadores/Entradas del gateway M300 en la pantalla ESP32 (28-29/08/2026)

Pedido del usuario: mostrar en la pantalla táctil (ver
`C:\Users\MAXI\CLAUDE\esp32-pantalla-lactia\`) un panel de las 8 entradas
(sensores on/off) y 8 salidas (actuadores) que tiene disponibles el gateway
PUSR M300, con botones para activar las salidas manualmente.

**Las salidas son PULSADORES, no llaves.** Tocar una activa la salida un
ratito (`iot_lavado.DURACION_PULSO_S = 0.5`) y se suelta sola — mismo
concepto que un botón de arranque de un tablero real, más seguro que dejar
una salida prendida indefinidamente desde una pantalla que no ve el equipo.
La pantalla pide CONFIRMACIÓN (tocar dos veces) antes de mandar el pulso,
justamente por controlar equipos físicos reales.

> **Esto vale para el BOTÓN de la pantalla, y desde los comandos de voz ya
> no vale para la salida en sí**: un "prender bomba de agua" dicho en voz
> alta la deja SOSTENIDA hasta que alguien pida apagarla. Ver "Comandos de
> voz Jarvis" al final de este archivo.

**`iot_lavado.py` es el ÚNICO dueño de la conexión Modbus al M300** — antes
solo sondeaba 2 entradas (lavado/barrido), ahora sondea las 8
(`CANALES` en ese archivo) y además revisa una tabla `comandos_actuador`
en cada ciclo para ejecutar pulsos pedidos desde la pantalla. `app.py` NO
abre su propia conexión Modbus: solo ENCOLA el pedido en esa tabla
(`iot_monitoreo.solicitar_pulso`) — abrir una segunda conexión TCP en
paralelo se arriesgaba a pisarse con el polling continuo.

**El endpoint que activa un actuador (`POST /api/iot/pantalla/actuador`) se
bloquea si el pedido llega por el túnel de Cloudflare** (chequea el header
`CF-Connecting-IP` que agrega cloudflared) — a diferencia de
`/api/iot/pantalla`/`/historico`/`/io` (de solo lectura, públicos sin
restricción), activar un equipo real desde fuera de la red del tambo es un
riesgo que no vale la pena. Decisión explícita del usuario entre tres
opciones (token propio / solo LAN / las dos) — eligió solo LAN.

**Nombres personalizados de cada canal, desde la web** (⚙ Configuración ›
🔌 Entradas/Salidas, 29/08/2026): `iot_canales.py` guarda
`{clave: nombre}` en `iot_canales_nombres.json` (gitignored, mismo
criterio que `alertas_canales.json`) — deliberadamente SIN import de
`iot_monitoreo`/`iot_lavado` (que sí lo importan a él) para no armar un
ciclo; la lista de claves válidas está declarada ahí mismo. Dejar el campo
vacío en la tabla de la web vuelve al nombre genérico ("Entrada 3",
"Actuador 1"), no hace falta borrar una fila. `iot_monitoreo.panel_io()`
aplica estos nombres por encima de `ENTRADAS_PANEL`/`SALIDAS_PANEL` — tanto
la pantalla ESP32 (que lee `/api/iot/pantalla/io`) como cualquier pantalla
web futura ven el nombre que cargó el tambo, sin tocar código.

**Pendiente, y es una decisión del tambo, no del código**: qué actuador
físico va en cada una de las 8 salidas todavía no está definido — hoy son
genéricas y no hay ningún cable conectado a la mayoría. Activar un pulso en
una salida sin nada conectado no hace nada (ni bueno ni malo); antes de
cablear algo de verdad ahí, confirmar qué dirección Modbus (0 a 7, ver
`iot_lavado.ACTUADORES`) corresponde a qué salida física del M300.

**Direcciones Modbus de DI3-DI8/DO1-DO8: asumidas por convención, NO
verificadas contra el M300 real.** El código asume numeración secuencial
(DI*n* física = dirección *n-1*, mismo criterio ya confirmado para DI1/DI2
-- lavado/barrido, ver el comentario de `CANALES` en `iot_lavado.py`). Es
la convención más común en estos gateways, pero nunca se probó para el
resto de los canales porque no hay nada cableado todavía. Antes de confiar
en una entrada/salida nueva (DI3 en adelante, cualquier DO), conviene
confirmarla contra la config web del M300 (si el tambo tiene acceso) o
probarla en el lugar con un jumper/botón y mirar qué tarjeta prende en la
pantalla -- no asumir que el número de la tarjeta es el terminal físico
sin haberlo visto andar una vez.

## Lavado Automático: secuenciador de etapas real (29/08/2026)

Aclaración importante del usuario sobre lo que "Lavado Automático" tenía
que hacer: NO es un visor del historial de lavado/barrido de la rotativa
(esos DI son señales de un sistema EXTERNO, propio del tablero de la
rotativa) -- es que LA APP misma controle un ciclo de lavado propio,
prendiendo relés de salida del M300 (bombas de agua/espuma/desinfectante)
en hasta 3 etapas configurables, cada una con sus propios relés y duración.

**Sin duración por defecto, a propósito** (mismo criterio que los umbrales
de retirada / preparación en `rutina.py`, ver más arriba): cuánto tiene que
durar cada etapa depende de lo que tarda la vuelta de ESTA rotativa en ESTE
tambo. `lavado_programa.py` rechaza guardar una etapa con relés elegidos
pero sin duración, y el botón de iniciar en la pantalla no hace nada si no
hay ninguna etapa configurada.

**El secuenciador vive en `iot_lavado.py`, no en Flask.** Mismo motivo que
`ejecutar_comandos_pendientes` (pulsos manuales de Actuadores): es el único
proceso dueño de la conexión Modbus al M300. `procesar_ciclo_lavado()`
corre en el MISMO ciclo de sondeo de 3s (no en un hilo aparte): lee una fila
de estado (`ciclo_lavado_estado`, con `comando` puesto por Flask e
`activo`/`etapa_actual`/`etapa_inicio` que actualiza este mismo proceso), y
avanza de etapa cuando el tiempo transcurrido supera la duración configurada.
Como corre en el ciclo de 3s, las etapas avanzan con esa precisión -- de
sobra para un ciclo que dura minutos, y evita tener que coordinar dos
procesos escribiendo Modbus al mismo tiempo.

**Cancelar NO pide confirmación en la pantalla; iniciar sí** (tocar dos
veces, mismo patrón que los pulsos de Actuadores). Frenar bombas reales es
la dirección segura -- ponerle una traba justo ahí sería quitar velocidad
de reacción exactamente cuando más importa.

**Si la configuración cambia mientras el ciclo está corriendo** (se guarda
una etapa 4 menos, por ejemplo) y la etapa activa queda fuera de rango,
`procesar_ciclo_lavado` simplemente deja de avanzar (no cae ni inventa una
etapa) hasta que alguien cancela -- caso raro, pero mejor quedarse quieto
que apagar/prender algo que ya no está definido.

## El M300 necesita mapeo EXPLÍCITO por punto para exponer algo por Modbus TCP (29/08/2026)

Se armó todo el código de Actuadores (pulsos manuales) y Lavado Automático
asumiendo que las 8 salidas del M300 respondían a direcciones Modbus 0-7 de
coils, igual que las 2 entradas ya usadas (DI01/DI02, direcciones 0/1) --
**la escritura fallaba con `ExceptionResponse(..., exception_code=2)`
("Illegal Data Address") en TODAS las salidas.** No era un problema de
cableado ni del código: el M300 (plataforma "USR IoT", edge computing) NO
expone nada por Modbus TCP salvo que se lo agregues a mano a una tabla de
mapeo. Se descubrió revisando la interfaz web del gateway (`http://192.168.1.1/`,
sección **Edge Computing → IO Module → Protocol → Modbus TCP → Node mapping
table`**) -- esa tabla tenía **UNA sola fila** (`DI01 → 10001, Only Read`),
nada más. Por eso DI01 (lavado) funcionaba desde siempre y absolutamente
nada más (ni DI02, ni ninguna salida) estaba realmente accesible desde
afuera, aunque la propia web del M300 sí las leyera/controlara internamente.

**Notación de direcciones de esa tabla** (convención Modicon clásica, la
misma que ya se documentó para DI01/DI02):

    0X   Coils            (bit, lectura/escritura)   -- para las 8 salidas
    1X   Discrete Inputs  (bit, solo lectura)         -- para las entradas
    3X   Input Registers  (16 bit, solo lectura)      -- analógicas
    4X   Holding Registers(16 bit, lectura/escritura)

Al agregar un punto nuevo (botón "Add" → elegir tipo 0X/1X/... y dirección
inicial → "Add points" → elegir slave **"Local_IO"**, NO "Slave_Status" ni
"EM60" -- esos dos son perfiles de ejemplo/no relacionados) el M300 asigna
direcciones SECUENCIALES automáticamente a partir de la inicial elegida.

**Mapeo actual, ya cargado en el M300** (coincide EXACTO con las direcciones
que ya usaba el código -- no hizo falta tocar `iot_lavado.ACTUADORES` ni
`CANALES` para nada de esto):

    Punto (M300)   Tipo   Dirección Modbus   Usado como (código)
    DI01           1X     10001              lavado_rotativa (dirección 0)
    DI02           1X     10002              barrido_rotativa (dirección 1)
    DO11           0X     00001              do_1 (dirección 0)
    DO12           0X     00002              do_2 (dirección 1)
    DO13..DO18     0X     00003..00008       do_3..do_8 (direcciones 2-7)

**Hardware real: 2 módulos de expansión, USR-IO0080 (8 DO) y USR-IO0440 (4
DO + 4 AI)**, conectados al bus propio del M300 (no por un puerto RS485
genérico) -- por eso aparecen bajo el slave "Local_IO" y no como un
"Modbus_RTU" externo. Con la base (DI01/DI02, DO01/DO02) más las dos
expansiones, el M300 tiene en total: **2 DI, 14 DO (2+8+4), 6 AI (2+4)** --
bastante más de lo que el diseño de "8 actuadores" necesita. Se usó
`DO11`-`DO18` (el módulo dedicado de 8 relés) para los 8 actuadores; `DO01`,
`DO02` y `DO21`-`DO24` quedan sin mapear/sin usar por ahora. **NO hay
ningún módulo de expansión de DI** -- las 8 "entradas" que ya existen en el
código/pantalla (`di_3` a `di_8`) NO tienen nada físico atrás y
probablemente nunca lo tengan salvo que se agregue esa expansión.

**Verificado en vivo, de punta a punta**: escribir `True` en el coil 0 (vía
un script Python aislado con pymodbus, sin pasar por `iot_lavado.py`) hizo
que el switch de `DO11` se moviera en la propia web del M300 Y que el relé
físico prendiera de verdad (confirmado por el usuario mirando el módulo).
Un pulso de 1 segundo no se llegó a percibir a simple vista (probablemente
sí funcionó, solo que muy rápido para notarlo) -- con 5 segundos quedó
clarísimo. **No hace falta alargar `iot_lavado.DURACION_PULSO_S` (0.5s) por
esto**: un relé mecánico conmuta en milisegundos, 500ms alcanza de sobra
para la función real (activar un pulsador), el problema era solo de
percepción humana en la prueba de diagnóstico, no del pulso en producción.

**Bug real encontrado al probar esto, ya arreglado: la conexión Modbus no se
recuperaba sola tras un corte.** Justo el cambio de mapeo de arriba reinició
el servicio Modbus TCP del M300, y `iot_lavado.py` (conexión persistente,
un solo `ModbusTcpClient` para todo el proceso) se quedó con un socket
muerto: `client.connected` seguía en `True` del lado de Python aunque el
M300 ya había cerrado la conexión del otro lado, así que CADA pulso de
actuador fallaba con `[WinError 10054] Se ha forzado la interrupción de una
conexión existente por el host remoto` -- y las lecturas de DI habrían
tenido el mismo problema silencioso (nunca se notó porque nadie reinició el
M300 antes). Se agregó `client.close()` en el `except` de `_leer_estado`,
`_escribir_reles` y `ejecutar_comandos_pendientes`: fuerza que el PRÓXIMO
intento abra una conexión nueva en vez de seguir reusando un socket que ya
no sirve para siempre. Antes de este arreglo, cualquier corte de red o
reinicio del M300 dejaba lectura/escritura muertas hasta reiniciar
`iot_lavado.py` a mano.

## Barra de progreso del lavado (29/08/2026)

`lavado_programa.estado()` calcula `progreso_pct` (0-100, % del ciclo
COMPLETO ya transcurrido, sumando las duraciones de las etapas ya hechas
más lo corrido de la etapa actual) y lo devuelve dentro de
`/api/iot/pantalla/lavado` → `programa.progreso_pct`. El cálculo se hace
ACÁ, en el servidor, y no se manda ningún timestamp para que la pantalla lo
reste sola -- así no importa si el reloj del ESP32 está desincronizado del
de esta PC. En la pantalla (`main.c`), la barra (`barra_lavado_progreso`,
un `lv_bar`) y su label de porcentaje están ocultos por defecto y solo se
muestran mientras `programa.activo` es `true`; se actualizan en
`actualizar_estado_programa()` cada vez que llega una respuesta del
polling (mismo intervalo que el resto de la pantalla de Lavado
Automático).

**Si en el futuro hace falta agregar otro punto** (por ejemplo activar
`DO01`/`DO02` o una entrada nueva si se agrega expansión de DI): repetir el
mismo camino -- Data Point (para que el M300 lo reconozca como canal) ya
debería estar hecho si aparece en "IO Module → Status"; lo que hay que
agregar es la fila en **Protocol → Modbus TCP → Node mapping table**, y
recién ahí un cliente externo (nuestro `iot_lavado.py`) lo puede leer/escribir.

**`iot_conexion.py`: IP/puerto del M300 ahora configurables** (⚙
Configuración › 🔌 Entradas/Salidas, arriba de la tabla de nombres) --
antes `HOST`/`PORT` eran constantes fijas en `iot_lavado.py`. Se lee UNA
SOLA VEZ al arrancar ese proceso (no en caliente, a diferencia de la URL
del servidor en el ESP32): un gateway de este tipo normalmente tiene IP
fija en la red del tambo, así que no hace falta la complejidad de recargar
en caliente -- cambiar el valor requiere reiniciar `iot_lavado.py`.

## Solapa 🔌 Entradas/Salidas: mismo patrón que 🤖 IA por WhatsApp

Tabla editable de 16 filas FIJAS (no se agregan/sacan, a diferencia del
Check-list o de IA por WhatsApp) — se edita `{clave: nombre}` directo en vez
de un array de objetos. Mismo mecanismo de "Guardar deshabilitado hasta que
cambia algo" (`JSON.stringify(actual) !== original`) y mismo endpoint
admin-gateado (`@auth.requiere_rol("admin")`) que el resto de
Configuración. `cargarCanalesGateway()` se agregó a la lista de loaders de
`cargarConfiguracion()`, junto a `cargarWhatsappIa()` y el resto.

## Comandos de voz "Jarvis" desde la pantalla ESP32 (29/08/2026)

Pedido del tambo: manejar el Lavado Automático y los actuadores con las
manos ocupadas o mojadas, diciendo "Jarvis" en vez de tocar la pantalla.
Diseño completo en
`docs/superpowers/specs/2026-08-29-comandos-voz-jarvis-design.md`.

**El pipeline, y por qué está partido donde está**: el ESP32 escucha la
wake word "Jarvis" LOCALMENTE (WakeNet9 de ESP-SR, modelo "Jarvis" gratuito
e incluido, corre sin red), graba ~4s y los manda a
`POST /api/iot/pantalla/voz` (LAN-only, mismo guard de `CF-Connecting-IP`
que `/actuador` y `/lavado/iniciar`). El comando en sí NO se reconoce en el
micro: **MultiNet, el reconocedor de comandos de Espressif, solo soporta
inglés y chino**. Del lado de la PC: `voz_stt.transcribir` (Vosk, local) →
`voz_comandos.interpretar` (vocabulario cerrado) → se ENCOLA el pedido en
SQLite → `voz_sintesis.sintetizar_wav` arma la confirmación hablada y el
WAV vuelve como body de la respuesta, para que la pantalla lo reproduzca.
Igual que Actuadores y Lavado Automático, **Flask nunca escribe Modbus**:
el único dueño de esa conexión sigue siendo `iot_lavado.py`.

**Vosk y no Whisper, y el motivo importa para producción**: `faster-whisper`
era la primera opción y **no se puede ni importar en esta PC** — Windows
Smart App Control bloquea el binario nativo de PyAV, del que depende, y SAC
no tiene excepción por archivo (solo se desactiva por completo, y eso es
irreversible sin reinstalar Windows). Vosk no tiene esa dependencia. Como
la PC de producción es OTRA máquina, **hay que confirmar `import vosk` ahí
antes de dar la función por terminada** (ver Despliegue, abajo). Por eso
mismo `import vosk` vive DENTRO de `voz_stt._cargar_modelo()` y no arriba
del archivo: `app.py` importa `voz_stt` al arrancar, así que un vosk
ausente o bloqueado tiraría el `ImportError` al importar `app` y dejaría a
**LactIA entera** sin levantar (`servidor.py` importa `app`) por una
función opcional. Adentro de la función, el mismo error viaja como
excepción de `transcribir()`, que el endpoint ya maneja bien ("No entendí,
repetí", sin tocar ningún relé). Verificado con vosk bloqueado a propósito:
`app` importa sus 114 rutas igual.

**EL VERBO DECIDE LA INTENCIÓN. Comparar la frase entera era peligroso.**
La primera versión de `voz_comandos.interpretar` usaba
`difflib.get_close_matches` contra frases fijas completas. Como "lavado" es
la palabra dominante y está en las dos familias de frases, el verbo —lo
único que distingue arrancar de parar— quedaba diluido entre los caracteres
compartidos. Medido con las constantes que tenía ese código:

    'parar el lavado'   -> lavado_iniciar  ('iniciar el lavado', 0,750)
    'parar lavado'      -> lavado_iniciar  ('arrancar lavado',   0,815)
    'para el lavado'    -> lavado_iniciar  ('iniciar el lavado', 0,774)
    'pare el lavado'    -> lavado_iniciar  ('iniciar el lavado', 0,774)
    'frenar el lavado'  -> lavado_iniciar  ('iniciar el lavado', 0,788)
    'cortar el lavado'  -> lavado_iniciar  ('iniciar el lavado', 0,788)
    'apagar el lavado'  -> lavado_iniciar  ('iniciar el lavado', 0,727)

O sea: **pedir que PARE arrancaba las bombas y contestaba "Lavado
iniciado"**. Y no es un caso de laboratorio — Vosk transcribe "parar el
lavado" como "para el lavado", una de esas filas. Ahora se parsea el primer
token contra listas explícitas de verbos (`VERBOS_INICIAR`,
`VERBOS_CANCELAR`, `VERBOS_PRENDER`, `VERBOS_APAGAR`, comparación EXACTA
sobre el texto normalizado sin tildes) y la comparación difusa se usa SOLO
para el resto de la frase, que es donde de verdad hace falta tolerar
errores del transcriptor. Las reglas, todas sesgadas al lado seguro:

- Verbo de arrancar **+ el lavado nombrado explícitamente**: cualquier otra
  cosa ("iniciar" solo, "arrancar eso") es "No entendí, repetí". Prender
  bombas es la dirección peligrosa.
- Verbo de parar solo, o seguido de palabras del lavado, o de algo que no
  se reconoce → cancelar. Frenar nunca se traba (mismo criterio que ya
  regía el botón de cancelar de la pantalla).
- `apagar` es el único AMBIGUO ("apagar el lavado" es cancelar el ciclo,
  "apagar bomba de agua" es ese relé): se resuelve por lo que sigue, y sin
  evidencia positiva de una de las dos cosas no hace nada.
- Lo del lavado se chequea SIEMPRE ANTES que los nombres de actuador. Hace
  falta de verdad: hoy `do_1` se llama **"Bomba de Lavado"**, y sin ese
  orden "parar el lavado" apagaría ese relé en vez de cancelar el ciclo.

**Los tres umbrales salen de una medición, no de un número lindo**
(batería en el scratchpad, `medir_umbrales_voz.py` + `test_voz_comandos.py`,
que los re-chequea en cada corrida):

    UMBRAL_CONFIANZA  0,72   nombre de actuador: lo que TIENE que reconocerse
                             da >= 0,960 (con errores de STT incluidos), lo
                             que NO tiene que reconocerse da <= 0,400
    MARGEN_AMBIGUEDAD 0,15   ventaja mínima sobre el segundo actuador: los
                             comandos que deben resolver sacan >= 0,214, los
                             genuinamente ambiguos <= 0,096
    UMBRAL_LAVADO     0,70   por PALABRA contra "lavado": variantes plausibles
                             >= 0,727, palabras de comandos de actuador <= 0,625

`UMBRAL_CONFIANZA` conserva el 0,72 que ya estaba, pero ahora cae en el
medio de una banda vacía medida en vez de ser un número asumido.

**Nombres parecidos: se rechaza en vez de elegir.** Con "Bomba de Agua
Fría" y "Bomba de Agua Caliente" configuradas, "prender bomba de agua"
daba 0,839 contra 0,743 y elegía "fría" **en silencio**. Ahora, si el
segundo candidato queda dentro del margen, la respuesta es "No entendí,
repetí". Excepción necesaria: **el nombre dicho EXACTO gana sin mirar el
margen** — "salida 5" a "salida 8" (nombres reales de hoy) se parecen 0,875
entre sí, y sin esa salida el margen dejaría inservible justamente al
comando bien dicho. Además `iot_canales.guardar` **rechaza nombres
repetidos** (comparando sin tildes ni mayúsculas): con dos salidas llamadas
igual no hay forma de saber cuál se pidió.

**Una salida del M300 ya NO es solo un pulso momentáneo.** Los comandos de
voz la dejan SOSTENIDA (tabla `voz_actuadores_estado`) hasta que alguien
pida apagarla — el botón de la pantalla sigue siendo el pulso de 0,5s de
siempre, son dos cosas distintas sobre el mismo relé. Por eso
`iot_monitoreo.panel_io()` ahora informa `sostenido_desde` por salida
además de `ultima_activacion`: un relé que puede quedar prendido para
siempre necesita un indicador en algún lado. **El tope máximo de tiempo
encendida NO se inventó**: es una pregunta para el tambo, misma regla que
las duraciones de etapa y los umbrales de retirada.

**Un apagado por voz SIEMPRE escribe, aunque no haya transición.**
`procesar_comandos_voz` es de flanco (solo escribe cuando cambia su propio
estado, para que el régimen normal no machaque Modbus cada 3s). Eso hacía
que un "apagar X" sobre un relé que la capa de voz no había prendido —lo
prendió la web del propio M300, o quedó de un apagado de arranque que
falló— no escribiera NADA mientras la pantalla ya decía que lo había
apagado. Ahora `solicitar_apagado` anota el pedido en
`voz_apagados_pendientes` y el ejecutor lo escribe una vez y lo consume
(si la escritura no se confirma, no lo consume: reintenta la vuelta
siguiente).

**Confirmaciones en PRESENTE, no en pasado.** "Arrancando el lavado",
"Prendiendo X" — el endpoint solo ENCOLA; quien mueve el relé es
`iot_lavado.py` en su ciclo de 3s. Con ese proceso parado, un "Lavado
iniciado" sería mentira lisa y llana en cada comando.

**Un comando de voz nunca pisa la etapa ACTUAL de un lavado en curso, y el
chequeo tiene que estar en el EJECUTOR.** El de Flask
(`voz_comandos.solicitar_*`) mira el estado de un instante anterior: un
"apagar bomba" aceptado durante la etapa 1 llegaba al ejecutor justo cuando
la etapa 2 acababa de prender ese mismo relé, y lo apagaba — la etapa
seguía corriendo con la bomba muerta informando avance normal.
`procesar_ciclo_lavado` ahora DEVUELVE los relés que tiene tomados y
`procesar_comandos_voz` los descarta (y lo registra en el log) en vez de
volver a consultar la base con datos de otro momento.

**Reiniciar `iot_lavado.py` en medio de un lavado CORTA el ciclo.** Es
deliberado y es un cambio respecto de antes de esta rama: el apagado de
seguridad del arranque de-energiza las 8 salidas, incluidas las de la etapa
en curso, y el motor solo escribe en los CAMBIOS de etapa — dejar el ciclo
"activo" haría que el resto de esa etapa corriera con las bombas apagadas
informando avance normal. `limpiar_ciclo_lavado_al_arrancar` lo da por
terminado, lo grita en el log y hay que volver a arrancarlo a mano.
También descarta un `comando` que hubiera quedado encolado mientras el
proceso estaba caído: un "iniciar" pedido vaya a saber cuándo, sin nadie al
lado del equipo, no puede arrancar bombas porque el proceso volvió.

**`_escribir_reles` MIRA LA RESPUESTA del M300, no solo si hubo excepción.**
Es el mismo error que ya está documentado más arriba en este archivo:
`ExceptionResponse(exception_code=2, "Illegal Data Address")` —lo que
devolvían TODAS las salidas antes de cargar la tabla de mapeo de nodos—
**deja el socket sano**. Con `client.connected` como única señal de éxito,
el apagado de seguridad del arranque devolvía "listo" en la primera vuelta
con los ocho relés sin tocar; y como `client.close()` en el `except` hace
que los relés siguientes reconecten, ese chequeo además solo reflejaba el
resultado del ÚLTIMO relé (podía fallar `do_1` y dar éxito igual). Ahora
`_escribir_reles` devuelve el SET de claves que no se pudieron confirmar,
el apagado de arranque reintenta SOLO esas, y si algo queda sin confirmar
lo avisa con una banda de exclamaciones en el log. Mismo criterio en los
pulsos manuales (`ejecutar_comandos_pendientes`): un pulso rechazado por el
M300 ya no queda guardado como `"ok"`, y si lo que falla es el apagado del
FINAL del pulso —el relé puede haber quedado energizado— es alarma.

**Si falla la síntesis de voz, el aviso va al log del servidor.**
`voz_sintesis.sintetizar_wav` corre PowerShell con `check=True`: si falla,
falla DESPUÉS de haber encolado el comando. Antes eso moría en un 500 con
un stack trace de `subprocess` y ninguna pista de lo peligroso del caso (un
relé pedido y el operario sin escuchar nada, creyendo que no pasó nada).
Ahora se loguea diciendo exactamente eso y se contesta 503.

**Probado con clientes Modbus FALSOS y con el `test_client` de Flask** (los
scripts quedan en el scratchpad: `test_voz_comandos.py`,
`test_iot_lavado_voz.py`, `test_voz_endpoint_fix2.py`,
`test_voz_stt_sin_vosk.py`, `medir_umbrales_voz.py`). Nunca se arrancó
`iot_lavado.py` ni se le habló al M300 real. La prueba del endpoint
sintetiza el audio en el momento y lo transcribe con Vosk de verdad: no hay
mocks en el camino audio → texto → intención → fila encolada.

### Despliegue de esta función (hacer en este orden)

1. `pip install -r requirements.txt` en la máquina de producción (`vosk`
   es dependencia nueva; sin eso, la voz no anda — pero el resto de LactIA
   sí, ver arriba).
2. **Confirmar `import vosk` ahí**, en una consola, antes de nada: es OTRA
   máquina que la de desarrollo y este es el punto que puede fallar por
   política de Windows (Smart App Control).
3. **Precalentar el modelo desde una consola** (`python -c "import voz_stt;
   voz_stt._cargar_modelo()"`, parado en la carpeta del proyecto): la
   primera vez se BAJAN ~38 MB, y si eso pasa dentro del primer comando de
   voz, pasa dentro del hilo del pedido HTTP y sin timeout.
4. Reiniciar `iot_lavado.py` (los cambios de este archivo no aplican solos)
   **arrancándolo desde la carpeta del proyecto**: la ruta de
   `iot_sensores.db` es relativa.
5. Después de ese primer reinicio, **confirmar FÍSICAMENTE que las ocho
   salidas quedaron des-energizadas** en vez de confiar en el log. Es
   exactamente el motivo por el que el apagado de arranque tuvo que
   aprender a detectar su propia falla.

## Rediseño del score de rutina: pesos nuevos + "Evaluación de Incidentes" separada (24-30/08/2026)

El tambo mandó su propia planilla de criterios (Rotativa y Convencional, cada
una con "Evaluación de Manejo" y una "Evaluación de Incidentes" nueva) para
reemplazar los pesos que había hasta ahora. Antes de tocar código se
verificaron dos cosas que la planilla daba por sentadas, y las dos importan:

**"Retiradas Forzadas" es EXCLUSIVA de la rotativa, no un dato sospechoso de
la convencional.** El tambo lo explicó: pasa cuando la plataforma llega a la
zona de sellado y la vaca sigue ordeñándose (velocidad de la rotativa muy
alta, u ordeño lento/mala rutina de esa vaca) — mecánicamente imposible en
una sala de tandas, que no tiene una plataforma que la lleve a un punto fijo
a horario. Esto resuelve algo que ya estaba anotado sin explicación más
arriba en este archivo: `ForcedRetract` daba 0 en las 32.051 filas de La
Martina porque el fenómeno no aplica ahí, no por un problema de datos.

**Los otros cuatro incidentes (Recolocaciones/Deslizamientos/Bloqueos/
Patadas) SÍ existen para la convencional**, con datos reales — se dudaba
porque `salas/convencional.py` solo leía `ForcedRetract` de
`SessionMilkYieldEx` (la sala nunca los había necesitado hasta ahora).
Medido en La Martina (14 días): 6,6%/34,2%/17,3%/4,8% de los ordeños con
recolocación/deslizamiento/bloqueo/patada respectivamente — variado y real,
no ceros. Se agregaron a `salas/convencional.sql_rutina` (columnas
`ex.NoOfReattaches/Slips/Blocks/KickOffs`) y a `rutina.sql_rutina` (ya
existían en `CMSMilkYield` para la rotativa, ver la sección de incidencias
más arriba en este archivo — solo faltaba leerlos en ESTA consulta puntual,
la de "Rutina de ordeño", no la de "Rendimiento Sala").

**"Evaluación de Incidentes" es un score 0-100 APARTE del de manejo**
(`rutina.componente_incidentes`, en el mismo diccionario de salida de
`_analizar_sesion` que ya tenía `detalle`/`score`/`hallazgos`, bajo la clave
`"incidentes"`): mide lo que registra la MÁQUINA en cada ordeño, no la
logística de traer los animales al corral — puede ser falla de rutina, pero
también de mantenimiento o del equipo en sí, y hay que poder mirarlo aparte
para saber cuál de las tres es. Cada tipo se puntúa
`100 × (1 - ocurrencias/vacas)`, lineal a propósito (a diferencia de
"identificación", que sí tiene curva no lineal confirmada — acá todavía no
hay motivo medido para inventar una). Pesos confirmados por el tambo:

    Rotativa: Recolocaciones 20 · Deslizamientos 20 · Bloqueos 10 ·
              Retiradas forzadas 45 · Patadas 5      (rutina.PESOS_INCIDENTES)
    Convencional: Recolocaciones 30 · Deslizamientos 40 · Bloqueos 20 ·
              Patadas 10, SIN retiradas forzadas     (salas.convencional.PESOS_INCIDENTES)

En el frontend (`pintarRutinaSesion`) se agregó como una segunda tarjeta
dentro del mismo panel de la sesión, debajo de Hallazgos — mismas barras de
componente que "Evaluación de Manejo", separada por un borde.

**Pesos de "Evaluación de Manejo" reemplazados** (antes: `rutina.PESOS` /
`salas.convencional.PESOS`), confirmados por el tambo:

    Rotativa: Colocación 30 · Identificación 30 · Lerdas 5 · Entre grupos 10 ·
              Mezcladas 5 · Ocupación 10 (+ Paradas de la rotativa 10, ver abajo)
    Convencional: Colocación 30 · Identificación 30 · Lerdas 5 ·
              Entre grupos 15 · Entre mangadas 15 · Mezcladas 5

Dos componentes que YA EXISTÍAN quedan en peso 0 en el rediseño, no
eliminados del código: "manejo_corral" en la rotativa (el tambo lo fusionó
conceptualmente en "Entre grupos" — tiene sentido, `_huecos_rotativa` ya
usaba un solo umbral de sesión para las dos cosas, a diferencia de la
convencional donde sí están bien separadas) y "ocupación"/"flujo" en la
convencional. Como el editor de pesos del frontend lee los componentes del
propio análisis (`componentesDelScore`), un tambo que quiera reactivarlos
puede hacerlo desde ⚙ (por tambo), sin tocar código.

**"Paradas de la rotativa" (10% del diseño del tambo): RESUELTO, con un
proxy, no con el dato literal (30/08/2026).** Se investigó a fondo si DDM
tiene un conteo real de la plataforma parándose: existe una tabla
`Chi_TempRotaryStops24` (motor "Chi" = analítica propia de DeLaval dentro de
DelPro) con `parlor/HH/Animals/Spintime/PercGaps/Stops/StopsLt5/Stops5to10/
StopsGt10` por hora, pero tiene SOLO 18 filas de un único día (15-16/07/2026)
y nunca se actualizó — el nombre "Temp" no es casualidad: por el patrón (otro
grupo de tablas `Chi*` con `create_date` del 30/07, mismo comportamiento)
parece ser una tabla de trabajo que algún reporte de DelPro genera al vuelo
cuando se lo abre, no un historial acumulado. Se buscó también por columnas
`%Stop%`/`%Downtime%`/`%Halt%`/`%Idle%` en todo el esquema y por
`CMSRotaryController` (solo config de red del controlador) — nada.

**La salida fue mirar el reporte nativo "Rendimiento de ordeño" de DelPro**
(el mismo que ya se replica 162/162 campos en `rutina.sql_rendimiento`, ver
más abajo): tiene una columna **"Controles manuales"**, que resultó ser
`CMSMilkYield.ManualMode` — YA usada en el código
(`ordeno.sql_incidentes_diarios`, con el comentario *"el operario enganchó
la pezonera a mano en vez de que lo haga la rotativa sola"*). No es un
conteo de la plataforma parándose (una vaca puede necesitar enganche manual
mientras el resto de la plataforma sigue girando sola), pero es la
intervención manual más cercana que DDM sí registra, y el tambo la aceptó
como proxy.

`rutina.sql_rutina` ahora trae `CASE WHEN y.ManualMode = 1 THEN 1 ELSE 0
END AS control_manual` (solo la rotativa la pide: la convencional no tiene
brazo automático que reemplazar a mano). `_analizar_sesion` calcula el
componente `paradas_rotativa` con el mismo criterio lineal que "Evaluación
de Incidentes" (`100 × (1 − controles_manuales/vacas)`), y `rutina.PESOS` ya
tiene `"paradas_rotativa": 10` — con esto los pesos de Manejo de la rotativa
vuelven a sumar 100 exacto.

**Bug encontrado y arreglado en el camino: `normalizar_pesos` colaba claves
rotativa-only a la convencional.** La función siempre completaba los
huecos de un `pesos` parcial contra `rutina.PESOS` (el universo de la
rotativa) sin importar para qué sala se estaba llamando — inofensivo
mientras las dos salas tuvieran EXACTAMENTE las mismas 8 claves, pero con
"paradas_rotativa" como novena clave solo de rotativa, la convencional
terminaba heredando peso 10 para un componente que en esa sala vale
siempre 100 (nunca pide `control_manual`), inflando el score sin que nadie
lo hubiera pedido. Se agregó un parámetro `defecto` a `normalizar_pesos`
(el universo de claves válidas y sus valores por defecto) y
`salas.convencional.analizar_dia`/`resumen_dia` ahora pasan
`pesos_defecto=PESOS` (el de ESA sala) explícitamente en vez de dejar que
`rutina.py` asuma el suyo. Vale la pena recordar esto si algún día se
agrega una clave nueva que no sea universal a las tres salas.

**Orden de los componentes en pantalla, y por qué algunos no se muestran.**
`_analizar_sesion` arma `detalle` en el orden pedido por el tambo:
`prep_90s, identificacion, lerdas, manejo_corral, entre_grupos,
mezcla_rodeos, ocupacion, paradas_rotativa, flujo` — ese orden, filtrado por
peso > 0 en el frontend (`pintarRutinaSesion`), da EXACTO la lista y el
orden de las dos planillas del tambo (rotativa sin manejo_corral/flujo,
convencional sin ocupacion/flujo/paradas_rotativa, con manejo_corral ANTES
que entre_grupos — por eso ese orden y no el alfabético). El editor de
pesos (⚙ Configurar análisis) sigue leyendo la lista COMPLETA sin filtrar,
así que un tambo puede reactivar cualquiera de estos sin tocar código.

## Un puntaje por debajo de 100 nunca se muestra como 100 (06/10/2026)

Reportado con San José (sala convencional): identificación, manejo de corral,
entre grupos y mezcla de rodeos en 100%, y el tambo sabe que hay vacas sin
identificar. Investigado contra la copia local de `SanJose` antes de tocar nada:

**La identificación de San José es de verdad ~99,7-99,8%, no 100.** El comodín
(`BasicAnimal.Number = 0`) tiene 153 ordeños en 30 días sobre 38.026 (0,4%; de 0
a 7 por día sobre ~1.280). Se descartó que haya otra forma de guardar los sin
identificar: `SessionMilkYield.BasicAnimal` nunca es NULL ni huérfano,
`SessionMilkYieldEx.ManualID` es 0 en las 217.201 filas (la carga manual no se
usa, o no queda registrada), los 79 animales con `Number = -1` son registros de
"Nacida muerta" sin un solo ordeño, y `UnknownTransponderId` apunta a una vaca
ficticia que no está en `BasicAnimal`. O sea que ~3 ordeños por día SÍ quedan a
nombre del comodín, y se perdían de vista por el redondeo: la curva de
identificación (100→100, 90→85) los lleva a 99,6 y `round()` mostraba "100%".

**`rutina._redondear`**: igual que `round()` salvo que un valor menor que 100
nunca da 100 (99,6 → 99). Se usa en los componentes del detalle, los
incidentes y los puntajes de sesión y del día. Un 100 exacto sigue siendo 100.
Verificado en tres tambos (La Ponderosa, La Martina, San José): de 321 valores
cambiaron 22, todos de 100 a 99 en componentes que no estaban exactamente en
100, y ningún puntaje de sesión se movió (La Ponderosa queda igual).

**LO QUE NO SE ARREGLÓ ESE DÍA, y se resolvió el 07/10 (ver la sección de abajo):
"entre grupos" era casi imposible de bajar de 100 en San José.** El componente solo penaliza el
exceso de los huecos que superan 3 veces la mediana de la PROPIA sesión, y en
San José el cambio de rodeo tarda una mediana de 8 a 10 minutos (492-579 s,
medido en 3 sesiones): hay que pasar de 30 minutos para que reste algo. Es
calificar a la sala contra sí misma, lo mismo que se evitó con el objetivo de
entrada→leche. "Vacas lerdas" tiene la misma forma (1,5× la mediana de la
sesión: da 91-99 aunque toda la sesión sea lenta). La salida es la misma que
`umbral_prep_s`: que el tambo cargue en ⚙ Configuración cuánto es un tiempo
aceptable entre rodeos (y entre mangadas), sin inventar un número en el código.

Cuando se mira una base local de una sala convencional: ni `delpro_lectura` ni
la copia de San José estaban disponibles (4060 y `RECOVERY_PENDING`); se analizó
con una conexión propia de solo lectura con autenticación de Windows, sin tocar
la configuración del tambo.

## La base de datos de cada tambo se elige en ⚙ Configuración (03/11/2026)

Antes la base (`DDM`, `DDM_LAMARTINA`, `SanJose`...) solo se podía fijar en
`tambos.py`. Ahora la sección "Conexión a la base" tiene un campo **Base de
datos** (`base_datos` en `configuracion_tambo.py`) que pisa la de `tambos.py`
(`tambos._config_manual`); vacío = la declarada ahí. El tambo SIGUE teniendo que
existir en `tambos.py` (id, rebaños): esto cambia a qué base apunta, no crea
tambos nuevos.

**"Ver bases"** (`GET /api/configuracion/bases`, `db.listar_bases`) conecta a
`master` con la conexión GUARDADA y lista las bases con `HAS_DBACCESS = 1`: las
que ESE usuario puede abrir. Es a propósito un filtro de acceso y no una lista
de todo el servidor: un `delpro_lectura` huérfano en una base recién restaurada
(el error 4060 de siempre) simplemente NO aparece, y eso sirve de diagnóstico
(en esta PC ofrece `SanJose` y no `DDM_LAMARTINA`). Si se cambió IP, usuario o
contraseña hay que guardar antes de pedir la lista.

Tres decisiones, todas medidas:
- **El nombre se valida** (`[A-Za-z0-9_.-]`, hasta 128): va dentro de la cadena de
  conexión (`DATABASE=...;`) y un `;` o `=` agregaría atributos de conexión.
  Probado con `DDM;Encrypt=no`, `a b`, `x=y` y 200 caracteres: todos rechazados (400).
- **Al guardar con una base distinta se descarta lo calculado de ESE tambo**
  (`_olvidar_datos_de`: caché, "esta base no tiene tal tabla" y `_tiene_bcs`). El
  caché dura 30-60 min y, sin esto, seguiría mostrando la base anterior. Solo
  toca las claves del tambo editado (verificado con un segundo tambo).
- **`listar_bases` NO pasa por el semáforo de consultas pesadas** (`_slot_for`):
  es metadatos instantáneo y, haciéndolo esperar turno, la pantalla de
  Configuración se colgaba detrás del calentamiento del tablero. Aun así, con
  SQL Express saturado tardó de 30 a 40 s en loguear (medido en esta PC), así que
  la pantalla avisa que puede demorar.

## Tiempos muertos de la sala convencional: SALA PARADA (07/10/2026)

El tambo mostró el gráfico de una sesión de San José con minutos sin ordeñar al
inicio y entre rodeos, y "Sin tiempos muertos entre grupos" en 100%. Se rehízo
la medición de `entre_grupos` y `manejo_corral` en `salas/convencional.py`
(`_huecos_por_rodeo`); la rotativa NO cambia (verificado: 0 de 6 sesiones de
La Ponderosa con diferencias).

**Definición, acordada con el tambo: tiempo muerto = ningún animal ordeñándose,
en los dos lados a la vez**, entre el primer y el último ordeño de la sesión.
La vaca ordeña de su arranque de leche a su retiro (`hora_coloc` → `hora_fin`).
Se probó primero "de la identificación al retiro" y NO sirve: la sala casi
nunca queda vacía así (2-4 minutos), porque la vaca se identifica al entrar y
espera.

**Por qué la medición anterior daba 100.** Medía el tramo entre la
identificación de la última vaca de un rodeo y la de la primera del siguiente
—que incluye el ordeño en curso de la otra mangada—, y solo penalizaba lo que
pasara 3 veces la MEDIANA DE LA PROPIA SESIÓN. En San José esa mediana es de 8 a
10 minutos: hacían falta ~30 de espera para perder un punto. Calificar a la sala
contra sí misma, otra vez.

**Medido en San José, 42 sesiones de 14 días** (sala parada, en minutos):

    entre rodeos (64 huecos)      mediana 2,4   p75 3,4   máx 11,9
    dentro de un rodeo (90)       mediana 1,0   p75 1,7   máx 15,2
    % de la sesión parada         mediana 3,2%  p75 5,2%  máx 11,1%
    arranque hasta 8 vacas a la vez   mediana 1,0   máx 1,8

**Cómo puntúa ahora.** Tolerancias ABSOLUTAS por tambo (⚙ Configuración › Sala,
`tol_cambio_rodeo_s` / `tol_mangada_s` / `tol_arranque_s`; por defecto
**180 / 120 / 180 s**, los p75 de arriba). Lo que pasa de la tolerancia de cada
hueco es EXCESO; el puntaje es `100 × (1 − exceso_total / 15 min)`, o sea que
llega a 0 con 15 minutos de exceso acumulado en la sesión. El arranque lento
(minutos hasta tener 8 vacas ordeñándose juntas) suma al exceso de "entre
grupos": el peso no cambió (15% y 15%). Con un solo rodeo y arranque normal,
"entre grupos" es "sin dato" (se excluye), no 100. El texto de cada hallazgo
dice hora, duración y entre qué rodeos ("Sala parada 15,2 min, 04:21–04:37, al
cambiar de rodeo (8 → 10)"); el rodeo se nombra por BLOQUE y no por la última
vaca que terminó, porque esa puede ser una suelta ("10 → 10" confundía).

**Resultado en San José (42 sesiones):** "entre grupos" baja de 100 en 14
(mínimo 18) y "manejo de corral" en 17 (mínimo 14); los puntajes de sesión
van de 67 a 84, mediana 79,5. Ninguno en 100. Un 100 sigue siendo posible y
significa que no hubo exceso.

**Los hallazgos de ocupación ya no se listan si el componente pesa 0**
(`rutina._analizar_sesion`): la convencional lo sacó de Manejo y esos "el lado 2
quedó 22,8 min sin ninguna vaca" —medidos contra la mediana de la propia sesión—
repetían y contradecían a los de sala parada.

**OJO con La Martina**: los defaults son de San José. Con ellos "manejo de
corral" da mediana 59 y varias sesiones en 0 (el 09/08 01:58 da 18 de score):
sus mangadas y rodeos son otros. Cada tambo tiene que cargar SUS tres números;
es la misma regla que el objetivo de entrada→leche. Y cambió el score de La
Martina respecto de los valores que figuran más arriba en este archivo.

## Días INCOMPLETOS fuera de los promedios por día (31/08/2026)

Reportado por el tambo: "Horas/día en ordeño" daba un número imposible. Tuvo
DOS causas distintas, arregladas una después de la otra:

**1. La cuenta estaba mal (daba 24,4 h/día).** Sumaba `(arreo + permanencia/2)`
de CADA rodeo. `permanencia_min` es entrada de la PRIMERA vaca del grupo →
salida de la ÚLTIMA: con rezagadas esa ventana se estira y SE PISA con la del
grupo siguiente, así que sumar los 7 rodeos cuenta el mismo tiempo de sala
varias veces (44 h de permanencias sumadas en un día de 24). Y el arreo pasa
FUERA de la sala, en paralelo con el ordeñe de otro grupo. Ahora suma la
duración de las SESIONES del día, que es lo que la tarjeta dice medir y queda
acotado por construcción.

**OJO con las dos tarjetas, que miden cosas distintas y no tienen por qué
coincidir**: "promedio por rodeo" mide cuánto está fuera del corral UNA vaca
(ahí arreo y permanencia/2 SÍ corresponden); "total de la sala" mide cuánto
trabajó la SALA. La columna "Total del día (todos los rodeos)" de la tabla de
detalle es la PRIMERA cuenta, no la segunda — el texto de esa tabla lo aclara
porque ya se había prestado a confusión.

**2. Un día a medias tiraba el promedio abajo.** La copia de DDM corta a mitad
de un día y ese día entraba al promedio con el mismo peso que uno entero.
Medido en producción sobre el rango 01-31/08:

    dias    horas/dia   vacas/dia
    25 (con el parcial)     13,4        1620
    24 (sin el parcial)     13,9        1666

El 25/08 tenía UNA sesión de 68 min contra los 13,1-14,0 h de un día normal.
Es el MISMO error que ya estaba documentado para la tasa de concepción (los
últimos meses censurados inventaban un derrumbe).

**LA REGLA: un día entra sólo si tiene al menos `ordenos_dia` sesiones**
(`app._dias_completos`, y la misma lógica en `index.html` para la tarjeta
gemela — que las dos pantallas den el mismo número es regla del tablero).

`ordenos_dia` lo carga el tambo en ⚙ Configuración → Sala de ordeño. **NO se
deduce de `CMSGroupMilkSetting.NumberOfMilkings`**: hay tambos de 2 y de 3
ordeños y quien sabe la rutina es el tambo — misma regla que ya rige para los
umbrales de retirada, `umbral_prep_s` y las duraciones de etapa del lavado.
Vacío = no se descarta ningún día (comportamiento de antes).

Dos guardas, las dos deliberadas:
  - **Si NINGÚN día llega al mínimo se devuelven TODOS.** Eso significa que el
    número configurado no coincide con lo que trae la base, y dejar la tarjeta
    en blanco esconde el problema en vez de mostrarlo.
  - **El descarte NUNCA es silencioso**: el detalle de la tarjeta dice sobre
    cuántos días promedió y cuántos dejó afuera. Un promedio sobre 24 días no
    puede leerse como si fuera sobre 25.

**Un día de 4 sesiones NO se descarta** (el filtro es "al menos", no "igual
a"). Aparecen porque DelPro, cuando el tambo arrancaba antes, asignaba mal las
sesiones y pasaba ordeños a la sesión anterior; el tambo ya lo corrigió. El
14/08 es uno de esos: 4 sesiones, pero el día cierra en 13,7 h normales.

Los promedios por SESIÓN (ordeños/hora, litros/hora) **no** se filtran: son
tasas, no sumas, y el día parcial no las distorsiona igual.

## "Respuesta inválida del servidor" en SERVER-DELPRO: cómo se diagnostica (31/08/2026)

El tambo reportó ese texto en las dos tarjetas de Rendimiento Sala. **Se
resolvió reiniciando el proceso, y NUNCA se llegó a capturar el traceback**:
sabemos cuándo se arregló, no exactamente por qué fallaba. Lo que sigue es
para que la próxima vez se agarre *mientras* está fallando.

**QUÉ SIGNIFICA ESE TEXTO, exactamente.** Sale de `pedirJSON` (index.html)
cuando la respuesta **no es ok Y el body no es JSON** — o sea la página HTML
de error de Flask (un 500), o una página de error de Cloudflare. No es "no
hay datos" ni "la consulta tardó": esos casos tienen sus propios mensajes.

**Lo que se descartó con medición, para no volver a recorrerlo:**

- Túnel y producción: responden bien (401 JSON en la API, 302 a /login).
- Base de producción: conecta, `delpro_lectura` entra. **NO** era el
  problema del restore que deja el usuario huérfano (error 4060).
- Dependencias: todas importan, y en las MISMAS versiones que desarrollo
  (numpy 2.5.1, cv2 5.0.0, pymodbus 3.14.0, flask 3.1.3). ODBC Driver 18
  presente.
- Los dos endpoints, apuntando a la base de PRODUCCIÓN desde la PC de
  desarrollo, devuelven 200 con JSON válido. O sea: ni el código ni los
  datos producían el error.

**PRODUCCIÓN CORRE PYTHON 3.14.6, y no por decisión de nadie.** El intérprete
es `C:\Users\DelPro\AppData\Local\Python\pythoncore-3.14-64\python.exe`,
instalado por el **Python Manager de la Microsoft Store**. `iniciar.bat`
arranca con `python` a secas y la Store lo resuelve a lo que tenga por
defecto — o sea que **una actualización de Windows puede cambiar la versión
de Python en producción sin que nadie toque nada**, con cuatro dependencias
de binarios nativos de por medio (`pyodbc`, `opencv-python-headless`,
`numpy`, `vosk`). Conviene que `iniciar.bat` use la RUTA COMPLETA del
intérprete con el que se corrió `pip install -r requirements.txt`.

**LOS DOS `servidor.py` DEL ADMINISTRADOR DE TAREAS NO SON DOS INSTANCIAS.**
Se ven así:

    7508    "python"  servidor.py
            -> C:\Program Files\WindowsApps\PythonSoftwareFoundation.PythonManager...\python.exe
    12496   ...\pythoncore-3.14-64\python.exe  servidor.py   <- ESTE tiene el puerto

El primero es el **shim** de WindowsApps que resuelve `python`; el segundo, el
intérprete real que lanza. Mismo segundo de creación, y **sólo el segundo
escucha el 5310**. El lock de instancia única está funcionando: no hay que
matar los dos. Se distinguen con `Get-Process python* | Select-Object Id, Path`
y `Get-NetTCPConnection -LocalPort 5310 -State Listen`.

**HAY DOS COPIAS DEL PROYECTO EN ESA MÁQUINA, y una es basura vieja:**

    C:\delpro-analitica                              <- LA QUE CORRE (git pull acá)
    C:\Users\DelPro\Desktop\delpro-analitica-deploy  <- restos del 25/07, NO corre

**Para saber cuál corre, mirar `.servidor.lock`**: el proceso vivo lo mantiene
abierto en SU carpeta, y en la del Desktop directamente no existe. El nombre
`-deploy` engaña: no es la instalación productiva. (Costó un rato de esta
investigación creer lo contrario.)

**Trampa del diagnóstico: el caché es POR PROCESO.** Un script que pega a los
endpoints en un proceso nuevo arranca con el caché vacío, recibe 202
"calentando" y **nunca pasa de ahí** si sale y vuelve a entrar — el hilo que
llena el caché es daemon y muere con el proceso. Hay que ESPERARLO adentro
del mismo proceso, o pegarle al servidor que ya está corriendo.

**`_refresh_rendimiento_async` se traga la excepción** (`except Exception:
pass`, no cachea el error). Consecuencia que conviene tener presente al
diagnosticar: si la consulta de fondo falla, el endpoint devuelve **202 para
siempre** y la pantalla dice "Calculando…", nunca un error. Así que un 500 en
esa pantalla NO viene de esa consulta: viene del código que corre en el hilo
del request (`analizar_rendimiento`, `_nombres_grupos`, `_grupos_ordene`,
`armar_identificacion`).

**`diagnostico_rendimiento.py`** (raíz del repo) hace todo esto de una: imprime
versión/intérprete/carpeta, versiones de las dependencias nativas, drivers
ODBC, la conexión a la base, y los dos endpoints — primero contra **el
servidor que está corriendo** (entrando con una cookie de sesión firmada con
la propia secret key) y después en su propio proceso con
`PROPAGATE_EXCEPTIONS`, para que salga el traceback completo en vez del HTML
de error. Es de sólo lectura: no escribe en la base, no manda alertas, no
toca configuración. Se corre parado en la carpeta del proyecto y con el
intérprete que tiene el puerto.

## "Rutina de ordeño" juntaba ordeños: DOS causas, no una (31/08/2026)

Reportado por el tambo: la pantalla mostraba sesiones de 12 h con 3.150 vacas
—el doble de un ordeñe real— y con eso el score y todos los tiempos salían
calculados sobre una mezcla de dos ordeños.

**Medido en producción antes de tocar nada** (21/08/2026, 7.155 visitas):

    ANTES    23:56-12:22   12,4 h   3150 vacas   score 84   <- dos ordeños
             15:23-19:44    4,3 h   1639 vacas   score 92
             23:59-04:34    4,6 h   1642 vacas   score 94

    DESPUES  23:56-04:28    4,5 h   1636 vacas   score 94
             07:22-12:22    5,0 h   1514 vacas   score 92
             15:23-19:44    4,3 h   1639 vacas   score 92

**CAUSA 1 — `analizar_dia` tenía una COPIA INLINE del corte por hueco.**
Nunca llamaba a `_separar_sesiones`, así que jamás llegaba a preguntar por
`CMSDeviceVisit.ParlorSession` y se quedó con el criterio que el resto del
código ya había abandonado. Ahora llama a la función compartida, y
`sql_rutina` trae `ParlorSession` (el JOIN a `CMSDeviceVisit` ya estaba: no
costó nada). **Ojo con esto al leer el código: había TRES lugares distintos
armando visitas desde filas, y sólo dos leían `sesion_parlor`.**

**CAUSA 2, y es la que de verdad producía el pegoteo — el filtro por día.**
`analizar_dia` conservaba todo bloque que SE SUPERPUSIERA con el día. Como la
consulta trae ±6h de margen, una sesión que arranca 23:59 (y pertenece al día
siguiente) entraba TAMBIÉN en el día que se estaba mirando: quedaban 4 bloques
donde el tambo hace 3 ordeños, y `_fusionar_hasta(4, 3)` pegaba los dos más
cercanos. Arreglar sólo la causa 1 NO cambiaba nada —verificado, los números
salían idénticos— porque la fusión volvía a unirlos igual.

Ahora cada bloque se asigna a **UN SOLO día** con `_dia_de_bloque` (la fecha de
su visita del medio), que es el criterio que `analizar_rendimiento` ya usaba.
Las dos pantallas cuentan las mismas sesiones, que es regla del proyecto.

**Verificado**: cinco días seguidos de La Ponderosa dan 3 sesiones de 4,3-5,5 h
y ~4.800 vacas por día; el 27/07 (el día con la `ParlorSession` faltante que
ya está documentado más arriba) sigue mostrando UNA sesión de 652 vacas sin
inventar las que faltan. Y con filas sintéticas, para las dos salas: la sesión
que arranca 23:50 se cuenta en el día siguiente, no se duplica entre días
consecutivos y no se pierde ninguna. La convencional —que no tiene
`ParlorSession` y sigue cortando por hueco— da lo mismo.

## Modo televisor: transmitir por Chromecast, 5 sectores fijos (31/08-01/09/2026)

`/tv` (ver más arriba, 25/08/2026) ganó soporte de Chromecast: un botón
"Transmitir a Chromecast" en ⚙ Configuración › Modo televisor, y después el
pedido del tambo de poder transmitir 5 "canales" — un sector fijo de la
pantalla (Ordeño en vivo, Producción del día, Producción por grupo, Calidad
de rutina, Alertas y pendientes) sin rotar, cada uno a una pantalla distinta.

**UNA SOLA aplicación registrada en la consola de Google Cast
(`pantalla_tv.CAST_APP_ID = "CFA16559"`), no cinco.** No es una cuestión de
costo (el registro es una vez por CUENTA, no por app) sino de mantenimiento:
el receiver que registra la consola apunta a una URL FIJA, y esa URL sólo
puede llevar el token vigente en el momento de registrarla. Como el token se
puede rotar desde la misma pantalla de Configuración (y rotarlo dice
explícitamente "la pantalla vieja deja de funcionar"), CADA app registrada
con ese token quedaría muda la próxima vez que alguien lo rote — y con 5
apps, arreglarlo es registrar 5 URLs de nuevo a mano en la consola de Google,
no una.

**Los 5 sectores se transmiten con la MISMA app ya conectada**, mandándole un
mensaje por un namespace de Cast (`urn:x-cast:com.analiticastambo.tv`) al
receiver que ya está corriendo — no relanzando la sesión con otra URL (que
además el SDK de Cast no deja hacer para un Custom Web Receiver: la URL que
carga es la registrada, fija, el sender no se la puede pisar). El botón
"Transmitir <sector>" de la Configuración conecta (o reusa la conexión si la
pestaña ya está transmitiendo) y manda `{vista: <clave-o-null>}`; el receiver
(`tv.html`) escucha ese namespace y pinea el sector con
`window.__pantallaTv.fijarVista(clave)`, sin recargar la página. `null` (el
botón "🔁 Rotación completa") vuelve al comportamiento de siempre.

**`?vista=<clave>` en la URL es la MISMA función, por otra puerta.** Sirve
para abrir un sector fijo en cualquier navegador, con o sin Chromecast —
`/tv` la valida server-side contra `pantalla_tv.VISTAS` (una clave inventada
o vacía se ignora, no rompe la página) y la pasa al template como
`vista_fija`; el canje de token (`?token=...`) la preserva a través del
redirect a la URL limpia.

**Una pestaña del navegador sostiene UNA transmisión.** `requestSession()`
de Cast, si esa pestaña ya tiene una sesión activa, no vuelve a preguntar el
dispositivo — resuelve con la sesión que ya hay. Eso es justo lo que se
quiere para cambiar de sector una pantalla ya transmitiendo (un click, sin
recastear), pero significa que para tener VARIAS transmisiones a la vez (una
pantalla con el ordeño en vivo, otra con las alertas) hay que abrir
Configuración en una pestaña por Chromecast — está explicado así en la
propia tarjeta, no es una limitación oculta.

Probado (sin llamar a Google): el canje de token conserva `?vista=` a través
del redirect; una clave inventada cae a `null` sin romper la página;
`/api/tv/config` sigue trayendo las 5 claves reales de `pantalla_tv.VISTAS`
en orden, no las tildadas para la rotación. Del lado del receiver, extraído
el propio `tv.html`: pinear oculta la barra de progreso y el reloj sigue
andando pero la vista no avanza (500 vueltas del tic sin cambiar), un
mensaje con una clave inventada cae a rotación en vez de romper, y
"Rotación completa" libera el pin. Del lado del sender, extraído el propio
`index.html` con un `cast.framework` mockeado: se arman los 6 botones en
orden (Rotación + los 5 sectores), el mensaje manda la clave exacta (o
`null`, no `""`, para "Rotación"), dos clicks en la misma pestaña reusan la
sesión en vez de reconectar, y cancelar el selector de dispositivos no
muestra un error feo. Se verificó además que el namespace del sender y el
del receiver son el mismo string LITERAL, comparando los dos archivos —
un typo ahí manda el mensaje pero el receiver nunca lo escucha, sin ningún
error visible en ningún lado.

**Registrar cada Chromecast por número de serie ANTES de probar en un
dispositivo real, o publicar la app para no tener que hacerlo** (aclarado
el 01/09/2026, contra la documentación oficial de Google — no se asumió de
memoria: https://developers.google.com/cast/docs/registration).

Mientras la app (`CFA16559`) siga SIN PUBLICAR, sólo puede transmitir a los
Chromecast que se hayan registrado uno por uno como dispositivo de prueba en
`cast.google.com/publish` (serie del equipo, esperar ~15 min, reiniciarlo).
No hay un tope documentado de cuántos se pueden registrar así — la duda de
"Google permite 5" que arrancó este feature no aparece en la documentación
oficial; probablemente se confundió con algún límite viejo o con otro
producto de Cast.

**PUBLICAR la app saca ese requisito por completo**: una vez publicada,
funciona en cualquier Chromecast sin registrar nada. El trámite es liviano
(nombre, descripción, ícono, la URL del sender) y no aparece review de
diseño ni demora de aprobación en la documentación.

**OJO al publicar: NO tildar la opción de listado** ("choose whether to
list your application on Google properties such as chromecast.com/apps").
Publicar y listar son cosas DISTINTAS — publicar solo saca el requisito de
registrar dispositivos; listar además pone la app en un directorio público
donde cualquiera podría encontrarla y transmitirla a su propio televisor.
Como la URL del receiver ya tiene el token pegado (ver más arriba), alguien
así vería los datos reales del tambo hasta que se rotara el token. Publicar
SIN listar deja el `CFA16559` tan privado como está hoy — vive sólo en
`/api/tv/config`, gateado por rol admin — sin la vuelta de registrar cada
equipo. La seguridad real no depende de esto de todos modos: sigue siendo
el token, que no cambia ni con publicar ni con listar.

**El botón "Transmitir" quedaba SIEMPRE oculto en producción: al script del
SDK de Cast le faltaba `?loadCastFramework=1` (04/09/2026).** Reportado por
el tambo con la app ya publicada, HTTPS, Chrome — las tres condiciones que
hacían sospechar de otra causa. Diagnosticado EN VIVO, no adivinado:
`window.cast` en la consola del navegador del tambo dio `undefined`.
Confirmado contra la guía oficial de Google
(developers.google.com/cast/docs/web_sender/integrate): el script se tiene
que cargar con `?loadCastFramework=1` para que exponga `cast.framework.*`
— sin ese parámetro `window.cast` puede quedar directamente sin definir.
Todo este código (`CastContext`, `CastReceiverContext`) ya estaba escrito
contra esa API desde el primer commit del botón; el parámetro faltaba
desde entonces, no es un requisito de una versión nueva del SDK.

Si "Transmitir" vuelve a desaparecer, el primer chequeo es ESTE, en la
consola del navegador de quien lo reporta:

    window.cast   // tiene que devolver un objeto, no "undefined"

Si da `undefined`, revisar que `templates/index.html` siga cargando
`cast_sender.js?loadCastFramework=1` (con el parámetro) antes de sospechar
de HTTPS, del navegador o de la publicación de la app en Google Cast — esas
tres ya se descartaron una vez para este mismo síntoma.

**Y con `window.cast` ya cargado, "Transmitir" se quedaba colgado en
"Buscando Chromecasts…" para siempre, sin error en la Console — Chrome
tenía en caché que la app estaba SIN publicar.** Mismo día, después de
arreglar lo de arriba. Descartado uno por uno, con evidencia, antes de
llegar a esto: el Chromecast SÍ aparecía en el "Transmitir…" nativo de
Chrome (3 dispositivos, red y mDNS bien); el Firewall de Windows permite
mDNS incluso en perfil "Público" (`Get-NetFirewallRule -DisplayName
"mDNS*"` mostró el de entrada habilitado para Public); ni Flask ni
Cloudflare mandan `Content-Security-Policy` ni `Permissions-Policy` (se
verificó con `curl -I` contra el sitio real, no de memoria). La app ya
estaba publicada del lado de Google — el problema era que ESTA sesión de
Chrome había consultado el estado del `CFA16559` ANTES de publicarla, y se
quedó con esa respuesta vieja. **Se resolvió solo con una ventana de
incógnito** (arranca sin ese caché). Si vuelve a pasar tras publicar o
cambiar algo en la consola de Google Cast, probar incógnito ANTES de
sospechar de la app o de la red — ese descarte largo ya se hizo una vez.

## Rutina de ordeño: pestaña "Rodeos por día" (03/11/2026)

Replica la planilla por grupo de DelPro (flujos 0-15/15-30/30-60/60-120, retirada, pico,
% flujo bajo, duración, kg/vaca) pero DÍA POR DÍA y comparable. Las DOS salas desde el 04/11/2026: `sql_flujos_rodeos_dia` en `salas/convencional.py` y, para
la rotativa, `flujos.sql_rodeos_dia` (salas que no la definan contestan `no_disponible`). Comparte permiso con "Flujos de ordeño" (`'flujos'`, solo admin).

- **El rodeo es el del DÍA** (`AnimalDaily.AnimalGroup`), no el actual. `MilkingGroup` viene
  NULL en todas las filas de La Martina. Ver "Los DOS criterios de rodeo".
- **Ventana desde el último día con datos de la base**, no desde hoy (`sql_ultimo_dia_ordeno`).
- **El rango literal de `ad.[Date]` es obligatorio** en el JOIN a AnimalDaily: sin él el plan
  deja de usar el índice por fecha (7 días: 0,1 s con rango, 38 s sin él). `FORCE ORDER` solo
  no alcanza.
- `SessionMilkYield.AnimalDaily` (FK directa) NO coincide con fecha+vaca en 1/3 de los
  ordeños: se usa el JOIN por fecha+animal, que cubre el 100%.
- Un endpoint, una consulta; el frontend arma el total del tambo (pondera por ordeños; suma
  ordeños/vacas/kg), el gráfico y la planilla con día A vs día B (flechas neutras, sin
  juzgar bueno/malo). Día con <60% de la mediana de ordeños = "parcial".
- Los errores del refresco en segundo plano se guardan en `_rodeos_dia_error` (60 s) y salen
  como 502, para no dejar la pantalla en "calentando" para siempre.
- **Tiempos medidos** (La Martina, backup 02/11, caché tibia): 7 días 0,8 s, 30 días 1,6 s, 90 días 5,5 s. En frío la
  primera lectura va a disco y tarda 35-45 s (SQL Express con poca RAM).
- Un rodeo con un solo ordeño ese día (p. ej. "Pre-Parto": 1 vaca, 0 kg) da promedios sin sentido (100% flujo bajo).
- **Rotativa (04/11/2026)**: el rodeo del día sale de `AnimalDaily.AnimalGroup` por el enlace directo de la
  bajada (`SessionMilkYield.AnimalDaily`). Medido en La Ponderosa (7 días, 28.568 ordeños): el enlace cae en la
  fecha del ordeño en el 99,95% y su rodeo coincide con `CMSDeviceVisit.VisitedInGroup` en el 96,7% de las
  visitas que lo tienen (viene NULL en el 17%); `BasicAnimal.[Group]` (hoy) coincide solo en el 88%. Mide
  SEGUNDOS de flujo bajo (`coloc_seg`, columna "Colocación") y no el porcentaje: cada sala trae una de las dos y
  la pestaña ofrece solo las medidas que traen datos (`rodArmar` -> `metricas`). Tiempos en `DDM` local:
  14 días 2,8 s, 45 días 4,6 s.
- **Solo los rodeos de ordeñe de este tambo**: el endpoint descarta las filas cuyo `numero` no esté en
  `sala.sql_grupos()` (en la rotativa entraban "Animals SB" y "VO Don German", y Secas/PreParto con un puñado
  de ordeños; en La Martina "Pre-Parto"). Si esa lista viene vacía no se filtra.

## Análisis de Producción: página propia, tres pestañas (03/11/2026)

Menú lateral "📈 Análisis de Producción" (`produccion` en `auth.PAGINAS_POR_ROL`, solo admin). Pestañas:
**Producción por rodeo**, **Producción por días en leche** y **Flujo por días en leche**. Un período A
(últimos N días completos) contra un B (los N anteriores), N = 7/14/30 elegible; la ventana de datos es de
30 a 120 días y termina en el ÚLTIMO día con datos de la base, no en hoy.

- `produccion.py`: las consultas de producción salen de `AnimalDaily` (una fila por vaca y día) y son las
  mismas en rotativa y convencional. El flujo por DEL es de cada sala (`sql_flujos_del_dia` en
  `salas/convencional.py` y `salas/rotativa.py` -> `flujos.sql_por_deo_dia`). Endpoints:
  `/api/produccion/analisis` (rodeo + DEL, una vez) y `/api/produccion/flujos_del` (aparte: recorre los
  ordeños uno por uno). Helper común de caché con error visible: `_servir_analisis` en `app.py`
  (lo usa también `/api/rutina/rodeos_dia`).
- **Los tramos de DEL son los de "Flujos de ordeño"** (`flujos.DEO_BUCKETS`: 0-30, 31-60, 61-90, 91-120,
  121-150, 151-200, 201-300, 300+) para poder leer una pantalla al lado de la otra.
- **Filtrar por los rodeos de ordeñe de ESTE tambo** (`salas.grupos_subquery(tambo)`, pasado como `grupos_sub`
  a `sql_por_rodeo_dia`/`sql_por_del_dia`): la base DDM la comparten varios tambos y sin ese JOIN aparecían
  "VO Don German", "Animals SB", "Vaq Parir Junio" (en `DDM` local 226 filas de rodeo -> 175 con el filtro). Probado
  de punta a punta en el navegador con La Ponderosa (rotativa): las tres pestañas andan; el flujo muestra
  "Colocación" (segundos) en vez de "% bajo", que es lo que mide esa sala.
- **`BasicAnimal.Number > 0`** siempre: la vaca comodín acumula los kg de todas las ordeñadas sin ID
  (un día "una vaca" tenía 13.409 kg y llevó la banda 0-30 a 153 kg promedio).
- **`IsYieldValid` es la marca de "total del día completo" y los kg por vaca se calculan SOLO con días
  válidos.** Esto corrige lo que figuraba más arriba ("IsYieldValid no sirve"): medido en La Martina
  (15-20/10, días completos) la misma vaca da 41,9 kg en Rodeo 1 con día válido y 27,3 kg sin él; la marca
  se reparte parejo (80-93% por rodeo, 84-93% por tramo de DEL), así que no sesga. Sin esto, cada ordeño
  que falta en la base baja el promedio sin que haya bajado la leche. Las consultas devuelven `vacas` (con
  leche), `vacas_v` y `kg_v` (válidas). La producción total es una ESTIMACIÓN (`vacas` por el kg/vaca de las
  válidas) y la pantalla lo dice.
- **La base de La Martina tiene días con ordeños faltantes** (backup 02/11): 2.148 ordeños el 21/10 y 2.157
  el 23/10 contra ~2.900; 643 el 24/10, 927 el 25/10, 1.612 el 26/10. Un día se marca incompleto si sus vacas
  VÁLIDAS son menos del 60% de la mediana (el último día casi siempre: la marca se cierra de noche, 11/01 tenía
  29 de ~900). Esos días no entran en A ni B. El conteo de vacas con leche NO sirve para detectarlos (el
  21/10 tenía 1.011 vacas y 26 kg/vaca).
- Mínimos: no se dibuja un rodeo/tramo con menos de 5 vacas válidas en un día, ni se muestra el promedio de
  un período con menos de 30 vacas-día u ordeños (una vaca sola "es" el promedio del rodeo).
- **`EndTime` de `SessionMilkYield` trae un relleno en algunos días** (resuelto el 03/11/2026, ver
  "`EndTime` de relleno" abajo): 26/10 526 ordeños, 27/10 1.172 (46% del día), 28/10 777, 29/10 53, 30/10 62,
  01/11 63 (y antes: 21/10 484, 23/10 494, 24/10 877). La duración da de -10.000 a -40.000 s.

- Tiempos medidos (La Martina nov, caché tibia): producción 0,1-0,2 s, flujo por DEL 0,8 s (30 días) y 2,4 s
  (90). La Ponderosa (`DDM` local, fría): 0,3 s / 2,7 s / 1,7 s a 30 días.
- Las bases locales de prueba: `DDM_LAMARTINA` (hasta 28/08) y `DDM_LAMARTINA_NOV` (backup del 02/11, hasta
  01/11), restauradas con `restaurar_lamartina.bat .\DELPRO <nombre>`.

### `EndTime` de relleno en la convencional (03/11/2026)

El relleno es SIEMPRE el mismo instante: **las 21:00:00 locales (= 00:00 UTC)**, no "un fin anterior" en general.
Para los ordeños que arrancan pasadas las 21:00 cae ANTES del comienzo (duración negativa); para los 40 que
arrancaron entre las 20:50 y las 20:59 cae DESPUÉS por 23-204 s (una duración falsa que parece un ordeño corto;
0,2% de ~22.400 filas del 24/10 al 01/11). No hay ninguna duración > 30 min del 24/10 al 01/11: el relleno nunca
da un ordeño largo.

**Qué distorsionaba** (La Martina nov, medido con el código anterior):

- **Duración de sesión**: `fin = max(hora_fin or hora_id)` toma el relleno, que es "verdadero". Sesiones de la
  tarde daban 191 min contra 367 reales (27/10 17:49) y las de la mañana 689 contra 366 (26/10 09:31). Eso arrastra
  los kg/h de Rendimiento Sala (599 contra 1.137 el 26/10).
- **Tiempo de ordeño** (`dur_prom_ordeño`, mediana de "lerdas"): -24.287 s de promedio el 27/10; mediana -26.725 s,
  que dejaba "sin lerdas" en 0% (toda la sesión "lerda") el 27/10 01:29.
- **Barras de rodeo del gráfico**: 135 de 174 con `fin < inicio` el 27/10 01:29.
- **Sala parada** (`entre_grupos`, `manejo_corral`) y **ocupación**: `_tramos_sala_parada` ya descartaba `fin <=
  inicio`, pero una vaca sin fin no ocupa la sala, así que con 15% de fines válidos la sala "estaba vacía" casi
  toda la sesión: **1.651 min de exceso en una sesión de 381 min** (27/10 01:29), 850 el 28/10 01:30.
- Score: con solo la capa 1 (abajo) las sesiones de madrugada subían 5 puntos (lerdas pasaba de 0 a 100 sobre una
  muestra sesgada) y el resto casi no se movía; con las tres capas ver el párrafo siguiente a la lista.

**Regla aplicada (tres capas, solo convencional):**

1. `salas/convencional.py` `_FIN_VALIDO`: `hora_fin` de `sql_rutina` y `sql_rendimiento` sale NULL si `EndTime <=
   BeginTime` (misma regla que `_DUR_SEG`). No se inventa una duración ni se reemplaza por otra hora: aguas arriba
   el código ya sabe qué hacer con un fin NULL (`hora_fin or hora_id`, `if hora_fin`).
2. `FIN_VALIDO_MIN = 0.9`: si menos del 90% de las visitas de la sesión tiene fin válido, `entre_grupos`,
   `manejo_corral` y `ocupacion` quedan SIN DATO (`_huecos_por_rodeo`, `_vacio_entre_mangadas`), con un `info` que
   dice el porcentaje. Medido: sesiones afectadas 15-56%, sanas 100%, rozadas 97-99%.
3. Lo mismo para "vacas lerdas", que vive en `rutina._analizar_sesion`: parámetro `fin_valido_min` (por defecto
   `None` = se puntúa siempre, que es la rotativa), que `convencional._opciones_score` pasa en `FIN_VALIDO_MIN`.
   Con 15% de fines la mediana era de 480 s (sesión sana: ~330) porque sobreviven pocas vacas que no son muestra.

Un componente sin dato sale del score y su peso se reparte (mecanismo de siempre), así que esas sesiones puntúan
sobre colocación + identificación + mezcla (65 de 100 puntos): 27/10 01:29 pasa de 31 a 47, 26/10 17:25 de 29 a 40,
28/10 01:30 de 29 a 45. Suben porque lo que se quitó era basura que puntuaba 0, no porque la sala haya mejorado.

**Invariante verificado** (antes y después sobre las mismas entradas, JSON completo de sesiones + hallazgos + fines
+ Rendimiento Sala): La Ponderosa 06/07 y 30/07 (73·73·69 y 78·78·79), San José 03-05/10 (80·92·95, 89·89·92,
90·84·93) y La Martina 20/10 y 25/10 idénticos. San José tiene 1 sola fila con `EndTime <= BeginTime` en 40 días
(27/09). OJO: la base local de La Ponderosa llega solo al 31/07; el 84·82·78 del 11/08 hay que verificarlo contra
producción. La consulta de la rotativa (`rutina.sql_rendimiento`, `s.EndTime`) NO se tocó: 0 filas con fin <=
comienzo en DDM desde julio.

**Lo que NO se resolvió:** los 40 ordeños con fin 21:00:00 DESPUÉS del comienzo (arriba) siguen contando como
cortos; atraparlos pide fijar las 21:00, atadas a la zona horaria del tambo. Y el 31/10 (día "sano") también cambia
por el margen de ±6 h: 3 y 33 filas con relleno caían en sus sesiones (la de la tarde pasa de 660 a 373 min).
Si el equipo vuelve a rellenar `EndTime`, el síntoma visible es "No se puede medir la sala parada: solo el N% ..."
en el detalle de la sesión.

## La Serenísima "no devuelve datos": el período actual arranca vacío cada mes (04/11/2026)

La tarjeta "Calidad de leche (La Serenísima)" de Entrega de Leche salía en cero (0 entregas, gráficos
vacíos). **No era un fallo de conexión**: con las credenciales de esta PC el login anda y la lectura también.
El 04/11 el período `actual` es noviembre y la página del comprador dice literalmente "TOTAL 0": La Serenísima
publica las entregas con DÍAS de demora (el 23/07 había 52 filas, o sea hasta el ~17/07: ~6 días), así que
los primeros días de cada mes no hay nada. El período `anterior` es el mes completo previo (1/10 al 31/10:
93 entregas, 2.198.584 litros). Los otros valores de `periodo` que se probaron (`mes`, `ultimo`, `2026-11`)
devuelven 0 filas: solo existen `actual` y `anterior`.

Qué se hizo (`laserenisima.py`, `app.py`, tarjeta en `index.html`):
- `laserenisima.obtener_periodos` trae los dos períodos con UN solo login; `app._laser_traer` los guarda en
  el caché `laser:actual` como `actual`, `anterior` y `entregas` (combinada, en orden cronológico).
- `/api/laser/entregas` sin parámetro devuelve el actual y, si está vacío, el anterior con un `aviso`;
  `?periodo=actual|anterior` respeta lo pedido. La tarjeta tiene un selector "Período anterior / actual" y
  muestra el rango de fechas y el aviso.
- El cruce CICLA contra La Serenísima (`/api/entregas/comparar`) y el UFC del Tablero leen la lista combinada:
  sin eso quedaban ciegos los primeros días de cada mes.
- **La alerta de UFC (`_lineas_alertas_puntuales`) NO cambió**: cuenta las entregas del período EN CURSO sobre
  el umbral; sumarle el mes anterior habría cambiado lo que significa el mensaje.

## Menú lateral en árbol, blanco con texto azul (06/11/2026)

El menú pasó de 17 entradas sueltas a un árbol: secciones plegables (Ordeño, Leche, Rodeo, Instalación,
Gestión; Dashboard, Tablero y Consultas quedan arriba sin título) y, debajo de cada página que tiene pestañas,
un submenú con esas pestañas. Tocar un submenú va a la página y a esa pestaña. Fondo blanco y texto azul por
pedido del tambo; los colores son PROPIOS del menú (variables `--side-*` en `.sidebar`) y no siguen el tema
claro/oscuro. Se hizo primero una simulación y el tambo eligió la variante "agrupada" (la otra era una lista
plana con submenús). La agrupación es criterio nuestro, no existía antes.

**Se arma en el navegador, no en la plantilla** (`armarMenuArbol` en `index.html`): reordena los botones
`.nav-item` que la plantilla ya trae filtrados con `paginas_visibles` y lee cada submenú de la barra de pestañas
REAL de la página (`rutina-tabs`, `prod-tabs`, `repro-tabs`, `alim-tabs`, `salud-tabs`, `config-tabs`). Por eso:
- Un usuario sin permiso para una página o pestaña no la ve en el árbol (el operario no ve nada de más).
- Agregar una pestaña a una de esas barras la suma sola al árbol. **Una página nueva con pestañas hay que
  agregarla a `GRUPOS` y a `BARRAS`** (si no queda al final del menú, sin submenú; no desaparece).
- Tocar un submenú hace `irAPagina(pagina)` y `click()` sobre el botón original de la barra: se reusa el
  handler de cada pestaña en vez de duplicar qué mostrar. El árbol se mantiene al día con un
  `MutationObserver` sobre `aria-pressed` de las barras y envolviendo `irAPagina`, así que los saltos que ya
  existían (Tablero, tarjetas del Resumen de Salud) también marcan la pestaña en el árbol.
- "Entrega de Leche" no tiene pestañas sino cuatro tarjetas, en este orden: Entregas de Leche (CICLA), Comprador
  (calidad), Caudalímetro vs. comprador y Comparación de Cargas (antes "Balance de leche"): su submenú (`ANCLAS`) hace scroll a cada una.
- Un solo submenú abierto a la vez (el de la página activa); tocar la página activa lo pliega/despliega. El
  grupo de la página activa nunca queda plegado. El plegado de grupos se recuerda en `localStorage`
  (`lactia_menu_grupos`).
- El menú ahora es `position: sticky` (alto de la ventana, con scroll propio): con los submenús abiertos el árbol
  es más alto que la pantalla. En celular sigue siendo el panel fijo que se abre con ☰.

## Balance de leche: sala → tanque → camión (07/11/2026)

Página "Entrega de Leche" → tarjeta "Balance de leche". Pone la MISMA leche en los tres sistemas de medición, por
día: **sala** (DelPro, `AnimalDaily.TotalYield`, en KILOS), **tanque** (Milky, litros por carga, venta + guachera) y
**camión** (La Serenísima, litros por día). Dos tramos: *sala → tanque* y *tanque → camión*. Diseño:
`docs/superpowers/specs/2026-11-07-balance-leche-design.md`; plan: `docs/superpowers/plans/2026-11-07-balance-leche.md`.
Toda la lógica es `balance_leche.py` (pura, sin base ni red); `app.py` trae los datos (`_balance_leche`) y
`index.html` la dibuja (`cargarBalance`). Pruebas: `tests/` (`C:\Python314\python.exe -m unittest discover -s tests`).

**Los kg de DelPro incluyen las lecturas NO válidas (08/11/2026).** `AnimalDaily.IsYieldValid <> 1` marca un total
del día incompleto en DelPro (faltan ordeños de esa vaca), pero esa leche sí fue al tanque. Medido con
`medir_kg_invalidos.py` en SERVER-DELPRO (17 días, 17/10 al 02/11/2026): 33.287 kg, 1.406 filas (~83 por día), 2,83 %
de lo válido. Sumarlos baja el cociente Milky/DelPro de 1,0506 a 1,0217 y lo vuelve más parejo (rango diario 0,023
contra 0,049). El 02/11 (3.535 kg inválidos, el día con más) daba +3,2 % de "desvío" con el criterio anterior y da
+0,8 % con este: era leche medida de menos, no de más en el tanque. `sql_kg_diarios` suma todas las filas con leche;
las `vacas` (con las que se decide el día cerrado) siguen siendo solo las identificadas con lectura válida.

**El factor 1,02 L por kg es una CALIBRACIÓN, no una conversión física.** Medido con `medir_factor_litros.py` en
SERVER-DELPRO (17 días, 17/10 al 02/11/2026): con todos los kg, Milky da 1,0217 L por cada kg de DelPro (antes, solo
con lecturas válidas: 1,0506); la correlación es con el MISMO día (0,87; con ±1 día, 0,3). Por densidad (~1,03 kg/L)
darían ~0,971: DelPro queda ~7,6 % por debajo de lo que Milky y el comprador coinciden en medir. Las lecturas
inválidas explican solo el 34 % de ese faltante; el resto (~5 %) NO tiene causa conocida (¿medidores de sala que leen
bajo? ¿leche que no pasa por ningún medidor?).
Es editable en pantalla (solo admin, se guarda por tambo en `configuracion_tambos.json`, campo `factor_kg_litros`,
rango 0,5 a 2,0). Hay que volver a medirlo cuando haya más datos (`medir_factor_litros.py`).

Reglas de cálculo (todas medidas, ver el spec):
- **Día de DelPro cerrado** = vacas con leche ≥ 90 % de la mediana del período. DelPro consolida con VARIOS días de
  atraso (el 07/11 el último día completo era el 02/11; el 03/11 tenía 95 kg y 0 vacas). El piso de 50 vacas de
  `resumen.VACAS_MIN_DIA` no sirve acá (el tambo ordeña ~1.600). Los días sin cerrar se muestran atenuados y no
  tienen desvío.
- **Umbral ±3 %** (`cicla.UMBRAL_DIF_PCT`, el de la tarjeta del comprador). Desvío normal de la sala: 1,1 % por día.
- **Fecha corrida:** las cargas de Milky caen a las 00:00–00:02, 07:20 y 15:20 (~23.000 L cada una) y el comprador
  asigna la de medianoche a otro día. El 25 y 26/10 dieron +52 % y −27 % en el tramo del camión y se compensan
  (≈ −0,7 % de los dos días juntos): no es leche perdida. Regla: dos días seguidos, signo opuesto, la suma **en
  litros** dentro de ±3 % del volumen de los dos días, y **los dos días fuera del umbral**. Sin esa última condición
  el 01/11 (−1,2 %, ruido normal) junto al 02/11 (+3,2 %, desvío real) se tomaba por corrida y tapaba el desvío.
  Los días corridos se muestran en violeta y no alertan.
- **Fechas del comprador sin ceros** (`1/11/2026`): `balance_leche._iso_de_ar` rellena. Se descubrió probando en el
  navegador: los tests con fechas con ceros no lo veían.
- **Sin comprador** (sin `LASER_*` o si falla): el balance sale sin el tramo del camión. **Sin `MILKY_*`**: error
  claro (400) que nombra las variables.

**Alerta** (línea del aviso de alertas puntuales, `_lineas_alerta_balance`): desvío fuera de ±3 % en algún tramo.
- Un día se evalúa recién cuando existe un día **posterior** con dato en ese tramo (normalmente el siguiente: así se
  distingue fecha corrida de pérdida); la alerta llega con un día de atraso. Si el día siguiente no existe pero hay uno
  más adelante (DelPro sin ordeños ese día, o sin cerrar), el hueco es permanente y el día se evalúa igual: esperar
  habría descartado en silencio una alerta real.
- Una sola vez por día y tramo: `balance_leche_avisos.json` (fuera de git) guarda el último día evaluado por tambo. No
  se usa la antigüedad del dato porque DelPro consolida tarde. Sin registro previo solo se evalúa el último día
  evaluable (no inunda con avisos viejos).
- `registrar=True` SOLO en el ciclo real (`_revisar_alertas_whatsapp`); el botón "Probar" arma el mismo mensaje sin
  "gastar" el aviso. Si el envío falla después de registrar, ese aviso se pierde (aceptado: es raro).
- Una fuente caída es una línea "⚠️ Balance de leche: no se pudo revisar" y no corta las demás alertas. Sin `MILKY_*`
  no avisa nada (igual que la alerta de temperatura).

**Alcance por tambo:** Milky (`MILKY_*`) y La Serenísima (`LASER_*`) son de UNA instalación. El balance y su alerta solo
corren para el tambo por defecto o uno que declare `caudalimetro: cicla` (y `usina_lactea: la_serenisima` para el
tramo del camión) en ⚙ Configuración; otro tambo recibe un 400 claro. Sin esto, elegir otro tambo en el selector
cruzaba SU DelPro con los litros de La Ponderosa y mostraba desvíos de decenas de % como si fueran pérdidas.

**Caché de Milky y del comprador:** `_cache_o_traer` vuelve a traer el dato cuando venció (y devuelve el viejo si falla).
Antes se usaba `allow_stale=True` a secas y el dato quedaba congelado para siempre: con la alerta leyendo de ahí, el
tramo tanque → camión dejaba de alertar sin avisar (lo encontró el revisor final).

`/api/entregas/comparar` se refactorizó para compartir `_cargas_cicla` y `_entregas_laser` con el balance (misma
caché); su respuesta no cambió (hay un test con la respuesta capturada antes del refactor).

**Tambos sin caudalímetro: el camión desde los despachos de DelPro, tramo `sala_camion` (08/11/2026).**
Sin Milky no hay tanque: el balance compara la sala DIRECTO con el camión. `_balance_leche` ya no exige Milky;
`balance_leche.calcular(..., uso_interno=, con_tanque=)` agrega el tramo `sala_camion` SOLO cuando `con_tanque` es
falso (con Milky el camión se compara contra la venta del tanque como siempre, y La Ponderosa no cambió). Fuente
del camión (`_camion_balance`, y la respuesta trae `camion_fuente`): **1)** La Serenísima si el tambo la tiene y hay
`LASER_*`; si falla NO se cambia a otra fuente en silencio (el mismo gráfico no puede significar cosas distintas según
el día); **2)** si no, los despachos cargados en DelPro (`despachos_delpro.py`, menú Leche > Despachos de leche). Lo
que sale de la sala pero NO va en el camión es la leche de **uso interno** (`MilkForInternalUsage`), y se SUMA al
camión antes de comparar: sin eso cada día daría ~−5 % (en San José el uso interno era ~5 % de lo producido). Un
tambo sin Milky y sin ninguna fuente de camión recibe un 400 que lo explica; si los despachos no se pueden leer, un
tambo con Milky sigue funcionando (`avisos`) y uno sin Milky recibe un 502. La alerta (`_lineas_alerta_balance`)
también corre para un tambo sin Milky, con el tramo "sala → camión"; un 400 ("nada que comparar") no es una falla y no avisa.

**Lo que NO se puede afirmar todavía.** Los litros de DelPro son los tipeados del remito, no una medición, y el factor
1,02 se midió con Milky en La Ponderosa: en un tambo sin tanque **no hay con qué calibrarlo**, y la pantalla lo dice.
Ningún tambo al que tengamos acceso carga despachos hoy (San José los dejó en 10/2024, La Ponderosa local los tiene
vacíos), así que el modo se probó solo con datos SIMULADOS: `tests/simulacion_despachos.py` genera despachos con la
forma de San José (2 por día, "Tarde"/"Noche", uso interno de ~5 % en pasos de 20 L) a partir de los kg reales de
sala, con una pérdida y una carga corrida a pedido. `factor_real` de la simulación es un supuesto: con el mismo
factor que usa el balance, un día normal da ≈ 0 %. Se vio en el navegador con los kg reales de `ponderosa_local`
(julio 2026): la pérdida del 15/07 sale en rojo, el 20 y 21/07 como fecha corrida y el 27/07 (parcial) sin cerrar.

**Diferencia tanque → camión en plata + "otros destinos" (guachera) (08/11/2026).** Dos columnas más en la tabla
(y dos tiles de resumen) de la tarjeta, solo con tanque (`tanque_disponible`); la de plata además pide comprador.
- **Diferencia en $** = `lts_venta − lts_comprador` del día × precio de la leche. El pedido fue explícito: es una
  **ESTIMACIÓN que supone que esa diferencia se pudiera recuperar**, no plata perdida confirmada (puede ser calibración
  del caudalímetro, una carga que cambió de día, merma). La pantalla lo dice en la cabecera de la columna, en la
  leyenda bajo los tiles y en la explicación de la tarjeta. Signo: **+** = el tanque vendió más de lo que reconoció el
  comprador; **−** = el comprador reconoció más.
- **El total del período es la suma NETA** de los días con par tanque+comprador (`totales.lts_dif_camion/valor_dif_camion`):
  las fechas corridas se compensan solas (25/26-oct: +24.890 y −25.851 L son −961 L, no 50.000). Por eso las celdas de
  un día en `corrida_tanque_camion` van en violeta con un `title` que dice que no es plata a recuperar.
- **Precio**: `app._precio_leche_balance` toma el precio de `app._precios_del_tambo` (ver "Precio de la leche en
  ⚙ Configuración" abajo): el que cargó el tambo y, si no hay, el de la planilla de precios (`precio_litro`). Nunca lanza ni inventa un precio (regla de este archivo: sin precio no hay pesos): sin planilla, sin
  la fila de leche o con precio 0 sale `precio_leche.valor = None` + `error`, los litros se muestran igual y la pantalla
  pide cargar el precio. Si la planilla marca el precio como ESTIMADO (`leche_estimada`) se avisa en rojo junto a la plata.
  La respuesta trae `precio_leche {valor, estimado, via, usd_litro, tipo_cambio, error}`.
- **A otros destinos (guachera)** = `lts_milky − lts_venta` por día (`lts_otros_destinos`; lo que salió del tanque y no
  se vendió al comprador: guachera y cualquier carga que Milky no marca como venta) y `pct_otros_destinos` = % de
  lo que salió del tanque. No depende del comprador ni del precio. Es lo mismo que el "total − venta" que ya mostraba el
  tile del tanque ("venta + guachera"), ahora abierto por día.
- `calcular(..., precio_litro=)` es opcional: sin él nada de lo anterior cambia. Tests: `TestPlataYOtrosDestinos` y los
  `test_*precio*` de `tests/test_balance_leche_api.py` (la `Base` simula una planilla SIN precio, así que las pruebas no
  leen la planilla real de la PC). Visto en el navegador con datos de ejemplo (con precio estimado, y sin precio).

**Precio de la leche en ⚙ Configuración (08/11/2026).** Campo `precio_leche` ($ por litro, por tambo; ⚙ Configuración >
Entrega de leche). **Si está cargado MANDA sobre el de la planilla de precios** (que suele venir marcado ESTIMADO: USD 0,35 ×
cambio) y deja de ser "estimado" (`leche_via = "configuracion"`); vacío = se usa la planilla, como antes. Se valida > 0
(`configuracion_tambo.guardar`). Vale en TODO lo que valúa leche porque ahora hay un solo lugar que lee la planilla,
`app._precios_del_tambo` (antes eran seis lecturas directas): Balance de leche, Alimentación, Rentabilidad e informe de RCS.
Hay una prueba que falla si alguien vuelve a leer `precios_alimentos.leer(` directo. Con el precio configurado y la planilla
ilegible igual hay precio; sin precio configurado y planilla ilegible se lanza lo mismo que antes. Al guardar un precio
distinto se borra el caché del tambo (`_olvidar_datos_de`): alimentación y rentabilidad cachean 6 horas el costo con el precio
adentro y seguirían mostrando el viejo. A futuro el precio podría salir de una API; hoy lo carga el tambo.

**¿Por qué cambió la producción? (09/11/2026).** Tarjeta en Consultas e IA: se elige UN día y el RANGO contra el que se
compara (por defecto los 7 días anteriores; hasta 31 días, mínimo 3 distintos del analizado). La lógica es
`diagnostico_baja.py` (la usa también el script de consola `diagnosticar_baja_produccion.py`, que acepta el rango como 3.º y
4.º argumento). Es solo lectura y devuelve un INFORME DE TEXTO: 1) faltaron vacas o rindió menos cada una (descomposición en
kg), 2) vacas ausentes y las que más cayeron, 3) por grupo, 4) por ordeño (hora, duración, vacas, caudal), 5) bocas,
6) partos/cambios de grupo/bajas/tratamientos/retiros de leche, 7) ITH. Son pistas, no un veredicto; la ración (mixer, carro)
no está en DelPro y se cruza a mano. Tarda (minutos en el SQL Express local; en prod no se midió): `/api/diagnostico_baja`
(POST) lanza un hilo y devuelve el `id`; la pantalla consulta `/api/diagnostico_baja/<id>` cada 2 s. El mismo pedido en curso
no se duplica. Lecciones que costaron un error cada una: (a) `db.run_query` CORTA en 5.000 filas sin avisar y los totales
salían falsos: acá `corre` pide hasta 100.000 y aborta si `truncated`; (b) el rebaño se filtra por el grupo DEL DÍA
(`AnimalDaily.AnimalGroup`), no por `BasicAnimal.[Group]` (el actual): tras cambios de grupo el actual deja afuera casi todo el
rodeo; (c) una sección que falla dice "sin dato", nunca ceros, y si falla la lectura de producción el trabajo termina en
error, no "listo" con un informe vacío; (d) los días incompletos (< 90 % de las vacas habituales) se sacan de la base.

**Veredicto gráfico (09/11/2026).** Encima del informe de texto, la tarjeta muestra un titular con semáforo, tres gráficos
(de dónde sale la diferencia, por grupo, por ordeño) y las causas con su evidencia. `diagnostico_baja.analizar` devuelve
`informe` + `datos` (lo medido) + `veredicto`; el veredicto es `veredicto_baja.veredicto(datos)`, una función PURA (se prueba
sin base) con reglas y umbrales a la vista al principio del módulo. Niveles de cada causa: probable / posible / descartada /
sin_dato (una sección que falló NO se descarta). Nivel global: `normal` (la diferencia está dentro de 1 desvío de lo que el
tambo varía solo, o no fue baja), `claro`, `posible` o `sin_causa` (baja real que nada de lo medido explica: se manda a revisar
ración/agua/manejo, que DelPro no mide). Causas: vacas ausentes, rinde por vaca (desvíos del kg/vaca de la base), grupo,
ordeño (reparto y corrimiento de horario), bocas, calor (ITH), manejo del día anterior, mezcla por cambios de grupo, lecturas
no válidas y leche de descarte. **Casos reales que dieron forma a las reglas** (La Ponderosa, 07/11/2026): (a) la "baja
considerable" del dashboard (-1,8 %) era contra el 06/11, el día más alto del período; contra la semana era -1,0 % (0,7
desvíos): el veredicto lo dice en vez de inventar un culpable; (b) el informe sumaba lecturas no válidas y sin identificar
(70.653 kg) y el dashboard no (69.225 kg): ahora las cifras salen con la definición del dashboard y esas lecturas se muestran
aparte; (c) ese día hubo 127 tratamientos contra 31,7 de lo normal (desvasado correctivo): el análisis cruza por vaca si las
tratadas el día anterior rindieron menos que el resto (`datos["tratadas_ayer"]`), que es lo que separa "hubo trabajo sanitario"
de "y eso bajó la leche"; (d) un grupo que cambió más de 10 % de tamaño no se marca por kg/vaca (es mezcla, no rinde).
**Correcciones tras el 2.º informe real del 07/11** (base de 10 días: total +0,4 %, kg/vaca -3,2 % a 4,3 desvíos, 62 vacas más):
(e) nivel global `atencion`: total normal pero cada vaca rindió claramente menos (lo compensaron más vacas); decir solo «dentro
de lo normal» ocultaba lo que el dashboard marcaba en rojo; (f) el texto decía «pesa más: FALTARON VACAS» con 62 vacas MÁS:
la etiqueta mira el SIGNO de cada efecto, no su tamaño; (g) las vacas de la base que hoy no figuran se separan en SIN REGISTRO (no
se ordeñaron) y con lectura NO válida (se ordeñaron, DelPro no cerró el total: esa leche fue al tanque); mezclarlas inflaba
«faltaron 63 vacas»; (h) el cruce de tratadas es `probable` solo si explica la mitad de la baja del rinde por vaca o resta 1 % de
la leche (el 07/11: -98 kg de -2.297 = 4 %: descartada, aunque hubo 127 tratamientos); (i) «cada vaca rindió menos» pasa a
`posible` si las vacas presentes en los dos períodos no bajaron (mediana > -0,5 kg): el promedio bajó por la MEZCLA de vacas que
entraron, no por rinde. Conclusión de ese día: baja de rinde PAREJA en todo el rodeo (66 % de las vacas por debajo de su propio
promedio, mediana -1,1 kg, también las no tratadas), sin calor (día más fresco), sin equipo y sin un grupo único: lo que queda por
revisar es lo que DelPro no mide (ración del 06 y 07, agua, clima: lluvia/viento) y el ordeño de la tarde (caudal -5 %).

**Dólar en ⚙ Configuración y botones "Buscar" (09/11/2026).** El precio de la leche se movió a ⚙ Configuración > "Precios de
alimentos y de la leche", junto con el dólar (`tipo_cambio`, $ por US$). **El dólar configurado manda sobre el de la planilla
en TODO lo que la planilla convierte de dólares**: insumos en USD/tonelada y leche en USD/litro
(`precios_alimentos.aplicar_tipo_cambio`); lo cargado en pesos a mano no se toca y un precio de la leche en pesos configurado
sigue mandando sobre todo. Orden en `app._precios_del_tambo`: planilla -> dólar -> precio de la leche. Cambiar cualquiera de
los dos borra el caché del tambo. `precios_alimentos.resumen(ruta, datos=)` recibe esos mismos datos: la línea «leche $x · TC $y»
de Configuración leía la planilla cruda y contradecía lo que regía (segundo punto de lectura que la prueba de "un solo lugar" no
veía porque pasa por `resumen`). Cada valor guarda su **origen** (`precio_leche_origen`, `tipo_cambio_origen`: "OCLA SEP/2026",
"DolarHoy · Dólar oficial (venta) · 09/11/26 01:07 AM"); escribir el número a mano lo borra, y el Balance lo muestra.

`cotizaciones_externas.py` + `GET /api/cotizaciones/leche` y `/api/cotizaciones/dolar?tipo=` (oficial|mayorista|mep|blue):
solo LEEN, a pedido (nunca solas), **no guardan nada** (el valor queda en el campo y se guarda con "Guardar configuración"),
URLs fijas (sin SSRF), caché de 1 h, un error no se cachea. **Ninguna de las dos fuentes tiene API: se lee su HTML**, que
puede cambiar sin aviso, por eso cada una tiene respaldo y se rechazan valores absurdos. Dólar: DolarHoy (tarjeta con
`class="val"` o fila de la tabla "Cotizaciones"; Cloudflare lo deja pasar con un User-Agent identificado) y, si falla,
**DolarApi** (`dolarapi.com/v1/dolares/{oficial|blue|bolsa|mayorista}`; pide citarla como fuente; el MEP es "bolsa"). Leche:
**precio nacional al productor SIGLeA** del encabezado de la portada de OCLA ("SIGLeA (SEP/2026): $543,01 /litro"); respaldo, el
Excel `BaseDeDatosOCLA.xlsx` hoja "Series Mensuales", **que OCLA actualiza muy atrasado** (el 09/11/2026 llegaba a junio: el
código avisa los meses de atraso desde 3). Es un PROMEDIO NACIONAL: referencia, no lo que paga la usina del tambo. El precio
al productor de los gráficos de OCLA son imágenes (no sirven para leer). Pedir de OCLA a un tambo que cobra otro valor y no
avisarlo sería inventar un precio: por eso la pantalla muestra el período y que falta guardar.

## Panel de despliegue: variables de entorno + actualizar/iniciar/parar (08/11/2026)

`panel_despliegue.py` (lanzador `iniciar_panel.bat`, uso en INSTALL.md §11). Pedido del tambo:
dejar de cargar variables con `setx` una por una y de andar con `actualizar.bat`/`iniciar.bat`
al desplegar. **App de escritorio (tkinter) que se corre EN SERVER-DELPRO, proceso aparte de
LactIA y sin puerto**: tiene que poder parar y levantar LactIA sin llevarse puesta, y maneja
secretos y procesos de producción, así que una página alcanzable por el túnel de Cloudflare
sería una puerta que no hace falta abrir. El núcleo (`Entorno`, `Despliegue`) no importa tkinter
y es lo que prueban los tests; la pantalla (`Panel`) es una capa fina encima.

**La regla que ordena el archivo: no confiar en que "el comando anduvo".** Cada acción verifica
el estado real después —proceso, puerto, HTTP, el valor leído de vuelta del registro— porque el
historial de este proyecto es de fallas silenciosas (`actualizar.bat` sin Administrador deja
corriendo el código viejo; un `taskkill` sin permiso "termina" igual). Por eso `Parar` re-lista
los procesos y `Entorno`/`Registro.escribir` lee de vuelta lo que escribió.

**Variables: escribe al registro de Windows directo, no con `setx`** (`setx` trunca a 1024
caracteres y se confunde con las comillas). Cuatro cosas que importan, y que están probadas:
  * **Gana Usuario sobre Equipo.** Guardar en Equipo con un valor viejo en Usuario deja viendo el
    valor viejo, que es exactamente el "reinicié y sigue igual". El panel muestra los dos alcances
    (fila naranja si difieren) y ofrece quitar el del otro lado al guardar.
  * **Al iniciar un proceso se le pasan las variables FRESCAS del registro** (`Entorno.para_hijo`),
    no las que hereda el panel de cuando se abrió. Es lo que `setx` solo no logra: no llega a los
    procesos ya creados (mismo problema que ya resolvió `cicla._variable` para MILKY_*).
  * **`para_hijo` pisa solo las variables GESTIONADAS** (catálogo + las que el tambo agregó), no
    todo el registro: el `Path` de Usuario se SUMA al del Equipo en Windows, y copiarlo tal cual
    lo reemplazaría y rompería la búsqueda de `git` y `python`. Los `%VAR%` de un valor expandible
    se resuelven contra el entorno que se le arma al hijo, no contra el del panel.
  * **Tipo del valor**: `REG_SZ` salvo que ya fuera `REG_EXPAND_SZ` y el valor siga usando `%`;
    con `REG_EXPAND_SZ` un `%` suelto dentro de una contraseña se "expandiría" y la cambiaría.
    Un valor con salto de línea se RECHAZA en vez de recortarlo: no se cambia una contraseña sin avisar.

**`CATALOGO` no se puede desactualizar en silencio**: hay una prueba que recorre todos los `.py`
y falla si el código lee una variable (`os.environ`, `os.getenv`, `_variable(...)`, `VAR_* = "..."`)
que no está en el catálogo. **Si agregás una variable nueva a la app, agregala también ahí** (las
que se arman con f-string por tambo —`DELPRO_PWD_<TAMBO>`, `SENSEHUB_*`, `PODAL_CAM_*`— son
familias por prefijo). Los secretos van marcados y salen ocultos; nunca se escriben al log ni a
`panel_despliegue.json` (ese archivo guarda solo rutas y opciones; el token del túnel de
Cloudflare va como variable `TUNNEL_TOKEN`, no en los argumentos).

**Procesos: se buscan por NOMBRE DE SCRIPT en la línea de comandos** (`servidor.py`,
`iot_lavado.py`), exigiendo el nombre entero —`servidor.py` no calza con `dummy_servidor.py`—, y
se cierran con `taskkill /T /F` verificando después. Cierra TODOS los que calzan: el acceso
directo de la Store y el intérprete real aparecen como dos `servidor.py` (ver "Respuesta inválida
del servidor"). Lo que lanza el panel sale con `DETACHED_PROCESS`, con la salida a
`logs_panel\*.log`, y con `PYTHONIOENCODING=utf-8` (con la salida a un archivo Python usa cp1252 y
un emoji en un `print` tumbaría el servidor). **Actualizar** hace lo mismo que `actualizar.bat`
pero avisa antes si hay cambios locales que el `reset --hard` descartaría, corta `git` por tiempo
(sin prompt interactivo) y solo reinicia lo que estaba corriendo —el poller IoT no se arranca
solo si estaba parado—. Cloudflare tiene dos modos (proceso / servicio de Windows, `auto` mira si
existe el servicio), porque el servicio llegó a reiniciarse en bucle y el workaround fue correr
`cloudflared` a mano; no se sabía cuál usa hoy el servidor.

**Cómo se probó (29 pruebas, `tests/test_panel_despliegue.py`) y por qué así:** en esta PC la
otra sesión tiene su servidor en el 5310, así que NADA toca lo real: las variables van a claves
temporales de `HKCU\Software`; los procesos son un servidor de mentira en un puerto libre (que
además lee un puerto de una variable propia, no `DELPRO_PORT`: un `HTTPServer` con reuso de
dirección puede ROBARLE el puerto a otro proceso en Windows); y `actualizar` corre contra un
repositorio git temporal con su "origen", **nunca contra el real —un `reset --hard` acá borraría
trabajo ajeno—**. Lo que NO se pudo verificar desde acá, y es lo primero a mirar en el servidor:
la reapertura como Administrador (UAC), el modo y la ruta de `cloudflared`, y qué intérprete de
Python usa (por defecto el del propio panel). Por eso el primer uso es solo mirar el estado.

**Limitación operativa que conviene tener presente:** los procesos que lanza el panel mueren si
se CIERRA SESIÓN en el RDP (desconectar no los mata). Para que arranquen con la PC sigue haciendo
falta el Programador de tareas; el panel no lo reemplaza (podría ofrecer crearlas: queda pendiente).

## Origen de las cargas de leche: caudalímetro o despachos de DelPro (08/11/2026)

Pedido del tambo (viendo el menú Leche > Despachos de leche de DelPro): poder tomar las cargas de la
pantalla "Entrega de Leche" **de DelPro en vez del caudalímetro**, elegible desde ⚙ Configuración
como ya se elige el caudalímetro. **Primera versión, para ver cómo queda: solo la pantalla "Entregas
de Leche" (`/api/cicla/cargas`).** El Balance de leche, la comparación contra La Serenísima y el
aviso de temperatura de las 8:00/20:00 SIGUEN leyendo el caudalímetro; moverlos es una decisión
aparte (ver "no son lo mismo" abajo).

`despachos_delpro.py` es la rutina aparte: lee `MilkShipmentBase` (un despacho: remito, tanque,
camión, usina, litros, temperatura y la calidad que informa la usina: grasa, proteína, RCS, CBT...) y
`MilkForInternalUsage` (leche para uso interno, aparte), y devuelve las cargas con **las mismas
claves que `cicla._carga_desde_api`** (hay una prueba que lo exige), así `cicla.resumen()` y la
pantalla andan sin cambios. Elección: `configuracion_tambo` → "Origen de las cargas" (`caudalimetro ==
"delpro"`; vive en el mismo campo porque es la misma pregunta), y la propia pantalla tiene un selector
de origen (`?origen=`) para alternar sin tocar la configuración; se recuerda por tambo. Sin configurar
es el caudalímetro, como siempre.

**NO SON LO MISMO, y por eso no se cambió nada más sin avisar:**
  * Los litros de DelPro son los que se **cargaron** en el despacho (lo del remito), **no una
    medición**: no hay un segundo número para comparar, así que no hay "diferencia" ni alerta de
    diferencia (`lts_declarados` queda en None). Con el caudalímetro sí hay medido contra declarado.
  * Todo despacho de DelPro es una venta a una usina (`es_venta` siempre True); la leche que se queda
    en el tambo está en uso interno. El caudalímetro, en cambio, mezcla venta y guachera y las separa
    por el código de cliente.
  * Por eso mover el Balance de leche a DelPro NO es cambiar de fuente: el balance usa los litros
    medidos del tanque para detectar pérdidas, y un número cargado a mano no detecta nada.

**Sin verificar, y la pantalla lo dice:** (1) que `AmountOfMilk` esté en litros y no en kg; (2) qué
significa cada `InternalMilkUsage` (un entero sin tabla de nombres a la vista: se muestra "Uso N"); (3)
la unidad de `Scc`/`BacterialCount`. **La copia local de DDM tiene `MilkShipmentBase`,
`MilkForInternalUsage` y `MilkPrice` en 0 filas**, así que ningún dato real se vio: las consultas se
validaron contra el esquema real (corren sin error), los números de las pruebas y de las capturas son
de ejemplo. Los campos de calidad en 0 se tratan como "sin dato" (la usina no los informó), no como una
medición de 0. Si en SERVER-DELPRO el rango sale vacío, la pantalla lo dice ("DelPro no tiene despachos
cargados entre X e Y") en vez de mostrar ceros: es la forma más rápida de saber si el tambo carga
los despachos en DelPro.

**Lo que SÍ se vio en datos reales: San José (copia local `san_jose`), `medir_despachos_delpro.py`.** Cargó
230 despachos del 01/07 al 23/10/2024 (2 por día, "Tarde" y "Noche", a una usina, 4 a 9.100 L cada uno, la
mayoría de 6.000 a 8.000 y muchos casi redondos: es lo que se tipea del remito), con temperatura (~4 °C) y
los campos de calidad en 0 (la usina no los informa en DelPro); y 116 usos internos de 720 a 800 L por día
(el equivalente de la guachera). **Después del 23/10/2024 no hay ni un despacho**: dejaron de cargarlos. Y la
producción de sala de ese tambo en DelPro recién arranca en mayo de 2025 (antes tiene ~8 vacas por día), así
que despachos y sala **no se solapan ni un día** y no se pudo medir el cociente litros/kg. La Ponderosa
(copia local al 31/07/2026) los tiene vacíos; La Martina no se pudo consultar desde esta PC. Falta correr el
script en SERVER-DELPRO para ver si algún tambo los carga hoy.

`MilkPrice` (`MilkDate`, `PricePerVolumeUnit`) es el precio de la leche por fecha, también en DelPro y
también vacío acá: de tener datos podría reemplazar el precio ESTIMADO de la planilla de precios (ver
"impacto económico del RCS"). Queda anotado, sin hacer.

## Panel de despliegue: Cloudflare como proceso suelto con el servicio detenido (08/11/2026)

Primera prueba en SERVER-DELPRO: el panel mostró "Corriendo · PID … · servicio de Windows: Stopped": el
servicio de Windows EXISTE pero está detenido y el túnel corre como un proceso suelto (el que se
arrancó a mano cuando el servicio se reiniciaba en bucle). La primera versión decidía el modo por
"existe un servicio" y habría detenido un servicio ya parado, dicho OK y dejado el túnel andando.
Ahora `modo_cloudflared()` en `auto` manda lo que está PASANDO (servicio solo si está `Running`), y
`parar_cloudflared()` detiene el servicio si anda y después cierra cualquier proceso que quede,
verificando que no quede ninguno. Probado con servicio/procesos simulados (`TestCloudflared`).

## Entorno de desarrollo (esta PC)

Python no está en el PATH (`C:\Users\MAXI\AppData\Local\Programs\Python\Python312\`).
SQL Server Express local con poca RAM — consultas pesadas necesitan
`OPTION (MAXDOP 1, MAX_GRANT_PERCENT ...)` para no colgarse en
`RESOURCE_SEMAPHORE`. Detalle completo en la memoria de Claude de este usuario
(`delpro-entorno.md`, `delpro-deploy-produccion.md`) — son point-in-time,
verificar contra el código actual antes de asumir vigentes.
