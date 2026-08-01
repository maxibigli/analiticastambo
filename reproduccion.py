# -*- coding: utf-8 -*-
"""Análisis reproductivo: catálogo de indicadores, metas configurables y
evaluación del rodeo contra esas metas para dos rangos de fechas.

Replica el informe de metas reproductivas de DelPro: un árbol
Sección → Subsección → Ítem, cada ítem con una META y una CONDICIÓN (>=, <=,
>, <) que define si el valor medido cumple o no.

QUÉ SE PUEDE CALCULAR Y QUÉ NO
------------------------------
La calibración de metas es 100% confiable: es configuración del tambo, no
depende de la base.

Los valores medidos, en cambio, dependen de los eventos reproductivos de DDM,
que en esta instalación están incompletos. Se verificó contra el informe de
DelPro (rangos 2025 completo y 2026 hasta el 26/07) por tres caminos distintos
y ninguno lo reproduce:

  * `AnimalDaily` da 242 vacas/día promedio en 2025 contra las 2.678 del
    informe — está poblada solo para una fracción del rodeo.
  * `HistoryAnimalLactationInfo.OpenDays` reproduce bien la FORMA del % de
    preñez por DEL en 2025 (31/40/42/48/53 contra 24/33/36/40/48) pero queda
    ~6 puntos alto, y en 2026 se derrumba porque las lactancias en curso
    todavía no tienen el campo cargado.
  * `EventPregCheck.DaysFromInsemination` viene en 0 en toda la base, así que
    no se puede ligar cada servicio con su resultado (por eso Tasa de
    Concepción sale vacía, igual que en el informe de DelPro).

OJO con una conclusión anterior que quedó desmentida: sin filtrar por rebaño
parecía que casi la mitad de las preñeces estaban mal cargadas (796 de 1.715).
Filtrando a La Ponderosa son 19 de 920 — los datos del tambo están sanos, el
problema era de los otros dos tambos que comparten la base.

Por eso cada indicador declara su `confianza`:

  "alta"    — sale del estado actual del rodeo, leído directo y verificado
              contra DelPro.
  "media"   — ratio calculado sobre eventos; el numerador y el denominador
              vienen de la misma fuente incompleta, así que la proporción es
              orientativa aunque los absolutos no lo sean.
  "sin_datos" — no hay forma de calcularlo con lo que hay en la base. Se
              muestra vacío, igual que hace DelPro con Tasa de Concepción.

La confianza viaja al frontend y se muestra al lado del valor: el objetivo es
que nadie tome una decisión creyendo que un número orientativo es exacto.
"""
import datetime
import json
import os
import threading

import rebano

# --- Catálogo ----------------------------------------------------------------
# Estructura: (seccion, subseccion, clave, etiqueta, meta_defecto, condicion,
#             unidad, decimales, confianza)
# Las metas y condiciones por defecto son las que tiene configuradas el tambo
# hoy (capturadas de la pantalla de Metas de DelPro). El usuario las cambia
# desde la página de Calibración de Objetivos.
#
# unidad: "" (conteo), "%" o "d" (días).
_C = [
    # --- INVENTARIO ---
    ("INVENTARIO", "Vacas", "vacas_ordeno", "Vacas Ordeño", 5, ">=", "", 0, "media"),
    ("INVENTARIO", "Vacas", "vacas_secas", "Vacas Secas", 6, ">=", "", 0, "media"),
    ("INVENTARIO", "Vacas", "total_vacas", "Total Vacas", 7, ">=", "", 0, "media"),
    ("INVENTARIO", "Vacas", "pct_lactando", "% Lactando", 45, ">=", "%", 1, "media"),
    ("INVENTARIO", "Vacas", "pct_ordeno_l1", "% vacas en ordeño 1ra Lactancia", 42, ">=", "%", 1, "media"),
    ("INVENTARIO", "Vacas", "lactancias_prom", "Lactancias promedio", 6, ">=", "", 1, "media"),
    ("INVENTARIO", "Promedio días en leche", "del_prom", "Promedio Días en Leche (todas las vacas en ordeño)", 8, ">", "d", 0, "media"),
    ("INVENTARIO", "Promedio días en leche", "del_l1", "Promedio Días en Leche 1ra Lactancia", 175, "<=", "d", 0, "media"),
    ("INVENTARIO", "Promedio días en leche", "del_l2", "Promedio Días en Leche 2da Lactancia", 175, "<=", "d", 0, "media"),
    ("INVENTARIO", "Promedio días en leche", "del_l3", "Promedio Días en Leche 3ra+ lactancia", 175, "<=", "d", 0, "media"),

    # --- PREÑEZ: % de lactancias que quedaron preñadas antes del día N ---
    ("PREÑEZ", "% Preñez lactancia 1", "prenez_100_l1", "% Preñez a 100 DEL - Lact = 1", 50, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 1", "prenez_130_l1", "% Preñez a 130 DEL - Lact = 1", 65, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 1", "prenez_150_l1", "% Preñez a 150 DEL - Lact = 1", 75, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 1", "prenez_200_l1", "% Preñez a 200 DEL - Lact = 1", 89, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 1", "prenez_300_l1", "% Preñez a 300 DEL - Lact = 1", 90, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 2+", "prenez_100_l2", "% Preñez a 100 DEL - Lact > 1", 50, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 2+", "prenez_130_l2", "% Preñez a 130 DEL - Lact > 1", 65, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 2+", "prenez_150_l2", "% Preñez a 150 DEL - Lact > 1", 75, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 2+", "prenez_200_l2", "% Preñez a 200 DEL - Lact > 1", 89, ">=", "%", 1, "media"),
    ("PREÑEZ", "% Preñez lactancia 2+", "prenez_300_l2", "% Preñez a 300 DEL - Lact > 1", 90, ">=", "%", 1, "media"),

    # --- TASA DE SERVICIO: % de vacas elegibles que recibieron servicio ---
    ("TASA DE SERVICIO", "", "ts_l1_ciclo", "TS Lact=1 último ciclo 21-días", None, ">=", "%", 1, "media"),
    ("TASA DE SERVICIO", "", "ts_l1_3ciclos", "TS Lact=1 Promedio último 3 ciclos", None, ">=", "%", 1, "media"),
    ("TASA DE SERVICIO", "", "ts_l1_12m", "TS Lact=1 Promedio últimos 12 meses", None, ">=", "%", 1, "media"),
    ("TASA DE SERVICIO", "", "ts_l2_ciclo", "TS Lact>1 último ciclo 21-días", None, ">=", "%", 1, "media"),
    ("TASA DE SERVICIO", "", "ts_l2_3ciclos", "TS Lact>1 Promedio último 3 ciclos", None, ">=", "%", 1, "media"),
    ("TASA DE SERVICIO", "", "ts_l2_12m", "TS Lact>1 Promedio últimos 12 meses", None, ">=", "%", 1, "media"),

    # --- TASA DE PREÑEZ = tasa de servicio x tasa de concepción ---
    ("TASA DE PREÑEZ", "", "tp_l1_ciclo", "TP Lact=1 último ciclo 21-días", None, ">=", "%", 1, "media"),
    ("TASA DE PREÑEZ", "", "tp_l1_3ciclos", "TP Lact=1 Promedio último 3 ciclos", None, ">=", "%", 1, "media"),
    ("TASA DE PREÑEZ", "", "tp_l1_12m", "TP Lact=1 Promedio últimos 12 meses", None, ">=", "%", 1, "media"),
    ("TASA DE PREÑEZ", "", "tp_l2_ciclo", "TP Lact>1 último ciclo 21-días", None, ">=", "%", 1, "media"),
    ("TASA DE PREÑEZ", "", "tp_l2_3ciclos", "TP Lact>1 Promedio último 3 ciclos", None, ">=", "%", 1, "media"),
    ("TASA DE PREÑEZ", "", "tp_l2_12m", "TP Lact>1 Promedio últimos 12 meses", None, ">=", "%", 1, "media"),

    # --- TASA DE CONCEPCIÓN a primer servicio ---
    # En el informe de DelPro estas tres salen EN BLANCO en los dos rangos:
    # requieren ligar cada servicio con el resultado del chequeo posterior, y
    # `EventPregCheck.DaysFromInsemination` está en 0 en toda la base.
    ("TASA CONCEPCIÓN", "IA Vacas lactantes", "tc_1s_l1", "1er Serv, Lact = 1", 40, ">=", "%", 1, "sin_datos"),
    ("TASA CONCEPCIÓN", "IA Vacas lactantes", "tc_1s_l2", "1er Serv, Lact > 1", 35, ">=", "%", 1, "sin_datos"),
    ("TASA CONCEPCIÓN", "IA Novillas", "tc_1s_nov", "1er Serv (Novillas)", 37, ">=", "%", 1, "sin_datos"),

    # --- ABORTOS ---
    ("ABORTOS", "Últimos 3 ciclos", "ab_3c_l1", "Lact=1, últimos 3 ciclos", 15, "<=", "%", 1, "media"),
    ("ABORTOS", "Últimos 3 ciclos", "ab_3c_l2", "Lact>1, últimos 3 ciclos", 15, "<=", "%", 1, "media"),
    ("ABORTOS", "Últimos 12 meses", "ab_12m_l1", "Lact=1, últimos 12 meses", 15, "<=", "%", 1, "media"),
    ("ABORTOS", "Últimos 12 meses", "ab_12m_l2", "Lact>1, últimos 12 meses", 15, "<=", "%", 1, "media"),
    ("ABORTOS", "Últimos 12 meses", "pct_no_inseminar", "% No Inseminar leche", 8, "<=", "%", 1, "media"),
    ("ABORTOS", "Últimos 12 meses", "pct_prenadas", "% Preñadas", 45, ">=", "%", 1, "alta"),
]

CATALOGO = [
    {"seccion": s, "subseccion": sub, "clave": k, "label": lbl,
     "meta_defecto": meta, "condicion_defecto": cond,
     "unidad": u, "decimales": dec, "confianza": conf}
    for (s, sub, k, lbl, meta, cond, u, dec, conf) in _C
]
POR_CLAVE = {i["clave"]: i for i in CATALOGO}
CONDICIONES = (">=", "<=", ">", "<")

# Ciclo reproductivo estándar: 21 días.
CICLO_DIAS = 21
# Día de lactancia a partir del cual una vaca es elegible para servicio
# (período de espera voluntario). No hay una tabla en DDM que lo declare, así
# que se usa el estándar y se documenta.
ESPERA_VOLUNTARIA_DIAS = 50


# --- Metas configurables -----------------------------------------------------
_RUTA = os.path.join(os.path.dirname(__file__), "metas_reproductivas.json")
_lock = threading.Lock()


def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def metas() -> list:
    """Catálogo completo con la meta y condición vigentes de cada ítem."""
    guardado = _leer()
    out = []
    for item in CATALOGO:
        g = guardado.get(item["clave"], {})
        meta = g.get("meta", item["meta_defecto"])
        cond = g.get("condicion", item["condicion_defecto"])
        out.append({**item,
                    "meta": meta,
                    "condicion": cond if cond in CONDICIONES else item["condicion_defecto"]})
    return out


def guardar_metas(cambios: dict) -> list:
    """`cambios`: {clave: {"meta": num|None, "condicion": ">="}}.

    Una meta en None significa "sin meta": el ítem se muestra pero no se
    evalúa. Es lo que hace DelPro con Tasa de Servicio y Tasa de Preñez.
    """
    with _lock:
        actual = _leer()
        for clave, valores in (cambios or {}).items():
            if clave not in POR_CLAVE:
                raise ValueError(f"Indicador desconocido: {clave}")
            entrada = actual.setdefault(clave, {})
            if "meta" in valores:
                m = valores["meta"]
                if m in ("", None):
                    entrada["meta"] = None
                else:
                    try:
                        entrada["meta"] = float(m)
                    except (TypeError, ValueError):
                        raise ValueError(f"Meta inválida para {clave}: {m!r}")
            if "condicion" in valores:
                c = valores["condicion"]
                if c not in CONDICIONES:
                    raise ValueError(f"Condición inválida para {clave}: {c!r}")
                entrada["condicion"] = c
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=1)
    return metas()


def cumple(valor, meta, condicion) -> bool | None:
    """True/False si cumple la meta; None si falta el valor o la meta."""
    if valor is None or meta is None:
        return None
    if condicion == ">=":
        return valor >= meta
    if condicion == "<=":
        return valor <= meta
    if condicion == ">":
        return valor > meta
    if condicion == "<":
        return valor < meta
    return None


# --- Consultas ---------------------------------------------------------------

# Período seco: se usa para decidir si una vaca estaba en ordeñe o seca en una
# fecha pasada (está seca en los últimos PERIODO_SECO_DIAS antes de su próximo
# parto). Mismo valor que usa `proyeccion.py`.
PERIODO_SECO_DIAS = 60
# Tope de días de lactancia que se considera: más que esto y el animal ya no
# está en ese ciclo (o falta el parto siguiente).
LACTANCIA_MAX_DIAS = 700


def sql_inventario_historico(fechas: list, herd=None, periodo_seco: int = None) -> str:
    """Composición del rodeo en cada una de las fechas dadas.

    Reconstruye el estado de cada animal a una fecha pasada a partir de sus
    partos: el parto anterior a la fecha abre la lactancia, el siguiente la
    cierra, y la vaca está SECA en los últimos `PERIODO_SECO_DIAS` antes de ese
    próximo parto. Los días en leche son los días desde el parto que abrió la
    lactancia.

    Es la única forma de tener inventario histórico: DDM no guarda una foto
    diaria del rodeo, y `AnimalDaily` está poblada solo para una fracción de
    los animales. Hereda el problema de fondo de esta base —hay partos que no
    quedaron registrados— así que subestima el plantel; por eso estos ítems
    viajan con confianza "media" cuando el rango no termina hoy.
    """
    lista = " UNION ALL ".join(f"SELECT CAST('{f}' AS date) AS d" for f in fechas)
    # Una vaca está seca solo si tiene un parto siguiente REGISTRADO dentro del
    # período seco. No se marca seca por llevar muchos días de lactancia sin
    # parto posterior: en esta base faltan partos, así que esa regla mandaba a
    # "seca" a cientos de vacas que estaban ordeñándose. Contrastado contra el
    # informe de DelPro para 2025: con esta regla da 2.627 vacas en ordeñe
    # contra 2.678, y 216 días en leche contra 217; con la regla estricta daba
    # 2.242 y 176.
    periodo_seco = periodo_seco or PERIODO_SECO_DIAS
    seca = (f"CASE WHEN l.sig IS NOT NULL AND DATEDIFF(day, f.d, l.sig) <= {periodo_seco}"
            f" THEN 1 ELSE 0 END")
    return f"""
        WITH fechas AS ({lista}),
        lact AS (
            SELECT ae.BasicAnimal AS animal, ae.DateAndTime AS inicio,
                   ae.LactationNumber AS lact,
                   LEAD(ae.DateAndTime) OVER (PARTITION BY ae.BasicAnimal
                                              ORDER BY ae.DateAndTime) AS sig
            FROM EventCalving c
            JOIN AbstractAnimalEvent ae ON ae.OID = c.OID AND ae.GCRecord IS NULL
        ),
        estado AS (
            SELECT f.d, l.lact,
                   DATEDIFF(day, l.inicio, f.d) AS del,
                   {seca} AS seca
            FROM fechas f
            JOIN lact l ON l.inicio <= f.d AND (l.sig IS NULL OR l.sig > f.d)
            JOIN BasicAnimal b ON b.OID = l.animal AND b.GCRecord IS NULL AND b.Number > 0
            WHERE (b.ExitDate IS NULL OR b.ExitDate > f.d)
              AND DATEDIFF(day, l.inicio, f.d) BETWEEN 0 AND {LACTANCIA_MAX_DIAS}
              AND {rebano.filtro('b', herd)}
        )
        SELECT d,
               SUM(CASE WHEN seca = 0 THEN 1 ELSE 0 END) AS vacas_ordeno,
               SUM(CASE WHEN seca = 1 THEN 1 ELSE 0 END) AS vacas_secas,
               COUNT(*) AS total_vacas,
               AVG(CASE WHEN seca = 0 THEN lact * 1.0 END) AS lactancias_prom,
               100.0 * SUM(CASE WHEN seca = 0 AND lact = 1 THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN seca = 0 THEN 1 ELSE 0 END), 0) AS pct_ordeno_l1,
               AVG(CASE WHEN seca = 0 THEN del * 1.0 END) AS del_prom,
               AVG(CASE WHEN seca = 0 AND lact = 1 THEN del * 1.0 END) AS del_l1,
               AVG(CASE WHEN seca = 0 AND lact = 2 THEN del * 1.0 END) AS del_l2,
               AVG(CASE WHEN seca = 0 AND lact >= 3 THEN del * 1.0 END) AS del_l3
        FROM estado
        GROUP BY d
        ORDER BY d
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def fechas_muestra(desde: str, hasta: str, maximo: int = 12) -> list:
    """Fechas donde se mide la composición del rodeo dentro de un rango.

    Se toma una muestra repartida (hasta `maximo` puntos) y después se
    promedia: el inventario de un trimestre es el promedio de ese trimestre,
    no el valor de un día suelto.
    """
    d0 = datetime.date.fromisoformat(desde)
    d1 = datetime.date.fromisoformat(hasta)
    dias = (d1 - d0).days
    if dias <= 0:
        return [d1.isoformat()]
    n = min(maximo, max(2, dias // 7))
    paso = dias / (n - 1) if n > 1 else dias
    return [(d0 + datetime.timedelta(days=round(i * paso))).isoformat() for i in range(n)]


# % Preñadas sobre las vacas en ordeñe — estado actual.
def sql_pct_prenadas(herd=None) -> str:
    return f"""
    SELECT 100.0 * SUM(CASE WHEN r.IsPregnant = 1 THEN 1 ELSE 0 END)
                 / NULLIF(COUNT(*), 0) AS pct_prenadas
    FROM BasicAnimal b
    JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
      AND r.LactationNumber >= 1 AND ISNULL(r.IsDryingOff, 0) = 0
      AND {rebano.filtro('b', herd)}
"""


def sql_prenez_por_del(desde: str, hasta: str, herd=None) -> str:
    """% de lactancias iniciadas en el rango que quedaron preñadas antes del
    día N de lactancia.

    `OpenDays` es los días entre el parto y la concepción. Un 0 o un NULL
    significa "todavía no quedó preñada" (o no se cargó), así que no cuenta
    como preñez pero sí como denominador — igual que en el informe.
    """
    tramos = ", ".join(
        f"100.0 * SUM(CASE WHEN h.OpenDays > 0 AND h.OpenDays <= {n} THEN 1 ELSE 0 END)"
        f" / NULLIF(COUNT(*), 0) AS p{n}"
        for n in (100, 130, 150, 200, 300))
    return f"""
        SELECT CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END AS grupo,
               COUNT(*) AS n, {tramos}
        FROM HistoryAnimalLactationInfo h
        WHERE h.LactationNumber >= 1
          AND h.StartDate >= '{desde}' AND h.StartDate < DATEADD(day, 1, '{hasta}')
          AND {rebano.filtro_por_animal('h.Animal', herd)}
        GROUP BY CASE WHEN h.LactationNumber = 1 THEN 'l1' ELSE 'l2' END
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_servicios_por_ciclo(hasta: str, ciclos: int, herd=None, ciclo_dias: int = None) -> str:
    """Servicios (inseminaciones) y preñeces confirmadas por ciclo de 21 días
    hacia atrás desde `hasta`, separando primera lactancia del resto.

    El ciclo 0 es el más reciente. Sirve para las tres ventanas del informe:
    último ciclo (0), promedio de los últimos 3 (0-2) y de los últimos 12
    meses (todos los ciclos que entren en 365 días).
    """
    ciclo_dias = ciclo_dias or CICLO_DIAS
    dias = ciclos * ciclo_dias
    return f"""
        WITH ev AS (
            SELECT ae.BasicAnimal AS animal, ae.DateAndTime AS fecha,
                   ae.LactationNumber AS lact,
                   DATEDIFF(day, ae.DateAndTime, '{hasta}') / {ciclo_dias} AS ciclo,
                   CASE WHEN i.OID IS NOT NULL THEN 1 ELSE 0 END AS es_servicio,
                   CASE WHEN p.OID IS NOT NULL AND p.Result = 1 THEN 1 ELSE 0 END AS es_prenez
            FROM AbstractAnimalEvent ae
            LEFT JOIN EventInsemination i ON i.OID = ae.OID
            LEFT JOIN EventPregCheck p ON p.OID = ae.OID
            WHERE ae.GCRecord IS NULL
              AND ae.DateAndTime > DATEADD(day, -{dias}, '{hasta}')
              AND ae.DateAndTime <= '{hasta}'
              AND (i.OID IS NOT NULL OR p.OID IS NOT NULL)
              AND {rebano.filtro_por_animal('ae.BasicAnimal', herd)}
        )
        SELECT ciclo,
               CASE WHEN lact <= 1 THEN 'l1' ELSE 'l2' END AS grupo,
               SUM(es_servicio) AS servicios,
               SUM(es_prenez) AS preneces,
               COUNT(DISTINCT animal) AS animales
        FROM ev
        GROUP BY ciclo, CASE WHEN lact <= 1 THEN 'l1' ELSE 'l2' END
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_abortos(desde: str, hasta: str, herd=None) -> str:
    """Abortos sobre partos del mismo período, separando primera lactancia."""
    return f"""
        SELECT CASE WHEN ae.LactationNumber <= 1 THEN 'l1' ELSE 'l2' END AS grupo,
               SUM(CASE WHEN a.OID IS NOT NULL THEN 1 ELSE 0 END) AS abortos,
               SUM(CASE WHEN c.OID IS NOT NULL THEN 1 ELSE 0 END) AS partos
        FROM AbstractAnimalEvent ae
        LEFT JOIN EventAbortion a ON a.OID = ae.OID
        LEFT JOIN EventCalving c ON c.OID = ae.OID
        WHERE ae.GCRecord IS NULL
          AND ae.DateAndTime >= '{desde}' AND ae.DateAndTime < DATEADD(day, 1, '{hasta}')
          AND (a.OID IS NOT NULL OR c.OID IS NOT NULL)
          AND {rebano.filtro_por_animal('ae.BasicAnimal', herd)}
        GROUP BY CASE WHEN ae.LactationNumber <= 1 THEN 'l1' ELSE 'l2' END
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


RIESGO_TERNEROS_DIAS = 90    # ventana de riesgo para contar una baja como "temprana"


def sql_bajas_terneros(desde: str, hasta: str, riesgo_dias: int = RIESGO_TERNEROS_DIAS,
                       herd=None) -> str:
    """Terneros NACIDOS en [desde, hasta) que se dieron de baja antes de los
    `riesgo_dias` días de vida, con el motivo real de la salida.

    Se filtra por NACIMIENTO, no por fecha de la baja: contesta "de los que
    nacieron en este período, cuáles se perdieron" — una salida ocurrida
    después del rango pedido, de un ternero nacido DENTRO de él, entra igual
    (si no, un ternero de fin de mes quedaría afuera de las dos consultas).

    ESTE TAMBO NO USA UN MOTIVO ESPECÍFICO DE "MUERTE" PARA TERNEROS (ver
    `app._tablero_mortandad_terneros`): el código de sistema `ExitReason = 50`
    ("Death") no tiene ni un caso de La Ponderosa en toda la base. Por eso se
    trae CUALQUIER motivo de salida temprana — puede incluir traslados o
    ventas, además de mortandad real — y se muestra el texto tal cual para que
    quien mira la tabla lo vea y lo pueda descartar caso por caso.

    Usa `filtro_historico`: un animal dado de baja pierde su `[Group]`
    (queda NULL), así que `rebano.filtro()` no lo encontraría — ver rebano.py.
    """
    return f"""
        SELECT b.Number AS rp,
               CONVERT(varchar(10), b.BirthDate, 120) AS nacimiento,
               CONVERT(varchar(10), a.DateAndTime, 120) AS salida,
               DATEDIFF(day, b.BirthDate, a.DateAndTime) AS edad_dias,
               ISNULL(tn.ItemValue, 'sin motivo cargado') AS motivo
        FROM EventExit e
        JOIN AbstractAnimalEvent a ON a.OID = e.OID
        JOIN BasicAnimal b ON b.OID = a.BasicAnimal
        LEFT JOIN TextLookupItem tn ON tn.OID = e.ExitReason
        WHERE a.GCRecord IS NULL AND {rebano.filtro_historico('b', herd)}
          AND b.BirthDate >= '{desde}' AND b.BirthDate < '{hasta}'
          AND DATEDIFF(day, b.BirthDate, a.DateAndTime) BETWEEN 0 AND {riesgo_dias}
        ORDER BY a.DateAndTime DESC
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_partos_periodo(desde: str, hasta: str, herd=None) -> str:
    """Cantidad de partos en [desde, hasta) — el denominador de la mortandad."""
    return f"""
        SELECT COUNT(*) AS partos
        FROM EventCalving e JOIN AbstractAnimalEvent a ON a.OID = e.OID
        WHERE a.GCRecord IS NULL
          AND a.DateAndTime >= '{desde}' AND a.DateAndTime < '{hasta}'
          AND {rebano.filtro_por_animal('a.BasicAnimal', herd)}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def armar_bajas_terneros(columns, rows, partos: int, desde: str, hasta: str,
                         riesgo_dias: int = RIESGO_TERNEROS_DIAS) -> dict:
    """Tabla de bajas + el resumen (%), listo para la pantalla."""
    idx = {c: i for i, c in enumerate(columns)}
    filas = [{c: f[idx[c]] for c in idx} for f in rows]
    bajas = len(filas)
    return {
        "filas": filas,
        "resumen": {
            "partos": partos, "bajas": bajas,
            "pct": round(100 * bajas / partos, 1) if partos else None,
            "desde": desde, "hasta": hasta, "riesgo_dias": riesgo_dias,
        },
    }


# Vacas marcadas para no inseminar, sobre las vacas en ordeñe.
def sql_no_inseminar(herd=None) -> str:
    return f"""
    SELECT 100.0 * SUM(CASE WHEN b.ToBeCulled = 1 THEN 1 ELSE 0 END)
                 / NULLIF(COUNT(*), 0) AS pct
    FROM BasicAnimal b
    JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
      AND r.LactationNumber >= 1 AND ISNULL(r.IsDryingOff, 0) = 0
      AND {rebano.filtro('b', herd)}
"""


# --- Cálculo -----------------------------------------------------------------

def _fila(data, i=0):
    filas = data.get("rows") or []
    if not filas:
        return {}
    return dict(zip(data["columns"], filas[i]))


def _r(v, dec=1):
    return None if v is None else round(float(v), dec)


def _promedio_inventario(data_inv) -> dict:
    """Promedia la composición del rodeo sobre las fechas de muestra del rango."""
    filas = [dict(zip(data_inv["columns"], f)) for f in (data_inv.get("rows") or [])]
    if not filas:
        return {}

    def prom(clave):
        vals = [float(f[clave]) for f in filas if f.get(clave) is not None]
        return sum(vals) / len(vals) if vals else None

    return {k: prom(k) for k in ("vacas_ordeno", "vacas_secas", "total_vacas",
                                 "lactancias_prom", "pct_ordeno_l1",
                                 "del_prom", "del_l1", "del_l2", "del_l3")}


def valores_de_rango(data_inv, data_pren, data_prenez, data_ciclos_1, data_ciclos_3,
                     data_ciclos_12, data_abortos_3c, data_abortos_12m, data_no_insem,
                     es_actual: bool) -> dict:
    """Calcula el valor medido de cada indicador para un rango.

    El inventario se reconstruye con el MISMO método en los dos rangos, aunque
    uno de ellos termine hoy y se pudiera leer el estado actual exacto. Es a
    propósito: la pantalla existe para comparar dos períodos, y comparar un
    número reconstruido contra uno exacto mediría la diferencia entre los dos
    métodos además de la del rodeo.

    `es_actual`: True si el rango termina hoy. Solo se usa para los indicadores
    que son inherentemente del presente (% preñadas, % no inseminar): en un
    rango cerrado en el pasado se dejan vacíos en vez de mostrar el valor de
    hoy como si fuera el de entonces.
    """
    v = {}

    inv = _promedio_inventario(data_inv)
    if inv.get("total_vacas"):
        v["vacas_ordeno"] = round(inv["vacas_ordeno"]) if inv.get("vacas_ordeno") else 0
        v["vacas_secas"] = round(inv["vacas_secas"]) if inv.get("vacas_secas") else 0
        v["total_vacas"] = round(inv["total_vacas"])
        v["lactancias_prom"] = _r(inv.get("lactancias_prom"))
        v["pct_ordeno_l1"] = _r(inv.get("pct_ordeno_l1"))
        for k in ("del_prom", "del_l1", "del_l2", "del_l3"):
            v[k] = _r(inv.get(k), 0)
        v["pct_lactando"] = _r(100.0 * v["vacas_ordeno"] / v["total_vacas"])

    if es_actual:
        v["pct_prenadas"] = _r(_fila(data_pren).get("pct_prenadas"))
        v["pct_no_inseminar"] = _r(_fila(data_no_insem).get("pct"))

    # % preñez por DEL
    for fila in (data_prenez.get("rows") or []):
        d = dict(zip(data_prenez["columns"], fila))
        g = d["grupo"]
        for n in (100, 130, 150, 200, 300):
            v[f"prenez_{n}_{g}"] = _r(d.get(f"p{n}"))

    # Tasa de servicio y de preñez. El denominador es la cantidad de animales
    # con actividad reproductiva en la ventana: no hay en DDM una lista fiable
    # de "elegibles para servicio" por fecha pasada, así que la tasa sale sobre
    # los animales que aparecen en los eventos. Por eso es orientativa.
    def tasas(data, sufijo):
        acum = {}
        for fila in (data.get("rows") or []):
            d = dict(zip(data["columns"], fila))
            g = d["grupo"]
            a = acum.setdefault(g, {"serv": 0, "pren": 0, "anim": 0})
            a["serv"] += int(d.get("servicios") or 0)
            a["pren"] += int(d.get("preneces") or 0)
            a["anim"] += int(d.get("animales") or 0)
        for g, a in acum.items():
            if a["anim"]:
                v[f"ts_{g}_{sufijo}"] = _r(100.0 * a["serv"] / a["anim"])
                v[f"tp_{g}_{sufijo}"] = _r(100.0 * a["pren"] / a["anim"])

    tasas(data_ciclos_1, "ciclo")
    tasas(data_ciclos_3, "3ciclos")
    tasas(data_ciclos_12, "12m")

    def abortos(data, sufijo):
        for fila in (data.get("rows") or []):
            d = dict(zip(data["columns"], fila))
            base = int(d.get("partos") or 0) + int(d.get("abortos") or 0)
            if base:
                v[f"ab_{sufijo}_{d['grupo']}"] = _r(100.0 * int(d.get("abortos") or 0) / base)

    abortos(data_abortos_3c, "3c")
    abortos(data_abortos_12m, "12m")
    return v


def armar(valores_r1: dict, valores_r2: dict, rango1: dict, rango2: dict,
          espera_voluntaria: int = None, ciclo_dias: int = None) -> dict:
    """Arma el árbol de secciones con meta, condición, los dos valores medidos
    y si cada uno cumple."""
    vigentes = metas()
    secciones, indice = [], {}
    for item in vigentes:
        sec = indice.get(item["seccion"])
        if sec is None:
            sec = {"seccion": item["seccion"], "subsecciones": [], "_idx": {}}
            indice[item["seccion"]] = sec
            secciones.append(sec)
        sub = sec["_idx"].get(item["subseccion"])
        if sub is None:
            sub = {"subseccion": item["subseccion"], "items": []}
            sec["_idx"][item["subseccion"]] = sub
            sec["subsecciones"].append(sub)
        v1 = valores_r1.get(item["clave"])
        v2 = valores_r2.get(item["clave"])
        sub["items"].append({
            "clave": item["clave"], "label": item["label"],
            "meta": item["meta"], "condicion": item["condicion"],
            "unidad": item["unidad"], "decimales": item["decimales"],
            "confianza": item["confianza"],
            "rango1": v1, "rango2": v2,
            "cumple1": cumple(v1, item["meta"], item["condicion"]),
            "cumple2": cumple(v2, item["meta"], item["condicion"]),
        })
    for sec in secciones:
        sec.pop("_idx", None)

    def contar(clave_cumple):
        ok = sum(1 for i in vigentes
                 if (valores_r1 if clave_cumple == "cumple1" else valores_r2).get(i["clave"]) is not None
                 and cumple((valores_r1 if clave_cumple == "cumple1" else valores_r2).get(i["clave"]),
                            i["meta"], i["condicion"]) is True)
        total = sum(1 for i in vigentes
                    if cumple((valores_r1 if clave_cumple == "cumple1" else valores_r2).get(i["clave"]),
                              i["meta"], i["condicion"]) is not None)
        return {"cumplen": ok, "evaluados": total}

    return {
        "secciones": secciones,
        "rango1": rango1, "rango2": rango2,
        "resumen1": contar("cumple1"), "resumen2": contar("cumple2"),
        "condiciones": list(CONDICIONES),
        "espera_voluntaria_dias": espera_voluntaria or ESPERA_VOLUNTARIA_DIAS,
        "ciclo_dias": ciclo_dias or CICLO_DIAS,
    }
