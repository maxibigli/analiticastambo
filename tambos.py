# -*- coding: utf-8 -*-
"""Configuración de los tambos del cliente.

Cada tambo de DelPro tiene su propia base DDM (una instalación por tambo).
Para AGREGAR UN TAMBO NUEVO basta con copiar un bloque aquí y completar el
servidor y la base — el listbox de la aplicación se actualiza solo.

  server:   instancia de SQL Server. Ejemplos:
              "localhost\\DELPRO"        (esta misma PC)
              "192.168.1.20\\DELPRO"     (otra PC en la red, por IP)
              "PC-DONGERMAN\\DELPRO"     (otra PC en la red, por nombre)
  database: nombre de la base (normalmente "DDM").
  auth:     "windows" usa la sesión de Windows (Trusted_Connection).
            "sql"     usa usuario y contraseña de SQL Server.
  rebanos:  QUÉ REBAÑOS de esa base son de este tambo, como lista de OID de
            `Herd`. Una misma base DDM puede tener VARIOS tambos adentro: la de
            La Ponderosa tiene tres (rebaño 1 = La Ponderosa, 6 = Don Germán,
            7 = SB). Sin esta clave, la aplicación deduce el rebaño mirando
            dónde están los grupos de ordeñe, lo que anda mientras haya un solo
            tambo ordeñando pero elige uno solo —y en silencio— si hubiera más.
            Declararlo es siempre preferible.
            Para separar los tambos de una misma base, se agrega un bloque por
            cada uno con el mismo server/database y distintos `rebanos`:

                "ponderosa":  { ..., "rebanos": [1] },
                "don_german": { ..., "rebanos": [6] },

            Un tambo puede tener más de un rebaño: "rebanos": [1, 4].
  produccion: True marca al tambo como base de producción EN VIVO. En ese caso la
            app BLOQUEA las preguntas por IA (lo único que ejecuta SQL generado):
            solo quedan disponibles el dashboard, la rotativa, las tareas y las
            consultas fijas, todas de solo lectura. Recomendado al apuntar a la
            base que graba el ordeño en vivo.

CONTRASEÑAS: nunca se escriben en este archivo. Con auth="sql" la contraseña se
lee de una VARIABLE DE ENTORNO (ver `password_de` más abajo):

    setx DELPRO_PWD_PONDEROSA "la-contraseña"      # tambo "ponderosa"
    setx DELPRO_PWD_DON_GERMAN "la-contraseña"     # tambo "don_german"

Es decir: DELPRO_PWD_ + el id del tambo en MAYÚSCULAS. Si preferís otro nombre
de variable, indicalo con la clave "password_env".
"""
import os

TAMBOS = {
    "ponderosa": {
        "nombre": "La Ponderosa",
        "server": "SERVER-DELPRO\\DELPRO",
        "database": "DDM",
        #auth": "windows",
        # Al apuntar a la base de producción en vivo, agregar:  "produccion": True
        # (bloquea las preguntas por IA; ver INSTALL.md).
        # Para usar el usuario de solo lectura en producción, reemplazar por:
         "auth": "sql",
         "user": "delpro_lectura",
        # Esta base la comparten tres tambos; La Ponderosa es el rebaño 1.
        # Don Germán (6) y SB (7) se pueden agregar como bloques aparte con el
        # mismo server/database y su propio "rebanos".
         "rebanos": [1],
        # (la contraseña va en la variable de entorno DELPRO_PWD_PONDEROSA)
    },

    # Copia local en esta PC de desarrollo (útil cuando no hay red hacia
    # SERVER-DELPRO, p. ej. la PC recién salió de suspensión). Autenticación
    # de Windows: no hace falta contraseña.
    "ponderosa_local": {
        "nombre": "La Ponderosa (local)",
        "server": "localhost\\DELPRO",
        "database": "DDM",
        "auth": "windows",
        "rebanos": [1],
    },

    # --- Plantilla para agregar otro tambo (descomentar y completar) ---
    # "don_german": {
    #     "nombre": "Don Germán",
    #     "server": "192.168.1.20\\DELPRO",   # IP o nombre de la PC del tambo
    #     "database": "DDM",
    #     "auth": "sql",
    #     "user": "delpro_lectura",
    #     # contraseña en la variable de entorno DELPRO_PWD_DON_GERMAN
    #     # (o indicá otro nombre con "password_env": "MI_VARIABLE")
    # },
}

# Tambo que se muestra por defecto al abrir la aplicación.
DEFAULT_TAMBO = "ponderosa"


def rebanos_de(tambo_id: str) -> list:
    """OID de los rebaños (`Herd`) que son de este tambo.

    Lista vacía = no está declarado, y quien llame decide qué hacer (ver
    `rebano.por_defecto`, que en ese caso cae a deducirlo).
    """
    cfg = TAMBOS.get(tambo_id, {})
    valor = cfg.get("rebanos") or cfg.get("rebano")
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        return [int(v) for v in valor]
    return [int(valor)]


def nombre_variable_password(tambo_id: str) -> str:
    """Nombre de la variable de entorno donde se espera la contraseña."""
    cfg = TAMBOS.get(tambo_id, {})
    return cfg.get("password_env") or f"DELPRO_PWD_{tambo_id.upper()}"


def password_de(tambo_id: str) -> str:
    """Contraseña del tambo, leída de la variable de entorno correspondiente.

    Como último recurso acepta una clave "password" escrita en este archivo,
    pero NO es recomendable: quedaría la contraseña en texto plano en el código.
    """
    cfg = TAMBOS.get(tambo_id, {})
    desde_entorno = os.environ.get(nombre_variable_password(tambo_id))
    return desde_entorno or cfg.get("password", "")


def es_produccion(tambo_id: str) -> bool:
    """True si el tambo apunta a una base de producción en vivo (bloquea IA)."""
    return bool(TAMBOS.get(tambo_id, {}).get("produccion"))


def existe(tambo_id: str) -> bool:
    return tambo_id in TAMBOS


def resolver(tambo_id: str) -> str:
    """Devuelve un id de tambo válido (usa el default si no existe)."""
    return tambo_id if tambo_id in TAMBOS else DEFAULT_TAMBO


def lista() -> list:
    return [{"id": k, "nombre": v["nombre"], "produccion": es_produccion(k)}
            for k, v in TAMBOS.items()]
