# -*- coding: utf-8 -*-
"""Vigilat: el motor. Cada `TICK_S` segundos:

1. mira qué reglas TOCAN (cada N minutos, apenas termina un ordeño o a sus horarios) y las evalúa en orden de
   prioridad;
2. de cada cosa que haya para avisar arma un AVISO: lo anota en la Bitácora, deja un envío por persona y canal, suena
   la alarma y lo dice en la sala si la regla lo pide;
3. ESCALA los avisos que piden confirmación y nadie confirmó: segunda persona, tercera, dueño;
4. MANDA lo pendiente, un solo mensaje por persona y canal.

Reglas que no se negocian (cada una con su prueba):
* En el horario de silencio solo trabaja la regla 1 (la primera de la lista). Lo demás espera.
* Lo mismo no se avisa dos veces (claves de dedup), ni después de un reinicio.
* Que falle el envío a una persona no frena a las demás ni hace repetir lo que ya salió. Si no se sabe si un mensaje
  salió, no se reintenta.
* El máximo de mensajes por persona por día no frena a la regla 1, ni a los avisos que piden confirmación, ni a las
  escaladas. Lo retenido va como una línea en el próximo mensaje de alertas de las 08:00/20:00.
* Todo queda en la Bitácora. Si la Bitácora falla, el aviso sale igual.
* Una regla que falla no tumba a las otras ni al hilo.
"""
import datetime
import json
import logging
import re
import threading
from collections import OrderedDict

import veredicto_baja
import vigilante_estado
import vigilat_acciones
import vigilat_config
import vigilat_tipos
from vigilat_estado import ABIERTOS, Estado

log = logging.getLogger("vigilat")

TICK_S = 30
GRACIA = datetime.timedelta(hours=2)          # un horario que se pasó por más que esto (PC apagada) ya no corre
VIDA_ENVIO = datetime.timedelta(hours=12)     # un mensaje que no se pudo mandar en este tiempo ya no es noticia
REINTENTO_REGLA = datetime.timedelta(minutes=15)   # alguna consulta es pesada: reintentar seguido la empeora
INTENTOS_REINTENTABLE = 15
INTENTOS_RECHAZO = 3                          # el canal dijo que no (credenciales, número mal): pocas vueltas
NOMBRE_CANAL = {"whatsapp": "WhatsApp", "telegram": "Telegram", "correo": "Correo",
                vigilat_config.GENERALES: "canales generales", "-": "ninguna"}
_candado = threading.Lock()

_RESPUESTA_RE = re.compile(r"\s*(?:ok|okey|si|sí|confirmo|confirmado|visto|recibido|listo)"
                           r"(?:\s*#?\s*([0-9]{1,9}))?\s*[.!]*\s*", re.IGNORECASE)


def _iso(momento: datetime.datetime) -> str:
    return momento.isoformat(timespec="seconds")


def _de_iso(texto: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(texto)


def _cuando_fue(momento: datetime.datetime) -> str:
    return f"{veredicto_baja.fecha_larga(momento.date().isoformat())} {momento:%H:%M}"


def _bitacora():
    import bitacora
    return bitacora


# --------------------------------------------------------------------------- cuándo toca cada regla
def _fin_de_silencio(momento: datetime.datetime, general: dict) -> datetime.datetime:
    h, m = general["silencio_hasta"].split(":")
    fin = momento.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return fin if fin > momento else fin + datetime.timedelta(days=1)


def toca(regla: dict, memoria: dict, ahora: datetime.datetime, general: dict) -> str | None:
    """El «turno» que toca correr ahora (una marca de tiempo), o `None`. `memoria` es lo guardado de esta regla:
    `desde` (cuándo se la vio así por primera vez: un horario anterior no se dispara al guardar), `ultima`, `turno` y
    `reintentar`."""
    cuando = regla["cuando"]
    if memoria.get("reintentar") and ahora < _de_iso(memoria["reintentar"]):
        return None
    if cuando["modo"] in ("cada", "ordeno"):
        if ahora.weekday() not in cuando["dias"]:
            return None
        minutos = cuando["minutos"] if cuando["modo"] == "cada" else general["revisar_min"]
        ultima = memoria.get("ultima")
        if ultima and ahora - _de_iso(ultima) < datetime.timedelta(minutes=minutos):
            return None
        return _iso(ahora)
    desde = _de_iso(memoria["desde"])
    turnos = []
    for atras in (1, 0):
        dia = ahora.date() - datetime.timedelta(days=atras)
        if dia.weekday() not in cuando["dias"]:
            continue
        for hora in cuando["horas"]:
            turno = datetime.datetime.combine(dia, datetime.time.fromisoformat(hora))
            if desde <= turno <= ahora:
                turnos.append(turno)
    if not turnos:
        return None
    turno = max(turnos)
    if memoria.get("turno") and _iso(turno) <= memoria["turno"]:
        return None
    if ahora - turno <= GRACIA:
        return _iso(turno)
    # un horario que cayó en el silencio sale cuando el silencio termina
    if vigilante_estado.en_silencio(turno, general) and ahora - _fin_de_silencio(turno, general) <= GRACIA:
        return _iso(turno)
    return None


def _memoria(estado: Estado, regla: dict, ahora: datetime.datetime) -> dict:
    guardada = estado.leer_valor(f"vt:regla:{regla['id']}") or {}
    firma = json.dumps(regla["cuando"], sort_keys=True)
    if guardada.get("cuando") != firma:               # regla nueva o con otro horario: arranca de cero desde ahora
        guardada = {"cuando": firma, "desde": _iso(ahora), "resultado": guardada.get("resultado"),
                    "ultima": None, "turno": None}
        estado.guardar_valor(f"vt:regla:{regla['id']}", guardada)
    return guardada


# --------------------------------------------------------------------------- historia
def _anotar(estado: Estado, bitac, aviso: dict, texto: str, ahora: datetime.datetime) -> None:
    estado.evento(aviso["id"], texto, ahora)
    if aviso.get("bitacora_id"):
        try:
            bitac.anotar(aviso["bitacora_id"], f"{ahora:%d/%m %H:%M} {texto}")
        except Exception as exc:  # noqa: BLE001
            log.warning("vigilat: no se pudo anotar en la bitácora (%s)", exc)


# --------------------------------------------------------------------------- crear avisos y niveles
def _agregar_nivel(cfg, estado, bitac, aviso: dict, nivel: int, ahora) -> int:
    """Un envío por persona y canal del nivel `nivel` de la escalera. Devuelve cuántos quedaron para mandar."""
    exento = bool(aviso["urgente"] or aviso["requiere_conf"] or nivel > 1)
    nombres, para_mandar = [], 0
    for p in vigilat_config.resolver_destinos(cfg, aviso["escalera"][nivel - 1]):
        if p["id"] == vigilat_config.GENERALES:
            estado.agregar_envio(aviso["id"], p["id"], p["nombre"], vigilat_config.GENERALES, nivel, exento, ahora)
            nombres.append(p["nombre"])
            para_mandar += 1
        elif not p["canales"]:
            estado.agregar_envio(aviso["id"], p["id"], p["nombre"], "-", nivel, exento, ahora, estado="fallido",
                                 error="no tiene ninguna forma de contacto tildada")
            nombres.append(f"{p['nombre']} (sin forma de contacto)")
        else:
            for canal in p["canales"]:
                estado.agregar_envio(aviso["id"], p["id"], p["nombre"], canal, nivel, exento, ahora)
                para_mandar += 1
            nombres.append(f"{p['nombre']} ({', '.join(NOMBRE_CANAL[c] for c in p['canales'])})")
    quienes = "; ".join(nombres) if nombres else "nadie (no hay destinatarios activos)"
    _anotar(estado, bitac, aviso, f"Se avisa a: {quienes}.", ahora)
    if aviso["requiere_conf"] and not para_mandar:
        estado.editar_aviso(aviso["id"], vence=_iso(ahora))          # nadie a quien esperar: al nivel siguiente
    return para_mandar


def _crear_aviso(cfg, estado, bitac, tambo, regla, posicion, hallazgo, ahora) -> dict:
    mensaje = regla["acciones"]["mensaje"]
    requiere = mensaje and regla["confirmacion"]["requiere"] and hallazgo.get("confirmable", True)
    niveles = vigilat_config.escalera(cfg, regla) if mensaje else []
    if not requiere:
        niveles = [regla["destinos"]] if mensaje else []
    aviso_id = estado.crear_aviso({
        "creado": _iso(ahora), "regla_id": regla["id"], "regla_nombre": regla["nombre"], "tipo": regla["tipo"],
        "prioridad": posicion + 1, "texto": hallazgo["texto"], "audio": hallazgo["audio"],
        "urgente": int(posicion == 0), "requiere_conf": int(bool(requiere and niveles)),
        "espera_min": regla["confirmacion"]["espera_min"], "escalera": niveles, "nivel": 1, "vence": None,
        "estado": "pendiente" if requiere and niveles else "avisado",
    }, hallazgo["claves"])
    try:
        registro = bitac.registrar_aviso(
            tambo, f"Aviso #{aviso_id} · regla {posicion + 1} «{regla['nombre']}»\n{hallazgo['texto']}", ahora)
        estado.editar_aviso(aviso_id, bitacora_id=registro)
    except Exception as exc:  # noqa: BLE001 - sin bitácora el aviso sale igual; queda en la historia de Vigilat
        log.warning("vigilat: no se pudo anotar el aviso en la bitácora (%s)", exc)
        estado.evento(aviso_id, f"No se pudo anotar en la Bitácora ({type(exc).__name__}).", ahora)
    aviso = estado.aviso(aviso_id)
    if niveles:
        _agregar_nivel(cfg, estado, bitac, aviso, 1, ahora)
    elif mensaje:
        _anotar(estado, bitac, aviso, "No hay a quién avisar: ningún destinatario activo.", ahora)
    return estado.aviso(aviso_id)


# --------------------------------------------------------------------------- evaluar
def _estado_datos(cfg, estado) -> str:
    """Cómo quedó la sala según las reglas «sin datos» ACTIVAS (una apagada no puede dejar frenadas a las demás)."""
    for r in cfg["reglas"]:
        if r["tipo"] == "sin_datos" and r["activa"]:
            if (estado.leer_valor(f"vt:datos:{r['id']}") or estado.estado_datos()) == "sin_datos":
                return "sin_datos"
    return "ok"


def _horas_sin_datos(cfg) -> float:
    return next((r["parametros"]["horas"] for r in cfg["reglas"] if r["tipo"] == "sin_datos" and r["activa"]),
                vigilat_tipos.HORAS_SIN_DATOS_DEFECTO)


def _evaluar_reglas(cfg, estado, bitac, tambo, fuentes, ahora, silencio) -> tuple:
    creados, pedidos, errores = [], [], 0
    memo: dict = {}
    for posicion, regla in enumerate(cfg["reglas"]):
        if not regla["activa"]:
            continue
        memoria = _memoria(estado, regla, ahora)           # antes del silencio: un horario nuevo cuenta desde que se guardó
        if silencio and posicion > 0:
            continue
        turno = toca(regla, memoria, ahora, cfg["general"])
        if turno is None:
            continue
        propio = None
        if regla["tipo"] == "sin_datos":
            propio = estado.leer_valor(f"vt:datos:{regla['id']}") or estado.estado_datos()
        ctx = vigilat_tipos.Contexto(
            fuentes, ahora, cfg["general"], ya=estado.ya_avisado,
            datos_estado=propio if propio is not None else _estado_datos(cfg, estado),
            horas_sin_datos=_horas_sin_datos(cfg), memo=memo)
        try:
            hallazgos, nota = vigilat_tipos.evaluar(regla, ctx)
        except vigilat_tipos.SinDatos as exc:
            hallazgos, nota = [], str(exc)
        except Exception as exc:  # noqa: BLE001 - una regla rota no tumba a las otras; se reintenta en un rato
            log.warning("vigilat: la regla «%s» falló (%s)", regla["nombre"], exc)
            errores += 1
            memoria.update(resultado=f"Error: no se pudo revisar ({type(exc).__name__}). Se reintenta.",
                           reintentar=_iso(ahora + REINTENTO_REGLA))
            estado.guardar_valor(f"vt:regla:{regla['id']}", memoria)
            continue
        for h in hallazgos:
            aviso = _crear_aviso(cfg, estado, bitac, tambo, regla, posicion, h, ahora)
            creados.append(aviso)
            # «Volvieron los datos» es una buena noticia: no hace sonar la alarma.
            pedidos.append((aviso, {**regla["acciones"], "alarma": False} if h.get("cierra") else regla["acciones"]))
            if h.get("datos_estado"):
                estado.guardar_valor(f"vt:datos:{regla['id']}", h["datos_estado"])
            if h.get("cierra"):
                for viejo in estado.avisos_abiertos_de_regla(regla["id"]):
                    if viejo["id"] != aviso["id"] and estado.resolver_solo(viejo["id"]):
                        _anotar(estado, bitac, estado.aviso(viejo["id"]),
                                "Se arregló solo (volvieron los datos): se deja de escalar.", ahora)
        memoria.update(ultima=_iso(ahora), resultado=nota, reintentar=None)
        if regla["cuando"]["modo"] == "horas":
            memoria["turno"] = turno
        estado.guardar_valor(f"vt:regla:{regla['id']}", memoria)
    return creados, pedidos, errores


# --------------------------------------------------------------------------- alarma y audio
def _en_fondo(tarea) -> None:
    threading.Thread(target=tarea, name="vigilat-sala", daemon=True).start()


def _acciones_en_sala(cfg, estado, bitac, pedidos, ahora, acciones, lanzar) -> None:
    con_alarma = [a for a, acc in pedidos if acc["alarma"]]
    con_audio = [a for a, acc in pedidos if acc["audio"]]
    if not con_alarma and not con_audio:
        return

    def tarea():
        for lista, nombre, hacer in (
                (con_alarma, "Alarma", lambda: acciones.sonar_alarma(cfg["general"]["salida_alarma"])),
                (con_audio, "Audio en la sala",
                 lambda: acciones.hablar("Atención. " + " ".join(a["audio"] for a in con_audio)))):
            if not lista:
                continue
            try:
                bien, detalle = hacer()
            except Exception as exc:  # noqa: BLE001
                bien, detalle = False, f"falló ({type(exc).__name__})"
            for aviso in lista:
                _anotar(estado, bitac, aviso, f"{nombre}: {detalle}." if bien else f"{nombre}: NO se pudo — {detalle}.",
                        ahora)

    lanzar(tarea)        # el audio tarda y el pulso se espera: no frenan el envío de los mensajes


# --------------------------------------------------------------------------- escalar
def _escalar(cfg, estado, bitac, ahora, silencio) -> None:
    for aviso in estado.esperando_confirmacion():
        if not aviso["vence"] or ahora < _de_iso(aviso["vence"]) or (silencio and not aviso["urgente"]):
            continue
        # Si Vigilat estuvo apagado (o la PC) mucho más de lo que dura la escalera, no se despierta a nadie por algo
        # viejo: queda como «sin confirmar».
        vida = datetime.timedelta(minutes=aviso["espera_min"] * len(aviso["escalera"])) + VIDA_ENVIO
        if ahora - _de_iso(aviso["creado"]) > vida:
            estado.editar_aviso(aviso["id"], estado="sin_confirmar", vence=None)
            _anotar(estado, bitac, aviso, "Nadie confirmó y el aviso ya es viejo: no se sigue escalando.", ahora)
            continue
        siguiente = aviso["nivel"] + 1
        if siguiente > len(aviso["escalera"]):
            estado.editar_aviso(aviso["id"], estado="sin_confirmar", vence=None)
            _anotar(estado, bitac, aviso, "Nadie confirmó y ya se avisó a todos los niveles.", ahora)
            continue
        estado.editar_aviso(aviso["id"], nivel=siguiente, vence=None)
        _anotar(estado, bitac, aviso, f"Sin confirmación: se pasa al nivel {siguiente}.", ahora)
        _agregar_nivel(cfg, estado, bitac, estado.aviso(aviso["id"]), siguiente, ahora)


# --------------------------------------------------------------------------- mandar
def componer(cfg, estado, envios: list, canal: str, ahora) -> str:
    """El mensaje que recibe una persona por un canal, con todos sus avisos pendientes en orden de prioridad."""
    partes = [f"👁 Vigilat — {_cuando_fue(ahora)}"]
    url = cfg["general"]["direccion_publica"]
    for e in envios:
        a = estado.aviso(e["aviso_id"])
        bloque = []
        if e["nivel"] > 1:
            antes = []
            for x in estado.envios_de(a["id"]):
                if x["nivel"] < e["nivel"] and x["estado"] in ("enviado", "incierto") and x["persona"] not in antes:
                    antes.append(x["persona"])
            quienes = f" (se le avisó a {', '.join(antes)})" if antes else ""
            bloque.append(f"⏫ Nadie confirmó este aviso del {_cuando_fue(_de_iso(a['creado']))}{quienes}.")
        bloque.append(f"#{a['id']} · {a['texto']}")
        if a["requiere_conf"] and a["estado"] in ABIERTOS:
            if url:
                bloque.append(f"👉 Confirmá que lo viste: {url}/vigilat/c/{e['token']}")
            if canal == "whatsapp":
                bloque.append(f"👉 {'O respondé' if url else 'Respondé'} OK {a['id']}")
            elif not url:
                bloque.append("👉 Confirmalo en LactIA, en la Bitácora.")
        partes.append("\n".join(bloque))
    return "\n\n".join(partes)


def _arrancar_reloj(estado, envio: dict, ahora) -> None:
    """La espera de confirmación de un nivel corre desde el primer intento de mandarle algo (no desde que se creó: en el
    silencio nadie puede confirmar lo que todavía no recibió)."""
    a = estado.aviso(envio["aviso_id"])
    if a["estado"] == "pendiente" and envio["nivel"] == a["nivel"] and not a["vence"]:
        estado.editar_aviso(a["id"], vence=_iso(ahora + datetime.timedelta(minutes=a["espera_min"])))


def _sin_nadie_que_esperar(estado, aviso_id: int, ahora) -> None:
    """Si en el nivel actual no le llegó a nadie ni queda nada por intentar, se escala ya."""
    a = estado.aviso(aviso_id)
    if a["estado"] != "pendiente":
        return
    del_nivel = [e for e in estado.envios_de(aviso_id) if e["nivel"] == a["nivel"]]
    if del_nivel and all(e["estado"] in ("fallido", "vencido") for e in del_nivel):
        estado.editar_aviso(aviso_id, vence=_iso(ahora))


def _falla(estado, bitac, envio, exc, ahora) -> None:
    quien = f"{envio['persona']} por {NOMBRE_CANAL.get(envio['canal'], envio['canal'])}"
    motivo = str(exc)[:300] or type(exc).__name__
    aviso = estado.aviso(envio["aviso_id"])
    if not getattr(exc, "no_salio", True):
        estado.marcar_envio(envio["id"], "incierto", error=motivo)
        _anotar(estado, bitac, aviso, f"No se sabe si le llegó a {quien}: no se reintenta. {motivo}", ahora)
        return
    intentos = envio["intentos"] + 1
    tope = INTENTOS_REINTENTABLE if getattr(exc, "reintentable", True) else INTENTOS_RECHAZO
    if intentos >= tope:
        estado.marcar_envio(envio["id"], "fallido", error=motivo, intentos=intentos)
        _anotar(estado, bitac, aviso, f"No se pudo mandar a {quien} ({intentos} intentos): {motivo}", ahora)
    else:
        espera = datetime.timedelta(minutes=min(5 * intentos, 30))
        estado.marcar_envio(envio["id"], "pendiente", error=motivo, intentos=intentos, proximo=ahora + espera)
        if intentos == 1:
            _anotar(estado, bitac, aviso, f"Falló el envío a {quien}, se reintenta: {motivo}", ahora)


def _despachar(cfg, estado, bitac, ahora, silencio, acciones, enviar_generales) -> int:
    grupos: OrderedDict = OrderedDict()
    tocados = set()
    for e in estado.envios_pendientes(ahora):
        if ahora - _de_iso(e["creado"]) > VIDA_ENVIO:
            estado.marcar_envio(e["id"], "vencido", error="pasaron 12 horas sin poder mandarlo")
            _anotar(estado, bitac, estado.aviso(e["aviso_id"]),
                    f"Ya no se manda a {e['persona']}: pasaron 12 horas.", ahora)
            tocados.add(e["aviso_id"])
            continue
        if silencio and not e["urgente"]:
            continue
        grupos.setdefault((e["persona_id"], e["canal"]), []).append(e)

    maximo = cfg["general"]["max_por_persona_dia"]
    lleno = {pid: estado.mensajes_del_dia(pid, ahora.date()) >= maximo for pid, _canal in grupos}
    retenidos, mandados = set(), 0
    for (pid, canal), envios in grupos.items():
        if lleno[pid]:
            for e in [x for x in envios if not x["exento"]]:
                estado.marcar_envio(e["id"], "retenido", error="llegó al máximo de mensajes del día")
                aviso = estado.aviso(e["aviso_id"])
                _anotar(estado, bitac, aviso, f"Retenido para {e['persona']}: ya recibió {maximo} mensajes hoy.", ahora)
                if aviso["id"] not in retenidos:
                    retenidos.add(aviso["id"])
                    estado.retener(f"{aviso['regla_nombre']}: {aviso['texto'].splitlines()[0]}", ahora)
            envios = [x for x in envios if x["exento"]]
        if not envios:
            continue
        persona = None if pid == vigilat_config.GENERALES else vigilat_config.persona(cfg, pid)
        destino = (persona or {}).get(canal, "")
        if canal != vigilat_config.GENERALES and (not persona or not persona["activa"] or not destino):
            for e in envios:
                estado.marcar_envio(e["id"], "fallido", error="la persona ya no está o no tiene ese dato")
                _anotar(estado, bitac, estado.aviso(e["aviso_id"]),
                        f"No se pudo mandar a {e['persona']}: ya no tiene ese contacto.", ahora)
                tocados.add(e["aviso_id"])
            continue
        mensaje = componer(cfg, estado, envios, canal, ahora)
        for e in envios:
            _arrancar_reloj(estado, e, ahora)
            tocados.add(e["aviso_id"])
        try:
            if canal == vigilat_config.GENERALES:
                enviar_generales(mensaje)
            else:
                acciones.enviar(canal, destino, mensaje)
        except Exception as exc:  # noqa: BLE001 - el fallo de una persona no frena a las demás
            log.warning("vigilat: falló un envío por %s (%s)", canal, type(exc).__name__)
            for e in envios:
                _falla(estado, bitac, e, exc, ahora)
            continue
        mandados += 1
        for e in envios:
            estado.marcar_envio(e["id"], "enviado", ahora=ahora, lote=_iso(ahora))
            _anotar(estado, bitac, estado.aviso(e["aviso_id"]),
                    f"Enviado a {e['persona']} por {NOMBRE_CANAL[canal]}.", ahora)
    for aviso_id in tocados:
        _sin_nadie_que_esperar(estado, aviso_id, ahora)
    return mandados


# --------------------------------------------------------------------------- la vuelta
def tick(cfg: dict, tambo: str, estado: Estado | None = None, ahora: datetime.datetime | None = None, fuentes=None,
         enviar_generales=None, acciones=vigilat_acciones, bitac=None, lanzar=_en_fondo) -> dict:
    """Una vuelta completa. Devuelve `{"estado": "apagado"|"ok"|"silencio"|"error", "avisos", "mensajes"}`."""
    if not cfg["general"]["activo"]:
        return {"estado": "apagado", "avisos": 0, "mensajes": 0}
    ahora = (ahora or datetime.datetime.now()).replace(microsecond=0)
    estado = estado or Estado()
    fuentes = fuentes or vigilat_tipos.Fuentes(tambo)
    bitac = bitac or _bitacora()

    def sin_generales(_texto):
        raise RuntimeError("No hay ningún canal de alerta configurado y activado.")

    with _candado:
        silencio = vigilante_estado.en_silencio(ahora, cfg["general"])
        creados, pedidos, errores = _evaluar_reglas(cfg, estado, bitac, tambo, fuentes, ahora, silencio)
        _acciones_en_sala(cfg, estado, bitac, pedidos, ahora, acciones, lanzar)
        _escalar(cfg, estado, bitac, ahora, silencio)
        mandados = _despachar(cfg, estado, bitac, ahora, silencio, acciones, enviar_generales or sin_generales)
        resultado = "error" if errores else "silencio" if silencio else "ok"
        estado.set_ultima_pasada(ahora, resultado)
    return {"estado": resultado, "avisos": len(creados), "mensajes": mandados}


# --------------------------------------------------------------------------- confirmar
def confirmar(estado: Estado, aviso_id: int, quien: str, via: str, ahora: datetime.datetime | None = None,
              bitac=None) -> bool:
    ahora = (ahora or datetime.datetime.now()).replace(microsecond=0)
    if not estado.confirmar(aviso_id, quien, via, ahora):
        return False
    _anotar(estado, bitac or _bitacora(), estado.aviso(aviso_id), f"Confirmado por {quien} ({via}).", ahora)
    return True


def _mismo_numero(a: str, b: str) -> bool:
    """Compara por los últimos 10 dígitos: Argentina escribe el mismo celular con y sin el 9 después del 54."""
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return len(da) >= 8 and len(db) >= 8 and da[-10:] == db[-10:]


def confirmar_por_respuesta(cfg: dict, estado: Estado, telefono: str, texto: str,
                            ahora: datetime.datetime | None = None, bitac=None) -> str | None:
    """Alguien contestó por WhatsApp «OK 12». Devuelve qué responderle, o `None` si el mensaje no era para Vigilat
    (sigue su camino, por ejemplo a la IA)."""
    m = _RESPUESTA_RE.fullmatch(texto or "")
    if not m:
        return None
    personas = [p for p in cfg["personas"] if p["whatsapp"] and _mismo_numero(p["whatsapp"], telefono)]
    if not personas:
        return None
    persona = personas[0]
    pendientes = sorted({i for p in personas for i in estado.pendientes_de_persona(p["id"])}, reverse=True)
    if m.group(1):
        numero = int(m.group(1))
        aviso = estado.aviso(numero)
        if numero not in pendientes:
            if aviso and aviso["estado"] == "confirmado":
                return f"El aviso #{numero} ya lo había confirmado {aviso['confirmado_por']}."
            return f"No tengo ningún aviso #{numero} tuyo esperando confirmación."
    elif not pendientes:
        return None
    elif len(pendientes) > 1:
        lista = ", ".join(f"#{i}" for i in pendientes[:6])
        return f"Tenés {len(pendientes)} avisos sin confirmar ({lista}). Respondé OK y el número."
    else:
        numero = pendientes[0]
    if not confirmar(estado, numero, persona["nombre"], "respuesta por WhatsApp", ahora, bitac):
        return f"El aviso #{numero} ya no esperaba confirmación."
    return f"✅ Confirmado el aviso #{numero}. Gracias, {persona['nombre']}."


# --------------------------------------------------------------------------- probar sin mandar
def _a_quien(cfg, destinos, disponibles: dict, generales_ok: bool) -> list:
    salida = []
    for p in vigilat_config.resolver_destinos(cfg, destinos):
        problemas = []
        if p["id"] == vigilat_config.GENERALES:
            canales = ["canales generales"]
            if not generales_ok:
                problemas.append("no hay ningún canal general configurado y activado")
        else:
            canales = [NOMBRE_CANAL[c] for c in p["canales"]]
            if not canales:
                problemas.append("no tiene ninguna forma de contacto tildada")
            problemas += [f"{NOMBRE_CANAL[c]} no está configurado en este servidor"
                          for c in p["canales"] if not disponibles.get(c)]
        salida.append({"nombre": p["nombre"], "canales": canales, "problemas": problemas})
    return salida


def probar(cfg: dict, tambo: str, regla_id: str | None = None, estado: Estado | None = None,
           ahora: datetime.datetime | None = None, fuentes=None, disponibles: dict | None = None,
           generales_ok: bool = True) -> list:
    """Qué diría ahora cada regla (o una sola) y a quién se lo mandaría. No escribe ni manda NADA; corre aunque Vigilat
    esté apagado, la regla en pausa o sea horario de silencio."""
    ahora = (ahora or datetime.datetime.now()).replace(microsecond=0)
    fuentes = fuentes or vigilat_tipos.Fuentes(tambo)
    disponibles = vigilat_acciones.disponibles() if disponibles is None else disponibles
    memo: dict = {}
    salida = []
    for posicion, regla in enumerate(cfg["reglas"]):
        if regla_id and regla["id"] != regla_id:
            continue
        ctx = vigilat_tipos.Contexto(fuentes, ahora, cfg["general"], horas_sin_datos=_horas_sin_datos(cfg), memo=memo)
        try:
            hallazgos, nota = vigilat_tipos.evaluar(regla, ctx)
        except vigilat_tipos.SinDatos as exc:
            hallazgos, nota = [], str(exc)
        except Exception as exc:  # noqa: BLE001
            hallazgos, nota = [], f"No se pudo revisar: {type(exc).__name__}: {str(exc)[:200]}"
        mensaje = regla["acciones"]["mensaje"]
        niveles = vigilat_config.escalera(cfg, regla) if mensaje else []
        if not regla["confirmacion"]["requiere"]:
            niveles = [regla["destinos"]] if mensaje else []
        salida.append({
            "id": regla["id"], "numero": posicion + 1, "nombre": regla["nombre"], "activa": regla["activa"],
            "nota": nota,
            "hallazgos": [{"texto": h["texto"], "audio": h["audio"],
                           "ya_avisado": bool(estado and any(estado.ya_avisado(k) for k in h["claves"]))}
                          for h in hallazgos],
            "niveles": [_a_quien(cfg, n, disponibles, generales_ok) for n in niveles],
            "acciones": regla["acciones"],
            "alarma_sin_salida": regla["acciones"]["alarma"] and not cfg["general"]["salida_alarma"],
        })
    return salida


# --------------------------------------------------------------------------- el hilo
def iniciar_hilo(tambo: str, enviar_generales, parar: threading.Event | None = None, espera_s: float | None = None):
    """Arranca el bucle en un hilo daemon y lo devuelve. No hace nada mientras `activo` sea falso; cada vuelta va en su
    propio try/except para que un error no mate el hilo. `parar` y `espera_s` son para las pruebas."""
    parar = parar or threading.Event()

    def bucle():
        while not parar.is_set():
            try:
                cfg, problema = vigilat_config.leer()
                if problema:
                    log.warning("vigilat: %s", problema)
                if cfg["general"]["activo"]:
                    tick(cfg, tambo, enviar_generales=enviar_generales)
            except Exception:  # noqa: BLE001
                log.exception("vigilat: la vuelta falló")
            parar.wait(espera_s if espera_s is not None else TICK_S)

    hilo = threading.Thread(target=bucle, name="vigilante", daemon=True)
    hilo.start()
    return hilo
