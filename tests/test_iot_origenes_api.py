"""Rutas de los orígenes HTTP: el ingreso público (`POST /api/iot/ingreso/<id>`, solo con token) y la administración (admin).

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_api -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_analogicas
import iot_ingreso
import iot_monitoreo
import iot_origenes
import iot_sensores as sen
import iot_vumetros as iv


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ruta = lambda n: os.path.join(self.dir.name, n)  # noqa: E731
        for p in (mock.patch.object(sen, "_RUTA", ruta("iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", ruta("iot_vumetros.json")),
                  mock.patch.object(iot_analogicas, "_RUTA", ruta("iot_analogicas.json")),
                  mock.patch.object(iot_origenes, "_RUTA", ruta("iot_origenes.json")),
                  mock.patch.object(iot_ingreso, "RUTA_DB", ruta("iot.db")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", ruta("iot.db"))):
            p.start()
            self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.publico = app.app.test_client()                      # sin sesión: así llama un ESP32
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"

    def crear(self, nombre="ESP32 del corral", formato="generico", ventana=120, canales=None):
        r = self.admin.post("/api/iot/origenes", json={"nombre": nombre, "formato": formato, "ventana_lectura_min": ventana})
        self.assertEqual(r.status_code, 200, r.get_json())
        origen, token = r.get_json()["origen"], r.get_json()["token"]
        if canales is not None:
            g = self.admin.post(f"/api/iot/origenes/{origen['id']}", json={
                "nombre": nombre, "formato": formato, "ventana_lectura_min": ventana, "canales": canales})
            self.assertEqual(g.status_code, 200, g.get_json())
        return origen["id"], token

    CANALES = [{"clave": "temp", "grupo": "entrada_analogica", "nombre": "Temp corral", "sensor": "temp_corral"},
               {"clave": "puerta", "grupo": "entrada_digital", "nombre": "Puerta"}]

    def enviar(self, id_, cuerpo, token=None, cliente=None, headers=None, **kw):
        h = dict(headers or {})
        if token is not None:
            h["X-Token"] = token
        return (cliente or self.publico).post(f"/api/iot/ingreso/{id_}", json=cuerpo, headers=h, **kw)


class TestIngreso(Base):
    def test_ingreso_feliz_y_la_lectura_se_ve_en_el_panel(self):
        id_, token = self.crear(canales=self.CANALES)
        r = self.enviar(id_, {"lecturas": {"temp": 21.5, "puerta": 1, "otra": 3}}, token)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "recibidas": 2, "ignorados": 1}))
        panel = {s["clave"]: s for s in iot_monitoreo.lecturas_actuales()}
        self.assertEqual(panel["temp_corral"]["valor"], 21.5)
        self.assertFalse(panel["temp_corral"]["sin_datos"])

    def test_la_respuesta_no_devuelve_el_token(self):
        id_, token = self.crear(canales=self.CANALES)
        r = self.enviar(id_, {"lecturas": {"temp": 1}}, token)
        self.assertNotIn(token, r.get_data(as_text=True))

    def test_token_malo_sin_token_y_origen_inexistente_contestan_igual(self):
        id_, token = self.crear(canales=self.CANALES)
        respuestas = [self.enviar(id_, {"lecturas": {}}, "malo"),
                      self.enviar(id_, {"lecturas": {}}, None),
                      self.enviar("no_existe", {"lecturas": {}}, token),
                      self.enviar("no_existe", {"lecturas": {}}, None)]
        self.assertEqual({r.status_code for r in respuestas}, {403})
        self.assertEqual(len({r.get_data() for r in respuestas}), 1)

    def test_un_token_malo_repetido_se_bloquea_por_ip_y_no_toca_el_login(self):
        id_, token = self.crear(canales=self.CANALES)
        for _ in range(auth._LIMITE_INTENTOS):
            self.assertEqual(self.enviar(id_, {"lecturas": {}}, "malo").status_code, 403)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, "malo").status_code, 429)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, token).status_code, 429)   # ni el bueno, mientras dure
        self.assertEqual(self.publico.post("/login", data={"usuario": "nadie", "password": "x"}).status_code, 200)

    def test_un_token_bueno_borra_los_intentos_fallidos(self):
        id_, token = self.crear(canales=self.CANALES)
        for _ in range(auth._LIMITE_INTENTOS - 1):
            self.enviar(id_, {"lecturas": {}}, "malo")
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, token).status_code, 200)
        for _ in range(auth._LIMITE_INTENTOS - 1):
            self.assertEqual(self.enviar(id_, {"lecturas": {}}, "malo").status_code, 403)

    def test_el_bloqueo_no_se_puede_provocar_desde_la_lan_con_cabeceras_inventadas(self):
        # Un ESP32 de la LAN le pega directo al servidor, sin túnel: nada impide que otro equipo ponga CF-Connecting-IP.
        id_, token = self.crear(canales=self.CANALES)
        lan = {"REMOTE_ADDR": "192.168.1.9"}
        for _ in range(auth._LIMITE_INTENTOS + 2):
            r = self.enviar(id_, {"lecturas": {}}, "malo", environ_overrides=lan, headers={"CF-Connecting-IP": "192.168.1.50"})
        victima = self.enviar(id_, {"lecturas": {}}, token, environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(victima.status_code, 200)

    def test_el_login_no_puede_llenar_el_contador_del_ingreso(self):
        id_, token = self.crear(canales=self.CANALES)
        for _ in range(auth._LIMITE_INTENTOS + 1):
            self.publico.post("/login", data={"usuario": "x", "password": "y"},
                              headers={"CF-Connecting-IP": "iot:192.168.1.50:" + id_}, environ_overrides={"REMOTE_ADDR": "192.168.1.9"})
        r = self.enviar(id_, {"lecturas": {}}, token, environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
        self.assertEqual(r.status_code, 200)

    def test_un_token_malo_de_un_origen_no_bloquea_a_otro_origen_de_la_misma_ip(self):
        # Un ChirpStack atiende varios orígenes desde UNA sola IP.
        a, _ = self.crear("Nodo A", canales=self.CANALES)
        b, token_b = self.crear("Nodo B", canales=[{"clave": "puerta", "grupo": "entrada_digital"}])
        for _ in range(auth._LIMITE_INTENTOS + 1):
            self.enviar(a, {"lecturas": {}}, "malo")
        self.assertEqual(self.enviar(a, {"lecturas": {}}, "malo").status_code, 429)
        self.assertEqual(self.enviar(b, {"lecturas": {}}, token_b).status_code, 200)

    def test_detras_del_tunel_la_ip_real_sale_de_la_cabecera(self):
        # Cloudflare conecta desde la propia PC (127.0.0.1) y pone la IP real en CF-Connecting-IP.
        id_, token = self.crear(canales=self.CANALES)
        for _ in range(auth._LIMITE_INTENTOS):
            self.enviar(id_, {"lecturas": {}}, "malo", headers={"CF-Connecting-IP": "203.0.113.7"})
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, token, headers={"CF-Connecting-IP": "203.0.113.7"}).status_code, 429)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, token, headers={"CF-Connecting-IP": "203.0.113.8"}).status_code, 200)

    def test_un_json_anidado_hasta_el_infinito_es_400_no_500(self):
        id_, token = self.crear(canales=self.CANALES)
        r = self.publico.post(f"/api/iot/ingreso/{id_}", data="[" * 60000, content_type="application/json",
                              headers={"X-Token": token})
        self.assertEqual(r.status_code, 400)

    def test_la_base_ocupada_es_503_para_que_el_equipo_reintente(self):
        import sqlite3
        id_, token = self.crear(canales=self.CANALES)
        with mock.patch.object(iot_ingreso, "recibir", side_effect=sqlite3.OperationalError("database is locked")):
            r = self.enviar(id_, {"lecturas": {"temp": 1}}, token)
        self.assertEqual(r.status_code, 503)
        self.assertIn("ocupada", r.get_json()["error"])

    def test_cuerpo_enorme(self):
        id_, token = self.crear(canales=self.CANALES)
        r = self.publico.post(f"/api/iot/ingreso/{id_}", data=json.dumps({"lecturas": {"temp": "x" * 70000}}),
                              content_type="application/json", headers={"X-Token": token})
        self.assertEqual(r.status_code, 413)

    def test_cuerpos_mal_formados_son_400_nunca_500_y_el_error_se_ve(self):
        id_, token = self.crear(canales=self.CANALES)
        casos = [{"lecturas": [1, 2]}, {"lecturas": "texto"}, {"lecturas": None}, [], "texto", 5, None, {}]
        for cuerpo in casos:
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(self.enviar(id_, cuerpo, token).status_code, 400)
        r = self.publico.post(f"/api/iot/ingreso/{id_}", data="esto no es json {", content_type="application/json",
                              headers={"X-Token": token})
        self.assertEqual(r.status_code, 400)
        origenes = self.admin.get("/api/iot/origenes").get_json()["origenes"]
        self.assertTrue(origenes[0]["estado"]["ultimo_error"])

    def test_el_json_se_acepta_aunque_falte_el_content_type(self):
        id_, token = self.crear(canales=self.CANALES)
        r = self.publico.post(f"/api/iot/ingreso/{id_}", data=json.dumps({"lecturas": {"temp": 3}}),
                              headers={"X-Token": token})
        self.assertEqual((r.status_code, r.get_json()["recibidas"]), (200, 1))

    def test_solo_post(self):
        id_, token = self.crear(canales=self.CANALES)
        self.assertEqual(self.publico.get(f"/api/iot/ingreso/{id_}", headers={"X-Token": token}).status_code, 405)

    def test_el_prefijo_publico_no_abre_rutas_parecidas(self):
        self.assertEqual(self.publico.post("/api/iot/ingresoX", json={}).status_code, 401)
        self.assertEqual(self.publico.get("/api/iot/origenes").status_code, 401)
        self.assertEqual(self.publico.post("/api/iot/origenes", json={}).status_code, 401)

    def test_token_por_url_solo_en_chirpstack(self):
        gid, gtoken = self.crear("Nodo generico", "generico", 60, [{"clave": "temp", "grupo": "entrada_analogica"}])
        cid, ctoken = self.crear("Nodo lora", "chirpstack", 90, [{"clave": "temperature", "grupo": "entrada_analogica"}])
        self.assertEqual(self.publico.post(f"/api/iot/ingreso/{gid}?token={gtoken}", json={"lecturas": {}}).status_code, 403)
        up = {"deviceInfo": {}, "object": {"temperature": 3}}
        r = self.publico.post(f"/api/iot/ingreso/{cid}?event=up&token={ctoken}", json=up)
        self.assertEqual((r.status_code, r.get_json()["recibidas"]), (200, 1))
        r = self.publico.post(f"/api/iot/ingreso/{cid}?event=up&token=malo", json=up)
        self.assertEqual(r.status_code, 403)

    def test_chirpstack_con_la_cabecera_tambien_anda(self):
        cid, ctoken = self.crear("Nodo lora", "chirpstack", 90, [{"clave": "temperature", "grupo": "entrada_analogica"}])
        r = self.publico.post(f"/api/iot/ingreso/{cid}?event=up", json={"deviceInfo": {}, "object": {"temperature": 3}},
                              headers={"X-Token": ctoken})
        self.assertEqual(r.status_code, 200)

    def test_chirpstack_join_es_200_con_cero(self):
        cid, ctoken = self.crear("Nodo lora", "chirpstack", 90, [])
        r = self.publico.post(f"/api/iot/ingreso/{cid}?event=join", json={"deviceInfo": {}}, headers={"X-Token": ctoken})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "recibidas": 0, "ignorados": 0}))

    def test_chirpstack_up_sin_object_es_400_legible(self):
        cid, ctoken = self.crear("Nodo lora", "chirpstack", 90, [])
        r = self.publico.post(f"/api/iot/ingreso/{cid}?event=up", json={"deviceInfo": {}}, headers={"X-Token": ctoken})
        self.assertEqual(r.status_code, 400)
        self.assertIn("códec", r.get_json()["error"])


class TestAdministracion(Base):
    def test_sin_sesion_es_401_y_con_sesion_de_operario_es_403(self):
        id_, _ = self.crear(canales=self.CANALES)
        pedidos = [("get", "/api/iot/origenes", None), ("post", "/api/iot/origenes", {}),
                   ("post", f"/api/iot/origenes/{id_}", {}), ("post", f"/api/iot/origenes/{id_}/token", {}),
                   ("delete", f"/api/iot/origenes/{id_}", None)]
        for metodo, ruta, cuerpo in pedidos:
            with self.subTest(ruta=ruta, metodo=metodo):
                kw = {"json": cuerpo} if cuerpo is not None else {}
                self.assertEqual(getattr(self.publico, metodo)(ruta, **kw).status_code, 401)
                self.assertEqual(getattr(self.operario, metodo)(ruta, **kw).status_code, 403)

    def test_el_listado_no_trae_ni_el_hash_ni_el_token(self):
        id_, token = self.crear(canales=self.CANALES)
        texto = self.admin.get("/api/iot/origenes").get_data(as_text=True)
        self.assertNotIn(token, texto)
        self.assertNotIn("token_hash", texto)
        self.assertNotIn(iot_origenes.obtener(id_)["token_hash"], texto)

    def test_el_listado(self):
        id_, token = self.crear(canales=self.CANALES)
        self.enviar(id_, {"lecturas": {"temp": 21.5, "puerta": 1}}, token)
        datos = self.admin.get("/api/iot/origenes").get_json()
        self.assertEqual(datos["principal"], {"id": "principal", "nombre": "Equipo principal (gateway M300)",
                                              "tipo": "modbus_tcp", "ventana_lectura_min": 30})
        o = datos["origenes"][0]
        self.assertEqual((o["id"], o["tipo"], o["formato"], o["ventana_lectura_min"], o["url_ingreso"]),
                         (id_, "http", "generico", 120, f"/api/iot/ingreso/{id_}"))
        canales = {c["clave"]: c for c in o["canales"]}
        self.assertEqual(canales["temp"]["ultimo"]["valor"], 21.5)
        self.assertEqual(canales["puerta"]["ultimo"]["valor"], 1)
        self.assertEqual(o["estado"]["recibidas"], 2)
        self.assertIn("temp_corral", [s["clave"] for s in datos["sensores_analogicos"]])
        self.assertEqual(set(datos["sensores_analogicos"][0]), {"clave", "label", "unidad"})

    def test_canal_sin_lecturas_tiene_ultimo_null(self):
        self.crear(canales=self.CANALES)
        o = self.admin.get("/api/iot/origenes").get_json()["origenes"][0]
        self.assertTrue(all(c["ultimo"] is None for c in o["canales"]))

    def test_crear_devuelve_el_token_una_vez(self):
        r = self.admin.post("/api/iot/origenes", json={"nombre": "ESP32", "formato": "generico", "ventana_lectura_min": 60})
        cuerpo = r.get_json()
        self.assertGreaterEqual(len(cuerpo["token"]), 43)
        self.assertNotIn("token_hash", cuerpo["origen"])
        self.assertNotIn(cuerpo["token"], self.admin.get("/api/iot/origenes").get_data(as_text=True))

    def test_crear_sin_ventana_es_400(self):
        r = self.admin.post("/api/iot/origenes", json={"nombre": "ESP32", "formato": "generico"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.admin.get("/api/iot/origenes").get_json()["origenes"], [])

    def test_cuerpos_que_no_son_un_objeto(self):
        id_, _ = self.crear(canales=self.CANALES)
        for ruta in ("/api/iot/origenes", f"/api/iot/origenes/{id_}"):
            self.assertEqual(self.admin.post(ruta, json=[1]).status_code, 400)
            self.assertEqual(self.admin.post(ruta, data="no json", content_type="application/json").status_code, 400)

    def test_guardar_con_un_error_no_cambia_nada(self):
        id_, _ = self.crear(canales=self.CANALES)
        antes = iot_origenes.obtener(id_)
        r = self.admin.post(f"/api/iot/origenes/{id_}", json={
            "nombre": "x", "formato": "generico", "ventana_lectura_min": 10,
            "canales": [{"clave": "con.punto", "grupo": "entrada_digital"}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(iot_origenes.obtener(id_), antes)

    def test_un_sensor_ya_asignado_a_una_entrada_del_m300_se_rechaza(self):
        id_, _ = self.crear()
        iot_analogicas.guardar({"ai_1": {"sensor": "temp_leche"}}, {"temp_leche": (0, 40)})
        r = self.admin.post(f"/api/iot/origenes/{id_}", json={
            "nombre": "x", "formato": "generico", "ventana_lectura_min": 10,
            "canales": [{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_leche"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("temp_leche", r.get_json()["error"])

    def test_regenerar_invalida_el_token_anterior(self):
        id_, viejo = self.crear(canales=self.CANALES)
        r = self.admin.post(f"/api/iot/origenes/{id_}/token")
        nuevo = r.get_json()["token"]
        self.assertNotEqual(nuevo, viejo)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, viejo).status_code, 403)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, nuevo).status_code, 200)

    def test_regenerar_un_origen_inexistente(self):
        self.assertEqual(self.admin.post("/api/iot/origenes/no_existe/token").status_code, 400)

    def test_eliminar(self):
        id_, token = self.crear(canales=self.CANALES)
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}").status_code, 200)
        self.assertEqual(self.enviar(id_, {"lecturas": {}}, token).status_code, 403)
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}").status_code, 400)

    def test_un_equipo_eliminado_y_vuelto_a_crear_con_el_mismo_nombre_empieza_de_cero(self):
        id_, token = self.crear(canales=self.CANALES)
        self.enviar(id_, {"lecturas": {"temp": 21.5, "puerta": 1}}, token)
        self.enviar(id_, {"lecturas": [1]}, token)
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}").status_code, 200)
        id2, _ = self.crear(canales=self.CANALES)
        self.assertEqual(id2, id_)
        o = self.admin.get("/api/iot/origenes").get_json()["origenes"][0]
        self.assertEqual(o["estado"], {"ultima_recepcion": None, "recibidas": None, "ignorados": None,
                                       "ultimo_error": None, "error_fecha": None})
        self.assertTrue(all(c["ultimo"] is None for c in o["canales"]))

    def test_eliminar_con_un_sensor_usando_un_canal_es_400_y_nombra_la_entrada(self):
        id_, _ = self.crear(canales=self.CANALES)
        sen.guardar(sen.cargar() + [{"label": "Puerta", "tipo": "digital", "rol": "alarma", "entrada": f"{id_}.puerta"}])
        r = self.admin.delete(f"/api/iot/origenes/{id_}")
        self.assertEqual(r.status_code, 400)
        self.assertIn(f"{id_}.puerta", r.get_json()["error"])
        self.assertIsNotNone(iot_origenes.obtener(id_))

    def test_sacar_un_canal_en_uso_es_400(self):
        id_, _ = self.crear(canales=self.CANALES)
        sen.guardar(sen.cargar() + [{"label": "Puerta", "tipo": "digital", "rol": "alarma", "entrada": f"{id_}.puerta"}])
        r = self.admin.post(f"/api/iot/origenes/{id_}", json={
            "nombre": "x", "formato": "generico", "ventana_lectura_min": 10, "canales": [self.CANALES[0]]})
        self.assertEqual(r.status_code, 400)
        self.assertIn(f"{id_}.puerta", r.get_json()["error"])

    def test_las_entradas_de_los_origenes_aparecen_en_el_editor_de_sensores(self):
        id_, _ = self.crear(canales=self.CANALES)
        entradas = self.admin.get("/api/iot/sensores").get_json()["entradas"]
        propias = [e for e in entradas if e.get("origen")]
        self.assertEqual(propias, [{"clave": f"{id_}.puerta", "numero": None, "label": "ESP32 del corral · Puerta",
                                    "origen": id_, "origen_nombre": "ESP32 del corral"}])
        self.assertEqual(entradas[0]["clave"], "lavado_rotativa")
        self.assertNotIn("origen", entradas[0])

    def test_el_editor_sabe_que_sensores_alimenta_un_origen(self):
        self.crear(canales=self.CANALES)
        self.assertEqual(self.admin.get("/api/iot/sensores").get_json()["asignados_origen"],
                         {"temp_corral": "ESP32 del corral"})


if __name__ == "__main__":
    unittest.main()
