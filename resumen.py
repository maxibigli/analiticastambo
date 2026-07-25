# -*- coding: utf-8 -*-
"""Réplica funcional de los widgets del dashboard 'home' original de DelPro:
resumen de producción diaria (7 días + variación vs. el día anterior),
composición del rodeo (donut vacas/novillas + indicadores de reproducción),
duraciones de las sesiones de ordeño (7 días) y producción media por grupo
(30 días). Usa datos reales de la base DDM, no los números del reporte
original (son de otro tambo, solo sirvió de referencia visual).

Categorías del rodeo (verificadas contra la proporción real del reporte de
referencia — no hay un campo explícito "novilla"/"vaca" en DDM, se infiere):
  Novilla = AnimalReproductionInfo.LactationNumber es NULL o 0 (nunca parió).
  Vaca    = LactationNumber > 0 (parió al menos una vez).
  Vaca en ordeño = vaca AND NOT IsDryingOff.
  Vaca seca      = vaca AND IsDryingOff.
  Novilla preñada     = novilla AND IsPregnant.
  Novilla sin preñar  = novilla AND NOT IsPregnant.
"""

PRODUCCION_DIAS = 7          # días a graficar
PRODUCCION_MARGEN_DIAS = 12  # se piden de más por si algún día queda incompleto
GRUPO_DIAS = 30
GRUPOS_TOP_N = 6             # grupos más numerosos a graficar (evita saturar el gráfico)
VACAS_MIN_DIA = 50           # por debajo de esto, el día se considera incompleto (copia parcial)

SQL_PRODUCCION_DIARIA = f"""
    SELECT CAST(ad.Date AS date) AS fecha,
           SUM(CASE WHEN b.Number > 0 THEN ad.TotalYield ELSE 0 END) AS kg_total,
           SUM(CASE WHEN b.Number = 0 THEN ad.TotalYield ELSE 0 END) AS kg_desconocida,
           COUNT(DISTINCT CASE WHEN b.Number > 0 THEN b.OID END) AS vacas_ordenadas
    FROM AnimalDaily ad
    JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
      AND ad.Date >= DATEADD(day, -{PRODUCCION_DIAS + PRODUCCION_MARGEN_DIAS}, CAST(GETDATE() AS date))
    GROUP BY ad.Date
    HAVING COUNT(DISTINCT CASE WHEN b.Number > 0 THEN b.OID END) >= {VACAS_MIN_DIA}
    ORDER BY ad.Date
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
"""

SQL_ANIMALES = """
    SELECT
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) > 0 AND ISNULL(r.IsDryingOff,0) = 0 THEN 1 ELSE 0 END) AS vacas_ordeno,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) > 0 AND r.IsDryingOff = 1 THEN 1 ELSE 0 END) AS vacas_secas,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) = 0 AND ISNULL(r.IsPregnant,0) = 1 THEN 1 ELSE 0 END) AS novillas_prenadas,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) = 0 AND ISNULL(r.IsPregnant,0) = 0 THEN 1 ELSE 0 END) AS novillas_sin_prenar,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) > 0 AND ISNULL(r.IsPregnant,0) = 1 THEN 1 ELSE 0 END) AS vacas_prenadas,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) > 0 THEN 1 ELSE 0 END) AS vacas_total,
      SUM(CASE WHEN ISNULL(r.LactationNumber,0) = 0 THEN 1 ELSE 0 END) AS novillas_total,
      COUNT(*) AS total
    FROM BasicAnimal b
    LEFT JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
"""

# Variación del rodeo vs. ayer: altas y bajas registradas el último día (no hay
# una foto histórica del total de animales para comparar directamente).
SQL_ALTAS_BAJAS_AYER = """
    SELECT
      (SELECT COUNT(*) FROM EventEntry e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        WHERE a.GCRecord IS NULL
          AND CAST(a.DateAndTime AS date) = CAST(DATEADD(day, -1, GETDATE()) AS date)) AS altas,
      (SELECT COUNT(*) FROM EventExit e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        WHERE a.GCRecord IS NULL
          AND CAST(a.DateAndTime AS date) = CAST(DATEADD(day, -1, GETDATE()) AS date)) AS bajas
"""


def sql_dim_promedio(fecha: str) -> str:
    """DIM (días en leche) promedio del día más reciente con datos completos
    (se le pasa `fecha` = el mismo 'fecha_dato' que ya usan los KPIs del
    dashboard, para no duplicar la lógica de "último día completo")."""
    return f"""
        SELECT AVG(CAST(ad.DIM AS float)) AS dim_promedio
        FROM AnimalDaily ad
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date = '{fecha}'
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


# OJO: AnimalDaily.AnimalGroup (grupo histórico del animal ESE día) puede no
# coincidir con BasicAnimal.[Group] (grupo actual) si hubo una reorganización
# del rodeo — se vio en la práctica que casi no pisan. Por eso los grupos a
# graficar se eligen DIRECTO de AnimalDaily del propio rango de 30 días (los
# que más animales-día acumulan), no de la foto actual del rodeo.
SQL_PRODUCCION_GRUPO_30D = f"""
    SELECT CAST(ad.Date AS date) AS fecha, ad.AnimalGroup AS grupo,
           AVG(ad.TotalYield) AS promedio_kg, COUNT(*) AS animales_dia
    FROM AnimalDaily ad
    WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
      AND ad.Date >= DATEADD(day, -{GRUPO_DIAS}, CAST(GETDATE() AS date))
      AND ad.AnimalGroup IS NOT NULL
    GROUP BY ad.Date, ad.AnimalGroup
    ORDER BY ad.Date, ad.AnimalGroup
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""


SQL_PRODUCCION_PROMEDIO_GENERAL = f"""
    SELECT CAST(ad.Date AS date) AS fecha, AVG(ad.TotalYield) AS promedio_kg
    FROM AnimalDaily ad
    WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
      AND ad.Date >= DATEADD(day, -{GRUPO_DIAS}, CAST(GETDATE() AS date))
    GROUP BY ad.Date
    ORDER BY ad.Date
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""
