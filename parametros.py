# -*- coding: utf-8 -*-
"""Parámetros reproductivos del tambo.

Son los valores que gobiernan todos los cálculos reproductivos: cuántos días
dura la gestación, a los cuántos días se seca una vaca, cuál es el período de
espera voluntario antes del primer servicio, cuánto dura un ciclo de celo.

NO SE INVENTAN NI SE CARGAN A MANO: DelPro los guarda en la tabla
`ReproductionSetting`, una fila por parámetro, y se leen en vivo. Así, si el
tambo cambia un valor en DelPro, los cálculos de la aplicación lo toman solos.

Esto corrigió tres supuestos que estaban hardcodeados:

    Días de gestación   280  →  ya estaba bien
    Ciclo de celo        21  →  ya estaba bien
    Secado               50  →  se venía usando 60 (el DEFECTO de DelPro,
                                no el valor que configuró este tambo)
    Espera voluntaria    53  →  se venía usando 50, estimado a ojo

El de secado es el que más pesa: mueve la fecha de secado de cada vaca diez
días, y con eso el reparto mensual de secados y la curva de vacas en ordeñe.

`ReproductionSetting.Parameter` es un código numérico; el mapa de códigos a
nombres se dedujo comparando la tabla contra la pantalla de DelPro, que las
lista en el mismo orden (`OrderIndex`).
"""
import threading
import time

# Código de `ReproductionSetting.Parameter` → (clave interna, etiqueta).
PARAMETROS = {
    19: ("periodo_ternero", "Duración del período de ternero"),
    0: ("novillas_primera_ia", "Novillas de primera inseminación"),
    1: ("vacas_primer_celo", "Vacas primer celo"),
    9: ("ciclo_celo", "Duración del ciclo de celo"),
    2: ("espera_voluntaria", "Vacas primera inseminación (PEV)"),
    3: ("diag_gestacion_1", "Diagnóstico de gestación 1"),
    4: ("diag_gestacion_2", "Diagnóstico de gestación 2"),
    5: ("diag_gestacion_3", "Diagnóstico de gestación 3"),
    6: ("diag_gestacion_4", "Diagnóstico de gestación 4"),
    18: ("diag_gestacion_toros", "Diagnóstico de gestación en grupo de toros"),
    10: ("dias_gestacion", "Días de gestación"),
    7: ("dias_secado", "Secado"),
    8: ("racion_preparto", "Ración extra preparto"),
    11: ("aten_antes_celo", "Atención antes del celo esperado"),
    12: ("aten_despues_celo", "Atención después del celo esperado"),
    13: ("aviso_control_gestacion", "Aviso previo a control de gestación"),
    14: ("aten_antes_secado", "Atención antes del secado"),
    15: ("aten_antes_preparto", "Atención antes de la alimentación preparto"),
    16: ("aten_antes_parto", "Atención antes del parto esperado"),
    17: ("aten_extra", "Atención adicional"),
}

# Qué usa cada cálculo de la aplicación. Se muestra en la página para que se
# entienda por qué cambiar un parámetro mueve un número.
USADO_EN = {
    "dias_gestacion": ["Proyección de Rebaños", "Partos y Secados", "Indicadores de Preñez"],
    "dias_secado": ["Proyección de Rebaños", "Partos y Secados", "Análisis Reproductivo"],
    "espera_voluntaria": ["Análisis Reproductivo", "Tasa de preñez por ciclo"],
    "ciclo_celo": ["Análisis Reproductivo", "Tasa de preñez por ciclo"],
}

# Valores de respaldo, por si la consulta falla. Son los que tiene configurados
# La Ponderosa hoy, no los defectos de DelPro.
RESPALDO = {
    "dias_gestacion": 280, "dias_secado": 50,
    "espera_voluntaria": 53, "ciclo_celo": 21,
}

SQL = """
    SELECT Parameter, ValueInDays, DefaultValue, MinValue, MaxValue, Active, OrderIndex
    FROM ReproductionSetting
    WHERE GCRecord IS NULL
    ORDER BY OrderIndex
"""

# Caché en memoria: son ~20 filas que casi nunca cambian, pero las consultan
# todos los módulos de cálculo. Sin esto habría una consulta por cada uso.
_TTL_S = 1800
_cache: dict = {}
_lock = threading.Lock()


def _leer(tambo: str) -> dict:
    """{clave: valor_en_dias} del tambo. Cachea; si falla, usa el respaldo."""
    with _lock:
        guardado = _cache.get(tambo)
        if guardado and time.time() - guardado[0] < _TTL_S:
            return guardado[1]
    valores = dict(RESPALDO)
    try:
        import db
        data = db.run_query(SQL, tambo=tambo, max_rows=60)
        idx = {c: i for i, c in enumerate(data["columns"])}
        for fila in data["rows"]:
            codigo = fila[idx["Parameter"]]
            if codigo in PARAMETROS:
                valores[PARAMETROS[codigo][0]] = int(fila[idx["ValueInDays"]] or 0)
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        _cache[tambo] = (time.time(), valores)
    return valores


def valor(clave: str, tambo: str, defecto=None):
    """Valor en días de un parámetro. Es la puerta de entrada para los módulos
    de cálculo: `parametros.valor("dias_secado", tambo)`."""
    v = _leer(tambo).get(clave)
    return v if v is not None else (defecto if defecto is not None else RESPALDO.get(clave))


def listado(data=None, tambo: str = None) -> list:
    """Tabla completa para mostrar en la página, con etiquetas y rangos.

    `data`: resultado crudo de `db.run_query(SQL)`. Si no viene, se lee.
    """
    if data is None:
        import db
        data = db.run_query(SQL, tambo=tambo, max_rows=60)
    idx = {c: i for i, c in enumerate(data["columns"])}
    filas = []
    for f in data["rows"]:
        codigo = f[idx["Parameter"]]
        clave, etiqueta = PARAMETROS.get(codigo, (f"param_{codigo}", f"Parámetro {codigo}"))
        v, defecto = f[idx["ValueInDays"]], f[idx["DefaultValue"]]
        filas.append({
            "clave": clave, "parametro": etiqueta,
            "valor": v, "defecto": defecto,
            "minimo": f[idx["MinValue"]], "maximo": f[idx["MaxValue"]],
            "activo": bool(f[idx["Active"]]),
            # Marcar los que el tambo cambió respecto del defecto de DelPro:
            # son las decisiones propias del tambo, y las que hay que mirar.
            "modificado": v is not None and defecto is not None and v != defecto,
            "usado_en": USADO_EN.get(clave, []),
        })
    return filas
