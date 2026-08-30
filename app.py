# -*- coding: utf-8 -*-
"""Analítica DelPro — aplicación web para consultar la base DDM (SQL Server),
hacer preguntas en lenguaje natural y generar gráficas y reportes."""
import json
import math
import statistics
import threading
import time

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

import datetime

import os

import agente
import ai
import alimentacion
import auth
import bitacora
import checklist
import cicla
import conciliacion
import cruce_sensehub
import conversion_historica
import config_alertas
import configuracion_tambo
import correo
import db
import clima
import ficha_animal
import flujos
import genetica
import herencia
import gestacion
import iot_canales
import iot_conexion
import iot_monitoreo
import lavado_programa
import laserenisima
import mantenimiento
import merito
import partos_secados
import performance
import podal
import precios_alimentos
import preneces
import parametros
import proveedores
import proyeccion
import rebano
import rentabilidad
import tasa_prenez
import reproduccion
import resumen
import rutina
import sala_convencional
import salas
import salud
import sensehub
import simulador
import tablero
import tambos
import telegram_bot
import whatsapp
import whatsapp_ia
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
_RUTAS_PUBLICAS = {"/login", "/webhook/whatsapp", "/api/iot/pantalla", "/api/iot/pantalla/historico",
                    "/api/iot/pantalla/io", "/api/iot/pantalla/actuador", "/api/iot/pantalla/lavado",
                    "/api/iot/pantalla/lavado/iniciar", "/api/iot/pantalla/lavado/cancelar"}


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
# "salud_rcs_grupo"/"salud_rcs_vacas"/"salud_conductividad"/
# "salud_produccion_rodeo"/"salud_atencion"/"salud_bcs_vacas" TAMPOCO se
# registran acá (mismo motivo que "rutina_grupos"): necesitan el filtro de
# "grupos de ordeñe reales" de `salas.de(tambo).sql_grupos()`, que varía por
# tambo — ver salud.sql_*(grupos_sql) y _refresh_salud_async más abajo.
# "salud_atencion_v2" tampoco se registra acá por el mismo motivo desde que
# salud.sql_atencion_v2() pasó a tomar el filtro de grupos por tambo (y, para
# rotativa/convencional, si incluye o no las señales de CMSMilkYield) — ver
# api_salud_atencion_v2 más abajo.
# "herencia_madres" NO se registra acá: necesita un tope de filas más alto que
# el genérico (hay más madres que 5.000) y se cachea aparte — ver
# `_historia_madres` más abajo.
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

# Último `db.TablaNoDisponibleError` de cada clave (se limpia si una consulta
# posterior tiene éxito). A diferencia de un error de conexión o de RAM, "esta
# tabla no existe en esta base" es un hecho ESTRUCTURAL de la instalación (la
# misma DDM de DelPro con hardware/sala distinto — cámara BCS, controlador de
# rotativa, etc.) que no se arregla reintentando: sin esto, el llamador se
# queda para siempre en "calentando" sin forma de saber que en realidad nunca
# va a resolver. Vive en memoria (como `_cache`/`_refreshing`): un reinicio
# del proceso vuelve a intentar todo de cero.
_errores_tabla: dict[str, str] = {}

# Si esta base tiene `BcsDailyData` (cámara BCS, add-on de hardware — ver
# salud.sql_bcs_vacas/sql_atencion_v2). Mismo criterio que `_errores_tabla`:
# es un hecho estructural de la instalación, no cambia mientras el proceso
# está corriendo, así que se consulta una sola vez (OBJECT_ID es metadata,
# instantáneo) y se cachea para siempre.
_tiene_bcs: dict[str, bool] = {}
_tiene_bcs_lock = threading.Lock()


def _tiene_bcs_de(tambo: str) -> bool:
    with _tiene_bcs_lock:
        if tambo in _tiene_bcs:
            return _tiene_bcs[tambo]
    try:
        data = db.run_query("SELECT OBJECT_ID('BcsDailyData') AS oid", tambo=tambo)
        existe = data["rows"][0][0] is not None
    except Exception:  # noqa: BLE001
        existe = False  # ante la duda, se arma la consulta sin BCS: degrada, no rompe
    with _tiene_bcs_lock:
        _tiene_bcs[tambo] = existe
    return existe


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
            resultado = db.run_query(sql if sql is not None else _SQL[consulta_id], tambo=tambo)
        except db.TablaNoDisponibleError as exc:
            with _cache_lock:
                _errores_tabla[key] = str(exc)
            return
        except Exception:  # noqa: BLE001
            return
        finally:
            with _cache_lock:
                _refreshing.discard(key)
        _cache_set(key, resultado)
        with _cache_lock:
            _errores_tabla.pop(key, None)

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


def _refresh_identificacion_async(tambo: str, desde: str, hasta: str):
    """% real de identificación por día (`sql_identificacion`), en segundo
    plano. Hace falta en las dos salas: "Rutina de ordeño" calcula "Vacas
    identificadas" contando `rp == 0` sobre las visitas de `sql_rutina`, y esa
    consulta descarta en silencio los ordeños sin identificar en las dos
    (la convencional por una causa nunca confirmada en producción; la
    rotativa porque `sql_rutina` todavía filtra `IDTime IS NOT NULL`) — acá,
    con el mismo criterio Number=0 pero sin ese descarte, sale el número
    real. Ver `rutina._analizar_sesion` (`identificacion_pct`)."""
    key = _clave(tambo, f"identificacion:{desde}:{hasta}")
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def worker():
        try:
            sala = salas.de(tambo)
            data = db.run_query(sala.sql_identificacion(desde, hasta), tambo=tambo)
            por_dia = {d["fecha"]: d["pct_identificacion"] for d in sala.armar_identificacion(
                data["columns"], data["rows"])}
            _cache_set(key, por_dia)
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _cache_lock:
                _refreshing.discard(key)

    threading.Thread(target=worker, daemon=True).start()


def _identificacion_pct_de(tambo: str, desde: str, hasta: str, fecha: str) -> float | None:
    """`por_dia.get(fecha)` del caché de arriba, disparando el refresco si
    hace falta. None si todavía no está lista — en ese caso el llamador sigue
    usando el conteo por sesión de siempre.

    Aplica a las DOS salas: la rotativa tiene el MISMO problema que la
    convencional, solo que en otra consulta — `rutina.sql_rutina` todavía
    filtra `IDTime IS NOT NULL` (ver su docstring, y el de `sql_rendimiento`
    que sí se corrigió), así que el conteo por sesión de "Vacas identificadas"
    daba siempre 100% ahí también. Medido en La Ponderosa: la tarjeta
    "Identificación de ordeños" (`sql_identificacion`, sin ese filtro) daba
    97,65% el mismo día que "Rutina de ordeño" mostraba 100%."""
    key = _clave(tambo, f"identificacion:{desde}:{hasta}")
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        _refresh_identificacion_async(tambo, desde, hasta)
        return None
    if not fresh:
        _refresh_identificacion_async(tambo, desde, hasta)
    return data.get(fecha)


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
        # Solo se mira el body si REALMENTE es JSON. `request.json` a secas
        # levanta 415 con cualquier otro content-type, y hay POST que no son
        # JSON: la carga del check-list viaja como multipart porque lleva las
        # fotos adentro (ver api_checklist_guardar). Se sigue usando `.json`
        # y no `get_json(silent=True)` para que un JSON MAL FORMADO siga
        # fallando fuerte en vez de caer callado al tambo por defecto — que es
        # el bug que explica esta función.
        if request.is_json:
            tambo = (request.json or {}).get("tambo", "")
        else:
            tambo = request.form.get("tambo", "")
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
    lista = tambos.lista()
    for t in lista:
        try:
            t["puestos"] = salas.de(t["id"]).cantidad_puestos(t["id"])
        except Exception:  # noqa: BLE001
            t["puestos"] = None
        _cfg = configuracion_tambo.config_de(t["id"])
        t["personas"] = _cfg.get("personas")
        t["arreo_min"] = _cfg.get("arreo_min")
        # "Vacas en ordeñe" para indicadores de dotación (vacas por puesto,
        # vacas por persona) -- se lee del caché de KPIs del dashboard, ya
        # calculado y refrescado en segundo plano; si todavía no está tibio
        # (recién arrancó el proceso) queda None y el frontend no muestra el
        # indicador en vez de disparar una consulta nueva acá.
        kpis, _ = _cache_get(_clave(t["id"], "__kpis__"), allow_stale=True)
        t["vacas_en_ordeno"] = (kpis or {}).get("vacas_en_ordeno")
    return jsonify({"tambos": lista, "default": tambos.DEFAULT_TAMBO})


# --- Página "⚙ Configuración": conexión, hardware y proveedores por tambo ---
CATALOGOS_CONFIGURACION = {
    "sistema_actividad": [{"id": i, "label": configuracion_tambo.SISTEMAS_ACTIVIDAD_LABEL[i]}
                          for i in configuracion_tambo.SISTEMAS_ACTIVIDAD],
    "caudalimetro": [{"id": i, "label": configuracion_tambo.CAUDALIMETROS_LABEL[i]}
                     for i in configuracion_tambo.CAUDALIMETROS],
    "usina_lactea": [{"id": i, "label": configuracion_tambo.USINAS_LACTEAS_LABEL[i]}
                     for i in configuracion_tambo.USINAS_LACTEAS],
    "sistema_alimentacion": [{"id": i, "label": configuracion_tambo.SISTEMAS_ALIMENTACION_LABEL[i]}
                             for i in configuracion_tambo.SISTEMAS_ALIMENTACION],
    "sala": [{"id": i, "label": configuracion_tambo.SALAS_LABEL[i]} for i in configuracion_tambo.SALAS],
}


RENT_CACHE_TTL_S = 6 * 3600
MERITO_CACHE_TTL_S = 6 * 3600


def _merito_ctx(tambo: str, herd=None):
    """Contexto del rodeo para el índice de mérito, cacheado.

    El índice es RELATIVO al rodeo, así que para puntuar un animal hay que
    conocer la distribución de los 5.578. Se calcula una vez cada 6 horas y
    sirve para todas las fichas: hacerlo por ficha sería traer el rodeo entero
    en cada búsqueda de RP.

    Devuelve None si la consulta se truncó: con el rodeo incompleto los
    percentiles saldrían mal y un índice mal calibrado es peor que ninguno.
    """
    clave = _clave(tambo, "__merito_ctx__")
    data, _ = _cache_get(clave, allow_stale=True, ttl=MERITO_CACHE_TTL_S)
    if data is None:
        vida = db.run_query(merito.sql_vida(herd), tambo=tambo, max_rows=20000)
        prod = db.run_query(merito.sql_produccion(herd), tambo=tambo, max_rows=20000)
        if vida.get("truncated") or prod.get("truncated"):
            return None
        data = {"vida": vida, "prod": prod}
        _cache_set(clave, data)
    return merito.preparar(data["vida"], data["prod"])


def _costo_mixer(tambo: str, desde, hasta):
    """({(lote, fecha): $}, precio_litro, resumen_precios), cacheado.

    Se cachea aparte de las pantallas de alimentación porque la ficha de un
    animal se abre de a una y no puede pagar el costo de bajar cuatro meses de
    consumos del mixer cada vez. La clave incluye el rango: dos fichas seguidas
    del mismo tambo comparten el trabajo.
    """
    clave = _clave(tambo, f"__costo_mixer__{desde}_{hasta}")
    data, _ = _cache_get(clave, allow_stale=True, ttl=RENT_CACHE_TTL_S)
    if data is None:
        consumos = proveedores.de(tambo).consumos(desde, hasta)
        pr = precios_alimentos.leer(configuracion_tambo.ruta_precios(tambo))
        costo = {}
        if pr.get("precios"):
            costo, _diag = alimentacion.costo_por_lote_dia(consumos, pr["precios"])
        data = {
            "costo": {f"{l}|{f.isoformat()}": v for (l, f), v in costo.items()},
            "precio_litro": pr.get("precio_litro"),
            "precios": precios_alimentos.resumen(
                configuracion_tambo.ruta_precios(tambo)),
        }
        _cache_set(clave, data)
    salida = {}
    for k, v in (data.get("costo") or {}).items():
        lote, fecha = k.rsplit("|", 1)
        salida[(lote, datetime.date.fromisoformat(fecha))] = v
    return salida, data.get("precio_litro"), data.get("precios") or {}


def _rentabilidad_animal(tambo: str, rp: int, herd=None, info: dict = None) -> dict:
    """Rentabilidad de un animal para la ficha. Ver `rentabilidad.py`."""
    hoy = datetime.date.today()
    # El costo de alimentación no existe antes de que arrancara el mixer: pedir
    # más atrás devolvería semanas de ingreso sin costo, o sea margen inventado.
    desde = max(datetime.date.fromisoformat(conversion_historica.INICIO_ALIMENTACION),
                hoy - datetime.timedelta(days=rentabilidad.RANGO_MAX_DIAS))
    # Se corta en el último día COMPLETO de AnimalDaily, igual que las pantallas
    # de alimentación: los dos sistemas no van al día parejo.
    try:
        d_dias = db.run_query(conciliacion.sql_dias_animaldaily(herd),
                              tambo=tambo, max_rows=60)
        ultimo = conciliacion.ultimo_dia_completo(d_dias)
        hasta = (datetime.date.fromisoformat(ultimo["fecha"]) if ultimo["fecha"] else hoy)
    except Exception:  # noqa: BLE001
        hasta = hoy

    dias = db.run_query(rentabilidad.sql_dias(rp, desde, hasta, herd),
                        tambo=tambo, max_rows=500)
    grupos = sorted({int(f[dias["columns"].index("grupo_oid")])
                     for f in dias["rows"]
                     if f[dias["columns"].index("grupo_oid")] is not None})
    vacas = ({"columns": ["grupo_oid", "fecha", "vacas"], "rows": []} if not grupos
             else db.run_query(rentabilidad.sql_vacas_grupo_dia(desde, hasta, grupos, herd),
                                tambo=tambo, max_rows=20000))
    costo, precio_litro, res_precios = _costo_mixer(tambo, desde, hasta)
    salida = rentabilidad.armar(dias, vacas, costo,
                                 conciliacion.lote_de_grupo(tambo),
                                 precio_litro, info=info)
    salida.update({"desde": desde.isoformat(), "hasta": hasta.isoformat(),
                   "precios": res_precios})
    return salida


TABLERO_CACHE_TTL_S = 30 * 60
# Cuánto se recuerda que la base no responde antes de volver a probar. Con la
# base caída cada intento de conexión cuesta ~11 segundos de timeout, así que sin
# esto el tablero pagaba un timeout POR INDICADOR: se midió en 43 segundos por
# request, y con el frontend reintentando cada 8 segundos las peticiones se
# apilaban y colgaban la pantalla. Con esto se paga un timeout cada minuto y el
# resto de las llamadas responden al instante con la última lectura buena.
BASE_CAIDA_TTL_S = 60


def _base_responde(tambo: str):
    """None si la base responde; el motivo si no.

    Se prueba UNA vez y se recuerda el resultado un minuto. La pregunta no es
    «¿esta consulta anda?» sino «¿tiene sentido intentar consultar?», y con el
    servidor apagado la respuesta es la misma para todas.
    """
    k = _clave(tambo, "__base_caida__")
    estado, _ = _cache_get(k, allow_stale=False, ttl=BASE_CAIDA_TTL_S)
    if estado is not None:
        return estado.get("motivo")
    try:
        db.run_query("SELECT 1 AS ok", tambo=tambo, max_rows=1, validate=False)
        _cache_set(k, {"motivo": None})
        return None
    except Exception as exc:  # noqa: BLE001
        s = str(exc)
        # El 08001 es "no se pudo abrir la conexión": el servidor no está o no
        # es alcanzable por red. Se distingue de un error de consulta porque lo
        # que hay que hacer al respecto es distinto.
        motivo = ("No hay conexión con el servidor de la base (SERVER-DELPRO). "
                  "El tablero muestra la última lectura de cada indicador."
                  if "08001" in s or "SQLDriverConnect" in s
                  else f"La base no respondió: {s[:120]}")
        _cache_set(k, {"motivo": motivo})
        return motivo


_calentando_tablero = set()


def _ultimo_dia_datos(tambo: str, herd=None) -> datetime.date:
    """Último día COMPLETO de `AnimalDaily`, cacheado.

    El tablero tiene que anclar sus ventanas acá y NO en `date.today()`. La base
    viene atrasada varios días (medido el 30/07/2026: el último día completo era
    el 21/07), así que «la última semana» contada desde hoy caía entera en el
    hueco y las tarjetas de Rendimiento Sala salían vacías con la base andando
    perfecto. Es el mismo criterio que ya usan `salud.py` y las pantallas de
    alimentación — ver el `_ANCLA` de salud.py.
    """
    k = _clave(tambo, "__ultimo_dia_datos__")
    d, _ = _cache_get(k, allow_stale=True, ttl=TABLERO_CACHE_TTL_S)
    if d and d.get("fecha"):
        return datetime.date.fromisoformat(d["fecha"])
    hoy = datetime.date.today()
    try:
        data = db.run_query(conciliacion.sql_dias_animaldaily(herd), tambo=tambo, max_rows=60)
        ultimo = conciliacion.ultimo_dia_completo(data)
        fecha = (datetime.date.fromisoformat(ultimo["fecha"]) if ultimo["fecha"] else hoy)
    except Exception:  # noqa: BLE001
        fecha = hoy
    _cache_set(k, {"fecha": fecha.isoformat()})
    return fecha


def _calentar_tablero(tambo: str, faltan: set):
    """Dispara los cálculos que le faltan al tablero, EN SERIE y en segundo plano.

    Sin esto el tablero solo LEE cachés: si nadie abría cada pantalla, las
    tarjetas se quedaban en «calculando» para siempre y el reintento del
    frontend giraba en falso. La pantalla prometía completarse sola y no podía.

    EN SERIE Y NO EN PARALELO, por el mismo motivo que `_warmup`: esta base
    corre con poca memoria y nueve consultas pesadas a la vez la tumban. Cada
    `_refresh_*` además ya se protege de dispararse dos veces (`_refreshing`),
    así que llamar de más no duplica trabajo.

    Solo se calienta lo que de verdad falta: si el tambo ya abrió Alimentación,
    ese cálculo no se rehace.
    """
    with _cache_lock:
        if tambo in _calentando_tablero:
            return
        _calentando_tablero.add(tambo)

    def worker():
        try:
            hoy = datetime.date.today()
            herd = rebano.por_defecto(tambo)

            # 1. KPIs primero: de ahí sale la fecha del último día con datos, que
            #    es lo que necesita la rutina para saber qué día calcular.
            if {"rutina_score"} & faltan:
                try:
                    _calcular_kpis(tambo)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
                    if kpis and kpis.get("fecha_dato"):
                        _refresh_rutina_async(tambo, str(kpis["fecha_dato"])[:10])
                except Exception:  # noqa: BLE001
                    pass

            if {"rcs_altas", "rcs_cronicas"} & faltan:
                try:
                    _refresh_async(tambo, "salud_rcs_grupo",
                                    salud.sql_rcs_por_grupo(salas.de(tambo).sql_grupos()))
                except Exception:  # noqa: BLE001
                    pass

            if {"horas_ordeno", "horas_ordeno_total", "pct_identificacion", "ordenos_hora",
                "litros_hora", "vacas_puesto", "vacas_persona"} & faltan:
                try:
                    ancla = _ultimo_dia_datos(tambo, herd)
                    _refresh_rendimiento_async(tambo, ancla - datetime.timedelta(days=6), ancla)
                except Exception:  # noqa: BLE001
                    pass

            if {"ufc"} & faltan:
                try:
                    _refresh_laser_async()
                except Exception:  # noqa: BLE001
                    pass

            # 2. Alimentación al final: es la más pesada (baja los consumos del
            #    mixer por HTTP además de consultar la base).
            if {"costo_litro", "litros_libres_pct", "conversion"} & faltan:
                try:
                    _calentar_alimentacion(tambo, herd)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            with _cache_lock:
                _calentando_tablero.discard(tambo)

    threading.Thread(target=worker, daemon=True).start()


def _calentar_alimentacion(tambo: str, herd):
    """Llena el caché de /api/alimentacion/conversion con sus días por defecto.

    Se arma con las MISMAS piezas y la MISMA clave que el endpoint, para que la
    pantalla de Alimentación encuentre el trabajo ya hecho en vez de rehacerlo.
    """
    key = f"{tambo}:alim_conversion:{herd}:{alimentacion.DIAS_DEFECTO}"
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)
    try:
        if not conciliacion.lote_de_grupo(tambo):
            return                     # sin mapeo de lotes no hay nada que calcular
        d_dias = db.run_query(conciliacion.sql_dias_animaldaily(herd), tambo=tambo, max_rows=60)
        ultimo = conciliacion.ultimo_dia_completo(d_dias)
        hasta = (datetime.date.fromisoformat(ultimo["fecha"]) if ultimo["fecha"]
                 else datetime.date.today())
        desde = hasta - datetime.timedelta(days=alimentacion.DIAS_DEFECTO - 1)
        consumos = proveedores.de(tambo).consumos(desde, hasta)
        ms, diag = alimentacion.ms_por_lote_dia(consumos)
        costo, diag_costo, precio_litro = {}, {}, None
        try:
            pr = precios_alimentos.leer(configuracion_tambo.ruta_precios(tambo))
            if pr.get("precios"):
                costo, diag_costo = alimentacion.costo_por_lote_dia(consumos, pr["precios"])
            precio_litro = pr.get("precio_litro")
            diag_costo["precios"] = precios_alimentos.resumen(
                configuracion_tambo.ruta_precios(tambo))
        except Exception as exc:  # noqa: BLE001
            diag_costo = {"precios": {"error": str(exc)}}
        _cache_set(key, {
            "desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "ms": {f"{l}|{f.isoformat()}": v for (l, f), v in ms.items()},
            "costo": {f"{l}|{f.isoformat()}": v for (l, f), v in costo.items()},
            "precio_litro": precio_litro,
            "diagnostico": {**diag, **diag_costo},
            "prod_dia": db.run_query(alimentacion.sql_produccion_grupo_dia(desde, hasta, herd),
                                     tambo=tambo, max_rows=4000),
            "prod_vaca": db.run_query(alimentacion.sql_produccion_vaca(desde, hasta, herd),
                                      tambo=tambo, max_rows=5000),
            "solidos": db.run_query(alimentacion.sql_solidos_vaca(desde, hasta, herd),
                                    tambo=tambo, max_rows=5000),
            "grupos": db.run_query(conciliacion.sql_grupos(salas.grupos_subquery(tambo), herd),
                                   tambo=tambo, max_rows=500),
        })
    except Exception as exc:  # noqa: BLE001
        _cache_set(key, {"error": str(exc)})
    finally:
        with _cache_lock:
            _refreshing.discard(key)


def _valores_tablero(tambo: str) -> dict:
    """{clave: {"valor"|"falta"|"calculando"}} para cada indicador del tablero.

    CADA INDICADOR SALE DE LA MISMA FUENTE QUE SU PANTALLA. Donde la pantalla ya
    deja un caché, se lee ese caché: así el tablero no puede mostrar un número
    distinto del que muestra el detalle, que es la peor falla posible acá. Donde
    no hay un caché con clave estable, se calcula con el MISMO rango por defecto
    que usa la pantalla y se cachea aparte.

    NINGÚN INDICADOR PUEDE TIRAR ABAJO EL RESTO: cada uno va en su propio
    try/except y, si falla, la tarjeta explica qué falta en vez de mostrar un
    cero — que en un semáforo se pintaría de un color y se leería como medido.
    """
    herd = rebano.por_defecto(tambo)
    out = {}
    # Si la base no responde, NINGUNA consulta va a andar: se saltean todas en
    # vez de pagar 11 segundos de timeout por cada una. Los indicadores que
    # salen de un caché ya calentado se sirven igual — no necesitan la base.
    caida = _base_responde(tambo)

    def poner(clave, valor=None, falta=None, calculando=False, detalle=None):
        out[clave] = {"valor": valor, "falta": falta,
                      "calculando": calculando, "detalle": detalle}

    # --- Alimentación: costo por litro, litros libres y conversión ----------
    # Misma clave de caché que /api/alimentacion/conversion con sus días por
    # defecto, que es como abre la pantalla.
    try:
        k = f"{tambo}:alim_conversion:{herd}:{alimentacion.DIAS_DEFECTO}"
        data, _ = _cache_get(k, allow_stale=True, ttl=CONVERSION_CACHE_TTL_S)
        if data is None:
            for c in ("costo_litro", "litros_libres_pct", "conversion"):
                poner(c, calculando=True,
                      falta="La pantalla de Alimentación todavía no se calculó.")
        elif data.get("error"):
            for c in ("costo_litro", "litros_libres_pct", "conversion"):
                poner(c, falta=data["error"])
        else:
            mapeo = conciliacion.lote_de_grupo(tambo)
            sal = alimentacion.analizar(
                data["prod_dia"], data["prod_vaca"], data["solidos"],
                _tablero_por_lote(data.get("ms")),
                conciliacion.grupos_de(data["grupos"]), mapeo, data.get("diagnostico"),
                costo_lote_dia=_tablero_por_lote(data.get("costo")),
                precio_litro=data.get("precio_litro"))
            ec, res = sal.get("economia") or {}, sal.get("resumen") or {}
            poner("costo_litro", ec.get("costo_por_litro"),
                  falta=ec.get("falta") or "Falta la planilla de precios.",
                  detalle=f"{ec.get('vacas') or 0} vacas valorizadas")
            poner("litros_libres_pct", ec.get("pct_litros_libres"),
                  falta=ec.get("falta") or "Falta la planilla de precios.",
                  detalle=(f"{ec.get('litros_libres')} de "
                           f"{ec.get('kg_leche_vaca_dia')} litros"
                           if ec.get("litros_libres") is not None else None))
            poner("conversion", res.get("conversion_tambo"),
                  falta="Sin grupos con materia seca y sólidos en el período.",
                  detalle=f"{res.get('grupos_confiables') or 0} grupo(s) confiables")
    except Exception as exc:  # noqa: BLE001
        for c in ("costo_litro", "litros_libres_pct", "conversion"):
            poner(c, falta=f"Error al leer Alimentación: {exc}")

    # --- Rendimiento de sala: horas de ordeño e identificación --------------
    # Mismo rango por defecto que /api/rutina/rendimiento (la última semana).
    try:
        # La MISMA ventana que usa `_calentar_tablero`, anclada en el último día
        # con datos: si difirieran, el lector miraría un caché que nadie llena.
        ancla = _ultimo_dia_datos(tambo, herd)
        k = (f"{tambo}:rendimiento:{(ancla - datetime.timedelta(days=6)).isoformat()}"
             f":{ancla.isoformat()}")
        data, _ = _cache_get(k, allow_stale=True)
        if data is None:
            for c in ("horas_ordeno", "horas_ordeno_total", "pct_identificacion"):
                poner(c, calculando=True,
                      falta="La pantalla de Rendimiento Sala todavía no se calculó.")
        else:
            # El caché guarda las CONSULTAS CRUDAS ({"visitas", "ident"}), no el
            # análisis: lo arma el endpoint al servir. Así que acá hay que
            # analizarlas igual que él, o el tablero lee campos que no existen.
            ident = (data or {}).get("ident")
            id_an = (salas.de(tambo).armar_identificacion(ident["columns"], ident["rows"])
                     if ident else None)
            # `armar_identificacion` devuelve una fila por día: el indicador es
            # el del último día con datos, no un promedio de la semana.
            ultimo_id = (sorted(id_an, key=lambda x: x.get("fecha") or "")[-1]
                         if id_an else None)
            poner("pct_identificacion", (ultimo_id or {}).get("pct_identificacion"),
                  falta="Sin ordeños en la semana previa al último día con datos.",
                  detalle=(f"{ultimo_id.get('desconocidos')} sin identificar el "
                           f"{ultimo_id.get('fecha')}" if ultimo_id else None))

            vis = (data or {}).get("visitas")
            sesiones = []
            if vis:
                sesiones = salas.de(tambo).analizar_rendimiento(
                    tambo, vis["columns"], vis["rows"],
                    (ancla - datetime.timedelta(days=6)).isoformat(), ancla.isoformat(),
                    max_sesiones=_max_sesiones(tambo),
                    nombres=_nombres_grupos(tambo),
                    grupos_ordene=_grupos_ordene(tambo)) or []

            sin_sesiones = "Sin sesiones en la semana previa al último día con datos."
            claves_rend = ("horas_ordeno", "horas_ordeno_total", "ordenos_hora",
                           "litros_hora", "vacas_puesto", "vacas_persona")
            if not sesiones:
                for c in claves_rend:
                    poner(c, falta=sin_sesiones)
            else:
                # Ordeños/hora y litros/hora: promedio SIMPLE entre sesiones (no
                # ponderado), igual que la tarjeta de Rendimiento Sala.
                oh = [s["ordenios_por_hora"] for s in sesiones if s.get("ordenios_por_hora") is not None]
                kh = [s["kg_por_hora"] for s in sesiones if s.get("kg_por_hora") is not None]
                poner("ordenos_hora", round(sum(oh) / len(oh), 1) if oh else None,
                      falta=sin_sesiones, detalle=f"{len(oh)} sesión(es)" if oh else None)
                poner("litros_hora", round(sum(kh) / len(kh), 1) if kh else None,
                      falta=sin_sesiones, detalle=f"{len(kh)} sesión(es)" if kh else None)

                # Vacas por puesto / por persona: dotación del tambo. Mismas
                # constantes que usa la propia pantalla (puestos de la sala,
                # personas de ⚙ Configuración) contra las vacas DISTINTAS
                # ordeñadas por día, promediadas en el período.
                cfg = configuracion_tambo.config_de(tambo)
                try:
                    puestos = salas.de(tambo).cantidad_puestos(tambo)
                except Exception:  # noqa: BLE001
                    puestos = None
                personas = cfg.get("personas")
                vacas_por_dia = {s["fecha"]: s["vacas_dia"] for s in sesiones
                                 if s.get("fecha") and s.get("vacas_dia") is not None}
                vacas_dia_prom = (round(sum(vacas_por_dia.values()) / len(vacas_por_dia))
                                  if vacas_por_dia else None)
                falta_dot = ("Faltan los puestos o las personas del tambo en "
                            "⚙ Configuración." if vacas_dia_prom is not None else sin_sesiones)
                poner("vacas_puesto",
                      (round(vacas_dia_prom / puestos, 1)
                       if vacas_dia_prom and puestos else None),
                      falta=falta_dot,
                      detalle=(f"{vacas_dia_prom} vacas/día · {puestos} puestos"
                               if vacas_dia_prom and puestos else None))
                poner("vacas_persona",
                      (round(vacas_dia_prom / personas)
                       if vacas_dia_prom and personas else None),
                      falta=falta_dot,
                      detalle=(f"{vacas_dia_prom} vacas/día · {personas} persona(s)"
                               if vacas_dia_prom and personas else None))

                # Horas/día en ordeño: EXACTAMENTE la misma cuenta que la
                # tarjeta de Rendimiento Sala (ver templates/index.html), no la
                # duración de la sesión. El rodeo llega completo al corral de
                # espera y entra de a una: la primera vaca casi no espera y la
                # última espera toda la ventana, así que la permanencia medida
                # se reparte por la mitad. El arreo (⚙ Configuración, no está
                # en DDM) lo vive cada vaca entero, y se suma una vez por
                # sesión. Por día se SUMAN las sesiones del mismo grupo (son
                # ordeños distintos); por grupo y por período se PROMEDIA.
                #
                # Antes esta tarjeta sumaba `duracion_min` de la última sesión
                # —el tiempo que la SALA estuvo funcionando, no el tiempo que
                # cada VACA pasa fuera del corral— y dos métricas distintas
                # quedaban con el mismo nombre y números que no coincidían con
                # esta misma pantalla.
                arreo_min = cfg.get("arreo_min") or 0
                perm_por_dia_grupo = {}
                for s in sesiones:
                    dia = perm_por_dia_grupo.setdefault(s.get("fecha"), {})
                    for g in (s.get("grupos") or []):
                        nombre_g = g.get("grupo")
                        perm = g.get("permanencia_min")
                        if nombre_g is None or perm is None:
                            continue
                        dia[nombre_g] = dia.get(nombre_g, 0.0) + (arreo_min + perm / 2) / 60
                grupos_perm = sorted({g for dia in perm_por_dia_grupo.values() for g in dia})
                perm_prom_por_grupo = {}
                for g in grupos_perm:
                    vals = [dia[g] for dia in perm_por_dia_grupo.values() if g in dia]
                    if vals:
                        perm_prom_por_grupo[g] = sum(vals) / len(vals)
                horas = (round(sum(perm_prom_por_grupo.values()) / len(perm_prom_por_grupo), 1)
                         if perm_prom_por_grupo else None)
                nota_arreo = "" if arreo_min else " · sin arreo configurado"
                poner("horas_ordeno", horas,
                      falta=sin_sesiones,
                      detalle=(f"promedio de {len(perm_prom_por_grupo)} rodeo(s)" + nota_arreo
                               if horas is not None else None))

                # TOTAL de la sala: la SUMA de todos los rodeos, que es otra
                # pregunta que el promedio de arriba —ese dice cuánto le lleva a
                # UNA vaca, este cuánto le consume al TAMBO—. Se suma por día y
                # recién después se promedian los días: sumar los promedios por
                # rodeo daría distinto cuando algún rodeo no ordeña todos los
                # días, y este es EXACTAMENTE el orden que usa la tarjeta de
                # Rendimiento Sala (ver permSumaPorDia en templates/index.html).
                # Que las dos pantallas muestren el mismo número no es un
                # detalle: es la regla del tablero.
                sumas_dia = [sum(dia.values()) for dia in perm_por_dia_grupo.values() if dia]
                horas_total = (round(sum(sumas_dia) / len(sumas_dia), 1) if sumas_dia else None)
                poner("horas_ordeno_total", horas_total,
                      falta=sin_sesiones,
                      detalle=(f"suma de {len(perm_prom_por_grupo)} rodeo(s)" + nota_arreo
                               if horas_total is not None else None))
    except Exception as exc:  # noqa: BLE001
        for c in ("horas_ordeno", "horas_ordeno_total", "pct_identificacion",
                  "ordenos_hora", "litros_hora", "vacas_puesto", "vacas_persona"):
            poner(c, falta=f"Error al leer Rendimiento Sala: {exc}")

    # --- RCS: vacas altas y crónicas ---------------------------------------
    try:
        data, _ = _cache_get(_clave(tambo, "salud_rcs_grupo"), allow_stale=True)
        if data is None:
            for c in ("rcs_altas", "rcs_cronicas"):
                poner(c, calculando=True,
                      falta="La pantalla de Salud del rodeo todavía no se calculó.")
        else:
            idx = {c: i for i, c in enumerate(data["columns"])}
            altas = sum((f[idx["altas_ultimo"]] or 0) for f in data["rows"]
                        if "altas_ultimo" in idx)
            cron = sum((f[idx["cronicas"]] or 0) for f in data["rows"]
                       if "cronicas" in idx)
            poner("rcs_altas", altas,
                  detalle=f"sobre {salud.UMBRAL_RCS:,} cél/ml".replace(",", "."))
            poner("rcs_cronicas", cron, detalle="altas en dos controles seguidos")
    except Exception as exc:  # noqa: BLE001
        for c in ("rcs_altas", "rcs_cronicas"):
            poner(c, falta=f"Error al leer RCS: {exc}")

    # --- UFC: de la usina, NO de DelPro ------------------------------------
    try:
        data, _ = _cache_get("laser:actual", allow_stale=True, ttl=LASER_CACHE_TTL_S)
        if data is None:
            poner("ufc", calculando=True,
                  falta="Todavía no se consultaron las entregas de la usina.")
        else:
            entregas = (data or {}).get("entregas") or []

            # LA ÚLTIMA ENTREGA CON RESULTADO, no la mediana de la ventana. Se
            # probó primero con la mediana (evita que un solo pico como 1.093
            # arrastre el número, que era el problema con el promedio), pero
            # para UFC lo que importa operativamente es el dato MÁS RECIENTE:
            # es lo que dispara una acción hoy, no el historial. La contraparte
            # es que un solo pico puede pintar la tarjeta en rojo por un día —
            # se acepta a propósito, es la lectura que se pidió.
            #
            # `fecha_entrega` es texto "DD/MM/AAAA HH:MM" (no ISO): no se puede
            # ordenar como string (p.ej. "2/7/2026" > "10/7/2026" en texto), hay
            # que parsearlo. Las entregas más recientes suelen llegar sin UFC
            # todavía (el laboratorio tarda más que la carga del remito), así
            # que se descartan las sin resultado en vez de reportar None como
            # si fuera el dato de hoy.
            def _fecha_entrega(e):
                try:
                    return datetime.datetime.strptime(e["fecha_entrega"], "%d/%m/%Y %H:%M")
                except (KeyError, TypeError, ValueError):
                    return None

            con_ufc = [e for e in entregas if e.get("ufc") is not None and _fecha_entrega(e)]
            con_ufc.sort(key=_fecha_entrega, reverse=True)
            ultima = con_ufc[0] if con_ufc else None
            poner("ufc", ultima.get("ufc") if ultima else None,
                  falta="Las entregas cargadas no traen UFC.",
                  detalle=(f"entrega del {ultima['fecha_entrega']}" if ultima else None))
    except Exception as exc:  # noqa: BLE001
        poner("ufc", falta=f"Error al leer las entregas: {exc}")

    # --- IoT: sensores fuera de rango o sin reportar ------------------------
    # `estado_sistema` consulta la base para saber si se está ordeñando, así que
    # con el servidor caído también se cuelga en el timeout.
    try:
        if caida:
            raise RuntimeError(caida)
        est = iot_monitoreo.estado_sistema(tambo) or {}
        sensores = est.get("sensores") or est.get("canales") or []
        if not sensores:
            poner("iot_alarmas", falta="No hay gateway IoT conectado en este tambo.")
        else:
            alarmas = sum(1 for s in sensores
                          if s.get("alarma") or s.get("estado") in ("alarma", "sin_datos"))
            poner("iot_alarmas", alarmas, detalle=f"de {len(sensores)} sensor(es)")
    except Exception as exc:  # noqa: BLE001
        poner("iot_alarmas", falta=f"Error al leer el monitoreo IoT: {exc}")

    # --- Rutina y días abiertos: con caché propio del tablero --------------
    # No tienen una clave de caché estable (la de rutina depende de la fecha y
    # la de reproducción de dos rangos), así que se calculan con el mismo rango
    # por defecto de su pantalla y se guardan aparte. El TTL largo evita que
    # abrir el tablero dispare trabajo pesado.
    # Rutina: la fecha sale de `__kpis__` y el resultado del caché `rutina:<fecha>`,
    # que son exactamente los que usa /api/rutina. Así el tablero muestra la
    # misma nota que la pantalla, sin recalcularla.
    try:
        kpis, _ = _cache_get(_clave(tambo, "__kpis__"), allow_stale=True)
        fecha = str(kpis["fecha_dato"])[:10] if kpis and kpis.get("fecha_dato") else None
        if not fecha:
            poner("rutina_score", calculando=True,
                  falta="Todavía no se sabe cuál fue el último día con datos.")
        else:
            data, _ = _cache_get(_clave(tambo, f"rutina:{fecha}"), allow_stale=True)
            if data is None:
                poner("rutina_score", calculando=True,
                      falta=f"La rutina del {fecha} todavía no se calculó.")
            else:
                # Mismo caso que rendimiento: el caché tiene la consulta cruda.
                an = salas.de(tambo).analizar_dia(
                    tambo, data["columns"], data["rows"], fecha,
                    nombres=_nombres_grupos(tambo),
                    identificacion_pct=_identificacion_pct_de(tambo, fecha, fecha, fecha)) or {}
                # `analizar_dia` NO devuelve un resumen: devuelve una sesión por
                # ordeño, cada una con su score y sus vacas. La nota del día es
                # el promedio PONDERADO POR VACAS —igual que en `rutina.py`—, no
                # el promedio simple: una sesión de 40 vacas no puede pesar lo
                # mismo que una de 400.
                ses = [x for x in (an.get("sesiones") or [])
                       if x.get("score") is not None and x.get("vacas")]
                vacas = sum(x["vacas"] for x in ses)
                score = (round(sum(x["score"] * x["vacas"] for x in ses) / vacas)
                         if vacas else None)
                poner("rutina_score", score,
                      # El motivo real no es "no hay sesiones": las hay, lo que
                      # falta es con qué calificarlas. En una sala que no
                      # registra colocación ni tandas contiguas quedan afuera
                      # tantos componentes que el score no se publica (ver
                      # PESO_MINIMO_SCORE en rutina.py).
                      falta=(f"Las {len(an.get('sesiones') or [])} sesión(es) del {fecha} no se "
                             f"pueden calificar: esta sala no registra lo suficiente. Ver el "
                             f"detalle en Rutina de ordeño."),
                      detalle=(f"rutina del {fecha} · {len(ses)} sesión(es), "
                               f"{vacas} vacas" if score is not None else None))
    except Exception as exc:  # noqa: BLE001
        poner("rutina_score", falta=f"Error al leer la rutina: {exc}")

    # Días abiertos: no hay un caché de pantalla con clave estable (la de
    # reproducción depende de dos rangos), así que se calcula con su propio
    # caché de media hora. Es una sola consulta agregada, barata.
    try:
        k = _clave(tambo, "__tablero_abiertos__")
        extra, _ = _cache_get(k, allow_stale=True, ttl=TABLERO_CACHE_TTL_S)
        if extra is None:
            if caida:
                raise RuntimeError(caida)
            extra = _tablero_dias_abiertos(tambo, herd)
            _cache_set(k, extra)
        poner("dias_abiertos", extra.get("dias_abiertos"),
              falta=extra.get("falta") or "Sin lactancias cerradas para promediar.",
              detalle=extra.get("detalle"))
    except Exception as exc:  # noqa: BLE001
        poner("dias_abiertos", falta=f"Error al calcular días abiertos: {exc}")

    # Mortandad de terneros: mismo patrón que días abiertos (sin caché de
    # pantalla propio, se calcula aparte con su propio caché de media hora).
    try:
        k = _clave(tambo, "__tablero_mortandad__")
        extra, _ = _cache_get(k, allow_stale=True, ttl=TABLERO_CACHE_TTL_S)
        if extra is None:
            if caida:
                raise RuntimeError(caida)
            extra = _tablero_mortandad_terneros(tambo, herd)
            _cache_set(k, extra)
        poner("mortandad_terneros", extra.get("mortandad"),
              falta=extra.get("falta") or "Sin datos suficientes.",
              detalle=extra.get("detalle"))
    except Exception as exc:  # noqa: BLE001
        poner("mortandad_terneros", falta=f"Error al calcular: {exc}")

    return out


def _tablero_por_lote(d):
    """El caché guarda {(lote, fecha)} como 'lote|fecha'; acá se deshace."""
    out = {}
    for clave, v in (d or {}).items():
        lote, fecha = clave.rsplit("|", 1)
        out[(lote, datetime.date.fromisoformat(fecha))] = v
    return out


MORTANDAD_VENTANA_MESES = 6
MORTANDAD_RIESGO_DIAS = 90


def _tablero_mortandad_terneros(tambo: str, herd) -> dict:
    """Bajas tempranas de terneros: salidas con menos de 90 días de vida sobre
    los partos del mismo período.

    ESTE TAMBO NO REGISTRA UN MOTIVO DE «MUERTE» PARA TERNEROS. Medido el
    30/07/2026: el código `ExitReason = 50` ("Death") existe en la base y tiene
    95 casos, pero NINGUNO es de La Ponderosa — son de los otros dos tambos que
    comparten la instancia. Las 22 salidas de terneros de los últimos meses de
    este tambo están TODAS bajo el motivo genérico "OTRAS CAUSAS". Por eso se
    cuenta cualquier salida temprana, no solo la marcada como muerte: puede
    incluir traslados o ventas de terneros, además de mortandad real. Es lo más
    cerca que se puede llegar con lo que carga hoy el tambo.

    VENTANA CENSURADA: un ternero nacido en los últimos 90 días todavía no
    completó su ventana de riesgo — no se sabe si va a tener una salida
    temprana o no—, así que se EXCLUYE del cálculo en vez de contarlo como
    "sobrevivió". Es la misma trampa que ya está documentada para la tasa de
    concepción en CLAUDE.md (servicios recientes sin resultado todavía).
    """
    hasta = _ultimo_dia_datos(tambo, herd) - datetime.timedelta(days=MORTANDAD_RIESGO_DIAS)
    desde = hasta - datetime.timedelta(days=30 * MORTANDAD_VENTANA_MESES)
    # Las mismas dos consultas que usa la tabla de "Análisis Reproductivo" (ver
    # reproduccion.sql_bajas_terneros): una sola fuente de verdad para el
    # criterio de "salida temprana", en vez de mantenerlo escrito dos veces.
    dp = db.run_query(reproduccion.sql_partos_periodo(str(desde), str(hasta), herd),
                      tambo=tambo, max_rows=5)
    db_ = db.run_query(reproduccion.sql_bajas_terneros(str(desde), str(hasta),
                                                       MORTANDAD_RIESGO_DIAS, herd),
                       tambo=tambo, max_rows=500)
    if not dp["rows"]:
        return {"falta": "No se pudo consultar partos ni salidas tempranas."}
    partos, bajas = int(dp["rows"][0][0] or 0), len(db_["rows"])
    if not partos:
        return {"falta": f"Sin partos registrados entre {desde} y {hasta}."}
    return {"mortandad": round(100 * bajas / partos, 1),
            "detalle": f"{bajas} de {partos} partos · {desde} a {hasta}"}


def _tablero_dias_abiertos(tambo: str, herd) -> dict:
    """Días abiertos promedio: días entre el parto y la concepción.

    `HistoryAnimalLactationInfo.OpenDays` es la fuente que ya usa
    `reproduccion.py`. Se acota a 0 < OpenDays < 400 con el mismo criterio: un 0
    o un NULL es "todavía no concibió", no cero días, y meterlo al promedio lo
    hunde; arriba de 400 son cargas viejas o erróneas.
    """
    d = db.run_query(f"""
        SELECT CAST(AVG(CAST(h.OpenDays AS float)) AS decimal(6,1)) AS dias,
               COUNT(*) AS lactancias
        FROM HistoryAnimalLactationInfo h
        JOIN BasicAnimal b ON b.OID = h.Animal
        WHERE h.OpenDays > 0 AND h.OpenDays < 400
          AND {rebano.filtro('b', herd)}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """, tambo=tambo, max_rows=5)
    if not d["rows"] or d["rows"][0][0] is None:
        return {"falta": "No hay lactancias con días abiertos cargados."}
    return {"dias_abiertos": float(d["rows"][0][0]),
            "detalle": f"{d['rows'][0][1]} lactancias"}


@app.get("/api/tablero")
@auth.requiere_rol("admin")
def api_tablero():
    """Tablero de Diagnóstico: los indicadores del tambo con su semáforo."""
    tambo = _tambo_del_request()
    try:
        valores = _valores_tablero(tambo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    # Cada valor que se pudo leer queda guardado con su fecha y hora: es lo que
    # permite que el tablero siga sirviendo cuando el servidor no está.
    try:
        tablero.guardar_lecturas(tambo, valores)
    except Exception:  # noqa: BLE001
        pass            # no poder guardar el histórico no puede romper la respuesta

    caida = _base_responde(tambo)
    # Lo que falta se manda a calcular en segundo plano. Sin esto el tablero
    # solo leía cachés y las tarjetas se quedaban en «calculando» hasta que
    # alguien abriera cada pantalla a mano. Con la base caída no se dispara
    # nada: no hay con qué calcular y solo se acumularían timeouts.
    faltan = {c for c, v in valores.items()
              if v.get("valor") is None and v.get("calculando")}
    if faltan and not caida:
        try:
            _calentar_tablero(tambo, faltan)
        except Exception:  # noqa: BLE001
            pass

    return jsonify(tablero.armar(valores, tablero.config_de(tambo),
                                  lecturas=tablero.lecturas_de(tambo),
                                  base_caida=caida))


@app.get("/api/tablero/config")
@auth.requiere_rol("admin")
def api_tablero_config():
    tambo = _tambo_del_request()
    return jsonify({"config": tablero.config_de(tambo),
                    "catalogo": tablero.catalogo()})


@app.post("/api/tablero/config")
@auth.requiere_rol("admin")
def api_tablero_guardar():
    tambo = _tambo_del_request()
    datos = request.json or {}
    try:
        if datos.get("restablecer"):
            cfg = tablero.restablecer(tambo, datos.get("clave"))
        else:
            cfg = tablero.guardar(tambo, datos.get("umbrales") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"config": cfg, "catalogo": tablero.catalogo()})


@app.post("/api/tablero/config/probar_resumen")
@auth.requiere_rol("admin")
def api_tablero_probar_resumen():
    """Manda el resumen del Tablero (los indicadores tildados en "📲 Resumen")
    ya mismo, por los canales activos — sin esperar al horario configurado
    (⚙ Configuración › 🔔 Alertas). Mismo criterio que /api/alertas/probar."""
    tambo = _tambo_del_request()
    valores = _valores_tablero(tambo)
    armado = tablero.armar(valores, tablero.config_de(tambo), lecturas=tablero.lecturas_de(tambo))
    texto = tablero.texto_resumen(armado, nombre_tambo=tambos.nombre_de(tambo))
    if not texto:
        return jsonify({"error": "No tildaste ningún indicador en la columna \"📲 Resumen\"."}), 400
    html = tablero.html_resumen(armado, nombre_tambo=tambos.nombre_de(tambo))
    try:
        _enviar_resumen_a_canales_activos(texto, html)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True})


def _estado_archivos(tambo: str) -> dict:
    """Qué archivos Excel se están usando de verdad, y en qué estado.

    Va junto con la configuración porque es la única forma de que el tambo vea
    si la ruta que escribió sirvió: una ruta con un error de tipeo cae en
    silencio a los archivos por defecto (ver `configuracion_tambo._resolver`), y
    sin mostrarlo uno cree que está leyendo la carpeta nueva. También trae los
    avisos de datos ESTIMADOS/SIMULADOS: son la advertencia de que hay números
    inventados en juego.
    """
    salida = {"archivos": configuracion_tambo.estado_rutas(tambo)}
    try:
        salida["genetica"] = genetica.resumen(configuracion_tambo.rutas_toros(tambo))
    except Exception as exc:  # noqa: BLE001
        salida["genetica"] = {"error": str(exc)}
    try:
        salida["precios"] = precios_alimentos.resumen(
            configuracion_tambo.ruta_precios(tambo))
    except Exception as exc:  # noqa: BLE001
        salida["precios"] = {"error": str(exc)}
    return salida


@app.get("/api/configuracion")
@auth.requiere_rol("admin")
def api_configuracion():
    """Config editable del tambo (conexión, hardware, proveedores) + los
    catálogos de opciones para los selectores. La contraseña NUNCA se
    devuelve: solo si hay una guardada (`contrasena_configurada`), para no
    reexponerla en cada carga de página — se pisa escribiendo una nueva."""
    tambo = _tambo_del_request()
    cfg = configuracion_tambo.config_de(tambo)
    cfg["contrasena_configurada"] = bool(cfg.pop("contrasena"))
    cfg["tambo"] = tambo
    cfg["sala_efectiva"] = tambos.tipo_sala(tambo)
    return jsonify({"config": cfg, "catalogos": CATALOGOS_CONFIGURACION,
                    **_estado_archivos(tambo)})


@app.post("/api/configuracion")
@auth.requiere_rol("admin")
def api_guardar_configuracion():
    tambo = _tambo_del_request()
    datos = dict(request.json or {})
    # Campo de UI, no de configuracion_tambo.guardar(): si el usuario no tocó
    # el campo contraseña (lo dejó en blanco a propósito, ver el frontend),
    # no hay que pisar la que ya estaba guardada con un vacío.
    if not datos.get("contrasena"):
        datos.pop("contrasena", None)
    try:
        cfg = configuracion_tambo.guardar(tambo, datos)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    cfg["contrasena_configurada"] = bool(cfg.pop("contrasena"))
    cfg["tambo"] = tambo
    cfg["sala_efectiva"] = tambos.tipo_sala(tambo)
    return jsonify({"config": cfg, "catalogos": CATALOGOS_CONFIGURACION,
                    **_estado_archivos(tambo)})


# --- Check-list de control (la mini app del celular) ------------------------
# Es una PANTALLA APARTE, no una sección de index.html: la usa el operario en
# la sala, con el celular, y no tiene por qué ver el resto de la analítica.
# Vive en la misma app Flask a propósito — un servicio separado duplicaría
# login, usuarios, deploy, túnel y backup, y los datos tienen que volver acá
# igual para cruzarlos con el ordeñe.

@app.get("/checklist/")
def checklist_pagina():
    return render_template("checklist.html", usuario=auth.usuario_actual(),
                           rol=auth.rol_actual(), tambo=_tambo_del_request())


@app.get("/checklist/manifest.webmanifest")
def checklist_manifest():
    """Lo que hace que se instale en el celular como una app (ícono propio,
    pantalla completa, sin barra del navegador)."""
    return jsonify({
        "name": "Check-list del tambo", "short_name": "Check-list",
        "start_url": "/checklist/", "scope": "/checklist/",
        "display": "standalone", "background_color": "#0b1016", "theme_color": "#0b1016",
        # Se usa el SVG que ya está en el repo. Android prefiere un PNG de 192 y
        # otro de 512 para el ícono del lanzador: si el tambo quiere el logo
        # propio en el escritorio del celular, se agregan esos dos archivos y se
        # suman acá, sin tocar nada más.
        "icons": [{"src": "/static/img/logo.svg", "sizes": "any", "type": "image/svg+xml",
                   "purpose": "any"}],
    })


@app.get("/checklist/sw.js")
def checklist_sw():
    """Service worker con scope /checklist/ — NO en la raíz. Con scope "/"
    también interceptaría la app principal, y un caché viejo ahí se ve como
    "el deploy no subió"."""
    js = """
const CACHE = 'checklist-v1';
const SHELL = ['/checklist/', '/checklist/manifest.webmanifest'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  // Solo el armazón se sirve del caché. Los POST y la plantilla NO: una
  // respuesta vieja de la plantilla haría cargar un check-list que ya cambió.
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (!url.pathname.startsWith('/checklist/') || url.pathname.includes('/api/')) return;
  e.respondWith(
    fetch(e.request).then(r => {
      const copia = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, copia));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
"""
    return Response(js, mimetype="application/javascript")


@app.get("/api/checklist/plantilla")
def api_checklist_plantilla():
    """Los items que toca cargar en ese momento, más lo que ya se cargó hoy."""
    tambo = _tambo_del_request()
    momento = request.args.get("momento", "sesion")
    fecha = request.args.get("fecha") or datetime.date.today().isoformat()
    try:
        datos = checklist.items_para(tambo, momento)
        datos["hechas_hoy"] = checklist.hechas_hoy(tambo, fecha)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    datos["fecha"] = fecha
    return jsonify(datos)


@app.post("/api/checklist/corrida")
def api_checklist_guardar():
    """Guarda una carga del celular. Llega como multipart en UNA sola pieza
    —el JSON en el campo `datos` y las fotos en `foto_<item_id>`— y no en dos
    pasos: del otro lado hay una cola que reintenta cuando vuelve la señal, y
    una carga que entró a medias es peor que una que no entró."""
    tambo = _tambo_del_request()
    try:
        datos = json.loads(request.form.get("datos") or "{}")
    except ValueError:
        return jsonify({"error": "El cuerpo de la carga no es JSON válido"}), 400

    fotos: dict = {}
    try:
        for campo, archivo in request.files.items(multi=True):
            if not campo.startswith("foto_"):
                continue
            try:
                item_id = int(campo[len("foto_"):])
            except ValueError:
                return jsonify({"error": f"Campo de foto inválido: {campo}"}), 400
            contenido = archivo.read()
            ext = checklist.validar_foto(archivo.filename or campo, archivo.mimetype, contenido)
            fotos.setdefault(item_id, []).append((checklist.nombre_de_foto(ext), contenido))

        res = checklist.guardar_corrida(
            tambo=tambo, momento=datos.get("momento"), sesion=datos.get("sesion"),
            usuario=auth.usuario_actual(), fecha=datos.get("fecha"),
            respuestas=datos.get("respuestas") or [], offline_id=datos.get("offline_id"),
            fotos=fotos)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


# --- Cruce SenseHub (collares Allflex) x DelPro -----------------------------

SQL_PADRON_SENSEHUB = """
    SELECT b.Number AS rp, g.Name AS grupo, b.OID AS oid
    FROM BasicAnimal b
    LEFT JOIN AnimalGroup ag ON ag.OID = b.[Group]
    LEFT JOIN AbstractGroup g ON g.OID = ag.OID
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL
      AND {filtro}
"""


@app.get("/api/sensehub/cruce")
@auth.requiere_rol("admin")
def api_sensehub_cruce():
    """Concilia el padrón de los collares con el de DelPro y cruza qué vaca
    marca cada sistema.

    Se resuelve en DOS PASOS INDEPENDIENTES a propósito. La conciliación de
    identidad solo necesita DDM y el padrón de SenseHub, así que se sirve
    aunque el índice de salud todavía se esté calculando o el controlador no
    conteste. Si se pidiera todo junto, un problema de red dejaría la pantalla
    en blanco cuando lo más útil —cuántas vacas emparejan— ya se podía mostrar.
    """
    tambo = _tambo_del_request()
    cfg = configuracion_tambo.config_de(tambo)
    ip = cfg.get("sensehub_ip")
    if not ip:
        return jsonify({"error": "Este tambo no tiene configurada la IP del controlador "
                                 "SenseHub (⚙ Configuración)."}), 400

    salida = {"tambo": tambo, "ip": ip,
              "usuario_env": sensehub.variable_usuario(tambo),
              "password_env": sensehub.variable_password(tambo)}

    # 1) Padrón de DelPro. El filtro por rebaño es OBLIGATORIO cuando la base
    #    la comparten varios tambos (la de La Ponderosa tiene tres): sin él
    #    entran vacas de otro establecimiento y el cruce "mejora" con animales
    #    que no son de acá.
    #
    #    Pero ese filtro deduce el rebaño desde `CMSGroupMilkSetting`, que es
    #    una tabla de la ROTATIVA y no existe en una instalación Alpro — es el
    #    caso de La Martina, donde la consulta filtrada muere con "Invalid
    #    object name". Así que: si la base tiene UN SOLO rebaño no hace falta
    #    filtrar y se sigue sin él; si tiene varios, no se puede seguir.
    try:
        h = db.run_query("SELECT COUNT(*) AS n FROM Herd", tambo=tambo, max_rows=5)
        n_rebanos = h["rows"][0][0] if h["rows"] else 1
    except Exception:  # noqa: BLE001
        n_rebanos = None
    salida["rebanos_en_la_base"] = n_rebanos

    data = None
    try:
        herd = rebano.por_defecto(tambo)
        data = db.run_query(SQL_PADRON_SENSEHUB.format(filtro=rebano.filtro("b", herd)),
                            tambo=tambo, max_rows=20000)
    except Exception as exc:  # noqa: BLE001
        if n_rebanos == 1:
            salida["sin_filtro_de_rebano"] = ("La base tiene un solo rebaño, así que se leyó "
                                              "el padrón completo sin filtrar.")
            try:
                data = db.run_query(SQL_PADRON_SENSEHUB.format(filtro="1 = 1"),
                                    tambo=tambo, max_rows=20000)
            except Exception as exc2:  # noqa: BLE001
                return jsonify({**salida,
                                "error": f"No se pudo leer el padrón de DelPro: {exc2}"}), 502
        else:
            return jsonify({**salida, "error":
                            f"No se pudo determinar el rebaño de este tambo y la base tiene "
                            f"{n_rebanos} rebaños: sin filtrar entrarían vacas de otro "
                            f"establecimiento. Detalle: {exc}"}), 502
    i = {c: k for k, c in enumerate(data["columns"])}
    padron_dp = [{"rp": r[i["rp"]], "grupo": r[i["grupo"]]} for r in data["rows"]]
    salida["animales_delpro"] = len(padron_dp)

    # 2) SenseHub. Todo lo que dependa del controlador va con su propio
    #    try/except: que no conteste no puede tumbar la pantalla entera.
    ctrl = sensehub.Controlador(ip, tambo)
    try:
        ctrl.login()
        padron_sh = ctrl.animales()
        salida["animales_sensehub"] = len(padron_sh)
    except sensehub.SenseHubError as e:
        return jsonify({**salida, "error_sensehub": str(e),
                        "ayuda": ("El padrón de collares no se pudo leer, así que no hay "
                                  "nada que conciliar. Revisá la IP, que el equipo esté "
                                  "encendido y las variables de entorno con el usuario y "
                                  "la contraseña.")})

    conc = cruce_sensehub.conciliar(padron_sh, padron_dp)
    salida["conciliacion"] = {k: v for k, v in conc.items() if k != "emparejadas"}
    salida["emparejadas"] = len(conc["emparejadas"])

    # 3) Marcas de salud de cada sistema. Las dos por separado y opcionales.
    marcadas = {}
    try:
        marcadas = cruce_sensehub.marcadas_por_sensehub(
            ctrl.exportar_salud(), [a for a in ctrl.alertas() if a.get("es_salud")])
        salida["marcadas_sensehub"] = len(marcadas)
    except sensehub.SenseHubError as e:
        salida["error_marcas"] = str(e)

    fichas = []
    con_alarmas = tambos.tipo_sala(tambo) == "rotativa"
    sql_v2 = salud.sql_atencion_v2(salas.de(tambo).sql_grupos(), con_alarmas)
    datos_v2, _ = _cache_get(_clave(tambo, "salud_atencion_v2"), allow_stale=True)
    if datos_v2 is None:
        _refresh_async(tambo, "salud_atencion_v2", sql_v2)
        salida["salud_calculando"] = True
    else:
        try:
            fichas = salud.calcular_atencion_v2(datos_v2["columns"], datos_v2["rows"],
                                                top=None) or []
        except Exception as exc:  # noqa: BLE001
            salida["error_salud"] = str(exc)

    if marcadas or fichas:
        cruce = cruce_sensehub.cruzar_salud(conc, marcadas, fichas)
        salida["cruce"] = {k: v for k, v in cruce.items() if k != "filas"}
        # Solo las filas que dicen algo: las que marca alguno de los dos.
        salida["filas"] = [f for f in cruce["filas"]
                           if f["sensehub_marca"] or f["lactia_marca"]]
        salida["resumen"] = cruce_sensehub.resumen(conc, cruce)
    else:
        salida["resumen"] = cruce_sensehub.resumen(conc)
    return jsonify(salida)


@app.get("/api/checklist/plantilla_completa")
@auth.requiere_rol("admin")
def api_checklist_plantilla_completa():
    """La plantilla ENTERA (los tres momentos juntos) más el historial de
    versiones, para el editor de ⚙ Configuración. La otra ruta, la que usa el
    celular, devuelve solo los items del momento que toca."""
    tambo = _tambo_del_request()
    return jsonify({**checklist.plantilla_vigente(tambo),
                    "versiones": checklist.versiones(tambo),
                    "momentos": list(checklist.MOMENTOS)})


@app.post("/api/checklist/plantilla")
@auth.requiere_rol("admin")
def api_checklist_guardar_plantilla():
    """Guarda la plantilla editada. Siempre crea una VERSIÓN NUEVA: la anterior
    queda intacta y las corridas viejas siguen colgando de ella (ver
    checklist.guardar_plantilla)."""
    tambo = _tambo_del_request()
    datos = request.json or {}
    try:
        pl = checklist.guardar_plantilla(tambo, datos.get("items") or [], auth.usuario_actual())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({**pl, "versiones": checklist.versiones(tambo),
                    "momentos": list(checklist.MOMENTOS)})


@app.get("/api/checklist/estadisticas")
@auth.requiere_rol("admin")
def api_checklist_estadisticas():
    """Panel del check-list: cumplimiento, adherencia, ranking de lo que más
    falla y las fallas con su tiempo de resolución. No toca DDM salvo para
    saber cuántos ordeñes por día tiene el tambo, que es lo que define cuántas
    cargas se ESPERABAN (ver checklist.estadisticas)."""
    tambo = _tambo_del_request()
    hasta = request.args.get("hasta") or datetime.date.today().isoformat()
    desde = request.args.get("desde") or (
        datetime.date.fromisoformat(hasta) - datetime.timedelta(days=29)).isoformat()
    try:
        datos = checklist.estadisticas(tambo, desde, hasta, ordenes_por_dia=_max_sesiones(tambo))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(datos)


@app.get("/api/checklist/config/resumen")
@auth.requiere_rol("admin")
def api_checklist_config_resumen():
    """Si las novedades del check-list (fallas abiertas/resueltas) van en el
    resumen periódico por WhatsApp/Telegram/Email."""
    return jsonify({"activo": config_alertas.checklist_resumen_activo()})


@app.post("/api/checklist/config/resumen")
@auth.requiere_rol("admin")
def api_checklist_config_resumen_set():
    body = request.json or {}
    config_alertas.set_checklist_resumen(bool(body.get("activo")))
    return jsonify({"ok": True})


@app.post("/api/checklist/config/probar_resumen")
@auth.requiere_rol("admin")
def api_checklist_probar_resumen():
    """Manda las novedades del check-list (fallas abiertas y resueltas) ya
    mismo, por los canales activos — sin esperar al horario configurado.
    Mismo criterio que /api/tablero/config/probar_resumen."""
    tambo = _tambo_del_request()
    datos = checklist.novedades(tambo)
    texto = checklist.texto_novedades(datos, nombre_tambo=tambos.nombre_de(tambo))
    if not texto:
        return jsonify({"error": "No hay fallas abiertas ni resueltas para mandar."}), 400
    html = checklist.html_novedades(datos, nombre_tambo=tambos.nombre_de(tambo))
    try:
        _enviar_resumen_a_canales_activos(texto, html)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ok": True})


@app.get("/checklist/foto/<int:foto_id>")
def checklist_foto(foto_id: int):
    """Sirve una foto del check-list. La ruta se arma SIEMPRE desde lo que hay
    en la base, nunca con algo del request (ver checklist.ruta_de_foto)."""
    ruta = checklist.ruta_de_foto(foto_id)
    if not ruta:
        return jsonify({"error": "No existe esa foto"}), 404
    carpeta, nombre = os.path.dirname(ruta[0]), ruta[1]
    return send_from_directory(carpeta, nombre)


# ---------------------------------------------------------------------------
# Bitácora de incidentes y reparaciones: la carga rápida de cualquier
# empleado, a cualquier hora, sin la agenda fija del Check-list. Mismo patrón
# de página propia + PWA + cola offline que /checklist/ (ver bitacora.py).
# ---------------------------------------------------------------------------
@app.get("/bitacora/")
def bitacora_pagina():
    return render_template("bitacora.html", usuario=auth.usuario_actual(),
                           rol=auth.rol_actual(), tambo=_tambo_del_request())


@app.get("/bitacora/manifest.webmanifest")
def bitacora_manifest():
    return jsonify({
        "name": "Bitácora del tambo", "short_name": "Bitácora",
        "start_url": "/bitacora/", "scope": "/bitacora/",
        "display": "standalone", "background_color": "#0b1016", "theme_color": "#0b1016",
        "icons": [{"src": "/static/img/logo.svg", "sizes": "any", "type": "image/svg+xml",
                   "purpose": "any"}],
    })


@app.get("/bitacora/sw.js")
def bitacora_sw():
    """Service worker con scope /bitacora/ -- mismo motivo que checklist_sw:
    con scope "/" interceptaría la app principal."""
    js = """
const CACHE = 'bitacora-v1';
const SHELL = ['/bitacora/', '/bitacora/manifest.webmanifest'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (!url.pathname.startsWith('/bitacora/') || url.pathname.includes('/api/')) return;
  e.respondWith(
    fetch(e.request).then(r => {
      const copia = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, copia));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
"""
    return Response(js, mimetype="application/javascript")


@app.get("/api/bitacora/abiertos")
def api_bitacora_abiertos():
    tambo = _tambo_del_request()
    return jsonify(bitacora.abiertos(tambo))


@app.post("/api/bitacora/registro")
def api_bitacora_guardar():
    """Guarda un registro nuevo. Llega como multipart en UNA sola pieza --el
    JSON en el campo `datos` y las fotos en `foto_0`, `foto_1`, ...-- mismo
    criterio que /api/checklist/corrida: del otro lado hay una cola que
    reintenta, y un registro a medias es peor que ninguno."""
    tambo = _tambo_del_request()
    try:
        datos = json.loads(request.form.get("datos") or "{}")
    except ValueError:
        return jsonify({"error": "El cuerpo del registro no es JSON válido"}), 400

    fotos = []
    try:
        for campo, archivo in request.files.items(multi=True):
            if not campo.startswith("foto_"):
                continue
            contenido = archivo.read()
            ext = bitacora.validar_foto(archivo.filename or campo, archivo.mimetype, contenido)
            fotos.append((bitacora.nombre_de_foto(ext), contenido))

        res = bitacora.crear_registro(
            tambo=tambo, tipo=datos.get("tipo"), sector=datos.get("sector"),
            puesto=datos.get("puesto"), descripcion=datos.get("descripcion"),
            usuario=auth.usuario_actual(), fecha=datos.get("fecha"),
            offline_id=datos.get("offline_id"), fotos=fotos)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@app.post("/api/bitacora/registro/<int:registro_id>/resolver")
def api_bitacora_resolver(registro_id: int):
    if not bitacora.resolver(registro_id, auth.usuario_actual()):
        return jsonify({"error": "Ese registro no existe o ya estaba resuelto."}), 400
    return jsonify({"ok": True})


@app.get("/bitacora/foto/<int:foto_id>")
def bitacora_foto(foto_id: int):
    """Sirve una foto de la bitácora. Mismo criterio que checklist_foto: la
    ruta se arma SIEMPRE desde lo que hay en la base."""
    ruta = bitacora.ruta_de_foto(foto_id)
    if not ruta:
        return jsonify({"error": "No existe esa foto"}), 404
    carpeta, nombre = os.path.dirname(ruta[0]), ruta[1]
    return send_from_directory(carpeta, nombre)


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
def _servir_cacheado(tambo: str, consulta_id: str, mensaje: str, sql: str | None = None):
    key = _clave(tambo, consulta_id)
    data, fresh = _cache_get(key, allow_stale=True)
    if data is None:
        # "Esta tabla no existe en esta base" no se arregla reintentando (ver
        # _errores_tabla): mostrarlo tal cual en vez de quedarse en
        # "calentando" para siempre sin nunca resolver.
        with _cache_lock:
            error_tabla = _errores_tabla.get(key)
        if error_tabla:
            return None, (jsonify({"no_disponible": True,
                                   "mensaje": "Esta sala no tiene los datos necesarios para esta sección."}), 200)
        _refresh_async(tambo, consulta_id, sql)
        return None, (jsonify({"calentando": True, "mensaje": mensaje}), 202)
    if not fresh:
        _refresh_async(tambo, consulta_id, sql)
    return data, None


@app.get("/api/salud/rcs_grupo")
@auth.requiere_rol("admin")
def api_salud_rcs_grupo():
    """Resumen de células somáticas (RCS) por rodeo: promedio del último
    control, cuántas superan 300.000 ahora y en el control previo, y cuántas
    son crónicas."""
    tambo = _tambo_del_request()
    sql = salud.sql_rcs_por_grupo(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_rcs_grupo", "Calculando RCS por rodeo…", sql)
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
    sql = salud.sql_rcs_vacas(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_rcs_vacas", "Calculando vacas con RCS alto…", sql)
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
    sql = salud.sql_conductividad_rebanio(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_conductividad", "Calculando conductividad…", sql)
    if espera:
        return espera
    return jsonify({**data, "umbral": salud.COND_ALTA})


@app.get("/api/salud/produccion_rodeo")
@auth.requiere_rol("admin")
def api_salud_produccion_rodeo():
    """Estadística de producción por rodeo con tendencias (día y semana)."""
    tambo = _tambo_del_request()
    sql = salud.sql_produccion_por_rodeo(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_produccion_rodeo", "Calculando producción por rodeo…", sql)
    if espera:
        return espera
    return jsonify({"rodeos": salud.resumen_por_rodeo(data["columns"], data["rows"])})


TOP_ATENCION_MIN, TOP_ATENCION_MAX = 5, 300  # sanidad: rango razonable para el techo de pantalla


def _top_atencion_de(tambo: str) -> int:
    """Cuántas vacas listar en "Atención" (clásico y experimental). Configurable
    por tambo en ⚙ Configuración — NO cambia el índice, solo cuánto de la lista
    ya ordenada se muestra. Vacío/inválido = el valor de siempre (15)."""
    valor = configuracion_tambo.config_de(tambo).get("top_atencion")
    if not valor:
        return salud.TOP_ATENCION
    try:
        return max(TOP_ATENCION_MIN, min(TOP_ATENCION_MAX, int(valor)))
    except (TypeError, ValueError):
        return salud.TOP_ATENCION


@app.get("/api/salud/atencion")
@auth.requiere_rol("admin")
def api_salud_atencion():
    """Vacas a revisar, ordenadas por un índice de atención PROPIO.

    OJO: no es el score del add-on Chi (ese se calcula dentro de su ejecutable
    y no queda en la base). Usa las mismas señales, con pesos definidos acá."""
    tambo = _tambo_del_request()
    sql = salud.sql_atencion_datos(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_atencion", "Calculando índice de atención…", sql)
    if espera:
        return espera
    fichas = salud.calcular_atencion(data["columns"], data["rows"], top=_top_atencion_de(tambo))
    return jsonify({"vacas": fichas, "estimacion_propia": True})


@app.get("/api/salud/atencion_v2")
@auth.requiere_rol("admin")
def api_salud_atencion_v2():
    """Vacas a revisar según el índice EXPERIMENTAL multi-sistema (ubre /
    metabólico / general), en validación de campo. Se muestra en paralelo al
    índice clásico (/api/salud/atencion), no lo reemplaza: el backtest contra
    647 diagnósticos reales no mostró que supere al clásico, pero las señales
    que usa sí están validadas individualmente. Cada vaca trae sus "motivos"
    en texto para que el operario juzgue si la señal es válida en el campo.

    En sala convencional corre igual, solo que sin las dos señales propias
    del controlador de la rotativa (alarma de bajo rendimiento y de
    conductividad del equipo, ambas de CMSMilkYield) — el resto (caída de
    leche, conductividad de sesión, BCS) sale de tablas comunes a cualquier
    sala, ver salud.sql_atencion_v2. Lo mismo si esta base no tiene la cámara
    BCS instalada (`_tiene_bcs_de`): sigue andando con lo que sí tiene."""
    tambo = _tambo_del_request()
    con_alarmas = tambos.tipo_sala(tambo) == "rotativa"
    con_bcs = _tiene_bcs_de(tambo)
    sql = salud.sql_atencion_v2(salas.de(tambo).sql_grupos(), con_alarmas, con_bcs)
    data, espera = _servir_cacheado(tambo, "salud_atencion_v2", "Calculando índice experimental…", sql)
    if espera:
        return espera
    # El riesgo heredado se inyecta como función (no se importa dentro de
    # salud.py) para no acoplar el motor del índice ni a la lectura del Excel
    # de toros ni a esta consulta — mismo criterio que `ocupacion_fn`.
    # Mitad padre (catálogo genético) + mitad madre (su historia clínica).
    madres = _historia_madres(tambo)
    rutas_gen = configuracion_tambo.rutas_toros(tambo)
    buscar_toro = genetica.buscador(rutas_gen)
    fichas = salud.calcular_atencion_v2(
        data["columns"], data["rows"], top=_top_atencion_de(tambo),
        genetica_fn=lambda p, m: herencia.de(buscar_toro, madres, p, m))
    gen = genetica.resumen(rutas_gen)
    return jsonify({"vacas": fichas, "experimental": True,
                    "incluye_alarmas_equipo": con_alarmas,
                    "genetica": {
                        "toros": gen["toros"], "con_riesgo": gen["con_riesgo"],
                        "peso": salud.PESO_GENETICA,
                        # Si hay datos ficticios en juego, la pantalla lo avisa.
                        "aviso_simulado": gen["aviso_simulado"],
                        "escala": gen["escala"], "error": gen["error"],
                    }})


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


@app.get("/api/iot/pantalla")
def api_iot_pantalla():
    """Estado del gateway IoT para una pantalla externa (ESP32), SIN login --
    un microcontrolador no puede iniciar sesión como un navegador. Público a
    propósito, mismo criterio que /webhook/whatsapp: se expone lo mínimo y
    nada sensible (ni animales, ni plata) -- el mismo estado de la rotativa
    y los mismos sensores que ya muestra el panel de Monitoreo IoT, en un
    JSON chico y plano, fácil de parsear con la poca memoria de un
    microcontrolador."""
    tambo = _tambo_del_request()
    sistema = iot_monitoreo.estado_sistema(tambo)
    sensores = {
        s["clave"]: {"valor": s["valor"], "unidad": s["unidad"], "label": s["label"]}
        for s in iot_monitoreo.lecturas_actuales()
    }
    return jsonify({"estado": sistema["estado"], "desde": sistema["desde"], "sensores": sensores})


_RANGOS_HISTORICO_IOT = {"7d": 7, "15d": 15, "30d": 30, "180d": 180}


@app.get("/api/iot/pantalla/historico")
def api_iot_pantalla_historico():
    """Histórico de UN sensor para el gráfico de la pantalla ESP32 -- mismo
    criterio público que /api/iot/pantalla (un microcontrolador no puede
    iniciar sesión). Ver iot_monitoreo.historico para el agrupado en baldes
    de tiempo (la pantalla no tiene memoria para miles de puntos)."""
    sensor = request.args.get("sensor", "")
    rango = request.args.get("rango", "7d")
    dias = _RANGOS_HISTORICO_IOT.get(rango)
    if dias is None:
        return jsonify({"error": f"Rango inválido: {rango} (opciones: "
                                  f"{', '.join(_RANGOS_HISTORICO_IOT)})"}), 400
    validos = {s["clave"] for s in iot_monitoreo.SENSORES_PLANEADOS} | {"ith"}
    if sensor not in validos:
        return jsonify({"error": f"Sensor inválido: {sensor}"}), 400
    hasta = datetime.datetime.now()
    desde = hasta - datetime.timedelta(days=dias)
    puntos = iot_monitoreo.historico(sensor, desde.isoformat(), hasta.isoformat())
    return jsonify({"sensor": sensor, "rango": rango, "puntos": puntos})


def _pedido_via_tunel() -> bool:
    """True si el pedido llegó por el túnel de Cloudflare (público, internet)
    en vez de directo por la red del tambo. Cloudflared agrega
    CF-Connecting-IP con la IP real del visitante cuando reenvía tráfico del
    túnel; un pedido que entra directo a esta PC por la LAN (como el de la
    pantalla ESP32) nunca trae ese header."""
    return bool(request.headers.get("CF-Connecting-IP"))


@app.get("/api/iot/pantalla/io")
def api_iot_pantalla_io():
    """Estado de las 8 entradas + 8 salidas del M300 para la pestaña
    Actuadores de la pantalla ESP32 -- mismo criterio público que
    /api/iot/pantalla (de solo lectura, sin nada sensible)."""
    return jsonify(iot_monitoreo.panel_io())


@app.post("/api/iot/pantalla/actuador")
def api_iot_pantalla_actuador():
    """Pulso manual de un actuador (salida del M300) pedido desde la
    pantalla ESP32. A DIFERENCIA de /api/iot/pantalla e /historico (solo
    lectura, público a propósito), esto ACTIVA un equipo real -- se bloquea
    todo pedido que llegue por el túnel de Cloudflare (internet) y solo se
    acepta el que entra directo por la red del tambo, donde vive la
    pantalla. Ver iot_monitoreo.solicitar_pulso: esto solo ENCOLA el pulso,
    lo ejecuta iot_lavado.py (dueño único de la conexión Modbus al M300)."""
    if _pedido_via_tunel():
        return jsonify({"error": "No se puede activar un actuador desde fuera de la red del tambo"}), 403
    datos = request.get_json(silent=True) or {}
    canal = datos.get("canal", "")
    if not iot_monitoreo.solicitar_pulso(canal):
        return jsonify({"error": f"Canal inválido: {canal}"}), 400
    return jsonify({"encolado": True, "canal": canal}), 202


@app.get("/api/iot/pantalla/lavado")
def api_iot_pantalla_lavado():
    """Historial de ciclos de lavado/barrido de la rotativa (contactos DI,
    ver ciclos_lavado) MÁS el estado del programa automático en curso (ver
    lavado_programa.estado) -- todo para la pestaña Lavado Automático de la
    pantalla ESP32. Mismo criterio público que el resto de /api/iot/pantalla*
    para la parte de lectura."""
    return jsonify({"ciclos": iot_monitoreo.ciclos_lavado(), "programa": lavado_programa.estado()})


@app.post("/api/iot/pantalla/lavado/iniciar")
def api_iot_pantalla_lavado_iniciar():
    """Arranca el programa de lavado automático (ver lavado_programa.py) --
    ACTIVA relés reales, mismo criterio de seguridad que /actuador: se
    bloquea todo pedido que llegue por el túnel de Cloudflare."""
    if _pedido_via_tunel():
        return jsonify({"error": "No se puede iniciar un lavado desde fuera de la red del tambo"}), 403
    if not lavado_programa.solicitar_inicio():
        return jsonify({"error": "No hay etapas configuradas, o ya hay un lavado en curso."}), 400
    return jsonify({"ok": True}), 202


@app.post("/api/iot/pantalla/lavado/cancelar")
def api_iot_pantalla_lavado_cancelar():
    """Corta el programa de lavado automático en curso (apaga los relés de
    la etapa activa). Mismo criterio de seguridad que /actuador e /iniciar."""
    if _pedido_via_tunel():
        return jsonify({"error": "No se puede cancelar un lavado desde fuera de la red del tambo"}), 403
    lavado_programa.solicitar_cancelacion()
    return jsonify({"ok": True}), 202


@app.get("/api/lavado_automatico/programa")
@auth.requiere_rol("admin")
def api_lavado_automatico_listar():
    """Configuración de las etapas del lavado automático, para el editor de
    ⚙ Configuración › 🧼 Lavado Automático."""
    custom = iot_canales.nombres()
    reles_disponibles = [{"clave": c, "nombre": custom.get(c, l)} for c, l in iot_monitoreo.SALIDAS_PANEL]
    return jsonify({"etapas": lavado_programa.etapas(), "reles_disponibles": reles_disponibles})


@app.post("/api/lavado_automatico/programa")
@auth.requiere_rol("admin")
def api_lavado_automatico_guardar():
    etapas = (request.json or {}).get("etapas")
    if not isinstance(etapas, list):
        return jsonify({"error": "Formato inválido."}), 400
    try:
        lavado_programa.guardar_etapas(etapas)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.get("/api/iot/conexion")
@auth.requiere_rol("admin")
def api_iot_conexion_listar():
    return jsonify(iot_conexion.config())


@app.post("/api/iot/conexion")
@auth.requiere_rol("admin")
def api_iot_conexion_guardar():
    datos = request.json or {}
    try:
        iot_conexion.guardar(datos.get("host"), datos.get("port"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.get("/api/iot/canales")
@auth.requiere_rol("admin")
def api_iot_canales_listar():
    """Nombres (custom o genérico) de las 8 entradas + 8 salidas del M300,
    para el editor de ⚙ Configuración › 🔌 Entradas/Salidas."""
    custom = iot_canales.nombres()
    entradas = [{"clave": c, "default": l, "nombre": custom.get(c, l)} for c, l in iot_monitoreo.ENTRADAS_PANEL]
    salidas = [{"clave": c, "default": l, "nombre": custom.get(c, l)} for c, l in iot_monitoreo.SALIDAS_PANEL]
    return jsonify({"entradas": entradas, "salidas": salidas})


@app.post("/api/iot/canales")
@auth.requiere_rol("admin")
def api_iot_canales_guardar():
    nombres = (request.json or {}).get("nombres")
    if not isinstance(nombres, dict):
        return jsonify({"error": "Formato inválido."}), 400
    try:
        iot_canales.guardar(nombres)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


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
    la lista de vacas fuera de rango. Cada vaca se anota acá con SU objetivo
    (según su propio DEL, ver `salud.objetivo_bcs`) y la banda aceptable
    alrededor — el frontend ya no decide el rango, solo lo pinta. El filtrado
    por estado reproductivo lo sigue haciendo el frontend sobre este listado."""
    tambo = _tambo_del_request()
    sql = salud.sql_bcs_vacas(salas.de(tambo).sql_grupos())
    data, espera = _servir_cacheado(tambo, "salud_bcs_vacas", "Calculando condición corporal…", sql)
    if espera:
        return espera
    vacas = []
    for r in data["rows"]:
        v = dict(zip(data["columns"], r))
        objetivo = salud.objetivo_bcs(v.get("del"))
        v["objetivo"] = objetivo
        if objetivo is not None:
            v["banda_inf"] = round(objetivo - salud.TOLERANCIA_BCS, 3)
            v["banda_sup"] = round(objetivo + salud.TOLERANCIA_BCS, 3)
            v["fuera_de_rango"] = (v["score"] is not None
                                   and not (v["banda_inf"] <= v["score"] <= v["banda_sup"]))
        else:
            v["banda_inf"] = v["banda_sup"] = v["fuera_de_rango"] = None
        vacas.append(v)
    # La curva de referencia entera (no solo los puntos con quiebre), para que
    # el gráfico la dibuje como una línea continua igual que la de origen —
    # sin esto el frontend tendría que reimplementar la interpolación.
    curva = [{"del": d, "objetivo": salud.objetivo_bcs(d),
             "banda_inf": round(salud.objetivo_bcs(d) - salud.TOLERANCIA_BCS, 3),
             "banda_sup": round(salud.objetivo_bcs(d) + salud.TOLERANCIA_BCS, 3)}
            for d in range(-30, 451, 10)]
    return jsonify({"vacas": vacas, "curva": curva, "tolerancia": salud.TOLERANCIA_BCS})


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
        test_controles = db.run_query(ficha_animal.sql_test_leche_controles(rp), tambo=tambo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"No se pudo consultar la base: {exc}"}), 502

    # BCS (cámara BCS) y el detalle diario de conductividad/alarmas (tabla
    # CMSMilkYield, propia del controlador de la rotativa) dependen de hardware
    # o esquema que no todas las instalaciones tienen — p.ej. San José (sala
    # convencional) no tiene ninguna de las dos tablas. Se degradan a "sin
    # datos" en vez de tirar abajo el resto de la ficha, que sí es universal.
    try:
        bcs = db.run_query(ficha_animal.sql_bcs_individual(rp), tambo=tambo)
    except db.TablaNoDisponibleError:
        bcs = {"columns": [], "rows": []}
    try:
        test_diario = db.run_query(ficha_animal.sql_test_leche_diario(rp), tambo=tambo)
    except db.TablaNoDisponibleError:
        test_diario = {"columns": [], "rows": []}

    # Rentabilidad del animal: lo que produjo contra lo que costó darle de
    # comer. En su propio try/except porque depende de tres cosas de afuera —el
    # proveedor del mixer, el mapeo de lotes y la planilla de precios— y ninguna
    # puede tirar abajo la ficha, que es universal.
    rent = None
    try:
        rent = _rentabilidad_animal(tambo, rp, herd=rebano.por_defecto(tambo),
                                     info=dict(zip(info["columns"], info["rows"][0])))
    except Exception as exc:  # noqa: BLE001
        rent = {"error": str(exc)}

    # Índice de mérito: la vida ya vivida del animal comparada con el rodeo.
    # Igual que los otros paneles, en su propio try/except.
    mer = None
    try:
        ctx = _merito_ctx(tambo, rebano.por_defecto(tambo))
        if ctx is None:
            mer = {"error": "El rodeo no entró completo en la consulta: los "
                            "percentiles saldrían mal calibrados."}
        else:
            mer = merito.de_animal(rp, ctx)
            if mer is not None:
                mer["escala"] = merito.escala_rodeo(ctx)
    except Exception as exc:  # noqa: BLE001
        mer = {"error": str(exc)}

    # Árbol de ancestros: padre (genético, del catálogo de toros) + línea
    # materna (fenotípica, de la propia base). En su propio try/except porque
    # depende del pedigrí estar cargado y del catálogo existir — si falla, la
    # ficha se muestra igual sin el panel, que es un agregado.
    herencia_arbol = None
    try:
        ped = db.run_query(ficha_animal.sql_pedigri(rp), tambo=tambo, max_rows=5)
        if ped["rows"]:
            pedigri = dict(zip(ped["columns"], ped["rows"][0]))
            # Producción real de las ancestras hembras (la "tendencia a
            # producir" medida, no estimada de un catálogo).
            rps = [pedigri.get("madre_rp"), pedigri.get("abuela_rp")]
            rps = [int(x) for x in rps if x is not None]
            prod_anc = {}
            if rps:
                dp = db.run_query(ficha_animal.sql_produccion_ancestras(rps),
                                  tambo=tambo, max_rows=10)
                idxp = {c: i for i, c in enumerate(dp["columns"])}
                prod_anc = {r[idxp["rp"]]: dict(zip(dp["columns"], r)) for r in dp["rows"]}
            herencia_arbol = herencia.arbol(
                pedigri, _historia_madres(tambo), prod_anc,
                genetica.buscador(configuracion_tambo.rutas_toros(tambo)))
    except Exception:  # noqa: BLE001
        herencia_arbol = None
    try:
        aviso_gen = genetica.resumen(
            configuracion_tambo.rutas_toros(tambo)).get("aviso_simulado")
    except Exception:  # noqa: BLE001
        aviso_gen = None      # falta el Excel de toros: la ficha se muestra igual

    return jsonify({
        "info": dict(zip(info["columns"], info["rows"][0])),
        "eventos": filas(eventos),
        "produccion": filas(produccion),
        "bcs": filas(bcs),
        "test_diario": filas(test_diario),
        "test_controles": filas(test_controles),
        "rentabilidad": rent,
        "merito": mer,
        "herencia": herencia_arbol,
        "genetica_aviso": aviso_gen,
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


# Historia clínica de las madres: diagnósticos de años atrás, no cambia en el
# día. TTL largo a propósito, la consulta es cara.
HERENCIA_CACHE_TTL_S = 6 * 3600


def _historia_madres(tambo: str) -> dict:
    """{rp: {riesgo, mastitis, metritis...}} — historia clínica de cada vaca,
    para usarla como la mitad "madre" del riesgo heredado (ver herencia.py).

    Se cachea aparte (no por `_run_consulta`) por una razón concreta: devuelve
    una fila por MADRE y hay más madres que el tope genérico de 5.000 filas de
    db.py — con el tope por defecto se truncaría en silencio y algunas vacas
    quedarían sin la mitad materna sin que nada lo avise.

    Es una foto que casi no se mueve (diagnósticos históricos), así que el TTL
    largo alcanza. Si falla devuelve {} y el riesgo heredado se calcula solo
    con el padre — el índice no se cae por esto, que es un dato de contexto."""
    key = _clave(tambo, "__herencia_madres__")
    cacheado, fresco = _cache_get(key, allow_stale=True, ttl=HERENCIA_CACHE_TTL_S)
    if cacheado is not None and fresco:
        return cacheado
    try:
        data = db.run_query(herencia.sql_historia(), tambo=tambo, max_rows=20000)
        if data.get("truncated"):
            # Mejor sin dato materno que con la mitad de las madres faltando en
            # silencio: el llamador cae a "solo padre" y la pantalla lo dice.
            return {}
        indice = herencia.indice_madres(data["columns"], data["rows"])
        _cache_set(key, indice)
        return indice
    except Exception:  # noqa: BLE001
        return cacheado if cacheado is not None else {}


def _grupos_ordene(tambo: str) -> list:
    """OIDs de los grupos de ordeñe REALES de esta sala (ver
    `salas.de(tambo).sql_grupos()`): NO incluye corrales que no pasan por el
    ordeño —secas, preparto, vaquillonas— ni, en una base compartida, grupos
    de otros tambos. A propósito NO atrapa excepciones: si esto falla, es
    mejor que la pantalla lo diga que mostrar en silencio rodeos que no son
    de ordeñe (mismo criterio de fallar ruidoso que `salas/`)."""
    data = _run_consulta("rutina_grupos", tambo, salas.de(tambo).sql_grupos())
    idx_grupo = data["columns"].index("grupo")
    return [r[idx_grupo] for r in data["rows"]]


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
        grupos = _grupos_ordene(tambo)
    pesos_txt = request.args.get("pesos")
    pesos = None
    if pesos_txt:
        try:
            pesos = json.loads(pesos_txt)
        except ValueError:
            pesos = None
    return grupos, pesos


UMBRAL_PREP_S_MIN, UMBRAL_PREP_S_MAX = 10, 600  # sanidad: rango razonable en segundos


def _umbral_prep_de_request(tambo: str | None = None):
    """Objetivo de colocación (segundos), en orden de prioridad: lo que pide la
    URL (el selector de "Configurar análisis", que es por sesión y por
    navegador), después lo guardado del tambo en ⚙ Configuración, y si no hay
    ninguno None — ahí cada sala decide, y una sala puede no puntuar el
    componente hasta que el tambo fije su objetivo (ver
    `salas.convencional.UMBRAL_PREP_S`).

    El de la URL le gana al guardado a propósito: sirve para probar "¿cómo
    quedaría con 240s?" sin pisarle la configuración al tambo."""
    valor = request.args.get("umbral_prep_s")
    if not valor and tambo:
        valor = (configuracion_tambo.config_de(tambo) or {}).get("umbral_prep_s")
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
    umbral_prep_s = _umbral_prep_de_request(tambo)
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

    identificacion_pct = _identificacion_pct_de(tambo, fecha, fecha, fecha)
    try:
        resultado = salas.de(tambo).analizar_dia(tambo, data["columns"], data["rows"], fecha,
                                                 grupos, pesos, max_sesiones=_max_sesiones(tambo),
                                                 nombres=_nombres_grupos(tambo),
                                                 umbral_prep_s=umbral_prep_s,
                                                 identificacion_pct=identificacion_pct)
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
    umbral_prep_s = _umbral_prep_de_request(tambo)

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
    # Un solo caché de identificación para TODO el rango (no uno por fecha):
    # `sql_identificacion` ya trae un renglón por día, así que alcanza con
    # pedirla una vez — ver `_identificacion_pct_de`.
    rango_desde, rango_hasta = (fechas[0], fechas[-1]) if fechas else (None, None)
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
        identificacion_pct = _identificacion_pct_de(tambo, rango_desde, rango_hasta, fecha)
        try:
            punto = salas.de(tambo).resumen_dia(tambo, data["columns"], data["rows"], fecha, grupos, pesos,
                                                max_sesiones=tope, nombres=nombres_grupos,
                                                umbral_prep_s=umbral_prep_s,
                                                identificacion_pct=identificacion_pct)
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
            # El % de identificación NO sale de `data`: `sql_rendimiento`
            # descarta las visitas sin identificar (ver el docstring de
            # rutina.sql_identificacion), así que va en su propia consulta
            # agregada por día. En su propio try/except porque es un dato
            # secundario: si falla, el resto de Rendimiento Sala igual sirve.
            ident = None
            sql_ident = salas.de(tambo).sql_identificacion(desde.isoformat(), hasta.isoformat())
            if sql_ident:   # None = esta sala todavía no calcula identificación
                try:
                    ident = db.run_query(sql_ident, tambo=tambo,
                                         max_rows=rutina.RANGO_RENDIMIENTO_MAX_DIAS + 2)
                except Exception:  # noqa: BLE001
                    ident = None
            _cache_set(key, {"visitas": data, "ident": ident})
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

    visitas, ident = data["visitas"], data.get("ident")
    sesiones = salas.de(tambo).analizar_rendimiento(tambo, visitas["columns"], visitas["rows"],
                                                    desde.isoformat(), hasta.isoformat(),
                                                    max_sesiones=_max_sesiones(tambo),
                                                    nombres=_nombres_grupos(tambo),
                                                    grupos_ordene=_grupos_ordene(tambo))
    # None = esta sala no calcula identificación, o la consulta falló: el
    # frontend muestra "no disponible" en vez de un 100% que no midió nada.
    # Dispatch por sala: la consulta y las columnas que arma cada una son
    # distintas (`salas.convencional.sql_identificacion` separa sin_lectura de
    # desconocido; `rutina.sql_identificacion` no) — un `armar_identificacion`
    # fijo rompía con KeyError en cuanto la convencional dejó de mandar `None`.
    identificacion = (salas.de(tambo).armar_identificacion(ident["columns"], ident["rows"])
                      if ident else None)
    return jsonify({"desde": desde.isoformat(), "hasta": hasta.isoformat(), "sesiones": sesiones,
                    "identificacion": identificacion,
                    "truncated": visitas.get("truncated", False)})


@app.get("/api/rutina/resumen_dia")
@auth.requiere_rol("admin")
def api_rutina_resumen_dia():
    """Réplica del reporte "Rendimiento de Ordeño" de DelPro para UN día:
    una fila por grupo (ordeños, producción, velocidad, tiempos, retiradas)
    más los totales (identificación, ocupación) y, además, `sesiones`: una
    fila por sesión de ordeño de ese día (rotaciones, horas, producción, igual
    que la tabla densa "por sala/fecha/sesión" del reporte de DelPro). Mismo
    caché que /api/rutina/rendimiento (mismas visitas, pidiendo
    desde=hasta=fecha) — no es una consulta nueva."""
    tambo = _tambo_del_request()
    try:
        fecha = (datetime.datetime.strptime(request.args["fecha"], "%Y-%m-%d").date()
                 if request.args.get("fecha") else _ultimo_dia_datos(tambo))
    except ValueError:
        return jsonify({"error": "Fecha inválida (se espera AAAA-MM-DD)."}), 400

    key = f"{tambo}:rendimiento:{fecha.isoformat()}:{fecha.isoformat()}"
    data, fresh = _cache_get(key, allow_stale=True, ttl=RENDIMIENTO_CACHE_TTL_S)
    if data is None:
        _refresh_rendimiento_async(tambo, fecha, fecha)
        return jsonify({"calentando": True, "mensaje": "Calculando rendimiento de ordeño…"}), 202
    if not fresh:
        _refresh_rendimiento_async(tambo, fecha, fecha)

    visitas = data["visitas"]
    resumen = salas.de(tambo).resumen_grupos_dia(tambo, visitas["columns"], visitas["rows"],
                                                 fecha.isoformat(),
                                                 grupos_ordene=_grupos_ordene(tambo),
                                                 nombres=_nombres_grupos(tambo))
    sesiones = salas.de(tambo).analizar_rendimiento(tambo, visitas["columns"], visitas["rows"],
                                                    fecha.isoformat(), fecha.isoformat(),
                                                    max_sesiones=_max_sesiones(tambo),
                                                    nombres=_nombres_grupos(tambo),
                                                    grupos_ordene=_grupos_ordene(tambo))
    return jsonify({"fecha": fecha.isoformat(), "sesiones": sesiones, **resumen})


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
            # El umbral de retirada sale del equipo, y NO todas las salas lo
            # publican: una Alpro no tiene `CMSMpcSetting` ni ninguna columna
            # equivalente en todo el esquema. Ahí la banda ±25% no se calcula y
            # la pantalla lo dice, en vez de inventar un umbral -- que no seria
            # un dato incompleto sino un diagnostico falso sobre el equipo.
            sala = salas.de(tambo)
            if getattr(sala, "PUBLICA_UMBRAL_RETIRADA", True):
                try:
                    cfg = db.run_query(flujos.SQL_CONFIG_RETIRADA, tambo=tambo, max_rows=5)
                except Exception:  # noqa: BLE001
                    cfg = None
            else:
                cfg = None
            umbrales = flujos.umbrales_retirada(cfg)
            umbrales["publicado_por_el_equipo"] = getattr(
                sala, "PUBLICA_UMBRAL_RETIRADA", True)
            if not umbrales["publicado_por_el_equipo"]:
                # `umbrales_retirada` cae a un valor de respaldo cuando no
                # puede leer la configuracion, y ese numero en pantalla se lee
                # como "el umbral del equipo". Si la sala no lo publica se
                # anulan los tres: mejor un hueco declarado que un umbral
                # inventado con cara de dato.
                for k in ("retirada_delpro", "retirada_min", "retirada_max",
                          "low_flow_limit"):
                    umbrales[k] = None
            rmin, rmax = umbrales["retirada_min"], umbrales["retirada_max"]
            # En serie a propósito: db.py ya serializa por servidor, y lanzarlas
            # en paralelo solo agregaría presión de memoria sobre SQL Express.
            data = {
                "umbrales": umbrales,
                "dia": db.run_query(sala.sql_flujos_por_dia(d, h, rmin, rmax), tambo=tambo,
                                    max_rows=flujos.RANGO_FLUJOS_MAX_DIAS + 2),
                "grupo": db.run_query(sala.sql_flujos_por_grupo(d, h), tambo=tambo, max_rows=100),
                "dist": db.run_query(sala.sql_flujos_distribucion(d, h), tambo=tambo, max_rows=200),
                "deo": db.run_query(sala.sql_flujos_por_deo(d, h), tambo=tambo, max_rows=50),
            }
            # "Tiempo fuera" (sql_tiempo_fuera) es la consulta más pesada de
            # las cinco -- ordena TODAS las bajadas del período por vaca para
            # el LAG, en vez de escanear y sumar. Medido contra la base real:
            # puede superar el timeout con el rango completo de 120 días,
            # aunque las demás respondan en segundos. Por eso corre sobre un
            # tramo más corto (el más reciente del rango elegido) y en su
            # propio try/except: si igual se pasa del timeout, el resto de la
            # página no se ve afectado -- "tiempo fuera" queda sin datos, no
            # toda la pantalla en "calentando" para siempre.
            desde_fuera = max(desde, hasta - datetime.timedelta(days=flujos.RANGO_FUERA_MAX_DIAS - 1))
            try:
                data["fuera"] = db.run_query(
                    sala.sql_flujos_tiempo_fuera(desde_fuera.isoformat(), h), tambo=tambo,
                    max_rows=flujos.RANGO_FUERA_MAX_DIAS + 2)
            except Exception:  # noqa: BLE001
                data["fuera"] = None
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
                                data["fuera"], data["umbrales"])
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


@app.get("/api/reproduccion/bajas_terneros")
@auth.requiere_rol("admin")
def api_bajas_terneros():
    """Terneros nacidos en el período que se dieron de baja antes de los 90
    días de vida, con el motivo real. Ver `reproduccion.sql_bajas_terneros` —
    ESTE TAMBO NO USA UN MOTIVO ESPECÍFICO DE MUERTE PARA TERNEROS, así que la
    lista trae cualquier salida temprana (puede incluir ventas/traslados)."""
    tambo = _tambo_del_request()
    hoy = datetime.date.today()
    try:
        meses = max(1, min(24, int(request.args.get("meses") or 6)))
        riesgo_dias = max(1, min(365, int(request.args.get("riesgo_dias")
                                          or reproduccion.RIESGO_TERNEROS_DIAS)))
    except ValueError:
        return jsonify({"error": "Parámetros numéricos inválidos."}), 400
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

    # La ventana se censura en el extremo reciente: un ternero nacido hace
    # menos de `riesgo_dias` todavía no completó su ventana de riesgo, así que
    # no se sabe si va a tener una salida temprana o no. Incluirlo como "no
    # tuvo baja" infla el número de partos "sanos" con casos que en realidad
    # todavía están en juego — misma trampa que la tasa de concepción censada
    # documentada en CLAUDE.md.
    hasta = _ultimo_dia_datos(tambo, herd) - datetime.timedelta(days=riesgo_dias)
    desde = hasta - datetime.timedelta(days=30 * meses)

    try:
        dp = db.run_query(reproduccion.sql_partos_periodo(str(desde), str(hasta), herd),
                          tambo=tambo, max_rows=5)
        db_ = db.run_query(reproduccion.sql_bajas_terneros(str(desde), str(hasta),
                                                           riesgo_dias, herd),
                           tambo=tambo, max_rows=500)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502

    partos = int(dp["rows"][0][0] or 0) if dp["rows"] else 0
    salida = reproduccion.armar_bajas_terneros(
        db_["columns"], db_["rows"], partos, str(desde), str(hasta), riesgo_dias)
    return jsonify(salida)


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


@app.post("/api/agente/preguntar")
@auth.requiere_rol("admin")
def api_agente_preguntar():
    """Agente que responde preguntas del tambo encadenando herramientas (los
    mismos endpoints que usa cada pantalla) en vez de escribir SQL a ciegas —
    ver el docstring de `agente.py` para el porqué. A diferencia de
    `/api/preguntar`, SÍ corre en tambos de producción: las herramientas son
    consultas fijas y auditadas, no SQL generado en el momento (ese camino
    sigue existiendo solo como último recurso dentro del propio agente, y ahí
    sí respeta el mismo candado que `/api/preguntar`)."""
    if not agente.api_disponible():
        return jsonify({
            "error": "La API de Claude no está configurada. Define la variable de "
                     "entorno ANTHROPIC_API_KEY y reiniciá la aplicación."
        }), 503
    body = request.json or {}
    pregunta = (body.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"error": "Escribí una pregunta."}), 400
    historial = body.get("mensajes") if isinstance(body.get("mensajes"), list) else None
    tambo = _tambo_del_request()
    try:
        resultado = agente.responder(pregunta, tambo, historial=historial)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"No se pudo responder: {exc}"}), 502
    return jsonify(resultado)


# ---------------------------------------------------------------------------
# "Preguntale a IA" por WhatsApp: números autorizados (⚙ Configuración,
# whatsapp_ia.py) le preguntan directo al mismo agente de /api/agente/preguntar
# -- por eso funciona en tambos de producción, a diferencia del SQL-a-ciegas
# de /api/preguntar. Sin sesión/login (Twilio llama esta URL directo, nadie
# inició sesión), así que la seguridad es: (1) se valida que el pedido venga
# realmente de Twilio (firma X-Twilio-Signature -- si no, cualquiera que se
# entere de la URL podría fingir ser un número autorizado) y (2) el número de
# origen tiene que estar en la lista de autorizados, cada uno atado a UN
# tambo fijo.
#
# La firma se valida con la URL PÚBLICA fija, no con request.url: la app
# corre detrás de Cloudflare Tunnel sin ProxyFix, así que request.url ve la
# URL interna (http://127.0.0.1:5310/...), no la que Twilio realmente llamó
# -- si se usara esa, la firma nunca daría válida.
# ---------------------------------------------------------------------------
_WEBHOOK_WHATSAPP_URL = os.environ.get("LACTIA_URL_PUBLICA", "https://www.analiticastambo.com").rstrip("/") \
    + "/webhook/whatsapp"
_WEBHOOK_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhook_whatsapp.log")


def _log_webhook(mensaje: str):
    """Log mínimo del webhook a un archivo propio (no a stdout): el proceso
    puede correr sin consola visible (Programador de tareas), donde un
    print() se pierde. Solo para diagnosticar -- si falla, no debe romper el
    webhook por eso."""
    try:
        with open(_WEBHOOK_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {mensaje}\n")
    except Exception:  # noqa: BLE001
        pass


def _responder_whatsapp_ia(origen: str, pregunta: str, tambo: str):
    if not agente.api_disponible():
        _log_webhook("ABORTADO -- agente.api_disponible() es False (¿falta ANTHROPIC_API_KEY?)")
        return
    try:
        resultado = agente.responder(pregunta, tambo, estilo="whatsapp")
        respuesta = resultado.get("respuesta") or "No pude generar una respuesta."
    except Exception as exc:  # noqa: BLE001
        respuesta = f"Hubo un error respondiendo: {exc}"
        _log_webhook(f"ERROR en agente.responder: {exc}")
    try:
        whatsapp.enviar(respuesta, destino=origen)
        _log_webhook(f"RESPONDIDO a {origen}: {respuesta[:120]!r}")
    except Exception as exc:  # noqa: BLE001
        _log_webhook(f"ERROR mandando la respuesta por WhatsApp: {exc}")


@app.post("/webhook/whatsapp")
def webhook_whatsapp():
    from twilio.request_validator import RequestValidator
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    firma = request.headers.get("X-Twilio-Signature", "")
    origen_crudo = request.form.get("From") or "(sin From)"
    if not token or not RequestValidator(token).validate(_WEBHOOK_WHATSAPP_URL, request.form, firma):
        _log_webhook(f"RECHAZADO por firma inválida -- From={origen_crudo}")
        return "", 403
    origen = (request.form.get("From") or "").replace("whatsapp:", "").strip()
    pregunta = (request.form.get("Body") or "").strip()
    tambo = whatsapp_ia.tambo_autorizado(origen)
    if not tambo or not pregunta:
        _log_webhook(f"IGNORADO -- From={origen!r} autorizado={tambo is not None} "
                     f"pregunta_vacía={not pregunta}")
        return "", 204  # número no autorizado, o mensaje vacío: se ignora en silencio
    _log_webhook(f"OK -- From={origen} tambo={tambo} pregunta={pregunta!r}")
    threading.Thread(target=_responder_whatsapp_ia, args=(origen, pregunta, tambo), daemon=True).start()
    return "", 204


@app.get("/api/whatsapp_ia/autorizados")
@auth.requiere_rol("admin")
def api_whatsapp_ia_listar():
    return jsonify({"autorizados": whatsapp_ia.listar()})


@app.post("/api/whatsapp_ia/autorizados")
@auth.requiere_rol("admin")
def api_whatsapp_ia_guardar():
    items = (request.json or {}).get("autorizados")
    if not isinstance(items, list):
        return jsonify({"error": "Formato inválido."}), 400
    try:
        whatsapp_ia.guardar(items)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Alertas por WhatsApp (Twilio): revisa en los días/horarios configurados
# (⚙ Configuración, tarjeta 🔔 Alertas — antes fijo a las 8:00 y 20:00 todos
# los días, ver config_alertas.horario()) las condiciones fuera de rango y
# manda TODO junto en UN solo mensaje por ciclo (pedido explícito del
# usuario: antes cada carga de CICLA fuera de rango, cada puesto con
# incidencias, etc. mandaba su propio WhatsApp/mail por separado, y un día
# con varios problemas eran decenas de avisos sueltos). No dispara consultas
# SQL pesadas nuevas: para rutina/incidencias lee la misma caché que ya usa
# el dashboard (si no está lista, la dispara para el próximo ciclo en vez de
# forzarla ahora).
# ---------------------------------------------------------------------------
ALERTA_TEMP_CICLA_C = 5.0         # temperatura del caudalímetro (más estricta que la visual de 4°C)
ALERTA_UFC_LASER = 40.0           # U.F.C. de La Serenísima
ALERTA_RUTINA_SCORE_MIN = 60      # score de una sesión de rutina de ordeño

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


def _enviar_resumen_a_canales_activos(texto: str, html: str):
    """Como _enviar_a_canales_activos, pero al canal de correo le manda la
    versión HTML (con badges de color reales — ver `tablero.html_resumen`)
    en vez de solo el texto plano que reciben WhatsApp/Telegram."""
    canales = _canales_disponibles()
    if not canales:
        raise RuntimeError("No hay ningún canal de alerta configurado y activado.")
    ultimo_error = None
    enviado = False
    for mod in canales:
        try:
            if mod is correo and html:
                correo.enviar_html(texto, html)
            else:
                mod.enviar(texto)
            enviado = True
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
    if not enviado:
        raise ultimo_error


def _lineas_alertas_puntuales(tambo: str) -> list:
    """Una línea de texto por tipo de alerta puntual que tenga algo que
    contar HOY (temperatura CICLA, U.F.C. de La Serenísima, score de
    rutina, incidencias de la rotativa) -- antes cada condición individual
    (cada carga, cada puesto) armaba su propio mensaje aparte; ahora se
    agrega en UNA línea por tipo, para que un día con varios problemas siga
    siendo parte de un solo envío."""
    lineas = []

    def _rango(valores, decimales=0):
        """"55" si es un solo valor, "48 – 62" si hay varios distintos --
        mostrar "55 – 55" cuando hay uno solo queda raro."""
        lo, hi = min(valores), max(valores)
        if round(lo, decimales) == round(hi, decimales):
            return f"{lo:.{decimales}f}"
        return f"{lo:.{decimales}f} – {hi:.{decimales}f}"

    usuario, password = os.environ.get("CICLA_USUARIO"), os.environ.get("CICLA_PASSWORD")
    if usuario and password:
        hoy = datetime.date.today()
        cargas, _incompleto = cicla.obtener_cargas(hoy - datetime.timedelta(days=1), hoy, usuario, password)
        altas = [c["temperatura"] for c in cargas
                 if c["temperatura"] is not None and c["temperatura"] > ALERTA_TEMP_CICLA_C]
        if altas:
            lineas.append(f"🌡️ CICLA: {len(altas)} carga(s) hoy con temperatura sobre el umbral "
                          f"({_rango(altas, 1)}°C, umbral {ALERTA_TEMP_CICLA_C}°C).")

    usuario, password = os.environ.get("LASER_USUARIO"), os.environ.get("LASER_PASSWORD")
    if usuario and password:
        entregas = laserenisima.obtener_entregas(usuario, password)
        altas = [e["ufc"] for e in entregas if e["ufc"] is not None and e["ufc"] > ALERTA_UFC_LASER]
        if altas:
            lineas.append(f"🧪 La Serenísima: {len(altas)} entrega(s) con U.F.C. sobre el umbral "
                          f"({_rango(altas)}, umbral {round(ALERTA_UFC_LASER)}).")

    hoy_str = datetime.date.today().strftime("%Y-%m-%d")
    data, _fresh = _cache_get(_clave(tambo, f"rutina:{hoy_str}"), allow_stale=True)
    if data is None:
        _refresh_rutina_async(tambo, hoy_str)
    else:
        grupos = _grupos_ordene(tambo)
        resultado = salas.de(tambo).analizar_dia(tambo, data["columns"], data["rows"], hoy_str, grupos,
                                                 max_sesiones=_max_sesiones(tambo),
                                                 nombres=_nombres_grupos(tambo),
                                                 identificacion_pct=_identificacion_pct_de(tambo, hoy_str, hoy_str, hoy_str))
        bajas = [s["score"] for s in resultado["sesiones"] if s["score"] < ALERTA_RUTINA_SCORE_MIN]
        if bajas:
            lineas.append(f"⏱️ Rutina de ordeño: {len(bajas)} sesión(es) hoy con score bajo "
                          f"({_rango(bajas)}%, umbral {ALERTA_RUTINA_SCORE_MIN}%).")

    data, _fresh = _cache_get(_clave(tambo, "ordeno_inc"), allow_stale=True)
    if data and data.get("rows"):
        idx = {c: i for i, c in enumerate(data["columns"])}
        totales = [
            (r[idx["posicion"]], (r[idx["desliz"]] or 0) + (r[idx["patadas"]] or 0)
             + (r[idx["bloqueos"]] or 0) + (r[idx["recoloc"]] or 0))
            for r in data["rows"]
        ]
        if totales:
            mediana = statistics.median(t for _p, t in totales)
            umbral_rojo = max(round(mediana * 2.5), 4)
            problemas = sorted([(p, t) for p, t in totales if t >= umbral_rojo], key=lambda x: -x[1])
            if problemas:
                # Si ya hay un registro abierto en la Bitácora para ese puesto, se
                # aclara en vez de repetir el mismo aviso como si fuera nuevo cada
                # vez que se dispara la alerta (ver bitacora.abiertos_por_puesto).
                reportados = bitacora.abiertos_por_puesto(tambo)
                partes = []
                for p, t in problemas:
                    fecha_rep = reportados.get(int(p)) if p is not None else None
                    extra = f", ya reportado el {fecha_rep}" if fecha_rep else ""
                    partes.append(f"puesto {p} ({t}{extra})")
                lineas.append(f"🔧 Incidencias: {len(problemas)} puesto(s) muy por encima de la mediana "
                              f"({mediana:.0f}) hoy — {', '.join(partes)}. Posible unidad fallada.")

    return lineas


def _html_alertas_puntuales(lineas: list) -> str:
    from html import escape as esc
    filas = "".join(
        f'<tr><td style="padding:7px 0;border-bottom:1px solid #eef1f4;color:#334155;font-size:13px;">'
        f'{esc(l)}</td></tr>' for l in lineas
    )
    return f"""<div style="background:#f1f5f9;padding:24px 12px;font-family:-apple-system,'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:10px;border:1px solid #e2e8f0;overflow:hidden;">
<tr><td style="background:#0072CE;padding:16px 20px;">
<span style="color:#ffffff;font-size:17px;font-weight:700;">⚠️ Alertas puntuales</span>
</td></tr>
<tr><td style="padding:4px 20px 20px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{filas}</table>
</td></tr>
</table>
</div>"""


def _construir_mensaje_alertas(tambo: str):
    """Arma UN solo mensaje con todo lo que haya para avisar, en vez de un
    envío por cada condición (ver comentario de más arriba). Orden pedido
    por el usuario: Tablero de Diagnóstico, alertas puntuales (CICLA/La
    Serenísima/rutina/incidencias), Check-list. No dispara consultas
    pesadas nuevas -- todo sale de caché. Devuelve (texto, html), o
    (None, None) si no hay nada que contar."""
    partes_texto = []
    secciones_html = []

    valores = _valores_tablero(tambo)
    armado = tablero.armar(valores, tablero.config_de(tambo), lecturas=tablero.lecturas_de(tambo))
    texto_tablero = tablero.texto_resumen(armado, nombre_tambo=tambos.nombre_de(tambo))
    if texto_tablero:
        partes_texto.append(texto_tablero)
        secciones_html.append(tablero.html_resumen(armado, nombre_tambo=tambos.nombre_de(tambo)))

    lineas = _lineas_alertas_puntuales(tambo)
    if lineas:
        partes_texto.append("\n".join(lineas))
        secciones_html.append(_html_alertas_puntuales(lineas))

    if config_alertas.checklist_resumen_activo():
        datos_cl = checklist.novedades(tambo)
        texto_cl = checklist.texto_novedades(datos_cl, nombre_tambo=tambos.nombre_de(tambo))
        if texto_cl:
            partes_texto.append(texto_cl)
            secciones_html.append(checklist.html_novedades(datos_cl, nombre_tambo=tambos.nombre_de(tambo)))

    if not partes_texto:
        return None, None
    return "\n\n".join(partes_texto), "".join(secciones_html) or None


def _revisar_alertas_whatsapp():
    if not _canales_disponibles():
        return
    try:
        texto, html = _construir_mensaje_alertas(tambos.DEFAULT_TAMBO)
        if texto:
            _enviar_resumen_a_canales_activos(texto, html)
    except Exception:  # noqa: BLE001
        pass


def _proximo_horario_alertas() -> datetime.datetime:
    """Próximo datetime en que toca revisar, según los días/horarios que
    configuró el usuario (config_alertas.horario()). Mira hasta 8 días
    adelante -- con un solo día de la semana habilitado, el próximo puede
    caer casi una semana después, no mañana."""
    horario = config_alertas.horario()
    ahora = datetime.datetime.now()
    candidatos = []
    for dias in range(8):
        base = (ahora + datetime.timedelta(days=dias)).replace(second=0, microsecond=0)
        if base.weekday() not in horario["dias"]:
            continue
        for hora in horario["horas"]:
            hh, mm = hora.split(":")
            candidato = base.replace(hour=int(hh), minute=int(mm))
            if candidato > ahora:
                candidatos.append(candidato)
    return min(candidatos)


# Se activa cuando el usuario guarda un horario nuevo, para que el ciclo de
# abajo recalcule el próximo horario YA en vez de esperar a que se cumpla el
# horario viejo (con el que ya se había dormido) para recién ahí notar el
# cambio -- el mismo tipo de "guardé y no pasó nada hasta la próxima" que
# categoriza a los reinicios de servidor.py, evitado acá desde el vamos.
_horario_alertas_cambiado = threading.Event()


def _bucle_alertas_whatsapp():
    while True:
        espera_s = (_proximo_horario_alertas() - datetime.datetime.now()).total_seconds()
        recalcular = _horario_alertas_cambiado.wait(timeout=max(espera_s, 1))
        _horario_alertas_cambiado.clear()
        if recalcular:
            continue
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


@app.get("/api/alertas/horario")
@auth.requiere_rol("admin")
def api_alertas_horario():
    """Días de la semana y horarios en que se revisa y avisa."""
    return jsonify(config_alertas.horario())


@app.post("/api/alertas/horario")
@auth.requiere_rol("admin")
def api_alertas_horario_set():
    body = request.json or {}
    try:
        config_alertas.set_horario(body.get("dias", []), body.get("horas", []))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _horario_alertas_cambiado.set()
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


@app.post("/api/alertas/probar_ciclo")
@auth.requiere_rol("admin")
def api_alertas_probar_ciclo():
    """Arma y manda el mensaje consolidado REAL (Tablero + alertas puntuales
    + Check-list, ver _construir_mensaje_alertas) ya mismo, sin esperar al
    próximo horario -- para poder ver cómo queda el mensaje del día antes de
    que se dispare solo."""
    tambo = _tambo_del_request()
    try:
        texto, html = _construir_mensaje_alertas(tambo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"No se pudo armar el mensaje: {exc}"}), 500
    if not texto:
        return jsonify({"error": "Hoy no hay nada para avisar (ni Tablero tildado, ni alertas "
                                  "puntuales, ni novedades del check-list)."}), 400
    try:
        _enviar_resumen_a_canales_activos(texto, html)
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
            "grupos": db.run_query(conciliacion.sql_grupos(salas.grupos_subquery(tambo), herd),
                                   tambo=tambo, max_rows=500),
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
                # Valorización: en su propio try/except porque depende de una
                # planilla que el tambo mantiene a mano. Si falta o está mal, la
                # conversión (que es física y no necesita precios) tiene que
                # seguir saliendo igual.
                costo, diag_costo, precio_litro = {}, {}, None
                try:
                    pr = precios_alimentos.leer(configuracion_tambo.ruta_precios(tambo))
                    if pr.get("precios"):
                        costo, diag_costo = alimentacion.costo_por_lote_dia(
                            consumos, pr["precios"])
                    precio_litro = pr.get("precio_litro")
                    diag_costo["precios"] = precios_alimentos.resumen(
                        configuracion_tambo.ruta_precios(tambo))
                except Exception as exc:  # noqa: BLE001
                    diag_costo = {"precios": {"error": str(exc)}}
                _cache_set(k, {
                    "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                    "ms": {f"{l}|{f.isoformat()}": v for (l, f), v in ms.items()},
                    "costo": {f"{l}|{f.isoformat()}": v for (l, f), v in costo.items()},
                    "precio_litro": precio_litro,
                    "diagnostico": {**diag, **diag_costo},
                    "prod_dia": db.run_query(
                        alimentacion.sql_produccion_grupo_dia(desde, hasta, herd),
                        tambo=tambo, max_rows=4000),
                    "prod_vaca": db.run_query(
                        alimentacion.sql_produccion_vaca(desde, hasta, herd),
                        tambo=tambo, max_rows=5000),
                    "solidos": db.run_query(
                        alimentacion.sql_solidos_vaca(desde, hasta, herd),
                        tambo=tambo, max_rows=5000),
                    "grupos": db.run_query(conciliacion.sql_grupos(salas.grupos_subquery(tambo), herd),
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

    def _por_lote_fecha(d):
        """El caché guarda las claves como 'lote|fecha' (JSON no admite tuplas)."""
        out = {}
        for clave, v in (d or {}).items():
            lote, fecha = clave.rsplit("|", 1)
            out[(lote, datetime.date.fromisoformat(fecha))] = v
        return out

    salida = alimentacion.analizar(
        data["prod_dia"], data["prod_vaca"], data["solidos"],
        _por_lote_fecha(data["ms"]),
        conciliacion.grupos_de(data["grupos"]), mapeo, data["diagnostico"],
        costo_lote_dia=_por_lote_fecha(data.get("costo")),
        precio_litro=data.get("precio_litro"))
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
                # Valorización: en su propio try/except, como en la pestaña de
                # conversión. La serie física no puede caerse porque falte una
                # planilla que el tambo mantiene a mano.
                costo, precio_litro, res_precios = {}, None, {}
                try:
                    pr = precios_alimentos.leer(configuracion_tambo.ruta_precios(tambo))
                    if pr.get("precios"):
                        costo, _dc = alimentacion.costo_por_lote_dia(consumos, pr["precios"])
                    precio_litro = pr.get("precio_litro")
                    res_precios = precios_alimentos.resumen(
                        configuracion_tambo.ruta_precios(tambo))
                except Exception as exc:  # noqa: BLE001
                    res_precios = {"error": str(exc)}
                _cache_set(k, {
                    "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                    "ms": {f"{l}|{f.isoformat()}": v for (l, f), v in ms.items()},
                    "costo": {f"{l}|{f.isoformat()}": v for (l, f), v in costo.items()},
                    "precio_litro": precio_litro,
                    "precios": res_precios,
                    "prod_dia": db.run_query(
                        alimentacion.sql_produccion_grupo_dia(desde, hasta, herd),
                        tambo=tambo, max_rows=20000),
                    "solidos": db.run_query(
                        conversion_historica.sql_solidos_por_control(desde, hasta, herd),
                        tambo=tambo, max_rows=200),
                    "ordene": db.run_query(
                        conversion_historica.sql_grupos_ordene(salas.grupos_subquery(tambo), herd),
                        tambo=tambo, max_rows=200),
                    "lactancia": db.run_query(
                        conversion_historica.sql_produccion_por_lactancia(
                            salas.grupos_subquery(tambo), desde, hasta, herd),
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

    def _por_lote_fecha(d):
        out = {}
        for clave, v in (d or {}).items():
            lote, fecha = clave.rsplit("|", 1)
            out[(lote, datetime.date.fromisoformat(fecha))] = v
        return out

    salida = conversion_historica.armar(
        data["prod_dia"], _por_lote_fecha(data["ms"]), mapeo, data["solidos"],
        datetime.date.fromisoformat(data["hasta"]),
        [f[0] for f in data["ordene"]["rows"]],
        costo_lote_dia=_por_lote_fecha(data.get("costo")),
        precio_litro=data.get("precio_litro"))
    salida["por_lactancia"] = conversion_historica.lactancia(
        data["lactancia"], datetime.date.fromisoformat(data["hasta"]))
    salida.update({"desde": data["desde"], "hasta": data["hasta"],
                   "precios": data.get("precios") or {}})
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
        # `fresh` SÍ se revisa acá (antes no): sin esto, un error puntual (p.ej.
        # una contraseña mal cargada en "⚙ Configuración") quedaba SERVIDO PARA
        # SIEMPRE una vez cacheado — nada volvía a intentar la consulta hasta
        # reiniciar el proceso, aunque el problema ya estuviera resuelto.
        data, fresh = _cache_get(key, allow_stale=True)
        if data is None:
            _refresh_sala_async(tambo, consulta_id, vivo=False)
            return jsonify({"calentando": True, "mensaje": "Cargando datos de la sala…"}), 202
        if not fresh:
            _refresh_sala_async(tambo, consulta_id, vivo=False)

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
