# -*- coding: utf-8 -*-
"""Análisis de gestación: cuánto duran realmente las gestaciones del tambo.

Réplica del informe "Duración de gestaciones" de DelPro. Por mes de parto y
separando el primer parto (L0) del resto (L1+), mide los días que pasaron
entre la última inseminación y el parto.

PARA QUÉ SIRVE. El parámetro "Días de gestación" que tiene configurado el
tambo (280) es el que usan TODAS las proyecciones de la aplicación para
calcular cuándo va a parir cada vaca. Este informe permite contrastar ese
supuesto contra lo que pasa de verdad: si las gestaciones reales promedian
276 y el parámetro dice 280, todas las fechas de parto proyectadas salen
cuatro días tarde, y con ellas las de secado.

También sirve para leer el rodeo: una gestación más corta de lo normal suele
venir con parto de mellizos, estrés térmico o problemas sanitarios, y esos
terneros nacen más chicos.

VERIFICADO contra el informe de DelPro (26/07/2025 a 26/07/2026, todos los
rebaños), y es el calce más ajustado de todo el análisis reproductivo:

    07-2025  L0   24 partos / 279 días   ✓ idéntico
    07-2025  L1+  25 partos / 277 días   ✓ idéntico
    08-2025  L0  193 partos / 277 días   ✓ idéntico
    08-2025  L1+ 134 partos / 277 días   ✓ idéntico
    11-2025  L0   56 partos / 276 días   ✓ idéntico

Se desvía en un animal en tres de las diez filas contrastadas.

CÓMO SE CALCULA. Para cada parto se busca la última inseminación anterior y se
cuentan los días. Se descartan las combinaciones fuera del rango biológico
(240 a 310 días): si la diferencia cae afuera, esa inseminación no es la que
corresponde a ese parto — falta el registro del servicio que sí quedó.

`AbstractAnimalEvent.LactationNumber` en un parto es la lactancia que ARRANCA,
así que un 1 es una vaquillona que acaba de parir por primera vez: es la L0
del informe (era vaquillona hasta ese parto).
"""
import rebano

# Rango biológico aceptable, en días. Fuera de esto la inseminación encontrada
# no es la que corresponde al parto.
GESTACION_MIN, GESTACION_MAX = 240, 310

RANGO_MAX_DIAS = 800

GRUPOS = [("L0", "Primer parto (L0)"), ("L1+", "Vacas (L1+)")]

_GRUPO = "CASE WHEN cal.LactationNumber <= 1 THEN 'L0' ELSE 'L1+' END"

_BASE = f"""
    FROM EventCalving c
    JOIN AbstractAnimalEvent cal ON cal.OID = c.OID AND cal.GCRecord IS NULL
    CROSS APPLY (
        SELECT TOP 1 ae.DateAndTime
        FROM EventInsemination i
        JOIN AbstractAnimalEvent ae ON ae.OID = i.OID AND ae.GCRecord IS NULL
        WHERE ae.BasicAnimal = cal.BasicAnimal AND ae.DateAndTime < cal.DateAndTime
        ORDER BY ae.DateAndTime DESC
    ) ins
    WHERE cal.DateAndTime >= '{{desde}}' AND cal.DateAndTime < DATEADD(day, 1, '{{hasta}}')
      AND DATEDIFF(day, ins.DateAndTime, cal.DateAndTime)
          BETWEEN {GESTACION_MIN} AND {GESTACION_MAX}
      AND {{filtro}}
"""


def sql_por_mes(desde: str, hasta: str, herd=None) -> str:
    """Partos y duración promedio de gestación, por mes de parto y grupo."""
    base = _BASE.format(desde=desde, hasta=hasta,
                        filtro=rebano.filtro_por_animal('cal.BasicAnimal', herd))
    return f"""
        SELECT FORMAT(cal.DateAndTime, 'yyyy-MM') AS mes,
               {_GRUPO} AS grupo,
               COUNT(*) AS partos,
               AVG(DATEDIFF(day, ins.DateAndTime, cal.DateAndTime) * 1.0) AS dias,
               MIN(DATEDIFF(day, ins.DateAndTime, cal.DateAndTime)) AS dias_min,
               MAX(DATEDIFF(day, ins.DateAndTime, cal.DateAndTime)) AS dias_max
        {base}
        GROUP BY FORMAT(cal.DateAndTime, 'yyyy-MM'), {_GRUPO}
        ORDER BY mes, grupo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_distribucion(desde: str, hasta: str, herd=None) -> str:
    """Cuántos partos cayeron en cada duración, para ver la dispersión.

    El promedio solo no alcanza: dos rodeos con el mismo promedio de 277 días
    pueden tener uno las gestaciones muy juntas y el otro media cola de partos
    prematuros, que es un problema sanitario.
    """
    base = _BASE.format(desde=desde, hasta=hasta,
                        filtro=rebano.filtro_por_animal('cal.BasicAnimal', herd))
    dias = "DATEDIFF(day, ins.DateAndTime, cal.DateAndTime)"
    return f"""
        SELECT {dias} AS dias, {_GRUPO} AS grupo, COUNT(*) AS partos
        {base}
        GROUP BY {dias}, {_GRUPO}
        ORDER BY dias
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def analizar(data_mes, data_dist, dias_configurados: int) -> dict:
    """Arma las series por mes y el histograma, y compara contra el parámetro
    de días de gestación que usa el tambo."""
    filas = [dict(zip(data_mes["columns"], f)) for f in (data_mes.get("rows") or [])]
    meses = sorted({f["mes"] for f in filas})
    por = {(f["mes"], f["grupo"]): f for f in filas}

    def serie(grupo, campo):
        return [round(float(por[(m, grupo)][campo]), 0) if (m, grupo) in por else None
                for m in meses]

    tabla, tot_partos, suma_dias = [], 0, 0.0
    for m in meses:
        for clave, _lbl in GRUPOS:
            f = por.get((m, clave))
            if not f:
                continue
            n, d = int(f["partos"] or 0), float(f["dias"] or 0)
            tot_partos += n
            suma_dias += d * n
            tabla.append({
                "mes": m, "grupo": clave, "partos": n,
                "dias": round(d, 1),
                "dias_min": int(f["dias_min"] or 0), "dias_max": int(f["dias_max"] or 0),
                "desvio": round(d - dias_configurados, 1),
            })

    prom_general = round(suma_dias / tot_partos, 1) if tot_partos else None

    # Histograma
    dist = {}
    for f in (data_dist.get("rows") or []):
        d = dict(zip(data_dist["columns"], f))
        dist.setdefault(int(d["dias"]), {"L0": 0, "L1+": 0})[d["grupo"]] = int(d["partos"] or 0)
    dias_orden = sorted(dist)
    histograma = {
        "dias": dias_orden,
        "L0": [dist[d]["L0"] for d in dias_orden],
        "L1+": [dist[d]["L1+"] for d in dias_orden],
    }

    return {
        "meses": meses,
        "series": {clave: {"dias": serie(clave, "dias"), "partos": serie(clave, "partos")}
                   for clave, _l in GRUPOS},
        "tabla": tabla,
        "histograma": histograma,
        "grupos": [{"clave": c, "label": l} for c, l in GRUPOS],
        "resumen": {
            "partos": tot_partos,
            "dias_promedio": prom_general,
            "dias_configurados": dias_configurados,
            # Lo que importa: cuántos días se corre cada fecha proyectada por
            # usar el parámetro en vez del promedio real.
            "desvio": round(prom_general - dias_configurados, 1) if prom_general else None,
        },
    }
