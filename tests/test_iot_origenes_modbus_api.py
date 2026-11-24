"""Rutas de los equipos Modbus adicionales (administración, sensores) y su convivencia con los orígenes HTTP.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_modbus_api -v
"""
import datetime
import json
import os
import sqlite3
import unittest
from unittest import mock

import auth
import iot_conexion
import iot_ingreso
import iot_lavado
import iot_monitoreo
import iot_origenes
import iot_sensores as sen
from tests.test_iot_origenes_api import Base as BaseApi

AHORA = datetime.datetime(2026, 11, 25, 12, 0, 0)


class Base(BaseApi):
    def setUp(self):
        super().setUp()
        ruta = os.path.join(self.dir.name, "iot_conexion.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"host": "192.168.1.1", "port": 502}, f)
        p = mock.patch.object(iot_conexion, "_RUTA", ruta)
        p.start()
        self.addCleanup(p.stop)

    def alta(self, nombre="PLC sala de máquinas", host="10.0.0.60", **kw):
        cuerpo = {"tipo": "modbus_tcp", "nombre": nombre, "host": host, "ventana_lectura_min": 15, **kw}
        r = self.admin.post("/api/iot/origenes", json=cuerpo)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["origen"]["id"]

    CANALES_MB = [{"clave": "bomba", "grupo": "entrada_digital", "nombre": "Bomba", "funcion": "coil", "direccion": 3},
                  {"clave": "temp", "grupo": "entrada_analogica", "nombre": "Temp corral", "funcion": "input_register",
                   "direccion": 10, "tipo_dato": "float32_ab_cd", "sensor": "temp_corral"}]

    def guardar(self, id_, canales=None, **kw):
        cuerpo = {"nombre": "PLC", "host": "10.0.0.60", "port": 502, "unit_id": 1, "ventana_lectura_min": 15,
                  "canales": self.CANALES_MB if canales is None else canales, **kw}
        return self.admin.post(f"/api/iot/origenes/{id_}", json=cuerpo)


class TestAdministracion(Base):
    def test_alta_y_listado(self):
        id_ = self.alta()
        datos = self.admin.get("/api/iot/origenes").get_json()
        o = datos["origenes"][0]
        self.assertEqual((o["id"], o["tipo"], o["host"], o["port"], o["unit_id"], o["ventana_lectura_min"]),
                         (id_, "modbus_tcp", "10.0.0.60", 502, 1, 15))
        self.assertNotIn("url_ingreso", o)
        self.assertNotIn("token", o)
        self.assertNotIn("token_hash", json.dumps(datos))
        self.assertEqual(o["estado"], {"ultima_recepcion": None, "recibidas": None, "ignorados": None, "ultimo_error": None,
                                       "error_fecha": None})
        self.assertIn("coil", datos["funciones_entrada"])
        self.assertEqual(datos["funciones_analogica"], ["input_register", "holding_register"])
        self.assertEqual(datos["tipos_dato"], ["uint16", "int16", "float32_ab_cd"])

    def test_el_alta_no_devuelve_token(self):
        r = self.admin.post("/api/iot/origenes", json={"tipo": "modbus_tcp", "nombre": "PLC", "host": "10.0.0.60",
                                                       "port": "1502", "unit_id": "3", "ventana_lectura_min": 15})
        cuerpo = r.get_json()
        self.assertNotIn("token", cuerpo)
        self.assertEqual((cuerpo["origen"]["port"], cuerpo["origen"]["unit_id"]), (1502, 3))

    def test_sin_tipo_sigue_siendo_http(self):
        r = self.admin.post("/api/iot/origenes", json={"nombre": "ESP32", "formato": "generico", "ventana_lectura_min": 60})
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.get_json())
        self.assertEqual(r.get_json()["origen"]["tipo"], "http")

    def test_un_tipo_desconocido_es_400(self):
        r = self.admin.post("/api/iot/origenes", json={"tipo": "mqtt", "nombre": "x", "ventana_lectura_min": 5})
        self.assertEqual(r.status_code, 400)

    def test_datos_invalidos_son_400_y_la_lista_queda_igual(self):
        malos = [dict(host=""), dict(host="http://x"), dict(host="con espacios"), dict(host="a" * 300), dict(port=0), dict(port=70000),
                 dict(port="abc"), dict(unit_id=0), dict(unit_id=248), dict(ventana_lectura_min=None), dict(ventana_lectura_min=0),
                 dict(nombre="")]
        for kw in malos:
            with self.subTest(**{k: repr(v)[:20] for k, v in kw.items()}):
                cuerpo = {"tipo": "modbus_tcp", "nombre": "PLC", "host": "10.0.0.60", "ventana_lectura_min": 15, **kw}
                self.assertEqual(self.admin.post("/api/iot/origenes", json=cuerpo).status_code, 400)
        self.assertEqual(self.admin.get("/api/iot/origenes").get_json()["origenes"], [])

    def test_el_mismo_host_y_puerto_es_400_y_nombra_al_otro(self):
        self.alta("Primero")
        r = self.admin.post("/api/iot/origenes", json={"tipo": "modbus_tcp", "nombre": "Segundo", "host": "10.0.0.60",
                                                       "ventana_lectura_min": 15})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Primero", r.get_json()["error"])

    def test_guardar_los_canales(self):
        id_ = self.alta()
        self.assertEqual(self.guardar(id_).status_code, 200)
        o = self.admin.get("/api/iot/origenes").get_json()["origenes"][0]
        canales = {c["clave"]: c for c in o["canales"]}
        self.assertEqual((canales["bomba"]["funcion"], canales["bomba"]["direccion"], canales["temp"]["tipo_dato"]),
                         ("coil", 3, "float32_ab_cd"))
        self.assertEqual(canales["temp"]["ultimo"], None)

    def test_guardar_con_un_error_no_cambia_nada(self):
        id_ = self.alta()
        self.guardar(id_)
        antes = iot_origenes.obtener(id_)
        for cuerpo in (self.CANALES_MB + [dict(self.CANALES_MB[0], clave="otra")],            # mismo punto que «bomba»
                       [{"clave": "x", "grupo": "salida", "funcion": "coil", "direccion": 1}],
                       [{"clave": "x", "grupo": "entrada_analogica", "funcion": "input_register", "direccion": 1}]):   # sin tipo de dato
            with self.subTest(cuerpo=str(cuerpo)[:50]):
                self.assertEqual(self.guardar(id_, cuerpo).status_code, 400)
                self.assertEqual(iot_origenes.obtener(id_), antes)

    def test_permisos(self):
        id_ = self.alta()
        pedidos = [("post", "/api/iot/origenes", {"tipo": "modbus_tcp", "nombre": "x", "host": "10.0.0.9", "ventana_lectura_min": 5}),
                   ("post", f"/api/iot/origenes/{id_}", {"canales": []}), ("delete", f"/api/iot/origenes/{id_}", None)]
        for metodo, ruta, cuerpo in pedidos:
            with self.subTest(ruta=ruta, metodo=metodo):
                kw = {"json": cuerpo} if cuerpo is not None else {}
                self.assertEqual(getattr(self.publico, metodo)(ruta, **kw).status_code, 401)
                self.assertEqual(getattr(self.operario, metodo)(ruta, **kw).status_code, 403)

    def test_un_equipo_modbus_no_usa_token(self):
        id_ = self.alta()
        r = self.admin.post(f"/api/iot/origenes/{id_}/token")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Modbus", r.get_json()["error"])

    def test_un_equipo_http_no_se_guarda_con_el_formulario_modbus_ni_al_reves(self):
        mb = self.alta()
        http, _ = self.crear("ESP32", canales=self.CANALES)
        r = self.admin.post(f"/api/iot/origenes/{http}", json={"nombre": "x", "host": "10.0.0.9", "ventana_lectura_min": 5, "canales": []})
        self.assertEqual(r.status_code, 400)
        r = self.admin.post(f"/api/iot/origenes/{mb}", json={"nombre": "x", "formato": "generico", "ventana_lectura_min": 5,
                                                               "canales": []})
        self.assertEqual(r.status_code, 400)

    def test_eliminar_y_su_estado_derivado(self):
        id_ = self.alta()
        self.guardar(id_)
        con = iot_lavado._conectar_db(iot_ingreso.RUTA_DB)
        iot_lavado.anotar_ultimo(con, f"{id_}.temp", "2026-11-25T11:00:00", 21.5)
        con.execute("INSERT INTO origenes_estado (origen, ultima_recepcion, ultimo_error, error_fecha) VALUES (?, ?, ?, ?)",
                    (id_, "2026-11-25T11:00:00", "viejo", "2026-11-25T10:00:00"))
        con.commit()
        con.close()
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}").status_code, 200)
        nuevo = self.alta()
        self.assertEqual(nuevo, id_)
        o = self.admin.get("/api/iot/origenes").get_json()["origenes"][0]
        self.assertIsNone(o["estado"]["ultimo_error"])

    def test_eliminar_con_un_sensor_atado_es_400_y_nombra_la_entrada(self):
        id_ = self.alta()
        self.guardar(id_)
        sen.guardar(sen.cargar() + [{"label": "Bomba", "tipo": "digital", "rol": "alarma", "entrada": f"{id_}.bomba"}])
        r = self.admin.delete(f"/api/iot/origenes/{id_}")
        self.assertEqual(r.status_code, 400)
        self.assertIn(f"{id_}.bomba", r.get_json()["error"])
        r = self.guardar(id_, [self.CANALES_MB[1]])
        self.assertEqual(r.status_code, 400)
        self.assertIn(f"{id_}.bomba", r.get_json()["error"])


class TestSensores(Base):
    def test_las_entradas_del_equipo_aparecen_en_el_editor_en_su_propio_grupo(self):
        id_ = self.alta("PLC sala")
        self.guardar(id_, nombre="PLC sala")
        d = self.admin.get("/api/iot/sensores").get_json()
        propias = [e for e in d["entradas"] if e.get("origen") == id_]
        self.assertEqual(propias, [{"clave": f"{id_}.bomba", "numero": None, "label": "PLC sala · Bomba", "origen": id_,
                                    "origen_nombre": "PLC sala"}])
        self.assertEqual(d["asignados_origen"], {"temp_corral": "PLC sala"})

    def test_un_sensor_digital_se_asigna_a_la_entrada_del_equipo_y_si_el_equipo_se_pierde_no_desaparece(self):
        id_ = self.alta()
        self.guardar(id_)
        sen.guardar(sen.cargar() + [{"label": "Bomba", "tipo": "digital", "rol": "alarma", "entrada": f"{id_}.bomba"}])
        os.remove(iot_origenes._RUTA)
        self.assertIn("Bomba", [s["label"] for s in sen.cargar()])
        panel = {s["label"]: s for s in iot_monitoreo.lecturas_actuales(AHORA)}
        self.assertTrue(panel["Bomba"]["sin_datos"])

    def test_la_ventana_del_equipo_aplica_a_sus_sensores_y_la_del_m300_sigue_en_30(self):
        id_ = self.alta(ventana_lectura_min=120)
        self.guardar(id_, ventana_lectura_min=120)
        sen.guardar(sen.cargar() + [{"label": "Bomba", "tipo": "digital", "rol": "alarma", "entrada": f"{id_}.bomba"}])
        con = iot_lavado._conectar_db(iot_monitoreo.RUTA_DB)
        hace = lambda m: (AHORA - datetime.timedelta(minutes=m)).isoformat(timespec="seconds")  # noqa: E731
        con.execute("INSERT INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, 1)", (f"{id_}.bomba", hace(90)))
        con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES ('temp_corral', ?, 20)", (hace(90),))
        con.execute("INSERT INTO lecturas_sensor (sensor, fecha_hora, valor) VALUES ('temp_leche', ?, 4)", (hace(31),))
        con.commit()
        con.close()
        panel = {s["label"]: s for s in iot_monitoreo.lecturas_actuales(AHORA)}
        self.assertFalse(panel["Bomba"]["sin_datos"])
        self.assertFalse(panel["Temp. corral"]["sin_datos"])
        self.assertTrue(panel["Temp. leche"]["sin_datos"])


class TestIngresoHTTP(Base):
    def test_un_post_de_ingreso_a_un_equipo_modbus_contesta_igual_que_a_uno_inexistente(self):
        mb = self.alta()
        http, token = self.crear("ESP32", canales=self.CANALES)
        respuestas = [self.enviar(mb, {"lecturas": {}}, token), self.enviar("no_existe", {"lecturas": {}}, token),
                      self.enviar(mb, {"lecturas": {}}, None)]
        self.assertEqual({r.status_code for r in respuestas}, {403})
        self.assertEqual(len({r.get_data() for r in respuestas}), 1)

    def test_el_ingreso_a_un_equipo_modbus_no_escribe_nada(self):
        mb = self.alta()
        self.guardar(mb)
        self.enviar(mb, {"lecturas": {"temp": 5}}, "cualquiera")
        self.assertIsNone(iot_ingreso.ultimo_de_canal(mb, "temp"))

    def test_el_ingreso_del_modulo_tampoco_acepta_un_equipo_modbus(self):
        mb = self.alta()
        with self.assertRaises(iot_ingreso.IngresoError):
            iot_ingreso.recibir(mb, {"lecturas": {}})

    def test_el_origen_http_de_la_etapa_3_sigue_andando_junto_a_uno_modbus(self):
        self.alta()
        http, token = self.crear("ESP32", canales=self.CANALES)
        r = self.enviar(http, {"lecturas": {"temp": 21.5}}, token)
        self.assertEqual((r.status_code, r.get_json()["recibidas"]), (200, 1))
        tipos = {o["tipo"] for o in self.admin.get("/api/iot/origenes").get_json()["origenes"]}
        self.assertEqual(tipos, {"http", "modbus_tcp"})

    def test_el_bloqueo_no_distingue_un_origen_real_de_uno_inexistente(self):
        # Antes la clave era por origen EXISTENTE o un único «inexistente»: probando ids se veía cuáles existen.
        http, token = self.crear("ESP32", canales=self.CANALES)
        for _ in range(auth._LIMITE_INTENTOS + 1):
            self.enviar("fantasma", {"lecturas": {}}, "malo")
        self.assertEqual(self.enviar("fantasma", {"lecturas": {}}, "malo").status_code, 429)
        # Un origen real y otro inexistente distinto: ninguno de los dos queda bloqueado por lo de «fantasma» (salvo choque de balde)
        respuestas = {self.enviar(i, {"lecturas": {}}, "malo").status_code for i in ("otro_fantasma_1", "otro_fantasma_2", http)}
        self.assertIn(403, respuestas)

    def test_los_intentos_fallidos_con_ids_inventados_no_agrandan_la_tabla_sin_limite(self):
        auth._intentos_fallidos.clear()
        for i in range(300):
            self.enviar(f"inventado_{i}", {"lecturas": {}}, "malo")
        self.assertLessEqual(len(auth._intentos_fallidos), 64)


if __name__ == "__main__":
    unittest.main()
