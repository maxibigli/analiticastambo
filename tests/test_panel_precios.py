"""Pestaña «Precios» del panel de despliegue: llevar la planilla de precios (Precios_alimentos.xlsx) al servidor.

El archivo NO está en git (es de cada instalación), así que un `git pull` nunca lo actualiza. Sin esto, la planilla del
servidor se quedaba con los nombres de Haasten y los litros libres daban «Sin datos todavía».

Nada de esto toca LactIA, procesos ni el panel_despliegue.json reales: LactIA va de mentira y todo se guarda en carpetas
temporales.

    C:/Python314/python.exe -m unittest tests.test_panel_precios -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import panel_despliegue as pd  # noqa: E402


def _planilla(**insumos):
    """Lo que devuelve `precios_alimentos.leer` para una planilla con estos insumos: {nombre: (precio_kg, estimado)}."""
    return {"error": None, "tipo_cambio": 1500.0, "precio_litro": 525.0,
            "precios": {n.lower(): {"nombre": n, "precio_kg": p, "estimado": e}
                        for n, (p, e) in insumos.items()}}


VIEJA = _planilla(**{"SILO DE MAIZ": (90.0, True), "HARINA SOJA": (600.0, True)})
NUEVA = _planilla(**{"SILO DE MAIZ": (95.0, True), "HARINA SOJA": (600.0, True),
                     "Silo Maiz 1 24-25": (112.5, False), "Harina de Soja": (630.0, False)})


class TestCompararPrecios(unittest.TestCase):
    def test_cuenta_agregados_quitados_cambiados_y_estimados(self):
        c = pd.comparar_precios(NUEVA, VIEJA)
        self.assertEqual((c["insumos_nueva"], c["insumos_actual"]), (4, 2))
        self.assertEqual(sorted(c["agregados"]), ["Harina de Soja", "Silo Maiz 1 24-25"])
        self.assertEqual(c["quitados"], [])
        self.assertEqual(c["cambiados"], [("SILO DE MAIZ", 90.0, 95.0)])
        self.assertEqual((c["estimados_nueva"], c["estimados_actual"]), (2, 2))

    def test_los_que_la_nueva_no_tiene_figuran_como_quitados(self):
        c = pd.comparar_precios(VIEJA, NUEVA)
        self.assertEqual(sorted(c["quitados"]), ["Harina de Soja", "Silo Maiz 1 24-25"])

    def test_sin_planilla_actual_todo_es_agregado(self):
        c = pd.comparar_precios(NUEVA, None)
        self.assertEqual(c["insumos_actual"], 0)
        self.assertEqual(len(c["agregados"]), 4)
        self.assertEqual(c["cambiados"], [])


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.app = os.path.join(self.dir.name, "app")          # la carpeta de LactIA en el servidor
        os.mkdir(self.app)
        self.destino = os.path.join(self.app, "Precios_alimentos.xlsx")
        self.origen = os.path.join(self.dir.name, "Precios_nuevos.xlsx")
        self._escribir(self.origen, b"NUEVA")
        self.log, self.orden, self.preguntas = [], [], []
        self.corriendo, self.permitir = True, True
        self.contenidos = {}         # {ruta: lo que `leer` devuelve para ese archivo}
        self.d = pd.Despliegue(dict(pd.AJUSTES_DEFECTO), entorno=mock.Mock(), log=self.log.append,
                               confirmar=self._confirmar, carpeta=self.app)
        self.d._del_script = lambda *a, **k: [{"pid": 1, "nombre": "python.exe"}] if self.corriendo else []
        self.d.reiniciar_lactia = self._reiniciar

    def _escribir(self, ruta, contenido: bytes):
        with open(ruta, "wb") as f:
            f.write(contenido)

    def _leer_archivo(self, ruta) -> bytes:
        with open(ruta, "rb") as f:
            return f.read()

    def _confirmar(self, mensaje):
        self.preguntas.append(mensaje)
        return self.permitir

    def _reiniciar(self):
        self.orden.append("reiniciar")
        return True

    def _leer(self, ruta):
        """`leer` de mentira: la planilla que corresponde al CONTENIDO del archivo (así se ve qué quedó puesto)."""
        return {b"NUEVA": NUEVA, b"VIEJA": VIEJA}.get(self._leer_archivo(ruta), {"error": "no es una planilla",
                                                                                 "precios": {}})

    def actualizar(self, **kw):
        return self.d.actualizar_precios(self.origen, self.destino, leer=self._leer, **kw)

    def poner_destino(self, contenido=b"VIEJA"):
        self._escribir(self.destino, contenido)

    def log_texto(self):
        return chr(10).join(self.log)


class TestActualizarPrecios(_Base):
    def test_copia_la_planilla_guarda_la_anterior_y_reinicia_lactia(self):
        self.poner_destino()
        self.assertTrue(self.actualizar())
        self.assertEqual(self._leer_archivo(self.destino), b"NUEVA")
        anteriores = os.listdir(os.path.join(self.app, "precios_anteriores"))
        self.assertEqual(len(anteriores), 1)
        self.assertEqual(self._leer_archivo(os.path.join(self.app, "precios_anteriores", anteriores[0])), b"VIEJA")
        self.assertEqual(self.orden, ["reiniciar"])

    def test_la_confirmacion_dice_que_se_agrega_y_que_lactia_se_reinicia(self):
        self.poner_destino()
        self.actualizar()
        pregunta = self.preguntas[0]
        self.assertIn("2 insumos", pregunta)          # los que se agregan
        self.assertIn("Harina de Soja", pregunta)
        self.assertIn("reinicia", pregunta)

    def test_si_lactia_estaba_parada_no_la_arranca(self):
        self.poner_destino()
        self.corriendo = False
        self.assertTrue(self.actualizar())
        self.assertEqual(self.orden, [])
        self.assertEqual(self._leer_archivo(self.destino), b"NUEVA")

    def test_solo_ver_no_cambia_nada_ni_pregunta(self):
        self.poner_destino()
        self.assertTrue(self.actualizar(solo_ver=True))
        self.assertEqual(self._leer_archivo(self.destino), b"VIEJA")
        self.assertEqual((self.orden, self.preguntas), ([], []))
        self.assertFalse(os.path.exists(os.path.join(self.app, "precios_anteriores")))
        self.assertIn("solo ver", self.log_texto().lower())

    def test_si_el_usuario_dice_que_no_no_se_toca_nada(self):
        self.poner_destino()
        self.permitir = False
        self.assertFalse(self.actualizar())
        self.assertEqual(self._leer_archivo(self.destino), b"VIEJA")
        self.assertEqual(self.orden, [])
        self.assertFalse(os.path.exists(os.path.join(self.app, "precios_anteriores")))

    def test_si_es_el_mismo_contenido_ya_esta_al_dia(self):
        self.poner_destino(b"NUEVA")
        self.assertTrue(self.actualizar())
        self.assertEqual((self.orden, self.preguntas), ([], []))
        self.assertIn("al día", self.log_texto())

    def test_sin_planilla_en_el_servidor_la_crea_sin_backup(self):
        self.assertTrue(self.actualizar())
        self.assertEqual(self._leer_archivo(self.destino), b"NUEVA")
        self.assertFalse(os.path.exists(os.path.join(self.app, "precios_anteriores")))

    def test_avisa_los_insumos_que_la_nueva_no_tiene(self):
        """Elegir por error una planilla más vieja que la del servidor: lo que se pierde tiene que verse antes de aceptar."""
        self.poner_destino(b"NUEVA")
        self._escribir(self.origen, b"VIEJA")
        self.actualizar()
        pregunta = self.preguntas[0]
        self.assertIn("NO tiene", pregunta)
        self.assertIn("Harina de Soja", pregunta)

    def test_si_lo_copiado_no_se_lee_bien_vuelve_a_la_anterior(self):
        self.poner_destino()

        def leer_que_falla_despues(ruta):
            if self._leer_archivo(ruta) == b"NUEVA" and ruta == self.destino:
                return {"error": "corrupta", "precios": {}}
            return self._leer(ruta)
        self.assertFalse(self.d.actualizar_precios(self.origen, self.destino, leer=leer_que_falla_despues))
        self.assertEqual(self._leer_archivo(self.destino), b"VIEJA")
        self.assertIn("ERROR", self.log_texto())
        self.assertEqual(self.orden, [])          # no se reinicia LactIA por un cambio que se deshizo

    def test_no_deja_archivos_temporales_en_la_carpeta(self):
        self.poner_destino()
        self.actualizar()
        self.assertEqual(sorted(os.listdir(self.app)), ["Precios_alimentos.xlsx", "precios_anteriores"])


class TestOrigenInvalido(_Base):
    def probar(self, mensaje):
        self.poner_destino()
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.actualizar()
        self.assertIn(mensaje, str(cm.exception))
        self.assertEqual(self._leer_archivo(self.destino), b"VIEJA")
        self.assertEqual((self.orden, self.preguntas), ([], []))

    def test_sin_origen(self):
        self.poner_destino()
        with self.assertRaises(pd.ErrorPanel):
            self.d.actualizar_precios("", self.destino, leer=self._leer)

    def test_origen_que_no_existe(self):
        os.remove(self.origen)
        self.probar("no existe")

    def test_origen_que_no_es_una_planilla_legible(self):
        self._escribir(self.origen, b"cualquier cosa")
        self.probar("no es una planilla")

    def test_origen_sin_ningun_precio(self):
        self._escribir(self.origen, b"VACIA")
        with mock.patch.object(self, "_leer", return_value={"error": None, "precios": {}}):
            self.probar("ningún precio")

    def test_origen_igual_al_destino(self):
        self.poner_destino()
        with self.assertRaises(pd.ErrorPanel) as cm:
            self.d.actualizar_precios(self.destino, self.destino, leer=self._leer)
        self.assertIn("misma", str(cm.exception))


class TestDestinoPrecios(_Base):
    def test_sin_ruta_configurada_es_la_planilla_de_la_carpeta_de_lactia(self):
        with mock.patch("configuracion_tambo.ruta_precios", return_value=None):
            self.assertEqual(self.d.destino_precios(), os.path.join(self.app, "Precios_alimentos.xlsx"))

    def test_con_ruta_configurada_es_esa(self):
        otra = os.path.join(self.dir.name, "otra", "Precios_alimentos.xlsx")
        with mock.patch("configuracion_tambo.ruta_precios", return_value=otra):
            self.assertEqual(self.d.destino_precios(), otra)


class TestAjustesPrecios(unittest.TestCase):
    def test_el_origen_se_recuerda_y_viene_vacio(self):
        self.assertEqual(pd.AJUSTES_DEFECTO["origen_precios"], "")
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "aj.json")
            a = pd.cargar_ajustes(ruta)
            a["origen_precios"] = r"G:\Mi unidad\Precios_alimentos.xlsx"
            pd.guardar_ajustes(a, ruta)
            self.assertEqual(pd.cargar_ajustes(ruta)["origen_precios"], r"G:\Mi unidad\Precios_alimentos.xlsx")


class TestPlanillaReal(unittest.TestCase):
    """Con un .xlsx de verdad (openpyxl) y la lectura real: lo que el panel copia es lo que LactIA va a leer."""

    def setUp(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("falta openpyxl")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.openpyxl = openpyxl

    def armar(self, nombre, filas):
        wb = self.openpyxl.Workbook()
        ws = wb.active
        ws.title = "Precios"
        ws.append(["Tipo de cambio", 1500])
        ws.append(["Precio de la leche", 0.35])
        ws.append([])
        ws.append(["Ingrediente", "USD/tonelada", "$/kg manual", "Estimado", "Notas"])
        for f in filas:
            ws.append(f)
        ruta = os.path.join(self.dir.name, nombre)
        wb.save(ruta)
        return ruta

    def test_de_punta_a_punta_con_la_lectura_real(self):
        origen = self.armar("nueva.xlsx", [["Silo Maiz 1 24-25", 75, None, None, None],
                                           ["Harina de Soja", 420, None, "SI", None]])
        app = os.path.join(self.dir.name, "app")
        os.mkdir(app)
        destino = self.armar("Precios_alimentos.xlsx", [["SILO DE MAIZ", 60, None, "SI", None]])
        os.replace(destino, os.path.join(app, "Precios_alimentos.xlsx"))
        destino = os.path.join(app, "Precios_alimentos.xlsx")
        d = pd.Despliegue(dict(pd.AJUSTES_DEFECTO), entorno=mock.Mock(), log=lambda m: None,
                          confirmar=lambda m: True, carpeta=app)
        d._del_script = lambda *a, **k: []
        self.assertTrue(d.actualizar_precios(origen, destino))
        import precios_alimentos
        leidos = precios_alimentos._leer_excel(destino)
        self.assertEqual(sorted(leidos["precios"]), ["harina de soja", "silo maiz 1 24-25"])
        self.assertAlmostEqual(leidos["precios"]["silo maiz 1 24-25"]["precio_kg"], 112.5)


class TestPantallaPrecios(unittest.TestCase):
    """Humo de la pestaña con tkinter de verdad (sin escritorio gráfico se saltea)."""

    def setUp(self):
        import uuid
        import winreg
        for nombre in ("difundir_cambio", "guardar_ajustes"):
            p = mock.patch.object(pd, nombre)
            m = p.start()
            self.addCleanup(p.stop)
            if nombre == "guardar_ajustes":
                self.guardar = m
        p = mock.patch.object(pd, "tambos_del_servidor", return_value=[])
        p.start()
        self.addCleanup(p.stop)

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
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"no se pudo abrir tkinter: {e}")
        self.addCleanup(self.panel.cerrar)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_hay_una_pestana_de_precios(self):
        textos = [self.panel.cuaderno.tab(i, "text").strip() for i in range(self.panel.cuaderno.index("end"))]
        self.assertIn("Precios", textos)

    def test_elegir_la_planilla_la_guarda_y_cancelar_no(self):
        ruta = os.path.join(self.dir.name, "p.xlsx")
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=ruta):
            self.panel._elegir_origen_precios()
        self.assertEqual(self.panel.aj["origen_precios"], os.path.normpath(ruta))
        self.assertEqual(self.guardar.call_count, 1)
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=""):
            self.panel._elegir_origen_precios()
        self.assertEqual(self.guardar.call_count, 1)


if __name__ == "__main__":
    unittest.main()
