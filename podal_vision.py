# -*- coding: utf-8 -*-
"""Visión por cámara para "Problemas podales" (renguera).

Dos cámaras miran el MISMO corredor de salida de la rotativa (ver
`config_podal.py`):
- "posicion": vista amplia del punto de salida. NO reconoce a la vaca por
  imagen -- solo confirma el instante en que algo cruza por ahí. La
  identidad del animal sale de cruzar ese instante con `MilkingDeviceVisit`/
  `CMSMilkYield` de DelPro, que ya sabe qué vaca estaba en qué puesto y
  cuándo terminó de ordeñarse (ver `podal.resolver_rp`). Reconocer la vaca
  por imagen (caravana, RFID visual) sería mucho menos confiable que usar el
  dato que DelPro ya tiene.
- "marcha": vista lateral del mismo corredor, para medir cómo camina (perfil
  del lomo mientras cruza el cuadro) y sacar un score de renguera.

Es un pipeline HEURÍSTICO v1: sustracción de fondo + curvatura del lomo, sin
modelos entrenados ni datos de este tambo (no hay cámaras instaladas
todavía). Los umbrales de `UMBRAL_*` son un punto de partida de la
bibliografía de "locomotion scoring" (lomo arqueado al caminar = señal
clásica de dolor podal, escala tipo Sprecher/Zinpro 1-5), pero DEPENDEN de la
resolución y distancia real de la cámara: hay que recalibrarlos con casos
reales (vacas con renguera confirmada por el veterinario) apenas haya video
del tambo -- mismo criterio que dejó `salud.calcular_atencion_v2` marcado
como EXPERIMENTAL hasta validarlo a campo.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:  # pesa varios MB; solo hace falta si las cámaras están activas
    cv2 = None

AREA_MINIMA_PX = 4000       # contorno mínimo para considerar "hay un animal"
MUESTRAS_PERFIL = 60        # columnas del bbox que se muestrean para el perfil del lomo
MIN_FRAMES_UTILES = 5       # por debajo de esto, la pasada no alcanza para puntuar

# Umbrales de partida (a recalibrar con video real, ver docstring del módulo).
UMBRAL_CURVATURA_LEVE = 0.015
UMBRAL_CURVATURA_ALTA = 0.035
UMBRAL_OSCILACION_ALTA = 18.0


def _requiere_cv2():
    if cv2 is None:
        raise RuntimeError(
            "Falta opencv-python-headless. Instalá las dependencias "
            "(pip install -r requirements.txt) para usar la visión de cámaras."
        )


class DetectorPresencia:
    """Sustracción de fondo (MOG2): detecta si hay un animal en cuadro y
    devuelve su contorno más grande. Sirve para las dos cámaras: en
    "posicion" solo importa el booleano de presencia; en "marcha" además se
    usa la máscara para el perfil del lomo."""

    def __init__(self, historia: int = 300, umbral: float = 24.0):
        _requiere_cv2()
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=historia, varThreshold=umbral, detectShadows=True)

    def procesar(self, frame):
        """Devuelve (presente, bbox (x,y,w,h) o None, máscara binaria)."""
        mask = self._bg.apply(frame)
        # MOG2 marca las sombras en gris (127); solo interesa el objeto (255).
        _, binaria = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contornos:
            return False, None, binaria
        mayor = max(contornos, key=cv2.contourArea)
        if cv2.contourArea(mayor) < AREA_MINIMA_PX:
            return False, None, binaria
        return True, cv2.boundingRect(mayor), binaria


def _perfil_lomo(mascara_bin, bbox):
    """Perfil superior de la silueta dentro del bbox (una muestra por
    columna): el primer píxel "encendido" bajando desde arriba, en cada
    columna muestreada. Es el perfil del lomo visto de costado."""
    x, y, w, h = bbox
    recorte = mascara_bin[y:y + h, x:x + w]
    paso = max(1, w // MUESTRAS_PERFIL)
    xs, ys = [], []
    for col in range(0, w, paso):
        idx = np.nonzero(recorte[:, col])[0]
        if idx.size:
            xs.append(col)
            ys.append(idx[0])
    return np.array(xs, dtype=float), np.array(ys, dtype=float)


def curvatura_lomo(mascara_bin, bbox) -> float | None:
    """Coeficiente cuadrático del perfil del lomo (ajuste parabólico). Un lomo
    arqueado hacia arriba -- señal clásica de renguera al caminar -- da un
    valor más alto que un lomo recto."""
    xs, ys = _perfil_lomo(mascara_bin, bbox)
    if xs.size < 8:
        return None
    # y de imagen crece hacia abajo: se invierte para que "más arqueado" dé
    # coeficiente positivo (más intuitivo para los umbrales de arriba).
    coef = np.polyfit(xs, -ys, 2)
    return float(coef[0])


def procesar_secuencia_marcha(frames: list) -> dict | None:
    """Recibe los frames capturados mientras el animal cruzó la cámara de
    "marcha" (ya recortados a la ventana de presencia por quien capturó) y
    devuelve las métricas agregadas, o None si la pasada no dio suficientes
    cuadros útiles para puntuar."""
    _requiere_cv2()
    detector = DetectorPresencia()
    curvaturas, centros_y = [], []
    for frame in frames:
        presente, bbox, mask = detector.procesar(frame)
        if not presente:
            continue
        c = curvatura_lomo(mask, bbox)
        if c is not None:
            curvaturas.append(c)
        centros_y.append(bbox[1] + bbox[3] / 2)
    if len(curvaturas) < MIN_FRAMES_UTILES:
        return None
    # Se descartan los extremos (entrada/salida de cuadro, cuerpo parcial):
    recorte_ini = len(curvaturas) // 5
    recorte = curvaturas[recorte_ini: len(curvaturas) - recorte_ini] or curvaturas
    return {
        "curvatura_prom": float(np.mean(recorte)),
        "curvatura_max": float(np.max(recorte)),
        "oscilacion_vertical": float(np.std(centros_y)) if len(centros_y) > 1 else 0.0,
        "n_frames": len(curvaturas),
    }


def score_renguera(metricas: dict) -> dict:
    """Score heurístico 1 (marcha normal) a 5 (renguera severa), estilo
    Sprecher/Zinpro. HEURÍSTICO SIN CALIBRAR con video real -- ver nota del
    módulo. Devuelve el score junto con los motivos en criollo."""
    curv, curv_max = metricas["curvatura_prom"], metricas["curvatura_max"]
    osc = metricas["oscilacion_vertical"]
    motivos = []
    score = 1.0
    if curv_max > UMBRAL_CURVATURA_ALTA:
        score += 2.0
        motivos.append("lomo muy arqueado durante el paso")
    elif curv > UMBRAL_CURVATURA_LEVE:
        score += 1.0
        motivos.append("lomo algo arqueado")
    if osc > UMBRAL_OSCILACION_ALTA:
        score += 1.0
        motivos.append("marcha irregular (oscilación vertical alta)")
    return {"score": min(5.0, round(score, 1)), "motivos": motivos, **metricas}
