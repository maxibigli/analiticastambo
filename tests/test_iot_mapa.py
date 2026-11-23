"""Mapa Modbus por canal del equipo principal (M300): entradas digitales y salidas.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_mapa -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_canales
import iot_mapa


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "iot_mapa.json")
        p = mock.patch.object(iot_mapa, "_RUTA", self.ruta)
        p.start()
        self.addCleanup(p.stop)

    def escribir(self, texto):
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write(texto)


class TestMapa(Base):
    def test_sin_archivo_todo_es_de_fabrica(self):
        self.assertEqual(iot_mapa.entrada("lavado_rotativa"),
                         {"funcion": "discrete_input", "direccion": 0, "unit_id": 1, "invertido": False})
        self.assertEqual(iot_mapa.entrada("di_32")["direccion"], 31)
        self.assertEqual(iot_mapa.salida("do_1"), {"direccion": 0, "unit_id": 1, "pulso_s": 0.5})
        self.assertEqual(iot_mapa.salida("do_32")["direccion"], 31)
        self.assertEqual(len(iot_mapa.entradas()), 32)
        self.assertEqual(len(iot_mapa.salidas()), 32)

    def test_guardar_y_leer_un_cambio(self):
        e = {"funcion": "coil", "direccion": 40, "unit_id": 3, "invertido": True}
        s = {"direccion": 9, "unit_id": 2, "pulso_s": 1.5}
        iot_mapa.guardar({"di_5": e}, {"do_2": s})
        self.assertEqual(iot_mapa.entrada("di_5"), e)
        self.assertEqual(iot_mapa.salida("do_2"), s)
        self.assertEqual(iot_mapa.entrada("di_6"), iot_mapa.de_fabrica_entrada("di_6"))

    def test_solo_se_guarda_lo_que_difiere_de_fabrica(self):
        # Foco 5: una fila igual a la de fábrica no queda en el archivo (si no, un cambio futuro del defecto no se vería).
        iot_mapa.guardar({"di_5": dict(iot_mapa.de_fabrica_entrada("di_5"))}, {"do_2": {"pulso_s": 2}})
        c = iot_mapa.cambios()
        self.assertEqual(c["entradas"], {})
        self.assertEqual(list(c["salidas"]), ["do_2"])

    def test_un_campo_en_blanco_vuelve_al_valor_de_fabrica(self):
        iot_mapa.guardar({"di_5": {"funcion": "", "direccion": "", "unit_id": None, "invertido": False}}, {})
        self.assertEqual(iot_mapa.cambios(), {"entradas": {}, "salidas": {}})

    def test_direccion_modicon_se_convierte(self):
        iot_mapa.guardar({"di_3": {"funcion": "discrete_input", "direccion": 10003},
                          "di_4": {"funcion": "input_register", "direccion": 30005},
                          "di_5": {"funcion": "holding_register", "direccion": 40001},
                          "di_6": {"funcion": "coil", "direccion": 10}}, {})
        self.assertEqual(iot_mapa.entrada("di_3")["direccion"], 2)
        self.assertEqual(iot_mapa.entrada("di_4")["direccion"], 4)
        self.assertEqual(iot_mapa.entrada("di_5")["direccion"], 0)
        self.assertEqual(iot_mapa.entrada("di_6")["direccion"], 10)

    def test_direccion_pisada_por_otra_entrada_se_rechaza(self):
        # Foco 1: di_5 en la dirección 0 la pisa a lavado_rotativa (misma función y mismo unit ID).
        with self.assertRaises(ValueError) as cm:
            iot_mapa.guardar({"di_5": {"direccion": 0}}, {})
        self.assertIn("di_5", str(cm.exception))
        self.assertIn("lavado_rotativa", str(cm.exception))
        self.assertFalse(os.path.exists(self.ruta))
        # Otra función o otro unit ID: es otro punto del equipo, se acepta.
        iot_mapa.guardar({"di_5": {"funcion": "coil", "direccion": 0}}, {})
        iot_mapa.guardar({"di_5": {"direccion": 0, "unit_id": 2}}, {})

    def test_dos_salidas_en_la_misma_bobina_se_rechazan(self):
        with self.assertRaises(ValueError) as cm:
            iot_mapa.guardar({}, {"do_2": {"direccion": 0}})
        self.assertIn("do_2", str(cm.exception))
        self.assertIn("do_1", str(cm.exception))

    def test_valores_invalidos_se_rechazan_sin_tocar_lo_anterior(self):
        iot_mapa.guardar({"di_5": {"direccion": 100}}, {"do_3": {"unit_id": 2}})
        antes = iot_mapa.cambios()
        malos_e = [("di_5", {"funcion": "hola"}), ("di_5", {"direccion": -1}), ("di_5", {"direccion": 65536}),
                   ("di_5", {"direccion": 1.5}), ("di_5", {"direccion": "abc"}), ("di_5", {"direccion": True}),
                   ("di_5", {"unit_id": 0}), ("di_5", {"unit_id": 248}), ("di_5", {"unit_id": 2.5}),
                   ("di_99", {"direccion": 5})]
        for clave, fila in malos_e:
            with self.subTest(entrada=(clave, fila)):
                with self.assertRaises(ValueError):
                    iot_mapa.guardar({clave: fila}, {})
                self.assertEqual(iot_mapa.cambios(), antes)
        for pulso in (0, -1, float("nan"), float("inf"), True, 11):
            with self.subTest(pulso=pulso):
                with self.assertRaises(ValueError):
                    iot_mapa.guardar({}, {"do_3": {"pulso_s": pulso}})
                self.assertEqual(iot_mapa.cambios(), antes)
        with self.assertRaises(ValueError):
            iot_mapa.guardar({}, {"do_99": {"pulso_s": 1}})
        iot_mapa.guardar({}, {"do_3": {"pulso_s": 10}})
        self.assertEqual(iot_mapa.salida("do_3")["pulso_s"], 10)

    def test_archivo_roto_cae_a_fabrica(self):
        # Foco 2: el gateway lee este archivo en cada ciclo; uno roto no lo puede tirar abajo.
        fabrica = iot_mapa.de_fabrica_entrada("di_4")
        for texto in ("no es json", "[1, 2]", json.dumps({"entradas": [], "salidas": 3}),
                      json.dumps({"entradas": {"di_99": {}, "di_4": "x"}})):
            with self.subTest(texto=texto):
                self.escribir(texto)
                self.assertEqual(iot_mapa.entrada("di_4"), fabrica)
                self.assertEqual(len(iot_mapa.entradas()), 32)
                self.assertEqual(len(iot_mapa.salidas()), 32)

    def test_un_cambio_guardado_a_medias_en_el_archivo_se_completa_con_fabrica(self):
        self.escribir(json.dumps({"entradas": {"di_4": {"direccion": 7}}}))
        self.assertEqual(iot_mapa.entrada("di_4"),
                         {"funcion": "discrete_input", "direccion": 7, "unit_id": 1, "invertido": False})


class TestApi(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(iot_canales, "_RUTA", os.path.join(self.dir.name, "nombres.json"))
        p.start()
        self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def mapa(self):
        return self.cli.get("/api/iot/mapa").get_json()

    def test_get_trae_las_32_de_cada_una_con_su_defecto(self):
        r = self.cli.get("/api/iot/mapa")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual((len(d["entradas"]), len(d["salidas"])), (32, 32))
        self.assertEqual(d["funciones"], list(iot_mapa.FUNCIONES_ENTRADA))
        self.assertEqual(d["pulso_max_s"], iot_mapa.PULSO_MAX_S)
        self.assertEqual(d["entradas"][2]["clave"], "di_3")
        self.assertEqual(d["entradas"][2]["numero"], 3)
        self.assertEqual(d["entradas"][2]["defecto"]["direccion"], 2)
        self.assertEqual(d["salidas"][0]["defecto"], {"direccion": 0, "unit_id": 1, "pulso_s": 0.5})
        self.assertFalse(any(f["modificada"] for f in d["entradas"] + d["salidas"]))

    def test_post_guarda_y_get_lo_marca_modificada(self):
        r = self.cli.post("/api/iot/mapa", json={"entradas": {"di_5": {"direccion": 100}}, "salidas": {}})
        self.assertEqual(r.status_code, 200)
        fila = self.mapa()["entradas"][4]
        self.assertTrue(fila["modificada"])
        self.assertEqual(fila["direccion"], 100)
        self.assertEqual(fila["defecto"]["direccion"], 4)

    def test_post_con_una_direccion_pisada_da_400_con_el_mensaje(self):
        r = self.cli.post("/api/iot/mapa", json={"entradas": {"di_5": {"direccion": 0}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lavado_rotativa", r.get_json()["error"])
        self.assertFalse(any(f["modificada"] for f in self.mapa()["entradas"]))

    def test_post_con_formato_invalido_da_400(self):
        self.assertEqual(self.cli.post("/api/iot/mapa", json={"entradas": [1], "salidas": {}}).status_code, 400)
        self.assertEqual(self.cli.post("/api/iot/mapa", json={"entradas": {}, "salidas": "x"}).status_code, 400)

    def test_sin_sesion_de_admin_no_entra(self):
        anonimo = app.app.test_client()
        self.assertNotEqual(anonimo.get("/api/iot/mapa").status_code, 200)
        self.assertNotEqual(anonimo.post("/api/iot/mapa", json={"entradas": {}, "salidas": {}}).status_code, 200)

    def test_el_nombre_es_el_que_puso_el_tambo(self):
        iot_canales.guardar({"di_5": "Térmico bomba"})
        d = self.mapa()
        self.assertEqual(d["entradas"][4]["nombre"], "Térmico bomba")
        self.assertEqual(d["entradas"][5]["nombre"], "Entrada 6")


if __name__ == "__main__":
    unittest.main()
