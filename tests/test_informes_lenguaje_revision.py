"""Lo que encontró la revisión independiente de «Describí el informe» (Fase 4b, la noche del 24/11/2026; los 9 hallazgos se reprodujeron) y se arregló.

Cada caso salió de una frase o de una respuesta de la IA concretas. Todo con un cliente de IA de mentira: ninguna prueba llama a la API de Anthropic.

    C:\\Python314\\python.exe -m unittest tests.test_informes_lenguaje_revision -v
"""
import logging
import unittest
from unittest import mock

import informes_lenguaje as il
from tests.test_informes_lenguaje import BUENA, FRASE, HOY, Base, Cliente, con, respuesta


def cond(campo, op, valor, valores=()):
    return {"campo": campo, "op": op, "valor": valor, "valores": list(valores)}


class TestUnDiaRelativoNoDesapareceSinRastro(Base):
    """Regla del usuario: nunca «ayer» ni «ese día» en un texto de la app. Pero sacar la línea que decía que faltó parte del pedido dejaba el chip verde."""

    def test_ayer_y_anteayer_se_escriben_con_la_fecha_y_el_dia_de_la_semana(self):
        r, _ = self.armar(respuesta(con(supuestos=["Tomé lo de ayer", "No miré anteayer"])))
        self.assertEqual(r["supuestos"], ["Tomé lo de mié 26/08/2026", "No miré mar 25/08/2026"])

    def test_ese_dia_no_se_puede_traducir_pero_deja_una_linea(self):
        r, _ = self.armar(respuesta(con(supuestos=["Usé ese día como base", "Esto queda"], lo_que_no_se_pudo=["No tengo lo de ese día", "El día anterior no está"])))
        self.assertEqual(r["supuestos"], ["Esto queda", il.SUPUESTO_SOBRE_UN_DIA])
        self.assertEqual(r["no_pude"], [il.PARTE_SIN_ARMAR])                        # dos líneas sacadas = UNA línea neutra

    def test_si_lo_unico_que_avisaba_tenia_un_dia_relativo_no_se_dice_listo(self):
        """La pantalla pinta «Revisalo» cuando `no_pude` o `advertencias` traen algo."""
        r, _ = self.armar(respuesta(con(lo_que_no_se_pudo=["No existe un campo para mastitis ni para lo de ese día"])))
        self.assertTrue(r["no_pude"])

    def test_si_no_se_puede_igual_hay_una_explicacion(self):
        for linea in ("No hay datos de mastitis: no se puede saber qué pasó ese día", "No hay mastitis de ayer"):
            r, _ = self.armar(respuesta({**BUENA, "se_puede": False, "columnas": [], "condiciones": [], "lo_que_no_se_pudo": [linea]}))
            self.assertFalse(r["ok"])
            self.assertTrue(r["no_pude"], linea)
            self.assertNotRegex(" ".join(r["no_pude"]), r"\b(ayer|ese día)\b")

    def test_la_linea_neutra_entra_aunque_la_lista_este_llena(self):
        llena = [f"Línea {i}" for i in range(il.TEXTOS_MAX + 2)] + ["Algo de ese día"]
        r, _ = self.armar(respuesta(con(supuestos=llena)))
        self.assertLessEqual(len(r["supuestos"]), il.TEXTOS_MAX)


class TestLasFechasQuedanFijas(Base):
    """El motor no tiene fechas relativas: «los últimos 30 días» se vuelve una fecha. Un informe guardado y programado repite SIEMPRE la misma ventana."""

    def test_una_condicion_de_fecha_avisa_que_es_fija(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("LastCalvingDate", ">=", "2026-07-28")])), frase="vacas que parieron en los últimos 30 días")
        fijas = [a for a in r["advertencias"] if "fechas" in a and "fijas" in a]
        self.assertEqual(len(fijas), 1)
        self.assertIn("no se corren solas", fijas[0])

    def test_sin_fechas_no_avisa(self):
        r, _ = self.armar(respuesta(BUENA))
        self.assertEqual([a for a in r["advertencias"] if "fijas" in a], [])


class TestLosNumerosDeLaFrase(Base):
    """El aviso «el número no figura en lo que escribiste» saltaba en las traducciones correctas (y se aprende a ignorarlo), y no saltaba cuando
    faltaba una condición o el número era casi el mismo."""

    def sin_figurar(self, condiciones, frase, **kw):
        r, _ = self.armar(respuesta(con(condiciones=condiciones, **kw)), frase=frase)
        return [a for a in r["advertencias"] if "no figura" in a]

    def faltan(self, condiciones, frase, **kw):
        r, _ = self.armar(respuesta(con(condiciones=condiciones, **kw)), frase=frase)
        return [a for a in r["advertencias"] if "escribiste" in a and "no figura" not in a]

    def test_en_ordeno_con_mayor_que_cero_no_avisa(self):
        """Es exactamente lo que ordena la regla 2 del prompt: el 0 nunca está en la frase."""
        self.assertEqual(self.sin_figurar([cond("AnimalGroupNumber", "=", "3"), cond("SevenDayAverage", ">", "0")], "vacas en ordeño del rodeo 3"), [])

    def test_ordinales_y_numeros_en_palabras(self):
        casos = [([cond("CurrentLactationNumber", "=", "1")], "vacas de primera lactancia"),
                 ([cond("CurrentLactationNumber", ">=", "2")], "vacas multíparas"),
                 ([cond("CurrentLactationNumber", "=", "1")], "las primíparas"),
                 ([cond("AnimalGroupNumber", "=", "3"), cond("CurrentDIM", ">", "200")], "rodeo tres con más de doscientos días"),
                 ([cond("CurrentDIM", ">", "35")], "más de treinta y cinco días en ordeño"),
                 ([cond("CurrentDIM", ">", "250")], "más de doscientos cincuenta días"),
                 ([cond("SevenDayAverage", ">", "2500")], "más de dos mil quinientos"),
                 ([cond("SevenDayAverage", ">", "30000")], "más de 30 mil")]
        for condiciones, frase in casos:
            self.assertEqual(self.sin_figurar(condiciones, frase), [], frase)

    def test_el_limite_en_palabras_o_en_cifras(self):
        for frase in ("las diez vacas con más producción", "las 10 vacas con más producción"):
            r, _ = self.armar(respuesta(con(condiciones=[], limite=10)), frase=frase)
            self.assertEqual([a for a in r["advertencias"] if "no figura" in a], [], frase)

    def test_punto_o_coma_decimal(self):
        for frase in ("más de 30,5 kg", "más de 30.5 kg"):
            self.assertEqual(self.sin_figurar([cond("SevenDayAverage", ">", "30.5")], frase), [], frase)

    def test_un_numero_inventado_se_sigue_avisando(self):
        self.assertTrue(self.sin_figurar([cond("CurrentDIM", ">", "250")], FRASE))

    def test_casi_el_mismo_numero_no_pasa(self):
        """La tolerancia del 0,6 % del agente es para miles abreviados: acá 201 no es 200."""
        self.assertTrue(self.sin_figurar([cond("AnimalGroupNumber", "=", "3"), cond("CurrentDIM", ">", "201")], FRASE))

    def test_una_condicion_olvidada_se_avisa(self):
        """«más de 200 días»: la IA dejó solo el rodeo. El 200 de la frase no llegó al informe."""
        avisos = self.faltan([cond("AnimalGroupNumber", "=", "3")], FRASE)
        self.assertTrue([a for a in avisos if "200" in a], avisos)

    def test_no_avisa_si_el_numero_esta_en_un_texto_o_es_un_plazo(self):
        self.assertEqual(self.faltan([cond("AnimalGroupName", "es", "Rodeo 3")], "animales del Rodeo 3"), [])
        self.assertEqual(self.faltan([cond("LastCalvingDate", ">=", "2026-07-28")], "vacas que parieron en los últimos 30 días"), [])
        self.assertEqual(self.faltan([cond("AnimalGroupNumber", "en", "", ["1", "2"])], "rodeos 1 y 2"), [])

    def test_los_numeros_de_la_frase_que_si_estan_no_se_avisan(self):
        r, _ = self.armar(respuesta(BUENA))
        self.assertEqual([a for a in r["advertencias"] if "escribiste" in a], [])


class ClienteConOpciones(Cliente):
    def __init__(self, *respuestas):
        super().__init__(*respuestas)
        self.opciones = []

    def with_options(self, **kw):
        self.opciones.append(kw)
        return self


class TestElTiempoDeEspera(Base):
    def test_el_sdk_no_reintenta_por_su_cuenta(self):
        """Con los reintentos del SDK, un pedido colgado esperaba 3 veces TIMEOUT_S (135 s): más que el corte del túnel."""
        c = ClienteConOpciones(respuesta(BUENA))
        il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        self.assertEqual(c.opciones, [{"max_retries": 0}])

    def test_conectar_tiene_un_tiempo_corto_y_leer_el_de_siempre(self):
        c = ClienteConOpciones(respuesta(BUENA))
        il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        t = c.llamadas[0]["timeout"]
        self.assertEqual((t.connect, t.read), (il.CONEXION_S, il.TIMEOUT_S))
        self.assertLess(il.CONEXION_S, il.TIMEOUT_S)

    def test_un_cliente_sin_with_options_igual_anda(self):
        r, c = self.armar(respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 1)


class TestUnaRespuestaContradictoria(Base):
    def test_valor_y_valores_a_la_vez_se_le_devuelven_a_la_ia(self):
        for mala in (con(condiciones=[cond("AnimalGroupNumber", "=", "3", ["3", "4"])]), con(condiciones=[cond("AnimalGroupNumber", "en", "9", ["3", "4"])])):
            r, c = self.armar(respuesta(mala), respuesta(BUENA))
            self.assertTrue(r["ok"])
            self.assertEqual(len(c.llamadas), 2)
            self.assertIn("valores", c.llamadas[1]["messages"][2]["content"])

    def test_rodeo_3_o_4_no_queda_como_rodeo_3(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("AnimalGroupNumber", "en", "", ["3", "4"])])), frase="vacas del rodeo 3 o 4")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "AnimalGroupNumber", "op": "en", "valor": ["3", "4"]}])


class TestLoQueNoCumpleElEsquema(Base):
    """Con salida estructurada no debería pasar, pero el módulo ya se defiende de lo raro: una respuesta rara se repite UNA vez, no da un 502 sin rastro."""

    def test_enumeraciones_y_tipos_raros_se_corrigen(self):
        raras = [con(columnas=[{"campo": "Number", "resumen": "none"}], orden_campo="Number", orden_dir="asc"),
                 con(orden_dir="DESC"), con(orden_dir="descendente"), con(orden_dir=""),
                 con(condiciones=[cond("AnimalGroupNumber", "igual", "3")]),
                 con(condiciones=[cond(["CurrentDIM"], ">", "3")]), con(condiciones=[cond(5, ">", "3")]),
                 con(orden_campo=["Number"]), con(columnas=[{"campo": 5, "resumen": "ninguno"}]), con(agrupar_por=[["AnimalGroupName"]]),
                 con(columnas=[{"campo": ["Number"], "resumen": "ninguno"}])]
        for mala in raras:
            r, c = self.armar(respuesta(mala), respuesta(BUENA))
            self.assertTrue(r["ok"], mala)
            self.assertEqual(len(c.llamadas), 2, mala)

    def test_un_error_inesperado_deja_rastro_sin_el_texto_de_la_excepcion(self):
        with mock.patch.object(il, "_resultado", side_effect=KeyError("SECRETO")):
            with self.assertLogs("informes_lenguaje", level="WARNING") as cap:
                with self.assertRaises(il.IAFallo) as cm:
                    self.armar(respuesta(BUENA))
        self.assertNotIn("SECRETO", str(cm.exception))
        texto = "\n".join(cap.output)
        self.assertIn("KeyError", texto)
        self.assertNotIn("SECRETO", texto)


class TestLosValoresPosiblesDeUnTexto(Base):
    def test_el_prompt_da_los_valores_de_un_campo_cerrado(self):
        s = il.sistema(HOY)
        for v in ("Abierta", "Inseminada", "Preñada", "Seca"):
            self.assertIn(v, s)

    def test_el_prompt_no_dice_que_falta_un_dato_que_si_esta(self):
        """«Estado de la reproducción» tiene el valor «Preñada»: no se puede decir al mismo tiempo que no hay datos de preñez."""
        self.assertNotIn("preñez", il.sistema(HOY).split("REGLAS")[1].split("2.")[0])

    def test_un_valor_que_no_es_de_la_lista_se_corrige(self):
        mala = con(condiciones=[cond("CurrentReproductionStatus", "es", "Gestante")])
        buena = con(condiciones=[cond("CurrentReproductionStatus", "es", "Preñada")])
        r, c = self.armar(respuesta(mala), respuesta(buena), frase="vacas preñadas")
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 2)
        self.assertIn("Abierta, Inseminada, Preñada, Seca", c.llamadas[1]["messages"][2]["content"])

    def test_se_acepta_con_otras_mayusculas_y_queda_con_las_del_catalogo(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("CurrentReproductionStatus", "es", "preñada")])), frase="vacas preñadas")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentReproductionStatus", "op": "es", "valor": "Preñada"}])

    def test_en_una_lista_se_revisa_cada_valor(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("CurrentReproductionStatus", "en", "", ["abierta", "Inseminada"])])), frase="vacas abiertas o inseminadas")
        self.assertEqual(r["definicion"]["condiciones"][0]["valor"], ["Abierta", "Inseminada"])
        mala = con(condiciones=[cond("CurrentReproductionStatus", "en", "", ["Abierta", "Vacía"])])
        r, c = self.armar(respuesta(mala), respuesta(BUENA), frase="vacas abiertas o vacías")
        self.assertEqual(len(c.llamadas), 2)

    def test_contiene_no_se_restringe(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("CurrentReproductionStatus", "contiene", "ñad")])), frase="vacas con ñad")
        self.assertTrue(r["ok"])

    def test_un_texto_abierto_no_tiene_lista(self):
        r, _ = self.armar(respuesta(con(condiciones=[cond("AnimalGroupName", "es", "Rodeo 4 - Baja")])), frase="el rodeo 4 - Baja")
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
