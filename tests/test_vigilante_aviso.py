"""Vigilante, Tarea 4: el texto del aviso. La IA redacta, nunca decide, y el aviso sale aunque ella falle.

Corre sin red: el cliente de la IA es un doble que se inyecta.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_vigilante_aviso.py" -v
"""
import datetime
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import vigilante_aviso as va

AHORA = datetime.datetime(2026, 11, 14, 9, 30)        # sábado


def baja():
    return {"tipo": "baja_ordeno", "clave": "2026-11-14|1|baja_ordeno",
            "datos": {"fecha": "2026-11-14", "sesion": 1, "kg": 920.0, "media": 1024.0, "z": -3.1, "pct": -10.2,
                      "dias_base": 7},
            "causas": ["Rodeo 3: faltaron 24 vacas (680 kg)", "Boca 14 casi no ordeñó (2 kg)"]}


def faltan():
    return {"tipo": "vacas_faltantes", "clave": "2026-11-14|1|vacas_faltantes",
            "datos": {"fecha": "2026-11-14", "sesion": 1, "faltan": 24, "kg": 680.0, "pct": 6.6,
                      "numeros": [101, 102]}, "causas": []}


def comida():
    return {"tipo": "comida_atrasada", "clave": "2026-11-14|comida", "causas": [],
            "datos": {"fecha": "2026-11-14", "primera": 525, "habitual": 450, "retraso": 75}}


def sin_datos():
    return {"tipo": "sin_datos", "clave": "sin_datos", "causas": [],
            "datos": {"ultima_fila": "2026-11-14T04:30:00", "horas": 5.0}}


def vuelven():
    return {"tipo": "datos_vuelven", "clave": "datos_vuelven", "causas": [],
            "datos": {"ultima_fila": "2026-11-14T09:20:00"}}


class Cliente:
    """Doble de `anthropic.Anthropic()`: devuelve el texto que se le pida, o levanta."""
    def __init__(self, texto=None, error=None):
        self.texto, self.error, self.llamadas = texto, error, 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.llamadas += 1
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texto)])


class TestDeterministico(unittest.TestCase):
    def test_baja_cita_fecha_con_dia_de_la_semana_numeros_y_causas(self):
        t = va.texto_deterministico([baja()], AHORA)
        self.assertIn("sábado 14/11", t)
        self.assertIn("ordeño 1", t)
        self.assertIn("920", t)
        self.assertIn("10,2", t)
        self.assertIn("Rodeo 3: faltaron 24 vacas", t)
        self.assertNotIn("ayer", t.lower())
        self.assertNotIn("día en curso del día", t)

    def test_dice_que_es_el_dia_en_curso_y_no_habla_del_dia_entero(self):
        t = va.texto_deterministico([baja()], AHORA)
        self.assertIn("día en curso", t)

    def test_varios_hallazgos_van_en_un_solo_mensaje(self):
        t = va.texto_deterministico([baja(), faltan(), comida()], AHORA)
        self.assertEqual(t.count("Vigilante"), 1)
        self.assertIn("24 vacas", t)
        self.assertIn("primera comida", t.lower())

    def test_comida_dice_las_horas_en_hh_mm(self):
        t = va.texto_deterministico([comida()], AHORA)
        self.assertIn("08:45", t)       # 525 min
        self.assertIn("07:30", t)       # 450 min habitual
        self.assertIn("75", t)

    def test_sin_datos_y_datos_vuelven_tienen_su_texto(self):
        s = va.texto_deterministico([sin_datos()], AHORA)
        self.assertIn("sin datos", s.lower())
        self.assertIn("04:30", s)
        v = va.texto_deterministico([vuelven()], AHORA)
        self.assertIn("volvieron", v.lower())

    def test_con_aviso_de_demora_lo_aclara(self):
        t = va.texto_deterministico([baja()], AHORA, con_aviso_demora=True)
        self.assertIn("con los datos al momento", t)
        self.assertNotIn("con los datos al momento", va.texto_deterministico([baja()], AHORA))

    def test_maximo_seis_lineas_por_hallazgo(self):
        h = baja()
        h["causas"] = [f"causa {i}" for i in range(20)]
        lineas = [l for l in va.texto_deterministico([h], AHORA).splitlines() if l.strip()]
        self.assertLessEqual(len(lineas), 1 + 6)      # encabezado + hasta 6 líneas del hallazgo


class TestNumeros(unittest.TestCase):
    def test_numeros_del_texto_entiende_coma_decimal_y_punto_de_miles(self):
        self.assertEqual(va.numeros_del_texto("bajó 10,2 % a 1.024 kg"), [10.2, 1024.0])
        self.assertEqual(va.numeros_del_texto("sin cifras"), [])


class TestRedactar(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "clave-de-prueba"})
        p.start()
        self.addCleanup(p.stop)

    def test_la_ia_con_numeros_que_figuran_en_los_datos_se_usa(self):
        c = Cliente("El ordeño 1 del sábado 14/11 dio 920 kg, 10,2 % menos de lo normal (1.024 kg). "
                    "Faltaron 24 vacas.")
        r = va.redactar([baja(), faltan()], AHORA, cliente=c)
        self.assertTrue(r.startswith(va.encabezado(AHORA)))     # el encabezado lo pone el código, no la IA
        self.assertIn(c.texto, r)
        self.assertEqual(c.llamadas, 1)

    def test_los_hallazgos_que_no_son_de_produccion_se_agregan_sin_pasar_por_la_ia(self):
        c = Cliente("El ordeño 1 dio 920 kg, 10,2 % menos de lo normal.")
        r = va.redactar([baja(), vuelven()], AHORA, cliente=c)
        self.assertIn(c.texto, r)
        self.assertIn("volvieron", r.lower())

    def test_la_ia_con_un_numero_inventado_se_descarta(self):
        c = Cliente("El ordeño 1 bajó 37 % respecto de lo normal.")
        r = va.redactar([baja()], AHORA, cliente=c)
        self.assertEqual(r, va.texto_deterministico([baja()], AHORA))

    def test_si_la_ia_falla_sale_el_deterministico(self):
        c = Cliente(error=RuntimeError("sin red"))
        self.assertEqual(va.redactar([baja()], AHORA, cliente=c), va.texto_deterministico([baja()], AHORA))

    def test_si_la_ia_devuelve_vacio_sale_el_deterministico(self):
        c = Cliente("   ")
        self.assertEqual(va.redactar([baja()], AHORA, cliente=c), va.texto_deterministico([baja()], AHORA))

    def test_sin_clave_no_llama_a_la_ia(self):
        c = Cliente("no debería usarse")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            r = va.redactar([baja()], AHORA, cliente=c)
        self.assertEqual(c.llamadas, 0)
        self.assertEqual(r, va.texto_deterministico([baja()], AHORA))

    def test_sin_datos_y_datos_vuelven_no_gastan_ia(self):
        c = Cliente("no debería usarse")
        va.redactar([sin_datos()], AHORA, cliente=c)
        va.redactar([vuelven()], AHORA, cliente=c)
        self.assertEqual(c.llamadas, 0)

    def test_el_prompt_le_da_solo_los_hechos_y_prohibe_inventar(self):
        visto = {}
        c = Cliente("ok 920")
        original = c.messages.create

        def espia(**kw):
            visto.update(kw)
            return original(**kw)
        c.messages.create = espia
        va.redactar([baja()], AHORA, cliente=c)
        self.assertIn("920", str(visto["messages"]))
        self.assertIn("no inventes", visto["system"].lower())


class TestResumenYAdvertencias(unittest.TestCase):
    def test_resumen_linea_es_la_primera_linea_del_hallazgo(self):
        linea = va.resumen_linea(baja())
        self.assertIn("ordeño 1", linea)
        self.assertNotIn(chr(10), linea)
        self.assertEqual(linea, va.texto_deterministico([baja()], AHORA).splitlines()[2])

    def test_las_advertencias_del_hallazgo_salen_en_el_texto(self):
        h = baja()
        h["advertencias"] = ["No se pudo consultar la causa (RuntimeError)."]
        t = va.texto_deterministico([h], AHORA)
        self.assertIn("No se pudo consultar la causa", t)


if __name__ == "__main__":
    unittest.main()
