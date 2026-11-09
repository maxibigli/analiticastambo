"""Endpoints de la UPS: `POST /api/ups/ingesta` (lo llama el agente del tambo, sin sesión pero con token) y las consultas de
estado, histórico y cortes (con sesión).

    C:\\Python314\\python.exe -m unittest tests.test_ups_api -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import tambos
import ups_registro as ur

TOKEN = "t" * 40
TAMBO = tambos.DEFAULT_TAMBO


def _ts(seg_atras=0):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seg_atras)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ups(en_bateria=False):
    return {"disponible": True, "en_bateria": en_bateria, "bateria": {"carga_pct": 100, "estado": "normal"},
            "entrada": {"lineas": [{"n": 1, "tension_v": 231}]}, "salida": {"fuente": "normal"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(ur, "RUTA_DB", os.path.join(self.dir.name, "ups.db")),
                  mock.patch.dict(os.environ, {"UPS_INGESTA_TOKEN": TOKEN})):
            p.start()
            self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.agente = app.app.test_client()                       # sin sesión: así llama el agente
        self.persona = app.app.test_client()
        with self.persona.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def mandar(self, lecturas, token=TOKEN, tambo=TAMBO, **kw):
        h = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return self.agente.post("/api/ups/ingesta", json={"tambo": tambo, "lecturas": lecturas}, headers=h, **kw)


class TestIngesta(Base):
    def test_con_token_guarda_y_la_consulta_la_ve(self):
        r = self.mandar([{"ts": _ts(5), "ups": _ups()}])
        self.assertEqual((r.status_code, r.get_json()["guardadas"]), (200, 1))
        e = self.persona.get(f"/api/ups/estado?tambo={TAMBO}").get_json()
        self.assertTrue(e["hay_datos"])
        self.assertFalse(e["sin_datos"])
        self.assertEqual(e["ups"]["entrada"]["lineas"][0]["tension_v"], 231)

    def test_sin_token_o_con_token_malo_no_entra_y_no_guarda(self):
        for token in (None, "x" * 40, ""):
            self.assertEqual(self.mandar([{"ts": _ts(), "ups": _ups()}], token=token).status_code, 401)
        self.assertFalse(self.persona.get(f"/api/ups/estado?tambo={TAMBO}").get_json()["hay_datos"])

    def test_la_recepcion_no_configurada_se_dice(self):
        with mock.patch.dict(os.environ, {"UPS_INGESTA_TOKEN": ""}):
            r = self.mandar([{"ts": _ts(), "ups": _ups()}], token="")
        self.assertEqual(r.status_code, 503)
        self.assertIn("UPS_INGESTA_TOKEN", r.get_json()["error"])

    def test_un_token_corto_configurado_no_se_acepta(self):
        """Un token débil («1234») dejaría la puerta abierta a cualquiera que adivine."""
        with mock.patch.dict(os.environ, {"UPS_INGESTA_TOKEN": "corto"}):
            self.assertEqual(self.mandar([{"ts": _ts(), "ups": _ups()}], token="corto").status_code, 503)

    def test_muchos_intentos_con_token_malo_se_bloquean(self):
        for _ in range(5):
            self.mandar([], token="x" * 40)
        self.assertEqual(self.mandar([{"ts": _ts(), "ups": _ups()}]).status_code, 429)   # ni el token bueno pasa

    def test_el_bloqueo_no_afecta_al_login_de_las_personas(self):
        for _ in range(5):
            self.mandar([], token="x" * 40)
        self.assertFalse(auth.login_bloqueado("127.0.0.1"))

    def test_tambo_inexistente(self):
        r = self.mandar([{"ts": _ts(), "ups": _ups()}], tambo="no-existe")
        self.assertEqual(r.status_code, 400)

    def test_pedidos_mal_formados(self):
        h = {"Authorization": f"Bearer {TOKEN}"}
        self.assertEqual(self.agente.post("/api/ups/ingesta", data="no es json", headers=h).status_code, 400)
        self.assertEqual(self.agente.post("/api/ups/ingesta", json=[1, 2], headers=h).status_code, 400)
        self.assertEqual(self.agente.post("/api/ups/ingesta", json={"tambo": TAMBO, "lecturas": "x"},
                                          headers=h).status_code, 400)

    def test_lote_demasiado_grande(self):
        r = self.mandar([{"ts": _ts(), "ups": _ups()}] * (ur.MAX_LOTE + 1))
        self.assertEqual(r.status_code, 400)

    def test_cuerpo_gigante_se_corta_antes_de_leerlo(self):
        h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        r = self.agente.post("/api/ups/ingesta", data=b"x" * 2_000_000, headers=h)
        self.assertEqual(r.status_code, 413)

    def test_una_lectura_invalida_no_tira_el_lote(self):
        r = self.mandar([{"ts": "ayer", "ups": _ups()}, {"ts": _ts(), "ups": _ups()}])
        self.assertEqual((r.status_code, r.get_json()["guardadas"], r.get_json()["rechazadas"]), (200, 1, 1))


class TestConsultas(Base):
    def test_exigen_sesion(self):
        for ruta in ("estado", "historico", "cortes"):
            self.assertEqual(self.agente.get(f"/api/ups/{ruta}?tambo={TAMBO}").status_code, 401)

    def test_sin_datos_hace_rato(self):
        self.mandar([{"ts": _ts(3600), "ups": _ups()}])
        e = self.persona.get(f"/api/ups/estado?tambo={TAMBO}").get_json()
        self.assertTrue(e["sin_datos"])

    def test_cortes_y_historico(self):
        self.mandar([{"ts": _ts(300), "ups": _ups()}, {"ts": _ts(240), "ups": _ups(en_bateria=True)},
                     {"ts": _ts(120), "ups": _ups()}])
        c = self.persona.get(f"/api/ups/cortes?tambo={TAMBO}").get_json()
        self.assertEqual(len(c["cortes"]), 1)
        self.assertEqual(c["cortes"][0]["duracion_s"], 120)
        h = self.persona.get(f"/api/ups/historico?tambo={TAMBO}&horas=1").get_json()
        self.assertEqual(len(h["lecturas"]), 3)

    def test_historico_horas_invalidas(self):
        self.assertEqual(self.persona.get(f"/api/ups/historico?tambo={TAMBO}&horas=abc").status_code, 400)
        self.assertEqual(self.persona.get(f"/api/ups/historico?tambo={TAMBO}&horas=100000").status_code, 400)


if __name__ == "__main__":
    unittest.main()
