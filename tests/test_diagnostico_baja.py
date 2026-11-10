"""Diagnóstico de producción: compara un día contra un rango y separa vacas ausentes de rinde bajo.

Corre sin base: `db.run_query` se reemplaza por datos sintéticos que reconocen qué consulta se pide.

    C:\\Python314\\python.exe -m unittest discover -s tests -v
"""
import datetime
import time
import unittest
from unittest import mock

import app
import diagnostico_baja as dg

app._warmup = lambda *a, **k: None   # el calentamiento de caché consultaría la base real

HOY = datetime.date(2026, 11, 8)
DIA = datetime.date(2026, 11, 7)

_parche_mixer = mock.patch.object(dg, "_consumos_mixer", lambda tambo, desde, hasta: (None, "sin mixer en la prueba"))


def setUpModule():
    _parche_mixer.start()


def tearDownModule():
    _parche_mixer.stop()


def _fechas(desde, hasta):
    d = datetime.date.fromisoformat(desde)
    h = datetime.date.fromisoformat(hasta)
    while d <= h:
        yield d.isoformat()
        d += datetime.timedelta(days=1)


def _filas_vacas(por_dia):
    """por_dia: {fecha: [(numero, grupo, kg), ...]} -> filas como las devuelve sql_vacas."""
    filas = []
    for fecha, vacas in por_dia.items():
        for numero, grupo, kg in vacas:
            filas.append([fecha, 1000 + numero, numero, grupo, 150, 2, kg, 1])
    return filas


def _db_falsa(vacas, truncada=False):
    """db.run_query de mentira: responde según la tabla que consulta el SQL."""
    def run_query(sql, validate=True, tambo="x", max_rows=5000):
        s = " ".join(sql.split())
        if "FROM AnimalDaily ad JOIN BasicAnimal b" in s:
            return {"columns": [], "rows": vacas, "truncated": truncada}
        return {"columns": [], "rows": [], "truncated": False}
    return run_query


def _informe(vacas, dia=DIA, bd="2026-11-01", bh="2026-11-06", truncada=False):
    with mock.patch.object(dg.db, "run_query", _db_falsa(vacas, truncada)), \
            mock.patch.object(dg, "ith_de", lambda dias: {}):
        return dg.generar("t", dia, bd, bh)


def _rodeo(n_vacas, kg, hasta_vaca=None):
    return [(i, 21, kg) for i in range(1, (hasta_vaca or n_vacas) + 1)]


class TestValidar(unittest.TestCase):
    def test_sin_base_son_los_7_dias_anteriores(self):
        dia, bd, bh = dg.validar("2026-11-07", hoy=HOY)
        self.assertEqual((bd, bh), (datetime.date(2026, 10, 31), datetime.date(2026, 11, 6)))

    def test_acepta_fechas_o_texto(self):
        dia, bd, bh = dg.validar(DIA, "2026-10-20", datetime.date(2026, 11, 3), hoy=HOY)
        self.assertEqual((dia, bd, bh), (DIA, datetime.date(2026, 10, 20), datetime.date(2026, 11, 3)))

    def test_rechaza_lo_que_no_se_puede_comparar(self):
        casos = [
            (("2026-11-09", None, None), "futuro"),
            (("no-es-fecha", None, None), "AAAA-MM-DD"),
            (("", None, None), "día a analizar"),
            (("2026-11-07", "2026-11-01", None), "las dos fechas"),
            (("2026-11-07", "2026-11-06", "2026-11-01"), "posterior"),
            (("2026-11-07", "2026-09-01", "2026-11-06"), "hasta 31"),
            (("2026-11-07", "2026-11-05", "2026-11-06"), "al menos 3"),
        ]
        for args, texto in casos:
            with self.assertRaises(dg.DiagnosticoError, msg=repr(args)) as cm:
                dg.validar(*args, hoy=HOY)
            self.assertIn(texto, str(cm.exception), msg=repr(args))

    def test_el_dia_analizado_dentro_de_la_base_no_cuenta_como_dia_de_base(self):
        # base 5/11 a 8/11 con el 7/11 adentro: quedan 3 días útiles (5, 6 y 8)
        dg.validar("2026-11-07", "2026-11-05", "2026-11-08", hoy=datetime.date(2026, 11, 9))
        with self.assertRaises(dg.DiagnosticoError):
            dg.validar("2026-11-07", "2026-11-06", "2026-11-08", hoy=datetime.date(2026, 11, 9))


class TestInforme(unittest.TestCase):
    def test_faltaron_vacas(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 40.0, hasta_vaca=90)        # 10 vacas no se ordeñaron
        t = _informe(_filas_vacas(por_dia))
        self.assertIn("FALTARON VACAS", t)
        self.assertIn("-400 kg", t)                                      # 10 vacas x 40 kg
        self.assertIn("SIN REGISTRO el 2026-11-07 (no se ordeñaron): 10", t)
        self.assertIn("comparado con 2026-11-01 a 2026-11-06", t)

    def test_rindio_menos_cada_vaca(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 36.0)                        # las 100 dan 4 kg menos
        t = _informe(_filas_vacas(por_dia))
        self.assertIn("BAJÓ EL RINDE POR VACA", t)
        self.assertIn("-400 kg", t)
        self.assertIn("SIN REGISTRO el 2026-11-07 (no se ordeñaron): 0", t)

    def test_un_dia_cortado_no_arrastra_la_base(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-03"] = _rodeo(100, 40.0, hasta_vaca=20)        # día cortado (backup a mitad de día)
        por_dia["2026-11-07"] = _rodeo(100, 40.0)
        t = _informe(_filas_vacas(por_dia))
        self.assertIn("se excluyen de la base por incompletos", t)
        self.assertIn("2026-11-03", t)
        self.assertIn("prom 5d", t)
        # sin el día cortado, el día analizado coincide con la base: nada que explicar
        self.assertIn("+0 kg", t)

    def test_la_base_se_elige_por_rango(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-10-20", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 40.0)
        t = _informe(_filas_vacas(por_dia), bd="2026-10-20", bh="2026-11-03")
        self.assertIn("comparado con 2026-10-20 a 2026-11-03", t)
        self.assertIn("prom 15d", t)

    def test_consulta_cortada_no_da_numeros_falsos(self):
        por_dia = {f: _rodeo(10, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        with self.assertRaises(dg.DiagnosticoError) as cm:
            _informe(_filas_vacas(por_dia), truncada=True)
        self.assertIn("se cortó", str(cm.exception))

    def test_si_no_se_puede_leer_la_produccion_es_un_error_no_un_dia_sin_datos(self):
        def run_query(sql, validate=True, tambo="x", max_rows=5000):
            raise RuntimeError("Se ha superado el tiempo de espera")

        with mock.patch.object(dg.db, "run_query", run_query), mock.patch.object(dg, "ith_de", lambda d: {}):
            with self.assertRaises(dg.DiagnosticoError) as cm:
                dg.generar("t", DIA, "2026-11-01", "2026-11-06")
        self.assertIn("No se pudo leer la producción", str(cm.exception))

    def test_sin_produccion_ese_dia_lo_dice(self):
        por_dia = {f: _rodeo(10, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        t = _informe(_filas_vacas(por_dia))
        self.assertIn("No hay producción registrada el 2026-11-07", t)

    def test_una_seccion_que_falla_dice_sin_dato_y_no_ceros(self):
        por_dia = {f: _rodeo(50, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        filas = _filas_vacas(por_dia)

        def run_query(sql, validate=True, tambo="x", max_rows=5000):
            s = " ".join(sql.split())
            if "FROM AnimalDaily ad JOIN BasicAnimal b" in s:
                return {"columns": [], "rows": filas, "truncated": False}
            if "EventCalving" in s:
                raise RuntimeError("timeout")
            return {"columns": [], "rows": [], "truncated": False}

        with mock.patch.object(dg.db, "run_query", run_query), mock.patch.object(dg, "ith_de", lambda d: {}):
            t = dg.generar("t", DIA, "2026-11-01", "2026-11-06")
        self.assertIn("sin dato", t)
        self.assertNotIn("parto     0", t)


def _analizar(vacas, extra=None, dia=DIA, bd="2026-11-01", bh="2026-11-06", ith=None):
    """`dg.analizar` con la base falsa; `extra(sql)` puede devolver las filas de otras consultas."""
    def run_query(sql, validate=True, tambo="x", max_rows=5000):
        s = " ".join(sql.split())
        if "FROM AnimalDaily ad JOIN BasicAnimal b" in s:
            return {"columns": [], "rows": vacas, "truncated": False}
        filas = extra(s) if extra else None
        return {"columns": [], "rows": filas or [], "truncated": False}

    with mock.patch.object(dg.db, "run_query", run_query), mock.patch.object(dg, "ith_de", lambda dias: ith or {}):
        return dg.analizar("t", dia, bd, bh)


class TestCifrasComoElDashboard(unittest.TestCase):
    """El dashboard cuenta solo vacas identificadas con lectura válida; si el informe cuenta otra cosa los
    números no coinciden y nadie sabe cuál creer (el 07/11 daba 70.653 kg contra 69.225 del dashboard)."""

    def test_las_lecturas_no_validas_y_las_sin_identificar_no_entran_al_total(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        filas = _filas_vacas(por_dia)
        # el 7/11: 5 vacas con lectura NO válida (3.000 kg en total) y una sin identificar (Number = 0)
        filas += [["2026-11-07", 5000 + i, 500 + i, 21, 150, 2, 600.0, 0] for i in range(5)]
        filas += [["2026-11-07", 9000, 0, 21, 150, 2, 30.0, 1]]
        r = _analizar(filas)
        res = r["datos"]["resumen"]
        self.assertEqual(res["kg_d"], 4000.0)
        self.assertEqual(res["vac_d"], 100)
        lec = r["datos"]["lecturas"]
        self.assertEqual((lec["inval_kg_d"], lec["inval_n_d"], lec["sinid_kg_d"]), (3000.0, 5, 30.0))

    def test_una_vaca_con_lectura_no_valida_no_se_cuenta_como_vaca_que_cayo(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        filas = [f for f in _filas_vacas(por_dia) if not (f[0] == "2026-11-07" and f[2] == 7)]
        filas.append(["2026-11-07", 1007, 7, 21, 150, 2, 4.0, 0])         # la vaca 7: total parcial, no válido
        r = _analizar(filas)
        a = r["datos"]["ausentes"]
        # no figura como "no se ordeñó" ni como "cayó 36 kg": se ordeñó, pero DelPro no cerró el total del día
        self.assertEqual((a["n"], a["n_invalidas"]), (0, 1))
        self.assertAlmostEqual(a["kg_invalidas"], 40.0)
        self.assertEqual(r["datos"]["caidas"]["n_comunes"], 99)

    def test_las_ausentes_separan_las_que_no_tienen_registro_de_las_de_lectura_no_valida(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        filas = [f for f in _filas_vacas(por_dia) if not (f[0] == "2026-11-07" and f[2] <= 10)]   # 10 vacas faltan
        filas += [["2026-11-07", 1000 + n, n, 21, 150, 2, 5.0, 0] for n in (1, 2, 3)]            # 3 de ellas: lectura no válida
        r = _analizar(filas)
        a = r["datos"]["ausentes"]
        self.assertEqual((a["n"], a["n_invalidas"]), (7, 3))               # 7 sin registro: esas SÍ no se ordeñaron
        self.assertIn("SIN REGISTRO", r["informe"])
        self.assertIn("lectura NO válida", r["informe"])


class TestEtiquetaDeLaCausa(unittest.TestCase):
    def test_si_pasaron_mas_vacas_no_dice_que_faltaron(self):
        """07/11: 62 vacas MÁS y cada una rindió menos; el informe decía «pesa más: FALTARON VACAS»."""
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(110, 36.0)
        t = _informe(_filas_vacas(por_dia))
        self.assertNotIn("pesa más: FALTARON", t)           # el título de la sección 1 sí dice «¿FALTARON VACAS…?»
        self.assertNotIn("Pesa más: faltaron", t)           # ni el resumen del final
        self.assertIn("BAJÓ EL RINDE POR VACA", t)
        self.assertIn("MÁS VACAS", t)                       # y dice que las vacas de más compensaron


class TestAnalizar(unittest.TestCase):
    def test_devuelve_informe_datos_y_veredicto(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 40.0, hasta_vaca=90)
        r = _analizar(_filas_vacas(por_dia))
        self.assertIn("FALTARON VACAS", r["informe"])
        self.assertEqual(r["datos"]["ausentes"]["n"], 10)
        self.assertEqual(r["veredicto"]["causas"][0]["clave"], "vacas")
        self.assertEqual(r["veredicto"]["causas"][0]["nivel"], "probable")

    def test_trae_lo_necesario_para_los_graficos(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        r = _analizar(_filas_vacas(por_dia))
        res = r["datos"]["resumen"]
        self.assertEqual(round(res["kg_b"] + res["efecto_vacas"] + res["efecto_rinde"]), round(res["kg_d"]))
        self.assertEqual(r["datos"]["grupos"][0]["grupo"], "Rodeo (id 21)")   # sin nombres: no se inventa uno

    def test_sin_desvio_en_la_base_una_diferencia_no_divide_por_cero(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 36.0)
        r = _analizar(_filas_vacas(por_dia))                               # base idéntica todos los días: desvío 0
        self.assertLess(r["datos"]["resumen"]["z_kg"], -2)


def _consumos_mixer(primera_del_dia="09:52", dias=range(1, 7)):
    """Comidas de MixerOne: del 01 al 06/11 la primera es a las 07:00 y el día analizado, a `primera_del_dia`."""
    desc = []
    for n in dias:
        f = f"2026-11-{n:02d}"
        desc += [{"fecha": f"{f}T07:0{n % 5}:00", "lote": "RODEO 1", "kg": 9000.0, "operacion": f"{f}-a", "etapa": "Ordeñe",
                  "ciclo": "06:30"},
                 {"fecha": f"{f}T16:00:00", "lote": "RODEO 1", "kg": 9000.0, "operacion": f"{f}-b", "etapa": "Ordeñe",
                  "ciclo": "15:30"}]
    desc += [{"fecha": f"2026-11-07T{primera_del_dia}:00", "lote": "RODEO 1", "kg": 9000.0, "operacion": "d-a",
              "etapa": "Ordeñe", "ciclo": "06:30"},
             {"fecha": "2026-11-07T16:00:00", "lote": "RODEO 1", "kg": 9000.0, "operacion": "d-b", "etapa": "Ordeñe",
              "ciclo": "15:30"}]
    return {"descargas": desc, "cargas": []}


class TestAlimentacionEnElInforme(unittest.TestCase):
    """Sección 8: la hora de la comida del mixer, contra lo normal de ese tambo."""

    def _correr(self, consumos, motivo="no disponible"):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 38.0)
        with mock.patch.object(dg, "_consumos_mixer", lambda tambo, desde, hasta: (consumos, motivo)):
            return _analizar(_filas_vacas(por_dia))

    def test_la_comida_tardia_aparece_en_el_informe_y_en_el_veredicto(self):
        r = self._correr(_consumos_mixer())
        self.assertIn("ALIMENTACIÓN", r["informe"])
        self.assertIn("09:52", r["informe"])
        a = r["datos"]["alimentacion"]
        self.assertEqual(a["dia"]["primera"], 9 * 60 + 52)
        self.assertGreater(a["dia"]["retraso"], 120)
        causa = next(c for c in r["veredicto"]["causas"] if c["clave"] == "alimentacion")
        self.assertEqual(causa["nivel"], "posible")

    def test_un_dia_normal_se_descarta(self):
        r = self._correr(_consumos_mixer(primera_del_dia="07:03"))
        causa = next(c for c in r["veredicto"]["causas"] if c["clave"] == "alimentacion")
        self.assertEqual(causa["nivel"], "descartada")

    def test_pide_al_mixer_30_dias_de_historia_hasta_el_dia_analizado(self):
        pedidos = []

        def falso(tambo, desde, hasta):
            pedidos.append((tambo, desde, hasta))
            return None, "x"
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}
        with mock.patch.object(dg, "_consumos_mixer", falso):
            _analizar(_filas_vacas(por_dia))
        self.assertEqual(pedidos, [("t", DIA - datetime.timedelta(days=dg.HISTORIA_MIXER_DIAS), DIA)])
        self.assertEqual(dg.HISTORIA_MIXER_DIAS, 30)

    def test_sin_mixer_dice_por_que_y_el_veredicto_lo_marca_sin_dato(self):
        r = self._correr(None, motivo="MixerOne no está conectado para este tambo")
        self.assertIn("ALIMENTACIÓN", r["informe"])
        self.assertIn("MixerOne no está conectado", r["informe"])
        self.assertIsNone(r["datos"]["alimentacion"])
        causa = next(c for c in r["veredicto"]["causas"] if c["clave"] == "alimentacion")
        self.assertEqual(causa["nivel"], "sin_dato")

    def test_con_poca_historia_no_inventa_lo_normal(self):
        r = self._correr(_consumos_mixer(dias=range(5, 7)))
        self.assertIsNone(r["datos"]["alimentacion"])
        self.assertIn("historia", r["informe"].lower())


class TestConsumosMixer(unittest.TestCase):
    """`_consumos_mixer` no rompe el informe: sin proveedor o con un error, devuelve el motivo."""

    def _llamar(self, prov):
        _parche_mixer.stop()
        try:
            with mock.patch("proveedores.de", return_value=prov):
                return dg._consumos_mixer("ponderosa", DIA - datetime.timedelta(days=30), DIA)
        finally:
            _parche_mixer.start()

    def test_con_el_proveedor_conectado_devuelve_los_consumos(self):
        class P:
            def disponible(self): return True, ""
            def consumos(self, d, h): return {"descargas": [1], "cargas": []}
        c, motivo = self._llamar(P())
        self.assertEqual(c, {"descargas": [1], "cargas": []})

    def test_si_el_proveedor_no_esta_disponible_devuelve_el_motivo(self):
        class P:
            def disponible(self): return False, "Falta la variable MIXERONE_CLIENTE_PONDEROSA"
        c, motivo = self._llamar(P())
        self.assertIsNone(c)
        self.assertIn("MIXERONE_CLIENTE_PONDEROSA", motivo)

    def test_un_error_del_proveedor_no_tumba_el_informe(self):
        class P:
            def disponible(self): return True, ""
            def consumos(self, d, h): raise RuntimeError("se cayó mixerone.net")
        c, motivo = self._llamar(P())
        self.assertIsNone(c)
        self.assertIn("se cayó mixerone.net", motivo)


class TestSerieParaLaLineaDeTiempo(unittest.TestCase):
    """`datos["serie"]`: la producción día por día, total, por rodeo y por ordeño, para dibujar la línea de tiempo."""

    DIAS = list(_fechas("2026-11-01", "2026-11-07"))

    def _escenario(self, sesiones=True, kg_dia_g21=40.0):
        # Rodeo 21: 30 vacas; rodeo 22: 10 vacas (chico: no se dibuja). El día analizado, el rodeo 21 rinde 40 en vez de 45.
        por_dia = {f: [(i, 21, 45.0) for i in range(1, 31)] + [(100 + i, 22, 20.0) for i in range(1, 11)]
                   for f in self.DIAS[:-1]}
        por_dia["2026-11-07"] = [(i, 21, kg_dia_g21) for i in range(1, 31)] + [(100 + i, 22, 20.0) for i in range(1, 11)]
        filas_ses = []
        for f in self.DIAS:
            for sn, kg in ((1, 400.0), (2, 500.0), (3, 450.0 if f != "2026-11-07" else 300.0)):
                filas_ses.append([f, sn, 40, kg, datetime.datetime.fromisoformat(f + "T06:00:00"),
                                  datetime.datetime.fromisoformat(f + "T09:00:00"), 5.0, 2.0])

        def extra(sql):
            if sesiones and "sm.SessionNo AS sesion" in sql:
                return filas_ses
            if "FROM AbstractGroup" in sql:
                return [[21, "Rodeo 3", 3], [22, "Rodeo 4 - Baja", 4]]
            return None
        return _analizar(_filas_vacas(por_dia), extra=extra)["datos"]["serie"]

    def test_los_dias_cubren_el_periodo_de_la_base_y_el_dia_analizado(self):
        serie = self._escenario()
        self.assertEqual(serie["dias"], self.DIAS)
        self.assertEqual(serie["dia"], "2026-11-07")

    def test_dice_cuales_son_los_dias_de_la_base(self):
        serie = self._escenario()
        self.assertEqual(serie["base"], self.DIAS[:6])          # la base es 01 a 06/11; el 07 es el analizado

    def test_el_total_por_dia_es_el_kg_de_vacas_identificadas(self):
        t = self._escenario()["total"]
        self.assertEqual(t["kg"][0], 30 * 45.0 + 10 * 20.0)
        self.assertEqual(t["kg"][-1], 30 * 40.0 + 10 * 20.0)
        self.assertEqual(t["vacas"], [40] * 7)

    def test_un_dia_sin_produccion_queda_vacio_no_en_cero(self):
        por_dia = {f: _rodeo(100, 40.0) for f in ("2026-11-01", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
                                                  "2026-11-07")}
        serie = _analizar(_filas_vacas(por_dia))["datos"]["serie"]
        self.assertIsNone(serie["total"]["kg"][1])              # el 02/11 no hay nada: un hueco, no una caída a cero

    def test_cada_rodeo_grande_tiene_su_serie(self):
        g = {x["nombre"]: x for x in self._escenario()["grupos"]}
        self.assertEqual(g["Rodeo 3"]["kg"][0], 30 * 45.0)
        self.assertEqual(g["Rodeo 3"]["kg"][-1], 30 * 40.0)
        self.assertEqual(g["Rodeo 3"]["vacas"][-1], 30)

    def test_los_rodeos_chicos_no_entran(self):
        self.assertEqual([x["nombre"] for x in self._escenario()["grupos"]], ["Rodeo 3"])

    def test_cada_ordeno_tiene_su_serie(self):
        s = {x["sesion"]: x for x in self._escenario()["sesiones"]}
        self.assertEqual(sorted(s), [1, 2, 3])
        self.assertEqual(s[3]["kg"][0], 450.0)
        self.assertEqual(s[3]["kg"][-1], 300.0)             # el ordeño que cayó el día analizado
        self.assertEqual(s[3]["vacas"][-1], 40)

    def test_sin_la_consulta_de_ordenos_no_se_inventan(self):
        serie = self._escenario(sesiones=False)
        self.assertEqual(serie["sesiones"], [])
        self.assertEqual([x["nombre"] for x in serie["grupos"]], ["Rodeo 3"])      # el resto de la serie sigue

    def test_los_valores_son_json(self):
        import json
        json.dumps(self._escenario())


class TestRodeosPorNombre(unittest.TestCase):
    """El análisis habla de rodeos con el NOMBRE que les da DelPro («Rodeo 3», «Rodeo 4 - Baja»), no con el
    identificador interno de la base (21, 25) ni con la palabra «grupo»."""

    NOMBRES = [[21, "Rodeo 3", 3], [22, "Rodeo 4 - Baja", 4]]

    def _correr(self, nombres=NOMBRES):
        por_dia = {f: [(i, 21, 45.0) for i in range(1, 31)] + [(100 + i, 22, 20.0) for i in range(1, 31)]
                   for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = [(i, 21, 36.0) for i in range(1, 31)] + [(100 + i, 22, 20.0) for i in range(1, 31)]
        pedidos = []

        def extra(sql):
            if "FROM AbstractGroup" in sql:
                pedidos.append(" ".join(sql.split()))
                return nombres
            return None
        r = _analizar(_filas_vacas(por_dia), extra=extra)
        r["pedidos"] = pedidos
        return r

    def test_los_datos_traen_el_nombre_del_rodeo(self):
        r = self._correr()
        self.assertEqual({g["grupo"] for g in r["datos"]["grupos"]}, {"Rodeo 3", "Rodeo 4 - Baja"})
        self.assertEqual({g["numero"] for g in r["datos"]["grupos"]}, {3, 4})

    def test_el_informe_dice_rodeo_y_el_nombre_no_el_identificador(self):
        t = self._correr()["informe"]
        self.assertIn("Rodeo 3", t)
        self.assertIn("EN QUÉ RODEOS", t)
        self.assertNotIn("EN QUÉ GRUPOS", t)
        self.assertNotRegex(t, r"grupo 21\b")

    def test_el_veredicto_nombra_el_rodeo(self):
        v = self._correr()["veredicto"]
        textos = " ".join(c["titulo"] + " ".join(c["evidencia"]) for c in v["sintomas"])
        self.assertIn("Rodeo 3", textos)
        self.assertNotIn("grupo", v["titular"].lower())

    def test_los_rodeos_salen_ordenados_por_su_numero_en_la_serie(self):
        self.assertEqual([g["nombre"] for g in self._correr()["datos"]["serie"]["grupos"]],
                         ["Rodeo 3", "Rodeo 4 - Baja"])

    def test_si_no_se_pueden_leer_los_nombres_lo_dice_y_no_inventa_un_rodeo(self):
        r = self._correr(nombres=None)
        nombres = {g["grupo"] for g in r["datos"]["grupos"]}
        self.assertEqual(nombres, {"Rodeo (id 21)", "Rodeo (id 22)"})
        self.assertIn("nombres de los rodeos", r["informe"])

    def test_pide_solo_los_rodeos_que_aparecen(self):
        self.assertIn("IN (21, 22)", self._correr()["pedidos"][0])

    def test_la_consulta_de_nombres_rechaza_lo_que_no_es_un_numero(self):
        with self.assertRaises(ValueError):
            dg.sql_rodeos(["21", "1); DROP TABLE x --"])


class TestFechasConcretasEnElInforme(unittest.TestCase):
    """El informe se lee días después: lleva las fechas (sábado 07/11, viernes 06/11), nunca «ayer» ni «ese día»."""

    def _informe(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 36.0)
        ev = [["2026-11-06", "tratamiento", "Desvasado Correctivo", 40]] + \
             [[f, "tratamiento", "Desvasado Correctivo", 2] for f in _fechas("2026-11-01", "2026-11-05")]

        def extra(s):
            if "EventCalving" in s:
                return ev
            if "DiagnosisTreatmentEvent" in s and "AS animal" in s:
                return [[1000 + i, "Desvasado Correctivo"] for i in range(1, 41)]
            return None
        return _analizar(_filas_vacas(por_dia), extra=extra)["informe"]

    def test_no_dice_ayer(self):
        import re
        t = self._informe()
        self.assertIsNone(re.search(r"(?i)\bayer\b", t), [l for l in t.splitlines() if re.search(r"(?i)\bayer\b", l)])
        self.assertIsNone(re.search(r"(?i)\bese día\b", t), [l for l in t.splitlines() if re.search(r"(?i)\bese día\b", l)])

    def test_la_comparacion_con_el_dia_anterior_lleva_su_fecha(self):
        t = self._informe()
        self.assertIn("viernes 06/11", t)
        self.assertIn("sábado 07/11", t)

    def test_la_tabla_de_eventos_encabeza_sus_columnas_con_las_fechas(self):
        linea = next(l for l in self._informe().splitlines() if "prom base/día" in l)
        self.assertIn("07/11", linea)
        self.assertIn("06/11", linea)

    def test_el_cruce_de_tratadas_nombra_el_dia(self):
        self.assertIn("vacas tratadas el viernes 06/11", self._informe())


class TestImpresionDelDiagnostico(unittest.TestCase):
    """La hoja es más angosta que la pantalla: los gráficos se dibujan al ancho de la pantalla y se desbordaban de su tarjeta (se
    cortaba «sáb 07-11» y «3.º ordeño»). Se probó imprimiendo la página real con el navegador; esto cuida que las reglas sigan."""

    @classmethod
    def setUpClass(cls):
        import pathlib
        cls.html = (pathlib.Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        i = cls.html.index("@media print {")
        cls.css_impresion = cls.html[i:cls.html.index("\n  }\n", i)]

    def test_los_graficos_se_achican_al_ancho_de_su_tarjeta(self):
        self.assertIn(".diag-chart canvas { width: 100% !important; height: auto !important", self.css_impresion)
        self.assertIn(".diag-chart { height: auto !important; }", self.css_impresion)

    def test_al_imprimir_el_diagnostico_se_oculta_el_resto_de_consultas(self):
        self.assertIn("body.imprime-diag #page-consultas > :not(#diag-card) { display: none !important; }", self.css_impresion)

    def test_los_graficos_se_redibujan_al_tamano_de_la_hoja_y_vuelven_a_la_pantalla(self):
        self.assertRegex(self.html, r"beforeprint[\s\S]{0,1400}c\.resize\(w, h\)")
        self.assertRegex(self.html, r"afterprint[\s\S]{0,400}c\.resize\(\)")

    def test_solo_se_oculta_lo_demas_si_lo_que_se_ve_es_el_diagnostico(self):
        self.assertIn("document.getElementById('resultado').hidden", self.html)      # la respuesta de una consulta se sigue imprimiendo


class TestCruceDeTratadas(unittest.TestCase):
    def _escenario(self, baja_las_tratadas):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = [(i, 21, (35.0 if i <= 40 and baja_las_tratadas else 40.0)) for i in range(1, 101)]
        ev = [["2026-11-06", "tratamiento", "Desvasado Correctivo", 40]] + \
             [[f, "tratamiento", "Desvasado Correctivo", 2] for f in _fechas("2026-11-01", "2026-11-05")]

        def extra(s):
            if "EventCalving" in s:
                return ev
            if "DiagnosisTreatmentEvent" in s and "AS animal" in s:
                return [[1000 + i, "Desvasado Correctivo"] for i in range(1, 41)]        # las vacas 1..40 el 06/11
            return []
        return _analizar(_filas_vacas(por_dia), extra)

    def test_si_las_tratadas_bajaron_el_cruce_lo_mide_y_el_veredicto_lo_marca(self):
        r = self._escenario(True)
        t = r["datos"]["tratadas_ayer"]
        self.assertEqual(t["n"], 40)
        self.assertAlmostEqual(t["delta_tratadas"], -5.0)
        self.assertAlmostEqual(t["delta_resto"], 0.0)
        self.assertAlmostEqual(t["efecto_kg"], -200.0)
        manejo = next(c for c in r["veredicto"]["causas"] if c["clave"] == "manejo")
        self.assertEqual(manejo["nivel"], "probable")

    def test_si_las_tratadas_rindieron_igual_se_descarta(self):
        r = self._escenario(False)
        manejo = next(c for c in r["veredicto"]["causas"] if c["clave"] == "manejo")
        self.assertEqual(manejo["nivel"], "descartada")

    def test_si_falla_la_consulta_de_tratadas_no_se_inventa_un_cruce(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-07")}

        def run_query(sql, validate=True, tambo="x", max_rows=5000):
            s = " ".join(sql.split())
            if "FROM AnimalDaily ad JOIN BasicAnimal b" in s:
                return {"columns": [], "rows": _filas_vacas(por_dia), "truncated": False}
            if "DiagnosisTreatmentEvent" in s and "AS animal" in s:
                raise RuntimeError("timeout")
            return {"columns": [], "rows": [], "truncated": False}

        with mock.patch.object(dg.db, "run_query", run_query), mock.patch.object(dg, "ith_de", lambda d: {}):
            r = dg.analizar("t", DIA, "2026-11-01", "2026-11-06")
        self.assertIsNone(r["datos"]["tratadas_ayer"])


class TestSegundoPlano(unittest.TestCase):
    def setUp(self):
        dg._JOBS.clear()

    def _esperar(self, job_id):
        for _ in range(100):
            j = dg.estado(job_id)
            if j["estado"] != "calculando":
                return j
            time.sleep(0.02)
        self.fail("el job no terminó")

    def test_termina_y_deja_el_informe(self):
        with mock.patch.object(dg, "generar", return_value="INFORME"):
            job = dg.iniciar("t", "2026-11-07", "2026-11-01", "2026-11-06")
            # el generar simulado es instantáneo: el hilo puede haber terminado antes de este chequeo
            self.assertIn(job["estado"], ("calculando", "listo"))
            j = self._esperar(job["id"])
        self.assertEqual((j["estado"], j["informe"], j["error"]), ("listo", "INFORME", None))

    def test_el_job_guarda_el_veredicto_y_los_datos_de_los_graficos(self):
        res = {"informe": "TXT", "datos": {"resumen": {"kg_d": 1}}, "veredicto": {"titular": "T", "nivel": "normal"}}
        with mock.patch.object(dg, "analizar", return_value=res):
            job = dg.iniciar("t", "2026-11-07", "2026-11-01", "2026-11-06")
            j = self._esperar(job["id"])
        self.assertEqual((j["informe"], j["veredicto"]["titular"], j["datos"]["resumen"]["kg_d"]), ("TXT", "T", 1))

    def test_el_mismo_pedido_en_curso_no_se_duplica(self):
        liberar = __import__("threading").Event()
        with mock.patch.object(dg, "generar", side_effect=lambda *a: (liberar.wait(2), "X")[1]):
            a = dg.iniciar("t", "2026-11-07", "2026-11-01", "2026-11-06")
            b = dg.iniciar("t", "2026-11-07", "2026-11-01", "2026-11-06")
            otro = dg.iniciar("t", "2026-11-06", "2026-10-30", "2026-11-05")
            self.assertEqual(a["id"], b["id"])
            self.assertNotEqual(a["id"], otro["id"])
            liberar.set()
            self._esperar(a["id"])
            self._esperar(otro["id"])

    def test_un_error_se_informa_sin_romper(self):
        with mock.patch.object(dg, "generar", side_effect=RuntimeError("se cayó la base")):
            job = dg.iniciar("t", "2026-11-07", "2026-11-01", "2026-11-06")
            j = self._esperar(job["id"])
        self.assertEqual(j["estado"], "error")
        self.assertIn("se cayó la base", j["error"])
        self.assertIsNone(j["informe"])

    def test_pedido_invalido_falla_al_iniciar_no_en_el_hilo(self):
        with self.assertRaises(dg.DiagnosticoError):
            dg.iniciar("t", "2026-11-07", "2026-11-06", "2026-11-01")

    def test_job_desconocido(self):
        self.assertIsNone(dg.estado("no-existe"))


class TestApi(unittest.TestCase):
    def setUp(self):
        dg._JOBS.clear()
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "x"

    def test_sin_sesion_no_entra(self):
        r = app.app.test_client().post("/api/diagnostico_baja", json={"dia": "2026-11-07"})
        self.assertEqual(r.status_code, 401)

    def test_inicia_y_consulta_el_resultado(self):
        with mock.patch.object(dg, "generar", return_value="INFORME"):
            r = self.client.post("/api/diagnostico_baja?tambo=lamartina_local",
                                 json={"dia": "2026-11-07", "base_desde": "2026-11-01", "base_hasta": "2026-11-06"})
            self.assertEqual(r.status_code, 200)
            job = r.get_json()
            self.assertIn(job["estado"], ("calculando", "listo"))
            for _ in range(100):
                j = self.client.get(f"/api/diagnostico_baja/{job['id']}").get_json()
                if j["estado"] != "calculando":
                    break
                time.sleep(0.02)
        self.assertEqual((j["estado"], j["informe"]), ("listo", "INFORME"))

    def test_el_veredicto_llega_a_la_pantalla_por_la_api(self):
        res = {"informe": "TXT", "datos": {"grupos": []},
               "veredicto": {"titular": "Dentro de lo normal", "nivel": "normal", "contexto": [], "causas": []}}
        with mock.patch.object(dg, "analizar", return_value=res):
            job = self.client.post("/api/diagnostico_baja?tambo=lamartina_local", json={"dia": "2026-11-07"}).get_json()
            for _ in range(100):
                j = self.client.get(f"/api/diagnostico_baja/{job['id']}").get_json()
                if j["estado"] != "calculando":
                    break
                time.sleep(0.02)
        self.assertEqual(j["veredicto"]["nivel"], "normal")
        self.assertEqual(j["datos"], {"grupos": []})

    def test_fechas_invalidas_son_400_con_el_motivo(self):
        r = self.client.post("/api/diagnostico_baja", json={"dia": "2026-11-07", "base_desde": "2026-11-06",
                                                           "base_hasta": "2026-11-01"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("posterior", r.get_json()["error"])

    def test_job_inexistente_es_404(self):
        self.assertEqual(self.client.get("/api/diagnostico_baja/nada").status_code, 404)


if __name__ == "__main__":
    unittest.main()
