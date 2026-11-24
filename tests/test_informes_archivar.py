"""Archivar y eliminar informes guardados (24/11/2026).

Al traer varios informes de DelPro de una vez la lista puede quedar llena de informes que no se usan. LactIA nunca tuvo «eliminar» (regla de no borrar), así que:
  * ARCHIVAR saca el informe de la lista y se puede deshacer (RESTAURAR). Un informe archivado no se edita, no se programa y no manda nada: archivarlo pausa su envío.
  * ELIMINAR para siempre solo se puede con un informe ya archivado (dos pasos a propósito) y borra también su programación de envío y el registro de lo enviado.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_archivar -v
"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app
import informes_envios as ie
import informes_guardados as ig

T, OTRO = "ponderosa", "san_jose"
DEF = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "informes.db")
        p = mock.patch.object(ig, "RUTA_DB", self.ruta)
        p.start()
        self.addCleanup(p.stop)


class TestArchivar(Base):
    def test_un_informe_archivado_sale_de_la_lista_y_aparece_en_los_archivados(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.crear(T, "Dos", DEF, "maxi")
        r = ig.archivar(T, a["id"])
        self.assertTrue(r["archivado"])
        self.assertEqual([i["nombre"] for i in ig.listar(T)], ["Dos"])
        arch = ig.listar(T, archivados=True)
        self.assertEqual([i["nombre"] for i in arch], ["Uno"])
        self.assertTrue(arch[0]["archivado"])

    def test_sin_archivar_nada_los_informes_no_tienen_fecha_de_archivo(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        self.assertIsNone(a["archivado"])
        self.assertEqual(ig.listar(T, archivados=True), [])

    def test_restaurar_lo_devuelve_a_la_lista(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.archivar(T, a["id"])
        r = ig.restaurar(T, a["id"])
        self.assertIsNone(r["archivado"])
        self.assertEqual([i["nombre"] for i in ig.listar(T)], ["Uno"])
        self.assertEqual(ig.listar(T, archivados=True), [])

    def test_archivar_y_restaurar_dos_veces_no_cambia_nada(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        primera = ig.archivar(T, a["id"])["archivado"]
        self.assertEqual(ig.archivar(T, a["id"])["archivado"], primera)          # la fecha de cuando se archivó no se pisa
        ig.restaurar(T, a["id"])
        self.assertIsNone(ig.restaurar(T, a["id"])["archivado"])

    def test_un_informe_que_no_existe_o_es_de_otro_tambo(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        for f in (ig.archivar, ig.restaurar, ig.eliminar):
            with self.assertRaises(ig.InformeGuardadoError):
                f(T, 9999)
            with self.assertRaises(ig.InformeGuardadoError):
                f(OTRO, a["id"])
            with self.assertRaises(ig.InformeGuardadoError):
                f(T, 2 ** 70)                                                       # un id que SQLite no puede guardar

    def test_un_archivado_no_se_edita(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.archivar(T, a["id"])
        with self.assertRaises(ig.InformeGuardadoError) as e:
            ig.actualizar(T, a["id"], nombre="Otro")
        self.assertIn("archivado", str(e.exception))
        ig.restaurar(T, a["id"])
        self.assertEqual(ig.actualizar(T, a["id"], nombre="Otro")["nombre"], "Otro")

    def test_el_nombre_de_un_archivado_sigue_ocupado_y_el_aviso_lo_dice(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.archivar(T, a["id"])
        with self.assertRaises(ig.InformeGuardadoError) as e:
            ig.crear(T, "Uno", DEF, "maxi")
        self.assertIn("archivado", str(e.exception))
        self.assertIn("Restauralo", str(e.exception))

    def test_se_puede_leer_uno_archivado_por_su_id(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.archivar(T, a["id"])
        self.assertTrue(ig.obtener(T, a["id"])["archivado"])


class TestEliminar(Base):
    def test_solo_se_elimina_un_informe_archivado(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        with self.assertRaises(ig.InformeGuardadoError) as e:
            ig.eliminar(T, a["id"])
        self.assertIn("Archivalo", str(e.exception))
        self.assertIsNotNone(ig.obtener(T, a["id"]))                                # no se tocó

    def test_eliminar_lo_borra_de_verdad_y_libera_el_nombre(self):
        a = ig.crear(T, "Uno", DEF, "maxi")
        ig.archivar(T, a["id"])
        ig.eliminar(T, a["id"])
        self.assertIsNone(ig.obtener(T, a["id"]))
        self.assertEqual((ig.listar(T), ig.listar(T, archivados=True)), ([], []))
        otro = ig.crear(T, "Uno", DEF, "maxi")                                       # el nombre quedó libre
        self.assertNotEqual(otro["id"], a["id"])                                     # y el número no se reutiliza (el registro de envíos nunca confunde dos informes)

    def test_no_toca_a_los_demas(self):
        a, b = ig.crear(T, "Uno", DEF, "maxi"), ig.crear(T, "Dos", DEF, "maxi")
        ig.archivar(T, a["id"])
        ig.eliminar(T, a["id"])
        self.assertEqual([i["nombre"] for i in ig.listar(T)], ["Dos"])
        self.assertIsNotNone(ig.obtener(T, b["id"]))


class TestUnaBaseVieja(Base):
    def test_se_agrega_la_columna_y_lo_guardado_sigue_estando(self):
        con = sqlite3.connect(self.ruta)
        con.execute("""CREATE TABLE informes (id INTEGER PRIMARY KEY AUTOINCREMENT, tambo TEXT NOT NULL, nombre TEXT NOT NULL, nombre_norm TEXT NOT NULL,
            definicion TEXT NOT NULL, favorito INTEGER NOT NULL DEFAULT 0, creado TEXT NOT NULL, modificado TEXT NOT NULL, usuario TEXT, origen TEXT, UNIQUE (tambo, nombre_norm))""")
        con.execute("INSERT INTO informes (tambo, nombre, nombre_norm, definicion, creado, modificado) VALUES (?,?,?,?,?,?)",
                    (T, "Viejo", "viejo", '{"columnas": [{"campo": "Number"}]}', "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"))
        con.commit()
        con.close()
        self.assertEqual([i["nombre"] for i in ig.listar(T)], ["Viejo"])
        v = ig.listar(T)[0]
        ig.archivar(T, v["id"])
        self.assertEqual(ig.listar(T), [])
        self.assertEqual([i["nombre"] for i in ig.listar(T, archivados=True)], ["Viejo"])


class TestLosEnvios(Base):
    def setUp(self):
        super().setUp()
        self.a = ig.crear(T, "Uno", DEF, "maxi")
        ie.guardar(T, self.a["id"], [0, 1], ["08:00"], ["correo"], "excel", True, "maxi")

    def test_archivar_pausa_el_envio_y_no_lo_borra(self):
        ig.archivar(T, self.a["id"])
        ie.pausar(T, self.a["id"])                                                   # lo que hace la ruta
        e = ie.obtener(T, self.a["id"])
        self.assertIsNotNone(e)
        self.assertFalse(e["activo"])

    def test_un_informe_archivado_no_se_puede_programar_ni_calcular_para_un_envio(self):
        ig.archivar(T, self.a["id"])
        with self.assertRaises(ie.EnvioError) as e:
            ie.guardar(T, self.a["id"], [0], ["09:00"], ["correo"], "excel", True, "maxi")
        self.assertIn("archivado", str(e.exception))
        with self.assertRaises(ie.EnvioError):
            ie.armar_de_la_base(T, self.a["id"], "excel", ejecutar=lambda *a, **k: self.fail("no se calcula un informe archivado"))

    def test_la_pasada_no_manda_un_informe_archivado_aunque_su_envio_siga_activo(self):
        import datetime
        ie.guardar(T, self.a["id"], [0], ["08:00"], ["correo"], "excel", True, "maxi", ahora=datetime.datetime(2026, 11, 22, 12, 0))    # programado el domingo para el lunes 23/11 a las 08:00
        ig.archivar(T, self.a["id"])                                                 # sin pausar a propósito: la defensa tiene que ser doble
        enviados = []
        ie.pasada(lambda canal, armado: enviados.append(canal), lambda: ["correo"], ahora=datetime.datetime(2026, 11, 23, 8, 1),
                  ejecutar=lambda *a, **k: self.fail("no se calcula un informe archivado"))
        self.assertEqual(enviados, [])
        self.assertTrue([r for r in ie.registro(T, self.a["id"])], "el horario no pasa en silencio: queda en el registro")

    def test_borrar_el_envio_de_un_informe_saca_la_programacion_y_el_registro(self):
        con = sqlite3.connect(self.ruta)
        eid = ie.obtener(T, self.a["id"])["id"]
        con.execute("INSERT INTO envios_registro (envio_id, slot, canal, estado, intentos, inicio, detalle) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (eid, "2026-11-23T08:00:00", "correo", "enviado", 1, "2026-11-23T08:00:01", ""))
        con.commit()
        con.close()
        self.assertEqual(len(ie.registro(T, self.a["id"])), 1)
        self.assertTrue(ie.borrar_de_informe(T, self.a["id"]))
        self.assertIsNone(ie.obtener(T, self.a["id"]))
        con = sqlite3.connect(self.ruta)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM envios_registro").fetchone()[0], 0)
        con.close()
        self.assertFalse(ie.borrar_de_informe(T, self.a["id"]))                      # sin programación no hay nada que borrar

    def test_borrar_el_envio_de_un_informe_no_toca_los_de_otros(self):
        b = ig.crear(T, "Dos", DEF, "maxi")
        ie.guardar(T, b["id"], [2], ["10:00"], ["telegram"], "texto", True, "maxi")
        ie.borrar_de_informe(T, self.a["id"])
        self.assertIsNotNone(ie.obtener(T, b["id"]))


class TestPorLaApi(Base):
    def setUp(self):
        super().setUp()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        self.anon = app.app.test_client()
        self.operario = app.app.test_client()
        with self.operario.session_transaction() as s:
            s["rol"], s["usuario"] = "operario", "y"
        p = mock.patch.object(app, "_tambo_del_request", lambda: T)
        p.start()
        self.addCleanup(p.stop)
        self.a = ig.crear(T, "Uno", DEF, "maxi")
        self.url = f"/api/informes/{self.a['id']}"

    def test_exigen_ser_administrador(self):
        for metodo, ruta in (("post", self.url + "/archivar"), ("post", self.url + "/restaurar"), ("delete", self.url)):
            self.assertEqual(getattr(self.anon, metodo)(ruta).status_code, 401, ruta)
            self.assertEqual(getattr(self.operario, metodo)(ruta).status_code, 403, ruta)
        self.assertEqual(self.anon.get("/api/informes?archivados=1").status_code, 401)
        self.assertEqual(self.operario.get("/api/informes?archivados=1").status_code, 403)
        self.assertEqual(len(ig.listar(T)), 1)                                       # nada se tocó

    def test_archivar_y_la_lista_de_archivados(self):
        r = self.admin.post(self.url + "/archivar")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["informe"]["archivado"])
        self.assertEqual(self.admin.get("/api/informes").get_json()["informes"], [])
        arch = self.admin.get("/api/informes?archivados=1").get_json()["informes"]
        self.assertEqual([i["nombre"] for i in arch], ["Uno"])

    def test_archivar_pausa_el_envio_programado_y_lo_dice(self):
        ie.guardar(T, self.a["id"], [0], ["08:00"], ["correo"], "excel", True, "x")
        j = self.admin.post(self.url + "/archivar").get_json()
        self.assertTrue(j["envio_pausado"])
        self.assertFalse(ie.obtener(T, self.a["id"])["activo"])
        otro = ig.crear(T, "Sin envio", DEF, "x")
        self.assertFalse(self.admin.post(f"/api/informes/{otro['id']}/archivar").get_json()["envio_pausado"])

    def test_restaurar_lo_devuelve_y_el_envio_sigue_en_pausa(self):
        ie.guardar(T, self.a["id"], [0], ["08:00"], ["correo"], "excel", True, "x")
        self.admin.post(self.url + "/archivar")
        r = self.admin.post(self.url + "/restaurar")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.admin.get("/api/informes").get_json()["informes"]), 1)
        self.assertFalse(ie.obtener(T, self.a["id"])["activo"])                      # reactivarlo es decisión de quien lo programó

    def test_eliminar_exige_haberlo_archivado(self):
        r = self.admin.delete(self.url)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Archivalo", r.get_json()["error"])
        self.assertIsNotNone(ig.obtener(T, self.a["id"]))

    def test_eliminar_borra_el_informe_y_su_envio(self):
        ie.guardar(T, self.a["id"], [0], ["08:00"], ["correo"], "excel", True, "x")
        self.admin.post(self.url + "/archivar")
        r = self.admin.delete(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(ig.obtener(T, self.a["id"]))
        self.assertIsNone(ie.obtener(T, self.a["id"]))
        self.assertEqual(self.admin.get("/api/informes?archivados=1").get_json()["informes"], [])

    def test_un_informe_que_no_existe_da_404(self):
        for metodo, ruta in (("post", "/api/informes/9999/archivar"), ("post", "/api/informes/9999/restaurar"), ("delete", "/api/informes/9999"),
                             ("post", f"/api/informes/{2 ** 70}/archivar"), ("delete", f"/api/informes/{2 ** 70}")):
            self.assertIn(getattr(self.admin, metodo)(ruta).status_code, (404, 405), ruta)
        self.assertEqual(self.admin.post("/api/informes/9999/archivar").status_code, 404)

    def test_editar_uno_archivado_da_400_con_el_motivo(self):
        self.admin.post(self.url + "/archivar")
        r = self.admin.put(self.url, json={"nombre": "Otro"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("archivado", r.get_json()["error"])

    def test_traer_de_delpro_cuenta_un_archivado_como_que_ya_estaba(self):
        # el mismo nombre ocupado por un archivado no se vuelve a crear (el aviso de `ig.crear` lo dice distinto, pero para «traer varios» sigue siendo «ya estaba»)
        self.admin.post(self.url + "/archivar")
        with self.assertRaises(ig.InformeGuardadoError) as e:
            ig.crear(T, "Uno", DEF, "x")
        self.assertTrue(str(e.exception).startswith("Ya hay un informe"))


if __name__ == "__main__":
    unittest.main()
