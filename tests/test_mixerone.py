"""Proveedor MixerOne: traduce la API de https://mixerone.net a la interfaz común de `proveedores`.

Sin red: `requests.Session.post` se reemplaza por respuestas con la MISMA estructura que devolvió la API real el
09/11/2026 (login, viajes del 07/11 de La Ponderosa, corrales, alimentos, equipos), recortadas.

    C:\\Python314\\python.exe -m unittest tests.test_mixerone -v
"""
import datetime
import json
import unittest
from unittest import mock

import alimentacion
import conciliacion
from proveedores import mixerone

TOKEN = "token-de-prueba"

CLIENTES = [
    {"id": 623, "business_name": "Cereigido La Ponderosa", "is_active": True},
    {"id": 629, "business_name": "Cereigido Recría LP", "is_active": True},
    {"id": 607, "business_name": "Cereigido Don German", "is_active": True},
]


def carga(hora, comida, kg, teo, ms, receta="ALTA"):
    return {"id": 1, "action": "Carga", "operator": "Jose Monteros", "kg_real": kg, "kg_theoretical": teo,
            "hour": hora, "food": comida, "food_ms": ms, "recipe": receta, "device_id": "d45e78491da9680d",
            "heads": None, "corral": None}


def descarga(hora, corral, kg, cabezas=None, receta="ALTA"):
    return {"id": 2, "action": "Descarga", "operator": "Daniel Monteros", "kg_real": kg, "kg_theoretical": kg,
            "hour": hora, "corral": corral, "corral_id": 11416, "recipe": receta,
            "device_id": "d45e78491da9680d", "heads": cabezas, "food": None}


def viaje(uuid, creado, detalles, etapa="Ordeñe"):
    return {"trip_uuid": uuid, "created": creado, "growth_stage": etapa, "details": detalles,
            "total_heads": 849}


# Dos viajes de la receta ALTA: maíz al 40 % y heno al 90 %. El segundo termina pasada la medianoche.
VIAJE_1 = viaje("viaje-1", "2026-11-07T09:02:25.000Z", [
    carga("09:02:25", "Silo Maiz", 1000, 980, 40),
    carga("09:10:00", "Heno", 500, 500, 90),
    descarga("09:52:00", "RODEO 1", 900, 531),
    descarga("09:54:00", "RODEO 2", 500, 318),
])
VIAJE_2 = viaje("viaje-2", "2026-11-07T23:50:00.000Z", [
    carga("23:50:00", "Silo Maiz", 200, 200, 40),
    descarga("00:10:00", "RODEO 3", 190, 322),
])


class Respuesta:
    def __init__(self, cuerpo, estado=201):
        self.status_code = estado
        self.content = json.dumps(cuerpo).encode("utf-8")

    def json(self):
        return json.loads(self.content)


class ApiFalsa:
    """Se hace pasar por mixerone.net. Anota cada llamada para poder mirarla después."""

    def __init__(self, clientes=CLIENTES, viajes=None, login_estado=201, por_pagina=25):
        self.clientes = clientes
        self.viajes = viajes if viajes is not None else [VIAJE_1, VIAJE_2]
        self.login_estado = login_estado
        self.por_pagina = por_pagina
        self.llamadas = []
        self.logins = 0

    def post(self, url, json=None, headers=None, timeout=None, **_):
        ruta = url.split("/api/", 1)[1]
        self.llamadas.append((ruta, json, dict(headers or {})))
        if ruta == "auth/loginuser":
            self.logins += 1
            if self.login_estado != 201:
                return Respuesta({"status": False, "detail": "Credenciales inválidas"}, self.login_estado)
            return Respuesta({"status": True, "response": {"access_token": TOKEN, "associated_clients": self.clientes}})
        if (headers or {}).get("Authorization") != f"Bearer {TOKEN}":
            return Respuesta({"status": False}, 401)
        if ruta == "trips/GetByFilterPaginated":
            pagina = json["page"]
            ini = (pagina - 1) * self.por_pagina
            lote = self.viajes[ini:ini + self.por_pagina]
            total = len(self.viajes)
            paginas = max(1, -(-total // self.por_pagina))
            return Respuesta({"status": True, "response": {
                "trips": lote,
                "pagination": {"page": pagina, "pageSize": self.por_pagina, "totalItems": total,
                               "totalPages": paginas, "hasNextPage": pagina < paginas}}})
        if ruta == "corral/getbyfilter":
            return Respuesta({"status": True, "response": {"corrals": [
                {"id": 11416, "name": "RODEO 1", "order": 1, "is_active": True, "recipe_name": "ALTA",
                 "troops": [{"name": "RODEO 1", "quantity": 531, "category_id": 6341}]},
                {"id": 11417, "name": "RODEO 2", "order": 2, "is_active": True, "recipe_name": "ALTA",
                 "troops": [{"name": "RODEO 2", "quantity": 318, "category_id": 6341}]},
                {"id": 11465, "name": "CHIQUITAS 2", "order": 11, "is_active": True, "recipe_name": None,
                 "troops": []},
                {"id": 11466, "name": "CHIQUITAS 3", "order": 12, "is_active": True, "recipe_name": None,
                 "troops": [{"name": "CHIQUITAS 3", "quantity": 0, "category_id": 6344}]},
                {"id": 11487, "name": "VIEJO", "order": 13, "is_active": True, "recipe_name": "ALTA",
                 "deleted_at": "2026-09-01T00:00:00.000Z", "troops": [{"name": "VIEJO", "quantity": 40}]},
            ]}})
        if ruta == "foods/getbyfilter":
            return Respuesta({"status": True, "response": {"foods": [
                {"id": 1, "name": "Cascara de Soja", "dry_mass": 93, "price": 225, "stock": 120},
                {"id": 2, "name": "Agua", "dry_mass": 0, "price": 0, "stock": 0},
            ]}})
        if ruta == "device/getall":
            return Respuesta({"status": True, "response": [
                {"fullname": "Mixer", "device_id": "d45e78491da9680d", "alias": None, "is_active": True,
                 "date_information_sent": "2026-11-09T00:00:00.000Z"}]})
        return Respuesta({"status": False}, 404)


class Base(unittest.TestCase):
    def setUp(self):
        mixerone._cache.clear()
        self.api = ApiFalsa()
        self._parches = [
            mock.patch.object(mixerone.requests.Session, "post", lambda s, *a, **k: self.api.post(*a, **k)),
            mock.patch.object(mixerone, "_variable", self._variable),
        ]
        self.entorno = {mixerone.VAR_USUARIO: "usuario", mixerone.VAR_PASSWORD: "clave",
                        "MIXERONE_CLIENTE_PONDEROSA": "623"}
        self.p = mixerone.para("ponderosa")
        for p in self._parches:
            p.start()
            self.addCleanup(p.stop)

    def _variable(self, nombre):
        return self.entorno.get(nombre)


class TestDisponible(Base):
    def test_sin_credenciales_dice_cuales_faltan(self):
        self.entorno.clear()
        ok, motivo = self.p.disponible()
        self.assertFalse(ok)
        self.assertIn("MIXERONE_USUARIO", motivo)
        self.assertIn("MIXERONE_PASSWORD", motivo)

    def test_con_credenciales(self):
        self.assertEqual(self.p.disponible(), (True, ""))

    def test_sin_credenciales_no_llama_a_la_red(self):
        self.entorno.clear()
        with self.assertRaises(mixerone.MixerOneError):
            self.p.lotes()
        self.assertEqual(self.api.llamadas, [])


class TestSesion(Base):
    def test_login_rechazado_explica_que_revisar(self):
        self.api.login_estado = 401
        with self.assertRaises(mixerone.MixerOneError) as cm:
            self.p.lotes()
        self.assertIn("rechaz", str(cm.exception).lower())
        self.assertIn("MIXERONE_USUARIO", str(cm.exception))

    def test_la_contrasena_nunca_aparece_en_el_error(self):
        self.api.login_estado = 401
        with self.assertRaises(mixerone.MixerOneError) as cm:
            self.p.lotes()
        self.assertNotIn("clave", str(cm.exception))

    def test_token_y_cliente_viajan_en_las_cabeceras(self):
        self.p.lotes()
        ruta, _cuerpo, cab = self.api.llamadas[-1]
        self.assertEqual(cab["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(cab["clientid"], "623")

    def test_el_login_se_reusa_entre_llamadas(self):
        self.p.lotes()
        self.p.ingredientes()
        self.assertEqual(self.api.logins, 1)

    def test_si_el_token_vence_vuelve_a_entrar_una_vez(self):
        self.p.lotes()
        mixerone._cache["sesion"] = (mixerone._cache["sesion"][0], "token-vencido",
                                     mixerone._cache["sesion"][2])
        self.assertTrue(self.p.lotes())
        self.assertEqual(self.api.logins, 2)


class TestCliente(Base):
    """La cuenta tiene varios establecimientos y San José también usa MixerOne: cada tambo tiene que ir SIEMPRE
    al suyo, y si no sabe cuál, avisar en vez de usar el de otro."""

    def test_sin_la_variable_del_tambo_no_adivina_aunque_haya_un_solo_cliente(self):
        del self.entorno["MIXERONE_CLIENTE_PONDEROSA"]
        self.api.clientes = CLIENTES[:1]
        with self.assertRaises(mixerone.MixerOneError) as cm:
            self.p.lotes()
        self.assertIn("MIXERONE_CLIENTE_PONDEROSA", str(cm.exception))
        self.assertEqual([r for r, _, _ in self.api.llamadas if r != "auth/loginuser"], [])

    def test_el_error_nombra_las_opciones_de_la_cuenta(self):
        del self.entorno["MIXERONE_CLIENTE_PONDEROSA"]
        with self.assertRaises(mixerone.MixerOneError) as cm:
            self.p.lotes()
        self.assertIn("623", str(cm.exception))
        self.assertIn("Cereigido La Ponderosa", str(cm.exception))

    def test_otro_tambo_no_usa_el_establecimiento_de_la_ponderosa(self):
        san_jose = mixerone.para("san_jose")
        with self.assertRaises(mixerone.MixerOneError) as cm:
            san_jose.consumos(datetime.date(2026, 11, 7), datetime.date(2026, 11, 7))
        self.assertIn("MIXERONE_CLIENTE_SAN_JOSE", str(cm.exception))
        self.assertEqual([r for r, _, _ in self.api.llamadas if r != "auth/loginuser"], [])

    def test_cada_tambo_manda_su_propio_clientid(self):
        self.entorno["MIXERONE_CLIENTE_SAN_JOSE"] = "607"
        self.p.lotes()
        mixerone.para("san_jose").lotes()
        self.assertEqual([c["clientid"] for r, _, c in self.api.llamadas if r == "corral/getbyfilter"],
                         ["623", "607"])

    def test_disponible_avisa_si_falta_el_establecimiento_del_tambo(self):
        self.assertEqual(self.p.disponible(), (True, ""))
        ok, motivo = mixerone.para("san_jose").disponible()
        self.assertFalse(ok)
        self.assertIn("MIXERONE_CLIENTE_SAN_JOSE", motivo)

    def test_cliente_que_no_es_de_la_cuenta(self):
        self.entorno["MIXERONE_CLIENTE_PONDEROSA"] = "999"
        with self.assertRaises(mixerone.MixerOneError) as cm:
            self.p.lotes()
        self.assertIn("999", str(cm.exception))

    def test_proveedores_de_liga_mixerone_al_tambo(self):
        import proveedores
        with mock.patch("configuracion_tambo.config_de", return_value={"sistema_alimentacion": "mixerone"}):
            prov = proveedores.de("ponderosa")
        self.assertEqual((prov.NOMBRE, prov.tambo), ("MixerOne", "ponderosa"))
        self.assertTrue(prov.lotes())


class TestSoloLectura(Base):
    """MixerOne se consulta con acceso de SOLO LECTURA y está prohibido modificar nada ahí. Casi toda la API usa POST
    hasta para leer, así que el método no sirve de freno: lo que lo garantiza es una lista cerrada de rutas."""

    RUTAS_DE_ESCRITURA = (
        "trips/delete-record", "trips/insert-record", "trips/update-heads-in-range", "trips/UpdateFoodPriceTrip",
        "trips/UpdateMixturePriceTrip", "trips/CreateUnloadFromTrip", "corral/update", "corral/create",
        "corral/disable", "foods/update", "foods/create", "foods/disable", "device/delete", "device/disable",
        "auth/registeruser", "auth/changepassword", "trip-automations/Create", "trip-automations/Delete",
        "punctual-trip-generations/Create", "una/ruta/desconocida",
    )

    def test_ninguna_ruta_de_escritura_sale_a_la_red(self):
        for ruta in self.RUTAS_DE_ESCRITURA:
            with self.subTest(ruta=ruta):
                with self.assertRaises(mixerone.MixerOneError) as cm:
                    mixerone._post(ruta, {}, "ponderosa")
                self.assertIn("solo lectura", str(cm.exception).lower())
        self.assertEqual(self.api.llamadas, [])          # ni siquiera se hizo el login

    def test_todo_lo_que_usa_el_proveedor_es_de_lectura(self):
        self.p.equipos()
        self.p.lotes()
        self.p.ingredientes()
        self.p.consumos(datetime.date(2026, 11, 7), datetime.date(2026, 11, 7))
        usadas = {r for r, _, _ in self.api.llamadas}
        self.assertTrue(usadas <= mixerone.RUTAS_DE_LECTURA, usadas - mixerone.RUTAS_DE_LECTURA)

    def test_la_lista_no_contiene_verbos_de_escritura(self):
        for ruta in mixerone.RUTAS_DE_LECTURA:
            nombre = ruta.rsplit("/", 1)[-1].lower()
            for verbo in ("create", "update", "delete", "insert", "disable", "enable", "register", "change",
                          "backfill", "reset", "associate"):
                self.assertNotIn(verbo, nombre, ruta)


class TestConsumos(Base):
    def consumos(self, desde=datetime.date(2026, 11, 7), hasta=datetime.date(2026, 11, 7)):
        return self.p.consumos(desde, hasta)

    def test_pide_las_fechas_como_aaaa_mm_dd(self):
        self.consumos(datetime.date(2026, 11, 1), datetime.date(2026, 11, 7))
        cuerpo = [c for r, c, _ in self.api.llamadas if r == "trips/GetByFilterPaginated"][0]
        self.assertEqual((cuerpo["startDate"], cuerpo["endDate"]), ("2026-11-01", "2026-11-07"))

    def test_descargas_por_corral(self):
        d = self.consumos()["descargas"]
        self.assertEqual([(x["lote"], x["kg"]) for x in d],
                         [("RODEO 1", 900), ("RODEO 2", 500), ("RODEO 3", 190)])
        self.assertEqual(d[0]["cabezas"], 531)
        self.assertEqual(d[0]["lote_id"], 11416)
        self.assertEqual(d[0]["operacion"], "viaje-1")

    def test_cargas_traen_la_materia_seca_en_porcentaje(self):
        c = self.consumos()["cargas"]
        maiz = [x for x in c if x["ingrediente"] == "Silo Maiz"][0]
        self.assertEqual((maiz["kg"], maiz["ms_pct"], maiz["receta"], maiz["operacion"]),
                         (1000, 40, "ALTA", "viaje-1"))
        self.assertIsNone(maiz["precio"])

    def test_la_fecha_junta_el_dia_del_viaje_con_la_hora_de_la_descarga(self):
        d = self.consumos()["descargas"]
        self.assertEqual(d[0]["fecha"], "2026-11-07T09:52:00")

    def test_una_descarga_pasada_la_medianoche_es_del_dia_siguiente(self):
        d = [x for x in self.consumos()["descargas"] if x["lote"] == "RODEO 3"][0]
        self.assertEqual(d["fecha"], "2026-11-08T00:10:00")

    def test_recorre_todas_las_paginas(self):
        self.api.viajes = [dict(VIAJE_1, trip_uuid=f"v{i}") for i in range(60)]
        ops = {x["operacion"] for x in self.consumos()["descargas"]}
        self.assertEqual(len(ops), 60)
        paginas = [c["page"] for r, c, _ in self.api.llamadas if r == "trips/GetByFilterPaginated"]
        self.assertEqual(paginas, [1, 2, 3])

    def test_sin_viajes_devuelve_listas_vacias(self):
        self.api.viajes = []
        self.assertEqual(self.consumos(), {"descargas": [], "cargas": []})

    def test_un_detalle_sin_kg_no_rompe(self):
        malo = dict(VIAJE_1, trip_uuid="x", details=[dict(descarga("10:00:00", "RODEO 1", None))])
        self.api.viajes = [malo]
        self.assertEqual(self.consumos()["descargas"][0]["kg"], 0)

    def test_funciona_con_la_materia_seca_de_alimentacion(self):
        """Contra el código real que consume el proveedor: nada de mocks de por medio."""
        ms, diag = alimentacion.ms_por_lote_dia(self.consumos())
        # La receta ALTA carga 1.200 kg de maíz y 500 de heno: 1.000*0,40 + 500*0,90 + 200*0,40 = 930 kg de MS
        # sobre 1.700 kg: 54,7 %. Los 900 kg del RODEO 1 llevan 900 * 0,547 de MS.
        pct = diag["recetas"]["ALTA"]
        self.assertAlmostEqual(pct, 54.7, places=1)
        self.assertAlmostEqual(ms[("RODEO 1", datetime.date(2026, 11, 7))], 900 * 930 / 1700, places=3)
        self.assertEqual(diag["kg_sin_receta"], 0)

    def test_kg_por_lote_reconoce_los_corrales_que_reciben_comida(self):
        kg = conciliacion.kg_por_lote(self.consumos())
        self.assertEqual(kg, {"RODEO 1": 900, "RODEO 2": 500, "RODEO 3": 190})


class TestLotesEIngredientes(Base):
    def test_lotes_son_los_corrales_activos_con_sus_cabezas(self):
        por_nombre = {l["lote"]: l for l in self.p.lotes()}
        self.assertEqual(por_nombre["RODEO 1"]["cabezas"], 531)
        self.assertTrue(por_nombre["RODEO 1"]["activo"])
        self.assertEqual(por_nombre["RODEO 1"]["id"], 11416)

    def test_un_corral_sin_cabezas_o_sin_receta_no_esta_activo(self):
        por_nombre = {l["lote"]: l for l in self.p.lotes()}
        self.assertFalse(por_nombre["CHIQUITAS 2"]["activo"])
        self.assertFalse(por_nombre["CHIQUITAS 3"]["activo"])

    def test_un_corral_dado_de_baja_no_aparece(self):
        self.assertNotIn("VIEJO", {l["lote"] for l in self.p.lotes()})

    def test_no_declara_un_mapeo_a_grupos_que_la_api_no_da(self):
        self.assertTrue(all(l["indice_ordene"] is None for l in self.p.lotes()))

    def test_ingredientes_con_materia_seca_y_sin_precio_inventado(self):
        por_nombre = {i["nombre"]: i for i in self.p.ingredientes()}
        soja = por_nombre["Cascara de Soja"]
        self.assertEqual(soja["ms_pct"], 93)
        # La unidad del precio de MixerOne no está verificada: no se propaga como $/kg.
        self.assertIsNone(soja["precio"])
        self.assertEqual(soja["precio_mixerone"], 225)
        self.assertIsNone(por_nombre["Agua"]["precio_mixerone"])

    def test_equipos(self):
        e = self.p.equipos()
        self.assertEqual(e[0]["serial"], "d45e78491da9680d")
        self.assertEqual(e[0]["alias"], "Mixer")


if __name__ == "__main__":
    unittest.main()
