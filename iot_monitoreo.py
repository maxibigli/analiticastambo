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
import iot_canales
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


def lecturas_actuales() -> list:
    """Último valor conocido de cada sensor planeado (None = todavía sin
    instalar / sin datos). El ITH se calcula al vuelo si hay temp+hum de
    ambiente disponibles; si no, también queda None."""
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
    salida.append({"clave": "ith", "label": "ITH (estrés calórico)", "unidad": "", "min": 50, "max": 90,
                    "valor": ith, "fecha_hora": None})
    return salida


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
