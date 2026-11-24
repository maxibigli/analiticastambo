"""El gateway (`iot_lavado.ConexionesModbus`) lee los equipos Modbus que el tambo agregó, cada uno con su cliente.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_modbus_poller -v
"""
import datetime
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

import iot_conexion
import iot_ingreso
import iot_lavado
import iot_origenes
import iot_sensores as sen
import iot_vumetros as iv

T0 = datetime.datetime(2026, 11, 25, 12, 0, 0)
CATALOGO = {"temp_corral": (-10, 45), "hum_corral": (0, 100)}
RELOJ = [0.0]                       # el reloj que mide cuánto tarda cada equipo (los timeouts lo adelantan)
TIEMPO_DE_UN_TIMEOUT_S = 2.0


class Respuesta:
    def __init__(self, datos=None, error=False):
        self.bits = list(datos or [])
        self.registers = list(datos or [])
        self._error = error

    def isError(self):
        return self._error


class Cliente:
    """Un equipo de mentira: `tabla` es (función, dirección, unit ID) -> lista de bits o registros, o una excepción."""

    def __init__(self, host, port, tabla=None, conecta=True):
        self.host, self.port, self.tabla, self.conecta = host, port, tabla if tabla is not None else {}, conecta
        self.connected = False
        self.llamadas = []
        self.intentos_conexion = 0
        self.cerrado = 0

    def connect(self):
        self.intentos_conexion += 1
        self.connected = self.conecta
        return self.connected

    def close(self):
        self.cerrado += 1
        self.connected = False

    def _leer(self, funcion, address, count, device_id):
        self.llamadas.append((funcion, address, count, device_id))
        if not self.connected:
            self.connect()                      # pymodbus reconecta solo en cada pedido
            if not self.connected:
                raise ConnectionError("no conectado")
        valor = self.tabla.get((funcion, address, device_id))
        if isinstance(valor, Exception):
            RELOJ[0] += TIEMPO_DE_UN_TIMEOUT_S  # un equipo que no contesta hace esperar el timeout del cliente
            raise valor
        return Respuesta(error=True) if valor is None else Respuesta(valor)

    def read_discrete_inputs(self, address, count=1, device_id=1):
        return self._leer("discrete_input", address, count, device_id)

    def read_coils(self, address, count=1, device_id=1):
        return self._leer("coil", address, count, device_id)

    def read_input_registers(self, address, count=1, device_id=1):
        return self._leer("input_register", address, count, device_id)

    def read_holding_registers(self, address, count=1, device_id=1):
        return self._leer("holding_register", address, count, device_id)


def float32(valor):
    return list(struct.unpack(">HH", struct.pack(">f", valor)))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = self.dir.name
        self.db = os.path.join(d, "iot.db")
        for p in (mock.patch.object(iot_origenes, "_RUTA", os.path.join(d, "iot_origenes.json")),
                  mock.patch.object(iot_conexion, "_RUTA", os.path.join(d, "iot_conexion.json")),
                  mock.patch.object(sen, "_RUTA", os.path.join(d, "iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", os.path.join(d, "iot_vumetros.json")),
                  mock.patch.object(iot_ingreso, "RUTA_DB", self.db),
                  mock.patch.object(iot_lavado, "AUDIO_ACTIVADO", False),
                  mock.patch.object(iot_lavado.time, "monotonic", lambda: RELOJ[0])):
            p.start()
            self.addCleanup(p.stop)
        RELOJ[0] = 0.0
        self.con = iot_lavado._conectar_db(self.db)
        self.addCleanup(self.con.close)
        self.clientes = {}          # (host, puerto) -> Cliente
        self.creados = []
        self.conexiones = iot_lavado.ConexionesModbus(self.fabrica)

    def fabrica(self, host, port):
        self.creados.append((host, port))
        return self.clientes.setdefault((host, port), Cliente(host, port))

    def equipo(self, host="10.0.0.10", canales=(), nombre=None, port=502, unit_id=1, ventana=15):
        nombre = nombre or f"equipo {host}"
        o = iot_origenes.crear_modbus(nombre, host, port, unit_id, ventana)
        iot_origenes.guardar_modbus(o["id"], nombre, host, port, unit_id, ventana, list(canales),
                                    sensores_analogicos=CATALOGO, sensores_ocupados=set(), entradas_en_uso=set())
        return iot_origenes.obtener(o["id"])

    def cliente(self, host="10.0.0.10", port=502, **kw):
        c = self.clientes.setdefault((host, port), Cliente(host, port))
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    def ciclo(self, ahora=T0):
        self.conexiones.ciclo(self.con, [o for o in iot_origenes.cargar() if o["tipo"] == "modbus_tcp"], ahora)

    def filas(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()


def dig(clave="puerta", **kw):
    return {"clave": clave, "grupo": "entrada_digital", "funcion": "discrete_input", "direccion": 0, **kw}


def ana(clave="temp", **kw):
    return {"clave": clave, "grupo": "entrada_analogica", "funcion": "input_register", "direccion": 10, "tipo_dato": "uint16", **kw}


class TestLectura(Base):
    def test_digitales_de_las_cuatro_funciones_con_su_direccion_y_unit_id(self):
        o = self.equipo(unit_id=3, canales=[dig("a", funcion="discrete_input", direccion=1), dig("b", funcion="coil", direccion=2),
                                            dig("c", funcion="input_register", direccion=3, unit_id=9),
                                            dig("d", funcion="holding_register", direccion=4)])
        c = self.cliente(tabla={("discrete_input", 1, 3): [1], ("coil", 2, 3): [0], ("input_register", 3, 9): [7],
                                ("holding_register", 4, 3): [0]})
        self.ciclo()
        self.assertEqual(sorted(c.llamadas), sorted([("discrete_input", 1, 1, 3), ("coil", 2, 1, 3),
                                                     ("input_register", 3, 1, 9), ("holding_register", 4, 1, 3)]))
        estados = dict(self.filas("SELECT canal, estado FROM digitales_estado"))
        self.assertEqual(estados, {f"{o['id']}.a": 1, f"{o['id']}.b": 0, f"{o['id']}.c": 1, f"{o['id']}.d": 0})
        self.assertEqual(len(self.filas("SELECT 1 FROM eventos_di")), 4)

    def test_el_ultimo_valor_de_un_digital_es_lo_que_dio_el_equipo_antes_del_contacto_nc(self):
        o = self.equipo(canales=[dig("a"), dig("b", direccion=1, invertido=True)])
        self.cliente(tabla={("discrete_input", 0, 1): [1], ("discrete_input", 1, 1): [1]})
        self.ciclo()
        self.assertEqual(iot_ingreso.ultimo_de_canal(o["id"], "a")["valor"], 1)
        self.assertEqual(iot_ingreso.ultimo_de_canal(o["id"], "b")["valor"], 1)       # el equipo dijo 1; el NC lo muestra inactivo
        self.assertEqual(dict(self.filas("SELECT canal, estado FROM digitales_estado"))[f"{o['id']}.b"], 0)

    def test_un_cambio_agrega_un_evento_y_lo_demas_es_latido(self):
        o = self.equipo(canales=[dig()])
        c = self.cliente(tabla={("discrete_input", 0, 1): [0]})
        self.ciclo(T0)
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual(len(self.filas("SELECT 1 FROM eventos_di WHERE canal = ?", f"{o['id']}.puerta")), 1)
        c.tabla[("discrete_input", 0, 1)] = [1]
        self.ciclo(T0 + datetime.timedelta(seconds=6))
        self.assertEqual([e for (e,) in self.filas("SELECT estado FROM eventos_di ORDER BY id")], [0, 1])

    def test_el_contacto_nc_del_canal_y_el_del_sensor_se_combinan(self):
        o = self.equipo(canales=[dig("nc", invertido=True), dig("nc2", direccion=1, invertido=True), dig("sensor_nc", direccion=2)])
        self.cliente(tabla={("discrete_input", 0, 1): [1], ("discrete_input", 1, 1): [1], ("discrete_input", 2, 1): [1]})
        with mock.patch.object(iot_lavado, "entradas_invertidas", lambda: {f"{o['id']}.nc2": True, f"{o['id']}.sensor_nc": True}):
            self.ciclo()
        estados = dict(self.filas("SELECT canal, estado FROM digitales_estado"))
        self.assertEqual(estados, {f"{o['id']}.nc": 0, f"{o['id']}.nc2": 1, f"{o['id']}.sensor_nc": 0})

    def test_analogicos_de_cada_tipo_con_factor_y_offset(self):
        o = self.equipo(canales=[ana("u16", direccion=1, tipo_dato="uint16"), ana("i16", direccion=2, tipo_dato="int16"),
                                 ana("f32", direccion=3, tipo_dato="float32_ab_cd", factor="0.5", offset=1, sensor="temp_corral"),
                                 ana("hold", direccion=5, funcion="holding_register", tipo_dato="uint16", unit_id=4)])
        c = self.cliente(tabla={("input_register", 1, 1): [65535], ("input_register", 2, 1): [0xFFFF],
                                ("input_register", 3, 1): float32(21.5), ("holding_register", 5, 4): [100]})
        self.ciclo()

        def ultimo(clave):
            return iot_ingreso.ultimo_de_canal(o["id"], clave)["valor"]
        self.assertEqual((ultimo("u16"), ultimo("i16"), ultimo("f32"), ultimo("hold")), (65535, -1, 11.75, 100))
        self.assertIn(("input_register", 3, 2, 1), c.llamadas)        # el decimal de 32 bits son dos registros
        self.assertEqual(self.filas("SELECT sensor, valor FROM lecturas_sensor"), [("temp_corral", 11.75)])

    def test_el_sensor_guarda_a_lo_sumo_una_lectura_cada_30_segundos(self):
        self.equipo(canales=[ana(sensor="temp_corral")])
        self.cliente(tabla={("input_register", 10, 1): [5]})
        for seg in (0, 3, 10, 29):
            self.ciclo(T0 + datetime.timedelta(seconds=seg))
        self.assertEqual(len(self.filas("SELECT 1 FROM lecturas_sensor")), 1)
        self.ciclo(T0 + datetime.timedelta(seconds=31))
        self.assertEqual(len(self.filas("SELECT 1 FROM lecturas_sensor")), 2)

    def test_un_canal_analogico_sin_sensor_solo_anota_el_ultimo_valor(self):
        o = self.equipo(canales=[ana()])
        self.cliente(tabla={("input_register", 10, 1): [5]})
        self.ciclo()
        self.assertEqual(self.filas("SELECT 1 FROM lecturas_sensor"), [])
        self.assertEqual(iot_ingreso.ultimo_de_canal(o["id"], "temp")["valor"], 5)

    def test_lecturas_basura_no_se_guardan(self):
        o = self.equipo(canales=[ana("nan", direccion=1, tipo_dato="float32_ab_cd", sensor="temp_corral"),
                                 ana("inf", direccion=3, tipo_dato="float32_ab_cd"), ana("corto", direccion=5, tipo_dato="float32_ab_cd"),
                                 ana("error", direccion=8), ana("grande", direccion=9, tipo_dato="uint16"),
                                 ana("ok", direccion=10)])
        self.cliente(tabla={("input_register", 1, 1): float32(float("nan")), ("input_register", 3, 1): float32(float("inf")),
                            ("input_register", 5, 1): [1], ("input_register", 9, 1): [70000], ("input_register", 10, 1): [8]})
        self.ciclo()
        guardados = {c: iot_ingreso.ultimo_de_canal(o["id"], c) for c in ("nan", "inf", "corto", "error", "grande", "ok")}
        self.assertEqual({c for c, v in guardados.items() if v}, {"ok"})
        self.assertEqual(self.filas("SELECT 1 FROM lecturas_sensor"), [])

    def test_una_direccion_rechazada_no_tumba_a_los_demas_canales_ni_se_toma_por_cero(self):
        o = self.equipo(canales=[dig("a", direccion=1), dig("rechazada", direccion=2), dig("c", direccion=3),
                                 ana("temp", direccion=1)])
        c = self.cliente(tabla={("discrete_input", 1, 1): [1], ("discrete_input", 3, 1): [1], ("input_register", 1, 1): [9]})
        self.ciclo()
        estados = dict(self.filas("SELECT canal, estado FROM digitales_estado"))
        self.assertEqual(estados, {f"{o['id']}.a": 1, f"{o['id']}.c": 1})
        self.assertEqual(iot_ingreso.ultimo_de_canal(o["id"], "temp")["valor"], 9)
        # el rechazo no le borra el estado anterior
        c.tabla[("discrete_input", 2, 1)] = [1]
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        del c.tabla[("discrete_input", 2, 1)]
        self.ciclo(T0 + datetime.timedelta(seconds=6))
        self.assertEqual(dict(self.filas("SELECT canal, estado FROM digitales_estado"))[f"{o['id']}.rechazada"], 1)

    def test_si_el_equipo_rechaza_todas_las_lecturas_el_error_manda_a_revisar_el_mapa_no_la_red(self):
        o = self.equipo(canales=[dig("a"), ana("t")])
        c = self.cliente()               # contesta error a todo: está vivo, pero el mapa no coincide
        self.ciclo()
        e = iot_ingreso.estado(o["id"])
        self.assertIn("rechaza todas las lecturas", e["ultimo_error"])
        self.assertIn("direcciones", e["ultimo_error"])
        self.assertIsNone(e["ultima_recepcion"])
        self.assertGreaterEqual(c.cerrado, 1)            # fuerza la reconexión


class TestCaidos(Base):
    def test_un_equipo_que_no_conecta_no_frena_al_siguiente_y_deja_su_error(self):
        malo = self.equipo("10.0.0.10", canales=[dig()], nombre="Caído")
        bueno = self.equipo("10.0.0.11", canales=[dig("x")], nombre="Sano")
        self.cliente("10.0.0.10", conecta=False)
        self.cliente("10.0.0.11", tabla={("discrete_input", 0, 1): [1]})
        self.ciclo()
        self.assertEqual(dict(self.filas("SELECT canal, estado FROM digitales_estado")), {f"{bueno['id']}.x": 1})
        e = iot_ingreso.estado(malo["id"])
        self.assertEqual(e["ultimo_error"], "No se pudo conectar a 10.0.0.10:502")
        self.assertEqual(e["error_fecha"], T0.isoformat(timespec="seconds"))
        self.assertIsNone(e["ultima_recepcion"])
        self.assertIsNotNone(iot_ingreso.estado(bueno["id"])["ultima_recepcion"])

    def test_un_equipo_caido_no_se_reintenta_en_cada_ciclo_y_se_recupera_solo(self):
        o = self.equipo(canales=[dig()])
        c = self.cliente(conecta=False)
        self.ciclo(T0)
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.ciclo(T0 + datetime.timedelta(seconds=29))
        self.assertEqual(c.intentos_conexion, 1)
        c.conecta, c.tabla = True, {("discrete_input", 0, 1): [1]}
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.REINTENTO_ORIGEN_CAIDO_S))
        self.assertEqual(c.intentos_conexion, 2)
        self.assertEqual(dict(self.filas("SELECT canal, estado FROM digitales_estado")), {f"{o['id']}.puerta": 1})
        e = iot_ingreso.estado(o["id"])
        self.assertIsNotNone(e["ultima_recepcion"])
        self.assertIn("No se pudo conectar", e["ultimo_error"])        # el error queda con su fecha
        # ya volvió al ritmo normal: el ciclo siguiente lee de nuevo
        c.llamadas.clear()
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.REINTENTO_ORIGEN_CAIDO_S + 3))
        self.assertEqual(len(c.llamadas), 1)

    def test_el_error_no_se_reescribe_en_cada_reintento_pero_si_se_fecha_de_nuevo_si_vuelve_a_caer(self):
        o = self.equipo(canales=[dig()])
        c = self.cliente(conecta=False)
        self.ciclo(T0)
        primera = iot_ingreso.estado(o["id"])["error_fecha"]
        self.ciclo(T0 + datetime.timedelta(seconds=40))               # sigue caído: mismo texto, la fecha no cambia
        self.assertEqual(iot_ingreso.estado(o["id"])["error_fecha"], primera)
        c.conecta, c.tabla = True, {("discrete_input", 0, 1): [1]}
        self.ciclo(T0 + datetime.timedelta(seconds=80))               # se recupera
        c.conecta, c.connected = False, False
        c.tabla = {}
        self.ciclo(T0 + datetime.timedelta(seconds=200))              # vuelve a caer: fecha nueva
        self.assertEqual(iot_ingreso.estado(o["id"])["error_fecha"], (T0 + datetime.timedelta(seconds=200)).isoformat(timespec="seconds"))

    def test_una_excepcion_al_conectar_tampoco_frena_a_los_demas(self):
        malo = self.equipo("10.0.0.10", canales=[dig()], nombre="Explota")
        self.equipo("10.0.0.11", canales=[dig("x")], nombre="Sano")
        self.cliente("10.0.0.10")
        self.clientes[("10.0.0.10", 502)].connect = mock.Mock(side_effect=OSError("red caída"))
        self.cliente("10.0.0.11", tabla={("discrete_input", 0, 1): [1]})
        self.ciclo()
        self.assertEqual(len(self.filas("SELECT 1 FROM digitales_estado")), 1)
        self.assertIn("red caída", iot_ingreso.estado(malo["id"])["ultimo_error"])

    def test_el_estado_de_un_equipo_sano_no_se_escribe_en_cada_ciclo(self):
        o = self.equipo(canales=[dig()])
        self.cliente(tabla={("discrete_input", 0, 1): [1]})
        self.ciclo(T0)
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual(iot_ingreso.estado(o["id"])["ultima_recepcion"], T0.isoformat(timespec="seconds"))
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.INTERVALO_LATIDO_DI_S))
        self.assertEqual(iot_ingreso.estado(o["id"])["ultima_recepcion"],
                         (T0 + datetime.timedelta(seconds=iot_lavado.INTERVALO_LATIDO_DI_S)).isoformat(timespec="seconds"))


class TestLentos(Base):
    """Un equipo que acepta la conexión y no contesta hace esperar el timeout en CADA pedido: el ciclo del principal (que corre antes
    y después) no puede pagarlo por cada canal."""

    def colgado(self, n=5, **kw):
        canales = [dig(f"c{i}", direccion=i) for i in range(n)]
        o = self.equipo(canales=canales, **kw)
        c = self.cliente(tabla={("discrete_input", i, 1): TimeoutError("sin respuesta") for i in range(n)})
        return o, c

    def test_un_equipo_que_no_contesta_no_se_lee_canal_por_canal(self):
        o, c = self.colgado()
        self.ciclo()
        self.assertEqual(len(c.llamadas), 2)                                   # dos timeouts agotan el tope del ciclo
        self.assertLessEqual(RELOJ[0], iot_lavado.PRESUPUESTO_ORIGEN_S)
        e = iot_ingreso.estado(o["id"])
        self.assertIn("no respondió", e["ultimo_error"])
        self.assertNotIn("rechaza", e["ultimo_error"])

    def test_el_equipo_que_no_contesta_se_reintenta_cada_30_segundos_y_cada_vez_con_otros_canales(self):
        o, c = self.colgado()
        self.ciclo(T0)
        antes = len(c.llamadas)
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual(len(c.llamadas), antes)                               # en espera: ni una llamada
        c.llamadas.clear()
        RELOJ[0] = 0.0
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.REINTENTO_ORIGEN_CAIDO_S))
        self.assertEqual([a[1] for a in c.llamadas], [2, 3])                   # los canales que ya fallaron esperan su turno
        self.assertLessEqual(RELOJ[0], iot_lavado.PRESUPUESTO_ORIGEN_S)

    def test_un_canal_muerto_entre_canales_sanos_no_frena_ni_se_reintenta_en_cada_ciclo(self):
        o = self.equipo(canales=[dig("a", direccion=0), dig("muerto", direccion=1), dig("b", direccion=2)])
        c = self.cliente(tabla={("discrete_input", 0, 1): [1], ("discrete_input", 1, 1): TimeoutError("x"),
                                ("discrete_input", 2, 1): [1]})
        self.ciclo(T0)
        self.assertEqual(sorted(dict(self.filas("SELECT canal, estado FROM digitales_estado"))), [f"{o['id']}.a", f"{o['id']}.b"])
        self.assertEqual(RELOJ[0], TIEMPO_DE_UN_TIMEOUT_S)
        e = iot_ingreso.estado(o["id"])
        self.assertIsNone(e["ultimo_error"])                                   # el equipo está sano
        c.llamadas.clear()
        RELOJ[0] = 0.0
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual([a[1] for a in c.llamadas], [0, 2])                   # el muerto espera
        self.assertEqual(RELOJ[0], 0.0)
        c.llamadas.clear()
        self.ciclo(T0 + datetime.timedelta(seconds=2 * iot_lavado.REINTENTO_ORIGEN_CAIDO_S + 3))
        self.assertEqual([a[1] for a in c.llamadas], [0, 1, 2])                # al pasar el tiempo se vuelve a probar

    def test_si_todos_los_canales_estan_en_espera_no_se_toca_el_estado(self):
        o, c = self.colgado(n=2)
        self.ciclo(T0)
        error = iot_ingreso.estado(o["id"])["error_fecha"]
        c.llamadas.clear()
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.REINTENTO_ORIGEN_CAIDO_S - 1))
        self.assertEqual(c.llamadas, [])
        self.assertEqual(iot_ingreso.estado(o["id"])["error_fecha"], error)

    def test_una_fabrica_que_explota_no_se_reintenta_en_cada_ciclo(self):
        o = self.equipo(canales=[dig()])
        llamadas = []

        def mala(host, port):
            llamadas.append(1)
            raise OSError("x" * 1000)
        self.conexiones = iot_lavado.ConexionesModbus(mala)
        self.ciclo(T0)
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.ciclo(T0 + datetime.timedelta(seconds=20))
        self.assertEqual(len(llamadas), 1)
        self.ciclo(T0 + datetime.timedelta(seconds=iot_lavado.REINTENTO_ORIGEN_CAIDO_S))
        self.assertEqual(len(llamadas), 2)
        self.assertLessEqual(len(iot_ingreso.estado(o["id"])["ultimo_error"]), 300)

    def test_cada_equipo_se_anota_con_su_propia_hora(self):
        # Sin `ahora` inyectado, cada equipo toma la hora al empezar: uno lento no le atrasa la marca a los que siguen.
        o1 = self.equipo("10.0.0.10", canales=[dig("a")], nombre="Uno")
        o2 = self.equipo("10.0.0.11", canales=[dig("b")], nombre="Dos")
        self.cliente("10.0.0.10", tabla={("discrete_input", 0, 1): [1]})
        self.cliente("10.0.0.11", tabla={("discrete_input", 0, 1): [1]})
        reloj = iter(T0 + datetime.timedelta(seconds=n) for n in range(0, 1000, 10))        # cada consulta de la hora avanza 10 s

        class Fecha(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return next(reloj)
        with mock.patch.object(iot_lavado.datetime, "datetime", Fecha):
            self.conexiones.ciclo(self.con, [o for o in iot_origenes.cargar() if o["tipo"] == "modbus_tcp"])
        hora = lambda o, c: self.filas("SELECT fecha_hora FROM digitales_estado WHERE canal = ?", f"{o['id']}.{c}")[0][0]  # noqa: E731
        self.assertLess(hora(o1, "a"), hora(o2, "b"))


class TestCambiosEnCaliente(Base):
    def test_una_lista_vacia_por_un_instante_no_hace_olvidar_a_los_equipos(self):
        # iot_origenes.cargar() devuelve [] un momento si Windows tiene el archivo tomado: no puede reiniciar el estado de los equipos
        # (se perderían los «anteriores» y se anotaría un cambio falso por canal).
        o = self.equipo(canales=[dig()])
        c = self.cliente(tabla={("discrete_input", 0, 1): [1]})
        self.ciclo(T0)
        self.conexiones.ciclo(self.con, [], T0 + datetime.timedelta(seconds=3))
        self.assertEqual(c.cerrado, 0)
        self.ciclo(T0 + datetime.timedelta(seconds=6))
        self.assertEqual(len(self.filas("SELECT 1 FROM eventos_di WHERE canal = ?", f"{o['id']}.puerta")), 1)

    def test_un_host_nuevo_borra_el_error_viejo(self):
        o = self.equipo("10.0.0.10", canales=[dig()])
        self.cliente("10.0.0.10", conecta=False)
        self.ciclo(T0)
        self.assertIn("10.0.0.10", iot_ingreso.estado(o["id"])["ultimo_error"])
        iot_origenes.guardar_modbus(o["id"], o["nombre"], "10.0.0.20", 502, 1, 15, [dig()], sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(), entradas_en_uso=set())
        self.cliente("10.0.0.20", tabla={("discrete_input", 0, 1): [1]})
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        e = iot_ingreso.estado(o["id"])
        self.assertIsNone(e["ultimo_error"])
        self.assertIsNotNone(e["ultima_recepcion"])

    def test_sin_equipos_no_se_abre_ningun_cliente(self):
        self.ciclo()
        self.assertEqual(self.creados, [])

    def test_un_equipo_sin_canales_no_se_conecta(self):
        self.equipo(canales=[])
        c = self.cliente()
        self.ciclo()
        self.assertEqual(c.intentos_conexion, 0)

    def test_un_equipo_que_desaparece_cierra_su_cliente(self):
        o = self.equipo(canales=[dig()])
        c = self.cliente(tabla={("discrete_input", 0, 1): [1]})
        self.ciclo()
        iot_origenes.eliminar(o["id"], set())
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.ciclo(T0 + datetime.timedelta(seconds=6))          # a la segunda ausencia seguida se cierra
        self.assertGreaterEqual(c.cerrado, 1)
        self.assertFalse(c.connected)

    def test_un_host_nuevo_cierra_el_cliente_viejo_y_abre_uno_al_nuevo(self):
        o = self.equipo("10.0.0.10", canales=[dig()])
        viejo = self.cliente("10.0.0.10", tabla={("discrete_input", 0, 1): [1]})
        self.ciclo()
        iot_origenes.guardar_modbus(o["id"], o["nombre"], "10.0.0.20", 502, 1, 15, [dig()], sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(), entradas_en_uso=set())
        nuevo = self.cliente("10.0.0.20", tabla={("discrete_input", 0, 1): [0]})
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertFalse(viejo.connected)
        self.assertEqual(nuevo.llamadas, [("discrete_input", 0, 1, 1)])

    def test_un_puerto_nuevo_tambien_reconecta(self):
        o = self.equipo("10.0.0.10", canales=[dig()])
        self.cliente("10.0.0.10", tabla={("discrete_input", 0, 1): [1]})
        self.ciclo()
        iot_origenes.guardar_modbus(o["id"], o["nombre"], "10.0.0.10", 1502, 1, 15, [dig()], sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(), entradas_en_uso=set())
        self.cliente("10.0.0.10", 1502, tabla={("discrete_input", 0, 1): [1]})
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertIn(("10.0.0.10", 1502), self.creados)

    def test_un_canal_que_se_saca_deja_de_leerse_en_el_ciclo_siguiente(self):
        o = self.equipo(canales=[dig("a"), dig("b", direccion=1)])
        c = self.cliente(tabla={("discrete_input", 0, 1): [1], ("discrete_input", 1, 1): [1]})
        self.ciclo()
        iot_origenes.guardar_modbus(o["id"], o["nombre"], "10.0.0.10", 502, 1, 15, [dig("a")], sensores_analogicos=CATALOGO,
                                    sensores_ocupados=set(), entradas_en_uso=set())
        c.llamadas.clear()
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual(c.llamadas, [("discrete_input", 0, 1, 1)])

    def test_un_equipo_nuevo_se_toma_solo(self):
        self.ciclo()
        self.equipo(canales=[dig()])
        c = self.cliente(tabla={("discrete_input", 0, 1): [1]})
        self.ciclo(T0 + datetime.timedelta(seconds=3))
        self.assertEqual(len(c.llamadas), 1)


class TestElPrincipalNoCambia(Base):
    def test_las_llamadas_del_principal_son_las_mismas_con_y_sin_equipos_caidos(self):
        from tests.test_iot_lavado_mapa import ClienteFalso
        sin, con_equipos = ClienteFalso(), ClienteFalso()
        anteriores, latido = {}, {}
        iot_lavado.procesar_entradas(self.con, sin, anteriores, latido)
        self.equipo("10.0.0.10", canales=[dig()], nombre="Caído")
        self.cliente("10.0.0.10", conecta=False)
        self.ciclo()
        iot_lavado.procesar_entradas(self.con, con_equipos, {}, {})
        self.assertEqual(sin.llamadas, con_equipos.llamadas)


if __name__ == "__main__":
    unittest.main()
