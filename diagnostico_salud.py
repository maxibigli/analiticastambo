# -*- coding: utf-8 -*-
"""Diagnostico de "Salud del rodeo" atascada en "Calculando N secciones..."
en SERVER-DELPRO.

Mismo patron que diagnostico_rendimiento.py (ver ese archivo para el porque):
la pantalla pega a 9 endpoints (ver SECCIONES en templates/index.html,
cargarSalud()) y cada uno sirve de un cache en memoria llenado por un hilo de
fondo (_refresh_async, en app.py). Ese hilo hace
"except Exception: return" -- si la consulta de fondo falla por lo que sea
(timeout, RESOURCE_SEMAPHORE de SQL Express, una columna que no existe en
esta base), el endpoint devuelve 202 "calentando" PARA SIEMPRE, sin ningun
rastro del error real. Este script corre las mismas 9 consultas en este
proceso, con las excepciones propagadas, para ver cual de las 9 es la que
en realidad esta fallando y por que.

Correr EN SERVER-DELPRO, parado en la carpeta del proyecto, con el MISMO
interprete que esta sirviendo el puerto 5310:

    cd C:\\ruta\\del\\proyecto
    <interprete> diagnostico_salud.py

El interprete correcto se averigua asi (PowerShell):

    Get-NetTCPConnection -LocalPort 5310 -State Listen | Select-Object OwningProcess
    Get-Process -Id <ese PID> | Select-Object Path

SOLO LECTURA: no escribe en la base, no manda alertas, no toca configuracion.
"""
import sys
import time
import traceback

print("=" * 70)
print("ENTORNO")
print("=" * 70)
print(f"  python      {sys.version.split()[0]}")
print(f"  ejecutable  {sys.executable}")

TAMBO = sys.argv[1] if len(sys.argv) > 1 else "ponderosa"
print(f"  tambo: {TAMBO}")

print("\n" + "=" * 70)
print("CONEXION A LA BASE")
print("=" * 70)
try:
    import db
    d = db.run_query("SELECT TOP 1 GETDATE() AS ahora", tambo=TAMBO, max_rows=5)
    print(f"  OK -> {d['rows']}")
except Exception:
    print("  FALLA:")
    traceback.print_exc()

print("\n" + "=" * 70)
print("LAS 9 SECCIONES DE LA PANTALLA (ver cargarSalud() en index.html)")
print("=" * 70)
try:
    import app as A
except Exception:
    print("  no se pudo importar app.py:")
    traceback.print_exc()
    raise SystemExit(1)

# Que las excepciones lleguen acá en vez de convertirse en el HTML de error
# de Flask, o -peor- quedar calladas adentro del hilo de _refresh_async.
A.app.config["PROPAGATE_EXCEPTIONS"] = True

cli = A.app.test_client()
with cli.session_transaction() as s:
    s["usuario"] = "diagnostico"
    s["rol"] = "admin"

# Mismas 9 URLs que SECCIONES en templates/index.html (cargarSalud), en el
# mismo orden -- si agregan/sacan una sección ahí, actualizar acá también.
CASOS = [
    ("Atención (clásico)", f"/api/salud/atencion?tambo={TAMBO}"),
    ("Atención (experimental)", f"/api/salud/atencion_v2?tambo={TAMBO}"),
    ("RCS por rodeo", f"/api/salud/rcs_grupo?tambo={TAMBO}"),
    ("Vacas con RCS alto", f"/api/salud/rcs_vacas?tambo={TAMBO}"),
    ("Condición corporal (BCS)", f"/api/salud/bcs_vacas?tambo={TAMBO}"),
    ("Conductividad", f"/api/salud/conductividad?tambo={TAMBO}"),
    ("Producción por rodeo", f"/api/salud/produccion_rodeo?tambo={TAMBO}"),
    ("Problemas podales: estado cámara", f"/api/podal/estado?tambo={TAMBO}"),
    ("Problemas podales: vacas", f"/api/podal/vacas?tambo={TAMBO}"),
]

# --- 1) Contra el servidor QUE ESTA CORRIENDO ------------------------------
# El cache es por proceso: lo que importa es en qué termina ESE proceso, que
# es el que de verdad sirve la pantalla.
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
            for intento in range(6):
                with urllib.request.urlopen(pedido, timeout=60) as resp:
                    cuerpo = resp.read().decode("utf-8", "replace")
                    codigo, tipo = resp.status, resp.headers.get("Content-Type")
                if codigo != 202:
                    break
                if intento == 0:
                    print("    202: calentando, esperando un poco...")
                time.sleep(8)
            print(f"    HTTP {codigo}   ct={tipo}")
            if codigo == 202:
                print("    SIGUE EN 202 -- la consulta de fondo no termina o")
                print("    falló en silencio (ver parte 2, más abajo, para el motivo).")
            print(f"    body[:300]: {cuerpo[:300]}")
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", "replace")
            print(f"    HTTP {e.code}   ct={e.headers.get('Content-Type')}")
            print(f"    body[:500]: {cuerpo[:500]}")
        except Exception as e:  # noqa: BLE001
            print(f"    no respondió: {type(e).__name__}: {e}")
except Exception:
    print("  no se pudo probar el servidor vivo:")
    traceback.print_exc()

# --- 2) En ESTE proceso, esperando el calentamiento ------------------------
# Acá se ve el traceback real: _servir_cacheado() del endpoint sigue
# devolviendo 202 mientras el hilo de _refresh_async no termine, así que hay
# que esperarlo Y DESPUÉS correr la consulta de nuevo A MANO (sin el
# except Exception que la esconde) para ver el motivo real si falló.
print("\n\n### 2) EN ESTE PROCESO, con el traceback completo de cada consulta")
for nombre, url in CASOS:
    print(f"\n--- {nombre}\n    {url}")
    try:
        r = None
        for intento in range(18):
            r = cli.get(url)
            if r.status_code != 202:
                break
            if intento == 0:
                print("    202: calentando el caché, esperando (hasta ~3 min)...")
            time.sleep(10)
        ct = r.headers.get("Content-Type", "")
        cuerpo = r.get_data(as_text=True)
        print(f"    HTTP {r.status_code}   ct={ct}")
        if r.status_code == 202:
            print("    SIGUE EN 202 tras esperar -- la consulta de fondo nunca")
            print("    terminó. Corriendo el SQL directo para ver el error real:")
    except Exception:
        print("    EXCEPCION pidiendo el endpoint -- ESTE ES EL ERROR QUE BUSCAMOS:")
        traceback.print_exc()
        continue
    print(f"    body[:400]: {cuerpo[:400]}")

# --- 3) El SQL crudo de cada consulta, SIN el except que lo esconde --------
# _refresh_async hace "except Exception: return" -- acá se arma el MISMO
# texto SQL que arma cada endpoint (ver salud.py y las funciones de arriba
# en app.py) y se corre directo con db.run_query, con el traceback completo.
# Esto es lo que de verdad contesta "por qué" cuando la parte 2 queda en 202.
print("\n\n### 3) SQL crudo de cada consulta DDM (sin el except que lo esconde)")
try:
    import salas
    import salud
    import tambos

    grupos_sql = salas.de(TAMBO).sql_grupos()
    con_alarmas = tambos.tipo_sala(TAMBO) == "rotativa"
    con_bcs = A._tiene_bcs_de(TAMBO)

    CASOS_SQL = [
        ("Atención (clásico)", salud.sql_atencion_datos(grupos_sql)),
        ("Atención (experimental)", salud.sql_atencion_v2(grupos_sql, con_alarmas, con_bcs)),
        ("RCS por rodeo", salud.sql_rcs_por_grupo(grupos_sql)),
        ("Vacas con RCS alto", salud.sql_rcs_vacas(grupos_sql)),
        ("Condición corporal (BCS)", salud.sql_bcs_vacas(grupos_sql)),
        ("Conductividad", salud.sql_conductividad_rebanio(grupos_sql)),
        ("Producción por rodeo", salud.sql_produccion_por_rodeo(grupos_sql)),
    ]
    for nombre, sql in CASOS_SQL:
        print(f"\n--- {nombre}")
        t0 = time.time()
        try:
            d = db.run_query(sql, tambo=TAMBO)
            print(f"    OK en {time.time() - t0:.1f}s -> {len(d['rows'])} fila(s)")
        except Exception:
            print(f"    FALLÓ en {time.time() - t0:.1f}s -- ESTE ES EL ERROR QUE BUSCAMOS:")
            traceback.print_exc()
except Exception:
    print("  no se pudo armar/correr el SQL crudo:")
    traceback.print_exc()

print("\n" + "=" * 70)
print("Pegale esta salida COMPLETA a Claude.")
print("=" * 70)
