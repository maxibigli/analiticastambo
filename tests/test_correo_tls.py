"""El correo (`correo.py`) verifica el certificado del servidor SMTP (24/11/2026).

Antes `starttls()` se llamaba SIN contexto, y en Python eso NO verifica nada: la contraseña de la cuenta y cada mensaje (incluido el Excel de un informe programado) viajaban sin comprobar con
quién se hablaba. Estas pruebas cuidan que ahora se verifique la cadena y el nombre del servidor, que el error sea claro y no se reintente solo, y que apagar la verificación sea una decisión
explícita (`SMTP_VERIFICAR_CERTIFICADO=0`).

Dos tipos de prueba:
  * con un SMTP de mentira (siempre corren): el contexto que se arma y lo que se hace con cada error;
  * con un SERVIDOR SMTP REAL de prueba, local y con un certificado propio generado en el momento con `openssl` (se saltan si no hay `openssl`): el handshake TLS de verdad, con
    certificado de confianza, sin firmar, de otro nombre, y con la verificación apagada.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_correo_tls -v
"""
import base64
import io
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from email.mime.text import MIMEText
from unittest import mock

import correo

OPENSSL = shutil.which("openssl") or next((p for p in (r"C:\Program Files\Git\mingw64\bin\openssl.exe", r"C:\Program Files\Git\usr\bin\openssl.exe", "/usr/bin/openssl",
                                                       "/usr/local/bin/openssl") if os.path.exists(p)), None)


def mensaje():
    m = MIMEText("hola", "plain", "utf-8")
    m["Subject"], m["From"], m["To"] = "prueba", "yo@ejemplo.invalid", "vos@ejemplo.invalid"
    return m


def error_de_certificado():
    e = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1032)")
    e.verify_code, e.verify_message = 18, "self-signed certificate"
    return e


class SMTPFalso:
    """Un `smtplib.SMTP` de mentira: anota lo que se le pide y puede fallar en el paso que se elija."""
    instancias = []

    def __init__(self, host, port, timeout=None, falla_en=None, error=None):
        self.host, self.port, self.llamadas, self.contexto = host, port, [], None
        self.falla_en, self.error = SMTPFalso.falla_en, SMTPFalso.error
        SMTPFalso.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, *, context=None):
        self.llamadas.append("starttls")
        self.contexto = context
        if self.falla_en == "starttls":
            raise self.error

    def login(self, u, p):
        self.llamadas.append("login")

    def sendmail(self, de, para, texto):
        self.llamadas.append("sendmail")

    falla_en = None
    error = None


class TestElContexto(unittest.TestCase):
    def test_por_defecto_verifica_la_cadena_y_el_nombre_del_servidor(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMTP_VERIFICAR_CERTIFICADO", None)
            ctx = correo._contexto_tls()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_solo_se_apaga_con_un_no_explicito(self):
        for valor in ("0", "no", "NO", "false", "False", "off", " 0 ", "falso"):
            with mock.patch.dict(os.environ, {"SMTP_VERIFICAR_CERTIFICADO": valor}):
                ctx = correo._contexto_tls()
            self.assertEqual((ctx.verify_mode, ctx.check_hostname), (ssl.CERT_NONE, False), valor)
        for valor in ("1", "", "si", "sí", "true", "quizás", "2", "x"):                      # cualquier otra cosa (incluida una errata) deja la verificación PUESTA
            with mock.patch.dict(os.environ, {"SMTP_VERIFICAR_CERTIFICADO": valor}):
                ctx = correo._contexto_tls()
            self.assertEqual((ctx.verify_mode, ctx.check_hostname), (ssl.CERT_REQUIRED, True), repr(valor))

    def test_apagarla_deja_un_aviso_una_sola_vez(self):
        correo._avisado_sin_verificar = False
        with mock.patch.dict(os.environ, {"SMTP_VERIFICAR_CERTIFICADO": "0"}):
            salida = io.StringIO()
            with redirect_stdout(salida):
                correo._contexto_tls()
                correo._contexto_tls()
        texto = salida.getvalue()
        self.assertEqual(texto.count("SMTP_VERIFICAR_CERTIFICADO"), 1)
        self.assertIn("sin verificar", texto)

    def test_encendida_no_avisa_nada(self):
        correo._avisado_sin_verificar = False
        with mock.patch.dict(os.environ, {"SMTP_VERIFICAR_CERTIFICADO": "1"}):
            salida = io.StringIO()
            with redirect_stdout(salida):
                correo._contexto_tls()
        self.assertEqual(salida.getvalue(), "")

    def test_suma_los_certificados_de_certifi_a_los_del_sistema_y_anda_sin_certifi(self):
        """Un equipo cuyo almacén de Windows todavía no bajó una raíz (pasa en un servidor que casi no navega) no tiene por qué dejar de mandar correo."""
        with mock.patch.object(ssl.SSLContext, "load_verify_locations", autospec=True) as carga:
            correo._contexto_tls()
        archivos = [c.kwargs.get("cafile") for c in carga.call_args_list]
        self.assertTrue(any(a and a.endswith(".pem") for a in archivos), archivos)
        with mock.patch.dict("sys.modules", {"certifi": None}):                                      # `import certifi` falla
            ctx = correo._contexto_tls()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


class TestMandar(unittest.TestCase):
    def setUp(self):
        SMTPFalso.instancias.clear()
        SMTPFalso.falla_en = SMTPFalso.error = None
        self.addCleanup(lambda: setattr(SMTPFalso, "falla_en", None))
        p = mock.patch.object(correo.smtplib, "SMTP", SMTPFalso)
        p.start()
        self.addCleanup(p.stop)

    def mandar(self, host="smtp.ejemplo.invalid"):
        correo._mandar(mensaje(), host, 587, "yo@ejemplo.invalid", "CLAVE-SECRETA-123", "vos@ejemplo.invalid")

    def test_starttls_va_con_un_contexto_que_verifica(self):
        self.mandar()
        s = SMTPFalso.instancias[0]
        self.assertEqual(s.llamadas, ["starttls", "login", "sendmail"])
        self.assertIsInstance(s.contexto, ssl.SSLContext)
        self.assertEqual((s.contexto.verify_mode, s.contexto.check_hostname), (ssl.CERT_REQUIRED, True))

    def test_un_certificado_rechazado_es_un_error_claro_que_no_salio_y_no_se_reintenta(self):
        SMTPFalso.falla_en, SMTPFalso.error = "starttls", error_de_certificado()
        with self.assertRaises(correo.CorreoError) as c:
            self.mandar("smtp.gmail.com")
        e = c.exception
        self.assertTrue(e.no_salio)                                                   # se SABE que no salió: falló antes de entrar
        self.assertFalse(e.reintentable)                                              # y volver a probar da lo mismo hasta que se arregle el certificado
        texto = str(e)
        self.assertIn("smtp.gmail.com", texto)
        self.assertIn("certificado", texto.lower())
        self.assertIn("self-signed certificate", texto)                              # el motivo que dio OpenSSL
        self.assertIn("INSTALL.md", texto)
        self.assertNotIn("CLAVE-SECRETA-123", texto)
        self.assertEqual(SMTPFalso.instancias[0].llamadas, ["starttls"])               # nunca llegó a mandar la contraseña

    def test_el_resto_de_los_errores_de_antes_de_entrar_siguen_siendo_reintentables(self):
        SMTPFalso.falla_en, SMTPFalso.error = "starttls", ConnectionResetError("se cortó")
        with self.assertRaises(correo.CorreoError) as c:
            self.mandar()
        self.assertTrue(c.exception.no_salio)
        self.assertTrue(c.exception.reintentable)


@unittest.skipUnless(OPENSSL, "no hay openssl para generar el certificado de prueba")
class TestConUnServidorDeVerdad(unittest.TestCase):
    """Un handshake TLS REAL: un servidor SMTP mínimo en 127.0.0.1 con un certificado propio (no firmado por nadie)."""

    @classmethod
    def setUpClass(cls):
        cls.carpeta = tempfile.TemporaryDirectory()
        cls.cert, cls.clave = os.path.join(cls.carpeta.name, "cert.pem"), os.path.join(cls.carpeta.name, "clave.pem")
        r = subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", cls.clave, "-out", cls.cert, "-days", "2",
                            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost", "-addext", "basicConstraints=critical,CA:TRUE",
                            "-addext", "keyUsage=critical,digitalSignature,keyCertSign"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(cls.cert):
            cls.carpeta.cleanup()
            raise unittest.SkipTest("openssl no pudo generar el certificado de prueba: " + (r.stderr or "")[-200:])

    @classmethod
    def tearDownClass(cls):
        cls.carpeta.cleanup()

    def levantar(self):
        servidor = ServidorSMTP(self.cert, self.clave)
        self.addCleanup(servidor.cerrar)
        return servidor

    def mandar(self, servidor, host="localhost", **entorno):
        base = {"SSL_CERT_FILE": "", "SMTP_VERIFICAR_CERTIFICADO": "1"}
        base.update(entorno)
        with mock.patch.dict(os.environ, base):
            correo._mandar(mensaje(), host, servidor.puerto, "yo@ejemplo.invalid", "clave-de-prueba", "vos@ejemplo.invalid", timeout=10)

    def test_un_certificado_que_nadie_firmo_se_rechaza_y_no_se_manda_nada(self):
        s = self.levantar()
        with self.assertRaises(correo.CorreoError) as c:
            self.mandar(s)
        self.assertTrue(c.exception.no_salio)
        self.assertFalse(c.exception.reintentable)
        self.assertIn("certificado", str(c.exception).lower())
        s.terminar()
        self.assertEqual(s.mensajes, [])
        self.assertEqual(s.autenticaciones, 0)                                         # ni siquiera llegó a mandar la contraseña

    def test_con_el_certificado_en_SSL_CERT_FILE_el_mensaje_sale(self):
        """La salida para un servidor propio o un proxy con certificado propio: instalarlo en Windows, o decir dónde está con la variable estándar de OpenSSL."""
        s = self.levantar()
        self.mandar(s, SSL_CERT_FILE=self.cert)
        s.terminar()
        self.assertEqual(len(s.mensajes), 1)
        self.assertIn("Subject: prueba", s.mensajes[0])

    def test_el_certificado_tiene_que_ser_del_servidor_al_que_se_llamo(self):
        """El certificado es de «localhost»: aunque se confíe en él, hablarle a 127.0.0.1 NO coincide con el nombre."""
        s = self.levantar()
        with self.assertRaises(correo.CorreoError) as c:
            self.mandar(s, host="127.0.0.1", SSL_CERT_FILE=self.cert)
        self.assertTrue(c.exception.no_salio)
        s.terminar()
        self.assertEqual(s.mensajes, [])

    def test_apagada_a_proposito_manda_igual(self):
        s = self.levantar()
        salida = io.StringIO()
        correo._avisado_sin_verificar = False
        with redirect_stdout(salida):
            self.mandar(s, SMTP_VERIFICAR_CERTIFICADO="0")
        s.terminar()
        self.assertEqual(len(s.mensajes), 1)
        self.assertIn("sin verificar", salida.getvalue())


class ServidorSMTP:
    """Un servidor SMTP mínimo con STARTTLS, solo para probar el handshake: atiende UNA conexión, anota qué llegó y cierra."""

    def __init__(self, cert, clave):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.puerto = self.sock.getsockname()[1]
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, clave)
        self.mensajes, self.autenticaciones, self.error = [], 0, None
        self.hilo = threading.Thread(target=self._atender, daemon=True)
        self.hilo.start()

    def _atender(self):
        try:
            self.sock.settimeout(15)
            con, _ = self.sock.accept()
            con.settimeout(15)
            self._conversar(con)
        except Exception as e:  # noqa: BLE001  -- un handshake rechazado por el cliente termina acá: es lo que se prueba
            self.error = e
        finally:
            try:
                self.sock.close()
            except OSError:
                pass

    @staticmethod
    def _linea(con, buf):
        while b"\r\n" not in buf[0]:
            parte = con.recv(4096)
            if not parte:
                return None
            buf[0] += parte
        linea, _, buf[0] = buf[0].partition(b"\r\n")
        return linea.decode("utf-8", "replace")

    def _conversar(self, con):
        buf, tls = [b""], False
        con.sendall(b"220 localhost ESMTP prueba\r\n")
        while True:
            linea = self._linea(con, buf)
            if linea is None:
                return
            orden = linea.upper()
            if orden.startswith(("EHLO", "HELO")):
                con.sendall(b"250-localhost\r\n" + (b"" if tls else b"250-STARTTLS\r\n") + b"250 AUTH PLAIN\r\n")
            elif orden == "STARTTLS":
                con.sendall(b"220 Listo para TLS\r\n")
                con, tls, buf = self.ctx.wrap_socket(con, server_side=True), True, [b""]
            elif orden.startswith("AUTH PLAIN"):
                self.autenticaciones += 1
                con.sendall(b"235 2.7.0 ok\r\n")
            elif orden.startswith(("MAIL FROM", "RCPT TO")):
                con.sendall(b"250 ok\r\n")
            elif orden == "DATA":
                con.sendall(b"354 termina con un punto\r\n")
                cuerpo = []
                while True:
                    l = self._linea(con, buf)
                    if l is None or l == ".":
                        break
                    cuerpo.append(l)
                self.mensajes.append("\n".join(cuerpo))
                con.sendall(b"250 ok\r\n")
            elif orden == "QUIT":
                con.sendall(b"221 chau\r\n")
                return
            else:
                con.sendall(b"502 no\r\n")

    def terminar(self):
        self.hilo.join(timeout=15)

    def cerrar(self):
        try:
            self.sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
