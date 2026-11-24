"""Lo que encontró la revisión independiente del importador de DelPro (la noche del 24/11/2026) y se arregló. Cada caso salió de una entrada concreta:
una definición o un filtro que mostraba MENOS animales que DelPro, un informe «completo» que no lo era, o un filtro raro que tumbaba todo el listado.

    C:\\Python314\\python.exe -m unittest tests.test_informes_delpro_revision -v
"""
import re
import time
import unittest
from unittest import mock

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im
from tests.test_informes_delpro import carpeta, columna, columnas_de, datos, filtro, informe


def tr(informes, columnas, filtros=(), oid=1, **kw):
    return dl.traducir_informe(datos(informes, columnas, filtros), oid, **kw)


def tipos(r, gravedad=None):
    return [(d["tipo"], d["gravedad"]) for d in r["diferencias"] if gravedad is None or d["gravedad"] == gravedad]


class TestUnFiltroRaroNoTumbaNada(unittest.TestCase):
    """Un solo filtro corrupto de DelPro no puede dar un 500 en toda la lista ni en el informe."""

    MALOS = ["[CurrentDIM] < 1e999", "[CurrentDIM] > -1e999", "[CurrentDIM] > 1" + "0" * 309, "[CurrentDIM] > " + "9" * 400, "[CurrentDIM] >= 1e308 * 10",
             "[CurrentDIM] > " + "+".join(["1"] * 1100), "[CurrentDIM] > " + "(" * 60 + "1" + ")" * 60, "[CurrentDIM] In (" + ", ".join(["1e999"] * 3) + ")"]

    def test_condiciones_de_nunca_levanta(self):
        for texto in self.MALOS:
            cs, pend = dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, 10)
            self.assertEqual(cs, [], texto[:40])
            self.assertEqual(len(pend), 1, texto[:40])

    def test_la_lista_sigue_y_el_informe_malo_figura_como_no_se_puede(self):
        informes = [informe(10, "Sano"), informe(11, "Roto", CustomFilter="[C2001] > 1e999"), informe(12, "Otro sano")]
        cols = columnas_de(10, ["Number"], 100) + [columna(2001, 11, 1, "CurrentDIM")] + columnas_de(12, ["Number"], 300)
        d = datos(informes, cols)
        r = dl.listar(d)
        self.assertEqual({i["oid"]: i["estado"] for i in r["informes"]}, {10: "completo", 11: "parcial", 12: "completo"})

    def test_aunque_algo_inesperado_falle_la_lista_no_se_cae(self):
        d = datos([informe(10, "Sano"), informe(11, "Raro")], columnas_de(10, ["Number"], 100) + columnas_de(11, ["Number"], 200))
        original = dl.traducir_informe

        def a_veces(datos_, oid, *a, **k):
            if oid == 11:
                raise MemoryError("algo que no es de DelPro")
            return original(datos_, oid, *a, **k)
        dl.traducir_informe = a_veces
        try:
            r = dl.listar(d)
        finally:
            dl.traducir_informe = original
        por = {i["oid"]: i for i in r["informes"]}
        self.assertEqual(por[10]["estado"], "completo")
        self.assertEqual(por[11]["estado"], "no_disponible")
        self.assertNotIn("MemoryError", por[11]["motivo"])                    # un mensaje fijo, sin el texto de la excepción

    def test_el_costo_de_limpiar_una_ruta_es_lineal(self):
        t0 = time.perf_counter()
        cs, pend = dl.condiciones_de("[" + "<" * 3980 + "] = 1", dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, 10)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(cs, [])


class TestFiltroDeLaGrillaYLosNulos(unittest.TestCase):
    """DelPro evalúa el filtro de la grilla EN MEMORIA, donde un animal sin dato pasa un «menor que» o un «distinto de»; SQL lo deja afuera. Traducirlo solo
    mostraría MENOS animales. Si el mismo campo exige «con dato», los dos coinciden y se trae."""

    def cols(self):
        return [columna(12, 1, 1, "CurrentLactationNumber"), columna(13, 1, 2, "SevenDayAverage"), columna(14, 1, 3, "AnimalGroupName")]

    def grilla(self, texto):
        return tr([informe(1, CustomFilter=texto)], self.cols())

    def test_menor_que_solo_no_se_trae(self):
        for texto in ("[C13] < 18", "[C13] <= 18", "[C12] <> 1"):
            r = self.grilla(texto)
            self.assertNotIn("condiciones", r["definicion"], texto)
            self.assertEqual(r["estado"], "parcial", texto)
            self.assertTrue([d for d in r["diferencias"] if d["tipo"] == "filtro"], texto)
            self.assertIn("sin dato", " ".join(d["texto"] + " ".join(d.get("detalle", [])) for d in r["diferencias"]), texto)       # el motivo va en el detalle

    def test_menor_que_con_con_dato_se_trae(self):
        r = self.grilla("[C13] < 18 And [C13] Is Not Null")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "SevenDayAverage", "op": "<", "valor": 18}])
        self.assertEqual(r["estado"], "completo")
        r = self.grilla("[C12] <> 1 And [C12] Is Not Null")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentLactationNumber", "op": "<>", "valor": 1}])

    def test_mayor_que_e_igual_se_traen_solos(self):
        for texto, op, valor in (("[C13] > 18", ">", 18), ("[C13] >= 18", ">=", 18), ("[C12] = 2", "=", 2), ("[C14] = 'Rodeo 1'", "es", "Rodeo 1")):
            r = self.grilla(texto)
            self.assertEqual(r["definicion"]["condiciones"][0]["op"], op, texto)
            self.assertEqual(r["estado"], "completo", texto)

    def test_un_con_dato_de_otro_campo_no_alcanza(self):
        r = self.grilla("[C13] < 18 And [C12] Is Not Null")
        self.assertNotIn("condiciones", r["definicion"])

    def test_entre_y_rangos_de_fechas_siguen_igual(self):
        r = self.grilla("[C13] Between(10, 20)")
        self.assertEqual(len(r["definicion"]["condiciones"]), 2)

    def test_el_filtro_de_servidor_es_de_SQL_y_el_menor_que_se_trae(self):
        r = tr([informe(1, ServerFilter=7)], self.cols(), [filtro(7, "F", "[SevenDayAverage] < 18")])
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "SevenDayAverage", "op": "<", "valor": 18}])


class TestLosResumenes(unittest.TestCase):
    def col(self, **kw):
        base = dict(SummaryType=6, SummaryTypes=None, GroupSummaryType=6)
        base.update(kw)
        return [columna(11, 1, 1, "AnimalGroupName", GroupIndex=0), columna(12, 1, 2, "SevenDayAverage", **base)]

    def test_total_y_grupos_con_resumenes_distintos_se_avisan_y_el_informe_es_parcial(self):
        r = tr([informe(1)], self.col(SummaryType=0, SummaryTypes="0", GroupSummaryType=4))
        self.assertEqual(r["estado"], "parcial")
        d = [x for x in r["diferencias"] if x["tipo"] == "resumen" and x["gravedad"] == "columnas"]
        self.assertEqual(len(d), 1)
        self.assertIn("suma", d[0]["texto"].lower())
        self.assertIn("promedio", d[0]["texto"].lower())

    def test_total_y_grupos_con_el_mismo_resumen_no_avisan(self):
        r = tr([informe(1)], self.col(SummaryType=4, SummaryTypes="4", GroupSummaryType=4))
        self.assertEqual(r["definicion"]["columnas"][1]["resumen"], "avg")
        self.assertEqual([x for x in r["diferencias"] if x["tipo"] == "resumen"], [])

    def test_un_resumen_en_texto_cae_al_codigo_numerico(self):
        for texto in ("Average", "[4]", "None", ""):
            r = tr([informe(1)], self.col(SummaryType=4, SummaryTypes=texto, GroupSummaryType=4))
            self.assertEqual(r["definicion"]["columnas"][1].get("resumen"), "avg", texto)

    def test_un_resumen_que_se_pierde_hace_parcial_al_informe(self):
        r = tr([informe(1)], [columna(11, 1, 1, "Number", SummaryType=0, SummaryTypes="0")])
        self.assertEqual(r["estado"], "parcial")                              # un total que DelPro mostraba y acá no aparece no es «se ve distinto»


class TestColumnasRepetidas(unittest.TestCase):
    def test_se_queda_con_la_que_tiene_resumen(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "SevenDayAverage"), columna(13, 1, 3, "SevenDayAverage", SummaryType=0, SummaryTypes="0", GroupSummaryType=0)]
        r = tr([informe(1)], cols)
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "SevenDayAverage"])
        self.assertEqual(r["definicion"]["columnas"][1].get("resumen"), "sum")

    def test_si_traian_resumenes_distintos_se_avisa_cual_se_pierde(self):
        cols = [columna(12, 1, 1, "SevenDayAverage", SummaryType=4, SummaryTypes="4", GroupSummaryType=4), columna(13, 1, 2, "SevenDayAverage", SummaryType=0, SummaryTypes="0", GroupSummaryType=0)]
        r = tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["columnas"][0].get("resumen"), "avg")
        d = [x for x in r["diferencias"] if x["tipo"] == "resumen"]
        self.assertTrue(d)
        self.assertNotEqual(d[0]["gravedad"], "info")
        self.assertIn("suma", d[0]["texto"].lower())


class TestElFiltroPropioDeLaColumna(unittest.TestCase):
    def test_sin_filtro_de_grilla_se_usa_el_de_cada_columna(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentLactationNumber", FilterString="[{0}] >= 3")]
        r = tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentLactationNumber", "op": ">=", "valor": 3}])

    def test_si_hay_filtro_de_grilla_el_de_la_columna_no_se_repite(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentLactationNumber", FilterString="[{0}] >= 3")]
        r = tr([informe(1, CustomFilter="[C12] >= 3")], cols)
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentLactationNumber", "op": ">=", "valor": 3}])

    def test_un_filtro_de_columna_que_no_se_trae_se_avisa(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "InMilk", FilterString="[{0}] = True")]
        r = tr([informe(1)], cols)
        self.assertEqual(r["estado"], "parcial")
        self.assertTrue([d for d in r["diferencias"] if d["tipo"] == "filtro"])


class TestElAlcanceDelRebano(unittest.TestCase):
    def test_todos_los_rebanos_se_avisa_y_dice_que_salen_menos_animales(self):
        """El verificador independiente lo mostró con datos reales: «todos los rebaños» es el caso habitual (26 de los 58 informes de animales del tambo) y
        mostrar solo los animales del tambo es justo lo que se quiere. Se avisa (info) pero NO vuelve «parcial» a la mitad de la lista."""
        r = tr([informe(1, Herd=0)], columnas_de(1, ["Number"]))
        d = [x for x in r["diferencias"] if x["tipo"] == "alcance"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "info")
        self.assertEqual(r["estado"], "completo")
        self.assertIn("menores", d[0]["texto"])                                # acá salen MENOS animales, no más: el texto lo dice

    def test_el_rebano_del_tambo_no_dice_nada(self):
        r = tr([informe(1, Herd=1)], columnas_de(1, ["Number"]), rebanos=[1])
        self.assertEqual(r["estado"], "completo")
        self.assertEqual([x for x in r["diferencias"] if x["tipo"] == "alcance"], [])

    def test_un_informe_de_otro_rebano_de_la_base_no_se_trae(self):
        r = tr([informe(1, Herd=6)], columnas_de(1, ["Number"]), rebanos=[1])
        self.assertEqual(r["estado"], "no_disponible")
        self.assertIsNone(r["definicion"])
        self.assertIn("otro tambo", r["motivo"])

    def test_sin_saber_los_rebanos_del_tambo_no_se_descarta_nada(self):
        self.assertEqual(tr([informe(1, Herd=6)], columnas_de(1, ["Number"]))["estado"], "completo")

    def test_un_rebano_raro_o_vacio_no_rompe(self):
        for h in (None, -1):
            self.assertEqual(tr([informe(1, Herd=h)], columnas_de(1, ["Number"]), rebanos=[1])["estado"], "completo", h)

    def test_la_lista_deja_afuera_solo_a_los_de_otro_rebano(self):
        d = datos([informe(1, "Todos", Herd=0), informe(2, "Mío", Herd=1), informe(3, "De otro", Herd=6)], columnas_de(1, ["Number"], 10) + columnas_de(2, ["Number"], 20) + columnas_de(3, ["Number"], 30))
        por = {i["oid"]: i for i in dl.listar(d, rebanos=[1])["informes"]}
        self.assertEqual((por[1]["estado"], por[2]["estado"], por[3]["estado"]), ("completo", "completo", "no_disponible"))


class TestLoOcultoYLosColores(unittest.TestCase):
    def test_un_informe_oculto_de_delpro_no_se_lista(self):
        d = datos([informe(1, "Visible"), informe(2, "AnimalInfoReportInternal", IsHidden=True)], columnas_de(1, ["Number"], 10) + columnas_de(2, ["Number"], 20))
        self.assertEqual([i["nombre"] for i in dl.listar(d)["informes"]], ["Visible"])

    def test_las_reglas_de_color_se_avisan(self):
        r = tr([informe(1, ConColores=1)], columnas_de(1, ["Number"]))
        d = [x for x in r["diferencias"] if x["tipo"] == "formato"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "presentacion")
        self.assertEqual(r["estado"], "completo")

    def test_las_consultas_siguen_siendo_de_solo_lectura(self):
        for sql in dl.CONSULTAS.values():
            db.validate_sql(sql)
        self.assertIn("IsHidden", dl.CONSULTAS["informes"])
        self.assertIn("ConColores", dl.CONSULTAS["informes"])
        self.assertNotIn("FormatRule,", dl.CONSULTAS["informes"].replace("r.FormatRule IS NULL", ""))        # el XML de las reglas no viaja: solo si hay o no


class TestLoQueSeOrdenaYSeAgrupa(unittest.TestCase):
    def test_un_grupo_descendente_se_avisa(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentLactationNumber", GroupIndex=0, SortDirection=2)]
        r = tr([informe(1)], cols)
        d = [x for x in r["diferencias"] if x["tipo"] == "orden" and "grupo" in x["texto"].lower()]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "presentacion")

    def test_un_grupo_ascendente_no_avisa(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentLactationNumber", GroupIndex=0, SortDirection=1)]
        self.assertEqual([x for x in tr([informe(1)], cols)["diferencias"] if x["tipo"] == "orden"], [])


class TestLosCamposSinVerificar(unittest.TestCase):
    def test_un_informe_con_un_campo_sin_verificar_lo_dice(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentReproductionStatus", GroupIndex=0)]
        r = tr([informe(1)], cols)
        d = [x for x in r["diferencias"] if x["tipo"] == "verificacion"]
        self.assertEqual(len(d), 1)
        self.assertIn("Estado de la reproducción", d[0]["texto"])
        self.assertEqual(d[0]["gravedad"], "info")

    def test_los_verificados_no_avisan(self):
        self.assertEqual([x for x in tr([informe(1)], columnas_de(1, ["Number", "CurrentDIM"]))["diferencias"] if x["tipo"] == "verificacion"], [])


class TestSiLaLecturaSeCorta(unittest.TestCase):
    def test_una_lectura_truncada_no_se_toma_por_completa(self):
        def run(sql, validate=True, tambo=None, max_rows=None):
            return {"columns": ["OID", "Name", "FilterText"], "rows": [[1, "F", ""]], "truncated": True}
        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)
        with self.assertRaises(dl.DelProNoDisponible) as cm:
            dl.cargar("ponderosa", run=run)
        self.assertIn("demasiados", str(cm.exception))


class TestFiltrosEscritosSinCorchetes(unittest.TestCase):
    """DelPro escribe muchos filtros de servidor como `ReproductionInfo.LactationNumber > 0 AND ReproductionInfo.IsDryingOff = False` (70 de 133 en la base real):
    sin corchetes y con rutas con puntos. Y un término que no se entiende no tiene que llevarse puestos a los demás."""

    def cond(self, texto, maximo=10):
        return dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, maximo)

    def test_nombres_sin_corchetes(self):
        cs, pend = self.cond("CurrentDIM >= 14 AND AnimalGroupNumber < 6")
        self.assertEqual([(c["campo"], c["op"], c["valor"]) for c in cs], [("CurrentDIM", ">=", 14), ("AnimalGroupNumber", "<", 6)])
        self.assertEqual(pend, [])

    def test_la_lactancia_de_la_reproduccion_es_la_del_catalogo(self):
        cs, pend = self.cond("ReproductionInfo.LactationNumber > 0 AND ReproductionInfo.IsDryingOff = False")
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": ">", "valor": 0}])
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["campos"], ["IsDryingOff"])

    def test_un_termino_ilegible_no_descarta_a_los_demas(self):
        cs, pend = self.cond("[CurrentDIM] >= 14 And Milkings[Date = 1].Exists() And [AnimalGroupNumber] < 6")
        self.assertEqual([c["campo"] for c in cs], ["CurrentDIM", "AnimalGroupNumber"])
        self.assertEqual(len(pend), 1)
        self.assertIn("no se pudo leer", pend[0]["motivo"].lower())

    def test_con_un_o_afuera_no_se_parte(self):
        cs, pend = self.cond("[CurrentDIM] >= 14 And ??? Or [AnimalGroupNumber] < 6")
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_la_y_dentro_de_un_texto_o_un_parentesis_no_parte(self):
        cs, pend = self.cond("[AnimalGroupName] = 'Rodeo A And B' And ([CurrentDIM] > 5 Or [CurrentDIM] < 2)")
        self.assertEqual(cs, [{"campo": "AnimalGroupName", "op": "es", "valor": "Rodeo A And B"}])
        self.assertEqual(len(pend), 1)

    def test_un_valor_con_una_palabra_prohibida_no_pasa(self):
        """`db.validate_sql` rechaza hasta «Backup» o «Delete» DENTRO de un texto: el informe «completo» no correría."""
        for palabra in ("Backup", "Delete", "Update", "Restore"):
            cs, pend = self.cond(f"[AnimalGroupName] = '{palabra}'")
            self.assertEqual((cs, len(pend)), ([], 1), palabra)
            self.assertIn("no se puede usar", pend[0]["motivo"])

    def test_el_tope_no_cuenta_dos_veces_lo_que_ya_estaba(self):
        d = datos([informe(1, CustomFilter=" And ".join(f"[C12] > {i}" for i in range(10)), ServerFilter=7)], [columna(12, 1, 1, "CurrentDIM")],
                  [filtro(7, "F", " And ".join(f"[CurrentDIM] > {i}" for i in range(10)))])
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["estado"], "completo")                                # las mismas 10 condiciones en las dos fuentes son 10, no 20
        self.assertEqual(len(r["definicion"]["condiciones"]), 10)

    def test_el_aviso_del_tope_dice_el_tope_de_verdad(self):
        d = datos([informe(1, CustomFilter=" And ".join(f"[C12] > {i}" for i in range(8)), ServerFilter=7)], [columna(12, 1, 1, "CurrentDIM")],
                  [filtro(7, "F", " And ".join(f"[CurrentLactationNumber] > {i}" for i in range(4)))])
        r = dl.traducir_informe(d, 1)
        textos = " ".join(x["texto"] + " ".join(x.get("detalle", [])) for x in r["diferencias"])
        self.assertIn(f"hasta {im.CONDICIONES_MAX} condiciones", textos)
        self.assertNotIn("hasta 2 condiciones", textos)


class TestLoQueSeLeeEsFiel(unittest.TestCase):
    def m(self, texto):
        return dl.mostrar(dl.analizar(texto))

    def test_los_parentesis_de_las_cuentas_se_conservan(self):
        self.assertEqual(self.m("[A] * ([B] + [C]) > 0"), "A * (B + C) > 0")
        self.assertEqual(self.m("[A] - ([B] - [C]) > 0"), "A - (B - C) > 0")
        self.assertEqual(self.m("-([A] + [B]) > 0"), "-(A + B) > 0")

    def test_los_numeros_no_se_redondean(self):
        self.assertEqual(self.m("[A] > 150000.75"), "A > 150000.75")
        self.assertEqual(self.m("[A] > 17.123456"), "A > 17.123456")
        self.assertEqual(self.m("[A] > 1234567.5"), "A > 1234567.5")

    def test_una_fecha_con_hora_dice_cual(self):
        self.assertIn("2026-01-07 13:30:00", self.m("[A] > #2026-01-07 13:30:00#"))
        self.assertNotIn("con hora", self.m("[A] > #1/7/2026#"))                  # un formato que no se entiende no es «una fecha con hora»


class TestLosTextosQueLeeElDueno(unittest.TestCase):
    def test_ortografia_y_plurales(self):
        r = tr([informe(1, ServerFilter=7)], columnas_de(1, ["Number", "InMilk", "ToBeCulled"]),
               [filtro(7, "Uno", "[<AnimalExtended>InMilk] = True"), ])
        textos = " ".join(d["texto"] for d in r["diferencias"])
        self.assertIn("No se trajeron 2 columnas", textos)
        self.assertNotIn("trajoron", textos)
        self.assertNotIn("condiciónes", textos)
        self.assertNotIn("  ", textos)                                            # sin dobles espacios
        r1 = tr([informe(1)], columnas_de(1, ["Number", "InMilk"]))
        self.assertIn("No se trajo 1 columna ", " ".join(d["texto"] for d in r1["diferencias"]))

    def test_los_campos_internos_de_delpro_se_explican_en_castellano(self):
        r = tr([informe(1)], columnas_de(1, ["Number", "InMilk", "ToBeCulled"]))
        texto = " ".join(d["texto"] for d in r["diferencias"])
        self.assertIn("En ordeño (InMilk)", texto)
        self.assertIn("Para descarte (ToBeCulled)", texto)

    def test_un_nivel_de_grupo_se_llama_rodeos(self):
        d = datos([informe(1, nivel="AnimalGroup")], columnas_de(1, ["Number"]))
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["nivel"], "Rodeos")
        self.assertNotIn("«Grupo»", r["motivo"])


class TestElMotorYLosNumerosEnormes(unittest.TestCase):
    def malo(self, valor, op="<"):
        with self.assertRaises(im.InformeError):
            im.compilar({"columnas": [{"campo": "CurrentDIM"}], "condiciones": [{"campo": "CurrentDIM", "op": op, "valor": valor}]}, herd=1)

    def test_un_numero_que_no_cabe_se_rechaza_con_un_mensaje(self):
        for v in ("9" * 400, "-" + "9" * 400, 10 ** 309, -10 ** 309, "1e999", "1" + "0" * 400, float("inf"), float("-inf"), float("nan"), "٢٠٢٦", "5 5", "5e3", "0x10"):
            self.malo(v)

    def test_un_numero_normal_sigue_andando(self):
        for v, esperado in ((5, "5"), ("38,5", "38.5"), (" 12 ", "12"), (-3, "-3"), (1e300, "1e+300"), ("9" * 40, "1e+40")):
            sql = im.compilar({"columnas": [{"campo": "CurrentDIM"}], "condiciones": [{"campo": "CurrentDIM", "op": "<", "valor": v}]}, herd=1)["sql"]
            self.assertIn(f"< {esperado}\n", sql + "\n", v)

    def test_en_una_lista_tambien(self):
        self.malo(["9" * 400, "7"], "en")
        self.malo([10 ** 309], "en")

    def test_nunca_sale_inf_en_la_consulta(self):
        sql = im.compilar({"columnas": [{"campo": "CurrentDIM"}], "condiciones": [{"campo": "CurrentDIM", "op": "<", "valor": 1e300}]}, herd=1)["sql"]
        self.assertIsNone(re.search(r"\b(inf|infinity|nan)\b", sql.lower()), sql)


# ============================================================================================== la revisión final (24/11/2026, sexta mirada)
# Cada caso salió de un hallazgo confirmado por un verificador adversarial con una reproducción propia.

def cond_nombre(texto, maximo=10, en_memoria=False, previas=()):
    return dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, maximo, previas=previas, en_memoria=en_memoria)


def diferencias(r, tipo):
    return [d for d in r["diferencias"] if d["tipo"] == tipo]


class TestElResumenDeGrupoSinGrupos(unittest.TestCase):
    """[importador #1] El `GroupSummaryType` es 0 («suma») en 1.450 de las 2.244 columnas de los informes SIN grupos del volcado real: es el valor por defecto
    de una columna, no una elección. Sin ninguna columna agrupada el resumen «de grupo» no se muestra nunca, así que no se trae (daba sumas sin sentido en el
    total y un «parcial» falso por «tenía conteo y suma»)."""

    def test_el_valor_por_defecto_de_un_informe_sin_grupos_no_trae_nada(self):
        r = tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=6, SummaryTypes=None, GroupSummaryType=0)])
        self.assertNotIn("resumen", r["definicion"]["columnas"][0])
        self.assertEqual(diferencias(r, "resumen"), [])
        self.assertEqual(r["estado"], "completo")

    def test_con_un_total_y_un_cero_de_grupo_por_defecto_no_hay_falso_parcial(self):
        r = tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=4, SummaryTypes="4", GroupSummaryType=0)])
        self.assertEqual(r["definicion"]["columnas"][0]["resumen"], "avg")
        self.assertEqual(diferencias(r, "resumen"), [])                           # ni «tenía promedio y suma» ni «solo en el total o solo en los grupos»
        self.assertEqual(r["estado"], "completo")

    def test_un_total_solo_en_un_informe_sin_grupos_no_es_una_asimetria(self):
        r = tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=4, SummaryTypes="4", GroupSummaryType=6)])
        self.assertEqual(diferencias(r, "resumen"), [])

    def test_con_grupos_el_resumen_de_grupo_sigue_trayendose(self):
        cols = [columna(10, 1, 1, "AnimalGroupName", GroupIndex=0), columna(11, 1, 2, "SevenDayAverage", SummaryType=6, GroupSummaryType=4)]
        r = tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["columnas"][1]["resumen"], "avg")
        self.assertEqual([d["gravedad"] for d in diferencias(r, "resumen")], ["info"])      # solo en los grupos: la asimetría se sigue avisando

    def test_con_grupos_un_total_y_un_grupo_distintos_siguen_avisandose(self):
        cols = [columna(10, 1, 1, "AnimalGroupName", GroupIndex=0), columna(11, 1, 2, "SevenDayAverage", SummaryType=4, SummaryTypes="4", GroupSummaryType=0)]
        r = tr([informe(1)], cols)
        self.assertEqual(r["estado"], "parcial")
        self.assertIn("suma", diferencias(r, "resumen")[0]["texto"].lower())

    def test_el_grupo_cuenta_aunque_el_campo_agrupado_no_este_en_el_catalogo(self):
        """Se mire o no el campo: DelPro agrupaba por algo (acá no se puede), así que los resúmenes de grupo de ese informe eran reales."""
        cols = [columna(10, 1, 1, "InMilk", GroupIndex=0), columna(11, 1, 2, "SevenDayAverage", SummaryType=6, GroupSummaryType=4)]
        r = tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["columnas"][0]["resumen"], "avg")

    def test_un_indice_de_grupo_vacio_no_es_un_grupo(self):
        for g in (None, -1):
            r = tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", GroupIndex=g, SummaryType=6, GroupSummaryType=0)])
            self.assertNotIn("resumen", r["definicion"]["columnas"][0], g)


class TestIncluirAnimalesDescartados(unittest.TestCase):
    """[importador #2] En DelPro el tilde incluye también a los animales que ya salieron (terneros y recría incluidos); acá solo ve a los que alguna vez
    ordeñaron (`rebano.filtro_historico`). Se trae igual, pero se dice: puede salir MENOS gente, y el informe es «parcial»."""

    def test_se_avisa_como_alcance_de_contenido_y_el_informe_queda_parcial(self):
        r = tr([informe(1, IncludeCulledAndExitedAnimals=True)], columnas_de(1, ["Number"]))
        self.assertIs(r["definicion"]["incluir_descartados"], True)
        d = diferencias(r, "alcance")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "contenido")
        self.assertEqual(r["estado"], "parcial")
        self.assertIn("ya salieron", d[0]["texto"])
        self.assertIn("alguna vez ordeñaron", d[0]["texto"])
        self.assertIn("menos", d[0]["texto"])

    def test_sin_el_tilde_no_se_dice_nada(self):
        for valor in (False, None, 0):
            r = tr([informe(1, IncludeCulledAndExitedAnimals=valor)], columnas_de(1, ["Number"]))
            self.assertEqual(diferencias(r, "alcance"), [], valor)
            self.assertEqual(r["estado"], "completo", valor)

    def test_se_suma_al_aviso_de_todos_los_rebanos_sin_pisarlo(self):
        r = tr([informe(1, IncludeCulledAndExitedAnimals=True, Herd=0)], columnas_de(1, ["Number"]))
        self.assertEqual(sorted(d["gravedad"] for d in diferencias(r, "alcance")), ["contenido", "info"])

    def test_la_lista_no_lo_confunde_con_un_filtro_sin_traer(self):
        """`filtro_sin_traer` dice «puede mostrar MÁS animales»; los descartados son lo contrario y no son un filtro."""
        d = datos([informe(1, "Con el tilde", IncludeCulledAndExitedAnimals=True), informe(2, "Con un filtro", ServerFilter=7)],
                  columnas_de(1, ["Number"], 10) + columnas_de(2, ["Number"], 20), [filtro(7, "F", "[<AnimalExtended>InMilk] = True")])
        por = {i["oid"]: i for i in dl.listar(d)["informes"]}
        self.assertEqual((por[1]["estado"], por[1]["filtro_sin_traer"], por[1]["n_diferencias"]), ("parcial", False, 1))
        self.assertTrue(por[2]["filtro_sin_traer"])


class TestUnCampoDeTextoCerrado(unittest.TestCase):
    """[importador #3] «Estado de la reproducción» (Abierta, Inseminada, Preñada, Seca) tiene valores cerrados. Un filtro de DelPro con un código (1) o un
    nombre en inglés (Open) no lo entiende nadie acá: se traía tal cual y el informe «completo» no devolvía ningún animal."""

    MOTIVO = "Compara con un código de DelPro que acá no existe."

    def test_un_valor_que_no_es_de_la_lista_no_se_trae(self):
        for v in ("1", "Open", "Pregnant", ""):
            cs, pend = cond_nombre(f"[CurrentReproductionStatus] = '{v}'")
            self.assertEqual((cs, len(pend)), ([], 1), v)
            self.assertEqual(pend[0]["motivo"], self.MOTIVO, v)

    def test_un_valor_de_la_lista_se_trae(self):
        cs, pend = cond_nombre("[CurrentReproductionStatus] = 'Abierta'")
        self.assertEqual(cs, [{"campo": "CurrentReproductionStatus", "op": "es", "valor": "Abierta"}])
        self.assertEqual(pend, [])

    def test_sin_tildes_ni_mayusculas_y_se_trae_con_el_nombre_de_la_lista(self):
        for escrito, canonico in (("abierta", "Abierta"), ("PREÑADA", "Preñada"), ("prenada", "Preñada"), (" seca ", "Seca"), ("INSEMINADA", "Inseminada")):
            cs, pend = cond_nombre(f"[CurrentReproductionStatus] = '{escrito}'")
            self.assertEqual(cs, [{"campo": "CurrentReproductionStatus", "op": "es", "valor": canonico}], escrito)
            self.assertEqual(pend, [], escrito)

    def test_en_una_lista_un_solo_valor_malo_deja_afuera_a_toda_la_lista(self):
        cs, pend = cond_nombre("[CurrentReproductionStatus] In ('Abierta', 'Open')")
        self.assertEqual((cs, len(pend)), ([], 1))
        self.assertEqual(pend[0]["motivo"], self.MOTIVO)

    def test_en_una_lista_todos_buenos_se_traen(self):
        cs, pend = cond_nombre("[CurrentReproductionStatus] In ('abierta', 'Preñada', 'SECA')")
        self.assertEqual(cs, [{"campo": "CurrentReproductionStatus", "op": "en", "valor": ["Abierta", "Preñada", "Seca"]}])
        self.assertEqual(pend, [])

    def test_contiene_acepta_un_pedazo_de_un_valor_y_rechaza_lo_que_no_es_de_ninguno(self):
        self.assertEqual(cond_nombre("Contains([CurrentReproductionStatus], 'Prena')")[1], [])
        cs, pend = cond_nombre("Contains([CurrentReproductionStatus], 'Open')")
        self.assertEqual((cs, len(pend)), ([], 1))
        self.assertEqual(pend[0]["motivo"], self.MOTIVO)

    def test_un_campo_de_texto_abierto_no_se_toca(self):
        cs, pend = cond_nombre("[AnimalGroupName] = 'cualquier cosa'")
        self.assertEqual(cs, [{"campo": "AnimalGroupName", "op": "es", "valor": "cualquier cosa"}])
        self.assertEqual(pend, [])

    def test_en_un_informe_queda_dicho_y_el_informe_es_parcial(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentReproductionStatus")]
        r = tr([informe(1, CustomFilter="[C12] = 'Open'")], cols)
        self.assertEqual(r["estado"], "parcial")
        self.assertNotIn("condiciones", r["definicion"])
        self.assertIn(self.MOTIVO, " ".join(" ".join(d.get("detalle", [])) for d in diferencias(r, "filtro")))


class TestUnRangoEscritoConDosComparaciones(unittest.TestCase):
    """[importador #5] El filtro de la grilla se evalúa EN MEMORIA: un animal sin dato pasa un «menor que». Pero si el mismo campo tiene OTRA condición que
    deja afuera a los nulos (un «mayor que», un «igual», un «entre»…), el sin dato ya no pasa y los dos mundos coinciden: no hay por qué perder la mitad del rango."""

    def c(self, texto, **kw):
        return cond_nombre(texto, en_memoria=True, **kw)

    def test_un_rango_con_dos_comparaciones_se_trae_entero(self):
        cs, pend = self.c("[SevenDayAverage] >= 3 And [SevenDayAverage] < 18")
        self.assertEqual(cs, [{"campo": "SevenDayAverage", "op": ">=", "valor": 3}, {"campo": "SevenDayAverage", "op": "<", "valor": 18}])
        self.assertEqual(pend, [])

    def test_el_orden_de_los_terminos_no_importa(self):
        cs, pend = self.c("[SevenDayAverage] < 18 And [SevenDayAverage] > 3")
        self.assertEqual(sorted((x["op"], x["valor"]) for x in cs), [("<", 18), (">", 3)])
        self.assertEqual(pend, [])

    def test_un_igual_o_un_es_uno_de_o_un_entre_tambien_cubren(self):
        for otro in ("[CurrentLactationNumber] = 2", "[CurrentLactationNumber] In (1b, 2b)", "[CurrentLactationNumber] Between(1, 3)"):
            cs, pend = self.c(f"[CurrentLactationNumber] <> 5 And {otro}")
            self.assertEqual(pend, [], otro)
            self.assertIn({"campo": "CurrentLactationNumber", "op": "<>", "valor": 5}, cs, otro)

    def test_otro_menor_que_o_distinto_de_no_cubre(self):
        """Dos «menor que» (o un «menor que» y un «distinto de») dejan pasar al animal sin dato igual que uno solo."""
        for texto in ("[SevenDayAverage] < 18 And [SevenDayAverage] < 20", "[CurrentLactationNumber] < 5 And [CurrentLactationNumber] <> 3"):
            cs, pend = self.c(texto)
            self.assertEqual((cs, len(pend)), ([], 2), texto)

    def test_una_condicion_de_otro_campo_no_cubre(self):
        cs, pend = self.c("[SevenDayAverage] < 18 And [CurrentLactationNumber] > 1")
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": ">", "valor": 1}])
        self.assertEqual(len(pend), 1)

    def test_un_menor_que_solo_sigue_sin_traerse(self):
        cs, pend = self.c("[SevenDayAverage] < 18")
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_una_condicion_previa_del_mismo_campo_cubre(self):
        previa = {"campo": "SevenDayAverage", "op": ">", "valor": 3}
        cs, pend = self.c("[SevenDayAverage] < 18", previas=[previa])
        self.assertEqual(cs, [{"campo": "SevenDayAverage", "op": "<", "valor": 18}])
        self.assertEqual(pend, [])
        cs, pend = self.c("[CurrentLactationNumber] < 18", previas=[previa])                  # de otro campo: no
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_dos_columnas_del_mismo_campo_con_su_propio_filtro_se_cubren_entre_si(self):
        """Sin filtro de la grilla DelPro usa el de cada columna; el de una cubre al de la otra (la segunda ve la primera como `previas`)."""
        cols = [columna(11, 1, 1, "SevenDayAverage", FilterString="[{0}] > 3"), columna(12, 1, 2, "CurrentDIM"),
                columna(13, 1, 3, "SevenDayAverage", FilterString="[{0}] < 18")]
        r = tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "SevenDayAverage", "op": ">", "valor": 3}, {"campo": "SevenDayAverage", "op": "<", "valor": 18}])

    def test_el_filtro_de_servidor_no_cambia(self):
        cs, pend = cond_nombre("[SevenDayAverage] < 18", en_memoria=False)
        self.assertEqual(len(cs), 1)

    def test_un_informe_con_el_rango_escrito_asi_es_completo(self):
        cols = [columna(12, 1, 1, "CurrentLactationNumber"), columna(13, 1, 2, "SevenDayAverage")]
        r = tr([informe(1, CustomFilter="[C13] >= 3 And [C13] < 18")], cols)
        self.assertEqual(len(r["definicion"]["condiciones"]), 2)
        self.assertEqual(r["estado"], "completo")


class TestLosMensajesNoEngañan(unittest.TestCase):
    """[importador #7] «El campo LastCalvingDate todavía no existe acá» cuando sí existe (es otra definición), y columnas mostradas como «UN-C2971»."""

    def test_un_campo_de_la_reproduccion_que_existe_pero_es_otra_definicion(self):
        cs, pend = cond_nombre("ReproductionInfo.LastCalvingDate > #2026-01-01#")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)
        self.assertNotIn("todavía no existe", pend[0]["motivo"])
        self.assertIn("reproducción", pend[0]["motivo"])
        self.assertIn("Fecha del último parto", pend[0]["motivo"])
        self.assertIn("ReproductionInfo.LastCalvingDate", pend[0]["texto"])             # se muestra con el nombre que tiene en DelPro
        self.assertEqual(pend[0]["campos"], [])                                         # existe en el catálogo: no es un campo que «falte»

    def test_un_campo_de_la_reproduccion_que_no_existe_sigue_diciendo_que_falta(self):
        cs, pend = cond_nombre("ReproductionInfo.IsDryingOff = False")
        self.assertIn("todavía no existe", pend[0]["motivo"])
        self.assertEqual(pend[0]["campos"], ["IsDryingOff"])

    def test_el_mismo_nombre_sin_el_prefijo_si_es_el_del_catalogo(self):
        cs, pend = cond_nombre("[LastCalvingDate] > #2026-01-01#")
        self.assertEqual(cs, [{"campo": "LastCalvingDate", "op": ">", "valor": "2026-01-01"}])

    def test_la_lactancia_de_la_reproduccion_sigue_siendo_la_del_catalogo(self):
        cs, pend = cond_nombre("ReproductionInfo.LactationNumber > 0")
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": ">", "valor": 0}])
        self.assertEqual(pend, [])

    def test_el_informe_con_ese_filtro_no_lista_el_campo_como_faltante(self):
        r = tr([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "F", "ReproductionInfo.LastCalvingDate > #2026-01-01#")])
        self.assertEqual(r["faltan"], [])
        self.assertEqual(r["estado"], "parcial")

    # --- «UN-C2971»

    def test_el_prefijo_un_se_acepta_igual_que_la_c(self):
        cols = [columna(2511, 1, 1, "CurrentLactationNumber"), columna(2691, 1, 2, "CMSFourteenDayAverage")]
        res = dl.resolver_por_columnas(cols, cat.CATALOGO)
        self.assertEqual(res("UN-C2511"), ("CurrentLactationNumber", "CurrentLactationNumber"))
        self.assertEqual(res("UN-C2691"), ("CMSFourteenDayAverage", None))
        self.assertEqual(res("C2691"), ("CMSFourteenDayAverage", None))

    def test_un_codigo_que_no_es_de_ninguna_columna_queda_como_esta(self):
        res = dl.resolver_por_columnas([columna(2511, 1, 1, "CurrentDIM")], cat.CATALOGO)
        self.assertEqual(res("UN-C9999"), ("UN-C9999", None))
        self.assertEqual(res("C9999"), ("C9999", None))

    def test_el_filtro_con_un_c_muestra_el_nombre_del_campo_y_no_el_codigo(self):
        cols = [columna(2691, 1, 1, "CMSFourteenDayAverage"), columna(2692, 1, 2, "Number")]
        r = tr([informe(1, CustomFilter="[UN-C2691] Is Not Null And [UN-C2691] < 18 And [UN-C2692] > 0")], cols)
        detalle = " ".join(" ".join(d.get("detalle", [])) + d["texto"] for d in diferencias(r, "filtro"))
        self.assertNotIn("UN-C", detalle)
        self.assertIn("CMSFourteenDayAverage", detalle)
        self.assertEqual(r["faltan"], ["CMSFourteenDayAverage"])

    def test_los_campos_conocidos_de_delpro_se_muestran_en_castellano_en_el_filtro(self):
        cols = [columna(12, 1, 1, "InMilk"), columna(13, 1, 2, "Number")]
        r = tr([informe(1, CustomFilter="[C12] = True")], cols)
        detalle = " ".join(" ".join(d.get("detalle", [])) for d in diferencias(r, "filtro"))
        self.assertIn("En ordeño (InMilk)", detalle)
        self.assertEqual(r["faltan"], ["InMilk"])                                      # el ranking de faltantes sigue usando el nombre interno

    def test_un_codigo_sin_identificar_no_dice_que_el_campo_no_existe(self):
        cs, pend = dl.condiciones_de("[C9999] > 5", dl.resolver_por_columnas([columna(1, 1, 1, "Number")], cat.CATALOGO), cat.CATALOGO, 10)
        self.assertEqual(cs, [])
        self.assertNotIn("todavía no existe", pend[0]["motivo"])
        self.assertIn("No se pudo identificar", pend[0]["motivo"])


class TestUnFiltroConSumasAnidadasNoTumbaElInforme(unittest.TestCase):
    """[seguridad #2] Los 200 operadores («+ - * /») eran POR llamada: 10 grupos de 190 entre paréntesis (3,8 KB) armaban un árbol de 1.900 niveles que
    al escribirlo en palabras agotaba la pila de Python (`RecursionError`) y el informe entero quedaba «no se pudo leer» en vez de «parcial»."""

    @staticmethod
    def anidado(grupos=10, por_grupo=190):
        s = "1" + "+1" * por_grupo
        for _ in range(grupos - 1):
            s = "(" + s + ")" + "+1" * por_grupo
        return "[CurrentDIM] > " + s

    def test_el_filtro_de_prueba_cabe_en_el_largo_maximo_y_pasaba_el_lector_por_partes(self):
        texto = self.anidado()
        self.assertLess(len(texto), dl.TEXTO_FILTRO_MAX)
        self.assertGreater(texto.count("+"), 10 * dl.MAX_OPERACIONES // 2)

    def test_el_tope_de_cuentas_es_de_todo_el_filtro(self):
        with self.assertRaises(dl.CriterioError):
            dl.analizar(self.anidado())
        cien = "+".join(["1"] * 101)                                                    # 100 cuentas
        noventa_y_nueve = "+".join(["1"] * 100)
        dl.analizar(f"[A] > ({cien}) + ({noventa_y_nueve})")                            # 100 + 1 + 99 = 200: entra
        with self.assertRaises(dl.CriterioError):
            dl.analizar(f"[A] > ({cien}) + ({cien})")                                   # 100 + 1 + 100 = 201: no entra

    def test_las_cuentas_normales_siguen_entrando(self):
        dl.analizar("[A] > ([B] + 5) * 2 - 1")
        dl.analizar("[A] > " + "+".join(["1"] * 200))                                    # 199 cuentas

    def test_condiciones_de_no_levanta_y_dice_que_no_se_pudo_leer(self):
        cs, pend = cond_nombre(self.anidado())
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_el_informe_queda_parcial_y_no_ilegible(self):
        cols = [columna(2001, 1, 1, "CurrentDIM")]
        filtro_malo = self.anidado().replace("[CurrentDIM]", "[C2001]")
        r = tr([informe(1, CustomFilter=filtro_malo)], cols)
        self.assertEqual(r["estado"], "parcial")
        self.assertTrue(diferencias(r, "filtro"))
        por = {i["oid"]: i for i in dl.listar(datos([informe(1, CustomFilter=filtro_malo)], cols))["informes"]}
        self.assertEqual(por[1]["estado"], "parcial")
        self.assertNotIn("No se pudo leer este informe", por[1]["motivo"])

    def test_aunque_el_lector_dejara_pasar_un_arbol_profundo_el_informe_no_se_cae(self):
        """Segunda red: el árbol que sí llega a `_faltantes_de` y a `mostrar` puede ser profundo (aquí 5.000 niveles armados a mano)."""
        n = dl._n("prop", 0, 10, path="CurrentDIM")
        profundo = dl._n("lit", 12, 13, k="num", v=1)
        for _ in range(5000):
            profundo = dl._n("arit", 0, 1, op="+", izq=profundo, der=dl._n("lit", 0, 1, k="num", v=1))
        arbol = dl._n("cmp", 0, 40, op=">", izq=n, der=profundo)
        with mock.patch.object(dl, "analizar", return_value=arbol):
            cs, pend = cond_nombre("[CurrentDIM] > algo muy profundo")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)


if __name__ == "__main__":
    unittest.main()
