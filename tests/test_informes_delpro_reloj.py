"""El filtro de servidor de DelPro que depende del reloj se ignora ENTERO, con aviso (25/11/2026).

«Separación de Vacas Mezcladas» pide, todo unido por «y», una vaca posiblemente en rodeo equivocado, un rodeo menor a 6, hembra, que no haya salido y además un día y una hora (`Farm.HourOfDay` es
`GetHour(Now())` y `Farm.DayOfWeek` es `GetDayOfWeek(DDMToday())`). Es el filtro de la separación programada, pero está puesto en informes de otra cosa (Producción, Velocidad de ordeño...). Ignorar solo
las condiciones del reloj dejaba las otras: «Producción» mostraba 6 vacas en vez de 1.459 (copia del 20/11/2026). El usuario decidió ignorarlo, con aviso.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_delpro_reloj -v
"""
import unittest

import informes_delpro as dl
from tests.test_informes_delpro import columna, columnas_de, datos, filtro, informe

SEPARACION = ("[<AnimalExtended>PossiblyInWrongGroup] = True And [Group.Herd.Farm.HourOfDay] > 0 And [Group.Herd.Farm.DayOfWeek] = 2 And "
              "[Group.Herd.Farm.HourOfDay] < 6 And [AnimalGroupNumber] < 6 And [IsExited] = False And [Sex] = 2")


def traducir(texto_filtro, columnas=("Number", "CurrentDIM"), **extra):
    d = datos([informe(1, "Producción", ServerFilter=7, **extra)], columnas_de(1, list(columnas)), [filtro(7, "Separación de Vacas Mezcladas", texto_filtro)])
    return dl.traducir_informe(d, 1)


def de_filtro(r):
    return [x for x in r["diferencias"] if x["tipo"] == "filtro"]


class TestElFiltroDelReloj(unittest.TestCase):
    def test_no_se_aplica_ninguna_de_sus_condiciones_y_el_informe_queda_completo(self):
        r = traducir(SEPARACION)
        self.assertEqual(r["estado"], "completo")
        self.assertNotIn("condiciones", r["definicion"])                 # ni la del rodeo equivocado, ni rodeo < 6, ni hembra, ni que no haya salido

    def test_se_avisa_con_el_nombre_y_todas_sus_condiciones(self):
        d = de_filtro(traducir(SEPARACION))
        self.assertEqual(len(d), 1)
        self.assertIn("Separación de Vacas Mezcladas", d[0]["texto"])
        self.assertIn("ignoró", d[0]["texto"])
        self.assertIn("más animales", d[0]["texto"])
        self.assertLess(len(d[0]["texto"]), 220)
        detalle = " ".join(d[0]["detalle"])
        for pedazo in ("PossiblyInWrongGroup", "HourOfDay", "DayOfWeek", "AnimalGroupNumber", "IsExited", "Sex"):
            self.assertIn(pedazo, detalle)

    def test_es_una_diferencia_de_contenido_aceptada(self):
        """Cambia QUÉ animales aparecen (por eso queda en la nota del informe y en los mensajes programados), pero el usuario la aceptó: no lo deja «parcial»."""
        d = de_filtro(traducir(SEPARACION))[0]
        self.assertEqual(d["gravedad"], "contenido")
        self.assertIs(d["aceptada"], True)

    def test_queda_anotado_en_el_origen_y_se_guarda_al_traer_varios(self):
        r = traducir(SEPARACION)
        origen = dl.origen_de(r)
        self.assertEqual(origen["avisos"], [de_filtro(r)[0]["texto"]])
        guardados = []
        d = datos([informe(1, "Producción", ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Separación de Vacas Mezcladas", SEPARACION)])
        dl.traer_varios(d, [1], lambda nombre, definicion, origen: (guardados.append((definicion, origen)) or "traido", 5))
        self.assertNotIn("condiciones", guardados[0][0])
        self.assertIn("ignoró", guardados[0][1]["avisos"][0])

    def test_en_la_lista_es_completo_pero_dice_que_hay_un_filtro_sin_traer(self):
        d = datos([informe(1, "Producción", ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Separación de Vacas Mezcladas", SEPARACION)])
        i = dl.listar(d)["informes"][0]
        self.assertEqual((i["estado"], i["filtro_sin_traer"], i["faltan"]), ("completo", True, []))

    def test_el_filtro_de_la_lista_se_sigue_aplicando(self):
        cols = [columna(51, 1, 1, "Number"), columna(52, 1, 2, "AnimalGroupName")]
        d = datos([informe(1, "Producción", ServerFilter=7, CustomFilter="[C52] In ('Rodeo 1', 'Rodeo 2')")], cols, [filtro(7, "Separación de Vacas Mezcladas", SEPARACION)])
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "AnimalGroupName", "op": "en", "valor": ["Rodeo 1", "Rodeo 2"]}])
        self.assertEqual(r["estado"], "completo")

    def test_las_demas_trabas_siguen_dejando_el_informe_parcial(self):
        r = traducir(SEPARACION, columnas=("Number", "Reloteo"))
        self.assertEqual(r["estado"], "parcial")
        self.assertTrue(de_filtro(r)[0]["aceptada"])

    def test_con_otro_nombre_y_solo_el_reloj_tambien(self):
        d = datos([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Turno noche", "[<AnimalExtended>Group.Herd.Farm.DayOfWeek] = 2")])
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["estado"], "completo")
        self.assertIn("Turno noche", de_filtro(r)[0]["texto"])

    def test_una_condicion_ilegible_al_lado_del_reloj_no_lo_esconde(self):
        r = traducir("[CurrentDIM] >>> 5 And [Group.Herd.Farm.HourOfDay] < 6")
        self.assertEqual(r["estado"], "completo")
        self.assertNotIn("condiciones", r["definicion"])
        self.assertIn(">>>", " ".join(de_filtro(r)[0]["detalle"]))


class TestLoQueNoEsUnFiltroDelReloj(unittest.TestCase):
    def test_el_informe_de_la_separacion_conserva_su_filtro(self):
        """Un informe que se llama como el filtro es el de la separación misma (en la base hay uno de DeLaval): ahí las condiciones SON su contenido, así que no se ignoran."""
        d = datos([informe(1, "Separacion de vacas mezcladas", ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Separación de Vacas Mezcladas", SEPARACION)])
        r = dl.traducir_informe(d, 1)
        self.assertEqual([c["campo"] for c in r["definicion"]["condiciones"]], ["PossiblyInWrongGroup", "AnimalGroupNumber", "IsExited", "Sex"])
        self.assertEqual(r["estado"], "parcial")
        self.assertNotIn("aceptada", de_filtro(r)[0])

    def test_sin_reloj_se_traduce_como_siempre(self):
        r = traducir("[CurrentDIM] <= 30 And [AnimalGroupNumber] < 6")
        self.assertEqual([(c["campo"], c["op"], c["valor"]) for c in r["definicion"]["condiciones"]], [("CurrentDIM", "<=", 30), ("AnimalGroupNumber", "<", 6)])
        self.assertEqual(de_filtro(r), [])

    def test_el_reloj_dentro_de_un_o_con_otro_dato_no_se_ignora(self):
        r = traducir("[CurrentDIM] <= 30 Or [Group.Herd.Farm.HourOfDay] < 6")
        self.assertEqual(r["estado"], "parcial")
        self.assertNotIn("aceptada", de_filtro(r)[0])

    def test_una_ruta_que_solo_se_parece_no_es_el_reloj(self):
        r = traducir("[Group.Herd.OtherFarm.HourOfDay] < 6")
        self.assertEqual(r["estado"], "parcial")
        r = traducir("[AnimalGroupName] = 'Farm.HourOfDay'")
        self.assertEqual(de_filtro(r), [])

    def test_en_el_filtro_de_la_lista_no_se_ignora(self):
        cols = [columna(51, 1, 1, "Number")]
        r = dl.traducir_informe(datos([informe(1, CustomFilter="[Group.Herd.Farm.HourOfDay] < 6")], cols), 1)
        self.assertEqual(r["estado"], "parcial")

    def test_un_filtro_imposible_de_leer_sigue_avisando_como_siempre(self):
        r = traducir("[C1 >>> ???")
        self.assertEqual(r["estado"], "parcial")
        self.assertNotIn("aceptada", de_filtro(r)[0])


if __name__ == "__main__":
    unittest.main()
