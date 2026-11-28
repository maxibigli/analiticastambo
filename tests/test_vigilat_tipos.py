"""Vigilat: lo que mira cada tipo de regla, lo que sale por cada canal y las acciones en la sala.

Datos de mentira, sin base, sin red, sin relés y sin audio.

    C:\\Python314\\python.exe -m unittest tests.test_vigilat_tipos -v
"""
import datetime
import os
import unittest
from unittest import mock

import ordeno
import vigilat_acciones as acciones
import vigilat_config as vc
import vigilat_tipos as tipos
import vigilante_reglas as reglas

T = datetime.datetime
D = datetime.date
AHORA = T(2026, 11, 28, 9, 0)
GENERAL = {"silencio_desde": "22:00", "silencio_hasta": "05:00"}


def ordenos(hasta=AHORA, kg_ultimo=1000.0):
    """Tres ordeños por día durante 8 días (00:30, 08:00, 16:00), 100 vacas y 1000 kg; el último terminado es el de las
    00:30 de `hasta`."""
    salida = []
    for atras in range(8, -1, -1):
        dia = hasta.date() - datetime.timedelta(days=atras)
        for sesion, hora in ((1, 0), (2, 8), (3, 16)):
            ini = T.combine(dia, datetime.time(hora, 30))
            fin = ini + datetime.timedelta(hours=3)
            if fin > hasta:
                continue
            kg = 1000.0 + (atras % 3) * 5
            if dia == hasta.date() and sesion == 1:
                kg = kg_ultimo
            salida.append(reglas.armar_ordeno(sesion, 100, kg, ini, fin))
    return salida


class Fuentes:
    tambo = "ponderosa"

    def __init__(self, **datos):
        self.datos = datos

    def ordenos(self, desde, hasta):
        return self.datos.get("ordenos", ordenos())

    def vacas(self, tambo, d0, d1):
        return self.datos.get("vacas", [])

    def causas(self, ordeno_, historial):
        return ["Rodeo 3: faltaron 4 vacas habituales (120 kg)"]

    def comida(self, hoy):
        return self.datos.get("comida")

    def incidencias(self):
        return self.datos.get("incidencias", [])

    def produccion(self, desde, hasta):
        return self.datos.get("produccion", [])

    def nombres_rodeos(self, oids):
        return {7: "Rodeo 3"}

    def reportados(self):
        return self.datos.get("reportados", {})

    def redactar(self, h, ahora):
        return f"{h['tipo']} ordeño {h['datos']['sesion']}"


def evaluar(tipo, fuentes, ahora=AHORA, ya=(), modo=None, estado_datos="ok", **params):
    regla = vc.regla_nueva(tipo, "r1")
    regla["parametros"].update(params)
    if modo:
        regla["cuando"]["modo"] = modo
    ctx = tipos.Contexto(fuentes, ahora, GENERAL, ya=lambda k: k in ya, datos_estado=estado_datos)
    return tipos.evaluar(regla, ctx)


class TestOrdeno(unittest.TestCase):
    def test_sin_datos_avisa_una_vez_y_despues_que_volvieron(self):
        cortado = Fuentes(ordenos=ordenos(T(2026, 11, 27, 20, 0)))
        h, _ = evaluar("sin_datos", cortado, horas=4)
        self.assertEqual(h[0]["datos_estado"], "sin_datos")
        self.assertIn("Sin datos de ordeño desde las 19:30 del viernes 27/11", h[0]["texto"])
        self.assertEqual(evaluar("sin_datos", cortado, estado_datos="sin_datos", horas=4)[0], [])
        entrando = reglas.armar_ordeno(2, 30, 300, T(2026, 11, 28, 8, 5), T(2026, 11, 28, 8, 50))
        h, _ = evaluar("sin_datos", Fuentes(ordenos=ordenos() + [entrando]), estado_datos="sin_datos", horas=4)
        self.assertEqual((h[0]["datos_estado"], h[0]["cierra"], h[0]["confirmable"]), ("ok", True, False))
        self.assertEqual(evaluar("sin_datos", Fuentes(), horas=4)[0], [])

    def test_baja_del_ordeno_respeta_el_porcentaje_y_no_repite(self):
        fuentes = Fuentes(ordenos=ordenos(kg_ultimo=900.0))
        h, _ = evaluar("baja_ordeno", fuentes, pct=4)
        self.assertEqual(h[0]["claves"], ["r1|2026-11-28|1"])
        self.assertIn("10 por ciento", h[0]["audio"])
        self.assertEqual(evaluar("baja_ordeno", fuentes, pct=15)[0], [])
        self.assertEqual(evaluar("baja_ordeno", fuentes, ya={"r1|2026-11-28|1"}, pct=4)[0], [])
        # lo que ya avisó el Vigilante anterior no se vuelve a avisar al actualizar
        self.assertEqual(evaluar("baja_ordeno", fuentes, ya={"2026-11-28|1|baja_ordeno"}, pct=4)[0], [])

    def test_con_los_datos_cortados_no_se_analiza_la_produccion(self):
        fuentes = Fuentes(ordenos=ordenos(kg_ultimo=900.0))
        h, nota = evaluar("baja_ordeno", fuentes, estado_datos="sin_datos", pct=4)
        self.assertEqual(h, [])
        self.assertIn("cortados", nota)

    def test_vacas_faltantes_usa_el_porcentaje_de_la_regla(self):
        filas = []
        for atras in range(8):
            dia = (AHORA.date() - datetime.timedelta(days=atras)).isoformat()
            for vaca in range(1, 101):
                if atras == 0 and vaca <= 3:
                    continue
                filas.append((dia, 1, vaca, 7, 10.0))
        fuentes = Fuentes(ordenos=ordenos(kg_ultimo=970.0), vacas=filas)
        h, _ = evaluar("vacas_faltantes", fuentes, pct=1)
        self.assertIn("faltaron 3 vacas", h[0]["audio"])
        self.assertEqual(evaluar("vacas_faltantes", fuentes, pct=5)[0], [])

    def test_comida_atrasada(self):
        comida = {"fecha": "2026-11-28", "primera": 480, "habitual": 390, "retraso": 90}
        h, _ = evaluar("comida_atrasada", Fuentes(comida=comida), minutos=60)
        self.assertEqual(h[0]["claves"], ["r1|2026-11-28"])
        self.assertIn("90 minutos más tarde", h[0]["texto"])
        self.assertEqual(evaluar("comida_atrasada", Fuentes(comida=comida), minutos=120)[0], [])
        self.assertEqual(evaluar("comida_atrasada", Fuentes(comida=comida), ya={"2026-11-28|comida"}, minutos=60)[0], [])


class TestFallas(unittest.TestCase):
    def filas(self, ultima="2026-11-28 08:40:00"):
        base = [(p, 0, 0, 1, 0, ultima) for p in range(1, 21)]
        base[3] = (4, 5, 2, 0, 3, ultima)         # puesto 4: 10
        base[8] = (9, 0, 0, 1, 4, ultima)         # puesto 9: 5
        base[11] = (12, 3, 0, 0, 0, ultima)       # puesto 12: 3
        return base

    def test_nombra_los_puestos_con_sus_fallas_y_lo_ya_reportado(self):
        h, _ = evaluar("fallas_unidades", Fuentes(incidencias=self.filas(), reportados={9: "2026-11-26"}),
                       minimo=4, veces_mediana=2.5)
        texto = h[0]["texto"]
        self.assertIn("2 puesto(s) con 4 o más en las 24 h hasta el sábado 28/11 08:40", texto)
        self.assertIn("Puesto 4: 10 (5 deslizamientos, 2 patadas, 3 recolocaciones)", texto)
        self.assertIn("Puesto 9: 5 (1 bloqueo, 4 recolocaciones) — ya reportado el jueves 26/11", texto)
        self.assertNotIn("Puesto 12", texto)
        self.assertEqual(h[0]["audio"], "Fallas en los puestos 4 y 9.")
        self.assertEqual(h[0]["claves"], [])      # a horas fijas se manda la foto completa cada vez

    def test_cada_tantos_minutos_no_repite_el_puesto_en_el_dia(self):
        fuentes = Fuentes(incidencias=self.filas())
        h, _ = evaluar("fallas_unidades", fuentes, modo="cada", ya={"r1|2026-11-28|4"}, minimo=4)
        self.assertEqual(h[0]["claves"], ["r1|2026-11-28|9"])
        self.assertNotIn("Puesto 4:", h[0]["texto"])

    def test_la_mediana_sube_el_umbral(self):
        filas = [(p, 4, 0, 0, 0, "2026-11-28 08:40:00") for p in range(1, 21)]
        h, nota = evaluar("fallas_unidades", Fuentes(incidencias=filas), minimo=4, veces_mediana=2.5)
        self.assertEqual(h, [])
        self.assertIn("10 fallas", nota)

    def test_datos_viejos_o_sin_rotativa_no_avisan(self):
        for filas in ([], self.filas("2026-11-25 08:40:00")):
            with self.assertRaises(tipos.SinDatos):
                evaluar("fallas_unidades", Fuentes(incidencias=filas))

    def test_la_consulta_es_solo_lectura_y_usa_las_constantes_de_la_pantalla_de_ordeno(self):
        import db
        sql = tipos.sql_incidencias()
        db.validate_sql(sql)
        self.assertIn(f"BETWEEN 1 AND {ordeno.PUESTOS}", sql)


class TestBajaProduccion(unittest.TestCase):
    def filas(self, hasta=D(2026, 11, 27)):
        filas = []
        for atras in range(8):
            dia = (hasta - datetime.timedelta(days=atras)).isoformat()
            for vaca in range(1, 51):
                kg = 40.0
                if atras == 0 and vaca == 1:
                    kg = 20.0                     # −50 %
                if atras == 0 and vaca == 2:
                    kg = 32.0                     # −20 %
                if atras == 0 and vaca == 3:
                    kg = 36.0                     # −10 %
                if vaca == 4:
                    kg = 8.0 if atras else 2.0    # venía dando poco
                filas.append((dia, vaca, 1000 + vaca, 7, 100, 2, kg, True))
        # el día en curso, a medio cargar: no cuenta
        filas += [(D(2026, 11, 28).isoformat(), v, 1000 + v, 7, 100, 2, 5.0, True) for v in range(1, 11)]
        filas.append((hasta.isoformat(), 99, 0, 7, 0, 0, 500.0, True))       # el comodín de leche sin identificar
        return filas

    def test_cada_vaca_contra_su_propio_promedio_en_el_ultimo_dia_completo(self):
        h, _ = evaluar("baja_produccion", Fuentes(produccion=self.filas()), pct=15, kg_min=10, max_vacas=10)
        texto = h[0]["texto"]
        self.assertIn("2 vacas bajaron más de 15 % el viernes 27/11", texto)
        self.assertIn("RP 1001: −50 % (40 → 20 kg, Rodeo 3)", texto)
        self.assertLess(texto.index("RP 1001"), texto.index("RP 1002"))
        for no in ("RP 1003", "RP 1004", "RP 0:"):
            self.assertNotIn(no, texto)
        self.assertEqual(h[0]["claves"], ["r1|2026-11-27"])
        self.assertEqual(evaluar("baja_produccion", Fuentes(produccion=self.filas()), ya={"r1|2026-11-27"})[0], [])

    def test_recorta_la_lista_y_dice_cuantas_quedan(self):
        h, _ = evaluar("baja_produccion", Fuentes(produccion=self.filas()), max_vacas=1)
        self.assertIn("… y 1 más", h[0]["texto"])

    def test_sin_dias_o_con_datos_viejos_no_avisa(self):
        for filas in ([], self.filas(D(2026, 11, 20))[:-11]):
            with self.assertRaises(tipos.SinDatos):
                evaluar("baja_produccion", Fuentes(produccion=filas))


class Gateway:
    class SalidaOcupada(Exception):
        pass

    def __init__(self, respuestas=(), ocupada=False, se_cancela=True):
        self.respuestas, self.ocupada, self.se_cancela = list(respuestas), ocupada, se_cancela
        self.cancelados = []

    def probar_salida(self, salida):
        if self.ocupada:
            raise self.SalidaOcupada("la usa el lavado")
        return 7

    def resultado_pulso(self, pedido):
        return self.respuestas.pop(0) if self.respuestas else {"ejecutado": False, "resultado": None}

    def cancelar_pulso(self, pedido):
        self.cancelados.append(pedido)
        return self.se_cancela


class TestAcciones(unittest.TestCase):
    def test_alarma(self):
        ok = Gateway([{"ejecutado": False, "resultado": None}, {"ejecutado": True, "resultado": "ok"}])
        self.assertEqual(acciones.sonar_alarma("do_3", dormir=lambda s: None, gateway=ok), (True, "sonó"))
        self.assertFalse(acciones.sonar_alarma("", gateway=ok)[0])
        bien, detalle = acciones.sonar_alarma("do_3", dormir=lambda s: None, gateway=Gateway(ocupada=True))
        self.assertFalse(bien)
        self.assertIn("ocupada", detalle)

    def test_si_el_gateway_no_contesta_el_pulso_se_cancela(self):
        mudo = Gateway()
        bien, detalle = acciones.sonar_alarma("do_3", espera_s=3, dormir=lambda s: None, gateway=mudo)
        self.assertEqual((bien, mudo.cancelados), (False, [7]))
        self.assertIn("se canceló", detalle)

    def test_el_audio_no_pega_el_texto_al_comando(self):
        llamadas = []

        def correr(comando, **kw):
            llamadas.append((comando, kw))
            return mock.Mock(returncode=0)

        texto = "Rodeo ’; Remove-Item C:\\ ‘ y '$(calc)'\nfin"
        self.assertTrue(acciones.hablar(texto, correr=correr)[0])
        comando, kw = llamadas[0]
        self.assertNotIn("Remove-Item", " ".join(comando))
        self.assertIn("Remove-Item", kw["env"]["LACTIA_TEXTO_VOZ"])
        self.assertFalse(acciones.hablar("x", correr=lambda *a, **k: mock.Mock(returncode=1))[0])

    def test_mensajes_largos_se_parten_y_una_falla_despues_de_la_primera_no_se_repite(self):
        texto = "\n".join(f"renglón número {i}" for i in range(300))
        partes = acciones.partir(texto, 1500)
        self.assertTrue(all(len(p) <= 1500 for p in partes))
        self.assertEqual("\n".join(partes), texto)
        self.assertEqual([len(p) for p in acciones.partir("a" * 3500, 1500)], [1500, 1500, 500])

        class Falla(Exception):
            no_salio, reintentable = True, True

        enviados = []

        def enviar(mensaje, destino=None):
            if enviados:
                raise Falla()
            enviados.append(mensaje)

        with mock.patch.object(acciones.whatsapp, "enviar", enviar), self.assertRaises(Falla) as e:
            acciones.enviar("whatsapp", "+549000000000", texto)
        self.assertEqual((e.exception.no_salio, e.exception.reintentable), (False, False))

    def test_cada_canal_recibe_el_destino_de_la_persona(self):
        with mock.patch.object(acciones.telegram_bot, "enviar") as tg, \
                mock.patch.object(acciones.correo, "enviar") as em:
            acciones.enviar("telegram", "123", "hola")
            acciones.enviar("correo", "a@b.test", "hola")
        tg.assert_called_once_with("hola", destino="123")
        em.assert_called_once_with("hola", asunto="Vigilat — aviso del tambo", destino="a@b.test")


class TestCanales(unittest.TestCase):
    def test_telegram_y_correo_usan_el_destino_pedido_o_el_de_siempre(self):
        import correo
        import telegram_bot
        entorno = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "SMTP_HOST": "h", "SMTP_USUARIO": "u",
                   "SMTP_PASSWORD": "p", "SMTP_DESTINO": "siempre@b.test"}
        with mock.patch.dict(os.environ, entorno), \
                mock.patch.object(telegram_bot.requests, "post", return_value=mock.Mock(status_code=200)) as post:
            telegram_bot.enviar("hola", destino="999")
            telegram_bot.enviar("hola")
            self.assertEqual([c.kwargs["data"]["chat_id"] for c in post.call_args_list], ["999", "1"])
            with mock.patch.object(correo, "_mandar") as mandar:
                correo.enviar("hola", destino="persona@b.test")
                correo.enviar("hola")
            self.assertEqual([c.args[5] for c in mandar.call_args_list], ["persona@b.test", "siempre@b.test"])

    def test_chats_de_telegram(self):
        import telegram_bot
        novedades = {"result": [{"message": {"chat": {"id": 5, "first_name": "Juan", "last_name": "P"}}},
                                {"message": {"chat": {"id": 5, "first_name": "Juan", "last_name": "P"}}},
                                {"my_chat_member": {"chat": {"id": -9, "title": "Tambo"}}}, {"poll": {}}]}
        respuesta = mock.Mock(status_code=200)
        respuesta.json.return_value = novedades
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t"}), \
                mock.patch.object(telegram_bot.requests, "get", return_value=respuesta):
            self.assertEqual(telegram_bot.chats_recientes(), [{"id": "5", "nombre": "Juan P"},
                                                              {"id": "-9", "nombre": "Tambo"}])


if __name__ == "__main__":
    unittest.main()
