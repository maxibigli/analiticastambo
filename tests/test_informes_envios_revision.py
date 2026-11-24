"""Lo que encontró la revisión independiente del envío programado de informes (Fase 4c, la noche del 24/11/2026; casi todo se reprodujo) y se arregló.

Cada caso salió de una situación concreta: un canal que acepta el mensaje y después falla (se reintentaba y salía repetido), una programación que se pausa
mientras la pasada trabaja, un texto que promete un adjunto que no va. Reloj, canales y base de mentira: ninguna prueba manda nada.

    C:\\Python314\\python.exe -m unittest tests.test_informes_envios_revision -v
"""
import datetime
import unittest
from unittest import mock

import db
import informes_envios as ie
import informes_guardados as ig
import informes_motor as im
from tests.test_informes_envios import DEF, DEF_GRUPOS, JUEVES, T, Base, NoSalio, Rechazo, meta, resultado


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def pasar(self, **kw):
        self.t += datetime.timedelta(**kw)


class ConProgramacion(Base):
    def setUp(self):
        super().setUp()
        self.programar(dias=[3], horas=["08:30"], canales=["telegram", "correo"])
        self.ahora = datetime.datetime(2026, 8, 27, 8, 31)

    def estado(self, canal, informe=None):
        return [x for x in ie.registro(T, (informe or self.informe)["id"], 50) if x["canal"] == canal][0]

    def mandos(self, canal):
        return [c for c, _ in self.enviados].count(canal)


class TestUnFalloDelCanalNoDuplica(ConProgramacion):
    def test_un_fallo_incierto_queda_sin_confirmar_y_no_se_reenvia(self):
        """El canal pudo haber aceptado el mensaje antes de fallar: reintentarlo lo repetía hasta 3 veces por horario."""
        llamadas = []

        def enviar(canal, armado):
            llamadas.append(canal)
            if canal == "telegram":
                raise RuntimeError("Telegram respondió 500: token-secreto-123")
        for minutos in (0, 11, 22, 33):
            ie.pasada(enviar, lambda: self.disponibles, ahora=self.ahora + datetime.timedelta(minutes=minutos), ejecutar=self.ejecutar)
        self.assertEqual(llamadas.count("telegram"), 1)
        tg = self.estado("telegram")
        self.assertEqual(tg["estado"], "sin_confirmar")
        self.assertIn("Telegram", tg["detalle"])
        self.assertIn("revisalo", tg["detalle"])
        self.assertNotIn("token-secreto", tg["detalle"])
        self.assertEqual(self.estado("correo")["estado"], "enviado")                   # el otro canal no se afecta

    def test_lo_que_se_sabe_que_no_salio_se_reintenta_y_sale_una_sola_vez(self):
        self.falla["telegram"] = NoSalio("sin internet")
        self.pasada(self.ahora)
        self.assertEqual((self.estado("telegram")["estado"], self.estado("telegram")["intentos"]), ("fallido", 1))
        del self.falla["telegram"]
        self.pasada(self.ahora + datetime.timedelta(minutes=11))
        self.pasada(self.ahora + datetime.timedelta(minutes=22))
        self.assertEqual(self.estado("telegram")["estado"], "enviado")
        self.assertEqual(self.mandos("telegram"), 1)

    def test_un_rechazo_no_se_reintenta(self):
        self.falla["telegram"] = Rechazo("401")
        for minutos in (0, 11, 22):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        fila = self.estado("telegram")
        self.assertEqual((fila["estado"], fila["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertNotIn("Se reintenta.", fila["detalle"])
        self.assertIn("configuración", fila["detalle"].lower())

    def test_el_ultimo_intento_no_promete_otro(self):
        self.falla["telegram"] = NoSalio("sin internet")
        for minutos in (0, 11, 22, 33):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        fila = self.estado("telegram")
        self.assertEqual((fila["estado"], fila["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertNotRegex(fila["detalle"], r"(?<!no )[Ss]e reintenta")
        self.assertIn("Ya no se reintenta", fila["detalle"])
        self.assertRegex(self.estado("correo")["detalle"] or "", r"^$")                 # lo que salió no lleva detalle

    def test_los_dos_primeros_intentos_si_dicen_que_se_reintenta(self):
        self.falla["telegram"] = NoSalio("sin internet")
        self.pasada(self.ahora)
        self.assertIn("Se reintenta", self.estado("telegram")["detalle"])

    def test_un_fallo_al_anotar_un_canal_no_deja_sin_mandar_a_los_demas(self):
        """La base local trabada al anotar el primero dejaba a los otros reclamados para siempre (nunca salían)."""
        original = ie._cerrar
        fallas = []

        def cerrar(con, envio_id, slot, canal, estado, detalle, ahora, intentos=None):
            if canal == "correo" and not fallas:
                fallas.append(1)
                raise RuntimeError("database is locked")
            return original(con, envio_id, slot, canal, estado, detalle, ahora, intentos)
        with mock.patch.object(ie, "_cerrar", cerrar):
            self.pasada(self.ahora)
        self.assertEqual(sorted(c for c, _ in self.enviados), ["correo", "telegram"])      # los dos salieron, aunque anotar uno haya fallado


class TestLaPasadaNoTrabajaConUnaFotoVieja(Base):
    def setUp(self):
        super().setUp()
        self.informe2 = ig.crear(T, "Otro informe", DEF, "maxi")
        self.informe3 = ig.crear(T, "Un tercero", DEF, "maxi")
        ayer = datetime.datetime(2026, 8, 26, 12, 0)
        for inf, canales in ((self.informe, ["telegram"]), (self.informe2, ["telegram", "correo"]), (self.informe3, ["telegram", "correo"])):
            ie.guardar(T, inf["id"], [3], ["08:30"], canales, "texto", True, "maxi", ahora=ayer)
        self.ahora = datetime.datetime(2026, 8, 27, 8, 31)

    def test_pausar_mientras_la_pasada_calcula_otro_informe_frena_el_envio(self):
        hecho = []

        def ejecutar(defn, tambo, hoy=None):
            if not hecho:                                                    # mientras se calcula el primero, el usuario pausa el segundo
                hecho.append(1)
                ie.pausar(T, self.informe2["id"], ahora=datetime.datetime(2026, 8, 27, 8, 31))
            return resultado()
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=ejecutar)
        self.assertEqual(ie.registro(T, self.informe2["id"], 50), [])           # no se reclamó ni se mandó nada del pausado
        self.assertEqual(len(ie.registro(T, self.informe["id"], 50)), 1)

    def test_cambiar_los_canales_mientras_tanto_tampoco_manda_con_los_viejos(self):
        hecho = []

        def ejecutar(defn, tambo, hoy=None):
            if not hecho:
                hecho.append(1)
                ie.guardar(T, self.informe3["id"], [3], ["08:30"], ["telegram"], "texto", True, "maxi", ahora=datetime.datetime(2026, 8, 27, 8, 31))
            return resultado()
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=ejecutar)
        self.assertNotIn(self.informe3["nombre"], [a["texto"] for _, a in self.enviados if self.informe3["nombre"] in a["texto"]])
        self.assertEqual(ie.registro(T, self.informe3["id"], 50), [])

    def test_sin_cambios_manda_a_todos(self):
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=self.ahora, ejecutar=self.ejecutar)
        self.assertEqual(len(self.enviados), 1 + 2 + 2)


class TestLaHoraDeCadaDecision(ConProgramacion):
    """La pasada usaba UNA hora (la del comienzo) para todo: si calcular tardaba, el registro, el «Calculado el» y la espera del reintento quedaban atrasados."""

    def test_el_texto_dice_cuando_se_termino_de_calcular_y_el_registro_cuando_salio(self):
        reloj = Reloj(self.ahora)

        def ejecutar(defn, tambo, hoy=None):
            reloj.pasar(minutes=6)                                           # una consulta lenta (la base ocupada con el arranque)
            return resultado()
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=reloj, ejecutar=ejecutar)
        self.assertIn("a las 08:37", self.enviados[0][1]["texto"])
        fila = self.estado("telegram")
        self.assertEqual((fila["inicio"], fila["fin"]), ("2026-08-27T08:31:00", "2026-08-27T08:37:00"))

    def test_el_reintento_espera_diez_minutos_desde_el_fallo_de_verdad(self):
        reloj = Reloj(self.ahora)
        self.falla["telegram"] = NoSalio("sin internet")

        def ejecutar(defn, tambo, hoy=None):
            reloj.pasar(minutes=6)
            return resultado()
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=reloj, ejecutar=ejecutar)           # falla a las 08:37
        self.assertEqual(self.estado("telegram")["fin"], "2026-08-27T08:37:00")
        del self.falla["telegram"]
        reloj.t = datetime.datetime(2026, 8, 27, 8, 44)                      # 13 minutos después de empezar, pero solo 7 desde el fallo
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=reloj, ejecutar=self.ejecutar)
        self.assertEqual(self.mandos("telegram"), 0)
        reloj.t = datetime.datetime(2026, 8, 27, 8, 48)
        ie.pasada(self.enviar, lambda: self.disponibles, ahora=reloj, ejecutar=self.ejecutar)
        self.assertEqual(self.mandos("telegram"), 1)

    def test_una_hora_fija_sigue_andando(self):
        self.pasada(self.ahora)
        self.assertEqual(len(self.enviados), 2)
        self.assertEqual(ie.ultima_pasada(), self.ahora)


class TestFallosQueSeRepetirianIgual(ConProgramacion):
    def test_una_tabla_que_no_existe_no_se_recalcula_tres_veces(self):
        self.resultado = db.TablaNoDisponibleError("Invalid object name 'BcsDailyData'")
        for minutos in (0, 11, 22, 33):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        self.assertEqual(len(self.consultas), 1)
        fila = self.estado("telegram")
        self.assertEqual((fila["estado"], fila["intentos"]), ("fallido", ie.MAX_INTENTOS))
        self.assertNotIn("base no contestó", fila["detalle"])
        self.assertNotIn("Se reintenta", fila["detalle"])
        self.assertNotIn("BcsDailyData", fila["detalle"])

    def test_un_error_de_base_pasajero_se_reintenta_y_en_el_ultimo_intento_no_lo_promete(self):
        self.resultado = RuntimeError("timeout")
        for minutos in (0, 11, 22, 33):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        self.assertEqual(len(self.consultas), 3)
        self.assertIn("Ya no se reintenta", self.estado("telegram")["detalle"])


class TestElTextoDeCadaCanal(Base):
    def armar(self, formato="excel", resultado_=None, defn=None):
        if resultado_ is not None:
            self.resultado = resultado_
        informe = dict(self.informe, definicion=defn or DEF)
        return ie.armar(T, informe, formato, ahora=JUEVES, ejecutar=self.ejecutar)

    def test_el_texto_sin_adjunto_no_promete_un_adjunto(self):
        """WhatsApp (Twilio) recibe solo texto: decirle «el Excel va adjunto» a quien no lo recibe era falso."""
        a = self.armar("excel", resultado_=resultado(25))
        self.assertIn("adjunto", a["texto"])
        self.assertIn("están en el Excel", a["texto"])
        self.assertNotIn("adjunto", a["texto_sin_adjunto"])
        self.assertNotIn("Excel", a["texto_sin_adjunto"])
        self.assertIn("25 animales", a["texto_sin_adjunto"])

    def test_en_formato_texto_los_tres_son_el_mismo(self):
        a = self.armar("texto")
        self.assertEqual(a["texto"], a["texto_sin_adjunto"])
        self.assertEqual(a["texto"], a["texto_telegram"])

    def test_el_pie_de_telegram_con_archivo_entra_en_1024_letras(self):
        grupos = [{"nivel": 1, "claves": [f"Rodeo {i}"], "n": 300 + i, "resumenes": {"c1": 30.5 + i, "c2": 200.1 + i}} for i in range(1, 9)]
        r = {"columnas": [meta("AnimalGroupName", "Nombre del grupo", "txt"), meta("SevenDayAverage", "Promedio producción diaria de leche 7 d.", decimales=1, resumen="avg"),
                          meta("CurrentDIM", "Días en ordeño", decimales=1, resumen="avg", verificado=False)],
             "filas": [["Rodeo 1", 38, 200]], "claves_filas": [["Rodeo 1"]], "truncado": False, "detalle": True,
             "agrupar": [{"id": "AnimalGroupName", "alias": "g0", "etiqueta": "Nombre del grupo", "tipo": "txt", "decimales": 0}], "grupos": grupos,
             "totales": {"n": 2600, "resumenes": {"c1": 36.2, "c2": 205.9}}, "sql": None, "segundos": 0.1}
        a = self.armar("excel", resultado_=r, defn=DEF_GRUPOS)
        self.assertLessEqual(len(a["texto_telegram"]), 1024)
        self.assertLessEqual(len(a["texto"]), ie.TEXTO_MAX)
        for fijo in ("Calculado el", "Total:", "Ojo: incluye campos sin verificar", "El Excel completo va adjunto"):
            self.assertIn(fijo, a["texto_telegram"], fijo)                       # lo importante NO se pierde: lo que se recorta es la lista de rodeos
            self.assertIn(fijo, a["texto"], fijo)
        self.assertIn("Rodeo 1", a["texto_telegram"])
        self.assertRegex(a["texto_telegram"], r"- y \d+ más")                    # y lo que no entró se dice

    def test_lo_fijo_sobrevive_aunque_haya_muchas_columnas_con_resumen(self):
        cols = [meta("AnimalGroupName", "Nombre del grupo", "txt")] + [meta(f"X{i}", f"Un campo con nombre largo número {i}", decimales=1, resumen="avg") for i in range(5)]
        grupos = [{"nivel": 1, "claves": [f"Rodeo {i}"], "n": 300, "resumenes": {f"c{k}": 30.5 for k in range(1, 6)}} for i in range(1, 9)]
        r = {"columnas": cols, "filas": [["Rodeo 1"] + [1] * 5], "claves_filas": [["Rodeo 1"]], "truncado": False, "detalle": True,
             "agrupar": [{"id": "AnimalGroupName", "alias": "g0", "etiqueta": "Nombre del grupo", "tipo": "txt", "decimales": 0}], "grupos": grupos,
             "totales": {"n": 2400, "resumenes": {f"c{k}": 30.5 for k in range(1, 6)}}, "sql": None, "segundos": 0.1}
        a = self.armar("excel", resultado_=r, defn=DEF_GRUPOS)
        for texto in (a["texto"], a["texto_telegram"], a["texto_sin_adjunto"]):
            self.assertLessEqual(len(texto), ie.TEXTO_MAX)
            self.assertIn("Total:", texto)

    def test_el_resumen_de_una_fecha_se_escribe_como_las_filas(self):
        cols = [meta("Number", "Número del animal", "txt"), meta("LastCalvingDate", "Fecha del último parto", "fecha", resumen="min")]
        r = {"columnas": cols, "filas": [["3000", "2026-08-05T14:30:00"], ["3001", "2026-08-01"]], "claves_filas": None, "truncado": False, "detalle": True,
             "agrupar": [], "grupos": [], "totales": {"n": 2, "resumenes": {"c1": "2026-08-01T14:30:00"}}, "sql": None, "segundos": 0.1}
        a = self.armar("texto", resultado_=r)
        self.assertIn("Fecha del último parto (mínimo): 01/08/2026", a["texto"])
        self.assertNotIn("2026-08-01T", a["texto"])

    def test_un_informe_sin_grupos_ni_resumenes_sigue_igual(self):
        a = self.armar("excel", resultado_=resultado(25))
        self.assertIn("Primeras filas", a["texto"])
        self.assertIn("... y 15 filas más (están en el Excel)", a["texto"])
        self.assertIn("... y 15 filas más", a["texto_sin_adjunto"])
        self.assertNotIn("Excel", a["texto_sin_adjunto"])


class TestProbarSinCanales(ConProgramacion):
    def test_mandar_de_verdad_sin_ningun_canal_se_rechaza(self):
        with self.assertRaises(ie.EnvioError) as cm:
            ie.probar(T, self.informe["id"], self.enviar, [], True, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertIn("canal", str(cm.exception))
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.consultas, [])                                  # ni se calcula

    def test_ver_como_queda_sin_canales_si_anda(self):
        r = ie.probar(T, self.informe["id"], self.enviar, [], False, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertEqual(r["enviado"], {})

    def test_un_fallo_incierto_se_dice_distinto_de_uno_seguro(self):
        self.falla["telegram"] = RuntimeError("raro")
        self.falla["correo"] = Rechazo("rechazado")
        r = ie.probar(T, self.informe["id"], self.enviar, ["telegram", "correo"], True, lambda: self.disponibles, ahora=JUEVES, ejecutar=self.ejecutar)
        self.assertIn("revisalo", r["enviado"]["telegram"])
        self.assertNotIn("revisalo", r["enviado"]["correo"])
        self.assertTrue(all(v.startswith("error: ") for v in r["enviado"].values()))


class TestElInformeNoCompila(ConProgramacion):
    def test_sigue_sin_reintentarse(self):
        self.resultado = im.InformeError("El campo «X» no existe en el catálogo.")
        for minutos in (0, 11):
            self.pasada(self.ahora + datetime.timedelta(minutes=minutos))
        self.assertEqual(len(self.consultas), 1)


if __name__ == "__main__":
    unittest.main()
