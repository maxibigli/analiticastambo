# -*- coding: utf-8 -*-
"""Ordeño en vivo de una sala convencional (espina de pescado), a diferencia de
`ordeno.py` que es la rotativa. Módulo totalmente aparte porque el mecanismo de
entrada de vacas es otro: no hay una plataforma girando con un puesto por
vaca, hay una sala de dos lados que se llenan y vacían por TANDA.

CÓMO CARGA UNA SALA DE ESPINA DE PESCADO DE DOS LADOS (verificado contra una
base real, tambo San José, DelPro 10.11):

    mientras un LADO está ordeñando, el otro está entrando/preparándose. Se
    numeran con `SideNo` (1 o 2) y `BatchNo` (la tanda). Medido en una sesión
    real: tanda 1 = lado 1 (16 vacas entre 17:39:22 y 17:41:10, cada una
    ordeña 3-4 min), tanda 2 = lado 2 (arranca 17:48:19, ~7 min después de la
    tanda 1), tanda 3 = lado 1 de nuevo (17:56:31, ~8 min después de la tanda
    2). Un ciclo completo ida y vuelta del mismo lado da ~14-17 minutos para
    16 puestos.

    El puesto individual dentro de un lado es `MPCNo` (Milking Point Controller):
    en San José el lado 1 usa 1-16 y el lado 2 usa 17-32, sin superponerse
    (estable en los ~6 meses de historia real que hay para auditar, ver más
    abajo).

    `BatchNo` NO ES UN IDENTIFICADOR ÚNICO ni una cuenta global confiable —
    verificado sobre las 210.074 filas reales: en general SÍ funciona como un
    contador compartido que sube de a uno alternando de lado (tanda 1→lado1,
    tanda 4→lado2, tanda 5→lado1…), pero en 1.688 filas (0,8% del total, 62 de
    543 sesiones — 11%) el MISMO número de tanda aparece en LOS DOS lados a la
    vez, casi siempre en grupos residuales chicos (1-2 vacas rezagadas al
    final de la sesión). Por eso acá se muestra solo como dato informativo
    ("Bloque" en la pantalla), nunca como clave para agrupar o comparar entre
    lados.

DE DÓNDE SALEN LOS DATOS — esto es la diferencia real con la rotativa:

    La rotativa graba cada vuelta en `MilkingDeviceVisit` (posición = `Place`) y
    la producción en `CMSMilkYield`/`SessionMilkYield`. Acá NO EXISTE
    `MilkingDeviceVisit`: la tabla que en teoría cumple ese rol,
    `ParlorVisit`, tiene apenas 60 filas en tres años — es vestigial, no el
    registro real. El dato real está en `SessionMilkYield` (base, igual que la
    rotativa: BasicAnimal, BeginTime, EndTime, TotalYield, ExpectedYield,
    RelativeConductivity) + `SessionMilkYieldEx` (extensión con lo propio de la
    sala: BatchNo, SideNo, MPCNo, y las MISMAS alarmas que la rotativa —
    LowYield, ConductivityAlarm, BloodAlarm, ForcedRetract, Slips, KickOffs,
    Blocks, NoOfReattaches — unidas por OID, misma herencia XPO que en toda la
    base). `ParlorHistoricalData` es del lado de la SESIÓN completa (hora de
    inicio/fin), no por vaca.

QUÉ NO SE REPLICÓ DE LA ROTATIVA, Y POR QUÉ:

    - `IsAnimalFlaggedDoNotMilk` / `IsAnimalFlaggedDumpMilk` (permiso/descarte
      de leche): en la rotativa viven en `MilkingDeviceVisit`, acá no hay
      equivalente poblado (`AbstractMilkingPermissions` está vacía y
      `SessionMilkYield.Destination` es NULL en las 210.074 filas). No se
      inventa: esas dos columnas no están en este módulo.
    - "Apartar": la rotativa usa `CMSManualSorting.SortArea` como bandera de
      apartado PENDIENTE. Acá existe `SortResultEvent` + `SortGateDevice`, pero
      es un LOG de aparatados ya ejecutados (tiene `CutTimestamp`, no un
      estado "pendiente"), así que no se puede mostrar como bandera en vivo con
      el mismo significado. Queda para una fase 2 si el tambo lo pide.

CANTIDAD DE PUESTOS Y LADOS: CONFIGURABLE POR TAMBO. La rotativa tiene un
`PUESTOS = 80` fijo en el código porque hay una sola instalación (La
Ponderosa). Acá, al ser un módulo pensado para cualquier sala convencional, la
cantidad de lados y de puestos por lado se guarda por tambo (`configuracion`/
`guardar_configuracion`), mismo mecanismo que `parametros.py`. San José tiene 2
lados de 16 — eso es el valor de RESPALDO, no una constante universal.

HASTA DÓNDE LLEGAN LOS DATOS REALES (mismo tipo de trampa que en La Ponderosa:
no es lo mismo que la base "tenga" una fecha vieja a que el dato esté ahí).
`ParlorVisit`/`ParlorHistoricalData` tienen registros desde 2023, pero
`SessionMilkYield`/`SessionMilkYieldEx` —de donde sale TODO lo que muestra este
módulo— arrancan recién el **27/01/2026**. Antes de esa fecha no hay con qué
mostrar nada acá. Auditados los ~6 meses reales que sí hay (210.074 filas,
543 sesiones, 181 días, siempre 3 sesiones/día sin faltar un solo día):

    - `SideNo` (1/2), `ParlorNo`/`Parlor` (siempre 1) y el rango de `MPCNo`
      por lado se mantienen estables en toda la ventana — no hay evidencia de
      que la sala haya cambiado de tamaño en este período.
    - Cero NULL en SideNo/MPCNo/BatchNo, cero `SessionMilkYield` sin su fila
      `SessionMilkYieldEx`, cero visitas cuyo `BasicAnimal` no resuelva contra
      `BasicAnimal` — la integridad referencial de estas tablas es sólida.
    - SIN FILTRO DE REBAÑO, A PROPÓSITO (ver `sql_sala_sesion`) — se probó con
      `rebano.filtro_por_animal` primero y se sacó: excluía en silencio a una
      vaca recién ordeñada porque DelPro le había dejado `BasicAnimal.[Group]`
      en NULL al darla de baja (mismo comportamiento ya documentado para La
      Ponderosa). Los datos de un equipo físico de ordeño no necesitan ese
      filtro —a diferencia de `AnimalGroup`/`BasicAnimal`, que si compartieran
      instalación con otro tambo sí lo necesitarían—, y `ordeno.py` (la
      rotativa) tampoco lo usa por la misma razón.
"""
import json
import os
import threading

# --- Configuración por tambo (lados, puestos por lado, ventana "en vivo") ---
_RUTA_CONFIG = os.path.join(os.path.dirname(__file__), "sala_convencional.json")
_lock_config = threading.Lock()

# Valores de San José, medidos contra la base real. Sirven de respaldo para
# cualquier tambo que todavía no configuró los suyos — NO son un default
# universal de sala convencional.
RESPALDO = {
    "lados": 2,
    "puestos_por_lado": 16,
    # Cubre un ciclo completo ida y vuelta del mismo lado (medido: 14-17 min)
    # con margen. Si se ordeña "vivo" con menos, se pierde el lado que no está
    # activo en este instante; con más, empieza a traer la tanda anterior.
    "ventana_vivo_min": 22,
}


def _leer_config() -> dict:
    try:
        with open(_RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def configuracion(tambo: str) -> dict:
    """Configuración vigente del tambo: lo guardado, o el respaldo si no configuró nada."""
    propio = _leer_config().get(tambo, {})
    return {**RESPALDO, **propio}


def guardar_configuracion(tambo: str, cambios: dict) -> dict:
    """Guarda lados/puestos_por_lado/ventana_vivo_min para el tambo."""
    claves_validas = set(RESPALDO)
    with _lock_config:
        todo = _leer_config()
        propio = todo.setdefault(tambo, {})
        for clave, valor in (cambios or {}).items():
            if clave not in claves_validas:
                raise ValueError(f"Parámetro desconocido: {clave}")
            try:
                n = int(valor)
            except (TypeError, ValueError):
                raise ValueError(f"Valor inválido para {clave}: {valor!r}")
            if not (1 <= n <= 200):
                raise ValueError(f"Valor fuera de rango para {clave}: {n}")
            propio[clave] = n
        if not propio:
            todo.pop(tambo, None)
        with open(_RUTA_CONFIG, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
    return configuracion(tambo)


# --- Ventanas de tiempo, igual espíritu que ordeno.py --------------------
VENTANA_HORAS = 3          # sesión completa (último ordeño)
VIVO_LIMITE_MIN = 30       # si la última visita supera esto, no hay ordeño en curso
DIA_HORAS = 24             # ventana de incidencias por puesto

# A DIFERENCIA del resto de la app, estas consultas NO llevan `MAXDOP 1`. Se
# probó porque es la convención establecida (evitar RESOURCE_SEMAPHORE en SQL
# Express), pero acá hace lo contrario: la CTE `scc` (ROW_NUMBER sobre MilkTest
# + AnimalHistoricalData, ~10.000 filas) pasa de 27s a 0,0s al sacarle el
# MAXDOP=1 — el plan en paralelo evita un sort costoso a un solo hilo. Medido
# con las tres consultas de este módulo; `MAX_GRANT_PERCENT` solo alcanza para
# no colgarse en memoria.

# Ordeno.py trae las alarmas con una consulta APARTE porque en la rotativa
# viven en `CMSMilkYield`, una tabla distinta de `SessionMilkYield`. Acá no
# hace falta: `SessionMilkYieldEx` se une 1 a 1 por OID con `SessionMilkYield`
# (misma herencia XPO de siempre), así que las alarmas van directo en la
# misma fila de `tanda` y salen en una sola consulta.
INC_COLS = ["desliz", "patadas", "bloqueos", "recoloc", "ordenos_dia",
            "ordenos_con_desliz", "ordenos_con_bloqueo"]

# CTEs de tratamiento/drogas/SCC — IDÉNTICAS a las de ordeno.py: son tablas de
# manejo del animal, no de la sala, y el esquema coincide columna por columna.
_VIA = """CASE via.ItemValue
             WHEN 'Intramammary' THEN 'Intramamaria'
             WHEN 'Subcutaneous' THEN 'Subcutánea'
             WHEN 'Intramuscular' THEN 'Intramuscular'
             WHEN 'Intravenous' THEN 'Intravenosa'
             WHEN 'Oral' THEN 'Oral' WHEN 'Topical' THEN 'Tópica'
             ELSE via.ItemValue END"""

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
    )
"""


def _select_tail(src: str, alias: str) -> str:
    """SELECT + JOINs comunes sobre el CTE de origen (sesión completa o vivo)."""
    return f"""
    SELECT
      CONVERT(varchar(19), (SELECT f FROM fin), 120) AS momento_ordeno,
      {alias}.SideNo AS lado,
      {alias}.MPCNo AS puesto,
      {alias}.BatchNo AS bloque,
      b.Number AS rp,
      b.[Group] AS grupo,
      DATEDIFF(day, r.LastLactationChangeDate, GETDATE()) AS dias,
      {alias}.TotalYield AS produccion_kg,
      CASE WHEN r.IsDryingOff = 1 THEN 'En secado' WHEN r.IsPregnant = 1 THEN 'Preñada'
           WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN '-'
           ELSE 'Vacía' END AS reproductivo,
      t.diagnostico AS diagnostico,
      t.tratamiento_nombre AS tratamiento_det,
      dr2.droga_dosis AS droga_dosis,
      CAST(t.TreatmentEndDate AS date) AS fin_tratamiento,
      CAST(t.MilkWithholdEndDate AS date) AS retiro_leche,
      CAST(t.NotSlaughterEndDate AS date) AS no_faenar,
      CAST(sc.SCC AS int) AS scc,
      {alias}.RelativeConductivity AS conductividad,
      CAST(r.LastLactationChangeDate AS date) AS ultimo_parto,
      ut.ult_nombre AS ult_tratamiento,
      ut.ult_fecha AS ult_tratamiento_fecha,
      {alias}.ExpectedYield AS esperada_kg,
      {alias}.a_baja, {alias}.a_cond, {alias}.a_sangre, {alias}.a_retirada
    FROM {src} {alias}
    JOIN BasicAnimal b ON b.OID = {alias}.Animal
    LEFT JOIN AnimalReproductionInfo r ON r.Animal = {alias}.Animal AND r.GCRecord IS NULL
    LEFT JOIN trat t ON t.BasicAnimal = {alias}.Animal AND t.rn = 1
    LEFT JOIN drogas dr2 ON dr2.trat_oid = t.trat_oid
    LEFT JOIN scc sc ON sc.Animal = {alias}.Animal AND sc.rn = 1
    LEFT JOIN ult_trat ut ON ut.BasicAnimal = {alias}.Animal AND ut.rn = 1
    WHERE {alias}.rn = 1
"""


def sql_sala_sesion() -> str:
    """Todas las vacas del último ordeño (sesión completa, últimas VENTANA_HORAS).

    Sin filtro de rebaño, A PROPÓSITO — igual que `ordeno.py` con la rotativa.
    `SessionMilkYield`/`SessionMilkYieldEx` son datos de UN equipo físico de
    ordeño, propio de un solo tambo: no hay ambigüedad de a qué rebaño
    pertenece la fila, a diferencia de `AnimalGroup`/`BasicAnimal` que sí
    puede compartir instalación entre varios tambos (caso La Ponderosa).
    Filtrar acá igual traía un bug: `rebano.filtro_por_animal` exige que
    `BasicAnimal.[Group]` no sea NULL, y DelPro lo deja en NULL al dar de baja
    un animal — una vaca recién ordeñada pero ya marcada de baja quedaba
    invisible en la pantalla en vivo sin ningún aviso.
    """
    return f"""
    WITH fin AS (SELECT MAX(BeginTime) AS f FROM SessionMilkYield),
    tanda AS (
      SELECT y.BasicAnimal AS Animal, ex.SideNo, ex.MPCNo, ex.BatchNo,
             y.TotalYield, y.ExpectedYield, y.RelativeConductivity, y.BeginTime,
             ISNULL(ex.LowYield, 0) AS a_baja,
             ISNULL(ex.ConductivityAlarm, 0) AS a_cond,
             ISNULL(ex.BloodAlarm, 0) AS a_sangre,
             ISNULL(CAST(ex.ForcedRetract AS int), 0) AS a_retirada,
             ROW_NUMBER() OVER (PARTITION BY y.BasicAnimal ORDER BY y.BeginTime DESC) rn
      FROM SessionMilkYield y CROSS JOIN fin
      JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
      WHERE y.BeginTime >= DATEADD(hour, -{VENTANA_HORAS}, fin.f)
    ),
    {_CTES_COMUNES}
    {_select_tail('tanda', 'ta')}
    ORDER BY CASE WHEN ta.SideNo IS NULL THEN 1 ELSE 0 END, ta.SideNo, ta.MPCNo
    OPTION (MAX_GRANT_PERCENT = 20)
"""


def sql_sala_vivo(tambo: str) -> str:
    """Solo las vacas ordeñando AHORA: última visita por (lado, puesto) dentro
    de la ventana configurada (cubre un ciclo completo de ambos lados).

    Sin filtro de rebaño — ver la nota de `sql_sala_sesion`.
    """
    ventana = configuracion(tambo)["ventana_vivo_min"]
    return f"""
    WITH fin AS (SELECT MAX(BeginTime) AS f FROM SessionMilkYield),
    tanda AS (
      SELECT y.BasicAnimal AS Animal, ex.SideNo, ex.MPCNo, ex.BatchNo,
             y.TotalYield, y.ExpectedYield, y.RelativeConductivity, y.BeginTime,
             ISNULL(ex.LowYield, 0) AS a_baja,
             ISNULL(ex.ConductivityAlarm, 0) AS a_cond,
             ISNULL(ex.BloodAlarm, 0) AS a_sangre,
             ISNULL(CAST(ex.ForcedRetract AS int), 0) AS a_retirada,
             ROW_NUMBER() OVER (PARTITION BY ex.SideNo, ex.MPCNo ORDER BY y.BeginTime DESC) rn
      FROM SessionMilkYield y CROSS JOIN fin
      JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
      WHERE y.BeginTime >= DATEADD(minute, -{ventana}, fin.f)
    ),
    {_CTES_COMUNES}
    {_select_tail('tanda', 'ta')}
    ORDER BY ta.SideNo, ta.MPCNo
    OPTION (MAX_GRANT_PERCENT = 20)
"""


# --- Incidencias del equipo por puesto (cacheada con TTL largo) -------------
def sql_sala_incidencias() -> str:
    """Sin filtro de rebaño — ver la nota de `sql_sala_sesion`."""
    return f"""
    WITH fin AS (SELECT MAX(BeginTime) AS f FROM SessionMilkYield)
    SELECT ex.SideNo AS lado, ex.MPCNo AS puesto,
           SUM(ISNULL(ex.Slips, 0))          AS desliz,
           SUM(ISNULL(ex.KickOffs, 0))       AS patadas,
           SUM(ISNULL(ex.Blocks, 0))         AS bloqueos,
           SUM(ISNULL(ex.NoOfReattaches, 0)) AS recoloc,
           SUM(ISNULL(CAST(ex.ForcedRetract AS int), 0)) AS retiradas,
           COUNT(*) AS ordenos_dia,
           SUM(CASE WHEN ex.Slips  > 0 THEN 1 ELSE 0 END) AS ordenos_con_desliz,
           SUM(CASE WHEN ex.Blocks > 0 THEN 1 ELSE 0 END) AS ordenos_con_bloqueo,
           AVG(CASE WHEN y.ExpectedYield > 0 THEN 100.0 * y.TotalYield / y.ExpectedYield END) AS prod_relativa_pct
    FROM SessionMilkYield y CROSS JOIN fin
    JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
    WHERE y.BeginTime >= DATEADD(hour, -{DIA_HORAS}, fin.f)
    GROUP BY ex.SideNo, ex.MPCNo
    OPTION (MAX_GRANT_PERCENT = 25)
"""


# --- Piezas para adaptar secciones del dashboard general que asumían la ------
# rotativa (CMSGroupMilkSetting, reconstrucción de sesión desde visitas). Una
# sala convencional no tiene esas tablas, pero tiene equivalentes más directos.

# Mínimo de vacas distintas en 30 días para que un grupo cuente como de ordeño
# real. Verificado contra San José: los grupos reales (ALTA, MEDIA, Vaquillonas
# Ordeñe, Hospital) tienen 69-171 vacas distintas; el "ruido" —Preparto,
# PreSecado, con una vaca de paso que parió hace poco y todavía no la
# movieron— tiene 1-2 filas. No hay una bandera tipo `EnableMilking` que separe
# esto (`AbstractGroup.MilkingType` marca 1 hasta para "Vacas secas" o
# "Herd 1"): el criterio es el dato, no una bandera, mismo criterio que ya se
# usó para los lotes de Haasten en `alimentacion.py`.
GRUPO_MIN_VACAS = 20


def sql_grupos_reales(dias: int = 30) -> str:
    """Grupos con producción real y sostenida en los últimos `dias` días.

    Reemplaza a `rutina.SQL_GRUPOS` (que depende de `CMSGroupMilkSetting`,
    inexistente acá) para el gráfico "Producción media por grupo" del
    dashboard. `resumen.SQL_PRODUCCION_GRUPO_30D`/`SQL_PRODUCCION_PROMEDIO_GENERAL`
    sí sirven tal cual: dependen solo de `AnimalDaily`, que existe igual en
    ambos tipos de sala.
    """
    return f"""
        SELECT ad.AnimalGroup AS grupo, COUNT(DISTINCT ad.BasicAnimal) AS vacas
        FROM AnimalDaily ad
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date >= DATEADD(day, -{dias}, CAST(GETDATE() AS date))
          AND ad.AnimalGroup IS NOT NULL
        GROUP BY ad.AnimalGroup
        HAVING COUNT(DISTINCT ad.BasicAnimal) >= {GRUPO_MIN_VACAS}
        ORDER BY vacas DESC
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_duraciones_dia(dias: int = 7) -> str:
    """Duración (minutos) de cada sesión de los últimos `dias` días, para
    "Duraciones de ordeño" del dashboard.

    La rotativa reconstruye la sesión a partir de visitas individuales
    (`rutina.py`, `MilkingDeviceVisit`) porque no tiene otra forma de saber
    cuándo arrancó y terminó. Acá no hace falta: `ParlorHistoricalData` ya
    trae `SessionStartTime`/`SessionEndTime` por sesión, medido y no
    reconstruido — se usa esa, sin pasar por `rutina.py` en absoluto.
    """
    return f"""
        SELECT CAST(SessionStartTime AS date) AS fecha, SessionNo AS sesion,
               DATEDIFF(minute, SessionStartTime, SessionEndTime) AS duracion_min
        FROM ParlorHistoricalData
        WHERE SessionStartTime >= DATEADD(day, -{dias},
              CAST((SELECT MAX(SessionStartTime) FROM ParlorHistoricalData) AS date))
        ORDER BY fecha, sesion
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def armar_duraciones(filas: list, dias: int = 7) -> dict:
    """{fecha: [duracion_sesion1, duracion_sesion2, ...]} → misma forma que
    espera el frontend de "Duraciones de ordeño", sea cual sea el tipo de sala."""
    por_fecha = {}
    for f in filas:
        por_fecha.setdefault(str(f["fecha"]), {})[f["sesion"]] = f["duracion_min"]
    fechas = sorted(por_fecha)[-dias:]
    return {
        "puntos": [{"fecha": f, "duraciones": [v for _s, v in sorted(por_fecha[f].items())]}
                   for f in fechas],
        "calculando": False,
    }
