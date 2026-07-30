# -*- coding: utf-8 -*-
"""Proveedores de datos de alimentación.

El tambo puede tener un mixer con computadora (hoy Haasten), otro sistema
mañana (MixerOne), o ninguno. El resto de la aplicación —`conciliacion.py` y las
pantallas de costo— no tiene por qué enterarse de cuál es: le pide siempre lo
mismo a un módulo que cumple esta interfaz.

    NOMBRE                      cómo se llama, para mostrarlo
    disponible()                (bool, motivo) — si falta configuración lo DICE,
                                no falla en silencio
    lotes()                     los lotes con sus cabezas
    ingredientes()              los ingredientes con %MS y precio
    consumos(desde, hasta)      kg descargados por lote y por ingrediente

Cada implementación traduce lo suyo a estas claves comunes. Un lote es:

    lote              nombre, y CLAVE del mapeo (es lo que ve el tambo)
    id                id interno del proveedor
    cabezas           cabezas declaradas en el lote
    kg_ms_cabeza      kg de materia seca por cabeza y por día
    categoria         nombre de la categoría ("Ordeñe", "Secas", "Preparto"…)
    indice_ordene     a qué NÚMERO de grupo del sistema de ordeñe dice el
                      proveedor que corresponde este lote. Vale oro: es el mapeo
                      declarado por el propio tambo, no una adivinanza nuestra.
                      None si no lo declararon.
    activo            si el lote se está alimentando de verdad (ver `haasten.py`)
    pct_alimentacion  fracción de la ración que se entrega realmente

Un ingrediente es `{nombre, ms_pct, precio, stock}`, con `precio = None` cuando
el proveedor no lo tiene cargado — NUNCA 0, que se propagaría como un costo real.
"""

# Proveedor de cada tambo. Se puede declarar acá (código) o desde la página
# "⚙ Configuración" (ver `configuracion_tambo.py`) — lo de la UI manda si
# está cargado, esto queda como respaldo para lo que todavía no se configuró.
#
# San José usa MixerOne, no Haasten — declararlo acá evita que caiga al
# defecto y termine comparando sus grupos contra el mixer de OTRO tambo
# (ver proveedores/mixerone.py: la integración real queda para más adelante,
# por ahora el módulo solo informa "no conectado" en vez de dar datos falsos).
POR_TAMBO: dict[str, str] = {"san_jose": "mixerone"}
POR_DEFECTO = "haasten"

_MODULOS = {"haasten": "haasten", "mixerone": "mixerone", "delpro": "delpro"}


def de(tambo: str):
    """El módulo proveedor que le toca a un tambo."""
    import configuracion_tambo
    nombre = configuracion_tambo.config_de(tambo).get("sistema_alimentacion") \
        or POR_TAMBO.get(tambo, POR_DEFECTO)
    modulo = _MODULOS.get(nombre)
    if modulo is None:
        raise ValueError(f"Proveedor de alimentación desconocido: {nombre!r}")
    import importlib
    return importlib.import_module(f".{modulo}", __name__)
