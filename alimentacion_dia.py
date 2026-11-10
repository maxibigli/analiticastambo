# -*- coding: utf-8 -*-
"""Cuándo y cuánto se le dio de comer al rodeo de ordeñe cada día, a partir de los consumos del mixer.

Alimenta la causa «alimentación» del veredicto de baja de producción (`veredicto_baja._alimentacion`). Es una
función PURA sobre lo que devuelve `proveedores.<proveedor>.consumos()`: no toca la red ni la base.

Qué se mide, y por qué eso:
  - La HORA DE LA PRIMERA COMIDA a los rodeos de ordeñe. Medido en La Ponderosa (1/10 al 9/11/2026): es casi siempre
    entre 06:55 y 07:15, con algunos días tardíos (08:29 a 09:14) y el 07/11 con la más tardía de todas, 09:52. Es lo
    que más separa un día de otro: la cantidad total apenas se mueve y el largo del hueco entre comidas no distingue
    nada (el 07/11 fue el hueco máximo MÁS CORTO del mes).
  - Los kg totales descargados, por si el problema no fue la hora sino que se dio menos comida.

Se compara contra la MEDIANA de la historia (no el promedio) y con un desvío robusto (MAD): los días tardíos son
justamente los valores raros, y con promedio y desvío común arrastrarían la base y se taparían a sí mismos.

Qué rodeos cuentan: los de la etapa «Ordeñe» que informa el proveedor (`etapa`). Si el proveedor no la informa
(Haasten), cuentan todas las descargas.
"""
import datetime
import statistics as st
import unicodedata

import veredicto_baja

# Una descarga antes de esta hora es de un viaje que arrancó la noche anterior: no es el desayuno de ese día.
INICIO_JORNADA = 4 * 60
MIN_HISTORIA = 5          # días con comida registrada para poder hablar de «lo normal»
KG_SD_MIN = 0.05         # fracción de la mediana de kg: piso del desvío de los kg
SD_MIN = 15               # minutos: piso del desvío, para que una historia clavada a la misma hora no dé z infinito


def _sin_tildes(txt) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(txt or "")) if unicodedata.category(c) != "Mn").lower()


def _momento(fecha):
    try:
        return datetime.datetime.fromisoformat(str(fecha))
    except ValueError:
        return None


def por_dia(consumos) -> dict:
    """{fecha_iso: {primera, ultima, viajes, kg, ciclo}} de las descargas a los rodeos de ordeñe.

    `primera`/`ultima` en minutos desde la medianoche; una descarga de madrugada (antes de las 04:00) se cuenta en el
    día ANTERIOR, después de las 24:00 (por eso `ultima` puede pasar de 1.440). `ciclo` es la hora prevista de la
    primera comida del día, si el proveedor la informa.
    """
    descargas = (consumos or {}).get("descargas") or []
    hay_etapa = any(d.get("etapa") for d in descargas)
    dias: dict = {}
    for d in descargas:
        if hay_etapa and not _sin_tildes(d.get("etapa")).startswith("orde"):
            continue
        kg = d.get("kg") or 0
        t = _momento(d.get("fecha"))
        if kg <= 0 or t is None:
            continue
        minuto = t.hour * 60 + t.minute
        dia = t.date()
        if minuto < INICIO_JORNADA:
            dia, minuto = dia - datetime.timedelta(days=1), minuto + 24 * 60
        e = dias.setdefault(dia.isoformat(), {"movs": [], "ops": set(), "kg": 0.0})
        e["movs"].append((minuto, d.get("ciclo")))
        e["ops"].add(d.get("operacion") or f"{dia}-{minuto}")
        e["kg"] += kg
    salida = {}
    for f, e in dias.items():
        movs = sorted(e["movs"], key=lambda m: m[0])
        salida[f] = {"primera": movs[0][0], "ultima": movs[-1][0], "viajes": len(e["ops"]), "kg": e["kg"],
                     "ciclo": next((c for _m, c in movs if c), None)}
    return salida


def _fila(fecha, d, med, sd, kg_med, kg_sd):
    if d is None:
        return None
    retraso = d["primera"] - med
    return {"fecha": fecha, **d, "retraso": retraso, "z": round(retraso / sd, 1),
            "kg_z": round((d["kg"] - kg_med) / kg_sd, 1)}


def ultima_comida(consumos):
    """La tarjeta del dashboard: la primera comida del ÚLTIMO día con comida contra lo habitual, o None si no se puede.

    {fecha, primera, habitual, retraso, base_dias}, con `primera`/`habitual` en minutos desde la medianoche y `retraso`
    en minutos (negativo si se comió antes de lo habitual). Se mide contra la propia historia del tambo (mediana de los
    demás días, ver `evaluar`) y NO contra la hora prevista del ciclo (`ciclo`): medido en La Ponderosa, esa etiqueta es
    gruesa (06:30 o 07:30) y daba retrasos de -36 a +39 minutos todos los días, normales o no.

    Sin la etapa («Ordeñe») no se sabe qué descarga es de un rodeo de ordeñe, y contarlas todas daría la hora de la
    recría o de las secas: ahí no se mide, en vez de mostrar un número que parece de ordeñe y no lo es.
    """
    descargas = (consumos or {}).get("descargas") or []
    if not any(d.get("etapa") for d in descargas):
        return None
    dias = por_dia(consumos)
    if not dias:
        return None
    ultimo = max(dias)
    r = evaluar(consumos, datetime.date.fromisoformat(ultimo))
    fila = r and r["dia"]
    if not fila:
        return None
    return {"fecha": ultimo, "primera": fila["primera"], "habitual": r["base"]["primera_med"],
            "retraso": fila["retraso"], "base_dias": r["base"]["dias"]}


HISTORIA_DIAS = 30      # días de comida que se piden para saber qué es «lo habitual» del tambo


def desde_el_proveedor(prov, hoy) -> dict:
    """`{"comida": ultima_comida}` o `{"falta": "por qué no se puede"}`. Nunca levanta: el dashboard no puede caerse
    porque el mixer no responda, y una tarjeta sin número tiene que decir qué falta en vez de quedar en blanco."""
    ok, motivo = prov.disponible()
    if not ok:
        return {"falta": motivo}
    try:
        c = prov.consumos(hoy - datetime.timedelta(days=HISTORIA_DIAS), hoy)
    except Exception as exc:  # noqa: BLE001
        return {"falta": f"No se pudo leer el mixer ({type(exc).__name__}: {exc})"}
    descargas = (c or {}).get("descargas") or []
    if not descargas:
        return {"falta": f"{prov.NOMBRE} no tiene descargas en los últimos {HISTORIA_DIAS} días."}
    if not any(d.get("etapa") for d in descargas):
        return {"falta": f"{prov.NOMBRE} no informa a qué etapa va cada comida: la hora de la primera comida de "
                         f"ordeñe solo se mide con MixerOne."}
    r = ultima_comida(c)
    if r is None:
        return {"falta": f"Todavía no hay historia suficiente (hacen falta al menos {MIN_HISTORIA} días con comida "
                         f"de ordeñe) para saber qué es lo habitual."}
    return {"comida": r}


def _hhmm(minutos) -> str:
    m = int(minutos) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def para_el_tablero(r) -> tuple:
    """(valor, detalle) de la tarjeta a partir de `ultima_comida`.

    El valor es «cuánto se atrasó»: comer antes de lo habitual vale 0 (no es una falla, y un número negativo en un
    semáforo se leería como un dato raro). El detalle nombra el día con su fecha: la tarjeta se mira días después.
    """
    fecha = veredicto_baja.fecha_larga(r["fecha"])
    detalle = f"Primera comida a las {_hhmm(r['primera'])} el {fecha}; lo habitual es {_hhmm(r['habitual'])}"
    return max(0, r["retraso"]), detalle


def evaluar(consumos, dia, base_dias=None):
    """Datos para el veredicto: la comida del día analizado y la de ayer contra la historia, o None si no hay con qué.

    `dia` es la fecha analizada (date). La historia son todos los demás días con comida registrada (hasta `dia`), SIN
    contar el día analizado ni ayer: ninguno de los dos puede ser parte de su propia vara de comparar.
    """
    pd = por_dia(consumos)
    dia_s = dia.isoformat()
    ayer_s = (dia - datetime.timedelta(days=1)).isoformat()
    hist = {f: v for f, v in pd.items() if f not in (dia_s, ayer_s) and f < dia_s}
    if len(hist) < MIN_HISTORIA:
        return None
    primeras = [v["primera"] for v in hist.values()]
    med = st.median(primeras)
    mad = st.median(abs(p - med) for p in primeras)
    sd = max(float(SD_MIN), 1.4826 * mad)
    med_i = int(med)
    kgs = [v["kg"] for v in hist.values()]
    kg_med = st.median(kgs)
    # Los kg del día varían ±15 % entre días normales: el piso del desvío es el 5 % de la mediana.
    kg_sd = max(KG_SD_MIN * kg_med, 1.4826 * st.median(abs(k - kg_med) for k in kgs))
    tarde = [{"fecha": f, "primera": v["primera"]} for f, v in sorted(hist.items())
             if v["primera"] - med_i >= veredicto_baja.ALIM_RETRASO_MIN]
    return {
        "dia": _fila(dia_s, pd.get(dia_s), med_i, sd, kg_med, kg_sd),
        "ayer": _fila(ayer_s, pd.get(ayer_s), med_i, sd, kg_med, kg_sd),
        "base": {"dias": len(hist), "primera_med": med_i, "primera_sd": round(sd, 1),
                 "kg_med": kg_med, "kg_sd": round(kg_sd, 0), "tarde": tarde},
    }
