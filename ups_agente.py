# -*- coding: utf-8 -*-
"""Agente de la UPS: corre en una PC DEL TAMBO, lee la tarjeta de red de la UPS por SNMP y EMPUJA las lecturas a LactIA.

Por qué existe: la tarjeta está en la red del tambo, sin IP pública y sin puertos abiertos. Este agente nunca recibe
conexiones: solo hace dos cosas hacia afuera —leer la tarjeta (red local, UDP 161) y mandar por HTTPS (saliente, como
navegar) a `POST /api/ups/ingesta` de LactIA—. Si se corta internet, las lecturas quedan en una cola local (`ups_cola.db`)
y se mandan en orden cuando vuelve; reiniciar el agente o la PC no las pierde.

Es autónomo a propósito: usa solo la biblioteca estándar de Python y los dos archivos hermanos `snmp_min.py` y
`ups_snmp.py`. Para instalarlo en la PC del tambo alcanza con copiar esos TRES archivos (más `iniciar_ups_agente.bat`).

Uso (la clave SNMP y el token van por variables de entorno, NUNCA por la línea de comandos ni en archivos del proyecto):

    set UPS_SNMP_COMMUNITY=public            (la «community» de lectura de la tarjeta)
    set UPS_INGESTA_TOKEN=...                (el mismo que tiene LactIA; ver el panel de despliegue)
    python ups_agente.py 192.168.1.50 --tambo ponderosa --url https://lactia.ejemplo.com

    python ups_agente.py 192.168.1.50 --sin-enviar --una-vez      # prueba: lee la UPS y muestra, sin mandar nada
"""
import argparse
import datetime
import ipaddress
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snmp_min  # noqa: E402
import ups_snmp  # noqa: E402

RUTA_COLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ups_cola.db")
RUTA_INGESTA = "/api/ups/ingesta"
_FORMATO = "%Y-%m-%dT%H:%M:%SZ"


class EnvioError(Exception):
    """No se pudo entregar un lote a LactIA. El mensaje nunca lleva el token."""


def validar_url(url):
    """https en general; http solo dentro de la red local (localhost, IP privada o nombre de PC sin puntos): el token
    viaja en cada pedido y por internet abierto no puede ir sin cifrar."""
    p = urllib.parse.urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("La dirección de LactIA tiene que empezar con https:// (o http:// dentro de la red del tambo).")
    if p.scheme == "https":
        return url
    host = p.hostname
    try:
        ip = ipaddress.ip_address(host)
        local = ip.is_private or ip.is_loopback
    except ValueError:
        local = host == "localhost" or "." not in host
    if not local:
        raise ValueError("Por internet la dirección tiene que ser https:// (el token viaja en cada pedido).")
    return url


class enviar_http:
    """Entrega un lote de lecturas a LactIA. Callable: `enviar(lote)`. Lanza `EnvioError` si no se pudo."""

    def __init__(self, url, token, tambo, timeout=15):
        self.url = validar_url(url).rstrip("/") + RUTA_INGESTA
        self._token, self.tambo, self.timeout = token, tambo, timeout

    def __repr__(self):                               # nunca muestra el token
        return f"enviar_http({self.url}, tambo={self.tambo})"

    def __call__(self, lote):
        cuerpo = json.dumps({"tambo": self.tambo, "lecturas": lote}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=cuerpo, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self._token}",
            "User-Agent": "LactIA-UPS-Agente/1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            pista = " (revisá que el token sea el mismo que tiene LactIA)" if exc.code in (401, 403) else ""
            raise EnvioError(f"LactIA contestó {exc.code}{pista}.") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            motivo = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            raise EnvioError(f"No se pudo conectar con LactIA ({motivo.__class__.__name__}).") from None


def resumen(ups):
    """Una línea para la consola con lo esencial."""
    lineas = ((ups.get("entrada") or {}).get("lineas") or [])
    v = "/".join("—" if l.get("tension_v") is None else f"{l['tension_v']:g}" for l in lineas) or "—"
    b = ups.get("bateria") or {}
    carga = "—" if b.get("carga_pct") is None else f"{b['carga_pct']:g}"
    mins = "" if b.get("minutos_restantes") is None else f" ({b['minutos_restantes']:g} min)"
    amp = (ups.get("salida") or {}).get("corriente_total_a")
    return (("EN BATERÍA" if ups.get("en_bateria") else "en red") + f" · entrada {v} V"
            + ("" if amp is None else f" · salida {amp:g} A") + f" · batería {carga} %{mins}")


class Agente:
    """Un ciclo = leer la UPS, encolar la lectura y vaciar la cola hacia LactIA."""

    def __init__(self, leer, enviar, ruta_cola=RUTA_COLA, ahora=None, max_cola=20000, lote=100):
        self._leer, self._enviar = leer, enviar
        self.ruta, self.max_cola, self.lote = ruta_cola, max_cola, lote
        self._ahora = ahora or (lambda: datetime.datetime.now(datetime.timezone.utc))
        con = self._con()
        con.execute("CREATE TABLE IF NOT EXISTS cola (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)")
        con.commit()
        con.close()

    def _con(self):
        return sqlite3.connect(self.ruta, timeout=15)

    def _lectura(self):
        ts = self._ahora().astimezone(datetime.timezone.utc).strftime(_FORMATO)
        try:
            ups = self._leer()
        except snmp_min.SnmpError as exc:
            return {"ts": ts, "error": str(exc)[:300]}
        except Exception as exc:  # noqa: BLE001 — el agente tiene que seguir vivo pase lo que pase
            return {"ts": ts, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
        if not ups.get("disponible"):
            return {"ts": ts, "error": "La tarjeta contestó pero no expone la UPS-MIB estándar (RFC 1628): "
                                       "hay que leer la MIB propia del fabricante."}
        return {"ts": ts, "ups": ups}

    def _pendientes(self, con):
        return con.execute("SELECT COUNT(*) FROM cola").fetchone()[0]

    def ciclo(self):
        lectura = self._lectura()
        con = self._con()
        try:
            con.execute("INSERT INTO cola (payload) VALUES (?)", (json.dumps(lectura, ensure_ascii=False),))
            exceso = self._pendientes(con) - self.max_cola
            if exceso > 0:                                    # sin internet por días: se pierden las MÁS VIEJAS
                con.execute("DELETE FROM cola WHERE id IN (SELECT id FROM cola ORDER BY id LIMIT ?)", (exceso,))
            con.commit()
            enviadas, error = 0, None
            while True:
                filas = con.execute("SELECT id, payload FROM cola ORDER BY id LIMIT ?", (self.lote,)).fetchall()
                if not filas:
                    break
                try:
                    self._enviar([json.loads(p) for _, p in filas])
                except EnvioError as exc:
                    error = str(exc)
                    break
                con.execute(f"DELETE FROM cola WHERE id IN ({','.join('?' * len(filas))})", [i for i, _ in filas])
                con.commit()
                enviadas += len(filas)
            return {"lectura": lectura, "enviadas": enviadas, "pendientes": self._pendientes(con),
                    "error_envio": error}
        finally:
            con.close()


class Lector:
    """Habla con la tarjeta. Prueba SNMP v2c y después v1 (muchas tarjetas viejas solo hablan v1) y recuerda cuál anduvo."""

    def __init__(self, host, community, puerto=snmp_min.PUERTO_SNMP, version="auto"):
        self.host, self.puerto, self._community = host, puerto, community
        self._versiones = {"1": [0], "2c": [1], "auto": [1, 0]}[version]
        self._cliente = None

    def __call__(self):
        if self._cliente is None:
            ultimo = None
            for v in self._versiones:
                c = snmp_min.Cliente(self.host, puerto=self.puerto, community=self._community, version=v,
                                     timeout=2.5, reintentos=2)
                try:
                    c.get(["1.3.6.1.2.1.1.1.0"])
                    self._cliente = c
                    break
                except snmp_min.SnmpError as exc:
                    ultimo = exc
            if self._cliente is None:
                raise ultimo
        try:
            return ups_snmp.leer_rfc1628(self._cliente)
        except snmp_min.SnmpError:
            self._cliente = None                              # que la próxima vez renegocie la versión
            raise


def _hora_local():
    return datetime.datetime.now().strftime("%H:%M:%S")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lee la UPS por SNMP y manda las lecturas a LactIA (solo salida, sin puertos).")
    ap.add_argument("host", help="IP de la tarjeta de red de la UPS (en la red del tambo)")
    ap.add_argument("--tambo", required=False, help="id del tambo en LactIA (el mismo que usa la app, por ejemplo ponderosa)")
    ap.add_argument("--url", default=os.environ.get("LACTIA_URL"), help="dirección de LactIA (o variable LACTIA_URL)")
    ap.add_argument("--intervalo", type=int, default=30, help="segundos entre lecturas (por defecto 30)")
    ap.add_argument("--version", choices=["auto", "1", "2c"], default="auto")
    ap.add_argument("--puerto", type=int, default=snmp_min.PUERTO_SNMP)
    ap.add_argument("--una-vez", action="store_true", help="un solo ciclo y termina (para probar)")
    ap.add_argument("--sin-enviar", action="store_true", help="solo lee y muestra: no manda nada (para probar la tarjeta)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):            # una consola de Windows vieja no sabe de «→»: que no se caiga por eso
        sys.stdout.reconfigure(errors="replace")

    community = os.environ.get("UPS_SNMP_COMMUNITY", "public")
    token = os.environ.get("UPS_INGESTA_TOKEN", "")
    if a.sin_enviar:
        def enviar(lote):
            pass
    else:
        faltan = [n for n, v in (("--tambo", a.tambo), ("--url (o LACTIA_URL)", a.url),
                                 ("la variable UPS_INGESTA_TOKEN", token)) if not v]
        if faltan:
            sys.exit("Falta: " + ", ".join(faltan) + ". Para probar solo la lectura de la UPS usá --sin-enviar.")
        try:
            enviar = enviar_http(a.url, token, a.tambo)
        except ValueError as exc:
            sys.exit(str(exc))
    ruta_cola = RUTA_COLA
    if a.sin_enviar:                       # la prueba no deja nada pendiente ni toca la cola real
        ruta_cola = os.path.join(tempfile.gettempdir(), "ups_agente_prueba.db")
        if os.path.exists(ruta_cola):
            os.remove(ruta_cola)
    agente = Agente(Lector(a.host, community, a.puerto, a.version), enviar, ruta_cola=ruta_cola)
    print(f"Agente de la UPS: {a.host} cada {a.intervalo} s" + ("" if a.sin_enviar else f" → {enviar.url}")
          + ". Ctrl+C para cortar.", flush=True)
    while True:
        r = agente.ciclo()
        l = r["lectura"]
        estado = resumen(l["ups"]) if "ups" in l else "SIN LECTURA: " + l["error"]
        cola = "" if a.sin_enviar else (f" · enviadas {r['enviadas']}" if not r["pendientes"]
                                        else f" · en cola {r['pendientes']}")
        falla = f"\n    No se pudo mandar a LactIA: {r['error_envio']}" if r["error_envio"] else ""
        print(f"{_hora_local()} · {estado}{cola}{falla}", flush=True)
        if a.una_vez:
            return
        time.sleep(a.intervalo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAgente detenido.")
        sys.exit(130)        # el .bat distingue «lo corté yo» de «se cayó»: solo en el segundo caso reintenta
