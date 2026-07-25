# -*- coding: utf-8 -*-
"""Punto de entrada para PRODUCCIÓN.

Flask incluye un servidor de desarrollo que NO debe usarse en producción
(monohilo y sin robustez). Este script sirve la misma aplicación con
**waitress**, un servidor WSGI estable y pensado para Windows.

Uso:
    python servidor.py

Variables de entorno opcionales:
    DELPRO_HOST   127.0.0.1 = solo esta PC (por defecto)
                  0.0.0.0   = accesible desde otras PCs del tambo
    DELPRO_PORT   puerto (5310 por defecto)
"""
import os

from waitress import serve

from app import app

HOST = os.environ.get("DELPRO_HOST", "127.0.0.1")
PORT = int(os.environ.get("DELPRO_PORT", "5310"))

if __name__ == "__main__":
    print(f"Analitica DelPro escuchando en http://{HOST}:{PORT}  (Ctrl+C para salir)")
    # threads=8 alcanza para varios usuarios a la vez; el acceso a SQL Server ya
    # se serializa en db.py para no saturar la instancia Express.
    serve(app, host=HOST, port=PORT, threads=8)
