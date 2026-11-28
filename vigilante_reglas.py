# -*- coding: utf-8 -*-
"""Vigilante: las reglas que deciden si un ordeño salió claramente peor que lo normal.

Todo acá es PURO: funciones sobre datos ya armados, sin base, sin red y sin reloj propio (el momento "ahora"
siempre viene de afuera). Así se prueban sin nada conectado y se pueden reproducir sobre días pasados
(`reproducir_vigilante.py`) con exactamente las mismas reglas que corren en vivo.

Las constantes de abajo son PROPUESTAS del diseño y se calibran contra datos reales (Tarea 7 del plan): están
juntas, con nombre, para cambiarlas en un solo lugar.

Se compara ordeño contra ordeño (mismo número de ordeño de los días anteriores), nunca el día a medio hacer
contra días enteros. Un ordeño pertenece al día en que EMPEZÓ.
"""
import datetime
import statistics as st
from dataclasses import dataclass

import diagnostico_baja
import veredicto_baja

Z_FUERTE = veredicto_baja.Z_FUERTE                   # 2 desvíos o más por debajo de la base
BAJA_MIN_PCT = 4.0                                   # y al menos 4 % por debajo de la media de la base
MIN_BASE = 3                                         # menos ordeños de base que esto: no se opina
PCT_VACAS_AUSENTES = veredicto_baja.PCT_VACAS_AUSENTES   # vacas que faltan y pesan >= 1 % de los kg de la base
ALIM_RETRASO_MIN = veredicto_baja.ALIM_RETRASO_MIN   # minutos de retraso de la primera comida
QUIETO_MIN = 45                                      # minutos sin filas nuevas para dar un ordeño por terminado


@dataclass(frozen=True)
class Ordeno:
    fecha: str                 # ISO, el día en que EMPEZÓ
    sesion: int                # número de ordeño del día (SessionNo)
    vacas: int
    kg: float
    inicio: datetime.datetime
    fin: datetime.datetime


def armar_ordeno(sesion: int, vacas: int, kg: float, inicio: datetime.datetime, fin: datetime.datetime) -> Ordeno:
    """Un `Ordeno` cuyo día es el de `inicio`: uno de 23:50 a 01:30 es del día que empezó."""
    return Ordeno(inicio.date().isoformat(), int(sesion), int(vacas), float(kg), inicio, fin)


def ordeno_terminado(ordeno: Ordeno, ahora: datetime.datetime, quieto_min: int = QUIETO_MIN) -> bool:
    """Terminó si pasaron `quieto_min` minutos desde su última fila sin que entrara ninguna más."""
    return ahora - ordeno.fin >= datetime.timedelta(minutes=quieto_min)


def baja_ordeno(actual: Ordeno, base: list, min_pct: float | None = None) -> dict | None:
    """`{"kg", "media", "z", "pct", "dias_base"}` si los kg de este ordeño están a 2 desvíos o más por debajo de
    los mismos ordeños de la base Y a 4 % o más por debajo de su media; si no, `None`. Las dos condiciones a la
    vez: con una sola, una base muy pareja avisaría por 1 % y una muy dispareja nunca avisaría.

    `base`: los ordeños del mismo número de ordeño de los días anteriores COMPLETOS (los arma quien llama).
    Con menos de `MIN_BASE` no se opina: no hay con qué comparar."""
    if len(base) < MIN_BASE:
        return None
    serie = [o.kg for o in base]
    media = st.mean(serie)
    if media <= 0:
        return None
    z = diagnostico_baja._z(actual.kg, serie)
    pct = (actual.kg - media) / media * 100
    if z <= -Z_FUERTE and pct <= -(BAJA_MIN_PCT if min_pct is None else min_pct):
        return {"kg": round(actual.kg, 1), "media": round(media, 1), "z": round(z, 1), "pct": round(pct, 1),
                "dias_base": len(base)}
    return None


def vacas_faltantes(presentes: set, habituales: dict, kg_base: float, pct_min: float | None = None) -> dict | None:
    """Vacas habituales de ese ordeño que no pasaron hoy. `habituales`: vaca -> kg medio que da en ese ordeño.
    `None` si lo que pesan las ausentes es menos de `pct_min` (fracción, 1 % por defecto) de `kg_base`, o si no hay kg
    de base."""
    if kg_base <= 0:
        return None
    ausentes = sorted(v for v in habituales if v not in presentes)
    kg = sum(habituales[v] for v in ausentes)
    if not ausentes or kg / kg_base < (PCT_VACAS_AUSENTES if pct_min is None else pct_min):
        return None
    return {"faltan": len(ausentes), "kg": round(kg, 1), "pct": round(kg / kg_base * 100, 1),
            "numeros": ausentes[:12]}


def sin_datos(ultima_fila: datetime.datetime | None, ahora: datetime.datetime, horas: float,
              ventana: tuple) -> bool:
    """Verdadero si, dentro de la ventana en que se suele ordeñar, no entra nada hace `horas` o más.

    `ventana` = (hora en que abre, hora en que cierra) del ordeño habitual. Fuera de ella nunca es «sin datos»,
    y el silencio se cuenta DENTRO de la ventana: a las 05:30, con la última fila de anoche, no pasaron 9 horas
    sin datos sino media hora desde que abrió el ordeño."""
    abre, cierra = ventana
    if not (abre <= ahora.hour < cierra):
        return False
    apertura = ahora.replace(hour=abre, minute=0, second=0, microsecond=0)
    referencia = apertura if ultima_fila is None else max(ultima_fila, apertura)
    return ahora - referencia >= datetime.timedelta(hours=horas)


def sin_datos_desde_apertura(ultima_fila: datetime.datetime | None, ahora: datetime.datetime, horas: float,
                             aperturas: list) -> bool:
    """Verdadero si un ordeño que ya tendría que haber arrancado (su hora de apertura de HOY ya pasó) no tiene ni una
    fila desde que abrió y pasaron `horas` o más. A diferencia de `sin_datos`, no depende de una ventana: un corte de
    la noche se ve al abrir el ordeño de la madrugada aunque su ventana ya haya cerrado a la hora en que se levanta el
    silencio, y no se pierde si una pasada cae fuera de la última hora útil de la ventana.

    `aperturas`: la hora en que suele empezar cada ordeño (los que casi nunca se hacen no van: los arma quien llama)."""
    for h in aperturas:
        apertura = ahora.replace(hour=h, minute=0, second=0, microsecond=0)
        if (apertura <= ahora and (ultima_fila is None or ultima_fila < apertura)
                and ahora - apertura >= datetime.timedelta(hours=horas)):
            return True
    return False


def comida_atrasada(r: dict | None, hoy: datetime.date, minimo: int = ALIM_RETRASO_MIN) -> dict | None:
    """Sobre el resultado de `alimentacion_dia.ultima_comida` (`fecha`, `primera`, `habitual`, `retraso`):
    solo si la primera comida es de HOY y se atrasó `minimo` minutos o más."""
    if not r or r.get("fecha") != hoy.isoformat() or r.get("retraso") is None or r["retraso"] < minimo:
        return None
    return {"fecha": r["fecha"], "primera": r["primera"], "habitual": r["habitual"], "retraso": r["retraso"]}
