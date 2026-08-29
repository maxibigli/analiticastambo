# -*- coding: utf-8 -*-
"""Genera lecturas FICTICIAS para lecturas_sensor, unicamente para poder ver
el grafico de historico de la pantalla ESP32 funcionando mientras no hay
sensores reales instalados (ver iot_monitoreo.SENSORES_PLANEADOS).

NO correr en la base de produccion sin avisar: mezcla datos falsos en una
tabla que hoy esta vacia. Pensado para la PC de desarrollo, mientras se
prueba la pantalla. Como hoy la tabla no tiene NINGUNA lectura real (ver el
comentario de SENSORES_PLANEADOS), todo lo que haya ahi por ahora es este
lote ficticio -- antes de que se instale hardware real, borrar TODO con:

    DELETE FROM lecturas_sensor;

Uso:
    python scripts/generar_lecturas_ficticias.py
"""
import datetime
import math
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iot_lavado import RUTA_DB

DIAS_HISTORIA = 200
INTERVALO_HORAS = 2

# clave -> (centro, amplitud diaria, ruido, min, max) -- valores plausibles
# para un tambo, no medidos: son SOLO para probar el grafico.
PERFILES = {
    "temp_leche":         (5.5, 0.5, 0.4, 0, 40),
    "temp_llegada":       (6.5, 0.8, 0.6, 0, 40),
    "temp_ambiente":      (18.0, 8.0, 1.5, -10, 45),
    "hum_ambiente":       (65.0, -15.0, 4.0, 0, 100),   # inversa a temp_ambiente
    "temp_lavado":        (55.0, 5.0, 4.0, 0, 90),
    "temp_sala_maquinas": (22.0, 3.0, 1.2, -10, 50),
    "temp_sala_tableros": (24.0, 2.5, 1.0, -10, 50),
    "temp_sala_caldera":  (45.0, 4.0, 3.0, 0, 80),
    "temp_corral":        (17.0, 9.0, 1.8, -10, 45),
    "hum_corral":         (60.0, -14.0, 4.5, 0, 100),
    "vacio_general":      (45.0, 1.0, 1.5, 0, 60),
}


def valor_en(clave, momento, fase_dia):
    centro, amplitud, ruido, minimo, maximo = PERFILES[clave]
    ciclo_diario = math.sin(2 * math.pi * (fase_dia - 6) / 24)  # pico ~14-15h
    v = centro + amplitud * ciclo_diario + random.gauss(0, ruido)
    return round(max(minimo, min(maximo, v)), 2)


def generar():
    con = sqlite3.connect(RUTA_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas_sensor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT NOT NULL,
            fecha_hora TEXT NOT NULL,
            valor REAL NOT NULL
        )
    """)
    existentes = con.execute("SELECT COUNT(*) FROM lecturas_sensor").fetchone()[0]
    if existentes > 0:
        print(f"lecturas_sensor ya tiene {existentes} filas -- no se toca nada. "
              "Si son de una corrida anterior de este script y queres regenerar, "
              "borralas primero a mano.")
        con.close()
        return

    ahora = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
    desde = ahora - datetime.timedelta(days=DIAS_HISTORIA)

    filas = []
    t = desde
    while t <= ahora:
        fase_dia = t.hour + t.minute / 60
        fecha_hora = t.isoformat(timespec="minutes")
        for clave in PERFILES:
            filas.append((clave, fecha_hora, valor_en(clave, t, fase_dia)))
        t += datetime.timedelta(hours=INTERVALO_HORAS)

    con.executemany(
        "INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES (?, ?, ?)", filas
    )
    con.commit()
    con.close()
    print(f"Insertadas {len(filas)} filas ficticias ({len(PERFILES)} sensores x "
          f"{len(filas)//len(PERFILES)} puntos, cada {INTERVALO_HORAS}h, "
          f"del {desde.date()} al {ahora.date()}) en {RUTA_DB}.")


if __name__ == "__main__":
    generar()
