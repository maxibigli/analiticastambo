"""Serie diaria de un informe: la producción de leche por día de los animales del informe, para graficarla en un rango de fechas.

Un informe lista animales HOY; el gráfico los sigue día por día. Se parte de `AnimalDaily` (una fila por animal y día) y se devuelve,
por cada día y cada serie (rodeo del día, lactancia o todo junto), tres números: las vacas con lectura válida, los kg de esas
vacas y los kg de toda la leche medida. La pantalla hace el resto (kg por vaca, total, promedio por semana o por mes, estadísticas):
así cambiar la medida, el resumen, el intervalo o la forma no vuelve a la base, que es lenta.

**Qué animales.** Si el informe tiene condiciones y se pide «solo los animales del informe», la consulta se restringe a los que HOY
las cumplen (`informes_motor.poblacion_sql`, la misma población que filtra la lista). Un animal que ya salió del rebaño no figura
ahí. Si el informe no tiene condiciones NO se restringe: son todas las vacas del tambo con su historia, incluidas las que se vendieron
después, para que el total de cada día sea el que tuvo el tambo.

**Dos formas de contar la leche, a propósito** (ver `balance_leche.py`): los kg «por vaca» y las vacas usan solo lecturas válidas
(`IsYieldValid = 1`: un día completo); el total de kg incluye también las no válidas, que son ordeños reales con el total del día
incompleto, porque esa leche igual fue al tanque y DelPro la cuenta (3 a 4 % de los kg en La Ponderosa).

**El rodeo es el de ese día** (`AnimalDaily.AnimalGroup`), no el de hoy: una vaca que cambió de rodeo cuenta en cada rodeo los días que
estuvo ahí.

Nada del usuario llega a la consulta sin validar: las fechas se validan y se escriben ISO, el resto sale de listas cerradas.
"""
import datetime
import re
import statistics
import time

import informes_motor as im
import rebano

MEDIDAS = ("kg_vaca", "kg_total", "vacas")
RESUMENES = ("avg", "sum", "min", "max")
SERIES = ("rodeo", "lactancia", "ninguna")
INTERVALOS = ("dia", "semana", "mes")
FORMAS = ("linea", "area", "barras")
PERIODOS = ("30", "90", "180", "365", "todo", "fechas")
DIAS_MAX = 731                      # dos años: lo que se grafica, y lo que la consulta puede recorrer
SERIES_MAX = 8                      # las de más leche; el resto se junta en «Otros»
PISO_DIA = 0.6                      # un día con menos de este tanto de la mediana de vacas está a medio cargar (el criterio del 60 % de la app)
MIN_DIAS_PARA_JUZGAR = 7            # con menos días con datos no hay mediana que valga
FILAS_MAX = 20000                   # días x series: 731 x 9 son ~6.600

GRAFICO_DEFECTO = {"medida": "kg_vaca", "resumen": "avg", "serie": "rodeo", "intervalo": "dia", "forma": "linea", "periodo": "90",
                   "desde": None, "hasta": None, "solo_informe": True}
_LISTAS = (("medida", MEDIDAS), ("resumen", RESUMENES), ("serie", SERIES), ("intervalo", INTERVALOS), ("forma", FORMAS), ("periodo", PERIODOS))
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SerieError(ValueError):
    """La configuración del gráfico no es válida. El mensaje se le muestra al usuario."""


def _fecha(valor, nombre) -> datetime.date:
    if not isinstance(valor, str) or not _FECHA.match(valor):
        raise SerieError(f"«{nombre}» tiene que ser una fecha AAAA-MM-DD.")
    try:
        return datetime.date.fromisoformat(valor)
    except ValueError:
        raise SerieError(f"«{valor}» no es una fecha válida.") from None


def validar(g) -> dict:
    """La configuración del gráfico completa y limpia (lo que no conoce lo deja afuera). Falla si algo no es válido: así lo guardado
    siempre se puede volver a correr."""
    if g is None:
        return dict(GRAFICO_DEFECTO)
    if not isinstance(g, dict):
        raise SerieError("La configuración del gráfico no es válida.")
    out = dict(GRAFICO_DEFECTO)
    for clave, opciones in _LISTAS:
        if clave in g:
            v = g[clave]
            if not isinstance(v, str) or v not in opciones:
                raise SerieError(f"«{clave}» no admite «{str(v)[:20]}».")
            out[clave] = v
    if "solo_informe" in g:
        if not isinstance(g["solo_informe"], bool):
            raise SerieError("«solo_informe» tiene que ser verdadero o falso.")
        out["solo_informe"] = g["solo_informe"]
    if out["periodo"] == "fechas":
        d, h = _fecha(g.get("desde"), "desde"), _fecha(g.get("hasta"), "hasta")
        if h < d:
            raise SerieError("La fecha «desde» tiene que ser anterior o igual a «hasta».")
        if (h - d).days + 1 > DIAS_MAX:
            raise SerieError(f"El período es demasiado largo (hasta {DIAS_MAX} días).")
        out["desde"], out["hasta"] = d.isoformat(), h.isoformat()
    else:
        out["desde"] = out["hasta"] = None
    return out


def _hoy_reloj() -> datetime.date:
    return datetime.date.today()


def periodo(g: dict, hoy) -> tuple:
    """(desde, hasta) del gráfico. `hoy` es el «hoy» de los datos (AAAA-MM-DD); None = el reloj de la PC."""
    if hoy is None:
        ref = _hoy_reloj()
    elif isinstance(hoy, str):
        ref = _fecha(hoy, "la fecha de referencia")
    else:
        raise SerieError("La fecha de referencia tiene que ser AAAA-MM-DD.")
    if g["periodo"] == "fechas":
        return datetime.date.fromisoformat(g["desde"]), datetime.date.fromisoformat(g["hasta"])
    n = DIAS_MAX if g["periodo"] == "todo" else int(g["periodo"])
    return ref - datetime.timedelta(days=n - 1), ref


# (id de la serie, nombre, JOIN extra, GROUP BY) de cada forma de dividir la leche
_LACT = "CASE WHEN ad.LactationNumber >= 3 THEN 3 ELSE ad.LactationNumber END"
_EXPR = {
    "rodeo": ("ad.AnimalGroup", "g.Name", "JOIN AbstractGroup g ON g.OID = ag.OID AND g.GCRecord IS NULL\n", "ad.Date, ad.AnimalGroup, g.Name"),
    "lactancia": (_LACT, "NULL", "", f"ad.Date, {_LACT}"),
    "ninguna": ("1", "NULL", "", "ad.Date"),
}


def compilar_serie(informe, grafico, hoy=None, herd=im._AUTO, campos=None, bloques=None) -> dict:
    """Valida y arma la consulta diaria. {"sql", "sql_poblacion" (cuenta los animales del informe, o None), "restringida", "serie", "desde", "hasta"}."""
    g = validar(grafico)
    desde, hasta = periodo(g, hoy)
    informe = informe if isinstance(informe, dict) else {}
    h = None if herd is im._AUTO else herd
    restringida = g["solo_informe"] and bool(informe.get("condiciones"))
    pob = im.poblacion_sql(informe, campos, bloques, herd, hoy) if restringida else None
    sid, snombre, join_g, grupo = _EXPR[g["serie"]]
    cond_herd = rebano.condicion_herd("ag", h)
    sql = (f"SELECT ad.Date AS fecha, {sid} AS sid, {snombre} AS snombre,\n"
           "       COUNT(DISTINCT CASE WHEN ad.IsYieldValid = 1 THEN ad.BasicAnimal END) AS vacas,\n"
           "       SUM(CASE WHEN ad.IsYieldValid = 1 THEN CAST(ad.TotalYield AS float) ELSE 0 END) AS kg_valido,\n"
           "       SUM(CAST(ad.TotalYield AS float)) AS kg_todo,\n"
           "       COUNT(DISTINCT ad.BasicAnimal) AS vacas_todas\n"
           "FROM AnimalDaily ad\n"
           "JOIN BasicAnimal bd ON bd.OID = ad.BasicAnimal AND bd.GCRecord IS NULL AND bd.Number > 0\n"
           "JOIN AnimalGroup ag ON ag.OID = ad.AnimalGroup\n" + join_g +
           "WHERE ad.GCRecord IS NULL AND ad.TotalYield > 0\n"
           f"  AND ad.Date BETWEEN '{desde.isoformat()}' AND '{hasta.isoformat()}'\n" +
           (f"  AND {cond_herd}\n" if cond_herd else "") +
           (f"  AND ad.BasicAnimal IN ({pob})\n" if pob else "") +
           f"GROUP BY {grupo}\nORDER BY ad.Date")
    return {"sql": sql, "sql_poblacion": f"SELECT COUNT(*) AS n FROM ({pob}) AS p" if pob else None, "restringida": restringida,
            "serie": g["serie"], "desde": desde.isoformat(), "hasta": hasta.isoformat()}


def _etiqueta_lactancia(sid) -> str:
    return "Sin dato" if sid is None else ("3ª o más" if sid >= 3 else f"{sid}ª lactancia")


def _sumar(series, etiqueta, sid):
    """Una serie que es la suma, día por día, de varias (un día vacío en todas sigue vacío)."""
    n = len(series[0]["vacas"])
    out = {"id": sid, "etiqueta": etiqueta}
    for campo in ("vacas", "kg_valido", "kg"):
        out[campo] = [None if all(s[campo][i] is None for s in series) else sum(s[campo][i] or 0 for s in series) for i in range(n)]
    return out


def armar(filas, desde: datetime.date, hasta: datetime.date, serie: str, restringida: bool = False) -> dict:
    """Las filas (fecha, sid, snombre, vacas, kg_valido, kg_todo, vacas_todas) en series alineadas con TODOS los días del rango.

    Un día sin datos queda vacío (None), no en cero. Se marcan los días «a medio cargar»: DelPro consolida con atraso y el último día
    suele estar a medias; un gráfico que los dibujara mostraría una caída inventada. Se juzga por las vacas con CUALQUIER lectura, no
    por las válidas: el 25/05/2026 casi la mitad de las vacas quedó con el total del día no válido pero la leche está medida (10.112 kg
    contra ~12.000), y ocultarlo sería esconder un dato real; el 27/07, en cambio, faltan ordeños enteros (8.596 kg contra ~61.000)."""
    n = (hasta - desde).days + 1
    fechas = [(desde + datetime.timedelta(days=i)).isoformat() for i in range(n)]
    pos = {f: i for i, f in enumerate(fechas)}
    por = {}
    for f, sid, nombre, vacas, kg_valido, kg_todo, vacas_todas in filas:
        i = pos.get(str(f)[:10])
        if i is None:
            continue
        clave = "todos" if serie == "ninguna" else (None if sid is None else (int(sid) if serie == "lactancia" else str(sid)))
        s = por.get(clave)
        if s is None:
            if serie == "rodeo":
                etiqueta = nombre or "Sin nombre"
            elif serie == "lactancia":
                etiqueta = _etiqueta_lactancia(clave)
            else:
                etiqueta = "Animales del informe" if restringida else "Todo el tambo"
            s = por[clave] = {"id": str(clave) if clave is not None else "sin_dato", "etiqueta": etiqueta, "_orden": clave,
                              "vacas": [None] * n, "kg_valido": [None] * n, "kg": [None] * n, "_todas": [None] * n}
        s["_todas"][i] = int(vacas_todas or 0)
        s["vacas"][i] = int(vacas or 0)
        s["kg_valido"][i] = float(kg_valido or 0)
        s["kg"][i] = float(kg_todo or 0)
    if serie == "rodeo":
        series = sorted(por.values(), key=lambda s: im._clave_natural(s["etiqueta"]))
    elif serie == "lactancia":
        series = sorted(por.values(), key=lambda s: (s["_orden"] is None, s["_orden"] or 0))
    else:
        series = list(por.values())
    # Cuántas vacas con CUALQUIER lectura hubo cada día en TOTAL (antes de juntar series): sirve para juzgar si el día está completo.
    totales = [None if all(s["_todas"][i] is None for s in series) else sum(s["_todas"][i] or 0 for s in series) for i in range(n)]
    for s in series:
        s.pop("_orden")
        s.pop("_todas")
    con_datos = [t for t in totales if t]
    incompletos = []
    if len(con_datos) >= MIN_DIAS_PARA_JUZGAR:
        piso = PISO_DIA * statistics.median(con_datos)
        incompletos = [fechas[i] for i, t in enumerate(totales) if t is not None and t < piso]
    if len(series) > SERIES_MAX:
        mayores = sorted(series, key=lambda s: -sum(x or 0 for x in s["kg"]))[:SERIES_MAX - 1]
        resto = [s for s in series if all(s is not m for m in mayores)]
        series = [s for s in series if any(s is m for m in mayores)] + [_sumar(resto, "Otros", "otros")]
    return {"fechas": fechas, "series": series, "incompletos": incompletos}


def ejecutar_serie(informe, grafico, tambo, hoy=None, run=None, herd=im._AUTO, campos=None, bloques=None) -> dict:
    """Compila y corre la serie contra el tambo. {"desde", "hasta", "fechas", "series", "incompletos", "poblacion", "segundos"}."""
    if herd is im._AUTO:
        herd = rebano.por_defecto(tambo)
    c = compilar_serie(informe, grafico, hoy, herd, campos, bloques)
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
                raise im.InformeError("Algún valor del informe contiene una palabra que no se puede usar en una consulta.") from e
            raise

    filas = correr(c["sql"], FILAS_MAX)["rows"]
    animales = None
    if c["sql_poblacion"]:
        r = correr(c["sql_poblacion"], 1)["rows"]
        animales = int(r[0][0]) if r else 0
    datos = armar(filas, datetime.date.fromisoformat(c["desde"]), datetime.date.fromisoformat(c["hasta"]), c["serie"], c["restringida"])
    datos.update({"desde": c["desde"], "hasta": c["hasta"], "poblacion": {"restringida": c["restringida"], "animales": animales},
                  "segundos": round(time.perf_counter() - t0, 2)})
    return datos
