# -*- coding: utf-8 -*-
"""Integración con La Serenísima / Mastellone (pmpl-laserenisima.com.ar):
datos físicos OFICIALES del comprador por entrega — litros, grasa, proteínas,
UFC y temperatura — para el tambo 1565 (La Ponderosa).

Credenciales SOLO por variable de entorno (nunca en el código):
  LASER_USUARIO, LASER_PASSWORD

Estructura verificada a mano (2026-07-23):
  - Login: POST a pages/publico/login2.aspx con
    WebUsersLogin2$cmbLoggingType=Propietario,
    WebUsersLogin2$login$UserName / WebUsersLogin2$login$Password.
  - Datos: GET directo (sin postback previo) a los iframes de la página
    "Datos Físicos":
      Pages/DatosFisicos/DatosFisicosCuerpoLeft.aspx?idTambo=1565&periodo=actual
        -> tabla[1] (52 filas limpias): Fecha, Fecha Entrega, Estado Sanitario, Litros
           (tabla[0] tiene una fila "blob" de scroll-sync con todo concatenado: NO usar)
      Pages/DatosFisicos/DatosFisicosCuerpoContent.aspx?idTambo=1565&periodo=actual
        -> tabla[0] (52 filas + 1 fila TOTAL al final, excluirla): Grasa, Proteínas,
           S.Útiles, Criosc., Células, U.F.C., Temp., Inhibidor, Aflatox M1, Urea,
           Ring Test, Lactosa, S.Tot, Termo.
  - Verificado: suma de Litros de las 52 filas = 1.200.317, coincide con el total
    mostrado en la página.
"""
import threading

import requests
from bs4 import BeautifulSoup

BASE = "https://www.pmpl-laserenisima.com.ar"
LOGIN_URL = BASE + "/pages/publico/login2.aspx"
TAMBO_ID = "1565"  # La Ponderosa

_lock = threading.Lock()


class LaserError(Exception):
    pass


def _campos_ocultos(form) -> dict:
    d = {}
    for inp in form.find_all("input"):
        nombre = inp.get("name")
        if nombre:
            d[nombre] = inp.get("value") or ""
    for sel in form.find_all("select"):
        nombre = sel.get("name")
        if not nombre:
            continue
        op = sel.find("option", selected=True) or sel.find("option")
        d[nombre] = op.get("value") if op else ""
    return d


def _num(txt):
    """'3,73' (formato AR) -> float. Vacío -> None."""
    if txt is None:
        return None
    txt = txt.strip().replace(".", "").replace(",", ".")
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _iniciar_sesion(usuario: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    r1 = s.get(LOGIN_URL, timeout=20)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    form1 = soup1.find("form")
    if form1 is None:
        raise LaserError("No se pudo leer la página de login de La Serenísima.")
    datos = _campos_ocultos(form1)
    datos["WebUsersLogin2$cmbLoggingType"] = "Propietario"
    datos["WebUsersLogin2$login$UserName"] = usuario
    datos["WebUsersLogin2$login$Password"] = password
    datos["WebUsersLogin2$login$LoginButton"] = "ENTRAR"
    url_post = requests.compat.urljoin(r1.url, form1.get("action") or "")
    r2 = s.post(url_post, data=datos, timeout=20)
    if "login$Password" in r2.text:
        raise LaserError("La Serenísima rechazó el usuario/contraseña "
                          "(revisá LASER_USUARIO/LASER_PASSWORD).")
    return s


def obtener_entregas(usuario: str, password: str, periodo: str = "actual") -> list[dict]:
    """Trae y combina las dos tablas (litros + físico-químico) del tambo 1565."""
    with _lock:
        s = _iniciar_sesion(usuario, password)
        base_df = BASE + "/Pages/DatosFisicos/"
        r_left = s.get(f"{base_df}DatosFisicosCuerpoLeft.aspx?idTambo={TAMBO_ID}&periodo={periodo}", timeout=20)
        r_content = s.get(f"{base_df}DatosFisicosCuerpoContent.aspx?idTambo={TAMBO_ID}&periodo={periodo}", timeout=20)

    tablas_izq = BeautifulSoup(r_left.text, "html.parser").find_all("table")
    if len(tablas_izq) < 2:
        raise LaserError("No se pudo leer la tabla de entregas (¿sesión vencida o sin datos?).")
    filas_izq = tablas_izq[1].find_all("tr")  # tabla[0] es un blob de scroll-sync, no usar

    tablas_der = BeautifulSoup(r_content.text, "html.parser").find_all("table")
    filas_der = tablas_der[0].find_all("tr")[:-1] if tablas_der else []  # última fila = TOTAL

    entregas = []
    for i, tr in enumerate(filas_izq):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 4:
            continue
        tdc = ([td.get_text(strip=True) for td in filas_der[i].find_all("td")]
               if i < len(filas_der) else [])

        def col(idx):
            return _num(tdc[idx]) if idx < len(tdc) else None

        entregas.append({
            "fecha": tds[0], "fecha_entrega": tds[1], "estado_sanitario": tds[2],
            "litros": _num(tds[3]),
            "grasa": col(0), "proteinas": col(1), "s_utiles": col(2), "criosc": col(3),
            "celulas": col(4), "ufc": col(5), "temperatura": col(6),
        })
    return entregas
