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
import sqlite3
import subprocess
import time

from pymodbus.client import ModbusTcpClient

import iot_conexion
import lavado_programa
import voz_comandos

INTERVALO_POLL_S = 3      # cada cuánto se pregunta el estado
INTERVALO_RECONEXION_S = 5

# Canal lógico -> (dirección Modbus 0-based, invertido?). "10001"/"10002" en
# la UI del gateway = direcciones 0/1 acá. El M300 tiene 8 DI en total; solo
# dos están cableadas a algo con nombre (lavado/barrido) -- las otras seis
# quedan con nombre genérico "di_N" hasta que el tambo defina qué miden. Ver
# iot_monitoreo.panel_io, que arma el panel de 8 tarjetas con estos canales.
CANALES = {
    "lavado_rotativa": {"direccion": 0, "invertido": False},
    "barrido_rotativa": {"direccion": 1, "invertido": False},
    "di_3": {"direccion": 2, "invertido": False},
    "di_4": {"direccion": 3, "invertido": False},
    "di_5": {"direccion": 4, "invertido": False},
    "di_6": {"direccion": 5, "invertido": False},
    "di_7": {"direccion": 6, "invertido": False},
    "di_8": {"direccion": 7, "invertido": False},
}

# Salidas (DO) del M300 -- 8 disponibles, ninguna con actuador físico
# definido todavía. Se manejan como PULSADOR (se activa un ratito y se
# suelta sola), no como llave: es el criterio más seguro para un botón que
# se toca desde una pantalla sin ver el equipo, y es el mismo concepto que
# un botón de arranque de un tablero real. Dirección Modbus 0-based de la
# bobina (coil) -- espacio de direcciones DISTINTO al de las DI de arriba,
# aunque los números se repitan (0 a 7 en los dos casos).
ACTUADORES = {
    "do_1": 0, "do_2": 1, "do_3": 2, "do_4": 3,
    "do_5": 4, "do_6": 5, "do_7": 6, "do_8": 7,
}
DURACION_PULSO_S = 0.5

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
    con.commit()
    return con


def _leer_estado(client: ModbusTcpClient, direccion: int, invertido: bool):
    """True/False = estado leído; None = error de lectura (no se toca el estado anterior)."""
    try:
        resultado = client.read_discrete_inputs(address=direccion, count=1, device_id=1)
        if resultado.isError():
            return None
        valor = bool(resultado.bits[0])
        return (not valor) if invertido else valor
    except Exception:  # noqa: BLE001
        client.close()   # ver el comentario igual en _escribir_reles: fuerza reconexion
        return None


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


def ejecutar_comandos_pendientes(con: sqlite3.Connection, client: ModbusTcpClient):
    """Busca pulsos de actuador pedidos desde la pantalla (tabla
    comandos_actuador, cargada por Flask) y los ejecuta ACA -- este proceso
    es el único dueño de la conexión Modbus al M300, para no abrir una
    segunda conexión TCP en paralelo desde app.py y pisarse con esta."""
    pendientes = con.execute(
        "SELECT id, canal FROM comandos_actuador WHERE ejecutado = 0 ORDER BY id"
    ).fetchall()
    for cmd_id, canal in pendientes:
        direccion = ACTUADORES.get(canal)
        if direccion is None:
            resultado = f"error: canal desconocido ({canal})"
        else:
            try:
                if not client.connected:
                    client.connect()
                client.write_coil(address=direccion, value=True, device_id=1)
                time.sleep(DURACION_PULSO_S)
                client.write_coil(address=direccion, value=False, device_id=1)
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


def _escribir_reles(client: ModbusTcpClient, claves: list, prender: bool):
    for clave in claves:
        direccion = ACTUADORES.get(clave)
        if direccion is None:
            continue
        try:
            if not client.connected:
                client.connect()
            client.write_coil(address=direccion, value=prender, device_id=1)
        except Exception as e:  # noqa: BLE001
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"error {'prendiendo' if prender else 'apagando'} {clave}: {e}")
            client.close()   # mismo motivo que en ejecutar_comandos_pendientes: forzar reconexion


def procesar_ciclo_lavado(con: sqlite3.Connection, client: ModbusTcpClient):
    """Motor del programa de lavado automático (ver lavado_programa.py):
    prende/apaga relés según la etapa configurada y el tiempo transcurrido.
    Corre en el MISMO ciclo de sondeo de 3s que el resto de este archivo, no
    en un hilo aparte -- las etapas avanzan con precisión de unos segundos,
    que alcanza de sobra para un ciclo que dura minutos, y evita coordinar
    dos cosas escribiendo Modbus al mismo tiempo."""
    fila = con.execute(
        "SELECT comando, activo, etapa_actual, etapa_inicio FROM ciclo_lavado_estado WHERE id = 1"
    ).fetchone()
    comando, activo, etapa_actual, etapa_inicio = fila if fila else (None, 0, None, None)
    programa = lavado_programa.etapas()
    ahora = datetime.datetime.now()

    if comando == "cancelar":
        if activo and etapa_actual is not None and etapa_actual < len(programa):
            _escribir_reles(client, programa[etapa_actual]["reles"], False)
            for clave in programa[etapa_actual]["reles"]:
                voz_comandos.limpiar_estado(clave)
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 0, "
                    "etapa_actual = NULL, etapa_inicio = NULL WHERE id = 1")
        con.commit()
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: cancelado")
        return

    if comando == "iniciar" and not activo and programa:
        _escribir_reles(client, programa[0]["reles"], True)
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL, activo = 1, "
                    "etapa_actual = 0, etapa_inicio = ? WHERE id = 1",
                    (ahora.isoformat(timespec="seconds"),))
        con.commit()
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: arranca etapa 1/{len(programa)}")
        return

    if comando:   # 'iniciar' pedido sin programa, o repetido con uno ya activo: se descarta
        con.execute("UPDATE ciclo_lavado_estado SET comando = NULL WHERE id = 1")
        con.commit()

    if not (activo and etapa_actual is not None and etapa_inicio and programa):
        return
    if etapa_actual >= len(programa):
        return   # config cambio mientras corria (menos etapas ahora); se corta solo en el proximo cancelar

    transcurrido = (ahora - datetime.datetime.fromisoformat(etapa_inicio)).total_seconds()
    etapa_cfg = programa[etapa_actual]
    if transcurrido < etapa_cfg["duracion_s"]:
        return

    _escribir_reles(client, etapa_cfg["reles"], False)
    for clave in etapa_cfg["reles"]:
        voz_comandos.limpiar_estado(clave)
    siguiente = etapa_actual + 1
    if siguiente < len(programa):
        _escribir_reles(client, programa[siguiente]["reles"], True)
        con.execute("UPDATE ciclo_lavado_estado SET etapa_actual = ?, etapa_inicio = ? WHERE id = 1",
                    (siguiente, ahora.isoformat(timespec="seconds")))
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: etapa {siguiente + 1}/{len(programa)}")
    else:
        con.execute("UPDATE ciclo_lavado_estado SET activo = 0, etapa_actual = NULL, "
                    "etapa_inicio = NULL WHERE id = 1")
        print(f"{ahora.isoformat(timespec='seconds')}  lavado automático: ciclo completo")
    con.commit()


def procesar_comandos_voz(con: sqlite3.Connection, client: ModbusTcpClient, anteriores_voz: dict) -> dict:
    """Aplica por Modbus los cambios en voz_comandos.estado() (actuadores
    sostenidos por voz) desde la última vuelta -- solo escribe cuando algo
    CAMBIÓ, mismo criterio que registrar_si_cambio/procesar_ciclo_lavado.
    anteriores_voz: clave -> bool aplicado la vuelta pasada; devuelve el
    dict actualizado para pasarlo de nuevo en la próxima vuelta."""
    deseado = voz_comandos.estado()  # clave -> encendido_desde, solo las que están ON
    nuevo = {}
    for clave in voz_comandos.ACTUADORES_VALIDOS:
        on = clave in deseado
        if on != anteriores_voz.get(clave, False):
            _escribir_reles(client, [clave], on)
            print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
                  f"voz: {clave} -> {'encendido' if on else 'apagado'}")
        nuevo[clave] = on
    return nuevo


def apagar_actuadores_voz_al_arrancar(client: ModbusTcpClient) -> None:
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
    """
    claves = sorted(voz_comandos.ACTUADORES_VALIDOS)
    for clave in claves:
        voz_comandos.limpiar_estado(clave)

    # Apagado físico con reintentos: justo después de un reinicio el M300
    # puede todavía estar arrancando y no responder al primer intento -- sin
    # reintentos, un relé que quedó prendido de antes se quedaría así para
    # siempre (el apagado de seguridad nunca se reintentaba).
    intentos_max = 3
    for intento in range(1, intentos_max + 1):
        _escribir_reles(client, claves, False)
        if client.connected:
            return
        print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
              f"apagar_actuadores_voz_al_arrancar: intento {intento}/{intentos_max} sin conexión al M300")
        if intento < intentos_max:
            time.sleep(INTERVALO_RECONEXION_S)
            client.connect()
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')}  "
          f"apagar_actuadores_voz_al_arrancar: no se pudo confirmar el apagado tras {intentos_max} intentos")


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


def main():
    con = _conectar_db()
    cfg = iot_conexion.config()
    client = ModbusTcpClient(cfg["host"], port=cfg["port"])
    anteriores = {canal: None for canal in CANALES}
    anteriores_voz = {clave: False for clave in voz_comandos.ACTUADORES_VALIDOS}
    print(f"Conectando a {cfg['host']}:{cfg['port']}... (Ctrl+C para salir)")
    try:
        if not client.connected:
            client.connect()
        apagar_actuadores_voz_al_arrancar(client)
        while True:
            if not client.connected:
                client.connect()
            for canal, cfg in CANALES.items():
                estado = _leer_estado(client, cfg["direccion"], cfg["invertido"])
                if estado is None:
                    continue  # error de lectura puntual: se reintenta el próximo ciclo
                arranco = estado and estado != anteriores[canal]
                anteriores[canal] = registrar_si_cambio(con, canal, estado, anteriores[canal])
                if arranco and AUDIO_ACTIVADO and canal in MENSAJES_VOZ:
                    _anunciar_voz(MENSAJES_VOZ[canal])
            ejecutar_comandos_pendientes(con, client)
            procesar_ciclo_lavado(con, client)
            anteriores_voz = procesar_comandos_voz(con, client, anteriores_voz)
            time.sleep(INTERVALO_POLL_S)
    except KeyboardInterrupt:
        print("Cortado por el usuario.")
    finally:
        client.close()
        con.close()


if __name__ == "__main__":
    main()
