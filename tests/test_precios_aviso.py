"""El aviso de «precios ESTIMADOS» tiene que hablar de lo que se USA, no de todo lo que tenga la marca.

La planilla de La Ponderosa conserva las filas con los nombres de Haasten (marcadas «Estimado») al lado de las
de MixerOne: contarlas todas daba «18 de 20 insumos» cuando los ingredientes del mixer ya tenían precio del tambo.

    C:\\Python314\\python.exe -m unittest tests.test_precios_aviso -v
"""
import unittest

import precios_alimentos


def _datos():
    def fila(nombre, estimado):
        return {"nombre": nombre, "precio_kg": 0.1, "usd_tn": 100, "via": "usd",
                "estimado": estimado, "notas": None}
    return {"error": None, "ruta": "x", "archivo": "x.xlsx", "tipo_cambio": 1500,
            "precio_litro": 525.0, "leche_usd_litro": 0.35, "leche_estimada": True, "leche_via": "usd",
            "precios": {
                "silo de maiz": fila("SILO DE MAIZ", True),          # fila vieja de Haasten, ya sin uso
                "harina soja": fila("HARINA SOJA", True),            # idem
                "harina de soja": fila("Harina de Soja", False),     # la del mixer, con precio del tambo
                "heno pastura": fila("Heno pastura", True),          # la del mixer, todavía estimada
            }}


class TestAvisoDeEstimados(unittest.TestCase):
    def test_sin_dato_de_uso_cuenta_todo_como_siempre(self):
        r = precios_alimentos.resumen(datos=_datos())
        self.assertEqual(r["estimados"], 3)
        self.assertIn("3 de 4", r["aviso_estimado"])

    def test_con_dato_de_uso_solo_nombra_los_que_el_mixer_usa(self):
        r = precios_alimentos.resumen(datos=_datos(), estimados_en_uso=["Heno pastura"])
        self.assertIn("Heno pastura", r["aviso_estimado"])
        self.assertNotIn("SILO DE MAIZ", r["aviso_estimado"])
        self.assertNotIn("3 de 4", r["aviso_estimado"])
        self.assertTrue(r["hay_estimados"])

    def test_si_ninguno_de_los_que_se_usan_es_estimado_no_hay_aviso(self):
        r = precios_alimentos.resumen(datos=_datos(), estimados_en_uso=[])
        self.assertIsNone(r["aviso_estimado"])
        self.assertFalse(r["hay_estimados"])


if __name__ == "__main__":
    unittest.main()
