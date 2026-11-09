# -*- coding: utf-8 -*-
"""Mide, SOLO LECTURA, qué expone por SNMP la tarjeta de red de la UPS del tambo.

Es el primer paso del monitoreo de la UPS: antes de armar pantallas hay que saber qué habla ESA tarjeta (la MIB estándar
de las UPS —RFC 1628— o solo la propia del fabricante) y si sus escalas son las del estándar. No escribe nada en la UPS
(el cliente SNMP de la app solo sabe leer) ni en la base.

Se corre en una PC de la MISMA red que la UPS (por ejemplo SERVER-DELPRO), parado en la carpeta del proyecto:

    python medir_ups.py 192.168.1.50
    python medir_ups.py 192.168.1.50 --community miClave --version 2c

La clave de lectura SNMP («community», por defecto «public») es una credencial: mejor pasarla por la variable de entorno
UPS_SNMP_COMMUNITY que por la línea de comandos. El informe NUNCA la incluye.

Deja un archivo `medicion_ups_<ip>_<fecha>.txt` con todo lo que se leyó (no se sube al repo). Mandámelo, y con la pantalla web
de la tarjeta abierta anotá lo que muestra AHORA (tensión de cada fase, corriente, carga %, minutos de autonomía): sirve para
comprobar que las escalas coinciden.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snmp_min  # noqa: E402
import ups_snmp  # noqa: E402

OID_SYSDESCR, OID_SYSOBJECTID, OID_SYSUPTIME, OID_SYSNAME = ("1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0",
                                                            "1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.1.5.0")


def _fmt(v):
    return "—" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def _linea(oid, v):
    if isinstance(v, bytes):
        v = "0x" + v.hex()
    return f"  {oid} = {v}"


def informe(cliente, host: str, empresa_max: int = 1500, ahora: datetime.datetime = None) -> str:
    """El informe completo como texto. Lanza `SnmpError` si la tarjeta no responde ni a sysDescr."""
    ahora = ahora or datetime.datetime.now()
    ident = cliente.get([OID_SYSDESCR, OID_SYSOBJECTID, OID_SYSUPTIME, OID_SYSNAME])
    if all(v is None for v in ident.values()):
        raise snmp_min.SnmpError("La tarjeta respondió pero no tiene información de sistema (sysDescr).")
    sysoid = ident[OID_SYSOBJECTID]
    fabricante = ups_snmp.fabricante_de(sysoid)
    uptime = ident[OID_SYSUPTIME]
    out = [f"MEDICIÓN DE LA UPS por SNMP · {host} · {ahora:%d/%m/%Y %H:%M}",
           f"(protocolo: SNMP {'v1' if cliente.version == 0 else 'v2c'})", "", "=" * 78, "1. IDENTIDAD DE LA TARJETA",
           "=" * 78,
           f"  sysDescr    : {ident[OID_SYSDESCR]}", f"  sysName     : {ident[OID_SYSNAME]}",
           f"  sysObjectID : {sysoid}",
           f"  fabricante  : {fabricante or 'no identificado por su número de empresa'}",
           f"  encendida   : {uptime / 8640000:.1f} días desde el último reinicio de la tarjeta"
           if isinstance(uptime, int) else "  encendida   : —", ""]

    out += ["=" * 78, "2. UPS-MIB ESTÁNDAR (RFC 1628) — INTERPRETADA", "=" * 78]
    crudos = list(cliente.walk(ups_snmp.RAIZ, maximo=1500))
    d = ups_snmp.interpretar_rfc1628(dict(crudos))
    if not d["disponible"]:
        out += ["  La tarjeta NO expone la MIB estándar de UPS (1.3.6.1.2.1.33).",
                "  El monitoreo tendría que hacerse con la MIB propia del fabricante (sección 4).", ""]
    else:
        i, b, e, s = d["identidad"], d["bateria"], d["entrada"], d["salida"]
        out += [f"  UPS         : {_fmt(i['fabricante'])} · {_fmt(i['modelo'])} · nombre {_fmt(i['nombre'])}",
                f"  La salida está alimentada por: {s['fuente'].upper()}   ({'EN BATERÍA' if d['en_bateria'] else 'en red'})",
                f"  Batería     : {b['estado']} · carga {_fmt(b['carga_pct'])} % · autonomía {_fmt(b['minutos_restantes'])} min"
                f" · tensión {_fmt(b['tension_v'])} V · temp {_fmt(b['temperatura_c'])} °C"
                f" · segundos en batería {_fmt(b['segundos_en_bateria'])}",
                "", f"  ENTRADA ({_fmt(e['n_lineas'])} fase/s; cortes de red contados: {_fmt(e['lineas_malas'])})",
                "    fase   tensión V   corriente A   frecuencia Hz   potencia W"]
        out += [f"    {l['n']:>4}   {_fmt(l['tension_v']):>9}   {_fmt(l['corriente_a']):>11}   {_fmt(l['frecuencia_hz']):>13}"
                f"   {_fmt(l['potencia_w']):>10}" for l in e["lineas"]] or ["    (sin datos de entrada)"]
        out += ["", f"  SALIDA (frecuencia {_fmt(s['frecuencia_hz'])} Hz · corriente total {_fmt(s['corriente_total_a'])} A"
                    f" · potencia total {_fmt(s['potencia_total_w'])} W)",
                "    fase   tensión V   corriente A   potencia W   carga %"]
        out += [f"    {l['n']:>4}   {_fmt(l['tension_v']):>9}   {_fmt(l['corriente_a']):>11}   {_fmt(l['potencia_w']):>10}"
                f"   {_fmt(l['carga_pct']):>7}" for l in s["lineas"]] or ["    (sin datos de salida)"]
        out += ["", f"  Alarmas activas: {_fmt(d['alarmas_presentes'])}"]
        out += [f"    - {a['nombre']}" for a in d["alarmas"]]
        c = d["config"]
        out += ["", f"  Configuración: entrada {_fmt(c['tension_entrada_v'])} V · salida {_fmt(c['tension_salida_v'])} V · "
                    f"{_fmt(c['va_salida'])} VA · {_fmt(c['potencia_salida_w'])} W", ""]
        out += ["=" * 78, "3. VALORES CRUDOS DE ESA MIB (para verificar las escalas contra la pantalla web)", "=" * 78,
                "  Según el estándar: tensión en V, corriente en décimas de A, frecuencia en décimas de Hz.",
                "  Si la pantalla web de la tarjeta muestra otra cosa, la escala de esa tarjeta es distinta."]
        out += [_linea(o, v) for o, v in crudos] + [""]

    raiz = ups_snmp.empresa_de(sysoid)
    out += ["=" * 78, "4. MIB PROPIA DEL FABRICANTE" + (f" ({raiz})" if raiz else ""), "=" * 78]
    if raiz:
        propios = list(cliente.walk(raiz, maximo=empresa_max))
        out += [f"  {len(propios)} valores" + (f" (cortado en {empresa_max}; subí --empresa-max si hace falta)"
                                               if len(propios) >= empresa_max else "")]
        out += [_linea(o, v) for o, v in propios]
    else:
        out += ["  El sysObjectID no es de una empresa conocida: no se recorre una MIB propia."]
    return "\n".join(out) + "\n"


def _cliente_que_responde(host, puerto, community, version):
    """v2c primero y v1 después (muchas tarjetas viejas solo hablan v1), salvo que la persona fije la versión."""
    versiones = {"1": [0], "2c": [1], "auto": [1, 0]}[version]
    ultimo = None
    for v in versiones:
        c = snmp_min.Cliente(host, puerto=puerto, community=community, version=v, timeout=2.5, reintentos=2)
        try:
            c.get([OID_SYSDESCR])
            return c
        except snmp_min.SnmpError as exc:
            ultimo = exc
    raise ultimo


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mide por SNMP qué expone la tarjeta de red de la UPS (solo lectura).")
    ap.add_argument("host", help="IP de la tarjeta de red de la UPS")
    ap.add_argument("--community", default=os.environ.get("UPS_SNMP_COMMUNITY", "public"),
                    help="clave de lectura SNMP (mejor por la variable UPS_SNMP_COMMUNITY); por defecto «public»")
    ap.add_argument("--version", choices=["auto", "1", "2c"], default="auto")
    ap.add_argument("--puerto", type=int, default=snmp_min.PUERTO_SNMP)
    ap.add_argument("--empresa-max", type=int, default=1500, help="tope de valores de la MIB propia (por defecto 1500)")
    ap.add_argument("--salida", help="archivo de salida (por defecto medicion_ups_<ip>_<fecha>.txt)")
    a = ap.parse_args(argv)
    try:
        cliente = _cliente_que_responde(a.host, a.puerto, a.community, a.version)
        texto = informe(cliente, a.host, a.empresa_max)
    except snmp_min.SnmpError as exc:
        sys.exit(f"No se pudo medir la UPS: {exc}")
    ruta = a.salida or f"medicion_ups_{a.host.replace(':', '_')}_{datetime.datetime.now():%Y%m%d_%H%M}.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)
    print(f"Guardado en {ruta}")


if __name__ == "__main__":
    main()
