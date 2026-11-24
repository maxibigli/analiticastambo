# -*- coding: utf-8 -*-
"""Monitoreo IoT en tiempo real: estado de la rotativa (ORDEÑO / LAVANDO /
BARRIDO / APAGADO) y sensores de temperatura/humedad del tambo. LAVANDO y BARRIDO ya no están escritos: son dos
sensores de estado de la lista que arma el tambo (`iot_sensores`), igual que cualquier otro equipo que quiera sumar.

Fuentes de datos:
- ORDEÑO: NO es un sensor propio — se deriva de la actividad reciente en
  MilkingDeviceVisit (base DDM de DelPro). Si hubo una visita hace poco,
  la rotativa está ordeñando.
- LAVANDO / BARRIDO (y los demás estados de equipo): contactos secos del gateway PUSR M300 (DI01/DI02 de
  siempre), leídos por iot_lavado.py en iot_sensores.db (tablas eventos_di y digitales_estado).
- Sensores de temperatura/humedad: en `lecturas_sensor` de la misma base —
  según el catálogo de `iot_sensores`. El tablero los
  muestra como "a instalar" hasta que aparezcan filas reales; no hace falta
  tocar este código cuando se cableen, empiezan a mostrarse solos.

Prioridad de estados: si está lavando o barriendo, eso manda (son procesos
que no se solapan con el ordeño real); si no, se mira si hay ordeño en
curso; si no hay nada de eso, APAGADO.
"""
import datetime
import sqlite3

import db
import iot_analogicas
import iot_canales
import iot_origenes
import iot_sensores
import iot_vumetros
import voz_comandos
from iot_lavado import RUTA_DB, _conectar_db, ACTUADORES

MINUTOS_ORDENO_ACTIVO = 15  # última visita dentro de esta ventana = "ordeñando"
CACHE_ORDENO_TTL_S = 30     # el frontend refresca cada 5s; sin esto pegaría
                            # una consulta a la base DDM por refresco, de más
                            # (una sesión de ordeño dura horas, 30s de caché
                            # no le hace perder nada de "tiempo real" real).

SQL_ULTIMA_VISITA = """
    SELECT MAX(CreationTime) AS ultima FROM MilkingDeviceVisit WHERE GCRecord IS NULL
"""

_cache_ordeno: dict = {}  # tambo -> (timestamp, bool)

# Los sensores ya no están escritos acá: son el catálogo que arma el tambo (`iot_sensores`, con los 11 de siempre de lista
# inicial). "ith" es calculado (temperatura-humedad, estrés calórico), no un sensor de la lista.


def calcular_ith(temp_c: float, hum_pct: float) -> float:
    """Índice temperatura-humedad (estrés calórico bovino). Fórmula NRC
    estándar; >68 empieza el estrés leve, >72 moderado, >80 severo."""
    return round((1.8 * temp_c + 32) - ((0.55 - 0.0055 * hum_pct) * (1.8 * temp_c - 26)), 1)


def _ultimo_estado_canal(canal: str):
    """(bool estado, fecha_hora str) del último evento de ese canal, o
    (False, None) si todavía no hay ningún dato (canal sin cablear)."""
    con = _conectar_db()
    try:
        fila = con.execute(
            "SELECT estado, fecha_hora FROM eventos_di WHERE canal = ? "
            "ORDER BY fecha_hora DESC LIMIT 1", (canal,)
        ).fetchone()
        return (bool(fila[0]), fila[1]) if fila else (False, None)
    finally:
        con.close()


def _ordeno_activo(tambo: str) -> bool:
    ahora = datetime.datetime.now()
    cacheado = _cache_ordeno.get(tambo)
    if cacheado and (ahora - cacheado[0]).total_seconds() < CACHE_ORDENO_TTL_S:
        return cacheado[1]
    try:
        data = db.run_query(SQL_ULTIMA_VISITA, tambo=tambo)
        ultima = data["rows"][0][0] if data["rows"] else None
        if ultima is None:
            resultado = False
        else:
            if isinstance(ultima, str):
                ultima = datetime.datetime.fromisoformat(ultima)
            resultado = (ahora - ultima) <= datetime.timedelta(minutes=MINUTOS_ORDENO_ACTIVO)
    except Exception:  # noqa: BLE001
        resultado = cacheado[1] if cacheado else False  # ante un error puntual, no parpadear a APAGADO
    _cache_ordeno[tambo] = (ahora, resultado)
    return resultado


def estado_sistema(tambo: str, ahora: datetime.datetime = None) -> dict:
    """El estado general de la rotativa, el cartel de arriba del panel.

    Manda el PRIMER sensor de estado (`iot_sensores.estados()`, en el orden que dejó el tambo) que esté activo y hablando:
    LAVANDO y BARRIDO de siempre, o lo que el tambo haya sumado (la lavadora, un portón). Si ninguno está activo, ORDEÑO
    cuando hay visitas recientes en DelPro; si no, APAGADO. Un sensor de estado que dejó de mandar datos (`sin_datos`) no
    cuenta como activo: mejor «APAGADO» que un «LAVANDO» de hace tres horas. Antes de que `iot_lavado.py` escriba su primer
    latido (recién actualizado, todavía sin reiniciar) se usa el último cambio registrado, como se hacía siempre.

    `equipo` dice si el estado viene de un sensor (True) o es ORDEÑO/APAGADO; `estados` es cada uno con si está activo."""
    ahora = ahora or datetime.datetime.now()
    lecturas = {s["clave"]: s for s in lecturas_actuales(ahora)}
    estados, elegido = [], None
    for sensor in iot_sensores.estados():
        lectura = lecturas.get(sensor["clave"]) or {}
        activo = lectura.get("activo")
        if activo is None and not lectura.get("sin_datos") and sensor["entrada"]:
            activo = _ultimo_estado_canal(sensor["entrada"])[0]            # sin latido todavía: el último cambio
        estados.append({"clave": sensor["clave"], "estado": sensor["estado_nombre"], "activo": bool(activo),
                        "sin_datos": bool(lectura.get("sin_datos"))})
        if activo and elegido is None:
            elegido = (sensor["estado_nombre"], _desde_del_estado(sensor))
    ordenando = _ordeno_activo(tambo)
    if elegido:
        estado, desde, equipo = elegido[0], elegido[1], True
    elif ordenando:
        estado, desde, equipo = "ORDEÑO", None, False
    else:
        estado, desde, equipo = "APAGADO", None, False
    return {"estado": estado, "desde": desde, "equipo": equipo, "ordenando": ordenando, "estados": estados}


def _desde_del_estado(sensor: dict):
    """Desde cuándo está activo un estado: la hora del último CAMBIO de su entrada (los latidos de cada 30 s no sirven,
    dicen «ahora» siempre). Sin cambio registrado (el gateway recién se conectó) queda sin hora."""
    if not sensor.get("entrada"):
        return None
    con = _conectar_db()
    try:
        fila = con.execute("SELECT fecha_hora FROM eventos_di WHERE canal = ? AND estado = 1 "
                           "ORDER BY fecha_hora DESC LIMIT 1", (sensor["entrada"],)).fetchone()
    finally:
        con.close()
    return fila[0] if fila else None


# Entradas digitales y salidas del M300 (hasta 32 de cada una, ver iot_canales) con su nombre genérico. Los dos primeros
# DI ya tienen semántica propia (lavado/barrido); el resto son genéricos hasta que el tambo les ponga nombre o les ate un sensor.
ENTRADAS_PANEL = [(c, iot_canales.generico(c)) for c in iot_canales.ENTRADAS_DIGITALES]
SALIDAS_PANEL = [(c, iot_canales.generico(c)) for c in iot_canales.SALIDAS]

# La pantalla ESP32 tiene lugar para 8 entradas y 8 salidas (16 tarjetas fijas en su firmware, y un buffer para la respuesta):
# le mandamos solo las primeras 8 de cada una aunque haya hasta 32.
MAXIMO_PANTALLA_ESP32 = 8


def panel_io(maximo: int = None) -> dict:
    """Estado de las entradas (on/off) y las salidas del M300, para la
    pestaña Actuadores de la pantalla ESP32 (que pide `maximo=MAXIMO_PANTALLA_ESP32`: las primeras 8 de cada una).
    Sin `maximo` salen todas. Una entrada se ve con el nombre que le puso el tambo, o el de su sensor, o «Entrada N».

    De cada salida se informan DOS cosas distintas:
    - `ultima_activacion`: cuándo se le mandó el último PULSO (el botón de
      la pantalla, 0,5s, ver iot_lavado.DURACION_PULSO_S). Un pulso no deja
      estado: se suelta solo.
    - `sostenido_desde`: desde cuándo está prendida de forma SOSTENIDA por
      un comando de voz (None si no lo está). Esto sí es estado persistente
      -- desde los comandos de voz "Jarvis" una salida puede quedar
      encendida indefinidamente hasta que alguien pida apagarla, así que ya
      no es cierto que las salidas sean solo pulsos momentáneos, y una
      pantalla que no lo muestre deja un relé prendido sin ningún indicador.
      (El tope máximo de tiempo encendida es una pregunta para el tambo, no
      un número para inventar acá -- ver CLAUDE.md, misma regla que las
      duraciones de etapa y los umbrales de retirada.)

    El import de voz_comandos va en la dirección que ya existe: este módulo
    importa iot_lavado, que importa voz_comandos; voz_comandos no importa a
    ninguno de los dos."""
    nombres_custom = iot_canales.nombres()
    sostenidos = voz_comandos.estado()   # clave -> encendido_desde (ISO)
    de_sensor = {s["entrada"]: s["label"] for s in iot_sensores.digitales() if s["entrada"]}
    entradas = [
        {"clave": clave, "label": nombres_custom.get(clave) or de_sensor.get(clave) or label, "estado": estado,
         "desde": desde}
        for clave, label in ENTRADAS_PANEL[:maximo]
        for estado, desde in [_ultimo_estado_canal(clave)]
    ]

    con = _conectar_db()
    try:
        salidas = []
        for clave, label in SALIDAS_PANEL[:maximo]:
            fila = con.execute(
                "SELECT fecha_hora FROM comandos_actuador WHERE canal = ? AND resultado = 'ok' "
                "ORDER BY fecha_hora DESC LIMIT 1", (clave,)
            ).fetchone()
            salidas.append({"clave": clave, "label": nombres_custom.get(clave, label),
                            "ultima_activacion": fila[0] if fila else None,
                            "sostenido_desde": sostenidos.get(clave)})
    finally:
        con.close()

    return {"entradas": entradas, "salidas": salidas}


def sensores_asignables() -> dict:
    """clave -> (min, max) de los sensores que se pueden asignar a una entrada analógica. El ITH no: se calcula."""
    return {s["clave"]: (s["min"], s["max"]) for s in iot_sensores.analogicos()}


def panel_analogicas() -> dict:
    """Las entradas analógicas con su asignación y su lectura EN VIVO, para ⚙ Configuración › 🔌 Entradas/Salidas.

    De cada una: la configuración (sensor, dirección Modbus, escala), el último número crudo que leyó iot_lavado.py y,
    si está calibrada, la corriente en mA y el valor ya convertido. Con un simulador en 4, 12 y 20 mA se ven los
    crudos y se cargan como escala. `ma`/`valor` son None si falta calibrar; `valor` es None y `sin_senal` True si el
    lazo está cortado."""
    configuradas = iot_analogicas.cargar()
    con = _conectar_db()
    try:
        crudos = {c: (raw, fh) for c, raw, fh in con.execute("SELECT canal, raw, fecha_hora FROM analogicas_crudo")}
    finally:
        con.close()

    canales = []
    nombres_custom = iot_canales.nombres()
    for clave, label in iot_analogicas.CANALES:
        label = nombres_custom.get(clave) or label
        cfg = configuradas.get(clave) or {}
        raw, fecha = crudos.get(clave, (None, None))
        conv = iot_analogicas.convertir(raw, cfg) if cfg else None
        canales.append({
            "clave": clave, "label": label,
            "sensor": cfg.get("sensor"), "direccion": cfg.get("direccion"),
            "raw_4": cfg.get("raw_4"), "raw_20": cfg.get("raw_20"),
            "min": cfg.get("min"), "max": cfg.get("max"),
            "raw": raw, "fecha_hora": fecha,
            "ma": conv["ma"] if conv else None,
            "valor": conv["valor"] if conv else None,
            "sin_senal": bool(conv and conv["sin_senal"]),
        })
    sensores = [{"clave": s["clave"], "label": s["label"], "unidad": s["unidad"], "min": s["min"], "max": s["max"]}
                for s in iot_sensores.analogicos()]
    return {"canales": canales, "sensores": sensores}


def _encolar_pulso(canal: str):
    """Inserta el pedido en `comandos_actuador` y devuelve su id (None si el canal no es una salida del M300)."""
    if canal not in ACTUADORES:
        return None
    con = _conectar_db()
    try:
        cur = con.execute(
            "INSERT INTO comandos_actuador (canal, fecha_hora, ejecutado) VALUES (?, ?, 0)",
            (canal, datetime.datetime.now().isoformat(timespec="seconds"))
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def solicitar_pulso(canal: str) -> bool:
    """Encola un pulso de actuador -- lo EJECUTA iot_lavado.py en su propio
    ciclo (dueño único de la conexión Modbus al M300; si esta función abriera
    su propia conexión se arriesgaría a pisarse con el polling continuo de
    DI que ya corre ahí). True si el canal es válido y quedó encolado."""
    return _encolar_pulso(canal) is not None


class SalidaOcupada(Exception):
    """La salida está prendida por el lavado automático o por un comando de voz: un pulso la APAGARÍA al terminar."""


def motivo_salida_ocupada(canal: str):
    """Por qué NO conviene pulsar esta salida ahora (texto para la pantalla), o None si está libre. Un pulso es
    «prender, esperar, apagar»: si la salida ya estaba prendida de forma sostenida, el apagado final la cortaría."""
    if voz_comandos._en_uso_por_lavado(canal):
        return "Está en uso por la etapa del lavado automático que está corriendo."
    if canal in voz_comandos.estado():
        return "Está prendida de forma sostenida por un comando de voz."
    if canal in voz_comandos.apagados_pendientes():
        return "Tiene un apagado por voz pendiente."
    return None


def probar_salida(canal: str) -> int:
    """Pulso de PRUEBA de una salida del M300, para ⚙ Configuración › 🔌 Entradas/Salidas. Usa el mapa YA GUARDADO
    (dirección, unit ID y largo del pulso): lo que se está tipeando sin guardar no cuenta. Devuelve el id del pedido, para
    preguntar después cómo salió (`resultado_pulso`). Levanta ValueError si no es una salida y SalidaOcupada si está en uso."""
    if canal not in ACTUADORES:
        raise ValueError(f"No es una salida del equipo: {canal}")
    motivo = motivo_salida_ocupada(canal)
    if motivo:
        raise SalidaOcupada(motivo)
    return _encolar_pulso(canal)


def resultado_pulso(pedido_id: int):
    """{"ejecutado": bool, "resultado": str|None} de un pedido de pulso, o None si ese pedido no existe.
    `resultado` es «ok» o «error: …» (lo escribe iot_lavado.py cuando lo ejecuta)."""
    con = _conectar_db()
    try:
        fila = con.execute("SELECT ejecutado, resultado FROM comandos_actuador WHERE id = ?", (pedido_id,)).fetchone()
    finally:
        con.close()
    if fila is None:
        return None
    return {"ejecutado": bool(fila[0]), "resultado": fila[1]}


def cancelar_pulso(pedido_id: int) -> bool:
    """Retira un pulso que iot_lavado.py todavía no tomó (para que un pedido de prueba no se dispare minutos después, cuando
    el gateway vuelva). True si se retiró; False si ya lo había tomado (o no existe): ahí ya no hay nada que cancelar."""
    con = _conectar_db()
    try:
        cur = con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = 'cancelado' "
                          "WHERE id = ? AND ejecutado = 0", (pedido_id,))
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def ciclos_lavado(limite: int = 20) -> list:
    """Historial de ciclos de lavado/barrido de la rotativa, para la pestaña
    Lavado Automático de la pantalla ESP32. `eventos_di` guarda un evento por
    CAMBIO de estado (ver iot_lavado.registrar_si_cambio), no uno por ciclo
    -- acá se empareja cada encendido con el próximo apagado del MISMO canal.
    Si el último evento de un canal es un encendido sin apagado todavía
    (está lavando/barriendo AHORA), el ciclo queda con `fin=None`."""
    con = _conectar_db()
    try:
        filas = con.execute(
            "SELECT canal, fecha_hora, estado FROM eventos_di "
            "WHERE canal IN ('lavado_rotativa', 'barrido_rotativa') ORDER BY fecha_hora"
        ).fetchall()
    finally:
        con.close()

    ciclos = []
    abiertos = {}   # canal -> fecha_hora del encendido todavía sin apagado
    for canal, fecha_hora, estado in filas:
        if estado:
            abiertos[canal] = fecha_hora
        elif canal in abiertos:
            ciclos.append({"tipo": canal, "inicio": abiertos.pop(canal), "fin": fecha_hora})
    for canal, inicio in abiertos.items():
        ciclos.append({"tipo": canal, "inicio": inicio, "fin": None})

    ahora = datetime.datetime.now()
    for c in ciclos:
        t0 = datetime.datetime.fromisoformat(c["inicio"])
        t1 = datetime.datetime.fromisoformat(c["fin"]) if c["fin"] else ahora
        c["duracion_s"] = int((t1 - t0).total_seconds())

    ciclos.sort(key=lambda c: c["inicio"], reverse=True)
    return ciclos[:limite]


def lecturas_actuales(ahora: datetime.datetime = None) -> list:
    """Lectura de AHORA de cada sensor del catálogo (`iot_sensores`: la lista que arma el tambo) más el ITH calculado.

    Analógicos: `valor` es la última lectura de `lecturas_sensor`. Digitales: `activo` (True/False), `estado` (el texto que el
    tambo le puso a ese estado) y `zona` (verde/amarillo/rojo, o None si ese estado no tiene color); `valor` es 1.0/0.0 por
    si alguien necesita un número. `valor` None = todavía sin instalar o sin datos (`sin_datos`: lleva más de
    `VENTANA_LECTURA_RECIENTE_MIN` minutos sin mandar nada; ahí NO se devuelve la última medición, ver
    `_sin_datos_si_esta_mudo`). `con_color` dice si el sensor se puede comparar y contar por color. El ITH se calcula al
    vuelo si hay temp+hum de ambiente; si no, también queda None."""
    catalogo = iot_sensores.cargar()
    ahora = ahora or datetime.datetime.now()
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lecturas_sensor_sensor_fecha ON lecturas_sensor(sensor, fecha_hora)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS digitales_estado (
            canal TEXT PRIMARY KEY,
            fecha_hora TEXT NOT NULL,
            estado INTEGER NOT NULL
        )
    """)
    con.commit()

    ventanas = iot_origenes.ventanas()      # una sola vez: los sensores de un origen HTTP tienen la ventana de su origen
    valores = {}
    for s in catalogo:
        if s["tipo"] != "analogico":
            continue
        fila = con.execute(
            "SELECT valor, fecha_hora FROM lecturas_sensor WHERE sensor = ? "
            "ORDER BY fecha_hora DESC LIMIT 1", (s["clave"],)
        ).fetchone()
        valores[s["clave"]] = {"valor": fila[0], "fecha_hora": fila[1]} if fila else {"valor": None, "fecha_hora": None}
    latidos = {c: (bool(e), f) for c, e, f in con.execute("SELECT canal, estado, fecha_hora FROM digitales_estado")}
    con.close()

    sin_valor = {"valor": None, "fecha_hora": None}
    temp, hum = valores.get("temp_ambiente", sin_valor), valores.get("hum_ambiente", sin_valor)
    ith = calcular_ith(temp["valor"], hum["valor"]) if temp["valor"] is not None and hum["valor"] is not None else None

    configuradas = iot_vumetros.cargar()
    salida = []
    for s in catalogo:
        if s["tipo"] == "analogico":
            v = valores[s["clave"]]
            cfg = iot_vumetros.config_de(s["clave"], configuradas)
            con_limites = any(cfg[k] is not None for k in iot_vumetros.LIMITES)
            mudo = _sin_datos_si_esta_mudo({**s, "valor": v["valor"], "fecha_hora": v["fecha_hora"]}, con_limites, ahora,
                                           ventanas["sensores"].get(s["clave"]))
            salida.append(_con_vumetro(mudo, configuradas))
        else:
            salida.append(_digital_actual(s, latidos, ahora, ventanas["entradas"].get(s["entrada"])))
    # El ITH es tan reciente como el más viejo de los dos datos de los que sale, y está mudo si cualquiera de los dos lo está
    # con SU ventana (la de su origen HTTP, o la de siempre): se decide con el dato que menos margen le deja.
    fechas_ith = [temp["fecha_hora"], hum["fecha_hora"]]
    fecha_ith = None if None in fechas_ith else min(fechas_ith)
    fecha_decision, ventana_ith = fecha_ith, None
    if fecha_ith is not None:
        limitante = _dato_limitante([(temp["fecha_hora"], ventanas["sensores"].get("temp_ambiente")),
                                     (hum["fecha_hora"], ventanas["sensores"].get("hum_ambiente"))], ahora)
        if limitante:
            fecha_decision, ventana_ith = limitante
    cfg = iot_vumetros.config_de("ith", configuradas)
    ith_mudo = _sin_datos_si_esta_mudo(
        {"clave": "ith", "label": "ITH (estrés calórico)", "tipo": "analogico", "tamano": "mediano", "unidad": "",
         "min": 50, "max": 90,
         "valor": ith, "fecha_hora": fecha_decision},
        any(cfg[k] is not None for k in iot_vumetros.LIMITES), ahora, ventana_ith)
    ith_mudo["fecha_hora"] = fecha_ith
    salida.append(_con_vumetro(ith_mudo, configuradas))
    return salida


def _dato_limitante(datos: list, ahora: datetime.datetime):
    """De varios [(fecha, ventana_min o None)], (fecha, ventana_min) del que está más cerca de quedar mudo (o ya lo está):
    el que menos margen tiene entre su ventana y lo viejo que es. None si alguna fecha no se entiende."""
    mejor = None
    for fecha, ventana in datos:
        ventana = VENTANA_LECTURA_RECIENTE_MIN if ventana is None else ventana
        try:
            margen = ventana * 60 - (ahora - datetime.datetime.fromisoformat(fecha)).total_seconds()
        except (TypeError, ValueError):
            return None
        if mejor is None or margen < mejor[0]:
            mejor = (margen, fecha, ventana)
    return None if mejor is None else (mejor[1], mejor[2])


def _digital_actual(s: dict, latidos: dict, ahora: datetime.datetime, ventana_min: int = None) -> dict:
    """Un sensor digital con su estado de ahora, tomado del último latido de su entrada (`digitales_estado`, que escribe
    iot_lavado.py en cada lectura del gateway, no solo cuando cambia). Sin entrada asignada es «sin instalar»."""
    latido = latidos.get(s["entrada"]) if s["entrada"] else None
    activo, fecha = (latido if latido else (None, None))
    texto, color = iot_sensores.estado_de(s, activo) if activo is not None else (None, None)
    item = {**s, "valor": None if activo is None else (1.0 if activo else 0.0), "activo": activo, "estado": texto,
            "zona": color, "fecha_hora": fecha, "limites": {}, "con_color": iot_sensores.tiene_color(s)}
    return _sin_datos_si_esta_mudo(item, item["con_color"] and bool(s["entrada"]), ahora, ventana_min)


# Un sensor que no mandó nada en este tiempo está MUDO: no se muestra su última medición como si fuera la de ahora (sería
# un «7 °C» tranquilizador de hace tres horas), se muestra «Sin datos» y cuenta como alarma. Las analógicas se guardan cada
# 30 s (iot_lavado.INTERVALO_GUARDADO_ANALOGICAS_S) y las entradas digitales dejan un latido cada 30 s
# (iot_lavado.INTERVALO_LATIDO_DI_S), así que 30 minutos es muy holgado. Es una decisión mía, no del tambo: si molesta, se
# cambia acá.
VENTANA_LECTURA_RECIENTE_MIN = 30


def _sin_datos_si_esta_mudo(sensor: dict, con_limites: bool, ahora: datetime.datetime, ventana_min: int = None) -> dict:
    """Marca `sin_datos` en un sensor INSTALADO (alguna vez leyó, o el tambo le cargó límites/colores) cuya última lectura es
    más vieja que `VENTANA_LECTURA_RECIENTE_MIN` (o que `ventana_min`, la que el tambo fijó para el origen HTTP del que
    viene el sensor: un nodo que manda cada 60 min no puede figurar mudo a los 30). En ese caso `valor` pasa a None (nunca el último conocido, que queda en
    `ultimo_valor` solo para informar) y `sin_datos_hace_min` dice cuánto hace. Uno que nunca leyó y no tiene límites
    es «sin instalar»: no es una falla, todavía no existe."""
    hace_min = None
    try:
        if sensor["fecha_hora"]:
            hace_min = int((ahora - datetime.datetime.fromisoformat(sensor["fecha_hora"])).total_seconds() // 60)
    except ValueError:
        pass
    ventana = VENTANA_LECTURA_RECIENTE_MIN if ventana_min is None else ventana_min
    mudo = con_limites if hace_min is None else hace_min > ventana
    if not mudo:
        return {**sensor, "sin_datos": False, "ultimo_valor": None, "sin_datos_hace_min": None}
    callado = {**sensor, "valor": None, "sin_datos": True, "ultimo_valor": sensor["valor"],
               "sin_datos_hace_min": hace_min}
    if sensor["tipo"] == "digital":
        callado.update(ultimo_estado=sensor.get("estado"), activo=None, estado=None, zona=None)
    return callado


def _con_vumetro(sensor: dict, configuradas: dict) -> dict:
    """Le suma a un sensor analógico cómo se dibuja: estilo, escala propia (si el tambo cargó una, pisa la de siempre), los
    4 límites y la zona de color de su lectura de ahora (None si no hay lectura o no hay ningún límite)."""
    cfg = iot_vumetros.config_de(sensor["clave"], configuradas)
    return {
        **sensor,
        "min": sensor["min"] if cfg["min"] is None else cfg["min"],
        "max": sensor["max"] if cfg["max"] is None else cfg["max"],
        "estilo": cfg["estilo"],
        "limites": {k: cfg[k] for k in iot_vumetros.LIMITES},
        "con_color": any(cfg[k] is not None for k in iot_vumetros.LIMITES),
        "zona": iot_vumetros.zona(sensor["valor"], cfg),
    }


def historico(sensor: str, desde: str, hasta: str, max_puntos: int = 150) -> list:
    """Serie temporal de un sensor entre desde/hasta (fechas ISO, con hora),
    agrupada en baldes de tiempo para no mandar miles de puntos a una
    pantalla chica -- como mucho `max_puntos` valores, cada uno el promedio
    de su balde. [] si no hay ninguna lectura en el rango (sensor sin
    instalar todavía, o directamente sin datos en esas fechas -- son la
    MISMA situación hoy: ver la nota de arriba, nadie escribe en
    `lecturas_sensor` todavía).

    "ith" es un caso especial: se calcula por balde a partir de los
    promedios de temp_ambiente y hum_ambiente de ESE MISMO balde, no de un
    valor propio guardado (mismo criterio que `calcular_ith` en
    `lecturas_actuales`)."""
    if sensor == "ith":
        temps = {p["fecha_hora"]: p["valor"] for p in historico("temp_ambiente", desde, hasta, max_puntos)}
        hums = {p["fecha_hora"]: p["valor"] for p in historico("hum_ambiente", desde, hasta, max_puntos)}
        return [{"fecha_hora": f, "valor": calcular_ith(temps[f], hums[f])}
                for f in sorted(temps) if f in hums]

    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    filas = con.execute(
        "SELECT fecha_hora, valor FROM lecturas_sensor WHERE sensor = ? AND fecha_hora BETWEEN ? AND ? "
        "ORDER BY fecha_hora", (sensor, desde, hasta)
    ).fetchall()
    con.close()
    if not filas:
        return []

    t0 = datetime.datetime.fromisoformat(filas[0][0])
    t1 = datetime.datetime.fromisoformat(filas[-1][0])
    # Minimo 60s por balde: con pocos datos (todo el rango cabe en un
    # instante) evita una division que de un balde de 0 segundos.
    balde_s = max((t1 - t0).total_seconds() / max_puntos, 60)

    baldes: dict = {}
    for fecha_hora, valor in filas:
        t = datetime.datetime.fromisoformat(fecha_hora)
        idx = int((t - t0).total_seconds() // balde_s)
        baldes.setdefault(idx, []).append(valor)

    return [
        {"fecha_hora": (t0 + datetime.timedelta(seconds=idx * balde_s)).isoformat(timespec="minutes"),
         "valor": round(sum(vals) / len(vals), 2)}
        for idx, vals in sorted(baldes.items())
    ]


def conteo_alarmas(ahora: datetime.datetime = None) -> dict:
    """Cuántos sensores están en rojo, amarillo y verde según las zonas de sus vúmetros, y cuántos están mudos (para el
    Tablero de Diagnóstico).

    Un sensor mudo (`sin_datos`: ver `_sin_datos_si_esta_mudo`) cuenta SIEMPRE, tenga límites o no: que dejó de mandar datos
    no depende de con qué se lo compare. Los demás solo cuentan con límites cargados (sin ellos no hay con qué comparar la
    lectura): nunca se mide con la última medición de un sensor que dejó de reportar."""
    c = {"rojo": 0, "amarillo": 0, "verde": 0, "sin_datos": 0, "en_rojo": [], "en_amarillo": [], "sin_lectura": [],
         "con_limites": 0, "sin_limites": 0}
    for s in lecturas_actuales(ahora):
        if s["sin_datos"]:
            c["sin_datos"] += 1
            c["sin_lectura"].append(s["label"])
            continue
        if not s["con_color"]:
            c["sin_limites"] += 1
            continue
        c["con_limites"] += 1
        # Un digital con color en un solo estado (rojo al activarse, sin color al apagarse) queda con zona None en el otro:
        # no está en alarma, cuenta como verde. Sin esto era c[None] -> KeyError y el Tablero decía «Error ...: None».
        c[s["zona"] or "verde"] += 1
        if s["zona"] == "rojo":
            c["en_rojo"].append(s["label"])
        elif s["zona"] == "amarillo":
            c["en_amarillo"].append(s["label"])
    return c


def sensores_con_ith() -> list:
    """Claves de todo lo que tiene vúmetro: los sensores del panel y el ITH (que se calcula)."""
    return [x["clave"] for x in iot_sensores.analogicos()] + ["ith"]


def sensores_para_vumetros() -> list:
    """Cada sensor con su configuración de vúmetro, para ⚙ Configuración › 🔌 Entradas/Salidas. `escala_defecto` es
    la de siempre (la que se usa si el tambo no carga una propia)."""
    configuradas = iot_vumetros.cargar()
    base = [{"clave": s["clave"], "label": s["label"], "unidad": s["unidad"], "min": s["min"], "max": s["max"]}
            for s in iot_sensores.analogicos()]
    base.append({"clave": "ith", "label": "ITH (estrés calórico)", "unidad": "", "min": 50, "max": 90})
    salida = []
    for s in base:
        cfg = iot_vumetros.config_de(s["clave"], configuradas)
        salida.append({"clave": s["clave"], "label": s["label"], "unidad": s["unidad"],
                       "escala_defecto": {"min": s["min"], "max": s["max"]},
                       "estilo": cfg["estilo"], "min": cfg["min"], "max": cfg["max"],
                       **{k: cfg[k] for k in iot_vumetros.LIMITES}})
    return salida


# Historial de un sensor al hacer clic en su tarjeta: hasta dónde se mira y de a cuánto se agrupa. Con lecturas cada 30 s
# se guardan ~1 millón de filas por sensor y por año (decisión del tambo: se guarda todo el detalle), así que el
# agrupado lo hace SQLite y nunca se trae una fila de más: cada rango devuelve de 120 a 365 puntos.
RANGOS_HISTORIAL = {
    "dia": (datetime.timedelta(hours=24), 600),            # 144 puntos, de 10 minutos
    "semana": (datetime.timedelta(days=7), 3600),          # 168 puntos, de 1 hora
    "mes": (datetime.timedelta(days=30), 6 * 3600),        # 120 puntos, de 6 horas
    "anio": (datetime.timedelta(days=365), 86400),         # 365 puntos, de 1 día
}
_EPOCA = datetime.datetime(1970, 1, 1)


def _baldes(con, sensor: str, desde: str, hasta: str, balde_s: int) -> list:
    """[(inicio del balde ISO, promedio, mínimo, máximo)] en orden cronológico, agrupado en la base."""
    filas = con.execute(
        "SELECT CAST(strftime('%s', fecha_hora) AS INTEGER) / ? AS b, AVG(valor), MIN(valor), MAX(valor) "
        "FROM lecturas_sensor WHERE sensor = ? AND fecha_hora >= ? AND fecha_hora <= ? "
        "GROUP BY b ORDER BY b", (balde_s, sensor, desde, hasta)).fetchall()
    return [((_EPOCA + datetime.timedelta(seconds=b * balde_s)).isoformat(timespec="minutes"), p, mn, mx)
            for b, p, mn, mx in filas]


def historial_digital(sensor: dict, rango: str, ahora: datetime.datetime = None) -> dict:
    """Historial de un sensor DIGITAL (ficha del catálogo) a partir de los cambios de su entrada (`eventos_di`).

    `tramos` son los períodos de la ventana con un estado fijo ({desde, hasta, activo, segundos}), `cambios` los últimos 40
    cambios (el más nuevo primero, con el texto que el tambo le puso a ese estado) y `resumen` cuánto estuvo activo.
    Se supone que entre dos cambios el estado se mantiene: `eventos_di` guarda solo los cambios y no dice si el gateway estuvo
    apagado en el medio. Antes del primer cambio que se conozca no se sabe el estado y ese tramo no se cuenta (la ventana
    puede empezar antes de que el contacto existiera). Dos cambios seguidos al mismo estado (el gateway al reiniciar vuelve a
    anotar el estado en que está) se funden en uno."""
    if rango not in RANGOS_HISTORIAL:
        raise ValueError(f"Rango inválido: {rango!r} (opciones: {', '.join(RANGOS_HISTORIAL)}).")
    ahora = ahora or datetime.datetime.now()
    ventana, _ = RANGOS_HISTORIAL[rango]
    desde_dt = ahora - ventana
    desde, hasta = desde_dt.isoformat(timespec="seconds"), ahora.isoformat(timespec="seconds")
    entrada = sensor.get("entrada")
    previo, eventos = None, []
    if entrada:
        con = _conectar_db()
        try:
            fila = con.execute("SELECT estado FROM eventos_di WHERE canal = ? AND fecha_hora < ? "
                               "ORDER BY fecha_hora DESC, id DESC LIMIT 1", (entrada, desde)).fetchone()
            previo = bool(fila[0]) if fila else None
            eventos = [(f, bool(e)) for f, e in con.execute(
                "SELECT fecha_hora, estado FROM eventos_di WHERE canal = ? AND fecha_hora >= ? AND fecha_hora <= ? "
                "ORDER BY fecha_hora, id", (entrada, desde, hasta))]
        finally:
            con.close()

    cambios, estado = [], previo                       # solo los cambios reales: un evento al mismo estado no cambia nada
    for f, e in eventos:
        if e != estado:
            cambios.append((f, e))
            estado = e
    tramos, cursor, actual = [], (desde if previo is not None else None), previo
    for f, e in cambios + [(hasta, None)]:
        if cursor is not None and actual is not None and f > cursor:
            seg = int((datetime.datetime.fromisoformat(f) - datetime.datetime.fromisoformat(cursor)).total_seconds())
            tramos.append({"desde": cursor, "hasta": f, "activo": actual, "segundos": seg})
        cursor, actual = f, e
    activo_s = sum(t["segundos"] for t in tramos if t["activo"])
    conocido_s = sum(t["segundos"] for t in tramos)
    activaciones = [f for f, e in cambios if e]
    cambios_texto = [{"fecha_hora": f, "activo": e,
                      "estado": iot_sensores.estado_de(sensor, e)[0], "zona": iot_sensores.estado_de(sensor, e)[1]}
                     for f, e in reversed(cambios)][:40]
    return {"sensor": sensor["clave"], "tipo": "digital", "rango": rango, "desde": desde, "hasta": hasta,
            "tramos": tramos, "cambios": cambios_texto,
            "resumen": {"activo_s": activo_s, "conocido_s": conocido_s,
                        "pct_activo": round(100 * activo_s / conocido_s, 1) if conocido_s else None,
                        "activaciones": len(activaciones), "cambios": len(cambios),
                        "ultima_activacion": activaciones[-1] if activaciones else None}}


def historial(sensor: str, rango: str, ahora: datetime.datetime = None) -> dict:
    """Serie de UN sensor para el gráfico del historial: {puntos: [{t, prom, min, max}], resumen: {min, prom, max, n}}.

    `resumen` es del rango entero y sale de todas las lecturas (no de promediar promedios). El ITH no es un sensor:
    se calcula por balde con el promedio de temperatura y de humedad ambiente de ESE balde, y por eso no tiene mínimo
    ni máximo propios (min/max de cada punto son None) y su `resumen.n` cuenta baldes, no lecturas."""
    if rango not in RANGOS_HISTORIAL:
        raise ValueError(f"Rango inválido: {rango!r} (opciones: {', '.join(RANGOS_HISTORIAL)}).")
    ahora = ahora or datetime.datetime.now()
    ventana, balde_s = RANGOS_HISTORIAL[rango]
    desde = (ahora - ventana).isoformat(timespec="seconds")
    hasta = ahora.isoformat(timespec="seconds")

    con = sqlite3.connect(RUTA_DB)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS lecturas_sensor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor TEXT NOT NULL,
                fecha_hora TEXT NOT NULL,
                valor REAL NOT NULL
            )
        """)
        if sensor == "ith":
            temps = {t: p for t, p, _, _ in _baldes(con, "temp_ambiente", desde, hasta, balde_s)}
            hums = {t: p for t, p, _, _ in _baldes(con, "hum_ambiente", desde, hasta, balde_s)}
            puntos = [{"t": t, "prom": calcular_ith(temps[t], hums[t]), "min": None, "max": None}
                      for t in sorted(temps) if t in hums]
            proms = [p["prom"] for p in puntos]
            resumen = {"min": min(proms), "prom": round(sum(proms) / len(proms), 2), "max": max(proms),
                       "n": len(proms)} if proms else {"min": None, "prom": None, "max": None, "n": 0}
        else:
            puntos = [{"t": t, "prom": round(p, 2), "min": round(mn, 2), "max": round(mx, 2)}
                      for t, p, mn, mx in _baldes(con, sensor, desde, hasta, balde_s)]
            mn, prom, mx, n = con.execute(
                "SELECT MIN(valor), AVG(valor), MAX(valor), COUNT(*) FROM lecturas_sensor "
                "WHERE sensor = ? AND fecha_hora >= ? AND fecha_hora <= ?", (sensor, desde, hasta)).fetchone()
            resumen = ({"min": round(mn, 2), "prom": round(prom, 2), "max": round(mx, 2), "n": n}
                       if n else {"min": None, "prom": None, "max": None, "n": 0})
    finally:
        con.close()
    return {"sensor": sensor, "rango": rango, "desde": desde, "hasta": hasta, "balde_s": balde_s,
            "puntos": puntos, "resumen": resumen}
