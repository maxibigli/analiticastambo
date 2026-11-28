# -*- coding: utf-8 -*-
"""Vigilat: la configuración que arma el tambo (reglas en orden de prioridad, personas, rangos y ajustes generales).

Se guarda en `vigilat_config.json`, fuera de git: tiene nombres, teléfonos y correos de personas, y es estado propio de
cada instalación. Antes de pisarlo queda una copia en `vigilat_config.anterior.json`.

* El ORDEN de la lista de reglas es la prioridad: la primera es la 1, y es la única que avisa también en el horario de
  silencio.
* El primer RANGO de la lista es el del dueño: es a quien se llama si nadie confirma.
* Un DESTINO es `"p:<id de persona>"`, `"rango:<nombre>"` o `"generales"` (los canales de alerta de siempre, los de
  ⚙ Configuración › Alertas, que mandan a las direcciones puestas por variable de entorno).

Si el archivo todavía no existe, la configuración sale de la del Vigilante anterior (`vigilante_estado.config()`): sus
cuatro condiciones quedan como reglas que avisan por los canales generales, así que actualizar no cambia a quién le
llega nada. Las dos reglas nuevas vienen en pausa.
"""
import copy
import json
import os
import re
import threading

import vigilante_estado

_AQUI = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(_AQUI, "vigilat_config.json")
_lock = threading.Lock()

CANALES = ("whatsapp", "telegram", "correo")
GENERALES = "generales"
RANGOS_DEFECTO = ["Dueño", "Encargado", "Veterinario", "Mecánico", "Mixero", "Ordeñador"]
URL_DEFECTO = "https://www.analiticastambo.com"

MAX_PERSONAS = 60
MAX_RANGOS = 20
MAX_REGLAS = 30
MAX_HORAS = 12

_HORA_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,30}")
_WHATSAPP_RE = re.compile(r"\+[0-9]{8,15}")
_TELEGRAM_RE = re.compile(r"-?[0-9]{4,20}")
_CORREO_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SALIDA_RE = re.compile(r"do_([1-9]|[12][0-9]|3[0-2])")

# El catálogo de reglas de la primera fase. `parametros`: (clave, texto, mínimo, máximo, por defecto, es entero).
# `modos`: cuándo puede correr. «ordeno» = apenas termina cada ordeño; «cada» = cada N minutos; «horas» = a horas fijas.
TIPOS = {
    "sin_datos": {
        "nombre": "Sin datos de la sala",
        "descripcion": "Dejaron de llegar datos de ordeño desde DelPro. Avisa también cuando vuelven.",
        "modos": ("cada",),
        "cuando": {"modo": "cada", "minutos": 15},
        "parametros": (("horas", "Avisar si no entra nada hace (horas)", 1, 24, 4, False),),
    },
    "fallas_unidades": {
        "nombre": "Fallas en las unidades",
        "descripcion": "Puestos de la rotativa con muchos deslizamientos, patadas, bloqueos o recolocaciones en las "
                       "últimas 24 horas.",
        "modos": ("horas", "cada"),
        "cuando": {"modo": "horas", "horas": ["05:30", "13:30", "21:30"]},
        "parametros": (("minimo", "Fallas de un puesto para avisar (como mínimo)", 1, 200, 4, True),
                       ("veces_mediana", "Y además tantas veces la mediana de los puestos", 0, 10, 2.5, False)),
    },
    "baja_ordeno": {
        "nombre": "Ordeño peor que lo normal",
        "descripcion": "Un ordeño terminado con claramente menos leche que el mismo ordeño de los 7 días anteriores.",
        "modos": ("ordeno", "horas"),
        "cuando": {"modo": "ordeno"},
        "parametros": (("pct", "Avisar si bajó al menos (%)", 1, 50, 4, False),),
    },
    "vacas_faltantes": {
        "nombre": "Faltaron vacas al ordeño",
        "descripcion": "Vacas habituales de un ordeño que no pasaron, y pesan en la leche.",
        "modos": ("ordeno", "horas"),
        "cuando": {"modo": "ordeno"},
        "parametros": (("pct", "Avisar si las que faltan pesan al menos (% de la leche)", 0.5, 30, 1, False),),
    },
    "baja_produccion": {
        "nombre": "Vacas con baja producción",
        "descripcion": "Vacas que en el último día completo dieron mucho menos que su promedio de los 7 días anteriores.",
        "modos": ("horas", "cada"),
        "cuando": {"modo": "horas", "horas": ["07:00"]},
        "parametros": (("pct", "Avisar la vaca que bajó más de (%)", 5, 80, 15, False),
                       ("kg_min", "Solo vacas que venían dando al menos (kg por día)", 0, 60, 10, False),
                       ("max_vacas", "Nombrar en el mensaje hasta (vacas)", 1, 40, 10, True)),
    },
    "comida_atrasada": {
        "nombre": "Primera comida del mixer atrasada",
        "descripcion": "La primera descarga a las vacas en ordeño se atrasó contra su horario habitual.",
        "modos": ("cada", "horas"),
        "cuando": {"modo": "cada", "minutos": 15},
        "parametros": (("minutos", "Avisar si se atrasó al menos (minutos)", 15, 600, 60, True),),
    },
}

# El orden de las reglas con que nace una instalación: (id, tipo, activa)
_REGLAS_DE_ARRANQUE = (
    ("sin_datos", "sin_datos", True),
    ("fallas_unidades", "fallas_unidades", False),
    ("baja_ordeno", "baja_ordeno", True),
    ("vacas_faltantes", "vacas_faltantes", True),
    ("baja_produccion", "baja_produccion", False),
    ("comida_atrasada", "comida_atrasada", True),
)


class ConfigError(ValueError):
    """Algo de la configuración no es válido. El mensaje se le muestra a quien está guardando."""


def regla_nueva(tipo: str, id_: str, activa: bool = True, destinos: list | None = None) -> dict:
    t = TIPOS[tipo]
    return {
        "id": id_, "tipo": tipo, "nombre": t["nombre"], "nota": "", "activa": activa,
        "parametros": {p[0]: p[4] for p in t["parametros"]},
        "cuando": {**copy.deepcopy(t["cuando"]), "dias": list(range(7))},
        "acciones": {"mensaje": True, "alarma": False, "audio": False},
        "destinos": list(destinos or []),
        "confirmacion": {"requiere": False, "espera_min": 15, "nivel2": [], "nivel3": [], "dueno": True},
    }


def por_defecto() -> dict:
    """La configuración con que arranca una instalación, tomando lo que estaba elegido en el Vigilante anterior."""
    viejo = vigilante_estado.config()
    reglas = []
    for id_, tipo, activa in _REGLAS_DE_ARRANQUE:
        r = regla_nueva(tipo, id_, activa, [GENERALES])
        if tipo == "sin_datos":
            r["parametros"]["horas"] = viejo["sin_datos_horas"]
        if r["cuando"]["modo"] == "cada":
            r["cuando"]["minutos"] = viejo["pasada_min"]
        reglas.append(r)
    return {
        "general": {
            "activo": viejo["activo"], "revisar_min": viejo["pasada_min"], "max_por_persona_dia": viejo["tope_dia"],
            "silencio_desde": viejo["silencio_desde"], "silencio_hasta": viejo["silencio_hasta"],
            "direccion_publica": (os.environ.get("LACTIA_URL_PUBLICA") or URL_DEFECTO).rstrip("/"),
            "salida_alarma": "",
        },
        "rangos": list(RANGOS_DEFECTO),
        "personas": [],
        "reglas": reglas,
    }


# --------------------------------------------------------------------------- validación
def _texto(v, campo: str, maximo: int, obligatorio: bool = True) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ConfigError(f"{campo}: tiene que ser un texto.")
    v = " ".join(v.split())
    if obligatorio and not v:
        raise ConfigError(f"Falta {campo.lower()}.")
    if len(v) > maximo:
        raise ConfigError(f"{campo}: como máximo {maximo} letras.")
    return v


def _numero(v, campo: str, lo: float, hi: float, entero: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi or (entero and v != int(v)):
        raise ConfigError(f"{campo}: tiene que ser un número{' entero' if entero else ''} entre {lo:g} y {hi:g}.")
    return int(v) if entero or v == int(v) else float(v)


def _booleano(v, campo: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{campo}: tiene que ser sí o no.")
    return v


def _hora(v, campo: str) -> str:
    if not isinstance(v, str) or not _HORA_RE.fullmatch(v):
        raise ConfigError(f"{campo}: tiene que tener formato HH:MM (por ejemplo 07:00).")
    return v


def _dict(v, campo: str) -> dict:
    if not isinstance(v, dict):
        raise ConfigError(f"{campo}: formato inválido.")
    return v


def _lista(v, campo: str, maximo: int) -> list:
    if not isinstance(v, list):
        raise ConfigError(f"{campo}: formato inválido.")
    if len(v) > maximo:
        raise ConfigError(f"{campo}: como máximo {maximo}.")
    return v


def _id(v, campo: str) -> str:
    if not isinstance(v, str) or not _ID_RE.fullmatch(v):
        raise ConfigError(f"{campo}: identificador inválido.")
    return v


def _general(g) -> dict:
    g = _dict(g, "Ajustes generales")
    url = _texto(g.get("direccion_publica"), "Dirección para confirmar", 200, obligatorio=False).rstrip("/")
    if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
        raise ConfigError("Dirección para confirmar: tiene que empezar con http:// o https:// (o quedar vacía).")
    salida = g.get("salida_alarma") or ""
    if salida and not (isinstance(salida, str) and _SALIDA_RE.fullmatch(salida)):
        raise ConfigError("Salida de la alarma: no es una salida del gateway.")
    return {
        "activo": _booleano(g.get("activo"), "Vigilat encendido"),
        "revisar_min": _numero(g.get("revisar_min"), "Cada cuántos minutos se miran los ordeños", 5, 120, True),
        "max_por_persona_dia": _numero(g.get("max_por_persona_dia"), "Máximo de mensajes por persona por día", 1, 100,
                                       True),
        "silencio_desde": _hora(g.get("silencio_desde"), "Silencio desde"),
        "silencio_hasta": _hora(g.get("silencio_hasta"), "Silencio hasta"),
        "direccion_publica": url,
        "salida_alarma": salida,
    }


def _rangos(rangos) -> list:
    limpio = []
    for r in _lista(rangos, "Rangos", MAX_RANGOS):
        r = _texto(r, "El nombre del rango", 40)
        if r.casefold() in (x.casefold() for x in limpio):
            raise ConfigError(f"El rango «{r}» está repetido.")
        limpio.append(r)
    if not limpio:
        raise ConfigError("Tiene que haber al menos un rango (el primero es el del dueño).")
    return limpio


def _persona(p, rangos: list) -> dict:
    p = _dict(p, "Persona")
    nombre = _texto(p.get("nombre"), "El nombre de la persona", 60)
    rango = p.get("rango")
    if rango not in rangos:
        raise ConfigError(f"{nombre}: elegí un rango de la lista.")
    canales = _lista(p.get("canales", []), f"{nombre}: formas de contacto", len(CANALES))
    if any(c not in CANALES for c in canales):
        raise ConfigError(f"{nombre}: forma de contacto desconocida.")
    whatsapp = re.sub(r"[\s\-()]", "", _texto(p.get("whatsapp"), "WhatsApp", 30, obligatorio=False))
    telegram = _texto(p.get("telegram"), "Telegram", 25, obligatorio=False)
    correo = _texto(p.get("correo"), "Correo", 120, obligatorio=False)
    if whatsapp and not _WHATSAPP_RE.fullmatch(whatsapp):
        raise ConfigError(f"{nombre}: el WhatsApp va con código de país, por ejemplo +5492392123456.")
    if telegram and not _TELEGRAM_RE.fullmatch(telegram):
        raise ConfigError(f"{nombre}: en Telegram va el número de chat (usá «Buscar chats de Telegram»), no el usuario.")
    if correo and not _CORREO_RE.fullmatch(correo):
        raise ConfigError(f"{nombre}: el correo no parece una dirección válida.")
    valores = {"whatsapp": whatsapp, "telegram": telegram, "correo": correo}
    for c in canales:
        if not valores[c]:
            raise ConfigError(f"{nombre}: tiene tildado {c} pero falta el dato.")
    return {"id": _id(p.get("id"), f"{nombre}"), "nombre": nombre, "rango": rango,
            "activa": _booleano(p.get("activa", True), f"{nombre}: activa"),
            "canales": [c for c in CANALES if c in canales], **valores}


def _destinos(v, campo: str, personas: set, rangos: list) -> list:
    limpio = []
    for d in _lista(v, campo, MAX_PERSONAS + MAX_RANGOS + 1):
        if not isinstance(d, str):
            raise ConfigError(f"{campo}: destinatario inválido.")
        valido = (d == GENERALES or (d.startswith("p:") and d[2:] in personas)
                  or (d.startswith("rango:") and d[6:] in rangos))
        if not valido:
            raise ConfigError(f"{campo}: hay un destinatario que ya no existe ({d}). Sacalo de la regla.")
        if d not in limpio:
            limpio.append(d)
    return limpio


def _cuando(c, tipo: str, nombre: str) -> dict:
    c = _dict(c, f"{nombre}: cuándo")
    modo = c.get("modo")
    if modo not in TIPOS[tipo]["modos"]:
        raise ConfigError(f"{nombre}: esta regla no puede correr de esa forma.")
    dias = _lista(c.get("dias", list(range(7))), f"{nombre}: días", 7)
    if not dias or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in dias):
        raise ConfigError(f"{nombre}: elegí al menos un día de la semana.")
    salida = {"modo": modo, "dias": sorted(set(dias))}
    if modo == "cada":
        salida["minutos"] = _numero(c.get("minutos"), f"{nombre}: cada cuántos minutos", 5, 1440, True)
    elif modo == "horas":
        horas = _lista(c.get("horas", []), f"{nombre}: horarios", MAX_HORAS)
        if not horas:
            raise ConfigError(f"{nombre}: cargá al menos un horario.")
        salida["horas"] = sorted({_hora(h, f"{nombre}: horario") for h in horas})
    return salida


def _regla(r, personas: set, rangos: list) -> dict:
    r = _dict(r, "Regla")
    tipo = r.get("tipo")
    if tipo not in TIPOS:
        raise ConfigError(f"Tipo de regla desconocido: {tipo!r}.")
    nombre = _texto(r.get("nombre"), "El nombre de la regla", 80)
    params = _dict(r.get("parametros", {}), f"{nombre}: condición")
    limpios = {}
    for clave, texto, lo, hi, defecto, entero in TIPOS[tipo]["parametros"]:
        limpios[clave] = _numero(params.get(clave, defecto), f"{nombre}: {texto.lower()}", lo, hi, entero)
    acc = _dict(r.get("acciones", {}), f"{nombre}: acciones")
    acciones = {k: _booleano(acc.get(k, False), f"{nombre}: {k}") for k in ("mensaje", "alarma", "audio")}
    destinos = _destinos(r.get("destinos", []), f"{nombre}: a quién", personas, rangos)
    activa = _booleano(r.get("activa"), f"{nombre}: activa")
    if activa and acciones["mensaje"] and not destinos:
        raise ConfigError(f"{nombre}: tildá a quién avisar, o sacale la acción «Mandar mensaje».")
    cf = _dict(r.get("confirmacion", {}), f"{nombre}: confirmación")
    confirmacion = {
        "requiere": _booleano(cf.get("requiere", False), f"{nombre}: requiere confirmación"),
        "espera_min": _numero(cf.get("espera_min", 15), f"{nombre}: minutos de espera", 5, 720, True),
        "nivel2": _destinos(cf.get("nivel2", []), f"{nombre}: segunda persona", personas, rangos),
        "nivel3": _destinos(cf.get("nivel3", []), f"{nombre}: tercera persona", personas, rangos),
        "dueno": _booleano(cf.get("dueno", True), f"{nombre}: avisar al dueño"),
    }
    if confirmacion["requiere"] and not acciones["mensaje"]:
        raise ConfigError(f"{nombre}: para pedir confirmación tiene que mandar mensaje.")
    return {"id": _id(r.get("id"), nombre), "tipo": tipo, "nombre": nombre,
            "nota": _texto(r.get("nota"), f"{nombre}: descripción", 300, obligatorio=False), "activa": activa,
            "parametros": limpios, "cuando": _cuando(r.get("cuando"), tipo, nombre), "acciones": acciones,
            "destinos": destinos, "confirmacion": confirmacion}


def validar(datos) -> dict:
    """La configuración limpia, o `ConfigError`. Se valida TODO antes de escribir: no queda nada a medias."""
    datos = _dict(datos, "Configuración")
    rangos = _rangos(datos.get("rangos"))
    personas = [_persona(p, rangos) for p in _lista(datos.get("personas"), "Personas", MAX_PERSONAS)]
    ids = [p["id"] for p in personas]
    if len(set(ids)) != len(ids):
        raise ConfigError("Hay dos personas con el mismo identificador.")
    reglas = [_regla(r, set(ids), rangos) for r in _lista(datos.get("reglas"), "Reglas", MAX_REGLAS)]
    ids_reglas = [r["id"] for r in reglas]
    if len(set(ids_reglas)) != len(ids_reglas):
        raise ConfigError("Hay dos reglas con el mismo identificador.")
    return {"general": _general(datos.get("general")), "rangos": rangos, "personas": personas, "reglas": reglas}


# --------------------------------------------------------------------------- lectura y escritura
def leer(ruta: str | None = None) -> tuple[dict, str | None]:
    """`(configuración, problema)`. Sin archivo: la de arranque y ningún problema. Con un archivo roto o inválido: la de
    arranque APAGADA y el motivo — nunca se manda nada con una configuración que no se pudo leer como se guardó."""
    ruta = ruta or RUTA
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            crudo = json.load(f)
    except FileNotFoundError:
        return por_defecto(), None
    except (OSError, ValueError) as exc:
        cfg = por_defecto()
        cfg["general"]["activo"] = False
        return cfg, f"No se pudo leer {os.path.basename(ruta)} ({type(exc).__name__}): Vigilat queda apagado."
    try:
        return validar(crudo), None
    except ConfigError as exc:
        cfg = por_defecto()
        cfg["general"]["activo"] = False
        return cfg, f"La configuración guardada no es válida ({exc}): Vigilat queda apagado."


def guardar(datos, ruta: str | None = None) -> dict:
    limpio = validar(datos)
    ruta = ruta or RUTA
    with _lock:
        if os.path.exists(ruta):
            base, ext = os.path.splitext(ruta)
            with open(ruta, "rb") as origen, open(f"{base}.anterior{ext}", "wb") as copia:
                copia.write(origen.read())
        temporal = ruta + ".tmp"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump(limpio, f, ensure_ascii=False, indent=1)
        os.replace(temporal, ruta)
    return limpio


# --------------------------------------------------------------------------- consultas sobre la configuración
def persona(cfg: dict, id_: str) -> dict | None:
    return next((p for p in cfg["personas"] if p["id"] == id_), None)


def resolver_destinos(cfg: dict, destinos: list) -> list:
    """Las personas (activas) a las que apuntan unos destinos, sin repetir; los canales generales salen como
    `{"id": "generales", ...}`. El orden es el de los destinos."""
    salida, vistos = [], set()

    def agregar(p):
        if p["id"] not in vistos:
            vistos.add(p["id"])
            salida.append(p)

    for d in destinos:
        if d == GENERALES:
            agregar({"id": GENERALES, "nombre": "Canales generales", "rango": "", "activa": True,
                     "canales": [GENERALES]})
        elif d.startswith("p:"):
            p = persona(cfg, d[2:])
            if p and p["activa"]:
                agregar(p)
        elif d.startswith("rango:"):
            for p in cfg["personas"]:
                if p["activa"] and p["rango"] == d[6:]:
                    agregar(p)
    return salida


def escalera(cfg: dict, regla: dict) -> list:
    """Los niveles de aviso de una regla, como lista de listas de destinos: quienes reciben primero y, si pide
    confirmación, la segunda persona, la tercera y por último el dueño. Un nivel sin nadie se saltea."""
    niveles = [regla["destinos"]]
    cf = regla["confirmacion"]
    if cf["requiere"]:
        niveles += [cf["nivel2"], cf["nivel3"]]
        if cf["dueno"]:
            niveles.append([f"rango:{cfg['rangos'][0]}"])
    return [n for n in niveles if resolver_destinos(cfg, n)]
