# -*- coding: utf-8 -*-
"""Despachos de leche CARGADOS EN DELPRO (menú Leche > Despachos de leche), como origen
alternativo de las cargas que hoy salen del caudalímetro (ver `cicla.py`).

Se elige por tambo en ⚙ Configuración > "Origen de las cargas". Devuelve las cargas con las MISMAS
claves que `cicla._carga_desde_api`, así la pantalla "Entregas de leche" y `cicla.resumen()` andan
sin cambios, más algunos campos que solo DelPro tiene (tanque, camión, calidad de la leche).

QUÉ ES CADA COSA (esquema DDM, verificado en las columnas; los DATOS no se pudieron mirar porque la
copia local de La Ponderosa tiene estas tablas en 0 filas):

  MilkShipmentBase     un despacho (remito): DeliveryDate, DeliveryNoteNumber, Tank, MilkTruckId,
                       CreameryName, AmountOfMilk, MilkTemperature y la calidad que informa la usina
                       (Fat, Protein, Scc, Dcc, BacterialCount, Mun, Acidity, Blood, Inhibitors).
  MilkForInternalUsage leche para uso interno (guachera, etc.): EventDate, InternalAmountOfMilk,
                       InternalMilkUsage (un entero: el tipo de uso).
  GCRecord             borrado lógico de DevExpress XPO: solo cuentan las filas con GCRecord NULL.

LO QUE DIFIERE DEL CAUDALÍMETRO, y por qué no son intercambiables sin avisar:
  * Los litros de DelPro son los que se CARGARON en el despacho (lo del remito), no una medición:
    no hay un segundo número con el que compararlos. Por eso `lts_declarados` y la diferencia quedan
    en None y no hay "alerta de diferencia" (con el caudalímetro sí: medido contra declarado).
  * Todo despacho de DelPro es a una usina, o sea una venta: `es_venta` es siempre True. La leche
    que se queda en el tambo va en `MilkForInternalUsage`, aparte, no mezclada con los despachos.
  * SUPUESTO SIN VERIFICAR: que `AmountOfMilk` esté en litros (DelPro lo guarda en la unidad de
    volumen del tambo; `MilkPrice.PricePerVolumeUnit` habla de "unidad de volumen"). Hay que
    confirmarlo contra un despacho real antes de comparar contra el comprador.
  * `InternalMilkUsage` es un código sin tabla de nombres a la vista: se muestra como "Uso N", no se
    le inventa un significado.
"""
from __future__ import annotations

import datetime
import re

import cicla
import db


class DespachosError(Exception):
    """Un fallo al leer los despachos, con un mensaje listo para mostrar."""


def _fecha_sql(d: datetime.date) -> str:
    # Estilo 23 (aaaa-mm-dd): no depende del idioma de la conexión (con 'dmy', '2026-11-08' podría
    # leerse como 2026-08-11).
    return f"CONVERT(date, '{d.isoformat()}', 23)"


def sql_despachos(desde: datetime.date, hasta: datetime.date) -> str:
    """Rango INCLUSIVE de días. `desde`/`hasta` son `date` (ya validadas), nunca texto del usuario."""
    return f"""
        SELECT s.DeliveryDate AS fecha, s.DeliveryNoteNumber AS remito, s.Tank AS tanque,
               s.MilkTruckId AS camion, s.CreameryName AS usina, s.AmountOfMilk AS litros,
               s.MilkTemperature AS temperatura, s.Fat AS grasa, s.Protein AS proteina,
               s.Scc AS rcs, s.Dcc AS dcc, s.BacterialCount AS cbt, s.Mun AS mun,
               s.Acidity AS acidez, s.Blood AS sangre, s.Inhibitors AS inhibidores
        FROM MilkShipmentBase s
        WHERE s.GCRecord IS NULL
          AND s.DeliveryDate >= {_fecha_sql(desde)}
          AND s.DeliveryDate < DATEADD(day, 1, {_fecha_sql(hasta)})
        ORDER BY s.DeliveryDate
    """


def sql_uso_interno(desde: datetime.date, hasta: datetime.date) -> str:
    return f"""
        SELECT u.EventDate AS fecha, u.InternalAmountOfMilk AS litros, u.InternalMilkUsage AS uso
        FROM MilkForInternalUsage u
        WHERE u.GCRecord IS NULL
          AND u.EventDate >= {_fecha_sql(desde)}
          AND u.EventDate < DATEADD(day, 1, {_fecha_sql(hasta)})
        ORDER BY u.EventDate
    """


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _positivo(v) -> float | None:
    """Los campos de calidad vienen en 0 cuando la usina no los informó: un 0 de RCS o de grasa no
    es una medición, es 'sin dato'. Mostrarlo como 0 arruinaría los promedios."""
    n = _num(v)
    return n if n is not None and n > 0 else None


def _fecha_ar(valor) -> str:
    """El formato que ya espera la pantalla: 'dd/mm/aaaa hh:mm:ss' (el día se toma con split(' ')[0])."""
    if isinstance(valor, str):
        try:
            valor = datetime.datetime.fromisoformat(valor)
        except ValueError:
            return valor
    if isinstance(valor, datetime.datetime):
        return valor.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(valor, datetime.date):
        return valor.strftime("%d/%m/%Y 00:00:00")
    return ""


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (texto or "").lower()).strip("_")


def carga_desde_fila(r: dict) -> dict:
    """Un despacho de DelPro con las claves que ya usaban `cicla.resumen()`, app.py y la pantalla."""
    litros = _num(r.get("litros"))
    temp = _num(r.get("temperatura"))
    usina = (r.get("usina") or "").strip()
    remito = "" if r.get("remito") is None else str(r["remito"])
    return {
        "turno": "", "carga": remito, "fecha": _fecha_ar(r.get("fecha")),
        "cliente": usina, "equipo": (r.get("tanque") or "").strip(), "remito": remito,
        "nro_cliente": usina or "?", "destino": _slug(usina) or "sin_usina",
        "destino_label": usina or "Sin usina",
        "es_venta": True,                       # un despacho siempre va a una usina
        "lts_cicla": litros,                    # el nombre de la clave es del caudalímetro; acá son los litros del despacho
        "lts_declarados": None, "diferencia": None, "diferencia_pct": None,
        "temperatura": temp, "ph": None,
        "alerta_diferencia": False,             # no hay un segundo número con que compararlo
        "alerta_temp": temp is not None and temp > cicla.UMBRAL_TEMP_C,
        # Solo de DelPro:
        "tanque": (r.get("tanque") or "").strip(), "camion": (r.get("camion") or "").strip(),
        "calidad": {
            "grasa": _positivo(r.get("grasa")), "proteina": _positivo(r.get("proteina")),
            "rcs": _positivo(r.get("rcs")), "dcc": _positivo(r.get("dcc")), "cbt": _positivo(r.get("cbt")),
            "mun": _positivo(r.get("mun")), "acidez": _positivo(r.get("acidez")),
            "sangre": _positivo(r.get("sangre")), "inhibidores": _positivo(r.get("inhibidores")),
        },
    }


def _filas(resultado: dict) -> list[dict]:
    cols = resultado["columns"]
    return [dict(zip(cols, fila)) for fila in resultado["rows"]]


def obtener_cargas(tambo: str, desde: datetime.date, hasta: datetime.date) -> tuple[list[dict], bool]:
    """Despachos cargados en DelPro entre dos fechas (incluidas): (cargas, incompleto).
    `incompleto` es True si la consulta llegó al tope de filas de `db.run_query`."""
    try:
        r = db.run_query(sql_despachos(desde, hasta), tambo=tambo)
    except Exception as exc:  # noqa: BLE001
        raise DespachosError(f"No se pudieron leer los despachos de DelPro: {exc}") from exc
    return [carga_desde_fila(f) for f in _filas(r)], bool(r.get("truncated"))


def uso_interno(tambo: str, desde: datetime.date, hasta: datetime.date) -> list[dict]:
    """Leche para uso interno en el rango. El tipo de uso se deja como el código de DelPro."""
    try:
        r = db.run_query(sql_uso_interno(desde, hasta), tambo=tambo)
    except Exception as exc:  # noqa: BLE001
        raise DespachosError(f"No se pudo leer la leche de uso interno de DelPro: {exc}") from exc
    return [{"fecha": _fecha_ar(f["fecha"]), "litros": _num(f["litros"]),
             "uso": f["uso"], "uso_label": f"Uso {f['uso']}" if f["uso"] is not None else "Sin tipo"}
            for f in _filas(r)]


def calidad_resumen(cargas: list[dict]) -> dict:
    """Promedio de la calidad de la leche despachada, PONDERADO por los litros de cada despacho (un
    camión de 25.000 L pesa más que uno de 700). Solo cuentan los despachos que informaron cada
    campo; `n` dice cuántos fueron, para no leer un promedio de 1 despacho como si fueran todos."""
    res = {}
    for campo in ("grasa", "proteina", "rcs", "dcc", "cbt"):
        pares = [(c["calidad"][campo], c["lts_cicla"] or 0) for c in cargas if c["calidad"].get(campo) is not None]
        peso = sum(p for _v, p in pares)
        if pares and peso:
            valor = sum(v * p for v, p in pares) / peso
        elif pares:
            valor = sum(v for v, _p in pares) / len(pares)
        else:
            valor = None
        res[campo] = {"promedio": None if valor is None else round(valor, 2 if campo in ("grasa", "proteina") else 0),
                      "n": len(pares)}
    return res
