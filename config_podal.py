# -*- coding: utf-8 -*-
"""Configuración de las cámaras del módulo "Problemas podales" (renguera),
una por tambo. Sigue el mismo criterio que `tambos.py`: nada sensible se
escribe en este archivo, la fuente de cada cámara (URL RTSP, que puede traer
usuario/contraseña embebidos) sale de una variable de entorno.

Para HABILITAR las cámaras de un tambo:

    setx PODAL_CAM_POSICION_PONDEROSA "rtsp://usuario:clave@192.168.1.30/stream1"
    setx PODAL_CAM_MARCHA_PONDEROSA   "rtsp://usuario:clave@192.168.1.31/stream1"

y agregar el tambo a PODAL_TAMBOS más abajo. Sin esas variables, el módulo
queda deshabilitado (así arranca hoy: no hay cámaras instaladas todavía) y la
interfaz sigue mostrando el placeholder "a instalar".

Las dos cámaras miran el MISMO corredor de salida de la rotativa:
- "posicion": vista amplia del punto de salida, para confirmar el instante en
  que un animal pasa (no lo identifica por imagen: eso lo resuelve
  `podal.resolver_rp` cruzando ese instante con los datos de DelPro).
- "marcha": vista lateral del mismo corredor, para el análisis de marcha.
"""
import os

# Tambos con el módulo podal activable. Agregar el id (el mismo que en
# tambos.TAMBOS) para que la app intente conectar sus cámaras.
PODAL_TAMBOS = ("ponderosa",)

TOLERANCIA_SEG_DEFECTO = 90     # ventana para cruzar el evento de cámara con DelPro
FPS_PROCESAMIENTO = 8            # a cuántos cuadros/seg se procesa (no hace falta más)
FRAMES_AUSENCIA_FIN_PASADA = 6   # cuadros seguidos sin detección = "terminó de pasar"


def _var(tambo_id: str, camara: str) -> str:
    return f"PODAL_CAM_{camara.upper()}_{tambo_id.upper()}"


def config_de(tambo_id: str) -> dict:
    """Config de cámaras del tambo: fuentes (URL RTSP, ruta de archivo o índice
    de webcam) y si está habilitado. `fuente` puede ser None si falta la
    variable de entorno correspondiente."""
    fuente_posicion = os.environ.get(_var(tambo_id, "posicion")) or None
    fuente_marcha = os.environ.get(_var(tambo_id, "marcha")) or None
    habilitado = (
        tambo_id in PODAL_TAMBOS
        and fuente_marcha is not None
    )
    return {
        "habilitado": habilitado,
        "fuente_posicion": fuente_posicion,
        "fuente_marcha": fuente_marcha,
        "tolerancia_seg": TOLERANCIA_SEG_DEFECTO,
    }


def tambos_configurables() -> list:
    return list(PODAL_TAMBOS)
