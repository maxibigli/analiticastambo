# -*- coding: utf-8 -*-
"""Análisis de la RUTINA de ordeño (réplica del reporte de DelPro): por cada
visita a la rotativa mide identificación → colocación de pezonera → retiro, y
separa el día en sus sesiones de ordeño para puntuar la calidad de cada una.

Mapeo de campos (verificado contra datos reales de La Ponderosa):
  Identificación (rombo)      = MilkingDeviceVisit.IDTime
  Colocación de pezonera (cuadrado) = CMSDeviceVisit.VerifiedTime (mismo OID que la visita)
  Retiro / fin (triángulo)    = CMSMilkYield.MilkConfirmTime (CMSMilkYield.MilkingDeviceVisit = visita.OID)
El objetivo de DelPro es colocar la pezonera dentro de los 90s desde la ID.
"""
import datetime
import re
import statistics

_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PUESTOS_ROTATIVA = 80    # puestos físicos de la rotativa (La Ponderosa)
GAP_SESION_MIN = 90      # separación entre visitas para considerar sesiones distintas
                         # (hay pausas normales de hasta ~1h DENTRO de una sesión, p.ej.
                         # entre lotes grandes; los cortes reales de turno son de horas)
UMBRAL_PREP_S = 90       # objetivo de colocación de pezonera (el que marca DelPro)
TOLERANCIA_PREP_S = 90   # zona de gracia: pasado el objetivo, el crédito baja gradual
                         # (no de un salto a 0) y llega a 0 recién a objetivo+tolerancia
CREDITO_SIN_COLOCAR = 0.3  # sin dato de colocación: puede ser falla de lectura, no
                           # necesariamente mal manejo, así que no cuenta como fracaso total
UMBRAL_SIN_DATOS_PREP = 0.8  # si esta fracción o más de la sesión no tiene colocación
                             # registrada, el componente no se evalúa ese día (se excluye)
FACTOR_HUECO = 3         # un gap > mediana de la sesión * este factor cuenta como "hueco"
UMBRAL_HUECO_MIN_S = 20  # piso del umbral, para sesiones con ritmo naturalmente lento
K_PENALIZACION = 3       # sensibilidad de la penalización de huecos sobre la duración total
UMBRAL_LERDA = 1.5       # vaca "lerda" = ordeño 50% más largo que la mediana de la sesión
UMBRAL_CORRIDA_MEZCLA = 3  # corrida de este largo o menos = animal(es) suelto(s) de otro rodeo

# sql_rutina() trae TODAS las visitas de un día (+ margen): en un día de mucho
# movimiento (rotativa de 80 puestos) puede superar el tope genérico de 5000
# filas de db.py y quedar truncado en silencio. Se pide un tope propio más alto
# (visto en la práctica: ~6800 filas/día en un día normal de bastante movimiento).
MAX_FILAS_DIA = 20000

# Pesos del score (documentan qué mide cada uno de los problemas que se buscan detectar).
PESOS = {
    "prep_90s": 30,        # errores de rutina: pezonera colocada a tiempo
    "lerdas": 10,          # atrasos por vacas lerdas
    "entre_grupos": 20,    # tiempos muertos entre distintos grupos
    "manejo_corral": 15,   # mal manejo de traída de animales dentro del mismo grupo
    "mezcla_rodeos": 10,   # vacas de un grupo que se mezclaron en el turno de otro
    "ocupacion": 15,       # puestos de la rotativa que giraron vacíos
}

PALETA = ["#b3382c", "#d9a066", "#4f9a94", "#d97f2b", "#7ec850", "#c2478a",
          "#5b7fd9", "#9b59b6", "#e0c341", "#3fa7a3"]

# Consulta de los grupos de ORDEÑO, para poblar el selector de "qué grupos
# incluir en el análisis" (tildar/destildar). "Grupo de ordeño" es una
# configuración explícita de DelPro (CMSGroupMilkSetting.EnableMilking),
# NO algo que se pueda inferir por estado reproductivo (se probó con
# LactationNumber/IsDryingOff y no coincidía con lo que muestra DelPro: un
# grupo puede tener vacas en producción y aun así no estar habilitado para
# ordeño, p. ej. corrales de tratamiento u otra clasificación operativa).
SQL_GRUPOS = """
    SELECT b.[Group] AS grupo, ag.Number AS numero, ag.Name AS nombre, COUNT(*) AS cantidad
    FROM BasicAnimal b
    JOIN CMSGroupMilkSetting g ON g.[Group] = b.[Group] AND g.GCRecord IS NULL
    JOIN AbstractGroup ag ON ag.OID = b.[Group] AND ag.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
      AND g.EnableMilking = 1
    GROUP BY b.[Group], ag.Number, ag.Name
    ORDER BY ag.Number
"""

# Nombre real de CADA grupo ("Rodeo 1", "Rodeo 4 - Baja"…), el mismo que se ve
# en DelPro. OJO: el número de grupo que muestra DelPro (AbstractGroup.Number)
# NO es el OID interno con el que se relacionan las tablas (BasicAnimal.[Group]
# apunta al OID). El OID se sigue usando como clave; el nombre es solo para
# mostrar. AnimalGroup comparte OID con AbstractGroup (herencia XPO).
SQL_GRUPOS_NOMBRES = """
    SELECT ag.OID AS grupo, g.Number AS numero, g.Name AS nombre
    FROM AnimalGroup ag
    JOIN AbstractGroup g ON g.OID = ag.OID
    WHERE g.GCRecord IS NULL
"""

# Cuántos ordeños por día tiene declarados el tambo (La Ponderosa: 3). Es el
# tope real de sesiones que puede tener un día: si el corte por hueco da más,
# es que una sesión se partió por una pausa larga y hay que volver a unirla.
SQL_ORDENOS_POR_DIA = """
    SELECT MAX(NumberOfMilkings) AS ordenos_dia
    FROM CMSGroupMilkSetting
    WHERE GCRecord IS NULL AND EnableMilking = 1
"""

MAX_SESIONES_DEFECTO = 3   # si no se puede leer la config, el valor habitual


def validar_fecha(fecha: str) -> str:
    if not _FECHA_RE.match(fecha or ""):
        raise ValueError("Fecha inválida (se espera AAAA-MM-DD).")
    return fecha


def normalizar_pesos(pesos: dict | None) -> dict:
    """Combina pesos personalizados (0 o más claves de PESOS, valores 0-100)
    con los pesos por defecto para las claves que falten. Ignora claves
    desconocidas o valores inválidos en vez de fallar, así una URL manual mal
    armada no rompe el análisis."""
    resultado = dict(PESOS)
    if not pesos:
        return resultado
    for clave, valor in pesos.items():
        if clave in PESOS:
            try:
                resultado[clave] = max(0.0, min(100.0, float(valor)))
            except (TypeError, ValueError):
                pass
    return resultado


def normalizar_grupos(grupos) -> set | None:
    """Convierte una lista/iterable de grupos (posiblemente strings desde la
    URL) a un set de ints. None = sin filtro (todos los grupos)."""
    if grupos is None:
        return None
    resultado = set()
    for g in grupos:
        try:
            resultado.add(int(g))
        except (TypeError, ValueError):
            pass
    return resultado


def sql_rutina(fecha: str) -> str:
    """Visitas de un día (con 6h de margen a cada lado para no cortar sesiones
    que arrancan antes de medianoche, como la primera vuelta del día)."""
    fecha = validar_fecha(fecha)
    return f"""
        SELECT
          m.Place AS puesto, b.Number AS rp, b.[Group] AS grupo,
          m.IDTime AS hora_id, c.VerifiedTime AS hora_coloc, y.MilkConfirmTime AS hora_fin,
          y.ForcedRetract AS retirada_forzada
        FROM MilkingDeviceVisit m
        JOIN BasicAnimal b ON b.OID = m.Animal
        LEFT JOIN CMSDeviceVisit c ON c.OID = m.OID
        LEFT JOIN CMSMilkYield y ON y.MilkingDeviceVisit = m.OID
        WHERE m.GCRecord IS NULL AND m.IDTime IS NOT NULL
          AND m.IDTime >= DATEADD(hour, -6, '{fecha}')
          AND m.IDTime < DATEADD(hour, 6, DATEADD(day, 1, '{fecha}'))
        ORDER BY m.IDTime
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


RANGO_RENDIMIENTO_MAX_DIAS = 31  # tope: consulta pesada, escanea todas las visitas del rango


def sql_rendimiento(desde: str, hasta: str) -> str:
    """Visitas de un RANGO de fechas (con el mismo margen de 6h), + el kg de
    cada visita — para "Rendimiento de sala" (throughput/producción de la
    rotativa), a diferencia de sql_rutina que es de calidad de rutina."""
    desde, hasta = validar_fecha(desde), validar_fecha(hasta)
    return f"""
        SELECT
          m.Place AS puesto, b.Number AS rp, b.[Group] AS grupo,
          m.IDTime AS hora_id, c.VerifiedTime AS hora_coloc, y.MilkConfirmTime AS hora_fin,
          s.TotalYield AS kg
        FROM MilkingDeviceVisit m
        JOIN BasicAnimal b ON b.OID = m.Animal
        LEFT JOIN CMSDeviceVisit c ON c.OID = m.OID
        LEFT JOIN CMSMilkYield y ON y.MilkingDeviceVisit = m.OID
        LEFT JOIN SessionMilkYield s ON s.OID = y.OID
        WHERE m.GCRecord IS NULL AND m.IDTime IS NOT NULL
          AND m.IDTime >= DATEADD(hour, -6, '{desde}')
          AND m.IDTime < DATEADD(hour, 6, DATEADD(day, 1, '{hasta}'))
        ORDER BY m.IDTime
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_identificacion(desde: str, hasta: str) -> str:
    """Por día: ordeños totales y cuántos quedaron SIN IDENTIFICAR.

    Va aparte de `sql_rendimiento` a propósito: esa filtra `IDTime IS NOT NULL`
    y ordena por IDTime porque lo necesita para separar sesiones, y justamente
    eso descarta las visitas sin identificar — con ese conjunto "desconocidos"
    solo podía dar 0, que es el bug que esto arregla. Acá el rango se filtra
    por `CreationTime` (igual que flujos.py, verificado contra el informe de
    DelPro) para que entren también las que no tienen IDTime.

    QUÉ CUENTA COMO SIN IDENTIFICAR: las visitas cuyo animal es el registro
    placeholder de DelPro (`BasicAnimal.Number = 0` — OID 2 en esta base, sin
    ExitDate, permanente). Medido sobre 2 días: 274 de 11.924 visitas (2,3%),
    con 3.350 kg.

    Dos criterios que se PROBARON Y NO SIRVEN, para no reintroducirlos:
      * `IDTime IS NULL` — de las 309 visitas sin IDTime de esos dos días, 55
        resolvían a un animal real Y verificado: "sin IDTime" no equivale a
        "sin identificar".
      * `ManualID` — viene 0 en TODAS las visitas de esta base (el tambo no
        carga identificaciones a mano), así que no discrimina nada.

    `Number IS NULL` se suma al criterio por defensa: hoy no pasa (el LEFT JOIN
    resuelve siempre), pero un animal borrado dejaría la fila sin número y es
    igual de "no identificado" para lo que mide esto.
    """
    desde, hasta = validar_fecha(desde), validar_fecha(hasta)
    sin_id = "b.Number IS NULL OR b.Number = 0"
    return f"""
        SELECT CAST(m.CreationTime AS date) AS fecha,
               COUNT(*) AS ordenos,
               SUM(CASE WHEN {sin_id} THEN 1 ELSE 0 END) AS desconocidos,
               SUM(CASE WHEN {sin_id} THEN s.TotalYield ELSE 0 END) AS kg_desconocidos
        FROM MilkingDeviceVisit m
        LEFT JOIN BasicAnimal b ON b.OID = m.Animal
        JOIN CMSMilkYield y ON y.MilkingDeviceVisit = m.OID
        JOIN SessionMilkYield s ON s.OID = y.OID
        WHERE m.GCRecord IS NULL
          AND m.CreationTime >= '{desde}'
          AND m.CreationTime < DATEADD(day, 1, '{hasta}')
        GROUP BY CAST(m.CreationTime AS date)
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def armar_identificacion(columns, rows) -> list:
    """Filas de `sql_identificacion` -> [{fecha, ordenos, desconocidos,
    kg_desconocidos, pct_identificacion}] por día."""
    idx = {c: i for i, c in enumerate(columns)}
    salida = []
    for r in rows:
        ordenos = int(r[idx["ordenos"]] or 0)
        desc = int(r[idx["desconocidos"]] or 0)
        salida.append({
            "fecha": str(r[idx["fecha"]])[:10],
            "ordenos": ordenos,
            "desconocidos": desc,
            "kg_desconocidos": round(float(r[idx["kg_desconocidos"]] or 0), 1),
            "pct_identificacion": round(100.0 * (ordenos - desc) / ordenos, 2) if ordenos else None,
        })
    return salida


def _rotaciones_rotativa(visitas: list, duracion_seg: float) -> int | None:
    """Vueltas completas de la plataforma en la sesión, estimadas con la
    mediana ID→retiro de cada visita (igual criterio que la ocupación en
    `_ocupacion_rotativa`: la rotativa es mecánica, todos los puestos giran al
    mismo ritmo). Intercambiable (`rotaciones_fn`) porque, como la ocupación,
    es lo único de "Rendimiento Sala" que depende de que haya una plataforma
    girando — una sala convencional cuenta TANDAS en su lugar."""
    totales_vuelta = [_seg(v["hora_id"], v["hora_fin"]) for v in visitas if v["hora_fin"] is not None]
    t_vuelta = statistics.median(totales_vuelta) if totales_vuelta else None
    return round(duracion_seg / t_vuelta) if t_vuelta else None


def _resumen_sesion_rendimiento(visitas: list, rotaciones_fn=None) -> dict:
    """Métricas de throughput de UNA sesión: rotaciones, producción, ordeños
    por hora, identificados/desconocidos. Réplica gráfica del reporte
    "Rendimiento Sala" de DelPro (el de la tabla densa por sala/fecha/sesión).

    `rotaciones_fn(visitas, duracion_seg) -> int | None`: None = el de la
    rotativa (ver `_rotaciones_rotativa`)."""
    rotaciones_fn = rotaciones_fn or _rotaciones_rotativa
    inicio = visitas[0]["hora_id"]
    fin = max((v["hora_fin"] or v["hora_id"]) for v in visitas)
    duracion_seg = max((fin - inicio).total_seconds(), 1)
    duracion_h = duracion_seg / 3600

    n_visitas = len(visitas)
    ordenios = [v for v in visitas if v["kg"] is not None]
    n_ordenios = len(ordenios)
    kg_total = sum(v["kg"] for v in ordenios)
    desconocidos = [v for v in ordenios if not v["rp"]]
    n_desconocidos = len(desconocidos)
    kg_desconocidos = sum(v["kg"] for v in desconocidos)
    n_identificadas = len({v["rp"] for v in ordenios if v["rp"]})

    tiempos_ordeño = [_seg(v["hora_coloc"], v["hora_fin"]) for v in visitas
                      if v["hora_coloc"] and v["hora_fin"]]
    dur_prom_ordeño = statistics.mean(tiempos_ordeño) if tiempos_ordeño else None

    n_rotaciones = rotaciones_fn(visitas, duracion_seg)

    return {
        "inicio": inicio.isoformat(), "fin": fin.isoformat(),
        "duracion_min": round(duracion_seg / 60),
        "n_rotaciones": n_rotaciones,
        "n_visitas": n_visitas, "n_ordenios": n_ordenios,
        "n_desconocidos": n_desconocidos,
        "kg_desconocidos": round(kg_desconocidos, 1),
        "kg_total": round(kg_total, 1),
        "n_identificadas": n_identificadas,
        "dur_prom_ordeño_seg": round(dur_prom_ordeño) if dur_prom_ordeño else None,
        "kg_por_hora": round(kg_total / duracion_h, 1) if duracion_h else None,
        "kg_por_ordeño": round(kg_total / n_ordenios, 1) if n_ordenios else None,
        "ordenios_por_hora": round(n_ordenios / duracion_h, 1) if duracion_h else None,
        "visitas_por_hora": round(n_visitas / duracion_h, 1) if duracion_h else None,
    }


def _grupos_sesion(visitas: list, nombres: dict | None = None,
                   grupos_ordene=None) -> list:
    """Por cada grupo presente en la sesión: cuándo entró la primera vaca de
    ESE grupo y cuándo salió la última, y cuánto duró eso -- estimación de
    "tiempo en sala" del rodeo en esta sesión (no toda la sesión: solo el
    tramo en que hubo vacas de ese grupo pasando).

    Idea del propio tambo: como el criterio de mezcla de rodeos (rutina.py)
    ya separa los grupos dentro de una sesión, con la primera y la última
    visita de cada uno alcanza -- no hace falta ninguna consulta nueva, ya
    está en las mismas visitas que arma `sql_rendimiento`.

    `grupos_ordene`: OIDs de los grupos de ordeñe REALES (de
    `salas.de(tambo).sql_grupos()`, o sea `EnableMilking = 1` en la rotativa).
    Sin esto aparecen corrales que no son de ordeñe -- secas, preparto,
    vaquillonas -- y, en una base compartida, grupos de OTROS tambos: sus
    vacas pasan sueltas por la sala y su "tiempo en sala" se estira sobre
    todo el día, ensuciando la comparación entre rodeos. Es el MISMO criterio
    que ya usa `analizar_dia` vía su parámetro `grupos` (ver api_rutina en
    app.py). None = sin filtro (compatibilidad)."""
    permitidos = set(grupos_ordene) if grupos_ordene else None
    por_grupo: dict = {}
    for v in visitas:
        if v["grupo"] is None:
            continue
        if permitidos is not None and v["grupo"] not in permitidos:
            continue
        fin = v["hora_fin"] or v["hora_id"]
        g = por_grupo.get(v["grupo"])
        if g is None:
            por_grupo[v["grupo"]] = {"entrada": v["hora_id"], "salida": fin, "n_visitas": 1,
                                     "rp": {v["rp"]} if v["rp"] else set()}
            continue
        if v["hora_id"] < g["entrada"]:
            g["entrada"] = v["hora_id"]
        if fin > g["salida"]:
            g["salida"] = fin
        g["n_visitas"] += 1
        if v["rp"]:
            g["rp"].add(v["rp"])

    grupos = []
    for g_oid, info in por_grupo.items():
        dur_seg = max((info["salida"] - info["entrada"]).total_seconds(), 0)
        grupos.append({
            "grupo": _grupo_txt(g_oid, nombres),
            "n_vacas": len(info["rp"]),
            "n_visitas": info["n_visitas"],
            "entrada": info["entrada"].isoformat(),
            "salida": info["salida"].isoformat(),
            "permanencia_min": round(dur_seg / 60, 1),
        })
    grupos.sort(key=lambda g: -g["permanencia_min"])
    return grupos


def analizar_rendimiento(columns, rows, desde: str, hasta: str, max_sesiones: int | None = None,
                         rotaciones_fn=None, nombres: dict | None = None,
                         grupos_ordene=None) -> list:
    """Versión "Rendimiento Sala" para un RANGO de fechas: separa las visitas
    en sesiones (mismo criterio de gap + fusión que analizar_dia, pero
    corriendo sobre el rango entero) y devuelve una fila por sesión con las
    métricas de throughput. Pensado para graficar la evolución, no para el
    detalle vaca por vaca de "Rutina de ordeño".

    `rotaciones_fn`: igual que en `_resumen_sesion_rendimiento` — None = el de
    la rotativa. `nombres`: {oid_grupo: "Rodeo N"} para el detalle de
    permanencia por grupo (ver `_grupos_sesion`) — sin esto, queda con el OID
    interno. `grupos_ordene`: ver `_grupos_sesion` — filtra SOLO ese detalle
    por grupo, no las métricas de throughput de la sesión (rotaciones, kg,
    ordeños/hora), que son del equipo físico y están verificadas contra el
    informe de DelPro con TODAS las visitas."""
    desde_d = datetime.datetime.strptime(validar_fecha(desde), "%Y-%m-%d").date()
    hasta_d = datetime.datetime.strptime(validar_fecha(hasta), "%Y-%m-%d").date()
    idx = {c: i for i, c in enumerate(columns)}
    visitas = []
    for r in rows:
        hora_id = _parse(r[idx["hora_id"]])
        if hora_id is None:
            continue
        visitas.append({
            "puesto": r[idx["puesto"]], "rp": r[idx["rp"]], "grupo": r[idx["grupo"]],
            "hora_id": hora_id, "hora_coloc": _parse(r[idx["hora_coloc"]]),
            "hora_fin": _parse(r[idx["hora_fin"]]), "kg": r[idx["kg"]],
            "lado": r[idx["lado"]] if "lado" in idx else None,
            "bloque": r[idx["bloque"]] if "bloque" in idx else None,
        })
    visitas.sort(key=lambda v: v["hora_id"])

    bloques, actual, anterior = [], [], None
    for v in visitas:
        if anterior is not None and (v["hora_id"] - anterior).total_seconds() > GAP_SESION_MIN * 60:
            bloques.append(actual)
            actual = []
        actual.append(v)
        anterior = v["hora_id"]
    if actual:
        bloques.append(actual)

    por_dia: dict = {}
    for vs in bloques:
        if not vs:
            continue
        por_dia.setdefault(_dia_de_bloque(vs), []).append(vs)

    sesiones = []
    for dia in sorted(por_dia):
        if dia < desde_d or dia > hasta_d:
            continue  # sesión de un día fuera del rango pedido (viene del margen ±6h)
        # Vacas DISTINTAS del día completo (no por sesión: una vaca que se
        # ordeña en más de una sesión del mismo día cuenta una sola vez) --
        # se calcula sobre los bloques originales, antes de fusionar, porque
        # la fusión es solo para no mostrar demasiadas tarjetas, no cambia
        # qué vacas pasaron ese día. Sirve para "vacas por puesto"/"vacas por
        # persona" en el tiempo (ver Rendimiento Sala en el frontend): no
        # necesita una consulta aparte a AnimalDaily (que mezclaría rebaños
        # en la base compartida) porque esta ya es la visita física de la
        # sala de ESTE tambo, igual que ordeno.py/sala_convencional.py.
        vacas_dia = len({v["rp"] for vs in por_dia[dia] for v in vs if v["rp"]})
        bloques_dia = por_dia[dia]
        if max_sesiones:
            bloques_dia = _fusionar_hasta(bloques_dia, max_sesiones)
        bloques_dia.sort(key=lambda vs: vs[0]["hora_id"])
        for i, vs in enumerate(bloques_dia):
            resumen = _resumen_sesion_rendimiento(vs, rotaciones_fn)
            resumen["fecha"] = dia.isoformat()
            resumen["sesion"] = i + 1
            resumen["vacas_dia"] = vacas_dia
            resumen["grupos"] = _grupos_sesion(vs, nombres, grupos_ordene)
            sesiones.append(resumen)
    return sesiones


def _grupo_txt(g, nombres: dict | None = None):
    """Cómo se nombra un grupo en los textos de hallazgos. Se prefiere el
    nombre real de DelPro ("Rodeo 1"); si no se pudo cargar, se cae al OID
    interno para no quedarse sin referencia."""
    if g is None:
        return "sin grupo"
    if nombres and g in nombres:
        return nombres[g]
    return f"grupo {g}"


def _seg(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _parse(v):
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v
    return datetime.datetime.fromisoformat(v)


def _dia_de_bloque(vs: list):
    """Día al que pertenece una sesión: el de la MAYORÍA de sus ordeños, no el
    de su primera visita.

    Por qué, con el caso real que lo motivó (verificado en La Ponderosa): el
    02/06/2026 el ordeño de la medianoche arrancó 23:58:09, dos minutos antes
    de las 12. Tomando el día de la primera visita, esa sesión caía en el 02/06
    y ese día quedaba con CUATRO sesiones; como el tambo tiene declarados 3
    ordeños/día, `_fusionar_hasta` daba por espurio el cuarto corte y pegaba
    dos sesiones REALES separadas por 3h42 de hueco:

        02/06 S3   15:30 -> 03/06 03:22   35.235,9 kg   2383 ordeños   (falso)

    que son exactamente 19.408,7 + 15.827,2 kg de dos sesiones distintas. Y de
    rebote el 03/06 se quedaba con 2 sesiones, sin su primer ordeño.

    Como las visitas del bloque vienen ordenadas por hora, la del medio cae
    siempre del lado donde está la mayoría: para esa sesión (1.176 ordeños,
    casi todos pasada la medianoche) da 03/06, que es el día que el tambo
    cuenta como suyo — es el PRIMER ordeño del 03/06, arrancado dos minutos
    temprano. Así ninguno de los dos días queda con 4 sesiones y no se fusiona
    nada.

    OJO al cambiarlo: `analizar_dia` (Rutina de ordeño) NO usa esto — resuelve
    lo mismo de otra forma, descartando los bloques de días vecinos ANTES de
    aplicar el tope. Por eso esa página nunca tuvo el pico.
    """
    return vs[len(vs) // 2]["hora_id"].date()


def _fusionar_hasta(bloques: list, maximo: int) -> list:
    """Une bloques adyacentes hasta que queden `maximo` como mucho, empezando
    siempre por el par separado por el hueco MÁS CHICO.

    Por qué hace falta: cortar por un umbral fijo de hueco (GAP_SESION_MIN) es
    frágil. Si dentro de un ordeño real hay una pausa mayor a ese umbral (se
    trabó una manga, se cortó la luz, se demoró la traída del último lote), esa
    única sesión aparece partida en dos. Como el tambo tiene declarado cuántos
    ordeños hace por día, ese número es el tope: lo que sobra son cortes
    espurios, y se deshacen empezando por el hueco menos significativo."""
    bloques = list(bloques)
    while len(bloques) > maximo >= 1:
        i_min, gap_min = 0, None
        for i in range(len(bloques) - 1):
            fin_i = max((v["hora_fin"] or v["hora_id"]) for v in bloques[i])
            gap = (bloques[i + 1][0]["hora_id"] - fin_i).total_seconds()
            if gap_min is None or gap < gap_min:
                i_min, gap_min = i, gap
        bloques[i_min] = bloques[i_min] + bloques[i_min + 1]
        del bloques[i_min + 1]
    return bloques


def analizar_dia(columns, rows, fecha: str, grupos=None, pesos: dict | None = None,
                 max_sesiones: int | None = None, nombres: dict | None = None,
                 ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None) -> dict:
    """Separa las visitas del día (+ margen) en sesiones y puntúa cada una.
    Solo se devuelven las sesiones que se solapan con el día pedido.

    `grupos`: set de números de grupo a INCLUIR (None = todos). Los animales
    de otros grupos se excluyen de raíz, antes de separar en sesiones, para
    que no distorsionen los huecos/mezclas del análisis.
    `pesos`: pesos 0-100 por componente (ver PESOS); None = los de por defecto.
    `max_sesiones`: tope de sesiones del día (los ordeños/día que tiene
    declarado el tambo). Si el corte por huecos da más, se vuelven a unir
    (ver _fusionar_hasta). None = sin tope.
    `nombres`: {oid_grupo: "Rodeo N"} para mostrar los nombres reales de
    DelPro en vez del OID interno (ver SQL_GRUPOS_NOMBRES).
    `umbral_prep_s`: objetivo de colocación en segundos (ver `_analizar_sesion`);
    None = UMBRAL_PREP_S."""
    grupos = normalizar_grupos(grupos)
    pesos = normalizar_pesos(pesos)
    idx = {c: i for i, c in enumerate(columns)}
    visitas = []
    for r in rows:
        hora_id = _parse(r[idx["hora_id"]])
        if hora_id is None:
            continue
        grupo = r[idx["grupo"]]
        if grupos is not None and grupo not in grupos:
            continue
        visitas.append({
            "puesto": r[idx["puesto"]], "rp": r[idx["rp"]], "grupo": grupo,
            "hora_id": hora_id, "hora_coloc": _parse(r[idx["hora_coloc"]]),
            "hora_fin": _parse(r[idx["hora_fin"]]),
            "retirada_forzada": bool(r[idx["retirada_forzada"]]) if "retirada_forzada" in idx else False,
            # "lado"/"bloque" (SideNo/BatchNo): solo los trae la consulta de una
            # sala convencional (ver `salas/convencional.py`). En la rotativa
            # quedan en None y nadie los usa — la ocupación por tanda es
            # opt-in vía `ocupacion_fn`.
            "lado": r[idx["lado"]] if "lado" in idx else None,
            "bloque": r[idx["bloque"]] if "bloque" in idx else None,
        })
    visitas.sort(key=lambda v: v["hora_id"])

    bloques, actual, anterior = [], [], None
    for v in visitas:
        if anterior is not None and (v["hora_id"] - anterior).total_seconds() > GAP_SESION_MIN * 60:
            bloques.append(actual)
            actual = []
        actual.append(v)
        anterior = v["hora_id"]
    if actual:
        bloques.append(actual)

    desde = datetime.datetime.strptime(fecha, "%Y-%m-%d")
    hasta = desde + datetime.timedelta(days=1)
    # Primero se descartan los bloques de días vecinos (vienen del margen de
    # ±6h de la consulta), y RECIÉN AHÍ se aplica el tope de sesiones: si no,
    # los bloques del día anterior gastarían cupo del día que se está mirando.
    del_dia = []
    for vs in bloques:
        if not vs:
            continue
        fin_bloque = max((v["hora_fin"] or v["hora_id"]) for v in vs)
        if fin_bloque < desde or vs[0]["hora_id"] >= hasta:
            continue  # sesión de un día adyacente (fuera del margen de interés)
        del_dia.append(vs)
    if max_sesiones:
        del_dia = _fusionar_hasta(del_dia, max_sesiones)
    sesiones = [_analizar_sesion(vs, pesos, nombres, ocupacion_fn, huecos_fn, umbral_prep_s) for vs in del_dia]
    sesiones.sort(key=lambda s: s["inicio"])
    for i, s in enumerate(sesiones):
        s["indice"] = i
    return {"fecha": fecha, "sesiones": sesiones}


DETALLE_CLAVES = ["prep_90s", "lerdas", "entre_grupos", "manejo_corral", "mezcla_rodeos", "ocupacion"]


def resumen_dia(columns, rows, fecha: str, grupos=None, pesos: dict | None = None,
                max_sesiones: int | None = None, nombres: dict | None = None,
                ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None):
    """Reduce las sesiones de un día a UN punto (promedio ponderado por vacas)
    para graficar la evolución de la rutina a lo largo del tiempo. None si el
    día no tiene ordeños (fin de semana sin datos, feriado, hueco de la copia).
    `grupos`/`pesos`/`max_sesiones`/`ocupacion_fn`/`huecos_fn`/`umbral_prep_s`:
    igual que en analizar_dia."""
    dia = analizar_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                       ocupacion_fn, huecos_fn, umbral_prep_s)
    sesiones = dia["sesiones"]
    total_vacas = sum(s["vacas"] for s in sesiones)
    if not sesiones or total_vacas == 0:
        return None
    duracion_total_min = sum(s["duracion_min"] for s in sesiones)
    punto = {"fecha": fecha, "vacas": total_vacas, "num_sesiones": len(sesiones),
             "score": round(sum(s["score"] * s["vacas"] for s in sesiones) / total_vacas),
             "vacas_por_hora": (round(total_vacas / (duracion_total_min / 60), 1)
                                if duracion_total_min else None),
             "retiradas_forzadas": sum(s["retiradas_forzadas"] for s in sesiones)}
    for clave in DETALLE_CLAVES:
        # Promedio ponderado solo entre las sesiones que sí pudieron evaluar
        # este componente (puede faltar por una falla de instrumentación).
        pares = [(s["vacas"], next(d["valor"] for d in s["detalle"] if d["clave"] == clave))
                 for s in sesiones]
        pares = [(peso, valor) for peso, valor in pares if valor is not None]
        peso_total = sum(peso for peso, _ in pares)
        punto[clave] = round(sum(peso * valor for peso, valor in pares) / peso_total) if peso_total else None
    # Detalle por sesión (no por día): para cruzar el score de UNA sesión
    # puntual contra su propio rendimiento de vacas/hora (throughput).
    punto["detalle_sesiones"] = [{
        "fecha": fecha, "inicio": s["inicio"], "score": s["score"], "vacas": s["vacas"],
        "duracion_min": s["duracion_min"],
        "vacas_por_hora": round(s["vacas"] / (s["duracion_min"] / 60), 1) if s["duracion_min"] else None,
        "retiradas_forzadas": s["retiradas_forzadas"],
    } for s in sesiones]
    return punto


def _credito_prep(prep_seg, umbral_s=UMBRAL_PREP_S):
    """Crédito 0-1 de una colocación: 100% hasta el objetivo, y de ahí baja
    GRADUAL (no de un salto a 0) hasta agotarse en objetivo+tolerancia. Pasarse
    por 30-40s no debe pesar igual que una falla real de varios minutos. Sin
    dato de colocación (posible falla de lectura, no necesariamente de rutina)
    se penaliza pero no se anula del todo.

    `umbral_s`: objetivo de colocación en segundos. Por defecto el de DelPro
    (90s, rotativa); configurable por tambo/sala porque una sala convencional
    de tandas no tiene por qué tener el mismo objetivo (ver `_analizar_sesion`)."""
    if prep_seg is None:
        return CREDITO_SIN_COLOCAR
    if prep_seg <= umbral_s:
        return 1.0
    return max(0.0, 1.0 - (prep_seg - umbral_s) / TOLERANCIA_PREP_S)


def _ocupacion_rotativa(visitas: list, duracion_seg: float) -> dict:
    """Ocupación de la plataforma: al ser mecánica, TODOS los puestos dan la
    misma cantidad de vueltas en el mismo tiempo. El tiempo de una vuelta se
    estima con la mediana ID→retiro; comparando vueltas teóricas vs vacas
    reales por puesto se ve cuánta capacidad giró vacía.

    Es el componente "ocupacion" del score, hecho INTERCAMBIABLE (parámetro
    `ocupacion_fn` de `_analizar_sesion`/`analizar_dia`) porque es el único que
    de verdad depende de la mecánica de la sala: una sala convencional no
    tiene plataforma que gire, tiene tandas por lado (ver
    `salas/convencional.py`). El resto del score (colocación, lerdas, huecos,
    mezcla) es igual de válido para cualquier sala que ordeñe vaca por vaca en
    un puesto fijo, rotativa o no.

    Devuelve {"label", "score" (0-100), "info", "hallazgos"}.
    """
    totales = [_seg(v["hora_id"], v["hora_fin"]) for v in visitas if v["hora_fin"] is not None]
    t_vuelta = statistics.median(totales) if totales else None
    con_puesto = [v for v in visitas if v["puesto"]]
    usos = {}
    for v in con_puesto:
        usos[v["puesto"]] = usos.get(v["puesto"], 0) + 1

    hallazgos = []
    if t_vuelta and con_puesto:
        n_vueltas = max(duracion_seg / t_vuelta, 1)
        score = min(100.0, 100.0 * len(con_puesto) / (PUESTOS_ROTATIVA * n_vueltas))
        info = (f"{len(con_puesto)} vacas reales de ~{round(PUESTOS_ROTATIVA * n_vueltas)} "
                f"puestos-vuelta disponibles ({round(n_vueltas)} vueltas estimadas).")
        vacios_por_puesto = {p: round(n_vueltas) - usos.get(p, 0)
                             for p in range(1, PUESTOS_ROTATIVA + 1)}
        peor_puesto, peor_vacios = max(vacios_por_puesto.items(), key=lambda kv: kv[1])
        if peor_vacios >= max(3, round(n_vueltas * 0.25)):
            hallazgos.append({
                "tipo": "vacio", "severidad": peor_vacios, "puesto": peor_puesto, "rp": None,
                "texto": f"Puesto {peor_puesto}: giró vacío en {peor_vacios} de "
                         f"~{round(n_vueltas)} vueltas (posible unidad de baja carga o fuera de servicio).",
            })
    else:
        score = 100.0
        info = "No se pudo estimar la duración de una vuelta."

    return {"label": "Ocupación de la plataforma", "score": score, "info": info, "hallazgos": hallazgos}


def _huecos_rotativa(visitas: list, duracion_seg: float, nombres: dict | None = None) -> dict:
    """Huecos entre vacas consecutivas: "entre grupos" (cambio de lote) y
    "dentro del grupo" (mismo lote, cómo se trajeron los animales al corral de
    espera). UN umbral (mediana) para toda la sesión: en la rotativa el ritmo
    de entrada es continuo aunque cambie el grupo —no hay una pausa mecánica
    al cambiar de lote—, así que separar el umbral por tipo no hace falta acá.

    Intercambiable (`huecos_fn`) por la misma razón que la ocupación: una sala
    de tandas SÍ tiene una pausa estructural entre tandas (mientras el otro
    lado ordeña) que no es un hueco real, y necesita un umbral propio por tipo
    en vez de uno solo — ver `salas.convencional._huecos_tandas`."""
    gaps = [((b["hora_id"] - a["hora_id"]).total_seconds(), a["grupo"] != b["grupo"], a, b)
            for a, b in zip(visitas, visitas[1:])]
    mediana_gap = statistics.median(g for g, _, _, _ in gaps) if gaps else 0
    umbral_hueco = max(mediana_gap * FACTOR_HUECO, UMBRAL_HUECO_MIN_S)
    exceso_entre_grupos = sum(g - mediana_gap for g, cambio, _, _ in gaps if cambio and g > umbral_hueco)
    exceso_intra_grupo = sum(g - mediana_gap for g, cambio, _, _ in gaps if not cambio and g > umbral_hueco)
    s3 = 100.0 * max(0.0, 1 - K_PENALIZACION * exceso_entre_grupos / duracion_seg)
    s4 = 100.0 * max(0.0, 1 - K_PENALIZACION * exceso_intra_grupo / duracion_seg)

    hallazgos = [{
        "tipo": "hueco_grupo", "severidad": g, "puesto": None, "rp": None,
        "texto": f"Hueco de {round(g / 60, 1)} min entre el {_grupo_txt(a['grupo'], nombres)} y el "
                 f"{_grupo_txt(b['grupo'], nombres)} a las {b['hora_id'].strftime('%H:%M')}.",
    } for g, cambio, a, b in gaps if cambio and g > umbral_hueco]

    return {
        "s3": s3, "s4": s4,
        "info3": f"{round(exceso_entre_grupos)}s perdidos en cambios de grupo.",
        "info4": f"{round(exceso_intra_grupo)}s perdidos por demoras trayendo animales.",
        "hallazgos": hallazgos,
    }


def _analizar_sesion(visitas, pesos: dict | None = None, nombres: dict | None = None,
                     ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None) -> dict:
    """`ocupacion_fn(visitas, duracion_seg) -> {label, score, info, hallazgos}`:
    el componente "ocupación" es lo único que depende de la mecánica de la
    sala (ver `_ocupacion_rotativa`). None = el de la rotativa, para no
    cambiarle el comportamiento a ningún llamador existente. Puede devolver
    `score=None` (sala sin un equivalente real de "ocupación" — ver
    `salas.convencional._sin_ocupacion`): el componente se excluye del score y
    su peso se redistribuye entre el resto, con el mismo mecanismo que ya usa
    "prep_90s" cuando falta el dato de colocación (ver más abajo).

    `huecos_fn(visitas, duracion_seg, nombres) -> {s3, s4, info3, info4,
    hallazgos}`: ídem para los huecos entre/dentro de grupo (ver
    `_huecos_rotativa`).

    `umbral_prep_s`: objetivo de colocación en segundos (None = UMBRAL_PREP_S,
    el de DelPro/rotativa). Configurable por sala: una sala de tandas puede
    tener un ritmo de colocación distinto al de una plataforma mecánica."""
    ocupacion_fn = ocupacion_fn or _ocupacion_rotativa
    huecos_fn = huecos_fn or _huecos_rotativa
    umbral_prep_s = umbral_prep_s or UMBRAL_PREP_S
    pesos = pesos or PESOS
    for v in visitas:
        v["prep_seg"] = _seg(v["hora_id"], v["hora_coloc"])
        v["ordeño_seg"] = _seg(v["hora_coloc"], v["hora_fin"])
        v["cumple_90"] = v["prep_seg"] is not None and v["prep_seg"] <= umbral_prep_s

    inicio, fin = visitas[0]["hora_id"], max((v["hora_fin"] or v["hora_id"]) for v in visitas)
    duracion_seg = max((fin - inicio).total_seconds(), 1)
    retiradas_forzadas = sum(1 for v in visitas if v["retirada_forzada"])

    # --- Colocación de pezonera dentro de los 90s (el KPI que marca DelPro),
    # con crédito gradual en vez de un corte binario (ver _credito_prep). Si a
    # casi toda la sesión le falta el dato de colocación, es una falla de
    # instrumentación (lectura), no de rutina: no se puede evaluar ese día y
    # se excluye del score en vez de penalizar (ver más abajo).
    cumplen = sum(1 for v in visitas if v["cumple_90"])
    frac_sin_coloc = sum(1 for v in visitas if v["hora_coloc"] is None) / len(visitas)
    if frac_sin_coloc >= UMBRAL_SIN_DATOS_PREP:
        s1 = None
    else:
        s1 = 100.0 * sum(_credito_prep(v["prep_seg"], umbral_prep_s) for v in visitas) / len(visitas)

    # --- Vacas lerdas: ordeño bastante más largo que la mediana de la sesión ---
    ordenios = [v["ordeño_seg"] for v in visitas if v["ordeño_seg"] is not None]
    mediana_ordeño = statistics.median(ordenios) if ordenios else None
    lerdas = 0
    for v in visitas:
        v["lerda"] = bool(mediana_ordeño and v["ordeño_seg"] and v["ordeño_seg"] > mediana_ordeño * UMBRAL_LERDA)
        lerdas += v["lerda"]
    s2 = 100.0 * (1 - lerdas / len(ordenios)) if ordenios else 100.0

    # --- Huecos: componente intercambiable, ver `huecos_fn` más arriba -------
    huecos = huecos_fn(visitas, duracion_seg, nombres)
    s3, s4 = huecos["s3"], huecos["s4"]

    # --- Vacas mezcladas de rodeos: la secuencia de la sesión debería avanzar en
    # bloques grandes por grupo. Una corrida CORTA de un grupo distinto, encajada
    # en medio del bloque de otro, es un animal suelto que se coló en ese turno
    # (a diferencia de una tanda grande del mismo grupo llegada en dos oleadas,
    # que no es "mezcla" aunque también corte la secuencia).
    corridas, ini_corrida = [], 0
    for i in range(1, len(visitas) + 1):
        if i == len(visitas) or visitas[i]["grupo"] != visitas[ini_corrida]["grupo"]:
            corridas.append((visitas[ini_corrida]["grupo"], ini_corrida, i - 1))
            ini_corrida = i
    for v in visitas:
        v["mezclada"] = False
    mezcladas_por_grupo = {}
    if len(corridas) > 1:
        for g, ini, fin_c in corridas:
            largo = fin_c - ini + 1
            if largo <= UMBRAL_CORRIDA_MEZCLA:
                for v in visitas[ini:fin_c + 1]:
                    v["mezclada"] = True
                mezcladas_por_grupo[g] = mezcladas_por_grupo.get(g, 0) + largo
    total_mezcladas = sum(mezcladas_por_grupo.values())
    s5 = 100.0 * (1 - total_mezcladas / len(visitas))

    # --- Ocupación: componente intercambiable, ver `ocupacion_fn` más arriba ---
    ocupacion = ocupacion_fn(visitas, duracion_seg)
    s6 = ocupacion["score"]

    # Score ponderado: si un componente no se pudo evaluar (None, p.ej. sin datos
    # de colocación), se excluye y su peso se redistribuye entre el resto en vez
    # de penalizar por una falla de instrumentación ajena a la rutina.
    componentes = {"prep_90s": s1, "lerdas": s2, "entre_grupos": s3,
                   "manejo_corral": s4, "mezcla_rodeos": s5, "ocupacion": s6}
    disponibles = {c: v for c, v in componentes.items() if v is not None}
    peso_total = sum(pesos[c] for c in disponibles)
    score = (round(sum(pesos[c] * v for c, v in disponibles.items()) / peso_total)
             if peso_total else 0)

    # Colores por grupo, en orden de aparición.
    color_de_grupo, grupos = {}, []
    for v in visitas:
        g = v["grupo"]
        if g not in color_de_grupo:
            color_de_grupo[g] = PALETA[len(color_de_grupo) % len(PALETA)]
        if not grupos or grupos[-1]["grupo"] != g:
            grupos.append({"grupo": g, "color": color_de_grupo[g],
                            "inicio": v["hora_id"], "fin": v["hora_id"], "cantidad": 0})
        grupos[-1]["fin"] = v["hora_fin"] or v["hora_id"]
        grupos[-1]["cantidad"] += 1

    # Hallazgos concretos: los peores casos, para poder ir directo al problema.
    hallazgos = []
    peores_prep = sorted((v for v in visitas if v["prep_seg"] is not None and v["prep_seg"] > umbral_prep_s),
                          key=lambda v: -v["prep_seg"])[:5]
    for v in peores_prep:
        hallazgos.append({
            "tipo": "prep", "severidad": v["prep_seg"], "puesto": v["puesto"], "rp": v["rp"],
            "texto": f"Puesto {v['puesto']} · RP {v['rp'] or '?'}: pezonera colocada a los "
                     f"{round(v['prep_seg'])}s (objetivo ≤{umbral_prep_s}s).",
        })
    sin_colocar = [v for v in visitas if v["hora_coloc"] is None]
    if sin_colocar:
        hallazgos.append({
            "tipo": "sin_colocar", "severidad": len(sin_colocar), "puesto": None, "rp": None,
            "texto": f"{len(sin_colocar)} identificación(es) sin colocación registrada "
                     "(posible falla de lectura o animal que se retiró).",
        })
    hallazgos.extend(huecos["hallazgos"])
    for g, cant in sorted(mezcladas_por_grupo.items(), key=lambda kv: -kv[1])[:3]:
        hallazgos.append({
            "tipo": "mezcla", "severidad": cant, "puesto": None, "rp": None,
            "texto": f"{cant} vaca(s) sueltas del {_grupo_txt(g, nombres)} se colaron en el turno de otro grupo.",
        })
    hallazgos.extend(ocupacion["hallazgos"])
    # Los huecos entre grupos son la señal más accionable (mal manejo de corral
    # entre lotes); dentro de cada tipo, el hallazgo más severo primero.
    orden_tipo = {"hueco_grupo": 0, "vacio": 1, "mezcla": 2, "prep": 3, "sin_colocar": 4}
    hallazgos.sort(key=lambda h: (orden_tipo[h["tipo"]], -h["severidad"]))
    for h in hallazgos:
        del h["severidad"]

    return {
        "inicio": inicio.isoformat(), "fin": fin.isoformat(),
        "duracion_min": round(duracion_seg / 60),
        "vacas": len(visitas), "score": max(0, min(100, score)),
        "retiradas_forzadas": retiradas_forzadas,
        "detalle": [
            {"clave": "prep_90s", "label": f"Colocación ≤{umbral_prep_s}s",
             "valor": round(s1) if s1 is not None else None,
             "peso": pesos["prep_90s"],
             "info": (f"{cumplen}/{len(visitas)} exactas dentro de los {umbral_prep_s}s (pasarse por "
                      "poco no resta todo; recién pesa fuerte pasados los "
                      f"{round((umbral_prep_s + TOLERANCIA_PREP_S) / 60, 1)} min).") if s1 is not None else
                     "Sin datos de colocación suficientes ese día (falla de instrumentación/lectura, "
                     "no se evalúa para no penalizar la rutina injustamente)."},
            {"clave": "lerdas", "label": "Sin vacas lerdas", "valor": round(s2),
             "peso": pesos["lerdas"],
             "info": (f"{lerdas} vaca(s) con ordeño 50%+ más largo que la mediana "
                      f"({round(mediana_ordeño)}s).") if mediana_ordeño else "Sin datos de duración."},
            {"clave": "entre_grupos", "label": "Sin tiempos muertos entre grupos", "valor": round(s3),
             "peso": pesos["entre_grupos"], "info": huecos["info3"]},
            {"clave": "manejo_corral", "label": "Manejo de corral (entrada fluida)", "valor": round(s4),
             "peso": pesos["manejo_corral"], "info": huecos["info4"]},
            {"clave": "mezcla_rodeos", "label": "Sin mezcla de rodeos", "valor": round(s5),
             "peso": pesos["mezcla_rodeos"],
             "info": (f"{total_mezcladas}/{len(visitas)} vacas sueltas coladas en el turno de otro grupo."
                      if total_mezcladas else "Ningún animal suelto se coló en otro turno.")},
            {"clave": "ocupacion", "label": ocupacion["label"],
             "valor": round(s6) if s6 is not None else None,
             "peso": pesos["ocupacion"], "info": ocupacion["info"]},
        ],
        "grupos": [{"grupo": g["grupo"], "nombre": _grupo_txt(g["grupo"], nombres),
                    "color": g["color"], "cantidad": g["cantidad"],
                    "inicio": g["inicio"].isoformat(), "fin": g["fin"].isoformat()} for g in grupos],
        "hallazgos": hallazgos[:8],
        "visitas": [{
            "puesto": v["puesto"], "rp": v["rp"], "grupo": v["grupo"],
            "color": color_de_grupo[v["grupo"]],
            "hora_id": v["hora_id"].isoformat(),
            "hora_coloc": v["hora_coloc"].isoformat() if v["hora_coloc"] else None,
            "hora_fin": v["hora_fin"].isoformat() if v["hora_fin"] else None,
            "prep_seg": round(v["prep_seg"]) if v["prep_seg"] is not None else None,
            "ordeño_seg": round(v["ordeño_seg"]) if v["ordeño_seg"] is not None else None,
            "cumple_90": v["cumple_90"], "lerda": v["lerda"], "mezclada": v["mezclada"],
        } for v in visitas],
    }
