# -*- coding: utf-8 -*-
"""Tipo de SALA de ordeño del tambo: rotativa, convencional (espina de
pescado), o —más adelante— robot (ordeño voluntario). Todas viven en la misma
base DDM, pero cada tipo de sala usa tablas y mecánicas propias del
controlador que DeLaval le vende con esa sala: la rotativa graba
`MilkingDeviceVisit`/`CMSMilkYield`/`CMSGroupMilkSetting`; la convencional
graba `SessionMilkYield`/`SessionMilkYieldEx`/`ParlorHistoricalData` y no
tiene ninguna de esas otras tres tablas (verificado contra San José, DelPro
10.11); un robot es otra base de datos de nuevo (no hay instalación real
todavía para verificar contra qué).

Mismo espíritu que `proveedores/__init__.py` para los sistemas de
alimentación: el resto de la aplicación —dashboard, "Rutina de Ordeño",
"Rendimiento Sala"— no tiene por qué saber cuál es. Le pide siempre lo mismo a
un módulo que cumple esta interfaz:

    NOMBRE                                cómo se llama, para mostrarlo
    cantidad_puestos(tambo)                puestos físicos de esta instalación
                                           (la rotativa: fijo, PUESTOS_ROTATIVA;
                                           la convencional: lados × puestos por
                                           lado de su config). Para escalar el
                                           eje del gráfico de "Rutina de
                                           Ordeño" — ver /api/tambos.
    sql_grupos()                          SQL: grupos de ordeñe reales
                                           (grupo, numero, nombre, cantidad) —
                                           para el selector "qué grupos incluir"
    sql_grupos_resumen(dias)              SQL: grupos con producción real en
                                           `dias` días (grupo, vacas) — para
                                           filtrar el gráfico de producción
    sql_ordenos_por_dia()                 SQL: ordeños/día declarados (1 fila)
    sql_duraciones_dia(dias)              SQL: duración de cada sesión de los
                                           últimos `dias` días
    armar_duraciones(filas, dias)         filas ya en memoria → {puntos, calculando}
    sql_rutina(fecha)                     SQL: visitas/tandas de un día (+margen),
                                           para "Rutina de Ordeño"
    analizar_dia(tambo, columns, rows, fecha,
                 grupos, pesos, max_sesiones, nombres,
                 umbral_prep_s, identificacion_pct) -> dict
                                           separa en sesiones y puntúa cada una.
                                           `tambo` va primero por si la sala lo
                                           necesita para su config (la rotativa
                                           lo ignora). `umbral_prep_s`: objetivo
                                           de colocación en segundos (None = el
                                           de DelPro, 90s) — configurable porque
                                           no todas las salas tienen el mismo
                                           ritmo de colocación. `identificacion_pct`:
                                           % real de identificación del día (0-100)
                                           a usar en vez de recalcularlo por sesión
                                           — solo lo pide `app.py` para convencional
                                           (ver rutina._analizar_sesion); la rotativa
                                           lo acepta por interfaz y lo ignora.
    resumen_dia(tambo, columns, rows, fecha,
                grupos, pesos, max_sesiones, nombres,
                umbral_prep_s, identificacion_pct) -> dict | None
                                           igual que analizar_dia pero reducido
                                           a UN punto (promedio ponderado por
                                           vacas), para graficar la evolución.
    sql_rendimiento(desde, hasta)         SQL: rango de fechas, para
                                           "Rendimiento Sala"
    analizar_rendimiento(tambo, columns, rows,
                          desde, hasta, max_sesiones) -> list
                                           throughput por sesión del rango

Cada implementación es dueña de las columnas y umbrales que le hacen falta;
lo único realmente compartido es el MOTOR de puntaje (colocación, vacas
lerdas, huecos entre/dentro de grupo, mezcla de rodeos), que vive en
`rutina.py` y recibe la "ocupación" —lo único que sí depende de la mecánica
física de la sala— como función intercambiable (`ocupacion_fn`, ver
`rutina._analizar_sesion`).

`grupos_subquery(tambo)`: para módulos FUERA de rutina/rendimiento que
también necesitan filtrar "qué [Group] son de ordeñe real" (Salud del rodeo,
Alimentación) sin hardcodear `CMSGroupMilkSetting` — ver la función más abajo.
"""
import re

import tambos


def de(tambo: str):
    """El módulo de sala que le toca a un tambo, según `tambos.tipo_sala()`."""
    tipo = tambos.tipo_sala(tambo)
    if tipo == "convencional":
        from . import convencional
        return convencional
    if tipo == "robot":
        raise ValueError(
            "Sala tipo 'robot' todavía no está implementada (no hay instalación "
            "real contra la cual verificar el esquema). Ver salas/__init__.py.")
    if tipo != "rotativa":
        raise ValueError(f"Tipo de sala desconocido: {tipo!r}")
    from . import rotativa
    return rotativa


# `sql_grupos()` viene armado para correr SOLO (trae su propio ORDER BY +
# OPTION de memoria) — SQL Server no permite ninguno de los dos dentro de una
# subquery/derived table sin TOP/OFFSET ("Incorrect syntax near OPTION" /
# "The ORDER BY clause is invalid in ... derived tables ... subqueries").
_OPTION_CLAUSE = re.compile(r"\s*OPTION\s*\([^)]*\)\s*;?\s*\Z", re.IGNORECASE)
_ORDER_BY_CLAUSE = re.compile(r"\s*ORDER\s+BY\b.*\Z", re.IGNORECASE | re.DOTALL)


def grupos_subquery(tambo: str) -> str:
    """`de(tambo).sql_grupos()`, lista para usar como derived table en un JOIN
    (p.ej. `JOIN ({grupos_subquery(tambo)}) gr ON gr.grupo = ...`) — el filtro
    de "qué [Group] son de ordeñe real" de CUALQUIER módulo que lo necesite
    (ver salud.py, conciliacion.py, conversion_historica.py), sin hardcodear
    `CMSGroupMilkSetting` (rotativa) en cada uno por separado."""
    sql = de(tambo).sql_grupos()
    sin_option = _OPTION_CLAUSE.sub("", sql)
    return _ORDER_BY_CLAUSE.sub("", sin_option)
