"""El botón «Probar» de las salidas del mapa Modbus: encola un pulso, dice cómo salió y se puede retirar.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_probar_salida -v
"""
import os
import sqlite3
import unittest
from unittest import mock

import iot_lavado
import iot_monitoreo
import voz_comandos
from tests.test_iot_origenes_api import Base as BaseApi

TUNEL = {"CF-Connecting-IP": "203.0.113.9"}


class Base(BaseApi):
    def setUp(self):
        super().setUp()
        self.ruta = os.path.join(self.dir.name, "gateway.db")
        iot_lavado._conectar_db(self.ruta).close()   # crea las tablas, como cuando corre el gateway
        # iot_monitoreo toma la base del gateway de iot_lavado: se apunta a una de prueba (y se crean sus tablas).
        for p in (mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(self.ruta)),
                  mock.patch.object(voz_comandos, "_en_uso_por_lavado", return_value=False),
                  mock.patch.object(voz_comandos, "estado", return_value={}),
                  mock.patch.object(voz_comandos, "apagados_pendientes", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def pedidos(self):
        con = sqlite3.connect(self.ruta)
        try:
            return con.execute("SELECT id, canal, ejecutado, resultado FROM comandos_actuador ORDER BY id").fetchall()
        finally:
            con.close()

    def probar(self, canal="do_1", cliente=None, **kw):
        return (cliente or self.admin).post("/api/iot/mapa/probar", json={"canal": canal}, **kw)


class TestProbar(Base):
    def test_encola_un_pulso_y_dice_cuanto_dura(self):
        r = self.probar("do_3")
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual((j["ok"], j["pulso_s"]), (True, 0.5))        # 0,5 s: el pulso de fábrica
        self.assertEqual(self.pedidos(), [(j["pedido"], "do_3", 0, None)])

    def test_un_canal_que_no_es_una_salida_se_rechaza_sin_encolar(self):
        for canal in ("di_1", "bomba", "", "do_99"):
            self.assertEqual(self.probar(canal).status_code, 400, canal)
        self.assertEqual(self.pedidos(), [])

    def test_por_el_tunel_de_internet_no(self):
        r = self.probar(headers=TUNEL)
        self.assertEqual(r.status_code, 403)
        self.assertIn("red del tambo", r.get_json()["error"])
        self.assertEqual(self.pedidos(), [])

    def test_solo_el_administrador(self):
        self.assertNotEqual(self.probar(cliente=self.operario).status_code, 200)
        self.assertNotEqual(self.probar(cliente=self.publico).status_code, 200)
        self.assertEqual(self.pedidos(), [])

    def test_una_salida_que_el_lavado_tiene_prendida_no_se_pulsa(self):
        # Un pulso es «prender, esperar, apagar»: el apagado final cortaría la etapa del lavado.
        with mock.patch.object(voz_comandos, "_en_uso_por_lavado", side_effect=lambda c: c == "do_2"):
            self.assertEqual(self.probar("do_2").status_code, 409)
            self.assertEqual(self.probar("do_1").status_code, 200)
        self.assertEqual([p[1] for p in self.pedidos()], ["do_1"])

    def test_una_salida_sostenida_por_voz_no_se_pulsa(self):
        with mock.patch.object(voz_comandos, "estado", return_value={"do_4": "2026-11-24T10:00:00"}):
            r = self.probar("do_4")
        self.assertEqual(r.status_code, 409)
        self.assertIn("voz", r.get_json()["error"])
        self.assertEqual(self.pedidos(), [])

    def test_una_salida_con_apagado_por_voz_pendiente_no_se_pulsa(self):
        with mock.patch.object(voz_comandos, "apagados_pendientes", return_value={"do_5": "2026-11-24T10:00:00"}):
            self.assertEqual(self.probar("do_5").status_code, 409)
        self.assertEqual(self.pedidos(), [])


class TestResultado(Base):
    def consultar(self, pedido):
        return self.admin.get(f"/api/iot/mapa/probar/{pedido}")

    def test_pendiente_hasta_que_el_gateway_lo_toma(self):
        pedido = self.probar().get_json()["pedido"]
        self.assertEqual(self.consultar(pedido).get_json(), {"ejecutado": False, "resultado": None})

    def test_cuando_el_gateway_lo_ejecuta_se_ve_como_salio(self):
        pedido = self.probar().get_json()["pedido"]
        con = sqlite3.connect(self.ruta)
        con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = 'ok' WHERE id = ?", (pedido,))
        con.commit()
        con.close()
        self.assertEqual(self.consultar(pedido).get_json(), {"ejecutado": True, "resultado": "ok"})

    def test_un_error_del_equipo_llega_a_la_pantalla(self):
        pedido = self.probar().get_json()["pedido"]
        con = sqlite3.connect(self.ruta)
        con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = 'error: el M300 rechazó el pulso' WHERE id = ?", (pedido,))
        con.commit()
        con.close()
        self.assertEqual(self.consultar(pedido).get_json()["resultado"], "error: el M300 rechazó el pulso")

    def test_un_pedido_que_no_existe(self):
        self.assertEqual(self.consultar(9999).status_code, 404)

    def test_solo_el_administrador_ve_el_resultado(self):
        pedido = self.probar().get_json()["pedido"]
        self.assertNotEqual(self.operario.get(f"/api/iot/mapa/probar/{pedido}").status_code, 200)


class TestCancelar(Base):
    def cancelar(self, pedido, cliente=None):
        return (cliente or self.admin).delete(f"/api/iot/mapa/probar/{pedido}")

    def test_un_pedido_que_el_gateway_no_tomo_se_retira_y_ya_no_se_dispara(self):
        pedido = self.probar().get_json()["pedido"]
        self.assertEqual(self.cancelar(pedido).get_json(), {"cancelado": True})
        self.assertEqual(self.pedidos(), [(pedido, "do_1", 1, "cancelado")])
        con = sqlite3.connect(self.ruta)
        try:   # lo que busca iot_lavado en cada ciclo: ya no encuentra nada
            self.assertEqual(con.execute("SELECT id FROM comandos_actuador WHERE ejecutado = 0").fetchall(), [])
        finally:
            con.close()

    def test_uno_que_el_gateway_ya_ejecuto_no_se_toca(self):
        pedido = self.probar().get_json()["pedido"]
        con = sqlite3.connect(self.ruta)
        con.execute("UPDATE comandos_actuador SET ejecutado = 1, resultado = 'ok' WHERE id = ?", (pedido,))
        con.commit()
        con.close()
        self.assertEqual(self.cancelar(pedido).get_json(), {"cancelado": False})
        self.assertEqual(self.pedidos(), [(pedido, "do_1", 1, "ok")])

    def test_uno_que_no_existe(self):
        self.assertEqual(self.cancelar(9999).get_json(), {"cancelado": False})

    def test_solo_el_administrador_cancela(self):
        pedido = self.probar().get_json()["pedido"]
        self.assertNotEqual(self.cancelar(pedido, self.operario).status_code, 200)
        self.assertEqual(self.pedidos()[0][2], 0)


class TestSinCambiarLoDeSiempre(Base):
    def test_el_boton_de_la_pantalla_esp32_sigue_igual(self):
        # La ruta de la pantalla ESP32 usa solicitar_pulso: sigue encolando y contestando 202 en la red del tambo.
        r = self.publico.post("/api/iot/pantalla/actuador", json={"canal": "do_1"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual([p[1] for p in self.pedidos()], ["do_1"])
        self.assertEqual(self.publico.post("/api/iot/pantalla/actuador", json={"canal": "do_1"}, headers=TUNEL).status_code, 403)


if __name__ == "__main__":
    unittest.main()
