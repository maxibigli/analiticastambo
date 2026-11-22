"""Catálogo de sensores del Monitoreo IoT: la lista que arma el tambo desde la web (analógicos y digitales).

Los 11 sensores de siempre son la lista inicial (mismas claves, así no se pierde historial ni configuración). Un digital es
una ALARMA (activo = problema, en rojo), un INDICADOR (dos textos y un color por estado, p. ej. un fin de carrera) y va atado a
una entrada digital del gateway. Las entradas físicas no se agregan: las fija el hardware.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_sensores -v
"""
import os
import tempfile
import unittest
from unittest import mock

import app
import auth
import iot_analogicas
import iot_lavado
import iot_sensores as sen


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(sen, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json"))
        p.start()
        self.addCleanup(p.stop)


def analogico(label="Temp. nueva", **kw):
    return {"label": label, "tipo": "analogico", "unidad": "°C", "min": 0, "max": 100, **kw}


def digital(label="Puerta sala", **kw):
    return {"label": label, "tipo": "digital", "rol": "indicador", "entrada": "di_3", **kw}


class TestLista(Base):
    def test_sin_archivo_la_lista_son_los_11_sensores_de_siempre(self):
        claves = [s["clave"] for s in sen.cargar()]
        self.assertEqual(len(claves), 11)
        self.assertEqual(claves[0], "temp_leche")
        self.assertIn("vacio_general", claves)
        self.assertTrue(all(s["tipo"] == "analogico" for s in sen.cargar()))

    def test_las_entradas_validas_son_las_del_gateway(self):
        self.assertEqual(set(sen.ENTRADAS), set(iot_lavado.CANALES))

    def test_guardar_y_volver_a_cargar(self):
        sen.guardar(sen.cargar() + [analogico("Temp. silo")])
        self.assertEqual(sen.cargar()[-1]["label"], "Temp. silo")
        self.assertEqual(len(sen.cargar()), 12)

    def test_un_archivo_roto_cae_a_la_lista_de_siempre(self):
        with open(sen._RUTA, "w", encoding="utf-8") as f:
            f.write("{no es json")
        self.assertEqual(len(sen.cargar()), 11)


class TestClaves(Base):
    def test_un_sensor_nuevo_recibe_una_clave_a_partir_del_nombre(self):
        sen.guardar(sen.cargar() + [analogico("Temp. Silo Ñandú 2")])
        self.assertEqual(sen.cargar()[-1]["clave"], "temp_silo_nandu_2")

    def test_dos_nombres_que_dan_la_misma_clave_no_chocan_pero_los_nombres_si(self):
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [analogico("temp. leche")])          # mismo nombre que uno existente

    def test_la_clave_de_un_sensor_existente_no_cambia_si_se_le_cambia_el_nombre(self):
        lista = sen.cargar()
        lista[0]["label"] = "Temperatura de la leche"
        sen.guardar(lista)
        self.assertEqual(sen.cargar()[0]["clave"], "temp_leche")
        self.assertEqual(sen.cargar()[0]["label"], "Temperatura de la leche")

    def test_una_clave_inventada_por_el_cliente_se_ignora_y_se_genera_la_propia(self):
        sen.guardar(sen.cargar() + [analogico("Otro", clave="temp_leche")])
        claves = [s["clave"] for s in sen.cargar()]
        self.assertEqual(len(set(claves)), len(claves))
        self.assertEqual(claves[-1], "otro")

    def test_ith_es_una_clave_reservada(self):
        sen.guardar(sen.cargar() + [analogico("ITH")])
        self.assertNotIn("ith", [s["clave"] for s in sen.cargar()])
        self.assertEqual(sen.cargar()[-1]["clave"], "ith_2")


class TestAnalogicos(Base):
    def test_exige_nombre_y_escala_valida(self):
        for malo in (analogico(""), analogico("x", min=10, max=10), analogico("y", min=20, max=10),
                     analogico("z", min="abc"), analogico("w", max=None)):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                sen.guardar(sen.cargar() + [malo])

    def test_la_unidad_puede_estar_vacia_pero_no_ser_larga(self):
        sen.guardar(sen.cargar() + [analogico("Sin unidad", unidad="")])
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [analogico("Larga", unidad="x" * 20)])

    def test_un_nombre_de_mas_de_40_letras_se_rechaza(self):
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [analogico("n" * 41)])


class TestDigitales(Base):
    def test_una_alarma_es_roja_activa_y_verde_inactiva_y_avisa_por_defecto(self):
        sen.guardar(sen.cargar() + [digital("Térmico bomba", rol="alarma")])
        s = sen.cargar()[-1]
        self.assertEqual((s["color_activo"], s["color_inactivo"], s["avisar"]), ("rojo", "verde", True))
        self.assertEqual((s["texto_activo"], s["texto_inactivo"]), ("ALARMA", "Normal"))

    def test_el_color_de_una_alarma_no_se_puede_cambiar(self):
        sen.guardar(sen.cargar() + [digital("Térmico", rol="alarma", color_activo="amarillo", color_inactivo="rojo")])
        s = sen.cargar()[-1]
        self.assertEqual((s["color_activo"], s["color_inactivo"]), ("rojo", "verde"))

    def test_un_indicador_tiene_textos_y_colores_propios_y_por_defecto_es_neutro_y_no_avisa(self):
        sen.guardar(sen.cargar() + [digital("Portón")])
        s = sen.cargar()[-1]
        self.assertEqual((s["texto_activo"], s["texto_inactivo"]), ("Activo", "Inactivo"))
        self.assertEqual((s["color_activo"], s["color_inactivo"], s["avisar"]), ("neutro", "neutro", False))
        sen.guardar(sen.cargar()[:-1] + [digital("Portón", texto_activo="Abierto", texto_inactivo="Cerrado",
                                                 color_activo="amarillo", color_inactivo="verde", avisar=True)])
        s = sen.cargar()[-1]
        self.assertEqual((s["texto_activo"], s["color_activo"], s["avisar"]), ("Abierto", "amarillo", True))

    def test_entrada_invalida_o_repetida_se_rechaza_y_vacia_es_sin_instalar(self):
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [digital("A", entrada="di_99")])
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [digital("A", entrada="di_3"), digital("B", entrada="di_3")])
        sen.guardar(sen.cargar() + [digital("C", entrada="")])
        self.assertIsNone(sen.cargar()[-1]["entrada"])

    def test_color_o_rol_invalido_se_rechaza(self):
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [digital("A", color_activo="violeta")])
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [digital("A", rol="otro")])

    def test_contacto_normalmente_cerrado_se_guarda(self):
        sen.guardar(sen.cargar() + [digital("NC", invertido=True)])
        self.assertTrue(sen.cargar()[-1]["invertido"])

    def test_texto_vacio_cae_al_de_siempre_y_largo_se_rechaza(self):
        sen.guardar(sen.cargar() + [digital("D", texto_activo="  ")])
        self.assertEqual(sen.cargar()[-1]["texto_activo"], "Activo")
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [digital("E", texto_activo="x" * 30)])

    def test_el_estado_de_un_valor_y_su_color(self):
        s = sen.normalizar(digital("Portón", texto_activo="Abierto", texto_inactivo="Cerrado",
                                   color_activo="amarillo", color_inactivo="verde"))
        self.assertEqual(sen.estado_de(s, True), ("Abierto", "amarillo"))
        self.assertEqual(sen.estado_de(s, False), ("Cerrado", "verde"))
        neutro = sen.normalizar(digital("Libre"))
        self.assertEqual(sen.estado_de(neutro, True), ("Activo", None))      # neutro: sin color, no cuenta como alarma


class TestSacarSensores(Base):
    def test_sacar_un_sensor_asignado_a_una_entrada_analogica_se_rechaza_con_el_motivo(self):
        lista = [s for s in sen.cargar() if s["clave"] != "temp_leche"]
        with self.assertRaises(ValueError) as cm:
            sen.guardar(lista, en_uso={"temp_leche"})
        self.assertIn("Temp. leche", str(cm.exception))

    def test_sacar_uno_libre_anda(self):
        sen.guardar([s for s in sen.cargar() if s["clave"] != "temp_corral"])
        self.assertNotIn("temp_corral", [s["clave"] for s in sen.cargar()])

    def test_un_guardado_que_falla_no_pisa_lo_anterior(self):
        sen.guardar(sen.cargar() + [analogico("Bueno")])
        with self.assertRaises(ValueError):
            sen.guardar(sen.cargar() + [analogico("Malo", min=5, max=5)])
        self.assertEqual(sen.cargar()[-1]["label"], "Bueno")
        self.assertEqual(len(sen.cargar()), 12)

    def test_no_se_guardan_mas_de_60(self):
        with self.assertRaises(ValueError):
            sen.guardar([analogico(f"S{i}") for i in range(61)])


class TestApi(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(iot_analogicas, "_RUTA", os.path.join(self.dir.name, "iot_analogicas.json"))
        p.start()
        self.addCleanup(p.stop)
        auth._intentos_fallidos.clear()
        self.addCleanup(auth._intentos_fallidos.clear)
        self.cli = app.app.test_client()
        with self.cli.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"

    def test_get_trae_la_lista_las_entradas_y_los_asignados(self):
        d = self.cli.get("/api/iot/sensores").get_json()
        self.assertEqual(len(d["sensores"]), 11)
        self.assertEqual([e["clave"] for e in d["entradas"]], list(sen.ENTRADAS))
        self.assertEqual(d["asignados"], [])

    def test_post_agrega_un_sensor_y_se_ve_en_la_lista(self):
        lista = self.cli.get("/api/iot/sensores").get_json()["sensores"]
        lista.append({"label": "Térmico bomba", "tipo": "digital", "rol": "alarma", "entrada": "di_3"})
        r = self.cli.post("/api/iot/sensores", json={"sensores": lista})
        self.assertEqual(r.status_code, 200)
        nuevo = self.cli.get("/api/iot/sensores").get_json()["sensores"][-1]
        self.assertEqual((nuevo["clave"], nuevo["color_activo"]), ("termico_bomba", "rojo"))

    def test_post_con_datos_invalidos_da_400_con_el_motivo_y_no_guarda(self):
        lista = self.cli.get("/api/iot/sensores").get_json()["sensores"]
        lista.append({"label": "Roto", "tipo": "analogico", "unidad": "", "min": 5, "max": 5})
        r = self.cli.post("/api/iot/sensores", json={"sensores": lista})
        self.assertEqual(r.status_code, 400)
        self.assertIn("escala", r.get_json()["error"])
        self.assertEqual(len(self.cli.get("/api/iot/sensores").get_json()["sensores"]), 11)

    def test_post_no_deja_sacar_un_sensor_asignado_a_una_entrada_analogica(self):
        iot_analogicas.guardar({"ai_1": {"sensor": "temp_leche", "direccion": 0, "raw_4": 4000, "raw_20": 20000}},
                               {s["clave"]: (s["min"], s["max"]) for s in sen.analogicos()})
        lista = [s for s in self.cli.get("/api/iot/sensores").get_json()["sensores"] if s["clave"] != "temp_leche"]
        r = self.cli.post("/api/iot/sensores", json={"sensores": lista})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Temp. leche", r.get_json()["error"])
        self.assertEqual(self.cli.get("/api/iot/sensores").get_json()["asignados"], ["temp_leche"])

    def test_el_asignador_analogico_ofrece_los_sensores_nuevos(self):
        sen.guardar(sen.cargar() + [{"label": "Temp. silo", "tipo": "analogico", "unidad": "°C", "min": 0, "max": 60}])
        sensores = self.cli.get("/api/iot/analogicas").get_json()["sensores"]
        self.assertIn("temp_silo", [s["clave"] for s in sensores])

    def test_formato_invalido_y_sin_sesion(self):
        self.assertEqual(self.cli.post("/api/iot/sensores", json={"sensores": {}}).status_code, 400)
        anonimo = app.app.test_client()
        self.assertNotEqual(anonimo.get("/api/iot/sensores").status_code, 200)
        self.assertNotEqual(anonimo.post("/api/iot/sensores", json={"sensores": []}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
