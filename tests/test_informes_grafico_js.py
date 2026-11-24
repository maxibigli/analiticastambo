"""La matemática y el dibujo del gráfico de informes viven en `static/js/informes_grafico.js`, un módulo puro (sin pantalla), justamente para
poder probarlos de verdad: armar las series por día, semana o mes, ignorar los días a medio cargar, las estadísticas, la escala del eje y
el SVG. Las pruebas están en `tests/js/informes_grafico.test.js` y se corren con Node; si esta PC no tiene Node, se saltan.

    C:\\Python314\\python.exe -m unittest tests.test_informes_grafico_js -v
"""
import os
import shutil
import subprocess
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODES = (shutil.which("node"), r"C:\Program Files\nodejs\node.exe", "/usr/bin/node", "/usr/local/bin/node")


def _node():
    for n in NODES:
        if n and os.path.exists(n):
            return n
    return None


class TestGraficoJs(unittest.TestCase):
    def test_el_modulo_del_grafico(self):
        node = _node()
        if node is None:
            self.skipTest("Node no está instalado en esta PC")
        r = subprocess.run([node, os.path.join("tests", "js", "informes_grafico.test.js")], cwd=RAIZ, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        self.assertIn("pruebas del gráfico", r.stdout)

    def test_la_pantalla_carga_el_modulo(self):
        """La plantilla tiene que pedir el módulo antes de su propio script: si no, la pantalla se rompe sin avisar."""
        with open(os.path.join(RAIZ, "templates", "informes.html"), encoding="utf-8") as f:
            html = f.read()
        carga = 'src="/static/js/informes_grafico.js"'
        self.assertTrue(carga in html, "la plantilla no pide /static/js/informes_grafico.js")
        self.assertLess(html.index(carga), html.index("(function(){"))
        self.assertTrue(os.path.exists(os.path.join(RAIZ, "static", "js", "informes_grafico.js")))


if __name__ == "__main__":
    unittest.main()
