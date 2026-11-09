# -*- coding: utf-8 -*-
"""Histórico gráfico de UNA vaca para quien está en la sala (PRUEBA DE CONCEPTO).

Se abre al hacer clic en una vaca de la rotativa. Junta en una sola respuesta lo
que el operario necesita para decidir si trata, aparta o libera:
  - tratamientos con diagnóstico, medicamentos aplicados, dosis y vía, y fin del retiro;
  - producción diaria (kg) con los días en leche;
  - eventos reproductivos (inseminación, celo, control de gestación, parto);
  - controles lecheros (células somáticas, grasa, proteína).

A diferencia de la ficha completa (`ficha_animal`, solo admin, con rentabilidad,
mérito y pedigrí), esto es liviano: cuatro consultas por RP, sin cálculos.
Los medicamentos salen de HealthEventDrugUsage → DrugUsage → Drug (lo que se
aplicó en ESE evento), no del protocolo del tratamiento.
"""
import ficha_animal
from ordeno import _VIA

DIAS_PRODUCCION = 450      # una lactancia completa más margen
MESES_TRATAMIENTOS = 30    # para ver enfermedades recurrentes
MESES_REPRODUCCION = 30


def sql_tratamientos(rp: int, meses: int = MESES_TRATAMIENTOS) -> str:
    return f"""
        SELECT CONVERT(varchar(10), a.DateAndTime, 120) AS fecha,
               COALESCE(NULLIF(LTRIM(RTRIM(tn.ItemValue)), ''),
                        NULLIF(LTRIM(RTRIM(dg.Description)), '')) AS diagnostico,
               loc.ItemValue AS ubicacion,
               tr.Name AS tratamiento,
               CONVERT(varchar(10), e.TreatmentEndDate, 120) AS fin_tratamiento,
               CONVERT(varchar(10), e.DumpMilkEndDate, 120) AS fin_retiro,
               (SELECT STRING_AGG(CONCAT(dr.Name,
                         CASE WHEN du.Dosage IS NOT NULL
                              THEN ' ' + CONVERT(varchar(20), CAST(du.Dosage AS decimal(10,1))) ELSE '' END,
                         CASE WHEN via.ItemValue IS NOT NULL THEN ' (' + {_VIA} + ')' ELSE '' END),
                         ' + ')
                FROM HealthEventDrugUsage hu
                JOIN DrugUsage du ON du.OID = hu.OID AND du.GCRecord IS NULL
                LEFT JOIN Drug dr ON dr.OID = du.Drug
                LEFT JOIN DrugParameters dp ON dp.OID = du.DrugParams
                LEFT JOIN TextLookupItem via ON via.OID = dp.ApplicationMethod
                WHERE hu.HealthEvent = e.OID) AS medicamentos
        FROM DiagnosisTreatmentEvent e
        JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
        LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
        LEFT JOIN TextLookupItem loc ON loc.OID = e.DiagnosisLocation
        LEFT JOIN Treatment tr ON tr.OID = e.Treatment
        WHERE b.Number = {rp} AND a.GCRecord IS NULL
          AND a.DateAndTime >= DATEADD(month, -{meses}, GETDATE())
        ORDER BY a.DateAndTime DESC
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_reproduccion(rp: int, meses: int = MESES_REPRODUCCION) -> str:
    return f"""
        SELECT CONVERT(varchar(10), a.DateAndTime, 120) AS fecha, 'Inseminación' AS tipo,
               CONCAT('N°', e.InseminationNo,
                      CASE WHEN e.ConceptionDate IS NOT NULL THEN ' (efectiva)' ELSE '' END) AS detalle
        FROM EventInsemination e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL
          AND a.DateAndTime >= DATEADD(month, -{meses}, GETDATE())
        UNION ALL
        SELECT CONVERT(varchar(10), a.DateAndTime, 120), 'Celo', 'Detectado'
        FROM EventHeat e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL
          AND a.DateAndTime >= DATEADD(month, -{meses}, GETDATE())
        UNION ALL
        SELECT CONVERT(varchar(10), a.DateAndTime, 120), 'Control de gestación',
               CASE WHEN e.Result = 1 THEN 'Positivo (+)' WHEN e.Result = 0 THEN 'Negativo (-)'
                    ELSE 'Sin resultado' END
        FROM EventPregCheck e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL
          AND a.DateAndTime >= DATEADD(month, -{meses}, GETDATE())
        UNION ALL
        SELECT CONVERT(varchar(10), a.DateAndTime, 120), 'Parto',
               CONCAT('Facilidad de parto: ', ISNULL(CAST(e.CalvingEase AS varchar), '?'))
        FROM EventCalving e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL
          AND a.DateAndTime >= DATEADD(month, -{meses}, GETDATE())
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_produccion(rp: int, dias: int = DIAS_PRODUCCION) -> str:
    return ficha_animal.sql_produccion_diaria(rp, dias)


def sql_controles(rp: int) -> str:
    return ficha_animal.sql_test_leche_controles(rp)
