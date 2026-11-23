"""Los 32 + 32 + 32 canales del gateway (entradas digitales, analógicas y salidas), sus nombres y qué se consulta de verdad.

32 es lo que se puede nombrar y asignar; el gateway solo consulta lo que se usa (`iot_lavado.canales_a_leer`) y solo maneja
las salidas en uso (`iot_lavado.actuadores_en_uso`), para no pedirle al M300 direcciones que no existen en su hardware.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_canales -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import app
import iot_analogicas
import iot_canales
import iot_lavado
import iot_monitoreo
import iot_sensores as sen
import lavado_programa
import voz_comandos


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ruta_db = os.path.join(self.dir.name, "iot.db")
        for p in (mock.patch.object(iot_canales, "_RUTA", os.path.join(self.dir.name, "nombres.json")),
                  mock.patch.object(sen, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json")),
                  mock.patch.object(lavado_programa, "_RUTA_CONFIG", os.path.join(self.dir.name, "lavado.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", ruta_db),
                  mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(ruta_db)),
                  mock.patch.object(voz_comandos, "estado", return_value={}),
                  mock.patch.object(voz_comandos, "apagados_pendientes", return_value={})):
            p.start()
            self.addCleanup(p.stop)


class TestLosTreintaYDos(Base):
    def test_hay_32_de_cada_tipo_con_claves_unicas(self):
        for grupo in (iot_canales.ENTRADAS_DIGITALES, iot_canales.ENTRADAS_ANALOGICAS, iot_canales.SALIDAS):
            self.assertEqual((len(grupo), len(set(grupo))), (32, 32))

    def test_las_claves_de_siempre_no_cambian(self):
        self.assertEqual(iot_canales.ENTRADAS_DIGITALES[:4], ("lavado_rotativa", "barrido_rotativa", "di_3", "di_4"))
        self.assertEqual((iot_canales.ENTRADAS_ANALOGICAS[0], iot_canales.SALIDAS[7]), ("ai_1", "do_8"))

    def test_todos_los_modulos_usan_la_misma_lista(self):
        self.assertEqual(set(sen.ENTRADAS), set(iot_lavado.CANALES))
        self.assertEqual(set(iot_lavado.ACTUADORES), set(iot_canales.SALIDAS))
        self.assertEqual(voz_comandos.ACTUADORES_VALIDOS, set(iot_canales.SALIDAS))
        self.assertEqual(lavado_programa.RELES_VALIDOS, set(iot_canales.SALIDAS))
        self.assertEqual([c for c, _ in iot_analogicas.CANALES], list(iot_canales.ENTRADAS_ANALOGICAS))

    def test_la_direccion_modbus_es_el_numero_menos_uno(self):
        self.assertEqual(iot_lavado.CANALES["lavado_rotativa"]["direccion"], 0)
        self.assertEqual(iot_lavado.CANALES["di_32"]["direccion"], 31)
        self.assertEqual(iot_lavado.ACTUADORES["do_1"], 0)
        self.assertEqual(iot_lavado.ACTUADORES["do_32"], 31)

    def test_el_numero_de_cada_canal(self):
        self.assertEqual([iot_canales.numero(c) for c in ("lavado_rotativa", "di_17", "ai_32", "do_9")], [1, 17, 32, 9])


class TestNombres(Base):
    def test_se_nombran_los_tres_tipos(self):
        iot_canales.guardar({"do_1": "Bomba de agua", "di_20": "Portón", "ai_30": "Temp. silo", "do_2": ""})
        self.assertEqual(iot_canales.nombres(), {"do_1": "Bomba de agua", "di_20": "Portón", "ai_30": "Temp. silo"})

    def test_sin_nombre_se_ve_el_generico(self):
        self.assertEqual(iot_canales.nombre("di_5"), "Entrada 5")
        self.assertEqual(iot_canales.nombre("ai_7"), "Analógica 7")
        self.assertEqual(iot_canales.nombre("do_12"), "Actuador 12")

    def test_un_canal_inexistente_se_rechaza(self):
        for clave in ("do_33", "di_0", "ai_99"):
            with self.assertRaises(ValueError):
                iot_canales.guardar({clave: "x"})

    def test_dos_salidas_no_pueden_llamarse_igual(self):
        with self.assertRaises(ValueError):
            iot_canales.guardar({"do_1": "Bomba de agua", "do_20": "bomba de agua"})

    def test_una_entrada_y_una_salida_si_pueden_llamarse_igual(self):
        iot_canales.guardar({"di_5": "Bomba", "do_5": "Bomba"})
        self.assertEqual(len(iot_canales.nombres()), 2)

    def test_un_archivo_roto_o_con_basura_se_lee_sin_romper(self):
        with open(iot_canales._RUTA, "w", encoding="utf-8") as f:
            f.write("{no es json")
        self.assertEqual(iot_canales.nombres(), {})
        with open(iot_canales._RUTA, "w", encoding="utf-8") as f:
            json.dump({"do_99": "x", "di_3": "Ordeño"}, f)
        self.assertEqual(iot_canales.nombres(), {"di_3": "Ordeño"})


class TestCanalesALeer(Base):
    def test_sin_nada_asignado_solo_lavado_y_barrido(self):
        self.assertEqual(iot_lavado.canales_a_leer(), ["lavado_rotativa", "barrido_rotativa"])

    def test_una_entrada_con_sensor_se_consulta(self):
        sen.guardar(sen.cargar() + [{"label": "Portón", "tipo": "digital", "entrada": "di_20"}])
        self.assertIn("di_20", iot_lavado.canales_a_leer())

    def test_una_entrada_con_nombre_se_consulta(self):
        iot_canales.guardar({"di_9": "Térmico"})
        self.assertIn("di_9", iot_lavado.canales_a_leer())

    def test_no_se_consultan_las_32(self):
        sen.guardar(sen.cargar() + [{"label": "Portón", "tipo": "digital", "entrada": "di_20"}])
        self.assertLess(len(iot_lavado.canales_a_leer()), 5)


class TestActuadoresEnUso(Base):
    def test_las_8_de_siempre_estan_siempre(self):
        self.assertEqual(iot_lavado.actuadores_en_uso(), {f"do_{i}" for i in range(1, 9)})

    def test_una_salida_nombrada_entra(self):
        iot_canales.guardar({"do_20": "Bomba de espuma"})
        self.assertIn("do_20", iot_lavado.actuadores_en_uso())

    def test_un_rele_de_una_etapa_del_lavado_entra(self):
        lavado_programa.guardar_etapas([{"reles": ["do_25"], "duracion_s": 60}])
        self.assertIn("do_25", iot_lavado.actuadores_en_uso())

    def test_un_rele_sostenido_por_voz_entra_aunque_ya_no_tenga_nombre(self):
        with mock.patch.object(voz_comandos, "estado", return_value={"do_30": "2026-11-22T10:00:00"}):
            self.assertIn("do_30", iot_lavado.actuadores_en_uso())

    def test_un_apagado_pendiente_entra(self):
        with mock.patch.object(voz_comandos, "apagados_pendientes", return_value={"do_31": "2026-11-22T10:00:00"}):
            self.assertIn("do_31", iot_lavado.actuadores_en_uso())

    def test_las_salidas_sin_uso_no_se_tocan(self):
        self.assertNotIn("do_32", iot_lavado.actuadores_en_uso())


class TestPanelIo(Base):
    def entradas(self, **kw):
        return {e["clave"]: e["label"] for e in iot_monitoreo.panel_io(**kw)["entradas"]}

    def test_una_entrada_sin_nada_dice_su_nombre_generico(self):
        self.assertEqual(self.entradas()["di_5"], "Entrada 5")

    def test_una_entrada_con_sensor_lleva_el_nombre_del_sensor(self):
        sen.guardar(sen.cargar() + [{"label": "Portón de la sala", "tipo": "digital", "entrada": "di_5"}])
        self.assertEqual(self.entradas()["di_5"], "Portón de la sala")

    def test_el_nombre_propio_de_la_entrada_le_gana_al_del_sensor(self):
        sen.guardar(sen.cargar() + [{"label": "Portón de la sala", "tipo": "digital", "entrada": "di_5"}])
        iot_canales.guardar({"di_5": "Borne 5"})
        self.assertEqual(self.entradas()["di_5"], "Borne 5")

    def test_lavado_y_barrido_llevan_el_nombre_de_sus_sensores_precargados(self):
        self.assertEqual(self.entradas()["lavado_rotativa"], "Lavado de la rotativa")

    def test_la_pantalla_esp32_recibe_solo_8_y_8(self):
        d = iot_monitoreo.panel_io(maximo=iot_monitoreo.MAXIMO_PANTALLA_ESP32)
        self.assertEqual((len(d["entradas"]), len(d["salidas"])), (8, 8))
        todo = iot_monitoreo.panel_io()
        self.assertEqual((len(todo["entradas"]), len(todo["salidas"])), (32, 32))


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_el_editor_trae_los_tres_grupos_numerados(self):
        d = self.cli.get("/api/iot/canales").get_json()
        for grupo in ("digitales", "analogicas", "salidas"):
            self.assertEqual([c["numero"] for c in d[grupo]], list(range(1, 33)))
        self.assertEqual(d["salidas"][0]["default"], "Actuador 1")

    def test_post_guarda_nombres_de_los_tres_grupos(self):
        r = self.cli.post("/api/iot/canales", json={"nombres": {"do_1": "Bomba", "di_3": "Ordeño", "ai_2": "Temp. leche"}})
        self.assertEqual(r.status_code, 200)
        d = self.cli.get("/api/iot/canales").get_json()
        self.assertEqual((d["salidas"][0]["nombre"], d["digitales"][2]["nombre"], d["analogicas"][1]["nombre"]),
                         ("Bomba", "Ordeño", "Temp. leche"))

    def test_el_menu_de_entradas_del_editor_de_sensores_trae_las_32_con_su_nombre(self):
        iot_canales.guardar({"di_3": "Ordeño"})
        e = self.cli.get("/api/iot/sensores").get_json()["entradas"]
        self.assertEqual(len(e), 32)
        self.assertEqual({x["clave"]: x["label"] for x in e}["di_3"], "Ordeño")

    def test_el_endpoint_de_la_pantalla_esp32_manda_8_y_8(self):
        d = app.app.test_client().get("/api/iot/pantalla/io").get_json()
        self.assertEqual((len(d["entradas"]), len(d["salidas"])), (8, 8))

    def test_el_panel_de_analogicas_usa_el_nombre_propio(self):
        iot_canales.guardar({"ai_3": "Sala de máquinas"})
        canales = {c["clave"]: c["label"] for c in iot_monitoreo.panel_analogicas()["canales"]}
        self.assertEqual((canales["ai_3"], canales["ai_4"]), ("Sala de máquinas", "Analógica 4"))


if __name__ == "__main__":
    unittest.main()
