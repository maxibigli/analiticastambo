"""Vigilat: el motor. Cuándo corre cada regla, qué se manda y a quién, silencio, máximo por día, reintentos,
confirmación y escalamiento, y que todo quede en la Bitácora.

Todo contra archivos temporales, sin base, sin red y sin mandar nada real.

    C:\\Python314\\python.exe -m unittest tests.test_vigilat_motor -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import vigilante_estado as ve
import vigilat_config as vc
import vigilat_motor as motor
import vigilat_tipos as tipos
from vigilat_estado import Estado

T = datetime.datetime
SABADO = T(2026, 11, 28, 9, 0)          # un sábado a las 9, fuera del silencio


def en(minutos: float, base: T = SABADO) -> T:
    return base + datetime.timedelta(minutes=minutos)


class ErrorDeCanal(Exception):
    def __init__(self, no_salio=True, reintentable=True):
        super().__init__("el canal dijo que no")
        self.no_salio, self.reintentable = no_salio, reintentable


class Acciones:
    def __init__(self):
        self.mensajes, self.alarmas, self.audios = [], [], []
        self.fallan = {}                                   # destino -> excepción
        self.resultado_alarma = (True, "sonó")

    def enviar(self, canal, destino, mensaje):
        if destino in self.fallan:
            raise self.fallan[destino]
        self.mensajes.append((canal, destino, mensaje))

    def sonar_alarma(self, salida):
        self.alarmas.append(salida)
        return self.resultado_alarma

    def hablar(self, texto):
        self.audios.append(texto)
        return True, "se reprodujo"

    def a(self, destino):
        return [m for _c, d, m in self.mensajes if d == destino]


class Bitacora:
    def __init__(self):
        self.registros = {}
        self.rota = False

    def registrar_aviso(self, tambo, descripcion, cuando=None):
        if self.rota:
            raise OSError("disco lleno")
        i = len(self.registros) + 1
        self.registros[i] = descripcion
        return i

    def anotar(self, i, linea):
        self.registros[i] += "\n" + linea
        return True


def persona(id_, nombre, rango="Encargado", **extra):
    base = {"id": id_, "nombre": nombre, "rango": rango, "activa": True, "canales": ["whatsapp"],
            "whatsapp": "+549239200000" + id_[-1], "telegram": "", "correo": ""}
    base.update(extra)
    return base


JUAN = persona("p1", "Juan", "Encargado")
PEDRO = persona("p2", "Pedro", "Mecánico")
LUIS = persona("p3", "Luis", "Veterinario")
DUENA = persona("p4", "Marta", "Dueño")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(ve, "RUTA_DB", os.path.join(self.dir.name, "v.db")),
                  mock.patch.object(ve, "RUTA_CONFIG", os.path.join(self.dir.name, "v.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.estado = Estado(ve.RUTA_DB)
        self.acciones, self.bitacora = Acciones(), Bitacora()
        self.generales = []
        self.hallazgos = {}                                # id de regla -> lista de hallazgos que devuelve
        self.evaluadas = []
        p = mock.patch.object(tipos, "evaluar", self._evaluar)
        p.start()
        self.addCleanup(p.stop)

    def _evaluar(self, regla, ctx):
        self.evaluadas.append((regla["id"], ctx.ahora))
        r = self.hallazgos.get(regla["id"], [])
        if isinstance(r, Exception):
            raise r
        return [dict(h) for h in r if not any(ctx.ya(k) for k in h["claves"])], "nota"

    def config(self, *reglas, personas=(JUAN, PEDRO, LUIS, DUENA), **general):
        cfg = vc.por_defecto()
        cfg["general"].update(activo=True, max_por_persona_dia=10, silencio_desde="22:00", silencio_hasta="05:00",
                              direccion_publica="https://ejemplo.test", salida_alarma="do_3")
        cfg["general"].update(general)
        cfg["personas"] = [dict(p) for p in personas]
        cfg["reglas"] = list(reglas)
        return vc.validar(cfg)

    def regla(self, id_, destinos=("p:p1",), cuando=None, acciones=None, confirmacion=None, tipo="comida_atrasada",
              activa=True):
        r = vc.regla_nueva(tipo, id_, activa, list(destinos))
        r["nombre"] = f"Regla {id_}"
        r["cuando"] = {"modo": "cada", "minutos": 15, "dias": list(range(7)), **(cuando or {})}
        r["acciones"].update(acciones or {})
        r["confirmacion"].update(confirmacion or {})
        return r

    def hallazgo(self, texto="Pasó algo", claves=None, **extra):
        return {"texto": texto, "audio": "Pasó algo.", "claves": claves if claves is not None else [texto], **extra}

    def tick(self, cfg, ahora):
        return motor.tick(cfg, "ponderosa", estado=self.estado, ahora=ahora, fuentes=object(),
                          enviar_generales=self.generales.append, acciones=self.acciones, bitac=self.bitacora,
                          lanzar=lambda tarea: tarea())


class TestCuandoToca(Base):
    def test_apagado_no_hace_nada(self):
        cfg = self.config(self.regla("a"), activo=False)
        self.assertEqual(self.tick(cfg, SABADO)["estado"], "apagado")
        self.assertEqual(self.evaluadas, [])

    def test_cada_n_minutos(self):
        cfg = self.config(self.regla("a"))
        for m in (0, 5, 14.5, 15, 16, 30):
            self.tick(cfg, en(m))
        self.assertEqual([t for _i, t in self.evaluadas], [en(0), en(15), en(30)])

    def test_a_horas_fijas_corre_una_vez_por_horario(self):
        cfg = self.config(self.regla("a", cuando={"modo": "horas", "horas": ["09:30", "13:30"]}, tipo="fallas_unidades"))
        for m in (0, 29, 30, 31, 60, 269, 270, 271):
            self.tick(cfg, en(m))
        self.assertEqual([t for _i, t in self.evaluadas], [T(2026, 11, 28, 9, 30), T(2026, 11, 28, 13, 30)])

    def test_guardar_un_horario_ya_pasado_no_lo_dispara(self):
        cfg = self.config(self.regla("a", cuando={"modo": "horas", "horas": ["08:00"]}, tipo="fallas_unidades"))
        self.tick(cfg, SABADO)
        self.tick(cfg, en(1))
        self.assertEqual(self.evaluadas, [])
        self.tick(cfg, T(2026, 11, 29, 8, 0, 20))          # al otro día sí
        self.assertEqual(len(self.evaluadas), 1)

    def test_un_horario_muy_pasado_por_pc_apagada_no_corre_y_uno_reciente_si(self):
        cfg = self.config(self.regla("a", cuando={"modo": "horas", "horas": ["10:00"]}, tipo="fallas_unidades"))
        self.tick(cfg, SABADO)
        self.tick(cfg, T(2026, 11, 28, 13, 0))             # tres horas tarde
        self.assertEqual(self.evaluadas, [])
        self.tick(cfg, T(2026, 11, 29, 11, 0))             # una hora tarde
        self.assertEqual(len(self.evaluadas), 1)

    def test_dias_de_la_semana(self):
        lunes_a_viernes = [0, 1, 2, 3, 4]
        cfg = self.config(self.regla("a", cuando={"dias": lunes_a_viernes}))
        self.tick(cfg, SABADO)
        self.assertEqual(self.evaluadas, [])
        self.tick(cfg, T(2026, 11, 30, 9, 0))
        self.assertEqual(len(self.evaluadas), 1)

    def test_silencio_solo_trabaja_la_regla_1_y_el_horario_sale_al_terminar(self):
        primera = self.regla("uno")
        segunda = self.regla("dos", cuando={"modo": "horas", "horas": ["23:00"]}, tipo="fallas_unidades")
        cfg = self.config(primera, segunda)
        self.hallazgos = {"uno": [self.hallazgo("urgente")], "dos": [self.hallazgo("de la noche")]}
        self.tick(cfg, T(2026, 11, 28, 21, 0))             # fuera del silencio: se fija «desde»
        self.acciones.mensajes.clear()
        self.hallazgos["uno"] = [self.hallazgo("urgente de noche")]
        self.assertEqual(self.tick(cfg, T(2026, 11, 28, 23, 0))["estado"], "silencio")
        self.assertEqual([i for i, t in self.evaluadas if t.hour == 23], ["uno"])
        self.assertIn("urgente de noche", self.acciones.mensajes[0][2])
        self.tick(cfg, T(2026, 11, 29, 5, 1))              # termina el silencio: corre el horario de las 23:00
        self.assertIn("dos", [i for i, t in self.evaluadas if t.hour == 5])
        self.assertTrue(any("de la noche" in m for _c, _d, m in self.acciones.mensajes))

    def test_una_regla_que_falla_no_tumba_a_las_otras_y_se_reintenta(self):
        cfg = self.config(self.regla("rota"), self.regla("bien"))
        self.hallazgos = {"rota": RuntimeError("base caída"), "bien": [self.hallazgo("ok")]}
        self.assertEqual(self.tick(cfg, SABADO)["estado"], "error")
        self.assertEqual(len(self.acciones.mensajes), 1)
        self.evaluadas.clear()
        self.tick(cfg, en(1))
        self.assertEqual(self.evaluadas, [])               # no martilla la base cada 30 segundos
        self.tick(cfg, en(16))
        self.assertIn("rota", [i for i, _t in self.evaluadas])


class TestEnvios(Base):
    def test_cada_persona_recibe_solo_sus_reglas_en_un_solo_mensaje_y_en_orden_de_prioridad(self):
        cfg = self.config(self.regla("a", ["p:p1"]), self.regla("b", ["p:p2", "p:p1"]))
        self.hallazgos = {"a": [self.hallazgo("PRIMERA")], "b": [self.hallazgo("SEGUNDA")]}
        self.tick(cfg, SABADO)
        juan, pedro = self.acciones.a(JUAN["whatsapp"]), self.acciones.a(PEDRO["whatsapp"])
        self.assertEqual((len(juan), len(pedro)), (1, 1))
        self.assertLess(juan[0].index("PRIMERA"), juan[0].index("SEGUNDA"))
        self.assertNotIn("PRIMERA", pedro[0])
        self.assertEqual(self.acciones.a(LUIS["whatsapp"]), [])
        self.assertIn("Vigilat — sábado 28/11 09:00", juan[0])

    def test_por_rango_y_por_todos_los_canales_de_la_persona(self):
        dos = persona("p5", "Ana", "Veterinario", canales=["telegram", "correo"], telegram="123456",
                      correo="ana@ejemplo.test")
        cfg = self.config(self.regla("a", ["rango:Veterinario"]), personas=(LUIS, dos, JUAN))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.assertEqual(sorted((c, d) for c, d, _m in self.acciones.mensajes),
                         [("correo", "ana@ejemplo.test"), ("telegram", "123456"), ("whatsapp", LUIS["whatsapp"])])

    def test_canales_generales(self):
        cfg = self.config(self.regla("a", ["generales"]))
        self.hallazgos = {"a": [self.hallazgo("para todos")]}
        self.tick(cfg, SABADO)
        self.assertEqual(len(self.generales), 1)
        self.assertIn("para todos", self.generales[0])
        self.assertEqual(self.acciones.mensajes, [])

    def test_no_repite_lo_ya_avisado_ni_despues_de_reiniciar(self):
        cfg = self.config(self.regla("a"))
        self.hallazgos = {"a": [self.hallazgo("una vez")]}
        self.tick(cfg, SABADO)
        self.estado = Estado(ve.RUTA_DB)
        self.tick(cfg, en(15))
        self.tick(cfg, en(30))
        self.assertEqual(len(self.acciones.mensajes), 1)

    def test_el_fallo_de_una_persona_no_frena_a_las_demas_y_se_reintenta_sin_repetir(self):
        cfg = self.config(self.regla("a", ["p:p1", "p:p2"]))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal()
        self.tick(cfg, SABADO)
        self.assertEqual(len(self.acciones.a(PEDRO["whatsapp"])), 1)
        self.tick(cfg, en(1))                              # todavía no toca reintentar
        self.acciones.fallan.clear()
        self.tick(cfg, en(6))
        self.tick(cfg, en(20))
        self.assertEqual((len(self.acciones.a(JUAN["whatsapp"])), len(self.acciones.a(PEDRO["whatsapp"]))), (1, 1))

    def test_si_no_se_sabe_si_salio_no_se_reintenta(self):
        cfg = self.config(self.regla("a"))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal(no_salio=False, reintentable=False)
        self.tick(cfg, SABADO)
        self.acciones.fallan.clear()
        for m in (6, 20, 60):
            self.tick(cfg, en(m))
        self.assertEqual(self.acciones.mensajes, [])
        self.assertEqual(self.estado.envios_de(1)[0]["estado"], "incierto")

    def test_un_rechazo_del_canal_se_prueba_tres_veces(self):
        cfg = self.config(self.regla("a"))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal(no_salio=True, reintentable=False)
        for m in (0, 6, 17, 60, 120):
            self.tick(cfg, en(m))
        e = self.estado.envios_de(1)[0]
        self.assertEqual((e["estado"], e["intentos"]), ("fallido", 3))

    def test_lo_que_no_se_pudo_mandar_en_12_horas_se_descarta(self):
        cfg = self.config(self.regla("a"))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal()
        self.tick(cfg, SABADO)
        self.acciones.fallan.clear()
        self.tick(cfg, en(13 * 60))
        self.assertEqual(self.acciones.mensajes, [])
        self.assertEqual(self.estado.envios_de(1)[0]["estado"], "vencido")

    def test_maximo_por_persona_por_dia(self):
        cfg = self.config(self.regla("uno", ["p:p2"]), self.regla("dos"), max_por_persona_dia=2)
        for n, m in enumerate((0, 15, 30, 45)):
            self.hallazgos = {"dos": [self.hallazgo(f"n{n}")]}
            self.tick(cfg, en(m))
        self.assertEqual(len(self.acciones.a(JUAN["whatsapp"])), 2)
        self.assertEqual(len(self.estado.retenidos()), 2)
        self.assertIn("Regla dos: n2", self.estado.retenidos()[0])
        self.hallazgos = {"dos": [self.hallazgo("mañana")]}
        self.tick(cfg, T(2026, 11, 29, 9, 0))              # otro día, otra cuenta
        self.assertEqual(len(self.acciones.a(JUAN["whatsapp"])), 3)

    def test_la_regla_1_y_las_que_piden_confirmacion_no_tienen_maximo(self):
        urgente = self.regla("uno")
        con_conf = self.regla("dos", confirmacion={"requiere": True, "dueno": False})
        comun = self.regla("tres")
        cfg = self.config(urgente, con_conf, comun, max_por_persona_dia=1)
        self.hallazgos = {"tres": [self.hallazgo("c0")]}
        self.tick(cfg, SABADO)
        self.hallazgos = {"uno": [self.hallazgo("URGENTE")], "dos": [self.hallazgo("CONFIRMAR")],
                          "tres": [self.hallazgo("c1")]}
        self.tick(cfg, en(15))
        segundo = self.acciones.a(JUAN["whatsapp"])[1]
        self.assertIn("URGENTE", segundo)
        self.assertIn("CONFIRMAR", segundo)
        self.assertNotIn("c1", segundo)

    def test_sin_canales_generales_se_reintenta(self):
        cfg = self.config(self.regla("a", ["generales"]))
        self.hallazgos = {"a": [self.hallazgo()]}
        motor.tick(cfg, "ponderosa", estado=self.estado, ahora=SABADO, fuentes=object(), enviar_generales=None,
                   acciones=self.acciones, bitac=self.bitacora, lanzar=lambda t: t())
        self.assertEqual(self.estado.envios_de(1)[0]["estado"], "pendiente")
        self.tick(cfg, en(6))
        self.assertEqual(len(self.generales), 1)


class TestSalaYBitacora(Base):
    def test_alarma_audio_y_todo_a_la_bitacora(self):
        cfg = self.config(self.regla("a", acciones={"alarma": True, "audio": True}))
        self.hallazgos = {"a": [self.hallazgo("Puesto 21 roto")]}
        self.tick(cfg, SABADO)
        self.assertEqual(self.acciones.alarmas, ["do_3"])
        self.assertEqual(len(self.acciones.audios), 1)
        self.assertIn("Pasó algo.", self.acciones.audios[0])
        nota = self.bitacora.registros[1]
        for parte in ("regla 1 «Regla a»", "Puesto 21 roto", "Se avisa a: Juan (WhatsApp)", "Alarma: sonó",
                      "Audio en la sala: se reprodujo", "Enviado a Juan por WhatsApp"):
            self.assertIn(parte, nota)

    def test_una_regla_sin_acciones_igual_queda_en_la_bitacora(self):
        cfg = self.config(self.regla("a", [], acciones={"mensaje": False}))
        self.hallazgos = {"a": [self.hallazgo("solo anotar")]}
        self.tick(cfg, SABADO)
        self.assertIn("solo anotar", self.bitacora.registros[1])
        self.assertEqual((self.acciones.mensajes, self.acciones.alarmas), ([], []))

    def test_si_la_alarma_falla_queda_anotado(self):
        self.acciones.resultado_alarma = (False, "el gateway no contestó")
        cfg = self.config(self.regla("a", acciones={"alarma": True}))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.assertIn("Alarma: NO se pudo — el gateway no contestó", self.bitacora.registros[1])

    def test_sin_bitacora_el_aviso_sale_igual(self):
        self.bitacora.rota = True
        cfg = self.config(self.regla("a"))
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.assertEqual(len(self.acciones.mensajes), 1)
        self.assertIn("No se pudo anotar en la Bitácora", self.estado.eventos_de(1)[0]["texto"])


class TestConfirmacion(Base):
    def cfg(self, **extra):
        conf = {"requiere": True, "espera_min": 10, "nivel2": ["p:p2"], "nivel3": ["p:p3"], "dueno": True}
        conf.update(extra)
        return self.config(self.regla("uno", ["p:p3"], tipo="fallas_unidades", activa=False),
                           self.regla("a", ["p:p1"], confirmacion=conf))

    def test_escala_segunda_tercera_y_dueno(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo("grave")]}
        self.tick(cfg, SABADO)
        self.assertIn("/vigilat/c/", self.acciones.a(JUAN["whatsapp"])[0])
        self.assertIn("OK 1", self.acciones.a(JUAN["whatsapp"])[0])
        self.tick(cfg, en(9))
        self.assertEqual(self.acciones.a(PEDRO["whatsapp"]), [])
        self.tick(cfg, en(10))
        pedro = self.acciones.a(PEDRO["whatsapp"])[0]
        self.assertIn("Nadie confirmó", pedro)
        self.assertIn("se le avisó a Juan", pedro)
        self.tick(cfg, en(20))
        self.assertEqual(len(self.acciones.a(LUIS["whatsapp"])), 1)
        self.tick(cfg, en(30))
        self.assertEqual(len(self.acciones.a(DUENA["whatsapp"])), 1)
        self.tick(cfg, en(40))
        self.tick(cfg, en(60))
        self.assertEqual(self.estado.aviso(1)["estado"], "sin_confirmar")
        self.assertEqual(len(self.acciones.mensajes), 4)
        self.assertIn("ya se avisó a todos los niveles", self.bitacora.registros[1])

    def test_al_confirmar_se_deja_de_escalar(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.assertTrue(motor.confirmar(self.estado, 1, "Juan", "enlace", en(5), self.bitacora))
        self.assertFalse(motor.confirmar(self.estado, 1, "Pedro", "enlace", en(6), self.bitacora))
        for m in (10, 20, 30):
            self.tick(cfg, en(m))
        self.assertEqual(len(self.acciones.mensajes), 1)
        self.assertEqual(self.estado.aviso(1)["confirmado_por"], "Juan")
        self.assertIn("Confirmado por Juan (enlace)", self.bitacora.registros[1])

    def test_sin_segunda_ni_tercera_va_al_dueno(self):
        cfg = self.cfg(nivel2=[], nivel3=[])
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.tick(cfg, en(10))
        self.assertEqual(len(self.acciones.a(DUENA["whatsapp"])), 1)

    def test_si_no_se_le_puede_mandar_a_la_primera_se_pasa_a_la_segunda_sin_esperar(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal(no_salio=True, reintentable=False)
        for m in (0, 5, 15):                               # tres rechazos
            self.tick(cfg, en(m))
        self.tick(cfg, en(16))
        self.assertEqual(len(self.acciones.a(PEDRO["whatsapp"])), 1)

    def test_con_la_primera_en_reintento_igual_escala_cumplida_la_espera(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.acciones.fallan[JUAN["whatsapp"]] = ErrorDeCanal()
        self.tick(cfg, SABADO)
        self.tick(cfg, en(10))
        self.assertEqual(len(self.acciones.a(PEDRO["whatsapp"])), 1)

    def test_confirmar_por_respuesta_de_whatsapp(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        responder = lambda t, de=JUAN["whatsapp"]: motor.confirmar_por_respuesta(cfg, self.estado, de, t, en(2),
                                                                                self.bitacora)
        self.assertIsNone(responder("¿cuánta leche hubo?"))
        self.assertIsNone(responder("ok 1", de="+5491100000000"))          # un desconocido
        self.assertIn("No tengo ningún aviso #1", responder("ok 1", de=PEDRO["whatsapp"]))
        # el mismo celular escrito sin el 9 que pone Argentina después del 54
        self.assertIn("Confirmado el aviso #1", responder("  Ok #1 !", de="+542392000001"))
        self.assertIn("ya lo había confirmado Juan", responder("ok 1"))
        self.assertIsNone(responder("ok"))                                  # nada pendiente: sigue a la IA

    def test_ok_sin_numero_con_varios_pendientes_pide_cual(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo("x"), self.hallazgo("y")]}
        self.tick(cfg, SABADO)
        r = motor.confirmar_por_respuesta(cfg, self.estado, JUAN["whatsapp"], "ok", en(1), self.bitacora)
        self.assertIn("2 avisos sin confirmar", r)
        self.assertEqual(self.estado.aviso(1)["estado"], "pendiente")

    def test_la_regla_comun_no_escala_de_noche(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, T(2026, 11, 28, 21, 55))
        self.tick(cfg, T(2026, 11, 28, 23, 0))
        self.assertEqual(self.acciones.a(PEDRO["whatsapp"]), [])
        self.tick(cfg, T(2026, 11, 29, 5, 0))
        self.assertEqual(len(self.acciones.a(PEDRO["whatsapp"])), 1)

    def test_despues_de_dias_apagado_no_escala_algo_viejo(self):
        cfg = self.cfg()
        self.hallazgos = {"a": [self.hallazgo()]}
        self.tick(cfg, SABADO)
        self.tick(cfg, T(2026, 12, 1, 9, 0))
        self.assertEqual(self.estado.aviso(1)["estado"], "sin_confirmar")
        self.assertEqual(len(self.acciones.mensajes), 1)

    def test_volvieron_los_datos_cierra_el_aviso_y_no_pide_confirmar(self):
        regla = self.regla("sd", tipo="sin_datos", confirmacion={"requiere": True, "espera_min": 10})
        cfg = self.config(regla)
        self.hallazgos = {"sd": [self.hallazgo("cortado", datos_estado="sin_datos")]}
        self.tick(cfg, SABADO)
        self.hallazgos = {"sd": [self.hallazgo("volvieron", datos_estado="ok", cierra=True, confirmable=False)]}
        self.tick(cfg, en(15))
        self.assertEqual(self.estado.aviso(1)["estado"], "resuelto")
        self.assertEqual(self.estado.aviso(2)["estado"], "avisado")
        self.tick(cfg, en(60))
        self.assertEqual(self.acciones.a(DUENA["whatsapp"]), [])


class TestProbar(Base):
    def test_probar_dice_que_saldria_y_a_quien_sin_escribir_ni_mandar_nada(self):
        cfg = self.config(self.regla("a", ["p:p1"], confirmacion={"requiere": True, "nivel2": ["p:p2"]},
                                     activa=False), activo=False)
        self.hallazgos = {"a": [self.hallazgo("PRUEBA")]}
        r = motor.probar(cfg, "ponderosa", estado=self.estado, ahora=T(2026, 11, 28, 23, 0), fuentes=object(),
                         disponibles={"whatsapp": False, "telegram": True, "correo": True})
        self.assertEqual(r[0]["hallazgos"][0]["texto"], "PRUEBA")
        self.assertEqual([[p["nombre"] for p in n] for n in r[0]["niveles"]], [["Juan"], ["Pedro"], ["Marta"]])
        self.assertIn("WhatsApp no está configurado", r[0]["niveles"][0][0]["problemas"][0])
        self.assertEqual((self.acciones.mensajes, self.bitacora.registros), ([], {}))
        self.assertIsNone(self.estado.aviso(1))
        self.assertIsNone(self.estado.ultima_pasada())
        self.assertFalse(self.estado.ya_avisado("PRUEBA"))


if __name__ == "__main__":
    unittest.main()
