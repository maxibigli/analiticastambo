"""Orígenes HTTP de datos (ESP32 / ChirpStack): alta, canales, token y búsquedas.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_origenes -v
"""
import os
import tempfile
import unittest
from unittest import mock

import iot_origenes

CATALOGO = {"temp_corral": (-10, 45), "temp_leche": (0, 40), "hum_corral": (0, 100)}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "iot_origenes.json")
        p = mock.patch.object(iot_origenes, "_RUTA", self.ruta)
        p.start()
        self.addCleanup(p.stop)

    def guardar(self, id_, canales, nombre="ESP32 del corral", formato="generico", ventana=120,
                ocupados=(), en_uso=()):
        iot_origenes.guardar_origen(id_, nombre, formato, ventana, canales, sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(ocupados), entradas_en_uso=set(en_uso))

    def leer_bytes(self):
        with open(self.ruta, "rb") as f:
            return f.read()

    def origen_con_canales(self):
        origen, token = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.guardar(origen["id"], [
            {"clave": "temp", "grupo": "entrada_analogica", "nombre": "Temp corral", "sensor": "temp_corral"},
            {"clave": "puerta", "grupo": "entrada_digital", "nombre": "Puerta"},
        ])
        return origen, token


class TestCrearYToken(Base):
    def test_el_token_se_muestra_una_vez_y_en_disco_solo_queda_su_hash(self):
        origen, token = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.assertGreaterEqual(len(token), 43)
        self.assertNotIn(token.encode(), self.leer_bytes())
        self.assertNotIn("token", origen)
        self.assertEqual(len(origen["token_hash"]), 64)

    def test_token_valido_acepta_el_correcto_y_rechaza_el_resto(self):
        origen, token = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.assertTrue(iot_origenes.token_valido(origen["id"], token))
        self.assertFalse(iot_origenes.token_valido(origen["id"], token + "x"))
        self.assertFalse(iot_origenes.token_valido(origen["id"], ""))
        self.assertFalse(iot_origenes.token_valido(origen["id"], None))
        self.assertFalse(iot_origenes.token_valido("no_existe", token))
        self.assertFalse(iot_origenes.token_valido("no_existe", None))

    def test_un_origen_inexistente_tambien_hace_la_comparacion(self):
        # Para que no se pueda distinguir un origen que existe de uno que no por el tiempo de respuesta.
        with mock.patch.object(iot_origenes.hmac, "compare_digest", wraps=iot_origenes.hmac.compare_digest) as cmp:
            iot_origenes.token_valido("no_existe", "algo")
        self.assertEqual(cmp.call_count, 1)

    def test_regenerar_invalida_el_token_anterior(self):
        origen, viejo = iot_origenes.crear("ESP32 del corral", "generico", 120)
        nuevo = iot_origenes.regenerar_token(origen["id"])
        self.assertNotEqual(viejo, nuevo)
        self.assertFalse(iot_origenes.token_valido(origen["id"], viejo))
        self.assertTrue(iot_origenes.token_valido(origen["id"], nuevo))
        self.assertNotIn(nuevo.encode(), self.leer_bytes())

    def test_regenerar_un_origen_inexistente_falla(self):
        with self.assertRaises(ValueError):
            iot_origenes.regenerar_token("no_existe")

    def test_el_id_sale_del_nombre_y_no_se_repite(self):
        a, _ = iot_origenes.crear("ESP32 del corral", "generico", 120)
        b, _ = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.assertEqual(a["id"], "esp32_del_corral")
        self.assertEqual(b["id"], "esp32_del_corral_2")

    def test_un_nombre_con_tildes_y_simbolos(self):
        o, _ = iot_origenes.crear("Galpón N°1 (Sur)", "generico", 60)
        self.assertEqual(o["id"], "galpon_n1_sur")

    def test_principal_no_se_puede_usar_de_id(self):
        o, _ = iot_origenes.crear("Principal", "generico", 120)
        self.assertNotEqual(o["id"], "principal")
        self.assertEqual(o["id"], "principal_2")

    def test_id_largo_se_corta_y_sigue_siendo_unico(self):
        nombre = "un nombre larguisimo para un nodo tambo"
        a, _ = iot_origenes.crear(nombre, "generico", 60)
        b, _ = iot_origenes.crear(nombre, "generico", 60)
        self.assertLessEqual(len(a["id"]), 24)
        self.assertLessEqual(len(b["id"]), 24)
        self.assertNotEqual(a["id"], b["id"])

    def test_la_ventana_es_obligatoria_y_entera(self):
        for mala in (None, 0, -5, 1.5, "abc", "120", True, float("nan")):
            with self.subTest(ventana=mala):
                with self.assertRaises(ValueError):
                    iot_origenes.crear("ESP32", "generico", mala)
        self.assertEqual(iot_origenes.cargar(), [])

    def test_formato_desconocido_nombre_vacio(self):
        with self.assertRaises(ValueError):
            iot_origenes.crear("ESP32", "xml", 60)
        with self.assertRaises(ValueError):
            iot_origenes.crear("   ", "generico", 60)

    def test_el_origen_21_se_rechaza(self):
        for i in range(20):
            iot_origenes.crear(f"nodo {i}", "generico", 60)
        with self.assertRaises(ValueError):
            iot_origenes.crear("uno de más", "generico", 60)
        self.assertEqual(len(iot_origenes.cargar()), 20)


class TestGuardarOrigen(Base):
    def setUp(self):
        super().setUp()
        self.origen, _ = iot_origenes.crear("ESP32 del corral", "generico", 120)
        self.id = self.origen["id"]

    def assertRechaza(self, canales, texto=None, **kw):
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar(self.id, canales, **kw)
        if texto:
            self.assertIn(texto, str(cm.exception))
        self.assertEqual(self.leer_bytes(), antes, "un guardado inválido tiene que dejar el archivo igual")

    def test_guarda_y_conserva_el_id_el_token_y_la_fecha(self):
        self.guardar(self.id, [{"clave": "temp", "grupo": "entrada_analogica", "nombre": "Temp", "sensor": "temp_corral"}],
                     nombre="Otro nombre", formato="chirpstack", ventana=30)
        o = iot_origenes.obtener(self.id)
        self.assertEqual((o["id"], o["nombre"], o["formato"], o["ventana_lectura_min"]),
                         (self.id, "Otro nombre", "chirpstack", 30))
        self.assertEqual(o["token_hash"], self.origen["token_hash"])
        self.assertEqual(o["creado"], self.origen["creado"])
        self.assertEqual(o["tipo"], "http")

    def test_factor_y_offset_vacios_son_la_identidad(self):
        self.guardar(self.id, [{"clave": "temp", "grupo": "entrada_analogica", "factor": "", "offset": None}])
        c = iot_origenes.obtener(self.id)["canales"][0]
        self.assertEqual((c["factor"], c["offset"]), (1, 0))

    def test_factor_y_offset_numericos(self):
        self.guardar(self.id, [{"clave": "temp", "grupo": "entrada_analogica", "factor": "0.1", "offset": -5}])
        c = iot_origenes.obtener(self.id)["canales"][0]
        self.assertEqual((c["factor"], c["offset"]), (0.1, -5))

    def test_claves_invalidas(self):
        for clave in ("con.punto", "con espacio", "", "x" * 41, "ñandú", None):
            with self.subTest(clave=clave):
                self.assertRechaza([{"clave": clave, "grupo": "entrada_digital"}])

    def test_clave_repetida(self):
        self.assertRechaza([{"clave": "a", "grupo": "entrada_digital"}, {"clave": "a", "grupo": "entrada_analogica"}])

    def test_una_salida_no_se_acepta(self):
        self.assertRechaza([{"clave": "rele", "grupo": "salida"}], "todavía no manejan salidas")

    def test_grupo_desconocido(self):
        self.assertRechaza([{"clave": "a", "grupo": "otra_cosa"}])

    def test_mas_de_32_canales(self):
        self.assertRechaza([{"clave": f"c{i}", "grupo": "entrada_digital"} for i in range(33)])
        self.guardar(self.id, [{"clave": f"c{i}", "grupo": "entrada_digital"} for i in range(32)])

    def test_sensor_en_un_canal_digital(self):
        self.assertRechaza([{"clave": "puerta", "grupo": "entrada_digital", "sensor": "temp_corral"}])

    def test_sensor_que_no_esta_en_el_catalogo(self):
        self.assertRechaza([{"clave": "t", "grupo": "entrada_analogica", "sensor": "no_existe"}])

    def test_sensor_ocupado_por_una_entrada_del_m300(self):
        self.assertRechaza([{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_leche"}],
                           ocupados={"temp_leche"})

    def test_mismo_sensor_en_dos_canales(self):
        self.assertRechaza([{"clave": "t1", "grupo": "entrada_analogica", "sensor": "temp_corral"},
                            {"clave": "t2", "grupo": "entrada_analogica", "sensor": "temp_corral"}])

    def test_mismo_sensor_en_dos_origenes(self):
        self.guardar(self.id, [{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_corral"}])
        otro, _ = iot_origenes.crear("Otro nodo", "generico", 60)
        antes = self.leer_bytes()
        with self.assertRaises(ValueError):
            self.guardar(otro["id"], [{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_corral"}])
        self.assertEqual(self.leer_bytes(), antes)

    def test_el_mismo_origen_puede_volver_a_guardar_su_propio_sensor(self):
        c = [{"clave": "t", "grupo": "entrada_analogica", "sensor": "temp_corral"}]
        self.guardar(self.id, c)
        self.guardar(self.id, c)

    def test_factor_cero_o_texto(self):
        for factor in (0, "0", "abc", float("nan"), float("inf"), True):
            with self.subTest(factor=factor):
                self.assertRechaza([{"clave": "t", "grupo": "entrada_analogica", "factor": factor}])
        self.assertRechaza([{"clave": "t", "grupo": "entrada_analogica", "offset": "mucho"}])

    def test_origen_inexistente(self):
        with self.assertRaises(ValueError):
            self.guardar("no_existe", [])

    def test_ventana_y_formato_se_validan_al_guardar(self):
        self.assertRechaza([], ventana=0)
        self.assertRechaza([], formato="xml")
        self.assertRechaza([], nombre="")

    def test_canales_que_no_es_una_lista(self):
        self.assertRechaza({"clave": "x"})

    def test_sacar_una_entrada_digital_en_uso_se_rechaza_y_la_nombra(self):
        self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_digital"}])
        antes = self.leer_bytes()
        with self.assertRaises(ValueError) as cm:
            self.guardar(self.id, [], en_uso={f"{self.id}.puerta"})
        self.assertIn(f"{self.id}.puerta", str(cm.exception))
        self.assertEqual(self.leer_bytes(), antes)

    def test_pasar_una_entrada_digital_en_uso_a_analogica_tambien_se_rechaza(self):
        self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_digital"}])
        with self.assertRaises(ValueError):
            self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_analogica"}], en_uso={f"{self.id}.puerta"})

    def test_conservar_la_entrada_en_uso_si_se_puede(self):
        self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_digital"}])
        self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_digital", "nombre": "Portón"}],
                     en_uso={f"{self.id}.puerta"})

    def test_eliminar_con_un_canal_en_uso_se_rechaza(self):
        self.guardar(self.id, [{"clave": "puerta", "grupo": "entrada_digital"}])
        with self.assertRaises(ValueError) as cm:
            iot_origenes.eliminar(self.id, {f"{self.id}.puerta"})
        self.assertIn(f"{self.id}.puerta", str(cm.exception))
        self.assertIsNotNone(iot_origenes.obtener(self.id))

    def test_eliminar_sin_uso(self):
        iot_origenes.eliminar(self.id, set())
        self.assertIsNone(iot_origenes.obtener(self.id))

    def test_eliminar_inexistente(self):
        with self.assertRaises(ValueError):
            iot_origenes.eliminar("no_existe", set())


class TestArchivo(Base):
    def test_un_archivo_roto_no_se_pisa_sin_dejar_copia(self):
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write('{"origenes": [ esto se rompió a mano')
        iot_origenes.crear("ESP32", "generico", 60)
        copias = [n for n in os.listdir(self.dir.name) if n.startswith("iot_origenes.json.roto-")]
        self.assertEqual(len(copias), 1)
        with open(os.path.join(self.dir.name, copias[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"origenes": [ esto se rompió a mano')
        self.assertEqual(len(iot_origenes.cargar()), 1)

    def test_un_archivo_sano_no_deja_copias(self):
        iot_origenes.crear("ESP32", "generico", 60)
        iot_origenes.crear("Otro", "generico", 60)
        self.assertEqual([n for n in os.listdir(self.dir.name) if ".roto-" in n], [])

    def test_si_windows_tiene_el_archivo_tomado_un_instante_se_reintenta_el_reemplazo(self):
        real = os.replace
        intentos = []

        def tomado(origen, destino):
            intentos.append(1)
            if len(intentos) < 3:
                raise PermissionError("el archivo está en uso")
            return real(origen, destino)
        with mock.patch.object(iot_origenes.os, "replace", tomado), mock.patch.object(iot_origenes.time, "sleep"):
            iot_origenes.crear("ESP32", "generico", 60)
        self.assertEqual(len(intentos), 3)
        self.assertEqual(len(iot_origenes.cargar()), 1)
        self.assertEqual([n for n in os.listdir(self.dir.name) if n.endswith(".tmp")], [])

    def test_si_el_reemplazo_no_se_logra_nunca_se_deja_un_temporal(self):
        with mock.patch.object(iot_origenes.os, "replace", side_effect=PermissionError("en uso")), \
                mock.patch.object(iot_origenes.time, "sleep"):
            with self.assertRaises(PermissionError):
                iot_origenes.crear("ESP32", "generico", 60)
        self.assertEqual([n for n in os.listdir(self.dir.name) if n.endswith(".tmp")], [])

    def test_leer_con_el_archivo_tomado_un_instante_reintenta_en_vez_de_devolver_vacio(self):
        iot_origenes.crear("ESP32", "generico", 60)
        real = open
        fallos = []

        def abrir(*a, **kw):
            if a and a[0] == self.ruta and len(fallos) < 2:
                fallos.append(1)
                raise PermissionError("en uso")
            return real(*a, **kw)
        with mock.patch("builtins.open", abrir), mock.patch.object(iot_origenes.time, "sleep"):
            self.assertEqual(len(iot_origenes.cargar()), 1)


class TestBusquedas(Base):
    def test_sin_archivo(self):
        self.assertEqual(iot_origenes.cargar(), [])
        self.assertEqual(iot_origenes.entradas_digitales(), ())
        self.assertEqual(iot_origenes.sensores_asignados(), set())
        self.assertEqual(iot_origenes.ventanas(), {"entradas": {}, "sensores": {}})
        self.assertIsNone(iot_origenes.obtener("lo_que_sea"))

    def test_lo_guardado_se_refleja_en_las_busquedas(self):
        origen, _ = self.origen_con_canales()
        self.assertEqual(origen["id"], "esp32_del_corral")
        self.assertEqual(iot_origenes.entradas_digitales(), ("esp32_del_corral.puerta",))
        self.assertEqual(iot_origenes.sensores_asignados(), {"temp_corral"})
        self.assertEqual(iot_origenes.ventanas(), {"entradas": {"esp32_del_corral.puerta": 120},
                                                   "sensores": {"temp_corral": 120}})
        self.assertEqual(iot_origenes.canal_global("esp32_del_corral", "puerta"), "esp32_del_corral.puerta")

    def test_principal(self):
        self.assertEqual(iot_origenes.PRINCIPAL_ID, "principal")
        self.assertEqual(iot_origenes.VENTANA_PRINCIPAL_MIN, 30)

    def test_archivo_roto(self):
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write("esto no es json {{{")
        self.assertEqual(iot_origenes.cargar(), [])
        self.assertEqual(iot_origenes.ventanas(), {"entradas": {}, "sensores": {}})

    def test_archivo_con_forma_rara(self):
        for contenido in ("[]", '{"origenes": 5}', '{"origenes": [1, "x", {"id": "principal"}]}', "null"):
            with self.subTest(contenido=contenido):
                with open(self.ruta, "w", encoding="utf-8") as f:
                    f.write(contenido)
                self.assertEqual(iot_origenes.cargar(), [])

    def test_un_origen_roto_no_tira_a_los_demas(self):
        origen, _ = self.origen_con_canales()
        import json
        with open(self.ruta, encoding="utf-8") as f:
            datos = json.load(f)
        datos["origenes"].insert(0, {"id": "con.punto", "nombre": "x", "formato": "generico",
                                     "ventana_lectura_min": 5, "token_hash": "0" * 64, "canales": []})
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        self.assertEqual([o["id"] for o in iot_origenes.cargar()], [origen["id"]])

    def test_dos_origenes_con_el_mismo_id_en_el_archivo_se_queda_el_primero(self):
        import json
        o = {"id": "dup", "nombre": "x", "formato": "generico", "ventana_lectura_min": 5, "token_hash": "0" * 64,
             "canales": []}
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "origenes": [o, {**o, "nombre": "segundo"}]}, f)
        self.assertEqual([x["nombre"] for x in iot_origenes.cargar()], ["x"])

    def test_cargar_devuelve_copias_que_no_cambian_el_archivo(self):
        origen, _ = self.origen_con_canales()
        antes = self.leer_bytes()
        iot_origenes.cargar()[0]["canales"].clear()
        self.assertEqual(self.leer_bytes(), antes)
        self.assertEqual(len(iot_origenes.obtener(origen["id"])["canales"]), 2)


if __name__ == "__main__":
    unittest.main()
