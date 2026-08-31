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

## Entorno de desarrollo (esta PC)

Python no está en el PATH (`C:\Users\MAXI\AppData\Local\Programs\Python\Python312\`).
SQL Server Express local con poca RAM — consultas pesadas necesitan
`OPTION (MAXDOP 1, MAX_GRANT_PERCENT ...)` para no colgarse en
`RESOURCE_SEMAPHORE`. Detalle completo en la memoria de Claude de este usuario
(`delpro-entorno.md`, `delpro-deploy-produccion.md`) — son point-in-time,
verificar contra el código actual antes de asumir vigentes.
