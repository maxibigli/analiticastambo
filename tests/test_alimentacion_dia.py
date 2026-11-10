"""Horario de la comida del mixer, día por día, para el diagnóstico de producción.

Los números reproducen lo medido en MixerOne de La Ponderosa (1/10 al 9/11/2026): la primera comida a los rodeos de
ordeñe es todos los días alrededor de las 07:00 (mediana 07:04, desvío de media hora), con algunos días tardíos
(08:33, 08:29, 09:14, 08:47) y el 07/11 con la más tardía de todas: 09:52.

    C:\\Python314\\python.exe -m unittest tests.test_alimentacion_dia -v
"""
import datetime
import unittest

import alimentacion_dia as ad

DIA = datetime.date(2026, 11, 7)


def descarga(dia, hhmm, kg=1000.0, etapa="Ordeñe", lote="RODEO 1", operacion=None, ciclo="06:30"):
    h, m = map(int, hhmm.split(":"))
    return {"fecha": datetime.datetime.combine(dia, datetime.time(h, m)).isoformat(), "lote": lote, "kg": kg,
            "operacion": operacion or f"{dia}-{hhmm}-{lote}", "etapa": etapa, "ciclo": ciclo}


def consumos(*descargas):
    return {"descargas": list(descargas), "cargas": []}


# Siete días normales (la primera comida entre 06:55 y 07:15) más uno tardío, todos antes del 06/11.
NORMALES = ["07:04", "07:02", "07:09", "06:55", "07:05", "07:10", "07:02", "08:47", "07:04", "07:13"]


def historia(primeras=NORMALES, hasta=datetime.date(2026, 11, 5), kg=8000.0):
    """Un día por cada hora de `primeras`, terminando en `hasta`. Cada día: una comida a esa hora y otra a las 16:00."""
    desc = []
    for i, hhmm in enumerate(reversed(primeras)):
        dia = hasta - datetime.timedelta(days=i)
        desc += [descarga(dia, hhmm, kg / 2), descarga(dia, "16:00", kg / 2, lote="RODEO 2")]
    return desc


class TestPorDia(unittest.TestCase):
    def test_primera_ultima_viajes_y_kg_del_dia(self):
        c = consumos(descarga(DIA, "09:52", 900, operacion="v1"),
                     descarga(DIA, "09:54", 500, lote="RODEO 2", operacion="v1"),
                     descarga(DIA, "17:28", 800, operacion="v2"))
        d = ad.por_dia(c)["2026-11-07"]
        self.assertEqual((d["primera"], d["ultima"], d["viajes"], d["kg"]), (9 * 60 + 52, 17 * 60 + 28, 2, 2200))
        self.assertEqual(d["ciclo"], "06:30")

    def test_solo_cuentan_los_rodeos_de_ordene(self):
        c = consumos(descarga(DIA, "06:00", etapa="Secas"), descarga(DIA, "07:10", etapa="Recría"),
                     descarga(DIA, "09:52"))
        self.assertEqual(ad.por_dia(c)["2026-11-07"]["primera"], 9 * 60 + 52)

    def test_si_el_proveedor_no_informa_la_etapa_cuentan_todas(self):
        c = consumos(dict(descarga(DIA, "08:00"), etapa=None))
        self.assertEqual(ad.por_dia(c)["2026-11-07"]["primera"], 8 * 60)

    def test_una_descarga_de_madrugada_no_es_la_primera_comida_del_dia(self):
        """Un viaje que arranca a las 23:50 descarga a las 00:10 del día siguiente: no es el desayuno."""
        c = consumos(descarga(DIA, "00:10"), descarga(DIA, "07:12"))
        self.assertEqual(ad.por_dia(c)["2026-11-07"]["primera"], 7 * 60 + 12)

    def test_no_cuentan_las_descargas_sin_kg_ni_las_negativas(self):
        c = consumos(descarga(DIA, "06:00", 0), descarga(DIA, "06:30", -50), descarga(DIA, "08:00", 700))
        d = ad.por_dia(c)["2026-11-07"]
        self.assertEqual((d["primera"], d["kg"]), (8 * 60, 700))

    def test_sin_descargas(self):
        self.assertEqual(ad.por_dia({"descargas": [], "cargas": []}), {})
        self.assertEqual(ad.por_dia({}), {})


class TestEvaluar(unittest.TestCase):
    def evaluar(self, dia_hhmm="09:52", ayer_hhmm="07:13", kg_dia=9000.0, kg_ayer=8000.0, **kw):
        ayer = DIA - datetime.timedelta(days=1)
        c = consumos(*historia())
        if dia_hhmm:
            c["descargas"] += [descarga(DIA, dia_hhmm, kg_dia / 2), descarga(DIA, "16:30", kg_dia / 2, lote="RODEO 2")]
        if ayer_hhmm:
            c["descargas"] += [descarga(ayer, ayer_hhmm, kg_ayer / 2), descarga(ayer, "16:00", kg_ayer / 2, lote="RODEO 2")]
        return ad.evaluar(c, DIA, **kw)

    def test_el_dia_tardio_se_mide_contra_la_mediana_de_la_historia(self):
        r = self.evaluar()
        self.assertEqual(r["base"]["dias"], 10)
        self.assertEqual(r["base"]["primera_med"], 7 * 60 + 4)           # 07:04, la mediana NO la arrastra el día tardío
        self.assertEqual(r["dia"]["primera"], 9 * 60 + 52)
        self.assertEqual(r["dia"]["retraso"], 9 * 60 + 52 - (7 * 60 + 4))     # 168 min
        self.assertGreater(r["dia"]["z"], 5)

    def test_un_dia_normal_no_se_aleja(self):
        r = self.evaluar(dia_hhmm="07:12")
        self.assertLess(abs(r["dia"]["z"]), 2)
        self.assertEqual(r["ayer"]["retraso"], 7 * 60 + 13 - (7 * 60 + 4))

    def test_la_historia_lista_los_otros_dias_con_comida_tardia(self):
        r = self.evaluar()
        self.assertEqual([t["fecha"] for t in r["base"]["tarde"]], ["2026-11-03"])
        self.assertEqual(r["base"]["tarde"][0]["primera"], 8 * 60 + 47)

    def test_el_desvio_tiene_un_piso_para_no_dividir_por_casi_cero(self):
        c = consumos(*historia(["07:00"] * 8))
        c["descargas"].append(descarga(DIA, "07:40"))
        r = ad.evaluar(c, DIA)
        self.assertEqual(r["base"]["primera_sd"], ad.SD_MIN)
        self.assertLess(r["dia"]["z"], 5)         # 40 min con un desvío de 15 = 2,7, no infinito

    def test_el_total_de_kg_se_compara_con_la_mediana(self):
        r = self.evaluar(kg_dia=6000.0)
        self.assertEqual(r["base"]["kg_med"], 8000.0)
        self.assertEqual(r["dia"]["kg"], 6000.0)

    def test_los_kg_tambien_se_miden_en_desvios_con_un_piso_del_5_por_ciento(self):
        r = self.evaluar(kg_dia=6000.0)                       # la historia es siempre 8.000 kg: sin variación
        self.assertEqual(r["base"]["kg_sd"], 0.05 * 8000.0)   # piso: 400 kg
        self.assertEqual(r["dia"]["kg_z"], -5.0)               # (6.000 - 8.000) / 400

    def test_si_ayer_no_hay_registro_igual_se_evalua_el_dia(self):
        r = self.evaluar(ayer_hhmm=None)
        self.assertIsNone(r["ayer"])
        self.assertIsNotNone(r["dia"])

    def test_si_el_dia_no_tiene_comidas_registradas_lo_dice(self):
        r = self.evaluar(dia_hhmm=None)
        self.assertIsNone(r["dia"])

    def test_con_poca_historia_no_se_mide(self):
        c = consumos(*historia(["07:00"] * 3))
        c["descargas"].append(descarga(DIA, "09:52"))
        self.assertIsNone(ad.evaluar(c, DIA))

    def test_sin_nada_no_se_mide(self):
        self.assertIsNone(ad.evaluar({"descargas": [], "cargas": []}, DIA))
        self.assertIsNone(ad.evaluar(None, DIA))

    def test_el_dia_analizado_y_ayer_no_entran_en_su_propia_base(self):
        r = self.evaluar()
        fechas = {t["fecha"] for t in r["base"]["tarde"]}
        self.assertNotIn("2026-11-07", fechas)
        self.assertNotIn("2026-11-06", fechas)


class TestUltimaComida(unittest.TestCase):
    """La tarjeta del dashboard: la primera comida del último día con comida, contra lo habitual del tambo."""

    def con_dia(self, hhmm, dia=DIA):
        c = consumos(*historia())
        if hhmm:
            c["descargas"].append(descarga(dia, hhmm))
        return c

    def test_el_ultimo_dia_con_comida_contra_la_mediana_de_la_historia(self):
        r = ad.ultima_comida(self.con_dia("09:52"))
        self.assertEqual(r["fecha"], "2026-11-07")
        self.assertEqual((r["primera"], r["habitual"], r["retraso"]), (9 * 60 + 52, 7 * 60 + 4, 168))
        self.assertEqual(r["base_dias"], 10)

    def test_un_dia_normal_da_un_retraso_chico(self):
        r = ad.ultima_comida(self.con_dia("07:10"))
        self.assertEqual(r["retraso"], 6)

    def test_comer_antes_de_lo_habitual_da_negativo_y_no_se_oculta(self):
        self.assertEqual(ad.ultima_comida(self.con_dia("06:30"))["retraso"], -34)

    def test_sin_comida_hoy_vale_el_ultimo_dia_que_hubo(self):
        r = ad.ultima_comida(self.con_dia(None))
        self.assertEqual(r["fecha"], "2026-11-05")      # el último de la historia

    def test_si_el_proveedor_no_informa_la_etapa_no_se_mide(self):
        """Sin etapa no se sabe qué descarga es de un rodeo de ordeñe: contar todas daría la hora de la recría."""
        c = {"descargas": [dict(d, etapa=None) for d in self.con_dia("09:52")["descargas"]], "cargas": []}
        self.assertIsNone(ad.ultima_comida(c))

    def test_con_poca_historia_no_se_mide(self):
        c = consumos(*historia(["07:00"] * 3))
        c["descargas"].append(descarga(DIA, "09:52"))
        self.assertIsNone(ad.ultima_comida(c))

    def test_sin_nada_no_se_mide(self):
        self.assertIsNone(ad.ultima_comida({"descargas": [], "cargas": []}))
        self.assertIsNone(ad.ultima_comida(None))


class TestParaElTablero(unittest.TestCase):
    def test_valor_y_detalle_con_la_fecha_concreta(self):
        r = {"fecha": "2026-11-07", "primera": 9 * 60 + 52, "habitual": 7 * 60 + 4, "retraso": 168, "base_dias": 30}
        valor, detalle = ad.para_el_tablero(r)
        self.assertEqual(valor, 168)
        self.assertIn("sábado 07/11", detalle)
        self.assertIn("09:52", detalle)
        self.assertIn("07:04", detalle)
        for palabra in ("ayer", "hoy", "ese día", "anoche"):
            self.assertNotIn(palabra, detalle.lower())

    def test_comer_antes_de_lo_habitual_no_es_un_problema_y_vale_cero(self):
        """El semáforo es de «cuánto se atrasó»: adelantarse no puede pintar rojo ni verse como un número negativo."""
        r = {"fecha": "2026-11-06", "primera": 6 * 60 + 30, "habitual": 7 * 60 + 4, "retraso": -34, "base_dias": 30}
        valor, detalle = ad.para_el_tablero(r)
        self.assertEqual(valor, 0)
        self.assertIn("06:30", detalle)


class ProveedorFalso:
    NOMBRE = "MixerOne"

    def __init__(self, consumos=None, disponible=(True, ""), error=None):
        self._consumos, self._disponible, self._error = consumos, disponible, error
        self.pedidos = []

    def disponible(self):
        return self._disponible

    def consumos(self, desde, hasta):
        self.pedidos.append((desde, hasta))
        if self._error:
            raise self._error
        return self._consumos


class TestDesdeElProveedor(unittest.TestCase):
    HOY = datetime.date(2026, 11, 9)

    def test_trae_los_ultimos_treinta_dias_y_da_la_comida(self):
        c = consumos(*historia(), descarga(DIA, "09:52"))
        prov = ProveedorFalso(c)
        r = ad.desde_el_proveedor(prov, self.HOY)
        self.assertEqual(r["comida"]["retraso"], 168)
        self.assertNotIn("falta", r)
        self.assertEqual(prov.pedidos, [(self.HOY - datetime.timedelta(days=30), self.HOY)])

    def test_sin_proveedor_conectado_dice_el_motivo(self):
        r = ad.desde_el_proveedor(ProveedorFalso(disponible=(False, "Falta la variable X")), self.HOY)
        self.assertEqual(r, {"falta": "Falta la variable X"})

    def test_si_el_mixer_falla_el_motivo_viaja_y_no_se_propaga_el_error(self):
        r = ad.desde_el_proveedor(ProveedorFalso(error=RuntimeError("HTTP 500")), self.HOY)
        self.assertIn("HTTP 500", r["falta"])

    def test_un_proveedor_que_no_informa_la_etapa_lo_nombra(self):
        prov = ProveedorFalso({"descargas": [dict(d, etapa=None) for d in historia()], "cargas": []})
        prov.NOMBRE = "Haasten"
        r = ad.desde_el_proveedor(prov, self.HOY)
        self.assertIn("Haasten", r["falta"])
        self.assertIn("MixerOne", r["falta"])

    def test_con_poca_historia_lo_dice(self):
        prov = ProveedorFalso(consumos(*historia(["07:00"] * 3)))
        self.assertIn("historia", ad.desde_el_proveedor(prov, self.HOY)["falta"])


if __name__ == "__main__":
    unittest.main()
