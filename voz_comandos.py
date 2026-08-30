# -*- coding: utf-8 -*-
"""Interpreta el texto transcripto de un comando de voz (ver
delpro-analitica/docs/superpowers/specs/2026-08-29-comandos-voz-jarvis-design.md)
contra un vocabulario CERRADO y CHICO: frases fijas de Lavado Automático +
"prender/encender/apagar <nombre de actuador>", usando los nombres que el
tambo configuró en iot_canales (si le cambia el nombre a una salida, esto
se adapta solo, sin tocar código).

EL VERBO DECIDE LA INTENCIÓN, no el parecido de la frase entera. La primera
versión de este archivo comparaba el texto completo contra una lista de
frases fijas con difflib.get_close_matches, y eso resultó PELIGROSO: como
"lavado" es la palabra dominante y está en las dos familias de frases, el
verbo —lo único que distingue arrancar de parar— quedaba diluido entre los
caracteres compartidos. Medido con las constantes de esa versión:

    'parar el lavado'   -> lavado_iniciar  ('iniciar el lavado', 0.750)
    'parar lavado'      -> lavado_iniciar  ('arrancar lavado',   0.815)
    'para el lavado'    -> lavado_iniciar  ('iniciar el lavado', 0.774)
    'frenar el lavado'  -> lavado_iniciar  ('iniciar el lavado', 0.788)
    'apagar el lavado'  -> lavado_iniciar  ('iniciar el lavado', 0.727)

O sea: pedir PARAR arrancaba las bombas y contestaba "Lavado iniciado".
Ahora se parsea el primer token contra listas explícitas de verbos y la
comparación difusa se usa SOLO para el resto de la frase (el nombre del
actuador), que es donde de verdad hace falta tolerar errores del
transcriptor. Ante la duda NUNCA se resuelve a lavado_iniciar: prender
bombas es la dirección peligrosa (ver CLAUDE.md, "Cancelar NO pide
confirmación... frenar bombas reales es la dirección segura").

También guarda el estado de los actuadores SOSTENIDOS por voz (distinto
del pulso de 0,5s que ya usa el panel de Actuadores, que sigue igual) --
la ejecución real de Modbus la hace iot_lavado.procesar_comandos_voz.

Deliberadamente SIN import de iot_lavado (que sí importa este módulo),
mismo criterio que lavado_programa.py/iot_conexion.py. SÍ importa
lavado_programa (que a su vez tampoco importa iot_lavado) para saber si un
actuador está en uso por la etapa activa del ciclo automático."""
import datetime
import difflib
import sqlite3
import threading
import unicodedata

import iot_canales
import lavado_programa

RUTA_DB = "iot_sensores.db"
ACTUADORES_VALIDOS = {"do_1", "do_2", "do_3", "do_4", "do_5", "do_6", "do_7", "do_8"}

# --- Umbrales de comparación difusa -------------------------------------
# Los tres salen de la batería de medición del scratchpad
# (medir_umbrales_voz.py / test_voz_comandos.py, que los vuelve a chequear
# en cada corrida), no de un número elegido a ojo.
#
# UMBRAL_CONFIANZA: parecido mínimo entre el resto de la frase y el nombre
# configurado de un actuador. Medido sobre la batería: todo lo que TIENE que
# reconocerse (incluidas variantes con errores típicos de transcripción,
# "bomba de agu", "de sinfectante") da >= 0,960; todo lo que NO tiene que
# reconocerse ("salida 3", "la luz del corral", "che como andas") da <= 0,400.
# 0,72 cae en el medio de esa banda vacía -- se mantiene el valor que ya
# tenía el archivo, pero ahora con evidencia atrás.
UMBRAL_CONFIANZA = 0.72
# MARGEN_AMBIGUEDAD: cuánto le tiene que sacar el actuador ganador al
# segundo (si es OTRO actuador) para aceptarlo. Medido: los comandos que
# SÍ tienen que resolver sacan >= 0,214 de ventaja; los genuinamente
# ambiguos ("prender bomba de agua" con "Bomba de Agua Fría" y "Bomba de
# Agua Caliente" configuradas: 0,839 contra 0,743) sacan <= 0,096. Sin
# esto, ese caso elegía "fría" en silencio (ver el plan de pruebas del
# spec: "que no haya falsos positivos entre actuadores con nombres
# parecidos"). Los nombres repetidos además se rechazan al guardarlos
# (iot_canales.guardar).
MARGEN_AMBIGUEDAD = 0.15
# UMBRAL_LAVADO: parecido mínimo de UNA palabra con "lavado" para
# considerarla una forma de nombrar el ciclo. Medido por palabra: las
# variantes plausibles del transcriptor ("labado", "lavao", "vado",
# "lavando", "abado") dan >= 0,727; las palabras que aparecen en comandos
# de actuador ("bomba", "agua", "espuma", "vacio", "llave", "lavarropas")
# dan <= 0,625.
UMBRAL_LAVADO = 0.70

# --- Vocabulario cerrado -------------------------------------------------
# El primer token de la frase tiene que ser UNO de estos, comparado EXACTO
# (después de normalizar: minúsculas y sin tildes, así "pará" == "para").
# A propósito no hay comparación difusa acá: un verbo que llega mal
# transcripto tiene que caer en "desconocido" ("No entendí, repetí") y no
# arriesgarse a caer en el verbo de al lado.
VERBOS_INICIAR = {
    "iniciar", "inicia", "inicie", "iniciemos",
    "arrancar", "arranca", "arranque", "arrancamos",
    "empezar", "empeza", "empieza", "empiece", "empecemos",
    "comenzar", "comenza", "comienza", "comience",
}
VERBOS_CANCELAR = {
    "parar", "para", "pare", "paren", "paralo", "pararlo",
    "detener", "detene", "detiene", "detenga", "detengan", "detenelo",
    "cancelar", "cancela", "cancele", "cancelen", "cancelalo",
    "frenar", "frena", "frene", "frenalo",
    "cortar", "corta", "corte", "cortalo",
}
VERBOS_PRENDER = {"prender", "prende", "prenda", "prendelo", "encender", "encende", "enciende", "encienda"}
# "apagar" es el único verbo AMBIGUO: "apagar el lavado" es cancelar el
# ciclo, "apagar bomba de agua" es apagar ese relé. Lo resuelve lo que
# viene después (ver interpretar).
VERBOS_APAGAR = {"apagar", "apaga", "apague", "apaguen", "apagalo"}

# Palabras que no aportan a QUÉ se está nombrando: se descartan antes de
# decidir si el resto de la frase habla del ciclo de lavado.
PALABRAS_VACIAS = {
    "el", "la", "los", "las", "un", "una", "lo", "al", "del", "de",
    "por", "favor", "ya", "ahora", "todo", "toda", "ciclo", "automatico",
}
ARTICULOS = {"el", "la", "los", "las", "un", "una", "lo"}
PALABRA_LAVADO = "lavado"

_lock = threading.Lock()


def _conectar_db() -> sqlite3.Connection:
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS voz_actuadores_estado (
            clave TEXT PRIMARY KEY,
            encendido_desde TEXT NOT NULL
        )
    """)
    # Apagados EXPLÍCITOS pedidos por voz, pendientes de aplicar por Modbus.
    # Existe porque procesar_comandos_voz es de flanco (solo escribe cuando
    # cambia SU estado): si el relé quedó prendido por algo que la capa de
    # voz no causó (un apagado de arranque que falló, la web del propio
    # M300, un ciclo trabado), un "apagar X" borraba la fila, no encontraba
    # transición y NO escribía nada -- mientras la pantalla ya había dicho
    # "Apagando X". Esta tabla fuerza esa escritura una vez.
    con.execute("""
        CREATE TABLE IF NOT EXISTS voz_apagados_pendientes (
            clave TEXT PRIMARY KEY,
            pedido_en TEXT NOT NULL
        )
    """)
    con.commit()
    return con


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin puntuación y con los espacios colapsados.
    Sacar las tildes hace que "pará"/"para" o "prendé"/"prende" sean la
    misma palabra, que es justo lo que hace falta para comparar contra las
    listas de verbos sin duplicar cada entrada."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = "".join(c if (c.isalnum() or c.isspace()) else " " for c in texto)
    return " ".join(texto.split())


def _parecido(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _es_del_lavado(resto: str, vacio_cuenta: bool = True) -> bool:
    """True si `resto` (lo que sigue al verbo) nombra el ciclo de lavado.

    Se mira PALABRA POR PALABRA y se exige que TODAS las palabras con
    contenido sean alguna forma de "lavado". Comparar la frase entera no
    sirve: "bomba de agua" contra "lavado" da 0,500, demasiado cerca de
    "el labado" (0,667). Y exigir que TODAS lo sean es lo que evita que un
    actuador llamado "Bomba de lavado" se lea como "el ciclo de lavado".

    `vacio_cuenta` decide qué pasa cuando no queda ninguna palabra con
    contenido ("parar" a secas, "apagar todo"): para PARAR eso ya es
    cancelar el lavado, pero para ARRANCAR no alcanza -- "iniciar" solo, o
    "arrancar eso", tiene que caer en "No entendí, repetí" y no poner en
    marcha las bombas."""
    palabras = [p for p in resto.split() if p not in PALABRAS_VACIAS]
    if not palabras:
        return vacio_cuenta
    return all(_parecido(p, PALABRA_LAVADO) >= UMBRAL_LAVADO for p in palabras)


def _sin_articulo(resto: str) -> str:
    palabras = resto.split()
    while palabras and palabras[0] in ARTICULOS:
        palabras.pop(0)
    return " ".join(palabras)


def _actuadores_con_nombre() -> list:
    """[(nombre normalizado, clave), ...] de las salidas que tienen nombre
    propio configurado. Sin nombre propio no es natural pedirlas en voz alta
    ("prender do 5"), así que no entran al vocabulario."""
    nombres = iot_canales.nombres()
    return [(_normalizar(nombres[clave]), clave)
            for clave in sorted(ACTUADORES_VALIDOS)
            if nombres.get(clave)]


def _buscar_actuador(resto: str) -> tuple:
    """("ok", clave) si `resto` nombra sin dudas a un actuador configurado;
    ("ambiguo", None) si se parece a DOS actuadores distintos y el ganador
    no le saca MARGEN_AMBIGUEDAD al segundo; ("ninguno", None) si no llega
    al umbral.

    "ambiguo" y "ninguno" se distinguen porque no significan lo mismo: con
    "ambiguo" el operario tiene que escuchar "No entendí, repetí" y volver a
    pedirlo con el nombre completo (nunca elegir uno de los dos en
    silencio), mientras que "ninguno" deja que el verbo decida qué hacer."""
    resto = _sin_articulo(resto)
    if not resto:
        return ("ninguno", None)
    candidatos = _actuadores_con_nombre()

    # El nombre dicho EXACTO gana sin mirar el margen. Hace falta: con los
    # nombres que hoy tiene cargados el tambo, "salida 5" a "salida 8" se
    # parecen 0,875 entre sí (brecha 0,125 < MARGEN_AMBIGUEDAD), así que el
    # margen solo, sin esta salida, dejaría inservible justamente al comando
    # BIEN dicho. Dos nombres idénticos sí son ambiguos -- no deberían poder
    # guardarse (iot_canales.guardar los rechaza), pero una configuración
    # vieja puede traerlos.
    exactos = [clave for nombre, clave in candidatos if nombre == resto]
    if len(exactos) == 1:
        return ("ok", exactos[0])
    if len(exactos) > 1:
        return ("ambiguo", None)

    puntajes = sorted(((_parecido(resto, nombre), clave) for nombre, clave in candidatos),
                      reverse=True)
    if not puntajes or puntajes[0][0] < UMBRAL_CONFIANZA:
        return ("ninguno", None)
    # _actuadores_con_nombre trae una entrada por clave, así que el segundo
    # de la lista siempre es OTRO actuador.
    if len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < MARGEN_AMBIGUEDAD:
        return ("ambiguo", None)
    return ("ok", puntajes[0][1])


def interpretar(texto: str) -> dict:
    """{"tipo": "lavado_iniciar"|"lavado_cancelar"|"actuador"|"desconocido",
    "clave": ..., "prender": ...} -- las dos últimas solo si tipo == "actuador".

    El primer token decide la intención; el resto de la frase solo elige
    QUÉ actuador. Cada rama está sesgada hacia el lado seguro: arrancar el
    lavado exige la frase completa y bien dicha, mientras que un verbo de
    parar alcanza solo."""
    texto_norm = _normalizar(texto)
    if not texto_norm:
        return {"tipo": "desconocido"}
    palabras = texto_norm.split()
    verbo, resto = palabras[0], " ".join(palabras[1:])

    if verbo in VERBOS_INICIAR:
        # Arrancar bombas es la dirección peligrosa: se exige que el resto
        # de la frase NOMBRE el lavado ("iniciar el lavado"), no alcanza
        # con el verbo suelto ni con un verbo seguido de cualquier cosa.
        if _es_del_lavado(resto, vacio_cuenta=False):
            return {"tipo": "lavado_iniciar"}
        return {"tipo": "desconocido"}

    if verbo in VERBOS_PRENDER:
        hallazgo, clave = _buscar_actuador(resto)
        if hallazgo == "ok":
            return {"tipo": "actuador", "clave": clave, "prender": True}
        return {"tipo": "desconocido"}

    if verbo in VERBOS_CANCELAR:
        # El lavado se chequea PRIMERO: si el tambo llegara a llamar
        # "Lavado" a una salida, "parar el lavado" tiene que seguir siendo
        # cancelar el ciclo y no apagar ese relé.
        if _es_del_lavado(resto):
            return {"tipo": "lavado_cancelar"}
        hallazgo, clave = _buscar_actuador(resto)
        if hallazgo == "ok":
            return {"tipo": "actuador", "clave": clave, "prender": False}
        if hallazgo == "ambiguo":
            return {"tipo": "desconocido"}
        # Verbo de parar + algo que no se reconoce: se cancela el lavado
        # igual. Es la dirección segura y la que el operario espera cuando
        # dice "parar" (CLAUDE.md: frenar no debe tener trabas). Si no hay
        # ningún lavado corriendo, cancelar no toca ningún relé.
        return {"tipo": "lavado_cancelar"}

    if verbo in VERBOS_APAGAR:
        # "apagar" es ambiguo a propósito: sin evidencia positiva de una de
        # las dos cosas no se hace nada.
        if _es_del_lavado(resto):
            return {"tipo": "lavado_cancelar"}
        hallazgo, clave = _buscar_actuador(resto)
        if hallazgo == "ok":
            return {"tipo": "actuador", "clave": clave, "prender": False}
        return {"tipo": "desconocido"}

    return {"tipo": "desconocido"}


def _en_uso_por_lavado(clave: str) -> bool:
    estado_lavado = lavado_programa.estado()
    if not estado_lavado.get("activo"):
        return False
    programa = lavado_programa.etapas()
    etapa_actual = estado_lavado["etapa_actual"]
    if etapa_actual >= len(programa):
        return False
    return clave in programa[etapa_actual]["reles"]


def estado() -> dict:
    """clave -> encendido_desde (ISO) para lo sostenido por voz ahora mismo."""
    con = _conectar_db()
    try:
        filas = con.execute("SELECT clave, encendido_desde FROM voz_actuadores_estado").fetchall()
    finally:
        con.close()
    return dict(filas)


def apagados_pendientes() -> dict:
    """clave -> pedido_en (ISO) de los apagados EXPLÍCITOS todavía sin
    aplicar por Modbus. Los consume iot_lavado.procesar_comandos_voz."""
    con = _conectar_db()
    try:
        filas = con.execute("SELECT clave, pedido_en FROM voz_apagados_pendientes").fetchall()
    finally:
        con.close()
    return dict(filas)


def consumir_apagado_pendiente(clave: str) -> None:
    """Lo llama iot_lavado cuando ya escribió el apagado por Modbus (o
    cuando lo descartó por pertenecer a la etapa activa de un lavado)."""
    with _lock:
        con = _conectar_db()
        try:
            con.execute("DELETE FROM voz_apagados_pendientes WHERE clave = ?", (clave,))
            con.commit()
        finally:
            con.close()


def solicitar_encendido(clave: str) -> bool:
    """True si quedó registrado. False si ese actuador está en uso por una
    etapa activa de Lavado Automático (se ignora, no se toca nada)."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        con = _conectar_db()
        try:
            con.execute(
                "INSERT INTO voz_actuadores_estado (clave, encendido_desde) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET encendido_desde = excluded.encendido_desde",
                (clave, ahora),
            )
            # Un encendido pisa un apagado que todavía no se llegó a
            # aplicar: manda el pedido más nuevo, no el que quedó en la cola.
            con.execute("DELETE FROM voz_apagados_pendientes WHERE clave = ?", (clave,))
            con.commit()
        finally:
            con.close()
    return True


def solicitar_apagado(clave: str) -> bool:
    """Mismo criterio que solicitar_encendido: False (ignorado) si está en
    uso por el lavado automático.

    Además de borrar el estado sostenido, deja el apagado ANOTADO como
    pendiente: la pantalla ya dice "Apagando X", así que la escritura por
    Modbus tiene que pasar sí o sí, aunque la capa de voz no fuera la que
    prendió ese relé (ver voz_apagados_pendientes en _conectar_db)."""
    if clave not in ACTUADORES_VALIDOS:
        raise ValueError(f"Actuador desconocido: {clave!r}.")
    if _en_uso_por_lavado(clave):
        return False
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    with _lock:
        con = _conectar_db()
        try:
            con.execute("DELETE FROM voz_actuadores_estado WHERE clave = ?", (clave,))
            con.execute(
                "INSERT INTO voz_apagados_pendientes (clave, pedido_en) VALUES (?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET pedido_en = excluded.pedido_en",
                (clave, ahora),
            )
            con.commit()
        finally:
            con.close()
    return True


def limpiar_estado(clave: str) -> None:
    """Borra el estado sostenido de `clave` (y cualquier apagado pendiente,
    que ya queda satisfecho) SIN chequear si está en uso por el lavado -- lo
    llama el propio motor de Lavado Automático
    (iot_lavado.procesar_ciclo_lavado) cuando apaga un relé como parte de
    su propia secuencia, para que la próxima vuelta de
    procesar_comandos_voz no intente prenderlo de nuevo."""
    with _lock:
        con = _conectar_db()
        try:
            con.execute("DELETE FROM voz_actuadores_estado WHERE clave = ?", (clave,))
            con.execute("DELETE FROM voz_apagados_pendientes WHERE clave = ?", (clave,))
            con.commit()
        finally:
            con.close()
