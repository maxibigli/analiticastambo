# -*- coding: utf-8 -*-
"""Cliente SNMP v1/v2c mínimo (GET, GETNEXT y recorrido de un subárbol), sin dependencias.

Para qué: leer la UPS del tambo por su tarjeta de red (tensión por fase, corriente, batería, alarmas). SNMP v1/v2c es un
paquete UDP por consulta; escribirlo acá evita sumar una librería al servidor (ninguna está instalada, y las de Python
cambiaron de API entre versiones). Está probado contra paquetes reales del estándar (tests/test_snmp_min.py).

Alcance (a propósito chico): GET y GETNEXT de OIDs, valores enteros/texto/OID/IP/contadores, v1 y v2c. NO hay SET (esto
solo LEE: no hay forma de que la app cambie algo en la UPS), ni v3 (usuario/clave/cifrado) ni traps. Si una tarjeta solo
habla v3, el error lo dice y hay que habilitar v1/v2c de solo lectura en la tarjeta, o ampliar este módulo.

La «community» es la clave de lectura de SNMP (por defecto casi siempre «public»). Es una credencial: nunca se escribe en
mensajes de error ni en el código.
"""
import os
import random
import socket
import time

PUERTO_SNMP = 161

_T_ENTERO, _T_TEXTO, _T_NULO, _T_OID, _T_SECUENCIA = 0x02, 0x04, 0x05, 0x06, 0x30
_T_IP, _T_CONTADOR32, _T_GAUGE32, _T_TICKS, _T_OPACO, _T_CONTADOR64 = 0x40, 0x41, 0x42, 0x43, 0x44, 0x46
_PDU = {"get": 0xA0, "next": 0xA1, "respuesta": 0xA2}
_PDU_INV = {v: k for k, v in _PDU.items()}


class SnmpError(Exception):
    """No se pudo hablar con el agente SNMP: el mensaje se le muestra a la persona."""


class _Marca:
    def __init__(self, nombre):
        self.nombre = nombre

    def __repr__(self):
        return self.nombre


NO_EXISTE = _Marca("noSuchObject")      # el agente no tiene ese OID
FIN_MIB = _Marca("endOfMibView")        # no hay nada más después


# --------------------------------------------------------------------------- BER: codificar
def _largo(n: int) -> bytes:
    if n < 128:
        return bytes([n])
    cuerpo = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(cuerpo)]) + cuerpo


def _tlv(tag: int, contenido: bytes) -> bytes:
    return bytes([tag]) + _largo(len(contenido)) + contenido


def _codificar_entero(n: int) -> bytes:
    largo = ((~n).bit_length() if n < 0 else n.bit_length()) // 8 + 1       # complemento a 2, el mínimo de bytes
    return _tlv(_T_ENTERO, n.to_bytes(largo, "big", signed=True))


def _codificar_oid(oid: str) -> bytes:
    try:
        arcos = [int(x) for x in str(oid).strip(".").split(".")]
    except ValueError:
        raise SnmpError(f"OID inválido: {oid!r}") from None
    if len(arcos) < 2 or arcos[0] > 2 or any(a < 0 for a in arcos):
        raise SnmpError(f"OID inválido: {oid!r}")
    sub = [40 * arcos[0] + arcos[1]] + arcos[2:]
    cuerpo = bytearray()
    for v in sub:
        trozo = [v & 0x7F]
        v >>= 7
        while v:
            trozo.append(0x80 | (v & 0x7F))
            v >>= 7
        cuerpo += bytes(reversed(trozo))
    return _tlv(_T_OID, bytes(cuerpo))


def _codificar_valor(v) -> bytes:
    if v is NO_EXISTE:
        return _tlv(0x80, b"")
    if v is FIN_MIB:
        return _tlv(0x82, b"")
    if v is None:
        return _tlv(_T_NULO, b"")
    if isinstance(v, bool) or isinstance(v, int):
        return _codificar_entero(int(v))
    if isinstance(v, (bytes, bytearray)):
        return _tlv(_T_TEXTO, bytes(v))
    return _tlv(_T_TEXTO, str(v).encode("utf-8"))


def _mensaje(version: int, community: str, tag_pdu: int, request_id: int, error: int, indice: int, varbinds) -> bytes:
    lista = b"".join(_tlv(_T_SECUENCIA, _codificar_oid(o) + _codificar_valor(v)) for o, v in varbinds)
    pdu = _codificar_entero(request_id) + _codificar_entero(error) + _codificar_entero(indice) + _tlv(_T_SECUENCIA, lista)
    return _tlv(_T_SECUENCIA, _codificar_entero(version) + _tlv(_T_TEXTO, community.encode("utf-8")) + _tlv(tag_pdu, pdu))


def armar_pedido(tipo: str, request_id: int, oids, community: str = "public", version: int = 1) -> bytes:
    """Paquete GET (`tipo="get"`) o GETNEXT (`"next"`). `version`: 0 = SNMPv1, 1 = SNMPv2c."""
    if isinstance(oids, str):
        oids = [oids]
    return _mensaje(version, community, _PDU[tipo], request_id, 0, 0, [(o, None) for o in oids])


def armar_respuesta(request_id: int, varbinds, community: str = "public", version: int = 1, error: int = 0,
                    indice: int = 0) -> bytes:
    """Paquete de respuesta (lo usa el agente falso de las pruebas)."""
    return _mensaje(version, community, _PDU["respuesta"], request_id, error, indice, varbinds)


# --------------------------------------------------------------------------- BER: decodificar
def _leer_tlv(buf: bytes, pos: int):
    """(tag, contenido, posición siguiente). Cualquier paquete cortado o con largos absurdos es SnmpError."""
    if pos + 2 > len(buf):
        raise SnmpError("respuesta SNMP cortada")
    tag = buf[pos]
    largo = buf[pos + 1]
    pos += 2
    if largo & 0x80:
        n = largo & 0x7F
        if n == 0 or n > 4 or pos + n > len(buf):
            raise SnmpError("respuesta SNMP con un largo inválido")
        largo = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    if pos + largo > len(buf):
        raise SnmpError("respuesta SNMP cortada")
    return tag, buf[pos:pos + largo], pos + largo


def _entero(datos: bytes, con_signo: bool = True) -> int:
    return int.from_bytes(datos, "big", signed=con_signo) if datos else 0


def _decodificar_oid(datos: bytes) -> str:
    if not datos:
        raise SnmpError("OID vacío en la respuesta")
    valores, actual = [], 0
    for b in datos:
        actual = (actual << 7) | (b & 0x7F)
        if not b & 0x80:
            valores.append(actual)
            actual = 0
    if datos[-1] & 0x80:
        raise SnmpError("OID cortado en la respuesta")
    primero = valores[0]
    a, b = (0, primero) if primero < 40 else (1, primero - 40) if primero < 80 else (2, primero - 80)
    return ".".join(str(x) for x in [a, b] + valores[1:])


def _decodificar_valor(tag: int, datos: bytes):
    if tag == _T_ENTERO:
        return _entero(datos)
    if tag in (_T_CONTADOR32, _T_GAUGE32, _T_TICKS, _T_CONTADOR64):
        return _entero(datos, con_signo=False)
    if tag == _T_TEXTO:
        try:
            texto = datos.decode("utf-8")
        except UnicodeDecodeError:
            texto = datos.decode("latin-1")
        # Lo que no es texto legible (una MAC, un valor binario) se devuelve como bytes, no como basura.
        return texto if all(c.isprintable() or c in "\r\n\t" for c in texto) else bytes(datos)
    if tag == _T_NULO:
        return None
    if tag == _T_OID:
        return _decodificar_oid(datos)
    if tag == _T_IP and len(datos) == 4:
        return ".".join(str(b) for b in datos)
    if tag in (0x80, 0x81):
        return NO_EXISTE
    if tag == 0x82:
        return FIN_MIB
    return bytes(datos)


def _leer_mensaje(buf: bytes) -> dict:
    tag, cuerpo, _ = _leer_tlv(buf, 0)
    if tag != _T_SECUENCIA:
        raise SnmpError("lo que respondió no es un paquete SNMP")
    t, v, pos = _leer_tlv(cuerpo, 0)
    if t != _T_ENTERO:
        raise SnmpError("paquete SNMP sin versión")
    version = _entero(v)
    t, comunidad, pos = _leer_tlv(cuerpo, pos)
    if t != _T_TEXTO:
        raise SnmpError("paquete SNMP sin community")
    if version == 3 or version not in (0, 1):
        raise SnmpError("la tarjeta respondió con SNMPv3, que este programa no soporta: habilitá SNMP v1/v2c de solo lectura")
    tag_pdu, pdu, _ = _leer_tlv(cuerpo, pos)
    campos, p = [], 0
    for _ in range(3):
        t, v, p = _leer_tlv(pdu, p)
        if t != _T_ENTERO:
            raise SnmpError("paquete SNMP mal formado")
        campos.append(_entero(v))
    t, lista, _ = _leer_tlv(pdu, p)
    if t != _T_SECUENCIA:
        raise SnmpError("paquete SNMP sin valores")
    valores, q = [], 0
    while q < len(lista):
        t, vb, q = _leer_tlv(lista, q)
        if t != _T_SECUENCIA:
            raise SnmpError("valor SNMP mal formado")
        t, o, r = _leer_tlv(vb, 0)
        if t != _T_OID:
            raise SnmpError("valor SNMP sin OID")
        tv, dv, _ = _leer_tlv(vb, r)
        valores.append((_decodificar_oid(o), _decodificar_valor(tv, dv)))
    return {"version": version, "community": comunidad.decode("utf-8", errors="replace"), "tipo": _PDU_INV.get(tag_pdu),
            "id": campos[0], "error": campos[1], "indice": campos[2], "valores": valores}


def leer_respuesta(buf: bytes) -> dict:
    return _leer_mensaje(buf)


def leer_pedido(buf: bytes) -> dict:
    m = _leer_mensaje(buf)
    return {"tipo": m["tipo"], "id": m["id"], "community": m["community"], "version": m["version"],
            "oids": [o for o, _ in m["valores"]]}


# --------------------------------------------------------------------------- cliente
def _tupla(oid: str):
    return tuple(int(x) for x in str(oid).strip(".").split("."))


class Cliente:
    """Un agente SNMP (la tarjeta de la UPS). `version`: 0 = v1, 1 = v2c (por defecto)."""

    def __init__(self, host: str, puerto: int = PUERTO_SNMP, community: str = "public", version: int = 1,
                 timeout: float = 2.0, reintentos: int = 2):
        self.host, self.puerto, self.version = host, int(puerto), version
        self._community = community
        self.timeout, self.reintentos = timeout, max(1, int(reintentos))

    def __repr__(self):                       # nunca muestra la community
        return f"Cliente({self.host}:{self.puerto}, v{'1' if self.version == 0 else '2c'})"

    def _pedir(self, tipo: str, oids) -> dict:
        rid = random.randint(1, 0x7FFFFFFF)
        paquete = armar_pedido(tipo, rid, oids, self._community, self.version)
        fin_mensaje = (f"La UPS no respondió ({self.host}:{self.puerto}). Revisá que la IP sea la de la tarjeta, que SNMP "
                       "esté habilitado en ella, que la community (clave de lectura) sea la correcta y que ningún firewall "
                       "bloquee el puerto UDP 161.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _ in range(self.reintentos):
                try:
                    s.sendto(paquete, (self.host, self.puerto))
                except OSError as exc:
                    raise SnmpError(f"No se pudo enviar a {self.host}:{self.puerto} ({exc.__class__.__name__}).") from None
                limite = time.monotonic() + self.timeout
                while True:
                    resto = limite - time.monotonic()
                    if resto <= 0:
                        break
                    s.settimeout(resto)
                    try:
                        datos, _ = s.recvfrom(65535)
                    except (socket.timeout, ConnectionResetError, OSError):
                        break
                    try:
                        r = leer_respuesta(datos)
                    except SnmpError:
                        continue              # paquete ajeno o dañado: se sigue esperando el verdadero
                    if r["id"] == rid:
                        return r
        raise SnmpError(fin_mensaje)

    def get(self, oids) -> dict:
        """{oid: valor}. Un OID que el agente no tiene vale None (no es un error: cada tarjeta expone lo suyo)."""
        oids = [oids] if isinstance(oids, str) else list(oids)
        r = self._pedir("get", oids)
        if r["error"] == 2 and len(oids) > 1:          # SNMPv1: un OID inexistente falla TODO el pedido; se piden de a uno
            salida = {}
            for o in oids:
                salida.update(self.get(o))
            return salida
        if r["error"] not in (0, 2):
            raise SnmpError(f"La UPS devolvió un error SNMP ({r['error']}).")
        salida = {o: None for o in oids}
        if r["error"] == 0:
            for o, v in r["valores"]:
                salida[o] = None if v in (NO_EXISTE, FIN_MIB) else v
        return salida

    def siguiente(self, oid: str):
        """(oid, valor) del OID que sigue al dado, o None si no hay más."""
        r = self._pedir("next", [oid])
        if r["error"] == 2 or not r["valores"]:
            return None
        o, v = r["valores"][0]
        return None if v is FIN_MIB else (o, None if v is NO_EXISTE else v)

    def walk(self, base: str, maximo: int = 5000):
        """Recorre el subárbol `base` y entrega (oid, valor). Se corta al salir del subárbol, al fin de la MIB, si el
        agente repite un OID (algunos tienen bugs y entrarían en un bucle) o al llegar a `maximo` valores."""
        base_t, actual, cuenta = _tupla(base), base, 0
        while cuenta < maximo:
            sig = self.siguiente(actual)
            if sig is None:
                return
            oid, valor = sig
            t = _tupla(oid)
            if t[:len(base_t)] != base_t or t <= _tupla(actual):
                return
            yield oid, valor
            actual, cuenta = oid, cuenta + 1
