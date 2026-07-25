# -*- coding: utf-8 -*-
"""Mantenimiento preventivo de equipos de ordeño (tubos cortos, pezoneras,
lubricadores, etc.), tal como lo configura el operario en DelPro.

Tablas DelPro:
  ServiceCounterDef -- catálogo de tipos de contador (ej. "Replace Liners").
  ServiceTimer       -- contador real cargado por el operario: Limit (tope),
                        Elapsed (acumulado desde el último reseteo),
                        ServiceTimerStartTime (desde cuándo cuenta),
                        AlarmGenerated (si ya superó el tope). Unit indica si
                        se mide en días (0) o en ordeños (2).
  Device             -- nombre del equipo/plataforma (ServiceTimerDevice → OID).

Se excluyen los contadores de sistema (chequeo de hora/reinicio de PC): no son
mantenimiento de equipo de ordeño.
"""

MANTENIMIENTO_SQL = """
    SELECT st.OID AS id,
           st.Name AS nombre,
           st.Limit AS limite,
           st.Elapsed AS acumulado,
           CONVERT(varchar(10), st.ServiceTimerStartTime, 120) AS desde,
           CAST(st.AlarmGenerated AS int) AS alarma,
           scd.ServiceCounterDescription AS tipo,
           CASE scd.Unit WHEN 0 THEN 'días' WHEN 2 THEN 'ordeños' WHEN 6 THEN 'hs' ELSE '' END AS unidad,
           d.DeviceName AS equipo
    FROM ServiceTimer st
    LEFT JOIN ServiceCounterDef scd ON scd.OID = st.ServiceCounterDef
    LEFT JOIN Device d ON d.OID = st.ServiceTimerDevice
    WHERE st.GCRecord IS NULL AND st.IsActive = 1
      AND ISNULL(scd.ServiceCounterID, '') NOT IN ('SystemTimeCheck', 'RestartComputerCheck')
    ORDER BY CASE WHEN st.Limit > 0 THEN st.Elapsed / st.Limit ELSE 0 END DESC
"""
