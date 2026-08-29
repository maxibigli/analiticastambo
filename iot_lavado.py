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

HOST = "192.168.1.1"
PORT = 502
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
        con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = ? WHERE id = ?",
                    (resultado, cmd_id))
        con.commit()


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
    client = ModbusTcpClient(HOST, port=PORT)
    anteriores = {canal: None for canal in CANALES}
    print(f"Conectando a {HOST}:{PORT}... (Ctrl+C para salir)")
    try:
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
            time.sleep(INTERVALO_POLL_S)
    except KeyboardInterrupt:
        print("Cortado por el usuario.")
    finally:
        client.close()
        con.close()


if __name__ == "__main__":
    main()
