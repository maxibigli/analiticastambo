"""El endpoint `POST /api/informes/describir` (armar un informe con una frase) y el aviso `ia` de `/api/informes/campos`.

Solo administrador. Cliente de IA de mentira: ninguna prueba llama a la API de Anthropic. Los errores nunca llevan detalles de la API ni del servidor.

    C:\\Python314\\python.exe -m unittest tests.test_informes_lenguaje_api -v
"""
import os
import unittest
from unittest import mock

import agente
import app
import informes_lenguaje as il
import informes_motor as im
from tests.test_informes_lenguaje import BUENA, Cliente, con, respuesta

FRASE = "vacas del rodeo 3 con más de 200 días en ordeño, ordenadas por producción"


class Base(unittest.TestCase):
    def setUp(self):
        self.anon = app.app.test_client()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        il.reiniciar_tope()
        self.addCleanup(il.reiniciar_tope)
        self.cliente = Cliente()
        for p in (mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "clave-de-prueba"}), mock.patch.object(agente, "_client", lambda: self.cliente),
                  mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa")):
            p.start()
            self.addCleanup(p.stop)

    def pedir(self, cuerpo=None, cliente=None, **kw):
        return (cliente or self.admin).post("/api/informes/describir", json={"frase": FRASE} if cuerpo is None else cuerpo, **kw)


class TestDescribir(Base):
    def test_exige_ser_administrador(self):
        self.assertEqual(self.pedir(cliente=self.anon).status_code, 401)
        self.assertEqual(self.pedir(cliente=self.operario).status_code, 403)
        self.assertEqual(self.cliente.llamadas, [])

    def test_arma_el_informe(self):
        self.cliente.respuestas = [respuesta(BUENA)]
        r = self.pedir()
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["nombre"], "Vacas del Rodeo 3 con más de 200 días")
        im.compilar(j["definicion"], herd=1)
        self.assertIn("Días en ordeño > 200", j["se_lee"])
        self.assertEqual(len(self.cliente.llamadas), 1)

    def test_no_devuelve_sql_ni_cosas_internas(self):
        self.cliente.respuestas = [respuesta(BUENA)]
        texto = self.pedir().get_data(as_text=True)
        for prohibido in ("SELECT", "DATEDIFF", "LEFT JOIN", "AnimalReproductionInfo", "clave-de-prueba"):
            self.assertNotIn(prohibido, texto)

    def test_pasa_el_dia_de_calcular_al(self):
        self.cliente.respuestas = [respuesta(BUENA)]
        self.pedir({"frase": FRASE, "hoy": "2026-07-30"})
        self.assertIn("jue 30/07/2026", self.cliente.llamadas[0]["system"])

    def test_un_dia_que_no_es_una_fecha_es_400(self):
        r = self.pedir({"frase": FRASE, "hoy": "ayer"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cliente.llamadas, [])

    def test_cuando_la_ia_dice_que_no_se_puede_es_una_respuesta_normal(self):
        self.cliente.respuestas = [respuesta({**BUENA, "se_puede": False, "columnas": [], "condiciones": [], "lo_que_no_se_pudo": ["No existe el campo células somáticas"]})]
        r = self.pedir()
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertFalse(j["ok"])
        self.assertEqual(j["no_pude"], ["No existe el campo células somáticas"])

    def test_pedidos_mal_hechos_son_400(self):
        for cuerpo in ({}, {"frase": ""}, {"frase": "   "}, {"frase": 5}, {"frase": "x" * (il.FRASE_MAX + 1)}):
            r = self.pedir(cuerpo)
            self.assertEqual(r.status_code, 400, cuerpo)
            self.assertIn("error", r.get_json())
        self.assertEqual(self.admin.post("/api/informes/describir", data="no es json").status_code, 400)
        self.assertEqual(self.cliente.llamadas, [])

    def test_sin_clave_de_api_es_503(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            r = self.pedir()
        self.assertEqual(r.status_code, 503)
        self.assertIn("ANTHROPIC_API_KEY", r.get_json()["error"])
        self.assertEqual(self.cliente.llamadas, [])

    def test_una_definicion_que_no_pasa_el_compilador_es_400_y_nunca_se_ejecuta(self):
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        self.cliente.respuestas = [respuesta(mala), respuesta(mala)]
        with mock.patch.object(im, "ejecutar", side_effect=AssertionError("armar un informe no lo ejecuta")):
            r = self.pedir()
        self.assertEqual(r.status_code, 400)
        self.assertIn("No pude armar", r.get_json()["error"])
        self.assertEqual(len(self.cliente.llamadas), 2)

    def test_si_la_api_falla_es_502_sin_detalles(self):
        self.cliente.respuestas = [RuntimeError("401 invalid x-api-key sk-ant-SECRETO")]
        r = self.pedir()
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("SECRETO", r.get_data(as_text=True))
        self.assertIn("No se pudo armar", r.get_json()["error"])

    def test_un_error_inesperado_tambien_es_502_sin_detalles(self):
        with mock.patch.object(il, "armar", side_effect=KeyError("SECRETO")):
            r = self.pedir()
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("SECRETO", r.get_data(as_text=True))

    def test_el_tope_de_pedidos_es_429(self):
        for _ in range(il.PEDIDOS_POR_HORA):
            self.cliente.respuestas.append(respuesta(BUENA))
            self.assertEqual(self.pedir().status_code, 200)
        r = self.pedir()
        self.assertEqual(r.status_code, 429)
        self.assertIn("muchos pedidos", r.get_json()["error"])


class TestCamposAvisaSiHayIA(Base):
    def test_con_clave(self):
        self.assertIs(self.admin.get("/api/informes/campos").get_json()["ia"], True)

    def test_sin_clave(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertIs(self.admin.get("/api/informes/campos").get_json()["ia"], False)


if __name__ == "__main__":
    unittest.main()
