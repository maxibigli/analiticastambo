"""Envío programado de informes (`informes_envios.py`): mandar un informe guardado por correo, Telegram o WhatsApp en los días y horas que se elijan.

Lo que importa:
  * NUNCA se manda dos veces lo mismo (un reinicio, dos pasadas seguidas o dos procesos no duplican): cada (programación, horario, canal) se RECLAMA
    en la base antes de mandar. Un envío que quedó a medias (el proceso murió) se marca «sin confirmar» y no se reenvía solo.
  * Un canal que falla no cancela a los otros ni repite al que ya salió. Un error de base se reintenta unas pocas veces; un informe que ya no se puede
    calcular, no.
  * Lo que venció hace más de una hora no sale tarde (el servidor estuvo apagado), y nada sale por un horario anterior a la programación.
  * Ningún error guarda ni muestra el texto de la excepción (puede traer el token de Telegram o un teléfono).
  * «Probar» arma y manda de verdad pero NO anota nada: no cuenta, funciona con la programación pausada.
  * No hay DELETE: una programación se pausa.
Todo con un reloj y un `enviar` inyectados: ninguna prueba manda nada ni toca una base real.

    C:\\Python314\\python.exe -m unittest tests.test_informes_envios -v
"""
import datetime
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import informes_envios as ie
import informes_excel
import informes_guardados as ig
import informes_motor as im

T = "ponderosa"
DEF = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM"}], "orden": {"campo": "CurrentDIM", "dir": -1}}
DEF_GRUPOS = {"columnas": [{"campo": "AnimalGroupName"}, {"campo": "CurrentDIM", "resumen": "avg"}, {"campo": "CurrentReproductionStatus"}], "agrupar_por": ["AnimalGroupName"]}
JUEVES = datetime.datetime(2026, 8, 27, 9, 0)                            # jueves 27/08/2026, 09:00


class NoSalio(RuntimeError):
    """Un canal que SABE que no mandó nada (no pudo ni conectar): lo único que se reintenta solo (ver `envio_red`)."""
    no_salio = True
    reintentable = True


class Rechazo(RuntimeError):
    """Un canal que contestó que no (credenciales, destinatario): no salió, pero volver a probar da lo mismo."""
    no_salio = True
    reintentable = False


def meta(id_, etiqueta, tipo="num", decimales=0, resumen="none", verificado=True, unidad=""):
    return {"id": id_, "alias": "c0", "etiqueta": etiqueta, "tipo": tipo, "unidad": unidad, "decimales": decimales, "categoria": "X", "verificado": verificado,
            "calculado": False, "resumen": resumen}


def resultado(n=3, truncado=False, verificado=True):
    cols = [meta("Number", "Número del animal", "txt"), meta("CurrentDIM", "Días en ordeño", verificado=verificado)]
    filas = [[str(3000 + i), 300 - i] for i in range(n)]
    return {"columnas": cols, "filas": filas, "claves_filas": None, "truncado": truncado, "detalle": True, "agrupar": [], "grupos": [], "totales": None,
            "sql": None, "segundos": 0.2}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(ig, "RUTA_DB", os.path.join(self.dir.name, "informes.db"))
        p.start()
        self.addCleanup(p.stop)
        self.informe = ig.crear(T, "Vacas por días en ordeño", DEF, "maxi")
        self.enviados, self.consultas = [], []
        self.falla = {}                                                      # canal -> excepción que tiene que levantar
        self.disponibles = ["whatsapp", "telegram", "correo"]
        self.resultado = resultado()

    # --- los dobles
    def enviar(self, canal, armado):
        if canal in self.falla:
            raise self.falla[canal]
        self.enviados.append((canal, armado))

    def ejecutar(self, defn, tambo, hoy=None):
        self.consultas.append((defn, tambo, hoy))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado

    def programar(self, dias=(3,), horas=("08:30",), canales=("telegram",), formato="texto", ahora=None, informe=None, activo=True):
        return ie.guardar(T, (informe or self.informe)["id"], list(dias), list(horas), list(canales), formato, activo, "maxi", ahora=ahora or datetime.datetime(2026, 8, 26, 12, 0))

    def pasada(self, ahora, **kw):
        return ie.pasada(self.enviar, lambda: self.disponibles, ahora=ahora, ejecutar=self.ejecutar, **kw)

    def registro(self):
        return ie.registro(T, self.informe["id"], 50)


class TestValidar(unittest.TestCase):
    def ok(self, **kw):
        base = dict(dias=[0, 3], horas=["08:00", "20:00"], canales=["telegram"], formato="excel")
        base.update(kw)
        return ie.validar(**base)

    def test_normaliza_dias_horas_y_canales(self):
        r = self.ok(dias=[3, 0, 3], horas=["20:00", "08:00", "08:00"], canales=["correo", "telegram", "correo"])
        self.assertEqual(r, {"dias": [0, 3], "horas": ["08:00", "20:00"], "canales": ["correo", "telegram"], "formato": "excel"})        # sin repetir y en orden

    def test_rechaza_lo_que_no_sirve(self):
        for malo in ({"dias": []}, {"dias": [7]}, {"dias": [-1]}, {"dias": ["lunes"]}, {"dias": [True]}, {"dias": "0"}, {"dias": None},
                     {"horas": []}, {"horas": ["8:00"]}, {"horas": ["24:00"]}, {"horas": ["08:60"]}, {"horas": [800]}, {"horas": "08:00"},
                     {"horas": [f"0{i}:00" for i in range(ie.MAX_HORARIOS + 1)]},
                     {"canales": []}, {"canales": ["sms"]}, {"canales": "correo"}, {"canales": [None]},
                     {"formato": "pdf"}, {"formato": None}):
            with self.assertRaises(ie.EnvioError, msg=str(malo)):
                self.ok(**malo)

    def test_el_maximo_de_horarios_sirve(self):
        self.assertEqual(len(self.ok(horas=[f"0{i}:00" for i in range(ie.MAX_HORARIOS)])["horas"]), ie.MAX_HORARIOS)


class TestGuardar(Base):
    def test_guarda_y_lee(self):
        e = self.programar(dias=[3, 0], horas=["20:00", "08:30"], canales=["correo", "telegram"], formato="excel")
        self.assertEqual((e["dias"], e["horas"], e["canales"], e["formato"], e["activo"]), ([0, 3], ["08:30", "20:00"], ["correo", "telegram"], "excel", True))
        self.assertEqual(ie.obtener(T, self.informe["id"], ahora=datetime.datetime(2026, 8, 26, 12, 0)), e)
        self.assertIsNone(ie.obtener(T, 999))

    def test_incluye_el_proximo_envio_con_el_dia_de_la_semana(self):
        e = self.programar(ahora=datetime.datetime(2026, 8, 27, 7, 0))            # jueves 07:00 -> hoy 08:30
        self.assertEqual(e["proximo_texto"], "jue 27/08/2026 08:30")
        e2 = ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 27, 9, 0))
        self.assertEqual(e2["proximo_texto"], "jue 03/09/2026 08:30")              # ya pasó la de hoy: el jueves que viene

    def test_un_segundo_guardado_actualiza_la_misma_programacion(self):
        self.programar()
        e = self.programar(horas=["07:00"], canales=["correo"])
        self.assertEqual(e["horas"], ["07:00"])
        self.assertEqual(len(ie.listar(T)), 1)

    def test_el_informe_tiene_que_existir_y_ser_del_tambo(self):
        with self.assertRaises(ie.EnvioError):
            ie.guardar(T, 999, [3], ["08:30"], ["telegram"], "texto", True, "maxi")
        with self.assertRaises(ie.EnvioError):
            ie.guardar("otro_tambo", self.informe["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi")

    def test_pausar_no_borra(self):
        self.programar()
        e = ie.pausar(T, self.informe["id"], ahora=datetime.datetime(2026, 8, 26, 13, 0))
        self.assertFalse(e["activo"])
        self.assertIsNone(e["proximo"])
        self.assertEqual(len(ie.listar(T)), 1)
        self.assertFalse(hasattr(ie, "borrar") or hasattr(ie, "eliminar"))             # regla de no borrar: no existe esa función

    def test_pausar_lo_que_no_existe(self):
        with self.assertRaises(ie.EnvioError):
            ie.pausar(T, 999)

    def test_hay_un_tope_de_programaciones_activas_por_tambo(self):
        ids = []
        for i in range(ie.MAX_PROGRAMADOS):
            inf = ig.crear(T, f"Informe {i}", DEF, "maxi")
            ids.append(inf["id"])
            ie.guardar(T, inf["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi")
        extra = ig.crear(T, "Uno de más", DEF, "maxi")
        with self.assertRaises(ie.EnvioError):
            ie.guardar(T, extra["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi")
        ie.guardar(T, extra["id"], [3], ["08:30"], ["telegram"], "texto", False, "maxi")      # pausada no cuenta
        ie.pausar(T, ids[0])
        ie.guardar(T, extra["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi")       # se liberó un lugar

    def test_la_lista_resume_por_informe(self):
        self.programar(canales=["correo", "telegram"])
        r = ie.listar(T)
        self.assertEqual(list(r), [self.informe["id"]])
        self.assertEqual(r[self.informe["id"]]["canales"], ["correo", "telegram"])


class TestCuandoVence(Base):
    def test_proximo(self):
        e = self.programar(dias=[0, 3], horas=["08:00", "20:00"])
        self.assertEqual(ie.proximo(e, datetime.datetime(2026, 8, 27, 7, 59)), datetime.datetime(2026, 8, 27, 8, 0))
        self.assertEqual(ie.proximo(e, datetime.datetime(2026, 8, 27, 8, 0)), datetime.datetime(2026, 8, 27, 20, 0))        # justo a la hora: ya no es «próximo»
        self.assertEqual(ie.proximo(e, datetime.datetime(2026, 8, 27, 20, 1)), datetime.datetime(2026, 8, 31, 8, 0))        # el lunes siguiente
        self.assertIsNone(ie.proximo(dict(e, activo=False), JUEVES))

    def test_slots_vencidos(self):
        e = self.programar(dias=[3], horas=["08:30"])
        antes = datetime.datetime(2026, 8, 27, 8, 29)
        self.assertEqual(ie.slots_vencidos(e, antes), [])
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 8, 30)), [datetime.datetime(2026, 8, 27, 8, 30)])
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 9, 30)), [datetime.datetime(2026, 8, 27, 8, 30)])      # justo al límite de la gracia
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 9, 31)), [])                                          # pasó la gracia: no sale tarde

    def test_un_horario_cerca_de_medianoche_no_se_pierde_al_cambiar_el_dia(self):
        e = self.programar(dias=[2], horas=["23:50"], ahora=datetime.datetime(2026, 8, 26, 12, 0))                                 # miércoles 23:50
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 0, 20)), [datetime.datetime(2026, 8, 26, 23, 50)])

    def test_nada_sale_por_un_horario_anterior_a_la_programacion(self):
        e = self.programar(dias=[3], horas=["08:30"], ahora=datetime.datetime(2026, 8, 27, 8, 45))                                 # se guardó 08:45
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 9, 0)), [])

    def test_un_dia_que_no_esta_elegido_no_vence(self):
        e = self.programar(dias=[0], horas=["08:30"])
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 8, 31)), [])

    def test_pausada_no_vence(self):
        e = self.programar(dias=[3], horas=["08:30"])
        e = ie.pausar(T, self.informe["id"], ahora=datetime.datetime(2026, 8, 26, 13, 0))
        self.assertEqual(ie.slots_vencidos(e, datetime.datetime(2026, 8, 27, 8, 31)), [])


class TestPasada(Base):
    def setUp(self):
        super().setUp()
        self.programar(dias=[3], horas=["08:30"], canales=["telegram", "correo"])
        self.ahora = datetime.datetime(2026, 8, 27, 8, 31)

    def test_manda_por_cada_canal_y_lo_anota(self):
        r = self.pasada(self.ahora)
        self.assertEqual(sorted(c for c, _ in self.enviados), ["correo", "telegram"])
        self.assertEqual(sorted((x["canal"], x["estado"]) for x in self.registro()), [("correo", "enviado"), ("telegram", "enviado")])
        self.assertEqual(len(r["enviados"]), 2)

    def test_arma_el_informe_UNA_vez_aunque_haya_varios_canales(self):
        self.pasada(self.ahora)
        self.assertEqual(len(self.consultas), 1)
        self.assertEqual(self.consultas[0][1], T)                           # con el tambo de la programación
        self.assertEqual(self.consultas[0][0]["columnas"], DEF["columnas"])      # y con la definición GUARDADA

    def test_no_manda_dos_veces_lo_mismo(self):
        self.pasada(self.ahora)
        self.pasada(self.ahora + datetime.timedelta(minutes=1))
        self.pasada(self.ahora + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.enviados), 2)
        self.assertEqual(len(self.consultas), 1)

    def test_un_reinicio_no_duplica(self):
        """Otra «instancia» del hilo (otro proceso, o el mismo reiniciado) mira la misma base: ve lo ya reclamado."""
        self.pasada(self.ahora)
        otro_enviar = mock.Mock()
        ie.pasada(otro_enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=self.ejecutar)
        otro_enviar.assert_not_called()

    def test_el_reclamo_es_atomico(self):
        e = ie.obtener(T, self.informe["id"])
        slot = datetime.datetime(2026, 8, 27, 8, 30)
        with ie._con() as con:
            self.assertTrue(ie._reclamar(con, e["id"], slot, "telegram", self.ahora))
            self.assertFalse(ie._reclamar(con, e["id"], slot, "telegram", self.ahora))
            self.assertTrue(ie._reclamar(con, e["id"], slot, "correo", self.ahora))

    def test_un_canal_que_falla_no_cancela_a_los_otros(self):
        self.falla["telegram"] = Rechazo("Telegram respondió 400: token-secreto-123")
        r = self.pasada(self.ahora)
        estados = {x["canal"]: x for x in self.registro()}
        self.assertEqual(estados["correo"]["estado"], "enviado")
        self.assertEqual(estados["telegram"]["estado"], "fallido")
        self.assertEqual([c for c, _ in self.enviados], ["correo"])
        self.assertEqual(len(r["fallidos"]), 1)

    def test_el_error_no_guarda_el_texto_de_la_excepcion(self):
        self.falla["telegram"] = RuntimeError("https://api.telegram.org/bot123:token-secreto-123/sendDocument falló, chat +5493415551234")
        self.pasada(self.ahora)
        detalle = [x for x in self.registro() if x["canal"] == "telegram"][0]["detalle"]
        self.assertNotIn("token-secreto", detalle)
        self.assertNotIn("5493415551234", detalle)
        self.assertIn("Telegram", detalle)

    def test_un_fallo_se_reintenta_pasados_10_minutos_y_hasta_tres_veces(self):
        self.falla["telegram"] = NoSalio("x")                                                      # solo lo que se SABE que no salió se reintenta (ver la revisión)
        self.pasada(self.ahora)                                                                    # intento 1
        self.pasada(self.ahora + datetime.timedelta(minutes=5))                                    # muy pronto
        self.assertEqual([x for x in self.registro() if x["canal"] == "telegram"][0]["intentos"], 1)
        self.pasada(self.ahora + datetime.timedelta(minutes=11))                                   # intento 2
        self.assertEqual([x for x in self.registro() if x["canal"] == "telegram"][0]["intentos"], 2)
        self.pasada(self.ahora + datetime.timedelta(minutes=22))                                   # intento 3
        self.pasada(self.ahora + datetime.timedelta(minutes=33))                                   # no hay cuarto
        fila = [x for x in self.registro() if x["canal"] == "telegram"][0]
        self.assertEqual((fila["intentos"], fila["estado"]), (ie.MAX_INTENTOS, "fallido"))

    def test_si_el_reintento_sale_queda_enviado_y_no_se_repite(self):
        self.falla["telegram"] = NoSalio("x")
        self.pasada(self.ahora)
        del self.falla["telegram"]
        self.pasada(self.ahora + datetime.timedelta(minutes=11))
        self.pasada(self.ahora + datetime.timedelta(minutes=22))
        fila = [x for x in self.registro() if x["canal"] == "telegram"][0]
        self.assertEqual(fila["estado"], "enviado")
        self.assertEqual([c for c, _ in self.enviados].count("telegram"), 1)

    def test_los_reintentos_no_pasan_de_la_gracia(self):
        self.falla["telegram"] = NoSalio("x")
        self.pasada(self.ahora)
        self.pasada(self.ahora + datetime.timedelta(minutes=75))                                   # ya pasó la hora de gracia del horario de las 08:30
        self.assertEqual(len(self.enviados), 1)                                                    # solo salió el correo: no hubo un segundo intento de Telegram
        fila = [x for x in self.registro() if x["canal"] == "telegram"][0]
        self.assertEqual((fila["estado"], fila["intentos"]), ("fallido", ie.MAX_INTENTOS))         # y ya no promete un reintento que no va a ocurrir (revisión final, envíos #3)
        self.assertNotIn("Se reintenta", fila["detalle"])
        self.assertIn("Ya no se reintenta: pasó la hora de gracia.", fila["detalle"])

    def test_un_informe_que_ya_no_compila_no_se_reintenta_ni_se_manda(self):
        self.resultado = im.InformeError("El campo «X» no existe en el catálogo.")
        r = self.pasada(self.ahora)
        self.assertEqual(self.enviados, [])
        filas = self.registro()
        self.assertTrue(all(x["estado"] == "fallido" and x["intentos"] == ie.MAX_INTENTOS for x in filas))
        self.assertIn("definición", filas[0]["detalle"])
        self.pasada(self.ahora + datetime.timedelta(minutes=15))
        self.assertEqual(len(self.consultas), 1)

    def test_un_error_de_base_se_reintenta(self):
        self.resultado = RuntimeError("login failed for user delpro_lectura en SERVER-DELPRO")
        self.pasada(self.ahora)
        filas = self.registro()
        self.assertTrue(all(x["estado"] == "fallido" and x["intentos"] == 1 for x in filas))
        self.assertNotIn("delpro_lectura", filas[0]["detalle"])
        self.assertNotIn("SERVER", filas[0]["detalle"])
        self.resultado = resultado()
        self.pasada(self.ahora + datetime.timedelta(minutes=11))
        self.assertEqual(len(self.enviados), 2)

    def test_un_canal_que_no_esta_disponible_se_anota_y_no_se_manda(self):
        self.disponibles = ["telegram"]
        self.pasada(self.ahora)
        self.assertEqual([c for c, _ in self.enviados], ["telegram"])
        correo = [x for x in self.registro() if x["canal"] == "correo"][0]
        self.assertEqual(correo["estado"], "omitido")
        self.assertIn("configurado", correo["detalle"])
        self.disponibles = ["telegram", "correo"]
        self.pasada(self.ahora + datetime.timedelta(minutes=2))                                    # ya quedó anotado para ese horario: no sale tarde
        self.assertEqual([c for c, _ in self.enviados], ["telegram"])

    def test_si_ningun_canal_esta_disponible_ni_se_calcula_el_informe(self):
        self.disponibles = []
        self.pasada(self.ahora)
        self.assertEqual(self.consultas, [])

    def test_no_sale_mas_tarde_de_la_gracia(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=70))
        self.assertEqual(self.enviados, [])
        # No sale, pero deja rastro (revisión final, envíos #2): una fila «omitido» por canal, para que la pantalla diga «no salió» en vez de mostrar el próximo de la semana siguiente.
        self.assertEqual(sorted((x["canal"], x["estado"]) for x in self.registro()), [("correo", "omitido"), ("telegram", "omitido")])
        self.assertTrue(all(x["detalle"].startswith("No salió: el servidor estaba apagado") for x in self.registro()))

    def test_una_programacion_pausada_no_manda(self):
        ie.pausar(T, self.informe["id"], ahora=datetime.datetime(2026, 8, 27, 8, 0))
        self.pasada(self.ahora)
        self.assertEqual(self.enviados, [])

    def test_lo_que_estaba_a_medias_se_marca_sin_confirmar_y_no_se_reenvia(self):
        e = ie.obtener(T, self.informe["id"])
        slot = datetime.datetime(2026, 8, 27, 8, 30)
        with ie._con() as con:
            ie._reclamar(con, e["id"], slot, "telegram", datetime.datetime(2026, 8, 27, 8, 30))        # el proceso murió justo después de reclamar
        self.pasada(datetime.datetime(2026, 8, 27, 9, 5))                                          # 35 minutos después
        fila = [x for x in self.registro() if x["canal"] == "telegram"][0]
        self.assertEqual(fila["estado"], "sin_confirmar")
        self.assertEqual([c for c, _ in self.enviados], ["correo"])                                # el otro canal sí salió; el dudoso NO se reenvía

    def test_un_envio_en_curso_reciente_no_se_toca(self):
        e = ie.obtener(T, self.informe["id"])
        with ie._con() as con:
            ie._reclamar(con, e["id"], datetime.datetime(2026, 8, 27, 8, 30), "telegram", self.ahora)
        self.pasada(self.ahora + datetime.timedelta(minutes=2))
        self.assertEqual([x for x in self.registro() if x["canal"] == "telegram"][0]["estado"], "en_curso")

    def test_la_pasada_deja_su_latido(self):
        self.assertIsNone(ie.ultima_pasada())
        self.pasada(self.ahora)
        self.assertEqual(ie.ultima_pasada(), self.ahora)

    def test_una_pasada_sin_nada_programado_no_falla(self):
        otra = tempfile.TemporaryDirectory()
        self.addCleanup(otra.cleanup)
        with mock.patch.object(ig, "RUTA_DB", os.path.join(otra.name, "vacia.db")):
            self.assertEqual(ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=self.ejecutar)["enviados"], [])

    def test_atiende_a_todos_los_tambos(self):
        inf2 = ig.crear("san_jose", "Otro tambo", DEF, "maxi")
        ie.guardar("san_jose", inf2["id"], [3], ["08:30"], ["correo"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.pasada(self.ahora)
        self.assertEqual(sorted({t for _, t, _ in self.consultas}), ["ponderosa", "san_jose"])

    def test_el_registro_dice_el_horario_programado_con_el_dia_de_la_semana(self):
        self.pasada(self.ahora)
        self.assertEqual(self.registro()[0]["slot_texto"], "jue 27/08/2026 08:30")


class TestArmar(Base):
    def armar(self, formato="texto", resultado_=None, defn=None, ahora=JUEVES):
        if resultado_ is not None:
            self.resultado = resultado_
        informe = dict(self.informe, definicion=defn or DEF)
        return ie.armar(T, informe, formato, ahora=ahora, ejecutar=self.ejecutar)

    def test_el_texto_dice_que_informe_cuando_y_cuantos(self):
        a = self.armar()
        self.assertIn("Vacas por días en ordeño", a["texto"])
        self.assertIn("jue 27/08/2026 a las 09:00", a["texto"])
        self.assertIn("3 animales", a["texto"])
        self.assertEqual(a["animales"], 3)

    def test_el_asunto_y_el_nombre_del_archivo(self):
        a = self.armar("excel")
        self.assertEqual(a["asunto"], "LactIA — Vacas por días en ordeño (jue 27/08/2026)")
        self.assertEqual(a["nombre_archivo"], "Vacas por días en ordeño 2026-08-27.xlsx")

    def test_en_formato_excel_lleva_el_archivo_y_en_texto_no(self):
        con_excel = self.armar("excel")
        self.assertTrue(con_excel["datos"].startswith(b"PK"))
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(con_excel["datos"]))
        self.assertIn("3000", [str(c.value) for c in wb.worksheets[0]["A"]])
        self.assertIsNone(self.armar("texto")["datos"])

    def test_las_primeras_filas_van_en_el_texto(self):
        a = self.armar(resultado_=resultado(25))
        self.assertIn("3000", a["texto"])
        self.assertIn("3009", a["texto"])
        self.assertNotIn("3010", a["texto"])                                  # solo las primeras FILAS_TEXTO
        self.assertIn("25 animales", a["texto"])

    def test_el_texto_no_pasa_del_tope_de_whatsapp(self):
        cols = [meta(f"C{i}", "Un nombre de columna bastante largo " + str(i), "txt") for i in range(12)]
        r = {"columnas": cols, "filas": [["x" * 80] * 12 for _ in range(30)], "claves_filas": None, "truncado": False, "detalle": True, "agrupar": [], "grupos": [],
             "totales": None, "sql": None, "segundos": 0.1}
        a = self.armar(resultado_=r)
        self.assertLessEqual(len(a["texto"]), ie.TEXTO_MAX)

    def test_avisa_si_la_lista_se_corto(self):
        a = self.armar(resultado_=resultado(5, truncado=True))
        self.assertTrue(a["truncado"])
        self.assertIn("se cortó", a["texto"])

    def test_avisa_si_hay_campos_sin_verificar(self):
        a = self.armar(resultado_=resultado(verificado=False))
        self.assertEqual(a["sin_verificar"], ["Días en ordeño"])
        self.assertIn("sin verificar", a["texto"])
        self.assertNotIn("sin verificar", self.armar(resultado_=resultado())["texto"])

    def test_un_informe_vacio(self):
        a = self.armar(resultado_=resultado(0))
        self.assertIn("0 animales", a["texto"])

    def test_con_grupos_y_totales(self):
        r = {"columnas": [meta("AnimalGroupName", "Nombre del grupo", "txt"), meta("CurrentDIM", "Días en ordeño", decimales=1, resumen="avg")],
             "filas": [["Rodeo 1", 100], ["Rodeo 2", 200]], "claves_filas": [["Rodeo 1"], ["Rodeo 2"]], "truncado": False, "detalle": True,
             "agrupar": [{"id": "AnimalGroupName", "alias": "g0", "etiqueta": "Nombre del grupo", "tipo": "txt", "decimales": 0}],
             "grupos": [{"nivel": 1, "claves": ["Rodeo 1"], "n": 410, "resumenes": {"c1": 150.26}}, {"nivel": 1, "claves": ["Rodeo 2"], "n": 325, "resumenes": {"c1": 201.5}}],
             "totales": {"n": 735, "resumenes": {"c1": 172.34}}, "sql": None, "segundos": 0.1}
        a = self.armar(resultado_=r, defn=DEF_GRUPOS)
        self.assertIn("Rodeo 1: 410 animales", a["texto"])
        self.assertIn("Rodeo 2: 325 animales", a["texto"])
        self.assertIn("735 animales", a["texto"])
        self.assertIn("Días en ordeño (promedio): 172,3", a["texto"])           # el promedio, con coma decimal y diciendo qué es
        self.assertIn("Rodeo 1: 410 animales · Días en ordeño (promedio): 150,3", a["texto"])

    def test_los_textos_no_usan_dias_relativos(self):
        for formato in ("texto", "excel"):
            texto = self.armar(formato, resultado_=resultado(5, truncado=True, verificado=False))["texto"].lower()
            for palabra in ("ayer", "ese día", "esa mañana", "el día anterior"):
                self.assertNotIn(palabra, texto)

    def test_lo_importante_va_antes_que_la_lista_de_filas_porque_telegram_corta_el_pie_a_1024_letras(self):
        a = self.armar("excel", resultado_=resultado(25, truncado=True, verificado=False))
        t = a["texto"]
        self.assertLess(t.index("sin verificar"), t.index("Primeras filas"))
        self.assertLess(t.index("adjunto"), t.index("Primeras filas"))
        self.assertLess(t.index("se cortó"), t.index("Primeras filas"))
        self.assertLess(t.index("Calculado el"), t.index("Primeras filas"))

    def test_el_excel_aclara_en_el_texto_que_va_adjunto(self):
        self.assertIn("adjunto", self.armar("excel")["texto"])
        self.assertNotIn("adjunto", self.armar("texto")["texto"])

    def test_se_ejecuta_con_la_definicion_y_el_tambo_y_sin_fijar_el_dia(self):
        self.armar()
        self.assertEqual(self.consultas, [(DEF, T, None)])

    def test_informe_inexistente_o_con_definicion_rota(self):
        with self.assertRaises(ie.EnvioError):
            ie.armar_de_la_base(T, 999, "texto", ahora=JUEVES, ejecutar=self.ejecutar)


class TestProbar(Base):
    def setUp(self):
        super().setUp()
        self.programar(dias=[3], horas=["08:30"], canales=["telegram", "correo"], formato="excel")

    def probar(self, enviar_de_verdad, canales=None):
        return ie.probar(T, self.informe["id"], self.enviar, canales, enviar_de_verdad, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)

    def test_sin_enviar_solo_arma(self):
        r = self.probar(False)
        self.assertEqual(self.enviados, [])
        self.assertIn("3 animales", r["texto"])
        self.assertEqual(r["enviado"], {})
        self.assertNotIn("datos", r)                                          # los bytes del Excel no vuelven por la API

    def test_enviando_manda_por_los_canales_pedidos(self):
        r = self.probar(True, ["telegram"])
        self.assertEqual([c for c, _ in self.enviados], ["telegram"])
        self.assertEqual(r["enviado"], {"telegram": "ok"})

    def test_sin_canales_pedidos_usa_los_de_la_programacion(self):
        self.probar(True)
        self.assertEqual(sorted(c for c, _ in self.enviados), ["correo", "telegram"])

    def test_no_anota_nada_ni_cuenta(self):
        self.probar(True)
        self.assertEqual(self.registro(), [])
        self.pasada_despues = ie.pasada(self.enviar, lambda: self.disponibles, ahora=datetime.datetime(2026, 8, 27, 8, 31), ejecutar=self.ejecutar)
        self.assertEqual(len(self.enviados), 4)                              # la prueba (2) + el envío programado de verdad (2): la prueba no consumió el horario

    def test_anda_con_la_programacion_pausada_y_aun_sin_programacion(self):
        ie.pausar(T, self.informe["id"])
        self.assertEqual(self.probar(True, ["telegram"])["enviado"], {"telegram": "ok"})
        otro = ig.crear(T, "Sin programar", DEF, "maxi")
        r = ie.probar(T, otro["id"], self.enviar, ["telegram"], True, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertEqual(r["enviado"], {"telegram": "ok"})

    def test_un_canal_que_falla_se_informa_sin_detalles_y_los_otros_salen(self):
        self.falla["telegram"] = RuntimeError("token-secreto-123")
        r = self.probar(True)
        self.assertEqual(r["enviado"]["correo"], "ok")
        self.assertTrue(r["enviado"]["telegram"].startswith("error"))
        self.assertNotIn("secreto", str(r))

    def test_un_canal_que_no_esta_disponible_se_dice(self):
        self.disponibles = ["telegram"]
        r = self.probar(True, ["telegram", "correo"])
        self.assertEqual(r["enviado"]["telegram"], "ok")
        self.assertIn("no está configurado", r["enviado"]["correo"])

    def test_canales_que_no_existen_se_rechazan(self):
        with self.assertRaises(ie.EnvioError):
            self.probar(True, ["sms"])

    def test_no_se_puede_probar_dos_veces_a_la_vez(self):
        entro, sigue = threading.Event(), threading.Event()

        def lento(defn, tambo, hoy=None):
            entro.set()
            sigue.wait(5)
            return resultado()

        hilo = threading.Thread(target=lambda: ie.probar(T, self.informe["id"], self.enviar, None, False, lambda: self.disponibles, ahora=JUEVES, ejecutar=lento))
        hilo.start()
        self.assertTrue(entro.wait(5))
        with self.assertRaises(ie.EnvioError) as cm:
            self.probar(False)
        self.assertIn("ya se está", str(cm.exception))
        sigue.set()
        hilo.join(5)
        self.probar(False)                                                    # liberado: vuelve a andar


class TestHilo(Base):
    def test_el_hilo_sigue_vivo_aunque_una_pasada_falle(self):
        llamadas = []
        parar = threading.Event()

        def pasada_falsa(*a, **k):
            llamadas.append(1)
            if len(llamadas) == 1:
                raise RuntimeError("se cayó la base")
            if len(llamadas) >= 3:
                parar.set()

        with mock.patch.object(ie, "pasada", pasada_falsa):
            hilo = ie.iniciar_hilo(self.enviar, lambda: [], parar=parar, espera_s=0.01, primera_espera_s=0)
            hilo.join(5)
        self.assertFalse(hilo.is_alive())
        self.assertGreaterEqual(len(llamadas), 3)
        self.assertEqual(hilo.name, "envios-informes")
        self.assertTrue(hilo.daemon)

    def test_la_primera_pasada_espera(self):
        llamadas = []
        parar = threading.Event()
        with mock.patch.object(ie, "pasada", lambda *a, **k: llamadas.append(1)):
            hilo = ie.iniciar_hilo(self.enviar, lambda: [], parar=parar, espera_s=0.01, primera_espera_s=30)
            time.sleep(0.2)
            self.assertEqual(llamadas, [])                                    # todavía en la espera inicial
            parar.set()
            hilo.join(5)
        self.assertEqual(llamadas, [])


if __name__ == "__main__":
    unittest.main()
