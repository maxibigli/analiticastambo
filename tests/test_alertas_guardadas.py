"""Copia diaria de las alertas de MixerOne en una base local.

Por qué existe: no se sabe si MixerOne guarda el historial de alertas para siempre, y las alertas empezaron el 04/11/2026
(15 días: poco para saber si anticipan caídas de leche). Si las depurara, lo que no se copió se pierde. Entonces se las
copia sola y a menudo, sin depender de que alguien abra la pantalla.

Sin red: el proveedor va de mentira y la base es un archivo temporal.

    C:/Python314/python.exe -m unittest tests.test_alertas_guardadas -v
"""
import datetime
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import alertas_guardadas as ag

HOY = datetime.date(2026, 11, 18)
AHORA = datetime.datetime(2026, 11, 18, 9, 0, 0)


def alerta(id_, codigo="N07", fecha="2026-11-17", leida=None, **kw):
    """Una alerta neutral con su crudo (como la entrega `proveedor.alertas(..., crudo=True)`)."""
    a = {"id": str(id_), "codigo": codigo, "tipo": f"Tipo {codigo}", "severidad": "warning", "mensaje": f"m{id_}",
         "fecha": fecha, "hora": "10:05", "viaje": "T-A", "rodeo": "RODEO 4", "insumo": None, "accion": "descarga",
         "operario": None, "crudo": {"id": str(id_), "code": codigo, "read_at": leida, "metadata": {"x": 1}}}
    a.update(kw)
    return a


class ProveedorFalso:
    NOMBRE = "MixerOne"

    def __init__(self, alertas=(), disponible=(True, ""), error=None):
        self.lista, self._disp, self.error, self.pedidos = list(alertas), disponible, error, []

    def disponible(self):
        return self._disp

    def alertas(self, desde, hasta, crudo=False):
        self.pedidos.append((desde, hasta, crudo))
        if self.error:
            raise self.error
        return list(self.lista)


class SinAlertas:
    NOMBRE = "Haasten"

    def disponible(self):
        return True, ""


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(ag, "RUTA_DB", os.path.join(self.dir.name, "alertas.db"))
        p.start()
        self.addCleanup(p.stop)


class TestGuardar(Base):
    def test_guarda_las_nuevas_y_no_duplica_al_volver_a_guardar(self):
        r = ag.guardar("ponderosa", [alerta(1), alerta(2)], AHORA)
        self.assertEqual((r["leidas"], r["nuevas"]), (2, 2))
        r = ag.guardar("ponderosa", [alerta(1), alerta(2)], AHORA)
        self.assertEqual((r["leidas"], r["nuevas"]), (2, 0))
        self.assertEqual(ag.resumen("ponderosa")["total"], 2)

    def test_el_crudo_se_guarda_completo(self):
        ag.guardar("ponderosa", [alerta(1)], AHORA)
        fila = ag.alertas_de("ponderosa")[0]
        self.assertEqual(fila["crudo"]["metadata"], {"x": 1})
        self.assertEqual((fila["codigo"], fila["fecha"], fila["rodeo"]), ("N07", "2026-11-17", "RODEO 4"))

    def test_si_la_alerta_cambia_se_actualiza_pero_se_recuerda_cuando_se_vio_por_primera_vez(self):
        ag.guardar("ponderosa", [alerta(1)], AHORA)
        despues = AHORA + datetime.timedelta(days=1)
        ag.guardar("ponderosa", [alerta(1, leida="2026-11-19T10:00:00Z")], despues)
        fila = ag.alertas_de("ponderosa")[0]
        self.assertEqual(fila["crudo"]["read_at"], "2026-11-19T10:00:00Z")
        self.assertEqual(fila["visto_primero"], AHORA.isoformat(timespec="seconds"))
        self.assertEqual(fila["visto_ultimo"], despues.isoformat(timespec="seconds"))

    def test_una_alerta_que_mixerone_ya_no_devuelve_no_se_borra(self):
        ag.guardar("ponderosa", [alerta(1), alerta(2)], AHORA)
        ag.guardar("ponderosa", [alerta(2)], AHORA + datetime.timedelta(days=1))       # la 1 ya no viene
        self.assertEqual(sorted(a["id"] for a in ag.alertas_de("ponderosa")), ["1", "2"])

    def test_cada_tambo_tiene_las_suyas_aunque_el_id_se_repita(self):
        ag.guardar("ponderosa", [alerta(1)], AHORA)
        ag.guardar("san_jose", [alerta(1, codigo="N06")], AHORA)
        self.assertEqual(ag.alertas_de("ponderosa")[0]["codigo"], "N07")
        self.assertEqual(ag.alertas_de("san_jose")[0]["codigo"], "N06")


class TestSincronizar(Base):
    def test_la_primera_vez_trae_todo_el_historial_que_haya(self):
        prov = ProveedorFalso([alerta(1)])
        r = ag.sincronizar("ponderosa", prov, HOY, AHORA)
        self.assertEqual(prov.pedidos, [(HOY - datetime.timedelta(days=ag.INICIAL_DIAS), HOY, True)])
        self.assertEqual((r["leidas"], r["nuevas"]), (1, 1))

    def test_las_siguientes_repasan_los_ultimos_dias(self):
        prov = ProveedorFalso([alerta(1)])
        ag.sincronizar("ponderosa", prov, HOY, AHORA)
        prov.pedidos.clear()
        ag.sincronizar("ponderosa", prov, HOY + datetime.timedelta(days=1), AHORA)
        self.assertEqual(prov.pedidos[0][0], HOY + datetime.timedelta(days=1) - datetime.timedelta(days=ag.REPASO_DIAS))

    def test_un_proveedor_sin_conexion_se_omite_con_el_motivo_y_no_pide_nada(self):
        prov = ProveedorFalso(disponible=(False, "Falta MIXERONE_USUARIO"))
        r = ag.sincronizar("ponderosa", prov, HOY, AHORA)
        self.assertEqual(r["omitido"], "Falta MIXERONE_USUARIO")
        self.assertEqual(prov.pedidos, [])

    def test_el_motivo_de_la_omision_queda_a_la_vista_en_el_resumen(self):
        """Si el tambo no está configurado con MixerOne la copia no hace nada: sin esto nadie se enteraría."""
        ag.sincronizar("ponderosa", SinAlertas(), HOY, AHORA)
        s = ag.resumen("ponderosa")["ultima_sincronizacion"]
        self.assertIn("Haasten", s["omitido"])
        self.assertIsNone(s["error"])

    def test_una_omision_no_cuenta_como_copia_hecha(self):
        ag.sincronizar("ponderosa", SinAlertas(), HOY, AHORA)
        prov = ProveedorFalso([alerta(1)])
        ag.sincronizar("ponderosa", prov, HOY, AHORA)
        self.assertEqual(prov.pedidos[0][0], HOY - datetime.timedelta(days=ag.INICIAL_DIAS))

    def test_una_copia_hecha_despues_de_una_omision_reemplaza_el_motivo(self):
        ag.sincronizar("ponderosa", SinAlertas(), HOY, AHORA)
        ag.sincronizar("ponderosa", ProveedorFalso([alerta(1)]), HOY, AHORA + datetime.timedelta(hours=6))
        s = ag.resumen("ponderosa")["ultima_sincronizacion"]
        self.assertIsNone(s["omitido"])
        self.assertEqual(s["nuevas"], 1)

    def test_un_proveedor_que_no_tiene_alertas_se_omite(self):
        r = ag.sincronizar("ponderosa", SinAlertas(), HOY, AHORA)
        self.assertIn("Haasten", r["omitido"])

    def test_si_el_proveedor_falla_no_se_propaga_y_queda_registrado(self):
        prov = ProveedorFalso(error=RuntimeError("HTTP 500"))
        r = ag.sincronizar("ponderosa", prov, HOY, AHORA)
        self.assertIn("HTTP 500", r["error"])
        self.assertIn("HTTP 500", ag.resumen("ponderosa")["ultima_sincronizacion"]["error"])

    def test_un_error_no_borra_lo_ya_guardado(self):
        ag.guardar("ponderosa", [alerta(1)], AHORA)
        ag.sincronizar("ponderosa", ProveedorFalso(error=RuntimeError("cortado")), HOY, AHORA)
        self.assertEqual(ag.resumen("ponderosa")["total"], 1)

    def test_despues_de_un_error_la_siguiente_vez_sigue_siendo_repaso_no_historial(self):
        """Solo cuenta como «ya hay copia» lo que se guardó de verdad."""
        ag.sincronizar("ponderosa", ProveedorFalso(error=RuntimeError("x")), HOY, AHORA)
        prov = ProveedorFalso([alerta(1)])
        ag.sincronizar("ponderosa", prov, HOY, AHORA)
        self.assertEqual(prov.pedidos[0][0], HOY - datetime.timedelta(days=ag.INICIAL_DIAS))

    def test_sincronizar_todos_recorre_los_tambos_y_salta_los_que_no_sirven(self):
        proveedores = {"ponderosa": ProveedorFalso([alerta(1)]), "lamartina": SinAlertas(),
                       "san_jose": ProveedorFalso(disponible=(False, "falta el establecimiento"))}

        def de(tambo):
            if tambo == "raro":
                raise ValueError("Proveedor de alimentación desconocido")
            return proveedores[tambo]
        r = ag.sincronizar_todos(["ponderosa", "lamartina", "san_jose", "raro"], proveedor_de=de, hoy=HOY, ahora=AHORA)
        self.assertEqual(r["ponderosa"]["nuevas"], 1)
        self.assertIn("omitido", r["lamartina"])
        self.assertIn("omitido", r["san_jose"])
        self.assertIn("desconocido", r["raro"]["error"])


class TestConsultas(Base):
    def test_diarias_cuenta_por_dia_y_tipo(self):
        ag.guardar("ponderosa", [alerta(1, "N07", "2026-11-17"), alerta(2, "N07", "2026-11-17"),
                                 alerta(3, "N06", "2026-11-17"), alerta(4, "N07", "2026-11-18")], AHORA)
        self.assertEqual(ag.diarias("ponderosa"), [
            {"fecha": "2026-11-17", "codigo": "N06", "n": 1}, {"fecha": "2026-11-17", "codigo": "N07", "n": 2},
            {"fecha": "2026-11-18", "codigo": "N07", "n": 1}])

    def test_diarias_recorta_por_fechas(self):
        ag.guardar("ponderosa", [alerta(1, fecha="2026-11-10"), alerta(2, fecha="2026-11-17")], AHORA)
        self.assertEqual([d["fecha"] for d in ag.diarias("ponderosa", desde="2026-11-15")], ["2026-11-17"])

    def test_resumen_dice_cuantas_hay_desde_cuando_y_la_ultima_sincronizacion(self):
        ag.sincronizar("ponderosa", ProveedorFalso([alerta(1, fecha="2026-11-04"), alerta(2, fecha="2026-11-17")]),
                       HOY, AHORA)
        r = ag.resumen("ponderosa")
        self.assertEqual((r["total"], r["desde"], r["hasta"]), (2, "2026-11-04", "2026-11-17"))
        s = r["ultima_sincronizacion"]
        self.assertEqual((s["cuando"], s["nuevas"], s["error"]), (AHORA.isoformat(timespec="seconds"), 2, None))

    def test_resumen_de_un_tambo_sin_nada(self):
        r = ag.resumen("ponderosa")
        self.assertEqual((r["total"], r["desde"], r["ultima_sincronizacion"]), (0, None, None))


class TestHilo(Base):
    def test_sincroniza_y_se_puede_parar(self):
        veces = []
        parar = threading.Event()

        def sincronizar_todos(*a, **k):
            veces.append(1)
            parar.set()
            return {}
        with mock.patch.object(ag, "sincronizar_todos", sincronizar_todos):
            hilo = ag.iniciar_hilo(parar=parar, espera_s=0.01, primera_espera_s=0)
            hilo.join(timeout=5)
        self.assertEqual(veces, [1])
        self.assertFalse(hilo.is_alive())

    def test_un_error_en_una_pasada_no_mata_el_hilo(self):
        veces, parar = [], threading.Event()

        def sincronizar_todos(*a, **k):
            veces.append(1)
            if len(veces) == 1:
                raise RuntimeError("boom")
            parar.set()
            return {}
        with mock.patch.object(ag, "sincronizar_todos", sincronizar_todos):
            hilo = ag.iniciar_hilo(parar=parar, espera_s=0.01, primera_espera_s=0)
            hilo.join(timeout=5)
        self.assertEqual(len(veces), 2)

    def test_la_base_es_un_archivo_json_serializable_del_crudo(self):
        ag.guardar("ponderosa", [alerta(1)], AHORA)
        json.dumps(ag.alertas_de("ponderosa")[0]["crudo"])


if __name__ == "__main__":
    unittest.main()
