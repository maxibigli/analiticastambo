# -*- coding: utf-8 -*-
"""Órdenes para las salidas de un equipo que MANDA sus datos por HTTP (un ESP32 con un relé): LactIA deja una orden, el equipo la
CONSULTA, la ejecuta y la CONFIRMA. El equipo no recibe conexiones; por eso cada orden tiene un ciclo de vida que termina siempre:

    pendiente --(el equipo consulta)--> entregada --(el equipo confirma)--> ejecutada | error
        |                                   |
        +--(pasó `vence`)--> vencida        +--(pasó el plazo sin confirmación)--> sin_confirmar

  * **Una orden se entrega UNA sola vez y nunca vencida.** Un equipo sin red que vuelve a los 20 minutos no dispara un pulso viejo sobre
    un equipo real. Una orden entregada cuya respuesta se perdió NO se reenvía: el error seguro es no disparar dos veces (queda
    `sin_confirmar`, y se ve así en la pantalla).
  * El vencimiento (`vencimiento_orden_s` del origen, obligatorio y sin defecto) corre desde que se crea la orden hasta que el equipo la
    levanta, y de nuevo desde que se la entregó hasta que confirma.
  * A lo sumo UNA orden viva (`pendiente` o `entregada`) por canal: apretar dos veces el botón no apila pulsos.
  * Vive en su propia tabla (`ordenes_origen`) y NO en `comandos_actuador`: `iot_lavado.ejecutar_comandos_pendientes` marca como error
    todo lo que no es del M300, y un `iot_lavado.py` viejo (sin reiniciar tras actualizar) se comería las órdenes de un ESP32.

Quién puede crear una orden lo decide `app.py` (solo el administrador con sesión): acá no hay ninguna credencial. Todo el bloque que
lee y escribe corre bajo un lock del módulo: dos consultas a la vez no entregan la misma orden.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

import iot_lavado
import iot_origenes

RUTA_DB = iot_lavado.RUTA_DB
ACCIONES = ("pulso", "prender", "apagar")
ESTADOS = ("pendiente", "entregada", "ejecutada", "error", "vencida", "sin_confirmar")
MAX_RESULTADO = 300
MAX_POR_CONSULTA = 10
_CAMPOS = ("id", "canal", "accion", "duracion_s", "estado", "creada", "vence", "entregada", "resuelta", "resultado", "usuario")

_lock = threading.Lock()


class OrdenError(ValueError):
    """Algo que el pedido no puede hacer. `codigo` es el estado HTTP que le corresponde (400, 404 o 409)."""

    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.codigo = codigo


def _conectar() -> sqlite3.Connection:
    con = iot_lavado._conectar_db(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ordenes_origen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origen TEXT NOT NULL,
            canal TEXT NOT NULL,
            accion TEXT NOT NULL,
            duracion_s REAL,
            creada TEXT NOT NULL,
            vence TEXT NOT NULL,
            estado TEXT NOT NULL,
            entregada TEXT,
            confirmar_antes TEXT,
            resuelta TEXT,
            resultado TEXT,
            usuario TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ordenes_origen_origen_estado ON ordenes_origen(origen, estado)")
    return con


def _marca(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


def _fila(r) -> dict:
    return dict(zip(_CAMPOS, r))


def _resolver(con, origen_id, ahora: datetime) -> int:
    """Pasa a su estado final lo que ya no puede avanzar. `origen_id=None` = todos los orígenes."""
    marca = _marca(ahora)
    filtro, args = ("", ()) if origen_id is None else (" AND origen = ?", (origen_id,))
    n = con.execute("UPDATE ordenes_origen SET estado = 'vencida', resuelta = ?, resultado = 'nadie la levantó a tiempo' "
                    "WHERE estado = 'pendiente' AND vence <= ?" + filtro, (marca, marca) + args).rowcount
    n += con.execute("UPDATE ordenes_origen SET estado = 'sin_confirmar', resuelta = ?, "
                     "resultado = 'se entregó y el equipo no la confirmó a tiempo' "
                     "WHERE estado = 'entregada' AND confirmar_antes <= ?" + filtro, (marca, marca) + args).rowcount
    return n


def resolver_vencidas(origen_id=None, ahora: datetime = None) -> int:
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    with _lock:
        con = _conectar()
        try:
            n = _resolver(con, origen_id, ahora)
            con.commit()
            return n
        finally:
            con.close()


def _id_valido(orden_id) -> bool:
    return 0 < orden_id < 2 ** 63                      # un entero fuera de lo que SQLite guarda no es una orden (y no es un error de la base)


def _coincide(salida, accion, duracion) -> bool:
    """¿La orden sigue siendo la que corresponde a la salida TAL COMO ESTÁ guardada ahora (mismo modo, misma duración de pulso)?"""
    if salida is None or salida.get("grupo") != "salida":
        return False
    if salida["modo"] == "pulso":
        return accion == "pulso" and duracion is not None and float(duracion) == float(salida["pulso_s"])
    return salida["modo"] == "sostenida" and accion in ("prender", "apagar")


def crear(origen: dict, canal: str, accion: str, usuario: str, ahora: datetime = None) -> dict:
    """Deja una orden `pendiente` para una salida de `origen`. Valida todo antes de escribir, contra lo guardado AHORA (la copia que trae
    el llamador puede estar vieja: otro administrador pudo sacar la salida un instante antes). Una salida sostenida acepta una orden
    DISTINTA a la viva (prender → apagar) y la reemplaza: un relé que pudo quedar prendido no se puede quedar sin poder apagarse."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    if not isinstance(origen, dict) or "id" not in origen:
        raise OrdenError("Falta el equipo al que va la orden.", 400)
    with _lock:
        fresco = iot_origenes.obtener(origen["id"])
        if fresco is None or fresco.get("tipo") != "http" or not fresco.get("puede_comandar"):
            nombre = (fresco or origen).get("nombre", "?")
            raise OrdenError(f"«{nombre}» no puede recibir órdenes: tildá «Puede recibir órdenes» en ese equipo.", 409)
        salida = next((c for c in fresco["canales"] if c["clave"] == canal), None)
        if salida is None:
            raise OrdenError(f"El equipo no tiene ninguna salida «{canal}».", 404)
        if salida["grupo"] != "salida":
            raise OrdenError(f"«{canal}» no es una salida: es una entrada.", 400)
        if accion not in ACCIONES:
            raise OrdenError(f"La acción tiene que ser «pulso», «prender» o «apagar» (llegó {accion!r}).", 400)
        if salida["modo"] == "pulso" and accion != "pulso":
            raise OrdenError(f"«{canal}» es una salida de pulso: la acción es «pulso» (no se prende ni se apaga).", 400)
        if salida["modo"] == "sostenida" and accion == "pulso":
            raise OrdenError(f"«{canal}» es una salida sostenida: la acción es «prender» o «apagar» (no hace pulsos).", 400)
        vence = ahora + timedelta(seconds=fresco["vencimiento_orden_s"])
        con = _conectar()
        try:
            _resolver(con, fresco["id"], ahora)
            viva = con.execute("SELECT id, estado, accion FROM ordenes_origen WHERE origen = ? AND canal = ? AND estado IN "
                               "('pendiente', 'entregada') LIMIT 1", (fresco["id"], canal)).fetchone()
            if viva:
                if salida["modo"] == "sostenida" and viva[2] != accion:
                    nuevo_estado = "vencida" if viva[1] == "pendiente" else "sin_confirmar"
                    con.execute("UPDATE ordenes_origen SET estado = ?, resuelta = ?, resultado = ? WHERE id = ?",
                                (nuevo_estado, _marca(ahora), f"reemplazada por «{accion}»", viva[0]))
                else:
                    raise OrdenError(f"Ya hay una orden {viva[1]} para «{canal}» (la n.º {viva[0]}): esperá a que termine.", 409)
            duracion = salida["pulso_s"] if accion == "pulso" else None
            cur = con.execute("INSERT INTO ordenes_origen (origen, canal, accion, duracion_s, creada, vence, estado, usuario) "
                              "VALUES (?, ?, ?, ?, ?, ?, 'pendiente', ?)",
                              (fresco["id"], canal, accion, duracion, _marca(ahora), _marca(vence), str(usuario or "")[:60]))
            con.commit()
            return {"id": cur.lastrowid, "canal": canal, "accion": accion, "duracion_s": duracion, "estado": "pendiente",
                    "creada": _marca(ahora), "vence": _marca(vence)}
        finally:
            con.close()


def entregar(origen_id: str, ahora: datetime = None) -> list:
    """Lo que el equipo se lleva en esta consulta: las órdenes `pendiente` no vencidas, las más viejas primero, de a lo sumo
    `MAX_POR_CONSULTA`. Pasan a `entregada` en la misma operación: no se vuelven a entregar. `canal` es la clave LOCAL (la del
    firmware). **Solo se entrega lo que sigue valiendo para la salida guardada AHORA**: si se la sacó o cambió de modo o de duración
    entre que se creó la orden y esta consulta, la orden pasa a `vencida` y no sale (no depende de que la cancelación de la app haya
    corrido)."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    with _lock:
        origen = iot_origenes.obtener(origen_id)
        if origen is None or origen["tipo"] != "http" or not origen.get("puede_comandar"):
            raise OrdenError("Este origen no recibe órdenes.", 409)
        salidas = {c["clave"]: c for c in origen["canales"] if c["grupo"] == "salida"}
        limite_confirmacion = _marca(ahora + timedelta(seconds=origen["vencimiento_orden_s"]))
        con = _conectar()
        try:
            _resolver(con, origen_id, ahora)
            entregadas = []
            for i, canal, accion, duracion in con.execute("SELECT id, canal, accion, duracion_s FROM ordenes_origen WHERE origen = ? "
                                                         "AND estado = 'pendiente' ORDER BY id", (origen_id,)).fetchall():
                if not _coincide(salidas.get(canal), accion, duracion):
                    con.execute("UPDATE ordenes_origen SET estado = 'vencida', resuelta = ?, "
                                "resultado = 'la salida ya no existe o cambió: la orden no se entrega' WHERE id = ?", (_marca(ahora), i))
                    continue
                if len(entregadas) >= MAX_POR_CONSULTA:
                    continue
                con.execute("UPDATE ordenes_origen SET estado = 'entregada', entregada = ?, confirmar_antes = ? WHERE id = ?",
                            (_marca(ahora), limite_confirmacion, i))
                entregadas.append({"id": i, "canal": canal, "accion": accion, "duracion_s": duracion})
            con.commit()
            return entregadas
        finally:
            con.close()


def confirmar(origen_id: str, orden_id, error=None, ahora: datetime = None) -> dict:
    """El equipo dice qué pasó con una orden que se le entregó. `error` (texto) = no pudo; vacío o `None` = la ejecutó. Una orden de
    otro origen contesta igual que una que no existe. Repetir la misma confirmación no cambia nada; la contraria es un conflicto.
    Una confirmación tardía de una orden `sin_confirmar` dice lo que realmente pasó."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    if isinstance(orden_id, bool) or not isinstance(orden_id, int):
        raise OrdenError("El número de orden tiene que ser un entero.", 400)
    if not _id_valido(orden_id):
        raise OrdenError("No existe esa orden.", 404)
    texto = "" if error is None or error is False else str(error).strip()[:MAX_RESULTADO]
    nuevo = "error" if texto else "ejecutada"
    with _lock:
        con = _conectar()
        try:
            _resolver(con, origen_id, ahora)
            fila = con.execute("SELECT estado FROM ordenes_origen WHERE id = ? AND origen = ?", (orden_id, origen_id)).fetchone()
            if fila is None:
                con.commit()
                raise OrdenError("No existe esa orden.", 404)
            estado = fila[0]
            if estado in ("ejecutada", "error"):
                con.commit()
                if estado == nuevo:
                    return {"id": orden_id, "estado": estado}
                raise OrdenError(f"La orden ya está {estado}: no se puede confirmar al revés.", 409)
            if estado != "entregada" and estado != "sin_confirmar":
                con.commit()
                raise OrdenError("Esa orden no se te entregó: " + ("todavía está pendiente." if estado == "pendiente"
                                                                      else "ya venció."), 409)
            con.execute("UPDATE ordenes_origen SET estado = ?, resuelta = ?, resultado = ? WHERE id = ?",
                        (nuevo, _marca(ahora), texto or "ok", orden_id))
            con.commit()
            return {"id": orden_id, "estado": nuevo}
        finally:
            con.close()


def cancelar(origen_id: str, canal: str = None, motivo: str = "", ahora: datetime = None) -> int:
    """Corta lo que está vivo (de un canal o de todo el origen): lo `pendiente` pasa a `vencida` y no se entrega nunca; lo `entregada`
    a `sin_confirmar` (ya se llevó, no se sabe qué hizo). Devuelve cuántas. Se usa al sacar una salida, apagar «puede recibir órdenes»
    o eliminar el equipo."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    filtro, args = ("", ()) if canal is None else (" AND canal = ?", (canal,))
    motivo = str(motivo)[:MAX_RESULTADO]
    with _lock:
        con = _conectar()
        try:
            n = con.execute("UPDATE ordenes_origen SET estado = 'vencida', resuelta = ?, resultado = ? "
                            "WHERE origen = ? AND estado = 'pendiente'" + filtro, (_marca(ahora), motivo, origen_id) + args).rowcount
            n += con.execute("UPDATE ordenes_origen SET estado = 'sin_confirmar', resuelta = ?, resultado = ? "
                             "WHERE origen = ? AND estado = 'entregada'" + filtro, (_marca(ahora), motivo, origen_id) + args).rowcount
            con.commit()
            return n
        finally:
            con.close()


def cancelar_orden(origen_id: str, orden_id, motivo: str = "", ahora: datetime = None) -> dict:
    """El administrador retira UNA orden viva: una `pendiente` pasa a `vencida` (no se entrega nunca); una `entregada` ya salió y no se la
    puede retirar: queda `sin_confirmar`. Una que ya terminó, o de otro origen, no. Libera el canal para una orden nueva."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    if isinstance(orden_id, bool) or not isinstance(orden_id, int) or not _id_valido(orden_id):
        raise OrdenError("No existe esa orden.", 404)
    with _lock:
        con = _conectar()
        try:
            _resolver(con, origen_id, ahora)
            fila = con.execute("SELECT estado FROM ordenes_origen WHERE id = ? AND origen = ?", (orden_id, origen_id)).fetchone()
            if fila is None:
                con.commit()
                raise OrdenError("No existe esa orden.", 404)
            if fila[0] not in ("pendiente", "entregada"):
                con.commit()
                raise OrdenError(f"Esa orden ya terminó ({fila[0]}): no hay nada que cancelar.", 409)
            nuevo = "vencida" if fila[0] == "pendiente" else "sin_confirmar"
            con.execute("UPDATE ordenes_origen SET estado = ?, resuelta = ?, resultado = ? WHERE id = ?",
                        (nuevo, _marca(ahora), str(motivo)[:MAX_RESULTADO], orden_id))
            con.commit()
            return {"id": orden_id, "estado": nuevo}
        finally:
            con.close()


def reajustar_vencimiento(origen_id: str, segundos: int) -> int:
    """Si el tambo BAJA el vencimiento de un equipo, lo que ya está esperando se acorta (una orden creada con 1 hora no puede seguir
    vigente después de bajarlo a 30 s). Subirlo no alarga nada: lo ya creado conserva el plazo con el que se creó. Devuelve cuántas cambió."""
    cambiadas = 0
    with _lock:
        con = _conectar()
        try:
            for i, creada, vence in con.execute("SELECT id, creada, vence FROM ordenes_origen WHERE origen = ? AND estado = 'pendiente'",
                                               (origen_id,)).fetchall():
                nuevo = _marca(datetime.fromisoformat(creada) + timedelta(seconds=segundos))
                if nuevo < vence:
                    con.execute("UPDATE ordenes_origen SET vence = ? WHERE id = ?", (nuevo, i))
                    cambiadas += 1
            for i, entregada, antes in con.execute("SELECT id, entregada, confirmar_antes FROM ordenes_origen WHERE origen = ? "
                                                  "AND estado = 'entregada'", (origen_id,)).fetchall():
                nuevo = _marca(datetime.fromisoformat(entregada) + timedelta(seconds=segundos))
                if nuevo < antes:
                    con.execute("UPDATE ordenes_origen SET confirmar_antes = ? WHERE id = ?", (nuevo, i))
                    cambiadas += 1
            con.commit()
        finally:
            con.close()
    return cambiadas


def listar(origen_id: str, limite: int = 20, ahora: datetime = None) -> list:
    """Las últimas órdenes del origen, las más nuevas primero. Antes de leer se resuelve lo vencido: nada queda a medias."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    with _lock:
        con = _conectar()
        try:
            _resolver(con, origen_id, ahora)
            con.commit()
            filas = con.execute("SELECT id, canal, accion, duracion_s, estado, creada, vence, entregada, resuelta, resultado, usuario "
                                "FROM ordenes_origen WHERE origen = ? ORDER BY id DESC LIMIT ?", (origen_id, int(limite))).fetchall()
        finally:
            con.close()
    return [_fila(f) for f in filas]


def olvidar(origen_id: str, ahora: datetime = None) -> None:
    """El equipo se eliminó: sus órdenes se APARTAN (el origen pasa a `<id>~eliminado-<fecha>`) y no se borran: queda el registro de qué
    relé se activó y quién lo pidió. Un equipo que se cree después con el mismo id no hereda nada."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    with _lock:
        con = _conectar()
        try:
            con.execute("UPDATE ordenes_origen SET origen = origen || ? WHERE origen = ?", (f"~eliminado-{_marca(ahora)}", origen_id))
            con.commit()
        finally:
            con.close()


def ultima_por_canal(origen_id: str, ahora: datetime = None) -> dict:
    """{clave de la salida: su orden más reciente}. Para mostrar «última orden: …» (nunca el estado real de un relé: LactIA no lo sabe)."""
    ultimas = {}
    for o in listar(origen_id, limite=200, ahora=ahora):
        ultimas.setdefault(o["canal"], o)
    return ultimas
