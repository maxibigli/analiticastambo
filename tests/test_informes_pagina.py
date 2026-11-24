"""La pantalla de informes (`/informes/`) y su entrada en el menú principal.

    C:\\Python314\\python.exe -m unittest tests.test_informes_pagina -v
"""
import re
import unittest
from unittest import mock

import app
import auth


def cliente(rol=None):
    c = app.app.test_client()
    if rol:
        with c.session_transaction() as s:
            s["rol"], s["usuario"] = rol, "x"
    return c


class TestPagina(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa")
        p.start()
        self.addCleanup(p.stop)

    def test_exige_ser_administrador(self):
        self.assertIn(cliente().get("/informes/").status_code, (302, 401))
        self.assertEqual(cliente("operario").get("/informes/").status_code, 403)

    def test_el_administrador_la_ve_y_trae_el_tambo(self):
        r = cliente("admin").get("/informes/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        html = r.get_data(as_text=True)
        self.assertIn("/api/informes/campos", html)
        self.assertIn('"ponderosa"', html)

    def test_embebida_en_el_menu_no_repite_el_encabezado(self):
        normal = cliente("admin").get("/informes/").get_data(as_text=True)
        emb = cliente("admin").get("/informes/?embebido=1").get_data(as_text=True)
        self.assertIn("const EMBEBIDO = true", emb)
        self.assertIn("const EMBEBIDO = false", normal)

    def test_ninguna_marca_de_jinja_se_cuela_en_el_codigo(self):
        """`{{` o `{%` dentro del JavaScript de una plantilla la rompe con un 500 (ver CLAUDE.md)."""
        with open("templates/informes.html", encoding="utf-8") as f:
            fuente = f.read()
        import re
        marcas = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", fuente)
        self.assertEqual(sorted(set(marcas)), sorted({"{{ tambo|tojson }}", "{{ embebido|tojson }}"}))


def sin_manejador(fuente: str) -> list:
    """Los `data-act` y `data-chg` que la pantalla genera y ningún manejador atiende (`a === '...'`)."""
    pedidas = set(re.findall(r'data-(?:act|chg)="([A-Za-z0-9]+)"', fuente))
    manejadas = set(re.findall(r"a === '([A-Za-z0-9]+)'", fuente))
    return sorted(pedidas - manejadas)


class TestFase2EnLaPantalla(unittest.TestCase):
    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_cada_boton_y_cada_selector_tiene_su_manejador(self):
        """Un `data-act` sin manejador es un botón que no hace nada y no da error en ningún lado."""
        self.assertEqual(sin_manejador(self.fuente()), [])
        # Los dos niveles de agrupación se arman con data-chg="g' + n + '": no los ve la búsqueda de arriba.
        self.assertTrue({"g1", "g2"} <= set(re.findall(r"a === '([A-Za-z0-9]+)'", self.fuente())))

    def test_el_control_detecta_un_manejador_que_falta(self):
        falso = "<button data-act=\"nuevo\"></button><select data-chg=\"rs\"></select> if (a === 'rs') {}"
        self.assertEqual(sin_manejador(falso), ["nuevo"])

    def test_trae_la_barra_de_agrupacion_y_el_cuadro_del_campo_calculado(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="groupbar"', '<dialog id="calc"', 'id="c-formula"', "/api/informes/formula",
                         'data-act="plegar"', 'data-act="nuevocalc"', 'data-chg="rs"', 'data-chg="solo"'):
            self.assertIn(esperado, html, esperado)

    def test_los_textos_que_escribe_el_usuario_pasan_por_esc(self):
        """El nombre de un campo calculado y la clave de un grupo se escriben a mano o vienen de la base: nunca van crudos al HTML."""
        fuente = self.fuente()
        for pieza in ("esc(f.etiqueta)", "esc(et.length", "esc(meta.etiqueta)", "esc(textoClave(", "esc(g.etiqueta)", "esc(c.etiqueta)"):
            self.assertIn(pieza, fuente, pieza)
        self.assertNotIn(".innerHTML = e.message", fuente)          # los mensajes de error van con textContent
        self.assertIn("e.textContent = texto", fuente)


class TestMenu(unittest.TestCase):
    def test_los_permisos(self):
        admin = auth.PAGINAS_POR_ROL["admin"]
        self.assertIn("informes", admin)
        self.assertEqual(admin.index("informes"), admin.index("consultas") + 1)    # justo después de «Consultas e IA»
        self.assertNotIn("informes", auth.PAGINAS_POR_ROL["operario"])

    def test_la_entrada_del_menu_solo_para_el_administrador(self):
        with mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"):
            admin = cliente("admin").get("/").get_data(as_text=True)
            operario = cliente("operario").get("/").get_data(as_text=True)
        self.assertIn('data-pagina="informes"', admin)
        self.assertIn('id="page-informes"', admin)
        self.assertNotIn('data-pagina="informes"', operario)


if __name__ == "__main__":
    unittest.main()
