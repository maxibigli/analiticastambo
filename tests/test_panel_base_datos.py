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


class TestRevisionFinal(_ConDespliegue):
    """Lo que encontró la revisión final: cada caso falló antes del arreglo."""

    def test_restaurar_que_no_restaura_no_se_da_por_exito(self):
        self._restaurar_original = self._restaurar
        self.d_restaurar = lambda *a, **kw: (self.orden.append("restaurar"), {"estado": "sin_backup"})[1]
        ok = self.d.actualizar_base("ponderosa", "DDM", ejecutar=self._ejecutar, resumen=self._resumen("nuevo"),
                                    restaurar=self.d_restaurar, anteriores=lambda b, e: ["DDM_ANT_1"])
        self.assertFalse(ok)
        self.assertEqual(self.orden, ["parar", "restaurar", "iniciar"])      # LactIA igual vuelve
        self.assertNotIn("anterior", self.log_texto())
        self.assertIn("no restauró", self.log_texto())

    def test_si_no_se_pudo_parar_pero_ya_no_queda_lactia_se_la_inicia(self):
        def parar_a_medias():
            self.orden.append("parar")
            self.corriendo = False          # murió el proceso pero el panel no llegó a verificarlo
            return False
        self.d.parar_lactia = parar_a_medias
        self.assertFalse(self.actualizar())
        self.assertEqual(self.orden, ["parar", "iniciar"])

    def test_si_no_se_pudo_parar_y_sigue_viva_no_se_toca(self):
        self.parar_ok = False
        self.assertFalse(self.actualizar())
        self.assertEqual(self.orden, ["parar"])

    def test_rechaza_una_carpeta_con_subcarpetas_de_otros_tambos(self):
        os.mkdir(os.path.join(self.carpeta, "lamartina"))
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.actualizar()
        self.assertIn("lamartina", str(cm.exception))
        self.assertEqual(self.orden, [])
        self.assertEqual(self.d.info_base("ponderosa")["estado"], "error")

    def test_rechaza_una_carpeta_que_ya_es_de_otro_tambo(self):
        self.d.aj["carpetas_bases"] = {"ponderosa": self.carpeta, "lamartina_local": self.carpeta + os.sep}
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.actualizar()
        self.assertIn("lamartina_local", str(cm.exception))
        self.assertEqual(self.orden, [])

    def test_problema_de_carpeta_deja_pasar_una_carpeta_limpia(self):
        self.assertIsNone(pd.problema_de_carpeta(self.carpeta, "ponderosa", {"ponderosa": self.carpeta}))


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


class TestPantallaBase(unittest.TestCase):
    """Humo de la pestaña: se arma con tkinter de verdad (sin escritorio gráfico se saltea). NUNCA toca el
    panel_despliegue.json real ni el registro de Windows real."""

    TAMBOS = [{"id": "ponderosa", "nombre": "La Ponderosa", "base": "DDM"},
              {"id": "lamartina_local", "nombre": "La Martina (local)", "base": "DDM_LAMARTINA"}]

    def setUp(self):
        import uuid
        import winreg
        for nombre, kw in (("difundir_cambio", {}), ("guardar_ajustes", {}),
                           ("tambos_del_servidor", {"return_value": self.TAMBOS})):
            p = mock.patch.object(pd, nombre, **kw)
            m = p.start()
            self.addCleanup(p.stop)
            if nombre == "guardar_ajustes":
                self.guardar = m

        def clave():
            sub = "Software" + chr(92) + f"LactIA_PanelTest_{os.getpid()}_{uuid.uuid4().hex[:8]}"

            def limpiar():
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except OSError:
                    pass
            self.addCleanup(limpiar)
            return pd.Registro(winreg.HKEY_CURRENT_USER, sub, "Prueba")
        try:
            self.panel = pd.Panel(ajustes=dict(pd.AJUSTES_DEFECTO, carpetas_bases={}),
                                  entorno=pd.Entorno(usuario=clave(), equipo=clave()), visible=False)
        except Exception as e:  # noqa: BLE001  (sin escritorio gráfico, por ejemplo)
            self.skipTest(f"no se pudo abrir tkinter: {e}")
        self.addCleanup(self.panel.cerrar)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def esperar(self, condicion, vueltas=40):
        import time
        for _ in range(vueltas):
            self.panel.raiz.update()
            if condicion():
                return True
            time.sleep(0.05)
        return condicion()

    def texto_info(self, tid):
        return self.panel.filas_base[tid]["info"].cget("text")

    def test_hay_una_fila_por_tambo(self):
        self.assertEqual(set(self.panel.filas_base), {"ponderosa", "lamartina_local"})

    def test_elegir_carpeta_la_guarda_y_cancelar_no(self):
        with mock.patch("tkinter.filedialog.askdirectory", return_value=self.dir.name):
            self.panel._elegir_carpeta_base("ponderosa")
        self.assertEqual(self.panel.aj["carpetas_bases"]["ponderosa"], os.path.normpath(self.dir.name))
        self.assertEqual(self.panel.filas_base["ponderosa"]["var"].get(), os.path.normpath(self.dir.name))
        self.assertEqual(self.guardar.call_count, 1)
        with mock.patch("tkinter.filedialog.askdirectory", return_value=""):
            self.panel._elegir_carpeta_base("lamartina_local")
        self.assertNotIn("lamartina_local", self.panel.aj["carpetas_bases"])
        self.assertEqual(self.guardar.call_count, 1)

    def test_elegir_una_carpeta_con_otros_tambos_adentro_se_rechaza(self):
        os.mkdir(os.path.join(self.dir.name, "lamartina"))
        with mock.patch("tkinter.filedialog.askdirectory", return_value=self.dir.name), \
                mock.patch("tkinter.messagebox.showerror") as error:
            self.panel._elegir_carpeta_base("ponderosa")
        self.assertTrue(error.called)
        self.assertNotIn("ponderosa", self.panel.aj["carpetas_bases"])
        self.assertEqual(self.guardar.call_count, 0)

    def test_actualizar_base_usa_correr_con_la_base_y_solo_ver(self):
        capturado = []
        self.panel.correr = lambda titulo, fn: capturado.append((titulo, fn))
        self.panel.d.actualizar_base = mock.Mock(return_value=True)
        self.panel.d.info_base = mock.Mock(return_value={"estado": "sin_carpeta"})
        self.panel._actualizar_base("ponderosa")
        titulo, fn = capturado[0]
        self.assertIn("Actualizar base", titulo)
        fn()
        self.panel.d.actualizar_base.assert_called_with("ponderosa", "DDM", solo_ver=False)
        self.panel.var_solo_ver.set(True)
        self.panel._actualizar_base("ponderosa")
        capturado[1][1]()
        self.panel.d.actualizar_base.assert_called_with("ponderosa", "DDM", solo_ver=True)

    def test_dar_acceso_usa_correr_con_la_base(self):
        capturado = []
        self.panel.correr = lambda titulo, fn: capturado.append((titulo, fn))
        self.panel.d.dar_acceso_lectura = mock.Mock(return_value=True)
        self.panel._dar_acceso("lamartina_local")
        self.assertIn("delpro_lectura", capturado[0][0])
        capturado[0][1]()
        self.panel.d.dar_acceso_lectura.assert_called_with("DDM_LAMARTINA")

    def test_los_botones_de_base_se_deshabilitan_mientras_corre(self):
        self.panel._botones(False)
        for fila in self.panel.filas_base.values():
            self.assertEqual(str(fila["actualizar"].cget("state")), "disabled")
            self.assertEqual(str(fila["acceso"].cget("state")), "disabled")
        self.panel._botones(True)
        for fila in self.panel.filas_base.values():
            self.assertEqual(str(fila["actualizar"].cget("state")), "normal")

    def test_la_info_de_la_fila_se_pinta(self):
        self.panel._pintar_info_base("ponderosa", {
            "estado": "nuevo", "backupdate": datetime.datetime(2026, 11, 14, 3, 10), "nombre": "b.zip",
            "restaurado": "2026-11-12T06:00:00", "saltados": []})
        texto = self.texto_info("ponderosa")
        self.assertIn("14/11", texto)
        self.assertIn("12/11", texto)
        self.panel._pintar_info_base("ponderosa", {"estado": "sin_carpeta"})
        self.assertIn("sin carpeta", self.texto_info("ponderosa"))

    def test_buscar_backups_pinta_lo_que_trae_el_hilo(self):
        self.assertTrue(self.esperar(lambda: "sin carpeta" in self.texto_info("ponderosa")))
        self.panel.d.info_base = mock.Mock(return_value={"estado": "sin_backup", "saltados": []})
        self.panel._refrescar_bases()
        self.assertTrue(self.esperar(lambda: "sin backup completo" in self.texto_info("ponderosa")))

    def test_el_log_llega_a_las_dos_pestanas(self):
        self.panel._log("línea para las dos")
        self.assertTrue(self.esperar(lambda: "línea para las dos" in self.panel.txt_base.get("1.0", "end")))
        self.assertIn("línea para las dos", self.panel.txt.get("1.0", "end"))
        self.panel._limpiar_log()
        self.assertNotIn("línea para las dos", self.panel.txt_base.get("1.0", "end"))
        self.assertNotIn("línea para las dos", self.panel.txt.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
