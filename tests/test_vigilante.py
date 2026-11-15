"""Vigilante, Tarea 5: la pasada (detecta, busca la causa, avisa) y sus bordes.

Todo con lecturas falsas inyectadas y un `Estado` temporal: no toca DelPro, ni la IA, ni `vigilante.db` real.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_vigilante.py" -v
"""
import datetime
import tempfile
import threading
import os
import unittest
from unittest import mock

import vigilante as v
import vigilante_aviso as va
import vigilante_estado as ve
import vigilante_reglas as vr

D = datetime.date(2026, 11, 14)                        # sábado
AHORA = datetime.datetime(2026, 11, 14, 9, 30)
BASE_KG = [1000, 1010, 990, 1005, 995, 1002, 998]      # 7 días normales del ordeño 1
CFG = dict(ve.DEFECTOS, activo=True)


def ordeno(dias_atras, ses, kg, vacas=1000, ini=(5, 0), horas=2.0, dia=None):
    base = dia or D
    ini_dt = datetime.datetime.combine(base - datetime.timedelta(days=dias_atras), datetime.time(*ini))
    return vr.armar_ordeno(ses, vacas, kg, ini_dt, ini_dt + datetime.timedelta(hours=horas))


def historial(ses=1, ini=(5, 0), horas=2.0, kgs=BASE_KG, dia=None):
    return [ordeno(i + 1, ses, kg, ini=ini, horas=horas, dia=dia) for i, kg in enumerate(kgs)]


def det(h, a, con_aviso_demora=False, cliente=None):
    return va.texto_deterministico(h, a, con_aviso_demora)


SIN_COMIDA = lambda tambo, hoy: None                  # noqa: E731
SIN_VACAS = lambda tambo, d0, d1: []                  # noqa: E731
CAUSAS = lambda tambo, o, hist: ["Rodeo 3: faltaron 24 vacas (680 kg)"]   # noqa: E731


class Base(unittest.TestCase):
    def setUp(self):
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        self.ruta = os.path.join(self.carpeta.name, "v.db")
        self.estado = ve.Estado(self.ruta)
        self.enviados = []
        v.olvidar_memo()

    def pasar(self, ordenos, ahora=AHORA, cfg=None, registrar=True, enviar="auto", causas=CAUSAS,
              comida=SIN_COMIDA, vacas=SIN_VACAS, estado=None):
        enviar = self.enviados.append if enviar == "auto" else enviar
        return v.pasada("ponderosa", ahora=ahora, registrar=registrar, enviar=enviar,
                        estado=estado or self.estado, leer=lambda t, d0, d1: list(ordenos), causas=causas,
                        cfg=cfg or CFG, comida=comida, vacas=vacas, redactar=det)


class TestPasada(Base):
    def test_un_ordeno_malo_manda_un_solo_mensaje_y_no_se_repite_en_la_pasada_siguiente(self):
        ords = historial() + [ordeno(0, 1, 920)]
        r1 = self.pasar(ords)
        self.assertEqual(r1["estado"], "ok")
        self.assertTrue(r1["enviado"])
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("Rodeo 3: faltaron 24 vacas", self.enviados[0])
        self.assertEqual([h["clave"] for h in r1["hallazgos"]], ["2026-11-14|1|baja_ordeno"])
        r2 = self.pasar(ords, ahora=AHORA + datetime.timedelta(minutes=15))
        self.assertEqual(r2["hallazgos"], [])
        self.assertFalse(r2["enviado"])
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(self.estado.avisos_del_dia(D), 1)

    def test_el_dedup_sobrevive_a_un_reinicio(self):
        ords = historial() + [ordeno(0, 1, 920)]
        self.pasar(ords)
        r = self.pasar(ords, estado=ve.Estado(self.ruta))      # «reinicio»: otra instancia sobre el mismo archivo
        self.assertEqual(r["hallazgos"], [])
        self.assertEqual(len(self.enviados), 1)

    def test_dos_hallazgos_en_la_misma_pasada_van_en_un_mensaje(self):
        ords = (historial(1) + historial(2, ini=(13, 0)) + [ordeno(0, 1, 920), ordeno(0, 2, 915, ini=(13, 0))])
        ahora = datetime.datetime(2026, 11, 14, 16, 30)
        r = self.pasar(ords, ahora=ahora)
        self.assertEqual(len(r["hallazgos"]), 2)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual(self.enviados[0].count("Vigilante"), 1)
        self.assertEqual(self.estado.avisos_del_dia(D), 1)            # un mensaje, un aviso para el tope

    def test_la_comida_atrasada_se_avisa_una_vez_por_dia(self):
        comida = lambda tambo, hoy: {"fecha": "2026-11-14", "primera": 525, "habitual": 450, "retraso": 75}  # noqa
        ords = historial() + [ordeno(0, 1, 1000)]
        r1 = self.pasar(ords, comida=comida)
        self.assertEqual([h["tipo"] for h in r1["hallazgos"]], ["comida_atrasada"])
        self.assertEqual(r1["hallazgos"][0]["clave"], "2026-11-14|comida")
        r2 = self.pasar(ords, comida=comida, ahora=AHORA + datetime.timedelta(minutes=15))
        self.assertEqual(r2["hallazgos"], [])

    def test_base_con_menos_de_tres_dias_no_avisa_ni_inventa(self):
        ords = historial(kgs=[1000, 1010]) + [ordeno(0, 1, 500)]
        r = self.pasar(ords)
        self.assertEqual(r["hallazgos"], [])
        self.assertIsNone(r["mensaje"])
        self.assertEqual(self.enviados, [])

    def test_un_dia_incompleto_de_la_base_se_descarta(self):
        hist = historial()
        hist[2] = ordeno(3, 1, 300, vacas=400)                       # día cortado: 40 % de las vacas
        base = v.base_de(ordeno(0, 1, 920), hist + [ordeno(0, 1, 920)])
        self.assertEqual(len(base), 6)
        self.assertNotIn(300, [o.kg for o in base])

    def test_base_de_usa_solo_el_mismo_ordeno_y_los_siete_dias_anteriores(self):
        hist = historial(1) + historial(2, ini=(13, 0)) + [ordeno(8, 1, 111), ordeno(0, 1, 920)]
        base = v.base_de(ordeno(0, 1, 920), hist)
        self.assertEqual(sorted(o.kg for o in base), sorted(BASE_KG))

    def test_un_ordeno_que_todavia_no_termino_no_se_evalua(self):
        ords = historial() + [ordeno(0, 1, 400, horas=4.4)]          # termina 09:24, ahora 09:30: solo 6 min quieto
        r = self.pasar(ords)
        self.assertEqual(r["hallazgos"], [])

    def test_ordeno_que_cruza_medianoche_no_se_duplica_al_cambiar_la_fecha(self):
        sin_silencio = dict(CFG, silencio_desde="00:00", silencio_hasta="00:00")
        hist = historial(3, ini=(23, 30), horas=1.8)
        malo = vr.armar_ordeno(3, 1000, 880, datetime.datetime(2026, 11, 14, 23, 30),
                               datetime.datetime(2026, 11, 15, 1, 20))
        ords = hist + [malo]
        r1 = self.pasar(ords, ahora=datetime.datetime(2026, 11, 15, 2, 30), cfg=sin_silencio)
        self.assertEqual([h["clave"] for h in r1["hallazgos"]], ["2026-11-14|3|baja_ordeno"])
        r2 = self.pasar(ords, ahora=datetime.datetime(2026, 11, 15, 5, 0), cfg=sin_silencio)
        self.assertEqual(r2["hallazgos"], [])
        self.assertEqual(len(self.enviados), 1)


class TestVacasFaltantes(Base):
    @staticmethod
    def vacas(hoy_presentes):
        filas = []
        for i in range(1, 8):
            f = (D - datetime.timedelta(days=i)).isoformat()
            filas += [(f, 1, n, 21, 10.0) for n in range(1, 101)]
        filas += [(D.isoformat(), 1, n, 21, 10.0) for n in hoy_presentes]
        return lambda tambo, d0, d1: filas

    def test_vacas_habituales_que_no_pasaron_disparan_el_aviso(self):
        ords = historial() + [ordeno(0, 1, 985, vacas=98)]            # 1,5 % menos: no alcanza para «baja»
        r = self.pasar(ords, vacas=self.vacas(range(3, 101)))         # faltan la 1 y la 2 (20 kg = 2 %)
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["vacas_faltantes"])
        d = r["hallazgos"][0]["datos"]
        self.assertEqual((d["faltan"], d["sesion"], d["fecha"]), (2, 1, "2026-11-14"))
        self.assertEqual(r["hallazgos"][0]["clave"], "2026-11-14|1|vacas_faltantes")

    def test_si_el_ordeno_no_bajo_ni_el_1_por_ciento_no_se_consulta_por_vaca(self):
        llamadas = []
        vacas = lambda t, a, b: llamadas.append(1) or []              # noqa: E731
        self.pasar(historial() + [ordeno(0, 1, 1003)], vacas=vacas)
        self.assertEqual(llamadas, [])

    def test_sin_las_vacas_del_ordeno_en_la_lectura_no_se_inventan_faltantes(self):
        ords = historial() + [ordeno(0, 1, 985, vacas=98)]
        r = self.pasar(ords, vacas=self.vacas([]))                    # la lectura por vaca vino vacía
        self.assertEqual(r["hallazgos"], [])

    def test_una_lectura_por_vaca_que_falla_no_corta_la_pasada(self):
        def rota(t, a, b):
            raise RuntimeError("base caida")
        ords = historial() + [ordeno(0, 1, 920)]                      # baja clara: igual avisa
        r = self.pasar(ords, vacas=rota)
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["baja_ordeno"])


class TestSinDatos(Base):
    def test_sin_datos_avisa_una_vez_y_los_datos_vuelven_avisa_una_vez(self):
        ords = historial()                                            # hoy todavía ni una fila
        for minutos in (0, 15, 30):                                   # 09:30, 09:45, 10:00
            self.pasar(ords, ahora=AHORA + datetime.timedelta(minutes=minutos))
        self.assertEqual(len(self.enviados), 1)
        self.assertIn("Sin datos", self.enviados[0])
        self.assertEqual(self.estado.estado_datos(), "sin_datos")
        ords = ords + [ordeno(0, 1, 1000, ini=(9, 55), horas=0.25)]   # vuelve a entrar leche
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 10, 30))
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["datos_vuelven"])
        self.assertEqual(len(self.enviados), 2)
        self.assertIn("Volvieron", self.enviados[1])
        self.assertEqual(self.estado.estado_datos(), "ok")
        self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 10, 45))
        self.assertEqual(len(self.enviados), 2)

    def test_sin_datos_no_cuenta_para_el_tope_diario(self):
        self.pasar(historial())
        self.assertEqual(self.estado.avisos_del_dia(D), 0)

    def test_el_hueco_normal_entre_dos_ordenos_no_es_sin_datos(self):
        # ordeño 1 de 05:00 a 07:00 y ordeño 2 que arranca a las 13:00: a las 11:00 no entra nada hace 4 h
        # y es lo normal.
        ords = historial(1) + historial(2, ini=(13, 0)) + [ordeno(0, 1, 1000)]
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 11, 0))
        self.assertEqual(r["hallazgos"], [])

    def test_sin_datos_no_analiza_produccion(self):
        # ordeño 1 de hoy cortado a los 30 min (200 kg: «baja» aparente) y sin filas desde las 05:30
        ords = historial() + [ordeno(0, 1, 200, vacas=200, horas=0.5)]
        r = self.pasar(ords)
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["sin_datos"])
        self.assertNotIn("Baja en el ordeño", self.enviados[0])


class TestSilencioYTope(Base):
    def test_en_silencio_no_registra_y_avisa_despues_de_las_05(self):
        ords = historial() + [ordeno(0, 1, 920, ini=(0, 10), horas=1.0)]
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 2, 0))
        self.assertEqual(r["estado"], "silencio")
        self.assertFalse(r["enviado"])
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.estado.avisos_del_dia(D), 0)
        self.assertFalse(self.estado.ya_avisado("2026-11-14|1|baja_ordeno"))
        r2 = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 5, 10))
        self.assertTrue(r2["enviado"])
        self.assertEqual(len(self.enviados), 1)

    def test_tope_alcanzado_retiene_en_vez_de_mandar(self):
        for _ in range(3):
            self.estado.registrar_envio(D)
        ords = historial() + [ordeno(0, 1, 920)]
        r = self.pasar(ords)
        self.assertEqual(r["retenidos"], 1)
        self.assertFalse(r["enviado"])
        self.assertEqual(self.enviados, [])
        lineas = self.estado.tomar_retenidos()
        self.assertEqual(len(lineas), 1)
        self.assertIn("ordeño 1", lineas[0])
        self.assertTrue(self.estado.ya_avisado("2026-11-14|1|baja_ordeno"))     # no se vuelve a retener
        r2 = self.pasar(ords, ahora=AHORA + datetime.timedelta(minutes=15))
        self.assertEqual(r2["retenidos"], 0)

    def test_con_el_tope_alcanzado_sin_datos_igual_se_avisa(self):
        for _ in range(3):
            self.estado.registrar_envio(D)
        r = self.pasar(historial())
        self.assertTrue(r["enviado"])
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["sin_datos"])


class TestProbarYApagado(Base):
    def test_probar_no_gasta_nada(self):
        ords = historial() + [ordeno(0, 1, 920)]
        for _ in range(3):
            self.estado.registrar_envio(D)
        antes = self.estado.avisos_del_dia(D)
        r1 = self.pasar(ords, registrar=False, enviar=None)
        r2 = self.pasar(ords, registrar=False, enviar=None)
        self.assertEqual(r1["mensaje"], r2["mensaje"])
        self.assertFalse(r1["enviado"])
        self.assertFalse(self.estado.ya_avisado("2026-11-14|1|baja_ordeno"))
        self.assertEqual(self.estado.avisos_del_dia(D), antes)
        self.assertEqual(self.estado.retenidos(), [])
        self.assertEqual(self.estado.estado_datos(), "ok")
        self.assertIsNone(self.estado.ultima_pasada())

    def test_probar_funciona_con_el_vigilante_apagado(self):
        ords = historial() + [ordeno(0, 1, 920)]
        r = self.pasar(ords, registrar=False, enviar=None, cfg=dict(CFG, activo=False))
        self.assertEqual(len(r["hallazgos"]), 1)
        self.assertIsNotNone(r["mensaje"])

    def test_apagado_no_hace_nada(self):
        ords = historial() + [ordeno(0, 1, 920)]
        leidas = []
        r = v.pasada("ponderosa", ahora=AHORA, registrar=True, enviar=self.enviados.append, estado=self.estado,
                     leer=lambda *a: leidas.append(1) or ords, causas=CAUSAS, cfg=dict(CFG, activo=False),
                     comida=SIN_COMIDA, vacas=SIN_VACAS, redactar=det)
        self.assertEqual(r["estado"], "apagado")
        self.assertEqual((leidas, self.enviados, r["hallazgos"]), ([], [], []))


class TestFuentesCaidas(Base):
    def test_una_fuente_caida_no_corta_las_demas(self):
        def rota(t, o, h):
            raise RuntimeError("DelPro no responde")
        ords = historial() + [ordeno(0, 1, 920)]
        r = self.pasar(ords, causas=rota)
        self.assertTrue(r["enviado"])
        self.assertIn("No se pudo consultar la causa", self.enviados[0])

    def test_si_la_lectura_de_ordenos_falla_la_pasada_lo_dice_y_no_levanta(self):
        def rota(t, d0, d1):
            raise RuntimeError("sin conexion")
        r = v.pasada("ponderosa", ahora=AHORA, registrar=True, enviar=self.enviados.append, estado=self.estado,
                     leer=rota, causas=CAUSAS, cfg=CFG, comida=SIN_COMIDA, vacas=SIN_VACAS, redactar=det)
        self.assertEqual(r["estado"], "error")
        self.assertEqual(self.enviados, [])
        self.assertEqual(self.estado.estado_datos(), "ok")            # un error de lectura no es «sin datos»

    def test_si_el_envio_falla_no_se_registra_nada_y_se_reintenta(self):
        def falla(texto):
            raise RuntimeError("canal caido")
        ords = historial() + [ordeno(0, 1, 920)]
        r = self.pasar(ords, enviar=falla)
        self.assertEqual(r["estado"], "error")
        self.assertFalse(self.estado.ya_avisado("2026-11-14|1|baja_ordeno"))
        self.assertEqual(self.estado.avisos_del_dia(D), 0)
        r2 = self.pasar(ords, ahora=AHORA + datetime.timedelta(minutes=15))
        self.assertTrue(r2["enviado"])

    def test_la_ia_falla_y_el_aviso_sale_igual(self):
        ords = historial() + [ordeno(0, 1, 920)]
        cliente = mock.Mock()
        cliente.messages.create.side_effect = RuntimeError("sin credito")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}):
            r = v.pasada("ponderosa", ahora=AHORA, registrar=True, enviar=self.enviados.append,
                         estado=self.estado, leer=lambda *a: list(ords), causas=CAUSAS, cfg=CFG,
                         comida=SIN_COMIDA, vacas=SIN_VACAS,
                         redactar=lambda h, a, con_aviso_demora=False: va.redactar(h, a, con_aviso_demora,
                                                                                    cliente=cliente))
        self.assertTrue(r["enviado"])
        self.assertIn("Baja en el ordeño 1", self.enviados[0])


class TestSql(unittest.TestCase):
    def test_las_consultas_nuevas_son_solo_lectura_y_filtran_por_rebano(self):
        import db
        for sql in (v.sql_vacas_por_ordeno("2026-11-06", "2026-11-14", "ponderosa"),
                    v.sql_bocas_por_ordeno("2026-11-06", "2026-11-14", "ponderosa")):
            db.validate_sql(sql)
            self.assertIn("AnimalGroup g_dia", sql)          # el filtro de rebaño: la base la comparten tres tambos
            self.assertIn("MAXDOP 1", sql)


class TestLeerOrdenos(unittest.TestCase):
    def test_un_ordeno_partido_por_el_corte_de_fecha_se_junta_y_cuenta_para_el_dia_que_empezo(self):
        f = datetime.datetime
        filas = [(datetime.date(2026, 11, 14), 3, 700, 800.0, f(2026, 11, 14, 23, 30), f(2026, 11, 14, 23, 59), 1, 1),
                 (datetime.date(2026, 11, 15), 3, 300, 350.0, f(2026, 11, 15, 0, 0), f(2026, 11, 15, 1, 20), 1, 1)]
        with mock.patch.object(v.diagnostico_baja, "corre", return_value=filas):
            ords = v.leer_ordenos("ponderosa", datetime.date(2026, 11, 6), datetime.date(2026, 11, 15))
        self.assertEqual(len(ords), 2)          # (14/11, 3) y (15/11, 3): el 15 empieza a las 00:00
        self.assertEqual(sorted((o.fecha, o.vacas) for o in ords), [("2026-11-14", 700), ("2026-11-15", 300)])

    def test_dos_filas_del_mismo_ordeno_se_juntan(self):
        f = datetime.datetime
        filas = [(datetime.date(2026, 11, 14), 1, 600, 700.0, f(2026, 11, 14, 5, 0), f(2026, 11, 14, 6, 0), 1, 1),
                 (datetime.date(2026, 11, 15), 1, 400, 300.0, f(2026, 11, 14, 5, 40), f(2026, 11, 14, 6, 50), 1, 1)]
        with mock.patch.object(v.diagnostico_baja, "corre", return_value=filas):
            (o,) = v.leer_ordenos("ponderosa", datetime.date(2026, 11, 6), datetime.date(2026, 11, 15))
        self.assertEqual((o.vacas, o.kg, o.inicio.hour, o.fin.hour, o.fecha), (1000, 1000.0, 5, 6, "2026-11-14"))
        self.assertEqual(o.fin, f(2026, 11, 14, 6, 50))

    def test_si_la_consulta_falla_levanta_con_un_mensaje(self):
        with mock.patch.object(v.diagnostico_baja, "corre", return_value=None):
            with self.assertRaises(v.LecturaError):
                v.leer_ordenos("ponderosa", datetime.date(2026, 11, 6), datetime.date(2026, 11, 15))


class TestRevisionFinal(Base):
    """Arreglos de la revisión final: ordeño cortado por la conexión, sin datos persistente, consulta pesada una vez."""

    def test_un_ordeno_cortado_por_la_conexion_no_es_una_baja_hasta_pasado_un_rato(self):
        corte = historial() + [ordeno(0, 1, 300, vacas=400, ini=(7, 40), horas=1.0)]        # el enlace cae a las 08:40
        r = self.pasar(corte)                                                                # 09:30: pasaron 50 min
        self.assertEqual(r["hallazgos"], [])
        self.assertFalse(self.estado.ya_avisado("2026-11-14|1|baja_ordeno"))                 # la clave NO se gastó
        completo = historial() + [ordeno(0, 1, 1000, vacas=1000, ini=(7, 40), horas=2.0)]    # llegan las filas que faltaban
        r2 = self.pasar(completo, ahora=datetime.datetime(2026, 11, 14, 10, 30))
        self.assertEqual(r2["hallazgos"], [])                                                # el ordeño completo es normal

    def test_un_ordeno_con_pocas_vacas_que_sigue_asi_se_avisa_despues_de_la_espera(self):
        corte = historial() + [ordeno(0, 1, 300, vacas=400, ini=(7, 40), horas=1.0)]
        r = self.pasar(corte, ahora=datetime.datetime(2026, 11, 14, 10, 50))                 # 130 min despues del corte
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["baja_ordeno"])

    def test_un_corte_de_noche_se_avisa_al_terminar_el_silencio(self):
        ords = (historial(1, ini=(0, 10), horas=4.5) + historial(2, ini=(8, 0)) + historial(3, ini=(16, 0), horas=4.5))
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 3, 0))                    # silencio
        self.assertEqual((r["estado"], self.enviados), ("silencio", []))
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 5, 10))                   # termina el silencio
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["sin_datos"])                 # no a las 11 de la manana
        self.assertEqual(len(self.enviados), 1)

    def test_un_ordeno_que_casi_nunca_se_hace_no_cuenta_para_sin_datos(self):
        ords = historial(1) + [ordeno(3, 4, 500, ini=(8, 0))] + [ordeno(0, 1, 1000)]         # el 4 apareció 1 dia de 7
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 14, 0))
        self.assertEqual(r["hallazgos"], [])

    def test_con_la_pasada_cada_dos_horas_el_sin_datos_igual_se_avisa(self):
        # la regla vieja solo disparaba en la ultima hora de la ventana: una pasada cada 120 min podia saltearla
        ords = historial(1)
        r = self.pasar(ords, ahora=datetime.datetime(2026, 11, 14, 12, 0), cfg=dict(CFG, pasada_min=120))
        self.assertEqual([h["tipo"] for h in r["hallazgos"]], ["sin_datos"])

    def test_un_ordeno_ya_avisado_no_vuelve_a_consultar_por_vaca(self):
        llamadas = []
        base = TestVacasFaltantes.vacas(range(3, 101))
        vacas = lambda t, a, b: llamadas.append(1) or base(t, a, b)                          # noqa: E731
        ords = historial() + [ordeno(0, 1, 985, vacas=98)]
        self.pasar(ords, vacas=vacas)
        self.assertEqual(len(llamadas), 1)
        self.pasar(ords, vacas=vacas, ahora=AHORA + datetime.timedelta(minutes=15))
        self.assertEqual(len(llamadas), 1)

    def test_en_silencio_no_se_busca_la_causa_ni_se_llama_a_la_ia(self):
        causas = mock.Mock(return_value=[])
        redactar = mock.Mock(return_value="x")
        ords = historial() + [ordeno(0, 1, 920, ini=(0, 10), horas=1.0)]
        r = v.pasada("ponderosa", ahora=datetime.datetime(2026, 11, 14, 2, 0), registrar=True,
                     enviar=self.enviados.append, estado=self.estado, leer=lambda *a: list(ords), causas=causas,
                     cfg=CFG, comida=SIN_COMIDA, vacas=SIN_VACAS, redactar=redactar)
        self.assertEqual(r["estado"], "silencio")
        causas.assert_not_called()
        redactar.assert_not_called()


class TestMemo(unittest.TestCase):
    def setUp(self):
        v.olvidar_memo()

    def test_el_memo_dura_mas_que_una_pasada_y_purga_lo_vencido(self):
        llamadas, reloj = [], [0.0]
        with mock.patch.object(v.time, "monotonic", side_effect=lambda: reloj[0]), \
                mock.patch.object(v, "_corre", side_effect=lambda sql, t: llamadas.append(1) or []):
            v.leer_vacas("ponderosa", "2026-11-01", "2026-11-14")
            reloj[0] = 15 * 60                                                               # la pasada siguiente
            v.leer_vacas("ponderosa", "2026-11-01", "2026-11-14")
            self.assertEqual(len(llamadas), 1)
            reloj[0] = 10 * 3600
            v.leer_vacas("ponderosa", "2026-11-02", "2026-11-15")
            self.assertEqual(len(v._MEMO), 1)                                                # la clave vieja se purgó


class TestHilo(unittest.TestCase):
    def test_iniciar_hilo_no_hace_nada_mientras_el_vigilante_esta_apagado(self):
        parar = threading.Event()
        llamadas = []
        with mock.patch.object(v.vigilante_estado, "config", return_value=dict(CFG, activo=False)), \
                mock.patch.object(v, "pasada", side_effect=lambda *a, **k: llamadas.append(1)):
            t = v.iniciar_hilo("ponderosa", lambda texto: None, parar=parar, espera_s=0.01)
            parar.wait(0.2)
            parar.set()
            t.join(2)
        self.assertEqual(llamadas, [])

    def test_iniciar_hilo_sobrevive_a_una_pasada_que_levanta(self):
        parar = threading.Event()
        llamadas = []

        def rota(*a, **k):
            llamadas.append(1)
            raise RuntimeError("boom")
        with mock.patch.object(v.vigilante_estado, "config", return_value=dict(CFG, activo=True)), \
                mock.patch.object(v, "pasada", side_effect=rota):
            t = v.iniciar_hilo("ponderosa", lambda texto: None, parar=parar, espera_s=0.01)
            parar.wait(0.3)
            parar.set()
            t.join(2)
        self.assertGreaterEqual(len(llamadas), 2)


if __name__ == "__main__":
    unittest.main()
