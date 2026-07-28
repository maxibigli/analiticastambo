# -*- coding: utf-8 -*-
"""Analítica DelPro — aplicación web para consultar la base DDM (SQL Server),
hacer preguntas en lenguaje natural y generar gráficas y reportes."""
import json
import math
import statistics
import threading
import time

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

import datetime

import os

import ai
import alimentacion
import auth
import cicla
import conciliacion
import conversion_historica
import config_alertas
import correo
import db
import clima
import ficha_animal
import flujos
import gestacion
import iot_monitoreo
import laserenisima
import mantenimiento
import partos_secados
import performance
import podal
import preneces
import parametros
import proveedores
import proyeccion
import rebano
import tasa_prenez
import reproduccion
import resumen
import rutina
import sala_convencional
import salas
import salud
import simulador
import tambos
import telegram_bot
import whatsapp
from consultas import CONSULTAS
from ordeno import (ORDENO_SQL, ORDENO_VIVO_SQL, ORDENO_INC_SQL,
                    ORDENO_ALARMAS_SQL, VIVO_LIMITE_MIN,
                    UMBRAL_DESLIZ_PCT, UMBRAL_BLOQ_PCT,
                    sql_incidentes_diarios)
from tareas import TAREAS

app = Flask(__name__)
# Recarga la plantilla al vuelo (los cambios de interfaz no requieren reiniciar).
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Clave de sesión: se genera sola la primera vez y se guarda en un archivo
# local (no es una contraseña de usuario, es la clave con la que Flask firma
# la cookie de sesión). Si se regenerara en cada reinicio, todos quedarían
# deslogueados cada vez que se reinicia el servidor.
_RUTA_SECRET_KEY = os.path.join(os.path.dirname(__file__), "secret_key.txt")
if os.path.exists(_RUTA_SECRET_KEY):
    with open(_RUTA_SECRET_KEY, "r", encoding="utf-8") as _f:
        app.secret_key = _f.read().strip()
else:
    app.secret_key = os.urandom(32).hex()
    with open(_RUTA_SECRET_KEY, "w", encoding="utf-8") as _f:
        _f.write(app.secret_key)
app.permanent_session_lifetime = datetime.timedelta(days=30)

# Rutas que no requieren haber iniciado sesión.
_RUTAS_PUBLICAS = {"/login"}


@app.before_request
def _requerir_login():
    if request.path in _RUTAS_PUBLICAS or request.path.startswith("/static/"):
        return None
    if "usuario" not in session:
        if request.path.startswith("/api/"):
            return jsonify({"error": "No autenticado"}), 401
        return redirect(url_for("login", siguiente=request.path))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = auth.ip_cliente()
        if auth.login_bloqueado(ip):
            return render_template(
                "login.html",
                error="Demasiados intentos fallidos. Esperá unos minutos y volvé a probar."), 429
        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "")
        rol = auth.verificar(usuario, password)
        if rol:
            auth.limpiar_intentos(ip)
            session.clear()
            session.permanent = True
            session["usuario"] = usuario
            session["rol"] = rol
            return redirect(request.args.get("siguiente") or url_for("index"))
        auth.registrar_intento_fallido(ip)
        return render_template("login.html", error="Usuario o contraseña incorrectos.")
    return render_template("login.html", error=None)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# Registro unificado id → SQL, para que el caché sirva a las consultas
# predefinidas, las listas de tareas y la vista de ordeño.
_SQL: dict[str, str] = {cid: c["sql"] for cid, c in CONSULTAS.items()}
_SQL.update({f"tarea:{tid}": t["sql"] for tid, t in TAREAS.items()})
_SQL["ordeno"] = ORDENO_SQL
_SQL["ordeno_vivo"] = ORDENO_VIVO_SQL
_SQL["ordeno_inc"] = ORDENO_INC_SQL
_SQL["ordeno_alarmas"] = ORDENO_ALARMAS_SQL
_SQL["mantenimiento"] = mantenimiento.MANTENIMIENTO_SQL
# "rutina_grupos"/"rutina_ordenos_dia" NO se registran acá: dependen del tipo
# de sala del tambo (ver salas/), así que todo llamador pasa el SQL armado en
# el momento con `salas.de(tambo)` — nunca caen al registro fijo de `_SQL`.
_SQL["rutina_grupos_nombres"] = rutina.SQL_GRUPOS_NOMBRES
_SQL["salud_rcs_grupo"] = salud.SQL_RCS_POR_GRUPO
_SQL["salud_rcs_vacas"] = salud.SQL_RCS_VACAS
_SQL["salud_conductividad"] = salud.SQL_CONDUCTIVIDAD_REBANIO
_SQL["salud_produccion_rodeo"] = salud.SQL_PRODUCCION_POR_RODEO
_SQL["salud_atencion"] = salud.SQL_ATENCION_DATOS
_SQL["salud_atencion_v2"] = salud.SQL_ATENCION_V2
_SQL["salud_bcs_vacas"] = salud.SQL_BCS_VACAS
_SQL["resumen_produccion_diaria"] = resumen.SQL_PRODUCCION_DIARIA
_SQL["resumen_animales"] = resumen.SQL_ANIMALES
_SQL["resumen_altas_bajas"] = resumen.SQL_ALTAS_BAJAS_AYER
_SQL["resumen_promedio_general"] = resumen.SQL_PRODUCCION_PROMEDIO_GENERAL

# Columnas de incidencias que se pegan al ordeño desde su caché aparte.
_INC_COLS = ["desliz", "patadas", "bloqueos", "recoloc", "ordenos_dia",
             "ordenos_con_desliz", "ordenos_con_bloqueo"]
# Columnas de alarmas por puesto (vaca actual) que se pegan al ordeño.
_ALARMA_COLS = ["real_kg", "esperada_kg", "a_baja", "a_cond", "a_sangre", "a_retirada"]

# TTL corto para el modo "en vivo": se refresca casi en cada actualización.
_VIVO_TTL_S = 10

# KPIs del dashboard, en una sola consulta. Se busca el último día con datos
# completos dentro de los últimos 60 días (el día en curso suele estar parcial).
KPIS_SQL = """
    WITH diarios AS (
      SELECT Date, COUNT(DISTINCT BasicAnimal) AS vacas
      FROM AnimalDaily
      WHERE GCRecord IS NULL AND IsYieldValid = 1 AND TotalYield > 0
        AND Date >= DATEADD(day, -60, CAST(GETDATE() AS date))
      GROUP BY Date
    ), ult AS (
      SELECT MAX(Date) AS d FROM diarios WHERE vacas >= 50
    )
    SELECT
      (SELECT d FROM ult) AS fecha_dato,
      (SELECT ROUND(SUM(TotalYield), 0) FROM AnimalDaily
         WHERE GCRecord IS NULL AND IsYieldValid = 1
           AND Date = (SELECT d FROM ult)) AS kg_ultimo_dia,
      (SELECT COUNT(DISTINCT BasicAnimal) FROM AnimalDaily
         WHERE GCRecord IS NULL AND IsYieldValid = 1 AND TotalYield > 0
           AND Date = (SELECT d FROM ult)) AS vacas_en_ordeno,
      (SELECT COUNT(*) FROM SessionMilkYield
         WHERE CAST(BeginTime AS date) = (SELECT d FROM ult)) AS ordenos_ultimo_dia,
      (SELECT COUNT(*) FROM BasicAnimal
         WHERE GCRecord IS NULL AND ExitDate IS NULL) AS animales_activos,
      (SELECT COUNT(*) FROM AnimalReproductionInfo r
         JOIN BasicAnimal b ON b.OID = r.Animal
         WHERE r.GCRecord IS NULL AND r.IsPregnant = 1
           AND b.GCRecord IS NULL AND b.ExitDate IS NULL) AS prenadas
"""

# Consultas que alimentan el dashboard: se precalientan primero.
DASHBOARD_IDS = ["produccion_30d", "estado_reproductivo"]

# Caché en memoria para consultas predefinidas (la base DelPro no tiene índices
# para estas agregaciones y en frío pueden tardar minutos).
_CACHE_TTL_S = 600
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, allow_stale: bool = False, ttl: float = _CACHE_TTL_S):
    with _cache_lock:
        item = _cache.get(key)
    if not item:
        return None, False
    fresh = time.time() - item[0] < ttl
    if fresh or allow_stale:
        return item[1], fresh
    return None, False


def _cache_set(key: str, value: dict):
    with _cache_lock:
        _cache[key] = (time.time(), value)


_refreshing: set = set()


def _clave(tambo: str, consulta_id: str) -> str:
    return f"{tambo}:{consulta_id}"


def _refresh_async(tambo: str, consulta_id: str, sql: str | None = None):
    """Refresca una consulta en segundo plano (una sola vez a la vez).

    `sql`: None = la registrada en `_SQL[consulta_id]` (el caso normal, un
    texto fijo para todos los tambos). Algunas pocas consultas SÍ varían según
    el tipo de sala del tambo (p.ej. "qué grupos ordeñan de verdad" — ver
    `salas/__init__.py`), y ahí el llamador arma el texto en el momento."""
    key = _clave(tambo, consulta_id)
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _cache_set(key, db.run_query(sql if sql is not None else _SQL[consulta_id], tambo=tambo))
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _run_consulta(consulta_id: str, tambo: str, sql: str | None = None) -> dict:
    # Si hay dato (aunque esté vencido) se sirve al instante; si venció,
    # se refresca en segundo plano para la próxima vez. `sql`: ver _refresh_async.
    key = _clave(tambo, consulta_id)
    value, fresh = _cache_get(key, allow_stale=True)
    if value is not None:
        if not fresh:
            _refresh_async(tambo, consulta_id, sql)
        return value
    data = db.run_query(sql if sql is not None else _SQL[consulta_id], tambo=tambo)
    _cache_set(key, data)
    return data


def _calcular_kpis(tambo: str) -> dict:
    kpis = db.run_query(KPIS_SQL, validate=False, tambo=tambo)
    fila = dict(zip(kpis["columns"], kpis["rows"][0])) if kpis["rows"] else {}
    _cache_set(_clave(tambo, "__kpis__"), fila)
    return fila


def _refresh_kpis_async(tambo: str):
    """Recalcula los KPIs en segundo plano (una sola vez a la vez)."""
    key = _clave(tambo, "__kpis__")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _calcular_kpis(tambo)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _calcular_resumen_animales(tambo: str) -> dict:
    """Composición del rodeo (donut vacas/novillas) + indicadores de
    reproducción + DIM promedio + altas/bajas de ayer, todo en un cálculo."""
    animales = db.run_query(resumen.SQL_ANIMALES, tambo=tambo)
    fila = dict(zip(animales["columns"], animales["rows"][0])) if animales["rows"] else {}
    ab = db.run_query(resumen.SQL_ALTAS_BAJAS_AYER, tambo=tambo)
    fila_ab = dict(zip(ab["columns"], ab["rows"][0])) if ab["rows"] else {}
    kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
    fecha_dato = kpis.get("fecha_dato") if kpis else None
    dim_promedio = None
    if fecha_dato:
        r = db.run_query(resumen.sql_dim_promedio(str(fecha_dato)[:10]), tambo=tambo)
        if r["rows"] and r["rows"][0][0] is not None:
            dim_promedio = round(r["rows"][0][0])
    resultado = {**fila, "altas_ayer": fila_ab.get("altas") or 0, "bajas_ayer": fila_ab.get("bajas") or 0,
                 "dim_promedio": dim_promedio}
    _cache_set(_clave(tambo, "__resumen_animales__"), resultado)
    return resultado


def _refresh_resumen_animales_async(tambo: str):
    key = _clave(tambo, "__resumen_animales__")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _calcular_resumen_animales(tambo)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _calcular_resumen_grupo(tambo: str) -> dict:
    """Producción media por grupo (30 días): SOLO los grupos de ordeño reales
    + el promedio general del tambo como referencia. "Grupo de ordeño real"
    depende del tipo de sala del tambo — ver `salas/`: en una rotativa sale de
    `CMSGroupMilkSetting.EnableMilking` (la única fuente correcta ahí, porque
    esa base puede compartir instalación con otro tambo); en una convencional,
    de la producción real en `AnimalDaily` (esa base es de un solo tambo, sin
    riesgo de mezclar corrales ajenos)."""
    grupos_data = db.run_query(salas.de(tambo).sql_grupos_resumen(), tambo=tambo)
    grupos = [r[0] for r in grupos_data["rows"]]
    grupos_set = set(grupos)
    todo = db.run_query(resumen.SQL_PRODUCCION_GRUPO_30D, tambo=tambo)
    idx = {c: i for i, c in enumerate(todo["columns"])}
    columnas = ["fecha", "grupo", "promedio_kg"]
    filas = [[r[idx["fecha"]], r[idx["grupo"]], r[idx["promedio_kg"]]]
             for r in todo["rows"] if r[idx["grupo"]] in grupos_set]
    general = db.run_query(resumen.SQL_PRODUCCION_PROMEDIO_GENERAL, tambo=tambo)
    resultado = {"grupos": grupos, "nombres": _nombres_grupos(tambo),
                 "detalle": {"columns": columnas, "rows": filas}, "general": general}
    _cache_set(_clave(tambo, "__resumen_grupo__"), resultado)
    return resultado


def _refresh_resumen_grupo_async(tambo: str):
    key = _clave(tambo, "__resumen_grupo__")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _calcular_resumen_grupo(tambo)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _refresh_rutina_async(tambo: str, fecha: str):
    """Recalcula la rutina de ordeño de un día en segundo plano (consulta
    parametrizada por fecha, no vive en el registro fijo _SQL). La consulta
    depende del tipo de sala del tambo — ver `salas/`."""
    key = _clave(tambo, f"rutina:{fecha}")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _cache_set(key, db.run_query(salas.de(tambo).sql_rutina(fecha), tambo=tambo,
                                          max_rows=rutina.MAX_FILAS_DIA))
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _warmup(tambo: str):
    """Precalienta el caché de un tambo en segundo plano. Como SQL Express corre
    con muy poca memoria, se ejecuta TODO en serie (Semaphore por servidor en
    db.py): primero lo que necesita el dashboard, después las consultas pesadas."""
    try:
        _calcular_kpis(tambo)
    except Exception:  # noqa: BLE001
        pass
    try:
        _calcular_resumen_animales(tambo)
    except Exception:  # noqa: BLE001
        pass
    try:
        _calcular_resumen_grupo(tambo)
    except Exception:  # noqa: BLE001
        pass
    prioridad = DASHBOARD_IDS + ["ordeno", "ordeno_vivo"] + \
        [f"tarea:{t}" for t in TAREAS] + \
        ["ordeno_inc", "ordeno_alarmas", "mantenimiento", "rutina_grupos",
         "resumen_produccion_diaria", "top_vacas", "curva_lactancia", "prod_por_lactancia"]
    restantes = [c for c in CONSULTAS if c not in prioridad]
    for consulta_id in prioridad + restantes:
        try:
            # "rutina_grupos" depende del tipo de sala (ver salas/); el resto
            # de las IDs son consultas fijas para todos los tambos.
            sql = salas.de(tambo).sql_grupos() if consulta_id == "rutina_grupos" else None
            _run_consulta(consulta_id, tambo, sql)
        except Exception:  # noqa: BLE001
            pass


# Al iniciar solo se precalienta el tambo por defecto; los demás se calientan
# la primera vez que se los selecciona en el listbox.
threading.Thread(target=_warmup, args=(tambos.DEFAULT_TAMBO,), daemon=True).start()


_calentados: set = set()
_calentados_lock = threading.Lock()


def _tambo_del_request() -> str:
    """Lee el tambo del request (query param o body) y lo valida. Además,
    dispara el warmup del tambo la primera vez que se lo usa.

    El query param se mira SIEMPRE, incluso en POST: varias pantallas mandan
    `?tambo=...` en la URL también para guardar (parámetros reproductivos,
    configuración de sala). Nunca se notó que un POST sin `tambo` en el body
    caía en el tambo por defecto porque hasta ahora el único tambo real era
    "ponderosa" — que ES el default. Con un segundo tambo (San José) guardar
    su configuración terminaba escribiendo silenciosamente sobre "ponderosa".
    """
    tambo = request.args.get("tambo", "")
    if not tambo and request.method != "GET":
        tambo = (request.json or {}).get("tambo", "")
    tambo = tambos.resolver(tambo)
    with _calentados_lock:
        nuevo = tambo not in _calentados
        if nuevo:
            _calentados.add(tambo)
    if nuevo and tambo != tambos.DEFAULT_TAMBO:
        threading.Thread(target=_warmup, args=(tambo,), daemon=True).start()
    return tambo


@app.get("/")
def index():
    return render_template("index.html", ia_disponible=ai.api_disponible(),
                            usuario=auth.usuario_actual(), rol=auth.rol_actual(),
                            paginas_visibles=auth.paginas_visibles())


@app.get("/api/tambos")
def api_tambos():
    return jsonify({"tambos": tambos.lista(), "default": tambos.DEFAULT_TAMBO})


@app.get("/api/health")
def health():
    try:
        db.run_query("SELECT 1 AS ok", validate=False)
        return jsonify({"db": "ok", "ia": ai.api_disponible()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"db": f"error: {exc}", "ia": ai.api_disponible()}), 500


@app.get("/api/dashboard")
@auth.requiere_rol("admin")
def dashboard():
    # El dashboard sirve exclusivamente desde el caché precalentado, así nunca
    # queda bloqueado detrás de una consulta pesada. Si todavía no está listo,
    # responde 202 y el frontend reintenta.
    tambo = _tambo_del_request()
    kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
    prod, _ = _cache_get(_clave(tambo, "produccion_30d"), allow_stale=True)
    repro, _ = _cache_get(_clave(tambo, "estado_reproductivo"), allow_stale=True)
    if kpis is None or prod is None or repro is None:
        # Auto-reparación: si el warmup dejó alguna pieza sin cargar (p. ej. por
        # un timeout), se dispara su cálculo en segundo plano y el frontend
        # reintenta hasta tenerlas las tres.
        if kpis is None:
            _refresh_kpis_async(tambo)
        if prod is None:
            _refresh_async(tambo, "produccion_30d")
        if repro is None:
            _refresh_async(tambo, "estado_reproductivo")
        listos = sum(x is not None for x in (kpis, prod, repro))
        return jsonify({
            "calentando": True,
            "progreso": f"{listos}/3",
            "mensaje": "Cargando datos por primera vez (SQL Server Express con poca "
                       "memoria)…",
        }), 202
    return jsonify({"kpis": kpis, "produccion": prod, "reproduccion": repro})


@app.get("/api/resumen/produccion")
@auth.requiere_rol("admin")
def api_resumen_produccion():
    """Producción diaria (últimos días) + KPIs de ayer con su variación vs. el
    día anterior, réplica del resumen diario de producción del home de DelPro."""
    tambo = _tambo_del_request()
    data = _run_consulta("resumen_produccion_diaria", tambo)
    idx = {c: i for i, c in enumerate(data["columns"])}
    filas = [dict(zip(data["columns"], r)) for r in data["rows"]]
    filas = filas[-(resumen.PRODUCCION_DIAS + 1):]  # margen -> últimos N+1 días completos
    for f in filas:
        f["promedio_kg"] = round(f["kg_total"] / f["vacas_ordenadas"], 1) if f["vacas_ordenadas"] else None
    dias = filas[-resumen.PRODUCCION_DIAS:]
    ayer = filas[-1] if filas else None
    anteayer = filas[-2] if len(filas) > 1 else None

    def _variacion(campo):
        if not ayer or not anteayer or anteayer.get(campo) in (None, 0):
            return None
        return round(ayer[campo] - anteayer[campo], 1)

    return jsonify({
        "dias": dias,
        "ayer": ayer,
        "variacion": {
            "kg_total": _variacion("kg_total"), "kg_desconocida": _variacion("kg_desconocida"),
            "promedio_kg": _variacion("promedio_kg"), "vacas_ordenadas": _variacion("vacas_ordenadas"),
        } if ayer else None,
    })


@app.get("/api/resumen/animales")
@auth.requiere_rol("admin")
def api_resumen_animales():
    """Composición del rodeo (vacas en ordeño/secas, novillas preñadas/sin
    preñar) + indicadores de reproducción, réplica del donut del home de
    DelPro. Las categorías se infieren de LactationNumber/IsDryingOff/
    IsPregnant (DDM no tiene un campo explícito 'novilla'/'vaca')."""
    tambo = _tambo_del_request()
    key = _clave(tambo, "__resumen_animales__")
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        _refresh_resumen_animales_async(tambo)
        return jsonify({"calentando": True, "mensaje": "Calculando composición del rodeo…"}), 202
    if not fresh:
        _refresh_resumen_animales_async(tambo)

    vacas_total = data.get("vacas_total") or 0
    novillas_total = data.get("novillas_total") or 0
    pct = lambda num, den: round(100 * num / den, 1) if den else None  # noqa: E731
    return jsonify({
        **data,
        "pct_novillas_prenadas": pct(data.get("novillas_prenadas"), novillas_total),
        "pct_vacas_prenadas": pct(data.get("vacas_prenadas"), vacas_total),
    })


def _refresh_duraciones_directo_async(tambo: str):
    """Para salas que SÍ tienen la duración de cada sesión medida en una tabla
    propia (convencional: `ParlorHistoricalData`) en vez de tener que
    reconstruirla desde visitas individuales (`rutina.py`, solo la rotativa).
    Ver `salas.de(tambo).sql_duraciones_dia`."""
    key = _clave(tambo, "resumen_duraciones_directo")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            sala = salas.de(tambo)
            data = db.run_query(sala.sql_duraciones_dia(resumen.PRODUCCION_DIAS),
                                tambo=tambo, max_rows=500)
            filas = [{"fecha": r[0], "sesion": r[1], "duracion_min": r[2]} for r in data["rows"]]
            _cache_set(key, sala.armar_duraciones(filas, resumen.PRODUCCION_DIAS))
        except Exception as exc:  # noqa: BLE001
            _cache_set(key, {"error": str(exc)})
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/resumen/duraciones")
@auth.requiere_rol("admin")
def api_resumen_duraciones():
    """Duración (min) de cada sesión de ordeño de los últimos días, para el
    gráfico de barras agrupadas 'Duraciones de ordeño' del home de DelPro.

    La rotativa reutiliza el mismo caché por día que ya usan Rutina/Evolución
    (reconstruye la sesión desde visitas individuales, no tiene otra forma).
    Una sala con la duración de sesión ya medida en una tabla propia (`ver
    salas.de(tambo).sql_duraciones_dia`) usa esa directo — más simple y más
    barato. `NotImplementedError` es la señal de "esta sala no tiene eso":
    la rotativa la levanta a propósito (`salas/rotativa.py`)."""
    tambo = _tambo_del_request()
    tiene_duraciones_propias = True
    try:
        salas.de(tambo).sql_duraciones_dia(resumen.PRODUCCION_DIAS)
    except NotImplementedError:
        tiene_duraciones_propias = False

    if tiene_duraciones_propias:
        key = _clave(tambo, "resumen_duraciones_directo")
        data, fresh = _cache_get(key, allow_stale=True)
        if data is None:
            _refresh_duraciones_directo_async(tambo)
            return jsonify({"calentando": True, "mensaje": "Calculando duraciones…"}), 202
        if not fresh:
            _refresh_duraciones_directo_async(tambo)
        if data.get("error"):
            return jsonify({"error": data["error"]}), 502
        return jsonify(data)

    kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
    if not kpis or not kpis.get("fecha_dato"):
        return jsonify({"calentando": True, "mensaje": "Calculando fecha por defecto…"}), 202
    fecha_fin = datetime.datetime.strptime(str(kpis["fecha_dato"])[:10], "%Y-%m-%d")
    dias = [(fecha_fin - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(resumen.PRODUCCION_DIAS - 1, -1, -1)]
    grupos, _pesos = _grupos_pesos_de_request(tambo)
    tope = _max_sesiones(tambo)
    puntos, faltan = [], 0
    for fecha in dias:
        data, _ = _cache_get(_clave(tambo, f"rutina:{fecha}"), allow_stale=True, ttl=EVOLUCION_TTL_S)
        if data is None:
            _refresh_rutina_async(tambo, fecha)
            faltan += 1
            puntos.append({"fecha": fecha, "duraciones": []})
            continue
        try:
            dia = rutina.analizar_dia(data["columns"], data["rows"], fecha,
                                       grupos, max_sesiones=tope)
            duraciones = [s["duracion_min"] for s in dia["sesiones"]]
        except Exception:  # noqa: BLE001
            duraciones = []
        puntos.append({"fecha": fecha, "duraciones": duraciones})
    return jsonify({"puntos": puntos, "calculando": faltan > 0, "max_sesiones": tope})


@app.get("/api/resumen/produccion_grupo")
@auth.requiere_rol("admin")
def api_resumen_produccion_grupo():
    """Producción media (kg/vaca/día) de los grupos más numerosos + el
    promedio general, últimos 30 días — réplica de 'Producción media por
    grupo' del home de DelPro."""
    tambo = _tambo_del_request()
    key = _clave(tambo, "__resumen_grupo__")
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        _refresh_resumen_grupo_async(tambo)
        return jsonify({"calentando": True, "mensaje": "Calculando producción por grupo…"}), 202
    if not fresh:
        _refresh_resumen_grupo_async(tambo)
    return jsonify(data)


# --- Salud del rodeo (réplica del reporte Chi) ------------------------------
# Todas sirven del caché con el patrón habitual: si el dato todavía no está,
# se dispara el cálculo en segundo plano y el frontend reintenta (202).
def _servir_cacheado(tambo: str, consulta_id: str, mensaje: str):
    key = _clave(tambo, consulta_id)
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        _refresh_async(tambo, consulta_id)
        return None, (jsonify({"calentando": True, "mensaje": mensaje}), 202)
    if not fresh:
        _refresh_async(tambo, consulta_id)
    return data, None


@app.get("/api/salud/rcs_grupo")
@auth.requiere_rol("admin")
def api_salud_rcs_grupo():
    """Resumen de células somáticas (RCS) por rodeo: promedio del último
    control, cuántas superan 300.000 ahora y en el control previo, y cuántas
    son crónicas."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_rcs_grupo", "Calculando RCS por rodeo…")
    if espera:
        return espera
    idx = {c: i for i, c in enumerate(data["columns"])}
    # La base guarda el RCS en miles: se pasa a células/ml para mostrarlo.
    cel = lambda v: round(v * salud.RCS_A_CELULAS) if v is not None else None  # noqa: E731
    filas = []
    for r in data["rows"]:
        vacas = r[idx["vacas"]] or 0
        pct = lambda n: round(100 * n / vacas) if vacas else None  # noqa: E731
        filas.append({
            "grupo": r[idx["grupo"]], "vacas": vacas,
            "scc_promedio": cel(r[idx["scc_promedio"]]),
            "scc_maximo": cel(r[idx["scc_maximo"]]),
            "altas_ultimo": r[idx["altas_ultimo"]], "pct_altas_ultimo": pct(r[idx["altas_ultimo"]]),
            "altas_anterior": r[idx["altas_anterior"]], "pct_altas_anterior": pct(r[idx["altas_anterior"]]),
            "nuevas": r[idx["nuevas"]],
            "cronicas": r[idx["cronicas"]], "pct_cronicas": pct(r[idx["cronicas"]]),
        })
    return jsonify({"filas": filas, "umbral": salud.UMBRAL_RCS})


@app.get("/api/salud/rcs_vacas")
@auth.requiere_rol("admin")
def api_salud_rcs_vacas():
    """Vacas con RCS por encima del umbral, separando las crónicas (altas en
    el último control Y en el anterior)."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_rcs_vacas", "Calculando vacas con RCS alto…")
    if espera:
        return espera
    todas = [dict(zip(data["columns"], r)) for r in data["rows"]]
    for v in todas:  # la base guarda el RCS en miles
        for k in ("scc_ultimo", "scc_anterior"):
            if v.get(k) is not None:
                v[k] = round(v[k] * salud.RCS_A_CELULAS)
    return jsonify({
        "altas": todas,
        "cronicas": [v for v in todas if v.get("cronica")],
        "umbral": salud.UMBRAL_RCS,
    })


@app.get("/api/salud/conductividad")
@auth.requiere_rol("admin")
def api_salud_conductividad():
    """Conductividad promedio diaria por rodeo (vista del rebaño)."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_conductividad", "Calculando conductividad…")
    if espera:
        return espera
    return jsonify({**data, "umbral": salud.COND_ALTA})


@app.get("/api/salud/produccion_rodeo")
@auth.requiere_rol("admin")
def api_salud_produccion_rodeo():
    """Estadística de producción por rodeo con tendencias (día y semana)."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_produccion_rodeo", "Calculando producción por rodeo…")
    if espera:
        return espera
    return jsonify({"rodeos": salud.resumen_por_rodeo(data["columns"], data["rows"])})


@app.get("/api/salud/atencion")
@auth.requiere_rol("admin")
def api_salud_atencion():
    """Vacas a revisar, ordenadas por un índice de atención PROPIO.

    OJO: no es el score del add-on Chi (ese se calcula dentro de su ejecutable
    y no queda en la base). Usa las mismas señales, con pesos definidos acá."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_atencion", "Calculando índice de atención…")
    if espera:
        return espera
    fichas = salud.calcular_atencion(data["columns"], data["rows"])
    return jsonify({"vacas": fichas, "estimacion_propia": True})


@app.get("/api/salud/atencion_v2")
@auth.requiere_rol("admin")
def api_salud_atencion_v2():
    """Vacas a revisar según el índice EXPERIMENTAL multi-sistema (ubre /
    metabólico / general), en validación de campo. Se muestra en paralelo al
    índice clásico (/api/salud/atencion), no lo reemplaza: el backtest contra
    647 diagnósticos reales no mostró que supere al clásico, pero las señales
    que usa sí están validadas individualmente. Cada vaca trae sus "motivos"
    en texto para que el operario juzgue si la señal es válida en el campo."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_atencion_v2", "Calculando índice experimental…")
    if espera:
        return espera
    fichas = salud.calcular_atencion_v2(data["columns"], data["rows"])
    return jsonify({"vacas": fichas, "experimental": True})


# --- Monitoreo IoT en tiempo real (gateway M300: lavado/barrido + sensores) -
@app.get("/api/iot/estado")
@auth.requiere_rol("admin")
def api_iot_estado():
    """Estado de 4 fases de la rotativa (ORDEÑO/LAVANDO/BARRIDO/APAGADO) +
    últimas lecturas de los sensores de temperatura/humedad planeados
    (aparecen como "sin instalar" hasta que haya datos reales)."""
    tambo = _tambo_del_request()
    return jsonify({
        "sistema": iot_monitoreo.estado_sistema(tambo),
        "sensores": iot_monitoreo.lecturas_actuales(),
    })


# --- Problemas podales (renguera por cámaras) -------------------------------
# Heurístico v1, sin calibrar con video real de este tambo (no hay cámaras
# instaladas todavía) -- ver la nota completa en podal_vision.py. Las cámaras
# solo se activan si el tambo tiene configuración en config_podal.py.

@app.get("/api/podal/estado")
@auth.requiere_rol("admin")
def api_podal_estado():
    tambo = _tambo_del_request()
    return jsonify(podal.estado(tambo))


@app.post("/api/podal/iniciar")
@auth.requiere_rol("admin")
def api_podal_iniciar():
    # Se llama sin cuerpo JSON (el tambo va en la URL), a diferencia de
    # _tambo_del_request() que espera el tambo en el body para POST.
    tambo = tambos.resolver(request.args.get("tambo", ""))
    return jsonify(podal.iniciar(tambo))


@app.post("/api/podal/detener")
@auth.requiere_rol("admin")
def api_podal_detener():
    tambo = tambos.resolver(request.args.get("tambo", ""))
    return jsonify(podal.detener(tambo))


@app.get("/api/podal/vacas")
@auth.requiere_rol("admin")
def api_podal_vacas():
    """Vacas con alerta de renguera: promedio reciente de score vs. su propio
    historial previo (ver podal.calcular_alertas)."""
    tambo = _tambo_del_request()
    return jsonify({"vacas": podal.calcular_alertas(tambo), "estimacion_propia": True})


@app.get("/api/podal/historial/<int:rp>")
@auth.requiere_rol("admin")
def api_podal_historial(rp: int):
    """Serie temporal de scores de una vaca puntual, para el gráfico de
    tendencia."""
    tambo = _tambo_del_request()
    dias = request.args.get("dias", type=int) or podal.DIAS_REFERENCIA_DEFECTO
    return jsonify({"rp": rp, "lecturas": podal.historial(tambo, rp=rp, dias=dias)})


@app.get("/api/podal/recientes")
@auth.requiere_rol("admin")
def api_podal_recientes():
    """Últimas pasadas registradas (identificadas o no), para el panel de
    actividad en tiempo real."""
    tambo = _tambo_del_request()
    limite = min(request.args.get("limite", type=int) or 20, 100)
    return jsonify({"lecturas": podal.recientes(tambo, limite=limite)})


@app.get("/api/podal/snapshot/<camara>")
@auth.requiere_rol("admin")
def api_podal_snapshot(camara: str):
    """Último cuadro (JPEG) de la cámara "marcha" o "posicion", para mostrar
    la vista en vivo en la interfaz."""
    if camara not in ("marcha", "posicion"):
        return jsonify({"error": "Cámara inválida."}), 400
    tambo = tambos.resolver(request.args.get("tambo", ""))
    data = podal.frame_jpeg(tambo, camara)
    if data is None:
        return jsonify({"error": "Sin imagen todavía."}), 404
    return Response(data, mimetype="image/jpeg")


@app.get("/api/salud/bcs_vacas")
@auth.requiere_rol("admin")
def api_salud_bcs_vacas():
    """Última lectura de condición corporal (BCS, cámara DeLaval) de cada
    vaca, con su DEL y estado reproductivo — para el gráfico DEL-vs-score y
    la lista de vacas fuera de rango. El filtrado por score mín/máx y estado
    reproductivo lo hace el frontend sobre este mismo listado."""
    tambo = _tambo_del_request()
    data, espera = _servir_cacheado(tambo, "salud_bcs_vacas", "Calculando condición corporal…")
    if espera:
        return espera
    vacas = [dict(zip(data["columns"], r)) for r in data["rows"]]
    return jsonify({
        "vacas": vacas, "bcs_bajo": salud.BCS_BAJO, "bcs_alto": salud.BCS_ALTO,
    })


@app.get("/api/animal/ficha")
@auth.requiere_rol("admin")
def api_animal_ficha():
    """Ficha individual de un animal por RP: datos generales, historial de
    eventos, producción diaria, condición corporal (BCS) y test de leche.
    Consulta directa (sin caché): es una búsqueda puntual, no un dashboard."""
    tambo = _tambo_del_request()
    rp_raw = request.args.get("rp", "").strip()
    if not rp_raw.isdigit():
        return jsonify({"error": "Ingresá un número de RP válido."}), 400
    rp = int(rp_raw)

    def filas(d):
        return [dict(zip(d["columns"], r)) for r in d["rows"]]

    try:
        info = db.run_query(ficha_animal.sql_info_general(rp), tambo=tambo)
        if not info["rows"]:
            return jsonify({"error": f"No se encontró el animal RP {rp}."}), 404
        eventos = db.run_query(ficha_animal.sql_eventos(rp), tambo=tambo)
        produccion = db.run_query(ficha_animal.sql_produccion_diaria(rp), tambo=tambo)
        bcs = db.run_query(ficha_animal.sql_bcs_individual(rp), tambo=tambo)
        test_diario = db.run_query(ficha_animal.sql_test_leche_diario(rp), tambo=tambo)
        test_controles = db.run_query(ficha_animal.sql_test_leche_controles(rp), tambo=tambo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"No se pudo consultar la base: {exc}"}), 502

    return jsonify({
        "info": dict(zip(info["columns"], info["rows"][0])),
        "eventos": filas(eventos),
        "produccion": filas(produccion),
        "bcs": filas(bcs),
        "test_diario": filas(test_diario),
        "test_controles": filas(test_controles),
    })


@app.get("/api/predefinidas")
def predefinidas():
    return jsonify([
        {"id": k, "titulo": v["titulo"]} for k, v in CONSULTAS.items()
    ])


@app.get("/api/tareas")
def api_tareas():
    """Listas de tareas pendientes del rodeo (estilo To-Do de DelPro). Cada
    categoría se sirve del caché; las que aún se están calculando se marcan
    como 'calculando' y el frontend reintenta."""
    tambo = _tambo_del_request()
    salida = []
    for tid, meta in TAREAS.items():
        key = _clave(tambo, f"tarea:{tid}")
        data, _ = _cache_get(key, allow_stale=True)
        if data is None:
            _refresh_async(tambo, f"tarea:{tid}")
            salida.append({"id": tid, "titulo": meta["titulo"],
                           "descripcion": meta["descripcion"], "calculando": True})
        else:
            salida.append({
                "id": tid, "titulo": meta["titulo"], "descripcion": meta["descripcion"],
                "cantidad": len(data["rows"]), "columns": data["columns"],
                "rows": data["rows"], "truncated": data.get("truncated", False),
            })
    # Mantenimiento preventivo: mismo caché que /api/mantenimiento (no se
    # duplica la consulta). Acá solo entran los contadores que ya llegaron o
    # están por llegar a su tope (>=85%), como en la tarjeta de Ordeño en vivo.
    key = _clave(tambo, "mantenimiento")
    data, _ = _cache_get(key, allow_stale=True)
    desc_mant = "Contadores de tubos, pezoneras, lubricadores… que llegaron o están por llegar a su tope (≥85%)."
    if data is None:
        _refresh_async(tambo, "mantenimiento")
        salida.append({"id": "mantenimiento", "titulo": "Mantenimiento preventivo",
                       "descripcion": desc_mant, "calculando": True})
    else:
        idx = {c: i for i, c in enumerate(data["columns"])}

        def _necesita_atencion(row):
            limite, acumulado, alarma = row[idx["limite"]], row[idx["acumulado"]], row[idx["alarma"]]
            pct = (100 * acumulado / limite) if limite else None
            return alarma == 1 or (pct is not None and pct >= 85)

        filas = [r for r in data["rows"] if _necesita_atencion(r)]
        salida.append({"id": "mantenimiento", "titulo": "Mantenimiento preventivo",
                       "descripcion": desc_mant, "cantidad": len(filas),
                       "columns": data["columns"], "rows": filas, "truncated": False})

    # Unidades de ordeño con incidencias: mismo caché que la tarjeta "Alarmas
    # críticas de unidades" de Ordeño en vivo (ordeno_inc), para que el
    # operario de cada sesión tenga una lista puntual de puestos a revisar
    # (deslizamiento/bloqueo por encima del umbral crítico de la rotativa).
    key = _clave(tambo, "ordeno_inc")
    data, _ = _cache_get(key, allow_stale=True)
    desc_unid = ("Puestos con deslizamiento > %d%% o bloqueo > %d%% de los ordeños del día "
                 "(umbrales críticos de la rotativa)." % (UMBRAL_DESLIZ_PCT, UMBRAL_BLOQ_PCT))
    cols_unid = ["posicion", "ordenos_dia", "deslizamiento_pct", "bloqueo_pct",
                 "patadas", "recolocaciones", "retiradas", "prod_relativa_pct"]
    if data is None:
        _refresh_async(tambo, "ordeno_inc")
        salida.append({"id": "unidades", "titulo": "Unidades de ordeño a revisar",
                       "descripcion": desc_unid, "calculando": True})
    else:
        idx = {c: i for i, c in enumerate(data["columns"])}
        filas = []
        for r in data["rows"]:
            n = r[idx["ordenos_dia"]] or 0
            if n <= 0:
                continue
            pct_desliz = 100 * (r[idx["ordenos_con_desliz"]] or 0) / n
            pct_bloq = 100 * (r[idx["ordenos_con_bloqueo"]] or 0) / n
            if pct_desliz > UMBRAL_DESLIZ_PCT or pct_bloq > UMBRAL_BLOQ_PCT:
                prod = r[idx["prod_relativa_pct"]]
                filas.append([r[idx["posicion"]], n, round(pct_desliz), round(pct_bloq),
                             r[idx["patadas"]], r[idx["recoloc"]], r[idx["retiradas"]],
                             round(prod) if prod is not None else None])
        filas.sort(key=lambda f: -max(f[2] - UMBRAL_DESLIZ_PCT, f[3] - UMBRAL_BLOQ_PCT))
        salida.append({"id": "unidades", "titulo": "Unidades de ordeño a revisar",
                       "descripcion": desc_unid, "cantidad": len(filas),
                       "columns": cols_unid, "rows": filas, "truncated": False})

    pendientes = sum(1 for s in salida if s.get("calculando"))
    return jsonify({"tareas": salida, "calculando": pendientes > 0})


@app.get("/api/mantenimiento")
def api_mantenimiento():
    """Contadores de mantenimiento preventivo del equipo de ordeño (tubos
    cortos, pezoneras, lubricadores, etc.), cargados por el operario en DelPro."""
    tambo = _tambo_del_request()
    data = _run_consulta("mantenimiento", tambo)
    return jsonify(data)


def _hace_texto(minutos: int) -> str:
    if minutos < 1:
        return "recién"
    if minutos < 60:
        return f"hace {minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"hace {horas} h {minutos % 60} min"
    return f"hace {horas // 24} d {horas % 24} h"


@app.get("/api/ordeno")
def api_ordeno():
    """Vacas del ordeño en la rotativa. modo=vivo → solo las que están girando
    ahora (última visita por posición en la última vuelta); si no, la sesión
    completa (último ordeño)."""
    tambo = _tambo_del_request()
    sim = request.args.get("modo") == "simulacion"
    vivo = request.args.get("modo") == "vivo"
    consulta_id = "ordeno" if sim else ("ordeno_vivo" if vivo else "ordeno")
    key = _clave(tambo, consulta_id)

    if vivo:
        # Modo en vivo: se busca dato fresco (TTL corto); si venció, se refresca
        # en segundo plano y se sirve lo último que haya para no bloquear.
        data, fresh = _cache_get(key, allow_stale=True, ttl=_VIVO_TTL_S)
        if data is None:
            _refresh_async(tambo, consulta_id)
            return jsonify({"calentando": True,
                            "mensaje": "Cargando ordeño en vivo…"}), 202
        if not fresh:
            _refresh_async(tambo, consulta_id)
    else:
        data, _ = _cache_get(key, allow_stale=True)
        if data is None:
            _refresh_async(tambo, consulta_id)
            return jsonify({"calentando": True,
                            "mensaje": "Cargando datos del ordeño…"}), 202

    alarmas_sim = None
    if sim:
        # Simulación: vacas reales del último ordeño subiendo a la plataforma,
        # con producción creciendo durante la vuelta. Solo en memoria.
        if request.args.get("reiniciar"):
            simulador.reiniciar(tambo)
        columns, rows, alarmas_sim = simulador.simular(
            tambo, data["columns"], data["rows"])
        momento = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        columns = list(data["columns"])
        rows = [list(r) for r in data["rows"]]
        # La 1ª columna (momento_ordeno) es la marca de tiempo de la sesión; se
        # saca de la tabla y se usa para el cartel EN VIVO / último ordeño.
        momento = rows[0][0] if rows else None
        if columns and columns[0] == "momento_ordeno":
            columns = columns[1:]
            rows = [r[1:] for r in rows]

    # Pegar datos por puesto desde consultas aparte cacheadas (incidencias del día
    # y alarmas de la vaca actual). Si aún no están listas, se dispara su cálculo
    # y se completa con valores por defecto.
    ip = columns.index("posicion") if "posicion" in columns else 0

    def _pegar(cache_id, cols, defecto):
        nonlocal columns, rows
        d, _f = _cache_get(_clave(tambo, cache_id), allow_stale=True)
        if d is None:
            _refresh_async(tambo, cache_id)
        mapa = {}
        if d:
            di = {c: i for i, c in enumerate(d["columns"])}
            for r in d["rows"]:
                mapa[r[di["posicion"]]] = [r[di[c]] for c in cols]
        columns = columns + cols
        rows = [r + (mapa.get(r[ip]) or list(defecto)) for r in rows]

    _pegar("ordeno_inc", _INC_COLS, [0] * len(_INC_COLS))
    if sim:
        # Las alarmas simuladas vienen alineadas fila a fila del simulador.
        columns = columns + _ALARMA_COLS
        rows = [r + a for r, a in zip(rows, alarmas_sim)]
    else:
        _pegar("ordeno_alarmas", _ALARMA_COLS, [None, None, 0, 0, 0, 0])

    en_vivo, hace, ordenando = False, "", False
    if sim:
        en_vivo, hace, ordenando = True, "recién", True
    elif momento:
        try:
            t = datetime.datetime.strptime(momento, "%Y-%m-%d %H:%M:%S")
            minutos = int((datetime.datetime.now() - t).total_seconds() // 60)
            en_vivo = minutos <= 20
            ordenando = minutos <= VIVO_LIMITE_MIN  # hay ordeño realmente en curso
            hace = _hace_texto(minutos)
        except (ValueError, TypeError):
            pass

    return jsonify({
        "modo": "vivo" if (vivo or sim) else "sesion",
        "simulacion": sim,
        "momento": momento, "en_vivo": en_vivo, "hace": hace,
        "ordenando": ordenando,
        "vacas": len(rows), "columns": columns, "rows": rows,
        "truncated": data.get("truncated", False),
    })


def _nombres_grupos(tambo: str) -> dict:
    """{oid_grupo: "Rodeo N"} — el nombre real que muestra DelPro. El OID
    interno sigue siendo la clave de las relaciones; esto es solo para mostrar."""
    try:
        data = _run_consulta("rutina_grupos_nombres", tambo)
        idx = {c: i for i, c in enumerate(data["columns"])}
        return {r[idx["grupo"]]: r[idx["nombre"]] for r in data["rows"] if r[idx["nombre"]]}
    except Exception:  # noqa: BLE001
        return {}


def _max_sesiones(tambo: str) -> int:
    """Ordeños por día que tiene declarados el tambo. Es el tope de sesiones
    que puede dar el análisis de un día; si la config no se puede leer, se usa
    el valor habitual. La consulta depende del tipo de sala (ver `salas/`):
    la rotativa lo lee de `CMSGroupMilkSetting`, la convencional de
    `ParlorHistoricalData` (no tiene esa tabla)."""
    try:
        data = _run_consulta("rutina_ordenos_dia", tambo, salas.de(tambo).sql_ordenos_por_dia())
        if data["rows"] and data["rows"][0][0]:
            return int(data["rows"][0][0])
    except Exception:  # noqa: BLE001
        pass
    return rutina.MAX_SESIONES_DEFECTO


def _grupos_pesos_de_request(tambo: str):
    """Lee los filtros opcionales de la interfaz: `grupos` (lista separada por
    comas, ej. "2,5,7") y `pesos` (JSON, ej. {"prep_90s":40,...}).
    `pesos` ausente/inválido => se usan los pesos base.
    `grupos` ausente => NO significa "todo el rodeo": se usan los grupos de
    ordeño reales de esta sala (ver `salas.de(tambo).sql_grupos()`), para no
    mezclar en el análisis corrales que ni pasan por el ordeño (secas,
    novillas, etc.)."""
    grupos_txt = request.args.get("grupos")
    if grupos_txt:
        grupos = grupos_txt.split(",")
    else:
        datos_grupos = _run_consulta("rutina_grupos", tambo, salas.de(tambo).sql_grupos())
        idx_grupo = datos_grupos["columns"].index("grupo")
        grupos = [r[idx_grupo] for r in datos_grupos["rows"]]
    pesos_txt = request.args.get("pesos")
    pesos = None
    if pesos_txt:
        try:
            pesos = json.loads(pesos_txt)
        except ValueError:
            pesos = None
    return grupos, pesos


UMBRAL_PREP_S_MIN, UMBRAL_PREP_S_MAX = 10, 600  # sanidad: rango razonable en segundos


def _umbral_prep_de_request():
    """Objetivo de colocación (segundos) pedido por la interfaz — ver el
    selector nuevo en "Configurar análisis". Ausente/inválido => None (cada
    sala usa su propio valor por defecto, ver rutina.UMBRAL_PREP_S)."""
    valor = request.args.get("umbral_prep_s")
    if not valor:
        return None
    try:
        return max(UMBRAL_PREP_S_MIN, min(UMBRAL_PREP_S_MAX, round(float(valor))))
    except (TypeError, ValueError):
        return None


@app.get("/api/rutina/grupos")
def api_rutina_grupos():
    """Grupos activos hoy (con cantidad de animales), para el selector de
    'qué grupos incluir' en el análisis de rutina."""
    tambo = _tambo_del_request()
    data = _run_consulta("rutina_grupos", tambo, salas.de(tambo).sql_grupos())
    return jsonify(data)


@app.get("/api/rutina")
def api_rutina():
    """Análisis de rutina de ordeño (identificación → colocación → retiro) de
    un día completo, separado en sesiones y puntuado 0-100%."""
    tambo = _tambo_del_request()
    grupos, pesos = _grupos_pesos_de_request(tambo)
    umbral_prep_s = _umbral_prep_de_request()
    fecha = request.args.get("fecha")
    if not fecha:
        kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
        if not kpis or not kpis.get("fecha_dato"):
            return jsonify({"calentando": True,
                            "mensaje": "Calculando fecha por defecto…"}), 202
        fecha = str(kpis["fecha_dato"])[:10]
    try:
        fecha = rutina.validar_fecha(fecha)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    key = _clave(tambo, f"rutina:{fecha}")
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        _refresh_rutina_async(tambo, fecha)
        return jsonify({"calentando": True,
                        "mensaje": "Calculando rutina de ordeño…"}), 202
    if not fresh:
        _refresh_rutina_async(tambo, fecha)

    try:
        resultado = salas.de(tambo).analizar_dia(tambo, data["columns"], data["rows"], fecha,
                                                 grupos, pesos, max_sesiones=_max_sesiones(tambo),
                                                 nombres=_nombres_grupos(tambo),
                                                 umbral_prep_s=umbral_prep_s)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    resultado["incompleto"] = data.get("truncated", False)
    return jsonify(resultado)


EVOLUCION_DIAS_DEFECTO = 365      # rango por defecto si no se pide uno explícito
EVOLUCION_MAX_PUNTOS = 60         # tope de muestras: a más rango, intervalo más grande
EVOLUCION_TTL_S = 30 * 24 * 3600  # un día pasado ya cerrado no cambia: cache larga


@app.get("/api/rutina/evolucion")
@auth.requiere_rol("admin")
def api_rutina_evolucion():
    """Evolución del score de rutina en un rango de fechas: si el rango es
    corto se muestra día por día, si es largo se muestrea (mismo intervalo en
    días, tope ~60 puntos) para no lanzar cientos de consultas pesadas. Cada
    punto reutiliza el mismo caché por fecha que /api/rutina y se completa
    progresivamente en segundo plano."""
    tambo = _tambo_del_request()
    grupos, pesos = _grupos_pesos_de_request(tambo)
    umbral_prep_s = _umbral_prep_de_request()

    hasta = request.args.get("hasta")
    if hasta:
        try:
            hasta = rutina.validar_fecha(hasta)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        fecha_fin = datetime.datetime.strptime(hasta, "%Y-%m-%d")
    else:
        kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
        if not kpis or not kpis.get("fecha_dato"):
            return jsonify({"calentando": True,
                            "mensaje": "Calculando fecha por defecto…"}), 202
        fecha_fin = datetime.datetime.strptime(str(kpis["fecha_dato"])[:10], "%Y-%m-%d")

    desde = request.args.get("desde")
    if desde:
        try:
            desde = rutina.validar_fecha(desde)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        fecha_ini = datetime.datetime.strptime(desde, "%Y-%m-%d")
    else:
        fecha_ini = fecha_fin - datetime.timedelta(days=EVOLUCION_DIAS_DEFECTO)

    if fecha_ini > fecha_fin:
        fecha_ini, fecha_fin = fecha_fin, fecha_ini

    total_dias = (fecha_fin - fecha_ini).days
    intervalo_dias = max(1, math.ceil((total_dias + 1) / EVOLUCION_MAX_PUNTOS))
    fechas, f = [], fecha_fin
    while f >= fecha_ini:
        fechas.append(f.strftime("%Y-%m-%d"))
        f -= datetime.timedelta(days=intervalo_dias)
    fechas.reverse()

    tope = _max_sesiones(tambo)
    nombres_grupos = _nombres_grupos(tambo)
    puntos, sesiones_todas, lanzadas, dias_incompletos = [], [], 0, []
    for fecha in fechas:
        data, _ = _cache_get(_clave(tambo, f"rutina:{fecha}"), allow_stale=True, ttl=EVOLUCION_TTL_S)
        if data is None:
            if lanzadas < 3:  # de a poco: es un solo SQL Express compartido
                _refresh_rutina_async(tambo, fecha)
                lanzadas += 1
            continue
        if data.get("truncated"):
            dias_incompletos.append(fecha)
        try:
            punto = salas.de(tambo).resumen_dia(tambo, data["columns"], data["rows"], fecha, grupos, pesos,
                                                max_sesiones=tope, nombres=nombres_grupos,
                                                umbral_prep_s=umbral_prep_s)
        except Exception:  # noqa: BLE001
            punto = None
        if punto:
            sesiones_todas.extend(punto.pop("detalle_sesiones"))
            puntos.append(punto)

    listas = sum(1 for f in fechas
                 if _cache_get(_clave(tambo, f"rutina:{f}"), allow_stale=True, ttl=EVOLUCION_TTL_S)[0] is not None)
    return jsonify({
        "puntos": puntos, "sesiones": sesiones_todas, "desde": fechas[0] if fechas else None,
        "hasta": fechas[-1] if fechas else None, "intervalo_dias": intervalo_dias,
        "total_puntos": len(fechas), "listas": listas, "calculando": listas < len(fechas),
        "dias_incompletos": dias_incompletos,
    })


CICLA_CACHE_TTL_S = 600  # 10 min: no tiene sentido reloguear en CICLA en cada pedido


def _refresh_cicla_async(desde, hasta):
    key = f"cicla:{desde.isoformat()}:{hasta.isoformat()}"
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            usuario = os.environ.get("CICLA_USUARIO")
            password = os.environ.get("CICLA_PASSWORD")
            cargas, incompleto = cicla.obtener_cargas(desde, hasta, usuario, password)
            _cache_set(key, {"cargas": cargas, "incompleto": incompleto})
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


INCIDENTES_CACHE_TTL_S = 900  # 15 min: las incidencias del equipo cambian de a poco


def _refresh_incidentes_async(tambo, desde, hasta):
    key = f"{tambo}:incidentes_dia:{desde.isoformat()}:{hasta.isoformat()}"
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            data = db.run_query(sql_incidentes_diarios(desde.isoformat(), hasta.isoformat()),
                                 tambo=tambo)
            _cache_set(key, data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/ordeno/incidentes_dia")
@auth.requiere_rol("admin")
def api_ordeno_incidentes_dia():
    """Incidencias del equipo de ordeño por día (% de ordeños afectados),
    para un rango de fechas elegible — bloqueos, deslizamientos, patadas,
    modo manual y recolocaciones. Base para el gráfico de líneas equivalente
    al "Incidentes de ordeño" de DelPro (que lo muestra de barras)."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=6))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde

    key = f"{tambo}:incidentes_dia:{desde.isoformat()}:{hasta.isoformat()}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=INCIDENTES_CACHE_TTL_S)
    if data is None:
        _refresh_incidentes_async(tambo, desde, hasta)
        return jsonify({"calentando": True, "mensaje": "Calculando incidencias del equipo…"}), 202
    if not fresh:
        _refresh_incidentes_async(tambo, desde, hasta)

    idx = {c: i for i, c in enumerate(data["columns"])}
    dias = []
    for r in data["rows"]:
        n = r[idx["ordenos"]] or 0
        pct = lambda campo: round(100 * (r[idx[campo]] or 0) / n, 1) if n else None  # noqa: E731
        dias.append({
            "fecha": str(r[idx["fecha"]])[:10], "ordenos": n,
            "pct_bloqueos": pct("con_bloqueo"), "pct_deslizamientos": pct("con_desliz"),
            "pct_patadas": pct("con_patada"), "pct_manual": pct("con_manual"),
            "pct_recolocaciones": pct("con_recoloc"),
        })
    return jsonify({"desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": dias})


RENDIMIENTO_CACHE_TTL_S = 900  # 15 min


def _refresh_rendimiento_async(tambo, desde, hasta):
    key = f"{tambo}:rendimiento:{desde.isoformat()}:{hasta.isoformat()}"
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            data = db.run_query(salas.de(tambo).sql_rendimiento(desde.isoformat(), hasta.isoformat()),
                                 tambo=tambo, max_rows=rutina.MAX_FILAS_DIA * rutina.RANGO_RENDIMIENTO_MAX_DIAS)
            _cache_set(key, data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/rutina/rendimiento")
@auth.requiere_rol("admin")
def api_rutina_rendimiento():
    """"Rendimiento Sala": throughput de la rotativa por sesión, para un
    rango de fechas elegible — rotaciones, ordeños/hora, producción,
    identificados/desconocidos. Réplica gráfica del reporte denso de DelPro."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=6))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > rutina.RANGO_RENDIMIENTO_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {rutina.RANGO_RENDIMIENTO_MAX_DIAS} días "
                                 "(la consulta escanea todas las visitas)."}), 400

    key = f"{tambo}:rendimiento:{desde.isoformat()}:{hasta.isoformat()}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=RENDIMIENTO_CACHE_TTL_S)
    if data is None:
        _refresh_rendimiento_async(tambo, desde, hasta)
        return jsonify({"calentando": True, "mensaje": "Calculando rendimiento de sala…"}), 202
    if not fresh:
        _refresh_rendimiento_async(tambo, desde, hasta)

    sesiones = salas.de(tambo).analizar_rendimiento(tambo, data["columns"], data["rows"],
                                                    desde.isoformat(), hasta.isoformat(),
                                                    max_sesiones=_max_sesiones(tambo))
    return jsonify({"desde": desde.isoformat(), "hasta": hasta.isoformat(), "sesiones": sesiones,
                    "truncated": data.get("truncated", False)})


# --- Análisis de flujos de ordeño -------------------------------------------
# Son cuatro escaneos de CMSMilkYield sobre el rango pedido (hasta 120 días),
# así que el TTL es largo: los flujos de días cerrados no cambian, y el único
# día que se mueve es el de hoy.
FLUJOS_CACHE_TTL_S = 1800  # 30 min


def _clave_flujos(tambo, desde, hasta):
    return f"{tambo}:flujos:{desde.isoformat()}:{hasta.isoformat()}"


def _refresh_flujos_async(tambo, desde, hasta):
    key = _clave_flujos(tambo, desde, hasta)
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    d, h = desde.isoformat(), hasta.isoformat()

    def worker():
        try:
            # Los umbrales de retirada NO son elegibles: salen de la
            # configuración de la rotativa. Se leen primero porque la consulta
            # diaria los necesita, y se guardan junto a los datos para que la
            # página muestre exactamente con qué valores se calculó.
            try:
                cfg = db.run_query(flujos.SQL_CONFIG_RETIRADA, tambo=tambo, max_rows=5)
            except Exception:  # noqa: BLE001
                cfg = None
            umbrales = flujos.umbrales_retirada(cfg)
            rmin, rmax = umbrales["retirada_min"], umbrales["retirada_max"]
            # En serie a propósito: db.py ya serializa por servidor, y lanzarlas
            # en paralelo solo agregaría presión de memoria sobre SQL Express.
            data = {
                "umbrales": umbrales,
                "dia": db.run_query(flujos.sql_por_dia(d, h, rmin, rmax), tambo=tambo,
                                    max_rows=flujos.RANGO_FLUJOS_MAX_DIAS + 2),
                "grupo": db.run_query(flujos.sql_por_grupo(d, h), tambo=tambo, max_rows=100),
                "dist": db.run_query(flujos.sql_distribucion(d, h), tambo=tambo, max_rows=200),
                "deo": db.run_query(flujos.sql_por_deo(d, h), tambo=tambo, max_rows=50),
            }
            _cache_set(key, data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/flujos/analisis")
@auth.requiere_rol("admin")
def api_flujos_analisis():
    """"Análisis de flujos de ordeño": curva de flujo por tramos, problemas de
    retirada, distribución de flujo promedio/pico y bimodalidad por DEO, para
    un rango de fechas amplio. Réplica de los informes de flujo de DelPro.

    Los umbrales de retirada no se reciben por parámetro: se leen de la
    configuración de la rotativa (`CMSMpcSetting.TakeoffLimit`) dentro del
    refresco, y viajan en la respuesta para que la página los muestre."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=29))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > flujos.RANGO_FLUJOS_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {flujos.RANGO_FLUJOS_MAX_DIAS} días "
                                 "(la consulta escanea todos los ordeños del período)."}), 400

    key = _clave_flujos(tambo, desde, hasta)
    data, fresh = _cache_get(key, allow_stale=True, ttl=FLUJOS_CACHE_TTL_S)
    if data is None:
        _refresh_flujos_async(tambo, desde, hasta)
        return jsonify({"calentando": True, "mensaje": "Analizando flujos de ordeño…"}), 202
    if not fresh:
        _refresh_flujos_async(tambo, desde, hasta)

    resultado = flujos.analizar(data["dia"], data["grupo"], data["dist"], data["deo"],
                                data["umbrales"])
    resultado["desde"] = desde.isoformat()
    resultado["hasta"] = hasta.isoformat()
    return jsonify(resultado)


# --- Proyección de rebaños ---------------------------------------------------
# Depende del estado reproductivo de hoy, que cambia de a poco: TTL largo.
PROYECCION_CACHE_TTL_S = 3600  # 1 hora


def _clave_proyeccion(tambo, desde, hasta):
    return f"{tambo}:proyeccion:{desde}:{hasta}"


def _refresh_proyeccion_async(tambo, desde, hasta):
    herd = rebano.por_defecto(tambo)
    # Los dias de gestacion y el periodo seco salen de lo que tiene
    # configurado el tambo en DelPro, no de constantes del codigo.
    gestacion = parametros.valor("dias_gestacion", tambo)
    seco = parametros.valor("dias_secado", tambo)
    key = _clave_proyeccion(tambo, desde, hasta)
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    # El histórico arranca antes de lo pedido para poder comparar contra el
    # mismo mes del año pasado y para reconstruir el balance hacia atrás.
    ini = proyeccion._sumar_meses(desde, -14)
    fin_hist = proyeccion._mes(datetime.date.today())

    def worker():
        try:
            data = {
                "lact": db.run_query(proyeccion.sql_lactantes_hoy(herd), tambo=tambo, max_rows=5),
                "partos_reales": db.run_query(proyeccion.sql_partos_reales(ini, fin_hist, herd),
                                              tambo=tambo, max_rows=200),
                "partos_prev": db.run_query(proyeccion.sql_partos_previstos(herd, gestacion), tambo=tambo, max_rows=200),
                "salidas": db.run_query(proyeccion.sql_salidas_reales(ini, fin_hist, herd),
                                        tambo=tambo, max_rows=200),
                "kg": db.run_query(proyeccion.sql_kg_por_vaca(ini, fin_hist, herd), tambo=tambo, max_rows=200),
                "descartadas": db.run_query(proyeccion.sql_preneces_descartadas(herd), tambo=tambo, max_rows=5),
                "lact_hist": db.run_query(proyeccion.sql_lactantes_historico(ini, fin_hist, herd),
                                          tambo=tambo, max_rows=200),
            }
            _cache_set(key, data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/proyeccion/rebanos")
@auth.requiere_rol("admin")
def api_proyeccion_rebanos():
    """"Proyección de rebaños": evolución mensual de vacas lactantes y
    producción, real hacia atrás y proyectada hacia adelante, con comparación
    contra el mismo mes del año pasado. Réplica del informe de DelPro.

    Ver `proyeccion.py` para el modelo: la ecuación de balance y la fórmula de
    producción están verificadas contra el informe; los partos previstos salen
    solo de preñeces confirmadas, así que a largo plazo quedan por debajo de
    los de DelPro (que además simula preñeces futuras)."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    patron = "%Y-%m"
    try:
        desde = request.args.get("desde") or proyeccion._sumar_meses(proyeccion._mes(hoy), -5)
        hasta = request.args.get("hasta") or proyeccion._sumar_meses(proyeccion._mes(hoy), 11)
        datetime.datetime.strptime(desde, patron)
        datetime.datetime.strptime(hasta, patron)
    except ValueError:
        return jsonify({"error": "Meses inválidos (se espera AAAA-MM)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if len(proyeccion.rango_meses(desde, hasta)) > proyeccion.RANGO_MESES_MAX:
        return jsonify({"error": f"El rango no puede superar {proyeccion.RANGO_MESES_MAX} meses."}), 400

    key = _clave_proyeccion(tambo, desde, hasta)
    data, fresh = _cache_get(key, allow_stale=True, ttl=PROYECCION_CACHE_TTL_S)
    if data is None:
        _refresh_proyeccion_async(tambo, desde, hasta)
        return jsonify({"calentando": True, "mensaje": "Calculando proyección de rebaños…"}), 202
    if not fresh:
        _refresh_proyeccion_async(tambo, desde, hasta)

    resultado = proyeccion.analizar(data["lact"], data["partos_reales"], data["partos_prev"],
                                    data["salidas"], data["kg"], data["descartadas"],
                                    data["lact_hist"], desde, hasta, hoy,
                                    gestacion=parametros.valor("dias_gestacion", tambo),
                                    periodo_seco=parametros.valor("dias_secado", tambo))
    resultado["desde"] = desde
    resultado["hasta"] = hasta
    return jsonify(resultado)


# --- Análisis reproductivo ---------------------------------------------------
REPRO_CACHE_TTL_S = 3600  # 1 hora: el estado reproductivo cambia de a poco


@app.get("/api/reproduccion/metas")
@auth.requiere_rol("admin")
def api_reproduccion_metas():
    """Catálogo de indicadores con la meta y condición vigentes de cada uno."""
    return jsonify({"metas": reproduccion.metas(),
                    "condiciones": list(reproduccion.CONDICIONES)})


@app.post("/api/reproduccion/metas")
@auth.requiere_rol("admin")
def api_reproduccion_guardar_metas():
    """Guarda metas y condiciones. Body: {"cambios": {clave: {meta, condicion}}}."""
    cambios = (request.json or {}).get("cambios") or {}
    try:
        actualizadas = reproduccion.guardar_metas(cambios)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"metas": actualizadas, "guardados": len(cambios)})


def _refresh_repro_async(tambo, rangos, key):
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            hoy = datetime.date.today().isoformat()
            data = {}
            for nombre, (desde, hasta, herd) in rangos.items():
                # Inventario reconstruido a varias fechas del rango y luego
                # promediado: el inventario de un trimestre es el promedio del
                # trimestre, no el de un día suelto.
                data[f"{nombre}:inv"] = db.run_query(
                    reproduccion.sql_inventario_historico(
                        reproduccion.fechas_muestra(desde, hasta), herd,
                        parametros.valor("dias_secado", tambo)),
                    tambo=tambo, max_rows=40)
                data[f"{nombre}:prenez"] = db.run_query(
                    reproduccion.sql_prenez_por_del(desde, hasta, herd), tambo=tambo, max_rows=20)
                # Ventanas de ciclos, contadas hacia atrás desde el fin del rango.
                for suf, ciclos in (("c1", 1), ("c3", 3), ("c12", 18)):
                    data[f"{nombre}:{suf}"] = db.run_query(
                        reproduccion.sql_servicios_por_ciclo(
                            hasta, ciclos, herd, parametros.valor("ciclo_celo", tambo)),
                        tambo=tambo, max_rows=200)
                data[f"{nombre}:ab3c"] = db.run_query(
                    reproduccion.sql_abortos(
                        (datetime.date.fromisoformat(hasta)
                         - datetime.timedelta(days=3 * reproduccion.CICLO_DIAS)).isoformat(),
                        hasta, herd), tambo=tambo, max_rows=20)
                data[f"{nombre}:ab12m"] = db.run_query(
                    reproduccion.sql_abortos(
                        (datetime.date.fromisoformat(hasta)
                         - datetime.timedelta(days=365)).isoformat(),
                        hasta, herd), tambo=tambo, max_rows=20)
                # Indicadores del presente: dependen del rebaño, no del rango.
                data[f"{nombre}:pren"] = db.run_query(
                    reproduccion.sql_pct_prenadas(herd), tambo=tambo, max_rows=5)
                data[f"{nombre}:no_insem"] = db.run_query(
                    reproduccion.sql_no_inseminar(herd), tambo=tambo, max_rows=5)
                data[f"{nombre}:es_actual"] = hasta >= hoy
            _cache_set(key, data)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/reproduccion/resultados")
@auth.requiere_rol("admin")
def api_reproduccion_resultados():
    """Evalúa el rodeo contra las metas, para dos rangos comparables.

    Réplica del informe de metas reproductivas de DelPro. Ver
    `reproduccion.py`: la calibración de metas es exacta, los valores medidos
    dependen de los eventos reproductivos de DDM y cada indicador declara su
    nivel de confianza."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    def leer(nombre, defecto):
        val = request.args.get(nombre)
        if not val:
            return defecto
        return datetime.datetime.strptime(val, "%Y-%m-%d").date()
    try:
        d1 = leer("desde1", datetime.date(hoy.year - 1, 1, 1))
        h1 = leer("hasta1", datetime.date(hoy.year - 1, 12, 31))
        d2 = leer("desde2", datetime.date(hoy.year, 1, 1))
        h2 = leer("hasta2", hoy)
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if d1 > h1:
        d1, h1 = h1, d1
    if d2 > h2:
        d2, h2 = h2, d2

    # Rebaño por rango: la base la comparten varios tambos (ver `rebano.py`).
    # Por defecto, el del tambo; "todos" para no filtrar.
    def leer_herd(nombre):
        v = (request.args.get(nombre) or "").strip()
        if not v:
            return rebano.por_defecto(tambo)
        if v.lower() == rebano.TODOS:
            return rebano.TODOS
        try:
            return int(v)
        except ValueError:
            raise ValueError(f"Rebaño inválido: {v!r}")
    try:
        herd1, herd2 = leer_herd("rebano1"), leer_herd("rebano2")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    rangos = {"r1": (d1.isoformat(), h1.isoformat(), herd1),
              "r2": (d2.isoformat(), h2.isoformat(), herd2)}
    key = f"{tambo}:repro:{d1}:{h1}:{herd1}:{d2}:{h2}:{herd2}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        _refresh_repro_async(tambo, rangos, key)
        return jsonify({"calentando": True, "mensaje": "Evaluando indicadores reproductivos…"}), 202
    if not fresh:
        _refresh_repro_async(tambo, rangos, key)

    def valores(nombre):
        return reproduccion.valores_de_rango(
            data[f"{nombre}:inv"], data[f"{nombre}:pren"], data[f"{nombre}:prenez"],
            data[f"{nombre}:c1"], data[f"{nombre}:c3"], data[f"{nombre}:c12"],
            data[f"{nombre}:ab3c"], data[f"{nombre}:ab12m"], data[f"{nombre}:no_insem"],
            es_actual=data.get(f"{nombre}:es_actual", False))

    resultado = reproduccion.armar(
        valores("r1"), valores("r2"),
        {"desde": rangos["r1"][0], "hasta": rangos["r1"][1], "rebano": herd1},
        {"desde": rangos["r2"][0], "hasta": rangos["r2"][1], "rebano": herd2},
        espera_voluntaria=parametros.valor("espera_voluntaria", tambo),
        ciclo_dias=parametros.valor("ciclo_celo", tambo))
    return jsonify(resultado)


@app.get("/api/reproduccion/preneces")
@auth.requiere_rol("admin")
def api_reproduccion_preneces():
    """"Indicadores de Preñez": cuándo quedan preñados los animales, por tramo
    de días en ordeñe y por mes. Réplica del "Gráfico de preñez" de DelPro
    (verificado: 1.382 concepciones en 26/07/2025-26/07/2026, 587 L1 y 795
    L2+). Ver `preneces.py`."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=365))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > preneces.RANGO_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {preneces.RANGO_MAX_DIAS} días."}), 400

    tipo = (request.args.get("tipo") or preneces.TIPO_VACAS).lower()
    if tipo not in preneces.TIPOS:
        return jsonify({"error": f"Tipo inválido (esperado: {', '.join(preneces.TIPOS)})."}), 400
    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400

    key = f"{tambo}:preneces:{desde}:{hasta}:{tipo}:{herd}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        def worker(k=key):
            with _cache_lock:
                if k in _refreshing:
                    return
                _refreshing.add(k)

            def run():
                try:
                    _cache_set(k, {
                        dim: db.run_query(
                            preneces.sql_concepciones(desde.isoformat(), hasta.isoformat(),
                                                      tipo, dim, herd),
                            tambo=tambo, max_rows=100)
                        for dim in ("deo", "mes")})
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    with _cache_lock:
                        _refreshing.discard(k)

            threading.Thread(target=run, daemon=True).start()
        worker()
        return jsonify({"calentando": True, "mensaje": "Calculando indicadores de preñez…"}), 202

    return jsonify({
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "tipo": tipo, "rebano": herd,
        "deo": preneces.analizar(data["deo"], "deo", tipo),
        "mes": preneces.analizar(data["mes"], "mes", tipo),
        "tramos_deo": [t[0] for t in preneces.TRAMOS_DEO],
    })


@app.get("/api/reproduccion/performance")
@auth.requiere_rol("admin")
def api_reproduccion_performance():
    """"Performance": curva de lactancia y peak por número de lactancia, o
    distribución de la producción por lactancia, grupo y días en ordeñe.
    Ver `performance.py`."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()

    def leer(nombre, defecto):
        v = request.args.get(nombre)
        return datetime.datetime.strptime(v, "%Y-%m-%d").date() if v else defecto
    try:
        hasta = leer("hasta", hoy)
        desde = leer("desde", hasta - datetime.timedelta(days=365))
        comp_hasta = leer("comp_hasta", None)
        comp_desde = leer("comp_desde", None)
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > performance.RANGO_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {performance.RANGO_MAX_DIAS} días."}), 400

    reporte = (request.args.get("reporte") or performance.REPORTE_PEAK).lower()
    if reporte not in performance.REPORTES:
        return jsonify({"error": f"Reporte inválido (esperado: {', '.join(performance.REPORTES)})."}), 400

    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400

    comparar = bool(comp_desde and comp_hasta)
    key = (f"{tambo}:perf:{reporte}:{desde}:{hasta}:{herd}"
           f":{comp_desde if comparar else ''}:{comp_hasta if comparar else ''}")
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            d, h = desde.isoformat(), hasta.isoformat()
            try:
                if reporte == performance.REPORTE_PEAK:
                    res = {
                        "curva": db.run_query(performance.sql_curva(d, h, herd),
                                              tambo=tambo, max_rows=100),
                        "peak": db.run_query(performance.sql_peak(d, h, herd),
                                             tambo=tambo, max_rows=20),
                    }
                    if comparar:
                        res["curva_comp"] = db.run_query(
                            performance.sql_curva(comp_desde.isoformat(),
                                                  comp_hasta.isoformat(), herd),
                            tambo=tambo, max_rows=100)
                else:
                    res = {dim: db.run_query(performance.sql_distribucion(d, h, dim, herd),
                                             tambo=tambo, max_rows=100)
                           for dim in ("lactancia", "grupo", "deo")}
                    res["concentracion"] = db.run_query(
                        performance.sql_concentracion(d, h, herd), tambo=tambo, max_rows=20)
                _cache_set(k, res)
            except Exception:  # noqa: BLE001
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True, "mensaje": "Calculando performance…"}), 202

    base = {"desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "reporte": reporte, "rebano": herd}
    if reporte == performance.REPORTE_PEAK:
        base.update(performance.armar_peak(data["curva"], data["peak"], data.get("curva_comp")))
        if comparar:
            base["comp_desde"] = comp_desde.isoformat()
            base["comp_hasta"] = comp_hasta.isoformat()
    else:
        base.update(performance.armar_distribucion(
            {d: data[d] for d in ("lactancia", "grupo", "deo")}, data["concentracion"]))
    return jsonify(base)


@app.get("/api/reproduccion/partos_secados")
@auth.requiere_rol("admin")
def api_partos_secados():
    """"Partos y Secados Proyectados": qué vaca pare y cuál hay que secar, y la
    proyección mensual de vacas en ordeñe que sale de ahí. Ver
    `partos_secados.py`."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()

    categoria = (request.args.get("categoria") or "todas").lower()
    if categoria not in partos_secados.CATEGORIAS:
        return jsonify({"error": f"Categoría inválida (esperado: "
                                 f"{', '.join(partos_secados.CATEGORIAS)})."}), 400
    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400
    try:
        meses = max(3, min(24, int(request.args.get("meses") or 9)))
        descarte = request.args.get("descarte")
        descarte = int(descarte) if descarte not in (None, "") else None
    except ValueError:
        return jsonify({"error": "Parámetros numéricos inválidos."}), 400

    key = f"{tambo}:partos_secados:{categoria}:{herd}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=PROYECCION_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            try:
                _cache_set(k, {
                    "esperados": db.run_query(partos_secados.sql_esperados(
                        categoria, herd, parametros.valor("dias_gestacion", tambo),
                        parametros.valor("dias_secado", tambo)),
                                              tambo=tambo, max_rows=4000),
                    "descarte": db.run_query(partos_secados.sql_descarte_mensual(herd),
                                             tambo=tambo, max_rows=5),
                    "vo": db.run_query(partos_secados.sql_vo_hoy(herd), tambo=tambo, max_rows=5),
                })
            except Exception:  # noqa: BLE001
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True, "mensaje": "Proyectando partos y secados…"}), 202

    resultado = partos_secados.analizar(data["esperados"], data["descarte"], data["vo"],
                                        hoy, meses=meses, descarte_manual=descarte)
    resultado.update({"categoria": categoria, "rebano": herd,
                      "categorias": partos_secados.CATEGORIAS})
    return jsonify(resultado)


@app.get("/api/reproduccion/parametros")
@auth.requiere_rol("admin")
def api_reproduccion_parametros():
    """Parámetros reproductivos configurados en DelPro (`ReproductionSetting`),
    que son los que gobiernan todos los cálculos. Ver `parametros.py`."""
    tambo = _tambo_del_request()
    key = f"{tambo}:parametros_repro"
    data, _ = _cache_get(key, allow_stale=True, ttl=1800)
    if data is None:
        try:
            data = db.run_query(parametros.SQL, tambo=tambo, max_rows=60)
            _cache_set(key, data)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
    return jsonify({"parametros": parametros.listado(data, tambo), "tambo": tambo,
                    "tambo_nombre": tambos.TAMBOS.get(tambo, {}).get("nombre", tambo)})


@app.post("/api/reproduccion/parametros")
@auth.requiere_rol("admin")
def api_reproduccion_guardar_parametros():
    """Guarda los valores propios del tambo, que pisan a los de DelPro.

    Body: {"cambios": {clave: numero}}. Un valor vacío borra el ajuste y hace
    que ese parámetro vuelva a tomar lo que dice DelPro."""
    tambo = _tambo_del_request()
    try:
        parametros.guardar_ajustes(tambo, (request.json or {}).get("cambios") or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Los cálculos cacheados quedaron hechos con los valores viejos.
    with _cache_lock:
        for k in [k for k in _cache if k.startswith(f"{tambo}:")]:
            _cache.pop(k, None)
    key = f"{tambo}:parametros_repro"
    data, _ = _cache_get(key, allow_stale=True, ttl=1800)
    if data is None:
        data = db.run_query(parametros.SQL, tambo=tambo, max_rows=60)
        _cache_set(key, data)
    return jsonify({"parametros": parametros.listado(data, tambo),
                    "ajustes": parametros.ajustes_de(tambo)})


@app.get("/api/reproduccion/tasa_prenez")
@auth.requiere_rol("admin")
def api_tasa_prenez():
    """"Tasa de preñez": el embudo aptas → celo → servicio → preñez, por ciclo
    de 21 días o por mes. Ver `tasa_prenez.py`."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=365))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > tasa_prenez.RANGO_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {tasa_prenez.RANGO_MAX_DIAS} días."}), 400

    tipo = (request.args.get("tipo") or tasa_prenez.TIPO_VACA).lower()
    if tipo not in tasa_prenez.TIPOS:
        return jsonify({"error": f"Tipo inválido (esperado: {', '.join(tasa_prenez.TIPOS)})."}), 400
    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400

    # Los umbrales salen de la configuración del tambo, no de constantes.
    pev = parametros.valor("espera_voluntaria", tambo)
    ciclo = parametros.valor("ciclo_celo", tambo)
    edad = parametros.valor("novillas_primera_ia", tambo, 447)

    key = f"{tambo}:tasa_prenez:{desde}:{hasta}:{tipo}:{herd}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            try:
                salida = {}
                for dim in (tasa_prenez.DIM_CICLO, tasa_prenez.DIM_MES):
                    vs = tasa_prenez.ventanas(desde.isoformat(), hasta.isoformat(), dim, ciclo)
                    # Una sola consulta para todas las ventanas: antes era una
                    # por ventana y tardaba ~166 s en total.
                    salida[dim] = {
                        "ventanas": vs,
                        "datos": db.run_query(
                            tasa_prenez.sql_embudo(vs, tipo, pev, edad, herd),
                            tambo=tambo, max_rows=100),
                    }
                _cache_set(k, salida)
            except Exception:  # noqa: BLE001
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True, "mensaje": "Calculando el embudo reproductivo…"}), 202

    return jsonify({
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "tipo": tipo, "rebano": herd,
        "ciclo": tasa_prenez.analizar(data["ciclo"]["datos"], data["ciclo"]["ventanas"],
                                      tipo, ciclo, pev),
        "mes": tasa_prenez.analizar(data["mes"]["datos"], data["mes"]["ventanas"],
                                    tipo, ciclo, pev),
    })


@app.get("/api/reproduccion/gestacion")
@auth.requiere_rol("admin")
def api_reproduccion_gestacion():
    """"Análisis de Gestación": duración real de las gestaciones por mes de
    parto, contra el parámetro de días de gestación que usa el tambo.
    Ver `gestacion.py`."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=365))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days > gestacion.RANGO_MAX_DIAS:
        return jsonify({"error": f"El rango no puede superar {gestacion.RANGO_MAX_DIAS} días."}), 400

    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400

    key = f"{tambo}:gestacion:{desde}:{hasta}:{herd}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            d, h = desde.isoformat(), hasta.isoformat()
            try:
                _cache_set(k, {
                    "mes": db.run_query(gestacion.sql_por_mes(d, h, herd),
                                        tambo=tambo, max_rows=200),
                    "dist": db.run_query(gestacion.sql_distribucion(d, h, herd),
                                         tambo=tambo, max_rows=300),
                })
            except Exception:  # noqa: BLE001
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True, "mensaje": "Analizando gestaciones…"}), 202

    resultado = gestacion.analizar(data["mes"], data["dist"],
                                   parametros.valor("dias_gestacion", tambo))
    resultado.update({"desde": desde.isoformat(), "hasta": hasta.isoformat(), "rebano": herd})
    return jsonify(resultado)


def sql_servicios_mensuales(desde, hasta, herd):
    """Servicios y concepciones por mes. Una concepción es un servicio que
    después tuvo un chequeo de preñez positivo apuntándole."""
    return f"""
        WITH serv AS (
            SELECT FORMAT(ae.DateAndTime, 'yyyy-MM') AS mes,
                   CASE WHEN EXISTS (SELECT 1 FROM EventPregCheck p
                                     WHERE p.EffectiveInsemination = i.OID AND p.Result = 1)
                        THEN 1 ELSE 0 END AS quedo
            FROM EventInsemination i
            JOIN AbstractAnimalEvent ae ON ae.OID = i.OID AND ae.GCRecord IS NULL
            WHERE ae.DateAndTime >= '{desde}' AND ae.DateAndTime <= '{hasta}'
              AND ae.LactationNumber >= 1
              AND {rebano.filtro_por_animal('ae.BasicAnimal', herd)}
        )
        SELECT mes, COUNT(*) AS servicios, SUM(quedo) AS concepciones
        FROM serv GROUP BY mes
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


@app.get("/api/reproduccion/ith")
@auth.requiere_rol("admin")
def api_reproduccion_ith():
    """Estrés calórico contra reproducción: servicios, concepción e ITH por mes.

    Ver `clima.py` — el análisis mostró que la hipótesis simple ("el calor baja
    la concepción") no se sostiene con estos datos, y que lo que se derrumba en
    verano son los servicios. El gráfico está armado para que eso se lea."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=730))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde

    herd_param = (request.args.get("rebano") or "").strip()
    if not herd_param:
        herd = rebano.por_defecto(tambo)
    elif herd_param.lower() == rebano.TODOS:
        herd = rebano.TODOS
    else:
        try:
            herd = int(herd_param)
        except ValueError:
            return jsonify({"error": f"Rebaño inválido: {herd_param!r}"}), 400

    key = f"{tambo}:ith:{desde}:{hasta}:{herd}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=REPRO_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            try:
                _cache_set(k, {
                    "servicios": db.run_query(
                        sql_servicios_mensuales(desde.isoformat(), hasta.isoformat(), herd),
                        tambo=tambo, max_rows=100),
                    "diario": clima.ith_diario(desde.isoformat(), hasta.isoformat()),
                })
            except Exception:  # noqa: BLE001
                pass
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True, "mensaje": "Trayendo el clima y cruzándolo…"}), 202

    resultado = clima.armar(data["servicios"], data["diario"], hoy)
    resultado.update({"desde": desde.isoformat(), "hasta": hasta.isoformat(), "rebano": herd})
    return jsonify(resultado)


@app.get("/api/reproduccion/rebanos")
@auth.requiere_rol("admin")
def api_reproduccion_rebanos():
    """Rebaños de la base, para el desplegable de cada rango. La base está
    compartida con otros tambos: ver `rebano.py`."""
    tambo = _tambo_del_request()
    key = f"{tambo}:rebanos"
    data, _ = _cache_get(key, allow_stale=True, ttl=3600)
    if data is None:
        try:
            data = db.run_query(rebano.SQL_LISTA, tambo=tambo, max_rows=20)
            _cache_set(key, data)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
    return jsonify({"rebanos": rebano.listar(data)})


@app.get("/api/cicla/cargas")
@auth.requiere_rol("admin")
def api_cicla_cargas():
    """Litros medidos (CICLA) vs declarados y temperatura de entrega, por
    carga del caudalímetro. Login vía CICLA_USUARIO/CICLA_PASSWORD (env)."""
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=6))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde

    usuario = os.environ.get("CICLA_USUARIO")
    password = os.environ.get("CICLA_PASSWORD")
    if not usuario or not password:
        return jsonify({"error": "Faltan las variables de entorno CICLA_USUARIO / "
                                 "CICLA_PASSWORD (ver INSTALL.md)."}), 400

    key = f"cicla:{desde.isoformat()}:{hasta.isoformat()}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=CICLA_CACHE_TTL_S)
    if data is None:
        try:
            cargas, incompleto = cicla.obtener_cargas(desde, hasta, usuario, password)
        except cicla.CiclaError as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"No se pudo consultar CICLA: {exc}"}), 502
        data = {"cargas": cargas, "incompleto": incompleto}
        _cache_set(key, data)
    elif not fresh:
        _refresh_cicla_async(desde, hasta)

    return jsonify({
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "cargas": data["cargas"], "resumen": cicla.resumen(data["cargas"]),
        "incompleto": data.get("incompleto", False),
    })


LASER_CACHE_TTL_S = 900  # 15 min


def _promedio(items, clave):
    vals = [x[clave] for x in items if x.get(clave) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _fecha_ar_a_iso(txt):
    """'1/7/2026' -> '2026-07-01'."""
    d, m, a = txt.strip().split("/")
    return f"{a}-{m.zfill(2)}-{d.zfill(2)}"


def _refresh_laser_async():
    key = "laser:actual"
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            usuario = os.environ.get("LASER_USUARIO")
            password = os.environ.get("LASER_PASSWORD")
            entregas = laserenisima.obtener_entregas(usuario, password)
            _cache_set(key, {"entregas": entregas})
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


@app.get("/api/laser/entregas")
@auth.requiere_rol("admin")
def api_laser_entregas():
    """Litros/grasa/proteínas/UFC/temperatura OFICIALES de La Serenísima
    (comprador) para el tambo 1565, período actual. Login vía
    LASER_USUARIO/LASER_PASSWORD (env)."""
    usuario = os.environ.get("LASER_USUARIO")
    password = os.environ.get("LASER_PASSWORD")
    if not usuario or not password:
        return jsonify({"error": "Faltan las variables de entorno LASER_USUARIO / "
                                 "LASER_PASSWORD (ver INSTALL.md)."}), 400

    key = "laser:actual"
    data, fresh = _cache_get(key, allow_stale=True, ttl=LASER_CACHE_TTL_S)
    if data is None:
        try:
            entregas = laserenisima.obtener_entregas(usuario, password)
        except laserenisima.LaserError as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"No se pudo consultar La Serenísima: {exc}"}), 502
        data = {"entregas": entregas}
        _cache_set(key, data)
    elif not fresh:
        _refresh_laser_async()

    entregas = data["entregas"]
    litros = [e["litros"] for e in entregas if e["litros"] is not None]
    return jsonify({
        "entregas": entregas,
        "resumen": {
            "total_entregas": len(entregas),
            "litros_total": round(sum(litros)) if litros else None,
            "grasa_promedio": _promedio(entregas, "grasa"),
            "proteinas_promedio": _promedio(entregas, "proteinas"),
            "ufc_promedio": _promedio(entregas, "ufc"),
            "temp_promedio": _promedio(entregas, "temperatura"),
        },
    })


@app.get("/api/entregas/comparar")
@auth.requiere_rol("admin")
def api_entregas_comparar():
    """Compara, por día, los litros medidos por el caudalímetro (CICLA) contra
    los litros OFICIALES recibidos por el comprador (La Serenísima)."""
    hoy = datetime.date.today()
    try:
        hasta = (datetime.datetime.strptime(request.args["hasta"], "%Y-%m-%d").date()
                 if request.args.get("hasta") else hoy)
        desde = (datetime.datetime.strptime(request.args["desde"], "%Y-%m-%d").date()
                 if request.args.get("desde") else hasta - datetime.timedelta(days=6))
    except ValueError:
        return jsonify({"error": "Fechas inválidas (se espera AAAA-MM-DD)."}), 400
    if desde > hasta:
        desde, hasta = hasta, desde

    cicla_usuario, cicla_pw = os.environ.get("CICLA_USUARIO"), os.environ.get("CICLA_PASSWORD")
    laser_usuario, laser_pw = os.environ.get("LASER_USUARIO"), os.environ.get("LASER_PASSWORD")
    if not (cicla_usuario and cicla_pw and laser_usuario and laser_pw):
        return jsonify({"error": "Faltan variables de entorno de CICLA y/o La Serenísima."}), 400

    key_c = f"cicla:{desde.isoformat()}:{hasta.isoformat()}"
    data_c, _ = _cache_get(key_c, allow_stale=True, ttl=CICLA_CACHE_TTL_S)
    if data_c is None:
        try:
            cargas_c, incompleto_c = cicla.obtener_cargas(desde, hasta, cicla_usuario, cicla_pw)
            data_c = {"cargas": cargas_c, "incompleto": incompleto_c}
            _cache_set(key_c, data_c)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"CICLA: {exc}"}), 502

    key_l = "laser:actual"
    data_l, _ = _cache_get(key_l, allow_stale=True, ttl=LASER_CACHE_TTL_S)
    if data_l is None:
        try:
            data_l = {"entregas": laserenisima.obtener_entregas(laser_usuario, laser_pw)}
            _cache_set(key_l, data_l)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"La Serenísima: {exc}"}), 502

    desde_s, hasta_s = desde.isoformat(), hasta.isoformat()
    # Solo las cargas que salieron a La Serenísima. Las de guachera las mide el
    # mismo caudalímetro pero no se venden: contarlas haría aparecer un desvío
    # contra el comprador que no existe.
    cargas_venta = cicla.solo_ventas(data_c["cargas"])
    cicla_por_dia, excluidos_por_dia = {}, {}
    for c in data_c["cargas"]:
        clave = _fecha_ar_a_iso(c["fecha"].split(" ")[0])
        if not (desde_s <= clave <= hasta_s) or c["lts_cicla"] is None:
            continue
        destino = excluidos_por_dia if not c.get("es_venta") else cicla_por_dia
        destino[clave] = destino.get(clave, 0) + c["lts_cicla"]

    laser_por_dia = {}
    for e in data_l["entregas"]:
        clave = _fecha_ar_a_iso(e["fecha"])
        if desde_s <= clave <= hasta_s and e["litros"] is not None:
            laser_por_dia[clave] = laser_por_dia.get(clave, 0) + e["litros"]

    dias = sorted(set(cicla_por_dia) | set(laser_por_dia))
    puntos = []
    for dia in dias:
        lc, ll = cicla_por_dia.get(dia), laser_por_dia.get(dia)
        dif = (lc - ll) if (lc is not None and ll is not None) else None
        dif_pct = round(dif / ll * 100, 1) if (dif is not None and ll) else None
        puntos.append({
            "fecha": dia, "lts_cicla": lc, "lts_serenisima": ll,
            "lts_otros_destinos": excluidos_por_dia.get(dia),
            "diferencia": dif, "diferencia_pct": dif_pct,
            "alerta": dif_pct is not None and abs(dif_pct) > cicla.UMBRAL_DIF_PCT,
        })

    resumen_c = cicla.resumen(data_c["cargas"])
    return jsonify({"desde": desde_s, "hasta": hasta_s, "dias": puntos,
                    "cicla_incompleto": data_c.get("incompleto", False),
                    "cargas_venta": len(cargas_venta),
                    "cargas_totales": len(data_c["cargas"]),
                    "lts_otros_destinos": round(sum(excluidos_por_dia.values())),
                    "destinos": resumen_c["destinos"],
                    "destinos_sin_clasificar": resumen_c["destinos_sin_clasificar"]})


@app.post("/api/consulta")
def consulta():
    consulta_id = (request.json or {}).get("id", "")
    item = CONSULTAS.get(consulta_id)
    if not item:
        return jsonify({"error": "Consulta no encontrada."}), 404
    tambo = _tambo_del_request()
    try:
        data = _run_consulta(consulta_id, tambo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "titulo": item["titulo"], "grafica": item["grafica"],
        "sql": item["sql"].strip(), **data,
    })


@app.post("/api/preguntar")
def preguntar():
    if not ai.api_disponible():
        return jsonify({
            "error": "La API de Claude no está configurada. Define la variable de "
                     "entorno ANTHROPIC_API_KEY y reinicia la aplicación."
        }), 503
    pregunta = ((request.json or {}).get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"error": "Escribe una pregunta."}), 400
    tambo = _tambo_del_request()
    # Candado de producción: no se ejecuta SQL generado por IA contra una base en
    # vivo. Solo quedan disponibles el dashboard, la rotativa, las tareas y las
    # consultas fijas (todas de solo lectura).
    if tambos.es_produccion(tambo):
        return jsonify({
            "error": "Las preguntas por IA están deshabilitadas para bases de "
                     "producción en vivo (por seguridad). Usá las consultas "
                     "predefinidas y el dashboard."
        }), 403
    try:
        plan = ai.pregunta_a_sql(pregunta)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Error generando la consulta: {exc}"}), 502
    try:
        data = db.run_query(plan["sql"], tambo=tambo)
    except db.UnsafeQueryError as exc:
        return jsonify({"error": f"Consulta rechazada por seguridad: {exc}"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Error ejecutando SQL: {exc}", "sql": plan.get("sql")}), 500
    analisis = ""
    try:
        analisis = ai.analizar_resultados(pregunta, data["columns"], data["rows"])
    except Exception:  # noqa: BLE001
        pass  # el análisis es opcional; la consulta ya respondió
    return jsonify({
        "titulo": plan.get("titulo", pregunta),
        "supuestos": plan.get("supuestos", ""),
        "grafica": plan.get("grafica", {"tipo": "table", "eje_x": "", "series": []}),
        "sql": plan.get("sql", ""),
        "analisis": analisis,
        **data,
    })


# ---------------------------------------------------------------------------
# Alertas por WhatsApp (Twilio): revisa dos veces al día (8:00 y 20:00) las
# condiciones fuera de rango y avisa UNA vez por condición nueva (no reenvía
# mientras siga activa). No dispara consultas SQL pesadas nuevas: para
# rutina/incidencias lee la misma caché que ya usa el dashboard (si no está
# lista, la dispara para el próximo ciclo en vez de forzarla ahora).
# ---------------------------------------------------------------------------
ALERTA_HORARIOS = (8, 20)         # horas del día (24h) en que se revisa y avisa
ALERTA_TEMP_CICLA_C = 5.0         # temperatura del caudalímetro (más estricta que la visual de 4°C)
ALERTA_UFC_LASER = 40.0           # U.F.C. de La Serenísima
ALERTA_RUTINA_SCORE_MIN = 60      # score de una sesión de rutina de ordeño

_alertas_avisadas: set = set()
_alertas_lock = threading.Lock()

# Canales de alerta disponibles: WhatsApp (Twilio, de pago) y Telegram/Email
# (gratis). Cada uno se manda solo si está CONFIGURADO (credenciales puestas
# por variable de entorno) y ACTIVADO (tildado en la interfaz).
_CANALES_MOD = {"whatsapp": whatsapp, "telegram": telegram_bot, "correo": correo}


def _canales_disponibles():
    """Módulos de canal configurados y tildados ahora mismo."""
    return [mod for nombre, mod in _CANALES_MOD.items()
            if config_alertas.activo(nombre) and mod.configurado()]


def _enviar_a_canales_activos(mensaje: str):
    """Manda el mensaje por todos los canales activos. Solo levanta error si
    NINGUNO pudo mandarlo (para no tapar un canal que sí funcionó)."""
    canales = _canales_disponibles()
    if not canales:
        raise RuntimeError("No hay ningún canal de alerta configurado y activado.")
    ultimo_error = None
    enviado = False
    for mod in canales:
        try:
            mod.enviar(mensaje)
            enviado = True
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
    if not enviado:
        raise ultimo_error


def _avisar_si_nuevo(clave: str, mensaje: str):
    """Manda la alerta solo la primera vez que aparece esta condición. Si el
    envío falla, se olvida la marca para reintentar en el próximo ciclo."""
    with _alertas_lock:
        if clave in _alertas_avisadas:
            return
        _alertas_avisadas.add(clave)
    try:
        _enviar_a_canales_activos(mensaje)
    except Exception:  # noqa: BLE001
        with _alertas_lock:
            _alertas_avisadas.discard(clave)


def _revisar_cicla_whatsapp(tambo: str):
    usuario, password = os.environ.get("CICLA_USUARIO"), os.environ.get("CICLA_PASSWORD")
    if not (usuario and password):
        return
    hoy = datetime.date.today()
    cargas, _incompleto = cicla.obtener_cargas(hoy - datetime.timedelta(days=1), hoy, usuario, password)
    for c in cargas:
        if c["temperatura"] is not None and c["temperatura"] > ALERTA_TEMP_CICLA_C:
            clave = f"cicla_temp:{c['turno']}:{c['carga']}"
            _avisar_si_nuevo(clave, f"🌡️ CICLA: carga {c['carga']} ({c['fecha']}) con temperatura "
                                    f"{c['temperatura']}°C (umbral {ALERTA_TEMP_CICLA_C}°C).")


def _revisar_laser_whatsapp():
    usuario, password = os.environ.get("LASER_USUARIO"), os.environ.get("LASER_PASSWORD")
    if not (usuario and password):
        return
    entregas = laserenisima.obtener_entregas(usuario, password)
    for e in entregas:
        if e["ufc"] is not None and e["ufc"] > ALERTA_UFC_LASER:
            clave = f"laser_ufc:{e['fecha_entrega']}"
            _avisar_si_nuevo(clave, f"🧪 La Serenísima: entrega del {e['fecha_entrega']} con U.F.C. "
                                    f"{round(e['ufc'])} (umbral {round(ALERTA_UFC_LASER)}).")


def _revisar_rutina_whatsapp(tambo: str):
    hoy = datetime.date.today().strftime("%Y-%m-%d")
    data, _fresh = _cache_get(_clave(tambo, f"rutina:{hoy}"), allow_stale=True)
    if data is None:
        _refresh_rutina_async(tambo, hoy)
        return
    grupos_data = _run_consulta("rutina_grupos", tambo, salas.de(tambo).sql_grupos())
    idx_grupo = grupos_data["columns"].index("grupo")
    grupos = [r[idx_grupo] for r in grupos_data["rows"]]
    resultado = salas.de(tambo).analizar_dia(tambo, data["columns"], data["rows"], hoy, grupos,
                                             max_sesiones=_max_sesiones(tambo),
                                             nombres=_nombres_grupos(tambo))
    for s in resultado["sesiones"]:
        if s["score"] < ALERTA_RUTINA_SCORE_MIN:
            clave = f"rutina_score:{hoy}:{s['indice']}"
            _avisar_si_nuevo(clave, f"⏱️ Rutina de ordeño: sesión de las {s['inicio'][11:16]} del {hoy} "
                                    f"con score {s['score']}% (umbral {ALERTA_RUTINA_SCORE_MIN}%).")


def _revisar_incidencias_whatsapp(tambo: str):
    data, _fresh = _cache_get(_clave(tambo, "ordeno_inc"), allow_stale=True)
    if not data or not data.get("rows"):
        return
    idx = {c: i for i, c in enumerate(data["columns"])}
    totales = [
        (r[idx["posicion"]], (r[idx["desliz"]] or 0) + (r[idx["patadas"]] or 0)
         + (r[idx["bloqueos"]] or 0) + (r[idx["recoloc"]] or 0))
        for r in data["rows"]
    ]
    if not totales:
        return
    mediana = statistics.median(t for _p, t in totales)
    umbral_rojo = max(round(mediana * 2.5), 4)
    hoy = datetime.date.today().isoformat()
    for puesto, total in totales:
        if total >= umbral_rojo:
            clave = f"incidencia:{hoy}:{puesto}"
            _avisar_si_nuevo(clave, f"🔧 Puesto {puesto}: {total} incidencias hoy (deslizamientos/patadas/"
                                    f"bloqueos/recolocaciones), muy por encima de la mediana ({mediana:.0f}). "
                                    "Posible unidad fallada.")


def _revisar_alertas_whatsapp():
    if not _canales_disponibles():
        return
    tambo = tambos.DEFAULT_TAMBO
    for fn, args in ((_revisar_cicla_whatsapp, (tambo,)), (_revisar_laser_whatsapp, ()),
                     (_revisar_rutina_whatsapp, (tambo,)), (_revisar_incidencias_whatsapp, (tambo,))):
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            pass


def _proximo_horario_alertas() -> datetime.datetime:
    """Próximo datetime en que toca revisar (hoy o mañana, el horario más cercano)."""
    ahora = datetime.datetime.now()
    candidatos = []
    for dias in (0, 1):
        base = (ahora + datetime.timedelta(days=dias)).replace(minute=0, second=0, microsecond=0)
        for hora in ALERTA_HORARIOS:
            candidato = base.replace(hour=hora)
            if candidato > ahora:
                candidatos.append(candidato)
    return min(candidatos)


def _bucle_alertas_whatsapp():
    while True:
        espera_s = (_proximo_horario_alertas() - datetime.datetime.now()).total_seconds()
        time.sleep(max(espera_s, 1))
        try:
            _revisar_alertas_whatsapp()
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_bucle_alertas_whatsapp, daemon=True).start()


@app.get("/api/alertas/canales")
@auth.requiere_rol("admin")
def api_alertas_canales():
    """Estado de cada canal de alerta: si tiene credenciales cargadas
    (configurado) y si está tildado por el usuario (activo)."""
    estado = config_alertas.estado()
    nombres = {"whatsapp": "WhatsApp (Twilio, de pago)", "telegram": "Telegram (gratis)",
               "correo": "Email (gratis)"}
    return jsonify({"canales": [
        {"id": cid, "nombre": nombres[cid], "configurado": mod.configurado(), "activo": estado[cid]}
        for cid, mod in _CANALES_MOD.items()
    ]})


@app.post("/api/alertas/canales")
@auth.requiere_rol("admin")
def api_alertas_canales_set():
    """Tilda/destilda un canal de alerta."""
    body = request.json or {}
    canal = body.get("canal", "")
    try:
        config_alertas.set_activo(canal, bool(body.get("activo")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.post("/api/alertas/probar")
@auth.requiere_rol("admin")
def api_alertas_probar():
    """Manda un mensaje de prueba por todos los canales activos y configurados,
    sin esperar al próximo ciclo de revisión."""
    try:
        _enviar_a_canales_activos("✅ Prueba de LactIA: si ves este mensaje, las alertas "
                                    "están funcionando.")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True})


# --- Alimentación: conciliación de lotes con grupos --------------------------
# El lado DelPro es barato (25 filas) pero el del proveedor implica un login
# contra un sitio externo, así que se cachea igual que CICLA.
CONCILIACION_CACHE_TTL_S = 600

# Ventana para decidir si un lote se usa. Un mes: alcanza para que un lote real
# aparezca aunque se descargue día por medio, y es corto como para que uno que
# se dejó de usar salga de la lista sin que haya que borrarlo en Haasten.
CONCILIACION_DIAS_USO = 30


def _conciliacion_estado(tambo: str, refrescar: bool = False) -> dict:
    """Los dos lados cruzados, más el estado del proveedor.

    Si el proveedor falla —falta configuración, se cayó el sitio— NO se corta:
    se devuelve igual el lado DelPro y el mapeo guardado, con el motivo en
    `proveedor.error`. La pantalla tiene que servir para mirar los grupos
    aunque el mixer esté incomunicado.
    """
    herd = rebano.por_defecto(tambo)
    key = f"{tambo}:conciliacion_grupos:{herd}"
    data, _ = _cache_get(key, allow_stale=True, ttl=CONCILIACION_CACHE_TTL_S)
    if data is None or refrescar:
        data = {
            "grupos": db.run_query(conciliacion.sql_grupos(herd), tambo=tambo, max_rows=500),
            "dias": db.run_query(conciliacion.sql_dias_animaldaily(herd), tambo=tambo, max_rows=60),
            "cambio": db.run_query(conciliacion.sql_ultimo_cambio_grupo(herd),
                                   tambo=tambo, max_rows=5),
        }
        _cache_set(key, data)

    prov = proveedores.de(tambo)
    key_prov = f"{tambo}:conciliacion_proveedor"
    lotes, kg_lote = [], None
    info = {"nombre": prov.NOMBRE, "error": None, "equipos": [],
            "dias_uso": CONCILIACION_DIAS_USO}
    guardado, _ = _cache_get(key_prov, allow_stale=True, ttl=CONCILIACION_CACHE_TTL_S)
    if guardado is not None and not refrescar:
        lotes, kg_lote, info = guardado["lotes"], guardado["kg_lote"], guardado["info"]
    else:
        try:
            lotes = prov.lotes()
            info["equipos"] = [{k: v for k, v in e.items() if not k.startswith("_")}
                               for e in prov.equipos()]
            # Qué lotes RECIBEN comida de verdad. Sin esto, los catorce lotes que
            # el tambo dejó configurados y no usa piden grupo y generan catorce
            # alertas falsas que tapan las verdaderas.
            hoy = datetime.date.today()
            kg_lote = conciliacion.kg_por_lote(
                prov.consumos(hoy - datetime.timedelta(days=CONCILIACION_DIAS_USO), hoy))
            _cache_set(key_prov, {"lotes": lotes, "kg_lote": kg_lote, "info": info})
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)

    grupos = conciliacion.grupos_de(data["grupos"])
    mapeo = conciliacion.mapeo_de(tambo)
    salida = conciliacion.analizar(grupos, lotes, mapeo, kg_lote)
    ultimo_cambio = (data["cambio"]["rows"] or [[None]])[0][0]
    salida.update({
        "tambo": tambo,
        "tambo_nombre": tambos.TAMBOS.get(tambo, {}).get("nombre", tambo),
        "mapeo": mapeo,
        "proveedor": info,
        "frescura": {
            "ultimo_cambio_grupo": ultimo_cambio,
            "animaldaily": conciliacion.ultimo_dia_completo(data["dias"]),
            "hoy": datetime.date.today().isoformat(),
        },
    })
    return salida


@app.get("/api/alimentacion/conciliacion")
@auth.requiere_rol("admin")
def api_alimentacion_conciliacion():
    """Lotes del proveedor de alimentación contra grupos de DelPro, con el mapeo
    guardado, las diferencias de cabezas y las sugerencias. Ver `conciliacion.py`."""
    tambo = _tambo_del_request()
    refrescar = request.args.get("refrescar") in ("1", "true", "si")
    try:
        return jsonify(_conciliacion_estado(tambo, refrescar))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.post("/api/alimentacion/conciliacion")
@auth.requiere_rol("admin")
def api_alimentacion_guardar_conciliacion():
    """Guarda el mapeo lote↔grupo del tambo y devuelve el estado recalculado.

    Body: {"lotes": [{"lote": str, "grupos": [oid], "nota": str}],
           "grupos_sin_alimentacion": [oid], "umbral_pct": n, "umbral_cabezas": n}
    """
    tambo = _tambo_del_request()
    try:
        conciliacion.guardar_mapeo(tambo, request.json or {},
                                   usuario=auth.usuario_actual(),
                                   ahora=datetime.datetime.now().isoformat(timespec="seconds"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        return jsonify(_conciliacion_estado(tambo))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


CONVERSION_CACHE_TTL_S = 1800


@app.get("/api/alimentacion/conversion")
@auth.requiere_rol("admin")
def api_alimentacion_conversion():
    """Eficiencia de conversión: kg de sólidos por kg de materia seca, por grupo
    y por vaca. Ver `alimentacion.py` — es una medida de GRUPO, la materia seca
    por vaca es un reparto del corral."""
    tambo = _tambo_del_request()
    herd = rebano.por_defecto(tambo)
    try:
        dias = min(int(request.args.get("dias") or alimentacion.DIAS_DEFECTO),
                   alimentacion.RANGO_MAX_DIAS)
    except ValueError:
        return jsonify({"error": "Cantidad de días inválida."}), 400

    mapeo = conciliacion.lote_de_grupo(tambo)
    if not mapeo:
        return jsonify({"error": "Todavía no hay ningún lote asignado a un grupo. "
                                 "Definí el mapeo en la pestaña «Conciliación de grupos» "
                                 "y volvé acá."}), 409

    key = f"{tambo}:alim_conversion:{herd}:{dias}"
    data, _ = _cache_get(key, allow_stale=True, ttl=CONVERSION_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            try:
                # El período termina en el último día COMPLETO de AnimalDaily, no
                # en hoy: los últimos días vienen a medio cargar y hundirían los
                # promedios (ver `conciliacion.ultimo_dia_completo`).
                d_dias = db.run_query(conciliacion.sql_dias_animaldaily(herd),
                                      tambo=tambo, max_rows=60)
                ultimo = conciliacion.ultimo_dia_completo(d_dias)
                hasta = (datetime.date.fromisoformat(ultimo["fecha"]) if ultimo["fecha"]
                         else datetime.date.today())
                desde = hasta - datetime.timedelta(days=dias - 1)
                consumos = proveedores.de(tambo).consumos(desde, hasta)
                ms, diag = alimentacion.ms_por_lote_dia(consumos)
                _cache_set(k, {
                    "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                    "ms": {f"{l}|{f.isoformat()}": v for (l, f), v in ms.items()},
                    "diagnostico": diag,
                    "prod_dia": db.run_query(
                        alimentacion.sql_produccion_grupo_dia(desde, hasta, herd),
                        tambo=tambo, max_rows=4000),
                    "prod_vaca": db.run_query(
                        alimentacion.sql_produccion_vaca(desde, hasta, herd),
                        tambo=tambo, max_rows=5000),
                    "solidos": db.run_query(
                        alimentacion.sql_solidos_vaca(desde, hasta, herd),
                        tambo=tambo, max_rows=5000),
                    "grupos": db.run_query(conciliacion.sql_grupos(herd),
                                           tambo=tambo, max_rows=500),
                })
            except Exception as exc:  # noqa: BLE001
                _cache_set(k, {"error": str(exc)})
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True,
                        "mensaje": "Calculando la conversión (leche, sólidos y "
                                   "materia seca de las últimas semanas)…"}), 202

    if data.get("error"):
        return jsonify({"error": data["error"]}), 502

    ms = {}
    for clave, v in data["ms"].items():
        lote, fecha = clave.rsplit("|", 1)
        ms[(lote, datetime.date.fromisoformat(fecha))] = v
    salida = alimentacion.analizar(
        data["prod_dia"], data["prod_vaca"], data["solidos"], ms,
        conciliacion.grupos_de(data["grupos"]), mapeo, data["diagnostico"])
    salida.update({"desde": data["desde"], "hasta": data["hasta"], "dias": dias})
    return jsonify(salida)


@app.get("/api/alimentacion/conversion-historica")
@auth.requiere_rol("admin")
def api_alimentacion_conversion_historica():
    """Cómo varió la conversión semana a semana, y los sólidos mes a mes.

    Ver `conversion_historica.py`. La pestaña hermana («Eficiencia de
    conversión») contesta cuánto convierte hoy cada rodeo; esta contesta si
    viene mejorando, que es lo que dice si un cambio de ración sirvió.
    """
    tambo = _tambo_del_request()
    herd = rebano.por_defecto(tambo)
    hoy = datetime.date.today()
    try:
        desde = datetime.date.fromisoformat(
            request.args.get("desde") or conversion_historica.INICIO_ALIMENTACION)
    except ValueError:
        return jsonify({"error": "Fecha de inicio inválida."}), 400
    # Antes de que arrancara el sistema de alimentación no hay con qué cruzar
    # la leche: pedir más atrás devolvería semanas vacías, no historia.
    desde = max(desde, datetime.date.fromisoformat(
        conversion_historica.INICIO_ALIMENTACION))
    desde = max(desde, hoy - datetime.timedelta(days=conversion_historica.RANGO_MAX_DIAS))

    mapeo = conciliacion.lote_de_grupo(tambo)
    if not mapeo:
        return jsonify({"error": "Todavía no hay ningún lote asignado a un grupo. "
                                 "Definí el mapeo en la pestaña «Conciliación de grupos» "
                                 "y volvé acá."}), 409

    key = f"{tambo}:alim_conv_hist:{herd}:{desde.isoformat()}"
    data, _ = _cache_get(key, allow_stale=True, ttl=CONVERSION_CACHE_TTL_S)
    if data is None:
        with _cache_lock:
            arrancar = key not in _refreshing
            if arrancar:
                _refreshing.add(key)

        def run(k=key):
            try:
                # Se corta en el último día COMPLETO de AnimalDaily. Los dos
                # sistemas no van al día parejo —DelPro queda unos cinco días
                # atrás y Haasten tiene lo de hoy—, así que la última semana
                # sale calculada contra dos días de leche y da una barra que no
                # significa nada.
                d_dias = db.run_query(conciliacion.sql_dias_animaldaily(herd),
                                      tambo=tambo, max_rows=60)
                ultimo = conciliacion.ultimo_dia_completo(d_dias)
                hasta = (datetime.date.fromisoformat(ultimo["fecha"]) if ultimo["fecha"]
                         else hoy)
                consumos = proveedores.de(tambo).consumos(desde, hasta)
                ms, _diag = alimentacion.ms_por_lote_dia(consumos)
                _cache_set(k, {
                    "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                    "ms": {f"{l}|{f.isoformat()}": v for (l, f), v in ms.items()},
                    "prod_dia": db.run_query(
                        alimentacion.sql_produccion_grupo_dia(desde, hasta, herd),
                        tambo=tambo, max_rows=20000),
                    "solidos": db.run_query(
                        conversion_historica.sql_solidos_por_control(desde, hasta, herd),
                        tambo=tambo, max_rows=200),
                    "ordene": db.run_query(
                        conversion_historica.sql_grupos_ordene(herd),
                        tambo=tambo, max_rows=200),
                    "lactancia": db.run_query(
                        conversion_historica.sql_produccion_por_lactancia(desde, hasta, herd),
                        tambo=tambo, max_rows=20000),
                })
            except Exception as exc:  # noqa: BLE001
                _cache_set(k, {"error": str(exc)})
            finally:
                with _cache_lock:
                    _refreshing.discard(k)

        if arrancar:
            threading.Thread(target=run, daemon=True).start()
        return jsonify({"calentando": True,
                        "mensaje": "Reconstruyendo la historia de conversión "
                                   "(leche diaria y descargas de mixer)…"}), 202

    if data.get("error"):
        return jsonify({"error": data["error"]}), 502

    ms = {}
    for clave, v in data["ms"].items():
        lote, fecha = clave.rsplit("|", 1)
        ms[(lote, datetime.date.fromisoformat(fecha))] = v
    salida = conversion_historica.armar(
        data["prod_dia"], ms, mapeo, data["solidos"],
        datetime.date.fromisoformat(data["hasta"]),
        [f[0] for f in data["ordene"]["rows"]])
    salida["por_lactancia"] = conversion_historica.lactancia(
        data["lactancia"], datetime.date.fromisoformat(data["hasta"]))
    salida.update({"desde": data["desde"], "hasta": data["hasta"]})
    return jsonify(salida)


def _refresh_sala_async(tambo: str, consulta_id: str, vivo: bool):
    """Recalcula la sala convencional en segundo plano. No usa el registro
    `_SQL` porque la consulta depende de tambo/ventana configurada, no es un
    texto fijo como el resto de las consultas cacheadas."""
    key = _clave(tambo, consulta_id)
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            sql = (sala_convencional.sql_sala_vivo(tambo) if vivo
                   else sala_convencional.sql_sala_sesion())
            _cache_set(key, db.run_query(sql, tambo=tambo, max_rows=3000))
        except Exception as exc:  # noqa: BLE001
            _cache_set(key, {"error": str(exc)})
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _refresh_sala_inc_async(tambo: str):
    key = _clave(tambo, "sala_inc")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            _cache_set(key, db.run_query(
                sala_convencional.sql_sala_incidencias(), tambo=tambo, max_rows=500))
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _no_es_sala_convencional(tambo: str):
    """None si el tambo es de sala convencional; si no, la respuesta de error
    lista para devolver. Este módulo entero (`/api/sala*`) solo tiene sentido
    para ese tipo de sala — evita disparar la consulta (SessionMilkYieldEx no
    existe en una rotativa) y de paso devuelve un mensaje legible en vez de la
    excepción cruda de ODBC. Puede pasar si el selector de tambo cambia a uno
    rotativo mientras esta página sigue actualizándose sola de fondo."""
    if tambos.tipo_sala(tambo) == "convencional":
        return None
    nombre = tambos.TAMBOS.get(tambo, {}).get("nombre", tambo)
    return jsonify({"error": f"«Ordeño en Vivo Sala CMS» es solo para salas convencionales — "
                             f"{nombre} es una sala rotativa. Usá «Ordeño en vivo» en su lugar.",
                    "no_aplica": True}), 409


@app.get("/api/sala/config")
def api_sala_config():
    """Configuración vigente de la sala convencional (lados, puestos por lado,
    ventana en vivo). Ver `sala_convencional.py`."""
    tambo = _tambo_del_request()
    error = _no_es_sala_convencional(tambo)
    if error:
        return error
    return jsonify(sala_convencional.configuracion(tambo))


@app.post("/api/sala/config")
@auth.requiere_rol("admin")
def api_sala_guardar_config():
    tambo = _tambo_del_request()
    error = _no_es_sala_convencional(tambo)
    if error:
        return error
    try:
        cfg = sala_convencional.guardar_configuracion(tambo, request.json or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # La ventana "en vivo" está adentro de la consulta cacheada: si cambió,
    # los cachés viejos quedan con datos de una ventana que ya no es la
    # vigente y hay que tirarlos, no esperar a que venzan solos.
    with _cache_lock:
        for k in [k for k in _cache if k.startswith(f"{tambo}:sala_")]:
            _cache.pop(k, None)
    return jsonify(cfg)


@app.get("/api/sala")
def api_sala():
    """Vacas de la sala convencional (espina de pescado). modo=vivo → última
    visita por (lado, puesto) dentro de la ventana configurada; si no, la
    sesión completa (último ordeño). Ver `sala_convencional.py`."""
    tambo = _tambo_del_request()
    error = _no_es_sala_convencional(tambo)
    if error:
        return error
    vivo = request.args.get("modo") == "vivo"
    cfg = sala_convencional.configuracion(tambo)
    # La clave incluye la ventana vigente: si el tambo la cambia, la consulta
    # vieja queda en otra clave y no se sirve por error (misma lección que
    # documenta PRECALENTAR.md sobre parámetros y claves de caché).
    consulta_id = f"sala_vivo:{cfg['ventana_vivo_min']}" if vivo else "sala_sesion"
    key = _clave(tambo, consulta_id)

    if vivo:
        data, fresh = _cache_get(key, allow_stale=True, ttl=_VIVO_TTL_S)
        if data is None:
            _refresh_sala_async(tambo, consulta_id, vivo=True)
            return jsonify({"calentando": True, "mensaje": "Cargando la sala en vivo…"}), 202
        if not fresh:
            _refresh_sala_async(tambo, consulta_id, vivo=True)
    else:
        data, _ = _cache_get(key, allow_stale=True)
        if data is None:
            _refresh_sala_async(tambo, consulta_id, vivo=False)
            return jsonify({"calentando": True, "mensaje": "Cargando datos de la sala…"}), 202

    if data.get("error"):
        return jsonify({"error": data["error"]}), 502

    columns = list(data["columns"])
    rows = [list(r) for r in data["rows"]]
    momento = rows[0][0] if rows else None
    if columns and columns[0] == "momento_ordeno":
        columns = columns[1:]
        rows = [r[1:] for r in rows]

    # Incidencias del equipo por puesto: consulta aparte, TTL largo (cambian
    # de a poco), se pegan por (lado, puesto).
    inc_data, _ = _cache_get(_clave(tambo, "sala_inc"), allow_stale=True)
    if inc_data is None:
        _refresh_sala_inc_async(tambo)
    mapa_inc = {}
    if inc_data:
        di = {c: i for i, c in enumerate(inc_data["columns"])}
        for r in inc_data["rows"]:
            mapa_inc[(r[di["lado"]], r[di["puesto"]])] = [r[di[c]] for c in sala_convencional.INC_COLS]
    il, ip = columns.index("lado"), columns.index("puesto")
    columns = columns + sala_convencional.INC_COLS
    defecto_inc = [0] * len(sala_convencional.INC_COLS)
    rows = [r + (mapa_inc.get((r[il], r[ip])) or defecto_inc) for r in rows]

    en_vivo, hace, ordenando = False, "", False
    if momento:
        try:
            t = datetime.datetime.strptime(momento, "%Y-%m-%d %H:%M:%S")
            minutos = int((datetime.datetime.now() - t).total_seconds() // 60)
            en_vivo = minutos <= 20
            ordenando = minutos <= sala_convencional.VIVO_LIMITE_MIN
            hace = _hace_texto(minutos)
        except (ValueError, TypeError):
            pass

    return jsonify({
        "modo": "vivo" if vivo else "sesion",
        "momento": momento, "en_vivo": en_vivo, "hace": hace, "ordenando": ordenando,
        "vacas": len(rows), "columns": columns, "rows": rows,
        "config": cfg,
        "truncated": data.get("truncated", False),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5310, debug=False)
