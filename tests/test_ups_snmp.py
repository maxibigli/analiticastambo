"""Interpretación de la UPS-MIB estándar (RFC 1628) de la tarjeta de red de la UPS: tensión por fase, corriente, batería.

La tabla de ejemplo es la de una UPS trifásica. Las unidades son las del estándar (tensión en V, corriente en décimas de
amper, frecuencia en décimas de Hz): cada fabricante puede desviarse, por eso el informe de medición muestra también los
valores crudos para compararlos con la pantalla web de la tarjeta antes de confiar en la escala.

    C:\\Python314\\python.exe -m unittest tests.test_ups_snmp -v
"""
import unittest

import snmp_min as sm
import ups_snmp as us

try:
    from test_snmp_min import AgenteFalso
except ImportError:                 # corrido como `python -m unittest tests.test_ups_snmp`
    from tests.test_snmp_min import AgenteFalso

B = "1.3.6.1.2.1.33.1"


def _tabla_trifasica():
    t = {
        f"{B}.1.1.0": "Eaton", f"{B}.1.2.0": "93PM 30kW", f"{B}.1.3.0": "1.2.3", f"{B}.1.4.0": "Agente 5.0",
        f"{B}.1.5.0": "UPS-Tambo", f"{B}.1.6.0": "Sala de ordeñe",
        f"{B}.2.1.0": 2, f"{B}.2.2.0": 0, f"{B}.2.3.0": 38, f"{B}.2.4.0": 100,           # normal, 0 s en batería, 38 min, 100 %
        f"{B}.2.5.0": 5400, f"{B}.2.6.0": 0, f"{B}.2.7.0": 27,                              # 540,0 V, 0 A, 27 °C
        f"{B}.3.1.0": 0, f"{B}.3.2.0": 3,                                                   # 0 líneas malas, 3 líneas
        f"{B}.4.1.0": 3, f"{B}.4.2.0": 500, f"{B}.4.3.0": 3,                                # salida normal, 50,0 Hz, 3 líneas
        f"{B}.6.1.0": 0, f"{B}.9.1.0": 220, f"{B}.9.3.0": 220, f"{B}.9.5.0": 30000,        # config: 220 V nominal
    }
    for n, (vin, vout, iout, pout, carga) in enumerate([(2301, 2200, 125, 2750, 41), (2298, 2198, 131, 2880, 43),
                                                       (2310, 2201, 118, 2600, 39)], start=1):
        t[f"{B}.3.3.1.1.{n}"] = n
        t[f"{B}.3.3.1.2.{n}"] = 500                       # frecuencia de entrada 50,0 Hz
        t[f"{B}.3.3.1.3.{n}"] = vin // 10                 # tensión de entrada (V)
        t[f"{B}.3.3.1.4.{n}"] = iout - 5                  # corriente de entrada (0,1 A)
        t[f"{B}.3.3.1.5.{n}"] = pout + 100                # potencia (W)
        t[f"{B}.4.4.1.1.{n}"] = n
        t[f"{B}.4.4.1.2.{n}"] = vout // 10                # tensión de salida (V)
        t[f"{B}.4.4.1.3.{n}"] = iout                      # corriente de salida (0,1 A)
        t[f"{B}.4.4.1.4.{n}"] = pout                      # potencia de salida (W)
        t[f"{B}.4.4.1.5.{n}"] = carga                     # carga (%)
    return t


class TestInterpretar(unittest.TestCase):
    def setUp(self):
        self.d = us.interpretar_rfc1628(_tabla_trifasica())

    def test_identidad(self):
        i = self.d["identidad"]
        self.assertEqual((i["fabricante"], i["modelo"], i["nombre"]), ("Eaton", "93PM 30kW", "UPS-Tambo"))

    def test_bateria_en_unidades_de_uso(self):
        b = self.d["bateria"]
        self.assertEqual((b["estado"], b["carga_pct"], b["minutos_restantes"], b["segundos_en_bateria"]),
                         ("normal", 100, 38, 0))
        self.assertAlmostEqual(b["tension_v"], 540.0)
        self.assertEqual(b["temperatura_c"], 27)

    def test_entrada_por_fase(self):
        e = self.d["entrada"]
        self.assertEqual(e["n_lineas"], 3)
        self.assertEqual([l["tension_v"] for l in e["lineas"]], [230, 229, 231])
        self.assertAlmostEqual(e["lineas"][0]["frecuencia_hz"], 50.0)
        self.assertAlmostEqual(e["lineas"][0]["corriente_a"], 12.0)            # 120 décimas = 12,0 A

    def test_salida_por_fase_con_corriente_potencia_y_carga(self):
        s = self.d["salida"]
        self.assertEqual(s["fuente"], "normal")
        self.assertEqual([l["tension_v"] for l in s["lineas"]], [220, 219, 220])
        self.assertEqual([round(l["corriente_a"], 1) for l in s["lineas"]], [12.5, 13.1, 11.8])
        self.assertEqual([l["carga_pct"] for l in s["lineas"]], [41, 43, 39])
        self.assertEqual(s["lineas"][0]["potencia_w"], 2750)

    def test_la_corriente_total_es_la_suma_de_las_fases(self):
        self.assertAlmostEqual(self.d["salida"]["corriente_total_a"], 37.4)

    def test_en_bateria_se_distingue_de_normal(self):
        t = _tabla_trifasica()
        t[f"{B}.4.1.0"] = 5                                   # upsOutputSource = battery
        t[f"{B}.2.1.0"] = 3                                   # batería baja
        t[f"{B}.2.2.0"] = 140                                 # 140 s en batería
        d = us.interpretar_rfc1628(t)
        self.assertEqual((d["salida"]["fuente"], d["bateria"]["estado"], d["bateria"]["segundos_en_bateria"]),
                         ("bateria", "baja", 140))
        self.assertTrue(d["en_bateria"])
        self.assertFalse(self.d["en_bateria"])

    def test_los_valores_no_disponibles_son_None_no_numeros_inventados(self):
        t = _tabla_trifasica()
        t[f"{B}.3.3.1.4.2"] = -1                              # algunas tarjetas ponen -1 donde no miden
        t[f"{B}.2.7.0"] = -1
        d = us.interpretar_rfc1628(t)
        self.assertIsNone(d["entrada"]["lineas"][1]["corriente_a"])
        self.assertIsNone(d["bateria"]["temperatura_c"])

    def test_alarmas(self):
        t = _tabla_trifasica()
        t[f"{B}.6.1.0"] = 1
        t[f"{B}.6.2.1.1.1"] = 1
        t[f"{B}.6.2.1.2.1"] = "1.3.6.1.2.1.33.1.6.3.6"       # upsAlarmInputBad (RFC 1628: well-known alarm 6)
        d = us.interpretar_rfc1628(t)
        self.assertEqual(d["alarmas_presentes"], 1)
        self.assertEqual(d["alarmas"][0]["nombre"], "entrada fuera de tolerancia")

    def test_sin_la_mib_estandar_no_esta_disponible(self):
        d = us.interpretar_rfc1628({"1.3.6.1.2.1.1.1.0": "algo"})
        self.assertFalse(d["disponible"])
        self.assertTrue(self.d["disponible"])


class TestConAgente(unittest.TestCase):
    def test_lee_todo_desde_el_agente(self):
        ag = AgenteFalso(_tabla_trifasica())
        self.addCleanup(ag.cerrar)
        c = sm.Cliente("127.0.0.1", puerto=ag.puerto, timeout=0.5, reintentos=2)
        d = us.leer_rfc1628(c)
        self.assertTrue(d["disponible"])
        self.assertEqual(len(d["salida"]["lineas"]), 3)
        self.assertEqual(d["identidad"]["fabricante"], "Eaton")


class TestInformeDeMedicion(unittest.TestCase):
    """El informe de `medir_ups.py`: lo que la persona manda de vuelta para que se arme el monitoreo."""

    def setUp(self):
        t = _tabla_trifasica()
        t["1.3.6.1.2.1.1.1.0"] = "Eaton 93PM Network Card"
        t["1.3.6.1.2.1.1.2.0"] = "1.3.6.1.4.1.534.1"
        t["1.3.6.1.2.1.1.3.0"] = 8640000 * 12
        t["1.3.6.1.2.1.1.5.0"] = "UPS-Tambo"
        t["1.3.6.1.4.1.534.1.2.1.0"] = 4321                 # un valor de la MIB propia
        self.ag = AgenteFalso(t, community="miClaveSecreta")
        self.addCleanup(self.ag.cerrar)
        self.c = sm.Cliente("127.0.0.1", puerto=self.ag.puerto, community="miClaveSecreta", timeout=0.5, reintentos=2)

    def test_incluye_identidad_fases_y_valores_crudos(self):
        import medir_ups
        txt = medir_ups.informe(self.c, "192.168.1.50")
        self.assertIn("Eaton / Powerware", txt)
        self.assertIn("(en red)", txt)                                     # la fuente de la salida, a la vista
        self.assertIn("ENTRADA (3 fase/s", txt)
        self.assertIn("SALIDA", txt)
        self.assertIn("1.3.6.1.2.1.33.1.3.3.1.3.1 = 230", txt)            # el crudo, para comparar con la pantalla web
        self.assertIn("1.3.6.1.4.1.534.1.2.1.0 = 4321", txt)              # la MIB propia del fabricante
        self.assertIn("12.0 días", txt)

    def test_la_community_nunca_aparece_en_el_informe(self):
        import medir_ups
        self.assertNotIn("miClaveSecreta", medir_ups.informe(self.c, "192.168.1.50"))

    def test_si_la_tarjeta_no_tiene_la_mib_estandar_lo_dice_y_sigue(self):
        import medir_ups
        ag = AgenteFalso({"1.3.6.1.2.1.1.1.0": "Tarjeta rara", "1.3.6.1.2.1.1.2.0": "1.3.6.1.4.1.318.1.3.2.4",
                          "1.3.6.1.4.1.318.1.1.1.1.1.1.0": "Smart-UPS"})
        self.addCleanup(ag.cerrar)
        c = sm.Cliente("127.0.0.1", puerto=ag.puerto, timeout=0.5, reintentos=2)
        txt = medir_ups.informe(c, "x")
        self.assertIn("NO expone la MIB estándar", txt)
        self.assertIn("APC (Schneider)", txt)
        self.assertIn("1.3.6.1.4.1.318.1.1.1.1.1.1.0 = Smart-UPS", txt)

    def test_sin_respuesta_es_un_error_claro(self):
        import medir_ups
        ag = AgenteFalso({})
        puerto = ag.puerto
        ag.cerrar()
        with self.assertRaises(sm.SnmpError):
            medir_ups.informe(sm.Cliente("127.0.0.1", puerto=puerto, timeout=0.2, reintentos=1), "x")


class TestFabricante(unittest.TestCase):
    def test_por_numero_de_empresa_del_sysObjectID(self):
        self.assertEqual(us.fabricante_de("1.3.6.1.4.1.318.1.3.2.4"), "APC (Schneider)")
        self.assertEqual(us.fabricante_de("1.3.6.1.4.1.534.1"), "Eaton / Powerware")
        self.assertIsNone(us.fabricante_de("1.3.6.1.4.1.99999.1"))
        self.assertIsNone(us.fabricante_de(None))
        self.assertEqual(us.empresa_de("1.3.6.1.4.1.318.1.3.2.4"), "1.3.6.1.4.1.318")


if __name__ == "__main__":
    unittest.main()
