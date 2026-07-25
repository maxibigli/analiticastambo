# -*- coding: utf-8 -*-
"""Catálogo de consultas predefinidas (funcionan sin API de IA)."""

CONSULTAS = {
    "produccion_30d": {
        "titulo": "Producción de leche por día (últimos 30 días)",
        "grafica": {"tipo": "line", "eje_x": "fecha", "series": ["kg_leche"]},
        "sql": """
            SELECT Date AS fecha, ROUND(SUM(TotalYield), 0) AS kg_leche,
                   COUNT(DISTINCT BasicAnimal) AS vacas_ordenadas
            FROM AnimalDaily
            WHERE GCRecord IS NULL AND IsYieldValid = 1
              AND Date >= DATEADD(day, -30, CAST(GETDATE() AS date))
            GROUP BY Date
            HAVING COUNT(DISTINCT BasicAnimal) >= 50  -- excluye días incompletos
            ORDER BY Date
        """,
    },
    "top_vacas": {
        "titulo": "Top 20 vacas por producción promedio (últimos 7 días)",
        "grafica": {"tipo": "bar", "eje_x": "vaca", "series": ["kg_promedio_dia"]},
        "sql": """
            SELECT TOP 20 CAST(b.Number AS varchar(20)) AS vaca,
                   ROUND(AVG(d.TotalYield), 1) AS kg_promedio_dia,
                   MAX(d.LactationNumber) AS lactancia, MAX(d.DIM) AS dias_en_leche
            FROM AnimalDaily d
            JOIN BasicAnimal b ON b.OID = d.BasicAnimal
            WHERE d.GCRecord IS NULL AND d.IsYieldValid = 1 AND d.TotalYield > 0
              AND b.GCRecord IS NULL AND b.Number > 0
              AND d.Date >= DATEADD(day, -7, CAST(GETDATE() AS date))
            GROUP BY b.OID, b.Number
            ORDER BY kg_promedio_dia DESC
        """,
    },
    "curva_lactancia": {
        "titulo": "Curva de lactancia promedio por nº de lactancia (kg por día en leche, último año)",
        "grafica": {"tipo": "line", "eje_x": "dias_en_leche",
                    "series": ["lactancia_1", "lactancia_2", "lactancia_3_o_mas"]},
        "sql": """
            SELECT DIM AS dias_en_leche,
                   ROUND(AVG(CASE WHEN LactationNumber = 1 THEN TotalYield END), 1) AS lactancia_1,
                   ROUND(AVG(CASE WHEN LactationNumber = 2 THEN TotalYield END), 1) AS lactancia_2,
                   ROUND(AVG(CASE WHEN LactationNumber >= 3 THEN TotalYield END), 1) AS lactancia_3_o_mas,
                   COUNT(*) AS observaciones
            FROM AnimalDaily
            WHERE GCRecord IS NULL AND IsYieldValid = 1 AND DIM BETWEEN 1 AND 305
              AND Date >= DATEADD(year, -1, CAST(GETDATE() AS date))
            GROUP BY DIM ORDER BY DIM
        """,
    },
    "prod_por_lactancia": {
        "titulo": "Producción promedio por número de lactancia (últimos 30 días)",
        "grafica": {"tipo": "bar", "eje_x": "lactancia", "series": ["kg_promedio_dia"]},
        "sql": """
            SELECT LactationNumber AS lactancia,
                   ROUND(AVG(TotalYield), 1) AS kg_promedio_dia,
                   COUNT(DISTINCT BasicAnimal) AS vacas
            FROM AnimalDaily
            WHERE GCRecord IS NULL AND IsYieldValid = 1 AND LactationNumber BETWEEN 1 AND 8
              AND Date >= DATEADD(day, -30, CAST(GETDATE() AS date))
            GROUP BY LactationNumber ORDER BY LactationNumber
        """,
    },
    "estado_reproductivo": {
        "titulo": "Estado reproductivo del rodeo activo",
        "grafica": {"tipo": "pie", "eje_x": "estado", "series": ["vacas"]},
        "sql": """
            SELECT estado, COUNT(*) AS vacas FROM (
                SELECT CASE
                         WHEN r.IsDryingOff = 1 THEN 'En secado'
                         WHEN r.IsPregnant = 1 THEN 'Preñadas'
                         WHEN r.IsInseminated = 1 THEN 'Inseminadas (sin confirmar)'
                         ELSE 'Vacías'
                       END AS estado
                FROM AnimalReproductionInfo r
                JOIN BasicAnimal b ON b.OID = r.Animal
                WHERE r.GCRecord IS NULL AND b.GCRecord IS NULL AND b.ExitDate IS NULL
            ) t GROUP BY estado ORDER BY vacas DESC
        """,
    },
    "inseminaciones_12m": {
        "titulo": "Inseminaciones por mes (últimos 12 meses)",
        "grafica": {"tipo": "bar", "eje_x": "mes", "series": ["inseminaciones"]},
        "sql": """
            SELECT CONVERT(char(7), a.DateAndTime, 126) AS mes, COUNT(*) AS inseminaciones
            FROM EventInsemination e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            WHERE a.GCRecord IS NULL
              AND a.DateAndTime >= DATEADD(month, -12, GETDATE())
            GROUP BY CONVERT(char(7), a.DateAndTime, 126) ORDER BY mes
        """,
    },
    "partos_12m": {
        "titulo": "Partos por mes (últimos 12 meses)",
        "grafica": {"tipo": "bar", "eje_x": "mes", "series": ["partos"]},
        "sql": """
            SELECT CONVERT(char(7), a.DateAndTime, 126) AS mes, COUNT(*) AS partos
            FROM EventCalving e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            WHERE a.GCRecord IS NULL
              AND a.DateAndTime >= DATEADD(month, -12, GETDATE())
            GROUP BY CONVERT(char(7), a.DateAndTime, 126) ORDER BY mes
        """,
    },
    "alerta_conductividad": {
        "titulo": "Vacas con conductividad relativa alta (posible mastitis, últimos 3 días)",
        "grafica": {"tipo": "table", "eje_x": "", "series": []},
        "sql": """
            SELECT TOP 50 b.Number AS vaca, MAX(s.RelativeConductivity) AS conductividad_relativa_max,
                   ROUND(AVG(s.TotalYield), 1) AS kg_promedio_ordeno,
                   COUNT(*) AS ordenos, MAX(CAST(s.BeginTime AS date)) AS ultimo_dia
            FROM SessionMilkYield s
            JOIN BasicAnimal b ON b.OID = s.BasicAnimal
            WHERE s.BeginTime >= DATEADD(day, -3, GETDATE())
              AND s.RelativeConductivity > 115
            GROUP BY b.Number
            ORDER BY conductividad_relativa_max DESC
        """,
    },
    "tratamientos_12m": {
        "titulo": "Diagnósticos/tratamientos por mes (últimos 12 meses)",
        "grafica": {"tipo": "bar", "eje_x": "mes", "series": ["tratamientos"]},
        "sql": """
            SELECT CONVERT(char(7), a.DateAndTime, 126) AS mes, COUNT(*) AS tratamientos
            FROM DiagnosisTreatmentEvent e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            WHERE a.GCRecord IS NULL
              AND a.DateAndTime >= DATEADD(month, -12, GETDATE())
            GROUP BY CONVERT(char(7), a.DateAndTime, 126) ORDER BY mes
        """,
    },
    "diagnosticos_frecuentes": {
        "titulo": "Diagnósticos más frecuentes (últimos 12 meses)",
        "grafica": {"tipo": "bar", "eje_x": "diagnostico", "series": ["casos"]},
        "sql": """
            SELECT TOP 15 COALESCE(NULLIF(LTRIM(RTRIM(tn.ItemValue)), ''),
                                   NULLIF(LTRIM(RTRIM(dg.Description)), ''),
                                   CONCAT('Código ', dg.Code)) AS diagnostico,
                   COUNT(*) AS casos
            FROM DiagnosisTreatmentEvent e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            JOIN Diagnosis dg ON dg.OID = e.Diagnosis
            LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
            WHERE a.GCRecord IS NULL
              AND a.DateAndTime >= DATEADD(month, -12, GETDATE())
            GROUP BY tn.ItemValue, dg.Description, dg.Code
            ORDER BY casos DESC
        """,
    },
}
