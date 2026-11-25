"""Tanda 3a de campos: todo lo que se puede definir BARATO con lo que DelPro guarda (24/11/2026).

El catálogo no cuesta nada en reposo (una consulta se arma solo con los bloques de los campos que el informe usa); lo que cuesta es USAR un campo pesado. Estos son los baratos:
  * el último valor de cada parámetro del control lechero (cada uno con SU fecha: un control puede traer solo el rendimiento),
  * lo que sale del índice de últimos eventos de DelPro (el último cambio de rodeo) y del resumen de lactancia (el parto anterior),
  * edad y sexo,
  * la condición corporal de la CÁMARA (tabla `BcsDailyData`, que solo tienen los tambos con cámara: en los demás los campos no se ofrecen).

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda3a -v
"""
import unittest
from unittest import mock

import app
import db
import informes_catalogo as cat
import informes_motor as im

LECHERO = ("LatestSCCTestDate", "LatestFat", "LatestFatDate", "LatestProtein", "LatestMilkTestYield", "LatestMilkTestYieldDate")
VARIOS = ("AgeInDays", "DaysSinceLastGroupChange", "PreviousGroupNumber", "Sex", "PrevCalvingDate")
BCS = ("LastBcsValue", "LastBcsDryOffValue", "LastBcsCalvingValue", "LastBcsValue190DaysPregnant")
MARCADOS = ("LastBcsDryOffValue", "LastBcsCalvingValue", "LastBcsValue190DaysPregnant")      # los tres valores que DelPro marca en la cámara
TODOS = LECHERO + VARIOS + BCS


def compilar(campos, **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy="2026-07-30")
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


class TestElCatalogo(unittest.TestCase):
    def test_estan_todos(self):
        for id_ in TODOS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_tipos_y_unidades(self):
        c = cat.CATALOGO
        for id_ in ("LatestSCCTestDate", "LatestFatDate", "LatestMilkTestYieldDate", "PrevCalvingDate"):
            self.assertEqual(c[id_].tipo, "fecha", id_)
        for id_ in ("LatestFat", "LatestProtein"):
            self.assertEqual((c[id_].tipo, c[id_].unidad, c[id_].decimales), ("num", "%", 1), id_)           # DelPro los muestra con un decimal ({0:#0.0})
        self.assertEqual((c["LatestMilkTestYield"].unidad, c["LatestMilkTestYield"].decimales), ("kg", 1))
        for id_ in ("AgeInDays", "DaysSinceLastGroupChange", "PreviousGroupNumber"):
            self.assertEqual(c[id_].tipo, "num", id_)
        for id_ in BCS:
            self.assertEqual((c[id_].tipo, c[id_].decimales), ("num", 1), id_)

    def test_el_sexo_es_una_lista_cerrada(self):
        s = cat.CATALOGO["Sex"]
        self.assertEqual((s.tipo, s.valores, s.agrupable, s.verificado), ("txt", ("Hembra", "Macho"), True, False))

    def test_todos_dicen_de_donde_salen_y_ninguno_se_da_por_comparado_con_delpro(self):
        for id_ in TODOS:
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)

    def test_solo_los_de_la_camara_piden_la_tabla_de_la_camara(self):
        for id_ in BCS:
            self.assertEqual(cat.CATALOGO[id_].tabla, "BcsDailyData", id_)
        for id_ in LECHERO + VARIOS:
            self.assertEqual(cat.CATALOGO[id_].tabla, "", id_)


class TestSeCompilan(unittest.TestCase):
    def test_cada_uno_pasa_la_validacion_de_solo_lectura(self):
        for id_ in TODOS:
            db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}, {"campo": id_}]}, herd=1, hoy="2026-07-30")["sql"])

    def test_en_condiciones_resumenes_y_orden(self):
        db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}, {"campo": "LatestFat"}], "condiciones": [{"campo": "LatestFat", "op": "<", "valor": 3},
                                     {"campo": "LastBcsValue", "op": ">=", "valor": 3}, {"campo": "Sex", "op": "es", "valor": "Hembra"},
                                     {"campo": "LatestSCCTestDate", "op": ">=", "valor": "2026-06-01"}], "orden": {"campo": "LatestFat", "dir": 1}}, herd=1, hoy="2026-07-30")["sql"])
        im.compilar({"columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "LatestFat", "resumen": "avg"}, {"campo": "LastBcsValue", "resumen": "avg"},
                                  {"campo": "AgeInDays", "resumen": "max"}], "detalle": False}, herd=1, hoy="2026-07-30")

    def test_ninguno_agrupa_una_tabla_de_eventos_entera(self):
        """La regla de costo: lo «último» se busca por animal (TOP 1 por animal o un puntero del índice), nunca con un GROUP BY sobre la tabla."""
        for id_ in TODOS:
            if id_ in MARCADOS:
                continue                                                 # agrupan, pero solo las filas marcadas: ver el test de abajo
            sql = compilar([id_]).upper()
            self.assertNotIn("GROUP BY", sql, id_)

    def test_los_valores_marcados_agrupan_solo_las_filas_marcadas_y_leen_la_tabla_una_vez(self):
        """Con `OUTER APPLY` por animal SQL Server armaba un índice temporal de toda la tabla (991.000 lecturas) y con poca memoria lo derramaba a disco: 23 s por 1.913 vacas. Una tabla derivada que
        recorre `BcsDailyData` una vez y se queda con las ~480 filas marcadas (de 335.000) lee 2.264 páginas y tarda 0,3 s."""
        for id_ in MARCADOS:
            sql = compilar([id_])
            self.assertIn("WHERE x.IsValueForLastBcsDryOff = 1 OR x.IsValueForLastCalving = 1 OR x.IsValueForLastBcs190DaysPregnant = 1 OR x.IsValueForLastBcsVWP = 1 "
                          "OR x.IsValueForLastBcsDryOffPrevLact = 1 OR x.IsValueForFirstInseminationCurrentLactation = 1 GROUP BY x.Animal", sql, id_)       # tanda 4: suma tres marcas más
            self.assertNotIn("OUTER APPLY", sql, id_)

    def test_cada_parametro_del_control_lechero_busca_el_ultimo_control_que_lo_tiene(self):
        for id_, col in (("LatestFat", "FAT"), ("LatestProtein", "PROTEIN"), ("LatestMilkTestYield", "YIELD"), ("LatestSCC", "SCC")):
            sql = compilar([id_]).upper()
            self.assertIn("TOP 1", sql, id_)
            self.assertIn(f"M.{col} IS NOT NULL", sql, id_)

    def test_la_fecha_de_cada_parametro_es_la_de_su_propio_control(self):
        sql = compilar(["LatestFatDate"]).upper()
        self.assertIn("M.FAT IS NOT NULL", sql)
        self.assertNotIn("M.PROTEIN IS NOT NULL", sql)

    def test_los_de_la_camara_buscan_el_valor_marcado_por_delpro(self):
        self.assertIn("IsValueForLastBcsDryOff = 1", compilar(["LastBcsDryOffValue"]))
        self.assertIn("IsValueForLastCalving = 1", compilar(["LastBcsCalvingValue"]))
        self.assertIn("IsValueForLastBcs190DaysPregnant = 1", compilar(["LastBcsValue190DaysPregnant"]))
        self.assertNotIn("IsValueFor", compilar(["LastBcsValue"]))

    def test_los_tres_valores_marcados_de_la_camara_se_leen_en_una_sola_pasada_por_animal(self):
        """Medido (copia local, 600 vacas): un `TOP 1 ... WHERE marca = 1` por indicador recorre las ~140 filas de la vaca con una búsqueda por fila cada una: 669.000 lecturas de página. Una sola
        pasada con agregados condicionales las toma las tres a la vez: 5.900. La marca es única por animal (393 de 393, 62 de 62 y 27 de 27 en la base del 20/11/2026), así que `MAX` es la fila marcada."""
        sql = compilar(["LastBcsDryOffValue", "LastBcsCalvingValue", "LastBcsValue190DaysPregnant"])
        self.assertEqual(sql.count("FROM BcsDailyData"), 1)
        self.assertNotIn("ORDER BY x.DateAndTime", sql)

    def test_el_cambio_de_rodeo_sale_del_indice_y_comparte_el_join(self):
        sql = compilar(["DaysSinceLastGroupChange", "PreviousGroupNumber", "LastInseminationDate"])
        self.assertEqual(sql.count("LEFT JOIN AnimalLatestHistoryIndex"), 1)
        self.assertIn("EventGroupChange", sql)

    def test_el_parto_anterior_es_el_inicio_de_la_lactancia_anterior(self):
        sql = compilar(["PrevCalvingDate"])
        self.assertIn("HistoryAnimalLactationInfo", sql)
        self.assertIn("LactationNumber - 1", sql)


class TestSoloLosTambosConCamara(unittest.TestCase):
    def setUp(self):
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def ids(self, tiene):
        with mock.patch.object(app, "_tiene_bcs_de", lambda tambo: tiene), mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"):
            j = self.admin.get("/api/informes/campos").get_json()
        return {c["id"] for cat_ in j["categorias"] for c in cat_["campos"]}

    def test_con_camara_se_ofrecen(self):
        ids = self.ids(True)
        for id_ in BCS:
            self.assertIn(id_, ids)

    def test_sin_camara_no_se_ofrecen_y_los_demas_si(self):
        ids = self.ids(False)
        for id_ in BCS:
            self.assertNotIn(id_, ids)
        for id_ in LECHERO + VARIOS + ("CurrentDIM", "InMilk"):
            self.assertIn(id_, ids)

    def ejecutar(self, tiene, defn):
        corridas = []
        with mock.patch.object(app, "_tiene_bcs_de", lambda tambo: tiene), mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"), \
                mock.patch.object(im, "ejecutar", lambda *a, **k: corridas.append(a) or {"columnas": [], "filas": [], "sql": "x"}):
            r = self.admin.post("/api/informes/ejecutar", json=defn)
        return r, corridas

    def test_un_informe_con_un_campo_de_la_camara_en_un_tambo_sin_camara_dice_por_que_no_se_puede(self):
        for defn in ({"columnas": [{"campo": "Number"}, {"campo": "LastBcsValue"}]},
                     {"columnas": [{"campo": "Number"}], "condiciones": [{"campo": "LastBcsDryOffValue", "op": "<", "valor": 3}]},
                     {"columnas": [{"campo": "calc_1"}], "calculados": [{"id": "calc_1", "etiqueta": "Doble", "formula": "[LastBcsValue190DaysPregnant] * 2"}]}):
            r, corridas = self.ejecutar(False, defn)
            self.assertEqual(r.status_code, 400, defn)
            self.assertIn("cámara de condición corporal", r.get_json()["error"])
            self.assertEqual(corridas, [], "no tiene que llegar a la base")

    def test_un_informe_sin_campos_de_la_camara_no_consulta_la_base_para_saber_si_hay_camara(self):
        """El chequeo de la cámara es una consulta a la base: se hace solo si el informe usa un campo de la cámara (si no, cada informe pagaría una consulta de más y una caída de la base lo frenaría)."""
        consultas = []
        with mock.patch.object(app, "_tiene_bcs_de", lambda tambo: consultas.append(tambo) or True), mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"), \
                mock.patch.object(im, "ejecutar", lambda *a, **k: {"columnas": [], "filas": [], "sql": "x"}):
            r = self.admin.post("/api/informes/ejecutar", json={"columnas": [{"campo": "Number"}, {"campo": "LatestFat"}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(consultas, [])

    def test_con_camara_o_sin_campos_de_la_camara_corre_normal(self):
        for tiene, defn in ((True, {"columnas": [{"campo": "Number"}, {"campo": "LastBcsValue"}]}), (False, {"columnas": [{"campo": "Number"}, {"campo": "LatestFat"}]})):
            r, corridas = self.ejecutar(tiene, defn)
            self.assertEqual(r.status_code, 200, defn)
            self.assertEqual(len(corridas), 1)


class TestSaberSiHayCamara(unittest.TestCase):
    """`app._tiene_bcs_de` se guarda por tambo para no consultar la base en cada pedido, pero una falla de la base NO se guarda: si se guardara, un corte de un segundo dejaría los
    campos de la cámara escondidos hasta reiniciar el servidor."""

    def setUp(self):
        app._tiene_bcs.pop("x_camara", None)
        self.addCleanup(lambda: app._tiene_bcs.pop("x_camara", None))

    def consultar(self, resultado):
        def run(sql, tambo=None, **kw):
            if isinstance(resultado, Exception):
                raise resultado
            return {"rows": [[resultado]]}
        with mock.patch.object(db, "run_query", run):
            return app._tiene_bcs_de("x_camara")

    def test_una_falla_de_la_base_no_se_guarda(self):
        self.assertFalse(self.consultar(RuntimeError("sin conexión")))
        self.assertTrue(self.consultar(12345))                      # la base volvió: ahora sí la encuentra

    def test_una_respuesta_buena_se_guarda(self):
        self.assertTrue(self.consultar(12345))
        self.assertTrue(self.consultar(RuntimeError("sin conexión")))    # ya estaba guardado: no vuelve a preguntar
        app._tiene_bcs.pop("x_camara", None)
        self.assertFalse(self.consultar(None))                      # OBJECT_ID nulo = el tambo no tiene la tabla
        self.assertFalse(self.consultar(RuntimeError("sin conexión")))   # y eso también se guarda


if __name__ == "__main__":
    unittest.main()
