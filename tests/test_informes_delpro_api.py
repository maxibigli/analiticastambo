"""Endpoints para traer los informes de DelPro: `GET /api/informes/delpro` (la lista) y `GET /api/informes/delpro/<oid>` (uno, traducido).

Solo administrador. Nada de esto toca la base real en las pruebas: `informes_delpro.cargar` se reemplaza, y `db.run_query` falla si alguien lo llama.
Lo que sale por la API nunca lleva SQL, ni usuarios ni servidores de la base.

    C:\\Python314\\python.exe -m unittest tests.test_informes_delpro_api -v
"""
import os
import tempfile
import unittest
from unittest import mock

import app
import db
import informes_delpro as dl
import informes_guardados as ig
import informes_motor as im
from tests.test_informes_delpro import carpeta, columna, columnas_de, datos, filtro, informe


def _datos():
    informes = [carpeta(1, "Mis informes"), informe(10, "Animales por rodeo", carpeta=1), informe(11, "Con un filtro", carpeta=1, ServerFilter=7),
                informe(12, "De otro nivel", nivel="SessionMilkYield"), informe(13, "Predefinido", predefinido=True)]
    cols = (columnas_de(10, ["Number", "AnimalGroupName", "CurrentDIM"], 100) + columnas_de(11, ["Number", "DaysDry"], 200) +
            columnas_de(12, ["Number"], 300) + columnas_de(13, ["Number"], 400))
    return datos(informes, cols, [filtro(7, "Preñadas", "[<AnimalExtended>ReproductionInfo.IsFirstHeat] = True")])


class Base(unittest.TestCase):
    def setUp(self):
        self.anon = app.app.test_client()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.cargas = []

        def cargar(tambo, run=None, refrescar=False):
            self.cargas.append((tambo, refrescar))
            if isinstance(self.datos, Exception):
                raise self.datos
            return self.datos

        self.datos = _datos()
        for p in (mock.patch.object(dl, "cargar", cargar), mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"),
                  mock.patch.object(db, "run_query", side_effect=AssertionError("la prueba no tiene que leer la base"))):
            p.start()
            self.addCleanup(p.stop)


class TestListar(Base):
    def test_exige_ser_administrador(self):
        self.assertEqual(self.anon.get("/api/informes/delpro").status_code, 401)
        self.assertEqual(self.operario.get("/api/informes/delpro").status_code, 403)

    def test_devuelve_los_informes_con_su_estado(self):
        r = self.admin.get("/api/informes/delpro")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["disponible"])
        por = {i["oid"]: i for i in j["informes"]}
        self.assertEqual(set(por), {10, 11, 12, 13})
        self.assertEqual(por[10]["estado"], "completo")
        self.assertEqual(por[11]["estado"], "parcial")
        self.assertTrue(por[11]["filtro_sin_traer"])
        self.assertEqual(por[12]["estado"], "no_disponible")
        self.assertEqual(por[10]["carpeta"], "Mis informes")
        self.assertEqual(j["cuenta"], {"completo": 2, "parcial": 1, "no_disponible": 1})
        self.assertEqual(j["faltantes"][0], {"campo": "DaysDry", "informes": 1, "nombre": "Días en seca"})       # el nombre en castellano, para que el dueño lo reconozca

    def test_la_lista_no_lleva_la_definicion_ni_sql(self):
        texto = self.admin.get("/api/informes/delpro").get_data(as_text=True)
        for prohibido in ("definicion", "SELECT", "LEFT JOIN", "DDMReport"):
            self.assertNotIn(prohibido, texto)

    def test_solo_los_del_tambo(self):
        j = self.admin.get("/api/informes/delpro?solo_tambo=1").get_json()
        self.assertNotIn(13, {i["oid"] for i in j["informes"]})

    def test_refrescar_vuelve_a_leer_la_base(self):
        self.admin.get("/api/informes/delpro")
        self.admin.get("/api/informes/delpro?refrescar=1")
        self.assertEqual(self.cargas, [("ponderosa", False), ("ponderosa", True)])

    def test_un_tambo_sin_informes_de_delpro_no_es_un_error(self):
        self.datos = dl.DelProNoDisponible("Este tambo no tiene informes de DelPro para traer.", vacio=True)
        r = self.admin.get("/api/informes/delpro")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertFalse(j["disponible"])
        self.assertTrue(j["vacio"])                                      # un estado vacío, no una falla: la pantalla no lo pinta como un error
        self.assertIn("no tiene informes de DelPro", j["motivo"])
        self.assertEqual(j["informes"], [])

    def test_si_no_se_pudo_leer_no_es_un_estado_vacio(self):
        self.datos = dl.DelProNoDisponible("No se pudieron leer los informes de DelPro.")
        j = self.admin.get("/api/informes/delpro").get_json()
        self.assertFalse(j["disponible"])
        self.assertFalse(j["vacio"])


    def test_un_error_inesperado_es_502_y_no_muestra_nada_de_la_excepcion(self):
        with mock.patch.object(dl, "listar", side_effect=RuntimeError("token=SECRETO en 10.0.0.5")):
            r = self.admin.get("/api/informes/delpro")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("SECRETO", r.get_data(as_text=True))
        self.assertNotIn("10.0.0.5", r.get_data(as_text=True))

    def test_se_le_pasan_los_rebanos_del_tambo(self):
        vistos = []
        original = dl.listar

        def espia(datos_, **kw):
            vistos.append(kw.get("rebanos"))
            return original(datos_, **kw)
        with mock.patch.object(dl, "listar", espia):
            self.admin.get("/api/informes/delpro")
        self.assertEqual(vistos, [[1]])                                  # los de `tambos.py` para «ponderosa»: los informes de otro rebaño de la base no se traen


class TestTraer(Base):
    def test_exige_ser_administrador(self):
        self.assertEqual(self.anon.get("/api/informes/delpro/10").status_code, 401)
        self.assertEqual(self.operario.get("/api/informes/delpro/10").status_code, 403)

    def test_devuelve_la_definicion_y_lo_que_no_se_trajo(self):
        r = self.admin.get("/api/informes/delpro/11")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["nombre"], "Con un filtro")
        self.assertEqual(j["estado"], "parcial")
        self.assertEqual(j["definicion"]["columnas"], [{"campo": "Number"}])
        self.assertEqual({d["tipo"] for d in j["diferencias"]}, {"columna", "filtro"})
        self.assertEqual(j["faltan"], ["DaysDry", "IsFirstHeat"])

    def test_un_informe_que_no_se_puede_traer_igual_se_explica(self):
        j = self.admin.get("/api/informes/delpro/12").get_json()
        self.assertEqual(j["estado"], "no_disponible")
        self.assertIsNone(j["definicion"])
        self.assertIn("animales", j["motivo"])

    def test_una_carpeta_o_un_numero_que_no_existe_es_404(self):
        for oid in (1, 999):
            self.assertEqual(self.admin.get(f"/api/informes/delpro/{oid}").status_code, 404)

    def test_un_error_de_adentro_no_se_confunde_con_un_informe_que_no_existe(self):
        with mock.patch.object(dl, "traducir_informe", side_effect=KeyError("Report")):
            r = self.admin.get("/api/informes/delpro/10")
        self.assertEqual(r.status_code, 502)                              # un KeyError cualquiera es un error nuestro, no un 404
        self.assertNotIn("Report", r.get_data(as_text=True))

    def test_un_informe_de_otro_rebano_de_la_base_se_explica(self):
        self.datos = datos([informe(20, "De otro tambo", Herd=6)], columnas_de(20, ["Number"], 500))
        j = self.admin.get("/api/informes/delpro/20").get_json()
        self.assertEqual(j["estado"], "no_disponible")
        self.assertIn("otro tambo", j["motivo"])

    def test_si_la_base_no_contesta_es_502_sin_detalles(self):
        self.datos = dl.DelProNoDisponible("No se pudieron leer los informes de DelPro de este tambo.")
        r = self.admin.get("/api/informes/delpro/10")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["error"], "No se pudieron leer los informes de DelPro de este tambo.")

    def test_la_definicion_traida_corre_y_se_puede_guardar(self):
        j = self.admin.get("/api/informes/delpro/10").get_json()
        im.compilar(j["definicion"], herd=1)
        with tempfile.TemporaryDirectory() as carpeta_tmp, mock.patch.object(ig, "RUTA_DB", os.path.join(carpeta_tmp, "informes.db")):
            r = self.admin.post("/api/informes", json={"nombre": j["nombre"], "definicion": j["definicion"]})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(r.get_json()["definicion"]["columnas"], j["definicion"]["columnas"])


if __name__ == "__main__":
    unittest.main()
