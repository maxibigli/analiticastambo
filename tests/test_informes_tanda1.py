"""Tanda 1 de campos nuevos del catálogo (sí/no de la reproducción, fechas y días de la reproducción, leche de ayer y de hoy) y lo que el importador de DelPro
necesita para entenderlos: los filtros de DelPro escriben los «sí/no» como `ReproductionInfo.IsPregnant = True`, `ToBeCulled = False`, `IsDryingOff <> True`.

Un campo «sí/no» es de texto con la lista cerrada («No», «Sí»): así se agrupa, se cuenta, sale en el Excel y en el mensaje sin tocar nada más, y la IA y el
importador ya saben qué valores admite.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda1 -v
"""
import unittest

import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im
import db

SI_NO = ("No", "Sí")
BOOLEANOS = ("InMilk", "ToBeCulled", "IsPregnant", "IsDryingOff")
FECHAS = ("BirthDate", "ExitDate", "LastInseminationDate", "LastAbortionDate", "ExpectedCalvingDate", "ExpectedDryOffDate")
NUMEROS = ("AgeInMonths", "NumberOfInseminations", "DaysSinceLastInsemination", "DaysToCalving", "DaysToDryOff")


class TestElCatalogo(unittest.TestCase):
    def test_estan_todos_con_el_nombre_que_les_da_delpro(self):
        for id_ in BOOLEANOS + FECHAS + NUMEROS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_los_si_no_son_de_texto_con_lista_cerrada_y_se_pueden_agrupar(self):
        for id_ in BOOLEANOS:
            c = cat.CATALOGO[id_]
            self.assertEqual((c.tipo, c.valores, c.agrupable), ("txt", SI_NO, True), id_)

    def test_los_tipos_de_los_demas(self):
        for id_ in FECHAS:
            self.assertEqual(cat.CATALOGO[id_].tipo, "fecha", id_)
        for id_ in NUMEROS:
            self.assertEqual(cat.CATALOGO[id_].tipo, "num", id_)

    def test_lo_que_se_calcula_con_una_cuenta_nuestra_lleva_la_advertencia(self):
        # Solo lo que DelPro GUARDA tal cual (una columna) es «verificado»; una fecha o un número que sale de una cuenta propia no se compara hasta tener un informe de DelPro.
        for id_ in ("ExpectedCalvingDate", "DaysToCalving", "ExpectedDryOffDate", "DaysToDryOff", "AgeInMonths", "InMilk", "DaysSinceLastInsemination"):
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)
        # Lo que es una columna de DelPro, o el índice y el resumen que DelPro mismo guarda (medido contra los datos), sí.
        for id_ in ("ToBeCulled", "IsPregnant", "IsDryingOff", "BirthDate", "ExitDate", "LastInseminationDate", "LastAbortionDate", "NumberOfInseminations"):
            self.assertTrue(cat.CATALOGO[id_].verificado, id_)



    def test_cada_campo_nuevo_tiene_una_nota_que_dice_de_donde_sale(self):
        for id_ in BOOLEANOS + FECHAS + NUMEROS:
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)


class TestSeCompilan(unittest.TestCase):
    """Cada campo nuevo, solo y con cada resumen y operador, genera una consulta que pasa la validación de solo lectura."""

    def sql(self, defn):
        c = im.compilar(defn)
        return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))

    def validar(self, defn):
        """Cada consulta del informe (el detalle y/o el resumen: «solo resumen» no trae el detalle) pasa la validación de solo lectura, una por una."""
        c = im.compilar(defn)
        consultas = [c[k] for k in ("sql", "sql_resumen") if c.get(k)]
        self.assertTrue(consultas)
        for q in consultas:
            db.validate_sql(q)

    def test_cada_campo_solo_pasa_la_validacion(self):
        for id_ in BOOLEANOS + FECHAS + NUMEROS:
            self.validar(({"columnas": [{"campo": "Number"}, {"campo": id_}]}))

    def test_los_si_no_se_filtran_y_se_agrupan(self):
        for id_ in BOOLEANOS:
            self.validar(({"columnas": [{"campo": id_}, {"campo": "Number", "resumen": "count"}], "agrupar_por": [id_], "detalle": False,
                                      "condiciones": [{"campo": id_, "op": "es", "valor": "Sí"}]}))

    def test_el_parto_esperado_lee_la_gestacion_de_delpro_y_no_un_numero_fijo(self):
        sql = self.sql({"columnas": [{"campo": "ExpectedCalvingDate"}, {"campo": "DaysToDryOff"}]})
        self.assertIn("ReproductionSetting", sql)
        self.assertIn("EffectiveInsemination", sql)

    def test_una_condicion_de_cada_tipo_nuevo(self):
        for defn in ({"campo": "DaysToCalving", "op": "<=", "valor": 30}, {"campo": "NumberOfInseminations", "op": ">=", "valor": 3},
                     {"campo": "ExpectedCalvingDate", "op": ">=", "valor": "2026-08-01"}, {"campo": "DaysSinceLastInsemination", "op": ">", "valor": 40}):
            self.validar(({"columnas": [{"campo": "Number"}], "condiciones": [defn]}))

    def test_los_resumenes_de_los_nuevos(self):
        self.validar(({"columnas": [{"campo": "NumberOfInseminations", "resumen": "avg"}, {"campo": "DaysToCalving", "resumen": "min"},
                                              {"campo": "ExpectedCalvingDate", "resumen": "max"}], "detalle": False}))

    def test_una_vaca_sin_reproduccion_es_no_y_no_nula(self):
        for id_ in BOOLEANOS:
            self.assertIn("ELSE 'No'", cat.CATALOGO[id_].expr, id_)


def filtro(texto, maximo=10):
    return dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, maximo)


class TestElImportadorEntiendeLosSiNo(unittest.TestCase):
    def test_igual_a_verdadero_y_a_falso(self):
        cs, pend = filtro("ReproductionInfo.IsPregnant = True And ToBeCulled = False")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "IsPregnant", "op": "es", "valor": "Sí"}, {"campo": "ToBeCulled", "op": "es", "valor": "No"}])

    def test_distinto_de_verdadero_es_no_y_distinto_de_falso_es_si(self):
        cs, pend = filtro("[<AnimalExtended>ReproductionInfo.IsDryingOff] <> True And [<AnimalExtended>InMilk] <> False")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "IsDryingOff", "op": "es", "valor": "No"}, {"campo": "InMilk", "op": "es", "valor": "Sí"}])

    def test_el_en_ordeno_de_delpro_se_trae(self):
        cs, pend = filtro("InMilk = True AND Group.CMSIsMilking")
        self.assertEqual(cs, [{"campo": "InMilk", "op": "es", "valor": "Sí"}])
        self.assertEqual(len(pend), 1)                                                   # lo de «Group.CMSIsMilking» NO se puede traer: se dice
        self.assertIn("Group.CMSIsMilking", pend[0]["texto"])

    def test_un_si_no_no_se_compara_con_un_numero_ni_con_un_texto(self):
        for texto in ("IsPregnant = 1", "IsPregnant = 'Sí'", "ToBeCulled > False"):
            cs, pend = filtro(texto)
            self.assertEqual(cs, [], texto)
            self.assertEqual(len(pend), 1, texto)

    def test_un_numero_o_una_fecha_no_se_toca(self):
        cs, pend = filtro("ReproductionInfo.NumberOfInseminations >= 3 And ReproductionInfo.DaysSinceLastInsemination > 40")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "NumberOfInseminations", "op": ">=", "valor": 3}, {"campo": "DaysSinceLastInsemination", "op": ">", "valor": 40}])

    def test_la_propiedad_de_la_reproduccion_es_el_mismo_campo_que_el_item_del_informe(self):
        res = dl.resolver_por_nombre(cat.CATALOGO)
        for ruta, id_ in (("ReproductionInfo.IsPregnant", "IsPregnant"), ("ReproductionInfo.IsDryingOff", "IsDryingOff"), ("ReproductionInfo.DaysToCalving", "DaysToCalving"),
                          ("ReproductionInfo.DaysToDryOff", "DaysToDryOff"), ("ReproductionInfo.NumberOfInseminations", "NumberOfInseminations"),
                          ("ReproductionInfo.DaysSinceLastInsemination", "DaysSinceLastInsemination"), ("ReproductionInfo.ExpectedCalvingDate", "ExpectedCalvingDate"),
                          ("<AnimalExtended>ToBeCulled", "ToBeCulled")):
            self.assertEqual(res(ruta), (id_, id_), ruta)

    def test_lo_que_sigue_distinto_sigue_distinto(self):
        res = dl.resolver_por_nombre(cat.CATALOGO)
        self.assertEqual(res("ReproductionInfo.LastCalvingDate")[1], None)                # es la fecha guardada en la reproducción, no la del último parto


class TestLaFechaDelUltimoPartoNoAgrupaLosEventos(unittest.TestCase):
    """Medida del costo de cada campo (24/11/2026): la «Fecha del último parto» agrupaba TODOS los eventos de parto de la base y en un SQL Express con poca memoria se quedaba
    esperando el permiso de memoria (`RESOURCE_SEMAPHORE`) por minutos: era lo único pesado del catálogo. DelPro ya guarda ese dato por animal en su índice de últimos eventos
    (`Animal.LatestHistoryIndex.Calving`, en el que se basa la definición de DelPro) y coincide con el máximo de los eventos en 1.772 de 1.772 animales de la copia local."""

    def sql(self):
        return im.compilar({"columnas": [{"campo": "Number"}, {"campo": "LastCalvingDate"}]}, herd=1, hoy="2026-07-30")["sql"]

    def test_sale_del_indice_de_delpro(self):
        self.assertIn("AnimalLatestHistoryIndex", self.sql())

    def test_no_agrupa_ni_recorre_los_eventos_de_parto(self):
        sql = self.sql().upper()
        self.assertNotIn("GROUP BY", sql)
        self.assertNotIn("EVENTCALVING", sql)
        self.assertNotIn("MAX(", sql)

    def test_el_campo_sigue_siendo_el_mismo(self):
        c = cat.CATALOGO["LastCalvingDate"]
        self.assertEqual((c.etiqueta, c.tipo, c.verificado), ("Fecha del último parto", "fecha", True))

    def test_se_puede_combinar_con_las_otras_fechas_del_indice(self):
        sql = im.compilar({"columnas": [{"campo": "LastCalvingDate"}, {"campo": "LastInseminationDate"}, {"campo": "LastAbortionDate"}]}, herd=1, hoy="2026-07-30")["sql"]
        self.assertEqual(sql.count("LEFT JOIN AnimalLatestHistoryIndex"), 1)              # el índice se une una sola vez
        db.validate_sql(sql)


class TestLaIANoAvisaDeMasConLosSiNo(unittest.TestCase):
    """«Describí el informe» avisa cuando la frase menciona un valor cerrado («preñadas», «secas») y el informe no tiene nada de ese campo. Con los sí/no nuevos hay otra forma
    de pedir lo mismo (`Preñada = Sí`): eso no es una condición olvidada."""

    def avisos(self, frase, *condiciones):
        import informes_lenguaje as il
        defn = {"columnas": [{"campo": "Number"}], "condiciones": list(condiciones)}
        return il._avisos_de_valores_cerrados(il._normal(frase), defn, cat.CATALOGO)        # la frase llega ya normalizada (sin tildes, en minúsculas), como en `_contra_la_frase`

    def test_preñadas_con_el_campo_si_no_no_es_una_condicion_olvidada(self):
        self.assertEqual(self.avisos("las vacas preñadas del rodeo 3", {"campo": "IsPregnant", "op": "es", "valor": "Sí"}), [])

    def test_secas_con_el_campo_en_secado_tampoco(self):
        self.assertEqual(self.avisos("las vacas secas", {"campo": "IsDryingOff", "op": "es", "valor": "Sí"}), [])

    def test_sin_ninguna_de_las_dos_formas_sigue_avisando(self):
        a = self.avisos("las vacas preñadas del rodeo 3")
        self.assertEqual(len(a), 1)
        self.assertIn("Preñada", a[0])

    def test_con_el_estado_de_la_reproduccion_sigue_sin_avisar(self):
        self.assertEqual(self.avisos("las vacas preñadas", {"campo": "CurrentReproductionStatus", "op": "es", "valor": "Preñada"}), [])

    def test_un_no_o_un_si_suelto_en_la_frase_no_dispara_nada(self):
        self.assertEqual(self.avisos("las vacas que no tienen mastitis, si hay alguna, y no son del rodeo 3"), [])


if __name__ == "__main__":
    unittest.main()
