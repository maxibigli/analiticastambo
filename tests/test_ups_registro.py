"""Registro de las lecturas de la UPS que el agente del tambo manda por HTTPS (ups_registro.py).

El agente lee la tarjeta de red de la UPS dentro del tambo y EMPUJA las lecturas hacia afuera; este módulo las guarda,
dice si siguen llegando y reconstruye los cortes de luz. Las lecturas pueden llegar repetidas (el agente reintenta si
se cortó internet) y en lote, en orden, después de un corte de conexión.

    C:\\Python314\\python.exe -m unittest tests.test_ups_registro -v
"""
import datetime
import os
import tempfile
import unittest

import ups_registro as ur

T0 = datetime.datetime(2026, 11, 9, 14, 0, 0, tzinfo=datetime.timezone.utc)


def _ts(minutos=0):
    return (T0 + datetime.timedelta(minutes=minutos)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ups(en_bateria=False, carga=100, vin=(231, 230, 231), segundos=0, alarmas=()):
    return {
        "disponible": True,
        "identidad": {"fabricante": "Eaton", "modelo": "93PM", "nombre": "UPS-Tambo"},
        "bateria": {"estado": "normal", "segundos_en_bateria": segundos, "minutos_restantes": 38, "carga_pct": carga,
                    "tension_v": 540.0, "corriente_a": 0.0, "temperatura_c": 27},
        "entrada": {"n_lineas": 3, "lineas_malas": 0,
                    "lineas": [{"n": i + 1, "tension_v": v, "corriente_a": 12.5, "frecuencia_hz": 50.0, "potencia_w": 2700}
                               for i, v in enumerate(vin)]},
        "salida": {"fuente": "bateria" if en_bateria else "normal", "frecuencia_hz": 50.0, "n_lineas": 3,
                   "lineas": [{"n": i, "tension_v": 220, "corriente_a": 12.5, "potencia_w": 2750, "carga_pct": 41}
                              for i in (1, 2, 3)], "corriente_total_a": 37.5, "potencia_total_w": 8250},
        "alarmas_presentes": len(alarmas), "alarmas": [{"id": i, "oid": "", "nombre": n, "tipo": None}
                                                       for i, n in enumerate(alarmas)],
        "config": {"tension_entrada_v": 220, "tension_salida_v": 220, "va_salida": 30000, "potencia_salida_w": 27000},
        "en_bateria": en_bateria,
    }


def _lect(minutos, **kw):
    return {"ts": _ts(minutos), "ups": _ups(**kw)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.dir.name, "ups.db")
        self.addCleanup(self.dir.cleanup)

    def guardar(self, lecturas, tambo="ponderosa", ahora=None):
        return ur.guardar(tambo, lecturas, ruta=self.ruta, ahora=ahora or T0 + datetime.timedelta(hours=3))


class TestGuardar(Base):
    def test_guarda_y_devuelve_la_ultima(self):
        r = self.guardar([_lect(0), _lect(1, vin=(228, 229, 230))])
        self.assertEqual(r["guardadas"], 2)
        u = ur.ultima("ponderosa", ruta=self.ruta)
        self.assertEqual(u["ts"], _ts(1))
        self.assertEqual([l["tension_v"] for l in u["ups"]["entrada"]["lineas"]], [228, 229, 230])

    def test_reintento_del_mismo_lote_no_duplica(self):
        self.guardar([_lect(0), _lect(1)])
        r = self.guardar([_lect(0), _lect(1), _lect(2)])
        self.assertEqual((r["guardadas"], r["repetidas"]), (1, 2))
        self.assertEqual(len(ur.historico("ponderosa", _ts(-1), _ts(5), ruta=self.ruta)), 3)

    def test_cada_tambo_tiene_lo_suyo(self):
        self.guardar([_lect(0)], tambo="ponderosa")
        self.assertIsNone(ur.ultima("san_jose", ruta=self.ruta))

    def test_rechaza_fecha_sin_zona_horaria_ilegible_o_del_futuro(self):
        malas = [{"ts": "2026-11-09T14:00:00", "ups": _ups()},          # sin zona: no se sabe de qué hora es
                 {"ts": "ayer", "ups": _ups()},
                 {"ts": _ts(24 * 60), "ups": _ups()},                     # reloj del agente adelantado un día
                 {"ups": _ups()},                                         # sin fecha
                 {"ts": _ts(0)}]                                          # ni datos ni error: no dice nada
        r = self.guardar(malas)
        self.assertEqual((r["guardadas"], r["rechazadas"]), (0, 5))

    def test_acepta_la_fecha_con_desfase_y_la_guarda_en_utc(self):
        self.guardar([{"ts": "2026-11-09T11:00:00-03:00", "ups": _ups()}])       # = 14:00 UTC
        self.assertEqual(ur.ultima("ponderosa", ruta=self.ruta)["ts"], _ts(0))

    def test_una_lectura_con_error_se_guarda_sin_datos_de_la_ups(self):
        """La tarjeta no contestó pero el agente sí está vivo: se distingue de «el agente no reporta»."""
        self.guardar([{"ts": _ts(0), "error": "La tarjeta no respondió"}])
        u = ur.ultima("ponderosa", ruta=self.ruta)
        self.assertIsNone(u["ups"])
        self.assertEqual(u["error"], "La tarjeta no respondió")

    def test_no_guarda_la_identidad_ni_la_configuracion_en_cada_lectura(self):
        """Una lectura por minuto: lo que no cambia no se repite (pesa en la base)."""
        self.guardar([_lect(0)])
        self.assertNotIn("config", ur.ultima("ponderosa", ruta=self.ruta)["ups"])

    def test_tope_de_lecturas_por_lote(self):
        self.assertEqual(self.guardar([_lect(0)] * ur.MAX_LOTE)["guardadas"], 1)        # justo el tope: pasa
        with self.assertRaises(ur.IngestaError):
            self.guardar([_lect(0)] * (ur.MAX_LOTE + 1))


class TestEstado(Base):
    def test_sin_ninguna_lectura(self):
        e = ur.estado("ponderosa", ruta=self.ruta, ahora=T0)
        self.assertFalse(e["hay_datos"])

    def test_reciente_es_vigente(self):
        self.guardar([_lect(0)])
        e = ur.estado("ponderosa", ruta=self.ruta, ahora=T0 + datetime.timedelta(seconds=90))
        self.assertTrue(e["hay_datos"])
        self.assertFalse(e["sin_datos"])
        self.assertEqual(e["edad_s"], 90)

    def test_vieja_marca_sin_datos(self):
        """Si el agente deja de reportar —corte de luz, de internet o PC apagada— LactIA lo dice, no muestra lo último como actual."""
        self.guardar([_lect(0)])
        e = ur.estado("ponderosa", ruta=self.ruta, ahora=T0 + datetime.timedelta(minutes=10))
        self.assertTrue(e["sin_datos"])
        self.assertGreaterEqual(e["edad_s"], 600)


class TestCortes(Base):
    def test_un_corte_abre_y_cierra(self):
        self.guardar([_lect(0), _lect(1), _lect(2, en_bateria=True, carga=96, segundos=20),
                      _lect(3, en_bateria=True, carga=90, segundos=80), _lect(4, carga=91), _lect(5, carga=93)])
        c = ur.cortes("ponderosa", ruta=self.ruta)
        self.assertEqual(len(c), 1)
        self.assertEqual((c[0]["inicio"], c[0]["fin"]), (_ts(2), _ts(4)))
        self.assertEqual(c[0]["duracion_s"], 120)
        self.assertEqual(c[0]["carga_minima_pct"], 90)

    def test_corte_en_curso_queda_abierto(self):
        self.guardar([_lect(0), _lect(1, en_bateria=True, carga=97)])
        c = ur.cortes("ponderosa", ruta=self.ruta)
        self.assertIsNone(c[0]["fin"])
        self.assertTrue(ur.estado("ponderosa", ruta=self.ruta, ahora=T0 + datetime.timedelta(minutes=2))["en_corte"])

    def test_el_corte_sigue_entre_lotes(self):
        self.guardar([_lect(0), _lect(1, en_bateria=True, carga=97)])
        self.guardar([_lect(2, en_bateria=True, carga=94), _lect(3, carga=95)])
        c = ur.cortes("ponderosa", ruta=self.ruta)
        self.assertEqual((len(c), c[0]["inicio"], c[0]["fin"], c[0]["carga_minima_pct"]), (1, _ts(1), _ts(3), 94))

    def test_un_lote_repetido_no_duplica_el_corte(self):
        lote = [_lect(0), _lect(1, en_bateria=True), _lect(2)]
        self.guardar(lote)
        self.guardar(lote)
        self.assertEqual(len(ur.cortes("ponderosa", ruta=self.ruta)), 1)

    def test_una_lectura_con_error_no_cierra_ni_abre_cortes(self):
        self.guardar([_lect(0, en_bateria=True), {"ts": _ts(1), "error": "sin respuesta"}])
        self.assertIsNone(ur.cortes("ponderosa", ruta=self.ruta)[0]["fin"])


class TestHistorico(Base):
    def test_rango_y_orden(self):
        self.guardar([_lect(i) for i in range(10)])
        h = ur.historico("ponderosa", _ts(2), _ts(5), ruta=self.ruta)
        self.assertEqual([x["ts"] for x in h], [_ts(i) for i in range(2, 6)])

    def test_reduce_a_max_puntos_sin_perder_los_extremos(self):
        self.guardar([_lect(i) for i in range(100)])
        h = ur.historico("ponderosa", _ts(0), _ts(99), max_puntos=10, ruta=self.ruta)
        self.assertLessEqual(len(h), 10)
        self.assertEqual((h[0]["ts"], h[-1]["ts"]), (_ts(0), _ts(99)))


class TestPodar(Base):
    def test_poda_lecturas_viejas_pero_no_los_cortes(self):
        self.guardar([_lect(0, en_bateria=True), _lect(1)], ahora=T0)
        borradas = ur.podar(dias=30, ruta=self.ruta, ahora=T0 + datetime.timedelta(days=40))
        self.assertEqual(borradas, 2)
        self.assertEqual(len(ur.cortes("ponderosa", ruta=self.ruta)), 1)


if __name__ == "__main__":
    unittest.main()
