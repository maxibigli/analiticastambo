# -*- coding: utf-8 -*-
"""Descripción del esquema DDM (DelPro / DeLaval) que se entrega al modelo
para traducir preguntas en lenguaje natural a T-SQL."""

SCHEMA_DOC = """
Base de datos: DDM (DelPro de DeLaval, gestión de tambo/lechería). SQL Server (T-SQL).
Los datos van desde 2011 hasta la fecha actual. Unidades de leche: kilogramos.

REGLAS GENERALES DEL ESQUEMA:
- En casi todas las tablas, `GCRecord IS NULL` significa registro ACTIVO (no borrado).
  Filtrar siempre con `GCRecord IS NULL` salvo que se pidan registros eliminados.
- Los eventos usan herencia: cada tabla de evento (EventCalving, EventInsemination,
  EventHeat, EventPregCheck, EventExit, EventEntry, EventAbortion, EventCullDecision,
  DiagnosisTreatmentEvent, EventBCS, EventGroupChange, HealthCheckEvent, VetVisitEvent)
  comparte su OID con la tabla base `AbstractAnimalEvent`. Para obtener la fecha y el
  animal de un evento: JOIN AbstractAnimalEvent a ON a.OID = evento.OID, y usar
  a.DateAndTime (datetime) y a.BasicAnimal (FK a BasicAnimal.OID).

TABLAS PRINCIPALES:

BasicAnimal  -- fichas de animales (~10.400 registros)
  OID int PK, Number int (número de animal visible en tambo), Name nvarchar,
  OfficialRegNo nvarchar, BirthDate datetime, ExitDate datetime (NULL = sigue en el rodeo),
  ExitType int, Sex int, Breed int, [Group] int (FK AnimalGroup.OID), BirthWeight float,
  ToBeCulled bit, TransponderID nvarchar, EarTagLeft/EarTagRight nvarchar, GCRecord int.
  Animal activo: ExitDate IS NULL AND GCRecord IS NULL.
  Number = 0 son registros comodín/sin numerar: excluirlos (Number > 0) en rankings
  por animal, y agrupar por OID (no por Number, que puede repetirse).
  Nota: [Group] es palabra reservada, usar corchetes.

AnimalDaily  -- resumen diario por animal (~492.000 filas, 2011..hoy)
  OID int PK, Date date, BasicAnimal int (FK), AnimalGroup int, DIM smallint (días en leche),
  DSLC smallint (días desde último parto), LactationNumber tinyint,
  TotalYield real (kg de leche del día), IsYieldValid bit, Duration real,
  AvgYieldPrev7d real, GCRecord int.
  Para producción diaria del rodeo: SUM(TotalYield) WHERE IsYieldValid = 1 AND GCRecord IS NULL.

SessionMilkYield  -- ordeños individuales por sesión (~628.000 filas)
  OID int PK, BeginTime datetime, EndTime datetime, BasicAnimal int (FK),
  AnimalDaily int (FK), SessionNo tinyint, MilkingNumber tinyint, TotalYield real (kg),
  ExpectedYield real, MilkingDevice int, Destination int,
  AvgConductivity smallint, MaxConductivity smallint, AverageConductivity7Days smallint,
  RelativeConductivity smallint (valor alto >115 puede indicar mastitis),
  AverageBlood smallint, MaxBlood smallint, MilkProductionRate float.

AnimalLactationSummary  -- resumen por lactancia (~13.500 filas)
  OID int PK, Animal int (FK BasicAnimal.OID), LactationNumber tinyint,
  StartDate datetime, EndDate datetime (NULL = lactancia en curso),
  PeakYield float (pico kg/día), DaysToPeak int, MatureEquivalent float,
  HistoryTotalYield int (kg totales de la lactancia), GCRecord int.

AnimalReproductionInfo  -- estado reproductivo actual por animal (~9.900 filas)
  OID int PK, Animal int (FK BasicAnimal.OID), LactationNumber tinyint,
  BreedingState int (código interno), IsInseminated bit, IsPregnant bit,
  IsDryingOff bit, ExpectedPregnancyCheckDate datetime, GCRecord int.
  Preferir los bits (IsPregnant, IsInseminated, IsDryingOff) frente a BreedingState.

AbstractAnimalEvent  -- base de todos los eventos (~351.000 filas)
  OID int PK, DateAndTime datetime, BasicAnimal int (FK), AnimalDaily int,
  LactationNumber tinyint, DSLC int, EventBreedingState int, Comment nvarchar,
  GCRecord int, ObjectType int.

EventInsemination  -- inseminaciones (~41.000): OID (JOIN a AbstractAnimalEvent),
  InsemMethod int, Bull int, InseminationNo int, ConceptionDate datetime.
EventCalving  -- partos (~13.000): OID, CalvingEase int, Calf1..Calf5 int (FK BasicAnimal).
EventHeat  -- celos (~46.000): OID.
EventPregCheck  -- chequeos de preñez (~25.600): OID.
EventExit -- bajas (~3.100): OID. EventEntry -- altas (~10.400): OID.
DiagnosisTreatmentEvent  -- diagnósticos/tratamientos (~106.000):
  OID, Diagnosis int (FK Diagnosis.OID), Treatment int, TreatmentEndDate datetime,
  MilkWithholdEndDate datetime (retiro de leche), DumpMilkEndDate datetime.

Diagnosis  -- catálogo de diagnósticos: OID, Code int, DiagnosisName int, Description nvarchar, Category int, Active bit.
  ¡OJO! Description está VACÍA en esta base. El nombre real del diagnóstico está en
  TextLookupItem: LEFT JOIN TextLookupItem tn ON tn.OID = Diagnosis.DiagnosisName → tn.ItemValue
  (ej.: 'Vacia', 'Alta Mastitis', 'Mastitis 1/4 A.I.', 'Desvasada').

BcsDailyData  -- condición corporal diaria (~119.000 filas, cámara BCS).
MilkTest  -- controles lecheros (mensuales). Hereda de AnimalHistoricalData:
  JOIN AnimalHistoricalData h ON h.OID = MilkTest.OID → h.BasicAnimal (el animal)
  y h.DateAndTime (la fecha del control). Campos: SCC, Fat, Protein, Lactose, Urea.
  ¡OJO! MilkTest.SCC está EN MILES de células/ml (un 300 son 300.000).
  ¡OJO! NO unir con MilkingTestAnimal por OID: los OID colisionan pero
  corresponden a animales distintos (verificado: 0 coincidencias en 9.945 filas),
  y su SampleDateTime está NULL en toda la base.
AnimalGroup  -- grupos: OID int PK, Herd int.

CONSEJOS T-SQL:
- Usar TOP N (no LIMIT). Fechas relativas: DATEADD(day,-30, CAST(GETDATE() AS date)).
- Agrupar por mes: FORMAT(a.DateAndTime,'yyyy-MM') o DATEFROMPARTS(YEAR(x),MONTH(x),1).
- Alias de columnas legibles en español y sin espacios (usar guion_bajo).
- Devolver como máximo ~500 filas (usar TOP 500 si hay riesgo de más).
"""
