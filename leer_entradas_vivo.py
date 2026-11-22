"""Lectura en vivo de las entradas (y salidas) del M300 por Modbus TCP. SOLO LECTURA.

Uso:  python leer_entradas_vivo.py [host] [segundos_entre_lecturas]
Corta con Ctrl+C. Imprime una línea cada vez que algo cambia, y una línea de
estado que se refresca en el lugar.

Ojo: si iot_lavado.py está corriendo contra el mismo M300 (SERVER-DELPRO), esta
es una segunda conexión; funciona, pero es una prueba corta, no para dejar fija.
"""
import sys
import time
from datetime import datetime

from pymodbus.client import ModbusTcpClient

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.1"
PERIODO = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
ENTRADAS = [("DI01 (lavado)", 0), ("DI02 (barrido)", 1)]  # 10001, 10002
SALIDAS = 8  # DO11..DO18 = coils 0..7


def leer(c):
    di = {}
    for nombre, addr in ENTRADAS:
        r = c.read_discrete_inputs(addr, count=1, device_id=1)
        di[nombre] = None if r.isError() else bool(r.bits[0])
    r = c.read_coils(0, count=SALIDAS, device_id=1)
    do = [None] * SALIDAS if r.isError() else [bool(b) for b in r.bits[:SALIDAS]]
    return di, do


def texto(v):
    return "?" if v is None else ("ACTIVA" if v else "apagada")


def main():
    c = ModbusTcpClient(HOST, port=502, timeout=3)
    if not c.connect():
        print(f"No conecta con {HOST}:502")
        return 1
    print(f"Leyendo {HOST} cada {PERIODO} s. Ctrl+C para cortar.\n")
    anterior = None
    try:
        while True:
            di, do = leer(c)
            estado = (tuple(di.values()), tuple(do))
            hora = datetime.now().strftime("%H:%M:%S")
            if estado != anterior:
                if anterior is not None:
                    print(f"\r{' ' * 100}\r{hora}  CAMBIO:", end=" ")
                    for (nombre, _), antes, ahora in zip(ENTRADAS, anterior[0], estado[0]):
                        if antes != ahora:
                            print(f"{nombre}: {texto(antes)} -> {texto(ahora)}", end="  ")
                    for i, (antes, ahora) in enumerate(zip(anterior[1], estado[1]), start=1):
                        if antes != ahora:
                            print(f"DO{i}: {texto(antes)} -> {texto(ahora)}", end="  ")
                    print()
                anterior = estado
            linea = "  ".join(f"{n}: {texto(v)}" for n, v in di.items())
            salidas = "".join("█" if v else ("·" if v is not None else "?") for v in do)
            print(f"\r{hora}  {linea}   salidas [{salidas}]   ", end="", flush=True)
            time.sleep(PERIODO)
    except KeyboardInterrupt:
        print("\nCortado.")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
