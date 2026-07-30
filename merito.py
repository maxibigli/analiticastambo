# -*- coding: utf-8 -*-
"""Índice de mérito del animal: 0 a 100, para ordenar candidatas a descarte.

QUÉ ES Y QUÉ NO ES. Es un resumen de la VIDA YA VIVIDA de una vaca, comparada
con las vacas de este mismo rodeo. Contesta «de las que tengo, cuáles rindieron
mejor». NO es una predicción, NO es un valor genético, y NO se puede usar para
elegir con qué toro servir: para eso hace falta el pedigrí, que vive aparte en
`herencia.py` y se muestra al lado, sin mezclarse.

    50 = la mediana del rodeo. 100 = la mejor del rodeo en todo.

ES FENOTÍPICO, o sea que mezcla genética con ambiente, manejo, sanidad y suerte.
Dos vacas con la misma genética paridas en meses distintos pueden diferir mucho.
Por eso el índice sirve para decidir sobre EL ANIMAL (venderlo, retenerlo) y no
sobre su familia.

ES RELATIVO AL RODEO, no absoluto. Si el rodeo entero mejora, los índices no
suben: se reacomodan. Un 30 no significa «mala vaca», significa «peor que el 70%
de las de este tambo». Y al agregar o sacar animales, todos los índices se
recalculan.

LOS CUATRO EJES Y POR QUÉ ESOS PESOS. Los índices de selección de la industria
(TPI, Mérito Neto) reparten aproximadamente mitad producción y mitad «el resto»
—salud, fertilidad, longevidad—. Acá se hace lo mismo, con los pesos a la vista
para que el tambo los pueda discutir y cambiar:

    Producción   40   es de lo que vive el tambo
    Sanidad      25   una vaca enferma cuesta plata y leche
    Fertilidad   25   una vaca que no se preña no vuelve a producir
    Longevidad   10   cuántas lactancias sostuvo por año de vida

TODO SE NORMALIZA POR OPORTUNIDAD, y esto es lo que evita el error más grave.
Una vaca de primera lactancia NO PUDO tener tres mastitis en cinco años: sin
normalizar, las jóvenes ganarían siempre en sanidad y perderían siempre en
producción (L1 promedia 35,8 litros y L4 46,4 — medido en este rodeo). Así que:
  * la producción se compara contra las vacas de SU MISMA LACTANCIA;
  * las enfermedades se cuentan por AÑO DE EXPOSICIÓN, no en total;
  * la longevidad es lactancias por año de vida, no lactancias.

CADA EJE INFORMA SU CONFIANZA, y el índice también. Una vaquillona sin partos no
tiene fertilidad medible: ese eje se EXCLUYE y su peso se reparte entre los
demás, en vez de puntuarla con un cero que sería inventado. Un índice armado con
un solo eje se muestra igual, avisando que es un índice flojo.

LO QUE NO SE PUDO USAR, medido el 30/07/2026:
  * `EventCalving.CalvingEase` (la «facilidad de parto» que muestra el historial)
    NO es una escala: 3.046 partos tienen el valor 35, 42 tienen 36 y 2 tienen
    37. Es un identificador, no un puntaje, y por eso el «tipo de parto» no
    entra al índice.
  * Los terneros por parto sí sirven (simple vs. mellizos) pero con cobertura
    pobre: 1.296 de 3.090 partos no tienen ningún ternero cargado. Se informa
    como dato, no se puntúa.
  * El catálogo de diagnósticos es un cajón de sastre: de los 25 más frecuentes,
    «Tratamiento», «Vacia», «I.U.Normal», «Calostrado», «Comentario» y hasta
    movimientos entre establecimientos («Vaq. A San Ricardo») no son
    enfermedades. Se cuenta una lista explícita y el resto se ignora.
  * «Alta Mastitis» (1.291 casos) es el ALTA del tratamiento, no un caso nuevo.
    Contarlo duplicaría cada mastitis.
"""
import datetime

import rebano

# --- Pesos del índice --------------------------------------------------------
# A la vista y en un solo lugar, para que el tambo los discuta. Si un eje no se
# puede medir, su peso se reparte entre los que sí (ver `_combinar`).
PESOS = {"produccion": 40, "sanidad": 25, "fertilidad": 25, "longevidad": 10}

# --- Qué se cuenta como enfermedad ------------------------------------------
# Lista explícita, no "todo lo que esté en Diagnosis": ese catálogo tiene
# estados reproductivos, comentarios y traslados. Cada grupo lleva su peso
# relativo dentro del eje de sanidad.
#
# Los patrones se comparan en minúsculas y sin acentos (COLLATE en SQL).
ENFERMEDADES = {
    # Mastitis por cuarto: 'Mastitis 1/4 P.D.', 'A.I.', 'P.I.', 'A.D.' y la
    # genérica 'Mastitis'. NO entra 'Alta Mastitis', que es el alta del
    # tratamiento y duplicaría el caso.
    "mastitis": {"peso": 0.40, "label": "Mastitis",
                 "like": ["mastitis 1/4%", "mastitis"],
                 "excluir": ["alta mastitis%"]},
    # Patología podal: es la segunda causa de descarte después de la ubre y
    # afecta producción por menor consumo. 'Desvasada' sola parece ser rutina
    # preventiva (1.031 casos), así que solo entra la correctiva y las lesiones.
    "podal": {"peso": 0.25, "label": "Patología podal",
              "like": ["dermatitis digital%", "renga%", "demat digit%",
                       "desvasado correctivo%"],
              "excluir": []},
    "metritis": {"peso": 0.15, "label": "Metritis",
                 "like": ["metritis%"], "excluir": []},
    # Defecto ESTRUCTURAL, no un episodio: una ubre incompleta no se cura y
    # limita a la vaca toda la vida. Por eso pesa aunque aparezca una sola vez.
    "ubre": {"peso": 0.12, "label": "Ubre incompleta",
             "like": ["ubre incompleta%"], "excluir": []},
    "otras": {"peso": 0.08, "label": "Otras (neumonía, quistes, lesiones)",
              "like": ["neumonia%", "q.o.%", "lesion traumatica%"],
              "excluir": []},
}

# Desde qué edad se cuenta la exposición a enfermarse. Antes de los 2 años la
# vaca no está en ordeñe y no puede tener mastitis clínica — mismo criterio que
# `herencia.EDAD_INICIO_ANIOS`.
EDAD_INICIO_ANIOS = 2
ANIOS_MIN = 0.5      # piso, para que una vaca reciente no dé una tasa infinita

# --- Referencias reproductivas ----------------------------------------------
# Los tramos salen de lo medido en este rodeo, no de una tabla de manual:
#   intervalo entre partos: 449 casos en 340-379 días, 280 en 480+
#   edad al primer parto:   738 en 22-24 meses, 86 en 34+
# Se usan para PUNTUAR de 0 a 1, no para aprobar o desaprobar.
IEP_IDEAL = 380          # días; por debajo de esto no se premia más
IEP_MALO = 480           # días; de acá para arriba es el peor puntaje
EPP_IDEAL = 24           # meses al primer parto
EPP_MALO = 34            # meses
# Un intervalo entre partos por debajo de esto no es una virtud: es un aborto o
# un parto mal cargado. Se descarta en vez de premiarlo.
IEP_MIN_PLAUSIBLE = 300

# Producción: se ignoran los valores imposibles. Se midió un máximo de 149,37
# litros/día en lactancia 1, que no existe.
KG_DIA_MAX_PLAUSIBLE = 100.0
DIAS_MIN_LACTANCIA = 30   # días con dato para que una lactancia cuente

# Confianza: cuántas fuentes tiene el índice. Sin esto una vaquillona con un solo
# eje medido se muestra igual que una vaca con historia completa.
CONFIANZA = {4: "alta", 3: "media", 2: "baja", 1: "muy baja", 0: "sin datos"}

# SIN PRODUCCIÓN MEDIDA NO HAY ÍNDICE DE MÉRITO, y esto no es un detalle: es lo
# que evita el error que hace inútil a un seleccionador.
#
# La regla general del módulo es «un eje que no se puede medir se excluye y su
# peso se reparte». Es correcta cuando falta un eje secundario, y catastrófica
# cuando falta el principal. Medido el 30/07/2026: la vaquillona RP 39 —3 años y
# 8 meses, cero partos, cero litros— sacaba 86,4 porque su único eje medible era
# sanidad (nunca se enfermó, porque nunca estuvo expuesta a enfermarse en
# ordeñe) y ese eje se llevaba el 100% del peso. La vaca RP 16823, que da 66,6
# litros por día, sacaba 70,7. O sea que el índice premiaba NO HABER TENIDO
# OPORTUNIDAD DE FALLAR, que es justo al revés de para lo que sirve.
#
# El mérito de una vaca de tambo es producir. Sin ese eje lo que queda no es un
# mérito menos preciso: es otra cosa. Para un animal sin historia productiva el
# estimador correcto es el pedigrí —padre y madre—, que vive en `herencia.py` y
# se muestra en su propio panel de la ficha.
EJE_OBLIGATORIO = "produccion"

# Debajo de esta proporción del peso total, el índice se muestra pero NO es
# comparable contra otro animal: están hechos con distinta cantidad de
# información. La pantalla lo dice y la tabla no debería ordenar por él.
PESO_MIN_COMPARABLE = 65


def _like(col: str, patrones: list, col_collate: str) -> str:
    return " OR ".join(f"{col} {col_collate} LIKE '{p}'" for p in patrones)


def sql_vida(herd=None) -> str:
    """Una fila por animal con los indicadores de su vida.

    Se agrupa en SQL y se devuelve una fila por animal (no una por evento):
    son miles de vacas por miles de eventos. Y se clasifica con `COLLATE
    Latin1_General_CI_AI` explícito para no depender de la collation de la
    instalación — el mismo criterio que `herencia.sql_historia`.

    NO se filtra `ExitDate IS NULL`: para calibrar el índice hace falta saber
    cómo les fue a las que ya se fueron.
    """
    col = "COLLATE Latin1_General_CI_AI"
    diag = "COALESCE(tn.ItemValue, dg.Description, '')"
    # Los CASE del SELECT principal tienen que mirar `ev.dx` (la columna que
    # EXPONE el CTE) y no `tn.ItemValue`: esos alias solo existen adentro del
    # CTE, y usarlos afuera falla con "multi-part identifier could not be bound".
    casos = []
    for clave, cfg in ENFERMEDADES.items():
        cond = f"({_like('ev.dx', cfg['like'], col)})"
        if cfg["excluir"]:
            cond += f" AND NOT ({_like('ev.dx', cfg['excluir'], col)})"
        casos.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS enf_{clave}")
    return f"""
        WITH ev AS (
          SELECT a.BasicAnimal AS animal, {diag} AS dx
          FROM AbstractAnimalEvent a
          JOIN DiagnosisTreatmentEvent e ON e.OID = a.OID
          LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
          LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
          WHERE a.GCRecord IS NULL
        ),
        partos AS (
          SELECT a.BasicAnimal AS animal,
                 COUNT(*) AS n_partos,
                 MIN(a.DateAndTime) AS primer_parto,
                 MAX(a.DateAndTime) AS ultimo_parto,
                 SUM(CASE WHEN e.Calf2 IS NOT NULL THEN 1 ELSE 0 END) AS mellizos,
                 SUM(CASE WHEN e.Calf1 IS NULL THEN 1 ELSE 0 END) AS sin_ternero
          FROM AbstractAnimalEvent a
          JOIN EventCalving e ON e.OID = a.OID
          WHERE a.GCRecord IS NULL
          GROUP BY a.BasicAnimal
        ),
        celos AS (
          SELECT a.BasicAnimal AS animal, COUNT(*) AS n
          FROM AbstractAnimalEvent a
          JOIN EventHeat e ON e.OID = a.OID
          WHERE a.GCRecord IS NULL
          GROUP BY a.BasicAnimal
        ),
        insem AS (
          SELECT a.BasicAnimal AS animal, COUNT(*) AS n_serv,
                 -- Servicios que terminaron en preñez, y en qué número de
                 -- servicio: es la medida directa de fertilidad.
                 SUM(CASE WHEN e.ConceptionDate IS NOT NULL THEN 1 ELSE 0 END) AS n_conc,
                 AVG(CASE WHEN e.ConceptionDate IS NOT NULL
                          THEN CAST(e.InseminationNo AS float) END) AS serv_x_conc
          FROM AbstractAnimalEvent a
          JOIN EventInsemination e ON e.OID = a.OID
          WHERE a.GCRecord IS NULL
          GROUP BY a.BasicAnimal
        )
        SELECT b.Number AS rp,
               CONVERT(varchar(10), b.BirthDate, 120) AS nacimiento,
               CONVERT(varchar(10), b.ExitDate, 120) AS salida,
               r.LactationNumber AS lactancia,
               CAST(DATEDIFF(day, b.BirthDate, COALESCE(b.ExitDate, GETDATE())) / 365.25
                    AS decimal(6,2)) AS anios_vida,
               -- Años EXPUESTOS a enfermarse: desde los 2 años de edad. Con
               -- piso, porque una vaca recién entrada daría una tasa absurda.
               CAST(DATEDIFF(day, DATEADD(year, {EDAD_INICIO_ANIOS}, b.BirthDate),
                             COALESCE(b.ExitDate, GETDATE())) / 365.25
                    AS decimal(6,2)) AS anios_expuesta,
               ISNULL(p.n_partos, 0) AS n_partos,
               CONVERT(varchar(10), p.primer_parto, 120) AS primer_parto,
               CONVERT(varchar(10), p.ultimo_parto, 120) AS ultimo_parto,
               ISNULL(p.mellizos, 0) AS mellizos,
               ISNULL(p.sin_ternero, 0) AS partos_sin_ternero,
               DATEDIFF(month, b.BirthDate, p.primer_parto) AS meses_primer_parto,
               -- Intervalo entre partos PROMEDIO de su vida: del primero al
               -- último dividido por los intervalos que hubo.
               CASE WHEN p.n_partos > 1
                    THEN DATEDIFF(day, p.primer_parto, p.ultimo_parto) / (p.n_partos - 1)
                    END AS iep,
               ISNULL(c.n, 0) AS n_celos,
               ISNULL(i.n_serv, 0) AS n_servicios,
               ISNULL(i.n_conc, 0) AS n_concepciones,
               CAST(i.serv_x_conc AS decimal(5,2)) AS serv_x_conc,
               {', '.join(casos)}
        FROM BasicAnimal b
        LEFT JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL
        LEFT JOIN partos p ON p.animal = b.OID
        LEFT JOIN celos c ON c.animal = b.OID
        LEFT JOIN insem i ON i.animal = b.OID
        LEFT JOIN ev ON ev.animal = b.OID
        WHERE b.GCRecord IS NULL AND b.Number > 0 AND b.BirthDate IS NOT NULL
          AND {rebano.filtro('b', herd)}
        GROUP BY b.Number, b.BirthDate, b.ExitDate, r.LactationNumber,
                 p.n_partos, p.primer_parto, p.ultimo_parto, p.mellizos,
                 p.sin_ternero, c.n, i.n_serv, i.n_conc, i.serv_x_conc
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 30)
    """


def sql_produccion(herd=None) -> str:
    """(rp, lactancia, kg_dia, dias) — una fila por animal y por lactancia.

    Por LACTANCIA y no por animal porque es contra las vacas de su misma
    lactancia que hay que comparar: en este rodeo L1 promedia 35,8 litros/día y
    L4 46,4, así que un ranking sin separar por lactancia ordena por edad.
    """
    return f"""
        SELECT b.Number AS rp, ad.LactationNumber AS lactancia,
               CAST(AVG(CAST(ad.TotalYield AS float)) AS decimal(6,2)) AS kg_dia,
               COUNT(*) AS dias,
               CAST(MAX(ad.TotalYield) AS decimal(6,2)) AS kg_dia_max
        FROM AnimalDaily ad
        JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
        WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1
          AND ad.TotalYield > 0 AND ad.TotalYield < {KG_DIA_MAX_PLAUSIBLE}
          AND ad.LactationNumber IS NOT NULL AND ad.LactationNumber > 0
          AND {rebano.filtro('b', herd)}
        GROUP BY b.Number, ad.LactationNumber
        HAVING COUNT(*) >= {DIAS_MIN_LACTANCIA}
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 30)
    """


# --- Cálculo -----------------------------------------------------------------

def _percentil(valor, ordenados: list) -> float:
    """Posición de `valor` en `ordenados` (ascendente), 0-100. Con menos de dos
    puntos de comparación devuelve 50: decir "es la mejor" con N=1 es inventar."""
    n = len(ordenados)
    if n <= 1:
        return 50.0
    menores = sum(1 for v in ordenados if v < valor)
    iguales = sum(1 for v in ordenados if v == valor)
    return 100.0 * (menores + (iguales - 1) / 2.0) / (n - 1)


def _tramo(valor, ideal, malo) -> float:
    """0-100 lineal entre `ideal` (=100) y `malo` (=0), recortado.

    Se usa donde hay una referencia biológica —intervalo entre partos, edad al
    primer parto— en vez del percentil: ahí no interesa el ranking sino la
    distancia a lo que corresponde. Un rodeo entero con mal intervalo no debería
    dar percentiles cómodos.
    """
    if valor is None:
        return None
    if malo == ideal:
        return 50.0
    v = 100.0 * (malo - valor) / (malo - ideal)
    return max(0.0, min(100.0, v))


def _filas(data) -> list:
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{c: f[i] for c, i in idx.items()} for f in data["rows"]]


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def preparar(vida_data, prod_data) -> dict:
    """Precalcula el contexto del rodeo: las distribuciones contra las que se
    posiciona cada animal. Se hace UNA vez y sirve para todos los animales, que
    es lo que permite cachearlo (ver app.py)."""
    vida = {int(f["rp"]): f for f in _filas(vida_data) if f["rp"] is not None}
    prod = {}
    for f in _filas(prod_data):
        prod.setdefault(int(f["rp"]), {})[int(f["lactancia"])] = f

    # Distribución de kg/día POR LACTANCIA.
    por_lact = {}
    for rp, lacts in prod.items():
        for lact, f in lacts.items():
            por_lact.setdefault(lact, []).append(_num(f["kg_dia"]))
    for lact in por_lact:
        por_lact[lact] = sorted(v for v in por_lact[lact] if v is not None)

    # Distribución de la tasa de enfermedad y de la longevidad, sobre los
    # animales que tienen exposición suficiente para que la tasa signifique algo.
    tasas, longev = [], []
    for rp, f in vida.items():
        t = _tasa_enfermedad(f)
        if t is not None:
            tasas.append(t)
        lg = _longevidad_bruta(f)
        if lg is not None:
            longev.append(lg)
    return {"vida": vida, "prod": prod, "por_lact": por_lact,
            "tasas": sorted(tasas), "longev": sorted(longev),
            "animales": len(vida)}


def _tasa_enfermedad(f: dict):
    """Episodios ponderados por año de exposición. None = no se puede calcular."""
    anios = _num(f.get("anios_expuesta"))
    if anios is None or anios < ANIOS_MIN:
        return None
    total = 0.0
    for clave, cfg in ENFERMEDADES.items():
        total += cfg["peso"] * (int(f.get("enf_" + clave) or 0))
    return total / max(anios, ANIOS_MIN)


def _longevidad_bruta(f: dict):
    """Lactancias por año de vida a partir de los 2 años. Mide si la vaca
    SOSTUVO su carrera, no cuánto vivió: una vaca de 8 años con 2 lactancias
    ocupó lugar sin producir."""
    anios = _num(f.get("anios_expuesta"))
    n = int(f.get("n_partos") or 0)
    if anios is None or anios < ANIOS_MIN or not n:
        return None
    return n / max(anios, ANIOS_MIN)


def _combinar(ejes: dict) -> tuple:
    """Combina los ejes disponibles renormalizando los pesos.

    UN EJE SECUNDARIO QUE NO SE PUEDE MEDIR SE EXCLUYE Y SU PESO SE REPARTE. No
    se cuenta como cero: una vaquillona sin partos no tiene fertilidad "mala",
    tiene fertilidad desconocida, y ponerle 0 la mandaría al fondo del ranking
    por no haber tenido tiempo.

    PERO SIN EL EJE OBLIGATORIO NO HAY ÍNDICE (ver `EJE_OBLIGATORIO`): con la
    redistribución sola, un animal sin producción quedaba puntuado por lo único
    que tenía y podía ganarle a una vaca que produce.
    """
    usables = {k: v for k, v in ejes.items() if v is not None}
    if not usables or ejes.get(EJE_OBLIGATORIO) is None:
        return None, 0.0, {}
    total = sum(PESOS[k] for k in usables)
    aporte = {k: round(PESOS[k] * 100.0 / total, 1) for k in usables}
    score = sum(PESOS[k] * v for k, v in usables.items()) / total
    return round(score, 1), total, aporte


def de_animal(rp: int, ctx: dict) -> dict | None:
    """Índice de mérito de un animal y su desglose. None = el animal no está."""
    f = ctx["vida"].get(int(rp))
    if not f:
        return None
    lacts = ctx["prod"].get(int(rp)) or {}
    detalle = {}

    # --- Producción: percentil dentro de SU MISMA lactancia -----------------
    # Se toma su MEJOR lactancia con dato suficiente: es lo que la vaca demostró
    # que puede. Usar la actual castigaría a una vaca recién parida por estar en
    # el arranque de la curva.
    eje_prod, pd = None, None
    if lacts:
        cands = []
        for lact, fila in lacts.items():
            kg = _num(fila["kg_dia"])
            dist = ctx["por_lact"].get(lact) or []
            if kg is None or len(dist) < 2:
                continue
            cands.append((_percentil(kg, dist), lact, kg, int(fila["dias"]), len(dist)))
        if cands:
            p, lact, kg, dias, n = max(cands)
            eje_prod = p
            pd = {"percentil": round(p, 1), "lactancia": lact, "kg_dia": round(kg, 1),
                  "dias": dias, "pares": n,
                  "texto": (f"{kg:.1f} litros/día en su lactancia {lact}: mejor que el "
                            f"{round(p)}% de las {n} vacas del rodeo que hicieron esa "
                            f"misma lactancia ({dias} días medidos).")}
    detalle["produccion"] = pd or {
        "texto": ("Sin ninguna lactancia con al menos "
                  f"{DIAS_MIN_LACTANCIA} días de producción medidos.")}

    # --- Sanidad: tasa por año de exposición, invertida --------------------
    eje_san, sd = None, None
    tasa = _tasa_enfermedad(f)
    if tasa is not None and len(ctx["tasas"]) > 1:
        # Más enfermedades es peor: el percentil se invierte para que el eje se
        # lea como los demás (más alto = mejor).
        eje_san = 100.0 - _percentil(tasa, ctx["tasas"])
        episodios = {cfg["label"]: int(f.get("enf_" + k) or 0)
                     for k, cfg in ENFERMEDADES.items() if int(f.get("enf_" + k) or 0)}
        anios = _num(f.get("anios_expuesta"))
        sd = {"percentil": round(eje_san, 1), "tasa": round(tasa, 3),
              "anios_expuesta": anios, "episodios": episodios,
              "texto": (("Sin episodios registrados en "
                         f"{anios:.1f} años de exposición: "
                         f"mejor que el {round(eje_san)}% del rodeo.")
                        if not episodios else
                        (", ".join(f"{v} {k.lower()}" for k, v in episodios.items())
                         + f" en {anios:.1f} años"
                         + f" — mejor que el {round(eje_san)}% del rodeo."))}
    detalle["sanidad"] = sd or {
        "texto": (f"Todavía no cumplió {ANIOS_MIN} año(s) desde los "
                  f"{EDAD_INICIO_ANIOS} de edad: no hay exposición suficiente "
                  "para que una tasa de enfermedad signifique algo.")}

    # --- Fertilidad: tres señales con referencia biológica -----------------
    partes, textos = [], []
    n_partos = int(f.get("n_partos") or 0)
    iep = _num(f.get("iep"))
    if n_partos > 1 and iep is not None and iep >= IEP_MIN_PLAUSIBLE:
        v = _tramo(iep, IEP_IDEAL, IEP_MALO)
        partes.append(("iep", v, 0.45))
        textos.append(f"intervalo entre partos de {round(iep)} días "
                      f"(lo bueno es {IEP_IDEAL} o menos)")
    elif n_partos > 1 and iep is not None:
        # Por debajo de 300 días no es una virtud: es un aborto o un parto mal
        # cargado. No se premia ni se castiga, se informa.
        textos.append(f"intervalo entre partos de {round(iep)} días, "
                      f"por debajo de lo biológicamente posible: no se puntúa")
    epp = _num(f.get("meses_primer_parto"))
    if epp is not None:
        v = _tramo(epp, EPP_IDEAL, EPP_MALO)
        partes.append(("primer_parto", v, 0.30))
        textos.append(f"primer parto a los {round(epp)} meses "
                      f"(la referencia es {EPP_IDEAL})")
    sxc = _num(f.get("serv_x_conc"))
    if sxc:
        # 1 servicio por preñez = 100; 5 o más = 0.
        v = _tramo(sxc, 1.0, 5.0)
        partes.append(("serv_x_conc", v, 0.25))
        textos.append(f"{sxc:.1f} servicios por preñez")
    eje_fer, fd = None, None
    if partes:
        peso = sum(p[2] for p in partes)
        eje_fer = sum(v * w for _, v, w in partes) / peso
        fd = {"percentil": round(eje_fer, 1),
              "componentes": {k: round(v, 1) for k, v, _ in partes},
              "n_celos": int(f.get("n_celos") or 0),
              "n_servicios": int(f.get("n_servicios") or 0),
              "n_partos": n_partos,
              "texto": "Se midió con " + "; ".join(textos) + "."}
    detalle["fertilidad"] = fd or {
        "texto": ("Todavía no parió ni tiene servicios efectivos: no hay con qué "
                  "medir fertilidad. No cuenta como mala, se excluye del índice.")}

    # --- Longevidad --------------------------------------------------------
    eje_lon, ld = None, None
    lg = _longevidad_bruta(f)
    if lg is not None and len(ctx["longev"]) > 1:
        eje_lon = _percentil(lg, ctx["longev"])
        ld = {"percentil": round(eje_lon, 1), "lactancias_por_anio": round(lg, 2),
              "n_partos": n_partos, "anios_vida": _num(f.get("anios_vida")),
              "texto": (f"{n_partos} parto(s) en {_num(f.get('anios_expuesta')):.1f} años "
                        f"de carrera ({lg:.2f} por año): mejor que el "
                        f"{round(eje_lon)}% del rodeo.")}
    detalle["longevidad"] = ld or {
        "texto": "Sin partos todavía: no hay carrera que medir."}

    ejes = {"produccion": eje_prod, "sanidad": eje_san,
            "fertilidad": eje_fer, "longevidad": eje_lon}
    score, peso_usado, aporte = _combinar(ejes)
    n_ejes = sum(1 for v in ejes.values() if v is not None)

    # Por qué no hay índice, cuando no hay. El motivo importa: «todavía no
    # produjo» manda al panel de Herencia; «se fue del rodeo antes de que hubiera
    # datos» no manda a ninguna parte.
    sin_indice = None
    if score is None:
        sin_indice = (
            "Este animal todavía no tiene historia productiva medida, y sin eso no "
            "hay índice de mérito: el mérito de una vaca de tambo es producir. "
            "Puntuarla por lo poco que sí se puede medir la premiaría por no haber "
            "tenido oportunidad de fallar. Para un animal sin lactancias, el "
            "estimador que corresponde es el pedigrí — está en el panel de Herencia."
            if ejes.get(EJE_OBLIGATORIO) is None else
            "No se pudo medir ningún eje.")

    return {
        "rp": int(rp),
        "score": score,
        "sin_indice": sin_indice,
        # Un índice armado con poca información no se compara con otro: la
        # pantalla lo marca y la tabla no debería ordenar por él.
        "comparable": bool(score is not None
                            and 100 * peso_usado / sum(PESOS.values()) >= PESO_MIN_COMPARABLE),
        "ejes": {k: (round(v, 1) if v is not None else None) for k, v in ejes.items()},
        "pesos": dict(PESOS),
        # Con qué peso entró cada eje DE VERDAD: si se excluyó alguno, los otros
        # pesan más que lo que dice `pesos`, y eso hay que poder verlo.
        "aporte_real": aporte,
        "detalle": detalle,
        # Sin índice la confianza no es "muy baja": es que no hay índice. Decir
        # "1 de 4 ejes, 0% del peso" al mismo tiempo se contradice.
        "confianza": (CONFIANZA.get(n_ejes, "sin datos") if score is not None
                      else "sin índice"),
        "ejes_medidos": n_ejes,
        "ejes_totales": len(PESOS),
        "peso_cubierto": round(100 * peso_usado / sum(PESOS.values())),
        "rodeo": ctx["animales"],
        # Datos de contexto que no puntúan pero se muestran.
        "vida": {
            "nacimiento": f.get("nacimiento"), "salida": f.get("salida"),
            "anios_vida": _num(f.get("anios_vida")),
            "lactancia": f.get("lactancia"),
            "n_partos": n_partos,
            "primer_parto": f.get("primer_parto"),
            "ultimo_parto": f.get("ultimo_parto"),
            "mellizos": int(f.get("mellizos") or 0),
            "partos_sin_ternero": int(f.get("partos_sin_ternero") or 0),
            "n_celos": int(f.get("n_celos") or 0),
            "n_servicios": int(f.get("n_servicios") or 0),
            "n_concepciones": int(f.get("n_concepciones") or 0),
        },
    }


def escala_rodeo(ctx: dict) -> dict:
    """Distribución del índice en el rodeo, para ubicar un animal sin tener que
    calcular los 4.000. Se calculan todos una vez y se resumen en cuartiles."""
    todos = []
    for rp in ctx["vida"]:
        d = de_animal(rp, ctx)
        if d and d["score"] is not None:
            todos.append(d["score"])
    todos.sort()
    if not todos:
        return {"n": 0}

    def pct(p):
        return todos[min(len(todos) - 1, max(0, int(round(p / 100 * (len(todos) - 1)))))]

    return {"n": len(todos), "p10": pct(10), "p25": pct(25), "p50": pct(50),
            "p75": pct(75), "p90": pct(90)}
