# -*- coding: utf-8 -*-
"""Riesgo heredado de enfermedad por vaca: mitad padre, mitad madre.

    riesgo_vaca = 0,5 × riesgo_del_PADRE + 0,5 × riesgo_de_la_MADRE

Los pesos siguen la herencia: la vaca recibe la mitad de sus genes de cada
uno. Decisión del tambo (2026-07-29); los abuelos quedan para más adelante
(ver `PESO_ABUELOS` al final).

LAS DOS MITADES NO SON LA MISMA CLASE DE DATO, y conviene tenerlo presente:

  * PADRE — índice GENÉTICO de catálogo (PTA), percentil entre los toros
    cargados. Ver `genetica.py`. Es una estimación de valor genético hecha
    sobre cientos o miles de hijas, así que es confiable... del padre.
  * MADRE — historia FENOTÍPICA real: las enfermedades que tuvo, medidas en
    este tambo. No es su valor genético: es lo que efectivamente le pasó, que
    mezcla genética con ambiente, y viene de UNA sola vaca (n=1), así que como
    estimador de genética es mucho más ruidoso que el PTA del padre.

Por eso el número combinado NO es un valor genético ni una probabilidad de
enfermarse: es un RIESGO HEREDADO relativo, para ordenar. Se sigue usando
como contexto y desempate en el índice de salud, nunca como evidencia de que
la vaca esté enferma hoy (ver `PESO_GENETICA` en salud.py).

Que la madre entre por fenotipo y no por pedigrí fue deliberado: no hay
genotipado cargado en esta base (`PedigreeInfo.GeneticValue` y `.Genotype`
vienen vacíos en las 7.314 vacas activas), y la madre es una vaca del propio
rodeo, sin PTA publicado. Su historia clínica es el mejor dato disponible, y
además está medido acá adentro, sin depender de ningún catálogo externo.

NORMALIZACIÓN POR EXPOSICIÓN — se cuentan enfermedades POR AÑO, no en total.
Sin esto, una madre con cinco lactancias parece peor que una con una sola
solo por haber vivido más, y la longevidad es una virtud, no un defecto:
estaríamos penalizando a las hijas de las mejores vacas del rodeo.
"""
# Reparto entre las dos ramas. Suman 1. Si falta una, la otra se lleva todo
# (ver `de()`): "sin dato" no es riesgo cero.
PESO_PADRE = 0.5
PESO_MADRE = 0.5

# Peso de cada tipo de enfermedad DENTRO de la mitad de la madre. Mastitis
# pesa más porque es la enfermedad que el índice de salud busca predecir
# (y la que tiene el backtest detrás: 568 casos, ver _bt_*.py).
PESOS_ENFERMEDAD = {"mastitis": 0.65, "metritis": 0.35}

# Piso de años de exposición, para no dividir por casi cero en una vaquillona
# recién entrada (que además todavía no tuvo tiempo de enfermarse).
ANIOS_MIN = 0.5

# Edad a la que se considera que arranca la exposición: antes del primer parto
# no está en ordeñe y no puede tener mastitis clínica.
EDAD_INICIO_ANIOS = 2


# QUÉ SE CUENTA COMO ENFERMEDAD: solo "Mastitis 1/4…" y "Metritis…", con el
# MISMO criterio de prefijo que `_bt_01_casos.py` (el script que armó los 568
# casos de mastitis y 79 de metritis del backtest del índice). Los demás
# diagnósticos de DelPro incluyen cosas que no son inicio de enfermedad (Alta,
# Vacía, Desvasado Correctivo) y separarlas bien exige la misma lista de
# exclusión del backtest — se deja para cuando haga falta. La clasificación
# vive dentro de `sql_historia()` porque hay que agrupar en SQL, ver ahí.


def sql_historia() -> str:
    """Una fila por MADRE (vaca que es madre de alguna vaca activa), con sus
    mastitis y metritis contadas y los años de exposición para pasarlo a tasa.

    TRES decisiones que importan, con el porqué:

    * `LEFT JOIN` a los eventos, NO `JOIN`: una madre que nunca se enfermó
      TIENE que aparecer, con los contadores en 0. Con `JOIN` desaparecía, y
      "sana" terminaba leyéndose como "sin dato" -- al revés de lo correcto,
      porque no enfermarse en seis años es información valiosa, no ausencia
      de información.
    * Se agrupa en SQL (no se traen los diagnósticos crudos) para devolver una
      fila por madre en vez de una por evento: son miles de vacas por miles de
      eventos, y así entra cómodo en el tope de filas.
    * Al agrupar hay que clasificar en SQL, y ahí se usa `COLLATE
      Latin1_General_CI_AI` explícito: CI = ignora mayúsculas, AI = ignora
      acentos. Sin eso el resultado depende de la collation de la instalación,
      que es exactamente el supuesto que después falla en otro tambo.

    NO filtra `ExitDate IS NULL`: la madre de una vaca activa puede haberse
    ido del rodeo hace años, y su historia sigue valiendo.
    """
    col = "COLLATE Latin1_General_CI_AI"
    diag = "COALESCE(tn.ItemValue, dg.Description, '')"
    return f"""
        WITH madres AS (
          SELECT DISTINCT TRY_CAST(p.MotherId AS int) AS rp
          FROM BasicAnimal h
          JOIN PedigreeInfo p ON p.OID = h.PedigreeInfo
          WHERE h.GCRecord IS NULL AND h.ExitDate IS NULL AND h.Number > 0
            AND NULLIF(LTRIM(RTRIM(p.MotherId)), '') IS NOT NULL
        )
        SELECT b.Number AS rp,
               CAST(DATEDIFF(day, DATEADD(year, {EDAD_INICIO_ANIOS}, b.BirthDate),
                             COALESCE(b.ExitDate, GETDATE())) / 365.25
                    AS decimal(6,2)) AS anios,
               SUM(CASE WHEN {diag} {col} LIKE 'mastitis 1/4%' THEN 1 ELSE 0 END) AS mastitis,
               SUM(CASE WHEN {diag} {col} LIKE 'metritis%' THEN 1 ELSE 0 END) AS metritis,
               -- Hace cuánto fue el último episodio. No entra al cálculo (la
               -- tasa ya normaliza por tiempo), pero cambia por completo cómo
               -- se lee: "3 mastitis, la última hace 5 años" no dice lo mismo
               -- que "3 mastitis, la última hace 2 meses".
               DATEDIFF(day, MAX(CASE WHEN {diag} {col} LIKE 'mastitis 1/4%'
                                      THEN a.DateAndTime END), GETDATE()) AS dias_ult_mastitis,
               DATEDIFF(day, MAX(CASE WHEN {diag} {col} LIKE 'metritis%'
                                      THEN a.DateAndTime END), GETDATE()) AS dias_ult_metritis
        FROM BasicAnimal b
        JOIN madres md ON md.rp = b.Number
        LEFT JOIN AbstractAnimalEvent a ON a.BasicAnimal = b.OID AND a.GCRecord IS NULL
        LEFT JOIN DiagnosisTreatmentEvent e ON e.OID = a.OID
        LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
        LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
        WHERE b.GCRecord IS NULL AND b.Number > 0 AND b.BirthDate IS NOT NULL
        GROUP BY b.Number, b.BirthDate, b.ExitDate
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 20)
    """


def _percentil(valor, ordenados: list) -> float:
    n = len(ordenados)
    if n <= 1:
        return 50.0
    menores = sum(1 for v in ordenados if v < valor)
    iguales = sum(1 for v in ordenados if v == valor)
    return 100.0 * (menores + (iguales - 1) / 2.0) / (n - 1)


def indice_madres(columns, rows) -> dict:
    """Filas de `sql_historia()` -> {rp_madre: {riesgo 0-100, mastitis,
    metritis, anios, tasa}}.

    El riesgo es el PERCENTIL de la tasa anual de enfermedad entre TODAS las
    madres, incluidas las que nunca se enfermaron. Que las sanas entren al
    ranking es el punto: son la referencia contra la cual una madre con
    mastitis es "peor". Si se rankeara solo entre las enfermas, la más sana de
    las enfermas daría 0 igual que una que nunca se enfermó, y las dos cosas
    no son lo mismo.

    Una madre que no está en este índice (no figura en la base, o sin fecha de
    nacimiento para calcular exposición) NO da riesgo 0: da None, y `de()` le
    pasa todo el peso al padre.
    """
    idx = {c: i for i, c in enumerate(columns)}
    por_rp: dict = {}
    for r in rows:
        rp = r[idx["rp"]]
        if rp is None:
            continue
        anios = max(float(r[idx["anios"]] or 0), ANIOS_MIN)
        mast, metr = int(r[idx["mastitis"]] or 0), int(r[idx["metritis"]] or 0)
        dias = lambda c: (int(r[idx[c]]) if c in idx and r[idx[c]] is not None else None)  # noqa: E731
        por_rp[rp] = {
            "mastitis": mast, "metritis": metr, "anios_expuesta": round(anios, 2),
            "dias_ult_mastitis": dias("dias_ult_mastitis"),
            "dias_ult_metritis": dias("dias_ult_metritis"),
            "tasa": (PESOS_ENFERMEDAD["mastitis"] * mast / anios
                     + PESOS_ENFERMEDAD["metritis"] * metr / anios),
        }

    ordenados = sorted(d["tasa"] for d in por_rp.values())
    for d in por_rp.values():
        d["riesgo"] = round(_percentil(d["tasa"], ordenados), 1)
    return por_rp


def _hace(dias) -> str:
    """"hace 8 meses" / "hace 3 años" — para leer la recencia sin hacer cuentas."""
    if dias is None:
        return ""
    if dias < 45:
        return f"hace {dias} días"
    if dias < 730:
        return f"hace {round(dias / 30)} meses"
    return f"hace {dias / 365.25:.1f} años"


def explicar_madre(m: dict | None, rp=None) -> list:
    """[{label, texto, percentil}] con la historia clínica de la madre, en
    criollo. Devuelve filas incluso cuando NO tuvo enfermedades: "nunca tuvo
    mastitis en 5 años" es un dato a favor y hay que mostrarlo, no omitirlo."""
    if not m:
        return []
    anios = m.get("anios_expuesta")
    filas = []
    # El label NO dice "de la madre": esta función también explica a la ABUELA
    # en el árbol de la ficha (ver `arbol()`), y ahí el parentesco lo pone la
    # caja. Poniéndolo acá el texto quedaba mintiendo un nivel de pedigrí.
    for clave, label in (("mastitis", "Mastitis"), ("metritis", "Metritis")):
        n = m.get(clave) or 0
        ult = m.get("dias_ult_" + clave)
        if n:
            tasa = n / anios if anios else None
            txt = (f"{n} episodio{'s' if n != 1 else ''} en {anios} años"
                   + (f" · {tasa:.2f}/año" if tasa is not None else "")
                   + (f" · último {_hace(ult)}" if ult is not None else ""))
        else:
            txt = f"sin episodios registrados en {anios} años"
        # `detalle` == `texto` acá porque este texto nunca llevó el label
        # adentro; se expone igual para que el frontend use siempre el mismo
        # campo con los dos orígenes (ver `genetica.explicar`).
        filas.append({"label": label, "texto": txt, "detalle": txt, "episodios": n})
    # Redacción por tramos: "peor que el 0%" es correcto pero se lee mal justo
    # en el mejor caso, que es el que más conviene que quede claro.
    p = m.get("riesgo")
    if p is None:
        pos = "sin posición calculada"
    elif p <= 5:
        pos = "entre las madres más sanas del rodeo"
    elif p >= 95:
        pos = "entre las madres con más enfermedades del rodeo"
    else:
        pos = f"peor que el {round(p)}% de las madres del rodeo"
    txt_pos = (f"RP {rp}: " if rp is not None else "") + pos
    filas.append({"label": "Posición en el rodeo", "percentil": p,
                  "texto": txt_pos, "detalle": txt_pos})
    return filas


def de(padre_fn, madres: dict, padre, madre_rp) -> dict | None:
    """Riesgo heredado combinado. None = no se pudo calcular ninguna rama.

    `padre_fn`: `genetica.de_toro`. `madres`: salida de `indice_madres()`.
    Si falta una rama, la otra se lleva TODO el peso (se renormaliza) y queda
    marcado en `ramas`, para que la pantalla pueda decir con qué se calculó.
    """
    r_padre, detalle_padre = None, []
    gen = padre_fn(padre) if (padre_fn and padre) else None
    if gen:
        r_padre = gen.get("riesgo")
        # Detalle rasgo por rasgo, para que la tarjeta pueda explicar POR QUÉ
        # el padre puntúa como puntúa (import perezoso: `genetica` es dueño de
        # los rasgos y sus direcciones, herencia.py solo combina).
        try:
            import genetica
            detalle_padre = genetica.explicar(gen)
        except Exception:  # noqa: BLE001
            detalle_padre = []

    r_madre, madre = None, None
    if madre_rp is not None:
        try:
            madre = madres.get(int(str(madre_rp).strip()))
        except (TypeError, ValueError):
            madre = None
        if madre:
            r_madre = madre.get("riesgo")

    partes = []
    if r_padre is not None:
        partes.append((PESO_PADRE, r_padre))
    if r_madre is not None:
        partes.append((PESO_MADRE, r_madre))
    if not partes:
        return None
    total = sum(p for p, _ in partes)
    riesgo = sum(p * v for p, v in partes) / total
    return {
        "riesgo": round(riesgo, 1),
        "riesgo_padre": r_padre,
        "riesgo_madre": r_madre,
        "padre": padre,
        "madre_rp": madre_rp,
        "madre_mastitis": madre.get("mastitis") if madre else None,
        "madre_metritis": madre.get("metritis") if madre else None,
        "madre_anios": madre.get("anios_expuesta") if madre else None,
        # Detalle explicado de cada rama, para el desplegable de la tarjeta.
        "detalle_padre": detalle_padre,
        "detalle_madre": explicar_madre(madre, madre_rp),
        # Con qué ramas se calculó: "padre+madre", "solo padre", "solo madre".
        "ramas": ("padre+madre" if len(partes) == 2
                  else ("solo padre" if r_padre is not None else "solo madre")),
        "simulado": bool(gen.get("simulado")) if gen else False,
    }


def arbol(pedigri: dict, madres: dict, produccion: dict, padre_fn) -> dict:
    """Árbol de ancestros para la ficha del animal, con el aporte de genes de
    cada uno y lo que se sabe de él.

    EL APORTE NO SE SUMA ENTRE NIVELES. Padre y madre aportan 50% cada uno y
    ahí está el 100%; los abuelos (25% cada uno) NO se agregan encima: están
    ADENTRO de ese 50%, lo explican. Si se sumaran los cinco daría 175%, que es
    el error clásico al mostrar un pedigrí como si fueran aportes acumulables.
    Por eso van en dos niveles separados y la pantalla lo dice.

    Cada ancestro trae de qué CLASE es su dato, que no es lo mismo:
      * toro    -> genético (PTA del catálogo, estimado sobre muchas hijas)
      * vaca    -> fenotípico (lo que realmente le pasó y produjo, n=1)
    """
    def de_toro(nombre, aporte, parentesco):
        t = padre_fn(nombre) if nombre else None
        return {
            "parentesco": parentesco, "clase": "toro", "aporte": aporte,
            "id": nombre, "en_catalogo": bool(t),
            "riesgo": (t or {}).get("riesgo"),
            "simulado": bool((t or {}).get("simulado")),
            "salud": genetica_explicar(t),
            "produccion": (t or {}).get("produccion") or {},
            "motivo": None if t else ("No está en el catálogo de toros."
                                      if nombre else "No cargado en DelPro."),
        }

    def de_vaca(rp, aporte, parentesco, salida=None, extra=None):
        try:
            rp_int = int(str(rp).strip()) if rp is not None else None
        except (TypeError, ValueError):
            rp_int = None
        m = madres.get(rp_int) if rp_int is not None else None
        prod = produccion.get(rp_int) if rp_int is not None else None
        # SIN PRODUCCIÓN NO ES PRODUCCIÓN CERO, y en las abuelas es la regla, no
        # la excepción: casi todas se fueron antes de que la base tenga leche
        # cargada (`AnimalDaily` con producción arranca en diciembre 2025 — ver
        # CLAUDE.md), mientras los EVENTOS sí van más atrás. Por eso una abuela
        # aparece con 12 mastitis y ni un kg: el hueco es del dato, no del
        # animal. Se dice con la fecha de salida, que es un dato, sin afirmar la
        # causa. Sin esta aclaración la pantalla se lee como "no producía".
        nota_prod = None
        if not prod and rp_int is not None:
            nota_prod = ("Sin producción en la base"
                         + (f" (salió del rodeo el {salida})" if salida else "")
                         + ". No significa que no haya producido: el histórico "
                           "de leche no llega tan atrás.")
        d = {
            "parentesco": parentesco, "clase": "vaca", "aporte": aporte,
            "id": rp_int, "en_catalogo": bool(m),
            "riesgo": (m or {}).get("riesgo"),
            "simulado": False,      # el fenotipo siempre es real, es de la base
            "salud": explicar_madre(m, rp_int),
            "produccion": {k: prod.get(k) for k in
                           ("lactancias", "kg_dia_prom", "kg_dia_max")} if prod else {},
            "nota_produccion": nota_prod,
            "motivo": None if (m or prod) else
                      ("Sin historia clínica ni producción registrada."
                       if rp_int is not None else "No cargada en DelPro."),
        }
        if extra:
            d.update(extra)
        return d

    padre = de_toro(pedigri.get("padre"), 50, "Padre")
    madre = de_vaca(pedigri.get("madre_rp"), 50, "Madre",
                    salida=pedigri.get("madre_salida"))
    # Abuelo PATERNO: el padre es un toro, así que su propio padre solo se
    # conoce si el catálogo lo trae (columna `Sire` del Excel).
    t_padre = padre_fn(pedigri.get("padre")) if pedigri.get("padre") else None
    abuelos = [
        de_toro((t_padre or {}).get("sire"), 25, "Abuelo paterno"),
        de_toro(pedigri.get("abuelo_materno"), 25, "Abuelo materno"),
        de_vaca(pedigri.get("abuela_rp"), 25, "Abuela materna",
                salida=pedigri.get("abuela_salida")),
    ]
    con_dato = [a for a in [padre, madre] if a["riesgo"] is not None]
    return {
        "rp": pedigri.get("rp"),
        "padres": [padre, madre],
        "abuelos": abuelos,
        "riesgo_enfermar": (round(sum(a["riesgo"] for a in con_dato) / len(con_dato), 1)
                            if con_dato else None),
        "ramas": (["padre", "madre"] if len(con_dato) == 2
                  else [a["parentesco"].lower() for a in con_dato]),
        "nota_aporte": ("Padre y madre aportan 50% cada uno: ahí está el 100% de la "
                        "genética. Los abuelos (25%) NO se suman a eso — están dentro "
                        "del aporte de cada padre y sirven para explicarlo."),
    }


def genetica_explicar(t):
    """`genetica.explicar(t)` con import perezoso, para que herencia.py no
    dependa de genetica a nivel de módulo (y no romper si falta el Excel)."""
    if not t:
        return []
    try:
        import genetica
        return genetica.explicar(t)
    except Exception:  # noqa: BLE001
        return []


# Los abuelos NO entran al riesgo que usa el índice de salud (decisión del
# tambo: primero padre+madre). En la FICHA del animal sí se muestran, como
# contexto para explicar de dónde viene cada padre — ver `arbol()`.
# Cuando se sumen, el reparto por proporción de genes sería padre 1/2, abuelo
# materno 1/4, abuela materna 1/4 -- pero el abuelo materno se puede sacar YA
# de la base (PedigreeInfo de la madre -> su FatherId, y el Excel de toros
# tiene la columna MGS), mientras la abuela materna necesita otro nivel de
# historia clínica. Ver la conversación del 29/07/2026.
PESO_ABUELOS = 0.0
