"""Vigilat: la configuración (reglas, personas, rangos), su validación y el arranque desde el Vigilante anterior.

    C:\\Python314\\python.exe -m unittest tests.test_vigilat_config -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import vigilante_estado as ve
import vigilat_config as vc

PERSONA = {"id": "p1", "nombre": "Juan Pérez", "rango": "Encargado", "activa": True, "canales": ["whatsapp"],
           "whatsapp": "+54 2392 15-123456", "telegram": "", "correo": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "vigilat_config.json")
        self.vieja = os.path.join(self.dir.name, "vigilante_config.json")
        for p in (mock.patch.object(ve, "RUTA_CONFIG", self.vieja), mock.patch.object(vc, "RUTA", self.ruta),
                  mock.patch.dict(os.environ, {"LACTIA_URL_PUBLICA": ""})):
            p.start()
            self.addCleanup(p.stop)

    def con(self, **cambios):
        cfg = vc.por_defecto()
        cfg["personas"] = [dict(PERSONA)]
        cfg.update(cambios)
        return cfg


class TestArranque(Base):
    def test_sin_nada_guardado_arranca_apagado_con_las_seis_reglas(self):
        cfg, problema = vc.leer()
        self.assertIsNone(problema)
        self.assertFalse(cfg["general"]["activo"])
        self.assertEqual([r["tipo"] for r in cfg["reglas"]],
                         ["sin_datos", "fallas_unidades", "baja_ordeno", "vacas_faltantes", "baja_produccion",
                          "comida_atrasada"])
        self.assertEqual(vc.validar(cfg)["reglas"], cfg["reglas"])
        self.assertFalse(os.path.exists(self.ruta))                      # mirar no escribe nada

    def test_toma_lo_elegido_en_el_vigilante_anterior_y_no_cambia_a_quien_avisa(self):
        ve.set_config(self.vieja, activo=True, tope_dia=5, silencio_desde="21:00", sin_datos_horas=6, pasada_min=30)
        cfg, _ = vc.leer()
        g = cfg["general"]
        self.assertEqual((g["activo"], g["max_por_persona_dia"], g["silencio_desde"], g["revisar_min"]),
                         (True, 5, "21:00", 30))
        self.assertEqual(cfg["reglas"][0]["parametros"]["horas"], 6)
        activas = [r for r in cfg["reglas"] if r["activa"]]
        self.assertEqual([r["tipo"] for r in activas],
                         ["sin_datos", "baja_ordeno", "vacas_faltantes", "comida_atrasada"])
        self.assertTrue(all(r["destinos"] == ["generales"] for r in activas))

    def test_archivo_roto_o_invalido_deja_vigilat_apagado_y_lo_dice(self):
        ve.set_config(self.vieja, activo=True)
        for contenido in ("{esto no es json", json.dumps({"general": {}})):
            with open(self.ruta, "w", encoding="utf-8") as f:
                f.write(contenido)
            cfg, problema = vc.leer()
            self.assertFalse(cfg["general"]["activo"])
            self.assertIn("apagado", problema)


class TestGuardar(Base):
    def test_guarda_limpia_y_deja_copia_de_la_anterior(self):
        primera = vc.guardar(self.con())
        self.assertEqual(primera["personas"][0]["whatsapp"], "+54239215123456")
        segunda = self.con()
        segunda["general"]["max_por_persona_dia"] = 9
        vc.guardar(segunda)
        self.assertEqual(vc.leer()[0]["general"]["max_por_persona_dia"], 9)
        with open(os.path.join(self.dir.name, "vigilat_config.anterior.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["general"]["max_por_persona_dia"], 3)

    def test_algo_invalido_no_se_guarda(self):
        vc.guardar(self.con())
        with open(self.ruta, encoding="utf-8") as f:
            antes = f.read()
        casos = [
            (lambda c: c["personas"][0].update(whatsapp="12345"), "código de país"),
            (lambda c: c["personas"][0].update(canales=["telegram"]), "falta el dato"),
            (lambda c: c["personas"][0].update(telegram="@juan"), "número de chat"),
            (lambda c: c["personas"][0].update(correo="juan"), "correo"),
            (lambda c: c["personas"][0].update(rango="Gerente"), "rango"),
            (lambda c: c.update(rangos=[]), "al menos un rango"),
            (lambda c: c.update(rangos=["Dueño", "dueño"]), "repetido"),
            (lambda c: c["reglas"][0].update(destinos=["p:p9"]), "ya no existe"),
            (lambda c: c["reglas"][0].update(destinos=[]), "a quién avisar"),
            (lambda c: c["reglas"][0].update(nombre="  "), "nombre de la regla"),
            (lambda c: c["reglas"][0]["parametros"].update(horas=0), "entre 1 y 24"),
            (lambda c: c["reglas"][1]["cuando"].update(horas=[]), "al menos un horario"),
            (lambda c: c["reglas"][1]["cuando"].update(horas=["25:00"]), "HH:MM"),
            (lambda c: c["reglas"][0]["cuando"].update(modo="horas", horas=["07:00"]), "no puede correr"),
            (lambda c: c["reglas"][0]["cuando"].update(dias=[]), "día de la semana"),
            (lambda c: (c["reglas"][0]["acciones"].update(mensaje=False),
                        c["reglas"][0]["confirmacion"].update(requiere=True)), "tiene que mandar mensaje"),
            (lambda c: c["reglas"][1].update(id=c["reglas"][0]["id"]), "mismo identificador"),
            (lambda c: c["general"].update(salida_alarma="rele1"), "no es una salida"),
            (lambda c: c["general"].update(direccion_publica="analiticastambo.com"), "http"),
            (lambda c: c["general"].update(activo="si"), "sí o no"),
        ]
        for romper, texto in casos:
            cfg = self.con()
            romper(cfg)
            with self.subTest(texto=texto), self.assertRaises(vc.ConfigError) as e:
                vc.guardar(cfg)
            self.assertIn(texto, str(e.exception))
        with open(self.ruta, encoding="utf-8") as f:
            self.assertEqual(f.read(), antes)


class TestDestinos(Base):
    def test_resolver_por_persona_rango_y_generales_sin_repetir_ni_inactivas(self):
        cfg = self.con()
        cfg["personas"] += [{**PERSONA, "id": "p2", "nombre": "Ana"},
                            {**PERSONA, "id": "p3", "nombre": "Baja", "activa": False}]
        r = vc.resolver_destinos(cfg, ["p:p2", "rango:Encargado", "generales", "p:p3"])
        self.assertEqual([p["nombre"] for p in r], ["Ana", "Juan Pérez", "Canales generales"])

    def test_la_escalera_termina_en_el_dueno_y_saltea_niveles_vacios(self):
        cfg = self.con()
        cfg["personas"].append({**PERSONA, "id": "p2", "nombre": "Marta", "rango": "Dueño"})
        regla = cfg["reglas"][0]
        regla["destinos"] = ["p:p1"]
        self.assertEqual(vc.escalera(cfg, regla), [["p:p1"]])
        regla["confirmacion"].update(requiere=True, nivel2=["rango:Mecánico"], nivel3=[], dueno=True)
        self.assertEqual(vc.escalera(cfg, regla), [["p:p1"], ["rango:Dueño"]])
        cfg["rangos"] = ["Encargado", "Dueño"]                            # el primero de la lista es el dueño
        self.assertEqual(vc.escalera(cfg, regla), [["p:p1"], ["rango:Encargado"]])


if __name__ == "__main__":
    unittest.main()
