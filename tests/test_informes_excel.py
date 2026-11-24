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

    def test_un_caracter_de_control_no_tumba_la_exportacion(self):
        """Un texto pegado desde Word puede traer un tabulador vertical (U+000B): `openpyxl` levanta IllegalCharacterError si se lo escribe, y el envío programado lo
        recalculaba 3 veces sin mandar nada."""
        filas = [["30" + chr(1) + "1", 1, 2, None], ["a" + chr(11) + "b\tc\nd", 1, 2, None], [chr(0) + chr(31), 1, 2, None]]
        ws = libro({"columnas": COLS, "filas": filas, "truncado": False})["Informe"]
        self.assertEqual(ws["A2"].value, "301")
        self.assertEqual(ws["A3"].value, "ab\tc\nd")                      # el tabulador y el salto de línea sí se conservan
        self.assertIsNone(ws["A4"].value)                                 # lo que queda vacío es una celda vacía
        wb = libro({"columnas": [dict(COLS[0], etiqueta="Rótulo" + chr(11) + " raro")] + COLS[1:], "filas": [], "truncado": False}, condiciones=["Condición" + chr(1) + " rara"])
        self.assertEqual(wb["Informe"]["A1"].value, "Rótulo raro")

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


# --- Fase 2: resúmenes por columna, grupos y totales -----------------------------------------------------------------------------
def _col(id_, etiqueta, tipo="num", unidad="", dec=0, resumen="none", alias=None):
    return {"id": id_, "etiqueta": etiqueta, "tipo": tipo, "unidad": unidad, "decimales": dec, "resumen": resumen, "alias": alias}


def _grupo(id_, etiqueta, alias, tipo="txt", dec=0):
    return {"id": id_, "alias": alias, "etiqueta": etiqueta, "tipo": tipo, "decimales": dec}


def _valores(ws):
    return [[c.value for c in fila] for fila in ws.iter_rows()]


def _textos(wb, hoja):
    return [c.value for fila in wb[hoja].iter_rows() for c in fila if c.value is not None]


class TestFilaDeTotales(unittest.TestCase):
    COLS = [_col("Number", "Número del animal", "txt", resumen="count", alias="c0"),
            _col("P24", "Producción 24 h", unidad="kg", dec=1, resumen="avg", alias="c1"),
            _col("Parto", "Último parto", "fecha", resumen="max", alias="c2")]

    def res(self, **kw):
        base = {"columnas": self.COLS, "truncado": False, "detalle": True, "claves_filas": None, "agrupar": [], "grupos": [],
                "filas": [["3250", 25.0, "2025-01-01T00:00:00"], ["4300", 30.0, None]],
                "totales": {"n": 2, "resumenes": {"c0": 2, "c1": 27.5, "c2": "2025-01-01T00:00:00"}}}
        base.update(kw)
        return base

    def test_una_columna_de_rotulos_y_la_fila_de_totales_al_final(self):
        f = _valores(libro(self.res())["Informe"])
        self.assertEqual(f[0][1:], ["Número del animal", "Producción 24 h (kg)", "Último parto"])
        self.assertEqual(f[1][1:], ["3250", 25.0, datetime.datetime(2025, 1, 1)])
        self.assertEqual(f[2][1:], ["4300", 30.0, None])
        self.assertEqual(f[3], ["Total · 2 animales", 2, 27.5, datetime.datetime(2025, 1, 1)])

    def test_cada_cifra_dice_que_resumen_es_y_sigue_siendo_un_numero(self):
        ws = libro(self.res())["Informe"]
        self.assertEqual(ws["B4"].number_format, '"Conteo "#,##0')
        self.assertEqual(ws["C4"].number_format, '"Prom. "#,##0.0')
        self.assertEqual(ws["D4"].number_format, '"Máx. "dd/mm/yyyy')
        self.assertIsInstance(ws["B4"].value, (int, float))                    # un conteo de texto es un NÚMERO

    def test_un_promedio_lleva_al_menos_un_decimal_aunque_la_columna_no_tenga(self):
        cols = [_col("Dias", "Días en ordeño", dec=0, resumen="avg", alias="c0"), _col("Dias2", "Otro", dec=0, resumen="sum", alias="c1"),
                _col("Dias3", "Tercero", dec=2, resumen="min", alias="c2")]
        res = self.res(columnas=cols, filas=[[10, 5, 1.5]], totales={"n": 1, "resumenes": {"c0": 10.0, "c1": 5.0, "c2": 1.5}})
        ws = libro(res)["Informe"]
        self.assertEqual([ws["B3"].number_format, ws["C3"].number_format, ws["D3"].number_format],
                         ['"Prom. "#,##0.0', '"Suma "#,##0', '"Mín. "#,##0.00'])

    def test_la_fila_de_totales_se_distingue(self):
        ws = libro(self.res())["Informe"]
        self.assertTrue(ws["A4"].font.bold)
        self.assertTrue(ws["C4"].font.bold)
        self.assertEqual(ws["A4"].border.top.style, "medium")

    def test_el_filtro_no_incluye_ni_los_rotulos_ni_la_fila_de_totales(self):
        self.assertEqual(libro(self.res())["Informe"].auto_filter.ref, "B1:D3")

    def test_un_resumen_sin_valor_deja_la_celda_vacia(self):
        res = self.res(filas=[], totales={"n": 0, "resumenes": {"c0": 0, "c1": None, "c2": None}})
        f = _valores(libro(res)["Informe"])
        self.assertEqual(f[1], ["Total · 0 animales", 0, None, None])

    def test_un_numero_que_no_es_finito_no_se_escribe(self):
        res = self.res(totales={"n": 2, "resumenes": {"c0": 2, "c1": float("nan"), "c2": None}})
        self.assertIsNone(libro(res)["Informe"]["C4"].value)

    def test_un_animal_se_cuenta_en_singular(self):
        res = self.res(filas=[["3250", 25.0, None]], totales={"n": 1, "resumenes": {"c0": 1, "c1": 25.0, "c2": None}})
        self.assertEqual(libro(res)["Informe"]["A3"].value, "Total · 1 animal")

    def test_los_miles_van_con_punto(self):
        res = self.res(totales={"n": 1664, "resumenes": {"c0": 1664, "c1": 27.5, "c2": None}})
        self.assertEqual(libro(res)["Informe"]["A4"].value, "Total · 1.664 animales")


class TestAgrupado(unittest.TestCase):
    COLS = [_col("Number", "Número del animal", "txt", alias="c0"), _col("P24", "Producción 24 h", unidad="kg", dec=1, resumen="avg", alias="c1")]
    G1 = _grupo("AnimalGroupName", "Nombre del grupo", "g0")
    G2 = _grupo("CurrentLactationNumber", "Nº Lactación", "g1", "num")

    def res(self, **kw):
        base = {"columnas": self.COLS, "agrupar": [self.G1], "truncado": False, "detalle": True,
                "filas": [["3250", 30.0], ["4300", 20.0], ["4317", 40.0]],
                "claves_filas": [["Rodeo 1"], ["Rodeo 1"], ["Rodeo 2"]],
                "grupos": [{"nivel": 1, "claves": ["Rodeo 1"], "n": 2, "resumenes": {"c1": 25.0}},
                           {"nivel": 1, "claves": ["Rodeo 2"], "n": 1, "resumenes": {"c1": 40.0}}],
                "totales": {"n": 3, "resumenes": {"c1": 30.0}}}
        base.update(kw)
        return base

    def res2(self, **kw):
        base = self.res(agrupar=[self.G1, self.G2],
                        filas=[["3250", 10.0], ["4300", 20.0], ["4317", 30.0]],
                        claves_filas=[["R1", 1], ["R1", 2], ["R1", 2]],
                        grupos=[{"nivel": 1, "claves": ["R1"], "n": 3, "resumenes": {"c1": 20.0}},
                                {"nivel": 2, "claves": ["R1", 1], "n": 1, "resumenes": {"c1": 10.0}},
                                {"nivel": 2, "claves": ["R1", 2], "n": 2, "resumenes": {"c1": 25.0}}])
        base.update(kw)
        return base

    def test_un_nivel_con_detalle(self):
        f = _valores(libro(self.res())["Informe"])
        self.assertEqual(f[0], ["Grupo", "Número del animal", "Producción 24 h (kg)"])
        self.assertEqual(f[1], ["Nombre del grupo: Rodeo 1 · 2 animales", None, 25.0])
        self.assertEqual(f[2], [None, "3250", 30.0])
        self.assertEqual(f[3], [None, "4300", 20.0])
        self.assertEqual(f[4], ["Nombre del grupo: Rodeo 2 · 1 animal", None, 40.0])
        self.assertEqual(f[5], [None, "4317", 40.0])
        self.assertEqual(f[6], ["Total · 3 animales", None, 30.0])

    def test_los_encabezados_de_grupo_y_el_total_se_distinguen(self):
        ws = libro(self.res())["Informe"]
        for r in (2, 5, 7):
            self.assertTrue(ws.cell(row=r, column=1).font.bold, r)
        self.assertFalse(ws["B3"].font.bold)

    def test_los_animales_se_pueden_plegar_bajo_su_grupo(self):
        ws = libro(self.res())["Informe"]
        self.assertEqual([ws.row_dimensions[r].outlineLevel for r in range(1, 8)], [0, 0, 1, 1, 0, 1, 0])
        self.assertFalse(ws.sheet_properties.outlinePr.summaryBelow)         # el +/- queda en el encabezado del grupo

    def test_sin_filtro_porque_ordenar_desarmaria_los_grupos(self):
        self.assertFalse(libro(self.res())["Informe"].auto_filter.ref)

    def test_el_orden_de_los_grupos_es_el_de_grupos_y_no_el_de_las_filas(self):
        res = self.res(filas=[["4317", 40.0], ["3250", 30.0], ["4300", 20.0]], claves_filas=[["Rodeo 2"], ["Rodeo 1"], ["Rodeo 1"]])
        f = _valores(libro(res)["Informe"])
        self.assertEqual([x[1] for x in f if x[1] and x[0] is None], ["3250", "4300", "4317"])
        self.assertTrue(f[1][0].startswith("Nombre del grupo: Rodeo 1"))

    def test_las_filas_se_reparten_aunque_la_clave_cambie_de_mayusculas_o_tenga_espacios_al_final(self):
        # SQL Server junta «Rodeo 1» con «rodeo 1» y con «Rodeo 1 » al agrupar: el detalle trae cada grafía.
        res = self.res(claves_filas=[["rodeo 1"], ["Rodeo 1 "], ["RODEO 2"]])
        f = _valores(libro(res)["Informe"])
        self.assertEqual([x[1] for x in f[2:4]], ["3250", "4300"])
        self.assertEqual(f[5][1], "4317")

    def test_una_fila_cuyo_grupo_no_figura_no_se_pierde(self):
        res = self.res(claves_filas=[["Rodeo 1"], ["Rodeo 1"], ["Rodeo 9"]])
        f = _valores(libro(res)["Informe"])
        self.assertIn("4317", [x[1] for x in f])
        self.assertTrue(any(x[0] and "sin grupo" in x[0].lower() for x in f))

    def test_dos_niveles_los_animales_van_con_su_subgrupo(self):
        ws = libro(self.res2())["Informe"]
        f = _valores(ws)
        self.assertEqual(f[1], ["Nombre del grupo: R1 · 3 animales", None, 20.0])
        self.assertEqual(f[2], ["Nº Lactación: 1 · 1 animal", None, 10.0])
        self.assertEqual(f[3], [None, "3250", 10.0])
        self.assertEqual(f[4], ["Nº Lactación: 2 · 2 animales", None, 25.0])
        self.assertEqual([x[1:] for x in f[5:7]], [["4300", 20.0], ["4317", 30.0]])
        self.assertEqual(f[7], ["Total · 3 animales", None, 30.0])

    def test_dos_niveles_el_subgrupo_va_sangrado_y_se_pliega_por_niveles(self):
        ws = libro(self.res2())["Informe"]
        self.assertEqual(ws["A3"].alignment.indent, 1)
        self.assertEqual(ws["A2"].alignment.indent, 0)
        self.assertEqual([ws.row_dimensions[r].outlineLevel for r in range(2, 9)], [0, 1, 2, 1, 2, 2, 0])

    def test_un_grupo_sin_dato_se_llama_sin_dato(self):
        res = self.res(claves_filas=[[None]] * 3, grupos=[{"nivel": 1, "claves": [None], "n": 3, "resumenes": {"c1": 30.0}}])
        f = _valores(libro(res)["Informe"])
        self.assertEqual(f[1][0], "Nombre del grupo: (sin dato) · 3 animales")
        self.assertEqual([x[1] for x in f[2:5]], ["3250", "4300", "4317"])

    def test_una_clave_numerica_entera_no_lleva_decimales(self):
        res = self.res(agrupar=[self.G2], claves_filas=[[2.0]] * 3, grupos=[{"nivel": 1, "claves": [2.0], "n": 3, "resumenes": {"c1": 30.0}}])
        self.assertEqual(_valores(libro(res)["Informe"])[1][0], "Nº Lactación: 2 · 3 animales")

    def test_si_la_lista_se_corto_el_total_y_cada_grupo_siguen_siendo_los_de_todos(self):
        res = self.res(truncado=True, filas=[["3250", 30.0]], claves_filas=[["Rodeo 1"]],
                       totales={"n": 900, "resumenes": {"c1": 31.0}},
                       grupos=[{"nivel": 1, "claves": ["Rodeo 1"], "n": 500, "resumenes": {"c1": 30.0}},
                               {"nivel": 1, "claves": ["Rodeo 2"], "n": 400, "resumenes": {"c1": 32.0}}])
        wb = libro(res)
        f = _valores(wb["Informe"])
        self.assertEqual(f[1], ["Nombre del grupo: Rodeo 1 · 500 animales", None, 30.0])
        self.assertEqual(f[3], ["Nombre del grupo: Rodeo 2 · 400 animales", None, 32.0])      # sin animales listados, con su resumen
        self.assertEqual(f[-1], ["Total · 900 animales", None, 31.0])
        avisos = " ".join(str(t) for t in _textos(wb, "Condiciones")).lower()
        self.assertIn("se cortó", avisos)
        self.assertIn("totales", avisos)

    def test_el_texto_de_una_clave_no_puede_ser_una_formula(self):
        res = self.res(claves_filas=[["=1+1"]] * 3, grupos=[{"nivel": 1, "claves": ["=1+1"], "n": 3, "resumenes": {"c1": 30.0}}])
        c = libro(res)["Informe"]["A2"]
        self.assertEqual(c.value, "Nombre del grupo: =1+1 · 3 animales")
        self.assertEqual(c.data_type, "s")


class TestSoloResumen(unittest.TestCase):
    COLS = [_col("Number", "Número del animal", "txt", alias="c0"),
            _col("P24", "Producción 24 h", unidad="kg", dec=1, resumen="avg", alias="c1"),
            _col("Dias", "Días en ordeño", resumen="max", alias="c2")]
    AGR = [_grupo("AnimalGroupName", "Nombre del grupo", "g0"), _grupo("CurrentLactationNumber", "Nº Lactación", "g1", "num")]
    RES = {"columnas": COLS, "agrupar": AGR, "filas": [], "claves_filas": None, "truncado": False, "detalle": False,
           "grupos": [{"nivel": 1, "claves": ["R1"], "n": 4, "resumenes": {"c1": 30.0, "c2": 400}},
                      {"nivel": 2, "claves": ["R1", 1], "n": 3, "resumenes": {"c1": 28.0, "c2": 400}},
                      {"nivel": 2, "claves": ["R1", 2], "n": 1, "resumenes": {"c1": 38.0, "c2": 150}}],
           "totales": {"n": 4, "resumenes": {"c1": 30.0, "c2": 400}}}

    def test_columnas_de_grupo_animales_y_solo_lo_que_tiene_resumen(self):
        f = _valores(libro(self.RES)["Informe"])
        self.assertEqual(f[0], ["Nombre del grupo", "Nº Lactación", "Animales", "Producción 24 h (kg) · Promedio", "Días en ordeño · Máximo"])

    def test_filas_subtotales_y_total(self):
        ws = libro(self.RES)["Informe"]
        f = _valores(ws)
        self.assertEqual(f[1], ["R1", "Subtotal", 4, 30.0, 400])
        self.assertEqual(f[2], ["R1", 1, 3, 28.0, 400])
        self.assertEqual(f[3], ["R1", 2, 1, 38.0, 150])
        self.assertEqual(f[4], ["Total", None, 4, 30.0, 400])
        self.assertTrue(ws["A2"].font.bold)
        self.assertFalse(ws["A3"].font.bold)
        self.assertTrue(ws["A5"].font.bold)

    def test_las_cifras_son_numeros_con_el_formato_de_su_columna(self):
        ws = libro(self.RES)["Informe"]
        self.assertEqual([ws["C3"].number_format, ws["D3"].number_format, ws["E3"].number_format], ["#,##0", "#,##0.0", "#,##0"])

    def test_con_dos_niveles_no_hay_filtro(self):
        self.assertFalse(libro(self.RES)["Informe"].auto_filter.ref)

    def test_un_solo_nivel(self):
        res = dict(self.RES, agrupar=self.AGR[:1], grupos=[{"nivel": 1, "claves": ["R1"], "n": 4, "resumenes": {"c1": 30.0, "c2": 400}},
                                                          {"nivel": 1, "claves": ["R2"], "n": 2, "resumenes": {"c1": 20.0, "c2": 100}}],
                   totales={"n": 6, "resumenes": {"c1": 26.7, "c2": 400}})
        ws = libro(res)["Informe"]
        f = _valores(ws)
        self.assertEqual(f[0], ["Nombre del grupo", "Animales", "Producción 24 h (kg) · Promedio", "Días en ordeño · Máximo"])
        self.assertEqual(f[1], ["R1", 4, 30.0, 400])
        self.assertEqual(f[2], ["R2", 2, 20.0, 100])
        self.assertEqual(f[3], ["Total", 6, 26.7, 400])
        self.assertEqual(ws.auto_filter.ref, "A1:D3")                  # se puede ordenar por cualquier columna, el total queda afuera

    def test_sin_grupos_solo_el_total(self):
        res = dict(self.RES, agrupar=[], grupos=[])
        f = _valores(libro(res)["Informe"])
        self.assertEqual(f[0], ["Animales", "Producción 24 h (kg) · Promedio", "Días en ordeño · Máximo"])
        self.assertEqual(f[1], [4, 30.0, 400])
        self.assertEqual(len(f), 2)

    def test_los_resumenes_de_fecha_son_fechas(self):
        cols = [_col("Parto", "Último parto", "fecha", resumen="min", alias="c0")]
        res = {"columnas": cols, "agrupar": [], "filas": [], "claves_filas": None, "truncado": False, "detalle": False, "grupos": [],
               "totales": {"n": 2, "resumenes": {"c0": "2025-03-04T00:00:00"}}}
        ws = libro(res)["Informe"]
        self.assertEqual(ws["B2"].value, datetime.datetime(2025, 3, 4))
        self.assertEqual(ws["B2"].number_format, "dd/mm/yyyy")
        self.assertEqual(ws["B1"].value, "Último parto · Mínimo")

    def test_un_conteo_de_un_campo_de_texto_es_un_numero(self):
        cols = [_col("Number", "Número del animal", "txt", resumen="count", alias="c0")]
        res = {"columnas": cols, "agrupar": [], "filas": [], "claves_filas": None, "truncado": False, "detalle": False, "grupos": [],
               "totales": {"n": 5, "resumenes": {"c0": 5}}}
        c = libro(res)["Informe"]["B2"]
        self.assertEqual(c.value, 5)
        self.assertEqual(c.number_format, "#,##0")

    def test_el_texto_de_una_clave_no_puede_ser_una_formula(self):
        res = dict(self.RES, agrupar=self.AGR[:1], grupos=[{"nivel": 1, "claves": ["=1+1"], "n": 1, "resumenes": {"c1": 1.0, "c2": 1}}])
        c = libro(res)["Informe"]["A2"]
        self.assertEqual(c.value, "=1+1")
        self.assertEqual(c.data_type, "s")

    def test_una_clave_sin_dato(self):
        res = dict(self.RES, agrupar=self.AGR[:1], grupos=[{"nivel": 1, "claves": [None], "n": 4, "resumenes": {"c1": 30.0, "c2": 400}}])
        self.assertEqual(libro(res)["Informe"]["A2"].value, "(sin dato)")

    def test_solo_grupos_sin_ningun_resumen_deja_animales_por_grupo(self):
        cols = [_col("Number", "Número del animal", "txt", alias="c0")]
        res = {"columnas": cols, "agrupar": self.AGR[:1], "filas": [], "claves_filas": None, "truncado": False, "detalle": False,
               "grupos": [{"nivel": 1, "claves": ["R1"], "n": 4, "resumenes": {}}], "totales": {"n": 4, "resumenes": {}}}
        f = _valores(libro(res)["Informe"])
        self.assertEqual(f, [["Nombre del grupo", "Animales"], ["R1", 4], ["Total", 4]])


class TestHojaCondicionesConResumenes(unittest.TestCase):
    def test_dice_cuantos_animales_cuenta_el_total_y_las_filas_listadas(self):
        res = TestAgrupado().res(totales={"n": 900, "resumenes": {"c1": 31.0}})
        t = _textos(libro(res), "Condiciones")
        self.assertIn("Animales en el informe", t)
        self.assertIn(900, t)
        self.assertIn("Filas", t)

    def test_un_informe_solo_de_resumen_no_dice_que_tiene_cero_filas(self):
        t = _textos(libro(TestSoloResumen.RES), "Condiciones")
        self.assertNotIn("Filas", t)
        self.assertIn("Animales en el informe", t)

    def test_una_lista_sin_resumenes_sigue_igual(self):
        t = _textos(libro(), "Condiciones")
        self.assertIn("Filas", t)
        self.assertNotIn("Animales en el informe", t)


class TestImpresion(unittest.TestCase):
    def comprobar(self, ws):
        self.assertEqual(ws.page_setup.orientation, "landscape")
        self.assertTrue(ws.sheet_properties.pageSetUpPr.fitToPage)
        self.assertEqual((ws.page_setup.fitToWidth, ws.page_setup.fitToHeight), (1, 0))       # una hoja de ancho, las que hagan falta de alto
        self.assertEqual(ws.print_title_rows, "$1:$1")                                         # el encabezado se repite en cada página

    def test_la_lista(self):
        self.comprobar(libro()["Informe"])

    def test_la_agrupada(self):
        self.comprobar(libro(TestAgrupado().res())["Informe"])

    def test_el_solo_resumen(self):
        self.comprobar(libro(TestSoloResumen.RES)["Informe"])

    def test_pliegues_y_impresion_conviven(self):
        ws = libro(TestAgrupado().res())["Informe"]
        self.assertFalse(ws.sheet_properties.outlinePr.summaryBelow)
        self.assertTrue(ws.sheet_properties.pageSetUpPr.fitToPage)


class TestEtiquetasPeligrosas(unittest.TestCase):
    def test_el_nombre_que_el_usuario_le_puso_a_un_campo_calculado_no_es_una_formula(self):
        cols = [_col("calc_1", "=HYPERLINK(\"http://x\")", alias="c0")]
        res = {"columnas": cols, "filas": [[1.0]], "truncado": False}
        c = libro(res)["Informe"]["A1"]
        self.assertEqual(c.data_type, "s")
        self.assertTrue(c.value.startswith("=HYPERLINK"))

    def test_tambien_en_un_informe_de_solo_resumen(self):
        cols = [_col("calc_1", "=1+1", resumen="sum", alias="c0")]
        res = {"columnas": cols, "agrupar": [], "filas": [], "claves_filas": None, "truncado": False, "detalle": False, "grupos": [],
               "totales": {"n": 1, "resumenes": {"c0": 2.0}}}
        c = libro(res)["Informe"]["B1"]
        self.assertEqual(c.data_type, "s")


if __name__ == "__main__":
    unittest.main()
