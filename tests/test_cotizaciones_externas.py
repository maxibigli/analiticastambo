"""Precio de la leche (OCLA) y dólar (DolarHoy) traídos de internet para cargarlos en ⚙ Configuración.

Sin red: `_get` se reemplaza por páginas de ejemplo copiadas de las reales (recortadas). La estructura de
esas páginas la define el sitio y puede cambiar: por eso cada fuente tiene su respaldo y el error explica qué falló.

    C:\\Python314\\python.exe -m unittest tests.test_cotizaciones_externas -v
"""
import datetime
import io
import json
import unittest
from unittest import mock

import cotizaciones_externas as ce

HOY = datetime.date(2026, 11, 9)

# --- DolarHoy (recortes de la página real del 09/11/2026) -----------------------------------------------------------
_TILE = ('<div class="tile is-child"><div class="cotizaciones__fila-principal"><div class="title">'
         '<a class="titleText" href="{href}" aria-label="Link">{nombre}</a></div><div class="values">'
         '<div class="compra"><div class="label">Compra</div><div class="val">{compra}</div><div class="label"></div></div>'
         '<div class="separator"></div><div class="venta"><div class="venta-wrapper"><div class="label">Venta</div>'
         '<div class="val">{venta}</div></div><div class="var-porcentaje"><div>0.00%</div></div></div></div></div></div>')
_FILA = ('<div class="tile is-child"><a href="{href}"><div class="title">{nombre}</div>'
         '<div class="compra">{compra}</div><div class="venta">{venta}</div></a></div>')
DOLARHOY = (
    "<html><body>"
    + _TILE.format(href="/cotizaciondolarblue", nombre="Dólar blue", compra="$1.535", venta="$1.555")
    + '<div class="tile update"><span>Actualizado por última vez: 09/11/26 01:03 AM</span></div>'
    + _TILE.format(href="/cotizaciondolaroficial", nombre="Dólar Oficial", compra="$1.520", venta="$1.570")
    + '<div class="cotizaciones">'
    + _FILA.format(href="/cotizaciondolarblue", nombre="Dólar Libre", compra="1.535,00", venta="1.555,00")
    + _FILA.format(href="/cotizaciondolarmayorista", nombre="Dólar Mayorista", compra="1.438,00", venta="1.439,00")
    + _FILA.format(href="/cotizaciondolarbolsa", nombre="Dólar MEP", compra="1.560,20", venta="1.587,90")
    + "</div></body></html>"
)

# --- OCLA ---------------------------------------------------------------------------------------------------------
OCLA_HOME = (
    '<li><a class="bold dropdown-toggle font-green">\n   SIGLeA (SEP/2026):\n </a></li>'
    '<li><a class="dropdown-toggle font-green"><i class="bold">$</i> <span class="badge badge-primary" '
    'style="font-size:14px !important">\n 543,01 \n </span> <i class="bold">/litro</i> </a></li>'
    '<li><a><i class="bold">$</i> <span class="badge badge-primary"> 7.387,13 </span> <i class="bold">/kg SU</i></a></li>'
)


def _xlsx_ocla(filas):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Series Mensuales"
    ws.append(["Mes", "Precio $/litro Nacional - SIGLeA ", "Precio/kg. de SU Nacional - SIGLeA"])
    for f in filas:
        ws.append(list(f))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _red(**paginas):
    """Un `_get` falso: devuelve bytes por URL que contiene la clave; lanza si la clave es una excepción."""
    def get(url, **kw):
        for clave, valor in paginas.items():
            if clave in url:
                if isinstance(valor, Exception):
                    raise valor
                return valor if isinstance(valor, bytes) else valor.encode("utf-8")
        raise ce.CotizacionError(f"sin página de prueba para {url}")
    return get


class Base(unittest.TestCase):
    def setUp(self):
        ce._cache.clear()


class TestDolarHoy(Base):
    def _dolar(self, tipo, **paginas):
        with mock.patch.object(ce, "_get", _red(**(paginas or {"dolarhoy.com": DOLARHOY}))):
            return ce.dolar(tipo)

    def test_oficial_es_el_precio_de_venta_de_la_tarjeta(self):
        r = self._dolar("oficial")
        self.assertEqual((r["valor"], r["compra"], r["tipo"]), (1570.0, 1520.0, "oficial"))
        self.assertEqual(r["fuente"], "DolarHoy")
        self.assertIn("09/11/26", r["actualizado"])
        self.assertEqual(r["origen"], "DolarHoy · Dólar oficial (venta) · 09/11/26 01:03 AM")

    def test_blue_mayorista_y_mep_salen_de_la_tabla_de_cotizaciones(self):
        self.assertEqual(self._dolar("blue")["valor"], 1555.0)
        self.assertEqual(self._dolar("mayorista")["valor"], 1439.0)
        self.assertEqual(self._dolar("mep")["valor"], 1587.9)

    def test_tipo_desconocido_es_un_error_claro(self):
        with self.assertRaises(ce.CotizacionError) as cm:
            self._dolar("cripto")
        self.assertIn("tipo de dólar", str(cm.exception).lower())

    def test_si_la_pagina_cambia_se_usa_dolarapi_y_se_dice(self):
        api = json.dumps({"compra": 1520, "venta": 1570, "casa": "oficial", "nombre": "Oficial",
                          "fechaActualizacion": "2026-11-09T01:03:00.000Z"})
        r = self._dolar("oficial", **{"dolarhoy.com": "<html>otra estructura</html>", "dolarapi.com": api})
        self.assertEqual(r["valor"], 1570.0)
        self.assertEqual(r["fuente"], "DolarApi")
        self.assertIn("DolarHoy", r["aviso"])           # explica por qué no vino de DolarHoy

    def test_si_dolarhoy_no_responde_se_usa_dolarapi(self):
        api = json.dumps({"compra": 1520, "venta": 1570, "fechaActualizacion": "2026-11-09T01:03:00.000Z"})
        r = self._dolar("oficial", **{"dolarhoy.com": ce.CotizacionError("timeout"), "dolarapi.com": api})
        self.assertEqual((r["valor"], r["fuente"]), (1570.0, "DolarApi"))

    def test_mep_en_dolarapi_se_pide_como_bolsa(self):
        pedidas = []

        def get(url, **kw):
            pedidas.append(url)
            if "dolarhoy" in url:
                raise ce.CotizacionError("caído")
            return json.dumps({"compra": 1560, "venta": 1588, "fechaActualizacion": "x"}).encode()
        with mock.patch.object(ce, "_get", get):
            ce.dolar("mep")
        self.assertTrue(any(u.endswith("/v1/dolares/bolsa") for u in pedidas), pedidas)

    def test_un_valor_absurdo_no_se_acepta(self):
        html = DOLARHOY.replace("$1.570", "$5")
        api = json.dumps({"compra": 1520, "venta": 1570, "fechaActualizacion": "x"})
        r = self._dolar("oficial", **{"dolarhoy.com": html, "dolarapi.com": api})
        self.assertEqual(r["valor"], 1570.0)             # el 5 se descartó

    def test_si_fallan_las_dos_fuentes_el_error_las_nombra(self):
        with self.assertRaises(ce.CotizacionError) as cm:
            self._dolar("oficial", **{"dolarhoy.com": ce.CotizacionError("timeout"),
                                      "dolarapi.com": ce.CotizacionError("503")})
        msg = str(cm.exception)
        self.assertIn("DolarHoy", msg)
        self.assertIn("DolarApi", msg)

    def test_la_segunda_consulta_sale_del_cache(self):
        llamadas = []

        def get(url, **kw):
            llamadas.append(url)
            return DOLARHOY.encode("utf-8")
        with mock.patch.object(ce, "_get", get):
            ce.dolar("oficial")
            ce.dolar("oficial")
        self.assertEqual(len(llamadas), 1)

    def test_un_error_no_se_guarda_en_cache(self):
        with mock.patch.object(ce, "_get", _red(**{"dolarhoy.com": ce.CotizacionError("x"),
                                                    "dolarapi.com": ce.CotizacionError("y")})):
            with self.assertRaises(ce.CotizacionError):
                ce.dolar("oficial")
        with mock.patch.object(ce, "_get", _red(**{"dolarhoy.com": DOLARHOY})):
            self.assertEqual(ce.dolar("oficial")["valor"], 1570.0)


class TestNumeroArgentino(unittest.TestCase):
    def test_formatos(self):
        casos = {"$1.570": 1570.0, "1.587,90": 1587.9, "$ 1.520 ": 1520.0, "543,01": 543.01, "7.387,13": 7387.13}
        for txt, esperado in casos.items():
            self.assertEqual(ce._num_ar(txt), esperado, txt)

    def test_basura_es_none(self):
        self.assertIsNone(ce._num_ar("n/d"))
        self.assertIsNone(ce._num_ar(""))


class TestOcla(Base):
    def _leche(self, hoy=HOY, **paginas):
        with mock.patch.object(ce, "_get", _red(**paginas)):
            return ce.precio_leche(hoy=hoy)

    def test_toma_el_precio_nacional_de_la_portada(self):
        r = self._leche(**{"ocla.org.ar/": OCLA_HOME})
        self.assertEqual(r["valor"], 543.01)
        self.assertEqual((r["periodo"], r["etiqueta"]), ("2026-09", "SEP/2026"))
        self.assertIn("OCLA", r["fuente"])
        self.assertEqual(r["origen"], "OCLA SEP/2026")
        self.assertNotIn("7387", str(r["valor"]))            # no confundir con el precio por kg de sólidos útiles

    def test_avisa_si_el_dato_tiene_varios_meses(self):
        r = self._leche(hoy=datetime.date(2027, 3, 10), **{"ocla.org.ar/": OCLA_HOME})
        self.assertIn("meses", r["aviso"])
        self.assertEqual(r["antiguedad_meses"], 6)

    def test_un_dato_reciente_no_trae_aviso_de_antiguedad(self):
        r = self._leche(**{"ocla.org.ar/": OCLA_HOME})
        self.assertEqual(r["antiguedad_meses"], 2)
        self.assertFalse(r["aviso"])

    def test_si_la_portada_cambia_usa_el_excel_y_dice_hasta_que_mes_llega(self):
        xlsx = _xlsx_ocla([(datetime.datetime(2026, 5, 1), 508.74, 6000), (datetime.datetime(2026, 6, 1), 518.16, 6894),
                           (datetime.datetime(2026, 7, 1), None, None)])
        r = self._leche(**{"BaseDeDatosOCLA.xlsx": xlsx, "ocla.org.ar/": "<html>sin ticker</html>"})
        self.assertEqual((r["valor"], r["periodo"]), (518.16, "2026-06"))
        self.assertIn("Excel", r["fuente"])
        self.assertEqual(r["origen"], "OCLA (Excel) 06/2026")
        self.assertIn("meses", r["aviso"])                   # junio vs noviembre: atrasado

    def test_si_falla_todo_el_error_dice_que_cargue_a_mano(self):
        with self.assertRaises(ce.CotizacionError) as cm:
            self._leche(**{"ocla.org.ar/": ce.CotizacionError("timeout"),
                           "BaseDeDatosOCLA.xlsx": ce.CotizacionError("404")})
        self.assertIn("a mano", str(cm.exception))

    def test_un_precio_absurdo_se_rechaza(self):
        html = OCLA_HOME.replace("543,01", "5,43")
        with self.assertRaises(ce.CotizacionError):
            self._leche(**{"ocla.org.ar/": html, "BaseDeDatosOCLA.xlsx": ce.CotizacionError("x")})


if __name__ == "__main__":
    unittest.main()
