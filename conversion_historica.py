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


def sql_grupos_ordene(herd=None) -> str:
    """Los OID de grupo que DelPro tiene marcados como de ordeñe.

    Hace falta filtrar por acá y no alcanza con "el grupo tuvo leche": los
    lotes de preparto tienen alguna vaca recién parida que todavía no movieron,
    así que aparecen con leche, pero el mixer les descarga la ración de TODO el
    lote —secas incluidas—. Esa MS entera se dividía entre ese puñado de vacas
    y daba 236 kg de materia seca por vaca, además de inflar el total del
    tambo. Un lote que come y no produce no puede entrar en una conversión.
    """
    cond = rebano.condicion_herd("ag", herd)
    return f"""
        SELECT c.[Group] AS grupo
        FROM CMSGroupMilkSetting c
        JOIN AnimalGroup ag ON ag.OID = c.[Group]
        WHERE c.GCRecord IS NULL AND c.EnableMilking = 1
          {f"AND {cond}" if cond else ""}
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


def armar(prod_dia, ms_lote_dia: dict, lote_de_grupo: dict,
          solidos_control, hoy: datetime.date, grupos_ordene=None) -> dict:
    """Cruza producción diaria con materia seca diaria y arma las dos series.

    `ms_lote_dia`: {(lote, fecha): kg_ms_totales} — sale de
    `alimentacion.ms_por_lote_dia()`.
    `lote_de_grupo`: {grupo_oid: lote} — el mapeo de `conciliacion.py`.
    `grupos_ordene`: OIDs de grupos de ordeñe (ver `sql_grupos_ordene`).
    """
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
        acum = diario.setdefault(clave, {"kg_leche": 0.0, "vacas": 0, "kg_ms": 0.0})
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
            "vacas_dia": 0, "dias": set()})
        ls["kg_leche"] += v["kg_leche"]
        ls["kg_ms"] += v["kg_ms"]
        ls["vacas_dia"] += int(v["vacas"] or 0)
        ls["dias"].add(dia)

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
            "vacas_dia_total": 0, "dias": set()})
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

        por_lote.setdefault(lote, {})[clave] = {
            "kg_leche": ls["kg_leche"], "kg_ms": ls["kg_ms"], "vacas_dia": vacas}

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
