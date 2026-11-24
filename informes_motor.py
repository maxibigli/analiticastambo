"""Compilador y ejecutor de informes personalizables.

Una DEFINICIÓN (un dict, que viene como JSON de la pantalla) se valida contra el catálogo y se traduce a UNA consulta SELECT.
Nunca entra SQL escrito por el usuario: los campos salen de una lista cerrada (`informes_catalogo`) y los valores de las
condiciones se validan por tipo y se escriben como literales seguros. Como `db.run_query` no admite parámetros, esa validación es
lo único que separa un valor de la consulta, y por eso es estricta y está probada.

Definición:

    {"columnas":   [{"campo": "Number"}, {"campo": "AnimalGroupName"}],
     "condiciones": [{"campo": "AnimalGroupNumber", "op": "=", "valor": 2}],
     "orden":      {"campo": "Number", "dir": 1},            # 1 ascendente, -1 descendente; opcional
     "incluir_descartados": false,                          # como el tilde de DelPro
     "limite": 2000}                                        # opcional, hasta LIMITE_MAX
"""
import datetime
import math
import re
import time

import informes_catalogo as cat

LIMITE_DEFECTO = 2000
LIMITE_MAX = 5000            # el mismo tope que `db.MAX_ROWS`
COLUMNAS_MAX = 40
CONDICIONES_MAX = 5
TEXTO_MAX = 60

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_NUMERO = re.compile(r"^-?\d+(\.\d+)?$")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OPERADORES = {
    "num": ("=", "<>", "<", "<=", ">", ">="),
    "fecha": ("=", "<", "<=", ">", ">="),
    "txt": ("es", "contiene"),
}
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


def compilar(defn, campos=None, bloques=None, herd=_AUTO, hoy=None) -> dict:
    """Valida `defn` y arma la consulta. Devuelve {"sql": ..., "columnas": [meta de cada columna], "limite": n}.

    `hoy`: el «hoy» de los datos. Las expresiones del catálogo usan la marca `@HOY` para los días en ordeño y los promedios
    de los últimos días; se reemplaza SOLO en el texto del catálogo, nunca dentro de un valor puesto por el usuario."""
    ref = _fecha_ref(hoy)
    H = lambda s: s.replace("@HOY", ref)
    campos = cat.CATALOGO if campos is None else campos
    todos = dict(cat.BLOQUES_NUCLEO)
    todos.update(cat.BLOQUES if bloques is None else bloques)
    if not isinstance(defn, dict):
        raise InformeError("La definición del informe no es válida.")

    cols = _como_lista(defn.get("columnas"), "columnas", COLUMNAS_MAX)
    if not cols:
        raise InformeError("El informe necesita al menos una columna.")
    usados, metas, exprs = [], [], []
    for i, c in enumerate(cols):
        if not isinstance(c, dict):
            raise InformeError("Cada columna tiene que indicar su campo.")
        f = _campo(campos, c.get("campo"))
        if f.id in usados:
            raise InformeError(f"El campo «{f.etiqueta}» está repetido.")
        usados.append(f.id)
        exprs.append(f"{H(f.expr)} AS c{i}")
        metas.append({"id": f.id, "alias": f"c{i}", "etiqueta": f.etiqueta, "tipo": f.tipo, "unidad": f.unidad,
                      "decimales": f.decimales, "categoria": f.categoria, "verificado": f.verificado})

    where = ["b.GCRecord IS NULL", "b.Number > 0"]
    bloques_usados = [campos[u].bloque for u in usados]
    for cd in _como_lista(defn.get("condiciones", []), "condiciones", CONDICIONES_MAX):
        if not isinstance(cd, dict):
            raise InformeError("Cada condición tiene que indicar campo, operador y valor.")
        f = _campo(campos, cd.get("campo"))
        op = cd.get("op")
        lit = _literal(f, op, cd.get("valor"))
        bloques_usados.append(f.bloque)
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

    joins = []

    def _unir(id_b, pila=()):
        if not id_b or any(id_b == j for j, _ in joins):
            return
        if id_b not in todos or id_b in pila:
            raise InformeError(f"Falta el bloque de datos «{id_b}» en el catálogo.")
        for req in todos[id_b].requiere:
            _unir(req, pila + (id_b,))
        joins.append((id_b, H(todos[id_b].join)))

    for id_b in bloques_usados:
        _unir(id_b)

    import rebano
    incluir = defn.get("incluir_descartados", False)
    if not isinstance(incluir, bool):
        raise InformeError("«incluir_descartados» tiene que ser verdadero o falso.")
    h = None if herd is _AUTO else herd
    where.append(rebano.filtro_historico("b", h) if incluir else rebano.filtro("b", h))

    sql = (f"SELECT TOP ({limite + 1}) " + ", ".join(exprs) + "\nFROM BasicAnimal b\n" +
           "".join(j + "\n" for _, j in joins) +
           "WHERE " + "\n  AND ".join(where) +
           f"\nORDER BY CASE WHEN {o_expr} IS NULL THEN 1 ELSE 0 END, {o_expr} {o_dir}")
    return {"sql": sql, "columnas": metas, "limite": limite}


_SIMBOLO = {">=": "≥", "<=": "≤"}


def describir_condiciones(defn, campos=None) -> list:
    """Las condiciones de un informe en palabras («Producción 24 h < 30»), para el Excel y la pantalla. Lo que no entiende lo
    omite: es solo texto de ayuda, no valida (para eso está `compilar`)."""
    campos = cat.CATALOGO if campos is None else campos
    if not isinstance(defn, dict):
        return []
    out = []
    for cd in defn.get("condiciones") or []:
        if not isinstance(cd, dict) or cd.get("campo") not in campos:
            continue
        f, op, v = campos[cd["campo"]], cd.get("op"), cd.get("valor")
        if f.tipo == "fecha" and isinstance(v, str) and _FECHA.match(v):
            v = f"{v[8:10]}/{v[5:7]}/{v[:4]}"
        v = f"«{v}»" if f.tipo == "txt" else v
        out.append(f"{f.etiqueta} {_SIMBOLO.get(op, op)} {v}")
    if defn.get("incluir_descartados") is True:
        out.append("Incluye animales descartados")
    return out


def ejecutar(defn, tambo, campos=None, bloques=None, run=None, herd=_AUTO, hoy=None) -> dict:
    """Compila y corre el informe contra el tambo. {"columnas": [...], "filas": [[...]], "truncado": bool, "sql": str,
    "segundos": float}."""
    if herd is _AUTO:
        import rebano
        herd = rebano.por_defecto(tambo)
    c = compilar(defn, campos, bloques, herd, hoy)
    t0 = time.perf_counter()
    if run is None:
        import db
        run = db.run_query
    try:
        # Se pidió UNA fila de más (ver `compilar`): si llega, hay más que el tope y se devuelven solo las primeras.
        r = run(c["sql"], tambo=tambo, max_rows=c["limite"] + 1)
    except Exception as e:
        import db
        if isinstance(e, db.UnsafeQueryError):
            raise InformeError("Algún valor del informe contiene una palabra que no se puede usar en una consulta.") from e
        raise
    filas = r["rows"]
    return {"columnas": c["columnas"], "filas": filas[:c["limite"]],
            "truncado": len(filas) > c["limite"] or bool(r.get("truncated")), "sql": c["sql"],
            "segundos": round(time.perf_counter() - t0, 2)}
