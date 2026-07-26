# -*- coding: utf-8 -*-
"""Proyección de rebaños: evolución mensual de vacas lactantes y producción,
hacia atrás (real) y hacia adelante (proyectada).

Réplica del informe "Proyección de rebaños" de DelPro. El modelo se dedujo
comparando contra el informe del tambo del 26/07/2026 y quedó verificado en
sus tres piezas:

1. ECUACIÓN DE BALANCE (exacta, verificada mes a mes):

       lactantes[m] = lactantes[m-1] + vacas_parir[m] + novillas_parir[m]
                      - secados[m] - salidas[m]

   Julio:  3253 + 178 + 136 - 133 - 127 = 3307  ✓ (DelPro: 3.307)
   Agosto: 3307 + 170 + 106 - 184 - 148 = 3251  ✓ (DelPro: 3.251)
   ...y así los doce meses.

2. PUNTO DE PARTIDA: la cantidad de vacas lactantes de hoy — animales activos
   con al menos un parto y que no están en secado.

   OJO CON LOS REBAÑOS: esta base la comparten varios tambos (ver `rebano.py`).
   Sumando los tres da 3.253, que es exactamente el punto de partida del
   informe de DelPro... porque ese informe estaba en "Todos los rebaños". La
   Ponderosa sola tiene 1.621. Acá se filtra por el rebaño del tambo, así que
   los números NO coinciden con ese informe y está bien que no coincidan.

3. PRODUCCIÓN (exacta, verificada):

       producción[m] = lactantes[m] * kg_por_vaca_dia_año_pasado[m] * días[m]

   Nov-2026: 3184 * 26,16 * 30 = 2.498.803  ✓ (DelPro: 2.498.803)

   Ojo: NO es la suma de la leche realmente ordeñada. Las dos filas de
   producción (proyectada y del año pasado) usan el MISMO promedio diario por
   vaca, el del año pasado; lo único que cambia es la cantidad de vacas.

LO QUE NO SE PUEDE REPLICAR — los partos previstos. DelPro no solo cuenta las
preñeces confirmadas: simula preñeces FUTURAS (novillas que llegan a edad de
servicio, vacas que se vuelven a inseminar). Se ve en que proyecta 1.747 vacas
y 1.150 novillas a parir en doce meses cuando hoy hay 1.715 animales preñados
en total. Acá los partos salen SOLO de preñeces confirmadas, así que a partir
del mes ~9 (cuando se agota la gestación en curso) la proyección se queda
corta contra DelPro. Está avisado en la página.

SOBRE LA CALIDAD DE LOS DATOS — corregido al filtrar por rebaño. Sin filtrar,
796 de 1.715 animales marcados como preñados (46%) no tenían inseminación
válida, y parecía un problema grave de carga. Filtrando a La Ponderosa son
19 de 920 (2%), con 176 días de preñez promedio: los datos del tambo están
sanos. Aquel 46% era casi todo de los otros dos tambos de la base.

Lo que sí es real y afecta a todos: `EventPregCheck.DaysFromInsemination`
viene en 0 en toda la base, así que el parto esperado no se puede sacar del
chequeo de preñez y sale de la inseminación efectiva más la gestación.
"""
import calendar
import datetime

import rebano

# Rango máximo, en meses, que se puede pedir de una vez.
RANGO_MESES_MAX = 36

# Gestación. El promedio real medido en esta base (concepción → parto, sobre
# 4.271 partos desde 2025) es 276,5 días; se usa el estándar de 280 porque es
# el que aplica DelPro y la diferencia queda dentro del mismo mes.
GESTACION_DIAS = 280

# Período seco: días entre el secado y el parto siguiente. Estándar DeLaval.
PERIODO_SECO_DIAS = 60

# Tope de antigüedad de la inseminación para considerar una preñez creíble.
# Más que esto y el animal ya tendría que haber parido: es un dato viejo.
PRENEZ_MAX_DIAS = 290


def _mes(fecha: datetime.date) -> str:
    return fecha.strftime("%Y-%m")


def _primer_dia(clave: str) -> datetime.date:
    anio, mes = clave.split("-")
    return datetime.date(int(anio), int(mes), 1)


def _sumar_meses(clave: str, n: int) -> str:
    d = _primer_dia(clave)
    total = d.year * 12 + (d.month - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _dias_del_mes(clave: str) -> int:
    d = _primer_dia(clave)
    return calendar.monthrange(d.year, d.month)[1]


def rango_meses(desde: str, hasta: str) -> list:
    """Lista de claves 'AAAA-MM' entre las dos, inclusive."""
    out, actual = [], desde
    while actual <= hasta and len(out) <= RANGO_MESES_MAX + 24:
        out.append(actual)
        actual = _sumar_meses(actual, 1)
    return out


# --- Consultas ---------------------------------------------------------------

# Punto de partida: vacas lactantes de HOY. Una vaca cuenta como lactante si
# está activa, ya parió al menos una vez y no está marcada en secado.
def sql_lactantes_hoy(herd=None) -> str:
    return f"""
    SELECT COUNT(*) AS lactantes
    FROM BasicAnimal b
    JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
    WHERE b.GCRecord IS NULL AND b.ExitDate IS NULL AND b.Number > 0
      AND r.LactationNumber >= 1 AND ISNULL(r.IsDryingOff, 0) = 0
      AND {rebano.filtro('b', herd)}
"""


def sql_partos_reales(desde: str, hasta: str, herd=None) -> str:
    """Partos ya ocurridos, por mes, separando primer parto (novilla) del resto.

    `AbstractAnimalEvent.LactationNumber` en un parto es la lactancia que
    ARRANCA, así que un 1 es una novilla que acaba de parir por primera vez.
    """
    return f"""
        SELECT FORMAT(ae.DateAndTime, 'yyyy-MM') AS mes,
               SUM(CASE WHEN ae.LactationNumber <= 1 THEN 0 ELSE 1 END) AS vacas,
               SUM(CASE WHEN ae.LactationNumber <= 1 THEN 1 ELSE 0 END) AS novillas
        FROM EventCalving c
        JOIN AbstractAnimalEvent ae ON ae.OID = c.OID AND ae.GCRecord IS NULL
        WHERE ae.DateAndTime >= '{desde}-01'
          AND ae.DateAndTime < DATEADD(month, 1, '{hasta}-01')
          AND {rebano.filtro_por_animal('ae.BasicAnimal', herd)}
        GROUP BY FORMAT(ae.DateAndTime, 'yyyy-MM')
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


# Partos esperados de las preñeces YA confirmadas. La fecha sale de la
# inseminación efectiva más la gestación. Se descartan dos casos:
#   - inseminación anterior al último parto (dato viejo que quedó colgado);
#   - inseminación de hace más de PRENEZ_MAX_DIAS (ese animal ya tendría que
#     haber parido: la preñez está mal cargada).
def sql_partos_previstos(herd=None, gestacion: int = None) -> str:
    gestacion = gestacion or GESTACION_DIAS
    return f"""
    SELECT FORMAT(DATEADD(day, {gestacion}, ae.DateAndTime), 'yyyy-MM') AS mes,
           SUM(CASE WHEN r.LactationNumber = 0 THEN 0 ELSE 1 END) AS vacas,
           SUM(CASE WHEN r.LactationNumber = 0 THEN 1 ELSE 0 END) AS novillas
    FROM AnimalReproductionInfo r
    JOIN BasicAnimal b ON b.OID = r.Animal AND b.GCRecord IS NULL
                      AND b.ExitDate IS NULL AND b.Number > 0
    JOIN AnimalLatestHistoryIndex ix ON ix.Animal = r.Animal AND ix.GCRecord IS NULL
    JOIN AbstractAnimalEvent ae ON ae.OID = ix.EffectiveInsemination AND ae.GCRecord IS NULL
    WHERE r.GCRecord IS NULL AND r.IsPregnant = 1
      AND (r.LastLactationChangeDate IS NULL OR ae.DateAndTime > r.LastLactationChangeDate)
      AND DATEDIFF(day, ae.DateAndTime, GETDATE()) BETWEEN 0 AND {PRENEZ_MAX_DIAS}
      AND {rebano.filtro('b', herd)}
    GROUP BY FORMAT(DATEADD(day, {gestacion}, ae.DateAndTime), 'yyyy-MM')
"""


# Cuántas preñeces quedan afuera por dato imposible — se muestra en la página
# como advertencia, no se esconde.
def sql_preneces_descartadas(herd=None) -> str:
    return f"""
    SELECT SUM(CASE WHEN ix.EffectiveInsemination IS NULL
                     OR ae.DateAndTime IS NULL
                     OR DATEDIFF(day, ae.DateAndTime, GETDATE()) > {PRENEZ_MAX_DIAS}
                     OR (r.LastLactationChangeDate IS NOT NULL
                         AND ae.DateAndTime <= r.LastLactationChangeDate)
                    THEN 1 ELSE 0 END) AS descartadas,
           COUNT(*) AS prenadas
    FROM AnimalReproductionInfo r
    JOIN BasicAnimal b ON b.OID = r.Animal AND b.GCRecord IS NULL
                      AND b.ExitDate IS NULL AND b.Number > 0
    LEFT JOIN AnimalLatestHistoryIndex ix ON ix.Animal = r.Animal AND ix.GCRecord IS NULL
    LEFT JOIN AbstractAnimalEvent ae ON ae.OID = ix.EffectiveInsemination AND ae.GCRecord IS NULL
    WHERE r.GCRecord IS NULL AND r.IsPregnant = 1
      AND {rebano.filtro('b', herd)}
"""


def sql_salidas_reales(desde: str, hasta: str, herd=None) -> str:
    """Bajas por mes (vacas que dejaron el rodeo)."""
    return f"""
        SELECT FORMAT(b.ExitDate, 'yyyy-MM') AS mes, COUNT(*) AS salidas
        FROM BasicAnimal b
        WHERE b.GCRecord IS NULL AND b.Number > 0 AND b.ExitDate IS NOT NULL
          AND b.ExitDate >= '{desde}-01'
          AND b.ExitDate < DATEADD(month, 1, '{hasta}-01')
          -- Filtro histórico: al dar de baja un animal DelPro le borra el
          -- grupo, así que el filtro normal las excluiría a todas.
          AND {rebano.filtro_historico('b', herd)}
        GROUP BY FORMAT(b.ExitDate, 'yyyy-MM')
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


# Largo de lactancia usado para reconstruir el histórico: el intervalo real
# entre partos de este tambo (390,8 días medidos sobre 1.985 partos desde 2024)
# menos el período seco. Una vaca cuenta como lactante en un mes si parió
# dentro de esa ventana y todavía no salió del rodeo.
LACTANCIA_DIAS = 330


def sql_lactantes_historico(desde: str, hasta: str, herd=None) -> str:
    """Vacas lactantes por mes, contadas desde los partos realmente ocurridos.

    Para el pasado NO se usa la ecuación de balance: arrastrar el balance hacia
    atrás acumula el error de cada mes y termina inflando el resultado (probado:
    3.992 para jul-2025 contra los 2.813 del informe). Contar partos es dato
    medido.

    Aun así queda por DEBAJO de DelPro (~600 vacas). La causa es la misma que
    ensucia los partos previstos: hay partos que no quedaron registrados como
    evento. Está avisado en la página.
    """
    return f"""
        WITH meses AS (
            SELECT DATEFROMPARTS(YEAR(d), MONTH(d), 1) AS m
            FROM (SELECT DISTINCT CAST(DATEADD(month, n.i, '{desde}-01') AS date) AS d
                  FROM (SELECT TOP ({RANGO_MESES_MAX + 26})
                               ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
                        FROM sys.all_objects) n
                  WHERE DATEADD(month, n.i, '{desde}-01') <= '{hasta}-01') t
        ), partos AS (
            SELECT ae.BasicAnimal AS animal, ae.DateAndTime AS fecha
            FROM EventCalving c
            JOIN AbstractAnimalEvent ae ON ae.OID = c.OID AND ae.GCRecord IS NULL
        )
        SELECT FORMAT(m.m, 'yyyy-MM') AS mes, COUNT(DISTINCT p.animal) AS lactantes
        FROM meses m
        JOIN partos p ON p.fecha < DATEADD(month, 1, m.m)
                     AND p.fecha >= DATEADD(day, -{LACTANCIA_DIAS}, DATEADD(month, 1, m.m))
        JOIN BasicAnimal b ON b.OID = p.animal AND b.GCRecord IS NULL AND b.Number > 0
        WHERE (b.ExitDate IS NULL OR b.ExitDate >= DATEADD(month, 1, m.m))
          AND {rebano.filtro('b', herd)}
        GROUP BY FORMAT(m.m, 'yyyy-MM')
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


def sql_kg_por_vaca(desde: str, hasta: str, herd=None) -> str:
    """Promedio de kg por vaca y por día, por mes.

    Es el único número del informe que sale de la leche realmente medida. Se
    promedian los ordeños válidos con producción > 0: los ceros son días sin
    lectura, no vacas que no dieron leche. Verificado contra DelPro
    (nov-2025: 26,16 acá, 26,2 en el informe).
    """
    return f"""
        SELECT FORMAT(d.Date, 'yyyy-MM') AS mes,
               AVG(CASE WHEN d.TotalYield > 0 THEN d.TotalYield END) AS kg_vaca_dia,
               COUNT(*) AS filas
        FROM AnimalDaily d
        WHERE d.GCRecord IS NULL
          AND d.Date >= '{desde}-01'
          AND d.Date < DATEADD(month, 1, '{hasta}-01')
          AND {rebano.filtro_por_animal('d.BasicAnimal', herd)}
        GROUP BY FORMAT(d.Date, 'yyyy-MM')
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 25)
    """


# --- Modelo ------------------------------------------------------------------

def _por_mes(data, *campos):
    """{mes: (campo1, campo2, ...)} a partir de un resultado de db.run_query."""
    cols = data["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    out = {}
    for row in data["rows"]:
        clave = row[idx["mes"]]
        out[str(clave)] = tuple(row[idx[c]] for c in campos)
    return out


def _secados_desde_partos(partos_por_mes: dict, periodo_seco: int = None) -> dict:
    """Secados por mes, derivados de los partos.

    Una vaca que pare el día D se secó PERIODO_SECO_DIAS antes. A nivel mes,
    los secados de un mes son los partos de ~2 meses después. Se reparte el mes
    de parto entre los dos meses de secado que le corresponden, en proporción a
    cuántos días de ese mes caen en cada uno.
    """
    periodo_seco = periodo_seco or PERIODO_SECO_DIAS
    secados = {}
    for mes, total in partos_por_mes.items():
        if not total:
            continue
        d1 = _primer_dia(mes)
        ndias = _dias_del_mes(mes)
        # Cada día del mes de parto aporta su secado a mes(día - período seco).
        reparto = {}
        for dia in range(ndias):
            fecha_secado = d1 + datetime.timedelta(days=dia - periodo_seco)
            reparto[_mes(fecha_secado)] = reparto.get(_mes(fecha_secado), 0) + 1
        for m, cuenta in reparto.items():
            secados[m] = secados.get(m, 0) + total * cuenta / ndias
    return {m: int(round(v)) for m, v in secados.items()}


def analizar(data_lact_hoy, data_partos_reales, data_partos_prev, data_salidas,
             data_kg, data_descartadas, data_lact_hist, desde: str, hasta: str,
             hoy: datetime.date, gestacion: int = None, periodo_seco: int = None) -> dict:
    """Arma la serie mensual completa, real hacia atrás y proyectada hacia adelante.

    `gestacion` y `periodo_seco` salen de los parámetros que tiene configurados
    el tambo en DelPro (ver `parametros.py`); las constantes del módulo quedan
    solo como respaldo.
    """
    gestacion = gestacion or GESTACION_DIAS
    periodo_seco = periodo_seco or PERIODO_SECO_DIAS
    mes_actual = _mes(hoy)

    # La serie interna arranca 13 meses antes de lo pedido para poder calcular
    # la comparación contra el mismo mes del año pasado.
    inicio = _sumar_meses(desde, -13)
    meses = rango_meses(inicio, _sumar_meses(hasta, 2))

    reales = _por_mes(data_partos_reales, "vacas", "novillas")
    previstos = _por_mes(data_partos_prev, "vacas", "novillas")
    salidas_reales = _por_mes(data_salidas, "salidas")
    kg = _por_mes(data_kg, "kg_vaca_dia")

    # Partos: reales hasta el mes en curso, previstos de ahí en adelante. El
    # mes en curso toma los previstos (ya incluye lo que falta parir).
    partos = {}
    for m in meses:
        if m < mes_actual:
            v, n = reales.get(m, (0, 0))
        else:
            v, n = previstos.get(m, (0, 0))
        partos[m] = (int(v or 0), int(n or 0))

    secados = _secados_desde_partos({m: partos[m][0] + partos[m][1] for m in meses},
                                    periodo_seco)

    # Tasa de salida del mismo mes del año pasado, en % sobre las lactantes de
    # ese mes. Es lo que se usa para proyectar las salidas futuras.
    def salidas_de(m):
        v = salidas_reales.get(m)
        return int(v[0]) if v and v[0] is not None else 0

    # Balance. Se conoce con certeza un solo punto —las lactantes de hoy— así
    # que la serie se arma desde ahí: hacia adelante sumando, hacia atrás
    # despejando la misma ecuación.
    lactantes_hoy = int(data_lact_hoy["rows"][0][0] or 0)
    lact = {mes_actual: lactantes_hoy}

    def salidas_de_mes(m, lact_anterior):
        """Reales si el mes ya pasó; si es futuro, tasa del mismo mes del año pasado."""
        if m <= mes_actual:
            return salidas_de(m)
        hace_un_anio = _sumar_meses(m, -12)
        base = lact.get(hace_un_anio)
        s_ant = salidas_de(hace_un_anio)
        tasa = (s_ant / base) if base else 0
        return int(round(lact_anterior * tasa))

    # La serie entera sale de la MISMA ecuación de balance, anclada en el único
    # dato exacto que hay: las lactantes de hoy. Hacia adelante se suma; hacia
    # atrás se despeja.
    #
    # Se probó reconstruir el pasado contando partos reales en vez de despejar
    # el balance, y quedó peor: da ~1.800 vacas contra las 3.253 de hoy, un
    # salto del doble en un solo mes que rompe la continuidad de la serie. La
    # causa es que faltan eventos de parto (2026 va a 203 partos/mes contra 241
    # en 2025). Ese conteo alternativo se sigue calculando y viaja en la
    # respuesta como `lactantes_medido`, para poder ver la brecha.
    for m in [x for x in meses if x > mes_actual]:
        prev = lact[_sumar_meses(m, -1)]
        s = salidas_de_mes(m, prev)
        lact[m] = prev + partos[m][0] + partos[m][1] - secados.get(m, 0) - s

    for m in reversed([x for x in meses if x < mes_actual]):
        sig = _sumar_meses(m, 1)
        lact[m] = (lact[sig] - partos[sig][0] - partos[sig][1]
                   + secados.get(sig, 0) + salidas_de(sig))

    medido = _por_mes(data_lact_hist, "lactantes")

    filas = []
    for m in rango_meses(desde, hasta):
        ant = _sumar_meses(m, -12)
        dias = _dias_del_mes(m)
        kg_ant = kg.get(ant, (None,))[0]
        kg_ant = round(float(kg_ant), 2) if kg_ant is not None else None
        lact_m = lact.get(m)
        lact_ant = lact.get(ant)
        prev_mes = lact.get(_sumar_meses(m, -1))
        s_ant = salidas_de(ant)
        filas.append({
            "mes": m,
            "futuro": m > mes_actual,
            "vacas_parir": partos[m][0],
            "novillas_parir": partos[m][1],
            "secados": secados.get(m, 0),
            "salidas": salidas_de_mes(m, prev_mes or 0),
            "tasa_salida_ant": round(100.0 * s_ant / lact_ant, 1) if lact_ant else None,
            "lactantes": lact_m,
            "lactantes_ant": lact_ant,
            "lactantes_medido": int(medido[m][0]) if m in medido else None,
            "cambio": (lact_m - prev_mes) if (lact_m is not None and prev_mes is not None) else None,
            "kg_vaca_dia_ant": kg_ant,
            "produccion": round(lact_m * kg_ant * dias) if (lact_m and kg_ant) else 0,
            "produccion_ant": round(lact_ant * kg_ant * dias) if (lact_ant and kg_ant) else 0,
            "dias": dias,
        })

    def suma(c):
        return sum(f[c] or 0 for f in filas)

    def media(c):
        vals = [f[c] for f in filas if f[c] is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    desc = data_descartadas["rows"][0] if data_descartadas["rows"] else (0, 0)
    return {
        "meses": filas,
        "totales": {c: suma(c) for c in ("vacas_parir", "novillas_parir", "secados",
                                         "salidas", "produccion", "produccion_ant")},
        "promedios": {c: media(c) for c in ("lactantes", "lactantes_ant", "tasa_salida_ant",
                                            "kg_vaca_dia_ant", "produccion", "produccion_ant",
                                            "cambio")},
        "base": {
            "lactantes_hoy": lactantes_hoy,
            "mes_actual": mes_actual,
            "prenadas": int(desc[1] or 0),
            "prenadas_descartadas": int(desc[0] or 0),
        },
        "parametros": {
            "gestacion_dias": gestacion,
            "periodo_seco_dias": periodo_seco,
        },
    }
