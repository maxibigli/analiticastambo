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


def _destino(herd) -> str | None:
    """Expresión SQL del rebaño a filtrar. None = sin filtro (todos)."""
    if herd is None:
        return SUB_HERD
    if str(herd).lower() == TODOS:
        return None
    return str(int(herd))


def filtro(alias_animal: str = "b", herd=None) -> str:
    """Condición SQL que deja solo los animales de un rebaño.

    `alias_animal`: alias de la tabla `BasicAnimal` en la consulta. Se usa
    `EXISTS` en vez de un JOIN para poder pegarlo en el WHERE de cualquier
    consulta sin cambiarle los JOIN ni arriesgar duplicar filas.
    `herd`: OID del rebaño, "todos" para no filtrar, o None para el rebaño del
    tambo (el que tiene los grupos de ordeñe).
    """
    destino = _destino(herd)
    if destino is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM AnimalGroup g_tb"
            f" WHERE g_tb.OID = {alias_animal}.[Group] AND g_tb.Herd = {destino})")


def filtro_por_animal(columna_oid: str, herd=None) -> str:
    """Igual que `filtro`, pero cuando en la consulta no hay un alias de
    `BasicAnimal` a mano y solo se tiene el OID del animal (por ejemplo al
    partir de `AbstractAnimalEvent.BasicAnimal`)."""
    destino = _destino(herd)
    if destino is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM BasicAnimal b_tb"
            f" JOIN AnimalGroup g_tb ON g_tb.OID = b_tb.[Group]"
            f" WHERE b_tb.OID = {columna_oid} AND g_tb.Herd = {destino})")


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
