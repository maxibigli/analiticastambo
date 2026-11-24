"""Lo que encontró la revisión independiente FINAL del envío programado de informes (Fase 4c, 24/11/2026) y se arregló. Cada caso salió de una situación concreta.

Los números de los hallazgos (`envios #N`, `completitud #N`, `seguridad #N`, `regresion #N`) son los de la revisión. Reloj, canales y base de mentira: ninguna prueba manda nada,
llama a una API ni toca una base real.

    C:\\Python314\\python.exe -m unittest tests.test_informes_envios_final -v
"""
import contextlib
import datetime
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import openpyxl

import config_alertas
import envio_red
import informes_envios as ie
import informes_excel
import informes_guardados as ig
import informes_motor as im
import whatsapp
from tests.test_informes_envios import DEF, JUEVES, T, Base, NoSalio, meta, resultado
from tests.test_informes_envios_revision import ConProgramacion, Reloj

OTRO_TAMBO = "san_jose"


# ============================================================================================ el respaldo (envios #4, completitud #3)

class TestElRespaldoIncluyeInformesDb(unittest.TestCase):
    """`informes.db` guarda los informes (también los traídos de DelPro), las programaciones de envío y el registro que evita mandar dos veces lo mismo. Está en `.gitignore`: si
    `backup_config.bat` no la copia, una PC reinstalada o un disco roto la pierde."""

    def archivos(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backup_config.bat")
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                if linea.lower().startswith("set archivos="):
                    return linea.split("=", 1)[1].split()
        self.fail("backup_config.bat no tiene la línea «set ARCHIVOS=»")

    def test_las_tres_partes_de_la_base_van_en_el_respaldo(self):
        archivos = self.archivos()
        for a in ("informes.db", "informes.db-wal", "informes.db-shm"):
            self.assertIn(a, archivos)

    def test_lo_que_ya_se_respaldaba_sigue(self):
        archivos = self.archivos()
        for a in ("usuarios.json", "alertas_canales.json", "checklist.db", "checklist.db-wal", "checklist.db-shm", "Precios_alimentos.xlsx"):
            self.assertIn(a, archivos)

    def test_esta_ignorada_por_git_o_sea_que_no_hay_otro_respaldo(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".gitignore")
        with open(ruta, encoding="utf-8", errors="replace") as f:
            self.assertIn("informes.db", f.read())


# ============================================================================================ horarios (seguridad #4, envios #11)

class TestHorariosExactos(unittest.TestCase):
    """`$` deja pasar un salto de línea final y `\\d` acepta dígitos de otros alfabetos: «08:00\\n» y «0٨:00» entraban como horarios DISTINTOS de «08:00» (se cargaba el mismo
    horario varias veces y se salteaba el tope de 5)."""

    MALAS = ("08:00\n", "08:00\r\n", "\n08:00", "08:00 ", " 08:00", "08:00\x00", "0\u0668:00", "08:0\u0660", "\u0660\u0668:\u0660\u0660", "\uff10\uff18:\uff10\uff10", "8:00", "08:0", "24:00", "08:60", "08-00")

    def ok(self, horas):
        return ie.validar([0, 3], horas, ["telegram"], "texto")

    def test_un_horario_con_basura_se_rechaza(self):
        for mala in self.MALAS:
            with self.assertRaises(ie.EnvioError, msg=repr(mala)):
                self.ok([mala])
            with self.assertRaises(ie.EnvioError, msg=repr(mala)):
                self.ok(["08:00", mala])

    def test_el_mismo_horario_escrito_de_varias_formas_no_pasa_el_tope_ni_se_duplica(self):
        with self.assertRaises(ie.EnvioError):
            self.ok(["08:00", "08:00\n", "0\u0668:00", "08:00 ", "\n08:00"])
        self.assertEqual(self.ok(["08:00", "08:00", "20:00"])["horas"], ["08:00", "20:00"])

    def test_los_horarios_buenos_siguen_andando(self):
        self.assertEqual(self.ok(["23:59", "00:00", "09:05"])["horas"], ["00:00", "09:05", "23:59"])

class TestGuardarNoAceptaHorariosRepetidos(Base):
    def test_el_mismo_horario_con_basura_no_se_guarda_dos_veces(self):
        with self.assertRaises(ie.EnvioError):
            ie.guardar(T, self.informe["id"], [3], ["08:30", "08:30\n"], ["telegram"], "texto", True, "maxi")
        self.assertEqual(ie.listar(T), {})                                              # no se guardó nada


class TestHorariosDeLasAlertas(unittest.TestCase):
    """`config_alertas` tenía el mismo patrón flojo."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(config_alertas, "_RUTA", os.path.join(self.dir.name, "alertas_canales.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_se_rechazan_los_horarios_con_salto_de_linea_o_digitos_de_otro_alfabeto(self):
        for mala in ("08:00\n", "0\u0668:00", "\uff10\uff18:\uff10\uff10", "08:00 "):
            with self.assertRaises(ValueError, msg=repr(mala)):
                config_alertas.set_horario([0], [mala])
        self.assertEqual(config_alertas.horario()["horas"], ["08:00", "20:00"])                 # no se guardó nada

    def test_un_archivo_con_un_horario_asi_cae_al_de_siempre(self):
        with open(config_alertas._RUTA, "w", encoding="utf-8") as f:
            json.dump({"horario": {"dias": [0], "horas": ["09:00\n"]}}, f)
        self.assertEqual(config_alertas.horario(), {"dias": [0], "horas": ["08:00", "20:00"]})            # los días estaban bien; los horarios, no: vuelven a los de siempre

    def test_los_buenos_se_guardan(self):
        config_alertas.set_horario([0, 3], ["07:30", "19:00"])
        self.assertEqual(config_alertas.horario(), {"dias": [0, 3], "horas": ["07:30", "19:00"]})


# ============================================================================================ dos «Guardar» a la vez (envios #11)

class _SinPrevia:
    """El resultado de una consulta que no encontró nada."""

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _ConexionCiega:
    """Una conexión que NO ve la programación que ya existe (la lectura de «¿hay una previa?» da vacío): lo que pasa cuando dos «Guardar» de una programación NUEVA se cruzan
    (los dos leen que no hay, y los dos quieren insertar)."""

    def __init__(self, con):
        self._con = con

    def execute(self, sql, *args):
        if sql.lstrip().startswith("SELECT id FROM envios_programados WHERE informe_id = ? AND tambo = ?"):
            return _SinPrevia()
        return self._con.execute(sql, *args)

    def __getattr__(self, nombre):
        return getattr(self._con, nombre)


class TestDosGuardarALaVez(Base):
    def test_el_segundo_guardar_de_una_programacion_nueva_actualiza_en_vez_de_dar_un_error(self):
        self.programar(horas=["08:30"], canales=["telegram"])
        original = ie._con

        @contextlib.contextmanager
        def ciega():
            with original() as con:
                yield _ConexionCiega(con)

        with mock.patch.object(ie, "_con", ciega):
            e = ie.guardar(T, self.informe["id"], [3], ["07:00"], ["correo"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 13, 0))
        self.assertEqual((e["horas"], e["canales"]), (["07:00"], ["correo"]))                      # quedó lo último que se guardó
        self.assertEqual(len(ie.listar(T)), 1)

    def test_con_hilos_de_verdad_no_hay_errores_y_queda_una_sola_programacion(self):
        errores, barrera = [], threading.Barrier(6)

        def trabajo(k):
            try:
                barrera.wait(5)
                ie.guardar(T, self.informe["id"], [3], [f"0{k}:00"], ["telegram"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
            except Exception as exc:  # noqa: BLE001
                errores.append(repr(exc))

        hilos = [threading.Thread(target=trabajo, args=(k,)) for k in range(6)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(30)
        self.assertEqual(errores, [])
        self.assertEqual(len(ie.listar(T)), 1)


# ============================================================================================ la pausa llega antes del envío (envios #1)

class TestAntesDeMandarSeVuelveAMirar(ConProgramacion):
    def test_pausar_mientras_se_calcula_su_propio_informe_no_manda_nada(self):
        def ejecutar_y_pausar(defn, tambo, hoy=None):
            ie.pausar(T, self.informe["id"], ahora=self.ahora)                      # el usuario toca «Pausar» mientras el informe se calcula
            return resultado()

        r = ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=ejecutar_y_pausar)
        self.assertEqual(self.enviados, [])
        self.assertEqual(sorted((x["canal"], x["estado"]) for x in self.registro()), [("correo", "omitido"), ("telegram", "omitido")])
        self.assertTrue(all("antes de mandarlo" in x["detalle"] for x in self.registro()))
        self.assertEqual(len(r["omitidos"]), 2)
        self.assertEqual(r["enviados"], [])

    def test_cambiar_la_programacion_mientras_sale_el_primer_canal_frena_los_demas(self):
        enviados = []

        def enviar_y_cambiar(canal, armado):
            enviados.append(canal)
            if len(enviados) == 1:
                ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi", ahora=self.ahora)        # «Guardar» con otros canales

        ie.pasada(enviar_y_cambiar, lambda: self.disponibles, ahora=self.ahora, ejecutar=self.ejecutar)
        self.assertEqual(enviados, ["correo"])                                          # el primero ya estaba saliendo; el segundo no
        estados = {x["canal"]: x["estado"] for x in self.registro()}
        self.assertEqual(estados, {"correo": "enviado", "telegram": "omitido"})

    def test_sin_cambios_manda_todo_como_siempre(self):
        self.pasada(self.ahora)
        self.assertEqual(sorted(c for c, _ in self.enviados), ["correo", "telegram"])

    def test_un_reintento_tambien_mira_antes_de_mandar(self):
        self.falla["telegram"] = NoSalio("x")
        self.pasada(self.ahora)                                                          # telegram falla (se reintenta), correo sale
        del self.falla["telegram"]

        def ejecutar_y_pausar(defn, tambo, hoy=None):
            ie.pausar(T, self.informe["id"], ahora=self.ahora + datetime.timedelta(minutes=10))
            return resultado()

        # pausar cierra la promesa del reintento; si igual llegara hasta acá, el control de antes de mandar lo frena
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora + datetime.timedelta(minutes=11), ejecutar=ejecutar_y_pausar)
        self.assertEqual([c for c, _ in self.enviados], ["correo"])

    def test_si_no_se_puede_mirar_la_programacion_no_se_manda_sin_mirar(self):
        with mock.patch.object(ie, "_sigue_igual", side_effect=RuntimeError("base trabada")), self.assertLogs("informes_envios", level="WARNING") as cap:
            self.pasada(self.ahora)
        self.assertEqual(self.enviados, [])
        self.assertNotIn("trabada", "\n".join(cap.output))                                # el texto de la excepción no va al log
        self.assertEqual({x["estado"] for x in self.registro()}, {"en_curso"})
        self.pasada(self.ahora + datetime.timedelta(minutes=35))                          # lo reclamado y no mandado nunca queda «en curso» para siempre
        self.assertEqual({x["estado"] for x in self.registro()}, {"sin_confirmar"})

    def test_si_la_base_no_contesta_al_anotar_un_fallo_igual_se_anota_con_la_promesa_de_siempre(self):
        self.resultado = RuntimeError("timeout")
        with mock.patch.object(ie, "_sigue_igual", side_effect=RuntimeError("base trabada")), self.assertLogs("informes_envios", level="WARNING"):
            self.pasada(self.ahora)
        for x in self.registro():
            self.assertEqual((x["estado"], x["intentos"]), ("fallido", 1))
            self.assertIn(" Se reintenta.", x["detalle"])

    def test_un_fallo_de_calculo_que_llega_despues_de_un_cambio_no_promete_reintentar(self):
        def ejecutar_cambia_y_falla(defn, tambo, hoy=None):
            ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "texto", True, "maxi", ahora=self.ahora)
            raise RuntimeError("timeout de la base")

        ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=ejecutar_cambia_y_falla)
        for x in self.registro():
            self.assertEqual((x["estado"], x["intentos"]), ("fallido", ie.MAX_INTENTOS))
            self.assertNotIn(" Se reintenta.", x["detalle"])
            self.assertIn("Ya no se reintenta: se cambió la programación.", x["detalle"])


# ============================================================================================ lo que no salió deja rastro (envios #2)

class TestUnHorarioPerdidoDejaRastro(ConProgramacion):
    def test_un_horario_que_vencio_con_el_servidor_apagado_queda_como_no_salio(self):
        r = self.pasada(self.ahora + datetime.timedelta(minutes=70))                    # 09:41: la gracia de las 08:30 terminó a las 09:30
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.consultas, [])                                             # ni se calcula
        filas = self.registro()
        self.assertEqual(sorted((x["canal"], x["estado"]) for x in filas), [("correo", "omitido"), ("telegram", "omitido")])
        for x in filas:
            self.assertEqual(x["slot_texto"], "jue 27/08/2026 08:30")
            self.assertTrue(x["detalle"].startswith("No salió: el servidor estaba apagado a esa hora"), x["detalle"])
            self.assertIn("60 minutos", x["detalle"])
        self.assertEqual(r["enviados"], [])

    def test_es_idempotente(self):
        for minutos in (70, 71, 120, 600):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        filas = self.registro()
        self.assertEqual(len(filas), 2)
        self.assertEqual(len({x["detalle"] for x in filas}), 1)

    def test_dentro_de_la_gracia_sigue_saliendo_y_no_se_marca_perdido(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=58))                         # 09:29: todavía dentro de la hora de gracia
        self.assertEqual(sorted(c for c, _ in self.enviados), ["correo", "telegram"])
        self.assertEqual({x["estado"] for x in self.registro()}, {"enviado"})

    def test_justo_en_el_limite_de_la_gracia_todavia_sale(self):
        self.pasada(datetime.datetime(2026, 8, 27, 9, 30))
        self.assertEqual(len(self.enviados), 2)

    def test_lo_que_ya_salio_no_se_pisa(self):
        self.pasada(self.ahora)
        self.pasada(self.ahora + datetime.timedelta(minutes=120))
        self.assertEqual({x["estado"] for x in self.registro()}, {"enviado"})
        self.assertEqual(len(self.registro()), 2)

    def test_lo_que_quedo_en_curso_o_fallido_no_se_pisa(self):
        self.falla["telegram"] = NoSalio("x")
        self.pasada(self.ahora)
        self.pasada(self.ahora + datetime.timedelta(minutes=120))
        estados = {x["canal"]: x["estado"] for x in self.registro()}
        self.assertEqual(estados, {"correo": "enviado", "telegram": "fallido"})

    def test_nada_sale_ni_se_anota_por_un_horario_anterior_a_la_programacion(self):
        self.programar(dias=[3], horas=["08:30"], canales=["telegram", "correo"], ahora=datetime.datetime(2026, 8, 27, 8, 45))             # se guardó 08:45: el de las 08:30 no cuenta
        self.pasada(datetime.datetime(2026, 8, 27, 11, 0))
        self.assertEqual(self.registro(), [])

    def test_una_programacion_pausada_no_deja_rastro_de_lo_que_no_iba_a_mandar(self):
        ie.pausar(T, self.informe["id"], ahora=datetime.datetime(2026, 8, 27, 8, 0))
        self.pasada(datetime.datetime(2026, 8, 27, 11, 0))
        self.assertEqual(self.registro(), [])

    def test_todos_los_horarios_perdidos_de_ayer_y_de_hoy(self):
        self.programar(dias=[2, 3], horas=["08:30", "20:00"], canales=["telegram"], ahora=datetime.datetime(2026, 8, 25, 12, 0))
        self.pasada(datetime.datetime(2026, 8, 27, 10, 0))                                # jueves 10:00: perdió el miércoles 08:30 y 20:00 y el jueves 08:30
        tg = [x for x in self.registro() if x["canal"] == "telegram"]
        self.assertEqual(sorted(x["slot_texto"] for x in tg), ["jue 27/08/2026 08:30", "mié 26/08/2026 08:30", "mié 26/08/2026 20:00"])
        self.assertTrue(all(x["estado"] == "omitido" for x in tg))

    def test_el_texto_no_usa_dias_relativos(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=70))
        for x in self.registro():
            for palabra in ("ayer", "ese día", "el día anterior"):
                self.assertNotIn(palabra, x["detalle"].lower())

    def test_otro_tambo_no_se_mezcla(self):
        inf2 = ig.crear(OTRO_TAMBO, "Otro", DEF, "maxi")
        ie.guardar(OTRO_TAMBO, inf2["id"], [3], ["08:30"], ["correo"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.pasada(self.ahora + datetime.timedelta(minutes=70))
        self.assertEqual(len(ie.registro(OTRO_TAMBO, inf2["id"], 50)), 1)
        self.assertEqual(len(self.registro()), 2)


# ============================================================================================ «Se reintenta.» no se promete en vano (envios #3)

class TestLaPromesaDeReintento(ConProgramacion):
    def setUp(self):
        super().setUp()
        self.falla["telegram"] = NoSalio("x")
        self.pasada(self.ahora)                                                           # telegram falla y promete reintentar; correo sale

    def tg(self):
        return self.estado("telegram")

    def test_antes_de_tocar_nada_se_promete(self):
        self.assertIn("Se reintenta.", self.tg()["detalle"])
        self.assertEqual((self.tg()["estado"], self.tg()["intentos"]), ("fallido", 1))

    def test_guardar_cierra_la_promesa(self):
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "texto", True, "maxi", ahora=self.ahora + datetime.timedelta(minutes=2))
        x = self.tg()
        self.assertNotIn(" Se reintenta.", x["detalle"])
        self.assertIn("Ya no se reintenta: se cambió la programación.", x["detalle"])
        self.assertIn("Telegram", x["detalle"])                                           # lo que falló se sigue sabiendo
        self.assertEqual((x["estado"], x["intentos"]), ("fallido", ie.MAX_INTENTOS))
        del self.falla["telegram"]
        self.pasada(self.ahora + datetime.timedelta(minutes=15))
        self.assertEqual([c for c, _ in self.enviados].count("telegram"), 0)              # y de hecho no se reintentó

    def test_pausar_cierra_la_promesa(self):
        ie.pausar(T, self.informe["id"], ahora=self.ahora + datetime.timedelta(minutes=2))
        x = self.tg()
        self.assertNotIn(" Se reintenta.", x["detalle"])
        self.assertIn("Ya no se reintenta: se pausó la programación.", x["detalle"])
        self.assertEqual(x["intentos"], ie.MAX_INTENTOS)

    def test_pausar_con_guardar_activo_falso_tambien(self):
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "texto", False, "maxi", ahora=self.ahora + datetime.timedelta(minutes=2))
        self.assertIn("Ya no se reintenta: se pausó la programación.", self.tg()["detalle"])

    def test_pasada_la_hora_de_gracia_la_promesa_se_cierra(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=75))
        x = self.tg()
        self.assertNotIn(" Se reintenta.", x["detalle"])
        self.assertIn("Ya no se reintenta: pasó la hora de gracia.", x["detalle"])
        self.assertEqual(x["intentos"], ie.MAX_INTENTOS)

    def test_no_toca_lo_que_todavia_se_va_a_reintentar(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=5))
        self.assertIn(" Se reintenta.", self.tg()["detalle"])
        self.assertEqual(self.tg()["intentos"], 1)

    def test_no_toca_lo_que_ya_estaba_cerrado_ni_a_los_enviados(self):
        self.pasada(self.ahora + datetime.timedelta(minutes=75))
        self.assertEqual(self.estado("correo")["estado"], "enviado")
        self.assertEqual(self.estado("correo")["detalle"], "")
        antes = self.tg()["detalle"]
        self.pasada(self.ahora + datetime.timedelta(minutes=80))
        self.assertEqual(self.tg()["detalle"], antes)                                    # la frase no se agrega dos veces

    def test_guardar_otro_informe_no_cierra_la_promesa_de_este(self):
        otro = ig.crear(T, "Otro informe", DEF, "maxi")
        ie.guardar(T, otro["id"], [3], ["09:00"], ["correo"], "texto", True, "maxi", ahora=self.ahora)
        self.assertIn(" Se reintenta.", self.tg()["detalle"])

    def test_un_fallo_permanente_ya_decia_que_no_se_reintenta_y_sigue_igual(self):
        self.resultado = im.InformeError("El campo «X» no existe en el catálogo.")
        otro = ig.crear(T, "Roto", DEF, "maxi")
        ie.guardar(T, otro["id"], [3], ["08:30"], ["correo"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.pasada(self.ahora + datetime.timedelta(minutes=1))
        fila = [x for x in ie.registro(T, otro["id"], 5)][0]
        self.assertIn("definición", fila["detalle"])
        antes = fila["detalle"]
        ie.pausar(T, otro["id"], ahora=self.ahora + datetime.timedelta(minutes=3))
        self.assertEqual(ie.registro(T, otro["id"], 5)[0]["detalle"], antes)


# ============================================================================================ un canal que se apagó mientras se calculaba (envios #5)

class TestCanalNoDisponible(ConProgramacion):
    def test_es_un_runtime_error_que_sabe_que_no_salio(self):
        e = envio_red.CanalNoDisponible()
        self.assertIsInstance(e, RuntimeError)
        self.assertIs(e.no_salio, True)
        self.assertIs(e.reintentable, False)

    def test_se_anota_como_fallido_y_no_como_sin_confirmar(self):
        estado, detalle, reintentable = ie._fallo_de_canal("telegram", envio_red.CanalNoDisponible())
        self.assertEqual(estado, "fallido")
        self.assertFalse(reintentable)
        self.assertIn("Telegram", detalle)
        self.assertIn("no está configurado o está apagado", detalle)
        self.assertNotIn("confirmar", detalle)

    def test_en_la_pasada(self):
        self.falla["telegram"] = envio_red.CanalNoDisponible()
        r = self.pasada(self.ahora)
        x = self.estado("telegram")
        self.assertEqual((x["estado"], x["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertIn("no está configurado o está apagado", x["detalle"])
        self.assertEqual(r["sin_confirmar"], [])
        self.assertEqual(len(r["fallidos"]), 1)

    def test_un_error_liso_sigue_siendo_incierto(self):
        self.assertEqual(ie._fallo_de_canal("telegram", RuntimeError("canal no disponible"))[0], "sin_confirmar")

    def test_en_probar_se_dice_sin_decir_que_no_se_sabe(self):
        self.programar(dias=[3], horas=["08:30"], canales=["telegram"], formato="texto")
        self.falla["telegram"] = envio_red.CanalNoDisponible()
        r = ie.probar(T, self.informe["id"], self.enviar, ["telegram"], True, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertIn("no está configurado o está apagado", r["enviado"]["telegram"])
        self.assertNotIn("revisalo", r["enviado"]["telegram"])


# ============================================================================================ los fallos conocidos de WhatsApp (completitud #2)

class TestFrasesFijasDeWhatsApp(unittest.TestCase):
    def error(self, motivo, mensaje="Twilio encoló el mensaje... Código 63016: texto de Twilio con +5493415551234 y token-secreto-123"):
        return whatsapp.WhatsappError(mensaje, no_salio=True, motivo=motivo)

    def test_fuera_de_la_ventana_de_24_horas(self):
        estado, detalle, reintentable = ie._fallo_de_canal("whatsapp", self.error("ventana_24h"))
        self.assertEqual((estado, reintentable), ("fallido", False))
        self.assertIn("WhatsApp no entregó el mensaje", detalle)
        self.assertIn("24 horas", detalle)

    def test_el_sandbox_sin_unir(self):
        estado, detalle, _ = ie._fallo_de_canal("whatsapp", self.error("sandbox"))
        self.assertEqual(estado, "fallido")
        self.assertIn("WhatsApp no entregó el mensaje", detalle)
        self.assertIn("sandbox", detalle)

    def test_cualquier_otro_no_entregado(self):
        estado, detalle, _ = ie._fallo_de_canal("whatsapp", self.error("no_entregado"))
        self.assertEqual(estado, "fallido")
        self.assertIn("WhatsApp no entregó el mensaje", detalle)
        self.assertNotIn("revisá su configuración", detalle)                           # no era un problema de configuración

    def test_nunca_se_guarda_el_texto_de_la_excepcion(self):
        for motivo in ("ventana_24h", "sandbox", "no_entregado"):
            detalle = ie._fallo_de_canal("whatsapp", self.error(motivo))[1]
            for secreto in ("5493415551234", "token-secreto", "Twilio con", "63016"):
                self.assertNotIn(secreto, detalle)

    def test_un_rechazo_sin_motivo_sigue_diciendo_que_revise_la_configuracion(self):
        estado, detalle, _ = ie._fallo_de_canal("whatsapp", whatsapp.WhatsappError("Twilio respondió 401", no_salio=True))
        self.assertEqual(estado, "fallido")
        self.assertIn("revisá su configuración", detalle)

    def test_un_motivo_desconocido_no_se_copia(self):
        detalle = ie._fallo_de_canal("whatsapp", whatsapp.WhatsappError("x", no_salio=True, motivo="texto libre con +5493415551234"))[1]
        self.assertNotIn("5493415551234", detalle)



class TestWhatsAppEnLaPasada(ConProgramacion):
    def test_el_registro_dice_que_paso_y_no_guarda_el_texto_de_twilio(self):
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["whatsapp"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.falla["whatsapp"] = whatsapp.WhatsappError("Código 63016: texto de Twilio con +5493415551234", no_salio=True, motivo="ventana_24h")
        r = self.pasada(self.ahora)
        x = self.estado("whatsapp")
        self.assertEqual((x["estado"], x["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertIn("24 horas", x["detalle"])
        self.assertNotIn("5493415551234", x["detalle"])
        self.assertNotIn("revisá su configuración", x["detalle"])
        self.assertEqual(len(r["fallidos"]), 1)


# ============================================================================================ «Ver cómo queda» / «Enviar ahora» (envios #6 y contrato)

class TestProbarDiceQueRecibeCadaCanal(ConProgramacion):
    def setUp(self):
        super().setUp()
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "excel", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.resultado = resultado(25)

    def probar(self, enviar_de_verdad=False, canales=None, **kw):
        return ie.probar(T, self.informe["id"], self.enviar, canales, enviar_de_verdad, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar, **kw)

    def test_devuelve_el_texto_de_cada_canal(self):
        r = self.probar()
        self.assertEqual(set(r["textos"]), {"correo", "telegram", "whatsapp"})
        self.assertEqual(r["textos"]["correo"], r["texto"])                              # `texto` sigue siendo el del correo
        self.assertIn("adjunto", r["textos"]["correo"])
        self.assertIn("adjunto", r["textos"]["telegram"])
        self.assertNotIn("adjunto", r["textos"]["whatsapp"])                              # WhatsApp no recibe el archivo
        self.assertNotIn("Excel", r["textos"]["whatsapp"])

    def test_es_el_mismo_texto_que_recibe_cada_canal(self):
        self.probar(True, ["correo", "telegram", "whatsapp"])
        mandados = dict(self.enviados)
        r = self.probar()
        for canal in ("correo", "telegram", "whatsapp"):
            self.assertEqual(r["textos"][canal], ie.texto_para(mandados[canal], canal), canal)

    def test_el_texto_de_telegram_respeta_el_pie_de_1024_letras(self):
        self.resultado = {"columnas": [meta(f"C{i}", "Un nombre de columna bastante largo " + str(i), "txt") for i in range(12)], "filas": [["x" * 80] * 12 for _ in range(30)],
                          "claves_filas": None, "truncado": False, "detalle": True, "agrupar": [], "grupos": [], "totales": None, "sql": None, "segundos": 0.1}
        r = self.probar()
        self.assertLessEqual(len(r["textos"]["telegram"]), 1024)

    def test_en_formato_texto_los_tres_son_iguales(self):
        r = self.probar(formato="texto")
        self.assertEqual(len({r["textos"]["correo"], r["textos"]["telegram"], r["textos"]["whatsapp"]}), 1)

    def test_sigue_sin_traer_los_bytes_del_excel(self):
        self.assertNotIn("datos", self.probar())

    def test_texto_para_cada_canal_con_y_sin_archivo(self):
        a = {"texto": "T", "texto_telegram": "TG", "texto_sin_adjunto": "SA", "datos": b"PK"}
        self.assertEqual([ie.texto_para(a, c) for c in ("correo", "telegram", "whatsapp")], ["T", "TG", "SA"])
        sin = dict(a, datos=None)
        self.assertEqual([ie.texto_para(sin, c) for c in ("correo", "telegram", "whatsapp")], ["SA", "SA", "SA"])
        viejo = {"texto": "T", "datos": None}
        self.assertEqual([ie.texto_para(viejo, c) for c in ("correo", "telegram", "whatsapp")], ["T", "T", "T"])


class TestProbarConUnFormatoElegido(ConProgramacion):
    """Contrato con la pantalla: «Enviar ahora» y «Ver cómo queda» mandan el formato que está en el formulario (todavía sin guardar), no el guardado."""

    def setUp(self):
        super().setUp()
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "excel", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))

    def probar(self, formato=None, informe_id=None, **kw):
        return ie.probar(T, informe_id or self.informe["id"], self.enviar, ["telegram"], True, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar, formato=formato, **kw)

    def test_sin_formato_usa_el_guardado(self):
        self.probar()
        self.assertTrue(self.enviados[-1][1]["datos"])

    def test_texto_sobre_una_programacion_excel_no_lleva_archivo(self):
        r = self.probar("texto")
        self.assertIsNone(self.enviados[-1][1]["datos"])
        self.assertEqual(r["bytes"], 0)
        self.assertNotIn("adjunto", r["textos"]["correo"])

    def test_excel_sobre_una_programacion_en_texto_lleva_archivo(self):
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 13, 0))
        r = self.probar("excel")
        self.assertTrue(self.enviados[-1][1]["datos"])
        self.assertGreater(r["bytes"], 0)

    def test_sin_programacion_tambien_se_puede_elegir(self):
        otro = ig.crear(T, "Sin programar", DEF, "maxi")
        r = self.probar("texto", informe_id=otro["id"])
        self.assertEqual(r["bytes"], 0)

    def test_un_formato_que_no_existe_se_rechaza_sin_mandar_nada(self):
        for malo in ("pdf", "EXCEL", "", 3, ["excel"], True):
            with self.assertRaises(ie.EnvioError, msg=repr(malo)):
                self.probar(malo)
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.consultas, [])

    def test_no_cambia_lo_guardado(self):
        self.probar("texto")
        self.assertEqual(ie.obtener(T, self.informe["id"])["formato"], "excel")


# ============================================================================================ el recorte no se come lo fijo (envios #7)

def res_con_totales(n_cols=12, verificado=True, nombres_largos=True, truncado=False, limite_pedido=False, filas=5):
    cols = [meta("Number", "Número del animal", "txt")]
    for i in range(n_cols):
        cols.append(meta(f"X{i}", (f"Un campo con un nombre bastante largo número {i}" if nombres_largos else f"Campo {i}"), decimales=1, resumen="avg", verificado=verificado))
    tot = {"n": 2600, "resumenes": {f"c{i}": 123456.789 + i for i in range(1, n_cols + 1)}}
    return {"columnas": cols, "filas": [[str(3000 + k)] + [1.5] * n_cols for k in range(filas)], "claves_filas": None, "truncado": truncado, "limite_pedido": limite_pedido,
            "detalle": True, "agrupar": [], "grupos": [], "totales": tot, "sql": None, "segundos": 0.1}


class TestElRecorteNoSeComeLoFijo(unittest.TestCase):
    def texto(self, r, con_adjunto=True, limite=1024):
        return ie.texto_resumen("Vacas por días en ordeño", JUEVES, r, con_adjunto, limite)[0]

    def test_con_un_total_larguisimo_el_aviso_y_el_adjunto_siguen(self):
        t = self.texto(res_con_totales(n_cols=12, verificado=False))
        self.assertLessEqual(len(t), 1024)
        for fijo in ("Calculado el", "Total:", "Ojo: incluye campos sin verificar", "va adjunto"):
            self.assertIn(fijo, t, fijo)
        self.assertTrue(t.rstrip().endswith("va adjunto."), t[-80:])                      # lo último de lo fijo no se corta

    def test_la_linea_del_total_es_la_que_se_acorta_y_lo_dice(self):
        t = self.texto(res_con_totales(n_cols=12, verificado=False))
        total = [linea for linea in t.split("\n") if linea.startswith("Total:")][0]
        self.assertTrue(total.endswith("…"), total)

    def test_en_los_tres_limites(self):
        for limite in (1024, ie.TEXTO_MAX):
            for adjunto in (True, False):
                t = self.texto(res_con_totales(n_cols=12, verificado=False), adjunto, limite)
                self.assertLessEqual(len(t), limite)
                self.assertIn("Ojo: incluye campos sin verificar", t)
                self.assertIn("Total:", t)
                if adjunto:
                    self.assertIn("va adjunto", t)

    def test_muchos_campos_sin_verificar_acortan_la_lista_de_nombres_y_no_el_aviso(self):
        r = res_con_totales(n_cols=30, verificado=False)
        t = self.texto(r)
        self.assertLessEqual(len(t), 1024)
        ojo = [linea for linea in t.split("\n") if linea.startswith("Ojo:")][0]
        self.assertIn("Ojo: incluye campos sin verificar", ojo)
        self.assertRegex(ojo, r"y \d+ más")
        self.assertIn("va adjunto", t)
        self.assertIn("Total:", t)

    def test_si_todo_entra_no_se_toca_nada(self):
        r = res_con_totales(n_cols=2, verificado=False, nombres_largos=False)
        t = self.texto(r, True, ie.TEXTO_MAX)
        self.assertNotIn("…", t.split("Primeras filas")[0])
        self.assertNotRegex(t, r"y \d+ más\):")
        self.assertIn("Ojo: incluye campos sin verificar contra DelPro (Campo 0, Campo 1): los valores pueden diferir.", t)

    def test_la_lista_es_lo_que_cede_primero(self):
        t = self.texto(res_con_totales(n_cols=2, nombres_largos=False, filas=10), True, 400)
        self.assertLessEqual(len(t), 400)
        self.assertIn("Total:", t)
        self.assertIn("va adjunto", t)

    def test_un_limite_imposible_corta_con_puntos_suspensivos_como_antes(self):
        r = {"columnas": [meta("Number", "Número del animal", "txt")], "filas": [["1"]], "truncado": False, "detalle": True, "agrupar": [], "grupos": [], "totales": None}
        texto, _, _ = ie.texto_resumen("Un informe con un nombre larguísimo " * 5, JUEVES, r, True, limite=80)
        self.assertEqual(len(texto), 80)
        self.assertTrue(texto.endswith("…"))


# ============================================================================================ cómo se escribe cada cosa (envios #8, completitud #5)

def res_agrupado(tipo, claves, decimales=0, resumen="none", valor_grupo=None):
    cols = [meta("G", "Grupo", tipo, decimales), meta("X", "Producción", "num", 1, resumen=resumen)]
    grupos = [{"nivel": 1, "claves": [k], "n": 3 + i, "resumenes": ({"c1": valor_grupo} if valor_grupo is not None else {})} for i, k in enumerate(claves)]
    tot = {"n": 788, "resumenes": ({"c1": 788.0 if resumen == "count" else 41.25} if resumen != "none" else {})}
    return {"columnas": cols, "filas": [["x", 1.0]], "claves_filas": [[claves[0]]], "truncado": False, "detalle": True,
            "agrupar": [{"id": "G", "alias": "g0", "etiqueta": "Grupo", "tipo": tipo, "decimales": decimales}], "grupos": grupos, "totales": tot, "sql": None, "segundos": 0.1}


class TestComoSeEscribeCadaCosa(unittest.TestCase):
    def texto(self, r):
        return ie.texto_resumen("Un informe", JUEVES, r, False)[0]

    def test_la_clave_de_un_grupo_de_fecha_lleva_el_dia_de_la_semana(self):
        for clave in ("2026-08-01T00:00:00", "2026-08-01", datetime.datetime(2026, 8, 1), datetime.date(2026, 8, 1)):
            t = self.texto(res_agrupado("fecha", [clave]))
            self.assertIn("- sáb 01/08/2026: 3 animales", t, repr(clave))
            self.assertNotIn("T00:00", t)
            self.assertNotIn("2026-08-01", t)

    def test_una_clave_de_fecha_que_no_es_fecha_se_escribe_tal_cual(self):
        self.assertIn("- no es fecha: 3 animales", self.texto(res_agrupado("fecha", ["no es fecha"])))

    def test_una_clave_numerica_entera_no_lleva_decimales(self):
        for clave in (3.0, 3):
            t = self.texto(res_agrupado("num", [clave]))
            self.assertIn("- 3: 3 animales", t, repr(clave))
            self.assertNotIn("3.0", t)

    def test_una_clave_numerica_con_decimales_lleva_coma(self):
        self.assertIn("- 2,5: 3 animales", self.texto(res_agrupado("num", [2.5], decimales=1)))

    def test_sin_dato_y_texto_como_siempre(self):
        t = self.texto(res_agrupado("txt", [None, "Rodeo 1"]))
        self.assertIn("- (sin dato): 3 animales", t)
        self.assertIn("- Rodeo 1: 4 animales", t)

    def test_el_conteo_se_escribe_entero_aunque_la_columna_tenga_decimales(self):
        t = self.texto(res_agrupado("txt", ["Rodeo 1"], resumen="count", valor_grupo=380.0))
        self.assertIn("Producción (conteo): 380", t)
        self.assertIn("Total: Producción (conteo): 788", t)
        self.assertNotIn("380,0", t)
        self.assertNotIn("788,0", t)

    def test_el_promedio_sigue_con_un_decimal_y_la_suma_con_los_de_la_columna(self):
        t = self.texto(res_agrupado("txt", ["Rodeo 1"], resumen="avg", valor_grupo=30.55))
        self.assertIn("Producción (promedio): 30,6", t)                                    # 30.55 -> 30,6 (mitad hacia arriba, como la pantalla)
        self.assertIn("Total: Producción (promedio): 41,3", t)                             # 41.25 -> 41,3


class TestElRedondeoEsElDeLaPantalla(unittest.TestCase):
    """La pantalla (`toLocaleString('es-AR')`, ICU) redondea la mitad HACIA ARRIBA sobre el decimal más corto; el texto usaba el valor binario y el par: 80,25 daba «80,2»."""

    def test_los_empates(self):
        self.assertEqual(ie._n(80.25, 1), "80,3")
        self.assertEqual(ie._n(2.5, 0), "3")
        self.assertEqual(ie._n(0.125, 2), "0,13")
        self.assertEqual(ie._n(1234.5, 0), "1.235")
        self.assertEqual(ie._n(1.005, 2), "1,01")
        self.assertEqual(ie._n(0.5, 0), "1")
        self.assertEqual(ie._n(1.5, 0), "2")

    def test_los_negativos_se_alejan_del_cero_como_en_la_pantalla(self):
        self.assertEqual(ie._n(-2.5, 0), "-3")
        self.assertEqual(ie._n(-80.25, 1), "-80,3")

    def test_lo_que_no_es_un_empate_no_cambia(self):
        self.assertEqual(ie._n(1234.5, 1), "1.234,5")
        self.assertEqual(ie._n(80.24, 1), "80,2")
        self.assertEqual(ie._n(80.26, 1), "80,3")
        self.assertEqual(ie._n(7, 1), "7,0")
        self.assertEqual(ie._n(1234567, 0), "1.234.567")
        self.assertEqual(ie._n(0, 1), "0,0")
        self.assertEqual(ie._n(3.0, 0), "3")

    def test_numeros_enormes_o_raros_no_rompen(self):
        self.assertEqual(ie._n(10 ** 30, 0), "1.000.000.000.000.000.000.000.000.000.000")
        self.assertIsInstance(ie._n(1e300, 1), str)
        for raro in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(ie._n(raro, 1), "—")

    def test_lo_que_no_es_un_numero_sigue_igual(self):
        self.assertEqual(ie._n(None), "—")
        self.assertEqual(ie._n(True), "True")
        self.assertEqual(ie._n("texto"), "texto")

    def test_en_el_texto_del_mensaje(self):
        cols = [meta("Number", "Número del animal", "txt"), meta("P", "Producción", "num", 1, resumen="avg")]
        r = {"columnas": cols, "filas": [["1", 80.25]], "claves_filas": None, "truncado": False, "detalle": True, "agrupar": [], "grupos": [],
             "totales": {"n": 1, "resumenes": {"c1": 80.25}}, "sql": None, "segundos": 0.1}
        t = ie.texto_resumen("Un informe", JUEVES, r, False)[0]
        self.assertIn("Producción (promedio): 80,3", t)


# ============================================================================================ el límite que eligió el informe (regresion #2)

class TestElLimiteDelInformeEnElMensaje(unittest.TestCase):
    def texto(self, **kw):
        r = res_con_totales(n_cols=1, nombres_largos=False, truncado=True, filas=5, **kw)
        return ie.texto_resumen("Un informe", JUEVES, r, True)[0]

    def test_un_corte_pedido_no_dice_que_se_corto_en_el_tope(self):
        t = self.texto(limite_pedido=True)
        self.assertIn("(se listan las primeras 5 filas, como pide el informe)", t)
        self.assertNotIn("se cortó", t)
        self.assertIn("El Excel va adjunto.", t)
        self.assertNotIn("completo", t)

    def test_un_corte_por_el_tope_sigue_diciendolo(self):
        for kw in ({}, {"limite_pedido": False}):
            t = self.texto(**kw)
            self.assertIn("(la lista se cortó en 5 filas)", t)
            self.assertIn("El Excel completo va adjunto.", t)

    def test_sin_corte_no_dice_nada_del_limite(self):
        r = res_con_totales(n_cols=1, nombres_largos=False, truncado=False, limite_pedido=True, filas=5)
        t = ie.texto_resumen("Un informe", JUEVES, r, True)[0]
        self.assertNotIn("se listan", t)
        self.assertNotIn("se cortó", t)
        self.assertIn("El Excel completo va adjunto.", t)

    def test_una_sola_fila_va_en_singular(self):
        r = res_con_totales(n_cols=1, nombres_largos=False, truncado=True, limite_pedido=True, filas=1)
        self.assertIn("(se lista la primera fila, como pide el informe)", ie.texto_resumen("Un informe", JUEVES, r, True)[0])



class TestElLimiteDelInformeAlArmar(Base):
    def test_armar_lo_lleva_al_texto_al_excel_y_a_las_condiciones(self):
        defn = dict(DEF, limite=10)
        r = dict(resultado(10, truncado=True), limite_pedido=True)
        a = ie.armar(T, dict(self.informe, definicion=defn), "excel", ahora=JUEVES, ejecutar=lambda d, t, hoy=None: r)
        self.assertIn("se listan las primeras 10 filas, como pide el informe", a["texto"])
        self.assertIn("El Excel va adjunto.", a["texto"])
        wb = openpyxl.load_workbook(io.BytesIO(a["datos"]))
        textos = [str(c.value) for fila in wb["Condiciones"].iter_rows() for c in fila if c.value is not None]
        self.assertIn("Lista limitada a las primeras 10 filas", textos)
        self.assertTrue(any("Se listan las primeras 10 filas, como pide el informe" in t for t in textos))
        self.assertFalse(any("tope" in t for t in textos))


# ============================================================================================ el Excel del envío (envios #9 y #10)

class TestElExcelDelEnvio(Base):
    def armar(self, defn=None, formato="excel", **kw):
        informe = dict(self.informe, definicion=defn or DEF)
        return ie.armar(T, informe, formato, ahora=JUEVES, ejecutar=lambda d, t, hoy=None: self.resultado, **kw)

    def textos(self, a):
        wb = openpyxl.load_workbook(io.BytesIO(a["datos"]))
        return [str(c.value) for fila in wb["Condiciones"].iter_rows() for c in fila if c.value is not None]

    def test_generado_y_las_fechas_de_las_condiciones_llevan_el_dia(self):
        defn = dict(DEF, condiciones=[{"campo": "LastCalvingDate", "op": ">=", "valor": "2026-08-01"}])
        textos = self.textos(self.armar(defn))
        self.assertIn("jue 27/08/2026 09:00", textos)                                       # Generado
        self.assertIn("Fecha del último parto ≥ sáb 01/08/2026", textos)

    def test_un_fallo_al_armar_el_excel_es_permanente_y_no_lleva_el_texto_de_la_excepcion(self):
        with mock.patch.object(informes_excel, "a_xlsx", side_effect=ValueError("detalle-secreto de openpyxl")):
            with self.assertRaises(ie.ExcelError) as cm:
                self.armar()
        self.assertIsInstance(cm.exception, ie.EnvioError)
        self.assertIn("No se pudo armar el Excel", str(cm.exception))
        self.assertNotIn("secreto", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)

    def test_en_formato_texto_no_se_arma_ningun_excel(self):
        with mock.patch.object(informes_excel, "a_xlsx", side_effect=ValueError("no debería llamarse")):
            self.assertIsNone(self.armar(formato="texto")["datos"])

    def test_una_fecha_con_zona_horaria_en_los_datos_no_rompe_el_armado(self):
        cols = [meta("Number", "Número del animal", "txt"), meta("LastCalvingDate", "Fecha del último parto", "fecha")]
        zona = datetime.timezone.utc
        self.resultado = {"columnas": cols, "filas": [["3000", datetime.datetime(2026, 8, 5, 14, 30, tzinfo=zona)], ["3001", "2026-08-01T00:00:00+00:00"]],
                          "claves_filas": None, "truncado": False, "detalle": True, "agrupar": [], "grupos": [], "totales": None, "sql": None, "segundos": 0.1}
        wb = openpyxl.load_workbook(io.BytesIO(self.armar()["datos"]))
        self.assertEqual(wb["Informe"]["B2"].value, datetime.datetime(2026, 8, 5, 14, 30))

    def test_un_nombre_con_caracteres_que_excel_no_admite_sale_limpio(self):
        informe = dict(self.informe, nombre="Informe\uffff raro", definicion=DEF)
        a = ie.armar(T, informe, "excel", ahora=JUEVES, ejecutar=lambda d, t, hoy=None: self.resultado)
        self.assertIn("Informe raro", self.textos(a))


class TestUnFalloDelExcelEnLaPasada(ConProgramacion):
    def setUp(self):
        super().setUp()
        ie.guardar(T, self.informe["id"], [3], ["08:30"], ["telegram", "correo"], "excel", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))

    def test_no_se_reintenta_ni_se_recalcula_tres_veces(self):
        with mock.patch.object(informes_excel, "a_xlsx", side_effect=ValueError("detalle-secreto")):
            self.pasada(self.ahora)
            self.pasada(self.ahora + datetime.timedelta(minutes=11))
            self.pasada(self.ahora + datetime.timedelta(minutes=22))
        self.assertEqual(self.enviados, [])
        self.assertEqual(len(self.consultas), 1)
        for x in self.registro():
            self.assertEqual((x["estado"], x["intentos"]), ("fallido", ie.MAX_INTENTOS))
            self.assertIn("No se pudo armar el Excel", x["detalle"])
            self.assertNotIn("Se reintenta", x["detalle"])
            self.assertNotIn("secreto", x["detalle"])
            self.assertNotIn("ya no existe", x["detalle"])

    def test_un_informe_que_no_existe_sigue_diciendo_que_no_existe(self):
        with mock.patch.object(ie, "armar_de_la_base", side_effect=ie.EnvioError("Ese informe no existe.")):
            self.pasada(self.ahora)
        self.assertIn("ya no existe", self.registro()[0]["detalle"])

    def test_probar_dice_que_no_se_pudo_armar_el_excel(self):
        with mock.patch.object(informes_excel, "a_xlsx", side_effect=ValueError("detalle-secreto")):
            with self.assertRaises(ie.ExcelError) as cm:
                ie.probar(T, self.informe["id"], self.enviar, ["telegram"], False, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertNotIn("secreto", str(cm.exception))


# ============================================================================================ el latido (envios #12)

class TestElLatidoSeEscribeDuranteLaPasada(Base):
    def setUp(self):
        super().setUp()
        self.programar(dias=[3], horas=["08:30"], canales=["correo"])
        self.informe2 = ig.crear(T, "Otro informe", DEF, "maxi")
        ie.guardar(T, self.informe2["id"], [3], ["08:30"], ["correo"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 26, 12, 0))
        self.reloj = Reloj(datetime.datetime(2026, 8, 27, 8, 31))

    def test_la_pantalla_ve_el_latido_mientras_el_hilo_calcula(self):
        vistos = []

        def ejecutar(defn, tambo, hoy=None):
            vistos.append(ie.ultima_pasada())
            self.reloj.pasar(minutes=5)                                                  # una pasada larga: la base tarda
            return resultado()

        self.assertIsNone(ie.ultima_pasada())
        ie.pasada(self.enviar, lambda: ["correo"], ahora=self.reloj, ejecutar=ejecutar)
        self.assertEqual(len(vistos), 2)
        self.assertEqual(vistos[0], datetime.datetime(2026, 8, 27, 8, 31))                # al empezar
        self.assertEqual(vistos[1], datetime.datetime(2026, 8, 27, 8, 36))                # y otra vez antes de atender la segunda programación
        self.assertEqual(ie.ultima_pasada(), datetime.datetime(2026, 8, 27, 8, 41))       # y al terminar, como siempre

    def test_una_pasada_sin_nada_vencido_igual_deja_el_latido(self):
        ie.pasada(self.enviar, lambda: ["correo"], ahora=datetime.datetime(2026, 8, 27, 7, 0), ejecutar=lambda *a, **k: resultado())
        self.assertEqual(ie.ultima_pasada(), datetime.datetime(2026, 8, 27, 7, 0))

    def test_si_no_se_puede_escribir_el_latido_la_pasada_sigue(self):
        with mock.patch.object(ie, "_latido", side_effect=RuntimeError("base trabada")), self.assertLogs("informes_envios", level="WARNING"):
            ie.pasada(self.enviar, lambda: ["correo"], ahora=datetime.datetime(2026, 8, 27, 8, 31), ejecutar=lambda *a, **k: resultado())
        self.assertEqual(len(self.enviados), 2)


if __name__ == "__main__":
    unittest.main()
