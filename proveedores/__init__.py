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

# Proveedor de cada tambo. Mientras haya uno solo alcanza con el defecto; el día
# que un tambo use otro sistema se agrega acá su id y listo.
POR_TAMBO: dict[str, str] = {}
POR_DEFECTO = "haasten"


def de(tambo: str):
    """El módulo proveedor que le toca a un tambo."""
    nombre = POR_TAMBO.get(tambo, POR_DEFECTO)
    if nombre == "haasten":
        from . import haasten
        return haasten
    raise ValueError(f"Proveedor de alimentación desconocido: {nombre!r}")
