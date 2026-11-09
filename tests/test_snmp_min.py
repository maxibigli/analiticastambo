"""Cliente SNMP v1/v2c mínimo (snmp_min.py), usado para leer la UPS del tambo por su tarjeta de red.

Dos clases de prueba: (1) contra bytes REALES del protocolo, tal como los documenta el estándar (así no se valida el
código contra sí mismo); (2) contra un agente SNMP falso por UDP en localhost, para el ciclo completo
(timeout, reintentos, walk, fin de la MIB, errores).

    C:\\Python314\\python.exe -m unittest tests.test_snmp_min -v
"""
import socket
import threading
import unittest

import snmp_min as sm


class TestBytesReales(unittest.TestCase):
    # GetRequest SNMPv1, community "public", request-id 1, para sysDescr.0 (1.3.6.1.2.1.1.1.0). Es el paquete de ejemplo
    # que figura en las guías de SNMP/BER (RFC 1157 + X.690).
    GET_SYSDESCR_V1 = bytes.fromhex(
        "30 26 02 01 00 04 06 70 75 62 6c 69 63 a0 19 02 01 01 02 01 00 02 01 00 "
        "30 0e 30 0c 06 08 2b 06 01 02 01 01 01 00 05 00".replace(" ", ""))

    def test_el_paquete_de_pedido_es_el_del_estandar(self):
        paquete = sm.armar_pedido("get", 1, "1.3.6.1.2.1.1.1.0", "public", version=0)
        self.assertEqual(paquete, self.GET_SYSDESCR_V1)

    def test_oid_codificado_como_en_x690(self):
        self.assertEqual(sm._codificar_oid("1.3.6.1.2.1.1.1.0"), bytes.fromhex("06082b06010201010100"))
        # arcos mayores a 127 usan varios bytes (base 128): 1.3.6.1.4.1.318 (APC) = 2b 06 01 04 01 82 3e
        self.assertEqual(sm._codificar_oid("1.3.6.1.4.1.318"), bytes.fromhex("06072b06010401823e"))

    def test_longitudes_cortas_y_largas(self):
        self.assertEqual(sm._largo(5), b"\x05")
        self.assertEqual(sm._largo(127), b"\x7f")
        self.assertEqual(sm._largo(128), b"\x81\x80")
        self.assertEqual(sm._largo(300), b"\x82\x01\x2c")

    def test_enteros_con_signo(self):
        self.assertEqual(sm._codificar_entero(0), bytes.fromhex("020100"))
        self.assertEqual(sm._codificar_entero(127), bytes.fromhex("02017f"))
        self.assertEqual(sm._codificar_entero(128), bytes.fromhex("02020080"))
        self.assertEqual(sm._codificar_entero(-1), bytes.fromhex("0201ff"))
        self.assertEqual(sm._codificar_entero(-129), bytes.fromhex("0202ff7f"))

    def test_respuesta_con_texto_y_enteros(self):
        # GetResponse v1 de sysDescr.0 = "UPS" (request-id 1)
        resp = bytes.fromhex(
            "30 29 02 01 00 04 06 70 75 62 6c 69 63 a2 1c 02 01 01 02 01 00 02 01 00 "
            "30 11 30 0f 06 08 2b 06 01 02 01 01 01 00 04 03 55 50 53".replace(" ", ""))
        r = sm.leer_respuesta(resp)
        self.assertEqual((r["id"], r["error"]), (1, 0))
        self.assertEqual(r["valores"], [("1.3.6.1.2.1.1.1.0", "UPS")])

    def test_tipos_numericos_sin_signo_y_noSuch(self):
        # Gauge32 0xFFFFFFFF debe leerse como 4294967295, no como -1; INTEGER 0xFF como -1
        self.assertEqual(sm._decodificar_valor(0x42, bytes.fromhex("ffffffff")), 4294967295)
        self.assertEqual(sm._decodificar_valor(0x02, bytes.fromhex("ff")), -1)
        self.assertEqual(sm._decodificar_valor(0x43, bytes.fromhex("0001e240")), 123456)    # TimeTicks
        self.assertIs(sm._decodificar_valor(0x80, b""), sm.NO_EXISTE)
        self.assertIs(sm._decodificar_valor(0x81, b""), sm.NO_EXISTE)
        self.assertIs(sm._decodificar_valor(0x82, b""), sm.FIN_MIB)

    def test_oid_decodificado_y_ip(self):
        self.assertEqual(sm._decodificar_valor(0x06, bytes.fromhex("2b06010401823e")), "1.3.6.1.4.1.318")
        self.assertEqual(sm._decodificar_valor(0x40, bytes([192, 168, 1, 50])), "192.168.1.50")

    def test_basura_no_rompe_el_decodificador(self):
        for malo in (b"", b"\x30", b"\x30\x05\x02", b"\xff" * 20, b"\x30\x84\xff\xff\xff\xff"):
            with self.assertRaises(sm.SnmpError):
                sm.leer_respuesta(malo)


class AgenteFalso:
    """Agente SNMP v2c por UDP en localhost con una tabla de OIDs, que responde GET y GETNEXT."""

    def __init__(self, tabla, community="public", callar=0):
        self.tabla = dict(tabla)
        self.community = community
        self.callar = callar                # cuántos pedidos ignora antes de contestar (prueba de reintentos)
        self.recibidos = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.puerto = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self._fin = False
        self.hilo = threading.Thread(target=self._servir, daemon=True)
        self.hilo.start()

    def cerrar(self):
        self._fin = True
        self.hilo.join(1)
        self.sock.close()

    def _orden(self, oid):
        return tuple(int(x) for x in oid.split("."))

    def _servir(self):
        while not self._fin:
            try:
                datos, origen = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            self.recibidos += 1
            if self.recibidos <= self.callar:
                continue
            try:
                p = sm.leer_pedido(datos)
            except sm.SnmpError:
                continue
            if p["community"] != self.community:
                continue                    # una community equivocada no contesta (como los agentes reales)
            salida = []
            for oid in p["oids"]:
                if p["tipo"] == "get":
                    salida.append((oid, self.tabla.get(oid, sm.NO_EXISTE)))
                else:
                    siguientes = sorted((o for o in self.tabla if self._orden(o) > self._orden(oid)), key=self._orden)
                    salida.append((siguientes[0], self.tabla[siguientes[0]]) if siguientes else (oid, sm.FIN_MIB))
            self.sock.sendto(sm.armar_respuesta(p["id"], salida, p["community"], p["version"]), origen)


TABLA = {
    "1.3.6.1.2.1.1.1.0": "Tarjeta UPS de prueba",
    "1.3.6.1.2.1.33.1.2.3.0": 45,                       # upsEstimatedMinutesRemaining
    "1.3.6.1.2.1.33.1.3.3.1.3.1": 2301,                 # upsInputVoltage, línea 1 (V)
    "1.3.6.1.2.1.33.1.3.3.1.3.2": 2298,
    "1.3.6.1.2.1.33.1.3.3.1.3.3": 2310,
    "1.3.6.1.2.1.33.1.4.4.1.3.1": 120,                  # upsOutputCurrent, línea 1 (0,1 A)
    "1.3.6.1.4.1.318.1.1.1.1.1.1.0": "Smart-UPS",
}


class TestConAgente(unittest.TestCase):
    def setUp(self):
        self.ag = AgenteFalso(TABLA)
        self.addCleanup(self.ag.cerrar)

    def _c(self, **kw):
        return sm.Cliente("127.0.0.1", puerto=self.ag.puerto, timeout=0.5, reintentos=2, **kw)

    def test_get_de_varios_oids(self):
        r = self._c().get(["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.33.1.2.3.0"])
        self.assertEqual(r, {"1.3.6.1.2.1.1.1.0": "Tarjeta UPS de prueba", "1.3.6.1.2.1.33.1.2.3.0": 45})

    def test_un_oid_que_no_existe_vale_None_no_rompe(self):
        r = self._c().get(["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.33.9.9.9"])
        self.assertIsNone(r["1.3.6.1.2.1.33.9.9.9"])
        self.assertEqual(r["1.3.6.1.2.1.1.1.0"], "Tarjeta UPS de prueba")

    def test_walk_recorre_un_subarbol_y_se_detiene_al_salir_de_el(self):
        filas = list(self._c().walk("1.3.6.1.2.1.33.1.3"))
        self.assertEqual([o for o, _ in filas], ["1.3.6.1.2.1.33.1.3.3.1.3.1", "1.3.6.1.2.1.33.1.3.3.1.3.2",
                                                "1.3.6.1.2.1.33.1.3.3.1.3.3"])
        self.assertEqual([v for _, v in filas], [2301, 2298, 2310])

    def test_walk_del_final_de_la_mib_termina(self):
        self.assertEqual(list(self._c().walk("1.3.6.1.4.1.318")), [("1.3.6.1.4.1.318.1.1.1.1.1.1.0", "Smart-UPS")])
        self.assertEqual(list(self._c().walk("1.3.6.1.9")), [])

    def test_community_equivocada_es_un_timeout_claro(self):
        with self.assertRaises(sm.SnmpError) as cm:
            self._c(community="otra").get(["1.3.6.1.2.1.1.1.0"])
        self.assertIn("no respondió", str(cm.exception))
        self.assertIn("community", str(cm.exception))

    def test_reintenta_si_se_pierde_un_paquete(self):
        self.ag.callar = 1
        r = self._c().get(["1.3.6.1.2.1.1.1.0"])
        self.assertEqual(r["1.3.6.1.2.1.1.1.0"], "Tarjeta UPS de prueba")
        self.assertEqual(self.ag.recibidos, 2)

    def test_sin_agente_no_se_cuelga(self):
        ag = AgenteFalso({})
        puerto = ag.puerto
        ag.cerrar()
        with self.assertRaises(sm.SnmpError):
            sm.Cliente("127.0.0.1", puerto=puerto, timeout=0.2, reintentos=1).get(["1.3.6.1.2.1.1.1.0"])

    def test_walk_con_tope_no_se_va_al_infinito(self):
        filas = list(self._c().walk("1.3.6.1", maximo=3))
        self.assertEqual(len(filas), 3)

    def test_la_community_no_aparece_en_los_errores(self):
        with self.assertRaises(sm.SnmpError) as cm:
            self._c(community="secreta").get(["1.3.6.1.2.1.1.1.0"])
        self.assertNotIn("secreta", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
