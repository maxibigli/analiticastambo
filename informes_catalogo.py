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
    # Fecha del último parto: el último evento de parto (DelPro: Animal.LatestHistoryIndex.Calving.EventDate).
    "parto": Bloque("parto",
                    "LEFT JOIN (SELECT a.BasicAnimal AS animal, MAX(a.DateAndTime) AS fecha FROM EventCalving e "
                    "JOIN AbstractAnimalEvent a ON a.OID = e.OID AND a.GCRecord IS NULL GROUP BY a.BasicAnimal) pa "
                    "ON pa.animal = b.OID"),
    # La lactancia ANTERIOR a la actual (pico y días al pico).
    "lactprev": Bloque("lactprev",
                       "LEFT JOIN AnimalLactationSummary sp ON sp.Animal = b.OID AND sp.GCRecord IS NULL "
                       "AND sp.LactationNumber = r.LactationNumber - 1", requiere=("repro",)),
}

_GEN = "Generalidades de animal"
_ORD = "Ordeño"
_REP = "Reproducción"

CATALOGO = {c.id: c for c in (
    Campo("Number", "Número del animal", _GEN, "txt", "CAST(b.Number AS nvarchar(20))",
          nota="definición de DelPro: Number"),
    Campo("AnimalGroupName", "Nombre del grupo", _GEN, "txt", "ag.Name", bloque="grupo",
          nota="definición de DelPro: Group.GroupName (el rodeo de HOY)"),
    Campo("AnimalGroupNumber", "Número del grupo", _GEN, "num", "ag.Number", bloque="grupo"),
    Campo("CurrentLactationNumber", "Nº Lactación", _GEN, "num", "r.LactationNumber", bloque="repro",
          nota="1665 de 1665 vacas coinciden con AnimalDaily.LactationNumber (30/07/2026)"),
    Campo("CurrentDIM", "Días en ordeño", _GEN, "num", "DATEDIFF(day, r.LastLactationChangeDate, @HOY)", bloque="repro",
          nota="1664 de 1665 coinciden con AnimalDaily.DIM de DelPro (30/07/2026); falta contra un informe de DelPro"),
    Campo("LastCalvingDate", "Fecha del último parto", _REP, "fecha", "pa.fecha", bloque="parto",
          nota="último EventCalving; difiere de AnimalReproductionInfo.LastLactationChangeDate en ~2% de los animales"),
    Campo("CurrentReproductionStatus", "Estado de la reproducción", _REP, "txt",
          "CASE WHEN r.IsDryingOff = 1 THEN 'Seca' WHEN r.IsPregnant = 1 THEN 'Preñada' "
          "WHEN r.IsInseminated = 1 THEN 'Inseminada' WHEN r.Animal IS NULL THEN NULL ELSE 'Abierta' END",
          bloque="repro", verificado=False,
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
)}
