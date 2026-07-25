# -*- coding: utf-8 -*-
"""Simulador de ordeño en la rotativa.

Genera una "vuelta en vivo" sintética a partir de las vacas REALES del último
ordeño cacheado: cada ~6 segundos sube una vaca al siguiente puesto, da una
vuelta completa (8 min) mientras su producción crece hasta su producción real,
y baja. NO toca la base de datos — es solo una transformación en memoria para
probar la pantalla de la rotativa (o hacer demos) cuando no hay ordeño en curso.

Determinista respecto del reloj: no guarda más estado que el instante de inicio
de la simulación por tambo (reiniciar() lo resetea).
"""
import time

PUESTOS = 80
VUELTA_S = 8 * 60                  # una vuelta completa de la plataforma
ENTRADA_S = VUELTA_S / PUESTOS     # sube una vaca cada 6 s

# Mismas columnas que ORDENO_ALARMAS_SQL (deben coincidir con _ALARMA_COLS de app.py).
ALARMA_COLS = ["real_kg", "esperada_kg", "a_baja", "a_cond", "a_sangre", "a_retirada"]

_inicio = {}  # tambo -> epoch en que arrancó la simulación


def reiniciar(tambo: str) -> None:
    """Arranca la simulación de cero (plataforma vacía que se va llenando)."""
    _inicio[tambo] = time.time()


def _total_kg(fila, idx):
    """Producción final de la vaca: la real de su último ordeño, o un valor
    determinista razonable (18-35 kg) si no tiene dato."""
    kg = fila[idx.get("produccion_kg")] if "produccion_kg" in idx else None
    if isinstance(kg, (int, float)) and kg > 3:
        return float(kg)
    rp = fila[idx.get("rp")] if "rp" in idx else 0
    base = rp if isinstance(rp, int) else 0
    return 18.0 + (base * 7) % 18


def _es_lenta(fila, idx):
    """~8% de vacas 'lentas' (deterministas por RP) para ver la alarma de baja
    producción en acción."""
    rp = fila[idx.get("rp")] if "rp" in idx else 0
    return isinstance(rp, int) and rp % 12 == 5


def simular(tambo: str, columns, rows):
    """Devuelve (columns_sin_momento, filas_en_plataforma, alarmas_por_fila).

    Cada fila es una vaca arriba de la plataforma en este instante, con su
    posición y producción simuladas. alarmas_por_fila trae los valores de
    ALARMA_COLS alineados fila a fila (reemplazan a la consulta de alarmas).
    """
    if tambo not in _inicio:
        reiniciar(tambo)
    e = time.time() - _inicio[tambo]

    cols = list(columns)
    base = [list(r) for r in rows]
    if cols and cols[0] == "momento_ordeno":
        cols = cols[1:]
        base = [r[1:] for r in base]
    idx = {c: i for i, c in enumerate(cols)}
    if not base:
        return cols, [], []

    # Orden de subida realista: por grupo y RP (los grupos entran juntos).
    # Los valores vienen con tipos mezclados (int/str/None): se normalizan a str.
    # Las vacas sin grupo o sin permiso de ordeño (hospital, comodines) van al
    # final para que el arranque de la simulación muestre vacas normales.
    def _clave_subida(r):
        rp = r[idx["rp"]]
        grupo = str(r[idx["grupo"]] or "")
        rara = (not grupo or not isinstance(rp, (int, float)) or rp == 0
                or ("permiso" in idx and r[idx["permiso"]] == "NO ordeñar"))
        return (1 if rara else 0, grupo,
                rp if isinstance(rp, (int, float)) else 0)
    base.sort(key=_clave_subida)

    # Vaca j sube en j*ENTRADA_S y ocupa el puesto (j % 80)+1 durante una vuelta.
    j_max = int(e / ENTRADA_S)
    j_min = max(0, int((e - VUELTA_S) / ENTRADA_S) + 1)

    filas, alarmas = [], []
    for j in range(j_min, j_max + 1):
        vaca = list(base[j % len(base)])
        p = (e - j * ENTRADA_S) / VUELTA_S      # progreso de la vuelta [0, 1)
        total = _total_kg(vaca, idx)
        lenta = _es_lenta(vaca, idx)
        avance = 1 - (1 - p) ** 1.7             # flujo alto al inicio, luego afloja
        real = round(total * (0.6 if lenta else 1.0) * avance, 1)

        vaca[idx["posicion"]] = (j % PUESTOS) + 1
        if "produccion_kg" in idx:
            vaca[idx["produccion_kg"]] = real
        filas.append(vaca)

        cond = vaca[idx["conductividad"]] if "conductividad" in idx else None
        alarmas.append([
            real,                                # real_kg
            round(total, 1),                     # esperada_kg
            1 if (lenta and p > 0.6) else 0,     # a_baja (recién sobre el final)
            1 if (cond or 0) > 115 else 0,       # a_cond
            1 if j % 97 == 13 else 0,            # a_sangre (rarísima)
            0,                                   # a_retirada
        ])

    orden = sorted(range(len(filas)), key=lambda i: filas[i][idx["posicion"]])
    return cols, [filas[i] for i in orden], [alarmas[i] for i in orden]
