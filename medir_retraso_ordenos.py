"""Mide, SOLO LECTURA, con qué retraso aparecen en DelPro las filas de cada ordeño.

Es la primera tarea del Vigilante: el aviso "apenas termina el ordeño" solo tiene sentido si la tabla por
ordeño (`SessionMilkYield`) se llena casi en tiempo real. En la base se ve el `EndTime` de cada fila, pero
NO cuándo entró a la base: eso solo se mide mirando cada pocos minutos durante un día y anotando cuándo
apareció cada avance. Corre donde la app llega a DelPro (SERVER-DELPRO). No imprime credenciales.

    python medir_retraso_ordenos.py                  # registra cada 300 s (Ctrl+C para cortar)
    python medir_retraso_ordenos.py ponderosa --cada 120
    python medir_retraso_ordenos.py --analizar       # lee el CSV y resume

Dejarlo correr AL MENOS 24 HORAS SEGUIDAS, de modo que incluya el ordeño de la noche. Registra una línea
por consulta en `retraso_ordenos.csv` (cuándo se miró, el último `EndTime` visto, cuántas filas y cuántos
segundos tardó la consulta). Con `--analizar` imprime, por ordeño, a qué hora terminó, cuándo se vio y
cuánto tardó en aparecer, más el máximo, la mediana y el tiempo medio de la consulta.

Límites de la medición: la precisión es la del intervalo entre consultas (con 300 s, hasta 5 minutos de
más por ordeño). Lo que ya estaba en la base cuando se empezó a medir no se puede medir (es la línea de
base). Mira solo el rebaño del tambo, igual que el diagnóstico de baja: la base la comparten tres tambos.
"""
import argparse
import csv
import datetime
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUTA_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retraso_ordenos.csv")
CADA_S = 300
QUIETO_MIN = 45          # dos avances de `EndTime` a menos de esto pertenecen al mismo ordeño
CAMPOS = ("cuando", "max_fin", "filas", "segundos")
LIMITE_SEGUIR_MIN = 60   # hasta acá el diseño sigue tal cual
LIMITE_HORAS_MIN = 180   # orientativo: pasado esto es "horas" y hay que detenerse y hablar con el usuario
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


# --------------------------------------------------------------------------- cálculo (puro)
def calcular_retrasos(muestras: list) -> list:
    """Un renglón por ordeño: `{"fin", "visto", "retraso_min"}`.

    El primer `max_fin` es la línea de base (ya estaba cuando se empezó a mirar): no se puede medir. Cada
    avance posterior es una fila nueva; los avances separados por menos de `QUIETO_MIN` son el mismo ordeño y
    cuenta el último (el que cierra el ordeño), porque es el que decide cuándo se puede avisar."""
    grupos = []
    anterior = None
    for s in sorted(muestras, key=lambda x: x["cuando"]):
        fin = s["max_fin"]
        if fin is None:
            continue
        if anterior is None:
            anterior = fin
            continue
        if fin <= anterior:
            continue
        ultimo = grupos[-1] if grupos else None
        if ultimo and (fin - ultimo["fin"]) <= datetime.timedelta(minutes=QUIETO_MIN):
            ultimo["fin"], ultimo["visto"] = fin, s["cuando"]
        else:
            grupos.append({"fin": fin, "visto": s["cuando"]})
        anterior = fin
    for g in grupos:
        g["retraso_min"] = (g["visto"] - g["fin"]).total_seconds() / 60
    return grupos


def filas_sin_fin_nuevo(muestras: list) -> int:
    """Cuántas veces entraron filas pero el `EndTime` máximo no avanzó: filas viejas que llegan tarde, o un
    ordeño que llega partido. Un número alto es una señal de que "avisar apenas termina" no es confiable."""
    n = 0
    fin_previo = filas_previas = None
    for s in sorted(muestras, key=lambda x: x["cuando"]):
        if s["max_fin"] is None:
            continue
        if fin_previo is not None and s["filas"] > filas_previas and s["max_fin"] <= fin_previo:
            n += 1
        fin_previo, filas_previas = s["max_fin"], s["filas"]
    return n


def resumir(retrasos: list, muestras: list) -> dict:
    mins = [r["retraso_min"] for r in retrasos]
    segs = [s["segundos"] for s in muestras]
    return {"ordenos": len(mins),
            "maximo_min": max(mins) if mins else None,
            "mediana_min": st.median(mins) if mins else None,
            "consulta_media_s": round(st.mean(segs), 2) if segs else None}


# --------------------------------------------------------------------------- CSV
def agregar_muestra(ruta: str, s: dict) -> None:
    nuevo = not os.path.exists(ruta)
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(CAMPOS)
        w.writerow([s["cuando"].isoformat(), s["max_fin"].isoformat() if s["max_fin"] else "",
                    s["filas"], s["segundos"]])


def leer_muestras(ruta: str) -> list:
    with open(ruta, newline="", encoding="utf-8") as f:
        return [{"cuando": datetime.datetime.fromisoformat(r["cuando"]),
                 "max_fin": datetime.datetime.fromisoformat(r["max_fin"]) if r["max_fin"] else None,
                 "filas": int(r["filas"]), "segundos": float(r["segundos"])}
                for r in csv.DictReader(f)]


# --------------------------------------------------------------------------- lectura de la base
def consultar(tambo: str) -> dict:
    """Una muestra: el último `EndTime` y la cantidad de filas del rebaño desde la medianoche de ayer."""
    import db
    import diagnostico_baja
    desde = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    sql = f"""
    SELECT MAX(sm.EndTime) AS max_fin, COUNT(*) AS filas
    FROM SessionMilkYield sm
    JOIN AnimalDaily ad ON ad.OID = sm.AnimalDaily AND ad.GCRecord IS NULL
    WHERE sm.BeginTime >= '{desde}' AND sm.TotalYield > 0 AND {diagnostico_baja._del_rebano(tambo)}
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """
    t0 = time.monotonic()
    fila = db.run_query(sql, tambo=tambo)["rows"][0]
    seg = round(time.monotonic() - t0, 2)
    fin = fila[0]
    if fin is not None and not isinstance(fin, datetime.datetime):
        fin = datetime.datetime.fromisoformat(str(fin))
    return {"cuando": datetime.datetime.now().replace(microsecond=0), "max_fin": fin,
            "filas": int(fila[1] or 0), "segundos": seg}


def registrar(tambo: str, cada: int, ruta: str = RUTA_CSV) -> None:
    print(f"Registrando en {ruta} cada {cada} s (Ctrl+C para cortar). Dejalo al menos 24 horas seguidas.")
    while True:
        try:
            s = consultar(tambo)
            agregar_muestra(ruta, s)
            print(f"{s['cuando']:%d/%m %H:%M}  último fin {s['max_fin']}  filas {s['filas']}  consulta {s['segundos']} s",
                  flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - una consulta fallida no debe cortar una medición de un día
            print(f"{datetime.datetime.now():%d/%m %H:%M}  no se pudo consultar: {type(exc).__name__}: {str(exc)[:160]}",
                  file=sys.stderr, flush=True)
        time.sleep(cada)


# --------------------------------------------------------------------------- informe
def _fecha(dt: datetime.datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt:%d/%m/%Y %H:%M}"


def analizar(ruta: str = RUTA_CSV) -> None:
    muestras = leer_muestras(ruta)
    if len(muestras) < 2:
        print("Todavía no hay muestras suficientes.")
        return
    retrasos = calcular_retrasos(muestras)
    horas = (muestras[-1]["cuando"] - muestras[0]["cuando"]).total_seconds() / 3600
    print(f"{len(muestras)} consultas en {horas:.1f} horas (de {_fecha(muestras[0]['cuando'])} "
          f"a {_fecha(muestras[-1]['cuando'])}).")
    if horas < 24:
        print("ATENCION: menos de 24 horas, puede faltar el ordeño de la noche. Seguí midiendo antes de decidir.")
    for r in retrasos:
        print(f"  terminó {_fecha(r['fin'])} -> visto {_fecha(r['visto'])}: {r['retraso_min']:.0f} min")
    res = resumir(retrasos, muestras)
    if not retrasos:
        print("No se vio ningún avance de ordeño: no hay nada que medir todavía.")
        return
    print(f"Ordeños medidos: {res['ordenos']}. Retraso máximo {res['maximo_min']:.0f} min, "
          f"mediana {res['mediana_min']:.0f} min. Consulta: {res['consulta_media_s']} s en promedio.")
    tardias = filas_sin_fin_nuevo(muestras)
    print(f"Veces que entraron filas sin que avanzara el último fin (llegadas tardías o ordeños partidos): {tardias}.")
    if res["maximo_min"] <= LIMITE_SEGUIR_MIN:
        print(f"Lectura: hasta {LIMITE_SEGUIR_MIN} min. El diseño sigue tal cual.")
    elif res["maximo_min"] <= LIMITE_HORAS_MIN:
        print(f"Lectura: entre {LIMITE_SEGUIR_MIN} min y unas horas. Los avisos van marcados «con los datos al momento».")
    else:
        print("Lectura: retraso de HORAS. Detenerse y volver a hablar con el usuario antes de seguir.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tambo", nargs="?", default=None)
    p.add_argument("--cada", type=int, default=CADA_S, help="segundos entre consultas (300)")
    p.add_argument("--analizar", action="store_true", help="resume el CSV en vez de registrar")
    a = p.parse_args(argv)
    if a.analizar:
        analizar()
        return 0
    import tambos
    try:
        registrar(a.tambo or tambos.DEFAULT_TAMBO, a.cada)
    except KeyboardInterrupt:
        print("\nCortado. Para ver el resumen: python medir_retraso_ordenos.py --analizar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
