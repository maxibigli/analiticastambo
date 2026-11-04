"""Análisis de Producción: cómo viene la producción, por rodeo y por días en leche.

Sale de `AnimalDaily` (una fila por vaca y día, con el rodeo de ESE día, los DEL,
el número de lactancia y los kg del día), que existe igual en la rotativa y en la
convencional: por eso estas consultas no dependen de la sala. Lo que sí depende
de la sala (el flujo de cada ordeño) vive en `sql_flujos_del_dia` de cada módulo
de `salas/`.

Tres reglas que no son obvias (medidas en La Martina y La Ponderosa):

* `BasicAnimal.Number > 0`: la vaca comodín (sin identificar, Number = 0) acumula
  en `AnimalDaily` los kg de TODAS las ordeñadas sin ID. Un día suelto llegó a
  13.409 kg "de una vaca" y llevó el promedio de la banda 0-30 días a 153 kg.
* `IsYieldValid` SÍ es la marca de "total del día completo" de esa vaca, y los kg por
  vaca se calculan SOLO con días válidos. Una vaca sin ese día cerrado tiene un total
  parcial (le faltan ordeños): medido en La Martina, 15-20/10 (días completos), la misma
  vaca da 41,9 kg en Rodeo 1 con el día válido y 27,3 kg sin él. La marca no está
  sesgada: se reparte parejo entre rodeos (80-93% válidos) y tramos de DEL (84-93%). Si
  se promediara todo, cada ordeño faltante en la base (hay días con 640 ordeños de los
  2.900 normales) bajaría los kg por vaca sin que la producción haya cambiado.
  Por eso cada consulta devuelve `vacas` (con leche, para contar el rodeo) y `vacas_v` /
  `kg_v` (solo válidas, para el promedio).
* Un día cuyas vacas válidas son pocas contra las de un día normal está incompleto (el
  último día casi siempre: se cierra de noche; y los días en que la base no recibió
  todos los ordeños). Se manda igual y el frontend lo marca y lo deja fuera de las
  comparaciones, en vez de decidirlo acá con un umbral.
"""
import flujos
from rutina import validar_fecha

# Mismos tramos de DEL que "Flujos de ordeño": si cada pantalla cortara distinto,
# la curva de producción y la de flujo no se podrían leer una al lado de la otra.
BANDAS_DEL = flujos.DEO_BUCKETS
_CASE_DEL = flujos._CASE_DEO          # escribe sobre el alias `d` (AnimalDaily)

# Lactancia en tres clases: primíparas, segunda y tercera en adelante. Es el corte
# que se usa para leer una curva de lactancia (una novilla no llega al pico de una
# multípara). `LactationNumber` 0 cae con las primíparas.
_CASE_LACT = """CASE WHEN d.LactationNumber <= 1 THEN 1
                      WHEN d.LactationNumber = 2 THEN 2 ELSE 3 END"""


# Vacas con el total del día VÁLIDO y sus kg: con esto se calculan los kg por vaca.
_VALIDAS = """SUM(CASE WHEN d.IsYieldValid = 1 THEN 1 ELSE 0 END) AS vacas_v,
               SUM(CASE WHEN d.IsYieldValid = 1 THEN d.TotalYield ELSE 0 END) AS kg_v"""


def sql_ultimo_dia() -> str:
    """Último día con leche registrada (`aaaa-mm-dd`). La ventana se arma hacia
    atrás desde ahí y no desde hoy: las copias de la base vienen con atraso."""
    return ("SELECT CONVERT(varchar(10), MAX(d.[Date]), 120) AS ultimo FROM AnimalDaily d "
            "WHERE d.GCRecord IS NULL AND d.TotalYield > 0")


def _filtro(desde: str, hasta: str) -> str:
    d, h = validar_fecha(desde), validar_fecha(hasta)
    return (f"d.[Date] >= '{d}' AND d.[Date] <= '{h}' AND d.GCRecord IS NULL "
            "AND d.TotalYield > 0")


def sql_por_rodeo_dia(desde: str, hasta: str, grupos_sub: str) -> str:
    """Un renglón por (día, rodeo DEL DÍA): vacas con leche, vacas y kg válidos, DEL promedio.

    `grupos_sub` es `salas.grupos_subquery(tambo)`: los rodeos de ordeñe REALES de este tambo.
    No es opcional: la base DDM la comparten varios tambos (La Ponderosa, Don Germán, SB) y
    sin ese filtro entran sus rodeos y sus vacas (aparecían "VO Don German", "Animals SB")."""
    return f"""
        SELECT CONVERT(varchar(10), d.[Date], 120) AS fecha, g.Name AS grupo, g.Number AS numero,
               COUNT(*) AS vacas, {_VALIDAS}, AVG(CAST(d.DIM AS float)) AS dim_prom
        FROM AnimalDaily d
        JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL AND b.Number > 0
        JOIN AbstractGroup g ON g.OID = d.AnimalGroup AND g.GCRecord IS NULL
        JOIN ({grupos_sub}) gr ON gr.grupo = d.AnimalGroup
        WHERE {_filtro(desde, hasta)}
        GROUP BY d.[Date], g.Name, g.Number
        ORDER BY fecha, g.Number
        OPTION (MAXDOP 1, FORCE ORDER, MAX_GRANT_PERCENT = 20)
    """


def sql_por_del_dia(desde: str, hasta: str, grupos_sub: str) -> str:
    """Un renglón por (día, tramo de DEL, clase de lactancia). El frontend suma los
    días del período que se compare: no hace falta volver a la base para cambiar
    de período ni de clase. `grupos_sub`: ver `sql_por_rodeo_dia`."""
    return f"""
        SELECT CONVERT(varchar(10), d.[Date], 120) AS fecha, {_CASE_DEL} AS banda,
               {_CASE_LACT} AS lact, COUNT(*) AS vacas, {_VALIDAS}
        FROM AnimalDaily d
        JOIN BasicAnimal b ON b.OID = d.BasicAnimal AND b.GCRecord IS NULL AND b.Number > 0
        JOIN ({grupos_sub}) gr ON gr.grupo = d.AnimalGroup
        WHERE {_filtro(desde, hasta)} AND d.DIM IS NOT NULL
        GROUP BY d.[Date], {_CASE_DEL}, {_CASE_LACT}
        ORDER BY fecha
        OPTION (MAXDOP 1, FORCE ORDER, MAX_GRANT_PERCENT = 20)
    """
