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
    tabla: str = ""           # una tabla de DelPro que no tienen todos los tambos (la cámara de condición corporal): el campo solo se ofrece donde existe
    equivale: tuple = ()      # de un campo de texto CERRADO: ((lo que escribe DelPro en un filtro, valor de la lista de acá), ...). DelPro compara con el código («Sex = '2'») o con el nombre
                              # en inglés de una enumeración («LatestPregCheckResult In ('Uncertain', 'Negative')»); el importador lo traduce con esta tabla
    item: str = ""            # el `Name` de DelPro cuando este campo es UNA VARIANTE de un campo con parámetro (id `CMSTakeOffFlowYesterday_2`, item `CMSTakeOffFlowYesterday`); vacío = el id es el `Name`
    parametros: tuple = ()    # lo que fija esa variante, como lo guarda DelPro en `AbstractSetting` (un `ReportItemParameter` por columna): (("MilkingNumber", "2"),). El importador elige la variante
                              # cuyo (item, parámetros) coincide con la columna de DelPro


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
    # Y los de «atención» (códigos 13 y 14: aviso previo al control de gestación y atención antes del secado, con su casilla de activo), que DelPro lee como `Farm.PregCheckBeforeAttention` y `Farm.DryOffBeforeAttention`.
    "ajustes": Bloque("ajustes", "LEFT JOIN (SELECT MAX(CASE WHEN Parameter = 10 THEN ValueInDays END) AS gest, MAX(CASE WHEN Parameter = 7 THEN ValueInDays END) AS secado, "
                                 "MAX(CASE WHEN Parameter = 13 THEN ValueInDays END) AS aviso_diag, MAX(CASE WHEN Parameter = 13 THEN CAST(Active AS int) END) AS aviso_diag_act, "
                                 "MAX(CASE WHEN Parameter = 14 THEN ValueInDays END) AS aviso_secado, MAX(CASE WHEN Parameter = 14 THEN CAST(Active AS int) END) AS aviso_secado_act "
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
# La fórmula de DelPro (`DDMReportItem.Expression`): el parto esperado es de un animal que NO salió (`IsNull(Animal.ExitDate)`), está preñado y tiene inseminación efectiva; el secado esperado,
# solo de una vaca que ya parió (`Animal.Type != 'Heifer'`: en la base todo animal con lactancia es del tipo vaca y ninguno sin lactancia) y que todavía no se secó
# (`IsNull(LastDryOffDate)`: el índice de DelPro guarda el último secado y lo reinicia con cada parto; 0 de las 1.873 vacas en ordeño lo tienen).
_PARTO_ESPERADO = f"CASE WHEN b.ExitDate IS NULL AND {_PRENEZ_VIGENTE} THEN DATEADD(day, ISNULL(rs.gest, 280), ee.DateAndTime) END"
_SECADO_ESPERADO = f"CASE WHEN r.LactationNumber >= 1 AND ix.DryOff IS NULL THEN DATEADD(day, -ISNULL(rs.secado, 50), {_PARTO_ESPERADO}) END"
_ADEMAS_PARTO = ("insem_efectiva", "ajustes")                 # además del bloque `repro`, que es el principal de estos campos
_ADEMAS_SECADO = _ADEMAS_PARTO + ("indice",)


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
    # El ÚLTIMO control lechero que trae cada parámetro (recuento, grasa, proteína, rendimiento), con SU fecha: un control puede traer solo el rendimiento (medido en la copia local: el
    # último control de 26 de 600 vacas no tiene recuento ni grasa) y DelPro guarda una fecha por parámetro. Se busca por animal (una fila), sin agrupar la tabla entera. Los controles
    # cuelgan de `AnimalHistoricalData` (ver CLAUDE.md, «Trampas del esquema»).
    **{f"ult_{nombre}": Bloque(f"ult_{nombre}", f"OUTER APPLY (SELECT TOP 1 m.{col} AS valor, h.DateAndTime AS fecha FROM AnimalHistoricalData h JOIN MilkTest m ON m.OID = h.OID "
                                                f"WHERE h.BasicAnimal = b.OID AND h.GCRecord IS NULL AND m.{col} IS NOT NULL ORDER BY h.DateAndTime DESC) {alias}")
       for nombre, col, alias in (("scc", "SCC", "sc"), ("grasa", "Fat", "gr"), ("proteina", "Protein", "pr"), ("rend", "Yield", "rd"))},
    # El último cambio de rodeo (puntero del índice de DelPro) y el rodeo del que venía.
    "cambio_grupo": Bloque("cambio_grupo", "LEFT JOIN AbstractAnimalEvent eg ON eg.OID = ix.GroupChange AND eg.GCRecord IS NULL LEFT JOIN EventGroupChange egc ON egc.OID = eg.OID "
                                           "LEFT JOIN AbstractGroup gp ON gp.OID = egc.OldGroup AND gp.GCRecord IS NULL", requiere=("indice",)),
    # El resumen de la lactancia ANTERIOR (su inicio es el parto anterior): una fila por (animal, lactancia).
    "lact_ant": Bloque("lact_ant", "LEFT JOIN HistoryAnimalLactationInfo hp ON hp.Animal = b.OID AND hp.LactationNumber = r.LactationNumber - 1", requiere=("repro",)),
    # La cámara de condición corporal (`BcsDailyData`, solo los tambos que la tienen): el último valor (un `TOP 1` por la fecha: busca en el índice y para; 12.475 lecturas y 0,5 s por 1.913 vacas) y los
    # tres que DelPro MARCA (secado, parto y 190 días de preñez). La marca es única por animal (393 de 393, 62 de 62 y 27 de 27 en la base del 20/11/2026, ningún animal con dos), así que `MAX` es la fila
    # marcada. **Se leen con una tabla derivada, no con un `OUTER APPLY` por animal**: la tabla se recorre UNA vez (2.264 lecturas, 0,3 s) y solo se agrupan las ~480 filas marcadas de 335.000. El `OUTER APPLY`
    # (con `TOP 1` por marca o con agregados) hacía que SQL Server armara un índice temporal de toda la tabla (991.000 lecturas) y, con poca memoria, lo derramara a disco: 23 a 66 s por 1.913 vacas.
    # (tanda 4: el mismo `TOP 1` trae también la fecha y las dos tendencias de esa lectura, que son del mismo día)
    "bcs_ultimo": Bloque("bcs_ultimo", "OUTER APPLY (SELECT TOP 1 x.BcsValue AS valor, x.DateAndTime AS fecha, x.TrendValueTwoWeeks AS t2, x.TrendValueFourWeeks AS t4 "
                                       "FROM BcsDailyData x WHERE x.Animal = b.OID ORDER BY x.DateAndTime DESC) bu"),
    # (tanda 4: suma las otras tres marcas de DelPro: fin del período de espera voluntaria, secado de la lactancia anterior y primera inseminación de la lactancia)
    "bcs_marcados": Bloque("bcs_marcados", "LEFT JOIN (SELECT x.Animal AS animal, MAX(CASE WHEN x.IsValueForLastBcsDryOff = 1 THEN x.BcsValue END) AS secado, "
                                           "MAX(CASE WHEN x.IsValueForLastCalving = 1 THEN x.BcsValue END) AS parto, MAX(CASE WHEN x.IsValueForLastBcs190DaysPregnant = 1 THEN x.BcsValue END) AS p190, "
                                           "MAX(CASE WHEN x.IsValueForLastBcsVWP = 1 THEN x.BcsValue END) AS vwp, MAX(CASE WHEN x.IsValueForLastBcsDryOffPrevLact = 1 THEN x.BcsValue END) AS secado_prev, "
                                           "MAX(CASE WHEN x.IsValueForFirstInseminationCurrentLactation = 1 THEN x.BcsValue END) AS primera_ia "
                                           "FROM BcsDailyData x WHERE x.IsValueForLastBcsDryOff = 1 OR x.IsValueForLastCalving = 1 OR x.IsValueForLastBcs190DaysPregnant = 1 OR x.IsValueForLastBcsVWP = 1 "
                                           "OR x.IsValueForLastBcsDryOffPrevLact = 1 OR x.IsValueForFirstInseminationCurrentLactation = 1 GROUP BY x.Animal) bm ON bm.animal = b.OID"),
})

_SAL = "Salud"
_TRA = "Tránsito de animales"


def _bloque_salud(id_: str, alias: str, clase: str) -> Bloque:
    """El ÚLTIMO evento de salud de cada animal de una clase (`clase`: la condición sobre `t.TreatmentType` de DelPro: 0 o sin tratamiento = diagnóstico, 1 = vacunación, 2 = IA a tiempo fijo).
    Los 100.000 eventos de la base se leen UNA vez: un solo agregado por animal con el máximo de «segundos desde el 2000 × 2^31 + número de evento» (un número que ordena igual que la fecha), y después se traen
    los datos de ESE evento por clave. Medido sobre la base del 20/11/2026: 3.400 páginas y 0,3 s para un informe de 2.000 vacas, contra los ~630.000 de leer el último de cada una.
    Sin funciones de ventana (ordenar 100.000 filas pide mucha memoria), sin agregados de texto (un `MAX` de cadena pide mucho más que uno de número o fecha) y con búsqueda por clave en `AbstractAnimalEvent` (`LOOP JOIN`): en un SQL con poca memoria de trabajo (el de la
    PC de desarrollo tiene 7 MB) una unión por hash grande espera la concesión de memoria minutos. Los nombres del diagnóstico, la categoría y la ubicación son códigos de `TextLookupItem`.
    Con empate de fecha gana el evento de mayor número (DelPro toma el máximo del nombre entre los empatados: una diferencia posible, rara)."""
    return Bloque(id_, "LEFT JOIN (SELECT m.animal, a2.DateAndTime AS fecha, tn.ItemValue AS diagnostico, tl.ItemValue AS ubicacion, tc.ItemValue AS categoria, t2.Name AS tratamiento, "
                       "d2.TreatmentEndDate AS fin, d2.DescriptionReport AS descripcion FROM ("
                       "SELECT a.BasicAnimal AS animal, MAX(DATEDIFF(second, '2000-01-01', a.DateAndTime) * CAST(2147483648 AS bigint) + a.OID) AS clave "
                       "FROM DiagnosisTreatmentEvent d INNER LOOP JOIN AbstractAnimalEvent a ON a.OID = d.OID AND a.GCRecord IS NULL LEFT JOIN Treatment t ON t.OID = d.Treatment "
                       f"WHERE {clase} GROUP BY a.BasicAnimal) m "
                       "INNER LOOP JOIN AbstractAnimalEvent a2 ON a2.OID = m.clave % 2147483648 INNER LOOP JOIN DiagnosisTreatmentEvent d2 ON d2.OID = a2.OID "
                       "LEFT JOIN Treatment t2 ON t2.OID = d2.Treatment LEFT JOIN Diagnosis dx ON dx.OID = d2.Diagnosis LEFT JOIN TextLookupItem tn ON tn.OID = dx.DiagnosisName "
                       f"LEFT JOIN TextLookupItem tc ON tc.OID = dx.Category LEFT JOIN TextLookupItem tl ON tl.OID = d2.DiagnosisLocation) {alias} ON {alias}.animal = b.OID")


# Tanda 3b: lo «pesado» que se mide barato leyendo cada tabla de eventos UNA vez (ver `tests/test_informes_tanda3b.py`). Las definiciones salen de `DDMReportItem.Expression`, la fórmula
# que DelPro guarda para cada campo.
BLOQUES.update({
    "lact_1": Bloque("lact_1", "LEFT JOIN HistoryAnimalLactationInfo h1 ON h1.Animal = b.OID AND h1.LactationNumber = 1"),
    # El resultado del último diagnóstico de gestación (`EventPregCheck.Result`: 0 negativo, 1 positivo, 2 dudoso) y si el último aborto lo generó un diagnóstico negativo.
    "pregcheck_result": Bloque("pregcheck_result", "LEFT JOIN EventPregCheck pc ON pc.OID = ep.OID LEFT JOIN EventAbortion ab ON ab.OID = ea.OID", requiere=("ult_control", "ult_aborto")),
    # La puerta de separación: 43.000 eventos (en la base del 20/11/2026 solo hay 8 días). Lo último confirmado es el máximo del corte de los eventos en que el animal se separó; la última
    # identificación (`CutIdTime`), la del evento más nuevo: se toma el MÁXIMO, porque la identificación de un evento posterior nunca es anterior a la de uno previo. Solo agregados de fecha:
    # uno de texto (un `MAX` de cadena compuesta) pedía tanta memoria que en un SQL con poco espacio de trabajo la consulta esperaba minutos.
    "separacion": Bloque("separacion", "LEFT JOIN (SELECT a.BasicAnimal AS animal, MAX(CASE WHEN s.IsAnimalSorted = 1 THEN s.CutDateTime END) AS confirmado, MAX(s.CutIdTime) AS cut_id, MAX(s.CutDateTime) AS ultimo "
                                       "FROM CMSSortResultEvent s INNER LOOP JOIN AbstractAnimalEvent a ON a.OID = s.OID AND a.GCRecord IS NULL GROUP BY a.BasicAnimal) sg ON sg.animal = b.OID"),
    "diag_ult": _bloque_salud("diag_ult", "dgn", "(t.TreatmentType = 0 OR d.Treatment IS NULL)"),
    "diag_vacuna": _bloque_salud("diag_vacuna", "dvc", "t.TreatmentType = 1"),
    "diag_sincro": _bloque_salud("diag_sincro", "dsc", "t.TreatmentType = 2"),
    "diag_todo": Bloque("diag_todo", "LEFT JOIN (SELECT a.BasicAnimal AS animal, MAX(a.DateAndTime) AS fecha FROM DiagnosisTreatmentEvent d INNER LOOP JOIN AbstractAnimalEvent a ON a.OID = d.OID AND a.GCRecord IS NULL "
                                     "GROUP BY a.BasicAnimal) dto ON dto.animal = b.OID"),
    # Cuántos ordeños tuvo ayer: las sesiones de leche (`SessionMilkYield`) con leche del registro diario de AYER del animal (el día lo define DelPro con su propio `AnimalDaily`, no un corte de horas nuestro).
    "ordenos_ayer": Bloque("ordenos_ayer", "LEFT JOIN (SELECT s.AnimalDaily AS dia, COUNT(*) AS n FROM SessionMilkYield s JOIN AnimalDaily d ON d.OID = s.AnimalDaily AND d.GCRecord IS NULL "
                                           "AND d.Date = DATEADD(day, -1, @HOY) WHERE s.TotalYield > 0 GROUP BY s.AnimalDaily) oa ON oa.dia = ly.OID", requiere=("leche_ayer",)),
})

# El resultado de un diagnóstico de gestación: `Result` 0 = negativo (ninguna de las 824 vacas con ese resultado en su último diagnóstico está preñada), 1 = positivo (2.396 de 3.624 preñadas hoy), 2 = dudoso
# (3 eventos en toda la base; DelPro lo llama `Uncertain` en sus filtros). La regla es la de DelPro: si el último aborto lo generó un diagnóstico negativo, el resultado es negativo.
_PREGCHECK = ("CASE (CASE WHEN ix.Abortion IS NULL OR (ea.DateAndTime IS NOT NULL AND ea.DateAndTime < ep.DateAndTime) THEN pc.Result "
              "WHEN ix.PregCheck IS NOT NULL AND ix.Abortion IS NOT NULL AND ab.IsGeneratedByNegativePregnancyCheck = 1 THEN 0 END) "
              "WHEN 0 THEN 'Negativo' WHEN 1 THEN 'Positivo' WHEN 2 THEN 'Dudoso' END")
# El número del transponder: `BasicAnimal.TransponderID` es un texto con ceros a la izquierda («032010031422386») y DelPro lo pasa a número con una función suya (`dbo.StringToNumeric`) que el usuario de
# solo lectura no puede ejecutar: `TRY_CAST` da lo mismo. Se muestra como texto para que no lo parta con separadores de miles.
_TRANSPONDER_NUMERO = "CAST(TRY_CAST(b.TransponderID AS bigint) AS nvarchar(20))"

# ---- Tanda 4 (25/11/2026): los bloques de los campos que el cliente eligió traer de DelPro (la lista «Campos que faltan» de «Traer de DelPro…»). Cada fórmula sale de `DDMReportItem.Expression`.
# Los ordeños de un día se leen UNA vez (5.000 filas de `SessionMilkYield` de un día, no la tabla de 826.000) y se agregan por animal con todas las medidas que piden los campos «de ayer» y «de hoy»:
# una consulta con muchas columnas del mismo día paga una sola lectura. Sin funciones de ventana ni agregados de texto (ver la nota de `_bloque_salud` sobre la memoria de este SQL).
_NUMEROS = (1, 2, 3)            # el 1.º, el 2.º y el 3.º ordeño (o sesión) del día: los que usan los informes del tambo


def _bloque_ordenos(id_: str, alias: str, dia: str) -> Bloque:
    """Los ordeños del día `dia` (una expresión de fecha sobre @HOY) agregados por animal. `MilkingNumber` es el ordeño de ESA vaca ese día (1.º, 2.º...) y `SessionNo` la sesión de la sala
    (1 a 3): son cosas distintas (una vaca que faltó a la sesión 2 tiene su 2.º ordeño en la sesión 3), y DelPro tiene un grupo de campos para cada una. «Ayer» y «hoy» son el día del registro
    diario del animal (`AnimalDaily.Date`), igual que «Producción de leche de ayer». Cada medida lleva el filtro de DelPro: con leche (`TotalYield > 0`) salvo el retiro forzado y la hora de
    identificación, que cuentan también un ordeño sin leche."""
    medidas = []
    for n in _NUMEROS:
        en, con_leche = f"s.MilkingNumber = {n}", f"s.MilkingNumber = {n} AND s.TotalYield > 0"
        medidas += [f"MAX(CASE WHEN {con_leche} THEN c.PeakFlow END) AS pico{n}", f"AVG(CASE WHEN {con_leche} THEN c.TakeOffFlow END) AS retiro{n}",
                    f"AVG(CASE WHEN {con_leche} THEN c.AverageFlow END) AS flujo{n}", f"MAX(CASE WHEN {con_leche} THEN DATEDIFF(second, s.BeginTime, s.EndTime) END) AS dur{n}",
                    f"MAX(CASE WHEN {en} AND c.ForcedRetract = 1 THEN 1 ELSE 0 END) AS forz{n}", f"MAX(CASE WHEN {en} THEN v.IDTime END) AS idt{n}",
                    f"SUM(CASE WHEN {con_leche} THEN s.TotalYield END) AS leche{n}"]
    medidas += [f"SUM(CASE WHEN s.SessionNo = {k} AND s.TotalYield > 0 THEN s.TotalYield END) AS sesion{k}" for k in _NUMEROS]
    medidas += ["SUM(s.TotalYield) AS total", "SUM(CASE WHEN s.TotalYield > 0 THEN 1 ELSE 0 END) AS n"]
    return Bloque(id_, "LEFT JOIN (SELECT s.BasicAnimal AS animal, " + ", ".join(medidas) + " FROM SessionMilkYield s "
                       f"INNER JOIN AnimalDaily d ON d.OID = s.AnimalDaily AND d.GCRecord IS NULL AND d.Date = {dia} INNER JOIN CMSMilkYield c ON c.OID = s.OID "
                       f"LEFT JOIN MilkingDeviceVisit v ON v.OID = c.MilkingDeviceVisit GROUP BY s.BasicAnimal) {alias} ON {alias}.animal = b.OID")


def _bloque_bcs_dslc(dias: int) -> Bloque:
    """La condición corporal de la cámara EXACTAMENTE `dias` días después del último parto (DelPro: `GetDate(DateAndTime) = AddDays(LastCalvingDate, N)`; el último parto de la reproducción es el inicio
    de la lactancia en curso, que es lo que usan los días en ordeño). Una sola lectura de la tabla de la cámara, unida a la reproducción por animal."""
    return Bloque(f"bcs_dslc{dias}", "LEFT JOIN (SELECT x.Animal AS animal, MAX(x.BcsValue) AS valor FROM BcsDailyData x INNER JOIN AnimalReproductionInfo q ON q.Animal = x.Animal AND q.GCRecord IS NULL "
                                     f"AND CAST(x.DateAndTime AS date) = CAST(DATEADD(day, {dias}, q.LastLactationChangeDate) AS date) GROUP BY x.Animal) bd{dias} ON bd{dias}.animal = b.OID")


BLOQUES.update({
    "ord_ayer": _bloque_ordenos("ord_ayer", "oy", "DATEADD(day, -1, @HOY)"),
    "ord_hoy": _bloque_ordenos("ord_hoy", "oh", "@HOY"),
    # Los 7 días anteriores a «Calcular al» ([hoy-7, hoy), como el promedio de 7 días). Dos agregados SIMPLES por animal (un solo nivel de GROUP BY cada uno) y no uno en dos niveles: el de dos niveles pedía
    # 3,5 MB de memoria de trabajo y en el SQL de la PC de desarrollo (7 MB) esperaba para siempre. `ord_7d`: los ordeños con más de 100 g y la duración promedio de los que tienen leche; `dias_7d`: los
    # días con leche (para «ordeños por día»).
    "ord_7d": Bloque("ord_7d", "LEFT JOIN (SELECT s.BasicAnimal AS animal, SUM(CASE WHEN s.TotalYield > 0.1 THEN 1 ELSE 0 END) AS ordenos, "
                               "AVG(CASE WHEN s.TotalYield > 0 THEN CAST(DATEDIFF(second, s.BeginTime, s.EndTime) AS float) END) AS dur FROM SessionMilkYield s "
                               "INNER JOIN AnimalDaily d ON d.OID = s.AnimalDaily AND d.GCRecord IS NULL AND d.Date >= DATEADD(day, -7, @HOY) AND d.Date < @HOY GROUP BY s.BasicAnimal) o7 ON o7.animal = b.OID"),
    "dias_7d": Bloque("dias_7d", "LEFT JOIN (SELECT d.BasicAnimal AS animal, COUNT(*) AS dias FROM AnimalDaily d WHERE d.GCRecord IS NULL AND d.Date >= DATEADD(day, -7, @HOY) AND d.Date < @HOY "
                                 "AND d.TotalYield > 0 GROUP BY d.BasicAnimal) d7 ON d7.animal = b.OID"),
    # El último ordeño del animal (`LatestHistoryIndex.SessionMilk`) y su visita a la sala: el rodeo en que pasó y si era el suyo.
    "ult_visita": Bloque("ult_visita", "LEFT JOIN SessionMilkYield lm ON lm.OID = ix.SessionMilk LEFT JOIN CMSMilkYield lc ON lc.OID = lm.OID LEFT JOIN CMSDeviceVisit lv ON lv.OID = lc.MilkingDeviceVisit "
                                       "LEFT JOIN AbstractGroup lg ON lg.OID = lv.VisitedInGroup AND lg.GCRecord IS NULL", requiere=("indice",)),
    # La última identificación en la sala: el máximo de TODAS las visitas del animal (también las que no dieron leche), no solo la del último ordeño.
    "ult_idtime": Bloque("ult_idtime", "LEFT JOIN (SELECT v.Animal AS animal, MAX(v.IDTime) AS fecha FROM MilkingDeviceVisit v WHERE v.GCRecord IS NULL GROUP BY v.Animal) li ON li.animal = b.OID"),
    # La sesión de ordeño en curso: la función de DelPro (`dbo.GetLastOngoingSessionStartTime`) no mira al animal: es el inicio de la última sesión de la sala que todavía no terminó.
    "sesion_actual": Bloque("sesion_actual", "LEFT JOIN (SELECT TOP 1 h.StartTime AS inicio FROM DeviceSessionHistory h WHERE h.EndTime IS NULL ORDER BY h.StartTime DESC) se ON 1 = 1"),
    "ult_secado": Bloque("ult_secado", "LEFT JOIN AbstractAnimalEvent es ON es.OID = ix.DryOff AND es.GCRecord IS NULL", requiere=("indice",)),
    # Todas las inseminaciones del animal (de todas las lactancias; el índice de DelPro se reinicia con cada parto): los eventos del tipo «inseminación» (el 99 en la base de La Ponderosa; se busca por el
    # NOMBRE del tipo, porque el número de cada tipo es de cada base). `AbstractAnimalEvent` tiene un índice que cubre justo esto (GCRecord, ObjectType, BasicAnimal, DateAndTime): se recorre solo ese
    # tramo, ya ordenado por animal, sin tabla temporal. La versión que buscaba cada evento por clave desde `EventInsemination` (42.000 filas) tardaba 13 s y pedía 3 MB de memoria de trabajo.
    "insem_todas": Bloque("insem_todas", "LEFT JOIN (SELECT a.BasicAnimal AS animal, MAX(a.DateAndTime) AS fecha FROM AbstractAnimalEvent a INNER JOIN XPObjectType t ON t.OID = a.ObjectType "
                                         "AND t.TypeName LIKE '%.EventInsemination' WHERE a.GCRecord IS NULL GROUP BY a.BasicAnimal) lie ON lie.animal = b.OID"),
    # La leche de la lactancia en curso (DelPro: `dbo.GetTotalYieldInDIMInLactation`, que suma el historial diario hasta ese día en ordeño): solo los días con leche, que dan lo mismo y son muchos menos.
    "leche_hasta_305": Bloque("leche_hasta_305", "LEFT JOIN (SELECT a.ReferenceId AS animal, dd.LactationNumber AS lact, SUM(dd.DailyYield) AS total FROM HistoryAnimalDailyData dd "
                                                 "INNER JOIN HistoryAnimal a ON a.OID = dd.Animal WHERE dd.DailyYield > 0 AND dd.DIM >= 0 AND dd.DIM <= 305 GROUP BY a.ReferenceId, dd.LactationNumber) ac305 "
                                                 "ON ac305.animal = b.OID AND ac305.lact = r.LactationNumber", requiere=("repro",)),
    "leche_dia_305": Bloque("leche_dia_305", "LEFT JOIN (SELECT d.BasicAnimal AS animal, d.LactationNumber AS lact, MAX(d.TotalYield) AS leche FROM AnimalDaily d WHERE d.GCRecord IS NULL "
                                             "AND d.DIM = 305 AND d.TotalYield > 0 GROUP BY d.BasicAnimal, d.LactationNumber) d305 ON d305.animal = b.OID AND d305.lact = r.LactationNumber", requiere=("repro",)),
    "prom7_hace_30": Bloque("prom7_hace_30", "LEFT JOIN (SELECT d.BasicAnimal AS animal, d.LactationNumber AS lact, MAX(d.AvgYieldPrev7d) AS prom FROM AnimalDaily d WHERE d.GCRecord IS NULL "
                                             "AND d.Date = DATEADD(day, -30, @HOY) GROUP BY d.BasicAnimal, d.LactationNumber) a30 ON a30.animal = b.OID AND a30.lact = r.LactationNumber", requiere=("repro",)),
    # La cámara de condición corporal: el valor de ayer (DelPro: `GetDate(DateAndTime) = Yesterday()`) y el de los días 7, 30, 50, 90 y 200 después del parto.
    "bcs_ayer": Bloque("bcs_ayer", "LEFT JOIN (SELECT x.Animal AS animal, MAX(x.BcsValue) AS valor FROM BcsDailyData x WHERE x.DateAndTime >= DATEADD(day, -1, @HOY) AND x.DateAndTime < @HOY "
                                   "GROUP BY x.Animal) bay ON bay.animal = b.OID"),
    **{f"bcs_dslc{n}": _bloque_bcs_dslc(n) for n in (7, 30, 50, 90, 200)},
    # El último corte confirmado de la puerta de separación (el evento con la fecha más nueva entre los que SÍ separaron al animal): su compuerta, su carril (que lleva al área), el motivo y la regla. Mismo
    # método que el último diagnóstico: un agregado numérico por animal (segundos × 2^31 + número de evento) y los datos de ESE evento por clave.
    "corte_conf": Bloque("corte_conf", "LEFT JOIN (SELECT m.animal, s2.CutGate AS compuerta, s2.SortDecision AS motivo, s2.SortedLane AS carril, s2.LastAutoSortRule AS regla FROM ("
                                       "SELECT a.BasicAnimal AS animal, MAX(DATEDIFF(second, '2000-01-01', s.CutDateTime) * CAST(2147483648 AS bigint) + s.OID) AS clave FROM CMSSortResultEvent s "
                                       "INNER LOOP JOIN AbstractAnimalEvent a ON a.OID = s.OID AND a.GCRecord IS NULL WHERE s.IsAnimalSorted = 1 GROUP BY a.BasicAnimal) m "
                                       "INNER LOOP JOIN CMSSortResultEvent s2 ON s2.OID = m.clave % 2147483648) cf ON cf.animal = b.OID LEFT JOIN CMSSortGateDevice cg ON cg.OID = cf.compuerta "
                                       "LEFT JOIN Device cd ON cd.OID = cg.OID LEFT JOIN CMSSortGateAreaMapping cm ON cm.OID = cf.carril LEFT JOIN CMSSortArea ca ON ca.OID = cm.TargetSortArea "
                                       "LEFT JOIN CMSAutoSortRule cr ON cr.OID = cf.regla"),
    # La separación manual: una fila por animal (`SortOnce`), y la primera de sus dos reglas (DelPro: `dbo.CowsToBeSortedToday`, que numera las reglas por su OID).
    "sep_una_vez": Bloque("sep_una_vez", "LEFT JOIN (SELECT ms.Animal AS animal, MAX(CAST(ms.SortOnce AS int)) AS una_vez FROM CMSManualSorting ms GROUP BY ms.Animal) so ON so.animal = b.OID"),
    "sep_siempre": Bloque("sep_siempre", "LEFT JOIN (SELECT ms.Animal AS animal, MAX(CASE WHEN r1.StartDateTime IS NOT NULL AND r1.EndDateTime IS NULL THEN 1 ELSE 0 END) AS siempre FROM CMSManualSorting ms "
                                         "INNER JOIN (SELECT ManualSorting, MIN(OID) AS primera FROM CMSManualSortingRule GROUP BY ManualSorting) p ON p.ManualSorting = ms.OID "
                                         "INNER JOIN CMSManualSortingRule r1 ON r1.OID = p.primera GROUP BY ms.Animal) sf ON sf.animal = b.OID"),
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
    Campo("AgeInMonths", "Edad en meses", _GEN, "num", "DATEDIFF(day, b.BirthDate, ISNULL(b.ExitDate, @HOY)) / 30.5", unidad="meses", decimales=1, verificado=False,
          nota="la fórmula de DelPro: la edad en días dividida 30,5 (un decimal); deja de contar cuando el animal salió; falta compararla con un informe de DelPro"),
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
          nota="la inseminación efectiva más los días de gestación que el tambo tiene en DelPro, solo de una vaca preñada y que no salió del tambo (la fórmula de DelPro); falta compararla con un informe de DelPro"),
    Campo("DaysToCalving", "Días para el parto", _REP, "num", f"DATEDIFF(day, @HOY, {_PARTO_ESPERADO})", bloque="repro", bloques=_ADEMAS_PARTO, verificado=False,
          nota="días entre «Calcular al» y la fecha esperada de parto (negativo = ya pasó); falta compararlo con un informe de DelPro"),
    Campo("ExpectedDryOffDate", "Fecha esperada de secado", _REP, "fecha", _SECADO_ESPERADO, bloque="repro", bloques=_ADEMAS_SECADO, verificado=False,
          nota="la fecha esperada de parto menos los días de secado que el tambo tiene en DelPro, como la fórmula de DelPro: solo de una vaca que ya parió y que todavía no se secó; falta compararla con un informe de DelPro"),
    Campo("DaysToDryOff", "Días para el secado", _REP, "num", f"DATEDIFF(day, @HOY, {_SECADO_ESPERADO})", bloque="repro", bloques=_ADEMAS_SECADO, verificado=False,
          nota="días entre «Calcular al» y la fecha esperada de secado (negativo = ya pasó); falta compararlo con un informe de DelPro"),

    # ---- Tanda 2 (24/11/2026): punteros del índice de DelPro, el último recuento de células y los promedios de 14, 21 y 30 días.
    Campo("EffectiveInseminationDate", "Fecha de la inseminación efectiva", _REP, "fecha", "ee.DateAndTime", bloque="insem_efectiva",
          nota="el evento al que apunta el índice de DelPro (LatestHistoryIndex.EffectiveInsemination): la inseminación con la que quedó preñada; es la que usan «Partos y secados» y la proyección"),
    Campo("LastPregnancyCheckDate", "Fecha del último diagnóstico de gestación", _REP, "fecha", "CASE WHEN ea.DateAndTime IS NULL OR ep.DateAndTime > ea.DateAndTime THEN ep.DateAndTime END",
          bloque="ult_control", bloques=("ult_aborto",), verificado=False,
          nota="el evento al que apunta el índice de DelPro (LatestHistoryIndex.PregCheck), y vacío si hay un aborto posterior: la fórmula de DelPro; falta compararla con un informe de DelPro"),
    Campo("LastCullDecisionDate", "Fecha de la última decisión de descarte", _GEN, "fecha", "ed.DateAndTime", bloque="ult_descarte", verificado=False,
          nota="la fecha del evento al que apunta el índice de DelPro (LatestHistoryIndex.CullDecision); falta confirmar si DelPro muestra esa fecha o la del descarte previsto"),
    Campo("DaysPregnant", "Días de preñez", _REP, "num", f"CASE WHEN {_PRENEZ_VIGENTE} THEN DATEDIFF(day, ee.DateAndTime, @HOY) END", bloque="repro", bloques=("insem_efectiva",),
          verificado=False, nota="días desde la inseminación efectiva hasta «Calcular al», solo de una vaca preñada; la cuenta es nuestra, falta compararla con un informe de DelPro"),
    Campo("LatestSCC", "Último recuento de células somáticas", _ORD, "num", "sc.valor", bloque="ult_scc", unidad="mil/ml", verificado=False,
          nota="el SCC del último control lechero que trae recuento (MilkTest, en miles de células por ml, como lo guarda DelPro); falta compararlo con un informe de DelPro"),
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

    # ---- Tanda 3a (24/11/2026): lo barato que queda (ver `tests/test_informes_tanda3a.py`). Todos sin comparar contra un informe de DelPro.
    Campo("LatestSCCTestDate", "Fecha del último recuento de células somáticas", _ORD, "fecha", "sc.fecha", bloque="ult_scc", verificado=False,
          nota="la fecha del último control lechero que trae el recuento; falta compararla con un informe de DelPro"),
    Campo("LatestFat", "Última grasa", _ORD, "num", "gr.valor", bloque="ult_grasa", unidad="%", decimales=1, verificado=False,
          nota="la grasa (%) del último control lechero que la trae; falta compararla con un informe de DelPro"),
    Campo("LatestFatDate", "Fecha de la última grasa", _ORD, "fecha", "gr.fecha", bloque="ult_grasa", verificado=False,
          nota="la fecha del último control lechero que trae la grasa; falta compararla con un informe de DelPro"),
    Campo("LatestProtein", "Última proteína", _ORD, "num", "pr.valor", bloque="ult_proteina", unidad="%", decimales=1, verificado=False,
          nota="la proteína (%) del último control lechero que la trae; falta compararla con un informe de DelPro"),
    Campo("LatestMilkTestYield", "Último rendimiento del control lechero", _ORD, "num", "rd.valor", bloque="ult_rend", unidad="kg", decimales=1, verificado=False,
          nota="los kg del último control lechero que trae el rendimiento; falta compararlo con un informe de DelPro"),
    Campo("LatestMilkTestYieldDate", "Fecha del último rendimiento del control", _ORD, "fecha", "rd.fecha", bloque="ult_rend", verificado=False,
          nota="la fecha del último control lechero que trae el rendimiento; falta compararla con un informe de DelPro"),
    Campo("AgeInDays", "Edad en días", _GEN, "num", "DATEDIFF(day, b.BirthDate, ISNULL(b.ExitDate, @HOY))", verificado=False,
          nota="días entre el nacimiento y «Calcular al», o hasta la salida si el animal salió (DelPro lo corta en la salida de un animal descartado o muerto; acá, de cualquier salida: no se sabe qué "
               "código de ExitType es cada una); falta compararla con un informe de DelPro"),
    Campo("Sex", "Sexo", _GEN, "txt", "CASE b.Sex WHEN 2 THEN 'Hembra' WHEN 1 THEN 'Macho' END", agrupable=True, valores=("Hembra", "Macho"), equivale=(("1", "Macho"), ("2", "Hembra")), verificado=False,
          nota="BasicAnimal.Sex: 2 = hembra (9.934 animales, los 5.911 con lactancia son todos 2) y 1 = macho (492, ninguno con lactancia); es la lectura de los datos, falta confirmarla con un informe de DelPro"),
    Campo("DaysSinceLastGroupChange", "Días desde el último cambio de rodeo", _GEN, "num", "DATEDIFF(day, eg.DateAndTime, @HOY)", bloque="cambio_grupo", verificado=False,
          nota="días entre el último cambio de rodeo (el índice de DelPro) y «Calcular al»; la cuenta es nuestra, falta compararla con un informe de DelPro"),
    Campo("PreviousGroupNumber", "Número del rodeo anterior", _GEN, "num", "gp.Number", bloque="cambio_grupo", verificado=False,
          nota="el número del rodeo del que venía en su último cambio (EventGroupChange.OldGroup); falta compararlo con un informe de DelPro"),
    Campo("PrevCalvingDate", "Fecha del parto anterior", _REP, "fecha", "hp.StartDate", bloque="lact_ant", verificado=False,
          nota="el inicio de la lactancia anterior en el resumen de DelPro: coincide con el penúltimo parto cargado en 382 de 388 vacas; falta compararla con un informe de DelPro"),
    Campo("LastBcsValue", "Última condición corporal (cámara)", "Condición corporal", "num", "bu.valor", bloque="bcs_ultimo", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el último valor de la cámara (BcsDailyData); solo en los tambos con cámara; falta compararlo con un informe de DelPro"),
    Campo("LastBcsDryOffValue", "Condición corporal al secado", "Condición corporal", "num", "bm.secado", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara que DelPro marca para el secado (IsValueForLastBcsDryOff); falta compararlo con un informe de DelPro"),
    Campo("LastBcsCalvingValue", "Condición corporal al parto", "Condición corporal", "num", "bm.parto", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara que DelPro marca para el último parto (IsValueForLastCalving); falta compararlo con un informe de DelPro"),
    Campo("LastBcsValue190DaysPregnant", "Condición corporal a los 190 días de preñez", "Condición corporal", "num", "bm.p190", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData",
          verificado=False, nota="el valor de la cámara que DelPro marca a los 190 días de preñez (IsValueForLastBcs190DaysPregnant); falta compararlo con un informe de DelPro"),

    # ---- Tanda 3b (24/11/2026): los «pesados» (se leen con una sola pasada por tabla de eventos) y los que DelPro define con una fórmula que ahora se conoce. Todos ⚠.
    Campo("IsBred", "Inseminada y no preñada", _REP, "txt", _si_no("r.IsInseminated = 1 AND r.IsPregnant = 0"), bloque="repro", agrupable=True, valores=SI_NO, verificado=False,
          nota="la fórmula de DelPro: IsInseminated = True AND IsPregnant = False, sobre las columnas de AnimalReproductionInfo; falta compararla con un informe de DelPro"),
    Campo("AgeAtFirstCalving", "Edad al primer parto", _REP, "num", "DATEDIFF(month, b.BirthDate, h1.StartDate) - CASE WHEN DAY(b.BirthDate) > DAY(h1.StartDate) THEN 1 ELSE 0 END",
          bloque="lact_1", unidad="meses", verificado=False,
          nota="meses entre el nacimiento y el inicio de la primera lactancia (el resumen de lactancia de DelPro); coincide el mismo día con el primer parto o aborto cargado en 498 de 498 vacas "
               "(25 meses de media). DelPro lo muestra en años y meses; falta compararlo con un informe de DelPro"),
    Campo("LatestPregCheckResult", "Resultado del último diagnóstico de gestación", _REP, "txt", _PREGCHECK, bloque="pregcheck_result", agrupable=True, valores=("Negativo", "Positivo", "Dudoso"),
          equivale=(("Negative", "Negativo"), ("Positive", "Positivo"), ("Uncertain", "Dudoso")), verificado=False,
          nota="la fórmula de DelPro sobre el último diagnóstico de gestación y el último aborto del índice. Resultado 0 = negativo (ninguna de las 824 vacas está preñada), 1 = positivo (2.396 de "
               "3.624 están preñadas hoy), 2 = dudoso (3 en toda la base; DelPro lo llama Uncertain). Los nombres 1 y 2 se deducen: falta compararlo con un informe de DelPro"),
    Campo("TransponderID", "Transponder", _GEN, "txt", "b.TransponderID", nota="la columna BasicAnimal.TransponderID (texto, con ceros a la izquierda)", verificado=False),
    Campo("TransponderIDNumeric", "Número del transponder", _GEN, "txt", _TRANSPONDER_NUMERO, verificado=False,
          nota="BasicAnimal.TransponderID pasado a número (sin los ceros de la izquierda), como la función de DelPro; se muestra como texto para que no lleve separadores de miles"),
    Campo("CMSTransponderID", "Número del transponder (CMS)", _GEN, "txt", _TRANSPONDER_NUMERO, verificado=False,
          nota="lo mismo que el número del transponder: la fórmula de DelPro lo pasa a entero largo; se muestra como texto para que no lleve separadores de miles"),
    Campo("HDXTransponderID", "Transponder HDX", _GEN, "txt", "CASE WHEN b.TransponderType = 1 THEN b.TransponderID END", verificado=False,
          nota="el transponder de los animales con TransponderType = 1 (HDX), como la fórmula de DelPro; falta compararlo con un informe de DelPro"),

    Campo("LastCutDateTimeSortingConfirmed", "Última separación confirmada", _TRA, "fecha", "sg.confirmado", bloque="separacion", verificado=False,
          nota="la fecha y hora del corte más reciente en que la puerta de separación sí separó al animal (CMSSortResultEvent.IsAnimalSorted); en la base del 20/11/2026 el registro solo cubre 8 días"),
    Campo("CMSDaysSinceLastSort", "Días desde la última separación", _TRA, "num", "CASE WHEN sg.confirmado < DATEADD(day, 1, @HOY) THEN DATEDIFF(day, sg.confirmado, @HOY) END", bloque="separacion",
          unidad="días", verificado=False, nota="días entre la última separación confirmada y «Calcular al»; la cuenta es de DelPro, falta compararla con un informe suyo"),
    Campo("LastCutIdTime", "Última identificación en la puerta de separación", _TRA, "fecha", "sg.cut_id", bloque="separacion", verificado=False,
          nota="la hora de identificación (CutIdTime) del evento más nuevo de la puerta de separación del animal; falta compararla con un informe de DelPro"),

    # Salud: el último diagnóstico (un tratamiento de tipo 0 o un evento sin tratamiento), la última vacunación (tipo 1) y el último protocolo de IA a tiempo fijo (tipo 2). La base del 20/11/2026 solo
    # tiene eventos de tipo 0 y sin tratamiento: los campos de vacunación y de IA a tiempo fijo no se pudieron probar con datos (otros tambos sí los usan).
    Campo("DateOfLatestDiagnosisTreatmentEvent", "Fecha del último diagnóstico", _SAL, "fecha", "dgn.fecha", bloque="diag_ult", verificado=False,
          nota="el último evento de diagnóstico y tratamiento del animal (tratamiento de tipo 0 o sin tratamiento, como DelPro); falta compararlo con un informe de DelPro"),
    Campo("DaysSinceLatestTreatmentFromTreatmentEvent", "Días desde el último diagnóstico", _SAL, "num", "DATEDIFF(day, dgn.fecha, @HOY)", bloque="diag_ult", unidad="días", verificado=False,
          nota="días entre el último diagnóstico y «Calcular al»; falta compararlo con un informe de DelPro"),
    Campo("DiagnosisFromDiagnosisTreatmentEvent", "Último diagnóstico", _SAL, "txt", "dgn.diagnostico", bloque="diag_ult", verificado=False,
          nota="el nombre del diagnóstico del último evento (la lista de textos de DelPro: Neumonia, Mastitis…); falta compararlo con un informe de DelPro"),
    Campo("DiagnosisLocationFromDiagnosisTreatmentEvent", "Ubicación del último diagnóstico", _SAL, "txt", "dgn.ubicacion", bloque="diag_ult", verificado=False,
          nota="la ubicación del último diagnóstico (la lista de textos de DelPro; «(-)» es sin ubicación); falta compararla con un informe de DelPro"),
    Campo("LatestDiagnosisCategory", "Categoría del último diagnóstico", _SAL, "txt", "dgn.categoria", bloque="diag_ult", agrupable=True, verificado=False,
          nota="la categoría del diagnóstico del último evento (Probl.Respiratorios, Prob.Ubre…); falta compararla con un informe de DelPro"),
    Campo("TreatmentFromDiagnosisTreatmentEvent", "Último tratamiento", _SAL, "txt", "dgn.tratamiento", bloque="diag_ult", verificado=False,
          nota="el tratamiento del último diagnóstico; vacío si el evento no tiene tratamiento; falta compararlo con un informe de DelPro"),
    Campo("TreatmentStartDate", "Inicio del último tratamiento", _SAL, "fecha", "dgn.fecha", bloque="diag_ult", verificado=False,
          nota="el inicio del tratamiento del último diagnóstico: la fecha del evento; falta compararla con un informe de DelPro"),
    Campo("TreatmentEndDate", "Fin del último tratamiento", _SAL, "fecha", "dgn.fin", bloque="diag_ult", verificado=False,
          nota="DiagnosisTreatmentEvent.TreatmentEndDate del último diagnóstico; falta compararla con un informe de DelPro"),
    Campo("TreatmentDrugsAndDosage", "Medicamentos y dosis del último tratamiento", _SAL, "txt", "dgn.descripcion", bloque="diag_ult", verificado=False,
          nota="DiagnosisTreatmentEvent.DescriptionReport del último diagnóstico; falta compararlo con un informe de DelPro"),
    Campo("TreatmentDaysRemaining", "Días que le quedan al tratamiento", _SAL, "num", "CASE WHEN dgn.fin > @HOY THEN DATEDIFF(hour, @HOY, dgn.fin) / 24.0 END", bloque="diag_ult", unidad="días",
          decimales=1, verificado=False,
          nota="días entre «Calcular al» y el fin del último tratamiento, si todavía no terminó; DelPro lo cuenta hasta el momento exacto, acá desde el día de «Calcular al»"),
    Campo("DateOfLatestVaccinationDiagnosisTreatmentEvent", "Fecha de la última vacunación", _SAL, "fecha", "dvc.fecha", bloque="diag_vacuna", verificado=False,
          nota="el último evento con un tratamiento de tipo 1 (vacunación); sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("TreatmentFromVaccinationDiagnosisTreatmentEvent", "Última vacuna", _SAL, "txt", "dvc.tratamiento", bloque="diag_vacuna", verificado=False,
          nota="el nombre del tratamiento de la última vacunación; sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("DaysSinceLatestVaccinationDiagnosisTreatmentEvent", "Días desde la última vacunación", _SAL, "num", "DATEDIFF(day, dvc.fecha, @HOY)", bloque="diag_vacuna", unidad="días", verificado=False,
          nota="días entre la última vacunación y «Calcular al»; sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("DateOfTimedAITreatmentEvent", "Fecha del último protocolo de IA a tiempo fijo", _SAL, "fecha", "dsc.fecha", bloque="diag_sincro", verificado=False,
          nota="el último evento con un tratamiento de tipo 2 (IA a tiempo fijo); sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("TreatmentFromTimedAIDiagnosisTreatmentEvent", "Último protocolo de IA a tiempo fijo", _SAL, "txt", "dsc.tratamiento", bloque="diag_sincro", verificado=False,
          nota="el nombre del tratamiento del último protocolo de IA a tiempo fijo; sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("DaysSinceLatestTreatmentFromTimedAITreatmentEvent", "Días desde el último protocolo de IA a tiempo fijo", _SAL, "num", "DATEDIFF(day, dsc.fecha, @HOY)", bloque="diag_sincro", unidad="días",
          verificado=False, nota="días entre el último protocolo de IA a tiempo fijo y «Calcular al»; sin datos en la base del 20/11/2026: sin probar con datos"),
    Campo("LatestDiagnosisTreatmentEventDate", "Fecha del último evento de salud", _SAL, "fecha", "dto.fecha", bloque="diag_todo", verificado=False,
          nota="la fecha del último evento de diagnóstico y tratamiento de cualquier tipo (diagnóstico, vacunación o IA a tiempo fijo); falta compararla con un informe de DelPro"),

    Campo("MilkingYesterday", "Ordeños de ayer", _ORD, "num", "ISNULL(oa.n, 0)", bloque="ordenos_ayer", verificado=False,
          nota="las sesiones de leche con producción del registro diario de ayer (el día anterior a «Calcular al», como «Producción de leche de ayer»); sin ordeños es 0, como el Count() de DelPro"),
)}


# ====================================================================================================================================================================================
# Tanda 4 (25/11/2026): los campos que el cliente eligió traer de DelPro y todavía faltaban (el cuadro «Campos que faltan» de «Traer de DelPro…»). Todos ⚠: cada uno sale de la fórmula que DelPro
# guarda en `DDMReportItem.Expression`, pero ninguno se comparó con un informe de DelPro. Ver `tests/test_informes_tanda4.py`.
#
# **Los campos con parámetro.** DelPro guarda el valor del parámetro de una columna (el número de ordeño, la sesión, los días en ordeño...) como una fila `ReportItemParameter` de `AbstractSetting`
# (`ReportItemInstance` = la columna): la nota de la tanda 3b que decía que «el informe no guarda el parámetro» era falsa. Acá cada valor que el tambo usa es un campo propio, con el id
# `<campo de DelPro>_<valor>`, y `Campo.item` + `Campo.parametros` le dicen al importador a cuál corresponde cada columna.
# ====================================================================================================================================================================================
_ORD_AYER = "Ordeño de ayer, por número de ordeño"
_ORD_HOY = "Ordeño de hoy, por número de ordeño"
_ORD_SES = "Ordeño por sesión"
_BCS = "Condición corporal"
_SIN_COMPARAR = "falta compararlo con un informe de DelPro"


def _variante(item: str, param: str, valor, etiqueta: str, categoria: str, tipo: str, expr: str, **kw) -> Campo:
    """Un campo de DelPro con parámetro, fijado a UN valor: su id es `<item>_<valor>`."""
    return Campo(f"{item}_{valor}", etiqueta, categoria, tipo, expr, item=item, parametros=((param, str(valor)),), verificado=False, **kw)


# (columna del bloque, campo de DelPro «de ayer», campo de DelPro «de hoy», nombre, tipo, unidad, decimales, qué mide)
_MEDIDAS_ORDENO = (
    ("pico", "PeakFlowYesterdayMilkingNumber", "PeakFlowTodayMilkingNumber", "Flujo máximo", "num", "kg/min", 1, "el flujo máximo del ordeño (CMSMilkYield.PeakFlow), de un ordeño con leche"),
    ("retiro", "CMSTakeOffFlowYesterday", "CMSTakeOffFlowToday", "Flujo de retiro", "num", "kg/min", 2,
     "el flujo al que se soltó la pezonera (CMSMilkYield.TakeOffFlow), promedio de los ordeños con leche"),
    ("flujo", "AvgMilkFlowYesterdayMilking", "AvgFlowTodayMilkingNumber", "Flujo promedio", "num", "kg/min", 1, "el flujo promedio del ordeño (CMSMilkYield.AverageFlow), de los ordeños con leche"),
    ("dur", "MilkDurationYesterdayMilkingNumber", "MilkDurationTodayMilkingNumber", "Duración del ordeño", "num", "s", 0,
     "los segundos entre el comienzo y el fin del ordeño (el mayor, si hubo más de uno con leche); DelPro los muestra como mm:ss"),
    ("leche", "YieldYesterdayMilkingNumber", "YieldTodayMilkingNumber", "Leche del ordeño", "num", "kg", 2, "los kg del ordeño (la suma de los ordeños con leche)"),
    ("forz", "ForcedRetractYesterdayMilking", "ForcedRetractTodayMilking", "Retirada forzada", "txt", "", 0,
     "«Sí» si en ese ordeño se retiró la pezonera a la fuerza (CMSMilkYield.ForcedRetract); sin ordeño es «No», como el Exists() de DelPro"),
    ("idt", "IdTimeYesterdayMilkingNumber", "IdTimeTodayMilkingNumber", "Hora de identificación", "txt", "", 0,
     "la hora (hh:mm:ss) en que se identificó a la vaca para ese ordeño (MilkingDeviceVisit.IDTime)"),
)


def _campos_por_ordeno() -> list:
    """Cada medida de ordeño, de ayer y de hoy, para el 1.º, el 2.º y el 3.º ordeño de la vaca: 7 × 2 × 3 = 42 campos que salen de dos bloques (uno por día)."""
    campos = []
    for col, item_ayer, item_hoy, nombre, tipo, unidad, dec, que in _MEDIDAS_ORDENO:
        for ayer, item, bloque, alias, cat, dia in ((True, item_ayer, "ord_ayer", "oy", _ORD_AYER, "el día anterior a «Calcular al»"), (False, item_hoy, "ord_hoy", "oh", _ORD_HOY, "el día de «Calcular al»")):
            for n in _NUMEROS:
                expr = (_si_no(f"ISNULL({alias}.{col}{n}, 0) = 1") if col == "forz" else f"CONVERT(varchar(8), {alias}.{col}{n}, 108)" if col == "idt" else f"{alias}.{col}{n}")
                extra = dict(valores=SI_NO, agrupable=True) if col == "forz" else {}
                campos.append(_variante(item, "MilkingNumber", n, f"{nombre} · ordeño {n} de {'ayer' if ayer else 'hoy'}", cat, tipo, expr, bloque=bloque, unidad=unidad, decimales=dec, **extra,
                                        nota=f"{que}. Es el ordeño nº {n} de la vaca en {dia} (DelPro: MilkingNumber = {n}); en una copia de la base es el «{'ayer' if ayer else 'hoy'}» de los datos. "
                                             "Falta compararlo con un informe de DelPro"))
    return campos


def _campos_por_sesion() -> list:
    """La leche de cada sesión de la sala (1 a 3), de ayer y de hoy. Una vaca que faltó a una sesión tiene su ordeño en la siguiente: la sesión no es el número de ordeño."""
    return [_variante(item, "SessionNumber", k, f"Leche de la sesión {k} de {'ayer' if ayer else 'hoy'}", _ORD_SES, "num", f"{alias}.sesion{k}", bloque=bloque, unidad="kg", decimales=2,
                      nota=f"la suma de los kg de los ordeños con leche de la sesión {k} de la sala (SessionMilkYield.SessionNo = {k}) en {dia} (DelPro: SessionNumber = {k}); "
                           "falta compararla con un informe de DelPro")
            for ayer, item, bloque, alias, dia in ((True, "YieldYesterdayForAllMilkingInSession", "ord_ayer", "oy", "el día anterior a «Calcular al»"),
                                                    (False, "YieldTodayForAllMilkingInSession", "ord_hoy", "oh", "el día de «Calcular al»"))
            for k in _NUMEROS]


CATALOGO.update({c.id: c for c in (
    # ---- Sí/no y fechas que salen de punteros o de columnas
    Campo("IsExited", "Ya salió del tambo", _GEN, "txt", _si_no("b.ExitDate IS NOT NULL"), agrupable=True, valores=SI_NO, verificado=False,
          nota="la fórmula de DelPro: NOT IsNull(ExitDate). Un animal que ya salió solo aparece con «Incluir animales descartados»; " + _SIN_COMPARAR),
    Campo("PossiblyInWrongGroup", "Posiblemente en el rodeo equivocado", _ORD, "txt", _si_no("lv.CurrentGroup IS NOT NULL AND lv.VisitedInGroup IS NOT NULL AND lv.CurrentGroup <> lv.VisitedInGroup"),
          bloque="ult_visita", agrupable=True, valores=SI_NO, verificado=False,
          nota="la visita de su último ordeño (la fórmula de DelPro): «Sí» si el rodeo de la vaca al pasar (CMSDeviceVisit.CurrentGroup) no es el que se ordeñaba (VisitedInGroup). DelPro no guarda "
               "el resultado en la base: esa comparación es una lectura de los datos (2 % de las últimas visitas) y falta confirmarla con un informe de DelPro; sin rodeo registrado es «No»"),
    Campo("MilkedGroupNumber", "Número del rodeo del último ordeño", _ORD, "num", "lg.Number", bloque="ult_visita", verificado=False,
          nota="el número del rodeo en que pasó en su último ordeño (CMSDeviceVisit.VisitedInGroup), como la fórmula de DelPro; " + _SIN_COMPARAR),
    Campo("CMSDaysSinceLastMilking", "Días desde el último ordeño", _ORD, "num", "DATEDIFF(day, CAST(lm.BeginTime AS date), @HOY)", bloque="ult_visita", unidad="días", verificado=False,
          nota="días entre el día de su último ordeño (el que apunta el índice de DelPro, LatestHistoryIndex.SessionMilk) y «Calcular al»; " + _SIN_COMPARAR),
    Campo("LastIdTime", "Última identificación en la sala", _ORD, "fecha", "li.fecha", bloque="ult_idtime", verificado=False,
          nota="la hora de identificación más nueva de todas sus visitas a la sala (MilkingDeviceVisit.IDTime), también las que no dieron leche; " + _SIN_COMPARAR),
    Campo("LastSessionStartTime", "Inicio de la sesión de ordeño en curso", _ORD, "fecha", "se.inicio", bloque="sesion_actual", verificado=False,
          nota="el inicio de la última sesión de la sala que todavía no terminó (DeviceSessionHistory sin fin): es el MISMO valor para todos los animales, porque la función de DelPro "
               "(dbo.GetLastOngoingSessionStartTime) no mira al animal; vacío si no hay una sesión en curso. En una copia de la base es la sesión que estaba abierta al hacerla"),
    Campo("LastDryOffDate", "Fecha del último secado", _REP, "fecha", "es.DateAndTime", bloque="ult_secado", verificado=False,
          nota="el evento al que apunta el índice de DelPro (LatestHistoryIndex.DryOff); DelPro lo reinicia con cada parto, así que una vaca que no se secó desde su último parto queda sin fecha; " + _SIN_COMPARAR),
    _variante("LatestEventDate", "ObjectType", 99, "Fecha de la última inseminación (todas las lactancias)", _REP, "fecha", "lie.fecha", bloque="insem_todas",
              nota="la fecha del último evento de inseminación del animal, de cualquier lactancia (DelPro: LatestEventDate con el tipo de evento 99, EventInsemination); a diferencia de «Fecha de la "
                   "última inseminación», que DelPro reinicia con cada parto; " + _SIN_COMPARAR),
    Campo("PregCheckDue", "Control de gestación por hacer", _REP, "txt",
          _si_no("r.ExpectedPregnancyCheckDate IS NOT NULL AND rs.aviso_diag_act = 1 AND @HOY >= DATEADD(day, -rs.aviso_diag, CAST(r.ExpectedPregnancyCheckDate AS date))"),
          bloque="repro", bloques=("ajustes",), agrupable=True, valores=SI_NO, verificado=False,
          nota="la fórmula de DelPro: tiene fecha prevista de control de gestación y «Calcular al» llegó a esa fecha menos los días de aviso que el tambo tiene configurados en DelPro (aviso previo al "
               "control de gestación, hoy 0), si ese aviso está activo. Se asume que esos días son el ajuste 13 de ReproductionSetting; " + _SIN_COMPARAR),
    Campo("DryOffDue", "Secado por hacer", _REP, "txt",
          _si_no(f"r.LactationNumber > 0 AND rs.aviso_secado_act = 1 AND {_SECADO_ESPERADO} IS NOT NULL AND @HOY >= DATEADD(day, -rs.aviso_secado, CAST({_SECADO_ESPERADO} AS date))"),
          bloque="repro", bloques=_ADEMAS_SECADO, agrupable=True, valores=SI_NO, verificado=False,
          nota="la fórmula de DelPro: tiene fecha de secado esperada y «Calcular al» llegó a esa fecha menos los días de atención antes del secado que el tambo tiene en DelPro (hoy 15), si está "
               "activa. Se asume que esos días son el ajuste 14 de ReproductionSetting; " + _SIN_COMPARAR),

    # ---- Leche: días anteriores y lactancia en curso
    Campo("CMSMilkYieldTotalYesterday", "Leche de ayer, suma de los ordeños", _ORD, "num", "oy.total", bloque="ord_ayer", unidad="kg", decimales=2, verificado=False,
          nota="la suma de los kg de TODOS sus ordeños del día anterior a «Calcular al» (SessionMilkYield); difiere de «Producción de leche de ayer» (el total del registro diario) en ~2 % de las vacas; " + _SIN_COMPARAR),
    Campo("CMSMilkingsTodayCount", "Ordeños de hoy", _ORD, "num", "ISNULL(oh.n, 0)", bloque="ord_hoy", verificado=False,
          nota="los ordeños con leche del día de «Calcular al»; sin ordeños es 0, como el Count() de DelPro. El día en curso suele estar a medio cargar; " + _SIN_COMPARAR),
    Campo("SevenDayAvgDuration", "Duración promedio del ordeño, 7 días", _ORD, "num", "o7.dur", bloque="ord_7d", unidad="s", verificado=False,
          nota="los segundos promedio entre el comienzo y el fin de sus ordeños con leche de los 7 días anteriores a «Calcular al»; DelPro lo muestra como días, minutos y segundos; " + _SIN_COMPARAR),
    Campo("AvgMilkingsPerDayLast7Days", "Ordeños por día, 7 días", _ORD, "num", "CAST(o7.ordenos AS float) / NULLIF(d7.dias, 0)", bloque="ord_7d", bloques=("dias_7d",), decimales=1, verificado=False,
          nota="los ordeños con más de 100 g de los 7 días anteriores a «Calcular al» sobre los días con leche de ese período (la fórmula de DelPro); " + _SIN_COMPARAR),
    _variante("AccumulatedTotalYieldInDIMInCurrentLactation", "DIM", 305, "Leche acumulada hasta los 305 días en ordeño", _ORD, "num", "NULLIF(ac305.total, 0)", bloque="leche_hasta_305", unidad="kg", decimales=2,
              nota="la suma de la leche diaria de la lactancia en curso hasta el día 305 en ordeño, del historial de DelPro (HistoryAnimalDailyData, como dbo.GetTotalYieldInDIMInLactation); "
                   "sin leche es vacío; " + _SIN_COMPARAR),
    _variante("TotalYieldInSelectedDIMInCurrentLactation", "DIM", 305, "Leche del día 305 en ordeño", _ORD, "num", "d305.leche", bloque="leche_dia_305", unidad="kg", decimales=2,
              nota="los kg del registro diario de la lactancia en curso en el día 305 en ordeño (AnimalDaily.DIM = 305, con leche); " + _SIN_COMPARAR),
    _variante("AvgDailyYieldOnDayOffset7Days", "DayOffset", 30, "Promedio de 7 días de la leche, hace 30 días", _ORD, "num", "a30.prom", bloque="prom7_hace_30", unidad="kg", decimales=2,
              nota="el promedio de los 7 días anteriores (AnimalDaily.AvgYieldPrev7d) del registro diario de 30 días antes de «Calcular al», de la lactancia en curso; " + _SIN_COMPARAR),

    # ---- Condición corporal de la cámara
    Campo("BcsValueYesterday", "Condición corporal de ayer (cámara)", _BCS, "num", "bay.valor", bloque="bcs_ayer", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara (BcsDailyData) del día anterior a «Calcular al»; " + _SIN_COMPARAR),
    Campo("DaysSinceLastBcsValue", "Días desde la última condición corporal", _BCS, "num", "DATEDIFF(day, bu.fecha, @HOY)", bloque="bcs_ultimo", unidad="días", tabla="BcsDailyData", verificado=False,
          nota="días entre la última lectura de la cámara y «Calcular al»; " + _SIN_COMPARAR),
    Campo("LastBcsTrendTwoWeeks", "Tendencia de la condición corporal, 2 semanas", _BCS, "num", "bu.t2", bloque="bcs_ultimo", decimales=2, tabla="BcsDailyData", verificado=False,
          nota="el cambio de condición corporal de las últimas 2 semanas en la última lectura de la cámara (BcsDailyData.TrendValueTwoWeeks). DelPro lo llama TrendTwoWeeksReport y no se sabe si muestra este "
               "número o su categoría (0 a 5); " + _SIN_COMPARAR),
    Campo("LastBcsTrendFourWeeks", "Tendencia de la condición corporal, 4 semanas", _BCS, "num", "bu.t4", bloque="bcs_ultimo", decimales=2, tabla="BcsDailyData", verificado=False,
          nota="el cambio de condición corporal de las últimas 4 semanas en la última lectura de la cámara (BcsDailyData.TrendValueFourWeeks). DelPro lo llama TrendFourWeeksReport y no se sabe si muestra este "
               "número o su categoría (0 a 5); " + _SIN_COMPARAR),
    Campo("LastBcsVWPValue", "Condición corporal al terminar la espera voluntaria", _BCS, "num", "bm.vwp", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara que DelPro marca para el fin del período de espera voluntaria (IsValueForLastBcsVWP); " + _SIN_COMPARAR),
    Campo("LastBcsDryOffPrevLactValue", "Condición corporal al secado de la lactancia anterior", _BCS, "num", "bm.secado_prev", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara que DelPro marca para el secado de la lactancia anterior (IsValueForLastBcsDryOffPrevLact); " + _SIN_COMPARAR),
    Campo("BcsFirstInseminationCurrentLactValue", "Condición corporal a la primera inseminación", _BCS, "num", "bm.primera_ia", bloque="bcs_marcados", decimales=1, tabla="BcsDailyData", verificado=False,
          nota="el valor de la cámara que DelPro marca para la primera inseminación de la lactancia en curso (IsValueForFirstInseminationCurrentLactation); " + _SIN_COMPARAR),
    *(Campo(id_, f"Condición corporal a los {n} días del parto", _BCS, "num", f"bd{n}.valor", bloque=f"bcs_dslc{n}", decimales=1, tabla="BcsDailyData", verificado=False,
            nota=f"el valor de la cámara exactamente {n} días después del último parto (el inicio de la lactancia en curso); vacío si ese día no hubo lectura; " + _SIN_COMPARAR)
      for id_, n in (("BcsValueDslcSevenDays", 7), ("BcsValueDslcThirtyDays", 30), ("BcsValueDslcFiftyDays", 50), ("BcsValueDslcNinetyDays", 90), ("BcsValueDslcTwoHundredDays", 200))),

    # ---- Puerta de separación
    Campo("LastCutAreaSortingConfirmed", "Área de la última separación confirmada", _TRA, "num", "ca.SortAreaNumber", bloque="corte_conf", verificado=False,
          nota="el número del área a la que la puerta de separación mandó al animal en su último corte confirmado (el carril del evento lleva a un área; vacío si lo mandó a otra puerta); " + _SIN_COMPARAR),
    Campo("LastCutAreaNameSortingConfirmed", "Nombre del área de la última separación confirmada", _TRA, "txt", "ca.SortAreaName", bloque="corte_conf", verificado=False,
          nota="el nombre del área de su último corte confirmado; " + _SIN_COMPARAR),
    Campo("LastCutGateSortingConfirmed", "Puerta de la última separación confirmada", _TRA, "num", "cg.GateNumber", bloque="corte_conf", verificado=False,
          nota="el número de la puerta de separación (CMSSortGateDevice.GateNumber) donde se hizo su último corte confirmado; " + _SIN_COMPARAR),
    Campo("LastCutGateNameSortingConfirmed", "Nombre de la puerta de la última separación confirmada", _TRA, "txt", "cd.DeviceName", bloque="corte_conf", verificado=False,
          nota="el nombre del equipo de la puerta de su último corte confirmado; " + _SIN_COMPARAR),
    Campo("LastCutReasonSortingConfirmed", "Motivo de la última separación confirmada", _TRA, "num", "cf.motivo", bloque="corte_conf", verificado=False,
          nota="el código del motivo de su último corte confirmado (CMSSortResultEvent.SortDecision: 1 y 2 en la base del 20/11/2026; DelPro lo muestra como número); " + _SIN_COMPARAR),
    Campo("LastAutoSortRuleSortingConfirmed", "Regla de la última separación confirmada", _TRA, "txt", "cr.Name", bloque="corte_conf", verificado=False,
          nota="el nombre de la regla de separación automática de su último corte confirmado; vacío si no fue por una regla; " + _SIN_COMPARAR),
    Campo("CMSHoursSinceLastSort", "Horas desde el último corte de la puerta (48 h)", _TRA, "num",
          "CASE WHEN sg.ultimo > DATEADD(hour, -48, GETDATE()) AND sg.ultimo < GETDATE() THEN DATEDIFF(hour, sg.ultimo, GETDATE()) END", bloque="separacion", unidad="h", verificado=False,
          nota="las horas desde el último corte de la puerta de separación (confirmado o no) hasta AHORA, solo si fue en las últimas 48 horas, como la fórmula de DelPro (que usa Now()); vacío si no. "
               "En una copia de la base casi siempre está vacío; " + _SIN_COMPARAR),
    Campo("CMSMinutesSinceLastSort", "Minutos desde el último corte de la puerta (48 h)", _TRA, "num",
          "CASE WHEN sg.ultimo > DATEADD(hour, -48, GETDATE()) AND sg.ultimo < GETDATE() THEN DATEDIFF(minute, sg.ultimo, GETDATE()) END", bloque="separacion", unidad="min", verificado=False,
          nota="lo mismo que las horas, en minutos (DelPro: DateDiffMinute con Now()); vacío si el último corte fue hace más de 48 horas; " + _SIN_COMPARAR),
    Campo("CMSSortOnce", "Separar una vez", _TRA, "txt", _si_no("so.una_vez = 1"), bloque="sep_una_vez", agrupable=True, valores=SI_NO, verificado=False,
          nota="CMSManualSorting.SortOnce: la vaca está marcada para separarla en el próximo paso por la puerta; " + _SIN_COMPARAR),
    Campo("CMSSortCowForever", "Separar siempre", _TRA, "txt", _si_no("sf.siempre = 1"), bloque="sep_siempre", agrupable=True, valores=SI_NO, verificado=False,
          nota="la fórmula de la función de DelPro dbo.IsForeverCowsToBeSortedToday: su primera regla de separación manual tiene fecha de inicio y no tiene fecha de fin; " + _SIN_COMPARAR),

    # ---- Los ordeños de ayer y de hoy, por número de ordeño (7 medidas × 3 ordeños × 2 días) y por sesión (3 × 2)
    *_campos_por_ordeno(),
    *_campos_por_sesion(),
)})
