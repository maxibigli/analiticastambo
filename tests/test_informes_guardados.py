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


if __name__ == "__main__":
    unittest.main()
