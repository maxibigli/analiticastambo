# -*- coding: utf-8 -*-
"""Precalienta los cachés de las secciones pesadas.

Las secciones de análisis guardan su resultado en un caché en memoria del
proceso del servidor, con vencimientos de 30 a 60 minutos. El problema es la
PRIMERA carga después de que el caché vence: el usuario que entra primero se
come toda la espera. La peor es Tasa de Preñez, que tarda unos 75 segundos.

Este script pide esas secciones desde afuera para que el caché ya esté lleno
cuando alguien entre. Se corre con el Programador de tareas de Windows.

    precalentar.bat          → una pasada
    precalentar.bat --loop   → una pasada cada 25 minutos, sin parar

CÓMO SE AUTENTICA. Los endpoints piden rol admin. En vez de agregar una puerta
de atrás, el script firma su propia cookie de sesión con la misma clave que usa
la aplicación (`secret_key.txt`). Solo funciona corriendo en la misma máquina,
que es donde tiene que correr.

EL DETALLE QUE HACE QUE ESTO SIRVA O NO: la clave del caché incluye los
parámetros de la consulta, así que el script tiene que pedir EXACTAMENTE lo
mismo que pide la pantalla. Si la pantalla manda `rebano=1` y el script no
manda nada, el servidor usa su valor por defecto —que es la lista `[1]`— y la
clave queda `...:[1]` contra `...:1`: se calienta un caché que nadie va a usar.
Por eso el script arranca preguntando cuál es el rebaño del tambo y lo manda
igual que la pantalla.
"""
import argparse
import datetime
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("LACTIA_URL", "http://127.0.0.1:" + os.environ.get("DELPRO_PORT", "5310"))
TAMBO = os.environ.get("LACTIA_TAMBO", "ponderosa")

# Cuánto esperar a que termine cada sección antes de darla por perdida.
ESPERA_MAX_S = 240
# Cada cuánto repetir en modo --loop. Los cachés más cortos duran 30 minutos.
INTERVALO_LOOP_S = 25 * 60


def _cookie_sesion() -> str:
    """Cookie de sesión firmada con la clave de la aplicación."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from flask.sessions import SecureCookieSessionInterface

    from app import app
    serializador = SecureCookieSessionInterface().get_signing_serializer(app)
    if serializador is None:
        raise RuntimeError("La aplicación no tiene secret_key: no se puede firmar la sesión.")
    return "session=" + serializador.dumps({"rol": "admin", "usuario": "precalentado"})


def _pedir(ruta: str, cookie: str, espera_max: int = ESPERA_MAX_S):
    """Pide un endpoint y espera a que deje de responder 202 (calentando).

    Devuelve (estado, segundos). El estado es 'listo', 'timeout' o 'error: ...'.
    """
    t0 = time.time()
    while time.time() - t0 < espera_max:
        pedido = urllib.request.Request(BASE + ruta, headers={"Cookie": cookie})
        try:
            with urllib.request.urlopen(pedido, timeout=espera_max) as r:
                if r.status == 200:
                    return "listo", time.time() - t0
        except urllib.error.HTTPError as e:
            if e.code == 202:          # todavía calculando
                time.sleep(3)
                continue
            return f"error HTTP {e.code}", time.time() - t0
        except Exception as e:         # noqa: BLE001
            return f"error: {e}", time.time() - t0
        time.sleep(3)
    return "timeout", time.time() - t0


def _rebano_del_tambo(cookie: str):
    """El rebaño que usa la pantalla por defecto. Ver la nota del encabezado."""
    import json
    pedido = urllib.request.Request(
        f"{BASE}/api/reproduccion/rebanos?tambo={TAMBO}", headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(pedido, timeout=60) as r:
            for x in json.load(r).get("rebanos", []):
                if x.get("es_el_tambo"):
                    return x["herd"]
    except Exception:  # noqa: BLE001
        pass
    return None


def rutas(hoy: datetime.date, herd) -> list:
    """Las secciones a precalentar, con los MISMOS parámetros que manda la
    pantalla. Ordenadas de más lenta a más rápida, para que lo que más molesta
    quede resuelto primero."""
    def d(dias):
        return (hoy - datetime.timedelta(days=dias)).isoformat()

    h = hoy.isoformat()
    reb = f"&rebano={herd}" if herd is not None else ""
    anio = hoy.year
    mes = hoy.strftime("%Y-%m")

    def mes_mas(n):
        t = hoy.year * 12 + hoy.month - 1 + n
        return f"{t // 12:04d}-{t % 12 + 1:02d}"

    return [
        ("Tasa de Preñez",
         f"/api/reproduccion/tasa_prenez?desde={d(365)}&hasta={h}&tipo=vaca{reb}&tambo={TAMBO}"),
        ("Análisis Reproductivo",
         f"/api/reproduccion/resultados?desde1={anio-1}-01-01&hasta1={anio-1}-12-31"
         f"&desde2={anio}-01-01&hasta2={h}&tambo={TAMBO}"),
        ("Performance · peak",
         f"/api/reproduccion/performance?reporte=peak&desde={d(365)}&hasta={h}{reb}&tambo={TAMBO}"),
        ("Performance · distribución",
         f"/api/reproduccion/performance?reporte=distribucion&desde={d(365)}&hasta={h}{reb}&tambo={TAMBO}"),
        ("Indicadores de Preñez",
         f"/api/reproduccion/preneces?desde={d(365)}&hasta={h}&tipo=vacas{reb}&tambo={TAMBO}"),
        ("Análisis de Gestación",
         f"/api/reproduccion/gestacion?desde={d(365)}&hasta={h}{reb}&tambo={TAMBO}"),
        ("Partos y Secados",
         f"/api/reproduccion/partos_secados?categoria=todas{reb}&meses=9&tambo={TAMBO}"),
        ("Proyección de Rebaños",
         f"/api/proyeccion/rebanos?desde={mes_mas(-5)}&hasta={mes_mas(11)}&tambo={TAMBO}"),
        ("Flujos de Ordeño",
         f"/api/flujos/analisis?desde={d(29)}&hasta={h}&tambo={TAMBO}"),
    ]


def una_pasada(cookie: str) -> int:
    hoy = datetime.date.today()
    herd = _rebano_del_tambo(cookie)
    print(f"[{datetime.datetime.now():%H:%M:%S}] precalentando {BASE} "
          f"(tambo={TAMBO}, rebaño={herd})")
    if herd is None:
        print("  ⚠ no se pudo leer el rebaño del tambo: el caché puede quedar "
              "con una clave distinta a la que pide la pantalla.")
    fallas = 0
    for nombre, ruta in rutas(hoy, herd):
        estado, seg = _pedir(ruta, cookie)
        marca = "ok " if estado == "listo" else "FALLA"
        if estado != "listo":
            fallas += 1
        print(f"  {marca} {nombre:<28} {seg:6.1f} s   {'' if estado == 'listo' else estado}")
    return fallas


def main() -> int:
    ap = argparse.ArgumentParser(description="Precalienta los cachés de LactIA.")
    ap.add_argument("--loop", action="store_true",
                    help=f"repetir cada {INTERVALO_LOOP_S // 60} minutos")
    args = ap.parse_args()

    try:
        cookie = _cookie_sesion()
    except Exception as e:  # noqa: BLE001
        print("No se pudo firmar la sesión:", e)
        return 2

    while True:
        fallas = una_pasada(cookie)
        if not args.loop:
            return 1 if fallas else 0
        time.sleep(INTERVALO_LOOP_S)


if __name__ == "__main__":
    raise SystemExit(main())
