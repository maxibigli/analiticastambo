# -*- coding: utf-8 -*-
"""Configuración de un tambo, editable desde la página "⚙ Configuración".

Consolida en una sola pantalla lo que hoy vive repartido: conexión a la base
(`tambos.py`), tipo de sala, y hardware/proveedores opcionales que hasta ahora
no tenían dónde declararse (cámara BCS, sistema de actividad, caudalímetro,
usina láctea, proveedor de alimentación). NO reemplaza `tambos.py`: un tambo
tiene que existir ahí primero (id, database, rebaños) — esto sólo agrega
overrides opcionales para un tambo ya declarado. Ver `tambos._config_manual`.

CONTRASEÑA: a diferencia del resto de la app (que nunca guarda contraseñas en
archivo, sólo variables de entorno — ver `tambos.py`), ACÁ SÍ se guarda en
este JSON. Fue una decisión explícita del tambo (2026-07-28): la comodidad de
cargar todo desde una sola pantalla pesó más que la regla general. El archivo
queda igual fuera de git (ver `.gitignore`), como `sala_convencional.json` o
`parametros_reproductivos.json`.

`lados`/`puestos_por_lado`/`ventana_vivo_min` de una sala convencional NO se
duplican acá: siguen viviendo en `sala_convencional.json`/`configuracion()` —
la página de Configuración los lee y guarda ahí directo, para no tener dos
fuentes de verdad para lo mismo.
"""
import json
import os
import threading

_RUTA = os.path.join(os.path.dirname(__file__), "configuracion_tambos.json")
_lock = threading.Lock()

SISTEMAS_ACTIVIDAD = ["sin_sistema", "delaval_am2", "delaval_ba", "scr", "nedap"]
SISTEMAS_ACTIVIDAD_LABEL = {
    "sin_sistema": "Sin sistema", "delaval_am2": "Delaval AM2", "delaval_ba": "Delaval BA",
    "scr": "SCR", "nedap": "Nedap",
}
CAUDALIMETROS = ["sin_caudalimetro", "cicla", "haasten"]
CAUDALIMETROS_LABEL = {
    "sin_caudalimetro": "Sin caudalímetro", "cicla": "CICLA", "haasten": "Haasten",
}
USINAS_LACTEAS = ["sin_datos", "la_serenisima"]
USINAS_LACTEAS_LABEL = {"sin_datos": "Sin datos", "la_serenisima": "La Serenísima"}

# Mismos ids que espera `proveedores.de()` (ver proveedores/__init__.py).
SISTEMAS_ALIMENTACION = ["haasten", "delpro", "mixerone"]
SISTEMAS_ALIMENTACION_LABEL = {"haasten": "Haasten", "delpro": "DelPro", "mixerone": "Mixerone"}

# Mismos ids que espera `tambos.tipo_sala()`. "robot" queda afuera: no hay
# instalación real todavía contra la cual verificar el esquema (ver salas/).
SALAS = ["rotativa", "convencional"]
SALAS_LABEL = {"rotativa": "Rotativa", "convencional": "Convencional (espina de pescado)"}

# None/"" en cualquiera de estos campos = "no lo pisa, usa lo de tambos.py o
# el defecto de cada módulo". Es la clave de que esta config sea de puro
# OVERRIDE opcional, nunca obligatoria.
#
# `ruta_toros` y `ruta_precios` apuntan a los dos Excel que el tambo mantiene a
# mano porque su contenido NO ESTÁ EN NINGÚN SISTEMA CONECTADO:
#   ruta_toros    catálogo genético de los padres del rodeo. DelPro no lo tiene
#                 (`PedigreeIndex` está todo en 0.0 y `GeneticValue` vacío: este
#                 rodeo no tiene genotipado), así que sale del proveedor de
#                 genética. Ver genetica.py.
#   ruta_precios  precios de los insumos y de la leche. Los 70 ingredientes de
#                 Haasten tienen `price: 0` y la usina publica solo datos
#                 físicos. Es lo que habilita el costo y los litros libres.
#                 Ver precios_alimentos.py.
# Las dos aceptan un ARCHIVO o una CARPETA (ahí se buscan los nombres por
# defecto). Vacío = usar el directorio de la app, que es el comportamiento de
# siempre. No se valida al guardar: el tambo puede querer dejar la ruta puesta
# antes de copiar el archivo, y bloquear el guardado por eso sería molesto sin
# necesidad — la pantalla informa el estado con `estado_rutas()`.
_CAMPOS_TEXTO = ("nombre", "ip", "usuario", "contrasena", "ruta_toros", "ruta_precios",
                 "sensehub_ip")
_CAMPOS_BOOL = ("tiene_bcs", "tiene_podal")
_CAMPOS_ENUM = {
    "sistema_actividad": SISTEMAS_ACTIVIDAD,
    "caudalimetro": CAUDALIMETROS,
    "usina_lactea": USINAS_LACTEAS,
    "sistema_alimentacion": SISTEMAS_ALIMENTACION,
    "sala": SALAS,
}

# Campos enteros simples (no enum, no texto). Ninguno de estos sale de DDM
# (DelPro no tiene noción de personal ni de arreo), los carga el tambo acá:
#   personas   cuántas personas participan del ordeño -> "vacas por persona"
#   arreo_min  minutos que lleva arrear el rodeo desde su corral hasta el
#              corral de espera, POR SESIÓN (el tambo es estabulado, no hay
#              pastoreo). Es tiempo en que la vaca ya salió del corral pero
#              todavía no entró a la sala, así que no hay ningún registro en
#              la base: sin este dato las "horas/día en ordeño" quedan
#              subestimadas. Ver Rendimiento Sala.
#   umbral_prep_s  objetivo, en segundos, del tramo que va de la vaca entrando
#              a la sala hasta que empieza a bajar la leche. NO tiene un valor
#              universal: en la rotativa DelPro marca 90s y ahi se mide la
#              colocacion de la pezonera; en una sala convencional la vaca se
#              identifica AL ENTRAR, asi que el mismo tramo incluye la caminata
#              y la espera en el puesto y la mediana real esta en minutos (281s
#              en La Martina). Por eso lo pone el tambo y no el codigo: elegirlo
#              nosotros seria calificar a la sala contra si misma. Vacio = el
#              componente no se puntua. Ver `salas.convencional.UMBRAL_PREP_S`.
#   top_atencion  cuantas vacas listar en las tarjetas "Atencion" (clasico y
#              experimental) de Salud del rodeo. Vacio = 15 (salud.TOP_ATENCION).
#              Es un techo de PANTALLA, no cambia el calculo: subirlo no hace
#              el indice mas preciso, solo muestra mas candidatas de la misma
#              lista ya ordenada. Se puso configurable porque un rodeo grande
#              con muchos casos reales se queda corto con 15 fijas.
#   ordenos_dia  cuantos ordenes por dia hace el tambo (2 o 3). Sirve para
#              saber que dias estan INCOMPLETOS y sacarlos de las estadisticas
#              que promedian por dia: la copia de la base corta a mitad de un
#              dia, y ese dia a medias entraba al promedio con el mismo peso
#              que uno entero y lo tiraba abajo (medido en produccion el
#              31/08/2026: el 25/08 tenia UNA sesion de 68 min contra los
#              13,1-14,0 h de un dia normal). Un dia con menos sesiones que
#              este numero no se promedia -- se cuenta aparte y se avisa.
#              NO se deduce de `CMSGroupMilkSetting.NumberOfMilkings`: lo pone
#              el tambo, que es quien sabe su rutina. Vacio = no se filtra
#              nada (mismo comportamiento que antes de que esto existiera).
_CAMPOS_INT = ("puerto", "personas", "arreo_min", "umbral_prep_s", "top_atencion",
               "ordenos_dia")

DEFAULT = {
    "nombre": None, "ip": None, "puerto": None, "usuario": None, "contrasena": None,
    "tiene_bcs": False, "tiene_podal": False,
    "sistema_actividad": "sin_sistema",
    "caudalimetro": "sin_caudalimetro",
    "usina_lactea": "sin_datos",
    "sistema_alimentacion": None,
    "sala": None,
    "personas": None,
    "arreo_min": None,
    "umbral_prep_s": None,
    "top_atencion": None,
    "ordenos_dia": None,
    "ruta_toros": None,
    "ruta_precios": None,
    # IP del controlador SenseHub/Allflex en la red del tambo (ver
    # `sensehub.py`). Va acá y no como constante porque CAMBIA: los dos tambos
    # mirados tenían una IP distinta en el caché de 2025 que hoy. El usuario y
    # la contraseña NO se guardan acá: van por variable de entorno
    # (`SENSEHUB_USER_<TAMBO>` / `SENSEHUB_PWD_<TAMBO>`).
    "sensehub_ip": None,
}

# Nombres que se buscan cuando la ruta configurada es una CARPETA. Los de toros
# son varios a propósito y en este orden: el dato real le gana al simulado (ver
# genetica.RUTAS).
ARCHIVOS_TOROS = ("Toros.xlsx", "Padres_del_rodeo.xlsx", "Padres_del_rodeo_SIMULADO.xlsx")
ARCHIVOS_PRECIOS = ("Precios_alimentos.xlsx",)


def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def config_de(tambo_id: str) -> dict:
    """Config guardada del tambo, con los valores por defecto ya puestos.
    Todo en None/False = "no hay override", el llamador cae a tambos.py."""
    guardado = _leer().get(tambo_id) or {}
    return {**DEFAULT, **guardado}


def _resolver(ruta, nombres, dir_defecto) -> list:
    """Rutas de archivo a usar, en orden de prioridad.

    Acepta las tres formas en que alguien puede llenar el campo, sin obligarlo a
    saber cuál esperamos:
      * un ARCHIVO           -> ese archivo primero, y después los de siempre
                                (así se puede agregar un catálogo sin perder los
                                que ya estaban)
      * una CARPETA          -> los `nombres` que existan ahí
      * vacío / inexistente  -> los `nombres` del directorio de la app
    Devuelve solo rutas que EXISTEN, sin repetir.
    """
    salida = []
    if ruta:
        ruta = os.path.expanduser(os.path.expandvars(str(ruta).strip()))
        if os.path.isdir(ruta):
            salida += [os.path.join(ruta, n) for n in nombres]
        elif ruta:
            salida.append(ruta)
    salida += [os.path.join(dir_defecto, n) for n in nombres]
    vistas, out = set(), []
    for r in salida:
        real = os.path.abspath(r)
        if real not in vistas and os.path.exists(real):
            vistas.add(real)
            out.append(real)
    return out


def rutas_toros(tambo_id: str, dir_defecto: str = None) -> list:
    """Catálogos de toros a leer, en orden (el primero que traiga un toro gana)."""
    cfg = config_de(tambo_id)
    return _resolver(cfg.get("ruta_toros"), ARCHIVOS_TOROS,
                      dir_defecto or os.path.dirname(__file__))


def ruta_precios(tambo_id: str, dir_defecto: str = None):
    """Planilla de precios a leer, o None si no hay ninguna."""
    cfg = config_de(tambo_id)
    encontradas = _resolver(cfg.get("ruta_precios"), ARCHIVOS_PRECIOS,
                             dir_defecto or os.path.dirname(__file__))
    return encontradas[0] if encontradas else None


def estado_rutas(tambo_id: str) -> dict:
    """Qué archivos se van a usar de verdad, para mostrarlo en la pantalla de
    configuración. Se informa lo CONFIGURADO y lo ENCONTRADO por separado: una
    ruta escrita con un error de tipeo cae en silencio a los archivos por
    defecto, y sin verlo al lado uno cree que está usando la carpeta nueva."""
    cfg = config_de(tambo_id)
    toros = rutas_toros(tambo_id)
    precios = ruta_precios(tambo_id)
    return {
        "toros": {
            "configurada": cfg.get("ruta_toros"),
            "existe": bool(cfg.get("ruta_toros")) and os.path.exists(
                os.path.expanduser(os.path.expandvars(str(cfg["ruta_toros"]).strip()))),
            "archivos": [os.path.basename(r) for r in toros],
            "rutas": toros,
        },
        "precios": {
            "configurada": cfg.get("ruta_precios"),
            "existe": bool(cfg.get("ruta_precios")) and os.path.exists(
                os.path.expanduser(os.path.expandvars(str(cfg["ruta_precios"]).strip()))),
            "archivo": os.path.basename(precios) if precios else None,
            "ruta": precios,
        },
    }


def guardar(tambo_id: str, datos: dict) -> dict:
    """Guarda (mergea) la config del tambo. Ignora claves desconocidas en vez
    de fallar; valida los enum y el puerto."""
    actual = config_de(tambo_id)
    entrantes = {}
    for clave, valor in (datos or {}).items():
        if clave in _CAMPOS_TEXTO:
            entrantes[clave] = (str(valor).strip() or None) if valor is not None else None
        elif clave in _CAMPOS_INT:
            if valor in (None, ""):
                entrantes[clave] = None
            else:
                try:
                    entrantes[clave] = int(valor)
                except (TypeError, ValueError):
                    raise ValueError(f"Valor inválido para {clave}: {valor!r} (se espera un entero)")
        elif clave in _CAMPOS_BOOL:
            entrantes[clave] = bool(valor)
        elif clave in _CAMPOS_ENUM:
            opciones = _CAMPOS_ENUM[clave]
            if valor not in opciones and not (valor is None and clave in ("sistema_alimentacion", "sala")):
                raise ValueError(f"Valor inválido para {clave}: {valor!r} (opciones: {opciones})")
            entrantes[clave] = valor
        # claves desconocidas: se ignoran (URL manual mal armada no rompe nada)

    nueva = {**actual, **entrantes}
    with _lock:
        todo = _leer()
        todo[tambo_id] = nueva
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
    return nueva
