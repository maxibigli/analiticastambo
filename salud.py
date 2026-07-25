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
SQL_RCS_POR_GRUPO = f"""
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
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
    GROUP BY g.Name, g.Number
    ORDER BY g.Number
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""

# --- Secciones "Vacas con RCS altos" y "Crónicas" ---------------------------
# `cronica` = superó el umbral en el último control Y en el anterior.
SQL_RCS_VACAS = f"""
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
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
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
# BCS_BAJO/BCS_ALTO son umbrales GENERALES de manejo lechero (no un dato propio
# de DelPro: no tenemos su curva "objetivo" interna) — el usuario los puede
# mover libremente en la pantalla.
BCS_BAJO = 2.5   # por debajo: vaca flaca, riesgo de cetosis/fertilidad
BCS_ALTO = 4.25  # por encima: vaca engrasada, riesgo de parto/metabólico

SQL_BCS_VACAS = f"""
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
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
    LEFT JOIN dia d ON d.BasicAnimal = bc.Animal AND d.rn = 1
    LEFT JOIN AnimalReproductionInfo r ON r.Animal = bc.Animal AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0 AND bc.rn = 1
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""


# --- Sección "Vista del rebaño (conductividad)" -----------------------------
# Promedio diario de conductividad relativa por grupo. >115 es sospecha de
# mastitis, así que la curva por rodeo muestra si un lote se está yendo.
SQL_CONDUCTIVIDAD_REBANIO = f"""
    WITH ancla AS ({_ANCLA})
    SELECT CAST(s.BeginTime AS date) AS fecha, g.Name AS grupo,
           AVG(CAST(s.RelativeConductivity AS float)) AS cond_promedio,
           SUM(CASE WHEN s.RelativeConductivity > {COND_ALTA} THEN 1 ELSE 0 END) AS vacas_altas,
           COUNT(*) AS ordenos
    FROM SessionMilkYield s CROSS JOIN ancla
    JOIN BasicAnimal b ON b.OID = s.BasicAnimal
    JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
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
SQL_ATENCION_DATOS = f"""
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
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
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
SQL_ATENCION_V2 = f"""
    WITH ancla AS ({_ANCLA}),
    ses AS (
      SELECT s.BasicAnimal,
             CASE WHEN CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -2, ancla.d) AND ancla.d THEN 'R'
                  WHEN CAST(s.BeginTime AS date) BETWEEN DATEADD(day, -9, ancla.d) AND DATEADD(day, -7, ancla.d) THEN 'B'
             END AS ventana,
             s.TotalYield, s.ExpectedYield, s.RelativeConductivity, s.MaxBlood,
             c.LowYieldAlarm, c.ConductivityAlarm
      FROM SessionMilkYield s CROSS JOIN ancla
      JOIN CMSMilkYield c ON c.OID = s.OID
      JOIN BasicAnimal b ON b.OID = s.BasicAnimal
      JOIN CMSGroupMilkSetting g ON g.[Group] = b.[Group] AND g.GCRecord IS NULL AND g.EnableMilking = 1
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
    ),
    bcs AS (
      SELECT Animal, TrendValueFourWeeks, TrendValueTwoWeeks,
             ROW_NUMBER() OVER (PARTITION BY Animal ORDER BY DateAndTime DESC) AS rn
      FROM BcsDailyData
      WHERE DateAndTime >= DATEADD(day, -10, GETDATE())
    ),
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
           a.n_alarmas_r, bc.TrendValueFourWeeks, bc.TrendValueTwoWeeks
    FROM agg a
    JOIN BasicAnimal b ON b.OID = a.BasicAnimal
    JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
    LEFT JOIN dia d ON d.BasicAnimal = a.BasicAnimal AND d.rn = 1
    LEFT JOIN bcs bc ON bc.Animal = a.BasicAnimal AND bc.rn = 1
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
"""


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def calcular_atencion_v2(columns, rows, top: int = TOP_ATENCION) -> list:
    """Índice EXPERIMENTAL multi-sistema (UBRE / METABÓLICO / SISTÉMICO), ver
    la nota arriba de SQL_ATENCION_V2. Score 0-10 por sistema (noisy-OR), se
    muestra el peor sistema y la evidencia de cada uno en criollo."""
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
        if g("n_ses_r") and g("n_ses_r") >= 2 and g("n_ses_b") and g("n_ses_b") >= 2:
            tasa_r, tasa_b = g("tasa_lya_r"), g("tasa_lya_b")
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

        fichas.append({
            "rp": g("rp"), "grupo": g("grupo"), "del": g("del"), "lactancia": g("lactancia"),
            "score": round(score, 1), "sistema": sistema_principal, "motivos": motivos,
            "S_ubre": round(S_ubre, 1) if S_ubre is not None else None,
            "S_metab": round(S_metab, 1) if S_metab is not None else None,
            "S_sist": round(S_sist, 1) if S_sist is not None else None,
        })
    fichas.sort(key=lambda f: -f["score"])
    return fichas[:top]


# --- Sección "Estadística de producción de leche" ---------------------------
# Producción por ordeño de cada rodeo, día por día, para calcular después en
# Python la tendencia (día vs día anterior) y comparar semanas.
SQL_PRODUCCION_POR_RODEO = f"""
    WITH ancla AS ({_ANCLA})
    SELECT CAST(s.BeginTime AS date) AS fecha, g.Name AS grupo,
           AVG(s.TotalYield) AS kg_por_ordeno,
           AVG(CAST(s.RelativeConductivity AS float)) AS cond_promedio,
           COUNT(*) AS ordenos
    FROM SessionMilkYield s CROSS JOIN ancla
    JOIN BasicAnimal b ON b.OID = s.BasicAnimal
    JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
    JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                              AND c.EnableMilking = 1
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
