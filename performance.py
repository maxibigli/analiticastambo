# -*- coding: utf-8 -*-
"""Performance productiva del rodeo: dos informes.

1. PEAK DE PRODUCCIÓN — la curva de lactancia por número de lactancia, el pico
   que alcanza cada grupo, a qué día lo alcanza, y qué tan rápido cae después.
   Réplica del informe de DelPro, con dos correcciones:

   - DelPro promedia la relación "producción 15-30 DEO vs peak" sobre vacas que
     no tienen peak calculado, y le salen cosas como un "Peak DEO" de 225,7
     días en segunda lactancia. Acá esa relación se calcula VACA POR VACA
     (cada una contra su propio peak) y solo entran las que tienen los dos
     datos, que son el 44% de las lactancias.
   - La persistencia no se toma de una columna: se mide como la caída
     porcentual mensual de la curva desde el pico hacia adelante.

2. DISTRIBUCIÓN DE PRODUCCIÓN — no existe en DelPro; se diseñó acá. Contesta
   "¿de dónde sale la leche?" cruzando, para tres cortes (lactancia, grupo de
   ordeñe y tramo de DEO), el % de VACAS contra el % de KILOS de cada segmento.

   La columna que importa es el ÍNDICE DE RENDIMIENTO: los kg por vaca y por
   día del segmento divididos por los del rodeo entero.
       > 1  el segmento rinde por encima del promedio del rodeo
       = 1  rinde como el promedio
       < 1  ocupa lugar en la sala y produce por debajo

   OJO — la primera versión de este índice usaba %kg / %vacas y estaba MAL: los
   kilos totales de un segmento dependen de cuántos días aportó cada vaca, no
   solo de cuánto da. Un grupo con muchas vacas entradas hace poco acumula
   menos días y quedaba castigado sin merecerlo (Rodeo 1 daba 0,45 con 33,2
   kg/vaca/día contra los 41,5 de Rodeo 2, que daba 0,93). Comparar kg por
   vaca y por día contra el promedio del rodeo es inmune a eso. El %vacas y el
   %kg se siguen mostrando, pero como contexto, no como el indicador.

   Se agrega una CURVA DE CONCENTRACIÓN: ordenando las vacas de mayor a menor
   producción, qué porcentaje de la leche hace cada décimo del rodeo. Dice si
   la producción se apoya en pocas vacas buenas (rodeo desparejo, hay margen
   para levantar la cola) o está repartida (rodeo parejo, el margen está en
   mover la media).

Toda la producción sale de `AnimalDaily`, que es la tabla que guarda el kg del
día por vaca. Está poblada solo para los animales que pasan por la rotativa,
así que sirve para promedios y proporciones —el kg/vaca/día coincidió exacto
contra DelPro— pero no para contar cabezas del rodeo entero.
"""
import rebano

REPORTE_PEAK = "peak"
REPORTE_DISTRIBUCION = "distribucion"
REPORTES = (REPORTE_PEAK, REPORTE_DISTRIBUCION)

RANGO_MAX_DIAS = 800

# Tramos de días en ordeñe de la curva de lactancia. Finos al principio, que es
# donde la curva se mueve, y anchos al final.
TRAMOS_DEO = [15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 200, 305]
ETIQUETAS_DEO = (["0-15"] + [f"{TRAMOS_DEO[i-1]}-{TRAMOS_DEO[i]}" for i in range(1, len(TRAMOS_DEO))]
                 + ["+305"])

# Grupos de lactancia que se comparan.
LACTANCIAS = [("l1", "1ra lactancia", "= 1"), ("l2", "2da lactancia", "= 2"),
              ("l3", "3ra y más", ">= 3")]

# Ventana de días en ordeñe que se usa como referencia de "arranque de
# lactancia": ya pasó el calostro y la vaca debería estar cerca de su ritmo.
ARRANQUE_DESDE, ARRANQUE_HASTA = 15, 30


def _caso_deo(col="d.DIM") -> str:
    partes = [f"WHEN {col} <= {TRAMOS_DEO[0]} THEN '{ETIQUETAS_DEO[0]}'"]
    for i in range(1, len(TRAMOS_DEO)):
        partes.append(f"WHEN {col} <= {TRAMOS_DEO[i]} THEN '{ETIQUETAS_DEO[i]}'")
    return "CASE " + " ".join(partes) + f" ELSE '{ETIQUETAS_DEO[-1]}' END"


_LACT = ("CASE WHEN d.LactationNumber = 1 THEN 'l1' "
         "WHEN d.LactationNumber = 2 THEN 'l2' ELSE 'l3' END")

# Filtro común de producción válida: hay que excluir los días sin lectura
# (TotalYield 0) porque si no bajan artificialmente todos los promedios.
_VALIDA = "d.GCRecord IS NULL AND d.TotalYield > 0 AND d.DIM IS NOT NULL AND d.DIM >= 0"


def sql_curva(desde: str, hasta: str, herd=None) -> str:
    """Curva de lactancia: kg/día promedio por tramo de DEO y lactancia."""
    return f"""
        SELECT {_caso_deo()} AS tramo, {_LACT} AS lact,
               AVG(d.TotalYield) AS kg, COUNT(*) AS dias_vaca,
               COUNT(DISTINCT d.BasicAnimal) AS vacas
        FROM AnimalDaily d
        JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL
        WHERE {_VALIDA}
          AND d.Date >= '{desde}' AND d.Date <= '{hasta}'
          AND d.LactationNumber >= 1
          AND {rebano.filtro('b', herd)}
        GROUP BY {_caso_deo()}, {_LACT}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_peak(desde: str, hasta: str, herd=None) -> str:
    """Peak por vaca contra su producción de arranque (15-30 DEO).

    Se arma vaca por vaca y recién después se promedia: promediar el peak del
    rodeo por un lado y el arranque por otro, y después dividir, mezcla vacas
    distintas en el numerador y el denominador.
    """
    return f"""
        WITH arranque AS (
            SELECT d.BasicAnimal AS animal, d.LactationNumber AS lact,
                   AVG(d.TotalYield) AS kg_arranque
            FROM AnimalDaily d
            JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL
            WHERE {_VALIDA}
              AND d.Date >= '{desde}' AND d.Date <= '{hasta}'
              AND d.DIM BETWEEN {ARRANQUE_DESDE} AND {ARRANQUE_HASTA}
              AND d.LactationNumber >= 1
              AND {rebano.filtro('b', herd)}
            GROUP BY d.BasicAnimal, d.LactationNumber
        ), pico AS (
            SELECT s.Animal AS animal, s.LactationNumber AS lact,
                   MAX(s.PeakYield) AS peak, MAX(s.DaysToPeak) AS dia_peak
            FROM AnimalLactationSummary s
            JOIN BasicAnimal b2 ON b2.OID = s.Animal AND b2.GCRecord IS NULL
            WHERE s.GCRecord IS NULL AND s.PeakYield > 0 AND s.DaysToPeak > 0
              AND s.StartDate >= DATEADD(day, -400, '{desde}')
              AND s.StartDate <= '{hasta}'
              AND {rebano.filtro('b2', herd)}
            GROUP BY s.Animal, s.LactationNumber
        )
        SELECT CASE WHEN a.lact = 1 THEN 'l1' WHEN a.lact = 2 THEN 'l2' ELSE 'l3' END AS lact,
               COUNT(*) AS vacas,
               AVG(a.kg_arranque) AS kg_arranque,
               AVG(p.peak) AS kg_peak,
               AVG(p.dia_peak * 1.0) AS dia_peak,
               AVG(100.0 * a.kg_arranque / NULLIF(p.peak, 0)) AS rel_arranque_peak
        FROM arranque a
        JOIN pico p ON p.animal = a.animal AND p.lact = a.lact
        WHERE p.peak > 0
        GROUP BY CASE WHEN a.lact = 1 THEN 'l1' WHEN a.lact = 2 THEN 'l2' ELSE 'l3' END
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_distribucion(desde: str, hasta: str, dimension: str, herd=None) -> str:
    """Vacas y kilos por segmento, para el corte pedido.

    `dimension`: "lactancia", "grupo" o "deo".
    """
    if dimension == "lactancia":
        segmento = ("CASE WHEN d.LactationNumber = 1 THEN '1ra lactancia' "
                    "WHEN d.LactationNumber = 2 THEN '2da lactancia' "
                    "WHEN d.LactationNumber = 3 THEN '3ra lactancia' "
                    "ELSE '4ta y más' END")
        extra_join = ""
    elif dimension == "grupo":
        segmento = "ISNULL(ag.Name, 'Sin grupo')"
        extra_join = ("LEFT JOIN AnimalGroup g ON g.OID = b.[Group] "
                      "LEFT JOIN AbstractGroup ag ON ag.OID = g.OID AND ag.GCRecord IS NULL")
    else:
        segmento = _caso_deo()
        extra_join = ""
    return f"""
        SELECT {segmento} AS segmento,
               COUNT(DISTINCT d.BasicAnimal) AS vacas,
               SUM(d.TotalYield) AS kg_total,
               AVG(d.TotalYield) AS kg_vaca_dia,
               COUNT(*) AS dias_vaca
        FROM AnimalDaily d
        JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL
        {extra_join}
        WHERE {_VALIDA}
          AND d.Date >= '{desde}' AND d.Date <= '{hasta}'
          AND d.LactationNumber >= 1
          AND {rebano.filtro('b', herd)}
        GROUP BY {segmento}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_concentracion(desde: str, hasta: str, herd=None) -> str:
    """Décimos del rodeo ordenados por producción, para la curva de
    concentración. El décimo 1 son las mejores vacas."""
    return f"""
        WITH por_vaca AS (
            SELECT d.BasicAnimal AS animal, AVG(d.TotalYield) AS kg
            FROM AnimalDaily d
            JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL
            WHERE {_VALIDA}
              AND d.Date >= '{desde}' AND d.Date <= '{hasta}'
              AND d.LactationNumber >= 1
              AND {rebano.filtro('b', herd)}
            GROUP BY d.BasicAnimal
            HAVING COUNT(*) >= 5      -- vacas con al menos 5 días medidos
        ), rank AS (
            SELECT kg, NTILE(10) OVER (ORDER BY kg DESC) AS decimo FROM por_vaca
        )
        SELECT decimo, COUNT(*) AS vacas, SUM(kg) AS kg, AVG(kg) AS kg_prom
        FROM rank GROUP BY decimo ORDER BY decimo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


# --- Armado ------------------------------------------------------------------

def _filas(data):
    return [dict(zip(data["columns"], f)) for f in (data.get("rows") or [])]


def _r(v, dec=1):
    return None if v is None else round(float(v), dec)


def _persistencia(serie: list) -> dict:
    """Caída porcentual mensual de la curva, desde el pico hacia adelante.

    Se busca el tramo de mayor producción y se mide cuánto cae por cada 30 días
    hasta el final de la curva. Es la "persistencia": una vaca que sostiene la
    producción después del pico da mucha más leche total que una que se cae,
    aunque las dos hayan picado igual.
    """
    puntos = [(i, kg) for i, kg in enumerate(serie) if kg is not None]
    if len(puntos) < 3:
        return {"pico_kg": None, "pico_tramo": None, "caida_mes_pct": None, "caida_mes_kg": None}
    idx_pico, kg_pico = max(puntos, key=lambda p: p[1])
    despues = [p for p in puntos if p[0] > idx_pico]
    if not despues or not kg_pico:
        return {"pico_kg": _r(kg_pico), "pico_tramo": ETIQUETAS_DEO[idx_pico],
                "caida_mes_pct": None, "caida_mes_kg": None}
    # Día medio de cada tramo, para poder expresar la caída por mes.
    bordes = [0] + TRAMOS_DEO + [400]
    medio = lambda i: (bordes[i] + bordes[i + 1]) / 2
    idx_fin, kg_fin = despues[-1]
    meses = (medio(idx_fin) - medio(idx_pico)) / 30.0
    if meses <= 0:
        return {"pico_kg": _r(kg_pico), "pico_tramo": ETIQUETAS_DEO[idx_pico],
                "caida_mes_pct": None, "caida_mes_kg": None}
    caida_kg = (kg_pico - kg_fin) / meses
    return {
        "pico_kg": _r(kg_pico), "pico_tramo": ETIQUETAS_DEO[idx_pico],
        "caida_mes_kg": _r(caida_kg, 2),
        "caida_mes_pct": _r(100.0 * caida_kg / kg_pico, 2),
    }


def armar_peak(data_curva, data_peak, data_curva_comp=None) -> dict:
    """Curva de lactancia + tabla de peak + persistencia por lactancia."""
    def curva_de(data):
        if not data:
            return None
        celdas = {(f["tramo"], f["lact"]): f for f in _filas(data)}
        salida = {}
        for clave, _lbl, _cond in LACTANCIAS:
            salida[clave] = [_r(celdas[(t, clave)]["kg"]) if (t, clave) in celdas else None
                             for t in ETIQUETAS_DEO]
        # "Todas" ponderada por días-vaca, no promedio de promedios.
        todas = []
        for t in ETIQUETAS_DEO:
            num = den = 0.0
            for clave, _l, _c in LACTANCIAS:
                f = celdas.get((t, clave))
                if f and f["kg"] is not None:
                    num += float(f["kg"]) * int(f["dias_vaca"] or 0)
                    den += int(f["dias_vaca"] or 0)
            todas.append(_r(num / den) if den else None)
        salida["todas"] = todas
        return salida

    curva = curva_de(data_curva)
    comparacion = curva_de(data_curva_comp)

    peak = {f["lact"]: f for f in _filas(data_peak)}
    tabla = []
    for clave, label, _cond in LACTANCIAS:
        f = peak.get(clave, {})
        tabla.append({
            "clave": clave, "lactancia": label,
            "vacas": int(f.get("vacas") or 0),
            "kg_arranque": _r(f.get("kg_arranque")),
            "kg_peak": _r(f.get("kg_peak")),
            "dia_peak": _r(f.get("dia_peak"), 0),
            "rel_arranque_peak": _r(f.get("rel_arranque_peak")),
            **_persistencia(curva[clave]),
        })
    # Fila de totales, ponderada por cantidad de vacas.
    n = sum(t["vacas"] for t in tabla)
    def pond(campo):
        vals = [(t[campo], t["vacas"]) for t in tabla if t[campo] is not None and t["vacas"]]
        d = sum(w for _v, w in vals)
        return round(sum(v * w for v, w in vals) / d, 1) if d else None
    tabla.append({
        "clave": "todas", "lactancia": "Todas las lactancias", "vacas": n,
        "kg_arranque": pond("kg_arranque"), "kg_peak": pond("kg_peak"),
        "dia_peak": pond("dia_peak"), "rel_arranque_peak": pond("rel_arranque_peak"),
        **_persistencia(curva["todas"]),
    })

    return {
        "tramos": ETIQUETAS_DEO, "curva": curva, "comparacion": comparacion,
        "tabla": tabla,
        "arranque": {"desde": ARRANQUE_DESDE, "hasta": ARRANQUE_HASTA},
    }


def armar_distribucion(cortes: dict, data_conc) -> dict:
    """Vacas vs kilos por segmento, con el índice de aporte, y la curva de
    concentración del rodeo."""
    salida = {}
    for dimension, data in cortes.items():
        filas = _filas(data)
        tot_vacas = sum(int(f["vacas"] or 0) for f in filas)
        tot_kg = sum(float(f["kg_total"] or 0) for f in filas)
        tot_dias = sum(int(f["dias_vaca"] or 0) for f in filas)
        # Promedio del rodeo ponderado por días-vaca: es la vara contra la que
        # se mide cada segmento.
        kg_ref = (tot_kg / tot_dias) if tot_dias else None
        segmentos = []
        for f in filas:
            v, kg = int(f["vacas"] or 0), float(f["kg_total"] or 0)
            kvd = float(f["kg_vaca_dia"]) if f["kg_vaca_dia"] is not None else None
            segmentos.append({
                "segmento": f["segmento"], "vacas": v,
                "pct_vacas": _r(100.0 * v / tot_vacas) if tot_vacas else None,
                "kg_total": round(kg),
                "pct_kg": _r(100.0 * kg / tot_kg) if tot_kg else None,
                "kg_vaca_dia": _r(kvd, 2),
                "indice": _r(kvd / kg_ref, 2) if (kvd is not None and kg_ref) else None,
            })
        if dimension == "deo":
            orden = {t: i for i, t in enumerate(ETIQUETAS_DEO)}
            segmentos.sort(key=lambda s: orden.get(s["segmento"], 99))
        else:
            segmentos.sort(key=lambda s: -(s["kg_vaca_dia"] or 0))
        salida[dimension] = {"segmentos": segmentos, "vacas": tot_vacas,
                             "kg_total": round(tot_kg), "kg_vaca_dia_rodeo": _r(kg_ref, 2)}

    filas_c = _filas(data_conc)
    tot_kg_c = sum(float(f["kg"] or 0) for f in filas_c) or 1
    acum, concentracion = 0.0, []
    for f in filas_c:
        pct = 100.0 * float(f["kg"] or 0) / tot_kg_c
        acum += pct
        concentracion.append({
            "decimo": int(f["decimo"]), "vacas": int(f["vacas"] or 0),
            "pct_kg": _r(pct), "acumulado": _r(acum),
            "kg_prom": _r(f["kg_prom"], 2),
        })
    salida["concentracion"] = concentracion
    return salida
