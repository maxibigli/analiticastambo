# Balance de leche: sala → tanque → camión (diseño)

Fecha: 07/11/2026. Proyecto: LactIA (`delpro-analitica`, Flask + SQL Server DDM).
Estado: borrador para aprobación del usuario. Todavía no se construyó nada.

## Intención y el porqué

El tambo mide la misma leche en tres puntos con tres instrumentos distintos:

1. **Sala:** los medidores de ordeñe de DelPro (`AnimalDaily.TotalYield`, en **kg**).
2. **Tanque:** el caudalímetro de Milky/CICLA (litros por carga, API de Milky).
3. **Camión:** lo que reconoce el comprador, La Serenísima (litros por día).

Hoy la app compara solo dos de los tres (Milky contra comprador, tarjeta "Caudalímetro vs. Comprador").
El usuario quiere un menú nuevo que ponga los tres lado a lado para **detectar dónde se pierde leche**
entre la sala, el tanque y el camión, y que ese desvío **avise como alerta** cuando se sale de lo normal.

## Lo medido (base de las decisiones; no son supuestos)

Medición con `medir_factor_litros.py` (solo lectura) corrida en SERVER-DELPRO, 17 días consolidados
(17/10 al 02/11/2026), tambo `ponderosa`:

- Litros de Milky (todas las cargas, venta + guachera) por kg de DelPro: **1,0506 acumulado**,
  mediana día a día 1,050, desvío 0,0115 (1,1 %), extremos 1,035 y 1,084. En ventanas de 3 y 7 días
  el desvío baja a 0,003 y 0,002.
- La correlación DelPro(d) contra Milky(d) es 0,87; contra Milky(d±1) es 0,34 y 0,31. **Los días se
  comparan en el mismo día, sin desfase.**
- La tarjeta del comprador (21 días comparables) muestra Milky ≈ La Serenísima (≈ 0 %) todos los días
  salvo un par de días seguidos y de signo opuesto (alrededor del 25 y 26/10: +52 % y −27 %), que es un
  corrimiento de fecha de una carga, no leche perdida (interpretación, no verificada con la carga).
- La conversión por densidad típica de la leche (≈ 1,03 kg/L, referencia general) daría ≈ 0,97 L/kg;
  lo medido es 1,05. Es decir, DelPro, convertido por densidad, queda ≈ 8 % por debajo de lo que
  Milky y el comprador coinciden en medir. **La causa no se conoce** (medidores de sala que leen bajo,
  u otra). El factor de 1,05 absorbe esa diferencia y por eso es una **calibración**, no una conversión
  física.
- El 02/11 dio +3,2 % de desvío sala → tanque (Milky por encima de lo esperado por el factor).
- El último día de DelPro se consolida tarde: el 03/11 tenía 95 kg y del 04 al 07/11 no había fila.

## Qué se construye

Un menú nuevo **"Balance de leche"** dentro del grupo "Entrega de Leche" (junto a "Entregas CICLA",
"Caudalímetro vs. comprador" y "Calidad de leche"), con el estilo de la tarjeta "Caudalímetro vs.
Comprador": fechas propias (desde/hasta) y botón **Recalcular**. El período por defecto lo decide el
builder (idea del diseñador: los últimos 15 días con DelPro consolidado).

La pantalla tiene tres bloques, en este orden:

1. **Gráfico (arriba).** Dos series por día alrededor de 0 %: *sala → tanque* y *tanque → camión*, con la
   banda de ±3 %. Los puntos fuera de la banda se marcan en rojo.
2. **Tarjetas del período.** Sala (kg × factor), tanque (Milky, venta + guachera) y camión (La
   Serenísima, solo ventas), con el porcentaje entre cada par, y una línea con el tramo que más se desvía.
3. **Tabla por día** con casilleros de color por tramo. Si un día tiene +X % y el siguiente −Y % con el
   mismo valor aproximado, se marca "fecha corrida" y **no** como pérdida.

### Cálculo

- **DelPro en litros** = kg del día × **factor** (1,05 L/kg por defecto). El factor es editable en pantalla.
  Solo lo cambian los usuarios con rol admin y queda guardado en el servidor (decisión del diseñador;
  no se fija en el código). La pantalla muestra siempre el factor usado y aclara que es una calibración
  medida, con la diferencia de ≈ 8 % respecto de la conversión por densidad.
- **Kg de DelPro** = `AnimalDaily.TotalYield` con `IsYieldValid = 1`, **incluyendo la leche sin identificar**
  (`BasicAnimal.Number = 0`), porque también sale por el tanque. Tomar solo los días **consolidados**.
  *Decisión del diseñador:* el piso de 50 vacas de `resumen.VACAS_MIN_DIA` es demasiado bajo para esto
  (en los 17 días medidos hubo entre 1.600 y 1.676 vacas por día, y el día sin cerrar tenía 0), así que
  un día cuenta como consolidado si tiene al menos el **90 %** de la mediana de vacas con leche de los
  días del período (referencia: los días completos medidos están en 97 % o más de esa mediana). El 90 %
  es un valor de partida que el builder puede ajustar con datos.
- **Tanque** = litros de todas las cargas de Milky del día (`lts_cicla`, venta + guachera): salen del
  mismo tanque.
- **Camión** = litros de La Serenísima por día. Se compara contra las cargas de **venta** de Milky
  (`cicla.solo_ventas`), igual que la tarjeta actual (la guachera no se vende).
- **Desvío sala → tanque** = Milky total ÷ (kg × factor) − 1, por día. **Desvío tanque → camión** = Milky
  venta ÷ comprador − 1, el mismo cálculo de `/api/entregas/comparar`.
- **Umbral** ±3 % para ambos tramos (el mismo `cicla.UMBRAL_DIF_PCT` que ya usa la tarjeta del comprador).
  Evidencia: el desvío normal de la sala es 1,1 % por día, así que ±3 % deja pasar lo normal.
- **Días en el mismo día**, sin desfase.
- **"Fecha corrida":** dos días consecutivos con desvío de signo opuesto cuya suma **en litros** (no en
  porcentaje: el denominador cambia de un día al otro) queda dentro de ±3 % del volumen de los dos días
  juntos, **y los dos días fuera del umbral** (condición agregada al construir: sin ella, el 01/11 con −1,2 % junto
  al 02/11 con +3,2 %, datos reales, se tomaba por corrida y tapaba un desvío real). Ejemplo: el 25 y 26/10.
- **Sin credenciales del comprador:** el menú muestra solo sala → tanque y lo dice. **Sin credenciales de
  Milky:** error claro, igual que las tarjetas existentes (mensaje que nombra `MILKY_*`).

### Alerta

Un desvío fuera de ±3 % en cualquiera de los dos tramos aparece como una línea más del aviso de
alertas puntuales (`_lineas_alertas_puntuales`, que hoy trae temperatura CICLA, U.F.C. de La
Serenísima, rutina e incidencias, y se envía por WhatsApp). Reglas:

- Se evalúa sobre el **último día consolidado**, no sobre "hoy" (el día en curso es parcial).
- Un par de "fecha corrida" **no** dispara alerta.
- **Esperar un día (decisión del usuario).** Un día se evalúa para alertar recién cuando existe el día
  **siguiente** también consolidado: así se ve el par completo y un corrimiento de fecha no sale como
  pérdida. La alerta llega con un día de atraso.
- **No repetir la misma alerta.** Las alertas salen como un resumen completo en cada ciclo (8:00 y
  20:00); `_avisar_si_nuevo` ya no existe (ver `CLAUDE.md`). Además DelPro consolida con atraso: el
  07/11 el último día completo era el 02/11 (el 03/11 tenía 95 kg y el 04 al 07 no tenían fila), es decir
  varios días. Por eso **no se puede usar la antigüedad del dato** para evitar repeticiones: una alerta
  sobre el 01/11 seguiría saliendo cada ciclo durante días. *Decisión del diseñador:* guardar un registro
  mínimo y persistente del último día ya avisado por tramo (un archivo JSON en el servidor, por tambo), y
  avisar **una sola vez por día y por tramo**. El builder mide primero cuánto tarda DelPro en consolidar
  y confirma que el esquema alcanza.
- **Tramo tanque → camión:** se evalúa el último día que tenga los dos datos (Milky y comprador). Si el
  comprador todavía no informó ese día, simplemente no se evalúa ese tramo, sin advertencia (el retraso
  es normal). Decisión del diseñador.
- **Alcance:** igual que la alerta de temperatura de CICLA, que ya corre solo para el tambo de producción
  con Milky configurado. El factor se guarda por tambo. El menú sigue las reglas de `paginas_visibles`.
  Decisión del diseñador.
- Si una fuente (DelPro, Milky o el comprador) no se puede leer, se informa como línea de advertencia y no
  corta las demás, igual que el resto de `_lineas_alertas_puntuales` (cada chequeo en su propio `try/except`).

## Objetivos

- Ver los tres sistemas juntos, por día y por período, en una sola pantalla.
- Que se vea **en qué tramo** se desvía (sala → tanque o tanque → camión), no solo que hay diferencia.
- Que el desvío fuera de lo normal avise solo.

## No objetivos

- No corrige ni calibra Milky ni el comprador: el factor es **solo** de DelPro (kg → litros).
- No explica la causa de la diferencia de ≈ 8 % con la densidad; solo la deja visible.
- No toca ni reemplaza la tarjeta "Caudalímetro vs. Comprador" ni "Entregas CICLA".
- No clasifica la leche que no pasa por el tanque (descarte, terneros, tratamiento): DelPro no la separa.

## Antiobjetivos (fallaría aunque "ande")

- Que una carga de medianoche corrida de día se lea como pérdida de leche.
- Esconder la diferencia de ≈ 8 % (calibración) como si fuera una conversión física.
- Alertar por días sin consolidar o por una fuente caída.
- Inventar números o umbrales: todo valor sale de la medición de arriba o de constantes ya existentes.

## Restricciones

- Reglas del proyecto (`CLAUDE.md`): evidencia antes que supuestos; credenciales solo por variables de
  entorno; no se commitea `rutina.py`, `salas/convencional.py` ni `diagnostico_salud.py`; antes de cada
  commit se revisa el diff y se espera aprobación explícita del usuario; nunca borrar (mover a
  `porlasdudas`).
- DelPro corre en SQL Express con poca RAM: consultas livianas, con filtro de fechas, cacheadas, sin
  paralelismo (ver `db.py`). Esta PC de desarrollo **no llega** a SERVER-DELPRO y su copia local termina
  el 31/07/2026, así que las pruebas con datos reales de DelPro solo se pueden hacer en el servidor; en
  esta PC hay que probar con datos simulados/inyectados (el patrón de `serve_*` con `_laser_traer`
  simulado).
- Despliegue: `git pull` + `actualizar.bat` como Administrador (cambia un `.py`) en SERVER-DELPRO.
- Jinja: sin `{{`, `{%` ni `{#` dentro del JS de `index.html`.

## Qué queda para el builder

- Dónde y cómo se guarda el factor en el servidor, y cómo se pide/edita desde la interfaz.
- El detalle de la regla de "fecha corrida" y el período por defecto.
- La consulta SQL concreta (reusar `resumen.SQL_PRODUCCION_DIARIA` o una consulta propia acotada por fechas).
- Cómo se evita repetir alertas, siguiendo el patrón de las existentes.
- La visibilidad del menú (mismas reglas de `paginas_visibles` que las otras páginas de "Entrega de Leche").
- Pruebas (el repo tiene un patrón de pruebas con `test_client` y servidores simulados).
- Mantener `medir_factor_litros.py` para volver a medir el factor cuando haya más datos (hoy son 17 días).
