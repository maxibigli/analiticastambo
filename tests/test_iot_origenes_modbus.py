"""Orígenes Modbus TCP (otros equipos que LactIA consulta): alta, canales y validaciones.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes_modbus -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import iot_conexion
import iot_origenes

CATALOGO = {"temp_corral": (-10, 45), "temp_leche": (0, 40), "hum_corral": (0, 100)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "iot_origenes.json")
        ruta_conexion = os.path.join(self.dir.name, "iot_conexion.json")
        with open(ruta_conexion, "w", encoding="utf-8") as f:
            json.dump({"host": "192.168.1.1", "port": 502}, f)
        for p in (mock.patch.object(iot_origenes, "_RUTA", self.ruta), mock.patch.object(iot_conexion, "_RUTA", ruta_conexion)):
            p.start()
            self.addCleanup(p.stop)

    def leer_bytes(self):
        with open(self.ruta, "rb") as f:
            return f.read()

    def crear(self, nombre="PLC sala de máquinas", host="192.168.1.60", port=None, unit_id=None, ventana=15):
        return iot_origenes.crear_modbus(nombre, host, port, unit_id, ventana)

    def guardar(self, id_, canales, nombre="PLC", host="192.168.1.60", port=502, unit_id=1, ventana=15,
                ocupados=(), en_uso=()):
        iot_origenes.guardar_modbus(id_, nombre, host, port, unit_id, ventana, canales, sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(ocupados), entradas_en_uso=set(en_uso))


def dig(clave="puerta", **kw):
    return {"clave": clave, "grupo": "entrada_digital", "nombre": clave, "funcion": "discrete_input", "direccion": 0, **kw}


def ana(clave="temp", **kw):
    return {"clave": clave, "grupo": "entrada_analogica", "nombre": clave, "funcion": "input_register", "direccion": 10,
            "tipo_dato": "uint16", **kw}


class TestAlta(Base):
    def test_alta_feliz_con_los_defectos_de_protocolo(self):
        o = self.crear()
        self.assertEqual((o["id"], o["tipo"], o["host"], o["port"], o["unit_id"], o["ventana_lectura_min"]),
                         ("plc_sala_de_maquinas", "modbus_tcp", "192.168.1.60", 502, 1, 15))
        self.assertNotIn("token_hash", o)
        self.assertNotIn("formato", o)
        self.assertEqual(iot_origenes.obtener(o["id"]), o)

    def test_puerto_y_unit_id_explicitos_y_en_texto(self):
        o = self.crear(port="1502", unit_id="7", host="PLC-1.tambo.local")
        self.assertEqual((o["port"], o["unit_id"], o["host"]), (1502, 7, "PLC-1.tambo.local"))

    def test_datos_invalidos_no_guardan_nada(self):
        malos = [dict(host=""), dict(host="   "), dict(host="con espacios"), dict(host="http://192.168.1.2"),
                 dict(host="a" * 300), dict(host=None), dict(host="192.168.1.2:502"),
                 dict(port=0), dict(port=70000), dict(port="abc"), dict(port=True), dict(port=-1),
                 dict(unit_id=0), dict(unit_id=248), dict(unit_id="x"), dict(unit_id=1.5),
                 dict(ventana=None), dict(ventana=0), dict(ventana=1.5), dict(ventana="15"), dict(ventana=True)]
        for kw in malos:
            with self.subTest(**{k: repr(v)[:20] for k, v in kw.items()}):
                with self.assertRaises(ValueError):
                    self.crear(**kw)
        self.assertEqual(iot_origenes.cargar(), [])
        self.assertFalse(os.path.exists(self.ruta))

    def test_nombre_vacio(self):
        with self.assertRaises(ValueError):
            self.crear(nombre="  ")

    def test_el_mismo_host_y_puerto_se_rechazan_nombrando_al_otro(self):
        self.crear("Primero")
        with self.assertRaises(ValueError) as cm:
            self.crear("Segundo")
        self.assertIn("Primero", str(cm.exception))
        self.crear("Segundo", port=1502)                    # el mismo host con otro puerto es otro equipo
        self.crear("Tercero", host="192.168.1.61")

    def test_el_host_se_compara_sin_importar_mayusculas(self):
        self.crear("Primero", host="PLC.local")
        with self.assertRaises(ValueError):
            self.crear("Segundo", host="plc.LOCAL")

    def test_el_host_y_puerto_del_principal_se_rechazan(self):
        with self.assertRaises(ValueError) as cm:
            self.crear(host="192.168.1.1")
        self.assertIn("principal", str(cm.exception).lower())
        self.crear(host="192.168.1.1", port=1502)

    def test_escribir_el_mismo_host_de_otra_forma_tambien_choca(self):
        self.crear("Primero", host="192.168.1.60")
        for otro in ("192.168.001.060", "192.168.1.60.", " 192.168.1.60 "):
            with self.subTest(host=otro):
                with self.assertRaises(ValueError):
                    self.crear("Otro", host=otro)
        with self.assertRaises(ValueError):                      # el principal, escrito como localhost / 127.0.0.1
            iot_conexion.guardar("localhost", 502)
            self.crear("Local", host="127.0.0.1")
        with self.assertRaises(ValueError):
            self.crear("Local 2", host="LOCALHOST")

    def test_el_origen_21_se_rechaza_contando_los_dos_tipos(self):
        for i in range(10):
            iot_origenes.crear(f"web {i}", "generico", 60)
        for i in range(10):
            self.crear(f"plc {i}", host=f"10.0.0.{i + 10}")
        with self.assertRaises(ValueError):
            self.crear("uno de más", host="10.0.0.99")
        with self.assertRaises(ValueError):
            iot_origenes.crear("otro web", "generico", 60)

    def test_los_ids_siguen_las_mismas_reglas(self):
        a = self.crear("Principal", host="10.0.0.1")
        self.assertEqual(a["id"], "principal_2")
        b = self.crear("Principal", host="10.0.0.2")
        self.assertEqual(b["id"], "principal_3")


class TestGuardar(Base):
    def setUp(self):
        super().setUp()
        self.o = self.crear()
        self.id = self.o["id"]

    def assertRechaza(self, canales, texto=None, **kw):
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar(self.id, canales, **kw)
        if texto:
            self.assertIn(texto, str(cm.exception))
        self.assertEqual(self.leer_bytes(), antes, "un guardado inválido tiene que dejar el archivo igual")

    def test_guarda_el_equipo_y_los_canales(self):
        self.guardar(self.id, [dig(), ana(sensor="temp_corral", factor="0.1", offset=5)],
                     nombre="Otro nombre", host="10.0.0.7", port=1502, unit_id=3, ventana=45)
        o = iot_origenes.obtener(self.id)
        self.assertEqual((o["nombre"], o["host"], o["port"], o["unit_id"], o["ventana_lectura_min"], o["tipo"]),
                         ("Otro nombre", "10.0.0.7", 1502, 3, 45, "modbus_tcp"))
        c = {x["clave"]: x for x in o["canales"]}
        self.assertEqual((c["temp"]["factor"], c["temp"]["offset"], c["temp"]["sensor"], c["temp"]["tipo_dato"]),
                         (0.1, 5, "temp_corral", "uint16"))
        self.assertEqual((c["puerta"]["funcion"], c["puerta"]["direccion"], c["puerta"]["unit_id"], c["puerta"]["invertido"]),
                         ("discrete_input", 0, None, False))

    def test_las_cuatro_funciones_digitales(self):
        for i, f in enumerate(("discrete_input", "coil", "input_register", "holding_register")):
            self.guardar(self.id, [dig(f"c{i}", funcion=f, direccion=5)])
            self.assertEqual(iot_origenes.obtener(self.id)["canales"][0]["funcion"], f)

    def test_notacion_modicon(self):
        self.guardar(self.id, [dig("a", direccion=10001), dig("b", funcion="holding_register", direccion=40001),
                               dig("c", funcion="input_register", direccion="30005"), dig("d", funcion="coil", direccion=3)])
        d = {c["clave"]: c["direccion"] for c in iot_origenes.obtener(self.id)["canales"]}
        self.assertEqual(d, {"a": 0, "b": 0, "c": 4, "d": 3})

    def test_unit_id_del_canal_y_invertido(self):
        self.guardar(self.id, [dig(unit_id="9", invertido=True)])
        c = iot_origenes.obtener(self.id)["canales"][0]
        self.assertEqual((c["unit_id"], c["invertido"]), (9, True))
        self.assertRechaza([dig(unit_id=0)])
        self.assertRechaza([dig(unit_id=300)])
        self.assertRechaza([dig(invertido="si")])

    def test_direccion_obligatoria_y_valida(self):
        for mala in (None, "", -1, 70000, "abc", True, 1.5):
            with self.subTest(direccion=mala):
                self.assertRechaza([dig(direccion=mala)])

    def test_funcion_invalida(self):
        for mala in (None, "", "write_coil", "input"):
            with self.subTest(funcion=mala):
                self.assertRechaza([dig(funcion=mala)])
        self.assertRechaza([ana(funcion="coil")])
        self.assertRechaza([ana(funcion="discrete_input")])

    def test_tipo_dato_del_analogico_obligatorio(self):
        for ok in ("uint16", "int16", "float32_ab_cd"):
            self.guardar(self.id, [ana(tipo_dato=ok)])
        for malo in (None, "", "float64", "uint32"):
            with self.subTest(tipo_dato=malo):
                self.assertRechaza([ana(tipo_dato=malo)])

    def test_factor_y_offset(self):
        self.guardar(self.id, [ana(factor="", offset=None)])
        c = iot_origenes.obtener(self.id)["canales"][0]
        self.assertEqual((c["factor"], c["offset"]), (1, 0))
        for factor in (0, "0", "abc", float("nan"), float("inf"), True):
            with self.subTest(factor=factor):
                self.assertRechaza([ana(factor=factor)])
        self.assertRechaza([ana(offset="mucho")])

    def test_los_digitales_no_llevan_sensor_y_los_analogicos_si(self):
        self.assertRechaza([dig(sensor="temp_corral")])
        self.assertRechaza([ana(sensor="no_existe")])
        self.assertRechaza([ana(sensor="temp_leche")], ocupados={"temp_leche"})
        self.assertRechaza([ana("a", sensor="temp_corral"), ana("b", direccion=11, sensor="temp_corral")])

    def test_el_mismo_sensor_en_dos_equipos(self):
        self.guardar(self.id, [ana(sensor="temp_corral")])
        otro = self.crear("Otro PLC", host="192.168.1.61")
        antes = self.leer_bytes()
        with self.assertRaises(ValueError):
            self.guardar(otro["id"], [ana(sensor="temp_corral")], host="192.168.1.61")
        self.assertEqual(self.leer_bytes(), antes)

    def test_el_mismo_sensor_en_un_equipo_http_y_uno_modbus(self):
        http, _ = iot_origenes.crear("Nodo", "generico", 60)
        iot_origenes.guardar_origen(http["id"], "Nodo", "generico", 60, [{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_corral"}],
                                    sensores_analogicos=CATALOGO, sensores_ocupados=set(), entradas_en_uso=set())
        self.assertRechaza([ana(sensor="temp_corral")])

    def test_las_salidas_se_rechazan(self):
        self.assertRechaza([{"clave": "rele", "grupo": "salida", "funcion": "coil", "direccion": 0}],
                           "todavía no manejan salidas")
        self.assertRechaza([{"clave": "x", "grupo": "otra", "funcion": "coil", "direccion": 0}])

    def test_claves_invalidas_y_repetidas(self):
        for clave in ("con.punto", "con espacio", "", "x" * 41, None):
            with self.subTest(clave=clave):
                self.assertRechaza([dig(clave)])
        self.assertRechaza([dig("a"), dig("a", direccion=1)])

    def test_mas_de_32_canales(self):
        self.assertRechaza([dig(f"c{i}", direccion=i) for i in range(33)])
        self.guardar(self.id, [dig(f"c{i}", direccion=i) for i in range(32)])

    def test_dos_canales_en_el_mismo_punto_se_rechazan_nombrando_los_dos(self):
        self.assertRechaza([dig("uno", direccion=7), dig("dos", direccion=7)], "uno")
        with self.assertRaises(ValueError) as cm:
            self.guardar(self.id, [dig("uno", direccion=7), dig("dos", direccion=7)])
        self.assertIn("dos", str(cm.exception))

    def test_el_unit_id_efectivo_cuenta_para_el_punto(self):
        # uno hereda el del equipo (1) y el otro lo pone igual: es el mismo punto
        self.assertRechaza([dig("uno", direccion=7), dig("dos", direccion=7, unit_id=1)])
        self.guardar(self.id, [dig("uno", direccion=7), dig("dos", direccion=7, unit_id=2)])        # otro unit ID: otro punto
        self.guardar(self.id, [dig("uno", direccion=7), dig("dos", direccion=7, funcion="coil")])    # otra función: otro punto

    def test_un_decimal_de_32_bits_ocupa_dos_registros_y_no_se_puede_pisar(self):
        f32 = ana("f", direccion=0, tipo_dato="float32_ab_cd")
        self.assertRechaza([f32, ana("pisa", direccion=1)], "pisa")                    # el uint16 cae en el segundo registro
        self.assertRechaza([ana("u", direccion=1), dict(f32, clave="f", direccion=0)])  # y al revés
        self.guardar(self.id, [f32, ana("sigue", direccion=2)])                          # el siguiente registro libre sí
        self.guardar(self.id, [f32, ana("hold", direccion=1, funcion="holding_register")])   # otra función: otro espacio
        self.guardar(self.id, [f32, ana("otro", direccion=1, unit_id=9)])                # otro unit ID: otro espacio
        self.guardar(self.id, [dict(f32, clave="b"), dig("bit", funcion="coil", direccion=1)])

    def test_el_mismo_punto_en_otro_equipo_es_valido(self):
        otro = self.crear("Otro PLC", host="192.168.1.61")
        self.guardar(self.id, [dig(direccion=7)])
        self.guardar(otro["id"], [dig(direccion=7)], host="192.168.1.61")

    def test_cambiar_el_host_a_uno_ya_usado(self):
        self.crear("Otro PLC", host="192.168.1.61")
        self.assertRechaza([], host="192.168.1.61")
        self.assertRechaza([], host="192.168.1.1")        # el principal
        self.guardar(self.id, [], host="192.168.1.60")    # quedarse en el propio no es un choque

    def test_validaciones_del_equipo_al_guardar(self):
        for kw in (dict(host=""), dict(host="a b"), dict(port=0), dict(port=70000), dict(unit_id=0), dict(unit_id=248),
                   dict(ventana=0), dict(ventana=None), dict(nombre="")):
            with self.subTest(**{k: repr(v) for k, v in kw.items()}):
                self.assertRechaza([], **kw)

    def test_canales_que_no_es_una_lista(self):
        self.assertRechaza({"clave": "x"})
        self.assertRechaza("texto")

    def test_equipo_inexistente(self):
        with self.assertRaises(ValueError):
            self.guardar("no_existe", [])

    def test_sacar_una_entrada_en_uso_se_rechaza_y_la_nombra(self):
        self.guardar(self.id, [dig("puerta")])
        g = f"{self.id}.puerta"
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar(self.id, [], en_uso={g})
        self.assertIn(g, str(cm.exception))
        with self.assertRaises(ValueError):
            self.guardar(self.id, [ana("puerta")], en_uso={g})
        self.assertEqual(self.leer_bytes(), antes)
        self.guardar(self.id, [dig("puerta", direccion=4)], en_uso={g})      # cambiarle la dirección sí se puede

    def test_eliminar(self):
        self.guardar(self.id, [dig("puerta")])
        with self.assertRaises(ValueError) as cm:
            iot_origenes.eliminar(self.id, {f"{self.id}.puerta"})
        self.assertIn(f"{self.id}.puerta", str(cm.exception))
        iot_origenes.eliminar(self.id, set())
        self.assertIsNone(iot_origenes.obtener(self.id))


class TestMezcla(Base):
    def setUp(self):
        super().setUp()
        self.http, self.token = iot_origenes.crear("ESP32 del corral", "generico", 120)
        iot_origenes.guardar_origen(self.http["id"], "ESP32 del corral", "generico", 120, [
            {"clave": "temp", "grupo": "entrada_analogica", "sensor": "temp_corral"},
            {"clave": "puerta", "grupo": "entrada_digital"}],
            sensores_analogicos=CATALOGO, sensores_ocupados=set(), entradas_en_uso=set())
        self.mb = self.crear()
        self.guardar(self.mb["id"], [dig("bomba", direccion=3), ana("hum", sensor="hum_corral")], ventana=15)

    def test_las_busquedas_traen_los_dos_tipos(self):
        self.assertEqual(set(iot_origenes.entradas_digitales()),
                         {f"{self.http['id']}.puerta", f"{self.mb['id']}.bomba"})
        self.assertEqual(iot_origenes.sensores_asignados(), {"temp_corral", "hum_corral"})
        self.assertEqual(iot_origenes.ventanas(), {
            "entradas": {f"{self.http['id']}.puerta": 120, f"{self.mb['id']}.bomba": 15},
            "sensores": {"temp_corral": 120, "hum_corral": 15}})

    def test_un_equipo_modbus_no_tiene_token(self):
        self.assertFalse(iot_origenes.token_valido(self.mb["id"], self.token))
        self.assertFalse(iot_origenes.token_valido(self.mb["id"], ""))
        self.assertFalse(iot_origenes.token_valido(self.mb["id"], None))
        with self.assertRaises(ValueError):
            iot_origenes.regenerar_token(self.mb["id"])
        with self.assertRaises(ValueError):
            iot_origenes.guardar_origen(self.mb["id"], "x", "generico", 5, [], sensores_analogicos={}, sensores_ocupados=set(),
                                        entradas_en_uso=set())

    def test_un_equipo_http_no_se_guarda_como_modbus(self):
        with self.assertRaises(ValueError):
            self.guardar(self.http["id"], [])

    def test_el_token_de_un_http_sigue_andando(self):
        self.assertTrue(iot_origenes.token_valido(self.http["id"], self.token))
        nuevo = iot_origenes.regenerar_token(self.http["id"])
        self.assertTrue(iot_origenes.token_valido(self.http["id"], nuevo))
        self.assertFalse(iot_origenes.token_valido(self.http["id"], self.token))

    def test_un_equipo_modbus_roto_en_el_archivo_no_tira_a_los_demas(self):
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        datos["origenes"].append({"id": "roto", "nombre": "x", "tipo": "modbus_tcp", "host": "con espacios", "port": 502,
                                  "unit_id": 1, "ventana_lectura_min": 5, "canales": []})
        datos["origenes"].append({"id": "roto2", "nombre": "x", "tipo": "modbus_tcp", "host": "10.0.0.5", "port": 0,
                                  "unit_id": 1, "ventana_lectura_min": 5, "canales": []})
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        self.assertEqual({o["id"] for o in iot_origenes.cargar()}, {self.http["id"], self.mb["id"]})

    def test_un_canal_roto_en_el_archivo_se_saltea_sin_tirar_al_equipo(self):
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        mb = next(o for o in datos["origenes"] if o["id"] == self.mb["id"])
        mb["canales"].append({"clave": "mal", "grupo": "entrada_digital", "funcion": "coil", "direccion": -4})
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        self.assertEqual([c["clave"] for c in iot_origenes.obtener(self.mb["id"])["canales"]], ["bomba", "hum"])

    def test_un_registro_sin_tipo_es_http(self):
        # los archivos de la etapa 3 no traían `tipo`
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        for o in datos["origenes"]:
            if o["id"] == self.http["id"]:
                del o["tipo"]
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        self.assertEqual(iot_origenes.obtener(self.http["id"])["tipo"], "http")


if __name__ == "__main__":
    unittest.main()
