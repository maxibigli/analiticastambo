"""Envío programado de informes (Fase 4c de los informes personalizables).

Un informe GUARDADO se puede programar para mandarse por correo, Telegram o WhatsApp en los días y horas que se elijan, con un resumen en texto y
(por correo y Telegram) el Excel adjunto. Se manda la versión GUARDADA del informe, calculada en el momento del envío.

Reglas, todas con prueba:
  * NUNCA se manda dos veces lo mismo. Cada (programación, horario, canal) se RECLAMA en `informes.db` (UNIQUE) antes de mandar: un reinicio, dos pasadas
    seguidas o dos procesos no duplican. Lo que quedó a medias (el proceso murió) pasa a «sin confirmar» y NO se reenvía solo: es preferible un aviso
    en la pantalla que un mensaje repetido (el tambo ya tuvo un pico de decenas de mensajes por un proceso duplicado).
  * Un canal que falla no cancela a los otros ni repite al que ya salió. Un error de base se reintenta hasta `MAX_INTENTOS` veces con `REINTENTO_MIN`
    minutos de espera, siempre dentro de la hora de gracia; un informe que ya no se puede calcular (la definición dejó de compilar) no se reintenta.
  * Lo que venció hace más de `GRACIA_MIN` minutos (el servidor estuvo apagado) NO sale tarde, pero deja una fila «omitido» por canal en el registro (la pantalla dice «no salió»),
    y nada sale por un horario anterior a la programación.
  * Antes de mandar por CADA canal se vuelve a mirar que la programación siga activa y sin tocar: calcular un informe puede tardar minutos y un «Pausar» o un «Guardar» en el medio
    no puede llegar tarde. Y un fallo no deja escrito «Se reintenta.» si ya no se va a reintentar (se tocó la programación, o pasó la hora de gracia).
  * Ningún error guarda ni muestra el texto de la excepción (puede traer el token de Telegram o un teléfono): se guarda una frase fija por canal.
  * No hay DELETE: una programación se PAUSA. «Probar» arma y manda de verdad pero no anota nada.
  * Los destinatarios son los de siempre (uno fijo por canal, por variable de entorno): no se agregan destinatarios nuevos ni datos personales.

El hilo (`iniciar_hilo`) lo arranca `servidor.py` DESPUÉS del candado de instancia única, nunca al importar `app`. Revisa cada `TICK_S` segundos que haya
algo vencido (una lectura de `informes.db`); recién cuando algo vence calcula el informe contra la base del tambo.
"""
import contextlib
import datetime
import decimal
import logging
import math
import re
import sqlite3
import threading
import time

import config_alertas
import envio_red
import informes_excel
import informes_guardados
import informes_motor
import telegram_bot

log = logging.getLogger("informes_envios")

CANALES = tuple(config_alertas.CANALES)
FORMATOS = ("excel", "texto")
MAX_HORARIOS = config_alertas.MAX_HORARIOS
MAX_PROGRAMADOS = 10                  # programaciones activas por tambo
GRACIA_MIN = 60                       # un horario vencido hace más que esto ya no sale
REINTENTO_MIN = 10
MAX_INTENTOS = 3
COLGADO_MIN = 30                      # un envío «en curso» más viejo que esto se da por perdido (sin confirmar)
TICK_S = 60
PRIMERA_ESPERA_S = 90
FILAS_TEXTO = 10
COLUMNAS_TEXTO = 6
GRUPOS_TEXTO = 8
TEXTO_MAX = 1500                      # el tope de un mensaje de WhatsApp (Twilio) es 1.600
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
NOMBRE_CANAL = {"whatsapp": "WhatsApp", "telegram": "Telegram", "correo": "correo"}
# Con `fullmatch` y `[0-9]`: `$` deja pasar un salto de línea final («08:00\n» era OTRO horario) y `\d` acepta dígitos de otros alfabetos («0٨:00»).
_HORA = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
DIAS_RASTRO = 7                       # hasta cuántos días atrás se anota un horario que no salió porque el servidor estaba apagado
AVISO_ORIGEN = "Ojo: se trajo de DelPro con diferencias que cambian qué animales aparecen: puede no coincidir con el informe de DelPro."     # va en CADA mensaje de un informe así
OJO_MIN = 100                        # lo menos que ocupa la línea «Ojo: incluye campos sin verificar…» cuando hay que acortar la lista de nombres
TOTAL_MIN = 40                        # y la del «Total:» cuando hay que acortarla

# Textos fijos del registro (nunca el texto de una excepción: puede traer el token de Telegram o un teléfono).
DETALLE_CAMBIO = "Se pausó o se cambió la programación antes de mandarlo."
DETALLE_APAGADO = f"No salió: el servidor estaba apagado a esa hora y no se manda con más de {GRACIA_MIN} minutos de atraso."
_FRASE_DE_WHATSAPP = {
    "ventana_24h": "WhatsApp no entregó el mensaje: solo deja mandar texto libre si el destinatario escribió en las últimas 24 horas.",
    "sandbox": "WhatsApp no entregó el mensaje: el número de destino no está unido al sandbox de Twilio.",
    "no_entregado": "WhatsApp no entregó el mensaje: Twilio lo recibió pero no llegó a destino.",
}


class EnvioError(ValueError):
    """La programación no es válida (o no se puede hacer eso). El mensaje se le muestra al usuario."""


class ExcelError(EnvioError):
    """No se pudo armar el Excel del informe. Es un fallo PERMANENTE (el mismo informe daría el mismo archivo roto): no se reintenta. Nunca lleva el texto de la excepción."""


# ============================================================================================ la base

@contextlib.contextmanager
def _con():
    """`informes.db` (la misma de los informes guardados; la ruta se lee en cada llamada para que las pruebas puedan fijar la suya). Se cierra siempre."""
    con = sqlite3.connect(informes_guardados.RUTA_DB, timeout=15)
    try:
        con.row_factory = sqlite3.Row
        informes_guardados.activar_wal(con)
        _crear_tablas(con)
        with con:
            yield con
    finally:
        con.close()


def _crear_tablas(con) -> None:
    informes_guardados._crear_tabla(con)
    con.execute("""CREATE TABLE IF NOT EXISTS envios_programados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        informe_id INTEGER NOT NULL UNIQUE,
        tambo TEXT NOT NULL,
        dias TEXT NOT NULL,
        horas TEXT NOT NULL,
        canales TEXT NOT NULL,
        formato TEXT NOT NULL,
        activo INTEGER NOT NULL DEFAULT 1,
        creado TEXT NOT NULL,
        modificado TEXT NOT NULL,
        usuario TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS envios_registro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        envio_id INTEGER NOT NULL,
        slot TEXT NOT NULL,
        canal TEXT NOT NULL,
        estado TEXT NOT NULL,
        intentos INTEGER NOT NULL DEFAULT 1,
        inicio TEXT NOT NULL,
        fin TEXT,
        detalle TEXT,
        UNIQUE (envio_id, slot, canal))""")
    con.execute("CREATE TABLE IF NOT EXISTS envios_estado (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _de_iso(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def _slot_iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def _texto_fecha(dt: datetime.datetime, con_hora: bool = True) -> str:
    return f"{DIAS[dt.weekday()]} {dt:%d/%m/%Y}" + (f" {dt:%H:%M}" if con_hora else "")


texto_fecha = _texto_fecha            # «jue 27/08/2026 08:30»: así se escribe toda fecha de la pantalla y de los mensajes


def _ahora(ahora):
    """La hora: la que se pasó (un `datetime`, o una función que la da en ese momento) o la del reloj de la PC."""
    if callable(ahora):
        return ahora()
    return ahora or datetime.datetime.now()


def _reloj(ahora):
    """Una función que da la hora de AHORA cada vez que se la llama (la de `ahora` si es fija o una función, o la del reloj de la PC)."""
    if callable(ahora):
        return ahora
    if ahora is not None:
        return lambda: ahora
    return datetime.datetime.now


# ============================================================================================ la programación

def validar(dias, horas, canales, formato) -> dict:
    """Los días (0 = lunes), horas (HH:MM), canales y formato de una programación, normalizados (sin repetir y en orden). `EnvioError` si no sirven."""
    if not isinstance(dias, (list, tuple)) or not dias or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in dias):
        raise EnvioError("Elegí al menos un día de la semana.")
    if not isinstance(horas, (list, tuple)) or not horas or not all(isinstance(h, str) and _HORA.fullmatch(h) for h in horas):
        raise EnvioError("Elegí al menos un horario, con formato HH:MM.")
    horas = sorted(set(horas))
    if len(horas) > MAX_HORARIOS:
        raise EnvioError(f"Como máximo {MAX_HORARIOS} horarios por día.")
    if not isinstance(canales, (list, tuple)) or not canales or not all(c in CANALES for c in canales if isinstance(c, str)) or not all(isinstance(c, str) for c in canales):
        raise EnvioError("Elegí al menos un canal: " + ", ".join(NOMBRE_CANAL[c] for c in CANALES) + ".")
    if formato not in FORMATOS:
        raise EnvioError("El formato tiene que ser «excel» o «texto».")
    return {"dias": sorted(set(dias)), "horas": horas, "canales": sorted(set(canales)), "formato": formato}


def _json(x) -> str:
    import json
    return json.dumps(x, ensure_ascii=False)


def _fila_a_envio(r, ahora=None) -> dict:
    import json
    e = {"id": r["id"], "informe_id": r["informe_id"], "tambo": r["tambo"], "dias": json.loads(r["dias"]), "horas": json.loads(r["horas"]),
         "canales": json.loads(r["canales"]), "formato": r["formato"], "activo": bool(r["activo"]), "modificado": r["modificado"], "usuario": r["usuario"]}
    p = proximo(e, _ahora(ahora))
    e["proximo"] = _iso(p) if p else None
    e["proximo_texto"] = _texto_fecha(p) if p else None
    return e


def obtener(tambo: str, informe_id: int, ahora=None):
    if not informes_guardados.id_valido(informe_id):
        return None                                                    # un id que SQLite no puede guardar (≥ 2^63) es un informe que no existe, no un error
    with _con() as con:
        r = con.execute("SELECT * FROM envios_programados WHERE informe_id = ? AND tambo = ?", (informe_id, tambo)).fetchone()
    return _fila_a_envio(r, ahora) if r else None


def listar(tambo: str, ahora=None) -> dict:
    """{id del informe: programación} de un tambo (pausadas incluidas)."""
    with _con() as con:
        filas = con.execute("SELECT * FROM envios_programados WHERE tambo = ?", (tambo,)).fetchall()
    return {r["informe_id"]: _fila_a_envio(r, ahora) for r in filas}


def _cerrar_promesas_de(con, informe_id: int, tambo: str, motivo: str) -> None:
    """Un fallo que prometía «Se reintenta.» ya no se va a reintentar (se tocó la programación, o se pausó): se lo dice, en vez de dejar escrita una promesa que no se cumple."""
    con.execute("UPDATE envios_registro SET intentos = ?, detalle = REPLACE(COALESCE(detalle, ''), ' Se reintenta.', '') || ? "
                "WHERE estado = 'fallido' AND intentos < ? AND envio_id = (SELECT id FROM envios_programados WHERE informe_id = ? AND tambo = ?)",
                (MAX_INTENTOS, f" Ya no se reintenta: {motivo}", MAX_INTENTOS, informe_id, tambo))


def guardar(tambo: str, informe_id: int, dias, horas, canales, formato, activo, usuario, ahora=None) -> dict:
    """Crea o actualiza la programación de un informe. Los horarios anteriores a este momento no se mandan (nada sale «retroactivo»).

    Tocar la programación descarta los reintentos pendientes (la pasada exige que `modificado` no haya cambiado): los fallos que prometían reintentarse lo dicen."""
    datos = validar(dias, horas, canales, formato)
    informe = informes_guardados.obtener(tambo, informe_id)
    if informe is None:
        raise EnvioError("Ese informe no existe.")
    if informe["archivado"]:
        raise EnvioError("Ese informe está archivado: restauralo para programar su envío.")
    ahora = _ahora(ahora)
    with _con() as con:
        con.execute("BEGIN IMMEDIATE")                                      # dos «Guardar» a la vez: el segundo espera al primero y ve su fila (y el tope de activas se cuenta bien)
        if activo:
            otras = con.execute("SELECT COUNT(*) FROM envios_programados WHERE tambo = ? AND activo = 1 AND informe_id <> ?", (tambo, informe_id)).fetchone()[0]
            if otras >= MAX_PROGRAMADOS:
                raise EnvioError(f"Ya hay {MAX_PROGRAMADOS} informes con envío programado: pausá alguno para programar otro.")
        # Crea o actualiza en UNA sentencia: aun si otro proceso insertó la fila entre medio, no hay un error de clave repetida.
        con.execute("INSERT INTO envios_programados (informe_id, tambo, dias, horas, canales, formato, activo, creado, modificado, usuario) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(informe_id) DO UPDATE SET dias = excluded.dias, horas = excluded.horas, canales = excluded.canales, formato = excluded.formato, "
                    "activo = excluded.activo, modificado = excluded.modificado, usuario = excluded.usuario",
                    (informe_id, tambo, _json(datos["dias"]), _json(datos["horas"]), _json(datos["canales"]), datos["formato"], int(bool(activo)),
                     _iso(ahora), _iso(ahora), usuario))
        _cerrar_promesas_de(con, informe_id, tambo, "se cambió la programación." if activo else "se pausó la programación.")
    return obtener(tambo, informe_id, ahora)


def pausar(tambo: str, informe_id: int, ahora=None) -> dict:
    """Pausa la programación (nunca se borra)."""
    actual = obtener(tambo, informe_id, ahora)
    if actual is None:
        raise EnvioError("Ese informe no tiene envío programado.")
    ahora = _ahora(ahora)
    with _con() as con:
        con.execute("UPDATE envios_programados SET activo = 0, modificado = ? WHERE id = ?", (_iso(ahora), actual["id"]))
        _cerrar_promesas_de(con, informe_id, tambo, "se pausó la programación.")
    return obtener(tambo, informe_id, ahora)


def borrar_de_informe(tambo: str, informe_id: int) -> bool:
    """Borra la programación de un informe Y el registro de lo que se mandó (para cuando el informe se elimina para siempre). Dice si había algo que borrar. El número del informe no se
    reutiliza, así que el registro de otro informe nunca se confunde con este."""
    if not informes_guardados.id_valido(informe_id):
        return False
    with _con() as con:
        con.execute("BEGIN IMMEDIATE")
        fila = con.execute("SELECT id FROM envios_programados WHERE informe_id = ? AND tambo = ?", (informe_id, tambo)).fetchone()
        if fila is None:
            return False
        con.execute("DELETE FROM envios_registro WHERE envio_id = ?", (fila["id"],))
        con.execute("DELETE FROM envios_programados WHERE id = ?", (fila["id"],))
    return True


# ============================================================================================ cuándo vence

def _horarios_del_dia(e: dict, dia: datetime.date) -> list:
    if dia.weekday() not in e["dias"]:
        return []
    return [datetime.datetime.combine(dia, datetime.time(int(h[:2]), int(h[3:]))) for h in e["horas"]]


def proximo(e: dict, ahora: datetime.datetime):
    """El próximo horario programado (después de `ahora`), o None si está pausada."""
    if not e.get("activo"):
        return None
    for k in range(0, 9):
        for slot in _horarios_del_dia(e, ahora.date() + datetime.timedelta(days=k)):
            if slot > ahora:
                return slot
    return None


def slots_vencidos(e: dict, ahora: datetime.datetime, gracia_min: int = GRACIA_MIN) -> list:
    """Los horarios que ya llegaron, hace menos de `gracia_min` minutos y después de la última vez que se tocó la programación."""
    if not e.get("activo"):
        return []
    desde = _de_iso(e["modificado"])
    limite = datetime.timedelta(minutes=gracia_min)
    out = []
    for k in (1, 0):
        for slot in _horarios_del_dia(e, ahora.date() - datetime.timedelta(days=k)):
            if desde < slot <= ahora and ahora - slot <= limite:
                out.append(slot)
    return sorted(out)


def slots_perdidos(e: dict, ahora: datetime.datetime, gracia_min: int = GRACIA_MIN, dias: int = DIAS_RASTRO) -> list:
    """Los horarios que vencieron hace MÁS de `gracia_min` minutos (el servidor estaba apagado) y que ya no salen: son posteriores a la última vez que se tocó la programación,
    de los últimos `dias` días. Se anotan en el registro para que la pantalla diga «no salió» en vez de mostrar solo el próximo de la semana siguiente."""
    if not e.get("activo"):
        return []
    desde = _de_iso(e["modificado"])
    limite = datetime.timedelta(minutes=gracia_min)
    out = []
    for k in range(dias, -1, -1):
        for slot in _horarios_del_dia(e, ahora.date() - datetime.timedelta(days=k)):
            if desde < slot and ahora - slot > limite:
                out.append(slot)
    return sorted(out)


# ============================================================================================ el registro

def registro(tambo: str, informe_id: int, limite: int = 15) -> list:
    """Lo último que pasó con los envíos de un informe, lo más nuevo primero."""
    if not informes_guardados.id_valido(informe_id):
        return []
    with _con() as con:
        filas = con.execute("SELECT r.* FROM envios_registro r JOIN envios_programados p ON p.id = r.envio_id WHERE p.informe_id = ? AND p.tambo = ? "
                            "ORDER BY r.slot DESC, r.canal LIMIT ?", (informe_id, tambo, int(limite))).fetchall()
    return [{"slot": r["slot"], "slot_texto": _texto_fecha(_de_iso(r["slot"] + ":00")), "canal": r["canal"], "estado": r["estado"], "intentos": r["intentos"],
             "detalle": r["detalle"], "inicio": r["inicio"], "fin": r["fin"]} for r in filas]


def ultima_pasada():
    """Cuándo revisó por última vez el hilo (para mostrar en la pantalla que está vivo), o None."""
    with _con() as con:
        r = con.execute("SELECT valor FROM envios_estado WHERE clave = 'ultima_pasada'").fetchone()
    return _de_iso(r["valor"]) if r else None


def _reclamar(con, envio_id: int, slot: datetime.datetime, canal: str, ahora: datetime.datetime) -> bool:
    """Anota «lo estoy mandando». True si ESTA llamada lo reclamó; False si ya estaba reclamado (por otra pasada, otro proceso o un reinicio)."""
    cur = con.execute("INSERT OR IGNORE INTO envios_registro (envio_id, slot, canal, estado, intentos, inicio) VALUES (?, ?, ?, 'en_curso', 1, ?)",
                      (envio_id, _slot_iso(slot), canal, _iso(ahora)))
    return cur.rowcount == 1


def _reclamar_reintento(con, fila_id: int, intentos: int, ahora: datetime.datetime) -> bool:
    cur = con.execute("UPDATE envios_registro SET estado = 'en_curso', intentos = intentos + 1, inicio = ?, fin = NULL "
                      "WHERE id = ? AND estado = 'fallido' AND intentos = ?", (_iso(ahora), fila_id, intentos))
    return cur.rowcount == 1


def _cerrar(con, envio_id: int, slot: datetime.datetime, canal: str, estado: str, detalle: str, ahora: datetime.datetime, intentos=None) -> None:
    if intentos is None:
        con.execute("UPDATE envios_registro SET estado = ?, detalle = ?, fin = ? WHERE envio_id = ? AND slot = ? AND canal = ?",
                    (estado, detalle, _iso(ahora), envio_id, _slot_iso(slot), canal))
    else:
        con.execute("UPDATE envios_registro SET estado = ?, detalle = ?, fin = ?, intentos = ? WHERE envio_id = ? AND slot = ? AND canal = ?",
                    (estado, detalle, _iso(ahora), intentos, envio_id, _slot_iso(slot), canal))


# ============================================================================================ el contenido

_CONTEXTO_N = decimal.Context(prec=400, rounding=decimal.ROUND_HALF_UP)      # alcanza para cualquier `float` (hasta ~310 cifras) sin que `quantize` proteste


def _n(v, dec=0) -> str:
    """Un número como se lee en español: 1.234,5.

    Redondea como la pantalla (`toLocaleString('es-AR')`, ICU): la mitad hacia arriba, sobre el decimal MÁS CORTO del número (80,25 → «80,3»; 2,5 con 0 decimales → «3»). El formato de
    Python redondeaba el valor binario y, en un empate exacto, al par: el mismo promedio decía «80,2» en el mensaje y «80,3» en la pantalla."""
    if v is None:
        return "—"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, float) and not math.isfinite(v):
        return "—"
    d = decimal.Decimal(repr(v)) if isinstance(v, float) else decimal.Decimal(v)
    d = d.quantize(decimal.Decimal(1).scaleb(-dec), rounding=decimal.ROUND_HALF_UP, context=_CONTEXTO_N)
    s = f"{d:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _fecha_de(v):
    """La fecha de un valor (un `datetime`, un `date` o un texto que empieza con AAAA-MM-DD), o None."""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v)
    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        try:
            return datetime.date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _clave_de_grupo(meta: dict, v) -> str:
    """Cómo se nombra el grupo en el texto: una fecha con su día de la semana («sáb 01/08/2026», nunca «2026-08-01T00:00:00»), un número entero sin «.0», y «(sin dato)» si no hay."""
    if v is None:
        return "(sin dato)"
    if (meta or {}).get("tipo") == "fecha":
        f = _fecha_de(v)
        return _texto_fecha(f, False) if f else str(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if isinstance(v, int) or (math.isfinite(v) and float(v).is_integer()):
            return str(int(v))
        return _n(v, max((meta or {}).get("decimales") or 0, 1))
    return str(v)


def _celda(meta: dict, v) -> str:
    if v is None:
        return "—"
    if meta.get("tipo") == "num":
        return _n(v, meta.get("decimales") or 0)
    if meta.get("tipo") == "fecha":
        s = str(v)[:10]
        return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if re.match(r"^\d{4}-\d{2}-\d{2}", s) else str(v)
    s = str(v)
    return s if len(s) <= 24 else s[:23] + "…"


def _animales(n) -> str:
    return f"{_n(n)} animal" + ("" if n == 1 else "es")


def _llenar(encabezado: str, items: list, total: int, resto, presupuesto: int) -> tuple:
    """El encabezado y los `items` que entren en `presupuesto` letras (cada línea cuenta su salto). Si quedaron afuera algunos de los `total` que hay, una última
    línea `resto(cuántos)` que lo dice (siempre que entre). Devuelve (líneas, letras gastadas); si ni el encabezado entra, ([], 0)."""
    costo = lambda linea: len(linea) + 1
    if costo(encabezado) > presupuesto:
        return [], 0
    kept, gastado = [], costo(encabezado)
    for it in items:
        if gastado + costo(it) > presupuesto:
            break
        kept.append(it)
        gastado += costo(it)
    while len(kept) < total:
        mas = resto(total - len(kept))
        if gastado + costo(mas) <= presupuesto:
            return [encabezado] + kept + [mas], gastado + costo(mas)
        if not kept:
            return [encabezado], costo(encabezado)
        gastado -= costo(kept.pop())
    return [encabezado] + kept, gastado


def _acotar(linea: str, maximo: int) -> str:
    """La línea tal cual si entra en `maximo` letras; si no, cortada con «…» al final DE ESA LÍNEA."""
    return linea if len(linea) <= maximo else linea[:max(maximo - 1, 0)].rstrip() + "…"


def _linea_ojo(nombres: list, maximo: int = None) -> str:
    """«Ojo: incluye campos sin verificar contra DelPro (a, b): los valores pueden diferir.» Si con todos los nombres pasa de `maximo` letras, se acorta la LISTA («a, b y 3 más»):
    el aviso en sí nunca se corta."""
    pre, post = "Ojo: incluye campos sin verificar contra DelPro (", "): los valores pueden diferir."
    for k in range(len(nombres), 0, -1):
        linea = pre + ", ".join(nombres[:k]) + (f" y {len(nombres) - k} más" if k < len(nombres) else "") + post
        if maximo is None or len(linea) <= maximo:
            return linea
    return "Ojo: incluye campos sin verificar contra DelPro: los valores pueden diferir."


def texto_resumen(nombre: str, ahora: datetime.datetime, r: dict, con_adjunto: bool, limite: int = TEXTO_MAX, aviso_origen: bool = False) -> tuple:
    """(texto, animales, campos sin verificar). Texto plano (Telegram y WhatsApp no interpretan nada), con la fecha y el día de la semana.

    `limite`: cuántas letras como máximo (Telegram corta en 1.024 el texto que acompaña a un archivo, WhatsApp en 1.600). **Lo fijo va SIEMPRE** (qué informe, cuándo, cuántos
    animales, el total, el aviso de campos sin verificar y «va adjunto») y la lista de grupos o de filas llena lo que sobre: lo que se pierde por falta de lugar tiene que ser
    la lista, nunca el total ni los avisos. Si lo fijo solo ya no entra, se acorta la línea del «Total:» (y la lista de nombres del aviso), no el final del texto.

    Si la lista se cortó porque el INFORME eligió un límite (`limite_pedido`: «las 10 vacas que más producen»), no se dice que «se cortó»: se dice que se listan las primeras N.

    `aviso_origen`: el informe se trajo de DelPro con diferencias que cambian QUÉ animales aparecen (ver `informes_guardados`, la «nota de origen»). Lo que llega por un canal no tiene
    la pantalla al lado, así que la línea va en la cabecera: es de lo fijo, nunca se recorta."""
    cols = r["columnas"]
    tot = r.get("totales")
    animales = tot["n"] if tot else len(r["filas"])
    pedido = bool(r.get("truncado") and r.get("limite_pedido"))
    corte = ""
    if r.get("truncado"):
        n_filas = len(r["filas"])
        if pedido:
            corte = " (se lista la primera fila, como pide el informe)" if n_filas == 1 else f" (se listan las primeras {_n(n_filas)} filas, como pide el informe)"
        else:
            corte = f" (la lista se cortó en {_n(n_filas)} filas)"
    cabecera = [f"Informe «{nombre}»", f"Calculado el {_texto_fecha(ahora, False)} a las {ahora:%H:%M}", _animales(animales) + corte] + ([AVISO_ORIGEN] if aviso_origen else [])
    res =[(i, c) for i, c in enumerate(cols) if c.get("resumen") and c["resumen"] != "none"]

    def valor(c, v):
        if c.get("tipo") == "fecha" and c["resumen"] in ("min", "max"):
            return _celda(c, v)                                                       # una fecha se escribe como en las filas (dd/mm/aaaa), no en ISO
        return _n(v, 0 if c["resumen"] == "count" else (1 if c["resumen"] == "avg" else (c.get("decimales") or 0)))        # un conteo es siempre un entero

    def con_resumenes(valores: dict) -> str:
        partes = [f"{c['etiqueta']} ({informes_motor.NOMBRE_RESUMEN[c['resumen']].lower()}): {valor(c, valores.get('c' + str(i)))}"
                  for i, c in res if valores.get("c" + str(i)) is not None]
        return " · ".join(partes)

    total = ("Total: " + con_resumenes(tot["resumenes"])) if (tot and res and con_resumenes(tot["resumenes"])) else None
    sin_verificar = [c["etiqueta"] for c in cols if not c.get("verificado", True)]
    ojo = _linea_ojo(sin_verificar) if sin_verificar else None
    adjunto = ("El Excel va adjunto." if pedido else "El Excel completo va adjunto.") if con_adjunto else None
    # Lo fijo va siempre. Si las tres líneas no entran juntas en `limite`, se acorta lo que se puede acortar sin perder QUÉ dice: la lista de nombres del «Ojo» y la línea del «Total».
    presentes = [x for x in (total, ojo, adjunto) if x]
    resto = limite - len("\n".join(cabecera)) - len(presentes) - (len(adjunto) if adjunto else 0)
    if total is not None and ojo is not None and len(total) + len(ojo) > resto:
        reservado_total = max(TOTAL_MIN, min(len(total), resto // 2))
        ojo = _linea_ojo(sin_verificar, max(OJO_MIN, resto - reservado_total))
        total = _acotar(total, max(TOTAL_MIN, resto - len(ojo)))
    elif ojo is not None and len(ojo) > resto:
        ojo = _linea_ojo(sin_verificar, max(OJO_MIN, resto))
    elif total is not None and len(total) > resto:
        total = _acotar(total, max(TOTAL_MIN, resto))
    fijas = [x for x in (total, ojo, adjunto) if x]
    presupuesto = limite - len("\n".join(cabecera + fijas))
    en_el_excel = " (están en el Excel)" if con_adjunto else ""

    grupos1 = [g for g in (r.get("grupos") or []) if g.get("nivel") == 1]
    bloque_grupos, bloque_filas = [], []
    if grupos1:
        meta_grupo = (r.get("agrupar") or [{}])[0]
        items = []
        for g in grupos1[:GRUPOS_TEXTO]:
            items.append(f"- {_clave_de_grupo(meta_grupo, g['claves'][0])}: {_animales(g['n'])}" + (" · " + con_resumenes(g["resumenes"]) if res and con_resumenes(g["resumenes"]) else ""))
        bloque_grupos, gastado = _llenar("Por " + ", ".join(a["etiqueta"] for a in r["agrupar"]) + ":", items, len(grupos1), lambda n: f"- y {n} más{en_el_excel}", presupuesto)
        presupuesto -= gastado
    if r.get("detalle", True) and r["filas"] and not grupos1:
        mostrar = cols[:COLUMNAS_TEXTO]
        items = [" | ".join(_celda(c, v) for c, v in zip(mostrar, f)) for f in r["filas"][:FILAS_TEXTO]]
        bloque_filas, _ = _llenar("Primeras filas: " + " | ".join(c["etiqueta"] for c in mostrar), items, len(r["filas"]), lambda n: f"... y {_n(n)} filas más{en_el_excel}", presupuesto)
    texto = "\n".join(cabecera + bloque_grupos + fijas + bloque_filas)
    if len(texto) > limite:
        texto = texto[:limite - 1].rstrip() + "…"
    return texto, animales, sin_verificar


def armar(tambo: str, informe: dict, formato: str, ahora=None, ejecutar=None) -> dict:
    """Calcula el informe y arma lo que se manda: {asunto, texto, texto_sin_adjunto, texto_telegram, nombre_archivo, datos (el Excel, o None en formato texto), animales, truncado,
    sin_verificar, segundos}. Levanta `informes_motor.InformeError` si la definición dejó de compilar (no se reintenta) y deja pasar los errores de base.

    Hay un texto por canal porque no todos reciben lo mismo: `texto` (correo, con el Excel adjunto: dice que va adjunto), `texto_telegram` (el pie de un archivo, que Telegram corta
    en 1.024 letras) y `texto_sin_adjunto` (WhatsApp, o cualquier canal que no lleva el archivo: no puede prometer un adjunto que no recibe). `ahora` puede ser una función: el
    «Calculado el» es la hora en que TERMINÓ de calcular, no la de cuando empezó la pasada."""
    ejecutar = ejecutar or informes_motor.ejecutar
    defn = informe["definicion"]
    r = ejecutar(defn, tambo, hoy=None)
    ahora = _ahora(ahora)
    nombre = informe["nombre"]
    datos = None
    if formato == "excel":
        try:
            # Las fechas de las condiciones y el «Generado» llevan el día de la semana, y la hoja dice el límite que eligió el informe.
            datos = informes_excel.a_xlsx(r, nombre, condiciones=informes_motor.describir_condiciones(defn, con_dia=True, con_limite=True), generado=ahora)
        except Exception as exc:  # noqa: BLE001 -- un Excel que no se puede armar se va a repetir igual: es permanente, y el texto de la excepción no se guarda ni se muestra
            log.warning("no se pudo armar el Excel del informe: %s", type(exc).__name__)
            raise ExcelError("No se pudo armar el Excel del informe.") from None
    con_aviso = bool((informe.get("origen") or {}).get("avisos"))                    # traído de DelPro con diferencias en los animales que aparecen
    texto, animales, sin_verificar = texto_resumen(nombre, ahora, r, datos is not None, aviso_origen=con_aviso)
    texto_sin_adjunto = texto_resumen(nombre, ahora, r, False, aviso_origen=con_aviso)[0] if datos is not None else texto
    texto_telegram = texto_resumen(nombre, ahora, r, True, telegram_bot.CAPTION_MAX, aviso_origen=con_aviso)[0] if datos is not None else texto
    return {"asunto": f"LactIA — {nombre} ({_texto_fecha(ahora, False)})", "texto": texto, "texto_sin_adjunto": texto_sin_adjunto, "texto_telegram": texto_telegram,
            "nombre_archivo": informes_excel.nombre_de_archivo(nombre, ahora.date()), "datos": datos, "animales": animales, "truncado": bool(r.get("truncado")),
            "sin_verificar": sin_verificar, "segundos": r.get("segundos")}


def texto_para(armado: dict, canal: str) -> str:
    """El texto que recibe CADA canal de un envío armado (el que usa `app._enviar_informe_a_canal` y el que muestra «Ver cómo queda»): el correo y Telegram, con el Excel, dicen que
    va adjunto (el de Telegram entra en el pie de 1.024 letras); sin archivo, y WhatsApp siempre, reciben el texto que no promete un adjunto."""
    sin_adjunto = armado.get("texto_sin_adjunto", armado["texto"])
    con_archivo = bool(armado.get("datos"))
    if canal == "correo":
        return armado["texto"] if con_archivo else sin_adjunto
    if canal == "telegram":
        return armado.get("texto_telegram", armado["texto"]) if con_archivo else sin_adjunto
    return sin_adjunto


def armar_de_la_base(tambo: str, informe_id: int, formato: str, ahora=None, ejecutar=None) -> dict:
    informe = informes_guardados.obtener(tambo, informe_id)
    if informe is None:
        raise EnvioError("Ese informe no existe.")
    if informe["archivado"]:                                              # un archivado no manda nada, aunque su programación siguiera activa
        raise EnvioError("Ese informe está archivado: restauralo para programar su envío.")
    return armar(tambo, informe, formato, ahora, ejecutar)


# ============================================================================================ la pasada

def _fallo_de_canal(canal: str, exc: Exception) -> tuple:
    """(estado, detalle, ¿se puede reintentar?) de un canal que levantó al mandar. Solo lo que se SABE que no salió (`no_salio`, ver `envio_red`) puede reintentarse, y solo si
    además tiene sentido (`reintentable`: no se pudo conectar); un fallo INCIERTO (el canal pudo haber aceptado el mensaje antes de fallar) queda «sin confirmar» y no se
    reenvía solo: reintentarlo lo duplicaba. Nunca lleva el texto de la excepción (puede traer el token de Telegram o un teléfono)."""
    nombre = NOMBRE_CANAL.get(canal, canal)
    if getattr(exc, "reintentable", False):
        return "fallido", f"No se pudo enviar por {nombre}.", True
    if isinstance(exc, envio_red.CanalNoDisponible):
        return "fallido", f"{nombre} no está configurado o está apagado en ⚙ Configuración.", False             # se destildó mientras se calculaba: se SABE que no se llamó a nadie
    if getattr(exc, "no_salio", False):
        motivo = getattr(exc, "motivo", None)                                                                       # un motivo de una lista cerrada (WhatsApp), nunca el texto de Twilio
        frase = _FRASE_DE_WHATSAPP.get(motivo) if isinstance(motivo, str) else None
        if frase:
            return "fallido", frase, False
        return "fallido", f"{nombre} no aceptó el envío: revisá su configuración en ⚙ Configuración.", False
    return "sin_confirmar", f"No se pudo confirmar si salió por {nombre}: revisalo antes de volver a mandarlo.", False


def _sigue_igual(e: dict) -> bool:
    """Si la programación sigue activa y sin tocar desde que la pasada la leyó. Se mira de nuevo ANTES de mandar (y de prometer un reintento): el cálculo del informe puede tardar
    minutos, y un «Pausar» o un «Guardar» en el medio no puede llegar tarde. Si la lectura falla, la pasada se corta ahí (lo reclamado queda «sin confirmar»): nunca se manda sin mirar."""
    with _con() as con:
        fila = con.execute("SELECT activo, modificado FROM envios_programados WHERE id = ?", (e["id"],)).fetchone()
    return fila is not None and bool(fila["activo"]) and fila["modificado"] == e["modificado"]


def _promesa(e: dict, n: int) -> tuple:
    """(texto que se agrega al detalle, intentos que se anotan) de un fallo que se reintentaría. Si la programación cambió mientras tanto el reintento no va a ocurrir:
    no se deja escrita una promesa que no se cumple."""
    if n >= MAX_INTENTOS:
        return " Ya no se reintenta.", n
    try:
        igual = _sigue_igual(e)
    except Exception as exc:  # noqa: BLE001 -- acá solo cambia una frase: si la base local no contesta, no se corta el anotado de los demás canales
        log.warning("envío programado %s: no se pudo mirar si la programación cambió (%s)", e["id"], type(exc).__name__)
        igual = True
    if not igual:
        return " Ya no se reintenta: se cambió la programación.", MAX_INTENTOS
    return " Se reintenta.", n


def _es_permanente(exc: Exception) -> bool:
    """Un fallo de cálculo que se repetiría igual (la tabla no existe en la base de ese tambo): no se recalcula tres veces."""
    import db
    return isinstance(exc, db.TablaNoDisponibleError)


def _anotar(e: dict, slot: datetime.datetime, canal: str, estado: str, detalle: str, reloj, intentos=None) -> None:
    """Anota cómo terminó un canal. Si anotar falla (la base local trabada) NO se corta el envío de los demás: queda «en curso» y a los 30 minutos «sin confirmar»."""
    try:
        with _con() as con:
            _cerrar(con, e["id"], slot, canal, estado, detalle, reloj(), intentos)
    except Exception as exc:  # noqa: BLE001
        log.warning("envío programado %s por %s: no se pudo anotar el resultado (%s)", e["id"], canal, type(exc).__name__)


def pasada(enviar, canales_disponibles, ahora=None, ejecutar=None) -> dict:
    """Una revisión: manda lo que venció y todavía no salió. `enviar(canal, armado)` manda por UN canal y levanta si falla; `canales_disponibles()` dice qué
    canales están tildados y configurados ahora. Devuelve {"enviados": [...], "fallidos": [...], "omitidos": [...], "sin_confirmar": [...]}.

    `ahora`: la hora (un `datetime` fijo, para las pruebas) o una función que la da. Cada decisión (cuánto falta, desde cuándo se espera un reintento, a qué hora salió
    algo) mira el reloj EN ESE MOMENTO: una pasada que tarda minutos (la base ocupada) no deja la hora del comienzo en todo lo que hace."""
    reloj = _reloj(ahora)
    resumen = {"enviados": [], "fallidos": [], "omitidos": [], "sin_confirmar": []}
    _dar_latido(reloj)                                                     # el latido se escribe al EMPEZAR, antes de cada programación y al terminar: una pasada larga no parece colgada
    inicio = reloj()
    with _con() as con:
        con.execute("UPDATE envios_registro SET estado = 'sin_confirmar', detalle = 'No se pudo confirmar si salió: revisalo antes de reenviar.', fin = ? "
                    "WHERE estado = 'en_curso' AND inicio < ?", (_iso(inicio), _iso(inicio - datetime.timedelta(minutes=COLGADO_MIN))))
        _cerrar_promesas_vencidas(con, inicio)
        programados = [_fila_a_envio(r, inicio) for r in con.execute("SELECT * FROM envios_programados WHERE activo = 1").fetchall()]
        for e in programados:
            _anotar_perdidos(con, e, inicio)
    for e in programados:
        for slot in slots_vencidos(e, reloj()):
            _dar_latido(reloj)
            try:
                _atender(e, slot, enviar, canales_disponibles, reloj, ejecutar, resumen)
            except Exception as exc:  # noqa: BLE001 -- una programación que falla no frena a las demás
                log.warning("envío programado %s (%s): la pasada falló: %s", e["id"], _slot_iso(slot), type(exc).__name__)
    _dar_latido(reloj)
    return resumen


def _latido(reloj) -> None:
    with _con() as con:
        con.execute("INSERT OR REPLACE INTO envios_estado (clave, valor) VALUES ('ultima_pasada', ?)", (_iso(reloj()),))


def _dar_latido(reloj) -> None:
    """Anota que el hilo está vivo (la pantalla avisa si hace más de 5 minutos que no revisa). Si no se puede escribir (la base local trabada) no corta la pasada."""
    try:
        _latido(reloj)
    except Exception as exc:  # noqa: BLE001
        log.warning("envío programado: no se pudo anotar el latido (%s)", type(exc).__name__)


def _anotar_perdidos(con, e: dict, ahora: datetime.datetime) -> None:
    """Un horario que venció con el servidor apagado más de `GRACIA_MIN` minutos no sale tarde (a propósito), pero tiene que dejar rastro: una fila «omitido» por canal, para que la
    pantalla diga «no salió» en vez de mostrar solo el próximo de la semana siguiente. Idempotente: `INSERT OR IGNORE` sobre el UNIQUE, y no pisa nada que ya tenga su fila."""
    for slot in slots_perdidos(e, ahora):
        for canal in e["canales"]:
            con.execute("INSERT OR IGNORE INTO envios_registro (envio_id, slot, canal, estado, intentos, inicio, fin, detalle) VALUES (?, ?, ?, 'omitido', 0, ?, ?, ?)",
                        (e["id"], _slot_iso(slot), canal, _iso(ahora), _iso(ahora), DETALLE_APAGADO))


def _cerrar_promesas_vencidas(con, ahora: datetime.datetime) -> None:
    """Los fallos que dicen «Se reintenta.» pero ya no se van a reintentar: pasó la hora de gracia, o la programación se pausó o se tocó después de ese horario
    (el reintento exige que el horario sea posterior a la última vez que se tocó). Ya no prometen nada."""
    gracia = datetime.timedelta(minutes=GRACIA_MIN)
    filas = con.execute("SELECT r.id, r.slot, p.activo, p.modificado FROM envios_registro r LEFT JOIN envios_programados p ON p.id = r.envio_id "
                        "WHERE r.estado = 'fallido' AND r.intentos < ?", (MAX_INTENTOS,)).fetchall()
    for f in filas:
        slot = _de_iso(f["slot"] + ":00")
        if ahora - slot > gracia:
            motivo = "pasó la hora de gracia."
        elif f["activo"] is None or not f["activo"]:
            motivo = "se pausó la programación."
        elif slot <= _de_iso(f["modificado"]):
            motivo = "se cambió la programación."
        else:
            continue
        con.execute("UPDATE envios_registro SET intentos = ?, detalle = REPLACE(COALESCE(detalle, ''), ' Se reintenta.', '') || ? WHERE id = ? AND estado = 'fallido' AND intentos < ?",
                    (MAX_INTENTOS, f" Ya no se reintenta: {motivo}", f["id"], MAX_INTENTOS))


def _atender(e: dict, slot: datetime.datetime, enviar, canales_disponibles, reloj, ejecutar, resumen: dict) -> None:
    disponibles = set(canales_disponibles())
    a_mandar = []                                                          # [(canal, intentos)]
    with _con() as con:
        con.execute("BEGIN IMMEDIATE")                                     # nadie cambia la programación mientras se la vuelve a mirar y se reclama
        fila = con.execute("SELECT activo, modificado FROM envios_programados WHERE id = ?", (e["id"],)).fetchone()
        if fila is None or not fila["activo"] or fila["modificado"] != e["modificado"]:
            return                                                         # se pausó o se cambió mientras esta pasada trabajaba en otra: la próxima pasada lo ve como es
        ahora = reloj()
        for canal in e["canales"]:
            if _reclamar(con, e["id"], slot, canal, ahora):
                if canal in disponibles:
                    a_mandar.append((canal, 1))
                else:
                    _cerrar(con, e["id"], slot, canal, "omitido", f"{NOMBRE_CANAL.get(canal, canal)} no está configurado o está apagado en ⚙ Configuración.", ahora)
                    resumen["omitidos"].append((e["id"], _slot_iso(slot), canal))
                continue
            fila = con.execute("SELECT id, estado, intentos, fin FROM envios_registro WHERE envio_id = ? AND slot = ? AND canal = ?", (e["id"], _slot_iso(slot), canal)).fetchone()
            if (fila and fila["estado"] == "fallido" and fila["intentos"] < MAX_INTENTOS and fila["fin"] and canal in disponibles
                    and ahora - _de_iso(fila["fin"]) >= datetime.timedelta(minutes=REINTENTO_MIN)
                    and _reclamar_reintento(con, fila["id"], fila["intentos"], ahora)):
                a_mandar.append((canal, fila["intentos"] + 1))
    if not a_mandar:
        return
    fallo = None                                                           # (texto, ¿permanente?)
    try:
        armado = armar_de_la_base(e["tambo"], e["informe_id"], e["formato"], reloj, ejecutar)
    except informes_motor.InformeError:
        fallo = ("El informe ya no se puede calcular: revisá su definición.", True)       # permanente: no se reintenta
    except ExcelError:
        fallo = ("No se pudo armar el Excel del informe.", True)                          # permanente: el mismo informe daría el mismo archivo roto
    except EnvioError:
        fallo = ("El informe ya no existe.", True)
    except Exception as exc:  # noqa: BLE001 -- el texto de la excepción puede traer el servidor o el usuario de la base
        log.warning("envío programado %s: no se pudo calcular el informe: %s", e["id"], type(exc).__name__)
        fallo = (("No se pudo calcular el informe: este tambo no tiene todos los datos que necesita.", True) if _es_permanente(exc)
                 else ("No se pudo calcular el informe (la base no contestó).", False))
    if fallo:
        texto, permanente = fallo
        for canal, n in a_mandar:
            if permanente:
                detalle, intentos = texto, MAX_INTENTOS
            else:
                promesa, intentos = _promesa(e, n)
                detalle = texto + promesa
            _anotar(e, slot, canal, "fallido", detalle, reloj, intentos)
            resumen["fallidos"].append((e["id"], _slot_iso(slot), canal))
        return
    for canal, n in a_mandar:
        if not _sigue_igual(e):
            # Se pausó o se cambió la programación mientras se calculaba el informe (o mientras salía el canal anterior): lo reclamado se cierra sin mandar.
            _anotar(e, slot, canal, "omitido", DETALLE_CAMBIO, reloj)
            resumen["omitidos"].append((e["id"], _slot_iso(slot), canal))
            continue
        try:
            enviar(canal, armado)
        except Exception as exc:  # noqa: BLE001
            log.warning("envío programado %s por %s: falló: %s", e["id"], canal, type(exc).__name__)
            estado, detalle, reintentable = _fallo_de_canal(canal, exc)
            if reintentable:
                promesa, intentos = _promesa(e, n)
                _anotar(e, slot, canal, estado, detalle + promesa, reloj, intentos)
            else:
                _anotar(e, slot, canal, estado, detalle, reloj, MAX_INTENTOS if estado == "fallido" else n)
            resumen["sin_confirmar" if estado == "sin_confirmar" else "fallidos"].append((e["id"], _slot_iso(slot), canal))
        else:
            _anotar(e, slot, canal, "enviado", "", reloj)
            resumen["enviados"].append((e["id"], _slot_iso(slot), canal))


# ============================================================================================ «Probar ahora»

_PROBANDO: set = set()
_LOCK = threading.Lock()


def probar(tambo: str, informe_id: int, enviar, canales, enviar_de_verdad: bool, canales_disponibles, ahora=None, ejecutar=None, formato=None) -> dict:
    """Arma el informe como se mandaría y, si se pide, lo manda de verdad. NO anota nada en el registro, no cuenta y anda aunque la programación esté
    pausada (o no exista: se usa el formato Excel). Un solo «probar» a la vez por informe.

    `formato` («excel» o «texto»): si viene, se usa en vez del guardado (la pantalla manda el que está en el formulario, todavía sin guardar). Devuelve además `textos`:
    el texto que recibe CADA canal (`correo`, `telegram`, `whatsapp`), el mismo con que se manda de verdad; `texto` queda como el del correo."""
    informe = informes_guardados.obtener(tambo, informe_id)
    if informe is None:
        raise EnvioError("Ese informe no existe.")
    if informe["archivado"]:
        raise EnvioError("Ese informe está archivado: restauralo para programar su envío.")
    if formato is not None and not (isinstance(formato, str) and formato in FORMATOS):
        raise EnvioError("El formato tiene que ser «excel» o «texto».")
    programacion = obtener(tambo, informe_id)
    if canales is None:
        canales = programacion["canales"] if programacion else []
    if not isinstance(canales, (list, tuple)) or not all(isinstance(c, str) and c in CANALES for c in canales):
        raise EnvioError("Los canales no son válidos.")
    if enviar_de_verdad and not canales:
        raise EnvioError("Elegí al menos un canal por el que mandarlo.")
    clave = (tambo, informe_id)
    with _LOCK:
        if clave in _PROBANDO:
            raise EnvioError("Este informe ya se está probando: esperá a que termine.")
        _PROBANDO.add(clave)
    try:
        armado = armar(tambo, informe, formato or (programacion["formato"] if programacion else "excel"), ahora, ejecutar)
        enviado = {}
        if enviar_de_verdad:
            disponibles = set(canales_disponibles())
            for canal in sorted(set(canales)):
                if canal not in disponibles:
                    enviado[canal] = f"error: {NOMBRE_CANAL[canal]} no está configurado o está apagado en ⚙ Configuración."
                    continue
                try:
                    enviar(canal, armado)
                    enviado[canal] = "ok"
                except Exception as exc:  # noqa: BLE001
                    log.warning("probar envío del informe %s por %s: falló: %s", informe_id, canal, type(exc).__name__)
                    enviado[canal] = "error: " + _fallo_de_canal(canal, exc)[1]
        salida = {k: armado[k] for k in ("asunto", "texto", "nombre_archivo", "animales", "truncado", "sin_verificar", "segundos")}
        salida["bytes"] = len(armado["datos"]) if armado["datos"] else 0
        salida["textos"] = {canal: texto_para(armado, canal) for canal in CANALES}
        salida["enviado"] = enviado
        return salida
    finally:
        with _LOCK:
            _PROBANDO.discard(clave)


# ============================================================================================ el hilo

def iniciar_hilo(enviar, canales_disponibles, parar: threading.Event = None, espera_s: float = None, primera_espera_s: float = None):
    """Arranca el hilo que revisa cada `TICK_S` segundos. Cada pasada va en su propio try/except: una que falla (la base no contesta) no mata el hilo.

    NO se arranca al importar `app`: lo llama `servidor.py` después de tomar el candado de instancia única, igual que el vigilante (si no,
    `precalentar.py` o las pruebas tendrían cada uno el suyo). Aun así, el reclamo en la base evita el doble envío entre procesos."""
    parar = parar or threading.Event()

    def bucle():
        parar.wait(PRIMERA_ESPERA_S if primera_espera_s is None else primera_espera_s)
        while not parar.is_set():
            try:
                pasada(enviar, canales_disponibles)
            except Exception as exc:  # noqa: BLE001
                log.warning("envío programado de informes: la pasada falló (%s); se reintenta", type(exc).__name__)
            parar.wait(TICK_S if espera_s is None else espera_s)

    hilo = threading.Thread(target=bucle, name="envios-informes", daemon=True)
    hilo.start()
    return hilo
