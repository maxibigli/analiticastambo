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
import msvcrt
import os
import sys

HOST = os.environ.get("DELPRO_HOST", "127.0.0.1")
PORT = int(os.environ.get("DELPRO_PORT", "5310"))

_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".servidor.lock")
_lock_fd = None  # se mantiene abierto mientras el proceso vive -- si se cierra, se libera el lock


def _tomar_lock_de_instancia_unica() -> bool:
    """True si conseguimos el lock (somos la única instancia). False si ya hay otra corriendo.

    En SERVER-DELPRO se vieron dos procesos servidor.py arrancados casi al
    mismo instante (Programador de tareas de Windows, causa puntual nunca
    confirmada del todo) -- cada uno con su propio ciclo de alertas de
    WhatsApp/Telegram/Email en segundo plano, mandando el doble de mensajes
    del resumen y de las alertas. En vez de perseguir la causa exacta del
    lado de Windows, la app se protege sola: si otra instancia ya tiene el
    lock, esta se cierra en vez de arrancar una segunda.

    Probado con un bind de socket primero y DESCARTADO: en Windows, sin
    SO_EXCLUSIVEADDRUSE, un segundo proceso puede bindear (e incluso robarle
    el puerto) a otro que ya está en LISTEN, aunque ninguno use SO_REUSEADDR
    -- no sirve para detectar el conflicto de forma confiable. Un archivo de
    lock sí es exclusivo en Windows mientras el descriptor siga abierto: si
    otro proceso ya lo tiene abierto, msvcrt.locking tira OSError al toque."""
    global _lock_fd
    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    _lock_fd = fd
    return True


if __name__ == "__main__":
    # Chequeo ANTES de importar `app` a propósito: ese import es pesado (carga
    # Flask entero y ya arranca sus hilos de fondo, entre ellos el ciclo de
    # alertas de las 8:00/20:00) y tarda unos segundos. Si el chequeo fuera
    # DESPUÉS del import, una instancia duplicada quedaría viva ese rato largo
    # con sus propios hilos ya corriendo ANTES de detectar el conflicto y
    # cerrarse -- exactamente el problema que esto tiene que evitar, solo que
    # retrasado en vez de prevenido.
    if not _tomar_lock_de_instancia_unica():
        print(f"Ya hay otra instancia de Analitica DelPro corriendo (lock tomado en {_LOCK_PATH}). "
              "No se arranca una segunda -- cerrando esta.")
        sys.exit(1)

    from waitress import serve

    from app import app

    print(f"Analitica DelPro escuchando en http://{HOST}:{PORT}  (Ctrl+C para salir)")
    # threads=8 alcanza para varios usuarios a la vez; el acceso a SQL Server ya
    # se serializa en db.py para no saturar la instancia Express.
    serve(app, host=HOST, port=PORT, threads=8)
