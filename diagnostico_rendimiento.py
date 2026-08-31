# -*- coding: utf-8 -*-
"""Diagnostico de "Rendimiento Sala" devolviendo 500 en SERVER-DELPRO.

Correr EN SERVER-DELPRO, parado en la carpeta del proyecto, con el MISMO
interprete que esta sirviendo el puerto 5310 (ver README de abajo). Pega a los
mismos endpoints que usa la pantalla, en proceso, con las excepciones
propagadas para ver el traceback completo en vez del HTML de error de Flask.

SOLO LECTURA: no escribe en la base, no manda alertas, no toca configuracion.

    cd C:\\ruta\\del\\proyecto
    <interprete> diagnostico_rendimiento.py

El interprete correcto se averigua asi (PowerShell):

    Get-NetTCPConnection -LocalPort 5310 -State Listen | Select-Object OwningProcess
    Get-Process -Id <ese PID> | Select-Object Path
"""
import datetime
import os
import sys
import traceback

print("=" * 70)
print("ENTORNO")
print("=" * 70)
print(f"  python      {sys.version.split()[0]}")
print(f"  ejecutable  {sys.executable}")
print(f"  carpeta     {os.getcwd()}")
print(f"  hay servidor.py aca: {os.path.exists('servidor.py')}")
print(f"  hay app.py aca:      {os.path.exists('app.py')}")

print("\n  versiones de las dependencias con binarios nativos:")
for paquete in ("pyodbc", "numpy", "cv2", "pymodbus", "flask", "waitress"):
    try:
        mod = __import__(paquete)
        print(f"    {paquete:12} {getattr(mod, '__version__', '(sin __version__)')}")
    except Exception as e:  # noqa: BLE001
        print(f"    {paquete:12} NO SE PUDO IMPORTAR -> {type(e).__name__}: {e}")

print("\n  drivers ODBC instalados:")
try:
    import pyodbc
    for d in pyodbc.drivers():
        print(f"    {d}")
except Exception as e:  # noqa: BLE001
    print(f"    no se pudo listar -> {type(e).__name__}: {e}")

print("\n" + "=" * 70)
print("CONEXION A LA BASE")
print("=" * 70)
TAMBO = sys.argv[1] if len(sys.argv) > 1 else "ponderosa"
print(f"  tambo: {TAMBO}")
try:
    import db
    d = db.run_query("SELECT TOP 1 GETDATE() AS ahora", tambo=TAMBO, max_rows=5)
    print(f"  OK -> {d['rows']}")
except Exception:
    print("  FALLA:")
    traceback.print_exc()

print("\n" + "=" * 70)
print("LOS DOS ENDPOINTS DE LA PANTALLA")
print("=" * 70)
try:
    import app as A
except Exception:
    print("  no se pudo importar app.py:")
    traceback.print_exc()
    raise SystemExit(1)

# Que las excepciones lleguen acá en vez de convertirse en el HTML de error
# de Flask -- justamente lo que la pantalla no puede mostrar ("Respuesta
# invalida del servidor" es esa pagina HTML donde se esperaba JSON).
A.app.config["PROPAGATE_EXCEPTIONS"] = True

hoy = datetime.date.today()
desde = (hoy.replace(day=1)).isoformat()
hasta = hoy.isoformat()

cli = A.app.test_client()
with cli.session_transaction() as s:
    s["usuario"] = "diagnostico"
    s["rol"] = "admin"

CASOS = [
    ("Rendimiento Sala (rango)",
     f"/api/rutina/rendimiento?tambo={TAMBO}&desde={desde}&hasta={hasta}"),
    ("Rendimiento de Ordeno (un dia)",
     f"/api/rutina/resumen_dia?tambo={TAMBO}"),
]

for nombre, url in CASOS:
    print(f"\n--- {nombre}\n    {url}")
    try:
        r = cli.get(url)
        ct = r.headers.get("Content-Type", "")
        cuerpo = r.get_data(as_text=True)
        print(f"    HTTP {r.status_code}   ct={ct}")
        if r.status_code == 202:
            print("    (202 = todavia calentando el cache; volve a correrlo en un minuto)")
        print(f"    body[:400]: {cuerpo[:400]}")
    except Exception:
        print("    EXCEPCION -- ESTE ES EL ERROR QUE BUSCAMOS:")
        traceback.print_exc()

print("\n" + "=" * 70)
print("Pegale esta salida COMPLETA a Claude.")
print("=" * 70)
