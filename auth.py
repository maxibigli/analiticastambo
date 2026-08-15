# -*- coding: utf-8 -*-
"""Login y roles de Analítica DelPro (sesión de Flask + usuarios locales).

Los usuarios se guardan en `usuarios.json` (mismo directorio), con la
contraseña ya HASHEADA (nunca en texto plano). Para crear, cambiar o borrar un
usuario usá `python gestionar_usuarios.py` — pide la contraseña oculta (no
queda en el historial de la terminal ni la ve nadie más).

Roles:
  "admin"    -- ve todo: Dashboard, Ordeño, Rutina, Evolución, Tareas, Consultas.
  "operario" -- ve Ordeño, Rutina, Tareas y Consultas. NO ve Dashboard (alertas,
                integraciones CICLA/La Serenísima) ni Evolución.
"""
import functools
import json
import os
import threading
import time

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

_RUTA_USUARIOS = os.path.join(os.path.dirname(__file__), "usuarios.json")

ROLES = ("admin", "operario")

# Páginas del sidebar visibles por rol. El frontend oculta las que no
# corresponden y el backend además bloquea sus endpoints (ver requiere_rol).
PAGINAS_POR_ROL = {
    # El orden es el mismo que el del menú lateral (ver templates/index.html):
    # cosmético para esta lista (solo se usa para membresía, "x in ..."), pero
    # mantenerlo igual evita que alguien lea un orden que ya no es el real.
    "admin": ["dashboard", "consultas", "tablero", "ordeno", "sala_cms", "rutina", "rendimiento", "flujos",
              "evolucion", "proyeccion", "repro", "alimentacion", "entregas", "salud", "ficha", "iot",
              "tareas", "checklist", "sensehub", "configuracion"],
    # "rendimiento" (Rendimiento Sala) estaba dentro de la página "rutina", así
    # que el operario ya lo veía: se le deja al pasar a sección propia.
    "operario": ["ordeno", "sala_cms", "rutina", "rendimiento", "tareas", "consultas"],
}


def _leer_usuarios() -> dict:
    try:
        with open(_RUTA_USUARIOS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _guardar_usuarios(usuarios: dict) -> None:
    with open(_RUTA_USUARIOS, "w", encoding="utf-8") as f:
        json.dump(usuarios, f, indent=2, ensure_ascii=False)


def crear_o_actualizar(usuario: str, password: str, rol: str) -> None:
    if rol not in ROLES:
        raise ValueError(f"Rol inválido: {rol} (opciones: {', '.join(ROLES)})")
    usuarios = _leer_usuarios()
    usuarios[usuario] = {"password_hash": generate_password_hash(password), "rol": rol}
    _guardar_usuarios(usuarios)


def eliminar(usuario: str) -> None:
    usuarios = _leer_usuarios()
    usuarios.pop(usuario, None)
    _guardar_usuarios(usuarios)


def listar() -> list:
    return [{"usuario": u, "rol": d["rol"]} for u, d in _leer_usuarios().items()]


def hay_usuarios() -> bool:
    return bool(_leer_usuarios())


def verificar(usuario: str, password: str):
    """Devuelve el rol si usuario/contraseña son correctos, si no None."""
    datos = _leer_usuarios().get(usuario)
    if not datos or not check_password_hash(datos["password_hash"], password):
        return None
    return datos["rol"]


def usuario_actual():
    return session.get("usuario")


def rol_actual():
    return session.get("rol")


def paginas_visibles() -> list:
    return PAGINAS_POR_ROL.get(rol_actual(), [])


# --- Bloqueo por intentos fallidos de login (defensa mínima al exponer la app
# a internet vía túnel). En memoria: alcanza para frenar fuerza bruta básica;
# se reinicia si se reinicia el servidor.
_LIMITE_INTENTOS = 5
_BLOQUEO_S = 10 * 60  # 10 minutos
_intentos_fallidos: dict = {}
_intentos_lock = threading.Lock()


def ip_cliente() -> str:
    """IP real del cliente. Detrás de un túnel (Cloudflare) o proxy, la conexión
    TCP que ve Flask es siempre la del túnel/proxy local — la IP real viaja en
    la cabecera CF-Connecting-IP (o X-Forwarded-For como respaldo)."""
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "desconocida")


def login_bloqueado(ip: str) -> bool:
    with _intentos_lock:
        cant, ultimo = _intentos_fallidos.get(ip, (0, 0))
        if cant < _LIMITE_INTENTOS:
            return False
        if time.time() - ultimo > _BLOQUEO_S:
            _intentos_fallidos.pop(ip, None)
            return False
        return True


def registrar_intento_fallido(ip: str) -> None:
    with _intentos_lock:
        cant, _ = _intentos_fallidos.get(ip, (0, 0))
        _intentos_fallidos[ip] = (cant + 1, time.time())


def limpiar_intentos(ip: str) -> None:
    with _intentos_lock:
        _intentos_fallidos.pop(ip, None)


def requiere_rol(*roles_permitidos):
    """Decorador para endpoints de una sección restringida a ciertos roles
    (ej. Dashboard/Evolución solo para 'admin'). El login en sí ya lo exige
    `_requerir_login` en app.py para toda la app."""
    def decorador(vista):
        @functools.wraps(vista)
        def envoltorio(*args, **kwargs):
            if "usuario" not in session:
                return jsonify({"error": "No autenticado"}), 401
            if session.get("rol") not in roles_permitidos:
                return jsonify({"error": "No tenés permiso para acceder a esta sección."}), 403
            return vista(*args, **kwargs)
        return envoltorio
    return decorador
