"""Vueltas por hora (rotativa) y mangadas por hora (sala convencional): la cuenta y las dos tarjetas del tablero.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_vueltas_mangadas -v

Los números de referencia salen de las copias locales (solo lectura): La Ponderosa, 22 al 29/07/2026, da 4,4 a 5,5
vueltas por hora; La Martina, 11/08/2026, da 30, 30 y 27 mangadas por ordeño (30 puestos por lado).
"""
import datetime
import unittest

import rutina
import tablero
from salas import convencional

T0 = datetime.datetime(2026, 8, 11, 6, 0, 0)


def _t(segundos):
    return T0 + datetime.timedelta(seconds=segundos)


def visita(lado=None, ini=0, fin=60, bloque=None, rotacion=None, kg=20.0):
    return {"lado": lado, "bloque": bloque, "rotacion": rotacion, "kg": kg, "rp": "1", "grupo": 1,
            "puesto": 1, "sin_id": False, "sesion_parlor": None, "turno": None, "retirada_forzada": False,
            "hora_id": _t(ini - 30), "hora_coloc": _t(ini), "hora_fin": _t(fin)}


class TestMangadas(unittest.TestCase):
    def contar(self, visitas):
        return convencional._rotaciones_tandas(visitas, 3600)

    def test_cada_vez_que_un_lado_se_llena_es_una_mangada(self):
        visitas = [visita(1, 0, 300), visita(1, 20, 280), visita(1, 100, 400),     # lado 1, primera mangada
                   visita(1, 700, 1000), visita(1, 720, 990),                      # lado 1, segunda (se vació 5 min)
                   visita(2, 150, 450), visita(2, 200, 500)]                       # lado 2, una
        self.assertEqual(self.contar(visitas), 3)

    def test_un_hueco_de_segundos_dentro_de_la_mangada_no_la_parte(self):
        # El fin de una vaca y el enganche de la siguiente se solapan o se separan por un segundo: es la misma mangada.
        visitas = [visita(1, 0, 100), visita(1, 110, 200), visita(1, 205, 300)]
        self.assertEqual(self.contar(visitas), 1)

    def test_el_hueco_minimo_es_el_piso_de_la_rutina(self):
        justo = rutina.UMBRAL_HUECO_MIN_S
        self.assertEqual(self.contar([visita(1, 0, 100), visita(1, 100 + justo, 200)]), 2)
        self.assertEqual(self.contar([visita(1, 0, 100), visita(1, 100 + justo - 1, 200)]), 1)

    def test_el_numero_de_bloque_de_la_maquina_no_cambia_la_cuenta(self):
        # El bloque se fragmenta (reaparece): contarlo daba 35, 30 y 27 en tres ordeños parecidos.
        visitas = [visita(1, 0, 300, bloque=1), visita(1, 50, 350, bloque=7), visita(1, 100, 400, bloque=3),
                   visita(2, 200, 500, bloque=2), visita(2, 250, 520, bloque=9)]
        self.assertEqual(self.contar(visitas), 2)

    def test_sin_horas_de_fin_vuelve_a_contar_tandas(self):
        sin_horas = []
        for lado, bloque in ((1, 1), (1, 1), (1, 2), (2, 1)):
            v = visita(lado, 0, 60, bloque=bloque)
            v["hora_coloc"] = v["hora_fin"] = None
            sin_horas.append(v)
        self.assertEqual(self.contar(sin_horas), 3)

    def test_sin_dato_de_lado_no_hay_cuenta(self):
        self.assertIsNone(self.contar([visita(None, 0, 60), visita(None, 100, 160)]))


class TestPorHora(unittest.TestCase):
    def sesion(self, vueltas, horas):
        paso = horas * 3600 / vueltas
        return [visita(rotacion=r + 1, ini=int(r * paso), fin=int(r * paso + 60)) for r in range(vueltas)]

    def test_la_sesion_trae_las_vueltas_por_hora(self):
        # 24 vueltas del 1 al 24 en ~4,8 h: el rango de La Ponderosa.
        resumen = rutina._resumen_sesion_rendimiento(self.sesion(24, 4.8))
        self.assertEqual(resumen["n_rotaciones"], 24)
        esperado = round(24 / (resumen["duracion_seg"] / 3600), 1)
        self.assertEqual(resumen["rotaciones_por_hora"], esperado)
        self.assertTrue(4.8 <= resumen["rotaciones_por_hora"] <= 5.3)

    def test_sin_vueltas_no_hay_por_hora(self):
        resumen = rutina._resumen_sesion_rendimiento([visita(ini=0, fin=60), visita(ini=100, fin=160)],
                                                     rotaciones_fn=lambda visitas, duracion: None)
        self.assertIsNone(resumen["n_rotaciones"])
        self.assertIsNone(resumen["rotaciones_por_hora"])

    def test_promedio_simple_entre_sesiones(self):
        sesiones = [{"rotaciones_por_hora": 5.0}, {"rotaciones_por_hora": 4.6}, {"rotaciones_por_hora": None}]
        self.assertEqual(rutina.promedio_rotaciones_por_hora(sesiones), (4.8, 2))

    def test_sin_sesiones_con_dato(self):
        self.assertEqual(rutina.promedio_rotaciones_por_hora([]), (None, 0))
        self.assertEqual(rutina.promedio_rotaciones_por_hora([{"rotaciones_por_hora": None}]), (None, 0))


class TestTarjetas(unittest.TestCase):
    def indicador(self, clave):
        return next(i for i in tablero.INDICADORES if i["clave"] == clave)

    def test_valores_de_partida(self):
        v = self.indicador("vueltas_hora")
        self.assertEqual((v["direccion"], v["verde"], v["rojo"], v["sala"], v["grupo"]),
                         ("alto_mejor", 5, 4, "rotativa", "Ordeño"))
        m = self.indicador("mangadas_hora")
        self.assertEqual((m["direccion"], m["verde"], m["rojo"], m["sala"], m["grupo"]),
                         ("alto_mejor", 6, 4.5, "convencional", "Ordeño"))

    def test_las_dos_llevan_al_gráfico_de_rendimiento_sala(self):
        for clave in ("vueltas_hora", "mangadas_hora"):
            i = self.indicador(clave)
            self.assertEqual((i["fuente"], i["pagina"], i["ancla"]), ("rendimiento", "rendimiento", "rend-vueltas"))

    def test_cada_tambo_ve_solo_la_suya(self):
        valores = {"vueltas_hora": {"valor": 4.9}, "mangadas_hora": {"valor": 5.6}}
        rot = {t["clave"] for t in tablero.armar(valores, sala="rotativa")["tarjetas"]}
        conv = {t["clave"] for t in tablero.armar(valores, sala="convencional")["tarjetas"]}
        self.assertIn("vueltas_hora", rot)
        self.assertNotIn("mangadas_hora", rot)
        self.assertIn("mangadas_hora", conv)
        self.assertNotIn("vueltas_hora", conv)

    def test_sin_decir_la_sala_se_ven_todas(self):
        claves = {t["clave"] for t in tablero.armar({})["tarjetas"]}
        self.assertTrue({"vueltas_hora", "mangadas_hora"} <= claves)

    def test_el_color_sale_de_los_umbrales(self):
        valores = {"vueltas_hora": {"valor": 5.2}}
        t = next(t for t in tablero.armar(valores, sala="rotativa")["tarjetas"] if t["clave"] == "vueltas_hora")
        self.assertEqual(t["nivel"], "bien")
        valores = {"vueltas_hora": {"valor": 3.8}}
        t = next(t for t in tablero.armar(valores, sala="rotativa")["tarjetas"] if t["clave"] == "vueltas_hora")
        self.assertEqual(t["nivel"], "mal")

    def test_el_catalogo_de_configuracion_tambien_se_filtra(self):
        claves = {c["clave"] for c in tablero.catalogo("rotativa")}
        self.assertIn("vueltas_hora", claves)
        self.assertNotIn("mangadas_hora", claves)
        todas = {c["clave"] for c in tablero.catalogo()}
        self.assertTrue({"vueltas_hora", "mangadas_hora"} <= todas)


if __name__ == "__main__":
    unittest.main()
