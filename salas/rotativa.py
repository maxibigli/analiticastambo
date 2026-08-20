# -*- coding: utf-8 -*-
"""Adaptador de la interfaz de `salas/__init__.py` para la rotativa: son
funciones que YA EXISTEN en `rutina.py`/`resumen.py`, de antes de que hubiera
más de un tipo de sala. Este módulo no agrega lógica, solo las expone bajo el
nombre común — así el comportamiento de La Ponderosa no cambia ni un bit."""
import flujos
import resumen
import rutina

NOMBRE = "Rotativa"


def sql_grupos() -> str:
    return rutina.SQL_GRUPOS


def sql_grupos_resumen(dias: int = 30) -> str:
    # La rotativa no necesita un umbral por producción: `sql_grupos()` ya da
    # la lista correcta vía CMSGroupMilkSetting. Se expone igual para que el
    # llamador no necesite un `if` por tipo de sala.
    return rutina.SQL_GRUPOS


def sql_ordenos_por_dia() -> str:
    return rutina.SQL_ORDENOS_POR_DIA


def sql_duraciones_dia(dias: int = 7) -> str:
    # La rotativa arma "Duraciones de ordeño" con el mismo caché por día que
    # ya usan Rutina/Evolución (ver `_refresh_rutina_async` en app.py) —no
    # necesita una consulta propia como la convencional. Se deja declarada acá
    # solo por completitud de la interfaz; app.py sigue con su camino actual
    # para este tipo de sala.
    raise NotImplementedError(
        "La rotativa arma duraciones desde el caché de rutina, no desde una "
        "consulta propia — ver api_resumen_duraciones en app.py.")


def armar_duraciones(filas: list, dias: int = 7) -> dict:
    raise NotImplementedError("Ver sql_duraciones_dia.")


def cantidad_puestos(tambo: str) -> int:
    return rutina.PUESTOS_ROTATIVA


def sql_rutina(fecha: str) -> str:
    return rutina.sql_rutina(fecha)


def analizar_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                 max_sesiones=None, nombres=None, umbral_prep_s=None,
                 identificacion_pct=None) -> dict:
    # `tambo` no hace falta acá — está en la firma solo para que coincida con
    # la interfaz común (ver salas/convencional.py). `identificacion_pct`
    # tampoco: la rotativa no reportó el problema que motivó ese parámetro
    # (ver `app.py::_identificacion_pct_de`, que para esta sala siempre
    # devuelve None) — se acepta y se ignora solo para que la interfaz común
    # no le rompa la llamada al resto de la app.
    return rutina.analizar_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                               umbral_prep_s=umbral_prep_s)


def resumen_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                max_sesiones=None, nombres=None, umbral_prep_s=None,
                identificacion_pct=None):
    return rutina.resumen_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                              umbral_prep_s=umbral_prep_s)


# La curva de flujo YA viene en kg/min en `CMSMilkYield`, así que no hay que
# escalar nada. La constante existe igual para que las dos salas expongan la
# misma interfaz (ver `salas/convencional.py`, donde vale 0.01).
ESCALA_FLUJO = 1.0

# Esta sala SÍ publica el umbral de retirada configurado en el equipo
# (`CMSMpcSetting.TakeoffLimit`), así que la pantalla de Flujos puede clasificar
# cada retirada contra la banda ±25%. Ver `salas.convencional`, donde no.
PUBLICA_UMBRAL_RETIRADA = True


# Las consultas de la pantalla de Flujos se delegan tal cual a `flujos.py`, que
# está escrito contra el esquema de esta sala. Existen acá solo para que las dos
# salas expongan la misma interfaz y `app.py` no tenga que preguntar de qué tipo
# es cada tambo (ver `salas/convencional.py`, donde son consultas propias).
def sql_flujos_por_dia(desde: str, hasta: str, retirada_min=None, retirada_max=None) -> str:
    return flujos.sql_por_dia(desde, hasta, retirada_min, retirada_max)


def sql_flujos_por_grupo(desde: str, hasta: str) -> str:
    return flujos.sql_por_grupo(desde, hasta)


def sql_flujos_distribucion(desde: str, hasta: str) -> str:
    return flujos.sql_distribucion(desde, hasta)


def sql_flujos_por_deo(desde: str, hasta: str) -> str:
    return flujos.sql_por_deo(desde, hasta)


def sql_flujos_tiempo_fuera(desde: str, hasta: str) -> str:
    return flujos.sql_tiempo_fuera(desde, hasta)


def sql_flujo_ordenios(desde: str, hasta: str) -> str:
    """Un renglón por ordeño con la curva de flujo en sus cuatro tramos, ya en
    kg/min. Es la base para calificar la rutina por flujo — ver
    `rutina.componente_flujo`."""
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    return f"""
        SELECT b.Number AS rp,
               y.Flow0To15   AS f0_15,
               y.Flow15To30  AS f15_30,
               y.Flow30To60  AS f30_60,
               y.Flow60To120 AS f60_120,
               y.AverageFlow AS f_prom,
               y.PeakFlow    AS f_pico
        FROM CMSMilkYield y
        JOIN MilkingDeviceVisit m ON m.OID = y.MilkingDeviceVisit
        JOIN BasicAnimal b ON b.OID = m.Animal
        WHERE m.GCRecord IS NULL AND y.Flow0To15 IS NOT NULL
          AND m.CreationTime >= '{desde}'
          AND m.CreationTime < DATEADD(day, 1, '{hasta}')
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_rendimiento(desde: str, hasta: str) -> str:
    return rutina.sql_rendimiento(desde, hasta)


def sql_identificacion(desde: str, hasta: str) -> str:
    return rutina.sql_identificacion(desde, hasta)


def armar_identificacion(columns, rows) -> list:
    return rutina.armar_identificacion(columns, rows)


def analizar_rendimiento(tambo: str, columns, rows, desde: str, hasta: str, max_sesiones=None,
                         nombres=None, grupos_ordene=None) -> list:
    return rutina.analizar_rendimiento(columns, rows, desde, hasta, max_sesiones, nombres=nombres,
                                       grupos_ordene=grupos_ordene)


def resumen_grupos_dia(tambo: str, columns, rows, fecha: str, grupos_ordene=None, nombres=None) -> dict:
    return rutina.resumen_grupos_dia(columns, rows, fecha, grupos_ordene=grupos_ordene, nombres=nombres)
