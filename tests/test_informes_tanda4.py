"""Tanda 4 de campos (25/11/2026): los que el cliente eligió traer de DelPro y todavía faltaban («Campos que faltan» de «Traer de DelPro…»).

Dos hallazgos que la definen (ver `CLAUDE.md`, «Informes personalizables»):

  * DelPro SÍ guarda el parámetro de cada columna (el número de ordeño, la sesión, los días en ordeño...): es una fila `ReportItemParameter` de `AbstractSetting` que apunta a la columna. La nota
    de la tanda 3b que decía que «el informe no guarda el parámetro» era falsa. Un campo con parámetro son, acá, varios campos (uno por valor: `CMSTakeOffFlowYesterday_2`), y el importador elige el
    que corresponde a cada columna.
  * `MilkingNumber` (el ordeño nº N de ESA vaca ese día) y `SessionNo` (la sesión de la sala, 1 a 3) son cosas distintas: DelPro tiene un grupo de campos para cada una.

Las definiciones salen de `DDMReportItem.Expression`. Ninguno se comparó con un informe de DelPro: todos llevan ⚠.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_tanda4 -v
"""
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im
from tests.test_informes_delpro import CLASES, FalsoRun, columna, filtro, informe

SIMPLES = ("IsExited", "PossiblyInWrongGroup", "MilkedGroupNumber", "CMSDaysSinceLastMilking", "LastIdTime", "LastSessionStartTime", "LastDryOffDate", "PregCheckDue", "DryOffDue",
           "CMSMilkYieldTotalYesterday", "CMSMilkingsTodayCount", "SevenDayAvgDuration", "AvgMilkingsPerDayLast7Days")
CON_PARAMETRO = ("AccumulatedTotalYieldInDIMInCurrentLactation_305", "TotalYieldInSelectedDIMInCurrentLactation_305", "AvgDailyYieldOnDayOffset7Days_30", "LatestEventDate_99")
BCS = ("BcsValueYesterday", "DaysSinceLastBcsValue", "LastBcsTrendTwoWeeks", "LastBcsTrendFourWeeks", "LastBcsVWPValue", "LastBcsDryOffPrevLactValue", "BcsFirstInseminationCurrentLactValue",
       "BcsValueDslcSevenDays", "BcsValueDslcThirtyDays", "BcsValueDslcFiftyDays", "BcsValueDslcNinetyDays", "BcsValueDslcTwoHundredDays")
SEPARACION = ("LastCutAreaSortingConfirmed", "LastCutAreaNameSortingConfirmed", "LastCutGateSortingConfirmed", "LastCutGateNameSortingConfirmed", "LastCutReasonSortingConfirmed",
              "LastAutoSortRuleSortingConfirmed", "CMSHoursSinceLastSort", "CMSMinutesSinceLastSort", "CMSSortOnce", "CMSSortCowForever")
# (campo de DelPro «de ayer», campo de DelPro «de hoy») de cada medida de un ordeño
MEDIDAS = (("PeakFlowYesterdayMilkingNumber", "PeakFlowTodayMilkingNumber"), ("CMSTakeOffFlowYesterday", "CMSTakeOffFlowToday"), ("AvgMilkFlowYesterdayMilking", "AvgFlowTodayMilkingNumber"),
           ("MilkDurationYesterdayMilkingNumber", "MilkDurationTodayMilkingNumber"), ("YieldYesterdayMilkingNumber", "YieldTodayMilkingNumber"),
           ("ForcedRetractYesterdayMilking", "ForcedRetractTodayMilking"), ("IdTimeYesterdayMilkingNumber", "IdTimeTodayMilkingNumber"))
POR_ORDENO = tuple(f"{item}_{n}" for par in MEDIDAS for item in par for n in (1, 2, 3))
POR_SESION = tuple(f"{item}_{n}" for item in ("YieldYesterdayForAllMilkingInSession", "YieldTodayForAllMilkingInSession") for n in (1, 2, 3))
TODOS = SIMPLES + CON_PARAMETRO + BCS + SEPARACION + POR_ORDENO + POR_SESION
SI_NO = ("IsExited", "PossiblyInWrongGroup", "PregCheckDue", "DryOffDue", "CMSSortOnce", "CMSSortCowForever") + tuple(i for i in POR_ORDENO if i.startswith("ForcedRetract"))

# Lo que el cliente vio en «Campos que faltan para los 61 elegidos» (25/11/2026): los 30 primeros.
PEDIDOS = ("PossiblyInWrongGroup", "IsExited", "YieldYesterdayForAllMilkingInSession", "CMSMilkYieldTotalYesterday", "PeakFlowYesterdayMilkingNumber", "CMSMilkingsTodayCount", "CMSTakeOffFlowToday",
           "CMSTakeOffFlowYesterday", "MilkDurationYesterdayMilkingNumber", "PregCheckDue", "SevenDayAvgDuration", "YieldTodayForAllMilkingInSession", "AccumulatedTotalYieldInDIMInCurrentLactation",
           "AvgDailyYieldOnDayOffset7Days", "AvgMilkFlowYesterdayMilking", "AvgMilkingsPerDayLast7Days", "BcsValueDslcSevenDays", "BcsValueYesterday", "CMSDaysSinceLastMilking", "CMSHoursSinceLastSort",
           "CMSSort_Iso", "CMSSortCowForever", "CMSSortOnce", "CMSSortTodayOrLater", "ForcedRetractYesterdayMilking", "IdTimeTodayMilkingNumber", "IdTimeYesterdayMilkingNumber", "LastBcsTrendFourWeeks",
           "LastBcsTrendTwoWeeks", "LastCutAreaSortingConfirmed")


def compilar(campos, hoy="2026-11-20", **extra):
    c = im.compilar(dict({"columnas": [{"campo": x} for x in campos]}, **extra), herd=1, hoy=hoy)
    return "\n".join(c[k] for k in ("sql", "sql_resumen") if c.get(k))


def traducir(columnas, parametros=(), filtros=(), informes=None, oid=1):
    d = dl.armar_datos(informes or [informe(1)], columnas, list(filtros), [{"OID": k, "TypeName": v} for k, v in CLASES.items()], list(parametros))
    return dl.traducir_informe(d, oid)


def param(columna_oid, nombre, valor):
    return {"columna": columna_oid, "nombre": nombre, "valor": valor}


class TestElCatalogo(unittest.TestCase):
    def test_estan_todos_y_son_87(self):
        self.assertEqual(len(TODOS), 87)
        self.assertEqual(len(set(TODOS)), 87)
        for id_ in TODOS:
            self.assertIn(id_, cat.CATALOGO, id_)

    def test_los_que_pidio_el_cliente_estan_salvo_dos_que_no_se_pudieron_definir(self):
        """Cada campo de DelPro de la lista del cliente está en el catálogo con su nombre, o es el `item` de sus variantes. Los dos que no: `CMSSort_Iso` y `CMSSortTodayOrLater` dependen de
        `IsActive` de una regla de separación manual, que DelPro calcula en su programa y no guarda en la base: definirlos sería inventar."""
        items = {c.item for c in cat.CATALOGO.values() if c.item}
        faltan = {n for n in PEDIDOS if n not in cat.CATALOGO and n not in items}
        self.assertEqual(faltan, {"CMSSort_Iso", "CMSSortTodayOrLater"})

    def test_ninguno_se_da_por_comparado_y_todos_dicen_de_donde_salen(self):
        for id_ in TODOS:
            self.assertFalse(cat.CATALOGO[id_].verificado, id_)
            self.assertGreater(len(cat.CATALOGO[id_].nota), 15, id_)

    def test_las_variantes_dicen_a_que_campo_de_delpro_y_a_que_parametro_corresponden(self):
        esperado = {"MilkingNumber": POR_ORDENO, "SessionNumber": POR_SESION,
                    "DIM": ("AccumulatedTotalYieldInDIMInCurrentLactation_305", "TotalYieldInSelectedDIMInCurrentLactation_305"), "DayOffset": ("AvgDailyYieldOnDayOffset7Days_30",),
                    "ObjectType": ("LatestEventDate_99",)}
        for nombre, ids in esperado.items():
            for id_ in ids:
                c = cat.CATALOGO[id_]
                valor = id_.rsplit("_", 1)[1]
                self.assertEqual(c.parametros, ((nombre, valor),), id_)
                self.assertEqual(id_, f"{c.item}_{valor}")

    def test_un_campo_sin_parametro_no_tiene_item_ni_parametros(self):
        for id_ in SIMPLES + BCS + SEPARACION:
            self.assertEqual((cat.CATALOGO[id_].item, cat.CATALOGO[id_].parametros), ("", ()), id_)

    def test_el_nombre_de_delpro_de_una_variante_no_esta_tambien_suelto_en_el_catalogo(self):
        """Si estuviera suelto, una columna sin parámetro guardado (o con uno que no es de los nuestros) se traería como si fuera una de las variantes."""
        for c in cat.CATALOGO.values():
            if c.item:
                self.assertNotIn(c.item, cat.CATALOGO, c.id)

    def test_el_orden_y_las_sesiones_son_cosas_distintas(self):
        """El número de ordeño (el 2.º de ESA vaca ese día) y la sesión de la sala son campos separados, con su propia categoría."""
        for id_ in POR_ORDENO:
            self.assertIn("por número de ordeño", cat.CATALOGO[id_].categoria, id_)
            self.assertEqual(cat.CATALOGO[id_].parametros[0][0], "MilkingNumber")
        for id_ in POR_SESION:
            self.assertEqual(cat.CATALOGO[id_].categoria, "Ordeño por sesión", id_)
            self.assertEqual(cat.CATALOGO[id_].parametros[0][0], "SessionNumber")

    def test_los_si_no_son_un_texto_cerrado_y_se_pueden_agrupar(self):
        for id_ in SI_NO:
            c = cat.CATALOGO[id_]
            self.assertEqual((c.tipo, c.valores, c.agrupable), ("txt", cat.SI_NO, True), id_)
        self.assertEqual({i for i in TODOS if cat.CATALOGO[i].agrupable}, set(SI_NO))

    def test_tipos_y_unidades(self):
        c = cat.CATALOGO
        for id_ in ("LastIdTime", "LastSessionStartTime", "LastDryOffDate", "LatestEventDate_99"):
            self.assertEqual(c[id_].tipo, "fecha", id_)
        for n in (1, 2, 3):
            self.assertEqual(c[f"IdTimeYesterdayMilkingNumber_{n}"].tipo, "txt")            # solo la hora, como DelPro (`{0:T}`): el día ya se sabe
            for item in ("PeakFlowYesterdayMilkingNumber", "CMSTakeOffFlowYesterday", "AvgMilkFlowYesterdayMilking"):
                self.assertEqual((c[f"{item}_{n}"].tipo, c[f"{item}_{n}"].unidad), ("num", "kg/min"), item)
            self.assertEqual((c[f"MilkDurationYesterdayMilkingNumber_{n}"].unidad, c[f"MilkDurationYesterdayMilkingNumber_{n}"].decimales), ("s", 0))
            self.assertEqual((c[f"YieldYesterdayForAllMilkingInSession_{n}"].unidad, c[f"YieldYesterdayForAllMilkingInSession_{n}"].decimales), ("kg", 2))
        self.assertEqual(c["SevenDayAvgDuration"].unidad, "s")
        self.assertEqual(c["CMSDaysSinceLastMilking"].unidad, "días")

    def test_solo_los_de_la_camara_piden_una_tabla_que_no_tienen_todos_los_tambos(self):
        self.assertEqual({i for i in TODOS if cat.CATALOGO[i].tabla}, set(BCS))
        for id_ in BCS:
            self.assertEqual(cat.CATALOGO[id_].tabla, "BcsDailyData", id_)


class TestSeCompilan(unittest.TestCase):
    def test_cada_uno_pasa_la_validacion_de_solo_lectura(self):
        for id_ in TODOS:
            sql = compilar(["Number", id_])
            db.validate_sql(sql)
            self.assertNotIn("@HOY", sql, id_)

    def test_en_condiciones_resumenes_y_orden(self):
        db.validate_sql(compilar(["Number", "CMSMilkYieldTotalYesterday"],
                                 condiciones=[{"campo": "PossiblyInWrongGroup", "op": "es", "valor": "Sí"}, {"campo": "CMSDaysSinceLastMilking", "op": ">", "valor": 3},
                                              {"campo": "PeakFlowYesterdayMilkingNumber_1", "op": ">=", "valor": 5}, {"campo": "PregCheckDue", "op": "es", "valor": "No"},
                                              {"campo": "LastDryOffDate", "op": ">=", "valor": "2026-09-01"}], orden={"campo": "CMSMilkYieldTotalYesterday", "dir": -1}))
        im.compilar({"columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "CMSTakeOffFlowYesterday_2", "resumen": "avg"}, {"campo": "YieldYesterdayForAllMilkingInSession_3", "resumen": "sum"},
                                  {"campo": "IdTimeYesterdayMilkingNumber_1", "resumen": "count"}], "detalle": False, "agrupar_por": ["ForcedRetractYesterdayMilking_1", "IsExited"]}, herd=1, hoy="2026-11-20")

    def test_los_ordenos_de_un_dia_se_leen_una_vez_aunque_haya_muchos_campos(self):
        ayer = [i for i in POR_ORDENO if "Yesterday" in i] + ["YieldYesterdayForAllMilkingInSession_1", "CMSMilkYieldTotalYesterday"]
        self.assertEqual(compilar(["Number"] + ayer).count("FROM SessionMilkYield s"), 1)
        hoy = [i for i in POR_ORDENO if "Today" in i] + ["YieldTodayForAllMilkingInSession_2", "CMSMilkingsTodayCount"]
        self.assertEqual(compilar(["Number"] + hoy).count("FROM SessionMilkYield s"), 1)
        self.assertEqual(compilar(["Number", "CMSMilkYieldTotalYesterday", "CMSMilkingsTodayCount"]).count("FROM SessionMilkYield s"), 2)

    def test_un_informe_que_no_usa_ordenos_no_los_lee(self):
        sql = compilar(["Number", "IsExited", "PregCheckDue", "LastDryOffDate", "LastBcsTrendFourWeeks"])
        for tabla in ("SessionMilkYield", "CMSMilkYield", "HistoryAnimalDailyData", "CMSSortResultEvent"):
            self.assertNotIn(tabla, sql, tabla)

    def test_ayer_y_hoy_son_el_dia_del_registro_diario_del_animal(self):
        ayer = compilar(["CMSMilkYieldTotalYesterday"], hoy="2026-11-20")
        self.assertIn("d.Date = DATEADD(day, -1, CONVERT(date, '2026-11-20', 23))", ayer)
        hoy = compilar(["CMSMilkingsTodayCount"], hoy="2026-11-20")
        self.assertIn("d.Date = CONVERT(date, '2026-11-20', 23)", hoy)

    def test_la_sesion_y_el_numero_de_ordeno_se_leen_de_columnas_distintas(self):
        """El bloque del día calcula las dos (una por `MilkingNumber`, otra por `SessionNo`); cada campo elige la suya."""
        sesion = compilar(["YieldYesterdayForAllMilkingInSession_2"])
        self.assertIn("SUM(CASE WHEN s.SessionNo = 2 AND s.TotalYield > 0 THEN s.TotalYield END) AS sesion2", sesion)
        self.assertIn("oy.sesion2 AS c0", sesion)
        ordeno = compilar(["YieldYesterdayMilkingNumber_2"])
        self.assertIn("SUM(CASE WHEN s.MilkingNumber = 2 AND s.TotalYield > 0 THEN s.TotalYield END) AS leche2", ordeno)
        self.assertIn("oy.leche2 AS c0", ordeno)

    def test_cada_medida_lleva_el_filtro_de_delpro(self):
        """Con leche (`TotalYield > 0`) salvo el retiro forzado y la hora de identificación, que DelPro cuenta también en un ordeño sin leche."""
        sql = compilar(["PeakFlowYesterdayMilkingNumber_1", "ForcedRetractYesterdayMilking_1", "IdTimeYesterdayMilkingNumber_1", "MilkDurationYesterdayMilkingNumber_1"])
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 AND s.TotalYield > 0 THEN c.PeakFlow END) AS pico1", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 AND c.ForcedRetract = 1 THEN 1 ELSE 0 END) AS forz1", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 THEN v.IDTime END) AS idt1", sql)
        self.assertIn("MAX(CASE WHEN s.MilkingNumber = 1 AND s.TotalYield > 0 THEN DATEDIFF(second, s.BeginTime, s.EndTime) END) AS dur1", sql)
        self.assertIn("CONVERT(varchar(8), oy.idt1, 108)", sql)                          # solo la hora, hh:mm:ss
        self.assertIn("ISNULL(oy.forz1, 0) = 1", sql)                                    # sin ordeño es «No», como el Exists() de DelPro

    def test_sin_funciones_de_ventana_ni_apply_por_animal(self):
        """Regla de costo (3a y 3b): un `OUTER APPLY` por animal o una función de ventana piden mucha memoria de trabajo. Lo único con `OUTER APPLY (SELECT TOP 1` es el último valor de la cámara
        (`bcs_ultimo`, de la tanda 3a: busca en el índice por fecha y para)."""
        con_apply = {"DaysSinceLastBcsValue", "LastBcsTrendTwoWeeks", "LastBcsTrendFourWeeks"}
        for id_ in TODOS:
            sql = compilar(["Number", id_])
            self.assertNotIn("OVER (", sql, id_)
            if id_ not in con_apply:
                self.assertNotIn("OUTER APPLY", sql, id_)

    def test_ningun_bloque_nuevo_agrupa_dos_veces(self):
        """El de «ordeños por día» se partió en dos agregados simples porque uno en dos niveles pedía 3,5 MB de memoria de trabajo y en el SQL de la PC de desarrollo (7 MB) esperaba para siempre."""
        for id_ in ("ord_7d", "dias_7d"):
            self.assertEqual(cat.BLOQUES[id_].join.upper().count("GROUP BY"), 1, id_)
        sql = compilar(["AvgMilkingsPerDayLast7Days", "SevenDayAvgDuration"])
        self.assertEqual(sql.count("FROM SessionMilkYield s"), 1)
        self.assertEqual(sql.count("FROM AnimalDaily d WHERE"), 1)

    def test_las_horas_desde_el_ultimo_corte_usan_el_reloj_como_la_formula_de_delpro(self):
        for id_, unidad in (("CMSHoursSinceLastSort", "hour"), ("CMSMinutesSinceLastSort", "minute")):
            sql = compilar([id_])
            self.assertIn("DATEADD(hour, -48, GETDATE())", sql)
            self.assertIn(f"DATEDIFF({unidad}, sg.ultimo, GETDATE())", sql)
        self.assertEqual(compilar(["CMSHoursSinceLastSort", "LastCutDateTimeSortingConfirmed"]).count("FROM CMSSortResultEvent"), 1)


class TestLasFormulasDeDelPro(unittest.TestCase):
    def test_los_avisos_de_control_de_gestacion_y_de_secado_salen_de_los_ajustes_del_tambo(self):
        sql = compilar(["PregCheckDue", "DryOffDue"])
        self.assertIn("Parameter = 13", sql)
        self.assertIn("Parameter = 14", sql)
        self.assertIn("rs.aviso_diag_act = 1", sql)
        self.assertIn("rs.aviso_secado_act = 1", sql)
        self.assertIn("ExpectedPregnancyCheckDate", sql)
        self.assertEqual(sql.count("FROM ReproductionSetting"), 1)                       # el bloque de ajustes se une una sola vez

    def test_el_secado_por_hacer_pide_una_vaca_que_ya_pario_con_fecha_esperada(self):
        sql = compilar(["DryOffDue"])
        self.assertIn("r.LactationNumber > 0", sql)
        self.assertIn("IS NOT NULL AND @HOY".replace("@HOY", "CONVERT(date, '2026-11-20', 23)"), sql)

    def test_los_siete_dias_son_los_de_las_demas_ventanas(self):
        sql = compilar(["SevenDayAvgDuration"])
        self.assertIn("d.Date >= DATEADD(day, -7, CONVERT(date, '2026-11-20', 23)) AND d.Date < CONVERT(date, '2026-11-20', 23)", sql)
        self.assertIn("CASE WHEN s.TotalYield > 0 THEN CAST(DATEDIFF(second, s.BeginTime, s.EndTime) AS float) END", sql)
        sql2 = compilar(["AvgMilkingsPerDayLast7Days"])
        self.assertIn("s.TotalYield > 0.1", sql2)                                         # los ordeños de más de 100 g
        self.assertIn("d.TotalYield > 0", sql2)                                           # sobre los días con leche

    def test_la_leche_hasta_los_305_dias_sale_del_historial_como_la_funcion_de_delpro(self):
        sql = compilar(["AccumulatedTotalYieldInDIMInCurrentLactation_305"])
        for pieza in ("HistoryAnimalDailyData", "HistoryAnimal a", "a.ReferenceId", "dd.DIM >= 0 AND dd.DIM <= 305", "ac305.lact = r.LactationNumber", "NULLIF(ac305.total, 0)"):
            self.assertIn(pieza, sql)

    def test_el_dia_305_y_el_promedio_de_hace_30_dias_son_de_la_lactancia_en_curso(self):
        sql = compilar(["TotalYieldInSelectedDIMInCurrentLactation_305", "AvgDailyYieldOnDayOffset7Days_30"])
        self.assertIn("d.DIM = 305 AND d.TotalYield > 0", sql)
        self.assertIn("d305.lact = r.LactationNumber", sql)
        self.assertIn("d.Date = DATEADD(day, -30, CONVERT(date, '2026-11-20', 23))", sql)
        self.assertIn("a30.lact = r.LactationNumber", sql)

    def test_el_corte_confirmado_es_el_ultimo_de_los_que_si_separaron_y_su_area_sale_del_carril(self):
        sql = compilar(["LastCutAreaSortingConfirmed", "LastCutGateNameSortingConfirmed", "LastCutReasonSortingConfirmed", "LastAutoSortRuleSortingConfirmed"])
        self.assertEqual(sql.count("FROM CMSSortResultEvent"), 1)
        self.assertIn("s.IsAnimalSorted = 1", sql)
        self.assertIn("INNER LOOP JOIN AbstractAnimalEvent", sql)
        self.assertIn("CMSSortGateAreaMapping cm ON cm.OID = cf.carril", sql)             # `SortedLane` apunta al carril (que lleva a un área), no es un número de carril
        self.assertIn("CMSSortArea ca ON ca.OID = cm.TargetSortArea", sql)
        self.assertIn("Device cd ON cd.OID = cg.OID", sql)                                 # el nombre de la puerta es el `DeviceName` del equipo
        self.assertNotIn("OVER (", sql)

    def test_separar_siempre_es_la_primera_regla_con_inicio_y_sin_fin(self):
        sql = compilar(["CMSSortCowForever"])
        self.assertIn("MIN(OID)", sql)
        self.assertIn("r1.StartDateTime IS NOT NULL AND r1.EndDateTime IS NULL", sql)

    def test_la_sesion_en_curso_no_mira_al_animal(self):
        sql = compilar(["LastSessionStartTime"])
        self.assertIn("ON 1 = 1", sql)
        self.assertIn("h.EndTime IS NULL", sql)

    def test_todas_las_inseminaciones_se_leen_desde_la_tabla_de_inseminaciones(self):
        sql = compilar(["LatestEventDate_99"])
        self.assertIn("FROM AbstractAnimalEvent a INNER JOIN XPObjectType t ON t.OID = a.ObjectType AND t.TypeName LIKE '%.EventInsemination'", sql)

    def test_la_condicion_corporal_a_los_n_dias_se_une_a_la_reproduccion(self):
        sql = compilar(["BcsValueDslcSevenDays", "BcsValueDslcTwoHundredDays"])
        self.assertIn("DATEADD(day, 7, q.LastLactationChangeDate)", sql)
        self.assertIn("DATEADD(day, 200, q.LastLactationChangeDate)", sql)
        self.assertIn("CAST(x.DateAndTime AS date) = CAST(", sql)

    def test_las_tendencias_y_los_dias_desde_la_ultima_lectura_comparten_el_ultimo_valor_de_la_camara(self):
        sql = compilar(["LastBcsValue", "LastBcsTrendTwoWeeks", "LastBcsTrendFourWeeks", "DaysSinceLastBcsValue"])
        self.assertEqual(sql.count("FROM BcsDailyData x WHERE x.Animal = b.OID"), 1)
        self.assertIn("bu.t2", sql)
        self.assertIn("bu.t4", sql)

    def test_las_tres_marcas_nuevas_de_la_camara_salen_del_mismo_bloque_que_las_de_la_3a(self):
        sql = compilar(["LastBcsDryOffValue", "LastBcsVWPValue", "LastBcsDryOffPrevLactValue", "BcsFirstInseminationCurrentLactValue"])
        self.assertEqual(sql.count("FROM BcsDailyData x WHERE x.IsValueForLastBcsDryOff = 1"), 1)

    def test_ya_salio_del_tambo_es_la_fecha_de_salida(self):
        self.assertIn("b.ExitDate IS NOT NULL", compilar(["IsExited"]))


class TestElImportadorEntiendeLosParametros(unittest.TestCase):
    def test_los_parametros_se_agrupan_por_columna(self):
        d = dl.armar_datos([informe(1)], [columna(11, 1, 1, "Number")], [], [], [param(11, "MilkingNumber", "2"), param(11, "Otro", 5), param(12, "SessionNumber", "1"), {"columna": 13, "nombre": None, "valor": "x"}])
        self.assertEqual(d["parametros"], {11: {"MilkingNumber": "2", "Otro": "5"}, 12: {"SessionNumber": "1"}})

    def test_sin_parametros_el_resultado_es_el_de_siempre(self):
        d = dl.armar_datos([informe(1)], [columna(11, 1, 1, "Number")], [], [])
        self.assertEqual(d["parametros"], {})

    def test_cada_columna_de_un_campo_con_parametro_se_vuelve_la_variante_que_le_toca(self):
        cols = [columna(10, 1, 1, "Number"), columna(11, 1, 2, "CMSTakeOffFlowYesterday"), columna(12, 1, 3, "CMSTakeOffFlowYesterday"), columna(13, 1, 4, "CMSTakeOffFlowYesterday")]
        r = traducir(cols, [param(11, "MilkingNumber", "1"), param(12, "MilkingNumber", "2"), param(13, "MilkingNumber", "3")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CMSTakeOffFlowYesterday_1", "CMSTakeOffFlowYesterday_2", "CMSTakeOffFlowYesterday_3"])
        self.assertEqual(r["faltan"], [])
        self.assertEqual(r["columnas"], {"traidas": 4, "total": 4})
        self.assertNotIn("repetida", " ".join(d["texto"] for d in r["diferencias"]))        # tres columnas del mismo campo con distinto parámetro NO son una repetida
        im.compilar(r["definicion"], herd=1)

    def test_el_orden_de_las_columnas_se_conserva_aunque_los_parametros_vengan_desordenados(self):
        cols = [columna(11, 1, 1, "YieldYesterdayForAllMilkingInSession"), columna(12, 1, 2, "YieldYesterdayForAllMilkingInSession"), columna(13, 1, 3, "YieldYesterdayForAllMilkingInSession")]
        r = traducir(cols, [param(13, "SessionNumber", "1"), param(11, "SessionNumber", "3"), param(12, "SessionNumber", "2")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]],
                         ["YieldYesterdayForAllMilkingInSession_3", "YieldYesterdayForAllMilkingInSession_2", "YieldYesterdayForAllMilkingInSession_1"])

    def test_un_valor_que_el_catalogo_no_tiene_queda_entre_los_campos_que_faltan(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "CMSTakeOffFlowYesterday")], [param(11, "MilkingNumber", "7")])
        self.assertEqual(r["faltan"], ["CMSTakeOffFlowYesterday"])
        self.assertEqual(r["estado"], "parcial")
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number"])

    def test_si_la_base_no_dijo_el_parametro_la_columna_queda_como_faltante(self):
        """Una versión de DelPro sin esa tabla (o una lectura que falló) deja todo como estaba antes de la tanda 4: ni se adivina ni se rompe."""
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "CMSTakeOffFlowYesterday")])
        self.assertEqual(r["faltan"], ["CMSTakeOffFlowYesterday"])

    def test_un_campo_sin_variantes_no_cambia_aunque_tenga_parametros(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "AvgDailyYieldInMonth")], [param(11, "Month", "3"), param(10, "Cualquiera", "1")])
        self.assertEqual(r["faltan"], ["AvgDailyYieldInMonth"])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number"])

    def test_el_parametro_tiene_que_coincidir_entero(self):
        """La variante de DIM 305 no es la de DIM 100 ni una columna con otro parámetro además del DIM."""
        cols = [columna(11, 1, 1, "TotalYieldInSelectedDIMInCurrentLactation"), columna(12, 1, 2, "TotalYieldInSelectedDIMInCurrentLactation"), columna(13, 1, 3, "TotalYieldInSelectedDIMInCurrentLactation")]
        r = traducir(cols, [param(11, "DIM", "305"), param(12, "DIM", "100"), param(13, "DIM", "305"), param(13, "Otro", "1")])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["TotalYieldInSelectedDIMInCurrentLactation_305"])
        self.assertEqual(r["faltan"], ["TotalYieldInSelectedDIMInCurrentLactation"])

    def test_el_filtro_de_la_lista_que_nombra_una_columna_con_parametro_usa_su_variante(self):
        cols = [columna(10, 1, 1, "Number"), columna(11, 1, 2, "CMSTakeOffFlowYesterday"), columna(12, 1, 3, "CMSTakeOffFlowYesterday")]
        r = traducir(cols, [param(11, "MilkingNumber", "1"), param(12, "MilkingNumber", "2")], informes=[informe(1, CustomFilter="[C12] > 1")])
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CMSTakeOffFlowYesterday_2", "op": ">", "valor": 1}])
        self.assertEqual(r["estado"], "parcial" if any(d["gravedad"] in ("contenido", "columnas") for d in r["diferencias"]) else "completo")

    def test_agrupar_y_ordenar_por_una_columna_con_parametro(self):
        cols = [columna(10, 1, 1, "Number"), columna(11, 1, 2, "ForcedRetractYesterdayMilking", GroupIndex=0), columna(12, 1, 3, "CMSTakeOffFlowYesterday", SortDirection=2, SortIndex=0)]
        r = traducir(cols, [param(11, "MilkingNumber", "1"), param(12, "MilkingNumber", "3")])
        self.assertEqual(r["definicion"]["agrupar_por"], ["ForcedRetractYesterdayMilking_1"])
        self.assertEqual(r["definicion"]["orden"], {"campo": "CMSTakeOffFlowYesterday_3", "dir": -1})

    def test_el_informe_queda_completo_y_marca_que_incluye_campos_sin_comparar(self):
        r = traducir([columna(10, 1, 1, "Number"), columna(11, 1, 2, "PeakFlowYesterdayMilkingNumber")], [param(11, "MilkingNumber", "2")])
        self.assertEqual(r["estado"], "completo")
        self.assertIn("⚠", " ".join(d["texto"] for d in r["diferencias"] if d["tipo"] == "verificacion"))

    def test_la_consulta_de_parametros_es_de_solo_lectura(self):
        db.validate_sql(dl.CONSULTA_PARAMETROS)
        self.assertTrue(dl.CONSULTA_PARAMETROS.lstrip().upper().startswith("SELECT"))
        self.assertIn("ReportItemParameter", dl.CONSULTA_PARAMETROS)
        self.assertNotIn("parametros", dl.CONSULTAS)                                       # va aparte: si falla, el importador sigue

    def test_cargar_lee_los_parametros(self):
        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)
        d = dl.cargar("ponderosa", run=FalsoRun())
        self.assertEqual(d["parametros"], {11: {"MilkingNumber": "2"}})

    def test_si_falla_la_lectura_de_los_parametros_el_importador_sigue_andando(self):
        class SinParametros(FalsoRun):
            def __call__(self, sql, validate=True, tambo=None, max_rows=None):
                if "ABSTRACTSETTING" in sql.upper():
                    self.sqls.append((sql, tambo, validate, max_rows))
                    raise RuntimeError("Invalid object name 'AbstractSetting'")
                return super().__call__(sql, validate, tambo, max_rows)

        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)
        d = dl.cargar("ponderosa", run=SinParametros())
        self.assertEqual(d["parametros"], {})
        self.assertEqual(set(d["informes"]), {1})


if __name__ == "__main__":
    unittest.main()
