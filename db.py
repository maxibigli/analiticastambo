# -*- coding: utf-8 -*-
"""Conexión de solo lectura a SQL Server (instancia DELPRO, base DDM)."""
import datetime
import decimal
import re
import threading

import pyodbc

from tambos import DEFAULT_TAMBO, TAMBOS, conexion, nombre_variable_password, password_de

# SQL Server de DelPro es Express y suele quedar con muy poca memoria (el
# equipo tiene 8 GB compartidos): consultas concurrentes se apilan esperando
# memoria (RESOURCE_SEMAPHORE). Se ejecuta de a UNA consulta a la vez POR
# SERVIDOR (tambos en PCs distintas no se serializan entre sí).
_slots: dict[str, threading.Semaphore] = {}
_slots_lock = threading.Lock()


def _slot_for(server: str) -> threading.Semaphore:
    with _slots_lock:
        if server not in _slots:
            _slots[server] = threading.Semaphore(1)
        return _slots[server]


def _conn_str(tambo_id: str) -> tuple[str, str]:
    """Construye la cadena de conexión del tambo. Devuelve (cadena, servidor).

    `conexion(tid)` ya combina lo declarado en `tambos.TAMBOS` con los
    overrides guardados desde la página "⚙ Configuración" (ver
    `tambos._config_manual`/`configuracion_tambo.py`) — server/auth/user
    pueden venir de cualquiera de los dos lados, sin que a este módulo le
    importe cuál."""
    tid = tambo_id if tambo_id in TAMBOS else DEFAULT_TAMBO
    cfg = conexion(tid)
    server = cfg["server"]
    partes = [
        "DRIVER={ODBC Driver 18 for SQL Server}",
        f"SERVER={server}",
        f"DATABASE={cfg['database']}",
        "TrustServerCertificate=yes",
        "ApplicationIntent=ReadOnly",
    ]
    if cfg.get("auth") == "sql":
        pwd = password_de(tid)
        if not pwd:
            raise RuntimeError(
                f"Falta la contraseña del tambo '{tid}'. Definila en la página "
                f"«⚙ Configuración», o con la variable de entorno "
                f"{nombre_variable_password(tid)} (reiniciando la aplicación "
                f"después) — ver INSTALL.md."
            )
        partes.append(f"UID={cfg['user']}")
        partes.append(f"PWD={pwd}")
    else:
        partes.append("Trusted_Connection=yes")
    return ";".join(partes) + ";", server


MAX_ROWS = 5000
QUERY_TIMEOUT_S = 180

# Palabras que nunca deben aparecer en una consulta generada
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|"
    r"exec|execute|backup|restore|shutdown|dbcc|openrowset|opendatasource|"
    r"openquery|waitfor)\b|xp_\w+|sp_\w+",
    re.IGNORECASE,
)
_SELECT_INTO = re.compile(r"\binto\s+[#\[\w]", re.IGNORECASE)
_COMMENT = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)


class UnsafeQueryError(Exception):
    pass


class TablaNoDisponibleError(Exception):
    """La consulta referencia una tabla/vista que no existe en ESTA base: la
    misma DDM de DelPro puede tener diferencias de estructura chicas según el
    hardware o el tipo de sala de cada instalación (p.ej. la cámara BCS —
    `BcsDailyData` —, o las tablas propias del controlador de una rotativa —
    `CMSMilkYield`, `CMSGroupMilkSetting` — que una sala convencional no
    tiene). Quien pida algo OPCIONAL debe capturar puntualmente este error
    (nunca `Exception` a secas) y degradar a "sin datos" — cualquier otro
    error (timeout, conexión, permisos, SQL mal armado) sigue siendo un error
    real y no debe confundirse con esto ni silenciarse."""


def validate_sql(sql: str) -> str:
    """Acepta únicamente una sentencia SELECT/CTE de solo lectura."""
    cleaned = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if not cleaned:
        raise UnsafeQueryError("Consulta vacía.")
    if ";" in cleaned:
        raise UnsafeQueryError("Solo se permite una única sentencia SQL.")
    first_word = cleaned.split(None, 1)[0].lower()
    if first_word not in ("select", "with"):
        raise UnsafeQueryError("Solo se permiten consultas SELECT.")
    if _FORBIDDEN.search(cleaned):
        raise UnsafeQueryError("La consulta contiene operaciones no permitidas.")
    if _SELECT_INTO.search(cleaned):
        raise UnsafeQueryError("SELECT ... INTO no está permitido.")
    return cleaned


def _to_jsonable(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


# Tope de memoria por consulta: en SQL Express con poca RAM, un pedido de memoria
# que no se puede satisfacer deja la consulta colgada en RESOURCE_SEMAPHORE. Con
# este hint la consulta corre con menos memoria (usando disco si hace falta) en
# vez de colgarse. Se aplica a toda consulta que no traiga su propia cláusula OPTION.
_MEM_HINT = "\nOPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)"


def _con_tope_memoria(sql: str) -> str:
    return sql if "OPTION (" in sql.upper().replace("OPTION(", "OPTION (") else sql + _MEM_HINT


def run_query(sql: str, validate: bool = True, tambo: str = DEFAULT_TAMBO,
              max_rows: int = MAX_ROWS) -> dict:
    """Ejecuta un SELECT contra el tambo indicado y devuelve
    {columns: [...], rows: [[...], ...], truncated: bool}.
    `max_rows`: tope de filas a traer (por defecto MAX_ROWS). Subirlo solo para
    consultas puntuales que se sabe pueden superar el tope genérico (ej. todas
    las visitas de un día de mucho movimiento) — el llamador debe revisar
    igual el flag `truncated` en vez de asumir que nunca se corta."""
    if validate:
        sql = validate_sql(sql)
    sql = _con_tope_memoria(sql)
    conn_str, server = _conn_str(tambo)
    with _slot_for(server):
        conn = pyodbc.connect(conn_str, timeout=10)
        conn.timeout = QUERY_TIMEOUT_S
        try:
            cur = conn.cursor()
            # Lectura sin bloqueos: las consultas NO toman locks compartidos, así
            # nunca frenan las escrituras del ordeño en vivo (la rotativa sigue
            # grabando normal). Combinado con el usuario de solo lectura y la
            # validación SELECT-only, la app no puede tocar ni trabar la base.
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
            try:
                cur.execute(sql)
            except pyodbc.Error as exc:
                # 42S02 = "Invalid object name": la consulta referencia una
                # tabla/vista que no existe EN ESTA base — la diferencia de
                # estructura entre instalaciones (ver TablaNoDisponibleError),
                # no un error de conexión ni de la consulta en sí. Cualquier
                # otro SQLSTATE sigue siendo un error real, sin tocar.
                if exc.args and exc.args[0] == "42S02":
                    raise TablaNoDisponibleError(str(exc)) from exc
                raise
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = []
            for row in cur.fetchmany(max_rows):
                rows.append([_to_jsonable(v) for v in row])
            truncated = cur.fetchone() is not None
            return {"columns": columns, "rows": rows, "truncated": truncated}
        finally:
            conn.close()
