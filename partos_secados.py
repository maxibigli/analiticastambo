# -*- coding: utf-8 -*-
"""Partos y secados esperados, animal por animal, y la proyección de vacas en
ordeñe que sale de ellos.

Es la contraparte detallada de `proyeccion.py`: aquella arma la curva mensual
del rodeo, esta muestra QUÉ vaca la compone. Sirve para dos cosas distintas:

  * operativa — la lista de qué vacas hay que secar y cuáles van a parir,
    ordenable y filtrable, para llevarla al corral;
  * control — poder contrastar el modelo contra el informe de DelPro vaca por
    vaca y no solo en el total.

LA ECUACIÓN es la misma que ya quedó verificada en `proyeccion.py`, y el
informe de DelPro la confirma en su tabla "Proyecciones VO":

    VO[m] = VO[m-1] + partos[m] - secados[m] - descarte[m]

    3.433 + 119 - 39 - 73 = 3.440   ✓ (DelPro: 3.440)
    3.440 +  86 - 71 - 73 = 3.382   ✓
    ...y así los nueve meses del informe.

El "Descarte Estimado" de DelPro es un valor FIJO por mes (73 en ese informe),
no un cálculo: es una tasa de reposición que el tambo asume. Acá se calcula
desde las bajas reales de los últimos doce meses, y se puede pisar a mano.

DATOS VENCIDOS. En el informe de DelPro aparecen vacas con DEO de 1.421 días y
"Días al Secado" de -1.122, con fechas esperadas de 2023. Son preñeces que
quedaron abiertas en la carga: el animal figura preñado desde hace años. Acá
esas filas se marcan con `vencido = True` y quedan FUERA de los totales
mensuales —no tiene sentido proyectar un parto que debió ocurrir hace dos
años— pero se siguen listando en la tabla, porque son justamente las que hay
que ir a corregir en DelPro.
"""
import datetime

import rebano
from proyeccion import GESTACION_DIAS, PERIODO_SECO_DIAS, PRENEZ_MAX_DIAS

# Categorías del desplegable de DelPro.
CATEGORIAS = {
    "todas": "Todas",
    "l0": "L0 (vaquillonas)",
    "l1": "L1",
    "l2+": "L2+",
    "l1+": "L1+ (todas las vacas)",
}


def _filtro_categoria(cat: str) -> str:
    """Condición sobre la lactancia actual del animal."""
    return {
        "l0": "r.LactationNumber = 0",
        "l1": "r.LactationNumber = 1",
        "l2+": "r.LactationNumber >= 2",
        "l1+": "r.LactationNumber >= 1",
    }.get(cat, "1 = 1")


def sql_esperados(categoria: str = "todas", herd=None) -> str:
    """Un renglón por animal preñado, con su parto y su secado esperados.

    El parto esperado sale de la inseminación efectiva más la gestación; el
    secado, de restarle el período seco. La leche de los últimos 7 días sirve
    para decidir el orden de secado: una vaca que ya bajó a poco se seca sin
    costo, una que todavía produce bien se puede estirar.
    """
    return f"""
        WITH pren AS (
            SELECT b.OID AS oid, b.Number AS rp, r.LactationNumber AS lact,
                   b.ToBeCulled AS descartar,
                   r.LastLactationChangeDate AS ult_parto,
                   ae.DateAndTime AS f_insem,
                   DATEADD(day, {GESTACION_DIAS}, ae.DateAndTime) AS parto_esperado
            FROM AnimalReproductionInfo r
            JOIN BasicAnimal b ON b.OID = r.Animal AND b.GCRecord IS NULL
                              AND b.ExitDate IS NULL AND b.Number > 0
            JOIN AnimalLatestHistoryIndex ix ON ix.Animal = r.Animal AND ix.GCRecord IS NULL
            JOIN AbstractAnimalEvent ae ON ae.OID = ix.EffectiveInsemination
                                       AND ae.GCRecord IS NULL
            WHERE r.GCRecord IS NULL AND r.IsPregnant = 1
              AND {_filtro_categoria(categoria)}
              AND {rebano.filtro('b', herd)}
        )
        SELECT p.rp, p.lact,
               DATEDIFF(day, p.ult_parto, GETDATE()) AS deo,
               CONVERT(varchar(10), p.parto_esperado, 23) AS parto_esperado,
               DATEDIFF(day, GETDATE(), p.parto_esperado) AS dias_al_parto,
               CONVERT(varchar(10), DATEADD(day, -{PERIODO_SECO_DIAS}, p.parto_esperado), 23)
                   AS secado_esperado,
               DATEDIFF(day, GETDATE(), DATEADD(day, -{PERIODO_SECO_DIAS}, p.parto_esperado))
                   AS dias_al_secado,
               ISNULL(CAST(p.descartar AS int), 0) AS descartar,
               leche.kg7 AS leche_7d,
               CASE WHEN DATEDIFF(day, p.f_insem, GETDATE()) > {PRENEZ_MAX_DIAS}
                     OR (p.ult_parto IS NOT NULL AND p.f_insem <= p.ult_parto)
                    THEN 1 ELSE 0 END AS vencido
        FROM pren p
        OUTER APPLY (
            SELECT AVG(d.TotalYield) AS kg7
            FROM AnimalDaily d
            WHERE d.BasicAnimal = p.oid AND d.GCRecord IS NULL AND d.TotalYield > 0
              AND d.Date >= DATEADD(day, -7, CAST(GETDATE() AS date))
        ) leche
        ORDER BY dias_al_parto
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


# Bajas reales de los últimos doce meses, para estimar el descarte mensual en
# vez de asumir un número fijo como hace DelPro.
def sql_descarte_mensual(herd=None) -> str:
    return f"""
        SELECT COUNT(*) AS bajas
        FROM BasicAnimal b
        WHERE b.GCRecord IS NULL AND b.Number > 0
          AND b.ExitDate >= DATEADD(month, -12, CAST(GETDATE() AS date))
          AND b.ExitDate <= CAST(GETDATE() AS date)
          AND {rebano.filtro_historico('b', herd)}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


SQL_VO_HOY = f"""
    SELECT COUNT(*) AS vo
    FROM BasicAnimal b
    JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
      AND r.LactationNumber >= 1 AND ISNULL(r.IsDryingOff, 0) = 0
      AND {{filtro}}
"""


def sql_vo_hoy(herd=None) -> str:
    return SQL_VO_HOY.format(filtro=rebano.filtro('b', herd))


# --- Armado ------------------------------------------------------------------

def _mes(iso: str) -> str:
    return iso[:7]


def _sumar_meses(clave: str, n: int) -> str:
    a, m = clave.split("-")
    t = int(a) * 12 + int(m) - 1 + n
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def analizar(data_esperados, data_descarte, data_vo, hoy: datetime.date,
             meses: int = 9, descarte_manual=None) -> dict:
    """Arma las listas por animal y la proyección mensual de vacas en ordeñe."""
    cols = data_esperados["columns"]
    filas = [dict(zip(cols, f)) for f in data_esperados["rows"]]
    for f in filas:
        f["vencido"] = bool(f.get("vencido"))
        f["descartar"] = bool(f.get("descartar"))
        f["leche_7d"] = round(float(f["leche_7d"]), 1) if f.get("leche_7d") is not None else None

    vigentes = [f for f in filas if not f["vencido"]]

    bajas12 = int((data_descarte["rows"] or [[0]])[0][0] or 0)
    descarte = (int(descarte_manual) if descarte_manual is not None
                else round(bajas12 / 12))

    vo = int((data_vo["rows"] or [[0]])[0][0] or 0)
    mes_actual = hoy.strftime("%Y-%m")
    claves = [_sumar_meses(mes_actual, i) for i in range(meses)]

    por_mes = {m: {"partos_vacas": 0, "partos_vaquillonas": 0, "secados": 0} for m in claves}
    for f in vigentes:
        m = _mes(f["parto_esperado"] or "")
        if m in por_mes:
            clave = "partos_vaquillonas" if int(f["lact"] or 0) == 0 else "partos_vacas"
            por_mes[m][clave] += 1
        ms = _mes(f["secado_esperado"] or "")
        # Solo las vacas se secan; una vaquillona que no parió no está en ordeñe.
        if ms in por_mes and int(f["lact"] or 0) >= 1:
            por_mes[ms]["secados"] += 1

    proyeccion, acumulado = [], vo
    for i, m in enumerate(claves):
        d = por_mes[m]
        total = d["partos_vacas"] + d["partos_vaquillonas"]
        # El mes en curso ya transcurrió en parte: se descarta proporcional a
        # los días que quedan, no el mes entero.
        if i == 0:
            import calendar
            dias_mes = calendar.monthrange(hoy.year, hoy.month)[1]
            desc = round(descarte * (dias_mes - hoy.day + 1) / dias_mes)
        else:
            desc = descarte
        acumulado = acumulado + total - d["secados"] - desc
        proyeccion.append({
            "mes": m, "partos_totales": total,
            "partos_vacas": d["partos_vacas"],
            "partos_vaquillonas": d["partos_vaquillonas"],
            "secados": d["secados"], "descarte": desc,
            "vo_total": acumulado,
        })

    def lista(clave_fecha, clave_dias):
        return sorted(
            [{"rp": f["rp"], "lact": f["lact"], "deo": f["deo"],
              "fecha": f[clave_fecha], "dias": f[clave_dias],
              "parto_esperado": f["parto_esperado"],
              "leche_7d": f["leche_7d"], "descartar": f["descartar"],
              "vencido": f["vencido"]} for f in filas],
            key=lambda x: (x["dias"] is None, x["dias"]))

    return {
        "proyeccion": proyeccion,
        "partos": lista("parto_esperado", "dias_al_parto"),
        "secados": [x for x in lista("secado_esperado", "dias_al_secado")
                    if (x["lact"] or 0) >= 1],
        "resumen": {
            "vo_hoy": vo,
            "prenados": len(filas),
            "vigentes": len(vigentes),
            "vencidos": len(filas) - len(vigentes),
            "descarte_mensual": descarte,
            "bajas_12m": bajas12,
        },
    }
