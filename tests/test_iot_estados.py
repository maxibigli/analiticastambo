"""El estado general de la rotativa (LAVANDO / BARRIDO / ORDEÑO / APAGADO) con estados de equipo configurables.

LAVANDO y BARRIDO ya no están escritos en `iot_monitoreo`: son dos sensores de estado de `iot_sensores`, y el tambo puede
sumar otros (la lavadora, un portón) y cambiar la prioridad moviéndolos de lugar.

    C:/Users/MAXI/AppData/Local/Programs/Python/Python312/python.exe -m unittest tests.test_iot_estados -v
"""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

import iot_lavado
import iot_monitoreo
import iot_sensores as sen
import iot_vumetros as iv

AHORA = datetime.datetime(2026, 11, 22, 19, 0, 0)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        ruta_db = os.path.join(self.dir.name, "iot.db")
        for p in (mock.patch.object(sen, "_RUTA", os.path.join(self.dir.name, "iot_sensores.json")),
                  mock.patch.object(iv, "_RUTA", os.path.join(self.dir.name, "iot_vumetros.json")),
                  mock.patch.object(iot_monitoreo, "RUTA_DB", ruta_db),
                  mock.patch.object(iot_monitoreo, "_conectar_db", lambda: iot_lavado._conectar_db(ruta_db))):
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(iot_monitoreo, "_ordeno_activo", return_value=False)
        self.ordeno = p.start()
        self.addCleanup(p.stop)
        self.con = iot_lavado._conectar_db(ruta_db)
        self.addCleanup(self.con.close)

    def latido(self, canal, activo, hace_min=1):
        cuando = (AHORA - datetime.timedelta(minutes=hace_min)).isoformat(timespec="seconds")
        self.con.execute("INSERT OR REPLACE INTO digitales_estado (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                         (canal, cuando, int(activo)))
        self.con.commit()

    def evento(self, canal, activo, hace_min):
        cuando = (AHORA - datetime.timedelta(minutes=hace_min)).isoformat(timespec="seconds")
        self.con.execute("INSERT INTO eventos_di (canal, fecha_hora, estado) VALUES (?, ?, ?)",
                         (canal, cuando, int(activo)))
        self.con.commit()

    def estado(self):
        return iot_monitoreo.estado_sistema("ponderosa", AHORA)

    def sumar(self, **kw):
        base = {"label": "Lavadora", "tipo": "digital", "rol": "estado", "entrada": "di_3", "estado_nombre": "LAVADORA"}
        sen.guardar(sen.cargar() + [{**base, **kw}])

    def intercambiar_estados(self):
        lista = sen.cargar()
        i, j = [k for k, s in enumerate(lista) if s["tipo"] == "digital" and s["rol"] == "estado"][:2]
        lista[i], lista[j] = lista[j], lista[i]
        sen.guardar(lista)


class TestPredefinidos(Base):
    def test_sin_archivo_vienen_lavando_y_barrido_atados_a_las_entradas_de_siempre(self):
        nombres = [(s["estado_nombre"], s["entrada"]) for s in sen.estados()]
        self.assertEqual(nombres, [("LAVANDO", "lavado_rotativa"), ("BARRIDO", "barrido_rotativa")])

    def test_un_archivo_de_antes_de_los_estados_los_recibe(self):
        with open(sen._RUTA, "w", encoding="utf-8") as f:
            json.dump({"sensores": [{"clave": "temp_leche", "label": "Temp. leche", "tipo": "analogico", "unidad": "°C",
                                     "min": 0, "max": 40}]}, f)
        self.assertEqual([s["estado_nombre"] for s in sen.estados()], ["LAVANDO", "BARRIDO"])

    def test_si_la_entrada_ya_la_usa_otro_sensor_no_se_pisa(self):
        with open(sen._RUTA, "w", encoding="utf-8") as f:
            json.dump({"sensores": [{"clave": "mi_lavado", "label": "Mi lavado", "tipo": "digital", "rol": "indicador",
                                     "entrada": "lavado_rotativa"}]}, f)
        self.assertEqual([s["estado_nombre"] for s in sen.estados()], ["BARRIDO"])

    def test_una_vez_guardado_lo_que_el_tambo_saca_no_vuelve(self):
        sen.guardar([s for s in sen.cargar() if s["clave"] != "lavado_rotativa"])
        self.assertEqual([s["estado_nombre"] for s in sen.estados()], ["BARRIDO"])
        self.assertNotIn("lavado_rotativa", [s["clave"] for s in sen.cargar()])


class TestEstadoGeneral(Base):
    def test_lavando_sale_del_sensor_de_estado(self):
        self.latido("lavado_rotativa", True)
        e = self.estado()
        self.assertEqual((e["estado"], e["equipo"]), ("LAVANDO", True))

    def test_el_primero_de_la_lista_manda_si_hay_dos_activos(self):
        self.latido("lavado_rotativa", True)
        self.latido("barrido_rotativa", True)
        self.assertEqual(self.estado()["estado"], "LAVANDO")
        self.intercambiar_estados()
        self.assertEqual(self.estado()["estado"], "BARRIDO")

    def test_un_estado_nuevo_del_tambo_cambia_el_cartel(self):
        self.sumar()
        self.latido("di_3", True)
        e = self.estado()
        self.assertEqual((e["estado"], e["equipo"]), ("LAVADORA", True))

    def test_sin_estados_activos_es_ordeno_o_apagado(self):
        e = self.estado()
        self.assertEqual((e["estado"], e["equipo"]), ("APAGADO", False))
        self.ordeno.return_value = True
        self.assertEqual(self.estado()["estado"], "ORDEÑO")

    def test_un_estado_activo_le_gana_al_ordeno(self):
        self.ordeno.return_value = True
        self.latido("barrido_rotativa", True)
        self.assertEqual(self.estado()["estado"], "BARRIDO")

    def test_el_contacto_inactivo_no_cuenta(self):
        self.latido("lavado_rotativa", False)
        self.assertEqual(self.estado()["estado"], "APAGADO")

    def test_un_estado_cuyo_gateway_se_callo_no_queda_pegado(self):
        self.latido("lavado_rotativa", True, hace_min=180)
        e = self.estado()
        self.assertEqual(e["estado"], "APAGADO")
        self.assertTrue(next(x for x in e["estados"] if x["estado"] == "LAVANDO")["sin_datos"])

    def test_antes_del_primer_latido_se_usa_el_ultimo_cambio_registrado(self):
        self.evento("lavado_rotativa", True, hace_min=5)
        self.assertEqual(self.estado()["estado"], "LAVANDO")

    def test_desde_es_la_hora_del_ultimo_encendido_no_la_del_latido(self):
        self.evento("lavado_rotativa", True, hace_min=20)
        self.latido("lavado_rotativa", True, hace_min=0)
        self.assertEqual(self.estado()["desde"], (AHORA - datetime.timedelta(minutes=20)).isoformat(timespec="seconds"))


class TestValidacion(Base):
    def test_el_estado_sin_nombre_toma_el_del_sensor_en_mayusculas(self):
        self.sumar(estado_nombre="")
        self.assertEqual(sen.estados()[-1]["estado_nombre"], "LAVADORA")

    def test_ordeno_y_apagado_estan_reservados(self):
        for nombre in ("ordeño", "Apagado"):
            with self.assertRaises(ValueError):
                self.sumar(label=f"Equipo {nombre}", estado_nombre=nombre)

    def test_dos_estados_no_pueden_llamarse_igual(self):
        with self.assertRaises(ValueError):
            self.sumar(estado_nombre="lavando")

    def test_un_estado_no_tiene_color_ni_aviso(self):
        self.sumar(color_activo="rojo", avisar=True)
        s = sen.estados()[-1]
        self.assertEqual((s["color_activo"], s["color_inactivo"], s["avisar"]), ("neutro", "neutro", False))

    def test_el_orden_de_la_lista_se_conserva_al_guardar(self):
        self.intercambiar_estados()
        self.assertEqual([s["estado_nombre"] for s in sen.estados()], ["BARRIDO", "LAVANDO"])


if __name__ == "__main__":
    unittest.main()
