"""Armar un informe a partir de una frase (`informes_lenguaje.py`). TODAS las pruebas usan un cliente de IA de mentira: ninguna llama a la API de
Anthropic (cuesta plata) ni toca una base de datos.

Lo que importa:
  * La IA no decide nada de seguridad: lo que devuelve es una definición que pasa SIEMPRE por `informes_guardados.validar_definicion`
    (compilar). Un campo inventado, un valor con SQL o una clave de más no llegan a ningún lado.
  * Una sola llamada (más UNA repetición con el motivo del error): el costo está acotado, y hay un tope de pedidos por hora.
  * Lo que sale es honesto: se dice en palabras lo que se entendió (armado por el servidor, no por el modelo), lo que no se pudo, los supuestos y los
    campos sin verificar. Una IA que falla, tarda o no está configurada nunca rompe nada: el diseñador manual sigue andando.
  * El modelo no ve SQL: ve el catálogo de campos, los valores posibles de un campo cerrado (el estado de la reproducción) y la frase.

    C:\\Python314\\python.exe -m unittest tests.test_informes_lenguaje -v
"""
import datetime
import json
import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

import agente
import informes_catalogo as cat
import informes_lenguaje as il
import informes_motor as im

HOY = datetime.date(2026, 8, 27)                    # un jueves
FRASE = "vacas del rodeo 3 con más de 200 días en ordeño, ordenadas por producción"

BUENA = {
    "se_puede": True, "nombre": "Vacas del Rodeo 3 con más de 200 días",
    "columnas": [{"campo": "Number", "resumen": "count"}, {"campo": "CurrentDIM", "resumen": "avg"}, {"campo": "SevenDayAverage", "resumen": "ninguno"}],
    "agrupar_por": [],
    "condiciones": [{"campo": "AnimalGroupNumber", "op": "=", "valor": "3", "valores": []}, {"campo": "CurrentDIM", "op": ">", "valor": "200", "valores": []}],
    "orden_campo": "SevenDayAverage", "orden_dir": "desc", "incluir_descartados": False, "solo_resumen": False, "limite": 0,
    "supuestos": [], "lo_que_no_se_pudo": [],
}


def con(**cambios):
    d = json.loads(json.dumps(BUENA))
    d.update(cambios)
    return d


def respuesta(obj, stop="end_turn"):
    texto = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)], stop_reason=stop, usage=SimpleNamespace(input_tokens=900, output_tokens=200))


class Cliente:
    """El doble de `anthropic.Anthropic()`: devuelve lo que se le cargó, en orden, y recuerda cada llamada."""

    def __init__(self, *respuestas):
        self.respuestas, self.llamadas = list(respuestas), []
        self.messages = self

    def create(self, **kw):
        self.llamadas.append(kw)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Base(unittest.TestCase):
    def setUp(self):
        il.reiniciar_tope()
        self.addCleanup(il.reiniciar_tope)
        p = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "clave-de-prueba"})
        p.start()
        self.addCleanup(p.stop)

    def armar(self, *respuestas, frase=FRASE, **kw):
        c = Cliente(*respuestas)
        r = il.armar(frase, hoy=HOY.isoformat(), cliente=c, **kw)
        return r, c


class TestArmarBien(Base):
    def test_una_frase_se_vuelve_una_definicion_valida(self):
        r, c = self.armar(respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertEqual(r["nombre"], "Vacas del Rodeo 3 con más de 200 días")
        self.assertEqual(r["definicion"]["columnas"], [{"campo": "Number", "resumen": "count"}, {"campo": "CurrentDIM", "resumen": "avg"}, {"campo": "SevenDayAverage"}])
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "AnimalGroupNumber", "op": "=", "valor": "3"}, {"campo": "CurrentDIM", "op": ">", "valor": "200"}])
        self.assertEqual(r["definicion"]["orden"], {"campo": "SevenDayAverage", "dir": -1})
        self.assertIs(r["definicion"]["incluir_descartados"], False)
        self.assertEqual(len(c.llamadas), 1)                                       # UNA sola llamada

    def test_la_definicion_corre_con_el_compilador(self):
        r, _ = self.armar(respuesta(BUENA))
        im.compilar(r["definicion"], herd=1)

    def test_lo_que_se_entendio_lo_arma_el_servidor_no_el_modelo(self):
        r, _ = self.armar(respuesta(BUENA))
        self.assertEqual(r["se_lee"][0], "Columnas: Número del animal (conteo), Días en ordeño (promedio), Promedio producción diaria de leche 7 d.")
        self.assertIn("Número del grupo = 3", r["se_lee"])
        self.assertIn("Días en ordeño > 200", r["se_lee"])
        self.assertIn("Ordenado por Promedio producción diaria de leche 7 d., de mayor a menor", r["se_lee"])

    def test_agrupar_y_solo_resumen(self):
        r, _ = self.armar(respuesta(con(agrupar_por=["AnimalGroupName"], solo_resumen=True)))
        self.assertEqual(r["definicion"]["agrupar_por"], ["AnimalGroupName"])
        self.assertIs(r["definicion"]["detalle"], False)
        self.assertIn("Agrupado por Nombre del grupo", r["se_lee"])

    def test_es_uno_de_con_lista(self):
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "AnimalGroupName", "op": "en", "valor": "", "valores": ["Rodeo 1", "Rodeo 2"]}])),
                          frase="animales de los rodeos Rodeo 1 y Rodeo 2")
        self.assertEqual(r["definicion"]["condiciones"], [{"campo": "AnimalGroupName", "op": "en", "valor": ["Rodeo 1", "Rodeo 2"]}])
        self.assertIn("Nombre del grupo es uno de «Rodeo 1», «Rodeo 2»", r["se_lee"])

    def test_fecha_con_el_dia_de_la_semana(self):
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "LastCalvingDate", "op": ">=", "valor": "2026-08-01", "valores": []}])), frase="parieron desde el 1 de agosto")
        self.assertIn("Fecha del último parto ≥ sáb 01/08/2026", r["se_lee"])

    def test_el_nombre_se_limpia(self):
        r, _ = self.armar(respuesta(con(nombre="  Vacas   del\trodeo 3  ")))
        self.assertEqual(r["nombre"], "Vacas del rodeo 3")

    def test_un_nombre_con_un_dia_relativo_se_reemplaza_por_uno_neutro(self):
        for malo in ("Vacas de ayer", "Producción de hoy", "Lo de este mes", "Partos de mañana", "Esta semana", "x" * 80, "", "   "):
            r, _ = self.armar(respuesta(con(nombre=malo)))
            self.assertEqual(r["nombre"], il.NOMBRE_NEUTRO, malo)
        r, _ = self.armar(respuesta(con(nombre="Ordeño de la mañana")))                # «la mañana» es un momento del ordeño, no un día relativo
        self.assertEqual(r["nombre"], "Ordeño de la mañana")
        r, _ = self.armar(respuesta(con(nombre="Vacas\ndel\x00 rodeo 3")))               # los saltos de línea y los caracteres de control se limpian
        self.assertEqual(r["nombre"], "Vacas del rodeo 3")

    def test_los_supuestos_y_lo_que_no_se_pudo_se_muestran_acotados(self):
        r, _ = self.armar(respuesta(con(supuestos=["Tomé «en ordeño» como producción en los últimos 7 días"], lo_que_no_se_pudo=["No existe el campo células somáticas"])))
        self.assertEqual(r["supuestos"], ["Tomé «en ordeño» como producción en los últimos 7 días"])
        self.assertEqual(r["no_pude"], ["No existe el campo células somáticas"])

    def test_el_texto_libre_del_modelo_se_acota_y_se_limpia(self):
        largo = "palabra " * 200
        r, _ = self.armar(respuesta(con(supuestos=[largo, "con\nsalto\x00y control", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"])))
        self.assertLessEqual(len(r["supuestos"]), il.TEXTOS_MAX)
        for t in r["supuestos"]:
            self.assertLessEqual(len(t), il.TEXTO_LARGO_MAX)
            self.assertNotRegex(t, r"[\x00-\x1f]")

    def test_los_dias_relativos_del_modelo_se_escriben_con_fecha_o_se_reemplazan(self):
        """Regla del usuario: nunca «ayer» ni «ese día» en un texto de la app; las fechas llevan el día de la semana. «Ayer» es una fecha que el servidor conoce (se
        escribe con su día de la semana); «ese día» no se sabe cuál es: la línea se reemplaza por una neutra, sin dejar al usuario sin el aviso (ver
        `test_informes_lenguaje_revision`)."""
        r, _ = self.armar(respuesta(con(supuestos=["Tomé lo de ayer", "Usé ese día como base", "El día anterior", "Esto sí queda"],
                                        lo_que_no_se_pudo=["No tengo lo de ayer", "Esto otro queda"])))
        self.assertEqual(r["supuestos"], ["Tomé lo de mié 26/08/2026", "Esto sí queda", il.SUPUESTO_SOBRE_UN_DIA])
        self.assertEqual(r["no_pude"], ["No tengo lo de mié 26/08/2026", "Esto otro queda"])
        for t in r["supuestos"] + r["no_pude"]:
            self.assertNotRegex(t, r"\b(ayer|ese día|el día anterior)\b")

    def test_sin_condiciones_avisa_que_trae_todos_los_animales(self):
        r, _ = self.armar(respuesta(con(condiciones=[])))
        self.assertTrue([a for a in r["advertencias"] if "todos los animales" in a])
        r2, _ = self.armar(respuesta(BUENA))
        self.assertFalse([a for a in r2["advertencias"] if "todos los animales" in a])

    def test_un_limite_con_totales_avisa_que_los_totales_cuentan_a_todos(self):
        r, _ = self.armar(respuesta(con(limite=10)))                                   # BUENA tiene resúmenes (conteo y promedio)
        self.assertEqual(r["definicion"]["limite"], 10)
        self.assertTrue([a for a in r["advertencias"] if "totales" in a])
        r2, _ = self.armar(respuesta(con(limite=10, columnas=[{"campo": "Number", "resumen": "ninguno"}], orden_campo="Number", orden_dir="asc")))
        self.assertFalse([a for a in r2["advertencias"] if "totales" in a])

    def test_los_campos_sin_verificar_salen_marcados(self):
        r, _ = self.armar(respuesta(con(columnas=[{"campo": "Number", "resumen": "ninguno"}, {"campo": "CurrentReproductionStatus", "resumen": "ninguno"}],
                                        orden_campo="Number", orden_dir="asc")))
        self.assertEqual(r["sin_verificar"], ["Estado de la reproducción"])
        self.assertTrue([a for a in r["advertencias"] if "Estado de la reproducción" in a])

    def test_un_numero_que_no_esta_en_la_frase_se_avisa_sin_rechazar(self):
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "CurrentDIM", "op": ">", "valor": "250", "valores": []}])))
        self.assertTrue(r["ok"])
        self.assertTrue([a for a in r["advertencias"] if "250" in a and "no figura" in a])

    def test_los_numeros_de_la_frase_no_se_avisan(self):
        r, _ = self.armar(respuesta(BUENA))
        self.assertEqual([a for a in r["advertencias"] if "no figura" in a], [])

    def test_los_numeros_en_miles_con_coma_o_punto_de_la_frase_valen(self):
        r, _ = self.armar(respuesta(con(condiciones=[{"campo": "SevenDayAverage", "op": ">", "valor": "30.5", "valores": []}])), frase="más de 30,5 kg")
        self.assertEqual([a for a in r["advertencias"] if "no figura" in a], [])

    def test_la_frase_se_limpia_pero_va_como_viene(self):
        r, c = self.armar(respuesta(BUENA), frase="  vacas   del\nrodeo 3  ")
        self.assertEqual(c.llamadas[0]["messages"][0]["content"], "vacas del rodeo 3")
        self.assertEqual(r["frase"], "vacas del rodeo 3")


class TestLaLlamada(Base):
    def test_va_con_el_modelo_del_agente_el_esquema_cerrado_y_un_tiempo_limite(self):
        _, c = self.armar(respuesta(BUENA))
        kw = c.llamadas[0]
        self.assertEqual(kw["model"], agente.MODEL)
        self.assertEqual(kw["timeout"], il._plazo())                                   # conectar en pocos segundos y leer hasta TIMEOUT_S
        self.assertEqual(getattr(kw["timeout"], "read", kw["timeout"]), il.TIMEOUT_S)
        self.assertEqual(kw["max_tokens"], il.MAX_TOKENS)
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(kw["output_config"]["format"]["schema"], il.esquema())
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["messages"], [{"role": "user", "content": FRASE}])

    def test_el_esquema_es_cerrado_y_con_enumeraciones_del_catalogo(self):
        e = il.esquema()
        json.dumps(e)                                                                  # se puede mandar como JSON

        def revisar(nodo):
            if isinstance(nodo, dict):
                if nodo.get("type") == "object":
                    self.assertIs(nodo.get("additionalProperties"), False)
                    self.assertEqual(sorted(nodo["required"]), sorted(nodo["properties"]))      # modo estricto: todo obligatorio
                for v in nodo.values():
                    revisar(v)
            elif isinstance(nodo, list):
                for v in nodo:
                    revisar(v)
        revisar(e)
        self.assertEqual(set(e["properties"]["columnas"]["items"]["properties"]["campo"]["enum"]), set(cat.CATALOGO))
        self.assertEqual(set(e["properties"]["agrupar_por"]["items"]["enum"]), {k for k, f in cat.CATALOGO.items() if f.agrupable})
        self.assertEqual(set(e["properties"]["condiciones"]["items"]["properties"]["op"]["enum"]), {o for ops in im.OPERADORES.values() for o in ops})

    def test_el_prompt_lleva_el_catalogo_las_reglas_y_el_dia_de_hoy(self):
        s = il.sistema(HOY)
        for campo in cat.CATALOGO.values():
            self.assertIn(campo.id, s)
            self.assertIn(campo.etiqueta, s)
        self.assertIn("jue 27/08/2026", s)
        self.assertIn("2026-08-27", s)
        self.assertIn("CurrentReproductionStatus", s)
        self.assertTrue(re.search(r"sin verificar", s, re.I))

    def test_el_modelo_no_ve_sql_ni_datos_internos(self):
        s = il.sistema(HOY)
        for prohibido in ("DATEDIFF", "LEFT JOIN", "AnimalReproductionInfo", "EventCalving", "b.Number", "ag.Name", "r.LactationNumber", "GETDATE", "SELECT"):
            self.assertNotIn(prohibido, s)
        for campo in cat.CATALOGO.values():
            self.assertNotIn(campo.expr, s)
            if campo.nota:
                self.assertNotIn(campo.nota, s)                                        # las notas internas de verificación tampoco

    def test_el_prompt_pide_no_reemplazar_un_campo_que_falta(self):
        s = il.sistema(HOY).lower()
        self.assertIn("no lo reemplaces", s)
        self.assertIn("se_puede", s)

    def test_el_prompt_pide_numeros_sin_separador_de_miles(self):
        self.assertIn("sin separador de miles", il.sistema(HOY))

    def test_sin_hoy_usa_la_fecha_de_la_pc(self):
        c = Cliente(respuesta(BUENA))
        il.armar(FRASE, cliente=c)
        self.assertIn(il._dia_largo(datetime.date.today()), c.llamadas[0]["system"])


class TestSiLaIAFalla(Base):
    def malo(self, *respuestas, **kw):
        with self.assertRaises(il.InformeIAError) as cm:
            self.armar(*respuestas, **kw)
        return cm.exception

    def test_sin_clave_de_api_no_llama_a_nadie(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            c = Cliente(respuesta(BUENA))
            with self.assertRaises(il.SinIA):
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
            self.assertEqual(c.llamadas, [])

    def test_frase_vacia_muy_larga_o_con_caracteres_de_control(self):
        for malo in (None, "", "   ", "x" * (il.FRASE_MAX + 1), 5):
            c = Cliente(respuesta(BUENA))
            with self.assertRaises(il.InformeIAError):
                il.armar(malo, hoy=HOY.isoformat(), cliente=c)
            self.assertEqual(c.llamadas, [], repr(malo)[:20])
        r, _ = self.armar(respuesta(BUENA), frase="vacas\x00 del rodeo 3 con más de 200 días ")        # el control se saca, no se rechaza
        self.assertTrue(r["ok"])

    def test_la_frase_justo_en_el_tope_sirve(self):
        r, _ = self.armar(respuesta(BUENA), frase="a" * il.FRASE_MAX)
        self.assertTrue(r["ok"])

    def test_se_puede_false_explica_sin_inventar(self):
        r, c = self.armar(respuesta({**BUENA, "se_puede": False, "lo_que_no_se_pudo": ["No existe el campo células somáticas"], "columnas": [], "condiciones": []}))
        self.assertFalse(r["ok"])
        self.assertEqual(r["no_pude"], ["No existe el campo células somáticas"])
        self.assertIn("campos", r["motivo"])
        self.assertEqual(len(c.llamadas), 1)                                           # no se repite: el modelo contestó que no se puede

    def test_un_campo_que_no_existe_se_corrige_con_una_repeticion(self):
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 2)
        segunda = c.llamadas[1]["messages"]
        self.assertEqual([m["role"] for m in segunda], ["user", "assistant", "user"])
        self.assertIn("CelulasSomaticas", segunda[2]["content"])                      # le dice qué estuvo mal

    def test_si_la_repeticion_tambien_falla_se_corta_con_un_mensaje_claro(self):
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        e = self.malo(respuesta(mala), respuesta(mala))
        self.assertIn("No pude armar", str(e))
        self.assertNotIn("CelulasSomaticas", str(e).replace("«CelulasSomaticas»", ""))        # (el detalle técnico no se vuelca tal cual)

    def test_nunca_hay_mas_de_dos_llamadas(self):
        mala = con(columnas=[{"campo": "CelulasSomaticas", "resumen": "ninguno"}], orden_campo="Number")
        c = Cliente(respuesta(mala), respuesta(mala), respuesta(BUENA))
        with self.assertRaises(il.InformeIAError):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        self.assertEqual(len(c.llamadas), 2)

    def test_valores_peligrosos_no_pasan(self):
        for valor in ("x'; DROP TABLE BasicAnimal--", "a;b", "a--b", "/* x */", "a\nb"):
            mala = con(condiciones=[{"campo": "AnimalGroupName", "op": "es", "valor": valor, "valores": []}])
            e = self.malo(respuesta(mala), respuesta(mala), frase="rodeo " + valor)
            self.assertNotIn("DROP", str(e))

    def test_un_resumen_que_el_campo_no_admite_se_corrige(self):
        mala = con(columnas=[{"campo": "Number", "resumen": "sum"}], orden_campo="Number", orden_dir="asc")
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertIn("resumen", c.llamadas[1]["messages"][2]["content"])

    def test_ordenar_por_algo_que_no_es_columna_se_corrige(self):
        mala = con(orden_campo="AnimalGroupName")
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertEqual(len(c.llamadas), 2)

    def test_numeros_con_separador_de_miles_se_corrigen(self):
        mala = con(condiciones=[{"campo": "SevenDayAverage", "op": ">", "valor": "1.200", "valores": []}])
        r, c = self.armar(respuesta(mala), respuesta(BUENA))
        self.assertTrue(r["ok"])
        self.assertIn("separador de miles", c.llamadas[1]["messages"][2]["content"])

    def test_json_roto_o_con_texto_de_mas(self):
        for roto in ("{no es json", "", "Claro, acá está: " + json.dumps(BUENA), "[]", "null", "5"):
            r, c = self.armar(respuesta(roto), respuesta(BUENA))
            self.assertTrue(r["ok"], repr(roto)[:30])
            self.assertEqual(len(c.llamadas), 2)

    def test_claves_de_mas_o_de_menos_se_rechazan_no_se_descartan_en_silencio(self):
        con_de_mas = dict(BUENA, filtros=[{"campo": "CurrentDIM"}])
        sin_condiciones = {k: v for k, v in BUENA.items() if k != "condiciones"}
        for malo in (con_de_mas, sin_condiciones):
            r, c = self.armar(respuesta(malo), respuesta(BUENA))
            self.assertTrue(r["ok"])
            self.assertEqual(len(c.llamadas), 2)
        e = self.malo(respuesta(con_de_mas), respuesta(con_de_mas))
        self.assertIsInstance(e, il.InformeIAError)

    def test_una_condicion_con_claves_de_mas_se_rechaza(self):
        malo = con(condiciones=[{"campo": "CurrentDIM", "op": ">", "valor": "200", "valores": [], "sql": "1=1"}])
        r, c = self.armar(respuesta(malo), respuesta(BUENA))
        self.assertEqual(len(c.llamadas), 2)

    def test_cortada_o_rechazada_no_se_repite_ni_se_interpreta(self):
        for stop in ("max_tokens", "refusal"):
            c = Cliente(respuesta(BUENA, stop=stop), respuesta(BUENA))
            with self.assertRaises(il.InformeIAError):
                il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
            self.assertEqual(len(c.llamadas), 1, stop)

    def test_una_respuesta_sin_texto(self):
        vacia = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        r, c = self.armar(vacia, respuesta(BUENA))
        self.assertTrue(r["ok"])

    def test_si_la_api_falla_el_mensaje_no_filtra_detalles(self):
        e = self.malo(RuntimeError("401 invalid x-api-key sk-ant-SECRETO en https://api.anthropic.com"))
        self.assertNotIn("SECRETO", str(e))
        self.assertNotIn("anthropic.com", str(e))
        self.assertIn("No se pudo armar", str(e))

    def test_el_log_tampoco_lleva_el_texto_de_la_excepcion(self):
        with self.assertLogs("informes_lenguaje", level="WARNING") as cap:
            self.malo(RuntimeError("401 invalid x-api-key sk-ant-SECRETO"))
        texto = "\n".join(cap.output)
        self.assertNotIn("SECRETO", texto)
        self.assertIn("RuntimeError", texto)

    def test_si_la_api_falla_no_se_repite(self):
        c = Cliente(RuntimeError("timeout"), respuesta(BUENA))
        with self.assertRaises(il.InformeIAError):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=c)
        self.assertEqual(len(c.llamadas), 1)


class TestTope(Base):
    def test_un_tope_de_pedidos_por_hora_protege_el_gasto(self):
        reloj = [1000.0]
        for _ in range(il.PEDIDOS_POR_HORA):
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: reloj[0])
        c = Cliente(respuesta(BUENA))
        with self.assertRaises(il.InformeIAError) as cm:
            il.armar(FRASE, hoy=HOY.isoformat(), cliente=c, reloj=lambda: reloj[0])
        self.assertIn("muchos pedidos", str(cm.exception))
        self.assertEqual(c.llamadas, [])
        reloj[0] += 3601                                                               # pasó la hora: vuelve a andar
        self.assertTrue(il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)), reloj=lambda: reloj[0])["ok"])

    def test_los_pedidos_que_ni_llegan_a_la_api_no_cuentan(self):
        for _ in range(il.PEDIDOS_POR_HORA + 5):
            with self.assertRaises(il.InformeIAError):
                il.armar("", hoy=HOY.isoformat(), cliente=Cliente())
        self.assertTrue(il.armar(FRASE, hoy=HOY.isoformat(), cliente=Cliente(respuesta(BUENA)))["ok"])


if __name__ == "__main__":
    unittest.main()
