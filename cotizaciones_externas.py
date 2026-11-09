# -*- coding: utf-8 -*-
"""Precio de la leche (OCLA) y dólar (DolarHoy) traídos de internet, para cargarlos en ⚙ Configuración.

Solo LEE páginas públicas, a pedido (cuando se aprieta "Buscar"), nunca por su cuenta ni en segundo plano, y nunca
escribe nada: el valor se muestra en la pantalla y recién se guarda cuando la persona aprieta "Guardar".

  precio_leche()   $ por litro: precio NACIONAL al productor (SIGLeA, Dirección Nacional Lechera) que OCLA
                   (Observatorio de la Cadena Láctea Argentina) muestra en el encabezado de su portada. Es un promedio
                   del país: sirve de referencia, no es lo que paga SU usina (el tambo puede cargar el suyo a mano).
                   Respaldo: el Excel de series mensuales de OCLA, que se actualiza MUY atrasado (a noviembre de 2026
                   llegaba a junio): se usa solo si cambió la portada, y la respuesta avisa hasta qué mes llega.
  dolar(tipo)      $ por US$ (precio de VENTA): oficial, mayorista, MEP o blue, de DolarHoy.
                   Respaldo: DolarApi (dolarapi.com), una API pública que pide citarla como fuente.

NINGUNA de las dos fuentes ofrece una API para esto (DolarHoy y OCLA son sitios para leer): se lee el HTML, y su
estructura la define el sitio y puede cambiar. Por eso cada fuente tiene su respaldo, se rechazan los valores absurdos
y el error dice qué falló y que se puede cargar a mano. Las URLs son FIJAS (nada que llegue de la pantalla se usa
para armar una dirección: no hay forma de usar esto para pedir otra cosa).
"""
import datetime
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request

URL_OCLA = "https://www.ocla.org.ar/"
URL_OCLA_EXCEL = "https://www.ocla.org.ar/NewsFiles/BaseDeDatosOCLA.xlsx"
URL_DOLARHOY = "https://dolarhoy.com/"
URL_DOLARAPI = "https://dolarapi.com/v1/dolares/{casa}"

# Quién pide, para que el sitio sepa que es un programa del tambo y no un bot anónimo.
USER_AGENT = "LactIA/1.0 (software de gestion de tambo; consulta manual, no automatica)"
TIMEOUT_S = 20
MAX_BYTES = 6_000_000
CACHE_S = 3600           # DolarApi pide no repetir la misma consulta: una hora alcanza de sobra

TIPOS_DOLAR = {
    # tipo: (rótulo, ruta en DolarHoy, casa en DolarApi)
    "oficial": ("Dólar oficial", "/cotizaciondolaroficial", "oficial"),
    "mayorista": ("Dólar mayorista", "/cotizaciondolarmayorista", "mayorista"),
    "mep": ("Dólar MEP", "/cotizaciondolarbolsa", "bolsa"),
    "blue": ("Dólar blue", "/cotizaciondolarblue", "blue"),
}
LECHE_MIN, LECHE_MAX = 50.0, 5000.0          # $ por litro: fuera de esto es un error de lectura, no un precio
DOLAR_MIN, DOLAR_MAX = 100.0, 100000.0
MESES_AVISO = 3                              # un dato de más de 3 meses se marca como atrasado

_MESES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8,
          "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12}

_cache: dict = {}
_lock = threading.Lock()


class CotizacionError(Exception):
    """No se pudo obtener el valor: el mensaje se le muestra a la persona."""


# --------------------------------------------------------------------------- red y utilidades
def _get(url: str, timeout: int = TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            datos = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CotizacionError(f"respondió {exc.code}") from None
    except Exception as exc:  # noqa: BLE001 - sin internet, DNS, timeout, certificado...
        raise CotizacionError(f"no se pudo conectar ({type(exc).__name__})") from None
    if len(datos) > MAX_BYTES:
        raise CotizacionError("la respuesta es demasiado grande")
    return datos


def _num_ar(texto):
    """'$1.570' -> 1570.0 ; '1.587,90' -> 1587.9 ; '543,01' -> 543.01. Lo que no es número da None."""
    t = re.sub(r"[^\d.,]", "", str(texto or ""))
    if not t or not re.search(r"\d", t):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") >= 1 and re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")              # "1.570" son miles, no 1,57
    try:
        return float(t)
    except ValueError:
        return None


def _con_cache(clave, producir):
    with _lock:
        hit = _cache.get(clave)
        if hit and time.time() - hit[0] < CACHE_S:
            return dict(hit[1])
    valor = producir()                       # un error NO se guarda: el próximo intento vuelve a probar
    with _lock:
        _cache[clave] = (time.time(), valor)
    return dict(valor)


# --------------------------------------------------------------------------- dólar
def _dolar_dolarhoy(tipo: str) -> dict:
    rotulo, ruta, _ = TIPOS_DOLAR[tipo]
    html = _get(URL_DOLARHOY).decode("utf-8", errors="replace")
    p = re.escape(ruta)
    # Tarjeta grande: el enlace con el nombre y, más abajo, "Compra" y "Venta" con su valor ("$1.570").
    m = re.search(rf'href="{p}"[^>]*>[^<]*</a>.{{0,300}}?>Compra</div><div class="val">([^<]+)</div>'
                  rf'.{{0,500}}?>Venta</div><div class="val">([^<]+)</div>', html, re.S)
    if not m:
        # Tabla de cotizaciones: <a href=..><div class="title">..</div><div class="compra">1.438,00</div><div class="venta">..
        m = re.search(rf'href="{p}"><div class="title">[^<]*</div><div class="compra">([^<]+)</div>'
                      rf'<div class="venta">([^<]+)</div>', html, re.S)
    if not m:
        raise CotizacionError("no se encontró la cotización en la página (puede haber cambiado su diseño)")
    compra, venta = _num_ar(m.group(1)), _num_ar(m.group(2))
    if venta is None or not (DOLAR_MIN <= venta <= DOLAR_MAX):
        raise CotizacionError(f"el valor leído no es creíble ({m.group(2).strip()})")
    f = re.search(r"Actualizado por [^:<]{0,30}: ?([^<]+)<", html)
    return _dolar_resultado("DolarHoy", URL_DOLARHOY, tipo, rotulo, venta, compra, f.group(1).strip() if f else None)


def _dolar_resultado(fuente, url, tipo, rotulo, venta, compra, actualizado) -> dict:
    # `origen`: el texto corto que queda guardado junto al valor en la configuración y se muestra después.
    origen = f"{fuente} · {rotulo} (venta)" + (f" · {actualizado}" if actualizado else "")
    return {"valor": venta, "compra": compra, "tipo": tipo, "tipo_rotulo": f"{rotulo} (venta)", "fuente": fuente,
            "url": url, "actualizado": actualizado, "origen": origen}


def _dolar_dolarapi(tipo: str) -> dict:
    rotulo, _, casa = TIPOS_DOLAR[tipo]
    try:
        d = json.loads(_get(URL_DOLARAPI.format(casa=casa)).decode("utf-8", errors="replace"))
        venta = float(d["venta"])
        compra = float(d["compra"]) if d.get("compra") is not None else None
    except (ValueError, KeyError, TypeError):
        raise CotizacionError("respondió algo que no se entiende") from None
    if not (DOLAR_MIN <= venta <= DOLAR_MAX):
        raise CotizacionError(f"el valor leído no es creíble ({venta})")
    return _dolar_resultado("DolarApi", "https://dolarapi.com", tipo, rotulo, venta, compra,
                            d.get("fechaActualizacion"))


def dolar(tipo: str = "oficial") -> dict:
    """Cotización de venta del tipo pedido. DolarHoy primero; si falla, DolarApi (y la respuesta lo avisa)."""
    tipo = str(tipo or "").strip().lower()
    if tipo not in TIPOS_DOLAR:
        raise CotizacionError(f"Tipo de dólar desconocido: «{tipo}». Los posibles son: {', '.join(TIPOS_DOLAR)}.")

    def producir():
        try:
            r = _dolar_dolarhoy(tipo)
            r["aviso"] = ""
            return r
        except CotizacionError as e1:
            try:
                r = _dolar_dolarapi(tipo)
            except CotizacionError as e2:
                raise CotizacionError(
                    f"No se pudo traer el dólar. DolarHoy: {e1}. DolarApi: {e2}. Cargalo a mano.") from None
            r["aviso"] = f"No se pudo leer DolarHoy ({e1}); el valor viene de DolarApi (dolarapi.com)."
            return r
    return _con_cache(("dolar", tipo), producir)


# --------------------------------------------------------------------------- precio de la leche
def _leche_portada() -> dict:
    html = _get(URL_OCLA).decode("utf-8", errors="replace")
    m = re.search(r"SIGLeA\s*\(\s*([A-Za-z]{3})\s*/\s*(\d{4})\s*\)\s*:.{0,1500}?<span[^>]*>\s*([\d.,]+)\s*</span>"
                  r"\s*<i[^>]*>\s*/\s*litro", html, re.S | re.I)
    if not m:
        raise CotizacionError("no se encontró el precio en la portada (puede haber cambiado su diseño)")
    mes = _MESES.get(m.group(1).upper())
    valor = _num_ar(m.group(3))
    if not mes or valor is None:
        raise CotizacionError("el precio de la portada no se entiende")
    if not (LECHE_MIN <= valor <= LECHE_MAX):
        raise CotizacionError(f"el valor leído no es creíble ({m.group(3).strip()})")
    return {"valor": valor, "periodo": f"{m.group(2)}-{mes:02d}", "etiqueta": f"{m.group(1).upper()}/{m.group(2)}",
            "fuente": "OCLA · SIGLeA, precio nacional al productor", "url": URL_OCLA,
            "origen": f"OCLA {m.group(1).upper()}/{m.group(2)}"}


def _leche_excel() -> dict:
    try:
        import openpyxl
    except ImportError:
        raise CotizacionError("falta la librería openpyxl para leer el Excel de OCLA") from None
    datos = _get(URL_OCLA_EXCEL, timeout=40)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
        ws = wb["Series Mensuales"] if "Series Mensuales" in wb.sheetnames else wb.worksheets[0]
        filas = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception:  # noqa: BLE001
        raise CotizacionError("el Excel de OCLA no se pudo abrir") from None
    cab = [re.sub(r"\s+", " ", str(c or "")).strip().lower() for c in (filas[0] if filas else [])]
    col = next((i for i, c in enumerate(cab) if c.startswith("precio $/litro nacional")), None)
    if col is None:
        raise CotizacionError("el Excel de OCLA no trae la columna del precio por litro")
    ultimo = None
    for f in filas[1:]:
        if col < len(f) and isinstance(f[col], (int, float)) and f[col] > 0 and isinstance(f[0], (datetime.date, datetime.datetime)):
            ultimo = (f[0], float(f[col]))
    if not ultimo:
        raise CotizacionError("el Excel de OCLA no tiene precios cargados")
    mes, valor = ultimo
    if not (LECHE_MIN <= valor <= LECHE_MAX):
        raise CotizacionError(f"el valor leído no es creíble ({valor})")
    return {"valor": valor, "periodo": f"{mes.year}-{mes.month:02d}", "etiqueta": f"{mes.month:02d}/{mes.year}",
            "fuente": "OCLA · Excel de series mensuales (SIGLeA, precio nacional al productor)",
            "url": URL_OCLA_EXCEL, "origen": f"OCLA (Excel) {mes.month:02d}/{mes.year}"}


def precio_leche(hoy: datetime.date = None) -> dict:
    """Precio nacional de la leche al productor, $ por litro, con el mes al que corresponde."""
    hoy = hoy or datetime.date.today()

    def producir():
        try:
            return _leche_portada()
        except CotizacionError as e1:
            try:
                r = _leche_excel()
            except CotizacionError as e2:
                raise CotizacionError(f"No se pudo traer el precio de la leche. Portada de OCLA: {e1}. "
                                      f"Excel de OCLA: {e2}. Cargalo a mano.") from None
            r["nota"] = f"No se pudo leer la portada de OCLA ({e1}); se usó su Excel de series mensuales."
            return r
    r = _con_cache(("leche",), producir)
    a, m = (int(x) for x in r["periodo"].split("-"))
    r["antiguedad_meses"] = (hoy.year - a) * 12 + (hoy.month - m)
    avisos = [r.pop("nota", "")]
    if r["antiguedad_meses"] >= MESES_AVISO:
        avisos.append(f"Es el dato de {r['etiqueta']}: tiene {r['antiguedad_meses']} meses. OCLA lo publica con atraso; "
                      "si tu usina te paga otro valor, cargalo a mano.")
    r["aviso"] = " ".join(x for x in avisos if x)
    return r
