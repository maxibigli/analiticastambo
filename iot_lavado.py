# -*- coding: utf-8 -*-
"""Poller de Modbus TCP para el gateway PUSR M300: lee el estado de DI01
(lavado) y DI02 (barrido) del tablero de la rotativa y los guarda en SQLite
con marca de tiempo, cada vez que CAMBIA de estado.

Arquitectura (ver memoria delpro-iot-gateway): el M300 expone sus I/O locales
como servidor Modbus TCP (puerto 502, "Local_IO" → DI01/DI02 mapeados a las
direcciones 10001/10002 = protocolo 0-based 0/1). Esta base SQLite se cruza
después con la base DDM de DelPro a nivel aplicación (no hay JOIN SQL directo
entre motores distintos) — el estado ORDEÑO sale de la actividad reciente en
MilkingDeviceVisit, no de un sensor nuevo; ver iot_monitoreo.py en la app.

DI01 = contacto seco de lavado, DI02 = contacto seco de barrido (a cablear
cuando esté la señal armada en el tablero — mientras tanto lee 0 sin dar
error). Si algún canal queda invertido al cablear, sumalo a ESTADOS_INVERTIDOS
en vez de recablear.

Corre como proceso aparte, continuo (no es parte de la app Flask):
    python iot_lavado.py
"""
import datetime
import math
import sqlite3
import struct
import subprocess
import time

from pymodbus.client import ModbusTcpClient

import iot_analogicas
import iot_canales
import iot_conexion
import iot_mapa
import iot_origenes
import iot_sensores
import lavado_programa
import voz_comandos

INTERVALO_POLL_S = 3      # cada cuánto se pregunta el estado
# La lectura analógica llega cada ciclo (para calibrar en vivo), pero a `lecturas_sensor` se guarda una cada tanto:
# a 3 s serían ~29.000 filas por día y por entrada para un dato que cambia en minutos. El histórico igual lo
# agrupa en baldes (iot_monitoreo.historico).
INTERVALO_GUARDADO_ANALOGICAS_S = 30
INTERVALO_LATIDO_DI_S = 30    # cada cuánto se anota que una entrada digital sigue viva aunque no haya cambiado
INTERVALO_RECONEXION_S = 5
INTENTOS_APAGADO_ARRANQUE = 3   # ver apagar_actuadores_voz_al_arrancar
# Equipos Modbus adicionales (ver `ConexionesModbus`). Topes técnicos, no umbrales del tambo: un equipo apagado no puede frenar el
# ciclo del equipo principal (que se atiende primero), así que la conexión corta rápido y no se reintenta en cada vuelta de 3 s.
REINTENTO_ORIGEN_CAIDO_S = 30
TIMEOUT_ORIGEN_S = 2
PRESUPUESTO_ORIGEN_S = 2 * TIMEOUT_ORIGEN_S    # lo máximo que un equipo le puede quitar a un ciclo
MAX_ERROR_ORIGEN = 300

# Canal lógico -> (dirección Modbus 0-based, invertido?). "10001"/"10002" en
# la UI del gateway = direcciones 0/1 acá. El M300 tiene 8 DI en total; solo
# dos están cableadas a algo con nombre (lavado/barrido) -- las otras seis
# quedan con nombre genérico "di_N" hasta que el tambo defina qué miden. Ver
# iot_monitoreo.panel_io, que arma el panel de 8 tarjetas con estos canales.
# Hasta 32 entradas digitales (`iot_canales`): la n-ésima es la dirección n-1. La numeración secuencial solo se comprobó
# con DI01 y DI02; el M300 expone únicamente los puntos que se cargan en su tabla de mapeo, así que una entrada que no está
# mapeada contesta «Illegal Data Address» (se ignora). Por eso NO se consultan las 32 en cada ciclo: ver `canales_a_leer`.
CANALES = {clave: {"direccion": i, "invertido": False} for i, clave in enumerate(iot_canales.ENTRADAS_DIGITALES)}

# Salidas (DO) del M300 -- 8 disponibles, ninguna con actuador físico
# definido todavía. Se manejan como PULSADOR (se activa un ratito y se
# suelta sola), no como llave: es el criterio más seguro para un botón que
# se toca desde una pantalla sin ver el equipo, y es el mismo concepto que
# un botón de arranque de un tablero real. Dirección Modbus 0-based de la
# bobina (coil) -- espacio de direcciones DISTINTO al de las DI de arriba,
# aunque los números se repitan (0 a 7 en los dos casos).
ACTUADORES = {clave: i for i, clave in enumerate(iot_canales.SALIDAS)}   # hasta 32, la n-ésima es la bobina n-1
SALIDAS_DE_SIEMPRE = tuple(iot_canales.SALIDAS[:8])   # las 8 que existían antes de ampliar a 32: siempre en uso
# CANALES y ACTUADORES son la forma de FÁBRICA (entrada n = discrete_input n-1, salida n = bobina n-1, unit ID 1). Lo que el
# tambo cambió desde la web (función, dirección, unit ID, contacto, pulso) lo da `iot_mapa`, que se vuelve a leer en cada
# ciclo; sin archivo `iot_mapa.json` las llamadas Modbus son idénticas a las de siempre.
DURACION_PULSO_S = iot_mapa.PULSO_DEFECTO_S

RUTA_DB = "iot_sensores.db"

# Aviso por voz cuando ARRANCA el lavado (no al terminar). Usa la síntesis de
# voz de Windows (System.Speech, vía PowerShell) — no hace falta internet ni
# paquetes nuevos. Sale por la salida de audio por defecto de esta PC, así
# que tiene que estar conectada al sistema de parlantes del tambo.
AUDIO_ACTIVADO = True
MENSAJES_VOZ = {
    "lavado_rotativa": "El sistema está lavando",
    "barrido_rotativa": "El sistema está en barrido",
}
VOZ_PREFERIDA = "Microsoft Helena Desktop"  # si no está instalada, usa la voz por defecto


def _conectar_db(ruta: str = RUTA_DB) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.execute("""
        CREATE TABLE IF NOT EXISTS eventos_di (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            estado INTEGER NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_eventos_di_canal_fecha ON eventos_di(canal, fecha_hora)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS comandos_actuador (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canal TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            ejecutado INTEGER NOT NULL DEFAULT 0,
            resultado TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ciclo_lavado_estado (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            comando TEXT,
            activo INTEGER NOT NULL DEFAULT 0,
            etapa_actual INTEGER,
            etapa_inicio TEXT
        )
    """)
    # Mismo esquema que crea iot_monitoreo.lecturas_actuales: la tabla la escribe ahora este proceso (entradas
    # analógicas) y la lee la app, así que tiene que existir desde acá.
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_lecturas_sensor_sensor_fecha ON lecturas_sensor(sensor, fecha_hora)")
    # Último estado leído de cada entrada digital y CUÁNDO (latido): `eventos_di` solo guarda los cambios, y con eso un
    # contacto que no se mueve en una semana no se distingue de un gateway apagado.
    con.execute("""
        CREATE TABLE IF NOT EXISTS digitales_estado (
            canal TEXT PRIMARY KEY,
            fecha_hora TEXT NOT NULL,
            estado INTEGER NOT NULL
        )
    """)
    # Último número CRUDO de cada entrada analógica: es lo que se mira para calibrar con un simulador.
    con.execute("""
        CREATE TABLE IF NOT EXISTS analogicas_crudo (
            canal TEXT PRIMARY KEY,
            fecha_hora TEXT NOT NULL,
            raw REAL NOT NULL
        )
    """)
    # Lo que se sabe de cada origen (equipo Modbus adicional o equipo HTTP) y lo último que llegó a cada uno de sus canales:
    # lo escriben este proceso (equipos Modbus) y el ingreso HTTP de la app (`iot_ingreso`), y lo lee la pantalla de orígenes.
    con.execute("""
        CREATE TABLE IF NOT EXISTS origenes_estado (
            origen TEXT PRIMARY KEY,
            ultima_recepcion TEXT NOT NULL,
            recibidas INTEGER NOT NULL DEFAULT 0,
            ignorados INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT,
            error_fecha TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS origenes_ultimo (
            canal TEXT PRIMARY KEY,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    con.commit()
    return con


def anotar_ultimo(con: sqlite3.Connection, canal: str, marca: str, valor: float) -> None:
    """Lo último que llegó a un canal de un origen. Nunca retrocede: una lectura vieja que llega tarde no pisa a una más nueva."""
    con.execute("""
        INSERT INTO origenes_ultimo (canal, fecha_hora, valor) VALUES (?, ?, ?)
        ON CONFLICT(canal) DO UPDATE SET fecha_hora = excluded.fecha_hora, valor = excluded.valor
        WHERE excluded.fecha_hora >= origenes_ultimo.fecha_hora
    """, (canal, marca, valor))


def _leer_estado(client: ModbusTcpClient, direccion: int, invertido: bool,
                 funcion: str = "discrete_input", unit_id: int = 1):
    """True/False = estado leído; None = error de lectura (no se toca el estado anterior).
    `funcion`: discrete_input y coil devuelven un bit; input_register y holding_register un registro (distinto de 0 = activo).
    Un error, una respuesta sin datos o una excepción dan None: nunca se toman como «apagado»."""
    try:
        if funcion == "coil":
            resultado = client.read_coils(address=direccion, count=1, device_id=unit_id)
        elif funcion == "input_register":
            resultado = client.read_input_registers(address=direccion, count=1, device_id=unit_id)
        elif funcion == "holding_register":
            resultado = client.read_holding_registers(address=direccion, count=1, device_id=unit_id)
        else:
            resultado = client.read_discrete_inputs(address=direccion, count=1, device_id=unit_id)
        if resultado.isError():
            return None
        datos = resultado.registers if funcion in ("input_register", "holding_register") else resultado.bits
        if not datos:
            return None
        valor = bool(datos[0])
        return (not valor) if invertido else valor
    except Exception:  # noqa: BLE001
        client.close()   # ver el comentario igual en _escribir_reles: fuerza reconexion
        return None


def latir(con: sqlite3.Connection, canal: str, estado: bool, ultimo: dict, ahora: datetime.datetime = None) -> None:
    """Anota en `digitales_estado` que la entrada `canal` se leyó bien y estaba en `estado`: siempre que cambia y, si no,
    una vez cada INTERVALO_LATIDO_DI_S (no en cada ciclo de 3 s: serían ocho escrituras por segundo para nada).
    `ultimo` (canal -> (estado, datetime)) es del llamador, para que sobreviva entre ciclos."""
    ahora = ahora or datetime.datetime.now()
    previo = ultimo.get(canal)
    if previo and previo[0] == estado and (ahora - previo[1]).total_seconds() < INTERVALO_LATIDO_DI_S:
        return
    con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                (canal, ahora.isoformat(timespec="seconds"), int(estado)))
    con.commit()
    ultimo[canal] = (estado, ahora)


def canales_a_leer() -> list:
    """Las entradas digitales que hay que consultar en este ciclo: lavado y barrido de siempre, las que tienen un sensor
    asignado y las que el tambo nombró. Las otras no se consultan: no hay nada que mostrar ahí, y en un M300 sin esos puntos
    mapeados cada consulta sería un error."""
    usados = {"lavado_rotativa", "barrido_rotativa"}
    usados |= {d["entrada"] for d in iot_sensores.digitales() if d["entrada"]}
    usados |= set(iot_canales.nombres()) & set(CANALES)
    return [c for c in CANALES if c in usados]


def actuadores_en_uso() -> set:
    """Las salidas que este proceso maneja de verdad: las 8 de siempre, las que tienen nombre, las que usa una etapa del
    lavado automático y las que están sostenidas (o con un apagado pendiente) por voz. Las demás de las 32 no se tocan: no
    se las apaga al arrancar ni se las recorre en cada ciclo, porque en un M300 donde no existen darían error."""
    nombradas = set(iot_canales.nombres()) & set(ACTUADORES)
    de_lavado = {r for etapa in lavado_programa.etapas() for r in etapa.get("reles", [])} & set(ACTUADORES)
    de_voz = (set(voz_comandos.estado()) | set(voz_comandos.apagados_pendientes())) & set(ACTUADORES)
    return set(SALIDAS_DE_SIEMPRE) | nombradas | de_lavado | de_voz


def entradas_invertidas() -> dict:
    """{entrada: contacto normalmente cerrado?} de los sensores digitales del catálogo que tienen una entrada asignada. Se
    vuelve a leer en cada ciclo: lo que se guarda en la pantalla se toma sin reiniciar este proceso."""
    return {d["entrada"]: bool(d["invertido"]) for d in iot_sensores.digitales() if d["entrada"]}


def _decimal_32_bits(resultado):
    """El número de una entrada analógica del M300, o None si la respuesta no sirve.

    Los puntos AI del M300 son «32 Bit Float (AB CD)» (se ve en su lista de Data Point): un decimal IEEE-754 en DOS
    registros consecutivos, el alto primero. Una respuesta de error, una corta, o un valor que no es número (NaN,
    infinito) se descarta: no hay lectura, y no se guarda nada."""
    if resultado.isError() or len(resultado.registers) < 2:
        return None
    alto, bajo = resultado.registers[:2]
    valor = struct.unpack(">f", struct.pack(">HH", alto, bajo))[0]
    if valor != valor or valor in (float("inf"), float("-inf")):
        return None
    return round(valor, 4)


def procesar_analogicas(con: sqlite3.Connection, client: ModbusTcpClient, ultimo_guardado: dict,
                        ahora: datetime.datetime = None) -> None:
    """Lee los registros de entrada de las entradas analógicas asignadas (ver iot_analogicas) y:
      * guarda SIEMPRE el número crudo (para calibrar en vivo con un simulador);
      * si la entrada está calibrada y asignada a un sensor, guarda el valor convertido en `lecturas_sensor` como
        mucho una vez cada INTERVALO_GUARDADO_ANALOGICAS_S. Un lazo cortado (~0 mA) NO se guarda.

    La configuración se vuelve a leer en cada ciclo: lo que se guarda en la pantalla se toma sin reiniciar este
    proceso. `ultimo_guardado` (canal -> datetime) es del llamador, para que sobreviva entre ciclos."""
    ahora = ahora or datetime.datetime.now()
    marca = ahora.isoformat(timespec="seconds")
    for canal, cfg in iot_analogicas.cargar().items():
        if cfg.get("direccion") is None:
            continue
        try:
            resultado = client.read_input_registers(address=cfg["direccion"], count=2, device_id=1)
        except Exception:  # noqa: BLE001
            client.close()   # fuerza la reconexión, igual que en _leer_estado; el resto del ciclo se reintenta
            break
        raw = _decimal_32_bits(resultado)
        if raw is None:
            continue
        con.execute("INSERT OR REPLACE INTO analogicas_crudo (canal, fecha_hora, raw) VALUES (?, ?, ?)",
                    (canal, marca, raw))
        conv = iot_analogicas.convertir(raw, cfg)
        if not conv or conv["sin_senal"] or not cfg.get("sensor"):
            continue
        anterior = ultimo_guardado.get(canal)
        if anterior is None or (ahora - anterior).total_seconds() >= INTERVALO_GUARDADO_ANALOGICAS_S:
            con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)",
                        (cfg["sensor"], marca, conv["valor"]))
            ultimo_guardado[canal] = ahora
    con.commit()


def registrar_si_cambio(con: sqlite3.Connection, canal: str, estado: bool, estado_anterior) -> bool:
    """Inserta una fila si `estado` difiere de `estado_anterior` (o es la primera
    lectura). Devuelve el estado que quedó registrado como "anterior"."""
    if estado == estado_anterior:
        return estado_anterior
    ahora = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO eventos_di (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                (canal, ahora, int(estado)))
    con.commit()
    print(f"{ahora}  {canal} -> {'ACTIVO' if estado else 'inactivo'}")
    return estado


def procesar_entradas(con: sqlite3.Connection, client: ModbusTcpClient, anteriores: dict, ultimo_latido: dict) -> None:
    """Lee las entradas digitales en uso con la función, dirección y unit ID del mapa de `iot_mapa` (que se vuelve a leer en
    cada ciclo) y deja el latido, los cambios y el aviso por voz. `anteriores` y `ultimo_latido` son del llamador (viven entre
    ciclos)."""
    mapa = iot_mapa.entradas()
    invertidas = entradas_invertidas()
    for canal in canales_a_leer():
        cfg = mapa[canal]
        # El contacto normalmente cerrado de un sensor digital se suma al propio de la entrada (si ambos, se anulan).
        estado = _leer_estado(client, cfg["direccion"], cfg["invertido"] != invertidas.get(canal, False),
                              cfg["funcion"], cfg["unit_id"])
        if estado is None:
            continue  # error de lectura puntual: se reintenta el próximo ciclo
        latir(con, canal, estado, ultimo_latido)
        anterior = anteriores.get(canal)
        arranco = estado and estado != anterior
        anteriores[canal] = registrar_si_cambio(con, canal, estado, anterior)
        if arranco and AUDIO_ACTIVADO and canal in MENSAJES_VOZ:
            _anunciar_voz(MENSAJES_VOZ[canal])


def ejecutar_comandos_pendientes(con: sqlite3.Connection, client: ModbusTcpClient):
    """Busca pulsos de actuador pedidos desde la pantalla (tabla
    comandos_actuador, cargada por Flask) y los ejecuta ACA -- este proceso
    es el único dueño de la conexión Modbus al M300, para no abrir una
    segunda conexión TCP en paralelo desde app.py y pisarse con esta."""
    pendientes = con.execute(
        "SELECT id, canal FROM comandos_actuador WHERE ejecutado = 0 ORDER BY id"
    ).fetchall()
    for cmd_id, canal in pendientes:
        if canal not in ACTUADORES:
            resultado = f"error: canal desconocido ({canal})"
        else:
            cfg = iot_mapa.salida(canal)   # bobina, unit ID y largo del pulso (de fábrica o lo que cargó el tambo)
            direccion = cfg["direccion"]
            try:
                if not client.connected:
                    client.connect()
                # Se miran las DOS respuestas por el mismo motivo que en
                # _escribir_reles: el M300 puede rechazar la escritura
                # (ExceptionResponse) sin que se caiga el socket, y un pulso
                # que nunca ocurrió quedaba guardado como "ok" y se mostraba
                # en la pantalla como última activación.
                r_on = client.write_coil(address=direccion, value=True, device_id=cfg["unit_id"])
                time.sleep(cfg["pulso_s"])
                r_off = client.write_coil(address=direccion, value=False, device_id=cfg["unit_id"])
                fallo_on = r_on is None or r_on.isError()
                fallo_off = r_off is None or r_off.isError()
                if fallo_off:
                    resultado = f"error: no se confirmó el fin del pulso ({r_off})"
                    _alarma(f"PULSO en {canal}: el M300 NO confirmó el apagado del FINAL del pulso.",
                            "Ese relé puede haber quedado ENERGIZADO. Revisarlo físicamente.")
                elif fallo_on:
                    resultado = f"error: el M300 rechazó el pulso ({r_on})"
                    print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                          f"pulso en {canal} RECHAZADO por el M300: {r_on}")
                else:
                    resultado = "ok"
                    print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                          f"pulso en {canal} (dirección {direccion})")
            except Exception as e:  # noqa: BLE001
                resultado = f"error: {e}"
                # La conexion puede quedar "viva" del lado de Python (client.connected
                # sigue en True) aunque el M300 ya la haya cerrado del otro lado (pasa,
                # por ejemplo, cuando se reinicia su servicio Modbus TCP al cambiar la
                # config) -- forzar el cierre ahora hace que el proximo intento la abra
                # de nuevo en vez de seguir usando un socket muerto para siempre.
                client.close()
        con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = ? WHERE id = ?",
                    (resultado, cmd_id))
        con.commit()


def _alarma(*lineas: str) -> None:
    """Aviso de MÁXIMA prominencia en el log de este proceso, para lo único
    que de verdad importa acá: que no se pueda garantizar el estado físico
    de un relé. Va con banda de exclamaciones porque este log se lee a ojo
    en una consola donde el resto de las líneas son ruido de sondeo."""
    borde = "!" * 72
    print("\n" + borde)
    for linea in lineas:
        print("!! " + linea)
    print(borde + "\n", flush=True)


# clave -> (unit ID, bobina) donde se la prendió por última vez y todavía no se confirmó apagada. Vive solo en este proceso:
# si se reinicia con una salida sostenida y el mapa cambió, el apagado de arranque usa el mapa nuevo (límite conocido).
_encendidas = {}


def _escribir_reles(client: ModbusTcpClient, claves, prender: bool) -> set:
    """Escribe `prender` en cada relé de `claves`. Devuelve el SET de claves
    que NO se pudieron CONFIRMAR -- vacío = las escribió todas y el M300 las
    aceptó.

    Mirar la respuesta y no solo "que no salte una excepción" es el punto
    de esta función: el M300 contesta `ExceptionResponse(exception_code=2,
    "Illegal Data Address")` —exactamente lo que devolvían TODAS las salidas
    antes de cargar la tabla de mapeo de nodos, ver CLAUDE.md— **sin** que
    se caiga el socket. Con `client.connected` como única señal de éxito,
    un apagado que no ocurrió pasaba por hecho."""
    fallidas = set()
    for clave in claves:
        if clave not in ACTUADORES:
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"_escribir_reles: canal desconocido ({clave})")
            fallidas.add(clave)
            continue
        cfg = iot_mapa.salida(clave)
        destinos = [(cfg["unit_id"], cfg["direccion"])]
        if not prender:
            # Un relé sostenido se prende y se apaga en ciclos distintos. Si en el medio el tambo cambió la bobina de esta
            # salida en el mapa, el apagado tiene que llegar TAMBIÉN a la que se prendió (si no, queda energizada).
            previa = _encendidas.get(clave)
            if previa and previa not in destinos:
                destinos.append(previa)
        todo_ok = True
        for unit_id, direccion in destinos:
            try:
                if not client.connected:
                    client.connect()
                resultado = client.write_coil(address=direccion, value=prender, device_id=unit_id)
                if resultado is None or resultado.isError():
                    print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                          f"el M300 RECHAZÓ {'prender' if prender else 'apagar'} {clave} "
                          f"(dirección {direccion}): {resultado}")
                    todo_ok = False
            except Exception as e:  # noqa: BLE001
                print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                      f"error {'prendiendo' if prender else 'apagando'} {clave}: {e}")
                todo_ok = False
                client.close()   # mismo motivo que en ejecutar_comandos_pendientes: forzar reconexion
        if not todo_ok:
            fallidas.add(clave)
        elif prender:
            _encendidas[clave] = destinos[0]
        else:
            _encendidas.pop(clave, None)
    return fallidas


def procesar_ciclo_lavado(con: sqlite3.Connection, client: ModbusTcpClient) -> set:
    """Motor del programa de lavado automático (ver lavado_programa.py):
    prende/apaga relés según la etapa configurada y el tiempo transcurrido.
    Corre en el MISMO ciclo de sondeo de 3s que el resto de este archivo, no
    en un hilo aparte -- las etapas avanzan con precisión de unos segundos,
    que alcanza de sobra para un ciclo que dura minutos, y evita coordinar
    dos cosas escribiendo Modbus al mismo tiempo.

    Devuelve el SET de relés que el ciclo tiene TOMADOS al salir de esta
    vuelta (los de la etapa activa; vacío si no hay ciclo corriendo). Lo usa
    procesar_comandos_voz, que se llama justo después, para no pisar una
    etapa en curso con un comando de voz -- se devuelve en vez de que esa
    función vuelva a leer la fila de estado y lavado_programa.etapas() una
    segunda vez con datos de un instante distinto."""
    fila = con.execute(
        "SELECT comando, activo, etapa_actual, etapa_inicio FROM ciclo_lavado_estado WHERE id = 1"
    ).fetchone()
    comando, activo, etapa_actual, etapa_inicio = fila if fila else (None, 0, None, None)
    programa = lavado_programa.etapas()
    ahora = datetime.datetime.now()

    if comando == "cancelar":
        if activo and etapa_actual is not None and etapa_actual < len(programa):
            fallidas = _escribir_reles(client, programa[etapa_actual]["reles"], False)
            if fallidas:
                # El ciclo se da por cancelado igual (frenar nunca se traba,
                # ver CLAUDE.md), pero que el apagado no se haya confirmado
                # significa bombas posiblemente prendidas sin nadie
                # mirándolas: eso no puede quedar en una línea más del log.
                _alarma(f"LAVADO CANCELADO pero el M300 NO confirmó el apagado de: "
                        f"{', '.join(sorted(fallidas))}.",
                        "Revisar FÍSICAMENTE esos relés antes de irse.")
            for clave in programa[etapa_actual]["reles"]:
                voz_comandos.limpiar_estado(clave)
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 0, "
                    "etapa_actual = NULL, etapa_inicio = NULL WHERE id = 1")
        con.commit()
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: cancelado")
        return set()

    if comando == "iniciar" and not activo and programa:
        fallidas = _escribir_reles(client, programa[0]["reles"], True)
        if fallidas:
            _alarma(f"LAVADO ARRANCADO pero el M300 NO confirmó el encendido de: "
                    f"{', '.join(sorted(fallidas))}.",
                    "La etapa 1 puede estar corriendo con esas bombas apagadas.")
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 1, "
                    "etapa_actual = 0, etapa_inicio = ? WHERE id = 1",
                    (ahora.isoformat(timespec="seconds"),))
        con.commit()
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: arranca etapa 1/{len(programa)}")
        return set(programa[0]["reles"])

    if comando:   # 'iniciar' pedido sin programa, o repetido con uno ya activo: se descarta
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL WHERE id = 1")
        con.commit()

    if not (activo and etapa_actual is not None and etapa_inicio and programa):
        return set()
    if etapa_actual >= len(programa):
        # config cambio mientras corria (menos etapas ahora); se corta solo
        # en el proximo cancelar. No hay etapa activa, así que tampoco hay
        # relés tomados: un apagado por voz acá NO se descarta (es
        # justamente el caso en que hace falta poder apagar a mano).
        return set()

    transcurrido = (ahora - datetime.datetime.fromisoformat(etapa_inicio)).total_seconds()
    etapa_cfg = programa[etapa_actual]
    if transcurrido < etapa_cfg["duracion_s"]:
        return set(etapa_cfg["reles"])

    fallidas = _escribir_reles(client, etapa_cfg["reles"], False)
    if fallidas:
        _alarma(f"Fin de la etapa {etapa_actual + 1}: el M300 NO confirmó el apagado de: "
                f"{', '.join(sorted(fallidas))}.",
                "Revisar FÍSICAMENTE esos relés.")
    for clave in etapa_cfg["reles"]:
        voz_comandos.limpiar_estado(clave)
    siguiente = etapa_actual + 1
    if siguiente < len(programa):
        fallidas = _escribir_reles(client, programa[siguiente]["reles"], True)
        if fallidas:
            _alarma(f"Etapa {siguiente + 1}: el M300 NO confirmó el encendido de: "
                    f"{', '.join(sorted(fallidas))}.",
                    "Esa etapa puede estar corriendo con esas bombas apagadas.")
        con.execute("UPDATE ciclo_lavado_estado SET etapa_actual = ?, etapa_inicio = ? WHERE id = 1",
                    (siguiente, ahora.isoformat(timespec="seconds")))
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: etapa {siguiente + 1}/{len(programa)}")
        con.commit()
        return set(programa[siguiente]["reles"])

    con.execute("UPDATE ciclo_lavado_estado SET activo = 0, etapa_actual = NULL, "
                "etapa_inicio = NULL WHERE id = 1")
    print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: ciclo completo")
    con.commit()
    return set()


def procesar_comandos_voz(con: sqlite3.Connection, client: ModbusTcpClient,
                          anteriores_voz: dict, reles_lavado=frozenset()) -> dict:
    """Aplica por Modbus los cambios en voz_comandos.estado() (actuadores
    sostenidos por voz) desde la última vuelta -- solo escribe cuando algo
    CAMBIÓ, mismo criterio que registrar_si_cambio/procesar_ciclo_lavado.
    anteriores_voz: clave -> bool aplicado la vuelta pasada; devuelve el
    dict actualizado para pasarlo de nuevo en la próxima vuelta.

    `reles_lavado` son los relés que el Lavado Automático tiene tomados en
    su etapa ACTUAL (lo devuelve procesar_ciclo_lavado, llamado una línea
    antes en el mismo ciclo). Un comando de voz sobre uno de ellos se
    DESCARTA y queda registrado en el log, como manda el spec ("antes de
    prender/apagar un relé pedido por voz, chequea si ese relé pertenece a
    la etapa ACTUAL... si es así, lo descarta"). El chequeo equivalente del
    lado de Flask (voz_comandos.solicitar_*) NO alcanza: mira el estado de
    un instante anterior y no ve la etapa que arranca en esta misma vuelta,
    así que un "apagar bomba" aceptado durante la etapa 1 llegaba acá justo
    cuando la etapa 2 acababa de prender ese mismo relé, y lo apagaba.

    Los APAGADOS EXPLÍCITOS (voz_comandos.apagados_pendientes) se escriben
    aunque no haya transición: si el relé quedó prendido por algo que esta
    capa no causó -- el apagado de arranque que no se pudo confirmar, la web
    del propio M300, un ciclo trabado con la etapa fuera de rango -- el
    "Apagando X" que ya dijo la pantalla tiene que corresponderse con una
    escritura real."""
    deseado = voz_comandos.estado()  # clave -> encendido_desde, solo las que están ON
    apagados_pendientes = voz_comandos.apagados_pendientes()
    nuevo = {}
    for clave in sorted(actuadores_en_uso()):
        on = clave in deseado
        apagado_explicito = clave in apagados_pendientes
        anterior = anteriores_voz.get(clave, False)

        if clave in reles_lavado:
            if apagado_explicito or on != anterior:
                print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                      f"voz: {clave} DESCARTADO (lo está usando la etapa activa "
                      f"del lavado automático)")
            if apagado_explicito:
                voz_comandos.consumir_apagado_pendiente(clave)
            nuevo[clave] = on
            continue

        if on == anterior and not apagado_explicito:
            nuevo[clave] = on
            continue

        if _escribir_reles(client, [clave], on):
            # No se pudo confirmar: se deja el estado ANTERIOR (y el apagado
            # explícito sin consumir) para reintentar en la próxima vuelta.
            # Darlo por aplicado sería justo el bug que evita el nuevo valor
            # de retorno de _escribir_reles.
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"voz: {clave} -> {'encendido' if on else 'apagado'} NO confirmado, "
                  f"se reintenta en el próximo ciclo")
            nuevo[clave] = anterior
            continue

        forzado = " (apagado explícito)" if apagado_explicito and not on else ""
        print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
              f"voz: {clave} -> {'encendido' if on else 'apagado'}{forzado}")
        if apagado_explicito:
            voz_comandos.consumir_apagado_pendiente(clave)
        nuevo[clave] = on
    return nuevo


def limpiar_ciclo_lavado_al_arrancar(con: sqlite3.Connection) -> bool:
    """Si la base todavía dice que hay un Lavado Automático corriendo (este
    proceso se reinició en medio de un ciclo), lo da por TERMINADO. True si
    había algo que cortar.

    Es la opción conservadora, y es deliberada: el apagado de seguridad de
    acá abajo de-energiza las 8 salidas, incluidas las que sostenía la etapa
    en curso, y procesar_ciclo_lavado solo escribe en los CAMBIOS de etapa
    -- dejar el ciclo "activo" haría que el resto de esa etapa corriera con
    las bombas muertas informando avance normal, y que al cambiar de etapa
    volviera a prender solo. Frenar es la dirección segura de este proyecto
    (ver CLAUDE.md); reafirmar los relés de la etapa sería resucitar un
    estado que se acaba de poner en cero a propósito.

    También descarta un `comando` que hubiera quedado encolado mientras este
    proceso estaba caído: un "iniciar" pedido hace vaya a saber cuánto, sin
    nadie esperando al lado del equipo, no puede arrancar bombas por el solo
    hecho de que el proceso volvió."""
    fila = con.execute(
        "SELECT comando, activo, etapa_actual FROM ciclo_lavado_estado WHERE id = 1"
    ).fetchone()
    if not fila:
        return False
    comando, activo, etapa_actual = fila
    if not activo and not comando:
        return False
    con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 0, "
                "etapa_actual = NULL, etapa_inicio = NULL WHERE id = 1")
    con.commit()
    if activo:
        _alarma(f"Había un LAVADO AUTOMÁTICO EN CURSO (etapa {(etapa_actual or 0) + 1}) al "
                f"arrancar iot_lavado.py.",
                "Se da por terminado y se apagan todas las salidas: el ciclo NO sigue solo.",
                "Si el lavado no había terminado, hay que volver a arrancarlo a mano.")
    else:
        print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
              f"se descarta el comando de lavado '{comando}' que quedó encolado "
              f"mientras este proceso estaba caído")
    return True


def apagar_actuadores_voz_al_arrancar(client: ModbusTcpClient) -> set:
    """Al arrancar el proceso no sabemos qué relés quedaron físicamente
    prendidos (el M300 mantiene su propio estado, independiente de este
    proceso) -- por seguridad, se fuerza apagado de todos los actuadores
    controlables por voz. Si algo se apaga así por error (por ejemplo
    alguien lo había prendido a mano desde la web del propio M300), hay que
    volver a prenderlo -- es una limitación aceptada, ver el spec.

    Vacía TAMBIÉN voz_actuadores_estado (ver spec, "Fuera de alcance v1": no
    se reanuda el estado sostenido de antes del reinicio, y "Arranque y
    reinicio": el arranque "vacía voz_actuadores_estado, no reafirma 'on'
    para nada"). Sin esto, un actuador que quedó sostenido por voz antes del
    reinicio se volvería a prender SOLO en la primera vuelta de
    procesar_comandos_voz (compara contra la base, no contra el Modbus recién
    escrito) -- se hace ANTES de tocar Modbus, para que quede garantizado
    pase lo que pase con la escritura física de abajo (la base sosteniendo
    "esto debería estar prendido" es la mitad peligrosa de este arranque).

    Devuelve el SET de relés que quedaron SIN CONFIRMAR (vacío = los 8
    apagados y aceptados por el M300). Se reintenta SOLO lo que falló:
    `client.connected` no sirve como señal de éxito (una respuesta de error
    del M300 deja el socket sano y, además, sólo reflejaría el resultado del
    ÚLTIMO relé de la vuelta)."""
    claves = sorted(actuadores_en_uso())
    for clave in claves:
        voz_comandos.limpiar_estado(clave)

    # Apagado físico con reintentos: justo después de un reinicio el M300
    # puede todavía estar arrancando y no responder al primer intento -- sin
    # reintentos, un relé que quedó prendido de antes se quedaría así para
    # siempre (el apagado de seguridad nunca se reintentaba).
    pendientes = set(claves)
    for intento in range(1, INTENTOS_APAGADO_ARRANQUE + 1):
        pendientes = _escribir_reles(client, sorted(pendientes), False)
        if not pendientes:
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"apagado de seguridad al arrancar: los {len(claves)} actuadores "
                  f"confirmados apagados por el M300")
            return set()
        print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
              f"apagar_actuadores_voz_al_arrancar: intento {intento}/{INTENTOS_APAGADO_ARRANQUE}, "
              f"sin confirmar {', '.join(sorted(pendientes))}")
        if intento < INTENTOS_APAGADO_ARRANQUE:
            time.sleep(INTERVALO_RECONEXION_S)
            if not client.connected:
                client.connect()
    _alarma(f"NO SE PUDO CONFIRMAR EL APAGADO DE: {', '.join(sorted(pendientes))} "
            f"tras {INTENTOS_APAGADO_ARRANQUE} intentos.",
            "Esos relés pueden estar ENERGIZADOS (bombas de agua/espuma/desinfectante).",
            "El estado conocido después de un reinicio tenía que ser 'todo apagado' y NO lo es.",
            "Ir a mirar los relés en el módulo físico / la web del M300 -- no alcanza con este log.")
    return pendientes


def _anunciar_voz(texto: str):
    """Reproduce `texto` por voz (síntesis de Windows). No bloquea el sondeo:
    se lanza en un proceso aparte, sin esperar a que termine de hablar."""
    texto_ps = texto.replace("'", "''")  # escapar comillas simples para PowerShell
    comando = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{VOZ_PREFERIDA}') }} catch {{}}; "
        f"$s.Speak('{texto_ps}')"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", comando],
                          creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass


def leer_analogica(client, funcion: str, direccion: int, unit_id: int, tipo_dato: str):
    """El número CRUDO de un canal analógico de un equipo Modbus adicional, o None si no hay lectura que valga: error del equipo,
    respuesta con menos registros de los pedidos, excepción (cierra el cliente, como `_leer_estado`, para forzar la reconexión),
    NaN o infinito. `uint16` e `int16` son un registro; `float32_ab_cd` son dos, el alto primero."""
    cantidad = 2 if tipo_dato == "float32_ab_cd" else 1
    try:
        if funcion == "holding_register":
            r = client.read_holding_registers(address=direccion, count=cantidad, device_id=unit_id)
        else:
            r = client.read_input_registers(address=direccion, count=cantidad, device_id=unit_id)
        if r is None or r.isError() or len(r.registers) < cantidad:
            return None
        regs = [int(x) for x in r.registers[:cantidad]]
    except Exception:  # noqa: BLE001
        client.close()
        return None
    try:
        if tipo_dato == "float32_ab_cd":
            valor = struct.unpack(">f", struct.pack(">HH", *regs))[0]
        elif tipo_dato == "int16":
            valor = struct.unpack(">h", struct.pack(">H", regs[0]))[0]
        else:
            valor = struct.unpack(">H", struct.pack(">H", regs[0]))[0]
    except struct.error:
        return None
    return float(valor) if math.isfinite(valor) else None


class _EquipoModbus:
    """Lo que `ConexionesModbus` recuerda de cada equipo entre ciclos."""

    def __init__(self, host, port, client):
        self.host, self.port, self.client = host, port, client      # `client` es None hasta que la fábrica lo entrega
        self.anteriores, self.ultimo_latido, self.ultimo_guardado = {}, {}, {}
        self.proximo_intento = None          # None = leerlo en este ciclo
        self.canal_hasta = {}                # canal -> hasta cuándo se lo deja en paz (lo último que hizo fue no contestar a tiempo)
        self.ultimo_error = None             # el texto que ya está escrito en origenes_estado
        self.ultima_recepcion = None         # la última vez que se escribió `ultima_recepcion`
        self.ausente = 0                     # ciclos seguidos en que el equipo no figuró en la lista


class ConexionesModbus:
    """Los equipos Modbus que el tambo agregó (`iot_origenes`, tipo `modbus_tcp`), cada uno con SU cliente. El equipo principal no
    pasa por acá: sigue con el suyo, en `main`, y se atiende primero.

    En cada `ciclo` se relee la lista (un equipo nuevo, un canal que se saca, un host o puerto cambiado se toman sin reiniciar).
    Se lee todo lo que el equipo declara. **Lo que cuesta un equipo que no contesta está acotado**, porque este ciclo corre en el
    mismo hilo que el del principal (lavado, voz, pulsos):
      * cada equipo tiene un tope de tiempo por ciclo (`PRESUPUESTO_ORIGEN_S`): lo que no llegó a leerse espera al ciclo siguiente;
      * un canal que no contestó a tiempo (el cliente se cae esperando) se deja en paz el DOBLE de `REINTENTO_ORIGEN_CAIDO_S` (así cada reintento del equipo prueba canales distintos): un unit ID que nadie
        atiende detrás de un gateway no se paga en cada ciclo;
      * un equipo que no conecta, o en el que NINGÚN canal contesta, queda «caído»: su error y la fecha van a `origenes_estado` y no se
        reintenta hasta `REINTENTO_ORIGEN_CAIDO_S`. Uno caído no frena a los demás.
    Un equipo que no figura en la lista se cierra recién a la segunda ausencia seguida: `iot_origenes.cargar()` devuelve [] un instante si
    Windows tiene el archivo tomado, y olvidar los equipos anotaría un cambio falso por cada canal digital."""

    def __init__(self, fabrica=None):
        self.fabrica = fabrica or (lambda host, port: ModbusTcpClient(host, port=port, timeout=TIMEOUT_ORIGEN_S, retries=0))
        self.equipos = {}

    def ciclo(self, con: sqlite3.Connection, origenes: list, ahora: datetime.datetime = None) -> None:
        """`ahora` solo se pasa en las pruebas: si no, cada equipo toma la hora al empezar (el ciclo de uno lento puede tardar)."""
        vigentes = {o["id"] for o in origenes if o["canales"]}
        for id_ in [i for i in self.equipos if i not in vigentes]:
            self.equipos[id_].ausente += 1
            if self.equipos[id_].ausente >= 2:
                self._cerrar(id_)
        for o in origenes:
            if o["id"] not in vigentes:
                continue
            ahora_equipo = ahora or datetime.datetime.now()
            try:
                self._equipo(con, o, ahora_equipo)
            except Exception as e:  # noqa: BLE001  -- lo que le pase a un equipo no puede frenar a los demás
                print(f"{ahora_equipo.isoformat(timespec='seconds')}  equipo Modbus {o['id']}: {e}")
                self._caido(con, o, self.equipos.get(o["id"]), ahora_equipo, f"Error leyendo el equipo: {e}")

    def cerrar_todo(self) -> None:
        for id_ in list(self.equipos):
            self._cerrar(id_)

    def _cerrar(self, id_: str) -> None:
        equipo = self.equipos.pop(id_, None)
        if equipo is not None and equipo.client is not None:
            try:
                equipo.client.close()
            except Exception:  # noqa: BLE001
                pass

    def _equipo(self, con, o: dict, ahora: datetime.datetime) -> None:
        eq = self.equipos.get(o["id"])
        if eq is not None and (eq.host, eq.port) != (o["host"], o["port"]):
            self._cerrar(o["id"])
            eq = None
            # El error de la dirección vieja ya no vale: si la nueva anda, la tarjeta no puede seguir diciendo «No se pudo conectar a <la vieja>».
            con.execute("UPDATE origenes_estado SET ultimo_error = NULL, error_fecha = NULL WHERE origen = ?", (o["id"],))
            con.commit()
        if eq is None:
            eq = self.equipos[o["id"]] = _EquipoModbus(o["host"], o["port"], None)
        eq.ausente = 0
        if eq.proximo_intento is not None and ahora < eq.proximo_intento:
            return
        if eq.client is None:
            eq.client = self.fabrica(o["host"], o["port"])       # si explota, `ciclo` lo deja caído con su reintento programado
        if not eq.client.connected:
            eq.client.connect()
        if not eq.client.connected:
            self._caido(con, o, eq, ahora, f"No se pudo conectar a {o['host']}:{o['port']}")
            return
        contestaron, intentados, rechazos = self._leer_canales(con, o, eq, ahora)
        if intentados == 0:
            return                       # todos los canales están en espera: no hay nada nuevo que decir del equipo
        if contestaron == 0:
            eq.client.close()
            if rechazos == intentados:   # está vivo y contesta: lo que no coincide es lo que se le pide
                texto = "El equipo contesta pero rechaza todas las lecturas: revisá las direcciones, las funciones y el unit ID"
            else:
                texto = "El equipo no respondió a ninguna lectura"
            self._caido(con, o, eq, ahora, texto)
            return
        eq.proximo_intento, eq.ultimo_error = None, None
        if eq.ultima_recepcion is None or (ahora - eq.ultima_recepcion).total_seconds() >= INTERVALO_LATIDO_DI_S:
            con.execute("""
                INSERT INTO origenes_estado (origen, ultima_recepcion) VALUES (?, ?)
                ON CONFLICT(origen) DO UPDATE SET ultima_recepcion = excluded.ultima_recepcion
            """, (o["id"], ahora.isoformat(timespec="seconds")))
            con.commit()
            eq.ultima_recepcion = ahora

    def _caido(self, con, o: dict, eq, ahora: datetime.datetime, texto: str) -> None:
        texto = texto[:MAX_ERROR_ORIGEN]
        if eq is not None:
            eq.proximo_intento = ahora + datetime.timedelta(seconds=REINTENTO_ORIGEN_CAIDO_S)
            if eq.ultimo_error == texto:
                return                       # el mismo error: no se reescribe ni se cambia su fecha
            eq.ultimo_error = texto
        con.execute("""
            INSERT INTO origenes_estado (origen, ultima_recepcion, ultimo_error, error_fecha) VALUES (?, '', ?, ?)
            ON CONFLICT(origen) DO UPDATE SET ultimo_error = excluded.ultimo_error, error_fecha = excluded.error_fecha
        """, (o["id"], texto, ahora.isoformat(timespec="seconds")))
        con.commit()

    def _leer_canales(self, con, o: dict, eq, ahora: datetime.datetime) -> tuple:
        """Lee los canales del equipo y devuelve (contestaron, intentados, rechazos). Uno que no contesta se saltea (no se toma por
        apagado ni por 0). Si el cliente quedó caído después de un pedido fue porque el equipo no contestó a tiempo (o cortó): ese canal
        espera el doble de `REINTENTO_ORIGEN_CAIDO_S`. Un error del equipo con el cliente vivo (una dirección que no tiene) es un `rechazo`."""
        marca = ahora.isoformat(timespec="seconds")
        invertidas = entradas_invertidas()
        inicio = time.monotonic()
        contestaron = intentados = rechazos = 0
        for c in o["canales"]:
            glob = iot_origenes.canal_global(o["id"], c["clave"])
            if eq.canal_hasta.get(glob) is not None and ahora < eq.canal_hasta[glob]:
                continue
            if time.monotonic() - inicio >= PRESUPUESTO_ORIGEN_S:
                break                    # el resto, al ciclo siguiente: el principal no puede esperar a un equipo lento
            intentados += 1
            unit_id = c["unit_id"] or o["unit_id"]
            nc = False
            if c["grupo"] == "entrada_digital":
                nc = c["invertido"] != invertidas.get(glob, False)
                resultado = _leer_estado(eq.client, c["direccion"], nc, c["funcion"], unit_id)
            else:
                resultado = leer_analogica(eq.client, c["funcion"], c["direccion"], unit_id, c["tipo_dato"])
            if resultado is None:
                if eq.client.connected:
                    rechazos += 1
                else:
                    eq.canal_hasta[glob] = ahora + datetime.timedelta(seconds=2 * REINTENTO_ORIGEN_CAIDO_S)
                continue
            eq.canal_hasta.pop(glob, None)
            if c["grupo"] == "entrada_digital":
                contestaron += 1
                anotar_ultimo(con, glob, marca, 1.0 if resultado != nc else 0.0)   # lo que dio el equipo, antes del contacto NC
                latir(con, glob, resultado, eq.ultimo_latido, ahora)
                eq.anteriores[glob] = registrar_si_cambio(con, glob, resultado, eq.anteriores.get(glob))
                continue
            valor = resultado * c["factor"] + c["offset"]
            if not math.isfinite(valor):
                rechazos += 1
                continue
            contestaron += 1
            valor = round(valor, 6)
            anotar_ultimo(con, glob, marca, valor)
            anterior = eq.ultimo_guardado.get(glob)
            if c["sensor"] and (anterior is None or (ahora - anterior).total_seconds() >= INTERVALO_GUARDADO_ANALOGICAS_S):
                con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)", (c["sensor"], marca, valor))
                eq.ultimo_guardado[glob] = ahora
        con.commit()
        return contestaron, intentados, rechazos


def main():
    con = _conectar_db()
    cfg = iot_conexion.config()
    client = ModbusTcpClient(cfg["host"], port=cfg["port"])
    anteriores = {canal: None for canal in CANALES}
    anteriores_voz = {clave: False for clave in actuadores_en_uso()}
    ultimo_guardado_analogicas = {}
    ultimo_latido = {}
    conexiones = ConexionesModbus()     # los equipos Modbus adicionales (si el tambo agregó alguno)
    print(f"Conectando a {cfg['host']}:{cfg['port']}... (Ctrl+C para salir)")
    try:
        if not client.connected:
            client.connect()
        # Orden: primero se corta lo que la base dice que estaba corriendo
        # (para que nada reclame relés) y recién ahí se fuerza el apagado
        # físico, que es lo que deja el estado conocido en "todo apagado".
        limpiar_ciclo_lavado_al_arrancar(con)
        apagar_actuadores_voz_al_arrancar(client)
        while True:
            if not client.connected:
                client.connect()
            procesar_entradas(con, client, anteriores, ultimo_latido)
            procesar_analogicas(con, client, ultimo_guardado_analogicas)
            ejecutar_comandos_pendientes(con, client)
            reles_lavado = procesar_ciclo_lavado(con, client)
            anteriores_voz = procesar_comandos_voz(con, client, anteriores_voz, reles_lavado)
            # Después del principal, que no espera a nadie. Cada equipo en su propio try: uno caído no frena a los demás.
            conexiones.ciclo(con, [o for o in iot_origenes.cargar() if o["tipo"] == "modbus_tcp"])
            time.sleep(INTERVALO_POLL_S)
    except KeyboardInterrupt:
        print("Cortado por el usuario.")
    finally:
        conexiones.cerrar_todo()
        client.close()
        con.close()


if __name__ == "__main__":
    main()
