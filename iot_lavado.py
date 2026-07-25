# -*- coding: utf-8 -*-
"""Poller de Modbus TCP para el gateway PUSR M300: lee el estado de DI01
(contacto seco del tablero de la lavadora/sistema de lavado de la rotativa)
y lo guarda en SQLite con marca de tiempo, cada vez que CAMBIA de estado.

Arquitectura (ver memoria delpro-iot-gateway): el M300 expone sus I/O locales
como servidor Modbus TCP (puerto 502, "Local_IO" → DI01 mapeado a la
dirección 10001 = protocolo 0-based 0). Esta base SQLite se cruza después
con la base DDM de DelPro a nivel aplicación (no hay JOIN SQL directo entre
motores distintos).

DI01 = contacto seco: 1 = lavando, 0 = no lavando. Si al cablear queda
invertido (activo cuando en realidad NO está lavando), poner
ESTADO_INVERTIDO = True en vez de recablear.

Corre como proceso aparte, continuo (no es parte de la app Flask):
    python iot_lavado.py
"""
import datetime
import sqlite3
import time

from pymodbus.client import ModbusTcpClient

HOST = "192.168.1.1"
PORT = 502
DIRECCION_DI01 = 0        # protocolo Modbus 0-based; "10001" en la UI del gateway
INTERVALO_POLL_S = 3      # cada cuánto se pregunta el estado
INTERVALO_RECONEXION_S = 5
ESTADO_INVERTIDO = False

RUTA_DB = "iot_sensores.db"
CANAL_LAVADO = "lavado_rotativa"


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
    con.commit()
    return con


def _leer_estado(client: ModbusTcpClient):
    """True/False = estado leído; None = error de lectura (no se toca el estado anterior)."""
    try:
        resultado = client.read_discrete_inputs(address=DIRECCION_DI01, count=1, device_id=1)
        if resultado.isError():
            return None
        valor = bool(resultado.bits[0])
        return (not valor) if ESTADO_INVERTIDO else valor
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
    print(f"{ahora}  {canal} -> {'LAVANDO' if estado else 'parado'}")
    return estado


def main():
    con = _conectar_db()
    client = ModbusTcpClient(HOST, port=PORT)
    estado_anterior = None
    print(f"Conectando a {HOST}:{PORT}... (Ctrl+C para salir)")
    try:
        while True:
            if not client.connected:
                client.connect()
            estado = _leer_estado(client)
            if estado is None:
                time.sleep(INTERVALO_RECONEXION_S)
                continue
            estado_anterior = registrar_si_cambio(con, CANAL_LAVADO, estado, estado_anterior)
            time.sleep(INTERVALO_POLL_S)
    except KeyboardInterrupt:
        print("Cortado por el usuario.")
    finally:
        client.close()
        con.close()


if __name__ == "__main__":
    main()
