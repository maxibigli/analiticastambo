"""Los endpoints del envío programado de informes y su conexión con la app: despacho por canal, arranque desde `servidor.py`.

Solo administrador. Ninguna prueba manda nada de verdad (canales de mentira), ni calcula contra una base real, ni toca `informes.db`.

    C:\\Python314\\python.exe -m unittest tests.test_informes_envios_api -v
"""
import datetime
import os
import re
import tempfile
import threading
import unittest
from unittest import mock

import app
import config_alertas
import correo
import envio_red
import informes_envios as ie
import informes_guardados as ig
import informes_motor as im
import telegram_bot
import whatsapp
from tests.test_informes_envios import resultado

DEF = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.anon = app.app.test_client()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "maxi"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.enviados = []
        self.disponibles = ["telegram", "correo", "whatsapp"]
        for p in (mock.patch.object(ig, "RUTA_DB", os.path.join(self.dir.name, "informes.db")),
                  mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"),
                  mock.patch.object(app, "_enviar_informe_a_canal", lambda canal, armado: self.enviados.append((canal, armado))),
                  mock.patch.object(app, "_ids_de_canales_disponibles", lambda: list(self.disponibles)),
                  mock.patch.object(im, "ejecutar", lambda defn, tambo, **kw: resultado())):
            p.start()
            self.addCleanup(p.stop)
        self.informe = ig.crear("ponderosa", "Vacas por días", DEF, "maxi")
        self.url = f"/api/informes/{self.informe['id']}/envio"
        self.cuerpo = {"dias": [3], "horas": ["08:30"], "canales": ["telegram", "correo"], "formato": "excel"}


class TestPermisos(Base):
    def test_todo_es_solo_para_administradores(self):
        pedidos = [("get", self.url), ("put", self.url), ("get", self.url + "/registro"), ("post", self.url + "/probar"), ("get", "/api/informes/envios")]
        for metodo, url in pedidos:
            self.assertEqual(getattr(self.anon, metodo)(url, **({"json": {}} if metodo in ("put", "post") else {})).status_code, 401, url)
            self.assertEqual(getattr(self.operario, metodo)(url, **({"json": {}} if metodo in ("put", "post") else {})).status_code, 403, url)
        self.assertEqual(self.enviados, [])


class TestProgramar(Base):
    def test_sin_programacion_dice_que_canales_hay(self):
        j = self.admin.get(self.url).get_json()
        self.assertIsNone(j["envio"])
        ids = {c["id"]: c for c in j["canales"]}
        self.assertEqual(set(ids), {"whatsapp", "telegram", "correo"})
        self.assertTrue(ids["telegram"]["disponible"])
        self.assertIn("nombre", ids["telegram"])

    def test_un_canal_sin_configurar_figura_como_no_disponible(self):
        self.disponibles = ["telegram"]
        ids = {c["id"]: c for c in self.admin.get(self.url).get_json()["canales"]}
        self.assertFalse(ids["correo"]["disponible"])
        self.assertTrue(ids["telegram"]["disponible"])

    def test_guarda_y_devuelve_el_proximo_envio_con_el_dia_de_la_semana(self):
        r = self.admin.put(self.url, json=self.cuerpo)
        self.assertEqual(r.status_code, 200)
        e = r.get_json()["envio"]
        self.assertEqual((e["dias"], e["horas"], e["canales"], e["formato"], e["activo"]), ([3], ["08:30"], ["correo", "telegram"], "excel", True))
        self.assertRegex(e["proximo_texto"], r"^[a-zéáíóú]{3} \d{2}/\d{2}/\d{4} 08:30$")
        self.assertEqual(self.admin.get(self.url).get_json()["envio"]["horas"], ["08:30"])

    def test_pausar_y_reanudar(self):
        self.admin.put(self.url, json=self.cuerpo)
        e = self.admin.put(self.url, json=dict(self.cuerpo, activo=False)).get_json()["envio"]
        self.assertFalse(e["activo"])
        self.assertIsNone(e["proximo_texto"])
        self.assertTrue(self.admin.put(self.url, json=dict(self.cuerpo, activo=True)).get_json()["envio"]["activo"])

    def test_lo_que_no_sirve_es_400_con_el_motivo(self):
        for malo in ({"dias": []}, {"horas": ["8:30"]}, {"canales": []}, {"formato": "pdf"}, {"canales": ["sms"]}):
            r = self.admin.put(self.url, json=dict(self.cuerpo, **malo))
            self.assertEqual(r.status_code, 400, malo)
            self.assertIn("error", r.get_json())

    def test_un_informe_que_no_existe_es_404(self):
        for m, extra in (("get", {}), ("put", {"json": self.cuerpo}), ("get", {})):
            r = getattr(self.admin, m)("/api/informes/999/envio", **extra)
            self.assertEqual(r.status_code, 404)
        self.assertEqual(self.admin.get("/api/informes/999/envio/registro").status_code, 404)
        self.assertEqual(self.admin.post("/api/informes/999/envio/probar", json={}).status_code, 404)

    def test_un_cuerpo_que_no_es_json_es_400(self):
        self.assertEqual(self.admin.put(self.url, data="no es json").status_code, 400)

    def test_el_resumen_para_la_lista(self):
        self.admin.put(self.url, json=self.cuerpo)
        j = self.admin.get("/api/informes/envios").get_json()
        self.assertEqual(list(j["envios"]), [str(self.informe["id"])])
        self.assertTrue(j["envios"][str(self.informe["id"])]["activo"])

    def test_no_hay_forma_de_borrar(self):
        self.assertEqual(self.admin.delete(self.url).status_code, 405)


class TestActivoYLatido(Base):
    def test_activo_tiene_que_ser_verdadero_o_falso(self):
        for malo in ("false", "no", 0, 1, None, [], {}):
            r = self.admin.put(self.url, json=dict(self.cuerpo, activo=malo))
            self.assertEqual(r.status_code, 400, repr(malo))
            self.assertIn("activo", r.get_json()["error"])
        self.assertIsNone(self.admin.get(self.url).get_json()["envio"])                # ninguno guardó nada

    def test_guardar_sin_decir_activo_no_reactiva_una_programacion_pausada(self):
        self.admin.put(self.url, json=dict(self.cuerpo, activo=False))
        e = self.admin.put(self.url, json=dict(self.cuerpo, horas=["09:00"])).get_json()["envio"]        # sin «activo»
        self.assertEqual(e["horas"], ["09:00"])
        self.assertFalse(e["activo"])
        e2 = self.admin.put(self.url, json=dict(self.cuerpo, activo=True)).get_json()["envio"]
        self.assertTrue(e2["activo"])

    def test_una_programacion_nueva_sin_decir_activo_queda_activa(self):
        self.assertTrue(self.admin.put(self.url, json=self.cuerpo).get_json()["envio"]["activo"])

    def test_el_latido_dice_hace_cuanto_reviso_el_hilo(self):
        j = self.admin.get(self.url).get_json()
        self.assertIsNone(j["ultima_pasada_texto"])
        self.assertIsNone(j["ultima_pasada_hace_s"])
        ahora = datetime.datetime.now().replace(microsecond=0)
        with ie._con() as con:
            con.execute("INSERT OR REPLACE INTO envios_estado (clave, valor) VALUES ('ultima_pasada', ?)", (ie._iso(ahora - datetime.timedelta(minutes=7)),))
        for url in (self.url, self.url + "/registro"):
            j = self.admin.get(url).get_json()
            self.assertRegex(j["ultima_pasada_texto"], r"^[a-zéáíóú]{3} \d{2}/\d{2}/\d{4} \d{2}:\d{2}$")
            self.assertGreaterEqual(j["ultima_pasada_hace_s"], 7 * 60)
            self.assertLess(j["ultima_pasada_hace_s"], 7 * 60 + 30)


class TestRegistro(Base):
    def test_lista_lo_ultimo_que_paso(self):
        self.admin.put(self.url, json=self.cuerpo)
        e = ie.obtener("ponderosa", self.informe["id"])
        with ie._con() as con:                                                 # la programación se guardó «ahora»: se la retrasa para que el horario ya haya vencido
            con.execute("UPDATE envios_programados SET modificado = '2026-08-20T10:00:00' WHERE id = ?", (e["id"],))
        ie.pasada(lambda canal, armado: None, lambda: ["telegram", "correo"], ahora=datetime.datetime(2026, 8, 27, 8, 31), ejecutar=lambda d, t, hoy=None: resultado())
        j = self.admin.get(self.url + "/registro").get_json()
        self.assertEqual(sorted((x["canal"], x["estado"], x["slot_texto"]) for x in j["registro"]),
                         [("correo", "enviado", "jue 27/08/2026 08:30"), ("telegram", "enviado", "jue 27/08/2026 08:30")])
        self.assertEqual(j["ultima_pasada_texto"], "jue 27/08/2026 08:31")


class TestProbar(Base):
    def test_arma_sin_mandar(self):
        r = self.admin.post(self.url + "/probar", json={"enviar": False})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn("3 animales", j["texto"])
        self.assertEqual(j["enviado"], {})
        self.assertEqual(self.enviados, [])
        self.assertNotIn("datos", j)

    def test_manda_de_verdad_por_los_canales_pedidos_y_no_anota_nada(self):
        r = self.admin.post(self.url + "/probar", json={"enviar": True, "canales": ["telegram"]})
        self.assertEqual(r.get_json()["enviado"], {"telegram": "ok"})
        self.assertEqual([c for c, _ in self.enviados], ["telegram"])
        self.assertEqual(ie.registro("ponderosa", self.informe["id"]), [])

    def test_mandar_de_verdad_sin_ningun_canal_es_400(self):
        self.admin.put(self.url, json=self.cuerpo)
        r = self.admin.post(self.url + "/probar", json={"enviar": True, "canales": []})
        self.assertEqual(r.status_code, 400)
        self.assertIn("canal", r.get_json()["error"])
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.admin.post(self.url + "/probar", json={"enviar": False, "canales": []}).status_code, 200)        # ver cómo queda no necesita canales

    def test_canales_invalidos_son_400(self):
        r = self.admin.post(self.url + "/probar", json={"enviar": True, "canales": ["sms"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.enviados, [])

    def test_si_el_informe_ya_no_compila_es_400_con_el_motivo(self):
        with mock.patch.object(im, "ejecutar", side_effect=im.InformeError("El campo «X» no existe en el catálogo.")):
            r = self.admin.post(self.url + "/probar", json={"enviar": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no existe", r.get_json()["error"])

    def test_si_la_base_falla_es_502_sin_detalles(self):
        with mock.patch.object(im, "ejecutar", side_effect=RuntimeError("login failed for user delpro_lectura")):
            r = self.admin.post(self.url + "/probar", json={"enviar": False})
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("delpro_lectura", r.get_data(as_text=True))

    def test_solo_un_verdadero_de_verdad_manda(self):
        """Mandar un mensaje real tiene que ser a propósito: un «false» escrito como texto, un 1, un «si» o un null no cuentan."""
        for valor in ("false", "true", "si", 1, 0, None, [], {}, "1"):
            r = self.admin.post(self.url + "/probar", json={"enviar": valor, "canales": ["telegram"]})
            self.assertEqual(r.status_code, 200, valor)
            self.assertEqual(r.get_json()["enviado"], {}, valor)
        self.assertEqual(self.enviados, [])
        self.admin.post(self.url + "/probar", json={"enviar": True, "canales": ["telegram"]})
        self.assertEqual([c for c, _ in self.enviados], ["telegram"])

    def test_sin_cuerpo_no_manda(self):
        r = self.admin.post(self.url + "/probar", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.enviados, [])                                    # «enviar» tiene que pedirse a propósito

    def test_devuelve_el_texto_que_recibe_cada_canal(self):
        """Revisión final (envíos #6): «Ver cómo queda» mostraba siempre el texto del correo; cada canal recibe el suyo."""
        self.admin.put(self.url, json=self.cuerpo)
        j = self.admin.post(self.url + "/probar", json={"enviar": False}).get_json()
        self.assertEqual(set(j["textos"]), {"correo", "telegram", "whatsapp"})
        self.assertEqual(j["textos"]["correo"], j["texto"])
        self.assertIn("adjunto", j["textos"]["telegram"])
        self.assertNotIn("adjunto", j["textos"]["whatsapp"])                  # a WhatsApp no le llega el archivo

    def test_el_formato_del_formulario_se_usa_en_vez_del_guardado(self):
        """Contrato con la pantalla: «Enviar ahora» y «Ver cómo queda» mandan el formato elegido, todavía sin guardar."""
        self.admin.put(self.url, json=self.cuerpo)                                           # guardado: excel
        j = self.admin.post(self.url + "/probar", json={"enviar": True, "canales": ["telegram"], "formato": "texto"}).get_json()
        self.assertEqual(j["bytes"], 0)
        self.assertIsNone(self.enviados[-1][1]["datos"])
        self.assertNotIn("adjunto", j["textos"]["correo"])
        j = self.admin.post(self.url + "/probar", json={"enviar": False, "formato": "excel"}).get_json()
        self.assertGreater(j["bytes"], 0)
        self.assertEqual(self.admin.get(self.url).get_json()["envio"]["formato"], "excel")   # lo guardado no cambió

    def test_sin_formato_se_usa_el_guardado_y_sin_programacion_el_excel(self):
        self.assertGreater(self.admin.post(self.url + "/probar", json={"enviar": False}).get_json()["bytes"], 0)
        self.admin.put(self.url, json=dict(self.cuerpo, formato="texto"))
        self.assertEqual(self.admin.post(self.url + "/probar", json={"enviar": False}).get_json()["bytes"], 0)

    def test_un_formato_que_no_existe_es_400_y_no_manda_nada(self):
        for malo in ("pdf", "EXCEL", "", 3, ["excel"], True):
            r = self.admin.post(self.url + "/probar", json={"enviar": True, "canales": ["telegram"], "formato": malo})
            self.assertEqual(r.status_code, 400, repr(malo))
            self.assertIn("formato", r.get_json()["error"])
        self.assertEqual(self.enviados, [])

    def test_un_formato_nulo_es_como_no_decirlo(self):
        self.assertEqual(self.admin.post(self.url + "/probar", json={"enviar": False, "formato": None}).status_code, 200)

    def test_un_excel_que_no_se_puede_armar_es_400_sin_el_texto_de_la_excepcion(self):
        with mock.patch("informes_excel.a_xlsx", side_effect=ValueError("detalle-secreto")):
            r = self.admin.post(self.url + "/probar", json={"enviar": False, "formato": "excel"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("No se pudo armar el Excel", r.get_json()["error"])
        self.assertNotIn("secreto", r.get_data(as_text=True))


class TestUnIdQueNoCabeEnSQLite(Base):
    """Revisión final (seguridad #3): un id de 2^63 o más daba un 500 en todas las rutas por id (SQLite no lo puede guardar). Es un informe que no existe: 404."""

    def test_todas_las_rutas_por_id_contestan_404(self):
        for id_ in (2 ** 63, 2 ** 63 + 1, 10 ** 30):
            base = f"/api/informes/{id_}"
            pedidos = [("get", base, {}), ("put", base, {"json": {"nombre": "x"}}), ("get", base + "/envio", {}), ("put", base + "/envio", {"json": self.cuerpo}),
                       ("get", base + "/envio/registro", {}), ("post", base + "/envio/probar", {"json": {"enviar": False}})]
            for metodo, url, extra in pedidos:
                r = getattr(self.admin, metodo)(url, **extra)
                # Con el conversor de la ruta (`<int(max=...)>`) un id imposible no calza con ninguna ruta: un GET da 404; los demás métodos dan 404 o 405 (hay una ruta
                # general de archivos solo-GET) y NUNCA un 500 ni un 200.
                self.assertIn(r.status_code, (404,) if metodo == "get" else (404, 405), f"{metodo} {url}")
        self.assertEqual(self.enviados, [])

    def test_el_mayor_id_que_cabe_es_un_informe_que_no_existe_y_el_cero_tambien(self):
        for id_ in (2 ** 63 - 1, 0):
            self.assertEqual(self.admin.get(f"/api/informes/{id_}").status_code, 404, id_)
            self.assertEqual(self.admin.get(f"/api/informes/{id_}/envio").status_code, 404, id_)

    def test_un_informe_de_verdad_sigue_andando(self):
        self.assertEqual(self.admin.get(f"/api/informes/{self.informe['id']}").status_code, 200)
        self.assertEqual(self.admin.get(self.url).status_code, 200)


class TestDespachoPorCanal(unittest.TestCase):
    """`app._enviar_informe_a_canal`: qué función de cada canal se usa. Los módulos de canal son de mentira."""

    ARMADO = {"asunto": "LactIA — Vacas (jue 27/08/2026)", "texto": "Resumen", "texto_sin_adjunto": "Resumen sin adjunto", "texto_telegram": "Resumen para Telegram",
              "nombre_archivo": "Vacas 2026-08-27.xlsx", "datos": b"PK\x03\x04xx"}

    def setUp(self):
        self.mod = {c: mock.MagicMock() for c in ("correo", "telegram", "whatsapp")}
        for m in self.mod.values():
            m.configurado.return_value = True
        p = mock.patch.dict(app._CANALES_MOD, self.mod)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(config_alertas, "activo", lambda canal: True)
        q.start()
        self.addCleanup(q.stop)

    def test_el_correo_lleva_el_excel_adjunto(self):
        with mock.patch.object(correo, "enviar_con_adjunto") as adj:
            app._enviar_informe_a_canal("correo", self.ARMADO)
        adj.assert_called_once_with(self.ARMADO["asunto"], "Resumen", "Vacas 2026-08-27.xlsx", self.ARMADO["datos"])
        self.mod["correo"].enviar.assert_not_called()

    def test_telegram_lleva_el_excel_como_documento(self):
        with mock.patch.object(telegram_bot, "enviar_documento") as doc:
            app._enviar_informe_a_canal("telegram", self.ARMADO)
        doc.assert_called_once_with("Vacas 2026-08-27.xlsx", self.ARMADO["datos"], "Resumen para Telegram")           # el pie de un archivo (1.024 letras)

    def test_whatsapp_solo_el_texto_y_sin_prometer_un_adjunto(self):
        app._enviar_informe_a_canal("whatsapp", self.ARMADO)
        self.mod["whatsapp"].enviar.assert_called_once_with("Resumen sin adjunto")

    def test_sin_excel_todos_mandan_el_texto(self):
        sin = dict(self.ARMADO, datos=None)
        app._enviar_informe_a_canal("correo", sin)
        self.mod["correo"].enviar.assert_called_once_with("Resumen sin adjunto", asunto=self.ARMADO["asunto"])        # con el asunto del informe, no el de una alerta
        for canal in ("telegram", "whatsapp"):
            app._enviar_informe_a_canal(canal, sin)
            self.mod[canal].enviar.assert_called_once_with("Resumen sin adjunto")

    def test_un_armado_sin_los_textos_por_canal_usa_el_texto_de_siempre(self):
        viejo = {"asunto": "A", "texto": "Resumen", "nombre_archivo": "x.xlsx", "datos": None}
        for canal in ("telegram", "whatsapp"):
            app._enviar_informe_a_canal(canal, viejo)
            self.mod[canal].enviar.assert_called_once_with("Resumen")
        with mock.patch.object(telegram_bot, "enviar_documento") as doc:
            app._enviar_informe_a_canal("telegram", dict(viejo, datos=b"PK"))
        doc.assert_called_once_with("x.xlsx", b"PK", "Resumen")

    def test_un_canal_apagado_o_sin_configurar_no_manda(self):
        self.mod["telegram"].configurado.return_value = False
        with self.assertRaises(RuntimeError):
            app._enviar_informe_a_canal("telegram", self.ARMADO)
        with mock.patch.object(config_alertas, "activo", lambda canal: canal != "correo"):
            with self.assertRaises(RuntimeError):
                app._enviar_informe_a_canal("correo", self.ARMADO)

    def test_un_canal_que_no_esta_disponible_levanta_un_error_que_se_sabe_que_no_salio(self):
        """Revisión final (envíos #5): un RuntimeError liso se anotaba «No se pudo confirmar si salió» cuando se SABE que no se llamó a ningún canal."""
        self.mod["telegram"].configurado.return_value = False
        with self.assertRaises(envio_red.CanalNoDisponible) as cm:
            app._enviar_informe_a_canal("telegram", self.ARMADO)
        self.assertIs(cm.exception.no_salio, True)
        self.assertIs(cm.exception.reintentable, False)
        estado, detalle, _ = ie._fallo_de_canal("telegram", cm.exception)
        self.assertEqual(estado, "fallido")
        self.assertIn("no está configurado o está apagado", detalle)
        for canal in ("correo", "telegram", "whatsapp"):
            self.mod[canal].enviar.assert_not_called()

    def texto_mandado(self, canal, armado):
        self.mod[canal].reset_mock()
        with mock.patch.object(correo, "enviar_con_adjunto") as adj, mock.patch.object(telegram_bot, "enviar_documento") as doc:
            app._enviar_informe_a_canal(canal, armado)
        if adj.called:
            return adj.call_args.args[1]
        if doc.called:
            return doc.call_args.args[2]
        return self.mod[canal].enviar.call_args.args[0]

    def test_cada_canal_manda_el_texto_que_ver_como_queda_le_muestra(self):
        """`probar` devuelve `textos` con `texto_para`: es el MISMO que usa el envío de verdad, con y sin el archivo."""
        for canal in ("correo", "telegram", "whatsapp"):
            for datos in (self.ARMADO["datos"], None):
                armado = dict(self.ARMADO, datos=datos)
                self.assertEqual(self.texto_mandado(canal, armado), ie.texto_para(armado, canal), (canal, bool(datos)))

    def test_un_canal_desconocido(self):
        with self.assertRaises(RuntimeError):
            app._enviar_informe_a_canal("sms", self.ARMADO)

    def test_ids_de_canales_disponibles(self):
        self.mod["whatsapp"].configurado.return_value = False
        with mock.patch.object(config_alertas, "activo", lambda canal: canal != "correo"):
            self.assertEqual(app._ids_de_canales_disponibles(), ["telegram"])


class TestArranque(unittest.TestCase):
    def test_importar_app_no_arranca_el_hilo_de_envios(self):
        self.assertNotIn("envios-informes", [t.name for t in threading.enumerate()])

    def test_iniciar_envios_informes_arranca_el_hilo_con_el_despacho_de_la_app(self):
        with mock.patch.object(ie, "iniciar_hilo") as arrancar:
            app.iniciar_envios_informes()
        arrancar.assert_called_once_with(app._enviar_informe_a_canal, app._ids_de_canales_disponibles)

    def test_servidor_lo_arranca_una_sola_vez_y_despues_del_candado(self):
        with open("servidor.py", encoding="utf-8") as f:
            fuente = f.read()
        self.assertEqual(fuente.count("iniciar_envios_informes()"), 1)
        self.assertIn("iniciar_envios_informes", fuente.split("from app import")[1].split("\n")[0])
        self.assertGreater(fuente.index("iniciar_envios_informes()"), fuente.index("_tomar_lock_de_instancia_unica()"))
        self.assertGreater(fuente.index("iniciar_envios_informes()"), fuente.index("from waitress import serve"))

    def test_app_no_llama_al_arranque_al_importarse(self):
        with open("app.py", encoding="utf-8") as f:
            fuente = f.read()
        llamadas = [m.start() for m in re.finditer(r"^iniciar_envios_informes\(\)", fuente, re.M)]
        self.assertEqual(llamadas, [])


if __name__ == "__main__":
    unittest.main()
