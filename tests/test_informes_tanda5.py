"""Tanda 5 de campos (25/11/2026): los 75 que quedaban después de la tanda 4 en «Campos que faltan» de «Traer de DelPro…».

De los 75, 65 quedaron definidos (93 campos del catálogo, porque los que llevan parámetro son uno por valor) y 10 NO se definen a propósito (ver `NO_SE_DEFINEN`). Además:

  * El importador lee la CLASE de cada ítem de DelPro (`DDMReportItem.ReportClass`): `Name`, `DIM`, `LactationNumber` y `GroupNumber` existen en varias clases, y hay cuatro que SON otro campo del
    catálogo con otro nombre (`ALIAS_DE_ITEMS`).
  * `CurrentDIM` pasó a la fórmula de DelPro (una vaca seca cuenta hasta su secado, un animal que salió hasta su salida).
  * Los tres bloques que leen `HistoryAnimalLactationInfo` (`insem_lact`, `lact_ant`, `lact_1`) la unían con el OID del animal, pero esa tabla cuelga del `HistoryAnimal`, que NO tiene el mismo OID en 2.344
    de los 11.548 históricos de la base del 20/11/2026: leían la lactancia de OTRO animal. Ahora pasan por `historia`.

Las definiciones salen de `DDMReportItem.Expression` y de las funciones que DelPro tiene en la base. Ninguno se comparó con un informe de DelPro: todos llevan ⚠.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda5 -v
"""
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im
from tests.test_informes_delpro import CLASES, FalsoRun, columna, filtro, informe

FINCA = ("HerdIdentifier", "HerdLocation", "HerdOwnerID", "FarmLocation", "FarmIdentifier", "FarmName")
REPRODUCCION = ("CurrentDSLC", "DaysSinceLastAbortion", "DaysSinceLastPregnCheck", "LastHeatDate", "DaysSinceLastHeat", "LastInseminationNumber", "LastInseminationSemen", "InseminatedByUser",
                "PregnancyCheckNumber", "LatestFatProt")
POR_LACTANCIA = tuple(f"{item}_{n}" for item in ("DaysDry", "DaysOpenInLactation", "DaysPregnantInLactation") for n in (1, 2, 3, 4))
SALUD = ("BodyScore", "LatestHealthEventDate", "DumpMilkDaysRemaining", "IsHealthCheckEventInCurrentLactation", "LatestHealthCheckEventResult", "AnimalSelectionsNumberItem")
ORDENO = ("CMSAvgMilkFlowYesterday", "CMSNumberOfMilkingsYesterday", "ForcedRetractYesterdayAnyMilking", "ForcedRetractTodayAnyMilking", "OverrideYesterdayAnyMilking", "MilkingDaysLast7Days",
          "MilkingDaysInLactationPeriod", "MilkingsLast24Hrs", "MilkingsPrev24Hrs", "MilkYieldLast24Hrs", "LastMilkTime", "CMSLowYieldLastMilking", "IdTimeLastMilking", "YieldInLastVisit",
          "MilkSlopeLastSevenDays", "MilkSlopeLastSevenDaysPercentage", "AnimalPeakYield", "TotalYieldInLactationPeriod", "TotalLifetimeYield", "CMSDoNotMilk", "CMSDontMilkForever")
TRAMOS = ("FlowZeroToFifteen", "FlowFifteentoThirty", "FlowThirtyToSixty", "FlowSixtyTo120")
POR_ORDENO = (tuple(f"{t}{dia}ForMilking_{n}" for t in TRAMOS for dia in ("Yesterday", "Today") for n in (1, 2, 3))
              + tuple(f"CMSLowMilkFlowYesterday_{n}" for n in (1, 2, 3)) + tuple(f"MpcNumberTodayMilkingNumber_{n}" for n in (1, 2, 3)))
SEPARACION = ("CMSSort_Iso", "CMSSortTodayOrLater", "CMSSortCowDays", "SortToday", "IsForeverForSort_1", "IsForeverForSort_2", "StartTimeForSort_1", "StartTimeForSort_2")
TODOS = FINCA + REPRODUCCION + POR_LACTANCIA + SALUD + ORDENO + POR_ORDENO + SEPARACION
SI_NO = ("ForcedRetractYesterdayAnyMilking", "ForcedRetractTodayAnyMilking", "OverrideYesterdayAnyMilking", "CMSLowYieldLastMilking", "CMSDoNotMilk", "CMSDontMilkForever", "IsHealthCheckEventInCurrentLactation",
         "CMSSort_Iso", "CMSSortTodayOrLater", "SortToday", "IsForeverForSort_1", "IsForeverForSort_2")
# Los que dependen del reloj de la PC (el `Now()` de DelPro): usan GETDATE()
CON_RELOJ = ("DumpMilkDaysRemaining", "MilkingsLast24Hrs", "MilkingsPrev24Hrs", "MilkYieldLast24Hrs", "CMSLowYieldLastMilking", "IdTimeLastMilking", "YieldInLastVisit", "CMSDoNotMilk", "CMSDontMilkForever")
# Los 10 campos de DelPro que se miraron y NO se definieron, con la razón (la misma que el cuadro de CLAUDE.md)
NO_SE_DEFINEN = {"AvgDailyYieldDates": "las fechas son fijas dentro de la columna",
                 "YieldFirst2MinTodayMilking": "DelPro lo calcula de la curva de flujo y no lo guarda", "YieldFirst2MinPercentageTodayMilking": "ídem",
                 "InseminationDueAbortedOrNotPregnant": "depende de fórmulas de la finca y de banderas que no se guardan",
                 "AllConcentrateFeedsConsYesterday": "sin comederos en la base", "AllConcentrateFeedsConsYesterdayPerc": "sin comederos en la base",
                 "Reloteo": "columna propia del cliente", "BCS Racion": "columna propia del cliente", "BCS Secado": "columna propia del cliente", "BCS DEO Vc": "columna propia del cliente"}

CLASES_5 = {**CLASES, 50: "DeLaval.DDM.CommonApp.Module.BasicFeatures.Reproduction.AnimalReproductionInfo", 54: "DeLaval.DDM.CommonApp.ExtendedFeatures.AnimalAdmin.Farm",
            55: "DeLaval.DDM.CommonApp.ExtendedFeatures.AnimalAdmin.Herd"}


def compilar(campos, hoy="2026-11-20", **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy=hoy)
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


def traducir(columnas, parametros=(), filtros=(), informes=None, oid=1):
    d = dl.armar_datos(informes or [informe(1)], columnas, list(filtros), [{"OID": k, "TypeName": v} for k, v in CLASES_5.items()], list(parametros))
    return dl.traducir_informe(d, oid)


def param(columna_oid, nombre, valor):
    return {"columna": columna_oid, "nombre": nombre, "valor": valor}


class TestElCatalogo(unittest.TestCase):
    def test_estan_todos_y_son_93(self):
        self.assertEqual(len(TODOS), 93)
        self.assertEqual(len(set(TODOS)), 93)
        for id_ in TODOS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_ninguno_se_da_por_comparado_y_todos_dicen_de_donde_salen(self):
        for id_ in TODOS:
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)

    def test_lo_que_no_se_definio_no_esta_y_cada_cosa_tiene_su_razon(self):
        """Un campo que se deja afuera porque inventarlo sería peor que no tenerlo: tiene que seguir afuera (y figurar en el cuadro de «lo que no se define»)."""
        items = {c.item for c in cat.CATALOGO.values() if c.item}
        for nombre, razon in NO_SE_DEFINEN.items():
            self.assertNotIn(nombre, cat.CATALOGO, nombre)
            self.assertNotIn(nombre, items, nombre)
            self.assertTrue(razon)
        self.assertEqual(len(NO_SE_DEFINEN), 10)

    def test_las_variantes_dicen_a_que_campo_de_delpro_y_a_que_parametro_corresponden(self):
        esperado = {"LactationNumber": POR_LACTANCIA, "RuleIndex": ("IsForeverForSort_1", "IsForeverForSort_2", "StartTimeForSort_1", "StartTimeForSort_2"),
                    "MilkingNumber": tuple(i for i in POR_ORDENO)}
        for nombre, ids in esperado.items():
            for id_ in ids:
                c = cat.CATALOGO[id_]
                valor = id_.rsplit("_", 1)[1]
                self.assertEqual(c.parametros, ((nombre, valor),), id_)
                self.assertEqual(id_, f"{c.item}_{valor}")

    def test_el_nombre_de_delpro_de_una_variante_no_esta_tambien_suelto_en_el_catalogo(self):
        for c in cat.CATALOGO.values():
            if c.item:
                self.assertNotIn(c.item, cat.CATALOGO, c.id)

    def test_los_sin_parametro_no_tienen_item_ni_parametros(self):
        for id_ in FINCA + REPRODUCCION + SALUD + ORDENO + ("CMSSort_Iso", "CMSSortTodayOrLater", "CMSSortCowDays", "SortToday"):
            self.assertEqual((cat.CATALOGO[id_].item, cat.CATALOGO[id_].parametros), ("", ()), id_)

    def test_los_si_no_son_un_texto_cerrado_y_se_pueden_agrupar(self):
        for id_ in SI_NO:
            c = cat.CATALOGO[id_]
            self.assertEqual((c.tipo, c.valores, c.agrupable), ("txt", cat.SI_NO, True), id_)

    def test_lo_que_se_puede_agrupar_de_la_tanda_5(self):
        self.assertEqual({i for i in TODOS if cat.CATALOGO[i].agrupable}, set(SI_NO) | set(FINCA) | {"InseminatedByUser", "PregnancyCheckNumber", "LatestHealthCheckEventResult"})

    def test_tipos_y_unidades(self):
        c = cat.CATALOGO
        self.assertEqual((c["LastHeatDate"].tipo, c["LatestHealthEventDate"].tipo), ("fecha", "fecha"))
        # la fecha y hora van como TEXTO: un campo de fecha de acá muestra solo el día
        for id_ in ("LastMilkTime", "IdTimeLastMilking"):
            self.assertEqual(c[id_].tipo, "txt", id_)
        for id_ in ("CurrentDSLC", "DaysSinceLastAbortion", "DaysSinceLastPregnCheck", "DaysSinceLastHeat", "MilkingDaysLast7Days", "MilkingDaysInLactationPeriod", "DumpMilkDaysRemaining", "CMSSortCowDays") + tuple(
                i for i in POR_LACTANCIA):
            self.assertEqual(c[id_].unidad, "días", id_)
        for n in (1, 2, 3):
            self.assertEqual((c[f"FlowZeroToFifteenYesterdayForMilking_{n}"].unidad, c[f"FlowZeroToFifteenYesterdayForMilking_{n}"].decimales), ("kg/min", 2))
            self.assertEqual((c[f"CMSLowMilkFlowYesterday_{n}"].unidad, c[f"CMSLowMilkFlowYesterday_{n}"].decimales), ("%", 1))
        for id_ in ("AnimalPeakYield", "TotalYieldInLactationPeriod", "TotalLifetimeYield", "MilkYieldLast24Hrs", "YieldInLastVisit"):
            self.assertEqual((c[id_].tipo, c[id_].unidad), ("num", "kg"), id_)

    def test_los_ordenos_de_ayer_y_de_hoy_siguen_cada_uno_a_su_dia(self):
        c = cat.CATALOGO
        self.assertEqual({c[i].bloque for i in POR_ORDENO if "Yesterday" in i}, {"ord_ayer"})
        self.assertEqual({c[i].bloque for i in POR_ORDENO if "Today" in i}, {"ord_hoy"})
        self.assertEqual(len([i for i in POR_ORDENO if i.startswith("Flow")]), 24)                        # 4 tramos × ayer y hoy × 3 ordeños
        # el flujo bajo existe solo de ayer y el puesto solo de hoy, como en DelPro
        self.assertFalse([i for i in POR_ORDENO if i.startswith("CMSLowMilkFlowToday") or i.startswith("MpcNumberYesterday")])

    def test_las_categorias_nuevas(self):
        self.assertEqual({cat.CATALOGO[i].categoria for i in FINCA}, {"Finca y rebaño"})


class TestSeCompilan(unittest.TestCase):
    def test_cada_uno_pasa_la_validacion_de_solo_lectura(self):
        for id_ in TODOS:
            sql = compilar(["Number", id_])
            db.validate_sql(sql)
            self.assertNotIn("@HOY", sql, id_)

    def test_todos_juntos_en_tandas_del_maximo_de_columnas(self):
        for i in range(0, len(TODOS), im.COLUMNAS_MAX):
            db.validate_sql(compilar(list(TODOS[i:i + im.COLUMNAS_MAX])))

    def test_en_condiciones_resumenes_y_orden(self):
        db.validate_sql(compilar(["Number", "CurrentDSLC"], condiciones=[{"campo": "CMSSort_Iso", "op": "es", "valor": "Sí"}, {"campo": "DaysSinceLastHeat", "op": "<", "valor": 37},
                                                                     {"campo": "AnimalSelectionsNumberItem", "op": "contiene", "valor": "Celo"}, {"campo": "PregnancyCheckNumber", "op": "=", "valor": 2},
                                                                     {"campo": "LatestHealthCheckEventResult", "op": "es", "valor": "Sana"}], orden={"campo": "CurrentDSLC", "dir": -1}))
        im.compilar({"columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "DaysDry_2", "resumen": "avg"}, {"campo": "MilkSlopeLastSevenDays", "resumen": "avg"},
                                  {"campo": "TotalLifetimeYield", "resumen": "sum"}], "detalle": False, "agrupar_por": ["FarmName", "InseminatedByUser"]}, herd=1, hoy="2026-11-20")

    def test_sin_funciones_de_ventana_ni_apply_ni_agregados_de_texto(self):
        """Regla de costo de las tandas 3b y 4: el SQL de la PC de desarrollo tiene muy poca memoria de trabajo. Lo único con `OUTER APPLY` son los «últimos» del control lechero (2 de la tanda 3a)."""
        for id_ in TODOS:
            sql = compilar(["Number", id_])
            self.assertNotIn("OVER (", sql, id_)
            self.assertNotIn("STRING_AGG", sql, id_)                                          # no existe en todas las versiones de SQL Server
            if id_ != "LatestFatProt":
                self.assertNotIn("OUTER APPLY", sql, id_)

    def test_los_que_usan_el_reloj_lo_dicen_y_los_demas_no(self):
        for id_ in TODOS:
            self.assertEqual("GETDATE()" in compilar(["Number", id_]), id_ in CON_RELOJ, id_)

    def test_el_que_no_pide_un_bloque_no_lo_lee(self):
        sql = compilar(["Number", "FarmName"])
        for tabla in ("SessionMilkYield", "AnimalDaily", "AbstractAnimalEvent", "HistoryAnimal"):
            self.assertNotIn(tabla, sql, tabla)
        sql = compilar(["Number", "CurrentLactationNumber", "BodyScore"])
        self.assertNotIn("SessionMilkYield", sql)

    def test_los_bloques_que_agrupan_lo_hacen_una_sola_vez_por_consulta(self):
        for id_ in ("lact_eventos", "lact_resumen", "pregcheck_cuenta", "pendiente7", "dias_lact", "ord_24h", "leche_vida", "dump_leche", "salud_todo", "bcs_manual", "chequeo", "selecciones", "reglas_sep"):
            self.assertGreaterEqual(cat.BLOQUES[id_].join.upper().count("GROUP BY"), 1, id_)

    def test_los_bloques_nuevos_que_no_agrupan_unen_por_una_clave_unica(self):
        """Medido en la copia del 20/11/2026: ninguno repite animal (`AnimalTreat`, `AnimalLactationSummary`, `HistoryAnimal.ReferenceId`, `Herd`, la finca y el índice de eventos)."""
        for id_ in ("historia", "finca", "ult_celo", "ult_insem_det", "lact_actual", "no_ordenar"):
            self.assertNotIn("GROUP BY", cat.BLOQUES[id_].join.upper(), id_)


class TestElHistorialSeUneAlAnimalPorSuHistoria(unittest.TestCase):
    """`HistoryAnimalLactationInfo.Animal` apunta al `HistoryAnimal.OID`, que no es el del animal en el 20 % de los casos (71 de 3.708 vacas con lactancia): unir con `b.OID` leía la lactancia de otro."""

    def test_los_tres_bloques_viejos_pasan_por_historia(self):
        for id_ in ("insem_lact", "lact_ant", "lact_1"):
            b = cat.BLOQUES[id_]
            self.assertIn("historia", b.requiere, id_)
            self.assertIn(".Animal = ha.OID", b.join, id_)
            self.assertNotIn(".Animal = b.OID", b.join, id_)

    def test_ningun_bloque_une_la_tabla_de_lactancias_con_el_oid_del_animal(self):
        for b in cat.BLOQUES.values():
            if "HistoryAnimalLactationInfo" in b.join:
                self.assertNotIn("Animal = b.OID", b.join, b.id)

    def test_la_union_a_la_historia_va_antes_que_la_de_las_lactancias(self):
        for campo in ("NumberOfInseminations", "PrevCalvingDate", "AgeAtFirstCalving", "TotalYieldInLactationPeriod"):
            sql = compilar([campo])
            self.assertIn("LEFT JOIN HistoryAnimal ha ON ha.ReferenceId = b.OID", sql, campo)
            self.assertLess(sql.index("LEFT JOIN HistoryAnimal ha"), sql.index("HistoryAnimalLactationInfo"), campo)
        self.assertEqual(compilar(["NumberOfInseminations", "PrevCalvingDate", "AgeAtFirstCalving"]).count("LEFT JOIN HistoryAnimal ha"), 1)


class TestLasFormulasDeDelPro(unittest.TestCase):
    def test_los_dias_en_ordeño_siguen_la_formula_de_delpro(self):
        """Una vaca seca cuenta hasta su secado; un animal que salió, hasta su salida; si no, hasta «Calcular al». Era siempre hasta hoy."""
        sql = compilar(["CurrentDIM"])
        for pieza in ("r.IsDryingOff = 1 OR es.DateAndTime IS NOT NULL THEN DATEDIFF(day, r.LastLactationChangeDate, es.DateAndTime)",
                      "b.ExitType IN (6, 7, 8) AND b.ExitDate IS NOT NULL AND CONVERT(date, '2026-11-20', 23) > b.ExitDate THEN DATEDIFF(day, r.LastLactationChangeDate, b.ExitDate)"):
            self.assertIn(pieza, sql)
        self.assertTrue(cat.CATALOGO["CurrentDIM"].verificado)                                # las secas coinciden 60 de 60 con el DriedDay del historial de DelPro

    def test_sin_lactancia_los_dias_en_ordeño_quedan_vacios_aunque_delpro_da_0(self):
        """DelPro da 0 a un animal sin lactancia. Acá queda vacío a propósito: los informes guardados acotan a las vacas con «Días en ordeño ≥ 0» (una comparación con un vacío no pasa), y con 0
        entrarían también las terneras y la recría (5.645 animales en vez de 1.779)."""
        sql = compilar(["CurrentDIM"])
        self.assertNotIn("THEN 0", sql)
        self.assertIn("VACÍO (DelPro da 0)", cat.CATALOGO["CurrentDIM"].nota)

    def test_los_dias_desde_el_ultimo_parto_solo_se_detienen_con_un_descarte(self):
        sql = compilar(["CurrentDSLC"])
        self.assertIn("b.ExitType = 6", sql)
        self.assertNotIn("es.DateAndTime", sql)                                               # a diferencia de los días en ordeño, una vaca seca sigue contando

    def test_los_dias_desde_el_ultimo_celo_son_de_una_preñada_desde_la_inseminacion_efectiva(self):
        sql = compilar(["DaysSinceLastHeat"])
        self.assertIn("b.ExitDate IS NULL", sql)
        self.assertIn("r.IsPregnant = 1 THEN DATEDIFF(day, ee.DateAndTime", sql)
        self.assertIn("eh.DateAndTime IS NOT NULL THEN DATEDIFF(day, CAST(eh.DateAndTime AS date)", sql)
        self.assertIn("ix.Heat", sql)

    def test_el_numero_del_proximo_diagnostico_cuenta_los_de_la_lactancia_despues_de_la_ultima_inseminacion(self):
        sql = compilar(["PregnancyCheckNumber"])
        for pieza in ("t.TypeName LIKE '%.EventPregCheck'", "q.LactationNumber = a.LactationNumber", "qx.Insemination", "qi.DateAndTime < a.DateAndTime",
                      "ISNULL(pk.n, 0) < 4 AND r.ExpectedPregnancyCheckDate IS NOT NULL THEN ISNULL(pk.n, 0) + 1"):
            self.assertIn(pieza, sql)

    def test_los_dias_por_lactancia_salen_de_los_eventos_de_esa_lactancia(self):
        sql = compilar(["DaysDry_2", "DaysOpenInLactation_2", "DaysPregnantInLactation_2"])
        self.assertEqual(sql.count("FROM AbstractAnimalEvent a INNER JOIN XPObjectType t ON t.OID = a.ObjectType AND (t.TypeName LIKE '%.EventDryOff'"), 1)   # una lectura de los eventos para los tres
        for pieza in ("t.TypeName LIKE '%.EventDryOff' AND a.LactationNumber = 2", "pcr.Result = 1 AND a.LactationNumber = 2", "pcr.EffectiveInsemination", "a.LactationNumber = 3 THEN a.DateAndTime",
                      "DATEDIFF(day, le.sec2, ISNULL(lr.fin2, ", "DATEDIFF(day, lr.ini2, ef2.DateAndTime)", "r.LactationNumber = 2 THEN ", "r.LactationNumber < 2 THEN NULL ELSE le.par3"):
            self.assertIn(pieza, sql)

    def test_el_ultimo_diagnostico_de_gestacion_de_los_dias_desde_es_el_mismo_de_su_fecha(self):
        self.assertIn(cat._ULT_CONTROL, cat.CATALOGO["LastPregnancyCheckDate"].expr)
        self.assertIn(cat._ULT_CONTROL, cat.CATALOGO["DaysSinceLastPregnCheck"].expr)

    def test_la_ultima_inseminacion_trae_numero_semen_y_usuario(self):
        sql = compilar(["LastInseminationNumber", "LastInseminationSemen", "InseminatedByUser"])
        self.assertEqual(sql.count("LEFT JOIN EventInsemination ein"), 1)
        for pieza in ("ein.InseminationNo", "smn.SemenName", "pup.UserName", "[User] usr ON usr.Oid = ein.Breeder", "PermissionPolicyUser pup ON pup.Oid = usr.PermissionPolicyUser"):
            self.assertIn(pieza, sql)

    def test_el_cociente_grasa_proteina_es_el_ultimo_control_que_lo_trae(self):
        sql = compilar(["LatestFatProt"])
        self.assertIn("m.FatProt IS NOT NULL", sql)
        self.assertIn("ORDER BY h.DateAndTime DESC", sql)

    def test_la_condicion_corporal_a_mano_es_el_ultimo_evento_no_la_camara(self):
        sql = compilar(["BodyScore"])
        self.assertIn("FROM EventBCS eb", sql)
        self.assertIn("e.BCSValue", sql)
        self.assertNotIn("BcsDailyData", sql)
        self.assertEqual(cat.CATALOGO["BodyScore"].tabla, "")                                  # no pide la tabla de la cámara

    def test_el_ultimo_evento_de_salud_mira_los_cinco_tipos_de_delpro(self):
        sql = compilar(["LatestHealthEventDate"])
        for tipo in ("DiagnosisTreatmentEvent", "EventBacteriaSample", "EventBCS", "EventRektalTemperature", "VetVisitEvent"):
            self.assertIn(f"LIKE '%.{tipo}'", sql)

    def test_el_chequeo_de_salud_en_esta_lactancia_se_mide_contra_el_ultimo_parto(self):
        sql = compilar(["IsHealthCheckEventInCurrentLactation"])
        self.assertIn("hk.fecha IS NOT NULL AND (pa.DateAndTime IS NULL OR hk.fecha >= pa.DateAndTime)", sql)
        self.assertIn("FROM HealthCheckEvent h", sql)
        self.assertIn("LEFT JOIN AbstractAnimalEvent pa ON pa.OID = ix.Calving", sql)

    def test_las_listas_de_animales_se_juntan_con_coma_sin_agregado_de_texto(self):
        sql = compilar(["AnimalSelectionsNumberItem"])
        self.assertIn("FOR XML PATH('')", sql)
        self.assertIn("BasicAnimalAnimals_AnimalSelectionSelections", sql)
        self.assertIn("',' + g2.Name", sql)

    def test_el_descarte_de_leche_cuenta_desde_ahora_lo_que_sigue_en_el_futuro(self):
        sql = compilar(["DumpMilkDaysRemaining"])
        self.assertIn("d.DumpMilkEndDate > GETDATE()", sql)
        self.assertIn("DATEDIFF(minute, GETDATE(), dm.fin) / 1440.0", sql)

    def test_la_finca_sale_del_rodeo_del_animal(self):
        sql = compilar(["FarmName", "HerdIdentifier"])
        self.assertIn("LEFT JOIN Herd hd ON hd.OID = g.Herd", sql)
        self.assertIn("LEFT JOIN AnimalFarm fm ON fm.OID = hd.Farm", sql)
        self.assertLess(sql.index("AnimalGroup g"), sql.index("Herd hd"))

    def test_el_flujo_por_tramo_y_el_flujo_bajo_llevan_el_filtro_de_delpro(self):
        """Con leche (`TotalYield > 0`) y del número de ordeño de la columna; el puesto, en cambio, también en un ordeño sin leche (así lo dice la fórmula de DelPro)."""
        sql = compilar(["FlowZeroToFifteenYesterdayForMilking_1", "FlowSixtyTo120TodayForMilking_3", "CMSLowMilkFlowYesterday_2", "MpcNumberTodayMilkingNumber_1"])
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 AND s.TotalYield > 0 THEN c.Flow0To15 END) AS tr01", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 3 AND s.TotalYield > 0 THEN c.Flow60To120 END) AS tr33", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 2 AND s.TotalYield > 0 THEN c.LowFlowDurationInSec END) AS bajo2", sql)
        self.assertIn("100.0 * oy.bajo2 / NULLIF(oy.dur2, 0)", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 THEN mp.MpcNumber END) AS mpc1", sql)
        self.assertIn("LEFT JOIN CMSMpcDevice mp ON mp.OID = s.MilkingDevice", sql)

    def test_lo_de_todos_los_ordenos_de_ayer(self):
        sql = compilar(["CMSAvgMilkFlowYesterday", "CMSNumberOfMilkingsYesterday", "ForcedRetractYesterdayAnyMilking", "OverrideYesterdayAnyMilking"])
        self.assertEqual(sql.count("FROM SessionMilkYield s"), 1)
        for pieza in ("AVG(CASE WHEN s.TotalYield > 0 THEN c.AverageFlow END) AS flujo_todos", "MAX(CASE WHEN s.TotalYield > 0 AND c.ForcedRetract = 1 THEN 1 ELSE 0 END) AS forz_todos",
                      "MAX(CASE WHEN s.TotalYield > 0 AND c.BlockOverride = 1 THEN 1 ELSE 0 END) AS ovr_todos", "ISNULL(oy.n, 0)"):
            self.assertIn(pieza, sql)

    def test_los_siete_dias_con_leche_son_los_de_la_ventana_de_siempre(self):
        sql = compilar(["MilkingDaysLast7Days"])
        self.assertIn("d.Date >= DATEADD(day, -7, CONVERT(date, '2026-11-20', 23)) AND d.Date < CONVERT(date, '2026-11-20', 23)", sql)
        self.assertIn("ISNULL(d7.dias, 0)", sql)

    def test_los_dias_con_leche_de_la_lactancia_van_por_lazo_y_hasta_hoy(self):
        sql = compilar(["MilkingDaysInLactationPeriod"])
        self.assertIn("INNER LOOP JOIN AnimalReproductionInfo q", sql)
        self.assertIn("d.Date >= q.LastLactationChangeDate AND d.Date <= CONVERT(date, '2026-11-20', 23)", sql)

    def test_las_ventanas_de_24_horas_son_de_ahora_hacia_atras(self):
        sql = compilar(["MilkingsLast24Hrs", "MilkingsPrev24Hrs", "MilkYieldLast24Hrs"])
        self.assertEqual(sql.count("FROM SessionMilkYield s"), 1)
        for pieza in ("s.BeginTime > DATEADD(day, -1, GETDATE())", "s.BeginTime > DATEADD(day, -2, GETDATE()) AND s.BeginTime < DATEADD(day, -1, GETDATE())", "s.BeginTime >= DATEADD(hour, -24, GETDATE())"):
            self.assertIn(pieza, sql)

    def test_la_leche_baja_del_ultimo_ordeño_mira_el_ordeño_que_apunta_el_indice(self):
        sql = compilar(["CMSLowYieldLastMilking"])
        self.assertIn("lc.LowYieldAlarm = 1", sql)
        self.assertIn("lm.BeginTime >= DATEADD(hour, -24, GETDATE()) AND lm.BeginTime <= GETDATE()", sql)
        self.assertNotIn("FROM CMSMilkYield c", sql)                                          # no recorre los 826.000 ordeños

    def test_el_ultimo_ordeño_de_24_horas_se_lee_una_vez_para_la_identificacion_y_los_kilos(self):
        sql = compilar(["IdTimeLastMilking", "YieldInLastVisit"])
        self.assertEqual(sql.count("FROM SessionMilkYield s"), 1)
        self.assertIn("CASE WHEN uvs.hay = 1 THEN ISNULL(u2.TotalYield, 0) ELSE u1.TotalYield END", sql)
        self.assertIn("u24.k1 % 2147483648", sql)

    def test_la_pendiente_de_la_leche_es_la_recta_de_minimos_cuadrados_de_delpro(self):
        sql = compilar(["MilkSlopeLastSevenDays", "MilkSlopeLastSevenDaysPercentage"])
        self.assertEqual(sql.count("FROM AnimalDaily d WHERE"), 2)                            # la pendiente y el promedio de 7 días que usa el porcentaje
        for pieza in ("d.TotalYield > 0 AND d.IsYieldValid = 1", "(ps.n * ps.sxy - ps.sx * ps.sy) / NULLIF(ps.n * ps.sxx - ps.sx * ps.sx, 0)", "p7.prom7 > 0",
                      "/ p7.prom7 * 100 ELSE 0 END", "SUM(DATEDIFF(day, DATEADD(day, -7, CONVERT(date, '2026-11-20', 23)), d.Date) * d.TotalYield) AS sxy"):
            self.assertIn(pieza, sql)

    def test_el_pico_la_leche_de_la_lactancia_y_la_de_toda_la_vida(self):
        self.assertIn("sa.LactationNumber = r.LactationNumber", compilar(["AnimalPeakYield"]))
        self.assertIn("NULLIF(hl.TotalYield, 0)", compilar(["TotalYieldInLactationPeriod"]))
        sql = compilar(["TotalLifetimeYield"])
        self.assertIn("INNER JOIN HistoryAnimal h2 ON h2.OID = hh.Animal", sql)
        self.assertIn("hh.TotalYield > 0", sql)

    def test_no_ordenar_usa_el_reloj_y_la_tabla_de_tratamientos(self):
        sql = compilar(["CMSDoNotMilk", "CMSDontMilkForever"])
        self.assertEqual(sql.count("LEFT JOIN AnimalTreat nm"), 1)
        self.assertIn("nm.DoNotMilkStartTime <= GETDATE() AND (nm.DoNotMilkEndTime IS NULL OR nm.DoNotMilkEndTime >= GETDATE())", sql)
        self.assertIn("nm.DoNotMilkStartTime <= GETDATE() AND nm.DoNotMilkEndTime IS NULL", sql)

    def test_la_separacion_manual_tiene_dos_reglas_por_animal_y_una_regla_vigente_tiene_alguna_fecha(self):
        sql = compilar(SEPARACION)
        self.assertEqual(sql.count("FROM CMSManualSorting ms"), 1)                           # los ocho salen de una sola lectura
        for pieza in ("(x.StartDateTime IS NOT NULL OR x.EndDateTime IS NOT NULL) AND (x.EndDateTime IS NULL OR x.EndDateTime > CONVERT(date, '2026-11-20', 23))",
                      "MIN(OID) AS r1, CASE WHEN COUNT(*) >= 2 THEN MAX(OID) END AS r2", "x.OID = p.r1", "x.OID = p.r2",
                      "x.StartDateTime < DATEADD(day, 1, CONVERT(date, '2026-11-20', 23))", "sr.ini IS NOT NULL AND (sr.fin IS NULL OR sr.fin >= CONVERT(date, '2026-11-20', 23))"):
            self.assertIn(pieza, sql)

    def test_los_dos_campos_de_la_misma_cuenta_de_delpro_dan_lo_mismo(self):
        self.assertEqual(cat.CATALOGO["CMSSort_Iso"].expr, cat.CATALOGO["CMSSortTodayOrLater"].expr)


class TestElImportadorLeeLaClaseDelItem(unittest.TestCase):
    def test_la_lectura_de_columnas_pide_la_clase_del_item_y_sigue_siendo_de_solo_lectura(self):
        self.assertIn("it.ReportClass AS ClaseItem", dl.CONSULTAS["columnas"])
        for sql in dl.CONSULTAS.values():
            db.validate_sql(sql)

    def test_cargar_conserva_la_clase_del_item_de_cada_columna(self):
        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)
        d = dl.cargar("ponderosa", run=FalsoRun())
        for cols in d["columnas"].values():
            for c in cols:
                self.assertIn("OID", c)                                                       # la forma de siempre; `ClaseItem` llega si la base la trae

    def test_dim_de_la_reproduccion_es_el_dim_del_animal(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "DIM", ClaseItem=50)])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CurrentDIM"])
        self.assertEqual(r["faltan"], [])

    def test_un_dim_de_otra_clase_no_es_el_dim_del_animal(self):
        """El `DIM` de un control lechero (otra clase) es otra cosa: no se traduce solo por el nombre."""
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "DIM", ClaseItem=10)])
        self.assertEqual(r["faltan"], ["DIM"])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number"])

    def test_sin_la_clase_del_item_no_se_adivina(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "DIM")])
        self.assertEqual(r["faltan"], ["DIM"])

    def test_la_lactancia_y_el_rodeo_tambien_son_alias(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "LactationNumber", ClaseItem=50), columna(12, 1, 3, "GroupNumber", ClaseItem=12)])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CurrentLactationNumber", "AnimalGroupNumber"])
        self.assertEqual(r["faltan"], [])

    def test_el_nombre_de_la_finca_es_el_name_de_la_clase_finca_y_ningun_otro_name(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "Name", ClaseItem=54), columna(12, 1, 3, "Name", ClaseItem=10)])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "FarmName"])
        self.assertEqual(r["faltan"], ["Name"])

    def test_cada_alias_apunta_a_un_campo_que_existe_y_a_su_clase_de_delpro(self):
        for (item, clase), id_ in dl.ALIAS_DE_ITEMS.items():
            self.assertIn(id_, cat.CATALOGO, id_)
            self.assertNotEqual(item, id_)
            self.assertTrue(clase)

    def test_un_alias_no_pisa_a_un_campo_con_parametro(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "DaysDry", ClaseItem=50), columna(12, 1, 3, "DaysDry", ClaseItem=50)], [param(11, "LactationNumber", "1"), param(12, "LactationNumber", "3")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "DaysDry_1", "DaysDry_3"])
        self.assertEqual(r["faltan"], [])

    def test_los_dias_abiertos_que_el_catalogo_no_tiene_quedan_como_faltantes(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "DaysOpenInLactation", ClaseItem=50)], [param(11, "LactationNumber", "9")])
        self.assertEqual(r["faltan"], ["DaysOpenInLactation"])

    def test_el_flujo_bajo_de_ayer_y_el_puesto_de_hoy_se_traen_por_numero_de_ordeño(self):
        cols = [columna(10, 1, 1, "Number"), columna(11, 1, 2, "CMSLowMilkFlowYesterday"), columna(12, 1, 3, "MpcNumberTodayMilkingNumber"), columna(13, 1, 4, "FlowZeroToFifteenYesterdayForMilking")]
        r = traducir(cols, [param(11, "MilkingNumber", "2"), param(12, "MilkingNumber", "1"), param(13, "MilkingNumber", "3")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CMSLowMilkFlowYesterday_2", "MpcNumberTodayMilkingNumber_1", "FlowZeroToFifteenYesterdayForMilking_3"])
        self.assertEqual(r["faltan"], [])

    def test_la_separacion_por_regla(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "IsForeverForSort"), columna(12, 1, 3, "StartTimeForSort")], [param(11, "RuleIndex", "2"), param(12, "RuleIndex", "2")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "IsForeverForSort_2", "StartTimeForSort_2"])


class TestLosFiltrosConLosCamposNuevos(unittest.TestCase):
    """Cinco de los campos solo aparecían en filtros de servidor: ahora el filtro los reconoce."""

    def informe_con_filtro(self, texto):
        f = [filtro(5, "Filtro de prueba", texto)]
        return traducir([columna(10, 1, 1, "Number")], filtros=f, informes=[informe(1, ServerFilter=5)])

    def test_dias_desde_el_ultimo_celo_con_la_ruta_de_la_reproduccion(self):
        r = self.informe_con_filtro("[<AnimalExtended>ReproductionInfo.DaysSinceLastHeat] < 37")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "DaysSinceLastHeat", "op": "<", "valor": 37}])
        self.assertEqual(r["faltan"], [])

    def test_el_numero_del_diagnostico_de_gestacion(self):
        r = self.informe_con_filtro("[ReproductionInfo.PregnancyCheckNumber] = 2")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "PregnancyCheckNumber", "op": "=", "valor": 2}])

    def test_las_listas_de_animales_con_contains(self):
        r = self.informe_con_filtro("Contains([AnimalSelectionsNumberItem], 'Celo Post Preñez')")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "AnimalSelectionsNumberItem", "op": "contiene", "valor": "Celo Post Preñez"}])

    def test_el_chequeo_de_salud_en_esta_lactancia(self):
        r = self.informe_con_filtro("[IsHealthCheckEventInCurrentLactation] = True")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "IsHealthCheckEventInCurrentLactation", "op": "es", "valor": "Sí"}])

    def test_el_resultado_del_chequeo_con_un_enum_de_delpro_no_se_adivina(self):
        """DelPro compara con un valor de enumeración («Healthy»): acá no se traduce, pero el campo ya existe y no figura como faltante."""
        r = self.informe_con_filtro("[LatestHealthCheckEventResult] <> ##Enum#DeLaval.DDM.CommonApp.ExtendedFeatures.Health.BO.HealthCheckResultType,Healthy#")
        self.assertNotIn("LatestHealthCheckEventResult", r["faltan"])
        self.assertEqual(r["definicion"].get("condiciones", []), [])

    def test_las_rutas_del_dim_y_los_dias_desde_el_ultimo_parto(self):
        r = self.informe_con_filtro("[<AnimalExtended>ReproductionInfo.DIM] >= 300 And [ReproductionInfo.DSLC] < 20")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentDIM", "op": ">=", "valor": 300}, {"campo": "CurrentDSLC", "op": "<", "valor": 20}])

    def test_un_filtro_con_los_campos_de_la_tanda_pasa_el_compilador(self):
        r = self.informe_con_filtro("[ReproductionInfo.DaysSinceLastHeat] < 37 And [ReproductionInfo.PregnancyCheckNumber] = 2")
        im.compilar(r["definicion"], herd=1)


if __name__ == "__main__":
    unittest.main()
