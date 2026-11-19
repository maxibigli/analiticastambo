# -*- coding: utf-8 -*-
"""Entrega de comida por rodeo y por día, para ponerla al lado de la producción de leche.

NEUTRAL: no sabe de qué sistema de alimentación vienen los datos (hoy MixerOne). El proveedor traduce lo suyo a estas
formas y este módulo hace la cuenta.

    descarga   {fecha: "AAAA-MM-DD", minuto: minutos desde la medianoche del día de la jornada, rodeo, kg_real, kg_teorico}
    alerta     {fecha, dia, rodeo, codigo}     (`dia` = el día del que habla la alerta; si falta, `fecha`)

LA «EFICIENCIA DE LA ENTREGA» TIENE DOS CARAS, y se miden aparte:

  * CUMPLIMIENTO: kg entregados contra los teóricos del día. Medido en La Ponderosa (03 al 16/11/2026) es casi 100 % todos los
    días (94 a 105 % en cuatro de los seis rodeos): el mixer entrega lo que le pidieron, así que SOLO no dice nada.
  * PUNTUALIDAD: cuándo llega la primera comida del rodeo contra lo habitual de ESE rodeo (la mediana de sus propios días, no un
    horario único: cada rodeo tiene el suyo). Es lo que más cambia: hubo días de 2 y 3 horas de atraso.

Con 14 días de historia NO se ve ninguna relación entre estos indicadores y la leche (r = −0,05 con 81 rodeo-días), y no hay que
vender lo contrario: la gráfica sirve para mirar, día por día, qué pasó con la comida y qué pasó con la leche, y para el análisis
formal cuando haya 6 a 8 semanas. Una descarga con 0 kg no es una comida y no cuenta.
"""
import copy
import statistics

MIN_DIAS_HABITUAL = 3       # días de comida que hacen falta para hablar de «lo habitual» de un rodeo


def por_rodeo(descargas, alertas=None) -> list:
    """`[{rodeo, habitual, dias: [{fecha, kg_real, kg_teorico, cumplimiento, descargas, primera, atraso, alertas}]}]`.

    `habitual` y `primera` en minutos desde la medianoche; `atraso` es `primera - habitual` (negativo si se adelantó) o None si
    el rodeo tiene menos de `MIN_DIAS_HABITUAL` días de comida. Un día sin comida figura solo si hubo una alerta ese día (por
    ejemplo «rodeo sin alimentar»), dentro del rango de días en que el rodeo sí comió, y va con ceros."""
    dias: dict = {}
    for d in descargas or []:
        rodeo, fecha = d.get("rodeo"), d.get("fecha")
        if not rodeo or not fecha or (d.get("kg_real") or 0) <= 0:
            continue
        x = dias.setdefault(rodeo, {}).setdefault(fecha, {"kg_real": 0.0, "kg_teorico": 0.0, "descargas": 0, "primera": None})
        x["kg_real"] += d["kg_real"]
        x["kg_teorico"] += d.get("kg_teorico") or 0
        x["descargas"] += 1
        minuto = d.get("minuto")
        if minuto is not None and (x["primera"] is None or minuto < x["primera"]):
            x["primera"] = minuto

    cuenta: dict = {}
    for a in alertas or []:
        rodeo, dia = a.get("rodeo"), a.get("dia") or a.get("fecha")
        if rodeo in dias and dia:
            cuenta[(rodeo, dia)] = cuenta.get((rodeo, dia), 0) + 1

    salida = []
    for rodeo in sorted(dias):
        propios = dias[rodeo]
        primeras = [v["primera"] for v in propios.values() if v["primera"] is not None]
        habitual = int(statistics.median(primeras)) if len(primeras) >= MIN_DIAS_HABITUAL else None
        desde, hasta = min(propios), max(propios)
        fechas = sorted(set(propios) | {f for (r, f) in cuenta if r == rodeo and desde <= f <= hasta})
        filas = []
        for f in fechas:
            v = propios.get(f) or {"kg_real": 0.0, "kg_teorico": 0.0, "descargas": 0, "primera": None}
            filas.append({
                "fecha": f, "kg_real": round(v["kg_real"], 1), "kg_teorico": round(v["kg_teorico"], 1),
                "cumplimiento": round(100 * v["kg_real"] / v["kg_teorico"], 1) if v["kg_teorico"] > 0 else None,
                "descargas": v["descargas"], "primera": v["primera"],
                "atraso": v["primera"] - habitual if habitual is not None and v["primera"] is not None else None,
                "alertas": cuenta.get((rodeo, f), 0)})
        salida.append({"rodeo": rodeo, "habitual": habitual, "dias": filas})
    return salida


def con_leche(rodeos, leche: dict, ultimo_completo: str) -> list:
    """Le pega a cada día la leche por vaca de DelPro: `leche = {rodeo: {fecha: {"vacas", "kg"}}}`.

    Un día posterior a `ultimo_completo` va SIN leche: el último día de DelPro viene a medio cargar y hundiría el promedio.
    `con_leche` dice si el rodeo tiene un grupo de DelPro mapeado (sin el mapeo de Conciliación no hay con qué cruzarlo)."""
    salida = copy.deepcopy(list(rodeos or []))
    for r in salida:
        propia = (leche or {}).get(r["rodeo"]) or {}
        r["con_leche"] = bool(propia)
        for d in r["dias"]:
            m = propia.get(d["fecha"]) if d["fecha"] <= str(ultimo_completo) else None
            ok = bool(m and m.get("vacas"))
            d["vacas"] = m["vacas"] if ok else None
            d["leche_vaca"] = round(m["kg"] / m["vacas"], 2) if ok else None
    return salida
