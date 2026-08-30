# -*- coding: utf-8 -*-
"""Interpreta el texto transcripto de un comando de voz (ver
delpro-analitica/docs/superpowers/specs/2026-08-29-comandos-voz-jarvis-design.md)
contra un vocabulario CERRADO y CHICO: frases fijas de Lavado Automático +
"prender/encender/apagar <nombre de actuador>", usando los nombres que el
tambo configuró en iot_canales (si le cambia el nombre a una salida, esto
se adapta solo, sin tocar código).

También guarda el estado de los actuadores SOSTENIDOS por voz (distinto
del pulso de 0,5s que ya usa el panel de Actuadores, que sigue igual) --
la ejecución real de Modbus la hace iot_lavado.procesar_comandos_voz.

Deliberadamente SIN import de iot_lavado (que sí importa este módulo),
mismo criterio que lavado_programa.py/iot_conexion.py. SÍ importa
lavado_programa (que a su vez tampoco importa iot_lavado) para saber si un
actuador está en uso por la etapa activa del ciclo automático."""
import datetime
import difflib
import sqlite3
import threading

import iot_canales
import lavado_programa

RUTA_DB = "iot_sensores.db"
UMBRAL_CONFIANZA = 0.72
ACTUADORES_VALIDOS = {"do_1", "do_2", "do_3", "do_4", "do_5", "do_6", "do_7", "do_8"}

FRASES_INICIAR = ["iniciar lavado", "arrancar lavado", "empezar lavado", "iniciar el lavado"]
FRASES_CANCELAR = ["cancelar", "cancelar lavado", "parar", "detener", "detener lavado"]

_lock = threading.Lock()


def _conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS voz_actuadores_estado (
            clave TEXT PRIMARY KEY,
            encendido_desde TEXT NOT NULL
        )
    """)
    con.commit()
    return con


def _normalizar(texto: str) -> str:
    return " ".join((texto or "").strip().lower().split())


def _candidatos():
    """[(frase_normalizada, tipo, datos), ...] -- datos es (clave, prender)
    para tipo == "actuador", None para el resto."""
    candidatos = [(f, "lavado_iniciar", None) for f in FRASES_INICIAR]
    candidatos += [(f, "lavado_cancelar", None) for f in FRASES_CANCELAR]
    nombres = iot_canales.nombres()
    for clave in sorted(ACTUADORES_VALIDOS):
        nombre = nombres.get(clave)
        if not nombre:
            continue   # sin nombre propio, no es natural decirlo en voz alta
        nombre_norm = _normalizar(nombre)
        candidatos.append((f"prender {nombre_norm}", "actuador", (clave, True)))
        candidatos.append((f"encender {nombre_norm}", "actuador", (clave, True)))
        candidatos.append((f"apagar {nombre_norm}", "actuador", (clave, False)))
    return candidatos


def interpretar(texto: str) -> dict:
    """{"tipo": "lavado_iniciar"|"lavado_cancelar"|"actuador"|"desconocido",
    "clave": ..., "prender": ...} -- las dos últimas solo si tipo == "actuador"."""
    texto_norm = _normalizar(texto)
    if not texto_norm:
        return {"tipo": "desconocido"}
    candidatos = _candidatos()
    frases = [c[0] for c in candidatos]
    mejor = difflib.get_close_matches(texto_norm, frases, n=1, cutoff=UMBRAL_CONFIANZA)
    if not mejor:
        return {"tipo": "desconocido"}
    _, tipo, datos = next(c for c in candidatos if c[0] == mejor[0])
    if tipo == "actuador":
        clave, prender = datos
        return {"tipo": "actuador", "clave": clave, "prender": prender}
    return {"tipo": tipo}


def _en_uso_por_lavado(clave: str) -> bool:
    estado_lavado = lavado_programa.estado()
    if not estado_lavado.get("activo"):
        return False
    programa = lavado_programa.etapas()
    etapa_actual = estado_lavado["etapa_actual"]
    if etapa_actual >= len(programa):
        return False
    return clave in programa[etapa_actual]["reles"]


def estado() -> dict:
    """clave -> encendido_desde (ISO) para lo sostenido por voz ahora mismo."""
    con = _conectar_db()
    try:
        filas = con.execute("SELECT clave, encendido_desde FROM voz_actuadores_estado").fetchall()
    finally:
        con.close()
    return dict(filas)


def solicitar_encendido(clave: str) -> bool:
    """True si quedó registrado. False si ese actuador está en uso por una
    etapa activa de Lavado Automático (se ignora, no se toca nada)."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        con = _conectar_db()
        try:
            con.execute(
                "INSERT INTO voz_actuadores_estado (clave, encendido_desde) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET encendido_desde = excluded.encendido_desde",
                (clave, ahora),
            )
            con.commit()
        finally:
            con.close()
    return True


def solicitar_apagado(clave: str) -> bool:
    """Mismo criterio que solicitar_encendido: False (ignorado) si está en
    uso por el lavado automático."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    limpiar_estado(clave)
    return True


def limpiar_estado(clave: str) -> None:
    """Borra el estado sostenido de `clave` SIN chequear si está en uso por
    el lavado -- lo llama el propio motor de Lavado Automático
    (iot_lavado.procesar_ciclo_lavado) cuando apaga un relé como parte de
    su propia secuencia, para que la próxima vuelta de
    procesar_comandos_voz no intente prenderlo de nuevo."""
    with _lock:
        con = _conectar_db()
        try:
            con.execute("DELETE FROM voz_actuadores_estado WHERE clave = ?", (clave,))
            con.commit()
        finally:
            con.close()
