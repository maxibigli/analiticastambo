"""Operarios del mixer: cada alerta que detecta el sistema de alimentación se atribuye al operario de la ACCIÓN que la
provocó, y se arma el análisis por operario. Neutral: no sabe de qué proveedor vienen los datos.

La regla que importa (medida en La Ponderosa, 03 al 18/11/2026): atribuir al operario «principal» del viaje le
adjudicaba a quien solo CARGA alertas de DESCARGA que no puede provocar (26 alertas de «descarga fuera del corral» a
alguien que nunca descargó). Cada alerta va a quien hizo la acción que la disparó.

    C:/Python314/python.exe -m unittest tests.test_operarios -v
"""
import unittest

import operarios


def mov(viaje, accion, operario, ingrediente=None, rodeo=None, real=100.0, teorico=100.0):
    return {"viaje": viaje, "accion": accion, "operario": operario, "ingrediente": ingrediente, "rodeo": rodeo,
            "kg_real": real, "kg_teorico": teorico}


def alerta(id_, codigo, accion=None, viaje=None, rodeo=None, insumo=None, operario=None, tipo=None):
    return {"id": id_, "codigo": codigo, "tipo": tipo or f"Tipo {codigo}", "severidad": "warning", "mensaje": f"m{id_}",
            "fecha": "2026-11-13", "hora": "10:00", "viaje": viaje, "rodeo": rodeo, "insumo": insumo,
            "accion": accion, "operario": operario}


# T1: José carga Heno y Cáscara, Daniel descarga en RODEO 4 y RODEO 1.
# T2: Víctor carga, Daniel descarga en RODEO 2.
# T3: Iván carga, José descarga en RODEO 2 (José descarga poco).
MOVS = [
    mov("T1", "carga", "José", ingrediente="Heno", real=520, teorico=500),
    mov("T1", "carga", "José", ingrediente="Cáscara", real=480, teorico=500),
    mov("T1", "descarga", "Daniel", rodeo="RODEO 4"),
    mov("T1", "descarga", "Daniel", rodeo="RODEO 1"),
    mov("T2", "carga", "Víctor", ingrediente="Heno", real=100, teorico=100),
    mov("T2", "descarga", "Daniel", rodeo="RODEO 2"),
    mov("T3", "carga", "Iván", ingrediente="Heno", real=100, teorico=0),          # sin teórico: no entra al desvío
    mov("T3", "descarga", "José", rodeo="RODEO 2"),
]


class TestAtribuir(unittest.TestCase):
    def quien(self, a, movs=MOVS):
        return operarios.atribuir(a, operarios.por_viaje(movs))

    def test_carga_rapida_es_de_quien_cargo_ese_insumo(self):
        self.assertEqual(self.quien(alerta(1, "N06", "carga", "T1", insumo="Cáscara")), "José")

    def test_descarga_fuera_del_corral_es_de_quien_descargo_en_ese_rodeo(self):
        self.assertEqual(self.quien(alerta(2, "N04", "descarga", "T1", rodeo="RODEO 4")), "Daniel")

    def test_una_alerta_de_descarga_no_se_le_adjudica_a_quien_solo_carga(self):
        # T3: Iván carga, José descarga. La alerta de descarga es de José, no de Iván.
        self.assertEqual(self.quien(alerta(3, "N07", "descarga", "T3", rodeo="RODEO 2")), "José")

    def test_una_alerta_de_carga_no_se_le_adjudica_a_quien_solo_descarga(self):
        self.assertEqual(self.quien(alerta(4, "N06", "carga", "T3", insumo="Heno")), "Iván")

    def test_si_el_rodeo_no_coincide_pero_un_solo_operario_descargo_en_el_viaje_es_de_el(self):
        self.assertEqual(self.quien(alerta(5, "N04", "descarga", "T1", rodeo="RODEO 9")), "Daniel")

    def test_si_el_rodeo_no_coincide_y_hay_empate_entre_operarios_no_se_atribuye(self):
        movs = [mov("T9", "descarga", "Ana", rodeo="A"), mov("T9", "descarga", "Beto", rodeo="B")]
        self.assertIsNone(self.quien(alerta(6, "N04", "descarga", "T9", rodeo="C"), movs))

    def test_una_edicion_trae_su_operario_explicito(self):
        self.assertEqual(self.quien(alerta(7, "N02", "edicion", operario="Daniel")), "Daniel")

    def test_sin_accion_no_se_atribuye_aunque_el_viaje_tenga_operarios(self):
        self.assertIsNone(self.quien(alerta(8, "N13", None, "T1", rodeo="RODEO 4")))

    def test_sin_viaje_o_viaje_desconocido_no_se_atribuye(self):
        self.assertIsNone(self.quien(alerta(9, "N12", None, None, rodeo="RODEO 4")))
        self.assertIsNone(self.quien(alerta(10, "N06", "carga", "T-que-no-existe", insumo="Heno")))


ALERTAS = [
    alerta(1, "N06", "carga", "T1", insumo="Cáscara", tipo="Tiempo de carga menor al límite"),
    alerta(2, "N04", "descarga", "T1", rodeo="RODEO 4", tipo="Descarga fuera del corral"),
    alerta(3, "N07", "descarga", "T3", rodeo="RODEO 2", tipo="Inestabilidad de horario"),
    alerta(4, "N02", "edicion", operario="Daniel", tipo="Kilogramos de corral modificados"),
    alerta(5, "N12", None, None, rodeo="RODEO 4", tipo="Consumo: variación en la oferta"),
    alerta(6, "N13", None, "T1", rodeo="RODEO 1", tipo="Corral no alimentado"),
]
PUNTAJES = {"puntaje": 8.74, "porcentajes": {"bien": 64.93, "regular": 25.19, "mal": 9.88},
            "operarios": {"José": {"general": 8.66, "cargas": 8.65, "descargas": 8.93},
                          "Daniel": {"general": 8.99, "cargas": 8.83, "descargas": 9.17}}}


class TestAnalizar(unittest.TestCase):
    def setUp(self):
        self.r = operarios.analizar(MOVS, ALERTAS, PUNTAJES)
        self.por_nombre = {o["nombre"]: o for o in self.r["operarios"]}

    def test_cuenta_las_acciones_de_cada_operario(self):
        j, d = self.por_nombre["José"], self.por_nombre["Daniel"]
        self.assertEqual((j["cargas"], j["descargas"]), (2, 1))
        self.assertEqual((d["cargas"], d["descargas"]), (0, 3))

    def test_las_alertas_van_a_quien_las_provoco_por_tipo(self):
        self.assertEqual(self.por_nombre["José"]["alertas_por_tipo"], {"N06": 1, "N07": 1})
        self.assertEqual(self.por_nombre["Daniel"]["alertas_por_tipo"], {"N04": 1, "N02": 1})
        self.assertEqual(self.por_nombre["José"]["alertas"], 2)
        self.assertNotIn("Víctor", [o["nombre"] for o in self.r["operarios"] if o["alertas"]])

    def test_alertas_cada_100_acciones(self):
        j = self.por_nombre["José"]          # 2 alertas en 3 acciones
        self.assertEqual(j["alertas_cada_100"], 66.7)
        self.assertEqual(self.por_nombre["Víctor"]["alertas_cada_100"], 0)

    def test_un_operario_sin_alertas_igual_figura(self):
        self.assertEqual(self.por_nombre["Víctor"]["alertas"], 0)
        self.assertEqual(self.por_nombre["Iván"]["alertas"], 0)

    def test_el_orden_es_por_cantidad_de_alertas_y_en_empate_alfabetico(self):
        nombres = [o["nombre"] for o in self.r["operarios"]]
        self.assertEqual(nombres[:2], ["Daniel", "José"])          # los dos tienen 2 alertas
        self.assertEqual(nombres[-1], "Víctor")                    # sin alertas, al final, por nombre

    def test_desvio_de_kg_solo_con_las_cargas_que_tienen_teorico(self):
        j = self.por_nombre["José"]          # |520-500| + |480-500| = 40 sobre 1.000
        self.assertEqual(j["desvio_kg_pct"], 4.0)
        self.assertIsNone(self.por_nombre["Iván"]["desvio_kg_pct"])           # su única carga no tiene teórico
        self.assertIsNone(self.por_nombre["Daniel"]["desvio_kg_pct"])         # no carga

    def test_el_rol_sale_de_lo_que_hace(self):
        self.assertEqual(self.por_nombre["Víctor"]["rol"], "Carga")
        self.assertEqual(self.por_nombre["Daniel"]["rol"], "Descarga")
        self.assertEqual(self.por_nombre["José"]["rol"], "Carga y descarga")

    def test_el_puntaje_es_el_del_sistema_y_si_no_lo_trae_va_vacio(self):
        j = self.por_nombre["José"]
        self.assertEqual((j["puntaje"], j["puntaje_cargas"], j["puntaje_descargas"]), (8.66, 8.65, 8.93))
        self.assertIsNone(self.por_nombre["Víctor"]["puntaje"])

    def test_lo_que_no_se_puede_atribuir_va_aparte_y_no_a_un_operario(self):
        s = self.r["sin_operario"]
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["por_tipo"], {"N12": 1, "N13": 1})

    def test_cada_alerta_sale_con_su_operario(self):
        por_id = {a["id"]: a for a in self.r["alertas"]}
        self.assertEqual(por_id[1]["operario"], "José")
        self.assertEqual(por_id[4]["operario"], "Daniel")
        self.assertIsNone(por_id[5]["operario"])

    def test_resumen_y_catalogo_de_tipos(self):
        res = self.r["resumen"]
        self.assertEqual((res["alertas"], res["atribuidas"], res["sin_atribuir"]), (6, 4, 2))
        self.assertEqual(res["puntaje"], 8.74)
        self.assertEqual(res["porcentajes"]["bien"], 64.93)
        self.assertEqual(self.r["tipos"]["N04"], "Descarga fuera del corral")

    def test_sin_puntajes_no_rompe(self):
        r = operarios.analizar(MOVS, ALERTAS, None)
        self.assertIsNone(r["resumen"]["puntaje"])
        self.assertIsNone({o["nombre"]: o for o in r["operarios"]}["José"]["puntaje"])

    def test_sin_nada_devuelve_estructura_vacia(self):
        r = operarios.analizar([], [], None)
        self.assertEqual((r["operarios"], r["alertas"]), ([], []))
        self.assertEqual(r["resumen"]["alertas"], 0)

    def test_un_operario_que_solo_aparece_en_una_alerta_figura_igual(self):
        r = operarios.analizar([], [alerta(1, "N02", "edicion", operario="Fulano")], None)
        self.assertEqual([o["nombre"] for o in r["operarios"]], ["Fulano"])
        self.assertEqual(r["operarios"][0]["alertas"], 1)
        self.assertIsNone(r["operarios"][0]["alertas_cada_100"])         # sin acciones no hay «cada 100»


if __name__ == "__main__":
    unittest.main()
