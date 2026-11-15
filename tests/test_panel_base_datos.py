"""Pestaña «Base de datos» del panel de despliegue: ajustes por tambo, qué tambos aparecen, actualizar la base
(parando y reiniciando LactIA) y dar acceso a delpro_lectura.

Nada de esto toca SQL Server ni procesos reales: `ejecutar`, `restaurar`, `parar_lactia` e `iniciar_lactia` van falsos, y
los ajustes se guardan en una carpeta temporal (NUNCA en el panel_despliegue.json real).

    C:/Python314/python.exe -m unittest tests.test_panel_base_datos -v
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import panel_despliegue as pd  # noqa: E402
import restaurar_backup as rb  # noqa: E402


class TestAjustes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "aj.json")

    def test_carpetas_bases_no_se_comparte_entre_cargas(self):
        a = pd.cargar_ajustes(self.ruta)
        a["carpetas_bases"]["x"] = "y"
        self.assertEqual(pd.cargar_ajustes(self.ruta)["carpetas_bases"], {})
        self.assertEqual(pd.AJUSTES_DEFECTO["carpetas_bases"], {})

    def test_carpetas_bases_ida_y_vuelta_y_basura(self):
        a = pd.cargar_ajustes(self.ruta)
        a["carpetas_bases"] = {"ponderosa": r"D:\bases\p"}
        pd.guardar_ajustes(a, self.ruta)
        self.assertEqual(pd.cargar_ajustes(self.ruta)["carpetas_bases"], {"ponderosa": r"D:\bases\p"})
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"carpetas_bases": ["no", "es", "dict"]}, f)
        self.assertEqual(pd.cargar_ajustes(self.ruta)["carpetas_bases"], {})


def _config():
    return {
        "ponderosa": {"server": "SERVER-DELPRO\\DELPRO", "database": "DDM", "nombre": "La Ponderosa"},
        "ponderosa_local": {"server": "localhost\\DELPRO", "database": "DDM", "nombre": "La Ponderosa (local)"},
        "lamartina_local": {"server": "localhost\\DELPRO", "database": "DDM_LAMARTINA", "nombre": "La Martina (local)"},
        "remoto": {"server": "192.168.1.20\\DELPRO", "database": "DDM", "nombre": "Remoto"},
        "sin_base": {"server": "localhost\\DELPRO", "nombre": "Sin base"},
    }


class TestTambosDelServidor(unittest.TestCase):
    def test_en_el_servidor_la_misma_base_sale_una_vez_y_gana_el_que_no_es_local(self):
        r = pd.tambos_del_servidor(_config(), equipo="SERVER-DELPRO")
        self.assertEqual([t["id"] for t in r], ["ponderosa", "lamartina_local"])
        self.assertEqual(r[0], {"id": "ponderosa", "nombre": "La Ponderosa", "base": "DDM"})

    def test_en_otra_pc_solo_los_locales(self):
        r = pd.tambos_del_servidor(_config(), equipo="MAXI-PC")
        self.assertEqual([t["id"] for t in r], ["ponderosa_local", "lamartina_local"])

    def test_otras_formas_de_decir_local(self):
        cfg = {"a": {"server": "localhost,1433", "database": "A", "nombre": "A"},
               "b": {"server": "LOCALHOST", "database": "B", "nombre": "B"},
               "c": {"server": ".\\DELPRO", "database": "C", "nombre": "C"}}
        self.assertEqual([t["id"] for t in pd.tambos_del_servidor(cfg, equipo="X")], ["a", "b", "c"])


class _ConDespliegue(unittest.TestCase):
    """Un Despliegue con LactIA, el restore y SQL de mentira: solo se anota el ORDEN de lo que pasa."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.carpeta = os.path.join(self.dir.name, "bases")
        os.mkdir(self.carpeta)
        self.log, self.orden, self.llamadas, self.preguntas = [], [], [], []
        self.corriendo, self.parar_ok, self.permitir, self.falla = True, True, True, None
        aj = dict(pd.AJUSTES_DEFECTO, carpetas_bases={"ponderosa": self.carpeta})
        self.d = pd.Despliegue(aj, entorno=mock.Mock(), log=self.log.append, confirmar=self._confirmar,
                               carpeta=self.dir.name)
        self.d._del_script = lambda *a, **k: [{"pid": 1, "nombre": "python.exe"}] if self.corriendo else []
        self.d.parar_lactia = self._parar
        self.d.iniciar_lactia = self._iniciar
        self.sql = []

    def _confirmar(self, mensaje):
        self.preguntas.append(mensaje)
        return self.permitir

    def _parar(self):
        self.orden.append("parar")
        return self.parar_ok

    def _iniciar(self):
        self.orden.append("iniciar")
        return True

    def _restaurar(self, *a, **kw):
        self.orden.append("restaurar")
        self.llamadas.append((a, kw))
        if self.falla:
            raise self.falla
        return {"estado": "restaurado"}

    def _ejecutar(self, sql, base="master"):
        self.sql.append(sql)
        return []

    def _resumen(self, estado="nuevo"):
        r = {"estado": estado, "backupdate": datetime.datetime(2026, 11, 14, 3, 10), "nombre": "b.zip",
             "restaurado": None, "saltados": ["raro.zip: cortado"] if estado == "sin_backup" else []}
        return lambda *a, **k: r

    def actualizar(self, estado="nuevo", anteriores=None, **kw):
        return self.d.actualizar_base("ponderosa", "DDM", ejecutar=self._ejecutar, resumen=self._resumen(estado),
                                      restaurar=self._restaurar, anteriores=anteriores or (lambda b, e: []), **kw)

    def log_texto(self):
        return chr(10).join(self.log)


class TestActualizarBase(_ConDespliegue):
    def test_para_restaura_y_reinicia_en_ese_orden(self):
        self.assertTrue(self.actualizar())
        self.assertEqual(self.orden, ["parar", "restaurar", "iniciar"])
        args, kw = self.llamadas[0]
        self.assertEqual(args[:3], ("ponderosa", "DDM", self.carpeta))
        self.assertTrue(kw["carpeta_propia"])
        self.assertIn("14/11/2026 03:10", self.preguntas[0])
        self.assertIn("DDM", self.preguntas[0])

    def test_si_el_restore_falla_igual_reinicia_lactia(self):
        self.falla = rb.RestauracionError("falla simulada")
        self.assertFalse(self.actualizar())
        self.assertEqual(self.orden, ["parar", "restaurar", "iniciar"])
        self.assertIn("falla simulada", self.log_texto())

    def test_si_el_restore_revienta_inesperado_igual_reinicia_y_propaga(self):
        self.falla = RuntimeError("inesperado")
        with self.assertRaises(RuntimeError):
            self.actualizar()
        self.assertEqual(self.orden, ["parar", "restaurar", "iniciar"])

    def test_si_lactia_estaba_parada_no_la_toca(self):
        self.corriendo = False
        self.assertTrue(self.actualizar())
        self.assertEqual(self.orden, ["restaurar"])

    def test_sin_backup_o_al_dia_no_para_lactia(self):
        self.assertFalse(self.actualizar("sin_backup"))
        self.assertTrue(self.actualizar("al_dia"))
        self.assertEqual(self.orden, [])
        self.assertIn("raro.zip", self.log_texto())

    def test_si_no_se_puede_parar_lactia_no_se_restaura(self):
        self.parar_ok = False
        self.assertFalse(self.actualizar())
        self.assertEqual(self.orden, ["parar"])

    def test_confirmacion_negada_no_hace_nada(self):
        self.permitir = False
        self.assertFalse(self.actualizar())
        self.assertEqual(self.orden, [])
        self.assertIn("Cancelado", self.log_texto())

    def test_solo_ver_no_confirma_ni_toca_lactia(self):
        self.assertTrue(self.actualizar(solo_ver=True))
        self.assertEqual(self.orden, ["restaurar"])
        self.assertTrue(self.llamadas[0][1]["solo_ver"])
        self.assertEqual(self.preguntas, [])

    def test_sin_carpeta_o_carpeta_inexistente_da_error_claro(self):
        self.d.aj["carpetas_bases"] = {}
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.actualizar()
        self.assertIn("carpeta", str(cm.exception))
        self.d.aj["carpetas_bases"] = {"ponderosa": os.path.join(self.dir.name, "no_existe")}
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.actualizar()
        self.assertIn("carpeta", str(cm.exception))
        self.assertEqual(self.orden, [])

    def test_avisa_cuantas_bases_anteriores_quedan(self):
        self.actualizar(anteriores=lambda b, e: ["DDM_ANT_1", "DDM_ANT_2"])
        texto = self.log_texto()
        self.assertIn("2", texto)
        self.assertIn("DDM_ANT_2", texto)
        self.assertNotIn("DROP", texto + "".join(self.sql))


class TestDarAcceso(_ConDespliegue):
    def test_dar_acceso_lectura(self):
        self.assertTrue(self.d.dar_acceso_lectura("DDM", self._ejecutar))
        self.assertTrue(any("USE [DDM]" in q for q in self.sql))
        self.assertEqual(self.orden, [])

    def test_dar_acceso_lectura_si_falla_lo_dice(self):
        def mala(sql, base="master"):
            raise rb.RestauracionError("sin permiso")
        self.assertFalse(self.d.dar_acceso_lectura("DDM", mala))
        self.assertIn("sin permiso", self.log_texto())


class TestInfoBase(_ConDespliegue):
    def test_info_base(self):
        self.d.aj["carpetas_bases"] = {}
        self.assertEqual(self.d.info_base("ponderosa")["estado"], "sin_carpeta")
        ruta = os.path.join(self.dir.name, "no_existe")
        self.d.aj["carpetas_bases"] = {"ponderosa": ruta}
        self.assertEqual(self.d.info_base("ponderosa"), {"estado": "carpeta_inexistente", "carpeta": ruta})
        self.d.aj["carpetas_bases"] = {"ponderosa": self.carpeta}
        self.assertEqual(self.d.info_base("ponderosa", resumen=self._resumen("al_dia"))["estado"], "al_dia")


if __name__ == "__main__":
    unittest.main()
