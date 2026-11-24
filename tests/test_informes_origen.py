"""El aviso de un informe traído de DelPro «con diferencias que cambian qué animales aparecen» tiene que SOBREVIVIR al guardado (revisión final, completitud #1).

Antes el aviso vivía solo en la pestaña: al guardar y reabrir se perdía, y un informe al que le faltaba un filtro de DelPro se podía programar y mandar todos los días con más
(o menos) animales que el original, sin ninguna marca. Ahora el informe guardado lleva una «nota de origen» (fuera de la definición: no es SQL ni cambia lo que se calcula):
la lista de las diferencias que cambian QUÉ animales aparecen. Esa nota se ve al reabrir, en la lista, en «Programar envío» y como una línea fija de cada mensaje programado.

    C:\\Python314\\python.exe -X utf8 -m unittest tests.test_informes_origen -v
"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app
import informes_envios as ie
import informes_guardados as ig
from tests.test_informes_envios import DEF, JUEVES, T, resultado

ORIGEN = {"titulo": "Traído de DelPro", "sub": "«Vacas en ordeño» · Reproducción",
          "avisos": ["No se trajo la condición «Días en ordeño > 30»: el informe puede mostrar más animales.",
                     "En DelPro incluía también los animales descartados; acá solo los que alguna vez ordeñaron."]}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ruta = os.path.join(self.dir.name, "informes.db")
        p = mock.patch.object(ig, "RUTA_DB", self.ruta)
        p.start()
        self.addCleanup(p.stop)


class TestLaNotaSeGuarda(Base):
    def test_se_guarda_y_se_lee_de_vuelta(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        self.assertEqual(r["origen"], ORIGEN)
        self.assertEqual(ig.obtener(T, r["id"])["origen"], ORIGEN)

    def test_sin_nota_no_hay_origen(self):
        r = ig.crear(T, "A mano", DEF, "maxi")
        self.assertIsNone(r["origen"])
        self.assertIsNone(ig.obtener(T, r["id"])["origen"])

    def test_la_lista_dice_cuales_tienen_aviso(self):
        a = ig.crear(T, "Con aviso", DEF, "maxi", origen=ORIGEN)
        b = ig.crear(T, "Sin aviso", DEF, "maxi")
        por_id = {x["id"]: x for x in ig.listar(T)}
        self.assertIs(por_id[a["id"]]["con_aviso"], True)
        self.assertIs(por_id[b["id"]]["con_aviso"], False)

    def test_guardar_de_nuevo_sin_decir_nada_deja_la_nota(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        ig.actualizar(T, r["id"], nombre="Otro nombre")
        self.assertEqual(ig.obtener(T, r["id"])["origen"], ORIGEN)

    def test_se_puede_quitar_a_proposito(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        ig.actualizar(T, r["id"], origen=None)
        self.assertIsNone(ig.obtener(T, r["id"])["origen"])

    def test_se_puede_reemplazar(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        nuevo = {"titulo": "Traído de DelPro", "sub": "", "avisos": ["Otro aviso."]}
        ig.actualizar(T, r["id"], origen=nuevo)
        self.assertEqual(ig.obtener(T, r["id"])["origen"], nuevo)

    def test_la_nota_no_forma_parte_de_la_definicion(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        self.assertNotIn("origen", r["definicion"])


class TestLaNotaSeLimpia(Base):
    """La nota la arma el navegador: se guarda solo texto, acotado, y lo que no sirve no se guarda (nunca da un error al guardar el informe)."""

    def test_sin_avisos_no_hay_nada_que_recordar(self):
        for malo in (None, {}, {"avisos": []}, {"titulo": "x"}, {"avisos": ["", "   "]}, "texto", 7, [1], {"avisos": "no es una lista"}, {"avisos": [None, 3]}):
            self.assertIsNone(ig.normalizar_origen(malo), repr(malo))

    def test_se_acota(self):
        o = ig.normalizar_origen({"titulo": "T" * 500, "sub": "S" * 500, "avisos": ["a" * 900] * 40})
        self.assertLessEqual(len(o["titulo"]), ig.ORIGEN_TITULO_MAX)
        self.assertLessEqual(len(o["sub"]), ig.ORIGEN_TITULO_MAX)
        self.assertEqual(len(o["avisos"]), ig.ORIGEN_AVISOS_MAX)
        self.assertTrue(all(len(a) <= ig.ORIGEN_TEXTO_MAX for a in o["avisos"]))

    def test_se_sacan_los_caracteres_de_control_y_se_ignoran_las_claves_de_mas(self):
        o = ig.normalizar_origen({"titulo": "Tra\x00ído\n", "sub": "", "avisos": ["Uno\x07\n con\tcontrol"], "sql": "DROP TABLE informes", "otra": 1})
        self.assertEqual(set(o), {"titulo", "sub", "avisos"})
        self.assertEqual(o["titulo"], "Traído")
        self.assertEqual(o["avisos"], ["Uno con control"])

    def test_el_titulo_vacio_cae_al_de_siempre(self):
        self.assertEqual(ig.normalizar_origen({"avisos": ["Algo."]})["titulo"], "Traído de DelPro")

    def test_una_nota_rota_no_impide_guardar_el_informe(self):
        r = ig.crear(T, "Con basura", DEF, "maxi", origen={"avisos": 5})
        self.assertIsNone(r["origen"])
        self.assertEqual(r["nombre"], "Con basura")

    def test_en_la_base_una_nota_ilegible_se_lee_como_sin_nota(self):
        r = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        con = sqlite3.connect(self.ruta)
        con.execute("UPDATE informes SET origen = ? WHERE id = ?", ("{esto no es json", r["id"]))
        con.commit()
        con.close()
        self.assertIsNone(ig.obtener(T, r["id"])["origen"])
        self.assertIs([x for x in ig.listar(T) if x["id"] == r["id"]][0]["con_aviso"], False)


class TestUnaBaseVieja(Base):
    """`informes.db` ya existe en cada instalación (SERVER-DELPRO) sin la columna nueva: se agrega sola, sin perder nada."""

    def test_se_agrega_la_columna_y_lo_guardado_sigue_estando(self):
        con = sqlite3.connect(self.ruta)
        con.execute("""CREATE TABLE informes (id INTEGER PRIMARY KEY AUTOINCREMENT, tambo TEXT NOT NULL, nombre TEXT NOT NULL, nombre_norm TEXT NOT NULL,
            definicion TEXT NOT NULL, favorito INTEGER NOT NULL DEFAULT 0, creado TEXT NOT NULL, modificado TEXT NOT NULL, usuario TEXT, UNIQUE (tambo, nombre_norm))""")
        con.execute("INSERT INTO informes (tambo, nombre, nombre_norm, definicion, favorito, creado, modificado, usuario) VALUES (?,?,?,?,?,?,?,?)",
                    (T, "Viejo", "viejo", '{"columnas": [{"campo": "Number"}]}', 0, "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z", "maxi"))
        con.commit()
        con.close()
        viejo = ig.listar(T)[0]
        self.assertEqual((viejo["nombre"], viejo["con_aviso"]), ("Viejo", False))
        self.assertIsNone(ig.obtener(T, viejo["id"])["origen"])
        nuevo = ig.crear(T, "Nuevo", DEF, "maxi", origen=ORIGEN)                # y ya se puede guardar la nota
        self.assertEqual(ig.obtener(T, nuevo["id"])["origen"], ORIGEN)
        self.assertEqual(len(ig.listar(T)), 2)

    def test_abrir_dos_veces_no_falla(self):
        ig.listar(T)
        ig.listar(T)
        ig.crear(T, "Uno", DEF, "maxi", origen=ORIGEN)


class TestPorLaApi(Base):
    def setUp(self):
        super().setUp()
        self.admin = app.app.test_client()
        with self.admin.session_transaction() as s:
            s["rol"], s["usuario"] = "admin", "x"
        p = mock.patch.object(app, "_tambo_del_request", lambda: T)
        p.start()
        self.addCleanup(p.stop)

    def test_se_crea_con_la_nota_y_vuelve_al_abrirlo(self):
        r = self.admin.post("/api/informes", json={"nombre": "Del DelPro", "definicion": DEF, "origen": ORIGEN})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["origen"], ORIGEN)
        g = self.admin.get("/api/informes/" + str(r.get_json()["id"]))
        self.assertEqual(g.get_json()["origen"], ORIGEN)

    def test_la_lista_trae_la_marca(self):
        self.admin.post("/api/informes", json={"nombre": "Con aviso", "definicion": DEF, "origen": ORIGEN})
        self.admin.post("/api/informes", json={"nombre": "Sin aviso", "definicion": DEF})
        marcas = {x["nombre"]: x["con_aviso"] for x in self.admin.get("/api/informes").get_json()["informes"]}
        self.assertEqual(marcas, {"Con aviso": True, "Sin aviso": False})

    def test_guardar_sin_la_clave_deja_la_nota_y_con_null_la_quita(self):
        i = self.admin.post("/api/informes", json={"nombre": "Del DelPro", "definicion": DEF, "origen": ORIGEN}).get_json()["id"]
        self.assertEqual(self.admin.put(f"/api/informes/{i}", json={"nombre": "Con otro nombre"}).get_json()["origen"], ORIGEN)
        self.assertIsNone(self.admin.put(f"/api/informes/{i}", json={"origen": None}).get_json()["origen"])
        self.assertIsNone(self.admin.get(f"/api/informes/{i}").get_json()["origen"])

    def test_una_nota_con_basura_no_da_500(self):
        for malo in ("texto", 7, [1, 2], {"avisos": 3}, {"avisos": [{"a": 1}]}):
            r = self.admin.post("/api/informes", json={"nombre": f"Basura {malo!r}"[:50], "definicion": DEF, "origen": malo})
            self.assertEqual(r.status_code, 201, repr(malo))
            self.assertIsNone(r.get_json()["origen"])


def fake_resultado(**kw):
    r = resultado(**kw)
    return r


class TestElMensajeProgramadoLoAvisa(Base):
    """Cada mensaje programado de un informe traído de DelPro con diferencias lleva UNA línea fija que lo dice: lo que llega por Telegram, WhatsApp o correo no tiene la pantalla al lado."""

    def armar(self, origen, formato="excel", limite=None):
        informe = {"id": 1, "nombre": "Vacas por días en ordeño", "definicion": DEF, "origen": origen}
        return ie.armar(T, informe, formato, ahora=JUEVES, ejecutar=lambda *a, **k: fake_resultado(n=3))

    def test_sin_nota_el_mensaje_es_el_de_siempre(self):
        a = self.armar(None)
        for t in (a["texto"], a["texto_sin_adjunto"], a["texto_telegram"]):
            self.assertNotIn("DelPro con diferencias", t)

    def test_con_nota_lo_llevan_los_tres_textos_y_cada_canal(self):
        a = self.armar(ORIGEN)
        for t in (a["texto"], a["texto_sin_adjunto"], a["texto_telegram"]):
            self.assertIn(ie.AVISO_ORIGEN, t)
        for canal in ("correo", "telegram", "whatsapp"):
            self.assertIn(ie.AVISO_ORIGEN, ie.texto_para(a, canal), canal)

    def test_tambien_en_formato_solo_texto(self):
        a = self.armar(ORIGEN, formato="texto")
        self.assertIn(ie.AVISO_ORIGEN, a["texto"])

    def test_una_nota_sin_avisos_no_agrega_nada(self):
        self.assertNotIn(ie.AVISO_ORIGEN, self.armar({"titulo": "x", "sub": "", "avisos": []})["texto"])

    def test_es_lo_fijo_nunca_lo_que_se_recorta(self):
        r = resultado(n=60)
        texto, _, _ = ie.texto_resumen("Vacas por días en ordeño", JUEVES, r, True, 400, aviso_origen=True)
        self.assertLessEqual(len(texto), 400)
        self.assertIn(ie.AVISO_ORIGEN, texto)
        self.assertIn("va adjunto", texto)

    def test_el_texto_no_usa_dias_relativos_ni_nombra_a_nadie(self):
        for palabra in ("ayer", "ese día", "el día anterior"):
            self.assertNotIn(palabra, ie.AVISO_ORIGEN.lower())

    def test_se_puede_probar_desde_la_base(self):
        ig_informe = ig.crear(T, "Del DelPro", DEF, "maxi", origen=ORIGEN)
        a = ie.armar_de_la_base(T, ig_informe["id"], "texto", ahora=JUEVES, ejecutar=lambda *a, **k: fake_resultado())
        self.assertIn(ie.AVISO_ORIGEN, a["texto"])


if __name__ == "__main__":
    unittest.main()
