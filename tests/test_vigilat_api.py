"""Vigilat: las rutas (configuración, probar, historial, confirmar), la página del enlace, la respuesta por WhatsApp y
los avisos en la Bitácora.

Todo contra archivos temporales: nunca toca la configuración, las bases ni los canales reales.

    C:\\Python314\\python.exe -m unittest tests.test_vigilat_api -v
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

import app
import auth
import bitacora
import tambos
import vigilante_estado as ve
import vigilat_acciones
import vigilat_config as vc
import vigilat_estado
import vigilat_motor

TAMBO = tambos.DEFAULT_TAMBO
PERSONA = {"id": "p1", "nombre": "Juan", "rango": "Encargado", "activa": True, "canales": ["whatsapp"],
           "whatsapp": "+5492392000001", "telegram": "", "correo": ""}


class Base(unittest.TestCase):
    def setUp(self):
        # la bitácora deja sus conexiones para el recolector de basura: en Windows la carpeta puede tardar en soltarse
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.dir.cleanup)
        d = self.dir.name
        for p in (mock.patch.object(ve, "RUTA_DB", os.path.join(d, "v.db")),
                  mock.patch.object(ve, "RUTA_CONFIG", os.path.join(d, "vieja.json")),
                  mock.patch.object(vc, "RUTA", os.path.join(d, "vigilat_config.json")),
                  mock.patch.object(bitacora, "_DB_PATH", os.path.join(d, "bitacora.db"))):
            p.start()
            self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.admin = self.cliente("admin", "maxi")
        self.operario = self.cliente("operario", "pepe")
        self.anonimo = app.app.test_client()

    def cliente(self, rol, usuario):
        c = app.app.test_client()
        with c.session_transaction() as s:
            s["rol"], s["usuario"] = rol, usuario
        return c

    def config(self):
        cfg = vc.por_defecto()
        cfg["personas"] = [dict(PERSONA)]
        return cfg

    def aviso(self, requiere=True, texto="Puesto 21 con fallas", creado=None):
        estado = vigilat_estado.Estado()
        creado = creado or datetime.datetime.now().replace(microsecond=0)
        registro = bitacora.registrar_aviso(TAMBO, texto, creado)
        i = estado.crear_aviso({"creado": creado.isoformat(), "regla_id": "r", "regla_nombre": "Fallas",
                                "tipo": "fallas_unidades", "prioridad": 2, "texto": texto, "audio": "x", "urgente": 0,
                                "requiere_conf": int(requiere), "espera_min": 10, "escalera": [["p:p1"]], "nivel": 1,
                                "vence": None, "estado": "pendiente" if requiere else "avisado"}, [])
        estado.editar_aviso(i, bitacora_id=registro)
        envio = estado.agregar_envio(i, "p1", "Juan", "whatsapp", 1, True, creado)
        estado.marcar_envio(envio, "enviado", ahora=creado, lote="l")
        return estado, i, estado.envios_de(i)[0]["token"]


class TestConfig(Base):
    def test_leer_y_guardar(self):
        r = self.admin.get("/api/vigilat/config")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertFalse(d["config"]["general"]["activo"])
        self.assertEqual(len(d["config"]["reglas"]), 6)
        self.assertEqual(sorted(d["tipos"]), sorted(vc.TIPOS))
        self.assertEqual(set(d["canales"]), {"whatsapp", "telegram", "correo"})
        cfg = self.config()
        cfg["general"]["activo"] = True
        r = self.admin.post("/api/vigilat/config", json=cfg)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(self.admin.get("/api/vigilat/config").get_json()["config"]["general"]["activo"])

    def test_guardar_algo_invalido_es_400_con_el_motivo(self):
        for cuerpo in ({}, [], {**self.config(), "rangos": []}):
            r = self.admin.post("/api/vigilat/config", json=cuerpo)
            self.assertEqual(r.status_code, 400)
            self.assertTrue(r.get_json()["error"])
        self.assertEqual(self.admin.post("/api/vigilat/config", data="x").status_code, 400)
        self.assertFalse(os.path.exists(vc.RUTA))

    def test_la_configuracion_y_las_pruebas_son_solo_del_administrador(self):
        pedidos = (("get", "/api/vigilat/config"), ("post", "/api/vigilat/config"), ("post", "/api/vigilat/probar"),
                   ("post", "/api/vigilat/probar_contacto"), ("post", "/api/vigilat/probar_alarma"),
                   ("post", "/api/vigilat/probar_audio"), ("get", "/api/vigilat/telegram/chats"),
                   ("get", "/api/vigilat/avisos"), ("get", "/api/vigilat/avisos.csv"))
        for metodo, ruta in pedidos:
            with self.subTest(ruta=ruta, quien="operario"):
                self.assertEqual(getattr(self.operario, metodo)(ruta, json={}).status_code, 403)
            with self.subTest(ruta=ruta, quien="anonimo"):
                self.assertIn(getattr(self.anonimo, metodo)(ruta, json={}).status_code, (302, 401, 403))
        for metodo, ruta in (("get", "/api/vigilat/avisos/recientes"), ("post", "/api/vigilat/avisos/1/confirmar")):
            self.assertIn(getattr(self.anonimo, metodo)(ruta).status_code, (302, 401, 403))


class TestProbar(Base):
    def test_probar_usa_lo_que_esta_en_pantalla_y_no_manda_nada(self):
        cfg = self.config()
        with mock.patch.object(vigilat_motor, "probar", return_value=[]) as p, \
                mock.patch.object(vigilat_acciones, "enviar") as env:
            r = self.admin.post("/api/vigilat/probar", json={"config": cfg, "regla": "sin_datos"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(p.call_args.args[0]["personas"][0]["nombre"], "Juan")
        self.assertEqual(p.call_args.kwargs["regla_id"], "sin_datos")
        env.assert_not_called()
        self.assertFalse(os.path.exists(vc.RUTA))
        self.assertFalse(os.path.exists(ve.RUTA_DB))

    def test_probar_una_persona_manda_por_cada_canal_y_cuenta_como_salio(self):
        persona = {**PERSONA, "canales": ["whatsapp", "correo"], "correo": "j@ejemplo.test"}
        with mock.patch.object(vigilat_acciones, "disponibles",
                               return_value={"whatsapp": True, "telegram": False, "correo": False}), \
                mock.patch.object(vigilat_acciones, "enviar") as env:
            r = self.admin.post("/api/vigilat/probar_contacto", json={"persona": persona})
        self.assertEqual([(x["canal"], x["ok"]) for x in r.get_json()["resultados"]],
                         [("whatsapp", True), ("correo", False)])
        self.assertEqual(env.call_args.args[:2], ("whatsapp", "+5492392000001"))
        for malo in (None, "x", {**PERSONA, "whatsapp": "123"}, {**PERSONA, "canales": []}):
            self.assertEqual(self.admin.post("/api/vigilat/probar_contacto", json={"persona": malo}).status_code, 400)

    def test_la_alarma_solo_acciona_una_salida_del_gateway(self):
        with mock.patch.object(app, "_vigilat_salidas", return_value=[{"clave": "do_3", "nombre": "Sirena"}]), \
                mock.patch.object(vigilat_acciones, "sonar_alarma", return_value=(True, "sonó")) as sonar:
            self.assertEqual(self.admin.post("/api/vigilat/probar_alarma", json={"salida": "do_9"}).status_code, 400)
            r = self.admin.post("/api/vigilat/probar_alarma", json={"salida": "do_3"})
        self.assertEqual(r.get_json(), {"ok": True, "detalle": "sonó"})
        sonar.assert_called_once_with("do_3")


class TestEnlace(Base):
    def test_abrir_el_enlace_no_confirma_y_el_boton_si(self):
        estado, i, llave = self.aviso()
        r = self.anonimo.get(f"/vigilat/c/{llave}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Confirmo que lo vi", r.get_data(as_text=True))
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(estado.aviso(i)["estado"], "pendiente")
        r = self.anonimo.post(f"/vigilat/c/{llave}")
        self.assertEqual(r.status_code, 302)
        a = estado.aviso(i)
        self.assertEqual((a["estado"], a["confirmado_por"], a["confirmado_via"]), ("confirmado", "Juan", "enlace"))
        self.assertIn("Confirmado por Juan", self.anonimo.get(f"/vigilat/c/{llave}").get_data(as_text=True))

    def test_llave_mala_o_vencida(self):
        self.aviso()
        for llave in ("corta", "x" * 22):
            self.assertEqual(self.anonimo.post(f"/vigilat/c/{llave}").status_code, 404)
        estado, i, llave = self.aviso(creado=datetime.datetime.now() - datetime.timedelta(days=8))
        self.assertEqual(self.anonimo.post(f"/vigilat/c/{llave}").status_code, 410)
        self.assertEqual(estado.aviso(i)["estado"], "pendiente")

    def test_el_texto_del_aviso_sale_escapado(self):
        _e, _i, llave = self.aviso(texto="<script>alert(1)</script>")
        self.assertNotIn("<script>alert", self.anonimo.get(f"/vigilat/c/{llave}").get_data(as_text=True))

    def test_lo_demas_sigue_pidiendo_sesion(self):
        self.assertIn(self.anonimo.get("/vigilat").status_code, (302, 404))
        self.assertEqual(self.anonimo.get("/api/vigilat/avisos/recientes").status_code, 401)


class TestHistorialYBitacora(Base):
    def test_historial_sin_llaves_y_planilla(self):
        estado, i, llave = self.aviso(texto="=SUMA(1)")
        d = self.admin.get("/api/vigilat/avisos").get_json()
        self.assertEqual(d["avisos"][0]["id"], i)
        self.assertNotIn(llave, self.admin.get("/api/vigilat/avisos").get_data(as_text=True))
        r = self.admin.get("/api/vigilat/avisos.csv")
        texto = r.get_data().decode("utf-8-sig")
        self.assertIn("Minutos hasta confirmar", texto)
        self.assertIn(";'=SUMA(1);", texto)
        self.assertNotIn(llave, texto)
        self.assertEqual(self.admin.get("/api/vigilat/avisos?desde=ayer").status_code, 400)

    def test_el_operario_ve_los_avisos_recientes_y_confirma(self):
        estado, i, _ = self.aviso()
        d = self.operario.get("/api/vigilat/avisos/recientes").get_json()
        self.assertEqual(d["avisos"][0]["estado"], "pendiente")
        self.assertEqual(self.operario.post(f"/api/vigilat/avisos/{i}/confirmar").status_code, 200)
        self.assertEqual(estado.aviso(i)["confirmado_por"], "pepe")
        self.assertEqual(self.operario.post(f"/api/vigilat/avisos/{i}/confirmar").status_code, 409)
        registro = [r for r in bitacora.registros(TAMBO, "2000-01-01", "2100-01-01")][0]
        self.assertIn("Confirmado por pepe (desde LactIA)", registro["descripcion"])

    def test_un_aviso_no_es_un_incidente_abierto(self):
        self.aviso()
        self.assertEqual(self.operario.get("/api/bitacora/abiertos").get_json(), [])
        self.assertEqual(bitacora.abiertos_por_puesto(TAMBO), {})
        with self.assertRaises(ValueError):                 # a mano no se puede cargar un «aviso»
            bitacora.crear_registro(TAMBO, "aviso", "x", None, "y", "pepe", "2026-11-28", "o1")

    def test_lo_que_cargo_una_persona_no_se_reescribe(self):
        r = bitacora.crear_registro(TAMBO, "incidente", "Usher", None, "se trabó", "pepe", "2026-11-28", "o2")
        self.assertFalse(bitacora.anotar(r["id"], "otra cosa"))

    def test_las_pantallas_incluyen_lo_nuevo(self):
        pagina = self.admin.get("/").get_data(as_text=True)
        for parte in ("config-vigilat-card", "👁 Vigilat", "window.cargarVigilat", "cargarVigilat();"):
            self.assertIn(parte, pagina)
        self.assertNotIn("config-vigilante-card", pagina)
        self.assertIn("vigilat-avisos", self.operario.get("/bitacora/").get_data(as_text=True))


class TestWhatsapp(Base):
    def mandar(self, texto, de="whatsapp:+5492392000001"):
        validador = mock.Mock()
        validador.RequestValidator.return_value.validate.return_value = True
        # el paquete `twilio` no tiene por qué estar instalado para correr las pruebas
        with mock.patch.dict(os.environ, {"TWILIO_AUTH_TOKEN": "t"}), \
                mock.patch.dict(sys.modules, {"twilio": mock.Mock(), "twilio.request_validator": validador}), \
                mock.patch.object(app, "_log_webhook"), \
                mock.patch.object(app.whatsapp_ia, "tambo_autorizado", return_value=None) as ia, \
                mock.patch.object(app.threading, "Thread") as hilo:
            r = self.anonimo.post("/webhook/whatsapp", data={"From": de, "Body": texto})
        return r, ia, hilo

    def test_ok_confirma_y_contesta(self):
        vc.guardar(self.config())
        estado, i, _ = self.aviso()
        r, ia, hilo = self.mandar(f"OK {i}")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(estado.aviso(i)["confirmado_via"], "respuesta por WhatsApp")
        ia.assert_not_called()
        self.assertIn("Confirmado el aviso", hilo.call_args.kwargs["args"][1])

    def test_otra_pregunta_sigue_su_camino(self):
        vc.guardar(self.config())
        self.aviso()
        for texto in ("¿cuánta leche hubo?", "ok gracias"):
            _r, ia, _h = self.mandar(texto)
            ia.assert_called_once()


if __name__ == "__main__":
    unittest.main()
