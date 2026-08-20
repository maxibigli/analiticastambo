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

Lo que SÍ es distinto es qué se puede puntuar, y por eso esta sala tiene sus
propios `PESOS` y sus propios componentes (no son los de la rotativa con otro
reparto):

    prep_90s        entrada a la sala → leche, NO colocación de pezonera, y con
                    el objetivo puesto por el tambo (ver `UMBRAL_PREP_S`)
    identificacion  ordeños que quedaron a nombre del comodín — en la rotativa
                    pesa 0; acá es el 17% de los ordeños
    entre_grupos    huecos entre rodeos, cortando por RODEO y no por tanda
    manejo_corral   demoras dentro del turno del mismo rodeo
    ocupacion       tiempo con un lado de la sala vacío entre mangadas

La "ocupación" es el caso más claro: una rotativa gasta capacidad real cuando un
puesto gira vacío (la plataforma gira al mismo ritmo haya vaca o no); acá los
dos lados ALTERNAN por diseño —uno ordeña mientras el otro carga, medido minuto
a minuto— así que la mitad de los puestos están vacíos todo el tiempo y contra
esa referencia cualquier sala daría pésimo. Lo que sí cuesta plata es que un
lado tarde de más en volver a llenarse: eso es lo que mide
`_vacio_entre_mangadas`, contra la mediana de la propia sesión.
"""
import bisect
import statistics

import resumen
import rutina
import sala_convencional

NOMBRE = "Convencional"

# ESTA SALA NO MIDE "COLOCACIÓN DE PEZONERA", MIDE ENTRADA A LA SALA → LECHE, y
# la diferencia no es de nombre. El único sello anterior a la leche es
# `SessionMilkYieldEx.IdTimestamp`, y la vaca se identifica AL ENTRAR, no en el
# puesto: el tramo incluye la caminata, la espera a que se llene la mangada y
# recién al final la preparación. `MilkStartTimestamp` es el mismo instante que
# `BeginTime`, así que no hay un tercer sello para separarlas.
#
# Medido en La Martina del 05 al 11/08/2026, sobre 12.926 ordeños con ID:
#
#     p05 152s   p25 227s   p50 281s   p75 341s   p95 497s
#     negativos 135 (1,0%)   más de 30 min 49 (0,4%)
#
# O sea que el dato es MEDIBLE y está limpio —no es ruido, como parecía con la
# primera muestra de 2.027 ordeños donde solo se miró el mínimo (-434s)—, pero
# su escala es de minutos, no de segundos. Contra los 90s de DelPro daba 109 de
# 12.926 vacas "en hora" (0,8%): un 0% que no acusa a la rutina, acusa a la
# regla.
#
# POR ESO EL OBJETIVO LO PONE EL TAMBO Y NO ESTE CÓDIGO. `None` = todavía no lo
# definió y el componente no se puntúa (se excluye y su peso se reparte). Elegir
# acá un número —la mediana, por ejemplo— sería calificar a la sala contra sí
# misma: cualquier tambo daría 50 y el componente no diría nada. Es la misma
# regla de CLAUDE.md que rige los umbrales de retirada. Se carga en
# ⚙ Configuración; la pantalla muestra la mediana real de la sala para elegirlo
# con el dato a la vista.
UMBRAL_PREP_S = None

# Fuera de esta banda el tramo no es rutina, es un registro roto: 135 ordeños
# con la ID DESPUÉS de la leche y 49 de más de media hora (hasta 16 h). Esos no
# se puntúan ni para bien ni para mal — entran como "sin dato".
PREP_MAX_S = 3600

PREP_LABEL = "Entrada a la sala → leche"
PREP_SIN_UMBRAL = (
    "Esta sala no registra cuándo se COLOCA la pezonera: la vaca se identifica al entrar, "
    "así que el tramo hasta la leche incluye la caminata y la espera en el puesto. Se puede "
    "medir igual, pero el objetivo NO es el de una rotativa (acá la mediana está en minutos, "
    "no en segundos). Cargá el objetivo de esta sala en ⚙ Configuración para que se puntúe.")

# CUIDADO: ESTO NO ES IGUAL EN TODAS LAS SALAS CONVENCIONALES. El encabezado de
# este módulo documenta que en SAN JOSÉ `IdTime` SÍ era el tiempo de colocación,
# en el orden de los segundos. Como el objetivo ahora es por tambo y no por
# módulo, las dos instalaciones pueden convivir: San José carga 90 y La Martina
# el suyo. Lo que sigue siendo del módulo es que el tramo se llama
# "entrada → leche"; en una sala donde la ID sea en el puesto, ese nombre queda
# corto pero el número es el mismo.

# Pesos propios de esta sala, y NO son los de la rotativa reordenados: son otros
# componentes. Acá "prep_90s" mide entrada→leche (más ruidoso que la colocación
# real, así que pesa menos que los 30 de allá), entra "identificacion" —que en
# la rotativa pesa 0— porque en esta sala el 17% de los ordeños quedan a nombre
# del comodín, y "ocupacion" pasa a ser el tiempo vacío entre mangadas
# (ver `_vacio_entre_mangadas`). El total sigue siendo 100.
PESOS = {
    "prep_90s": 15,        # entrada a la sala → leche (objetivo del tambo)
    "identificacion": 15,  # ordeños que quedaron a nombre del comodín
    "lerdas": 10,          # atrasos por vacas lerdas
    "entre_grupos": 15,    # tiempos muertos entre rodeos
    "manejo_corral": 10,   # demoras trayendo animales dentro del mismo rodeo
    "mezcla_rodeos": 10,   # vacas de un rodeo coladas en el turno de otro
    "ocupacion": 10,       # lado de la sala completamente vacío (entre mangadas)
    "flujo": 15,           # estímulo, leído en la bimodalidad de la curva
}


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
    `rutina.sql_rutina`). `lado`/`bloque` viajan además de las columnas comunes:
    los necesita `_huecos_por_rodeo`/`_vacio_entre_mangadas`/
    `_rotaciones_tandas` (ver `analizar_dia`).

    NO SE FILTRA `IdTimestamp IS NOT NULL`, y este es el cambio que hace visible
    el problema más grande de esta sala. Ese filtro dejaba afuera a las vacas que
    la sala no logró identificar —2.677 de 2.739 sin sello de ID son del comodín
    RP 0— o sea justo los ordeños que hay que contar para medir la
    identificación. Son ordeños REALES, con leche: en La Martina, 2.728 de
    15.665 en una semana (17,4%), y la mañana del 11/08 llegó al 18,8% contra
    3,3% y 4,1% de las otras dos sesiones de ese mismo día.

    Es el mismo error que ya se había corregido en `rutina.sql_rendimiento` de
    la rotativa, con la misma consecuencia: la pantalla parecía identificar
    mucho mejor de lo que identifica.

    `hora_id` cae a `BeginTime` cuando no hay sello (si no, la fila no tendría
    eje de tiempo y se rompería el corte en sesiones), y `sin_id` viaja al lado
    para que el tramo hasta la leche NO se puntúe en esas filas: sin lectura no
    hay desde dónde medir. Ver `rutina._analizar_sesion`."""
    return f"""
        SELECT ex.MPCNo AS puesto, b.Number AS rp, b.[Group] AS grupo,
               COALESCE(ex.IdTimestamp, y.BeginTime) AS hora_id,
               CASE WHEN ex.IdTimestamp IS NULL THEN 1 ELSE 0 END AS sin_id,
               y.BeginTime AS hora_coloc, y.EndTime AS hora_fin,
               CAST(ex.ForcedRetract AS int) AS retirada_forzada,
               ex.SideNo AS lado, ex.BatchNo AS bloque,
               -- Curva de flujo para el componente de estimulo, ya en kg/min
               -- (ver ESCALA_FLUJO: en Alpro estos tramos vienen x100).
               ex.FlowZerotoFifteen   * 0.01 AS f0_15,
               ex.FlowFifteentoThirty * 0.01 AS f15_30
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE COALESCE(ex.IdTimestamp, y.BeginTime) >= DATEADD(hour, -6, '{fecha}')
          AND COALESCE(ex.IdTimestamp, y.BeginTime) < DATEADD(hour, 6, DATEADD(day, 1, '{fecha}'))
        ORDER BY COALESCE(ex.IdTimestamp, y.BeginTime)
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def _tramos_ocupados(visitas: list) -> list:
    """Los intervalos [arranque de leche, fin] de una lista de visitas, unidos
    cuando se solapan. Sirve para saber cuándo un lado tuvo AL MENOS una vaca
    puesta."""
    crudos = sorted((v["hora_coloc"], v["hora_fin"]) for v in visitas
                    if v["hora_coloc"] and v["hora_fin"])
    unidos = []
    for ini, fin in crudos:
        if unidos and ini <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], fin)
        else:
            unidos.append([ini, fin])
    return unidos


class _OcupacionLado:
    """Cuántas vacas tuvo puestas un lado en cada instante, y cuál fue su pico.

    Se arma una sola vez por sesión con los eventos de enganche y retiro, y
    después se consulta por intervalos. El pico observado hace de CAPACIDAD del
    lado: no se toma de la configuración a propósito, porque lo que importa es
    cuántos puestos se usaron de verdad esa sesión (medido el 11/08: 33 y 31 a
    la mañana contra 30 y 30 en las otras dos, por solapes de un segundo entre
    el fin de una vaca y el enganche de la siguiente)."""

    def __init__(self, visitas: list):
        eventos = {}
        for v in visitas:
            if v.get("lado") is None or not v["hora_coloc"] or not v["hora_fin"]:
                continue
            eventos.setdefault(v["lado"], []).extend(
                [(v["hora_coloc"], 1), (v["hora_fin"], -1)])
        self._t, self._n, self.capacidad = {}, {}, {}
        for lado, evs in eventos.items():
            evs.sort()
            tiempos, cuenta, actual = [], [], 0
            for t, delta in evs:
                actual += delta
                tiempos.append(t)
                cuenta.append(actual)
            self._t[lado], self._n[lado] = tiempos, cuenta
            self.capacidad[lado] = max(cuenta) if cuenta else 0

    def rango(self, lado, desde, hasta) -> tuple:
        """(mínimo, máximo) de vacas puestas en ese lado durante [desde, hasta).
        (None, None) si no hay datos de ese lado."""
        tiempos = self._t.get(lado)
        if not tiempos:
            return None, None
        i = bisect.bisect_right(tiempos, desde) - 1
        actual = self._n[lado][i] if i >= 0 else 0
        mn = mx = actual
        j = bisect.bisect_right(tiempos, desde)
        while j < len(tiempos) and tiempos[j] < hasta:
            mn = min(mn, self._n[lado][j])
            mx = max(mx, self._n[lado][j])
            j += 1
        return mn, mx


def _vacios_por_lado(visitas: list) -> dict:
    """{lado: [(desde, hasta), ...]} con los tramos en que ese lado NO tuvo
    NINGUNA vaca puesta. Es el cambio de mangada: el lado se vació y todavía no
    volvió a llenarse. Lo usan los dos componentes que necesitan distinguir una
    pausa estructural de una demora real."""
    vacios = {}
    for lado in {v.get("lado") for v in visitas if v.get("lado") is not None}:
        tramos = _tramos_ocupados([v for v in visitas if v.get("lado") == lado])
        vacios[lado] = [(a[1], b[0]) for a, b in zip(tramos, tramos[1:]) if b[0] > a[1]]
    return vacios


def _vacio_entre_mangadas(visitas: list, duracion_seg: float) -> dict:
    """Reemplaza a `rutina._ocupacion_rotativa`: el equivalente real de
    "capacidad girando vacía" en una espina es EL LADO PARADO ENTRE MANGADAS.

    Los dos lados ALTERNAN, y eso está medido, no supuesto: el 11/08/2026,
    minuto a minuto, el lado 1 sube a 30 vacas, baja a 0 y se queda en 0
    mientras el lado 2 sube a 30, y así toda la sesión (60 puestos, MPCNo 1-30
    y 31-60). Por eso NO se puede puntuar como una rotativa —donde el puesto
    vacío gira igual— ni tampoco contra los 60 puestos a la vez: la mitad de la
    sala está vacía por diseño todo el tiempo.

    Lo que sí es una pérdida es que un lado tarde MÁS DE LO NORMAL en volver a
    llenarse. Se mide como todos los demás huecos de este motor: contra la
    mediana de la propia sesión (ver `rutina.FACTOR_HUECO`), no contra un
    objetivo inventado. Un cambio de mangada normal no descuenta nada; los que
    se estiran, sí."""
    lados = {v.get("lado") for v in visitas if v.get("lado") is not None}
    if not lados:
        return {"label": "Tiempo vacío entre mangadas", "score": None,
                "info": "Esta sala no registra el lado de cada ordeño, así que no se puede "
                        "separar el lado que trabaja del que está esperando. No se puntúa.",
                "hallazgos": []}

    huecos, vacio_total, ventana_total = [], 0.0, 0.0
    for lado, tramos_vacios in sorted(_vacios_por_lado(visitas).items()):
        ocupados = _tramos_ocupados([v for v in visitas if v.get("lado") == lado])
        if len(ocupados) < 2:
            continue
        # La ventana de un lado va de su primera vaca a la última: si ese lado
        # arranca más tarde o termina antes, eso NO es un hueco entre mangadas
        # (es el principio y el fin de su turno, y ya lo miden otros componentes).
        ventana_total += (ocupados[-1][1] - ocupados[0][0]).total_seconds()
        for desde, hasta in tramos_vacios:
            g = (hasta - desde).total_seconds()
            huecos.append((g, lado, desde, hasta))
            vacio_total += g
    if not huecos:
        return {"label": "Tiempo vacío entre mangadas", "score": 100.0,
                "info": "Ningún lado quedó vacío entre mangadas.", "hallazgos": []}

    largos = [g for g, _, _, _ in huecos]
    mediana = statistics.median(largos)
    umbral = max(mediana * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    exceso = sum(g - mediana for g in largos if g > umbral)
    score = 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso / duracion_seg)
    pct_vacio = 100 * vacio_total / ventana_total if ventana_total else 0

    hallazgos = [{
        "tipo": "vacio", "severidad": g, "puesto": None, "rp": None,
        "texto": f"El lado {lado} quedó {round(g / 60, 1)} min sin ninguna vaca puesta "
                 f"({desde.strftime('%H:%M')}–{hasta.strftime('%H:%M')}), bastante más que el "
                 f"cambio de mangada normal de esta sesión ({round(mediana / 60, 1)} min).",
    } for g, lado, desde, hasta in huecos if g > umbral]

    return {
        "label": "Tiempo vacío entre mangadas", "score": score,
        "info": f"Cada lado pasó {round(pct_vacio)}% de su turno sin ninguna vaca puesta "
                f"(cambio de mangada normal: {round(mediana / 60, 1)} min). Se descuentan solo "
                f"los {round(exceso / 60)} min de más de los cambios que se estiraron.",
        "hallazgos": hallazgos,
    }


def _rotaciones_tandas(visitas: list, duracion_seg: float) -> int | None:
    """Análogo de `rutina._rotaciones_rotativa` para "Rendimiento Sala": en vez
    de vueltas de plataforma, cuenta tandas (lado, bloque) distintas."""
    tandas = {(v["lado"], v["bloque"]) for v in visitas if v.get("lado") is not None}
    return len(tandas) or None


# Qué proporción de los cambios de tanda pueden ser reapariciones antes de dar
# por inservible la numeración. Con tandas sanas esto es 0: cada tanda entra,
# se ordeña y no vuelve. San José daba 25 cambios limpios; La Martina, 112 de
# 143 (78%). El corte en la mitad deja lugar a algún solapamiento puntual entre
# lados sin tragarse un caso como el de La Martina.
FRAGMENTACION_MAXIMA = 0.5


def _fragmentacion_de_tandas(visitas: list) -> float:
    """Qué fracción de los cambios de tanda son tandas que YA habían aparecido.

    Es la prueba de si `BatchNo` sirve para segmentar: en una sala de tandas
    sana, cada tanda ocupa un tramo continuo del tiempo. Si el mismo número va
    y viene, no está identificando un grupo de vacas."""
    cambios, repetidas, vistas, actual = 0, 0, set(), None
    for v in visitas:
        clave = (v.get("lado"), v.get("bloque"))
        if clave == actual:
            continue
        if actual is not None:
            cambios += 1
            if clave in vistas:
                repetidas += 1
        vistas.add(clave)
        actual = clave
    return repetidas / cambios if cambios else 0.0


def _bloques_de_rodeo(visitas: list) -> list:
    """Un número de bloque por visita: sube cada vez que EMPIEZA una corrida
    larga de un rodeo distinto.

    Las corridas cortas (≤ `rutina.UMBRAL_CORRIDA_MEZCLA`) y las vacas sin
    rodeo (el comodín no tiene) NO abren bloque: heredan el de la corrida
    anterior. Es la pieza que faltaba, y sale de un dato medido — el 11/08 en La
    Martina, un bloque de 796 ordeños tenía 109 corridas por rodeo con mediana
    de largo 1 pero máximos de 76 y 122, o sea RODEOS QUE SÍ ENTRAN EN BLOQUE
    con vacas sueltas salpicadas en el medio. Tratando cada suelta como un
    cambio de rodeo, "el hueco entre rodeos" pasaba a ser el hueco entre
    cualquier par de vacas y la métrica no medía nada. Las sueltas ya tienen su
    propio componente: `mezcla_rodeos`."""
    bloques, actual, grupo_actual = [], 0, None
    i = 0
    while i < len(visitas):
        g = visitas[i]["grupo"]
        j = i
        while j < len(visitas) and visitas[j]["grupo"] == g:
            j += 1
        largo = j - i
        if g is not None and largo > rutina.UMBRAL_CORRIDA_MEZCLA and g != grupo_actual:
            actual += 1
            grupo_actual = g
        bloques.extend([actual] * largo)
        i = j
    return bloques


def _huecos_por_rodeo(visitas: list, duracion_seg: float, nombres: dict | None = None) -> dict:
    """Los dos componentes de tiempo muerto, cortando POR RODEO.

    `s3` (entre rodeos) mide las pausas en el cambio de un rodeo al siguiente:
    el corral vacío esperando que traigan la próxima tanda de animales. `s4`
    (manejo de corral) mide las demoras trayendo animales DENTRO del turno de un
    mismo rodeo.

    Cada lado del corte usa SU PROPIA mediana, porque son cosas de escalas muy
    distintas. Y HAY UN TERCER TIPO DE HUECO QUE NO ENTRA EN NINGUNO DE LOS DOS:
    el cambio de mangada. Los huecos dentro de un mismo rodeo son bimodales —
    medido el 11/08 en La Martina: mediana 5s (vaca tras vaca en la misma
    mangada) con una cola de 78 huecos de 240 a 983s (la mangada que se vació y
    todavía no volvió a llenarse)—. Metiendo los dos en la misma bolsa, la
    mediana es la chica, la cola entera queda marcada como anormal y daba
    13.911s "perdidos" en una sesión de 5,4 h: casi cuatro horas de pérdida
    inventadas por la estructura de la sala. Por eso los huecos en que el lado
    quedó VACÍO se sacan de acá — los mide `_vacio_entre_mangadas`, que es su
    componente propio.

    Reemplaza a `_huecos_tandas`, que cortaba por (lado, tanda) y quedó
    inservible: ver `_fragmentacion_de_tandas`."""
    bloques = _bloques_de_rodeo(visitas)
    ocup = _OcupacionLado(visitas)

    def es_demora_real(a, b) -> bool:
        """¿Este hueco es una demora de manejo, o la sala haciendo lo suyo?

        Cuenta SOLO si durante todo el hueco el lado tuvo un puesto libre Y al
        menos una vaca puesta. Las dos condiciones son físicas, no umbrales
        elegidos:

          lado LLENO      no hay dónde poner una vaca, nadie está demorando nada
          lado VACÍO      la mangada se está dando vuelta; eso lo mide
                          `_vacio_entre_mangadas`, su componente propio

        Sin esto, el componente daba 0 en las tres sesiones. Los 20 huecos
        intra-rodeo de más de 60s del 11/08 tenían el lado lleno (15 a 29 de 30
        puestos) o vaciándose (0 a 4): NINGUNO era manejo de corral, y sumaban
        13.911s de pérdida inventada. Con la regla quedan 4.162s y el
        componente pasa a 35 · 64 · 91, que sí distingue una sesión de otra."""
        lado = a.get("lado")
        cap = ocup.capacidad.get(lado)
        if not cap:
            return True          # sin dato de lado no se puede descartar: cuenta
        mn, mx = ocup.rango(lado, a["hora_id"], b["hora_id"])
        return mn is not None and mn > 0 and mx < cap

    inter, intra = [], []
    gaps = []
    for i, (a, b) in enumerate(zip(visitas, visitas[1:])):
        g = (b["hora_id"] - a["hora_id"]).total_seconds()
        cambio = bloques[i] != bloques[i + 1]
        gaps.append((g, cambio, a, b))
        if cambio:
            inter.append(g)
        elif es_demora_real(a, b):
            intra.append(g)
    if not inter:
        return {
            "s3": None, "s4": _score_huecos(intra, duracion_seg) if intra else None,
            "info3": "Esta sesión tuvo un solo rodeo, así que no hay cambio de rodeo que medir.",
            "info4": _info_huecos(intra, duracion_seg, "demoras trayendo animales dentro del "
                                                       "mismo rodeo") if intra else "Sin datos.",
            "hallazgos": [],
        }
    mediana_inter = statistics.median(inter)
    umbral_inter = max(mediana_inter * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    hallazgos = [{
        "tipo": "hueco_grupo", "severidad": g, "puesto": None, "rp": None,
        "texto": f"Hueco de {round(g / 60, 1)} min al cambiar de rodeo "
                 f"({nombres.get(a['grupo'], a['grupo']) if nombres else a['grupo']} → "
                 f"{nombres.get(b['grupo'], b['grupo']) if nombres else b['grupo']}) a las "
                 f"{b['hora_id'].strftime('%H:%M')}, bastante más largo que el resto de los "
                 "cambios de rodeo de esta sesión.",
    } for g, cambio, a, b in gaps if cambio and g > umbral_inter]

    return {
        "s3": _score_huecos(inter, duracion_seg),
        "s4": _score_huecos(intra, duracion_seg) if intra else None,
        "info3": _info_huecos(inter, duracion_seg, "cambios de rodeo anormalmente largos"),
        "info4": (_info_huecos(intra, duracion_seg, "demoras trayendo animales dentro del mismo "
                                                    "rodeo") if intra else "Sin datos."),
        "hallazgos": hallazgos,
    }


def _score_huecos(gaps: list, duracion_seg: float) -> float:
    """Penaliza SOLO el exceso sobre la mediana de los huecos que se pasaron del
    umbral — mismo criterio que `rutina._huecos_rotativa`."""
    mediana = statistics.median(gaps)
    umbral = max(mediana * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    exceso = sum(g - mediana for g in gaps if g > umbral)
    return 100.0 * max(0.0, 1 - rutina.K_PENALIZACION * exceso / duracion_seg)


def _info_huecos(gaps: list, duracion_seg: float, que: str) -> str:
    mediana = statistics.median(gaps)
    umbral = max(mediana * rutina.FACTOR_HUECO, rutina.UMBRAL_HUECO_MIN_S)
    exceso = sum(g - mediana for g in gaps if g > umbral)
    return f"{round(exceso)}s perdidos en {que} (lo normal en esta sesión: {round(mediana)}s)."


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
    lado del corte usa SU PROPIA mediana como referencia.

    PERO ESO EXIGE QUE `BatchNo` MARQUE TANDAS DE VERDAD, y no siempre lo hace.
    En La Martina (10/08/2026, sesión de 731 ordeños) los números de tanda se
    cortan y REAPARECEN 112 veces sobre 143 cambios: las vacas de una tanda no
    quedan juntas en el tiempo, ni ordenando por identificación ni por arranque
    de leche. Con eso, lo que la métrica llama "cambio de tanda" son en su
    mayoría reapariciones del mismo número, la mediana entre tandas cae a 5-7s
    y CUALQUIER pausa real queda marcada como anormal: daba 12.850s "perdidos"
    y entre_grupos=0 en las tres sesiones, o sea acusar al tambo de perder tres
    horas por ordeñe cuando el dato no dice eso.

    Cuando la fragmentación pasa de `FRAGMENTACION_MAXIMA`, los dos componentes
    se devuelven en None y se excluyen del score (mismo mecanismo que
    "ocupación" y "colocación"). Es preferible no medir a publicar un número
    que parece un diagnóstico y no lo es."""
    fragmentacion = _fragmentacion_de_tandas(visitas)
    if fragmentacion > FRAGMENTACION_MAXIMA:
        return {
            "s3": None, "s4": None,
            "info3": (f"No se puede evaluar: los números de tanda de esta sala no agrupan a las "
                      f"vacas de forma contigua ({round(100 * fragmentacion)}% de los cambios son "
                      f"tandas que ya habían aparecido antes), así que no hay forma de separar "
                      f"una pausa real de un cambio de tanda."),
            "info4": "No se puede evaluar por el mismo motivo que la fila de arriba.",
            "hallazgos": [],
        }
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


def _opciones_score(umbral_prep_s):
    """Los argumentos comunes de `analizar_dia`/`resumen_dia`. El objetivo de
    entrada→leche sale del tambo (`umbral_prep_s`, de ⚙ Configuración) y, si no
    lo definió, el componente no se puntúa: ver `UMBRAL_PREP_S`."""
    umbral = umbral_prep_s or UMBRAL_PREP_S
    return {"ocupacion_fn": _vacio_entre_mangadas, "huecos_fn": _huecos_por_rodeo,
            "umbral_prep_s": umbral, "mide_colocacion": umbral is not None,
            "prep_max_s": PREP_MAX_S, "prep_label": PREP_LABEL,
            "sin_prep_info": PREP_SIN_UMBRAL, "incluir_sin_grupo": True}


def analizar_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                 max_sesiones=None, nombres=None, umbral_prep_s=None,
                 identificacion_pct=None) -> dict:
    # `tambo` no hace falta acá — queda en la firma solo para cumplir la
    # interfaz común (ver salas/rotativa.py).
    return rutina.analizar_dia(columns, rows, fecha, grupos, pesos or PESOS, max_sesiones,
                               nombres, identificacion_pct=identificacion_pct,
                               **_opciones_score(umbral_prep_s))


def resumen_dia(tambo: str, columns, rows, fecha: str, grupos=None, pesos=None,
                max_sesiones=None, nombres=None, umbral_prep_s=None,
                identificacion_pct=None):
    return rutina.resumen_dia(columns, rows, fecha, grupos, pesos or PESOS, max_sesiones,
                              nombres, identificacion_pct=identificacion_pct,
                              **_opciones_score(umbral_prep_s))


# LOS CUATRO TRAMOS DE FLUJO DE ALPRO VIENEN ×100, y esto es una trampa cara.
# Medido sobre 30.949 ordeños de La Martina contra 643.474 de La Ponderosa:
#
#     tramo      0-15s   15-30s   30-60s   60-120s   AverageFlow   PeakFlow
#     rotativa    0,85     2,71     2,58      3,79       2,84        4,86
#     Alpro         65      138      141       216       2,17        4,39
#
# `AverageFlow` y `PeakFlow` coinciden en escala; los tramos no. Corriendo los
# umbrales de la rotativa tal cual (bimodalidad con inicio ≥0,2 y arranque
# lento <0,5 kg/min) daba 100% de bimodalidad y 0% de arranque lento en TODOS
# los ordeños: el tambo entero diagnosticado como catástrofe por un factor de
# escala. Se normaliza en la consulta para que aguas abajo todo sea kg/min y
# los umbrales sean los mismos para las dos salas.
ESCALA_FLUJO = 0.01


def sql_flujo_ordenios(desde: str, hasta: str) -> str:
    """Un renglón por ordeño con la curva en cuatro tramos, YA convertida a
    kg/min (ver `ESCALA_FLUJO`). Mismo contrato que en la rotativa."""
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    e = ESCALA_FLUJO
    return f"""
        SELECT b.Number AS rp,
               ex.FlowZerotoFifteen   * {e} AS f0_15,
               ex.FlowFifteentoThirty * {e} AS f15_30,
               ex.FlowThirtyToSixty   * {e} AS f30_60,
               ex.FlowSixtyTo120      * {e} AS f60_120,
               ex.AverageFlow AS f_prom,
               ex.PeakFlow    AS f_pico
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE ex.FlowZerotoFifteen IS NOT NULL
          AND y.BeginTime >= '{desde}'
          AND y.BeginTime < DATEADD(day, 1, '{hasta}')
        OPTION (MAX_GRANT_PERCENT = 25)
    """


# --- Pantalla de Flujos -----------------------------------------------------
# ESTA SALA NO PUBLICA SU UMBRAL DE RETIRADA. `CMSMpcSetting` no existe y no hay
# NINGUNA columna TakeoffLimit/LowFlowLimit en todo el esquema (se buscó en
# sys.columns). El flujo al que se retiró cada pezonera SÍ está
# (`TakeOffFlow`, 31.266 filas, 0 a 4,7 kg/min), pero sin el umbral configurado
# del equipo no se puede clasificar cada retirada en temprana / en objetivo /
# tardía, que es la banda ±25% del informe de DelPro.
#
# Se deja en NULL y la pantalla lo dice, en vez de inventar un umbral: un
# número inventado ahí no es un dato incompleto, es un diagnóstico falso sobre
# el equipo. Es la misma regla que ya está en CLAUDE.md ("NO inventarlos ni
# hacerlos editables"), aplicada al caso que esa regla no contemplaba.
PUBLICA_UMBRAL_RETIRADA = False

# La duración del ordeño no viene como columna (no hay `IsoDuration`): se
# calcula del intervalo, que es exactamente lo que esa columna guarda en la
# rotativa (verificado en su momento: IsoDuration = EndTime - BeginTime).
_DUR_SEG = "DATEDIFF(second, y.BeginTime, y.EndTime)"

# `LowFlowDurationInSec` (segundos de flujo bajo al inicio) NO TIENE
# EQUIVALENTE. Lo más parecido es `LowMilkFlowPercentage`, que es un PORCENTAJE
# del ordeño, no segundos: son medidas distintas y convertir una en otra
# requeriría suponer la duración. Va en NULL — el frontend ya sabe mostrar
# "sin datos" cuando falta una serie.
_COLOC_SEG = "NULL"

_FLUJOS_PROM_CONV = f"""
       AVG(ex.FlowZerotoFifteen   * {ESCALA_FLUJO}) AS f_0_15,
       AVG(ex.FlowFifteentoThirty * {ESCALA_FLUJO}) AS f_15_30,
       AVG(ex.FlowThirtyToSixty   * {ESCALA_FLUJO}) AS f_30_60,
       AVG(ex.FlowSixtyTo120      * {ESCALA_FLUJO}) AS f_60_120,
       AVG(ex.TakeOffFlow) AS f_retirada"""

_BIMODAL_CONV = f"""
       100.0 * SUM(CASE WHEN ex.FlowZerotoFifteen * {ESCALA_FLUJO} >= {rutina.BIMODAL_INICIO_MIN}
                         AND ex.FlowFifteentoThirty < ex.FlowZerotoFifteen
                        THEN 1 ELSE 0 END) / COUNT(*) AS pct_bimodal,
       100.0 * SUM(CASE WHEN ex.FlowZerotoFifteen * {ESCALA_FLUJO} < 0.5
                        THEN 1 ELSE 0 END) / COUNT(*) AS pct_arranque_lento"""


def _rango_conv(desde: str, hasta: str) -> str:
    """El rango va sobre `BeginTime` (arranque de leche), no sobre la
    identificación: acá la ID puede caer minutos antes o incluso después
    (ver MIDE_COLOCACION), así que como eje de tiempo no sirve."""
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    return f"y.BeginTime >= '{desde}' AND y.BeginTime < DATEADD(day, 1, '{hasta}')"


def sql_flujos_por_dia(desde: str, hasta: str, retirada_min=None, retirada_max=None) -> str:
    """Serie diaria. `retirada_min`/`retirada_max` se aceptan para respetar la
    interfaz común pero SE IGNORAN: ver PUBLICA_UMBRAL_RETIRADA."""
    return f"""
        SELECT CAST(y.BeginTime AS date) AS fecha,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM_CONV},
               AVG(ex.AverageFlow) AS f_prom,
               AVG(ex.PeakFlow)    AS f_pico,
               AVG({_DUR_SEG} * 1.0) AS dur_seg,
               {_COLOC_SEG} AS coloc_seg,
               AVG(y.TotalYield) AS litros_bajada,
               NULL AS pct_bajo_min,
               NULL AS pct_sobre_max,
               100.0 * SUM(CASE WHEN ex.ManualMode <> 0 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_manual,
               100.0 * SUM(CASE WHEN ex.ManualDetach = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_retiro_manual,
               100.0 * SUM(CASE WHEN ex.ForcedRetract = 1 THEN 1 ELSE 0 END)
                     / COUNT(*) AS pct_forzada,
               {_BIMODAL_CONV}
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        WHERE {_rango_conv(desde, hasta)} AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY CAST(y.BeginTime AS date)
        ORDER BY fecha
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_por_grupo(desde: str, hasta: str) -> str:
    """Curva promedio por grupo. Igual que en la rotativa, usa el grupo ACTUAL
    del animal: la base no guarda el grupo del día del ordeño.

    No se filtra por `CMSGroupMilkSetting.EnableMilking` (esa tabla no existe
    acá): se toman los grupos que efectivamente aparecen ordeñando, que es el
    mismo criterio que usa `sql_grupos()` de este módulo."""
    return f"""
        SELECT g.Number AS grupo_num, g.Name AS grupo,
               COUNT(*) AS ordenos,
               {_FLUJOS_PROM_CONV}
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal AND b.GCRecord IS NULL
        JOIN AnimalGroup ag ON ag.OID = b.[Group]
        JOIN AbstractGroup g ON g.OID = ag.OID AND g.GCRecord IS NULL
        WHERE {_rango_conv(desde, hasta)} AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY g.Number, g.Name
        ORDER BY g.Number
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_distribucion(desde: str, hasta: str) -> str:
    """Histograma conjunto de flujo promedio y pico, en cajones de 1 kg/min.
    `AverageFlow`/`PeakFlow` YA están en kg/min en esta sala (no se escalan:
    los ×100 son solo los cuatro tramos)."""
    bp = "CASE WHEN ex.AverageFlow >= 9.5 THEN 10 ELSE CAST(ROUND(ex.AverageFlow, 0) AS int) END"
    bk = "CASE WHEN ex.PeakFlow >= 9.5 THEN 10 ELSE CAST(ROUND(ex.PeakFlow, 0) AS int) END"
    return f"""
        SELECT {bp} AS bin_prom, {bk} AS bin_pico, COUNT(*) AS n
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        WHERE {_rango_conv(desde, hasta)}
          AND ex.AverageFlow >= 0 AND ex.PeakFlow >= 0
        GROUP BY {bp}, {bk}
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_por_deo(desde: str, hasta: str) -> str:
    """Bimodalidad y duración por tramo de días en ordeño. Los tramos de DEL
    son los MISMOS que en la rotativa (se reusa `flujos._CASE_DEO`, que ya
    escribe sobre el alias `d`): si cada sala cortara distinto, los dos tambos
    no se podrían comparar."""
    import flujos
    return f"""
        SELECT {flujos._CASE_DEO} AS deo,
               COUNT(*) AS ordenos,
               {_BIMODAL_CONV},
               AVG({_DUR_SEG} * 1.0) AS dur_seg,
               {_COLOC_SEG} AS coloc_seg
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN AnimalDaily d ON d.OID = y.AnimalDaily
        WHERE {_rango_conv(desde, hasta)} AND d.DIM IS NOT NULL
          AND ex.FlowZerotoFifteen IS NOT NULL
        GROUP BY {flujos._CASE_DEO}
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def sql_flujos_tiempo_fuera(desde: str, hasta: str) -> str:
    """Por día: segundos promedio POR VACA entre bajadas del mismo día.

    Mismo cálculo en dos pasos que la rotativa (`flujos.sql_tiempo_fuera`), y
    no un promedio de huecos sueltos: primero se SUMAN los huecos de cada vaca
    en el día y recién después se promedia entre vacas. Son números distintos
    -uno responde "cuánto dura un hueco", el otro "cuánto tiempo pasa afuera
    una vaca en el día"- y este último es el que muestra la pantalla. Los
    alias tienen que ser los que espera `flujos.analizar`.

    Es una ESTIMACIÓN, igual que en la rotativa: la base no tiene sensores de
    entrada/salida al corral, así que el hueco mezcla comida, descanso,
    caminata y espera. Mismas guardas de plausibilidad y mismo criterio de
    descartar el hueco nocturno (solo huecos dentro del mismo día)."""
    import flujos
    return f"""
        WITH visitas AS (
          SELECT y.BasicAnimal, CAST(y.BeginTime AS date) AS fecha,
                 y.BeginTime AS inicio,
                 LAG(y.BeginTime) OVER (
                   PARTITION BY y.BasicAnimal ORDER BY y.BeginTime
                 ) AS inicio_anterior
          FROM SessionMilkYield y
          WHERE {_rango_conv(desde, hasta)}
        ),
        huecos AS (
          SELECT BasicAnimal, fecha,
                 DATEDIFF(second, inicio_anterior, inicio) AS gap_seg
          FROM visitas
          WHERE inicio_anterior IS NOT NULL
            AND CAST(inicio_anterior AS date) = fecha
            AND DATEDIFF(second, inicio_anterior, inicio)
                BETWEEN {flujos.GAP_MIN_SEG} AND {flujos.GAP_MAX_SEG}
        ),
        por_vaca_dia AS (
          SELECT BasicAnimal, fecha, SUM(gap_seg) AS seg_fuera
          FROM huecos GROUP BY BasicAnimal, fecha
        )
        SELECT fecha, AVG(seg_fuera * 1.0) AS seg_fuera_prom, COUNT(*) AS vacas_con_dato
        FROM por_vaca_dia
        GROUP BY fecha
        HAVING COUNT(*) >= {flujos.VACAS_FUERA_MIN}
        ORDER BY fecha
        OPTION (MAX_GRANT_PERCENT = 20)
    """

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
               NULL AS rotacion, NULL AS turno,
               ex.SideNo AS lado, ex.BatchNo AS bloque
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE COALESCE(ex.IdTimestamp, y.BeginTime) >= DATEADD(hour, -6, '{desde}')
          AND COALESCE(ex.IdTimestamp, y.BeginTime) < DATEADD(hour, 6, DATEADD(day, 1, '{hasta}'))
        ORDER BY COALESCE(ex.IdTimestamp, y.BeginTime)
        OPTION (MAX_GRANT_PERCENT = 25)
    """


def sql_identificacion(desde: str, hasta: str) -> str:
    """Ordeños que la sala no logró atribuir a una vaca, por día.

    YA SE PUDO MEDIR, y el criterio terminó siendo EL MISMO que el de la
    rotativa: el comodín es `BasicAnimal.Number = 0`. Verificado en La Martina
    del 05 al 11/08/2026: es UN solo animal (un `BasicAnimal` distinto) y se
    lleva 2.728 de 15.665 ordeños, el 17,4%.

    Las dos formas de quedar sin dueño se separan igual que en el reporte de
    DelPro (ver `rutina.sql_identificacion`), y acá también dan distinto:

        sin_lectura   2.677  nunca se leyó el collar (no hay IdTimestamp)
        desconocido      51  se leyó algo, pero no es de ninguna vaca del rodeo

    Y hay 62 ordeños con RP real y sin sello de hora: esos SÍ están
    identificados, solo les falta el momento, así que no cuentan como perdidos.
    """
    desde, hasta = rutina.validar_fecha(desde), rutina.validar_fecha(hasta)
    return f"""
        SELECT CAST(y.BeginTime AS date) AS fecha,
               COUNT(*) AS visitas,
               SUM(CASE WHEN b.Number = 0 THEN 1 ELSE 0 END) AS sin_duenio,
               SUM(CASE WHEN b.Number = 0 THEN y.TotalYield ELSE 0 END) AS kg_sin_duenio,
               SUM(CASE WHEN b.Number = 0 AND ex.IdTimestamp IS NULL
                        THEN 1 ELSE 0 END) AS sin_lectura,
               SUM(CASE WHEN b.Number = 0 AND ex.IdTimestamp IS NOT NULL
                        THEN 1 ELSE 0 END) AS desconocido
        FROM SessionMilkYield y
        JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
        JOIN BasicAnimal b ON b.OID = y.BasicAnimal
        WHERE y.BeginTime >= '{desde}' AND y.BeginTime < DATEADD(day, 1, '{hasta}')
        GROUP BY CAST(y.BeginTime AS date)
        ORDER BY fecha
        OPTION (MAX_GRANT_PERCENT = 20)
    """


def armar_identificacion(columns, rows) -> list:
    """Filas de `sql_identificacion` (de ESTA sala) -> mismo shape que
    `rutina.armar_identificacion` (`ordenos`/`desconocidos`/`kg_desconocidos`/
    `pct_identificacion`), para que `/api/rutina/rendimiento` no tenga que
    saber qué sala está mirando — MÁS los dos motivos separados
    (`sin_lectura`/`desconocido_transponder`) que la consulta de esta sala sí
    puede distinguir y la de la rotativa no."""
    idx = {c: i for i, c in enumerate(columns)}
    salida = []
    for r in rows:
        ordenos = int(r[idx["visitas"]] or 0)
        desc = int(r[idx["sin_duenio"]] or 0)
        salida.append({
            "fecha": str(r[idx["fecha"]])[:10],
            "ordenos": ordenos,
            "desconocidos": desc,
            "kg_desconocidos": round(float(r[idx["kg_sin_duenio"]] or 0), 1),
            "pct_identificacion": round(100.0 * (ordenos - desc) / ordenos, 2) if ordenos else None,
            "sin_lectura": int(r[idx["sin_lectura"]] or 0),
            "desconocido_transponder": int(r[idx["desconocido"]] or 0),
        })
    return salida


def analizar_rendimiento(tambo: str, columns, rows, desde: str, hasta: str, max_sesiones=None,
                         nombres=None, grupos_ordene=None) -> list:
    return rutina.analizar_rendimiento(columns, rows, desde, hasta, max_sesiones,
                                       rotaciones_fn=_rotaciones_tandas, nombres=nombres,
                                       grupos_ordene=grupos_ordene)


def resumen_grupos_dia(tambo: str, columns, rows, fecha: str, grupos_ordene=None, nombres=None) -> dict:
    return rutina.resumen_grupos_dia(columns, rows, fecha, grupos_ordene=grupos_ordene, nombres=nombres,
                                     ocupacion_fn=_vacio_entre_mangadas)
