"""Pruebas de `panel_despliegue` (variables de entorno, control de procesos, actualización).

Correr desde la carpeta del proyecto:
    python -m unittest tests.test_panel_despliegue -v

NADA de esto toca lo real, a propósito (esta PC comparte carpeta con otra sesión que tiene su
servidor en el 5310):
  * las variables se escriben en claves temporales de HKCU\\Software, no en `Environment`;
  * los procesos son un servidor de mentira (`dummy_servidor.py`) en un puerto libre; las
    funciones buscan procesos por NOMBRE de script, así que no pueden calzar con `servidor.py`;
  * `actualizar` se prueba sobre un repositorio git temporal: contra el real, `reset --hard`
    borraría trabajo ajeno.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
import winreg
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import panel_despliegue as pd  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ConRegistroTemporal(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(pd, "difundir_cambio")     # no mandar WM_SETTINGCHANGE a todo Windows
        p.start()
        self.addCleanup(p.stop)

    def clave(self) -> pd.Registro:
        sub = rf"Software\LactIA_PanelTest_{os.getpid()}_{uuid.uuid4().hex[:8]}"

        def limpiar():
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
        self.addCleanup(limpiar)
        return pd.Registro(winreg.HKEY_CURRENT_USER, sub, "Prueba")


class TestRegistro(_ConRegistroTemporal):
    def test_ida_y_vuelta_con_caracteres_dificiles(self):
        r = self.clave()
        for valor in ('contraseña con tildes ñ Á', '100%_no_se_expande', 'con "comillas" y \'simples\'',
                      "x" * 600, 'a&b|c^d<e>f'):
            r.escribir("MILKY_PASSWORD", valor)
            self.assertEqual(r.valor("MILKY_PASSWORD"), valor)

    def test_sobrescribir_conserva_el_nombre_y_no_duplica(self):
        r = self.clave()
        r.escribir("Milky_Usuario", "a")
        r.escribir("MILKY_USUARIO", "b")
        self.assertEqual(len(r.leer()), 1)
        self.assertEqual(list(r.leer())[0], "Milky_Usuario")
        self.assertEqual(r.valor("milky_usuario"), "b")

    def test_tipo_expandible_solo_si_ya_lo_era_y_el_valor_usa_porcentajes(self):
        r = self.clave()
        winreg.CreateKeyEx(r.raiz, r.subclave, 0, winreg.KEY_SET_VALUE).Close()
        with winreg.OpenKey(r.raiz, r.subclave, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "FFMPEG_PATH", 0, winreg.REG_EXPAND_SZ, r"%TEMP%\ffmpeg")
        r.escribir("FFMPEG_PATH", r"%TEMP%\otro")
        self.assertEqual(r.leer()["FFMPEG_PATH"][1], winreg.REG_EXPAND_SZ)
        r.escribir("FFMPEG_PATH", r"C:\ffmpeg")
        self.assertEqual(r.leer()["FFMPEG_PATH"][1], winreg.REG_SZ)
        r.escribir("NUEVA", "100%")                      # una variable nueva nunca es expandible
        self.assertEqual(r.leer()["NUEVA"][1], winreg.REG_SZ)

    def test_rechaza_nombres_y_valores_invalidos(self):
        r = self.clave()
        for nombre in ("", "1ABC", "CON ESPACIO", "A=B", "ñandú"):
            with self.assertRaises(pd.ErrorPanel, msg=nombre):
                r.escribir(nombre, "x")
        for valor in ("con\nsalto", "con\rretorno", "nulo\x00"):
            with self.assertRaises(pd.ErrorPanel, msg=repr(valor)):
                r.escribir("OK", valor)
        self.assertEqual(r.leer(), {})                  # no quedó nada a medias

    def test_borrar(self):
        r = self.clave()
        r.escribir("TELEGRAM_CHAT_ID", "1")
        self.assertTrue(r.borrar("telegram_chat_id"))
        self.assertFalse(r.borrar("TELEGRAM_CHAT_ID"))
        self.assertIsNone(r.valor("TELEGRAM_CHAT_ID"))


class TestEntorno(_ConRegistroTemporal):
    def setUp(self):
        super().setUp()
        self.u, self.e = self.clave(), self.clave()
        self.entorno = pd.Entorno(usuario=self.u, equipo=self.e)

    def fila(self, nombre, extras=()):
        return next(f for f in self.entorno.lectura(extras) if pd._norm(f["nombre"]) == pd._norm(nombre))

    def test_el_catalogo_aparece_aunque_no_este_definido(self):
        f = self.fila("TWILIO_AUTH_TOKEN")
        self.assertIsNone(f["efectivo"])
        self.assertTrue(f["var"].secreta)

    def test_prefijos_y_extras_pero_no_el_resto_del_sistema(self):
        self.e.escribir("DELPRO_PWD_LAMARTINA", "x")
        self.e.escribir("Path", r"C:\algo")
        self.e.escribir("MI_VARIABLE", "y")
        nombres = {pd._norm(f["nombre"]) for f in self.entorno.lectura()}
        self.assertIn("DELPRO_PWD_LAMARTINA", nombres)
        self.assertNotIn("PATH", nombres)
        self.assertNotIn("MI_VARIABLE", nombres)
        self.assertIn("MI_VARIABLE", {pd._norm(f["nombre"]) for f in self.entorno.lectura(["MI_VARIABLE"])})

    def test_usuario_gana_y_el_conflicto_se_marca(self):
        self.e.escribir("SMTP_HOST", "equipo.example")
        f = self.fila("SMTP_HOST")
        self.assertEqual((f["efectivo"], f["en_conflicto"]), ("equipo.example", False))
        self.u.escribir("SMTP_HOST", "usuario.example")
        f = self.fila("SMTP_HOST")
        self.assertEqual((f["efectivo"], f["en_conflicto"]), ("usuario.example", True))
        self.u.escribir("SMTP_HOST", "equipo.example")
        self.assertFalse(self.fila("SMTP_HOST")["en_conflicto"])

    def test_guardar_quitando_el_valor_del_otro_alcance(self):
        self.u.escribir("SMTP_PORT", "25")                       # el viejo, que pisaría al nuevo
        self.entorno.guardar("SMTP_PORT", "587", "equipo", quitar_del_otro=True)
        f = self.fila("SMTP_PORT")
        self.assertEqual((f["efectivo"], f["usuario"], f["equipo"]), ("587", None, "587"))

    def test_para_hijo_inyecta_solo_las_gestionadas_y_usuario_gana(self):
        self.e.escribir("SMTP_HOST", "equipo")
        self.u.escribir("SMTP_HOST", "usuario")
        self.e.escribir("Path", r"C:\NO_DEBE_REEMPLAZAR_EL_PATH")
        self.e.escribir("MI_VARIABLE", "extra")
        winreg.CreateKeyEx(self.u.raiz, self.u.subclave, 0, winreg.KEY_SET_VALUE).Close()
        with winreg.OpenKey(self.u.raiz, self.u.subclave, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "FFMPEG_PATH", 0, winreg.REG_EXPAND_SZ, r"%LACTIA_TEST_BASE%\ffmpeg")
        base = {"Path": r"C:\Windows", "LACTIA_TEST_BASE": r"D:\x"}
        env = self.entorno.para_hijo(extras=["MI_VARIABLE"], base=base)
        self.assertEqual(env["SMTP_HOST"], "usuario")
        self.assertEqual(env["PATH"], r"C:\Windows")
        self.assertEqual(env["MI_VARIABLE"], "extra")
        self.assertEqual(env["FFMPEG_PATH"], r"D:\x\ffmpeg")
        self.assertNotIn("MI_VARIABLE", self.entorno.para_hijo(base=base))

    def test_alcance_invalido(self):
        with self.assertRaises(pd.ErrorPanel):
            self.entorno.guardar("SMTP_HOST", "x", "otro")


class TestCatalogo(unittest.TestCase):
    PATRONES = [
        r'os\.environ(?:\.get)?\(\s*["\']([A-Z][A-Z0-9_]*)["\']',
        r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]*)["\']\s*\]',
        r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]*)["\']',
        r'_variable\(\s*["\']([A-Z][A-Z0-9_]*)["\']',
        r'\bVAR_[A-Z_]+\s*=\s*["\']([A-Z][A-Z0-9_]*)["\']',
    ]

    def test_toda_variable_que_lee_el_codigo_esta_en_el_catalogo(self):
        leidas = {}
        for carpeta, _dirs, archivos in os.walk(RAIZ):
            if any(p in carpeta for p in (os.sep + "tests", ".git", ".worktrees", "porlasdudas", "__pycache__",
                                          ".venv", "venv")):
                continue
            for nombre in archivos:
                if not nombre.endswith(".py") or nombre == "panel_despliegue.py":
                    continue
                with open(os.path.join(carpeta, nombre), encoding="utf-8", errors="replace") as f:
                    texto = f.read()
                for patron in self.PATRONES:
                    for var in re.findall(patron, texto):
                        leidas.setdefault(var, nombre)
        self.assertTrue(leidas, "el recorrido no encontró ninguna variable: la prueba está rota")
        faltan = {v: a for v, a in leidas.items() if not pd.en_catalogo(v)}
        self.assertEqual(faltan, {}, "variables que el código lee y el panel no conoce (agregarlas a CATALOGO)")

    def test_las_familias_por_tambo_estan(self):
        for nombre in ("DELPRO_PWD_PONDEROSA", "SENSEHUB_USER_X", "SENSEHUB_PWD_X", "PODAL_CAM_POSICION_X"):
            self.assertIsNotNone(pd.en_catalogo(nombre), nombre)
        self.assertIsNone(pd.en_catalogo("DELPRO_PWD_"))        # el prefijo solo no es una variable
        self.assertIsNone(pd.en_catalogo("PATH"))

    def test_los_secretos_estan_marcados(self):
        for nombre in ("ANTHROPIC_API_KEY", "TWILIO_AUTH_TOKEN", "SMTP_PASSWORD", "TELEGRAM_BOT_TOKEN",
                       "MILKY_PASSWORD", "LASER_PASSWORD", "HASTEN_PASSWORD", "TUNNEL_TOKEN", "DELPRO_PWD_X"):
            self.assertTrue(pd.en_catalogo(nombre).secreta, nombre)


class TestEsScript(unittest.TestCase):
    def test_calces(self):
        casos = [
            (r'"C:\Python\python.exe" servidor.py', "servidor.py", True),
            ('python servidor.py', "servidor.py", True),
            (r'python C:\ruta\servidor.py', "servidor.py", True),
            ('"python" "servidor.py"', "servidor.py", True),
            ('python dummy_servidor.py', "servidor.py", False),      # no es el mismo script
            ('python servidor.py.bak', "servidor.py", False),
            ('python otro.py servidor.py', "servidor.py", True),
            ('python panel_despliegue.py', "servidor.py", False),
            (None, "servidor.py", False),
        ]
        for cmd, script, esperado in casos:
            self.assertEqual(pd.es_script(cmd, script), esperado, cmd)


class TestAjustes(unittest.TestCase):
    def test_ida_y_vuelta_y_tolerancia_a_basura(self):
        with tempfile.TemporaryDirectory() as t:
            ruta = os.path.join(t, "aj.json")
            self.assertEqual(pd.cargar_ajustes(ruta), pd.AJUSTES_DEFECTO)
            aj = pd.cargar_ajustes(ruta)
            aj["puerto"] = 8080
            aj["secreto_que_no_deberia_guardarse"] = "x"
            pd.guardar_ajustes(aj, ruta)
            self.assertEqual(pd.cargar_ajustes(ruta)["puerto"], 8080)
            with open(ruta, encoding="utf-8") as f:
                self.assertNotIn("secreto_que_no_deberia_guardarse", f.read())
            with open(ruta, "w", encoding="utf-8") as f:
                f.write('{"puerto": "no es un numero", "rama": 5}')
            self.assertEqual(pd.cargar_ajustes(ruta)["puerto"], 5310)
            self.assertEqual(pd.cargar_ajustes(ruta)["rama"], "master")


DUMMY = '''import http.server, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
class S(http.server.HTTPServer):
    allow_reuse_address = False
print("dummy arrancando", flush=True)
S(("127.0.0.1", int(os.environ["DUMMY_PUERTO"])), H).serve_forever()
'''


class TestProcesos(_ConRegistroTemporal):
    """Un servidor de mentira controlado con las mismas funciones que usa el panel."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="panel_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with open(os.path.join(self.tmp, "dummy_servidor.py"), "w", encoding="utf-8") as f:
            f.write(DUMMY)
        self.puerto = _puerto_libre()
        p = mock.patch.dict(os.environ, {"DUMMY_PUERTO": str(self.puerto)})
        p.start()
        self.addCleanup(p.stop)
        self.lineas = []
        aj = dict(pd.AJUSTES_DEFECTO, script_lactia="dummy_servidor.py", puerto=self.puerto,
                  script_iot="dummy_iot.py")
        self.d = pd.Despliegue(aj, pd.Entorno(usuario=self.clave(), equipo=self.clave()),
                               log=self.lineas.append, carpeta=self.tmp)
        self.addCleanup(lambda: self.d._del_script("dummy_servidor.py") and
                        self.d._matar(self.d._del_script("dummy_servidor.py"), "limpieza"))

    def test_iniciar_dos_veces_no_duplica_y_parar_libera_el_puerto(self):
        real_antes = pd.puerto_abierto(5310)                     # el servidor de la otra sesión, si está
        self.assertTrue(self.d.iniciar_lactia(), "\n".join(self.lineas))
        self.assertTrue(pd.puerto_abierto(self.puerto))
        antes = sorted(p["pid"] for p in self.d._del_script("dummy_servidor.py"))
        self.assertTrue(antes)
        self.assertTrue(self.d.iniciar_lactia())
        self.assertIn("ya está corriendo", "\n".join(self.lineas))
        self.assertEqual(sorted(p["pid"] for p in self.d._del_script("dummy_servidor.py")), antes)

        est = self.d.estado()["lactia"]
        self.assertEqual((bool(est["procesos"]), est["puerto"], est["http"]), (True, True, 200))

        self.assertTrue(self.d.parar_lactia(), "\n".join(self.lineas))
        self.assertFalse(pd.puerto_abierto(self.puerto))
        self.assertEqual(self.d._del_script("dummy_servidor.py"), [])
        self.assertTrue(self.d.parar_lactia())                    # parar algo parado no es un error
        self.assertEqual(pd.puerto_abierto(5310), real_antes, "el panel tocó el servidor real")

    def test_reiniciar_cambia_el_proceso(self):
        self.assertTrue(self.d.iniciar_lactia())
        antes = {p["pid"] for p in self.d._del_script("dummy_servidor.py")}
        self.assertTrue(self.d.reiniciar_lactia(), "\n".join(self.lineas))
        despues = {p["pid"] for p in self.d._del_script("dummy_servidor.py")}
        self.assertTrue(despues and not (antes & despues))

    def test_el_log_queda_en_un_archivo(self):
        self.d.iniciar_lactia()
        self.assertIn("dummy arrancando", pd.cola_de_log(os.path.join(self.tmp, "logs_panel", "lactia.log")))

    def test_script_inexistente_da_un_error_claro(self):
        self.d.aj["script_lactia"] = "no_existe.py"
        with self.assertRaises(pd.ErrorPanel):
            self.d.iniciar_lactia()

    def test_si_el_proceso_muere_enseguida_se_informa_y_se_muestra_el_log(self):
        with open(os.path.join(self.tmp, "dummy_servidor.py"), "w", encoding="utf-8") as f:
            f.write('import sys\nprint("falla de arranque simulada")\nsys.exit(3)\n')
        self.assertFalse(self.d.iniciar_lactia())
        texto = "\n".join(self.lineas)
        self.assertIn("se cerró enseguida", texto)
        self.assertIn("falla de arranque simulada", texto)

    def test_las_variables_frescas_llegan_al_proceso_hijo(self):
        self.d.entorno.equipo.escribir("LACTIA_URL", "http://valor-fresco")
        env = self.d._entorno_hijo()
        self.assertEqual(env["LACTIA_URL"], "http://valor-fresco")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["DELPRO_PORT"], str(self.puerto))


class TestActualizar(_ConRegistroTemporal):
    """El flujo más riesgoso (`reset --hard`), contra un repositorio de verdad pero temporal."""

    def git(self, cwd, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                           capture_output=True, text=True, creationflags=pd.CREATE_NO_WINDOW)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def setUp(self):
        super().setUp()
        if not shutil.which("git"):
            self.skipTest("no hay git")
        self.tmp = tempfile.mkdtemp(prefix="panel_git_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        origen, trabajo, self.srv = (os.path.join(self.tmp, n) for n in ("origen.git", "trabajo", "servidor"))
        self.git(self.tmp, "init", "--bare", "-b", "master", origen)
        self.git(self.tmp, "clone", origen, trabajo)
        for archivo, texto in (("dummy_servidor.py", DUMMY), ("requirements.txt", "# vacío\n"), ("dato.txt", "v1\n")):
            with open(os.path.join(trabajo, archivo), "w", encoding="utf-8") as f:
                f.write(texto)
        self.git(trabajo, "add", ".")
        self.git(trabajo, "commit", "-m", "uno")
        self.git(trabajo, "push", "origin", "master")
        self.git(self.tmp, "clone", origen, self.srv)
        self.trabajo = trabajo
        with open(os.path.join(trabajo, "dato.txt"), "w", encoding="utf-8") as f:
            f.write("v2\n")
        self.git(trabajo, "commit", "-am", "dos")
        self.git(trabajo, "push", "origin", "master")

        self.puerto = _puerto_libre()
        p = mock.patch.dict(os.environ, {"DUMMY_PUERTO": str(self.puerto)})
        p.start()
        self.addCleanup(p.stop)
        self.lineas, self.preguntas = [], []
        aj = dict(pd.AJUSTES_DEFECTO, script_lactia="dummy_servidor.py", puerto=self.puerto, script_iot="dummy_iot.py")
        self.respuesta = True
        self.d = pd.Despliegue(aj, pd.Entorno(usuario=self.clave(), equipo=self.clave()), log=self.lineas.append,
                               confirmar=lambda m: (self.preguntas.append(m), self.respuesta)[1], carpeta=self.srv)
        self.addCleanup(lambda: self.d._del_script("dummy_servidor.py") and
                        self.d._matar(self.d._del_script("dummy_servidor.py"), "limpieza"))

    def leer(self):
        with open(os.path.join(self.srv, "dato.txt"), encoding="utf-8") as f:
            return f.read().strip()

    def test_actualiza_y_reinicia_el_servidor(self):
        self.assertEqual(self.leer(), "v1")
        self.assertTrue(self.d.iniciar_lactia())
        antes = {p["pid"] for p in self.d._del_script("dummy_servidor.py")}
        self.assertTrue(self.d.actualizar(), "\n".join(self.lineas))
        self.assertEqual(self.leer(), "v2")
        despues = {p["pid"] for p in self.d._del_script("dummy_servidor.py")}
        self.assertTrue(despues and not (antes & despues), "no reinició el servidor")
        self.assertTrue(pd.puerto_abierto(self.puerto))
        self.assertEqual(self.preguntas, [], "no había cambios locales: no debió preguntar nada")

    def test_con_cambios_locales_pregunta_y_si_se_dice_que_no_no_toca_nada(self):
        with open(os.path.join(self.srv, "dato.txt"), "w", encoding="utf-8") as f:
            f.write("cambio local\n")
        self.respuesta = False
        self.assertFalse(self.d.actualizar())
        self.assertEqual(len(self.preguntas), 1)
        self.assertEqual(self.leer(), "cambio local")             # no se perdió

    def test_con_cambios_locales_y_se_acepta_se_descartan(self):
        with open(os.path.join(self.srv, "dato.txt"), "w", encoding="utf-8") as f:
            f.write("cambio local\n")
        self.assertTrue(self.d.actualizar(), "\n".join(self.lineas))
        self.assertEqual(self.leer(), "v2")

    def test_lo_que_no_estaba_corriendo_no_se_arranca_solo_salvo_lactia(self):
        self.assertTrue(self.d.actualizar(), "\n".join(self.lineas))
        self.assertEqual(self.d._del_script("dummy_iot.py"), [])   # el IoT no estaba corriendo: sigue parado


class TestPantalla(_ConRegistroTemporal):
    """Humo de la pantalla: se arma, muestra, enmascara los secretos y se destruye sin romper."""

    def setUp(self):
        super().setUp()
        try:
            self.panel = pd.Panel(ajustes=dict(pd.AJUSTES_DEFECTO, script_lactia="dummy_servidor.py",
                                               script_iot="dummy_iot.py"),
                                  entorno=pd.Entorno(usuario=self.clave(), equipo=self.clave()), visible=False)
        except Exception as e:  # noqa: BLE001  (sin escritorio gráfico, por ejemplo)
            self.skipTest(f"no se pudo abrir tkinter: {e}")
        self.addCleanup(self.panel.cerrar)

    def filas(self):
        t = self.panel.tabla
        return {t.item(i, "values")[0]: t.item(i, "values") for i in t.get_children()}

    def test_enmascara_secretos_y_los_muestra_con_la_casilla(self):
        self.panel.entorno.equipo.escribir("TWILIO_AUTH_TOKEN", "super-secreto-123")
        self.panel.entorno.equipo.escribir("SMTP_HOST", "smtp.example.com")
        self.panel._refrescar_variables()
        filas = self.filas()
        self.assertEqual(filas["TWILIO_AUTH_TOKEN"][1], "•" * 8)
        self.assertEqual(filas["SMTP_HOST"][1], "smtp.example.com")
        self.assertNotIn("super-secreto-123", " ".join(" ".join(map(str, v)) for v in filas.values()))
        self.panel.var_mostrar.set(True)
        self.panel._alternar_secretos()
        self.assertEqual(self.filas()["TWILIO_AUTH_TOKEN"][1], "super-secreto-123")

    def test_aparece_el_aviso_de_reinicio_al_cambiar_una_variable(self):
        self.assertFalse(self.panel.marco_reinicio.winfo_ismapped())
        self.panel._avisar_cambio("x")
        self.panel.raiz.update()
        self.assertTrue(self.panel.cambio_pendiente)

    def test_el_log_recibe_lineas_de_otros_hilos(self):
        self.panel._log("línea de prueba")
        self.panel.raiz.update()
        import time
        for _ in range(20):
            self.panel.raiz.update()
            if "línea de prueba" in self.panel.txt.get("1.0", "end"):
                break
            time.sleep(0.05)
        self.assertIn("línea de prueba", self.panel.txt.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
