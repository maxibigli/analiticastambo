"""Pruebas de `despachos_delpro` (despachos de leche cargados en DelPro como origen de las cargas) y de
su elección en `/api/cicla/cargas`. Con datos inyectados: la copia local de DDM tiene esas tablas en 0
filas, así que los números de acá son de ejemplo, NO de un despacho real.

    python -m unittest tests.test_despachos_delpro -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import app
import cicla
import configuracion_tambo
import despachos_delpro as dd

app._warmup = lambda *a, **k: None   # el calentamiento de caché consultaría la base real

TAMBO = app.tambos.DEFAULT_TAMBO
D = datetime.date

COLS_DESP = ["fecha", "remito", "tanque", "camion", "usina", "litros", "temperatura", "grasa", "proteina",
             "rcs", "dcc", "cbt", "mun", "acidez", "sangre", "inhibidores"]


def fila(dia, litros, temp=3.5, usina="La Serenisima", remito=1001, **calidad):
    base = {"grasa": 3.6, "proteina": 3.2, "rcs": 250, "dcc": 0, "cbt": 8, "mun": 0, "acidez": 0, "sangre": 0,
            "inhibidores": 0}
    base.update(calidad)
    return [datetime.datetime(2026, 11, dia, 7, 30), remito, "Tanque 1", "AB123CD", usina, litros, temp,
            base["grasa"], base["proteina"], base["rcs"], base["dcc"], base["cbt"], base["mun"], base["acidez"],
            base["sangre"], base["inhibidores"]]


DESPACHOS = {"columns": COLS_DESP, "truncated": False,
             "rows": [fila(2, 25000, 3.4, remito=1001), fila(3, 24000, 5.2, remito=1002, rcs=300),
                      fila(4, 1000, 3.9, remito=1003, grasa=0, proteina=0, rcs=0, cbt=0)]}
USO = {"columns": ["fecha", "litros", "uso"], "truncated": False,
       "rows": [[datetime.datetime(2026, 11, 2), 650.0, 1], [datetime.datetime(2026, 11, 3), 700.0, 2]]}


def consulta(sql, **_k):
    return USO if "MilkForInternalUsage" in sql else DESPACHOS


class TestSql(unittest.TestCase):
    def test_tablas_borrado_logico_y_fechas_sin_idioma(self):
        s = dd.sql_despachos(D(2026, 11, 1), D(2026, 11, 8))
        self.assertIn("FROM MilkShipmentBase", s)
        self.assertIn("GCRecord IS NULL", s)
        self.assertIn("CONVERT(date, '2026-11-01', 23)", s)         # estilo 23: no depende del idioma
        self.assertIn("DATEADD(day, 1, CONVERT(date, '2026-11-08', 23))", s)      # el último día entra entero
        u = dd.sql_uso_interno(D(2026, 11, 1), D(2026, 11, 8))
        self.assertIn("FROM MilkForInternalUsage", u)
        self.assertIn("GCRecord IS NULL", u)


class TestCarga(unittest.TestCase):
    def cargas(self):
        with mock.patch.object(dd.db, "run_query", consulta):
            return dd.obtener_cargas("x", D(2026, 11, 1), D(2026, 11, 8))

    def test_tiene_todas_las_claves_que_ya_usaba_la_pantalla(self):
        """Si cicla agrega o cambia una clave de la carga, esta prueba avisa que DelPro quedó atrás."""
        de_cicla = cicla._carga_desde_api({"Id": 1, "FechaIni": "2026-11-02T07:00:00", "LitrosTotal": 1,
                                           "LitrosEstimados": 1, "Tambo": 5003, "TempPromedio": 3})
        de_delpro = dd.carga_desde_fila(dict(zip(COLS_DESP, DESPACHOS["rows"][0])))
        self.assertEqual(set(de_cicla) - set(de_delpro), set())

    def test_mapeo(self):
        cargas, incompleto = self.cargas()
        self.assertFalse(incompleto)
        c = cargas[0]
        self.assertEqual((c["fecha"], c["remito"], c["tanque"], c["camion"]),
                         ("02/11/2026 07:30:00", "1001", "Tanque 1", "AB123CD"))
        self.assertEqual((c["lts_cicla"], c["temperatura"], c["destino_label"]), (25000.0, 3.4, "La Serenisima"))
        self.assertTrue(c["es_venta"])
        self.assertIsNone(c["lts_declarados"])               # no hay un segundo número: no se inventa la diferencia
        self.assertFalse(c["alerta_diferencia"])
        self.assertFalse(c["alerta_temp"])
        self.assertTrue(cargas[1]["alerta_temp"])            # 5,2 °C pasa el umbral de 4

    def test_la_calidad_en_cero_es_sin_dato(self):
        cargas, _ = self.cargas()
        self.assertEqual(cargas[0]["calidad"]["grasa"], 3.6)
        self.assertIsNone(cargas[2]["calidad"]["grasa"])
        self.assertIsNone(cargas[2]["calidad"]["rcs"])

    def test_el_resumen_de_cicla_lo_entiende(self):
        cargas, _ = self.cargas()
        r = cicla.resumen(cargas)
        self.assertEqual((r["total_cargas"], r["lts_total"], r["lts_venta"], r["alertas_temperatura"]), (3, 50000, 50000, 1))
        self.assertEqual(r["destinos_sin_clasificar"], [])
        self.assertEqual([d["label"] for d in r["destinos"]], ["La Serenisima"])

    def test_calidad_ponderada_por_litros_y_con_cuantos_la_informaron(self):
        cargas, _ = self.cargas()
        q = dd.calidad_resumen(cargas)
        # RCS: (250*25000 + 300*24000) / 49000 ; el 3er despacho no la informó
        self.assertEqual(q["rcs"], {"promedio": round((250 * 25000 + 300 * 24000) / 49000), "n": 2})
        self.assertEqual(q["grasa"]["n"], 2)
        self.assertEqual(dd.calidad_resumen([])["rcs"], {"promedio": None, "n": 0})

    def test_usina_vacia_y_remito_vacio(self):
        c = dd.carga_desde_fila({"fecha": datetime.datetime(2026, 11, 2), "usina": None, "remito": None, "litros": 10})
        self.assertEqual((c["destino_label"], c["remito"], c["destino"]), ("Sin usina", "", "sin_usina"))

    def test_uso_interno(self):
        with mock.patch.object(dd.db, "run_query", consulta):
            uso = dd.uso_interno("x", D(2026, 11, 1), D(2026, 11, 8))
        self.assertEqual([(u["fecha"][:10], u["litros"], u["uso_label"]) for u in uso],
                         [("02/11/2026", 650.0, "Uso 1"), ("03/11/2026", 700.0, "Uso 2")])

    def test_tope_de_filas_se_informa(self):
        with mock.patch.object(dd.db, "run_query", lambda *a, **k: {**DESPACHOS, "truncated": True}):
            self.assertTrue(dd.obtener_cargas("x", D(2026, 11, 1), D(2026, 11, 8))[1])

    def test_un_fallo_de_la_base_sale_como_error_con_mensaje(self):
        with mock.patch.object(dd.db, "run_query", side_effect=RuntimeError("sin red")):
            with self.assertRaises(dd.DespachosError) as ctx:
                dd.obtener_cargas("x", D(2026, 11, 1), D(2026, 11, 8))
        self.assertIn("sin red", str(ctx.exception))


CARGAS_CICLA = [cicla._carga_desde_api({"Id": 9, "FechaIni": "2026-11-02T07:00:00", "LitrosTotal": 20000,
                                        "LitrosEstimados": 20000, "Tambo": 5003, "TempPromedio": 3.0})]
URL = "?desde=2026-11-01&hasta=2026-11-08"


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.consultas = []

        def run_query(sql, **k):
            self.consultas.append(sql)
            return consulta(sql, **k)
        for objeto, nombre, valor in (
                (configuracion_tambo, "_RUTA", os.path.join(tmp.name, "cfg.json")),
                (app.cicla, "credenciales", lambda: ("u", "p")),
                (app.cicla, "obtener_cargas", lambda *a, **k: (list(CARGAS_CICLA), False)),
                (app.db, "run_query", run_query)):
            p = mock.patch.object(objeto, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def get(self, extra=""):
        return self.client.get("/api/cicla/cargas" + URL + extra)

    def test_sin_configurar_sigue_siendo_el_caudalimetro(self):
        d = self.get().get_json()
        self.assertEqual(d["origen"], "cicla")
        self.assertEqual([o["id"] for o in d["origenes"]], ["cicla", "delpro"])
        self.assertEqual(d["cargas"][0]["lts_cicla"], 20000)
        self.assertEqual(self.consultas, [], "no tenía por qué tocar DelPro")

    def test_configurado_en_delpro(self):
        configuracion_tambo.guardar(TAMBO, {"caudalimetro": "delpro"})
        r = self.get()
        d = r.get_json()
        self.assertEqual(r.status_code, 200, d)
        self.assertEqual((d["origen"], d["resumen"]["total_cargas"], d["resumen"]["lts_total"]), ("delpro", 3, 50000))
        self.assertEqual(len(d["uso_interno"]), 2)
        self.assertEqual(d["calidad"]["rcs"]["n"], 2)

    def test_la_pantalla_puede_pedir_el_otro_origen_sin_tocar_la_config(self):
        self.assertEqual(self.get("&origen=delpro").get_json()["origen"], "delpro")
        configuracion_tambo.guardar(TAMBO, {"caudalimetro": "delpro"})
        self.assertEqual(self.get("&origen=cicla").get_json()["origen"], "cicla")

    def test_un_origen_inventado_cae_a_lo_configurado(self):
        self.assertEqual(self.get("&origen=cualquier_cosa").get_json()["origen"], "cicla")
        configuracion_tambo.guardar(TAMBO, {"caudalimetro": "delpro"})
        self.assertEqual(self.get("&origen=cualquier_cosa").get_json()["origen"], "delpro")

    def test_la_segunda_consulta_sale_del_cache(self):
        self.get("&origen=delpro")
        n = len(self.consultas)
        self.get("&origen=delpro")
        self.assertEqual(len(self.consultas), n)

    def test_un_tambo_sin_milky_solo_ofrece_delpro(self):
        d = self.client.get("/api/cicla/cargas" + URL + "&tambo=lamartina_local&origen=delpro").get_json()
        self.assertEqual([o["id"] for o in d["origenes"]], ["delpro"])

    def test_falla_la_base_de_delpro(self):
        with mock.patch.object(app.db, "run_query", side_effect=RuntimeError("sin red")):
            r = self.get("&origen=delpro")
        self.assertEqual(r.status_code, 502)
        self.assertIn("sin red", r.get_json()["error"])

    def test_la_opcion_existe_en_el_catalogo_de_configuracion(self):
        self.assertIn("delpro", configuracion_tambo.CAUDALIMETROS)
        self.assertIn("delpro", configuracion_tambo.CAUDALIMETROS_LABEL)
        configuracion_tambo.guardar(TAMBO, {"caudalimetro": "delpro"})        # no la rechaza
        self.assertEqual(configuracion_tambo.config_de(TAMBO)["caudalimetro"], "delpro")


if __name__ == "__main__":
    unittest.main()
