# -*- coding: utf-8 -*-
"""Operarios del mixer: atribuir cada alerta al operario que la provocó y armar el análisis por operario.

NEUTRAL: no sabe de qué sistema de alimentación vienen los datos (hoy MixerOne). El proveedor traduce lo suyo a estas
formas y este módulo hace la cuenta, así otro sistema de alimentación puede usarlo sin tocar nada de acá.

    movimiento  {viaje, accion: "carga"|"descarga", operario, ingrediente, rodeo, kg_real, kg_teorico}
    alerta      {id, codigo, tipo, severidad, mensaje, fecha, hora, viaje, rodeo, insumo,
                 accion: "carga"|"descarga"|"edicion"|None, operario}
    puntajes    {puntaje, porcentajes: {bien, regular, mal}, operarios: {nombre: {general, cargas, descargas}}}

LA REGLA QUE IMPORTA: cada alerta es del operario de la ACCIÓN que la disparó, no del «operario principal» del viaje.
Un viaje lo cargan y lo descargan personas distintas: en La Ponderosa (03 al 18/11/2026) atribuir por viaje le
adjudicaba 26 alertas de «descarga fuera del corral» a alguien que nunca descargó. Por eso el proveedor dice QUÉ acción
disparó cada alerta (`accion`) y acá se busca quién la hizo en ese viaje:

  * `carga`:    quien cargó ese insumo en el viaje.
  * `descarga`: quien descargó en ese rodeo; si nadie descargó en ese rodeo, el único que descargó en el viaje.
  * `edicion`:  el operario viene explícito en la alerta (alguien modificó los kg de un corral).
  * `None`:     NO se atribuye (stock, variación de consumo de un rodeo, rodeo sin alimentar): son de la gestión del
                tambo, no de una persona, y adjudicarlas al operario del viaje sería inventar un culpable.

Si hay empate (dos operarios con la misma cantidad y ninguno desempata) tampoco se atribuye: mejor «sin atribuir» que
señalar a uno al azar.
"""
import collections


def por_viaje(movimientos) -> dict:
    """{viaje: {"carga": [movimientos], "descarga": [movimientos]}}."""
    salida: dict = {}
    for m in movimientos or []:
        viaje, accion = m.get("viaje"), m.get("accion")
        if not viaje or accion not in ("carga", "descarga") or not m.get("operario"):
            continue
        salida.setdefault(viaje, {"carga": [], "descarga": []})[accion].append(m)
    return salida


def _unico_mas_frecuente(movs):
    """El operario con más movimientos, o None si no hay ninguno o hay empate en el primer puesto."""
    if not movs:
        return None
    c = collections.Counter(m["operario"] for m in movs).most_common(2)
    if len(c) == 2 and c[0][1] == c[1][1]:
        return None
    return c[0][0]


def atribuir(alerta: dict, viajes: dict):
    """El operario de la acción que disparó `alerta`, o None si no se puede (ver el encabezado)."""
    if alerta.get("operario"):
        return alerta["operario"]
    accion = alerta.get("accion")
    if accion not in ("carga", "descarga"):
        return None
    candidatos = (viajes.get(alerta.get("viaje")) or {}).get(accion) or []
    if not candidatos:
        return None
    clave, valor = ("ingrediente", alerta.get("insumo")) if accion == "carga" else ("rodeo", alerta.get("rodeo"))
    coinciden = [m for m in candidatos if valor and (m.get(clave) or "").strip() == str(valor).strip()]
    return _unico_mas_frecuente(coinciden) if coinciden else _unico_mas_frecuente(candidatos)


def _rol(cargas: int, descargas: int):
    total = cargas + descargas
    if not total:
        return None
    if not descargas:
        return "Carga"
    if not cargas:
        return "Descarga"
    return "Carga" if descargas / total < 0.10 else "Descarga" if descargas / total > 0.90 else "Carga y descarga"


def analizar(movimientos, alertas, puntajes=None) -> dict:
    """Un renglón por operario (acciones, desvío de kg, alertas atribuidas por tipo y cada 100 acciones, puntaje del
    sistema), lo que no se pudo atribuir aparte, y cada alerta con su operario."""
    viajes = por_viaje(movimientos)
    filas: dict = {}

    def fila(nombre):
        return filas.setdefault(nombre, {"cargas": 0, "descargas": 0, "kg_real": 0.0, "kg_teo": 0.0, "kg_dif": 0.0,
                                          "por_tipo": collections.Counter()})

    for m in movimientos or []:
        if not m.get("operario"):
            continue
        f = fila(m["operario"])
        if m.get("accion") == "carga":
            f["cargas"] += 1
            teo, real = m.get("kg_teorico"), m.get("kg_real")
            if teo and teo > 0 and real is not None:
                f["kg_teo"] += teo
                f["kg_dif"] += abs(real - teo)
        elif m.get("accion") == "descarga":
            f["descargas"] += 1

    anotadas, sin_tipo, tipos = [], collections.Counter(), {}
    for a in alertas or []:
        quien = atribuir(a, viajes)
        anotadas.append({**a, "operario": quien})
        tipos.setdefault(a.get("codigo"), a.get("tipo"))
        if quien:
            fila(quien)["por_tipo"][a.get("codigo")] += 1
        else:
            sin_tipo[a.get("codigo")] += 1

    ps = (puntajes or {}).get("operarios") or {}
    salida = []
    for nombre, f in filas.items():
        acciones = f["cargas"] + f["descargas"]
        total = sum(f["por_tipo"].values())
        p = ps.get(nombre) or {}
        salida.append({
            "nombre": nombre, "rol": _rol(f["cargas"], f["descargas"]),
            "cargas": f["cargas"], "descargas": f["descargas"],
            "puntaje": p.get("general"), "puntaje_cargas": p.get("cargas"), "puntaje_descargas": p.get("descargas"),
            "desvio_kg_pct": round(100 * f["kg_dif"] / f["kg_teo"], 1) if f["kg_teo"] else None,
            "alertas": total, "alertas_por_tipo": dict(sorted(f["por_tipo"].items())),
            "alertas_cada_100": round(100 * total / acciones, 1) if acciones else None,
        })
    salida.sort(key=lambda o: (-o["alertas"], o["nombre"]))
    atribuidas = sum(1 for a in anotadas if a["operario"])
    return {
        "operarios": salida,
        "sin_operario": {"total": sum(sin_tipo.values()), "por_tipo": dict(sorted(sin_tipo.items()))},
        "alertas": anotadas,
        "tipos": tipos,
        "resumen": {"alertas": len(anotadas), "atribuidas": atribuidas, "sin_atribuir": len(anotadas) - atribuidas,
                    "puntaje": (puntajes or {}).get("puntaje"), "porcentajes": (puntajes or {}).get("porcentajes") or {}},
    }
