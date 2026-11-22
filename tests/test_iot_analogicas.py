"""Entradas analógicas (4-20 mA) del gateway M300 asignadas a los vúmetros del panel IoT.

Cada entrada analógica se asigna a UN sensor de los del panel (temp. leche, vacío, etc.) con su escala: qué número crudo
da el M300 a 4 mA y a 20 mA (se calibra con un simulador), y qué valor del sensor son 4 y 20 mA. La conversión y el
guardado en `lecturas_sensor` son lo que hace que el vúmetro se encienda solo.

Sin red ni hardware: el cliente Modbus es de mentira y las bases son archivos temporales.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_analogicas -v
"""
import datetime
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_analogicas as ia
import iot_lavado
import iot_monitoreo

SENSORES = {"temp_leche": (0, 40), "temp_lavado": (0, 90), "vacio_general": (0, 60)}


class Resp:
    def __init__(self, registros=None, error=False):
        self.registers = registros or []
        self._error = error

    def isError(self):
        return self._error


def registros_de(valor):
    """Un decimal de 32 bits «AB CD» (el formato de los puntos AI del M300) son dos registros, el alto primero."""
    return list(struct.unpack(">HH", struct.pack(">f", valor)))


class ClienteFalso:
    """Devuelve, por dirección del primer registro, el decimal de 32 bits que se le puso (2 registros)."""

    def __init__(self, valores):
        self.valores = valores
        self.pedidos = []
        self.cantidades = []

    def read_input_registers(self, address, count=1, device_id=1):
        self.pedidos.append(address)
        self.cantidades.append(count)
        if address not in self.valores:
            return Resp(error=True)
        valor = self.valores[address]
        regs = valor if isinstance(valor, list) else registros_de(valor)
        return Resp(regs[:count])

    def close(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(ia, "_RUTA", os.path.join(self.dir.name, "iot_analogicas.json"))
        p.start()
        self.addCleanup(p.stop)
        self.ruta_db = os.path.join(self.dir.name, "iot.db")
        self.con = iot_lavado._conectar_db(self.ruta_db)
        self.addCleanup(self.con.close)

    def parchar_conexion_del_panel(self):
        """iot_monitoreo abre y CIERRA su propia conexión en cada llamada: se le da una nueva al mismo archivo."""
        p = mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(self.ruta_db))
        p.start()
        self.addCleanup(p.stop)


def config_basica():
    return {"ai_1": {"sensor": "temp_leche", "direccion": 0, "raw_4": 4000, "raw_20": 20000, "min": 0, "max": 40}}


class TestConvertir(unittest.TestCase):
    CFG = {"sensor": "temp_leche", "direccion": 0, "raw_4": 4000, "raw_20": 20000, "min": 0, "max": 100}

    def test_a_4_ma_es_el_minimo_y_a_20_el_maximo(self):
        self.assertEqual(ia.convertir(4000, self.CFG)["valor"], 0)
        self.assertEqual(ia.convertir(20000, self.CFG)["valor"], 100)

    def test_a_mitad_de_escala_da_la_mitad_del_rango(self):
        r = ia.convertir(12000, self.CFG)
        self.assertEqual(r["ma"], 12.0)
        self.assertEqual(r["valor"], 50.0)
        self.assertFalse(r["sin_senal"])

    def test_sin_calibrar_no_inventa_un_valor(self):
        self.assertIsNone(ia.convertir(12000, {**self.CFG, "raw_4": None}))
        self.assertIsNone(ia.convertir(12000, {**self.CFG, "raw_20": None}))

    def test_sin_rango_del_sensor_no_inventa_un_valor(self):
        self.assertIsNone(ia.convertir(12000, {**self.CFG, "min": None}))

    def test_lazo_cortado_se_marca_sin_senal_y_no_da_valor(self):
        # Un lazo abierto lee ~0 mA: si se guardara como 0 °C, un cable suelto parecería leche congelada.
        r = ia.convertir(0, self.CFG)
        self.assertTrue(r["sin_senal"])
        self.assertIsNone(r["valor"])

    def test_corriente_por_encima_del_rango_tambien_es_sin_senal(self):
        self.assertTrue(ia.convertir(24000, self.CFG)["sin_senal"])

    def test_un_poco_debajo_de_4_ma_sigue_siendo_una_lectura_valida(self):
        r = ia.convertir(3800, self.CFG)
        self.assertFalse(r["sin_senal"])
        self.assertLess(r["valor"], 0)

    def test_escala_invertida_funciona(self):
        # Hay transmisores de nivel/vacío que dan 20 mA en el mínimo.
        cfg = {**self.CFG, "raw_4": 20000, "raw_20": 4000}
        self.assertEqual(ia.convertir(20000, cfg)["ma"], 4.0)
        self.assertEqual(ia.convertir(4000, cfg)["ma"], 20.0)


class TestGuardar(Base):
    def test_guardar_y_cargar_dan_lo_mismo(self):
        ia.guardar(config_basica(), SENSORES)
        self.assertEqual(ia.cargar()["ai_1"], config_basica()["ai_1"])

    def test_el_rango_en_blanco_toma_el_del_sensor(self):
        ia.guardar({"ai_1": {"sensor": "temp_lavado", "direccion": 0}}, SENSORES)
        c = ia.cargar()["ai_1"]
        self.assertEqual((c["min"], c["max"]), (0, 90))

    def test_un_rango_propio_pisa_el_del_sensor(self):
        ia.guardar({"ai_1": {"sensor": "temp_lavado", "direccion": 0, "min": 20, "max": 120}}, SENSORES)
        c = ia.cargar()["ai_1"]
        self.assertEqual((c["min"], c["max"]), (20, 120))

    def test_sin_archivo_no_hay_nada_asignado(self):
        self.assertEqual(ia.cargar(), {})

    def test_canal_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_99": {"sensor": "temp_leche"}}, SENSORES)

    def test_sensor_desconocido_se_rechaza(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "no_existe"}}, SENSORES)

    def test_el_ith_no_se_puede_asignar_porque_se_calcula(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "ith"}}, SENSORES)

    def test_un_sensor_en_dos_entradas_se_rechaza(self):
        # Dos entradas pisándose en el mismo vúmetro: no hay forma de saber cuál manda.
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "temp_leche"}, "ai_2": {"sensor": "temp_leche"}}, SENSORES)

    def test_dos_entradas_con_la_misma_direccion_se_rechazan(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 3},
                        "ai_2": {"sensor": "temp_lavado", "direccion": 3}}, SENSORES)

    def test_direcciones_que_se_pisan_se_rechazan_porque_cada_entrada_ocupa_dos_registros(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 3},
                        "ai_2": {"sensor": "temp_lavado", "direccion": 4}}, SENSORES)

    def test_direcciones_contiguas_de_dos_en_dos_se_aceptan(self):
        ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 0},
                    "ai_2": {"sensor": "temp_lavado", "direccion": 2},
                    "ai_3": {"sensor": "vacio_general", "direccion": 4}}, SENSORES)
        self.assertEqual(sorted(c["direccion"] for c in ia.cargar().values()), [0, 2, 4])

    def test_mismos_crudos_en_4_y_20_ma_se_rechazan(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "temp_leche", "raw_4": 100, "raw_20": 100}}, SENSORES)

    def test_rango_con_minimo_mayor_o_igual_al_maximo_se_rechaza(self):
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "temp_leche", "min": 50, "max": 10}}, SENSORES)

    def test_direccion_fuera_de_rango_o_no_numerica_se_rechaza(self):
        for mala in (-1, 70000, "abc", 1.5):
            with self.subTest(direccion=mala), self.assertRaises(ValueError):
                ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": mala}}, SENSORES)

    def test_los_campos_vacios_del_formulario_se_toman_como_sin_dato(self):
        ia.guardar({"ai_1": {"sensor": "", "direccion": "", "raw_4": "", "raw_20": "", "min": "", "max": ""},
                    "ai_2": {"sensor": "temp_leche", "direccion": "2", "raw_4": "4000", "raw_20": "20000"}}, SENSORES)
        cfg = ia.cargar()
        self.assertNotIn("ai_1", cfg)            # sin nada cargado no se guarda
        self.assertEqual(cfg["ai_2"]["direccion"], 2)
        self.assertEqual(cfg["ai_2"]["raw_4"], 4000)

    def test_un_guardado_que_falla_no_pisa_lo_anterior(self):
        ia.guardar(config_basica(), SENSORES)
        with self.assertRaises(ValueError):
            ia.guardar({"ai_1": {"sensor": "no_existe"}}, SENSORES)
        self.assertEqual(ia.cargar()["ai_1"]["sensor"], "temp_leche")


class TestProcesarAnalogicas(Base):
    AHORA = datetime.datetime(2026, 11, 22, 15, 0, 0)

    def correr(self, valores, ultimo=None, ahora=None):
        cliente = ClienteFalso(valores)
        ultimo = {} if ultimo is None else ultimo
        iot_lavado.procesar_analogicas(self.con, cliente, ultimo, ahora or self.AHORA)
        return cliente, ultimo

    def filas(self, tabla):
        return self.con.execute(f"SELECT * FROM {tabla}").fetchall()

    def test_lee_el_registro_y_guarda_el_valor_convertido_en_el_sensor(self):
        ia.guardar(config_basica(), SENSORES)
        self.correr({0: 12000})                      # 12 mA = mitad de 0..40 °C
        lecturas = self.con.execute("SELECT sensor, valor FROM lecturas_sensor").fetchall()
        self.assertEqual(lecturas, [("temp_leche", 20.0)])

    def test_guarda_siempre_el_crudo_para_poder_calibrar(self):
        ia.guardar({"ai_1": {"direccion": 0}}, SENSORES)   # sin sensor ni escala: solo se mira el número
        self.correr({0: 7777})
        self.assertEqual(self.con.execute("SELECT canal, raw FROM analogicas_crudo").fetchall(), [("ai_1", 7777)])
        self.assertEqual(self.filas("lecturas_sensor"), [])

    def test_sin_calibrar_no_guarda_lectura_del_sensor(self):
        ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 0}}, SENSORES)
        self.correr({0: 12000})
        self.assertEqual(self.filas("lecturas_sensor"), [])

    def test_lazo_cortado_no_guarda_lectura(self):
        ia.guardar(config_basica(), SENSORES)
        self.correr({0: 0})
        self.assertEqual(self.filas("lecturas_sensor"), [])
        self.assertEqual(self.con.execute("SELECT raw FROM analogicas_crudo").fetchall(), [(0,)])

    def test_no_guarda_en_cada_ciclo_de_3_segundos(self):
        ia.guardar(config_basica(), SENSORES)
        ultimo = {}
        self.correr({0: 12000}, ultimo)
        self.correr({0: 12000}, ultimo, self.AHORA + datetime.timedelta(seconds=3))
        self.assertEqual(len(self.filas("lecturas_sensor")), 1)
        self.correr({0: 12000}, ultimo,
                    self.AHORA + datetime.timedelta(seconds=iot_lavado.INTERVALO_GUARDADO_ANALOGICAS_S))
        self.assertEqual(len(self.filas("lecturas_sensor")), 2)

    def test_el_crudo_se_actualiza_en_cada_ciclo_aunque_no_se_guarde_la_lectura(self):
        ia.guardar(config_basica(), SENSORES)
        ultimo = {}
        self.correr({0: 12000}, ultimo)
        self.correr({0: 13000}, ultimo, self.AHORA + datetime.timedelta(seconds=3))
        self.assertEqual(self.con.execute("SELECT raw FROM analogicas_crudo").fetchall(), [(13000,)])

    def test_un_error_de_lectura_se_ignora_sin_romper_el_ciclo(self):
        ia.guardar(config_basica(), SENSORES)
        self.correr({})                              # el cliente contesta error de dirección
        self.assertEqual(self.filas("lecturas_sensor"), [])
        self.assertEqual(self.filas("analogicas_crudo"), [])

    def test_una_excepcion_del_cliente_no_corta_el_ciclo(self):
        ia.guardar(config_basica(), SENSORES)
        cliente = mock.Mock()
        cliente.read_input_registers.side_effect = OSError("conexión caída")
        iot_lavado.procesar_analogicas(self.con, cliente, {}, self.AHORA)   # no tiene que levantar
        cliente.close.assert_called_once()           # igual que las DI: fuerza la reconexión

    def test_cada_entrada_son_dos_registros_decodificados_como_decimal_de_32_bits(self):
        ia.guardar({"ai_1": {"direccion": 4}}, SENSORES)
        cliente, _ = self.correr({4: 6404.5})
        self.assertEqual(cliente.cantidades, [2])
        self.assertEqual(self.con.execute("SELECT raw FROM analogicas_crudo").fetchall(), [(6404.5,)])

    def test_el_orden_es_ab_cd_el_registro_alto_primero(self):
        # 0x4040 0x0000 es 3.0 en IEEE-754: con el orden de registros al revés daría un número ridículo.
        ia.guardar({"ai_1": {"direccion": 0}}, SENSORES)
        self.correr({0: [0x4040, 0x0000]})
        self.assertEqual(self.con.execute("SELECT raw FROM analogicas_crudo").fetchall(), [(3.0,)])

    def test_un_valor_que_no_es_numero_se_ignora(self):
        ia.guardar(config_basica(), SENSORES)
        self.correr({0: [0x7FC0, 0x0000]})           # NaN
        self.correr({0: [0x7F80, 0x0000]})           # infinito
        self.assertEqual(self.filas("analogicas_crudo"), [])
        self.assertEqual(self.filas("lecturas_sensor"), [])

    def test_una_respuesta_corta_se_ignora(self):
        ia.guardar(config_basica(), SENSORES)
        self.correr({0: [0x4040]})                   # llegó un solo registro
        self.assertEqual(self.filas("analogicas_crudo"), [])

    def test_sin_direccion_no_se_lee_nada(self):
        ia.guardar({"ai_1": {"sensor": "temp_leche", "raw_4": 4000, "raw_20": 20000}}, SENSORES)
        cliente, _ = self.correr({0: 12000})
        self.assertEqual(cliente.pedidos, [])

    def test_los_cambios_de_configuracion_se_toman_sin_reiniciar(self):
        ia.guardar(config_basica(), SENSORES)
        ultimo = {}
        self.correr({0: 12000, 5: 16000}, ultimo)
        ia.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 5, "raw_4": 4000, "raw_20": 20000, "min": 0,
                             "max": 40}}, SENSORES)
        self.correr({0: 12000, 5: 16000}, ultimo, self.AHORA + datetime.timedelta(minutes=5))
        valores = [v for (v,) in self.con.execute("SELECT valor FROM lecturas_sensor ORDER BY id")]
        self.assertEqual(valores, [20.0, 30.0])


class TestPanel(Base):
    def setUp(self):
        super().setUp()
        self.parchar_conexion_del_panel()

    def test_el_panel_trae_los_6_canales_y_los_sensores_asignables(self):
        d = iot_monitoreo.panel_analogicas()
        self.assertEqual([c["clave"] for c in d["canales"]], [f"ai_{i}" for i in range(1, 7)])
        claves = {s["clave"] for s in d["sensores"]}
        self.assertIn("temp_leche", claves)
        self.assertNotIn("ith", claves)

    def test_muestra_el_crudo_la_corriente_y_el_valor_en_vivo(self):
        ia.guardar(config_basica(), SENSORES)
        iot_lavado.procesar_analogicas(self.con, ClienteFalso({0: 12000}), {}, datetime.datetime(2026, 11, 22, 15))
        c = iot_monitoreo.panel_analogicas()["canales"][0]
        self.assertEqual((c["raw"], c["ma"], c["valor"]), (12000, 12.0, 20.0))
        self.assertFalse(c["sin_senal"])

    def test_un_canal_sin_lectura_sale_con_todo_vacio(self):
        c = iot_monitoreo.panel_analogicas()["canales"][1]
        self.assertEqual((c["raw"], c["ma"], c["valor"]), (None, None, None))

    def test_la_lectura_guardada_enciende_el_vumetro(self):
        ia.guardar(config_basica(), SENSORES)
        iot_lavado.procesar_analogicas(self.con, ClienteFalso({0: 12000}), {}, datetime.datetime(2026, 11, 22, 15))
        with mock.patch.object(iot_monitoreo, "RUTA_DB", self.ruta_db):
            lect = {s["clave"]: s for s in iot_monitoreo.lecturas_actuales()}
        self.assertEqual(lect["temp_leche"]["valor"], 20.0)


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.parchar_conexion_del_panel()
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_get_trae_los_canales(self):
        r = self.cli.get("/api/iot/analogicas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["canales"]), 6)

    def test_post_guarda_y_get_lo_devuelve(self):
        r = self.cli.post("/api/iot/analogicas", json={"canales": {
            "ai_1": {"sensor": "temp_leche", "direccion": 0, "raw_4": 4000, "raw_20": 20000}}})
        self.assertEqual(r.status_code, 200)
        c = self.cli.get("/api/iot/analogicas").get_json()["canales"][0]
        self.assertEqual((c["sensor"], c["direccion"], c["min"], c["max"]), ("temp_leche", 0, 0, 40))

    def test_post_con_datos_invalidos_da_400_y_no_guarda(self):
        r = self.cli.post("/api/iot/analogicas", json={"canales": {"ai_1": {"sensor": "no_existe"}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(ia.cargar(), {})

    def test_post_con_formato_invalido_da_400(self):
        self.assertEqual(self.cli.post("/api/iot/analogicas", json={"canales": [1, 2]}).status_code, 400)

    def test_sin_sesion_de_admin_no_entra(self):
        anonimo = app.app.test_client()
        self.assertNotEqual(anonimo.get("/api/iot/analogicas").status_code, 200)
        self.assertNotEqual(anonimo.post("/api/iot/analogicas", json={"canales": {}}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
