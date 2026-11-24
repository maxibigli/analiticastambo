"""Exportar un informe a Excel (informes_excel.a_xlsx): lo que se ve en pantalla, con tipos reales (números y fechas, no
texto), una hoja con las condiciones, y sin que un valor que empiece con «=» se convierta en una fórmula.

    C:\\Python314\\python.exe -m unittest tests.test_informes_excel -v
"""
import datetime
import io
import unittest

import openpyxl

import informes_excel as ie

COLS = [
    {"id": "Number", "etiqueta": "Número del animal", "tipo": "txt", "unidad": "", "decimales": 0},
    {"id": "P24", "etiqueta": "Producción 24 h", "tipo": "num", "unidad": "kg", "decimales": 1},
    {"id": "Dias", "etiqueta": "Días en ordeño", "tipo": "num", "unidad": "", "decimales": 0},
    {"id": "Parto", "etiqueta": "Fecha del último parto", "tipo": "fecha", "unidad": "", "decimales": 0},
]
FILAS = [["3250", 25.123, 881, "2024-06-25T00:00:00"], ["=1+1", None, 12, None]]


def libro(resultado=None, **kw):
    r = resultado or {"columnas": COLS, "filas": FILAS, "truncado": False}
    return openpyxl.load_workbook(io.BytesIO(ie.a_xlsx(r, "Prod. x lactancia", **kw)))


class TestXlsx(unittest.TestCase):
    def test_encabezados_con_la_unidad(self):
        ws = libro()["Informe"]
        self.assertEqual([c.value for c in ws[1]], ["Número del animal", "Producción 24 h (kg)", "Días en ordeño", "Fecha del último parto"])

    def test_los_numeros_son_numeros_y_las_fechas_son_fechas(self):
        ws = libro()["Informe"]
        self.assertIsInstance(ws["B2"].value, float)
        self.assertAlmostEqual(ws["B2"].value, 25.123)
        self.assertEqual(ws["B2"].number_format, "#,##0.0")
        self.assertEqual(ws["C2"].number_format, "#,##0")
        self.assertEqual(ws["D2"].value, datetime.datetime(2024, 6, 25))
        self.assertEqual(ws["D2"].number_format, "dd/mm/yyyy")

    def test_un_texto_que_empieza_con_igual_no_es_una_formula(self):
        c = libro()["Informe"]["A3"]
        self.assertEqual(c.value, "=1+1")
        self.assertEqual(c.data_type, "s")

    def test_los_vacios_quedan_vacios(self):
        ws = libro()["Informe"]
        self.assertIsNone(ws["B3"].value)
        self.assertIsNone(ws["D3"].value)

    def test_encabezado_fijo_y_filtro(self):
        ws = libro()["Informe"]
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:D3")

    def test_hoja_de_condiciones(self):
        ws = libro(condiciones=["Días en ordeño >= 0", "Producción 24 h < 30"],
                   generado=datetime.datetime(2026, 11, 23, 12, 0))["Condiciones"]
        textos = [c.value for fila in ws.iter_rows() for c in fila if c.value]
        self.assertIn("Prod. x lactancia", textos)
        self.assertIn("Días en ordeño >= 0", textos)
        self.assertTrue(any("23/11/2026" in str(t) for t in textos))

    def test_avisa_si_se_corto_por_el_tope(self):
        ws = libro({"columnas": COLS, "filas": FILAS, "truncado": True})["Condiciones"]
        textos = " ".join(str(c.value) for fila in ws.iter_rows() for c in fila if c.value)
        self.assertIn("se cortó", textos.lower())

    def test_sin_filas_igual_arma_el_archivo(self):
        ws = libro({"columnas": COLS, "filas": [], "truncado": False})["Informe"]
        self.assertEqual(ws.max_row, 1)

    def test_nombre_de_archivo_seguro(self):
        self.assertEqual(ie.nombre_de_archivo('Prod. x lactancia: "2026"/<a>', datetime.date(2026, 11, 23)), "Prod. x lactancia 2026 a 2026-11-23.xlsx")
        self.assertEqual(ie.nombre_de_archivo("", datetime.date(2026, 11, 23)), "Informe 2026-11-23.xlsx")


if __name__ == "__main__":
    unittest.main()
