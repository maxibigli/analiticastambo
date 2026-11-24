"""Casos de borde de la Fase 4 que una medición de cobertura mostró SIN ejecutar (traductor de criterios de DelPro, texto de los envíos, validaciones de la respuesta de la IA).
Cada uno es una rama que alguna vez va a pasar con un dato real: mejor que la corra una prueba antes que el tambo.

    C:\\Python314\\python.exe -m unittest tests.test_informes_bordes -v
"""
import datetime
import logging
import sys
import unittest
from unittest import mock

import informes_catalogo as cat
import informes_delpro as dl
import informes_envios as ie
import informes_lenguaje as il
import informes_motor as im
from tests.test_informes_delpro import columna, columnas_de, datos, filtro, informe
from tests.test_informes_envios import JUEVES, T, Base as BaseEnvios, meta, resultado
from tests.test_informes_lenguaje import BUENA, FRASE, HOY, Base as BaseIA, Cliente, con, respuesta


def cond(texto, resolver="nombre", maximo=10, en_memoria=False):
    r = dl.resolver_por_nombre(cat.CATALOGO) if resolver == "nombre" else resolver
    return dl.condiciones_de(texto, r, cat.CATALOGO, maximo, en_memoria=en_memoria)


class TestPartirEnY(unittest.TestCase):
    """`_partir_en_y` corta el filtro en sus «y» de primer nivel para que un término ilegible no se lleve puestos a los demás. El texto es de DelPro: tiene que aguantar de todo."""

    def test_un_texto_con_una_y_adentro_no_se_parte(self):
        self.assertEqual(dl._partir_en_y("[A] = 'Rodeo A and B' and [B] = 1"), ["[A] = 'Rodeo A and B'", "[B] = 1"])

    def test_una_comilla_doble_adentro_de_un_texto_es_una_comilla(self):
        self.assertEqual(dl._partir_en_y("[A] = 'it''s and ok' and [B] = 1"), ["[A] = 'it''s and ok'", "[B] = 1"])

    def test_una_comilla_sin_cerrar_no_se_parte(self):
        self.assertEqual(dl._partir_en_y("[A] = 'sin cerrar and [B] = 1"), ["[A] = 'sin cerrar and [B] = 1"])

    def test_una_fecha_con_y_adentro_y_una_sin_cerrar(self):
        self.assertEqual(dl._partir_en_y("[A] > #2026-01-01# and [B] = 1"), ["[A] > #2026-01-01#", "[B] = 1"])
        self.assertEqual(dl._partir_en_y("[A] > #2026-01-01 and [B] = 1"), ["[A] > #2026-01-01 and [B] = 1"])
        self.assertEqual(dl._partir_en_y("[A] = ##Enum#DeLaval.Tipo,Open# and [B] = 1"), ["[A] = ##Enum#DeLaval.Tipo,Open#", "[B] = 1"])      # el formato real: tipo y miembro separados por coma

    def test_los_dos_simbolos_y_las_palabras(self):
        self.assertEqual(dl._partir_en_y("[A] = 1 && [B] = 2 AND [C] = 3 And [D] = 4"), ["[A] = 1", "[B] = 2", "[C] = 3", "[D] = 4"])

    def test_un_o_de_primer_nivel_o_un_parentesis_sin_balancear_no_se_parte(self):
        for t in ("[A] = 1 or [B] = 2 and [C] = 3", "[A] = 1 || [B] = 2 && [C] = 3", "([A] = 1 and [B] = 2", "[A] = 1) and ([B] = 2", "[A = 1 and [B] = 2"):
            self.assertEqual(dl._partir_en_y(t), [t], t)

    def test_un_o_adentro_de_un_parentesis_no_impide_partir(self):
        self.assertEqual(dl._partir_en_y("([A] = 1 or [B] = 2) and [C] = 3"), ["([A] = 1 or [B] = 2)", "[C] = 3"])

    def test_una_palabra_que_contiene_and_no_parte(self):
        self.assertEqual(dl._partir_en_y("Standard = 1 and Android = 2"), ["Standard = 1", "Android = 2"])

    def test_y_un_filtro_con_un_termino_ilegible_conserva_los_demas(self):
        cs, pend = cond("[CurrentDIM] > 5 and ??? and [AnimalGroupNumber] < 6 and [CurrentLactationNumber] = [DaysDry]")
        self.assertEqual([c["campo"] for c in cs], ["CurrentDIM", "AnimalGroupNumber"])
        self.assertEqual(len(pend), 2)

    def test_con_una_comilla_sin_cerrar_no_se_sabe_donde_termina_cada_termino_y_todo_queda_sin_traer(self):
        cs, pend = cond("[CurrentDIM] > 5 and ??? and [AnimalGroupNumber] < 6 and [CurrentLactationNumber] = 'x")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 1)


class TestLoQueSeTraduceYLoQueNo(unittest.TestCase):
    def test_contains_se_trae_como_contiene(self):
        cs, pend = cond("Contains([AnimalGroupName], 'Rodeo')")
        self.assertEqual((cs, pend), ([{"campo": "AnimalGroupName", "op": "contiene", "valor": "Rodeo"}], []))

    def test_contains_sobre_algo_que_no_es_texto_o_una_fecha_que_no_es_fecha(self):
        for texto, motivo in (("Contains([CurrentDIM], 'x')", "no es un texto"), ("InDateRange([CurrentDIM], #2026-01-01#, #2026-02-01#)", "no es una fecha")):
            cs, pend = cond(texto)
            self.assertEqual(cs, [], texto)
            self.assertIn(motivo, pend[0]["motivo"], texto)

    def test_like_se_lee_y_se_dice_que_no_existe_aca(self):
        cs, pend = cond("[AnimalGroupName] Like 'Rodeo%'")
        self.assertEqual(cs, [])
        self.assertIn("parecido a", pend[0]["motivo"])
        self.assertIn("parecido a", pend[0]["texto"])                                   # y el término se muestra en palabras

    def test_una_fecha_no_se_trae_con_distinto_de_ni_con_es_uno_de(self):
        cs, pend = cond("[LastCalvingDate] <> #2026-01-01#")
        self.assertEqual(cs, [])
        self.assertIn("distinto de", pend[0]["motivo"])
        cs, pend = cond("[LastCalvingDate] In (#2026-01-01#, #2026-02-01#)")
        self.assertEqual(cs, [])
        self.assertIn("es uno de", pend[0]["motivo"])

    def test_una_fecha_con_hora_o_mal_escrita_dice_cual_de_las_dos(self):
        cs, pend = cond("[LastCalvingDate] > #2026-01-01 13:30:00#")
        self.assertEqual(cs, [])
        self.assertIn("hora", pend[0]["motivo"])
        cs, pend = cond("[LastCalvingDate] > #2026-13-45#")
        self.assertEqual(cs, [])
        self.assertTrue(pend)

    def test_un_valor_que_el_motor_rechaza_por_otra_causa_se_descarta_con_un_mensaje(self):
        with mock.patch.object(im, "compilar", side_effect=ValueError("raro")):
            cs, pend = cond("[CurrentDIM] > 5")
        self.assertEqual(cs, [])
        self.assertIn("no es válido", pend[0]["motivo"])

    def test_un_termino_que_levanta_algo_inesperado_no_tumba_el_filtro(self):
        with mock.patch.object(dl, "_traducir_termino", side_effect=ArithmeticError("boom")):
            cs, pend = cond("[CurrentDIM] > 5 and [AnimalGroupNumber] < 6")
        self.assertEqual(cs, [])
        self.assertEqual(len(pend), 2)
        self.assertTrue(all("No se pudo leer" in p["motivo"] for p in pend))

    def test_un_arbol_que_no_se_puede_escribir_en_palabras_se_muestra_con_el_texto_original(self):
        with mock.patch.object(dl, "mostrar", side_effect=RecursionError()):
            cs, pend = cond("[CurrentDIM] > 5 and [DaysDry] = True")
        self.assertEqual(cs[0]["campo"], "CurrentDIM")
        self.assertIn("DaysDry", pend[0]["texto"])


class TestElAvisoDeUnFiltro(unittest.TestCase):
    def test_con_muchas_condiciones_sin_traer_se_muestran_doce_y_se_dice_cuantas_mas(self):
        texto = " And ".join(f"[Campo{i}] = {i}" for i in range(15))
        d = datos([informe(1, ServerFilter=7)], columnas_de(1, ["Number"]), [filtro(7, "Muchas", texto)])
        r = dl.traducir_informe(d, 1)
        aviso = [x for x in r["diferencias"] if x["tipo"] == "filtro"][0]
        self.assertEqual(len(aviso["detalle"]), 13)
        self.assertEqual(aviso["detalle"][-1], "…y 3 más.")
        self.assertIn("15 condiciones", aviso["texto"])

    def test_tres_resumenes_perdidos_se_cuentan_y_el_detalle_los_nombra(self):
        cols = [columna(10 + i, 1, i + 1, c, SummaryType=0, SummaryTypes="0") for i, c in enumerate(["Number", "AnimalGroupName", "CurrentReproductionStatus"])]
        r = dl.traducir_informe(datos([informe(1)], cols), 1)
        aviso = [x for x in r["diferencias"] if x["tipo"] == "resumen"][0]
        self.assertIn("En 3 columnas", aviso["texto"])
        self.assertEqual(len(aviso["detalle"]), 3)
        self.assertEqual(r["estado"], "parcial")

    def test_agrupar_por_un_campo_que_no_se_puede_agrupar_o_repetido_o_de_mas(self):
        cols = [columna(10, 1, 1, "Number"), columna(11, 1, 2, "CurrentDIM", GroupIndex=0), columna(12, 1, 3, "DaysDry", GroupIndex=1),
                columna(13, 1, 4, "AnimalGroupName", GroupIndex=2), columna(14, 1, 5, "CurrentLactationNumber", GroupIndex=3),
                columna(15, 1, 6, "CurrentReproductionStatus", GroupIndex=4), columna(16, 1, 7, "AnimalGroupName", GroupIndex=5)]
        r = dl.traducir_informe(datos([informe(1)], cols), 1)
        self.assertEqual(r["definicion"]["agrupar_por"], ["AnimalGroupName", "CurrentLactationNumber"])
        textos = " ".join(x["texto"] for x in r["diferencias"] if x["tipo"] == "agrupacion")
        self.assertIn("demasiados valores distintos", textos)                           # CurrentDIM no se puede agrupar
        self.assertIn("todavía no existe acá", textos)                                  # DaysDry no está en el catálogo
        self.assertIn("más de 2 niveles", textos)                                       # el tercero sobra

    def test_si_el_motor_rechazara_lo_armado_no_se_ofrece_un_informe_que_no_corre(self):
        d = datos([informe(1)], columnas_de(1, ["Number"]))
        with mock.patch.object(im, "compilar", side_effect=im.InformeError("algo raro")):
            r = dl.traducir_informe(d, 1)
        self.assertEqual(r["estado"], "no_disponible")
        self.assertIsNone(r["definicion"])
        self.assertIn("No se pudo armar", r["motivo"])

    def test_cargar_sin_pasar_la_funcion_usa_la_de_la_base(self):
        import db
        dl.vaciar_cache()
        self.addCleanup(dl.vaciar_cache)
        llamadas = []

        def run(sql, **kw):
            llamadas.append(sql)
            return {"columns": ["OID"], "rows": [], "truncated": False}
        with mock.patch.object(db, "run_query", run):
            try:
                dl.cargar("ponderosa")
            except Exception:  # noqa: BLE001 -- con filas vacías `armar_datos` puede fallar: solo importa que haya usado db.run_query
                pass
        self.assertTrue(llamadas)


class TestElTextoDeLosEnvios(unittest.TestCase):
    def test_los_numeros_y_las_celdas_raras(self):
        self.assertEqual(ie._n(None), "—")
        self.assertEqual(ie._n(True), "True")
        self.assertEqual(ie._n("texto"), "texto")
        self.assertEqual(ie._n(1234.5, 1), "1.234,5")
        self.assertEqual(ie._celda({"tipo": "num", "decimales": 1}, None), "—")
        self.assertEqual(ie._celda({"tipo": "fecha"}, "2026-08-01T14:30:00"), "01/08/2026")
        self.assertEqual(ie._celda({"tipo": "fecha"}, "no es fecha"), "no es fecha")
        self.assertEqual(ie._celda({"tipo": "txt"}, "x" * 40), "x" * 23 + "…")

    def test_llenar_si_ni_el_encabezado_entra_no_pone_nada(self):
        self.assertEqual(ie._llenar("Un encabezado largo", ["a"], 1, lambda n: "y más", 5), ([], 0))

    def test_llenar_si_ni_la_linea_de_cuantos_mas_entra_deja_solo_el_encabezado(self):
        lineas, gastado = ie._llenar("Por rodeo:", ["- Rodeo 1: 300 animales", "- Rodeo 2: 200 animales"], 2, lambda n: "- y 2 más (están en el Excel)", 30)
        self.assertEqual(lineas, ["Por rodeo:"])
        self.assertEqual(gastado, len("Por rodeo:") + 1)

    def test_llenar_saca_lineas_para_que_entre_la_de_cuantos_mas(self):
        items = [f"- Rodeo {i}: 300 animales" for i in range(1, 6)]
        lineas, gastado = ie._llenar("Por rodeo:", items, 5, lambda n: f"- y {n} más", 80)
        self.assertEqual(lineas[0], "Por rodeo:")
        self.assertRegex(lineas[-1], r"^- y \d+ más$")
        self.assertLessEqual(gastado, 80)
        self.assertEqual(len(lineas) - 2 + int(lineas[-1].split()[2]), 5)                  # lo que se muestra más lo que dice que falta son los 5

    def test_si_lo_fijo_solo_ya_pasa_el_limite_se_corta_con_puntos_suspensivos(self):
        r = {"columnas": [meta("Number", "Número del animal", "txt")], "filas": [["1"]], "truncado": False, "detalle": True, "agrupar": [], "grupos": [], "totales": None}
        texto, _, _ = ie.texto_resumen("Un informe con un nombre larguísimo " * 5, JUEVES, r, True, limite=80)
        self.assertEqual(len(texto), 80)
        self.assertTrue(texto.endswith("…"))

    def test_probar_un_informe_que_no_existe(self):
        base = BaseEnvios("setUp")
        with self.assertRaises(ie.EnvioError):
            ie.probar(T, 999, lambda *a: None, ["telegram"], False, lambda: ["telegram"], ahora=JUEVES)


class TestLaPasadaSigueAunqueUnaProgramacionRevienta(BaseEnvios):
    def setUp(self):
        super().setUp()
        self.programar(dias=[3], horas=["08:30"], canales=["telegram"])
        self.ahora = datetime.datetime(2026, 8, 27, 8, 31)

    def test_una_falla_inesperada_dentro_de_la_atencion_se_registra_y_no_corta_la_pasada(self):
        with mock.patch.object(ie, "_atender", side_effect=RuntimeError("falló algo de adentro")), self.assertLogs("informes_envios", level="WARNING") as cap:
            r = self.pasada(self.ahora)
        self.assertEqual(r["enviados"], [])
        self.assertIn("la pasada falló: RuntimeError", "\n".join(cap.output))
        self.assertNotIn("falló algo de adentro", "\n".join(cap.output))                  # el texto de la excepción no va al log
        self.assertEqual(ie.ultima_pasada(), self.ahora)                                  # y el latido se anota igual

    def test_si_el_informe_deja_de_existir_mientras_se_arma_se_dice_y_no_se_reintenta(self):
        def armar_roto(*a, **k):
            raise ie.EnvioError("Ese informe no existe.")
        with mock.patch.object(ie, "armar_de_la_base", armar_roto):
            self.pasada(self.ahora)
            self.pasada(self.ahora + datetime.timedelta(minutes=11))
        fila = ie.registro(T, self.informe["id"], 10)[0]
        self.assertEqual((fila["estado"], fila["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertIn("ya no existe", fila["detalle"])


class TestLaRespuestaDeLaIAFueraDeEsquema(BaseIA):
    """Una respuesta que no cumple el esquema (no debería pasar con salida estructurada, pero el módulo ya se defiende) se le devuelve a la IA UNA vez."""

    def corrige(self, mala):
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertTrue(r["ok"], str(mala)[:80])
        self.assertEqual(len(c.llamadas), 2, str(mala)[:80])
        return c.llamadas[1]["messages"][2]["content"]

    def test_cada_tipo_mal_puesto(self):
        for clave in ("columnas", "agrupar_por", "condiciones", "supuestos", "lo_que_no_se_pudo"):
            self.assertIn("lista", self.corrige(con(**{clave: "no es una lista"})), clave)
        for clave in ("se_puede", "incluir_descartados", "solo_resumen"):
            self.assertIn("verdadero o falso", self.corrige(con(**{clave: "si"})), clave)
        self.assertIn("entero", self.corrige(con(limite="diez")))
        self.assertIn("entero", self.corrige(con(limite=True)))
        self.assertIn("campo y resumen", self.corrige(con(columnas=[{"campo": "Number"}])))
        self.assertIn("campo, op, valor y valores", self.corrige(con(condiciones=[{"campo": "CurrentDIM", "op": ">"}])))
        self.assertIn("lista de textos", self.corrige(con(condiciones=[{"campo": "CurrentDIM", "op": ">", "valor": 5, "valores": []}])))
        self.assertIn("lista de textos", self.corrige(con(condiciones=[{"campo": "CurrentDIM", "op": ">", "valor": "5", "valores": [5]}])))

    def test_una_condicion_sin_campo_valor_o_lista(self):
        self.assertIn("no existe en el catálogo", self.corrige(con(condiciones=[{"campo": "Inventado", "op": ">", "valor": "5", "valores": []}])))
        self.assertIn("lista de valores", self.corrige(con(condiciones=[{"campo": "AnimalGroupNumber", "op": "en", "valor": "", "valores": []}])))
        self.assertIn("falta el valor", self.corrige(con(condiciones=[{"campo": "CurrentDIM", "op": ">", "valor": "  ", "valores": []}])))

    def test_las_lineas_vacias_del_modelo_no_cuentan(self):
        r, _ = self.armar(respuesta(con(supuestos=["", "  ", "Una frase"], lo_que_no_se_pudo=["\n"])))
        self.assertEqual(r["supuestos"], ["Una frase"])
        self.assertEqual(r["no_pude"], [])

    def test_miles_con_punto_o_coma_se_leen_de_las_dos_formas(self):
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "SevenDayAverage", "op": ">", "valor": "1200", "valores": []}])), frase="más de 1.200 kg")
        self.assertEqual([a for a in r["advertencias"] if "no figura" in a], [])
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "SevenDayAverage", "op": ">", "valor": "1200", "valores": []}])), frase="más de 1,200 kg")
        self.assertEqual([a for a in r["advertencias"] if "no figura" in a], [])

    def test_sin_el_objeto_de_tiempo_del_sdk_vale_el_numero(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            self.assertEqual(il._plazo(), il.TIMEOUT_S)

    def test_un_cliente_cuyo_with_options_falla_se_usa_tal_cual(self):
        class Roto(Cliente):
            def with_options(self, **kw):
                raise RuntimeError("no se puede")
        c = Roto(respuesta(BUENA))
        r = il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 1)

    def test_un_error_de_la_ia_ya_conocido_pasa_derecho(self):
        with mock.patch.object(il, "_resultado", side_effect=il.TopeDePedidos("Demasiados")):
            with self.assertRaises(il.TopeDePedidos):
                self.armar(respuesta(BUENA))


if __name__ == "__main__":
    unittest.main()
