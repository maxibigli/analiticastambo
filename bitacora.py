# -*- coding: utf-8 -*-
"""Bitácora de incidentes y reparaciones: la carga rápida que hace cualquier
empleado en el celular cuando algo se rompe o se arregla, en el momento —a
diferencia del Check-list de control, que es una planilla de inspección con
frecuencia fija (por ordeñe/diaria/semanal). Acá no hay agenda: un empleado
puede cargar un registro a cualquier hora, sobre cualquier equipo.

DÓNDE SE GUARDA, Y POR QUÉ NO EN DDM. En una base SQLite propia
(`bitacora.db`), mismo criterio que `checklist.py`/`podal.py`/
`iot_monitoreo.py`: DDM es de solo lectura para esta app y además es la base
de DeLaval, se pierde en el próximo restore. Estado propio de cada
instalación, no va al repo (ver `.gitignore`).

PARA QUÉ SIRVE, más allá del registro en sí: `abiertos_por_puesto` la cruza
con la alerta de incidencias de la rotativa (ver
`app.py::_lineas_alertas_puntuales`) — si el puesto 21 ya tiene un reporte
abierto, la alerta dice "ya reportado" en vez de repetirlo como si fuera
nuevo cada vez que se dispara.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import threading
import uuid

_DIR = os.path.dirname(__file__)
_DB_PATH = os.path.join(_DIR, "bitacora.db")
DIR_FOTOS = os.path.join(_DIR, "bitacora_fotos")

_db_lock = threading.Lock()

TIPOS = ("incidente", "reparacion", "mantenimiento")

# Mismo tope que checklist.py: la foto ya viene redimensionada del celular
# (ver bitacora.html), esto es la red de contención del servidor.
MAX_BYTES_FOTO = 3 * 1024 * 1024
TIPOS_FOTO = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _ahora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("""
        CREATE TABLE IF NOT EXISTS registro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tambo TEXT NOT NULL,
            fecha TEXT NOT NULL,
            tipo TEXT NOT NULL,
            sector TEXT NOT NULL,
            puesto INTEGER,
            descripcion TEXT NOT NULL,
            usuario TEXT NOT NULL,
            creado_en TEXT NOT NULL,
            -- Lo genera el celular. Si el envío se reintenta al volver la
            -- señal (misma cola offline que checklist.html), el UNIQUE evita
            -- que el mismo registro entre dos veces.
            offline_id TEXT NOT NULL UNIQUE,
            resuelto_en TEXT,
            resuelto_por TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_registro_tambo_fecha ON registro(tambo, fecha)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_registro_abiertos ON registro(tambo, resuelto_en)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS registro_foto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registro_id INTEGER NOT NULL REFERENCES registro(id),
            archivo TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            creada_en TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_foto_registro ON registro_foto(registro_id)")
    return con


def crear_registro(tambo: str, tipo: str, sector: str, puesto, descripcion: str,
                   usuario: str, fecha: str, offline_id: str, fotos: dict | None = None) -> dict:
    """Guarda un registro nuevo. ATÓMICO a propósito, mismo motivo que
    `checklist.guardar_corrida`: el registro y sus fotos entran juntos o no
    entra nada, porque del otro lado hay una cola que reintenta.

    `fotos`: [(nombre_archivo, bytes), ...] — ya validadas por el llamador
    (ver `validar_foto`). `offline_id`: si el mismo registro llega dos veces,
    la segunda se ignora y se devuelve la que ya estaba (`duplicado=True`).
    """
    tipo = str(tipo or "").strip().lower()
    if tipo not in TIPOS:
        raise ValueError(f"Tipo inválido: {tipo} (opciones: {', '.join(TIPOS)})")
    sector = str(sector or "").strip()
    if not sector:
        raise ValueError("Falta el sector o equipo")
    descripcion = str(descripcion or "").strip()
    if not descripcion:
        raise ValueError("Falta la descripción de qué pasó")
    if not str(offline_id or "").strip():
        raise ValueError("Falta el identificador del registro (offline_id)")
    try:
        datetime.date.fromisoformat(fecha)
    except (TypeError, ValueError):
        raise ValueError(f"Fecha inválida: {fecha}")
    puesto_limpio = None
    if str(puesto or "").strip():
        try:
            puesto_limpio = int(puesto)
        except (TypeError, ValueError):
            raise ValueError(f"Puesto inválido: {puesto}")

    fotos = fotos or []
    with _db_lock, _conectar() as con:
        ya = con.execute("SELECT id FROM registro WHERE offline_id = ?", (offline_id,)).fetchone()
        if ya:
            return {"id": ya["id"], "duplicado": True}
        cur = con.execute(
            "INSERT INTO registro (tambo, fecha, tipo, sector, puesto, descripcion, usuario, "
            "creado_en, offline_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tambo, fecha, tipo, sector, puesto_limpio, descripcion, usuario, _ahora(), offline_id))
        rid = cur.lastrowid
        for nombre, datos in fotos:
            ruta_rel = _escribir_foto(nombre, datos)
            con.execute("INSERT INTO registro_foto (registro_id, archivo, bytes, creada_en) "
                        "VALUES (?, ?, ?, ?)", (rid, ruta_rel, len(datos), _ahora()))
    return {"id": rid, "duplicado": False, "fotos": len(fotos)}


def resolver(registro_id: int, usuario: str) -> bool:
    """Marca un registro como resuelto. True si existía y estaba abierto."""
    with _db_lock, _conectar() as con:
        fila = con.execute("SELECT resuelto_en FROM registro WHERE id = ?", (registro_id,)).fetchone()
        if fila is None or fila["resuelto_en"] is not None:
            return False
        con.execute("UPDATE registro SET resuelto_en = ?, resuelto_por = ? WHERE id = ?",
                    (_ahora(), usuario, registro_id))
    return True


def _con_fotos(con: sqlite3.Connection, filas) -> list:
    salida = []
    for r in filas:
        fs = con.execute("SELECT id FROM registro_foto WHERE registro_id = ?",
                         (r["id"],)).fetchall()
        d = dict(r)
        d["fotos"] = [f["id"] for f in fs]
        d["abierto"] = d["resuelto_en"] is None
        salida.append(d)
    return salida


def abiertos(tambo: str) -> list:
    """Los registros sin resolver, más nuevos primero -- lo que muestra la
    bitácora arriba de todo y lo que consulta el resumen de alertas."""
    with _db_lock, _conectar() as con:
        filas = con.execute(
            "SELECT * FROM registro WHERE tambo = ? AND resuelto_en IS NULL "
            "ORDER BY fecha DESC, creado_en DESC", (tambo,)).fetchall()
        return _con_fotos(con, filas)


def abiertos_por_puesto(tambo: str) -> dict:
    """{puesto: fecha_iso del reporte más viejo todavía abierto} -- para que
    la alerta de incidencias de la rotativa (ver
    app.py::_lineas_alertas_puntuales) diga "ya reportado" en vez de
    repetirlo como si fuera nuevo. Solo entran los registros CON puesto
    cargado (los de sectores sin numerar, como Usher o Piatinero, no tienen
    cómo cruzarse con un puesto)."""
    salida: dict = {}
    for r in abiertos(tambo):
        p = r.get("puesto")
        if p is None:
            continue
        if p not in salida or r["fecha"] < salida[p]:
            salida[p] = r["fecha"]
    return salida


def registros(tambo: str, desde: str, hasta: str) -> list:
    """Todos los registros del rango (abiertos y resueltos), más nuevos
    primero."""
    with _db_lock, _conectar() as con:
        filas = con.execute(
            "SELECT * FROM registro WHERE tambo = ? AND fecha BETWEEN ? AND ? "
            "ORDER BY fecha DESC, creado_en DESC", (tambo, desde, hasta)).fetchall()
        return _con_fotos(con, filas)


def validar_foto(nombre: str, tipo: str, datos: bytes) -> str:
    """Devuelve la extensión a usar, o levanta ValueError. Mismo criterio que
    checklist.validar_foto: el archivo no se guarda con el nombre que mandó
    el cliente, así que no hay forma de escribir fuera de DIR_FOTOS."""
    if not datos:
        raise ValueError(f"La foto {nombre} llegó vacía")
    if len(datos) > MAX_BYTES_FOTO:
        raise ValueError(f"La foto {nombre} pesa {len(datos) // 1024} KB, el máximo son "
                         f"{MAX_BYTES_FOTO // 1024} KB")
    ext = TIPOS_FOTO.get((tipo or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError(f"La foto {nombre} no es una imagen soportada ({tipo})")
    return ext


def _escribir_foto(nombre_con_ext: str, datos: bytes) -> str:
    hoy = datetime.date.today()
    sub = os.path.join(f"{hoy.year:04d}", f"{hoy.month:02d}")
    os.makedirs(os.path.join(DIR_FOTOS, sub), exist_ok=True)
    ruta_rel = os.path.join(sub, nombre_con_ext).replace("\\", "/")
    with open(os.path.join(DIR_FOTOS, ruta_rel), "wb") as f:
        f.write(datos)
    return ruta_rel


def nombre_de_foto(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def ruta_de_foto(foto_id: int) -> tuple | None:
    """(ruta absoluta, nombre) de una foto, o None si no existe. La ruta se
    arma desde DIR_FOTOS con lo guardado en la base, nunca con algo que venga
    del request."""
    with _db_lock, _conectar() as con:
        fila = con.execute("SELECT archivo FROM registro_foto WHERE id = ?", (foto_id,)).fetchone()
    if not fila:
        return None
    ruta = os.path.normpath(os.path.join(DIR_FOTOS, fila["archivo"]))
    if not ruta.startswith(os.path.normpath(DIR_FOTOS)) or not os.path.exists(ruta):
        return None
    return ruta, os.path.basename(ruta)
