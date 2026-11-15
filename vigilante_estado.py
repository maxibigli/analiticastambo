# -*- coding: utf-8 -*-
"""Vigilante: lo que tiene que sobrevivir a un reinicio (avisos ya hechos, tope diario, avisos retenidos,
si los datos estaban cortados) y la configuración que elige el usuario.

* El ESTADO va en SQLite (`vigilante.db`), como `checklist.db` / `bitacora.db`: estado propio de cada
  instalación, fuera de git. Cada operación abre y cierra su propia conexión (el hilo del vigilante y los
  pedidos de Flask no comparten conexión, y en Windows un archivo abierto no se puede borrar).
* La CONFIGURACIÓN va en un JSON (`vigilante_config.json`), mismo patrón que `config_alertas.py`: es una
  preferencia del usuario, no un secreto. Un archivo ausente o corrupto, o un campo inválido, nunca rompe:
  cae al valor por defecto de ESE campo.

El vigilante viene APAGADO: se enciende cuando el usuario lo decide, después de calibrarlo.
"""
import contextlib
import datetime
import json
import os
import re
import sqlite3
import threading

_AQUI = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.path.join(_AQUI, "vigilante.db")
RUTA_CONFIG = os.path.join(_AQUI, "vigilante_config.json")
_lock = threading.Lock()

DEFECTOS = {"activo": False, "pasada_min": 15, "tope_dia": 3,
            "silencio_desde": "22:00", "silencio_hasta": "05:00", "sin_datos_horas": 4}
_HORA_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ESTADOS_DATOS = ("ok", "sin_datos")


# --------------------------------------------------------------------------- estado (SQLite)
class Estado:
    def __init__(self, ruta: str = RUTA_DB):
        self.ruta = ruta
        with self._con() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS avisos (clave TEXT PRIMARY KEY, cuando TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS envios (id INTEGER PRIMARY KEY AUTOINCREMENT, dia TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS retenidos (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                                      linea TEXT NOT NULL, cuando TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS kv (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            """)

    @contextlib.contextmanager
    def _con(self):
        with _lock:
            con = sqlite3.connect(self.ruta, timeout=10)
            try:
                with con:                      # commit (o rollback) de la transacción
                    yield con
            finally:
                con.close()

    def ya_avisado(self, clave: str) -> bool:
        with self._con() as c:
            return c.execute("SELECT 1 FROM avisos WHERE clave = ?", (clave,)).fetchone() is not None

    def registrar_aviso(self, clave: str, ahora: datetime.datetime) -> None:
        with self._con() as c:
            c.execute("INSERT OR IGNORE INTO avisos (clave, cuando) VALUES (?, ?)", (clave, ahora.isoformat()))

    def registrar_envio(self, dia: datetime.date) -> None:
        """Un mensaje enviado (varias claves en un mismo mensaje cuentan UNA vez para el tope)."""
        with self._con() as c:
            c.execute("INSERT INTO envios (dia) VALUES (?)", (dia.isoformat(),))

    def avisos_del_dia(self, dia: datetime.date) -> int:
        with self._con() as c:
            return c.execute("SELECT COUNT(*) FROM envios WHERE dia = ?", (dia.isoformat(),)).fetchone()[0]

    def retener(self, linea: str, ahora: datetime.datetime) -> None:
        with self._con() as c:
            c.execute("INSERT INTO retenidos (linea, cuando) VALUES (?, ?)", (linea, ahora.isoformat()))

    def retenidos(self) -> list:
        """Las líneas retenidas SIN vaciarlas (para mostrar en «Probar»)."""
        with self._con() as c:
            return [f[0] for f in c.execute("SELECT linea FROM retenidos ORDER BY id")]

    def tomar_retenidos(self) -> list:
        """Devuelve las líneas retenidas y las borra, en una sola transacción."""
        with self._con() as c:
            lineas = [f[0] for f in c.execute("SELECT linea FROM retenidos ORDER BY id")]
            c.execute("DELETE FROM retenidos")
            return lineas

    def set_ultima_pasada(self, cuando: datetime.datetime, resultado: str) -> None:
        with self._con() as c:
            c.execute("INSERT OR REPLACE INTO kv (clave, valor) VALUES ('ultima_pasada', ?)",
                      (json.dumps({"cuando": cuando.isoformat(), "resultado": resultado}),))

    def ultima_pasada(self):
        """`(momento, resultado)` de la última pasada que registró, o `None` si todavía no hubo ninguna."""
        with self._con() as c:
            f = c.execute("SELECT valor FROM kv WHERE clave = 'ultima_pasada'").fetchone()
        if not f:
            return None
        d = json.loads(f[0])
        return datetime.datetime.fromisoformat(d["cuando"]), d["resultado"]

    def estado_datos(self) -> str:
        with self._con() as c:
            f = c.execute("SELECT valor FROM kv WHERE clave = 'estado_datos'").fetchone()
        return f[0] if f else "ok"

    def set_estado_datos(self, valor: str) -> None:
        if valor not in ESTADOS_DATOS:
            raise ValueError(f"Estado de datos desconocido: {valor}")
        with self._con() as c:
            c.execute("INSERT OR REPLACE INTO kv (clave, valor) VALUES ('estado_datos', ?)", (valor,))


# --------------------------------------------------------------------------- configuración (JSON)
def _entero(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError
    return v


def _numero(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError
    return v


def _hora(v):
    if not isinstance(v, str) or not _HORA_RE.match(v):
        raise ValueError
    return v


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError
    return v


_VALIDADORES = {
    "activo": (_bool, "«activo» tiene que ser verdadero o falso."),
    "pasada_min": (lambda v: _entero(v, 5, 120), "Cada cuántos minutos se revisa tiene que ser un número entre 5 y 120."),
    "tope_dia": (lambda v: _entero(v, 1, 20), "El tope de avisos por día tiene que ser un número entre 1 y 20."),
    "silencio_desde": (_hora, "El horario silencioso tiene que tener formato HH:MM (por ejemplo 22:00)."),
    "silencio_hasta": (_hora, "El horario silencioso tiene que tener formato HH:MM (por ejemplo 05:00)."),
    "sin_datos_horas": (lambda v: _numero(v, 1, 24), "Las horas para avisar «sin datos» tienen que ser un número entre 1 y 24."),
}


def _leer(ruta: str) -> dict:
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def config(ruta: str | None = None) -> dict:
    guardado = _leer(ruta or RUTA_CONFIG)
    cfg = dict(DEFECTOS)
    for campo, (validar, _msg) in _VALIDADORES.items():
        if campo in guardado:
            try:
                cfg[campo] = validar(guardado[campo])
            except ValueError:
                pass                              # un campo malo cae a su defecto, no tira los demás
    return cfg


def set_config(ruta: str | None = None, **campos) -> None:
    """Valida TODO antes de escribir: si algo es inválido no se guarda nada a medias."""
    nuevos = {}
    for campo, valor in campos.items():
        if campo not in _VALIDADORES:
            raise ValueError(f"Campo desconocido: {campo}")
        validar, mensaje = _VALIDADORES[campo]
        try:
            nuevos[campo] = validar(valor)
        except ValueError:
            raise ValueError(mensaje) from None
    ruta = ruta or RUTA_CONFIG
    with _lock:
        actual = _leer(ruta)
        actual.update(nuevos)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=1)


def _minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def en_silencio(ahora: datetime.datetime, cfg: dict) -> bool:
    """Si `ahora` cae en el horario silencioso (el inicio cuenta, el fin no). El tramo puede cruzar la
    medianoche (22:00 a 05:00). Con «desde» igual a «hasta» no hay silencio."""
    desde, hasta = _minutos(cfg["silencio_desde"]), _minutos(cfg["silencio_hasta"])
    t = ahora.hour * 60 + ahora.minute
    if desde == hasta:
        return False
    if desde < hasta:
        return desde <= t < hasta
    return t >= desde or t < hasta
