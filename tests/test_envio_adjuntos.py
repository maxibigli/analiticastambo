"""Mandar un archivo adjunto por correo y por Telegram (`correo.enviar_con_adjunto`, `telegram_bot.enviar_documento`): lo que usa el envío programado
de informes para llevar el Excel. SMTP y `requests` son de mentira: ninguna prueba manda nada de verdad.

    C:\\Python314\\python.exe -m unittest tests.test_envio_adjuntos -v
"""
import email
import email.header
import http.client
import os
import smtplib
import unittest
from unittest import mock

import requests
import urllib3.exceptions

import correo
import telegram_bot
import whatsapp

XLSX = b"PK\x03\x04" + bytes(range(256)) * 4
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ENTORNO_SMTP = {"SMTP_HOST": "smtp.ejemplo.test", "SMTP_PORT": "587", "SMTP_USUARIO": "lactia@ejemplo.test", "SMTP_PASSWORD": "clave-de-prueba",
                "SMTP_DESTINO": "dueno@ejemplo.test"}
ENTORNO_TG = {"TELEGRAM_BOT_TOKEN": "123:token-de-prueba", "TELEGRAM_CHAT_ID": "-1001"}
ENTORNO_WA = {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "token-twilio", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886", "WHATSAPP_TELEFONO": "+5493415551234"}


class TestCorreoConAdjunto(unittest.TestCase):
    def mandar(self, **kw):
        smtp = mock.MagicMock()
        smtp.__enter__.return_value = smtp
        with mock.patch.dict(os.environ, ENTORNO_SMTP), mock.patch.object(correo.smtplib, "SMTP", return_value=smtp) as clase:
            correo.enviar_con_adjunto(kw.pop("asunto", "Informe de prueba"), kw.pop("texto", "Resumen\ndel informe"), kw.pop("nombre", "Informe 2026-08-27.xlsx"),
                                      kw.pop("datos", XLSX), **kw)
        return clase, smtp

    def test_el_mensaje_lleva_el_texto_y_el_archivo_intacto(self):
        clase, smtp = self.mandar()
        desde, destinos, cuerpo = smtp.sendmail.call_args.args
        self.assertEqual((desde, destinos), ("lactia@ejemplo.test", ["dueno@ejemplo.test"]))
        msg = email.message_from_string(cuerpo)
        self.assertEqual(msg["Subject"], "Informe de prueba")
        self.assertEqual(msg["To"], "dueno@ejemplo.test")
        partes = [p for p in msg.walk() if not p.is_multipart()]
        texto = [p for p in partes if p.get_content_type() == "text/plain"][0]
        self.assertIn("Resumen", texto.get_payload(decode=True).decode("utf-8"))
        adjunto = [p for p in partes if p.get_filename()][0]
        self.assertEqual(adjunto.get_filename(), "Informe 2026-08-27.xlsx")
        self.assertEqual(adjunto.get_content_type(), MIME)
        self.assertEqual(adjunto.get_payload(decode=True), XLSX)                       # los bytes del Excel llegan iguales

    def test_un_adjunto_necesita_mas_tiempo_que_un_texto(self):
        clase, _ = self.mandar()
        self.assertGreater(clase.call_args.kwargs["timeout"], 15)

    def test_el_texto_plano_pierde_los_asteriscos_y_el_semaforo_como_en_los_otros_mensajes(self):
        _, smtp = self.mandar(texto="*Informe* 🟢 listo")
        msg = email.message_from_string(smtp.sendmail.call_args.args[2])
        texto = [p for p in msg.walk() if p.get_content_type() == "text/plain"][0].get_payload(decode=True).decode("utf-8")
        self.assertIn("Informe [OK] listo", texto)

    def test_un_nombre_de_archivo_con_acentos_y_espacios(self):
        _, smtp = self.mandar(nombre="Producción del rodeo 3 2026-08-27.xlsx")
        msg = email.message_from_string(smtp.sendmail.call_args.args[2])
        adjunto = [p for p in msg.walk() if p.get_filename()][0]
        self.assertEqual(adjunto.get_filename(), "Producción del rodeo 3 2026-08-27.xlsx")

    def test_el_asunto_con_acentos_no_se_rompe(self):
        _, smtp = self.mandar(asunto="Producción — Rodeo 3")
        msg = email.message_from_string(smtp.sendmail.call_args.args[2])
        self.assertEqual(str(email.header.make_header(email.header.decode_header(msg["Subject"]))), "Producción — Rodeo 3")

    def test_sin_credenciales_levanta_un_error_de_correo(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENTORNO_SMTP}):
            with self.assertRaises(correo.CorreoError):
                correo.enviar_con_adjunto("a", "b", "c.xlsx", XLSX)

    def test_si_el_servidor_falla_es_un_error_de_correo(self):
        with mock.patch.dict(os.environ, ENTORNO_SMTP), mock.patch.object(correo.smtplib, "SMTP", side_effect=OSError("sin red")):
            with self.assertRaises(correo.CorreoError):
                correo.enviar_con_adjunto("a", "b", "c.xlsx", XLSX)

    def test_lo_de_siempre_sigue_igual(self):
        smtp = mock.MagicMock()
        smtp.__enter__.return_value = smtp
        with mock.patch.dict(os.environ, ENTORNO_SMTP), mock.patch.object(correo.smtplib, "SMTP", return_value=smtp) as clase:
            correo.enviar("hola")
        self.assertEqual(clase.call_args.kwargs["timeout"], 15)
        asunto = email.message_from_string(smtp.sendmail.call_args.args[2])["Subject"]
        self.assertEqual(str(email.header.make_header(email.header.decode_header(asunto))), "LactIA — Alerta")


class TestTelegramDocumento(unittest.TestCase):
    def mandar(self, estado=200, **kw):
        respuesta = mock.Mock(status_code=estado, text="cuerpo de la respuesta")
        with mock.patch.dict(os.environ, ENTORNO_TG), mock.patch.object(telegram_bot.requests, "post", return_value=respuesta) as post:
            telegram_bot.enviar_documento(kw.pop("nombre", "Informe.xlsx"), kw.pop("datos", XLSX), kw.pop("texto", "Resumen del informe"), **kw)
        return post

    def test_manda_el_archivo_con_el_resumen_como_texto_al_pie(self):
        post = self.mandar()
        self.assertEqual(post.call_args.args[0], "https://api.telegram.org/bot123:token-de-prueba/sendDocument")
        kw = post.call_args.kwargs
        self.assertEqual(kw["data"], {"chat_id": "-1001", "caption": "Resumen del informe"})
        nombre, datos, mime = kw["files"]["document"]
        self.assertEqual((nombre, datos, mime), ("Informe.xlsx", XLSX, MIME))
        self.assertGreater(kw["timeout"], 15)

    def test_el_pie_se_acota_al_maximo_de_telegram(self):
        post = self.mandar(texto="x" * 3000)
        caption = post.call_args.kwargs["data"]["caption"]
        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(caption.endswith("…"))

    def test_si_telegram_rechaza_es_un_error_de_telegram_sin_el_token(self):
        with self.assertRaises(telegram_bot.TelegramError) as cm:
            self.mandar(estado=400)
        self.assertNotIn("token-de-prueba", str(cm.exception))

    def test_sin_credenciales(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENTORNO_TG}):
            with self.assertRaises(telegram_bot.TelegramError):
                telegram_bot.enviar_documento("a.xlsx", XLSX, "t")

    def test_un_error_de_red_es_un_error_de_telegram_sin_el_token(self):
        with mock.patch.dict(os.environ, ENTORNO_TG), mock.patch.object(telegram_bot.requests, "post", side_effect=OSError("https://api.telegram.org/bot123:token-de-prueba/sendDocument")):
            with self.assertRaises(telegram_bot.TelegramError) as cm:
                telegram_bot.enviar_documento("a.xlsx", XLSX, "t")
        self.assertNotIn("token-de-prueba", str(cm.exception))                         # la URL de la API lleva el token: no puede filtrarse en el error

    def test_lo_de_siempre_sigue_igual(self):
        respuesta = mock.Mock(status_code=200, text="")
        with mock.patch.dict(os.environ, ENTORNO_TG), mock.patch.object(telegram_bot.requests, "post", return_value=respuesta) as post:
            telegram_bot.enviar("hola")
        self.assertTrue(post.call_args.args[0].endswith("/sendMessage"))
        self.assertEqual(post.call_args.kwargs["data"], {"chat_id": "-1001", "text": "hola"})


def conexion_fallida():
    """`requests` cuando ni siquiera pudo conectar (sin red, DNS, rechazo): el pedido NO llegó al servidor."""
    razon = urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection")
    return requests.exceptions.ConnectionError(urllib3.exceptions.MaxRetryError(None, "/x", razon))


def conexion_cortada():
    """La conexión se cortó DESPUÉS de mandar el pedido: el servidor pudo haberlo recibido y procesado."""
    return requests.exceptions.ConnectionError(urllib3.exceptions.ProtocolError("Connection aborted.", http.client.RemoteDisconnected("Remote end closed connection without response")))


class TestSeSabeSiSalio(unittest.TestCase):
    """Lo que decide si un envío programado se puede reintentar: solo lo que se SABE que no salió. Un fallo después de que el canal aceptó el mensaje (una consulta
    de estado, un tiempo de lectura, un corte de la conexión) no prueba que no salió: reintentarlo lo duplicaba hasta 3 veces (revisión del 24/11/2026)."""

    def chequear(self, exc, no_salio, reintentable):
        self.assertIs(getattr(exc, "no_salio", None), no_salio, repr(exc))
        self.assertIs(getattr(exc, "reintentable", None), reintentable, repr(exc))

    # --- Telegram
    def telegram(self, enviar_fn, **post):
        with mock.patch.dict(os.environ, ENTORNO_TG), mock.patch.object(telegram_bot.requests, "post", **post):
            with self.assertRaises(telegram_bot.TelegramError) as cm:
                enviar_fn()
        return cm.exception

    def test_telegram_sin_conexion_no_salio_y_se_puede_reintentar(self):
        for exc in (requests.exceptions.ConnectTimeout("t"), conexion_fallida()):
            self.chequear(self.telegram(lambda: telegram_bot.enviar_documento("a.xlsx", XLSX, "t"), side_effect=exc), True, True)
            self.chequear(self.telegram(lambda: telegram_bot.enviar("hola"), side_effect=exc), True, True)

    def test_telegram_un_corte_o_un_tiempo_de_lectura_son_inciertos(self):
        for exc in (requests.exceptions.ReadTimeout("t"), conexion_cortada(), OSError("raro")):
            self.chequear(self.telegram(lambda: telegram_bot.enviar_documento("a.xlsx", XLSX, "t"), side_effect=exc), False, False)
            self.chequear(self.telegram(lambda: telegram_bot.enviar("hola"), side_effect=exc), False, False)

    def test_telegram_un_rechazo_no_salio_pero_no_se_reintenta_y_un_5xx_es_incierto(self):
        self.chequear(self.telegram(lambda: telegram_bot.enviar_documento("a.xlsx", XLSX, "t"), return_value=mock.Mock(status_code=400, text="x")), True, False)
        self.chequear(self.telegram(lambda: telegram_bot.enviar("hola"), return_value=mock.Mock(status_code=403, text="x")), True, False)
        self.chequear(self.telegram(lambda: telegram_bot.enviar_documento("a.xlsx", XLSX, "t"), return_value=mock.Mock(status_code=502, text="x")), False, False)

    def test_telegram_sin_credenciales_no_salio_y_no_se_reintenta(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENTORNO_TG}):
            with self.assertRaises(telegram_bot.TelegramError) as cm:
                telegram_bot.enviar("hola")
        self.chequear(cm.exception, True, False)

    def test_telegram_el_error_de_enviar_tampoco_lleva_el_token(self):
        e = self.telegram(lambda: telegram_bot.enviar("hola"), side_effect=OSError("https://api.telegram.org/bot123:token-de-prueba/sendMessage"))
        self.assertNotIn("token-de-prueba", str(e))

    # --- WhatsApp (Twilio)
    def whatsapp(self, **kw):
        with mock.patch.dict(os.environ, ENTORNO_WA), mock.patch.object(whatsapp.time, "sleep"):
            return whatsapp.enviar("hola", **kw)

    def test_whatsapp_sin_conexion_no_salio_y_se_puede_reintentar(self):
        for exc in (requests.exceptions.ConnectTimeout("t"), conexion_fallida()):
            with mock.patch.object(whatsapp.requests, "post", side_effect=exc), self.assertRaises(whatsapp.WhatsappError) as cm:
                self.whatsapp()
            self.chequear(cm.exception, True, True)

    def test_whatsapp_un_corte_o_un_tiempo_de_lectura_son_inciertos(self):
        for exc in (requests.exceptions.ReadTimeout("t"), conexion_cortada()):
            with mock.patch.object(whatsapp.requests, "post", side_effect=exc), self.assertRaises(whatsapp.WhatsappError) as cm:
                self.whatsapp()
            self.chequear(cm.exception, False, False)

    def test_whatsapp_un_rechazo_no_salio_y_solo_el_limite_de_velocidad_se_reintenta(self):
        for estado, reintentable in ((400, False), (401, False), (429, True)):
            with mock.patch.object(whatsapp.requests, "post", return_value=mock.Mock(status_code=estado, text="x")), self.assertRaises(whatsapp.WhatsappError) as cm:
                self.whatsapp()
            self.chequear(cm.exception, True, reintentable)
        with mock.patch.object(whatsapp.requests, "post", return_value=mock.Mock(status_code=500, text="x")), self.assertRaises(whatsapp.WhatsappError) as cm:
            self.whatsapp()
        self.chequear(cm.exception, False, False)

    def test_whatsapp_si_twilio_ya_acepto_el_mensaje_un_fallo_de_la_consulta_de_estado_no_es_un_error(self):
        """El mensaje ya está encolado: avisar un fallo hacía que el envío programado lo reintentara y se duplicara (3 mensajes por un horario)."""
        aceptado = mock.Mock(status_code=201)
        aceptado.json.return_value = {"sid": "SM123"}
        for falla in (requests.exceptions.ConnectionError("x"), requests.exceptions.ReadTimeout("x"), OSError("x")):
            with mock.patch.object(whatsapp.requests, "post", return_value=aceptado), mock.patch.object(whatsapp.requests, "get", side_effect=falla):
                self.whatsapp()                                                          # no levanta
        estado_roto = mock.Mock(status_code=200)
        estado_roto.json.side_effect = ValueError("no es json")
        with mock.patch.object(whatsapp.requests, "post", return_value=aceptado), mock.patch.object(whatsapp.requests, "get", return_value=estado_roto):
            self.whatsapp()
        respuesta_rota = mock.Mock(status_code=201)
        respuesta_rota.json.side_effect = ValueError("no es json")
        with mock.patch.object(whatsapp.requests, "post", return_value=respuesta_rota):
            self.whatsapp()

    def test_whatsapp_un_mensaje_que_twilio_dice_que_fallo_no_salio(self):
        aceptado = mock.Mock(status_code=201)
        aceptado.json.return_value = {"sid": "SM123"}
        estado = mock.Mock(status_code=200)
        estado.json.return_value = {"status": "undelivered", "error_code": 63016, "error_message": "x"}
        with mock.patch.object(whatsapp.requests, "post", return_value=aceptado), mock.patch.object(whatsapp.requests, "get", return_value=estado), \
                self.assertRaises(whatsapp.WhatsappError) as cm:
            self.whatsapp()
        self.chequear(cm.exception, True, False)

    def test_whatsapp_sin_credenciales_no_salio_y_no_se_reintenta(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENTORNO_WA}), self.assertRaises(whatsapp.WhatsappError) as cm:
            whatsapp.enviar("hola")
        self.chequear(cm.exception, True, False)

    def test_whatsapp_el_error_de_conexion_no_lleva_la_url_ni_el_telefono(self):
        with mock.patch.object(whatsapp.requests, "post", side_effect=OSError("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json whatsapp:+5493415551234")), \
                self.assertRaises(whatsapp.WhatsappError) as cm:
            self.whatsapp()
        self.assertNotIn("AC123", str(cm.exception))
        self.assertNotIn("5493415551234", str(cm.exception))

    # --- correo
    def smtp(self, **comportamiento):
        s = mock.MagicMock()
        s.__enter__.return_value = s
        for k, v in comportamiento.items():
            getattr(s, k).side_effect = v
        return s

    def mandar_correo(self, s=None, clase_efecto=None):
        with mock.patch.dict(os.environ, ENTORNO_SMTP):
            if clase_efecto is not None:
                with mock.patch.object(correo.smtplib, "SMTP", side_effect=clase_efecto):
                    return correo.enviar("hola")
            with mock.patch.object(correo.smtplib, "SMTP", return_value=s):
                return correo.enviar("hola")

    def test_correo_sin_conexion_o_con_la_clave_mal_no_salio(self):
        with self.assertRaises(correo.CorreoError) as cm:
            self.mandar_correo(clase_efecto=OSError("sin red"))
        self.chequear(cm.exception, True, True)                                          # ni conectó: reintentar no puede duplicar
        with self.assertRaises(correo.CorreoError) as cm:
            self.mandar_correo(self.smtp(login=smtplib.SMTPAuthenticationError(535, b"mal")))
        self.chequear(cm.exception, True, False)                                          # la clave mal es siempre igual: reintentar solo sirve para que Gmail bloquee

    def test_correo_un_rechazo_del_servidor_no_salio_y_no_se_reintenta(self):
        for exc in (smtplib.SMTPRecipientsRefused({"x@y.z": (550, b"no")}), smtplib.SMTPSenderRefused(550, b"no", "a@b.c"), smtplib.SMTPDataError(554, b"no")):
            with self.assertRaises(correo.CorreoError) as cm:
                self.mandar_correo(self.smtp(sendmail=exc))
            self.chequear(cm.exception, True, False)

    def test_correo_una_desconexion_mientras_manda_es_incierta(self):
        with self.assertRaises(correo.CorreoError) as cm:
            self.mandar_correo(self.smtp(sendmail=smtplib.SMTPServerDisconnected("se cortó")))
        self.chequear(cm.exception, False, False)

    def test_correo_un_error_al_cerrar_despues_de_aceptado_no_es_un_fallo(self):
        """`sendmail` ya volvió bien: el servidor aceptó el mensaje. Un QUIT que falla después no lo desmanda."""
        s = self.smtp()
        s.__exit__.side_effect = smtplib.SMTPServerDisconnected("se cortó al cerrar")
        self.mandar_correo(s)                                                            # no levanta
        s.sendmail.assert_called_once()

    def test_correo_el_asunto_se_puede_elegir_en_un_mensaje_de_texto(self):
        s = self.smtp()
        with mock.patch.dict(os.environ, ENTORNO_SMTP), mock.patch.object(correo.smtplib, "SMTP", return_value=s):
            correo.enviar("hola", asunto="LactIA — Vacas por días (jue 27/08/2026)")
            correo.enviar("hola")
        asuntos = [str(email.header.make_header(email.header.decode_header(email.message_from_string(c.args[2])["Subject"]))) for c in s.sendmail.call_args_list]
        self.assertEqual(asuntos, ["LactIA — Vacas por días (jue 27/08/2026)", "LactIA — Alerta"])

    def test_correo_sin_credenciales_no_salio_y_no_se_reintenta(self):
        with mock.patch.dict(os.environ, {k: "" for k in ENTORNO_SMTP}), self.assertRaises(correo.CorreoError) as cm:
            correo.enviar("hola")
        self.chequear(cm.exception, True, False)


if __name__ == "__main__":
    unittest.main()
