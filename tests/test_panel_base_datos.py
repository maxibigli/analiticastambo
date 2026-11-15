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


if __name__ == "__main__":
    unittest.main()
