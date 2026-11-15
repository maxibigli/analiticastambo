# -*- coding: utf-8 -*-
"""Vigilante de LactIA: una pasada liviana cada ~15 minutos que mira cada ordeño apenas termina y, SOLO si salió
claramente peor que lo normal (o se cortaron los datos), busca la causa y manda UN aviso por los canales actuales.

Las reglas están en `vigilante_reglas.py` (puras), el estado y la configuración en `vigilante_estado.py`, el texto
en `vigilante_aviso.py`. Acá se juntan: leer los datos, armar la base de cada ordeño, decidir qué avisar, respetar
el silencio y el tope, enviar y recién entonces registrar.

Reglas que no se negocian:
* SOLO LECTURA sobre DelPro y MixerOne.
* Un aviso no se registra hasta que el envío salió: si el canal falla, se reintenta en la pasada siguiente.
* En horario silencioso no se registra nada: lo pendiente espera a que termine el silencio.
* `registrar=False` («Probar») calcula y devuelve lo mismo, sin escribir en el estado ni mandar nada.
* Una fuente que falla es una advertencia en el aviso, nunca corta las demás ni el hilo.
"""
import datetime
import logging
import statistics as st
import threading
import time
from collections import defaultdict

import diagnostico_baja
import vigilante_aviso
import vigilante_estado
import vigilante_reglas as reglas
from vigilante_estado import Estado

log = logging.getLogger("vigilante")

DIAS_BASE = 7                          # los 7 días anteriores del mismo número de ordeño
DIAS_LECTURA = DIAS_BASE + 1           # hoy menos 8 días: alcanza para la base de un ordeño de ayer
COMPLETO_MIN = 0.9                     # un ordeño de la base con menos del 90 % de las vacas habituales es un día cortado
PENDIENTE_H = 12                       # un ordeño que terminó hace más de esto ya no se avisa (pasó el momento)
HABITUAL_PCT = 0.5                     # «vaca habitual» de un ordeño: estuvo en al menos la mitad de los de la base
PRESENTES_MIN = 0.9                    # si la lectura por vaca trae menos del 90 % de las vacas del ordeño, no se opina
BOCA_RATIO = 0.6                       # boca que rindió menos del 60 % de sus ordeños habituales (mismo criterio que diagnostico_baja)
MIN_ORDENOS_BOCA = 3                   # una boca con menos ordeños que esto en la base no tiene «lo habitual»
MEMO_VACAS_S = 2 * 3600                # más que la pasada (15 min): el mismo ordeño no vuelve a leer ~37.000 filas por vaca
MIN_DIAS_SESION = 4                    # un número de ordeño que no se hizo al menos 4 de los últimos 7 días no cuenta para «sin datos»
ESPERA_INCOMPLETO_MIN = 120            # un ordeño con menos del 90 % de las vacas habituales espera esto antes de avisarse
MEMO_COMIDA_S = 1800
MAX_CAUSAS_RODEOS = 3
CON_AVISO_DEMORA = False               # se pasa a True si la medición de `medir_retraso_ordenos.py` muestra demora


class LecturaError(Exception):
    """No se pudo leer DelPro: el mensaje es para el log y para «Probar»."""


# --------------------------------------------------------------------------- memo (evita repetir consultas pesadas)
_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()


def olvidar_memo() -> None:
    with _MEMO_LOCK:
        _MEMO.clear()


def _memo(clave, ttl_s, fn):
    ahora = time.monotonic()
    with _MEMO_LOCK:
        hit = _MEMO.get(clave)
    if hit and ahora - hit[0] < ttl_s:
        return hit[1]
    valor = fn()
    with _MEMO_LOCK:
        for k in [k for k, (t, _v) in _MEMO.items() if ahora - t >= MEMO_VACAS_S]:
            del _MEMO[k]                                       # lo vencido sale: las claves llevan fechas
        _MEMO[clave] = (ahora, valor)
    return valor


# --------------------------------------------------------------------------- lectura de DelPro (solo SELECT)
def _corre(sql: str, tambo: str):
    """Filas de la consulta o `LecturaError`. `diagnostico_baja.corre` escribe sus avisos en un buffer por hilo que
    hay que inicializar acá: este hilo no pasó por `generar`."""
    diagnostico_baja._salida.lineas = []
    filas = diagnostico_baja.corre(sql, tambo)
    if filas is None:
        detalle = " ".join(diagnostico_baja._salida.lineas).strip() or "sin detalle"
        raise LecturaError(f"No se pudo leer DelPro {detalle}")
    return filas


def leer_ordenos(tambo: str, desde: datetime.date, hasta: datetime.date) -> list:
    """Los ordeños (vacas, kg, inicio, fin) de `desde` a `hasta`, uno por (día en que EMPEZÓ, número de ordeño).
    Si un mismo ordeño viene partido en dos filas por el corte de `AnimalDaily.Date`, se juntan."""
    filas = _corre(diagnostico_baja.sql_sesiones(desde.isoformat(), hasta.isoformat(), tambo), tambo)
    acum = {}
    for _fecha, sesion, vacas, kg, inicio, fin, *_resto in filas:
        ini, fi = diagnostico_baja._dt(inicio), diagnostico_baja._dt(fin)
        if sesion is None or ini is None or fi is None:
            continue
        clave = (ini.date(), int(sesion))
        if clave in acum:
            a = acum[clave]
            a[0] += int(vacas or 0)
            a[1] += float(kg or 0)
            a[2], a[3] = min(a[2], ini), max(a[3], fi)
        else:
            acum[clave] = [int(vacas or 0), float(kg or 0), ini, fi]
    ordenos = [reglas.armar_ordeno(ses, v, kg, ini, fi) for (_d, ses), (v, kg, ini, fi) in acum.items()]
    return sorted(ordenos, key=lambda o: (o.inicio, o.sesion))


def sql_vacas_por_ordeno(d0: str, d1: str, tambo: str) -> str:
    """kg de cada vaca identificada en cada ordeño de cada día (para saber qué vacas habituales faltaron)."""
    filtro = diagnostico_baja._del_rebano(tambo)
    desde_t = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=1)).isoformat()
    hasta_t = (datetime.date.fromisoformat(d1) + datetime.timedelta(days=2)).isoformat()
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha, sm.SessionNo AS sesion, b.Number AS numero, ad.AnimalGroup AS grupo,
           SUM(sm.TotalYield) AS kg
    FROM SessionMilkYield sm
    JOIN AnimalDaily ad ON ad.OID = sm.AnimalDaily AND ad.GCRecord IS NULL
    JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE sm.BeginTime >= '{desde_t}' AND sm.BeginTime < '{hasta_t}'
      AND ad.Date >= '{d0}' AND ad.Date <= '{d1}' AND sm.TotalYield > 0 AND b.Number > 0 AND {filtro}
    GROUP BY ad.Date, sm.SessionNo, b.Number, ad.AnimalGroup
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_bocas_por_ordeno(d0: str, d1: str, tambo: str) -> str:
    """Cuántas vacas pasaron por cada unidad de ordeñe (MilkingDevice) en cada ordeño de cada día."""
    filtro = diagnostico_baja._del_rebano(tambo)
    desde_t = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=1)).isoformat()
    hasta_t = (datetime.date.fromisoformat(d1) + datetime.timedelta(days=2)).isoformat()
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha, sm.SessionNo AS sesion, sm.MilkingDevice AS boca,
           COUNT(*) AS vacas, SUM(sm.TotalYield) AS kg
    FROM SessionMilkYield sm
    JOIN AnimalDaily ad ON ad.OID = sm.AnimalDaily AND ad.GCRecord IS NULL
    WHERE sm.BeginTime >= '{desde_t}' AND sm.BeginTime < '{hasta_t}'
      AND ad.Date >= '{d0}' AND ad.Date <= '{d1}' AND sm.TotalYield > 0 AND {filtro}
    GROUP BY ad.Date, sm.SessionNo, sm.MilkingDevice
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def leer_vacas(tambo: str, d0: str, d1: str) -> list:
    """`[(fecha_iso, sesion, numero, grupo, kg)]`. Memoizada: el mismo ordeño se mira varias pasadas seguidas."""
    def traer():
        return [(str(f)[:10], int(s), int(n), g, float(kg or 0))
                for f, s, n, g, kg in _corre(sql_vacas_por_ordeno(d0, d1, tambo), tambo)]
    return _memo(("vacas", tambo, d0, d1), MEMO_VACAS_S, traer)


def _leer_bocas(tambo: str, d0: str, d1: str) -> list:
    def traer():
        return [(str(f)[:10], int(s), b, int(n)) for f, s, b, n, _kg in _corre(sql_bocas_por_ordeno(d0, d1, tambo), tambo)]
    return _memo(("bocas", tambo, d0, d1), MEMO_VACAS_S, traer)


def comida_de_hoy(tambo: str, hoy: datetime.date):
    """`alimentacion_dia.ultima_comida` del proveedor de mixer del tambo, o `None` si no se puede (sin proveedor,
    sin historia, MixerOne caído): la comida es una condición más, nunca la que tumba la pasada."""
    def traer():
        import alimentacion_dia
        import proveedores
        r = alimentacion_dia.desde_el_proveedor(proveedores.de(tambo), hoy)
        return r.get("comida")
    try:
        return _memo(("comida", tambo, hoy.isoformat()), MEMO_COMIDA_S, traer)
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante: no se pudo leer la comida del mixer (%s)", exc)
        return None


# --------------------------------------------------------------------------- base de cada ordeño
def base_de(ordeno: reglas.Ordeno, historial: list) -> list:
    """Los ordeños del MISMO número de ordeño de los `DIAS_BASE` días anteriores al de `ordeno` (nunca el propio día),
    sin los cortados: uno con menos del 90 % de las vacas habituales es un día a medio cargar y tiraría la base para
    abajo (mismo criterio que `diagnostico_baja`)."""
    f0 = datetime.date.fromisoformat(ordeno.fecha)
    cand = [o for o in historial
            if o.sesion == ordeno.sesion and 1 <= (f0 - datetime.date.fromisoformat(o.fecha)).days <= DIAS_BASE]
    if not cand:
        return []
    med = st.median(o.vacas for o in cand)
    return [o for o in cand if o.vacas >= COMPLETO_MIN * med]


def _aperturas(ordenos: list, hoy: datetime.date, cfg: dict) -> list:
    """La hora en que suele EMPEZAR cada número de ordeño (la más temprana de los últimos 7 días, antes de hoy).
    Un número de ordeño que se hizo menos de `MIN_DIAS_SESION` de esos días no cuenta: no es un ordeño habitual y
    marcaría «sin datos» todos los días en que no se hace. Sin historia, abre cuando termina el horario silencioso."""
    dias_y_horas = defaultdict(list)
    for o in ordenos:
        d = (hoy - datetime.date.fromisoformat(o.fecha)).days
        if 1 <= d <= DIAS_BASE:
            dias_y_horas[o.sesion].append((d, o.inicio.hour))
    horas = {min(h for _d, h in v) for v in dias_y_horas.values() if len({d for d, _h in v}) >= MIN_DIAS_SESION}
    return sorted(horas) or [int(cfg["silencio_hasta"][:2])]


def _ventanas_de_ordeno(aperturas: list, cfg: dict) -> list:
    """Ventanas `(abre, cierra)` de `reglas.sin_datos`: desde que abre cada ordeño hasta `sin_datos_horas` + 1 h
    después. Sirven para el corte A MITAD de un ordeño; el que se ve desde que abre es `sin_datos_desde_apertura`.
    Una sola ventana de punta a punta del día marcaría «sin datos» en el hueco normal entre dos ordeños."""
    ventana_h = int(-(-cfg["sin_datos_horas"] // 1)) + 1
    return [(h, min(24, h + ventana_h)) for h in aperturas]


# --------------------------------------------------------------------------- las condiciones de producción
def _faltantes(tambo, o, base, leer_vacas_fn):
    """`reglas.vacas_faltantes` para este ordeño, o `None`. Solo se consulta por vaca si el ordeño ya salió al menos
    1 % por debajo de la base: las ausentes pesan >= 1 % de los kg de la base, y con el ordeño por encima de eso lo
    que faltó lo cubrieron otras vacas (no «salió peor»). Si la lectura no trae a las vacas del ordeño, no se opina."""
    if len(base) < reglas.MIN_BASE:
        return None
    kg_base = st.mean(b.kg for b in base)
    if kg_base <= 0 or (o.kg - kg_base) / kg_base > -reglas.PCT_VACAS_AUSENTES:
        return None
    d0 = min(b.fecha for b in base)
    filas = leer_vacas_fn(tambo, d0, o.fecha)
    presentes = {n for f, s, n, _g, _kg in filas if f == o.fecha and s == o.sesion}
    if len(presentes) < PRESENTES_MIN * o.vacas:
        return None
    dias_base = {b.fecha for b in base}
    kgs = defaultdict(list)
    for f, s, n, _g, kg in filas:
        if s == o.sesion and f in dias_base:
            kgs[n].append(kg)
    minimo = max(1, int(HABITUAL_PCT * len(base) + 0.999))
    habituales = {n: st.mean(v) for n, v in kgs.items() if len(v) >= minimo}
    return reglas.vacas_faltantes(presentes, habituales, kg_base)


def _evaluar_ordeno(tambo, o, historial, leer_vacas_fn, ya_avisado=None) -> list:
    """Las condiciones de producción de un ordeño terminado. Lo que ya se avisó (`ya_avisado(clave)`) no se vuelve a
    evaluar: la lectura por vaca es la consulta pesada y no puede repetirse cada pasada por 12 horas."""
    ya = ya_avisado or (lambda _clave: False)
    base = base_de(o, historial)
    hallazgos = []
    baja = None if ya(f"{o.fecha}|{o.sesion}|baja_ordeno") else reglas.baja_ordeno(o, base)
    if baja:
        hallazgos.append(_hallazgo("baja_ordeno", o, baja))
    try:
        falt = None if ya(f"{o.fecha}|{o.sesion}|vacas_faltantes") else _faltantes(tambo, o, base, leer_vacas_fn)
    except Exception as exc:  # noqa: BLE001 - una lectura rota no tumba la baja ni la pasada
        log.warning("vigilante: no se pudieron revisar las vacas faltantes (%s)", exc)
        falt = None
    if falt:
        hallazgos.append(_hallazgo("vacas_faltantes", o, falt))
    return hallazgos


def _hallazgo(tipo: str, o: reglas.Ordeno, datos: dict) -> dict:
    return {"tipo": tipo, "clave": f"{o.fecha}|{o.sesion}|{tipo}",
            "datos": {**datos, "fecha": o.fecha, "sesion": o.sesion}, "causas": []}


# --------------------------------------------------------------------------- causas (la consulta pesada, solo si algo se disparó)
def _hh(minutos) -> str:
    return vigilante_aviso._hhmm(minutos)


def buscar_causas(tambo: str, ordeno: reglas.Ordeno, historial: list) -> list:
    """Frases ya medidas, de más a menos peso: vacas y rodeos que faltaron, bocas casi sin leche, ITH, comida del
    mixer. Cada causa va en su propio try/except y la que no se puede medir se omite (no se inventa)."""
    base = base_de(ordeno, historial)
    causas = []
    for f in (_causa_rodeos, _causa_bocas, _causa_ith, _causa_comida):
        try:
            causas += f(tambo, ordeno, base)
        except Exception as exc:  # noqa: BLE001
            log.warning("vigilante: %s falló (%s)", f.__name__, exc)
    return causas


def _causa_rodeos(tambo, o, base):
    if len(base) < reglas.MIN_BASE:
        return []
    filas = leer_vacas(tambo, min(b.fecha for b in base), o.fecha)
    presentes = {n for f, s, n, _g, _kg in filas if f == o.fecha and s == o.sesion}
    dias_base = {b.fecha for b in base}
    kgs, grupo = defaultdict(list), {}
    for f, s, n, g, kg in filas:
        if s == o.sesion and f in dias_base:
            kgs[n].append(kg)
            grupo[n] = g
    minimo = max(1, int(HABITUAL_PCT * len(base) + 0.999))
    ausentes = [(n, st.mean(v)) for n, v in kgs.items() if len(v) >= minimo and n not in presentes]
    if not ausentes or len(presentes) < PRESENTES_MIN * o.vacas:
        return []
    por_grupo = defaultdict(lambda: [0, 0.0])
    for n, kg in ausentes:
        por_grupo[grupo[n]][0] += 1
        por_grupo[grupo[n]][1] += kg
    nombres, _ok = diagnostico_baja._nombres_de_rodeos(tambo, set(por_grupo))
    frases = []
    for g, (n, kg) in sorted(por_grupo.items(), key=lambda kv: -kv[1][1])[:MAX_CAUSAS_RODEOS]:
        nombre = (nombres.get(g) or (None, None))[0] or f"Rodeo (id {g})"
        frases.append(f"{nombre}: faltaron {n} vacas habituales ({vigilante_aviso._n(kg)} kg)")
    return frases


def _causa_bocas(tambo, o, base):
    if len(base) < reglas.MIN_BASE:
        return []
    filas = _leer_bocas(tambo, min(b.fecha for b in base), o.fecha)
    dias_base = {b.fecha for b in base}
    hoy, hist = {}, defaultdict(list)
    for f, s, boca, n in filas:
        if s != o.sesion:
            continue
        if f == o.fecha:
            hoy[boca] = n
        elif f in dias_base:
            hist[boca].append(n)
    malas = []
    for boca, lst in hist.items():
        habitual = st.mean(lst)
        if len(lst) >= MIN_ORDENOS_BOCA and habitual > 0 and hoy.get(boca, 0) / habitual < BOCA_RATIO:
            malas.append((hoy.get(boca, 0) / habitual, boca, hoy.get(boca, 0), habitual))
    return [f"Boca {b}: ordeñó {n} vacas contra {vigilante_aviso._n(h)} habituales"
            for _r, b, n, h in sorted(malas)[:3]]


def _causa_ith(tambo, o, base):
    import veredicto_baja
    dias = [o.fecha] + [b.fecha for b in base]
    ith = diagnostico_baja.ith_de(dias)
    hoy = (ith.get(o.fecha) or {}).get("max")
    previos = [ith[b.fecha]["max"] for b in base if (ith.get(b.fecha) or {}).get("max") is not None]
    if hoy is None or not previos:
        return []
    media = st.mean(previos)
    if hoy >= veredicto_baja.ITH_ESTRES and hoy - media >= veredicto_baja.ITH_SALTO:
        return [f"Calor: ITH máximo de {vigilante_aviso._n(hoy, 1)} contra {vigilante_aviso._n(media, 1)} en los días de base"]
    return []


def _causa_comida(tambo, o, base):
    r = comida_de_hoy(tambo, datetime.date.fromisoformat(o.fecha))
    c = reglas.comida_atrasada(r, datetime.date.fromisoformat(o.fecha))
    if not c:
        return []
    return [f"La primera comida del mixer fue a las {_hh(c['primera'])} (lo habitual es {_hh(c['habitual'])})"]


# --------------------------------------------------------------------------- la pasada
def _resultado(estado, hallazgos=None, mensaje=None, enviado=False, retenidos=0, **extra) -> dict:
    return {"estado": estado, "hallazgos": hallazgos or [], "mensaje": mensaje, "enviado": enviado,
            "retenidos": retenidos, **extra}


def pasada(tambo: str, ahora: datetime.datetime | None = None, registrar: bool = True, enviar=None,
           estado: Estado | None = None, leer=None, causas=None, cfg: dict | None = None,
           comida=None, vacas=None, redactar=None) -> dict:
    """Una revisión completa. Devuelve `{"estado": "apagado"|"ok"|"sin_datos"|"silencio"|"error", "hallazgos",
    "mensaje", "enviado", "retenidos"}`.

    `registrar=False` es «Probar»: calcula lo mismo pero no escribe en `Estado` ni envía (y funciona con el vigilante
    apagado, para poder ver qué haría). `cfg`, `leer`, `causas`, `comida`, `vacas` y `redactar` se inyectan en las
    pruebas; en producción son la configuración, DelPro, el mixer y la IA reales."""
    ahora = ahora or datetime.datetime.now()
    cfg = cfg or vigilante_estado.config()
    if registrar and not cfg["activo"]:
        return _resultado("apagado")
    estado = estado or Estado(vigilante_estado.RUTA_DB)
    silencio = vigilante_estado.en_silencio(ahora, cfg)
    if registrar and silencio:
        # Lo que pase queda esperando a que termine el silencio: buscar causas y llamar a la IA ahora serían consultas
        # pesadas y llamadas pagas, repetidas cada pasada, para un mensaje que no se puede mandar.
        estado.set_ultima_pasada(ahora, "silencio")
        return _resultado("silencio")
    leer, causas, comida, vacas = leer or leer_ordenos, causas or buscar_causas, comida or comida_de_hoy, vacas or leer_vacas
    redactar = redactar or vigilante_aviso.redactar
    hoy = ahora.date()
    try:
        ordenos = leer(tambo, hoy - datetime.timedelta(days=DIAS_LECTURA), hoy)
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante: no se pudieron leer los ordeños (%s)", exc)
        if registrar:
            estado.set_ultima_pasada(ahora, "error")
        return _resultado("error", error=f"{type(exc).__name__}: {exc}")

    hallazgos = []
    en_sin_datos = _evaluar_datos(ordenos, ahora, hoy, cfg, estado, hallazgos)
    if not en_sin_datos:
        hallazgos += _evaluar_produccion(tambo, ordenos, ahora, estado, causas, comida, vacas)

    retener, enviar_ahora = [], []
    prod = [h for h in hallazgos if h["tipo"] in vigilante_aviso.PRODUCCION]
    tope_lleno = estado.avisos_del_dia(hoy) >= cfg["tope_dia"]
    for h in hallazgos:
        (retener if tope_lleno and h in prod else enviar_ahora).append(h)

    mensaje = redactar(enviar_ahora, ahora, con_aviso_demora=CON_AVISO_DEMORA) if enviar_ahora else None
    base_res = dict(hallazgos=enviar_ahora + retener, mensaje=mensaje, retenidos=len(retener))
    est = "sin_datos" if en_sin_datos else "ok"
    if silencio:                                       # solo «Probar» llega acá: muestra el mensaje sin mandarlo
        return _resultado("silencio", **base_res)
    if not registrar:
        return _resultado(est, **base_res)

    try:
        if mensaje and enviar:
            enviar(mensaje)
        enviado = bool(mensaje and enviar)
        if enviado or not enviar_ahora:
            _registrar(estado, enviar_ahora if enviado else [], retener, ahora, hoy)
    except Exception as exc:  # noqa: BLE001 - el canal falló: nada se registra, la próxima pasada reintenta
        log.warning("vigilante: no se pudo enviar el aviso (%s)", exc)
        estado.set_ultima_pasada(ahora, "error")
        return _resultado("error", error=f"{type(exc).__name__}: {exc}", **base_res)
    estado.set_ultima_pasada(ahora, est)
    return _resultado(est, enviado=enviado, **base_res)


def _evaluar_datos(ordenos, ahora, hoy, cfg, estado, hallazgos) -> bool:
    """Agrega `sin_datos` / `datos_vuelven` y dice si la producción NO se analiza (los datos están cortados)."""
    ultima = max((o.fin for o in ordenos), default=None)
    horas = cfg["sin_datos_horas"]
    aperturas = _aperturas(ordenos, hoy, cfg)
    cortado = (any(reglas.sin_datos(ultima, ahora, horas, v) for v in _ventanas_de_ordeno(aperturas, cfg))
               or reglas.sin_datos_desde_apertura(ultima, ahora, horas, aperturas))
    previo = estado.estado_datos()
    if cortado:
        if previo == "ok":
            hallazgos.append({"tipo": "sin_datos", "clave": "sin_datos", "causas": [],
                              "datos": {"ultima_fila": ultima.isoformat() if ultima else None, "horas": horas}})
        return True
    if previo == "sin_datos":
        if ultima is not None and ahora - ultima < datetime.timedelta(hours=horas):
            hallazgos.append({"tipo": "datos_vuelven", "clave": "datos_vuelven", "causas": [],
                              "datos": {"ultima_fila": ultima.isoformat()}})
            return False
        return True                          # sigue cortado (fuera de la ventana, sin filas nuevas): no se opina
    return False


def _incompleto(o: reglas.Ordeno, historial: list) -> bool:
    """El ordeño trae menos del 90 % de las vacas habituales de su base: tanto puede ser un problema real (faltó
    media tropa) como un corte de la conexión a mitad de ordeño. Antes de avisarlo se espera `ESPERA_INCOMPLETO_MIN`
    por si llegan las filas que faltan (el criterio de «incompleto» es el mismo que descarta días de la base)."""
    base = base_de(o, historial)
    return len(base) >= reglas.MIN_BASE and o.vacas < COMPLETO_MIN * st.median(b.vacas for b in base)


def _evaluar_produccion(tambo, ordenos, ahora, estado, causas, comida, vacas) -> list:
    hallazgos = []
    limite = datetime.timedelta(hours=PENDIENTE_H)
    ayer = (ahora - datetime.timedelta(days=1)).date().isoformat()
    for o in ordenos:
        if o.fecha not in (ahora.date().isoformat(), ayer):
            continue
        if not reglas.ordeno_terminado(o, ahora) or ahora - o.fin > limite:
            continue
        if _incompleto(o, ordenos) and ahora - o.fin < datetime.timedelta(minutes=ESPERA_INCOMPLETO_MIN):
            continue                 # puede ser el enlace con DelPro, que cortó a mitad: si llegan filas, se reevalúa
        for h in _evaluar_ordeno(tambo, o, ordenos, vacas, estado.ya_avisado):
            try:
                h["causas"] = list(causas(tambo, o, ordenos))
            except Exception as exc:  # noqa: BLE001
                log.warning("vigilante: no se pudo buscar la causa (%s)", exc)
                h["advertencias"] = [f"No se pudo consultar la causa ({type(exc).__name__}); revisar a mano."]
            hallazgos.append(h)
    try:
        c = reglas.comida_atrasada(comida(tambo, ahora.date()), ahora.date())
    except Exception as exc:  # noqa: BLE001
        log.warning("vigilante: no se pudo revisar la comida (%s)", exc)
        c = None
    clave = f"{ahora.date().isoformat()}|comida"
    if c and not estado.ya_avisado(clave):
        hallazgos.append({"tipo": "comida_atrasada", "clave": clave, "datos": c, "causas": []})
    return hallazgos


def _registrar(estado, enviados, retenidos, ahora, hoy) -> None:
    for h in enviados + retenidos:
        if h["tipo"] in vigilante_aviso.PRODUCCION:
            estado.registrar_aviso(h["clave"], ahora)
        elif h["tipo"] == "sin_datos":
            estado.set_estado_datos("sin_datos")
        elif h["tipo"] == "datos_vuelven":
            estado.set_estado_datos("ok")
    for h in retenidos:
        estado.retener(vigilante_aviso.resumen_linea(h), ahora)
    if any(h["tipo"] in vigilante_aviso.PRODUCCION for h in enviados):
        estado.registrar_envio(hoy)


# --------------------------------------------------------------------------- el hilo
def iniciar_hilo(tambo: str, enviar, parar: threading.Event | None = None, espera_s: float | None = None):
    """Arranca el bucle en un hilo daemon y lo devuelve. No hace nada mientras `activo` sea falso; cada pasada va en
    su propio try/except para que un error no mate el hilo. `parar` y `espera_s` son para las pruebas."""
    parar = parar or threading.Event()

    def bucle():
        while not parar.is_set():
            cfg = vigilante_estado.config()
            if cfg["activo"]:
                try:
                    pasada(tambo, enviar=enviar)
                except Exception:  # noqa: BLE001
                    log.exception("vigilante: la pasada falló")
            parar.wait(espera_s if espera_s is not None else cfg["pasada_min"] * 60)

    hilo = threading.Thread(target=bucle, name="vigilante", daemon=True)
    hilo.start()
    return hilo
