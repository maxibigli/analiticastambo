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
import time

import informes_motor
import informes_serie

RUTA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "informes.db")
NOMBRE_MAX = 60
CLAVES_DEFINICION = ("columnas", "calculados", "agrupar_por", "detalle", "condiciones", "orden", "incluir_descartados", "limite", "grafico")
# La «nota de origen» de un informe traído de DelPro: las diferencias que cambian QUÉ animales aparecen. Es solo texto para mostrar (nunca SQL, nunca parte de la definición).
ORIGEN_TITULO_DEFECTO = "Traído de DelPro"
ORIGEN_TITULO_MAX = 120
ORIGEN_AVISOS_MAX = 12
ORIGEN_TEXTO_MAX = 300
SIN_CAMBIO = object()                        # «no toques la nota de origen» (None quiere decir «quitala»)
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿\ud800-\udfff]")


class InformeGuardadoError(ValueError):
    """Nombre o definición inválidos, nombre repetido o informe inexistente. El mensaje se le muestra al usuario."""


def _ahora() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def activar_wal(con) -> None:
    """Deja el archivo en modo WAL (varios lectores mientras alguien escribe). Pasar a WAL necesita el archivo libre y, a diferencia del resto de las sentencias, NO espera:
    sobre un archivo nuevo, dos pedidos a la vez (la pantalla pide la lista de informes y la de envíos juntas) daban «database is locked». Se reintenta unos instantes, y
    como el modo queda guardado en el archivo, si otro pedido ya lo dejó en WAL no hace falta repetirlo. Si no se logra sigue andando en el modo de siempre."""
    for _ in range(60):
        try:
            if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                return
            con.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            time.sleep(0.05)


@contextlib.contextmanager
def _con():
    """Una conexión que SIEMPRE se cierra al salir (en Windows un SQLite abierto traba el archivo) y confirma los cambios."""
    con = sqlite3.connect(RUTA_DB, timeout=15)
    try:
        con.row_factory = sqlite3.Row
        activar_wal(con)
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
        origen TEXT,
        UNIQUE (tambo, nombre_norm))""")
    # La base de cada instalación ya existe sin la columna: se agrega sola, sin perder nada. Dos pedidos a la vez pueden intentarlo juntos: el segundo no es un error.
    if "origen" not in [c[1] for c in con.execute("PRAGMA table_info(informes)").fetchall()]:
        try:
            con.execute("ALTER TABLE informes ADD COLUMN origen TEXT")
        except sqlite3.OperationalError:
            if "origen" not in [c[1] for c in con.execute("PRAGMA table_info(informes)").fetchall()]:
                raise


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


def validar_definicion(defn) -> dict:
    """La definición LIMPIA (solo las claves conocidas) si el compilador la acepta; `InformeGuardadoError` si no. Es el mismo control con que
    se guarda un informe: lo usan también el importador de DelPro y el armado con lenguaje natural, que no guardan nada."""
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


_definicion = validar_definicion
normalizar_nombre = _nombre            # (nombre limpio, nombre para comparar): el que se guarda y el que no se puede repetir


def _texto_de_origen(valor, maximo: int) -> str:
    """Un texto para mostrar: sin caracteres de control, con los espacios y saltos juntados en uno, y acotado."""
    t = re.sub(r"\s+", " ", _CONTROL.sub("", valor)).strip()
    return t if len(t) <= maximo else t[:maximo - 1].rstrip() + "…"


def normalizar_origen(origen):
    """La nota de origen LIMPIA ({titulo, sub, avisos}) o None si no hay nada que recordar. La arma el navegador, así que nunca se confía: solo texto, acotado, y lo que no sirve se
    ignora en vez de dar un error (guardar el informe no puede fallar por la nota). Sin avisos no hay nota: un informe traído completo no necesita recordar nada."""
    if not isinstance(origen, dict) or not isinstance(origen.get("avisos"), list):
        return None
    avisos = [a for a in (_texto_de_origen(x, ORIGEN_TEXTO_MAX) for x in origen["avisos"] if isinstance(x, str)) if a][:ORIGEN_AVISOS_MAX]
    if not avisos:
        return None
    titulo, sub = (origen.get(k) for k in ("titulo", "sub"))
    return {"titulo": (_texto_de_origen(titulo, ORIGEN_TITULO_MAX) if isinstance(titulo, str) else "") or ORIGEN_TITULO_DEFECTO,
            "sub": _texto_de_origen(sub, ORIGEN_TITULO_MAX) if isinstance(sub, str) else "", "avisos": avisos}


def _leer_origen(crudo):
    """La nota guardada, o None si no hay o está ilegible (un informe nunca deja de abrirse por su nota)."""
    if not crudo:
        return None
    try:
        return normalizar_origen(json.loads(crudo))
    except (ValueError, TypeError):
        return None


def _guardar_origen(origen):
    n = normalizar_origen(origen)
    return json.dumps(n, ensure_ascii=False) if n else None


def _fila(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "nombre": r["nombre"], "favorito": bool(r["favorito"]), "creado": r["creado"],
            "modificado": r["modificado"], "usuario": r["usuario"], "definicion": json.loads(r["definicion"]), "origen": _leer_origen(r["origen"])}


def listar(tambo: str) -> list:
    """Lo justo para la lista: sin la definición. Favoritos primero, después por nombre. `con_aviso`: se trajo de DelPro con diferencias que cambian qué animales aparecen."""
    with _con() as con:
        filas = con.execute("SELECT id, nombre, favorito, modificado, definicion, origen FROM informes WHERE tambo = ? "
                            "ORDER BY favorito DESC, nombre_norm", (tambo,)).fetchall()
    return [{"id": r["id"], "nombre": r["nombre"], "favorito": bool(r["favorito"]), "modificado": r["modificado"],
             "n_columnas": len(json.loads(r["definicion"]).get("columnas", [])), "con_aviso": _leer_origen(r["origen"]) is not None} for r in filas]


ID_MAX = 2 ** 63 - 1            # el mayor INTEGER que guarda SQLite


def id_valido(id_) -> bool:
    """Si `id_` puede ser el id de un informe. Un entero fuera del rango de SQLite levanta `OverflowError` al usarse como parámetro (daba un 500 en las rutas por id):
    para quien lo pide es un informe que no existe."""
    return isinstance(id_, int) and not isinstance(id_, bool) and 0 < id_ <= ID_MAX


def obtener(tambo: str, id_: int):
    if not id_valido(id_):
        return None
    with _con() as con:
        r = con.execute("SELECT * FROM informes WHERE id = ? AND tambo = ?", (id_, tambo)).fetchone()
    return _fila(r) if r else None


def crear(tambo: str, nombre, definicion, usuario, favorito: bool = False, origen=None) -> dict:
    nombre, norm = _nombre(nombre)
    defn = _definicion(definicion)
    ahora = _iso(_ahora())
    try:
        with _con() as con:
            cur = con.execute("INSERT INTO informes (tambo, nombre, nombre_norm, definicion, favorito, creado, modificado, usuario, origen) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (tambo, nombre, norm, json.dumps(defn, ensure_ascii=False), int(bool(favorito)), ahora, ahora, usuario, _guardar_origen(origen)))
            nuevo = cur.lastrowid
    except sqlite3.IntegrityError:
        raise InformeGuardadoError(f"Ya hay un informe llamado «{nombre}». Elegí otro nombre.") from None
    return obtener(tambo, nuevo)


def actualizar(tambo: str, id_: int, nombre=None, definicion=None, favorito=None, origen=SIN_CAMBIO) -> dict:
    """`origen`: sin pasarlo queda la nota que ya tenía; una nota nueva la reemplaza y `None` la quita."""
    actual = obtener(tambo, id_)
    if actual is None:
        raise InformeGuardadoError("Ese informe no existe.")
    n, norm = (actual["nombre"], actual["nombre"].casefold()) if nombre is None else _nombre(nombre)
    defn = actual["definicion"] if definicion is None else _definicion(definicion)
    fav = actual["favorito"] if favorito is None else bool(favorito)
    nota = _guardar_origen(actual["origen"] if origen is SIN_CAMBIO else origen)
    try:
        with _con() as con:
            con.execute("UPDATE informes SET nombre = ?, nombre_norm = ?, definicion = ?, favorito = ?, modificado = ?, origen = ? "
                        "WHERE id = ? AND tambo = ?",
                        (n, norm, json.dumps(defn, ensure_ascii=False), int(fav), _iso(_ahora()), nota, id_, tambo))
    except sqlite3.IntegrityError:
        raise InformeGuardadoError(f"Ya hay un informe llamado «{n}». Elegí otro nombre.") from None
    return obtener(tambo, id_)
