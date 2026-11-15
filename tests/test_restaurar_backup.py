"""restaurar_backup.py: busca el backup más nuevo de cada tambo en C:\\backupsdelpro, lo extrae y lo restaura en SQL Server
sobre una base paralela, le devuelve el acceso a delpro_lectura y recién ahí la intercambia con la actual.

Lo que importa: que NUNCA se pise la base buena con un backup incompleto, vacío o a medio bajar de Drive, que un restore
que falla deje todo como estaba, y que no se repita el mismo backup cada noche.

    C:\\Python314\\python.exe -m unittest tests.test_restaurar_backup -v
"""
import datetime
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import restaurar_backup as rb

AHORA = datetime.datetime(2026, 11, 12, 6, 0, 0)
XML = ('<?xml version="1.0" encoding="utf-8" ?><DelPro version="2025.3.4.11" farmid="x" license="SECRETA-NO-MOSTRAR" '
       'sqlserver="16.00.4115" backupdate="{f}" />')


def _tocar(ruta, contenido=b"x", hace_s=3600):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)
    t = AHORA.timestamp() - hace_s
    os.utime(ruta, (t, t))
    return ruta


def _zip(ruta, miembros, hace_s=3600):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ruta, "w") as z:
        for nombre, dato in miembros.items():
            z.writestr(nombre, dato)
    t = AHORA.timestamp() - hace_s
    os.utime(ruta, (t, t))
    return ruta


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.raiz = Path(self.dir.name)
        self.tambo = self.raiz / "laponderosa"
        self.tambo.mkdir()


class TestBuscar(Base):
    def test_elige_el_zip_mas_nuevo(self):
        _zip(self.tambo / "a.zip", {"DelPro.1.bak": b"1"}, hace_s=7200)
        _zip(self.tambo / "b.zip", {"DelPro.1.bak": b"2"}, hace_s=3600)
        elegido, saltados = rb.elegir_ultimo(rb.buscar(self.tambo, AHORA), AHORA)
        self.assertEqual(elegido.ruta.name, "b.zip")
        self.assertEqual(saltados, [])

    def test_carpeta_vacia_no_da_nada(self):
        elegido, saltados = rb.elegir_ultimo(rb.buscar(self.tambo, AHORA), AHORA)
        self.assertIsNone(elegido)

    def test_un_archivo_recien_tocado_se_considera_a_medio_bajar(self):
        """Drive todavía lo está sincronizando: se usa el anterior completo en vez de arriesgarse con este."""
        _zip(self.tambo / "vieja.zip", {"DelPro.1.bak": b"1"}, hace_s=86400)
        _zip(self.tambo / "nueva.zip", {"DelPro.1.bak": b"2"}, hace_s=10)
        elegido, saltados = rb.elegir_ultimo(rb.buscar(self.tambo, AHORA), AHORA)
        self.assertEqual(elegido.ruta.name, "vieja.zip")
        self.assertEqual([c.ruta.name for c in saltados], ["nueva.zip"])

    def test_un_zip_cortado_no_sirve(self):
        _zip(self.tambo / "ok.zip", {"DelPro.1.bak": b"1"}, hace_s=7200)
        _tocar(self.tambo / "roto.zip", b"PK\x03\x04" + b"basura", hace_s=3600)
        elegido, saltados = rb.elegir_ultimo(rb.buscar(self.tambo, AHORA), AHORA)
        self.assertEqual(elegido.ruta.name, "ok.zip")
        self.assertEqual([c.ruta.name for c in saltados], ["roto.zip"])

    def test_partes_bak_sueltas_con_su_manifiesto_usan_la_fecha_del_backup(self):
        d = self.tambo / "05-11"
        for i in (1, 2, 3):
            _tocar(d / f"DelPro.{i}.bak", b"p", hace_s=100000)
        _tocar(d / "DelPro.xml", XML.format(f="20261105 011231").encode())
        c = [x for x in rb.buscar(self.tambo, AHORA) if x.tipo == "bak"][0]
        self.assertEqual(c.fecha, datetime.datetime(2026, 11, 5, 1, 12, 31))
        self.assertTrue(c.completo)

    def test_falta_una_parte_numerada(self):
        d = self.tambo / "dia"
        _tocar(d / "DelPro.1.bak")
        _tocar(d / "DelPro.3.bak")
        c = rb.buscar(self.tambo, AHORA)[0]
        elegido, saltados = rb.elegir_ultimo([c], AHORA)
        self.assertIsNone(elegido)
        self.assertIn("parte", saltados[0].motivo)

    def test_ignora_la_carpeta_de_trabajo(self):
        _tocar(self.tambo / "_trabajo" / "DelPro_full.bak")
        self.assertEqual(rb.buscar(self.tambo, AHORA), [])


class TestPartes(Base):
    def test_orden_numerico_no_alfabetico(self):
        nombres = ["DelPro.10.bak", "DelPro.2.bak", "DelPro.1.bak"]
        self.assertEqual([p.name for p in rb.partes_ordenadas([Path(n) for n in nombres])],
                         ["DelPro.1.bak", "DelPro.2.bak", "DelPro.10.bak"])

    def test_unir_concatena_en_orden(self):
        a = _tocar(self.raiz / "DelPro.1.bak", b"AAAA")
        b = _tocar(self.raiz / "DelPro.2.bak", b"BB")
        c = _tocar(self.raiz / "DelPro.3.bak", b"C")
        destino = self.raiz / "full.bak"
        n = rb.unir_partes([c, a, b], destino, bloque=3)
        self.assertEqual((destino.read_bytes(), n), (b"AAAABBC", 7))

    def test_una_sola_parte_no_se_copia(self):
        solo = _tocar(self.raiz / "DelPro.bak", b"X")
        self.assertEqual(rb.preparar_bak([solo], self.raiz / "full.bak"), solo)
        self.assertFalse((self.raiz / "full.bak").exists())


class TestExtraer(Base):
    def test_extrae_solo_bak_y_xml_y_sin_salirse_de_la_carpeta(self):
        z = _zip(self.raiz / "b.zip", {"DelPro.1.bak": b"1", "DelPro.xml": b"<x/>", "..\\..\\mal.bak": b"m",
                                       "sub/otro.txt": b"t"})
        destino = self.raiz / "out"
        rb.extraer(z, destino)
        self.assertEqual(sorted(p.name for p in destino.iterdir()), ["DelPro.1.bak", "DelPro.xml", "mal.bak"])
        self.assertFalse((self.raiz / "mal.bak").exists())

    def test_7z_sin_7zip_instalado_lo_dice(self):
        f = _tocar(self.raiz / "b.7z")
        with self.assertRaises(rb.RestauracionError) as cm:
            rb.extraer(f, self.raiz / "out", buscar_7z=lambda: None)
        self.assertIn("7-Zip", str(cm.exception))


class TestManifiesto(unittest.TestCase):
    def test_lee_la_fecha_y_nunca_devuelve_la_licencia(self):
        m = rb.leer_manifiesto(XML.format(f="20260731 011231"))
        self.assertEqual(m["backupdate"], datetime.datetime(2026, 7, 31, 1, 12, 31))
        self.assertNotIn("SECRETA", json.dumps(m, default=str))

    def test_xml_roto_o_sin_fecha(self):
        self.assertIsNone(rb.leer_manifiesto("no es xml"))
        self.assertIsNone(rb.leer_manifiesto('<DelPro version="1" />'))


FILAS = [["DDM_Data", "C:\\x\\DDM.mdf", "D"], ["DDM_Log", "C:\\x\\DDM_log.ldf", "L"]]


class TestSql(unittest.TestCase):
    def test_restore_mueve_cada_archivo_a_nombres_nuevos(self):
        sql = rb.sql_restore("DDM_NUEVA", [Path("C:/t/full.bak")], FILAS, "C:\\D\\", "C:\\L\\", "20261105_011231")
        self.assertIn("RESTORE DATABASE [DDM_NUEVA]", sql)
        self.assertIn("MOVE N'DDM_Data' TO N'C:\\D\\DDM_NUEVA_20261105_011231.mdf'", sql)
        self.assertIn("MOVE N'DDM_Log' TO N'C:\\L\\DDM_NUEVA_20261105_011231.ldf'", sql)
        self.assertNotIn("REPLACE", sql)

    def test_filelistonly_real_trae_muchas_columnas(self):
        """SQL Server devuelve ~22 columnas por archivo (Size, MaxSize, FileId...): solo importan nombre y tipo."""
        filas = [["DDM_Data", "C:\\x\\DDM.mdf", "D"] + ["0"] * 19, ["DDM_Log", "C:\\x\\DDM_log.ldf", "L"] + ["0"] * 19]
        sql = rb.sql_restore("DDM_NUEVA", [Path("C:/t/full.bak")], filas, "C:\\D\\", "C:\\L\\", "s")
        self.assertIn("MOVE N'DDM_Data' TO N'C:\\D\\DDM_NUEVA_s.mdf'", sql)

    def test_nombres_de_base_con_corchetes_no_inyectan(self):
        with self.assertRaises(rb.RestauracionError):
            rb.sql_restore("DDM]; DROP DATABASE x;--", [Path("a.bak")], FILAS, "C:\\D\\", "C:\\L\\", "s")

    def test_usuario_de_lectura_se_arregla_solo_si_existe_el_login(self):
        sql = rb.sql_usuario_lectura("DDM_NUEVA")
        self.assertIn("sys.server_principals", sql)
        self.assertIn("ALTER USER delpro_lectura WITH LOGIN = delpro_lectura", sql)
        self.assertIn("CREATE USER delpro_lectura FOR LOGIN delpro_lectura", sql)
        self.assertIn("ALTER ROLE db_datareader ADD MEMBER delpro_lectura", sql)


class _Proceso:
    def __init__(self, codigo, salida):
        self.returncode, self.stdout = codigo, salida.encode()


class TestSqlcmd(unittest.TestCase):
    """El instante justo después de crear el .bak de 2 GB la instancia (con poca RAM) tarda en atender el login: pasó en
    2 de 4 corridas reales. Un login que no entró significa que la sentencia NO corrió, así que reintentar es seguro."""
    LOGIN = "Sqlcmd: Error: Microsoft ODBC Driver 17 for SQL Server : Login timeout expired."

    def _ejecutor(self, respuestas):
        llamadas = []

        def correr(cmd, **kw):
            llamadas.append(cmd)
            return respuestas[min(len(llamadas) - 1, len(respuestas) - 1)]
        return rb.sqlcmd("x\\y", correr=correr, esperar=lambda s: None), llamadas

    def test_reintenta_si_el_login_no_entro(self):
        ejecutar, llamadas = self._ejecutor([_Proceso(1, self.LOGIN), _Proceso(1, self.LOGIN), _Proceso(0, "ok|1\n")])
        self.assertEqual(ejecutar("SELECT 1"), [["ok", "1"]])
        self.assertEqual(len(llamadas), 3)

    def test_si_nunca_entra_se_rinde_con_el_mensaje(self):
        ejecutar, llamadas = self._ejecutor([_Proceso(1, self.LOGIN)])
        with self.assertRaises(rb.RestauracionError) as cm:
            ejecutar("SELECT 1")
        self.assertIn("Login timeout", str(cm.exception))
        self.assertEqual(len(llamadas), rb.INTENTOS_LOGIN)

    def test_un_error_de_sql_no_se_reintenta(self):
        """Si la sentencia SÍ corrió y falló, repetirla podría hacer daño (un RESTORE, un cambio de nombre)."""
        ejecutar, llamadas = self._ejecutor([_Proceso(1, "Msg 3013, Level 16\nVERIFY DATABASE is terminating")])
        with self.assertRaises(rb.RestauracionError):
            ejecutar("RESTORE VERIFYONLY FROM DISK = N'x'")
        self.assertEqual(len(llamadas), 1)

    def test_el_mensaje_de_error_incluye_stderr_y_stdout(self):
        ejecutar, _ = self._ejecutor([_Proceso(1, "Msg 5030, Level 16\nno se pudo bloquear")])
        with self.assertRaises(rb.RestauracionError) as cm:
            ejecutar("USE x")
        self.assertIn("5030", str(cm.exception))


class SqlFalso:
    """Un sqlcmd de mentira: anota cada sentencia y contesta lo que se le configure."""

    def __init__(self, existentes=("DDM",), animales=1500, fallar_en=None, ocupada=None):
        """`ocupada` = {texto: veces}: esa sentencia falla con el 5030 real ('could not be exclusively locked') esas veces."""
        self.sentencias, self.existentes, self.animales, self.fallar_en = [], set(existentes), animales, fallar_en
        self.ocupada = dict(ocupada or {})

    def __call__(self, sql, base="master"):
        self.sentencias.append(sql)
        for texto, veces in self.ocupada.items():
            if texto in sql and veces > 0:
                self.ocupada[texto] -= 1
                raise rb.RestauracionError("Msg 5030, Level 16, State 2\nThe database could not be exclusively locked "
                                           "to perform the operation.")
        if self.fallar_en and self.fallar_en in sql:
            raise rb.RestauracionError("falla simulada")
        if "FILELISTONLY" in sql:
            return [list(f) for f in FILAS]
        if "InstanceDefaultDataPath" in sql:
            return [["C:\\D\\", "C:\\L\\"]]
        if "sys.databases" in sql:
            return [[n] for n in sorted(self.existentes)]
        if "COUNT(*)" in sql:
            return [[str(self.animales)]]
        return []

    def hubo(self, texto):
        return any(texto in s for s in self.sentencias)


class TestOrquestador(Base):
    def _preparar(self):
        _zip(self.tambo / "b.zip", {"DelPro.1.bak": b"AAA", "DelPro.2.bak": b"BB",
                                    "DelPro.xml": XML.format(f="20261105 011231").encode()})

    def _correr(self, sql, **kw):
        return rb.restaurar_tambo("laponderosa", "DDM", self.raiz, sql, ahora=AHORA, log=lambda *_: None, **kw)

    def test_camino_feliz_restaura_arregla_usuario_e_intercambia(self):
        self._preparar()
        sql = SqlFalso()
        r = self._correr(sql)
        self.assertEqual(r["estado"], "restaurado")
        orden = [s for s in sql.sentencias if any(k in s for k in ("VERIFYONLY", "RESTORE DATABASE", "delpro_lectura", "MODIFY NAME"))]
        self.assertTrue("VERIFYONLY" in orden[0] and "RESTORE DATABASE" in orden[1])
        self.assertTrue(sql.hubo("MODIFY NAME = [DDM_NUEVA]") or sql.hubo("[DDM_NUEVA] MODIFY NAME = [DDM]"))
        self.assertTrue(sql.hubo("delpro_lectura"))

    def test_los_archivos_nuevos_llevan_la_hora_de_la_corrida_no_la_del_backup(self):
        """Restaurar dos veces el MISMO backup (--forzar) chocaba con los archivos de la base de la corrida anterior
        ('cannot be restored to ...ldf', medido con una base real)."""
        self._preparar()
        sql = SqlFalso()
        self._correr(sql)
        lote = [s for s in sql.sentencias if "RESTORE DATABASE" in s][0]
        self.assertIn("DDM_NUEVA_20261112_060000.mdf", lote)
        self.assertNotIn("20261105", lote)

    def test_apaga_el_auto_cierre_de_la_base_nueva_en_el_mismo_lote_del_restore(self):
        """Las bases de DelPro vienen con AUTO_CLOSE activado: al restaurar, la primera conexión siguiente se cuelga
        2 minutos intentando abrirla y falla con 'could not be exclusively locked' (medido, error 5030)."""
        self._preparar()
        sql = SqlFalso()
        self._correr(sql)
        lote = [s for s in sql.sentencias if "RESTORE DATABASE" in s][0]
        self.assertIn("ALTER DATABASE [DDM_NUEVA] SET AUTO_CLOSE OFF", lote)

    def test_la_base_vieja_se_conserva_con_otro_nombre_no_se_borra(self):
        self._preparar()
        sql = SqlFalso()
        self._correr(sql)
        self.assertTrue(sql.hubo("MODIFY NAME = [DDM_ANT_"))
        self.assertFalse(sql.hubo("DROP DATABASE"))

    def test_registra_lo_restaurado_y_no_repite_el_mismo_backup(self):
        self._preparar()
        self._correr(SqlFalso())
        reg = json.loads((self.raiz / "restauraciones.json").read_text("utf-8"))
        self.assertEqual(reg["laponderosa"]["backupdate"], "2026-11-05T01:12:31")
        sql2 = SqlFalso()
        r = self._correr(sql2)
        self.assertEqual(r["estado"], "al_dia")
        self.assertFalse(sql2.hubo("RESTORE DATABASE"))

    def test_forzar_repite(self):
        self._preparar()
        self._correr(SqlFalso())
        self.assertEqual(self._correr(SqlFalso(), forzar=True)["estado"], "restaurado")

    def test_un_backup_mas_viejo_que_el_ya_restaurado_no_pisa(self):
        self._preparar()
        (self.raiz / "restauraciones.json").write_text(
            json.dumps({"laponderosa": {"backupdate": "2026-11-09T01:12:31"}}), "utf-8")
        self.assertEqual(self._correr(SqlFalso())["estado"], "al_dia")

    def test_si_la_base_nueva_viene_vacia_no_se_intercambia(self):
        self._preparar()
        sql = SqlFalso(animales=0)
        with self.assertRaises(rb.RestauracionError):
            self._correr(sql)
        self.assertFalse(sql.hubo("MODIFY NAME"))

    def test_si_falla_el_restore_la_actual_queda_intacta(self):
        self._preparar()
        sql = SqlFalso(fallar_en="RESTORE DATABASE")
        with self.assertRaises(rb.RestauracionError):
            self._correr(sql)
        self.assertFalse(sql.hubo("MODIFY NAME"))

    def test_si_falla_el_ultimo_cambio_de_nombre_se_vuelve_atras(self):
        self._preparar()
        sql = SqlFalso(fallar_en="[DDM_NUEVA] MODIFY NAME = [DDM]")
        with self.assertRaises(rb.RestauracionError):
            self._correr(sql)
        self.assertTrue(sql.hubo("MODIFY NAME = [DDM]"))
        self.assertTrue(any("[DDM_ANT_" in s and "MODIFY NAME = [DDM]" in s for s in sql.sentencias))

    def test_el_cambio_de_nombre_se_reintenta_si_la_base_esta_ocupada(self):
        """Medido: justo después del restore, tareas internas de SQL Server tienen la base tomada un rato y el
        cambio de nombre falla con el 5030 (en 2 de 4 corridas reales, tras esperar 2 y 5 minutos)."""
        self._preparar()
        sql = SqlFalso(ocupada={"[DDM_NUEVA] MODIFY NAME = [DDM]": 2})
        r = self._correr(sql, esperar=lambda s: None)
        self.assertEqual(r["estado"], "restaurado")
        self.assertEqual(sum("[DDM_NUEVA] MODIFY NAME = [DDM]" in s for s in sql.sentencias), 3)

    def test_si_la_base_nunca_se_libera_la_actual_vuelve_a_su_nombre(self):
        self._preparar()
        sql = SqlFalso(ocupada={"[DDM_NUEVA] MODIFY NAME = [DDM]": 99})
        with self.assertRaises(rb.RestauracionError):
            self._correr(sql, esperar=lambda s: None)
        self.assertEqual(sum("[DDM_NUEVA] MODIFY NAME = [DDM]" in s for s in sql.sentencias), rb.INTENTOS_RENOMBRAR)
        self.assertTrue(any("[DDM_ANT_" in s and "MODIFY NAME = [DDM]" in s for s in sql.sentencias))

    def test_otro_error_en_el_cambio_de_nombre_no_se_reintenta(self):
        self._preparar()
        sql = SqlFalso(fallar_en="[DDM_NUEVA] MODIFY NAME = [DDM]")
        with self.assertRaises(rb.RestauracionError):
            self._correr(sql, esperar=lambda s: None)
        self.assertEqual(sum("[DDM_NUEVA] MODIFY NAME = [DDM]" in s for s in sql.sentencias), 1)

    def test_resto_de_una_corrida_anterior_frena_sin_borrar(self):
        self._preparar()
        with self.assertRaises(rb.RestauracionError) as cm:
            self._correr(SqlFalso(existentes=("DDM", "DDM_NUEVA")))
        self.assertIn("DDM_NUEVA", str(cm.exception))

    def test_sin_base_actual_solo_renombra_la_nueva(self):
        self._preparar()
        sql = SqlFalso(existentes=())
        self.assertEqual(self._correr(sql)["estado"], "restaurado")
        self.assertFalse(sql.hubo("DDM_ANT_"))

    def test_solo_ver_no_toca_nada(self):
        self._preparar()
        sql = SqlFalso()
        r = self._correr(sql, solo_ver=True)
        self.assertEqual(r["estado"], "solo_ver")
        self.assertEqual(sql.sentencias, [])

    def test_sin_backups_lo_dice(self):
        r = self._correr(SqlFalso())
        self.assertEqual(r["estado"], "sin_backup")

    def test_limpia_la_carpeta_de_trabajo(self):
        self._preparar()
        self._correr(SqlFalso())
        self.assertFalse((self.tambo / "_trabajo").exists())

    def test_el_trabajo_se_hace_donde_SQL_Server_puede_leer_no_en_la_carpeta_de_los_backups(self):
        """Medido en SERVER-DELPRO: con los backups en `G:\\Mi unidad\\...` (Google Drive, que solo existe para el usuario
        de Windows) SQL Server contestaba 'Operating system error 5 (Acceso denegado)' al VERIFYONLY, porque el .bak
        armado quedaba en `_trabajo` dentro de esa carpeta. El .bak se arma en la carpeta de datos de la instancia."""
        self._preparar()
        datos = self.raiz / "sqldata"
        datos.mkdir()

        class SqlConDatos(SqlFalso):
            def __call__(self, sql, base="master"):
                if "InstanceDefaultDataPath" in sql:
                    self.sentencias.append(sql)
                    return [[str(datos) + "\\", str(datos) + "\\"]]
                return super().__call__(sql, base)

        sql = SqlConDatos()
        r = self._correr(sql)
        self.assertEqual(r["estado"], "restaurado")
        verificar = [s for s in sql.sentencias if "VERIFYONLY" in s][0]
        self.assertIn(str(datos), verificar)
        self.assertNotIn(str(self.tambo), verificar)
        self.assertEqual(list(datos.iterdir()), [])       # y lo limpia al terminar
        self.assertFalse((self.tambo / "_trabajo").exists())


class TestCarpetaPropia(Base):
    def _zip_en(self, carpeta):
        _zip(carpeta / "b.zip", {"DelPro.1.bak": b"AAA", "DelPro.xml": XML.format(f="20261105 011231").encode()})

    def test_usa_la_carpeta_dada_sin_agregar_el_nombre_del_tambo(self):
        self._zip_en(self.raiz)
        r = rb.restaurar_tambo("laponderosa", "DDM", self.raiz, SqlFalso(), ahora=AHORA, log=lambda *_: None,
                               carpeta_propia=True)
        self.assertEqual(r["estado"], "restaurado")
        reg = json.loads((self.raiz / "restauraciones.json").read_text("utf-8"))
        self.assertIn("laponderosa", reg)

    def test_sin_carpeta_propia_busca_en_la_subcarpeta(self):
        self._zip_en(self.tambo)
        r = rb.restaurar_tambo("laponderosa", "DDM", self.raiz, SqlFalso(), ahora=AHORA, log=lambda *_: None)
        self.assertEqual(r["estado"], "restaurado")


class TestResumen(Base):
    def _zip_nuevo(self, hace_s=3600):
        _zip(self.raiz / "b.zip", {"DelPro.1.bak": b"AAA", "DelPro.xml": XML.format(f="20261105 011231").encode()},
             hace_s=hace_s)

    def test_carpeta_vacia_es_sin_backup(self):
        self.assertEqual(rb.resumen_carpeta(self.raiz, "laponderosa", AHORA)["estado"], "sin_backup")

    def test_carpeta_inexistente_es_sin_backup_sin_excepcion(self):
        r = rb.resumen_carpeta(self.raiz / "no_existe", "laponderosa", AHORA)
        self.assertEqual(r["estado"], "sin_backup")

    def test_backup_sin_restaurar_es_nuevo(self):
        self._zip_nuevo()
        r = rb.resumen_carpeta(self.raiz, "laponderosa", AHORA)
        self.assertEqual(r["estado"], "nuevo")
        self.assertEqual(r["backupdate"], datetime.datetime(2026, 11, 5, 1, 12, 31))
        self.assertEqual(r["nombre"], "b.zip")
        self.assertIsNone(r["restaurado"])

    def test_ya_restaurado_es_al_dia_y_forzar_lo_vuelve_nuevo(self):
        self._zip_nuevo()
        rb.restaurar_tambo("laponderosa", "DDM", self.raiz, SqlFalso(), ahora=AHORA, log=lambda *_: None,
                           carpeta_propia=True)
        r = rb.resumen_carpeta(self.raiz, "laponderosa", AHORA)
        self.assertEqual(r["estado"], "al_dia")
        self.assertEqual(r["restaurado"], "2026-11-05T01:12:31")
        self.assertEqual(rb.resumen_carpeta(self.raiz, "laponderosa", AHORA, forzar=True)["estado"], "nuevo")

    def test_el_nombre_dice_en_que_subcarpeta_esta_el_backup(self):
        """Si la carpeta elegida tiene subcarpetas, «b.zip» solo no dice de cuál tambo es: se muestra la ruta relativa."""
        _zip(self.raiz / "otro_tambo" / "b.zip", {"DelPro.1.bak": b"A", "DelPro.xml": XML.format(f="20261105 011231").encode()})
        self.assertEqual(rb.resumen_carpeta(self.raiz, "laponderosa", AHORA)["nombre"], "otro_tambo/b.zip")

    def test_un_zip_recien_tocado_se_salta_y_se_informa(self):
        self._zip_nuevo(hace_s=10)
        r = rb.resumen_carpeta(self.raiz, "laponderosa", AHORA)
        self.assertEqual(r["estado"], "sin_backup")
        self.assertEqual(len(r["saltados"]), 1)
        self.assertIn("b.zip", r["saltados"][0])


class TestAyudas(unittest.TestCase):
    def test_bases_anteriores_filtra_y_ordena(self):
        sql = SqlFalso(existentes={"DDM", "DDM_ANT_20261105_0600", "DDM_ANT_20261101_0600", "OTRA"})
        self.assertEqual(rb.bases_anteriores("DDM", sql), ["DDM_ANT_20261101_0600", "DDM_ANT_20261105_0600"])

    def test_dar_acceso_lectura_corre_el_script_sobre_esa_base(self):
        sql = SqlFalso()
        rb.dar_acceso_lectura("DDM", sql)
        self.assertTrue(sql.hubo("USE [DDM]"))
        self.assertTrue(sql.hubo("delpro_lectura"))

    def test_dar_acceso_rechaza_nombres_raros(self):
        with self.assertRaises(rb.RestauracionError):
            rb.dar_acceso_lectura("DDM; DROP", SqlFalso())


if __name__ == "__main__":
    unittest.main()
