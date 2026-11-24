# Informes personalizables (estilo DelPro, en la web) — diseño

Fecha: 23/11/2026. Estado: **aprobado para la Fase 0** por el usuario; las fases siguientes se deciden con lo que enseñe la Fase 0.

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
  enum estándar de DevExpress: **sin verificar**). Solo 10 columnas agrupadas.

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
* Fase 4: importar los informes de DelPro, armarlos por lenguaje natural, envío programado.

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
| Estado de la reproducción | por los bits de `AnimalReproductionInfo` | **sin verificar**: los códigos de `BreedingState` 1, 3 y 5 no tienen nombre en la base |
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

## Riesgos

* Muchos campos tienen definiciones sutiles (¿qué cuenta como «lactancia actual» para una vaca seca?, ¿los DEL de una vaca que
  secó?). En la captura, la vaca 4300 tiene «días en ordeño» 356 y último parto del 07/10/2024: no es días desde el parto. Por eso
  cada campo se verifica contra DelPro y no se infiere por el nombre.
* SQL Express con poca RAM: no se lanzan consultas pesadas en paralelo; nada de escanear `CMSMilkYield` sin acotar fechas.
* Un campo que cambia de definición rompería informes ya guardados: los identificadores son estables y los campos viejos se
  marcan «no disponible», no se borran.
