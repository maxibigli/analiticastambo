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
# Peso mínimo (sobre 100) que tiene que quedar VIVO para animarse a publicar un
# score. Ver la nota en `_analizar_sesion`: por debajo de esto lo que queda no
# califica la rutina, y además tiende a dar alto porque los componentes que
# sobreviven son los benignos.
PESO_MINIMO_SCORE = 50
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
    # Bimodalidad de la curva de flujo. PESA 0 POR DEFECTO a propósito: en la
    # rotativa la rutina ya se mide con "prep_90s" (el tiempo real hasta
    # colocar la pezonera), que es una señal más directa. Este componente es
    # para las salas que NO registran ese instante y quedarían sin nada con qué
    # calificar la preparación — ver `salas.convencional.PESOS`, donde toma los
    # 30 puntos que allá no puede usar "prep_90s".
    "flujo": 0,
    # Vacas que la sala no logró identificar (el comodín RP 0). PESA 0 POR
    # DEFECTO por el mismo motivo que "flujo": en la rotativa el % de
    # identificación ya se muestra en Rendimiento Sala como métrica propia, y
    # meterlo al score movería el puntaje histórico de ese tambo. Las salas que
    # lo quieran adentro le ponen peso — ver `salas.convencional.PESOS`.
    "identificacion": 0,
}

# Bimodalidad: la vaca arranca a dar leche, la bajada se corta y vuelve. Es el
# síntoma clásico de estímulo pobre — pezonera colocada antes de que la oxitocina
# haga efecto. Se detecta con los cuatro tramos que guarda la base: arranque con
# flujo real y caída en el tramo siguiente. Los umbrales son los mismos que usa
# la pantalla de Flujos (ver `flujos.BIMODAL_INICIO_MIN`), en kg/min: las salas
# entregan la curva ya normalizada a esa unidad (ver `sql_flujo_ordenios`).
BIMODAL_INICIO_MIN = 0.2
# A partir de qué porcentaje de ordeños bimodales se considera que la rutina de
# estímulo está mal. Punto de partida medido, no de manual: La Ponderosa —que
# con "prep_90s" puntúa 89-94— tiene 3,4%, y La Martina 13,3%. Con 5% "sano" y
# 25% "malo", la primera queda cerca de 100 y la segunda alrededor de 60.
BIMODAL_PCT_SANO = 5.0
BIMODAL_PCT_MALO = 25.0

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
    rotativa), a diferencia de sql_rutina que es de calidad de rutina.

    `retirada_forzada` (`CMSMilkYield.ForcedRetract`) viaja también acá, para
    poder separar la cantidad de retiradas forzadas por rodeo (`_grupos_sesion`)
    sin pagar una consulta aparte — mismo criterio de `sql_rutina`.

    **NO se filtra `IDTime IS NOT NULL`**, y es importante que siga así. Ese
    filtro (que esta consulta tenía) descartaba en silencio las visitas cuya
    identificación falló del todo: no llegan a tener hora de ID, pero SÍ son
    ordeños reales, con leche medida. Medido contra el reporte "Rendimiento de
    ordeño" de DelPro del 06/07/2026, primera sesión — con el filtro faltaban
    71 visitas y todo quedaba corto; sin él, cierra exacto:

        ordeños     1.389 + 71 = 1.460   (DelPro: 1.460)
        visitas     1.437 + 71 = 1.508   (DelPro: 1.508)
        producción  20.885,5 + 974,8 = 21.860,3 kg  (DelPro: 21.860)
        desconocidos     2 + 67 = 69     (DelPro: 69)

    O sea que 67 de esas 71 son además la mayor parte de los "ordeños
    desconocidos" del reporte: al excluirlas, la aplicación mostraba ~2
    desconocidos por sesión contra los ~69 reales, y parecía identificar
    mucho mejor de lo que identifica.

    Como esas visitas no tienen `IDTime`, se usa `CreationTime` de respaldo
    para ubicarlas en el tiempo (medido sobre las 1.437 visitas con ambos
    datos ese día: `CreationTime` cae a 7,5s de `IDTime` en promedio, con un
    rango de -230s a +344s — suficiente para ordenarlas y asignarlas a su
    sesión, que dura horas). El consumidor recibe `sin_id` para saber cuáles
    son y no medir con ellas tiempos que arrancan en la identificación (ver
    `analizar_rendimiento`).

    `sql_rutina` (calidad de rutina) SÍ sigue exigiendo `IDTime`: ahí todo el
    puntaje se apoya en el tramo identificación→colocación, que para estas
    visitas no existe. Son dos preguntas distintas sobre los mismos datos.

    QUÉ CAMPO ES CADA COSA, medido contra ese mismo reporte (importa, porque
    los "parecidos" dan el doble):

      * `hora_coloc` / `hora_fin` salen de `SessionMilkYield.BeginTime` y
        `.EndTime` (arranque y fin de la bajada de leche), NO de
        `CMSDeviceVisit.VerifiedTime` / `CMSMilkYield.MilkConfirmTime`, que es
        lo que esta consulta usaba. `MilkConfirmTime` es cuándo se CONFIRMA el
        registro, no cuándo terminó el ordeño: cae unos 6 minutos después.
        Con los campos viejos la duración promedio de ordeño daba 11:19 y el
        reporte marca 05:17; con `BeginTime`→`EndTime` da 05:17 exacto (y es
        lo mismo que `CMSMilkYield.IsoDuration`, que promedia 317s). De rebote
        arregla el inicio y el fin de la sesión (00:14:38 y 04:54:34, exactos)
        y con ellos su duración y todos los promedios por hora.
      * `rotacion` es `CMSDeviceVisit.BatchOrRotation`, el número de vuelta que
        graba la propia máquina: contar sus valores distintos da las 22
        rotaciones del reporte, EXACTO. Antes se estimaba dividiendo la
        duración por la mediana del tramo ID→retiro, y daba 28: ese tramo es
        más corto que la vuelta completa de la plataforma.
      * `sesion_parlor` es `CMSDeviceVisit.ParlorSession`: la sesión de ordeño
        según la máquina (un solo valor para toda la primera sesión del día).
      * `turno` es `CMSDeviceVisit.VisitedInGroup`: EN QUÉ RODEO PASÓ la vaca
        ese día. NO confundir con `BasicAnimal.[Group]` (la columna `grupo`),
        que es el rodeo en el que está HOY: una vaca que cambió de rodeo desde
        entonces queda mal asignada en un ordeño viejo, y con eso los rodeos
        parecían mezclarse en cada vuelta cuando en realidad pasan en bloques
        limpios (medido el 06/07/2026: con `[Group]` las 22 rotaciones daban
        "mezcladas"; con `VisitedInGroup`, solo 5, y son las de transición de
        un rodeo al siguiente). Ver `_grupos_sesion`.

    `VerifiedTime` sigue estando para `sql_rutina`, donde sí corresponde: ahí
    lo que se mide es cuándo se COLOCÓ la pezonera, no cuándo empezó a bajar
    la leche."""
    desde, hasta = validar_fecha(desde), validar_fecha(hasta)
    return f"""
        SELECT
          m.Place AS puesto, b.Number AS rp, b.[Group] AS grupo,
          m.IDTime AS hora_id, m.CreationTime AS hora_creacion,
          s.BeginTime AS hora_coloc, s.EndTime AS hora_fin,
          s.TotalYield AS kg, y.ForcedRetract AS retirada_forzada,
          c.BatchOrRotation AS rotacion, c.ParlorSession AS sesion_parlor,
          c.VisitedInGroup AS turno
        FROM MilkingDeviceVisit m
        JOIN BasicAnimal b ON b.OID = m.Animal
        LEFT JOIN CMSDeviceVisit c ON c.OID = m.OID
        LEFT JOIN CMSMilkYield y ON y.MilkingDeviceVisit = m.OID
        LEFT JOIN SessionMilkYield s ON s.OID = y.OID
        WHERE m.GCRecord IS NULL
          AND COALESCE(m.IDTime, m.CreationTime) >= DATEADD(hour, -6, '{desde}')
          AND COALESCE(m.IDTime, m.CreationTime) < DATEADD(hour, 6, DATEADD(day, 1, '{hasta}'))
        ORDER BY COALESCE(m.IDTime, m.CreationTime)
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
    girando — una sala convencional cuenta TANDAS en su lugar.

    Si las visitas traen `rotacion` (`CMSDeviceVisit.BatchOrRotation`, el
    número de vuelta que graba la propia máquina) se cuentan sus valores
    distintos: es el dato REAL, y da exacto contra el reporte de DelPro (22
    rotaciones en la primera sesión del 06/07/2026).

    Sin ese dato se cae a la estimación vieja —duración sobre la mediana del
    tramo ID→retiro—, que para esa misma sesión daba 28: ese tramo es más
    corto que la vuelta completa de la plataforma, así que sobrestima. Queda
    solo como respaldo. Las visitas sin identificación no entran en la
    mediana: su `hora_id` es una hora de respaldo (ver `sql_rendimiento`)."""
    rotaciones = {v["rotacion"] for v in visitas if v.get("rotacion") is not None}
    if rotaciones:
        return len(rotaciones)
    totales_vuelta = [_seg(v["hora_id"], v["hora_fin"]) for v in visitas
                      if v["hora_fin"] is not None and not v.get("sin_id")]
    t_vuelta = statistics.median(totales_vuelta) if totales_vuelta else None
    return round(duracion_seg / t_vuelta) if t_vuelta else None


def _resumen_sesion_rendimiento(visitas: list, rotaciones_fn=None) -> dict:
    """Métricas de throughput de UNA sesión: rotaciones, producción, ordeños
    por hora, identificados/desconocidos. Réplica gráfica del reporte
    "Rendimiento Sala" de DelPro (el de la tabla densa por sala/fecha/sesión).

    `rotaciones_fn(visitas, duracion_seg) -> int | None`: None = el de la
    rotativa (ver `_rotaciones_rotativa`)."""
    rotaciones_fn = rotaciones_fn or _rotaciones_rotativa
    # La sesión va del ARRANQUE DE LECHE de la primera vaca al FIN DE ORDEÑO de
    # la última, que es como la mide DelPro (00:14:38 → 04:54:34 en la primera
    # sesión del 06/07/2026, exacto). Antes arrancaba en la primera
    # identificación y terminaba en la última confirmación de registro, y por
    # eso daba ~10 minutos larga: la duración se usa para todos los promedios
    # por hora, así que ese error los corría a todos.
    arranques = [v["hora_coloc"] for v in visitas if v["hora_coloc"]]
    finales = [v["hora_fin"] for v in visitas if v["hora_fin"]]
    inicio = min(arranques) if arranques else visitas[0]["hora_id"]
    fin = max(finales) if finales else max(v["hora_id"] for v in visitas)
    duracion_seg = max((fin - inicio).total_seconds(), 1)
    duracion_h = duracion_seg / 3600

    n_visitas = len(visitas)
    ordenios = [v for v in visitas if v["kg"] is not None]
    n_ordenios = len(ordenios)
    kg_total = sum(v["kg"] for v in ordenios)
    desconocidos = [v for v in ordenios if not v["rp"]]
    n_desconocidos = len(desconocidos)
    kg_desconocidos = sum(v["kg"] for v in desconocidos)

    # Las dos formas en que una visita queda sin dueño, que el reporte de
    # DelPro separa en columnas distintas y acá dan EXACTO (verificado contra
    # las tres sesiones del 06/07/2026):
    #   * "Vacas no identificadas": nunca se leyó nada -- la visita ni siquiera
    #     tiene hora de identificación (`sin_id`).
    #   * "Transponders desconocidos": SÍ se leyó un transponder, pero no
    #     corresponde a ninguna vaca del rodeo -- hay hora de ID y aun así el
    #     animal resuelve al registro comodín de DelPro.
    # Y "Vacas identificadas" del reporte NO son vacas distintas: son las
    # visitas que sí tienen dueño (1.508 - 67 - 2 = 1.439 en la sesión 1).
    # `n_vacas_distintas` queda aparte porque es otra pregunta, y es la que
    # usan las métricas de dotación (vacas por puesto / por persona).
    sin_dueno = [v for v in visitas if not v["rp"]]
    n_no_identificadas = sum(1 for v in sin_dueno if v.get("sin_id"))
    n_transponders_desconocidos = len(sin_dueno) - n_no_identificadas
    n_identificadas = n_visitas - len(sin_dueno)
    n_vacas_distintas = len({v["rp"] for v in ordenios if v["rp"]})

    tiempos_ordeño = [_seg(v["hora_coloc"], v["hora_fin"]) for v in visitas
                      if v["hora_coloc"] and v["hora_fin"]]
    dur_prom_ordeño = statistics.mean(tiempos_ordeño) if tiempos_ordeño else None

    n_rotaciones = rotaciones_fn(visitas, duracion_seg)

    return {
        "inicio": inicio.isoformat(), "fin": fin.isoformat(),
        "duracion_min": round(duracion_seg / 60),
        # TRUNCADO, no redondeado: es como lo muestra DelPro (04:39:55 para una
        # sesión de 16.795,66 s; redondeando daría 04:39:56).
        "duracion_seg": int(duracion_seg),
        "n_rotaciones": n_rotaciones,
        "n_visitas": n_visitas, "n_ordenios": n_ordenios,
        "n_desconocidos": n_desconocidos,
        "kg_desconocidos": round(kg_desconocidos, 1),
        "kg_total": round(kg_total, 1),
        "n_identificadas": n_identificadas,
        "n_no_identificadas": n_no_identificadas,
        "n_transponders_desconocidos": n_transponders_desconocidos,
        "n_vacas_distintas": n_vacas_distintas,
        # También truncado, por lo mismo que `duracion_seg`: verificado contra
        # tres días del reporte, redondear daba 1 segundo de más en 3 de las 9
        # sesiones (04:58 contra 04:57, etc.).
        "dur_prom_ordeño_seg": int(dur_prom_ordeño) if dur_prom_ordeño else None,
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

    LOS RODEOS PASAN EN BLOQUES, no mezclados. Cada visita se cuenta en el
    rodeo cuyo TURNO estaba corriendo, y el turno de cada vuelta se decide por
    mayoría de `VisitedInGroup` (ver `sql_rendimiento`). Dos motivos:

      * `BasicAnimal.[Group]` es el rodeo de HOY, no el del día del ordeño.
        Usándolo, las 22 rotaciones del 06/07/2026 salían "con vacas de varios
        rodeos" y parecía que la sala los mezclaba; con el turno real son
        bloques limpios (Rodeo 4 → 1 → 2 → 3 → 5 → 9) y solo se comparten las
        vueltas de transición.
      * `VisitedInGroup` viene NULL en ~19% de las visitas identificadas.
        Resolviendo la vuelta entera por mayoría, esas visitas quedan en el
        turno que de verdad estaba pasando, y NINGÚN ordeño se pierde
        (verificado: los 1.460 de esa sesión quedan asignados).

    `grupos_ordene`: OIDs de los grupos de ordeñe REALES (de
    `salas.de(tambo).sql_grupos()`, o sea `EnableMilking = 1` en la rotativa).
    Sin esto aparecen corrales que no son de ordeñe -- secas, preparto,
    vaquillonas -- y, en una base compartida, grupos de OTROS tambos: sus
    vacas pasan sueltas por la sala y su "tiempo en sala" se estira sobre
    todo el día, ensuciando la comparación entre rodeos. Es el MISMO criterio
    que ya usa `analizar_dia` vía su parámetro `grupos` (ver api_rutina en
    app.py). None = sin filtro (compatibilidad)."""
    permitidos = set(grupos_ordene) if grupos_ordene else None
    _marcar_rodeo(visitas)

    por_grupo: dict = {}
    for v in visitas:
        if v["_rodeo"] is None:
            continue
        if permitidos is not None and v["_rodeo"] not in permitidos:
            continue
        fin = v["hora_fin"] or v["hora_id"]
        arranque = v["hora_coloc"] or v["hora_id"]
        es_ordenio = v["kg"] is not None
        es_forzada = bool(v.get("retirada_forzada"))
        g = por_grupo.get(v["_rodeo"])
        if g is None:
            por_grupo[v["_rodeo"]] = {"entrada": v["hora_id"], "salida": fin,
                                      "arranque": arranque, "n_visitas": 1,
                                      "n_ordenios": 1 if es_ordenio else 0,
                                      "retiradas_forzadas": 1 if es_forzada else 0,
                                      "horas": [v["hora_id"]],
                                      "rotaciones": {v["rotacion"]} if v.get("rotacion") is not None else set(),
                                      "rp": {v["rp"]} if v["rp"] else set()}
            continue
        if v["hora_id"] < g["entrada"]:
            g["entrada"] = v["hora_id"]
        if arranque < g["arranque"]:
            g["arranque"] = arranque
        if fin > g["salida"]:
            g["salida"] = fin
        g["n_visitas"] += 1
        if es_ordenio:
            g["n_ordenios"] += 1
        if es_forzada:
            g["retiradas_forzadas"] += 1
        g["horas"].append(v["hora_id"])
        if v.get("rotacion") is not None:
            g["rotaciones"].add(v["rotacion"])
        if v["rp"]:
            g["rp"].add(v["rp"])

    dur_vuelta = _duraciones_de_vuelta(visitas)

    grupos = []
    for g_oid, info in por_grupo.items():
        dur_seg = max((info["salida"] - info["entrada"]).total_seconds(), 0)
        # Velocidad del turno: sus ordeños sobre la SUMA de lo que duraron sus
        # vueltas. No se mide de su primera vaca a su última: alcanza con que
        # una vuelta suelta del final del ordeñe le toque por mayoría para que
        # esa ventana se estire a toda la sesión (pasó el 13/07/2026: bloques
        # de 347 min en una sesión de 347). Sumando vueltas, el tiempo es el
        # que la plataforma estuvo efectivamente con ese rodeo, estén o no
        # pegadas. Medido el 06/07: los rodeos van de 111 a 375 ordeños/hora
        # alrededor de los 313 de la sesión, todos bajo el techo físico.
        bloque_seg = sum(dur_vuelta[rot] for rot in info["rotaciones"] if rot in dur_vuelta)
        if not bloque_seg:   # sin dato de vueltas (sala convencional)
            bloque_seg = max((info["salida"] - info["arranque"]).total_seconds(), 0)
        ordenios_hora = (info["n_ordenios"] / (bloque_seg / 3600)) if bloque_seg else None
        # Cuántos puestos de la plataforma ocupó, en promedio, en sus vueltas.
        vueltas = len(info["rotaciones"])
        grupos.append({
            "grupo": _grupo_txt(g_oid, nombres),
            "n_vacas": len(info["rp"]),
            "n_visitas": info["n_visitas"],
            "n_ordenios": info["n_ordenios"],
            "retiradas_forzadas": info["retiradas_forzadas"],
            "entrada": info["entrada"].isoformat(),
            "salida": info["salida"].isoformat(),
            "permanencia_min": round(dur_seg / 60, 1),
            "duracion_activa_min": round(_duracion_activa_grupo(info["horas"]) / 60, 1),
            "bloque_min": round(bloque_seg / 60, 1),
            "n_rotaciones": vueltas or None,
            "ordenios_por_hora": round(ordenios_hora, 1) if ordenios_hora else None,
            "vacas_por_vuelta": (round(info["n_ordenios"] / vueltas, 1) if vueltas else None),
        })
    grupos.sort(key=lambda g: -g["permanencia_min"])
    return grupos


def _retiradas_grupo_actual(visitas: list, nombres: dict | None = None,
                            grupos_ordene=None) -> dict:
    """Retiradas forzadas de la sesión repartidas con el criterio de DELPRO:
    por `BasicAnimal.[Group]`, o sea el rodeo en el que la vaca está HOY.

    Va aparte de `_grupos_sesion` a propósito, no es un duplicado: son dos
    preguntas distintas y las dos se usan.

      * `_grupos_sesion` agrupa por `_rodeo` (el rodeo con el que la vaca PASÓ
        ese día, `VisitedInGroup`, resuelto por vuelta). Es lo correcto para
        medir la rutina de un día -ordeños/hora, tiempo en sala-, porque deja
        fijo lo que efectivamente pasó.
      * Esto agrupa por el rodeo de hoy, que es lo que hace el reporte de
        DelPro. Tiene la propiedad incómoda de reescribir el pasado (si mañana
        se mueven 30 vacas de rodeo, el número de una fecha vieja cambia solo),
        pero es contra lo que el tambo compara.

    Medido contra el reporte real de DelPro del 28/07 al 04/08/2026, rodeos 2 y
    3, sesión por sesión (64 celdas): con `VisitedInGroup` NINGÚN día daba
    exacto; con el rodeo de hoy coinciden 37 celdas y **4 de los 8 días dan
    idénticos** en los dos rodeos (31/07, 02/08, 03/08 y 04/08). Los totales
    del día coinciden en 11 de 16 (rodeo × día). Lo que queda:

      * ±2 vacas entre la 1ª y la 2ª sesión (01/08 y 29/07): el día cierra
        exacto y lo que se corre es el CORTE entre sesiones. Acá se usa
        `ParlorSession`, el número que graba la máquina; DelPro corta un par de
        vacas distinto y sin su algoritmo no se puede replicar mejor.
      * El 28/07 no cierra con ningún criterio (Rodeo 3: 41 contra 79). Ese día
        el 12,9% de las visitas tienen un rodeo de hoy distinto al que pasaron,
        contra 5,0% de un día normal: hubo un movimiento grande de vacas justo
        ahí, y con eso ni DelPro corriendo el reporte HOY daría lo que dio
        cuando se exportó. Es la contra de este criterio, medida: reescribe el
        pasado a medida que las vacas cambian de rodeo.
      * Aparte, al 27/07 le faltan dos ordeños EN LA BASE: la máquina numera
        las sesiones y va de la 925 a la 927, la 926 no existe (674 visitas ese
        día contra ~4.800 de uno normal). No es un problema del cálculo."""
    permitidos = set(grupos_ordene) if grupos_ordene else None
    por_grupo: dict = {}
    for v in visitas:
        g = v.get("grupo")
        if g is None or (permitidos is not None and g not in permitidos):
            continue
        clave = _grupo_txt(g, nombres)
        # Todos los grupos presentes arrancan en 0: un rodeo que pasó y no tuvo
        # ninguna retirada es un dato (cero real), y el frontend lo distingue
        # de un rodeo que no pasó en esa sesión (que no aparece).
        por_grupo[clave] = por_grupo.get(clave, 0) + (1 if v.get("retirada_forzada") else 0)
    return por_grupo


def _marcar_rodeo(visitas: list) -> None:
    """Deja en cada visita `_rodeo`: EN QUÉ RODEO cuenta ese ordeño.

    Los rodeos pasan en bloques, uno tras otro, así que la unidad es la VUELTA:
    cada una se asigna al turno que la ocupa, por mayoría de `VisitedInGroup`
    (el rodeo con el que la vaca pasó ese día, ver `sql_rendimiento`). Hace
    falta resolverlo por vuelta y no visita por visita porque ese campo viene
    NULL en ~19% de las visitas identificadas; así esas visitas caen igual en
    el turno que de verdad estaba pasando y no se pierde ningún ordeño.

    Sin `VisitedInGroup` ni rotación —sala convencional— se cae al grupo del
    animal, que es el comportamiento de siempre.

    Lo usan `_grupos_sesion` y `resumen_grupos_dia`: es lo que garantiza que el
    gráfico por rodeo y la tabla del día cuenten lo mismo."""
    votos: dict = {}
    for v in visitas:
        rot, turno = v.get("rotacion"), v.get("turno")
        if rot is None or turno is None:
            continue
        votos.setdefault(rot, {})
        votos[rot][turno] = votos[rot].get(turno, 0) + 1
    turno_de_vuelta = {rot: max(c.items(), key=lambda kv: kv[1])[0]
                       for rot, c in votos.items()}
    for v in visitas:
        rot = v.get("rotacion")
        if rot is not None and rot in turno_de_vuelta:
            v["_rodeo"] = turno_de_vuelta[rot]
        else:
            v["_rodeo"] = v.get("turno") or v["grupo"]


def _duraciones_de_vuelta(visitas: list) -> dict:
    """{rotación: segundos que le llevó a la plataforma esa vuelta}.

    Una vuelta va de su arranque AL ARRANQUE DE LA SIGUIENTE, no de su arranque
    a su propio fin: ese tramo incluye el ordeño de la última vaca, que sigue
    mientras la vuelta siguiente ya empezó, y contarlo dos veces inflaba los
    tiempos un 50% (los rodeos de una sesión de 279 min sumaban 415). Medido
    así las vueltas suman la sesión, y cada una refleja su ritmo real: una
    vuelta lenta dura más.

    OJO: hay que llamarlo POR SESIÓN, no con el día entero -- entre la última
    vuelta de un ordeñe y la primera del siguiente hay horas de pausa, y esa
    pausa se le cargaría a la última vuelta del turno."""
    extremos: dict = {}
    for v in visitas:
        rot = v.get("rotacion")
        if rot is None:
            continue
        ini = v["hora_coloc"] or v["hora_id"]
        fin = v["hora_fin"] or v["hora_id"]
        r = extremos.get(rot)
        if r is None:
            extremos[rot] = [ini, fin]
            continue
        if ini < r[0]:
            r[0] = ini
        if fin > r[1]:
            r[1] = fin
    orden = sorted(extremos, key=lambda rot: extremos[rot][0])
    dur: dict = {}
    for i, rot in enumerate(orden):
        ini, fin = extremos[rot]
        sig = extremos[orden[i + 1]][0] if i + 1 < len(orden) else None
        dur[rot] = max(((sig or fin) - ini).total_seconds(), 0)
    return dur


def _separar_sesiones(visitas: list) -> list:
    """Parte las visitas en sesiones de ordeño.

    Si vienen con `sesion_parlor` (`CMSDeviceVisit.ParlorSession`) se usa ESE
    dato: es la sesión que declara la propia máquina, y coincide exacto con el
    reporte de DelPro. Resuelve de raíz el problema que motivó
    `_fusionar_hasta`: el corte por hueco parte una sesión si adentro hubo una
    pausa larga, y al volver a unirlas por el tope de ordeños/día podía pegar
    dos rondas REALES (medido el 13/07/2026: daba una sesión de 11,5 h con 46
    rotaciones, que en realidad eran dos ordeños distintos).

    Sin ese campo —sala convencional, o `sql_rutina`, que no lo trae— se cae al
    criterio de siempre: cortar por hueco mayor a `GAP_SESION_MIN`. El llamador
    sigue aplicando `_fusionar_hasta` sobre el resultado; con `ParlorSession`
    esa fusión no encuentra nada que unir, que es lo correcto."""
    if visitas and visitas[0].get("sesion_parlor") is not None:
        por_sesion: dict = {}
        for v in visitas:
            por_sesion.setdefault(v.get("sesion_parlor"), []).append(v)
        return [vs for _, vs in sorted(por_sesion.items(),
                                       key=lambda kv: kv[1][0]["hora_id"])]
    bloques, actual, anterior = [], [], None
    for v in visitas:
        if anterior is not None and (v["hora_id"] - anterior).total_seconds() > GAP_SESION_MIN * 60:
            bloques.append(actual)
            actual = []
        actual.append(v)
        anterior = v["hora_id"]
    if actual:
        bloques.append(actual)
    return bloques


def _duracion_activa_grupo(horas: list) -> float:
    """Segundos en que un grupo estuvo EFECTIVAMENTE pasando por la máquina
    dentro de una sesión -- para medir VELOCIDAD de paso (ordeños/hora por
    rodeo), a diferencia de "permanencia_min" (entrada->salida sin descontar
    nada), que mide tiempo fuera del corral y por eso SÍ quiere los huecos
    (ver "Horas/día en ordeño" en Rendimiento Sala).

    Se suman los huecos entre visitas CONSECUTIVAS DEL MISMO GRUPO, salvo los
    que superen el umbral normal (mediana de esos huecos × FACTOR_HUECO, piso
    UMBRAL_HUECO_MIN_S) -- mismo criterio que `_huecos_rotativa`. Un hueco así
    de grande no es parte del ritmo de este rodeo: es otro grupo pasando en el
    medio, o dos rondas de ordeñe físicamente separadas que `_fusionar_hasta`
    unió bajo la misma tarjeta de "sesión" (medido: Rodeo 2 del 13/07/2026,
    fusionado en una sesión de 11,5h con 638 ordeños para 327 vacas -es decir,
    dos rondas- daba 28,8 vacas/hora con el criterio viejo; con este quedan
    excluidos los ~600 min del hueco entre rondas)."""
    tiempos = sorted(horas)
    if len(tiempos) < 2:
        return 0.0
    gaps = [(b - a).total_seconds() for a, b in zip(tiempos, tiempos[1:])]
    mediana = statistics.median(gaps)
    umbral = max(mediana * FACTOR_HUECO, UMBRAL_HUECO_MIN_S)
    return sum(g for g in gaps if g <= umbral)


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
        # Sin hora de identificación se usa la de creación del registro como
        # respaldo: son ordeños reales (con leche) cuya identificación falló
        # del todo, y dejarlos afuera desviaba TODAS las métricas de esta
        # pantalla — ver el detalle medido en `sql_rendimiento`. `sin_id`
        # viaja para no medir con ellos tiempos que arrancan en la ID.
        hora_id = _parse(r[idx["hora_id"]])
        sin_id = hora_id is None
        if sin_id and "hora_creacion" in idx:
            hora_id = _parse(r[idx["hora_creacion"]])
        if hora_id is None:
            continue
        visitas.append({
            "puesto": r[idx["puesto"]], "rp": r[idx["rp"]], "grupo": r[idx["grupo"]],
            "hora_id": hora_id, "sin_id": sin_id, "hora_coloc": _parse(r[idx["hora_coloc"]]),
            "hora_fin": _parse(r[idx["hora_fin"]]), "kg": r[idx["kg"]],
            "rotacion": r[idx["rotacion"]] if "rotacion" in idx else None,
            "sesion_parlor": r[idx["sesion_parlor"]] if "sesion_parlor" in idx else None,
            "turno": r[idx["turno"]] if "turno" in idx else None,
            "lado": r[idx["lado"]] if "lado" in idx else None,
            "bloque": r[idx["bloque"]] if "bloque" in idx else None,
            "retirada_forzada": bool(r[idx["retirada_forzada"]])
                if "retirada_forzada" in idx and r[idx["retirada_forzada"]] is not None else False,
        })
    visitas.sort(key=lambda v: v["hora_id"])

    bloques = _separar_sesiones(visitas)

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
            # Las mismas retiradas forzadas con el criterio de DelPro (rodeo de
            # HOY), para la tabla que se compara contra su reporte. Ver
            # `_retiradas_grupo_actual`: no reemplaza a `grupos`, convive.
            resumen["retiradas_grupo_actual"] = _retiradas_grupo_actual(vs, nombres, grupos_ordene)
            sesiones.append(resumen)
    return sesiones


def resumen_grupos_dia(columns, rows, fecha: str, grupos_ordene=None,
                       nombres: dict | None = None, ocupacion_fn=None) -> dict:
    """Réplica del reporte "Rendimiento de Ordeño" de DelPro para UN día: una
    fila por grupo (ordeños, producción promedio, velocidad, tiempo de
    ordeñe, tiempo de estímulo, retiradas forzadas y su %), más los totales
    del día (identificación, ocupación de la plataforma).

    A diferencia de `analizar_rendimiento` (que arma "sesiones" para graficar
    la evolución), acá NO importan las sesiones: se toman TODAS las visitas
    del día -aunque hayan sido 2 o 3 rondas de ordeñe reales- y se agrupan
    directo por grupo. `_duracion_activa_grupo` ya sabe descartar los huecos
    grandes ENTRE rondas (mismo criterio que separa sesiones fusionadas en
    `_grupos_sesion`), así que no hace falta partir por sesión para que la
    velocidad no se hunda con el hueco de mediodía entre ordeños.

    `grupos_ordene`/`nombres`: igual que en `analizar_rendimiento`. `ocupacion_fn`:
    igual que en `_analizar_sesion` -- None = el de la rotativa; una sala de
    tandas no tiene un equivalente real (ver `salas.convencional._sin_ocupacion`)
    y pasa esa en su lugar."""
    ocupacion_fn = ocupacion_fn or _ocupacion_rotativa
    fecha_d = datetime.datetime.strptime(validar_fecha(fecha), "%Y-%m-%d").date()
    idx = {c: i for i, c in enumerate(columns)}
    visitas = []
    for r in rows:
        # Respaldo de hora para las visitas sin identificación — ver el mismo
        # bloque en `analizar_rendimiento` y el detalle en `sql_rendimiento`.
        hora_id = _parse(r[idx["hora_id"]])
        sin_id = hora_id is None
        if sin_id and "hora_creacion" in idx:
            hora_id = _parse(r[idx["hora_creacion"]])
        if hora_id is None:
            continue
        visitas.append({
            "puesto": r[idx["puesto"]], "rp": r[idx["rp"]], "grupo": r[idx["grupo"]],
            "hora_id": hora_id, "sin_id": sin_id, "hora_coloc": _parse(r[idx["hora_coloc"]]),
            "hora_fin": _parse(r[idx["hora_fin"]]), "kg": r[idx["kg"]],
            "rotacion": r[idx["rotacion"]] if "rotacion" in idx else None,
            "sesion_parlor": r[idx["sesion_parlor"]] if "sesion_parlor" in idx else None,
            "turno": r[idx["turno"]] if "turno" in idx else None,
            "retirada_forzada": bool(r[idx["retirada_forzada"]])
                if "retirada_forzada" in idx and r[idx["retirada_forzada"]] is not None else False,
        })
    visitas.sort(key=lambda v: v["hora_id"])

    # Sesiones de la máquina (ver `_separar_sesiones`) y quedarse con las del
    # día pedido -- `sql_rendimiento` trae ±6h de margen para no cortar una
    # sesión que arranca antes de medianoche.
    bloques = _separar_sesiones(visitas)
    visitas_dia = [v for vs in bloques if vs and _dia_de_bloque(vs) == fecha_d for v in vs]
    visitas_dia.sort(key=lambda v: v["hora_id"])

    if not visitas_dia:
        return {"grupos": [], "ordenos_total": 0, "identificadas": 0, "otros": 0,
                "pct_identificacion": None, "ocupacion": None}

    permitidos = set(grupos_ordene) if grupos_ordene else None
    # MISMO criterio que el gráfico por rodeo: se agrupa por el turno en que la
    # vaca pasó (ver `_marcar_rodeo`) y el tiempo de cada rodeo es la suma de
    # lo que duraron sus vueltas (`_duraciones_de_vuelta`), calculadas POR
    # SESIÓN para no cargarle a la última vuelta de un ordeñe la pausa hasta el
    # siguiente. Antes esta tabla agrupaba por el rodeo actual del animal y
    # medía con `_duracion_activa_grupo`: daba números distintos de los del
    # gráfico para el mismo día, y hasta imposibles (494 ordeños/hora).
    _marcar_rodeo(visitas_dia)
    dur_vuelta: dict = {}
    for vs in bloques:
        if vs and _dia_de_bloque(vs) == fecha_d:
            dur_vuelta.update(_duraciones_de_vuelta(vs))

    por_grupo: dict = {}
    for v in visitas_dia:
        if v["_rodeo"] is None or (permitidos is not None and v["_rodeo"] not in permitidos):
            continue
        g = por_grupo.setdefault(v["_rodeo"], {"horas": [], "n_ordenios": 0, "kg_total": 0.0,
                                               "retiradas_forzadas": 0, "prep_segs": [],
                                               "ordeño_segs": [], "rotaciones": set()})
        g["horas"].append(v["hora_id"])
        if v.get("rotacion") is not None:
            g["rotaciones"].add(v["rotacion"])
        if v["kg"] is not None:
            g["n_ordenios"] += 1
            g["kg_total"] += v["kg"]
        if v["retirada_forzada"]:
            g["retiradas_forzadas"] += 1
        # El tiempo de estímulo arranca en la IDENTIFICACIÓN: en una visita sin
        # ID, `hora_id` es la hora de creación de respaldo y el tramo no
        # significa nada. La duración del ordeñe, en cambio, va de colocación a
        # retiro y no depende de la ID, así que esas visitas sí entran.
        if not v.get("sin_id"):
            prep = _seg(v["hora_id"], v["hora_coloc"])
            if prep is not None:
                g["prep_segs"].append(prep)
        ordeño = _seg(v["hora_coloc"], v["hora_fin"])
        if ordeño is not None:
            g["ordeño_segs"].append(ordeño)

    grupos = []
    for g_oid, info in por_grupo.items():
        bloque_seg = sum(dur_vuelta[rot] for rot in info["rotaciones"] if rot in dur_vuelta)
        if not bloque_seg:   # sin dato de vueltas (sala convencional)
            bloque_seg = _duracion_activa_grupo(info["horas"])
        velocidad = (info["n_ordenios"] / (bloque_seg / 3600)) if bloque_seg else None
        grupos.append({
            "grupo": _grupo_txt(g_oid, nombres),
            "ordenos": info["n_ordenios"],
            "produccion_prom": (round(info["kg_total"] / info["n_ordenios"], 1)
                               if info["n_ordenios"] else None),
            "velocidad": round(velocidad, 1) if velocidad else None,
            # MEDIANA, no promedio: un puñado de visitas con el mismo
            # timestamp de retiro "pegado" (posible falla de confirmación)
            # arrastra el promedio muy por encima de lo real -- mismo
            # problema, y misma solución, que el de UFC en tablero.py. Medido
            # el 29/07/2026 en Rodeo 9: ~10 visitas de 2618s (43,6 min, un
            # cluster idéntico) subían el promedio a 985s (16,4 min) contra
            # una mediana de 628s (10,5 min), en línea con el resto de los
            # rodeos (9-11 min).
            "ordeño_prom_seg": (round(statistics.median(info["ordeño_segs"]))
                               if info["ordeño_segs"] else None),
            "prep_prom_seg": round(statistics.median(info["prep_segs"])) if info["prep_segs"] else None,
            "retiradas_forzadas": info["retiradas_forzadas"],
            "pct_retiradas": (round(100 * info["retiradas_forzadas"] / info["n_ordenios"], 1)
                             if info["n_ordenios"] else None),
        })
    grupos.sort(key=lambda g: g["grupo"])

    # Totales del día. "Identificadas" usa el mismo criterio que
    # sql_identificacion/armar_identificacion (BasicAnimal.Number = 0 es el
    # placeholder "sin identificar" de DelPro). "otros" son vacas SÍ
    # identificadas pero de un grupo que no es de ordeñe de este tambo --
    # sueltas de otro rebaño de la misma base, o sin grupo asignado.
    con_kg = [v for v in visitas_dia if v["kg"] is not None]
    ordenos_total = len(con_kg)
    desconocidos = sum(1 for v in con_kg if not v["rp"])
    identificadas = ordenos_total - desconocidos
    otros = identificadas - sum(g["ordenos"] for g in grupos)

    # Ocupación de la plataforma con la duración ACTIVA del día entero (no el
    # hueco entre rondas de ordeñe): si se usara el rango calendario completo,
    # las "vueltas estimadas" se inflarían con tiempo muerto y la ocupación
    # saldría subestimada -- mismo problema que ya se corrigió para la
    # velocidad por rodeo.
    dur_activa_dia_seg = _duracion_activa_grupo([v["hora_id"] for v in visitas_dia])
    ocupacion = ocupacion_fn(visitas_dia, dur_activa_dia_seg)["score"] if dur_activa_dia_seg else None

    return {
        "grupos": grupos,
        "ordenos_total": ordenos_total,
        "identificadas": identificadas,
        "otros": otros,
        "pct_identificacion": round(100 * identificadas / ordenos_total, 1) if ordenos_total else None,
        "ocupacion": round(ocupacion, 1) if ocupacion is not None else None,
    }


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
                 ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None,
                 mide_colocacion: bool = True,
                 prep_max_s: int | None = None, prep_label: str = "Colocación",
                 sin_prep_info: str | None = None,
                 incluir_sin_grupo: bool = False) -> dict:
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
        # UNA VACA SIN IDENTIFICAR NO TIENE RODEO, y el filtro por rodeo la
        # tiraba justo cuando es lo que hay que contar: en La Martina el
        # comodín se lleva el 17% de los ordeños y el score de identificación
        # daba 100 igual. Las salas que la necesitan piden `incluir_sin_grupo`
        # (ver `salas.convencional`); en la rotativa sigue filtrando como antes.
        # CONTRA, y hay que saberla: si se mira UN rodeo suelto, esos ordeños
        # entran igual aunque no se sepa de qué rodeo eran, así que el % sin
        # identificar de esa vista es el de la sala entera, no el del rodeo.
        if grupos is not None and grupo not in grupos:
            if not (incluir_sin_grupo and grupo is None):
                continue
        visitas.append({
            "puesto": r[idx["puesto"]], "rp": r[idx["rp"]], "grupo": grupo,
            "hora_id": hora_id, "hora_coloc": _parse(r[idx["hora_coloc"]]),
            "hora_fin": _parse(r[idx["hora_fin"]]),
            "retirada_forzada": bool(r[idx["retirada_forzada"]]) if "retirada_forzada" in idx else False,
            # `sin_id`: la sala NO leyó el collar y `hora_id` es un respaldo
            # (`BeginTime`), no la identificación. Solo lo trae la consulta de
            # la convencional. SIN ESTO el tramo hasta la leche se calcula
            # contra la propia hora de la leche y da 0s "en hora ✓" — o sea que
            # las vacas sin identificar mejoraban el puntaje de colocación.
            "sin_id": bool(r[idx["sin_id"]]) if "sin_id" in idx else False,
            # "lado"/"bloque" (SideNo/BatchNo): solo los trae la consulta de una
            # sala convencional (ver `salas/convencional.py`). En la rotativa
            # quedan en None y nadie los usa — la ocupación por tanda es
            # opt-in vía `ocupacion_fn`.
            "lado": r[idx["lado"]] if "lado" in idx else None,
            "bloque": r[idx["bloque"]] if "bloque" in idx else None,
            # Los dos primeros tramos de la curva de flujo, YA en kg/min: solo
            # los trae la consulta de la sala que puntúa el estímulo por
            # bimodalidad (ver `componente_flujo`). Sin ellos el componente
            # queda en None y se excluye, que es lo que pasa en la rotativa.
            "f0_15": r[idx["f0_15"]] if "f0_15" in idx else None,
            "f15_30": r[idx["f15_30"]] if "f15_30" in idx else None,
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
    sesiones = [_analizar_sesion(vs, pesos, nombres, ocupacion_fn, huecos_fn, umbral_prep_s,
                                 mide_colocacion, prep_max_s, prep_label,
                                 sin_prep_info) for vs in del_dia]
    sesiones.sort(key=lambda s: s["inicio"])
    for i, s in enumerate(sesiones):
        s["indice"] = i
    return {"fecha": fecha, "sesiones": sesiones}


DETALLE_CLAVES = ["prep_90s", "lerdas", "entre_grupos", "manejo_corral", "mezcla_rodeos",
                  "ocupacion", "identificacion"]


def componente_flujo(visitas: list) -> tuple:
    """Puntaje 0-100 de la rutina de ESTÍMULO, leído en la curva de flujo.

    Devuelve (score, bimodales, evaluadas). `score` es None si las visitas no
    traen curva: la sala no la registra, o el día no la tiene. Nunca se asume
    que "sin dato" es "bien".

    Por qué sirve donde "colocación" no: la bimodalidad NO necesita saber
    cuándo se colocó la pezonera, se ve en la leche misma. Si la vaca arranca,
    se corta y vuelve, es que la pezonera entró antes de que bajara la leche.
    Es una consecuencia de la rutina, no un cronómetro de la rutina — más
    indirecta que `prep_90s`, pero medible donde la otra no existe."""
    con_curva = [v for v in visitas
                 if v.get("f0_15") is not None and v.get("f15_30") is not None]
    if not con_curva:
        return None, 0, 0
    bimodales = sum(1 for v in con_curva
                    if v["f0_15"] >= BIMODAL_INICIO_MIN and v["f15_30"] < v["f0_15"])
    pct = 100.0 * bimodales / len(con_curva)
    # Interpolación lineal entre "sano" y "malo", con los extremos planos.
    if pct <= BIMODAL_PCT_SANO:
        score = 100.0
    elif pct >= BIMODAL_PCT_MALO:
        score = 0.0
    else:
        score = 100.0 * (BIMODAL_PCT_MALO - pct) / (BIMODAL_PCT_MALO - BIMODAL_PCT_SANO)
    return score, bimodales, len(con_curva)


def _score_ponderado(sesiones: list):
    """Score del día: promedio de las sesiones ponderado por vacas.

    Las sesiones SIN score (la sala no registra lo suficiente como para
    calificar, ver `_analizar_sesion`) se saltean en vez de contarse como cero
    — un cero arrastraría el día entero y diría algo que el dato no dice. Si
    ninguna sesión tiene score, el día tampoco."""
    con_score = [s for s in sesiones if s.get("score") is not None]
    vacas = sum(s["vacas"] for s in con_score)
    if not vacas:
        return None
    return round(sum(s["score"] * s["vacas"] for s in con_score) / vacas)


def resumen_dia(columns, rows, fecha: str, grupos=None, pesos: dict | None = None,
                max_sesiones: int | None = None, nombres: dict | None = None,
                ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None,
                mide_colocacion: bool = True,
                prep_max_s: int | None = None, prep_label: str = "Colocación",
                sin_prep_info: str | None = None,
                incluir_sin_grupo: bool = False):
    """Reduce las sesiones de un día a UN punto (promedio ponderado por vacas)
    para graficar la evolución de la rutina a lo largo del tiempo. None si el
    día no tiene ordeños (fin de semana sin datos, feriado, hueco de la copia).
    `grupos`/`pesos`/`max_sesiones`/`ocupacion_fn`/`huecos_fn`/`umbral_prep_s`/
    `mide_colocacion`: igual que en analizar_dia."""
    dia = analizar_dia(columns, rows, fecha, grupos, pesos, max_sesiones, nombres,
                       ocupacion_fn, huecos_fn, umbral_prep_s, mide_colocacion,
                       prep_max_s, prep_label, sin_prep_info, incluir_sin_grupo)
    sesiones = dia["sesiones"]
    total_vacas = sum(s["vacas"] for s in sesiones)
    if not sesiones or total_vacas == 0:
        return None
    duracion_total_min = sum(s["duracion_min"] for s in sesiones)
    punto = {"fecha": fecha, "vacas": total_vacas, "num_sesiones": len(sesiones),
             "score": _score_ponderado(sesiones),
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
    # Cuántas vueltas dio la plataforma. Si las visitas traen el número de
    # rotación de la máquina (`sql_rendimiento`), se cuenta el dato REAL; si no
    # —el caso de `sql_rutina`, que no lo trae— se estima con la mediana del
    # tramo ID→retiro. Las visitas sin identificación quedan fuera de esa
    # mediana (su `hora_id` es de respaldo) pero sí entran en `con_puesto`:
    # ocuparon un puesto real de la plataforma.
    rotaciones = {v["rotacion"] for v in visitas if v.get("rotacion") is not None}
    totales = [_seg(v["hora_id"], v["hora_fin"]) for v in visitas
               if v["hora_fin"] is not None and not v.get("sin_id")]
    t_vuelta = statistics.median(totales) if totales else None
    con_puesto = [v for v in visitas if v["puesto"]]
    usos = {}
    for v in con_puesto:
        usos[v["puesto"]] = usos.get(v["puesto"], 0) + 1

    hallazgos = []
    if (rotaciones or t_vuelta) and con_puesto:
        medido = bool(rotaciones)
        n_vueltas = len(rotaciones) if medido else max(duracion_seg / t_vuelta, 1)
        score = min(100.0, 100.0 * len(con_puesto) / (PUESTOS_ROTATIVA * n_vueltas))
        info = (f"{len(con_puesto)} vacas reales de {round(PUESTOS_ROTATIVA * n_vueltas)} "
                f"puestos-vuelta disponibles ({round(n_vueltas)} vueltas"
                f"{'' if medido else ' estimadas'}).")
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
                     ocupacion_fn=None, huecos_fn=None, umbral_prep_s=None,
                     mide_colocacion: bool = True,
                     prep_max_s: int | None = None, prep_label: str = "Colocación",
                     sin_prep_info: str | None = None) -> dict:
    """`mide_colocacion`: si esta sala tiene un instante real de COLOCACIÓN de
    la pezonera. En la rotativa sí (`VerifiedTime`). En una sala de tandas tipo
    Alpro NO: el único sello previo a la leche es la identificación, y la vaca
    se identifica AL ENTRAR a la sala, no en el puesto. Medido en La Martina el
    10/08/2026 sobre 2.027 ordeños, ese tramo promedia **300 segundos** y llega
    a **-434** (la ID queda después del arranque de leche), o sea que incluye
    toda la espera en el puesto y a veces ni siquiera es un intervalo válido.
    Puntuar "colocación ≤90s" con eso daba 0/727 vacas en hora y hundía el
    score a 37 contra el ~81 de la rotativa: un número que dice que el tambo
    trabaja mal cuando el dato no lo dice. Con False el componente se excluye y
    su peso se redistribuye, igual que "ocupación".

    `ocupacion_fn(visitas, duracion_seg) -> {label, score, info, hallazgos}`:
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
        # `sin_id` y `prep_max_s` son para las salas donde `hora_id` es un
        # respaldo y no la identificación real (ver `salas.convencional`): ahí
        # la fila entra igual —es un ordeño de verdad, con leche— pero su tramo
        # hasta la leche NO se puede puntuar. En la rotativa ninguna de las dos
        # cosas viaja, así que esto no cambia nada de lo que ya calculaba.
        v["prep_seg"] = None if v.get("sin_id") else _seg(v["hora_id"], v["hora_coloc"])
        if (prep_max_s is not None and v["prep_seg"] is not None
                and not 0 <= v["prep_seg"] <= prep_max_s):
            v["prep_seg"] = None
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
    # Las vacas que la sala NO identificó salen del denominador: sin lectura no
    # hay instante desde el cual medir, y hacerlas contar como "colocación
    # dudosa" cobraría dos veces el mismo problema, que ya tiene su propio
    # componente ("identificacion"). En la rotativa `sin_id` no viaja, así que
    # esto es la lista completa y el número no cambia.
    evaluables = [v for v in visitas if not v.get("sin_id")]
    info_sin_prep = None
    if not mide_colocacion or frac_sin_coloc >= UMBRAL_SIN_DATOS_PREP or not evaluables:
        # Tres motivos distintos para no evaluar, y el `info` los distingue: la
        # sala no registra el instante, el tambo todavía no definió su objetivo
        # (ver `salas.convencional.UMBRAL_PREP_S`), o ese día faltó el dato.
        s1 = None
        info_sin_prep = (sin_prep_info if not mide_colocacion else
                         "Sin datos de colocación suficientes ese día (falla de instrumentación/"
                         "lectura, no se evalúa para no penalizar la rutina injustamente).")
    else:
        s1 = 100.0 * sum(_credito_prep(v["prep_seg"], umbral_prep_s) for v in evaluables) / len(evaluables)

    # --- Vacas que la sala no logró identificar (el comodín RP 0) ------------
    # Es una falla de lectura de collar/transponder, y en una sala convencional
    # es además un problema de rutina: la vaca ordeñada sin identificar no
    # queda registrada como suya (se le pierde la producción, la conductividad
    # y el control). Ver `salas.convencional.sql_identificacion`.
    sin_identificar = sum(1 for v in visitas if v.get("rp") == 0)
    s8 = 100.0 * (1 - sin_identificar / len(visitas))

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
    # Se mide sobre `evaluables`, no sobre todas: una vaca que la sala no
    # identificó NO TIENE RODEO, así que no se puede decir si se coló en el
    # turno de otro. Contándola, los 2.677 ordeños del comodín de La Martina
    # aparecían como "mezcla" — un problema de lectura de collar disfrazado de
    # problema de manejo de corral. En la rotativa `sin_id` no viaja, así que
    # esta lista es la completa y el número no cambia.
    corridas, ini_corrida = [], 0
    for i in range(1, len(evaluables) + 1):
        if i == len(evaluables) or evaluables[i]["grupo"] != evaluables[ini_corrida]["grupo"]:
            corridas.append((evaluables[ini_corrida]["grupo"], ini_corrida, i - 1))
            ini_corrida = i
    for v in visitas:
        v["mezclada"] = False
    mezcladas_por_grupo = {}
    if len(corridas) > 1:
        for g, ini, fin_c in corridas:
            largo = fin_c - ini + 1
            if largo <= UMBRAL_CORRIDA_MEZCLA:
                for v in evaluables[ini:fin_c + 1]:
                    v["mezclada"] = True
                mezcladas_por_grupo[g] = mezcladas_por_grupo.get(g, 0) + largo
    total_mezcladas = sum(mezcladas_por_grupo.values())
    s5 = 100.0 * (1 - total_mezcladas / len(evaluables)) if evaluables else 100.0

    # --- Ocupación: componente intercambiable, ver `ocupacion_fn` más arriba ---
    ocupacion = ocupacion_fn(visitas, duracion_seg)
    s6 = ocupacion["score"]

    # Score ponderado: si un componente no se pudo evaluar (None, p.ej. sin datos
    # de colocación), se excluye y su peso se redistribuye entre el resto en vez
    # de penalizar por una falla de instrumentación ajena a la rutina.
    s7, bimodales, con_curva = componente_flujo(visitas)

    componentes = {"prep_90s": s1, "lerdas": s2, "entre_grupos": s3,
                   "manejo_corral": s4, "mezcla_rodeos": s5, "ocupacion": s6,
                   "flujo": s7, "identificacion": s8}
    disponibles = {c: v for c, v in componentes.items() if v is not None}
    peso_total = sum(pesos[c] for c in disponibles)
    # SI QUEDA MUY POCO PESO VIVO, NO HAY SCORE. Excluir un componente que no
    # aplica es correcto, pero con la mitad del peso afuera lo que queda ya no
    # es una calificación de la rutina: es el promedio de lo poco que se pudo
    # medir, y encima sale ALTO porque los componentes que sobreviven suelen
    # ser los benignos. Medido en La Martina: al excluir colocación, ocupación
    # y los dos de huecos quedaban 2 de 6 componentes (20% del peso) y el score
    # saltaba de 37 a 93 — de acusar al tambo injustamente a felicitarlo
    # injustamente. Ninguna de las dos cosas es un dato. None = "no se puede
    # calificar con lo que registra esta sala".
    score = (round(sum(pesos[c] * v for c, v in disponibles.items()) / peso_total)
             if peso_total >= PESO_MINIMO_SCORE else None)

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
        "vacas": len(visitas),
        "score": max(0, min(100, score)) if score is not None else None,
        "retiradas_forzadas": retiradas_forzadas,
        "detalle": [
            # El umbral solo se nombra si de verdad se está midiendo contra él:
            # con el componente apagado, un "≤90s" en la etiqueta se lee como el
            # objetivo de esta sala cuando en realidad es el de la rotativa.
            {"clave": "prep_90s",
             "label": f"{prep_label} ≤{umbral_prep_s}s" if mide_colocacion else prep_label,
             "valor": round(s1) if s1 is not None else None,
             "peso": pesos["prep_90s"],
             "info": (f"{cumplen}/{len(evaluables)} exactas dentro de los {umbral_prep_s}s (pasarse por "
                      "poco no resta todo; recién pesa fuerte pasados los "
                      f"{round((umbral_prep_s + TOLERANCIA_PREP_S) / 60, 1)} min).")
                     if s1 is not None else info_sin_prep},
            {"clave": "identificacion", "label": "Vacas identificadas", "valor": round(s8),
             "peso": pesos["identificacion"],
             "info": (f"{sin_identificar}/{len(visitas)} ordeños quedaron a nombre del comodín: la sala "
                      "no leyó el collar, así que esa leche no se le acredita a ninguna vaca y ese "
                      "animal queda sin control ese día."
                      if sin_identificar else "Todos los ordeños quedaron a nombre de su vaca.")},
            {"clave": "lerdas", "label": "Sin vacas lerdas", "valor": round(s2),
             "peso": pesos["lerdas"],
             "info": (f"{lerdas} vaca(s) con ordeño 50%+ más largo que la mediana "
                      f"({round(mediana_ordeño)}s).") if mediana_ordeño else "Sin datos de duración."},
            # s3/s4 pueden venir en None: la sala puede no tener cómo separar
            # una pausa real de un cambio de tanda (ver
            # `salas.convencional._huecos_tandas`). Se excluyen del score igual
            # que "ocupación" y "colocación".
            {"clave": "entre_grupos", "label": "Sin tiempos muertos entre grupos",
             "valor": round(s3) if s3 is not None else None,
             "peso": pesos["entre_grupos"], "info": huecos["info3"]},
            {"clave": "manejo_corral", "label": "Manejo de corral (entrada fluida)",
             "valor": round(s4) if s4 is not None else None,
             "peso": pesos["manejo_corral"], "info": huecos["info4"]},
            {"clave": "flujo", "label": "Estímulo (sin bimodalidad)",
             "valor": round(s7) if s7 is not None else None, "peso": pesos["flujo"],
             "info": (f"{bimodales}/{con_curva} ordeños con la bajada cortada y vuelta a "
                      f"arrancar ({round(100 * bimodales / con_curva, 1)}%), señal de pezonera "
                      f"colocada antes de que baje la leche. Sano hasta "
                      f"{round(BIMODAL_PCT_SANO)}%, malo desde {round(BIMODAL_PCT_MALO)}%."
                      if s7 is not None else
                      "Esta sala no registra la curva de flujo de cada ordeño.")},
            {"clave": "mezcla_rodeos", "label": "Sin mezcla de rodeos", "valor": round(s5),
             "peso": pesos["mezcla_rodeos"],
             "info": (f"{total_mezcladas}/{len(evaluables)} vacas sueltas coladas en el turno de otro grupo."
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
            # Para que el gráfico pueda DECIR por qué esa visita no tiene tramo,
            # en vez de mostrar un "—" mudo. `sin_id` = no se leyó el collar;
            # `sin_duenio` = se leyó pero el animal es el comodín.
            "sin_id": v["sin_id"], "sin_duenio": v["rp"] == 0,
        } for v in visitas],
    }
