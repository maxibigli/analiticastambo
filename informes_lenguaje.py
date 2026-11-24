"""Armar un informe a partir de una frase (Fase 4b de los informes personalizables).

El administrador escribe qué informe quiere («vacas del rodeo 3 con más de 200 días en ordeño, ordenadas por producción») y una IA (el mismo
Claude del agente) lo traduce a la DEFINICIÓN de un informe. La definición se abre en una pestaña nueva, SIN guardar y SIN ejecutarse sola: el
usuario revisa lo que se entendió y recién ahí toca «Actualizar».

LA IA NO DECIDE NADA DE SEGURIDAD. Lo único que devuelve es una definición (JSON con un esquema cerrado) que pasa por
`informes_guardados.validar_definicion`, o sea por `informes_motor.compilar`: campo y operador salen de listas cerradas y los valores se escriben
como literales validados. Un campo inventado, un valor con SQL o una clave de más no llegan a ningún lado: se rechazan y se le devuelve el motivo a
la IA UNA vez. El modelo no escribe SQL ni ve SQL: ve el catálogo de campos (id, nombre, tipo, unidad), los valores posibles de un campo cerrado (el estado de
la reproducción) y la frase; ningún dato de los animales del tambo.

Lo que se le muestra al usuario («Se lee así») lo arma el servidor desde la definición ya validada, no lo que escriba el modelo; el texto libre del
modelo (supuestos, lo que no pudo) va acotado y limpio. Si hay un campo sin verificar, un número o una fecha que no figuran en la frase (o, al revés, algo
de la frase que no llegó al informe), un informe sin condiciones o un límite contra totales, se AVISA (no se rechaza: es una advertencia, como las del
gráfico del agente).

Costo y falla: UNA llamada (más una repetición con el motivo, si la definición no pasó la validación), con tiempo límite, y un tope de pedidos por
hora (un pedido que falla antes de que la API acepte el trabajo devuelve su turno). Sin clave de API, con la IA caída o lenta, o con cualquier respuesta
rara, el diseñador manual de informes sigue andando igual: nada de esto es necesario para armar un informe.
"""
import calendar
import datetime
import json
import logging
import math
import re
import threading
import time
import unicodedata

import informes_catalogo
import informes_guardados
import informes_motor

log = logging.getLogger("informes_lenguaje")

FRASE_MAX = 500
MAX_TOKENS = 1500
TIMEOUT_S = 45                                   # leer la respuesta
CONEXION_S = 5                                   # conectar: con internet intermitente no se espera 45 s para enterarse de que no hay
PEDIDOS_POR_HORA = 20
TEXTOS_MAX = 8
TEXTO_LARGO_MAX = 240
NOMBRE_NEUTRO = "Informe armado con IA"
# Lo que se dice cuando una línea del modelo con «ese día» o «el día anterior» no se puede mostrar (regla: nunca un día relativo): la pantalla pinta «Revisalo»
# cuando hay algo en `no_pude` o en las advertencias, así que sacar la línea en silencio dejaba un «Listo para revisar» sobre un pedido a medio armar.
SUPUESTO_SOBRE_UN_DIA = "Hay un supuesto sobre un día que no se pudo mostrar: revisá las fechas de las condiciones."
PARTE_SIN_ARMAR = "Una parte del pedido no se pudo armar con los campos disponibles: revisá lo que escribiste."
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

_CLAVES = ("se_puede", "nombre", "columnas", "agrupar_por", "condiciones", "orden_campo", "orden_dir", "incluir_descartados", "solo_resumen", "limite",
           "supuestos", "lo_que_no_se_pudo")
_CLAVES_COLUMNA = ("campo", "resumen")
_CLAVES_CONDICION = ("campo", "op", "valor", "valores")
# «1.200» y «1,200» son 1200 con separador de miles: la IA los escribe sin él. Un grupo de miles no empieza con 0 («0,250» y «0.125» son decimales de verdad).
_MILES = re.compile(r"^-?[1-9]\d{0,2}(?:[.,]\d{3})+$")
# Los nombres y los textos se miran SIN tildes (el texto se pasa por `_sin_tildes`): «ese dia», «mañana» y «manana» son lo mismo.
# El nombre de un informe viaja en el asunto de cada envío programado y las fechas del informe quedan fijas: ni un día relativo («hoy», «ayer») ni un período relativo
# («últimos 30 días», «mes pasado», «hace 30 días») sirven, porque el nombre envejece. «La mañana» es un momento del ordeño, no un día relativo.
_DIA_RELATIVO_EN_NOMBRE = re.compile(
    r"\b(?:hoy|ayer|anteayer|este mes|esta semana|esta quincena|este ano|ese dia|esa manana|esta manana|el dia anterior"
    r"|ultim[oa]s?\s+(?:\d+\s+|[a-z]+\s+)?(?:dias?|semanas?|mes(?:es)?|anos?|quincenas?)"
    r"|(?:mes|semana|quincena|ano)\s+(?:pasad[oa]|anterior|actual|en\s+curso)"
    r"|hace\s+(?:\d+|[a-z]+)\s+(?:dias?|semanas?|mes(?:es)?|anos?)"
    r"|recientes?)\b|(?<!la )\bmanana\b", re.I)
# Un día que el servidor no sabe cuál es (a «ayer» y «anteayer» sí los conoce: `_con_fecha` los escribe con su fecha antes de llegar acá).
_DIA_RELATIVO_EN_TEXTO = re.compile(
    r"\b(?:ayer|anteayer|(?:ese|aquel)\s+(?:mismo\s+)?dia|d?el\s+mismo\s+dia|d?el\s+dia\s+(?:anterior|previo|siguiente)|al\s+dia\s+siguiente|esa\s+manana)\b", re.I)


class InformeIAError(ValueError):
    """No se pudo armar el informe. El mensaje se le muestra al usuario (nunca lleva detalles de la API ni del servidor)."""


class SinIA(InformeIAError):
    """No hay clave de la API de Claude en este servidor."""


class IAFallo(InformeIAError):
    """La API de Claude falló, tardó o devolvió algo que no se puede usar."""


class TopeDePedidos(InformeIAError):
    """Demasiados pedidos en la última hora."""


class _Corregible(Exception):
    """La respuesta de la IA no sirvió, pero el motivo se le puede devolver para que la corrija (una vez)."""


# ============================================================================================ el tope de pedidos

_PEDIDOS: list = []
_LOCK = threading.Lock()


def reiniciar_tope() -> None:
    with _LOCK:
        _PEDIDOS.clear()


def _tomar_turno(reloj) -> float:
    """Anota un pedido en la última hora y devuelve su marca (para devolver el turno si el pedido falla antes de llegar a la API)."""
    ahora = reloj()
    with _LOCK:
        while _PEDIDOS and ahora - _PEDIDOS[0] > 3600:
            _PEDIDOS.pop(0)
        if len(_PEDIDOS) >= PEDIDOS_POR_HORA:
            # El turno más viejo sale de la ventana cuando pasan MÁS de 3.600 s desde que se tomó: «unos minutos» podían ser 59.
            minutos = max(1, math.ceil((3600 - (ahora - _PEDIDOS[0])) / 60))
            raise TopeDePedidos(f"Hiciste muchos pedidos seguidos (hasta {PEDIDOS_POR_HORA} por hora). Podés hacer otro pedido en {minutos} "
                                f"{'minuto' if minutos == 1 else 'minutos'}: esperá o armá el informe a mano.")
        _PEDIDOS.append(ahora)
    return ahora


def _devolver_turno(marca: float) -> None:
    """Un pedido que se SABE que la API no aceptó (no hubo conexión, 429, 5xx) no gasta uno de los turnos de la hora."""
    with _LOCK:
        try:
            _PEDIDOS.remove(marca)
        except ValueError:                                                     # ya salió de la ventana de una hora
            pass


def _no_llego_a_la_api(exc) -> bool:
    """True si el error dice que la API NO aceptó el trabajo: no se pudo conectar, o contestó enseguida con un 429 o un 5xx (la sobrecarga es un 529). Un tiempo
    agotado NO cuenta (pudo estar trabajando del otro lado), ni un error que no se reconoce: ante la duda el turno se gasta, que es lo que protege el costo."""
    try:
        import anthropic
        if isinstance(exc, anthropic.APITimeoutError):
            return False
        if isinstance(exc, anthropic.APIConnectionError):
            return True
    except Exception:  # noqa: BLE001
        pass
    estado = getattr(exc, "status_code", None)
    return isinstance(estado, int) and not isinstance(estado, bool) and (estado == 429 or estado >= 500)


# ============================================================================================ lo que se le manda a la IA

def _dia_largo(d: datetime.date) -> str:
    return f"{DIAS[d.weekday()]} {d:%d/%m/%Y}"


def _catalogo(catalogo):
    return informes_catalogo.CATALOGO if catalogo is None else catalogo


def _operadores() -> list:
    ops: list = []
    for tipo in ("num", "fecha", "txt"):
        for o in informes_motor.OPERADORES[tipo]:
            if o not in ops:
                ops.append(o)
    return ops


def _resumenes() -> list:
    kinds: list = []
    for tipo in ("num", "fecha", "txt"):
        for k in informes_motor.RESUMENES[tipo]:
            if k not in kinds:
                kinds.append(k)
    return kinds


def esquema(catalogo=None) -> dict:
    """El formato de la respuesta de la IA (salida estructurada): cerrado, con los ids del catálogo como enumeración. Los topes (columnas,
    condiciones...) NO se pueden imponer acá: los hace cumplir el compilador."""
    catalogo = _catalogo(catalogo)
    ids = list(catalogo)
    agrupables = [k for k, f in catalogo.items() if f.agrupable]
    lista_de_texto = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "se_puede": {"type": "boolean", "description": "false si con los campos disponibles no se puede armar un informe útil de este pedido."},
            "nombre": {"type": "string", "description": "Nombre corto del informe (hasta 60 letras), sin fechas ni períodos relativos como hoy, ayer, este mes, últimos 30 días o mes pasado: si el pedido es "
                                                        "por período, la fecha fija."},
            "columnas": {"type": "array", "description": "Las columnas del informe, en orden.", "items": {
                "type": "object",
                "properties": {"campo": {"type": "string", "enum": ids},
                               "resumen": {"type": "string", "enum": ["ninguno"] + _resumenes(), "description": "Resumen de la columna sobre todos los animales, o ninguno."}},
                "required": list(_CLAVES_COLUMNA), "additionalProperties": False}},
            "agrupar_por": {"type": "array", "description": "Hasta dos campos agrupables, o vacío.", "items": {"type": "string", "enum": agrupables}},
            "condiciones": {"type": "array", "description": "Condiciones que se cumplen todas a la vez, o vacío.", "items": {
                "type": "object",
                "properties": {"campo": {"type": "string", "enum": ids}, "op": {"type": "string", "enum": _operadores()},
                               "valor": {"type": "string", "description": "El valor, como texto. Vacío si el operador es en."},
                               "valores": {**lista_de_texto, "description": "Solo para el operador en (es uno de): la lista de valores. Vacío en los demás."}},
                "required": list(_CLAVES_CONDICION), "additionalProperties": False}},
            "orden_campo": {"type": "string", "enum": ids + [""], "description": "Una columna del informe por la que se ordena la lista, o vacío."},
            "orden_dir": {"type": "string", "enum": ["asc", "desc"], "description": "asc de menor a mayor, desc de mayor a menor (se ignora si orden_campo está vacío)."},
            "incluir_descartados": {"type": "boolean", "description": "true solo si piden también animales que ya salieron del rebaño."},
            "solo_resumen": {"type": "boolean", "description": "true si piden solo totales o promedios por grupo, sin la lista de animales."},
            "limite": {"type": "integer", "description": "Cuántas filas de la lista como máximo; 0 = sin límite."},
            "supuestos": {**lista_de_texto, "description": "Lo que decidiste vos y el usuario no dijo, una frase corta cada uno."},
            "lo_que_no_se_pudo": {**lista_de_texto, "description": "Lo que pidió el usuario y no se pudo armar con los campos disponibles."},
        },
        "required": list(_CLAVES),
        "additionalProperties": False,
    }


def _linea_de_campo(f) -> str:
    tipo = {"num": "número", "txt": "texto", "fecha": "fecha"}[f.tipo]
    extras = ([f"unidad {f.unidad}"] if f.unidad else []) + (["agrupable"] if f.agrupable else []) + ([] if f.verificado else ["sin verificar"])
    if f.valores:
        extras.append("valores posibles: " + ", ".join(f.valores))               # un texto cerrado: sin esto el modelo adivina el literal («Gestante», «Vacía»)
    return f"- {f.id} · {f.etiqueta} · {tipo}" + (f" · {', '.join(extras)}" if extras else "")


def sistema(hoy: datetime.date, catalogo=None) -> str:
    """El prompt: las reglas y el catálogo de campos (id, nombre, tipo, unidad), generado desde el catálogo real. Sin SQL ni notas internas; del tambo solo lleva los
    valores posibles de un campo cerrado (el estado de la reproducción) y los ejemplos de las reglas son inventados."""
    catalogo = _catalogo(catalogo)
    campos = "\n".join(_linea_de_campo(f) for f in catalogo.values())
    res = informes_motor.RESUMENES
    ops = informes_motor.OPERADORES
    return (
        "Sos el asistente que arma informes de LactIA, una aplicación de gestión de un tambo lechero argentino. Un administrador te escribe en español qué "
        "informe quiere; tu único trabajo es traducir ese pedido a la definición de un informe usando SOLO los campos de la lista de abajo. No escribís "
        "consultas ni código: devolvés la definición en el formato pedido.\n\n"
        f"Hoy es {_dia_largo(hoy)} ({hoy.isoformat()}).\n\n"
        f"CAMPOS DISPONIBLES (id · nombre · tipo · notas):\n{campos}\n\n"
        "CÓMO SE ARMA UN INFORME\n"
        f"- columnas: los campos que se ven, en orden (hasta {informes_motor.COLUMNAS_MAX}). Cada columna puede llevar un resumen que se calcula sobre todos los "
        f"animales del informe: sum (suma), avg (promedio), min (mínimo), max (máximo), count (conteo); usá \"ninguno\" si no hace falta. Admitidos: números "
        f"{', '.join(res['num'])}; fechas {', '.join(res['fecha'])}; textos {', '.join(res['txt'])}.\n"
        f"- agrupar_por: hasta {informes_motor.AGRUPAR_MAX} campos marcados «agrupable» (por ejemplo el rodeo o el número de lactancia).\n"
        f"- condiciones: hasta {informes_motor.CONDICIONES_MAX}, todas se cumplen a la vez. Operadores: números {', '.join(ops['num'])}; fechas {', '.join(ops['fecha'])}; "
        f"textos {', '.join(ops['txt'])}. El operador \"en\" significa «es uno de» y lleva la lista en \"valores\" (hasta {informes_motor.LISTA_MAX}); los demás llevan "
        "un solo \"valor\". Las fechas se escriben AAAA-MM-DD.\n"
        "- orden: una columna del informe, asc (de menor a mayor) o desc (de mayor a menor).\n"
        "- incluir_descartados: solo si piden también los animales que ya salieron del rebaño.\n"
        "- solo_resumen: true si piden solamente totales o promedios por grupo, sin la lista de animales.\n"
        "- limite: cuántas filas de la lista como máximo (0 = sin límite); los totales y resúmenes siempre cuentan a TODOS los animales.\n\n"
        "REGLAS\n"
        "1. Usá únicamente los ids de la lista. Si hace falta un dato que no está (por ejemplo células somáticas, mastitis, tratamientos, peso), NO lo "
        "reemplaces por uno parecido: armá el informe con lo que sí se puede y anotá lo que falta en lo_que_no_se_pudo; si no se puede armar nada útil, poné "
        "se_puede en false y explicá por qué. Un campo de texto con «valores posibles» solo admite esos valores, escritos tal cual.\n"
        "2. No existe un campo «en ordeño». Si piden las vacas en ordeño, usá SevenDayAverage > 0 (tuvo producción en los últimos 7 días) y anotalo en supuestos.\n"
        "3. «Más de N» es >, «al menos N» o «N o más» es >=, «menos de N» es <, «hasta N» es <=.\n"
        "4. Un rodeo por número: AnimalGroupNumber. Por nombre: AnimalGroupName con es, escrito como lo llama DelPro («Rodeo 3», «Rodeo 5 - Alta»). Si dicen "
        "«rodeo 3» sin más, preferí el número.\n"
        "5. Los números van tal cual los dijo el usuario, sin convertir unidades, sin separador de miles y con punto decimal (1200, 30.5). Si un número no está "
        "en el pedido, no lo inventes.\n"
        "6. Para fechas relativas («este mes», «los últimos 30 días») calculá la fecha con el día de hoy y anotá el supuesto. Para los días en ordeño usá CurrentDIM.\n"
        "7. Los campos marcados «sin verificar» todavía no se compararon con DelPro: podés usarlos si el pedido los necesita.\n"
        "8. nombre: corto (hasta 60 letras), descriptivo, sin fechas ni períodos relativos (hoy, ayer, mañana, este mes, últimos 30 días, mes pasado, semana pasada, "
        f"hace 30 días): el nombre envejece pero las fechas del informe quedan fijas, así que si el pedido es por período usá la fecha («Partos desde {_dia_largo(hoy - datetime.timedelta(days=30))}»).\n"
        "9. Escribí los textos para el usuario en español rioplatense (voseo). Si nombrás un día, usá la fecha con el día de la semana (jue 27/08/2026); nunca "
        "«ayer», «ese día» ni «el día anterior». Al rodeo llamalo «rodeo», con el nombre que le da DelPro.\n"
        "10. supuestos: lo que decidiste vos y el usuario no dijo, una frase corta cada uno.\n"
        "11. Un informe sin condiciones trae todos los animales del rebaño, también terneras, secas y recría: si el pedido es de «las vacas» sin más, anotá en "
        "supuestos qué filtro usaste, o que no usaste ninguno."
    )


# ============================================================================================ lo que vuelve de la IA

def _frase(frase) -> str:
    if not isinstance(frase, str):
        raise InformeIAError("Escribí qué informe querés.")
    limpia = re.sub(r"\s+", " ", frase)
    limpia = "".join(c for c in limpia if ord(c) >= 32).strip()
    if not limpia:
        raise InformeIAError("Escribí qué informe querés.")
    if len(limpia) > FRASE_MAX:
        raise InformeIAError(f"Es muy largo: resumilo en {FRASE_MAX} letras o menos.")
    return limpia


def _hoy(hoy) -> datetime.date:
    if hoy is None or hoy == "":
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(str(hoy))
    except ValueError:
        raise InformeIAError("La fecha de «Calcular al» no es válida.") from None


def _texto_de(resp) -> str:
    """El texto de la respuesta. Una respuesta cortada por largo o rechazada NO se interpreta ni se repite: no hay definición."""
    parada = getattr(resp, "stop_reason", None)
    if parada == "max_tokens":
        raise IAFallo("La respuesta de la IA salió cortada. Probá con un pedido más corto o armá el informe a mano.")
    if parada == "refusal":
        raise IAFallo("La IA no quiso armar ese informe. Probá decirlo de otra forma o armalo a mano.")
    return "".join(getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text").strip()


def _leer(crudo: str) -> dict:
    try:
        bruto = json.loads(crudo)
    except (ValueError, TypeError):
        raise _Corregible("La respuesta no es un JSON válido con el formato pedido.") from None
    if not isinstance(bruto, dict):
        raise _Corregible("La respuesta tiene que ser un objeto JSON con el formato pedido.")
    if set(bruto) != set(_CLAVES):
        raise _Corregible("La respuesta tiene claves de más o de menos: tiene que traer exactamente " + ", ".join(_CLAVES) + ".")
    for k in ("columnas", "agrupar_por", "condiciones", "supuestos", "lo_que_no_se_pudo"):
        if not isinstance(bruto[k], list):
            raise _Corregible(f"«{k}» tiene que ser una lista.")
    for k in ("se_puede", "incluir_descartados", "solo_resumen"):
        if not isinstance(bruto[k], bool):
            raise _Corregible(f"«{k}» tiene que ser verdadero o falso.")
    for k in ("nombre", "orden_campo", "orden_dir"):
        if not isinstance(bruto[k], str):
            raise _Corregible(f"«{k}» tiene que ser un texto.")
    if isinstance(bruto["limite"], bool) or not isinstance(bruto["limite"], int):
        raise _Corregible("«limite» tiene que ser un número entero.")
    if bruto["orden_dir"] not in ("asc", "desc"):
        raise _Corregible("«orden_dir» tiene que ser asc (de menor a mayor) o desc (de mayor a menor).")
    resumenes = ["ninguno"] + _resumenes()
    for c in bruto["columnas"]:
        if not isinstance(c, dict) or set(c) != set(_CLAVES_COLUMNA):
            raise _Corregible("Cada columna tiene que traer exactamente campo y resumen.")
        if not isinstance(c["campo"], str):
            raise _Corregible("El campo de cada columna tiene que ser un texto.")
        if c["resumen"] not in resumenes:
            raise _Corregible(f"El resumen de una columna tiene que ser uno de: {', '.join(resumenes)}.")
    operadores = _operadores()
    for c in bruto["condiciones"]:
        if not isinstance(c, dict) or set(c) != set(_CLAVES_CONDICION):
            raise _Corregible("Cada condición tiene que traer exactamente campo, op, valor y valores.")
        if not isinstance(c["campo"], str):
            raise _Corregible("El campo de cada condición tiene que ser un texto.")
        if c["op"] not in operadores:
            raise _Corregible(f"El operador de una condición tiene que ser uno de: {', '.join(operadores)}.")
        if not isinstance(c["valor"], str) or not isinstance(c["valores"], list) or not all(isinstance(v, str) for v in c["valores"]):
            raise _Corregible("En cada condición, «valor» es un texto y «valores» una lista de textos.")
    if not all(isinstance(x, str) for x in bruto["agrupar_por"] + bruto["supuestos"] + bruto["lo_que_no_se_pudo"]):
        raise _Corregible("Las listas tienen que ser de textos.")
    return bruto


def _con_fecha(texto: str, dia: datetime.date) -> str:
    """«ayer», «anteayer» y «antes de ayer» son una fecha que el servidor SÍ conoce: se escribe con el día de la semana (regla del usuario). «Ese día» o «el día
    anterior» no. «Antes de ayer» se cambia ANTES que «ayer»: si no, quedaría «antes de mié 26/08/2026» (un «antes de» abierto sobre otro día)."""
    texto = re.sub(r"\b(?:anteayer|antes\s+de\s+ayer)\b", _dia_largo(dia - datetime.timedelta(days=2)), texto, flags=re.I)
    return re.sub(r"\bayer\b", _dia_largo(dia - datetime.timedelta(days=1)), texto, flags=re.I)


def _textos(lista, dia: datetime.date, neutra: str) -> list:
    """El texto libre del modelo: limpio, corto, con las fechas escritas con su día de la semana y con tope de cantidad. Una línea con «ese día» o «el día anterior»
    no se puede mostrar (no se sabe qué día es), pero NO desaparece sin rastro: queda UNA línea `neutra` para que el usuario sepa que había algo más."""
    out, sacadas = [], 0
    for t in lista:
        s = "".join(c for c in re.sub(r"\s+", " ", str(t)) if ord(c) >= 32).strip()
        if not s:
            continue
        s = _con_fecha(s, dia)
        if _DIA_RELATIVO_EN_TEXTO.search(_sin_tildes(s)):
            sacadas += 1
        elif len(out) < TEXTOS_MAX:
            out.append(s if len(s) <= TEXTO_LARGO_MAX else s[:TEXTO_LARGO_MAX - 1].rstrip() + "…")
    if sacadas:
        out = out[:TEXTOS_MAX - 1] + [neutra]
    return out


def _nombre(nombre: str) -> str:
    limpio = "".join(c for c in re.sub(r"\s+", " ", nombre) if ord(c) >= 32).strip()
    if _DIA_RELATIVO_EN_NOMBRE.search(_sin_tildes(limpio)):
        return NOMBRE_NEUTRO
    try:
        return informes_guardados.normalizar_nombre(limpio)[0]
    except informes_guardados.InformeGuardadoError:
        return NOMBRE_NEUTRO


def _numero(texto):
    try:
        return float(str(texto).strip().replace(",", "."))
    except ValueError:
        return None


def _parte_de_un_valor(texto: str, valores):
    """Lo que el valor posible dice, escrito como lo tiene el catálogo, si `texto` (sin tildes ni mayúsculas) es parte de alguno; si no, None. «pren» da «Preñ»: la
    consulta busca con la letra que el campo realmente lleva, no con la que escribió el modelo."""
    buscado = _sin_tildes(str(texto)).lower().strip()
    if not buscado:
        return None
    for v in valores:
        plano = _sin_tildes(v).lower()
        i = plano.find(buscado)
        if i >= 0:
            return v[i:i + len(buscado)] if len(plano) == len(v) else str(texto).strip()
    return None


def _definicion(bruto: dict, catalogo) -> dict:
    """La definición de LactIA a partir de la respuesta. Las condiciones que la IA arma mal (un valor donde va una lista, un número con separador de
    miles) se le devuelven como error para que las corrija."""
    defn = {"columnas": [{"campo": c["campo"], **({} if c["resumen"] == "ninguno" else {"resumen": c["resumen"]})} for c in bruto["columnas"]],
            "incluir_descartados": bruto["incluir_descartados"]}
    if bruto["agrupar_por"]:
        defn["agrupar_por"] = list(bruto["agrupar_por"])
    condiciones = []
    for c in bruto["condiciones"]:
        campo = catalogo.get(c["campo"])
        if campo is None:
            raise _Corregible(f"El campo «{c['campo'][:40]}» no existe en el catálogo.")
        if c["op"] == informes_motor.OPERADOR_LISTA:
            if c["valor"].strip():                                             # no se descarta uno en silencio: «rodeo 3 o 4» no puede quedar como «= 3»
                raise _Corregible(f"«{campo.etiqueta}»: con el operador en (es uno de) la lista va en «valores» y «valor» tiene que quedar vacío.")
            valor = [v.strip() for v in c["valores"] if v.strip()]
            if not valor:
                raise _Corregible(f"«{campo.etiqueta}»: el operador en (es uno de) necesita la lista de valores en «valores».")
            items = valor
        else:
            if any(v.strip() for v in c["valores"]):
                raise _Corregible(f"«{campo.etiqueta}»: con el operador {c['op']} va un solo «valor» y «valores» tiene que quedar vacío "
                                  "(para varios valores usá el operador en).")
            valor = c["valor"].strip()
            if not valor:
                raise _Corregible(f"«{campo.etiqueta}»: falta el valor de la condición.")
            items = [valor]
        if campo.tipo == "num":
            for v in items:
                if _MILES.match(v.strip()):
                    raise _Corregible(f"El número «{v}» lleva separador de miles: escribilo sin él (por ejemplo 1200 y no 1.200).")
        if campo.valores and c["op"] == "contiene":                            # un texto cerrado: lo que se busca tiene que ser parte de algún valor posible
            partes = [_parte_de_un_valor(v, campo.valores) for v in items]
            for v, p in zip(items, partes):
                if p is None:
                    raise _Corregible(f"«{campo.etiqueta}»: «{v[:40]}» no es parte de ningún valor posible. Los valores posibles son: {', '.join(campo.valores)}.")
            items = partes
            valor = items[0]
        elif campo.valores:                                                    # un texto cerrado (el estado de la reproducción): solo esos valores, con las mayúsculas del catálogo
            canon = {x.casefold(): x for x in campo.valores}
            for v in items:
                if v.casefold() not in canon:
                    raise _Corregible(f"«{campo.etiqueta}»: «{v[:40]}» no es un valor posible. Los valores posibles son: {', '.join(campo.valores)}.")
            items = [canon[v.casefold()] for v in items]
            valor = items if isinstance(valor, list) else items[0]
        condiciones.append({"campo": c["campo"], "op": c["op"], "valor": valor})
    if condiciones:
        defn["condiciones"] = condiciones
    if bruto["orden_campo"]:
        defn["orden"] = {"campo": bruto["orden_campo"], "dir": -1 if bruto["orden_dir"] == "desc" else 1}
    if bruto["solo_resumen"]:
        defn["detalle"] = False
    if bruto["limite"]:
        defn["limite"] = bruto["limite"]
    try:
        return informes_guardados.validar_definicion(defn)
    except informes_guardados.InformeGuardadoError as e:
        raise _Corregible(str(e)) from None


def _se_lee(defn: dict, catalogo) -> list:
    por_id = {k: f.etiqueta for k, f in catalogo.items()}
    cols = []
    for c in defn["columnas"]:
        r = c.get("resumen")
        cols.append(por_id.get(c["campo"], c["campo"]) + (f" ({informes_motor.NOMBRE_RESUMEN[r].lower()})" if r else ""))
    lineas = ["Columnas: " + ", ".join(cols)]
    lineas += informes_motor.describir_condiciones(defn, catalogo, con_dia=True)
    if defn.get("orden"):
        o = defn["orden"]
        lineas.append(f"Ordenado por {por_id.get(o['campo'], o['campo'])}, de {'menor a mayor' if o['dir'] == 1 else 'mayor a menor'}")
    if defn.get("detalle") is False:
        lineas.append("Solo el resumen (sin la lista de animales)")
    if defn.get("limite"):
        lineas.append("Se lista la primera fila" if defn["limite"] == 1 else f"Se listan las primeras {defn['limite']} filas")
    return lineas


# ============================================================================================ los números de la frase

_PALABRAS_UNIDAD = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
                    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
                    "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
                    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_PALABRAS_DECENA = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_PALABRAS_CENTENA = {"cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
                     "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700, "setecientas": 700,
                     "ochocientos": 800, "ochocientas": 800, "novecientos": 900, "novecientas": 900}
def _ordinales() -> dict:
    """«primera», «segundos», «sexta»... hasta «décimo» (sin tildes), masculino y femenino, singular y plural."""
    out = {"primer": 1, "tercer": 3}
    for raiz, n in (("primer", 1), ("segund", 2), ("tercer", 3), ("cuart", 4), ("quint", 5), ("sext", 6), ("septim", 7), ("octav", 8), ("noven", 9), ("decim", 10)):
        for fin in ("o", "a", "os", "as"):
            out[raiz + fin] = n
    return out


_ORDINALES = _ordinales()
_IMPLICITOS = {"primipara": 1, "primiparas": 1, "multipara": 2, "multiparas": 2}                  # «multíparas» = de segunda lactancia en adelante
_UNIDADES_DE_TIEMPO = {"dia", "dias", "semana", "semanas", "mes", "meses", "ano", "anos", "hora", "horas", "hs", "minuto", "minutos"}
# Los plazos que una FECHA puede explicar («30 días» = hoy menos 30): el resto («12 horas») no se vuelve una fecha nunca.
_UNIDAD_DE_PLAZO = {"dia": "dia", "dias": "dia", "semana": "semana", "semanas": "semana", "mes": "mes", "meses": "mes", "ano": "ano", "anos": "ano"}
_MESES_NUM = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
              "noviembre": 11, "diciembre": 12}
_MESES = "|".join(_MESES_NUM)
_MARCA_ORDINAL = r"\s*(?:°|º|ro|er|o)?"                                                           # «1°», «1º», «1ro», «1er»
# Las fechas que la frase escribe, ya sin tildes y en minúsculas: AAAA-MM-DD, d/m/aaaa (con barra, punto o guión), «del 1 al 15 de agosto», «1 de agosto [de 2026]»
# (también «1°», «1ro» y «primero»), «agosto de 2026», «15/08» (con barra y sin año) y un mes suelto («en julio»). Se usa para sacarlas de la lectura de números
# (sus cifras no son cantidades) y para saber qué fecha está diciendo la frase.
_FECHAS_EN_LA_FRASE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<dmy_d>\d{1,2})[/.-](?P<dmy_m>\d{1,2})[/.-](?P<dmy_a>\d{4}|\d{2})(?!\d)"
    rf"|(?P<ra>\d{{1,2}})\s+(?:y|al?|hasta)\s+(?:el\s+)?(?P<rb>\d{{1,2}}){_MARCA_ORDINAL}\s+de\s+(?P<rmes>{_MESES})(?:\s+(?:de\s+|del\s+)?(?P<ranio>(?:19|20)\d{{2}}))?"
    rf"|(?P<d>\d{{1,2}}|primero){_MARCA_ORDINAL}\s+de\s+(?P<mes>{_MESES})(?:\s+(?:de\s+|del\s+)?(?P<anio>(?:19|20)\d{{2}}))?"
    rf"|(?P<mes2>{_MESES})\s+(?:de\s+|del\s+)?(?P<anio2>(?:19|20)\d{{2}})"
    r"|(?P<dm_d>\d{1,2})/(?P<dm_m>\d{1,2})(?![\d/])"
    rf"|(?P<mes3>{_MESES})"
    r")(?![a-z0-9])")
_ANIO_SUELTO = re.compile(r"(?<![a-z0-9])(?:19|20)\d{2}(?![a-z0-9])")
# «rodeos 2/3», «grupo 1/2»: una lista de números, no el 2 de marzo.
_LISTA_CON_BARRA = re.compile(r"\b(rodeos?|grupos?|lactancias?|numeros?|nros?|puestos?)(\s+)(\d{1,2}(?:/\d{1,2})+)(?![\d/])")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+")
# «la vaca que más produce», «el animal con más días», «la que más»: pide UNA fila sin decir «1».
_UNO_IMPLICITO = re.compile(r"\b(?:(?:la|el|una?)\s+(?:mejor\s+|peor\s+)?(?:vaca|animal|ternera|vaquillona|toro)|la\s+que\s+(?:mas|menos)|la\s+(?:mejor|peor)|el\s+(?:mejor|peor))\b")
_NEGACIONES = {"no", "sin", "ni", "nunca", "ningun", "ninguna", "excepto", "salvo", "menos", "fuera", "tampoco"}
# Las palabras que dan el sentido de una fecha («desde el 1 de agosto» = ≥; «hasta el 15» = ≤) y cómo se escriben en el aviso.
_PALABRAS_DESDE = {"desde": "desde", "despues": "después", "posterior": "posterior", "posteriores": "posteriores", "partir": "a partir de"}
_PALABRAS_HASTA = {"hasta": "hasta", "antes": "antes"}
_NOMBRE_DE_UNIDAD = {"dia": "día", "dias": "días", "semana": "semana", "semanas": "semanas", "mes": "mes", "meses": "meses", "ano": "año", "anos": "años",
                     "ultimos": "últimos", "ultimas": "últimas", "ultimo": "último", "ultima": "última", "proximos": "próximos", "proximas": "próximas"}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _normal(frase: str) -> str:
    """La frase en minúsculas y sin tildes, con las listas de rodeos escritas con barra («rodeos 2/3») ya leídas como lista («rodeos 2 y 3»)."""
    t = _sin_tildes(frase).lower()
    return _LISTA_CON_BARRA.sub(lambda m: m.group(1) + m.group(2) + m.group(3).replace("/", " y "), t)


def _de_cifras(token: str) -> list:
    """Las lecturas posibles de un número escrito con cifras. Cada lectura es una TUPLA de números que el informe tiene que traer todos a la vez; con que
    una lectura se cumpla alcanza. «30,5» y «30.5» son 30,5; «1.200» y «1,200» son 1200; «3,4» y «2,3,4» pueden ser una lista («rodeos 3,4»): partes de una o dos cifras."""
    if token.isdigit():
        return [(float(token),)]
    partes = re.split(r"[.,]", token)
    lecturas = []
    if len(partes[0]) <= 3 and all(len(p) == 3 for p in partes[1:]):
        lecturas.append((float("".join(partes)),))
    if len(partes) == 2:
        lecturas.append((float(partes[0] + "." + partes[1]),))
    if len(partes) >= 2 and all(len(p) <= 2 for p in partes) and (len(partes) >= 3 or all(len(p) == 1 for p in partes)):
        lecturas.append(tuple(float(p) for p in partes))
    return lecturas or [(float(partes[0]),)]


def _numeros_de_la_frase(t: str) -> tuple:
    """(los tokens de la frase sin sus fechas escritas, todos los números que la frase puede estar diciendo, y los GRUPOS: cada número que el informe tiene que reflejar).

    `t` es la frase ya pasada por `_normal`. Es una lectura GENEROSA a propósito (ordinales, «multíparas», «treinta y cinco», «30 mil», coma o punto decimal, listas
    pegadas): solo sirve para AVISAR, y un aviso que salta en un pedido bien armado se aprende a ignorar. Las fechas («1 de agosto», «15/08») no son cantidades. Cada
    grupo trae: `texto` (como se escribió), `alts` (las lecturas posibles, ver `_de_cifras`), `requerido` (un/una/uno no se exigen: son un artículo), `valor` (el número, si
    es uno solo) y `unidad` (si dice «30 días»: ese número puede ser un plazo que una fecha explica)."""
    sin_fechas = _FECHAS_EN_LA_FRASE.sub(lambda m: " " * len(m.group()), t)                    # misma longitud: las posiciones siguen valiendo para `t`
    tokens = [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(sin_fechas)]
    todos: set = set()
    grupos: list = []
    i, n = 0, len(tokens)
    while i < n:
        w = tokens[i][0]
        if w[0].isdigit():
            alts = _de_cifras(w)
            j = i + 1
            if j < n and tokens[j][0] == "mil":
                alts = alts + [tuple(x * 1000 for x in a) for a in alts]
                j += 1
            valor = alts[0][0] if w.isdigit() and len(alts) == 1 else None
            grupos.append({"i": i, "j": j, "texto": w, "alts": alts, "requerido": True, "valor": valor, "unidad": None})
            i = j
        elif w in _ORDINALES:
            todos.add(float(_ORDINALES[w]))
            i += 1
        elif w in _IMPLICITOS:
            todos.add(float(_IMPLICITOS[w]))
            i += 1
        elif w in _PALABRAS_UNIDAD or w in _PALABRAS_DECENA or w in _PALABRAS_CENTENA or w == "mil":
            total = actual = 0
            j, previo = i, ""
            while j < n:
                p = tokens[j][0]
                if p == "y" and previo in _PALABRAS_DECENA and j + 1 < n and tokens[j + 1][0] in _PALABRAS_UNIDAD:        # «treinta y cinco»
                    previo = p
                    j += 1
                    continue
                v = _PALABRAS_UNIDAD.get(p, _PALABRAS_DECENA.get(p, _PALABRAS_CENTENA.get(p)))
                if v is not None:
                    actual += v
                    todos.add(float(v))
                elif p == "mil":
                    actual = max(actual, 1) * 1000
                    total += actual
                    actual = 0
                    todos.add(float(total))
                else:
                    break
                previo = p
                j += 1
            valor = float(total + actual)
            todos.add(valor)
            palabras = [tokens[k][0] for k in range(i, j)]
            grupos.append({"i": i, "j": j, "texto": " ".join(palabras), "alts": [(valor,)], "valor": valor, "unidad": None,
                           "requerido": not all(p in ("un", "una", "uno") for p in palabras)})        # «un rodeo», «una vaca»: un artículo, no una cantidad
            i = j
        else:
            i += 1
    for g in grupos:
        for a in g["alts"]:
            todos.update(a)
        sig = tokens[g["j"]][0] if g["j"] < n else ""
        g["unidad"] = sig if sig in _UNIDADES_DE_TIEMPO else None
    for g, h in zip(grupos, grupos[1:]):                                                       # «entre 20 y 40 días»: los dos son plazos
        if g["unidad"] is None and h["unidad"] and h["i"] == g["j"] + 1 and tokens[g["j"]][0] in ("y", "a", "al"):
            g["unidad"] = h["unidad"]
    return tokens, todos, grupos


def _esta(x: float, conjunto) -> bool:
    return any(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for y in conjunto)


def _campos_usados(defn: dict) -> list:
    """Los ids de los campos que el informe usa: columnas, agrupación, condiciones y orden."""
    return ([c["campo"] for c in defn["columnas"]] + list(defn.get("agrupar_por") or []) + [c["campo"] for c in defn.get("condiciones") or []]
            + ([defn["orden"]["campo"]] if defn.get("orden") else []))


def _numeros_del_informe(defn: dict, catalogo) -> list:
    """Todo número que el informe dice: los valores de las condiciones numéricas, los dígitos de los valores de texto («Rodeo 3»), el año, el mes y el día de cada fecha
    de una condición, los números de la etiqueta de cada campo que usa (el 7 de «Promedio producción diaria de leche 7 d.») y el límite."""
    out = []
    for c in defn.get("condiciones") or []:
        campo = catalogo.get(c["campo"])
        valores = c["valor"] if isinstance(c["valor"], list) else [c["valor"]]
        for v in valores:
            if campo is not None and campo.tipo == "num":
                x = _numero(v)
                if x is not None:
                    out.append(x)
            elif campo is not None and campo.tipo == "txt":
                out += [float(m.replace(",", ".")) for m in re.findall(r"\d+(?:[.,]\d+)?", str(v))]
            elif campo is not None and campo.tipo == "fecha":
                try:
                    d = datetime.date.fromisoformat(str(v))
                except ValueError:
                    continue
                out += [float(d.year), float(d.month), float(d.day)]
    for u in _campos_usados(defn):
        f = catalogo.get(u)
        if f is not None:
            out += [float(m.replace(",", ".")) for m in re.findall(r"\d+(?:[.,]\d+)?", f.etiqueta)]
    if defn.get("limite"):
        out.append(float(defn["limite"]))
    return out


# ============================================================================================ las fechas de la frase

def _mover_meses(d: datetime.date, n: int) -> datetime.date:
    """`d` más `n` meses de calendario (menos si n es negativo); el 31 de un mes de 30 días cae el 30."""
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    m += 1
    return datetime.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _rango_de_mes(y: int, m: int) -> tuple:
    return datetime.date(y, m, 1), datetime.date(y, m, calendar.monthrange(y, m)[1])


def _anios_sin_decir(hoy: datetime.date, mes: int, dia: int = 1) -> list:
    """Los años que puede tener una fecha escrita sin año: el de hoy y, si esa fecha cae DESPUÉS de hoy, el anterior («15/12» dicho en agosto es el del año pasado)."""
    try:
        este = datetime.date(hoy.year, mes, dia)
    except ValueError:
        return [hoy.year - 1]
    return [hoy.year] if este <= hoy else [hoy.year, hoy.year - 1]


def _puntos_de_plazo(n, unidad: str, hoy: datetime.date, futuro: bool = False) -> list:
    """Las fechas que puede ser «n días/semanas/meses/años» desde hoy (hacia atrás; también hacia adelante si `futuro`). Un mes son los de calendario o 30 días."""
    if not isinstance(n, (int, float)) or n != n or n <= 0 or n > 5000 or n != int(n):
        return []
    k = int(n)
    out = []
    for s in ((-1, 1) if futuro else (-1,)):
        try:
            if unidad == "dia":
                out.append(hoy + datetime.timedelta(days=s * k))
            elif unidad == "semana":
                out.append(hoy + datetime.timedelta(days=s * 7 * k))
            elif unidad == "mes":
                out += [_mover_meses(hoy, s * k), hoy + datetime.timedelta(days=s * 30 * k)]
            elif unidad == "ano":
                out += [_mover_meses(hoy, s * 12 * k), hoy + datetime.timedelta(days=s * 365 * k)]
        except (OverflowError, ValueError):                                                  # un plazo que se sale del calendario
            pass
    return out


def _candidata(ini, fin, rotulo, pos, fin_pos, pista=True, con_sentido=True):
    """Una fecha (o un intervalo) que la frase puede estar diciendo. `pista`: se nombra en el aviso. `con_sentido`: «desde» y «hasta» le dan un sentido (no a «hasta 30 días
    atrás», que puede ser «los últimos 30 días» o «hace más de 30 días»)."""
    return {"ini": ini, "fin": fin, "rotulo": rotulo, "pos": pos, "fin_pos": fin_pos, "pista": pista, "con_sentido": con_sentido, "sentido": None}


def _fechas_explicitas(t: str, hoy: datetime.date) -> list:
    """Las fechas que la frase escribe: cada una un intervalo [ini, fin] (un día, o un mes entero para «en julio»), con dónde está en la frase."""
    out = []
    for m in _FECHAS_EN_LA_FRASE.finditer(t):
        texto = re.sub(r"\s+", " ", m.group(0)).strip()[:30]
        pos, fin_pos = m.start(), m.end()

        def uno(y, mes, dia):
            try:
                d = datetime.date(y, mes, dia)
            except ValueError:
                return
            out.append(_candidata(d, d, texto, pos, fin_pos))

        def dias_de(mes, dias, anio):
            for y in ([int(anio)] if anio else _anios_sin_decir(hoy, mes, min(dias))):
                for dia in dias:
                    uno(y, mes, dia)
        if m.group("iso"):
            uno(*(int(x) for x in m.group("iso").split("-")))
        elif m.group("dmy_d"):
            a = int(m.group("dmy_a"))
            uno(a if a > 99 else 2000 + a, int(m.group("dmy_m")), int(m.group("dmy_d")))
        elif m.group("ra"):
            dias_de(_MESES_NUM[m.group("rmes")], (int(m.group("ra")), int(m.group("rb"))), m.group("ranio"))
        elif m.group("d"):
            dias_de(_MESES_NUM[m.group("mes")], (1 if m.group("d") == "primero" else int(m.group("d")),), m.group("anio"))
        elif m.group("dm_d"):
            mes = int(m.group("dm_m"))
            if 1 <= mes <= 12:
                dias_de(mes, (int(m.group("dm_d")),), None)
        else:
            mes = _MESES_NUM[m.group("mes2") or m.group("mes3")]
            anio = m.group("anio2")
            for y in ([int(anio)] if anio else _anios_sin_decir(hoy, mes)):
                try:
                    ini, fin = _rango_de_mes(y, mes)
                except ValueError:
                    continue
                out.append(_candidata(ini, fin, texto, pos, fin_pos))
    return out


_RELATIVAS = re.compile(
    r"(?<![a-z])(?:(?P<anteayer>antes de ayer|anteayer)|(?P<ayer>ayer)|(?P<hoy>hoy)|(?P<mes_actual>este mes|mes actual|mes en curso)"
    r"|(?P<mes_pasado>(?:el )?mes (?:pasado|anterior))|(?P<semana_actual>esta semana|semana actual|semana en curso)|(?P<semana_pasada>semana (?:pasada|anterior))"
    r"|(?P<ano_actual>este ano|ano actual|ano en curso)|(?P<ano_pasado>ano (?:pasado|anterior)))(?![a-z])")


def _fechas_relativas(sin_fechas: str, hoy: datetime.date) -> list:
    """«hoy», «ayer», «este mes», «el mes pasado», «esta semana», «el año pasado»... como intervalos. «Ayer» y «anteayer» no se nombran en el aviso (regla: nunca un día relativo)."""
    out = []
    dia = datetime.timedelta(days=1)
    for m in _RELATIVAS.finditer(sin_fechas):
        que, pos, fin_pos = m.lastgroup, m.start(), m.end()
        try:
            if que == "anteayer":
                out.append(_candidata(hoy - 2 * dia, hoy - 2 * dia, None, pos, fin_pos))
            elif que == "ayer":
                out.append(_candidata(hoy - dia, hoy - dia, None, pos, fin_pos))
            elif que == "hoy":
                out.append(_candidata(hoy, hoy, "hoy", pos, fin_pos))
            elif que == "mes_actual":
                out.append(_candidata(*_rango_de_mes(hoy.year, hoy.month), "este mes", pos, fin_pos))
            elif que == "mes_pasado":
                ant = _mover_meses(hoy.replace(day=1), -1)
                out.append(_candidata(*_rango_de_mes(ant.year, ant.month), "el mes pasado", pos, fin_pos))
            elif que in ("semana_actual", "semana_pasada"):
                lunes = hoy - datetime.timedelta(days=hoy.weekday() + (7 if que == "semana_pasada" else 0))
                out.append(_candidata(lunes, lunes + 6 * dia, "esta semana" if que == "semana_actual" else "la semana pasada", pos, fin_pos))
            elif que == "ano_actual":
                out.append(_candidata(datetime.date(hoy.year, 1, 1), datetime.date(hoy.year, 12, 31), "este año", pos, fin_pos))
            else:
                out.append(_candidata(datetime.date(hoy.year - 1, 1, 1), datetime.date(hoy.year - 1, 12, 31), "el año pasado", pos, fin_pos))
        except (OverflowError, ValueError):
            pass
    return out


def _fechas_de_plazos(t: str, tokens: list, grupos: list, hoy: datetime.date) -> list:
    """«los últimos 30 días», «hace 2 meses», «30 días atrás», «el último mes»: hoy menos el plazo (y, si es «últimos», también hoy: la cota de arriba)."""
    out = []
    n = len(tokens)
    for g in grupos:
        unidad = _UNIDAD_DE_PLAZO.get(g["unidad"] or "")
        if unidad is None or g["valor"] is None:
            continue
        previo = tokens[g["i"] - 1][0] if g["i"] > 0 else ""
        ultimos = previo in ("ultimos", "ultimas", "ultimo", "ultima")
        futuro = previo in ("proximos", "proximas") or (previo == "de" and g["i"] >= 2 and tokens[g["i"] - 2][0] == "dentro")
        con_previo = ultimos or previo in ("hace", "proximos", "proximas")
        k_unidad = tokens[g["j"]] if g["j"] < n else tokens[g["j"] - 1]
        pos = tokens[g["i"] - 1][1] if con_previo else tokens[g["i"]][1]
        fin_pos = max(k_unidad[2], tokens[g["j"] - 1][2])
        rotulo = " ".join([_NOMBRE_DE_UNIDAD.get(previo, previo)] * con_previo + [g["texto"], _NOMBRE_DE_UNIDAD.get(g["unidad"], g["unidad"])])
        puntos = _puntos_de_plazo(g["valor"], unidad, hoy, futuro)
        for p in puntos:
            out.append(_candidata(p, p, rotulo, pos, fin_pos, con_sentido=previo == "hace"))             # «desde hace 30 días» es ≥; «hasta 30 días» no se sabe
        if puntos and ultimos:
            out.append(_candidata(hoy, hoy, None, pos, fin_pos, pista=False, con_sentido=False))
    for k, (w, ini, fin) in enumerate(tokens[:-1]):                                        # «el último mes», «la última semana»: un plazo de 1
        sig = tokens[k + 1][0]
        if w in ("ultimo", "ultima") and sig in ("dia", "semana", "mes", "ano"):
            rotulo = f"{_NOMBRE_DE_UNIDAD[w]} {_NOMBRE_DE_UNIDAD[sig]}"
            for p in _puntos_de_plazo(1, sig, hoy):
                out.append(_candidata(p, p, rotulo, ini, tokens[k + 1][2], con_sentido=False))
            out.append(_candidata(hoy, hoy, None, ini, tokens[k + 1][2], pista=False, con_sentido=False))
    return out


def _fechas_de_la_frase(t: str, tokens: list, grupos: list, hoy: datetime.date) -> list:
    """Todo lo que la frase puede estar diciendo como fecha, ordenado por dónde aparece, cada uno con la palabra que lo precede si da el sentido («desde», «hasta»)."""
    explicitas = _fechas_explicitas(t, hoy)
    sin_fechas = _FECHAS_EN_LA_FRASE.sub(lambda m: " " * len(m.group()), t)
    cands = explicitas + _fechas_relativas(sin_fechas, hoy) + _fechas_de_plazos(t, tokens, grupos, hoy)
    for a in _ANIO_SUELTO.finditer(sin_fechas):                                            # «en 2025»: cualquier día de ese año
        y = int(a.group())
        cands.append(_candidata(datetime.date(y, 1, 1), datetime.date(y, 12, 31), a.group(), a.start(), a.end()))
    cands.sort(key=lambda c: (c["pos"], c["ini"]))
    for c in cands:
        if not c["con_sentido"]:
            continue
        corte = max([x["fin_pos"] for x in cands if x["fin_pos"] <= c["pos"]], default=0)
        for palabra in reversed(re.findall(r"[a-z]+", t[corte:c["pos"]])[-3:]):
            if palabra in _PALABRAS_DESDE:
                c["sentido"] = ("desde", _PALABRAS_DESDE[palabra])
                break
            if palabra in _PALABRAS_HASTA:
                c["sentido"] = ("hasta", _PALABRAS_HASTA[palabra])
                break
    return cands


def _plazo_explicado(valor, unidad: str, fechas: list, hoy: datetime.date) -> bool:
    """¿Alguna fecha de una condición es hoy menos (o más) ese plazo, con un día de tolerancia? Si sí, el «30» de «últimos 30 días» no es una condición olvidada."""
    return any(abs((f - p).days) <= 1 for p in _puntos_de_plazo(valor, _UNIDAD_DE_PLAZO.get(unidad, ""), hoy, True) for f in fechas)


def _pista_de_fechas(cands: list) -> str:
    """Lo que SÍ sale de la frase, en fechas con su día de la semana: « (últimos 30 días = mar 28/07/2026)». «Ayer» no se nombra."""
    pistas: dict = {}
    for c in cands:
        if c["pista"]:
            cuando = _dia_largo(c["ini"]) if c["ini"] == c["fin"] else f"del {_dia_largo(c['ini'])} al {_dia_largo(c['fin'])}"
            fechas = pistas.setdefault((c["pos"], c["rotulo"]), [])
            if cuando not in fechas:
                fechas.append(cuando)
    textos = [f"{rotulo} = {' o '.join(fechas)}" if rotulo else " o ".join(fechas) for (_, rotulo), fechas in pistas.items()]
    return f" ({'; '.join(textos[:3])})" if textos else ""


_SIMBOLO_DE_OPERADOR = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "=": "="}


def _avisos_de_fechas(fechas_cond: list, cands: list, t: str) -> list:
    """La fecha de cada condición contra lo que la frase dice (tolerancia de un día), y el operador contra las palabras («desde» con ≤ o «hasta» con ≥)."""
    avisos = []
    negado = any(w in _NEGACIONES for w in re.findall(r"[a-z]+", t))                       # «las que NO parieron desde el 1»: el sentido se da vuelta, no se juzga
    for c, campo, v in fechas_cond:
        explican = [x for x in cands if (x["ini"] - v).days <= 1 and (v - x["fin"]).days <= 1]      # un día de tolerancia; restar fechas nunca se sale del calendario
        if not explican:
            avisos.append(f"La fecha {_dia_largo(v)} de «{campo.etiqueta}» no sale de lo que escribiste{_pista_de_fechas(cands)}: revisala en las condiciones.")
            continue
        op = c["op"]
        if negado or op == "=":
            continue

        def compatible(s):
            return s is None or (s[0] == "desde") == (op in (">", ">="))
        if not any(compatible(x["sentido"]) for x in explican):
            palabra = explican[0]["sentido"][1]
            avisos.append(f"«{campo.etiqueta}»: escribiste «{palabra}» pero la condición es {_SIMBOLO_DE_OPERADOR.get(op, op)} {_dia_largo(v)}: revisá que el sentido sea el que querías.")
    return avisos


def _avisos_de_valores_cerrados(t: str, defn: dict, catalogo) -> list:
    """Un valor de un campo cerrado («preñadas», «secas») que la frase menciona y el informe ni usa ni muestra: una condición que la IA se olvidó."""
    usados = set(_campos_usados(defn))
    # Hay dos maneras de pedir «preñadas»: el estado de la reproducción («Preñada») o el sí/no `Preñada = Sí`; con «secas», `En secado = Sí`. Si el informe ya usa un campo que
    # lo nombra (su etiqueta contiene la raíz), no se está olvidando nada.
    etiquetas = [_sin_tildes(catalogo[u].etiqueta).lower() for u in usados if u in catalogo]
    avisos = []
    for campo in catalogo.values():
        if not campo.valores or campo.id in usados:
            continue
        for v in campo.valores:
            raiz = _sin_tildes(v).lower()
            raiz = raiz[:-1] if raiz[-1:] in ("a", "o") else raiz
            if len(raiz) >= 3 and re.search(rf"(?<![a-z]){re.escape(raiz)}[ao]s?(?![a-z])", t) and not any(raiz in e for e in etiquetas):
                avisos.append(f"Escribiste algo sobre «{v}» pero el informe no tiene ninguna condición ni columna de «{campo.etiqueta}»: revisá que no falte algo.")
    return avisos


def _contra_la_frase(defn: dict, frase: str, catalogo, hoy: datetime.date) -> list:
    """Lo que la frase dice contra lo que el informe tiene, en las dos direcciones: fechas, números y valores cerrados."""
    condiciones = defn.get("condiciones") or []
    t = _normal(frase)
    tokens, de_la_frase, grupos = _numeros_de_la_frase(t)
    fechas_cond = []
    for c in condiciones:
        campo = catalogo.get(c["campo"])
        if campo is not None and campo.tipo == "fecha" and isinstance(c["valor"], str):
            try:
                fechas_cond.append((c, campo, datetime.date.fromisoformat(c["valor"])))
            except ValueError:
                pass
    avisos = []
    if fechas_cond:
        try:
            avisos += _avisos_de_fechas(fechas_cond, _fechas_de_la_frase(t, tokens, grupos, hoy), t)
        except Exception as exc:  # noqa: BLE001 -- un aviso nunca puede tumbar un informe armado: queda el tipo en el log (nunca el texto)
            log.warning("armar informe: falló el chequeo de las fechas: %s", type(exc).__name__)
    # 1) lo que el informe dice y la frase no: un número inventado (el 0 de «> 0», que la regla 2 del prompt impone para «en ordeño», no cuenta)
    for c in condiciones:
        campo = catalogo.get(c["campo"])
        if campo is None or campo.tipo != "num":
            continue
        for v in (c["valor"] if isinstance(c["valor"], list) else [c["valor"]]):
            x = _numero(v)
            if x is not None and x != 0 and not _esta(x, de_la_frase):
                avisos.append(f"El número {v} de «{campo.etiqueta}» no figura en lo que escribiste: revisalo en las condiciones.")
    limite = defn.get("limite")
    if limite and not _esta(float(limite), de_la_frase) and not (limite == 1 and _UNO_IMPLICITO.search(t)):
        avisos.append(f"El límite de {limite} {'fila' if limite == 1 else 'filas'} no figura en lo que escribiste.")
    # 2) lo que la frase dice y el informe no: una condición que la IA se olvidó
    del_informe = _numeros_del_informe(defn, catalogo)
    fechas = [f for _, _, f in fechas_cond]
    for g in grupos:
        if not g["requerido"]:
            continue
        if g["unidad"] and g["valor"] is not None and _plazo_explicado(g["valor"], g["unidad"], fechas, hoy):
            continue                                                                       # «30 días» y una fecha que es hoy menos 30: el plazo ya está en el informe
        if not any(all(_esta(x, del_informe) for x in alt) for alt in g["alts"]):
            avisos.append(f"El número {g['texto']} que escribiste no está en las condiciones del informe: revisá que no falte algo.")
    return avisos + _avisos_de_valores_cerrados(t, defn, catalogo)


def _advertencias(defn: dict, frase: str, catalogo, hoy: datetime.date = None) -> tuple:
    """(advertencias, campos sin verificar). Avisan, no rechazan."""
    hoy = hoy or datetime.date.today()
    condiciones = defn.get("condiciones") or []
    usados = [c["campo"] for c in defn["columnas"]] + list(defn.get("agrupar_por") or []) + [c["campo"] for c in condiciones]
    sin_verificar = []
    for u in usados:
        f = catalogo.get(u)
        if f is not None and not f.verificado and f.etiqueta not in sin_verificar:
            sin_verificar.append(f.etiqueta)
    avisos = []
    if not condiciones:
        avisos.append("Sin condiciones: el informe trae todos los animales del rebaño, también terneras, secas y recría.")
    if defn.get("limite") and (defn.get("agrupar_por") or any(c.get("resumen") for c in defn["columnas"])):
        avisos.append("El límite recorta la lista, pero los totales y resúmenes cuentan a todos los animales del informe.")
    if sin_verificar:
        avisos.append("Incluye campos que todavía no se compararon con DelPro (⚠): " + ", ".join(sin_verificar) + ".")
    con_fechas = any(catalogo.get(c["campo"]) is not None and catalogo[c["campo"]].tipo == "fecha" for c in condiciones)
    if con_fechas:
        avisos.append("Las fechas de las condiciones son fijas: si guardás el informe y lo programás, no se corren solas (va a mirar siempre el mismo período).")
    try:
        avisos += _contra_la_frase(defn, frase, catalogo, hoy)
    except Exception as exc:  # noqa: BLE001 -- un aviso nunca puede tumbar un informe armado: queda el tipo en el log (nunca el texto)
        log.warning("armar informe: falló el chequeo contra la frase: %s", type(exc).__name__)
    return avisos, sin_verificar


def _resultado(bruto: dict, frase: str, catalogo, dia: datetime.date) -> dict:
    if not bruto["se_puede"]:
        return {"ok": False, "motivo": "Con los campos que hay todavía no se puede armar ese informe.",
                "no_pude": _textos(bruto["lo_que_no_se_pudo"], dia, PARTE_SIN_ARMAR), "frase": frase}
    defn = _definicion(bruto, catalogo)
    avisos, sin_verificar = _advertencias(defn, frase, catalogo, dia)
    return {"ok": True, "nombre": _nombre(bruto["nombre"]), "definicion": defn, "se_lee": _se_lee(defn, catalogo),
            "supuestos": _textos(bruto["supuestos"], dia, SUPUESTO_SOBRE_UN_DIA), "no_pude": _textos(bruto["lo_que_no_se_pudo"], dia, PARTE_SIN_ARMAR),
            "advertencias": avisos, "sin_verificar": sin_verificar, "frase": frase}


def _uso(resp):
    u = getattr(resp, "usage", None)
    return getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)


def _plazo():
    """El tiempo límite de UN pedido: corto para conectar (`CONEXION_S`) y `TIMEOUT_S` para leer la respuesta. Sin el objeto del SDK, el número de siempre."""
    try:
        import anthropic
        return anthropic.Timeout(TIMEOUT_S, connect=CONEXION_S)
    except Exception:  # noqa: BLE001
        return TIMEOUT_S


def _sin_reintentos(cliente):
    """Un solo intento HTTP: con los reintentos del SDK (2 por defecto, y cada uno con el tiempo límite entero) un pedido colgado esperaba 3 × 45 s, más que el corte del
    túnel de Cloudflare. La repetición de este módulo es otra cosa: corrige una respuesta que no pasó la validación.

    Se evaluó dejar UN reintento (para una sobrecarga o un corte momentáneo) y se descartó: el SDK también reintenta un tiempo agotado, así que un pedido colgado esperaría
    2 × `TIMEOUT_S` por llamada (hasta 180 s con la repetición de la validación), más que el corte de ~100 s del túnel. Lo que se ganaba lo da `_devolver_turno`: un
    pedido que falla enseguida no gasta turno, y volver a tocar el botón no cuesta nada."""
    con_opciones = getattr(cliente, "with_options", None)
    if callable(con_opciones):
        try:
            return con_opciones(max_retries=0)
        except Exception:  # noqa: BLE001
            return cliente
    return cliente


def armar(frase, hoy=None, cliente=None, catalogo=None, reloj=None) -> dict:
    """La frase -> {"ok": True, "nombre", "definicion", "se_lee", "supuestos", "no_pude", "advertencias", "sin_verificar", "frase"}, o {"ok": False, "motivo",
    "no_pude"} si la IA dice que no se puede. Levanta `InformeIAError` (o sus hijas) si no hay clave, falla la IA, se pasó el tope o no salió un informe válido."""
    import agente
    catalogo = _catalogo(catalogo)
    texto = _frase(frase)
    dia = _hoy(hoy)
    if not agente.api_disponible():
        raise SinIA("La API de Claude no está configurada en este servidor. Definí la variable de entorno ANTHROPIC_API_KEY y reiniciá la aplicación.")
    cliente = _sin_reintentos(cliente or agente._client())
    marca = _tomar_turno(reloj or time.monotonic)
    gastado = False                                                            # ¿la API ya contestó algo? Entonces el turno se pagó
    prompt, formato = sistema(dia, catalogo), {"format": {"type": "json_schema", "schema": esquema(catalogo)}}
    mensajes = [{"role": "user", "content": texto}]
    for intento in range(2):
        try:
            resp = cliente.messages.create(model=agente.MODEL, max_tokens=MAX_TOKENS, system=prompt, timeout=_plazo(), output_config=formato, messages=mensajes)
        except Exception as exc:  # noqa: BLE001 -- sin red, sin crédito, límite, tiempo: ni la pantalla ni el log llevan el texto de la excepción
            log.warning("la API de Claude falló al armar un informe (intento %d): %s, estado %s", intento + 1, type(exc).__name__, getattr(exc, "status_code", "?"))
            if not gastado and _no_llego_a_la_api(exc):                        # sin internet, 429 o 5xx: la API no aceptó el trabajo, el pedido no cuenta para el tope
                _devolver_turno(marca)
            raise IAFallo("No se pudo armar el informe ahora. Probá de nuevo en un rato o armalo a mano.") from None
        gastado = True
        entrada, salida = _uso(resp)
        log.info("armar informe: intento %d, tokens entrada=%s salida=%s, frase de %d letras", intento + 1, entrada, salida, len(texto))
        crudo = _texto_de(resp)
        try:
            return _resultado(_leer(crudo), texto, catalogo, dia)
        except _Corregible as e:
            log.info("armar informe: la respuesta no sirvió (intento %d): %s", intento + 1, e)
            if intento == 0:
                mensajes = mensajes + [{"role": "assistant", "content": crudo or "{}"},
                                       {"role": "user", "content": f"Ese informe no es válido: {e} Corregilo y devolvelo de nuevo."}]
        except InformeIAError:
            raise
        except Exception as exc:  # noqa: BLE001 -- algo nuestro, no de la IA: queda el tipo en el log (nunca el texto) y la pantalla recibe un mensaje fijo
            log.warning("armar informe: falló al leer la respuesta: %s", type(exc).__name__)
            raise IAFallo("No se pudo armar el informe ahora. Probá de nuevo en un rato o armalo a mano.") from None
    raise InformeIAError("No pude armar ese informe con lo que escribiste. Probá decirlo de otra forma, nombrando los campos (por ejemplo «días en ordeño» o «rodeo»).")
