"""Lo que encontró la revisión independiente de «Describí el informe» (Fase 4b, la noche del 24/11/2026; los 9 hallazgos se reprodujeron) y se arregló.

Cada caso salió de una frase o de una respuesta de la IA concretas. Todo con un cliente de IA de mentira: ninguna prueba llama a la API de Anthropic.

Al final, la tanda de la REVISIÓN FINAL de la Fase 4 (24/11/2026, hallazgos «ia #3» a «ia #11»): las fechas contra la frase, los falsos positivos del lector de números, el
turno que se devuelve cuando un pedido falla antes de llegar a la API, los valores de la definición y los días relativos. Cada aviso nuevo tiene una prueba de que NO salta en el
pedido bien armado y otra de que SÍ salta en el mal armado.

    C:\\Python314\\python.exe -m unittest tests.test_informes_lenguaje_revision -v
"""
import datetime
import logging
import re
import unittest
from unittest import mock

import anthropic
import httpx

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
        self.assertIn("Ternera, Vaquillona, Fresca, Inseminada, Abierta, Preñada, Seca, Dada de baja", c.llamadas[1]["messages"][2]["content"])

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
        r, _ = self.armar(respuesta(con(condiciones=[cond("AnimalGroupName", "es", "Rodeo 5 - Alta")])), frase="el rodeo 5 - Alta")
        self.assertTrue(r["ok"])


# ============================================================================================================================================
# Revisión final de «Describí el informe» (24/11/2026): los hallazgos ia #3 a #11 de la revisión independiente de la Fase 4.
# Cada aviso nuevo tiene dos pruebas: que NO salta en el pedido bien armado (un aviso que salta de más se aprende a ignorar y el chip pasa de
# «Listo para revisar» a «revisá estos avisos») y que SÍ salta en el mal armado.
# ============================================================================================================================================

def G(op, valor):
    """Una condición sobre la fecha del último parto."""
    return cond("LastCalvingDate", op, valor)


class BaseAvisos(Base):
    def avisos(self, condiciones, frase, **kw):
        il.reiniciar_tope()                                                    # un caso por pedido: no es la prueba del tope de 20 por hora
        r, _ = self.armar(respuesta(con(condiciones=condiciones, **kw)), frase=frase)
        self.assertTrue(r["ok"], r)
        return r["advertencias"]

    def con_texto(self, condiciones, frase, trozo, **kw):
        return [a for a in self.avisos(condiciones, frase, **kw) if trozo in a]

    def fechas(self, condiciones, frase, **kw):
        return self.con_texto(condiciones, frase, "no sale de lo que escribiste", **kw)

    def sentido(self, condiciones, frase, **kw):
        return self.con_texto(condiciones, frase, "el sentido", **kw)

    def inventados(self, condiciones, frase, **kw):
        return self.con_texto(condiciones, frase, "no figura", **kw)

    def olvidados(self, condiciones, frase, **kw):
        return self.con_texto(condiciones, frase, "no está en las condiciones", **kw)

    def sin_aviso_de_lo_escrito(self, frase, *condiciones, **kw):
        """El pedido bien armado: ningún aviso sobre fechas, sentido, números ni valores."""
        avisos = self.avisos(list(condiciones), frase, **kw)
        malos = [a for a in avisos if any(t in a for t in ("no sale de lo que escribiste", "el sentido", "no figura", "no está en las condiciones",
                                                           "ninguna condición ni columna"))]
        self.assertEqual(malos, [], frase)


class TestIa3LasFechasContraLaFrase(BaseAvisos):
    """ia #3: las fechas de las condiciones se comparan con la frase (con el «hoy» de la consulta, jue 27/08/2026) y el operador con las palabras."""

    def test_el_pedido_bien_armado_no_avisa(self):
        casos = [
            ("vacas que parieron en los últimos 30 días", G(">=", "2026-07-28")),
            ("vacas que parieron en los últimos 30 días", G(">=", "2026-07-29")),                         # tolerancia de un día para cada lado
            ("vacas que parieron en los últimos 30 días", G(">=", "2026-07-27")),
            ("parieron en los últimos 30 días hasta hoy", G(">=", "2026-07-28"), G("<=", "2026-08-27")),
            ("parieron desde el 1 de agosto", G(">=", "2026-08-01")),
            ("parieron desde el 1 de agosto de 2026", G(">=", "2026-08-01")),
            ("parieron desde el 1 de agosto del 2026", G(">=", "2026-08-01")),
            ("parieron desde el 01/08/2026", G(">=", "2026-08-01")),
            ("parieron desde el 1/8/26", G(">=", "2026-08-01")),
            ("parieron desde 2026-08-01", G(">=", "2026-08-01")),
            ("parieron desde el 1° de agosto", G(">=", "2026-08-01")),
            ("parieron desde el 1º de agosto", G(">=", "2026-08-01")),
            ("parieron desde el 1ro de agosto", G(">=", "2026-08-01")),
            ("parieron desde el primero de agosto", G(">=", "2026-08-01")),
            ("parieron desde el 01-08-2026", G(">=", "2026-08-01")),
            ("parieron desde el 1.8.2026", G(">=", "2026-08-01")),
            ("parieron en julio", G(">=", "2026-07-01"), G("<=", "2026-07-31")),
            ("parieron en agosto de 2026", G(">=", "2026-08-01"), G("<=", "2026-08-31")),
            ("parieron en agosto 2026", G(">=", "2026-08-01"), G("<=", "2026-08-31")),
            ("parieron ayer", G(">=", "2026-08-26")),
            ("parieron hoy", G(">=", "2026-08-27")),
            ("parieron anteayer", G(">=", "2026-08-25")),
            ("parieron antes de ayer", G(">=", "2026-08-25")),
            ("vacas que parieron este mes", G(">=", "2026-08-01")),
            ("vacas que parieron el mes pasado", G(">=", "2026-07-01"), G("<=", "2026-07-31")),
            ("vacas que parieron esta semana", G(">=", "2026-08-24")),
            ("vacas que parieron la semana pasada", G(">=", "2026-08-17"), G("<=", "2026-08-23")),
            ("vacas que parieron este año", G(">=", "2026-01-01")),
            ("vacas que parieron el año pasado", G(">=", "2025-01-01"), G("<=", "2025-12-31")),
            ("parieron en los últimos 2 meses", G(">=", "2026-06-27")),                                   # dos meses de calendario...
            ("parieron en los últimos 2 meses", G(">=", "2026-06-28")),                                   # ... o 60 días
            ("parieron en las últimas dos semanas", G(">=", "2026-08-13")),
            ("parieron hace 30 días", G("<=", "2026-07-28")),
            ("parieron en los últimos treinta días", G(">=", "2026-07-28")),
            ("parieron en el último mes", G(">=", "2026-07-27")),
            ("con más de 30 días desde el parto", G("<=", "2026-07-28")),                                 # un plazo escrito como fecha: hoy menos 30
            ("parieron entre el 1 y el 15 de agosto", G(">=", "2026-08-01"), G("<=", "2026-08-15")),
            ("parieron del 1 al 15 de agosto", G(">=", "2026-08-01"), G("<=", "2026-08-15")),
            ("parieron en 2025", G(">=", "2025-01-01"), G("<=", "2025-12-31")),
            ("parieron desde el 28/07", G(">=", "2026-07-28")),
            ("parieron hasta el 15/12", G("<=", "2025-12-15")),                                           # un día sin año que cae después de hoy: el del año pasado...
            ("parieron hasta el 15/12", G("<=", "2026-12-15")),                                           # ... o el de este año
            ("parieron hace entre 20 y 40 días", G(">=", "2026-07-18"), G("<=", "2026-08-07")),
            ("parieron desde el 1 de agosto hasta el 15 de agosto", G(">=", "2026-08-01"), G("<=", "2026-08-15")),
            ("parieron después del 1 de agosto", G(">", "2026-08-01")),
            ("parieron antes del 15 de agosto", G("<", "2026-08-15")),
            ("parieron hasta el 15 de agosto", G("<=", "2026-08-15")),
            ("parieron a partir del 1 de agosto", G(">=", "2026-08-01")),
            ("parieron el 15 de agosto", G("=", "2026-08-15")),
            ("vacas que no parieron desde el 1 de agosto", G("<", "2026-08-01")),                         # una negación da vuelta el sentido: no se juzga
            ("parieron desde hace 30 días", G(">=", "2026-07-28")),
            ("parieron hasta hace 30 días", G("<=", "2026-07-28")),
            ("parieron hasta 30 días atrás", G(">=", "2026-07-28")),                                      # «hasta 30 días atrás» puede ser «en los últimos 30 días»: no se juzga
            ("parieron hasta el mes pasado", G("<=", "2026-07-31")),
        ]
        for frase, *conds in casos:
            self.sin_aviso_de_lo_escrito(frase, *conds)

    def test_una_fecha_que_no_sale_de_la_frase_se_avisa_con_lo_que_si_sale(self):
        for malo in ("2026-06-01", "2025-07-28"):                                                         # 87 días atrás; un año de más
            a = self.fechas([G(">=", malo)], "vacas que parieron en los últimos 30 días")
            self.assertEqual(len(a), 1, malo)
            self.assertIn("Fecha del último parto", a[0])
            self.assertIn("últimos 30 días = mar 28/07/2026", a[0])

    def test_el_aviso_lleva_la_fecha_mala_con_su_dia_de_la_semana(self):
        a = self.fechas([G(">=", "2026-06-01")], "vacas que parieron en los últimos 30 días")
        self.assertIn("lun 01/06/2026", a[0])
        self.assertRegex(a[0], r"\b(lun|mar|mié|jue|vie|sáb|dom) \d\d/\d\d/\d{4}")

    def test_un_anio_equivocado_en_una_fecha_escrita(self):
        a = self.fechas([G(">=", "2025-08-01")], "parieron desde el 1 de agosto")
        self.assertEqual(len(a), 1)
        self.assertIn("1 de agosto = sáb 01/08/2026", a[0])

    def test_una_fecha_a_mas_de_un_dia_de_lo_escrito_se_avisa(self):
        self.assertEqual(len(self.fechas([G(">=", "2026-08-05")], "parieron desde el 1 de agosto")), 1)
        self.assertEqual(len(self.fechas([G(">=", "2026-08-03")], "parieron desde el 1 de agosto")), 1)
        self.assertEqual(self.fechas([G(">=", "2026-08-02")], "parieron desde el 1 de agosto"), [])

    def test_una_fecha_sin_nada_en_la_frase_que_la_explique_se_avisa_sin_pista(self):
        a = self.fechas([G(">=", "2026-07-28")], "vacas recién paridas")
        self.assertEqual(len(a), 1)
        self.assertNotIn("(", a[0])                                                                       # sin nada en la frase, no hay con qué compararla

    def test_avisar_no_cambia_el_informe(self):
        r, _ = self.armar(respuesta(con(condiciones=[G(">=", "2026-06-01")])), frase="vacas que parieron en los últimos 30 días")
        self.assertTrue(r["ok"])
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "LastCalvingDate", "op": ">=", "valor": "2026-06-01"}])

    def test_el_aviso_nunca_escribe_un_dia_relativo(self):
        a = self.fechas([G(">=", "2026-06-01")], "parieron ayer")
        self.assertEqual(len(a), 1)
        self.assertIn("mié 26/08/2026", a[0])
        for frase in ("parieron ayer", "parieron anteayer", "parieron antes de ayer"):
            for t in self.avisos([G(">=", "2026-06-01")], frase):
                self.assertNotRegex(t, r"\b(ayer|anteayer|ese día|el día anterior)\b", frase)

    def test_el_operador_que_contradice_las_palabras_se_avisa(self):
        casos = [("parieron desde el 1 de agosto", G("<=", "2026-08-01"), "desde"),
                 ("parieron desde el 1 de agosto", G("<", "2026-08-01"), "desde"),
                 ("parieron después del 1 de agosto", G("<", "2026-08-01"), "después"),
                 ("parieron a partir del 1 de agosto", G("<=", "2026-08-01"), "a partir de"),
                 ("parieron desde hace 30 días", G("<=", "2026-07-28"), "desde"),
                 ("parieron hasta hace 30 días", G(">=", "2026-07-28"), "hasta"),
                 ("parieron hasta hoy", G(">=", "2026-08-27"), "hasta"),
                 ("parieron hasta el 15 de agosto", G(">=", "2026-08-15"), "hasta"),
                 ("parieron hasta el 15 de agosto", G(">", "2026-08-15"), "hasta"),
                 ("parieron antes del 15 de agosto", G(">", "2026-08-15"), "antes")]
        for frase, c, palabra in casos:
            a = self.sentido([c], frase)
            self.assertEqual(len(a), 1, frase)
            self.assertIn(f"«{palabra}»", a[0])
            self.assertIn("Fecha del último parto", a[0])

    def test_cada_palabra_se_junta_con_su_fecha(self):
        """«Desde el 1 de agosto hasta el 15 de agosto»: el «hasta» no juzga a la fecha del «desde»."""
        self.assertEqual(self.sentido([G(">=", "2026-08-01"), G("<=", "2026-08-15")], "parieron desde el 1 de agosto hasta el 15 de agosto"), [])
        self.assertEqual(len(self.sentido([G("<=", "2026-08-01"), G("<=", "2026-08-15")], "parieron desde el 1 de agosto hasta el 15 de agosto")), 1)

    def test_el_igual_no_contradice_a_nadie(self):
        self.assertEqual(self.sentido([G("=", "2026-08-01")], "parieron desde el 1 de agosto"), [])
        self.assertEqual(self.sentido([G("=", "2026-08-15")], "parieron hasta el 15 de agosto"), [])

    def test_un_fallo_del_chequeo_de_fechas_no_tumba_el_informe(self):
        with mock.patch.object(il, "_avisos_de_fechas", side_effect=KeyError("SECRETO")):
            with self.assertLogs("informes_lenguaje", level="WARNING") as cap:
                r, _ = self.armar(respuesta(con(condiciones=[G(">=", "2026-07-28")])), frase="vacas que parieron en los últimos 30 días")
        self.assertTrue(r["ok"])
        self.assertNotIn("SECRETO", "\n".join(cap.output))


class TestIa4LasFechasEscritasDeOtraFormaNoDisparanElAvisoDeNumeros(BaseAvisos):
    """ia #4: un año o una fecha que no está escrita «D de mes de AAAA» dejaba sus cifras sueltas, y el aviso «el número X no está en las condiciones»
    saltaba en un pedido bien armado."""

    def test_formas_de_escribir_una_fecha(self):
        for frase in ("parieron desde agosto de 2026", "parieron desde agosto 2026", "parieron desde agosto del 2026", "parieron desde el 01-08-2026",
                      "parieron desde el 1.8.2026", "parieron desde el 1/8/2026", "parieron desde el 1° de agosto de 2026", "parieron desde el 1º de agosto del 2026",
                      "parieron desde el 1ro de agosto de 2026", "parieron desde el 1er de agosto", "parieron desde 2026-08-01", "parieron en 2026"):
            self.assertEqual(self.olvidados([G(">=", "2026-08-01")], frase), [], frase)

    def test_las_cifras_de_la_fecha_del_informe_cuentan_como_del_informe(self):
        """Aunque la frase las escriba de una forma que no se reconoce como fecha: el año, el mes y el día de cada fecha de una condición son números del informe."""
        self.assertEqual(self.olvidados([G(">=", "2026-08-01")], "parieron en el año 2026, mes 08, día 1"), [])

    def test_un_numero_que_de_verdad_falta_se_sigue_avisando(self):
        a = self.olvidados([G(">=", "2026-08-01")], "parieron desde el 1 de agosto y con más de 200 días en ordeño")
        self.assertTrue([x for x in a if "200" in x], a)


class TestIa5UnNombreConUnPeriodoRelativo(Base):
    """ia #5: el nombre viaja en el asunto de cada envío programado y las fechas del informe quedan fijas: «últimos 30 días» envejece."""

    def test_los_periodos_relativos_se_reemplazan_por_el_nombre_neutro(self):
        for malo in ("Partos de los últimos 30 días", "Partos últimos 30 días", "Vacas de las últimas 2 semanas", "Parieron en el último mes", "Vacas del mes pasado",
                     "Partos del mes anterior", "Partos de la semana pasada", "Semana actual", "Mes en curso", "Parieron hace 30 días", "Parieron hace dos semanas",
                     "Parieron hace un mes", "Esta quincena", "Quincena actual", "Partos de este año", "Año en curso", "Partos del año pasado", "Partos recientes",
                     "Vacas recién paridas del último año", "Últimos días de lactancia"):
            r, _ = self.armar(respuesta(con(nombre=malo)))
            self.assertEqual(r["nombre"], il.NOMBRE_NEUTRO, malo)

    def test_un_nombre_con_fechas_fijas_o_sin_periodo_se_queda(self):
        for bueno in ("Partos desde jue 30/07/2026", "Vacas del rodeo 3", "Fecha del último parto", "Último control de cada vaca", "Ordeño de la mañana",
                      "Vacas con más de 200 días", "Partos de julio de 2026", "Últimas lactancias"):
            r, _ = self.armar(respuesta(con(nombre=bueno)))
            self.assertEqual(r["nombre"], bueno)

    def test_la_regla_8_del_prompt_pide_fechas_fijas_en_el_nombre(self):
        regla = re.split(r"\n9\. ", re.split(r"\n8\. ", il.sistema(HOY))[1])[0]
        for trozo in ("últimos 30 días", "mes pasado", "semana pasada", "fecha"):
            self.assertIn(trozo, regla)

    def test_el_esquema_tambien_lo_dice(self):
        desc = il.esquema()["properties"]["nombre"]["description"]
        self.assertIn("últimos 30 días", desc)


class TestIa6FalsosPositivosDelLectorDeNumeros(BaseAvisos):
    def test_los_ultimos_7_dias_con_el_campo_de_7_dias(self):
        """El «7» de «Promedio producción diaria de leche 7 d.» es un número del informe."""
        self.sin_aviso_de_lo_escrito("vacas con promedio de los últimos 7 días mayor a 30", cond("SevenDayAverage", ">", "30"))

    def test_los_numeros_pegados_con_coma_o_punto_son_una_lista(self):
        casos = [("vacas de los rodeos 3,4", cond("AnimalGroupNumber", "en", "", ["3", "4"])),
                 ("vacas de los rodeos 2,3,4", cond("AnimalGroupNumber", "en", "", ["2", "3", "4"])),
                 ("vacas de los rodeos 3.4", cond("AnimalGroupNumber", "en", "", ["3", "4"])),
                 ("vacas de los rodeos 3, 4", cond("AnimalGroupNumber", "en", "", ["3", "4"])),
                 ("vacas de lactancia 1,2", cond("CurrentLactationNumber", "en", "", ["1", "2"]))]
        for frase, c in casos:
            self.sin_aviso_de_lo_escrito(frase, c)

    def test_una_lista_pegada_a_la_que_le_falta_un_numero_se_avisa(self):
        self.assertTrue(self.olvidados([cond("AnimalGroupNumber", "=", "3")], "vacas de los rodeos 3,4"))
        self.assertTrue(self.olvidados([cond("AnimalGroupNumber", "en", "", ["2", "3"])], "vacas de los rodeos 2,3,4"))

    def test_un_decimal_sigue_siendo_un_decimal(self):
        self.sin_aviso_de_lo_escrito("más de 30,5 kg", cond("SevenDayAverage", ">", "30.5"))
        self.assertTrue(self.olvidados([cond("SevenDayAverage", ">", "30")], "más de 30,5 kg"))                 # la IA lo redondeó: se avisa

    def test_rodeo_2_barra_3_es_una_lista_no_una_fecha(self):
        self.sin_aviso_de_lo_escrito("vacas de los rodeos 2/3", cond("AnimalGroupNumber", "en", "", ["2", "3"]))
        self.sin_aviso_de_lo_escrito("vacas del rodeo 2/3", cond("AnimalGroupNumber", "en", "", ["2", "3"]))
        self.assertTrue(self.olvidados([cond("AnimalGroupNumber", "=", "2")], "vacas de los rodeos 2/3"))

    def test_una_fecha_con_barra_sigue_siendo_una_fecha(self):
        self.sin_aviso_de_lo_escrito("parieron desde el 15/08", G(">=", "2026-08-15"))

    def test_ordinales_hasta_decimo(self):
        casos = [("sexta", "6"), ("sexto", "6"), ("séptima", "7"), ("octava", "8"), ("novena", "9"), ("décima", "10"), ("décimo", "10"), ("sextas", "6"), ("primeras", "1"),
                 ("segunda", "2"), ("cuarta", "4"), ("quinta", "5")]
        for palabra, n in casos:
            self.sin_aviso_de_lo_escrito(f"vacas de {palabra} lactancia", cond("CurrentLactationNumber", "=", n))

    def test_el_limite_de_uno_cuando_la_frase_dice_la_vaca(self):
        for frase in ("la vaca que más produce", "el animal con más días en ordeño", "la que más produce", "la mejor vaca del rodeo 3"):
            r, _ = self.armar(respuesta(con(condiciones=[], limite=1, columnas=[{"campo": "Number", "resumen": "ninguno"}], orden_campo="Number", orden_dir="desc")),
                              frase=frase)
            self.assertEqual([a for a in r["advertencias"] if "límite" in a], [], frase)

    def test_el_limite_de_uno_sin_esas_palabras_se_sigue_avisando(self):
        r, _ = self.armar(respuesta(con(condiciones=[], limite=1, columnas=[{"campo": "Number", "resumen": "ninguno"}], orden_campo="Number", orden_dir="desc")),
                          frase="las vacas que más producen")
        self.assertEqual([a for a in r["advertencias"] if "límite de 1 fila no figura" in a], ["El límite de 1 fila no figura en lo que escribiste."])

    def test_se_pluraliza_bien(self):
        flat = dict(columnas=[{"campo": "Number", "resumen": "ninguno"}], orden_campo="Number", orden_dir="desc", condiciones=[])
        r, _ = self.armar(respuesta(con(limite=1, **flat)), frase="la vaca que más produce")
        self.assertIn("Se lista la primera fila", r["se_lee"])
        self.assertFalse([x for x in r["se_lee"] if "primeras 1" in x or "1 filas" in x])
        r, _ = self.armar(respuesta(con(limite=10, **flat)), frase="las 10 vacas que más producen")
        self.assertIn("Se listan las primeras 10 filas", r["se_lee"])
        r, _ = self.armar(respuesta(con(limite=25, **flat)), frase="las 10 vacas que más producen")
        self.assertIn("El límite de 25 filas no figura en lo que escribiste.", r["advertencias"])


class TestIa7CondicionOlvidada(BaseAvisos):
    def test_un_numero_con_palabras_tambien_se_exige(self):
        self.assertTrue([a for a in self.olvidados([cond("AnimalGroupNumber", "=", "3")], "vacas del rodeo 3 con más de doscientos días en ordeño") if "doscientos" in a])
        self.assertTrue(self.olvidados([cond("CurrentDIM", ">", "200")], "vacas del rodeo tres con más de doscientos días en ordeño"))     # falta el rodeo
        self.assertTrue(self.olvidados([cond("AnimalGroupNumber", "=", "3")], "más de treinta y cinco días en ordeño"))

    def test_un_numero_con_palabras_que_si_esta_no_se_avisa(self):
        self.sin_aviso_de_lo_escrito("vacas del rodeo tres con más de doscientos días en ordeño", cond("AnimalGroupNumber", "=", "3"), cond("CurrentDIM", ">", "200"))
        self.sin_aviso_de_lo_escrito("vacas de dos a cuatro lactancias", cond("CurrentLactationNumber", ">=", "2"), cond("CurrentLactationNumber", "<=", "4"))

    def test_un_uno_una_o_un_ordinal_no_se_exigen(self):
        """«Un rodeo», «una vaca», «uno de los rodeos» y «primera lactancia» no son una cantidad que haya que ver en las condiciones."""
        for frase in ("vacas de un rodeo con más de 200 días", "una vaca con más de 200 días", "uno de los rodeos con más de 200 días", "vacas de primera lactancia con más de 200 días",
                      "vacas multíparas con más de 200 días"):
            self.assertEqual(self.olvidados([cond("CurrentDIM", ">", "200")], frase), [], frase)

    def test_un_plazo_se_descarta_solo_si_una_fecha_lo_explica(self):
        # «últimos 30 días» con una fecha que sí es hoy menos 30: el 30 es un plazo explicado por la fecha, no una condición olvidada
        self.assertEqual(self.olvidados([G(">=", "2026-07-28")], "vacas que parieron en los últimos 30 días"), [])
        # «más de 200 días en ordeño» con una fecha cualquiera en el informe: antes el 200 se descartaba; ahora hace falta que esté
        a = self.olvidados([G(">=", "2026-08-01")], "vacas que parieron desde el 1 de agosto y con más de 200 días en ordeño")
        self.assertTrue([x for x in a if "200" in x], a)
        # un plazo que la fecha NO explica también se exige
        a = self.olvidados([G(">=", "2026-06-01")], "vacas que parieron en los últimos 30 días")
        self.assertTrue([x for x in a if "30" in x], a)

    def test_un_valor_cerrado_que_la_frase_menciona_y_el_informe_no_tiene(self):
        a = self.con_texto([cond("AnimalGroupNumber", "=", "3")], "vacas preñadas del rodeo 3", "ninguna condición ni columna")
        self.assertEqual(len(a), 1)
        self.assertIn("«Preñada»", a[0])
        self.assertIn("«Estado de la reproducción»", a[0])
        for frase, valor in (("vacas secas del rodeo 3", "Seca"), ("las vacas abiertas del rodeo 3", "Abierta"), ("vaca inseminada del rodeo 3", "Inseminada"),
                             ("un preñado cualquiera del rodeo 3", "Preñada")):
            a = self.con_texto([cond("AnimalGroupNumber", "=", "3")], frase, "ninguna condición ni columna")
            self.assertEqual(len(a), 1, frase)
            self.assertIn(f"«{valor}»", a[0])

    def test_un_valor_cerrado_que_esta_en_una_condicion_columna_o_grupo_no_avisa(self):
        estado = cond("CurrentReproductionStatus", "es", "Preñada")
        self.sin_aviso_de_lo_escrito("vacas preñadas del rodeo 3", cond("AnimalGroupNumber", "=", "3"), estado)
        r, _ = self.armar(respuesta(con(columnas=[{"campo": "Number", "resumen": "ninguno"}, {"campo": "CurrentReproductionStatus", "resumen": "ninguno"}],
                                        condiciones=[cond("AnimalGroupNumber", "=", "3")], orden_campo="Number", orden_dir="asc")), frase="vacas preñadas del rodeo 3")
        self.assertEqual([a for a in r["advertencias"] if "ninguna condición ni columna" in a], [])
        r, _ = self.armar(respuesta(con(agrupar_por=["CurrentReproductionStatus"], solo_resumen=True, condiciones=[cond("AnimalGroupNumber", "=", "3")])),
                          frase="cuántas vacas preñadas hay en el rodeo 3")
        self.assertEqual([a for a in r["advertencias"] if "ninguna condición ni columna" in a], [])

    def test_palabras_que_se_parecen_pero_no_son_el_valor_no_avisan(self):
        for frase in ("vacas del sector 3 con más de 200 días", "vacas de la sección 3 con más de 200 días", "vacas del rodeo 3 con más de 200 días y preñez baja",
                      "vacas del rodeo 3 con más de 200 días según la segunda lactancia"):
            r, _ = self.armar(respuesta(BUENA), frase=frase)
            self.assertEqual([a for a in r["advertencias"] if "ninguna condición ni columna" in a], [], frase)


def _sin_red():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.example.invalid/v1/messages"))


def _con_estado(estado):
    r = httpx.Response(estado, request=httpx.Request("POST", "https://api.example.invalid/v1/messages"))
    if estado == 429:
        return anthropic.RateLimitError("x", response=r, body=None)
    if estado >= 500:
        return anthropic.InternalServerError("x", response=r, body=None)
    return anthropic.APIStatusError("x", response=r, body=None)


class TestIa8ElTurnoDeUnPedidoQueFalla(Base):
    """ia #8: un pedido que falla antes de que la API acepte el trabajo (sin internet, 429, 5xx, 529 de sobrecarga) no puede gastar uno de los 20 turnos por hora."""

    def fallar_20_veces(self, error):
        for _ in range(il.PEDIDOS_POR_HORA):
            with self.assertRaises(il.IAFallo):
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(error()), reloj=lambda: 1000.0)

    def sigue_andando(self):
        c = Cliente(respuesta(BUENA))
        r = il.armar(FRASE, hoy=HOY.isoformat(), cliente=c, reloj=lambda: 1000.0)
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 1)

    def test_sin_internet_devuelve_el_turno(self):
        self.fallar_20_veces(_sin_red)
        self.sigue_andando()

    def test_un_429_un_500_y_un_529_devuelven_el_turno(self):
        for estado in (429, 500, 503, 529):
            il.reiniciar_tope()
            self.fallar_20_veces(lambda: _con_estado(estado))
            self.sigue_andando()

    def test_un_error_con_estado_pero_sin_el_tipo_del_sdk_tambien(self):
        class Falso(Exception):
            status_code = 529
        self.fallar_20_veces(Falso)
        self.sigue_andando()

    def test_un_tiempo_agotado_si_gasta_el_turno(self):
        """Pudo haber trabajado del otro lado: el costo no se puede descartar."""
        timeout = lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.example.invalid/v1/messages"))     # noqa: E731
        self.fallar_20_veces(timeout)
        with self.assertRaises(il.TopeDePedidos):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: 1000.0)

    def test_un_error_desconocido_si_gasta_el_turno(self):
        self.fallar_20_veces(lambda: RuntimeError("raro"))
        with self.assertRaises(il.TopeDePedidos):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: 1000.0)

    def test_una_respuesta_gasta_el_turno_aunque_no_sirva(self):
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        for _ in range(il.PEDIDOS_POR_HORA):
            with self.assertRaises(il.InformeIAError):
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(mala), respuesta(mala)), reloj=lambda: 1000.0)
        with self.assertRaises(il.TopeDePedidos):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: 1000.0)

    def test_si_la_primera_llamada_contesto_el_turno_ya_esta_gastado(self):
        """La respuesta de la primera llamada se pagó: que falle la repetición no lo devuelve."""
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        for _ in range(il.PEDIDOS_POR_HORA):
            with self.assertRaises(il.IAFallo):
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(mala), _sin_red()), reloj=lambda: 1000.0)
        with self.assertRaises(il.TopeDePedidos):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: 1000.0)

    def test_devolver_el_turno_no_borra_los_de_otros_pedidos(self):
        il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: 1000.0)
        with self.assertRaises(il.IAFallo):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(_sin_red()), reloj=lambda: 1000.0)
        self.assertEqual(len(il._PEDIDOS), 1)

    def test_el_mensaje_del_tope_dice_en_cuantos_minutos_se_libera_un_turno(self):
        reloj = [1000.0]
        for _ in range(il.PEDIDOS_POR_HORA):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: reloj[0])

        def mensaje(en_segundos):
            reloj[0] = 1000.0 + en_segundos
            with self.assertRaises(il.TopeDePedidos) as cm:
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: reloj[0])
            return str(cm.exception)
        self.assertIn("en 60 minutos", mensaje(0))
        self.assertIn("en 60 minutos", mensaje(1))                                       # faltan 3.599 s: se redondea hacia arriba
        self.assertIn("en 59 minutos", mensaje(60))
        self.assertIn("en 59 minutos", mensaje(61))
        self.assertIn("en 2 minutos", mensaje(3481))                                     # faltan 119 s
        self.assertIn("en 1 minuto:", mensaje(3541))                                     # faltan 59 s
        self.assertIn("en 1 minuto:", mensaje(3600))                                     # justo en el límite: todavía no se libera
        self.assertNotIn("unos minutos", mensaje(10))
        self.assertIn("muchos pedidos", mensaje(10))

    def test_el_sdk_sigue_sin_reintentar_y_acotado_por_el_tiempo_limite(self):
        """max_retries=0 se queda: el SDK también reintenta un tiempo agotado, y con 1 reintento un pedido colgado esperaría 2 × TIMEOUT_S por llamada
        (hasta 180 s con la repetición), más que el corte de ~100 s del túnel de Cloudflare. El turno devuelto hace gratis el reintento a mano."""
        c = ClienteConOpciones(respuesta(BUENA))
        il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        self.assertEqual(c.opciones, [{"max_retries": 0}])
        self.assertEqual(getattr(c.llamadas[0]["timeout"], "read", c.llamadas[0]["timeout"]), il.TIMEOUT_S)


class TestIa9ValidacionDeValores(Base):
    def test_contiene_sobre_un_campo_cerrado_que_no_es_parte_de_ningun_valor_se_corrige(self):
        for malo in ("gestante", "vacia", "xyz", "Preñadaa"):
            c_mala = con(condiciones=[cond("CurrentReproductionStatus", "contiene", malo)])
            r, c = self.armar(respuesta(c_mala), respuesta(BUENA), frase="vacas con estado " + malo)
            self.assertTrue(r["ok"], malo)
            self.assertEqual(len(c.llamadas), 2, malo)
            texto = c.llamadas[1]["messages"][2]["content"]
            self.assertIn("Ternera, Vaquillona, Fresca, Inseminada, Abierta, Preñada, Seca, Dada de baja", texto, malo)

    def test_contiene_con_parte_de_un_valor_sirve_sin_tildes_ni_mayusculas(self):
        for texto in ("ñad", "PREÑ", "pren", "Pre", "preñada", "seca", " sec ", "NSEM", "bier"):
            r, c = self.armar(respuesta(con(condiciones=[cond("CurrentReproductionStatus", "contiene", texto)])), frase="vacas con " + texto.strip())
            self.assertTrue(r["ok"], texto)
            self.assertEqual(len(c.llamadas), 1, texto)

    def test_contiene_queda_con_la_letra_que_tiene_el_valor_del_catalogo(self):
        """«pren» sin la ñ no encontraría «Preñada» en una base con acentos distintos: se escribe con lo que el campo realmente tiene."""
        r, _ = self.armar(respuesta(con(condiciones=[cond("CurrentReproductionStatus", "contiene", "pren")])), frase="vacas con pren")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "CurrentReproductionStatus", "op": "contiene", "valor": "Preñ"}])

    def test_contiene_en_un_texto_abierto_no_se_restringe(self):
        r, c = self.armar(respuesta(con(condiciones=[cond("AnimalGroupName", "contiene", "cualquier cosa")])), frase="grupos con cualquier cosa")
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 1)

    def test_coma_de_miles_se_rechaza_igual_que_el_punto(self):
        for numero in ("1,200", "1,200,000", "12,345", "-1,200", "1.200", "1.200.000"):
            mala = con(condiciones=[cond("SevenDayAverage", ">", numero)])
            r, c = self.armar(respuesta(mala), respuesta(BUENA))
            self.assertTrue(r["ok"], numero)
            self.assertEqual(len(c.llamadas), 2, numero)
            self.assertIn("separador de miles", c.llamadas[1]["messages"][2]["content"], numero)

    def test_los_decimales_no_se_confunden_con_miles(self):
        for numero in ("30,5", "30.5", "1,5", "0,250", "0.125", "1,50", "12", "1200"):
            r, c = self.armar(respuesta(con(condiciones=[cond("SevenDayAverage", ">", numero)])), frase=f"más de {numero} kg")
            self.assertEqual(len(c.llamadas), 1, numero)

    def test_la_coma_de_miles_se_rechaza_tambien_dentro_de_una_lista(self):
        mala = con(condiciones=[cond("AnimalGroupNumber", "en", "", ["3", "1,200"])])
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertEqual(len(c.llamadas), 2)


class TestIa10DiasRelativosEnLosTextos(Base):
    def test_antes_de_ayer_es_anteayer(self):
        r, _ = self.armar(respuesta(con(supuestos=["Parieron antes de ayer", "Antes de Ayer no hay datos", "Tomé lo de ayer"])))
        self.assertEqual(r["supuestos"], ["Parieron mar 25/08/2026", "mar 25/08/2026 no hay datos", "Tomé lo de mié 26/08/2026"])
        for t in r["supuestos"]:
            self.assertNotIn("antes de", t.lower())

    def test_mas_formas_de_decir_un_dia_que_no_se_sabe_cual_es(self):
        for linea in ("Usé ese dia como base", "Usé ese día como base", "Tomé aquel día", "Pasó el mismo día", "Pasó ese mismo día", "Volvió el día siguiente",
                      "Se midió al día siguiente", "Se tomó el día previo", "Se tomó el día anterior", "Lo del día anterior", "Se midió el Día Siguiente"):
            r, _ = self.armar(respuesta(con(supuestos=[linea])))
            self.assertEqual(r["supuestos"], [il.SUPUESTO_SOBRE_UN_DIA], linea)

    def test_un_dia_concreto_no_se_toca(self):
        for linea in ("Tomé el día 15 de agosto", "Tomé el día completo", "El día de ordeño más largo", "Parieron el jue 27/08/2026"):
            r, _ = self.armar(respuesta(con(supuestos=[linea])))
            self.assertEqual(r["supuestos"], [linea], linea)


class TestIa11ElPromptNoLlevaDatosDelTambo(Base):
    def test_el_ejemplo_de_rodeo_es_inventado(self):
        s = il.sistema(HOY)
        self.assertEqual(re.findall(r"Rodeo \d+ - \w+", s), ["Rodeo 5 - Alta"])         # el único nombre con guion es el inventado: ninguno del tambo

    def test_el_docstring_dice_lo_que_el_modelo_ve(self):
        self.assertNotIn("ni datos del tambo", il.__doc__)
        self.assertIn("valores posibles", il.__doc__)
        self.assertNotIn("sin datos del tambo", il.sistema.__doc__)
        self.assertIn("valores posibles", il.sistema.__doc__)


if __name__ == "__main__":
    unittest.main()
