"""Agente de la UPS (ups_agente.py): corre en una PC DEL TAMBO, lee la tarjeta por SNMP y empuja las lecturas hacia afuera.

Lo que importa: que no se pierda ninguna lectura si internet se corta (cola local), que reintente sin duplicar, que no se
caiga si la tarjeta no responde y que la clave de lectura y el token nunca aparezcan en un mensaje.

    C:\\Python314\\python.exe -m unittest tests.test_ups_agente -v
"""
import datetime
import json
import os
import tempfile
import unittest

import snmp_min
import ups_agente as ag

AHORA = datetime.datetime(2026, 11, 9, 14, 0, 0, tzinfo=datetime.timezone.utc)
UPS = {"disponible": True, "en_bateria": False, "bateria": {"carga_pct": 100, "minutos_restantes": 38},
       "entrada": {"lineas": [{"n": 1, "tension_v": 231}, {"n": 2, "tension_v": 230}, {"n": 3, "tension_v": 231}]},
       "salida": {"fuente": "normal", "corriente_total_a": 37.5}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cola = os.path.join(self.dir.name, "cola.db")
        self.enviados = []
        self.internet = True
        self.reloj = [AHORA]

    def enviar(self, lote):
        if not self.internet:
            raise ag.EnvioError("sin conexión")
        self.enviados.append(list(lote))

    def leer(self):
        return UPS

    def agente(self, leer=None, **kw):
        return ag.Agente(leer or self.leer, self.enviar, ruta_cola=self.cola,
                         ahora=lambda: self.reloj[0], **kw)

    def avanzar(self, segundos=30):
        self.reloj[0] += datetime.timedelta(seconds=segundos)


class TestCiclo(Base):
    def test_lee_y_envia_con_hora_utc_con_zona(self):
        r = self.agente().ciclo()
        self.assertEqual(len(self.enviados), 1)
        l = self.enviados[0][0]
        self.assertEqual(l["ts"], "2026-11-09T14:00:00Z")
        self.assertEqual(l["ups"]["bateria"]["carga_pct"], 100)
        self.assertEqual((r["enviadas"], r["pendientes"]), (1, 0))

    def test_sin_internet_acumula_y_al_volver_manda_todo_en_orden(self):
        a = self.agente()
        self.internet = False
        for _ in range(3):
            r = a.ciclo()
            self.avanzar()
        self.assertEqual((self.enviados, r["pendientes"]), ([], 3))
        self.internet = True
        r = a.ciclo()
        self.assertEqual(r["pendientes"], 0)
        horas = [l["ts"] for lote in self.enviados for l in lote]
        self.assertEqual(horas, sorted(horas))
        self.assertEqual(len(horas), 4)

    def test_la_cola_sobrevive_a_un_reinicio_del_agente(self):
        self.internet = False
        self.agente().ciclo()
        self.avanzar()
        self.internet = True
        self.agente().ciclo()                              # otro objeto = el agente se reinició
        self.assertEqual(sum(len(l) for l in self.enviados), 2)

    def test_la_tarjeta_sin_respuesta_se_informa_como_error_y_no_tira_el_agente(self):
        def mala():
            raise snmp_min.SnmpError("La UPS no respondió (192.168.1.50:161)")
        self.agente(leer=mala).ciclo()
        l = self.enviados[0][0]
        self.assertNotIn("ups", l)
        self.assertIn("no respondió", l["error"])

    def test_una_falla_inesperada_tampoco_tira_el_agente(self):
        def rota():
            raise ZeroDivisionError("bug")
        r = self.agente(leer=rota).ciclo()
        self.assertEqual(r["pendientes"], 0)
        self.assertIn("ZeroDivisionError", self.enviados[0][0]["error"])

    def test_la_tarjeta_que_no_habla_la_mib_estandar_se_informa(self):
        """RFC 1628 ausente: sin esto el servidor tomaría una lectura vacía por buena."""
        self.agente(leer=lambda: {"disponible": False}).ciclo()
        self.assertIn("1628", self.enviados[0][0]["error"])

    def test_manda_de_a_lotes_y_corta_en_el_primero_que_falla(self):
        self.internet = False
        a = self.agente(lote=2)
        for _ in range(5):
            a.ciclo()
            self.avanzar()
        self.internet = True
        a.ciclo()
        self.assertEqual([len(l) for l in self.enviados], [2, 2, 2])      # 6 lecturas: 5 acumuladas + la nueva

    def test_tope_de_la_cola_descarta_las_mas_viejas(self):
        self.internet = False
        a = self.agente(max_cola=3)
        for _ in range(5):
            r = a.ciclo()
            self.avanzar()
        self.assertEqual(r["pendientes"], 3)
        self.internet = True
        a.ciclo()
        horas = [l["ts"] for lote in self.enviados for l in lote]
        self.assertEqual(horas[0], "2026-11-09T14:01:30Z")                  # las 3 más viejas se descartaron
        self.assertEqual(len(horas), 3)

    def test_el_lote_enviado_es_json_serializable(self):
        self.agente().ciclo()
        json.dumps(self.enviados[0])


class TestResumen(unittest.TestCase):
    def test_linea_para_la_consola(self):
        s = ag.resumen(UPS)
        self.assertIn("231/230/231", s)
        self.assertIn("100 %", s)

    def test_linea_en_bateria(self):
        u = dict(UPS, en_bateria=True)
        self.assertIn("EN BATERÍA", ag.resumen(u))


class TestDestino(unittest.TestCase):
    def test_exige_https_fuera_de_la_red_local(self):
        with self.assertRaises(ValueError):
            ag.validar_url("http://lactia.ejemplo.com")
        ag.validar_url("https://lactia.ejemplo.com")
        ag.validar_url("http://192.168.1.10:5310")          # prueba dentro de la red del tambo
        ag.validar_url("http://localhost:5310")

    def test_no_acepta_otros_esquemas(self):
        for u in ("ftp://x", "file:///etc/passwd", "lactia.ejemplo.com", ""):
            with self.assertRaises(ValueError):
                ag.validar_url(u)


class _Servidor:
    """Un servidor HTTP de mentira en 127.0.0.1 que guarda lo que le llega y contesta el código que se le pida."""

    def __init__(self, codigo=200):
        import http.server
        import threading
        self.pedidos, self.codigo = [], codigo
        padre = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                largo = int(self.headers.get("Content-Length", 0))
                padre.pedidos.append({"ruta": self.path, "auth": self.headers.get("Authorization"),
                                      "cuerpo": json.loads(self.rfile.read(largo))})
                self.send_response(padre.codigo)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"guardadas": 1}')

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def cerrar(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestTransporte(unittest.TestCase):
    def servidor(self, codigo=200):
        s = _Servidor(codigo)
        self.addCleanup(s.cerrar)
        return s

    def test_manda_el_token_y_el_tambo_a_la_ruta_de_ingesta(self):
        s = self.servidor()
        ag.enviar_http(s.url, "TOKEN-SECRETO-123", "ponderosa")([{"ts": "2026-11-09T14:00:00Z", "ups": UPS}])
        p = s.pedidos[0]
        self.assertEqual((p["ruta"], p["auth"]), ("/api/ups/ingesta", "Bearer TOKEN-SECRETO-123"))
        self.assertEqual((p["cuerpo"]["tambo"], len(p["cuerpo"]["lecturas"])), ("ponderosa", 1))

    def test_un_rechazo_es_un_error_de_envio_sin_el_token(self):
        for codigo in (401, 403, 500):
            s = self.servidor(codigo)
            with self.assertRaises(ag.EnvioError) as cm:
                ag.enviar_http(s.url, "TOKEN-SECRETO-123", "ponderosa")([{"ts": "x"}])
            self.assertIn(str(codigo), str(cm.exception))
            self.assertNotIn("TOKEN-SECRETO-123", str(cm.exception))

    def test_servidor_inalcanzable_es_error_de_envio(self):
        s = self.servidor()
        url = s.url
        s.cerrar()
        with self.assertRaises(ag.EnvioError):
            ag.enviar_http(url, "t" * 20, "ponderosa", timeout=2)([{"ts": "x"}])

    def test_el_token_no_aparece_en_la_representacion(self):
        e = ag.enviar_http("https://lactia.ejemplo.com", "TOKEN-SECRETO-123", "ponderosa")
        self.assertNotIn("TOKEN-SECRETO-123", repr(e))


class TestDeExtremoAExtremo(Base):
    """Tarjeta SNMP falsa -> Lector -> Agente -> HTTP -> servidor falso: la cadena real, sin simulacros intermedios."""

    def test_lee_la_tarjeta_y_la_entrega(self):
        try:
            from test_snmp_min import AgenteFalso
            from test_ups_snmp import _tabla_trifasica
        except ImportError:
            from tests.test_snmp_min import AgenteFalso
            from tests.test_ups_snmp import _tabla_trifasica
        tarjeta = AgenteFalso(_tabla_trifasica(), community="miClave")
        self.addCleanup(tarjeta.cerrar)
        srv = _Servidor()
        self.addCleanup(srv.cerrar)
        lector = ag.Lector("127.0.0.1", "miClave", puerto=tarjeta.puerto, version="2c")
        agente = ag.Agente(lector, ag.enviar_http(srv.url, "t" * 20, "ponderosa"), ruta_cola=self.cola)
        r = agente.ciclo()
        self.assertEqual((r["enviadas"], r["pendientes"]), (1, 0))
        ups = srv.pedidos[0]["cuerpo"]["lecturas"][0]["ups"]
        self.assertEqual([l["tension_v"] for l in ups["entrada"]["lineas"]], [230, 229, 231])
        self.assertEqual(ups["salida"]["fuente"], "normal")


if __name__ == "__main__":
    unittest.main()
