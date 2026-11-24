"""Informes personalizables guardados con nombre.

SQLite propio (`informes.db`, fuera de git), una tabla, por tambo. Nunca en DDM: es la base de DeLaval, de solo lectura, y lo que
se escriba ahí se pierde en el próximo restore (mismo criterio que `checklist.db`).

Se guarda solo la DEFINICIÓN del informe (columnas, condiciones, orden...), nunca SQL ni resultados, y solo si el compilador
(`informes_motor.compilar`) la acepta: así lo que está guardado siempre se puede volver a correr.
"""
import contextlib
import datetime
import json
import os
import re
import sqlite3

import informes_motor
import informes_serie

RUTA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes.db")
NOMBRE_MAX = 60
CLAVES_DEFINICION = ("columnas", "calculados", "agrupar_por", "detalle", "condiciones", "orden", "incluir_descartados", "limite", "grafico")


class InformeGuardadoError(ValueError):
    """Nombre o definición inválidos, nombre repetido o informe inexistente. El mensaje se le muestra al usuario."""


def _ahora() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def _con():
    """Una conexión que SIEMPRE se cierra al salir (en Windows un SQLite abierto traba el archivo) y confirma los cambios."""
    con = sqlite3.connect(RUTA_DB)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        _crear_tabla(con)
        with con:
            yield con
    finally:
        con.close()


def _crear_tabla(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS informes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tambo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        nombre_norm TEXT NOT NULL,
        definicion TEXT NOT NULL,
        favorito INTEGER NOT NULL DEFAULT 0,
        creado TEXT NOT NULL,
        modificado TEXT NOT NULL,
        usuario TEXT,
        UNIQUE (tambo, nombre_norm))""")


def _nombre(nombre) -> tuple:
    if not isinstance(nombre, str):
        raise InformeGuardadoError("El informe necesita un nombre.")
    if any(ord(c) < 32 for c in nombre):
        raise InformeGuardadoError("El nombre no puede tener saltos de línea ni caracteres de control.")
    limpio = re.sub(r"\s+", " ", nombre).strip()
    if not limpio:
        raise InformeGuardadoError("El informe necesita un nombre.")
    if len(limpio) > NOMBRE_MAX:
        raise InformeGuardadoError(f"El nombre es demasiado largo (hasta {NOMBRE_MAX} letras).")
    return limpio, limpio.casefold()


def _definicion(defn) -> dict:
    if not isinstance(defn, dict):
        raise InformeGuardadoError("La definición del informe no es válida.")
    limpia = {k: defn[k] for k in CLAVES_DEFINICION if k in defn}
    try:
        informes_motor.compilar(limpia)
        if limpia.get("grafico") is None:
            limpia.pop("grafico", None)                      # sin gráfico configurado: no se guarda nada
        else:
            limpia["grafico"] = informes_serie.validar(limpia["grafico"])       # completa y limpia, y que se pueda volver a correr
    except (informes_motor.InformeError, informes_serie.SerieError) as e:
        raise InformeGuardadoError(str(e)) from e
    return limpia


def _fila(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "nombre": r["nombre"], "favorito": bool(r["favorito"]), "creado": r["creado"],
            "modificado": r["modificado"], "usuario": r["usuario"], "definicion": json.loads(r["definicion"])}


def listar(tambo: str) -> list:
    """Lo justo para la lista: sin la definición. Favoritos primero, después por nombre."""
    with _con() as con:
        filas = con.execute("SELECT id, nombre, favorito, modificado, definicion FROM informes WHERE tambo = ? "
                            "ORDER BY favorito DESC, nombre_norm", (tambo,)).fetchall()
    return [{"id": r["id"], "nombre": r["nombre"], "favorito": bool(r["favorito"]), "modificado": r["modificado"],
             "n_columnas": len(json.loads(r["definicion"]).get("columnas", []))} for r in filas]


def obtener(tambo: str, id_: int):
    with _con() as con:
        r = con.execute("SELECT * FROM informes WHERE id = ? AND tambo = ?", (id_, tambo)).fetchone()
    return _fila(r) if r else None


def crear(tambo: str, nombre, definicion, usuario, favorito: bool = False) -> dict:
    nombre, norm = _nombre(nombre)
    defn = _definicion(definicion)
    ahora = _iso(_ahora())
    try:
        with _con() as con:
            cur = con.execute("INSERT INTO informes (tambo, nombre, nombre_norm, definicion, favorito, creado, modificado, usuario) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (tambo, nombre, norm, json.dumps(defn, ensure_ascii=False), int(bool(favorito)), ahora, ahora, usuario))
            nuevo = cur.lastrowid
    except sqlite3.IntegrityError:
        raise InformeGuardadoError(f"Ya hay un informe llamado «{nombre}». Elegí otro nombre.") from None
    return obtener(tambo, nuevo)


def actualizar(tambo: str, id_: int, nombre=None, definicion=None, favorito=None) -> dict:
    actual = obtener(tambo, id_)
    if actual is None:
        raise InformeGuardadoError("Ese informe no existe.")
    n, norm = (actual["nombre"], actual["nombre"].casefold()) if nombre is None else _nombre(nombre)
    defn = actual["definicion"] if definicion is None else _definicion(definicion)
    fav = actual["favorito"] if favorito is None else bool(favorito)
    try:
        with _con() as con:
            con.execute("UPDATE informes SET nombre = ?, nombre_norm = ?, definicion = ?, favorito = ?, modificado = ? "
                        "WHERE id = ? AND tambo = ?",
                        (n, norm, json.dumps(defn, ensure_ascii=False), int(fav), _iso(_ahora()), id_, tambo))
    except sqlite3.IntegrityError:
        raise InformeGuardadoError(f"Ya hay un informe llamado «{n}». Elegí otro nombre.") from None
    return obtener(tambo, id_)
