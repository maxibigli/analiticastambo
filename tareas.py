# -*- coding: utf-8 -*-
"""Listas de tareas pendientes del rodeo, al estilo del To-Do de DelPro.

DelPro no guarda las tareas en una tabla (la tabla `Task` está vacía): las
DERIVA del estado reproductivo y sanitario. Estas consultas reconstruyen las
categorías principales. Los umbrales (p. ej. período de espera voluntario de
50 días para inseminar) son aproximaciones de la configuración de DelPro; se
pueden ajustar aquí si el tambo usa otros valores.

Todas usan tablas livianas (AnimalReproductionInfo, BasicAnimal,
DiagnosisTreatmentEvent) para no saturar el SQL Express.
"""

# Período de espera voluntario post-parto antes de habilitar para inseminar.
ESPERA_VOLUNTARIA_DIAS = 50

TAREAS = {
    "chequeo_prenez": {
        "titulo": "Chequeos de preñez pendientes",
        "descripcion": "Vacas inseminadas sin confirmar preñez, con fecha de "
                       "chequeo ya alcanzada (inseminación de los últimos 120 días).",
        "sql": """
            SELECT b.Number AS vaca, r.LactationNumber AS lactancia,
                   CAST(r.ExpectedPregnancyCheckDate AS date) AS fecha_chequeo,
                   DATEDIFF(day, r.ExpectedPregnancyCheckDate, GETDATE()) AS dias_atraso,
                   DATEDIFF(day, r.LastLactationChangeDate, GETDATE()) AS dias_en_leche
            FROM AnimalReproductionInfo r
            JOIN BasicAnimal b ON b.OID = r.Animal
            WHERE r.GCRecord IS NULL AND b.GCRecord IS NULL AND b.ExitDate IS NULL
              AND b.Number > 0 AND r.IsInseminated = 1 AND r.IsPregnant = 0
              AND r.ExpectedPregnancyCheckDate BETWEEN DATEADD(day, -120, GETDATE()) AND GETDATE()
            ORDER BY r.ExpectedPregnancyCheckDate
        """,
    },
    "para_inseminar": {
        "titulo": "Vacas para inseminar",
        "descripcion": "Vacas paridas hace más de 50 días, vacías, sin inseminar "
                       "y sin estar en secado.",
        "sql": """
            SELECT b.Number AS vaca, r.LactationNumber AS lactancia,
                   CAST(r.LastLactationChangeDate AS date) AS ultimo_parto,
                   DATEDIFF(day, r.LastLactationChangeDate, GETDATE()) AS dias_en_leche
            FROM AnimalReproductionInfo r
            JOIN BasicAnimal b ON b.OID = r.Animal
            WHERE r.GCRecord IS NULL AND b.GCRecord IS NULL AND b.ExitDate IS NULL
              AND b.Number > 0 AND r.IsPregnant = 0 AND r.IsInseminated = 0
              AND r.IsDryingOff = 0 AND r.LastLactationChangeDate IS NOT NULL
              AND r.LastLactationChangeDate <= DATEADD(day, -50, GETDATE())
            ORDER BY r.LastLactationChangeDate
        """,
    },
    "para_secar": {
        "titulo": "Vacas para secar",
        "descripcion": "Vacas preñadas marcadas en proceso de secado.",
        "sql": """
            SELECT b.Number AS vaca, r.LactationNumber AS lactancia,
                   CAST(r.LastLactationChangeDate AS date) AS ultimo_parto,
                   DATEDIFF(day, r.LastLactationChangeDate, GETDATE()) AS dias_en_leche
            FROM AnimalReproductionInfo r
            JOIN BasicAnimal b ON b.OID = r.Animal
            WHERE r.GCRecord IS NULL AND b.GCRecord IS NULL AND b.ExitDate IS NULL
              AND b.Number > 0 AND r.IsDryingOff = 1
            ORDER BY b.Number
        """,
    },
    "tratamiento_curso": {
        "titulo": "Tratamientos en curso",
        "descripcion": "Tratamientos sanitarios activos, con fecha de fin pendiente.",
        "sql": """
            SELECT b.Number AS vaca,
                   COALESCE(NULLIF(LTRIM(RTRIM(tn.ItemValue)), ''),
                            NULLIF(LTRIM(RTRIM(dg.Description)), ''),
                            CONCAT('Código ', dg.Code)) AS diagnostico,
                   CAST(e.TreatmentEndDate AS date) AS fin_tratamiento,
                   DATEDIFF(day, GETDATE(), e.TreatmentEndDate) AS dias_restantes
            FROM DiagnosisTreatmentEvent e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            JOIN BasicAnimal b ON b.OID = a.BasicAnimal
            LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
            LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
            WHERE a.GCRecord IS NULL AND b.GCRecord IS NULL
              AND e.IsTreatmentStopped = 0 AND e.TreatmentEndDate >= GETDATE()
            ORDER BY e.TreatmentEndDate
        """,
    },
    "retiro_leche": {
        "titulo": "Retiro de leche vigente",
        "descripcion": "Vacas cuya leche NO debe enviarse: período de retiro por "
                       "tratamiento aún vigente.",
        "sql": """
            SELECT b.Number AS vaca,
                   COALESCE(NULLIF(LTRIM(RTRIM(tn.ItemValue)), ''),
                            NULLIF(LTRIM(RTRIM(dg.Description)), ''),
                            CONCAT('Código ', dg.Code)) AS diagnostico,
                   CAST(e.MilkWithholdEndDate AS date) AS fin_retiro,
                   DATEDIFF(day, GETDATE(), e.MilkWithholdEndDate) AS dias_restantes
            FROM DiagnosisTreatmentEvent e
            JOIN AbstractAnimalEvent a ON a.OID = e.OID
            JOIN BasicAnimal b ON b.OID = a.BasicAnimal
            LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
            LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
            WHERE a.GCRecord IS NULL AND b.GCRecord IS NULL
              AND e.MilkWithholdEndDate >= GETDATE()
            ORDER BY e.MilkWithholdEndDate
        """,
    },
}
