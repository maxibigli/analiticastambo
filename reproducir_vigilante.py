# -*- coding: utf-8 -*-
"""Reproduce el vigilante sobre los últimos días: «¿qué habría avisado?».

Sirve para calibrar los umbrales con datos reales ANTES de encender el vigilante. No tiene lógica propia de detección:
recorre los ordeños uno por uno y llama a las MISMAS funciones que la pasada en vivo (`vigilante._evaluar_ordeno`,
`vigilante.base_de`, `vigilante_reglas.*`). SOLO LEE DelPro: no toca `vigilante.db`, la configuración ni los canales.

    python reproducir_vigilante.py [tambo] [--dias 60] [--sin-vacas]

Correrlo en SERVER-DELPRO (la copia local de DDM corta el 30/07/2026). Dice cuántos avisos por semana habría mandado,
cuáles, si el sábado 07/11/2026 (la baja real que motivó el vigilante) habría disparado, y una tabla de sensibilidad
(cuántos avisos por semana saldrían con otros umbrales de baja del ordeño). No reproduce la comida del mixer ni «sin
datos»: esas dos miran el momento de la pasada y se verifican en vivo.

Objetivo de la calibración: **como mucho 1 aviso por semana en condiciones normales Y que el 07/11/2026 sí dispare.**
Si chocan, cede el límite semanal, no la detección: se informa la frecuencia y decide el usuario.
"""
import argparse
import contextlib
import datetime
import statistics as st
import sys
from collections import defaultdict

import tambos
import veredicto_baja
import vigilante
import vigilante_aviso
import vigilante_reglas as reglas

DIA_REFERENCIA = datetime.date(2026, 11, 7)          # sábado: la baja real que motivó el vigilante
GRILLA_Z = (1.5, 2.0, 2.5, 3.0)
GRILLA_PCT = (2.0, 4.0, 6.0, 8.0)
TRAMO_VACAS_DIAS = 10                                # la lectura por vaca se trae de a 10 días: el tope de filas es 100.000


@contextlib.contextmanager
def _umbrales(z: float, pct: float):
    """Cambia un momento los dos umbrales de `baja_ordeno` (para medir z y % de cada ordeño sin filtrar) y los
    devuelve siempre como estaban."""
    z0, p0 = reglas.Z_FUERTE, reglas.BAJA_MIN_PCT
    reglas.Z_FUERTE, reglas.BAJA_MIN_PCT = z, pct
    try:
        yield
    finally:
        reglas.Z_FUERTE, reglas.BAJA_MIN_PCT = z0, p0


def _medidas(o: reglas.Ordeno, base: list) -> dict:
    """kg, media de la base, z y % del ordeño contra ella, sin umbrales (`None` si está por encima de la media o no hay
    base suficiente)."""
    with _umbrales(0.0, 0.0):
        m = reglas.baja_ordeno(o, base)
    if m:
        return {"kg": m["kg"], "media": m["media"], "z": m["z"], "pct": m["pct"], "dias_base": m["dias_base"]}
    media = round(st.mean(b.kg for b in base), 1) if len(base) >= reglas.MIN_BASE else None
    return {"kg": round(o.kg, 1), "media": media, "z": None, "pct": None, "dias_base": len(base)}


def reproducir(tambo: str, ordenos: list, vacas, desde: datetime.date, hasta: datetime.date,
               ahora: datetime.datetime | None = None, dia_ref: datetime.date = DIA_REFERENCIA) -> dict:
    """Evalúa cada ordeño terminado entre `desde` y `hasta` como lo haría la pasada en vivo."""
    ahora = ahora or datetime.datetime.combine(hasta + datetime.timedelta(days=1), datetime.time.min)
    hallazgos, medidas, referencia = [], [], []
    for o in ordenos:
        f = datetime.date.fromisoformat(o.fecha)
        if not (desde <= f <= hasta) or not reglas.ordeno_terminado(o, ahora):
            continue
        hs = vigilante._evaluar_ordeno(tambo, o, ordenos, vacas)
        hallazgos += hs
        m = _medidas(o, vigilante.base_de(o, ordenos))
        medidas.append((o, m))
        if f == dia_ref:
            referencia.append({"sesion": o.sesion, **m, "disparo": bool(hs), "tipos": [h["tipo"] for h in hs]})
    avisos = len({(h["datos"]["fecha"], h["datos"]["sesion"]) for h in hallazgos})
    dias = (hasta - desde).days + 1
    sensibilidad = {}
    for z in GRILLA_Z:
        for p in GRILLA_PCT:
            sensibilidad[(z, p)] = sum(1 for _o, m in medidas
                                       if m["z"] is not None and m["z"] <= -z and m["pct"] <= -p)
    return {"tambo": tambo, "desde": desde, "hasta": hasta, "dias": dias, "evaluados": len(medidas),
            "hallazgos": hallazgos, "avisos": avisos, "por_semana": avisos / dias * 7,
            "referencia": {"fecha": dia_ref, "ordenos": referencia, "disparo": any(r["disparo"] for r in referencia)},
            "sensibilidad": sensibilidad}


def _n(x, dec=0) -> str:
    return vigilante_aviso._n(x, dec)


def formatear(r: dict) -> str:
    L = [f"Reproduccion del vigilante - {r['tambo']} - {r['desde']} a {r['hasta']} "
         f"({r['dias']} dias, {r['evaluados']} ordenos evaluados)",
         "",
         f"Habria mandado {r['avisos']} avisos ({_n(r['por_semana'], 1)} por semana). "
         f"Objetivo: 1 por semana o menos.",
         ""]
    por_orden = sorted(r["hallazgos"], key=lambda h: (h["datos"]["fecha"], h["datos"]["sesion"], h["tipo"]))
    for h in por_orden:
        d = h["datos"]
        cuando = f"{veredicto_baja.fecha_larga(d['fecha'])} ordeno {d['sesion']}"
        if h["tipo"] == "baja_ordeno":
            L.append(f"  {cuando}: baja de {_n(abs(d['pct']), 1)} % ({_n(d['kg'])} kg contra {_n(d['media'])}; "
                     f"z {_n(d['z'], 1)})")
        else:
            L.append(f"  {cuando}: faltaron {d['faltan']} vacas habituales ({_n(d['kg'])} kg, {_n(d['pct'], 1)} %)")
    if not por_orden:
        L.append("  (ningun aviso en el periodo: 0 avisos)")
    ref = r["referencia"]
    L += ["", f"Dia de referencia {veredicto_baja.fecha_larga(ref['fecha'].isoformat())}: "
              + ("DISPARÓ." if ref["disparo"] else "NO disparó.")]
    if not ref["ordenos"]:
        L.append("  (el periodo no incluye ese dia, o no hay ordenos de ese dia en los datos)")
    for x in ref["ordenos"]:
        if x["z"] is None:
            medida = ("sin base suficiente" if x["media"] is None
                      else f"por encima de la media de la base ({_n(x['media'])} kg)")
            if x["media"] is not None and x["kg"] <= x["media"]:
                medida = f"a la par de la base ({_n(x['media'])} kg)"
        else:
            medida = f"{_n(abs(x['pct']), 1)} % bajo la media {_n(x['media'])} kg, z {_n(x['z'], 1)}"
        L.append(f"  ordeno {x['sesion']}: {_n(x['kg'])} kg -> {medida}"
                 + ("  [AVISO]" if x["disparo"] else ""))
    L += ["", "Sensibilidad: avisos por semana por baja del ordeno segun los umbrales "
              "(filas = desvios z, columnas = % bajo la media). Los actuales: "
              f"z {_n(reglas.Z_FUERTE, 1)} y {_n(reglas.BAJA_MIN_PCT, 1)} %.",
          "          " + "".join(f"{_n(p, 0):>8} %" for p in GRILLA_PCT)]
    for z in GRILLA_Z:
        L.append(f"  z {_n(z, 1):>4}  " + "".join(f"{_n(r['sensibilidad'][(z, p)] / r['dias'] * 7, 1):>10}"
                                                  for p in GRILLA_PCT))
    L += ["", "No reproduce la comida del mixer ni «sin datos» (miran el momento de la pasada): se verifican en vivo."]
    return "\n".join(L)


def leer_argumentos(argv=None):
    ap = argparse.ArgumentParser(description="Reproduce el vigilante sobre los últimos días (solo lectura).")
    ap.add_argument("tambo", nargs="?", default=tambos.DEFAULT_TAMBO)
    ap.add_argument("--dias", type=int, default=60, help="cuántos días hacia atrás evaluar (por defecto 60)")
    ap.add_argument("--hasta", type=datetime.date.fromisoformat, default=None, help="último día (AAAA-MM-DD; hoy)")
    ap.add_argument("--sin-vacas", action="store_true",
                    help="no revisa las vacas que faltaron (mucho más rápido: la lectura por vaca es lo pesado)")
    return ap.parse_args(argv)


def _vacas_en_tramos(tambo: str, d0: datetime.date, d1: datetime.date):
    """La lectura por vaca de todo el período, traída de a TRAMO_VACAS_DIAS días (el tope de filas es de 100.000), y
    una función con la misma forma que `vigilante.leer_vacas` que la sirve desde memoria."""
    por_dia = defaultdict(list)
    cur = d0
    while cur <= d1:
        fin = min(cur + datetime.timedelta(days=TRAMO_VACAS_DIAS - 1), d1)
        print(f"  leyendo vacas {cur} a {fin}...", flush=True)
        for fila in vigilante.leer_vacas(tambo, cur.isoformat(), fin.isoformat()):
            por_dia[fila[0]].append(fila)
        cur = fin + datetime.timedelta(days=1)

    def servir(_tambo, a, b):
        f, out = datetime.date.fromisoformat(a), []
        while f <= datetime.date.fromisoformat(b):
            out += por_dia.get(f.isoformat(), [])
            f += datetime.timedelta(days=1)
        return out
    return servir


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    a = leer_argumentos(argv)
    hasta = a.hasta or datetime.date.today()
    desde = hasta - datetime.timedelta(days=a.dias - 1)
    print(f"Leyendo ordenos de {a.tambo} de {desde - datetime.timedelta(days=vigilante.DIAS_LECTURA)} a {hasta}...",
          flush=True)
    ordenos = vigilante.leer_ordenos(a.tambo, desde - datetime.timedelta(days=vigilante.DIAS_LECTURA), hasta)
    if a.sin_vacas:
        vacas = lambda t, d0, d1: []   # noqa: E731
    else:
        vacas = _vacas_en_tramos(a.tambo, desde - datetime.timedelta(days=vigilante.DIAS_LECTURA), hasta)
    r = reproducir(a.tambo, ordenos, vacas, desde, hasta, ahora=datetime.datetime.now())
    print()
    print(formatear(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
