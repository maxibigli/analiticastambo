# -*- coding: utf-8 -*-
"""Clima histórico del tambo e índice de estrés calórico (ITH).

Trae temperatura y humedad hora por hora de las coordenadas del tambo desde
Open-Meteo (gratis, sin credenciales, archivo histórico desde 1940) y calcula
el ITH diario.

    ITH = (1,8·T + 32) − ((0,55 − 0,0055·HR) · (1,8·T − 26))

Es la misma fórmula que usa `iot_monitoreo.calcular_ith()` para los sensores
en vivo; acá se aplica al histórico para poder cruzarlo con reproducción.

Lo que manda para estrés calórico es el ITH **máximo** del día, no el
promedio: una vaca que pasa cuatro horas a 80 ya sufre, aunque el promedio del
día dé 68.

    < 68  sin estrés
    68-72 leve
    72-80 moderado
    80+   severo

El clima pasado no cambia, así que se cachea a disco por día y solo se piden
los rangos que faltan.

QUÉ MOSTRÓ EL ANÁLISIS (2 años, ~4.400 servicios de La Ponderosa)
-----------------------------------------------------------------
La hipótesis simple —"el calor baja la concepción"— NO se sostiene con estos
datos: por tramos de ITH la concepción da plana (36,3% sin estrés contra 36,7%
con estrés severo). Se probaron cuatro ventanas de desfasaje (-40 a +7, -21 a
0, 0 a +7, -60 a -21) y ninguna da señal limpia.

Lo que SÍ se ve es que en verano se derrumban los SERVICIOS. Por eso el
gráfico muestra las tres series juntas —servicios, concepción e ITH—: para que
se lea de un vistazo que el bache de preñez del verano viene del lado de los
servicios, no de la concepción.

DOS TRAMPAS, resueltas acá:

1. CENSURA. El chequeo de preñez se hace ~35 días después del servicio, así
   que los servicios recientes todavía no tienen resultado y su tasa de
   concepción aparece artificialmente en cero. Los meses afectados se marcan
   con `incompleto` y NO se dibuja su línea de concepción.

2. PARADA DE SERVICIO. El tambo no insemina en marzo y abril (confirmado:
   cero inseminaciones y cero celos los dos años, mientras chequeos y partos
   siguen normales). Esos meses se marcan con `sin_servicio` para que no se
   lean como una caída de performance.
"""
import datetime
import json
import os
import threading
import urllib.parse
import urllib.request

# Coordenadas por defecto: La Ponderosa. Se pueden pisar por tambo.
LAT_DEFECTO, LON_DEFECTO = -36.001618, -62.778799
ZONA = "America/Argentina/Buenos_Aires"

API = "https://archive-api.open-meteo.com/v1/archive"

UMBRALES = [(68, "sin_estres", "Sin estrés"), (72, "leve", "Leve"),
            (80, "moderado", "Moderado"), (999, "severo", "Severo")]

# Días que tarda en confirmarse una preñez. Los servicios más recientes que
# esto todavía no tienen resultado: su tasa de concepción no es real.
DIAS_HASTA_CHEQUEO = 45

_RUTA_CACHE = os.path.join(os.path.dirname(__file__), "clima_cache.json")
_lock = threading.Lock()


def calcular_ith(temp_c: float, hum_pct: float) -> float:
    return (1.8 * temp_c + 32) - ((0.55 - 0.0055 * hum_pct) * (1.8 * temp_c - 26))


def categoria(ith: float) -> str:
    for tope, clave, _label in UMBRALES:
        if ith < tope:
            return clave
    return "severo"


def _leer_cache() -> dict:
    try:
        with open(_RUTA_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _guardar_cache(datos: dict) -> None:
    try:
        with open(_RUTA_CACHE, "w", encoding="utf-8") as f:
            json.dump(datos, f)
    except OSError:
        pass


def _clave(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def ith_diario(desde: str, hasta: str, lat: float = None, lon: float = None) -> dict:
    """{'AAAA-MM-DD': {'max': .., 'prom': ..}} para el rango pedido.

    Solo pide a la API los días que no estén cacheados. Si la API falla,
    devuelve lo que haya en caché en vez de romper: un gráfico sin la línea de
    ITH sigue sirviendo.
    """
    lat = LAT_DEFECTO if lat is None else lat
    lon = LON_DEFECTO if lon is None else lon
    clave = _clave(lat, lon)

    with _lock:
        cache = _leer_cache()
        guardado = cache.get(clave, {})

    d0 = datetime.date.fromisoformat(desde)
    d1 = datetime.date.fromisoformat(hasta)
    faltan = [(d0 + datetime.timedelta(days=i)).isoformat()
              for i in range((d1 - d0).days + 1)]
    faltan = [d for d in faltan if d not in guardado]

    if faltan:
        # Se pide el rango completo que falta de una sola vez: la API cobra
        # por request, no por día.
        params = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "start_date": min(faltan), "end_date": max(faltan),
            "hourly": "temperature_2m,relative_humidity_2m",
            "timezone": ZONA,
        })
        try:
            with urllib.request.urlopen(f"{API}?{params}", timeout=60) as r:
                h = json.load(r)["hourly"]
            por_dia = {}
            for ts, t, hr in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"]):
                if t is None or hr is None:
                    continue
                por_dia.setdefault(ts[:10], []).append(calcular_ith(t, hr))
            for dia, vals in por_dia.items():
                guardado[dia] = {"max": round(max(vals), 1),
                                 "prom": round(sum(vals) / len(vals), 1)}
            with _lock:
                cache = _leer_cache()
                cache.setdefault(clave, {}).update(guardado)
                _guardar_cache(cache)
        except Exception:  # noqa: BLE001
            pass

    return {d: v for d, v in guardado.items() if desde <= d <= hasta}


def por_mes(diario: dict) -> dict:
    """Resumen mensual: ITH máximo promedio y cuántos días superaron cada umbral."""
    meses = {}
    for dia, v in diario.items():
        m = meses.setdefault(dia[:7], {"maximos": [], "dias_72": 0, "dias_80": 0})
        m["maximos"].append(v["max"])
        if v["max"] >= 72:
            m["dias_72"] += 1
        if v["max"] >= 80:
            m["dias_80"] += 1
    return {m: {
        "ith": round(sum(d["maximos"]) / len(d["maximos"]), 1),
        "ith_pico": round(max(d["maximos"]), 1),
        "dias": len(d["maximos"]),
        "dias_estres": d["dias_72"],
        "dias_severo": d["dias_80"],
    } for m, d in meses.items() if d["maximos"]}


def armar(data_servicios, diario: dict, hoy: datetime.date) -> dict:
    """Cruza los servicios y concepciones mensuales con el ITH.

    `data_servicios`: {columns, rows} con mes, servicios, concepciones.
    """
    mensual = por_mes(diario)
    filas_sql = [dict(zip(data_servicios["columns"], f))
                 for f in (data_servicios.get("rows") or [])]
    por_mes_serv = {f["mes"]: f for f in filas_sql}

    # Un mes está incompleto si sus servicios todavía no llegaron al chequeo.
    corte = (hoy - datetime.timedelta(days=DIAS_HASTA_CHEQUEO)).strftime("%Y-%m")

    filas = []
    for m in sorted(set(list(mensual) + list(por_mes_serv))):
        s = por_mes_serv.get(m, {})
        servicios = int(s.get("servicios") or 0)
        concepciones = int(s.get("concepciones") or 0)
        clima = mensual.get(m, {})
        incompleto = m >= corte and servicios > 0
        filas.append({
            "mes": m,
            "servicios": servicios,
            "concepciones": concepciones,
            # Si el mes está incompleto no se informa la concepción: sería un
            # número falso, y encima el más reciente, que es el más mirado.
            "pct_concepcion": (None if incompleto or not servicios
                               else round(100.0 * concepciones / servicios, 1)),
            "ith": clima.get("ith"),
            "ith_pico": clima.get("ith_pico"),
            "dias_estres": clima.get("dias_estres"),
            "dias_severo": clima.get("dias_severo"),
            "incompleto": incompleto,
            "sin_servicio": servicios == 0,
        })

    con_dato = [f for f in filas if f["pct_concepcion"] is not None and f["ith"] is not None]
    calor = [f for f in con_dato if f["ith"] >= 72]
    fresco = [f for f in con_dato if f["ith"] < 68]

    def tasa(grupo):
        s = sum(f["servicios"] for f in grupo)
        c = sum(f["concepciones"] for f in grupo)
        return round(100.0 * c / s, 1) if s else None

    def serv_prom(grupo):
        return round(sum(f["servicios"] for f in grupo) / len(grupo)) if grupo else None

    return {
        "meses": filas,
        "umbrales": {"leve": 68, "moderado": 72, "severo": 80},
        "dias_hasta_chequeo": DIAS_HASTA_CHEQUEO,
        # La comparación que importa, con las dos trampas ya sacadas.
        "comparacion": {
            "meses_calor": len(calor), "meses_fresco": len(fresco),
            "concepcion_calor": tasa(calor), "concepcion_fresco": tasa(fresco),
            "servicios_calor": serv_prom(calor), "servicios_fresco": serv_prom(fresco),
        },
    }
