"""Las tarjetas del Tablero que llevan a una sección puntual de otra pantalla.

El clic llama a `saltarDesdeTablero(pagina, tab, ancla)` y baja hasta `document.getElementById(ancla)`;
si el id no existe en la página no pasa nada y nadie se entera. Estas pruebas lo vigilan.

    C:\\Python314\\python.exe -m unittest tests.test_tablero_anclas -v
"""
import os
import re
import unittest

import tablero

HTML = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "index.html"),
            encoding="utf-8").read()


class TestAnclasDelTablero(unittest.TestCase):
    def test_litros_hora_lleva_al_grafico_de_litros_por_hora_de_rendimiento_sala(self):
        ind = tablero.POR_CLAVE["litros_hora"]
        self.assertEqual(ind["pagina"], "rendimiento")
        self.assertEqual(ind["ancla"], "rend-throughput")
        self.assertIn("litros por hora", ind["destino"].lower())

    def test_el_ancla_apunta_al_contenedor_del_grafico_de_throughput(self):
        m = re.search(r'<div[^>]*id="rend-throughput"[^>]*>(.*?)</div>\s*</div>', HTML, flags=re.S)
        self.assertIsNotNone(m, "falta el contenedor con id=rend-throughput")
        self.assertIn('id="chart-rend-throughput"', m.group(1))

    def test_todo_ancla_del_catalogo_existe_en_la_pagina(self):
        for ind in tablero.INDICADORES:
            ancla = ind.get("ancla")
            if ancla:
                self.assertRegex(HTML, r'id="%s"' % re.escape(ancla),
                                 f"«{ind['nombre']}» salta a #{ancla}, que no existe en index.html")

    def test_los_ids_de_ancla_no_se_repiten_en_la_pagina(self):
        # un id repetido hace que el salto vaya a la primera aparición, que puede ser otra sección
        for ind in tablero.INDICADORES:
            ancla = ind.get("ancla")
            if ancla:
                self.assertEqual(len(re.findall(r'id="%s"' % re.escape(ancla), HTML)), 1,
                                 f"el id {ancla} aparece más de una vez")


if __name__ == "__main__":
    unittest.main()
