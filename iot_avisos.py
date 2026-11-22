"""Avisa por los canales de alerta (WhatsApp, Telegram, correo) cuando un sensor del Monitoreo IoT se queda sin mandar datos.

Un sensor está mudo cuando `iot_monitoreo.lecturas_actuales` lo marca `sin_datos` (más de `VENTANA_LECTURA_RECIENTE_MIN`
minutos sin una lectura). Se avisa UNA vez cuando se calla y UNA vez cuando vuelve a hablar, nunca en cada pasada. A quién ya
se avisó se guarda en `iot_avisos.json` (estado propio de cada instalación, fuera de git): un reinicio del servidor no
repite el aviso de un sensor que sigue mudo. Si el envío falla no se anota nada y la pasada siguiente reintenta.

Sin horario silencioso, a propósito: que un sensor de la leche o de la sala de máquinas deje de hablar a las 3 de la mañana
es justo lo que se quiere saber. Si se cortó todo (por ejemplo la luz) sale un solo mensaje con todos los sensores.
El ITH no avisa aparte: se calcula con la temperatura y la humedad ambiente y se calla cuando se calla alguna de las dos.
"""
import datetime
import json
import logging
import os
import threading

import iot_monitoreo

log = logging.getLogger(__name__)

RUTA_ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iot_avisos.json")
CADA_MIN = 5                   # cada cuánto se mira: la ventana de «mudo» son 30, con 5 se entera en el rato
PRIMERA_ESPERA_S = 120         # después de arrancar el servidor: da tiempo a que `iot_lavado.py` siga escribiendo

_DIAS_SEMANA = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_lock = threading.Lock()


def _cargar() -> dict:
    try:
        with open(RUTA_ESTADO, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _guardar(avisados: dict) -> None:
    tmp = RUTA_ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"avisados": avisados}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUTA_ESTADO)


def _cuando(fecha_hora: str) -> str:
    """«dom 22/11 17:25». La fecha concreta con su día de la semana: un aviso se lee mucho después de mandado."""
    d = datetime.datetime.fromisoformat(fecha_hora)
    return f"{_DIAS_SEMANA[d.weekday()]} {d:%d/%m %H:%M}"


def _numero(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",") if v is not None else ""


def _linea_mudo(s: dict) -> str:
    if not s.get("fecha_hora"):
        return f"• {s['label']}: nunca mandó datos"
    ultimo = f" (último valor {_numero(s['ultimo_valor'])}{s.get('unidad') or ''})" if s.get("ultimo_valor") is not None else ""
    return f"• {s['label']}: sin datos desde el {_cuando(s['fecha_hora'])}{ultimo}"


def revisar(enviar, ahora: datetime.datetime = None):
    """Una pasada: manda UN mensaje con los sensores que se callaron y los que volvieron desde la pasada anterior.
    Devuelve el texto mandado, o None si no había nada nuevo. Si `enviar` levanta, el error sale y NO se anota nada."""
    ahora = ahora or datetime.datetime.now()
    with _lock:
        sensores = [s for s in iot_monitoreo.lecturas_actuales(ahora) if s["clave"] != "ith"]
        mudos = {s["clave"]: s for s in sensores if s["sin_datos"]}
        avisados = _cargar().get("avisados") or {}
        nuevos = [mudos[k] for k in mudos if k not in avisados]
        por_clave = {s["clave"]: s for s in sensores}
        volvieron = [por_clave[k]["label"] if k in por_clave else k for k in avisados if k not in mudos]
        if not nuevos and not volvieron:
            return None
        partes = []
        if nuevos:
            partes.append("⚠️ LactIA: sensores sin datos\n" + "\n".join(_linea_mudo(s) for s in nuevos))
        if volvieron:
            partes.append("✅ Volvieron a mandar datos: " + ", ".join(volvieron))
        texto = "\n\n".join(partes)
        enviar(texto)
        _guardar({k: avisados.get(k) or ahora.isoformat(timespec="seconds") for k in mudos})
        return texto


def iniciar_hilo(enviar, parar: threading.Event = None, espera_s: float = None, primera_espera_s: float = None):
    """Arranca el hilo que revisa cada `CADA_MIN` minutos. Cada pasada va en su propio try/except: un envío que falla
    (sin canal configurado, sin internet) no mata el hilo y se reintenta en la pasada siguiente.

    NO se arranca al importar `app`: lo llama `servidor.py` después de tomar el candado de instancia única, igual que el
    vigilante (si no, `precalentar.py` o las pruebas tendrían cada uno el suyo y el mismo aviso saldría dos veces)."""
    parar = parar or threading.Event()

    def bucle():
        parar.wait(PRIMERA_ESPERA_S if primera_espera_s is None else primera_espera_s)
        while not parar.is_set():
            try:
                revisar(enviar)
            except Exception as exc:  # noqa: BLE001
                log.warning("aviso de sensores mudos: la pasada falló (%s); se reintenta", exc)
            parar.wait(CADA_MIN * 60 if espera_s is None else espera_s)

    hilo = threading.Thread(target=bucle, name="avisos-iot", daemon=True)
    hilo.start()
    return hilo
