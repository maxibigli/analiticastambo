"""Avisa por los canales de alerta (WhatsApp, Telegram, correo) cuando un sensor del Monitoreo IoT se queda sin mandar datos.

Un sensor está mudo cuando `iot_monitoreo.lecturas_actuales` lo marca `sin_datos` (más de `VENTANA_LECTURA_RECIENTE_MIN`
minutos sin una lectura). Se avisa UNA vez cuando se calla y UNA vez cuando vuelve a hablar, nunca en cada pasada. A quién ya
se avisó se guarda en `iot_avisos.json` (estado propio de cada instalación, fuera de git): un reinicio del servidor no
repite el aviso de un sensor que sigue mudo. Si el envío falla no se anota nada y la pasada siguiente reintenta.

Sin horario silencioso, a propósito: que un sensor de la leche o de la sala de máquinas deje de hablar a las 3 de la mañana
es justo lo que se quiere saber. Si se cortó todo (por ejemplo la luz) sale un solo mensaje con todos los sensores.
El ITH no avisa aparte: se calcula con la temperatura y la humedad ambiente y se calla cuando se calla alguna de las dos.

También avisa de las ALARMAS DIGITALES (`iot_sensores`: un contacto con `avisar` y un estado en rojo): cuando entra en rojo y
cuando vuelve. Se leen los CAMBIOS (`eventos_di`), no el estado de ahora, para que una alarma que entró y salió entre dos
pasadas igual se avise. `ultimo_evento` es hasta qué cambio se miró. La primera vez no repite el historial: solo avisa lo
que está en alarma en ese momento.
"""
import datetime
import json
import logging
import os
import sqlite3
import threading

import iot_monitoreo
import iot_sensores

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


def _guardar(avisados: dict, ultimo_evento) -> None:
    tmp = RUTA_ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"avisados": avisados, "ultimo_evento": ultimo_evento}, f, ensure_ascii=False, indent=2)
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
    if s.get("tipo") == "digital":
        ultimo = f" (último estado: {s['ultimo_estado']})" if s.get("ultimo_estado") else ""
    else:
        ultimo = (f" (último valor {_numero(s['ultimo_valor'])}{s.get('unidad') or ''})"
                  if s.get("ultimo_valor") is not None else "")
    return f"• {s['label']}: sin datos desde el {_cuando(s['fecha_hora'])}{ultimo}"


MAX_LINEAS_ALARMA = 10         # un contacto que rebota no tiene que llenar el mensaje


def _alarmas_digitales(ultimo_evento, sensores: list):
    """(líneas, nuevo `ultimo_evento`) de los cambios de las entradas digitales con aviso desde la pasada anterior."""
    con_aviso = {s["entrada"]: s for s in iot_sensores.digitales()
                 if s["entrada"] and s["avisar"] and iot_sensores.estado_de_alarma(s) is not None}
    try:
        con = sqlite3.connect(iot_monitoreo.RUTA_DB)
        try:
            if ultimo_evento is None:
                fila = con.execute("SELECT MAX(id) FROM eventos_di").fetchone()
                eventos = []
                ultimo = fila[0] or 0
            else:
                eventos = con.execute("SELECT id, canal, fecha_hora, estado FROM eventos_di WHERE id > ? ORDER BY id",
                                      (ultimo_evento,)).fetchall()
                ultimo = eventos[-1][0] if eventos else ultimo_evento
        finally:
            con.close()
    except sqlite3.OperationalError:               # todavía no existe la tabla: el gateway nunca corrió
        return [], ultimo_evento or 0
    lineas = []
    if ultimo_evento is None:
        # Primera vez: sin historial viejo, solo lo que está en alarma ahora.
        for s in sensores:
            if s.get("tipo") == "digital" and s.get("avisar") and s.get("zona") == "rojo" and s.get("fecha_hora"):
                lineas.append(f"🔴 {s['label']}: {s['estado']} ({_cuando(s['fecha_hora'])})")
        return lineas, ultimo
    for _id, canal, fecha_hora, estado in eventos:
        s = con_aviso.get(canal)
        if s is None:
            continue
        en_alarma = bool(estado) == iot_sensores.estado_de_alarma(s)
        texto = s["texto_activo"] if estado else s["texto_inactivo"]
        lineas.append(f"{'🔴' if en_alarma else '✅'} {s['label']}: {texto} ({_cuando(fecha_hora)})")
    if len(lineas) > MAX_LINEAS_ALARMA:
        lineas = lineas[:MAX_LINEAS_ALARMA] + [f"… y {len(lineas) - MAX_LINEAS_ALARMA} cambios más"]
    return lineas, ultimo


def revisar(enviar, ahora: datetime.datetime = None):
    """Una pasada: manda UN mensaje con los sensores que se callaron y los que volvieron desde la pasada anterior.
    Devuelve el texto mandado, o None si no había nada nuevo. Si `enviar` levanta, el error sale y NO se anota nada."""
    ahora = ahora or datetime.datetime.now()
    with _lock:
        sensores = [s for s in iot_monitoreo.lecturas_actuales(ahora) if s["clave"] != "ith"]
        mudos = {s["clave"]: s for s in sensores if s["sin_datos"]}
        estado = _cargar()
        avisados = estado.get("avisados") or {}
        ultimo_evento = estado.get("ultimo_evento")
        nuevos = [mudos[k] for k in mudos if k not in avisados]
        por_clave = {s["clave"]: s for s in sensores}
        volvieron = [por_clave[k]["label"] if k in por_clave else k for k in avisados if k not in mudos]
        alarmas, nuevo_ultimo = _alarmas_digitales(ultimo_evento, sensores)
        if not nuevos and not volvieron and not alarmas:
            if nuevo_ultimo != ultimo_evento:
                _guardar(avisados, nuevo_ultimo)       # nada que avisar, pero ya se miraron esos cambios
            return None
        partes = []
        if alarmas:
            partes.append("🚨 LactIA: alarmas\n" + "\n".join(alarmas))
        if nuevos:
            partes.append("⚠️ LactIA: sensores sin datos\n" + "\n".join(_linea_mudo(s) for s in nuevos))
        if volvieron:
            partes.append("✅ Volvieron a mandar datos: " + ", ".join(volvieron))
        texto = "\n\n".join(partes)
        enviar(texto)
        _guardar({k: avisados.get(k) or ahora.isoformat(timespec="seconds") for k in mudos}, nuevo_ultimo)
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
