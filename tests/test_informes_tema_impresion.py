"""El tema claro y la impresión de la pantalla de informes (`templates/informes.html`), 24/11/2026.

Hasta acá la pantalla era SOLO oscura y no tenía reglas de impresión. Estas pruebas cuidan tres cosas:
  * que todo color de una regla salga de una variable (si no, un color escrito a mano se ve bien en un tema y ilegible en el otro);
  * que el tema claro redefina todos los colores propios del oscuro y no deje ninguna variable sin definir;
  * que la hoja impresa tenga lo suyo (apaisada, sin controles, con encabezado, el título de la tabla en cada página).
Lo que se ve en pantalla se mira en el navegador; el comportamiento (el botón, `beforeprint`/`afterprint`, el encabezado) se ejecuta en Node: `tests/js/informes_tema_impresion.test.js`.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tema_impresion -v
"""
import json
import os
import re
import subprocess
import unittest
from unittest import mock

import app
from tests.test_informes_pagina import NODE, cliente, sin_manejador

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COLOR = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(")


def fuente() -> str:
    with open(os.path.join(RAIZ, "templates", "informes.html"), encoding="utf-8") as f:
        return f.read()


def estilo() -> str:
    f = fuente()
    return f[f.index("<style>") + 7:f.index("</style>")]


def bloque(texto: str, abre: str) -> str:
    """El contenido de la regla que empieza en `abre` (hasta su `}`)."""
    i = texto.index(abre) + len(abre)
    return texto[i:texto.index("}", i)]


def variables(texto: str) -> dict:
    return {n: v.strip() for n, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", texto)}


def sin_espacios(texto: str) -> str:
    return re.sub(r"\s+", "", texto)


class TestTemaClaro(unittest.TestCase):
    def test_los_colores_de_las_reglas_salen_de_variables(self):
        """Fuera de los dos bloques de variables no puede quedar un color escrito a mano (`#16232E`, `rgba(...)`): en el otro tema se vería mal."""
        resto = re.sub(r":root(?:\[data-theme=\"light\"\])?\s*\{[^}]*\}", "", estilo())
        self.assertEqual(COLOR.findall(resto), [])

    def test_el_claro_redefine_todo_color_propio_del_oscuro(self):
        e = estilo()
        oscuro, claro = variables(bloque(e, ":root{")), variables(bloque(e, ':root[data-theme="light"]{'))
        propios = {n for n, v in oscuro.items() if COLOR.search(v)}
        self.assertEqual(propios - set(claro), set(), "colores del tema oscuro que el claro no redefine")
        self.assertEqual(set(claro) - set(oscuro), set(), "el claro define una variable que el oscuro no tiene")
        self.assertGreater(len(propios), 20)

    def test_cada_variable_que_se_usa_esta_definida(self):
        f = fuente()
        with open(os.path.join(RAIZ, "static", "css", "lactia-tokens.css"), encoding="utf-8") as t:
            tokens = t.read()
        with open(os.path.join(RAIZ, "static", "js", "informes_grafico.js"), encoding="utf-8") as g:
            grafico = g.read()
        usadas = set(re.findall(r"var\((--[\w-]+)", f + grafico)) - {"--s"}          # `--s` es el prefijo con el que el JavaScript arma --s1 ... --s8
        definidas = set(re.findall(r"(--[\w-]+)\s*:", f + tokens))
        self.assertEqual(usadas - definidas, set())

    def test_el_tema_oscuro_sigue_siendo_el_de_salida_y_el_claro_declara_su_esquema(self):
        f = fuente()
        self.assertIn('<html lang="es" data-theme="dark">', f)
        self.assertIn("color-scheme:dark;", bloque(estilo(), ":root{"))
        self.assertIn("color-scheme:light;", bloque(estilo(), ':root[data-theme="light"]{'))

    def test_el_tema_elegido_se_aplica_antes_de_pintar(self):
        f = fuente()
        i = f.index('localStorage.getItem("lactia.informes.tema")')
        self.assertLess(i, f.index("lactia-tokens.css"))
        self.assertLess(i, f.index("<style>"))
        self.assertIn("try{", f[i - 20:i])                                             # sin almacenamiento (navegación privada) no se rompe
        self.assertIn("const TEMA_CLAVE = 'lactia.informes.tema';", f)

    def test_el_boton_del_tema_no_esta_en_el_encabezado_que_el_menu_oculta(self):
        """Embebida en el menú la pantalla esconde su encabezado (`.emb .top`): el botón vive en la barra de pestañas, que siempre se ve."""
        f = fuente()
        self.assertIn('id="btn-tema"', f)
        self.assertNotIn("btn-tema", f[f.index('<header class="top">'):f.index("</header>")])
        self.assertLess(f.index('id="doctabs"'), f.index('id="btn-tema"'))

    def test_cambiar_de_tema_no_se_guarda_con_el_informe(self):
        """El tema es de quien mira, no del informe: no entra en `foto` (lo que decide si hay cambios sin guardar)."""
        f = fuente()
        self.assertNotIn("tema", f[f.index("const foto ="):f.index("function nuevoDoc")].lower())


class TestImpresion(unittest.TestCase):
    def imprime(self) -> str:
        e = sin_espacios(estilo())
        return e[e.index("@mediaprint{"):]

    def test_la_hoja_es_apaisada_y_numera_las_paginas(self):
        e = sin_espacios(estilo())
        self.assertIn("@page{size:A4landscape;", e)
        self.assertIn("counter(page)", e)

    def test_la_impresion_saca_los_controles_y_muestra_el_encabezado(self):
        imp = self.imprime()
        regla = imp[imp.index(".top,"):imp.index("{display:none!important}")]
        for pieza in (".tabsrow", ".lhead", ".ask", ".head", "#importado", ".filters", ".groupbar", ".cols", ".fields", "#aviso", "#gctl", ".toast", "dialog"):
            self.assertIn(pieza, regla, pieza)
        self.assertIn(".strip.seg", regla)                                                 # cuánto tardó el cálculo no sirve en papel
        self.assertIn(".solo-impresion{display:block", imp)
        self.assertIn(".solo-impresion{display:none}", sin_espacios(estilo()))              # en pantalla el encabezado de la hoja no se ve

    def test_el_titulo_de_la_tabla_se_repite_en_cada_pagina_y_el_total_sale_una_vez(self):
        imp = self.imprime()
        self.assertIn("thead{display:table-header-group}", imp)
        self.assertIn("tfoot{display:table-row-group}", imp)
        self.assertIn("tr{break-inside:avoid}", imp)
        self.assertIn(sin_espacios("thead th{position:static"), imp)
        self.assertIn(sin_espacios("tfoot td{position:static}"), imp)

    def test_el_boton_de_ordenar_del_titulo_se_imprime_como_texto(self):
        """Medido imprimiendo a PDF con Edge: un `<button>` dentro del encabezado que se repite en cada página sale SIN texto (solo la franja gris) a partir de la página 2.
        Con `display:contents` el texto es del encabezado y se repite bien."""
        self.assertIn(".sortb{display:contents}", self.imprime())

    def test_la_tabla_sale_entera_y_no_con_el_scroll_de_la_pantalla(self):
        self.assertIn(".tablewrap{max-height:none;overflow:visible;", self.imprime())

    def test_el_grafico_se_adapta_al_ancho_de_la_hoja_y_sale_sin_animar(self):
        imp = self.imprime()
        self.assertIn(sin_espacios("#chart svg{width:100%;height:auto}"), imp)
        self.assertIn(".draw,.fade{animation:none!important;", imp)

    def test_las_sombras_y_los_colores_de_fondo_se_piden_a_la_impresora(self):
        """Sin `print-color-adjust`, el encabezado gris de la tabla y los puntos de la leyenda salen en blanco con la configuración normal del navegador."""
        self.assertIn("print-color-adjust:exact", self.imprime())

    def test_cada_boton_de_imprimir_tiene_su_manejador_y_estan_el_informe_y_el_grafico(self):
        f = fuente()
        self.assertEqual(sin_manejador(f), [])
        self.assertIn("else if (a === 'imprimir') imprimir();", f)
        self.assertGreaterEqual(f.count('data-act="imprimir"'), 2)                       # el del informe y el del gráfico (se arma con la barra de su contexto)
        self.assertIn('id="btn-print" data-act="imprimir"', f)

    def test_el_encabezado_de_la_hoja_pasa_todo_por_esc(self):
        """Nombre, condiciones y avisos vienen del usuario o de DelPro: van como texto, nunca como HTML."""
        f = fuente()
        cuerpo = f[f.index("function encabezadoImpresion"):f.index("function prepararImpresion")]
        for pieza in ("esc(d.nombre || 'Sin nombre')", "esc(TAMBO_NOMBRE || TAMBO)", "esc(fmtFechaHora(cuando))", "esc(t)"):
            self.assertIn(pieza, cuerpo, pieza)
        self.assertNotIn("+ d.nombre +", cuerpo)

    def test_la_pagina_trae_el_nombre_del_tambo_para_la_hoja(self):
        with mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"):
            html = cliente("admin").get("/informes/").get_data(as_text=True)
        self.assertIn('const TAMBO_NOMBRE = "La Ponderosa";', html)
        self.assertIn('const TAMBO = "ponderosa";', html)

    def test_imprimir_no_toca_lo_que_el_usuario_dejo_guardado(self):
        """`beforeprint` y `afterprint` cambian el tema sin recordarlo (`aplicarTema(..., false)`): la preferencia guardada es la del botón."""
        f = fuente()
        cuerpo = f[f.index("let temaAntesDeImprimir"):f.index("document.addEventListener('click'")]
        self.assertIn("aplicarTema('light', false)", cuerpo)
        self.assertIn("aplicarTema(temaAntesDeImprimir, false)", cuerpo)
        self.assertNotIn("true)", cuerpo)


class TestEjecutada(unittest.TestCase):
    """El JavaScript REAL de la pantalla en Node (`tests/js/informes_tema_impresion.test.js`): el botón del tema, lo que recuerda, lo que arma `beforeprint` y lo que vuelve `afterprint`."""
    resultados = {}

    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("no hay Node en esta PC")
        r = subprocess.run([NODE, os.path.join("tests", "js", "informes_tema_impresion.test.js")], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", timeout=180)
        lineas = [l for l in (r.stdout or "").splitlines() if l.startswith("RESULTADOS ")]
        if not lineas:
            raise AssertionError("Node no llegó a dar resultados:\n" + (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
        cls.resultados = {x["nombre"]: x for x in json.loads(lineas[0][len("RESULTADOS "):])}

    def test_todos_los_casos_pasan(self):
        self.assertGreaterEqual(len(self.resultados), 15)
        fallan = ["%s\n%s" % (n, r.get("error", "")) for n, r in self.resultados.items() if not r["ok"]]
        self.assertEqual(fallan, [])

    def test_el_tema_se_recuerda_y_un_valor_raro_se_ignora(self):
        for n in ("tema: el botón alterna claro y oscuro, lo recuerda y el texto cambia", "tema: lo que quedó guardado se aplica al abrir",
                  "tema: un valor guardado que no es un tema se ignora", "tema: sin almacenamiento (navegación privada) el botón igual cambia el tema, sin errores"):
            self.assertTrue(self.resultados[n]["ok"], n)

    def test_se_imprime_siempre_en_claro_y_se_devuelve_el_tema(self):
        for n in ("imprimir: se imprime siempre en claro y después vuelve al tema de la pantalla", "imprimir: Ctrl+P (sin el botón) también imprime en claro y con el encabezado",
                  "imprimir: un afterprint que llega dos veces no cambia el tema que el usuario eligió"):
            self.assertTrue(self.resultados[n]["ok"], n)


if __name__ == "__main__":
    unittest.main()
