"""Tanda 3b de campos: lo que quedaba «pesado» o «de significado desconocido» (24/11/2026).

La premisa de la tanda 3a era que estos campos necesitaban una copia diaria en `informes.db` porque «el último evento de un tipo» recorría de 80 a 340 páginas por animal. Se midió de
nuevo con el SQL que arma el motor sobre la base del 20/11/2026: leyendo la tabla de eventos UNA vez (una tabla derivada con `ROW_NUMBER` o `GROUP BY`) son 3.400 páginas y 0,3 s para
TODOS los animales de un informe de 2.000 vacas, así que la copia no hace falta. Y se escriben pidiéndole lo mínimo de memoria al SQL (sin funciones de ventana, un solo agregado por
animal, búsqueda por clave en los eventos): en un SQL con poca memoria de trabajo una consulta que pide más de lo que se le puede dar espera minutos.

Las definiciones salen de `DDMReportItem.Expression`, la fórmula que DelPro guarda para cada campo (ver `CLAUDE.md`, «Informes personalizables»).

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda3b -v
"""
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im

IDENT = ("IsBred", "TransponderID", "TransponderIDNumeric", "CMSTransponderID", "HDXTransponderID", "AgeAtFirstCalving", "LatestPregCheckResult")
SEPARACION = ("LastCutDateTimeSortingConfirmed", "CMSDaysSinceLastSort", "LastCutIdTime")
DIAGNOSTICO = ("DateOfLatestDiagnosisTreatmentEvent", "DaysSinceLatestTreatmentFromTreatmentEvent", "DiagnosisFromDiagnosisTreatmentEvent", "DiagnosisLocationFromDiagnosisTreatmentEvent",
               "LatestDiagnosisCategory", "TreatmentFromDiagnosisTreatmentEvent", "TreatmentStartDate", "TreatmentEndDate", "TreatmentDrugsAndDosage", "TreatmentDaysRemaining")
VACUNA = ("DateOfLatestVaccinationDiagnosisTreatmentEvent", "TreatmentFromVaccinationDiagnosisTreatmentEvent", "DaysSinceLatestVaccinationDiagnosisTreatmentEvent")
SINCRO = ("DateOfTimedAITreatmentEvent", "TreatmentFromTimedAIDiagnosisTreatmentEvent", "DaysSinceLatestTreatmentFromTimedAITreatmentEvent")
OTROS = ("LatestDiagnosisTreatmentEventDate", "MilkingYesterday")
TODOS = IDENT + SEPARACION + DIAGNOSTICO + VACUNA + SINCRO + OTROS


def compilar(campos, **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy="2026-11-20")
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


class TestElCatalogo(unittest.TestCase):
    def test_estan_todos_con_el_nombre_que_les_da_delpro(self):
        for id_ in TODOS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_tipos_y_unidades(self):
        c = cat.CATALOGO
        for id_ in ("LastCutDateTimeSortingConfirmed", "LastCutIdTime", "DateOfLatestDiagnosisTreatmentEvent", "TreatmentStartDate", "TreatmentEndDate", "LatestDiagnosisTreatmentEventDate",
                    "DateOfLatestVaccinationDiagnosisTreatmentEvent", "DateOfTimedAITreatmentEvent"):
            self.assertEqual(c[id_].tipo, "fecha", id_)
        for id_ in ("CMSDaysSinceLastSort", "DaysSinceLatestTreatmentFromTreatmentEvent", "DaysSinceLatestVaccinationDiagnosisTreatmentEvent", "DaysSinceLatestTreatmentFromTimedAITreatmentEvent",
                    "MilkingYesterday", "AgeAtFirstCalving", "TreatmentDaysRemaining"):
            self.assertEqual(c[id_].tipo, "num", id_)
        self.assertEqual((c["AgeAtFirstCalving"].unidad, c["AgeAtFirstCalving"].decimales), ("meses", 0))
        self.assertEqual(c["TreatmentDaysRemaining"].decimales, 1)
        for id_ in ("TransponderID", "TransponderIDNumeric", "CMSTransponderID", "HDXTransponderID", "DiagnosisFromDiagnosisTreatmentEvent", "TreatmentFromDiagnosisTreatmentEvent",
                    "TreatmentDrugsAndDosage", "DiagnosisLocationFromDiagnosisTreatmentEvent", "LatestDiagnosisCategory"):
            self.assertEqual(c[id_].tipo, "txt", id_)

    def test_el_inseminada_sin_prenez_es_un_si_no(self):
        c = cat.CATALOGO["IsBred"]
        self.assertEqual((c.tipo, c.valores, c.agrupable), ("txt", cat.SI_NO, True))

    def test_el_resultado_del_diagnostico_de_gestacion_es_una_lista_cerrada_con_los_nombres_de_delpro(self):
        c = cat.CATALOGO["LatestPregCheckResult"]
        self.assertEqual((c.tipo, c.valores, c.agrupable), ("txt", ("Negativo", "Positivo", "Dudoso"), True))
        self.assertEqual(dict(c.equivale), {"Negative": "Negativo", "Positive": "Positivo", "Uncertain": "Dudoso"})

    def test_el_sexo_se_entiende_con_el_codigo_de_delpro(self):
        self.assertEqual(dict(cat.CATALOGO["Sex"].equivale), {"1": "Macho", "2": "Hembra"})

    def test_las_equivalencias_apuntan_a_valores_que_existen(self):
        for c in cat.CATALOGO.values():
            for _, valor in c.equivale:
                self.assertIn(valor, c.valores, c.id)

    def test_solo_se_agrupa_por_lo_que_tiene_pocas_categorias(self):
        nuevos = {id_ for id_ in TODOS if cat.CATALOGO[id_].agrupable}
        self.assertEqual(nuevos, {"IsBred", "LatestPregCheckResult", "LatestDiagnosisCategory"})

    def test_los_de_salud_y_separacion_tienen_su_categoria(self):
        for id_ in DIAGNOSTICO + VACUNA + SINCRO + ("LatestDiagnosisTreatmentEventDate",):
            self.assertEqual(cat.CATALOGO[id_].categoria, "Salud", id_)
        for id_ in SEPARACION:
            self.assertEqual(cat.CATALOGO[id_].categoria, "Tránsito de animales", id_)

    def test_todos_dicen_de_donde_salen_y_ninguno_se_da_por_comparado_con_delpro(self):
        for id_ in TODOS:
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)

    def test_ninguno_pide_una_tabla_que_no_tengan_todos_los_tambos(self):
        for id_ in TODOS:
            self.assertEqual(cat.CATALOGO[id_].tabla, "", id_)


class TestSeCompilan(unittest.TestCase):
    def test_cada_uno_pasa_la_validacion_de_solo_lectura(self):
        for id_ in TODOS:
            sql = im.compilar({"columnas": [{"campo": "Number"}, {"campo": id_}]}, herd=1, hoy="2026-11-20")["sql"]
            db.validate_sql(sql)
            self.assertNotIn("@HOY", sql, id_)

    def test_en_condiciones_resumenes_y_orden(self):
        db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}, {"campo": "DiagnosisFromDiagnosisTreatmentEvent"}],
                                     "condiciones": [{"campo": "IsBred", "op": "es", "valor": "Sí"}, {"campo": "LatestPregCheckResult", "op": "en", "valor": ["Dudoso", "Negativo"]},
                                                     {"campo": "CMSDaysSinceLastSort", "op": "<=", "valor": 3}, {"campo": "MilkingYesterday", "op": ">=", "valor": 2},
                                                     {"campo": "LatestDiagnosisCategory", "op": "es", "valor": "Probl.Respiratorios"}],
                                     "orden": {"campo": "DiagnosisFromDiagnosisTreatmentEvent", "dir": 1}}, herd=1, hoy="2026-11-20")["sql"])
        im.compilar({"columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "MilkingYesterday", "resumen": "avg"}, {"campo": "AgeAtFirstCalving", "resumen": "avg"},
                                  {"campo": "TreatmentDaysRemaining", "resumen": "max"}], "detalle": False, "agrupar_por": ["LatestPregCheckResult"]}, herd=1, hoy="2026-11-20")

    def test_ninguno_usa_un_apply_por_animal_ni_agrupa_una_tabla_de_eventos_sin_filtrar(self):
        """Regla de costo (ver la tanda 3a): un `OUTER APPLY` por animal sobre una tabla grande hace que SQL Server arme un índice temporal de toda la tabla. Las tablas de eventos se leen UNA
        vez con una tabla derivada, y solo las que son de un tipo (los diagnósticos, la separación)."""
        for id_ in TODOS:
            self.assertNotIn("OUTER APPLY", compilar([id_]), id_)

    def test_el_transponder_no_llama_a_la_funcion_de_delpro(self):
        """`dbo.StringToNumeric` es una función de usuario: el usuario de solo lectura (`db_datareader`) no tiene permiso para ejecutarla. `TRY_CAST` hace lo mismo sin función."""
        for id_ in ("TransponderIDNumeric", "CMSTransponderID"):
            sql = compilar([id_])
            self.assertNotIn("dbo.", sql, id_)
            self.assertIn("TRY_CAST(b.TransponderID AS bigint)", sql, id_)
        self.assertIn("b.TransponderType = 1", compilar(["HDXTransponderID"]))

    def test_la_edad_al_primer_parto_sale_del_inicio_de_la_primera_lactancia(self):
        sql = compilar(["AgeAtFirstCalving"])
        self.assertIn("HistoryAnimalLactationInfo", sql)
        self.assertIn("LactationNumber = 1", sql)

    def test_el_resultado_del_diagnostico_de_gestacion_comparte_el_indice_y_usa_la_regla_del_aborto(self):
        sql = compilar(["LatestPregCheckResult", "LastInseminationDate", "LastAbortionDate", "LastPregnancyCheckDate"])
        self.assertEqual(sql.count("LEFT JOIN AnimalLatestHistoryIndex"), 1)
        self.assertIn("IsGeneratedByNegativePregnancyCheck = 1", sql)
        self.assertIn("EventPregCheck", sql)

    def test_los_tres_de_la_separacion_leen_la_tabla_una_vez_y_sin_ventanas(self):
        sql = compilar(list(SEPARACION))
        self.assertEqual(sql.count("FROM CMSSortResultEvent"), 1)
        self.assertIn("IsAnimalSorted = 1", sql)
        self.assertNotIn("OVER (", sql)
        self.assertNotIn("CONVERT(char", sql)
        self.assertIn("INNER LOOP JOIN AbstractAnimalEvent", sql)

    def test_los_diagnosticos_leen_la_tabla_una_vez_y_con_el_filtro_de_diagnostico_de_delpro(self):
        sql = compilar(list(DIAGNOSTICO))
        self.assertEqual(sql.count("FROM DiagnosisTreatmentEvent"), 1)
        self.assertIn("TreatmentType = 0 OR d.Treatment IS NULL", sql)           # el «diagnóstico» de DelPro: tratamiento de tipo 0 o sin tratamiento
        self.assertIn("GROUP BY a.BasicAnimal", sql)                             # un solo agregado por animal: el máximo de «segundos × 2^31 + número de evento»
        self.assertNotIn("OVER (", sql)                                          # ni funciones de ventana: ordenar 100.000 filas pide mucha memoria
        self.assertNotIn("CONVERT(char", sql)                                    # ni agregados de texto: un MAX de cadena pide mucha más memoria que uno de número o de fecha
        self.assertIn("INNER LOOP JOIN AbstractAnimalEvent", sql)                # búsqueda por clave, sin unión por hash grande
        self.assertIn("TextLookupItem", sql)                                    # los nombres de diagnóstico, ubicación y categoría están en una lista de textos

    def test_la_vacunacion_y_la_sincronizacion_son_su_propia_lectura(self):
        sql = compilar([DIAGNOSTICO[0], VACUNA[0], SINCRO[0]])
        self.assertEqual(sql.count("FROM DiagnosisTreatmentEvent"), 3)
        self.assertIn("t.TreatmentType = 1", sql)
        self.assertIn("t.TreatmentType = 2", sql)
        self.assertEqual(compilar(list(VACUNA)).count("FROM DiagnosisTreatmentEvent"), 1)
        self.assertEqual(compilar(list(SINCRO)).count("FROM DiagnosisTreatmentEvent"), 1)

    def test_los_ordenos_de_ayer_cuentan_las_sesiones_del_dia_de_ayer_del_animal(self):
        sql = compilar(["MilkingYesterday", "YieldYesterday"])
        self.assertIn("SessionMilkYield", sql)
        self.assertIn("TotalYield > 0", sql)
        self.assertIn("ISNULL(", sql)                                           # sin ordeños es 0, como el `Count()` de DelPro
        self.assertEqual(sql.count("LEFT JOIN AnimalDaily ly"), 1)              # comparte el registro de ayer con la leche de ayer

    def test_los_dias_que_quedan_de_tratamiento_cuentan_desde_calcular_al(self):
        self.assertIn("2026-11-20", compilar(["TreatmentDaysRemaining"]))


class TestFormulasDeDelPro(unittest.TestCase):
    """Con la fórmula que DelPro guarda en `DDMReportItem.Expression` a la vista se corrigieron campos de las tandas anteriores que se habían definido «a ojo». Cada prueba nombra la
    diferencia que había."""

    def test_la_edad_deja_de_contar_cuando_el_animal_salio_y_los_meses_son_dias_sobre_30_5(self):
        self.assertIn("ISNULL(b.ExitDate", cat.CATALOGO["AgeInDays"].expr)
        meses = cat.CATALOGO["AgeInMonths"]
        self.assertIn("/ 30.5", meses.expr)
        self.assertIn("ISNULL(b.ExitDate", meses.expr)
        self.assertEqual((meses.unidad, meses.decimales), ("meses", 1))                # DelPro lo muestra con un decimal

    def test_el_parto_esperado_pide_que_el_animal_no_haya_salido(self):
        for id_ in ("ExpectedCalvingDate", "DaysToCalving", "ExpectedDryOffDate", "DaysToDryOff"):
            self.assertIn("b.ExitDate IS NULL", cat.CATALOGO[id_].expr, id_)
        self.assertNotIn("b.ExitDate", cat.CATALOGO["DaysPregnant"].expr)             # los días de preñez, en cambio, no lo piden

    def test_el_secado_esperado_es_de_una_vaca_que_ya_pario_y_todavia_no_se_seco(self):
        for id_ in ("ExpectedDryOffDate", "DaysToDryOff"):
            sql = compilar([id_])
            self.assertIn("r.LactationNumber >= 1", sql, id_)                         # una vaquillona no tiene secado esperado
            self.assertIn("ix.DryOff IS NULL", sql, id_)                              # el índice de DelPro guarda el último secado y lo reinicia con cada parto
        self.assertEqual(compilar(["ExpectedDryOffDate", "LastInseminationDate", "ExpectedCalvingDate"]).count("LEFT JOIN AnimalLatestHistoryIndex"), 1)

    def test_el_ultimo_diagnostico_de_gestacion_queda_vacio_si_hay_un_aborto_despues(self):
        sql = compilar(["LastPregnancyCheckDate", "LastAbortionDate"])
        self.assertIn("ea.DateAndTime IS NULL OR ep.DateAndTime > ea.DateAndTime", sql)
        self.assertEqual(sql.count("LEFT JOIN AnimalLatestHistoryIndex"), 1)

    def test_grasa_proteina_y_condicion_corporal_se_muestran_con_un_decimal_como_delpro(self):
        for id_ in ("LatestFat", "LatestProtein", "LastBcsValue", "LastBcsDryOffValue", "LastBcsCalvingValue", "LastBcsValue190DaysPregnant"):
            self.assertEqual(cat.CATALOGO[id_].decimales, 1, id_)


def filtro(texto, maximo=10):
    return dl.condiciones_de(texto, dl.resolver_por_nombre(cat.CATALOGO), cat.CATALOGO, maximo)


class TestElImportadorLosEntiende(unittest.TestCase):
    def test_el_sexo_con_el_codigo_de_delpro(self):
        cs, pend = filtro("[Sex] = '2' And ToBeCulled = False")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "Sex", "op": "es", "valor": "Hembra"}, {"campo": "ToBeCulled", "op": "es", "valor": "No"}])
        cs, pend = filtro("[Sex] = '1'")
        self.assertEqual(cs, [{"campo": "Sex", "op": "es", "valor": "Macho"}])

    def test_un_codigo_que_no_existe_sigue_siendo_un_pendiente(self):
        cs, pend = filtro("[Sex] = '3'")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_el_resultado_del_diagnostico_con_los_nombres_en_ingles_de_delpro(self):
        cs, pend = filtro("[<AnimalExtended>LatestPregCheckResult] In ('Uncertain', 'Negative')")
        self.assertEqual(pend, [])
        self.assertEqual(cs, [{"campo": "LatestPregCheckResult", "op": "en", "valor": ["Dudoso", "Negativo"]}])
        cs, pend = filtro("LatestPregCheckResult = 'Positive'")
        self.assertEqual(cs, [{"campo": "LatestPregCheckResult", "op": "es", "valor": "Positivo"}])

    def test_el_nombre_ya_traducido_tambien_se_acepta_y_lo_inventado_no(self):
        cs, _ = filtro("LatestPregCheckResult = 'negativo'")
        self.assertEqual(cs, [{"campo": "LatestPregCheckResult", "op": "es", "valor": "Negativo"}])
        cs, pend = filtro("LatestPregCheckResult = 'Maybe'")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_inseminada_sin_prenez_con_la_ruta_de_la_reproduccion(self):
        cs, pend = filtro("ToBeCulled = False And ReproductionInfo.IsBred = True And ReproductionInfo.DaysSinceLastInsemination >= 30")
        self.assertEqual(pend, [])
        self.assertEqual(cs[:2], [{"campo": "ToBeCulled", "op": "es", "valor": "No"}, {"campo": "IsBred", "op": "es", "valor": "Sí"}])
        self.assertEqual(dl.resolver_por_nombre(cat.CATALOGO)("ReproductionInfo.IsBred"), ("IsBred", "IsBred"))

    def test_la_fecha_de_la_ultima_separacion_se_compara_con_la_funcion_de_delpro(self):
        cs, pend = filtro("LastCutDateTimeSortingConfirmed >= DDMToday()")
        self.assertEqual(len(cs) + len(pend), 1)                               # o se trae o queda como pendiente con el motivo: nunca se pierde en silencio


if __name__ == "__main__":
    unittest.main()
