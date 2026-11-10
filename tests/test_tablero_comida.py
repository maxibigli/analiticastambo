"""La tarjeta «Retraso de la primera comida» del Tablero de Diagnóstico.

    C:\\Python314\\python.exe -m unittest tests.test_tablero_comida -v
"""
import unittest

import tablero
import veredicto_baja


def tarjeta(valor, **extra):
    armado = tablero.armar({"retraso_comida": {"valor": valor, **extra}})
    return next(t for t in armado["tarjetas"] if t["clave"] == "retraso_comida")


class TestTarjetaRetrasoComida(unittest.TestCase):
    def test_esta_en_el_catalogo_dentro_de_alimentacion(self):
        ind = tablero.POR_CLAVE["retraso_comida"]
        self.assertEqual(ind["grupo"], "Alimentación")
        self.assertEqual(ind["direccion"], "bajo_mejor")

    def test_el_rojo_es_el_mismo_corte_que_usa_el_diagnostico_de_baja(self):
        self.assertEqual(tablero.POR_CLAVE["retraso_comida"]["rojo"], veredicto_baja.ALIM_RETRASO_MIN)

    def test_semaforo_a_la_hora_habitual_verde_y_con_dos_horas_de_atraso_rojo(self):
        self.assertEqual(tarjeta(0)["nivel"], "bien")
        self.assertEqual(tarjeta(168)["nivel"], "mal")

    def test_sin_dato_explica_por_que_no_hay_numero(self):
        t = tarjeta(None, falta="MixerOne no informa la etapa")
        self.assertEqual(t["nivel"], "sin_dato")
        self.assertEqual(t["falta"], "MixerOne no informa la etapa")

    def test_lleva_al_diagnostico_de_baja(self):
        t = tarjeta(0)
        self.assertEqual((t["pagina"], t["ancla"]), ("consultas", "diag-card"))


if __name__ == "__main__":
    unittest.main()
