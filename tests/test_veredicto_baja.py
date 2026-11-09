"""Veredicto del diagnóstico de producción: qué causa apuntar, con qué confianza, y cuándo NO apuntar ninguna.

Los datos de `_datos()` reproducen el caso real del 07/11/2026 de La Ponderosa (informe del tambo): la baja
contra el promedio de la semana era normal (0,7 desvíos), el rinde por vaca era lo que más pesaba, el 3.º ordeño
concentraba la diferencia y el día anterior había tenido 127 tratamientos contra 31,7 de promedio.

    C:\\Python314\\python.exe -m unittest tests.test_veredicto_baja -v
"""
import copy
import unittest

import veredicto_baja as vb


def _datos(**cambios):
    d = {
        "dia": "2026-11-07", "tambo": "ponderosa",
        "base": {"desde": "2026-10-31", "hasta": "2026-11-08", "dias": 7},
        "resumen": {"kg_d": 69225.0, "kg_b": 69900.0, "vac_d": 1712, "vac_b": 1680, "kv_d": 40.4, "kv_b": 41.6,
                    "efecto_vacas": 1330.0, "efecto_rinde": -2005.0, "sd_kg": 946.0, "z_kg": -0.7,
                    "z_vac": 0.4, "z_kv": -2.4, "ayer_kg": 70484.0, "ayer_vacas": 1690, "ayer_es_maximo": True},
        "ausentes": {"n": 7, "kg": 131.0, "por_grupo": [{"grupo": 25, "n": 5, "kg": 98.0}]},
        "caidas": {"n_comunes": 1743, "n_menos": 1005, "mediana": -0.6},
        "grupos": [
            {"grupo": 3, "vacas_d": 329, "vacas_b": 333.4, "kg_d": 15275.0, "kg_b": 16228.0, "delta_kg": -953.0},
            {"grupo": 4, "vacas_d": 353, "vacas_b": 359.9, "kg_d": 14769.0, "kg_b": 15410.0, "delta_kg": -641.0},
            {"grupo": 5, "vacas_d": 255, "vacas_b": 253.4, "kg_d": 4644.0, "kg_b": 5209.0, "delta_kg": -565.0},
            {"grupo": 21, "vacas_d": 362, "vacas_b": 362.9, "kg_d": 13934.0, "kg_b": 13931.0, "delta_kg": 3.0},
            {"grupo": 2, "vacas_d": 429, "vacas_b": 385.6, "kg_d": 20241.0, "kg_b": 18775.0, "delta_kg": 1466.0},
        ],
        "ordenos": [
            {"sesion": 1, "vacas_d": 1747, "vacas_b": 1728, "kg_d": 24825.0, "kg_b": 25606.0,
             "ini_d": "00:07", "ini_b": "00:05", "dur_d": 4.6, "dur_b": 4.2, "cau_d": 26.9, "cau_b": 27.3},
            {"sesion": 2, "vacas_d": 1524, "vacas_b": 1483, "kg_d": 21979.0, "kg_b": 21431.0,
             "ini_d": "07:33", "ini_b": "07:30", "dur_d": 4.1, "dur_b": 3.9, "cau_d": 29.4, "cau_b": 29.5},
            {"sesion": 3, "vacas_d": 1765, "vacas_b": 1729, "kg_d": 24041.0, "kg_b": 25210.0,
             "ini_d": "15:31", "ini_b": "15:28", "dur_d": 4.3, "dur_b": 4.4, "cau_d": 26.5, "cau_b": 27.0},
        ],
        "bocas": {"medidas": 80, "caidas": []},
        "eventos": {"parto": {"dia": 2, "ayer": 19, "base": 8.7},
                    "cambio de grupo": {"dia": 43, "ayer": 14, "base": 21.9},
                    "baja": {"dia": 0, "ayer": 0, "base": 0.9},
                    "tratamiento": {"dia": 13, "ayer": 127, "base": 31.7}},
        "tratamientos_ayer": [{"nombre": "Desvasado Correctivo", "ayer": 98, "base": 10.7}],
        "retiros": {"dia": 10, "base": 9.3},
        "tratadas_ayer": None,
        "lecturas": {"inval_kg_d": 1400.0, "inval_kg_b": 1380.0, "sinid_kg_d": 766.0, "sinid_kg_b": 770.0},
        "ith": {"max_d": 62.4, "max_b": 68.9, "serie": {}},
    }
    for k, v in cambios.items():
        d[k] = v
    return d


def _causa(v, clave):
    return next(c for c in v["causas"] if c["clave"] == clave)


class TestCasoReal(unittest.TestCase):
    """El 07/11: una variación normal que NO hay que presentar como un problema."""

    def setUp(self):
        self.v = vb.veredicto(_datos())

    def test_la_baja_contra_la_base_es_normal_y_el_titular_lo_dice(self):
        self.assertEqual(self.v["nivel"], "normal")
        self.assertIn("dentro de lo normal", self.v["titular"].lower())

    def test_aclara_que_contra_ayer_parece_mas_porque_ayer_fue_el_mejor_dia(self):
        txt = " ".join(self.v["contexto"])
        self.assertIn("ayer", txt.lower())
        self.assertIn("más alto", txt.lower())

    def test_el_calor_se_descarta_con_su_motivo(self):
        c = _causa(self.v, "calor")
        self.assertEqual(c["nivel"], "descartada")
        self.assertTrue(any("más fresco" in e.lower() or "normal" in e.lower() for e in c["evidencia"]))

    def test_faltantes_de_vacas_se_descarta(self):
        self.assertEqual(_causa(self.v, "vacas")["nivel"], "descartada")

    def test_el_rinde_por_vaca_inusual_se_marca_aunque_el_total_sea_normal(self):
        # kg/vaca -2,4 desvíos de lo que viene dando el tambo: eso SÍ es raro, aunque el total no lo sea
        self.assertIn(_causa(self.v, "rinde")["nivel"], ("probable", "posible"))

    def test_el_tercer_ordeno_concentra_la_diferencia(self):
        c = _causa(self.v, "ordeno")
        self.assertEqual(c["nivel"], "posible")
        self.assertIn("3", c["titulo"])

    def test_los_tratamientos_del_dia_anterior_son_una_pista_a_confirmar(self):
        c = _causa(self.v, "manejo")
        self.assertEqual(c["nivel"], "posible")
        self.assertTrue(any("127" in e for e in c["evidencia"]))
        self.assertTrue(any("confirmar" in e.lower() or "no se pudo" in e.lower() for e in c["evidencia"]))

    def test_las_causas_vienen_ordenadas_de_mas_a_menos_probable(self):
        orden = {"probable": 0, "posible": 1, "sin_dato": 2, "descartada": 3}
        niveles = [orden[c["nivel"]] for c in self.v["causas"]]
        self.assertEqual(niveles, sorted(niveles))


class TestCruceDeTratadas(unittest.TestCase):
    def test_si_las_tratadas_bajaron_mas_que_el_resto_es_probable(self):
        d = _datos(tratadas_ayer={"n": 110, "delta_tratadas": -3.1, "delta_resto": -0.2, "efecto_kg": -319.0,
                                  "por_diagnostico": [{"nombre": "Desvasado Correctivo", "n": 98, "delta": -3.3}]})
        c = _causa(vb.veredicto(d), "manejo")
        self.assertEqual(c["nivel"], "probable")
        self.assertTrue(any("-319" in e or "319" in e for e in c["evidencia"]))

    def test_si_las_tratadas_rindieron_igual_que_el_resto_se_descarta(self):
        d = _datos(tratadas_ayer={"n": 110, "delta_tratadas": -0.3, "delta_resto": -0.2, "efecto_kg": -11.0,
                                  "por_diagnostico": []})
        c = _causa(vb.veredicto(d), "manejo")
        self.assertEqual(c["nivel"], "descartada")


class TestBajaReal(unittest.TestCase):
    def test_faltaron_vacas_es_la_causa_probable(self):
        d = _datos()
        d["resumen"].update(kg_d=63000.0, kg_b=70000.0, vac_d=1550, vac_b=1700, kv_d=40.6, kv_b=41.2,
                            efecto_vacas=-6180.0, efecto_rinde=-820.0, z_kg=-3.0, z_vac=-3.5, z_kv=-0.6)
        d["ausentes"] = {"n": 150, "kg": 6200.0, "por_grupo": [{"grupo": 4, "n": 140, "kg": 5800.0}]}
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "claro")
        self.assertEqual(v["causas"][0]["clave"], "vacas")
        self.assertEqual(v["causas"][0]["nivel"], "probable")
        self.assertIn("150", v["titular"])

    def test_baja_sin_ninguna_senal_dice_que_no_hay_causa_clara_y_manda_a_la_racion(self):
        d = _datos()
        d["resumen"].update(kg_d=66000.0, kg_b=70000.0, efecto_vacas=-100.0, efecto_rinde=-3900.0,
                            z_kg=-2.5, z_vac=0.1, z_kv=-0.5)
        d["ordenos"] = [dict(o, kg_d=o["kg_b"] * 0.943) for o in _datos()["ordenos"]]      # repartido parejo
        d["grupos"] = [dict(g, kg_d=g["kg_b"] * 0.943, delta_kg=-g["kg_b"] * 0.057) for g in _datos()["grupos"]]
        d["eventos"]["tratamiento"] = {"dia": 30, "ayer": 30, "base": 31.7}
        d["eventos"]["cambio de grupo"] = {"dia": 20, "ayer": 20, "base": 21.9}
        d["tratamientos_ayer"] = []
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "sin_causa")
        self.assertIn("ración", " ".join(v["contexto"] + [v["titular"]]).lower())

    def test_calor_fuerte_es_probable(self):
        d = _datos()
        d["resumen"].update(kg_d=66500.0, kg_b=70000.0, efecto_vacas=-100.0, efecto_rinde=-3400.0,
                            z_kg=-2.2, z_vac=0.0, z_kv=-2.1)
        d["ith"] = {"max_d": 79.0, "max_b": 69.0, "serie": {}}
        c = _causa(vb.veredicto(d), "calor")
        self.assertEqual(c["nivel"], "probable")

    def test_boca_caida_es_causa_de_equipo(self):
        d = _datos()
        d["bocas"] = {"medidas": 80, "caidas": [{"boca": 12, "ratio": 0.3}, {"boca": 13, "ratio": 0.4},
                                                  {"boca": 14, "ratio": 0.5}]}
        self.assertEqual(_causa(vb.veredicto(d), "equipo")["nivel"], "probable")

    def test_ordeno_corrido_de_horario_es_probable(self):
        d = _datos()
        d["ordenos"][2].update(ini_d="17:10", ini_b="15:28")
        c = _causa(vb.veredicto(d), "ordeno")
        self.assertEqual(c["nivel"], "probable")
        self.assertTrue(any("17:10" in e for e in c["evidencia"]))


class TestSinDatos(unittest.TestCase):
    def test_seccion_que_fallo_figura_sin_dato_no_descartada(self):
        d = _datos(ith=None, bocas=None, ordenos=None)
        v = vb.veredicto(d)
        self.assertEqual(_causa(v, "calor")["nivel"], "sin_dato")
        self.assertEqual(_causa(v, "equipo")["nivel"], "sin_dato")
        self.assertEqual(_causa(v, "ordeno")["nivel"], "sin_dato")

    def test_no_modifica_los_datos_que_recibe(self):
        d = _datos()
        antes = copy.deepcopy(d)
        vb.veredicto(d)
        self.assertEqual(d, antes)

    def test_dia_que_subio_no_se_presenta_como_baja(self):
        d = _datos()
        d["resumen"].update(kg_d=72000.0, kg_b=70000.0, z_kg=2.1, efecto_vacas=500.0, efecto_rinde=1500.0)
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "normal")
        self.assertIn("más que la base", v["titular"])
        self.assertNotIn("baja", v["titular"].lower())


if __name__ == "__main__":
    unittest.main()
