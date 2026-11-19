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


def descarga(hora, corral, kg, cabezas=None, receta="ALTA", ciclo="07:30"):
    return {"id": 2, "action": "Descarga", "operator": "Daniel Monteros", "kg_real": kg, "kg_theoretical": kg,
            "hour": hora, "corral": corral, "corral_id": 11416, "recipe": receta, "feedCycle": ciclo,
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


def viaje_tablero(creado, teorica, carga, descarga):
    """Un viaje como lo devuelve `dashboard/getTripsDashboard`: con sus totales ya sumados."""
    return {"trip_uuid": "t-" + creado, "created": creado, "growth_stage": "Ordeñe",
            "total_kg_theoretical_load": teorica, "Carga": {"Real": carga}, "Descarga": {"Real": descarga}}


CONTADORES = {"milkingCows": 1864, "generalAverage": 41.15, "conversion": 1.8, "msRealHead": 22.82}
VIAJES_TABLERO = [
    viaje_tablero("2026-11-03T06:33:24.000Z", 8760.0, 8960.09, 8925.0),
    viaje_tablero("2026-11-03T14:10:00.000Z", 7000.0, 7100.0, 6900.0),
    viaje_tablero("2026-11-04T07:00:00.000Z", 9000.0, 9200.0, 9100.0),
]
PUNTAJE = {"score_final": 8.74, "score_percentages": {"bien": 64.93, "regular": 25.19, "mal": 9.88},
           "operators": [
               {"name": "Daniel Monteros", "loads": {"total": 342, "score": 8.83},
                "unloads": {"total": 334, "score": 9.17},
                "general": {"total": 676, "score": 8.99, "score_result": "REGULAR"}},
               {"name": "Victor Aguero", "loads": {"total": 153, "score": 8.97},
                "unloads": {"total": 0, "score": None},
                "general": {"total": 153, "score": 8.97, "score_result": "REGULAR"}}]}
ALERTAS = [{"date": "2026-11-03T00:00:00.000Z", "count": 6}, {"date": "2026-11-05T00:00:00.000Z", "count": 29}]


class ApiFalsa:
    """Se hace pasar por mixerone.net. Anota cada llamada para poder mirarla después."""

    def __init__(self, clientes=CLIENTES, viajes=None, login_estado=201, por_pagina=25):
        self.clientes = clientes
        self.viajes = viajes if viajes is not None else [VIAJE_1, VIAJE_2]
        self.login_estado = login_estado
        self.por_pagina = por_pagina
        self.llamadas = []
        self.logins = 0
        # {ruta: (estado HTTP, response)}: lo que contesta cada ruta del tablero. Se pisa en cada prueba.
        self.tablero = {
            "dashboard/getCountersDashboard": (201, CONTADORES),
            "dashboard/getTripsDashboard": (201, VIAJES_TABLERO),
            "dashboard/getOperatorsScoreDashboard": (201, PUNTAJE),
            "notifications/alerts/daily-counts": (200, ALERTAS),
        }
        self.metodos = []
        self.notificaciones = []         # las alertas que devuelve `GET notifications`

    def get(self, url, params=None, headers=None, timeout=None, **_):
        ruta = url.split("/api/", 1)[1]
        self.metodos.append(("GET", ruta))
        self.llamadas.append((ruta, params, dict(headers or {})))
        if ruta == "notifications" and ruta not in self.tablero:      # el listado de alertas, paginado como el real
            pagina, limite = int(params["page"]), int(params["limit"])
            lote = self.notificaciones[(pagina - 1) * limite:pagina * limite]
            return Respuesta({"status": True, "response": {
                "items": lote, "total": len(self.notificaciones), "page": pagina, "limit": limite,
                "page_count": max(1, -(-len(self.notificaciones) // limite))}}, 200)
        return self._tablero(ruta)

    def _tablero(self, ruta):
        if ruta not in self.tablero:
            return Respuesta({"status": False}, 404)
        estado, respuesta = self.tablero[ruta]
        if estado not in (200, 201):
            return Respuesta({"status": False, "detail": "sin permiso"}, estado)
        return Respuesta({"status": True, "response": respuesta}, estado)

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
        self.metodos.append(("POST", ruta))
        if ruta.startswith("dashboard/"):
            return self._tablero(ruta)
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
            mock.patch.object(mixerone.requests.Session, "get", lambda s, *a, **k: self.api.get(*a, **k)),
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

    def test_el_tablero_solo_usa_rutas_de_lectura(self):
        self.p.tablero()
        post = {r for m, r in self.api.metodos if m == "POST"}
        get = {r for m, r in self.api.metodos if m == "GET"}
        self.assertTrue(post <= mixerone.RUTAS_DE_LECTURA, post - mixerone.RUTAS_DE_LECTURA)
        self.assertTrue(get <= mixerone.RUTAS_GET_DE_LECTURA, get - mixerone.RUTAS_GET_DE_LECTURA)

    def test_un_GET_a_una_ruta_que_no_esta_en_la_lista_no_sale_a_la_red(self):
        for ruta in ("notifications/read-all", "notifications/settings/x", "auth/logout", "una/ruta/desconocida"):
            with self.subTest(ruta=ruta):
                with self.assertRaises(mixerone.MixerOneError) as cm:
                    mixerone._get(ruta, "ponderosa")
                self.assertIn("solo lectura", str(cm.exception).lower())
        self.assertEqual(self.api.llamadas, [])

    def test_la_lista_de_GET_no_contiene_verbos_de_escritura(self):
        for ruta in mixerone.RUTAS_GET_DE_LECTURA:
            for verbo in ("create", "update", "delete", "insert", "disable", "enable", "register", "change",
                          "backfill", "reset", "associate", "read-all", "markread"):
                self.assertNotIn(verbo, ruta.lower(), ruta)

    def test_la_lista_no_contiene_verbos_de_escritura(self):
        for ruta in mixerone.RUTAS_DE_LECTURA:
            nombre = ruta.rsplit("/", 1)[-1].lower()
            for verbo in ("create", "update", "delete", "insert", "disable", "enable", "register", "change",
                          "backfill", "reset", "associate"):
                self.assertNotIn(verbo, nombre, ruta)


class TestTablero(Base):
    """El tablero principal del portal (tarjetas, «Listado de viajes» con sus alertas y puntaje de operarios), en la
    forma neutral que cualquier proveedor puede dar. Cifras de La Ponderosa, 18/11/2026."""

    def test_las_tarjetas_son_las_que_informa_mixerone(self):
        k = self.p.tablero()["kpis"]
        self.assertEqual(k, {"vacas": 1864, "leche_vaca": 41.15, "ms_vaca": 22.82, "conversion": 1.8})

    def test_los_viajes_se_suman_por_dia_con_los_totales_de_cada_viaje(self):
        dias = {d["fecha"]: d for d in self.p.tablero()["viajes"]}
        self.assertEqual(list(dias), ["2026-11-03", "2026-11-04"])
        self.assertEqual(dias["2026-11-03"]["teorica"], 15760.0)
        self.assertAlmostEqual(dias["2026-11-03"]["carga_real"], 16060.09)
        self.assertEqual(dias["2026-11-03"]["descarga_real"], 15825.0)
        self.assertEqual(dias["2026-11-04"]["descarga_real"], 9100.0)

    def test_las_alertas_se_pegan_al_dia_y_un_dia_sin_alertas_es_cero(self):
        dias = {d["fecha"]: d["alertas"] for d in self.p.tablero()["viajes"]}
        self.assertEqual(dias, {"2026-11-03": 6, "2026-11-04": 0})

    def test_las_alertas_se_piden_con_GET_y_el_resto_con_POST(self):
        self.p.tablero()
        self.assertIn(("GET", "notifications/alerts/daily-counts"), self.api.metodos)
        self.assertIn(("POST", "dashboard/getTripsDashboard"), self.api.metodos)
        self.assertNotIn(("POST", "notifications/alerts/daily-counts"), self.api.metodos)

    def test_el_puntaje_de_operarios(self):
        o = self.p.tablero()["operarios"]
        self.assertEqual(o["puntaje"], 8.74)
        self.assertEqual(o["porcentajes"], {"bien": 64.93, "regular": 25.19, "mal": 9.88})
        self.assertEqual(o["lista"][0], {"nombre": "Daniel Monteros", "puntaje": 8.99, "resultado": "REGULAR",
                                         "cargas": 342, "descargas": 334})
        self.assertEqual(o["lista"][1]["descargas"], 0)

    def test_sin_permiso_para_las_alertas_el_resto_sale_igual_y_lo_avisa(self):
        self.api.tablero["notifications/alerts/daily-counts"] = (403, None)
        t = self.p.tablero()
        self.assertTrue(all(d["alertas"] is None for d in t["viajes"]))
        self.assertEqual(t["kpis"]["vacas"], 1864)
        self.assertTrue(any("alertas" in a.lower() for a in t["avisos"]), t["avisos"])

    def test_si_un_bloque_falla_los_demas_siguen(self):
        self.api.tablero["dashboard/getCountersDashboard"] = (500, None)
        t = self.p.tablero()
        self.assertIsNone(t["kpis"])
        self.assertEqual(len(t["viajes"]), 2)
        self.assertEqual(t["operarios"]["puntaje"], 8.74)
        self.assertEqual(len(t["avisos"]), 1)

    def test_si_falla_todo_lo_dice_en_vez_de_devolver_un_tablero_vacio(self):
        for ruta in list(self.api.tablero):
            self.api.tablero[ruta] = (500, None)
        with self.assertRaises(mixerone.MixerOneError):
            self.p.tablero()

    def test_un_viaje_sin_totales_no_rompe_y_cuenta_cero(self):
        self.api.tablero["dashboard/getTripsDashboard"] = (201, [
            {"created": "2026-11-03T06:00:00.000Z", "Carga": None, "Descarga": {}}])
        d = self.p.tablero()["viajes"][0]
        self.assertEqual((d["teorica"], d["carga_real"], d["descarga_real"]), (0, 0, 0))

    def test_el_nombre_del_proveedor_viaja(self):
        self.assertEqual(self.p.tablero()["proveedor"], "MixerOne")


def alerta_api(id_, codigo, ocurrio, titulo, mensaje, metadata=None, severidad="warning"):
    """Una alerta como la devuelve `GET notifications` (La Ponderosa, 13/11/2026). `ocurrio` en UTC."""
    return {"id": str(id_), "is_alert": True, "read_at": None, "created_at": ocurrio, "code": codigo,
            "severity": severidad, "title": titulo, "message": mensaje, "metadata": metadata or {},
            "occurred_at": ocurrio}


def detalle(accion, operario, kg, teo, comida=None, corral=None):
    return {"id": 1, "action": accion, "operator": operario, "kg_real": kg, "kg_theoretical": teo, "food": comida,
            "corral": corral, "hour": "07:00:00"}


# T-A: José carga Cáscara y Heno, Daniel descarga en RODEO 4. T-B: Víctor carga, Daniel descarga en RODEO 1.
VIAJES_CON_DETALLE = [
    {"trip_uuid": "T-A", "created": "2026-11-12T07:00:00.000Z", "growth_stage": "Ordeñe", "details": [
        detalle("Carga", "Jose Monteros", 520, 500, comida="Cáscara"), detalle("Carga", "Jose Monteros", 480, 500, comida="Heno"),
        detalle("Descarga", "Daniel Monteros", 900, 900, corral="RODEO 4")]},
    {"trip_uuid": "T-B", "created": "2026-11-13T08:00:00.000Z", "growth_stage": "Ordeñe", "details": [
        detalle("Carga", "Victor Aguero", 100, 100, comida="Heno"), detalle("Descarga", "Daniel Monteros", 100, 100, corral="RODEO 1")]},
]
ALERTAS_API = [
    alerta_api(1, "N06", "2026-11-13T13:05:00.000Z", "Tiempo de carga menor al límite",
               "Mixer: 18 segundos entre cargas; límite 30 segundos", {"trip_uuid": "T-A", "item_name": "Cáscara"}),
    alerta_api(2, "N04", "2026-11-14T01:30:00.000Z", "Descarga fuera del corral",
               "La descarga en RODEO 4 se registró a 575 m de la ubicación esperada",
               {"trip_uuid": "T-A", "corral_name": "RODEO 4"}),
    alerta_api(3, "N02", "2026-11-13T15:00:00.000Z", "Kilogramos de corral modificados",
               "Daniel Monteros modificó RODEO 4: 4059 → 4500", {"corral_id": 11419}),
    alerta_api(4, "N12", "2026-11-13T03:10:00.000Z", "Consumo: variación en la oferta",
               "R9 ENFERMERIA: Se ofreció +52,58%", {"corral_name": "R9 ENFERMERIA"}),
    alerta_api(5, "N13", "2026-11-13T12:00:00.000Z", "Corral no alimentado",
               "VAQ PREPARTO: no se registró ninguna descarga el 12/11/2026.",
               {"corral_name": "VAQ PREPARTO", "detected_trip_uuid": "T-B"}),
    alerta_api(6, "N09", "2026-11-13T12:00:00.000Z", "Proyección de stock baja",
               "Expeller de Soja tiene 2 días de stock proyectados", {"item_name": "Expeller de Soja"}, "error"),
]


class TestAlertasYOperarios(Base):
    """Las alertas de MixerOne traducidas a la forma neutral, y el análisis por operario. Solo lectura: se pide el
    LISTADO (`GET notifications`); marcarlas como leídas es otra ruta y no se toca."""

    def setUp(self):
        super().setUp()
        self.api.notificaciones = list(ALERTAS_API)
        self.api.tablero["dashboard/getTripsDashboard"] = (201, VIAJES_CON_DETALLE)

    def alertas(self):
        return {a["id"]: a for a in self.p.alertas(datetime.date(2026, 11, 12), datetime.date(2026, 11, 13))}

    def test_se_piden_solo_alertas_con_las_fechas_y_sin_marcar_nada(self):
        self.alertas()
        _, params, _ = next(l for l in self.api.llamadas if l[0] == "notifications")
        self.assertEqual((params["is_alert"], params["start_date"], params["end_date"]),
                         ("true", "2026-11-12", "2026-11-13"))
        self.assertEqual(params["unread_only"], "false")
        self.assertTrue(all(m == "GET" for m, r in self.api.metodos if r.startswith("notifications")))

    def test_el_dia_es_hora_argentina_no_utc(self):
        a = self.alertas()
        self.assertEqual((a["1"]["fecha"], a["1"]["hora"]), ("2026-11-13", "10:05"))
        self.assertEqual((a["2"]["fecha"], a["2"]["hora"]), ("2026-11-13", "22:30"))      # 01:30Z es el día anterior
        self.assertEqual((a["4"]["fecha"], a["4"]["hora"]), ("2026-11-13", "00:10"))

    def test_que_accion_provoco_cada_alerta(self):
        a = self.alertas()
        self.assertEqual(a["1"]["accion"], "carga")
        self.assertEqual(a["2"]["accion"], "descarga")
        self.assertEqual(a["3"]["accion"], "edicion")
        for i in ("4", "5", "6"):                       # variación de consumo, rodeo sin alimentar, stock
            self.assertIsNone(a[i]["accion"], i)

    def test_viaje_rodeo_e_insumo(self):
        a = self.alertas()
        self.assertEqual((a["1"]["viaje"], a["1"]["insumo"]), ("T-A", "Cáscara"))
        self.assertEqual((a["2"]["viaje"], a["2"]["rodeo"]), ("T-A", "RODEO 4"))
        self.assertEqual(a["6"]["insumo"], "Expeller de Soja")

    def test_la_edicion_trae_el_operario_del_mensaje(self):
        self.assertEqual(self.alertas()["3"]["operario"], "Daniel Monteros")

    def test_titulo_mensaje_y_severidad(self):
        a = self.alertas()
        self.assertEqual(a["6"]["tipo"], "Proyección de stock baja")
        self.assertEqual(a["6"]["severidad"], "error")
        self.assertIn("575 m", a["2"]["mensaje"])

    def test_con_crudo_trae_el_json_original_para_la_copia(self):
        a = {x["id"]: x for x in self.p.alertas(datetime.date(2026, 11, 12), datetime.date(2026, 11, 13), crudo=True)}
        self.assertEqual(a["2"]["crudo"]["metadata"]["corral_name"], "RODEO 4")
        self.assertEqual(a["2"]["crudo"]["code"], "N04")
        self.assertEqual(a["2"]["crudo"]["occurred_at"], "2026-11-14T01:30:00.000Z")      # el instante en UTC, intacto

    def test_sin_pedirlo_no_incluye_el_crudo(self):
        self.assertNotIn("crudo", self.alertas()["1"])

    def test_recorre_todas_las_paginas(self):
        self.api.notificaciones = [alerta_api(i, "N06", "2026-11-13T13:00:00.000Z", "t", "m",
                                              {"trip_uuid": "T-A", "item_name": "Heno"}) for i in range(250)]
        self.assertEqual(len(self.p.alertas(datetime.date(2026, 11, 12), datetime.date(2026, 11, 13))), 250)

    def test_el_analisis_atribuye_cada_alerta_a_quien_la_provoco(self):
        r = self.p.operarios()
        por = {o["nombre"]: o for o in r["operarios"]}
        self.assertEqual(por["Jose Monteros"]["alertas_por_tipo"], {"N06": 1})
        self.assertEqual(por["Daniel Monteros"]["alertas_por_tipo"], {"N04": 1, "N02": 1})
        self.assertEqual(r["sin_operario"]["por_tipo"], {"N09": 1, "N12": 1, "N13": 1})
        self.assertEqual(r["proveedor"], "MixerOne")

    def test_la_ventana_es_la_de_los_viajes_del_tablero(self):
        v = self.p.operarios()["ventana"]
        self.assertEqual((v["desde"], v["hasta"]), ("2026-11-12", "2026-11-13"))
        _, params, _ = next(l for l in self.api.llamadas if l[0] == "notifications")
        self.assertEqual((params["start_date"], params["end_date"]), ("2026-11-12", "2026-11-13"))

    def test_el_puntaje_es_el_que_informa_mixerone(self):
        r = self.p.operarios()
        self.assertEqual(r["resumen"]["puntaje"], 8.74)
        por = {o["nombre"]: o for o in r["operarios"]}
        self.assertEqual((por["Daniel Monteros"]["puntaje"], por["Daniel Monteros"]["puntaje_descargas"]), (8.99, 9.17))
        self.assertIsNone(por["Jose Monteros"]["puntaje"])        # MixerOne no lo trae en este ejemplo
        self.assertEqual(r["resumen"]["porcentajes"]["bien"], 64.93)

    def test_sin_permiso_para_las_alertas_el_analisis_sale_y_lo_avisa(self):
        self.api.tablero["notifications"] = (403, None)
        r = self.p.operarios()
        self.assertEqual(r["alertas"], [])
        self.assertTrue(any("alertas" in a.lower() for a in r["avisos"]), r["avisos"])
        self.assertTrue(r["operarios"])           # los operarios y sus acciones salen igual

    def test_si_falla_todo_lo_dice(self):
        self.api.tablero["dashboard/getTripsDashboard"] = (500, None)
        self.api.tablero["dashboard/getOperatorsScoreDashboard"] = (500, None)
        self.api.tablero["notifications"] = (500, None)
        with self.assertRaises(mixerone.MixerOneError):
            self.p.operarios()

    def test_la_primera_comida_de_cada_dia_trae_quien_la_hizo(self):
        self.api.viajes = [
            viaje("v1", "2026-11-12T07:00:00.000Z", [carga("07:00:00", "Heno", 100, 100, 88),
                                                     descarga("07:04:00", "RODEO 1", 900, 531)]),
            viaje("v2", "2026-11-13T07:30:00.000Z", [carga("07:30:00", "Heno", 100, 100, 88),
                                                     descarga("07:40:00", "RODEO 1", 900, 531)]),
            viaje("v0", "2026-11-05T07:00:00.000Z", [carga("07:00:00", "Heno", 100, 100, 88),
                                                     descarga("07:02:00", "RODEO 1", 900, 531)]),   # antes de la ventana
        ]
        r = self.p.operarios()
        self.assertEqual([(c["fecha"], c["operario"]) for c in r["primeras_comidas"]],
                         [("2026-11-12", "Daniel Monteros"), ("2026-11-13", "Daniel Monteros")])
        self.assertEqual(r["primeras_comidas"][0]["primera"], 7 * 60 + 4)

    def test_si_falla_la_primera_comida_el_resto_sale_y_lo_avisa(self):
        with mock.patch.object(mixerone, "_viajes", side_effect=mixerone.MixerOneError("cortado")):
            r = self.p.operarios()
        self.assertEqual(r["primeras_comidas"], [])
        self.assertTrue(any("primera comida" in a.lower() for a in r["avisos"]), r["avisos"])
        self.assertTrue(r["operarios"])

    def test_el_rodeo_de_una_edicion_de_kg_sale_del_despues(self):
        """En «kg modificados» el rodeo no está arriba: viene dentro de `before`/`after`."""
        self.api.notificaciones = [alerta_api(
            9, "N02", "2026-11-13T15:00:00.000Z", "Kilogramos de corral modificados",
            "Daniel Monteros modificó RODEO 4: 4059 → 4500",
            {"before": {"corral_name": "RODEO 4", "kg": 4059}, "after": {"corral_name": "RODEO 4", "kg": 4500}})]
        self.assertEqual(self.alertas()["9"]["rodeo"], "RODEO 4")

    def test_el_dia_del_que_habla_la_alerta(self):
        """«Rodeo sin alimentar» se detecta al día siguiente, pero es del día que faltó (`missing_date`); la variación
        de consumo trae el suyo en `date`; el resto es del día en que pasó."""
        self.api.notificaciones = [
            alerta_api(1, "N13", "2026-11-13T12:00:00.000Z", "t", "m", {"corral_name": "X", "missing_date": "2026-11-12"}),
            alerta_api(2, "N12", "2026-11-13T12:00:00.000Z", "t", "m", {"corral_name": "X", "date": "2026-11-12"}),
            alerta_api(3, "N07", "2026-11-13T12:00:00.000Z", "t", "m", {"corral_name": "X"})]
        a = self.alertas()
        self.assertEqual([a["1"]["dia"], a["2"]["dia"], a["3"]["dia"]], ["2026-11-12", "2026-11-12", "2026-11-13"])

    def test_el_analisis_solo_usa_rutas_de_lectura(self):
        self.p.operarios()
        post = {r for m, r in self.api.metodos if m == "POST"}
        get = {r for m, r in self.api.metodos if m == "GET"}
        self.assertTrue(post <= mixerone.RUTAS_DE_LECTURA, post - mixerone.RUTAS_DE_LECTURA)
        self.assertTrue(get <= mixerone.RUTAS_GET_DE_LECTURA, get - mixerone.RUTAS_GET_DE_LECTURA)
        self.assertNotIn("notifications/read-all", get)


def descarga_en(hora, rodeo, kg, teo, operario="Daniel Monteros"):
    return {"id": 1, "action": "Descarga", "operator": operario, "kg_real": kg, "kg_theoretical": teo, "corral": rodeo,
            "hour": hora, "food": None}


def viaje_de(dia, creado_hhmm, detalles, etapa="Ordeñe"):
    return {"trip_uuid": f"V-{dia}-{creado_hhmm}", "created": f"{dia}T{creado_hhmm}:00.000Z", "growth_stage": etapa,
            "details": detalles}


class TestEntregasPorRodeo(Base):
    """La entrega de comida por rodeo y día (kg contra teóricos, primera comida, alertas) a partir del tablero de
    MixerOne. El cálculo es de `entrega.py`; acá se prueba que el proveedor le pase lo correcto."""

    def setUp(self):
        super().setUp()
        self.api.notificaciones = []
        self.api.tablero["dashboard/getTripsDashboard"] = (201, [
            viaje_de("2026-11-10", "07:00", [descarga_en("07:04:00", "RODEO 1", 900, 1000),
                                             descarga_en("09:00:00", "RODEO 2", 500, 500)]),
            viaje_de("2026-11-11", "07:00", [descarga_en("07:04:00", "RODEO 1", 1000, 1000)]),
            viaje_de("2026-11-12", "07:00", [descarga_en("07:04:00", "RODEO 1", 1000, 1000)]),
            viaje_de("2026-11-13", "09:00", [descarga_en("09:04:00", "RODEO 1", 1100, 1000)]),
            viaje_de("2026-11-13", "08:00", [descarga_en("08:00:00", "RECRIA LP", 300, 300)], etapa="Recría"),
        ])

    def rodeos(self):
        return {r["rodeo"]: r for r in self.p.entregas_por_rodeo()["rodeos"]}

    def dias(self, rodeo):
        return {d["fecha"]: d for d in self.rodeos()[rodeo]["dias"]}

    def test_suma_lo_entregado_y_lo_teorico_por_rodeo_y_dia(self):
        d = self.dias("RODEO 1")["2026-11-10"]
        self.assertEqual((d["kg_real"], d["kg_teorico"], d["cumplimiento"]), (900.0, 1000.0, 90.0))

    def test_la_primera_comida_en_minutos_y_el_atraso_contra_lo_habitual_del_rodeo(self):
        r = self.rodeos()["RODEO 1"]
        self.assertEqual(r["habitual"], 7 * 60 + 4)
        d = {x["fecha"]: x for x in r["dias"]}
        self.assertEqual(d["2026-11-10"]["primera"], 7 * 60 + 4)
        self.assertEqual(d["2026-11-13"]["atraso"], 120)               # 09:04 contra 07:04

    def test_solo_los_rodeos_de_ordene(self):
        self.assertNotIn("RECRIA LP", self.rodeos())

    def test_un_rodeo_con_pocos_dias_no_tiene_lo_habitual(self):
        r = self.rodeos()["RODEO 2"]
        self.assertIsNone(r["habitual"])
        self.assertIsNone(r["dias"][0]["atraso"])

    def test_las_alertas_se_cuentan_en_el_dia_y_rodeo_de_los_que_hablan(self):
        self.api.notificaciones = [
            alerta_api(1, "N07", "2026-11-13T12:30:00.000Z", "Horario", "m",
                       {"trip_uuid": "V", "corral_name": "RODEO 1"}),                       # 09:30 del 13
            alerta_api(2, "N13", "2026-11-13T12:00:00.000Z", "Rodeo sin alimentar", "m",
                       {"corral_name": "RODEO 2", "missing_date": "2026-11-11"})]
        r = self.rodeos()
        self.assertEqual({d["fecha"]: d["alertas"] for d in r["RODEO 1"]["dias"]}["2026-11-13"], 1)
        r2 = {d["fecha"]: d for d in r["RODEO 2"]["dias"]}
        self.assertNotIn("2026-11-11", r2)      # Rodeo 2 solo comió el 10: su alerta del 11 queda fuera de su rango

    def test_la_ventana_y_el_proveedor(self):
        e = self.p.entregas_por_rodeo()
        self.assertEqual((e["ventana"]["desde"], e["ventana"]["hasta"]), ("2026-11-10", "2026-11-13"))
        self.assertEqual(e["proveedor"], "MixerOne")

    def test_si_fallan_las_alertas_la_entrega_sale_igual_y_lo_avisa(self):
        self.api.tablero["notifications"] = (403, None)
        e = self.p.entregas_por_rodeo()
        self.assertTrue(e["rodeos"])
        self.assertTrue(any("alertas" in a.lower() for a in e["avisos"]), e["avisos"])

    def test_si_falla_el_tablero_de_viajes_es_un_error(self):
        self.api.tablero["dashboard/getTripsDashboard"] = (500, None)
        with self.assertRaises(mixerone.MixerOneError):
            self.p.entregas_por_rodeo()

    def test_solo_usa_rutas_de_lectura(self):
        self.p.entregas_por_rodeo()
        post = {r for m, r in self.api.metodos if m == "POST"}
        get = {r for m, r in self.api.metodos if m == "GET"}
        self.assertTrue(post <= mixerone.RUTAS_DE_LECTURA, post - mixerone.RUTAS_DE_LECTURA)
        self.assertTrue(get <= mixerone.RUTAS_GET_DE_LECTURA, get - mixerone.RUTAS_GET_DE_LECTURA)


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

    def test_cada_movimiento_trae_la_etapa_y_la_hora_prevista_del_viaje(self):
        c = self.consumos()
        d = c["descargas"][0]
        self.assertEqual((d["etapa"], d["ciclo"]), ("Ordeñe", "07:30"))
        self.assertTrue(all(x["etapa"] == "Ordeñe" for x in c["cargas"]))

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
