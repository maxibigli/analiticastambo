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
        self.assertIn("que NO se ordeñaron el 2026-11-07: 10", t)
        self.assertIn("comparado con 2026-11-01 a 2026-11-06", t)

    def test_rindio_menos_cada_vaca(self):
        por_dia = {f: _rodeo(100, 40.0) for f in _fechas("2026-11-01", "2026-11-06")}
        por_dia["2026-11-07"] = _rodeo(100, 36.0)                        # las 100 dan 4 kg menos
        t = _informe(_filas_vacas(por_dia))
        self.assertIn("BAJÓ EL RINDE POR VACA", t)
        self.assertIn("-400 kg", t)
        self.assertIn("que NO se ordeñaron el 2026-11-07: 0", t)

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

    def test_fechas_invalidas_son_400_con_el_motivo(self):
        r = self.client.post("/api/diagnostico_baja", json={"dia": "2026-11-07", "base_desde": "2026-11-06",
                                                           "base_hasta": "2026-11-01"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("posterior", r.get_json()["error"])

    def test_job_inexistente_es_404(self):
        self.assertEqual(self.client.get("/api/diagnostico_baja/nada").status_code, 404)


if __name__ == "__main__":
    unittest.main()
