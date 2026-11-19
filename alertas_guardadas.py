# -*- coding: utf-8 -*-
"""Copia local de las alertas que detecta el sistema de alimentación (hoy MixerOne).

POR QUÉ: no se sabe si MixerOne guarda el historial de alertas para siempre, y empezaron a generarse el 04/11/2026: con
15 días no alcanza para saber si anticipan caídas de leche (hacen falta 6 a 8 semanas). Si MixerOne las depurara, lo que
no se copió se pierde. Esta copia se hace sola, sin depender de que alguien abra la pantalla.

SE GUARDA TODO, CRUDO: cada alerta queda con los campos neutrales (los que usa `operarios.py`) y además el JSON original
completo de MixerOne, para poder rehacer cualquier análisis sin volver a pedirlo.

CÓMO SE SINCRONIZA: la primera vez de cada tambo trae todo el historial que haya (`INICIAL_DIAS` hacia atrás) y las
siguientes repasan los últimos `REPASO_DIAS` días, porque una alerta puede llegar tarde o cambiar (por ejemplo cuando
alguien la marca como leída en el portal) y `visto_ultimo` dice hasta cuándo siguió figurando en MixerOne. Una alerta que
MixerOne deja de devolver NO se borra: es justamente lo que esta copia protege. Es idempotente: sincronizar dos veces no
duplica nada.

Estado propio de cada instalación (`alertas_mixerone.db` está en `.gitignore`), como `vigilante.db`. Solo guarda lo que
MixerOne devuelve; no escribe nada en MixerOne.
"""
import contextlib
import datetime
import json
import logging
import os
import sqlite3
import threading

log = logging.getLogger("alertas_guardadas")

RUTA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alertas_mixerone.db")

INICIAL_DIAS = 180       # cuánto historial se pide la primera vez
REPASO_DIAS = 3          # cuánto se repasa en cada sincronización
CADA_HORAS = 6           # cada cuánto sincroniza el hilo (una pasada son 1 o 2 pedidos: es barato)
PRIMERA_ESPERA_S = 60    # al arrancar el servidor, un minuto de gracia antes de la primera pasada

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS alertas (
    tambo TEXT NOT NULL, id TEXT NOT NULL, codigo TEXT, tipo TEXT, severidad TEXT, mensaje TEXT,
    fecha TEXT, hora TEXT, viaje TEXT, rodeo TEXT, insumo TEXT, accion TEXT, operario TEXT,
    crudo TEXT, visto_primero TEXT NOT NULL, visto_ultimo TEXT NOT NULL,
    PRIMARY KEY (tambo, id));
CREATE INDEX IF NOT EXISTS alertas_por_fecha ON alertas (tambo, fecha);
CREATE TABLE IF NOT EXISTS sincronizaciones (
    tambo TEXT NOT NULL, cuando TEXT NOT NULL, desde TEXT, hasta TEXT,
    leidas INTEGER, nuevas INTEGER, error TEXT, omitido TEXT);
"""
_CAMPOS = ("codigo", "tipo", "severidad", "mensaje", "fecha", "hora", "viaje", "rodeo", "insumo", "accion", "operario")
_lock = threading.Lock()


def _conectar():
    con = sqlite3.connect(RUTA_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(_ESQUEMA)
    return con


@contextlib.contextmanager
def _con():
    """Una conexión que commitea (o deshace) al salir y SIEMPRE se cierra: `with sqlite3.connect()` solo commitea."""
    con = _conectar()
    try:
        with con:
            yield con
    finally:
        con.close()


def _iso(momento: datetime.datetime) -> str:
    return momento.isoformat(timespec="seconds")


def guardar(tambo: str, alertas, ahora: datetime.datetime = None) -> dict:
    """Guarda (o actualiza) las alertas. `{"leidas", "nuevas"}`."""
    ahora = ahora or datetime.datetime.now()
    visto = _iso(ahora)
    alertas = list(alertas)
    nuevas = 0
    with _lock, _con() as con:
        for a in alertas:
            id_ = str(a["id"])
            crudo = json.dumps(a.get("crudo"), ensure_ascii=False)
            valores = [a.get(c) for c in _CAMPOS]
            if con.execute("SELECT 1 FROM alertas WHERE tambo = ? AND id = ?", (tambo, id_)).fetchone():
                con.execute("UPDATE alertas SET " + ", ".join(f"{c} = ?" for c in _CAMPOS) +
                            ", crudo = ?, visto_ultimo = ? WHERE tambo = ? AND id = ?",
                            [*valores, crudo, visto, tambo, id_])
            else:
                nuevas += 1
                con.execute("INSERT INTO alertas (tambo, id, " + ", ".join(_CAMPOS) +
                            ", crudo, visto_primero, visto_ultimo) VALUES (?, ?, " + ", ".join("?" * len(_CAMPOS)) +
                            ", ?, ?, ?)", [tambo, id_, *valores, crudo, visto, visto])
    return {"leidas": len(alertas), "nuevas": nuevas}


def _registrar(tambo, ahora, desde, hasta, leidas, nuevas, error, omitido=None):
    with _lock, _con() as con:
        con.execute("INSERT INTO sincronizaciones (tambo, cuando, desde, hasta, leidas, nuevas, error, omitido) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (tambo, _iso(ahora), desde and str(desde), hasta and str(hasta), leidas, nuevas, error, omitido))


def _hay_copia(tambo: str) -> bool:
    with _lock, _con() as con:
        return con.execute("SELECT 1 FROM sincronizaciones WHERE tambo = ? AND error IS NULL AND omitido IS NULL "
                           "LIMIT 1", (tambo,)).fetchone() is not None


def sincronizar(tambo: str, proveedor, hoy: datetime.date = None, ahora: datetime.datetime = None) -> dict:
    """Copia las alertas de `proveedor` para `tambo`. NUNCA levanta: devuelve `{"omitido": motivo}` si el proveedor no
    sirve para esto, `{"error": texto}` si falló (queda registrado) o `{"leidas", "nuevas", ...}`."""
    hoy = hoy or datetime.date.today()
    ahora = ahora or datetime.datetime.now()
    nombre = getattr(proveedor, "NOMBRE", "") or "El sistema de alimentación"
    omitido = None
    if not hasattr(proveedor, "alertas"):
        omitido = f"{nombre} no ofrece alertas."
    else:
        ok, motivo = proveedor.disponible() if hasattr(proveedor, "disponible") else (True, "")
        if not ok:
            omitido = motivo or f"{nombre} no está conectado."
    if omitido:
        # Queda a la vista (la pantalla lo muestra): si el tambo no está configurado con MixerOne la copia no hace nada
        # y, sin esto, nadie se enteraría.
        _registrar(tambo, ahora, None, None, 0, 0, None, omitido)
        return {"omitido": omitido}
    desde = hoy - datetime.timedelta(days=REPASO_DIAS if _hay_copia(tambo) else INICIAL_DIAS)
    try:
        lista = proveedor.alertas(desde, hoy, crudo=True)
        r = guardar(tambo, lista, ahora)
    except Exception as exc:  # noqa: BLE001 - una pasada que falla no puede tirar el hilo ni el servidor
        log.warning("copia de alertas de %s: %s: %s", tambo, type(exc).__name__, exc)
        _registrar(tambo, ahora, desde, hoy, 0, 0, f"{type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}
    _registrar(tambo, ahora, desde, hoy, r["leidas"], r["nuevas"], None)
    return {**r, "desde": desde.isoformat(), "hasta": hoy.isoformat()}


def sincronizar_todos(tambos=None, proveedor_de=None, hoy=None, ahora=None) -> dict:
    """`{tambo: resultado de sincronizar}` para cada tambo. Los que no usan un sistema con alertas, o no están
    conectados, se omiten con el motivo."""
    if tambos is None:
        import tambos as _tambos
        tambos = list(_tambos.TAMBOS)
    if proveedor_de is None:
        import proveedores
        proveedor_de = proveedores.de
    salida = {}
    for t in tambos:
        try:
            prov = proveedor_de(t)
        except Exception as exc:  # noqa: BLE001 - un proveedor mal configurado no frena a los demás tambos
            salida[t] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        salida[t] = sincronizar(t, prov, hoy, ahora)
    return salida


def alertas_de(tambo: str, desde: str = None, hasta: str = None) -> list:
    """Las alertas guardadas (con su `crudo` ya como dict), de la más vieja a la más nueva."""
    sql, args = "SELECT * FROM alertas WHERE tambo = ?", [tambo]
    if desde:
        sql, args = sql + " AND fecha >= ?", args + [str(desde)]
    if hasta:
        sql, args = sql + " AND fecha <= ?", args + [str(hasta)]
    with _lock, _con() as con:
        filas = con.execute(sql + " ORDER BY fecha, hora, id", args).fetchall()
    return [{**dict(f), "crudo": json.loads(f["crudo"]) if f["crudo"] else None} for f in filas]


def diarias(tambo: str, desde: str = None, hasta: str = None) -> list:
    """`[{fecha, codigo, n}]`: cuántas alertas de cada tipo hubo cada día. Es la base del análisis contra la leche."""
    sql, args = "SELECT fecha, codigo, COUNT(*) AS n FROM alertas WHERE tambo = ?", [tambo]
    if desde:
        sql, args = sql + " AND fecha >= ?", args + [str(desde)]
    if hasta:
        sql, args = sql + " AND fecha <= ?", args + [str(hasta)]
    with _lock, _con() as con:
        filas = con.execute(sql + " GROUP BY fecha, codigo ORDER BY fecha, codigo", args).fetchall()
    return [dict(f) for f in filas]


def resumen(tambo: str) -> dict:
    """Qué hay copiado: `{total, desde, hasta, ultima_sincronizacion}`."""
    with _lock, _con() as con:
        t, d, h = con.execute("SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM alertas WHERE tambo = ?",
                              (tambo,)).fetchone()
        s = con.execute("SELECT cuando, desde, hasta, leidas, nuevas, error, omitido FROM sincronizaciones WHERE tambo = ? "
                        "ORDER BY rowid DESC LIMIT 1", (tambo,)).fetchone()
    return {"total": t, "desde": d, "hasta": h, "ultima_sincronizacion": dict(s) if s else None}


def iniciar_hilo(parar: threading.Event = None, espera_s: float = None, primera_espera_s: float = None):
    """Arranca el hilo que sincroniza cada `CADA_HORAS` horas (y una vez poco después de arrancar). Cada pasada va en su
    propio try/except. `parar`, `espera_s` y `primera_espera_s` son para las pruebas.

    NO se arranca al importar `app`: lo llama `servidor.py` después de tomar el candado de instancia única, para que
    procesos como `precalentar.py` o las pruebas no tengan cada uno el suyo."""
    parar = parar or threading.Event()

    def bucle():
        parar.wait(PRIMERA_ESPERA_S if primera_espera_s is None else primera_espera_s)
        while not parar.is_set():
            try:
                sincronizar_todos()
            except Exception:  # noqa: BLE001
                log.exception("copia de alertas: la pasada falló")
            parar.wait(CADA_HORAS * 3600 if espera_s is None else espera_s)

    hilo = threading.Thread(target=bucle, name="copia-alertas", daemon=True)
    hilo.start()
    return hilo
