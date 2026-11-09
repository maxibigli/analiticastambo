# -*- coding: utf-8 -*-
"""Registro de la UPS del tambo: guarda las lecturas que el AGENTE del tambo manda por HTTPS y reconstruye los cortes.

Por qué así: la tarjeta de red de la UPS está dentro de la red del tambo, sin IP pública y sin abrir puertos. Entonces la
conexión va siempre de adentro hacia afuera: `ups_agente.py` (en una PC del tambo) lee la tarjeta por SNMP y EMPUJA las
lecturas a `POST /api/ups/ingesta`; este módulo las guarda en `ups.db` (SQLite propio, fuera del repo).

Garantías que pesan:
  * Idempotente: el agente reintenta si se cortó internet y puede mandar dos veces la misma lectura (clave tambo + hora).
  * Las horas se guardan en UTC y se EXIGE zona horaria: una hora sin zona no se sabe de cuándo es.
  * Una lectura puede ser un ERROR («la tarjeta no respondió»): demuestra que el agente está vivo aunque la UPS no conteste.
    Es distinto de «no llega nada» (agente apagado, sin internet, corte de luz): eso lo dice `estado()` como `sin_datos`.
  * Los cortes (la salida pasa a batería) se reconstruyen de las lecturas EN ORDEN; un lote atrasado no los toca.
  * Solo se guarda lo que cambia a cada rato (no la identidad ni la configuración de la UPS) y las lecturas se podan.
"""
import datetime
import json
import os
import sqlite3

RUTA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ups.db")
MAX_LOTE = 200                      # lecturas por pedido; el agente manda de a tandas
SIN_DATOS_S = 180                   # sin lecturas hace más de esto = «sin datos» (el agente lee cada 30-60 s)
TOLERANCIA_RELOJ = datetime.timedelta(minutes=10)    # hora «del futuro» tolerada (relojes desfasados)
_FORMATO = "%Y-%m-%dT%H:%M:%SZ"


class IngestaError(ValueError):
    """El pedido entero es inválido (no una lectura suelta): se le muestra al agente."""


def _ahora(ahora):
    return ahora or datetime.datetime.now(datetime.timezone.utc)


def _conectar(ruta):
    con = sqlite3.connect(ruta or RUTA_DB, timeout=15, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS ups_lecturas (
        tambo TEXT NOT NULL, ts TEXT NOT NULL, ok INTEGER NOT NULL, en_bateria INTEGER, error TEXT, datos TEXT,
        PRIMARY KEY (tambo, ts))""")
    con.execute("""CREATE TABLE IF NOT EXISTS ups_cortes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tambo TEXT NOT NULL, inicio TEXT NOT NULL, fin TEXT,
        carga_minima_pct REAL, segundos_en_bateria INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ups_cortes ON ups_cortes (tambo, inicio)")
    return con


def normalizar_ts(valor, ahora=None):
    """Hora ISO con zona -> 'YYYY-MM-DDTHH:MM:SSZ' en UTC, o None si no sirve (ilegible, sin zona o del futuro)."""
    if not isinstance(valor, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(valor.strip())
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    t = t.astimezone(datetime.timezone.utc)
    if t > _ahora(ahora) + TOLERANCIA_RELOJ:
        return None
    return t.strftime(_FORMATO)


def _parse(ts):
    return datetime.datetime.strptime(ts, _FORMATO).replace(tzinfo=datetime.timezone.utc)


def _compactar(ups):
    """Lo que cambia de una lectura a otra. La identidad y la configuración de la UPS no se repiten en cada minuto."""
    return {k: v for k, v in ups.items() if k not in ("identidad", "config")}


def _lectura_valida(l, ahora):
    if not isinstance(l, dict):
        return None
    ts = normalizar_ts(l.get("ts"), ahora)
    ups, error = l.get("ups"), l.get("error")
    if ts is None:
        return None
    if isinstance(ups, dict) and ups.get("disponible"):
        return ts, _compactar(ups), None
    if isinstance(error, str) and error.strip():
        return ts, None, error.strip()[:300]
    return None


def _corte(con, tambo, ts, ups):
    """Actualiza los cortes con una lectura buena que es la más nueva del tambo."""
    abierto = con.execute("SELECT id, carga_minima_pct, segundos_en_bateria FROM ups_cortes "
                          "WHERE tambo=? AND fin IS NULL ORDER BY inicio DESC LIMIT 1", (tambo,)).fetchone()
    carga = (ups.get("bateria") or {}).get("carga_pct")
    seg = (ups.get("bateria") or {}).get("segundos_en_bateria")
    if ups.get("en_bateria"):
        if abierto is None:
            con.execute("INSERT INTO ups_cortes (tambo, inicio, carga_minima_pct, segundos_en_bateria) VALUES (?,?,?,?)",
                        (tambo, ts, carga, seg))
        else:
            minima = min([c for c in (abierto[1], carga) if c is not None], default=None)
            maxima = max([s for s in (abierto[2], seg) if s is not None], default=None)
            con.execute("UPDATE ups_cortes SET carga_minima_pct=?, segundos_en_bateria=? WHERE id=?",
                        (minima, maxima, abierto[0]))
    elif abierto is not None:
        con.execute("UPDATE ups_cortes SET fin=? WHERE id=?", (ts, abierto[0]))


def guardar(tambo, lecturas, ruta=None, ahora=None):
    """Guarda un lote de lecturas. Devuelve {'guardadas', 'repetidas', 'rechazadas'}."""
    if not isinstance(lecturas, list):
        raise IngestaError("«lecturas» tiene que ser una lista.")
    if len(lecturas) > MAX_LOTE:
        raise IngestaError(f"Demasiadas lecturas en un pedido ({len(lecturas)}; el máximo es {MAX_LOTE}).")
    ahora = _ahora(ahora)
    validas, rechazadas = [], 0
    for l in lecturas:
        v = _lectura_valida(l, ahora)
        if v is None:
            rechazadas += 1
        else:
            validas.append(v)
    validas.sort(key=lambda v: v[0])
    guardadas = repetidas = 0
    con = _conectar(ruta)
    try:
        con.execute("BEGIN IMMEDIATE")
        ultimo = con.execute("SELECT MAX(ts) FROM ups_lecturas WHERE tambo=? AND ok=1", (tambo,)).fetchone()[0]
        for ts, ups, error in validas:
            cur = con.execute(
                "INSERT OR IGNORE INTO ups_lecturas (tambo, ts, ok, en_bateria, error, datos) VALUES (?,?,?,?,?,?)",
                (tambo, ts, 1 if ups else 0, (1 if ups.get("en_bateria") else 0) if ups else None, error,
                 json.dumps(ups, ensure_ascii=False, separators=(",", ":")) if ups else None))
            if cur.rowcount == 0:
                repetidas += 1
                continue
            guardadas += 1
            if ups and (ultimo is None or ts > ultimo):          # los cortes se siguen en orden: un lote viejo no los toca
                _corte(con, tambo, ts, ups)
                ultimo = ts
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return {"guardadas": guardadas, "repetidas": repetidas, "rechazadas": rechazadas}


def _fila(f):
    return {"ts": f[0], "ok": bool(f[1]), "error": f[2], "ups": json.loads(f[3]) if f[3] else None}


def ultima(tambo, ruta=None):
    con = _conectar(ruta)
    try:
        f = con.execute("SELECT ts, ok, error, datos FROM ups_lecturas WHERE tambo=? ORDER BY ts DESC LIMIT 1",
                        (tambo,)).fetchone()
    finally:
        con.close()
    return _fila(f) if f else None


def estado(tambo, ruta=None, ahora=None):
    """Lo último que se sabe de la UPS y si sigue siendo actual. `sin_datos` = el agente dejó de reportar."""
    u = ultima(tambo, ruta=ruta)
    if u is None:
        return {"hay_datos": False, "sin_datos": True, "en_corte": False}
    edad = int((_ahora(ahora) - _parse(u["ts"])).total_seconds())
    con = _conectar(ruta)
    try:
        en_corte = con.execute("SELECT 1 FROM ups_cortes WHERE tambo=? AND fin IS NULL", (tambo,)).fetchone() is not None
    finally:
        con.close()
    return {"hay_datos": True, "ts": u["ts"], "edad_s": edad, "sin_datos": edad > SIN_DATOS_S, "ok": u["ok"],
            "error": u["error"], "ups": u["ups"], "en_corte": en_corte}


def historico(tambo, desde, hasta, max_puntos=300, ruta=None):
    """Lecturas buenas entre dos horas ISO (UTC), de menor a mayor, reducidas a `max_puntos` conservando los extremos."""
    con = _conectar(ruta)
    try:
        filas = con.execute("SELECT ts, ok, error, datos FROM ups_lecturas WHERE tambo=? AND ok=1 AND ts>=? AND ts<=? "
                            "ORDER BY ts", (tambo, desde, hasta)).fetchall()
    finally:
        con.close()
    if max_puntos >= 2 and len(filas) > max_puntos:
        n = len(filas)
        filas = [filas[i] for i in sorted({round(k * (n - 1) / (max_puntos - 1)) for k in range(max_puntos)})]
    return [_fila(f) for f in filas]


def cortes(tambo, limite=50, ruta=None):
    con = _conectar(ruta)
    try:
        filas = con.execute("SELECT inicio, fin, carga_minima_pct, segundos_en_bateria FROM ups_cortes WHERE tambo=? "
                            "ORDER BY inicio DESC LIMIT ?", (tambo, limite)).fetchall()
    finally:
        con.close()
    return [{"inicio": i, "fin": f, "carga_minima_pct": c, "segundos_en_bateria": s,
             "duracion_s": int((_parse(f) - _parse(i)).total_seconds()) if f else None} for i, f, c, s in filas]


def podar(dias=90, ruta=None, ahora=None):
    """Borra las lecturas más viejas que `dias`. Los cortes se conservan: son poco y son la historia."""
    corte = (_ahora(ahora) - datetime.timedelta(days=dias)).strftime(_FORMATO)
    con = _conectar(ruta)
    try:
        return con.execute("DELETE FROM ups_lecturas WHERE ts<?", (corte,)).rowcount
    finally:
        con.close()
