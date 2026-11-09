# -*- coding: utf-8 -*-
"""Busca, SOLO LECTURA, qué pudo causar la baja de producción de un día.

Compara un día contra los 7 días anteriores (la "base") y descompone la caída para
separar las causas de manejo más comunes:

  1. ¿Faltaron vacas o rindió menos cada una?   (vacas ordeñadas vs kg por vaca)
  2. ¿Qué vacas se perdieron del ordeñe y cuáles bajaron? (listas, con grupo y DIM)
  3. ¿En qué grupo(s) se concentra la caída?
  4. ¿En qué ordeño (sesión) y a qué hora? Horario corrido, ordeño corto, vacas que no pasaron.
  5. ¿Alguna bajada de bocas/unidades puntuales? (equipo)
  6. ¿Cambió la mezcla del rodeo? (parto reciente, secado, cambios de grupo, bajas, tratamientos)
  7. ¿Hubo estrés calórico (ITH) ese día o el anterior?

Corre donde la app llega a DelPro (SERVER-DELPRO). No escribe nada ni imprime credenciales.

Lo usan dos cosas, con la misma lógica:
  * la tarjeta "¿Por qué cambió la producción?" de Consultas e IA (`iniciar`/`estado`, en
    segundo plano, porque tarda);
  * el script de consola `diagnosticar_baja_produccion.py`.

Se analiza UN día y se lo compara contra un RANGO de días (por defecto, los 7 anteriores).

Es un diagnóstico de PISTAS, no un veredicto: cada sección dice qué mide y qué no. Si dos
secciones apuntan al mismo lugar (por ejemplo "faltaron vacas" + "un solo ordeño corto"),
esa es la causa probable; si ninguna se destaca, se dice.
"""
import datetime
import json
import statistics as st
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict

import db
import rebano

DIAS_BASE = 7            # base por defecto: los 7 días anteriores al analizado
MAX_DIAS_BASE = 31       # más que esto trae demasiadas filas y tarda sin aportar
MIN_DIAS_BASE = 3
# Días de comidas del mixer que se piden para saber cuál es la hora «normal» de la primera comida. Es más que la base
# de producción a propósito: 7 días son pocos para una mediana de horarios, y con 30 un día tardío suelto no la mueve.
HISTORIA_MIXER_DIAS = 30


class DiagnosticoError(Exception):
    """Pedido inválido o consulta que no se puede hacer bien: el mensaje se le muestra al usuario."""


# Salida: cada hilo arma su propio informe (corre en segundo plano y puede haber dos a la vez).
_salida = threading.local()


def _p(texto=""):
    _salida.lineas.append(str(texto))


def _texto():
    return "\n".join(_salida.lineas)


def _z(valor, serie):
    """Cuántos desvíos está `valor` de lo que dio la serie (con signo). Una base sin ninguna variación (todos los
    días iguales, solo pasa con datos de prueba) no puede dividir por cero: cualquier diferencia cuenta como enorme."""
    if len(serie) < 2:
        return 0.0
    media, sd = st.mean(serie), st.pstdev(serie)
    if sd < 1e-9:
        return 0.0 if abs(valor - media) < 1e-9 else (9.9 if valor > media else -9.9)
    return (valor - media) / sd


LIMITE = 12          # filas por lista
UMBRAL_GRUPO = 0.0   # se muestran todos los grupos, ordenados por aporte a la caída


# --------------------------------------------------------------------------- consultas
def _del_rebano(tambo, alias_ad="ad"):
    """Condición: la vaca estaba, ESE día, en un grupo del rebaño del tambo.

    Se mira `AnimalDaily.AnimalGroup` (el grupo del día) y no `BasicAnimal.[Group]` (el actual):
    si hubo cambios de grupo entre el día y hoy, el grupo actual dejaría afuera vacas que sí se
    ordeñaron ese día y la comparación contra la base saldría falsa."""
    cond = rebano.condicion_herd("g_dia", rebano.por_defecto(tambo))
    if cond is None:
        return "1 = 1"
    return (f"EXISTS (SELECT 1 FROM AnimalGroup g_dia WHERE g_dia.OID = {alias_ad}.AnimalGroup AND {cond})")


def sql_vacas(d0, d1, tambo):
    """Una fila por vaca y día: kg, grupo, DIM, lactancia (AnimalDaily)."""
    filtro = _del_rebano(tambo)
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha, b.OID AS animal, b.Number AS numero, ad.AnimalGroup AS grupo,
           ad.DIM AS dim, ad.LactationNumber AS lactancia, ad.TotalYield AS kg,
           ISNULL(ad.IsYieldValid, 0) AS valido
    FROM AnimalDaily ad JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE ad.GCRecord IS NULL AND ad.TotalYield > 0
      AND ad.Date >= '{d0}' AND ad.Date <= '{d1}' AND {filtro}
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_sesiones(d0, d1, tambo):
    """Por día y ordeño (SessionNo): vacas pasadas (filas), kg, primer inicio y último fin, conductividad, caudal."""
    filtro = _del_rebano(tambo)
    desde_t = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=1)).isoformat()
    hasta_t = (datetime.date.fromisoformat(d1) + datetime.timedelta(days=2)).isoformat()
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha, sm.SessionNo AS sesion,
           COUNT(*) AS vacas, SUM(sm.TotalYield) AS kg,
           MIN(sm.BeginTime) AS inicio, MAX(sm.EndTime) AS fin,
           AVG(CAST(sm.AvgConductivity AS float)) AS conduct, AVG(sm.MilkProductionRate) AS caudal
    FROM SessionMilkYield sm
    JOIN AnimalDaily ad ON ad.OID = sm.AnimalDaily AND ad.GCRecord IS NULL
    WHERE sm.BeginTime >= '{desde_t}' AND sm.BeginTime < '{hasta_t}'
      AND ad.Date >= '{d0}' AND ad.Date <= '{d1}' AND sm.TotalYield > 0 AND {filtro}
    GROUP BY ad.Date, sm.SessionNo
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_bocas(d0, d1, tambo):
    """kg por día y unidad de ordeñe (MilkingDevice)."""
    filtro = _del_rebano(tambo)
    desde_t = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=1)).isoformat()
    hasta_t = (datetime.date.fromisoformat(d1) + datetime.timedelta(days=2)).isoformat()
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha, sm.MilkingDevice AS boca, COUNT(*) AS ordenes, SUM(sm.TotalYield) AS kg
    FROM SessionMilkYield sm
    JOIN AnimalDaily ad ON ad.OID = sm.AnimalDaily AND ad.GCRecord IS NULL
    WHERE sm.BeginTime >= '{desde_t}' AND sm.BeginTime < '{hasta_t}'
      AND ad.Date >= '{d0}' AND ad.Date <= '{d1}' AND sm.TotalYield > 0 AND {filtro}
    GROUP BY ad.Date, sm.MilkingDevice
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_eventos(d0, d1, tambo):
    """Cantidad de eventos por día y tipo, del rebaño. Tratamientos con el nombre del diagnóstico."""
    f = rebano.filtro_por_animal("a.BasicAnimal", rebano.por_defecto(tambo))
    return f"""
    SELECT CAST(a.DateAndTime AS date) AS fecha, 'parto' AS tipo, CAST(NULL AS nvarchar(80)) AS detalle, COUNT(*) AS n
      FROM EventCalving e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{d0}' AND a.DateAndTime < DATEADD(day, 1, '{d1}') AND {f}
      GROUP BY CAST(a.DateAndTime AS date)
    UNION ALL
    SELECT CAST(a.DateAndTime AS date), 'cambio de grupo', NULL, COUNT(*)
      FROM EventGroupChange e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{d0}' AND a.DateAndTime < DATEADD(day, 1, '{d1}') AND {f}
      GROUP BY CAST(a.DateAndTime AS date)
    UNION ALL
    SELECT CAST(a.DateAndTime AS date), 'tratamiento', LEFT(tn.ItemValue, 80), COUNT(*)
      FROM DiagnosisTreatmentEvent e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
      LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{d0}' AND a.DateAndTime < DATEADD(day, 1, '{d1}') AND {f}
      GROUP BY CAST(a.DateAndTime AS date), LEFT(tn.ItemValue, 80)
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_tratadas(dia, tambo):
    """Una fila por tratamiento registrado ESE día (el día anterior al analizado): vaca y diagnóstico. Sirve para
    cruzar quién fue tratada contra cuánto rindió al día siguiente."""
    f = rebano.filtro_por_animal("a.BasicAnimal", rebano.por_defecto(tambo))
    return f"""
    SELECT a.BasicAnimal AS animal, LEFT(tn.ItemValue, 80) AS detalle
      FROM DiagnosisTreatmentEvent e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      LEFT JOIN Diagnosis dg ON dg.OID = e.Diagnosis
      LEFT JOIN TextLookupItem tn ON tn.OID = dg.DiagnosisName
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{dia}' AND a.DateAndTime < DATEADD(day, 1, '{dia}') AND {f}
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_bajas(d0, d1, tambo):
    """Bajas (EventExit): el animal ya no tiene grupo, por eso se usa el filtro histórico."""
    f = rebano.filtro_historico("b", rebano.por_defecto(tambo))
    return f"""
    SELECT CAST(a.DateAndTime AS date) AS fecha, 'baja' AS tipo, CAST(NULL AS nvarchar(80)) AS detalle, COUNT(*) AS n
      FROM EventExit e JOIN AbstractAnimalEvent a ON a.OID = e.OID JOIN BasicAnimal b ON b.OID = a.BasicAnimal
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{d0}' AND a.DateAndTime < DATEADD(day, 1, '{d1}') AND {f}
      GROUP BY CAST(a.DateAndTime AS date)
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def sql_retiro(d0, d1, tambo):
    """Vacas con leche de descarte/retiro VIGENTE ese día (la leche no va al tanque): por día."""
    f = rebano.filtro_por_animal("a.BasicAnimal", rebano.por_defecto(tambo))
    return f"""
    SELECT CAST(a.DateAndTime AS date) AS fecha, COUNT(DISTINCT a.BasicAnimal) AS vacas
      FROM DiagnosisTreatmentEvent e JOIN AbstractAnimalEvent a ON a.OID = e.OID
      WHERE a.GCRecord IS NULL AND a.DateAndTime >= '{d0}' AND a.DateAndTime < DATEADD(day, 1, '{d1}')
        AND (e.MilkWithholdEndDate IS NOT NULL OR e.DumpMilkEndDate IS NOT NULL) AND {f}
      GROUP BY CAST(a.DateAndTime AS date)
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


# --------------------------------------------------------------------------- utilidades
def _consumos_mixer(tambo, desde, hasta):
    """(consumos del proveedor de alimentación, motivo). Sin proveedor conectado o con un error devuelve
    `(None, por qué)`: la comida es UNA sección del informe y no puede tumbar el resto."""
    try:
        import proveedores
        prov = proveedores.de(tambo)
        ok, motivo = prov.disponible()
        if not ok:
            return None, motivo
        return prov.consumos(desde, hasta), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


TOPE_FILAS = 100_000   # db.run_query corta en 5.000 por defecto: acá hay una fila por vaca y día


def corre(sql, tambo):
    """Filas de la consulta, o None si falló. Si la base devolvió MÁS filas que el tope, aborta:
    una suma sobre datos cortados da números falsos que parecen verdaderos."""
    try:
        r = db.run_query(sql, tambo=tambo, max_rows=TOPE_FILAS)
    except Exception as e:  # noqa: BLE001 - una sección que falla no tumba el resto
        _p(f"   (no se pudo consultar: {type(e).__name__}: {str(e)[:160]})")
        return None
    if r.get("truncated"):
        raise DiagnosticoError(f"La consulta devolvió más de {TOPE_FILAS} filas y se cortó; "
                               "los totales saldrían mal. Achicá el período de comparación.")
    return r["rows"]


def f0(x):
    return f"{x:,.0f}".replace(",", ".")


def f1(x):
    return f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def signo(x, dec=0):
    s = f"{abs(x):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("+" if x >= 0 else "-") + s


def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


def titulo(t):
    _p("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def _dt(x):
    """Fecha y hora como datetime, venga como datetime o como texto (según el driver)."""
    if x is None or isinstance(x, datetime.datetime):
        return x
    try:
        return datetime.datetime.fromisoformat(str(x)[:26])
    except ValueError:
        return None


def hora_min(minutos):
    """Minutos desde la medianoche -> «HH:MM» (pasadas las 24:00 vuelve a empezar)."""
    m = int(minutos) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def hora(dt):
    return dt.strftime("%H:%M") if dt else "--:--"


def ith_de(dias):
    """ITH máximo y promedio por día. Si el archivo histórico no llega, usa el pronóstico con días pasados.
    Sin red devuelve {}: el resto del diagnóstico no depende de esto."""
    import clima
    d0, d1 = min(dias), max(dias)
    try:
        r = clima.ith_diario(d0, d1)
    except Exception:  # noqa: BLE001
        r = {}
    faltan = [d for d in dias if d not in r]
    if faltan:
        try:
            q = urllib.parse.urlencode({"latitude": clima.LAT_DEFECTO, "longitude": clima.LON_DEFECTO,
                                        "hourly": "temperature_2m,relative_humidity_2m", "past_days": 14,
                                        "forecast_days": 1, "timezone": clima.ZONA})
            with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + q, timeout=30) as resp:
                h = json.load(resp)["hourly"]
            por = defaultdict(list)
            for ts, t, hr in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"]):
                if t is not None and hr is not None:
                    por[ts[:10]].append(clima.calcular_ith(t, hr))
            for d in faltan:
                if por.get(d):
                    r[d] = {"max": round(max(por[d]), 1), "prom": round(sum(por[d]) / len(por[d]), 1)}
        except Exception:  # noqa: BLE001
            pass
    return r


# --------------------------------------------------------------------------- análisis
def validar(dia, base_desde=None, base_hasta=None, hoy=None):
    """(dia, base_desde, base_hasta) como fechas, o DiagnosticoError con el motivo.

    Sin base se usan los DIAS_BASE días anteriores al analizado."""
    hoy = hoy or datetime.date.today()

    def fecha(x):
        if x in (None, ""):
            return None
        return x if isinstance(x, datetime.date) else datetime.date.fromisoformat(str(x))

    try:
        dia, bd, bh = fecha(dia), fecha(base_desde), fecha(base_hasta)
    except ValueError:
        raise DiagnosticoError("Las fechas tienen que ser AAAA-MM-DD.") from None
    if dia is None:
        raise DiagnosticoError("Falta el día a analizar.")
    if dia > hoy:
        raise DiagnosticoError("El día a analizar no puede ser futuro.")
    if bd is None and bh is None:
        bd, bh = dia - datetime.timedelta(days=DIAS_BASE), dia - datetime.timedelta(days=1)
    elif bd is None or bh is None:
        raise DiagnosticoError("Indicá las dos fechas de la comparación (desde y hasta), o ninguna.")
    if bd > bh:
        raise DiagnosticoError("En la comparación, 'desde' no puede ser posterior a 'hasta'.")
    dias = (bh - bd).days + 1
    if dias > MAX_DIAS_BASE:
        raise DiagnosticoError(f"La comparación puede abarcar hasta {MAX_DIAS_BASE} días (pediste {dias}).")
    utiles = dias - (1 if bd <= dia <= bh else 0)
    if utiles < MIN_DIAS_BASE:
        raise DiagnosticoError(f"La comparación necesita al menos {MIN_DIAS_BASE} días distintos del analizado.")
    return dia, bd, bh


def generar(tambo, dia, base_desde=None, base_hasta=None) -> str:
    """El informe como texto. Puede tardar (consultas pesadas a DelPro): correrlo en segundo plano."""
    dia, base_desde, base_hasta = validar(dia, base_desde, base_hasta)
    _salida.lineas = []
    datos = _salida.datos = {"dia": dia.isoformat(), "tambo": tambo}
    ayer = dia - datetime.timedelta(days=1)
    base_dias = [(base_desde + datetime.timedelta(days=i)).isoformat()
                 for i in range((base_hasta - base_desde).days + 1)]
    base_dias = [d for d in base_dias if d != dia.isoformat()]
    D, A = dia.isoformat(), ayer.isoformat()
    d0 = min(base_desde, dia, ayer).isoformat()
    d1 = max(base_hasta, dia).isoformat()

    _p(f"Diagnóstico de producción · tambo {tambo} · día {D} · comparado con "
       f"{base_desde.isoformat()} a {base_hasta.isoformat()}")

    # ---- 1. vacas vs kg por vaca ------------------------------------------------------------
    titulo("1. ¿FALTARON VACAS, O RINDIÓ MENOS CADA UNA?")
    filas = corre(sql_vacas(d0, d1, tambo), tambo)
    if filas is None:
        # Sin esta lectura no hay nada que comparar. Es un FALLO, no "un día sin datos": si el trabajo
        # figurara como listo, el informe vacío se leería como que ese día no se ordeñó.
        raise DiagnosticoError("No se pudo leer la producción de la base (tardó demasiado o no respondió). "
                               "Probá de nuevo en un momento.")
    if not filas:
        _p("   Sin datos de producción para el período. Revisá la fecha y el tambo.")
        return _texto()
    # MISMA definición que el dashboard (resumen.py): solo vacas IDENTIFICADAS con lectura VÁLIDA. Sumar también las
    # no válidas y las sin identificar daba 70.653 kg contra los 69.225 que muestra el dashboard el mismo día, y
    # además una vaca con total parcial (no válido) figuraba como "cayó 40 kg". Esas lecturas se cuentan aparte.
    por_dia = defaultdict(lambda: {"kg": 0.0, "vacas": 0})
    lect = defaultdict(lambda: {"inval_kg": 0.0, "inval_n": 0, "sinid_kg": 0.0})
    inval_animales = defaultdict(set)    # fecha -> animales ordeñados ese día con total NO válido
    cow = defaultdict(dict)          # animal -> {fecha: fila}
    for fecha, animal, numero, grupo, dim, lact, kg, valido in filas:
        f = str(fecha)[:10]
        kg = float(kg or 0)
        if not valido:
            lect[f]["inval_kg"] += kg
            lect[f]["inval_n"] += 1
            inval_animales[f].add(animal)
            continue
        if not (numero and int(numero) > 0):
            lect[f]["sinid_kg"] += kg
            continue
        por_dia[f]["kg"] += kg
        por_dia[f]["vacas"] += 1
        cow[animal][f] = {"kg": kg, "numero": int(numero), "grupo": grupo, "dim": dim, "lact": lact}
    if D not in por_dia:
        _p(f"   No hay producción registrada el {D}.")
        return _texto()
    kg_d, vac_d = por_dia[D]["kg"], por_dia[D]["vacas"]
    con_datos = [d for d in base_dias if d in por_dia]
    if not con_datos:
        _p("   Sin días de base para comparar.")
        return _texto()
    # Un día cortado (corte de luz, base restaurada a mitad de día) tiraría la base para abajo: se
    # descartan los que tienen menos de 90 % de las vacas habituales, igual que el Balance de leche.
    med_vacas = st.median(por_dia[d]["vacas"] for d in con_datos)
    incompletos = [d for d in con_datos if por_dia[d]["vacas"] < 0.9 * med_vacas]
    base_dias = [d for d in con_datos if d not in incompletos]
    if incompletos:
        _p(f"   (se excluyen de la base por incompletos, con menos de 90 % de las vacas habituales: "
              f"{', '.join(sorted(incompletos))})")
    if not base_dias:
        _p("   Sin días de base completos para comparar.")
        return _texto()
    base_kg = [por_dia[d]["kg"] for d in base_dias]
    base_vac = [por_dia[d]["vacas"] for d in base_dias]
    n_base = len(base_dias)
    kg_b, vac_b = st.mean(base_kg), st.mean(base_vac)
    kv_d, kv_b = kg_d / max(vac_d, 1), kg_b / max(vac_b, 1)
    _p(f"   {'':22}{'día':>12}{'base (prom ' + str(n_base) + 'd)':>16}{'diferencia':>14}")
    _p(f"   {'kg totales':22}{f0(kg_d):>12}{f0(kg_b):>16}{signo(kg_d - kg_b):>9} ({pct(kg_d, kg_b):+.1f} %)")
    _p(f"   {'vacas con leche':22}{f0(vac_d):>12}{f0(vac_b):>16}{signo(vac_d - vac_b):>9} ({pct(vac_d, vac_b):+.1f} %)")
    _p(f"   {'kg por vaca':22}{f1(kv_d):>12}{f1(kv_b):>16}{signo(kv_d - kv_b, 1):>9} ({pct(kv_d, kv_b):+.1f} %)")
    _p(f"   {'ayer':22}{f0(por_dia[A]['kg']) if A in por_dia else '--':>12}"
          f"{'(' + str(por_dia[A]['vacas']) + ' vacas)' if A in por_dia else '':>16}")
    efecto_vacas = (vac_d - vac_b) * kv_b
    efecto_rinde = vac_d * (kv_d - kv_b)
    _p(f"\n   Descomposición de {signo(kg_d - kg_b)} kg frente a la base:")
    _p(f"     por cantidad de vacas ordeñadas:  {signo(efecto_vacas):>8} kg")
    _p(f"     por rinde de cada vaca:           {signo(efecto_rinde):>8} kg")
    # Se mira el SIGNO de cada efecto: «faltaron vacas» solo si las vacas restan. Antes comparaba tamaños y el 07/11
    # (62 vacas MÁS, cada una rindiendo menos) decía «pesa más: FALTARON VACAS».
    if efecto_vacas < 0 and efecto_rinde < 0:
        mayor = "FALTARON VACAS" if efecto_vacas <= efecto_rinde else "BAJÓ EL RINDE POR VACA"
    elif efecto_vacas < 0:
        mayor = "FALTARON VACAS"
    elif efecto_rinde < 0:
        mayor = ("BAJÓ EL RINDE POR VACA, y las MÁS VACAS ordeñadas lo compensaron" if efecto_vacas > 0
                 else "BAJÓ EL RINDE POR VACA")
    else:
        mayor = "SUBIERON LAS VACAS Y EL RINDE" if efecto_vacas > 0 or efecto_rinde > 0 else "SIN CAMBIOS"
    _p(f"   -> pesa más: {mayor}")
    sd = st.pstdev(base_kg) if len(base_kg) > 1 else 0
    if sd:
        z = abs(kg_d - kg_b) / sd
        _p(f"   Variación normal de la base: ±{f0(sd)} kg (1 desvío). La diferencia es de {z:.1f} desvíos"
              + (" -> dentro de lo que varía normalmente." if z < 1 else
                 " -> fuera de lo normal." if z >= 2 else "."))
    kv_serie = [por_dia[d]["kg"] / max(por_dia[d]["vacas"], 1) for d in base_dias]
    ayer_kg = por_dia[A]["kg"] if A in por_dia else None
    datos["base"] = {"desde": base_desde.isoformat(), "hasta": base_hasta.isoformat(), "dias": n_base,
                     "excluidos": sorted(incompletos)}
    datos["resumen"] = {
        "kg_d": kg_d, "kg_b": kg_b, "vac_d": vac_d, "vac_b": vac_b, "kv_d": kv_d, "kv_b": kv_b,
        "efecto_vacas": efecto_vacas, "efecto_rinde": efecto_rinde, "sd_kg": sd,
        "z_kg": _z(kg_d, base_kg), "z_vac": _z(vac_d, base_vac), "z_kv": _z(kv_d, kv_serie),
        "ayer_kg": ayer_kg, "ayer_vacas": por_dia[A]["vacas"] if A in por_dia else None,
        "ayer_es_maximo": bool(ayer_kg is not None and ayer_kg >= max(base_kg)),
    }

    def _lec(campo):
        return st.mean(lect[d][campo] for d in base_dias)
    datos["lecturas"] = {"inval_kg_d": lect[D]["inval_kg"], "inval_kg_b": _lec("inval_kg"),
                         "inval_n_d": lect[D]["inval_n"], "inval_n_b": _lec("inval_n"),
                         "sinid_kg_d": lect[D]["sinid_kg"], "sinid_kg_b": _lec("sinid_kg")}
    _p(f"   Lecturas no válidas: {f0(lect[D]['inval_kg'])} kg en {lect[D]['inval_n']} vacas (base {f0(_lec('inval_kg'))} kg); "
       f"sin identificar: {f0(lect[D]['sinid_kg'])} kg (base {f0(_lec('sinid_kg'))}). "
       "No entran en las cifras de arriba (igual que el dashboard).")

    # ---- 2. vacas perdidas y vacas que bajaron ---------------------------------------------
    titulo("2. QUÉ VACAS SE PERDIERON Y CUÁLES BAJARON")
    en_base = {a: [v[d]["kg"] for d in base_dias if d in v] for a, v in cow.items()}
    faltan_hoy = [(a, st.mean(en_base[a])) for a, v in cow.items()
                  if D not in v and len(en_base[a]) >= 4]        # ordeñadas casi todos los días de la base
    # Una vaca de la base que hoy no figura puede ser dos cosas MUY distintas: no tiene ningún registro (no se la
    # ordeñó) o se la ordeñó pero DelPro dejó su total del día como NO válido (la leche existe y fue al tanque).
    perdidas = sorted((x for x in faltan_hoy if x[0] not in inval_animales[D]), key=lambda x: -x[1])
    invalidas = sorted((x for x in faltan_hoy if x[0] in inval_animales[D]), key=lambda x: -x[1])
    nuevas = [a for a, v in cow.items() if D in v and not en_base[a]]
    kg_perdido = sum(k for _, k in perdidas)
    kg_invalidas = sum(k for _, k in invalidas)
    _p(f"   Vacas habituales (≥4 de {n_base} días) SIN REGISTRO el {D} (no se ordeñaron): {len(perdidas)} "
       f"= {f0(kg_perdido)} kg que dejaron de entrar.")
    _p(f"   Otras {len(invalidas)} vacas habituales se ordeñaron pero con lectura NO válida (DelPro no cerró el total): "
       f"unos {f0(kg_invalidas)} kg que sí fueron al tanque y no figuran en las cifras.")
    _p(f"   Vacas que aparecen el {D} y no estaban en la base: {len(nuevas)}")
    aus_grupo = defaultdict(lambda: [0, 0.0])
    for a, k in perdidas:
        g_ult = cow[a][sorted(cow[a])[-1]]["grupo"]
        aus_grupo[g_ult][0] += 1
        aus_grupo[g_ult][1] += k
    datos["ausentes"] = {"n": len(perdidas), "kg": kg_perdido, "n_invalidas": len(invalidas),
                         "kg_invalidas": kg_invalidas,
                         "por_grupo": [{"grupo": g, "n": n, "kg": k}
                                       for g, (n, k) in sorted(aus_grupo.items(), key=lambda x: -x[1][1])]}
    if perdidas:
        _p(f"   {'vaca':>8}{'grupo':>8}{'DIM':>6}{'kg/día base':>13}  último día ordeñada")
        for a, k in perdidas[:LIMITE]:
            fechas = sorted(cow[a])
            ult = fechas[-1] if fechas else "--"
            ref = cow[a][ult]
            _p(f"   {ref['numero']:>8}{str(ref['grupo']):>8}{str(ref['dim']):>6}{f1(k):>13}  {ult}")
        por_grp = defaultdict(lambda: [0, 0.0])
        for a, k in perdidas:
            ult = sorted(cow[a])[-1]
            g = cow[a][ult]["grupo"]
            por_grp[g][0] += 1
            por_grp[g][1] += k
        _p("   Las ausentes por grupo: " + ", ".join(
            f"grupo {g}: {n} vacas / {f0(k)} kg" for g, (n, k) in sorted(por_grp.items(), key=lambda x: -x[1][1])[:6]))
    comunes = [(a, v[D]["kg"] - st.mean(en_base[a]), st.mean(en_base[a])) for a, v in cow.items()
               if D in v and len(en_base[a]) >= 4]
    caidas = sorted(comunes, key=lambda x: x[1])
    sub = [c for c in comunes if c[1] < 0]
    _p(f"\n   De las vacas ordeñadas los dos períodos: {len(sub)} de {len(comunes)} dieron menos que su promedio;"
          f" suman {signo(sum(c[1] for c in comunes))} kg netos.")
    dist = sorted(c[1] for c in comunes)
    datos["caidas"] = {"n_comunes": len(comunes), "n_menos": len(sub), "mediana": st.median(dist) if dist else 0.0}
    if dist:
        _p(f"   Mediana de cambio por vaca: {signo(st.median(dist), 1)} kg  "
              f"(si es ~0 la caída viene de pocas vacas; si es negativa, bajó el rodeo entero)")
    _p(f"   {'vaca':>8}{'grupo':>8}{'DIM':>6}{'base kg':>10}{'día kg':>9}{'cambio':>9}")
    for a, dif, kb in caidas[:LIMITE]:
        r = cow[a][D]
        _p(f"   {r['numero']:>8}{str(r['grupo']):>8}{str(r['dim']):>6}{f1(kb):>10}{f1(r['kg']):>9}{signo(dif, 1):>9}")

    # ---- 3. por grupo -----------------------------------------------------------------------
    titulo("3. EN QUÉ GRUPOS SE CONCENTRA LA CAÍDA")
    g_d, g_b = defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for a, v in cow.items():
        for f, r in v.items():
            if f == D:
                g_d[r["grupo"]][0] += 1
                g_d[r["grupo"]][1] += r["kg"]
            elif f in base_dias:
                g_b[r["grupo"]][0] += 1
                g_b[r["grupo"]][1] += r["kg"]
    filasg = []
    for g in set(g_d) | set(g_b):
        nd, kd = g_d[g]
        nb, kb = g_b[g][0] / n_base, g_b[g][1] / n_base
        filasg.append((g, nd, nb, kd, kb, kd - kb))
    filasg.sort(key=lambda x: x[5])
    datos["grupos"] = [{"grupo": g, "vacas_d": nd, "vacas_b": nb, "kg_d": kd, "kg_b": kb, "delta_kg": dk}
                       for g, nd, nb, kd, kb, dk in filasg]
    _p(f"   {'grupo':>7}{'vacas día':>11}{'vacas base':>12}{'kg día':>10}{'kg base':>10}{'cambio kg':>11}{'kg/vaca d':>11}{'kg/vaca b':>11}")
    for g, nd, nb, kd, kb, dk in filasg[:LIMITE]:
        _p(f"   {str(g):>7}{nd:>11}{f1(nb):>12}{f0(kd):>10}{f0(kb):>10}{signo(dk):>11}"
              f"{f1(kd / nd) if nd else '--':>11}{f1(kb / nb) if nb else '--':>11}")
    grandes = [x for x in filasg if x[2] >= 20]          # los grupos de 1 a 5 vacas no explican nada
    bajan = [x for x in grandes if x[5] < 0]
    tot_neg = sum(x[5] for x in bajan)
    if bajan and tot_neg:
        top = bajan[0]
        _p(f"   -> de los grupos de 20 vacas o más, el grupo {top[0]} explica "
              f"{top[5] / tot_neg * 100:.0f} % de lo que cayó entre los que bajaron ({signo(top[5])} kg)")
    elif grandes:
        _p("   -> ningún grupo de 20 vacas o más produjo menos que su base: la diferencia no está en un grupo")

    # ---- 4. por ordeño ----------------------------------------------------------------------
    titulo("4. ¿EN QUÉ ORDEÑO, Y A QUÉ HORA?")
    ses = corre(sql_sesiones(d0, d1, tambo), tambo)
    datos["ordenos"] = None             # None = no se pudo leer (el veredicto lo trata como "sin dato")
    if ses:
        ordenos = []
        s_d, s_b = {}, defaultdict(list)
        for fecha, sn, v, kg, ini, fin, cond, cau in ses:
            f = str(fecha)[:10]
            fila = {"vacas": int(v), "kg": float(kg or 0), "ini": _dt(ini), "fin": _dt(fin),
                    "cond": float(cond or 0), "cau": float(cau or 0)}
            if f == D:
                s_d[sn] = fila
            elif f in base_dias:
                s_b[sn].append(fila)
        _p(f"   {'ordeño':>7}{'vacas':>8}{'base':>8}{'kg':>9}{'base':>9}{'cambio':>9}"
              f"{'inicio':>8}{'fin':>7}{'dur(h)':>8}{'dur base':>10}{'caudal':>8}{'base':>7}")
        for sn in sorted(set(s_d) | set(s_b)):
            d, b = s_d.get(sn), s_b.get(sn, [])
            if not d:
                _p(f"   {sn:>7}  (no hay registros ese día; en la base sí: {len(b)} días)")
                continue
            bv = st.mean(x["vacas"] for x in b) if b else 0
            bk = st.mean(x["kg"] for x in b) if b else 0
            dur_d = (d["fin"] - d["ini"]).total_seconds() / 3600 if d["ini"] and d["fin"] else 0
            dur_b = st.mean((x["fin"] - x["ini"]).total_seconds() / 3600 for x in b if x["ini"] and x["fin"]) if b else 0
            ini_b = st.mean(x["ini"].hour * 60 + x["ini"].minute for x in b if x["ini"]) if b else 0
            ibh = f"{int(ini_b // 60):02d}:{int(ini_b % 60):02d}" if b else "--:--"
            cau_b = st.mean(x["cau"] for x in b) if b else 0
            ordenos.append({"sesion": sn, "vacas_d": d["vacas"], "vacas_b": bv, "kg_d": d["kg"], "kg_b": bk,
                            "ini_d": hora(d["ini"]) if d["ini"] else None, "ini_b": ibh if b else None,
                            "dur_d": dur_d, "dur_b": dur_b, "cau_d": d["cau"], "cau_b": cau_b})
            _p(f"   {sn:>7}{d['vacas']:>8}{f0(bv):>8}{f0(d['kg']):>9}{f0(bk):>9}{signo(d['kg'] - bk):>9}"
                  f"{hora(d['ini']):>8}{hora(d['fin']):>7}{dur_d:>8.1f}{dur_b:>10.1f}{d['cau']:>8.2f}{cau_b:>7.2f}")
            if b:
                notas = []
                if abs(d["ini"].hour * 60 + d["ini"].minute - ini_b) > 45:
                    notas.append(f"inició {hora(d['ini'])} (lo normal ~{ibh})")
                if bv and d["vacas"] < bv * 0.95:
                    notas.append(f"pasaron {bv - d['vacas']:.0f} vacas menos")
                if dur_b and dur_d < dur_b * 0.85:
                    notas.append("sesión más corta de lo normal")
                if cau_b and d["cau"] < cau_b * 0.93:
                    notas.append("caudal por vaca más bajo (rinde/ritmo)")
                if notas:
                    _p(f"           -> {'; '.join(notas)}")
        datos["ordenos"] = ordenos
        _p("   (inicio/fin = primer y último registro de ese ordeño; sesiones sin número fijo en tambos\n"
              "    de 2 ordeños pueden aparecer como 1 y 2, y las de 3 como 1, 2 y 3.)")

    # ---- 5. bocas ---------------------------------------------------------------------------
    titulo("5. ¿ALGUNA BOCA O UNIDAD DE ORDEÑE CAÍDA?")
    bocas = corre(sql_bocas(d0, d1, tambo), tambo)
    datos["bocas"] = None
    if bocas:
        b_d, b_b = {}, defaultdict(list)
        for fecha, boca, n, kg in bocas:
            f = str(fecha)[:10]
            if f == D:
                b_d[boca] = (int(n), float(kg or 0))
            elif f in base_dias:
                b_b[boca].append((int(n), float(kg or 0)))
        rel = []
        for boca, lst in b_b.items():
            nb = st.mean(x[0] for x in lst)
            kb = st.mean(x[1] for x in lst)
            nd, kd = b_d.get(boca, (0, 0.0))
            if nb >= 5:
                rel.append((boca, nd, nb, kd, kb, nd / nb))
        rel.sort(key=lambda x: x[5])
        _p(f"   Bocas con menos ordeños que lo habitual (de {len(rel)} bocas medidas):")
        _p(f"   {'boca':>7}{'ordeños día':>13}{'base':>8}{'kg día':>9}{'base':>9}{'día/base':>10}")
        for boca, nd, nb, kd, kb, r in rel[:LIMITE]:
            _p(f"   {str(boca):>7}{nd:>13}{nb:>8.1f}{f0(kd):>9}{f0(kb):>9}{r:>10.2f}")
        malas = [x for x in rel if x[5] < 0.6]
        datos["bocas"] = {"medidas": len(rel), "caidas": [{"boca": x[0], "ratio": x[5]} for x in malas]}
        if malas:
            _p(f"   -> {len(malas)} boca(s) rindieron menos de 60 % de lo normal: revisar unidad/pulsación/lector")
        else:
            _p("   -> ninguna boca cayó de forma llamativa; no parece un problema de equipo puntual")

    # ---- 6. eventos / mezcla del rodeo -------------------------------------------------------
    titulo("6. CAMBIOS EN EL RODEO: PARTOS, GRUPOS, BAJAS, TRATAMIENTOS")
    ev = corre(sql_eventos(d0, d1, tambo), tambo)
    bj = corre(sql_bajas(d0, d1, tambo), tambo)
    por_tipo = defaultdict(lambda: defaultdict(int))
    trat = defaultdict(lambda: defaultdict(int))
    for fecha, tipo, detalle, n in list(ev or []) + list(bj or []):
        f = str(fecha)[:10]
        por_tipo[tipo][f] += int(n)
        if tipo == "tratamiento":
            trat[detalle or "(sin nombre)"][f] += int(n)
    _p(f"   {'evento':>18}{'día':>6}{'ayer':>6}{'prom base/día':>15}")
    datos["eventos"] = {}
    for tipo in ("parto", "cambio de grupo", "baja", "tratamiento"):
        # Si la consulta falló NO se muestran ceros: un cero inventado parece "no pasó nada".
        if (bj if tipo == "baja" else ev) is None:
            _p(f"   {tipo:>18}{'sin dato':>27}")
            datos["eventos"][tipo] = None
            continue
        v = por_tipo.get(tipo, {})
        base = sum(v.get(d, 0) for d in base_dias) / n_base
        datos["eventos"][tipo] = {"dia": v.get(D, 0), "ayer": v.get(A, 0), "base": base}
        flag = "  <- sube" if v.get(D, 0) > max(2 * base, base + 5) else ""
        _p(f"   {tipo:>18}{v.get(D, 0):>6}{v.get(A, 0):>6}{base:>15.1f}{flag}")
    sube = sorted(((sum(v.get(d, 0) for d in (D, A)), k, v.get(D, 0), sum(v.get(d, 0) for d in base_dias) / n_base)
                   for k, v in trat.items()), reverse=True)[:6]
    if sube:
        _p("   Diagnósticos tratados ayer y el día (día / prom base):")
        for _, k, n, b in sube:
            _p(f"     {k:<50}{n:>4} / {b:.1f}")
    datos["tratamientos_ayer"] = sorted(
        ({"nombre": k, "ayer": v.get(A, 0), "base": sum(v.get(d, 0) for d in base_dias) / n_base}
         for k, v in trat.items()), key=lambda x: -x["ayer"])[:6]

    # Cruce por vaca: ¿las que se trataron AYER rindieron menos HOY que el resto? Es lo que separa "hubo mucho
    # trabajo sanitario" (un dato) de "y eso bajó la leche" (una causa).
    datos["tratadas_ayer"] = None
    tr = corre(sql_tratadas(A, tambo), tambo)
    if tr:
        tratadas = defaultdict(set)
        for animal, detalle in tr:
            tratadas[animal].add(detalle or "(sin nombre)")
        d_t, d_r, por_diag = [], [], defaultdict(list)
        for a, v in cow.items():
            if D in v and len(en_base[a]) >= 4:
                dl = v[D]["kg"] - st.mean(en_base[a])
                if a in tratadas:
                    d_t.append(dl)
                    for nombre in tratadas[a]:
                        por_diag[nombre].append(dl)
                else:
                    d_r.append(dl)
        if d_t and d_r:
            m_t, m_r = st.mean(d_t), st.mean(d_r)
            datos["tratadas_ayer"] = {
                "n": len(d_t), "delta_tratadas": m_t, "delta_resto": m_r, "efecto_kg": (m_t - m_r) * len(d_t),
                "por_diagnostico": [{"nombre": k, "n": len(x), "delta": st.mean(x)}
                                    for k, x in sorted(por_diag.items(), key=lambda kv: -len(kv[1]))[:5]]}
            _p(f"\n   Cruce por vaca: las {len(d_t)} vacas tratadas ayer rindieron {signo(m_t, 1)} kg respecto de su promedio;"
               f" las otras {len(d_r)}, {signo(m_r, 1)} kg.  Efecto estimado de lo tratado: {signo((m_t - m_r) * len(d_t))} kg.")
            for k, x in sorted(por_diag.items(), key=lambda kv: -len(kv[1]))[:5]:
                _p(f"     {k:<50}{len(x):>4} vacas, {signo(st.mean(x), 1)} kg cada una")
            _p("   (Ojo: una vaca con mastitis ya venía rindiendo menos; mirá cada diagnóstico por separado.)")
    elif tr is not None:
        _p("   Cruce por vaca: ayer no se registraron tratamientos.")
    ret = corre(sql_retiro(d0, d1, tambo), tambo)
    datos["retiros"] = None
    if ret is None:
        _p("   Vacas con retiro/descarte de leche: sin dato.")
    else:
        rv = {str(f)[:10]: int(v) for f, v in ret}
        datos["retiros"] = {"dia": rv.get(D, 0), "base": sum(rv.get(d, 0) for d in base_dias) / n_base}
        _p(f"   Vacas con retiro/descarte de leche registrado ese día: {rv.get(D, 0)} "
           f"(base {sum(rv.get(d, 0) for d in base_dias) / n_base:.1f}/día). "
           "Esa leche puede no ir al tanque aunque sí figure en DelPro.")

    # ---- 7. clima ---------------------------------------------------------------------------
    titulo("7. ESTRÉS CALÓRICO (ITH) — ¿HIZO CALOR?")
    ith = ith_de(base_dias + [A, D])
    datos["ith"] = None
    if ith:
        _p(f"   {'fecha':>12}{'ITH máx':>9}{'ITH prom':>10}   (>=68 estrés leve, >=72 moderado, >=80 severo)")
        for d in sorted(ith)[-9:]:
            marca = " <- día analizado" if d == D else ""
            _p(f"   {d:>12}{ith[d]['max']:>9}{ith[d]['prom']:>10}{marca}")
        mx_d = ith.get(D, {}).get("max")
        mx_b = [ith[d]["max"] for d in base_dias if d in ith]
        if mx_d is not None and mx_b:
            datos["ith"] = {"max_d": mx_d, "max_b": st.mean(mx_b), "serie": {d: ith[d] for d in sorted(ith)}}
            _p(f"   -> ITH máx {mx_d} vs {st.mean(mx_b):.1f} de base: "
                  + ("dentro de lo normal." if mx_d - st.mean(mx_b) < 3 else "MÁS CALUROSO que la base."))
    else:
        _p("   No se pudo obtener el clima (sin internet o la API no tiene esos días). Nada que concluir.")

    # ---- 8. alimentación -----------------------------------------------------------------------
    titulo("8. ALIMENTACIÓN — ¿LLEGÓ A HORA LA COMIDA DEL MIXER?")
    datos["alimentacion"] = None
    cons, motivo = _consumos_mixer(tambo, dia - datetime.timedelta(days=HISTORIA_MIXER_DIAS), dia)
    if cons is None:
        _p(f"   Sin dato: {motivo or 'no se pudo leer el mixer.'}")
    else:
        import alimentacion_dia
        ev = alimentacion_dia.evaluar(cons, dia)
        if ev is None:
            _p(f"   Sin historia suficiente de comidas del mixer (hacen falta al menos {alimentacion_dia.MIN_HISTORIA} "
               "días registrados antes de ayer) para saber a qué hora es lo normal.")
        else:
            datos["alimentacion"] = ev
            serie = alimentacion_dia.por_dia(cons)
            b = ev["base"]
            _p(f"   {'fecha':>12}{'1.ª comida':>12}{'última':>9}{'viajes':>8}{'kg':>9}   "
               f"(lo normal: primera a las {hora_min(b['primera_med'])} ± {b['primera_sd']:.0f} min)")
            for f in sorted(serie)[-9:]:
                v = serie[f]
                marca = " <- día analizado" if f == D else " <- ayer" if f == A else ""
                tarde = "  TARDE" if v["primera"] - b["primera_med"] >= 60 and f in (D, A) else ""
                _p(f"   {f:>12}{hora_min(v['primera']):>12}{hora_min(v['ultima']):>9}{v['viajes']:>8}{f0(v['kg']):>9}{marca}{tarde}")
            for nombre, x in (("el día analizado", ev["dia"]), ("ayer", ev["ayer"])):
                if x is None:
                    _p(f"   -> El mixer no registró comidas {nombre}.")
                else:
                    _p(f"   -> Primera comida de {nombre}: {hora_min(x['primera'])}, {x['retraso']:+d} min contra lo normal "
                       f"({x['z']:+.1f} desvíos).")
            if b["tarde"]:
                _p("   Otros días con comida tardía: " + ", ".join(
                    f"{t['fecha'][8:10]}/{t['fecha'][5:7]} ({hora_min(t['primera'])})" for t in b["tarde"][-6:])
                   + ". Mirá si en esos días también bajó la leche por vaca.")

    # ---- resumen ----------------------------------------------------------------------------
    titulo("RESUMEN (las pistas, no el veredicto)")
    _p(f"   Diferencia: {signo(kg_d - kg_b)} kg ({pct(kg_d, kg_b):+.1f} %) vs prom de {n_base} días completos.")
    _p(f"   Pesa más: {mayor.lower()}  (vacas {signo(efecto_vacas)} kg, rinde {signo(efecto_rinde)} kg).")
    if perdidas:
        _p(f"   {len(perdidas)} vacas habituales no se ordeñaron ({f0(kg_perdido)} kg).")
    _p("   Mirá las secciones 3 y 4: si la caída está en un grupo o en un ordeño, ahí está la pista de manejo;")
    _p("   si está repartida en todo el rodeo y el ITH subió, apunta a calor; si el rodeo entero rinde menos\n"
          "   sin cambios de vacas ni de clima, mirá ración/alimentación (carro, mixer) de ayer.")
    return _texto()


def analizar(tambo, dia, base_desde=None, base_hasta=None) -> dict:
    """Todo el análisis: `informe` (texto), `datos` (lo medido, para los gráficos) y `veredicto` (qué causa apuntar)."""
    import veredicto_baja
    texto = generar(tambo, dia, base_desde, base_hasta)
    datos = getattr(_salida, "datos", {})
    veredicto = veredicto_baja.veredicto(datos) if "resumen" in datos else None
    return {"informe": texto, "datos": datos, "veredicto": veredicto}


# --------------------------------------------------------------------------- en segundo plano
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_MAX_JOBS = 20


def iniciar(tambo, dia, base_desde=None, base_hasta=None) -> dict:
    """Lanza el análisis en un hilo y devuelve el job (`id`, `estado`). Si YA hay uno corriendo con los
    mismos parámetros devuelve ese: el botón apretado dos veces no duplica consultas pesadas."""
    dia, bd, bh = validar(dia, base_desde, base_hasta)     # falla acá, no en el hilo
    clave = (tambo, dia.isoformat(), bd.isoformat(), bh.isoformat())
    with _JOBS_LOCK:
        for j in _JOBS.values():
            if j["clave"] == clave and j["estado"] == "calculando":
                return _vista(j)
        job = {"id": uuid.uuid4().hex[:12], "clave": clave, "estado": "calculando",
               "informe": None, "datos": None, "veredicto": None, "error": None,
               "inicio": time.time(), "fin": None}
        _JOBS[job["id"]] = job
        terminados = [k for k, v in _JOBS.items() if v["estado"] != "calculando"]
        while len(_JOBS) > _MAX_JOBS and terminados:
            _JOBS.pop(terminados.pop(0), None)

    def worker():
        try:
            r = analizar(tambo, dia, bd, bh)
            informe, datos, veredicto = r["informe"], r["datos"], r["veredicto"]
            estado_, error = "listo", None
        except DiagnosticoError as exc:
            informe = datos = veredicto = None
            estado_, error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 - el usuario ve el motivo
            informe = datos = veredicto = None
            estado_ = "error"
            error = f"No se pudo completar el análisis: {type(exc).__name__}: {str(exc)[:200]}"
        with _JOBS_LOCK:
            job.update(informe=informe, datos=datos, veredicto=veredicto, estado=estado_, error=error,
                       fin=time.time())

    threading.Thread(target=worker, daemon=True).start()
    return _vista(job)


def _vista(job: dict) -> dict:
    fin = job["fin"] or time.time()
    return {"id": job["id"], "estado": job["estado"], "informe": job["informe"], "datos": job["datos"],
            "veredicto": job["veredicto"], "error": job["error"],
            "segundos": round(fin - job["inicio"])}


def estado(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return _vista(job) if job else None
