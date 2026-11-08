"""Pruebas de `balance_leche` (sala DelPro -> tanque Milky -> camión La Serenísima).

Correr desde la carpeta del proyecto:
    C:\\Python314\\python.exe -m unittest discover -s tests -v
Los números salen de la medición real del 17/10 al 02/11/2026 (ver medir_factor_litros.py).
"""
import datetime
import decimal
import unittest

import balance_leche as bl


def _kg(fecha, kg, vacas):
    return {"fecha": fecha, "kg": kg, "vacas": vacas}


def _milky(venta, total=None):
    return {"venta": venta, "total": venta if total is None else total}


class TestCalcular(unittest.TestCase):
    def test_dia_sin_cerrar(self):
        kg_dia = [_kg("2026-10-30", 69374, 1642), _kg("2026-10-31", 71070, 1676),
                  _kg("2026-11-01", 70524, 1678), _kg("2026-11-02", 68402, 1608),
                  _kg("2026-11-03", 95, 0)]
        milky = {f: _milky(70000, 73000) for f in ("2026-10-30", "2026-10-31", "2026-11-01",
                                                   "2026-11-02", "2026-11-03")}
        r = bl.calcular(kg_dia, milky, None, 1.05)
        # mediana de vacas de los días con vacas = (1642+1676)/2 -> piso = 90 %
        self.assertAlmostEqual(r["piso_vacas"], 0.9 * 1659.0, places=3)
        ultimo = [d for d in r["dias"] if d["fecha"] == "2026-11-03"][0]
        self.assertIs(ultimo["cerrado"], False)
        self.assertIsNone(ultimo["desvio_sala_tanque_pct"])
        self.assertIs(ultimo["alerta_sala_tanque"], False)

    def test_piso_exacto(self):
        kg_dia = [_kg("2026-10-01", 1, 1640), _kg("2026-10-02", 1, 1650), _kg("2026-10-03", 1, 1660),
                  _kg("2026-10-04", 1, 1600), _kg("2026-10-05", 95, 0)]
        r = bl.calcular(kg_dia, {}, None, 1.05)
        self.assertEqual(r["piso_vacas"], 1480.5)   # mediana 1645 x 0,90

    def test_desvio_sala_tanque(self):
        kg_dia = [_kg("2026-10-21", 68729, 1623), _kg("2026-11-02", 68402, 1608)]
        milky = {"2026-10-21": _milky(71044, 73319), "2026-11-02": _milky(71990, 74127)}
        r = bl.calcular(kg_dia, milky, None, 1.05)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-11-02"]["lts_delpro"], 71822)
        self.assertEqual(d["2026-11-02"]["desvio_sala_tanque_pct"], 3.2)
        self.assertIs(d["2026-11-02"]["alerta_sala_tanque"], True)
        self.assertEqual(d["2026-10-21"]["desvio_sala_tanque_pct"], 1.6)
        self.assertIs(d["2026-10-21"]["alerta_sala_tanque"], False)

    def test_desvio_tanque_camion(self):
        r = bl.calcular([], {"2026-10-20": _milky(71000)}, {"2026-10-20": 70500}, 1.05)
        d = r["dias"][0]
        self.assertEqual(d["desvio_tanque_camion_pct"], 0.7)
        self.assertIs(d["alerta_tanque_camion"], False)
        self.assertIs(r["comprador_disponible"], True)

    def test_fecha_corrida_25_26_oct(self):
        milky = {"2026-10-25": _milky(72690), "2026-10-26": _milky(69949)}
        comprador = {"2026-10-25": 47800, "2026-10-26": 95800}
        r = bl.calcular([], milky, comprador, 1.05)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-10-25"]["desvio_tanque_camion_pct"], 52.1)
        self.assertEqual(d["2026-10-26"]["desvio_tanque_camion_pct"], -27.0)
        for f in ("2026-10-25", "2026-10-26"):
            self.assertIs(d[f]["corrida_tanque_camion"], True)
            self.assertIs(d[f]["alerta_tanque_camion"], False)

    def test_desvio_grande_sin_par_no_es_corrida(self):
        milky = {"2026-10-25": _milky(72690), "2026-10-26": _milky(70070)}
        comprador = {"2026-10-25": 47800, "2026-10-26": 70000}
        r = bl.calcular([], milky, comprador, 1.05)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-10-26"]["desvio_tanque_camion_pct"], 0.1)
        self.assertIs(d["2026-10-25"]["alerta_tanque_camion"], True)
        self.assertIs(d["2026-10-25"]["corrida_tanque_camion"], False)

    def test_ruido_normal_no_es_corrida(self):
        """01/11 (-1,2 %) y 02/11 (+3,2 %) reales: signos opuestos y suma chica en litros, pero el 01/11
        es ruido normal. Si eso contara como fecha corrida taparía el desvío real del 02/11."""
        kg_dia = [_kg("2026-11-01", 70524, 1678), _kg("2026-11-02", 68402, 1608)]
        milky = {"2026-11-01": _milky(71066, 73141), "2026-11-02": _milky(71990, 74127)}
        d = {x["fecha"]: x for x in bl.calcular(kg_dia, milky, None, 1.05)["dias"]}
        self.assertEqual(d["2026-11-01"]["desvio_sala_tanque_pct"], -1.2)
        self.assertIs(d["2026-11-02"]["alerta_sala_tanque"], True)
        self.assertIs(d["2026-11-02"]["corrida_sala_tanque"], False)

    def test_sin_milky_o_sin_delpro(self):
        kg_dia = [_kg("2026-10-01", 70000, 1640), _kg("2026-10-02", 70000, 1640)]
        milky = {"2026-10-02": _milky(70000, 73000), "2026-10-03": _milky(70000, 73000)}
        r = bl.calcular(kg_dia, milky, None, 1.05)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertIsNone(d["2026-10-01"]["desvio_sala_tanque_pct"])     # DelPro sin Milky
        self.assertIsNone(d["2026-10-03"]["desvio_sala_tanque_pct"])     # Milky sin DelPro
        self.assertIsNotNone(d["2026-10-02"]["desvio_sala_tanque_pct"])

    def test_sin_comprador(self):
        r = bl.calcular([], {"2026-10-20": _milky(71000)}, None, 1.05)
        self.assertIs(r["comprador_disponible"], False)
        self.assertTrue(all(x["desvio_tanque_camion_pct"] is None for x in r["dias"]))

    def test_vacio(self):
        r = bl.calcular([], {}, None, 1.05)
        self.assertEqual(r["dias"], [])
        self.assertIsNone(r["tramo_mayor"])

    def test_totales_y_tramo_mayor(self):
        kg_dia = [_kg("2026-10-01", 1000, 1600)]
        r = bl.calcular(kg_dia, {"2026-10-01": _milky(1000, 1060.5)}, {"2026-10-01": 1002}, 1.05)
        t = r["totales"]
        self.assertEqual(t["desvio_sala_tanque_pct"], 1.0)
        self.assertEqual(t["desvio_tanque_camion_pct"], -0.2)
        self.assertEqual(r["tramo_mayor"], "sala_tanque")


class TestPlataYOtrosDestinos(unittest.TestCase):
    """Diferencia tanque -> camión valuada con el precio de la leche (una ESTIMACIÓN que supone que se pudiera
    recuperar) y litros que salieron del tanque sin ir al comprador (guachera)."""

    def test_diferencia_en_litros_y_en_plata(self):
        r = bl.calcular([], {"2026-10-20": _milky(71000, 72500)}, {"2026-10-20": 70500}, 1.05, precio_litro=600)
        d = r["dias"][0]
        self.assertEqual(d["lts_dif_camion"], 500.0)          # el tanque vendió 500 L más de lo que se reconoció
        self.assertEqual(d["valor_dif_camion"], 300000)
        self.assertEqual(r["totales"]["lts_dif_camion"], 500)
        self.assertEqual(r["totales"]["valor_dif_camion"], 300000)
        self.assertEqual(r["precio_litro"], 600)

    def test_el_comprador_reconoce_mas_da_negativo(self):
        r = bl.calcular([], {"2026-10-20": _milky(70000)}, {"2026-10-20": 70200}, 1.05, precio_litro=500)
        d = r["dias"][0]
        self.assertEqual(d["lts_dif_camion"], -200.0)
        self.assertEqual(d["valor_dif_camion"], -100000)

    def test_sin_precio_no_se_inventa_plata(self):
        for precio in (None, 0):
            r = bl.calcular([], {"2026-10-20": _milky(71000)}, {"2026-10-20": 70500}, 1.05, precio_litro=precio)
            self.assertEqual(r["dias"][0]["lts_dif_camion"], 500.0)     # los litros salen igual
            self.assertIsNone(r["dias"][0]["valor_dif_camion"])
            self.assertEqual(r["totales"]["lts_dif_camion"], 500)
            self.assertIsNone(r["totales"]["valor_dif_camion"])
            self.assertIsNone(r["precio_litro"])

    def test_sin_comprador_no_hay_diferencia_pero_si_otros_destinos(self):
        r = bl.calcular([], {"2026-10-20": _milky(71000, 72000)}, None, 1.05, precio_litro=600)
        d = r["dias"][0]
        self.assertIsNone(d["lts_dif_camion"])
        self.assertIsNone(d["valor_dif_camion"])
        self.assertIsNone(r["totales"]["lts_dif_camion"])
        self.assertIsNone(r["totales"]["valor_dif_camion"])
        self.assertEqual(d["lts_otros_destinos"], 1000.0)
        self.assertEqual(r["totales"]["lts_otros_destinos"], 1000)

    def test_dia_sin_dato_del_comprador_no_entra_en_la_diferencia(self):
        milky = {"2026-10-20": _milky(70000), "2026-10-21": _milky(70000)}
        r = bl.calcular([], milky, {"2026-10-20": 69000}, 1.05, precio_litro=100)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertIsNone(d["2026-10-21"]["lts_dif_camion"])
        self.assertEqual(r["totales"]["lts_dif_camion"], 1000)          # solo el 20/10, que tiene el par
        self.assertEqual(r["totales"]["valor_dif_camion"], 100000)

    def test_fecha_corrida_se_compensa_en_el_total(self):
        """25/26 de octubre reales: +24.890 L un día y -25.851 L el otro. En el período es -961 L, no 50.000 L."""
        milky = {"2026-10-25": _milky(72690), "2026-10-26": _milky(69949)}
        comprador = {"2026-10-25": 47800, "2026-10-26": 95800}
        r = bl.calcular([], milky, comprador, 1.05, precio_litro=10)
        self.assertEqual(r["totales"]["lts_dif_camion"], -961)
        self.assertEqual(r["totales"]["valor_dif_camion"], -9610)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-10-25"]["lts_dif_camion"], 24890.0)
        self.assertIs(d["2026-10-25"]["corrida_tanque_camion"], True)

    def test_otros_destinos_es_total_menos_venta(self):
        milky = {"2026-10-20": _milky(1000, 1060), "2026-10-21": _milky(2000, 2000)}
        r = bl.calcular([], milky, None, 1.05)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-10-20"]["lts_otros_destinos"], 60.0)
        self.assertEqual(d["2026-10-21"]["lts_otros_destinos"], 0.0)
        self.assertEqual(r["totales"]["lts_otros_destinos"], 60)
        self.assertEqual(r["totales"]["pct_otros_destinos"], 2.0)       # 60 / 3060 del tanque

    def test_dia_sin_cargas_del_tanque_no_tiene_otros_destinos(self):
        r = bl.calcular([_kg("2026-10-20", 70000, 1640)], {}, None, 1.05)
        self.assertIsNone(r["dias"][0]["lts_otros_destinos"])
        self.assertIsNone(r["totales"]["lts_otros_destinos"])
        self.assertIsNone(r["totales"]["pct_otros_destinos"])

    def test_sin_tanque_no_hay_otros_destinos(self):
        r = bl.calcular([_kg("2026-10-20", 70000, 1640)], {}, {"2026-10-20": 71000}, 1.05, con_tanque=False)
        self.assertIsNone(r["dias"][0]["lts_otros_destinos"])
        self.assertIsNone(r["dias"][0]["lts_dif_camion"])
        self.assertIsNone(r["totales"]["valor_dif_camion"])


class TestEntradas(unittest.TestCase):
    def test_litros_milky_por_dia(self):
        cargas = [
            {"fecha": "26/10/2026 07:22:15", "lts_cicla": 20651, "es_venta": True},
            {"fecha": "26/10/2026 15:22:40", "lts_cicla": 24676, "es_venta": True},
            {"fecha": "26/10/2026 18:00:00", "lts_cicla": 700, "es_venta": False},
            {"fecha": "26/10/2026 19:00:00", "lts_cicla": None, "es_venta": True},
        ]
        r = bl.litros_milky_por_dia(cargas)
        self.assertEqual(r, {"2026-10-26": {"venta": 45327.0, "total": 46027.0}})

    def test_litros_comprador_por_dia(self):
        r = bl.litros_comprador_por_dia([{"fecha": "25/10/2026", "litros": 47800},
                                         {"fecha": "25/10/2026", "litros": 100},
                                         {"fecha": "26/10/2026", "litros": None}])
        self.assertEqual(r, {"2026-10-25": 47900.0})

    def test_fechas_del_comprador_sin_ceros(self):
        """La Serenísima publica '1/11/2026', sin ceros (ver app._fecha_ar_a_iso)."""
        r = bl.litros_comprador_por_dia([{"fecha": "1/11/2026", "litros": 70000},
                                         {"fecha": "9/7/2026", "litros": 5}])
        self.assertEqual(r, {"2026-11-01": 70000.0, "2026-07-09": 5.0})
        r = bl.calcular([], {"2026-11-01": _milky(70000), "2026-11-02": _milky(70000)},
                        bl.litros_comprador_por_dia([{"fecha": "1/11/2026", "litros": 70000},
                                                     {"fecha": "2/11/2026", "litros": 70000}]), 1.05)
        self.assertEqual([d["desvio_tanque_camion_pct"] for d in r["dias"]], [0.0, 0.0])

    def test_filas_kg(self):
        data = {"columns": ["fecha", "kg", "vacas"],
                "rows": [[datetime.date(2026, 11, 2), decimal.Decimal("68402.5"), 1608],
                         ["2026-11-03 00:00:00", None, None]]}
        self.assertEqual(bl.filas_kg(data), [_kg("2026-11-02", 68402.5, 1608), _kg("2026-11-03", 0.0, 0)])

    def test_sql_kg_diarios(self):
        sql = bl.sql_kg_diarios(datetime.date(2026, 10, 17), datetime.date(2026, 11, 2))
        self.assertIn("AnimalDaily", sql)
        # Los kg incluyen las lecturas no válidas (leche real que va al tanque): el filtro de
        # IsYieldValid = 1 ya no va en el WHERE, solo en el conteo de vacas del día cerrado.
        self.assertNotIn("AND ad.IsYieldValid = 1 AND ad.TotalYield > 0", sql)
        self.assertIn("ad.TotalYield > 0", sql)
        self.assertIn("CASE WHEN ad.IsYieldValid = 1 AND b.Number > 0 THEN b.OID", sql)
        self.assertIn("2026-10-17", sql)
        self.assertIn("2026-11-02", sql)
        self.assertNotIn("HAVING", sql)
        import db
        db.validate_sql(sql)   # solo SELECT: no lanza


class TestFactor(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from unittest import mock
        import configuracion_tambo
        self._dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(configuracion_tambo, "_RUTA", os.path.join(self._dir.name, "cfg.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.cfg = configuracion_tambo

    def test_defecto(self):
        self.assertEqual(bl.factor_de("t"), 1.02)

    def test_guardar_con_coma(self):
        self.assertEqual(bl.guardar_factor("t", "1,06"), 1.06)
        self.assertEqual(bl.factor_de("t"), 1.06)

    def test_invalidos_no_pisan_lo_guardado(self):
        bl.guardar_factor("t", 1.06)
        for malo in (0, -1, "abc", 1e9, float("nan"), 0.49, 2.01, None, ""):
            with self.assertRaises(ValueError, msg=repr(malo)):
                bl.guardar_factor("t", malo)
        self.assertEqual(bl.factor_de("t"), 1.06)

    def test_guardar_otros_campos_no_pierde_el_factor(self):
        bl.guardar_factor("t", 1.07)
        self.cfg.guardar("t", {"personas": 4})
        self.assertEqual(bl.factor_de("t"), 1.07)

    def test_factor_por_tambo(self):
        bl.guardar_factor("a", 1.06)
        self.assertEqual(bl.factor_de("b"), 1.02)


def _d(fecha, st=None, tc=None, corrida_st=False, corrida_tc=False):
    """Un día del balance con solo lo que la evaluación de alertas necesita."""
    return {"fecha": fecha, "desvio_sala_tanque_pct": st, "desvio_tanque_camion_pct": tc,
            "alerta_sala_tanque": st is not None and abs(st) > 3 and not corrida_st,
            "alerta_tanque_camion": tc is not None and abs(tc) > 3 and not corrida_tc,
            "corrida_sala_tanque": corrida_st, "corrida_tanque_camion": corrida_tc}


class TestAlertas(unittest.TestCase):
    def test_espera_al_dia_siguiente(self):
        con_siguiente = [_d("2026-11-01", st=0.5), _d("2026-11-02", st=3.2), _d("2026-11-03", st=0.4)]
        eventos, nuevos = bl.evaluar_alertas(con_siguiente, {"sala_tanque": "2026-10-31"})
        self.assertEqual([(e["tramo"], e["fecha"], e["desvio_pct"]) for e in eventos],
                         [("sala_tanque", "2026-11-02", 3.2)])
        self.assertEqual(nuevos["sala_tanque"], "2026-11-02")
        # Sin el día siguiente, el 02/11 todavía no se evalúa.
        eventos, nuevos = bl.evaluar_alertas(con_siguiente[:2], {"sala_tanque": "2026-10-31"})
        self.assertEqual(eventos, [])
        self.assertEqual(nuevos["sala_tanque"], "2026-11-01")

    def test_hueco_de_delpro_no_pierde_la_alerta(self):
        """01, 02, 04 y 05/11 con datos y SIN el 03: el 02/11 (+16,7 %) no tiene "día siguiente" pero ya hay días
        posteriores, o sea que nunca lo va a tener. No se puede descartar en silencio una alerta real."""
        dias = [_d("2026-11-01", st=0.5), _d("2026-11-02", st=16.7), _d("2026-11-04", st=0.2),
                _d("2026-11-05", st=0.1)]
        eventos, nuevos = bl.evaluar_alertas(dias, {"sala_tanque": "2026-11-01"})
        self.assertEqual([(e["fecha"], e["desvio_pct"]) for e in eventos], [("2026-11-02", 16.7)])
        self.assertEqual(nuevos["sala_tanque"], "2026-11-04")

    def test_no_repite(self):
        dias = [_d("2026-11-02", st=3.2), _d("2026-11-03", st=0.4)]
        eventos, nuevos = bl.evaluar_alertas(dias, {"sala_tanque": "2026-10-31"})
        self.assertEqual(len(eventos), 1)
        eventos2, nuevos2 = bl.evaluar_alertas(dias, nuevos)
        self.assertEqual(eventos2, [])
        self.assertEqual(nuevos2, nuevos)

    def test_sin_registro_solo_el_ultimo_evaluable(self):
        dias = [_d("2026-10-28", st=4.0), _d("2026-10-29", st=-5.0), _d("2026-10-30", st=6.0),
                _d("2026-10-31", st=0.1)]
        eventos, _ = bl.evaluar_alertas(dias, {})
        self.assertEqual([e["fecha"] for e in eventos], ["2026-10-30"])

    def test_con_registro_todos_los_dias_nuevos(self):
        dias = [_d("2026-10-28", st=0.1), _d("2026-10-29", st=-5.0), _d("2026-10-30", st=6.0),
                _d("2026-10-31", st=0.1)]
        eventos, nuevos = bl.evaluar_alertas(dias, {"sala_tanque": "2026-10-28"})
        self.assertEqual([e["fecha"] for e in eventos], ["2026-10-29", "2026-10-30"])
        self.assertEqual(nuevos["sala_tanque"], "2026-10-30")

    def test_fecha_corrida_no_alerta(self):
        dias = [_d("2026-10-25", tc=52.1, corrida_tc=True), _d("2026-10-26", tc=-27.0, corrida_tc=True),
                _d("2026-10-27", tc=0.2)]
        eventos, nuevos = bl.evaluar_alertas(dias, {"tanque_camion": "2026-10-24"})
        self.assertEqual(eventos, [])
        self.assertEqual(nuevos["tanque_camion"], "2026-10-26")

    def test_tramos_independientes(self):
        dias = [_d("2026-11-02", st=0.1, tc=-4.0), _d("2026-11-03", st=0.1, tc=None)]
        eventos, nuevos = bl.evaluar_alertas(dias, {})
        self.assertEqual(eventos, [])                      # el camión del 03/11 todavía no existe
        self.assertNotIn("tanque_camion", nuevos)
        self.assertEqual(nuevos["sala_tanque"], "2026-11-02")

    def test_texto_alerta(self):
        lineas = bl.texto_alerta([{"tramo": "sala_tanque", "fecha": "2026-11-02", "desvio_pct": 3.2}])
        self.assertEqual(len(lineas), 1)
        self.assertIn("+3,2 %", lineas[0])
        self.assertIn("02/11", lineas[0])
        self.assertIn("sala → tanque", lineas[0])
        dos = bl.texto_alerta([{"tramo": "tanque_camion", "fecha": "2026-11-02", "desvio_pct": -4.0},
                               {"tramo": "tanque_camion", "fecha": "2026-11-03", "desvio_pct": 5.5}])
        self.assertEqual(len(dos), 1)                      # una línea por tramo
        self.assertIn("−4,0 %", dos[0])
        self.assertIn("+5,5 %", dos[0])
        self.assertEqual(bl.texto_alerta([]), [])


class TestRegistroAvisos(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from unittest import mock
        self._dir = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self._dir.name, "avisos.json")
        patcher = mock.patch.object(bl, "_RUTA_AVISOS", self.ruta)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

    def test_ida_y_vuelta_por_tambo(self):
        self.assertEqual(bl.leer_avisados("t"), {})
        bl.guardar_avisados("t", {"sala_tanque": "2026-11-02"})
        bl.guardar_avisados("otro", {"tanque_camion": "2026-10-30"})
        self.assertEqual(bl.leer_avisados("t"), {"sala_tanque": "2026-11-02"})
        self.assertEqual(bl.leer_avisados("otro"), {"tanque_camion": "2026-10-30"})

    def test_archivo_corrupto(self):
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write("{no es json")
        self.assertEqual(bl.leer_avisados("t"), {})
        bl.guardar_avisados("t", {"sala_tanque": "2026-11-02"})     # se recupera al guardar
        self.assertEqual(bl.leer_avisados("t"), {"sala_tanque": "2026-11-02"})


if __name__ == "__main__":
    unittest.main()


class TestCamionDesdeDelpro(unittest.TestCase):
    """Tambo SIN caudalímetro: no hay tanque, y el camión sale de los despachos cargados en DelPro (o del
    comprador). El tramo es sala -> camión, y a lo despachado se le suma la leche de uso interno, que sale
    de la sala pero no va en el camión. Con tanque (La Ponderosa) nada de esto cambia."""

    KG = [_kg("2026-10-20", 60000, 1500), _kg("2026-10-21", 61000, 1500), _kg("2026-10-22", 59000, 1500)]

    def test_sin_tanque_compara_sala_con_camion_mas_uso_interno(self):
        # kg x 1,02 = 61.200 / 62.220 / 60.180 litros esperados
        camion = {"2026-10-20": 58000.0, "2026-10-21": 58900.0, "2026-10-22": 57000.0}
        uso = {"2026-10-20": 3000.0, "2026-10-21": 3200.0, "2026-10-22": 3100.0}
        r = bl.calcular(self.KG, {}, camion, 1.02, uso_interno=uso, con_tanque=False)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertFalse(r["tanque_disponible"])
        self.assertEqual(d["2026-10-20"]["lts_uso_interno"], 3000.0)
        self.assertEqual(d["2026-10-20"]["desvio_sala_camion_pct"], -0.3)     # 61000 / 61200 - 1
        self.assertEqual(d["2026-10-21"]["desvio_sala_camion_pct"], -0.2)     # 62200 / 62220 - 1
        self.assertIsNone(d["2026-10-20"]["desvio_sala_tanque_pct"])
        self.assertIsNone(d["2026-10-20"]["desvio_tanque_camion_pct"])
        self.assertEqual(r["totales"]["desvio_sala_camion_pct"], -0.2)
        self.assertEqual(r["tramo_mayor"], "sala_camion")

    def test_sin_uso_interno_cuenta_cero(self):
        r = bl.calcular(self.KG, {}, {"2026-10-20": 61200.0}, 1.02, con_tanque=False)
        self.assertEqual(r["dias"][0]["desvio_sala_camion_pct"], 0.0)
        self.assertIsNone(r["dias"][0]["lts_uso_interno"])

    def test_con_tanque_no_hay_tramo_sala_camion(self):
        """La Ponderosa: hay Milky, el camión se compara contra el tanque como siempre."""
        milky = {"2026-10-20": _milky(61000, 62000)}
        r = bl.calcular(self.KG, milky, {"2026-10-20": 61000.0}, 1.02, uso_interno={"2026-10-20": 900.0})
        self.assertTrue(r["tanque_disponible"])
        self.assertIsNone(r["dias"][0]["desvio_sala_camion_pct"])
        self.assertIsNone(r["totales"]["desvio_sala_camion_pct"])
        self.assertEqual(r["dias"][0]["desvio_tanque_camion_pct"], 0.0)

    def test_tanque_sin_cargas_en_el_rango_no_pasa_a_modo_sin_tanque(self):
        """Un tambo CON Milky al que no le llegó ninguna carga no es un tambo sin caudalímetro."""
        r = bl.calcular(self.KG, {}, {"2026-10-20": 61200.0}, 1.02)
        self.assertFalse(r["tanque_disponible"])      # sin con_tanque, se deduce de que no hay cargas
        r = bl.calcular(self.KG, {}, {"2026-10-20": 61200.0}, 1.02, con_tanque=True)
        self.assertTrue(r["tanque_disponible"])
        self.assertIsNone(r["dias"][0]["desvio_sala_camion_pct"])

    def test_dia_sin_cerrar_no_tiene_desvio(self):
        kg = self.KG + [_kg("2026-10-23", 95, 0)]
        r = bl.calcular(kg, {}, {"2026-10-23": 58000.0}, 1.02, con_tanque=False)
        ultimo = r["dias"][-1]
        self.assertFalse(ultimo["cerrado"])
        self.assertIsNone(ultimo["desvio_sala_camion_pct"])

    def test_perdida_fuera_del_umbral_alerta(self):
        camion = {"2026-10-20": 57000.0 - 4000, "2026-10-21": 59000.0, "2026-10-22": 57000.0}   # 20/10: -9,3 %
        uso = {f: 3000.0 for f in camion}
        r = bl.calcular(self.KG, {}, camion, 1.02, uso_interno=uso, con_tanque=False)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertTrue(d["2026-10-20"]["alerta_sala_camion"])
        self.assertFalse(d["2026-10-21"]["alerta_sala_camion"])

    def test_fecha_corrida_en_sala_camion(self):
        """El remito de un día se cargó al día siguiente: -8 % y +8 % seguidos se compensan."""
        camion = {"2026-10-20": 58000.0 - 4900, "2026-10-21": 59000.0 + 4900, "2026-10-22": 57000.0}
        uso = {f: 3000.0 for f in camion}
        r = bl.calcular(self.KG, {}, camion, 1.02, uso_interno=uso, con_tanque=False)
        d = {x["fecha"]: x for x in r["dias"]}
        for f in ("2026-10-20", "2026-10-21"):
            self.assertTrue(d[f]["corrida_sala_camion"], f)
            self.assertFalse(d[f]["alerta_sala_camion"], f)

    def test_alerta_y_texto_del_tramo_sala_camion(self):
        dias = [{"fecha": "2026-11-01", "desvio_sala_camion_pct": -6.2, "alerta_sala_camion": True,
                 "corrida_sala_camion": False},
                {"fecha": "2026-11-02", "desvio_sala_camion_pct": 0.3, "alerta_sala_camion": False,
                 "corrida_sala_camion": False}]
        eventos, nuevos = bl.evaluar_alertas(dias, {})
        self.assertEqual([(e["tramo"], e["fecha"], e["desvio_pct"]) for e in eventos],
                         [("sala_camion", "2026-11-01", -6.2)])
        self.assertEqual(nuevos["sala_camion"], "2026-11-01")
        self.assertEqual(bl.texto_alerta(eventos),
                         ["⚖️ Balance de leche: sala → camión se desvió −6,2 % el 01/11 (umbral ±3 %)."])


class TestEntradasDespachos(unittest.TestCase):
    def test_litros_despachos_por_dia(self):
        cargas = [{"fecha": "01/11/2026 16:20:00", "lts_cicla": 8040.0},
                  {"fecha": "01/11/2026 16:30:00", "lts_cicla": 6000.0},
                  {"fecha": "2/11/2026 16:20:00", "lts_cicla": 8032.0},
                  {"fecha": "02/11/2026 17:00:00", "lts_cicla": None}]
        self.assertEqual(bl.litros_despachos_por_dia(cargas), {"2026-11-01": 14040.0, "2026-11-02": 8032.0})

    def test_uso_interno_por_dia(self):
        uso = [{"fecha": "01/11/2026 08:50:00", "litros": 720.0, "uso": 0},
               {"fecha": "01/11/2026 19:00:00", "litros": 80.0, "uso": 1},
               {"fecha": "02/11/2026 08:50:00", "litros": None, "uso": 0}]
        self.assertEqual(bl.uso_interno_por_dia(uso), {"2026-11-01": 800.0})
