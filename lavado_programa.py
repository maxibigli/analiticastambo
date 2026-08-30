# -*- coding: utf-8 -*-
"""Programa de lavado automático de la rotativa: hasta 3 etapas, cada una
prende uno o más relés de salida del M300 (ver iot_lavado.ACTUADORES) por
una duración fija en segundos.

NO hay una duración "correcta" genérica -- depende de cuánto tarda la
vuelta de ESTA rotativa en ESTE tambo, así que no se inventa ningún valor
por defecto acá: el tambo carga los tiempos desde ⚙ Configuración › 🧼
Lavado Automático, mismo criterio que los umbrales de retirada o el umbral
de preparación (nunca inventarlos, ver CLAUDE.md).

Este módulo SOLO guarda/valida la configuración y el ESTADO del ciclo en
curso (tabla ciclo_lavado_estado, en la misma iot_sensores.db) -- quien
EJECUTA el ciclo (prende/apaga los relés de verdad) es iot_lavado.py, único
dueño de la conexión Modbus al M300 (ver ejecutar_comandos_pendientes ahí,
mismo patrón que ya usan los pulsos manuales de Actuadores).

Deliberadamente SIN import de iot_lavado (que sí importa este módulo) para
no armar un ciclo -- la ruta de la base y las claves válidas de relé se
declaran acá mismo, mismo criterio que iot_canales.py.
"""
import datetime
import json
import os
import sqlite3
import threading

RUTA_DB = "iot_sensores.db"
_RUTA_CONFIG = os.path.join(os.path.dirname(__file__), "lavado_programa.json")
_lock = threading.Lock()

MAX_ETAPAS = 3
RELES_VALIDOS = {"do_1", "do_2", "do_3", "do_4", "do_5", "do_6", "do_7", "do_8"}


def _conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ciclo_lavado_estado (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            comando TEXT,
            activo INTEGER NOT NULL DEFAULT 0,
            etapa_actual INTEGER,
            etapa_inicio TEXT
        )
    """)
    con.commit()
    return con


def etapas() -> list:
    """[{"reles": ["do_1","do_2"], "duracion_s": 180}, ...] -- [] si el
    tambo todavía no configuró ninguna etapa."""
    try:
        with open(_RUTA_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def guardar_etapas(nuevas: list) -> None:
    if len(nuevas) > MAX_ETAPAS:
        raise ValueError(f"Como mucho {MAX_ETAPAS} etapas.")
    limpio = []
    for i, etapa in enumerate(nuevas, start=1):
        reles = list((etapa or {}).get("reles") or [])
        if not reles:
            raise ValueError(f"Etapa {i}: elegí al menos un relé.")
        for r in reles:
            if r not in RELES_VALIDOS:
                raise ValueError(f"Etapa {i}: relé desconocido {r!r}.")
        try:
            duracion = int((etapa or {}).get("duracion_s"))
        except (TypeError, ValueError):
            raise ValueError(f"Etapa {i}: la duración tiene que ser un número de segundos.")
        if duracion <= 0:
            raise ValueError(f"Etapa {i}: la duración tiene que ser mayor a 0.")
        limpio.append({"reles": reles, "duracion_s": duracion})
    with _lock:
        with open(_RUTA_CONFIG, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=2)


def estado() -> dict:
    """Estado del ciclo automático AHORA MISMO, para la pantalla ESP32.

    `progreso_pct` es el % transcurrido del ciclo COMPLETO (suma de las
    duraciones de todas las etapas configuradas), calculado acá -- no manda
    timestamps para que la pantalla los reste, así no depende de que su
    reloj esté sincronizado con el de esta PC."""
    programa = etapas()
    con = _conectar_db()
    try:
        fila = con.execute(
            "SELECT activo, etapa_actual, etapa_inicio FROM ciclo_lavado_estado WHERE id = 1"
        ).fetchone()
    finally:
        con.close()
    if not fila or not fila[0]:
        return {"activo": False, "etapas_configuradas": len(programa)}

    etapa_actual, etapa_inicio = fila[1], fila[2]
    duracion_total_s = sum(e["duracion_s"] for e in programa)
    transcurrido_previas_s = sum(e["duracion_s"] for e in programa[:etapa_actual])
    duracion_etapa_s = programa[etapa_actual]["duracion_s"] if etapa_actual < len(programa) else 0
    try:
        transcurrido_etapa_s = (datetime.datetime.now()
                                 - datetime.datetime.fromisoformat(etapa_inicio)).total_seconds()
    except (TypeError, ValueError):
        transcurrido_etapa_s = 0.0
    transcurrido_etapa_s = max(0.0, min(transcurrido_etapa_s, duracion_etapa_s or 0))

    progreso_pct = 0.0
    if duracion_total_s:
        progreso_pct = min(100.0, (transcurrido_previas_s + transcurrido_etapa_s) / duracion_total_s * 100)

    return {
        "activo": True,
        "etapa_actual": etapa_actual,
        "etapa_inicio": etapa_inicio,
        "etapas_total": len(programa),
        "progreso_pct": round(progreso_pct, 1),
    }


def solicitar_inicio() -> bool:
    """True si el pedido de arranque quedó registrado. False si no hay
    ninguna etapa configurada, o si ya hay un ciclo corriendo -- en
    cualquiera de los dos casos no se toca nada."""
    if not etapas():
        return False
    con = _conectar_db()
    try:
        fila = con.execute("SELECT activo FROM ciclo_lavado_estado WHERE id = 1").fetchone()
        if fila and fila[0]:
            return False
        con.execute(
            "INSERT INTO ciclo_lavado_estado (id, comando, activo) VALUES (1, 'iniciar', 0) "
            "ON CONFLICT(id) DO UPDATE SET comando = 'iniciar'"
        )
        con.commit()
    finally:
        con.close()
    return True


def solicitar_cancelacion() -> None:
    con = _conectar_db()
    try:
        con.execute(
            "INSERT INTO ciclo_lavado_estado (id, comando, activo) VALUES (1, 'cancelar', 0) "
            "ON CONFLICT(id) DO UPDATE SET comando = 'cancelar'"
        )
        con.commit()
    finally:
        con.close()
