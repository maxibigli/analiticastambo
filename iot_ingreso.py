# -*- coding: utf-8 -*-
"""Ingreso de lecturas que llegan POR HTTP (un ESP32, un nodo LoRaWAN vía ChirpStack) al Monitoreo IoT.

`recibir` escribe en las MISMAS tablas de `iot_sensores.db` que usa `iot_lavado` para el equipo principal (`digitales_estado`,
`eventos_di`, `lecturas_sensor`), así que el panel, el historial, los colores y los avisos no saben de dónde vino la lectura.
Solo la llaman rutas que ya autenticaron el token (ver `app.py`): acá no se mira ninguna credencial.

Reglas (las fijan las pruebas):
  * Analógico: `valor = crudo × factor + offset`. Se anota siempre como «último valor» del canal; si el canal tiene un sensor,
    se guarda en `lecturas_sensor` salvo que ya haya una lectura de ese sensor a menos de `INTERVALO_GUARDADO_ANALOGICAS_S` de
    la hora de ésta (en cualquier sentido: no depende del orden en que lleguen).
  * Digital: se aplica el contacto normalmente cerrado (`invertido`) del sensor que tiene esa entrada. Se escribe el latido
    (`digitales_estado`) y, solo si el estado cambia, una fila en `eventos_di`. Una lectura con hora anterior al estado ya
    guardado se ignora: un reloj atrasado no puede dar vuelta el estado actual.
  * Un valor que no sirve (NaN, infinito, texto, un booleano en un analógico, un texto en un digital) y un canal que el origen
    no declaró se CUENTAN como ignorados: no son un error, y el resto del envío se procesa.
  * Hora: ISO 8601. Con zona se pasa a la hora local sin zona (todo el código usa hora local naíve). Inválida o en el futuro
    respecto del servidor: la del servidor (un reloj roto no puede dejar el «último estado» clavado en el futuro). En el pasado se
    respeta: un ESP32 puede reenviar lo que guardó mientras no tenía red.
"""
import math
import sqlite3
import threading
from datetime import datetime, timedelta

import iot_lavado
import iot_origenes

RUTA_DB = iot_lavado.RUTA_DB
INTERVALO_GUARDADO_ANALOGICAS_S = iot_lavado.INTERVALO_GUARDADO_ANALOGICAS_S
MAX_ERROR = 300

_lock = threading.Lock()      # todo el bloque de escritura: dos envíos a la vez con el mismo cambio dejan UN solo evento

TEXTO_SIN_OBJECT = "el nodo no manda el payload decodificado (falta el códec en ChirpStack)"


class IngresoError(ValueError):
    """El cuerpo no tiene la forma que espera el formato del origen (el texto es para mostrarle al tambo)."""


def _conectar() -> sqlite3.Connection:
    return iot_lavado._conectar_db(RUTA_DB)      # las tablas de estado de los orígenes las crea el gateway, que también las escribe


def _marca(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


def _hora(valor, ahora: datetime, creado: datetime = None) -> datetime:
    """La hora de la lectura como hora local naíve, o `ahora` si no sirve, está en el futuro o es anterior a la creación del
    origen (imposible: un ESP32 sin NTP manda la hora de arranque, 1970, y esas lecturas quedarían fuera de todo)."""
    if not isinstance(valor, str) or not valor.strip():
        return ahora
    try:
        h = datetime.fromisoformat(valor.strip())
        if h.tzinfo is not None:
            h = h.astimezone().replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return ahora
    h = h.replace(microsecond=0)
    if h > ahora or (creado is not None and h < creado):
        return ahora
    return h


def _creado_de(origen: dict):
    try:
        return datetime.fromisoformat(origen.get("creado") or "")
    except ValueError:
        return None


def _numero(valor):
    """float finito, o None si no es un número que sirva (los booleanos no cuentan como número)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    try:
        n = float(valor)
    except OverflowError:
        return None
    return n if math.isfinite(n) else None


def _activo(valor):
    """True/False de una lectura digital (bool, o un número: distinto de 0 es activo), o None si no sirve."""
    if isinstance(valor, bool):
        return valor
    n = _numero(valor)
    return None if n is None else n != 0


def _extraer(origen: dict, cuerpo, evento):
    """(lecturas, hora cruda) del cuerpo según el formato del origen; (None, None) si es un evento que no trae lecturas."""
    if not isinstance(cuerpo, dict):
        raise IngresoError("El cuerpo tiene que ser un objeto JSON.")
    if origen["formato"] == "chirpstack":
        if evento is None and "deviceInfo" in cuerpo:
            evento = "up"
        if evento is None:
            raise IngresoError("El cuerpo no parece un evento de ChirpStack (falta «deviceInfo»).")
        if evento != "up":
            return None, None
        objeto = cuerpo.get("object")
        if not isinstance(objeto, dict):
            if cuerpo.get("fPort") == 0:         # solo comandos de la red LoRaWAN (MAC): nunca trae datos del nodo
                return None, None
            raise IngresoError(TEXTO_SIN_OBJECT)
        return objeto, cuerpo.get("time")
    lecturas = cuerpo.get("lecturas")
    if not isinstance(lecturas, dict):
        raise IngresoError("«lecturas» tiene que ser un objeto {canal: valor}.")
    return lecturas, cuerpo.get("hora")


def _analogica(con, origen_id: str, canal: dict, crudo: float, hora: datetime) -> bool:
    valor = crudo * canal["factor"] + canal["offset"]
    if not math.isfinite(valor):
        return False
    valor = round(valor, 6)
    marca = _marca(hora)
    iot_lavado.anotar_ultimo(con, iot_origenes.canal_global(origen_id, canal["clave"]), marca, valor)
    if canal["sensor"]:
        margen = timedelta(seconds=INTERVALO_GUARDADO_ANALOGICAS_S)
        cerca = con.execute("SELECT 1 FROM lecturas_sensor WHERE sensor = ? AND fecha_hora > ? AND fecha_hora < ? LIMIT 1",
                            (canal["sensor"], _marca(hora - margen), _marca(hora + margen))).fetchone()
        if not cerca:
            con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                        (canal["sensor"], marca, valor))
    return True


def _digital(con, origen_id: str, canal: dict, activo: bool, hora: datetime, invertidas: dict) -> bool:
    glob = iot_origenes.canal_global(origen_id, canal["clave"])
    estado = int(activo != bool(invertidas.get(glob, False)))
    marca = _marca(hora)
    previo = con.execute("SELECT fecha_hora, estado FROM digitales_estado WHERE canal = ?", (glob,)).fetchone()
    if previo and marca < previo[0]:
        return False
    con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)", (glob, marca, estado))
    if previo is None or previo[1] != estado:
        con.execute("INSERT INTO eventos_di (canal, fecha_hora, estado) VALUES (?, ?, ?)", (glob, marca, estado))
    iot_lavado.anotar_ultimo(con, glob, marca, 1.0 if activo else 0.0)
    return True


def _sumar_estado(con, origen_id: str, ahora: datetime, recibidas: int, ignorados: int) -> None:
    con.execute("""
        INSERT INTO origenes_estado (origen, ultima_recepcion, recibidas, ignorados) VALUES (?, ?, ?, ?)
        ON CONFLICT(origen) DO UPDATE SET ultima_recepcion = excluded.ultima_recepcion,
            recibidas = recibidas + excluded.recibidas, ignorados = ignorados + excluded.ignorados
    """, (origen_id, _marca(ahora), recibidas, ignorados))


def anotar_error(origen_id: str, texto: str, ahora: datetime = None) -> None:
    """Deja `texto` como el último error del origen (se ve en la tarjeta de ⚙ Configuración). Un origen que no existe no
    guarda nada: nadie puede llenar la base de filas con ids inventados."""
    if iot_origenes.obtener(origen_id) is None:
        return
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    with _lock:
        con = _conectar()
        try:
            _sumar_estado(con, origen_id, ahora, 0, 0)
            con.execute("UPDATE origenes_estado SET ultimo_error = ?, error_fecha = ? WHERE origen = ?",
                        (str(texto)[:MAX_ERROR], _marca(ahora), origen_id))
            con.commit()
        finally:
            con.close()


def recibir(origen_id: str, cuerpo, evento: str = None, ahora: datetime = None) -> dict:
    """Procesa un envío ya autenticado. Devuelve {"recibidas": n, "ignorados": m}. Lanza `IngresoError` si el cuerpo no
    tiene la forma del formato del origen (y deja el error en su estado)."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    origen = iot_origenes.obtener(origen_id)
    if origen is None or origen["tipo"] != "http":      # un equipo Modbus no recibe datos por HTTP: LactIA lo consulta
        raise IngresoError("No existe ese origen.")
    try:
        lecturas, hora_cruda = _extraer(origen, cuerpo, evento)
    except IngresoError as e:
        anotar_error(origen_id, str(e), ahora)
        raise
    if lecturas is None:        # un evento que no trae lecturas (join, status…): no cuenta como una recepción
        return {"recibidas": 0, "ignorados": 0}
    recibidas = ignorados = 0
    with _lock:
        con = _conectar()
        try:
            if lecturas:
                hora = _hora(hora_cruda, ahora, _creado_de(origen))
                canales = {c["clave"]: c for c in origen["canales"] if c["grupo"] != "salida"}   # una salida no es una lectura
                invertidas = iot_lavado.entradas_invertidas()
                for clave, valor in lecturas.items():
                    canal = canales.get(clave)
                    if canal is None:
                        ignorados += 1
                        continue
                    if canal["grupo"] == "entrada_digital":
                        activo = _activo(valor)
                        bien = activo is not None and _digital(con, origen_id, canal, activo, hora, invertidas)
                    else:
                        crudo = _numero(valor)
                        bien = crudo is not None and _analogica(con, origen_id, canal, crudo, hora)
                    if bien:
                        recibidas += 1
                    else:
                        ignorados += 1
            _sumar_estado(con, origen_id, ahora, recibidas, ignorados)
            con.commit()
        finally:
            con.close()
    return {"recibidas": recibidas, "ignorados": ignorados}


def olvidar(origen_id: str) -> None:
    """Borra el estado DERIVADO de un origen que se eliminó (lo último que llegó, los contadores, el latido de sus entradas),
    para que otro con el mismo id no herede una «última recepción» ajena. El historial de cambios (`eventos_di`) y las
    lecturas de los sensores (`lecturas_sensor`) no se tocan."""
    desde, hasta = f"{origen_id}.", f"{origen_id}/"       # «/» es el carácter que sigue a «.»: todos los `<id>.<canal>`
    with _lock:
        con = _conectar()
        try:
            con.execute("DELETE FROM origenes_estado WHERE origen = ?", (origen_id,))
            for tabla in ("origenes_ultimo", "digitales_estado"):
                con.execute(f"DELETE FROM {tabla} WHERE canal >= ? AND canal < ?", (desde, hasta))
            con.commit()
        finally:
            con.close()


def estado(origen_id: str) -> dict:
    """Lo último que se sabe del origen: cuándo llegó algo, cuántas lecturas se aceptaron e ignoraron en total y el último error."""
    con = _conectar()
    try:
        fila = con.execute("SELECT ultima_recepcion, recibidas, ignorados, ultimo_error, error_fecha FROM origenes_estado "
                           "WHERE origen = ?", (origen_id,)).fetchone()
    finally:
        con.close()
    if fila is None:
        return {"ultima_recepcion": None, "recibidas": None, "ignorados": None, "ultimo_error": None, "error_fecha": None}
    datos = dict(zip(("ultima_recepcion", "recibidas", "ignorados", "ultimo_error", "error_fecha"), fila))
    datos["ultima_recepcion"] = datos["ultima_recepcion"] or None      # '' = un equipo Modbus que todavía no leyó nada
    return datos


def ultimo_de_canal(origen_id: str, clave: str):
    """{valor, fecha_hora} de lo último que llegó a ese canal (analógico: el valor ya convertido; digital: 1 o 0 tal cual
    llegó, antes del contacto NC), o None si nunca llegó nada."""
    con = _conectar()
    try:
        fila = con.execute("SELECT valor, fecha_hora FROM origenes_ultimo WHERE canal = ?",
                           (iot_origenes.canal_global(origen_id, clave),)).fetchone()
    finally:
        con.close()
    return None if fila is None else {"valor": fila[0], "fecha_hora": fila[1]}
