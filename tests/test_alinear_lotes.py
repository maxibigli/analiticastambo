"""Los nombres de lote del mapeo guardado y los del proveedor no se escriben igual.

Haasten dice «Rodeo 2» y MixerOne «RODEO 2»; la conciliación guardada de un tambo que cambió de
proveedor sigue con los nombres viejos, y como los lotes se cruzan por nombre exacto, quedaban
sin una sola descarga (medido en La Ponderosa: Rodeo 2, 3, 5 y 9 fuera, 1.105 vacas).

    C:\\Python314\\python.exe -m unittest tests.test_alinear_lotes -v
"""
import unittest

import alimentacion
import conciliacion
import conversion_historica


class TestAlinearLotes(unittest.TestCase):
    def test_lleva_el_nombre_guardado_a_como_lo_escribe_el_proveedor(self):
        r = conciliacion.alinear_lotes({3: "Rodeo 2"}, ["RODEO 1", "RODEO 2"])
        self.assertEqual(r, {3: "RODEO 2"})

    def test_ignora_tildes_y_espacios_de_mas(self):
        r = conciliacion.alinear_lotes({25: "Enfermería  "}, ["ENFERMERIA"])
        self.assertEqual(r, {25: "ENFERMERIA"})

    def test_el_nombre_exacto_no_se_toca(self):
        r = conciliacion.alinear_lotes({3: "RODEO 2"}, ["RODEO 2", "Rodeo 2"])
        self.assertEqual(r, {3: "RODEO 2"})

    def test_si_dos_lotes_del_proveedor_dan_igual_no_se_adivina(self):
        # «Preparto» y «PREPARTO» son dos lotes distintos para el proveedor: elegir uno
        # sería asignar la comida de un corral al otro.
        r = conciliacion.alinear_lotes({19: "preparto"}, ["Preparto", "PREPARTO"])
        self.assertEqual(r, {19: "preparto"})

    def test_un_nombre_que_no_esta_se_deja_como_esta(self):
        r = conciliacion.alinear_lotes({3: "Rodeo 2", 4: "Rodeo 9"}, ["RODEO 2"])
        self.assertEqual(r, {3: "RODEO 2", 4: "Rodeo 9"})

    def test_no_confunde_numeros_distintos(self):
        r = conciliacion.alinear_lotes({3: "Rodeo 2"}, ["RODEO 12"])
        self.assertEqual(r, {3: "Rodeo 2"})


def _tabla(columnas, filas):
    return {"columns": columnas, "rows": filas}


class TestAnalizarConNombresDistintos(unittest.TestCase):
    """El caso real: mapeo con «Rodeo 2», descargas del mixer a nombre de «RODEO 2»."""

    GRUPOS = [{"oid": 3, "numero": 2, "nombre": "Rodeo 2", "cabezas": 300, "es_ordene": True}]

    def _analizar(self, ms, costo=None):
        prod_dia = _tabla(["grupo", "fecha", "vacas", "kg_leche"],
                          [[3, "2026-11-01", 300, 9000.0], [3, "2026-11-02", 300, 9000.0]])
        prod_vaca = _tabla(["rp", "grupo", "kg_leche", "dias", "lactancia", "dim"],
                           [[111, 3, 60.0, 2, 2, 100]])
        solidos = _tabla(["rp", "controles", "grasa", "proteina", "ultimo"],
                         [[111, 1, 3.8, 3.2, "2026-10-20"]])
        import datetime
        d1, d2 = datetime.date(2026, 11, 1), datetime.date(2026, 11, 2)
        ms_lote = {(l, f): v for l, f, v in ms}
        costo_lote = {(l, f): v for l, f, v in (costo or [])}
        return alimentacion.analizar(prod_dia, prod_vaca, solidos, ms_lote, self.GRUPOS,
                                     {3: "Rodeo 2"}, costo_lote_dia=costo_lote, precio_litro=500.0)

    def test_la_conversion_encuentra_las_descargas_aunque_cambie_la_mayuscula(self):
        import datetime
        d1, d2 = datetime.date(2026, 11, 1), datetime.date(2026, 11, 2)
        r = self._analizar([("RODEO 2", d1, 7500.0), ("RODEO 2", d2, 7500.0)])
        self.assertEqual([g["grupo"] for g in r["grupos"]], ["Rodeo 2"])
        self.assertEqual(r["grupos"][0]["dias"], 2)
        # y el lote que se muestra es el del proveedor, que es el que se puede buscar allá
        self.assertEqual(r["grupos"][0]["lote"], "RODEO 2")
        self.assertEqual(r["cobertura"]["fuera"], [])

    def test_el_costo_tambien_se_cruza(self):
        import datetime
        d1, d2 = datetime.date(2026, 11, 1), datetime.date(2026, 11, 2)
        r = self._analizar([("RODEO 2", d1, 7500.0), ("RODEO 2", d2, 7500.0)],
                           costo=[("RODEO 2", d1, 900000.0), ("RODEO 2", d2, 900000.0)])
        self.assertEqual(r["grupos"][0]["dias_con_costo"], 2)
        self.assertTrue(r["economia"]["hay_costo"])


class TestPorQueNoHayCosto(unittest.TestCase):
    """«Sin datos todavía» tiene que decir la causa REAL. Medido en La Ponderosa con MixerOne: la
    planilla tenía 20 insumos pero con los nombres de Haasten («silo de maiz», «harina soja»), el
    mixer manda «Silo Maiz 1 24-25», «Harina de Soja»: cobertura del 18 %, ninguna receta se
    valoriza, y la pantalla decía «No hay planilla de precios cargada»."""

    def _falta(self, diagnostico):
        import datetime
        d1 = datetime.date(2026, 11, 1)
        prod_dia = _tabla(["grupo", "fecha", "vacas", "kg_leche"], [[3, "2026-11-01", 300, 9000.0]])
        prod_vaca = _tabla(["rp", "grupo", "kg_leche", "dias", "lactancia", "dim"], [[111, 3, 30.0, 1, 2, 100]])
        solidos = _tabla(["rp", "controles", "grasa", "proteina", "ultimo"], [[111, 1, 3.8, 3.2, "2026-10-20"]])
        grupos = [{"oid": 3, "numero": 2, "nombre": "Rodeo 2", "cabezas": 300, "es_ordene": True}]
        r = alimentacion.analizar(prod_dia, prod_vaca, solidos, {("RODEO 2", d1): 7500.0}, grupos,
                                  {3: "RODEO 2"}, diagnostico, costo_lote_dia={}, precio_litro=525.0)
        return r["economia"]["falta"]

    def test_planilla_con_otros_nombres_lo_dice_y_nombra_los_ingredientes(self):
        falta = self._falta({"precios": {"insumos": 20},
                             "ingredientes_sin_precio": [
                                 {"ingrediente": "Silo Maiz 1 24-25", "kg": 355320},
                                 {"ingrediente": "Harina de Soja", "kg": 70630}]})
        self.assertNotIn("No hay planilla", falta)
        self.assertIn("20 insumos", falta)
        self.assertIn("Silo Maiz 1 24-25", falta)
        self.assertIn("Harina de Soja", falta)

    def test_sin_planilla_sigue_diciendo_que_no_hay_planilla(self):
        self.assertIn("No hay planilla", self._falta({}))
        self.assertIn("No hay planilla", self._falta({"precios": {"insumos": 0}}))


class TestConciliacionConNombresDistintos(unittest.TestCase):
    GRUPOS = [{"oid": 3, "numero": 2, "nombre": "Rodeo 2", "cabezas": 300, "es_ordene": True}]

    def test_el_lote_mapeado_y_el_del_proveedor_son_una_sola_fila(self):
        mapeo = {"lotes": [{"lote": "Rodeo 2", "grupos": [3], "nota": ""}]}
        lotes = [{"lote": "RODEO 2", "cabezas": 310, "activo": True}]
        r = conciliacion.analizar(self.GRUPOS, lotes, mapeo, {"RODEO 2": 1000.0})
        self.assertEqual([f["lote"] for f in r["lotes"]], ["RODEO 2"])
        fila = r["lotes"][0]
        self.assertTrue(fila["en_proveedor"])
        self.assertEqual([g["oid"] for g in fila["grupos"]], [3])
        self.assertEqual(r["grupos"][0]["lote"], "RODEO 2")
        self.assertFalse(r["grupos"][0]["falta_lote"])


class TestHistoricaConNombresDistintos(unittest.TestCase):
    def test_la_serie_semanal_no_queda_vacia_por_una_mayuscula(self):
        import datetime
        dias = [datetime.date(2026, 11, 2) + datetime.timedelta(days=i) for i in range(7)]
        prod = _tabla(["grupo", "fecha", "vacas", "kg_leche", "nombre"],
                      [[3, d.isoformat(), 300, 9000.0, "Rodeo 2"] for d in dias])
        solidos = _tabla(["mes", "grupo", "kg_leche", "kg_solidos", "vacas"], [])
        ms = {("RODEO 2", d): 7500.0 for d in dias}
        r = conversion_historica.armar(prod, ms, {3: "Rodeo 2"}, solidos,
                                       datetime.date(2026, 11, 10), grupos_ordene=[3])
        self.assertEqual([s["semana"] for s in r["semanas"]], ["2026-S45"])
        self.assertEqual(r["descartes"], [])


if __name__ == "__main__":
    unittest.main()
