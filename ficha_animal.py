# -*- coding: utf-8 -*-
"""Ficha individual de un animal: datos generales, historial de eventos,
producción diaria, condición corporal (BCS) y test de leche.

Réplica funcional de las pantallas nativas de DelPro (ficha de animal /
pestaña Eventos / Cámara BCS / Gráfico Test Leche), buscando por número de
RP. Primera versión: cubre lo esencial, se va a ir ampliando con más datos
(celo esperado, parto esperado, actividad, etc. quedan pendientes — no hay
un campo directo para "próximo parto esperado" en AnimalReproductionInfo,
habría que calcularlo desde la última inseminación efectiva + gestación).

Eventos cubiertos: cambio de grupo, inseminación, celo, control de gestación,
cambio de transponder, entrada, parto, salida. Faltan (a agregar después):
vacunaciones, visitas veterinarias, pesadas.
"""

DIAS_PRODUCCION = 60      # ventana del gráfico de producción diaria
DIAS_TEST_DIARIO = 180    # ventana del gráfico "test de leche" (parte diaria)
DIAS_TEST_CONTROLES = 730  # ventana de controles lecheros (más espaciados)


def sql_info_general(rp: int) -> str:
    return f"""
        WITH dia AS (
          SELECT TOP 1 ad.DIM, ad.LactationNumber
          FROM AnimalDaily ad JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
          WHERE b.Number = {rp} AND ad.GCRecord IS NULL AND ad.IsYieldValid = 1
          ORDER BY ad.Date DESC
        ),
        prod7 AS (
          SELECT AVG(s.TotalYield) AS kg_prom
          FROM SessionMilkYield s JOIN BasicAnimal b ON b.OID = s.BasicAnimal
          WHERE b.Number = {rp} AND s.BeginTime >= DATEADD(day, -7, GETDATE())
        ),
        prod_ayer AS (
          SELECT SUM(s.TotalYield) AS kg
          FROM SessionMilkYield s JOIN BasicAnimal b ON b.OID = s.BasicAnimal
          WHERE b.Number = {rp}
            AND CAST(s.BeginTime AS date) = CAST(DATEADD(day, -1, GETDATE()) AS date)
        ),
        scc AS (
          SELECT TOP 1 mt.SCC, h.DateAndTime AS fecha
          FROM MilkTest mt JOIN AnimalHistoricalData h ON h.OID = mt.OID
          JOIN BasicAnimal b ON b.OID = h.BasicAnimal
          WHERE b.Number = {rp} AND mt.SCC IS NOT NULL
          ORDER BY h.DateAndTime DESC
        ),
        heat AS (
          SELECT TOP 1 a.DateAndTime AS fecha
          FROM EventHeat e JOIN AbstractAnimalEvent a ON a.OID = e.OID
          JOIN BasicAnimal b ON b.OID = a.BasicAnimal
          WHERE b.Number = {rp} AND a.GCRecord IS NULL
          ORDER BY a.DateAndTime DESC
        ),
        insem AS (
          SELECT TOP 1 a.DateAndTime AS fecha
          FROM EventInsemination e JOIN AbstractAnimalEvent a ON a.OID = e.OID
          JOIN BasicAnimal b ON b.OID = a.BasicAnimal
          WHERE b.Number = {rp} AND a.GCRecord IS NULL
          ORDER BY a.DateAndTime DESC
        )
        SELECT b.Number AS rp, CONVERT(varchar(10), b.BirthDate, 120) AS nacimiento,
               g.Name AS grupo, g.Number AS grupo_num,
               r.LactationNumber AS lactancia,
               CONVERT(varchar(10), r.LastLactationChangeDate, 120) AS ultimo_parto,
               CONVERT(varchar(10), r.ExpectedPregnancyCheckDate, 120) AS control_gestacion_esperado,
               CASE WHEN r.IsDryingOff = 1 THEN 'En secado' WHEN r.IsPregnant = 1 THEN 'Preñada'
                    WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN '-'
                    ELSE 'Vacía' END AS reproductivo,
               dia.DIM AS del, prod7.kg_prom AS produccion_media_7d, prod_ayer.kg AS produccion_ayer,
               scc.SCC AS ultimo_rcs, CONVERT(varchar(10), scc.fecha, 120) AS fecha_rcs,
               CONVERT(varchar(10), heat.fecha, 120) AS ultimo_celo,
               CONVERT(varchar(10), insem.fecha, 120) AS ultima_inseminacion
        FROM BasicAnimal b
        LEFT JOIN AbstractGroup g ON g.OID = b.[Group] AND g.GCRecord IS NULL
        LEFT JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
        LEFT JOIN dia ON 1 = 1
        LEFT JOIN prod7 ON 1 = 1
        LEFT JOIN prod_ayer ON 1 = 1
        LEFT JOIN scc ON 1 = 1
        LEFT JOIN heat ON 1 = 1
        LEFT JOIN insem ON 1 = 1
        WHERE b.Number = {rp} AND b.GCRecord IS NULL
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


# Historial de eventos: UNION de los tipos de evento más relevantes (herencia
# XPO — cada Event* se une a AbstractAnimalEvent por OID compartido). Las
# descripciones son una simplificación propia (no siempre calzan letra por
# letra con el texto de DelPro, p. ej. no traducimos el motivo exacto del
# celo ni el nombre del toro/semen todavía).
def sql_eventos(rp: int) -> str:
    return f"""
        SELECT a.DateAndTime AS fecha, 'Cambio de grupo' AS tipo,
               CONCAT(ISNULL(CAST(gOld.Number AS varchar), '?'), ' ', ISNULL(gOld.Name, ''), ' -> ',
                      ISNULL(CAST(gNew.Number AS varchar), '?'), ' ', ISNULL(gNew.Name, '')) AS detalle
        FROM EventGroupChange e
        JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        LEFT JOIN AbstractGroup gOld ON gOld.OID = e.OldGroup
        LEFT JOIN AbstractGroup gNew ON gNew.OID = e.NewGroup
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Inseminación',
               CONCAT('N°', e.InseminationNo,
                      CASE WHEN e.ConceptionDate IS NOT NULL THEN ' (efectiva)' ELSE '' END)
        FROM EventInsemination e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Celo', 'Detectado'
        FROM EventHeat e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Control de gestación',
               CASE WHEN e.Result = 1 THEN 'Positivo (+)' WHEN e.Result = 0 THEN 'Negativo (-)'
                    ELSE 'Sin resultado' END
        FROM EventPregCheck e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Cambio de transponder', CONCAT('-> ', e.NewTransponderID)
        FROM EventTransponderIDChange e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Entrada', 'Ingreso al rodeo'
        FROM EventEntry e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Parto',
               CONCAT('Facilidad de parto: ', ISNULL(CAST(e.CalvingEase AS varchar), '?'))
        FROM EventCalving e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        UNION ALL
        SELECT a.DateAndTime, 'Salida', 'Baja del rodeo'
        FROM EventExit e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        WHERE b.Number = {rp} AND a.GCRecord IS NULL

        ORDER BY fecha DESC
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_produccion_diaria(rp: int, dias: int = DIAS_PRODUCCION) -> str:
    return f"""
        SELECT CONVERT(varchar(10), ad.Date, 120) AS fecha, ad.DIM AS del, ad.TotalYield AS kg
        FROM AnimalDaily ad JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
        WHERE b.Number = {rp} AND ad.GCRecord IS NULL
          AND ad.Date >= DATEADD(day, -{dias}, GETDATE())
        ORDER BY ad.Date
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


# DIM por lectura de BCS: se calcula contra el ÚLTIMO parto conocido
# (AnimalReproductionInfo). Para lecturas de una lactancia anterior a la
# actual el DIM saldría mal (muy alto) — no tenemos el historial completo de
# partos enlazado todavía; queda para una próxima vuelta.
def sql_bcs_individual(rp: int) -> str:
    return f"""
        SELECT CONVERT(varchar(10), bc.DateAndTime, 120) AS fecha, bc.BcsValue AS score,
               DATEDIFF(day, r.LastLactationChangeDate, bc.DateAndTime) AS del
        FROM BcsDailyData bc
        JOIN BasicAnimal b ON b.OID = bc.Animal
        LEFT JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
        WHERE b.Number = {rp} AND bc.BcsValue IS NOT NULL
        ORDER BY bc.DateAndTime
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_test_leche_diario(rp: int, dias: int = DIAS_TEST_DIARIO) -> str:
    return f"""
        SELECT CONVERT(varchar(10), s.BeginTime, 120) AS fecha,
               SUM(s.TotalYield) AS kg,
               MAX(CASE WHEN s.RelativeConductivity > 0 THEN s.RelativeConductivity END) AS cond_max,
               MAX(CASE WHEN y.BloodAlarm = 1 THEN 1 ELSE 0 END) AS alarma_sangre,
               MAX(CASE WHEN y.ConductivityAlarm = 1 THEN 1 ELSE 0 END) AS alarma_cond
        FROM SessionMilkYield s
        JOIN CMSMilkYield y ON y.OID = s.OID
        JOIN BasicAnimal b ON b.OID = s.BasicAnimal
        WHERE b.Number = {rp} AND s.BeginTime >= DATEADD(day, -{dias}, GETDATE())
        GROUP BY CONVERT(varchar(10), s.BeginTime, 120)
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_test_leche_controles(rp: int, dias: int = DIAS_TEST_CONTROLES) -> str:
    return f"""
        SELECT CONVERT(varchar(10), h.DateAndTime, 120) AS fecha, mt.SCC AS scc,
               mt.Fat AS grasa, mt.Protein AS proteina, mt.Lactose AS lactosa, mt.Urea AS urea
        FROM MilkTest mt
        JOIN AnimalHistoricalData h ON h.OID = mt.OID
        JOIN BasicAnimal b ON b.OID = h.BasicAnimal
        WHERE b.Number = {rp} AND h.DateAndTime >= DATEADD(day, -{dias}, GETDATE())
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """
