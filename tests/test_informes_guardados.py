"""Informes guardados con nombre (informes_guardados.py): SQLite propio, por tambo, nunca en DDM (que es de solo lectura y se pisa en
cada restore).

Lo que importa: que no se guarde nada que el compilador no acepte, que el nombre no se pueda repetir dentro de un tambo, que un
tambo no vea los informes de otro y que lo que se guarda sea solo la definición (sin claves de más).

    C:\\Python314\\python.exe -m unittest tests.test_informes_guardados -v
"""
import datetime
import os
import tempfile
import unittest
from unittest import mock

import informes_guardados as ig

DEF = {"columnas": [{"campo": "Number"}, {"campo": "CurrentDIM"}],
       "condiciones": [{"campo": "CurrentDIM", "op": ">=", "valor": 0}],
       "orden": {"campo": "CurrentDIM", "dir": -1}}
T = "ponderosa"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(ig, "RUTA_DB", os.path.join(self.dir.name, "informes.db"))
        p.start()
        self.addCleanup(p.stop)
        self.reloj = [datetime.datetime(2026, 11, 23, 12, 0, 0, tzinfo=datetime.timezone.utc)]
        q = mock.patch.object(ig, "_ahora", lambda: self.reloj[0])
        q.start()
        self.addCleanup(q.stop)


class TestCrear(Base):
    def test_crea_y_devuelve_el_informe(self):
        r = ig.crear(T, "Prod. x lactancia", DEF, "maxi")
        self.assertEqual((r["nombre"], r["favorito"], r["creado"], r["usuario"]), ("Prod. x lactancia", False, "2026-11-23T12:00:00Z", "maxi"))
        self.assertEqual(r["definicion"]["columnas"], DEF["columnas"])
        self.assertIsInstance(r["id"], int)

    def test_se_puede_leer_de_vuelta(self):
        r = ig.crear(T, "Uno", DEF, "maxi")
        self.assertEqual(ig.obtener(T, r["id"]), r)

    def test_guarda_solo_la_definicion_conocida(self):
        r = ig.crear(T, "Uno", dict(DEF, basura="x", sql="DROP TABLE x"), "maxi")
        self.assertEqual(set(r["definicion"]), {"columnas", "condiciones", "orden"})

    def test_guarda_resumenes_grupos_y_campos_calculados(self):
        d = {"columnas": [{"campo": "AnimalGroupName"}, {"campo": "CurrentDIM", "resumen": "avg"}, {"campo": "calc_1", "resumen": "max"}],
             "calculados": [{"id": "calc_1", "etiqueta": "Días por dos", "formula": "[CurrentDIM] * 2", "decimales": 1, "unidad": "d"}],
             "agrupar_por": ["AnimalGroupName"], "detalle": False}
        r = ig.crear(T, "Resumen por rodeo", d, "maxi")
        self.assertEqual(r["definicion"], d)
        self.assertEqual(ig.obtener(T, r["id"])["definicion"], d)

    def test_rechaza_un_calculado_con_una_formula_mala_o_un_grupo_que_no_corresponde(self):
        malos = [
            {"columnas": [{"campo": "Number"}], "calculados": [{"id": "calc_1", "etiqueta": "X", "formula": "[Number] + 1"}]},
            {"columnas": [{"campo": "Number"}], "agrupar_por": ["CurrentDIM"]},
            {"columnas": [{"campo": "Number"}], "detalle": False},
            {"columnas": [{"campo": "Number", "resumen": "avg"}]},
        ]
        for m in malos:
            with self.assertRaises(ig.InformeGuardadoError):
                ig.crear(T, "Malo", m, "maxi")
        self.assertEqual(ig.listar(T), [])

    def test_guarda_la_configuracion_del_grafico_completa_y_limpia(self):
        g = {"medida": "kg_total", "serie": "lactancia", "periodo": "fechas", "desde": "2026-05-01", "hasta": "2026-06-30", "sql": "DROP TABLE x"}
        r = ig.crear(T, "Con gráfico", dict(DEF, grafico=g), "maxi")
        guardado = r["definicion"]["grafico"]
        self.assertEqual((guardado["medida"], guardado["serie"], guardado["desde"], guardado["hasta"]), ("kg_total", "lactancia", "2026-05-01", "2026-06-30"))
        self.assertNotIn("sql", guardado)                                  # lo que no conoce, afuera
        self.assertEqual(guardado["resumen"], "avg")                       # lo que faltaba, con su valor de siempre
        self.assertEqual(ig.obtener(T, r["id"])["definicion"]["grafico"], guardado)

    def test_un_grafico_en_null_no_se_guarda(self):
        self.assertNotIn("grafico", ig.crear(T, "Sin gráfico", dict(DEF, grafico=None), "maxi")["definicion"])

    def test_rechaza_un_grafico_que_no_se_puede_correr(self):
        for malo in ({"medida": "litros"}, {"periodo": "fechas"}, {"periodo": "fechas", "desde": "2026-06-30", "hasta": "2026-05-01"}, "texto", ["x"]):
            with self.assertRaises(ig.InformeGuardadoError, msg=str(malo)):
                ig.crear(T, "Malo", dict(DEF, grafico=malo), "maxi")
        self.assertEqual(ig.listar(T), [])

    def test_se_puede_cambiar_el_grafico_de_un_informe_ya_guardado(self):
        r = ig.crear(T, "Uno", DEF, "maxi")
        a = ig.actualizar(T, r["id"], definicion=dict(DEF, grafico={"serie": "ninguna", "forma": "barras"}))
        self.assertEqual((a["definicion"]["grafico"]["serie"], a["definicion"]["grafico"]["forma"]), ("ninguna", "barras"))
        with self.assertRaises(ig.InformeGuardadoError):
            ig.actualizar(T, r["id"], definicion=dict(DEF, grafico={"serie": "sala"}))
        self.assertEqual(ig.obtener(T, r["id"])["definicion"]["grafico"]["serie"], "ninguna")      # la mala no cambió nada

    def test_rechaza_una_definicion_que_el_compilador_no_acepta(self):
        for malo in ({"columnas": [{"campo": "NoExiste"}]}, {"columnas": []}, {}, None, "texto"):
            with self.assertRaises(ig.InformeGuardadoError):
                ig.crear(T, "Malo", malo, "maxi")
        self.assertEqual(ig.listar(T), [])

    def test_nombre(self):
        for malo in ("", "   ", "x" * (ig.NOMBRE_MAX + 1), "con\nsalto", None, 5):
            with self.assertRaises(ig.InformeGuardadoError):
                ig.crear(T, malo, DEF, "maxi")
        self.assertEqual(ig.crear(T, "  con   espacios  ", DEF, "m")["nombre"], "con espacios")

    def test_el_nombre_no_se_repite_en_el_mismo_tambo_ni_cambiando_mayusculas(self):
        ig.crear(T, "Vacas a secar", DEF, "m")
        for repetido in ("Vacas a secar", "vacas A SECAR", "Vacas   a  secar"):
            with self.assertRaises(ig.InformeGuardadoError) as cm:
                ig.crear(T, repetido, DEF, "m")
            self.assertIn("Ya hay un informe", str(cm.exception))

    def test_el_mismo_nombre_en_otro_tambo_si_se_puede(self):
        ig.crear("ponderosa", "Uno", DEF, "m")
        ig.crear("san_jose", "Uno", DEF, "m")


class TestValidadoresPublicos(unittest.TestCase):
    """Lo que usan el importador de DelPro y el armado con lenguaje natural: validar SIN guardar. Es el mismo control que al guardar."""

    def test_validar_definicion_devuelve_la_definicion_limpia(self):
        r = ig.validar_definicion(dict(DEF, basura="x", sql="DROP TABLE x"))
        self.assertEqual(set(r), {"columnas", "condiciones", "orden"})

    def test_validar_definicion_rechaza_con_el_error_de_los_informes_guardados(self):
        for malo in (None, "x", {}, {"columnas": [{"campo": "NoExiste"}]}):
            with self.assertRaises(ig.InformeGuardadoError):
                ig.validar_definicion(malo)

    def test_guardar_usa_el_mismo_validador(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ig, "RUTA_DB", os.path.join(d, "informes.db")):
            with self.assertRaises(ig.InformeGuardadoError):
                ig.crear(T, "Malo", {"columnas": [{"campo": "NoExiste"}]}, "maxi")

    def test_normalizar_nombre(self):
        self.assertEqual(ig.normalizar_nombre("  Vacas   a secar "), ("Vacas a secar", "vacas a secar"))
        for malo in (None, "", "   ", "a\nb", "x" * 61, 5):
            with self.assertRaises(ig.InformeGuardadoError):
                ig.normalizar_nombre(malo)


class TestListarYAislamiento(Base):
    def test_lista_con_lo_justo_para_la_lista(self):
        ig.crear(T, "B", DEF, "m")
        ig.crear(T, "A", DEF, "m", favorito=True)
        r = ig.listar(T)
        self.assertEqual([x["nombre"] for x in r], ["A", "B"])                     # favoritos primero, después por nombre
        self.assertEqual(r[0]["n_columnas"], 2)
        self.assertNotIn("definicion", r[0])

    def test_un_tambo_no_ve_los_del_otro(self):
        a = ig.crear("ponderosa", "Mio", DEF, "m")
        self.assertEqual(ig.listar("san_jose"), [])
        self.assertIsNone(ig.obtener("san_jose", a["id"]))
        with self.assertRaises(ig.InformeGuardadoError):
            ig.actualizar("san_jose", a["id"], nombre="Robado")

    def test_obtener_inexistente(self):
        self.assertIsNone(ig.obtener(T, 999))


class TestActualizar(Base):
    def test_cambia_nombre_definicion_y_favorito_y_la_fecha(self):
        r = ig.crear(T, "Uno", DEF, "m")
        self.reloj[0] += datetime.timedelta(hours=2)
        nueva = {"columnas": [{"campo": "Number"}]}
        a = ig.actualizar(T, r["id"], nombre="Dos", definicion=nueva, favorito=True)
        self.assertEqual((a["nombre"], a["favorito"], a["modificado"], a["creado"]), ("Dos", True, "2026-11-23T14:00:00Z", "2026-11-23T12:00:00Z"))
        self.assertEqual(a["definicion"]["columnas"], nueva["columnas"])

    def test_cambiar_solo_el_favorito_no_toca_lo_demas(self):
        r = ig.crear(T, "Uno", DEF, "m")
        a = ig.actualizar(T, r["id"], favorito=True)
        self.assertEqual((a["nombre"], a["definicion"]), (r["nombre"], r["definicion"]))

    def test_no_se_puede_pisar_el_nombre_de_otro(self):
        ig.crear(T, "Uno", DEF, "m")
        dos = ig.crear(T, "Dos", DEF, "m")
        with self.assertRaises(ig.InformeGuardadoError):
            ig.actualizar(T, dos["id"], nombre="uno")

    def test_guardar_con_el_mismo_nombre_propio_si_se_puede(self):
        uno = ig.crear(T, "Uno", DEF, "m")
        ig.actualizar(T, uno["id"], nombre="UNO", definicion=DEF)

    def test_una_definicion_mala_no_cambia_nada(self):
        r = ig.crear(T, "Uno", DEF, "m")
        with self.assertRaises(ig.InformeGuardadoError):
            ig.actualizar(T, r["id"], definicion={"columnas": [{"campo": "No"}]})
        self.assertEqual(ig.obtener(T, r["id"])["definicion"], r["definicion"])

    def test_inexistente(self):
        with self.assertRaises(ig.InformeGuardadoError):
            ig.actualizar(T, 999, nombre="X")


class TestPrimerUsoConPedidosALaVez(unittest.TestCase):
    """La pantalla pide la lista de informes y la de envíos programados al mismo tiempo. Sobre un archivo NUEVO, pasar a modo WAL (`PRAGMA journal_mode=WAL`) no espera como el
    resto de las sentencias: uno de los dos pedidos daba «database is locked» (se vio al abrir la vista previa, y 19 de 30 corridas con 8 hilos)."""

    def test_varios_pedidos_a_la_vez_sobre_un_archivo_nuevo_no_se_traban(self):
        import threading
        import informes_envios as ie
        errores = []
        for vuelta in range(12):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(ig, "RUTA_DB", os.path.join(d, "informes.db")):
                barrera = threading.Barrier(8)

                def trabajo(k):
                    barrera.wait()
                    try:
                        (ie.listar if k % 2 else ig.listar)(T)
                    except Exception as e:  # noqa: BLE001
                        errores.append(f"{type(e).__name__}: {e}")
                hilos = [threading.Thread(target=trabajo, args=(k,)) for k in range(8)]
                for h in hilos:
                    h.start()
                for h in hilos:
                    h.join()
        self.assertEqual(errores, [])

    def test_el_archivo_queda_en_modo_wal(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ig, "RUTA_DB", os.path.join(d, "informes.db")):
            ig.listar(T)
            import sqlite3
            con = sqlite3.connect(ig.RUTA_DB)
            try:
                self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            finally:
                con.close()


class TestUnIdQueSQLiteNoPuedeGuardar(Base):
    """Revisión final (seguridad #3): un id de 2^63 o más no cabe en un INTEGER de SQLite (`OverflowError`) y daba un 500 en todas las rutas por id.
    Para el que lo pide es simplemente un informe que no existe."""

    def test_obtener_no_levanta_y_dice_que_no_existe(self):
        ig.crear(T, "Uno", DEF, "maxi")
        for id_ in (2 ** 63, 2 ** 63 + 5, 2 ** 200, 0, -1):
            self.assertIsNone(ig.obtener(T, id_), id_)

    def test_el_mayor_id_que_cabe_se_consulta_normal(self):
        self.assertIsNone(ig.obtener(T, 2 ** 63 - 1))

    def test_actualizar_dice_que_no_existe(self):
        for id_ in (2 ** 63, 2 ** 200):
            with self.assertRaises(ig.InformeGuardadoError) as cm:
                ig.actualizar(T, id_, nombre="Otro")
            self.assertIn("no existe", str(cm.exception))

    def test_lo_que_no_es_un_numero_tampoco_rompe(self):
        for id_ in (None, "1", 1.5, True, [1]):
            self.assertIsNone(ig.obtener(T, id_), repr(id_))


if __name__ == "__main__":
    unittest.main()
