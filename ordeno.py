# -*- coding: utf-8 -*-
"""Vista del ordeño: vacas de la rotativa con sus datos e info clínica para el
operario que aplica tratamientos (RP, grupo, días, permiso, tratamiento con
droga/dosis/vía, retiros de leche y carne, diagnóstico, células somáticas,
conductividad, producción, estado reproductivo, apartar).

Cómo funciona el "tiempo real":
- La consulta toma siempre el ÚLTIMO ordeño. Si la app apunta a la base DDM que
  DelPro escribe EN VIVO durante el ordeño, la vista es en tiempo real; si apunta
  a una copia, muestra el último ordeño registrado.
- `momento_ordeno` (1ª columna) es la marca de tiempo de la sesión.

Notas de datos DelPro:
- MilkingDeviceVisit.Place = posición en la rotativa; IsAnimalFlaggedDoNotMilk =
  sin permiso; IsAnimalFlaggedDumpMilk = leche a descartar.
- "Apartar" = CMSManualSorting con SortArea definido.
- Tratamientos: DiagnosisTreatmentEvent (Treatment.Name, Diagnosis.Description,
  MilkWithholdEndDate = retiro leche, NotSlaughterEndDate = no faenar).
- Drogas del tratamiento (protocolo): Treatment → TreatmentDrugUsage → DrugUsage
  (Dosage, Drug.Name) + DrugParameters.ApplicationMethod → TextLookupItem (vía).
  Herencia XPO: TreatmentDrugUsage.OID = DrugUsage.OID.
- Células somáticas: MilkTest.SCC vía AnimalHistoricalData (MilkTest.OID =
  AnimalHistoricalData.OID; el animal es h.BasicAnimal y la fecha h.DateAndTime).
  El SCC viene EN MILES de células/ml (300 en la base = 300.000).
- Conductividad: SessionMilkYield.RelativeConductivity (relativa; >115 sospecha mastitis).
"""

PUESTOS = 80              # puestos de la rotativa (La Ponderosa)
VENTANA_HORAS = 3        # ventana de la sesión completa
VIVO_VENTANA_MIN = 40    # ventana del modo "en vivo" (una vuelta de la rotativa)
VIVO_LIMITE_MIN = 30     # si la última visita supera esto, no hay ordeño en curso
DIA_HORAS = 24           # ventana del "día" para las incidencias del equipo

# Umbrales de referencia de la rotativa para marcar una unidad como crítica.
UMBRAL_DESLIZ_PCT = 15   # % de ordeños del día con deslizamiento
UMBRAL_BLOQ_PCT = 6      # % de ordeños del día con bloqueo

# Expresión de vía de aplicación traducida al español.
_VIA = """CASE via.ItemValue
             WHEN 'Intramammary' THEN 'Intramamaria'
             WHEN 'Subcutaneous' THEN 'Subcutánea'
             WHEN 'Intramuscular' THEN 'Intramuscular'
             WHEN 'Intravenous' THEN 'Intravenosa'
             WHEN 'Oral' THEN 'Oral' WHEN 'Topical' THEN 'Tópica'
             ELSE via.ItemValue END"""

# CTEs que no dependen de la ventana (tratamiento, drogas, SCC, apartado).
_CTES_COMUNES = f"""
    trat AS (
      SELECT a.BasicAnimal, e.Treatment AS trat_oid,
             COALESCE(NULLIF(LTRIM(RTRIM(tn.ItemValue)), ''),
                      NULLIF(LTRIM(RTRIM(dg.Description)), '')) AS diagnostico,
             tr.Name AS tratamiento_nombre,
             e.TreatmentEndDate, e.MilkWithholdEndDate, e.NotSlaughterEndDate,
             ROW_NUMBER() OVER (PARTITION BY a.BasicAnimal ORDER BY a.DateAndTime DESC) rn
      FROM DiagnosisTreatmentEvent e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
      LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
      LEFT JOIN Treatment tr ON tr.OID = e.Treatment
      WHERE a.GCRecord IS NULL AND e.IsTreatmentStopped = 0 AND e.TreatmentEndDate >= GETDATE()
    ),
    drogas AS (
      SELECT tdu.Treatment AS trat_oid,
             STRING_AGG(
               CONCAT(dr.Name,
                 CASE WHEN du.Dosage IS NOT NULL
                      THEN ' ' + CONVERT(varchar(20), CAST(du.Dosage AS decimal(10,1))) ELSE '' END,
                 CASE WHEN via.ItemValue IS NOT NULL THEN ' (' + {_VIA} + ')' ELSE '' END),
               ' + ') AS droga_dosis
      FROM TreatmentDrugUsage tdu
      JOIN DrugUsage du ON du.OID = tdu.OID
      LEFT JOIN Drug dr ON dr.OID = du.Drug
      LEFT JOIN DrugParameters dp ON dp.OID = du.DrugParams
      LEFT JOIN TextLookupItem via ON via.OID = dp.ApplicationMethod
      GROUP BY tdu.Treatment
    ),
    scc AS (
      -- El animal y la fecha del control lechero salen de AnimalHistoricalData,
      -- de la que MilkTest hereda (FK real MilkTest.OID → AnimalHistoricalData).
      -- ANTES esto pasaba por MilkingTestAnimal uniendo por OID: era una
      -- colisión de OIDs (el animal no coincidía en NINGUNA fila) y además
      -- ordenaba por SampleDateTime, que está NULL en toda la base. Resultado:
      -- se mostraban células somáticas de OTRA vaca. Verificado contra el
      -- reporte Chi: ahora los valores coinciden vaca por vaca.
      SELECT h.BasicAnimal AS Animal, mt.SCC,
             ROW_NUMBER() OVER (PARTITION BY h.BasicAnimal ORDER BY h.DateAndTime DESC) rn
      FROM MilkTest mt JOIN AnimalHistoricalData h ON h.OID = mt.OID
      WHERE h.GCRecord IS NULL AND mt.SCC IS NOT NULL
    ),
    ult_trat AS (
      SELECT a.BasicAnimal, tr.Name AS ult_nombre, CAST(a.DateAndTime AS date) AS ult_fecha,
             ROW_NUMBER() OVER (PARTITION BY a.BasicAnimal ORDER BY a.DateAndTime DESC) rn
      FROM DiagnosisTreatmentEvent e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      JOIN Treatment tr ON tr.OID = e.Treatment
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= DATEADD(month, -24, GETDATE())
        AND tr.Name IS NOT NULL AND tr.Name <> 'N/A'
    ),
    apart AS (SELECT Animal FROM CMSManualSorting WHERE SortArea IS NOT NULL)
"""


# --- Incidencias del equipo por puesto (consulta APARTE, se cachea con TTL largo)
# Pesada (escanea CMSMilkYield), pero las incidencias cambian de a poco, así que
# no hace falta recalcularla en cada refresco del ordeño en vivo.
ORDENO_INC_SQL = f"""
    WITH fin AS (SELECT MAX(CreationTime) AS f FROM MilkingDeviceVisit)
    SELECT v.Place AS posicion,
           SUM(ISNULL(y.Slips, 0))          AS desliz,
           SUM(ISNULL(y.KickOffs, 0))       AS patadas,
           SUM(ISNULL(y.Blocks, 0))         AS bloqueos,
           SUM(ISNULL(y.NoOfReattaches, 0)) AS recoloc,
           SUM(ISNULL(CAST(y.ForcedRetract AS int), 0)) AS retiradas,
           COUNT(*)                          AS ordenos_dia,
           SUM(CASE WHEN y.Slips  > 0 THEN 1 ELSE 0 END) AS ordenos_con_desliz,
           SUM(CASE WHEN y.Blocks > 0 THEN 1 ELSE 0 END) AS ordenos_con_bloqueo,
           AVG(CASE WHEN s.ExpectedYield > 0 THEN 100.0 * s.TotalYield / s.ExpectedYield END) AS prod_relativa_pct
    FROM MilkingDeviceVisit v CROSS JOIN fin
    JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
    JOIN SessionMilkYield s ON s.OID = y.OID
    WHERE v.Place BETWEEN 1 AND {PUESTOS}
      AND v.CreationTime >= DATEADD(hour, -{DIA_HORAS}, fin.f)
      AND y.MilkConfirmTime >= DATEADD(hour, -{DIA_HORAS + 1}, fin.f)
    GROUP BY v.Place
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
"""


def _select_tail(src, alias):
    """SELECT + JOINs comunes. src = CTE de origen (visitas/plataforma), alias = vi/pl."""
    return f"""
    SELECT
      CONVERT(varchar(19), (SELECT f FROM fin), 120) AS momento_ordeno,
      {alias}.Place AS posicion,
      b.Number AS rp,
      b.[Group] AS grupo,
      DATEDIFF(day, r.LastLactationChangeDate, GETDATE()) AS dias,
      CASE WHEN {alias}.IsAnimalFlaggedDoNotMilk = 1 THEN 'NO ordeñar' ELSE 'OK' END AS permiso,
      CASE WHEN {alias}.IsAnimalFlaggedDumpMilk = 1 OR t.BasicAnimal IS NOT NULL
           THEN 'Sí' ELSE 'No' END AS tratamiento,
      prod.kg AS produccion_kg,
      CASE WHEN r.IsDryingOff = 1 THEN 'En secado' WHEN r.IsPregnant = 1 THEN 'Preñada'
           WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN '-'
           ELSE 'Vacía' END AS reproductivo,
      CASE WHEN a2.Animal IS NOT NULL THEN 'Apartar' ELSE '' END AS apartar,
      t.diagnostico AS diagnostico,
      t.tratamiento_nombre AS tratamiento_det,
      dr2.droga_dosis AS droga_dosis,
      CAST(t.TreatmentEndDate AS date) AS fin_tratamiento,
      CAST(t.MilkWithholdEndDate AS date) AS retiro_leche,
      CAST(t.NotSlaughterEndDate AS date) AS no_faenar,
      CAST(sc.SCC AS int) AS scc,
      prod.cond_rel AS conductividad,
      CAST(r.LastLactationChangeDate AS date) AS ultimo_parto,
      ut.ult_nombre AS ult_tratamiento,
      ut.ult_fecha AS ult_tratamiento_fecha,
      CASE WHEN a2.Animal IS NOT NULL THEN 'Sí' ELSE '' END AS separar
    FROM {src} {alias}
    JOIN BasicAnimal b ON b.OID = {alias}.Animal
    LEFT JOIN AnimalReproductionInfo r ON r.Animal = {alias}.Animal AND r.GCRecord IS NULL
    LEFT JOIN prod ON prod.BasicAnimal = {alias}.Animal
    LEFT JOIN trat t ON t.BasicAnimal = {alias}.Animal AND t.rn = 1
    LEFT JOIN drogas dr2 ON dr2.trat_oid = t.trat_oid
    LEFT JOIN scc sc ON sc.Animal = {alias}.Animal AND sc.rn = 1
    LEFT JOIN ult_trat ut ON ut.BasicAnimal = {alias}.Animal AND ut.rn = 1
    LEFT JOIN apart a2 ON a2.Animal = {alias}.Animal
    WHERE {alias}.rn = 1
"""


# --- Alarmas del ordeño por puesto (consulta APARTE, cacheada). Usa los flags
# oficiales de DeLaval en CMSMilkYield + producción real vs esperada. Es la vaca
# ACTUAL de cada puesto (última visita en la vuelta). Se pega al ordeño por puesto.
ORDENO_ALARMAS_SQL = f"""
    WITH fin AS (SELECT MAX(CreationTime) AS f FROM MilkingDeviceVisit),
    plat AS (
      SELECT v.OID AS visita_oid, v.Animal, v.Place,
             ROW_NUMBER() OVER (PARTITION BY v.Place ORDER BY v.CreationTime DESC) rn
      FROM MilkingDeviceVisit v CROSS JOIN fin
      WHERE v.Place BETWEEN 1 AND {PUESTOS}
        AND v.CreationTime >= DATEADD(minute, -{VIVO_VENTANA_MIN}, fin.f)
    ),
    cms AS (
      SELECT y.MilkingDeviceVisit, y.LowYieldAlarm, y.ConductivityAlarm, y.BloodAlarm,
             y.ForcedRetract, s.TotalYield, s.ExpectedYield
      FROM CMSMilkYield y CROSS JOIN fin
      JOIN SessionMilkYield s ON s.OID = y.OID
      WHERE y.MilkConfirmTime >= DATEADD(hour, -{DIA_HORAS}, fin.f)
    )
    SELECT plat.Place AS posicion, b.Number AS rp,
           c.TotalYield AS real_kg, c.ExpectedYield AS esperada_kg,
           ISNULL(c.LowYieldAlarm, 0)      AS a_baja,
           ISNULL(c.ConductivityAlarm, 0)  AS a_cond,
           ISNULL(c.BloodAlarm, 0)         AS a_sangre,
           ISNULL(c.ForcedRetract, 0)      AS a_retirada
    FROM plat
    JOIN BasicAnimal b ON b.OID = plat.Animal
    LEFT JOIN cms c ON c.MilkingDeviceVisit = plat.visita_oid
    WHERE plat.rn = 1
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""


# --- Sesión completa: todas las vacas del último ordeño ---
ORDENO_SQL = f"""
    WITH fin AS (SELECT MAX(CreationTime) AS f FROM MilkingDeviceVisit),
    visitas AS (
      SELECT v.Animal, v.Place, v.IsAnimalFlaggedDoNotMilk, v.IsAnimalFlaggedDumpMilk,
             v.SecondLap, v.CreationTime,
             ROW_NUMBER() OVER (PARTITION BY v.Animal ORDER BY v.CreationTime DESC) rn
      FROM MilkingDeviceVisit v CROSS JOIN fin
      WHERE v.CreationTime >= DATEADD(hour, -{VENTANA_HORAS}, fin.f)
    ),
    prod AS (
      SELECT s.BasicAnimal, MAX(s.TotalYield) AS kg, MAX(s.RelativeConductivity) AS cond_rel,
             MIN(CAST(s.IsValidEndTimeInterval AS int)) AS fin_ok
      FROM SessionMilkYield s CROSS JOIN fin
      WHERE s.BeginTime >= DATEADD(hour, -{VENTANA_HORAS}, fin.f)
      GROUP BY s.BasicAnimal
    ),
    {_CTES_COMUNES}
    {_select_tail('visitas', 'vi')}
    ORDER BY CASE WHEN vi.Place IS NULL THEN 1 ELSE 0 END, vi.Place
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""


# --- Modo EN VIVO: solo las vacas girando ahora (última visita por puesto) ---
ORDENO_VIVO_SQL = f"""
    WITH fin AS (SELECT MAX(CreationTime) AS f FROM MilkingDeviceVisit),
    plataforma AS (
      SELECT v.Animal, v.Place, v.IsAnimalFlaggedDoNotMilk, v.IsAnimalFlaggedDumpMilk,
             v.SecondLap, v.CreationTime,
             ROW_NUMBER() OVER (PARTITION BY v.Place ORDER BY v.CreationTime DESC) rn
      FROM MilkingDeviceVisit v CROSS JOIN fin
      WHERE v.Place BETWEEN 1 AND {PUESTOS}
        AND v.CreationTime >= DATEADD(minute, -{VIVO_VENTANA_MIN}, fin.f)
    ),
    prod AS (
      SELECT s.BasicAnimal, MAX(s.TotalYield) AS kg, MAX(s.RelativeConductivity) AS cond_rel,
             MIN(CAST(s.IsValidEndTimeInterval AS int)) AS fin_ok
      FROM SessionMilkYield s CROSS JOIN fin
      WHERE s.BeginTime >= DATEADD(minute, -{VIVO_VENTANA_MIN}, fin.f)
      GROUP BY s.BasicAnimal
    ),
    {_CTES_COMUNES}
    {_select_tail('plataforma', 'pl')}
    ORDER BY pl.Place
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
"""
