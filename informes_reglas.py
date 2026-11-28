"""Reglas «si… entonces…» de un campo calculado: las columnas que el tambo armó en DelPro (`Iif(condición, resultado, condición, resultado, …, resultado si no se cumple ninguna)`).

El texto es el que DelPro guarda en `DDMReportItem.Expression` (el lenguaje de criterios de DevExpress). Se lee con el mismo analizador de los filtros (`informes_delpro.analizar`) y de acá solo puede salir
un `CASE` armado con piezas conocidas:

  * campos del catálogo (su expresión SQL), comparados con `=`, `<>`, `<`, `<=`, `>`, `>=`, «es uno de», «entre» o «con dato»; un campo sí/no puede ser una condición solo;
  * `y`, `o`, `no` y paréntesis;
  * valores escritos como literales seguros (los mismos controles que las condiciones de un informe, `informes_motor._literal`), nunca copiados del texto tal cual.

El resultado es SIEMPRE un texto (un número se escribe como texto y verdadero/falso como «Sí»/«No»). Los vacíos se tratan como en SQL (y en DelPro): un animal sin el dato no cumple ni «≤ 5» ni «no ≤ 5», y si no
cumple ninguna condición le toca el resultado final. La única diferencia conocida con DelPro está escrita en `EN_REGLAS`.

Los `import` de `informes_delpro` y de `informes_motor` van dentro de la función: ellos importan a este módulo.
"""

REGLAS_MAX = 8000          # largo del texto (las del tambo de La Ponderosa miden hasta 2.826)
ANIDADO_MAX = 5            # un «Iif» como resultado de otro: SQL Server admite hasta 10 niveles de CASE

# Un campo cuyo valor DENTRO de una regla de DelPro difiere de lo que muestra LactIA ({} = la expresión del campo). `CurrentDIM`: DelPro (`ReproductionInfo.DIM`) da 0 a un animal sin lactancia y acá
# el campo se muestra vacío a propósito (ver CLAUDE.md, «Tanda 5»); en una regla manda DelPro, que es de donde salió la regla.
EN_REGLAS = {"CurrentDIM": "(CASE WHEN r.LastLactationChangeDate IS NULL THEN 0 ELSE ({}) END)"}

_NULO = "CAST(NULL AS nvarchar(60))"
_MOTIVOS = {"like": "Usa «parecido a», que las reglas todavía no calculan.", "arit": "Las reglas todavía no hacen cuentas."}


class ReglasError(ValueError):
    """Las reglas no se pueden usar. El mensaje se le muestra al usuario."""


def traducir(texto, campos: dict):
    """El texto de unas reglas -> (expresión SQL, ids de los campos usados, sin repetir, resultados posibles en el orden en que aparecen).

    `campos`: {id: Campo} del catálogo (una regla no puede nombrar otro campo calculado). `ReglasError` si algo no se puede."""
    import informes_delpro as dl
    if not isinstance(texto, str) or not texto.strip():
        raise ReglasError("Faltan las reglas.")
    if len(texto) > REGLAS_MAX:
        raise ReglasError(f"Las reglas son demasiado largas (hasta {REGLAS_MAX} caracteres).")
    try:
        arbol = dl.analizar(texto, maximo=REGLAS_MAX)
    except dl.CriterioError as e:
        raise ReglasError(f"No se entienden las reglas: {str(e).replace('el filtro', 'el texto')}.") from None
    if not _es_iif(arbol):
        raise ReglasError("Las reglas se escriben con Iif(condición, resultado, …, resultado si no se cumple ninguna).")
    lector = _Reglas(campos, dl)
    sql = lector.iif(arbol, 1)
    if not lector.usados:
        raise ReglasError("Las reglas tienen que mirar al menos un campo.")
    return sql, lector.usados, lector.resultados


def _es_iif(n) -> bool:
    return n.get("t") == "func" and str(n.get("nombre", "")).lower() == "iif"


class _Reglas:
    def __init__(self, campos, dl):
        self.campos = campos
        self.dl = dl
        self.resolver = dl.resolver_por_nombre(campos)
        self.usados: list = []
        self.resultados: list = []

    # ---------------------------------------------------------------- resultados
    def iif(self, n, nivel) -> str:
        if nivel > ANIDADO_MAX:
            raise ReglasError(f"Hay demasiados «Iif» uno dentro de otro (hasta {ANIDADO_MAX}).")
        args = n["args"]
        if len(args) < 3 or len(args) % 2 == 0:
            raise ReglasError("Cada «Iif» lleva pares de condición y resultado, y después un resultado final: el de cuando no se cumple ninguna.")
        ramas = [f"WHEN {self.condicion(args[i])} THEN {self.resultado(args[i + 1], nivel)}" for i in range(0, len(args) - 1, 2)]
        return "CASE " + " ".join(ramas) + f" ELSE {self.resultado(args[-1], nivel)} END"

    def resultado(self, n, nivel) -> str:
        if _es_iif(n):
            return self.iif(n, nivel + 1)
        if n.get("t") != "lit" or n.get("k") not in ("str", "num", "bool", "null"):
            raise ReglasError("El resultado de una regla tiene que ser un texto, un número, verdadero o falso, u otro «Iif».")
        if n["k"] == "null":
            return _NULO
        if n["k"] == "bool":
            texto = "Sí" if n["v"] else "No"
        elif n["k"] == "num":
            texto = format(float(n["v"]), ".10g")
        else:
            texto = n["v"]
        literal = self.texto_seguro(texto)
        if texto not in self.resultados:
            self.resultados.append(texto)
        return literal

    def texto_seguro(self, texto, etiqueta="El resultado") -> str:
        import informes_catalogo
        return self.literal(informes_catalogo.Campo("resultado", etiqueta, "Reglas", "txt", ""), "es", texto)

    def literal(self, campo, op, valor) -> str:
        """El literal seguro de `informes_motor`, más una regla propia: nada de «@» (el motor reemplaza `@HOY` en el texto de los campos, y una regla es texto de campo)."""
        import informes_motor as im
        if isinstance(valor, str) and "@" in valor:
            raise ReglasError("En un texto de las reglas no se puede usar «@».")
        try:
            return im._literal(campo, op, valor)
        except im.InformeError as e:
            raise ReglasError(str(e)) from None

    # ---------------------------------------------------------------- condiciones
    def condicion(self, n) -> str:
        t = n.get("t")
        if t in ("and", "or"):
            return "(" + f" {t.upper()} ".join(self.condicion(x) for x in n["items"]) + ")"
        if t == "not":
            return f"(NOT {self.condicion(n['x'])})"
        if t == "cmp":
            return self.comparacion(n)
        if t == "in":
            campo = self.campo(n["x"])
            if campo.tipo not in ("num", "txt"):
                raise ReglasError(f"«{campo.etiqueta}» no admite «es uno de».")
            op = "=" if campo.tipo == "num" else "es"
            return f"{self.expr(campo)} IN (" + ", ".join(self.literal(campo, op, self.valor(campo, v)) for v in n["valores"]) + ")"
        if t == "between" or (t == "func" and n["nombre"].lower() == "between" and len(n["args"]) == 3):
            x, a, b = (n["x"], n["a"], n["b"]) if t == "between" else tuple(n["args"])
            campo = self.campo(x)
            if campo.tipo not in ("num", "fecha"):
                raise ReglasError(f"«{campo.etiqueta}» no admite «entre».")
            return f"({self.comparar(campo, '>=', a)} AND {self.comparar(campo, '<=', b)})"
        if t == "isnull":
            campo = self.campo(n["x"])
            return f"{self.expr(campo)} IS {'NOT ' if n['negado'] else ''}NULL"
        if t == "prop":
            campo = self.campo(n)
            if not self.dl._es_si_no(campo):
                raise ReglasError(f"«{campo.etiqueta}» no es un sí/no: una condición compara el campo con un valor.")
            return f"{self.expr(campo)} = N'Sí'"
        if t == "func":
            raise ReglasError(f"Usa «{n['nombre']}», que las reglas todavía no calculan.")
        raise ReglasError(_MOTIVOS.get(t, "Eso no es una condición: cada condición compara un campo con un valor (o es un campo sí/no)."))

    def comparacion(self, n) -> str:
        izq, der, op = n["izq"], n["der"], n["op"]
        if izq.get("t") == "lit" and der.get("t") == "prop":
            izq, der, op = der, izq, self.dl._VUELTA[op]
        return self.comparar(self.campo(izq), op, der)

    def comparar(self, campo, op, lit) -> str:
        valor = self.valor(campo, lit)
        if campo.tipo == "txt":
            if op not in ("=", "<>"):
                raise ReglasError(f"De «{campo.etiqueta}», un texto, solo se puede pedir «igual» o «distinto».")
            return f"{self.expr(campo)} {op} {self.literal(campo, 'es', valor)}"
        if campo.tipo == "fecha":
            return f"CAST({self.expr(campo)} AS date) {op} {self.literal(campo, '=', valor)}"
        return f"{self.expr(campo)} {op} {self.literal(campo, op, valor)}"

    def valor(self, campo, lit):
        """El valor de la regla en los términos del campo de acá (un sí/no es «Sí»/«No», un estado de DelPro es su nombre): la misma traducción de los filtros de DelPro."""
        try:
            return self.dl._valor_de(campo, lit)
        except self.dl._NoTraducible as e:
            raise ReglasError(str(e)) from None

    def campo(self, n):
        if n.get("t") != "prop":
            raise ReglasError(_MOTIVOS.get(n.get("t"), "Cada condición compara un campo con un valor fijo."))
        nombre, id_ = self.resolver(n["path"])
        if id_ is None:
            raise ReglasError(f"El campo «{nombre}» todavía no existe acá.")
        if id_ not in self.usados:
            self.usados.append(id_)
        return self.campos[id_]

    def expr(self, campo) -> str:
        return EN_REGLAS.get(campo.id, "{}").replace("{}", campo.expr)
