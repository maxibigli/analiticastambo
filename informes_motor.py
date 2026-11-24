"""Compilador y ejecutor de informes personalizables.

Una DEFINICIÓN (un dict, que viene como JSON de la pantalla) se valida contra el catálogo y se traduce a consultas SELECT.
Nunca entra SQL escrito por el usuario: los campos salen de una lista cerrada (`informes_catalogo`), los valores de las
condiciones se validan por tipo y se escriben como literales seguros, y las fórmulas de los campos calculados pasan por un
analizador propio (`informes_formula`). Como `db.run_query` no admite parámetros, esa validación es lo único que separa un valor
de la consulta, y por eso es estricta y está probada.

Definición:

    {"columnas":   [{"campo": "Number", "resumen": "count"}, {"campo": "SevenDayAverage", "resumen": "avg"}, {"campo": "calc_1"}],
     "calculados": [{"id": "calc_1", "etiqueta": "Sólidos (kg/día)", "formula": "[SevenDayAverage] * 0,08",
                     "decimales": 2, "unidad": "kg"}],           # cuentas por animal con los campos del catálogo
     "agrupar_por": ["AnimalGroupName", "CurrentLactationNumber"],  # hasta dos niveles; solo campos agrupables
     "detalle":    true,                                         # false = solo grupos y totales, sin la lista de animales
     "condiciones": [{"campo": "AnimalGroupNumber", "op": "=", "valor": 2}],
     "orden":      {"campo": "Number", "dir": 1},                # 1 ascendente, -1 descendente; opcional
     "incluir_descartados": false,                              # como el tilde de DelPro
     "limite": 2000}                                            # filas de detalle; opcional, hasta LIMITE_MAX

Se arman hasta DOS consultas: el DETALLE (un renglón por animal, con tope) y el RESUMEN (grupos y totales, con ROLLUP). Los
resúmenes siempre salen del resumen y no del detalle: así el total cuenta a TODOS los animales del filtro aunque la lista se
corte en el tope, y un promedio es el promedio de los animales y no un promedio de promedios.
"""
import datetime
import math
import re
import time

import informes_catalogo as cat
import informes_formula

LIMITE_DEFECTO = 2000
LIMITE_MAX = 5000            # el mismo tope que `db.MAX_ROWS`
COLUMNAS_MAX = 40
CONDICIONES_MAX = 5
CALCULADOS_MAX = 10
AGRUPAR_MAX = 2
GRUPOS_MAX = 2000            # filas que se leen de la consulta de resumen
TEXTO_MAX = 60
ETIQUETA_MAX = 40

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_ID_CALCULADO = re.compile(r"^calc_[A-Za-z0-9_]{1,24}$")
_UNIDAD = re.compile(r"^[A-Za-z0-9%/·. ºª-]{0,12}$")
_NUMERO = re.compile(r"^-?\d+(\.\d+)?$")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OPERADORES = {
    "num": ("=", "<>", "<", "<=", ">", ">="),
    "fecha": ("=", "<", "<=", ">", ">="),
    "txt": ("es", "contiene"),
}
# Resúmenes que admite una columna según su tipo (los de DelPro: suma, promedio, mínimo, máximo, conteo).
RESUMENES = {"num": ("sum", "avg", "min", "max", "count"), "fecha": ("min", "max", "count"), "txt": ("count",)}
NOMBRE_RESUMEN = {"sum": "Suma", "avg": "Promedio", "min": "Mínimo", "max": "Máximo", "count": "Conteo"}
_AUTO = object()


class InformeError(ValueError):
    """La definición no es válida (campo desconocido, valor imposible, etc.). El mensaje se le muestra al usuario."""


def _como_lista(x, nombre, maximo):
    if not isinstance(x, list):
        raise InformeError(f"«{nombre}» tiene que ser una lista.")
    if len(x) > maximo:
        raise InformeError(f"«{nombre}»: como mucho {maximo}.")
    return x


def _campo(campos, id_):
    if not isinstance(id_, str) or not _ID.match(id_) or id_ not in campos:
        raise InformeError(f"El campo «{str(id_)[:40]}» no existe en el catálogo.")
    return campos[id_]


def _bloques_de(f) -> list:
    return ([f.bloque] if f.bloque else []) + list(f.bloques)


def _literal(campo, op, valor):
    t = campo.tipo
    if op not in OPERADORES.get(t, ()):
        raise InformeError(f"El operador «{op}» no se puede usar con «{campo.etiqueta}».")
    if t == "num":
        if isinstance(valor, bool):
            raise InformeError(f"El valor de «{campo.etiqueta}» tiene que ser un número.")
        if isinstance(valor, (int, float)):
            if not math.isfinite(valor):
                raise InformeError(f"El valor de «{campo.etiqueta}» no es un número válido.")
            x = float(valor)
        elif isinstance(valor, str) and _NUMERO.match(valor.strip().replace(",", ".")):
            x = float(valor.strip().replace(",", "."))
        else:
            raise InformeError(f"El valor de «{campo.etiqueta}» tiene que ser un número.")
        return format(x, ".10g")
    if t == "fecha":
        if not isinstance(valor, str) or not _FECHA.match(valor):
            raise InformeError(f"El valor de «{campo.etiqueta}» tiene que ser una fecha AAAA-MM-DD.")
        try:
            datetime.date.fromisoformat(valor)
        except ValueError:
            raise InformeError(f"«{valor}» no es una fecha válida.") from None
        return f"CONVERT(date, '{valor}', 23)"
    # texto
    if not isinstance(valor, str):
        raise InformeError(f"El valor de «{campo.etiqueta}» tiene que ser un texto.")
    if len(valor) > TEXTO_MAX:
        raise InformeError(f"El texto es demasiado largo (hasta {TEXTO_MAX} letras).")
    if any(ord(c) < 32 for c in valor) or ";" in valor or "--" in valor or "/*" in valor or "*/" in valor:
        raise InformeError("El texto tiene caracteres que no se pueden usar (; -- /* */ o saltos de línea).")
    lit = valor.replace("'", "''")
    if op == "contiene":
        lit = lit.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
        return f"N'%{lit}%'"
    return f"N'{lit}'"


def _fecha_ref(hoy) -> str:
    """El «hoy» de los datos. None = el reloj de la PC; si no, una fecha AAAA-MM-DD."""
    if hoy is None:
        return "CAST(GETDATE() AS date)"
    if not isinstance(hoy, str) or not _FECHA.match(hoy):
        raise InformeError("La fecha de referencia tiene que ser AAAA-MM-DD.")
    try:
        datetime.date.fromisoformat(hoy)
    except ValueError:
        raise InformeError(f"«{hoy}» no es una fecha válida.") from None
    return f"CONVERT(date, '{hoy}', 23)"


def _calculados(defn, campos) -> dict:
    """Los campos calculados del informe como `Campo` (tipo num), con la fórmula ya traducida a SQL.

    Una fórmula solo puede nombrar campos NUMÉRICOS DEL CATÁLOGO: ni otro campo calculado (no hay ciclos que resolver) ni texto."""
    out: dict = {}
    for cd in _como_lista(defn.get("calculados", []), "calculados", CALCULADOS_MAX):
        if not isinstance(cd, dict):
            raise InformeError("Cada campo calculado necesita su identificador, nombre y fórmula.")
        id_ = cd.get("id")
        if not isinstance(id_, str) or not _ID_CALCULADO.match(id_) or id_ in campos or id_ in out:
            raise InformeError(f"El identificador «{str(id_)[:30]}» de un campo calculado no es válido o está repetido.")
        et = cd.get("etiqueta")
        if not isinstance(et, str) or not et.strip() or len(et.strip()) > ETIQUETA_MAX or any(ord(c) < 32 for c in et):
            raise InformeError(f"El nombre de un campo calculado tiene que tener entre 1 y {ETIQUETA_MAX} letras.")
        et = re.sub(r"\s+", " ", et).strip()
        dec = cd.get("decimales", 2)
        if isinstance(dec, bool) or not isinstance(dec, int) or not 0 <= dec <= 6:
            raise InformeError(f"Los decimales de «{et}» tienen que ser un número entre 0 y 6.")
        unidad = cd.get("unidad", "")
        if not isinstance(unidad, str) or not _UNIDAD.match(unidad):
            raise InformeError(f"La unidad de «{et}» no es válida (hasta 12 letras, números o % / · . -).")
        try:
            sql, usados = informes_formula.traducir(cd.get("formula"), campos)
        except informes_formula.FormulaError as e:
            raise InformeError(f"Campo calculado «{et}»: {e}") from None
        bloques = []
        for u in usados:
            for b in _bloques_de(campos[u]):
                if b not in bloques:
                    bloques.append(b)
        out[id_] = cat.Campo(id_, et, "Calculados", "num", sql, unidad=unidad, decimales=dec,
                             verificado=all(campos[u].verificado for u in usados), nota="calculado", bloques=tuple(bloques))
    return out


def _sql_resumen(f, kind, H) -> str:
    """La agregación SQL de un resumen. Los números se pasan a float: `AVG` de enteros devuelve un entero (el promedio de las
    lactancias 1 y 2 daría 1) y `SUM` de enteros se desborda con sumas grandes."""
    e = H(f.expr)
    if kind == "count":
        return f"COUNT({e})"
    if f.tipo == "fecha":
        return f"{kind.upper()}({e})"
    return f"{kind.upper()}(CAST(({e}) AS float))"


def compilar(defn, campos=None, bloques=None, herd=_AUTO, hoy=None) -> dict:
    """Valida `defn` y arma las consultas.

    Devuelve {"sql": detalle o None, "sql_resumen": resumen o None, "columnas": [meta de cada columna], "agrupar": [meta de cada
    nivel de agrupación], "resumenes": [{"alias": "s0", "columna": "c3", "resumen": "avg"}], "limite": n}.

    `hoy`: el «hoy» de los datos. Las expresiones del catálogo usan la marca `@HOY` para los días en ordeño y los promedios
    de los últimos días; se reemplaza SOLO en el texto del catálogo, nunca dentro de un valor puesto por el usuario."""
    ref = _fecha_ref(hoy)
    H = lambda s: s.replace("@HOY", ref)
    base = cat.CATALOGO if campos is None else campos
    todos = dict(cat.BLOQUES_NUCLEO)
    todos.update(cat.BLOQUES if bloques is None else bloques)
    if not isinstance(defn, dict):
        raise InformeError("La definición del informe no es válida.")
    campos = dict(base)
    campos.update(_calculados(defn, base))

    cols = _como_lista(defn.get("columnas"), "columnas", COLUMNAS_MAX)
    if not cols:
        raise InformeError("El informe necesita al menos una columna.")
    usados, metas, exprs, con_resumen = [], [], [], []
    for i, c in enumerate(cols):
        if not isinstance(c, dict):
            raise InformeError("Cada columna tiene que indicar su campo.")
        f = _campo(campos, c.get("campo"))
        if f.id in usados:
            raise InformeError(f"El campo «{f.etiqueta}» está repetido.")
        usados.append(f.id)
        kind = "none"
        if "resumen" in c:
            kind = c["resumen"]
            if not isinstance(kind, str) or (kind != "none" and kind not in RESUMENES[f.tipo]):
                raise InformeError(f"«{f.etiqueta}» no admite el resumen «{str(kind)[:20]}».")
        if kind != "none":
            con_resumen.append((i, f, kind))
        exprs.append(f"{H(f.expr)} AS c{i}")
        metas.append({"id": f.id, "alias": f"c{i}", "etiqueta": f.etiqueta, "tipo": f.tipo, "unidad": f.unidad,
                      "decimales": f.decimales, "categoria": f.categoria, "verificado": f.verificado,
                      "calculado": f.id not in base, "resumen": kind})

    grupos = []
    for k, g in enumerate(_como_lista(defn.get("agrupar_por", []), "agrupar_por", AGRUPAR_MAX)):
        f = _campo(campos, g)
        if not f.agrupable:
            raise InformeError(f"No se puede agrupar por «{f.etiqueta}»: solo por campos con pocas categorías (rodeo, lactancia...).")
        if any(f.id == x.id for x in grupos):
            raise InformeError(f"Ya se agrupa por «{f.etiqueta}».")
        grupos.append(f)
    metas_grupo = [{"id": f.id, "alias": f"g{k}", "etiqueta": f.etiqueta, "tipo": f.tipo, "decimales": f.decimales}
                   for k, f in enumerate(grupos)]

    detalle = defn.get("detalle", True)
    if not isinstance(detalle, bool):
        raise InformeError("«detalle» tiene que ser verdadero o falso.")
    if not detalle and not grupos and not con_resumen:
        raise InformeError("Un informe sin detalle necesita agrupar o tener al menos una columna con resumen.")

    where = ["b.GCRecord IS NULL", "b.Number > 0"]
    bloques_cond = []
    for cd in _como_lista(defn.get("condiciones", []), "condiciones", CONDICIONES_MAX):
        if not isinstance(cd, dict):
            raise InformeError("Cada condición tiene que indicar campo, operador y valor.")
        f = _campo(campos, cd.get("campo"))
        op = cd.get("op")
        lit = _literal(f, op, cd.get("valor"))
        bloques_cond += _bloques_de(f)
        if f.tipo == "txt":
            where.append(f"{H(f.expr)} {'LIKE' if op == 'contiene' else '='} {lit}")
        else:
            where.append(f"{H(f.expr)} {op} {lit}")

    orden = defn.get("orden")
    if orden is None:
        o_expr, o_dir = H(campos[usados[0]].expr), "ASC"
    else:
        if not isinstance(orden, dict) or orden.get("dir") not in (1, -1) or isinstance(orden.get("dir"), bool):
            raise InformeError("El orden no es válido.")
        f = _campo(campos, orden.get("campo"))
        if f.id not in usados:
            raise InformeError("Solo se puede ordenar por una columna del informe.")
        o_expr, o_dir = H(f.expr), "ASC" if orden["dir"] == 1 else "DESC"

    limite = defn.get("limite", LIMITE_DEFECTO)
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= LIMITE_MAX:
        raise InformeError(f"El límite de filas tiene que estar entre 1 y {LIMITE_MAX}.")

    import rebano
    incluir = defn.get("incluir_descartados", False)
    if not isinstance(incluir, bool):
        raise InformeError("«incluir_descartados» tiene que ser verdadero o falso.")
    h = None if herd is _AUTO else herd
    where.append(rebano.filtro_historico("b", h) if incluir else rebano.filtro("b", h))

    def unir(ids_bloques):
        """Los LEFT JOIN de esos bloques (y de los que ellos requieren), en orden y sin repetir."""
        joins = []

        def uno(id_b, pila=()):
            if not id_b or any(id_b == j for j, _ in joins):
                return
            if id_b not in todos or id_b in pila:
                raise InformeError(f"Falta el bloque de datos «{id_b}» en el catálogo.")
            for req in todos[id_b].requiere:
                uno(req, pila + (id_b,))
            joins.append((id_b, H(todos[id_b].join)))
        for id_b in ids_bloques:
            uno(id_b)
        return "".join(j + "\n" for _, j in joins)

    bloques_grupo = [b for f in grupos for b in _bloques_de(f)]
    clausula_where = "WHERE " + "\n  AND ".join(where)
    gexprs = [H(f.expr) for f in grupos]

    sql = None
    if detalle:
        bloques_det = [b for u in usados for b in _bloques_de(campos[u])] + bloques_cond + bloques_grupo
        sel = exprs + [f"{ge} AS g{k}" for k, ge in enumerate(gexprs)]
        orden_sql = "".join(f"CASE WHEN {ge} IS NULL THEN 1 ELSE 0 END, {ge}, " for ge in gexprs)
        sql = (f"SELECT TOP ({limite + 1}) " + ", ".join(sel) + "\nFROM BasicAnimal b\n" + unir(bloques_det) + clausula_where +
               f"\nORDER BY {orden_sql}CASE WHEN {o_expr} IS NULL THEN 1 ELSE 0 END, {o_expr} {o_dir}")

    sql_resumen, resumenes = None, []
    if grupos or con_resumen:
        sel = []
        for k, ge in enumerate(gexprs):
            sel += [f"{ge} AS g{k}", f"GROUPING({ge}) AS gr{k}"]
        sel.append("COUNT(*) AS n")
        for j, (i, f, kind) in enumerate(con_resumen):
            sel.append(f"{_sql_resumen(f, kind, H)} AS s{j}")
            resumenes.append({"alias": f"s{j}", "columna": f"c{i}", "resumen": kind})
        bloques_res = [b for _, f, _ in con_resumen for b in _bloques_de(f)] + bloques_cond + bloques_grupo
        sql_resumen = ("SELECT " + ", ".join(sel) + "\nFROM BasicAnimal b\n" + unir(bloques_res) + clausula_where +
                       (f"\nGROUP BY ROLLUP({', '.join(gexprs)})" if gexprs else ""))
    return {"sql": sql, "sql_resumen": sql_resumen, "columnas": metas, "agrupar": metas_grupo, "resumenes": resumenes,
            "limite": limite}


_SIMBOLO = {">=": "≥", "<=": "≤"}


def describir_condiciones(defn, campos=None) -> list:
    """Las condiciones de un informe en palabras («Producción 24 h < 30»), para el Excel y la pantalla. Lo que no entiende lo
    omite: es solo texto de ayuda, no valida (para eso está `compilar`)."""
    campos = cat.CATALOGO if campos is None else campos
    if not isinstance(defn, dict):
        return []
    etiquetas = {k: (f.etiqueta, f.tipo) for k, f in campos.items()}
    for cd in defn.get("calculados") or []:
        if isinstance(cd, dict) and isinstance(cd.get("id"), str) and isinstance(cd.get("etiqueta"), str):
            etiquetas[cd["id"]] = (cd["etiqueta"][:ETIQUETA_MAX], "num")
    out = []
    for cd in defn.get("condiciones") or []:
        if not isinstance(cd, dict) or cd.get("campo") not in etiquetas:
            continue
        et, tipo = etiquetas[cd["campo"]]
        op, v = cd.get("op"), cd.get("valor")
        if tipo == "fecha" and isinstance(v, str) and _FECHA.match(v):
            v = f"{v[8:10]}/{v[5:7]}/{v[:4]}"
        v = f"«{v}»" if tipo == "txt" else v
        out.append(f"{et} {_SIMBOLO.get(op, op)} {v}")
    if defn.get("incluir_descartados") is True:
        out.append("Incluye animales descartados")
    agrupar = [etiquetas[g][0] for g in (defn.get("agrupar_por") or []) if isinstance(g, str) and g in etiquetas]
    if agrupar:
        out.append("Agrupado por " + " y luego por ".join(agrupar))
    return out


def _clave_natural(v):
    """Para ordenar grupos: los números como números, el texto sin importar mayúsculas y con los números adentro como números
    («Rodeo 2» antes de «Rodeo 10»), y «sin dato» al final."""
    if v is None:
        return (2, ())
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, (int, float)):
        return (0, (v,))
    trozos = re.split(r"(\d+)", str(v).casefold())
    return (1, tuple((0, int(t)) if t.isdigit() else (1, t) for t in trozos if t != ""))


def _armar_grupos(filas, c):
    """Las filas del resumen (g0, gr0, [g1, gr1,] n, s0, s1...) como grupos por nivel y la fila de totales."""
    ng = len(c["agrupar"])
    grupos, totales = [], None
    for f in filas:
        nivel = sum(1 for k in range(ng) if not f[2 * k + 1])
        res = {r["columna"]: f[2 * ng + 1 + j] for j, r in enumerate(c["resumenes"])}
        item = {"n": f[2 * ng], "resumenes": res}
        if nivel == 0:
            totales = item
        else:
            grupos.append(dict(item, nivel=nivel, claves=[f[2 * k] for k in range(nivel)]))
    grupos.sort(key=lambda g: tuple(_clave_natural(v) for v in g["claves"]))
    if totales is None:
        totales = {"n": 0, "resumenes": {r["columna"]: None for r in c["resumenes"]}}
    return grupos, totales


def ejecutar(defn, tambo, campos=None, bloques=None, run=None, herd=_AUTO, hoy=None) -> dict:
    """Compila y corre el informe contra el tambo.

    {"columnas", "filas", "claves_filas" (la clave de grupo de cada fila, solo si se agrupa), "truncado", "detalle" (si trae la
    lista de animales: un informe «solo resumen» no la trae), "agrupar", "grupos", "totales" (None si no hay resúmenes ni grupos),
    "sql", "segundos"}."""
    if herd is _AUTO:
        import rebano
        herd = rebano.por_defecto(tambo)
    c = compilar(defn, campos, bloques, herd, hoy)
    t0 = time.perf_counter()
    if run is None:
        import db
        run = db.run_query

    def correr(sql, max_rows):
        try:
            return run(sql, tambo=tambo, max_rows=max_rows)
        except Exception as e:
            import db
            if isinstance(e, db.UnsafeQueryError):
                raise InformeError("Algún valor del informe contiene una palabra que no se puede usar en una consulta.") from e
            raise

    filas, claves, truncado = [], None, False
    if c["sql"] is not None:
        # Se pidió UNA fila de más (ver `compilar`): si llega, hay más que el tope y se devuelven solo las primeras.
        r = correr(c["sql"], c["limite"] + 1)
        todas = r["rows"]
        truncado = len(todas) > c["limite"] or bool(r.get("truncated"))
        todas = todas[:c["limite"]]
        n = len(c["columnas"])
        filas = [f[:n] for f in todas]
        if c["agrupar"]:
            claves = [list(f[n:]) for f in todas]
    grupos, totales = [], None
    if c["sql_resumen"] is not None:
        grupos, totales = _armar_grupos(correr(c["sql_resumen"], GRUPOS_MAX)["rows"], c)
    return {"columnas": c["columnas"], "filas": filas, "claves_filas": claves, "truncado": truncado, "detalle": c["sql"] is not None,
            "agrupar": c["agrupar"], "grupos": grupos, "totales": totales, "sql": c["sql"] or c["sql_resumen"],
            "segundos": round(time.perf_counter() - t0, 2)}
