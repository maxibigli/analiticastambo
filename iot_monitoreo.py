# -*- coding: utf-8 -*-
"""Monitoreo IoT en tiempo real: estado de la rotativa (ORDEÑO / LAVANDO /
BARRIDO / APAGADO) y sensores de temperatura/humedad del tambo.

Fuentes de datos:
- ORDEÑO: NO es un sensor propio — se deriva de la actividad reciente en
  MilkingDeviceVisit (base DDM de DelPro). Si hubo una visita hace poco,
  la rotativa está ordeñando.
- LAVANDO / BARRIDO: contactos secos DI01/DI02 del gateway PUSR M300,
  leídos y guardados por iot_lavado.py en iot_sensores.db (tabla eventos_di).
- Sensores de temperatura/humedad: en `lecturas_sensor` de la misma base —
  todavía SIN hardware instalado (ver SENSORES_PLANEADOS). El tablero los
  muestra como "a instalar" hasta que aparezcan filas reales; no hace falta
  tocar este código cuando se cableen, empiezan a mostrarse solos.

Prioridad de estados: si está lavando o barriendo, eso manda (son procesos
que no se solapan con el ordeño real); si no, se mira si hay ordeño en
curso; si no hay nada de eso, APAGADO.
"""
import datetime
import sqlite3

import db
import iot_analogicas
import iot_canales
import iot_vumetros
import voz_comandos
from iot_lavado import RUTA_DB, _conectar_db, ACTUADORES

MINUTOS_ORDENO_ACTIVO = 15  # última visita dentro de esta ventana = "ordeñando"
CACHE_ORDENO_TTL_S = 30     # el frontend refresca cada 5s; sin esto pegaría
                            # una consulta a la base DDM por refresco, de más
                            # (una sesión de ordeño dura horas, 30s de caché
                            # no le hace perder nada de "tiempo real" real).

SQL_ULTIMA_VISITA = """
    SELECT MAX(CreationTime) AS ultima FROM MilkingDeviceVisit WHERE GCRecord IS NULL
"""

_cache_ordeno: dict = {}  # tambo -> (timestamp, bool)

# Sensores planeados (ninguno instalado todavía, 2026-07-25 — ver memoria
# delpro-iot-gateway). "ith" es calculado (temperatura-humedad, estrés
# calórico), no un sensor propio, así que no tiene "unidad" de sensor directo.
SENSORES_PLANEADOS = [
    {"clave": "temp_leche", "label": "Temp. leche", "unidad": "°C", "min": 0, "max": 40},
    {"clave": "temp_llegada", "label": "Temp. llegada", "unidad": "°C", "min": 0, "max": 40},
    {"clave": "temp_ambiente", "label": "Temp. ambiente", "unidad": "°C", "min": -10, "max": 45},
    {"clave": "hum_ambiente", "label": "Humedad ambiente", "unidad": "%", "min": 0, "max": 100},
    {"clave": "temp_lavado", "label": "Temp. lavado", "unidad": "°C", "min": 0, "max": 90},
    {"clave": "temp_sala_maquinas", "label": "Temp. sala máquinas", "unidad": "°C", "min": -10, "max": 50},
    {"clave": "temp_sala_tableros", "label": "Temp. sala tableros", "unidad": "°C", "min": -10, "max": 50},
    {"clave": "temp_sala_caldera", "label": "Temp. sala caldera", "unidad": "°C", "min": 0, "max": 80},
    {"clave": "temp_corral", "label": "Temp. corral", "unidad": "°C", "min": -10, "max": 45},
    {"clave": "hum_corral", "label": "Humedad corral", "unidad": "%", "min": 0, "max": 100},
    {"clave": "vacio_general", "label": "Vacío (a definir sectores)", "unidad": "kPa", "min": 0, "max": 60},
]


def calcular_ith(temp_c: float, hum_pct: float) -> float:
    """Índice temperatura-humedad (estrés calórico bovino). Fórmula NRC
    estándar; >68 empieza el estrés leve, >72 moderado, >80 severo."""
    return round((1.8 * temp_c + 32) - ((0.55 - 0.0055 * hum_pct) * (1.8 * temp_c - 26)), 1)


def _ultimo_estado_canal(canal: str):
    """(bool estado, fecha_hora str) del último evento de ese canal, o
    (False, None) si todavía no hay ningún dato (canal sin cablear)."""
    con = _conectar_db()
    try:
        fila = con.execute(
            "SELECT estado, fecha_hora FROM eventos_di WHERE canal = ? "
            "ORDER BY fecha_hora DESC LIMIT 1", (canal,)
        ).fetchone()
        return (bool(fila[0]), fila[1]) if fila else (False, None)
    finally:
        con.close()


def _ordeno_activo(tambo: str) -> bool:
    ahora = datetime.datetime.now()
    cacheado = _cache_ordeno.get(tambo)
    if cacheado and (ahora - cacheado[0]).total_seconds() < CACHE_ORDENO_TTL_S:
        return cacheado[1]
    try:
        data = db.run_query(SQL_ULTIMA_VISITA, tambo=tambo)
        ultima = data["rows"][0][0] if data["rows"] else None
        if ultima is None:
            resultado = False
        else:
            if isinstance(ultima, str):
                ultima = datetime.datetime.fromisoformat(ultima)
            resultado = (ahora - ultima) <= datetime.timedelta(minutes=MINUTOS_ORDENO_ACTIVO)
    except Exception:  # noqa: BLE001
        resultado = cacheado[1] if cacheado else False  # ante un error puntual, no parpadear a APAGADO
    _cache_ordeno[tambo] = (ahora, resultado)
    return resultado


def estado_sistema(tambo: str) -> dict:
    lavando, fecha_lavado = _ultimo_estado_canal("lavado_rotativa")
    barriendo, fecha_barrido = _ultimo_estado_canal("barrido_rotativa")
    ordenando = _ordeno_activo(tambo)

    if lavando:
        estado, desde = "LAVANDO", fecha_lavado
    elif barriendo:
        estado, desde = "BARRIDO", fecha_barrido
    elif ordenando:
        estado, desde = "ORDEÑO", None
    else:
        estado, desde = "APAGADO", None

    return {
        "estado": estado, "desde": desde,
        "lavando": lavando, "barriendo": barriendo, "ordenando": ordenando,
    }


# Panel de entradas/salidas del M300 (pestaña Actuadores de la pantalla
# ESP32) -- 8 DI + 8 DO, ver iot_lavado.CANALES/ACTUADORES. Los dos primeros
# DI ya tienen semántica propia (lavado/barrido, con su badge de estado en
# la pestaña Sensores); el resto son genéricos hasta que el tambo defina qué
# sensor/actuador va en cada uno -- mismo criterio que SENSORES_PLANEADOS.
ENTRADAS_PANEL = [
    ("lavado_rotativa", "Entrada 1 (lavado)"),
    ("barrido_rotativa", "Entrada 2 (barrido)"),
    ("di_3", "Entrada 3"),
    ("di_4", "Entrada 4"),
    ("di_5", "Entrada 5"),
    ("di_6", "Entrada 6"),
    ("di_7", "Entrada 7"),
    ("di_8", "Entrada 8"),
]
SALIDAS_PANEL = [(f"do_{i}", f"Actuador {i}") for i in range(1, 9)]


def panel_io() -> dict:
    """Estado de las 8 entradas (on/off) y las 8 salidas del M300, para la
    pestaña Actuadores de la pantalla ESP32.

    De cada salida se informan DOS cosas distintas:
    - `ultima_activacion`: cuándo se le mandó el último PULSO (el botón de
      la pantalla, 0,5s, ver iot_lavado.DURACION_PULSO_S). Un pulso no deja
      estado: se suelta solo.
    - `sostenido_desde`: desde cuándo está prendida de forma SOSTENIDA por
      un comando de voz (None si no lo está). Esto sí es estado persistente
      -- desde los comandos de voz "Jarvis" una salida puede quedar
      encendida indefinidamente hasta que alguien pida apagarla, así que ya
      no es cierto que las salidas sean solo pulsos momentáneos, y una
      pantalla que no lo muestre deja un relé prendido sin ningún indicador.
      (El tope máximo de tiempo encendida es una pregunta para el tambo, no
      un número para inventar acá -- ver CLAUDE.md, misma regla que las
      duraciones de etapa y los umbrales de retirada.)

    El import de voz_comandos va en la dirección que ya existe: este módulo
    importa iot_lavado, que importa voz_comandos; voz_comandos no importa a
    ninguno de los dos."""
    nombres_custom = iot_canales.nombres()
    sostenidos = voz_comandos.estado()   # clave -> encendido_desde (ISO)
    entradas = [
        {"clave": clave, "label": nombres_custom.get(clave, label), "estado": estado, "desde": desde}
        for clave, label in ENTRADAS_PANEL
        for estado, desde in [_ultimo_estado_canal(clave)]
    ]

    con = _conectar_db()
    try:
        salidas = []
        for clave, label in SALIDAS_PANEL:
            fila = con.execute(
                "SELECT fecha_hora FROM comandos_actuador WHERE canal = ? AND resultado = 'ok' "
                "ORDER BY fecha_hora DESC LIMIT 1", (clave,)
            ).fetchone()
            salidas.append({"clave": clave, "label": nombres_custom.get(clave, label),
                            "ultima_activacion": fila[0] if fila else None,
                            "sostenido_desde": sostenidos.get(clave)})
    finally:
        con.close()

    return {"entradas": entradas, "salidas": salidas}


def sensores_asignables() -> dict:
    """clave -> (min, max) de los sensores que se pueden asignar a una entrada analógica. El ITH no: se calcula."""
    return {s["clave"]: (s["min"], s["max"]) for s in SENSORES_PLANEADOS}


def panel_analogicas() -> dict:
    """Las entradas analógicas con su asignación y su lectura EN VIVO, para ⚙ Configuración › 🔌 Entradas/Salidas.

    De cada una: la configuración (sensor, dirección Modbus, escala), el último número crudo que leyó iot_lavado.py y,
    si está calibrada, la corriente en mA y el valor ya convertido. Con un simulador en 4, 12 y 20 mA se ven los
    crudos y se cargan como escala. `ma`/`valor` son None si falta calibrar; `valor` es None y `sin_senal` True si el
    lazo está cortado."""
    configuradas = iot_analogicas.cargar()
    con = _conectar_db()
    try:
        crudos = {c: (raw, fh) for c, raw, fh in con.execute("SELECT canal, raw, fecha_hora FROM analogicas_crudo")}
    finally:
        con.close()

    canales = []
    for clave, label in iot_analogicas.CANALES:
        cfg = configuradas.get(clave) or {}
        raw, fecha = crudos.get(clave, (None, None))
        conv = iot_analogicas.convertir(raw, cfg) if cfg else None
        canales.append({
            "clave": clave, "label": label,
            "sensor": cfg.get("sensor"), "direccion": cfg.get("direccion"),
            "raw_4": cfg.get("raw_4"), "raw_20": cfg.get("raw_20"),
            "min": cfg.get("min"), "max": cfg.get("max"),
            "raw": raw, "fecha_hora": fecha,
            "ma": conv["ma"] if conv else None,
            "valor": conv["valor"] if conv else None,
            "sin_senal": bool(conv and conv["sin_senal"]),
        })
    sensores = [{"clave": s["clave"], "label": s["label"], "unidad": s["unidad"], "min": s["min"], "max": s["max"]}
                for s in SENSORES_PLANEADOS]
    return {"canales": canales, "sensores": sensores}


def solicitar_pulso(canal: str) -> bool:
    """Encola un pulso de actuador -- lo EJECUTA iot_lavado.py en su propio
    ciclo (dueño único de la conexión Modbus al M300; si esta función abriera
    su propia conexión se arriesgaría a pisarse con el polling continuo de
    DI que ya corre ahí). True si el canal es válido y quedó encolado."""
    if canal not in ACTUADORES:
        return False
    con = _conectar_db()
    try:
        con.execute(
            "INSERT INTO comandos_actuador (canal, fecha_hora, ejecutado) VALUES (?, ?, 0)",
            (canal, datetime.datetime.now().isoformat(timespec="seconds"))
        )
        con.commit()
    finally:
        con.close()
    return True


def ciclos_lavado(limite: int = 20) -> list:
    """Historial de ciclos de lavado/barrido de la rotativa, para la pestaña
    Lavado Automático de la pantalla ESP32. `eventos_di` guarda un evento por
    CAMBIO de estado (ver iot_lavado.registrar_si_cambio), no uno por ciclo
    -- acá se empareja cada encendido con el próximo apagado del MISMO canal.
    Si el último evento de un canal es un encendido sin apagado todavía
    (está lavando/barriendo AHORA), el ciclo queda con `fin=None`."""
    con = _conectar_db()
    try:
        filas = con.execute(
            "SELECT canal, fecha_hora, estado FROM eventos_di "
            "WHERE canal IN ('lavado_rotativa', 'barrido_rotativa') ORDER BY fecha_hora"
        ).fetchall()
    finally:
        con.close()

    ciclos = []
    abiertos = {}   # canal -> fecha_hora del encendido todavía sin apagado
    for canal, fecha_hora, estado in filas:
        if estado:
            abiertos[canal] = fecha_hora
        elif canal in abiertos:
            ciclos.append({"tipo": canal, "inicio": abiertos.pop(canal), "fin": fecha_hora})
    for canal, inicio in abiertos.items():
        ciclos.append({"tipo": canal, "inicio": inicio, "fin": None})

    ahora = datetime.datetime.now()
    for c in ciclos:
        t0 = datetime.datetime.fromisoformat(c["inicio"])
        t1 = datetime.datetime.fromisoformat(c["fin"]) if c["fin"] else ahora
        c["duracion_s"] = int((t1 - t0).total_seconds())

    ciclos.sort(key=lambda c: c["inicio"], reverse=True)
    return ciclos[:limite]


def lecturas_actuales(ahora: datetime.datetime = None) -> list:
    """Lectura de AHORA de cada sensor planeado. `valor` None = todavía sin instalar o sin datos (`sin_datos`: lleva más de
    `VENTANA_LECTURA_RECIENTE_MIN` minutos sin mandar nada; ahí NO se devuelve la última medición, ver
    `_sin_datos_si_esta_mudo`). El ITH se calcula al vuelo si hay temp+hum de ambiente; si no, también queda None."""
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lecturas_sensor_sensor_fecha ON lecturas_sensor(sensor, fecha_hora)")
    con.commit()

    valores = {}
    for s in SENSORES_PLANEADOS:
        fila = con.execute(
            "SELECT valor, fecha_hora FROM lecturas_sensor WHERE sensor = ? "
            "ORDER BY fecha_hora DESC LIMIT 1", (s["clave"],)
        ).fetchone()
        valores[s["clave"]] = {"valor": fila[0], "fecha_hora": fila[1]} if fila else {"valor": None, "fecha_hora": None}
    con.close()

    ith = None
    if valores["temp_ambiente"]["valor"] is not None and valores["hum_ambiente"]["valor"] is not None:
        ith = calcular_ith(valores["temp_ambiente"]["valor"], valores["hum_ambiente"]["valor"])

    salida = []
    for s in SENSORES_PLANEADOS:
        v = valores[s["clave"]]
        salida.append({**s, "valor": v["valor"], "fecha_hora": v["fecha_hora"]})
    # El ITH es tan reciente como el más viejo de los dos datos de los que sale.
    fechas_ith = [valores[k]["fecha_hora"] for k in ("temp_ambiente", "hum_ambiente")]
    salida.append({"clave": "ith", "label": "ITH (estrés calórico)", "unidad": "", "min": 50, "max": 90,
                    "valor": ith, "fecha_hora": None if None in fechas_ith else min(fechas_ith)})
    configuradas = iot_vumetros.cargar()
    ahora = ahora or datetime.datetime.now()
    return [_con_vumetro(_sin_datos_si_esta_mudo(s, configuradas, ahora), configuradas) for s in salida]


# Un sensor que no mandó nada en este tiempo está MUDO: no se muestra su última medición como si fuera la de ahora (sería
# un «7 °C» tranquilizador de hace tres horas), se muestra «Sin datos» y cuenta como alarma. Las analógicas se guardan cada
# 30 s (iot_lavado.INTERVALO_GUARDADO_ANALOGICAS_S), así que 30 minutos es muy holgado. Es una decisión mía, no del tambo:
# si molesta, se cambia acá.
VENTANA_LECTURA_RECIENTE_MIN = 30


def _sin_datos_si_esta_mudo(sensor: dict, configuradas: dict, ahora: datetime.datetime) -> dict:
    """Marca `sin_datos` en un sensor INSTALADO (alguna vez leyó, o el tambo le cargó límites) cuya última lectura es más
    vieja que `VENTANA_LECTURA_RECIENTE_MIN`. En ese caso `valor` pasa a None (nunca el último conocido, que queda en
    `ultimo_valor` solo para informar) y `sin_datos_hace_min` dice cuánto hace. Uno que nunca leyó y no tiene límites
    es «sin instalar»: no es una falla, todavía no existe."""
    cfg = iot_vumetros.config_de(sensor["clave"], configuradas)
    con_limites = any(cfg[k] is not None for k in iot_vumetros.LIMITES)
    hace_min = None
    try:
        if sensor["fecha_hora"]:
            hace_min = int((ahora - datetime.datetime.fromisoformat(sensor["fecha_hora"])).total_seconds() // 60)
    except ValueError:
        pass
    mudo = con_limites if hace_min is None else hace_min > VENTANA_LECTURA_RECIENTE_MIN
    if not mudo:
        return {**sensor, "sin_datos": False, "ultimo_valor": None, "sin_datos_hace_min": None}
    return {**sensor, "valor": None, "sin_datos": True, "ultimo_valor": sensor["valor"], "sin_datos_hace_min": hace_min}


def _con_vumetro(sensor: dict, configuradas: dict) -> dict:
    """Le suma a un sensor cómo se dibuja: estilo, escala propia (si el tambo cargó una, pisa la de siempre), los
    4 límites y la zona de color de su lectura de ahora (None si no hay lectura o no hay ningún límite)."""
    cfg = iot_vumetros.config_de(sensor["clave"], configuradas)
    return {
        **sensor,
        "min": sensor["min"] if cfg["min"] is None else cfg["min"],
        "max": sensor["max"] if cfg["max"] is None else cfg["max"],
        "estilo": cfg["estilo"],
        "limites": {k: cfg[k] for k in iot_vumetros.LIMITES},
        "zona": iot_vumetros.zona(sensor["valor"], cfg),
    }


def historico(sensor: str, desde: str, hasta: str, max_puntos: int = 150) -> list:
    """Serie temporal de un sensor entre desde/hasta (fechas ISO, con hora),
    agrupada en baldes de tiempo para no mandar miles de puntos a una
    pantalla chica -- como mucho `max_puntos` valores, cada uno el promedio
    de su balde. [] si no hay ninguna lectura en el rango (sensor sin
    instalar todavía, o directamente sin datos en esas fechas -- son la
    MISMA situación hoy: ver la nota de arriba, nadie escribe en
    `lecturas_sensor` todavía).

    "ith" es un caso especial: se calcula por balde a partir de los
    promedios de temp_ambiente y hum_ambiente de ESE MISMO balde, no de un
    valor propio guardado (mismo criterio que `calcular_ith` en
    `lecturas_actuales`)."""
    if sensor == "ith":
        temps = {p["fecha_hora"]: p["valor"] for p in historico("temp_ambiente", desde, hasta, max_puntos)}
        hums = {p["fecha_hora"]: p["valor"] for p in historico("hum_ambiente", desde, hasta, max_puntos)}
        return [{"fecha_hora": f, "valor": calcular_ith(temps[f], hums[f])}
                for f in sorted(temps) if f in hums]

    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    filas = con.execute(
        "SELECT fecha_hora, valor FROM lecturas_sensor WHERE sensor = ? AND fecha_hora BETWEEN ? AND ? "
        "ORDER BY fecha_hora", (sensor, desde, hasta)
    ).fetchall()
    con.close()
    if not filas:
        return []

    t0 = datetime.datetime.fromisoformat(filas[0][0])
    t1 = datetime.datetime.fromisoformat(filas[-1][0])
    # Minimo 60s por balde: con pocos datos (todo el rango cabe en un
    # instante) evita una division que de un balde de 0 segundos.
    balde_s = max((t1 - t0).total_seconds() / max_puntos, 60)

    baldes: dict = {}
    for fecha_hora, valor in filas:
        t = datetime.datetime.fromisoformat(fecha_hora)
        idx = int((t - t0).total_seconds() // balde_s)
        baldes.setdefault(idx, []).append(valor)

    return [
        {"fecha_hora": (t0 + datetime.timedelta(seconds=idx * balde_s)).isoformat(timespec="minutes"),
         "valor": round(sum(vals) / len(vals), 2)}
        for idx, vals in sorted(baldes.items())
    ]


def conteo_alarmas(ahora: datetime.datetime = None) -> dict:
    """Cuántos sensores están en rojo, amarillo y verde según las zonas de sus vúmetros, y cuántos están mudos (para el
    Tablero de Diagnóstico).

    Un sensor mudo (`sin_datos`: ver `_sin_datos_si_esta_mudo`) cuenta SIEMPRE, tenga límites o no: que dejó de mandar datos
    no depende de con qué se lo compare. Los demás solo cuentan con límites cargados (sin ellos no hay con qué comparar la
    lectura): nunca se mide con la última medición de un sensor que dejó de reportar."""
    c = {"rojo": 0, "amarillo": 0, "verde": 0, "sin_datos": 0, "en_rojo": [], "en_amarillo": [], "sin_lectura": [],
         "con_limites": 0, "sin_limites": 0}
    for s in lecturas_actuales(ahora):
        if s["sin_datos"]:
            c["sin_datos"] += 1
            c["sin_lectura"].append(s["label"])
            continue
        if all(v is None for v in s["limites"].values()):
            c["sin_limites"] += 1
            continue
        c["con_limites"] += 1
        c[s["zona"]] += 1
        if s["zona"] == "rojo":
            c["en_rojo"].append(s["label"])
        elif s["zona"] == "amarillo":
            c["en_amarillo"].append(s["label"])
    return c


def sensores_con_ith() -> list:
    """Claves de todo lo que tiene vúmetro: los sensores del panel y el ITH (que se calcula)."""
    return [x["clave"] for x in SENSORES_PLANEADOS] + ["ith"]


def sensores_para_vumetros() -> list:
    """Cada sensor con su configuración de vúmetro, para ⚙ Configuración › 🔌 Entradas/Salidas. `escala_defecto` es
    la de siempre (la que se usa si el tambo no carga una propia)."""
    configuradas = iot_vumetros.cargar()
    base = [{"clave": s["clave"], "label": s["label"], "unidad": s["unidad"], "min": s["min"], "max": s["max"]}
            for s in SENSORES_PLANEADOS]
    base.append({"clave": "ith", "label": "ITH (estrés calórico)", "unidad": "", "min": 50, "max": 90})
    salida = []
    for s in base:
        cfg = iot_vumetros.config_de(s["clave"], configuradas)
        salida.append({"clave": s["clave"], "label": s["label"], "unidad": s["unidad"],
                       "escala_defecto": {"min": s["min"], "max": s["max"]},
                       "estilo": cfg["estilo"], "min": cfg["min"], "max": cfg["max"],
                       **{k: cfg[k] for k in iot_vumetros.LIMITES}})
    return salida


# Historial de un sensor al hacer clic en su tarjeta: hasta dónde se mira y de a cuánto se agrupa. Con lecturas cada 30 s
# se guardan ~1 millón de filas por sensor y por año (decisión del tambo: se guarda todo el detalle), así que el
# agrupado lo hace SQLite y nunca se trae una fila de más: cada rango devuelve de 120 a 365 puntos.
RANGOS_HISTORIAL = {
    "dia": (datetime.timedelta(hours=24), 600),            # 144 puntos, de 10 minutos
    "semana": (datetime.timedelta(days=7), 3600),          # 168 puntos, de 1 hora
    "mes": (datetime.timedelta(days=30), 6 * 3600),        # 120 puntos, de 6 horas
    "anio": (datetime.timedelta(days=365), 86400),         # 365 puntos, de 1 día
}
_EPOCA = datetime.datetime(1970, 1, 1)


def _baldes(con, sensor: str, desde: str, hasta: str, balde_s: int) -> list:
    """[(inicio del balde ISO, promedio, mínimo, máximo)] en orden cronológico, agrupado en la base."""
    filas = con.execute(
        "SELECT CAST(strftime('%s', fecha_hora) AS INTEGER) / ? AS b, AVG(valor), MIN(valor), MAX(valor) "
        "FROM lecturas_sensor WHERE sensor = ? AND fecha_hora >= ? AND fecha_hora <= ? "
        "GROUP BY b ORDER BY b", (balde_s, sensor, desde, hasta)).fetchall()
    return [((_EPOCA + datetime.timedelta(seconds=b * balde_s)).isoformat(timespec="minutes"), p, mn, mx)
            for b, p, mn, mx in filas]


def historial(sensor: str, rango: str, ahora: datetime.datetime = None) -> dict:
    """Serie de UN sensor para el gráfico del historial: {puntos: [{t, prom, min, max}], resumen: {min, prom, max, n}}.

    `resumen` es del rango entero y sale de todas las lecturas (no de promediar promedios). El ITH no es un sensor:
    se calcula por balde con el promedio de temperatura y de humedad ambiente de ESE balde, y por eso no tiene mínimo
    ni máximo propios (min/max de cada punto son None) y su `resumen.n` cuenta baldes, no lecturas."""
    if rango not in RANGOS_HISTORIAL:
        raise ValueError(f"Rango inválido: {rango!r} (opciones: {', '.join(RANGOS_HISTORIAL)}).")
    ahora = ahora or datetime.datetime.now()
    ventana, balde_s = RANGOS_HISTORIAL[rango]
    desde = (ahora - ventana).isoformat(timespec="seconds")
    hasta = ahora.isoformat(timespec="seconds")

    con = sqlite3.connect(RUTA_DB)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS lecturas_sensor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor TEXT NOT NULL,
                fecha_hora TEXT NOT NULL,
                valor REAL NOT NULL
            )
        """)
        if sensor == "ith":
            temps = {t: p for t, p, _, _ in _baldes(con, "temp_ambiente", desde, hasta, balde_s)}
            hums = {t: p for t, p, _, _ in _baldes(con, "hum_ambiente", desde, hasta, balde_s)}
            puntos = [{"t": t, "prom": calcular_ith(temps[t], hums[t]), "min": None, "max": None}
                      for t in sorted(temps) if t in hums]
            proms = [p["prom"] for p in puntos]
            resumen = {"min": min(proms), "prom": round(sum(proms) / len(proms), 2), "max": max(proms),
                       "n": len(proms)} if proms else {"min": None, "prom": None, "max": None, "n": 0}
        else:
            puntos = [{"t": t, "prom": round(p, 2), "min": round(mn, 2), "max": round(mx, 2)}
                      for t, p, mn, mx in _baldes(con, sensor, desde, hasta, balde_s)]
            mn, prom, mx, n = con.execute(
                "SELECT MIN(valor), AVG(valor), MAX(valor), COUNT(*) FROM lecturas_sensor "
                "WHERE sensor = ? AND fecha_hora >= ? AND fecha_hora <= ?", (sensor, desde, hasta)).fetchone()
            resumen = ({"min": round(mn, 2), "prom": round(prom, 2), "max": round(mx, 2), "n": n}
                       if n else {"min": None, "prom": None, "max": None, "n": 0})
    finally:
        con.close()
    return {"sensor": sensor, "rango": rango, "desde": desde, "hasta": hasta, "balde_s": balde_s,
            "puntos": puntos, "resumen": resumen}
