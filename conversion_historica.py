# -*- coding: utf-8 -*-
"""Cómo varía en el tiempo la eficiencia de conversión del rodeo.

La pestaña "Eficiencia de conversión" contesta *cuánto* convierte hoy cada
rodeo. Esta contesta *si está mejorando o empeorando*, que es lo que permite
saber si un cambio de ración sirvió.

DOS INDICADORES, A PROPÓSITO
----------------------------
No es lo mismo lo que se puede medir seguido que lo que importa
económicamente, así que se muestran los dos por separado en vez de mezclarlos:

  1. LITROS POR KG DE MATERIA SECA — semanal, todo medido.
     La leche es diaria y la comida se descarga varias veces por día, así que
     esto no interpola nada. Da respuesta rápida: se cambia la ración un lunes
     y a la semana siguiente se ve si convirtió mejor.

  2. SÓLIDOS POR KG DE MATERIA SECA — mensual, anclado al control lechero.
     Es el que importa: al tambo le pagan por kilos de grasa y proteína, no
     por litros. Pero la grasa y la proteína se miden UNA VEZ POR MES, el día
     del control (verificado: un solo día por mes, ~1.100-1.400 vacas).

NO se calcula una conversión de sólidos semanal o diaria arrastrando el último
control. Sería inventar variación: lo único que se movería es la leche,
multiplicada por una composición vieja. Un gráfico que se mueve todos los días
sin significar nada es peor que no tenerlo.

POR QUÉ SEMANAL Y NO DIARIO
---------------------------
La comida llega por descargas de mixer. Un día suelto puede tener dos descargas
o ninguna según cuándo cargaron: eso es ruido del registro, no del rodeo. La
semana lo promedia sin perder capacidad de reacción.

EL GRUPO TIENE QUE SER EL DEL DÍA
---------------------------------
`AnimalDaily.AnimalGroup` guarda en qué grupo estaba la vaca ESE día (poblado
en 245.467 de 245.670 filas). Hay que usar ese y no el grupo actual: 2.467
vacas cambiaron de rodeo en 2026, así que atribuirles la comida del rodeo donde
están hoy sería falso para casi todo el rodeo. `sql_produccion_grupo_dia` de
`alimentacion.py` ya lo hace bien.

HASTA DÓNDE LLEGA
-----------------
Lo limita Haasten, que arranca en abril de 2026: unas 17 semanas. La leche de
DelPro tiene más historia (desde diciembre) pero sin la comida no sirve. El
indicador mensual de sólidos tiene apenas 4 puntos — se muestra, pero como
"los últimos cuatro valores", no como una tendencia.
"""
import datetime

import alimentacion
import rebano

# Arranque de los datos de alimentación. Antes de esta fecha no hay con qué
# cruzar la leche.
INICIO_ALIMENTACION = "2026-04-01"

RANGO_MAX_DIAS = 400

# Una semana necesita un mínimo de días con datos de los DOS lados para que el
# número signifique algo. Con menos se muestra la barra en gris.
DIAS_MIN_SEMANA = 4

# Y un mínimo de rodeo cubierto. Los días-lote sin descarga cargada se caen del
# cálculo y se llevan sus vacas: si se cae un rodeo entero, la conversión que
# queda es la de los otros, no la del tambo.
COBERTURA_MIN = 85.0

# Un lote-semana con menos vacas que esto no es un rodeo: son las que quedaron
# sueltas en un grupo que se está armando o vaciando. Sin este piso el informe
# denunciaba "Rodeo 5 sin descargas, 13 semanas" cuando en DelPro ese grupo
# tenía SEIS vacas-día por semana hasta fines de junio y recién se pobló en la
# semana 27 — que es exactamente cuando Haasten le empieza a descargar. No
# faltaba comida: no había rodeo.
VACAS_DIA_MIN_LOTE = 50


def sql_solidos_por_control(desde, hasta, herd=None) -> str:
    """Grasa y proteína promedio de cada fecha de control lechero.

    El control es mensual y se hace en un solo día, así que cada fila de esto
    es un punto real medido — no hay interpolación en el medio.
    """
    return f"""
        SELECT CAST(h.DateAndTime AS date) AS fecha,
               COUNT(*) AS vacas,
               AVG(mt.Fat) AS grasa,
               AVG(mt.Protein) AS proteina
        FROM MilkTest mt
        JOIN AnimalHistoricalData h ON h.OID = mt.OID
        JOIN BasicAnimal b ON b.OID = h.BasicAnimal
        WHERE mt.Fat > 0 AND mt.Protein > 0
          AND CAST(h.DateAndTime AS date) BETWEEN '{desde}' AND '{hasta}'
          AND b.GCRecord IS NULL AND b.Number > 0
          AND {rebano.filtro("b", herd)}
        GROUP BY CAST(h.DateAndTime AS date)
        HAVING COUNT(*) >= 50          -- un puñado de controles sueltos no es un control
        ORDER BY fecha
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def sql_grupos_ordene(grupos_sql: str, herd=None) -> str:
    """Los OID de grupo que DelPro tiene marcados como de ordeñe.

    Hace falta filtrar por acá y no alcanza con "el grupo tuvo leche": los
    lotes de preparto tienen alguna vaca recién parida que todavía no movieron,
    así que aparecen con leche, pero el mixer les descarga la ración de TODO el
    lote —secas incluidas—. Esa MS entera se dividía entre ese puñado de vacas
    y daba 236 kg de materia seca por vaca, además de inflar el total del
    tambo. Un lote que come y no produce no puede entrar en una conversión.

    `grupos_sql`: de `salas.grupos_subquery(tambo)` — qué [Group] son de
    ordeñe real para ESTE tipo de sala. NUNCA se hardcodea CMSGroupMilkSetting
    acá: esa tabla es propia del controlador de una rotativa (ver salud.py,
    mismo patrón).
    """
    cond = rebano.condicion_herd("ag", herd)
    return f"""
        SELECT gr.grupo AS grupo
        FROM ({grupos_sql}) gr
        JOIN AnimalGroup ag ON ag.OID = gr.grupo
        WHERE 1 = 1
          {f"AND {cond}" if cond else ""}
    """


BUCKETS_LACT = ["L1", "L2", "L3+"]

# `LactationNumber` 0 = novilla que nunca parió (ver `resumen.py`), y no
# debería aparecer en una fila de ordeño con leche. Aparece, pero apenas: 39
# filas de 128.000 y 17 vacas en cuatro meses — un lag de carga en el momento
# del parto, no una categoría real. Se suma a L1 en vez de abrir un bucket
# aparte para eso.
_BUCKET_LACT_SQL = ("CASE WHEN ad.LactationNumber <= 1 THEN 'L1' "
                    "WHEN ad.LactationNumber = 2 THEN 'L2' ELSE 'L3+' END")


def sql_produccion_por_lactancia(grupos_sql: str, desde, hasta, herd=None) -> str:
    """Vacas en ordeñe y leche por día, agrupadas por lactancia (L1/L2/L3+).

    Para separar si un cambio en litros por vaca es el rodeo mejorando o
    empeorando, o es que cambió la MEZCLA de lactancias que se está ordeñando
    (entran vaquillonas de primera cría, que producen menos que una vaca de
    2da o 3ra). Se agrupa server-side por bucket, no por vaca: por vaca
    multiplicaría las filas por 6-8 sin necesidad, esto ya alcanza para
    reconstruir vacas-día y litros por semana y por bucket.

    `grupos_sql`: ver la nota de `sql_grupos_ordene`.
    """
    cond = rebano.condicion_herd("ag", herd)
    return f"""
        SELECT CAST(ad.Date AS date) AS fecha, {_BUCKET_LACT_SQL} AS bucket,
               COUNT(DISTINCT ad.BasicAnimal) AS vacas, SUM(ad.TotalYield) AS kg_leche
        FROM AnimalDaily ad
        JOIN AnimalGroup ag ON ag.OID = ad.AnimalGroup
        JOIN ({grupos_sql}) gr ON gr.grupo = ad.AnimalGroup
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date BETWEEN '{desde}' AND '{hasta}'
          {f"AND {cond}" if cond else ""}
        GROUP BY CAST(ad.Date AS date), {_BUCKET_LACT_SQL}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def _fecha(v) -> datetime.date:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def semana_de(d: datetime.date) -> tuple:
    """(clave, lunes) de la semana ISO a la que pertenece la fecha."""
    lunes = d - datetime.timedelta(days=d.weekday())
    a, s, _ = d.isocalendar()
    return f"{a}-S{s:02d}", lunes


def _corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = (sum((x - ma) ** 2 for x in a) / n) ** 0.5
    sb = (sum((x - mb) ** 2 for x in b) / n) ** 0.5
    if not sa or not sb:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n / (sa * sb), 2)


def diagnosticar(serie) -> dict:
    """¿La serie mide el rodeo o mide cómo se cargaron las descargas?

    Es la pregunta que hay que contestar ANTES de leer la tendencia, porque en
    este tambo la respuesta hoy es "el registro". Medido sobre 13 semanas
    completas: la conversión correlaciona **-0,76 con los kg de MS por vaca** y
    apenas **+0,13 con los litros por vaca**. O sea que cuando el gráfico sube
    no es que las vacas rindieran más — es que se cargó menos comida.

    Se ve también en la amplitud: la leche por vaca se mueve parejo con la
    estación (36,9 a 44,7 en cuatro meses, +19%), mientras la MS por vaca salta
    de 20,1 a 26,8 (+28%) sin ninguna razón biológica que lo explique. Una vaca
    no cambia un cuarto de lo que come de una semana a la otra.

    Mientras esto siga así, la tendencia semanal NO se puede leer como mejora o
    empeora del rodeo. La pantalla lo dice, en vez de dibujar una flecha verde.
    """
    c = [s for s in serie if not s["parcial"] and s["conversion"]]
    if len(c) < 4:
        return {"semanas": len(c), "confiable": False,
                "motivo": "hacen falta al menos 4 semanas completas"}
    conv = [s["conversion"] for s in c]
    r_ms = _corr(conv, [s["ms_vaca"] for s in c])
    r_leche = _corr(conv, [s["litros_vaca"] for s in c])
    # Si la conversión sigue a la MS más que a la leche, lo que se está
    # graficando es la irregularidad de la carga.
    manda_registro = r_ms is not None and abs(r_ms) > abs(r_leche or 0) and r_ms < -0.5
    return {
        "semanas": len(c),
        "corr_ms": r_ms,
        "corr_leche": r_leche,
        "confiable": not manda_registro,
        "motivo": ("la conversión sigue a los kg de materia seca cargados "
                   "(r=%s) y no a la leche producida (r=%s): lo que varía es "
                   "el registro de descargas, no el rodeo" % (r_ms, r_leche))
        if manda_registro else None,
    }


def _filas(data) -> list:
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{c: f[i] for c, i in idx.items()} for f in data["rows"]]


def _bloque_lactancia(semanas: list, claves: list) -> tuple:
    """Suma vacas-día y kg de leche de varias semanas, y devuelve (share, litros_vaca)
    por bucket sobre ese bloque. `semanas` es la lista completa de semanas ya
    armadas (con "buckets" adentro); `claves` son las que entran al bloque."""
    por_semana = {s["semana"]: s for s in semanas}
    tot_v = {b: 0 for b in BUCKETS_LACT}
    tot_kg = {b: 0.0 for b in BUCKETS_LACT}
    for k in claves:
        s = por_semana[k]
        for b in BUCKETS_LACT:
            tot_v[b] += s["buckets"][b]["vacas"]
            tot_kg[b] += s["buckets"][b]["kg_leche"]
    total_v = sum(tot_v.values())
    share = {b: (tot_v[b] / total_v if total_v else 0.0) for b in BUCKETS_LACT}
    litros_vaca = {b: (tot_kg[b] / tot_v[b] if tot_v[b] else 0.0) for b in BUCKETS_LACT}
    return share, litros_vaca


def _descomponer(claves_a: list, claves_b: list, semanas: list, etiqueta: str) -> dict:
    """¿El litros/vaca del rodeo cambió porque las vacas produjeron distinto, o
    porque cambió la MEZCLA de lactancias que se está ordeñando?

    Descomposición estándar de dos factores (a pesos de Laspeyres): el cambio
    total se separa en cuánto es "mismo bucket, distinta mezcla de acá en más"
    (efecto MEZCLA, a productividad del bloque B) más "misma mezcla de acá
    para atrás, distinta productividad" (efecto PRODUCTIVIDAD, a mezcla del
    bloque A). Los dos suman el cambio total, sin residuo.

    Por qué hace falta: una vaquillona de primera cría da menos leche que una
    de 2da o 3ra lactancia SIN que eso sea un problema de ración. Si en el
    período de comparación entraron muchas de primera, el promedio del rodeo
    puede caer o amesetarse aunque cada vaca, dentro de su categoría, esté
    dando igual o más que antes. Sin esta cuenta, esa caída se lee como que
    "algo empeoró", cuando en realidad cambió quién está en el tambo.
    """
    share_a, lv_a = _bloque_lactancia(semanas, claves_a)
    share_b, lv_b = _bloque_lactancia(semanas, claves_b)
    total_a = sum(share_a[b] * lv_a[b] for b in BUCKETS_LACT)
    total_b = sum(share_b[b] * lv_b[b] for b in BUCKETS_LACT)
    efecto_mezcla = sum(lv_b[b] * (share_b[b] - share_a[b]) for b in BUCKETS_LACT)
    efecto_productividad = sum(share_a[b] * (lv_b[b] - lv_a[b]) for b in BUCKETS_LACT)
    return {
        "etiqueta": etiqueta,
        "semanas_a": claves_a, "semanas_b": claves_b,
        "litros_vaca_a": round(total_a, 2), "litros_vaca_b": round(total_b, 2),
        "delta_total": round(total_b - total_a, 2),
        "efecto_mezcla": round(efecto_mezcla, 2),
        "efecto_productividad": round(efecto_productividad, 2),
        "detalle": {b: {
            "share_a": round(share_a[b] * 100, 1), "share_b": round(share_b[b] * 100, 1),
            "litros_vaca_a": round(lv_a[b], 1), "litros_vaca_b": round(lv_b[b], 1),
        } for b in BUCKETS_LACT},
    }


def lactancia(filas_lactancia, hoy: datetime.date) -> dict:
    """Vacas y litros por vaca, semana a semana, abiertos por lactancia.

    Es independiente de la materia seca: sale entero de `AnimalDaily`, así que
    no depende de que Haasten tenga las descargas cargadas. Por eso una semana
    puede estar completa acá y parcial en `armar()`, o al revés.
    """
    por_semana_dia = {}
    for f in _filas(filas_lactancia):
        d = _fecha(f["fecha"])
        clave, lunes = semana_de(d)
        s = por_semana_dia.setdefault(clave, {
            "semana": clave, "desde": lunes.isoformat(),
            "hasta": (lunes + datetime.timedelta(days=6)).isoformat(),
            "dias": set(),
            "buckets": {b: {"vacas": 0, "kg_leche": 0.0} for b in BUCKETS_LACT},
        })
        s["dias"].add(d.isoformat())
        b = f["bucket"]
        s["buckets"][b]["vacas"] += int(f["vacas"] or 0)
        s["buckets"][b]["kg_leche"] += float(f["kg_leche"] or 0)

    def cerrar(s):
        dias = len(s["dias"])
        total_v = sum(s["buckets"][b]["vacas"] for b in BUCKETS_LACT)
        pct_l1 = round(100 * s["buckets"]["L1"]["vacas"] / total_v, 1) if total_v else None
        return {
            "semana": s["semana"], "desde": s["desde"], "hasta": s["hasta"], "dias": dias,
            "parcial": dias < DIAS_MIN_SEMANA,
            "vacas_total": total_v, "pct_l1": pct_l1,
            "buckets": {b: {
                "vacas": s["buckets"][b]["vacas"],
                "kg_leche": round(s["buckets"][b]["kg_leche"]),
                "litros_vaca": (round(s["buckets"][b]["kg_leche"] / s["buckets"][b]["vacas"], 1)
                                if s["buckets"][b]["vacas"] else None),
            } for b in BUCKETS_LACT},
        }

    semanas = [cerrar(s) for s in sorted(por_semana_dia.values(), key=lambda x: x["semana"])]
    completas = [s["semana"] for s in semanas if not s["parcial"]]

    descomposiciones = []
    # Rango completo: primeras 4 semanas completas contra las últimas 4. Mismo
    # criterio que `tendencia` en `armar()`, para que hablen de las mismas
    # semanas si alguien compara las dos pestañas.
    if len(completas) >= 8:
        descomposiciones.append(_descomponer(
            completas[:4], completas[-4:], semanas, "todo el rango medido"))

    # Pico contra la actualidad: la ventana de 4 semanas con mayor litros/vaca
    # del rodeo ANTES de las últimas 4, contra esas últimas 4. Es la pregunta
    # que importa cuando el gráfico muestra un amesetamiento o caída sobre el
    # final: el promedio de "primeras contra últimas" puede taparla si el
    # rodeo venía de un arranque lento en abril, como es el caso acá. Se busca
    # solo ANTES del bloque final para que las dos mitades no compartan
    # semanas — si no, "antes/después" comparte datos con "después".
    ultimas = completas[-4:]
    anteriores = completas[:-4]
    if len(anteriores) >= 4:
        ventanas = [anteriores[i:i + 4] for i in range(len(anteriores) - 3)]

        def litros_vaca_bloque(claves):
            share, lv = _bloque_lactancia(semanas, claves)
            return sum(share[b] * lv[b] for b in BUCKETS_LACT)

        pico = max(ventanas, key=litros_vaca_bloque)
        descomposiciones.append(_descomponer(pico, ultimas, semanas, "desde el pico"))

    return {"semanas": semanas, "descomposiciones": descomposiciones}


def armar(prod_dia, ms_lote_dia: dict, lote_de_grupo: dict,
          solidos_control, hoy: datetime.date, grupos_ordene=None,
          costo_lote_dia: dict = None, precio_litro: float = None) -> dict:
    """Cruza producción diaria con materia seca diaria y arma las dos series.

    `ms_lote_dia`: {(lote, fecha): kg_ms_totales} — sale de
    `alimentacion.ms_por_lote_dia()`.
    `lote_de_grupo`: {grupo_oid: lote} — el mapeo de `conciliacion.py`.
    `grupos_ordene`: OIDs de grupos de ordeñe (ver `sql_grupos_ordene`).
    `costo_lote_dia`: {(lote, fecha): $ de alimento} — de
        `alimentacion.costo_por_lote_dia()`. None = sin planilla de precios; la
        serie física sale igual y la de litros libres queda vacía.
    `precio_litro`: $ del litro de leche, para pasar el costo a litros.

    LOS LITROS LIBRES SIGUEN EL MISMO CAMINO QUE LA MATERIA SECA, semana por
    semana y con los mismos descartes: un lote-semana con descargas incompletas
    queda afuera acá también. Si se lo dejara entrar, su costo bajo (menos comida
    anotada es menos plata) dibujaría una MEJORA del margen justo en las semanas
    peor cargadas — al revés de la realidad.
    """
    costo_lote_dia = costo_lote_dia or {}
    # Un lote entra a la conversión solo si TODOS sus grupos son de ordeñe. Si
    # comparte lote con un grupo que no ordeña, su comida no es atribuible a
    # leche y el lote entero queda afuera.
    lotes_ok = None
    if grupos_ordene is not None:
        ordene = {int(g) for g in grupos_ordene}
        fuera = {l for g, l in lote_de_grupo.items() if int(g) not in ordene}
        lotes_ok = {l for l in lote_de_grupo.values() if l not in fuera}

    # --- Diario: se junta la leche con la comida del MISMO lote y día -------
    diario = {}
    sin_lote = set()
    no_ordene = set()
    for f in _filas(prod_dia):
        d = _fecha(f["fecha"])
        lote = lote_de_grupo.get(f["grupo"])
        if lote is None:
            sin_lote.add(f.get("nombre") or f["grupo"])
            continue
        if lotes_ok is not None and lote not in lotes_ok:
            no_ordene.add(f.get("nombre") or f["grupo"])
            continue
        clave = (lote, d.isoformat())
        acum = diario.setdefault(clave, {"kg_leche": 0.0, "vacas": 0, "kg_ms": 0.0,
                                          "costo": 0.0, "dias_costo": 0})
        acum["kg_leche"] += float(f["kg_leche"] or 0)
        acum["vacas"] += int(f["vacas"] or 0)

    # La comida se suma SOLO sobre días que ya tienen producción cargada, y por
    # eso el `if clave in diario` no es una optimización: es lo que sostiene el
    # número. Los dos sistemas no van al día parejo — `AnimalDaily` queda
    # completo recién unos cinco días atrás y Haasten tiene lo de hoy. Sumando
    # toda la MS de la última semana contra la leche de los dos días que sí
    # cargaron, la última barra daba 79 kg de MS por vaca en vez de 20.
    #
    # (`ms_por_lote_dia` devuelve la fecha como objeto `date` y acá las claves
    # son texto ISO: sin normalizar no coincide ninguna y la serie sale vacía.)
    for (lote, dia), kg in ms_lote_dia.items():
        clave = ((lote or "").strip(), _fecha(dia).isoformat())
        if clave in diario:
            diario[clave]["kg_ms"] += float(kg or 0)
    # El costo, por el mismo camino y con el mismo `if clave in diario`: sumar
    # plata de días sin producción cargada infla el costo por litro igual que
    # infla la materia seca.
    for (lote, dia), plata in costo_lote_dia.items():
        clave = ((lote or "").strip(), _fecha(dia).isoformat())
        if clave in diario:
            diario[clave]["costo"] += float(plata or 0)
            diario[clave]["dias_costo"] += 1

    # --- Lote × semana, que es la unidad más chica CONFIABLE -----------------
    # El día suelto no lo es: el mixer puede descargar dos veces un día y
    # ninguna al siguiente, y eso es ruido del registro, no del rodeo. Filtrar
    # por día lo que hace es tirar días buenos —la cobertura se caía del 99% al
    # 65%— sin arreglar nada. La semana ya promedia esa irregularidad.
    ls_acum = {}
    for (lote, dia), v in diario.items():
        clave, lunes = semana_de(datetime.date.fromisoformat(dia))
        ls = ls_acum.setdefault((lote, clave), {
            "lunes": lunes, "kg_leche": 0.0, "kg_ms": 0.0,
            "vacas_dia": 0, "dias": set(), "costo": 0.0,
            "leche_costo": 0.0, "vacas_costo": 0})
        ls["kg_leche"] += v["kg_leche"]
        ls["kg_ms"] += v["kg_ms"]
        ls["vacas_dia"] += int(v["vacas"] or 0)
        ls["dias"].add(dia)
        # La leche y las vacas de los días VALORIZADOS se acumulan aparte: el
        # costo por litro tiene que dividir por la leche de esos mismos días, no
        # por la de la semana entera (si se valorizaron 4 de 7 días, dividir por
        # los 7 da un costo por litro casi la mitad del real).
        if v["dias_costo"]:
            ls["costo"] += v["costo"]
            ls["leche_costo"] += v["kg_leche"]
            ls["vacas_costo"] += int(v["vacas"] or 0)

    # --- Semanal ------------------------------------------------------------
    semanas = {}
    por_lote = {}
    descartes = {}
    for (lote, clave), ls in ls_acum.items():
        lunes = ls["lunes"]
        s = semanas.setdefault(clave, {
            "semana": clave, "desde": lunes.isoformat(),
            "hasta": (lunes + datetime.timedelta(days=6)).isoformat(),
            "kg_leche": 0.0, "kg_ms": 0.0, "vacas_dia": 0,
            "vacas_dia_total": 0, "dias": set(),
            "costo": 0.0, "leche_costo": 0.0, "vacas_costo": 0,
            "vacas_base_costo": 0})
        # Las vacas se cuentan SIEMPRE, con comida cargada o sin ella: es el
        # denominador de la cobertura.
        s["vacas_dia_total"] += ls["vacas_dia"]

        # Un lote-semana sin comida cargada no puede entrar: bajaría la MS y la
        # conversión saldría inflada. Descartarlo también se lleva sus vacas,
        # así que la semana queda calculada sobre una parte del rodeo — por eso
        # se informa qué parte. Y no alcanza con exigir kg_ms > 0: "Enfermeria"
        # registra las descargas incompletas y "Rodeo 4" dejó de recibirlas, y
        # los dos pasarían ese filtro metiendo media ración. Va la misma banda
        # de plausibilidad que usa `alimentacion.py`.
        vacas = ls["vacas_dia"]

        def anotar(motivo):
            # Solo se reporta si el lote tenía tamaño de rodeo esa semana.
            if vacas < VACAS_DIA_MIN_LOTE:
                return
            d = descartes.setdefault(lote, {"semanas": 0, "motivo": motivo})
            d["semanas"] += 1
            d["motivo"] = motivo

        if ls["kg_ms"] <= 0 or ls["kg_leche"] <= 0 or not vacas:
            anotar("sin descargas registradas")
            continue
        ms_vaca = ls["kg_ms"] / vacas
        if not (alimentacion.MS_MIN_PLAUSIBLE <= ms_vaca <= alimentacion.MS_MAX_PLAUSIBLE):
            anotar(f"{ms_vaca:.1f} kg de MS por vaca, fuera de "
                   f"{alimentacion.MS_MIN_PLAUSIBLE:.0f}-{alimentacion.MS_MAX_PLAUSIBLE:.0f}")
            continue

        s["kg_leche"] += ls["kg_leche"]
        s["kg_ms"] += ls["kg_ms"]
        s["vacas_dia"] += vacas
        s["dias"] |= ls["dias"]
        # Solo llega acá un lote-semana que pasó los descartes de arriba, así que
        # la plata hereda las mismas garantías que la materia seca.
        s["costo"] += ls["costo"]
        s["leche_costo"] += ls["leche_costo"]
        s["vacas_costo"] += ls["vacas_costo"]
        # Denominador de la COBERTURA DEL COSTO: las vacas que sí tienen materia
        # seca, valorizadas o no. Hace falta porque las dos coberturas son
        # distintas y la del costo puede ser mucho peor sin que se note. Medido
        # el 30/07/2026: dos recetas quedaban sin precio y con eso el costo de la
        # semana 24 salía de 4.646 $/vaca contra 9.000 en las semanas completas
        # —los rodeos 2, 3 y 4 no entraban— y el gráfico dibujaba una mejora del
        # margen que nunca pasó. Aritméticamente el número cerraba: lo que
        # cambiaba era QUÉ RODEOS estaba midiendo cada semana.
        s["vacas_base_costo"] += vacas

        por_lote.setdefault(lote, {})[clave] = {
            "kg_leche": ls["kg_leche"], "kg_ms": ls["kg_ms"], "vacas_dia": vacas,
            "costo": ls["costo"], "leche_costo": ls["leche_costo"],
            "vacas_costo": ls["vacas_costo"], "vacas_base_costo": vacas}

    def _economia(s):
        """Costo y litros libres de una semana. Todo None si no se valorizó nada:
        un cero acá se leería como "comieron gratis".

        SE DEVUELVE LA COBERTURA DEL COSTO, que no es la misma que la de materia
        seca. Una semana en la que solo se pudo valorizar la mitad de las vacas
        da un costo por vaca perfectamente creíble —la aritmética cierra— pero
        está midiendo otros rodeos que la semana de al lado, y la comparación
        entre semanas es justamente para lo que sirve esta pantalla.
        """
        vc, lc, plata = s.get("vacas_costo") or 0, s.get("leche_costo") or 0, s.get("costo") or 0
        base = s.get("vacas_base_costo") or 0
        vacio = {"costo_vaca_dia": None, "costo_por_litro": None,
                 "litros_libres": None, "pct_litros_libres": None,
                 "cobertura_costo": None, "parcial_costo": None}
        if not vc or not plata:
            return vacio
        cob = round(100 * vc / base, 1) if base else None
        costo_vaca = plata / vc
        litros_vaca = lc / vc if vc else None
        por_litro = plata / lc if lc else None
        libres = (litros_vaca - costo_vaca / precio_litro
                  if litros_vaca is not None and precio_litro else None)
        return {
            "costo_vaca_dia": round(costo_vaca, 2),
            "costo_por_litro": round(por_litro, 2) if por_litro is not None else None,
            "litros_libres": round(libres, 1) if libres is not None else None,
            "pct_litros_libres": (round(100 * libres / litros_vaca, 1)
                                  if libres is not None and litros_vaca else None),
            "cobertura_costo": cob,
            "parcial_costo": (cob or 0) < COBERTURA_MIN,
        }

    def cerrar(s):
        dias = len(s["dias"]) if isinstance(s.get("dias"), set) else s.get("dias", 0)
        vacas = s["vacas_dia"]
        total = s["vacas_dia_total"]
        cobertura = round(100 * vacas / total, 1) if total else None
        return {
            "semana": s["semana"], "desde": s["desde"], "hasta": s["hasta"],
            "kg_leche": round(s["kg_leche"]),
            "kg_ms": round(s["kg_ms"]),
            "vacas_prom": round(vacas / dias) if dias else None,
            "litros_vaca": round(s["kg_leche"] / vacas, 1) if vacas else None,
            "ms_vaca": round(s["kg_ms"] / vacas, 1) if vacas else None,
            "conversion": round(s["kg_leche"] / s["kg_ms"], 3) if s["kg_ms"] else None,
            **_economia(s),
            "dias": dias,
            # Qué parte del rodeo en ordeñe entró en el cálculo. Sin esto, una
            # semana a la que le faltan descargas parece igual de sólida que
            # una completa, y no lo es.
            "cobertura": cobertura,
            # Una semana a la que le falta una parte grande del rodeo se
            # muestra, pero avisada: el número es de los lotes que quedaron.
            "parcial": (cobertura or 0) < COBERTURA_MIN,
        }

    # Las semanas cortadas al medio —la primera y la última del rango— se caen
    # enteras. No es lo mismo que una semana con poca cobertura: acá no falta
    # una parte del rodeo, faltan días. La última daba dos días contra la
    # semana completa de descargas y dibujaba un pico de 3,6 litros por kg de
    # MS. Marcarla no alcanza: en el gráfico el pico se ve igual.
    def dias_de(s):
        return len(s["dias"]) if isinstance(s.get("dias"), set) else s.get("dias", 0)

    todas = sorted(semanas.values(), key=lambda x: x["semana"])
    usables = [s for s in todas if s["kg_ms"] > 0 and dias_de(s) >= DIAS_MIN_SEMANA]
    recortadas = [s["semana"] for s in todas
                  if s["kg_ms"] > 0 and dias_de(s) < DIAS_MIN_SEMANA]
    serie = [cerrar(s) for s in usables]

    lotes = {}
    for lote, sems in por_lote.items():
        lotes[lote] = [{
            "semana": k,
            "conversion": round(v["kg_leche"] / v["kg_ms"], 3) if v["kg_ms"] else None,
            "litros_vaca": round(v["kg_leche"] / v["vacas_dia"], 1) if v["vacas_dia"] else None,
            "ms_vaca": round(v["kg_ms"] / v["vacas_dia"], 1) if v["vacas_dia"] else None,
            **_economia(v),
        } for k, v in sorted(sems.items())]

    # --- Mensual, anclado al control lechero --------------------------------
    # Cada control es un punto REAL. La leche y la materia seca se toman del
    # mes calendario de ese control.
    # Mismo criterio que arriba, en lote × mes: se acumula por lote, se aplica
    # la banda de plausibilidad y recién ahí se suma al mes.
    lm = {}
    for (lote, dia), v in diario.items():
        a = lm.setdefault((lote, dia[:7]), {"kg_leche": 0.0, "kg_ms": 0.0, "vacas_dia": 0})
        a["kg_leche"] += v["kg_leche"]
        a["kg_ms"] += v["kg_ms"]
        a["vacas_dia"] += int(v["vacas"] or 0)

    por_mes = {}
    for (lote, m), a in lm.items():
        acum = por_mes.setdefault(m, {"kg_leche": 0.0, "kg_ms": 0.0,
                                      "vacas_dia": 0, "vacas_dia_total": 0})
        acum["vacas_dia_total"] += a["vacas_dia"]
        if a["kg_ms"] <= 0 or a["kg_leche"] <= 0 or not a["vacas_dia"]:
            continue
        ms_vaca = a["kg_ms"] / a["vacas_dia"]
        if not (alimentacion.MS_MIN_PLAUSIBLE <= ms_vaca <= alimentacion.MS_MAX_PLAUSIBLE):
            continue
        acum["kg_leche"] += a["kg_leche"]
        acum["kg_ms"] += a["kg_ms"]
        acum["vacas_dia"] += a["vacas_dia"]

    meses = []
    for c in _filas(solidos_control):
        f = _fecha(c["fecha"])
        m = f.strftime("%Y-%m")
        datos = por_mes.get(m)
        if not datos or datos["kg_ms"] <= 0:
            continue
        grasa = float(c["grasa"] or 0)
        prote = float(c["proteina"] or 0)
        pct = (grasa + prote) / 100.0
        kg_sol = datos["kg_leche"] * pct
        meses.append({
            "mes": m, "control": f.isoformat(), "vacas_control": int(c["vacas"] or 0),
            "grasa": round(grasa, 2), "proteina": round(prote, 2),
            "kg_leche": round(datos["kg_leche"]),
            "kg_solidos": round(kg_sol),
            "kg_ms": round(datos["kg_ms"]),
            "conversion_solidos": round(kg_sol / datos["kg_ms"], 4) if datos["kg_ms"] else None,
            "conversion_leche": round(datos["kg_leche"] / datos["kg_ms"], 3) if datos["kg_ms"] else None,
            "cobertura": (round(100 * datos["vacas_dia"] / datos["vacas_dia_total"], 1)
                          if datos["vacas_dia_total"] else None),
        })
    meses.sort(key=lambda x: x["mes"])

    completas = [s for s in serie if not s["parcial"] and s["conversion"]]
    tendencia = None
    if len(completas) >= 4:
        # Comparar el último mes contra el anterior dice más que la última
        # semana contra la primera, que puede ser puro ruido.
        ult = completas[-4:]
        prev = completas[-8:-4] if len(completas) >= 8 else None
        prom = lambda g: sum(x["conversion"] for x in g) / len(g)
        tendencia = {
            "ultimas4": round(prom(ult), 3),
            "previas4": round(prom(prev), 3) if prev else None,
            "cambio_pct": (round(100 * (prom(ult) - prom(prev)) / prom(prev), 1)
                           if prev and prom(prev) else None),
        }

    return {
        "semanas": serie,
        "lotes": lotes,
        "meses": meses,
        "tendencia": tendencia,
        "diagnostico": diagnosticar(serie),
        "sin_mapear": sorted(str(x) for x in sin_lote),
        "fuera_ordene": sorted(str(x) for x in no_ordene),
        # Lotes que quedaron fuera del cálculo alguna semana, con el motivo.
        # Se muestran: son problemas de carga del tambo, no del código, y el
        # que mira la pantalla tiene que poder ir a arreglarlos.
        "descartes": [{"lote": l, **d} for l, d in
                      sorted(descartes.items(), key=lambda x: -x[1]["semanas"])],
        # Semanas cortadas al medio por los bordes del rango pedido — no
        # entran a la serie porque unos pocos días no representan la semana,
        # ni siquiera marcadas como parciales.
        "semanas_recortadas": recortadas,
        "inicio_alimentacion": INICIO_ALIMENTACION,
    }
