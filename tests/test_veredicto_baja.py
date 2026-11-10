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
        "ausentes": {"n": 7, "kg": 131.0, "por_grupo": [{"grupo": "Rodeo 9", "n": 5, "kg": 98.0}]},
        "caidas": {"n_comunes": 1743, "n_menos": 1005, "mediana": -0.6},
        "grupos": [
            {"grupo": "Rodeo 3", "vacas_d": 329, "vacas_b": 333.4, "kg_d": 15275.0, "kg_b": 16228.0, "delta_kg": -953.0},
            {"grupo": "Rodeo 4 - Baja", "vacas_d": 353, "vacas_b": 359.9, "kg_d": 14769.0, "kg_b": 15410.0, "delta_kg": -641.0},
            {"grupo": "Rodeo 5", "vacas_d": 255, "vacas_b": 253.4, "kg_d": 4644.0, "kg_b": 5209.0, "delta_kg": -565.0},
            {"grupo": "Rodeo 1", "vacas_d": 362, "vacas_b": 362.9, "kg_d": 13934.0, "kg_b": 13931.0, "delta_kg": 3.0},
            {"grupo": "Rodeo 2", "vacas_d": 429, "vacas_b": 385.6, "kg_d": 20241.0, "kg_b": 18775.0, "delta_kg": 1466.0},
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


def _datos_informe_2():
    """El informe REAL del 07/11/2026 con la comparación 28/10 a 06/11 (base de 10 días): el total salió normal
    (+0,4 %) pero cada vaca rindió 3,2 % menos (4,3 desvíos) y lo compensaron 62 vacas más."""
    d = _datos()
    d["resumen"].update(kg_d=69225.0, kg_b=68919.0, vac_d=1712, vac_b=1650, kv_d=40.4, kv_b=41.8,
                        efecto_vacas=2603.0, efecto_rinde=-2297.0, sd_kg=1507.0, z_kg=0.2, z_vac=1.7, z_kv=-4.3)
    d["caidas"] = {"n_comunes": 1673, "n_menos": 1099, "mediana": -1.1}
    d["ausentes"] = {"n": 12, "kg": 420.0, "n_invalidas": 51, "kg_invalidas": 1847.0,
                     "por_grupo": [{"grupo": "Rodeo 2", "n": 13, "kg": 556.0}]}
    d["tratadas_ayer"] = {"n": 73, "delta_tratadas": -2.3, "delta_resto": -1.0, "efecto_kg": -98.0,
                          "por_diagnostico": [{"nombre": "Desvasado Correctivo", "n": 59, "delta": -1.3}]}
    d["lecturas"] = {"inval_kg_d": 1428.0, "inval_kg_b": 2176.0, "sinid_kg_d": 0.0, "sinid_kg_b": 0.0}
    return d


def _causa(v, clave):
    return next(c for c in v["sintomas"] + v["causas"] if c["clave"] == clave)


class TestCasoReal(unittest.TestCase):
    """El 07/11: una variación normal que NO hay que presentar como un problema."""

    def setUp(self):
        self.v = vb.veredicto(_datos())

    def test_si_el_rinde_por_vaca_es_normal_la_baja_se_dice_dentro_de_lo_normal(self):
        d = _datos()
        d["resumen"]["z_kv"] = -0.3
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "normal")
        self.assertIn("dentro de lo normal", v["titular"].lower())

    def test_total_normal_pero_cada_vaca_rindio_menos_pide_atencion_y_lo_dice_en_el_titular(self):
        """Lo que pasó el 07/11: el dashboard marcaba -1,3 kg por vaca; decir solo 'normal' ocultaba eso."""
        v = vb.veredicto(_datos_informe_2())
        self.assertEqual(v["nivel"], "atencion")
        t = v["titular"]
        self.assertIn("total", t.lower())
        self.assertIn("cada vaca rindió menos", t)
        self.assertIn("62 vacas más", t)                  # lo que compensó
        self.assertNotIn("dentro de lo normal", t.lower())

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

    def test_el_rinde_que_baja_solo_por_mezcla_de_vacas_no_es_que_cada_una_rinda_menos(self):
        """El promedio por vaca puede bajar porque ENTRARON vacas de baja producción, sin que ninguna rinda menos."""
        d = _datos()
        d["resumen"]["z_kv"] = -3.0
        d["caidas"] = {"n_comunes": 1700, "n_menos": 860, "mediana": -0.1}
        c = _causa(vb.veredicto(d), "rinde")
        self.assertEqual(c["nivel"], "posible")
        self.assertTrue(any("mezcla" in e.lower() for e in c["evidencia"]))

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
        d = _datos(tratadas_ayer={"n": 110, "delta_tratadas": -9.0, "delta_resto": -0.2, "efecto_kg": -968.0,
                                  "por_diagnostico": [{"nombre": "Desvasado Correctivo", "n": 98, "delta": -9.3}]})
        c = _causa(vb.veredicto(d), "manejo")
        self.assertEqual(c["nivel"], "probable")
        self.assertTrue(any("968" in e for e in c["evidencia"]))

    def test_si_las_tratadas_bajaron_un_poco_mas_pero_explican_casi_nada_no_es_probable(self):
        """07/11: 73 tratadas -2,3 kg contra -1,0 del resto = -98 kg sobre una baja de rinde de -2.297 kg (4 %)."""
        c = _causa(vb.veredicto(_datos_informe_2()), "manejo")
        self.assertEqual(c["nivel"], "descartada")
        self.assertTrue(any("solo" in e.lower() and "%" in e for e in c["evidencia"]), c["evidencia"])
        self.assertTrue(any("127" in e for e in c["evidencia"]))        # el hecho (127 tratamientos) sigue a la vista

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
        d["ausentes"] = {"n": 150, "kg": 6200.0, "por_grupo": [{"grupo": "Rodeo 4 - Baja", "n": 140, "kg": 5800.0}]}
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


def _alimentacion(dia=None, ayer=None, tarde=None, **base):
    """Lo que mide `alimentacion_dia.evaluar`. Por defecto, el 07/11 real de La Ponderosa: la primera comida a las
    09:52 contra las 06:30 previstas y la 07:04 que es lo normal (168 minutos tarde, a 11 desvíos)."""
    normal = {"fecha": "2026-11-06", "primera": 7 * 60 + 10, "ultima": 17 * 60 + 49, "viajes": 5, "kg": 69385.0,
              "ciclo": "06:30", "retraso": 6, "z": 0.4, "kg_z": -1.7}
    tarde_dia = {"fecha": "2026-11-07", "primera": 9 * 60 + 52, "ultima": 19 * 60 + 2, "viajes": 5, "kg": 85305.0,
                 "ciclo": "06:30", "retraso": 168, "z": 11.2, "kg_z": 0.9}
    return {"dia": tarde_dia if dia is None else dia, "ayer": normal if ayer is None else ayer,
            "base": {"dias": 36, "primera_med": 7 * 60 + 4, "primera_sd": 15.0, "kg_med": 79740.0, "kg_sd": 5600.0,
                     "tarde": [{"fecha": "2026-10-24", "primera": 9 * 60 + 14},
                               {"fecha": "2026-10-29", "primera": 8 * 60 + 47}] if tarde is None else tarde, **base}}


class TestAlimentacion(unittest.TestCase):
    """La comida del mixer: la hora a la que llegó, no solo cuánto se dio. Nunca pasa de «posible»: la API da el
    horario, no cuánta leche costó, y un solo día no alcanza para afirmar causa."""

    def causa(self, **kw):
        return _causa(vb.veredicto(_datos(alimentacion=_alimentacion(**kw))), "alimentacion")

    def test_la_primera_comida_muy_tardia_es_una_causa_posible(self):
        c = self.causa()
        self.assertEqual(c["nivel"], "posible")
        self.assertIn("tarde", c["titulo"].lower())
        txt = " ".join(c["evidencia"])
        self.assertIn("09:52", txt)
        self.assertIn("07:04", txt)
        self.assertIn("2 h 48", txt)               # 168 minutos

    def test_nunca_pasa_de_posible_ni_con_una_comida_tardisima(self):
        tardisima = _alimentacion()["dia"] | {"primera": 13 * 60, "retraso": 356, "z": 23.7}
        self.assertEqual(self.causa(dia=tardisima)["nivel"], "posible")

    def test_dice_cuales_fueron_los_otros_dias_con_comida_tardia(self):
        txt = " ".join(self.causa()["evidencia"])
        self.assertIn("24/10", txt)
        self.assertIn("29/10", txt)
        self.assertIn("rinde", txt.lower())        # invita a mirar si ESOS días también bajó la leche

    def test_si_nunca_hubo_otra_comida_asi_de_tardia_lo_dice(self):
        txt = " ".join(self.causa(tarde=[])["evidencia"])
        self.assertIn("ninguna otra", txt.lower())

    def test_un_dia_normal_se_descarta(self):
        normal = _alimentacion()["ayer"] | {"fecha": "2026-11-07"}
        c = self.causa(dia=normal)
        self.assertEqual(c["nivel"], "descartada")

    def test_una_demora_corta_no_es_una_senal(self):
        corta = _alimentacion()["ayer"] | {"retraso": 40, "z": 2.7}      # 40 min: notable en desvíos, no en horas
        self.assertEqual(self.causa(dia=corta)["nivel"], "descartada")

    def test_una_demora_larga_pero_dentro_de_lo_que_varia_ese_tambo_no_es_una_senal(self):
        ruidoso = _alimentacion()["ayer"] | {"retraso": 75, "z": 1.2}
        self.assertEqual(self.causa(dia=ruidoso)["nivel"], "descartada")

    def test_la_comida_tardia_de_ayer_tambien_cuenta(self):
        ayer_tarde = _alimentacion()["dia"] | {"fecha": "2026-11-06"}
        normal = _alimentacion()["ayer"] | {"fecha": "2026-11-07"}
        c = self.causa(dia=normal, ayer=ayer_tarde)
        self.assertEqual(c["nivel"], "posible")
        self.assertIn("ayer", c["titulo"].lower())

    def test_menos_comida_de_la_habitual_es_una_causa_posible(self):
        poca = _alimentacion()["ayer"] | {"fecha": "2026-11-07", "kg": 60000.0, "kg_z": -3.5}   # 25 % menos que la mediana
        c = self.causa(dia=poca)
        self.assertEqual(c["nivel"], "posible")
        self.assertIn("menos comida", c["titulo"].lower())

    def test_un_dia_algo_mas_flojo_pero_dentro_de_lo_que_varia_no_es_una_senal(self):
        """Los kg del día varían ±15 % en días normales (66 a 89 mil en octubre): 13 % menos no dice nada."""
        flojo = _alimentacion()["ayer"] | {"fecha": "2026-11-07", "kg": 69385.0, "kg_z": -1.7}
        self.assertEqual(self.causa(dia=flojo)["nivel"], "descartada")

    def test_una_caida_de_kg_en_desvios_pero_chica_en_porcentaje_tampoco(self):
        chico = _alimentacion()["ayer"] | {"fecha": "2026-11-07", "kg": 75000.0, "kg_z": -2.5}
        self.assertEqual(self.causa(dia=chico)["nivel"], "descartada")

    def test_sin_comidas_registradas_ese_dia_no_se_descarta(self):
        normal = _alimentacion()["ayer"]
        c = _causa(vb.veredicto(_datos(alimentacion=dict(_alimentacion(ayer=normal), dia=None))), "alimentacion")
        self.assertEqual(c["nivel"], "sin_dato")
        self.assertIn("no registró", " ".join(c["evidencia"]).lower())

    def test_sin_el_dato_del_mixer_figura_sin_dato(self):
        for datos in (_datos(), _datos(alimentacion=None)):
            c = _causa(vb.veredicto(datos), "alimentacion")
            self.assertEqual(c["nivel"], "sin_dato")

    def test_en_el_caso_del_07_11_el_veredicto_la_nombra_como_senal_para_mirar(self):
        d = _datos_informe_2()
        d["alimentacion"] = _alimentacion()
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "atencion")
        self.assertIn("la causa con más peso: la comida llegó tarde (posible)", " ".join(v["contexto"]).lower())

    def test_si_la_baja_es_clara_y_solo_la_comida_la_explica_la_apunta(self):
        d = _datos()
        d["resumen"].update(kg_d=64000.0, kg_b=69900.0, z_kg=-3.0, z_kv=-0.5, z_vac=-0.2, efecto_vacas=-2900.0,
                            efecto_rinde=-3000.0)
        d["ausentes"] = {"n": 0, "kg": 0.0}
        d["caidas"] = {"n_comunes": 1700, "n_menos": 900, "mediana": -0.2}
        d["grupos"] = []
        d["ordenos"] = None
        d["eventos"]["tratamiento"] = {"dia": 13, "ayer": 30, "base": 31.7}
        d["tratamientos_ayer"] = []
        d["alimentacion"] = _alimentacion()
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "posible")
        self.assertIn("comida", v["titular"].lower())


class TestSintomasYCausas(unittest.TestCase):
    """«Cada vaca rindió menos», «el 3.º ordeño concentra la diferencia» y «el grupo 5 rindió menos» son CÓMO SE VE la
    baja, no POR QUÉ pasó. Si compiten como causas ganan siempre (son lo más fuerte que se mide) y tapan la causa."""

    def setUp(self):
        d = _datos_informe_2()
        d["alimentacion"] = _alimentacion()
        self.d = d
        self.v = vb.veredicto(d)

    def test_el_rinde_los_grupos_y_el_ordeno_son_sintomas_no_causas(self):
        self.assertEqual({c["clave"] for c in self.v["sintomas"]}, {"rinde", "grupos", "ordeno"})
        self.assertFalse({"rinde", "grupos", "ordeno"} & {c["clave"] for c in self.v["causas"]})

    def test_las_causas_son_las_que_explican_el_por_que(self):
        self.assertEqual({c["clave"] for c in self.v["causas"]},
                         {"vacas", "equipo", "calor", "manejo", "alimentacion", "mezcla", "lecturas", "retiros"})

    def test_una_baja_que_solo_muestra_sintomas_no_apunta_a_un_sintoma_como_causa(self):
        d = _datos_informe_2()
        d["resumen"].update(kg_d=64000.0, kg_b=69900.0, z_kg=-3.0)
        d["alimentacion"] = None
        d["eventos"]["cambio de grupo"] = {"dia": 20, "ayer": 14, "base": 21.9}       # nada más señala una causa
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "sin_causa")
        self.assertNotIn("cada vaca rindió menos", v["titular"].lower())
        self.assertIn("cómo se ve", " ".join(v["contexto"]).lower())

    def test_el_contexto_dice_como_se_ve_la_baja(self):
        d = _datos_informe_2()
        d["resumen"].update(kg_d=64000.0, kg_b=69900.0, z_kg=-3.0)
        txt = " ".join(vb.veredicto(d)["contexto"])
        self.assertIn("Cómo se ve", txt)
        self.assertIn("Cada vaca rindió menos", txt)


class TestPesoDeLasCausas(unittest.TestCase):
    """El orden: primero el nivel de evidencia; a igual nivel, la causa que ENCAJA con el patrón de la baja (una baja
    pareja en todo el rodeo apunta a algo que afecta a todo el rodeo, como la comida o el calor; una concentrada en
    un grupo o un ordeño, a algo local); después el impacto en kg."""

    def setUp(self):
        self.d = _datos_informe_2()                  # 67 % de las vacas rindió menos y 4 de 5 grupos bajaron: parejo
        self.d["alimentacion"] = _alimentacion()

    def puestos(self, d=None):
        v = vb.veredicto(d or self.d)
        return {c["clave"]: c["puesto"] for c in v["causas"]}, v

    def test_con_una_baja_pareja_la_comida_tardia_va_antes_que_los_cambios_de_grupo(self):
        puestos, v = self.puestos()
        self.assertEqual((puestos["alimentacion"], puestos["mezcla"]), (1, 2))
        self.assertEqual(v["causas"][0]["clave"], "alimentacion")

    def test_dice_por_que_va_primero(self):
        c = vb.veredicto(self.d)["causas"][0]
        self.assertIn("todo el rodeo", c["peso"])
        self.assertIn(f"{round(1099 / 1673 * 100)} %", c["peso"])

    def test_dice_por_que_pesa_menos_la_que_toca_a_pocas_vacas(self):
        mezcla = next(c for c in vb.veredicto(self.d)["causas"] if c["clave"] == "mezcla")
        self.assertIn("pocas", mezcla["peso"])

    def test_con_una_baja_concentrada_gana_la_causa_local(self):
        self.d["caidas"] = {"n_comunes": 1673, "n_menos": 520, "mediana": -0.1}          # solo 31 % rindió menos
        self.d["grupos"] = [g | {"kg_d": g["kg_b"] * 1.0, "vacas_d": g["vacas_b"]} for g in self.d["grupos"]]
        self.d["grupos"][0] |= {"kg_d": self.d["grupos"][0]["kg_b"] * 0.8}               # un solo grupo cae
        puestos, _ = self.puestos()
        self.assertLess(puestos["mezcla"], puestos["alimentacion"])

    def test_la_evidencia_manda_sobre_el_patron(self):
        """Una causa PROBABLE va antes que una posible aunque la posible encaje mejor con el patrón."""
        self.d["eventos"]["tratamiento"] = {"dia": 13, "ayer": 127, "base": 31.7}
        self.d["tratadas_ayer"] = {"n": 400, "delta_tratadas": -4.0, "delta_resto": -0.5, "efecto_kg": -1400.0,
                                   "por_diagnostico": []}
        puestos, v = self.puestos()
        self.assertEqual(_causa(v, "manejo")["nivel"], "probable")
        self.assertEqual(puestos["manejo"], 1)
        self.assertEqual(puestos["alimentacion"], 2)

    def test_las_descartadas_y_las_sin_dato_no_llevan_puesto(self):
        v = vb.veredicto(self.d)
        for c in v["causas"]:
            if c["nivel"] in ("descartada", "sin_dato"):
                self.assertIsNone(c["puesto"], c["clave"])
            else:
                self.assertIsInstance(c["puesto"], int, c["clave"])

    def test_los_puestos_son_consecutivos_desde_1(self):
        v = vb.veredicto(self.d)
        puestos = sorted(c["puesto"] for c in v["causas"] if c["puesto"])
        self.assertEqual(puestos, list(range(1, len(puestos) + 1)))

    def test_sin_datos_para_saber_el_patron_ordena_sin_romper(self):
        self.d["caidas"] = None
        self.d["grupos"] = []
        puestos, _ = self.puestos()
        self.assertEqual(sorted(p for p in puestos.values() if p), [1, 2])

    def test_si_el_rinde_no_bajo_no_hay_patron_que_encajar(self):
        self.d["resumen"]["z_kv"] = 0.2
        v = vb.veredicto(self.d)
        self.assertTrue(all("patrón" not in c["peso"].lower() for c in v["causas"] if c["puesto"]))

    def test_no_modifica_los_datos(self):
        antes = copy.deepcopy(self.d)
        vb.veredicto(self.d)
        self.assertEqual(self.d, antes)


class TestRodeosPorNombre(unittest.TestCase):
    """Todo se dice por RODEO y con el nombre que le da DelPro: «Rodeo 5», no «el grupo 5» ni «grupo 21»."""

    def setUp(self):
        d = _datos_informe_2()
        d["alimentacion"] = _alimentacion()
        self.v = vb.veredicto(d)

    def textos(self, lista):
        return " ".join(c["titulo"] + " " + " ".join(c["evidencia"]) + " " + c.get("revisar", "") for c in lista)

    def test_el_sintoma_por_rodeo_nombra_al_rodeo_sin_la_palabra_grupo(self):
        g = next(c for c in self.v["sintomas"] if c["clave"] == "grupos")
        self.assertEqual(g["titulo"], "Rodeo 5 rindió mucho menos por vaca")
        self.assertIn("Rodeo 3: -953 kg", " ".join(g["evidencia"]))
        self.assertNotRegex(self.textos([g]), r"(?i)\bgrupo")

    def test_las_causas_hablan_de_rodeos(self):
        txt = self.textos(self.v["causas"])
        self.assertIn("Mucho movimiento entre rodeos", txt)
        self.assertIn("Rodeo 2 sumó 43 vacas", txt)
        self.assertNotRegex(txt, r"(?i)\bgrupos?\b")

    def test_el_peso_de_las_causas_cuenta_rodeos_no_grupos(self):
        self.assertIn("rodeos bajaron", self.v["causas"][0]["peso"])

    def test_la_caida_concentrada_en_un_rodeo_lo_nombra(self):
        d = _datos()
        d["grupos"] = [dict(g, kg_d=g["kg_b"], delta_kg=0.0) for g in _datos()["grupos"]]
        d["grupos"][0] = dict(d["grupos"][0], kg_d=d["grupos"][0]["kg_b"] - 2500, delta_kg=-2500.0)
        g = next(c for c in vb.veredicto(d)["sintomas"] if c["clave"] == "grupos")
        self.assertEqual(g["titulo"], "La caída se concentra en Rodeo 3")
        self.assertIn("Rodeo 3 explica", g["evidencia"][0])

    def test_las_vacas_ausentes_dicen_de_que_rodeo_son(self):
        d = _datos()
        d["ausentes"] = {"n": 150, "kg": 6200.0, "por_grupo": [{"grupo": "Rodeo 4 - Baja", "n": 140, "kg": 5800.0}]}
        txt = " ".join(next(c for c in vb.veredicto(d)["causas"] if c["clave"] == "vacas")["evidencia"])
        self.assertIn("sobre todo de Rodeo 4 - Baja", txt)


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
        d["resumen"].update(kg_d=72000.0, kg_b=70000.0, z_kg=2.1, z_kv=0.5, efecto_vacas=500.0, efecto_rinde=1500.0)
        v = vb.veredicto(d)
        self.assertEqual(v["nivel"], "normal")
        self.assertIn("más que la base", v["titular"])
        self.assertNotIn("baja", v["titular"].lower())


if __name__ == "__main__":
    unittest.main()
