"""Vigilante, Tarea 3: estado en SQLite (dedup, tope, retenidos), configuración y horario silencioso.

Todo contra archivos temporales: nunca toca `vigilante.db` ni `vigilante_config.json` reales.

    C:\\Python314\\python.exe -m unittest discover -s tests -p "test_vigilante_estado.py" -v
"""
import datetime
import json
import os
import tempfile
import unittest

import vigilante_estado as ve

D = datetime.date(2026, 11, 14)


def a(h, mi=0, dia=0):
    return datetime.datetime.combine(D + datetime.timedelta(days=dia), datetime.time(h, mi))


class Base(unittest.TestCase):
    def setUp(self):
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        self.ruta_db = os.path.join(self.carpeta.name, "v.db")
        self.ruta_cfg = os.path.join(self.carpeta.name, "v.json")


class TestEstado(Base):
    def test_dedup_sobrevive_a_un_reinicio(self):
        e1 = ve.Estado(self.ruta_db)
        self.assertFalse(e1.ya_avisado("2026-11-14|1|baja"))
        e1.registrar_aviso("2026-11-14|1|baja", a(7))
        e2 = ve.Estado(self.ruta_db)             # "reinicio": otra instancia sobre el mismo archivo
        self.assertTrue(e2.ya_avisado("2026-11-14|1|baja"))
        self.assertFalse(e2.ya_avisado("2026-11-14|2|baja"))

    def test_registrar_dos_veces_la_misma_clave_no_falla(self):
        e = ve.Estado(self.ruta_db)
        e.registrar_aviso("k", a(7))
        e.registrar_aviso("k", a(8))
        self.assertTrue(e.ya_avisado("k"))

    def test_estado_datos_sobrevive_a_un_reinicio(self):
        e1 = ve.Estado(self.ruta_db)
        self.assertEqual(e1.estado_datos(), "ok")
        e1.set_estado_datos("sin_datos")
        self.assertEqual(ve.Estado(self.ruta_db).estado_datos(), "sin_datos")

    def test_estado_datos_invalido_se_rechaza(self):
        with self.assertRaises(ValueError):
            ve.Estado(self.ruta_db).set_estado_datos("roto")

    def test_tomar_retenidos_vacia_la_lista(self):
        e = ve.Estado(self.ruta_db)
        e.retener("baja del ordeño 1", a(7))
        e.retener("comida atrasada", a(8))
        self.assertEqual(e.tomar_retenidos(), ["baja del ordeño 1", "comida atrasada"])
        self.assertEqual(e.tomar_retenidos(), [])

    def test_avisos_del_dia_cuenta_mensajes_no_claves(self):
        e = ve.Estado(self.ruta_db)
        for k in ("a", "b", "c"):                 # tres claves en un mismo mensaje...
            e.registrar_aviso(k, a(7))
        e.registrar_envio(D)                      # ...son UN mensaje
        self.assertEqual(e.avisos_del_dia(D), 1)
        e.registrar_envio(D)
        self.assertEqual(e.avisos_del_dia(D), 2)
        self.assertEqual(e.avisos_del_dia(D + datetime.timedelta(days=1)), 0)

    def test_ultima_pasada_se_guarda_y_sobrevive_a_un_reinicio(self):
        e1 = ve.Estado(self.ruta_db)
        self.assertIsNone(e1.ultima_pasada())
        e1.set_ultima_pasada(a(9, 30), "ok")
        cuando, resultado = ve.Estado(self.ruta_db).ultima_pasada()
        self.assertEqual((cuando, resultado), (a(9, 30), "ok"))


class TestSilencio(unittest.TestCase):
    CFG = {"silencio_desde": "22:00", "silencio_hasta": "05:00"}

    def test_tramo_que_cruza_la_medianoche(self):
        self.assertTrue(ve.en_silencio(a(23), self.CFG))
        self.assertTrue(ve.en_silencio(a(2), self.CFG))
        self.assertTrue(ve.en_silencio(a(22, 0), self.CFG))        # el inicio ya es silencio
        self.assertFalse(ve.en_silencio(a(5, 0), self.CFG))        # el fin ya no
        self.assertFalse(ve.en_silencio(a(12), self.CFG))

    def test_tramo_dentro_del_mismo_dia(self):
        cfg = {"silencio_desde": "13:00", "silencio_hasta": "15:00"}
        self.assertTrue(ve.en_silencio(a(14), cfg))
        self.assertFalse(ve.en_silencio(a(16), cfg))

    def test_desde_igual_a_hasta_es_sin_silencio(self):
        cfg = {"silencio_desde": "06:00", "silencio_hasta": "06:00"}
        self.assertFalse(ve.en_silencio(a(6), cfg))
        self.assertFalse(ve.en_silencio(a(23), cfg))


class TestConfig(Base):
    def test_sin_archivo_da_los_valores_por_defecto_y_apagado(self):
        cfg = ve.config(self.ruta_cfg)
        self.assertEqual(cfg, {"activo": False, "pasada_min": 15, "tope_dia": 3, "silencio_desde": "22:00",
                               "silencio_hasta": "05:00", "sin_datos_horas": 4})

    def test_archivo_corrupto_da_los_valores_por_defecto(self):
        with open(self.ruta_cfg, "w", encoding="utf-8") as f:
            f.write("{esto no es json")
        self.assertFalse(ve.config(self.ruta_cfg)["activo"])
        self.assertEqual(ve.config(self.ruta_cfg)["tope_dia"], 3)

    def test_un_campo_invalido_en_el_archivo_cae_a_su_defecto_sin_tirar_los_demas(self):
        with open(self.ruta_cfg, "w", encoding="utf-8") as f:
            json.dump({"tope_dia": 999, "pasada_min": 30, "activo": True}, f)
        cfg = ve.config(self.ruta_cfg)
        self.assertEqual(cfg["tope_dia"], 3)
        self.assertEqual(cfg["pasada_min"], 30)
        self.assertTrue(cfg["activo"])

    def test_guardar_y_leer(self):
        ve.set_config(ruta=self.ruta_cfg, activo=True, tope_dia=5, silencio_desde="21:30")
        cfg = ve.config(self.ruta_cfg)
        self.assertTrue(cfg["activo"])
        self.assertEqual((cfg["tope_dia"], cfg["silencio_desde"], cfg["pasada_min"]), (5, "21:30", 15))

    def test_valores_invalidos_se_rechazan_con_mensaje_en_español(self):
        for campos in ({"tope_dia": 0}, {"tope_dia": 21}, {"pasada_min": 4}, {"pasada_min": 121},
                       {"silencio_desde": "25:00"}, {"silencio_hasta": "7"}, {"sin_datos_horas": 0},
                       {"sin_datos_horas": 25}, {"tope_dia": "tres"}, {"inexistente": 1}):
            with self.subTest(campos=campos):
                with self.assertRaises(ValueError) as cm:
                    ve.set_config(ruta=self.ruta_cfg, **campos)
                self.assertTrue(str(cm.exception))
        self.assertFalse(os.path.exists(self.ruta_cfg))      # nada se guardó a medias


if __name__ == "__main__":
    unittest.main()
