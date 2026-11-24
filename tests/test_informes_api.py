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
import db
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
        por_id = {c["id"]: c for cat in j["categorias"] for c in cat["campos"]}
        self.assertTrue(por_id["AnimalGroupName"]["agrupable"])
        self.assertTrue(por_id["CurrentLactationNumber"]["agrupable"])
        self.assertFalse(por_id["CurrentDIM"]["agrupable"])
        ok = [c for cat in j["categorias"] for c in cat["campos"] if c["id"] == "Number"][0]
        self.assertTrue(ok["verificado"])
        self.assertEqual(ok["aviso"], "")


class TestFormula(Base):
    def post(self, cuerpo, cliente=None):
        return (cliente or self.admin).post("/api/informes/formula", json=cuerpo)

    def test_exige_ser_administrador(self):
        self.assertEqual(self.post({"formula": "[CurrentDIM] * 2"}, self.anon).status_code, 401)
        self.assertEqual(self.post({"formula": "[CurrentDIM] * 2"}, self.operario).status_code, 403)

    def test_una_formula_valida_dice_que_campos_usa(self):
        r = self.post({"formula": "[SevenDayAverage] / [CurrentDIM]"})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual([c["id"] for c in j["campos"]], ["SevenDayAverage", "CurrentDIM"])
        self.assertEqual(j["campos"][0]["etiqueta"], "Promedio producción diaria de leche 7 d.")
        self.assertTrue(j["verificado"])
        self.assertNotIn("CAST", r.get_data(as_text=True))                # el SQL no sale por la API

    def test_avisa_si_la_cuenta_usa_un_campo_sin_verificar(self):
        j = self.post({"formula": "[PeakYieldLastLactation] * 2"}).get_json()
        self.assertFalse(j["verificado"])

    def test_una_formula_mala_es_un_400_con_el_motivo(self):
        for f, motivo in (("[CurrentDIM] +", "incompleta"), ("[Nada] * 2", "Nada"), ("[Number] * 2", "numérico"), ("2 * 3", "campo"),
                          ("[CurrentDIM]; DROP", "No entiendo")):
            r = self.post({"formula": f})
            self.assertEqual(r.status_code, 400, f)
            self.assertIn(motivo, r.get_json()["error"])

    def test_cuerpo_que_no_es_json(self):
        self.assertEqual(self.admin.post("/api/informes/formula", data="x", content_type="text/plain").status_code, 400)
        self.assertEqual(self.post({}).status_code, 400)


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

    def test_los_resumenes_los_grupos_y_los_calculados_llegan_al_motor(self):
        falso = {"columnas": [], "filas": [], "truncado": False, "sql": "", "grupos": [{"nivel": 1}], "totales": {"n": 3}}
        cuerpo = {"columnas": [{"campo": "CurrentDIM", "resumen": "avg"}], "agrupar_por": ["AnimalGroupName"], "detalle": False,
                  "calculados": [{"id": "calc_1", "etiqueta": "X", "formula": "[CurrentDIM] * 2"}]}
        with mock.patch.object(im, "ejecutar", return_value=falso) as m:
            r = self.post(cuerpo)
        self.assertEqual(m.call_args.args[0], cuerpo)
        self.assertEqual(r.get_json()["totales"], {"n": 3})
        self.assertEqual(r.get_json()["grupos"], [{"nivel": 1}])

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


class TestExportarAgrupado(BaseGuardados):
    """De punta a punta con el catálogo REAL: el pedido → el motor (que compila y corre) → el Excel. Lo único falso es la base, y
    cada consulta que el motor arma pasa antes por el validador de SQL de db.py, como pasaría en producción."""

    DEF2 = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM", "resumen": "avg"}, {"campo": "SevenDayAverage", "resumen": "sum"}],
            "agrupar_por": ["AnimalGroupName"]}
    # Las filas de detalle salen en el orden de SQL («Rodeo 10» antes que «Rodeo 2»); el Excel tiene que ordenarlas como el motor.
    DETALLE = [["3250", 100, 30.0, "Rodeo 1"], ["4300", 120, 20.0, "Rodeo 1"], ["5100", 150, 40.0, "Rodeo 10"], ["4100", 210, 29.0, "Rodeo 2"]]
    RESUMEN = [["Rodeo 1", 0, 2, 110.0, 50.0], ["Rodeo 10", 0, 1, 150.0, 40.0], ["Rodeo 2", 0, 1, 210.0, 29.0], [None, 1, 4, 145.0, 119.0]]

    def exportar_con_base_falsa(self, filas_detalle, filas_resumen, **kw):
        vistas = []

        def run(sql, validate=True, tambo=None, max_rows=None):
            db.validate_sql(sql)
            vistas.append(sql)
            return {"columns": [], "rows": filas_detalle if " AS c0" in sql else filas_resumen, "truncated": False}
        cuerpo = dict(self.DEF2, nombre="Producción por rodeo")
        cuerpo.update(kw)
        with mock.patch.object(db, "run_query", run):
            r = self.admin.post("/api/informes/exportar", json=cuerpo)
        return r, vistas

    @staticmethod
    def hoja(r, nombre="Informe"):
        ws = openpyxl.load_workbook(io.BytesIO(r.data))[nombre]
        return [[c.value for c in fila] for fila in ws.iter_rows()]

    def test_agrupado_con_detalle(self):
        r, vistas = self.exportar_con_base_falsa(self.DETALLE, self.RESUMEN)
        self.assertEqual(r.status_code, 200, r.data[:300])
        self.assertEqual(len(vistas), 2)                              # el detalle y el resumen
        f = self.hoja(r)
        self.assertEqual(f[0], ["Grupo", "Número del animal", "Días en ordeño", "Promedio producción diaria de leche 7 d. (kg)"])
        self.assertEqual(f[1], ["Nombre del grupo: Rodeo 1 · 2 animales", None, 110.0, 50.0])
        self.assertEqual([x[1:] for x in f[2:4]], [["3250", 100, 30.0], ["4300", 120, 20.0]])
        self.assertEqual(f[4], ["Nombre del grupo: Rodeo 2 · 1 animal", None, 210.0, 29.0])           # 2 antes que 10, como en pantalla
        self.assertEqual(f[5][1:], ["4100", 210, 29.0])
        self.assertEqual(f[6], ["Nombre del grupo: Rodeo 10 · 1 animal", None, 150.0, 40.0])
        self.assertEqual(f[7][1:], ["5100", 150, 40.0])
        self.assertEqual(f[8], ["Total · 4 animales", None, 145.0, 119.0])

    def test_solo_resumen_hace_una_sola_consulta(self):
        r, vistas = self.exportar_con_base_falsa(None, self.RESUMEN, detalle=False)
        self.assertEqual(r.status_code, 200, r.data[:300])
        self.assertEqual(len(vistas), 1)
        f = self.hoja(r)
        self.assertEqual(f[0], ["Nombre del grupo", "Animales", "Días en ordeño · Promedio",
                                "Promedio producción diaria de leche 7 d. (kg) · Suma"])
        self.assertEqual(f[1:], [["Rodeo 1", 2, 110.0, 50.0], ["Rodeo 2", 1, 210.0, 29.0], ["Rodeo 10", 1, 150.0, 40.0],
                                 ["Total", 4, 145.0, 119.0]])

    def test_la_hoja_de_condiciones_dice_cuantos_animales_y_como_se_agrupo(self):
        r, _ = self.exportar_con_base_falsa(None, self.RESUMEN, detalle=False)
        textos = [x for fila in self.hoja(r, "Condiciones") for x in fila if x is not None]
        self.assertIn("Animales en el informe", textos)
        self.assertIn(4, textos)
        self.assertIn("Agrupado por Nombre del grupo", textos)

    def test_un_campo_calculado_con_un_nombre_peligroso_no_es_una_formula_en_el_excel(self):
        cuerpo = {"columnas": [{"campo": "calc_1", "resumen": "sum"}], "detalle": False, "agrupar_por": [],
                  "calculados": [{"id": "calc_1", "etiqueta": "=1+1", "formula": "[CurrentDIM] * 2"}]}
        r, _ = self.exportar_con_base_falsa(None, [[4, 800.0]], **cuerpo)
        self.assertEqual(r.status_code, 200, r.data[:300])
        ws = openpyxl.load_workbook(io.BytesIO(r.data))["Informe"]
        self.assertEqual(ws["B1"].data_type, "s")


if __name__ == "__main__":
    unittest.main()
