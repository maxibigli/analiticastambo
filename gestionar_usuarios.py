# -*- coding: utf-8 -*-
"""Alta/baja/listado de usuarios de Analítica DelPro.

Uso:
    python gestionar_usuarios.py crear <usuario> <admin|operario>
    python gestionar_usuarios.py eliminar <usuario>
    python gestionar_usuarios.py listar

La contraseña se pide de forma OCULTA (no se ve en pantalla ni queda en el
historial de la terminal). Nunca se escribe en ningún archivo de código.
"""
import getpass
import sys

import auth


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    accion = sys.argv[1]

    if accion == "crear":
        if len(sys.argv) != 4:
            print("Uso: python gestionar_usuarios.py crear <usuario> <admin|operario>")
            return
        usuario, rol = sys.argv[2], sys.argv[3]
        if rol not in auth.ROLES:
            print(f"Rol inválido: {rol} (opciones: {', '.join(auth.ROLES)})")
            return
        password = getpass.getpass("Contraseña: ")
        confirmar = getpass.getpass("Repetí la contraseña: ")
        if not password:
            print("La contraseña no puede estar vacía.")
            return
        if password != confirmar:
            print("Las contraseñas no coinciden.")
            return
        auth.crear_o_actualizar(usuario, password, rol)
        print(f"Usuario '{usuario}' ({rol}) guardado.")

    elif accion == "eliminar":
        if len(sys.argv) != 3:
            print("Uso: python gestionar_usuarios.py eliminar <usuario>")
            return
        auth.eliminar(sys.argv[2])
        print(f"Usuario '{sys.argv[2]}' eliminado (si existía).")

    elif accion == "listar":
        usuarios = auth.listar()
        if not usuarios:
            print("Todavía no hay usuarios creados.")
        for u in usuarios:
            print(f"  {u['usuario']}  ({u['rol']})")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
