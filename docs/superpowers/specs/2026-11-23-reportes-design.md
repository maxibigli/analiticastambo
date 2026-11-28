# Informes personalizables (estilo DelPro, en la web) — diseño

Fecha: 23/11/2026. Estado: **aprobado para la Fase 0** por el usuario; las fases siguientes se deciden con lo que enseñe la Fase 0. **Fases 1 a 4 hechas y en master** (24/11/2026); la Fase 4 pasó por dos
revisiones independientes (ver «Revisión final de la Fase 4»). Lo que falta es probarla en SERVER-DELPRO y aceptar la Fase 0 (ver «Pendientes de la Fase 4»).

Maquetas navegables (datos de ejemplo, privadas): versión elegida
<https://claude.ai/artifact/YTvaKfeT2AWaPqZkeNwivA> (pestañas de documento como DelPro) y la primera
<https://claude.ai/artifact/2b2KDiqcqDHQ19erpQV8mC> (todo en una pantalla).

## Qué se quiere

Una sección web donde el usuario arma sus propios informes como en el «Diseñador de informes» de DelPro: elegir campos de un
árbol por categorías, filtrar (animales, rodeo, grupo, condiciones), ordenar, agrupar, sumar / promediar / contar por columna,
agregar campos calculados, **guardar cada informe con nombre**, y **graficarlo en un rango de fechas**. Con la ventaja de ser
web: gráficos, tendencias dentro de la tabla, exportar a Excel, ver desde el celular.

## Lo que se encontró en DDM (23/11/2026, copia local del 31/07/2026)

DelPro guarda su propio diseñador de informes dentro de la base:

| tabla | filas | qué es |
|---|---|---|
| `DDMReport` | 220 (141 predefinidos, 79 del tambo) | informes con nombre, carpeta, favorito, filtro |
| `DDMReportItem` | ~1.740 | catálogo de campos: `Name` (inglés interno), `Category`, `Aggregate`, `Expression` |
| `DDMReportColumn` | 2.336 | columnas de cada informe: orden, `SummaryType`, `GroupIndex`, `SortDirection` |
| `DDMGraphSeriesReport` | 227 | series de gráficos |

* Los 79 informes del tambo usan **223 campos distintos de los ~1.740**: el catálogo útil es chico. Más usados: número de animal
  (63 informes), nombre de grupo (41), nº de lactancia (37), días en ordeño (31), estado reproductivo (31), promedio de 7 días (27).
* **No se puede importar el catálogo tal cual**: la `Expression` está en el lenguaje de criterios de DevExpress XPO
  (`AnimalDaily.AnimalGroup.Number`, `History[ObjectType = TMilkTest() AND ...]`), no es SQL, y las etiquetas en español no están en
  esas tablas. Sí sirve como **fuente de verdad de qué significa cada campo** al traducirlo a SQL.
* Resúmenes por columna usados (783 columnas): ninguno 619, promedio 88, conteo 57, suma 17, máximo 2 (códigos supuestos del
  enum estándar de DevExpress; **verificado en la Fase 4** contra la base real: 0 suma, 1 mínimo, 2 máximo, 3 conteo, 4 promedio, 5 y 6 ninguno). Solo 10 columnas agrupadas.

## Decisiones

1. **El informe es una definición declarativa (JSON), nunca SQL.** Un compilador la traduce a una consulta con parámetros contra
   una lista cerrada de campos y operaciones. Mismo motivo por el que «Preguntar con IA» está bloqueado en producción: las trampas
   de DDM (rebaño, rodeo del día contra el de hoy, RCS en miles) no se pueden dejar a quien escribe la consulta.
2. **Catálogo propio, curado, por campo verificado** (`reportes_catalogo.py`). El identificador del campo es el `Name` de DelPro
   cuando existe (`CurrentDIM`, `CurrentLactationNumber`, `SevenDayAverage`…): así se podrán importar los informes del tambo.
   Cada campo declara etiqueta en español, categoría, tipo, unidad, resúmenes admitidos, salas donde existe y su bloque de datos.
3. **Bloques de datos.** La consulta solo une los bloques que las columnas elegidas necesitan (núcleo del animal; producción
   reciente; lactancias; reproducción; control lechero), cada uno pre-agregado por animal. Pocas columnas = consulta barata.
4. **Se guarda en SQLite propio** (`reportes.db`, fuera de git), por tambo. Nunca en DDM (de solo lectura y se pisa en cada restore).
5. **Resúmenes correctos.** El promedio de un grupo es suma/cantidad, no promedio de promedios; los campos calculados trabajan
   sobre agregados. Hace falta un promedio ponderado explícito.
6. **Ejecución con las reglas de la app:** consultas serializadas por servidor, en segundo plano con caché y «datos al DD/MM»,
   tope de filas, período obligatorio en los niveles diarios.
7. **Pantalla propia `/informes/`** (plantilla aparte, como `/checklist/`), no dentro de `index.html`. Pestañas de documento,
   árbol de campos a la derecha, gráfico en su propia pestaña (ver maqueta v2). Chart.js, que ya se usa.
8. **Roles:** lo arma y lo ve el administrador. [Supuesto: el operario no entra.]

## Fases

* **Fase 0 (esta):** catálogo de ~12 campos del nivel «Animal» + compilador + ejecución, reproduciendo el informe
  «Prod. x lactancia» y comparándolo campo por campo con DelPro. Sin pantalla nueva, sin guardado. Valida el enfoque y la velocidad.
* Fase 1: pantalla (pestañas, árbol, filtros) y guardado con nombre, exportar a Excel.
* Fase 2: resúmenes, agrupación, totales, campos calculados. **Hecha** (ver «Fase 2: estado»).
* Fase 3: nivel «Animal por día» y gráficos con rango de fechas. **Hecha** (ver «Fase 3: estado»), sin un nivel «Animal por día» general.
* Fase 4: importar los informes de DelPro, armarlos por lenguaje natural, envío programado. **Hecha** (ver «Fase 4: estado» y «Revisión final de la Fase 4»); sin probar contra la base real ni con el modelo real.

## Fase 0: plan y criterio de aceptación

Campos de arranque (los de la captura del informe «Prod. x lactancia»): número del animal, nombre del grupo, nº de lactancia,
días en ordeño, estado de la reproducción, producción 24 h, promedio 7 días, pico de la última lactación, días al pico,
producción total de lactancia, fecha del último parto.

Pasos: (1) leer la definición de cada campo en `DDMReportItem` y las tablas que usa; (2) escribir el catálogo con su SQL y una
prueba por campo; (3) compilador con lista cerrada + pruebas de rechazo (campo desconocido, valor con comillas, operador raro);
(4) `POST /api/informes/ejecutar` (admin, tope de filas); (5) medir tiempo en SQL Express; (6) comparar contra la exportación de
DelPro.

**Aceptado cuando:** todos los campos del informe coinciden con la exportación de DelPro del **mismo momento** (misma copia de la
base, mismas vacas), con el recuento «N de N» a la vista, y la consulta con todas las columnas tarda un tiempo razonable en la PC
de desarrollo. Cada diferencia se explica o se corrige; no se baja el listón.

**Necesito del usuario para cerrar la comparación:** exportar de DelPro el informe «Prod. x lactancia» (Excel) **justo después de
que corra el backup** que se va a restaurar, para que los dos lados vean los mismos datos. La captura del 23/11 no sirve para
comparar contra la copia del 31/07: las vacas cambiaron.

## Fase 0: estado al 23/11/2026

Módulos: `informes_catalogo.py` (campos y bloques), `informes_motor.py` (compilador y ejecución), `GET /api/informes/campos` y
`POST /api/informes/ejecutar` (administrador) en `app.py`. Pruebas: `tests/test_informes_motor.py`, `test_informes_catalogo.py`,
`test_informes_api.py` (49).

**Cómo se verificó cada campo (copia local del 31/07/2026, «hoy» de los datos = 30/07/2026).** Como la captura de DelPro del 23/11
no existe en esta copia (los mismos números de animal corresponden a otras vacas: el número de animal NO es único entre rebaños de
la misma base), se comparó contra lo que DelPro mismo guarda y sus funciones:

| campo | contra qué | resultado |
|---|---|---|
| Nº Lactación (`CurrentLactationNumber`) | `AnimalDaily.LactationNumber` | 1665 de 1665 |
| Días en ordeño (`CurrentDIM`) | `AnimalDaily.DIM` (días desde `LastLactationChangeDate`) | 1664 de 1665 |
| Promedio 7 días (`SevenDayAverage`) | `AnimalDaily.AvgYieldPrev7d` | 1615 de 1615 |
| Días al pico, lactancia anterior | `dbo.GetDaysToPeakInLactation` | 40 de 40 (solo hay datos desde dic-2025) |
| Fecha del último parto (`LastCalvingDate`) | último `EventCalving` contra `LastLactationChangeDate` | 3405 de 3490: ~2 % difieren; se usa el evento, como la definición de DelPro |
| Estado de la reproducción | `ReproductionInfo.BreedingState`, la fórmula de DelPro (desde el 27/11/2026; antes se deducía de los bits) | **sin verificar** contra un informe de DelPro; los nombres de los códigos 1 y 8 no se vieron escritos en la base (ver CLAUDE.md) |
| Pico de la última lactación | `AnimalLactationSummary.PeakYield` | **sin verificar**: la función de DelPro no existe en esta base |

Esto prueba que el SQL calcula lo que DelPro **guarda**, no que el informe sea igual al de la pantalla de DelPro: eso sigue
necesitando la exportación del mismo momento.

**Velocidad:** 9 columnas, 5.000 filas, contra la copia local: 0,8 s.

**Diferencias que conviene saber:**
* Las definiciones de `DDMReportItem` citan funciones (`dbo.GetPeakYieldInLactation`) que no existen en todas las bases:
  no se depende de ellas.
* «Pico» y «días al pico» están vacíos para casi todas las vacas (solo 984 de 13.583 lactancias lo tienen): los datos de leche
  empiezan en dic-2025. DelPro muestra esos mismos huecos.
* El informe de DelPro trae solo vacas en lactancia; el motor trae todos los animales del rebaño. Se filtra con condiciones
  (Fase 1); todavía no hay un filtro «en ordeño».

## Fase 1: estado al 23/11/2026

Pantalla `/informes/` (plantilla `templates/informes.html`, solo administrador) mostrada en el menú principal como **📑 Informes**,
en el primer grupo, después de «Consultas e IA» (dentro de un marco con `?embebido=1`; el tambo viaja en la dirección).

* **Lista de informes** con favoritos y búsqueda; cada informe y cada resultado en su propia pestaña, como DelPro; árbol de campos
  a la derecha (los campos sin verificar llevan ⚠).
* **Armar:** elegir y reordenar columnas (arrastrando), condiciones por campo (número, texto o fecha), incluir descartados,
  «Calcular al» (el «hoy» de los datos; vacío = hoy), orden por columna, **Actualizar** a pedido (la base es lenta: no se
  recalcula en cada cambio).
* **Guardar con nombre** (`informes.db`, por tambo, nombre único sin importar mayúsculas), «Guardar como…», favorito.
* **Exportar a Excel** (`informes_excel.py`): números y fechas reales, hoja «Condiciones», sin fórmulas, aviso si se cortó.
* API (administrador): `GET/POST /api/informes`, `GET/PUT /api/informes/<id>`, `POST /api/informes/exportar`.
* Pruebas: 95 de informes en total (motor 38, catálogo 7, API 19, guardado 16, Excel 9, página y menú 6); suite completa 1638 OK. La pantalla se revisó en el navegador con datos de ejemplo (la base local se
  quedó sin memoria en ese momento); falta probarla contra la base real y desde el menú de un servidor.

**Fuera de la Fase 1, a propósito:** agrupar, resúmenes por columna y campos calculados (Fase 2); gráficos (Fase 3); eliminar
informes (por la regla de no borrar: se resolverá archivando).

## Fase 2: estado al 24/11/2026

Agrupar, resumir y calcular, en la misma pantalla y con el mismo motor.

* **Resumen por columna:** suma, promedio, mínimo, máximo y conteo (fecha: mínimo, máximo, conteo; texto: conteo). Se calcula **en SQL sobre
  TODOS los animales del informe**, no sobre las filas que se ven: si la lista se corta en el tope, los totales siguen siendo los de todos.
  El promedio es el de los animales con dato (`AVG` sobre `CAST ... AS float`, porque el `AVG` de enteros trunca en SQL Server), nunca un
  promedio de promedios. Queda sin resolver el promedio **ponderado** que pedía la decisión 5: hace falta definir contra qué se pondera.
* **Agrupar hasta en dos niveles**, solo por campos con pocas categorías (`agrupable` en el catálogo: nombre y número del grupo, nº de
  lactancia, estado de la reproducción). Un `GROUP BY ROLLUP` da cada grupo, cada subtotal y el total en una consulta. Los grupos salen en
  orden natural («Rodeo 2» antes que «Rodeo 10»), con «(sin dato)» al final.
* **«Solo el resumen»:** sin la lista de animales, una sola consulta. Exige agrupar o tener al menos un resumen (la pantalla lo apaga sola
  si se quita lo último).
* **Campos calculados** (hasta 10 por informe): cuentas con `[Campo]`, números y + − × ÷ ( ), traducidas a SQL por un analizador propio
  (`informes_formula.py`; nada de `eval` ni texto libre): todo a `float`, la división con `NULLIF` (un cero da vacío, no un error). Solo con
  campos del catálogo, no entre calculados. Sirven de columna, condición, orden y resumen; no para agrupar. Si usan un campo sin verificar,
  el resultado también lo es (⚠). La pantalla valida la fórmula mientras se escribe (`POST /api/informes/formula`, sin tocar la base).
* **Pantalla:** selector de resumen en cada columna, barra «Agrupar por… y luego por…», grupos plegables con su cantidad
  de animales y sus resúmenes, «Plegar todo / Desplegar todo», total fijo abajo, y un cuadro para crear, editar y eliminar campos calculados
  con una línea «Se lee: …» que traduce la fórmula a los nombres en español.
* **Excel:** tres formas (lista con fila de totales; agrupada, con los grupos plegables con el «−» de Excel y subtotales; solo resumen),
  apaisado, una hoja de ancho y el encabezado repetido en cada página. Cada cifra de resumen dice cuál es («Prom. 27,5») y sigue siendo un
  número. Ningún texto se escribe como fórmula, tampoco el nombre que el usuario le pone a un campo calculado.

**Verificado contra la base real** (copia local, «hoy» = 30/07/2026; 1.779 animales con lactancia, agrupados por rodeo y lactancia, con conteo,
promedio, máximo, mínimo, suma y un campo calculado): las 51 filas de resumen (1 total + 50 grupos) × 7 resúmenes que calculó SQL coinciden
con lo calculado en Python sobre las mismas filas del detalle: **0 diferencias**. «Solo resumen» da exactamente los mismos grupos y totales.
Tarda 0,9 s con detalle y 0,1 s sin él. La pantalla se probó en el navegador: abrir un informe guardado, plegar, cambiar resúmenes y grupos,
crear, editar y eliminar un campo calculado, guardar y reabrir, exportar, y el celular, con datos reales y, cuando el SQL local se quedó sin
memoria, con 90 animales de ejemplo.

**Pruebas:** 217 de informes (motor 76, catálogo 13, fórmula 20, guardados 18, API 29, Excel 51, página 10). El catálogo comprueba que cada
combinación de campo, resumen y agrupación pase `db.validate_sql`; la fórmula tiene un fuzz de 7.500 casos; la API prueba el camino completo
(pedido → motor → Excel) con un informe agrupado y uno de solo resumen.

**Límites conocidos:**
* Un resumen por columna, y un campo no puede estar en dos columnas: no se ve el promedio **y** la suma del mismo campo a la vez (se
  resuelve con un campo calculado que lo duplique, por ejemplo `[Campo] * 1`).
* El plegado de los grupos con el teclado (Enter o espacio sobre el encabezado) está programado pero no se probó a mano.
* Si el servidor tarda (SQL Express sin memoria), la pantalla dice «Calculando…» sin límite de tiempo.
* El informe trae todos los animales del rebaño (crianza, piquetes…) salvo que una condición lo acote; falta un filtro «en ordeño».
* El Excel se verificó leyendo el `.xlsx` y abriendo uno de los archivos en Excel, que en la PC de desarrollo está sin licencia y no deja imprimir
  ni exportar: la impresión apaisada y el plegado de grupos no se vieron en una hoja real.

## Fase 3: estado al 24/11/2026

El gráfico de un informe, en su propia pestaña («Graficar», al lado de la tabla), como en la maqueta elegida (v2): medida, resumen, una serie por…,
intervalo, forma y período arriba; el gráfico con su leyenda (que prende y apaga series) y una tabla de estadísticas abajo.

**Qué grafica.** La producción de leche **por día** de los animales del informe, en un rango de fechas. No se hizo un nivel «Animal por día» general en el catálogo
(como pensaba la lista de fases): `AnimalDaily` tiene muy pocos datos diarios (`TotalYield`, `DIM`, `DSLC`, `LactationNumber`, `Duration`, `AvgYieldPrev7d`, el rodeo de ese día
y si la lectura es válida), y solo la leche está verificada. Se resolvió con un módulo propio, `informes_serie.py`.

* **El servidor entrega números diarios, la pantalla hace el resto.** `POST /api/informes/serie` devuelve, por día y por serie, las vacas con lectura válida, los kg de esas vacas y los
  kg de toda la leche medida. La medida, el resumen, el intervalo (día, semana de lunes a domingo, mes), la forma y las estadísticas se resuelven en `static/js/informes_grafico.js`,
  un módulo puro con sus pruebas (Node): cambiarlos no vuelve a la base, que es lenta. Solo la serie, el período, la población y el «Calcular al» piden datos nuevos.
* **Series:** rodeo **de ese día** (`AnimalDaily.AnimalGroup`), lactancia (1ª, 2ª, 3ª o más) o todo junto; más de 8, las 7 de más leche y «Otros».
* **Medidas:** producción por vaca (kg de lecturas válidas ÷ vacas con lectura válida), producción total (**toda** la leche medida, también las lecturas no válidas, como DelPro:
  son 3 a 4 % de los kg en La Ponderosa) y vacas en ordeño (válidas).
* **Población.** Con condiciones en el informe y «Solo los animales del informe» (tildado por defecto), la consulta se restringe a los que **hoy** las cumplen: la misma población que filtra la
  lista (`informes_motor.poblacion_sql`, que comparte con `compilar` el filtro y los bloques). Sin condiciones no se restringe: son todas las vacas del tambo con su historia, también las que
  se vendieron después, para que el total de cada día sea el que tuvo el tambo. La pantalla dice cuál de las dos es y cuántos animales son.
* **Días a medio cargar.** DelPro consolida con atraso y el último día suele estar a medias. Un día se marca cuando sus vacas con cualquier lectura son menos del 60 % de la mediana del período; no se grafica
  (hueco y franja amarilla), no cuenta en el promedio de su semana y se nombra bajo el título. Se juzga por lecturas y no por vacas válidas a propósito: el 25/05/2026 casi la mitad de las vacas tiene el
  total del día no válido, pero la leche está medida (10.112 kg contra ~12.000) y ocultarlo sería esconder un dato real; el 27/07/2026, en cambio, faltan ordeños enteros (8.596 kg contra ~61.000). Es el único
  día que marca la copia local, y es el hueco que ya estaba documentado.
* **Período:** 30, 90, 180, 365 días, «todo» (hasta dos años) o desde-hasta a mano, hasta 731 días. El final es el «Calcular al» del informe (vacío = hoy).
* **Se guarda con el informe** (`grafico` en la definición, validado: lo guardado siempre se puede volver a correr) y no cuenta como parte de la tabla ni la marca «sin guardar» al abrirlo.

**Verificado contra la base real** (copia local, «hoy» = 30/07/2026, 90 días, 120.726 filas crudas de `AnimalDaily`): las tres formas de dividir (rodeo, lactancia, todo) × con y sin la población del informe
(379 animales con `Días en ordeño ≥ 300`) = seis casos, **1.724 celdas día-serie comparadas contra un recálculo independiente en Python sobre las filas crudas: 0 diferencias**, y los kg totales idénticos
(4.747.735,2 y 968.593,8). Cada consulta tarda 0,5 a 1,2 s. La pantalla se probó en el navegador (con 90 animales de ejemplo, porque el SQL local se quedó sin memoria): abrir el gráfico, medida, resumen, intervalo,
forma, leyenda, tooltip, serie, períodos, fechas a mano, rango imposible (mensaje del servidor), guardar y reabrir, informe sin condiciones y el ancho de celular.

**Pruebas:** 292 de informes en Python (motor 86, catálogo 13, fórmula 20, guardados 22, API 38, Excel 51, página 14, serie 46, módulo JavaScript 2) más **29 en Node** del módulo del gráfico (semana que cruza
el fin de año, días rotos que no arrastran un promedio, huecos que cortan la línea, escala del eje…). Se comprobó con mutaciones que cada grupo de pruebas detecta cambios de comportamiento (4 en Python, 7 en JavaScript).

**Límites conocidos:**
* **No se comparó contra un gráfico de DelPro del mismo período**: se verificó que el cálculo coincide con las filas crudas de `AnimalDaily`, no que coincida con lo que muestra DelPro.
* Solo leche. `AnimalDaily` también trae `DIM`, `Duration` y `AvgYieldPrev7d`, pero sin verificar contra DelPro no se ofrecen.
* Con condiciones, los animales que ya salieron del rebaño no figuran (la población es la de hoy).
* El gráfico no se exporta a Excel ni a imagen.
* Si el servidor tarda (SQL Express sin memoria), la pantalla dice «Calculando…» sin límite de tiempo.
* Barras diarias con varias series quedan muy densas: están pensadas para semana o mes.

## Fase 4: estado al 24/11/2026

Tres funciones, hechas de noche y **sin commitear** (esperan el «commiteá» del usuario): **traer los informes que el tambo ya armó en DelPro (4a)**, **armar un informe describiéndolo con una frase (4b)** y
**programar el envío de un informe (4c)**. Las tres respetan la regla de siempre: lo que entra es una definición JSON que pasa por `compilar`; ningún valor llega como SQL. Ninguna llamó a la API de Anthropic,
mandó un mensaje ni escribió en una base: las pruebas usan un cliente de IA y canales de mentira.

### Un operador nuevo: «es uno de»

La condición más común de los informes de DelPro («rodeo 1, 2 o 3») y de lo que se pide en palabras no se podía expresar. El motor suma el operador `en` (números y textos), con una lista de hasta 20 valores:
**cada valor se valida como el de un «=» o un «es»**, se sacan los repetidos y nada llega crudo a la consulta. `CONDICIONES_MAX` subió de 5 a 10 (un «entre» de DelPro son dos condiciones). En la pantalla se escribe separado por
comas y viaja como lista. `describir_condiciones(defn, con_dia=True)` escribe las fechas con el día de la semana.

### 4a. Traer los informes de DelPro

DelPro guarda su propio diseñador de informes dentro de DDM, así que se **lee** (cuatro SELECT fijos, guardados 10 minutos en memoria, nunca se escribe): `DDMReport` (con `XPObjectType.TypeName`, porque el `ObjectType` 16/19 es un OID que
cambia de una base a otra), `DDMReportColumn` → `DDMReportItemInstance` → `DDMReportItem` (el `Name` del campo es el id del catálogo de LactIA), `Filter` y `DDMReportClass`. Comprobado contra la base real: el `CustomFilter` del informe
llama a cada columna `C` + el OID de la columna (`[C2511]`), y los códigos de resumen son los `SummaryItemType` de DevExpress (0 suma, 1 mínimo, 2 máximo, 3 conteo, 4 promedio, 5/6 ninguno; el conteo cae en `Number` y el promedio en los días
en ordeño y las producciones). Los filtros están escritos en el lenguaje de criterios de DevExpress, que no es SQL: `informes_delpro.py` trae **un lector propio** (sin `eval`) y traduce solo una conjunción («y») de comparaciones simples,
«es uno de», «entre» y rangos de fechas sobre campos del catálogo. **Lo que no se puede traer no se adivina**: cada término queda dicho, con el motivo, y el informe pasa a **«parcial»**. Un filtro que no se trae cambia QUÉ animales
aparecen (el informe puede mostrar más), así que se avisa con otra gravedad («contenido») que una columna que falta («columnas») o un orden que se ve distinto («presentación»). Cada condición traducida se vuelve a validar con `compilar`: un valor
con `;`, `--` o `/*` se descarta y se avisa.

* **Solo informes de animales** (`BasicAnimal` y `AnimalExtended`); los de ordeño por sesión, grupo, tratamientos, etc. figuran como «no se puede traer» con su motivo.
* **Estados:** `completo` (sin diferencias de contenido ni de columnas), `parcial`, `no_disponible`. La lista los ordena (completos primero, los del tambo antes que los de fábrica) y marca «filtro sin traer».
* **Pantalla:** «Traer de DelPro…» en la lista abre un cuadro con búsqueda, el estado de cada informe y los campos de DelPro que más faltan; «Traer» abre el informe como **uno nuevo, sin guardar**, con un aviso arriba (qué cambia qué animales
  aparecen, qué falta, qué se ve distinto). API (admin): `GET /api/informes/delpro` y `GET /api/informes/delpro/<oid>`.
* **Cobertura real, medida sobre el volcado de la copia local** (con el catálogo de 10 campos, ANTES de la tanda 1: ver «Tanda 1 de campos de DelPro»). De los 65 informes del tambo que lista el cuadro (sin los predefinidos de fábrica) **1 sale «completo», 56 «parcial» (casi
  siempre por un filtro que no se trae) y 8 no se pueden traer**. Mirando solo las columnas, 4 de los 58 informes de animales caben enteros en el catálogo. Los campos que más faltan, **contados en INFORMES distintos** (no en columnas: una
  columna repetida cuenta una vez): `InMilk` (19), `ToBeCulled` (15), `NumberOfInseminations` (11), `DaysToCalving` (9), `DiagnosisFromDiagnosisTreatmentEvent` (9), `ExpectedCalvingDate` (8), `IsPregnant` (8), `IsDryingOff` (8),
  `YieldYesterdayForAllMilkingInSession` (6) y `CMSTakeOffFlowYesterday` (3). (La primera versión de este párrafo contaba 16 y 9 para los dos últimos: eran apariciones de la columna, con repeticiones dentro del mismo informe.)
  El volcado no trae el OID de cada columna, así que el filtro de la grilla no se pudo leer con él: con la base real el cuadro puede dar otras cifras. **Ampliar el catálogo (cada campo con su verificación contra DelPro) es lo que más hace
  crecer el valor del importador.**

**Revisión independiente de las tres funciones (la noche del 24/11/2026).** Cuatro miradas por función (traductor, seguridad, semántica de lo que se importa, uso de la pantalla, y regresión contra la versión commiteada) y UN
verificador adversarial por hallazgo, que intentó refutarlo con una reproducción propia. De los hallazgos de la 4a se confirmaron casi todos (los de pantalla, 15 de 15) y se **refutaron 5**; los de la 4b (9 de 9) y la 4c (todos menos 1) se
confirmaron. Todo lo confirmado se arregló con una prueba que primero falló (`tests/test_informes_delpro_revision.py`, `test_informes_lenguaje_revision.py`, `test_informes_envios_revision.py` y ampliaciones). Lo más importante:
* **Un filtro de DelPro raro tumbaba toda la lista** (un `1e999`, un entero de 400 cifras, 1.000 sumas o dígitos no ASCII lanzaban una excepción sin atrapar: HTTP 500 para todos los informes). Ahora nada de lo que viene de DelPro levanta:
  el lector devuelve «no se pudo leer», `listar` aísla cada informe y los endpoints contestan 502 sin detalles. El motor rechaza números enormes, `inf` y `nan`.
* **70 de los 133 filtros de servidor reales se escriben sin corchetes** (`ReproductionInfo.LactationNumber > 0 AND ...`): el lector los entiende, y un término ilegible ya no descarta a los demás.
* **El filtro de la grilla de DelPro se evalúa EN MEMORIA, donde un animal sin dato pasa un «menor que»**; SQL lo deja afuera, así que traerlo tal cual mostraría menos animales. Solo se trae si el mismo campo pide «con dato»
  (DelPro mismo escribe `< 18 And Is Not Null`). *Un verificador lo marcó como no demostrable con datos reales; se dejó la opción prudente (avisar antes que traer algo distinto).*
* **«Todos los rebaños» (`Herd = 0`) es el caso habitual (26 de los 58 informes del tambo)** y mostrar solo los animales de este tambo es lo que se quiere: es un aviso informativo y no vuelve «parcial» al informe. Un informe de otro
  rebaño de la base no se trae. Los informes ocultos no se listan; las reglas de color (8 de 66) se avisan.
* **Dos defectos del motor de las Fases 0 a 3 que salieron de acá:** el orden por «Número del animal» era alfabético («10» antes que «2»: 45 de los 58 informes salen ordenados por ese campo) y «incluir descartados» **reemplazaba** el
  filtro de los animales de hoy por el del historial en vez de sumarse (mostraba MENOS animales al marcarlo). Ahora el Número se ordena como número (`Campo.orden_expr`) y los descartados son una unión
  (desde el 27/11/2026 es `rebano.filtro_del_animal`: la unión sumaba animales vivos de otros rebaños; ver CLAUDE.md).
* **4b:** el límite de filas que arma la IA («las 10 vacas que más producen») se perdía en la pantalla (Actualizar, Guardar, Exportar y el envío programado lo ignoraban): ahora viaja y hay un control «Listar hasta … filas». Las fechas
  que arma la IA son fijas (un informe «últimos 30 días» programado repite el mismo período): se avisa en el aviso del informe y en el cuadro de programar. El aviso «este número no figura en lo que escribiste» saltaba en las
  traducciones correctas (el `> 0` de «en ordeño», «primera lactancia», «doscientos», «30.5»): ahora lee ordinales, números en palabras y punto o coma decimal, y además avisa lo que la IA se olvidó (un número de la frase que no llegó
  al informe). Un campo de texto cerrado (el estado de la reproducción) le muestra sus valores a la IA y se le rechaza otro. Sin reintentos automáticos del SDK (un pedido colgado esperaba 3 × 45 s).
* **4c: un fallo después de que el canal aceptó el mensaje se reintentaba y lo duplicaba** (3 mensajes por un horario; reproducido con los módulos reales de WhatsApp, Telegram y correo). Ahora cada error de canal dice si se SABE que no salió
  (no pudo ni conectar) y solo eso se reintenta; lo incierto queda «sin confirmar» y no se reenvía solo. Además: WhatsApp ya no recibe un texto que promete un Excel adjunto; el pie de Telegram (1.024 letras) conserva el total y los
  avisos y recorta la lista de grupos; pausar mientras la pasada trabaja ahora frena el envío; cada decisión mira el reloj de ese momento; pausar y guardar no se pisan; un Excel con caracteres de control no tumba la exportación.

### 4b. Describí el informe

`informes_lenguaje.py` + `POST /api/informes/describir` + un cuadro en la lista. El administrador escribe «vacas del rodeo 3 con más de 200 días en ordeño, ordenadas por producción» y Claude (el mismo del agente) devuelve la **definición**
con un esquema JSON cerrado (los ids del catálogo como enumeración). **La IA no decide nada de seguridad:** la definición pasa por `informes_guardados.validar_definicion` (o sea `compilar`); un campo inventado, un valor con SQL o una clave de más
se rechazan y se le devuelve el motivo UNA vez. El prompt y el esquema se generan desde el catálogo y **no llevan SQL, notas internas ni datos del tambo**.

* **Una llamada (más una repetición si la definición no pasó la validación)**, con tiempo límite y un **tope de 20 pedidos por hora**. Sin clave de la API (503), con la IA caída o lenta (502, sin detalles) o con una respuesta rara, el diseñador manual sigue andando igual.
* **Lo que se muestra lo arma el servidor** desde la definición ya validada («Se lee así»), no el texto del modelo; lo que escribe el modelo (supuestos, lo que no pudo) va acotado y limpio, y se sacan las líneas con «ayer» o «ese día».
  Se **avisa** (no se rechaza) cuando hay campos sin verificar, un número que no figura en lo escrito, un informe sin condiciones (trae todos los animales del rebaño) o un límite contra totales.
* El informe se abre **sin guardar y sin ejecutarse solo**: el usuario revisa lo que se entendió y recién ahí toca «Actualizar».
* **No probado con el modelo real** (no se llamó a la API): los tests usan un cliente de mentira. La primera prueba real hay que hacerla en SERVER-DELPRO, con la clave cargada, con frases de verdad.

### 4c. Programar el envío

`informes_envios.py`. Un informe **guardado** se programa para mandarse por correo, Telegram o WhatsApp en los días y horas que se elijan (hasta 5 horarios por día, 10 programaciones activas por tambo), con un resumen en texto y, por
correo y Telegram, el **Excel adjunto** (WhatsApp con Twilio lleva solo el resumen: 1.600 letras de tope, sin archivos). Se manda **la versión guardada**, calculada en el momento del envío con la fecha de ese día (igual que la pantalla con
«Calcular al» vacío). Los destinatarios son los de siempre (uno fijo por canal, por variable de entorno): no se agrega ningún destinatario ni dato personal.

* **Nunca se manda dos veces lo mismo.** Cada (programación, horario, canal) se **reclama** en `informes.db` (`UNIQUE`) antes de mandar: un reinicio, dos pasadas seguidas o dos procesos no duplican. Lo que quedó a medias (el proceso murió) pasa a
  «sin confirmar» a los 30 minutos y **no se reenvía solo**. Un canal que falla no cancela a los otros. **Solo se reintenta lo que se SABE que no salió** (no se pudo conectar: `envio_red.py`; hasta 3 veces con 10 minutos de espera, siempre dentro de
  la hora de gracia) y un error de base; un fallo después de que el canal aceptó el mensaje (un tiempo de lectura, una conexión cortada) queda «sin confirmar» y NO se reenvía; un rechazo del canal (credenciales) y un informe que ya no compila, no se reintentan.
* **Nada sale tarde ni retroactivo:** lo que venció hace más de una hora (el servidor estuvo apagado) no se manda, y nada sale por un horario anterior al momento en que se guardó la programación.
* **Ningún error guarda ni muestra el texto de una excepción** (puede traer el token de Telegram o un teléfono): se guarda una frase fija por canal.
* **No hay «borrar»:** se pausa. «Ver cómo queda» arma el resumen sin mandar nada; «Enviar ahora» manda de verdad, pide un segundo toque y **no anota nada** (no cuenta, anda con la programación pausada).
* El hilo (`envios-informes`) lo arranca `servidor.py` **después del candado de instancia única**, nunca al importar `app`; cada minuto lee `informes.db` y recién cuando un horario vence calcula el informe contra la base del tambo.
* `correo.enviar_con_adjunto` y `telegram_bot.enviar_documento` son nuevos (el pie de un archivo en Telegram se corta en 1.024 letras: lo importante va antes que la lista de filas).

**Pruebas nuevas:** `tests/test_informes_delpro.py`, `test_informes_delpro_api.py`, `test_informes_lenguaje.py`, `test_informes_lenguaje_api.py`, `test_informes_envios.py`, `test_informes_envios_api.py`, `test_envio_adjuntos.py`, más ampliaciones del motor, los guardados
y la página. De las dos revisiones: `test_informes_delpro_revision.py`, `test_informes_lenguaje_revision.py`, `test_informes_envios_revision.py`, `test_informes_envios_final.py`, `test_informes_bordes.py` (casos límite de los tres módulos) y
`test_informes_origen.py` (la nota de origen); y en Node, `tests/js/informes_pantalla.test.js` (con `mini_dom.js` y `pantalla_informes.js`), lanzado por `TestPantallaEjecutada` de `test_informes_pagina.py`.

**Límites conocidos:**
* Cobertura baja del catálogo (10 campos): la mayoría de los informes de DelPro se traen «parcial» y la IA va a contestar «no se puede» seguido.
* Lo importado de DelPro hereda los ⚠ de los campos sin verificar (estado de la reproducción, pico de la última lactancia): la Fase 0 sigue sin aceptarse.
* El informe programado usa la fecha del día del envío; DelPro consolida con atraso y los últimos días pueden faltar: el mensaje dice cuándo se calculó, no hasta qué día llegan los datos.
* Las fechas de las condiciones son fijas (el motor no tiene «hoy menos 30 días»): un informe «parieron en los últimos 30 días» guardado y programado mira siempre el mismo período. Se avisa, pero no se resuelve (ver abajo).
* El filtro de DelPro con un «o», una función (`Milkings[...].Exists()`) o un valor especial no se trae: el informe queda «parcial» y muestra más animales que en DelPro.
* **No se pudo probar contra la base real en esta sesión**: el servicio de SQL Server de la PC de desarrollo (`MSSQL$DELPRO`) estaba detenido (había estado sin memoria durante horas). Las cuatro consultas fijas del importador (que cambiaron
  en dos columnas: `IsHidden` y `ConColores`) solo se probaron con la validación de solo lectura de la app y contra el volcado que se había hecho antes de que la base dejara de contestar.

## Revisión final de la Fase 4 (24/11/2026)

Segunda revisión independiente, **después** del commit de la Fase 4 (`2caa4b5`): siete miradas (IA, regresión, seguridad, pantalla, envíos, importador, completitud) y un verificador adversarial por hallazgo. **59 hallazgos: 53 confirmados y 6
refutados.** Los 53 se arreglaron, cada uno con una prueba que primero falló. Lo que cambia el comportamiento, por área:

* **Motor.** Una condición de fecha se compara **por día** (`CAST(campo AS date)`): `LastCalvingDate` es `MAX(DateAndTime)` y, si trae hora, `=`, `<=` y `>` contra un `date` comparaban contra las 00:00 («parieron hasta el 15» dejaba afuera a las del 15).
  *No se pudo medir si `DateAndTime` de un parto trae hora; el cambio es inocuo si no la trae.* `ejecutar()` devuelve `limite_pedido`: cuando el corte lo eligió el informe («las 10 vacas que más producen») el Excel y el mensaje dicen «se listan las primeras N filas,
  como pide el informe» y no «se cortó en el tope». `describir_condiciones` agrega esa línea solo con `con_limite=True` (la IA ya escribe la suya en «Se lee así»).
* **Importador.** Los resúmenes «de grupo» se ignoran si el informe no tiene ninguna columna agrupada (el 0 es el valor por defecto: 1.450 de 2.244 columnas de informes sin grupos); «incluir animales descartados y salidos» ahora es una diferencia `contenido`
  (decía que acá solo se ven los que alguna vez ordeñaron, lo que el 27/11/2026 resultó falso: ver CLAUDE.md); un filtro de texto sobre un campo cerrado con un valor que no es de la lista (un código de DelPro) no se trae; un campo con otra condición que excluye nulos «cubre» al
  «menor que» (un rango de dos comparaciones ya no pierde la mitad); los avisos dicen «En ordeño (InMilk)» y no el código de columna; el tope de 200 cuentas es de TODO el filtro (un filtro de 3,8 KB con sumas anidadas daba `RecursionError` y el informe quedaba «no se pudo leer»).
* **«Describí el informe».** Nuevos avisos (todos con texto fijo, ninguno cambia el informe): una fecha de condición que no sale de lo escrito (con el día de la semana) o un operador que contradice las palabras («desde» con «<»); un número escrito con palabras o un
  valor cerrado («preñadas») que la frase pide y el informe no tiene. Se corrigieron los falsos avisos del lector de números (el 7 de «últimos 7 días» y del campo «… 7 d.», «rodeos 3,4», «rodeo 2/3», ordinales hasta décimo, «la vaca» con límite 1). El nombre del informe no puede decir
  «últimos 30 días», «mes pasado»… (las fechas son fijas). **Un pedido que falla antes de que la API acepte el trabajo (sin red, 429, 5xx) devuelve su turno del cupo de 20 por hora**; el mensaje del tope dice en cuántos minutos se libera uno. El prompt ya no trae un nombre
  de rodeo real. `max_retries` sigue en 0 (con 1, un pedido colgado esperaría hasta 180 s, más que el corte de ~100 s del túnel).
* **Envíos.** Antes de CADA envío se vuelve a mirar la programación (pausar o cambiar mientras se calcula frena lo que falta); **un horario perdido por el servidor apagado deja una fila «no salió»** en el registro; el texto «Se reintenta.» no queda escrito si
  el reintento ya no ocurre; un canal destildado mientras se calcula es «fallido» (no «sin confirmar»); «Ver cómo queda» y «Enviar ahora» muestran el texto de CADA canal (`textos`) y respetan el formato del cuadro (`formato`); lo fijo del mensaje (total, «Ojo»,
  «va adjunto») nunca se recorta; WhatsApp «undelivered» se anota con una frase fija (ventana de 24 horas, sandbox, no entregado) y un 429 de Telegram se reintenta; el Excel lleva el día de la semana en «Generado» y en las fechas de las condiciones; los redondeos
  (mitad hacia arriba) coinciden con los de la pantalla; un horario con salto de línea o dígitos de otro alfabeto se rechaza; dos «Guardar» a la vez no dan 500; un id de informe imposible da 404; el «latido» del hilo se escribe durante la pasada. **`backup_config.bat`
  copia `informes.db` (y su `-wal`/`-shm`)**: sin eso los informes, las programaciones y el registro que evita duplicados no tenían ningún respaldo.
* **Pantalla.** «Es uno de» sobre un campo calculado separa por «;»; en un campo entero separa también por coma; una respuesta tardía (guardar la programación, el registro, traer de DelPro) no pinta ni escribe el cuadro de OTRO informe; un doble clic en «Pausar» no se
  deshace solo; «Ver cómo queda» no dice «Mandando…»; el foco del teclado no se pierde; el aviso de fechas fijas mira lo GUARDADO; la barra de botones vuelve a una línea a 1280 px; las pruebas de la pantalla ahora **ejecutan** el JavaScript de la plantilla en Node con un DOM mínimo
  (`tests/js/mini_dom.js`, `pantalla_informes.js`, `informes_pantalla.test.js`), no solo buscan texto en el código.
* **El aviso de un informe traído de DelPro sobrevive al guardado.** Antes vivía solo en la pestaña: se guardaba el informe, se reabría y ya no decía nada, y se podía programar su envío con otra cantidad de animales que la de DelPro sin ninguna marca. Ahora el informe guardado
  lleva una **«nota de origen»** (`informes.origen`, columna nueva que se agrega sola; fuera de la definición): las diferencias que cambian QUÉ animales aparecen. Se ve al reabrir (una franja con «Ver el aviso»), en la lista («⚠ traído de DelPro»), en «Programar envío»
  y como **una línea fija en cada mensaje programado** (`informes_envios.AVISO_ORIGEN`, de lo que nunca se recorta). Se quita a mano con «Ya lo revisé» (`PUT {origen: null}`): corregir el informe no la borra sola.

**Refutados (6), y por qué:** [ia #2] el «o» entre campos distintos: lo que falla es la respuesta del modelo, que solo se puede medir con el modelo real; [seguridad #1] listar el nombre de informes de otro rebaño de la base compartida es una decisión documentada y no cruza ningún acceso;
[importador #4], [importador #6] y [completitud #7] leer `Group`/`Selection`/`DateFilter` del informe de DelPro y si una fecha trae hora: nadie midió lo que significan `-1` y `2` (en el volcado, `Group` vale 0 y `DateFilter` es NULL en los 58 informes de animales) y SQL Server no se
puede arrancar acá; [completitud #9] «`pausar()` no se usa en producción»: la suite igual falla si `guardar` deja de pausar.

**Lo que NO se hizo, a propósito:** (1) ~~la pantalla es solo oscura~~ (resuelto el 24/11/2026: tiene tema claro y se imprime, ver «Tema claro e impresión» abajo). (2) `backup_config.bat` copia los
archivos sueltos (como con `checklist.db`): con el hilo de envíos escribiendo no es una instantánea perfecta; la API de respaldo de SQLite lo sería. (3) En el importador, la regla del «menor que» depende del orden en que se leen las fuentes: dos columnas del mismo campo con filtro propio
se ven una a la otra solo en un sentido. (4) `UN-C<oid>` se traduce igual que `C<oid>` (no se sabe qué significa «UN-»). (5) Los avisos de fechas de la IA avisan de menos, nunca de más: «15 ago» (mes abreviado) y «quincena» no se leen como fecha.

## Tanda 1 de campos de DelPro (24/11/2026)

La primera prueba en SERVER-DELPRO mostró que las 4 consultas del importador andan contra el SQL real y que, de 181 informes, solo 1 salía completo y 1 «casi igual»: el catálogo de 10 campos era el límite. La **tanda 1 suma 15 campos (25 en total)**, elegidos por lo que más piden los informes de
DelPro (`InMilk`, `ToBeCulled`, `IsPregnant`, `IsDryingOff`, `BirthDate`, `ExitDate`, `AgeInMonths`, `LastInseminationDate`, `LastAbortionDate`, `DaysSinceLastInsemination`, `NumberOfInseminations`, `ExpectedCalvingDate`, `DaysToCalving`, `ExpectedDryOffDate`, `DaysToDryOff`).

* **DelPro no guarda la fórmula de sus campos** (en `DDMReportItem` el `Criteria` está vacío; en la base solo hay 72 funciones y ninguna es de estas). Cada campo se definió contra las tablas de DDM y se verificó contra lo que DelPro **sí** guarda: el índice de últimos eventos por animal, el resumen de lactancia y las
  columnas de la reproducción. Lo que sale de una cuenta nuestra sin un valor de DelPro con que compararlo queda con ⚠ («sin verificar»).
* **Los «sí/no» son texto con lista cerrada («No», «Sí»)**, no un tipo nuevo: se agrupan, cuentan, exportan y se mandan sin tocar el motor, el Excel ni los mensajes. Se eligen de un desplegable. El importador traduce `= True`, `= False`, `<> True` y `<> False`.
* **Medido en la copia local (30/07/2026):** `InMilk` coincide con la leche medida (1.636 de las 1.679 en ordeño dieron leche el 29/07; ninguna de las 3.479 fuera de ordeño); el índice de última inseminación se reinicia con cada parto (165 de 165); `NumberOfInseminations` coincide con contar eventos en
  433 de 433; `LastAbortionDate` en 91 de 91; 820 preñadas, 817 con inseminación efectiva de este ciclo y 3 de hace más de 320 días (datos mal cargados, se muestran como son).
* **Efecto sobre el importador (volcado):** los informes de animales con TODAS sus columnas en el catálogo pasan de 4 a 9 de 59. La mejora grande se espera en los filtros: casi todos los de DelPro hablan de estos campos.
* **Quedó afuera a propósito:** la leche «de ayer» y «de hoy» (nombre relativo: un informe guardado se lee días después). **Tanda 2** (en cuántos informes): diagnóstico 9, `LastCutIdTime` 8, leche de ayer/hoy 6, `CMSTransponderID` 6, promedios de 14/21/30 días, `LatestSCC` 5, `IsBred` 4.

## Tanda 2 de campos y el costo en la base (24/11/2026)

**Costo.** Se midió con la base real (`SET STATISTICS IO`, muestra de 600 animales o el informe completo de 2.000 filas) el SQL que genera el motor para cada grupo de campos. Un campo del catálogo **no cuesta nada si el informe no lo usa**: la consulta une solo los bloques que las columnas pedidas necesitan. Lo
que cuesta cuando se usa: el núcleo del animal, 315 lecturas de página; los bloques por clave (reproducción, índice de últimos eventos, resumen de lactancia, inseminación efectiva), 400 a 7.000 lecturas y menos de 100 ms de CPU; el promedio de 7 días, 3.800 lecturas. **Lo único pesado era la «Fecha del último parto»** (agrupaba
todos los eventos de parto de la base: en un SQL Express con poca memoria quedaba esperando el permiso de memoria por minutos); ahora sale del índice de últimos eventos de DelPro (coincide en 1.772 de 1.772 animales) y es un join por clave. Las cifras son de una PC con SQL Server casi sin RAM; en SERVER-DELPRO no se midieron.
Los otros dos campos candidatos recorren decenas de páginas de eventos por animal (el último «corte» de la puerta de separación, 80 por animal; el último diagnóstico, 340) y quedaron para una tanda 3 que necesita un índice, un tope de fecha o una copia diaria.
**Regla de diseño:** nunca agrupar una tabla de eventos entera; el «último» de algo se busca por animal (puntero del índice de DelPro o `OUTER APPLY TOP 1`).

**Leche «de ayer» y «de hoy» (35 campos).** Decisión del usuario: se llaman así, como en DelPro (relativas a «Calcular al»); es una excepción pedida por el dueño a la regla de las fechas concretas. **Elegir y traer varios informes de DelPro:** el cliente es quien usa esos informes, así que el cuadro «Traer de DelPro…» permite elegir los suyos
y traerlos juntos (se guardan con su nota de origen, sin ejecutar nada) y recalcula qué campos faltan SOLO para los elegidos: así se priorizan los campos que el cliente de verdad necesita (tanda 3).

**Archivar, restaurar y eliminar (24/11/2026).** Al traer varios informes de DelPro de una vez la lista puede llenarse de informes que no se usan, y LactIA no tenía «eliminar» (regla de no borrar). Ahora: **«Archivar»** saca el informe de la lista, se puede restaurar y **pausa su envío programado**; un archivado no se edita, no se programa y no manda nada.
**«Eliminar para siempre»** solo existe para los archivados y pide dos toques sobre el mismo informe (la confirmación vence sola); borra el informe, su programación y el registro de lo mandado. El nombre de un archivado sigue ocupado. Pendiente si hace falta: archivar varios a la vez.

**Tanda 2 (33 campos en total):** `EffectiveInseminationDate`, `LastPregnancyCheckDate`, `LastCullDecisionDate` (punteros del índice de DelPro), `DaysPregnant`, `LatestSCC` (último control lechero, en miles/ml) y los promedios de 14, 21 y 30 días. Los promedios y los días son cuentas nuestras (⚠ hasta compararlos con un informe de DelPro).

**Tanda 3a (50 campos en total, 24/11/2026).** Pedido del usuario: traer todos los campos que se pueden definir de una vez, porque la consulta solo se hace al pedir el listado (un campo que el informe no usa no se une). Entran 15 campos baratos, todos ⚠: el último valor de **cada parámetro del
control lechero** con su propia fecha (recuento, grasa, proteína, rendimiento: «el último control que lo trae», porque un control puede traer solo el rendimiento), edad en días, **sexo** (texto cerrado Hembra/Macho), **cambio de rodeo** (días desde el último y rodeo anterior, del índice de DelPro), **fecha del parto anterior**
(inicio de la lactancia anterior) y **condición corporal de la cámara** (último valor, al secado, al parto y a los 190 días de preñez). La cámara solo existe en algunos tambos: esos campos llevan `Campo.tabla = "BcsDailyData"`, no se ofrecen donde la tabla no está y un informe que ya los usa recibe un error claro en vez de un fallo de la base.
Un informe admite hasta 40 columnas y el catálogo tiene 50 campos. Lo que había quedado pendiente en esta tanda (los campos pesados y los de significado desconocido) se resolvió en la tanda 3b, abajo, y por otro camino que el que se había pensado: ni copia diaria ni exportación de DelPro.

**Tanda 3b (78 campos en total, 24/11/2026).** Dos hallazgos: **(1)** DelPro SÍ guarda la fórmula de cada campo (`DDMReportItem.Expression`, lenguaje de criterios de DevExpress); con ella se definieron los campos «de significado desconocido» (inseminada y no preñada, número del transponder, edad al primer parto,
resultado del último diagnóstico de gestación, ordeños de ayer) y se **corrigieron campos de las tandas anteriores** que se habían definido sin leerla: el secado esperado solo vale para una vaca que ya parió y no se secó, el parto esperado pide un animal que no salió, el último diagnóstico de gestación queda vacío si hay un
aborto posterior, la edad en meses es la edad en días sobre 30,5 con un decimal. **(2)** La «copia diaria en `informes.db`» que se había planeado para los campos pesados **no hace falta**: leyendo cada tabla de eventos UNA vez (un agregado por animal y los datos del último evento por clave) son 3.400 páginas y 0,3 s para 2.000 vacas, contra
los ~630.000 de buscar el último de cada una. Entran la puerta de separación (3 campos), el último diagnóstico y tratamiento (10), la última vacunación y el último protocolo de IA a tiempo fijo (3 y 3, sin probar con datos: La Ponderosa no tiene esos eventos), el último evento de salud y los ordeños de ayer. Los campos con parámetro de sesión
(`YieldYesterdayForAllMilkingInSession`, `CMSTakeOffFlowYesterday`) no se traían (esta frase era falsa: DelPro sí guarda el parámetro de cada columna, ver la tanda 4). **Costo de memoria:** el SQL de la PC de desarrollo tiene 7 MB de espacio de trabajo y una consulta que pide más de ~1,75 MB de mínimo espera minutos; por eso los bloques se escriben sin funciones de ventana, sin agregados de texto y
con búsqueda por clave (ver `CLAUDE.md`). Nuevo `Campo.equivale` para que el importador entienda `Sex = '2'` y `LatestPregCheckResult In ('Uncertain', 'Negative')`.

**Tanda 4 (25/11/2026, 78 a 165 campos).** Pedido del usuario: la lista «Campos que faltan para los 61 elegidos» de «Traer de DelPro…» (129 campos distintos; los 30 primeros se ven en pantalla). **Hallazgo central:** DelPro SÍ guarda el parámetro de cada columna (el número de ordeño, la sesión de la sala, los días en ordeño, el desfase de días), como una fila `ReportItemParameter` de `AbstractSetting` que apunta a la columna; sin eso, las tres columnas «Flujo de retiro de ayer» de un informe eran indistinguibles. Un campo con parámetro son, acá, varios campos (uno por valor: `CMSTakeOffFlowYesterday_2`), con `Campo.item` y `Campo.parametros`, y el importador lee los parámetros en una quinta lectura aparte (si falla, todo sigue como antes) y elige la variante de cada columna.
Entran 87 campos, todos ⚠: ya salió del tambo, posiblemente en el rodeo equivocado (una lectura de los datos: DelPro no guarda el resultado), rodeo y días del último ordeño, última identificación, control de gestación y secado por hacer (con los ajustes de aviso de DelPro), leche de ayer como suma de ordeños, ordeños de hoy, duración y ordeños por día de los últimos 7 días, leche acumulada y del día 305, promedio de hace 30 días, 12 de la cámara de condición corporal (ayer, tendencias, a los 7, 30, 50, 90 y 200 días del parto, y tres marcas más), 10 de la puerta de separación (último corte confirmado: área, puerta, motivo, regla; horas y minutos desde el último corte; separar una vez y siempre), **42 por número de ordeño** (7 medidas × ayer y hoy × ordeños 1 a 3) y **6 por sesión** de la sala. El número de ordeño (el nº N de esa vaca ese día) y la sesión (1 a 3) son cosas distintas: una vaca que faltó a la sesión 2 tiene su 2.º ordeño en la sesión 3. Quedan sin definir `CMSSort_Iso` y `CMSSortTodayOrLater` (dependen de una propiedad que DelPro calcula en su programa y no guarda). **Resultado:** de los 61 informes del tambo que se pueden traer, 13 salen completos (antes 3) y los campos distintos que faltan bajan de 129 a 75. Las horas van como texto `hh:mm:ss` (la pantalla muestra de un campo fecha solo el día) y las duraciones en segundos.

**Tanda 5 (25/11/2026, 165 a 258 campos).** Pedido del usuario: «seguí con los 75 campos que faltan». De los 75 nombres de DelPro, 65 quedan definidos (93 campos del catálogo; cuatro nombres son alias de campos que ya estaban) y 10 se dejan afuera a propósito: unas fechas fijas dentro de la columna, dos cálculos de la curva de flujo que DelPro no guarda, una fórmula que depende de ajustes de la finca y de banderas que no se guardan, dos de alimentación sin datos en la base, y **cuatro columnas que el cliente armó él mismo en DelPro** (reglas «si… entonces…» propias, no campos de DelPro: traerlas pide un lenguaje de fórmulas con condiciones). **Resultado:** de los 61 informes del tambo que se pueden traer, 22 salen completos (antes 13) y los campos de DelPro que faltan bajan de 75 a 10.
Entran: la finca y el rebaño del animal; más reproducción (días desde el último parto, celo y diagnóstico; el último celo; el número, el semen y el usuario de la última inseminación; el número del próximo diagnóstico de gestación; los días en seca, abiertos y de preñez de cada lactancia); salud (la condición corporal cargada a mano, el último evento de salud de cinco tipos, el descarte de leche que falta, los chequeos de salud, las listas de animales); ordeño (flujo por tramo, flujo bajo y puesto por número de ordeño; lo de todos los ordeños de ayer; las ventanas de 24 horas; la pendiente de la leche de 7 días; el pico y la leche de la lactancia y de toda la vida; «no ordeñar»); y la separación manual con sus dos reglas por animal.
**Tres hallazgos:** (1) un mismo nombre existe en varias clases de DelPro (`Name`, `DIM`, `LactationNumber`, `GroupNumber`), así que el importador ahora lee la clase de cada ítem y solo trata como alias a los que de verdad son otro campo del catálogo; (2) `CurrentDIM` no seguía la fórmula de DelPro (una vaca seca cuenta hasta su secado, un animal que salió hasta su salida): corregido, y las vacas secas coinciden 60 de 60 con el día de secado que DelPro guarda; (3) **tres bloques de la tanda 3b y 4 unían las lactancias del historial con el OID del animal, que no es el de su historial en 2.344 de 11.548 casos: leían la lactancia de otro animal.** Se corrigieron (hay un bloque `historia`); el mismo error estaba en el tablero de días abiertos y en `reproduccion.py`, fuera de los informes, y se corrigió el mismo día.
**Cómo se verificó:** cada campo contra una consulta cruda independiente, y varios contra las **funciones que DelPro tiene en la base** (la pendiente de la leche, el kilaje de la última visita, la leche de la lactancia, las listas de animales, y las reglas de separación: 19 de 19 y 24 de 24 con las funciones `dbo.IsForeverCowsToBeSortedToday` y `dbo.StartTimeCowsToBeSortedToday`); los días abiertos y de preñez de cada lactancia coinciden 21 de 21 y 26 de 26 con el resumen que DelPro guarda. Todos siguen marcados ⚠ (ninguno se comparó con un informe de DelPro). Los que usan el reloj de la PC (el `Now()` de DelPro) salen vacíos en una copia vieja de la base. La separación manual se supone «vigente» si la regla tiene alguna fecha y su fin no venció, como hace la función de DelPro.

**Tanda 6 (28/11/2026): campos calculados de tres tipos, y 270 campos.** Pedido: «podremos buscar esos campos» (los 10 que faltaban) y «hacelo». DelPro tiene en la base fórmulas que la tanda 5 no había visto. Entran: (1) **la leche de los primeros 2 minutos** de cada ordeño, en kg y en % (12 campos), con la fórmula de DelPro sobre los flujos de la curva; (2) **las columnas que el tambo armó en DelPro** (reglas «si… entonces…»), como un tipo nuevo de campo calculado: se copian al traer el informe, se pueden editar, filtrar, ordenar y agrupar, y solo producen texto; la fórmula se lee con el mismo analizador de los filtros de DelPro y de ahí solo sale SQL armado con campos del catálogo y valores validados (nunca el texto del usuario); (3) **el promedio de leche entre dos fechas**, otro tipo de campo calculado, con las fechas de la columna de DelPro y editable en la pantalla.
Las cuentas calculadas de antes siguen igual. **Quedan 3 sin traer:** inseminación pendiente por aborto o vacía (dos marcas que DelPro no guarda: pendiente de comparar con una exportación) y los dos de alimento concentrado (sin consumos en ninguna base).
**Cómo se verificó:** las reglas, contra un evaluador escrito aparte, en SQLite con miles de combinaciones con vacíos y sobre datos reales (86 de 86); los primeros 2 minutos (240 de 240) y el promedio (20 de 20) contra consultas crudas; SQL Server compila los 5 informes que los usan. De los 61 informes del tambo salen completos 33 (antes 31). No se comparó con un informe exportado de DelPro, ni se corrieron esos informes sobre todo el rodeo en la PC de desarrollo (poca memoria).

**Tema claro e impresión (24/11/2026; el tema, cambiado el 26/11/2026).** La pantalla no elige el tema: lo toma del botón del encabezado de la app, el único lugar donde se elige (clave `lactia-tema`; el evento `storage` la actualiza al instante). El botón propio que tuvo entre el 24 y el 26/11/2026 se sacó. Todo color sale de variables definidas para los dos temas. **Imprimir** (botón o Ctrl+P): hoja A4 apaisada con número de página, sin controles, con un
encabezado (nombre, tambo, cuándo se calculó, condiciones, agrupación, orden y los avisos que importan en papel), el título de la tabla repetido en cada página y los totales una sola vez; siempre en claro, y vuelve al tema que había. Se comprobó imprimiendo a PDF con Edge; esa prueba encontró que un botón dentro del título repetido sale sin texto desde la página 2 (se resolvió).

## Pendientes de la Fase 4: lo que decide el tambo o falta probar

1. **Probar en SERVER-DELPRO (en este orden):** (a) `git pull` y `actualizar.bat` **como Administrador**; verificar con el `Get-CimInstance` de `CLAUDE.md` que hay UN solo `servidor.py` y es nuevo (el hilo `envios-informes` arranca ahí, nunca al importar
   `app`). (b) 📑 Informes › «Traer de DelPro…»: ver que la lista salga (si dice «No se pudieron leer…», avisar: serían las cuatro consultas contra el SQL Server real). (c) «Describí el informe» **con la clave de la IA cargada** y frases de verdad:
   es lo único que no se probó con el modelo real (los tests usan un cliente de mentira) y es donde puede haber diferencias de interpretación. (d) «Programar envío» con «Ver cómo queda» y recién después «Enviar ahora» a un solo canal.
2. **¿«Hoy» o el último día completo en los envíos programados?** El informe se calcula con la fecha del día del envío. Si DelPro consolida con atraso, los números del último día pueden estar incompletos a las 08:00 (hoy el mensaje dice a qué hora se calculó,
   no hasta qué día llegan los datos). Opción: calcular contra el último día completo (como hace Rendimiento Sala).
3. **¿WhatsApp para los informes programados?** Twilio manda solo texto (sin Excel) y, fuera de la ventana de 24 horas, solo plantillas: con el sandbox esto puede fallar seguido (Twilio acepta el pedido y después marca «undelivered»; el registro lo anota con una frase fija:
   ventana de 24 horas, sandbox o «no entregado»). Correo y Telegram llevan el Excel.
4. **Ampliar el catálogo de campos.** Es lo que más hace crecer el valor del importador y de la IA. Los más pedidos por los informes reales: `InMilk`, `IsDryingOff`, `ToBeCulled`, `PossiblyInWrongGroup`, `Sex`, `IsExited`, `NumberOfInseminations`, `IsPregnant`
   (cada uno con su verificación contra DelPro, como los diez que hay).
5. **Fechas relativas en el motor** («hoy menos N días»): sin eso un informe de «últimos 30 días» no sirve para programar. Hoy el aviso solo lo dice. **Decisión del tambo (24/11/2026): se deja así por ahora.**
6. **Aceptar la Fase 0**: falta comparar contra una exportación de DelPro del MISMO momento (los campos con ⚠ siguen sin compararse).
7. ~~**Validar el certificado del correo.**~~ **Hecho el 24/11/2026** (decisión del tambo: «hagámoslo»): `correo._mandar` verifica la cadena y el nombre del servidor, y un error de certificado es un mensaje claro que no sale y no se reintenta. La salida para un servidor propio o un proxy con inspección de TLS es el almacén de Windows o `SSL_CERT_FILE`; apagarlo (`SMTP_VERIFICAR_CERTIFICADO=0`) es el último recurso y deja un aviso. Ver `INSTALL.md` y `CLAUDE.md`.
8. **Versiones de Python y del SDK de Anthropic.** Las pruebas de la Fase 4 pasan con Python 3.14 + `anthropic` 1.0.0 y con Python 3.12.10 + `anthropic` 0.117.0 (el Python y el mínimo de `requirements.txt` que usa `iniciar.bat`);
   `output_config` (salida estructurada) y `anthropic.Timeout` existen en las dos. Igual conviene un `python --version` y un `pip show anthropic` en SERVER-DELPRO antes del primer uso.
9. **`_maqueta_tmp/`** (tres maquetas HTML de la Fase 0) sigue sin seguimiento en el repositorio: no se commitea, y un `git add -A` la sumaría. Mover a `porlasdudas/` o ignorarla, a elección.

## Riesgos

* Muchos campos tienen definiciones sutiles (¿qué cuenta como «lactancia actual» para una vaca seca?, ¿los DEL de una vaca que
  secó?). En la captura, la vaca 4300 tiene «días en ordeño» 356 y último parto del 07/10/2024: no es días desde el parto. Por eso
  cada campo se verifica contra DelPro y no se infiere por el nombre.
* SQL Express con poca RAM: no se lanzan consultas pesadas en paralelo; nada de escanear `CMSMilkYield` sin acotar fechas.
* Un campo que cambia de definición rompería informes ya guardados: los identificadores son estables y los campos viejos se
  marcan «no disponible», no se borran.
