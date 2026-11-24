"""Endpoints de los informes: `GET /api/informes/campos` (el árbol de campos) y `POST /api/informes/ejecutar`.

Solo administrador. El catálogo que sale por la API nunca lleva el SQL de los campos.

    C:\\Python314\\python.exe -m unittest tests.test_informes_api -v
"""
import io
import os
import tempfile
import unittest
from unittest import mock

import openpyxl

import app
import informes_guardados as ig
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


class BaseGuardados(Base):
    """Con una base de informes propia en una carpeta temporal y el tambo fijado: nada toca informes.db ni arranca warmups."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tambo = ["ponderosa"]
        for p in (mock.patch.object(ig, "RUTA_DB", os.path.join(self.dir.name, "informes.db")),
                  mock.patch.object(app, "_tambo_del_request", lambda: self.tambo[0])):
            p.start()
            self.addCleanup(p.stop)

    DEF = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM"}]}


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


class TestGuardados(BaseGuardados):
    def crear(self, nombre="Uno", cliente=None, **extra):
        return (cliente or self.admin).post("/api/informes", json=dict({"nombre": nombre, "definicion": self.DEF}, **extra))

    def test_exigen_ser_administrador(self):
        for cliente, codigo in ((self.anon, 401), (self.operario, 403)):
            self.assertEqual(cliente.get("/api/informes").status_code, codigo)
            self.assertEqual(cliente.get("/api/informes/1").status_code, codigo)
            self.assertEqual(self.crear(cliente=cliente).status_code, codigo)
            self.assertEqual(cliente.put("/api/informes/1", json={"nombre": "x"}).status_code, codigo)

    def test_crear_listar_y_leer(self):
        r = self.crear("Prod. x lactancia", favorito=True)
        self.assertEqual(r.status_code, 201)
        id_ = r.get_json()["id"]
        self.assertEqual(r.get_json()["usuario"], "x")
        lista = self.admin.get("/api/informes").get_json()["informes"]
        self.assertEqual([(i["nombre"], i["favorito"], i["n_columnas"]) for i in lista], [("Prod. x lactancia", True, 2)])
        uno = self.admin.get(f"/api/informes/{id_}").get_json()
        self.assertEqual(uno["definicion"]["columnas"], self.DEF["columnas"])

    def test_nombre_repetido_o_definicion_mala_son_un_400_con_el_motivo(self):
        self.crear("Uno")
        r = self.crear("uno")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Ya hay un informe", r.get_json()["error"])
        r = self.admin.post("/api/informes", json={"nombre": "Malo", "definicion": {"columnas": [{"campo": "NoExiste"}]}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("NoExiste", r.get_json()["error"])
        self.assertEqual(self.admin.post("/api/informes", data="x", content_type="text/plain").status_code, 400)

    def test_actualizar(self):
        id_ = self.crear("Uno").get_json()["id"]
        r = self.admin.put(f"/api/informes/{id_}", json={"nombre": "Dos", "favorito": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["nombre"], r.get_json()["favorito"]), ("Dos", True))
        self.crear("Tres")
        r = self.admin.put(f"/api/informes/{id_}", json={"nombre": "TRES"})
        self.assertEqual(r.status_code, 400)

    def test_inexistente_es_404(self):
        self.assertEqual(self.admin.get("/api/informes/999").status_code, 404)
        self.assertEqual(self.admin.put("/api/informes/999", json={"nombre": "x"}).status_code, 404)

    def test_un_tambo_no_ve_ni_toca_los_del_otro(self):
        id_ = self.crear("Mio").get_json()["id"]
        self.tambo[0] = "san_jose"
        self.assertEqual(self.admin.get("/api/informes").get_json()["informes"], [])
        self.assertEqual(self.admin.get(f"/api/informes/{id_}").status_code, 404)
        self.assertEqual(self.admin.put(f"/api/informes/{id_}", json={"nombre": "Robado"}).status_code, 404)


class TestExportar(BaseGuardados):
    RES = {"columnas": [{"id": "Number", "etiqueta": "Número del animal", "tipo": "txt", "unidad": "", "decimales": 0},
                        {"id": "CurrentDIM", "etiqueta": "Días en ordeño", "tipo": "num", "unidad": "", "decimales": 0}],
           "filas": [["3250", 881], ["4300", 356]], "truncado": False, "sql": "SELECT 1", "segundos": 0.1}

    def exportar(self, cliente=None, **kw):
        cuerpo = dict(self.DEF, nombre="Prod. x lactancia")
        cuerpo.update(kw)
        return (cliente or self.admin).post("/api/informes/exportar", json=cuerpo)

    def test_exige_ser_administrador(self):
        self.assertEqual(self.exportar(self.anon).status_code, 401)
        self.assertEqual(self.exportar(self.operario).status_code, 403)

    def test_devuelve_un_xlsx_con_los_datos_y_un_nombre_de_archivo(self):
        with mock.patch.object(im, "ejecutar", return_value=self.RES):
            r = self.exportar()
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r.headers["Content-Type"])
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertIn("Prod. x lactancia", r.headers["Content-Disposition"])
        ws = openpyxl.load_workbook(io.BytesIO(r.data))["Informe"]
        self.assertEqual([c.value for c in ws[2]], ["3250", 881])

    def test_la_hoja_de_condiciones_dice_las_del_informe(self):
        with mock.patch.object(im, "ejecutar", return_value=self.RES):
            r = self.exportar(condiciones=[{"campo": "CurrentDIM", "op": ">=", "valor": 0}])
        textos = [c.value for fila in openpyxl.load_workbook(io.BytesIO(r.data))["Condiciones"].iter_rows() for c in fila if c.value]
        self.assertTrue(any("Días en ordeño ≥ 0" in str(t) for t in textos))

    def test_una_definicion_mala_es_un_400(self):
        r = self.exportar(columnas=[{"campo": "NoExiste"}])
        self.assertEqual(r.status_code, 400)

    def test_un_error_de_la_base_no_filtra_el_detalle(self):
        with mock.patch.object(im, "ejecutar", side_effect=RuntimeError("Pwd=SECRETO")):
            r = self.exportar()
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("SECRETO", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
