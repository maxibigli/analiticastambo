# -*- coding: utf-8 -*-
"""Catálogo de sensores del Monitoreo IoT: la lista que arma el tambo desde ⚙ Configuración › 🔌 Entradas/Salidas.

Las ENTRADAS FÍSICAS (6 analógicas y 8 digitales del gateway PUSR M300) las fija el hardware y no se agregan. Los SENSORES
son lo que el tambo quiere ver y se agregan o sacan desde la web; cada uno se conecta, si quiere, a una entrada:

  * `analogico`: un valor medido (temperatura, humedad, vacío…) con su unidad y la escala por defecto de su dial. Se conecta a
    una entrada analógica en `iot_analogicas` (el asignador de siempre, con su calibración).
  * `digital`: un contacto de dos estados atado a una entrada digital (`entrada`). Dos funciones (`rol`):
      - `alarma`: activo = problema. Sale en rojo («ALARMA») y en verde inactivo («Normal»); no se pueden cambiar los colores.
      - `indicador`: un fin de carrera, una posición… Dos textos y un color por estado (`verde`, `amarillo`, `rojo` o
        `neutro` = sin color, no cuenta para nada). Si un estado es rojo es una alarma igual.
      - `estado`: el estado de un equipo (la lavadora, el barrido…). Mientras está activo, el estado general de la rotativa
        (el cartel de arriba del panel) dice su `estado_nombre` («LAVANDO»). Sin colores y sin aviso: no es una alarma. Si
        hay varios activos a la vez manda el que está primero en la lista (la prioridad es el orden). Debajo de todos
        quedan ORDEÑO (hay visitas recientes en DelPro) y APAGADO.
    `avisar`: manda por los canales de alerta cuando entra en rojo (ver `iot_avisos`). `invertido` es el contacto
    normalmente cerrado (el cable cortado se lee como activo).

Sin archivo, la lista son los 11 sensores analógicos de siempre (mismas claves: se conserva el historial y la configuración)
más los dos estados de siempre de la rotativa, LAVANDO y BARRIDO (`PREDEFINIDOS`, atados a DI01 y DI02). Un archivo guardado
antes de que existieran los estados (sin `version`) los recibe una vez al leerlo, si sus entradas están libres; el primer
guardado le pone `version` y de ahí en más se respeta lo que el tambo deje (puede sacarlos).
El ORDEN de la lista es el orden de las tarjetas del panel, y `tamano` (chico / mediano / grande) cuánto lugar ocupa cada una.
La `clave` es interna y no cambia nunca (el nombre sí). Se guarda en `iot_sensores.json` (estado propio de cada instalación,
fuera de git). Deliberadamente SIN import de `iot_monitoreo`/`iot_lavado`, que son los que lo importan a él.
"""
import json
import os
import re
import tempfile
import threading
import unicodedata

import iot_canales
from iot_analogicas import numero as _numero

_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iot_sensores.json")
_lock = threading.Lock()

MAX_SENSORES = 60
MAX_NOMBRE = 40
MAX_UNIDAD = 12
MAX_TEXTO = 24
CLAVE_RESERVADA = "ith"           # el ITH se calcula con la temperatura y la humedad ambiente: no es un sensor de la lista

# Las entradas digitales del gateway: hasta 32 (las mismas claves que `iot_lavado.CANALES`; una prueba lo verifica).
ENTRADAS = iot_canales.ENTRADAS_DIGITALES
ROLES = ("alarma", "indicador", "estado")
VERSION = 2
ESTADOS_RESERVADOS = ("ORDEÑO", "ORDENO", "APAGADO")   # los dos de abajo del estado general: no se pueden usar de nombre
COLORES = ("verde", "amarillo", "rojo", "neutro")
TAMANOS = ("chico", "mediano", "grande")      # cuánto lugar ocupa la tarjeta en el panel: por importancia
TEXTOS_DE_ROL = {"alarma": ("ALARMA", "Normal"), "indicador": ("Activo", "Inactivo"), "estado": ("Activo", "Inactivo")}

DEFECTO = [
    {"clave": "temp_leche", "label": "Temp. leche", "unidad": "°C", "min": 0, "max": 40},
    {"clave": "temp_llegada", "label": "Temp. llegada", "unidad": "°C", "min": 0, "max": 40},
    {"clave": "temp_ambiente", "label": "Temp. ambiente", "unidad": "°C", "min": -10, "max": 45},
    {"clave": "hum_ambiente", "label": "Humedad ambiente", "unidad": "%", "min": 0, "max": 100},
    {"clave": "temp_lavado", "label": "Temp. lavado", "unidad": "°C", "min": 0, "max": 90},
    {"clave": "temp_sala_maquinas", "label": "Temp. sala máquinas", "unidad": "°C", "min": -10, "max": 50},
    {"clave": "temp_sala_tableros", "label": "Temp. sala tableros", "unidad": "°C", "min": -10, "max": 50},
    {"clave": "temp_sala_caldera", "label": "Temp. sala caldera", "unidad": "°C", "min": 0, "max": 80},
    {"clave": "temp_corral", "label": "Temp. corral", "unidad": "°C", "min": -10, "max": 45},
    {"clave": "hum_corral", "label": "Humedad corral", "unidad": "%", "min": 0, "max": 100},
    {"clave": "vacio_general", "label": "Vacío (a definir sectores)", "unidad": "kPa", "min": 0, "max": 60},
]


PREDEFINIDOS = [
    {"clave": "lavado_rotativa", "label": "Lavado de la rotativa", "tipo": "digital", "rol": "estado",
     "entrada": "lavado_rotativa", "estado_nombre": "LAVANDO", "texto_activo": "Lavando", "texto_inactivo": "Parado"},
    {"clave": "barrido_rotativa", "label": "Barrido de la rotativa", "tipo": "digital", "rol": "estado",
     "entrada": "barrido_rotativa", "estado_nombre": "BARRIDO", "texto_activo": "Barriendo", "texto_inactivo": "Parado"},
]


def _texto(valor, nombre, maximo) -> str:
    t = "" if valor is None else str(valor).strip()
    if len(t) > maximo:
        raise ValueError(f"«{nombre}» no puede tener más de {maximo} letras.")
    return t


def normalizar(s: dict) -> dict:
    """Valida UN sensor y lo deja con todos sus campos. `clave` queda como vino (puede faltar: la pone `guardar`)."""
    if not isinstance(s, dict):
        raise ValueError("Cada sensor tiene que ser un objeto.")
    label = _texto(s.get("label"), "Nombre", MAX_NOMBRE)
    if not label:
        raise ValueError("Todo sensor necesita un nombre.")
    tipo = s.get("tipo")
    if tipo not in ("analogico", "digital"):
        raise ValueError(f"{label}: el tipo tiene que ser «analogico» o «digital».")
    tamano = s.get("tamano") or "mediano"
    if tamano not in TAMANOS:
        raise ValueError(f"{label}: el tamaño tiene que ser «chico», «mediano» o «grande».")
    salida = {"clave": s.get("clave") or None, "label": label, "tipo": tipo, "tamano": tamano}
    if tipo == "analogico":
        minimo, maximo = _numero(s.get("min"), "Escala desde", label), _numero(s.get("max"), "Escala hasta", label)
        if minimo is None or maximo is None:
            raise ValueError(f"{label}: falta la escala del dial (desde y hasta).")
        if minimo >= maximo:
            raise ValueError(f"{label}: la escala tiene que ir de menor a mayor (desde {minimo} hasta {maximo}).")
        salida.update(unidad=_texto(s.get("unidad"), "Unidad", MAX_UNIDAD), min=minimo, max=maximo)
        return salida

    rol = s.get("rol") or "indicador"
    if rol not in ROLES:
        raise ValueError(f"{label}: la función tiene que ser «alarma», «indicador» o «estado».")
    entrada = s.get("entrada") or None
    if entrada is not None and entrada not in ENTRADAS:
        raise ValueError(f"{label}: la entrada {entrada!r} no existe en el gateway.")
    por_defecto_activo, por_defecto_inactivo = TEXTOS_DE_ROL[rol]
    texto_activo = _texto(s.get("texto_activo"), "Texto activo", MAX_TEXTO) or por_defecto_activo
    texto_inactivo = _texto(s.get("texto_inactivo"), "Texto inactivo", MAX_TEXTO) or por_defecto_inactivo
    estado_nombre = ""
    if rol == "alarma":
        color_activo, color_inactivo = "rojo", "verde"
        avisar = True if s.get("avisar") is None else bool(s.get("avisar"))
    elif rol == "estado":
        color_activo = color_inactivo = "neutro"      # un estado de equipo no es una alarma: sin color y sin aviso
        avisar = False
        estado_nombre = (_texto(s.get("estado_nombre"), "Nombre del estado", MAX_TEXTO) or label).upper()
        if estado_nombre in ESTADOS_RESERVADOS:
            raise ValueError(f"{label}: «{estado_nombre}» ya es un estado de la rotativa (ORDEÑO y APAGADO no se tocan): "
                             "elegí otro nombre.")
    else:
        color_activo, color_inactivo = s.get("color_activo") or "neutro", s.get("color_inactivo") or "neutro"
        for c in (color_activo, color_inactivo):
            if c not in COLORES:
                raise ValueError(f"{label}: el color {c!r} no existe (verde, amarillo, rojo o neutro).")
        avisar = bool(s.get("avisar"))
    salida.update(unidad="", rol=rol, entrada=entrada, invertido=bool(s.get("invertido")),
                  texto_activo=texto_activo, texto_inactivo=texto_inactivo,
                  color_activo=color_activo, color_inactivo=color_inactivo, avisar=avisar, estado_nombre=estado_nombre)
    return salida


def estado_de(s: dict, activo: bool):
    """(texto, color) de un sensor digital en ese estado. El color es 'verde'/'amarillo'/'rojo', o None si es neutro."""
    texto = s["texto_activo"] if activo else s["texto_inactivo"]
    color = s["color_activo"] if activo else s["color_inactivo"]
    return texto, (None if color == "neutro" else color)


def tiene_color(s: dict) -> bool:
    """¿Un sensor digital tiene algún estado con color (o sea que se lo puede comparar y contar)?"""
    return s["color_activo"] != "neutro" or s["color_inactivo"] != "neutro"


def estado_de_alarma(s: dict):
    """True si la alarma de un digital es cuando está ACTIVO, False si es cuando está inactivo, None si no tiene ninguna.
    Es el estado que el tambo pintó de rojo (el activo si están los dos)."""
    if s["color_activo"] == "rojo":
        return True
    return False if s["color_inactivo"] == "rojo" else None


def cargar() -> list:
    """La lista de sensores. Sin archivo, o roto, la de siempre: antes dejar el panel andando que fallar el ciclo del
    gateway. Una entrada inválida del archivo se saltea, no tira las demás."""
    try:
        with open(_RUTA, encoding="utf-8") as f:
            datos = json.load(f)
        lista = datos.get("sensores") if isinstance(datos, dict) else None
        if not isinstance(lista, list):
            raise ValueError("formato")
    except (OSError, ValueError):
        return [normalizar({**s, "tipo": "analogico"}) for s in DEFECTO] + [normalizar(s) for s in PREDEFINIDOS]
    salida = []
    for s in lista:
        try:
            n = normalizar(s)
        except ValueError:
            continue
        if n["clave"]:
            salida.append(n)
    if not datos.get("version"):
        salida += _predefinidos_que_faltan(salida)
    return salida


def _predefinidos_que_faltan(lista: list) -> list:
    """LAVANDO y BARRIDO para un archivo de antes de los estados, salvo que su clave o su entrada ya las tenga otro sensor."""
    claves = {s["clave"] for s in lista}
    entradas = {s.get("entrada") for s in lista if s["tipo"] == "digital" and s.get("entrada")}
    nombres = {s["label"].casefold() for s in lista}
    return [normalizar(p) for p in PREDEFINIDOS
            if p["clave"] not in claves and p["entrada"] not in entradas and p["label"].casefold() not in nombres]


def estados() -> list:
    """Los sensores que dan el estado general de la rotativa, en orden de prioridad (el primero activo manda)."""
    return [s for s in digitales() if s["rol"] == "estado"]


def analogicos() -> list:
    return [s for s in cargar() if s["tipo"] == "analogico"]


def digitales() -> list:
    return [s for s in cargar() if s["tipo"] == "digital"]


def _slug(label: str) -> str:
    t = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_") or "sensor"


def _clave_libre(label: str, usadas: set) -> str:
    base = _slug(label)
    clave, n = base, 2
    while clave in usadas or clave == CLAVE_RESERVADA:
        clave, n = f"{base}_{n}", n + 1
    return clave


def guardar(nueva: list, en_uso: set = None) -> None:
    """Valida y guarda la lista entera. `en_uso` son las claves asignadas a una entrada analógica: no se pueden sacar (se
    cortaría la lectura sin avisar). Un guardado que falla no toca lo que había."""
    if not isinstance(nueva, list):
        raise ValueError("La lista de sensores tiene que ser una lista.")
    if len(nueva) > MAX_SENSORES:
        raise ValueError(f"Hay demasiados sensores (el máximo son {MAX_SENSORES}).")
    existentes = {s["clave"]: s for s in cargar()}
    resultado, claves, nombres, entradas, estados_usados = [], set(), set(), {}, {}
    for crudo in nueva:
        s = normalizar(crudo)
        previo = existentes.get(s["clave"]) if s["clave"] else None
        if previo is None:
            s["clave"] = None                          # una clave que no es de ningún sensor existente se ignora
        elif previo["tipo"] != s["tipo"]:
            raise ValueError(f"{s['label']}: no se puede cambiar de analógico a digital (o al revés); agregá otro sensor.")
        elif s["clave"] in claves:
            s["clave"] = None                          # la misma clave dos veces: la segunda es un sensor nuevo
        if s["clave"]:
            claves.add(s["clave"])
        if s["label"].casefold() in nombres:
            raise ValueError(f"Hay dos sensores que se llaman «{s['label']}»: cambiale el nombre a uno.")
        nombres.add(s["label"].casefold())
        if s["tipo"] == "digital" and s["entrada"]:
            if s["entrada"] in entradas:
                raise ValueError(f"La entrada {s['entrada']} la tienen {entradas[s['entrada']]} y {s['label']}: "
                                 "cada entrada va a un solo sensor.")
            entradas[s["entrada"]] = s["label"]
        if s["tipo"] == "digital" and s["rol"] == "estado":
            if s["estado_nombre"] in estados_usados:
                raise ValueError(f"{estados_usados[s['estado_nombre']]} y {s['label']} dan el mismo estado "
                                 f"(«{s['estado_nombre']}»): cada estado de la rotativa tiene un nombre distinto.")
            estados_usados[s["estado_nombre"]] = s["label"]
        resultado.append(s)
    for s in resultado:                                # recién ahora, con todas las claves existentes ya reservadas
        if not s["clave"]:
            s["clave"] = _clave_libre(s["label"], claves)
            claves.add(s["clave"])
    sacados = [e for c, e in existentes.items() if c not in claves and c in (en_uso or set())]
    if sacados:
        raise ValueError(f"{sacados[0]['label']} está asignado a una entrada analógica: sacalo de ahí antes de borrarlo.")
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_RUTA), prefix=".iot_sensores_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION, "sensores": resultado}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _RUTA)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
