# -*- coding: utf-8 -*-
"""Qué animales son del tambo y cuáles son de otro.

La base DDM de La Ponderosa está COMPARTIDA con otros tambos: la tabla `Herd`
tiene tres filas y los animales de los tres conviven en `BasicAnimal`. Se
distinguen por el rebaño de su grupo (`AnimalGroup.Herd`):

    Rebaño 1 → La Ponderosa   (Rodeo 1..9, Secas LP, PreParto LP, Crianza LP,
                               Recria LP, Vq Servicio LP, ...)
    Rebaño 6 → Don Germán     (VO Don German, Secas DG, Crianza DG)
    Rebaño 7 → SB             (Animals SB, Secas SB, Crianza SB)

Cualquier consulta de rodeo que no filtre por rebaño devuelve los tres tambos
juntos. En números: sin filtrar hay 3.253 vacas lactantes, de las cuales 1.168
son de Don Germán y 464 de SB.

NO se hardcodea el número 1. El rebaño del tambo se deduce de dónde están los
grupos de ordeñe (`CMSGroupMilkSetting.EnableMilking = 1`), que es la regla que
ya usa el resto de la aplicación para saber qué grupos son reales. Así, si
mañana cambian los OID o se agrega un rebaño, el filtro sigue apuntando al
tambo correcto sin tocar código.
"""

# Rebaño del tambo: el que contiene más grupos marcados como de ordeñe.
SUB_HERD = """(
    SELECT TOP 1 g_h.Herd
    FROM AnimalGroup g_h
    JOIN CMSGroupMilkSetting c_h ON c_h.[Group] = g_h.OID AND c_h.GCRecord IS NULL
                                AND c_h.EnableMilking = 1
    GROUP BY g_h.Herd
    ORDER BY COUNT(*) DESC
)"""


TODOS = "todos"

# Nombre completo de cada tambo a partir del sufijo que usan sus grupos. Si
# aparece un rebaño nuevo con otro sufijo se muestra el sufijo tal cual, no se
# rompe nada.
NOMBRES = {"LP": "La Ponderosa", "DG": "Don Germán", "SB": "SB"}


def condicion_herd(alias: str = "g_tb", herd=None) -> str | None:
    """Condición SQL sobre `<alias>.Herd`. None = sin filtro (todos los rebaños).

    `herd` acepta:
      - None            → el rebaño deducido (el que tiene los grupos de ordeñe)
      - "todos"         → sin filtro
      - un número       → ese rebaño
      - una lista/tupla → varios rebaños (un tambo puede tener más de uno)

    Los `filtro*()` de abajo la usan con su propio alias dentro del EXISTS. Se
    expone aparte porque hay consultas que YA parten de `AnimalGroup` —la de
    grupos de `conciliacion.py`, por ejemplo— y no necesitan el EXISTS: les
    alcanza con la condición sobre su propia tabla. Sin esto tendrían que
    rearmar la lógica de `SUB_HERD` a mano, que es justo lo que termina en un
    rebaño hardcodeado.
    """
    col = f"{alias}.Herd"
    if herd is None:
        return f"{col} = {SUB_HERD}"
    if isinstance(herd, (list, tuple, set)):
        ids = sorted({int(h) for h in herd})
        if not ids:
            return f"{col} = {SUB_HERD}"
        if len(ids) == 1:
            return f"{col} = {ids[0]}"
        return f"{col} IN (" + ", ".join(str(i) for i in ids) + ")"
    if str(herd).lower() == TODOS:
        return None
    return f"{col} = {int(herd)}"


def _condicion(herd) -> str | None:
    """La condición con el alias que usan los `filtro*()` de este módulo."""
    return condicion_herd("g_tb", herd)


def filtro(alias_animal: str = "b", herd=None) -> str:
    """Condición SQL que deja solo los animales de un rebaño (o de varios).

    `alias_animal`: alias de la tabla `BasicAnimal` en la consulta. Se usa
    `EXISTS` en vez de un JOIN para poder pegarlo en el WHERE de cualquier
    consulta sin cambiarle los JOIN ni arriesgar duplicar filas.
    """
    cond = _condicion(herd)
    if cond is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM AnimalGroup g_tb"
            f" WHERE g_tb.OID = {alias_animal}.[Group] AND {cond})")


def filtro_por_animal(columna_oid: str, herd=None) -> str:
    """Igual que `filtro`, pero cuando en la consulta no hay un alias de
    `BasicAnimal` a mano y solo se tiene el OID del animal (por ejemplo al
    partir de `AbstractAnimalEvent.BasicAnimal`)."""
    cond = _condicion(herd)
    if cond is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM BasicAnimal b_tb"
            f" JOIN AnimalGroup g_tb ON g_tb.OID = b_tb.[Group]"
            f" WHERE b_tb.OID = {columna_oid} AND {cond})")


def filtro_del_animal(columna_oid: str, herd=None) -> str:
    """Como `filtro_por_animal`, pero un animal que YA SE FUE también tiene rebaño: el de su último registro diario.

    `filtro_por_animal` mira el grupo de HOY, y al darse de baja un animal pierde el `[Group]`: toda cuenta sobre el PASADO (las lactancias de 2025, los
    servicios del año...) se quedaba con las vacas que SIGUEN en el tambo y sin las que se fueron, que en buena parte son las que peor andaban. Acá el
    rebaño de un animal es: (1) el de su grupo de hoy, si tiene; (2) si ya no tiene grupo, el del grupo de su ÚLTIMO registro diario
    (`AnimalDaily.AnimalGroup`, con un índice por animal y fecha: es una búsqueda por animal, no un recorrido).

    ESCRITO COMO CONJUNTO (`columna IN (animales del rebaño)`), no como condición que se evalúa fila por fila: el conjunto se arma una sola vez. La primera versión (con `EXISTS ... OR ...`
    sobre cada fila) hacía repetir la búsqueda por cada registro diario o evento: el 26/11/2026 el plan de «Bajas de terneros» la repetía unos 191 millones de veces (la consulta no
    terminaba) y «kg por vaca» de Proyección ordenaba las 697 mil filas de `AnimalDaily`. Escribirlo de nuevo como condición por fila vuelve a romper eso.

    Medido el 25/11/2026 en las copias del 20/11/2026 (`HistoryAnimal`, `AnimalDaily` y `EventGroupChange`):
      * el último registro diario de un animal que SIGUE en un grupo da el mismo rebaño que su grupo de hoy en 8.001 de 8.001 animales de La Ponderosa y en
        1.011 de 1.011 de San José; el último cambio de grupo (`EventGroupChange`) también, y entre los que ya se fueron los dos coinciden en 2.944 de 2.944;
      * cubre 2.944 de los 3.094 animales sin grupo de La Ponderosa (95,2 %) y 1.509 de 1.590 de San José (94,9 %). Los demás son casi todos salidas de tipo 7,
        sin un solo registro diario; los animales borrados de `BasicAnimal` (454 históricos) tampoco figuran en `AnimalDaily`: no se pueden ubicar;
      * NO se usa «alguna vez estuvo en el rebaño»: 1.840 animales de La Ponderosa tienen registros en más de un rebaño (se mudaron; casi todos siguen vivos), y
        contarlos en los dos duplicaría;
      * NO se usa `HistoryAnimal.LastGroup`: en La Ponderosa coincide con el rebaño de hoy solo en el 41,7 % de los animales vivos (en San José sí, 100 %).
    Un animal sin grupo y sin registro diario no tiene rebaño: no cuenta para ninguno.
    """
    cond = _condicion(herd)
    if cond is None:
        return "1 = 1"
    return f"{columna_oid} IN ({_animales_del_rebano(cond)})"


def _animales_del_rebano(cond: str) -> str:
    """Subconsulta con los OID de los animales del rebaño: los de un rodeo suyo HOY y los que ya no tienen rodeo pero cuyo último registro diario fue en uno suyo."""
    return (f"SELECT a_tb.OID FROM BasicAnimal a_tb JOIN AnimalGroup g_tb ON g_tb.OID = a_tb.[Group] WHERE {cond}"
            f" UNION ALL"
            f" SELECT a_tb.OID FROM BasicAnimal a_tb WHERE a_tb.[Group] IS NULL"
            f" AND EXISTS (SELECT 1 FROM AnimalGroup g_tb WHERE {cond} AND g_tb.OID ="
            f" (SELECT TOP 1 d_tb.AnimalGroup FROM AnimalDaily d_tb"
            f" WHERE d_tb.BasicAnimal = a_tb.OID AND d_tb.GCRecord IS NULL AND d_tb.AnimalGroup IS NOT NULL"
            f" ORDER BY d_tb.[Date] DESC, d_tb.OID DESC))")


def filtro_del_ternero(columna_oid: str, herd=None) -> str:
    """Como `filtro_del_animal`, y un ternero que no tiene ni grupo ni registro diario es del rebaño de su MADRE (la vaca del parto en el que nació, `BasicAnimal.BirthEvent`).

    Es el caso de las nacidas muertas (DelPro les pone número -1 y motivo «Death») y de algunos terneros que se van a los pocos días: nunca entran a un grupo. Sin esto la mortandad no contaba NINGUNA
    nacida muerta. Medido el 25/11/2026 en la copia de La Ponderosa del 20/11/2026: de las 142 salidas «Death» ninguna tiene rebaño propio; por la madre, 122 son de La Ponderosa, 7 de SB, 2 de Don
    Germán y 11 no se pueden ubicar. En San José, 79 nacidas muertas, 77 con la madre en el rebaño.
    """
    cond = _condicion(herd)
    if cond is None:
        return "1 = 1"
    return (f"{columna_oid} IN ({_animales_del_rebano(cond)}"
            f" UNION ALL"
            f" SELECT c_tb.OID FROM BasicAnimal c_tb JOIN AbstractAnimalEvent p_tb ON p_tb.OID = c_tb.BirthEvent"
            f" WHERE c_tb.[Group] IS NULL"
            f" AND NOT EXISTS (SELECT 1 FROM AnimalDaily u_tb WHERE u_tb.BasicAnimal = c_tb.OID AND u_tb.GCRecord IS NULL AND u_tb.AnimalGroup IS NOT NULL)"
            f" AND {filtro_del_animal('p_tb.BasicAnimal', herd)})")


def filtro_historico(alias_animal: str = "b", herd=None) -> str:
    """Como `filtro`, pero para animales que YA SALIERON del rodeo.

    Al dar de baja un animal, DelPro le deja el `[Group]` en NULL: de las 1.103
    bajas de los últimos doce meses, las 1.103 quedaron sin grupo. O sea que
    `filtro()` las excluye a todas y cualquier cuenta de salidas da cero.

    Acá se resuelve mirando el historial: se busca si el animal alguna vez
    tuvo un registro diario en un grupo del rebaño. Es más caro que `filtro()`,
    así que se usa solo donde hace falta —consultas de bajas—, no en general.

    OJO: «alguna vez estuvo» cuenta a un animal en CADA rebaño por el que pasó:
    1.840 animales de La Ponderosa se mudaron de rebaño (y casi todos siguen
    vivos en el otro). Para decidir «de qué rebaño es este animal» —lo que
    necesita cualquier cuenta sobre el pasado— usar `filtro_del_animal`.
    """
    cond = _condicion(herd)
    if cond is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM AnimalDaily d_tb"
            f" JOIN AnimalGroup g_tb ON g_tb.OID = d_tb.AnimalGroup"
            f" WHERE d_tb.BasicAnimal = {alias_animal}.OID AND {cond})")


def por_defecto(tambo_id: str):
    """Rebaño(s) que le corresponden a un tambo.

    Si `tambos.py` los declara, se usan esos: es lo correcto y lo explícito.
    Si no, se cae a la deducción (None → el rebaño con los grupos de ordeñe),
    que anda mientras haya un solo tambo ordeñando en la base pero elige uno
    solo, en silencio, si hubiera más. Por eso conviene declararlos.
    """
    import tambos
    declarados = tambos.rebanos_de(tambo_id)
    return declarados or None


SQL_LISTA = """
    SELECT g.Herd AS rebano,
           COUNT(DISTINCT b.OID) AS animales,
           CASE WHEN g.Herd = """ + SUB_HERD + """ THEN 1 ELSE 0 END AS es_el_tambo,
           (SELECT STRING_AGG(CAST(ag2.Name AS nvarchar(MAX)), ' | ')
            FROM AnimalGroup g2
            JOIN AbstractGroup ag2 ON ag2.OID = g2.OID AND ag2.GCRecord IS NULL
            WHERE g2.Herd = g.Herd AND ag2.Name IS NOT NULL) AS grupos,
           (SELECT COUNT(*) FROM AnimalGroup g3
            JOIN CMSGroupMilkSetting c3 ON c3.[Group] = g3.OID AND c3.GCRecord IS NULL
                                       AND c3.EnableMilking = 1
            WHERE g3.Herd = g.Herd) AS grupos_ordeñe
    FROM AnimalGroup g
    LEFT JOIN BasicAnimal b ON b.[Group] = g.OID AND b.GCRecord IS NULL
                           AND b.ExitDate IS NULL AND b.Number > 0
    GROUP BY g.Herd
    ORDER BY animales DESC
"""


def listar(data) -> list:
    """Rebaños de la base, con un nombre legible deducido del sufijo que usan
    sus grupos ('Secas LP', 'Crianza LP' → La Ponderosa).

    Se mira la lista COMPLETA de grupos del rebaño y se elige el sufijo más
    repetido, no un grupo de ejemplo: muchos grupos no llevan sufijo ('Rodeo 1',
    'Vaq a Parir Oct-Nv-Dic') y mirando uno solo se falla.
    """
    filas = [dict(zip(data["columns"], f)) for f in (data.get("rows") or [])]
    out = []
    for f in filas:
        texto = (f.get("grupos") or "").upper()
        # Cuántos grupos del rebaño terminan con cada sufijo conocido.
        conteo = {tok: sum(1 for g in texto.split("|")
                           if g.strip().endswith(" " + tok) or g.strip() == tok)
                  for tok in NOMBRES}
        mejor = max(conteo, key=conteo.get) if conteo else None
        nombre = NOMBRES[mejor] if mejor and conteo[mejor] else None
        out.append({
            "herd": f["rebano"],
            "nombre": nombre or f"Rebaño {f['rebano']}",
            "animales": int(f.get("animales") or 0),
            "es_el_tambo": bool(f.get("es_el_tambo")),
        })
    total = sum(r["animales"] for r in out)
    out.append({"herd": TODOS, "nombre": "Todos los rebaños",
                "animales": total, "es_el_tambo": False})
    return out


SQL_REBANOS = """
    SELECT g.Herd AS rebano,
           COUNT(DISTINCT b.OID) AS activos,
           MIN(ag.Name) AS un_grupo,
           CASE WHEN g.Herd = """ + SUB_HERD + """ THEN 1 ELSE 0 END AS es_el_tambo
    FROM AnimalGroup g
    JOIN AbstractGroup ag ON ag.OID = g.OID AND ag.GCRecord IS NULL
    LEFT JOIN BasicAnimal b ON b.[Group] = g.OID AND b.GCRecord IS NULL
                           AND b.ExitDate IS NULL AND b.Number > 0
    GROUP BY g.Herd
    ORDER BY activos DESC
"""
