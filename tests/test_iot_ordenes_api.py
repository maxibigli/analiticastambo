"""Las rutas de las órdenes: la que encola el administrador y las dos del ESP32 (consultar y confirmar, con su token).

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_ordenes_api -v
"""
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

import auth
import iot_ordenes
import iot_origenes
from tests.test_iot_origenes_api import Base as BaseApi

VENCE = 30
SALIDAS = [{"clave": "bomba", "grupo": "salida", "nombre": "Bomba", "modo": "pulso", "pulso_s": 1.5},
           {"clave": "calefaccion", "grupo": "salida", "nombre": "Calefacción", "modo": "sostenida"},
           {"clave": "puerta", "grupo": "entrada_digital", "nombre": "Puerta"}]


class Base(BaseApi):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(iot_ordenes, "RUTA_DB", __import__("iot_ingreso").RUTA_DB)
        p.start()
        self.addCleanup(p.stop)

    def comandable(self, nombre="ESP32 del corral", canales=None, vence=VENCE, formato="generico"):
        r = self.admin.post("/api/iot/origenes", json={"nombre": nombre, "formato": formato, "ventana_lectura_min": 120})
        origen, token = r.get_json()["origen"]["id"], r.get_json()["token"]
        g = self.guardar(origen, nombre, canales if canales is not None else SALIDAS, vence=vence, formato=formato)
        self.assertEqual(g.status_code, 200, g.get_json())
        return origen, token

    def guardar(self, id_, nombre, canales, vence=VENCE, puede=True, formato="generico"):
        cuerpo = {"nombre": nombre, "formato": formato, "ventana_lectura_min": 120, "canales": canales, "puede_comandar": puede}
        if vence is not None:
            cuerpo["vencimiento_orden_s"] = vence
        return self.admin.post(f"/api/iot/origenes/{id_}", json=cuerpo)

    def ordenar(self, id_, canal="bomba", accion="pulso", cliente=None):
        return (cliente or self.admin).post(f"/api/iot/origenes/{id_}/ordenes", json={"canal": canal, "accion": accion})

    def consultar(self, id_, token, **kw):
        return self.publico.get(f"/api/iot/ingreso/{id_}/ordenes", headers={"X-Token": token} if token is not None else {}, **kw)

    def confirmar(self, id_, orden, token, cuerpo=None, **kw):
        h = {"X-Token": token} if token is not None else {}
        if cuerpo is None:
            return self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{orden}/ok", headers=h, **kw)
        return self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{orden}/ok", json=cuerpo, headers=h, **kw)

    def listado(self, id_):
        return next(o for o in self.admin.get("/api/iot/origenes").get_json()["origenes"] if o["id"] == id_)


class TestFlujo(Base):
    def test_de_punta_a_punta(self):
        id_, token = self.comandable()
        r = self.ordenar(id_)
        self.assertEqual(r.status_code, 200, r.get_json())
        orden = r.get_json()["orden"]
        self.assertEqual((orden["canal"], orden["accion"], orden["estado"], orden["duracion_s"]), ("bomba", "pulso", "pendiente", 1.5))
        c = self.consultar(id_, token)
        self.assertEqual((c.status_code, c.get_json()),
                         (200, {"ordenes": [{"id": orden["id"], "canal": "bomba", "accion": "pulso", "duracion_s": 1.5}]}))
        self.assertEqual(self.consultar(id_, token).get_json(), {"ordenes": []})
        self.assertEqual(self.listado(id_)["ordenes"][0]["estado"], "entregada")
        ok = self.confirmar(id_, orden["id"], token)
        self.assertEqual((ok.status_code, ok.get_json()), (200, {"ok": True, "estado": "ejecutada"}))
        o = self.listado(id_)
        self.assertEqual(o["ordenes"][0]["estado"], "ejecutada")
        bomba = next(c for c in o["canales"] if c["clave"] == "bomba")
        self.assertEqual((bomba["ultima_orden"]["estado"], bomba["ultima_orden"]["accion"], bomba["ultima_orden"]["usuario"]),
                         ("ejecutada", "pulso", "x"))
        self.assertTrue(o["puede_comandar"])
        self.assertEqual(o["vencimiento_orden_s"], VENCE)
        puerta = next(c for c in o["canales"] if c["clave"] == "puerta")
        self.assertNotIn("ultima_orden", puerta)

    def test_una_orden_con_error_se_ve(self):
        id_, token = self.comandable()
        i = self.ordenar(id_).get_json()["orden"]["id"]
        self.consultar(id_, token)
        r = self.confirmar(id_, i, token, {"error": "el relé no responde"})
        self.assertEqual(r.get_json()["estado"], "error")
        self.assertEqual(self.listado(id_)["ordenes"][0]["resultado"], "el relé no responde")

    def test_sostenida(self):
        id_, token = self.comandable()
        r = self.ordenar(id_, "calefaccion", "prender")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.consultar(id_, token).get_json()["ordenes"][0]["accion"], "prender")

    def test_una_orden_vencida_no_se_entrega(self):
        id_, token = self.comandable(vence=1)
        self.ordenar(id_)
        with mock.patch.object(iot_ordenes, "datetime", wraps=datetime) as dt:
            dt.now.return_value = datetime.now() + timedelta(seconds=5)
            self.assertEqual(self.consultar(id_, token).get_json(), {"ordenes": []})
        self.assertEqual(self.listado(id_)["ordenes"][0]["estado"], "vencida")


class TestEncolar(Base):
    def test_permisos(self):
        id_, _ = self.comandable()
        self.assertEqual(self.ordenar(id_, cliente=self.publico).status_code, 401)
        self.assertEqual(self.ordenar(id_, cliente=self.operario).status_code, 403)
        self.assertEqual(self.publico.get(f"/api/iot/origenes/{id_}/ordenes").status_code, 401)
        self.assertEqual(self.operario.get(f"/api/iot/origenes/{id_}/ordenes").status_code, 403)

    def test_ni_el_token_del_equipo_sirve_para_encolar(self):
        id_, token = self.comandable()
        r = self.publico.post(f"/api/iot/origenes/{id_}/ordenes", json={"canal": "bomba", "accion": "pulso"}, headers={"X-Token": token})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.consultar(id_, token).get_json(), {"ordenes": []})

    def test_dos_encolados_seguidos_dejan_una_sola_orden_viva(self):
        id_, token = self.comandable()
        self.assertEqual(self.ordenar(id_).status_code, 200)
        r = self.ordenar(id_)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Ya hay una orden", r.get_json()["error"])
        self.assertEqual(len(self.consultar(id_, token).get_json()["ordenes"]), 1)

    def test_las_negativas(self):
        id_, _ = self.comandable()
        sin, _ = self.comandable("Sin ordenes", canales=[])
        self.guardar(sin, "Sin ordenes", [], puede=False, vence=None)
        casos = [(id_, "no_existe", "pulso", 404), (id_, "puerta", "pulso", 400), (id_, "bomba", "prender", 400),
                 (id_, "calefaccion", "pulso", 400), (id_, "bomba", "rafaga", 400), (id_, None, None, 404),
                 (sin, "bomba", "pulso", 409), ("no_existe", "bomba", "pulso", 404)]
        for origen, canal, accion, codigo in casos:
            with self.subTest(origen=origen, canal=canal, accion=accion):
                r = self.ordenar(origen, canal, accion)
                self.assertEqual(r.status_code, codigo, r.get_json())
                self.assertIn("error", r.get_json())

    def test_cuerpo_que_no_es_un_objeto(self):
        id_, _ = self.comandable()
        self.assertEqual(self.admin.post(f"/api/iot/origenes/{id_}/ordenes", json=[1]).status_code, 400)
        self.assertEqual(self.admin.post(f"/api/iot/origenes/{id_}/ordenes", data="no json", content_type="application/json").status_code, 400)


class TestSeguridadDelEsp32(Base):
    def test_token_malo_sin_token_y_origen_inexistente_contestan_igual(self):
        id_, token = self.comandable()
        pedidos = [lambda: self.consultar(id_, "malo"), lambda: self.consultar(id_, None),
                   lambda: self.consultar("no_existe", token), lambda: self.confirmar(id_, 1, "malo"),
                   lambda: self.confirmar(id_, 1, None), lambda: self.confirmar("no_existe", 1, token)]
        rs = []
        for pedido in pedidos:
            auth._intentos_fallidos.clear()           # lo que se compara es la respuesta, no cuántos intentos van
            rs.append(pedido())
        self.assertEqual({r.status_code for r in rs}, {403})
        self.assertEqual(len({r.get_data() for r in rs}), 1)

    def test_los_intentos_malos_cuentan_para_el_bloqueo(self):
        id_, token = self.comandable()
        for _ in range(auth._LIMITE_INTENTOS):
            self.consultar(id_, "malo")
        self.assertEqual(self.consultar(id_, token).status_code, 429)

    def test_el_token_de_un_origen_no_sirve_en_otro(self):
        a, token_a = self.comandable("Equipo A")
        b, token_b = self.comandable("Equipo B")
        orden_b = self.ordenar(b).get_json()["orden"]["id"]
        self.assertEqual(self.consultar(b, token_a).status_code, 403)
        self.assertEqual(self.consultar(a, token_a).get_json(), {"ordenes": []})
        self.consultar(b, token_b)
        # la orden de B, con el token de A hacia A: contesta como una que no existe
        r = self.confirmar(a, orden_b, token_a)
        s = self.confirmar(a, 99999, token_a)
        self.assertEqual((r.status_code, r.get_data()), (s.status_code, s.get_data()))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.listado(b)["ordenes"][0]["estado"], "entregada")

    def test_un_equipo_modbus_contesta_el_mismo_403(self):
        mb = self.admin.post("/api/iot/origenes", json={"tipo": "modbus_tcp", "nombre": "PLC", "host": "10.9.9.9",
                                                       "ventana_lectura_min": 15}).get_json()["origen"]["id"]
        _, token = self.comandable()
        self.assertEqual(self.consultar(mb, token).status_code, 403)

    def test_un_origen_que_no_puede_comandar_contesta_409_a_la_consulta(self):
        id_, token = self.comandable("Solo lectura", canales=[])
        self.guardar(id_, "Solo lectura", [], puede=False, vence=None)
        r = self.consultar(id_, token)
        self.assertEqual(r.status_code, 409)
        self.assertIn("no recibe órdenes", r.get_json()["error"])

    def test_confirmaciones_basura_son_4xx_nunca_500(self):
        id_, token = self.comandable()
        i = self.ordenar(id_).get_json()["orden"]["id"]
        self.consultar(id_, token)
        for cuerpo in ([1], "x", 5, None, {"error": 5}, {"error": ["a"]}, {"error": {"x": 1}}, {}):
            with self.subTest(cuerpo=cuerpo):
                r = self.confirmar(id_, i, token, cuerpo)
                self.assertLess(r.status_code, 500, r.get_data())
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{i}/ok", data="esto no es json {", content_type="application/json",
                              headers={"X-Token": token})
        self.assertEqual(r.status_code, 400)
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{i}/ok", data=json.dumps({"error": "x" * 70000}),
                              content_type="application/json", headers={"X-Token": token})
        self.assertEqual(r.status_code, 413)
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/abc/ok", headers={"X-Token": token})
        self.assertEqual(r.status_code, 400)

    def test_un_json_anidado_hasta_el_infinito_es_400(self):
        id_, token = self.comandable()
        i = self.ordenar(id_).get_json()["orden"]["id"]
        self.consultar(id_, token)
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{i}/ok", data="[" * 60000, content_type="application/json",
                              headers={"X-Token": token})
        self.assertEqual(r.status_code, 400)

    def test_confirmar_dos_veces_y_una_pendiente(self):
        id_, token = self.comandable()
        i = self.ordenar(id_).get_json()["orden"]["id"]
        self.assertEqual(self.confirmar(id_, i, token).status_code, 409)             # todavía no se la entregaron
        self.consultar(id_, token)
        self.assertEqual(self.confirmar(id_, i, token).status_code, 200)
        self.assertEqual(self.confirmar(id_, i, token).status_code, 200)             # idempotente
        self.assertEqual(self.confirmar(id_, i, token, {"error": "ahora falló"}).status_code, 409)

    def test_la_base_ocupada_es_503(self):
        import sqlite3
        id_, token = self.comandable()
        with mock.patch.object(iot_ordenes, "entregar", side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(self.consultar(id_, token).status_code, 503)

    def test_get_y_post_no_se_mezclan(self):
        id_, token = self.comandable()
        self.assertEqual(self.publico.post(f"/api/iot/ingreso/{id_}/ordenes", headers={"X-Token": token}).status_code, 405)
        self.assertEqual(self.publico.get(f"/api/iot/ingreso/{id_}/ordenes/1/ok", headers={"X-Token": token}).status_code, 405)


class TestCancelaciones(Base):
    def test_apagar_puede_recibir_ordenes_cancela_lo_vivo(self):
        id_, token = self.comandable()
        self.ordenar(id_)
        self.ordenar(id_, "calefaccion", "prender")
        self.consultar(id_, token)                                            # las dos entregadas
        self.guardar(id_, "ESP32 del corral", [], puede=False, vence=None)
        lista = {o["canal"]: o for o in iot_ordenes.listar(id_)}
        self.assertEqual({o["estado"] for o in lista.values()}, {"sin_confirmar"})
        self.assertIn("Puede recibir órdenes", lista["bomba"]["resultado"])

    def test_sacar_la_salida_cancela_sus_ordenes_y_no_las_de_las_otras(self):
        id_, token = self.comandable()
        self.ordenar(id_, "bomba", "pulso")
        self.ordenar(id_, "calefaccion", "prender")
        self.guardar(id_, "ESP32 del corral", [SALIDAS[1], SALIDAS[2]])
        estados = {o["canal"]: o["estado"] for o in iot_ordenes.listar(id_)}
        self.assertEqual(estados, {"bomba": "vencida", "calefaccion": "pendiente"})
        self.assertEqual([o["canal"] for o in self.consultar(id_, token).get_json()["ordenes"]], ["calefaccion"])

    def test_cambiar_el_modo_de_una_salida_cancela_lo_vivo(self):
        id_, token = self.comandable()
        self.ordenar(id_, "bomba", "pulso")
        self.guardar(id_, "ESP32 del corral", [dict(SALIDAS[0], modo="sostenida", pulso_s=None), SALIDAS[1], SALIDAS[2]])
        self.assertEqual(iot_ordenes.listar(id_)[0]["estado"], "vencida")

    def test_un_guardado_que_falla_no_cancela_nada(self):
        id_, token = self.comandable()
        self.ordenar(id_)
        r = self.guardar(id_, "ESP32 del corral", [{"clave": "con.punto", "grupo": "salida", "modo": "pulso", "pulso_s": 1}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(iot_ordenes.listar(id_)[0]["estado"], "pendiente")

    def test_guardar_sin_los_campos_nuevos_no_apaga_el_comando(self):
        # un navegador con la pantalla de antes de actualizar no manda `puede_comandar`: no puede apagarlo en silencio
        id_, token = self.comandable()
        self.ordenar(id_)
        r = self.admin.post(f"/api/iot/origenes/{id_}", json={"nombre": "ESP32 del corral", "formato": "generico",
                                                              "ventana_lectura_min": 120, "canales": SALIDAS})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(self.listado(id_)["puede_comandar"])
        self.assertEqual(iot_ordenes.listar(id_)[0]["estado"], "pendiente")

    def test_eliminar_el_equipo_cancela_y_no_hereda_nada_el_que_se_cree_despues(self):
        id_, token = self.comandable()
        self.ordenar(id_)
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}").status_code, 200)
        nuevo, _ = self.comandable()
        self.assertEqual(nuevo, id_)
        self.assertEqual(iot_ordenes.listar(id_), [])

    def test_chirpstack_no_puede_comandar(self):
        r = self.admin.post("/api/iot/origenes", json={"nombre": "Nodo", "formato": "chirpstack", "ventana_lectura_min": 60})
        id_ = r.get_json()["origen"]["id"]
        r = self.guardar(id_, "Nodo", [], formato="chirpstack")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ChirpStack", r.get_json()["error"])


class TestRevisionFinal(Base):
    def test_un_numero_de_orden_enorme_es_404_no_500(self):
        id_, token = self.comandable()
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/{'9' * 30}/ok", headers={"X-Token": token})
        self.assertEqual(r.status_code, 404)
        r = self.publico.post(f"/api/iot/ingreso/{id_}/ordenes/-5/ok", headers={"X-Token": token})
        self.assertEqual(r.status_code, 404)

    def test_el_administrador_puede_cancelar_una_orden(self):
        id_, token = self.comandable()
        n = self.ordenar(id_).get_json()["orden"]["id"]
        self.assertEqual(self.publico.delete(f"/api/iot/origenes/{id_}/ordenes/{n}").status_code, 401)
        self.assertEqual(self.operario.delete(f"/api/iot/origenes/{id_}/ordenes/{n}").status_code, 403)
        r = self.admin.delete(f"/api/iot/origenes/{id_}/ordenes/{n}")
        self.assertEqual((r.status_code, r.get_json()["estado"]), (200, "vencida"))
        self.assertEqual(self.consultar(id_, token).get_json(), {"ordenes": []})
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}/ordenes/{n}").status_code, 409)        # ya terminó
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}/ordenes/999").status_code, 404)
        self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}/ordenes/abc").status_code, 400)
        self.assertEqual(self.admin.delete("/api/iot/origenes/no_existe/ordenes/1").status_code, 404)
        self.assertEqual(self.ordenar(id_).status_code, 200)                                                # y el canal quedó libre

    def test_apagar_reemplaza_a_un_prender_que_no_se_confirmo(self):
        id_, token = self.comandable()
        self.ordenar(id_, "calefaccion", "prender")
        self.consultar(id_, token)                                           # entregada; el equipo no avisa
        self.assertEqual(self.ordenar(id_, "calefaccion", "apagar").status_code, 200)
        self.assertEqual(self.ordenar(id_, "calefaccion", "apagar").status_code, 409)
        self.assertEqual([o["accion"] for o in self.consultar(id_, token).get_json()["ordenes"]], ["apagar"])

    def test_una_base_ocupada_es_503_en_las_rutas_del_administrador(self):
        import sqlite3
        id_, token = self.comandable()
        ocupada = sqlite3.OperationalError("database is locked")
        with mock.patch.object(iot_ordenes, "crear", side_effect=ocupada):
            self.assertEqual(self.ordenar(id_).status_code, 503)
        with mock.patch.object(iot_ordenes, "listar", side_effect=ocupada):
            self.assertEqual(self.admin.get("/api/iot/origenes").status_code, 503)
            self.assertEqual(self.admin.get(f"/api/iot/origenes/{id_}/ordenes").status_code, 503)
        with mock.patch.object(iot_ordenes, "cancelar_orden", side_effect=ocupada):
            self.assertEqual(self.admin.delete(f"/api/iot/origenes/{id_}/ordenes/1").status_code, 503)

    def test_si_falla_la_cancelacion_al_guardar_la_orden_de_la_salida_sacada_igual_no_se_entrega(self):
        import sqlite3
        id_, token = self.comandable()
        self.ordenar(id_, "bomba", "pulso")
        with mock.patch.object(iot_ordenes, "cancelar", side_effect=sqlite3.OperationalError("database is locked")):
            r = self.guardar(id_, "ESP32 del corral", [SALIDAS[1], SALIDAS[2]])        # se saca «bomba»
        self.assertEqual(r.status_code, 503)
        self.assertIn("guardó el equipo", r.get_json()["error"])
        self.assertEqual(self.consultar(id_, token).get_json(), {"ordenes": []})
        self.assertEqual(iot_ordenes.listar(id_)[0]["estado"], "vencida")

    def test_si_falla_la_cancelacion_al_eliminar_es_503_y_el_equipo_se_elimino(self):
        import sqlite3
        id_, token = self.comandable()
        self.ordenar(id_)
        with mock.patch.object(iot_ordenes, "cancelar", side_effect=sqlite3.OperationalError("database is locked")):
            r = self.admin.delete(f"/api/iot/origenes/{id_}")
        self.assertEqual(r.status_code, 503)
        self.assertIsNone(iot_origenes.obtener(id_))
        self.assertEqual(self.consultar(id_, token).status_code, 403)                  # sin equipo no hay a quién entregar

    def test_bajar_el_vencimiento_acorta_las_ordenes_que_ya_esperaban(self):
        id_, token = self.comandable(vence=3600)
        n = self.ordenar(id_).get_json()["orden"]["id"]
        self.assertEqual(self.guardar(id_, "ESP32 del corral", SALIDAS, vence=1).status_code, 200)
        o = next(x for x in iot_ordenes.listar(id_) if x["id"] == n)
        self.assertLessEqual((datetime.fromisoformat(o["vence"]) - datetime.fromisoformat(o["creada"])).total_seconds(), 1)

    def test_subir_el_vencimiento_no_toca_las_ordenes_que_ya_esperaban(self):
        id_, token = self.comandable(vence=30)
        n = self.ordenar(id_).get_json()["orden"]["id"]
        antes = next(x for x in iot_ordenes.listar(id_) if x["id"] == n)["vence"]
        self.assertEqual(self.guardar(id_, "ESP32 del corral", SALIDAS, vence=3600).status_code, 200)
        self.assertEqual(next(x for x in iot_ordenes.listar(id_) if x["id"] == n)["vence"], antes)

    def test_el_historial_de_un_equipo_eliminado_se_conserva(self):
        id_, token = self.comandable()
        self.ordenar(id_)
        self.admin.delete(f"/api/iot/origenes/{id_}")
        con = iot_ordenes._conectar()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ordenes_origen").fetchone()[0], 1)
        finally:
            con.close()


class TestListado(Base):
    def test_el_listado_no_trae_el_token_y_trae_las_ultimas_diez(self):
        id_, token = self.comandable()
        for k in range(12):
            i = self.ordenar(id_).get_json()["orden"]["id"]
            self.consultar(id_, token)
            self.confirmar(id_, i, token)
        texto = self.admin.get("/api/iot/origenes").get_data(as_text=True)
        self.assertNotIn(token, texto)
        self.assertNotIn("token_hash", texto)
        o = self.listado(id_)
        self.assertEqual(len(o["ordenes"]), 10)
        self.assertEqual(o["ordenes"][0]["id"], 12)

    def test_un_origen_que_no_comanda_trae_la_lista_vacia(self):
        id_, token = self.comandable("Solo lectura", canales=[])
        self.guardar(id_, "Solo lectura", [], puede=False, vence=None)
        o = self.listado(id_)
        self.assertEqual((o["puede_comandar"], o["ordenes"]), (False, []))

    def test_la_ruta_de_ordenes_del_admin(self):
        id_, token = self.comandable()
        self.ordenar(id_)
        r = self.admin.get(f"/api/iot/origenes/{id_}/ordenes")
        self.assertEqual((r.status_code, [o["canal"] for o in r.get_json()["ordenes"]]), (200, ["bomba"]))
        self.assertEqual(self.admin.get("/api/iot/origenes/no_existe/ordenes").status_code, 404)


if __name__ == "__main__":
    unittest.main()
