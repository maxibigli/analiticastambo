"""«Estado de la reproducción» como lo muestra DelPro (27/11/2026): el código que DelPro guarda (`ReproductionInfo.BreedingState`, la fórmula del campo en
`DDMReportItem.Expression`) y no una cuenta sobre los bits, que juntaba terneras, vaquillonas y frescas en «Abierta». Los filtros de DelPro que comparan el estado
(la enumeración, el nombre en inglés o el código) ahora se traducen.

    C:\\Python314\\python.exe -m unittest tests.test_informes_estado_repro -v
"""
import datetime
import os
import re
import sqlite3
import unittest

import informes_catalogo as cat
import informes_delpro as dl
import informes_lenguaje
import informes_motor as im
from tests.test_informes_delpro import columna, datos, filtro, informe

CAMPO = cat.CATALOGO["CurrentReproductionStatus"]
# el código de DelPro -> cómo se muestra acá
ESTADOS = {1: "Ternera", 2: "Vaquillona", 3: "Fresca", 4: "Inseminada", 5: "Abierta", 6: "Preñada", 7: "Seca", 8: "Dada de baja"}
# el nombre de la enumeración `ReproductionStatus` de DelPro que se vio escrito en la base -> cómo se muestra acá
NOMBRES_DE_DELPRO = {"Heifer": "Vaquillona", "Fresh": "Fresca", "Bred": "Inseminada", "Open": "Abierta", "Pregnant": "Preñada", "Dry": "Seca"}
ENUM = "##Enum#DeLaval.DDM.CommonApp.Module.BasicFeatures.Reproduction.ReproductionStatus,{}#"
MOTIVO = "Compara con un código de DelPro que acá no existe."


def condiciones(texto):
    return dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, 10)


def es(valor):
    return [{"campo": "CurrentReproductionStatus", "op": "es", "valor": valor}]


class TestElCampo(unittest.TestCase):
    """La expresión real del catálogo, corrida contra una base de prueba."""

    def correr(self, filas_repro):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE b (OID INTEGER)")
        con.execute("CREATE TABLE r (Animal INTEGER, BreedingState INTEGER, IsDryingOff INTEGER, IsPregnant INTEGER, IsInseminated INTEGER)")
        animales = sorted({f[0] for f in filas_repro} | {99})                       # el 99 no tiene fila de reproducción
        con.executemany("INSERT INTO b VALUES (?)", [(a,) for a in animales])
        con.executemany("INSERT INTO r VALUES (?, ?, ?, ?, ?)", filas_repro)
        r = con.execute(f"SELECT b.OID, {CAMPO.expr} FROM b LEFT JOIN r ON r.Animal = b.OID ORDER BY b.OID").fetchall()
        con.close()
        return dict(r)

    def test_cada_codigo_de_delpro_da_su_estado_sin_mirar_los_bits(self):
        # los bits van al revés a propósito: el estado sale SOLO del código (una fresca, una ternera o una vaquillona tienen los tres bits en 0)
        filas = [(codigo, codigo, 1 if codigo == 3 else 0, 1 if codigo == 2 else 0, 0) for codigo in ESTADOS]
        r = self.correr(filas)
        self.assertEqual({k: v for k, v in r.items() if k != 99}, ESTADOS)

    def test_sin_fila_sin_codigo_o_con_un_codigo_desconocido_queda_vacio(self):
        r = self.correr([(1, None, 0, 0, 0), (2, 9, 0, 1, 1)])
        self.assertEqual(r, {1: None, 2: None, 99: None})

    def test_los_valores_posibles_van_en_el_orden_de_los_codigos(self):
        self.assertEqual(CAMPO.valores, tuple(ESTADOS.values()))
        self.assertTrue(CAMPO.agrupable)

    def test_sigue_marcado_y_la_nota_dice_que_falta_confirmar(self):
        self.assertFalse(CAMPO.verificado)
        for pieza in ("BreedingState", "«Ternera»", "«Dada de baja»", "«Incluir animales descartados»"):
            self.assertIn(pieza, CAMPO.nota)

    def test_las_equivalencias_son_los_codigos_y_los_nombres_vistos(self):
        esperadas = {**{str(c): v for c, v in ESTADOS.items()}, **NOMBRES_DE_DELPRO}
        self.assertEqual(dict(CAMPO.equivale), esperadas)


class TestElImportadorTraduceElEstado(unittest.TestCase):
    def test_la_enumeracion_de_delpro(self):
        for nombre, valor in NOMBRES_DE_DELPRO.items():
            cs, pend = condiciones(f"[<AnimalExtended>CurrentReproductionStatus] = {ENUM.format(nombre)}")
            self.assertEqual((cs, pend), (es(valor), []), nombre)

    def test_las_otras_formas_en_que_delpro_lo_escribe(self):
        for texto, valor in (("ReproductionInfo.BreedingState = 'Fresh'", "Fresca"),                   # sus filtros de fábrica
                             ("[<AnimalExtended>ReproductionInfo.BreedingState] = 'Open'", "Abierta"),
                             ("[<AnimalExtended>CurrentReproductionStatus] = '1'", "Ternera"),          # el código entre comillas
                             ("[CurrentReproductionStatus] = 5", "Abierta"),                            # y sin ellas
                             ("[CurrentReproductionStatus] = 'preñada'", "Preñada")):
            cs, pend = condiciones(texto)
            self.assertEqual((cs, pend), (es(valor), []), texto)

    def test_es_uno_de_mezclando_las_formas(self):
        cs, pend = condiciones(f"[CurrentReproductionStatus] In ({ENUM.format('Open')}, 'Fresh', 'Seca', '4')")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "CurrentReproductionStatus", "op": "en", "valor": ["Abierta", "Fresca", "Seca", "Inseminada"]}])

    def test_un_nombre_o_un_codigo_que_no_existe_no_se_trae(self):
        for texto in (f"[CurrentReproductionStatus] = {ENUM.format('Calf')}", f"[CurrentReproductionStatus] = {ENUM.format('Culled')}",
                      "[CurrentReproductionStatus] = '9'", "[CurrentReproductionStatus] = 0", "ReproductionInfo.BreedingState = 'Gestante'"):
            cs, pend = condiciones(texto)
            self.assertEqual((cs, len(pend)), ([], 1), texto)

    def test_un_codigo_desconocido_dice_por_que(self):
        self.assertEqual(condiciones("[CurrentReproductionStatus] = '9'")[1][0]["motivo"], MOTIVO)

    def test_distinto_de_sigue_sin_traerse(self):
        cs, pend = condiciones("[CurrentReproductionStatus] <> '1'")                                  # de un texto solo se trae «es igual a»
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_una_enumeracion_en_un_campo_sin_equivalencias_sigue_sin_traerse(self):
        for texto in ("[AnimalGroupName] = ##Enum#DeLaval.X.Otra,Open#", "[Sex] = ##Enum#DeLaval.X.Sex,Female#", "[CurrentDIM] = ##Enum#DeLaval.X.Otra,Open#"):
            cs, pend = condiciones(texto)
            self.assertEqual((cs, len(pend)), ([], 1), texto)

    def test_un_filtro_de_la_lista_sobre_la_columna_queda_completo(self):
        """Como «Vq a Parir en 2026»: el filtro de la grilla nombra la columna y compara con la enumeración."""
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentReproductionStatus")]
        r = dl.traducir_informe(datos([informe(1, CustomFilter=f"[C12] = {ENUM.format('Pregnant')}")], cols), 1)
        self.assertEqual(r["estado"], "completo", r["diferencias"])
        self.assertEqual(r["definicion"]["condiciones"], es("Preñada"))

    def test_un_filtro_de_delpro_como_anestro_post_parto_queda_completo(self):
        texto = f"[ToBeCulled] = False And [<AnimalExtended>CurrentReproductionStatus] = {ENUM.format('Open')} And [<AnimalExtended>ReproductionInfo.NumberOfInseminations] = 0"
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentReproductionStatus")]
        r = dl.traducir_informe(datos([informe(1, ServerFilter=7)], cols, [filtro(7, "Anestro post parto", texto)]), 1)
        self.assertEqual(r["estado"], "completo", r["diferencias"])
        self.assertIn({"campo": "CurrentReproductionStatus", "op": "es", "valor": "Abierta"}, r["definicion"]["condiciones"])


class TestLoQueUsaElCampo(unittest.TestCase):
    def test_un_informe_ya_guardado_con_abierta_sigue_valiendo(self):
        c = im.compilar({"columnas": [{"campo": "Number"}, {"campo": "CurrentReproductionStatus"}],
                         "condiciones": es("Abierta")}, herd=1, hoy="2026-11-20")
        self.assertIn("N'Abierta'", c["sql"])
        self.assertIn("r.BreedingState", c["sql"])

    def test_la_ia_ve_los_estados_de_delpro(self):
        self.assertIn("valores posibles: " + ", ".join(ESTADOS.values()), informes_lenguaje.sistema(datetime.date(2026, 11, 27)))

    def test_la_pantalla_tiene_un_color_para_cada_estado(self):
        ruta = os.environ.get("INFORMES_HTML") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "informes.html")
        with open(ruta, encoding="utf-8") as f:
            html = f.read()
        for valor in CAMPO.valores:
            self.assertRegex(html, re.escape(".st-" + valor.replace(" ", "-")) + r"\{", valor)


if __name__ == "__main__":
    unittest.main()
