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
import time
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

# --- 1) Contra el servidor QUE ESTA CORRIENDO ------------------------------
# Es lo que de verdad importa: el 500 puede depender del ESTADO de ese proceso
# (cache tibio, warmup a medias), y un proceso nuevo no lo reproduciria. Se
# entra con una cookie de sesion firmada con la propia secret key de la app.
print("\n### 1) EL SERVIDOR QUE ESTA CORRIENDO (puerto 5310)")
try:
    import hashlib
    import urllib.error
    import urllib.request

    from flask.sessions import TaggedJSONSerializer
    from itsdangerous import URLSafeTimedSerializer

    with open("secret_key.txt") as f:
        clave = f.read().strip()
    firmador = URLSafeTimedSerializer(
        clave, salt="cookie-session", serializer=TaggedJSONSerializer(),
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1})
    cookie = firmador.dumps({"usuario": "diagnostico", "rol": "admin"})

    for nombre, url in CASOS:
        print(f"\n--- {nombre}")
        pedido = urllib.request.Request("http://127.0.0.1:5310" + url)
        pedido.add_header("Cookie", f"session={cookie}")
        try:
            # Esperar el calentamiento tambien acá: un 202 no dice nada, y lo
            # que buscamos es en que TERMINA este proceso, que es el que sirve
            # la pantalla.
            for intento in range(30):
                with urllib.request.urlopen(pedido, timeout=300) as resp:
                    cuerpo = resp.read().decode("utf-8", "replace")
                    codigo, tipo = resp.status, resp.headers.get("Content-Type")
                if codigo != 202:
                    break
                if intento == 0:
                    print("    202: calentando, esperando (hasta ~5 min)...")
                time.sleep(10)
            print(f"    HTTP {codigo}   ct={tipo}")
            if codigo == 202:
                print("    SIGUE EN 202: la consulta de fondo no termina.")
            print(f"    body[:300]: {cuerpo[:300]}")
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", "replace")
            es_json = "json" in (e.headers.get("Content-Type") or "")
            print(f"    HTTP {e.code}   ct={e.headers.get('Content-Type')}")
            print(f"    es JSON: {es_json}")
            if not es_json:
                print("    <<< ESTE es el error que la pantalla muestra como")
                print("        'Respuesta invalida del servidor' >>>")
            print(f"    body[:500]: {cuerpo[:500]}")
        except Exception as e:  # noqa: BLE001
            print(f"    no respondio: {type(e).__name__}: {e}")
except Exception:
    print("  no se pudo probar el servidor vivo:")
    traceback.print_exc()

# --- 2) En ESTE proceso, esperando el calentamiento ------------------------
# El cache es POR PROCESO: recien arrancado esta vacio y el endpoint contesta
# 202 mientras un hilo de fondo trae los datos. Hay que ESPERAR ese hilo acá
# adentro -- salir y volver a entrar arranca de cero otra vez.
print("\n\n### 2) EN ESTE PROCESO, con el traceback completo")
for nombre, url in CASOS:
    print(f"\n--- {nombre}\n    {url}")
    try:
        for intento in range(40):
            r = cli.get(url)
            if r.status_code != 202:
                break
            if intento == 0:
                print("    202: calentando el cache, esperando (hasta ~7 min)...")
            elif intento % 6 == 0:
                print(f"    ...sigue calentando (intento {intento + 1})")
            time.sleep(10)
        ct = r.headers.get("Content-Type", "")
        cuerpo = r.get_data(as_text=True)
        print(f"    HTTP {r.status_code}   ct={ct}")
        if r.status_code == 202:
            print("    SIGUE EN 202: la consulta de fondo no termino o esta fallando"
                  " en silencio (el worker se traga la excepcion).")
        print(f"    body[:400]: {cuerpo[:400]}")
    except Exception:
        print("    EXCEPCION -- ESTE ES EL ERROR QUE BUSCAMOS:")
        traceback.print_exc()

print("\n" + "=" * 70)
print("Pegale esta salida COMPLETA a Claude.")
print("=" * 70)
