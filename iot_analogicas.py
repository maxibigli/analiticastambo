# -*- coding: utf-8 -*-
"""Entradas analógicas (4-20 mA) del gateway PUSR M300 asignadas a los vúmetros del panel IoT.

Cada entrada analógica (las 2 del M300 y las 4 del módulo de expansión USR-IO0440) se asigna a UN sensor de los
que ya muestra el panel (`iot_monitoreo.SENSORES_PLANEADOS`), con su escala. `iot_lavado.py` (único dueño de la
conexión Modbus) lee el registro, lo convierte con `convertir` y lo guarda en `lecturas_sensor`: el vúmetro se
enciende solo, igual que cuando se cablea cualquier otro sensor.

La escala son dos pares de números, y por eso no hay que saber cómo codifica el M300 la corriente:
  * `raw_4` / `raw_20`: el número CRUDO que da el registro a 4 mA y a 20 mA. Se toman con un simulador mirando el valor
    en vivo de ⚙ Configuración › 🔌 Entradas/Salidas.
  * `min` / `max`: qué valor del sensor son 4 y 20 mA (el rango del transmisor). En blanco toma el del sensor.

Se guarda en un JSON chico junto al código (estado propio de cada instalación, igual que iot_canales_nombres.json).
Deliberadamente SIN import de iot_monitoreo/iot_lavado, que son los que lo importan a él.
"""
import json
import os
import tempfile
import threading

_RUTA = os.path.join(os.path.dirname(__file__), "iot_analogicas.json")
_lock = threading.Lock()

CANALES = [(f"ai_{i}", f"Analógica {i}") for i in range(1, 7)]
CLAVES_VALIDAS = {c for c, _ in CANALES}

# Un lazo 4-20 mA sano nunca baja de 4 mA: por debajo de ~3,5 mA es un cable cortado o un transmisor apagado, no una
# medición. Guardarlo como un valor mostraría, por ejemplo, leche a 0 °C porque se soltó un cable.
MA_MIN_VALIDO = 3.5
MA_MAX_VALIDO = 21.0

_CAMPOS = ("sensor", "direccion", "raw_4", "raw_20", "min", "max")


def _numero(valor, nombre, clave, entero=False):
    """None/"" -> None (campo sin cargar). Cualquier otra cosa tiene que ser un número (o un entero)."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, bool):
        raise ValueError(f"{clave}: «{nombre}» tiene que ser un número.")
    try:
        n = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{clave}: «{nombre}» tiene que ser un número (llegó {valor!r}).") from None
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError(f"{clave}: «{nombre}» no es un número válido.")
    if entero and not n.is_integer():
        raise ValueError(f"{clave}: «{nombre}» tiene que ser un número entero (llegó {valor!r}).")
    return int(n) if n.is_integer() else n


def cargar() -> dict:
    """clave -> {sensor, direccion, raw_4, raw_20, min, max}. Solo las entradas que el tambo configuró; {} si no hay
    archivo (o está roto: antes dejar la lectura de sensores sin andar que fallar el ciclo del gateway)."""
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {c: {k: v.get(k) for k in _CAMPOS} for c, v in datos.items()
            if c in CLAVES_VALIDAS and isinstance(v, dict)}


def guardar(nuevo: dict, sensores: dict) -> None:
    """Valida TODO antes de tocar el archivo: un guardado que falla no pisa lo anterior.

    `sensores` es clave -> (min, max) de los sensores asignables (los del panel, sin el ITH, que se calcula).
    """
    limpio = {}
    sensores_usados = {}    # sensor -> canal que ya lo tiene
    direcciones_usadas = {}  # dirección -> canal que ya la lee
    for clave, c in (nuevo or {}).items():
        if clave not in CLAVES_VALIDAS:
            raise ValueError(f"Entrada analógica desconocida: {clave!r}.")
        c = c or {}
        sensor = c.get("sensor")
        sensor = sensor.strip() if isinstance(sensor, str) else sensor
        sensor = sensor or None
        if sensor is not None and sensor not in sensores:
            raise ValueError(f"{clave}: el sensor {sensor!r} no existe o no se puede asignar a una entrada.")
        if sensor is not None:
            if sensor in sensores_usados:
                raise ValueError(
                    f"El sensor {sensor!r} está asignado a dos entradas ({sensores_usados[sensor]} y {clave}): "
                    "no hay forma de saber cuál manda en el vúmetro.")
            sensores_usados[sensor] = clave

        direccion = _numero(c.get("direccion"), "dirección Modbus", clave, entero=True)
        if direccion is not None:
            if not 0 <= direccion <= 65535:
                raise ValueError(f"{clave}: la dirección Modbus tiene que estar entre 0 y 65535.")
            if direccion in direcciones_usadas:
                raise ValueError(
                    f"La dirección Modbus {direccion} está en dos entradas ({direcciones_usadas[direccion]} y "
                    f"{clave}).")
            direcciones_usadas[direccion] = clave

        raw_4 = _numero(c.get("raw_4"), "crudo a 4 mA", clave)
        raw_20 = _numero(c.get("raw_20"), "crudo a 20 mA", clave)
        if raw_4 is not None and raw_20 is not None and raw_4 == raw_20:
            raise ValueError(f"{clave}: el crudo a 4 mA y a 20 mA no pueden ser iguales.")

        minimo = _numero(c.get("min"), "valor a 4 mA", clave)
        maximo = _numero(c.get("max"), "valor a 20 mA", clave)
        if sensor is not None:
            minimo = sensores[sensor][0] if minimo is None else minimo
            maximo = sensores[sensor][1] if maximo is None else maximo
        if minimo is not None and maximo is not None and minimo >= maximo:
            raise ValueError(f"{clave}: el valor a 4 mA tiene que ser menor que el de 20 mA.")

        entrada = {"sensor": sensor, "direccion": direccion, "raw_4": raw_4, "raw_20": raw_20,
                   "min": minimo, "max": maximo}
        if any(v is not None for v in entrada.values()):   # una fila en blanco no se guarda
            limpio[clave] = entrada

    with _lock:
        # Se escribe a un temporal y se reemplaza: iot_lavado.py lo lee en cada ciclo y no debe ver un archivo a medias.
        carpeta = os.path.dirname(_RUTA) or "."
        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=carpeta)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(limpio, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _RUTA)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


def convertir(raw, cfg: dict):
    """Del número crudo del registro a {"ma", "valor", "sin_senal"}.

    None si la entrada no está calibrada o no tiene rango (no se inventa un valor). `valor` es None cuando la
    corriente está fuera de lo que puede dar un lazo sano (cable cortado): ahí no hay medición."""
    raw_4, raw_20 = cfg.get("raw_4"), cfg.get("raw_20")
    minimo, maximo = cfg.get("min"), cfg.get("max")
    if raw is None or None in (raw_4, raw_20, minimo, maximo) or raw_4 == raw_20:
        return None
    ma = 4 + 16 * (raw - raw_4) / (raw_20 - raw_4)
    if not MA_MIN_VALIDO <= ma <= MA_MAX_VALIDO:
        return {"ma": round(ma, 2), "valor": None, "sin_senal": True}
    valor = minimo + (ma - 4) / 16 * (maximo - minimo)
    return {"ma": round(ma, 2), "valor": round(valor, 2), "sin_senal": False}
