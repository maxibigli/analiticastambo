"""Traer VARIOS informes de DelPro de una vez (24/11/2026).

Los informes de DelPro los usa el cliente, no quien arma LactIA: en vez de adivinar cuáles importan, se eligen de la lista los que el cliente de verdad usa y se traen juntos.
La lista dice qué campos le faltan a cada informe, y con eso se calcula qué le falta a la SELECCIÓN (lo que hay que sumar al catálogo primero).

Traer = guardar el informe en `informes.db` con su nota de origen (los avisos que cambian qué animales aparecen). No se ejecuta nada: ninguna consulta contra la base.
Los errores nunca muestran el texto de una excepción.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_delpro_seleccion -v
"""
import os
import tempfile
import unittest
from unittest import mock

import app
import db
import informes_delpro as dl
import informes_guardados as ig
from tests.test_informes_delpro import carpeta, columnas_de, datos, filtro, informe
from tests.test_informes_delpro_api import Base as BaseApi, _datos


class TestLaListaDiceQueLeFalta(unittest.TestCase):
    def setUp(self):
        self.r = dl.listar(_datos())
        self.por = {i["oid"]: i for i in self.r["informes"]}

    def test_cada_informe_trae_los_campos_de_delpro_que_le_faltan(self):
        self.assertEqual(self.por[10]["faltan"], [])
        self.assertEqual(sorted(self.por[11]["faltan"]), ["DaysDry", "IsFirstHeat"])         # una columna y un campo de su filtro

    def test_un_informe_de_otro_nivel_no_cuenta_lo_que_le_falta(self):
        self.assertEqual(self.por[12]["faltan"], [])                                      # no se puede traer: no hay nada que sumar al catálogo para él

    def test_el_ranking_de_siempre_sigue_igual(self):
        self.assertEqual({f["campo"]: f["informes"] for f in self.r["faltantes"]}, {"DaysDry": 1, "IsFirstHeat": 1})


class TestLaNotaDeOrigen(unittest.TestCase):
    def traducir(self, oid):
        return dl.traducir_informe(_datos(), oid)

    def test_un_informe_completo_no_lleva_nota(self):
        self.assertIsNone(dl.origen_de(self.traducir(10)))

    def test_un_filtro_sin_traer_se_recuerda(self):
        o = dl.origen_de(self.traducir(11))
        self.assertEqual(o["titulo"], "Traído de DelPro")
        self.assertIn("Con un filtro", o["sub"])
        self.assertTrue(o["avisos"])
        contenido = [d["texto"] for d in self.traducir(11)["diferencias"] if d["gravedad"] == "contenido"]
        self.assertEqual(o["avisos"], contenido)

    def test_solo_lo_que_cambia_los_animales_va_en_la_nota(self):
        t = self.traducir(11)
        o = dl.origen_de(t)
        for d in t["diferencias"]:
            if d["gravedad"] != "contenido":
                self.assertNotIn(d["texto"], o["avisos"])


class TestTraerVarios(unittest.TestCase):
    def setUp(self):
        self.guardados = []

        def guardar(nombre, definicion, origen):
            if nombre in [g[0] for g in self.guardados]:
                return "ya_estaba", None
            self.guardados.append((nombre, definicion, origen))
            return "traido", 100 + len(self.guardados)
        self.guardar = guardar

    def traer(self, oids, **kw):
        return dl.traer_varios(_datos(), oids, self.guardar, **kw)

    def test_cada_uno_dice_que_paso(self):
        r = {x["oid"]: x for x in self.traer([10, 11, 12, 999])}
        self.assertEqual((r[10]["estado"], r[10]["id"]), ("traido", 101))
        self.assertEqual(r[11]["estado"], "traido")
        self.assertEqual(r[12]["estado"], "no_se_puede")
        self.assertTrue(r[12]["motivo"])
        self.assertEqual(r[999]["estado"], "no_existe")

    def test_sigue_el_orden_pedido_y_no_repite(self):
        self.assertEqual([x["oid"] for x in self.traer([11, 10, 11, 10])], [11, 10])

    def test_se_guarda_la_definicion_y_la_nota(self):
        self.traer([11])
        nombre, definicion, origen = self.guardados[0]
        self.assertEqual(nombre, "Con un filtro")
        self.assertEqual(definicion["columnas"][0], {"campo": "Number"})
        self.assertEqual(origen, dl.origen_de(dl.traducir_informe(_datos(), 11)))

    def test_uno_completo_se_guarda_sin_nota(self):
        self.traer([10])
        self.assertIsNone(self.guardados[0][2])

    def test_lo_que_ya_estaba_no_se_vuelve_a_guardar(self):
        self.traer([10])
        r = self.traer([10])
        self.assertEqual(r[0]["estado"], "ya_estaba")
        self.assertEqual(len(self.guardados), 1)

    def test_un_error_al_guardar_no_frena_a_los_demas_ni_muestra_el_texto(self):
        def guardar(nombre, definicion, origen):
            if nombre == "Con un filtro":
                raise RuntimeError("token secreto 12345")
            return "traido", 7
        r = {x["oid"]: x for x in dl.traer_varios(_datos(), [11, 10], guardar)}
        self.assertEqual(r[11]["estado"], "error")
        self.assertNotIn("12345", str(r))
        self.assertNotIn("secreto", str(r))
        self.assertEqual(r[10]["estado"], "traido")

    def test_un_informe_que_no_se_puede_leer_no_tumba_el_resto(self):
        original = dl.traducir_informe

        def traducir(datos_, oid, *a, **k):
            if oid == 10:
                raise ValueError("raro")
            return original(datos_, oid, *a, **k)
        with mock.patch.object(dl, "traducir_informe", traducir):
            r = {x["oid"]: x for x in self.traer([10, 11])}
        self.assertEqual(r[10]["estado"], "error")
        self.assertEqual(r[11]["estado"], "traido")


class TestPorLaApi(BaseApi):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(ig, "RUTA_DB", os.path.join(self.dir.name, "informes.db"))
        p.start()
        self.addCleanup(p.stop)

    def traer(self, oids, cliente=None, **kw):
        return (cliente or self.admin).post("/api/informes/delpro/traer", json={"oids": oids}, **kw)

    def test_exige_ser_administrador(self):
        self.assertEqual(self.anon.post("/api/informes/delpro/traer", json={"oids": [10]}).status_code, 401)
        self.assertEqual(self.operario.post("/api/informes/delpro/traer", json={"oids": [10]}).status_code, 403)

    def test_pedidos_mal_armados(self):
        for malo in (None, [], "10", {"a": 1}, [10, "11"], [10, 1.5], [True], [None], [0], [-3], list(range(1, dl.TRAER_MAX + 2))):
            r = self.admin.post("/api/informes/delpro/traer", json={"oids": malo})
            self.assertEqual(r.status_code, 400, repr(malo)[:40])
        self.assertEqual(self.admin.post("/api/informes/delpro/traer", data="no es json").status_code, 400)
        self.assertEqual(self.admin.post("/api/informes/delpro/traer", json=[10]).status_code, 400)
        self.assertEqual(ig.listar("ponderosa"), [])                                      # nada se guardó

    def test_trae_guarda_y_cuenta(self):
        r = self.traer([10, 11, 12, 999])
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        por = {x["oid"]: x for x in j["resultado"]}
        self.assertEqual((por[10]["estado"], por[11]["estado"], por[12]["estado"], por[999]["estado"]), ("traido", "traido", "no_se_puede", "no_existe"))
        self.assertEqual((j["traidos"], j["ya_estaban"], j["no_se_pudieron"]), (2, 0, 2))
        guardados = {i["nombre"]: i for i in ig.listar("ponderosa")}
        self.assertEqual(set(guardados), {"Animales por rodeo", "Con un filtro"})
        self.assertFalse(guardados["Animales por rodeo"]["con_aviso"])
        self.assertTrue(guardados["Con un filtro"]["con_aviso"])                          # lleva su nota: el filtro que no se trajo

    def test_lo_guardado_es_lo_mismo_que_trae_uno_por_uno(self):
        self.traer([10])
        uno = self.admin.get("/api/informes/delpro/10").get_json()["definicion"]
        guardado = ig.obtener("ponderosa", ig.listar("ponderosa")[0]["id"])["definicion"]
        self.assertEqual(guardado["columnas"], uno["columnas"])

    def test_traer_dos_veces_no_duplica(self):
        self.traer([10, 11])
        j = self.traer([10, 11]).get_json()
        self.assertEqual((j["traidos"], j["ya_estaban"]), (0, 2))
        self.assertEqual(len(ig.listar("ponderosa")), 2)

    def test_no_lee_ni_ejecuta_nada_contra_la_base(self):
        self.traer([10, 11])                                                              # `db.run_query` falla si alguien lo llama (ver Base)
        self.assertEqual(len(self.cargas), 1)

    def test_si_delpro_no_contesta_es_502_sin_detalles(self):
        self.datos = dl.DelProNoDisponible("No se pudieron leer los informes de DelPro.")
        r = self.traer([10])
        self.assertEqual(r.status_code, 502)
        self.assertEqual(ig.listar("ponderosa"), [])

    def test_un_error_al_guardar_no_muestra_el_texto(self):
        with mock.patch.object(ig, "crear", side_effect=RuntimeError("clave abc123")):
            r = self.traer([10, 11])
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("abc123", r.get_data(as_text=True))
        self.assertEqual({x["estado"] for x in r.get_json()["resultado"]}, {"error"})

    def test_un_nombre_ocupado_por_otro_informe_cuenta_como_ya_estaba(self):
        ig.crear("ponderosa", "Animales por rodeo", {"columnas": [{"campo": "Number"}]}, "x")
        j = self.traer([10]).get_json()
        self.assertEqual(j["resultado"][0]["estado"], "ya_estaba")


if __name__ == "__main__":
    unittest.main()
