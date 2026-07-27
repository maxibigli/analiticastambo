# -*- coding: utf-8 -*-
"""Tasa de preñez: el embudo reproductivo por ciclo de 21 días y por mes.

De todas las vacas que estaban en condiciones de recibir servicio, ¿a cuántas
se les detectó celo? De esas, ¿a cuántas se inseminó? Y de esas, ¿cuántas
quedaron preñadas? Cada escalón que se pierde tiene una causa distinta y se
arregla de forma distinta:

    aptas → con celo      detección de celo (observación, podómetros, collares)
    con celo → inseminada  disponibilidad de inseminador, semen, decisión
    inseminada → preñada   calidad del semen, momento del servicio, sanidad

FÓRMULAS, verificadas contra el informe de DelPro (ciclo 1: aptas 1.112, con
celo 563, inseminadas 545, preñadas 229, abortos 33):

    % Celo         = con celo / aptas            563/1112 = 51%  ✓
    % Inseminación = inseminadas / aptas         545/1112 = 49%  ✓
    % Preñadas     = preñadas / aptas            229/1112 = 21%  ✓
    % Concepción   = preñadas / inseminadas      229/545  = 42%  ✓
    % Aborto       = abortos / preñadas          33/229   = 14%  ✓

APTAS es la parte que hubo que reconstruir, porque DDM no guarda una lista de
elegibles por fecha. Una vaca es apta en un ciclo si, al TERMINAR el ciclo:
ya parió, pasó el período de espera voluntario, sigue en el rodeo y no estaba
preñada al empezar el ciclo. Se probó también "apta al inicio" y da 940 contra
las 1.112 del informe; al fin da 1.112 exacto, o sea que DelPro cuenta las
vacas que cruzan la espera voluntaria durante el ciclo.

El período de espera NO está hardcodeado: sale de `ReproductionSetting` vía
`parametros.py` (este tambo lo tiene en 53 días, no en los 50 por defecto).
Lo mismo la duración del ciclo de celo (21 días).

LO QUE NO CIERRA:

  * Con celo e inseminadas quedan ~4 puntos por debajo del informe (522 vs
    563, 494 vs 545). Es el faltante de eventos que ya apareció en todo el
    análisis reproductivo de esta base. El % de concepción, que es el
    indicador que de verdad importa, queda a 1-3 puntos (40,7 vs 42; 41,9 vs
    45), y las tendencias entre ciclos son fieles.

  * % ABORTO NO CIERRA y se muestra marcado como tal. Da ~38% donde el
    informe da 14%: se cuentan unos 77 abortos donde DelPro cuenta 33. Se
    probó acotar el aborto a la ventana de gestación de la preñez lograda en
    el ciclo (que es la definición que mejor explica la forma de la serie de
    DelPro, con ceros en los ciclos recientes) y casi no movió el número. No
    se encontró qué criterio adicional aplica DelPro. Mejor mostrarlo con la
    advertencia que dar por bueno un número que se sabe inflado.
"""
import datetime

import parametros
import rebano

TIPO_VACA = "vaca"
TIPO_NOVILLA = "novilla"
TIPO_AMBAS = "ambas"
TIPOS = (TIPO_VACA, TIPO_NOVILLA, TIPO_AMBAS)
ETIQUETA_TIPO = {TIPO_VACA: "Vacas", TIPO_NOVILLA: "Novillas",
                 TIPO_AMBAS: "Vacas y novillas"}

DIM_CICLO = "ciclo"
DIM_MES = "mes"

RANGO_MAX_DIAS = 800

# Tope de días después de la concepción dentro del cual un aborto pertenece a
# esa preñez. Pasado eso, la gestación llegó a término.
GESTACION_MAX = 290


def ventanas(desde: str, hasta: str, dimension: str, ciclo_dias: int) -> list:
    """Los períodos en que se parte el rango: ciclos de N días o meses."""
    d0 = datetime.date.fromisoformat(desde)
    d1 = datetime.date.fromisoformat(hasta)
    out = []
    if dimension == DIM_CICLO:
        i, actual = 1, d0
        while actual <= d1 and i <= 60:
            fin = min(actual + datetime.timedelta(days=ciclo_dias - 1), d1)
            out.append({"n": i, "desde": actual.isoformat(), "hasta": fin.isoformat()})
            actual = fin + datetime.timedelta(days=1)
            i += 1
    else:
        i, actual = 1, d0
        while actual <= d1 and i <= 40:
            if actual.month == 12:
                sig = datetime.date(actual.year + 1, 1, 1)
            else:
                sig = datetime.date(actual.year, actual.month + 1, 1)
            fin = min(sig - datetime.timedelta(days=1), d1)
            out.append({"n": i, "desde": actual.isoformat(), "hasta": fin.isoformat()})
            actual = fin + datetime.timedelta(days=1)
            i += 1
    return out


def _cond_tipo(tipo: str, alias: str = "ae") -> str:
    """Vaca = ya parió (lactancia >= 1). Novilla = todavía no."""
    if tipo == TIPO_VACA:
        return f"{alias}.LactationNumber >= 1"
    if tipo == TIPO_NOVILLA:
        return f"{alias}.LactationNumber = 0"
    return "1 = 1"


def sql_embudo(ventanas_lista: list, tipo: str, pev: int,
               edad_novilla: int = 447, herd=None) -> str:
    """El embudo de TODAS las ventanas, en una sola consulta.

    `pev`: período de espera voluntario en días (parámetro del tambo).
    `edad_novilla`: edad a la que una novilla entra a servicio (parámetro
    "Novillas de primera inseminación").

    POR QUÉ TODAS JUNTAS. La primera versión hacía una consulta por ventana y
    tardaba 5,4 segundos cada una: con 18 ciclos más 13 meses eran 31
    consultas y unos 166 segundos de espera. El costo no estaba en contar los
    eventos de la ventana sino en armar el CTE `prenez`, que recorre los 25.789
    chequeos de preñez buscando para cada uno el parto que cierra esa preñez —
    y eso se recalculaba entero 31 veces para devolver siempre lo mismo.

    Acá los CTE caros (`prenez`, `partos`) se arman UNA vez y las ventanas
    entran como una tabla contra la que se cruzan.
    """
    filas = ", ".join(
        "(%d, '%s', '%s')" % (v["n"], v["desde"], v["hasta"]) for v in ventanas_lista)
    if not filas:
        filas = "(0, '1900-01-01', '1900-01-01')"

    # Elegibilidad al FIN de la ventana (ver encabezado del módulo).
    ref = "DATEADD(day, 1, v.hasta)"
    # No estaba preñada al empezar: si ya lo estaba, no era candidata.
    libre = ("NOT EXISTS (SELECT 1 FROM prenez pz WHERE pz.animal = {col}"
             " AND pz.concep < v.desde AND (pz.parto IS NULL OR pz.parto > v.desde))")

    # Vaca: ya parió y pasó el período de espera desde el parto.
    aptas_vaca = f"""
        SELECT v.n, b.OID AS animal
        FROM ventanas v
        JOIN BasicAnimal b ON b.GCRecord IS NULL AND b.Number > 0
                          AND (b.ExitDate IS NULL OR b.ExitDate >= v.desde)
        CROSS APPLY (SELECT MAX(pa.fecha) AS f FROM partos pa
                     WHERE pa.animal = b.OID AND pa.fecha < {ref}) up
        WHERE up.f IS NOT NULL
          AND DATEDIFF(day, up.f, {ref}) >= {pev}
          AND {libre.format(col='b.OID')}
          AND {rebano.filtro('b', herd)}
    """
    # Novilla: no parió nunca, así que es apta por EDAD.
    aptas_novilla = f"""
        SELECT v.n, b.OID AS animal
        FROM ventanas v
        JOIN BasicAnimal b ON b.GCRecord IS NULL AND b.Number > 0
                          AND (b.ExitDate IS NULL OR b.ExitDate >= v.desde)
        JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
                                     AND r.LactationNumber = 0
        WHERE b.BirthDate IS NOT NULL
          AND DATEDIFF(day, b.BirthDate, {ref}) >= {edad_novilla}
          AND {libre.format(col='b.OID')}
          AND {rebano.filtro('b', herd)}
    """
    if tipo == TIPO_VACA:
        aptas = aptas_vaca
    elif tipo == TIPO_NOVILLA:
        aptas = aptas_novilla
    else:
        aptas = aptas_vaca + "\n            UNION\n" + aptas_novilla

    # NO acotar por fecha los CTE `partos` y `prenez`. Se probó recortarlos al
    # período analizado para que no recorran toda la historia: no mejoró el
    # tiempo y CAMBIÓ los números (aptas 365 contra 361 en el primer ciclo).
    # La razón es que esta base tiene preñeces viejas que siguen figurando
    # abiertas; al recortarlas, esas vacas dejan de detectarse como preñadas y
    # pasan a contarse como aptas.
    return f"""
        WITH ventanas(n, desde, hasta) AS (
            SELECT n, CAST(d AS date), CAST(h AS date)
            FROM (VALUES {filas}) AS t(n, d, h)
        ), partos AS (
            SELECT ae.BasicAnimal AS animal, ae.DateAndTime AS fecha
            FROM EventCalving c
            JOIN AbstractAnimalEvent ae ON ae.OID = c.OID AND ae.GCRecord IS NULL
        ), prenez AS (
            -- Cada preñez, desde la concepción hasta el parto que la cierra.
            -- Es el CTE caro: se arma una sola vez para todas las ventanas.
            SELECT DISTINCT pc.BasicAnimal AS animal, ins.DateAndTime AS concep,
                   (SELECT MIN(pa2.fecha) FROM partos pa2
                    WHERE pa2.animal = pc.BasicAnimal
                      AND pa2.fecha > ins.DateAndTime) AS parto
            FROM EventPregCheck p
            JOIN AbstractAnimalEvent pc ON pc.OID = p.OID AND pc.GCRecord IS NULL
            JOIN AbstractAnimalEvent ins ON ins.OID = p.EffectiveInsemination
                                        AND ins.GCRecord IS NULL
            WHERE p.Result = 1
        ), aptas AS ({aptas})
        SELECT v.n,
          (SELECT COUNT(*) FROM aptas a WHERE a.n = v.n) AS aptas,
          (SELECT COUNT(DISTINCT ae.BasicAnimal) FROM EventHeat h
            JOIN AbstractAnimalEvent ae ON ae.OID = h.OID AND ae.GCRecord IS NULL
           WHERE ae.DateAndTime >= v.desde AND ae.DateAndTime < DATEADD(day, 1, v.hasta)
             AND EXISTS (SELECT 1 FROM aptas a WHERE a.n = v.n
                         AND a.animal = ae.BasicAnimal)) AS con_celo,
          (SELECT COUNT(DISTINCT ae.BasicAnimal) FROM EventInsemination i
            JOIN AbstractAnimalEvent ae ON ae.OID = i.OID AND ae.GCRecord IS NULL
           WHERE ae.DateAndTime >= v.desde AND ae.DateAndTime < DATEADD(day, 1, v.hasta)
             AND EXISTS (SELECT 1 FROM aptas a WHERE a.n = v.n
                         AND a.animal = ae.BasicAnimal)) AS inseminadas,
          (SELECT COUNT(DISTINCT pz.animal) FROM prenez pz
           WHERE pz.concep >= v.desde AND pz.concep < DATEADD(day, 1, v.hasta)
             AND EXISTS (SELECT 1 FROM aptas a WHERE a.n = v.n
                         AND a.animal = pz.animal)) AS prenadas,
          -- Abortos DE las preñeces logradas en la ventana. El aborto tiene que
          -- caer después de esa concepción y antes de que la gestación llegara
          -- a término; sin esa ventana se cuentan abortos de otras preñeces del
          -- mismo animal y el porcentaje se va al triple.
          (SELECT COUNT(DISTINCT ae.BasicAnimal) FROM EventAbortion ab
            JOIN AbstractAnimalEvent ae ON ae.OID = ab.OID AND ae.GCRecord IS NULL
           WHERE EXISTS (SELECT 1 FROM prenez pz
                         WHERE pz.animal = ae.BasicAnimal
                           AND pz.concep >= v.desde
                           AND pz.concep < DATEADD(day, 1, v.hasta)
                           AND ae.DateAndTime > pz.concep
                           AND ae.DateAndTime <= DATEADD(day, {GESTACION_MAX}, pz.concep))
          ) AS abortos
        FROM ventanas v
        ORDER BY v.n
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 30)
    """


def analizar(data, ventanas_lista: list, tipo: str, ciclo_dias: int, pev: int) -> dict:
    """Arma las filas del embudo con sus porcentajes.

    `data`: el resultado ÚNICO de `sql_embudo`, con una fila por ventana
    identificada por su número (ver por qué en el docstring de esa función).
    """
    por_n = {}
    for fila in (data.get("rows") or []):
        d = dict(zip(data["columns"], fila))
        por_n[int(d["n"])] = d

    filas = []
    for v in ventanas_lista:
        f = por_n.get(v["n"], {})
        aptas = int(f.get("aptas") or 0)
        celo = int(f.get("con_celo") or 0)
        insem = int(f.get("inseminadas") or 0)
        pren = int(f.get("prenadas") or 0)
        ab = int(f.get("abortos") or 0)
        pct = lambda a, b: round(100.0 * a / b, 1) if b else None
        filas.append({
            "n": v["n"], "desde": v["desde"], "hasta": v["hasta"],
            "aptas": aptas, "con_celo": celo, "pct_celo": pct(celo, aptas),
            "inseminadas": insem, "pct_insem": pct(insem, aptas),
            "prenadas": pren, "pct_prenadas": pct(pren, aptas),
            "abortos": ab, "pct_concepcion": pct(pren, insem),
            "pct_aborto": pct(ab, pren),
        })

    tot = {k: sum(f[k] for f in filas)
           for k in ("aptas", "con_celo", "inseminadas", "prenadas", "abortos")}
    pct = lambda a, b: round(100.0 * a / b, 1) if b else None
    return {
        "filas": filas,
        "totales": {
            **tot,
            "pct_celo": pct(tot["con_celo"], tot["aptas"]),
            "pct_insem": pct(tot["inseminadas"], tot["aptas"]),
            "pct_prenadas": pct(tot["prenadas"], tot["aptas"]),
            "pct_concepcion": pct(tot["prenadas"], tot["inseminadas"]),
            "pct_aborto": pct(tot["abortos"], tot["prenadas"]),
        },
        "tipo": tipo, "tipo_label": ETIQUETA_TIPO.get(tipo, tipo),
        "ciclo_dias": ciclo_dias, "espera_voluntaria": pev,
    }
