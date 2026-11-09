# -*- coding: utf-8 -*-
"""La UPS vista por su tarjeta de red: interpreta la UPS-MIB estándar (RFC 1628, mib-2 33) por SNMP.

Da, en unidades de uso (V, A, Hz, %, minutos): tensión, corriente, frecuencia, potencia y carga POR FASE de entrada y de
salida, el estado de la batería (carga, autonomía, segundos en batería), la fuente de la salida (red / batería / bypass) y
las alarmas activas. Los OID, las columnas de las tablas y las unidades salen del texto del RFC 1628 (verificado).

Límite que hay que tener presente: la mayoría de las tarjetas de red de UPS implementan esta MIB, pero NO todas, y algunas
se desvían de las unidades del estándar. Por eso `medir_ups.py` muestra los valores interpretados Y los crudos, para
compararlos con la pantalla web de la tarjeta antes de confiar en la escala, y por eso existe `fabricante_de` (si la tarjeta
solo habla su MIB propia, hay que sumar ese fabricante).

`interpretar_rfc1628` es una función pura sobre {oid: valor}: se prueba sin red. `leer_rfc1628(cliente)` hace el recorrido.
"""
import re

RAIZ = "1.3.6.1.2.1.33.1"

ESTADOS_BATERIA = {1: "desconocida", 2: "normal", 3: "baja", 4: "agotada"}
FUENTES_SALIDA = {1: "otra", 2: "ninguna", 3: "normal", 4: "bypass", 5: "bateria", 6: "booster", 7: "reductor"}
ALARMAS = {
    1: "batería defectuosa", 2: "funcionando con batería", 3: "batería baja", 4: "batería agotada",
    5: "temperatura fuera de rango", 6: "entrada fuera de tolerancia", 7: "salida fuera de tolerancia",
    8: "sobrecarga en la salida", 9: "en bypass", 10: "bypass fuera de tolerancia",
    11: "salida apagada por pedido", 12: "UPS apagada por pedido", 13: "falla del cargador", 14: "salida apagada",
    15: "sistema apagado", 16: "falla de ventilador", 17: "falla de fusible", 18: "falla general",
    19: "falló la prueba de diagnóstico", 20: "comunicaciones perdidas", 21: "esperando energía",
    22: "apagado pendiente", 23: "apagado inminente", 24: "prueba en curso",
}
_ALARMA_BASE = RAIZ + ".6.3."

# Número de empresa (iso.org.dod.internet.private.enterprises.N) del sysObjectID -> fabricante.
_EMPRESAS = {318: "APC (Schneider)", 534: "Eaton / Powerware", 705: "MGE (Eaton / Schneider)", 476: "Vertiv / Liebert",
             4555: "Socomec", 850: "Tripp Lite", 3808: "CyberPower"}


def empresa_de(sys_object_id):
    """'1.3.6.1.4.1.318.1.3.2.4' -> '1.3.6.1.4.1.318' (la raíz de la MIB propia del fabricante), o None."""
    m = re.match(r"^(1\.3\.6\.1\.4\.1\.\d+)", str(sys_object_id or ""))
    return m.group(1) if m else None


def fabricante_de(sys_object_id):
    raiz = empresa_de(sys_object_id)
    return _EMPRESAS.get(int(raiz.rsplit(".", 1)[1])) if raiz else None


def _num(v, factor=1.0):
    """Número en su unidad de uso, o None. Varias tarjetas ponen -1 donde no miden: eso NO es un valor."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return None
    return v * factor if factor != 1.0 else v


def _tabla(valores: dict, prefijo: str) -> dict:
    """{indice: {columna: valor}} de una tabla SNMP. El índice sale del final del OID: la columna del índice suele ser
    «not-accessible» y no viene en el recorrido, así que no se puede depender de ella."""
    patron = re.compile(r"^" + re.escape(prefijo) + r"\.(\d+)\.(\d+)$")
    filas = {}
    for oid, v in valores.items():
        m = patron.match(oid)
        if m:
            filas.setdefault(int(m.group(2)), {})[int(m.group(1))] = v
    return filas


def interpretar_rfc1628(valores: dict) -> dict:
    """De {oid: valor} (el recorrido de 1.3.6.1.2.1.33.1) al estado de la UPS. No toca la red."""
    def g(suf):
        return valores.get(f"{RAIZ}.{suf}")

    disponible = any(o.startswith(RAIZ + ".") for o in valores)
    ident = {"fabricante": g("1.1.0"), "modelo": g("1.2.0"), "version_sw": g("1.3.0"), "agente": g("1.4.0"),
             "nombre": g("1.5.0"), "equipos": g("1.6.0")}
    estado_bat = ESTADOS_BATERIA.get(g("2.1.0"), "desconocida")
    bateria = {"estado": estado_bat, "segundos_en_bateria": _num(g("2.2.0")), "minutos_restantes": _num(g("2.3.0")),
               "carga_pct": _num(g("2.4.0")), "tension_v": _num(g("2.5.0"), 0.1), "corriente_a": g("2.6.0") if isinstance(g("2.6.0"), (int, float)) else None,
               "temperatura_c": _num(g("2.7.0"))}
    if bateria["corriente_a"] is not None:
        bateria["corriente_a"] = bateria["corriente_a"] * 0.1       # puede ser negativa (descarga): no se descarta

    entrada = []
    for n, c in sorted(_tabla(valores, RAIZ + ".3.3.1").items()):
        entrada.append({"n": n, "frecuencia_hz": _num(c.get(2), 0.1), "tension_v": _num(c.get(3)),
                        "corriente_a": _num(c.get(4), 0.1), "potencia_w": _num(c.get(5))})
    salida_lineas = []
    for n, c in sorted(_tabla(valores, RAIZ + ".4.4.1").items()):
        salida_lineas.append({"n": n, "tension_v": _num(c.get(2)), "corriente_a": _num(c.get(3), 0.1),
                              "potencia_w": _num(c.get(4)), "carga_pct": _num(c.get(5))})
    corrientes = [l["corriente_a"] for l in salida_lineas if l["corriente_a"] is not None]
    potencias = [l["potencia_w"] for l in salida_lineas if l["potencia_w"] is not None]
    fuente = FUENTES_SALIDA.get(g("4.1.0"), "otra")

    alarmas = []
    for idx, c in sorted(_tabla(valores, RAIZ + ".6.2.1").items()):
        descr = str(c.get(2) or "")
        num = int(descr[len(_ALARMA_BASE):]) if descr.startswith(_ALARMA_BASE) and descr[len(_ALARMA_BASE):].isdigit() else None
        alarmas.append({"id": idx, "oid": descr, "nombre": ALARMAS.get(num, descr or "alarma sin descripción"),
                        "tipo": num})
    segundos = bateria["segundos_en_bateria"]
    return {
        "disponible": disponible,
        "identidad": ident,
        "bateria": bateria,
        "entrada": {"n_lineas": _num(g("3.2.0")), "lineas_malas": _num(g("3.1.0")), "lineas": entrada},
        "salida": {"fuente": fuente, "frecuencia_hz": _num(g("4.2.0"), 0.1), "n_lineas": _num(g("4.3.0")),
                   "lineas": salida_lineas,
                   "corriente_total_a": round(sum(corrientes), 1) if corrientes else None,
                   "potencia_total_w": sum(potencias) if potencias else None},
        "alarmas_presentes": _num(g("6.1.0")), "alarmas": alarmas,
        "config": {"tension_entrada_v": _num(g("9.1.0")), "tension_salida_v": _num(g("9.3.0")),
                   "va_salida": _num(g("9.5.0")), "potencia_salida_w": _num(g("9.6.0"))},
        # «en batería» = la salida la alimenta la batería o ya hay segundos acumulados o la batería está baja/agotada
        "en_bateria": bool(fuente == "bateria" or (segundos or 0) > 0 or estado_bat in ("baja", "agotada")),
    }


def leer_rfc1628(cliente, maximo: int = 1500) -> dict:
    """Recorre la UPS-MIB de la tarjeta y la interpreta. Lanza `snmp_min.SnmpError` si la tarjeta no responde."""
    return interpretar_rfc1628(dict(cliente.walk(RAIZ, maximo=maximo)))
