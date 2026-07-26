# -*- coding: utf-8 -*-
"""Indicadores de preñez: cuándo quedan preñados los animales.

Réplica del "Gráfico de preñez" de DelPro. Cuenta las CONCEPCIONES ocurridas
dentro de un rango de fechas y las reparte de dos maneras:

  * por DEO — a cuántos días de ordeñe (días desde el parto) quedó preñada
    cada vaca. Dice si el rodeo se preña temprano o si se estira.
  * por MES — en qué mes se produjo cada concepción. Dice si hubo meses
    flojos de servicio.

En las dos vistas se separa primera lactancia (L1) del resto (L2+), porque las
vaquillonas de primer parto suelen preñarse distinto que las vacas adultas, y
se acompaña con la curva de porcentaje ACUMULADO: cuánto del total de preñeces
del período ya se había logrado hasta ese tramo.

CÓMO SE IDENTIFICA UNA CONCEPCIÓN
---------------------------------
`EventPregCheck` tiene una columna `EffectiveInsemination` que apunta a la
inseminación que efectivamente quedó. Un chequeo con `Result = 1` más esa
inseminación dan la fecha de concepción. Se deduplica por (animal,
inseminación) porque una misma preñez suele tener varios chequeos y si no se
contaría dos veces.

Verificado contra el informe de DelPro (26/07/2025 a 26/07/2026, todos los
rebaños): 1.382 concepciones, 587 de L1 y 795 de L2+ — idéntico.

LIMITACIÓN CONOCIDA del corte por DEO: los días de ordeñe salen del parto que
abrió esa lactancia. En ~2,7% de los casos ese parto no está registrado y el
animal cae en el tramo más alto (306+) en vez del que le corresponde. Los
totales no se ven afectados, solo el reparto entre tramos.
"""
import rebano

# Tipos de animal que se pueden analizar.
TIPO_VACAS = "vacas"
TIPO_VAQUILLONAS = "vaquillonas"
TIPO_AMBAS = "ambas"
TIPOS = (TIPO_VACAS, TIPO_VAQUILLONAS, TIPO_AMBAS)

ETIQUETA_TIPO = {
    TIPO_VACAS: "Vacas",
    TIPO_VAQUILLONAS: "Vaquillonas",
    TIPO_AMBAS: "Vacas y vaquillonas",
}

# Tramos de días en ordeñe, los mismos del informe de DelPro.
TRAMOS_DEO = [
    ("0-100", 0, 100), ("101-130", 101, 130), ("131-150", 131, 150),
    ("151-200", 151, 200), ("201-305", 201, 305), ("306+", 306, 99999),
]

# Rango máximo, en días. Es una consulta de eventos, no de ordeños: aguanta
# bastante más que las de flujo.
RANGO_MAX_DIAS = 800


def _filtro_tipo(tipo: str) -> str:
    """Condición sobre la lactancia según el tipo de animal elegido.

    La lactancia del chequeo de preñez es 0 en una vaquillona que todavía no
    parió, y 1 o más en una vaca.
    """
    if tipo == TIPO_VACAS:
        return "c.lact >= 1"
    if tipo == TIPO_VAQUILLONAS:
        return "c.lact = 0"
    return "1 = 1"


def _caso_deo(columna: str = "deo") -> str:
    partes = " ".join(
        f"WHEN {columna} <= {hasta} THEN '{nombre}'"
        for nombre, _desde, hasta in TRAMOS_DEO[:-1])
    return f"CASE WHEN {columna} IS NULL THEN 'sin dato' {partes} ELSE '{TRAMOS_DEO[-1][0]}' END"


def sql_concepciones(desde: str, hasta: str, tipo: str, dimension: str, herd=None) -> str:
    """Concepciones del rango, agrupadas por tramo de DEO o por mes.

    `dimension`: "deo" o "mes".
    """
    grupo = _caso_deo() if dimension == "deo" else "FORMAT(f_conc, 'yyyy-MM')"
    return f"""
        WITH conc AS (
            SELECT DISTINCT pc.BasicAnimal AS animal,
                   p.EffectiveInsemination AS insem,
                   ins.DateAndTime AS f_conc,
                   pc.LactationNumber AS lact
            FROM EventPregCheck p
            JOIN AbstractAnimalEvent pc ON pc.OID = p.OID AND pc.GCRecord IS NULL
            JOIN AbstractAnimalEvent ins ON ins.OID = p.EffectiveInsemination
                                        AND ins.GCRecord IS NULL
            WHERE p.Result = 1
              AND ins.DateAndTime >= '{desde}'
              AND ins.DateAndTime < DATEADD(day, 1, '{hasta}')
              AND {rebano.filtro_por_animal('pc.BasicAnimal', herd)}
        ), det AS (
            SELECT c.animal, c.f_conc, c.lact,
                   -- Días de ordeñe a la concepción. Se busca el parto que
                   -- abrió ESA lactancia; si no está registrado se cae al
                   -- parto anterior más reciente (ver limitación en el
                   -- encabezado del módulo).
                   DATEDIFF(day, COALESCE(
                       (SELECT MAX(c2.DateAndTime) FROM EventCalving cc
                        JOIN AbstractAnimalEvent c2 ON c2.OID = cc.OID AND c2.GCRecord IS NULL
                        WHERE c2.BasicAnimal = c.animal AND c2.DateAndTime <= c.f_conc
                          AND c2.LactationNumber = c.lact),
                       (SELECT MAX(c3.DateAndTime) FROM EventCalving cc3
                        JOIN AbstractAnimalEvent c3 ON c3.OID = cc3.OID AND c3.GCRecord IS NULL
                        WHERE c3.BasicAnimal = c.animal AND c3.DateAndTime <= c.f_conc)
                   ), c.f_conc) AS deo
            FROM conc c
            WHERE {_filtro_tipo(tipo)}
        )
        SELECT {grupo} AS tramo,
               SUM(CASE WHEN lact = 1 THEN 1 ELSE 0 END) AS l1,
               SUM(CASE WHEN lact > 1 THEN 1 ELSE 0 END) AS l2,
               SUM(CASE WHEN lact = 0 THEN 1 ELSE 0 END) AS vaquillonas
        FROM det
        GROUP BY {grupo}
        ORDER BY tramo
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def _orden_deo(nombre: str) -> int:
    for i, (etiqueta, _d, _h) in enumerate(TRAMOS_DEO):
        if etiqueta == nombre:
            return i
    return len(TRAMOS_DEO)  # 'sin dato' al final


def analizar(data, dimension: str, tipo: str) -> dict:
    """Arma las filas con porcentajes y la curva acumulada.

    Los porcentajes de cada serie son sobre el TOTAL de esa serie en el
    período (así la columna suma 100), y el acumulado es sobre el total
    general — igual que en el informe de DelPro.
    """
    filas = [dict(zip(data["columns"], f)) for f in (data.get("rows") or [])]
    if dimension == "deo":
        filas.sort(key=lambda f: _orden_deo(f["tramo"]))
    else:
        filas.sort(key=lambda f: f["tramo"] or "")

    tot_l1 = sum(int(f["l1"] or 0) for f in filas)
    tot_l2 = sum(int(f["l2"] or 0) for f in filas)
    tot_vq = sum(int(f["vaquillonas"] or 0) for f in filas)
    total = tot_l1 + tot_l2 + tot_vq

    def pct(parte, base):
        return round(100.0 * parte / base, 1) if base else None

    salida, acumulado = [], 0.0
    for f in filas:
        l1, l2, vq = int(f["l1"] or 0), int(f["l2"] or 0), int(f["vaquillonas"] or 0)
        del_total = l1 + l2 + vq
        p_total = pct(del_total, total)
        acumulado += (p_total or 0)
        salida.append({
            "tramo": f["tramo"],
            "l1": l1, "pct_l1": pct(l1, tot_l1),
            "l2": l2, "pct_l2": pct(l2, tot_l2),
            "vaquillonas": vq, "pct_vaquillonas": pct(vq, tot_vq),
            "total": del_total, "pct_total": p_total,
            "acumulado": round(min(acumulado, 100.0), 1),
        })

    return {
        "filas": salida,
        "dimension": dimension,
        "tipo": tipo,
        "tipo_label": ETIQUETA_TIPO.get(tipo, tipo),
        "totales": {
            "l1": tot_l1, "pct_l1": pct(tot_l1, total),
            "l2": tot_l2, "pct_l2": pct(tot_l2, total),
            "vaquillonas": tot_vq, "pct_vaquillonas": pct(tot_vq, total),
            "total": total,
        },
        # Qué series tiene sentido dibujar según el tipo elegido.
        "series": ([] if tipo == TIPO_VAQUILLONAS else ["l1", "l2"]) +
                  ([] if tipo == TIPO_VACAS else ["vaquillonas"]),
    }
