# -*- coding: utf-8 -*-
"""Rentabilidad de UN animal: lo que produjo contra lo que costó darle de comer.

Es el mismo cálculo de «litros libres» de `alimentacion.py`, pero mirando una
vaca en vez del rodeo, y con su evolución en el tiempo. Los precios salen de la
planilla del tambo (`precios_alimentos.py`) porque no están en ningún sistema
conectado: los ingredientes del mixer vienen con precio 0.

    ingreso        = litros × precio del litro
    costo          = costo del LOTE de su grupo ese día ÷ vacas del grupo ese día
    margen         = ingreso − costo
    litros libres  = litros − costo ÷ precio del litro

TRES COSAS QUE HAY QUE TENER PRESENTES, y las tres están medidas:

1. LA SERIE ES SEMANAL, NO DIARIA. El mixer descarga irregular: puede entregar
   dos veces un día y ninguna al siguiente. Medido en Rodeo 2 sobre días
   consecutivos de julio 2026, el costo por vaca saltaba entre $4.695 y $11.053
   —más del doble— sin que cambiara nada del rodeo. Un gráfico diario mostraría
   esa irregularidad como si fuera la rentabilidad del animal. La semana la
   promedia, que es el mismo criterio que usa `conversion_historica.py`.

2. EL COSTO ES UN REPARTO, NO UNA MEDICIÓN. El TMR se entrega al corral y no hay
   comederos individuales: a todas las vacas del grupo se les imputa el mismo
   costo. Entonces la diferencia de rentabilidad entre dos vacas DEL MISMO GRUPO
   es enteramente su producción. Lo que este análisis agrega es el nivel: si esta
   vaca cubre lo que come, y por cuánto.

3. UN DÍA SIN DESCARGA REGISTRADA NO ES UN DÍA DE AYUNO. Se excluye del cálculo
   en vez de contarse como costo cero — contarlo daría un margen inflado
   justamente en los días peor cargados. Se informa cuántos días quedaron afuera.

EL COSTO DE ALIMENTACIÓN ARRANCA CON EL SISTEMA DEL MIXER, no con la vaca: antes
de `conversion_historica.INICIO_ALIMENTACION` no hay descargas con las que
valorizar nada. Así que el acumulado NO es "lo que costó esta vaca en su vida",
es lo que costó en el período que se puede medir, y la pantalla lo dice.
"""
import collections
import datetime

import rebano

# Días mínimos con dato para que una semana entre a la serie. Con menos, unos
# pocos días no representan la semana — mismo criterio que
# `conversion_historica.DIAS_MIN_SEMANA`.
DIAS_MIN_SEMANA = 4

# Proporción mínima de los días de la semana que tienen que estar valorizados.
# Una semana con un solo día de costo da un número creíble pero es de ese día,
# no de la semana.
COBERTURA_MIN_SEMANA = 0.5

# Edad a la que se espera el primer parto. NO sale de DDM (no hay un parámetro
# de crianza en `ReproductionSetting`): es la referencia de la industria para
# Holando, y sirve para decir cuánto atraso lleva una vaquillona que todavía no
# parió. Se muestra siempre como referencia, nunca como meta del tambo.
MESES_PRIMER_PARTO = 24

RANGO_MAX_DIAS = 400


def sql_dias(rp: int, desde, hasta, herd=None) -> str:
    """Un día por fila del animal: litros, DEL, lactancia y en qué grupo estaba.

    El GRUPO POR DÍA es lo que permite imputarle el costo correcto a una vaca que
    cambió de rodeo en el medio del período — que es lo normal. Sin esto se le
    aplicaría el costo de su grupo de hoy a toda su historia.
    """
    return f"""
        SELECT CONVERT(varchar(10), ad.Date, 120) AS fecha,
               CAST(ad.TotalYield AS decimal(8,2)) AS litros,
               ad.DIM AS dim, ad.LactationNumber AS lactancia,
               ad.AnimalGroup AS grupo_oid,
               ag.Number AS grupo_num, ag.Name AS grupo
        FROM AnimalDaily ad
        JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
        LEFT JOIN AbstractGroup ag ON ag.OID = ad.AnimalGroup AND ag.GCRecord IS NULL
        WHERE b.Number = {rp} AND ad.GCRecord IS NULL
          AND ad.Date >= '{desde}' AND ad.Date <= '{hasta}'
          AND {rebano.filtro('b', herd)}
        ORDER BY ad.Date
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_vacas_grupo_dia(desde, hasta, grupos: list, herd=None) -> str:
    """(grupo, fecha, vacas) — el DENOMINADOR con el que se reparte el costo.

    Se cuenta lo MEDIDO (las filas de `AnimalDaily` de ese grupo ese día) y no
    las cabezas declaradas del lote: en el grupo de frescas pasaron 868 vacas
    distintas en 28 días teniendo ~400 a la vez, así que un conteo fijo reparte
    el costo entre la mitad de los animales y lo duplica (ver `alimentacion.py`).

    Se acota a los grupos por los que pasó el animal para no barrer el rodeo
    entero: con la base local en SQL Express y poca RAM, un GROUP BY sobre todo
    `AnimalDaily` del período es justo lo que se cuelga.
    """
    lista = ", ".join(str(int(g)) for g in grupos) or "-1"
    return f"""
        SELECT ad.AnimalGroup AS grupo_oid,
               CONVERT(varchar(10), ad.Date, 120) AS fecha,
               COUNT(*) AS vacas
        FROM AnimalDaily ad
        WHERE ad.GCRecord IS NULL
          AND ad.AnimalGroup IN ({lista})
          AND ad.Date >= '{desde}' AND ad.Date <= '{hasta}'
        GROUP BY ad.AnimalGroup, CONVERT(varchar(10), ad.Date, 120)
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def _filas(data) -> list:
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{c: f[i] for c, i in idx.items()} for f in data["rows"]]


def _semana_de(d: datetime.date) -> tuple:
    """(clave, lunes). Se replica la de `conversion_historica` para no importar
    ese módulo entero, que arrastra sus consultas."""
    lunes = d - datetime.timedelta(days=d.weekday())
    iso = lunes.isocalendar()
    return f"{iso[0]}-S{iso[1]:02d}", lunes


def armar(dias_data, vacas_data, costo_lote_dia: dict, lote_de_grupo: dict,
          precio_litro: float, info: dict = None) -> dict:
    """Rentabilidad del animal: serie semanal, acumulado y por qué falta lo que
    falta.

    `dias_data`: salida de `sql_dias`.
    `vacas_data`: salida de `sql_vacas_grupo_dia`.
    `costo_lote_dia`: {(lote, fecha): $} de `alimentacion.costo_por_lote_dia`.
    `info`: la fila de `ficha_animal.sql_info_general` (para nacimiento y
        lactancia, que es lo que permite explicar el caso de la vaquillona que
        todavía no produce).
    """
    info = info or {}
    dias = _filas(dias_data)
    vacas_gd = {(int(f["grupo_oid"]), f["fecha"]): int(f["vacas"] or 0)
                for f in _filas(vacas_data) if f["grupo_oid"] is not None}

    sem = {}
    sin_lote, sin_costo, sin_vacas = set(), 0, 0
    for f in dias:
        fecha = f["fecha"]
        if isinstance(fecha, (datetime.date, datetime.datetime)):
            fecha = fecha.strftime("%Y-%m-%d")
        d = datetime.date.fromisoformat(fecha)
        clave, lunes = _semana_de(d)
        s = sem.setdefault(clave, {
            "semana": clave, "desde": lunes.isoformat(),
            "hasta": (lunes + datetime.timedelta(days=6)).isoformat(),
            "litros": 0.0, "dias": 0, "costo": 0.0, "dias_costo": 0,
            "litros_costo": 0.0, "dim": None, "lactancia": None, "grupos": set()})
        litros = float(f["litros"] or 0)
        s["litros"] += litros
        s["dias"] += 1
        if f.get("grupo"):
            s["grupos"].add(f["grupo"])
        # El DEL y la lactancia del último día de la semana: son los que ubican
        # la semana en la curva de lactancia.
        if f.get("dim") is not None:
            s["dim"] = int(f["dim"])
        if f.get("lactancia") is not None:
            s["lactancia"] = int(f["lactancia"])

        goid = int(f["grupo_oid"]) if f["grupo_oid"] is not None else None
        lote = lote_de_grupo.get(goid) if goid is not None else None
        if lote is None:
            if f.get("grupo"):
                sin_lote.add(f["grupo"])
            continue
        plata = costo_lote_dia.get((lote, d))
        n = vacas_gd.get((goid, fecha))
        if plata is None:
            # Día sin descarga registrada: se excluye. NO es un día de ayuno.
            sin_costo += 1
            continue
        if not n:
            sin_vacas += 1
            continue
        s["costo"] += plata / n
        s["dias_costo"] += 1
        # Los litros de los días VALORIZADOS, para que el costo por litro divida
        # cosas comparables.
        s["litros_costo"] += litros

    serie, descartadas = [], []
    for s in sorted(sem.values(), key=lambda x: x["semana"]):
        litros_dia = s["litros"] / s["dias"] if s["dias"] else None
        fila = {
            "semana": s["semana"], "desde": s["desde"], "hasta": s["hasta"],
            "dias": s["dias"], "dias_con_costo": s["dias_costo"],
            "litros": round(s["litros"], 1),
            "litros_dia": round(litros_dia, 1) if litros_dia is not None else None,
            "dim": s["dim"], "lactancia": s["lactancia"],
            "grupo": " / ".join(sorted(s["grupos"])) or None,
            "costo_dia": None, "ingreso_dia": None, "margen_dia": None,
            "litros_libres": None, "pct_litros_libres": None, "costo_por_litro": None,
        }
        # Semana cortada por los bordes del rango, o valorizada a medias: no
        # entra a la serie. Igual que en la conversión histórica, marcarla no
        # alcanza — en el gráfico el salto se ve igual.
        if s["dias"] < DIAS_MIN_SEMANA:
            descartadas.append({**fila, "motivo": f"solo {s['dias']} día(s) con dato"})
            continue
        if s["dias_costo"] and s["dias_costo"] / s["dias"] >= COBERTURA_MIN_SEMANA:
            costo_dia = s["costo"] / s["dias_costo"]
            litros_v = s["litros_costo"] / s["dias_costo"]
            fila["costo_dia"] = round(costo_dia, 2)
            fila["costo_por_litro"] = (round(costo_dia / litros_v, 2)
                                       if litros_v else None)
            if precio_litro:
                fila["ingreso_dia"] = round(litros_v * precio_litro, 2)
                fila["margen_dia"] = round(litros_v * precio_litro - costo_dia, 2)
                fila["litros_libres"] = round(litros_v - costo_dia / precio_litro, 1)
                fila["pct_litros_libres"] = (round(100 * fila["litros_libres"] / litros_v, 1)
                                             if litros_v else None)
        serie.append(fila)

    # --- Acumulado del período ---------------------------------------------
    # Solo de las semanas valorizadas: sumar el ingreso de semanas sin costo
    # daría un margen que crece sin que nadie coma.
    con = [f for f in serie if f["margen_dia"] is not None]
    dias_val = sum(f["dias_con_costo"] for f in con)
    ingreso = sum(f["ingreso_dia"] * f["dias_con_costo"] for f in con)
    costo = sum(f["costo_dia"] * f["dias_con_costo"] for f in con)
    acumulado = []
    corr = 0.0
    for f in serie:
        if f["margen_dia"] is not None:
            corr += f["margen_dia"] * f["dias_con_costo"]
        acumulado.append(round(corr))

    litros_total = sum(f["litros"] for f in serie)
    produce = litros_total > 0

    # --- Por qué no hay número, cuando no hay -------------------------------
    # Los tres motivos son distintos y se arreglan en lugares distintos: el
    # animal no produce (nada que hacer con el código), su grupo no tiene lote
    # (conciliación), o falta la planilla de precios (configuración).
    falta = None
    if not dias:
        falta = ("No hay registros diarios de este animal en el período. "
                 "`AnimalDaily` solo trae producción de las vacas en ordeñe.")
    elif not produce:
        falta = ("Este animal no produjo ningún litro en el período: no hay "
                 "ingreso contra el que comparar el costo.")
    elif not precio_litro:
        falta = ("Falta el precio de la leche en la planilla de precios: sin él "
                 "hay costo en pesos pero no margen ni litros libres.")
    elif not con:
        falta = (("Su grupo (" + ", ".join(sorted(sin_lote)) + ") no tiene lote "
                  "asignado en la conciliación de grupos, así que no se le puede "
                  "imputar comida.") if sin_lote else
                 "No se pudo valorizar ninguna semana completa.")

    # --- Vaquillona que todavía no parió ------------------------------------
    # Es el caso donde este análisis dice lo más importante y donde no hay ni un
    # litro que graficar: la plata ya se gastó y el ingreso todavía no empezó.
    espera = None
    lact = info.get("lactancia")
    nac = info.get("nacimiento")
    if not produce and nac and (lact in (0, None)):
        try:
            n = datetime.date.fromisoformat(str(nac)[:10])
            hoy = datetime.date.today()
            meses = (hoy.year - n.year) * 12 + (hoy.month - n.month)
            atraso = max(0, meses - MESES_PRIMER_PARTO)
            a, m = meses // 12, meses % 12
            edad = " y ".join(
                p for p in (f"{a} año" + ("s" if a != 1 else "") if a else "",
                            f"{m} mes" + ("es" if m != 1 else "") if m else "") if p)
            espera = {
                "meses_edad": meses,
                "meses_referencia": MESES_PRIMER_PARTO,
                "meses_atraso": atraso,
                "texto": (f"Tiene {edad} y todavía no parió. La referencia de primer "
                          f"parto es {MESES_PRIMER_PARTO} meses"
                          + (f", así que lleva {atraso} "
                             + ("mes" if atraso == 1 else "meses")
                             + " comiendo sin haber producido un litro."
                             if atraso else ".")),
            }
        except (TypeError, ValueError):
            espera = None

    return {
        "produce": produce,
        "semanas": serie,
        "acumulado": acumulado,
        "descartadas": descartadas,
        "resumen": {
            "dias_con_dato": sum(f["dias"] for f in serie),
            "dias_valorizados": dias_val,
            "litros_total": round(litros_total, 1),
            "litros_dia": (round(litros_total / sum(f["dias"] for f in serie), 1)
                           if serie and sum(f["dias"] for f in serie) else None),
            "ingreso": round(ingreso) if con else None,
            "costo": round(costo) if con else None,
            "margen": round(ingreso - costo) if con else None,
            "margen_dia": (round((ingreso - costo) / dias_val, 2)
                           if dias_val else None),
            "costo_por_litro": (round(costo / sum(f["litros_dia"] * f["dias_con_costo"]
                                                  for f in con), 2)
                                if con and sum(f["litros_dia"] * f["dias_con_costo"]
                                               for f in con) else None),
            "litros_libres_dia": (round(sum(f["litros_libres"] * f["dias_con_costo"]
                                            for f in con) / dias_val, 1)
                                  if dias_val and all(f["litros_libres"] is not None
                                                      for f in con) else None),
            "precio_litro": precio_litro,
            "semanas": len(serie),
            "semanas_valorizadas": len(con),
        },
        "dias_sin_costo": sin_costo,
        "dias_sin_vacas": sin_vacas,
        "grupos_sin_lote": sorted(sin_lote),
        "espera_primer_parto": espera,
        "falta": falta,
    }
