# -*- coding: utf-8 -*-
"""Tablero de Diagnóstico: los indicadores del tambo en una sola pantalla.

QUÉ ES. Una portada con tarjetas semáforo. Cada tarjeta muestra un número que
la aplicación YA CALCULA en alguna de sus pantallas, lo pinta de verde a rojo
según los umbrales que fijó el tambo, y lleva de un clic a la pantalla donde
está el detalle, con el último mes ya seleccionado.

NO CALCULA NADA NUEVO, y es a propósito. Cada indicador declara de qué caché
sale su valor, y ese caché es el mismo que alimenta la pantalla de origen. Si el
tablero recalculara por su cuenta, dos pantallas podrían mostrar números
distintos del mismo indicador —que es la peor falla posible en un tablero— y
además nueve consultas pesadas al abrir la portada dejarían la aplicación
inusable con la memoria que tiene esta instalación.

CÓMO SE AGREGA UN INDICADOR NUEVO: una entrada en `INDICADORES`. Nada más. El
endpoint, el semáforo, la pantalla de configuración y la navegación salen todos
de lo que la entrada declara. Si agregar uno obliga a tocar otro archivo, el
registro está mal diseñado.

EL SEMÁFORO. Cada indicador declara `verde` y `rojo` —los dos valores entre los
que interpola— y una `direccion`:

    "bajo_mejor"  el costo por litro: verde 150, rojo 250
    "alto_mejor"  la calificación de rutina: verde 85, rojo 60

O sea que `verde` NO es siempre el número más chico: es el valor que se pinta
verde. Así el tambo carga los umbrales pensando en su significado y no en la
aritmética. Entre los dos, el color pasa por naranja de forma continua.

DOS COSAS QUE UNA TARJETA NUNCA DEBE HACER, y por eso están resueltas acá:

  * INVENTAR UN VALOR. Si el dato no está —falta la planilla de precios, el
    caché todavía no se calentó, el tambo no tiene sensores IoT— la tarjeta dice
    qué falta y en gris. Nunca un 0, que en un semáforo se pinta de un color y
    se lee como una medición.
  * TAPAR AL RESTO. Cada indicador se resuelve por separado y con su propio
    try/except: que falte la planilla de precios no puede dejar el tablero en
    blanco.
"""
import datetime
import json
import os
import threading

# Umbrales por tambo. Fuera de git como el resto del estado propio de cada
# instalación (`metas_reproductivas.json`, `conciliacion_grupos.json`): los
# umbrales de un tambo no son los de otro, y el del servidor de producción no
# tiene por qué venir de la PC de desarrollo.
_RUTA = os.path.join(os.path.dirname(__file__), "tablero_umbrales.json")
_lock = threading.Lock()

# --- Registro de indicadores -------------------------------------------------
# Campos de cada entrada:
#   clave       identificador estable; es la clave con la que se guardan los
#               umbrales del tambo, así que renombrarla pierde su configuración
#   nombre      cómo se muestra
#   unidad      sufijo del valor ("$/litro", "vacas", "%"...)
#   decimales   con cuántos se muestra
#   direccion   "alto_mejor" | "bajo_mejor"
#   verde/rojo  umbrales POR DEFECTO; el tambo los pisa desde Configuración
#   fuente      de dónde sale el valor (ver `_FUENTES` en app.py)
#   pagina      a qué sección salta el clic
#   destino     texto de "ver el detalle"
#   ayuda       qué mide y de dónde sale, en criollo — se muestra en la tarjeta
#   grupo       para ordenar la pantalla de configuración
INDICADORES = [
    {
        "clave": "costo_litro",
        "nombre": "Costo de producción por litro",
        "unidad": "$/litro", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 150, "rojo": 250,
        "fuente": "alimentacion", "pagina": "alimentacion", "tab": "libres",
        "destino": "Alimentación › Litros libres",
        "grupo": "Economía",
        "ayuda": ("Lo que cuesta el alimento por cada litro producido. Sale de la "
                  "planilla de precios del tambo; solo descuenta comida."),
    },
    {
        "clave": "litros_libres_pct",
        "nombre": "Litros libres",
        "unidad": "%", "decimales": 1,
        "direccion": "alto_mejor", "verde": 60, "rojo": 40,
        "fuente": "alimentacion", "pagina": "alimentacion", "tab": "libres",
        "destino": "Alimentación › Litros libres",
        "grupo": "Economía",
        "ayuda": ("Qué porcentaje de la producción queda después de pagar la "
                  "comida. Es el margen expresado en litros."),
    },
    {
        "clave": "conversion",
        "nombre": "Índice de conversión de alimento",
        "unidad": "kg sól./kg MS", "decimales": 3,
        "direccion": "alto_mejor", "verde": 0.16, "rojo": 0.13,
        "fuente": "alimentacion", "pagina": "alimentacion", "tab": "conversion",
        "destino": "Alimentación › Eficiencia de conversión",
        "grupo": "Alimentación",
        "ayuda": ("Kg de sólidos producidos por kg de materia seca consumida. Es "
                  "una relación física: no depende de los precios."),
    },
    {
        "clave": "rutina_score",
        "nombre": "Calificación de la última rutina",
        "unidad": "/100", "decimales": 0,
        "direccion": "alto_mejor", "verde": 85, "rojo": 65,
        "fuente": "rutina", "pagina": "rutina",
        "destino": "Rutina de ordeño",
        "grupo": "Ordeño",
        "ayuda": ("Nota de la última rutina de ordeño completa, combinando "
                  "colocación, flujo, repasos y bimodalidad."),
    },
    {
        "clave": "horas_ordeno",
        "nombre": "Horas de ordeño por día",
        "unidad": "h/día", "decimales": 1,
        "direccion": "bajo_mejor", "verde": 12, "rojo": 18,
        "fuente": "rendimiento", "pagina": "rendimiento",
        "destino": "Rendimiento Sala",
        "grupo": "Ordeño",
        "ayuda": ("Cuántas horas por día está trabajando la sala, sumando las "
                  "sesiones. Incluye el tiempo de arreo configurado."),
    },
    {
        "clave": "pct_identificacion",
        "nombre": "Identificación de ordeños",
        "unidad": "%", "decimales": 2,
        "direccion": "alto_mejor", "verde": 99, "rojo": 95,
        "fuente": "rendimiento", "pagina": "rendimiento",
        "destino": "Rendimiento Sala",
        "grupo": "Ordeño",
        "ayuda": ("Qué proporción de los ordeños quedó asociada a una vaca. Lo "
                  "que no se identifica no entra a ningún análisis individual."),
    },
    {
        "clave": "rcs_altas",
        "nombre": "Vacas con RCS alto",
        "unidad": "vacas", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 80, "rojo": 250,
        "fuente": "rcs", "pagina": "salud",
        "destino": "Salud del rodeo › RCS",
        "grupo": "Sanidad",
        "ayuda": ("Vacas por encima de 300.000 células/ml en el último control "
                  "lechero."),
    },
    {
        "clave": "rcs_cronicas",
        "nombre": "Casos crónicos de mastitis",
        "unidad": "vacas", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 30, "rojo": 120,
        "fuente": "rcs", "pagina": "salud",
        "destino": "Salud del rodeo › RCS",
        "grupo": "Sanidad",
        "ayuda": ("Vacas con RCS alto en el último control Y en el anterior: la "
                  "infección no se resolvió entre un control y otro."),
    },
    {
        "clave": "ufc",
        "nombre": "Recuento de UFC",
        "unidad": "×1000/ml", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 20, "rojo": 50,
        "fuente": "laserenisima", "pagina": "entregas",
        "destino": "Entregas a la usina",
        "grupo": "Sanidad",
        "ayuda": ("Unidades formadoras de colonia de las entregas. NO sale de "
                  "DelPro: lo publica la usina con cada remito."),
    },
    {
        "clave": "dias_abiertos",
        "nombre": "Días abiertos promedio",
        "unidad": "días", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 110, "rojo": 160,
        "fuente": "reproduccion", "pagina": "repro",
        "destino": "Análisis Reproductivo",
        "grupo": "Reproducción",
        "ayuda": ("Días entre el parto y la concepción. Cada día abierto de más "
                  "es un día de lactancia que no se recupera."),
    },
    {
        "clave": "iot_alarmas",
        "nombre": "Alarmas de monitoreo IoT",
        "unidad": "activas", "decimales": 0,
        "direccion": "bajo_mejor", "verde": 0, "rojo": 3,
        "fuente": "iot", "pagina": "iot",
        "destino": "Monitoreo IoT",
        "grupo": "Instalación",
        "ayuda": ("Sensores fuera de rango o sin reportar. Si el tambo no tiene "
                  "gateway conectado, la tarjeta queda sin dato."),
    },
]

POR_CLAVE = {i["clave"]: i for i in INDICADORES}

# Cuántos días atrás abre el histórico al saltar desde una tarjeta.
DIAS_HISTORICO = 30


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def color_de(valor, verde, rojo) -> dict | None:
    """Semáforo continuo verde → naranja → rojo. None si no hay valor.

    `verde` y `rojo` son los VALORES que se pintan de cada color, no un mínimo y
    un máximo: cuál de los dos es mayor depende de la dirección del indicador
    (en el costo por litro, verde=150 y rojo=250; en la calificación de rutina,
    verde=85 y rojo=65). Interpolando entre ellos sin asumir cuál es mayor, la
    misma función sirve para los dos casos y no hay que invertir nada.
    """
    v, g, r = _num(valor), _num(verde), _num(rojo)
    if v is None or g is None or r is None or g == r:
        return None
    # 0 = está en el valor "verde"; 1 = está en el valor "rojo".
    t = (v - g) / (r - g)
    t = max(0.0, min(1.0, t))
    if t <= 0.5:                       # verde -> naranja
        f = t / 0.5
        rgb = (int(0 + f * (242 - 0)), int(135 + f * (169 - 135)), int(90 + f * (0 - 90)))
        nivel = "bien" if t < 0.25 else "atencion"
    else:                              # naranja -> rojo
        f = (t - 0.5) / 0.5
        rgb = (int(242 + f * (228 - 242)), int(169 + f * (0 - 169)), int(0 + f * (43 - 0)))
        nivel = "atencion" if t < 0.75 else "mal"
    return {"css": f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})", "t": round(t, 3), "nivel": nivel}


def rango_historico(hasta: datetime.date = None) -> dict:
    """El mes que abre una tarjeta al hacer clic."""
    hasta = hasta or datetime.date.today()
    return {"desde": (hasta - datetime.timedelta(days=DIAS_HISTORICO)).isoformat(),
            "hasta": hasta.isoformat(), "dias": DIAS_HISTORICO}


def armar(valores: dict, config: dict = None, lecturas: dict = None,
          base_caida: str = None) -> dict:
    """Las tarjetas del tablero, listas para pintar.

    `valores`: {clave: {"valor": x} | {"falta": "por qué"}} — lo resuelve app.py
        leyendo los cachés de cada pantalla.
    `config`: {clave: {"verde", "rojo", "activo", "orden"}} del tambo.
    `lecturas`: la última lectura buena de cada indicador (ver `lecturas_de`).
    `base_caida`: si la base no responde, el motivo — para explicar de una vez
        por qué todo el tablero está mostrando datos viejos.

    SI NO HAY VALOR FRESCO SE MUESTRA EL ÚLTIMO CONOCIDO, rotulado con cuándo se
    leyó. Un número de ayer con su fecha sirve para decidir; un guion que no se
    mueve, no. La tarjeta queda marcada `viejo: true` para que la pantalla lo
    distinga a simple vista y nadie lo confunda con una medición de ahora.
    """
    config = config or {}
    lecturas = lecturas or {}
    tarjetas = []
    for ind in INDICADORES:
        cfg = config.get(ind["clave"]) or {}
        if cfg.get("activo") is False:
            continue
        verde = cfg.get("verde", ind["verde"])
        rojo = cfg.get("rojo", ind["rojo"])
        dato = valores.get(ind["clave"]) or {}
        valor, detalle = dato.get("valor"), dato.get("detalle")
        viejo, leido, hace = False, None, None

        if valor is None:
            prev = lecturas.get(ind["clave"]) or {}
            if prev.get("valor") is not None:
                valor = prev["valor"]
                detalle = prev.get("detalle")
                leido = prev.get("leido")
                hace, _ = _antiguedad(leido)
                viejo = True
        else:
            leido = datetime.datetime.now().isoformat(timespec="seconds")
            hace = "recién"

        col = color_de(valor, verde, rojo)
        tarjetas.append({
            **{k: ind[k] for k in ("clave", "nombre", "unidad", "decimales",
                                    "direccion", "pagina", "destino", "ayuda", "grupo")},
            "tab": ind.get("tab"),
            "valor": valor,
            "verde": verde, "rojo": rojo,
            "color": (col or {}).get("css"),
            "nivel": (col or {}).get("nivel", "sin_dato"),
            "posicion": (col or {}).get("t"),
            # Por qué no hay número, cuando no hay. Es lo que evita que una
            # tarjeta en gris parezca un error de la aplicación.
            "falta": dato.get("falta") if valor is None else None,
            # `calculando` solo si de verdad hay algo en curso: con la base
            # caída no se está calculando nada y decirlo sería mentir.
            "calculando": bool(dato.get("calculando")) and not base_caida,
            "detalle": detalle,
            "leido": leido, "hace": hace, "viejo": viejo,
            "orden": cfg.get("orden", 999),
        })
    tarjetas.sort(key=lambda t: (t["orden"], t["grupo"], t["nombre"]))
    con_dato = [t for t in tarjetas if t["valor"] is not None]
    return {
        "tarjetas": tarjetas,
        "historico": rango_historico(),
        "base_caida": base_caida,
        "resumen": {
            "total": len(tarjetas),
            "con_dato": len(con_dato),
            "viejos": sum(1 for t in tarjetas if t["viejo"]),
            "calculando": sum(1 for t in tarjetas if t["calculando"]),
            "en_rojo": sum(1 for t in con_dato if t["nivel"] == "mal"),
            "en_naranja": sum(1 for t in con_dato if t["nivel"] == "atencion"),
            "en_verde": sum(1 for t in con_dato if t["nivel"] == "bien"),
        },
    }


def catalogo() -> list:
    """Los indicadores con sus defaults, para la pantalla de configuración."""
    return [{k: i[k] for k in ("clave", "nombre", "unidad", "decimales",
                               "direccion", "verde", "rojo", "grupo", "ayuda",
                               "destino")}
            for i in INDICADORES]


# --- Última lectura buena de cada indicador ---------------------------------
# EL TABLERO TIENE QUE SEGUIR SIRVIENDO CON LA BASE CAÍDA. Este tambo se conecta
# a SERVER-DELPRO por red: cuando el servidor no está, cada consulta se cuelga 11
# segundos en el timeout de conexión y el tablero quedaba en «calculando» para
# siempre, reintentando cada 8 segundos sin que nada avanzara nunca.
#
# Así que cada valor que se resuelve bien se guarda en disco con su fecha y hora,
# y cuando la base no responde se sirve ESE, diciendo de cuándo es. Un número de
# ayer rotulado «leído ayer a las 14:30» es útil; un guion que no se mueve, no.
#
# En disco y no en memoria porque el caso que importa es justamente el de
# reiniciar el proceso con el servidor caído: en memoria se perdería todo.
_RUTA_LECTURAS = os.path.join(os.path.dirname(__file__), "tablero_lecturas.json")


def _leer_lecturas() -> dict:
    try:
        with open(_RUTA_LECTURAS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def lecturas_de(tambo_id: str) -> dict:
    """{clave: {"valor", "leido" ISO, "detalle"}} — la última lectura buena."""
    return (_leer_lecturas().get(tambo_id) or {})


def guardar_lecturas(tambo_id: str, valores: dict) -> dict:
    """Guarda los indicadores que SÍ tienen valor, con su fecha y hora.

    Solo los que tienen valor: si se guardara un None se pisaría la última
    lectura buena justo cuando más falta hace.
    """
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        todo = _leer_lecturas()
        del_tambo = todo.get(tambo_id) or {}
        for clave, d in (valores or {}).items():
            if (d or {}).get("valor") is None:
                continue
            del_tambo[clave] = {"valor": d["valor"], "leido": ahora,
                                "detalle": d.get("detalle")}
        todo[tambo_id] = del_tambo
        try:
            with open(_RUTA_LECTURAS, "w", encoding="utf-8") as f:
                json.dump(todo, f, ensure_ascii=False, indent=1)
        except OSError:
            pass          # no poder guardar el histórico no puede romper el tablero
    return del_tambo


def _antiguedad(iso: str) -> tuple:
    """(texto, minutos) de hace cuánto se leyó. ('', None) si no hay fecha."""
    try:
        t = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "", None
    mins = max(0, int((datetime.datetime.now() - t).total_seconds() // 60))
    if mins < 1:
        txt = "recién"
    elif mins < 60:
        txt = f"hace {mins} min"
    elif mins < 60 * 24:
        txt = f"hace {mins // 60} h"
    else:
        txt = f"hace {mins // (60 * 24)} día(s)"
    return txt, mins


# --- Umbrales guardados por tambo -------------------------------------------

def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def config_de(tambo_id: str) -> dict:
    """{clave: {verde, rojo, activo, orden}} del tambo, con los defaults del
    registro ya aplicados. Un indicador que nunca se tocó sale con lo que
    declara `INDICADORES`, así el tablero funciona sin configurar nada."""
    guardado = (_leer().get(tambo_id) or {})
    salida = {}
    for i, ind in enumerate(INDICADORES):
        g = guardado.get(ind["clave"]) or {}
        salida[ind["clave"]] = {
            "verde": g.get("verde", ind["verde"]),
            "rojo": g.get("rojo", ind["rojo"]),
            "activo": g.get("activo", True),
            "orden": g.get("orden", i),
            # Para que la pantalla pueda marcar qué se cambió y ofrecer volver
            # al valor original sin tener que recordarlo.
            "verde_defecto": ind["verde"], "rojo_defecto": ind["rojo"],
            "modificado": ("verde" in g and g["verde"] != ind["verde"])
                          or ("rojo" in g and g["rojo"] != ind["rojo"]),
        }
    return salida


def guardar(tambo_id: str, datos: dict) -> dict:
    """Guarda (mergea) los umbrales del tambo.

    VALIDA QUE VERDE Y ROJO NO SEAN IGUALES y que respeten la dirección del
    indicador. Sin esto se puede guardar «verde 200, rojo 150» en un indicador
    donde más bajo es mejor, y el semáforo queda al revés: el tablero pinta de
    verde justo lo que hay que mirar. Es un error fácil de cometer cargando
    números a mano y muy difícil de notar después.
    """
    entrantes = {}
    for clave, v in (datos or {}).items():
        ind = POR_CLAVE.get(clave)
        if not ind:
            continue                       # clave desconocida: se ignora
        actual = {}
        for campo in ("verde", "rojo"):
            if campo in v:
                n = _num(v[campo])
                if n is None:
                    raise ValueError(f"{ind['nombre']}: «{campo}» tiene que ser un número.")
                actual[campo] = n
        verde = actual.get("verde", ind["verde"])
        rojo = actual.get("rojo", ind["rojo"])
        if verde == rojo:
            raise ValueError(f"{ind['nombre']}: verde y rojo no pueden ser iguales "
                             f"(no habría escala).")
        if ind["direccion"] == "bajo_mejor" and verde > rojo:
            raise ValueError(
                f"{ind['nombre']}: en este indicador MÁS BAJO ES MEJOR, así que el "
                f"valor verde ({verde:g}) tiene que ser menor que el rojo ({rojo:g}). "
                f"Como está, el tablero pintaría de verde lo que hay que mirar.")
        if ind["direccion"] == "alto_mejor" and verde < rojo:
            raise ValueError(
                f"{ind['nombre']}: en este indicador MÁS ALTO ES MEJOR, así que el "
                f"valor verde ({verde:g}) tiene que ser mayor que el rojo ({rojo:g}). "
                f"Como está, el tablero pintaría de verde lo que hay que mirar.")
        actual["verde"], actual["rojo"] = verde, rojo
        if "activo" in v:
            actual["activo"] = bool(v["activo"])
        if "orden" in v and v["orden"] is not None:
            try:
                actual["orden"] = int(v["orden"])
            except (TypeError, ValueError):
                pass
        entrantes[clave] = actual

    with _lock:
        todo = _leer()
        del_tambo = todo.get(tambo_id) or {}
        for clave, v in entrantes.items():
            del_tambo[clave] = {**(del_tambo.get(clave) or {}), **v}
        todo[tambo_id] = del_tambo
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
    return config_de(tambo_id)


def restablecer(tambo_id: str, clave: str = None) -> dict:
    """Vuelve a los umbrales por defecto: uno solo, o todos si `clave` es None."""
    with _lock:
        todo = _leer()
        if clave:
            (todo.get(tambo_id) or {}).pop(clave, None)
        else:
            todo.pop(tambo_id, None)
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
    return config_de(tambo_id)
