# -*- coding: utf-8 -*-
"""Cruce SenseHub (collares Allflex) ↔ DelPro/DDM.

QUÉ RESUELVE. Son dos sistemas que miran las mismas vacas sin conocerse: los
collares miden actividad y rumia; DelPro mide leche, conductividad, células y
lleva los eventos. Para poder decir algo con los dos juntos hacen falta dos
cosas, en este orden y no al revés:

  1. `conciliar()`  — que las vacas sean las mismas. Sin esto lo demás es ruido.
  2. `cruzar_salud()` — qué marca cada sistema y dónde coinciden.

LA IDENTIDAD ES EL 90% DEL PROBLEMA, y falla en silencio. SenseHub devuelve el
RP como TEXTO ("0351", " 351") y DelPro como NÚMERO (351): comparados crudos no
emparejan y el cruce da vacío sin un solo error. Por eso `normalizar_rp()` es la
pieza central y `conciliar()` devuelve SIEMPRE lo que no emparejó de cada lado,
que es lo único que permite darse cuenta.

NO SE COMPARAN LOS DOS ÍNDICES COMO NÚMEROS. Es la trampa de este cruce:

    SenseHub  `healthIndex`   ~0-100, MÁS BAJO es peor (medido: alertadas 28-86)
    LactIA    `score`         0-10,   MÁS ALTO es peor

No están en la misma escala, no miden lo mismo y ninguno es la verdad. Lo único
comparable es el hecho binario de que cada sistema HAYA MARCADO o no a la vaca,
y eso es lo que hace `matriz_acuerdo()`.

EL ACUERDO SE MIDE SOLO SOBRE LAS VACAS QUE LOS DOS PUEDEN VER. Una vaca sin
collar no puede ser marcada por SenseHub, y contarla como "desacuerdo" ensucia
la comparación con un problema de cobertura. `cruzar_salud()` trabaja sobre la
intersección y dice cuántas quedaron afuera.
"""
from __future__ import annotations

import datetime


def normalizar_rp(valor) -> str | None:
    """El RP en una forma comparable entre sistemas.

    SenseHub lo manda como texto y DelPro como entero, y encima aparecen ceros a
    la izquierda y espacios. Se normaliza a texto SIN ceros de más:

        "0351" -> "351"      351 -> "351"      " 351 " -> "351"

    Los RP que no son numéricos se dejan como están (en mayúsculas y sin
    espacios): hay tambos que usan letras y romperlos sería peor que no
    emparejarlos. None/vacío devuelve None: un RP vacío NO es un RP y no tiene
    que emparejar con otro vacío del otro lado.
    """
    if valor is None:
        return None
    txt = str(valor).strip()
    if not txt:
        return None
    if txt.isdigit():
        return str(int(txt))          # saca ceros a la izquierda
    return txt.upper()


def conciliar(animales_sensehub: list, animales_delpro: list) -> dict:
    """Empareja los dos padrones por RP.

    `animales_sensehub`: lo que devuelve `sensehub.parse_animal` ({rp, id, ...}).
    `animales_delpro`:  [{rp, ...}] — de `BasicAnimal.Number`, ya filtrado por
    rebaño (la base DDM la comparten varios tambos: sin filtrar entran vacas de
    otro establecimiento y el cruce "mejora" con animales que no son de acá).

    Devuelve emparejadas y, sobre todo, LO QUE NO EMPAREJÓ de cada lado. Que
    sobren de un lado no es necesariamente un error —una vaquillona sin collar,
    una vaca que se fue— pero es lo que hay que mirar antes de creerle a
    cualquier número posterior.
    """
    idx_sh, dup_sh = _indexar(animales_sensehub)
    idx_dp, dup_dp = _indexar(animales_delpro)

    comunes = sorted(set(idx_sh) & set(idx_dp), key=_orden_rp)
    emparejadas = [{"rp": rp, "sensehub": idx_sh[rp], "delpro": idx_dp[rp]} for rp in comunes]
    solo_sh = [idx_sh[rp] for rp in sorted(set(idx_sh) - set(idx_dp), key=_orden_rp)]
    solo_dp = [idx_dp[rp] for rp in sorted(set(idx_dp) - set(idx_sh), key=_orden_rp)]

    total_sh, total_dp = len(idx_sh), len(idx_dp)
    return {
        "emparejadas": emparejadas,
        "solo_sensehub": solo_sh,
        "solo_delpro": solo_dp,
        # RP repetidos DENTRO de un mismo sistema: en SenseHub el número se
        # reutiliza cuando la vaca original se va, así que un duplicado suele
        # ser una baja vieja y no un error de carga. Se informa, no se corrige.
        "rp_duplicados_sensehub": dup_sh,
        "rp_duplicados_delpro": dup_dp,
        "sin_rp_sensehub": sum(1 for a in animales_sensehub if normalizar_rp(a.get("rp")) is None),
        "sin_rp_delpro": sum(1 for a in animales_delpro if normalizar_rp(a.get("rp")) is None),
        "cobertura_sensehub": round(100 * len(comunes) / total_sh, 1) if total_sh else None,
        "cobertura_delpro": round(100 * len(comunes) / total_dp, 1) if total_dp else None,
    }


def _indexar(animales: list) -> tuple:
    """{rp_normalizado: animal} + la lista de RP repetidos. Ante un repetido
    gana el primero, y el hecho queda informado."""
    idx, dup = {}, {}
    for a in animales or []:
        rp = normalizar_rp(a.get("rp"))
        if rp is None:
            continue
        if rp in idx:
            dup.setdefault(rp, 1)
            dup[rp] += 1
            continue
        idx[rp] = {**a, "rp": rp}
    return idx, [{"rp": rp, "veces": n} for rp, n in sorted(dup.items(), key=_orden_par)]


def _orden_rp(rp: str):
    """Numérico si se puede, alfabético si no — para que 9 vaya antes que 10."""
    return (0, int(rp), "") if rp.isdigit() else (1, 0, rp)


def _orden_par(par):
    return _orden_rp(par[0])


def marcadas_por_sensehub(salud_export: dict | None = None,
                          alertas: list | None = None,
                          umbral_indice: int | None = None) -> dict:
    """Las vacas que SenseHub está señalando, de sus dos canales.

    `salud_export`: lo de `sensehub.exportar_salud()`. OJO: ese canal YA trae
    solo alertadas, así que estar en la lista es la marca. `umbral_indice` es
    opcional y sirve para quedarse con las peores (índice MENOR o igual, porque
    en SenseHub más bajo es peor); None = todas las que vengan.

    `alertas`: lo de `sensehub.alertas()`; se toman solo las de salud
    (`Distress`) y de entidad vaca.

    Devuelve {rp: {indice, motivos:[...], desde}}.
    """
    marcadas: dict = {}
    for v in ((salud_export or {}).get("vacas") or []):
        rp = normalizar_rp(v.get("rp"))
        if rp is None:
            continue
        ind = v.get("indice")
        if umbral_indice is not None and ind is not None and ind > umbral_indice:
            continue
        m = marcadas.setdefault(rp, {"indice": None, "motivos": [], "desde": None})
        m["indice"] = ind
        m["motivos"].append("índice de salud")
        m["desde"] = (salud_export or {}).get("calculado")
    for a in (alertas or []):
        if not a.get("es_salud") or a.get("de") not in (None, "Cow"):
            continue
        rp = normalizar_rp(a.get("rp"))
        if rp is None:
            continue
        m = marcadas.setdefault(rp, {"indice": None, "motivos": [], "desde": None})
        m["motivos"].append(a.get("detalle") or "alerta de salud")
        f = a.get("fecha")
        if f and (m["desde"] is None or f < m["desde"]):
            m["desde"] = f
    return marcadas


def cruzar_salud(conciliacion: dict, marcadas_sh: dict, fichas_lactia: list,
                 umbral_lactia: float = 5.0) -> dict:
    """Qué marca cada sistema, sobre las vacas que LOS DOS pueden ver.

    `fichas_lactia`: lo que devuelve `salud.calcular_atencion_v2` ({rp, score,
    sistema, motivos, ...}). `umbral_lactia`: score a partir del cual se
    considera marcada (0-10, más alto es peor).

    Se trabaja SOLO sobre las emparejadas: una vaca sin collar no puede ser
    marcada por SenseHub y contarla como desacuerdo mide cobertura, no acuerdo.
    """
    emparejadas = {e["rp"]: e for e in (conciliacion.get("emparejadas") or [])}
    lactia = {}
    for f in (fichas_lactia or []):
        rp = normalizar_rp(f.get("rp"))
        if rp is not None:
            lactia[rp] = f

    filas = []
    for rp, e in emparejadas.items():
        sh = marcadas_sh.get(rp)
        lf = lactia.get(rp)
        score = (lf or {}).get("score")
        marcada_lactia = score is not None and score >= umbral_lactia
        filas.append({
            "rp": rp,
            "animal_id": (e.get("sensehub") or {}).get("id"),
            "grupo_sensehub": (e.get("sensehub") or {}).get("grupo"),
            "grupo_delpro": (e.get("delpro") or {}).get("grupo"),
            "sensehub_marca": sh is not None,
            "sensehub_indice": (sh or {}).get("indice"),
            "sensehub_motivos": (sh or {}).get("motivos") or [],
            "sensehub_desde": (sh or {}).get("desde"),
            "lactia_marca": marcada_lactia,
            "lactia_score": score,
            "lactia_sistema": (lf or {}).get("sistema"),
            "lactia_motivos": (lf or {}).get("motivos") or [],
            # Sin ficha de LactIA no se puede decir que "no la marcó": no la
            # evaluó. Se distingue, porque si no un problema de datos se lee
            # como una discrepancia de criterio.
            "lactia_evaluada": lf is not None,
        })
    filas.sort(key=lambda f: (not (f["sensehub_marca"] and f["lactia_marca"]),
                              not f["sensehub_marca"], -(f["lactia_score"] or 0)))

    evaluables = [f for f in filas if f["lactia_evaluada"]]
    ambos = [f for f in evaluables if f["sensehub_marca"] and f["lactia_marca"]]
    solo_sh = [f for f in evaluables if f["sensehub_marca"] and not f["lactia_marca"]]
    solo_la = [f for f in evaluables if not f["sensehub_marca"] and f["lactia_marca"]]
    ninguno = [f for f in evaluables if not f["sensehub_marca"] and not f["lactia_marca"]]

    # Marcadas por SenseHub que ni siquiera están en el padrón emparejado: no
    # son un desacuerdo, son un agujero de conciliación. Van aparte.
    fuera = sorted(set(marcadas_sh) - set(emparejadas), key=_orden_rp)

    return {
        "filas": filas,
        "matriz": matriz_acuerdo(len(ambos), len(solo_sh), len(solo_la), len(ninguno)),
        "sin_evaluar_lactia": len(filas) - len(evaluables),
        "marcadas_sh_fuera_del_padron": fuera,
        "umbral_lactia": umbral_lactia,
    }


def matriz_acuerdo(ambos: int, solo_sensehub: int, solo_lactia: int, ninguno: int) -> dict:
    """La 2×2 de los dos sistemas, con las tres lecturas que importan.

    NO se llama "precisión" a nada: ninguno de los dos es la verdad. Que una
    vaca la marque uno solo NO significa que el otro se equivocó — miden cosas
    distintas (rumia y actividad contra leche, conductividad y células), y es
    esperable que se solapen parcialmente. El número útil es cuántas ve cada uno
    que el otro no ve, para decidir si conviene tener los dos.
    """
    total = ambos + solo_sensehub + solo_lactia + ninguno
    marcadas_sh = ambos + solo_sensehub
    marcadas_la = ambos + solo_lactia
    union = ambos + solo_sensehub + solo_lactia
    return {
        "evaluadas": total,
        "ambos": ambos, "solo_sensehub": solo_sensehub,
        "solo_lactia": solo_lactia, "ninguno": ninguno,
        "marcadas_sensehub": marcadas_sh,
        "marcadas_lactia": marcadas_la,
        # Jaccard: de todas las marcadas por alguno, cuántas marcan los dos.
        "solapamiento_pct": round(100 * ambos / union, 1) if union else None,
        # Cuánto AGREGA cada sistema sobre el otro: es la pregunta de negocio
        # -si SenseHub no agrega vacas nuevas, no justifica su costo, y al revés.
        "aporte_exclusivo_sensehub_pct": round(100 * solo_sensehub / union, 1) if union else None,
        "aporte_exclusivo_lactia_pct": round(100 * solo_lactia / union, 1) if union else None,
    }


def resumen(conciliacion: dict, cruce: dict | None = None) -> list:
    """Frases en criollo para la pantalla: qué salió y qué hay que mirar.
    Devuelve [{"nivel": "ok"|"aviso"|"problema", "texto": ...}]."""
    lineas = []
    n_ok = len(conciliacion.get("emparejadas") or [])
    cob_sh = conciliacion.get("cobertura_sensehub")
    cob_dp = conciliacion.get("cobertura_delpro")
    lineas.append({
        "nivel": "ok" if (cob_sh or 0) >= 95 else "aviso",
        "texto": (f"{n_ok} vacas emparejadas por RP: {cob_sh}% de las que tienen collar "
                  f"y {cob_dp}% de las del rodeo en DelPro."),
    })
    if conciliacion.get("solo_sensehub"):
        lineas.append({"nivel": "aviso", "texto":
            f"{len(conciliacion['solo_sensehub'])} con collar que no están en DelPro. "
            f"Suele ser vaquillonas o vacas que se fueron; si son muchas, el RP no "
            f"está cargado igual en los dos sistemas."})
    if conciliacion.get("solo_delpro"):
        lineas.append({"nivel": "aviso", "texto":
            f"{len(conciliacion['solo_delpro'])} vacas en DelPro sin collar: SenseHub "
            f"no puede decir nada de ellas."})
    for lado in ("sensehub", "delpro"):
        dups = conciliacion.get(f"rp_duplicados_{lado}") or []
        if dups:
            lineas.append({"nivel": "problema", "texto":
                f"{len(dups)} RP repetidos en {lado}: se tomó el primero de cada uno. "
                f"Ejemplos: {', '.join(d['rp'] for d in dups[:5])}."})
    if cruce:
        m = cruce["matriz"]
        if not m["evaluadas"]:
            lineas.append({"nivel": "problema",
                           "texto": "Ninguna vaca quedó evaluada por los dos sistemas."})
            return lineas
        lineas.append({"nivel": "ok", "texto":
            f"De {m['evaluadas']} vacas que ven los dos, SenseHub marca {m['marcadas_sensehub']} "
            f"y LactIA {m['marcadas_lactia']}; coinciden en {m['ambos']} "
            f"({m['solapamiento_pct']}% de las marcadas por alguno)."})
        lineas.append({"nivel": "aviso", "texto":
            f"{m['solo_sensehub']} las ve solo el collar y {m['solo_lactia']} solo DelPro. "
            f"No es que uno se equivoque: miden cosas distintas (rumia y actividad contra "
            f"leche, conductividad y células)."})
    if cruce and cruce.get("marcadas_sh_fuera_del_padron"):
        lineas.append({"nivel": "problema", "texto":
            f"{len(cruce['marcadas_sh_fuera_del_padron'])} vacas alertadas por el collar no "
            f"están en el padrón emparejado — eso es un agujero de conciliación, no un "
            f"desacuerdo entre sistemas."})
    if cruce and cruce.get("sin_evaluar_lactia"):
        lineas.append({"nivel": "aviso", "texto":
            f"{cruce['sin_evaluar_lactia']} emparejadas sin ficha de LactIA: no se pueden "
            f"contar ni a favor ni en contra."})
    return lineas
