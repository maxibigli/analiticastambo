"""Endpoints de los informes: `GET /api/informes/campos` (el árbol de campos) y `POST /api/informes/ejecutar`.

Solo administrador. El catálogo que sale por la API nunca lleva el SQL de los campos.

    C:\\Python314\\python.exe -m unittest tests.test_informes_api -v
"""
import unittest
from unittest import mock

import app
import informes_motor as im


class Base(unittest.TestCase):
    def setUp(self):
        self.anon = app.app.test_client()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"


class TestCampos(Base):
    def test_exige_ser_administrador(self):
        self.assertEqual(self.anon.get("/api/informes/campos").status_code, 401)
        self.assertEqual(self.operario.get("/api/informes/campos").status_code, 403)

    def test_devuelve_las_categorias_con_sus_campos_y_sin_sql(self):
        r = self.admin.get("/api/informes/campos")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        ids = [c["id"] for cat in j["categorias"] for c in cat["campos"]]
        self.assertIn("CurrentDIM", ids)
        self.assertIn("Number", ids)
        texto = r.get_data(as_text=True)
        for prohibido in ("DATEDIFF", "AnimalReproductionInfo", "LEFT JOIN", "r.LastLactationChangeDate"):
            self.assertNotIn(prohibido, texto)
        c = [c for cat in j["categorias"] for c in cat["campos"] if c["id"] == "CurrentReproductionStatus"][0]
        self.assertFalse(c["verificado"])
        self.assertIn("DelPro", c["aviso"])
        self.assertNotIn("nota", c)                       # la nota interna (tablas, cuentas de verificación) no sale
        ok = [c for cat in j["categorias"] for c in cat["campos"] if c["id"] == "Number"][0]
        self.assertTrue(ok["verificado"])
        self.assertEqual(ok["aviso"], "")


class TestEjecutar(Base):
    def post(self, cuerpo, cliente=None):
        return (cliente or self.admin).post("/api/informes/ejecutar", json=cuerpo)

    def test_exige_ser_administrador(self):
        self.assertEqual(self.post({"columnas": [{"campo": "Number"}]}, self.anon).status_code, 401)
        self.assertEqual(self.post({"columnas": [{"campo": "Number"}]}, self.operario).status_code, 403)

    def test_ejecuta_y_devuelve_columnas_y_filas(self):
        falso = {"columnas": [{"id": "Number", "etiqueta": "Número del animal"}], "filas": [["3250"]], "truncado": False, "sql": "SELECT 1"}
        with mock.patch.object(im, "ejecutar", return_value=falso) as m:
            r = self.post({"columnas": [{"campo": "Number"}]})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["filas"], [["3250"]])
        self.assertNotIn("sql", j)                                  # el SQL no sale por la API
        self.assertEqual(m.call_args.args[0], {"columnas": [{"campo": "Number"}]})

    def test_la_fecha_de_referencia_llega_al_motor(self):
        falso = {"columnas": [], "filas": [], "truncado": False, "sql": ""}
        with mock.patch.object(im, "ejecutar", return_value=falso) as m:
            self.post({"columnas": [{"campo": "CurrentDIM"}], "hoy": "2026-07-30"})
        self.assertEqual(m.call_args.kwargs.get("hoy"), "2026-07-30")

    def test_una_definicion_mala_es_un_400_con_el_motivo(self):
        r = self.post({"columnas": [{"campo": "NoExiste"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("NoExiste", r.get_json()["error"])

    def test_un_cuerpo_que_no_es_json_es_un_400(self):
        r = self.admin.post("/api/informes/ejecutar", data="no es json", content_type="text/plain")
        self.assertEqual(r.status_code, 400)

    def test_un_error_de_la_base_no_filtra_el_detalle(self):
        with mock.patch.object(im, "ejecutar", side_effect=RuntimeError("Server=SECRETO;Pwd=xyz")):
            r = self.post({"columnas": [{"campo": "Number"}]})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("SECRETO", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
