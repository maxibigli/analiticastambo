# -*- coding: utf-8 -*-
"""Busca, SOLO LECTURA, qué pudo causar la baja de producción de un día (versión de consola).

La lógica está en `diagnostico_baja.py`, la misma que usa la tarjeta "¿Por qué cambió la
producción?" de Consultas e IA. Corre donde la app llega a DelPro (SERVER-DELPRO).

    python diagnosticar_baja_produccion.py                                  # ayer, tambo por defecto
    python diagnosticar_baja_produccion.py 2026-11-07                       # un día puntual
    python diagnosticar_baja_produccion.py 2026-11-07 ponderosa             # y un tambo
    python diagnosticar_baja_produccion.py 2026-11-07 ponderosa 2026-10-20 2026-11-03   # y la base a comparar
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnostico_baja  # noqa: E402
from tambos import DEFAULT_TAMBO  # noqa: E402


def main(argv):
    dia = argv[0] if argv else (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    tambo = argv[1] if len(argv) > 1 else DEFAULT_TAMBO
    desde = argv[2] if len(argv) > 2 else None
    hasta = argv[3] if len(argv) > 3 else None
    try:
        print(diagnostico_baja.generar(tambo, dia, desde, hasta))
    except diagnostico_baja.DiagnosticoError as exc:
        sys.exit(f"No se puede hacer el análisis: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
