"""Armar un informe a partir de una frase (Fase 4b de los informes personalizables).

El administrador escribe qué informe quiere («vacas del rodeo 3 con más de 200 días en ordeño, ordenadas por producción») y una IA (el mismo
Claude del agente) lo traduce a la DEFINICIÓN de un informe. La definición se abre en una pestaña nueva, SIN guardar y SIN ejecutarse sola: el
usuario revisa lo que se entendió y recién ahí toca «Actualizar».

LA IA NO DECIDE NADA DE SEGURIDAD. Lo único que devuelve es una definición (JSON con un esquema cerrado) que pasa por
`informes_guardados.validar_definicion`, o sea por `informes_motor.compilar`: campo y operador salen de listas cerradas y los valores se escriben
como literales validados. Un campo inventado, un valor con SQL o una clave de más no llegan a ningún lado: se rechazan y se le devuelve el motivo a
la IA UNA vez. El modelo no escribe SQL ni ve SQL, ni datos del tambo: solo ve el catálogo de campos (id, nombre, tipo, unidad) y la frase.

Lo que se le muestra al usuario («Se lee así») lo arma el servidor desde la definición ya validada, no lo que escriba el modelo; el texto libre del
modelo (supuestos, lo que no pudo) va acotado y limpio. Si hay un campo sin verificar, un número que no figura en la frase, un informe sin condiciones
o un límite contra totales, se AVISA (no se rechaza: es una advertencia, como las del gráfico del agente).

Costo y falla: UNA llamada (más una repetición con el motivo, si la definición no pasó la validación), con tiempo límite, y un tope de pedidos por
hora. Sin clave de API, con la IA caída o lenta, o con cualquier respuesta rara, el diseñador manual de informes sigue andando igual: nada de esto
es necesario para armar un informe.
"""
import datetime
import json
import logging
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
_MILES = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_DIA_RELATIVO_EN_NOMBRE = re.compile(r"\b(hoy|ayer|anteayer|este mes|esta semana|ese día|esa mañana|esta mañana|el día anterior)\b|(?<!la )\bmañana\b", re.I)
_DIA_RELATIVO_EN_TEXTO = re.compile(r"\b(ayer|anteayer|ese día|esa mañana|el día anterior)\b", re.I)


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


def _tomar_turno(reloj) -> None:
    ahora = reloj()
    with _LOCK:
        while _PEDIDOS and ahora - _PEDIDOS[0] > 3600:
            _PEDIDOS.pop(0)
        if len(_PEDIDOS) >= PEDIDOS_POR_HORA:
            raise TopeDePedidos(f"Hiciste muchos pedidos seguidos (hasta {PEDIDOS_POR_HORA} por hora). Esperá unos minutos o armá el informe a mano.")
        _PEDIDOS.append(ahora)


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
            "nombre": {"type": "string", "description": "Nombre corto del informe (hasta 60 letras), sin fechas relativas como hoy, ayer o este mes."},
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
    """El prompt: las reglas y el catálogo de campos (id, nombre, tipo, unidad), generado desde el catálogo real. Sin SQL, sin notas internas, sin datos del tambo."""
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
        "4. Un rodeo por número: AnimalGroupNumber. Por nombre: AnimalGroupName con es, escrito como lo llama DelPro («Rodeo 3», «Rodeo 4 - Baja»). Si dicen "
        "«rodeo 3» sin más, preferí el número.\n"
        "5. Los números van tal cual los dijo el usuario, sin convertir unidades, sin separador de miles y con punto decimal (1200, 30.5). Si un número no está "
        "en el pedido, no lo inventes.\n"
        "6. Para fechas relativas («este mes», «los últimos 30 días») calculá la fecha con el día de hoy y anotá el supuesto. Para los días en ordeño usá CurrentDIM.\n"
        "7. Los campos marcados «sin verificar» todavía no se compararon con DelPro: podés usarlos si el pedido los necesita.\n"
        "8. nombre: corto (hasta 60 letras), descriptivo, sin fechas relativas (hoy, ayer, mañana, este mes).\n"
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
    """«ayer» y «anteayer» son una fecha que el servidor SÍ conoce: se escribe con el día de la semana (regla del usuario). «Ese día» o «el día anterior» no."""
    texto = re.sub(r"\banteayer\b", _dia_largo(dia - datetime.timedelta(days=2)), texto, flags=re.I)
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
        if _DIA_RELATIVO_EN_TEXTO.search(s):
            sacadas += 1
        elif len(out) < TEXTOS_MAX:
            out.append(s if len(s) <= TEXTO_LARGO_MAX else s[:TEXTO_LARGO_MAX - 1].rstrip() + "…")
    if sacadas:
        out = out[:TEXTOS_MAX - 1] + [neutra]
    return out


def _nombre(nombre: str) -> str:
    limpio = "".join(c for c in re.sub(r"\s+", " ", nombre) if ord(c) >= 32).strip()
    if _DIA_RELATIVO_EN_NOMBRE.search(limpio):
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
        if campo.valores and c["op"] != "contiene":                            # un texto cerrado (el estado de la reproducción): solo esos valores, con las mayúsculas del catálogo
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
        lineas.append(f"Se listan las primeras {defn['limite']} filas")
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
_ORDINALES = {"primer": 1, "primera": 1, "primeras": 1, "primero": 1, "primeros": 1, "segunda": 2, "segundas": 2, "segundo": 2, "segundos": 2, "tercer": 3,
              "tercera": 3, "terceras": 3, "tercero": 3, "terceros": 3, "cuarta": 4, "cuarto": 4, "quinta": 5, "quinto": 5}
_IMPLICITOS = {"primipara": 1, "primiparas": 1, "multipara": 2, "multiparas": 2}                  # «multíparas» = de segunda lactancia en adelante
_UNIDADES_DE_TIEMPO = {"dia", "dias", "semana", "semanas", "mes", "meses", "ano", "anos", "hora", "horas", "hs", "minuto", "minutos"}
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_FECHAS_EN_LA_FRASE = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{1,2}} de (?:{_MESES})(?: de \d{{4}})?")


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _de_cifras(token: str) -> set:
    """Las lecturas posibles de un número escrito con cifras: «30,5» y «30.5» son 30,5; «1.200» y «1,200» son 1200 (y también 1,2)."""
    if token.isdigit():
        return {float(token)}
    partes = re.split(r"[.,]", token)
    lecturas = set()
    if len(partes[0]) <= 3 and all(len(p) == 3 for p in partes[1:]):
        lecturas.add(float("".join(partes)))
    if len(partes) == 2:
        lecturas.add(float(partes[0] + "." + partes[1]))
    return lecturas or {float(partes[0])}


def _numeros_de_la_frase(frase: str, plazos_son_fechas: bool) -> tuple:
    """(todos los números que la frase puede estar diciendo, con cifras o con palabras; los escritos con cifras que el informe tiene que reflejar: [(texto, lecturas)]).

    Es una lectura GENEROSA a propósito (ordinales, «multíparas», «treinta y cinco», «30 mil», coma o punto decimal): solo sirve para AVISAR, y un aviso que salta
    en un pedido bien armado se aprende a ignorar. Las fechas («1 de agosto», «15/08») no cuentan, y los plazos («30 días») tampoco cuando el informe los volvió una fecha."""
    t = _FECHAS_EN_LA_FRASE.sub(" ", _sin_tildes(frase).lower())
    tokens = re.findall(r"\d+(?:[.,]\d+)*|[a-z]+", t)
    todos: set = set()
    cifras: list = []
    i, n = 0, len(tokens)
    while i < n:
        w = tokens[i]
        if w[0].isdigit():
            lecturas = _de_cifras(w)
            siguiente = tokens[i + 1] if i + 1 < n else ""
            if siguiente == "mil":
                lecturas = lecturas | {x * 1000 for x in lecturas}
                siguiente = tokens[i + 2] if i + 2 < n else ""
                i += 1
            todos |= lecturas
            if not (plazos_son_fechas and siguiente in _UNIDADES_DE_TIEMPO):
                cifras.append((w, lecturas))
            i += 1
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
                p = tokens[j]
                if p == "y" and previo in _PALABRAS_DECENA and j + 1 < n and tokens[j + 1] in _PALABRAS_UNIDAD:        # «treinta y cinco»
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
            todos.add(float(total + actual))
            i = j
        else:
            i += 1
    return todos, cifras


def _esta(x: float, conjunto) -> bool:
    return any(abs(x - y) <= 1e-9 * max(1.0, abs(y)) for y in conjunto)


def _numeros_del_informe(defn: dict, catalogo) -> list:
    """Todo número que el informe dice: los valores de las condiciones numéricas, los dígitos de los valores de texto («Rodeo 3») y el límite."""
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
    if defn.get("limite"):
        out.append(float(defn["limite"]))
    return out


def _advertencias(defn: dict, frase: str, catalogo) -> tuple:
    """(advertencias, campos sin verificar). Avisan, no rechazan."""
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
    de_la_frase, con_cifras = _numeros_de_la_frase(frase, con_fechas)
    # 1) lo que el informe dice y la frase no: un número inventado (el 0 de «> 0», que la regla 2 del prompt impone para «en ordeño», no cuenta)
    for c in condiciones:
        campo = catalogo.get(c["campo"])
        if campo is None or campo.tipo != "num":
            continue
        for v in (c["valor"] if isinstance(c["valor"], list) else [c["valor"]]):
            x = _numero(v)
            if x is not None and x != 0 and not _esta(x, de_la_frase):
                avisos.append(f"El número {v} de «{campo.etiqueta}» no figura en lo que escribiste: revisalo en las condiciones.")
    if defn.get("limite") and not _esta(float(defn["limite"]), de_la_frase):
        avisos.append(f"El límite de {defn['limite']} filas no figura en lo que escribiste.")
    # 2) lo que la frase dice y el informe no: una condición que la IA se olvidó
    del_informe = _numeros_del_informe(defn, catalogo)
    for token, lecturas in con_cifras:
        if not any(_esta(x, del_informe) for x in lecturas):
            avisos.append(f"El número {token} que escribiste no está en las condiciones del informe: revisá que no falte algo.")
    return avisos, sin_verificar


def _resultado(bruto: dict, frase: str, catalogo, dia: datetime.date) -> dict:
    if not bruto["se_puede"]:
        return {"ok": False, "motivo": "Con los campos que hay todavía no se puede armar ese informe.",
                "no_pude": _textos(bruto["lo_que_no_se_pudo"], dia, PARTE_SIN_ARMAR), "frase": frase}
    defn = _definicion(bruto, catalogo)
    avisos, sin_verificar = _advertencias(defn, frase, catalogo)
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
    túnel de Cloudflare. La repetición de este módulo es otra cosa: corrige una respuesta que no pasó la validación."""
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
    _tomar_turno(reloj or time.monotonic)
    cliente = _sin_reintentos(cliente or agente._client())
    prompt, formato = sistema(dia, catalogo), {"format": {"type": "json_schema", "schema": esquema(catalogo)}}
    mensajes = [{"role": "user", "content": texto}]
    for intento in range(2):
        try:
            resp = cliente.messages.create(model=agente.MODEL, max_tokens=MAX_TOKENS, system=prompt, timeout=_plazo(), output_config=formato, messages=mensajes)
        except Exception as exc:  # noqa: BLE001 -- sin red, sin crédito, límite, tiempo: ni la pantalla ni el log llevan el texto de la excepción
            log.warning("la API de Claude falló al armar un informe (intento %d): %s, estado %s", intento + 1, type(exc).__name__, getattr(exc, "status_code", "?"))
            raise IAFallo("No se pudo armar el informe ahora. Probá de nuevo en un rato o armalo a mano.") from None
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
