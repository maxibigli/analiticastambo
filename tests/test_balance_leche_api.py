"""Endpoints y alerta del "Balance de leche" (app.py), con datos inyectados: esta PC no llega a
SERVER-DELPRO. Los números son los reales del 24 al 27/10/2026 (el 25 y 26 es la fecha corrida).

    C:\\Python314\\python.exe -m unittest discover -s tests -v
"""
import datetime
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import app
import balance_leche
import configuracion_tambo

app._warmup = lambda *a, **k: None   # el calentamiento de caché consultaría la base real

DELPRO = {"columns": ["fecha", "kg", "vacas"],
          "rows": [[datetime.date(2026, 10, 24), 66790.0, 1660], [datetime.date(2026, 10, 25), 71116.0, 1671],
                   [datetime.date(2026, 10, 26), 68553.0, 1674], [datetime.date(2026, 10, 27), 69125.0, 1659]]}


def _carga(dia, litros, venta):
    """Una carga con la forma real (la arma `cicla` desde un item de la API de Milky)."""
    return app.cicla._carga_desde_api({
        "Id": f"{dia}{int(venta)}", "FechaIni": f"2026-10-{dia:02d}T07:00:00", "LitrosTotal": litros,
        "LitrosEstimados": litros, "Tambo": 5003 if venta else 23, "TempPromedio": 3.5})


CARGAS = [_carga(24, 68030, True), _carga(24, 1951, False), _carga(25, 72690, True), _carga(25, 1859, False),
          _carga(26, 69949, True), _carga(26, 1824, False), _carga(27, 70937, True), _carga(27, 1894, False)]
LASER = {"entregas": [{"fecha": "24/10/2026", "litros": 68000}, {"fecha": "25/10/2026", "litros": 47800},
                      {"fecha": "26/10/2026", "litros": 95800}, {"fecha": "27/10/2026", "litros": 70900}],
         "actual": [], "anterior": []}
URL = "?desde=2026-10-24&hasta=2026-10-27"
TAMBO = app.tambos.DEFAULT_TAMBO


class Base(unittest.TestCase):
    def setUp(self):
        app._cache.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for objeto, nombre, valor in (
                (configuracion_tambo, "_RUTA", os.path.join(self._tmp.name, "cfg.json")),
                (balance_leche, "_RUTA_AVISOS", os.path.join(self._tmp.name, "avisos.json")),
                (app.cicla, "credenciales", lambda: ("usuario", "clave")),
                (app.cicla, "obtener_cargas", lambda *a, **k: (list(CARGAS), False)),
                (app, "_laser_traer", lambda *a, **k: LASER),
                # Por defecto el tambo no tiene despachos cargados en DelPro (`db.run_query` está simulado con kilos).
                (app.despachos_delpro, "obtener_cargas", lambda *a, **k: ([], False)),
                (app.despachos_delpro, "uso_interno", lambda *a, **k: []),
                # Por defecto sin precio de la leche (no se lee la planilla real de esta PC): sin pesos.
                (app.precios_alimentos, "leer", lambda *a, **k: {"precio_litro": None, "error": "sin planilla (prueba)"}),
                (app.db, "run_query", lambda *a, **k: DELPRO)):
            p = mock.patch.object(objeto, nombre, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"LASER_USUARIO": "u", "LASER_PASSWORD": "p"})
        p.start()
        self.addCleanup(p.stop)
        self.client = app.app.test_client()
        with self.client.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"


class TestComparar(Base):
    def test_comparar_sin_cambios(self):
        """Red para el refactor: la respuesta de /api/entregas/comparar no cambia."""
        r = self.client.get("/api/entregas/comparar" + URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), GOLDEN_COMPARAR)


class TestBalance(Base):
    def _get(self, extra=URL):
        return self.client.get("/api/balance/leche" + extra)

    def test_balance_ok(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual((d["desde"], d["hasta"]), ("2026-10-24", "2026-10-27"))
        self.assertEqual(d["factor"], 1.02)
        self.assertEqual(d["factor_defecto"], 1.02)
        self.assertIs(d["comprador_disponible"], True)
        self.assertIs(d["cicla_incompleto"], False)
        dias = {x["fecha"]: x for x in d["dias"]}
        self.assertEqual(len(dias), 4)
        self.assertEqual(dias["2026-10-25"]["desvio_sala_tanque_pct"], 2.8)      # 74549 / (71116 x 1,02)
        self.assertEqual(dias["2026-10-25"]["desvio_tanque_camion_pct"], 52.1)
        for f in ("2026-10-25", "2026-10-26"):
            self.assertIs(dias[f]["corrida_tanque_camion"], True)

    def test_fechas_por_defecto(self):
        d = self._get("").get_json()
        hoy = datetime.date.today()
        self.assertEqual(d["hasta"], hoy.isoformat())
        self.assertEqual(d["desde"], (hoy - datetime.timedelta(days=20)).isoformat())

    def test_fechas_invertidas(self):
        d = self._get("?desde=2026-10-27&hasta=2026-10-24").get_json()
        self.assertEqual((d["desde"], d["hasta"]), ("2026-10-24", "2026-10-27"))

    def test_fechas_invalidas(self):
        self.assertEqual(self._get("?desde=ayer").status_code, 400)

    def test_sin_comprador(self):
        for v in ("LASER_USUARIO", "LASER_PASSWORD"):
            os.environ.pop(v, None)
        r = self._get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["comprador_disponible"], False)
        self.assertTrue(all(x["desvio_tanque_camion_pct"] is None for x in d["dias"]))

    def test_comprador_caido_no_corta_el_balance(self):
        with mock.patch.object(app, "_laser_traer", side_effect=RuntimeError("sin red")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["comprador_disponible"], False)

    def test_sin_credenciales_de_milky(self):
        with mock.patch.object(app.cicla, "credenciales", lambda: (None, None)):
            r = self._get()
        self.assertEqual(r.status_code, 400)
        self.assertIn("MILKY_USUARIO", r.get_json()["error"])

    def test_milky_caido(self):
        with mock.patch.object(app.cicla, "obtener_cargas", side_effect=RuntimeError("sesión vencida")):
            r = self._get()
        self.assertEqual(r.status_code, 502)
        self.assertIn("CICLA", r.get_json()["error"])

    def test_delpro_caido(self):
        with mock.patch.object(app.db, "run_query", side_effect=RuntimeError("sin red")):
            r = self._get()
        self.assertEqual(r.status_code, 502)
        self.assertIn("DelPro", r.get_json()["error"])

    def test_requiere_admin(self):
        anonimo = app.app.test_client()
        esperado = anonimo.get("/api/entregas/comparar" + URL).status_code
        self.assertNotEqual(esperado, 200)
        self.assertEqual(anonimo.get("/api/balance/leche" + URL).status_code, esperado)
        self.assertEqual(anonimo.post("/api/balance/factor", json={"factor": 1.1}).status_code, esperado)

    def test_factor_post_y_efecto(self):
        r = self.client.post("/api/balance/factor", json={"factor": "1,06"})
        self.assertEqual((r.status_code, r.get_json()), (200, {"factor": 1.06}))
        d = self._get().get_json()
        self.assertEqual(d["factor"], 1.06)
        dia = [x for x in d["dias"] if x["fecha"] == "2026-10-25"][0]
        self.assertEqual(dia["lts_delpro"], round(71116 * 1.06))
        malo = self.client.post("/api/balance/factor", json={"factor": 0})
        self.assertEqual(malo.status_code, 400)
        self.assertIn("error", malo.get_json())
        self.assertEqual(self._get().get_json()["factor"], 1.06)    # no pisó lo guardado

    def test_plata_de_la_diferencia_con_el_precio_de_la_planilla(self):
        planilla = {"precio_litro": 500.0, "leche_estimada": True, "leche_via": "manual", "error": None}
        with mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: planilla):
            d = self._get().get_json()
        self.assertEqual(d["precio_leche"]["valor"], 500.0)
        self.assertIs(d["precio_leche"]["estimado"], True)          # la planilla lo marca ESTIMADO: se avisa en pantalla
        self.assertEqual(d["precio_leche"]["via"], "manual")
        self.assertIsNone(d["precio_leche"]["error"])
        dias = {x["fecha"]: x for x in d["dias"]}
        self.assertEqual(dias["2026-10-25"]["lts_dif_camion"], 24890.0)         # 72690 vendidos - 47800 reconocidos
        self.assertEqual(dias["2026-10-25"]["valor_dif_camion"], 12445000)
        self.assertEqual(dias["2026-10-26"]["valor_dif_camion"], -12925500)
        # El 25 y el 26 se compensan (fecha corrida): el período neto es -894 L, no la suma de los valores absolutos.
        self.assertEqual(d["totales"]["lts_dif_camion"], -894)
        self.assertEqual(d["totales"]["valor_dif_camion"], -447000)
        self.assertEqual(d["totales"]["lts_otros_destinos"], 7528)              # 1951 + 1859 + 1824 + 1894 de guachera
        self.assertEqual(d["totales"]["pct_otros_destinos"], 2.6)

    def test_sin_precio_en_la_planilla_no_inventa_pesos(self):
        d = self._get().get_json()          # la Base simula una planilla sin precio
        self.assertIsNone(d["precio_leche"]["valor"])
        self.assertTrue(d["precio_leche"]["error"])
        self.assertTrue(all(x["valor_dif_camion"] is None for x in d["dias"]))
        self.assertIsNone(d["totales"]["valor_dif_camion"])
        self.assertEqual(d["totales"]["lts_dif_camion"], -894)                  # los litros salen igual

    def test_planilla_ilegible_no_corta_el_balance(self):
        with mock.patch.object(app.precios_alimentos, "leer", side_effect=RuntimeError("archivo abierto en Excel")):
            r = self._get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIsNone(d["precio_leche"]["valor"])
        self.assertIn("archivo abierto en Excel", d["precio_leche"]["error"])
        self.assertIs(d["comprador_disponible"], True)

    def test_planilla_con_precio_cero_es_sin_precio(self):
        with mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: {"precio_litro": 0, "error": None}):
            d = self._get().get_json()
        self.assertIsNone(d["precio_leche"]["valor"])
        self.assertTrue(d["precio_leche"]["error"])
        self.assertIsNone(d["totales"]["valor_dif_camion"])


    def test_el_precio_de_configuracion_manda_sobre_la_planilla(self):
        configuracion_tambo.guardar(TAMBO, {"precio_leche": 600})
        planilla = {"precio_litro": 500.0, "leche_estimada": True, "leche_via": "usd", "leche_usd_litro": 0.33}
        with mock.patch.object(app.precios_alimentos, "leer", lambda *a, **k: planilla):
            d = self._get().get_json()
        p = d["precio_leche"]
        self.assertEqual((p["valor"], p["estimado"], p["via"]), (600.0, False, "configuracion"))
        self.assertIsNone(p["error"])
        dias = {x["fecha"]: x for x in d["dias"]}
        self.assertEqual(dias["2026-10-25"]["valor_dif_camion"], 14934000)          # 24.890 L x $600
        self.assertEqual(d["totales"]["valor_dif_camion"], -536400)                  # -894 L x $600

    def test_el_precio_de_configuracion_alcanza_sin_planilla(self):
        configuracion_tambo.guardar(TAMBO, {"precio_leche": 600})
        d = self._get().get_json()          # la Base simula una planilla sin precio
        self.assertEqual(d["precio_leche"]["valor"], 600.0)
        self.assertEqual(d["totales"]["valor_dif_camion"], -536400)

    def test_un_precio_de_configuracion_se_nota_aunque_la_planilla_no_se_pueda_leer(self):
        configuracion_tambo.guardar(TAMBO, {"precio_leche": 600})
        with mock.patch.object(app.precios_alimentos, "leer", side_effect=RuntimeError("abierto en Excel")):
            d = self._get().get_json()
        self.assertEqual(d["precio_leche"]["valor"], 600.0)


class TestCacheVencida(Base):
    """Los datos del comprador y de Milky no pueden quedar congelados en la caché: la alerta del balance los
    usa y, con el comprador congelado, el tramo tanque -> camión dejaba de alertar sin avisar."""

    def _vencer(self, clave, valor):
        app._cache[clave] = (time.time() - 100000, valor)

    def test_comprador_vencido_se_vuelve_a_traer(self):
        self._vencer("laser:actual", {"entregas": [], "viejo": True})
        self.assertEqual(app._entregas_laser("u", "p"), LASER)
        self.assertEqual(app._cache["laser:actual"][1], LASER)

    def test_comprador_vencido_y_caido_devuelve_lo_viejo(self):
        self._vencer("laser:actual", {"viejo": True})
        with mock.patch.object(app, "_laser_traer", side_effect=RuntimeError("sin red")):
            self.assertEqual(app._entregas_laser("u", "p"), {"viejo": True})

    def test_comprador_sin_cache_y_caido_lanza(self):
        with mock.patch.object(app, "_laser_traer", side_effect=RuntimeError("sin red")):
            with self.assertRaises(RuntimeError):
                app._entregas_laser("u", "p")

    def test_milky_vencido_se_vuelve_a_traer(self):
        desde, hasta = datetime.date(2026, 10, 24), datetime.date(2026, 10, 27)
        self._vencer(f"cicla:{desde}:{hasta}", {"cargas": [], "incompleto": False})
        self.assertEqual(len(app._cargas_cicla(desde, hasta, "u", "p")["cargas"]), len(CARGAS))


class TestTamboConMilky(Base):
    """Milky y La Serenísima son de UNA instalación: otro tambo no puede cruzarse con esos litros."""

    def test_otro_tambo_sin_caudalimetro_configurado(self):
        r = self.client.get("/api/balance/leche" + URL + "&tambo=lamartina_local")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Milky", r.get_json()["error"])

    def test_otro_tambo_con_caudalimetro_y_usina_configurados(self):
        configuracion_tambo.guardar("lamartina_local", {"caudalimetro": "cicla", "usina_lactea": "la_serenisima"})
        r = self.client.get("/api/balance/leche" + URL + "&tambo=lamartina_local")
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["comprador_disponible"], True)

    def test_otro_tambo_con_milky_pero_sin_usina_no_trae_comprador(self):
        configuracion_tambo.guardar("lamartina_local", {"caudalimetro": "cicla"})
        d = self.client.get("/api/balance/leche" + URL + "&tambo=lamartina_local").get_json()
        self.assertIs(d["comprador_disponible"], False)

    def test_la_alerta_de_otro_tambo_no_dice_nada(self):
        self.assertEqual(app._lineas_alerta_balance("lamartina_local", registrar=True), [])


class TestAlerta(Base):
    """`_lineas_alerta_balance`: el aviso del desvío en las alertas puntuales (tambo por defecto, que es el de Milky)."""

    def setUp(self):
        super().setUp()
        kg = [{"fecha": "2026-11-01", "kg": 70524.0, "vacas": 1678}, {"fecha": "2026-11-02", "kg": 68402.0, "vacas": 1608},
              {"fecha": "2026-11-03", "kg": 71000.0, "vacas": 1650}]
        milky = {"2026-11-01": {"venta": 71066, "total": 73141}, "2026-11-02": {"venta": 71990, "total": 74127},
                 "2026-11-03": {"venta": 72000, "total": 74500}}
        self.balance = balance_leche.calcular(kg, milky, None, 1.05)    # el 02/11 da +3,2 %
        for objeto, nombre, valor in ((app, "_balance_leche", lambda *a, **k: self.balance),
                                      (app, "_refresh_rutina_async", lambda *a, **k: None),
                                      (app.laserenisima, "obtener_entregas", lambda *a, **k: [])):
            p = mock.patch.object(objeto, nombre, valor)
            p.start()
            self.addCleanup(p.stop)

    def test_linea_sin_registrar_no_consume_el_aviso(self):
        lineas = app._lineas_alerta_balance(TAMBO, registrar=False)
        self.assertEqual(len(lineas), 1)
        self.assertIn("+3,2 %", lineas[0])
        self.assertEqual(balance_leche.leer_avisados(TAMBO), {})
        self.assertEqual(app._lineas_alerta_balance(TAMBO, registrar=False), lineas)   # sigue disponible

    def test_registrar_no_repite(self):
        self.assertEqual(len(app._lineas_alerta_balance(TAMBO, registrar=True)), 1)
        self.assertEqual(balance_leche.leer_avisados(TAMBO)["sala_tanque"], "2026-11-02")
        self.assertEqual(app._lineas_alerta_balance(TAMBO, registrar=True), [])

    def test_balance_roto_es_una_linea_de_advertencia(self):
        with mock.patch.object(app, "_balance_leche", side_effect=app.BalanceError("DelPro: sin red", 502)):
            lineas = app._lineas_alerta_balance(TAMBO, registrar=True)
        self.assertEqual(len(lineas), 1)
        self.assertTrue(lineas[0].startswith("⚠️ Balance de leche"))
        self.assertIn("sin red", lineas[0])
        self.assertEqual(balance_leche.leer_avisados(TAMBO), {})

    def test_sin_credenciales_de_milky_no_hay_linea(self):
        with mock.patch.object(app.cicla, "credenciales", lambda: (None, None)):
            self.assertEqual(app._lineas_alerta_balance(TAMBO, registrar=True), [])

    def test_balance_roto_no_corta_las_demas_alertas(self):
        carga_caliente = app.cicla._carga_desde_api({
            "Id": "1", "FechaIni": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "LitrosTotal": 20000,
            "LitrosEstimados": 20000, "Tambo": 5003, "TempPromedio": 6.0})
        with mock.patch.object(app, "_balance_leche", side_effect=app.BalanceError("DelPro: sin red", 502)),                 mock.patch.object(app.cicla, "obtener_cargas", lambda *a, **k: ([carga_caliente], False)):
            lineas = app._lineas_alertas_puntuales(TAMBO)
        self.assertTrue(any("CICLA" in l and "temperatura" in l for l in lineas), lineas)
        self.assertTrue(any(l.startswith("⚠️ Balance de leche") for l in lineas), lineas)

    def test_el_ciclo_real_registra_y_el_boton_probar_no(self):
        import inspect
        self.assertIs(inspect.signature(app._construir_mensaje_alertas).parameters["registrar"].default, False)
        self.assertIs(inspect.signature(app._lineas_alertas_puntuales).parameters["registrar"].default, False)
        with mock.patch.object(app, "_canales_disponibles", lambda: True),                 mock.patch.object(app, "_construir_mensaje_alertas", return_value=(None, None)) as armar:
            app._revisar_alertas_whatsapp()
        armar.assert_called_once_with(app.tambos.DEFAULT_TAMBO, registrar=True)


# --- Camión desde los despachos de DelPro (tambos sin caudalímetro o sin La Serenísima) ----------------------
# Los despachos son SIMULADOS (tests/simulacion_despachos.py, con la forma de San José) a partir de los kg reales.
SIN_MILKY = "lamartina_local"        # no es el tambo por defecto y no declara caudalímetro: no tiene Milky
KG_SIM = {f"2026-10-{d}": kg for d, kg in ((24, 66790.0), (25, 71116.0), (26, 68553.0), (27, 69125.0))}


class BaseDespachos(Base):
    def simular(self, **kw):
        try:
            from simulacion_despachos import simular_despachos   # unittest discover -s tests
        except ImportError:
            from tests.simulacion_despachos import simular_despachos   # unittest tests.test_...
        cargas, uso = simular_despachos(KG_SIM, **kw)
        self.llamadas = {"cargas": 0, "uso": 0}

        def _cargas(*a, **k):
            self.llamadas["cargas"] += 1
            return list(cargas), False

        def _uso(*a, **k):
            self.llamadas["uso"] += 1
            return list(uso)
        for nombre, valor in (("obtener_cargas", _cargas), ("uso_interno", _uso)):
            p = mock.patch.object(app.despachos_delpro, nombre, valor)
            p.start()
            self.addCleanup(p.stop)

    def get(self, tambo=SIN_MILKY):
        return self.client.get("/api/balance/leche" + URL + "&tambo=" + tambo)


class TestSinCaudalimetro(BaseDespachos):
    """Sin Milky no hay tanque: el camión (despachos de DelPro) se compara directo con la sala."""

    def test_un_dia_normal_da_cero_y_la_perdida_se_ve(self):
        self.simular(perdidas={"2026-10-25": -9.0})
        r = self.get()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertIs(d["tanque_disponible"], False)
        self.assertEqual(d["camion_fuente"], "delpro")
        dias = {x["fecha"]: x for x in d["dias"]}
        for f in ("2026-10-24", "2026-10-26", "2026-10-27"):
            self.assertLess(abs(dias[f]["desvio_sala_camion_pct"]), 1.5, f)
            self.assertFalse(dias[f]["alerta_sala_camion"], f)
        self.assertLess(dias["2026-10-25"]["desvio_sala_camion_pct"], -7)
        self.assertTrue(dias["2026-10-25"]["alerta_sala_camion"])
        self.assertIsNone(dias["2026-10-25"]["desvio_sala_tanque_pct"])
        self.assertIsNotNone(dias["2026-10-25"]["lts_uso_interno"])
        self.assertEqual(d["tramo_mayor"], "sala_camion")

    def test_el_uso_interno_cuenta_en_el_camion(self):
        """Sin sumarlo, cada día daría −5 %: es la leche de la guachera que sale de la sala y no va en el camión."""
        self.simular()
        d = self.get().get_json()
        con = d["totales"]["desvio_sala_camion_pct"]
        sin = round((d["totales"]["lts_despachado"] / d["totales"]["lts_delpro_camion"] - 1) * 100, 1)
        self.assertLess(abs(con), 1.0)
        self.assertLess(sin, -3.5)

    def test_una_carga_corrida_de_dia_no_es_perdida(self):
        self.simular(corridas={"2026-10-25": 5500.0})
        dias = {x["fecha"]: x for x in self.get().get_json()["dias"]}
        for f in ("2026-10-25", "2026-10-26"):
            self.assertTrue(dias[f]["corrida_sala_camion"], f)
            self.assertFalse(dias[f]["alerta_sala_camion"], f)

    def test_sin_despachos_ni_comprador_es_un_error_claro(self):
        p = mock.patch.object(app.despachos_delpro, "obtener_cargas", lambda *a, **k: ([], False))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(app.despachos_delpro, "uso_interno", lambda *a, **k: [])
        p.start()
        self.addCleanup(p.stop)
        r = self.get()
        self.assertEqual(r.status_code, 400)
        self.assertIn("despachos", r.get_json()["error"])

    def test_falla_la_lectura_de_despachos(self):
        p = mock.patch.object(app.despachos_delpro, "obtener_cargas",
                              side_effect=app.despachos_delpro.DespachosError("sin red"))
        p.start()
        self.addCleanup(p.stop)
        r = self.get()
        self.assertEqual(r.status_code, 502)
        self.assertIn("sin red", r.get_json()["error"])

    def test_no_pide_credenciales_de_milky(self):
        self.simular()
        with mock.patch.object(app.cicla, "credenciales", lambda: (None, None)):
            self.assertEqual(self.get().status_code, 200)


class TestCamionConCaudalimetro(BaseDespachos):
    """Con Milky, el camión sigue siendo tanque -> camión; solo cambia de dónde sale el comprador."""

    def test_la_serenisima_configurada_manda_y_no_se_leen_los_despachos(self):
        self.simular()
        d = self.client.get("/api/balance/leche" + URL).get_json()
        self.assertEqual(d["camion_fuente"], "la_serenisima")
        self.assertEqual(self.llamadas, {"cargas": 0, "uso": 0})
        self.assertEqual({x["fecha"]: x["desvio_tanque_camion_pct"] for x in d["dias"]}["2026-10-25"], 52.1)

    def test_si_la_serenisima_falla_no_se_cambia_de_fuente_en_silencio(self):
        self.simular()
        with mock.patch.object(app, "_laser_traer", side_effect=RuntimeError("login vencido")):
            d = self.client.get("/api/balance/leche" + URL).get_json()
        self.assertIs(d["comprador_disponible"], False)
        self.assertIsNone(d["camion_fuente"])
        self.assertEqual(self.llamadas, {"cargas": 0, "uso": 0})

    def test_sin_serenisima_el_camion_son_los_despachos_de_delpro(self):
        self.simular()
        with mock.patch.dict(os.environ, {"LASER_USUARIO": "", "LASER_PASSWORD": ""}):
            d = self.client.get("/api/balance/leche" + URL).get_json()
        self.assertIs(d["tanque_disponible"], True)
        self.assertEqual(d["camion_fuente"], "delpro")
        self.assertIs(d["comprador_disponible"], True)
        dias = {x["fecha"]: x for x in d["dias"]}
        self.assertIsNotNone(dias["2026-10-26"]["desvio_tanque_camion_pct"])
        self.assertIsNone(dias["2026-10-26"]["desvio_sala_camion_pct"])     # con tanque no hay sala -> camión

    def test_sin_serenisima_y_sin_despachos_el_balance_sale_igual(self):
        with mock.patch.dict(os.environ, {"LASER_USUARIO": "", "LASER_PASSWORD": ""}), \
                mock.patch.object(app.despachos_delpro, "obtener_cargas", lambda *a, **k: ([], False)), \
                mock.patch.object(app.despachos_delpro, "uso_interno", lambda *a, **k: []):
            d = self.client.get("/api/balance/leche" + URL).get_json()
        self.assertIs(d["comprador_disponible"], False)
        self.assertIsNone(d["camion_fuente"])

    def test_despachos_caidos_con_milky_avisan_pero_no_cortan(self):
        with mock.patch.dict(os.environ, {"LASER_USUARIO": "", "LASER_PASSWORD": ""}), \
                mock.patch.object(app.despachos_delpro, "obtener_cargas",
                                  side_effect=app.despachos_delpro.DespachosError("sin red")):
            r = self.client.get("/api/balance/leche" + URL)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["comprador_disponible"], False)
        self.assertTrue(any("sin red" in a for a in d["avisos"]), d["avisos"])


class TestAlertaSinCaudalimetro(BaseDespachos):
    def test_alerta_de_sala_a_camion(self):
        # Sin registro previo solo se evalúa el último día evaluable (el 26/10: el 27/10 ya existe).
        self.simular(perdidas={"2026-10-26": -9.0})
        lineas = app._lineas_alerta_balance(SIN_MILKY, registrar=False)
        self.assertEqual(len(lineas), 1, lineas)
        self.assertIn("sala → camión", lineas[0])
        self.assertIn("26/10", lineas[0])

    def test_un_tambo_sin_nada_que_comparar_no_dice_nada(self):
        with mock.patch.object(app.despachos_delpro, "obtener_cargas", lambda *a, **k: ([], False)), \
                mock.patch.object(app.despachos_delpro, "uso_interno", lambda *a, **k: []):
            self.assertEqual(app._lineas_alerta_balance(SIN_MILKY, registrar=True), [])
        self.assertEqual(balance_leche.leer_avisados(SIN_MILKY), {})


# Respuesta capturada ANTES de extraer `_cargas_cicla`/`_entregas_laser` de `api_entregas_comparar`.
GOLDEN_COMPARAR = json.loads('''
{
 "cargas_totales": 8,
 "cargas_venta": 4,
 "cicla_incompleto": false,
 "desde": "2026-10-24",
 "destinos": [
  {
   "cargas": 4,
   "codigos": [
    "5003"
   ],
   "destino": "serenisima",
   "label": "La Serenísima",
   "lts_cicla": 281606.0,
   "pct": 97.4
  },
  {
   "cargas": 4,
   "codigos": [
    "23"
   ],
   "destino": "guachera",
   "label": "Guachera",
   "lts_cicla": 7528.0,
   "pct": 2.6
  }
 ],
 "destinos_sin_clasificar": [],
 "dias": [
  {
   "alerta": false,
   "diferencia": 30,
   "diferencia_pct": 0.0,
   "fecha": "2026-10-24",
   "lts_cicla": 68030,
   "lts_otros_destinos": 1951,
   "lts_serenisima": 68000
  },
  {
   "alerta": true,
   "diferencia": 24890,
   "diferencia_pct": 52.1,
   "fecha": "2026-10-25",
   "lts_cicla": 72690,
   "lts_otros_destinos": 1859,
   "lts_serenisima": 47800
  },
  {
   "alerta": true,
   "diferencia": -25851,
   "diferencia_pct": -27.0,
   "fecha": "2026-10-26",
   "lts_cicla": 69949,
   "lts_otros_destinos": 1824,
   "lts_serenisima": 95800
  },
  {
   "alerta": false,
   "diferencia": 37,
   "diferencia_pct": 0.1,
   "fecha": "2026-10-27",
   "lts_cicla": 70937,
   "lts_otros_destinos": 1894,
   "lts_serenisima": 70900
  }
 ],
 "hasta": "2026-10-27",
 "lts_otros_destinos": 7528
}
''')


if __name__ == "__main__":
    unittest.main()
