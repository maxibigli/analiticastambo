# -*- coding: utf-8 -*-
"""Adaptador de la interfaz de `salas/__init__.py` para una sala convencional
(espina de pescado). A diferencia de `salas/rotativa.py` (que solo reexporta
`rutina.py`), acá SÍ hay lógica nueva: el esquema de esta sala no tiene
`CMSGroupMilkSetting`/`MilkingDeviceVisit`/`CMSMilkYield`, así que "qué grupos
ordeñan de verdad" y "cuándo se colocaron las pezoneras" salen de otro lado.

Verificado contra San José (DelPro 10.11):

    Identificación (rombo, ID)         = SessionMilkYieldEx.IdTimestamp
    Arranque de leche (cuadrado)       = SessionMilkYield.BeginTime
                                          (= SessionMilkYieldEx.MilkStartTimestamp,
                                          idénticos en las 210.074 filas —
                                          mismo dato, dos nombres)
    Fin / retiro (triángulo)           = SessionMilkYield.EndTime
    `SessionMilkYieldEx.IdTime` = -(IdTimestamp → BeginTime) en segundos: es
    LITERALMENTE el mismo "tiempo de colocación" que mide la rotativa, con
    otro nombre de columna. El resto del motor de puntaje (colocación,
    vacas lerdas, huecos entre/dentro de grupo, mezcla de rodeos) es el mismo
    de `rutina.py` sin cambios: no depende de que haya una plataforma.

Lo único que SÍ es distinto es la "ocupación": una rotativa gasta capacidad
real cuando un puesto gira vacío (la plataforma sigue girando al mismo ritmo
igual, haya vaca o no); acá no hay plataforma girando, hay TANDAS por lado
(`SideNo`/`BatchNo`/`MPCNo`, ver `sala_convencional.py`) que simplemente
procesan las vacas que estén listas en ese momento — una tanda más chica no
cuesta tiempo de máquina de más, solo trae menos vacas. No hay un equivalente
real de "ocupación de la plataforma" para puntuar, así que ese componente del
score NO SE EVALÚA acá (ver `_sin_ocupacion`): se excluye y su peso se
redistribuye entre el resto, con el mismo mecanismo que ya usa "prep_90s"
cuando falta el dato de colocación (ver `rutina._analizar_sesion`).
"""
import statistics

import resumen
import rutina
import sala_convencional

NOMBRE = "Convencional"


def sql_grupos() -> str:
    """Grupos con producción real y sostenida, con nombre y número — mismo
    shape que `rutina.SQL_GRUPOS`, para el selector "qué grupos incluir"."""
    return f"""
        SELECT ad.AnimalGroup AS grupo, ag.Number AS numero, ag.Name AS nombre,
               COUNT(DISTINCT ad.BasicAnimal) AS cantidad
        FROM AnimalDaily ad
        JOIN AbstractGroup ag ON ag.OID = ad.AnimalGroup AND ag.GCRecord IS NULL
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date >= DATEADD(day, -{resumen.GRUPO_DIAS}, CAST(GETDATE() AS date))
          AND ad.AnimalGroup IS NOT NULL
        GROUP BY ad.AnimalGroup, ag.Number, ag.Name
        HAVING COUNT(DISTINCT ad.BasicAnimal) >= {sala_convencional.GRUPO_MIN_VACAS}
        ORDER BY ag.Number
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_grupos_resumen(dias: int = 30) -> str:
    return sala_convencional.sql_grupos_reales(dias)


def sql_ordenos_por_dia() -> str:
    return "SELECT MAX(SessionNo) AS ordenos_dia FROM ParlorHistoricalData"


def sql_duraciones_dia(dias: int = 7) -> str:
    return sala_convencional.sql_duraciones_dia(dias)


def armar_duraciones(filas: list, dias: int = 7) -> dict:
    return sala_convencional.armar_duraciones(filas, dias)


def cantidad_puestos(tambo: str) -> int:
    """Puestos REALES de esta instalación (lados × puestos por lado, ver
    `sala_convencional.configuracion`) — a diferencia de la rotativa, acá no
    hay un número fijo: cada sala convencional puede tener otra cantidad de
    lados/puestos por lado."""
    cfg = sala_convencional.configuracion(tambo)
    return cfg["lados"] * cfg["puestos_por_lado"]


def sql_rutina(fecha: str) -> str:
    """Visitas de un día (+6h de margen a cada lado, mismo criterio que
    `rutina.sql_rutina`). `lado`/`bloque` viajan además de las columnas
    comunes: los necesita `_huecos_tandas`/`_rotaciones_tandas` (ver
    `analizar_dia`)."""
    return f"""
        SELECT ex.MPCNo AS puesto, b.Number AS rp, b.[Group] AS grupo,
               ex.IdTimestamp AS hora_id, y.BeginTime AS hora_coloc, y.EndTime AS hora_fin,
               CAST(ex.ForcedRetract AS int) AS retirada_forzada,
               ex.SideNo AS lado, ex.BatchNo AS bloque
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE ex.IdTimestamp IS NOT NULL
          AND ex.IdTimestamp >= DATEADD(hour, -6, '{fecha}')
          AND ex.IdTimestamp < DATEADD(hour, 6, DATEADD(day, 1, '{fecha}'))
        ORDER BY ex.IdTimestamp
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def _sin_ocupacion(visitas: list, duracion_seg: float) -> dict:
    """Reemplaza a `rutina._ocupacion_rotativa` para esta sala: acá no hay
    plataforma que gire, así que no hay un equivalente real de "capacidad
    desperdiciada" para medir (ver el docstring del módulo). `score=None` hace
    que `rutina._analizar_sesion` EXCLUYA este componente del score y
    redistribuya su peso entre el resto, en vez de forzar un número que no
    significa nada para esta sala."""
    return {"label": "Ocupación de la plataforma (no aplica)", "score": None,
            "info": "Esta sala no tiene plataforma: cada tanda procesa las vacas que estén "
                    "listas, sin el costo de \"puesto girando vacío\" de una rotativa. No se "
                    "puntúa.", "hallazgos": []}


def _rotaciones_tandas(visitas: list, duracion_seg: float) -> int | None:
    """Análogo de `rutina._rotaciones_rotativa` para "Rendimiento Sala": en vez
    de vueltas de plataforma, cuenta tandas (lado, bloque) distintas."""
    tandas = {(v["lado"], v["bloque"]) for v in visitas if v.get("lado") is not None}
    return len(tandas) or None


def _huecos_tandas(visitas: list, duracion_seg: float, nombres: dict | None = None) -> dict:
    """Análogo de `rutina._huecos_rotativa`, pero NO se puede reusar tal cual:
    esa versión compara todo contra UNA mediana de sesión, y en una sala de
    tandas eso rompe. Medido contra San José (26/07, sesión de la mañana):
    los gaps DENTRO de una tanda tienen mediana 5s (373 casos); los gaps ENTRE
    tandas (el otro lado ordeñando) tienen mediana 399s, de 177s a 1359s (25
    casos). Con una mediana pooled (~5s), CUALQUIER cambio de tanda —algo
    esperado y normal— queda por encima del umbral y se marca como "hueco":
    dio manejo_corral=0 y entre_grupos=26-59 en las tres sesiones reales, un
    puntaje que no refleja ningún problema real de manejo.

    Acá el corte es CAMBIO DE TANDA (lado, bloque), no cambio de grupo —la
    pausa estructural de esta sala es entre tandas, no entre rodeos—, y cada
    lado del corte usa SU PROPIA mediana como referencia."""
    gaps = [((b["hora_id"] - a["hora_id"]).total_seconds(),
             (a.get("lado"), a.get("bloque")) != (b.get("lado"), b.get("bloque")), a, b)
            for a, b in zip(visitas, visitas[1:])]
    intra = [g for g, cambio, _, _ in gaps if not cambio]
    inter = [g for g, cambio, _, _ in gaps if cambio]
    mediana_intra = statistics.median(intra) if intra else 0
    mediana_inter = statistics.median(inter) if inter else 0
    umbral_intra = max(mediana_intra * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    umbral_inter = max(mediana_inter * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)

    exceso_entre_tandas = sum(g - mediana_inter for g in inter if g > umbral_inter)
    exceso_intra_tanda = sum(g - mediana_intra for g in intra if g > umbral_intra)
    s3 = 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso_entre_tandas / duracion_seg)
    s4 = 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso_intra_tanda / duracion_seg)

    hallazgos = [{
        "tipo": "hueco_grupo", "severidad": g, "puesto": None, "rp": None,
        "texto": f"Hueco de {round(g / 60, 1)} min al cambiar de tanda (lado {a.get('lado')}, "
                 f"bloque {a.get('bloque')} → lado {b.get('lado')}, bloque {b.get('bloque')}) "
                 f"a las {b['hora_id'].strftime('%H:%M')}, bastante más largo que el resto de "
                 "los cambios de tanda de esta sesión.",
    } for g, cambio, a, b in gaps if cambio and g > umbral_inter]

    return {
        "s3": s3, "s4": s4,
        "info3": f"{round(exceso_entre_tandas)}s perdidos en cambios de tanda anormalmente largos "
                 f"(mediana real entre tandas: {round(mediana_inter)}s).",
        "info4": f"{round(exceso_intra_tanda)}s perdidos por demoras trayendo animales dentro "
                 f"de la misma tanda (mediana real: {round(mediana_intra)}s).",
        "hallazgos": hallazgos,
    }


def analizar_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                 max_sesiones=None, nombres=None, umbral_prep_s=None) -> dict:
    # `tambo` no hace falta acá (a diferencia de antes: la ocupación ya no
    # depende de `puestos_por_lado`, ver `_sin_ocupacion`) — queda en la firma
    # solo para cumplir la interfaz común (ver salas/rotativa.py).
    return rutina.analizar_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                               _sin_ocupacion, _huecos_tandas, umbral_prep_s)


def resumen_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                max_sesiones=None, nombres=None, umbral_prep_s=None):
    return rutina.resumen_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                              _sin_ocupacion, _huecos_tandas, umbral_prep_s)


def sql_rendimiento(desde: str, hasta: str) -> str:
    """Igual que `sql_rutina`, + el kg de cada visita — para "Rendimiento Sala".

    NO se filtra `IdTimestamp IS NOT NULL`, por el mismo motivo que en la
    rotativa (ver el detalle medido en `rutina.sql_rendimiento`): un ordeño
    cuya identificación falló sigue siendo un ordeño real con leche, y
    excluirlo desviaba todas las métricas de la pantalla. Acá el respaldo de
    hora es `BeginTime` (arranque de leche), que en esta sala existe siempre
    porque la fila SALE de `SessionMilkYield`.

    OJO: esto está portado del arreglo de la rotativa, donde sí se pudo medir
    contra el reporte de DelPro. En una sala convencional real todavía no se
    verificó cuántas filas tienen `IdTimestamp` nulo — si son cero, el cambio
    no altera nada; si no, las incluye, que es lo correcto."""
    return f"""
        SELECT ex.MPCNo AS puesto, b.Number AS rp, b.[Group] AS grupo,
               ex.IdTimestamp AS hora_id, y.BeginTime AS hora_creacion,
               y.BeginTime AS hora_coloc, y.EndTime AS hora_fin,
               y.TotalYield AS kg, CAST(ex.ForcedRetract AS int) AS retirada_forzada,
               NULL AS rotacion,
               ex.SideNo AS lado, ex.BatchNo AS bloque
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE COALESCE(ex.IdTimestamp, y.BeginTime) >= DATEADD(hour, -6, '{desde}')
          AND COALESCE(ex.IdTimestamp, y.BeginTime) < DATEADD(hour, 6, DATEADD(day, 1, '{hasta}'))
        ORDER BY COALESCE(ex.IdTimestamp, y.BeginTime)
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def sql_identificacion(desde: str, hasta: str):
    """None = el % de identificación no está disponible en esta sala.

    El criterio de la rotativa (visitas de `MilkingDeviceVisit` cuyo animal es
    el placeholder `BasicAnimal.Number = 0`, ver `rutina.sql_identificacion`)
    no se puede trasladar tal cual: acá el ordeño sale de `SessionMilkYield`,
    que referencia el animal directo, y NO se verificó contra datos reales de
    San José cómo queda una vaca que la sala no logró identificar. Devolver
    None y que la pantalla lo diga es preferible a mostrar un 100% inventado —
    justamente el bug que tenía la rotativa antes (ver el docstring de
    `rutina.sql_identificacion`). Se implementa cuando haya un caso real para
    medirlo, con el mismo método: contar contra los datos, no suponer.
    """
    return None


def analizar_rendimiento(tambo: str, columns, rows, desde: str, hasta: str, max_sesiones=None,
                         nombres=None, grupos_ordene=None) -> list:
    return rutina.analizar_rendimiento(columns, rows, desde, hasta, max_sesiones,
                                       rotaciones_fn=_rotaciones_tandas, nombres=nombres,
                                       grupos_ordene=grupos_ordene)


def resumen_grupos_dia(tambo: str, columns, rows, fecha: str, grupos_ordene=None, nombres=None) -> dict:
    return rutina.resumen_grupos_dia(columns, rows, fecha, grupos_ordene=grupos_ordene, nombres=nombres,
                                     ocupacion_fn=_sin_ocupacion)
