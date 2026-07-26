# -*- coding: utf-8 -*-
"""Análisis de flujos de ordeño (réplica gráfica de los informes de flujo de
DelPro, con rango de fechas amplio y series de líneas).

Todo sale de `CMSMilkYield`, que guarda una fila por ordeño individual con la
curva de flujo ya resumida en cuatro tramos:

    Flow0To15 / Flow15To30 / Flow30To60 / Flow60To120   kg/min promedio del tramo
    AverageFlow / PeakFlow                              kg/min del ordeño entero
    TakeOffFlow                                         kg/min al momento de retirar
    IsoDuration                                         duración del ordeño, en segundos
    LowFlowDurationInSec                                segundos iniciales con flujo bajo
                                                        ("tiempo de colocación")

Verificado contra el informe de Power BI del tambo (11–23/07/2026): los
promedios de los cuatro tramos, el % de retirada forzada y el % sobre el flujo
de retirada máximo coinciden con lo que muestra DelPro.

BIMODALIDAD — la base NO guarda la curva segundo a segundo, solo los cuatro
tramos promediados, así que acá se calcula con dos criterios complementarios:

  * `pct_bimodal`  — criterio estricto: el flujo CAE entre 0-15 s y 15-30 s
    habiendo arrancado (la vaca bajó leche cisternal, se cortó, y volvió).
    Es la definición correcta de bimodalidad, pero con tramos de 15 s se
    pierden las caídas cortas: da bastante más bajo que el número de DelPro,
    que sí ve la curva completa. La TENDENCIA sí es comparable.
  * `pct_arranque_lento` — criterio sensible: casi no hubo leche en los
    primeros 15 s. Es el síntoma de estimulación/preparación insuficiente,
    que es la causa habitual de la bimodalidad.
"""

# Rango máximo de días. Más amplio que el de "Rendimiento Sala" (31) porque
# estas consultas devuelven agregados (una fila por día/grupo/tramo), no las
# visitas una por una — lo que cuesta es el escaneo, no el transporte.
RANGO_FLUJOS_MAX_DIAS = 120

# Umbrales de flujo de retirada (kg/min). Son los mismos que muestra el informe
# de DelPro como tarjetas; se pueden cambiar desde la interfaz.
RETIRADA_MIN_DEFECTO = 0.60
RETIRADA_MAX_DEFECTO = 1.00
RETIRADA_DELPRO_DEFECTO = 0.80   # el valor configurado en la máquina (referencia)

# kg/min en los primeros 15 s por debajo de los cuales se considera que la vaca
# todavía no había bajado la leche cuando se enganchó la pezonera.
ARRANQUE_LENTO_UMBRAL = 0.5

# kg/min mínimos en 0-15 s para que una caída posterior cuente como bimodalidad
# (sin esto, cualquier ordeño que arranca en 0 daría "bimodal" por ruido).
BIMODAL_INICIO_MIN = 0.2

# Tramos de días en ordeño (DEO) del gráfico de bimodalidad.
DEO_BUCKETS = ["0-30", "31-60", "61-90", "91-120", "121-150", "151-200", "201-300", "300+"]

_CASE_DEO = """CASE WHEN d.DIM <= 30 THEN '0-30' WHEN d.DIM <= 60 THEN '31-60'
            WHEN d.DIM <= 90 THEN '61-90' WHEN d.DIM <= 120 THEN '91-120'
            WHEN d.DIM <= 150 THEN '121-150' WHEN d.DIM <= 200 THEN '151-200'
            WHEN d.DIM <= 300 THEN '201-300' ELSE '300+' END"""

# Denominador para los porcentajes de retirada: solo los ordeños que tienen
# lectura de TakeOffFlow (si es NULL no se sabe a qué flujo se retiró, y
# contarlo como "dentro de rango" ensuciaría el indicador).
_CON_RETIRADA = "SUM(CASE WHEN y.TakeOffFlow IS NOT NULL THEN 1 ELSE 0 END)"

_FLUJOS_PROM = """
       AVG(y.Flow0To15)   AS f_0_15,
       AVG(y.Flow15To30)  AS f_15_30,
       AVG(y.Flow30To60)  AS f_30_60,
       AVG(y.Flow60To120) AS f_60_120,
       AVG(y.TakeOffFlow) AS f_retirada"""

# `y.ForcedRetract` y `y.ManualDetach` son bit: no se pueden sumar directo en
# SQL Server, por eso van con CASE WHEN en vez de SUM(...).
_BIMODAL = f"""
       100.0 * SUM(CASE WHEN y.Flow0To15 >= {BIMODAL_INICIO_MIN}
                         AND y.Flow15To30 < y.Flow0To15 THEN 1 ELSE 0 END)
             / COUNT(*) AS pct_bimodal,
       100.0 * SUM(CASE WHEN y.Flow0To15 < {ARRANQUE_LENTO_UMBRAL} THEN 1 ELSE 0 END)
             / COUNT(*) AS pct_arranque_lento"""


def _rango(desde: str, hasta: str) -> str:
    """Filtro de fechas sobre la visita (inclusive en ambos extremos)."""
    return (f"v.CreationTime >= '{desde}' "
            f"AND v.CreationTime < DATEADD(day, 1, '{hasta}')")


def sql_por_dia(desde: str, hasta: str, retirada_min: float, retirada_max: float) -> str:
    """Serie diaria: problemas de retirada + tramos de flujo + rutina.

    `desde`/`hasta`: fechas ISO ya validadas (rango inclusive).
    `retirada_min`/`retirada_max`: umbrales en kg/min, ya convertidos a float.
    """
    return f"""
        SELECT CAST(v.CreationTime AS date) AS fecha,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM},
               AVG(y.AverageFlow) AS f_prom,
               AVG(y.PeakFlow)    AS f_pico,
               AVG(y.IsoDuration * 1.0)          AS dur_seg,
               AVG(y.LowFlowDurationInSec * 1.0) AS coloc_seg,
               100.0 * SUM(CASE WHEN y.TakeOffFlow < {retirada_min} THEN 1 ELSE 0 END)
                     / NULLIF({_CON_RETIRADA}, 0) AS pct_bajo_min,
               100.0 * SUM(CASE WHEN y.TakeOffFlow > {retirada_max} THEN 1 ELSE 0 END)
                     / NULLIF({_CON_RETIRADA}, 0) AS pct_sobre_max,
               100.0 * SUM(CASE WHEN y.ManualMode <> 0 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_manual,
               100.0 * SUM(CASE WHEN y.ManualDetach = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_retiro_manual,
               100.0 * SUM(CASE WHEN y.ForcedRetract = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_forzada,
               {_BIMODAL}
        FROM MilkingDeviceVisit v
        JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
        WHERE {_rango(desde, hasta)}
        GROUP BY CAST(v.CreationTime AS date)
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_por_grupo(desde: str, hasta: str) -> str:
    """Curva de flujo promedio por grupo de ordeñe.

    OJO: usa el grupo ACTUAL del animal (`BasicAnimal.[Group]`), no el que
    tenía el día del ordeño — DelPro tampoco guarda el grupo en la visita. En
    rangos largos, las vacas que cambiaron de grupo cuentan en el nuevo.
    """
    return f"""
        SELECT g.Number AS grupo_num, g.Name AS grupo,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM}
        FROM MilkingDeviceVisit v
        JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
        JOIN SessionMilkYield s ON s.OID = y.OID
        JOIN BasicAnimal b ON b.OID = s.BasicAnimal
        JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        JOIN CMSGroupMilkSetting c ON c.[Group] = b.[Group] AND c.GCRecord IS NULL
                                  AND c.EnableMilking = 1
        WHERE {_rango(desde, hasta)}
          AND b.GCRecord IS NULL
        GROUP BY g.Number, g.Name
        ORDER BY g.Number
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_distribucion(desde: str, hasta: str) -> str:
    """Distribución conjunta de flujo promedio y flujo pico, en cajones de
    1 kg/min (0..10, con 10 = "10 o más").

    Se agrupa por las DOS variables a la vez para resolverlo en un solo
    escaneo: son a lo sumo 11×11 combinaciones, y los dos histogramas se
    separan después en Python.

    Se REDONDEA (no se trunca) para que los cajones coincidan con los del
    informe de DelPro: el cajón "3" son los ordeños de 2,5 a 3,49 kg/min.
    """
    bin_prom = "CASE WHEN y.AverageFlow >= 9.5 THEN 10 ELSE CAST(ROUND(y.AverageFlow, 0) AS int) END"
    bin_pico = "CASE WHEN y.PeakFlow >= 9.5 THEN 10 ELSE CAST(ROUND(y.PeakFlow, 0) AS int) END"
    return f"""
        SELECT {bin_prom} AS bin_prom, {bin_pico} AS bin_pico, COUNT(*) AS n
        FROM MilkingDeviceVisit v
        JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
        WHERE {_rango(desde, hasta)}
          AND y.AverageFlow >= 0 AND y.PeakFlow >= 0
        GROUP BY {bin_prom}, {bin_pico}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_por_deo(desde: str, hasta: str) -> str:
    """Bimodalidad, duración y tiempo de colocación por tramo de días en ordeño."""
    return f"""
        SELECT {_CASE_DEO} AS deo,
               COUNT(*) AS ordenos,
               {_BIMODAL},
               AVG(y.IsoDuration * 1.0)          AS dur_seg,
               AVG(y.LowFlowDurationInSec * 1.0) AS coloc_seg
        FROM MilkingDeviceVisit v
        JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
        JOIN SessionMilkYield s ON s.OID = y.OID
        JOIN AnimalDaily d ON d.OID = s.AnimalDaily
        WHERE {_rango(desde, hasta)}
          AND d.DIM IS NOT NULL
        GROUP BY {_CASE_DEO}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


# --- Armado del resultado ----------------------------------------------------

def _filas(data):
    """Convierte {columns, rows} en una lista de dicts."""
    cols = data["columns"]
    return [dict(zip(cols, row)) for row in data["rows"]]


def _num(v, dec=2):
    return None if v is None else round(float(v), dec)


def analizar(data_dia, data_grupo, data_dist, data_deo,
             retirada_min: float, retirada_max: float) -> dict:
    """Arma el JSON que consume la página, ya redondeado y ordenado."""
    dias = []
    for f in _filas(data_dia):
        dias.append({
            "fecha": str(f["fecha"])[:10],
            "ordenos": int(f["ordenos"] or 0),
            "f_0_15": _num(f["f_0_15"]), "f_15_30": _num(f["f_15_30"]),
            "f_30_60": _num(f["f_30_60"]), "f_60_120": _num(f["f_60_120"]),
            "f_prom": _num(f["f_prom"]), "f_pico": _num(f["f_pico"]),
            "f_retirada": _num(f["f_retirada"]),
            "dur_seg": _num(f["dur_seg"], 0), "coloc_seg": _num(f["coloc_seg"], 1),
            "pct_bajo_min": _num(f["pct_bajo_min"], 1),
            "pct_sobre_max": _num(f["pct_sobre_max"], 1),
            "pct_manual": _num(f["pct_manual"], 2),
            "pct_retiro_manual": _num(f["pct_retiro_manual"], 2),
            "pct_forzada": _num(f["pct_forzada"], 1),
            "pct_bimodal": _num(f["pct_bimodal"], 1),
            "pct_arranque_lento": _num(f["pct_arranque_lento"], 1),
        })

    grupos = [{
        "grupo": (f["grupo"] or f"Grupo {f['grupo_num']}"),
        "ordenos": int(f["ordenos"] or 0),
        "curva": [_num(f["f_0_15"]), _num(f["f_15_30"]),
                  _num(f["f_30_60"]), _num(f["f_60_120"]), _num(f["f_retirada"])],
    } for f in _filas(data_grupo)]

    # Histogramas: los dos salen de la misma tabla de combinaciones.
    prom = [0] * 11
    pico = [0] * 11
    for f in _filas(data_dist):
        n = int(f["n"] or 0)
        bp, bk = f["bin_prom"], f["bin_pico"]
        if bp is not None and 0 <= bp <= 10:
            prom[bp] += n
        if bk is not None and 0 <= bk <= 10:
            pico[bk] += n
    total_p, total_k = sum(prom) or 1, sum(pico) or 1
    distribucion = {
        "bins": [str(i) for i in range(10)] + ["10+"],
        "promedio_pct": [round(100.0 * x / total_p, 1) for x in prom],
        "pico_pct": [round(100.0 * x / total_k, 1) for x in pico],
        "ordenos": sum(prom),
    }

    por_deo = {f["deo"]: f for f in _filas(data_deo)}
    deo = [{
        "deo": b,
        "ordenos": int(por_deo[b]["ordenos"] or 0),
        "pct_bimodal": _num(por_deo[b]["pct_bimodal"], 1),
        "pct_arranque_lento": _num(por_deo[b]["pct_arranque_lento"], 1),
        "dur_seg": _num(por_deo[b]["dur_seg"], 0),
        "coloc_seg": _num(por_deo[b]["coloc_seg"], 1),
    } for b in DEO_BUCKETS if b in por_deo]

    # Totales del período, para las tarjetas de arriba. Se ponderan por
    # cantidad de ordeños: un día de media sesión no puede pesar lo mismo que
    # uno completo.
    total = sum(d["ordenos"] for d in dias)

    def ponderado(clave):
        vals = [(d[clave], d["ordenos"]) for d in dias if d[clave] is not None]
        n = sum(w for _, w in vals)
        return round(sum(v * w for v, w in vals) / n, 2) if n else None

    resumen = {
        "ordenos": total,
        "f_prom": ponderado("f_prom"),
        "f_pico": ponderado("f_pico"),
        "f_retirada": ponderado("f_retirada"),
        "dur_seg": ponderado("dur_seg"),
        "coloc_seg": ponderado("coloc_seg"),
        "pct_bajo_min": ponderado("pct_bajo_min"),
        "pct_sobre_max": ponderado("pct_sobre_max"),
        "pct_forzada": ponderado("pct_forzada"),
        "pct_bimodal": ponderado("pct_bimodal"),
        "pct_arranque_lento": ponderado("pct_arranque_lento"),
    }

    return {
        "dias": dias,
        "grupos": grupos,
        "distribucion": distribucion,
        "deo": deo,
        "resumen": resumen,
        "umbrales": {
            "retirada_min": retirada_min,
            "retirada_max": retirada_max,
            "retirada_delpro": RETIRADA_DELPRO_DEFECTO,
            "arranque_lento": ARRANQUE_LENTO_UMBRAL,
        },
        "tramos": ["0-15 s", "15-30 s", "30-60 s", "60-120 s", "Retirada"],
    }
