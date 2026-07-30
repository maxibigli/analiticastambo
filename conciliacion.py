# -*- coding: utf-8 -*-
"""Conciliación de los lotes de alimentación con los grupos de DelPro.

El costo de alimento se mide por LOTE (lo que el mixer descarga en un corral) y
la producción se mide por ANIMAL, que en DelPro vive en un GRUPO. Para pasar de
uno al otro hace falta saber qué lote alimenta a qué grupo. Los dos sistemas se
cargan por separado y no coinciden solos: al 26/07/2026 el proveedor tenía 1.219
cabezas repartidas en 4 lotes y DelPro 1.621 en 9 grupos de ordeñe.

POR QUÉ ESTO VA PRIMERO Y POR QUÉ NO SE ADIVINA. Si un rodeo se mapea mal, el
costo por vaca igual da un número plausible: nadie se entera de que está mal. Un
error acá no se ve, se propaga. Así que la aplicación NO deduce el mapeo: lo
propone, el tambo lo confirma una vez, queda guardado, y de ahí en más se avisa
cuando las cabezas de los dos lados difieren de más.

CARDINALIDAD: un lote alimenta a UNO O VARIOS grupos, y cada grupo pertenece a
UN SOLO lote. Es lo que pasa de verdad —el TMR se entrega a un corral que puede
contener más de un grupo de DelPro— y es lo único que deja el reparto de costo
bien definido: si un grupo estuviera en dos lotes, su costo sería ambiguo. Por
eso `guardar_mapeo` rechaza el grupo repetido en vez de resolver el empate solo.

DE DÓNDE SALEN LAS CABEZAS. De la membresía actual (`BasicAnimal.[Group]`), no
de `AnimalDaily`. Medido el 26/07/2026: `AnimalDaily` está completo solo hasta
el 21/07 (el 22 trae 420 filas y del 23 al 25 quedan restos de 20 a 40) y además
SOLO cubre vacas en ordeñe — para secas, recría y crianza no hay ninguna fila.
La membresía actual, en cambio, existe para los 25 grupos del rebaño y es lo que
muestra DelPro en pantalla. `AnimalDaily` queda como dato secundario, del último
día completo, para ver si el grupo se movió mucho.

QUÉ ALERTA Y QUÉ NO. Solo los grupos de ORDEÑE con animales y sin lote levantan
alerta (hoy: Rodeo 5 con 347 y Rodeo 9 con 65 = 412 vacas cuyo alimento no se le
puede imputar a nadie). Crianza y Recría suman 2.628 cabezas que ningún mixer de
este tambo carga: si alertaran, el ruido taparía lo único accionable. Igual se
listan y se pueden mapear, y el tambo puede silenciar un grupo de ordeñe a mano
si de verdad no se alimenta desde el proveedor.
"""
import collections
import json
import os
import re
import threading
import unicodedata

import rebano

# --- Lado DelPro -------------------------------------------------------------


def sql_grupos(grupos_sql: str, herd=None) -> str:
    """Todos los grupos del rebaño con sus cabezas activas.

    OJO con las dos claves: `AbstractGroup.Number` es el número que MUESTRA
    DelPro y `OID` es con el que se relacionan las tablas (`BasicAnimal.[Group]`
    apunta al OID). El mapeo se guarda por OID —el número se puede reasignar—,
    el número es solo para que el tambo reconozca el grupo.

    `AnimalGroup` comparte OID con `AbstractGroup` (herencia XPO), así que el
    filtro por rebaño se puede aplicar directo sobre `AnimalGroup.Herd` sin el
    EXISTS que usan los otros módulos.

    `grupos_sql`: de `salas.grupos_subquery(tambo)` — qué [Group] son de
    ordeñe real para ESTE tipo de sala. NUNCA se hardcodea CMSGroupMilkSetting
    acá: esa tabla es propia del controlador de una rotativa y una sala
    convencional (San José) no la tiene (ver salud.py, mismo patrón).
    """
    cond = rebano.condicion_herd("ag", herd)
    where_herd = f"AND {cond}" if cond else ""
    return f"""
        SELECT ag.OID AS oid, g.Number AS numero, g.Name AS nombre, ag.Herd AS rebano,
               (SELECT COUNT(*) FROM BasicAnimal b
                 WHERE b.[Group] = ag.OID AND b.GCRecord IS NULL
                   AND b.ExitDate IS NULL AND b.Number > 0) AS cabezas,
               CASE WHEN gr.grupo IS NOT NULL THEN 1 ELSE 0 END AS es_ordene
        FROM AnimalGroup ag
        JOIN AbstractGroup g ON g.OID = ag.OID AND g.GCRecord IS NULL
        LEFT JOIN ({grupos_sql}) gr ON gr.grupo = ag.OID
        WHERE 1 = 1 {where_herd}
        ORDER BY g.Number
    """


def sql_ultimo_cambio_grupo(herd=None) -> str:
    """Cuándo se registró la última mudanza de grupo. Es la frescura real del
    lado DelPro para esta pantalla: si el último cambio es de hace tres días,
    las cabezas que se muestran son las de hace tres días."""
    return f"""
        SELECT MAX(a.DateAndTime) AS ultimo
        FROM EventGroupChange e
        JOIN AbstractAnimalEvent a ON a.OID = e.OID
        WHERE a.GCRecord IS NULL
          AND {rebano.filtro_por_animal("a.BasicAnimal", herd)}
    """


def sql_dias_animaldaily(herd=None, dias: int = 30) -> str:
    """Animales con registro diario por día, para elegir el último día COMPLETO.

    No alcanza con `MAX(Date)`: los últimos días vienen a medio cargar (el 25/07
    tenía 29 filas contra las ~1.600 de un día normal) y tomarlos como si
    estuvieran completos haría ver un tambo que se vació de un día para el otro.
    """
    cond = rebano.condicion_herd("ag", herd)
    where_herd = f"AND {cond}" if cond else ""
    return f"""
        SELECT CAST(ad.Date AS date) AS fecha,
               COUNT(DISTINCT ad.BasicAnimal) AS animales
        FROM AnimalDaily ad
        JOIN AnimalGroup ag ON ag.OID = ad.AnimalGroup
        WHERE ad.GCRecord IS NULL
          AND ad.Date >= DATEADD(day, -{int(dias)}, CAST(GETDATE() AS date))
          {where_herd}
        GROUP BY ad.Date
        ORDER BY ad.Date DESC
    """


# Un día se considera completo si llega a esta fracción del día más cargado de
# la ventana. Con 1.604 animales de máximo, el corte queda en ~800: los días a
# medio cargar (420, 39, 23) quedan afuera y los normales adentro.
FRACCION_DIA_COMPLETO = 0.5


def ultimo_dia_completo(data) -> dict:
    """{fecha, animales, parciales} del último día con datos completos."""
    filas = [(f[0], int(f[1] or 0)) for f in (data.get("rows") or [])]
    if not filas:
        return {"fecha": None, "animales": None, "parciales": 0}
    tope = max(a for _f, a in filas)
    minimo = tope * FRACCION_DIA_COMPLETO
    # `filas` viene de más reciente a más viejo: el primero que pasa el corte es
    # el último día completo, y los que quedaron antes son los parciales.
    for i, (fecha, animales) in enumerate(filas):
        if animales >= minimo:
            return {"fecha": fecha, "animales": animales, "parciales": i}
    return {"fecha": None, "animales": None, "parciales": len(filas)}


def grupos_de(data) -> list:
    """Filas de `sql_grupos` como diccionarios."""
    idx = {c: i for i, c in enumerate(data["columns"])}
    return [{
        "oid": int(f[idx["oid"]]),
        "numero": f[idx["numero"]],
        "nombre": (f[idx["nombre"]] or "").strip(),
        "cabezas": int(f[idx["cabezas"]] or 0),
        "es_ordene": bool(f[idx["es_ordene"]]),
    } for f in data["rows"]]


# --- Mapeo guardado ----------------------------------------------------------
# Archivo fuera de git, como `parametros_reproductivos.json` y `usuarios.json`:
# es estado propio de cada instalación. CONSECUENCIA: el mapeo hay que definirlo
# en el servidor de producción; el que se cargue en una PC de desarrollo no
# viaja con el `git pull`.
_RUTA = os.path.join(os.path.dirname(__file__), "conciliacion_grupos.json")
_lock = threading.Lock()

UMBRAL_PCT = 5          # % de diferencia de cabezas tolerado por lote
UMBRAL_CABEZAS = 10     # ...o esta cantidad absoluta, lo que sea más permisivo


def _leer() -> dict:
    try:
        with open(_RUTA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def mapeo_de(tambo: str) -> dict:
    """Lo guardado para un tambo, con los valores por defecto ya puestos."""
    guardado = _leer().get(tambo) or {}
    return {
        "lotes": guardado.get("lotes") or [],
        "grupos_sin_alimentacion": guardado.get("grupos_sin_alimentacion") or [],
        "umbral_pct": guardado.get("umbral_pct", UMBRAL_PCT),
        "umbral_cabezas": guardado.get("umbral_cabezas", UMBRAL_CABEZAS),
        "actualizado": guardado.get("actualizado"),
        "por": guardado.get("por"),
    }


def _validar(lotes, sin_alimentacion) -> tuple[list, list]:
    """Normaliza y valida el mapeo. Lanza ValueError con un texto que se le
    puede mostrar al usuario tal cual."""
    limpios, vistos = [], {}
    for entrada in lotes or []:
        nombre = (entrada.get("lote") or "").strip()
        if not nombre:
            raise ValueError("Hay un lote sin nombre.")
        grupos = []
        for g in entrada.get("grupos") or []:
            try:
                oid = int(g)
            except (TypeError, ValueError):
                raise ValueError(f"Grupo inválido en el lote {nombre!r}: {g!r}")
            # Un grupo en dos lotes dejaría su costo ambiguo: se rechaza en vez
            # de elegir uno por orden de aparición.
            if oid in vistos and vistos[oid] != nombre:
                raise ValueError(
                    f"El grupo {oid} está asignado a dos lotes ({vistos[oid]} y "
                    f"{nombre}). Cada grupo puede pertenecer a un solo lote.")
            vistos[oid] = nombre
            if oid not in grupos:
                grupos.append(oid)
        limpios.append({"lote": nombre, "grupos": grupos,
                        "nota": (entrada.get("nota") or "").strip()})

    nombres = [e["lote"] for e in limpios]
    repetido = next((n for n in nombres if nombres.count(n) > 1), None)
    if repetido:
        raise ValueError(f"El lote {repetido!r} aparece más de una vez.")

    silenciados = []
    for g in sin_alimentacion or []:
        try:
            oid = int(g)
        except (TypeError, ValueError):
            raise ValueError(f"Grupo inválido en la lista de no alimentados: {g!r}")
        if oid in vistos:
            raise ValueError(
                f"El grupo {oid} está marcado como 'no se alimenta desde acá' y a "
                f"la vez asignado al lote {vistos[oid]!r}.")
        if oid not in silenciados:
            silenciados.append(oid)
    return limpios, silenciados


def guardar_mapeo(tambo: str, datos: dict, usuario: str = None, ahora: str = None) -> dict:
    """Guarda el mapeo del tambo. `datos` acepta las mismas claves que devuelve
    `mapeo_de`; las que no vengan se dejan como estaban."""
    datos = datos or {}
    actual = mapeo_de(tambo)
    lotes = datos["lotes"] if "lotes" in datos else actual["lotes"]
    silencio = (datos["grupos_sin_alimentacion"] if "grupos_sin_alimentacion" in datos
                else actual["grupos_sin_alimentacion"])
    lotes, silencio = _validar(lotes, silencio)

    def _umbral(clave, defecto, tope):
        if clave not in datos or datos[clave] in (None, ""):
            return actual[clave]
        try:
            n = float(datos[clave])
        except (TypeError, ValueError):
            raise ValueError(f"Valor inválido para {clave}: {datos[clave]!r}")
        if not (0 <= n <= tope):
            raise ValueError(f"Valor fuera de rango para {clave}: {n}")
        return n

    with _lock:
        todo = _leer()
        todo[tambo] = {
            "lotes": lotes,
            "grupos_sin_alimentacion": silencio,
            "umbral_pct": _umbral("umbral_pct", UMBRAL_PCT, 100),
            "umbral_cabezas": _umbral("umbral_cabezas", UMBRAL_CABEZAS, 10000),
            "actualizado": ahora,
            "por": usuario,
        }
        with open(_RUTA, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=1)
    return mapeo_de(tambo)


def lote_de_grupo(tambo: str) -> dict:
    """{oid_grupo: nombre_de_lote}. Es la puerta de entrada para las pantallas
    de costo: con esto saben a qué lote imputarle cada animal."""
    return {oid: e["lote"] for e in mapeo_de(tambo)["lotes"] for oid in e["grupos"]}


def grupos_de_lote(tambo: str) -> dict:
    """{nombre_de_lote: [oid_grupo, ...]}."""
    return {e["lote"]: list(e["grupos"]) for e in mapeo_de(tambo)["lotes"]}


# --- Sugerencias -------------------------------------------------------------
# Se proponen, NO se aplican. Cada una dice por qué, para que el tambo pueda
# darse cuenta de que está mal antes de aceptarla.

_RE_NUM = re.compile(r"(\d+)")
# Sufijos de tambo que DelPro le agrega a los grupos ('Secas LP', 'Crianza LP')
# y que el proveedor no usa: estorban para comparar nombres.
_SUFIJOS = {"lp", "dg", "sb"}


def _normalizar(texto: str) -> str:
    """'Rodeo 4 - Baja' → 'rodeo 4 baja'. Sin acentos, sin puntuación, sin el
    sufijo del tambo."""
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    partes = [p for p in re.split(r"[^a-z0-9]+", s) if p]
    while partes and partes[-1] in _SUFIJOS:
        partes.pop()
    return " ".join(partes)


def _numero_de(texto: str):
    """El primer número del nombre: 'Rodeo 4 - Baja' → 4. None si no tiene."""
    m = _RE_NUM.search(texto or "")
    return int(m.group(1)) if m else None


# Fuerza de cada criterio: el mapeo que ya declaró el tambo primero, y las
# coincidencias de nombre después.
#
# NO SE SUGIERE POR CANTIDAD DE CABEZAS. Se probó y es puro ruido: con 25 grupos
# y una tolerancia de ±10, el lote "Chiquitas 2 LAP" (73 cabezas) daba CUATRO
# candidatos de 65, 76, 81 y 68, y "Preñadas 5" (234) enganchaba un grupo de 225
# que no tiene nada que ver. Una sugerencia así es una moneda al aire disfrazada
# de respuesta, y es justo el error que esta pantalla existe para evitar. Las
# cabezas se muestran igual en el selector de grupos, para que el tambo compare
# a ojo — pero eligiendo él, no la aplicación.
_CRITERIOS = ("indice_proveedor", "nombre_exacto", "mismo_numero")


def sugerir(grupos: list, lotes: list, ya_asignados: set) -> list:
    """Candidatos grupo↔lote para los lotes que todavía no tienen grupos.

    EL MEJOR CRITERIO NO ES NUESTRO. Haasten guarda en cada lote un
    `associatedMilkerIndex` con el número de grupo del sistema de ordeñe al que
    corresponde, y el tambo ya lo completó para 8 de sus lotes. Eso no es una
    adivinanza de la aplicación: es el mapeo declarado por quien lo sabe. Se usa
    primero y se etiqueta como tal, para que se entienda de dónde salió. Es lo
    que emparejó el lote "Enfermeria" con el grupo "Rodeo 9", que por nombre no
    se hubiera encontrado nunca.

    Después vienen los criterios propios, ambos por nombre: se llaman igual, o
    mismo número de rodeo. Cuando más de un grupo califica con la misma fuerza,
    la sugerencia sale marcada como ambigua CON todos los candidatos — que es
    justo el caso que hay que mirar a mano. Los lotes que no caen en ninguno de
    los tres criterios NO reciben sugerencia: se mapean a mano.
    """
    libres = [g for g in grupos if g["oid"] not in ya_asignados]
    salida = []
    for lote in lotes:
        nombre_lote = (lote.get("lote") or "").strip()
        indice = lote.get("indice_ordene")
        norm_lote, num_lote = _normalizar(nombre_lote), _numero_de(nombre_lote)

        candidatos = []
        for g in libres:
            # `numero` es AbstractGroup.Number, el que muestra DelPro, que es
            # justo con el que Haasten dice relacionarse.
            if indice is not None and g["numero"] == indice:
                candidatos.append((g, "indice_proveedor",
                                   f"Haasten declara este lote como el grupo N° {indice} "
                                   f"de DelPro ({g['nombre']})"))
                continue
            norm_g = _normalizar(g["nombre"])
            if norm_g == norm_lote:
                candidatos.append((g, "nombre_exacto", f"Se llaman igual: {g['nombre']}"))
                continue
            num_g = _numero_de(g["nombre"])
            # Mismo número Y misma primera palabra ('rodeo 4' ↔ 'rodeo 4 baja'),
            # para no emparejar 'Rodeo 2' con 'Piquete 2'.
            if (num_lote is not None and num_g == num_lote
                    and norm_g.split(" ")[0] == norm_lote.split(" ")[0]):
                candidatos.append((g, "mismo_numero", f"Mismo número de rodeo: {g['nombre']}"))
                continue
        if not candidatos:
            continue
        # Se queda solo con los del criterio más fuerte que haya aparecido: si
        # hay uno declarado por Haasten, los parecidos de cabezas sobran.
        mejor = min(_CRITERIOS.index(c[1]) for c in candidatos)
        elegidos = [c for c in candidatos if _CRITERIOS.index(c[1]) == mejor]
        salida.append({
            "lote": nombre_lote,
            "criterio": _CRITERIOS[mejor],
            "candidatos": [{"oid": g["oid"], "nombre": g["nombre"], "numero": g["numero"],
                            "cabezas": g["cabezas"], "criterio": crit, "motivo": motivo}
                           for g, crit, motivo in elegidos],
            # Con más de un candidato la aplicación no elige: lo marca para que
            # lo mire el tambo.
            "ambigua": len(elegidos) > 1,
        })
    return salida


# --- Comparación -------------------------------------------------------------

ESTADO_OK = "ok"
ESTADO_REVISAR = "revisar"
ESTADO_GRAVE = "grave"
ESTADO_SIN_GRUPOS = "sin_grupos"


def _estado(diferencia, pct, umbral_pct, umbral_cabezas) -> str:
    if diferencia is None:
        return ESTADO_SIN_GRUPOS
    if abs(diferencia) <= umbral_cabezas or (pct is not None and abs(pct) <= umbral_pct):
        return ESTADO_OK
    if pct is None or abs(pct) > 2 * umbral_pct:
        return ESTADO_GRAVE
    return ESTADO_REVISAR


def kg_por_lote(consumos: dict) -> dict:
    """{lote: kg descargados} en el período. Es lo que distingue un lote que se
    usa de uno que solo está configurado."""
    salida = collections.defaultdict(float)
    for d in (consumos or {}).get("descargas") or []:
        kg = d.get("kg") or 0
        if kg > 0:
            salida[(d.get("lote") or "").strip()] += kg
    return dict(salida)


def analizar(grupos: list, lotes: list, mapeo: dict, kg_lote: dict = None) -> dict:
    """Cruza los dos lados y arma todo lo que muestra la pantalla.

    `grupos`: salida de `grupos_de`. `lotes`: lo que devuelve el proveedor
    (`[{lote, cabezas, ...}]`, puede venir vacío si no se pudo consultar).
    `kg_lote`: {lote: kg descargados} del período, de `kg_por_lote`.

    UN LOTE QUE NO RECIBE COMIDA NO ES UN LOTE. El mixer de este tambo declara
    24 lotes con ración configurada, pero solo 8 reciben descargas: "Secas" y
    los trece de Chiquitas, Servicio y Preñadas no vieron un kg en cuatro meses.
    Existen como configuración vieja, no como corrales que haya que conciliar.
    Pedirles un grupo generaba catorce alertas de algo que no es un problema, y
    catorce alertas falsas tapan las dos verdaderas. En un tambo real son dos a
    seis lotes de ordeñe más secas, vaquillonas y enfermería: no mucho más.

    Los que sí quedan a la vista son los que recibieron comida O los que el
    tambo ya mapeó — un lote mapeado que dejó de recibir es justamente el caso
    que hay que ver (le pasó a "Rodeo 4" al reorganizar los rodeos).
    """
    umbral_pct = mapeo.get("umbral_pct", UMBRAL_PCT)
    umbral_cabezas = mapeo.get("umbral_cabezas", UMBRAL_CABEZAS)
    por_oid = {g["oid"]: g for g in grupos}
    silenciados = set(mapeo.get("grupos_sin_alimentacion") or [])

    asignado_a, huerfanos = {}, []
    for entrada in mapeo.get("lotes") or []:
        for oid in entrada["grupos"]:
            if oid in por_oid:
                asignado_a[oid] = entrada["lote"]
            else:
                # El grupo se borró o se movió de rebaño después de mapearlo.
                huerfanos.append({"oid": oid, "lote": entrada["lote"]})

    # Un lote puede estar en el mapeo y ya no venir del proveedor (lo borraron),
    # o venir del proveedor y no estar mapeado todavía. Se muestran los dos.
    cabezas_prov = {(l.get("lote") or "").strip(): l for l in lotes}
    nombres = list(cabezas_prov) + [e["lote"] for e in (mapeo.get("lotes") or [])
                                    if e["lote"] not in cabezas_prov]

    filas_lote = []
    for nombre in nombres:
        entrada = next((e for e in (mapeo.get("lotes") or []) if e["lote"] == nombre), None)
        oids = [o for o in (entrada["grupos"] if entrada else []) if o in por_oid]
        del_prov = cabezas_prov.get(nombre)
        cab_prov = del_prov.get("cabezas") if del_prov else None
        cab_delpro = sum(por_oid[o]["cabezas"] for o in oids) if oids else None
        dif = (cab_prov - cab_delpro) if (cab_prov is not None and cab_delpro is not None) else None
        pct = round(dif / cab_delpro * 100, 1) if (dif is not None and cab_delpro) else None
        # Un lote que ya no está en el proveedor pero sí en el mapeo cuenta como
        # activo: hay que mirarlo, no esconderlo.
        tiene_racion = del_prov.get("activo", True) if del_prov else True
        kg_recibidos = (kg_lote or {}).get(nombre)
        # Sin datos de descargas (`kg_lote` en None) no se puede saber si se usa:
        # se asume que sí, que es el comportamiento anterior.
        en_uso = None if kg_lote is None else bool(kg_recibidos)
        # A la tabla principal van los que reciben comida o los que el tambo ya
        # mapeó. El resto es configuración vieja y va a la sección plegada.
        activo = tiene_racion and (en_uso is not False or bool(oids))
        filas_lote.append({
            "lote": nombre,
            "en_proveedor": del_prov is not None,
            "activo": activo,
            "tiene_racion": tiene_racion,
            "en_uso": en_uso,
            "kg_recibidos": round(kg_recibidos) if kg_recibidos else 0,
            "cabezas_proveedor": cab_prov,
            "categoria": (del_prov or {}).get("categoria"),
            "kg_ms_cabeza": (del_prov or {}).get("kg_ms_cabeza"),
            "pct_alimentacion": (del_prov or {}).get("pct_alimentacion"),
            "indice_ordene": (del_prov or {}).get("indice_ordene"),
            "grupos": [{"oid": o, "nombre": por_oid[o]["nombre"],
                        "numero": por_oid[o]["numero"],
                        "cabezas": por_oid[o]["cabezas"]} for o in oids],
            "cabezas_delpro": cab_delpro,
            "diferencia": dif,
            "diferencia_pct": pct,
            "estado": _estado(dif, pct, umbral_pct, umbral_cabezas) if activo else ESTADO_OK,
            "nota": (entrada or {}).get("nota", ""),
        })

    filas_grupo = []
    for g in grupos:
        # Solo los grupos de ordeñe CON animales reclaman lote. Los Rodeos 6, 7
        # y 8 existen con 0 cabezas y no son un problema; Crianza y Recría
        # (2.628 cabezas) no las carga ningún mixer de este tambo.
        silenciado = g["oid"] in silenciados
        falta = (g["es_ordene"] and g["cabezas"] > 0
                 and g["oid"] not in asignado_a and not silenciado)
        filas_grupo.append({**g,
                            "lote": asignado_a.get(g["oid"]),
                            "sin_alimentacion": silenciado,
                            "falta_lote": falta})

    sin_lote = [g for g in filas_grupo if g["falta_lote"]]
    # Afuera quedan los de relleno (sin ración configurada) y los que están
    # configurados pero no reciben comida: ninguno alimenta a nadie, así que no
    # tiene sentido reclamarles un grupo ni contar sus cabezas.
    activos = [f for f in filas_lote if f["activo"]]
    inactivos = [f for f in filas_lote if not f["activo"]]
    lotes_sin_grupo = [f for f in activos if f["en_proveedor"] and not f["grupos"]]
    total_ordene = sum(g["cabezas"] for g in grupos if g["es_ordene"])
    nombres_activos = {f["lote"] for f in activos}
    total_prov = sum(l.get("cabezas") or 0 for l in lotes
                     if (l.get("lote") or "").strip() in nombres_activos)

    alertas = []
    if sin_lote:
        alertas.append({
            "nivel": "grave",
            "texto": (f"{len(sin_lote)} grupo(s) de ordeñe sin lote asignado — "
                      f"{sum(g['cabezas'] for g in sin_lote)} vacas cuyo alimento no se "
                      f"le puede imputar a nadie: "
                      + ", ".join(f"{g['nombre']} ({g['cabezas']})" for g in sin_lote)),
        })
    if lotes_sin_grupo:
        alertas.append({
            "nivel": "grave",
            "texto": (f"{len(lotes_sin_grupo)} lote(s) del proveedor sin grupo asignado: "
                      f"su costo no se reparte a ninguna vaca — "
                      + ", ".join(f["lote"] for f in lotes_sin_grupo)),
        })
    for f in activos:
        if f["estado"] in (ESTADO_REVISAR, ESTADO_GRAVE):
            alertas.append({
                "nivel": "revisar" if f["estado"] == ESTADO_REVISAR else "grave",
                "texto": (f"Lote {f['lote']}: {f['cabezas_proveedor']} cabezas en el "
                          f"proveedor contra {f['cabezas_delpro']} en DelPro "
                          f"({f['diferencia']:+d}"
                          + (f", {f['diferencia_pct']:+.1f}%" if f["diferencia_pct"] is not None else "")
                          + ")."),
            })
    if huerfanos:
        alertas.append({
            "nivel": "revisar",
            "texto": (f"{len(huerfanos)} grupo(s) del mapeo guardado ya no existen en "
                      f"DelPro (se borraron o cambiaron de rebaño). Conviene volver a "
                      f"guardar el mapeo para limpiarlos."),
        })

    return {
        "lotes": filas_lote,
        "grupos": filas_grupo,
        "alertas": alertas,
        "huerfanos": huerfanos,
        # Solo se sugieren los lotes que están en uso: proponerle un grupo a uno
        # que hace meses no recibe comida es inventarle trabajo al tambo.
        "sugerencias": sugerir(grupos,
                               [l for l in lotes
                                if (l.get("lote") or "").strip() in nombres_activos],
                               set(asignado_a) | silenciados),
        "resumen": {
            "grupos": len(grupos),
            "grupos_ordene": sum(1 for g in grupos if g["es_ordene"] and g["cabezas"] > 0),
            "cabezas_delpro_total": sum(g["cabezas"] for g in grupos),
            "cabezas_delpro_ordene": total_ordene,
            "cabezas_proveedor": total_prov,
            "lotes": len([f for f in activos if f["en_proveedor"]]),
            "lotes_inactivos": len(inactivos),
            "grupos_sin_lote": len(sin_lote),
            "cabezas_sin_lote": sum(g["cabezas"] for g in sin_lote),
            "lotes_sin_grupo": len(lotes_sin_grupo),
            "umbral_pct": umbral_pct,
            "umbral_cabezas": umbral_cabezas,
        },
    }
