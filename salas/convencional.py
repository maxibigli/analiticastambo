# -*- coding: utf-8 -*-
"""Adaptador de la interfaz de `salas/__init__.py` para una sala convencional
(espina de pescado). A diferencia de `salas/rotativa.py` (que solo reexporta
`rutina.py`), acá SÍ hay lógica nueva: el esquema de esta sala no tiene
`CMSGroupMilkSetting`/`MilkingDeviceVisit`/`CMSMilkYield`, así que "qué grupos
ordeñan de verdad" y "cuándo se colocaron las pezoneras" salen de otro lado.

Verificado contra San José (DelPro 10.11):

    Identificación (rombo, ID)         = SessionMilkYieldEx.IdTimestamp
    Arranque de leche (cuadrado)       = SessionMilkYield.BeginTime
                                          (= SessionMilkYieldEx.MilkStartTimestamp,
                                          idénticos en las 210.074 filas —
                                          mismo dato, dos nombres)
    Fin / retiro (triángulo)           = SessionMilkYield.EndTime
    `SessionMilkYieldEx.IdTime` = -(IdTimestamp → BeginTime) en segundos: es
    LITERALMENTE el mismo "tiempo de colocación" que mide la rotativa, con
    otro nombre de columna. El resto del motor de puntaje (colocación,
    vacas lerdas, huecos entre/dentro de grupo, mezcla de rodeos) es el mismo
    de `rutina.py` sin cambios: no depende de que haya una plataforma.

Lo único que SÍ es distinto es la "ocupación": una rotativa gasta capacidad
real cuando un puesto gira vacío (la plataforma sigue girando al mismo ritmo
igual, haya vaca o no); acá no hay plataforma girando, hay TANDAS por lado
(`SideNo`/`BatchNo`/`MPCNo`, ver `sala_convencional.py`) que simplemente
procesan las vacas que estén listas en ese momento — una tanda más chica no
cuesta tiempo de máquina de más, solo trae menos vacas. No hay un equivalente
real de "ocupación de la plataforma" para puntuar, así que ese componente del
score NO SE EVALÚA acá (ver `_sin_ocupacion`): se excluye y su peso se
redistribuye entre el resto, con el mismo mecanismo que ya usa "prep_90s"
cuando falta el dato de colocación (ver `rutina._analizar_sesion`).
"""
import statistics

import resumen
import rutina
import sala_convencional

NOMBRE = "Convencional"

# Esta sala NO registra el instante en que se COLOCA la pezonera. El único
# sello anterior a la leche es `SessionMilkYieldEx.IdTimestamp`, y la vaca se
# identifica AL ENTRAR a la sala, no en el puesto: medido en La Martina el
# 10/08/2026 sobre 2.027 ordeños, el tramo identificación→arranque de leche
# promedia 300 segundos y baja hasta -434 (la ID cae después de que empezó a
# bajar la leche). O sea que ese tramo es la espera en el puesto, no la rutina
# de preparación, y `MilkStartTimestamp` resulta ser el mismo instante que
# `BeginTime`, así que no hay un tercer sello para separarlas.
#
# Puntuarlo igual daba 0 de 727 vacas "en hora" y hundía el score a 37 contra
# el ~81 de la rotativa: un número que acusa al tambo de trabajar mal cuando el
# dato no dice eso. Con esto el componente se excluye y su peso se reparte
# entre los demás, igual que "ocupación" (ver `_sin_ocupacion`).
#
# CUIDADO: ESTO NO ES CIERTO EN TODAS LAS SALAS CONVENCIONALES. El encabezado
# de este módulo documenta que en SAN JOSÉ se verificó lo contrario —ahí
# `IdTime` SÍ era el tiempo de colocación, el mismo que mide la rotativa—. O
# sea que el mismo campo significa una cosa en una instalación y otra en otra,
# y esta constante, al ser del módulo, se las aplica a las dos por igual: si
# vuelve a entrar San José, le apaga un componente que allá funcionaba.
#
# Lo correcto sería decidirlo POR INSTALACIÓN mirando el dato (si la mediana de
# identificación→leche está en el orden de los segundos es colocación; si está
# en minutos, es espera). Queda pendiente: con un solo tambo convencional
# activo no se puede calibrar esa regla sin inventarla.
MIDE_COLOCACION = False

# Pesos propios de esta sala. Los 30 puntos que en la rotativa lleva
# "colocación" acá no se pueden usar (ver MIDE_COLOCACION) y pasan a "flujo":
# la bimodalidad es la única señal de la rutina de ESTÍMULO que esta sala sí
# registra. El resto queda como en `rutina.PESOS` — no hay motivo para
# moverlos, y cambiarlos haría incomparables los dos tambos.
PESOS = {**rutina.PESOS, "prep_90s": 0, "flujo": 30}


def sql_grupos() -> str:
    """Grupos con producción real y sostenida, con nombre y número — mismo
    shape que `rutina.SQL_GRUPOS`, para el selector "qué grupos incluir"."""
    return f"""
        SELECT ad.AnimalGroup AS grupo, ag.Number AS numero, ag.Name AS nombre,
               COUNT(DISTINCT ad.BasicAnimal) AS cantidad
        FROM AnimalDaily ad
        JOIN AbstractGroup ag ON ag.OID = ad.AnimalGroup AND ag.GCRecord IS NULL
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date >= DATEADD(day, -{resumen.GRUPO_DIAS}, CAST(GETDATE() AS date))
          AND ad.AnimalGroup IS NOT NULL
        GROUP BY ad.AnimalGroup, ag.Number, ag.Name
        HAVING COUNT(DISTINCT ad.BasicAnimal) >= {sala_convencional.GRUPO_MIN_VACAS}
        ORDER BY ag.Number
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_grupos_resumen(dias: int = 30) -> str:
    return sala_convencional.sql_grupos_reales(dias)


def sql_ordenos_por_dia() -> str:
    return "SELECT MAX(SessionNo) AS ordenos_dia FROM ParlorHistoricalData"


def sql_duraciones_dia(dias: int = 7) -> str:
    return sala_convencional.sql_duraciones_dia(dias)


def armar_duraciones(filas: list, dias: int = 7) -> dict:
    return sala_convencional.armar_duraciones(filas, dias)


def cantidad_puestos(tambo: str) -> int:
    """Puestos REALES de esta instalación (lados × puestos por lado, ver
    `sala_convencional.configuracion`) — a diferencia de la rotativa, acá no
    hay un número fijo: cada sala convencional puede tener otra cantidad de
    lados/puestos por lado."""
    cfg = sala_convencional.configuracion(tambo)
    return cfg["lados"] * cfg["puestos_por_lado"]


def sql_rutina(fecha: str) -> str:
    """Visitas de un día (+6h de margen a cada lado, mismo criterio que
    `rutina.sql_rutina`). `lado`/`bloque` viajan además de las columnas
    comunes: los necesita `_huecos_tandas`/`_rotaciones_tandas` (ver
    `analizar_dia`)."""
    return f"""
        SELECT ex.MPCNo AS puesto, b.Number AS rp, b.[Group] AS grupo,
               ex.IdTimestamp AS hora_id, y.BeginTime AS hora_coloc, y.EndTime AS hora_fin,
               CAST(ex.ForcedRetract AS int) AS retirada_forzada,
               ex.SideNo AS lado, ex.BatchNo AS bloque,
               -- Curva de flujo para el componente de estimulo, ya en kg/min
               -- (ver ESCALA_FLUJO: en Alpro estos tramos vienen x100).
               ex.FlowZerotoFifteen   * 0.01 AS f0_15,
               ex.FlowFifteentoThirty * 0.01 AS f15_30
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE ex.IdTimestamp IS NOT NULL
          AND ex.IdTimestamp >= DATEADD(hour, -6, '{fecha}')
          AND ex.IdTimestamp < DATEADD(hour, 6, DATEADD(day, 1, '{fecha}'))
        ORDER BY ex.IdTimestamp
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def _sin_ocupacion(visitas: list, duracion_seg: float) -> dict:
    """Reemplaza a `rutina._ocupacion_rotativa` para esta sala: acá no hay
    plataforma que gire, así que no hay un equivalente real de "capacidad
    desperdiciada" para medir (ver el docstring del módulo). `score=None` hace
    que `rutina._analizar_sesion` EXCLUYA este componente del score y
    redistribuya su peso entre el resto, en vez de forzar un número que no
    significa nada para esta sala."""
    return {"label": "Ocupación de la plataforma (no aplica)", "score": None,
            "info": "Esta sala no tiene plataforma: cada tanda procesa las vacas que estén "
                    "listas, sin el costo de \"puesto girando vacío\" de una rotativa. No se "
                    "puntúa.", "hallazgos": []}


def _rotaciones_tandas(visitas: list, duracion_seg: float) -> int | None:
    """Análogo de `rutina._rotaciones_rotativa` para "Rendimiento Sala": en vez
    de vueltas de plataforma, cuenta tandas (lado, bloque) distintas."""
    tandas = {(v["lado"], v["bloque"]) for v in visitas if v.get("lado") is not None}
    return len(tandas) or None


# Qué proporción de los cambios de tanda pueden ser reapariciones antes de dar
# por inservible la numeración. Con tandas sanas esto es 0: cada tanda entra,
# se ordeña y no vuelve. San José daba 25 cambios limpios; La Martina, 112 de
# 143 (78%). El corte en la mitad deja lugar a algún solapamiento puntual entre
# lados sin tragarse un caso como el de La Martina.
FRAGMENTACION_MAXIMA = 0.5


def _fragmentacion_de_tandas(visitas: list) -> float:
    """Qué fracción de los cambios de tanda son tandas que YA habían aparecido.

    Es la prueba de si `BatchNo` sirve para segmentar: en una sala de tandas
    sana, cada tanda ocupa un tramo continuo del tiempo. Si el mismo número va
    y viene, no está identificando un grupo de vacas."""
    cambios, repetidas, vistas, actual = 0, 0, set(), None
    for v in visitas:
        clave = (v.get("lado"), v.get("bloque"))
        if clave == actual:
            continue
        if actual is not None:
            cambios += 1
            if clave in vistas:
                repetidas += 1
        vistas.add(clave)
        actual = clave
    return repetidas / cambios if cambios else 0.0


def _huecos_tandas(visitas: list, duracion_seg: float, nombres: dict | None = None) -> dict:
    """Análogo de `rutina._huecos_rotativa`, pero NO se puede reusar tal cual:
    esa versión compara todo contra UNA mediana de sesión, y en una sala de
    tandas eso rompe. Medido contra San José (26/07, sesión de la mañana):
    los gaps DENTRO de una tanda tienen mediana 5s (373 casos); los gaps ENTRE
    tandas (el otro lado ordeñando) tienen mediana 399s, de 177s a 1359s (25
    casos). Con una mediana pooled (~5s), CUALQUIER cambio de tanda —algo
    esperado y normal— queda por encima del umbral y se marca como "hueco":
    dio manejo_corral=0 y entre_grupos=26-59 en las tres sesiones reales, un
    puntaje que no refleja ningún problema real de manejo.

    Acá el corte es CAMBIO DE TANDA (lado, bloque), no cambio de grupo —la
    pausa estructural de esta sala es entre tandas, no entre rodeos—, y cada
    lado del corte usa SU PROPIA mediana como referencia.

    PERO ESO EXIGE QUE `BatchNo` MARQUE TANDAS DE VERDAD, y no siempre lo hace.
    En La Martina (10/08/2026, sesión de 731 ordeños) los números de tanda se
    cortan y REAPARECEN 112 veces sobre 143 cambios: las vacas de una tanda no
    quedan juntas en el tiempo, ni ordenando por identificación ni por arranque
    de leche. Con eso, lo que la métrica llama "cambio de tanda" son en su
    mayoría reapariciones del mismo número, la mediana entre tandas cae a 5-7s
    y CUALQUIER pausa real queda marcada como anormal: daba 12.850s "perdidos"
    y entre_grupos=0 en las tres sesiones, o sea acusar al tambo de perder tres
    horas por ordeñe cuando el dato no dice eso.

    Cuando la fragmentación pasa de `FRAGMENTACION_MAXIMA`, los dos componentes
    se devuelven en None y se excluyen del score (mismo mecanismo que
    "ocupación" y "colocación"). Es preferible no medir a publicar un número
    que parece un diagnóstico y no lo es."""
    fragmentacion = _fragmentacion_de_tandas(visitas)
    if fragmentacion > FRAGMENTACION_MAXIMA:
        return {
            "s3": None, "s4": None,
            "info3": (f"No se puede evaluar: los números de tanda de esta sala no agrupan a las "
                      f"vacas de forma contigua ({round(100 * fragmentacion)}% de los cambios son "
                      f"tandas que ya habían aparecido antes), así que no hay forma de separar "
                      f"una pausa real de un cambio de tanda."),
            "info4": "No se puede evaluar por el mismo motivo que la fila de arriba.",
            "hallazgos": [],
        }
    gaps = [((b["hora_id"] - a["hora_id"]).total_seconds(),
             (a.get("lado"), a.get("bloque")) != (b.get("lado"), b.get("bloque")), a, b)
            for a, b in zip(visitas, visitas[1:])]
    intra = [g for g, cambio, _, _ in gaps if not cambio]
    inter = [g for g, cambio, _, _ in gaps if cambio]
    mediana_intra = statistics.median(intra) if intra else 0
    mediana_inter = statistics.median(inter) if inter else 0
    umbral_intra = max(mediana_intra * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    umbral_inter = max(mediana_inter * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)

    exceso_entre_tandas = sum(g - mediana_inter for g in inter if g > umbral_inter)
    exceso_intra_tanda = sum(g - mediana_intra for g in intra if g > umbral_intra)
    s3 = 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso_entre_tandas / duracion_seg)
    s4 = 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso_intra_tanda / duracion_seg)

    hallazgos = [{
        "tipo": "hueco_grupo", "severidad": g, "puesto": None, "rp": None,
        "texto": f"Hueco de {round(g / 60, 1)} min al cambiar de tanda (lado {a.get('lado')}, "
                 f"bloque {a.get('bloque')} → lado {b.get('lado')}, bloque {b.get('bloque')}) "
                 f"a las {b['hora_id'].strftime('%H:%M')}, bastante más largo que el resto de "
                 "los cambios de tanda de esta sesión.",
    } for g, cambio, a, b in gaps if cambio and g > umbral_inter]

    return {
        "s3": s3, "s4": s4,
        "info3": f"{round(exceso_entre_tandas)}s perdidos en cambios de tanda anormalmente largos "
                 f"(mediana real entre tandas: {round(mediana_inter)}s).",
        "info4": f"{round(exceso_intra_tanda)}s perdidos por demoras trayendo animales dentro "
                 f"de la misma tanda (mediana real: {round(mediana_intra)}s).",
        "hallazgos": hallazgos,
    }


def analizar_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                 max_sesiones=None, nombres=None, umbral_prep_s=None) -> dict:
    # `tambo` no hace falta acá (a diferencia de antes: la ocupación ya no
    # depende de `puestos_por_lado`, ver `_sin_ocupacion`) — queda en la firma
    # solo para cumplir la interfaz común (ver salas/rotativa.py).
    return rutina.analizar_dia(columns, rows, fecha, grupos, pesos or PESOS, max_sesiones,
                               nombres, _sin_ocupacion, _huecos_tandas, umbral_prep_s,
                               mide_colocacion=MIDE_COLOCACION)


def resumen_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                max_sesiones=None, nombres=None, umbral_prep_s=None):
    return rutina.resumen_dia(columns, rows, fecha, grupos, pesos or PESOS, max_sesiones,
                              nombres, _sin_ocupacion, _huecos_tandas, umbral_prep_s,
                              mide_colocacion=MIDE_COLOCACION)


# LOS CUATRO TRAMOS DE FLUJO DE ALPRO VIENEN ×100, y esto es una trampa cara.
# Medido sobre 30.949 ordeños de La Martina contra 643.474 de La Ponderosa:
#
#     tramo      0-15s   15-30s   30-60s   60-120s   AverageFlow   PeakFlow
#     rotativa    0,85     2,71     2,58      3,79       2,84        4,86
#     Alpro         65      138      141       216       2,17        4,39
#
# `AverageFlow` y `PeakFlow` coinciden en escala; los tramos no. Corriendo los
# umbrales de la rotativa tal cual (bimodalidad con inicio ≥0,2 y arranque
# lento <0,5 kg/min) daba 100% de bimodalidad y 0% de arranque lento en TODOS
# los ordeños: el tambo entero diagnosticado como catástrofe por un factor de
# escala. Se normaliza en la consulta para que aguas abajo todo sea kg/min y
# los umbrales sean los mismos para las dos salas.
ESCALA_FLUJO = 0.01


def sql_flujo_ordenios(desde: str, hasta: str) -> str:
    """Un renglón por ordeño con la curva en cuatro tramos, YA convertida a
    kg/min (ver `ESCALA_FLUJO`). Mismo contrato que en la rotativa."""
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    e = ESCALA_FLUJO
    return f"""
        SELECT b.Number AS rp,
               ex.FlowZerotoFifteen   * {e} AS f0_15,
               ex.FlowFifteentoThirty * {e} AS f15_30,
               ex.FlowThirtyToSixty   * {e} AS f30_60,
               ex.FlowSixtyTo120      * {e} AS f60_120,
               ex.AverageFlow AS f_prom,
               ex.PeakFlow    AS f_pico
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE ex.FlowZerotoFifteen IS NOT NULL
          AND y.BeginTime >= '{desde}'
          AND y.BeginTime < DATEADD(day, 1, '{hasta}')
        OPTION (MAX_GRANT_PERCENT = 25)
    """


# --- Pantalla de Flujos -----------------------------------------------------
# ESTA SALA NO PUBLICA SU UMBRAL DE RETIRADA. `CMSMpcSetting` no existe y no hay
# NINGUNA columna TakeoffLimit/LowFlowLimit en todo el esquema (se buscó en
# sys.columns). El flujo al que se retiró cada pezonera SÍ está
# (`TakeOffFlow`, 31.266 filas, 0 a 4,7 kg/min), pero sin el umbral configurado
# del equipo no se puede clasificar cada retirada en temprana / en objetivo /
# tardía, que es la banda ±25% del informe de DelPro.
#
# Se deja en NULL y la pantalla lo dice, en vez de inventar un umbral: un
# número inventado ahí no es un dato incompleto, es un diagnóstico falso sobre
# el equipo. Es la misma regla que ya está en CLAUDE.md ("NO inventarlos ni
# hacerlos editables"), aplicada al caso que esa regla no contemplaba.
PUBLICA_UMBRAL_RETIRADA = False

# La duración del ordeño no viene como columna (no hay `IsoDuration`): se
# calcula del intervalo, que es exactamente lo que esa columna guarda en la
# rotativa (verificado en su momento: IsoDuration = EndTime - BeginTime).
_DUR_SEG = "DATEDIFF(second, y.BeginTime, y.EndTime)"

# `LowFlowDurationInSec` (segundos de flujo bajo al inicio) NO TIENE
# EQUIVALENTE. Lo más parecido es `LowMilkFlowPercentage`, que es un PORCENTAJE
# del ordeño, no segundos: son medidas distintas y convertir una en otra
# requeriría suponer la duración. Va en NULL — el frontend ya sabe mostrar
# "sin datos" cuando falta una serie.
_COLOC_SEG = "NULL"

_FLUJOS_PROM_CONV = f"""
       AVG(ex.FlowZerotoFifteen   * {ESCALA_FLUJO}) AS f_0_15,
       AVG(ex.FlowFifteentoThirty * {ESCALA_FLUJO}) AS f_15_30,
       AVG(ex.FlowThirtyToSixty   * {ESCALA_FLUJO}) AS f_30_60,
       AVG(ex.FlowSixtyTo120      * {ESCALA_FLUJO}) AS f_60_120,
       AVG(ex.TakeOffFlow) AS f_retirada"""

_BIMODAL_CONV = f"""
       100.0 * SUM(CASE WHEN ex.FlowZerotoFifteen * {ESCALA_FLUJO} >= {rutina.BIMODAL_INICIO_MIN}
                         AND ex.FlowFifteentoThirty < ex.FlowZerotoFifteen
                        THEN 1 ELSE 0 END) / COUNT(*) AS pct_bimodal,
       100.0 * SUM(CASE WHEN ex.FlowZerotoFifteen * {ESCALA_FLUJO} < 0.5
                        THEN 1 ELSE 0 END) / COUNT(*) AS pct_arranque_lento"""


def _rango_conv(desde: str, hasta: str) -> str:
    """El rango va sobre `BeginTime` (arranque de leche), no sobre la
    identificación: acá la ID puede caer minutos antes o incluso después
    (ver MIDE_COLOCACION), así que como eje de tiempo no sirve."""
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    return f"y.BeginTime >= '{desde}' AND y.BeginTime < DATEADD(day, 1, '{hasta}')"


def sql_flujos_por_dia(desde: str, hasta: str, retirada_min=None, retirada_max=None) -> str:
    """Serie diaria. `retirada_min`/`retirada_max` se aceptan para respetar la
    interfaz común pero SE IGNORAN: ver PUBLICA_UMBRAL_RETIRADA."""
    return f"""
        SELECT CAST(y.BeginTime AS date) AS fecha,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM_CONV},
               AVG(ex.AverageFlow) AS f_prom,
               AVG(ex.PeakFlow)    AS f_pico,
               AVG({_DUR_SEG} * 1.0) AS dur_seg,
               {_COLOC_SEG} AS coloc_seg,
               AVG(y.TotalYield) AS litros_bajada,
               NULL AS pct_bajo_min,
               NULL AS pct_sobre_max,
               100.0 * SUM(CASE WHEN ex.ManualMode <> 0 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_manual,
               100.0 * SUM(CASE WHEN ex.ManualDetach = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_retiro_manual,
               100.0 * SUM(CASE WHEN ex.ForcedRetract = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_forzada,
               {_BIMODAL_CONV}
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        WHERE {_rango_conv(desde, hasta)} AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY CAST(y.BeginTime AS date)
        ORDER BY fecha
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_por_grupo(desde: str, hasta: str) -> str:
    """Curva promedio por grupo. Igual que en la rotativa, usa el grupo ACTUAL
    del animal: la base no guarda el grupo del día del ordeño.

    No se filtra por `CMSGroupMilkSetting.EnableMilking` (esa tabla no existe
    acá): se toman los grupos que efectivamente aparecen ordeñando, que es el
    mismo criterio que usa `sql_grupos()` de este módulo."""
    return f"""
        SELECT g.Number AS grupo_num, g.Name AS grupo,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM_CONV}
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal AND b.GCRecord IS NULL
        JOIN AnimalGroup ag ON ag.OID = b.[Group]
        JOIN AbstractGroup g ON g.OID = ag.OID AND g.GCRecord IS NULL
        WHERE {_rango_conv(desde, hasta)} AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY g.Number, g.Name
        ORDER BY g.Number
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_distribucion(desde: str, hasta: str) -> str:
    """Histograma conjunto de flujo promedio y pico, en cajones de 1 kg/min.
    `AverageFlow`/`PeakFlow` YA están en kg/min en esta sala (no se escalan:
    los ×100 son solo los cuatro tramos)."""
    bp = "CASE WHEN ex.AverageFlow >= 9.5 THEN 10 ELSE CAST(ROUND(ex.AverageFlow, 0) AS int) END"
    bk = "CASE WHEN ex.PeakFlow >= 9.5 THEN 10 ELSE CAST(ROUND(ex.PeakFlow, 0) AS int) END"
    return f"""
        SELECT {bp} AS bin_prom, {bk} AS bin_pico, COUNT(*) AS n
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        WHERE {_rango_conv(desde, hasta)}
          AND ex.AverageFlow >= 0 AND ex.PeakFlow >= 0
        GROUP BY {bp}, {bk}
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_por_deo(desde: str, hasta: str) -> str:
    """Bimodalidad y duración por tramo de días en ordeño. Los tramos de DEL
    son los MISMOS que en la rotativa (se reusa `flujos._CASE_DEO`, que ya
    escribe sobre el alias `d`): si cada sala cortara distinto, los dos tambos
    no se podrían comparar."""
    import flujos
    return f"""
        SELECT {flujos._CASE_DEO} AS deo,
               COUNT(*) AS ordenos,
               {_BIMODAL_CONV},
               AVG({_DUR_SEG} * 1.0) AS dur_seg,
               {_COLOC_SEG} AS coloc_seg
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN AnimalDaily d ON d.OID = y.AnimalDaily
        WHERE {_rango_conv(desde, hasta)} AND d.DIM IS NOT NULL
          AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY {flujos._CASE_DEO}
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_tiempo_fuera(desde: str, hasta: str) -> str:
    """Por día: segundos promedio POR VACA entre bajadas del mismo día.

    Mismo cálculo en dos pasos que la rotativa (`flujos.sql_tiempo_fuera`), y
    no un promedio de huecos sueltos: primero se SUMAN los huecos de cada vaca
    en el día y recién después se promedia entre vacas. Son números distintos
    -uno responde "cuánto dura un hueco", el otro "cuánto tiempo pasa afuera
    una vaca en el día"- y este último es el que muestra la pantalla. Los
    alias tienen que ser los que espera `flujos.analizar`.

    Es una ESTIMACIÓN, igual que en la rotativa: la base no tiene sensores de
    entrada/salida al corral, así que el hueco mezcla comida, descanso,
    caminata y espera. Mismas guardas de plausibilidad y mismo criterio de
    descartar el hueco nocturno (solo huecos dentro del mismo día)."""
    import flujos
    return f"""
        WITH visitas AS (
          SELECT y.BasicAnimal, CAST(y.BeginTime AS date) AS fecha,
                 y.BeginTime AS inicio,
                 LAG(y.BeginTime) OVER (
                   PARTITION BY y.BasicAnimal ORDER BY y.BeginTime
                 ) AS inicio_anterior
          FROM SessionMilkYield y
          WHERE {_rango_conv(desde, hasta)}
        ),
        huecos AS (
          SELECT BasicAnimal, fecha,
                 DATEDIFF(second, inicio_anterior, inicio) AS gap_seg
          FROM visitas
          WHERE inicio_anterior IS NOT NULL
            AND CAST(inicio_anterior AS date) = fecha
            AND DATEDIFF(second, inicio_anterior, inicio)
                BETWEEN {flujos.GAP_MIN_SEG} AND {flujos.GAP_MAX_SEG}
        ),
        por_vaca_dia AS (
          SELECT BasicAnimal, fecha, SUM(gap_seg) AS seg_fuera
          FROM huecos GROUP BY BasicAnimal, fecha
        )
        SELECT fecha, AVG(seg_fuera * 1.0) AS seg_fuera_prom, COUNT(*) AS vacas_con_dato
        FROM por_vaca_dia
        GROUP BY fecha
        HAVING COUNT(*) >= {flujos.VACAS_FUERA_MIN}
        ORDER BY fecha
        OPTION (MAX_GRANT_PERCENT = 20)
    """

def sql_rendimiento(desde: str, hasta: str) -> str:
    """Igual que `sql_rutina`, + el kg de cada visita — para "Rendimiento Sala".

    NO se filtra `IdTimestamp IS NOT NULL`, por el mismo motivo que en la
    rotativa (ver el detalle medido en `rutina.sql_rendimiento`): un ordeño
    cuya identificación falló sigue siendo un ordeño real con leche, y
    excluirlo desviaba todas las métricas de la pantalla. Acá el respaldo de
    hora es `BeginTime` (arranque de leche), que en esta sala existe siempre
    porque la fila SALE de `SessionMilkYield`.

    OJO: esto está portado del arreglo de la rotativa, donde sí se pudo medir
    contra el reporte de DelPro. En una sala convencional real todavía no se
    verificó cuántas filas tienen `IdTimestamp` nulo — si son cero, el cambio
    no altera nada; si no, las incluye, que es lo correcto."""
    return f"""
        SELECT ex.MPCNo AS puesto, b.Number AS rp, b.[Group] AS grupo,
               ex.IdTimestamp AS hora_id, y.BeginTime AS hora_creacion,
               y.BeginTime AS hora_coloc, y.EndTime AS hora_fin,
               y.TotalYield AS kg, CAST(ex.ForcedRetract AS int) AS retirada_forzada,
               NULL AS rotacion, NULL AS turno,
               ex.SideNo AS lado, ex.BatchNo AS bloque
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE COALESCE(ex.IdTimestamp, y.BeginTime) >= DATEADD(hour, -6, '{desde}')
          AND COALESCE(ex.IdTimestamp, y.BeginTime) < DATEADD(hour, 6, DATEADD(day, 1, '{hasta}'))
        ORDER BY COALESCE(ex.IdTimestamp, y.BeginTime)
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def sql_identificacion(desde: str, hasta: str):
    """None = el % de identificación no está disponible en esta sala.

    El criterio de la rotativa (visitas de `MilkingDeviceVisit` cuyo animal es
    el placeholder `BasicAnimal.Number = 0`, ver `rutina.sql_identificacion`)
    no se puede trasladar tal cual: acá el ordeño sale de `SessionMilkYield`,
    que referencia el animal directo, y NO se verificó contra datos reales de
    San José cómo queda una vaca que la sala no logró identificar. Devolver
    None y que la pantalla lo diga es preferible a mostrar un 100% inventado —
    justamente el bug que tenía la rotativa antes (ver el docstring de
    `rutina.sql_identificacion`). Se implementa cuando haya un caso real para
    medirlo, con el mismo método: contar contra los datos, no suponer.
    """
    return None


def analizar_rendimiento(tambo: str, columns, rows, desde: str, hasta: str, max_sesiones=None,
                         nombres=None, grupos_ordene=None) -> list:
    return rutina.analizar_rendimiento(columns, rows, desde, hasta, max_sesiones,
                                       rotaciones_fn=_rotaciones_tandas, nombres=nombres,
                                       grupos_ordene=grupos_ordene)


def resumen_grupos_dia(tambo: str, columns, rows, fecha: str, grupos_ordene=None, nombres=None) -> dict:
    return rutina.resumen_grupos_dia(columns, rows, fecha, grupos_ordene=grupos_ordene, nombres=nombres,
                                     ocupacion_fn=_sin_ocupacion)
