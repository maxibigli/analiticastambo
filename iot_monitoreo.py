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
from iot_lavado import RUTA_DB, _conectar_db

MINUTOS_ORDENO_ACTIVO = 15  # última visita dentro de esta ventana = "ordeñando"

SQL_ULTIMA_VISITA = """
    SELECT MAX(CreationTime) AS ultima FROM MilkingDeviceVisit WHERE GCRecord IS NULL
"""

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
    try:
        data = db.run_query(SQL_ULTIMA_VISITA, tambo=tambo)
        ultima = data["rows"][0][0] if data["rows"] else None
        if ultima is None:
            return False
        if isinstance(ultima, str):
            ultima = datetime.datetime.fromisoformat(ultima)
        return (datetime.datetime.now() - ultima) <= datetime.timedelta(minutes=MINUTOS_ORDENO_ACTIVO)
    except Exception:  # noqa: BLE001
        return False


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
