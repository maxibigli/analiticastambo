# -*- coding: utf-8 -*-
"""Informe de células somáticas (RCS) de un período, con su impacto en leche y en plata.

POR QUÉ EXISTE. La pestaña "Análisis de RCS" calcula todo en el navegador
(`rcspAnalizar`, `rcspEfectoDentro`, `rcsdContar` en index.html) a partir de dos
listas de agregados que manda el servidor (`/api/salud/rcs_historico` y
`/api/salud/rcs_dinamica`). El agente de IA no tiene navegador: para poder
contestar "cómo anduvieron las células somáticas estos tres meses y qué impacto
económico tuvieron" necesita esos mismos números armados del lado del servidor.

ES UN PORTE, NO UN CRITERIO NUEVO. Cada función de acá hace lo mismo que su
gemela de JavaScript, con las mismas decisiones (las explica CLAUDE.md, sección
"Salud del rodeo: pestaña Análisis de RCS"):

  * La leche que "se pierde" es la diferencia entre lo que produjeron las vacas
    con RCS > 300.000 y lo que producen vacas SANAS comparables: misma lactancia,
    mismo tramo de DEL y, si hay al menos `MIN_REF` sanas, el mismo RODEO DEL DÍA
    del control (los rodeos se arman por nivel de producción: contra el promedio
    de todo el tambo, un rodeo de baja producción "perdería" leche sin que el RCS
    tuviera nada que ver).
  * "Sana" es RCS hasta la banda `ref_max` (2 = hasta 200.000, la más conservadora).
  * El costo de una infección nueva se mide DENTRO de la misma vaca (de un control
    al siguiente) contra las que siguen sanas, estandarizado por tramo de DEL.
  * La pérdida por rodeo se divide por los controles del PERÍODO, no por los que
    tuvo ese rodeo: así la suma de los rodeos da la del tambo.

LA PLATA. Es el único número que no sale de la base: se multiplica la leche
perdida por el precio de la leche que carga el tambo en la planilla de precios
(`precios_alimentos`, efectivo por litro). Reglas, las mismas que el resto de la
app (ver CLAUDE.md, "no inventar números"):
  * Sin precio en la planilla NO hay pesos: se devuelve `falta` diciendo qué cargar.
    Nunca un precio por defecto.
  * Es SOLO la leche que no se produjo. NO incluye bonificaciones ni castigos de
    calidad del comprador por RCS (no están en ningún sistema conectado) ni el
    costo de tratamientos o descarte: el informe lo dice, porque sin ese aviso el
    número parecería el costo total del RCS.
  * Los kg del control se toman como litros, igual que `rentabilidad.py`.
  * La pérdida diaria es el promedio POR CONTROL (un día por mes por vaca) y se
    extrapola solo a los días que cubren los controles (`dias_cubiertos`), no a todo
    el período pedido: si el último control es viejo, extrapolar hasta hoy inventaría
    meses de pérdida sin ningún dato detrás. Es una estimación, y está rotulada así.
"""
import datetime

MIN_REF = 10            # vacas sanas mínimas en una celda para usarla como referencia
MIN_MUESTRAS = 30       # por debajo de esto un promedio o una tasa es poco confiable
MIN_PUNTO = 10          # vacas mínimas para mostrar el promedio de un rodeo en un control
BANDA_ALTA = 4          # banda de RCS desde la que una vaca es "alta" (> 300.000)
DIAS_DEFECTO = 90


def _en(f, desde, hasta):
    return desde <= f["fecha"] <= hasta


# --- Pérdida de leche: vacas altas contra vacas sanas comparables ------------

def referencia(filas, desde, hasta, ref_max):
    """kg medio de las vacas sanas de cada (rodeo, lactancia, DEL) y de cada
    (lactancia, DEL) sin separar por rodeo."""
    por_rodeo, glob = {}, {}

    def sumar(o, k, f):
        a = o.setdefault(k, [0, 0.0])
        a[0] += f["n_kg"]
        a[1] += f["suma_kg"]

    for f in filas:
        if not _en(f, desde, hasta) or f["bscc"] > ref_max or not f.get("n_kg"):
            continue
        sumar(por_rodeo, (f["grupo"], f["lact"], f["banda"]), f)
        sumar(glob, (f["lact"], f["banda"]), f)
    return {"por_rodeo": por_rodeo, "global": glob}


def esperado(ref, f):
    """kg esperados por vaca de una fila, o None si no hay con qué comparar."""
    r = ref["por_rodeo"].get((f["grupo"], f["lact"], f["banda"]))
    if r and r[0] >= MIN_REF:
        return r[1] / r[0]
    g = ref["global"].get((f["lact"], f["banda"]))
    return g[1] / g[0] if g and g[0] >= MIN_REF else None


def analizar(filas, desde, hasta, ref, rodeo=None):
    """Todo lo del período, para el tambo entero (rodeo=None) o un rodeo."""
    por_banda = {}
    n_tot = kg_tot = n_altas = perd_altas = 0
    fechas = set()
    for f in filas:
        if not _en(f, desde, hasta) or not f.get("n_kg"):
            continue
        if rodeo is not None and f["grupo"] != rodeo:
            continue
        fechas.add(f["fecha"])
        esp = esperado(ref, f)
        if esp is None:
            continue   # sin vacas sanas comparables: no se puede estimar
        b = por_banda.setdefault(f["bscc"], {"n": 0, "kg": 0.0, "esp": 0.0})
        b["n"] += f["n_kg"]
        b["kg"] += f["suma_kg"]
        b["esp"] += f["n_kg"] * esp
        n_tot += f["n_kg"]
        kg_tot += f["suma_kg"]
        if f["bscc"] >= BANDA_ALTA:
            n_altas += f["n_kg"]
            perd_altas += f["n_kg"] * esp - f["suma_kg"]
    return {"por_banda": por_banda, "n_tot": n_tot, "kg_tot": kg_tot, "n_altas": n_altas,
            "perd_altas": perd_altas, "controles": len(fechas), "fechas": sorted(fechas)}


def dias_cubiertos(fechas, dias_periodo):
    """A cuántos días del período representan los controles. Cada control mide UN día
    y vale por el intervalo hasta el siguiente; el último vale por el intervalo medio.
    Extrapolar la pérdida a todo el período pedido es un error cuando el período se
    extiende más allá del último control (medido: con un último control de hace cuatro
    meses, 191 días de pérdida salían de 3 controles que cubrían unos 80)."""
    if not fechas:
        return 0
    if len(fechas) == 1:
        return min(dias_periodo, 30)
    f0, f1 = _fecha(fechas[0]), _fecha(fechas[-1])
    span = (f1 - f0).days
    return min(dias_periodo, round(span + span / (len(fechas) - 1)))


def efecto_dentro(din_filas, desde, hasta, rodeo=None):
    """Cambio de producción (kg/día por vaca) al pasar de sana a alta, y de alta
    a sana, contra las que no cambiaron de estado, por tramo de DEL."""
    ss, nu, cu, cr = {}, {}, {}, {}

    def suma(o, b, f):
        a = o.setdefault(b, [0, 0.0])
        a[0] += f["n_dkg"]
        a[1] += f["dkg"]

    for f in din_filas:
        if f["tipo"] != "intra" or not _en(f, desde, hasta) or not f.get("n_dkg"):
            continue
        if rodeo is not None and f["grupo"] != rodeo:
            continue
        if not f["prev_alta"]:
            suma(ss if not f["curr_alta"] else nu, f["banda"], f)
        else:
            suma(cu if not f["curr_alta"] else cr, f["banda"], f)

    def efecto(caso, ref):
        num = den = 0.0
        for b, c in caso.items():
            r = ref.get(b)
            if c[0] and r and r[0]:
                num += c[0] * (c[1] / c[0] - r[1] / r[0])
                den += c[0]
        return {"kg": num / den, "n": den} if den else None

    return {"nueva": efecto(nu, ss), "curada": efecto(cu, cr)}


# --- Dinámica entre controles ------------------------------------------------

def contar_dinamica(din_filas, desde, hasta, tipo="intra"):
    c = {"ss": 0, "nueva": 0, "curada": 0, "cronica": 0}
    for f in din_filas:
        if f["tipo"] != tipo or not _en(f, desde, hasta):
            continue
        if not f["prev_alta"] and not f["curr_alta"]:
            c["ss"] += f["vacas"]
        elif not f["prev_alta"] and f["curr_alta"]:
            c["nueva"] += f["vacas"]
        elif f["prev_alta"] and not f["curr_alta"]:
            c["curada"] += f["vacas"]
        else:
            c["cronica"] += f["vacas"]
    sanas, altas = c["ss"] + c["nueva"], c["curada"] + c["cronica"]
    tot = sanas + altas
    return {**c, "sanas": sanas, "altas": altas, "tot": tot,
            "nueva_pct": 100 * c["nueva"] / sanas if sanas else None,
            "cura_pct": 100 * c["curada"] / altas if altas else None,
            "cron_pct": 100 * c["cronica"] / tot if tot else None}


# --- Armado del informe ------------------------------------------------------

def _r(v, d=1):
    return None if v is None else round(v, d)


def _fecha(s):
    return datetime.date.fromisoformat(s)


def _evolucion(filas, desde, hasta):
    """Una fila por control lechero (fecha), todo el tambo."""
    por = {}
    for f in filas:
        if not _en(f, desde, hasta):
            continue
        a = por.setdefault(f["fecha"], {"vacas": 0, "altas": 0, "suma_scc": 0.0})
        a["vacas"] += f["vacas"]
        a["altas"] += f["altas"]
        a["suma_scc"] += f["suma_scc"]
    return [{"fecha": fe, "vacas": a["vacas"], "pct_altas": _r(100 * a["altas"] / a["vacas"]) if a["vacas"] else None,
             "rcs_promedio": round(a["suma_scc"] / a["vacas"]) if a["vacas"] else None}
            for fe, a in sorted(por.items())]


def _resumen_periodo(filas, din, desde, hasta, ref_max):
    """Cifras clave de un período (también se usa para el período anterior)."""
    ref = referencia(filas, desde, hasta, ref_max)
    a = analizar(filas, desde, hasta, ref)
    ev = efecto_dentro(din, desde, hasta)
    d = contar_dinamica(din, desde, hasta, "intra")
    vacas = sum(f["vacas"] for f in filas if _en(f, desde, hasta))
    altas = sum(f["altas"] for f in filas if _en(f, desde, hasta))
    suma_scc = sum(f["suma_scc"] for f in filas if _en(f, desde, hasta))
    perd_dia = a["perd_altas"] / a["controles"] if a["controles"] else None
    return {
        "analisis": a, "efecto": ev, "dinamica": d,
        "controles": a["controles"],
        "vacas_controladas": vacas,
        "pct_altas": _r(100 * altas / vacas) if vacas else None,
        "rcs_promedio": round(suma_scc / vacas) if vacas else None,
        "perdida_kg_dia_tambo": _r(perd_dia, 0) if perd_dia is not None else None,
        "perdida_kg_dia_por_vaca_alta": _r(a["perd_altas"] / a["n_altas"]) if a["n_altas"] else None,
        "pct_produccion_perdida": _r(100 * a["perd_altas"] / a["kg_tot"]) if a["kg_tot"] else None,
        "infeccion_nueva_kg_dia": _r(-ev["nueva"]["kg"]) if ev["nueva"] else None,
        "infeccion_nueva_casos": int(ev["nueva"]["n"]) if ev["nueva"] else None,
        "infecciones_nuevas_pct": _r(d["nueva_pct"]),
        "curacion_pct": _r(d["cura_pct"]),
        "cronicas_pct": _r(d["cron_pct"]),
        "_perd_dia": perd_dia,
    }


def _origen_precio(info):
    """De dónde sale el precio, en una frase que se pueda citar."""
    info = info or {}
    if info.get("via") == "usd" and info.get("usd_litro") and info.get("tipo_cambio"):
        usd = f"{info['usd_litro']:g}".replace(".", ",")
        tc = f"{info['tipo_cambio']:,.0f}".replace(",", ".")
        return f"planilla de precios del tambo: USD {usd} por litro × tipo de cambio ${tc}"
    if info.get("via") == "manual":
        return "planilla de precios del tambo: precio manual en $/litro"
    return "planilla de precios del tambo"


def _impacto_economico(perd_kg_dia, dias, precio, info=None, cubiertos=None):
    """`dias` = días del período pedido; `cubiertos` = días que representan los controles
    (ver `dias_cubiertos`). La pérdida del período se extrapola SOLO a los cubiertos."""
    if perd_kg_dia is None:
        return {"falta": "No hay controles con producción en el período: no se puede estimar la leche perdida."}
    cubiertos = dias if cubiertos is None else cubiertos
    if not precio:
        return {"falta": ("Falta el precio de la leche en la planilla de precios (Precios_alimentos.xlsx): sin él "
                          "se informa la leche perdida en kg pero no su valor en pesos."),
                "leche_perdida_kg_dia": round(perd_kg_dia), "dias_que_cubren_los_controles": cubiertos,
                "leche_perdida_kg_periodo": round(perd_kg_dia * cubiertos)}
    estimado = bool((info or {}).get("estimado"))
    res = {
        "precio_litro": round(precio, 2),
        "precio_origen": _origen_precio(info),
        "precio_estimado": estimado,
        "leche_perdida_kg_dia": round(perd_kg_dia),
        "dias_que_cubren_los_controles": cubiertos,
        "leche_perdida_kg_periodo": round(perd_kg_dia * cubiertos),
        "perdida_pesos_dia": round(perd_kg_dia * precio),
        "perdida_pesos_periodo": round(perd_kg_dia * cubiertos * precio),
        "dias_del_periodo_pedido": dias,
        "no_incluye": ["bonificaciones o castigos de calidad del comprador por RCS (no están en ningún sistema conectado)",
                       "tratamientos, descarte de leche ni vacas eliminadas por mastitis"],
        "supuestos": ["1 kg de leche = 1 litro (como en Rentabilidad)",
                      f"la pérdida diaria es el promedio por control (un día por vaca por mes) y se extrapola solo a los "
                      f"{cubiertos} días que cubren los controles del período, no a los {dias} días pedidos: es una "
                      f"estimación, no una medición diaria"],
    }
    if estimado:
        # Va primero en el diccionario de lo que se lee: es lo que más cambia cómo se interpreta el número.
        res = {"AVISO_PRECIO_ESTIMADO": (
            "El precio de la leche de la planilla está marcado como ESTIMADO: es un valor de ejemplo que cargó el "
            "sistema, NO el precio real del tambo. Los pesos de este informe son ilustrativos. Para que sean los "
            "reales, cargar en Precios_alimentos.xlsx el precio EFECTIVO por litro (lo cobrado ÷ litros entregados) y "
            "borrar la nota «ESTIMADO» de esa fila. Decilo de forma destacada, junto a cada cifra en pesos."), **res}
    return res


def armar(filas, din, desde=None, hasta=None, ref_max=2, precio_litro=None, hoy=None, precio_info=None):
    """Informe del período [desde, hasta] (ISO). Por defecto, los últimos 90 días.
    `precio_info` = {"estimado", "via", "usd_litro", "tipo_cambio"} de la planilla de precios."""
    hoy = hoy or datetime.date.today()
    hasta = hasta or hoy.isoformat()
    desde = desde or (_fecha(hasta) - datetime.timedelta(days=DIAS_DEFECTO)).isoformat()
    if desde > hasta:
        desde, hasta = hasta, desde
    dias = (_fecha(hasta) - _fecha(desde)).days + 1

    # Período anterior de igual duración, para comparar.
    h0 = (_fecha(desde) - datetime.timedelta(days=1)).isoformat()
    d0 = (_fecha(desde) - datetime.timedelta(days=dias)).isoformat()

    A = _resumen_periodo(filas, din, desde, hasta, ref_max)
    if not A["controles"]:
        return {"periodo": {"desde": desde, "hasta": hasta, "dias": dias},
                "sin_datos": ("No hay controles lecheros con producción entre esas fechas. Los controles son "
                              "mensuales: probá con un rango de al menos 2 meses.")}
    B = _resumen_periodo(filas, din, d0, h0, ref_max)
    hay_b = bool(B["controles"])

    a = A["analisis"]
    ref = referencia(filas, desde, hasta, ref_max)

    # Por rodeo: la pérdida se divide por los controles del período entero.
    rodeos = sorted({(f["numero"], f["grupo"]) for f in filas if _en(f, desde, hasta)})
    por_rodeo = []
    for _num, nombre in rodeos:
        x = analizar(filas, desde, hasta, ref, nombre)
        if not x["n_tot"]:
            continue
        perd_dia = x["perd_altas"] / a["controles"] if a["controles"] else None
        por_rodeo.append({
            "rodeo": nombre, "vacas": x["n_tot"], "vacas_altas": x["n_altas"],
            "pct_altas": _r(100 * x["n_altas"] / x["n_tot"]),
            "perdida_kg_dia_por_vaca_alta": _r(x["perd_altas"] / x["n_altas"]) if x["n_altas"] else None,
            "perdida_kg_dia": _r(perd_dia, 0) if perd_dia is not None else None,
            "pct_produccion_perdida": _r(100 * x["perd_altas"] / x["kg_tot"]) if x["kg_tot"] else None,
            "poco_confiable": x["n_altas"] < MIN_MUESTRAS,
        })

    nombres_banda = {1: "hasta 100.000", 2: "101.000 a 200.000", 3: "201.000 a 300.000",
                     4: "301.000 a 500.000", 5: "501.000 a 1.000.000", 6: "más de 1.000.000"}
    por_banda = []
    for b in sorted(a["por_banda"]):
        v = a["por_banda"][b]
        if not v["n"]:
            continue
        por_banda.append({
            "rcs_de_la_vaca": nombres_banda.get(b, str(b)), "vacas": v["n"],
            "pct_de_las_vacas": _r(100 * v["n"] / a["n_tot"]),
            "kg_dia_reales": _r(v["kg"] / v["n"]), "kg_dia_esperados": _r(v["esp"] / v["n"]),
            "diferencia_kg_dia": _r((v["kg"] - v["esp"]) / v["n"]),
            "es_referencia": b <= ref_max,
        })

    d = A["dinamica"]
    sec = contar_dinamica(din, desde, hasta, "seco")

    def _cmp(clave):
        return B[clave] if hay_b else None

    informe = {
        "periodo": {"desde": desde, "hasta": hasta, "dias": dias, "controles": A["controles"],
                    "referencia_sana": f"vacas con RCS {'hasta 100.000' if ref_max == 1 else 'hasta 200.000'}"},
        "resumen": {
            "vacas_controladas": A["vacas_controladas"],
            "pct_vacas_con_rcs_alto": A["pct_altas"],
            "rcs_promedio": A["rcs_promedio"],
            "leche_que_producen_de_menos_por_vaca_alta_kg_dia": A["perdida_kg_dia_por_vaca_alta"],
            "leche_perdida_tambo_kg_dia": A["perdida_kg_dia_tambo"],
            "pct_de_la_produccion_perdida": A["pct_produccion_perdida"],
            "costo_de_una_infeccion_nueva_kg_dia": A["infeccion_nueva_kg_dia"],
            "casos_infeccion_nueva": A["infeccion_nueva_casos"],
        },
        "periodo_anterior": ({
            "desde": d0, "hasta": h0, "controles": B["controles"],
            "pct_vacas_con_rcs_alto": _cmp("pct_altas"), "rcs_promedio": _cmp("rcs_promedio"),
            "leche_perdida_tambo_kg_dia": _cmp("perdida_kg_dia_tambo"),
            "pct_de_la_produccion_perdida": _cmp("pct_produccion_perdida"),
            "infecciones_nuevas_pct": _cmp("infecciones_nuevas_pct"),
            "curacion_pct": _cmp("curacion_pct"), "cronicas_pct": _cmp("cronicas_pct"),
        } if hay_b else None),
        "evolucion_por_control": _evolucion(filas, desde, hasta),
        "por_rodeo": por_rodeo,
        "produccion_segun_rcs": por_banda,
        "dinamica_dentro_de_la_lactancia": {
            "infecciones_nuevas_pct": A["infecciones_nuevas_pct"], "curacion_pct": A["curacion_pct"],
            "cronicas_pct": A["cronicas_pct"], "base_sanas": d["sanas"], "base_altas": d["altas"],
            "explicacion": ("infecciones nuevas = % de vacas sanas en el control anterior que pasaron a alta; "
                            "curación = % de las altas que pasaron a sanas; crónicas = % de las controladas altas "
                            "en los dos controles"),
        },
        "durante_el_seco": {
            "curacion_pct": _r(sec["cura_pct"]), "infecciones_nuevas_al_parir_pct": _r(sec["nueva_pct"]),
            "base_altas": sec["altas"], "base_sanas": sec["sanas"],
        },
        "impacto_economico": _impacto_economico(A["_perd_dia"], dias, precio_litro, precio_info,
                                                dias_cubiertos(a["fechas"], dias)),
        "avisos": [],
    }
    atraso = (_fecha(hasta) - _fecha(a["fechas"][-1])).days
    if atraso > 45:
        informe["avisos"].append(
            f"El último control lechero del período es del {a['fechas'][-1]}, {atraso} días antes de la fecha final "
            f"pedida ({hasta}): no hay controles más recientes cargados, y todas las cifras son de ese tramo.")
    if a["n_altas"] < MIN_MUESTRAS:
        informe["avisos"].append(f"Pocas vacas altas en el período ({a['n_altas']}): la estimación de leche perdida es poco confiable.")
    if A["controles"] < 2:
        informe["avisos"].append("Hay un solo control lechero en el período: no se puede ver una tendencia, solo una foto.")
    if not hay_b:
        informe["avisos"].append("No hay controles en el período anterior de igual duración: no hay con qué comparar.")
    return informe
