"""El ciclo de vida de una orden para la salida de un equipo que manda por HTTP (ESP32): crear, entregar UNA vez, confirmar, vencer.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_ordenes -v
"""
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

import iot_lavado
import iot_ordenes
import iot_origenes

T0 = datetime(2026, 11, 25, 12, 0, 0)
VENCE = 30


def seg(n):
    return T0 + timedelta(seconds=n)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "iot.db")
        for p in (mock.patch.object(iot_origenes, "_RUTA", os.path.join(self.dir.name, "iot_origenes.json")),
                  mock.patch.object(iot_ordenes, "RUTA_DB", self.db)):
            p.start()
            self.addCleanup(p.stop)
        self.id = self.nuevo_origen("ESP32 del corral")
        self.origen = iot_origenes.obtener(self.id)

    def nuevo_origen(self, nombre, canales=None, puede=True):
        o, _ = iot_origenes.crear(nombre, "generico", 120)
        canales = canales if canales is not None else [
            {"clave": "bomba", "grupo": "salida", "modo": "pulso", "pulso_s": 1.5},
            {"clave": "otra", "grupo": "salida", "modo": "pulso", "pulso_s": 2},
            {"clave": "calefaccion", "grupo": "salida", "modo": "sostenida"},
            {"clave": "puerta", "grupo": "entrada_digital"}]
        iot_origenes.guardar_origen(o["id"], nombre, "generico", 120, canales, sensores_analogicos={}, sensores_ocupados=set(),
                                    entradas_en_uso=set(), puede_comandar=puede, vencimiento_orden_s=VENCE if puede else None)
        return o["id"]

    def crear(self, canal="bomba", accion="pulso", ahora=T0, origen=None):
        return iot_ordenes.crear(origen or self.origen, canal, accion, "admin", ahora=ahora)

    def filas(self, sql, *args):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def estado(self, orden_id):
        return self.filas("SELECT estado FROM ordenes_origen WHERE id = ?", orden_id)[0][0]


class TestCrear(Base):
    def test_una_orden_de_pulso_toma_la_duracion_del_canal_y_vence(self):
        o = self.crear()
        self.assertEqual((o["canal"], o["accion"], o["duracion_s"], o["estado"]), ("bomba", "pulso", 1.5, "pendiente"))
        self.assertEqual(o["creada"], T0.isoformat(timespec="seconds"))
        self.assertEqual(o["vence"], seg(VENCE).isoformat(timespec="seconds"))

    def test_una_orden_sostenida_no_lleva_duracion(self):
        o = self.crear("calefaccion", "prender")
        self.assertIsNone(o["duracion_s"])

    def test_las_negativas(self):
        sin_orden = self.nuevo_origen("Sin ordenes", puede=False, canales=[])
        casos = [
            (dict(origen=iot_origenes.obtener(sin_orden)), "no puede recibir órdenes"),
            (dict(canal="no_existe"), "ninguna salida"),
            (dict(canal="puerta"), "no es una salida"),
            (dict(accion="prender"), "pulso"),                      # una salida de pulso no se prende
            (dict(canal="calefaccion", accion="pulso"), "prender"),     # una sostenida no hace pulsos
            (dict(accion="rafaga"), "acción"),
            (dict(accion=None), "acción"),
        ]
        for kw, texto in casos:
            with self.subTest(**{k: str(v)[:20] for k, v in kw.items()}):
                with self.assertRaises(iot_ordenes.OrdenError) as cm:
                    self.crear(**kw)
                self.assertIn(texto, str(cm.exception))
        self.assertEqual(iot_ordenes.listar(self.id), [])                     # ninguna negativa dejó una fila

    def test_una_orden_a_un_origen_modbus_o_chirpstack_se_rechaza(self):
        cs, _ = iot_origenes.crear("Nodo", "chirpstack", 60)
        with self.assertRaises(iot_ordenes.OrdenError):
            self.crear(origen=iot_origenes.obtener(cs["id"]))

    def test_no_se_apilan_ordenes_del_mismo_canal(self):
        primera = self.crear()
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            self.crear()
        self.assertEqual(cm.exception.codigo, 409)
        self.assertIn(str(primera["id"]), str(cm.exception))
        iot_ordenes.entregar(self.id, ahora=seg(1))                         # entregada y sin confirmar sigue viva
        with self.assertRaises(iot_ordenes.OrdenError):
            self.crear(ahora=seg(2))
        iot_ordenes.confirmar(self.id, primera["id"], ahora=seg(3))
        self.assertEqual(self.crear(ahora=seg(4))["estado"], "pendiente")   # ya terminó: se puede de nuevo

    def test_otro_canal_u_otro_origen_no_se_bloquean(self):
        self.crear("bomba")
        self.crear("otra")
        otro = iot_origenes.obtener(self.nuevo_origen("Otro ESP32"))
        self.crear("bomba", origen=otro)

    def test_una_pendiente_vencida_no_bloquea_la_siguiente(self):
        self.crear()
        self.assertEqual(self.crear(ahora=seg(VENCE + 1))["estado"], "pendiente")


class TestEntregar(Base):
    def test_se_entrega_con_la_forma_exacta_y_una_sola_vez(self):
        o = self.crear()
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1)),
                         [{"id": o["id"], "canal": "bomba", "accion": "pulso", "duracion_s": 1.5}])
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(2)), [])
        self.assertEqual(self.estado(o["id"]), "entregada")

    def test_una_orden_sostenida_se_entrega_sin_duracion(self):
        self.crear("calefaccion", "prender")
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1))[0],
                         {"id": 1, "canal": "calefaccion", "accion": "prender", "duracion_s": None})

    def test_las_mas_viejas_primero_y_de_a_diez(self):
        self.nuevo_origen("Muchos", canales=[{"clave": f"s{i}", "grupo": "salida", "modo": "pulso", "pulso_s": 1} for i in range(12)])
        o = iot_origenes.obtener("muchos")
        for i in range(12):
            self.crear(f"s{i}", origen=o, ahora=seg(i))
        primera = iot_ordenes.entregar("muchos", ahora=seg(20))
        self.assertEqual([x["canal"] for x in primera], [f"s{i}" for i in range(10)])
        self.assertEqual([x["canal"] for x in iot_ordenes.entregar("muchos", ahora=seg(21))], ["s10", "s11"])

    def test_una_orden_vencida_no_se_entrega_nunca_ni_con_un_reloj_que_vuelve_atras(self):
        o = self.crear()
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(VENCE)), [])          # justo al vencer ya no sale
        self.assertEqual(self.estado(o["id"]), "vencida")
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(5)), [])               # el reloj «vuelve»: sigue vencida
        self.assertEqual(self.estado(o["id"]), "vencida")

    def test_cada_origen_recibe_solo_las_suyas(self):
        otro_id = self.nuevo_origen("Otro ESP32")
        self.crear("bomba")
        self.crear("bomba", origen=iot_origenes.obtener(otro_id))
        self.assertEqual(len(iot_ordenes.entregar(otro_id, ahora=seg(1))), 1)
        self.assertEqual(len(iot_ordenes.entregar(self.id, ahora=seg(1))), 1)

    def test_un_origen_que_ya_no_puede_comandar_no_recibe_nada(self):
        self.crear()
        iot_origenes.guardar_origen(self.id, "x", "generico", 120, [], sensores_analogicos={}, sensores_ocupados=set(),
                                    entradas_en_uso=set())
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            iot_ordenes.entregar(self.id, ahora=seg(1))
        self.assertEqual(cm.exception.codigo, 409)

    def test_dos_consultas_a_la_vez_no_entregan_la_misma_orden_dos_veces(self):
        canales = [{"clave": f"s{i}", "grupo": "salida", "modo": "pulso", "pulso_s": 1} for i in range(5)]
        self.nuevo_origen("Paralelo", canales=canales)
        o = iot_origenes.obtener("paralelo")
        for i in range(5):
            self.crear(f"s{i}", origen=o)
        recibidas, errores = [], []

        def consultar():
            try:
                recibidas.extend(x["id"] for x in iot_ordenes.entregar("paralelo", ahora=seg(1)))
            except Exception as e:  # noqa: BLE001
                errores.append(e)
        hilos = [threading.Thread(target=consultar) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(sorted(recibidas), sorted(set(recibidas)))
        self.assertEqual(len(recibidas), 5)


class TestConfirmar(Base):
    def entregada(self, canal="bomba", accion="pulso", ahora=T0):
        o = self.crear(canal, accion, ahora=ahora)
        iot_ordenes.entregar(self.id, ahora=ahora + timedelta(seconds=1))
        return o["id"]

    def test_confirmada_sin_error_es_ejecutada(self):
        i = self.entregada()
        r = iot_ordenes.confirmar(self.id, i, ahora=seg(2))
        self.assertEqual(r["estado"], "ejecutada")
        self.assertEqual(self.filas("SELECT resuelta FROM ordenes_origen WHERE id = ?", i), [(seg(2).isoformat(timespec="seconds"),)])

    def test_confirmada_con_error_guarda_el_texto_cortado(self):
        i = self.entregada()
        r = iot_ordenes.confirmar(self.id, i, error="x" * 1000, ahora=seg(2))
        self.assertEqual(r["estado"], "error")
        self.assertEqual(len(self.filas("SELECT resultado FROM ordenes_origen WHERE id = ?", i)[0][0]), iot_ordenes.MAX_RESULTADO)

    def test_un_error_que_no_es_texto_no_rompe(self):
        for k, malo in enumerate((5, ["a"], {"x": 1}, 0.5)):
            with self.subTest(error=malo):
                i = self.entregada(ahora=seg(100 * (k + 1)))
                self.assertEqual(iot_ordenes.confirmar(self.id, i, error=malo, ahora=seg(100 * (k + 1) + 2))["estado"], "error")

    def test_un_error_vacio_o_falso_es_una_confirmacion_normal(self):
        for k, vacio in enumerate((None, "", "   ", False)):
            i = self.entregada(ahora=seg(100 * (k + 1)))
            self.assertEqual(iot_ordenes.confirmar(self.id, i, error=vacio, ahora=seg(100 * (k + 1) + 2))["estado"], "ejecutada")

    def test_la_orden_inexistente_y_la_de_otro_origen_contestan_igual(self):
        otro_id = self.nuevo_origen("Otro ESP32")
        i = self.entregada()
        with self.assertRaises(iot_ordenes.OrdenError) as a:
            iot_ordenes.confirmar(otro_id, i, ahora=seg(2))                 # existe, pero es de otro
        with self.assertRaises(iot_ordenes.OrdenError) as b:
            iot_ordenes.confirmar(self.id, 9999, ahora=seg(2))              # no existe
        self.assertEqual((a.exception.codigo, str(a.exception)), (b.exception.codigo, str(b.exception)))
        self.assertEqual(a.exception.codigo, 404)
        self.assertEqual(self.estado(i), "entregada")                       # la ajena no se tocó

    def test_una_pendiente_todavia_no_se_puede_confirmar(self):
        o = self.crear()
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            iot_ordenes.confirmar(self.id, o["id"], ahora=seg(1))
        self.assertEqual(cm.exception.codigo, 409)
        self.assertEqual(self.estado(o["id"]), "pendiente")

    def test_una_vencida_no_se_puede_confirmar(self):
        o = self.crear()
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            iot_ordenes.confirmar(self.id, o["id"], ahora=seg(VENCE + 5))
        self.assertEqual(cm.exception.codigo, 409)
        self.assertEqual(self.estado(o["id"]), "vencida")

    def test_repetir_la_misma_confirmacion_es_idempotente_y_la_contraria_no(self):
        i = self.entregada()
        iot_ordenes.confirmar(self.id, i, ahora=seg(2))
        self.assertEqual(iot_ordenes.confirmar(self.id, i, ahora=seg(3))["estado"], "ejecutada")
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            iot_ordenes.confirmar(self.id, i, error="se rompió", ahora=seg(4))
        self.assertEqual(cm.exception.codigo, 409)
        self.assertEqual(self.estado(i), "ejecutada")

    def test_el_id_tiene_que_ser_un_entero(self):
        for malo in ("1", None, 1.5, True, [1]):
            with self.subTest(id=malo):
                with self.assertRaises(iot_ordenes.OrdenError):
                    iot_ordenes.confirmar(self.id, malo, ahora=seg(2))


class TestVencimientos(Base):
    def test_nada_queda_entregada_para_siempre(self):
        o = self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        self.assertEqual(iot_ordenes.listar(self.id, ahora=seg(1 + VENCE - 1))[0]["estado"], "entregada")      # todavía hay plazo
        self.assertEqual(iot_ordenes.listar(self.id, ahora=seg(1 + VENCE))[0]["estado"], "sin_confirmar")
        self.assertEqual(self.estado(o["id"]), "sin_confirmar")

    def test_la_orden_entregada_y_sin_confirmar_no_se_reenvia(self):
        self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1 + VENCE + 5)), [])

    def test_una_confirmacion_tardia_dice_lo_que_paso(self):
        o = self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        self.assertEqual(iot_ordenes.confirmar(self.id, o["id"], ahora=seg(1 + VENCE + 60))["estado"], "ejecutada")
        o2 = self.crear(ahora=seg(500))
        iot_ordenes.entregar(self.id, ahora=seg(501))
        self.assertEqual(iot_ordenes.confirmar(self.id, o2["id"], error="atascado", ahora=seg(501 + VENCE + 60))["estado"], "error")

    def test_el_esp32_que_vuelve_a_los_20_minutos(self):
        a = self.crear("bomba", ahora=T0)                                       # nadie la levantó
        b = self.crear("otra", ahora=T0)
        iot_ordenes.entregar(self.id, ahora=seg(1))                             # las dos se entregan...
        c = self.crear("calefaccion", "prender", ahora=seg(2))                  # ...y esta no
        estados = {x["id"]: x["estado"] for x in iot_ordenes.listar(self.id, ahora=seg(20 * 60))}
        self.assertEqual(estados, {a["id"]: "sin_confirmar", b["id"]: "sin_confirmar", c["id"]: "vencida"})
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(20 * 60)), [])

    def test_resolver_vencidas_cuenta_lo_que_cambio(self):
        self.crear("bomba")
        self.crear("otra")
        self.assertEqual(iot_ordenes.resolver_vencidas(self.id, ahora=seg(1)), 0)
        self.assertEqual(iot_ordenes.resolver_vencidas(self.id, ahora=seg(VENCE + 1)), 2)
        self.assertEqual(iot_ordenes.resolver_vencidas(ahora=seg(VENCE + 2)), 0)


class TestCancelarYListar(Base):
    def test_cancelar_por_canal_y_por_origen(self):
        a, b, c = self.crear("bomba"), self.crear("otra"), self.crear("calefaccion", "prender")
        self.assertEqual(iot_ordenes.cancelar(self.id, "bomba", "se sacó la salida", ahora=seg(1)), 1)
        res = {x["id"]: (x["estado"], x["resultado"]) for x in iot_ordenes.listar(self.id, ahora=seg(1))}
        self.assertEqual(res[a["id"]], ("vencida", "se sacó la salida"))
        self.assertEqual((res[b["id"]][0], res[c["id"]][0]), ("pendiente", "pendiente"))
        self.assertEqual(iot_ordenes.cancelar(self.id, None, "se desactivó el comando", ahora=seg(2)), 2)
        self.assertEqual(iot_ordenes.cancelar(self.id, None, "otra vez", ahora=seg(3)), 0)
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(4)), [])        # lo cancelado no se entrega

    def test_cancelar_marca_lo_pendiente_vencida_y_lo_entregado_sin_confirmar(self):
        a = self.crear("bomba")
        b = self.crear("otra")
        iot_ordenes.entregar(self.id, ahora=seg(1))                                   # a y b entregadas
        c = self.crear("calefaccion", "prender", ahora=seg(2))                        # pendiente
        self.assertEqual(iot_ordenes.cancelar(self.id, None, "se eliminó el equipo", ahora=seg(3)), 3)
        self.assertEqual([self.estado(a["id"]), self.estado(b["id"]), self.estado(c["id"])], ["sin_confirmar", "sin_confirmar", "vencida"])

    def test_listar_trae_las_nuevas_primero_con_su_limite(self):
        for i in range(5):
            o = self.crear(ahora=seg(100 * i))
            iot_ordenes.entregar(self.id, ahora=seg(100 * i + 1))
            iot_ordenes.confirmar(self.id, o["id"], ahora=seg(100 * i + 2))
        lista = iot_ordenes.listar(self.id, limite=3, ahora=seg(1000))
        self.assertEqual([x["id"] for x in lista], [5, 4, 3])
        self.assertEqual(set(lista[0]), {"id", "canal", "accion", "duracion_s", "estado", "creada", "vence", "entregada", "resuelta",
                                         "resultado", "usuario"})
        self.assertEqual(lista[0]["usuario"], "admin")

    def test_ultima_por_canal(self):
        a = self.crear("bomba", ahora=T0)
        iot_ordenes.entregar(self.id, ahora=seg(1))
        iot_ordenes.confirmar(self.id, a["id"], ahora=seg(2))
        b = self.crear("bomba", ahora=seg(50))
        self.crear("calefaccion", "prender", ahora=seg(50))
        u = iot_ordenes.ultima_por_canal(self.id, ahora=seg(51))
        self.assertEqual(set(u), {"bomba", "calefaccion"})
        self.assertEqual((u["bomba"]["id"], u["bomba"]["accion"], u["bomba"]["estado"]), (b["id"], "pulso", "pendiente"))
        self.assertEqual(u["calefaccion"]["accion"], "prender")


class TestConfiguracionCambiada(Base):
    """Una orden vale solo para la salida TAL COMO ESTÁ ahora: la app cancela al guardar, pero entre el guardado y la cancelación (o si
    esta falla) una consulta no puede llevarse una orden vieja. `entregar` y `crear` comparan con lo guardado, dentro del lock."""

    def reconfigurar(self, canales):
        iot_origenes.guardar_origen(self.id, "ESP32 del corral", "generico", 120, canales, sensores_analogicos={},
                                    sensores_ocupados=set(), entradas_en_uso=set(), puede_comandar=True, vencimiento_orden_s=VENCE)

    def test_una_orden_para_una_salida_que_ya_no_existe_no_se_entrega(self):
        o = self.crear("bomba")
        self.reconfigurar([{"clave": "otra", "grupo": "salida", "modo": "pulso", "pulso_s": 2}])
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1)), [])
        self.assertEqual(self.estado(o["id"]), "vencida")
        self.assertIn("ya no", self.filas("SELECT resultado FROM ordenes_origen WHERE id = ?", o["id"])[0][0])

    def test_una_orden_para_una_salida_que_cambio_de_modo_no_se_entrega(self):
        o = self.crear("bomba", "pulso")
        self.reconfigurar([{"clave": "bomba", "grupo": "salida", "modo": "sostenida"}])
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1)), [])
        self.assertEqual(self.estado(o["id"]), "vencida")

    def test_una_orden_de_pulso_con_otra_duracion_no_se_entrega(self):
        o = self.crear("bomba", "pulso")
        self.reconfigurar([{"clave": "bomba", "grupo": "salida", "modo": "pulso", "pulso_s": 9}])
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(1)), [])
        self.assertEqual(self.estado(o["id"]), "vencida")

    def test_lo_que_sigue_igual_se_entrega(self):
        self.crear("bomba")
        self.reconfigurar([{"clave": "bomba", "grupo": "salida", "modo": "pulso", "pulso_s": 1.5},
                           {"clave": "nueva", "grupo": "salida", "modo": "pulso", "pulso_s": 3}])
        self.assertEqual([x["canal"] for x in iot_ordenes.entregar(self.id, ahora=seg(1))], ["bomba"])

    def test_crear_con_una_copia_vieja_del_origen_mira_lo_guardado_ahora(self):
        vieja = iot_origenes.obtener(self.id)                       # el administrador la leyó…
        self.reconfigurar([{"clave": "otra", "grupo": "salida", "modo": "pulso", "pulso_s": 2}])   # …y otro sacó «bomba»
        with self.assertRaises(iot_ordenes.OrdenError):
            iot_ordenes.crear(vieja, "bomba", "pulso", "admin", ahora=T0)
        self.assertEqual(iot_ordenes.listar(self.id), [])

    def test_crear_con_una_copia_vieja_de_un_origen_que_ya_no_puede_comandar(self):
        vieja = iot_origenes.obtener(self.id)
        iot_origenes.guardar_origen(self.id, "x", "generico", 120, [], sensores_analogicos={}, sensores_ocupados=set(),
                                    entradas_en_uso=set())
        with self.assertRaises(iot_ordenes.OrdenError):
            iot_ordenes.crear(vieja, "bomba", "pulso", "admin", ahora=T0)


class TestSostenidasYCancelacion(Base):
    def test_apagar_reemplaza_a_un_prender_que_espera_confirmacion(self):
        # El relé puede estar prendido y LactIA no se entera: apagar no puede quedar bloqueado hasta que venza el prender.
        p = self.crear("calefaccion", "prender")
        iot_ordenes.entregar(self.id, ahora=seg(1))                              # entregada; la respuesta del equipo se perdió
        a = self.crear("calefaccion", "apagar", ahora=seg(2))
        self.assertEqual(a["estado"], "pendiente")
        self.assertEqual(self.estado(p["id"]), "sin_confirmar")
        self.assertIn("reemplaz", self.filas("SELECT resultado FROM ordenes_origen WHERE id = ?", p["id"])[0][0])
        self.assertEqual([x["accion"] for x in iot_ordenes.entregar(self.id, ahora=seg(3))], ["apagar"])

    def test_un_prender_pendiente_reemplazado_no_se_entrega(self):
        p = self.crear("calefaccion", "prender")
        self.crear("calefaccion", "apagar", ahora=seg(1))
        self.assertEqual(self.estado(p["id"]), "vencida")
        self.assertEqual([x["accion"] for x in iot_ordenes.entregar(self.id, ahora=seg(2))], ["apagar"])

    def test_la_misma_accion_repetida_sigue_bloqueada_y_el_pulso_tampoco_se_reemplaza(self):
        self.crear("calefaccion", "prender")
        with self.assertRaises(iot_ordenes.OrdenError) as cm:
            self.crear("calefaccion", "prender", ahora=seg(1))
        self.assertEqual(cm.exception.codigo, 409)
        self.crear("bomba", "pulso")
        with self.assertRaises(iot_ordenes.OrdenError):
            self.crear("bomba", "pulso", ahora=seg(1))

    def test_cancelar_una_orden_pendiente_no_se_entrega_nunca(self):
        o = self.crear()
        r = iot_ordenes.cancelar_orden(self.id, o["id"], "la canceló admin", ahora=seg(1))
        self.assertEqual(r["estado"], "vencida")
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(2)), [])
        self.assertEqual(self.crear(ahora=seg(3))["estado"], "pendiente")            # y el canal queda libre

    def test_cancelar_una_entregada_la_deja_sin_confirmar(self):
        o = self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        self.assertEqual(iot_ordenes.cancelar_orden(self.id, o["id"], "x", ahora=seg(2))["estado"], "sin_confirmar")

    def test_cancelar_lo_que_ya_termino_o_lo_ajeno(self):
        o = self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        iot_ordenes.confirmar(self.id, o["id"], ahora=seg(2))
        with self.assertRaises(iot_ordenes.OrdenError) as a:
            iot_ordenes.cancelar_orden(self.id, o["id"], "x", ahora=seg(3))
        self.assertEqual(a.exception.codigo, 409)
        otro = self.nuevo_origen("Otro ESP32")
        with self.assertRaises(iot_ordenes.OrdenError) as b:
            iot_ordenes.cancelar_orden(otro, o["id"], "x", ahora=seg(3))
        with self.assertRaises(iot_ordenes.OrdenError) as c:
            iot_ordenes.cancelar_orden(self.id, 999, "x", ahora=seg(3))
        self.assertEqual((b.exception.codigo, str(b.exception)), (c.exception.codigo, str(c.exception)))
        self.assertEqual(c.exception.codigo, 404)

    def test_un_numero_de_orden_enorme_o_negativo_es_404_no_un_error_de_la_base(self):
        for n in (10 ** 30, 2 ** 63, -5, 0):
            with self.subTest(n=n):
                with self.assertRaises(iot_ordenes.OrdenError) as cm:
                    iot_ordenes.confirmar(self.id, n, ahora=seg(1))
                self.assertEqual(cm.exception.codigo, 404)
                with self.assertRaises(iot_ordenes.OrdenError) as cm:
                    iot_ordenes.cancelar_orden(self.id, n, "x", ahora=seg(1))
                self.assertEqual(cm.exception.codigo, 404)

    def test_bajar_el_vencimiento_acorta_lo_que_ya_esta_esperando(self):
        pendiente = self.crear("bomba", ahora=T0)                        # vence a los 30 s
        entregada = self.crear("otra", ahora=T0)
        iot_ordenes.entregar(self.id, ahora=seg(1))                      # (ambas) confirmar_antes a los 31 s
        pendiente2 = self.crear("calefaccion", "prender", ahora=T0)
        self.assertGreaterEqual(iot_ordenes.reajustar_vencimiento(self.id, 10), 1)
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(15)), [])             # la pendiente venció a los 10 s
        self.assertEqual(self.estado(pendiente2["id"]), "vencida")
        self.assertEqual(self.estado(entregada["id"]), "sin_confirmar")                # la entregada, a 1 + 10 s

    def test_subir_el_vencimiento_no_alarga_lo_que_ya_estaba(self):
        o = self.crear("bomba", ahora=T0)
        self.assertEqual(iot_ordenes.reajustar_vencimiento(self.id, 600), 0)
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(VENCE + 1)), [])
        self.assertEqual(self.estado(o["id"]), "vencida")

    def test_olvidar_conserva_el_historial_pero_el_equipo_nuevo_no_lo_hereda(self):
        o = self.crear()
        iot_ordenes.entregar(self.id, ahora=seg(1))
        iot_ordenes.confirmar(self.id, o["id"], ahora=seg(2))
        iot_ordenes.olvidar(self.id)
        self.assertEqual(iot_ordenes.listar(self.id), [])
        self.assertEqual(self.filas("SELECT COUNT(*) FROM ordenes_origen"), [(1,)])      # el registro de qué se activó y quién lo pidió sigue
        self.assertEqual(iot_ordenes.entregar(self.id, ahora=seg(3)), [])


class TestElM300NoLasVe(Base):
    def test_iot_lavado_no_toca_las_ordenes_de_un_esp32(self):
        # `iot_lavado.ejecutar_comandos_pendientes` marca «error: canal desconocido» todo lo que no es del M300: por eso las órdenes de
        # un ESP32 viven en OTRA tabla (un `iot_lavado.py` viejo, sin reiniciar, se las comería).
        self.crear()
        con = iot_lavado._conectar_db(self.db)
        try:
            cliente = mock.Mock(connected=True)
            iot_lavado.ejecutar_comandos_pendientes(con, cliente)
            self.assertEqual(cliente.mock_calls, [])
        finally:
            con.close()
        self.assertEqual(self.estado(1), "pendiente")
        self.assertEqual(self.filas("SELECT COUNT(*) FROM comandos_actuador"), [(0,)])


if __name__ == "__main__":
    unittest.main()
