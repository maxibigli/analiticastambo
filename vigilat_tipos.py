# -*- coding: utf-8 -*-
"""Vigilat: qué mira cada tipo de regla y con qué texto lo cuenta.

`evaluar(regla, ctx)` devuelve `(hallazgos, nota)`. Un HALLAZGO es:

    {"texto":  lo que se manda,
     "audio":  lo que se dice en la sala (corto),
     "claves": las claves de dedup que quedan registradas al avisarlo,
     "datos_estado": "sin_datos" | "ok"   (solo la regla «sin datos»: cómo queda la sala después de avisar),
     "cierra": True                       (solo «volvieron los datos»: cierra los avisos abiertos de esa regla),
     "confirmable": False                 (no pide confirmación aunque la regla la pida)}

`nota` dice por qué no hay nada que avisar (o qué se encontró): se muestra en la pantalla y en «Probar».

Las reglas de ordeño reusan las del Vigilante anterior (`vigilante.py`, `vigilante_reglas.py`): hay UNA sola versión
de cada cuenta. Todo es SOLO LECTURA sobre DelPro y el mixer; los datos llegan por `ctx.fuentes`, que en las pruebas
es de mentira.
"""
import datetime
import logging
import statistics as st
from collections import defaultdict
from dataclasses import dataclass, field

import veredicto_baja
import vigilante
import vigilante_aviso
import vigilante_reglas as reglas

log = logging.getLogger("vigilat")

HORAS_SIN_DATOS_DEFECTO = 4
VIEJO_INCIDENCIAS_H = 36       # si la última visita es más vieja que esto, «las últimas 24 h» ya no son noticia
VIEJO_PRODUCCION_DIAS = 3      # idem para el último día completo de producción
DIAS_PREVIOS = 7
MIN_DIAS_PREVIOS = 4           # una vaca con menos días que esto en la semana no tiene «su promedio»
DIA_COMPLETO = 0.8             # un día con menos del 80 % de las vacas habituales está a medio cargar
MAX_PUESTOS_TEXTO = 12


class SinDatos(Exception):
    """La regla no se puede evaluar (la sala no tiene esas tablas, no hay datos recientes): no es una falla."""


@dataclass
class Contexto:
    fuentes: object
    ahora: datetime.datetime
    general: dict                       # ajustes generales de Vigilat
    ya: object = None                   # callable(clave) -> bool: esa clave ya se avisó
    datos_estado: str = "ok"            # cómo quedó la sala la última vez que se avisó (regla «sin datos»)
    horas_sin_datos: float = HORAS_SIN_DATOS_DEFECTO
    memo: dict = field(default_factory=dict)

    def __post_init__(self):
        self.ya = self.ya or (lambda _clave: False)

    @property
    def hoy(self) -> datetime.date:
        return self.ahora.date()


def _fecha(d) -> str:
    return veredicto_baja.fecha_larga(d)


def _n(x, dec=0) -> str:
    return veredicto_baja._n(x, dec)


def _hallazgo(texto: str, audio: str, claves=(), **extra) -> dict:
    return {"texto": texto, "audio": audio, "claves": list(claves), **extra}


# --------------------------------------------------------------------------- datos de ordeño
def ordenos_de(ctx: Contexto) -> list:
    if "ordenos" not in ctx.memo:
        desde = ctx.hoy - datetime.timedelta(days=vigilante.DIAS_LECTURA)
        ctx.memo["ordenos"] = ctx.fuentes.ordenos(desde, ctx.hoy)
    return ctx.memo["ordenos"]


def datos_cortados(ctx: Contexto, horas: float) -> tuple:
    """`(cortados, última fila)` con el mismo criterio de «sin datos» del Vigilante: se mide dentro de la ventana en que
    se suele ordeñar, no de punta a punta del día."""
    ordenos = ordenos_de(ctx)
    ultima = max((o.fin for o in ordenos), default=None)
    cfg = {"sin_datos_horas": horas, "silencio_hasta": ctx.general["silencio_hasta"]}
    aperturas = vigilante._aperturas(ordenos, ctx.hoy, cfg)
    cortado = (any(reglas.sin_datos(ultima, ctx.ahora, horas, v)
                   for v in vigilante._ventanas_de_ordeno(aperturas, cfg))
               or reglas.sin_datos_desde_apertura(ultima, ctx.ahora, horas, aperturas))
    return cortado, ultima


def _sin_datos(regla: dict, ctx: Contexto):
    horas = regla["parametros"]["horas"]
    cortado, ultima = datos_cortados(ctx, horas)
    if cortado:
        if ctx.datos_estado == "sin_datos":
            return [], "Los datos siguen cortados (ya se avisó)."
        h = {"tipo": "sin_datos", "datos": {"ultima_fila": ultima.isoformat() if ultima else None, "horas": horas}}
        return [_hallazgo("\n".join(vigilante_aviso._lineas(h)),
                          f"No están llegando datos del ordeño hace más de {_n(horas)} horas.",
                          datos_estado="sin_datos")], "No entran datos."
    if ctx.datos_estado == "sin_datos":
        if ultima is not None and ctx.ahora - ultima < datetime.timedelta(hours=horas):
            h = {"tipo": "datos_vuelven", "datos": {"ultima_fila": ultima.isoformat()}}
            return [_hallazgo("\n".join(vigilante_aviso._lineas(h)), "Volvieron los datos del ordeño.",
                              datos_estado="ok", cierra=True, confirmable=False)], "Volvieron los datos."
        return [], "Sin filas nuevas todavía."
    return [], "Los datos están llegando."


def _sala_cortada(ctx: Contexto) -> bool:
    return ctx.datos_estado == "sin_datos" or datos_cortados(ctx, ctx.horas_sin_datos)[0]


def _de_un_ordeno(regla: dict, ctx: Contexto, tipo: str):
    """Baja del ordeño y vacas que faltaron: cada ordeño terminado se evalúa una vez."""
    if _sala_cortada(ctx):
        return [], "Los datos de ordeño están cortados: no se analiza la producción."
    ordenos = ordenos_de(ctx)
    horas = vigilante.PENDIENTE_H if regla["cuando"]["modo"] == "ordeno" else 24
    limite = datetime.timedelta(hours=horas)
    dias = {ctx.hoy.isoformat(), (ctx.hoy - datetime.timedelta(days=1)).isoformat()}
    salida, mirados = [], 0
    for o in ordenos:
        if o.fecha not in dias or not reglas.ordeno_terminado(o, ctx.ahora) or ctx.ahora - o.fin > limite:
            continue
        if (vigilante._incompleto(o, ordenos)
                and ctx.ahora - o.fin < datetime.timedelta(minutes=vigilante.ESPERA_INCOMPLETO_MIN)):
            continue             # puede ser el enlace con DelPro, que cortó a mitad: si llegan filas, se reevalúa
        mirados += 1
        clave = f"{regla['id']}|{o.fecha}|{o.sesion}"
        if ctx.ya(clave) or ctx.ya(f"{o.fecha}|{o.sesion}|{tipo}"):      # la segunda: avisado por el Vigilante anterior
            continue
        base = vigilante.base_de(o, ordenos)
        if tipo == "baja_ordeno":
            datos = reglas.baja_ordeno(o, base, regla["parametros"]["pct"])
        else:
            datos = vigilante._faltantes(ctx.fuentes.tambo, o, base, ctx.fuentes.vacas,
                                         regla["parametros"]["pct"] / 100)
        if not datos:
            continue
        h = {"tipo": tipo, "clave": clave, "datos": {**datos, "fecha": o.fecha, "sesion": o.sesion}, "causas": []}
        try:
            h["causas"] = list(ctx.fuentes.causas(o, ordenos))
        except Exception as exc:  # noqa: BLE001
            log.warning("vigilat: no se pudo buscar la causa (%s)", exc)
            h["advertencias"] = [f"No se pudo consultar la causa ({type(exc).__name__}); revisar a mano."]
        if tipo == "baja_ordeno":
            audio = f"El ordeño {o.sesion} dio {_n(abs(datos['pct']))} por ciento menos de lo normal."
        else:
            audio = f"En el ordeño {o.sesion} faltaron {datos['faltan']} vacas."
        salida.append(_hallazgo(ctx.fuentes.redactar(h, ctx.ahora), audio, [clave]))
    if salida:
        return salida, f"{len(salida)} ordeño(s) para avisar."
    return [], (f"{mirados} ordeño(s) terminado(s) sin nada nuevo para avisar." if mirados
                else "No hay ningún ordeño terminado para mirar.")


def _comida(regla: dict, ctx: Contexto):
    if _sala_cortada(ctx):
        return [], "Los datos de ordeño están cortados: no se analiza."
    c = reglas.comida_atrasada(ctx.fuentes.comida(ctx.hoy), ctx.hoy, regla["parametros"]["minutos"])
    if not c:
        return [], "La primera comida de hoy no está atrasada (o todavía no se sabe)."
    clave = f"{regla['id']}|{ctx.hoy.isoformat()}"
    if ctx.ya(clave) or ctx.ya(f"{ctx.hoy.isoformat()}|comida"):
        return [], "La comida atrasada de hoy ya se avisó."
    h = {"tipo": "comida_atrasada", "datos": c, "causas": []}
    return [_hallazgo("\n".join(vigilante_aviso._lineas(h)),
                      f"La primera comida del mixer salió {_n(c['retraso'])} minutos más tarde de lo habitual.",
                      [clave])], "La primera comida se atrasó."


# --------------------------------------------------------------------------- fallas en las unidades
def sql_incidencias() -> str:
    """Incidencias por puesto en las 24 h anteriores a la última visita (mismas columnas y mismo criterio que la
    pantalla de ordeño, `ordeno.ORDENO_INC_SQL`), más cuándo fue esa visita, para no avisar sobre datos viejos."""
    import ordeno
    return f"""
    WITH fin AS (SELECT MAX(CreationTime) AS f FROM MilkingDeviceVisit)
    SELECT v.Place AS posicion,
           SUM(ISNULL(y.Slips, 0))          AS desliz,
           SUM(ISNULL(y.KickOffs, 0))       AS patadas,
           SUM(ISNULL(y.Blocks, 0))         AS bloqueos,
           SUM(ISNULL(y.NoOfReattaches, 0)) AS recoloc,
           CONVERT(varchar(19), MAX(fin.f), 120) AS ultima_visita
    FROM MilkingDeviceVisit v CROSS JOIN fin
    JOIN CMSMilkYield y ON y.MilkingDeviceVisit = v.OID
    WHERE v.Place BETWEEN 1 AND {ordeno.PUESTOS}
      AND v.CreationTime >= DATEADD(hour, -{ordeno.DIA_HORAS}, fin.f)
      AND y.MilkConfirmTime >= DATEADD(hour, -{ordeno.DIA_HORAS + 1}, fin.f)   -- sin esto recorre CMSMilkYield entera
    GROUP BY v.Place
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def _partes_de_falla(desliz, patadas, bloqueos, recoloc) -> str:
    nombres = ((desliz, "deslizamiento"), (patadas, "patada"), (bloqueos, "bloqueo"), (recoloc, "recolocación"))
    plural = {"deslizamiento": "deslizamientos", "patada": "patadas", "bloqueo": "bloqueos",
              "recolocación": "recolocaciones"}
    return ", ".join(f"{n} {k if n == 1 else plural[k]}" for n, k in nombres if n)


def _fallas(regla: dict, ctx: Contexto):
    filas = ctx.fuentes.incidencias()
    if not filas:
        raise SinDatos("No hay visitas a la rotativa para mirar.")
    ultima = max(datetime.datetime.fromisoformat(str(f[5])[:19]) for f in filas)
    if ctx.ahora - ultima > datetime.timedelta(hours=VIEJO_INCIDENCIAS_H):
        raise SinDatos(f"La última visita a la rotativa es del {_fecha(ultima.date().isoformat())} {ultima:%H:%M}: "
                       "no hay datos recientes.")
    totales = []
    for posicion, desliz, patadas, bloqueos, recoloc, _u in filas:
        partes = [int(x or 0) for x in (desliz, patadas, bloqueos, recoloc)]
        totales.append((int(posicion), sum(partes), partes))
    mediana = st.median(t for _p, t, _x in totales)
    p = regla["parametros"]
    umbral = max(p["minimo"], round(mediana * p["veces_mediana"]))
    malos = sorted((x for x in totales if x[1] >= umbral), key=lambda x: (-x[1], x[0]))
    if not malos:
        return [], f"Ningún puesto llegó a {umbral} fallas en 24 h (mediana {_n(mediana)})."
    claves = []
    if regla["cuando"]["modo"] == "cada":
        # Cada tantos minutos: un puesto ya avisado hoy no se repite. A horas fijas se manda la foto completa.
        dia = ctx.hoy.isoformat()
        malos = [x for x in malos if not ctx.ya(f"{regla['id']}|{dia}|{x[0]}")]
        if not malos:
            return [], "Los puestos con fallas ya se avisaron hoy."
        claves = [f"{regla['id']}|{dia}|{x[0]}" for x in malos]
    try:
        reportados = ctx.fuentes.reportados()
    except Exception:  # noqa: BLE001 - sin bitácora igual se avisa
        reportados = {}
    lineas = [f"🔧 Fallas en las unidades: {len(malos)} puesto(s) con {umbral} o más en las 24 h hasta el "
              f"{_fecha(ultima.date().isoformat())} {ultima:%H:%M} (mediana {_n(mediana)})."]
    for puesto, total, partes in malos[:MAX_PUESTOS_TEXTO]:
        extra = ""
        if reportados.get(puesto):
            extra = f" — ya reportado el {_fecha(reportados[puesto])}"
        lineas.append(f"• Puesto {puesto}: {total} ({_partes_de_falla(*partes)}){extra}")
    if len(malos) > MAX_PUESTOS_TEXTO:
        lineas.append(f"• … y {len(malos) - MAX_PUESTOS_TEXTO} puesto(s) más.")
    nombrados = [str(x[0]) for x in malos[:5]]
    lista = nombrados[0] if len(nombrados) == 1 else ", ".join(nombrados[:-1]) + " y " + nombrados[-1]
    audio = (f"Fallas en {'el puesto' if len(malos) == 1 else 'los puestos'} {lista}."
             if len(malos) <= 5 else f"Hay {len(malos)} puestos con fallas. Los peores: {lista}.")
    return [_hallazgo("\n".join(lineas), audio, claves)], f"{len(malos)} puesto(s) con fallas."


# --------------------------------------------------------------------------- vacas con baja producción
def vacas_en_baja(filas: list, hoy: datetime.date, pct: float, kg_min: float):
    """`(día, [(caída %, rp, kg del día, kg promedio previo, rodeo)])` sobre filas
    `(fecha, animal, número, rodeo, del, lactancia, kg, válido)`. El día es el último COMPLETO anterior a hoy, o `None`
    si no hay ninguno. Cada vaca se compara contra su propio promedio de los `DIAS_PREVIOS` días anteriores."""
    por_dia = defaultdict(dict)
    for fecha, animal, numero, grupo, _dim, _lact, kg, valido in filas:
        if not valido or not numero or int(numero) <= 0 or not kg:
            continue
        por_dia[datetime.date.fromisoformat(str(fecha)[:10])][animal] = (int(numero), grupo, float(kg))
    previos_a_hoy = sorted(d for d in por_dia if d < hoy)
    if not previos_a_hoy:
        return None, []
    habitual = st.median(len(por_dia[d]) for d in previos_a_hoy)
    completos = [d for d in previos_a_hoy if len(por_dia[d]) >= DIA_COMPLETO * habitual]
    if not completos:
        return None, []
    dia = completos[-1]
    ventana = [d for d in completos if 1 <= (dia - d).days <= DIAS_PREVIOS]
    bajas = []
    for animal, (numero, grupo, kg) in por_dia[dia].items():
        antes = [por_dia[d][animal][2] for d in ventana if animal in por_dia[d]]
        if len(antes) < MIN_DIAS_PREVIOS:
            continue
        media = st.mean(antes)
        if media < kg_min or media <= 0:
            continue
        caida = (media - kg) / media * 100
        if caida > pct:
            bajas.append((caida, numero, kg, media, grupo))
    bajas.sort(key=lambda x: (-x[0], x[1]))
    return dia, bajas


def _baja_produccion(regla: dict, ctx: Contexto):
    p = regla["parametros"]
    desde = ctx.hoy - datetime.timedelta(days=DIAS_PREVIOS + VIEJO_PRODUCCION_DIAS + 1)
    filas = ctx.fuentes.produccion(desde, ctx.hoy)
    dia, bajas = vacas_en_baja(filas, ctx.hoy, p["pct"], p["kg_min"])
    if dia is None:
        raise SinDatos("No hay ningún día completo de producción en los últimos días.")
    if (ctx.hoy - dia).days > VIEJO_PRODUCCION_DIAS:
        raise SinDatos(f"El último día completo de producción es el {_fecha(dia.isoformat())}: no hay datos recientes.")
    if not bajas:
        return [], f"Ninguna vaca bajó más de {_n(p['pct'])} % el {_fecha(dia.isoformat())}."
    clave = f"{regla['id']}|{dia.isoformat()}"
    if ctx.ya(clave):
        return [], f"Las bajas del {_fecha(dia.isoformat())} ya se avisaron."
    mostrar = bajas[:int(p["max_vacas"])]
    try:
        nombres = ctx.fuentes.nombres_rodeos({x[4] for x in mostrar if x[4] is not None})
    except Exception:  # noqa: BLE001 - sin nombres de rodeo igual se avisa
        nombres = {}
    plural = "vacas bajaron" if len(bajas) != 1 else "vaca bajó"
    lineas = [f"📉 {len(bajas)} {plural} más de {_n(p['pct'])} % el {_fecha(dia.isoformat())} contra sus "
              f"{DIAS_PREVIOS} días anteriores:"]
    for caida, numero, kg, media, grupo in mostrar:
        rodeo = f", {nombres[grupo]}" if nombres.get(grupo) else ""
        lineas.append(f"• RP {numero}: −{_n(caida)} % ({_n(media)} → {_n(kg)} kg{rodeo})")
    if len(bajas) > len(mostrar):
        lineas.append(f"• … y {len(bajas) - len(mostrar)} más (ver Salud del rodeo en LactIA).")
    audio = f"{len(bajas)} {'vacas con' if len(bajas) != 1 else 'vaca con'} baja producción."
    return [_hallazgo("\n".join(lineas), audio, [clave])], f"{len(bajas)} vaca(s) en baja."


_EVALUADORES = {
    "sin_datos": _sin_datos,
    "baja_ordeno": lambda r, c: _de_un_ordeno(r, c, "baja_ordeno"),
    "vacas_faltantes": lambda r, c: _de_un_ordeno(r, c, "vacas_faltantes"),
    "comida_atrasada": _comida,
    "fallas_unidades": _fallas,
    "baja_produccion": _baja_produccion,
}


def evaluar(regla: dict, ctx: Contexto) -> tuple:
    return _EVALUADORES[regla["tipo"]](regla, ctx)


# --------------------------------------------------------------------------- las fuentes reales
class Fuentes:
    """De dónde salen los datos en producción (solo lectura). Las pruebas usan otra con los mismos métodos."""

    def __init__(self, tambo: str):
        self.tambo = tambo

    def ordenos(self, desde, hasta):
        return vigilante.leer_ordenos(self.tambo, desde, hasta)

    def vacas(self, tambo, d0, d1):
        return vigilante.leer_vacas(tambo, d0, d1)

    def causas(self, ordeno, historial):
        return vigilante.buscar_causas(self.tambo, ordeno, historial)

    def comida(self, hoy):
        return vigilante.comida_de_hoy(self.tambo, hoy)

    def incidencias(self):
        import tambos
        if tambos.tipo_sala(self.tambo) != "rotativa":
            raise SinDatos("Las fallas por puesto solo se miden en sala rotativa.")
        return vigilante._corre(sql_incidencias(), self.tambo)

    def produccion(self, desde, hasta):
        import diagnostico_baja
        return vigilante._corre(diagnostico_baja.sql_vacas(desde.isoformat(), hasta.isoformat(), self.tambo),
                                self.tambo)

    def nombres_rodeos(self, oids) -> dict:
        import diagnostico_baja
        diagnostico_baja._salida.lineas = []
        nombres, _ok = diagnostico_baja._nombres_de_rodeos(self.tambo, oids)
        return {o: n for o, (n, _num) in nombres.items() if n}

    def reportados(self) -> dict:
        import bitacora
        return bitacora.abiertos_por_puesto(self.tambo)

    def redactar(self, hallazgo: dict, ahora: datetime.datetime) -> str:
        """El texto de un hallazgo de ordeño: lo redacta la IA sobre los hechos medidos y, si no se puede o no es de
        fiar, sale la plantilla (`vigilante_aviso.redactar`). Sin el encabezado, que lo pone Vigilat."""
        texto = vigilante_aviso.redactar([hallazgo], ahora, con_aviso_demora=vigilante.CON_AVISO_DEMORA)
        cabeza = vigilante_aviso.encabezado(ahora)       # solo la primera línea: la nota de demora, si va, se queda
        return texto[len(cabeza):].strip() if texto.startswith(cabeza) else texto
