# -*- coding: utf-8 -*-
"""Salud del rodeo: réplica funcional del reporte "Chi" (add-on HealthIndex.exe
de DeLaval) usando los datos reales de la base DDM.

IMPORTANTE sobre el SCORE: el add-on Chi calcula su índice de salud DENTRO del
ejecutable y NO lo guarda en la base (sus tablas Chi_* son solo temporales de
trabajo: Chi_HerdEvents, Chi_MilkGroups24, Chi_TempRotaryStops24). Por eso el
índice de atención de este módulo es PROPIO: usa las mismas señales que el
reporte declara ("Caída de leche", "Conductividad", "Caída del estado corporal
BCS", "Tendencia de caída de leche", "Baja producción diaria"), pero los
valores NO coinciden con los del Chi oficial y no deben presentarse como tales.

Todo lo demás (RCS/células somáticas, conductividad del rebaño y estadística
de producción por rodeo) sí sale de datos reales y es directamente comparable.
"""
import re

# `salas.de(tambo).sql_grupos()` viene armado para correr SOLO (trae su
# propio ORDER BY + OPTION de memoria, ver salas/convencional.py) — SQL Server
# no permite NINGUNO de los dos dentro de una subquery/derived table sin
# TOP/OFFSET ("Incorrect syntax near OPTION" / "The ORDER BY clause is
# invalid in ... derived tables ... subqueries"), así que se sacan acá antes
# de usarlo como JOIN en las consultas de abajo.
_OPTION_CLAUSE = re.compile(r"\s*OPTION\s*\([^)]*\)\s*;?\s*\Z", re.IGNORECASE)
_ORDER_BY_CLAUSE = re.compile(r"\s*ORDER\s+BY\b.*\Z", re.IGNORECASE | re.DOTALL)


def _grupos_subquery(grupos_sql: str) -> str:
    sin_option = _OPTION_CLAUSE.sub("", grupos_sql)
    return _ORDER_BY_CLAUSE.sub("", sin_option)

# OJO CON LAS UNIDADES: MilkTest.SCC se guarda en MILES de células/ml (un valor
# de 4488 en la base son 4.488.000 células/ml). Verificado contra el reporte
# Chi, que muestra "Prom. último RCS 159.000" donde la base tiene ~159.
# Por eso las comparaciones en SQL van contra 300 y el valor se multiplica por
# 1000 recién al mostrarlo.
UMBRAL_RCS_BASE = 300      # umbral en las unidades de la base (= 300.000 cél/ml)
RCS_A_CELULAS = 1000       # factor para pasar de la unidad de la base a cél/ml
UMBRAL_RCS = UMBRAL_RCS_BASE * RCS_A_CELULAS   # umbral "de verdad", para mostrar
MESES_TESTS = 12           # ventana de controles lecheros a considerar
DIAS_CONDUCTIVIDAD = 30    # ventana del gráfico de conductividad del rebaño
COND_ALTA = 115            # conductividad relativa sospechosa (>115)
TOP_ATENCION = 15          # cuántas vacas listar en "Atención vacas"

# Último día con producción VÁLIDA. Se usa como ancla de todas las ventanas en
# vez de GETDATE(): los datos suelen venir con atraso y el último día cargado
# está a medio ordeñar (IsYieldValid = 0). Anclando en "hoy" se comparaba media
# sesión contra la expectativa del día entero y salían caídas de leche falsas.
_ANCLA = """
      SELECT MAX(Date) AS d FROM AnimalDaily
      WHERE GCRecord IS NULL AND IsYieldValid = 1 AND TotalYield > 0
"""

# --- Controles lecheros: último y anterior RCS de cada vaca -----------------
# CUIDADO CON ESTE JOIN (se verificó a mano contra el reporte Chi):
#   MilkTest hereda de AnimalHistoricalData (hay FK real MilkTest.OID →
#   AnimalHistoricalData), y de ahí salen el animal (BasicAnimal) y la fecha
#   (DateAndTime) del control.
#   NO se usa MilkingTestAnimal: aunque su OID "matchea" con MilkTest, es pura
#   colisión de OIDs — se comprobó que el animal NO coincide en ninguna de las
#   9.945 filas, y su SampleDateTime está NULL en toda la base.
# Validado: vacas 2441→3718, 463→2825, 17627→2661 (previo 6), 14811→2313
# (previo 2877), todas idénticas al reporte Chi del 2026-07-07.
_CTE_TESTS = f"""
    tests AS (
      SELECT h.BasicAnimal AS Animal, mt.SCC, CAST(h.DateAndTime AS date) AS fecha,
             ROW_NUMBER() OVER (PARTITION BY h.BasicAnimal
                                ORDER BY h.DateAndTime DESC) AS rn
      FROM MilkTest mt
      JOIN AnimalHistoricalData h ON h.OID = mt.OID
      WHERE h.GCRecord IS NULL AND mt.SCC IS NOT NULL
        AND h.DateAndTime >= DATEADD(month, -{MESES_TESTS}, GETDATE())
    ),
    ult AS (
      SELECT t1.Animal, t1.SCC AS scc_ultimo, t1.fecha AS fecha_ultimo,
             t2.SCC AS scc_anterior, t2.fecha AS fecha_anterior
      FROM tests t1
      LEFT JOIN tests t2 ON t2.Animal = t1.Animal AND t2.rn = 2
      WHERE t1.rn = 1
    )
"""

# --- Sección "Mayores RCS": resumen por grupo -------------------------------
# "nuevas" = vacas que superan el umbral en el último control pero NO lo
# superaban en el anterior (casos nuevos, los más accionables).
#
# `grupos_sql`: subquery de "qué [Group] son de ordeñe real" — sale de
# `salas.de(tambo).sql_grupos()` (ver salas/__init__.py), NUNCA se hardcodea
# CMSGroupMilkSetting acá: esa tabla es propia del controlador de una
# rotativa y una sala convencional (San José) no la tiene — la consulta
# tiraría 'Invalid object name' (ver db.TablaNoDisponibleError). Todas las
# consultas de este módulo que necesitan "grupos reales" siguen este mismo
# patrón, con el mismo shape de columna (`gr.grupo`).
def sql_rcs_por_grupo(grupos_sql: str) -> str:
    return f"""
        WITH {_CTE_TESTS}
        SELECT g.Name AS grupo, g.Number AS numero,
               COUNT(*) AS vacas,
               AVG(u.scc_ultimo) AS scc_promedio,
               MAX(u.scc_ultimo) AS scc_maximo,
               SUM(CASE WHEN u.scc_ultimo > {UMBRAL_RCS_BASE} THEN 1 ELSE 0 END) AS altas_ultimo,
               SUM(CASE WHEN u.scc_anterior > {UMBRAL_RCS_BASE} THEN 1 ELSE 0 END) AS altas_anterior,
               SUM(CASE WHEN u.scc_ultimo > {UMBRAL_RCS_BASE}
                         AND (u.scc_anterior IS NULL OR u.scc_anterior <= {UMBRAL_RCS_BASE})
                        THEN 1 ELSE 0 END) AS nuevas,
               SUM(CASE WHEN u.scc_ultimo > {UMBRAL_RCS_BASE} AND u.scc_anterior > {UMBRAL_RCS_BASE}
                        THEN 1 ELSE 0 END) AS cronicas
        FROM ult u
        JOIN BasicAnimal b ON b.OID = u.Animal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
        GROUP BY g.Name, g.Number
        ORDER BY g.Number
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """

# --- Secciones "Vacas con RCS altos" y "Crónicas" ---------------------------
# `cronica` = superó el umbral en el último control Y en el anterior.
def sql_rcs_vacas(grupos_sql: str) -> str:
    return f"""
        WITH ancla AS ({_ANCLA}),
        {_CTE_TESTS},
        dia AS (
          SELECT ad.BasicAnimal, ad.DIM, ad.LactationNumber,
                 ROW_NUMBER() OVER (PARTITION BY ad.BasicAnimal ORDER BY ad.Date DESC) AS rn
          FROM AnimalDaily ad CROSS JOIN ancla
          WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1
            AND ad.Date BETWEEN DATEADD(day, -20, ancla.d) AND ancla.d
        )
        SELECT b.Number AS rp, g.Name AS grupo, d.DIM AS del, d.LactationNumber AS lactancia,
               u.scc_ultimo, u.scc_anterior,
               CONVERT(varchar(10), u.fecha_ultimo, 120) AS fecha_ultimo,
               CONVERT(varchar(10), u.fecha_anterior, 120) AS fecha_anterior,
               CASE WHEN u.scc_anterior > {UMBRAL_RCS_BASE} THEN 1 ELSE 0 END AS cronica
        FROM ult u
        JOIN BasicAnimal b ON b.OID = u.Animal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        LEFT JOIN dia d ON d.BasicAnimal = u.Animal AND d.rn = 1
        WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
          AND u.scc_ultimo > {UMBRAL_RCS_BASE}
        ORDER BY u.scc_ultimo DESC
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """

# --- Condición corporal (BCS) por vaca --------------------------------------
# Réplica del gráfico de DelPro "Score corporal" (cámara BCS): un punto por
# vaca (su ÚLTIMA lectura), DEL en el eje X y score 1-5 en el eje Y. Escala
# real verificada en esta base: 1,6 a 4,6 (promedio ~3,1).
#
# CURVA OBJETIVO POR DEL, no un umbral parejo para toda la lactancia. Hasta
# el 17/08/2026 esta sección usaba un mínimo/máximo fijo (2,5 a 4,25) para
# CUALQUIER vaca sea cual sea su estado de lactancia — con eso, una vaca recién
# parida en la caída fisiológica normal de condición corporal (que llega a
# ~2,75 al pico de producción, ~DEL 100) podía marcar "fuera de rango" sin
# estar mal, y una vaca a punto de secarse con 3,4 —sana para su DEL— podía no
# saltar si el máximo fijo estaba puesto más alto. La condición corporal SIEMPRE
# se lee contra dónde debería estar la vaca en SU lactancia, no contra un
# número parejo para el rodeo entero.
#
# Curva de referencia del tambo (confirmada con el usuario el 17/08/2026,
# leída de su gráfico de referencia): objetivo por tramo de DEL, tolerancia
# fija de ±0,25 puntos a cada lado — no independiente por punto, así que no se
# guardan tres curvas sino una sola (el objetivo) más un margen constante.
_OBJETIVO_BCS_PUNTOS = [
    (0, 3.50),      # preparto/seca: objetivo estable, no depende del DEL
    (30, 3.00),     # caída post-parto en marcha
    (100, 2.75),    # mínimo fisiológico, pico de producción
    (200, 3.00),    # recuperación
    (300, 3.30),
    (350, 3.50),    # de vuelta al objetivo de seca, y se mantiene
]
TOLERANCIA_BCS = 0.25  # banda aceptable: objetivo ± esto


def objetivo_bcs(dim) -> float | None:
    """Score corporal objetivo para un DEL dado, interpolando linealmente
    entre los puntos de `_OBJETIVO_BCS_PUNTOS`. Antes del primer punto (DEL<0,
    preparto) y después del último (DEL>350) el objetivo queda CONSTANTE en el
    valor del extremo — no tiene sentido extrapolar una curva de lactancia
    más allá de sus puntos medidos. None si no hay DEL (no hay con qué
    comparar; ver `sql_bcs_vacas`, `d.DIM` puede venir NULL si el animal no
    tiene un `AnimalDaily` reciente)."""
    if dim is None:
        return None
    puntos = _OBJETIVO_BCS_PUNTOS
    if dim <= puntos[0][0]:
        return puntos[0][1]
    if dim >= puntos[-1][0]:
        return puntos[-1][1]
    for (d0, v0), (d1, v1) in zip(puntos, puntos[1:]):
        if d0 <= dim <= d1:
            frac = (dim - d0) / (d1 - d0)
            return round(v0 + frac * (v1 - v0), 3)
    return None  # inalcanzable: los puntos cubren todo el rango por construcción

# Además del grupo (ver nota de sql_rcs_por_grupo), esta consulta depende de
# BcsDailyData: existe solo si el tambo tiene la cámara BCS instalada (es un
# add-on de hardware, independiente del tipo de sala). Si no está, tira
# 'Invalid object name' → db.TablaNoDisponibleError; el llamador (app.py) lo
# captura puntualmente y muestra "sin cámara BCS" en vez de reintentar para
# siempre.
def sql_bcs_vacas(grupos_sql: str) -> str:
    return f"""
        WITH bcs AS (
          SELECT Animal, BcsValue, CAST(DateAndTime AS date) AS fecha,
                 ROW_NUMBER() OVER (PARTITION BY Animal ORDER BY DateAndTime DESC) AS rn
          FROM BcsDailyData
          WHERE BcsValue IS NOT NULL
        ),
        dia AS (
          SELECT ad.BasicAnimal, ad.DIM, ad.LactationNumber,
                 ROW_NUMBER() OVER (PARTITION BY ad.BasicAnimal ORDER BY ad.Date DESC) AS rn
          FROM AnimalDaily ad
          WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1
        )
        SELECT b.Number AS rp, g.Name AS grupo, d.DIM AS del, d.LactationNumber AS lactancia,
               bc.BcsValue AS score, CONVERT(varchar(10), bc.fecha, 120) AS fecha_score,
               CASE WHEN r.IsDryingOff = 1 THEN 'En secado' WHEN r.IsPregnant = 1 THEN 'Preñada'
                    WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN '-'
                    ELSE 'Vacía' END AS reproductivo
        FROM bcs bc
        JOIN BasicAnimal b ON b.OID = bc.Animal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        LEFT JOIN dia d ON d.BasicAnimal = bc.Animal AND d.rn = 1
        LEFT JOIN AnimalReproductionInfo r ON r.Animal = bc.Animal AND r.GCRecord IS NULL
        WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0 AND bc.rn = 1
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


# --- Sección "Vista del rebaño (conductividad)" -----------------------------
# Promedio diario de conductividad relativa por grupo. >115 es sospecha de
# mastitis, así que la curva por rodeo muestra si un lote se está yendo.
def sql_conductividad_rebanio(grupos_sql: str) -> str:
    return f"""
        WITH ancla AS ({_ANCLA})
        SELECT CAST(s.BeginTime AS date) AS fecha, g.Name AS grupo,
               AVG(CAST(s.RelativeConductivity AS float)) AS cond_promedio,
               SUM(CASE WHEN s.RelativeConductivity > {COND_ALTA} THEN 1 ELSE 0 END) AS vacas_altas,
               COUNT(*) AS ordenos
        FROM SessionMilkYield s CROSS JOIN ancla
        JOIN BasicAnimal b ON b.OID = s.BasicAnimal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        WHERE CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -{DIAS_CONDUCTIVIDAD}, ancla.d) AND ancla.d
          AND s.RelativeConductivity IS NOT NULL AND b.Number > 0
        GROUP BY CAST(s.BeginTime AS date), g.Name
        ORDER BY fecha, grupo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """

# --- Índice de atención (PROPIO, no es el score Chi) ------------------------
# Datos crudos por vaca de los últimos días: producción real vs. esperada,
# conductividad, y la referencia de los 7 días previos. Con esto se arma en
# Python el índice (ver calcular_atencion), para poder explicar cada motivo.
DIAS_ATENCION = 3          # días COMPLETOS de ordeño a mirar hacia atrás
def sql_atencion_datos(grupos_sql: str) -> str:
    return f"""
        WITH ancla AS ({_ANCLA}),
        reciente AS (
          SELECT s.BasicAnimal,
                 SUM(s.TotalYield) AS kg_real,
                 SUM(s.ExpectedYield) AS kg_esperado,
                 MAX(s.RelativeConductivity) AS cond_max,
                 AVG(CAST(s.RelativeConductivity AS float)) AS cond_prom,
                 MAX(s.BeginTime) AS ultimo_ordeno,
                 COUNT(*) AS ordenos
          FROM SessionMilkYield s CROSS JOIN ancla
          -- Solo días COMPLETOS: el último día cargado suele estar a medio ordeñar
          -- y compararlo contra la expectativa del día entero da caídas falsas.
          WHERE CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -{DIAS_ATENCION - 1}, ancla.d) AND ancla.d
            AND s.TotalYield IS NOT NULL
          GROUP BY s.BasicAnimal
        ),
        dia AS (
          SELECT ad.BasicAnimal, ad.TotalYield AS kg_dia, ad.AvgYieldPrev7d AS kg_prev7,
                 ad.DIM, ad.LactationNumber,
                 ROW_NUMBER() OVER (PARTITION BY ad.BasicAnimal ORDER BY ad.Date DESC) AS rn
          FROM AnimalDaily ad CROSS JOIN ancla
          WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
            AND ad.Date BETWEEN DATEADD(day, -10, ancla.d) AND ancla.d
        )
        SELECT b.Number AS rp, g.Name AS grupo, d.DIM AS del, d.LactationNumber AS lactancia,
               r.kg_real, r.kg_esperado, r.cond_max, r.cond_prom, r.ordenos,
               d.kg_dia, d.kg_prev7,
               CONVERT(varchar(16), r.ultimo_ordeno, 120) AS ultimo_ordeno
        FROM reciente r
        JOIN BasicAnimal b ON b.OID = r.BasicAnimal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        LEFT JOIN dia d ON d.BasicAnimal = r.BasicAnimal AND d.rn = 1
        WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def calcular_atencion(columns, rows, top: int = TOP_ATENCION) -> list:
    """Índice de atención PROPIO (0-10), con el motivo de cada punto.

    No reproduce el score del add-on Chi (su cálculo es interno y no está en
    la base): usa las señales que el propio reporte declara, con pesos
    elegidos acá. Sirve para priorizar a qué vaca mirar, pero sus números no
    son comparables con los del reporte original.
    """
    idx = {c: i for i, c in enumerate(columns)}
    fichas = []
    for r in rows:
        motivos, score = [], 0.0

        # 1) Produce menos de lo esperado para su curva de lactancia.
        real, esperado = r[idx["kg_real"]], r[idx["kg_esperado"]]
        pct_esperado = None
        if real is not None and esperado:
            pct_esperado = round(100 * real / esperado)
            if pct_esperado < 85:
                # cada 10 puntos por debajo de 85% suma ~1 punto (tope 4)
                score += min((85 - pct_esperado) / 10.0, 4.0)
                motivos.append("Caída de leche")

        # 2) Conductividad alta: sospecha de mastitis.
        cond = r[idx["cond_max"]]
        if cond and cond > COND_ALTA:
            score += min((cond - COND_ALTA) / 10.0, 3.0)
            motivos.append("Conductividad")

        # 3) Viene cayendo respecto de su propio promedio de los 7 días previos.
        kg_dia, kg_prev7 = r[idx["kg_dia"]], r[idx["kg_prev7"]]
        caida_pct = None
        if kg_dia is not None and kg_prev7:
            caida_pct = round(100 * (kg_dia - kg_prev7) / kg_prev7)
            if caida_pct < -15:
                score += min(abs(caida_pct + 15) / 10.0, 3.0)
                motivos.append("Tendencia de caída de leche")

        if not motivos:
            continue
        fichas.append({
            "rp": r[idx["rp"]], "grupo": r[idx["grupo"]], "del": r[idx["del"]],
            "lactancia": r[idx["lactancia"]], "score": round(score, 2),
            "motivos": motivos, "pct_esperado": pct_esperado, "caida_pct": caida_pct,
            "conductividad": round(cond) if cond else None,
            "kg_dia": round(kg_dia, 1) if kg_dia is not None else None,
            "ultimo_ordeno": r[idx["ultimo_ordeno"]],
        })
    fichas.sort(key=lambda f: -f["score"])
    return fichas[:top]


# --- Atención vacas v2: índice multi-sistema EXPERIMENTAL -------------------
# Construido y afinado en una sesión de trabajo con backtest contra 568 casos
# reales de mastitis y 79 de metritis de este mismo tambo (3 meses). Quedó
# demostrado que las señales individuales SÍ son reales (caída de leche vs el
# propio baseline, LowYieldAlarm, ConductivityAlarm, BCS para metritis), pero
# la comparación rigurosa (igual % de falsos positivos) mostró que combinarlas
# en un único score, con la fórmula que se probó, NO supera de forma
# concluyente al índice simple de abajo. Por eso se muestra separado y
# rotulado como EXPERIMENTAL — el objetivo es validarlo con evidencia real de
# campo (¿las vacas que marca son las que el veterinario/operario también
# encuentran?), no reemplazar el índice de arriba todavía.
#
# Ventanas: R = últimos 3 días (incluye hoy) · B = 3 días de referencia, con
# 4 días de hueco antes de R (así el propio proceso de la enfermedad no
# contamina el baseline). Es la misma ventana con la que se midió el lift
# de 4,16x en el backtest.
#
# De las señales de acá, SOLO DOS son propias del controlador de una rotativa:
# LowYieldAlarm y ConductivityAlarm (tabla CMSMilkYield). El resto (caída de
# leche, ratio esperado y conductividad de sesión, de SessionMilkYield; BCS,
# de BcsDailyData) sale de tablas comunes a cualquier tipo de sala. Por eso
# `con_alarmas_rotativa` gatilla SOLO esas dos columnas — en sala convencional
# se arma la misma consulta con `CAST(NULL AS bit)` en su lugar, en vez de
# tirar `db.TablaNoDisponibleError` para el índice entero como pasaba antes.
# El filtro de grupos ahora sí usa `_grupos_subquery` como el resto del
# módulo (antes hardcodeaba `CMSGroupMilkSetting`, que tampoco existe en
# convencional) — ver api_salud_atencion_v2 en app.py para quién decide
# `con_alarmas_rotativa` (tambos.tipo_sala(tambo) == "rotativa").
#
# `con_bcs` es el MISMO caso, para OTRA tabla: `BcsDailyData` existe solo si
# el tambo tiene la cámara BCS instalada, y eso NO depende del tipo de sala
# (ver la nota de `sql_bcs_vacas`) — San José (convencional, sin cámara) lo
# dejó en evidencia: sin este `if`, faltar esa UNA tabla tiraba
# `TablaNoDisponibleError` para el índice ENTERO, aunque caída de leche y
# conductividad —lo que sí tiene esa base— estuvieran perfectas. `app.py`
# decide `con_bcs` chequeando `OBJECT_ID('BcsDailyData')` una vez por proceso
# (ver `_tiene_bcs_de`), no por tipo de sala.
def sql_atencion_v2(grupos_sql: str, con_alarmas_rotativa: bool, con_bcs: bool = True) -> str:
    if con_alarmas_rotativa:
        columnas_alarma = "c.LowYieldAlarm, c.ConductivityAlarm"
        join_alarma = "JOIN CMSMilkYield c ON c.OID = s.OID"
    else:
        columnas_alarma = ("CAST(NULL AS bit) AS LowYieldAlarm, "
                            "CAST(NULL AS bit) AS ConductivityAlarm")
        join_alarma = ""
    if con_bcs:
        cte_bcs = """,
        bcs AS (
          SELECT Animal, TrendValueFourWeeks, TrendValueTwoWeeks,
                 ROW_NUMBER() OVER (PARTITION BY Animal ORDER BY DateAndTime DESC) AS rn
          FROM BcsDailyData
          WHERE DateAndTime >= DATEADD(day, -10, GETDATE())
        )"""
        columnas_bcs = "bc.TrendValueFourWeeks, bc.TrendValueTwoWeeks"
        join_bcs = "LEFT JOIN bcs bc ON bc.Animal = a.BasicAnimal AND bc.rn = 1"
    else:
        cte_bcs = ""
        columnas_bcs = ("CAST(NULL AS float) AS TrendValueFourWeeks, "
                        "CAST(NULL AS float) AS TrendValueTwoWeeks")
        join_bcs = ""
    return f"""
        WITH ancla AS ({_ANCLA}),
        ses AS (
          SELECT s.BasicAnimal,
                 CASE WHEN CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -2, ancla.d) AND ancla.d THEN 'R'
                      WHEN CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -9, ancla.d) AND DATEADD(day, -7, ancla.d) THEN 'B'
                 END AS ventana,
                 s.TotalYield, s.ExpectedYield, s.RelativeConductivity, s.MaxBlood,
                 {columnas_alarma}
          FROM SessionMilkYield s CROSS JOIN ancla
          {join_alarma}
          JOIN BasicAnimal b ON b.OID = s.BasicAnimal
          JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
          WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
            AND s.BeginTime >= DATEADD(day, -9, ancla.d) AND CAST(s.BeginTime AS date) <= ancla.d
        ),
        agg AS (
          SELECT BasicAnimal,
            COUNT(CASE WHEN ventana = 'R' THEN 1 END) AS n_ses_r,
            AVG(CASE WHEN ventana = 'R' THEN TotalYield END) AS kg_r,
            COUNT(CASE WHEN ventana = 'B' THEN 1 END) AS n_ses_b,
            AVG(CASE WHEN ventana = 'B' THEN TotalYield END) AS kg_b,
            AVG(CASE WHEN ventana = 'R' THEN CAST(LowYieldAlarm AS float) END) AS tasa_lya_r,
            AVG(CASE WHEN ventana = 'B' THEN CAST(LowYieldAlarm AS float) END) AS tasa_lya_b,
            AVG(CASE WHEN ventana = 'R' AND ExpectedYield >= 1 THEN TotalYield / ExpectedYield END) AS ratio_r,
            AVG(CASE WHEN ventana = 'B' AND ExpectedYield >= 1 THEN TotalYield / ExpectedYield END) AS ratio_b,
            MAX(CASE WHEN ventana = 'R' AND RelativeConductivity > 0 THEN RelativeConductivity END) AS cond_max_r,
            MAX(CASE WHEN ventana = 'B' AND RelativeConductivity > 0 THEN RelativeConductivity END) AS cond_max_b,
            MAX(CASE WHEN ventana = 'R' THEN MaxBlood END) AS blood_max_r,
            AVG(CASE WHEN ventana = 'B' THEN CAST(MaxBlood AS float) END) AS blood_avg_b,
            SUM(CASE WHEN ventana = 'R' AND ConductivityAlarm = 1 THEN 1 ELSE 0 END) AS n_alarmas_r
          FROM ses
          WHERE ventana IS NOT NULL
          GROUP BY BasicAnimal
        ){cte_bcs},
        dia AS (
          SELECT ad.BasicAnimal, ad.DIM, ad.LactationNumber,
                 ROW_NUMBER() OVER (PARTITION BY ad.BasicAnimal ORDER BY ad.Date DESC) AS rn
          FROM AnimalDaily ad CROSS JOIN ancla
          WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1
            AND ad.Date BETWEEN DATEADD(day, -15, ancla.d) AND ancla.d
        )
        SELECT b.Number AS rp, g.Name AS grupo, d.DIM AS del, d.LactationNumber AS lactancia,
               a.n_ses_r, a.kg_r, a.n_ses_b, a.kg_b, a.tasa_lya_r, a.tasa_lya_b,
               a.ratio_r, a.ratio_b, a.cond_max_r, a.cond_max_b, a.blood_max_r, a.blood_avg_b,
               a.n_alarmas_r, {columnas_bcs},
               pd.FatherId AS padre, pd.MotherId AS madre
        FROM agg a
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        LEFT JOIN dia d ON d.BasicAnimal = a.BasicAnimal AND d.rn = 1
        {join_bcs}
        -- Padre de la vaca, para el riesgo genético (ver genetica.py). LEFT
        -- JOIN: 76 de 7.314 vacas activas no tienen padre cargado y NO deben
        -- desaparecer del índice por eso -- salen sin dato genético, nada más.
        LEFT JOIN PedigreeInfo pd ON pd.OID = b.PedigreeInfo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# Cuánto puede mover la genética el ORDEN de la lista, en puntos de score
# (que va de 0 a 10). Con 0,3 un riesgo genético del 100% adelanta a la vaca
# como máximo 0,3 puntos: alcanza para desempatar entre vacas que hoy dan una
# señal parecida, y no alcanza para tapar una diferencia real de evidencia.
#
# POR QUÉ TAN POCO, y por qué NO entra al score:
#   * La heredabilidad de estos rasgos es baja (mastitis ~0,03-0,12), y del
#     catálogo solo se conoce al PADRE: la mitad de la genética de la vaca.
#   * La genética NO CAMBIA día a día. Si pesara fuerte, las mismas vacas
#     encabezarían la lista para siempre pase lo que pase, y "Atención vacas"
#     dejaría de servir para lo único que sirve: decir a quién mirar HOY.
#   * El umbral de entrada (score >= 2.0) se aplica al score CRUDO, antes del
#     ajuste: la genética nunca mete una vaca a la lista, solo reordena las que
#     ya entraron por evidencia propia.
# El `score` que se muestra y se compara con el backtest queda intacto.
PESO_GENETICA = 0.3


def _texto_herencia(gen, padre, madre) -> str:
    """Explicación en criollo del riesgo heredado, diciendo con qué ramas se
    calculó — para que no se lea un "solo padre" como si fuera padre+madre."""
    if not gen or gen.get("riesgo") is None:
        falta = []
        if not padre:
            falta.append("padre")
        if not madre:
            falta.append("madre")
        if falta:
            return f"Sin {' ni '.join(falta)} cargado en DelPro."
        return (f"Ni el padre ({padre}) está en el catálogo de toros, ni la madre "
                f"(RP {madre}) tiene historia clínica registrada.")
    partes = [f"{gen['riesgo']} sobre 100 ({gen['ramas']})"]
    if gen.get("riesgo_padre") is not None:
        partes.append(f"padre {padre}: {round(gen['riesgo_padre'])}")
    if gen.get("riesgo_madre") is not None:
        det = f"madre RP {gen['madre_rp']}: {round(gen['riesgo_madre'])}"
        m, t = gen.get("madre_mastitis") or 0, gen.get("madre_metritis") or 0
        if m or t:
            det += f" ({m} mastitis, {t} metritis en {gen.get('madre_anios')} años)"
        else:
            det += " (sin enfermedades registradas)"
        partes.append(det)
    return " · ".join(partes)


def calcular_atencion_v2(columns, rows, top: int = TOP_ATENCION,
                         genetica_fn=None) -> list:
    """Índice EXPERIMENTAL multi-sistema (UBRE / METABÓLICO / SISTÉMICO), ver
    la nota arriba de sql_atencion_v2. Score 0-10 por sistema (noisy-OR), se
    muestra el peor sistema y la evidencia de cada uno en criollo.

    `genetica_fn(padre) -> dict | None`: riesgo genético del padre de la vaca
    (ver `genetica.de_toro`). Se inyecta desde app.py para no acoplar este
    módulo a la lectura del Excel. None = sin genética, y el índice queda
    exactamente como antes. Ver `PESO_GENETICA` para qué hace y qué NO hace."""
    idx = {c: i for i, c in enumerate(columns)}
    fichas = []
    for r in rows:
        g = lambda campo: r[idx[campo]]  # noqa: E731

        e_terms = []
        kg_r, kg_b = g("kg_r"), g("kg_b")
        if kg_r is not None and kg_b:
            drop_pct = 100 * (kg_r - kg_b) / kg_b
            e_terms.append(("Caída de leche", _clip((-drop_pct - 6) / 16), drop_pct,
                             f"{kg_r:.1f} kg/ordeño (venía en {kg_b:.1f}, {drop_pct:+.0f}%)"))
        tasa_r, tasa_b = g("tasa_lya_r"), g("tasa_lya_b")
        if (g("n_ses_r") and g("n_ses_r") >= 2 and g("n_ses_b") and g("n_ses_b") >= 2
                and tasa_r is not None and tasa_b is not None):
            # tasa_lya_* viene NULL en sala convencional (no tiene CMSMilkYield,
            # ver sql_atencion_v2) — se excluye el término, nunca se trata como
            # evidencia negativa, mismo criterio que el resto del motor.
            e_lya = _clip((tasa_r - max(tasa_b, 0.05) - 0.05) / 0.35)
            e_terms.append(("Alarma de bajo rendimiento", e_lya, tasa_r,
                             f"{round(tasa_r * g('n_ses_r'))} de {g('n_ses_r')} ordeños con alarma"))
        ratio_r, ratio_b = g("ratio_r"), g("ratio_b")
        if ratio_r is not None and ratio_b:
            e_exp = _clip((0.93 * ratio_b - ratio_r) / (0.20 * ratio_b))
            e_terms.append(("Por debajo de lo esperado", e_exp, ratio_r,
                             f"{round(100*ratio_r)}% de lo esperado (su normal: {round(100*ratio_b)}%)"))
        E_LECHE, motivo_leche = None, None
        if e_terms:
            e_terms.sort(key=lambda t: -t[1])
            E_LECHE = min(1.0, e_terms[0][1] + 0.15 * sum(1 for t in e_terms[1:] if t[1] >= 0.40))
            motivo_leche = e_terms[0]

        def noisy_or(pares):
            disp = [(p, e) for p, e in pares if e is not None]
            if not disp:
                return None
            prod = 1.0
            for p, e in disp:
                prod *= (1 - p * e)
            return 10 * (1 - prod)

        cond_max_r, cond_max_b = g("cond_max_r"), g("cond_max_b")
        e_cond = None
        motivos_ubre = []
        if cond_max_r:
            e_abs = _clip((cond_max_r - 112) / 13)
            e_rel = _clip((cond_max_r - (cond_max_b or cond_max_r) - 5) / 10)
            e_cond = max(e_abs, e_rel)
            if e_cond > 0.3:
                motivos_ubre.append(("Conductividad alta", e_cond,
                                      f"pico de {round(cond_max_r)} (su normal: {round(cond_max_b) if cond_max_b else '—'})"))
        n_alarmas = g("n_alarmas_r")
        e_alarm = min(1.0, (n_alarmas or 0) / 2)
        if e_alarm > 0.3:
            motivos_ubre.append(("Alarma de conductividad del equipo", e_alarm,
                                  f"{n_alarmas} ordeño(s) con alarma en 3 días"))
        if E_LECHE and E_LECHE > 0.3:
            motivos_ubre.append((motivo_leche[0], 0.20 * E_LECHE, motivo_leche[3]))
        S_ubre = noisy_or([(0.35, e_alarm), (0.35, e_cond), (0.20, E_LECHE)])

        trend4, trend2 = g("TrendValueFourWeeks"), g("TrendValueTwoWeeks")
        e_bcs, motivo_bcs = None, None
        if trend4 is not None:
            e_bcs = _clip((-trend4 - 0.15) / 0.35)
            motivo_bcs = f"perdió {abs(trend4):.2f} puntos de condición en 4 semanas"
        elif trend2 is not None:
            e_bcs = _clip((-trend2 - 0.10) / 0.25)
            motivo_bcs = f"perdió {abs(trend2):.2f} puntos de condición en 2 semanas"
        S_metab = noisy_or([(0.40, e_bcs), (0.20, E_LECHE)])

        # Ordeños perdidos: contra el PROPIO historial de la vaca (n_ses_b, la
        # misma ventana de 3 días de referencia), no un número fijo -- este
        # tambo ordeña 3 veces/día, otro tambo podría ordeñar 2.
        e_s2, n_esperado = None, None
        n_ses_b = g("n_ses_b")
        if n_ses_b and n_ses_b >= 2:
            n_esperado = n_ses_b
            e_s2 = _clip((n_ses_b - (g("n_ses_r") or 0) - 1) / 3)
        S_sist = noisy_or([(0.55, E_LECHE), (0.35, e_s2)])

        candidatos = {"UBRE": S_ubre, "METABÓLICO": S_metab, "GENERAL": S_sist}
        candidatos = {k: v for k, v in candidatos.items() if v is not None}
        if not candidatos:
            continue
        sistema_principal = max(candidatos, key=candidatos.get)
        score = candidatos[sistema_principal]
        if score < 2.0:
            continue

        motivos = []
        if sistema_principal == "UBRE":
            motivos = [{"texto": t[0] + ": " + t[2]} for t in
                       sorted(motivos_ubre, key=lambda x: -x[1])[:3]]
        elif sistema_principal == "METABÓLICO" and motivo_bcs:
            motivos = [{"texto": "Caída de condición corporal: " + motivo_bcs}]
            if E_LECHE and E_LECHE > 0.3:
                motivos.append({"texto": motivo_leche[0] + ": " + motivo_leche[3]})
        elif sistema_principal == "GENERAL":
            motivos = []
            if motivo_leche:
                motivos.append({"texto": motivo_leche[0] + ": " + motivo_leche[3]})
            if e_s2 and e_s2 > 0.3:
                motivos.append({"texto": f"Faltó a ordeños: {g('n_ses_r') or 0} de ~{n_esperado} esperados en 3 días (su normal)"})
        if not motivos:
            continue

        # Riesgo genético del padre: CONTEXTO, no evidencia de hoy. No entra al
        # score (que ya quedó fijado arriba, y el umbral de entrada ya se
        # aplicó), solo desplaza el orden hasta PESO_GENETICA puntos.
        padre = g("padre") if "padre" in idx else None
        padre = padre.strip() if isinstance(padre, str) else None
        madre = g("madre") if "madre" in idx else None
        madre = madre.strip() if isinstance(madre, str) else None
        # `genetica_fn(padre, madre)` -> riesgo heredado combinado (mitad
        # padre por catálogo genético, mitad madre por su historia clínica).
        # Ver herencia.py; app.py es quien arma la función.
        gen = genetica_fn(padre, madre) if genetica_fn and (padre or madre) else None
        riesgo_gen = gen.get("riesgo") if gen else None

        # --- Desglose para los vúmetros de la tarjeta -----------------------
        # Cada término con su evidencia 0-1, el valor crudo en criollo, y a qué
        # sistema(s) alimenta con qué peso. Es EXACTAMENTE lo que ya se usó
        # arriba para calcular: acá no se recalcula nada, solo se expone, para
        # que la pantalla pueda mostrar de dónde sale el score en vez de un
        # número suelto. `evidencia: None` = sin dato (no es evidencia cero).
        por_label = {t[0]: t for t in e_terms}

        def _p(clave, label, ev, texto, sistemas, sin_dato):
            return {"clave": clave, "label": label,
                    "evidencia": round(ev, 3) if ev is not None else None,
                    "texto": texto if ev is not None else sin_dato,
                    "sistemas": sistemas}

        def _sub(clave, label, sin_dato):
            """Sub-término de leche: no alimenta un sistema directo, primero se
            combina en `E_LECHE` (ver más abajo) y ESE va a los sistemas."""
            t = por_label.get(label)
            p = _p(clave, label, t[1] if t else None, t[3] if t else None, {}, sin_dato)
            p["via"] = "leche"
            return p

        parametros = [
            _sub("leche_caida", "Caída de leche",
                 "Sin dato: falta el promedio reciente o el de referencia."),
            _sub("leche_alarma", "Alarma de bajo rendimiento",
                 "No disponible en esta sala (es del controlador de la rotativa)."),
            _sub("leche_esperada", "Por debajo de lo esperado",
                 "Sin dato: no hay producción esperada para comparar."),
            _p("leche", "Evidencia de leche (combinada)", E_LECHE,
               (motivo_leche[3] if motivo_leche else None),
               {"UBRE": 0.20, "METABÓLICO": 0.20, "GENERAL": 0.55},
               "Sin ninguna señal de leche disponible."),
            _p("conductividad", "Conductividad", e_cond,
               (f"pico de {round(cond_max_r)}"
                + (f" (su normal: {round(cond_max_b)})" if cond_max_b else "")) if cond_max_r else None,
               {"UBRE": 0.35}, "Sin lectura de conductividad en la ventana."),
            _p("alarma_equipo", "Alarma de conductividad del equipo",
               e_alarm if n_alarmas is not None else None,
               f"{n_alarmas or 0} ordeño(s) con alarma en 3 días",
               {"UBRE": 0.35}, "No disponible en esta sala."),
            _p("bcs", "Condición corporal (BCS)", e_bcs, motivo_bcs,
               {"METABÓLICO": 0.40},
               "Sin cámara BCS o sin lectura reciente."),
            _p("ordenos_perdidos", "Ordeños perdidos", e_s2,
               (f"{g('n_ses_r') or 0} de ~{n_esperado} esperados en 3 días"
                if n_esperado else None),
               {"GENERAL": 0.35}, "Sin historial suficiente para saber su normal."),
            # La herencia va última y marcada `contexto`: NO entra al score.
            {"clave": "genetica", "label": "Riesgo heredado (padre + madre)",
             "evidencia": round(riesgo_gen / 100.0, 3) if riesgo_gen is not None else None,
             "texto": _texto_herencia(gen, padre, madre),
             "sistemas": {}, "contexto": True,
             "simulado": bool(gen.get("simulado")) if gen else False},
        ]

        fichas.append({
            "rp": g("rp"), "grupo": g("grupo"), "del": g("del"), "lactancia": g("lactancia"),
            "score": round(score, 1), "sistema": sistema_principal, "motivos": motivos,
            "S_ubre": round(S_ubre, 1) if S_ubre is not None else None,
            "S_metab": round(S_metab, 1) if S_metab is not None else None,
            "S_sist": round(S_sist, 1) if S_sist is not None else None,
            "padre": padre,
            "madre": madre,
            "riesgo_genetico": riesgo_gen,
            "riesgo_padre": gen.get("riesgo_padre") if gen else None,
            "riesgo_madre": gen.get("riesgo_madre") if gen else None,
            "ramas_herencia": gen.get("ramas") if gen else None,
            "detalle_padre": gen.get("detalle_padre") if gen else [],
            "detalle_madre": gen.get("detalle_madre") if gen else [],
            "parametros": parametros,
            # True = el riesgo sale de datos FICTICIOS de prueba. La pantalla
            # TIENE que decirlo: nadie puede descartar una vaca por esto.
            "genetica_simulada": bool(gen.get("simulado")) if gen else False,
            "_orden": score + PESO_GENETICA * (riesgo_gen / 100.0 if riesgo_gen is not None else 0),
        })
    # Se ordena por el score ajustado, pero el que se muestra es el crudo.
    fichas.sort(key=lambda f: -f["_orden"])
    for f in fichas:
        del f["_orden"]
    return fichas[:top]


# --- Sección "Estadística de producción de leche" ---------------------------
# Producción por ordeño de cada rodeo, día por día, para calcular después en
# Python la tendencia (día vs día anterior) y comparar semanas.
def sql_produccion_por_rodeo(grupos_sql: str) -> str:
    return f"""
        WITH ancla AS ({_ANCLA})
        SELECT CAST(s.BeginTime AS date) AS fecha, g.Name AS grupo,
               AVG(s.TotalYield) AS kg_por_ordeno,
               AVG(CAST(s.RelativeConductivity AS float)) AS cond_promedio,
               COUNT(*) AS ordenos
        FROM SessionMilkYield s CROSS JOIN ancla
        JOIN BasicAnimal b ON b.OID = s.BasicAnimal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN ({_grupos_subquery(grupos_sql)}) gr ON gr.grupo = b.[Group]
        WHERE CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -{DIAS_CONDUCTIVIDAD}, ancla.d) AND ancla.d
          AND s.TotalYield > 0 AND b.Number > 0
        GROUP BY CAST(s.BeginTime AS date), g.Name
        ORDER BY fecha, grupo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """

DIAS_DETALLE_RODEO = 6     # días sueltos que se muestran antes de los promedios


def _variacion_pct(actual, previo):
    if actual is None or not previo:
        return None
    return round(100 * (actual - previo) / previo, 1)


def resumen_por_rodeo(columns, rows) -> list:
    """Arma la tabla "Estadística de producción de leche": por cada rodeo, la
    producción por ordeño de los últimos días sueltos + los promedios de la
    semana pasada y la previa, cada uno con su variación.

    La variación se calcula contra el período inmediatamente anterior de la
    misma duración (día vs. día anterior, semana vs. semana previa), que es la
    lectura útil: "¿venimos mejor o peor que recién?"."""
    idx = {c: i for i, c in enumerate(columns)}
    por_grupo: dict = {}
    for r in rows:
        g = r[idx["grupo"]]
        por_grupo.setdefault(g, {})[str(r[idx["fecha"]])[:10]] = {
            "kg": r[idx["kg_por_ordeno"]], "cond": r[idx["cond_promedio"]],
            "ordenos": r[idx["ordenos"]],
        }

    salida = []
    for grupo, dias in sorted(por_grupo.items()):
        # Se descartan los días a medio cargar (el último día suele tener solo
        # una parte de los ordeños): mezclarlos daría tendencias falsas, como
        # un +10% que en realidad es "todavía no terminó de ordeñarse el día".
        cuentas = sorted(d["ordenos"] or 0 for d in dias.values())
        if cuentas:
            tipico = cuentas[len(cuentas) // 2]
            dias = {f: d for f, d in dias.items()
                    if (d["ordenos"] or 0) >= tipico * 0.6}
        if not dias:
            continue
        fechas = sorted(dias, reverse=True)
        filas = []
        # Días sueltos, cada uno comparado con el día anterior.
        for i, f in enumerate(fechas[:DIAS_DETALLE_RODEO]):
            hoy_d, ayer_d = dias[f], dias.get(fechas[i + 1]) if i + 1 < len(fechas) else None
            etiqueta = "Últimas 24 h" if i == 0 else ("Ayer" if i == 1 else f)
            filas.append({
                "periodo": etiqueta, "fecha": f,
                "kg_por_ordeno": round(hoy_d["kg"], 2) if hoy_d["kg"] is not None else None,
                "ordenos": hoy_d["ordenos"],
                "var_leche": _variacion_pct(hoy_d["kg"], ayer_d["kg"] if ayer_d else None),
                "var_cond": _variacion_pct(hoy_d["cond"], ayer_d["cond"] if ayer_d else None),
            })

        # Promedios semanales (última semana vs. la previa).
        def _prom(lista, clave):
            vals = [dias[f][clave] for f in lista if dias[f][clave] is not None]
            return sum(vals) / len(vals) if vals else None

        sem1, sem2 = fechas[:7], fechas[7:14]
        if sem1:
            filas.append({
                "periodo": "Semana pasada", "fecha": None,
                "kg_por_ordeno": round(_prom(sem1, "kg"), 2) if _prom(sem1, "kg") else None,
                "ordenos": None,
                "var_leche": _variacion_pct(_prom(sem1, "kg"), _prom(sem2, "kg")),
                "var_cond": _variacion_pct(_prom(sem1, "cond"), _prom(sem2, "cond")),
            })
        if sem2:
            sem3 = fechas[14:21]
            filas.append({
                "periodo": "Semana previa", "fecha": None,
                "kg_por_ordeno": round(_prom(sem2, "kg"), 2) if _prom(sem2, "kg") else None,
                "ordenos": None,
                "var_leche": _variacion_pct(_prom(sem2, "kg"), _prom(sem3, "kg")),
                "var_cond": _variacion_pct(_prom(sem2, "cond"), _prom(sem3, "cond")),
            })
        salida.append({"grupo": grupo, "filas": filas})
    return salida
