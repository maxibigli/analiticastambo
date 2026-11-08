# -*- coding: utf-8 -*-
"""Balance de leche: sala (DelPro) -> tanque (Milky) -> camión (La Serenísima).

La misma leche se mide en tres puntos con tres instrumentos:

  sala     medidores de ordeñe de DelPro (`AnimalDaily.TotalYield`, en KILOS)
  tanque   caudalímetro de Milky/CICLA (litros por carga)
  camión   lo que reconoce el comprador (litros por día)

Este módulo es PURO (no toca la base ni la red): recibe los datos ya traídos y
devuelve el balance. `app.py` trae los datos, expone los endpoints y engancha la
alerta. Diseño y números medidos: docs/superpowers/specs/2026-11-07-balance-leche-design.md

LOS KG DE DELPRO INCLUYEN LAS LECTURAS NO VÁLIDAS (`IsYieldValid` distinto de 1).
Son ordeños reales cuyo total del día quedó incompleto en DelPro, pero esa leche
igual va al tanque. Medido el 17/10 al 02/11/2026 con `medir_kg_invalidos.py`: son
33.287 kg (1.406 filas, 2,83 % de lo válido). Sumarlos baja el cociente Milky/DelPro
de 1,0506 a 1,0217 y lo vuelve más parejo día a día (rango 0,023 contra 0,049). El
02/11, con 3.535 kg inválidos, daba +3,2 % de "desvío" y con ellos da +0,8 %.

EL FACTOR kg -> litros ES UNA CALIBRACIÓN, NO UNA CONVERSIÓN FÍSICA. Con esos kg,
Milky da 1,0217 L por cada kg de DelPro (se usa 1,02). La conversión por densidad de
la leche (~1,03 kg/L) daría ~0,971 L/kg: DelPro queda ~7,6 % por debajo de lo que
Milky y el comprador coinciden en medir, y los kg inválidos explican solo el 34 % de
esa diferencia. El resto (~5 %) sigue sin causa conocida. Por eso el factor se
muestra siempre a la vista y es editable.
"""
import datetime
import json
import os
import statistics
import threading

import cicla
import configuracion_tambo

FACTOR_DEFECTO = 1.02                 # L por kg de DelPro, con lecturas no válidas (medido, ver arriba)
FACTOR_DENSIDAD_TIPICO = 1 / 1.03     # ~0,971 L/kg: referencia, no se usa para calcular
UMBRAL_PCT = cicla.UMBRAL_DIF_PCT     # ±3 %, el mismo de la tarjeta "Caudalímetro vs. comprador"
# Un día de DelPro se da por cerrado si tiene al menos esta fracción de la mediana
# de vacas con leche de los días del período. DelPro consolida con varios días de
# atraso y el día a medio cargar (el 03/11 tenía 95 kg y 0 vacas) daría un desvío
# falso. Los días completos medidos están en 97 % o más de la mediana.
PISO_VACAS = 0.90

TRAMOS = ("sala_tanque", "tanque_camion")


FACTOR_MIN, FACTOR_MAX = 0.5, 2.0     # tope de sentido común al editar el factor


def factor_de(tambo: str) -> float:
    """Litros por kg de DelPro del tambo: el guardado, o el defecto medido."""
    valor = configuracion_tambo.config_de(tambo).get("factor_kg_litros")
    return float(valor) if valor else FACTOR_DEFECTO


def guardar_factor(tambo: str, valor) -> float:
    """Valida y guarda el factor del tambo. `ValueError` si no es un número entre
    FACTOR_MIN y FACTOR_MAX (acepta coma decimal); lo ya guardado no se toca."""
    try:
        numero = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"Factor inválido: {valor!r} (se espera un número, por ejemplo 1,02).")
    if not (FACTOR_MIN <= numero <= FACTOR_MAX):    # también rechaza nan e infinitos
        raise ValueError(f"Factor fuera de rango: {valor!r} (debe estar entre {FACTOR_MIN} y {FACTOR_MAX}).")
    configuracion_tambo.guardar(tambo, {"factor_kg_litros": numero})
    return numero


def sql_kg_diarios(desde: datetime.date, hasta: datetime.date) -> str:
    """Kg por día de ordeñe y vacas identificadas con leche, entre las fechas
    (inclusive). Los kg suman TODAS las filas con leche, también las de lectura no
    válida (esa leche va al tanque); las vacas cuentan solo las identificadas con
    lectura válida, que es con lo que se midió el día cerrado. Sin filtro de rebaño
    (así se midió el factor) y sin HAVING: decidir qué día está cerrado es de
    `calcular`, que conoce la mediana del período."""
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha,
           SUM(ad.TotalYield) AS kg,
           COUNT(DISTINCT CASE WHEN ad.IsYieldValid = 1 AND b.Number > 0 THEN b.OID END) AS vacas
    FROM AnimalDaily ad
    JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE ad.GCRecord IS NULL AND ad.TotalYield > 0
      AND ad.Date >= '{desde.isoformat()}' AND ad.Date <= '{hasta.isoformat()}'
    GROUP BY ad.Date
    ORDER BY ad.Date
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def filas_kg(data: dict) -> list:
    """`{columns, rows}` de `db.run_query` -> [{"fecha": "AAAA-MM-DD", "kg", "vacas"}]."""
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{"fecha": str(r[idx["fecha"]])[:10],
             "kg": float(r[idx["kg"]] or 0),
             "vacas": int(r[idx["vacas"]] or 0)} for r in data["rows"]]


def _iso_de_ar(txt: str) -> str:
    """'26/10/2026 07:22:15' o '1/11/2026' (La Serenísima no pone ceros) -> 'AAAA-MM-DD'."""
    dd, mm, aa = txt.strip().split(" ")[0].split("/")
    return f"{aa}-{mm.zfill(2)}-{dd.zfill(2)}"


def litros_milky_por_dia(cargas: list) -> dict:
    """Cargas de Milky -> {"AAAA-MM-DD": {"venta": L, "total": L}}. `total` es todo lo
    que salió del tanque (venta + guachera); `venta` solo lo que se vende."""
    por_dia = {}
    for c in cargas:
        if c.get("lts_cicla") is None:
            continue
        d = por_dia.setdefault(_iso_de_ar(c["fecha"]), {"venta": 0.0, "total": 0.0})
        d["total"] += c["lts_cicla"]
        if c.get("es_venta"):
            d["venta"] += c["lts_cicla"]
    return por_dia


def litros_comprador_por_dia(entregas: list) -> dict:
    """Entregas de La Serenísima -> {"AAAA-MM-DD": litros}."""
    por_dia = {}
    for e in entregas:
        if e.get("litros") is None:
            continue
        clave = _iso_de_ar(e["fecha"])
        por_dia[clave] = por_dia.get(clave, 0.0) + e["litros"]
    return por_dia


def _pct(observado, esperado):
    return round((observado / esperado - 1) * 100, 1)


def _marcar_corridas(dias: list, tramo: str, dif_ref: dict, umbral: float):
    """Fecha corrida: dos días seguidos con desvío de signo opuesto cuya suma, EN
    LITROS, queda dentro del umbral del volumen de los dos días juntos (en porcentaje
    no sirve: el denominador cambia de un día al otro). Es leche que cambió de día,
    no leche perdida.

    Los DOS días tienen que estar fuera del umbral: una carga que cambia de día deja un
    desvío grande de cada lado (el 25 y 26/10: +52 % y −27 %). Sin esa condición, un día
    de ruido normal (−1,2 % el 01/11) al lado de uno que sí se desvió (+3,2 % el 02/11) suma
    poco en litros y se tomaba por corrida, tapando justo el desvío que hay que ver."""
    por_fecha = {d["fecha"]: d for d in dias}
    for d in dias:
        sig = datetime.date.fromisoformat(d["fecha"]) + datetime.timedelta(days=1)
        sig = por_fecha.get(sig.isoformat())
        if not sig or d["fecha"] not in dif_ref or sig["fecha"] not in dif_ref:
            continue
        if not (d["alerta_" + tramo] and sig["alerta_" + tramo]):
            continue
        dif1, ref1 = dif_ref[d["fecha"]]
        dif2, ref2 = dif_ref[sig["fecha"]]
        if dif1 * dif2 >= 0:
            continue
        if abs(dif1 + dif2) <= umbral / 100 * (ref1 + ref2):
            for x in (d, sig):
                if x["alerta_" + tramo]:
                    x["alerta_" + tramo] = False
                    x["corrida_" + tramo] = True


def calcular(kg_dia: list, milky: dict, comprador, factor: float, umbral: float = UMBRAL_PCT) -> dict:
    """Balance por día. `kg_dia`: [{"fecha","kg","vacas"}]; `milky`: salida de
    `litros_milky_por_dia`; `comprador`: salida de `litros_comprador_por_dia` o None si
    no hay datos del comprador. Un valor que no se puede calcular va en None."""
    kg_por_fecha = {k["fecha"]: k for k in kg_dia}
    vacas_con_leche = [k["vacas"] for k in kg_dia if k["vacas"] > 0]
    piso = PISO_VACAS * statistics.median(vacas_con_leche) if vacas_con_leche else 0.0

    fechas = set(kg_por_fecha) | set(milky) | set(comprador or {})
    dias, dif_sala, dif_camion = [], {}, {}
    for fecha in sorted(fechas):
        k = kg_por_fecha.get(fecha)
        m = milky.get(fecha)
        kg = k["kg"] if k else None
        vacas = k["vacas"] if k else None
        cerrado = bool(k) and bool(vacas_con_leche) and k["vacas"] >= piso and k["vacas"] > 0
        esperado = kg * factor if kg is not None else None
        lts_milky = m["total"] if m else None
        lts_venta = m["venta"] if m else None
        lts_comprador = comprador.get(fecha) if comprador else None

        d_sala = d_camion = None
        if cerrado and lts_milky is not None and esperado:
            d_sala = _pct(lts_milky, esperado)
            dif_sala[fecha] = (lts_milky - esperado, esperado)
        if lts_venta is not None and lts_comprador:
            d_camion = _pct(lts_venta, lts_comprador)
            dif_camion[fecha] = (lts_venta - lts_comprador, lts_comprador)

        dias.append({
            "fecha": fecha, "kg": kg, "vacas": vacas, "cerrado": cerrado,
            "lts_delpro": round(esperado) if esperado is not None else None,
            "lts_milky": lts_milky, "lts_venta": lts_venta, "lts_comprador": lts_comprador,
            "desvio_sala_tanque_pct": d_sala, "desvio_tanque_camion_pct": d_camion,
            "alerta_sala_tanque": d_sala is not None and abs(d_sala) > umbral,
            "alerta_tanque_camion": d_camion is not None and abs(d_camion) > umbral,
            "corrida_sala_tanque": False, "corrida_tanque_camion": False,
        })
    _marcar_corridas(dias, "sala_tanque", dif_sala, umbral)
    _marcar_corridas(dias, "tanque_camion", dif_camion, umbral)

    # Totales del período: cada tramo suma solo los días donde SU par existe.
    esp_sala = sum(v[1] for v in dif_sala.values())
    obs_sala = sum(v[0] + v[1] for v in dif_sala.values())
    ref_camion = sum(v[1] for v in dif_camion.values())
    obs_camion = sum(v[0] + v[1] for v in dif_camion.values())
    t_sala = _pct(obs_sala, esp_sala) if esp_sala else None
    t_camion = _pct(obs_camion, ref_camion) if ref_camion else None
    mayor = None
    if t_sala is not None or t_camion is not None:
        mayor = "sala_tanque" if abs(t_sala or 0) >= abs(t_camion or 0) else "tanque_camion"
    return {
        "factor": factor, "umbral": umbral, "comprador_disponible": comprador is not None,
        "piso_vacas": piso, "dias": dias,
        "totales": {"lts_delpro": round(esp_sala), "lts_milky": round(obs_sala),
                    "lts_venta": round(obs_camion), "lts_comprador": round(ref_camion),
                    "desvio_sala_tanque_pct": t_sala, "desvio_tanque_camion_pct": t_camion},
        "tramo_mayor": mayor,
    }


# --- Alertas --------------------------------------------------------------------------------------
# Un día se evalúa para alertar recién cuando existe un día POSTERIOR en el mismo tramo (normalmente el
# siguiente): con el par completo se ve si fue una fecha corrida (una carga de medianoche asignada a otro día) y no se
# alerta una pérdida que no es tal. La alerta llega con un día de atraso. DelPro consolida con
# varios días de atraso, así que la antigüedad del dato no sirve para no repetir: se guarda, por
# tambo y por tramo, el último día ya evaluado (`balance_leche_avisos.json`) y cada día se evalúa
# una sola vez. Sin registro previo solo se evalúa el último día evaluable, para no inundar de
# avisos viejos la primera vez.
_RUTA_AVISOS = os.path.join(os.path.dirname(__file__), "balance_leche_avisos.json")
_lock_avisos = threading.Lock()
_NOMBRE_TRAMO = {"sala_tanque": "sala → tanque", "tanque_camion": "tanque → camión"}


def evaluar_alertas(dias: list, avisados: dict) -> tuple:
    """-> (eventos, avisados_nuevo). `dias` es `calcular(...)["dias"]`; `avisados` es
    {tramo: "AAAA-MM-DD"} (el último día ya evaluado). Evento: {"tramo", "fecha",
    "desvio_pct"}. Un par de fecha corrida no genera evento (`calcular` ya le sacó la alerta)."""
    eventos, nuevos = [], dict(avisados)
    for tramo in TRAMOS:
        col = f"desvio_{tramo}_pct"
        con_dato = [d for d in sorted(dias, key=lambda x: x["fecha"]) if d[col] is not None]
        # Evaluable = ya hay algún día POSTERIOR con dato. Casi siempre es el día siguiente, pero si
        # ese día no existe (DelPro sin ordeños ese día, no cerrado) y hay uno más adelante, el hueco
        # es permanente —los datos llegan en orden— y esperar sería descartar la alerta en silencio.
        evaluables = con_dato[:-1]
        if not evaluables:
            continue
        ultimo = avisados.get(tramo)
        candidatos = [d for d in evaluables if d["fecha"] > ultimo] if ultimo else evaluables[-1:]
        eventos += [{"tramo": tramo, "fecha": d["fecha"], "desvio_pct": d[col]}
                    for d in candidatos if d[f"alerta_{tramo}"]]
        nuevos[tramo] = evaluables[-1]["fecha"]
    return eventos, nuevos


def _signado(valor: float) -> str:
    return f"{'+' if valor >= 0 else '−'}{abs(valor):.1f}".replace(".", ",") + " %"


def texto_alerta(eventos: list, umbral: float = UMBRAL_PCT) -> list:
    """Una línea por tramo (varios días del mismo tramo van en la misma línea)."""
    lineas = []
    for tramo in TRAMOS:
        propios = [e for e in eventos if e["tramo"] == tramo]
        if not propios:
            continue
        partes = [f"{_signado(e['desvio_pct'])} el {e['fecha'][8:10]}/{e['fecha'][5:7]}" for e in propios]
        lineas.append(f"⚖️ Balance de leche: {_NOMBRE_TRAMO[tramo]} se desvió {' y '.join(partes)} "
                      f"(umbral ±{umbral:g} %).")
    return lineas


def leer_avisados(tambo: str) -> dict:
    """{tramo: "AAAA-MM-DD"} del tambo; {} si no hay registro o el archivo está roto."""
    try:
        with open(_RUTA_AVISOS, "r", encoding="utf-8") as f:
            todo = json.load(f)
    except (OSError, ValueError):
        return {}
    propio = todo.get(tambo) if isinstance(todo, dict) else None
    return dict(propio) if isinstance(propio, dict) else {}


def guardar_avisados(tambo: str, avisados: dict) -> None:
    """Guarda el registro del tambo sin tocar el de los otros. Escribe a un archivo
    temporal y lo reemplaza, para que un corte a mitad no deje el JSON a medias."""
    with _lock_avisos:
        try:
            with open(_RUTA_AVISOS, "r", encoding="utf-8") as f:
                todo = json.load(f)
            if not isinstance(todo, dict):
                todo = {}
        except (OSError, ValueError):
            todo = {}
        todo[tambo] = dict(avisados)
        tmp = _RUTA_AVISOS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _RUTA_AVISOS)
