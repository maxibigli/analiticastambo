# -*- coding: utf-8 -*-
"""Proveedor de alimentación: MixerOne (https://mixerone.net).

El portal es una aplicación Angular que habla con una API REST con JSON y token. No hay documentación
pública: las rutas y los campos salen de leer el código de la propia página y se VERIFICARON contra la
cuenta de La Ponderosa el 09/11/2026 (los viajes del 07/11 coinciden con el Excel «viajes» que exporta el
portal: mismos horarios y mismos kg).

  - `POST auth/loginuser` con `{username, password}` → `response.access_token` y
    `response.associated_clients` (una cuenta puede tener VARIOS establecimientos; la de La Ponderosa
    tiene cuatro). Respuesta HTTP 201, no 200.
  - El token va como `Authorization: Bearer <token>` y el establecimiento como cabecera `clientid`.
  - `POST trips/GetByFilterPaginated` con `{startDate, endDate, page}` (fechas `AAAA-MM-DD`, ambas
    inclusive) → `response.trips` y `response.pagination`. Un VIAJE es una tanda del mixer: sus
    `details` son las cargas de ingredientes y las descargas a cada corral.
  - `POST corral/getbyfilter` (corrales con sus cabezas), `POST foods/getbyfilter` (alimentos con su
    %MS) y `POST device/getall` (equipos).

CREDENCIALES: variables `MIXERONE_USUARIO` y `MIXERONE_PASSWORD` (nunca en el código), compartidas por la
cuenta. Se leen del registro de Windows si el proceso no las tiene, como las de Milky.

ESTABLECIMIENTO POR TAMBO: `MIXERONE_CLIENTE_<TAMBO>` con el id del establecimiento (`MIXERONE_CLIENTE_PONDEROSA`
= 623), igual que `DELPRO_PWD_<TAMBO>`. Es OBLIGATORIA y nunca se adivina ni se cae a la de otro tambo: San José
también usa MixerOne, y sin esto vería el mixer de La Ponderosa y cruzaría sus grupos contra otro tambo. Un tambo
sin su variable informa el motivo (con los establecimientos de la cuenta) en vez de dar datos ajenos.

SOLO LECTURA, POR REGLA DEL TAMBO: el acceso a MixerOne es de solo lectura y está PROHIBIDO modificar nada ahí
(viajes, corrales, alimentos, precios, usuarios, equipos). Como la API usa POST hasta para consultar, el método HTTP no
frena nada: lo garantiza `RUTAS_DE_LECTURA`, una lista cerrada. `_post` rechaza cualquier ruta que no esté en ella ANTES
de salir a la red (ni siquiera hace el login). Para agregar una consulta nueva hay que sumar su ruta a esa lista a
propósito, verificando que solo lee; una ruta de la API que cree, edite, borre, habilite o recalcule algo no entra nunca.

LO QUE MIXERONE NO DA, y por eso va vacío en vez de inventado:
  - El mapeo corral ↔ grupo de DelPro (`indice_ordene`): se cae a coincidencia de nombre.
  - Los kg de materia seca por cabeza (`kg_ms_cabeza`) del corral.
  - Un precio por kg con unidad confiable: la API trae `price` por alimento (18 de 59 cargados) pero no se
    sabe si es por kg ni en qué moneda, así que `precio` es `None` y el valor crudo va en `precio_mixerone`.
    Un cero o un número de otra unidad se propagaría como costo real y mentiría.
"""
import datetime
import json
import threading
import time

import requests

NOMBRE = "MixerOne"
BASE = "https://mixerone.net/api/"

VAR_USUARIO = "MIXERONE_USUARIO"
VAR_PASSWORD = "MIXERONE_PASSWORD"
PREFIJO_CLIENTE = "MIXERONE_CLIENTE_"

TIMEOUT_S = 45
# El servidor devuelve 25 viajes por página (≈13 por día): cuatro meses son unas 65 páginas.
MAX_PAGINAS = 400

# Las ÚNICAS rutas que este módulo puede llamar. El login es un POST pero no cambia datos del tambo: abre sesión.
RUTAS_DE_LECTURA = frozenset({
    "auth/loginuser",
    "trips/GetByFilterPaginated",
    "corral/getbyfilter",
    "foods/getbyfilter",
    "device/getall",
    # El tablero principal del portal. Son lecturas: el portal las llama solas, sin tocar nada, al abrir la pantalla
    # de inicio (el método es POST sin cuerpo, como todo en esta API). Verificado el 18/11/2026.
    "dashboard/getCountersDashboard",          # vacas en ordeñe, producción individual, MS por cabeza, conversión
    "dashboard/getTripsDashboard",             # teórica / carga real / descarga real por día, con sus alertas
    "dashboard/getOperatorsScoreDashboard",    # puntaje de los operarios
    "dashboard/getConsumptionDailyDashboard",  # consumo diario por corral
})

# Las ÚNICAS rutas que se piden con GET (lectura de verdad: el método ya lo garantiza, pero la lista es igual de
# cerrada). Autorizada por el tambo el 18/11/2026.
RUTAS_GET_DE_LECTURA = frozenset({
    "notifications/alerts/daily-counts",       # cuántas alertas hubo cada día (los triángulos del gráfico de viajes)
    # El listado de alertas/notificaciones (con filtro por fechas). OJO: marcarlas como leídas es un PATCH a otra ruta
    # (`notifications/<id>/read`, `notifications/read-all`) y NUNCA se llama desde acá: leer no las marca.
    "notifications",
})

# La sesión se reusa: cada login son unos segundos y los lotes casi no cambian.
_TTL_SESION_S = 300
_cache: dict = {}
_lock = threading.Lock()

# Una descarga con una hora MUY anterior a la del inicio del viaje pasó la medianoche.
_SALTO_DE_DIA = datetime.timedelta(hours=6)


class MixerOneError(Exception):
    pass


def _variable(nombre: str):
    """Variable de entorno, o del registro de Windows si el proceso no la tiene (ver `cicla._variable`)."""
    import cicla
    return cicla._variable(nombre)


def var_cliente(tambo) -> str:
    """Nombre de la variable que dice qué establecimiento de MixerOne es de este tambo."""
    return PREFIJO_CLIENTE + str(tambo or "").upper()


def disponible(tambo=None) -> tuple[bool, str]:
    """(True, "") si hay credenciales (y, con `tambo`, su establecimiento); si no, (False, por qué)."""
    faltan = [v for v in (VAR_USUARIO, VAR_PASSWORD) if not _variable(v)]
    if faltan:
        return False, (
            f"Faltan las variables de entorno {' y '.join(faltan)} (ver el panel de despliegue). Si ya las "
            f"cargaste, los procesos que estaban abiertos pueden no verlas: reiniciá la aplicación.")
    if tambo is not None and not (_variable(var_cliente(tambo)) or "").strip():
        return False, (
            f"Falta la variable {var_cliente(tambo)} con el id del establecimiento de este tambo en MixerOne "
            f"(la cuenta puede tener varios). Sin eso no se sabe de qué mixer son los datos.")
    return True, ""


def _http() -> requests.Session:
    with _lock:
        s = _cache.get("http")
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = "LactIA/1.0"
            _cache["http"] = s
        return s


def _json(r, ruta: str):
    try:
        return json.loads(r.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise MixerOneError(f"MixerOne devolvió algo que no es JSON en {ruta}.")


def _sesion() -> tuple[str, list]:
    """(token, establecimientos de la cuenta). Cacheada unos minutos."""
    with _lock:
        guardado = _cache.get("sesion")
        if guardado and time.time() - guardado[0] < _TTL_SESION_S:
            return guardado[1], guardado[2]

    ok, motivo = disponible()
    if not ok:
        raise MixerOneError(motivo)
    try:
        r = _http().post(BASE + "auth/loginuser",
                         json={"username": _variable(VAR_USUARIO), "password": _variable(VAR_PASSWORD)},
                         headers={"Accept": "application/json"}, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        raise MixerOneError(f"No se pudo conectar con MixerOne: {exc}")
    if r.status_code not in (200, 201):
        raise MixerOneError(f"MixerOne rechazó el login (HTTP {r.status_code}). "
                            f"Revisá {VAR_USUARIO} / {VAR_PASSWORD}.")
    resp = (_json(r, "auth/loginuser") or {}).get("response") or {}
    token = resp.get("access_token")
    if not token:
        raise MixerOneError("MixerOne no devolvió token: usuario o contraseña incorrectos.")
    clientes = resp.get("associated_clients") or []
    with _lock:
        _cache["sesion"] = (time.time(), token, clientes)
    return token, clientes


def _cliente(clientes: list, tambo) -> str:
    """El establecimiento de este tambo, SIEMPRE el que dice su variable: nunca uno por defecto."""
    if tambo is None:
        raise MixerOneError("MixerOne necesita saber de qué tambo se piden los datos (usá `para(tambo)`).")
    var = var_cliente(tambo)
    elegido = (_variable(var) or "").strip()
    if not elegido:
        raise MixerOneError(
            f"Falta la variable {var} con el id del establecimiento de este tambo en MixerOne. "
            f"Opciones de la cuenta: {_opciones(clientes)}.")
    if elegido not in {str(c.get("id")) for c in clientes}:
        raise MixerOneError(
            f"{var}={elegido} no es un establecimiento de esta cuenta de MixerOne. "
            f"Opciones: {_opciones(clientes)}.")
    return elegido


def _opciones(clientes: list) -> str:
    return "; ".join(f"{c.get('id')} = {c.get('business_name') or c.get('name')}" for c in clientes) or "ninguna"


def _post(ruta: str, cuerpo: dict, tambo, _reintento: bool = True):
    """El `response` de la API. Si el token venció, vuelve a entrar UNA vez. Solo rutas de lectura."""
    if ruta not in RUTAS_DE_LECTURA:
        raise MixerOneError(f"MixerOne es de solo lectura: la ruta {ruta!r} no está permitida.")
    token, clientes = _sesion()
    cliente = _cliente(clientes, tambo)
    try:
        r = _http().post(BASE + ruta, json=cuerpo, timeout=TIMEOUT_S,
                         headers={"Authorization": f"Bearer {token}", "clientid": cliente,
                                  "Accept": "application/json"})
    except requests.RequestException as exc:
        raise MixerOneError(f"No se pudo consultar MixerOne ({ruta}): {exc}")
    if r.status_code == 401 and _reintento:
        with _lock:
            _cache.pop("sesion", None)
        return _post(ruta, cuerpo, tambo, _reintento=False)
    if r.status_code not in (200, 201):
        raise MixerOneError(f"MixerOne devolvió HTTP {r.status_code} en {ruta}.")
    datos = _json(r, ruta)
    if isinstance(datos, dict) and datos.get("status") is False:
        raise MixerOneError(f"MixerOne rechazó {ruta}: {datos.get('detail') or 'sin detalle'}.")
    return (datos or {}).get("response")


def _get(ruta: str, tambo, params: dict = None, _reintento: bool = True):
    """El `response` de un GET de lectura. Misma idea que `_post`: lista cerrada, se rechaza ANTES de salir a la red."""
    if ruta not in RUTAS_GET_DE_LECTURA:
        raise MixerOneError(f"MixerOne es de solo lectura: la ruta {ruta!r} no está permitida.")
    token, clientes = _sesion()
    cliente = _cliente(clientes, tambo)
    try:
        r = _http().get(BASE + ruta, params=params, timeout=TIMEOUT_S,
                        headers={"Authorization": f"Bearer {token}", "clientid": cliente,
                                 "Accept": "application/json"})
    except requests.RequestException as exc:
        raise MixerOneError(f"No se pudo consultar MixerOne ({ruta}): {exc}")
    if r.status_code == 401 and _reintento:
        with _lock:
            _cache.pop("sesion", None)
        return _get(ruta, tambo, params, _reintento=False)
    if r.status_code not in (200, 201):
        raise MixerOneError(f"MixerOne devolvió HTTP {r.status_code} en {ruta}.")
    datos = _json(r, ruta)
    if isinstance(datos, dict) and datos.get("status") is False:
        raise MixerOneError(f"MixerOne rechazó {ruta}: {datos.get('detail') or 'sin detalle'}.")
    return (datos or {}).get("response")


def equipos(tambo=None) -> list:
    """Los equipos (tablets del mixer, de la pala...) activos de la cuenta."""
    salida = []
    for e in _post("device/getall", {}, tambo) or []:
        if e.get("is_active") is False:
            continue
        salida.append({"serial": e.get("device_id"), "tipo": "MixerOne",
                       "alias": (e.get("alias") or e.get("fullname") or "").strip(),
                       "ultimo_dato": e.get("date_information_sent"), "_crudo": e})
    return salida


def lotes(tambo=None) -> list:
    """Los corrales activos, en el formato común de `proveedores` (un corral de MixerOne es un lote)."""
    datos = _post("corral/getbyfilter", {"is_active": True}, tambo) or {}
    salida = []
    for c in datos.get("corrals") or []:
        if c.get("deleted_at") or c.get("is_active") is False:
            continue
        manadas = [t for t in c.get("troops") or [] if not t.get("deleted_at") and t.get("is_active") is not False]
        cabezas = sum(int(t.get("quantity") or 0) for t in manadas)
        receta = c.get("recipe_name")
        salida.append({
            "lote": (c.get("name") or "").strip(),
            "id": c.get("id"),
            "cabezas": cabezas,
            "kg_ms_cabeza": None,
            "categoria": None,
            "categoria_id": manadas[0].get("category_id") if manadas else None,
            "indice_ordene": None,
            # Sin cabezas o sin receta asignada no se alimenta: es un corral configurado, no una boca.
            "activo": cabezas > 0 and bool(receta),
            "pct_alimentacion": None,
            "receta": receta,
            "orden": c.get("order"),
        })
    return salida


def ingredientes(tambo=None) -> list:
    """Los alimentos con su %MS. `precio` va siempre `None` (ver el encabezado del módulo)."""
    datos = _post("foods/getbyfilter", {"is_active": True}, tambo) or {}
    alimentos = datos.get("foods") or []
    total = datos.get("total")
    if isinstance(total, int) and total > len(alimentos):
        raise MixerOneError(f"MixerOne devolvió {len(alimentos)} de {total} alimentos: la lista está cortada.")
    salida = []
    for a in alimentos:
        precio = a.get("price")
        ms = a.get("dry_mass")
        salida.append({
            "nombre": (a.get("name") or "").strip(),
            "id": a.get("id"),
            # Acá el %MS ya viene en porcentaje (93), igual que `food_ms` en los viajes.
            "ms_pct": ms if isinstance(ms, (int, float)) else None,
            "precio": None,
            "precio_mixerone": precio if precio else None,
            "stock": a.get("stock"),
            "equipo": None,
        })
    return salida


def _dia(v) -> str:
    if isinstance(v, datetime.datetime):
        v = v.date()
    return v.isoformat() if isinstance(v, datetime.date) else str(v)[:10]


def _fecha(viaje: dict, hora: str):
    """Fecha y hora de un movimiento: el día del viaje con la hora del movimiento. Una descarga muy anterior
    a la hora de arranque del viaje pasó la medianoche."""
    creado = str(viaje.get("created") or "")
    try:
        inicio = datetime.datetime.fromisoformat(creado[:19])
        t = datetime.time.fromisoformat(str(hora)[:8])
    except ValueError:
        return None
    momento = datetime.datetime.combine(inicio.date(), t)
    if momento < inicio - _SALTO_DE_DIA:
        momento += datetime.timedelta(days=1)
    return momento.isoformat(timespec="seconds")


def _viajes(desde, hasta, tambo) -> list:
    viajes, pagina = [], 1
    while True:
        datos = _post("trips/GetByFilterPaginated",
                      {"startDate": _dia(desde), "endDate": _dia(hasta), "page": pagina}, tambo) or {}
        viajes.extend(datos.get("trips") or [])
        pag = datos.get("pagination") or {}
        if not pag.get("hasNextPage"):
            return viajes
        pagina += 1
        if pagina > MAX_PAGINAS:
            raise MixerOneError(f"MixerOne devolvió más de {MAX_PAGINAS} páginas de viajes: acotá el período.")


def consumos(desde, hasta, tambo=None) -> dict:
    """Descargas por corral y cargas por ingrediente entre dos fechas (inclusive).

    `descargas`: cada entrega del mixer a un corral, con los kg reales. `cargas`: cada ingrediente cargado,
    con su %MS del momento. `operacion` es el id del viaje: une las cargas de una tanda con sus descargas,
    que es lo que `alimentacion` usa para saber a qué receta pertenece cada descarga.
    """
    salida = {"descargas": [], "cargas": []}
    for v in _viajes(desde, hasta, tambo):
        operacion = v.get("trip_uuid")
        for d in v.get("details") or []:
            fecha = _fecha(v, d.get("hour"))
            if d.get("action") == "Descarga":
                salida["descargas"].append({
                    "fecha": fecha,
                    "lote": (d.get("corral") or "").strip() or None,
                    "lote_id": d.get("corral_id"),
                    "kg": d.get("kg_real") or 0,
                    "cabezas": d.get("heads"),
                    "kg_ms_cabeza": None,
                    "operacion": operacion,
                    "usuario": d.get("operator"),
                    "equipo": d.get("device_id"),
                    # Lo que MixerOne sabe del viaje y Haasten no: la etapa («Ordeñe», «Secas», «Recría») y la
                    # hora a la que estaba PREVISTA la comida. El diagnóstico de producción los usa.
                    "etapa": (v.get("growth_stage") or "").strip() or None,
                    "ciclo": (d.get("feedCycle") or "").strip() or None,
                })
            elif d.get("action") == "Carga":
                ms = d.get("food_ms")
                salida["cargas"].append({
                    "fecha": fecha,
                    "ingrediente": (d.get("food") or "").strip() or None,
                    "receta": (d.get("recipe") or "").strip() or None,
                    "kg": d.get("kg_real") or 0,
                    "ms_pct": ms if isinstance(ms, (int, float)) else None,
                    "precio": None,
                    "operacion": operacion,
                    "usuario": d.get("operator"),
                    "equipo": d.get("device_id"),
                    "etapa": (v.get("growth_stage") or "").strip() or None,
                })
    return salida


def tablero(tambo=None) -> dict:
    """El tablero principal del portal, en la forma neutral de `proveedores` (ver su docstring): tarjetas, kg por
    día de los viajes con sus alertas, y puntaje de los operarios.

    Cada bloque se pide aparte y uno que falla no tira a los demás: va en `avisos` y ese bloque queda en None
    (o, en las alertas, sin dato por día). Solo si NO se pudo leer ninguno es un error.

    Las cifras de las tarjetas (`vacas`, `leche_vaca`, `ms_vaca`, `conversion`) las calcula MixerOne con las cabezas que el
    tambo carga a mano en cada corral: se informan tal cual, rotuladas como suyas, y no se recalculan acá.
    """
    avisos = []

    def leer(etiqueta, pedir):
        try:
            return pedir()
        except MixerOneError as exc:
            avisos.append(f"{etiqueta}: {exc}")
            return None

    contadores = leer("Las tarjetas", lambda: _post("dashboard/getCountersDashboard", None, tambo))
    viajes = leer("El listado de viajes", lambda: _post("dashboard/getTripsDashboard", None, tambo))
    puntaje = leer("El puntaje de operarios", lambda: _post("dashboard/getOperatorsScoreDashboard", None, tambo))
    alertas = leer("Las alertas por día", lambda: _get("notifications/alerts/daily-counts", tambo))
    if contadores is None and viajes is None and puntaje is None:
        raise MixerOneError("MixerOne no devolvió el tablero. " + " ".join(avisos))

    por_dia: dict = {}
    for v in viajes or []:
        dia = str(v.get("created") or "")[:10]
        if not dia:
            continue
        x = por_dia.setdefault(dia, {"teorica": 0.0, "carga_real": 0.0, "descarga_real": 0.0})
        x["teorica"] += v.get("total_kg_theoretical_load") or 0
        x["carga_real"] += (v.get("Carga") or {}).get("Real") or 0
        x["descarga_real"] += (v.get("Descarga") or {}).get("Real") or 0
    cuenta = None if alertas is None else {str(a.get("date") or "")[:10]: a.get("count") or 0 for a in alertas}
    dias = [{"fecha": d, **{k: round(v, 2) for k, v in x.items()},
             "alertas": None if cuenta is None else cuenta.get(d, 0)} for d, x in sorted(por_dia.items())]

    operarios = None
    if puntaje is not None:
        operarios = {
            "puntaje": puntaje.get("score_final"),
            "porcentajes": puntaje.get("score_percentages") or {},
            "lista": [{"nombre": o.get("name"), "puntaje": (o.get("general") or {}).get("score"),
                       "resultado": (o.get("general") or {}).get("score_result"),
                       "cargas": (o.get("loads") or {}).get("total"),
                       "descargas": (o.get("unloads") or {}).get("total")} for o in puntaje.get("operators") or []],
        }
    return {
        "proveedor": NOMBRE,
        "kpis": None if contadores is None else {
            "vacas": contadores.get("milkingCows"), "leche_vaca": contadores.get("generalAverage"),
            "ms_vaca": contadores.get("msRealHead"), "conversion": contadores.get("conversion")},
        "viajes": dias,
        "operarios": operarios,
        "avisos": avisos,
    }


# MixerOne manda los instantes en UTC («...Z»). Argentina no tiene horario de verano: UTC-3 todo el año. Con esta
# conversión el conteo de alertas por día coincide al número con el que muestra el portal.
_UTC_ARGENTINA = datetime.timedelta(hours=-3)

# Qué ACCIÓN del operario dispara cada tipo de alerta (ver `operarios.py`). Lo que no figura acá (stock, variación de
# consumo de un rodeo, rodeo sin alimentar) NO se atribuye a una persona: es de la gestión del tambo.
_ACCION_DE_ALERTA = {"N06": "carga",         # tiempo de carga menor al límite: quien cargó ese insumo
                     "N04": "descarga",      # descarga fuera del corral (GPS): quien descargó en ese rodeo
                     "N07": "descarga",      # horario de alimentación inestable: idem
                     "N11": "descarga",      # viaje malo por puntuación de descarga
                     "N02": "edicion"}       # alguien modificó los kg de un corral: el operario viene en el mensaje


def _local(utc_iso: str):
    return datetime.datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00")).replace(tzinfo=None) + _UTC_ARGENTINA


def _alerta_neutra(x: dict, crudo: bool = False) -> dict:
    md = x.get("metadata") or {}
    codigo = x.get("code")
    accion, operario = _ACCION_DE_ALERTA.get(codigo), None
    if accion == "edicion":
        mensaje = x.get("message") or ""
        operario = mensaje.split(" modificó")[0].strip() if " modificó" in mensaje else None
        accion = "edicion" if operario else None
    momento = _local(x.get("occurred_at") or x.get("created_at"))
    neutra = {"id": x.get("id"), "codigo": codigo, "tipo": x.get("title"), "severidad": x.get("severity"),
              "mensaje": x.get("message"), "fecha": momento.date().isoformat(), "hora": momento.strftime("%H:%M"),
              "viaje": md.get("trip_uuid") or md.get("detected_trip_uuid"), "rodeo": md.get("corral_name"),
              "insumo": md.get("item_name"), "accion": accion, "operario": operario}
    if crudo:
        neutra["crudo"] = x          # el JSON original completo, para la copia local (`alertas_guardadas.py`)
    return neutra


def alertas(desde, hasta, tambo=None, crudo: bool = False) -> list:
    """Las alertas que detectó MixerOne entre dos fechas (inclusive), en la forma neutral de `operarios.py`.
    Con `crudo=True` cada una trae además el JSON original de MixerOne (clave `crudo`).

    Solo LEE el listado (`GET notifications`): marcarlas como leídas es un PATCH a otra ruta y no se llama nunca."""
    salida, pagina = [], 1
    while True:
        r = _get("notifications", tambo, {
            "page": pagina, "limit": 100, "grouped": "true", "unread_only": "false", "is_alert": "true",
            "start_date": _dia(desde), "end_date": _dia(hasta)}) or {}
        salida.extend(_alerta_neutra(x, crudo) for x in r.get("items") or [])
        if pagina >= (r.get("page_count") or 1):
            return salida
        pagina += 1
        if pagina > MAX_PAGINAS:
            raise MixerOneError(f"MixerOne devolvió más de {MAX_PAGINAS} páginas de alertas: acotá el período.")


def operarios(tambo=None) -> dict:
    """Los operarios del mixer analizados junto con las alertas que detecta MixerOne (ver `operarios.analizar`).

    La ventana es la del tablero del portal (los viajes que devuelve `dashboard/getTripsDashboard`, unos 15 días): el
    puntaje que da MixerOne es de ESA ventana y mezclarlo con otra daría números que no se pueden comparar. Cada bloque
    se pide aparte: uno que falla va a `avisos` y los otros siguen; solo si no hay ni viajes ni puntaje es un error."""
    import operarios as analisis
    avisos = []

    def leer(etiqueta, pedir):
        try:
            return pedir()
        except MixerOneError as exc:
            avisos.append(f"{etiqueta}: {exc}")
            return None

    viajes = leer("El listado de viajes", lambda: _post("dashboard/getTripsDashboard", None, tambo))
    puntaje = leer("El puntaje de operarios", lambda: _post("dashboard/getOperatorsScoreDashboard", None, tambo))
    if viajes is None and puntaje is None:
        raise MixerOneError("MixerOne no devolvió los viajes ni el puntaje de operarios. " + " ".join(avisos))

    movimientos = []
    for v in viajes or []:
        for d in v.get("details") or []:
            accion = {"Carga": "carga", "Descarga": "descarga"}.get(d.get("action"))
            if accion and d.get("operator"):
                movimientos.append({"viaje": v.get("trip_uuid"), "accion": accion, "operario": d["operator"],
                                    "ingrediente": (d.get("food") or "").strip() or None,
                                    "rodeo": (d.get("corral") or "").strip() or None,
                                    "kg_real": d.get("kg_real"), "kg_teorico": d.get("kg_theoretical")})
    fechas = sorted(str(v.get("created") or "")[:10] for v in viajes or [] if v.get("created"))
    ventana = {"desde": fechas[0] if fechas else None, "hasta": fechas[-1] if fechas else None}
    lista = []
    if fechas:
        lista = leer("Las alertas", lambda: alertas(datetime.date.fromisoformat(fechas[0]),
                                                    datetime.date.fromisoformat(fechas[-1]), tambo)) or []
    comidas = []
    if fechas:
        def primera_comida():
            import alimentacion_dia
            desde = datetime.date.fromisoformat(fechas[0])
            c = consumos(desde - datetime.timedelta(days=alimentacion_dia.HISTORIA_DIAS),
                         datetime.date.fromisoformat(fechas[-1]), tambo)
            return alimentacion_dia.primeras_con_operario(c, desde=fechas[0])
        comidas = leer("La primera comida de cada día", primera_comida) or []
    puntajes = None
    if puntaje is not None:
        puntajes = {"puntaje": puntaje.get("score_final"), "porcentajes": puntaje.get("score_percentages") or {},
                    "operarios": {o.get("name"): {"general": (o.get("general") or {}).get("score"),
                                                  "cargas": (o.get("loads") or {}).get("score"),
                                                  "descargas": (o.get("unloads") or {}).get("score")}
                                  for o in puntaje.get("operators") or []}}
    return {**analisis.analizar(movimientos, lista, puntajes), "proveedor": NOMBRE, "ventana": ventana,
            "primeras_comidas": comidas, "avisos": avisos}


class _DeUnTambo:
    """El proveedor ligado a UN tambo: tiene la misma interfaz que cualquier módulo de `proveedores`
    (`NOMBRE`, `disponible()`, `equipos()`, `lotes()`, `ingredientes()`, `consumos(desde, hasta)`), pero cada
    llamada va al establecimiento de ESE tambo."""

    NOMBRE = NOMBRE

    def __init__(self, tambo: str):
        self.tambo = tambo

    def disponible(self):
        return disponible(self.tambo)

    def equipos(self):
        return equipos(self.tambo)

    def lotes(self):
        return lotes(self.tambo)

    def ingredientes(self):
        return ingredientes(self.tambo)

    def consumos(self, desde, hasta):
        return consumos(desde, hasta, self.tambo)

    def tablero(self):
        return tablero(self.tambo)

    def operarios(self):
        return operarios(self.tambo)

    def alertas(self, desde, hasta, crudo=False):
        return alertas(desde, hasta, self.tambo, crudo)


def para(tambo: str) -> _DeUnTambo:
    return _DeUnTambo(tambo)
