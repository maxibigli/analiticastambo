"""El gateway (`iot_lavado`) usa el mapa Modbus de `iot_mapa`, y sin mapa llama igual que antes.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_lavado_mapa -v
"""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import iot_canales
import iot_lavado
import iot_mapa
import iot_sensores as sen
import iot_vumetros as iv


class Respuesta:
    def __init__(self, bits=None, registers=None, error=False):
        self.bits = bits if bits is not None else []
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class ClienteFalso:
    """Registra cada llamada como (método, address, count_o_value, device_id)."""

    def __init__(self):
        self.llamadas = []
        self.connected = True
        self.bit = True                      # lo que contestan read_discrete_inputs / read_coils
        self.registro = [5]                  # lo que contestan los registros
        self.error_lectura = False
        self.cerrado = 0

    def _lectura(self, nombre, address, count, device_id):
        self.llamadas.append((nombre, address, count, device_id))
        return Respuesta(bits=[self.bit], registers=list(self.registro), error=self.error_lectura)

    def read_discrete_inputs(self, address, count=1, device_id=1):
        return self._lectura("read_discrete_inputs", address, count, device_id)

    def read_coils(self, address, count=1, device_id=1):
        return self._lectura("read_coils", address, count, device_id)

    def read_input_registers(self, address, count=1, device_id=1):
        return self._lectura("read_input_registers", address, count, device_id)

    def read_holding_registers(self, address, count=1, device_id=1):
        return self._lectura("read_holding_registers", address, count, device_id)

    def write_coil(self, address, value, device_id=1):
        self.llamadas.append(("write_coil", address, value, device_id))
        return Respuesta()

    def connect(self):
        self.connected = True

    def close(self):
        self.cerrado += 1


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = self.dir.name
        for p in (mock.patch.object(iot_mapa, "_RUTA", os.path.join(d, "iot_mapa.json")),
                  mock.patch.object(iot_canales, "_RUTA", os.path.join(d, "nombres.json")),
                  mock.patch.object(sen, "_RUTA", os.path.join(d, "iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", os.path.join(d, "iot_vumetros.json")),
                  mock.patch.object(iot_lavado, "AUDIO_ACTIVADO", False),
                  mock.patch.dict(iot_lavado._encendidas, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(os.path.join(d, "iot.db"))
        self.addCleanup(self.con.close)
        self.cliente = ClienteFalso()
        self.anteriores = {c: None for c in iot_lavado.CANALES}
        self.latidos = {}

    def nombrar(self, **nombres):
        iot_canales.guardar(nombres)

    def procesar(self):
        iot_lavado.procesar_entradas(self.con, self.cliente, self.anteriores, self.latidos)

    def estado_guardado(self, canal):
        fila = self.con.execute("SELECT estado FROM digitales_estado WHERE canal=?", (canal,)).fetchone()
        return fila[0] if fila else None

    def encolar(self, canal):
        self.con.execute("INSERT INTO comandos_actuador (canal, fecha_hora, ejecutado) VALUES (?, ?, 0)",
                         (canal, datetime.datetime.now().isoformat(timespec="seconds")))
        self.con.commit()

    def resultado_ultimo_comando(self):
        return self.con.execute("SELECT resultado FROM comandos_actuador ORDER BY id DESC LIMIT 1").fetchone()[0]


class TestSinMapa(Base):
    """Sin iot_mapa.json las llamadas Modbus son exactamente las de antes (invariante de La Ponderosa)."""

    def test_sin_mapa_las_entradas_se_leen_como_siempre(self):
        self.nombrar(di_3="Térmico bomba")
        self.procesar()
        self.assertEqual(self.cliente.llamadas, [("read_discrete_inputs", 0, 1, 1), ("read_discrete_inputs", 1, 1, 1),
                                                 ("read_discrete_inputs", 2, 1, 1)])

    def test_sin_mapa_un_pulso_es_el_de_siempre(self):
        self.encolar("do_3")
        with mock.patch.object(iot_lavado.time, "sleep") as dormir:
            iot_lavado.ejecutar_comandos_pendientes(self.con, self.cliente)
        self.assertEqual(self.cliente.llamadas, [("write_coil", 2, True, 1), ("write_coil", 2, False, 1)])
        dormir.assert_called_once_with(0.5)
        self.assertEqual(self.resultado_ultimo_comando(), "ok")

    def test_sin_mapa_prender_y_apagar_un_rele_es_como_siempre(self):
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_1"], True), set())
        self.assertEqual(self.cliente.llamadas, [("write_coil", 0, True, 1)])


class TestConMapa(Base):
    def test_una_entrada_con_otro_unit_id_y_otra_funcion(self):
        self.nombrar(di_3="Térmico bomba")
        iot_mapa.guardar({"di_3": {"funcion": "input_register", "direccion": 7, "unit_id": 4}}, {})
        self.cliente.registro = [5]
        self.procesar()
        self.assertIn(("read_input_registers", 7, 1, 4), self.cliente.llamadas)
        self.assertEqual(self.estado_guardado("di_3"), 1)
        self.cliente.registro = [0]
        self.procesar()
        self.assertEqual(self.estado_guardado("di_3"), 0)

    def test_una_entrada_invertida_en_el_mapa_y_en_el_sensor_se_anulan(self):
        sen.guardar(sen.cargar() + [{"label": "Térmico", "tipo": "digital", "rol": "alarma", "entrada": "di_3",
                                     "invertido": True}])
        iot_mapa.guardar({"di_3": {"invertido": True}}, {})
        self.cliente.bit = True
        self.procesar()
        self.assertEqual(self.estado_guardado("di_3"), 1)   # los dos NC se anulan: el bit queda como se leyó

    def test_un_registro_con_error_no_cambia_el_estado(self):
        # Foco 3: un error o un registro cortado NO es «apagado»: el canal conserva su estado anterior.
        self.nombrar(di_3="Térmico bomba")
        iot_mapa.guardar({"di_3": {"funcion": "input_register", "direccion": 7}}, {})
        self.cliente.registro = [5]
        self.procesar()
        self.assertEqual(self.estado_guardado("di_3"), 1)
        eventos = self.con.execute("SELECT COUNT(*) FROM eventos_di WHERE canal='di_3'").fetchone()[0]
        self.cliente.error_lectura = True
        self.procesar()
        self.cliente.error_lectura = False
        self.cliente.registro = []
        self.procesar()
        self.assertEqual(self.estado_guardado("di_3"), 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM eventos_di WHERE canal='di_3'").fetchone()[0], eventos)

    def test_un_pulso_con_mapa_usa_su_unit_id_su_bobina_y_su_duracion(self):
        iot_mapa.guardar({}, {"do_3": {"direccion": 40, "unit_id": 2, "pulso_s": 1.5}})
        self.encolar("do_3")
        with mock.patch.object(iot_lavado.time, "sleep") as dormir:
            iot_lavado.ejecutar_comandos_pendientes(self.con, self.cliente)
        self.assertEqual(self.cliente.llamadas, [("write_coil", 40, True, 2), ("write_coil", 40, False, 2)])
        dormir.assert_called_once_with(1.5)
        self.assertEqual(self.resultado_ultimo_comando(), "ok")

    def test_prender_un_rele_con_mapa_usa_su_unit_id_y_su_bobina(self):
        iot_mapa.guardar({}, {"do_3": {"direccion": 40, "unit_id": 2}})
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_3"], False), set())
        self.assertEqual(self.cliente.llamadas, [("write_coil", 40, False, 2)])

    def test_si_cambia_el_mapa_con_la_salida_prendida_se_apaga_la_bobina_que_se_prendio(self):
        # Revisión final: un relé sostenido (lavado, voz) se prende y se apaga en ciclos distintos. Si en el medio se movió la
        # bobina en el mapa, el apagado tiene que llegar también a la que se prendió, o queda energizada.
        iot_mapa.guardar({}, {"do_3": {"direccion": 40, "unit_id": 2}})
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_3"], True), set())
        iot_mapa.guardar({}, {"do_3": {"direccion": 41, "unit_id": 2}})
        self.cliente.llamadas.clear()
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_3"], False), set())
        self.assertCountEqual(self.cliente.llamadas, [("write_coil", 41, False, 2), ("write_coil", 40, False, 2)])
        # ya apagada: el próximo apagado va solo a la bobina actual
        self.cliente.llamadas.clear()
        iot_lavado._escribir_reles(self.cliente, ["do_3"], False)
        self.assertEqual(self.cliente.llamadas, [("write_coil", 41, False, 2)])

    def test_prender_y_apagar_sin_cambiar_el_mapa_es_una_sola_escritura_cada_vez(self):
        iot_lavado._escribir_reles(self.cliente, ["do_3"], True)
        iot_lavado._escribir_reles(self.cliente, ["do_3"], False)
        self.assertEqual(self.cliente.llamadas, [("write_coil", 2, True, 1), ("write_coil", 2, False, 1)])

    def test_si_el_apagado_de_la_bobina_vieja_falla_se_informa_y_se_sigue_recordando(self):
        iot_mapa.guardar({}, {"do_3": {"direccion": 40}})
        iot_lavado._escribir_reles(self.cliente, ["do_3"], True)
        iot_mapa.guardar({}, {"do_3": {"direccion": 41}})
        escribir = self.cliente.write_coil
        self.cliente.write_coil = lambda address, value, device_id=1: (
            Respuesta(error=True) if address == 40 else escribir(address, value, device_id))
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_3"], False), {"do_3"})
        self.cliente.write_coil = escribir
        self.cliente.llamadas.clear()
        self.assertEqual(iot_lavado._escribir_reles(self.cliente, ["do_3"], False), set())   # reintento: ahora sí
        self.assertIn(("write_coil", 40, False, 1), self.cliente.llamadas)

    def test_un_canal_desconocido_sigue_dando_el_mismo_error(self):
        self.encolar("do_99")
        iot_lavado.ejecutar_comandos_pendientes(self.con, self.cliente)
        self.assertEqual(self.resultado_ultimo_comando(), "error: canal desconocido (do_99)")
        self.assertEqual(self.cliente.llamadas, [])

    def test_el_mapa_se_toma_sin_reiniciar(self):
        self.nombrar(di_3="Térmico bomba")
        self.procesar()
        iot_mapa.guardar({"di_3": {"direccion": 50}}, {})
        self.cliente.llamadas.clear()
        self.procesar()
        self.assertIn(("read_discrete_inputs", 50, 1, 1), self.cliente.llamadas)
        self.assertNotIn(("read_discrete_inputs", 2, 1, 1), self.cliente.llamadas)

    def test_las_bobinas_como_entrada_se_leen_con_read_coils(self):
        self.nombrar(di_3="Térmico bomba")
        iot_mapa.guardar({"di_3": {"funcion": "coil", "direccion": 12}}, {})
        self.procesar()
        self.assertIn(("read_coils", 12, 1, 1), self.cliente.llamadas)


if __name__ == "__main__":
    unittest.main()
