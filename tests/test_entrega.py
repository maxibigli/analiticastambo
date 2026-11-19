"""Entrega de comida por rodeo y por día contra la producción de leche.

La «eficiencia de la entrega» tiene dos caras que se miden aparte: el CUMPLIMIENTO (kg entregados contra los teóricos) y la
PUNTUALIDAD (cuándo llega la primera comida del rodeo contra lo habitual de ESE rodeo). Medido en La Ponderosa (03 al
16/11/2026) el cumplimiento es casi 100 % todos los días, así que solo no dice nada; la puntualidad es lo que más cambia.

Neutral: no sabe de qué sistema vienen los datos. Sin red ni base.

    C:/Python314/python.exe -m unittest tests.test_entrega -v
"""
import unittest

import entrega


def desc(fecha, minuto, rodeo="RODEO 1", real=1000.0, teorico=1000.0):
    return {"fecha": fecha, "minuto": minuto, "rodeo": rodeo, "kg_real": real, "kg_teorico": teorico}


def alerta(fecha, rodeo="RODEO 1", dia=None, codigo="N07"):
    return {"fecha": fecha, "dia": dia, "rodeo": rodeo, "codigo": codigo}


def dias_de(rodeos, rodeo):
    return {d["fecha"]: d for d in next(r for r in rodeos if r["rodeo"] == rodeo)["dias"]}


class TestPorRodeo(unittest.TestCase):
    def test_suma_lo_entregado_y_lo_teorico_del_dia_y_da_el_cumplimiento(self):
        r = entrega.por_rodeo([desc("2026-11-10", 420, real=900, teorico=1000), desc("2026-11-10", 900, real=1050,
                                                                                    teorico=1000)])
        d = dias_de(r, "RODEO 1")["2026-11-10"]
        self.assertEqual((d["kg_real"], d["kg_teorico"], d["descargas"]), (1950.0, 2000.0, 2))
        self.assertEqual(d["cumplimiento"], 97.5)

    def test_sin_teorico_no_hay_cumplimiento(self):
        d = dias_de(entrega.por_rodeo([desc("2026-11-10", 420, teorico=0)]), "RODEO 1")["2026-11-10"]
        self.assertIsNone(d["cumplimiento"])

    def test_la_primera_comida_es_la_descarga_mas_temprana_del_dia(self):
        d = dias_de(entrega.por_rodeo([desc("2026-11-10", 900), desc("2026-11-10", 424), desc("2026-11-10", 700)]),
                    "RODEO 1")["2026-11-10"]
        self.assertEqual(d["primera"], 424)

    def test_una_descarga_sin_kg_no_cuenta_como_comida(self):
        d = dias_de(entrega.por_rodeo([desc("2026-11-10", 300, real=0), desc("2026-11-10", 424)]), "RODEO 1")
        self.assertEqual(d["2026-11-10"]["primera"], 424)
        self.assertEqual(d["2026-11-10"]["descargas"], 1)

    def test_el_atraso_es_contra_la_mediana_del_propio_rodeo(self):
        # Rodeo 1 come a las 07:04 (424); un día llegó a las 09:04 (544) y otro a las 06:44 (404).
        datos = [desc(f"2026-11-0{i}", 424) for i in range(1, 6)] + [desc("2026-11-06", 544), desc("2026-11-07", 404)]
        d = dias_de(entrega.por_rodeo(datos), "RODEO 1")
        self.assertEqual(d["2026-11-06"]["atraso"], 120)
        self.assertEqual(d["2026-11-07"]["atraso"], -20)
        self.assertEqual(d["2026-11-01"]["atraso"], 0)

    def test_cada_rodeo_se_mide_contra_su_propia_hora(self):
        datos = ([desc(f"2026-11-0{i}", 424, "RODEO 1") for i in range(1, 5)]
                 + [desc(f"2026-11-0{i}", 600, "RODEO 3") for i in range(1, 5)])
        r = entrega.por_rodeo(datos)
        self.assertEqual(dias_de(r, "RODEO 3")["2026-11-01"]["atraso"], 0)
        self.assertEqual(next(x for x in r if x["rodeo"] == "RODEO 3")["habitual"], 600)

    def test_con_menos_de_tres_dias_no_hay_lo_habitual_y_el_atraso_va_vacio(self):
        r = entrega.por_rodeo([desc("2026-11-01", 424), desc("2026-11-02", 500)])
        self.assertIsNone(r[0]["habitual"])
        self.assertIsNone(dias_de(r, "RODEO 1")["2026-11-01"]["atraso"])

    def test_las_alertas_se_cuentan_por_rodeo_y_dia(self):
        r = entrega.por_rodeo([desc("2026-11-10", 424), desc("2026-11-11", 424), desc("2026-11-10", 424, "RODEO 2")],
                              [alerta("2026-11-10"), alerta("2026-11-10"), alerta("2026-11-10", "RODEO 2"),
                               alerta("2026-11-11", "RODEO 9")])
        self.assertEqual(dias_de(r, "RODEO 1")["2026-11-10"]["alertas"], 2)
        self.assertEqual(dias_de(r, "RODEO 1")["2026-11-11"]["alertas"], 0)
        self.assertEqual(dias_de(r, "RODEO 2")["2026-11-10"]["alertas"], 1)

    def test_una_alerta_cuenta_en_el_dia_del_que_habla_no_en_el_que_se_detecto(self):
        """«Rodeo sin alimentar»: se detecta al día siguiente, pero es del día que faltó."""
        r = entrega.por_rodeo([desc("2026-11-16", 424), desc("2026-11-18", 424)],
                              [alerta("2026-11-18", dia="2026-11-17", codigo="N13")])
        d = dias_de(r, "RODEO 1")
        self.assertEqual(d["2026-11-17"]["alertas"], 1)
        self.assertEqual(d["2026-11-18"]["alertas"], 0)

    def test_un_dia_sin_comida_con_alerta_figura_con_ceros(self):
        r = entrega.por_rodeo([desc("2026-11-16", 424), desc("2026-11-18", 424)],
                              [alerta("2026-11-18", dia="2026-11-17", codigo="N13")])
        d = dias_de(r, "RODEO 1")["2026-11-17"]
        self.assertEqual((d["descargas"], d["kg_real"], d["primera"], d["cumplimiento"]), (0, 0.0, None, None))

    def test_las_alertas_de_un_rodeo_que_no_comio_en_toda_la_ventana_no_crean_un_rodeo(self):
        r = entrega.por_rodeo([desc("2026-11-10", 424)], [alerta("2026-11-10", "OTRO RODEO")])
        self.assertEqual([x["rodeo"] for x in r], ["RODEO 1"])

    def test_dias_y_rodeos_salen_ordenados(self):
        r = entrega.por_rodeo([desc("2026-11-12", 424, "RODEO 2"), desc("2026-11-10", 424, "RODEO 2"),
                               desc("2026-11-11", 424, "RODEO 1")])
        self.assertEqual([x["rodeo"] for x in r], ["RODEO 1", "RODEO 2"])
        self.assertEqual([d["fecha"] for d in r[1]["dias"]], ["2026-11-10", "2026-11-12"])

    def test_sin_datos_devuelve_vacio(self):
        self.assertEqual(entrega.por_rodeo([]), [])
        self.assertEqual(entrega.por_rodeo(None, None), [])


class TestConLeche(unittest.TestCase):
    RODEOS = entrega.por_rodeo([desc("2026-11-10", 424), desc("2026-11-11", 424), desc("2026-11-12", 424)])

    def test_pega_la_leche_por_vaca_de_cada_dia(self):
        leche = {"RODEO 1": {"2026-11-10": {"vacas": 400, "kg": 19600.0}, "2026-11-11": {"vacas": 410, "kg": 20090.0}}}
        d = dias_de(entrega.con_leche(self.RODEOS, leche, "2026-11-12"), "RODEO 1")
        self.assertEqual((d["2026-11-10"]["vacas"], d["2026-11-10"]["leche_vaca"]), (400, 49.0))
        self.assertEqual(d["2026-11-11"]["leche_vaca"], 49.0)

    def test_un_dia_sin_leche_cargada_queda_vacio(self):
        d = dias_de(entrega.con_leche(self.RODEOS, {"RODEO 1": {}}, "2026-11-12"), "RODEO 1")
        self.assertIsNone(d["2026-11-10"]["leche_vaca"])

    def test_un_dia_posterior_al_ultimo_completo_no_lleva_leche(self):
        """El último día de DelPro viene a medio cargar y hundiría la leche por vaca."""
        leche = {"RODEO 1": {"2026-11-12": {"vacas": 120, "kg": 3000.0}}}
        d = dias_de(entrega.con_leche(self.RODEOS, leche, "2026-11-11"), "RODEO 1")
        self.assertIsNone(d["2026-11-12"]["leche_vaca"])

    def test_un_rodeo_sin_grupo_mapeado_queda_sin_leche_y_se_sabe(self):
        r = entrega.con_leche(self.RODEOS, {}, "2026-11-12")
        self.assertFalse(r[0]["con_leche"])
        self.assertTrue(entrega.con_leche(self.RODEOS, {"RODEO 1": {"2026-11-10": {"vacas": 1, "kg": 1.0}}},
                                          "2026-11-12")[0]["con_leche"])

    def test_no_modifica_lo_que_recibe(self):
        entrega.con_leche(self.RODEOS, {"RODEO 1": {"2026-11-10": {"vacas": 1, "kg": 1.0}}}, "2026-11-12")
        self.assertNotIn("leche_vaca", self.RODEOS[0]["dias"][0])


if __name__ == "__main__":
    unittest.main()
