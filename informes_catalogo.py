"""Catálogo de campos de los informes personalizables (nivel «Animal»).

Cada campo es una pieza ya VERIFICADA de lo que DelPro muestra: lo que no se compara contra un informe de DelPro del mismo
momento no entra acá. Los identificadores siguen al `Name` que DelPro usa en `DDMReportItem` (`CurrentDIM`,
`CurrentLactationNumber`...): así más adelante se podrán importar los informes que el tambo ya tiene armados.

Un campo apunta a un BLOQUE de datos: un LEFT JOIN a una subconsulta ya agregada por animal. La consulta solo une los bloques que
las columnas pedidas necesitan, así un informe de pocas columnas es barato. Ver `informes_motor.compilar`.

El usuario nunca escribe SQL: elige campos de esta lista cerrada.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Campo:
    id: str                   # estable: lo guardan los informes
    etiqueta: str             # en español, como lo llama DelPro
    categoria: str            # el árbol de campos
    tipo: str                 # 'num' | 'txt' | 'fecha'
    expr: str                 # expresión SQL sobre los alias del núcleo y del bloque
    bloque: str = ""          # bloque de datos que necesita ('' = el núcleo, siempre disponible)
    unidad: str = ""
    decimales: int = 0
    resumenes: tuple = ()     # resúmenes admitidos por columna (Fase 2)
    verificado: bool = True   # False = definido por lectura de DelPro pero SIN comparar contra un informe suyo del mismo momento
    nota: str = ""            # cómo se verificó, o qué falta
    agrupable: bool = False   # se puede agrupar por este campo: pocas categorías (rodeo, lactancia, estado), no un valor continuo
    bloques: tuple = ()       # bloques ADEMÁS de `bloque` (un campo calculado usa los de todos los campos de su fórmula)
    orden_expr: str = ""      # cómo se ORDENA, si no es la misma expresión con la que se muestra (el Número sale como texto y se ordena como número)
    valores: tuple = ()       # un campo de texto CERRADO (el estado de la reproducción): los únicos valores posibles. La IA los ve y se le rechaza otro


@dataclass(frozen=True)
class Bloque:
    id: str
    join: str                 # un LEFT JOIN completo; usa el alias `b` de BasicAnimal
    requiere: tuple = ()      # bloques que tienen que ir antes (los usa por su alias)


# El núcleo del animal: el grupo (rodeo de HOY, que es lo que usa el informe de DelPro: ver CLAUDE.md, «Los DOS criterios de
# rodeo»). `AnimalGroup` y `AbstractGroup` comparten OID.
BLOQUES_NUCLEO = {
    "grupo": Bloque("grupo", "LEFT JOIN AnimalGroup g ON g.OID = b.[Group] "
                             "LEFT JOIN AbstractGroup ag ON ag.OID = g.OID AND ag.GCRecord IS NULL"),
}

# `@HOY` = el «hoy» de los datos (ver informes_motor.compilar). Las ventanas «últimos 7 días» son [hoy-7, hoy), como el
# `Last7Days() ... < DDMToday()` de DelPro.
BLOQUES: dict = {
    # Estado reproductivo y lactancia actual: una fila por animal.
    "repro": Bloque("repro", "LEFT JOIN AnimalReproductionInfo r ON r.Animal = b.OID AND r.GCRecord IS NULL"),
    # Promedio de los 7 días anteriores: solo días con leche y con el dato válido (IsYieldValid), igual que DelPro.
    "prom7": Bloque("prom7",
                    "LEFT JOIN (SELECT d.BasicAnimal AS animal, AVG(d.TotalYield) AS prom7 FROM AnimalDaily d "
                    "WHERE d.GCRecord IS NULL AND d.Date >= DATEADD(day, -7, @HOY) AND d.Date < @HOY "
                    "AND d.TotalYield > 0 AND d.IsYieldValid = 1 GROUP BY d.BasicAnimal) p7 ON p7.animal = b.OID"),
    # Fecha del último parto: el último evento de parto, que DelPro guarda en su índice por animal (Animal.LatestHistoryIndex.Calving.EventDate). Antes se calculaba agrupando TODOS los
    # eventos de parto de la base (`MAX(...) GROUP BY`): en un SQL Express con poca memoria quedaba esperando el permiso de memoria por minutos, y era lo único pesado del catálogo.
    # Medido en la copia local (30/07/2026): el índice coincide con ese máximo en 1.772 de 1.772 animales con parto, sin ninguna diferencia.
    "parto": Bloque("parto", "LEFT JOIN AbstractAnimalEvent pa ON pa.OID = ix.Calving AND pa.GCRecord IS NULL", requiere=("indice",)),
    # La lactancia ANTERIOR a la actual (pico y días al pico).
    "lactprev": Bloque("lactprev",
                       "LEFT JOIN AnimalLactationSummary sp ON sp.Animal = b.OID AND sp.GCRecord IS NULL "
                       "AND sp.LactationNumber = r.LactationNumber - 1", requiere=("repro",)),
    # El índice de ÚLTIMOS EVENTOS de cada animal: DelPro guarda ahí, por tipo de evento, cuál fue el último (`Animal.LatestHistoryIndex.<Evento>`). Es de donde salen sus campos
    # «última inseminación» y «último aborto». Medido en la copia local (30/07/2026): el índice de inseminación se REINICIA con cada parto (queda vacío en 165 de 165 vacas cuya última
    # inseminación es anterior al último parto) y el de aborto y el de parto coinciden con el máximo de sus eventos en el 100 %.
    "indice": Bloque("indice", "LEFT JOIN AnimalLatestHistoryIndex ix ON ix.Animal = b.OID AND ix.GCRecord IS NULL"),
    "ult_insem": Bloque("ult_insem", "LEFT JOIN AbstractAnimalEvent ei ON ei.OID = ix.Insemination AND ei.GCRecord IS NULL", requiere=("indice",)),
    "ult_aborto": Bloque("ult_aborto", "LEFT JOIN AbstractAnimalEvent ea ON ea.OID = ix.Abortion AND ea.GCRecord IS NULL", requiere=("indice",)),
    "insem_efectiva": Bloque("insem_efectiva", "LEFT JOIN AbstractAnimalEvent ee ON ee.OID = ix.EffectiveInsemination AND ee.GCRecord IS NULL", requiere=("indice",)),
    # Los días de gestación (código 10) y de secado (código 7) que el tambo tiene configurados en DelPro (`ReproductionSetting`, ver `parametros.py`): no se escriben a mano acá.
    "ajustes": Bloque("ajustes", "LEFT JOIN (SELECT MAX(CASE WHEN Parameter = 10 THEN ValueInDays END) AS gest, MAX(CASE WHEN Parameter = 7 THEN ValueInDays END) AS secado "
                                 "FROM ReproductionSetting WHERE GCRecord IS NULL) rs ON 1 = 1"),
    # Cuántas inseminaciones lleva la lactancia en curso: el resumen de lactancia que DelPro guarda por animal (`HistoryAnimalLactationInfo.InsemCount`).
    "insem_lact": Bloque("insem_lact", "LEFT JOIN HistoryAnimalLactationInfo hl ON hl.Animal = b.OID AND hl.LactationNumber = r.LactationNumber", requiere=("repro",)),
}

_GEN = "Generalidades de animal"
_ORD = "Ordeño"
_REP = "Reproducción"

# Un «sí/no» de DelPro es un texto con la lista cerrada («No», «Sí»): se agrupa, se cuenta, sale en el Excel y en el mensaje sin tocar nada más, y la IA y el importador de DelPro
# ya saben qué valores admite (los filtros de DelPro dicen `IsPregnant = True`). Una columna que falta (un animal sin reproducción) es «No», como en DelPro.
SI_NO = ("No", "Sí")


def _si_no(condicion: str) -> str:
    return f"CASE WHEN {condicion} THEN 'Sí' ELSE 'No' END"


# El parto esperado: la inseminación efectiva más los días de gestación del tambo, solo de una vaca preñada y con una inseminación posterior a su último parto (una anterior es un dato
# viejo que quedó colgado). Es lo mismo que hacen «Partos y secados» y la proyección (`partos_secados.py`, `proyeccion.py`).
_PRENEZ_VIGENTE = "r.IsPregnant = 1 AND ee.DateAndTime IS NOT NULL AND (r.LastLactationChangeDate IS NULL OR ee.DateAndTime > r.LastLactationChangeDate)"
_PARTO_ESPERADO = f"CASE WHEN {_PRENEZ_VIGENTE} THEN DATEADD(day, ISNULL(rs.gest, 280), ee.DateAndTime) END"
_SECADO_ESPERADO = f"DATEADD(day, -ISNULL(rs.secado, 50), {_PARTO_ESPERADO})"
_ADEMAS_PARTO = ("insem_efectiva", "ajustes")                 # además del bloque `repro`, que es el principal de estos campos


def _bloque_promedio(n: int) -> Bloque:
    """El promedio de los `n` días anteriores a «Calcular al»: igual que el de 7 días (solo días con leche y con el total válido), con otra ventana."""
    return Bloque(f"prom{n}", f"LEFT JOIN (SELECT d.BasicAnimal AS animal, AVG(d.TotalYield) AS prom{n} FROM AnimalDaily d "
                              f"WHERE d.GCRecord IS NULL AND d.Date >= DATEADD(day, -{n}, @HOY) AND d.Date < @HOY "
                              f"AND d.TotalYield > 0 AND d.IsYieldValid = 1 GROUP BY d.BasicAnimal) p{n} ON p{n}.animal = b.OID")


# Tanda 2. Costo medido con la base real (muestra de 600 animales): el último recuento de células, 3.600 lecturas y 72 ms; los punteros del índice, nada más que un join por clave.
# El último «corte» de la puerta de separación y el último diagnóstico recorren 80 y 340 páginas de eventos por animal: no entraron.
BLOQUES.update({b.id: b for b in (_bloque_promedio(n) for n in (14, 21, 30))})
BLOQUES.update({
    "ult_control": Bloque("ult_control", "LEFT JOIN AbstractAnimalEvent ep ON ep.OID = ix.PregCheck AND ep.GCRecord IS NULL", requiere=("indice",)),
    "ult_descarte": Bloque("ult_descarte", "LEFT JOIN AbstractAnimalEvent ed ON ed.OID = ix.CullDecision AND ed.GCRecord IS NULL", requiere=("indice",)),
    # La leche de UN día: `AnimalDaily` tiene una fila por animal y día (medido: 1.667, 1.680 y 1.665 filas para 1.667, 1.680 y 1.665 animales), así que se une directo.
    "leche_ayer": Bloque("leche_ayer", "LEFT JOIN AnimalDaily ly ON ly.BasicAnimal = b.OID AND ly.GCRecord IS NULL AND ly.Date = DATEADD(day, -1, @HOY)"),
    "leche_hoy": Bloque("leche_hoy", "LEFT JOIN AnimalDaily lh ON lh.BasicAnimal = b.OID AND lh.GCRecord IS NULL AND lh.Date = @HOY"),
    # El ÚLTIMO control lechero del animal: se busca por animal (una fila), sin agrupar la tabla entera. Los controles cuelgan de `AnimalHistoricalData` (ver CLAUDE.md, «Trampas del esquema»).
    "ult_scc": Bloque("ult_scc", "OUTER APPLY (SELECT TOP 1 m.SCC AS scc FROM AnimalHistoricalData h JOIN MilkTest m ON m.OID = h.OID "
                                 "WHERE h.BasicAnimal = b.OID AND h.GCRecord IS NULL ORDER BY h.DateAndTime DESC) sc"),
})

CATALOGO = {c.id: c for c in (
    Campo("Number", "Número del animal", _GEN, "txt", "CAST(b.Number AS nvarchar(20))", orden_expr="b.Number",
          nota="definición de DelPro: Number; se ordena como número (1, 2, 10), no como texto (1, 10, 2)"),
    Campo("AnimalGroupName", "Nombre del grupo", _GEN, "txt", "ag.Name", bloque="grupo", agrupable=True,
          nota="definición de DelPro: Group.GroupName (el rodeo de HOY)"),
    Campo("AnimalGroupNumber", "Número del grupo", _GEN, "num", "ag.Number", bloque="grupo", agrupable=True),
    Campo("CurrentLactationNumber", "Nº Lactación", _GEN, "num", "r.LactationNumber", bloque="repro", agrupable=True,
          nota="1665 de 1665 vacas coinciden con AnimalDaily.LactationNumber (30/07/2026)"),
    Campo("CurrentDIM", "Días en ordeño", _GEN, "num", "DATEDIFF(day, r.LastLactationChangeDate, @HOY)", bloque="repro",
          nota="1664 de 1665 coinciden con AnimalDaily.DIM de DelPro (30/07/2026); falta contra un informe de DelPro"),
    Campo("LastCalvingDate", "Fecha del último parto", _REP, "fecha", "pa.DateAndTime", bloque="parto",
          nota="el último parto del índice de DelPro (coincide con el máximo de los EventCalving en 1.772 de 1.772 animales); difiere de AnimalReproductionInfo.LastLactationChangeDate en ~2% de los animales"),
    Campo("CurrentReproductionStatus", "Estado de la reproducción", _REP, "txt",
          "CASE WHEN r.IsDryingOff = 1 THEN 'Seca' WHEN r.IsPregnant = 1 THEN 'Preñada' "
          "WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN NULL ELSE 'Abierta' END",
          bloque="repro", verificado=False, agrupable=True, valores=("Abierta", "Inseminada", "Preñada", "Seca"),
          nota="por los bits de AnimalReproductionInfo; los códigos de BreedingState 1, 3 y 5 no tienen nombre en la base"),
    Campo("SevenDayAverage", "Promedio producción diaria de leche 7 d.", _ORD, "num", "p7.prom7", bloque="prom7",
          unidad="kg", decimales=1,
          nota="1615 de 1615 coinciden con AnimalDaily.AvgYieldPrev7d de DelPro (30/07/2026)"),
    Campo("DaysToPeakLastLactation", "Días al pico en la última lactación", _ORD, "num", "sp.DaysToPeak",
          bloque="lactprev",
          nota="40 de 40 coinciden con dbo.GetDaysToPeakInLactation de DelPro; solo hay datos desde dic-2025"),
    Campo("PeakYieldLastLactation", "Pico en la última lactación", _ORD, "num", "sp.PeakYield", bloque="lactprev",
          unidad="kg", decimales=1, verificado=False,
          nota="de AnimalLactationSummary; la función de DelPro (GetPeakYieldInLactation) no existe en esta base"),

    # ---- Tanda 1 de campos de DelPro (24/11/2026): los «sí/no» de la reproducción, sus fechas y días, y la edad. Cada uno lleva el id que DelPro usa en `DDMReportItem`.
    Campo("InMilk", "En ordeño", _ORD, "txt", _si_no("r.LactationNumber > 0 AND r.IsDryingOff = 0"), bloque="repro", verificado=False, agrupable=True, valores=SI_NO,
          nota="lactancia en curso y no seca (DelPro la filtra así: LactationNumber > 0 AND IsDryingOff = False). Coincide con la leche medida el 29/07/2026: las 1.636 vacas que dieron leche "
               "figuran en ordeño y ninguna de las 3.479 que no figuran dio leche; falta compararla con un informe de DelPro"),
    Campo("ToBeCulled", "Para descarte", _GEN, "txt", _si_no("b.ToBeCulled = 1"), agrupable=True, valores=SI_NO,
          nota="la columna BasicAnimal.ToBeCulled, que es la que DelPro usa en sus filtros (ToBeCulled = False)"),
    Campo("IsPregnant", "Preñada", _REP, "txt", _si_no("r.IsPregnant = 1"), bloque="repro", agrupable=True, valores=SI_NO,
          nota="la columna AnimalReproductionInfo.IsPregnant (DelPro: ReproductionInfo.IsPregnant)"),
    Campo("IsDryingOff", "En secado", _REP, "txt", _si_no("r.IsDryingOff = 1"), bloque="repro", agrupable=True, valores=SI_NO,
          nota="la columna AnimalReproductionInfo.IsDryingOff (DelPro: ReproductionInfo.IsDryingOff); es la vaca seca"),
    Campo("BirthDate", "Fecha de nacimiento", _GEN, "fecha", "b.BirthDate", nota="la columna BasicAnimal.BirthDate"),
    Campo("ExitDate", "Fecha de salida", _GEN, "fecha", "b.ExitDate",
          nota="la columna BasicAnimal.ExitDate; un animal que ya salió solo aparece con «Incluir animales descartados»"),
    Campo("AgeInMonths", "Edad en meses", _GEN, "num", "DATEDIFF(month, b.BirthDate, @HOY) - CASE WHEN DAY(b.BirthDate) > DAY(@HOY) THEN 1 ELSE 0 END", verificado=False,
          nota="meses cumplidos entre el nacimiento y «Calcular al»; la cuenta es nuestra, falta compararla con un informe de DelPro"),
    Campo("LastInseminationDate", "Fecha de la última inseminación", _REP, "fecha", "ei.DateAndTime", bloque="ult_insem",
          nota="el último evento del índice de DelPro (LatestHistoryIndex.Insemination); DelPro lo reinicia con cada parto, así que una vaca que no se inseminó desde su último parto queda sin fecha "
               "(165 de 165 en la copia local)"),
    Campo("LastAbortionDate", "Fecha del último aborto", _REP, "fecha", "ea.DateAndTime", bloque="ult_aborto",
          nota="el último evento del índice de DelPro (LatestHistoryIndex.Abortion); coincide con el último aborto cargado en 91 de 91 animales"),
    Campo("DaysSinceLastInsemination", "Días desde la última inseminación", _REP, "num", "DATEDIFF(day, ei.DateAndTime, @HOY)", bloque="ult_insem", verificado=False,
          nota="días entre la fecha del índice de DelPro y «Calcular al»; la cuenta es nuestra, falta compararla con un informe de DelPro"),
    Campo("NumberOfInseminations", "Cantidad de inseminaciones", _REP, "num", "ISNULL(hl.InsemCount, 0)", bloque="insem_lact",
          nota="las inseminaciones de la lactancia en curso, del resumen de lactancia de DelPro (InsemCount): coincide con contar los eventos en 433 de 433 vacas; donde DelPro no lo cargó "
               "(167 vacas) no tiene ninguna"),
    Campo("ExpectedCalvingDate", "Fecha esperada de parto", _REP, "fecha", _PARTO_ESPERADO, bloque="repro", bloques=_ADEMAS_PARTO, verificado=False,
          nota="la inseminación efectiva más los días de gestación que el tambo tiene en DelPro, solo de una vaca preñada; falta compararla con un informe de DelPro"),
    Campo("DaysToCalving", "Días para el parto", _REP, "num", f"DATEDIFF(day, @HOY, {_PARTO_ESPERADO})", bloque="repro", bloques=_ADEMAS_PARTO, verificado=False,
          nota="días entre «Calcular al» y la fecha esperada de parto (negativo = ya pasó); falta compararlo con un informe de DelPro"),
    Campo("ExpectedDryOffDate", "Fecha esperada de secado", _REP, "fecha", _SECADO_ESPERADO, bloque="repro", bloques=_ADEMAS_PARTO, verificado=False,
          nota="la fecha esperada de parto menos los días de secado que el tambo tiene en DelPro; falta compararla con un informe de DelPro"),
    Campo("DaysToDryOff", "Días para el secado", _REP, "num", f"DATEDIFF(day, @HOY, {_SECADO_ESPERADO})", bloque="repro", bloques=_ADEMAS_PARTO, verificado=False,
          nota="días entre «Calcular al» y la fecha esperada de secado (negativo = ya pasó); falta compararlo con un informe de DelPro"),

    # ---- Tanda 2 (24/11/2026): punteros del índice de DelPro, el último recuento de células y los promedios de 14, 21 y 30 días.
    Campo("EffectiveInseminationDate", "Fecha de la inseminación efectiva", _REP, "fecha", "ee.DateAndTime", bloque="insem_efectiva",
          nota="el evento al que apunta el índice de DelPro (LatestHistoryIndex.EffectiveInsemination): la inseminación con la que quedó preñada; es la que usan «Partos y secados» y la proyección"),
    Campo("LastPregnancyCheckDate", "Fecha del último diagnóstico de gestación", _REP, "fecha", "ep.DateAndTime", bloque="ult_control",
          nota="el evento al que apunta el índice de DelPro (LatestHistoryIndex.PregCheck)"),
    Campo("LastCullDecisionDate", "Fecha de la última decisión de descarte", _GEN, "fecha", "ed.DateAndTime", bloque="ult_descarte", verificado=False,
          nota="la fecha del evento al que apunta el índice de DelPro (LatestHistoryIndex.CullDecision); falta confirmar si DelPro muestra esa fecha o la del descarte previsto"),
    Campo("DaysPregnant", "Días de preñez", _REP, "num", f"CASE WHEN {_PRENEZ_VIGENTE} THEN DATEDIFF(day, ee.DateAndTime, @HOY) END", bloque="repro", bloques=("insem_efectiva",),
          verificado=False, nota="días desde la inseminación efectiva hasta «Calcular al», solo de una vaca preñada; la cuenta es nuestra, falta compararla con un informe de DelPro"),
    Campo("LatestSCC", "Último recuento de células somáticas", _ORD, "num", "sc.scc", bloque="ult_scc", unidad="mil/ml", verificado=False,
          nota="el SCC del último control lechero del animal (MilkTest, en miles de células por ml, como lo guarda DelPro); falta compararlo con un informe de DelPro"),
    # «De ayer» y «de hoy» como los llama DelPro (decisión del usuario, 24/11/2026): ayer es el día anterior a «Calcular al» y hoy, ese día. Quien mira una copia de la base lo sabe.
    Campo("YieldYesterday", "Producción de leche de ayer", _ORD, "num", "ly.TotalYield", bloque="leche_ayer", unidad="kg", decimales=1, verificado=False,
          nota="el total del día anterior a «Calcular al» en AnimalDaily (una fila por animal y día); falta compararlo con un informe de DelPro. Relativo a esa fecha: en una copia de la base es «ayer» de los datos"),
    Campo("YieldToday", "Producción de leche de hoy", _ORD, "num", "lh.TotalYield", bloque="leche_hoy", unidad="kg", decimales=1, verificado=False,
          nota="el total del día de «Calcular al» en AnimalDaily; el día en curso suele estar a medio cargar (DelPro consolida al cierre); falta compararlo con un informe de DelPro"),
    Campo("CMSFourteenDayAverage", "Promedio producción diaria de leche 14 d.", _ORD, "num", "p14.prom14", bloque="prom14", unidad="kg", decimales=1, verificado=False,
          nota="igual que el de 7 días (días con leche y con el total válido, de los 14 anteriores a «Calcular al»); falta compararlo con un informe de DelPro"),
    Campo("CMSTwentyOneDayAverage", "Promedio producción diaria de leche 21 d.", _ORD, "num", "p21.prom21", bloque="prom21", unidad="kg", decimales=1, verificado=False,
          nota="igual que el de 7 días, con 21 días; falta compararlo con un informe de DelPro"),
    Campo("CMSThirtyDayAverage", "Promedio producción diaria de leche 30 d.", _ORD, "num", "p30.prom30", bloque="prom30", unidad="kg", decimales=1, verificado=False,
          nota="igual que el de 7 días, con 30 días; falta compararlo con un informe de DelPro"),
)}
