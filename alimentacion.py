# -*- coding: utf-8 -*-
"""Consumo, materia seca y eficiencia de conversión.

Lo que se quiere saber de cada vaca es cuánta leche produce, con qué sólidos, y
cuánto salió darle de comer. Este módulo arma las dos mitades que HOY se pueden
calcular; la tercera —la plata— falta y se explica más abajo.

    kg de sólidos = kg de leche × (% grasa + % proteína) / 100
    conversión    = kg de sólidos / kg de materia seca consumida

LA CONVERSIÓN ES UNA MEDIDA DE GRUPO, NO DE VACA. El TMR se entrega al corral y
no hay comederos individuales: a todas las vacas del grupo se les imputa la
misma materia seca. Entonces, DENTRO de un grupo, ordenar por conversión es
ordenar por kg de sólidos con otro nombre — el dato de comida no agrega nada.
Donde sí agrega es al comparar grupos entre sí y al tambo contra sí mismo en el
tiempo. Medido el 26/07/2026 sobre las cuatro semanas al 21/07:

    Rodeo 2   23,3 kg MS → 3,81 kg sólidos → 0,163
    Rodeo 3   22,3 kg MS → 3,14 kg sólidos → 0,141
    Rodeo 1   23,8 kg MS → 3,24 kg sólidos → 0,136   (vacas frescas, DIM 23)
    Rodeo 5   25,1 kg MS → 2,82 kg sólidos → 0,112   ← come más y convierte peor
    tambo                                    0,129

Los 22 a 25 kg de MS por vaca confirman que el mixer entrega la ración completa:
no hay pastoreo sin contabilizar que arruine la comparación (Haasten tiene el
campo `dryMatterFreeRangePerDay` y para los rodeos de ordeñe está en 0).

SOLO GRUPOS DE ORDEÑE. En preparto el cálculo da 210 kg de MS por vaca, y no es
un error de los datos: el lote alimenta a todas las vacas del corral pero solo
unas pocas están en ordeñe, así que el denominador es una fracción del
numerador. Los grupos que no son de ordeñe quedan EXCLUIDOS del indicador en
vez de mostrarse con un número absurdo.

DE DÓNDE SALE LA MATERIA SECA. Haasten no la entrega hecha: `kgHeads` de cada
lote es el objetivo configurado (24,3 constante), no lo que se entregó. Hay que
calcularla, y se puede porque cada operación del mixer tiene UNA sola receta
(verificado: 314 de 314) y las cargas traen el %MS de cada ingrediente:

    %MS de la receta = Σ(kg del ingrediente × %MS) / Σ(kg del ingrediente)
    kg de MS al lote = kg descargados × %MS de la receta de esa operación

Las cargas y las descargas de una misma operación cierran dentro del 15% en el
74% de los casos y dentro del 4% en los normales, así que la receta identifica
bien la mezcla. Los %MS por receta salen coherentes: ~50,5% las de los rodeos de
ordeñe, 48,8% preparto vaquillonas, 39% preparto vacas y 90,1% recría (que come
balanceado seco, no TMR).

DE DÓNDE SALE LA PLATA. Los 70 ingredientes de Haasten tienen `price: 0` y La
Serenísima solo publica datos físicos, sin importes: ninguno de los dos precios
que hacen falta está en un sistema conectado. Los carga el tambo en una
planilla y los lee `precios_alimentos.py`. Con eso este módulo calcula:

    costo de la receta = Σ(kg del ingrediente × $/kg) / Σ(kg del ingrediente)
    costo del lote/día = kg descargados × $/kg de la receta de esa operación
    LITROS LIBRES      = litros producidos − costo del alimento / $ del litro

LOS LITROS LIBRES SON EL MARGEN EXPRESADO EN LITROS, y solo descuentan el
alimento: no hay mano de obra, sanidad, amortizaciones ni estructura. Es el
"litros libres de alimentación" de siempre, no la rentabilidad del tambo.

EL COSTO SE CALCULA SOBRE KG FRESCOS, no sobre materia seca: es como se carga
el mixer, como se factura el insumo y como se descarga al lote. La materia seca
se sigue usando para la conversión, que es otra cuenta.

SI FALTA EL PRECIO DE UN INGREDIENTE, LA RECETA QUEDA SIN COSTO. No se le pone
el promedio de los otros ni se lo cuenta como cero: las dos cosas darían un
costo que parece completo y no lo es, y con eso alguien decide. Se informa qué
proporción de los kg de cada receta tiene precio (`cobertura_precios`) y las
recetas por debajo de `COBERTURA_MIN` no reportan costo.
"""
import collections
import datetime

import rebano

# Referencias de la industria para vacas de alta producción, para que el número
# no quede huérfano en la pantalla. No son metas del tambo: son un contexto.
CONVERSION_BAJA = 0.13
CONVERSION_BUENA = 0.16

# Banda de consumo físicamente posible para una vaca EN ORDEÑE, en kg de materia
# seca por día. Fuera de esta banda el problema no es la vaca: son las descargas
# que no se registraron completas.
#
# No es un umbral inventado para que los números queden lindos. El caso que lo
# motivó: el lote "Enfermeria" (grupo Rodeo 9) tiene 3.142 kg registrados en
# cuatro semanas para 35 vacas — 7,5 kg de MS por vaca y por día — y con eso la
# conversión daba 0,349, más del doble que el mejor rodeo del tambo y por encima
# de lo que permite la biología. Ese número encabezaba el ranking. Una vaca de
# 36 kg de leche necesita más de 20 kg de MS; por debajo de 10 lo que falta son
# datos, no comida. Los grupos fuera de banda se muestran igual, con el motivo,
# pero no entran en el total del tambo ni en la conversión por vaca.
MS_MIN_PLAUSIBLE = 10.0
MS_MAX_PLAUSIBLE = 35.0

# Ventana por defecto: cuatro semanas. Menos que eso y un día de descarga doble
# (o un día sin descargar) mueve mucho el promedio.
DIAS_DEFECTO = 28
RANGO_MAX_DIAS = 120

# Proporción mínima de los kg de una receta que tiene que tener precio para
# reportar su costo. Con menos que esto el número sería una fracción de la
# ración haciéndose pasar por la ración entera, y siempre para abajo: un costo
# subestimado se lee como una ración barata, que es justo la conclusión
# equivocada. Se informa la cobertura real igual, para que se vea qué falta.
COBERTURA_MIN = 0.95


# --- Materia seca entregada, desde el proveedor ------------------------------

def ms_por_lote_dia(consumos: dict) -> tuple[dict, dict]:
    """({(lote, fecha): kg_de_MS}, diagnóstico).

    El %MS se calcula por RECETA sobre todo el período, no operación por
    operación: una operación suelta puede ser una carga parcial y dar un %MS
    disparatado (se vieron cargas de 91 kg con 74,6%), mientras que la receta
    agregada da valores estables.
    """
    kg_receta = collections.defaultdict(float)
    ms_receta = collections.defaultdict(float)
    receta_de_op = {}
    for c in consumos.get("cargas") or []:
        kg, ms, receta = c.get("kg") or 0, c.get("ms_pct"), c.get("receta")
        if kg <= 0 or not receta:
            continue
        kg_receta[receta] += kg
        if ms is not None:
            ms_receta[receta] += kg * ms / 100.0
        receta_de_op[c.get("operacion")] = receta
    pct = {r: ms_receta[r] / kg_receta[r] for r in kg_receta if kg_receta[r]}

    salida = collections.defaultdict(float)
    kg_sin_receta = 0.0
    kg_negativos = 0.0
    for d in consumos.get("descargas") or []:
        kg = d.get("kg") or 0
        if kg < 0:
            # Correcciones y devoluciones. Son el 0,1% de los kg del período:
            # se descuentan igual, pero se informan.
            kg_negativos += kg
        if kg == 0:
            continue
        p = pct.get(receta_de_op.get(d.get("operacion")))
        if p is None:
            # La mezcla se cargó antes del inicio del período: no se sabe su
            # receta. Se deja afuera y se avisa, en vez de meterle un %MS
            # promedio que no le corresponde.
            kg_sin_receta += kg
            continue
        fecha = _fecha_de(d.get("fecha"))
        if fecha is None:
            kg_sin_receta += kg
            continue
        salida[((d.get("lote") or "").strip(), fecha)] += kg * p
    return dict(salida), {
        "recetas": {r: round(100 * v, 1) for r, v in sorted(pct.items())},
        "kg_sin_receta": round(kg_sin_receta),
        "kg_negativos": round(kg_negativos),
    }


def costo_por_lote_dia(consumos: dict, precios: dict) -> tuple[dict, dict]:
    """({(lote, fecha): $ del alimento}, diagnóstico).

    Espejo de `ms_por_lote_dia`, con la misma estructura y el mismo criterio: el
    costo se resuelve POR RECETA sobre todo el período y después se aplica a los
    kg descargados. Una operación suelta puede ser una carga parcial y dar un
    $/kg disparatado; la receta agregada es estable.

    `precios`: el dict de `precios_alimentos.leer()["precios"]`, indexado por
    nombre normalizado.

    Se calcula sobre kg FRESCOS a propósito (ver el encabezado del módulo).
    """
    import precios_alimentos    # perezoso: sin planilla el resto del módulo anda igual

    kg_receta = collections.defaultdict(float)
    pesos_receta = collections.defaultdict(float)
    kg_sin_precio = collections.defaultdict(float)
    faltantes = collections.defaultdict(float)
    receta_de_op = {}
    estimados = set()
    for c in consumos.get("cargas") or []:
        kg, receta, ingr = c.get("kg") or 0, c.get("receta"), c.get("ingrediente")
        if kg <= 0 or not receta:
            continue
        kg_receta[receta] += kg
        receta_de_op[c.get("operacion")] = receta
        p = precios.get(precios_alimentos._norm(ingr)) if ingr else None
        if p is None or p.get("precio_kg") is None:
            kg_sin_precio[receta] += kg
            if ingr:
                faltantes[ingr] += kg
            continue
        pesos_receta[receta] += kg * p["precio_kg"]
        if p.get("estimado"):
            estimados.add(ingr)

    # $/kg de mezcla por receta, solo donde los precios cubren lo suficiente.
    cobertura = {r: (1 - kg_sin_precio[r] / kg_receta[r]) if kg_receta[r] else 0.0
                 for r in kg_receta}
    por_kg = {r: pesos_receta[r] / kg_receta[r]
              for r in kg_receta
              if kg_receta[r] and cobertura[r] >= COBERTURA_MIN}

    salida = collections.defaultdict(float)
    kg_sin_costo = 0.0
    for d in consumos.get("descargas") or []:
        kg = d.get("kg") or 0
        if kg == 0:
            continue
        p = por_kg.get(receta_de_op.get(d.get("operacion")))
        fecha = _fecha_de(d.get("fecha"))
        if p is None or fecha is None:
            kg_sin_costo += kg
            continue
        salida[((d.get("lote") or "").strip(), fecha)] += kg * p
    return dict(salida), {
        "costo_por_kg_receta": {r: round(v, 2) for r, v in sorted(por_kg.items())},
        "cobertura_precios": {r: round(100 * v) for r, v in sorted(cobertura.items())},
        "recetas_sin_costo": sorted(r for r in kg_receta if r not in por_kg),
        "kg_sin_costo": round(kg_sin_costo),
        # Qué ingrediente falta cargar, ordenado por cuánto pesa: es la lista de
        # trabajo del tambo, no un mensaje genérico de "faltan precios".
        "ingredientes_sin_precio": [
            {"ingrediente": n, "kg": round(k)}
            for n, k in sorted(faltantes.items(), key=lambda kv: -kv[1])],
        "ingredientes_estimados": sorted(estimados),
    }


def _fecha_de(v):
    """Las descargas traen la fecha como milisegundos desde epoch."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    try:
        return datetime.datetime.fromtimestamp(float(v) / 1000).date()
    except (TypeError, ValueError, OSError):
        return None


# --- Producción, del lado DelPro ---------------------------------------------

def sql_produccion_grupo_dia(desde, hasta, herd=None) -> str:
    """Vacas en ordeñe y kg de leche por grupo y por día.

    El denominador de la conversión sale de acá y no de un conteo fijo: en el
    grupo de frescas pasaron 868 vacas distintas en 28 días teniendo ~400 a la
    vez. Con un conteo estático el kg de MS por vaca sale a la mitad.
    """
    cond = rebano.condicion_herd("ag", herd)
    return f"""
        SELECT CAST(ad.Date AS date) AS fecha, ag.OID AS grupo, g.Name AS nombre,
               COUNT(DISTINCT ad.BasicAnimal) AS vacas,
               SUM(ad.TotalYield) AS kg_leche
        FROM AnimalDaily ad
        JOIN AnimalGroup ag ON ag.OID = ad.AnimalGroup
        JOIN AbstractGroup g ON g.OID = ag.OID AND g.GCRecord IS NULL
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date BETWEEN '{desde}' AND '{hasta}'
          {f"AND {cond}" if cond else ""}
        GROUP BY ad.Date, ag.OID, g.Name
    """


def sql_produccion_vaca(desde, hasta, herd=None) -> str:
    """Por vaca: leche del período, su grupo actual, lactancia y días en leche."""
    cond = rebano.condicion_herd("ag", herd)
    return f"""
        SELECT b.Number AS rp, b.OID AS animal,
               MAX(ad.AnimalGroup) AS grupo,
               COUNT(*) AS dias,
               SUM(ad.TotalYield) AS kg_leche,
               MAX(ad.DIM) AS dim,
               MAX(ad.LactationNumber) AS lactancia
        FROM AnimalDaily ad
        JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
        JOIN AnimalGroup ag ON ag.OID = ad.AnimalGroup
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
          AND ad.Date BETWEEN '{desde}' AND '{hasta}'
          AND b.GCRecord IS NULL AND b.Number > 0
          {f"AND {cond}" if cond else ""}
        GROUP BY b.Number, b.OID
    """


# Los controles se toman con una ventana hacia atrás: el control es mensual, así
# que una vaca puede no tener uno DENTRO del período y sí uno reciente que la
# describe bien. `MilkTest` se une por `AnimalHistoricalData`, nunca por
# `MilkingTestAnimal` (colisión de OIDs, ver CLAUDE.md).
DIAS_CONTROL_ATRAS = 45


def sql_solidos_vaca(desde, hasta, herd=None) -> str:
    """Grasa y proteína promedio de cada vaca, de sus controles lecheros."""
    return f"""
        SELECT b.Number AS rp,
               COUNT(*) AS controles,
               AVG(mt.Fat) AS grasa,
               AVG(mt.Protein) AS proteina,
               MAX(CAST(h.DateAndTime AS date)) AS ultimo
        FROM MilkTest mt
        JOIN AnimalHistoricalData h ON h.OID = mt.OID
        JOIN BasicAnimal b ON b.OID = h.BasicAnimal
        WHERE mt.Fat > 0 AND mt.Protein > 0
          AND CAST(h.DateAndTime AS date)
              BETWEEN DATEADD(day, -{DIAS_CONTROL_ATRAS}, '{desde}') AND '{hasta}'
          AND b.GCRecord IS NULL AND b.Number > 0
          AND {rebano.filtro("b", herd)}
        GROUP BY b.Number
    """


# --- Cruce -------------------------------------------------------------------

def _filas(data) -> list:
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{c: f[i] for c, i in idx.items()} for f in data["rows"]]


def analizar(prod_dia, prod_vaca, solidos, ms_lote_dia: dict, grupos: list,
             lote_de_grupo: dict, diagnostico: dict = None,
             costo_lote_dia: dict = None, precio_litro: float = None) -> dict:
    """Conversión por grupo y sólidos por vaca, y —si hay precios— costo de
    alimentación y litros libres.

    `grupos`: la lista de `conciliacion.grupos_de`, que trae `es_ordene`.
    `lote_de_grupo`: {oid_grupo: nombre_de_lote}, de `conciliacion.lote_de_grupo`.
    `costo_lote_dia`: salida de `costo_por_lote_dia`. None = sin planilla de
        precios; todo lo físico se calcula igual y las columnas de plata quedan
        en None, que es distinto de cero y así lo tiene que mostrar la pantalla.
    `precio_litro`: $ por litro de leche, de `precios_alimentos`. Sin él hay
        costo en pesos pero NO litros libres.
    """
    costo_lote_dia = costo_lote_dia or {}
    info = {g["oid"]: g for g in grupos}
    # Se suman por grupo solo los días en que HAY las dos cosas: producción y
    # descarga. Un día sin descarga registrada no es un día de ayuno, es un
    # agujero de datos, y contarlo hundiría la conversión del grupo.
    acum = collections.defaultdict(lambda: {"ms": 0.0, "leche": 0.0, "vacas_dia": 0,
                                            "dias": 0, "costo": 0.0, "dias_costo": 0})
    for f in _filas(prod_dia):
        oid = int(f["grupo"])
        lote = lote_de_grupo.get(oid)
        g = info.get(oid)
        # Solo grupos de ordeñe: en preparto el lote alimenta a vacas que no
        # ordeñan y el indicador da cualquier cosa.
        if not lote or not g or not g["es_ordene"]:
            continue
        fecha = f["fecha"]
        if isinstance(fecha, str):
            fecha = datetime.date.fromisoformat(fecha)
        ms = ms_lote_dia.get((lote, fecha))
        if ms is None:
            continue
        a = acum[oid]
        a["ms"] += ms
        a["leche"] += float(f["kg_leche"] or 0)
        a["vacas_dia"] += int(f["vacas"] or 0)
        a["dias"] += 1
        # El costo se acumula aparte y con su propio contador de días: puede
        # faltar el precio de una receta y no de otra, y mezclando los días la
        # división daría un costo diluido en jornadas que no se valorizaron.
        costo = costo_lote_dia.get((lote, fecha))
        if costo is not None:
            a["costo"] += costo
            a["dias_costo"] += 1

    # Sólidos: el % de cada vaca, promediado por grupo con el peso de su leche.
    sol_vaca = {int(f["rp"]): f for f in _filas(solidos)}
    prod = _filas(prod_vaca)
    leche_grupo = collections.defaultdict(float)
    solidos_grupo = collections.defaultdict(float)
    for p in prod:
        oid = int(p["grupo"]) if p["grupo"] is not None else None
        s = sol_vaca.get(int(p["rp"]))
        if oid is None or not s:
            continue
        kg = float(p["kg_leche"] or 0)
        leche_grupo[oid] += kg
        solidos_grupo[oid] += kg * (float(s["grasa"]) + float(s["proteina"])) / 100.0

    filas_grupo, tot_ms, tot_sol = [], 0.0, 0.0
    tot_costo, tot_leche_costo = 0.0, 0.0
    for oid, a in acum.items():
        g = info[oid]
        # % de sólidos del grupo, de las vacas que tienen control.
        pct = (solidos_grupo[oid] / leche_grupo[oid]) if leche_grupo.get(oid) else None
        if not a["vacas_dia"] or pct is None:
            continue
        ms_vaca = a["ms"] / a["vacas_dia"]
        leche_vaca = a["leche"] / a["vacas_dia"]
        sol_vaca_dia = leche_vaca * pct

        # Costo por vaca y por día. El denominador son las vacas-día DE LOS DÍAS
        # VALORIZADOS, no del período entero: si se valorizaron 20 de 28 días,
        # dividir por los 28 daría un costo 30% más bajo del real.
        costo_vaca, litros_pagar, litros_libres, pct_libres = None, None, None, None
        if a["dias_costo"]:
            vacas_dia_costo = a["vacas_dia"] * a["dias_costo"] / a["dias"]
            costo_vaca = a["costo"] / vacas_dia_costo if vacas_dia_costo else None
        if costo_vaca is not None and precio_litro:
            litros_pagar = costo_vaca / precio_litro
            litros_libres = leche_vaca - litros_pagar
            pct_libres = 100 * litros_libres / leche_vaca if leche_vaca else None

        motivo = None
        if ms_vaca < MS_MIN_PLAUSIBLE:
            motivo = (f"Solo {ms_vaca:.1f} kg de materia seca por vaca y por día: una vaca "
                      f"en ordeñe come más del doble. Faltan descargas sin registrar en "
                      f"el lote, así que la conversión de este grupo saldría inflada.")
        elif ms_vaca > MS_MAX_PLAUSIBLE:
            motivo = (f"{ms_vaca:.1f} kg de materia seca por vaca y por día es más de lo "
                      f"que come una vaca: el lote debe estar alimentando a más animales "
                      f"de los que ordeñan en este grupo.")
        confiable = motivo is None
        if confiable:
            tot_ms += a["ms"]
            tot_sol += a["leche"] * pct
            if costo_vaca is not None:
                tot_costo += a["costo"]
                # Litros del mismo subconjunto de días que el costo, para que el
                # total del tambo divida cosas comparables.
                tot_leche_costo += a["leche"] * a["dias_costo"] / a["dias"]
        filas_grupo.append({
            "oid": oid, "grupo": g["nombre"], "numero": g["numero"],
            "lote": lote_de_grupo.get(oid),
            "dias": a["dias"],
            "vacas_promedio": round(a["vacas_dia"] / a["dias"], 1) if a["dias"] else None,
            "kg_ms_vaca_dia": round(ms_vaca, 1),
            "kg_leche_vaca_dia": round(leche_vaca, 1),
            "pct_solidos": round(100 * pct, 2),
            "kg_solidos_vaca_dia": round(sol_vaca_dia, 2),
            "conversion": round(sol_vaca_dia / ms_vaca, 3) if ms_vaca else None,
            "dias_con_costo": a["dias_costo"],
            "costo_vaca_dia": round(costo_vaca, 2) if costo_vaca is not None else None,
            "costo_por_litro": (round(costo_vaca / leche_vaca, 2)
                                if costo_vaca is not None and leche_vaca else None),
            "litros_para_pagar": round(litros_pagar, 1) if litros_pagar is not None else None,
            "litros_libres": round(litros_libres, 1) if litros_libres is not None else None,
            "pct_litros_libres": round(pct_libres, 1) if pct_libres is not None else None,
            "confiable": confiable,
            "motivo": motivo,
        })
    # Los no confiables al final: si no, encabezan el ranking justamente por
    # estar mal (menos materia seca registrada = conversión más alta).
    filas_grupo.sort(key=lambda f: (not f["confiable"], -(f["conversion"] or 0)))

    # Grupos que tienen lote asignado pero ninguna descarga en el período: son
    # vacas comiendo sin que quede registro. No aparecerían en ningún lado si no
    # se las buscara a propósito.
    con_dato = set(acum)
    sin_descargas = [
        {"grupo": g["nombre"], "numero": g["numero"], "lote": lote_de_grupo[g["oid"]],
         "cabezas": g["cabezas"]}
        for g in grupos
        if g["es_ordene"] and g["cabezas"] > 0 and g["oid"] in lote_de_grupo
        and g["oid"] not in con_dato]

    # Por vaca: los sólidos son medidos e individuales. La materia seca es la de
    # su grupo, repartida en partes iguales — y eso hay que decirlo en pantalla.
    # Solo de los grupos confiables: en los otros la materia seca es incompleta
    # y arrastraría a cada vaca del grupo.
    ms_de_grupo = {f["oid"]: f["kg_ms_vaca_dia"] for f in filas_grupo if f["confiable"]}
    # El costo por vaca sale del costo de SU GRUPO, igual que la materia seca y
    # con la misma advertencia: es un reparto en partes iguales, no una medición.
    # Adentro de un grupo, ordenar por litros libres es ordenar por producción
    # con otro nombre — lo único que varía entre esas vacas es la leche.
    costo_de_grupo = {f["oid"]: f["costo_vaca_dia"] for f in filas_grupo
                      if f["confiable"] and f["costo_vaca_dia"] is not None}
    filas_vaca = []
    for p in prod:
        oid = int(p["grupo"]) if p["grupo"] is not None else None
        g = info.get(oid)
        s = sol_vaca.get(int(p["rp"]))
        dias = int(p["dias"] or 0)
        if not g or not dias:
            continue
        leche_dia = float(p["kg_leche"] or 0) / dias
        pct = ((float(s["grasa"]) + float(s["proteina"])) / 100.0) if s else None
        ms_dia = ms_de_grupo.get(oid)
        sol_dia = leche_dia * pct if pct is not None else None
        costo_dia = costo_de_grupo.get(oid)
        libres = (leche_dia - costo_dia / precio_litro
                  if costo_dia is not None and precio_litro else None)
        filas_vaca.append({
            "rp": int(p["rp"]),
            "grupo": g["nombre"],
            "lote": lote_de_grupo.get(oid),
            "lactancia": p["lactancia"], "dim": p["dim"], "dias": dias,
            "kg_leche_dia": round(leche_dia, 1),
            "grasa": round(float(s["grasa"]), 2) if s else None,
            "proteina": round(float(s["proteina"]), 2) if s else None,
            "controles": int(s["controles"]) if s else 0,
            "kg_solidos_dia": round(sol_dia, 2) if sol_dia is not None else None,
            "kg_ms_dia": ms_dia,
            "conversion": (round(sol_dia / ms_dia, 3)
                           if (sol_dia is not None and ms_dia) else None),
            "costo_vaca_dia": costo_dia,
            "litros_libres": round(libres, 1) if libres is not None else None,
            "pct_litros_libres": (round(100 * libres / leche_dia, 1)
                                  if libres is not None and leche_dia else None),
        })
    filas_vaca.sort(key=lambda f: -(f["conversion"] or -1))

    sin_control = sum(1 for f in filas_vaca if not f["controles"])
    no_confiables = [f for f in filas_grupo if not f["confiable"]]

    # --- Economía del tambo -------------------------------------------------
    # Se pondera por VACAS-DÍA de los grupos que tienen costo, no por el promedio
    # simple de los grupos: un rodeo de 400 vacas y otro de 40 no pesan igual, y
    # el promedio simple le daría al chico diez veces la influencia que le toca.
    # Todo en None cuando falta la planilla o el precio: la pantalla tiene que
    # poder decir "no hay dato", nunca "costo cero".
    con_costo = [f for f in filas_grupo
                 if f["confiable"] and f["costo_vaca_dia"] is not None
                 and f["vacas_promedio"]]
    vd = sum(f["vacas_promedio"] for f in con_costo)
    costo_tambo = (sum(f["costo_vaca_dia"] * f["vacas_promedio"] for f in con_costo) / vd
                   if vd else None)
    leche_tambo = (sum(f["kg_leche_vaca_dia"] * f["vacas_promedio"] for f in con_costo) / vd
                   if vd else None)
    libres_tambo = (leche_tambo - costo_tambo / precio_litro
                    if costo_tambo is not None and leche_tambo and precio_litro else None)
    economia = {
        "precio_litro": precio_litro,
        "costo_vaca_dia": round(costo_tambo, 2) if costo_tambo is not None else None,
        "costo_por_litro": (round(costo_tambo / leche_tambo, 2)
                            if costo_tambo is not None and leche_tambo else None),
        "kg_leche_vaca_dia": round(leche_tambo, 1) if leche_tambo is not None else None,
        "litros_para_pagar": (round(costo_tambo / precio_litro, 1)
                              if costo_tambo is not None and precio_litro else None),
        "litros_libres": round(libres_tambo, 1) if libres_tambo is not None else None,
        "pct_litros_libres": (round(100 * libres_tambo / leche_tambo, 1)
                              if libres_tambo is not None and leche_tambo else None),
        # Sobre cuántas vacas se calculó: sin esto un costo sacado de un solo
        # rodeo se lee como el costo del tambo entero.
        "vacas": round(vd) if vd else 0,
        "grupos_con_costo": len(con_costo),
        "grupos_confiables": len(filas_grupo) - len(no_confiables),
        "hay_costo": costo_tambo is not None,
        # El motivo tiene que apuntar a la causa REAL, no a la primera plausible.
        # Se probó con el mapeo de lotes vacío y el mensaje culpaba a la
        # cobertura de precios, que estaba al 100%: se manda a revisar el lugar
        # equivocado. Los tres casos son distintos y se arreglan en pantallas
        # distintas (planilla / conciliación de grupos / planilla otra vez).
        "falta": None if costo_tambo is not None else (
            "No hay planilla de precios cargada, o no tiene ningún precio."
            if not costo_lote_dia else
            "Ningún grupo de ordeñe tiene lote asignado: hay que mapearlos en "
            "la conciliación de grupos." if not filas_grupo else
            "Hay precios y hay grupos, pero ninguna descarga de sus lotes se "
            "pudo valorizar (mirá la cobertura de precios por receta)."),
    }

    # Cobertura sobre el rodeo EN ORDEÑE. Es la pregunta que importa —"¿estoy
    # analizando bien a las vacas que producen?"— y sin esto queda escondida:
    # las tablas muestran lo que sí se pudo calcular, nunca lo que falta.
    ordene = {g["oid"]: g for g in grupos if g["es_ordene"] and g["cabezas"] > 0}
    analizados = {f["oid"] for f in filas_grupo if f["confiable"]}
    cab_total = sum(g["cabezas"] for g in ordene.values())
    cab_ok = sum(g["cabezas"] for oid, g in ordene.items() if oid in analizados)
    motivo_de = {f["oid"]: f["motivo"] for f in no_confiables}
    sin_lote_oids = {g["oid"] for g in ordene.values() if g["oid"] not in lote_de_grupo}
    fuera = []
    for oid, g in ordene.items():
        if oid in analizados:
            continue
        if oid in motivo_de:
            motivo = motivo_de[oid]
        elif oid in sin_lote_oids:
            motivo = "No tiene lote asignado en la conciliación de grupos."
        else:
            motivo = ("No hay ninguna descarga registrada a su lote en el período: "
                      "esas vacas comen sin que quede registro.")
        fuera.append({"grupo": g["nombre"], "cabezas": g["cabezas"], "motivo": motivo})
    fuera.sort(key=lambda x: -x["cabezas"])

    return {
        "grupos": filas_grupo,
        "vacas": filas_vaca,
        "sin_descargas": sin_descargas,
        "cobertura": {
            "cabezas_ordene": cab_total,
            "cabezas_analizadas": cab_ok,
            "pct": round(100 * cab_ok / cab_total) if cab_total else None,
            "fuera": fuera,
        },
        "economia": economia,
        "resumen": {
            "conversion_tambo": round(tot_sol / tot_ms, 3) if tot_ms else None,
            "kg_ms_total": round(tot_ms),
            "kg_solidos_total": round(tot_sol),
            "vacas": len(filas_vaca),
            "vacas_sin_control": sin_control,
            "grupos": len(filas_grupo),
            "grupos_confiables": len(filas_grupo) - len(no_confiables),
            "grupos_no_confiables": len(no_confiables),
            "grupos_sin_descargas": len(sin_descargas),
            "cabezas_sin_descargas": sum(g["cabezas"] for g in sin_descargas),
            "referencia_baja": CONVERSION_BAJA,
            "referencia_buena": CONVERSION_BUENA,
        },
        "diagnostico": diagnostico or {},
    }
