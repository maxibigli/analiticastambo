# -*- coding: utf-8 -*-
"""Vigilante: el texto del aviso.

Un HALLAZGO es un dict `{"tipo", "clave", "datos", "causas"}` que arma la pasada (`vigilante.py`):

    tipo   "baja_ordeno" | "vacas_faltantes" | "comida_atrasada" | "sin_datos" | "datos_vuelven"
    clave  la clave de dedup (`fecha|sesion|condicion`)
    datos  lo medido; para los dos de ordeño trae además `fecha` (ISO) y `sesion`
    causas frases ya medidas ("Rodeo 3: faltaron 24 vacas (680 kg)"), de más a menos peso

DOS formas de redactarlo, y la segunda es la que importa:
* `texto_deterministico`: plantilla con los números medidos. Siempre disponible.
* `redactar`: le pide a la IA que lo cuente en lenguaje de tambo, SOLO sobre esos hechos. La IA redacta, no
  decide si avisar ni agrega nada. Cada número y cada hora del texto que devuelve tiene que figurar en los datos
  medidos (mismo criterio que `agente._esta_entre`); si alguno no figura, o la IA falla, tarda, devuelve vacío
  o no hay `ANTHROPIC_API_KEY`, sale el texto determinístico. **Un aviso nunca depende de que la IA responda.**

«Sin datos» y «volvieron los datos» no gastan IA: son frases fijas.
"""
import datetime
import json
import re

import veredicto_baja

PRODUCCION = ("baja_ordeno", "vacas_faltantes", "comida_atrasada")
MAX_LINEAS_HALLAZGO = 6
MAX_CAUSAS = 4
TIMEOUT_IA_S = 20
MAX_TOKENS_IA = 600

SISTEMA = """Sos el redactor de avisos de LactIA, el sistema de un tambo argentino. Te paso hechos YA medidos \
(en JSON) sobre uno o más ordeños del día en curso y escribís el aviso que va por WhatsApp.

Reglas, todas obligatorias:
- No inventes cifras, causas, horas ni recomendaciones: usá solo lo que está en los hechos. Cada número y cada \
hora que escribas tiene que estar en los hechos tal cual (se redondea como figura).
- Seis líneas como máximo por hallazgo: qué pasó, cuánto, causa probable (solo las que figuran en `causas`), \
qué conviene mirar.
- Fechas con día de la semana ("sábado 14/11"), nunca «ayer» ni «ese día». Es el día en curso: no hables del día entero.
- Español rioplatense simple, sin saludos ni despedidas, sin encabezado (lo pone el sistema).
- Si no hay causas medidas, decí que no se pudo determinar la causa con los datos de ese ordeño."""


# --------------------------------------------------------------------------- formato
def _hhmm(minutos) -> str:
    m = int(minutos) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _n(x, dec=0):
    return veredicto_baja._n(x, dec)


def _fecha(iso) -> str:
    return veredicto_baja.fecha_larga(iso)


def encabezado(ahora: datetime.datetime, con_aviso_demora: bool = False) -> str:
    t = f"👁 Vigilante de LactIA — {_fecha(ahora.date().isoformat())} {ahora:%H:%M}"
    if con_aviso_demora:
        t += "\n(con los datos al momento: pueden estar llegando con demora)"
    return t


def _lineas(h: dict) -> list:
    d, tipo = h["datos"], h["tipo"]
    if tipo == "baja_ordeno":
        lineas = [f"⚠️ Baja en el ordeño {d['sesion']} del día en curso ({_fecha(d['fecha'])}): {_n(d['kg'])} kg, "
                  f"{_n(abs(d['pct']), 1)} % menos de lo normal (promedio de {_n(d['media'])} kg en los últimos "
                  f"{d['dias_base']} días)."]
    elif tipo == "vacas_faltantes":
        lineas = [f"⚠️ Faltaron {d['faltan']} vacas habituales en el ordeño {d['sesion']} del día en curso "
                  f"({_fecha(d['fecha'])}): pesan {_n(d['kg'])} kg, el {_n(d['pct'], 1)} % de lo normal."]
    elif tipo == "comida_atrasada":
        lineas = [f"🍽️ La primera comida del mixer fue a las {_hhmm(d['primera'])} (lo habitual es "
                  f"{_hhmm(d['habitual'])}): {_n(d['retraso'])} minutos más tarde."]
    elif tipo == "sin_datos":
        if d.get("ultima_fila"):
            ult = datetime.datetime.fromisoformat(d["ultima_fila"])
            desde = f"desde las {ult:%H:%M} del {_fecha(ult.date().isoformat())}"
        else:
            desde = "en todo el día"
        return [f"📡 Sin datos de ordeño {desde} (más de {_n(d['horas'])} h). Revisá la conexión con DelPro: "
                f"mientras tanto no se analiza la producción."]
    elif tipo == "datos_vuelven":
        ult = datetime.datetime.fromisoformat(d["ultima_fila"])
        return [f"✅ Volvieron los datos de ordeño (último a las {ult:%H:%M}). Se retoma el análisis."]
    else:
        raise ValueError(f"Tipo de hallazgo desconocido: {tipo}")
    causas = h.get("causas") or []
    if causas:
        lineas.append("Causas probables:")
        lineas += [f"• {c}" for c in causas[:MAX_CAUSAS]]
    elif tipo != "comida_atrasada":
        lineas.append("No se pudo determinar la causa con los datos de ese ordeño.")
    lineas = lineas[:MAX_LINEAS_HALLAZGO - len(h.get("advertencias") or [])]
    return lineas + [f"⚠️ {a}" for a in h.get("advertencias") or []]


def resumen_linea(h: dict) -> str:
    """La primera línea del hallazgo (lo que se cuenta en «el vigilante retuvo N aviso(s)»)."""
    return _lineas(h)[0]


def texto_deterministico(hallazgos: list, ahora: datetime.datetime, con_aviso_demora: bool = False) -> str:
    """UN solo mensaje con todos los hallazgos de la pasada."""
    partes = [encabezado(ahora, con_aviso_demora)]
    for h in hallazgos:
        partes.append("\n".join(_lineas(h)))
    return "\n\n".join(partes)


# --------------------------------------------------------------------------- validación de lo que escribe la IA
_FECHA_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_HORA_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_NUM_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")


def numeros_del_texto(texto: str) -> list:
    """Los números de un texto en español («1.024» = 1024, «10,2» = 10,2), sin contar fechas ni horas."""
    limpio = _HORA_RE.sub(" ", _FECHA_RE.sub(" ", texto))
    return [float(m.replace(".", "").replace(",", ".")) for m in _NUM_RE.findall(limpio)]


def _horas_del_texto(texto: str) -> set:
    return {f"{int(h):02d}:{m}" for h, m in _HORA_RE.findall(texto)}


def _recorrer(x, numeros: list, horas: set):
    """Junta todos los números (en positivo: «10,2 % menos» viene de -10,2) y las horas de los hechos."""
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        numeros.append(abs(float(x)))
    elif isinstance(x, str):
        numeros.extend(numeros_del_texto(x))
        horas.update(_horas_del_texto(x))
        try:
            horas.add(f"{datetime.datetime.fromisoformat(x):%H:%M}")
        except ValueError:
            pass
    elif isinstance(x, dict):
        for k, v in x.items():
            _recorrer(v, numeros, horas)
            if k in ("primera", "habitual") and isinstance(v, (int, float)):
                horas.add(_hhmm(v))
    elif isinstance(x, (list, tuple, set)):
        for v in x:
            _recorrer(v, numeros, horas)


def _texto_ia_valido(texto: str, hallazgos: list, ahora: datetime.datetime) -> bool:
    import agente
    numeros, horas = [], {f"{ahora:%H:%M}"}
    _recorrer(hallazgos, numeros, horas)
    ordenados = sorted(numeros)
    if not all(agente._esta_entre(v, ordenados) for v in numeros_del_texto(texto)):
        return False
    return _horas_del_texto(texto) <= horas


# --------------------------------------------------------------------------- redacción con IA
def redactar(hallazgos: list, ahora: datetime.datetime, con_aviso_demora: bool = False, cliente=None) -> str:
    """El aviso, con la IA si se puede y es de fiar, y con la plantilla si no. Nunca levanta por la IA."""
    determ = texto_deterministico(hallazgos, ahora, con_aviso_demora)
    prod = [h for h in hallazgos if h["tipo"] in PRODUCCION]
    otros = [h for h in hallazgos if h["tipo"] not in PRODUCCION]
    if not prod:
        return determ
    try:
        import agente
        if not agente.api_disponible():
            return determ
        cliente = cliente or agente._client()
        hechos = json.dumps([{"tipo": h["tipo"], "datos": h["datos"], "causas": h.get("causas") or []}
                             for h in prod], ensure_ascii=False)
        resp = cliente.messages.create(
            model=agente.MODEL, max_tokens=MAX_TOKENS_IA, system=SISTEMA, timeout=TIMEOUT_IA_S,
            messages=[{"role": "user", "content": f"Hechos medidos:\n{hechos}"}])
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not texto or not _texto_ia_valido(texto, prod, ahora):
            return determ
    except Exception:  # noqa: BLE001 - sin red, sin crédito, respuesta rara: sale la plantilla
        return determ
    partes = [encabezado(ahora, con_aviso_demora), texto]
    partes += ["\n".join(_lineas(h)) for h in otros]
    return "\n\n".join(partes)
