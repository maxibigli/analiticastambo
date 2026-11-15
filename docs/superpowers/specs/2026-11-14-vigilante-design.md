# Vigilante: LactIA avisa sola cuando algo cambia (diseño)

Fecha: sábado 14/11/2026. Proyecto: LactIA (`delpro-analitica`, Flask + SQL Server DDM).
Estado: borrador para aprobación del usuario. Todavía no se construyó nada.

## Intención y el porqué

Hoy LactIA contesta si alguien pregunta ("Preguntale a LactIA", WhatsApp) y avisa por umbrales fijos en
dos horarios (08:00 y 20:00, configurables: hasta 5 por día). Nada mira la producción y decide solo que
algo anda mal, y el diagnóstico de baja (`diagnostico_baja.py` + `veredicto_baja.py`), que ya sabe separar
síntomas de causas, corre solo cuando alguien aprieta el botón y tarda decenas de segundos.

El usuario pidió un "Jarvis" que **avisa solo**: detectar que algo cambió, investigar la causa y
mandarla ya redactada por los canales que ya usa (WhatsApp, Telegram, correo), sin abrir la app.

**Dato del usuario (14/11/2026):** a SERVER-DELPRO le entran datos de DelPro durante el día, casi en tiempo
real, **cuando la conexión con producción está activa**. Esa condición es parte del diseño (ver "Sin datos").

## Lo que el usuario pidió explícitamente antes (restricción de diseño)

El 24/08/2026 recibió ~20 mensajes sueltos en un ciclo y pidió **un solo mensaje por ciclo** (CLAUDE.md,
"Alertas: un solo mensaje por ciclo"). El vigilante no puede volver a eso. Además pidió entonces un
snapshot completo en cada ciclo y no "solo lo nuevo": el vigilante **no reemplaza** ese mensaje de las
08:00/20:00, lo complementa entre medio.

## Objetivos

- Detectar, apenas termina cada ordeño, que ese ordeño salió claramente peor que lo normal, y avisar con la
  **causa probable ya calculada** y redactada en lenguaje de tambo.
- Callar cuando no pasa nada. Cuando algo sigue igual, no repetirlo.
- Decir "sin datos" cuando la conexión está caída, en vez de leer la desconexión como una baja.
- Nunca avisar un número que no coincida con el de las pantallas.

## No objetivos y anti-objetivos

- **No es un agente libre en cada pasada.** La IA solo redacta sobre hechos ya medidos; no decide si avisar.
- **No escribe en DelPro ni en ninguna fuente.** Solo lectura, como el resto.
- **No es multitambo en esta versión:** como las alertas actuales, mira `tambos.DEFAULT_TAMBO` (`ponderosa`).
- Anti-objetivo: que el tambo termine silenciando los avisos por ruido. Un vigilante que avisa seguido
  por nada es peor que no tenerlo.
- **Fuera de esta versión, y es un cambio respecto de lo hablado antes:** el RCS. Los controles lecheros son
  mensuales, así que no hay nada que "cambie durante el día"; solo se puede mirar cuando entra un control
  nuevo. Queda para una segunda etapa.
- **Fuera de esta versión:** cortes de la UPS. Las escalas de lectura SNMP todavía no se validaron contra la
  tarjeta real. Dejar el punto de enganche para sumarlos después.
- **Etapa 2, apagada por defecto:** la pasada diaria del agente que busca "algo raro" y lo suma al mensaje de
  las 08:00. Es decisión del diseñador (orden), no del usuario.

## Qué mira (cuatro condiciones, todas con datos que ya existen)

Los umbrales son propuestas del diseñador, a calibrar con datos reales (ver "Cómo se sabe que funciona").

1. **Baja del ordeño:** los kg de un ordeño terminado contra el mismo número de ordeño (`SessionNo`) de los
   7 días anteriores completos. Se avisa si está a **2 desvíos o más por debajo** (`Z_FUERTE` de
   `veredicto_baja.py`) y a **al menos 4 % por debajo** de la media. Se compara ordeño contra ordeño, nunca
   el día a medio hacer contra días enteros.
2. **Vacas que faltaron:** vacas habituales que no pasaron por ese ordeño (misma idea que `sql_vacas` /
   `PCT_VACAS_AUSENTES`), si pesan 1 % o más de los kg de la base.
3. **Primera comida del mixer atrasada:** reusar la lógica de la tarjeta "Retraso de la primera comida"
   (`ALIM_RETRASO_MIN` = 60 min). Es la única condición que no viene de la sala.
4. **Sin datos:** no entra ningún ordeño nuevo desde hace más de N horas dentro del horario habitual de
   ordeño (N = 4 por defecto, configurable). Es un estado propio, no una condición de producción.

**Un ordeño "terminó"** cuando no entraron filas nuevas de `SessionMilkYield` en los últimos 45 minutos
(propuesta del diseñador; el builder la ajusta con datos reales).

**Decisión del diseñador: las incidencias de la rotativa quedan afuera.** Ya salen dos veces por día en el
mensaje de las 08:00/20:00 (`_lineas_alertas_puntuales`), y el usuario ya se quejó de ver lo mismo repetido
(24/08/2026). Un aviso inmediato duplicaría la línea.

**Qué kg se cita y a qué día pertenece un ordeño (decisión del diseñador):** el aviso cita los kg por ordeño
tal cual los muestra la pantalla donde el tambo ve sus ordeños (el builder confirma cuál es, probablemente
Rendimiento Sala, que agrupa por `ParlorSession`) y no una suma propia. Un ordeño pertenece al día en que
**empezó** (un ordeño de 23:50 a 01:30 es del día que empezó). La clave de dedup es
`(fecha de inicio, número de ordeño, condición)`. Si el builder encuentra que la suma de `sql_sesiones`
(que incluye lecturas no válidas) difiere de la pantalla, gana la pantalla y se anota la diferencia.

## Qué hace cuando se dispara

1. Una pasada **liviana** cada ~15 minutos (configurable) evalúa las cuatro condiciones usando consultas
   acotadas y lo que ya está en caché. Nada de consultas pesadas en esta pasada.
2. Si una condición de producción (1 o 2) se dispara, **recién ahí** se busca la causa. Es la consulta
   pesada: en SQL Express `db.py` las serializa de a una (`Semaphore(1)`), así que se encola detrás de lo que
   ya esté corriendo.

   **`diagnostico_baja.analizar` NO sirve tal cual para un día a medias:** compara el día completo contra la
   base, así que con el primer ordeño de tres diría "faltaron vacas" y "caída enorme" por un ordeño que todavía
   no pasó. **Decisión del diseñador: no se toca `diagnostico_baja.py`** (está verificado, commit `7eb74e3`).
   El vigilante arma su propia versión por ordeño, **reusando los constructores de consulta ya existentes**
   (`sql_sesiones`, `sql_bocas`, `_del_rebano`) y las constantes y reglas de `veredicto_baja.py`, y compara solo
   ordeños terminados contra los mismos ordeños de la base. La causa que explica queda limitada a lo medible por
   ordeño: rodeos o vacas que faltaron, bocas, ITH, comida del mixer. El aviso dice "ordeño N, día en curso" y
   nunca habla del día entero. (Si el día ya cerró, un resumen del día completo con `analizar` queda para el
   mensaje de las 08:00.)
3. **La IA redacta, no decide.** Recibe el veredicto y los números medidos y escribe un aviso corto (seis
   líneas como máximo: qué pasó, cuánto, causa probable, qué mirar). Cada número del texto debe figurar en
   los datos medidos; si alguno no figura, o la IA falla, o no hay `ANTHROPIC_API_KEY`, **sale igual con el
   texto determinístico del veredicto**. La alerta nunca depende de que la IA responda. (Mismo patrón que
   `_esta_entre` de `agente.py`.)
4. Sale por `_enviar_resumen_a_canales_activos` (WhatsApp/Telegram/correo según estén tildados).

## Control del ruido

- Si en una misma pasada se disparan varias condiciones, **van en UN solo mensaje**.
- **Dedup:** una condición ya avisada para el mismo día y el mismo ordeño no se repite. Se guarda en un
  SQLite propio (`vigilante.db`, a agregar a `.gitignore` como `podal.db`/`iot_sensores.db`).
- **Tope de avisos por día** (por defecto 3) y **horario silencioso** (por defecto de 22:00 a 05:00). Ambos
  configurables. **Reglas (decisión del diseñador):**
  - Lo que se dispara con el tope alcanzado **no se descarta ni se manda suelto**: se anota como línea en el
    próximo mensaje de las 08:00/20:00 ("el vigilante retuvo N aviso(s)"). Nada se pierde y no hay mensajes de más.
  - Lo que se dispara en horario silencioso **espera hasta las 05:00** (el ordeño nocturno entra en ese caso a
    propósito; el usuario puede acortar el horario silencioso).
  - "Sin datos" **no cuenta contra el tope** y respeta el horario silencioso.
- El estado "sin datos" avisa **una vez** al empezar y **una vez** cuando vuelven los datos, no cada pasada.

## Configuración y prueba

- Solapa nueva en ⚙ Configuración, mismo patrón que "🤖 IA por WhatsApp" y "🔌 Entradas/Salidas": encendido,
  frecuencia de la pasada, tope por día, horario silencioso, horas de "sin datos". Los canales son los
  existentes (`alertas_canales.json`); no se crea un canal nuevo.
- Botón **Probar**: evalúa y arma el mensaje **sin registrar** el aviso (igual que `registrar=False` en
  `_lineas_alerta_balance`): probar no puede "gastar" un aviso real.
- No hay variables de entorno nuevas: usa `ANTHROPIC_API_KEY` y las de los canales, ya cargadas.

## Restricciones del proyecto que aplican (CLAUDE.md)

- Los números tienen que ser los mismos que muestra la pantalla: reusar las consultas existentes, no escribir
  una segunda versión de la lógica.
- Días INCOMPLETOS fuera de los promedios (la base de 7 días excluye el día en curso y los días incompletos).
- Fechas concretas con día de la semana en los textos, nunca "ayer"; "rodeo" con el nombre que da DelPro.
- Solo lectura. Si hubiera que escribir en DelPro o en MixerOne, no se hace.
- Cada chequeo en su propio `try/except`: una fuente caída es una línea de advertencia, nunca corta a las demás.
- Si se lee alguna variable de entorno nueva, tiene que estar en `panel_despliegue.CATALOGO` (hay un test).

## Riesgos que el builder debe medir primero (no son supuestos)

1. **¿`SessionMilkYield` se llena en tiempo real, o se consolida tarde como `AnimalDaily`?** El diseño del
   Balance de leche midió que el último día de DelPro "se consolida tarde" (03/11: 95 kg). Si la tabla por
   ordeño también llega tarde, el vigilante no puede avisar "apenas termina el ordeño" y el diseño cambia.
   Primera tarea: un script de solo lectura estilo `medir_*.py`, corrido en SERVER-DELPRO, que mida el retraso
   entre el fin de un ordeño y la aparición de sus filas. **Plan B (decisión del diseñador):** hasta 60 minutos
   de retraso se sigue adelante con el diseño tal cual. Entre 60 minutos y unas horas, el aviso sale igual pero
   dice "con los datos al momento" y no promete inmediatez. Si el retraso es de horas o hay ordeños que llegan
   partidos, **se detiene el trabajo y se vuelve a hablar con el usuario**: el vigilante por ordeño no vale la
   pena y habría que pensar otra cosa.
2. **Costo de la consulta liviana** de cada 15 minutos sobre SQL Express con DelPro escribiendo. Medir.
3. **Qué tan "normal" es la variación entre ordeños** del mismo número (mañana contra tarde, domingos).

## Cómo se sabe que funciona

- **Reproducción histórica:** correr el detector sobre los últimos 60 días de DDM y contar cuántos avisos
  habría mandado. Objetivo propuesto: **no más de uno por semana en condiciones normales**, y que **sí**
  se dispare con la baja real del 07/11/2026 (la del informe que originó las correcciones `7eb74e3`).
  **Si los dos objetivos chocan** (la baja del 07/11 fue pareja en todo el día, y puede que ningún ordeño suelto
  llegue a 2 desvíos y 4 %), **cede el límite de uno por semana, no la detección**: se bajan los umbrales
  hasta que el 07/11 salte y se le informa al usuario cuántos avisos por semana resulta, para que decida.
  Si hace falta, se suma una comparación acumulada del día (litros hasta esa hora contra los mismos ordeños de
  la base). La copia local de DDM corta el 30/07: esta reproducción se corre en SERVER-DELPRO.
- Tests con datos armados para cada condición, para "sin datos" (no se confunde con baja), para el dedup,
  el tope diario y el horario silencioso, y para el fallback sin IA (el mensaje sale igual).
- Antes de darlo por terminado, un aviso real de punta a punta por un canal en SERVER-DELPRO. Las alertas
  anteriores se probaron solo con mocks y quedaron "falta probar en producción"; esta no tiene que quedar así.

## Enfoques considerados

- **A. Solo reglas fijas:** predecible y sin costo de IA, pero detecta únicamente lo que alguien previó y el
  mensaje es una plantilla. No elegido.
- **B. Agente de IA que decide qué avisar en cada pasada:** flexible, pero gasta IA siempre, puede inventar
  alarmas y es la forma más directa de volver a los 20 mensajes. No elegido.
- **C. Híbrido (elegido por el usuario el 14/11/2026):** reglas livianas vigilan y deciden, el diagnóstico
  busca la causa solo si algo se dispara, y la IA solo redacta sobre hechos medidos.

## Qué queda para el builder

Dónde vive el código (propuesta: `vigilante.py` con hilo de fondo como `_bucle_alertas_whatsapp`, y las
rutas en `app.py`), el esquema de `vigilante.db`, los textos exactos de los avisos, el ajuste fino de los
umbrales con los datos reales, y la interfaz de la solapa de Configuración.

## Anotaciones de la implementación (15/11/2026)

Lo que se decidió construyendo y no estaba en el diseño (detalle y costo de cada uno en `CLAUDE.md`, sección «Vigilante»):

- **«Sin datos» mide por número de ordeño**, no de punta a punta del día: una sola ventana marcaría «sin datos» en el
  hueco normal entre dos ordeños.
- **El horario silencioso 22:00–05:00 cae sobre el primer ordeño de La Ponderosa** (~00:10 a ~04:40, medido en la copia
  local): su aviso sale a las 05:00. El tambo puede cambiarlo en la pestaña.
- **Faltantes por vaca** solo se consulta si el ordeño ya salió al menos 1 % por debajo de la base (mantiene la pasada liviana).
- **Pendiente de medir** (Tarea 1 del plan): el retraso real con que DelPro carga cada ordeño en `SessionMilkYield`. El diseño
  supone casi tiempo real; hasta correr `medir_retraso_ordenos.py` 24 h en SERVER-DELPRO no se sabe.
