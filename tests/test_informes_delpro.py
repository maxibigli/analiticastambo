"""Traer a LactIA los informes que el tambo ya armó en DelPro (`informes_delpro.py`).

Lo que importa:
  * NUNCA se escribe nada en DelPro: las cuatro lecturas son SELECT fijos que pasan la validación de solo lectura de la app.
  * Lo que no se puede traer no se adivina: queda dicho, con el motivo, y el informe pasa a «parcial». Un filtro que no se trae
    cambia QUÉ animales aparecen, así que se avisa distinto que una columna que falta.
  * La definición que sale SIEMPRE se puede correr: pasa `informes_motor.compilar`, o la condición/columna que no pasa se descarta.
  * Los textos de DelPro son datos, no órdenes: un valor con SQL adentro no llega a la consulta (lo frena `compilar`).

Los datos de prueba son inventados (nombres genéricos), con la forma de las tablas reales `DDMReport`, `DDMReportColumn`, `Filter`.

    C:\\Python314\\python.exe -m unittest tests.test_informes_delpro -v
"""
import unittest

import db
import informes_catalogo as cat
import informes_delpro as dl
import informes_motor as im

NS = "DeLaval.DDM.CommonApp.Module.BasicFeatures.AnimalAdmin."
TIPO_INFORME = "DeLaval.DDM.CommonApp.Platform.BO.DDMReport"
TIPO_CARPETA = "DeLaval.DDM.CommonApp.Platform.BO.DDMReportFolder"
CLASES = {10: NS + "BasicAnimal", 42: NS + "AnimalExtended", 12: NS + "AnimalGroup",
          41: "DeLaval.DDM.CommonApp.Module.BasicFeatures.Milking.SessionMilkYield"}
NIVEL = {"BasicAnimal": 10, "AnimalExtended": 42, "AnimalGroup": 12, "SessionMilkYield": 41}


def informe(oid, nombre="Informe de prueba", nivel="BasicAnimal", carpeta=None, predefinido=False, tipo=TIPO_INFORME, **kw):
    r = {"OID": oid, "Name": nombre, "ParentFolder": carpeta, "IsPredefined": predefinido, "TypeName": tipo,
         "IncludeCulledAndExitedAnimals": False, "ReportClass": NIVEL[nivel], "ServerFilter": None, "CustomFilter": None,
         "Description": None, "Herd": 1}
    r.update(kw)
    return r


def carpeta(oid, nombre, padre=None, predefinida=False):
    return informe(oid, nombre, carpeta=padre, predefinido=predefinida, tipo=TIPO_CARPETA)


def columna(oid, informe_oid, indice, item, **kw):
    c = {"OID": oid, "Report": informe_oid, "Index": indice, "item": item, "GroupIndex": -1, "SortDirection": 0, "SortIndex": -1,
         "SummaryType": 6, "GroupSummaryType": 6, "SummaryTypes": None, "FilterString": None}
    c.update(kw)
    return c


def filtro(oid, nombre, texto):
    return {"OID": oid, "Name": nombre, "FilterText": texto}


def datos(informes, columnas, filtros=()):
    return dl.armar_datos(list(informes), list(columnas), list(filtros), [{"OID": k, "TypeName": v} for k, v in CLASES.items()])


def columnas_de(oid_informe, items, desde=1000):
    return [columna(desde + i, oid_informe, i + 1, it) for i, it in enumerate(items)]


# ------------------------------------------------------------------------------------------ criterios de DelPro

class TestMostrar(unittest.TestCase):
    """El filtro de DelPro, leído por un humano: es lo que se le muestra al usuario cuando algo no se puede traer."""

    def m(self, texto):
        return dl.mostrar(dl.analizar(texto))

    def test_comparaciones(self):
        self.assertEqual(self.m("[CurrentDIM] >= 300"), "CurrentDIM >= 300")
        self.assertEqual(self.m("[A] <> 5"), "A <> 5")

    def test_los_sufijos_de_numero_no_se_muestran(self):
        self.assertEqual(self.m("[A] = 1b"), "A = 1")
        self.assertEqual(self.m("[A] < 18.0f"), "A < 18")
        self.assertEqual(self.m("[A] < 18.5f"), "A < 18.5")
        self.assertEqual(self.m("[A] >= 7s"), "A >= 7")

    def test_texto_con_comilla_duplicada(self):
        self.assertEqual(self.m("[A] = 'O''Brien'"), "A = 'O''Brien'")

    def test_la_ruta_del_campo_pierde_el_calificador_de_tipo(self):
        self.assertEqual(self.m("[<AnimalExtended>ReproductionInfo.IsExited] = True"), "ReproductionInfo.IsExited = Verdadero")

    def test_in_between_y_nulos(self):
        self.assertEqual(self.m("[A] In (1b, 2b)"), "A es uno de (1, 2)")
        self.assertEqual(self.m("[A] Between(14, 40)"), "A entre 14 y 40")
        self.assertEqual(self.m("[A] Is Null"), "A sin dato")
        self.assertEqual(self.m("[A] Is Not Null"), "A con dato")

    def test_fecha_y_enum(self):
        self.assertEqual(self.m("InDateRange([D], #2026-01-07#, #2026-01-22#)"), "D entre mié 07/01/2026 y jue 22/01/2026 (sin incluir el último día)")
        self.assertEqual(self.m("[D] >= #2026-08-27#"), "D >= jue 27/08/2026")             # las fechas de los textos llevan el día de la semana
        self.assertEqual(self.m("[E] = ##Enum#DeLaval.Algo.Reproduction.ReproductionStatus,Open#"), "E = Open")

    def test_y_o_no(self):
        self.assertEqual(self.m("[A] = 1 And [B] = 2"), "A = 1 y B = 2")
        self.assertEqual(self.m("[A] = 1 Or [B] = 2 And [C] = 3"), "A = 1 o B = 2 y C = 3")
        self.assertEqual(self.m("([A] = 1 Or [B] = 2) And [C] = 3"), "(A = 1 o B = 2) y C = 3")
        self.assertEqual(self.m("Not [A] In (14)"), "no (A es uno de (14))")

    def test_funciones(self):
        self.assertEqual(self.m("Contains([A], 'x')"), "Contains(A, 'x')")
        self.assertEqual(self.m("IsOutlookIntervalLastWeek([A])"), "IsOutlookIntervalLastWeek(A)")

    def test_sin_dato_y_booleanos(self):
        self.assertEqual(self.m("[A] = Null"), "A = sin dato")
        self.assertEqual(self.m("[A] = False"), "A = Falso")

    def test_numero_negativo(self):
        self.assertEqual(self.m("[A] > -5"), "A > -5")


class TestAnalizarRechaza(unittest.TestCase):
    def malo(self, texto):
        with self.assertRaises(dl.CriterioError):
            dl.analizar(texto)

    def test_sintaxis_rota(self):
        for t in ("[A] >", "[A] = 1 And", "([A] = 1", "[A] = 1)", "[A", "[A] = 'sin cerrar", "[A] In (1, 2", "[A] = 1 [B] = 2",
                  "[A] Between(1)", "[A] Is Foo", "", "   ", "= 5", "[A] = #sin cerrar"):
            self.malo(t)

    def test_un_sufijo_que_no_existe(self):
        self.malo("[A] = 5x")

    def test_caracteres_raros(self):
        self.malo("[A] = 1; DROP TABLE x")
        self.malo("[A] = \x00")


# ------------------------------------------------------------------------------------------ términos -> condiciones

def cond(texto, maximo=10, catalogo=None):
    """Condiciones y pendientes de un texto de filtro, resolviendo los campos por su nombre (como el ServerFilter)."""
    c = cat.CATALOGO if catalogo is None else catalogo
    return dl.condiciones_de(texto, dl.resolver_por_nombre(c), c, maximo)


class TestCondiciones(unittest.TestCase):
    def test_numero(self):
        cs, pend = cond("[CurrentDIM] >= 300")
        self.assertEqual(cs, [{"campo": "CurrentDIM", "op": ">=", "valor": 300}])
        self.assertEqual(pend, [])

    def test_los_sufijos_se_ignoran_y_un_decimal_entero_es_entero(self):
        self.assertEqual(cond("[CurrentLactationNumber] >= 1s")[0][0]["valor"], 1)
        self.assertEqual(cond("[SevenDayAverage] < 18.0f")[0][0]["valor"], 18)
        v = cond("[SevenDayAverage] < 18.5f")[0][0]["valor"]
        self.assertEqual(v, 18.5)
        self.assertIsInstance(cond("[SevenDayAverage] < 18.0f")[0][0]["valor"], int)

    def test_texto(self):
        cs, _ = cond("[AnimalGroupName] = 'Rodeo 4 - Baja'")
        self.assertEqual(cs, [{"campo": "AnimalGroupName", "op": "es", "valor": "Rodeo 4 - Baja"}])
        self.assertEqual(cond("[AnimalGroupName] = 'O''Brien'")[0][0]["valor"], "O'Brien")

    def test_el_campo_a_la_derecha_se_da_vuelta(self):
        self.assertEqual(cond("300 <= [CurrentDIM]")[0], [{"campo": "CurrentDIM", "op": ">=", "valor": 300}])
        self.assertEqual(cond("5 > [CurrentDIM]")[0], [{"campo": "CurrentDIM", "op": "<", "valor": 5}])

    def test_el_calificador_de_tipo_se_ignora(self):
        self.assertEqual(cond("[<AnimalExtended>CurrentDIM] > 5")[0], [{"campo": "CurrentDIM", "op": ">", "valor": 5}])

    def test_varias_con_y(self):
        cs, pend = cond("[CurrentDIM] >= 14 And [CurrentLactationNumber] = 2 And [AnimalGroupNumber] < 6")
        self.assertEqual([(c["campo"], c["op"], c["valor"]) for c in cs],
                         [("CurrentDIM", ">=", 14), ("CurrentLactationNumber", "=", 2), ("AnimalGroupNumber", "<", 6)])
        self.assertEqual(pend, [])

    def test_los_parentesis_de_una_comparacion_no_cambian_nada(self):
        cs, pend = cond("([CurrentDIM] >= 300) And ([CurrentLactationNumber] = 2)")
        self.assertEqual(len(cs), 2)
        self.assertEqual(pend, [])

    def test_es_uno_de_numeros(self):
        cs, _ = cond("[CurrentLactationNumber] In (1b, 2b)")
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": "en", "valor": [1, 2]}])

    def test_es_uno_de_texto(self):
        cs, _ = cond("[AnimalGroupName] In ('Rodeo 1', 'Rodeo 2', 'Rodeo 4 - Baja')")
        self.assertEqual(cs, [{"campo": "AnimalGroupName", "op": "en", "valor": ["Rodeo 1", "Rodeo 2", "Rodeo 4 - Baja"]}])

    def test_es_uno_de_con_valores_de_otro_tipo_no_se_trae(self):
        cs, pend = cond("[CurrentLactationNumber] In (1, 'dos')")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_es_uno_de_con_demasiados_valores_no_se_trae(self):
        valores = ", ".join(str(i) for i in range(im.LISTA_MAX + 1))
        cs, pend = cond(f"[CurrentLactationNumber] In ({valores})")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_entre_son_dos_condiciones(self):
        cs, _ = cond("[CurrentDIM] Between(14, 40)")
        self.assertEqual(cs, [{"campo": "CurrentDIM", "op": ">=", "valor": 14}, {"campo": "CurrentDIM", "op": "<=", "valor": 40}])

    def test_entre_de_texto_no_se_trae(self):
        cs, pend = cond("[AnimalGroupName] Between('a', 'z')")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_rango_de_fechas_es_cerrado_a_la_izquierda_y_abierto_a_la_derecha(self):
        cs, _ = cond("InDateRange([LastCalvingDate], #2026-01-07#, #2026-01-22#)")
        self.assertEqual(cs, [{"campo": "LastCalvingDate", "op": ">=", "valor": "2026-01-07"},
                              {"campo": "LastCalvingDate", "op": "<", "valor": "2026-01-22"}])

    def test_fecha_con_hora_cero_vale_y_con_hora_no(self):
        self.assertEqual(cond("[LastCalvingDate] >= #2026-01-07 00:00:00#")[0][0]["valor"], "2026-01-07")
        cs, pend = cond("[LastCalvingDate] >= #2026-01-07 13:30:00#")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_contiene(self):
        self.assertEqual(cond("Contains([AnimalGroupName], 'Baja')")[0], [{"campo": "AnimalGroupName", "op": "contiene", "valor": "Baja"}])

    def test_con_dato_sobra_si_ya_hay_una_comparacion_del_mismo_campo(self):
        cs, pend = cond("[SevenDayAverage] < 18.0f And [SevenDayAverage] Is Not Null")
        self.assertEqual(cs, [{"campo": "SevenDayAverage", "op": "<", "valor": 18}])
        self.assertEqual(pend, [])

    def test_con_dato_solo_no_se_trae(self):
        cs, pend = cond("[SevenDayAverage] Is Not Null")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)
        self.assertIn("con dato", pend[0]["texto"])

    def test_sin_dato_no_se_trae(self):
        cs, pend = cond("[LastCalvingDate] Is Null")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_o_no_se_trae_entero(self):
        cs, pend = cond("[CurrentDIM] > 5 Or [CurrentLactationNumber] = 2")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)
        self.assertIn(" o ", pend[0]["texto"])

    def test_un_o_adentro_de_un_y_no_arrastra_a_los_demas_terminos(self):
        cs, pend = cond("[CurrentLactationNumber] = 2 And ([CurrentDIM] > 5 Or [CurrentDIM] < 2)")
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": "=", "valor": 2}])
        self.assertEqual(len(pend), 1)

    def test_no_se_trae(self):
        cs, pend = cond("Not [AnimalGroupNumber] In (14)")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_distinto_de_un_texto_no_se_trae(self):
        cs, pend = cond("[AnimalGroupName] <> 'Rodeo 9'")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_un_campo_que_no_esta_en_el_catalogo_se_cuenta_como_faltante(self):
        cs, pend = cond("[<AnimalExtended>ReproductionInfo.IsExited] = True And [CurrentDIM] > 5")
        self.assertEqual(cs, [{"campo": "CurrentDIM", "op": ">", "valor": 5}])
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["campos"], ["IsExited"])
        self.assertIn("IsExited", pend[0]["texto"])

    def test_un_campo_del_catalogo_no_figura_como_faltante(self):
        _, pend = cond("[CurrentDIM] = Null")
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["campos"], [])

    def test_valor_de_otro_tipo_que_el_campo_no_se_trae(self):
        for t in ("[CurrentDIM] > 'x'", "[AnimalGroupName] = 5", "[LastCalvingDate] > 5", "[CurrentDIM] > #2026-01-01#"):
            cs, pend = cond(t)
            self.assertEqual((cs, len(pend)), ([], 1), t)

    def test_enumeraciones_booleanos_y_nulos_no_se_traen(self):
        for t in ("[CurrentReproductionStatus] = ##Enum#DeLaval.X.ReproductionStatus,Open#", "[CurrentDIM] = True", "[CurrentDIM] = Null"):
            cs, pend = cond(t)
            self.assertEqual((cs, len(pend)), ([], 1), t)

    def test_fechas_relativas_y_funciones_no_se_traen(self):
        for t in ("IsOutlookIntervalLastWeek([LastCalvingDate])", "IsLastMonth([LastCalvingDate])", "[LastCalvingDate] > LocalDateTimeToday()",
                  "[CurrentDIM] > AddDays(5)"):
            cs, pend = cond(t)
            self.assertEqual((cs, len(pend)), ([], 1), t)

    def test_aritmetica_no_se_trae(self):
        cs, pend = cond("[CurrentDIM] + 5 > 30")
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_comparar_dos_campos_no_se_trae(self):
        cs, pend = cond("[CurrentDIM] > [SevenDayAverage]")
        self.assertEqual((cs, len(pend)), ([], 1))

    def test_texto_vacio_es_no_hay_filtro(self):
        for t in (None, "", "   "):
            self.assertEqual(cond(t), ([], []))

    def test_sintaxis_que_no_se_entiende_se_avisa_sin_romper(self):
        cs, pend = cond("[CurrentDIM] >>> 5 ???")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)
        self.assertIn("no se pudo leer", pend[0]["motivo"].lower())

    def test_tope_de_condiciones(self):
        t = " And ".join(f"[CurrentDIM] > {i}" for i in range(6))
        cs, pend = cond(t, maximo=4)
        self.assertEqual(len(cs), 4)
        self.assertEqual(len(pend), 2)
        self.assertIn("hasta 4", pend[0]["motivo"])

    def test_el_entre_cuenta_dos_para_el_tope(self):
        cs, pend = cond("[CurrentDIM] Between(1, 5) And [CurrentLactationNumber] = 2", maximo=2)
        self.assertEqual([c["campo"] for c in cs], ["CurrentDIM", "CurrentDIM"])
        self.assertEqual(len(pend), 1)

    def test_un_valor_con_sql_adentro_no_pasa(self):
        """El texto de DelPro es un dato: lo que no aguanta `compilar` (; -- /* */) se descarta y se avisa."""
        cs, pend = cond("[AnimalGroupName] = 'a; DROP TABLE BasicAnimal--'")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)
        cs, pend = cond("[AnimalGroupName] In ('Rodeo 1', 'x /* y')")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)

    def test_lo_que_sale_siempre_compila(self):
        textos = ["[CurrentDIM] >= 300", "[CurrentLactationNumber] In (1b, 2b)", "[AnimalGroupName] In ('Rodeo 1', 'Rodeo 2')",
                  "[CurrentDIM] Between(14, 40) And [AnimalGroupNumber] < 6", "InDateRange([LastCalvingDate], #2026-01-07#, #2026-01-22#)",
                  "Contains([AnimalGroupName], 'Baja') And [SevenDayAverage] < 18.0f And [SevenDayAverage] Is Not Null"]
        for t in textos:
            cs, pend = cond(t)
            self.assertEqual(pend, [], t)
            db.validate_sql(im.compilar({"columnas": [{"campo": "Number"}], "condiciones": cs}, herd=1)["sql"])


class TestResolverPorColumnas(unittest.TestCase):
    """El CustomFilter (el filtro de la grilla) llama a cada columna `C` + su OID: ese número es el de la columna."""

    def test_un_campo_por_oid_de_columna(self):
        cols = [columna(2511, 1, 1, "CurrentLactationNumber"), columna(2691, 1, 2, "MilkingYesterday")]
        res = dl.resolver_por_columnas(cols, cat.CATALOGO)
        self.assertEqual(res("C2511"), ("CurrentLactationNumber", "CurrentLactationNumber"))
        self.assertEqual(res("C2691"), ("MilkingYesterday", None))
        self.assertEqual(res("C9999"), ("C9999", None))
        self.assertEqual(res("UN-C2971"), ("UN-C2971", None))

    def test_el_filtro_de_la_grilla_se_traduce_con_ese_mapa(self):
        cols = [columna(2511, 1, 1, "CurrentLactationNumber"), columna(2714, 1, 2, "AnimalGroupName")]
        res = dl.resolver_por_columnas(cols, cat.CATALOGO)
        cs, pend = dl.condiciones_de("[C2511] In (1b, 2b) And [C2714] = 'Rodeo 4 - Baja'", res, cat.CATALOGO, 10)
        self.assertEqual(cs, [{"campo": "CurrentLactationNumber", "op": "en", "valor": [1, 2]},
                              {"campo": "AnimalGroupName", "op": "es", "valor": "Rodeo 4 - Baja"}])
        self.assertEqual(pend, [])

    def test_una_columna_que_no_existe_en_el_catalogo_se_muestra_con_su_nombre_de_delpro(self):
        cols = [columna(2691, 1, 1, "MilkingYesterday")]
        res = dl.resolver_por_columnas(cols, cat.CATALOGO)
        cs, pend = dl.condiciones_de("[C2691] < 18.0f And [C2691] Is Not Null", res, cat.CATALOGO, 10)
        self.assertEqual(cs, [])
        self.assertEqual(pend[0]["campos"], ["MilkingYesterday"])
        self.assertIn("MilkingYesterday < 18", pend[0]["texto"])


# ------------------------------------------------------------------------------------------ un informe

class TestTraducirInforme(unittest.TestCase):
    def tr(self, informes, columnas, filtros=(), oid=1):
        return dl.traducir_informe(datos(informes, columnas, filtros), oid)

    def test_un_informe_con_todo_en_el_catalogo_se_trae_completo(self):
        r = self.tr([informe(1, "Animales por rodeo")], columnas_de(1, ["Number", "AnimalGroupName", "CurrentDIM"]))
        self.assertEqual(r["estado"], "completo")
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}, {"campo": "AnimalGroupName"}, {"campo": "CurrentDIM"}])
        self.assertEqual(r["columnas"], {"traidas": 3, "total": 3})
        self.assertEqual(r["diferencias"], [])
        self.assertEqual(r["nombre"], "Animales por rodeo")
        self.assertEqual(r["nivel"], "Animal")

    def test_las_columnas_siguen_el_orden_de_delpro(self):
        cols = [columna(11, 1, 3, "CurrentDIM"), columna(12, 1, 1, "Number"), columna(13, 1, 2, "AnimalGroupName")]
        r = self.tr([informe(1)], cols)
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "AnimalGroupName", "CurrentDIM"])

    def test_las_columnas_que_no_existen_se_listan_y_el_informe_queda_parcial(self):
        r = self.tr([informe(1)], columnas_de(1, ["Number", "DaysDry", "CurrentDIM", "AgeAtFirstCalving"]))
        self.assertEqual(r["estado"], "parcial")
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CurrentDIM"])
        self.assertEqual(r["columnas"], {"traidas": 2, "total": 4})
        self.assertEqual(r["faltan"], ["DaysDry", "AgeAtFirstCalving"])
        d = [x for x in r["diferencias"] if x["tipo"] == "columna"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "columnas")
        self.assertIn("DaysDry", d[0]["texto"])
        self.assertIn("AgeAtFirstCalving", d[0]["texto"])

    def test_sin_ninguna_columna_utilizable_no_se_puede_traer(self):
        r = self.tr([informe(1)], columnas_de(1, ["DaysDry", "AgeAtFirstCalving"]))
        self.assertEqual(r["estado"], "no_disponible")
        self.assertIsNone(r["definicion"])
        self.assertIn("DaysDry", r["motivo"])
        self.assertEqual(r["faltan"], ["DaysDry", "AgeAtFirstCalving"])

    def test_un_informe_sin_columnas_no_se_puede_traer(self):
        r = self.tr([informe(1)], [])
        self.assertEqual(r["estado"], "no_disponible")

    def test_un_informe_de_otro_nivel_no_se_trae(self):
        r = self.tr([informe(1, nivel="SessionMilkYield")], columnas_de(1, ["Number"]))
        self.assertEqual(r["estado"], "no_disponible")
        self.assertIsNone(r["definicion"])
        self.assertIn("animales", r["motivo"])
        self.assertEqual(r["nivel"], "Ordeño por sesión")

    def test_el_nivel_animal_extendido_se_trae(self):
        r = self.tr([informe(1, nivel="AnimalExtended")], columnas_de(1, ["Number"]))
        self.assertEqual(r["estado"], "completo")
        self.assertEqual(r["nivel"], "Animal extendido")

    def test_una_carpeta_o_un_informe_inexistente(self):
        d = datos([carpeta(5, "Mis informes"), informe(1)], columnas_de(1, ["Number"]))
        for oid in (5, 99):
            with self.assertRaises(KeyError):
                dl.traducir_informe(d, oid)

    # --- agrupar, ordenar, resumir

    def test_agrupar_sigue_el_orden_de_delpro(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentLactationNumber", GroupIndex=1), columna(13, 1, 3, "AnimalGroupName", GroupIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["agrupar_por"], ["AnimalGroupName", "CurrentLactationNumber"])
        self.assertEqual(r["estado"], "completo")

    def test_agrupar_por_un_campo_que_no_se_puede_agrupar_se_avisa(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentDIM", GroupIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertNotIn("agrupar_por", r["definicion"])
        d = [x for x in r["diferencias"] if x["tipo"] == "agrupacion"]
        self.assertEqual(len(d), 1)
        self.assertIn("Días en ordeño", d[0]["texto"])                   # se nombra con la etiqueta que el usuario ve en el árbol de campos
        self.assertEqual(r["estado"], "parcial")

    def test_agrupar_por_un_campo_que_no_existe_se_avisa_y_se_cuenta_como_faltante(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "Reloteo", GroupIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["faltan"], ["Reloteo"])
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number"])
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "agrupacion"])

    def test_mas_de_dos_niveles_de_grupo_se_recortan_y_se_avisa(self):
        cols = [columna(11, 1, 1, "AnimalGroupName", GroupIndex=0), columna(12, 1, 2, "CurrentLactationNumber", GroupIndex=1),
                columna(13, 1, 3, "CurrentReproductionStatus", GroupIndex=2), columna(14, 1, 4, "Number")]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["agrupar_por"], ["AnimalGroupName", "CurrentLactationNumber"])
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "agrupacion"])

    def test_orden(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentDIM", SortDirection=2, SortIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["orden"], {"campo": "CurrentDIM", "dir": -1})
        r = self.tr([informe(1)], [columna(11, 1, 1, "Number", SortDirection=1, SortIndex=0)])
        self.assertEqual(r["definicion"]["orden"], {"campo": "Number", "dir": 1})

    def test_sin_orden_no_hay_orden(self):
        r = self.tr([informe(1)], columnas_de(1, ["Number"]))
        self.assertNotIn("orden", r["definicion"])

    def test_el_orden_de_una_columna_agrupada_es_el_del_grupo_y_no_cuenta(self):
        cols = [columna(11, 1, 1, "AnimalGroupName", GroupIndex=0, SortDirection=1, SortIndex=-1), columna(12, 1, 2, "Number", SortDirection=1, SortIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["orden"], {"campo": "Number", "dir": 1})
        self.assertEqual([x for x in r["diferencias"] if x["tipo"] == "orden"], [])

    def test_un_segundo_criterio_de_orden_se_avisa(self):
        cols = [columna(11, 1, 1, "CurrentDIM", SortDirection=2, SortIndex=0), columna(12, 1, 2, "Number", SortDirection=1, SortIndex=1)]
        r = self.tr([informe(1)], cols)
        self.assertEqual(r["definicion"]["orden"], {"campo": "CurrentDIM", "dir": -1})
        d = [x for x in r["diferencias"] if x["tipo"] == "orden"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "presentacion")
        self.assertEqual(r["estado"], "completo")                       # un segundo orden no cambia qué animales ni qué columnas hay

    def test_ordenar_por_una_columna_que_no_se_trae_se_avisa(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "DaysDry", SortDirection=1, SortIndex=0)]
        r = self.tr([informe(1)], cols)
        self.assertNotIn("orden", r["definicion"])
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "orden"])

    def test_resumenes(self):
        casos = {0: "sum", 1: "min", 2: "max", 3: "count", 4: "avg"}
        for codigo, esperado in casos.items():
            r = self.tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=codigo, SummaryTypes=str(codigo), GroupSummaryType=codigo)])
            self.assertEqual(r["definicion"]["columnas"][0].get("resumen"), esperado, codigo)

    def test_sin_resumen_es_el_codigo_5_o_6(self):
        for codigo in (5, 6):
            r = self.tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=codigo)])
            self.assertNotIn("resumen", r["definicion"]["columnas"][0])

    def test_un_resumen_solo_para_los_grupos_tambien_se_trae(self):
        """Solo si el informe TIENE grupos: sin ninguna columna agrupada el resumen «de grupo» no se ve nunca (ver test_informes_delpro_revision)."""
        r = self.tr([informe(1)], [columna(10, 1, 1, "AnimalGroupName", GroupIndex=0), columna(11, 1, 2, "SevenDayAverage", SummaryType=6, GroupSummaryType=4)])
        self.assertEqual(r["definicion"]["columnas"][1]["resumen"], "avg")

    def test_un_resumen_que_el_tipo_no_admite_se_descarta_y_se_avisa(self):
        r = self.tr([informe(1)], [columna(11, 1, 1, "Number", SummaryType=0, SummaryTypes="0"), columna(12, 1, 2, "CurrentDIM")])
        self.assertNotIn("resumen", r["definicion"]["columnas"][0])
        d = [x for x in r["diferencias"] if x["tipo"] == "resumen"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "columnas")                  # un total que DelPro mostraba y acá no aparece falta de verdad: el informe queda parcial
        self.assertEqual(r["estado"], "parcial")

    def test_varios_resumenes_en_una_columna_se_trae_el_primero_y_se_avisa(self):
        r = self.tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=4, SummaryTypes="4,0")])
        self.assertEqual(r["definicion"]["columnas"][0]["resumen"], "avg")
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "resumen"])

    def test_un_codigo_de_resumen_desconocido_se_ignora_y_se_avisa(self):
        r = self.tr([informe(1)], [columna(11, 1, 1, "SevenDayAverage", SummaryType=99, SummaryTypes="99")])
        self.assertNotIn("resumen", r["definicion"]["columnas"][0])
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "resumen"])

    def test_un_resumen_en_los_totales_y_otro_en_los_grupos_se_avisa_como_dato(self):
        r = self.tr([informe(1)], [columna(10, 1, 1, "AnimalGroupName", GroupIndex=0),
                                   columna(11, 1, 2, "SevenDayAverage", SummaryType=4, SummaryTypes="4", GroupSummaryType=6),
                                   columna(12, 1, 3, "CurrentDIM", SummaryType=4, SummaryTypes="4", GroupSummaryType=4)])
        d = [x for x in r["diferencias"] if x["tipo"] == "resumen"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "info")

    # --- filtros

    def test_el_filtro_de_la_grilla_se_traduce(self):
        cols = [columna(2511, 1, 1, "Number"), columna(2512, 1, 2, "CurrentLactationNumber"), columna(2713, 1, 3, "AnimalGroupName")]
        r = self.tr([informe(1, CustomFilter="[C2512] In (1b, 2b) And [C2713] = 'Rodeo 4 - Baja'")], cols)
        self.assertEqual(r["estado"], "completo")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentLactationNumber", "op": "en", "valor": [1, 2]},
                                                          {"campo": "AnimalGroupName", "op": "es", "valor": "Rodeo 4 - Baja"}])

    def test_el_filtro_de_servidor_se_traduce(self):
        r = self.tr([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Frescas", "[<AnimalExtended>CurrentDIM] <= 30")])
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentDIM", "op": "<=", "valor": 30}])
        self.assertEqual(r["estado"], "completo")

    def test_los_dos_filtros_se_suman(self):
        cols = [columna(51, 1, 1, "Number"), columna(52, 1, 2, "AnimalGroupName")]
        r = self.tr([informe(1, ServerFilter=7, CustomFilter="[C52] = 'Rodeo 1'")], cols, [filtro(7, "F", "[CurrentDIM] <= 30")])
        self.assertEqual({c["campo"] for c in r["definicion"]["condiciones"]}, {"CurrentDIM", "AnimalGroupName"})

    def test_un_filtro_sin_texto_no_es_un_filtro(self):
        r = self.tr([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Vacío", "")])
        self.assertEqual(r["estado"], "completo")
        self.assertNotIn("condiciones", r["definicion"])

    def test_un_filtro_que_no_se_puede_traer_deja_el_informe_parcial_y_dice_cual(self):
        r = self.tr([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]),
                    [filtro(7, "Preñadas a secar", "[<AnimalExtended>ReproductionInfo.IsExited] = True And [<AnimalExtended>DaysDry] = True")])
        self.assertEqual(r["estado"], "parcial")
        d = [x for x in r["diferencias"] if x["tipo"] == "filtro"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "contenido")
        self.assertIn("Preñadas a secar", d[0]["texto"])
        self.assertIn("más animales", d[0]["texto"])
        self.assertLess(len(d[0]["texto"]), 220)                         # el texto es corto: cada condición y su motivo van aparte, en `detalle`
        self.assertIn("IsExited", " ".join(d[0]["detalle"]))
        self.assertEqual(set(r["faltan"]), {"IsExited", "DaysDry"})

    def test_un_filtro_de_la_grilla_con_una_columna_que_no_esta_se_avisa(self):
        cols = [columna(2691, 1, 1, "MilkingYesterday"), columna(2692, 1, 2, "Number")]
        r = self.tr([informe(1, CustomFilter="[C2691] < 18.0f And [C2691] Is Not Null")], cols)
        self.assertEqual(r["estado"], "parcial")
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}])
        tipos = {x["tipo"] for x in r["diferencias"]}
        self.assertEqual(tipos, {"columna", "filtro"})

    def test_un_filtro_con_sintaxis_rota_no_rompe_la_traduccion(self):
        r = self.tr([informe(1, CustomFilter="[C1 >>> ???")], columnas_de(1, ["Number"]))
        self.assertEqual(r["estado"], "parcial")
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "filtro"])

    # --- el resto

    def test_incluir_descartados(self):
        r = self.tr([informe(1, IncludeCulledAndExitedAnimals=True)], columnas_de(1, ["Number"]))
        self.assertIs(r["definicion"]["incluir_descartados"], True)
        r = self.tr([informe(1)], columnas_de(1, ["Number"]))
        self.assertIs(r["definicion"]["incluir_descartados"], False)

    def test_todos_los_rebanos_se_avisa_como_dato(self):
        r = self.tr([informe(1, Herd=0)], columnas_de(1, ["Number"]))
        d = [x for x in r["diferencias"] if x["tipo"] == "alcance"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["gravedad"], "info")                      # es lo que el tambo quiere (solo los suyos) y el caso habitual: no vuelve parcial al informe
        self.assertEqual(r["estado"], "completo")
        self.assertEqual([x for x in self.tr([informe(1, Herd=1)], columnas_de(1, ["Number"]))["diferencias"] if x["tipo"] == "alcance"], [])

    def test_columna_repetida(self):
        cols = [columna(11, 1, 1, "Number"), columna(12, 1, 2, "CurrentDIM"), columna(13, 1, 3, "CurrentDIM")]
        r = self.tr([informe(1)], cols)
        self.assertEqual([c["campo"] for c in r["definicion"]["columnas"]], ["Number", "CurrentDIM"])
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "columna"])

    def test_tope_de_columnas(self):
        grande = {f"F{i}": cat.Campo(f"F{i}", f"Campo {i}", "X", "txt", "b.Number") for i in range(im.COLUMNAS_MAX + 5)}
        d = datos([informe(1)], columnas_de(1, list(grande)))
        r = dl.traducir_informe(d, 1, catalogo=grande)
        self.assertEqual(len(r["definicion"]["columnas"]), im.COLUMNAS_MAX)
        self.assertEqual(r["estado"], "parcial")
        self.assertTrue([x for x in r["diferencias"] if x["tipo"] == "columna" and str(im.COLUMNAS_MAX) in x["texto"]])
        im.compilar(r["definicion"], campos=grande, herd=1)

    def test_el_largo_del_nombre_es_el_mismo_que_el_de_los_informes_guardados(self):
        import informes_guardados
        self.assertEqual(dl.NOMBRE_MAX, informes_guardados.NOMBRE_MAX)

    def test_el_nombre_se_limpia_y_se_acota(self):
        r = self.tr([informe(1, "  Vacas   a\tsecar  ")], columnas_de(1, ["Number"]))
        self.assertEqual(r["nombre"], "Vacas a secar")
        r = self.tr([informe(1, "x" * 100)], columnas_de(1, ["Number"]))
        self.assertEqual(len(r["nombre"]), 60)

    def test_la_carpeta_es_el_camino_completo(self):
        d = datos([carpeta(1, "Mis informes"), carpeta(2, "Repro", padre=1), informe(3, carpeta=2)], columnas_de(3, ["Number"]))
        self.assertEqual(dl.traducir_informe(d, 3)["carpeta"], "Mis informes › Repro")

    def test_un_ciclo_de_carpetas_no_cuelga(self):
        d = datos([carpeta(1, "A", padre=2), carpeta(2, "B", padre=1), informe(3, carpeta=1)], columnas_de(3, ["Number"]))
        self.assertIsInstance(dl.traducir_informe(d, 3)["carpeta"], str)

    def test_una_carpeta_que_no_existe(self):
        d = datos([informe(3, carpeta=99)], columnas_de(3, ["Number"]))
        self.assertEqual(dl.traducir_informe(d, 3)["carpeta"], "")

    def test_la_descripcion_viaja_y_el_origen_tambien(self):
        r = self.tr([informe(1, Description="Vacas para revisar", predefinido=True)], columnas_de(1, ["Number"]))
        self.assertEqual(r["descripcion"], "Vacas para revisar")
        self.assertFalse(r["del_tambo"])
        self.assertTrue(self.tr([informe(1)], columnas_de(1, ["Number"]))["del_tambo"])

    def test_la_definicion_siempre_compila(self):
        todo = ["Number", "AnimalGroupName", "AnimalGroupNumber", "CurrentLactationNumber", "CurrentDIM", "LastCalvingDate",
                "CurrentReproductionStatus", "SevenDayAverage", "DaysToPeakLastLactation", "PeakYieldLastLactation"]
        numericos = {"Number", "CurrentLactationNumber", "CurrentDIM", "SevenDayAverage", "DaysToPeakLastLactation", "PeakYieldLastLactation", "AnimalGroupNumber"}
        cols = [columna(100 + i, 1, i + 1, it, SummaryType=3 if it == "Number" else 4 if it in numericos else 6,
                        SummaryTypes="3" if it == "Number" else "4" if it in numericos else None) for i, it in enumerate(todo)]
        cols[1]["GroupIndex"] = 0
        cols[3]["GroupIndex"] = 1
        cols[7]["SortDirection"], cols[7]["SortIndex"] = 2, 0
        r = self.tr([informe(1, CustomFilter="[C104] Between(10, 40) And [C101] In ('Rodeo 1', 'Rodeo 2')")], cols)
        self.assertEqual(r["estado"], "completo")
        db.validate_sql(im.compilar(r["definicion"], herd=1)["sql"])
        db.validate_sql(im.compilar(r["definicion"], herd=1)["sql_resumen"])

    def test_un_valor_peligroso_del_filtro_no_llega_a_la_definicion(self):
        r = self.tr([informe(1, CustomFilter="[C11] = 'a; DROP TABLE BasicAnimal--'")], [columna(11, 1, 1, "AnimalGroupName")])
        self.assertNotIn("condiciones", r["definicion"])
        self.assertEqual(r["estado"], "parcial")
        im.compilar(r["definicion"], herd=1)

    def test_nombres_de_columna_peligrosos_no_son_campos(self):
        r = self.tr([informe(1)], [columna(11, 1, 1, "Number"), columna(12, 1, 2, "x'; DROP TABLE a;--")])
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number"}])
        self.assertEqual(r["faltan"], ["x'; DROP TABLE a;--"])           # se muestra como texto (la pantalla lo escapa), nunca llega a una consulta
        im.compilar(r["definicion"], herd=1)

    def test_un_nombre_de_campo_larguisimo_se_acota_en_la_lista_de_faltantes(self):
        r = self.tr([informe(1)], [columna(11, 1, 1, "Number"), columna(12, 1, 2, "Z" * 500)])
        self.assertEqual(r["faltan"], ["Z" * 60])


# ------------------------------------------------------------------------------------------ el listado

class TestListar(unittest.TestCase):
    def armar(self):
        informes = [carpeta(1, "Mis informes"), carpeta(2, "Repro", padre=1),
                    informe(10, "Completo", carpeta=2), informe(11, "Con faltantes", carpeta=2), informe(12, "De otro nivel", nivel="SessionMilkYield"),
                    informe(13, "Sin columnas buenas", carpeta=1), informe(14, "Predefinido", predefinido=True),
                    informe(15, "Un informe fijo de DelPro", tipo="DeLaval.DDM.CommonApp.Module.BasicFeatures.Milking.Report.SCCAllCowsReport")]
        cols = (columnas_de(10, ["Number", "CurrentDIM"], 100) + columnas_de(11, ["Number", "DaysDry", "AgeAtFirstCalving"], 200) +
                columnas_de(12, ["Number", "ZCampoDeOtroNivel"], 300) + columnas_de(13, ["DaysDry", "AgeAtFirstCalving"], 400) + columnas_de(14, ["Number", "DaysDry"], 500))
        return datos(informes, cols)

    def test_lista_informes_con_su_estado_y_deja_afuera_carpetas_y_fijos(self):
        r = dl.listar(self.armar())
        por = {i["oid"]: i for i in r["informes"]}
        self.assertEqual(set(por), {10, 11, 12, 13, 14})
        self.assertEqual(por[10]["estado"], "completo")
        self.assertEqual(por[11]["estado"], "parcial")
        self.assertEqual(por[12]["estado"], "no_disponible")
        self.assertEqual(por[13]["estado"], "no_disponible")
        self.assertEqual(por[10]["carpeta"], "Mis informes › Repro")
        self.assertEqual(por[11]["columnas"], {"traidas": 1, "total": 3})
        self.assertNotIn("definicion", por[10])

    def test_cuenta_por_estado_y_ordena_primero_los_que_se_pueden_traer(self):
        r = dl.listar(self.armar())
        self.assertEqual(r["cuenta"], {"completo": 1, "parcial": 2, "no_disponible": 2})
        self.assertEqual([i["oid"] for i in r["informes"]][:3], [10, 11, 14])            # completos, después parciales (los del tambo primero)
        self.assertEqual({i["oid"] for i in r["informes"][3:]}, {12, 13})

    def test_los_del_tambo_van_antes_que_los_predefinidos_del_mismo_estado(self):
        informes = [informe(1, "B predefinido", predefinido=True), informe(2, "A del tambo")]
        r = dl.listar(datos(informes, columnas_de(1, ["Number"], 10) + columnas_de(2, ["Number"], 20)))
        self.assertEqual([i["oid"] for i in r["informes"]], [2, 1])

    def test_los_campos_que_mas_faltan(self):
        r = dl.listar(self.armar())
        top = {f["campo"]: f["informes"] for f in r["faltantes"]}
        self.assertEqual(top["DaysDry"], 3)
        self.assertEqual(top["AgeAtFirstCalving"], 2)
        self.assertEqual(r["faltantes"][0]["campo"], "DaysDry")

    def test_el_ranking_de_faltantes_solo_cuenta_informes_de_animales(self):
        r = dl.listar(self.armar())
        campos = {f["campo"] for f in r["faltantes"]}
        self.assertNotIn("Number", campos)                 # el que está en el catálogo no falta
        self.assertNotIn("ZCampoDeOtroNivel", campos)      # un informe de otro nivel no cuenta: sus campos no son del catálogo de animales

    def test_solo_los_del_tambo(self):
        r = dl.listar(self.armar(), solo_tambo=True)
        self.assertNotIn(14, {i["oid"] for i in r["informes"]})
        top = {f["campo"]: f["informes"] for f in r["faltantes"]}
        self.assertEqual(top["DaysDry"], 2)

    def test_sin_informes(self):
        r = dl.listar(datos([], []))
        self.assertEqual(r["informes"], [])
        self.assertEqual(r["cuenta"], {"completo": 0, "parcial": 0, "no_disponible": 0})
        self.assertEqual(r["faltantes"], [])

    def test_traducir_todo_el_listado_siempre_compila(self):
        d = self.armar()
        for i in dl.listar(d)["informes"]:
            t = dl.traducir_informe(d, i["oid"])
            if t["definicion"] is not None:
                im.compilar(t["definicion"], herd=1)


# ------------------------------------------------------------------------------------------ la lectura de la base

class FalsoRun:
    """Un `db.run_query` de mentira: devuelve filas según la tabla que mira la consulta y recuerda todas las consultas."""

    def __init__(self, error=None):
        self.sqls, self.error = [], error

    def __call__(self, sql, validate=True, tambo=None, max_rows=None):
        self.sqls.append((sql, tambo, validate, max_rows))
        if self.error:
            raise self.error
        s = sql.upper()
        if "FROM DDMREPORTCOLUMN" in s:
            cols = ["OID", "Report", "Index", "item", "GroupIndex", "SortDirection", "SortIndex", "SummaryType", "GroupSummaryType", "SummaryTypes", "FilterString"]
            return {"columns": cols, "rows": [[11, 1, 1, "Number", -1, 1, 0, 3, 6, "3", None]], "truncated": False}
        if "FROM DDMREPORTCLASS" in s:
            return {"columns": ["OID", "TypeName"], "rows": [[10, NS + "BasicAnimal"]], "truncated": False}
        if "FROM FILTER" in s:
            return {"columns": ["OID", "Name", "FilterText"], "rows": [[7, "F", "[CurrentDIM] > 5"]], "truncated": False}
        cols = ["OID", "Name", "ParentFolder", "IsPredefined", "TypeName", "IncludeCulledAndExitedAnimals", "ReportClass", "ServerFilter",
                "CustomFilter", "Description", "Herd"]
        return {"columns": cols, "rows": [[1, "Un informe", None, False, TIPO_INFORME, False, 10, 7, None, None, 1]], "truncated": False}


class TestCargar(unittest.TestCase):
    def setUp(self):
        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)

    def test_son_cuatro_lecturas_fijas_de_solo_lectura(self):
        run = FalsoRun()
        d = dl.cargar("ponderosa", run=run)
        self.assertEqual(len(run.sqls), 4)
        for sql, tambo, validate, max_rows in run.sqls:
            db.validate_sql(sql)
            self.assertEqual(tambo, "ponderosa")
            self.assertTrue(validate)
            self.assertGreaterEqual(max_rows, 5000)
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"))
        self.assertEqual(set(d["informes"]), {1})

    def test_arma_los_datos_con_lo_leido(self):
        d = dl.cargar("ponderosa", run=FalsoRun())
        r = dl.traducir_informe(d, 1)
        self.assertEqual(r["estado"], "completo")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentDIM", "op": ">", "valor": 5}])
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number", "resumen": "count"}])
        self.assertEqual(r["definicion"]["orden"], {"campo": "Number", "dir": 1})

    def test_se_guarda_en_memoria_un_rato_por_tambo(self):
        run = FalsoRun()
        dl.cargar("ponderosa", run=run)
        dl.cargar("ponderosa", run=run)
        self.assertEqual(len(run.sqls), 4)
        dl.cargar("otro", run=run)
        self.assertEqual(len(run.sqls), 8)

    def test_refrescar_vuelve_a_leer(self):
        run = FalsoRun()
        dl.cargar("ponderosa", run=run)
        dl.cargar("ponderosa", run=run, refrescar=True)
        self.assertEqual(len(run.sqls), 8)

    def test_si_la_base_falla_no_se_guarda_y_el_error_no_filtra_detalles(self):
        run = FalsoRun(error=RuntimeError("Login failed for user 'delpro_lectura' on server SERVER-DELPRO"))
        with self.assertRaises(dl.DelProNoDisponible) as cm:
            dl.cargar("ponderosa", run=run)
        self.assertNotIn("delpro_lectura", str(cm.exception))
        self.assertNotIn("SERVER", str(cm.exception))
        ok = FalsoRun()
        dl.cargar("ponderosa", run=ok)                                  # el error no quedó guardado: se vuelve a intentar
        self.assertEqual(len(ok.sqls), 4)

    def test_un_tambo_sin_las_tablas_de_informes_dice_que_no_hay(self):
        class SinTabla(Exception):
            pass
        with self.assertRaises(dl.DelProNoDisponible) as cm:
            dl.cargar("ponderosa", run=FalsoRun(error=SinTabla("Invalid object name 'DDMReport'")))
        self.assertTrue(cm.exception.vacio)                             # es un estado vacío, no una falla

    def test_cualquier_otra_falla_no_es_un_estado_vacio_y_dice_que_hacer(self):
        with self.assertRaises(dl.DelProNoDisponible) as cm:
            dl.cargar("ponderosa", run=FalsoRun(error=RuntimeError("Login failed for user 'x'")))
        self.assertFalse(cm.exception.vacio)
        self.assertIn("Volver a leer", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
