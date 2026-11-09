# -*- coding: utf-8 -*-
"""Veredicto del diagnóstico de producción: qué causa apuntar, con qué confianza, y cuándo NO apuntar ninguna.

Es una función PURA sobre el dict `datos` que arma `diagnostico_baja.analizar` (no toca la base ni la red),
así que se prueba sin nada conectado. Reglas simples y a la vista; ninguna es "inteligente": cada una
compara una medición contra lo que el propio tambo viene dando y deja escrito en `evidencia` qué midió.

Niveles de cada causa:
  probable   la medición es claramente anormal y alcanza para explicar buena parte de la diferencia
  posible    hay una señal, pero no alcanza para afirmarlo, o falta un dato para confirmarlo
  descartada se midió y está normal
  sin_dato   esa parte del análisis no se pudo calcular (no se la descarta: no se sabe)

Nivel global (`nivel`):
  normal     la diferencia contra la base cae dentro de lo que el tambo varía solo (menos de 1 desvío) o no fue baja
  atencion   el TOTAL es normal pero cada vaca rindió claramente menos (más vacas lo compensaron): no es un día normal
  claro      hubo una baja fuera de lo normal y una causa probable
  posible    hubo una baja fuera de lo normal y solo causas posibles
  sin_causa  hubo una baja fuera de lo normal y NADA de lo que mide DelPro la explica: mirar ración y manejo
Lo que DelPro no mide (carro, agua, estrés) queda fuera de este veredicto, y se avisa. La comida del mixer SÍ entra
cuando hay un proveedor conectado (MixerOne): ver `_alimentacion` y `alimentacion_dia.py`.
"""

# Umbrales. Están acá arriba para poder discutirlos y cambiarlos sin leer la lógica.
Z_NORMAL = 1.0           # menos de 1 desvío de la base = variación normal del tambo
Z_FUERTE = 2.0           # 2 desvíos o más = claramente fuera de lo normal
PCT_VACAS_AUSENTES = 0.01   # vacas habituales sin ordeñar que pesan >= 1 % de los kg de la base: probable
PCT_GRUPO_RINDE = -8.0   # un grupo grande cuyo kg por vaca baja 8 % o más
PCT_ORDENO_MIN = 0.005   # un ordeño que bajó >= 0.5 % de los kg de la base
PCT_ORDENO_FUERTE = 0.01
MIN_VACAS_GRUPO = 20     # los grupos más chicos no explican nada
DESPLAZO_ORDENO_MIN = 45  # minutos de corrimiento del inicio de un ordeño que ya es una señal
ITH_ESTRES = 72.0
ITH_LEVE = 68.0
ITH_SALTO = 3.0          # puntos de ITH por encima de la base
BOCAS_PROBABLE = 3       # tres o más bocas que casi no ordeñaron
MANEJO_VECES = 2.0       # tratamientos del día anterior >= 2 veces lo normal
MANEJO_EXTRA = 30        # ... y al menos 30 más
MANEJO_EFECTO_KG = -1.0  # las tratadas rinden al menos 1 kg menos que el resto: probable
MANEJO_MIN_VACAS = 20
MANEJO_PESO_PROBABLE = 0.5   # lo tratado explica la mitad de la baja del rinde por vaca...
MANEJO_PESO_POSIBLE = 0.2    # ...o al menos una quinta parte
MANEJO_PCT_PROBABLE = 0.01   # ...o por sí solo resta 1 % de la leche del día
RINDE_MEDIANA_MIN = -0.5     # kg: si las vacas que estuvieron en los dos períodos no bajaron esto, es mezcla de vacas
MEZCLA_VECES = 1.5
MEZCLA_EXTRA = 15
LECTURAS_PCT = 0.005
RETIROS_VECES = 2.0
RETIROS_EXTRA = 10
ALIM_RETRASO_MIN = 60    # minutos más tarde que la mediana de la primera comida: ya es una señal
ALIM_KG_BAJO = 0.15      # se descargó al menos 15 % menos que la mediana de la historia, Y a 2 desvíos o más

_ORDEN = {"probable": 0, "posible": 1, "sin_dato": 2, "descartada": 3}


# --------------------------------------------------------------------------- formato
def _n(x, dec=0):
    s = f"{abs(x):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def _s(x, dec=0):
    return ("+" if x >= 0 else "-") + _n(abs(x), dec)


def _pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


def _causa(clave, titulo, nivel, evidencia, impacto=0.0, revisar=""):
    return {"clave": clave, "titulo": titulo, "nivel": nivel, "evidencia": evidencia,
            "impacto_kg": round(float(impacto), 0), "revisar": revisar}


def _hm(txt):
    h, m = str(txt).split(":")[:2]
    return int(h) * 60 + int(m)


# --------------------------------------------------------------------------- una función por causa
def _vacas(d):
    r, a = d["resumen"], d.get("ausentes") or {}
    kg_b = r["kg_b"] or 1.0
    ev = []
    z_vac = r.get("z_vac")
    n_aus, kg_aus = a.get("n", 0), a.get("kg", 0.0)
    ev.append(f"{_n(r['vac_d'])} vacas ordeñadas contra {_n(r['vac_b'])} de la base ({_s(r['vac_d'] - r['vac_b'])}).")
    if n_aus:
        grupo = (a.get("por_grupo") or [{}])[0]
        extra = f", sobre todo del grupo {grupo['grupo']}" if grupo.get("grupo") is not None else ""
        ev.append(f"{n_aus} vacas habituales no se ordeñaron ese día ({_n(kg_aus)} kg que dejaron de entrar{extra}).")
    if a.get("n_invalidas"):
        ev.append(f"Otras {a['n_invalidas']} vacas habituales se ordeñaron pero con lectura no válida (unos "
                  f"{_n(a.get('kg_invalidas', 0))} kg que sí fueron al tanque): no son vacas que faltaron.")
    diff = r["kg_d"] - r["kg_b"]
    pesa = -r["efecto_vacas"] / abs(diff) if diff < 0 and r["efecto_vacas"] < 0 else 0.0
    if z_vac is not None:
        ev.append(f"La cantidad de vacas está a {_n(abs(z_vac), 1)} desvíos de lo habitual "
                  f"({'por debajo' if z_vac < 0 else 'por encima'}).")
    if (z_vac is not None and z_vac <= -Z_FUERTE) or (kg_aus >= PCT_VACAS_AUSENTES * kg_b and pesa >= 0.5):
        nivel = "probable"
    elif (z_vac is not None and z_vac <= -Z_NORMAL) or (kg_aus >= 0.005 * kg_b and pesa >= 0.3):
        nivel = "posible"
    else:
        nivel = "descartada"
    titulo = {"probable": f"Faltaron vacas: {n_aus} habituales sin ordeñar",
              "posible": "Pasaron menos vacas que de costumbre"}.get(nivel, "Cantidad de vacas ordeñadas")
    return _causa("vacas", titulo, nivel, ev, impacto=min(0.0, r["efecto_vacas"]),
                  revisar="Qué pasó con esas vacas: encierros, retiradas forzadas, un lote que no pasó o quedó sin ordeñar.")


def _rinde(d):
    r, c = d["resumen"], d.get("caidas") or {}
    z_kv = r.get("z_kv")
    ev = [f"{_n(r['kv_d'], 1)} kg por vaca contra {_n(r['kv_b'], 1)} de la base ({_s(_pct(r['kv_d'], r['kv_b']), 1)} %)."]
    if z_kv is not None:
        ev.append(f"Está a {_n(abs(z_kv), 1)} desvíos de lo que viene dando el tambo "
                  f"({'por debajo' if z_kv < 0 else 'por encima'}).")
    if c.get("n_comunes"):
        ev.append(f"{c['n_menos']} de {c['n_comunes']} vacas ({c['n_menos'] / c['n_comunes'] * 100:.0f} %) dieron menos que "
                  f"su promedio; mediana de cambio {_s(c.get('mediana', 0), 1)} kg por vaca.")
    ev.append(f"Aporte a la diferencia: {_s(r['efecto_rinde'])} kg.")
    if z_kv is not None and z_kv <= -Z_FUERTE:
        nivel = "probable"
    elif z_kv is not None and z_kv <= -Z_NORMAL:
        nivel = "posible"
    else:
        nivel = "descartada"
    titulo = "Cada vaca rindió menos" if nivel != "descartada" else "Rinde por vaca"
    mediana = c.get("mediana")
    if nivel != "descartada" and mediana is not None and mediana > RINDE_MEDIANA_MIN:
        # El promedio por vaca puede bajar porque ENTRARON vacas de baja producción (recién paridas, retornadas de
        # un tratamiento) sin que ninguna rinda menos. Si las vacas que estuvieron en los dos períodos rindieron casi
        # igual que su propio promedio, el promedio general bajó por la mezcla, no por el rinde.
        nivel, titulo = "posible", "El promedio por vaca bajó por la mezcla de vacas"
        ev.append(f"Pero las vacas que estuvieron en los dos períodos rindieron casi igual que su promedio "
                  f"(mediana {_s(mediana, 1)} kg): el promedio bajó por la mezcla de vacas que entraron, no porque "
                  "cada una rinda menos.")
    return _causa("rinde", titulo, nivel, ev,
                  impacto=min(0.0, r["efecto_rinde"]),
                  revisar="Si es parejo en todo el rodeo y no hay calor ni cambios de ordeño: ración de ayer (carro, "
                          "mixer, silo), agua, o algo que alteró el descanso/estrés de las vacas.")


def _grupos(d):
    r = d["resumen"]
    kg_b = r["kg_b"] or 1.0
    grandes = [g for g in (d.get("grupos") or []) if g["vacas_b"] >= MIN_VACAS_GRUPO]
    if not grandes:
        return _causa("grupos", "Por grupo", "sin_dato", ["No hay grupos de 20 vacas o más para comparar."])
    bajan = sorted((g for g in grandes if g["delta_kg"] < 0), key=lambda g: g["delta_kg"])
    total_neg = sum(g["delta_kg"] for g in bajan)
    # Un grupo que cambió más de 10 % de tamaño no se marca por kg por vaca: entraron o salieron vacas y su
    # promedio cambia por la mezcla, no porque cada una rinda menos.
    atipicos = [g for g in bajan if g["vacas_d"] and g["vacas_b"] and
                abs(g["vacas_d"] / g["vacas_b"] - 1) <= 0.10 and
                _pct(g["kg_d"] / g["vacas_d"], g["kg_b"] / g["vacas_b"]) <= PCT_GRUPO_RINDE
                and -g["delta_kg"] >= 0.005 * kg_b]
    ev = []
    for g in (atipicos + [x for x in bajan if x not in atipicos])[:3]:
        kv_b = g["kg_b"] / g["vacas_b"] if g["vacas_b"] else 0
        kv_d = g["kg_d"] / g["vacas_d"] if g["vacas_d"] else 0
        ev.append(f"Grupo {g['grupo']}: {_s(g['delta_kg'])} kg; kg por vaca {_n(kv_b, 1)} -> {_n(kv_d, 1)} "
                  f"({_s(_pct(kv_d, kv_b), 1)} %).")
    share = (bajan[0]["delta_kg"] / total_neg) if bajan and total_neg else 0.0
    if bajan and share >= 0.7 and -bajan[0]["delta_kg"] >= 0.01 * kg_b:
        nivel, titulo = "probable", f"La caída se concentra en el grupo {bajan[0]['grupo']}"
        ev.insert(0, f"El grupo {bajan[0]['grupo']} explica {share * 100:.0f} % de lo que bajó entre los grupos que bajaron.")
    elif atipicos:
        a = atipicos[0]
        nivel, titulo = "posible", f"El grupo {a['grupo']} rindió mucho menos por vaca"
    elif bajan and share >= 0.5 and -bajan[0]["delta_kg"] >= 0.005 * kg_b:
        nivel, titulo = "posible", f"Se nota sobre todo en el grupo {bajan[0]['grupo']}"
    else:
        nivel, titulo = "descartada", "Por grupo"
        ev.insert(0, "La diferencia está repartida entre varios grupos, sin uno que la explique.")
    return _causa("grupos", titulo, nivel, ev, impacto=bajan[0]["delta_kg"] if bajan else 0.0,
                  revisar="Qué tienen en común esas vacas: lote, corral, ración del lote, cambios de grupo recientes.")


def _ordeno(d):
    ords = d.get("ordenos")
    if ords is None:
        return _causa("ordeno", "Por ordeño", "sin_dato", ["No se pudieron leer los ordeños de ese día."])
    r = d["resumen"]
    kg_b = r["kg_b"] or 1.0
    filas = []
    for o in ords:
        delta = o["kg_d"] - o["kg_b"]
        notas = []
        if o.get("ini_d") and o.get("ini_b") and abs(_hm(o["ini_d"]) - _hm(o["ini_b"])) > DESPLAZO_ORDENO_MIN:
            notas.append(f"empezó a las {o['ini_d']} (lo normal es ~{o['ini_b']})")
        if o["vacas_b"] and o["vacas_d"] < 0.95 * o["vacas_b"]:
            notas.append(f"pasaron {_n(o['vacas_b'] - o['vacas_d'])} vacas menos")
        if o.get("dur_b") and o.get("dur_d") is not None and o["dur_d"] < 0.85 * o["dur_b"]:
            notas.append("duró menos de lo normal")
        filas.append((o, delta, notas))
    bajan = sorted((f for f in filas if f[1] < 0), key=lambda f: f[1])
    total_neg = sum(f[1] for f in bajan)
    if not bajan:
        return _causa("ordeno", "Por ordeño", "descartada", ["Ningún ordeño produjo menos que lo habitual."])
    o, delta, notas = bajan[0]
    share = delta / total_neg if total_neg else 0.0
    hora = f" ({o['ini_d']})" if o.get("ini_d") else ""
    ev = [f"El {o['sesion']}.º ordeño{hora}: {_s(delta)} kg ({share * 100:.0f} % de lo que bajó entre los ordeños que bajaron).",
          f"Vacas: {_n(o['vacas_d'])} contra {_n(o['vacas_b'])} de la base; kg por vaca en ese ordeño "
          f"{_n(o['kg_d'] / o['vacas_d'], 1) if o['vacas_d'] else '--'} contra "
          f"{_n(o['kg_b'] / o['vacas_b'], 1) if o['vacas_b'] else '--'}."]
    ev += [f"Atención: {n}." for n in notas]
    if notas and -delta >= PCT_ORDENO_MIN * kg_b:
        nivel = "probable"
    elif share >= 0.7 and -delta >= PCT_ORDENO_FUERTE * kg_b:
        nivel = "probable"
    elif share >= 0.5 and -delta >= PCT_ORDENO_FUERTE * kg_b:
        nivel = "posible"
    else:
        nivel = "descartada"
        ev.insert(0, "La diferencia está repartida entre los ordeños.")
    titulo = f"El {o['sesion']}.º ordeño concentra la diferencia" if nivel != "descartada" else "Por ordeño"
    return _causa("ordeno", titulo, nivel, ev, impacto=delta,
                  revisar="Intervalo con el ordeño anterior, hora de entrada de las vacas, si algo demoró la sala "
                          "(lavado, falla, cambio de turno) y la rutina de esa tarde.")


def _equipo(d):
    b = d.get("bocas")
    if b is None:
        return _causa("equipo", "Bocas de ordeñe", "sin_dato", ["No se pudo leer el rendimiento por boca."])
    caidas = b.get("caidas") or []
    if not caidas:
        return _causa("equipo", "Bocas de ordeñe", "descartada",
                      [f"Ninguna de las {b.get('medidas', 0)} bocas medidas cayó de forma llamativa."])
    ev = [f"Boca {c['boca']}: ordeñó {c['ratio'] * 100:.0f} % de lo habitual." for c in caidas[:5]]
    nivel = "probable" if len(caidas) >= BOCAS_PROBABLE else "posible"
    return _causa("equipo", f"{len(caidas)} boca(s) casi sin ordeñar", nivel, ev,
                  revisar="Esas unidades: pulsación, lector, manguera, que no estén fuera de servicio.")


def _calor(d):
    t = d.get("ith")
    if not t or t.get("max_d") is None or t.get("max_b") is None:
        return _causa("calor", "Calor", "sin_dato", ["No se pudo obtener el clima de esos días."])
    salto = t["max_d"] - t["max_b"]
    ev = [f"ITH máximo {t['max_d']} contra {t['max_b']} de la base "
          f"({'más fresco' if salto < -1 else 'más caluroso' if salto > 1 else 'normal'})."]
    if t["max_d"] >= ITH_ESTRES and salto >= ITH_SALTO:
        nivel = "probable"
        ev.append("Por encima de 72 hay estrés calórico moderado: baja la producción.")
    elif t["max_d"] >= ITH_LEVE and salto >= ITH_SALTO:
        nivel = "posible"
    else:
        nivel = "descartada"
    return _causa("calor", "Estrés calórico" if nivel != "descartada" else "Calor", nivel, ev,
                  revisar="Sombra, ventilación y aspersión de esa tarde; el calor pega más a las vacas de alta producción.")


def _manejo(d):
    e = (d.get("eventos") or {}).get("tratamiento")
    if not e:
        return _causa("manejo", "Manejo del día anterior", "sin_dato", ["No se pudieron leer los tratamientos."])
    ayer, base = e["ayer"], e["base"]
    inusual = ayer >= MANEJO_VECES * max(base, 1) and ayer - base >= MANEJO_EXTRA
    if not inusual:
        return _causa("manejo", "Manejo del día anterior", "descartada",
                      [f"Tratamientos de ayer: {ayer} contra {_n(base, 1)} de lo normal."])
    top = (d.get("tratamientos_ayer") or [{}])[0]
    detalle = f" (sobre todo {top['nombre']}: {top['ayer']})" if top.get("nombre") else ""
    ev = [f"Ayer hubo {ayer} tratamientos contra {_n(base, 1)} de lo normal{detalle}: sacar vacas del corral, "
          "inmovilizarlas o hacerlas esperar suele bajar la leche del día siguiente."]
    t = d.get("tratadas_ayer")
    if not t or t.get("n", 0) < MANEJO_MIN_VACAS:
        ev.append("Falta confirmar si esas vacas rindieron menos que el resto: no se pudo cruzar por vaca.")
        return _causa("manejo", "Mucho trabajo sanitario el día anterior", "posible", ev,
                      revisar="Cuánto tiempo estuvieron fuera del corral y si perdieron un ordeño o comida.")
    r = d["resumen"]
    dif = t["delta_tratadas"] - t["delta_resto"]
    ev.append(f"Las {t['n']} vacas tratadas rindieron {_s(t['delta_tratadas'], 1)} kg respecto de su promedio; "
              f"el resto del rodeo, {_s(t['delta_resto'], 1)} kg.")
    for p in (t.get("por_diagnostico") or [])[:3]:
        ev.append(f"{p['nombre']}: {p['n']} vacas, {_s(p['delta'], 1)} kg cada una.")
    # Que las tratadas rindan algo menos que el resto no alcanza: hay que ver cuánto PESA. El 07/11, 73 vacas tratadas
    # -2,3 kg contra -1,0 del resto sumaban -98 kg de una baja de rinde de -2.297 kg (4 %): casi nada.
    perdido = -r["efecto_rinde"] if r["efecto_rinde"] < 0 else abs(r["kg_d"] - r["kg_b"])
    efecto = -t["efecto_kg"] if t["efecto_kg"] < 0 else 0.0
    peso = efecto / perdido if perdido > 0 else 0.0
    if dif <= MANEJO_EFECTO_KG and efecto > 0:
        ev.append(f"Efecto estimado: {_s(t['efecto_kg'])} kg en el día, {peso * 100:.0f} % de lo que bajó el rinde por vaca.")
    if dif <= MANEJO_EFECTO_KG and (peso >= MANEJO_PESO_PROBABLE or efecto >= MANEJO_PCT_PROBABLE * (r["kg_b"] or 1)):
        return _causa("manejo", "Las vacas tratadas ayer rindieron menos", "probable", ev,
                      impacto=t["efecto_kg"],
                      revisar="Cuánto tiempo estuvieron fuera del corral y si perdieron un ordeño o comida; "
                              "compensar con descanso y comida esos días.")
    if dif <= MANEJO_EFECTO_KG and peso >= MANEJO_PESO_POSIBLE:
        return _causa("manejo", "Las vacas tratadas ayer explican una parte de la baja", "posible", ev,
                      impacto=t["efecto_kg"],
                      revisar="Cuánto tiempo estuvieron fuera del corral y si perdieron un ordeño o comida.")
    if dif <= MANEJO_EFECTO_KG:
        ev.append(f"Las tratadas rindieron algo menos que el resto, pero explican solo {peso * 100:.0f} % de la baja: "
                  "la causa principal está en otro lado.")
    else:
        ev.append("Las tratadas rindieron como el resto: la actividad fue inusual pero no se nota en la leche.")
    return _causa("manejo", "Manejo del día anterior", "descartada", ev)


def _mezcla(d):
    e = (d.get("eventos") or {}).get("cambio de grupo")
    if not e:
        return _causa("mezcla", "Cambios de grupo", "sin_dato", ["No se pudieron leer los cambios de grupo."])
    dia, base = e["dia"], e["base"]
    if dia >= MEZCLA_VECES * max(base, 1) and dia - base >= MEZCLA_EXTRA:
        subio = [g for g in (d.get("grupos") or []) if g["vacas_d"] - g["vacas_b"] >= 20]
        ev = [f"{dia} cambios de grupo contra {_n(base, 1)} de lo normal."]
        ev += [f"El grupo {g['grupo']} sumó {_n(g['vacas_d'] - g['vacas_b'])} vacas." for g in subio[:2]]
        return _causa("mezcla", "Mucho movimiento entre grupos", "posible", ev,
                      revisar="Mover vacas entre grupos cambia el kg por vaca de cada grupo sin que cambie la leche total.")
    return _causa("mezcla", "Cambios de grupo", "descartada",
                  [f"{dia} cambios de grupo contra {_n(base, 1)} de lo normal."])


def _lecturas(d):
    l = d.get("lecturas")
    if not l:
        return _causa("lecturas", "Lecturas de leche", "sin_dato", ["No se pudieron leer las lecturas."])
    kg_b = d["resumen"]["kg_b"] or 1.0
    mas = l["inval_kg_d"] - l["inval_kg_b"]
    if mas >= LECTURAS_PCT * kg_b:
        return _causa("lecturas", "Más leche quedó en lecturas no válidas", "posible",
                      [f"Lecturas no válidas: {_n(l['inval_kg_d'])} kg contra {_n(l['inval_kg_b'])} de la base.",
                       "Esa leche existe pero no se le asigna a una vaca: el rinde por vaca parece menor sin serlo."],
                      impacto=-mas, revisar="Lector de caravana y medidor de leche de la sala.")
    return _causa("lecturas", "Lecturas de leche", "descartada",
                  [f"Lecturas no válidas: {_n(l['inval_kg_d'])} kg contra {_n(l['inval_kg_b'])} de la base."])


def _retiros(d):
    r = d.get("retiros")
    if not r:
        return _causa("retiros", "Leche de descarte", "sin_dato", ["No se pudo leer el retiro de leche."])
    if r["dia"] >= RETIROS_VECES * max(r["base"], 1) and r["dia"] - r["base"] >= RETIROS_EXTRA:
        return _causa("retiros", "Más vacas con leche de descarte", "posible",
                      [f"{r['dia']} vacas con retiro contra {_n(r['base'], 1)} de lo normal: esa leche no va al tanque."])
    return _causa("retiros", "Leche de descarte", "descartada",
                  [f"{r['dia']} vacas con retiro contra {_n(r['base'], 1)} de lo normal."])


def _hhmm(minutos):
    m = int(minutos) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _dur(minutos):
    h, m = divmod(int(abs(minutos)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _dm(iso):
    return f"{iso[8:10]}/{iso[5:7]}"


def _alimentacion(d):
    """La comida del mixer: a qué hora llegó la primera del día y cuánto se descargó, contra lo normal de ese tambo.

    NUNCA pasa de «posible»: el mixer da el horario y los kg, no cuánta leche costó, y con un día solo no se puede
    afirmar causa. Lo que sí hace es nombrar los OTROS días con comida tardía, para que se mire si en ellos también
    bajó el rinde por vaca: esa es la prueba que le falta."""
    titulo = "Alimentación (mixer)"
    a = d.get("alimentacion")
    if not a or not a.get("base"):
        return _causa("alimentacion", titulo, "sin_dato",
                      ["No se pudo leer lo que dio el mixer (proveedor sin conexión o sin comidas registradas en la "
                       "historia de esos días)."])
    base, dia, ayer = a["base"], a.get("dia"), a.get("ayer")
    if dia is None and ayer is None:
        return _causa("alimentacion", titulo, "sin_dato",
                      ["El mixer no registró comidas ni ese día ni el anterior (puede ser que el equipo no haya "
                       "transmitido): no se puede evaluar la comida."])

    def linea(x, nombre):
        previsto = f", previsto {x['ciclo']}" if x.get("ciclo") else ""
        cuando = "después" if x["retraso"] >= 0 else "antes"
        return (f"{nombre}: la primera comida fue a las {_hhmm(x['primera'])}{previsto}; lo habitual es "
                f"{_hhmm(base['primera_med'])} ({_dur(x['retraso'])} {cuando}, a {_n(abs(x['z']), 1)} desvíos). "
                f"Se descargaron {_n(x['kg'])} kg en {x['viajes']} viajes (lo habitual: {_n(base['kg_med'])} kg).")

    def tarde(x):
        return bool(x) and x["retraso"] >= ALIM_RETRASO_MIN and x["z"] >= Z_FUERTE

    def poca(x):
        return bool(x) and x["kg_z"] <= -Z_FUERTE and x["kg"] <= (1 - ALIM_KG_BAJO) * base["kg_med"]

    ev = [linea(dia, "Ese día")] if dia else [
        "El mixer no registró comidas ese día (puede ser que el equipo no haya transmitido): no se puede evaluar "
        "la comida de ese mismo día."]
    if ayer:
        ev.append(linea(ayer, "Ayer"))
    if tarde(dia) or tarde(ayer):
        quien = "La comida llegó tarde" if tarde(dia) else "La comida de ayer llegó tarde"
        otros = base.get("tarde") or []
        if otros:
            ev.append(f"Otros días con comida tardía en los últimos {base['dias']} días: "
                      + ", ".join(f"{_dm(t['fecha'])} ({_hhmm(t['primera'])})" for t in otros[-6:])
                      + ". Mirá si en esos días también bajó el rinde por vaca: si no, esta hora sola no alcanza para "
                        "explicar la baja.")
        else:
            ev.append(f"En los últimos {base['dias']} días no hubo ninguna otra comida así de tardía: no hay con qué "
                      "comparar si la leche bajó otros días por lo mismo.")
        return _causa("alimentacion", quien, "posible", ev,
                      revisar="Qué pasó en el mixer esa mañana (turno, falla del equipo, falta de algún insumo, "
                              "cambio de personal) y si las vacas comieron más tarde o directamente menos.")
    if poca(dia) or poca(ayer):
        return _causa("alimentacion", "Se dio menos comida de la habitual" if poca(dia)
                      else "Ayer se dio menos comida de la habitual", "posible", ev,
                      revisar="Qué se cargó y descargó ese día en el mixer y si hubo corrales que quedaron sin comida.")
    if dia is None:
        return _causa("alimentacion", titulo, "sin_dato", ev)
    return _causa("alimentacion", titulo, "descartada", ev)


_CAUSAS = (_vacas, _rinde, _grupos, _ordeno, _equipo, _calor, _manejo, _alimentacion, _mezcla, _lecturas, _retiros)


# --------------------------------------------------------------------------- veredicto
def veredicto(datos: dict) -> dict:
    """{titular, nivel, contexto[], causas[]} a partir de los datos del análisis. No modifica `datos`."""
    r = datos["resumen"]
    diff = r["kg_d"] - r["kg_b"]
    z = r.get("z_kg") or 0.0
    causas = [f(datos) for f in _CAUSAS]
    causas.sort(key=lambda c: (_ORDEN[c["nivel"]], c["impacto_kg"]))      # impacto negativo = más peso primero
    contexto = []

    rinde = next(c for c in causas if c["clave"] == "rinde")
    if (diff >= 0 or z > -Z_NORMAL) and rinde["nivel"] == "probable":
        # El total salió normal PORQUE se ordeñaron más vacas, pero cada una rindió claramente menos que lo habitual:
        # decir solo «dentro de lo normal» ocultaba justo lo que el dashboard marcaba en rojo (kg por vaca).
        nivel = "atencion"
        titular = (f"El total está normal ({_s(_pct(r['kg_d'], r['kg_b']), 1)} %), pero cada vaca rindió menos: "
                   f"{_n(r['kv_d'], 1)} kg contra {_n(r['kv_b'], 1)} ({_s(_pct(r['kv_d'], r['kv_b']), 1)} %), a "
                   f"{_n(abs(r['z_kv']), 1)} desvíos de lo habitual.")
        if r["vac_d"] > r["vac_b"] * 1.02:
            titular += f" Lo compensaron {_n(r['vac_d'] - r['vac_b'])} vacas más ordeñadas."
    elif diff >= 0 or z > -Z_NORMAL:
        nivel = "normal"
        if diff >= 0:
            titular = (f"El día produjo más que la base: {_s(_pct(r['kg_d'], r['kg_b']), 1)} % "
                       f"({_n(r['kg_d'])} contra {_n(r['kg_b'])} kg).")
        else:
            titular = (f"La baja está dentro de lo normal: {_s(_pct(r['kg_d'], r['kg_b']), 1)} % frente al promedio "
                       f"de comparación ({_n(abs(z), 1)} desvíos; el tambo varía solo ±{_n(r['sd_kg'])} kg).")
    else:
        top = causas[0]
        if top["nivel"] == "probable":
            nivel = "claro"
            titular = f"Causa más probable: {top['titulo'][0].lower() + top['titulo'][1:]}."
            if top["clave"] == "vacas" and datos.get("ausentes", {}).get("n"):
                titular = (f"Causa más probable: faltaron {datos['ausentes']['n']} vacas habituales "
                           f"({_n(datos['ausentes']['kg'])} kg que dejaron de entrar).")
        elif top["nivel"] == "posible":
            nivel = "posible"
            titular = f"Posible causa: {top['titulo'][0].lower() + top['titulo'][1:]}."
        else:
            nivel = "sin_causa"
            titular = (f"Bajó {_s(_pct(r['kg_d'], r['kg_b']), 1)} % ({_n(abs(z), 1)} desvíos) y nada de lo que mide "
                       "DelPro lo explica.")
            contexto.append("Revisá lo que DelPro no mide: la ración de ayer (mixer, carro, silo), el agua y el manejo "
                            "del día anterior.")

    ayer = r.get("ayer_kg")
    if ayer:
        p = _pct(r["kg_d"], ayer)
        if r.get("ayer_es_maximo") and p < 0:
            contexto.append(f"Contra ayer la baja parece mayor ({_s(p, 1)} %) porque ayer ({_n(ayer)} kg) fue el día más "
                            "alto del período de comparación.")
        elif p < 0:
            contexto.append(f"Contra ayer: {_s(p, 1)} % ({_n(ayer)} kg).")
    if nivel in ("normal", "atencion"):
        notables = [c for c in causas if c["nivel"] in ("probable", "posible")
                    and not (nivel == "atencion" and c["clave"] == "rinde")]
        if notables:
            contexto.append("Aun así hay señales para mirar: " + "; ".join(c["titulo"] for c in notables[:3]) + ".")
    contexto.append("Son pistas a partir de lo que registra DelPro, no una certeza.")
    return {"titular": titular, "nivel": nivel, "contexto": contexto, "causas": causas}
