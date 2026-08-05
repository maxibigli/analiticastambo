# -*- coding: utf-8 -*-
"""Check-list de control del tambo: la carga que hace el operario en el celular.

QUÉ ES. El tambo tiene dos planillas de papel —"Control Diario" (11 puntos) y
"Control Semanal" (9)— con OK / NO / Comentarios por cada punto. Esto es esa
planilla, en el teléfono, con foto cuando algo está mal.

DÓNDE SE GUARDA, Y POR QUÉ NO EN DDM. En una base SQLite propia
(`checklist.db`), igual criterio que `podal.py` e `iot_monitoreo.py`. DDM es de
solo lectura para esta app (`delpro_lectura` es `db_datareader`) y además es la
base de DeLaval: lo que se escriba ahí **se pierde en el próximo restore**, que
es exactamente lo que pasó el 05/08/2026. La base es estado propio de cada
instalación y no va al repo (ver `.gitignore`).

LA PLANTILLA SE VERSIONA, y no es un detalle. Cuando el tambo agregue o saque
una tarea, las corridas viejas tienen que seguir mostrando lo que se preguntó
ESE día: si los items se editaran en el lugar, un "95% de cumplimiento" del mes
pasado pasaría a calcularse sobre preguntas que entonces no existían. Por eso
`guardar_plantilla` crea una versión nueva y deja la anterior intacta; las
corridas apuntan a la versión con la que se cargaron.

TRES MOMENTOS, NO DOS. El PDF diario trae 11 puntos, pero hacerlos completos en
cada ordeñe son 33 checks por día: eso termina en tildar OK sin mirar, y el
dato deja de servir. Cada item lleva su `frecuencia`:

    sesion   -> se pregunta en CADA ordeñe (lo que cambia entre turnos)
    diario   -> una vez al día
    semanal  -> el día de la semana que corresponda

El reparto inicial de los 11 puntos del PDF diario entre `sesion` y `diario`
está abajo, en SEMILLA, y es una PROPUESTA para que el tambo corrija: es
conocimiento de ellos, no del código.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
import uuid

_DIR = os.path.dirname(__file__)
_DB_PATH = os.path.join(_DIR, "checklist.db")
DIR_FOTOS = os.path.join(_DIR, "checklist_fotos")

_db_lock = threading.Lock()

MOMENTOS = ("sesion", "diario", "semanal")
ESTADOS = ("ok", "no", "na")

# Tamaño máximo aceptado por foto YA redimensionada en el navegador (ver
# checklist.html). Una foto de celular pesa 4-8 MB y por el túnel, con señal de
# campo, no sube; el cliente la baja a ~1600px / ~300 KB. Este tope es la red
# de contención del servidor, no el objetivo.
MAX_BYTES_FOTO = 3 * 1024 * 1024
TIPOS_FOTO = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


# --- Semilla: los dos PDF del tambo, transcriptos ---------------------------
# (frecuencia, sector, tarea). El texto se mantiene como en la planilla de
# papel para que el operario reconozca cada punto; solo se corrigieron dos
# erratas evidentes ("tengas" -> "tengan", "ect" -> "etc").
SEMILLA = [
    # Control diario (11 puntos). Van a `sesion` los que dependen del ordeñe y
    # pueden cambiar de un turno a otro; a `diario` los de inspección de equipo,
    # que no cambian tres veces por día. PROPUESTA, a corregir por el tambo.
    ("sesion", "Rotativa", "Verificación del nivel de vacío"),
    ("sesion", "Rotativa", "Escuche si hay ruidos inusuales"),
    ("sesion", "Rotativa", "Limpieza de la cubierta de la plataforma, área de entrada/salida "
                           "y foso de la plataforma"),
    ("sesion", "Rotativa", "Confirmación de que el dispositivo de lavado funciona (lavado "
                           "completado, las unidades se lavan correctamente)"),
    ("sesion", "Rotativa", "Controlar que todos los puestos tengan sus partes (gancho, corrugado)"),
    ("sesion", "Piatinero", "Comprobar que el mismo está limpio"),
    ("diario", "Rotativa", "Comprobar si existen fugas (aire, agua, aceite, etc)"),
    ("diario", "Rotativa", "Limpieza de pantalla del controlador de rotativa"),
    ("diario", "Usher", "Inspección visual de todo el producto (tranquera, estado de cadenas)"),
    ("diario", "Usher", "Comprobar funcionamiento de elevación por medio de los cilindros neumáticos"),
    ("diario", "Usher", "Comprobación de que la usher se traslade de forma pareja y homogénea "
                        "sobre todo el trayecto (en ambas direcciones)"),
    # Control semanal (9 puntos), tal cual el PDF.
    ("semanal", "Rotativa", "Registro y control del software de DelPro"),
    ("semanal", "Usher", "Inspección de nivel de aceite (gabinete de acero inox)"),
    ("semanal", "Usher", "Constatar que el lubricador tenga grasa (cartucho plástico arriba "
                         "de la tranquera)"),
    ("semanal", "Usher", "Inspección de las cadenas de guía (cada 15 días limpiarlas de suciedad "
                         "o excedente de grasa y engrasar nuevamente)"),
    ("semanal", "Usher", "Inspección de los bujes de las ruedas — limpiarlas de suciedad o "
                         "excedente de grasa (cada 15 días engrasar)"),
    ("semanal", "Usher", "Inspección visual de línea de aire"),
    ("semanal", "Usher", "Comprobar la tensión de los cables de la puerta"),
    ("semanal", "TSR", "Lavado general de los protectores del robot"),
    ("semanal", "Piatinero", "Controlar el estado de dosificación de líquido"),
]


def _ahora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL: la carga del operario escribe mientras el panel de estadísticas lee.
    # Sin esto, un lector bloquea al que está guardando en el celular.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("""
        CREATE TABLE IF NOT EXISTS plantilla (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tambo TEXT NOT NULL,
            version INTEGER NOT NULL,
            creada_en TEXT NOT NULL,
            creada_por TEXT,
            UNIQUE (tambo, version)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS item (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plantilla_id INTEGER NOT NULL REFERENCES plantilla(id),
            orden INTEGER NOT NULL,
            frecuencia TEXT NOT NULL,
            sector TEXT NOT NULL,
            tarea TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_item_plantilla ON item(plantilla_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS corrida (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tambo TEXT NOT NULL,
            plantilla_id INTEGER NOT NULL REFERENCES plantilla(id),
            fecha TEXT NOT NULL,
            momento TEXT NOT NULL,
            sesion INTEGER,
            usuario TEXT NOT NULL,
            guardada_en TEXT NOT NULL,
            -- Identificador que genera el CELULAR antes de mandar. Si el envío
            -- se reintenta al volver la señal (ver la cola de checklist.html),
            -- el UNIQUE evita que la misma carga entre dos veces.
            offline_id TEXT NOT NULL UNIQUE
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_corrida_tambo_fecha ON corrida(tambo, fecha)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS respuesta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrida_id INTEGER NOT NULL REFERENCES corrida(id),
            item_id INTEGER NOT NULL REFERENCES item(id),
            estado TEXT NOT NULL,
            comentario TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_respuesta_corrida ON respuesta(corrida_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS foto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            respuesta_id INTEGER NOT NULL REFERENCES respuesta(id),
            archivo TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            creada_en TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_foto_respuesta ON foto(respuesta_id)")
    return con


# --- Plantilla --------------------------------------------------------------

def plantilla_vigente(tambo: str) -> dict:
    """La última versión de la plantilla del tambo. Si no hay ninguna, siembra
    la v1 con los dos PDF del tambo (ver SEMILLA)."""
    with _db_lock, _conectar() as con:
        fila = con.execute(
            "SELECT * FROM plantilla WHERE tambo = ? ORDER BY version DESC LIMIT 1",
            (tambo,)).fetchone()
        if fila is None:
            fila = _sembrar(con, tambo)
        items = con.execute(
            "SELECT id, orden, frecuencia, sector, tarea FROM item "
            "WHERE plantilla_id = ? ORDER BY orden", (fila["id"],)).fetchall()
    return {"id": fila["id"], "version": fila["version"], "tambo": tambo,
            "items": [dict(i) for i in items]}


def _sembrar(con: sqlite3.Connection, tambo: str) -> sqlite3.Row:
    cur = con.execute("INSERT INTO plantilla (tambo, version, creada_en, creada_por) "
                      "VALUES (?, 1, ?, 'semilla')", (tambo, _ahora()))
    pid = cur.lastrowid
    con.executemany("INSERT INTO item (plantilla_id, orden, frecuencia, sector, tarea) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(pid, i, f, s, t) for i, (f, s, t) in enumerate(SEMILLA, start=1)])
    return con.execute("SELECT * FROM plantilla WHERE id = ?", (pid,)).fetchone()


def guardar_plantilla(tambo: str, items: list, usuario: str) -> dict:
    """Crea una VERSIÓN NUEVA con los items dados. No edita la anterior: las
    corridas viejas tienen que seguir apuntando a lo que se preguntó ese día
    (ver la nota de arriba). `items`: [{frecuencia, sector, tarea}, ...]."""
    limpios = []
    for it in items:
        frec = str(it.get("frecuencia", "")).strip()
        if frec not in MOMENTOS:
            raise ValueError(f"Frecuencia inválida: {frec} (opciones: {', '.join(MOMENTOS)})")
        tarea = str(it.get("tarea", "")).strip()
        if not tarea:
            raise ValueError("Hay un item sin tarea")
        limpios.append((frec, str(it.get("sector", "")).strip() or "General", tarea))
    if not limpios:
        raise ValueError("La plantilla no puede quedar vacía")

    with _db_lock, _conectar() as con:
        ver = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM plantilla WHERE tambo = ?",
                          (tambo,)).fetchone()[0]
        cur = con.execute("INSERT INTO plantilla (tambo, version, creada_en, creada_por) "
                          "VALUES (?, ?, ?, ?)", (tambo, ver, _ahora(), usuario))
        pid = cur.lastrowid
        con.executemany("INSERT INTO item (plantilla_id, orden, frecuencia, sector, tarea) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [(pid, i, f, s, t) for i, (f, s, t) in enumerate(limpios, start=1)])
    return plantilla_vigente(tambo)


def items_para(tambo: str, momento: str) -> dict:
    """Los items que toca cargar en ese momento. `sesion` trae SOLO los de cada
    ordeñe; `diario` trae los diarios; `semanal`, los semanales. Se devuelven
    junto con el id y la versión de la plantilla, que viajan de vuelta al
    guardar para no depender de que el celular la tenga fresca."""
    if momento not in MOMENTOS:
        raise ValueError(f"Momento inválido: {momento} (opciones: {', '.join(MOMENTOS)})")
    pl = plantilla_vigente(tambo)
    pl["momento"] = momento
    pl["items"] = [i for i in pl["items"] if i["frecuencia"] == momento]
    return pl


# --- Carga ------------------------------------------------------------------

def guardar_corrida(tambo: str, momento: str, sesion, usuario: str, fecha: str,
                    respuestas: list, offline_id: str, fotos: dict | None = None) -> dict:
    """Guarda una carga completa del celular. ATÓMICA a propósito: la respuesta
    y sus fotos entran juntas o no entra nada, porque del otro lado hay una
    cola que reintenta cuando vuelve la señal y una carga a medias sería peor
    que ninguna.

    `respuestas`: [{item_id, estado, comentario}, ...].
    `fotos`: {item_id: [(nombre_archivo, bytes), ...]} — ya validadas por el
    llamador (ver `validar_foto`).
    `offline_id`: lo genera el celular. Si la misma carga llega dos veces
    (reintento después de un timeout que en realidad había entrado), la segunda
    se ignora y se devuelve la que ya estaba: `duplicada=True`.
    """
    if momento not in MOMENTOS:
        raise ValueError(f"Momento inválido: {momento}")
    if not str(offline_id or "").strip():
        raise ValueError("Falta el identificador de la carga (offline_id)")
    try:
        datetime.date.fromisoformat(fecha)
    except (TypeError, ValueError):
        raise ValueError(f"Fecha inválida: {fecha}")
    sesion = int(sesion) if str(sesion or "").strip() else None
    if momento == "sesion" and not sesion:
        raise ValueError("Falta indicar de qué ordeñe es el check-list")

    fotos = fotos or {}
    pl = plantilla_vigente(tambo)
    validos = {i["id"] for i in pl["items"]}
    limpias = []
    for r in respuestas:
        item_id = int(r.get("item_id", 0))
        if item_id not in validos:
            raise ValueError(f"El item {item_id} no es de la plantilla vigente")
        estado = str(r.get("estado", "")).strip().lower()
        if estado not in ESTADOS:
            raise ValueError(f"Estado inválido: {estado} (opciones: {', '.join(ESTADOS)})")
        comentario = (r.get("comentario") or "").strip() or None
        # Un "NO" sin decir qué pasó no sirve para nada después: es justo el
        # dato que el panel de estadísticas necesita para mostrar el problema.
        if estado == "no" and not comentario:
            raise ValueError("Cuando algo está mal hay que escribir qué pasó")
        limpias.append((item_id, estado, comentario))
    if not limpias:
        raise ValueError("No hay ninguna respuesta cargada")

    with _db_lock, _conectar() as con:
        ya = con.execute("SELECT id FROM corrida WHERE offline_id = ?", (offline_id,)).fetchone()
        if ya:
            return {"id": ya["id"], "duplicada": True}
        cur = con.execute(
            "INSERT INTO corrida (tambo, plantilla_id, fecha, momento, sesion, usuario, "
            "guardada_en, offline_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tambo, pl["id"], fecha, momento, sesion, usuario, _ahora(), offline_id))
        cid = cur.lastrowid
        guardadas = 0
        for item_id, estado, comentario in limpias:
            rid = con.execute(
                "INSERT INTO respuesta (corrida_id, item_id, estado, comentario) "
                "VALUES (?, ?, ?, ?)", (cid, item_id, estado, comentario)).lastrowid
            for nombre, datos in fotos.get(item_id, []):
                ruta_rel = _escribir_foto(nombre, datos)
                con.execute("INSERT INTO foto (respuesta_id, archivo, bytes, creada_en) "
                            "VALUES (?, ?, ?, ?)", (rid, ruta_rel, len(datos), _ahora()))
                guardadas += 1
    return {"id": cid, "duplicada": False, "respuestas": len(limpias), "fotos": guardadas}


def validar_foto(nombre: str, tipo: str, datos: bytes) -> str:
    """Devuelve la extensión a usar, o levanta ValueError. El archivo no se
    guarda con el nombre que mandó el cliente: se genera uno propio (ver
    `_escribir_foto`), así que no hay forma de escribir fuera de DIR_FOTOS."""
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
    """Guarda la foto EN DISCO y devuelve su ruta relativa. Al disco y no
    adentro de SQLite: unos pocos MB por día de blobs hacen crecer la base y el
    backup se vuelve impracticable, y encima cada lectura del panel se lleva la
    imagen puesta. Se reparte por año/mes para que ninguna carpeta junte miles
    de archivos."""
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
        fila = con.execute("SELECT archivo FROM foto WHERE id = ?", (foto_id,)).fetchone()
    if not fila:
        return None
    ruta = os.path.normpath(os.path.join(DIR_FOTOS, fila["archivo"]))
    if not ruta.startswith(os.path.normpath(DIR_FOTOS)) or not os.path.exists(ruta):
        return None
    return ruta, os.path.basename(ruta)


# --- Lectura (la usa la etapa 2, y la pantalla para saber qué falta hoy) -----

def corridas(tambo: str, desde: str, hasta: str) -> list:
    """Las cargas del rango, con sus respuestas y fotos. Una fila por corrida."""
    with _db_lock, _conectar() as con:
        cs = con.execute(
            "SELECT * FROM corrida WHERE tambo = ? AND fecha BETWEEN ? AND ? "
            "ORDER BY fecha DESC, sesion, guardada_en", (tambo, desde, hasta)).fetchall()
        salida = []
        for c in cs:
            rs = con.execute("""
                SELECT r.id, r.estado, r.comentario, i.sector, i.tarea, i.frecuencia
                FROM respuesta r JOIN item i ON i.id = r.item_id
                WHERE r.corrida_id = ? ORDER BY i.orden""", (c["id"],)).fetchall()
            detalle = []
            for r in rs:
                fotos = con.execute("SELECT id FROM foto WHERE respuesta_id = ?",
                                    (r["id"],)).fetchall()
                d = dict(r)
                d["fotos"] = [f["id"] for f in fotos]
                detalle.append(d)
            fila = dict(c)
            fila["respuestas"] = detalle
            salida.append(fila)
    return salida


def hechas_hoy(tambo: str, fecha: str) -> list:
    """Qué se cargó ya ese día: [{momento, sesion}]. La pantalla lo usa para
    marcar lo que falta y no pedir dos veces lo mismo."""
    with _db_lock, _conectar() as con:
        fs = con.execute("SELECT momento, sesion FROM corrida WHERE tambo = ? AND fecha = ?",
                         (tambo, fecha)).fetchall()
    return [{"momento": f["momento"], "sesion": f["sesion"]} for f in fs]
