# -*- coding: utf-8 -*-
"""Agente de IA que responde preguntas del tambo en lenguaje natural,
encadenando herramientas hasta tener una respuesta (o hasta reconocer que no
la tiene).

DECISIÓN DE DISEÑO, y es la que importa: las "herramientas" del agente NO son
SQL libre contra DDM. Son los mismos endpoints `/api/...` que ya usa cada
pantalla, llamados en proceso con el `test_client` de Flask. Motivo medido,
no supuesto — la lista de trampas de esquema que hay en CLAUDE.md (filtrar
por rebaño porque la base la comparten 3 tambos, `MilkTest` unido por
`AnimalHistoricalData` y no por `MilkingTestAnimal`, los tramos de flujo de
Alpro que vienen ×100, `MilkConfirmTime` que no es el fin del ordeño, `SCC`
en miles de células/ml...) es exactamente lo que un modelo escribiendo SQL
desde cero pisaría, con un número que sale mal pero no avisa. Reusar los
endpoints ya verificados evita duplicar esa lógica una segunda vez y
GARANTIZA que el agente diga lo mismo que la pantalla — no una versión
paralela que puede desviarse con el próximo hallazgo.

El SQL libre (`ai.py`, ya existía) queda como ÚLTIMO RECURSO: solo cuando
ninguna herramienta cubre la pregunta, y la respuesta final tiene que
avisarlo — "esto no pasó por ninguna pantalla verificada".

SOLO LECTURA, en tres capas independientes (ninguna depende de que el
agente "se porte bien"):
  1. El mapa de herramientas de este módulo solo contiene rutas GET. Ninguna
     ruta que escribe (POST de configuración, plantillas, umbrales) está
     alcanzable desde acá — no es una regla que el agente pueda saltarse,
     es que la función ni existe en `_ENDPOINTS`.
  2. El *fallback* de SQL libre pasa igual por `db.run_query`, que exige
     `validate_sql` (bloquea INSERT/UPDATE/DELETE/DROP/EXEC/... y cualquier
     `INTO`) antes de ejecutar nada.
  3. La conexión a DDM es del usuario `delpro_lectura` con
     `ApplicationIntent=ReadOnly` (ver `db.py`/`tambos.py`): aunque las dos
     capas de arriba fallaran, el motor de SQL Server igual rechaza una
     escritura.

El `test_client` interno abre sesión como admin sintético SOLO para poder
llamar a los endpoints que ya están protegidos con `@auth.requiere_rol` (son
pantallas de gestión, no público). Eso NO filtra hacia afuera: el endpoint
`/api/agente/preguntar` que expone este módulo está gateado con el mismo
`@auth.requiere_rol("admin")`, así que quien le puede preguntar al agente ya
podía ver esos mismos datos desde las pantallas.
"""
import datetime
import json
import os
import time
from urllib.parse import urlencode

import anthropic

import ai
import db
import tambos

MODEL = "claude-sonnet-5"
MAX_TURNOS = 8                  # tope de ida-y-vuelta con herramientas por pregunta
# Un informe pide varias herramientas, gráficos y texto largo: con 2.000 tokens la
# respuesta se corta a mitad de una sección.
MAX_TOKENS = 2000
MAX_TOKENS_INFORME = 5000
FORMATOS = ("auto", "grafico", "informe", "tabla")
# Varios endpoints devuelven 202 "calentando" mientras arman el caché por primera
# vez, y SQL Express en esta máquina es lento y compartido (ver CLAUDE.md). Medido
# en frío: la rutina de un día puede tardar más de 48s en calentar. El agente no
# es una pantalla en vivo —una respuesta por Telegram que tarda dos minutos es
# aceptable—, así que el presupuesto es generoso: hasta 160s por herramienta.
CALENTANDO_REINTENTOS = 20
CALENTANDO_ESPERA_S = 8
# Caracteres; ver `_recortar`. Contenido legítimo y curado puede ser grande:
# el tablero completo mide ~11.500 (17 indicadores con su texto de ayuda) y el
# ranking de "vacas a revisar" o de partos/secados proyectados —ya acotados a
# un top-N por la propia herramienta, no una lista sin filtrar— rondan los
# 20.000. Se conservan ENTEROS a propósito: ES la respuesta, no adorno. El
# límite protege contra lo genuinamente desmedido (un sql_libre sin filtrar,
# un rango de fechas larguísimo).
_LIMITE_TOOL_RESULT = 30000


def api_disponible() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


# --- Mapa de herramientas: nombre -> (ruta GET, parámetros que se reenvían) -
# `tambo` NO es parámetro de ninguna herramienta: lo fija `responder()` desde
# afuera (mismo criterio que la interfaz, donde el tambo es un selector fijo
# de la sesión, no algo que se escribe en cada pregunta) — así el modelo no
# puede "elegir" un tambo inventado.
_ENDPOINTS = {
    "tablero_diagnostico": ("/api/tablero", []),
    "resumen_rapido": ("/api/dashboard", []),
    "rutina_dia": ("/api/rutina", ["fecha"]),
    "rutina_evolucion": ("/api/rutina/evolucion", ["desde", "hasta"]),
    "rendimiento_sala": ("/api/rutina/rendimiento", ["desde", "hasta"]),
    "flujos_ordeno": ("/api/flujos/analisis", ["desde", "hasta"]),
    "salud_atencion": ("/api/salud/atencion_v2", []),
    "salud_rcs_informe": ("/api/salud/rcs_informe", ["desde", "hasta", "ref_max"]),
    "reproduccion_resultados": ("/api/reproduccion/resultados",
                               ["desde1", "hasta1", "desde2", "hasta2"]),
    "reproduccion_tasa_prenez": ("/api/reproduccion/tasa_prenez", ["desde", "hasta", "tipo"]),
    "reproduccion_gestacion": ("/api/reproduccion/gestacion", ["desde", "hasta"]),
    "partos_secados": ("/api/reproduccion/partos_secados", ["categoria"]),
    "proyeccion_rebanos": ("/api/proyeccion/rebanos", ["desde", "hasta"]),
    "alimentacion_conversion": ("/api/alimentacion/conversion", ["dias"]),
    "ficha_animal": ("/api/animal/ficha", ["rp"]),
}

_TOOLS = [
    {
        "name": "tablero_diagnostico",
        "description": (
            "Panorama general del tambo: 17 indicadores con semáforo verde/amarillo/rojo "
            "contra los umbrales que el propio tambo configuró — litros por vaca, score de "
            "rutina, horas de ordeño, vacas por puesto y por persona, RCS, UFC, % de "
            "identificación, mortandad de terneros, alarmas de IoT, días abiertos. USAR "
            "PRIMERO para cualquier pregunta general tipo 'cómo anda el tambo' o 'hay algo mal'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resumen_rapido",
        "description": (
            "Producción de los últimos 30 días y estado reproductivo actual, en pocos "
            "números. Para preguntas simples tipo '¿cuánta leche se está produciendo?'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rutina_dia",
        "description": (
            "Calidad de la rutina de ordeño de UN día puntual, separada por sesión "
            "(mañana/mediodía/tarde): score 0-100 y sus componentes — identificación de "
            "vacas, colocación de pezonera (o el tramo entrada→leche en sala convencional), "
            "vacas lerdas, tiempos muertos entre y dentro de rodeo, mezcla de rodeos, "
            "ocupación o tiempo vacío entre mangadas, estímulo/bimodalidad — más los peores "
            "casos concretos de esa sesión. Para 'cómo fue el ordeño de tal día'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "AAAA-MM-DD. Vacío = el último día con datos."},
            },
        },
    },
    {
        "name": "rutina_evolucion",
        "description": (
            "Tendencia del score de rutina y de cada uno de sus componentes a lo largo de "
            "un rango de fechas (hasta un año). Para '¿mejoró la rutina esta semana/mes?', "
            "'¿cómo viene el % de identificación en el tiempo?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD"},
                "hasta": {"type": "string", "description": "AAAA-MM-DD"},
            },
        },
    },
    {
        "name": "rendimiento_sala",
        "description": (
            "Throughput de la sala por sesión, para un rango de hasta 31 días: rotaciones "
            "o tandas, ordeños por hora, litros por hora, vacas identificadas vs "
            "desconocidas. Es capacidad/eficiencia de la sala, NO calidad de rutina — para "
            "eso usar rutina_dia o rutina_evolucion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD"},
                "hasta": {"type": "string", "description": "AAAA-MM-DD"},
            },
        },
    },
    {
        "name": "flujos_ordeno",
        "description": (
            "Calidad del flujo de leche durante el ordeño, para un rango de hasta 120 días: "
            "bimodalidad (mala estimulación), retiradas prematuras/tardías o forzadas "
            "(cuando el equipo publica su umbral), tiempo de colocación, arranque lento, "
            "litros por bajada, tiempo entre ordeños. Para preguntas sobre mastitis por mal "
            "manejo de la pezonera, no de rutina general."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD"},
                "hasta": {"type": "string", "description": "AAAA-MM-DD"},
            },
        },
    },
    {
        "name": "salud_atencion",
        "description": (
            "Ranking de vacas que conviene revisar, según el índice propio que combina RCS "
            "(mastitis subclínica), conductividad, caída de producción, BCS y genética, con "
            "el motivo de cada una en texto. Para '¿qué vacas hay que mirar?', '¿cuántas con "
            "mastitis probable?'. Es un índice EXPERIMENTAL en validación, no un diagnóstico."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "salud_rcs_informe",
        "description": (
            "Informe de células somáticas (RCS) de un período (por defecto los últimos 90 "
            "días): % de vacas con RCS > 300.000 y RCS promedio en cada control lechero, "
            "comparación con el período anterior de igual duración, desglose por rodeo, "
            "producción de las vacas según su RCS, infecciones nuevas / curación / crónicas "
            "entre controles, curación durante el seco, y la LECHE QUE SE PIERDE por RCS "
            "alto (kg/día contra vacas sanas comparables) con su VALOR EN PESOS según el precio "
            "de la leche que cargó el tambo. Para 'cómo anduvieron las células somáticas', "
            "'cuánto nos cuesta el RCS', 'qué impacto económico tuvo la mastitis'. Los controles "
            "lecheros son mensuales: un período de menos de 2 meses trae un solo punto. Si el "
            "tambo no cargó el precio de la leche, el informe lo dice (`impacto_economico.falta`) "
            "y no hay pesos: no inventes un precio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD. Sin esto: 90 días antes de `hasta`."},
                "hasta": {"type": "string", "description": "AAAA-MM-DD. Sin esto: hoy."},
                "ref_max": {"type": "integer", "enum": [1, 2],
                            "description": "Hasta qué RCS una vaca se considera sana para comparar: "
                                           "1 = hasta 100.000, 2 = hasta 200.000 (por defecto, el más conservador)."},
            },
        },
    },
    {
        "name": "reproduccion_resultados",
        "description": (
            "Indicadores reproductivos del rodeo contra las metas que configuró el tambo "
            "(servicio, concepción, preñez), comparando DOS períodos entre sí. Para '¿cómo "
            "viene la reproducción?', '¿mejoró contra el año pasado?'. Sin parámetros compara "
            "el año calendario pasado contra el actual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde1": {"type": "string", "description": "AAAA-MM-DD, inicio del período 1 (referencia)"},
                "hasta1": {"type": "string", "description": "AAAA-MM-DD, fin del período 1"},
                "desde2": {"type": "string", "description": "AAAA-MM-DD, inicio del período 2 (a evaluar)"},
                "hasta2": {"type": "string", "description": "AAAA-MM-DD, fin del período 2"},
            },
        },
    },
    {
        "name": "reproduccion_tasa_prenez",
        "description": (
            "Embudo aptas → celo → servicio → preñez, por ciclo de 21 días o por mes, para "
            "un rango de hasta 800 días. OJO: los últimos ~2 meses quedan con la concepción "
            "censurada (un servicio tarda ~35 días en poder chequearse) — no leer como una "
            "caída real sin aclararlo. Para '¿cuál es la tasa de preñez de tal mes?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD"},
                "hasta": {"type": "string", "description": "AAAA-MM-DD"},
                "tipo": {"type": "string", "enum": ["vaca", "vaquillona", "todas"],
                        "description": "Categoría de animal. Vacío = vacas."},
            },
        },
    },
    {
        "name": "reproduccion_gestacion",
        "description": (
            "Duración real de las gestaciones por mes de parto, contra el parámetro de días "
            "de gestación configurado en DelPro. Para chequear si ese parámetro está bien "
            "calibrado, o para explicar un corrimiento en fechas de parto/secado proyectadas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM-DD"},
                "hasta": {"type": "string", "description": "AAAA-MM-DD"},
            },
        },
    },
    {
        "name": "partos_secados",
        "description": (
            "Próximos partos y secados esperados, animal por animal, y la proyección "
            "mensual de vacas en ordeñe que resulta de eso. Para '¿cuántas vacas paren este "
            "mes?', '¿a quién hay que secar?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria": {"type": "string", "description": "'todas' (por defecto), o una categoría del catálogo del tambo."},
            },
        },
    },
    {
        "name": "proyeccion_rebanos",
        "description": (
            "Proyección mensual de vacas lactantes y producción esperada, real hacia atrás "
            "y proyectada hacia adelante. Para '¿cuántas vacas vamos a tener en ordeñe en 3 "
            "meses?', '¿cómo viene la producción proyectada?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "desde": {"type": "string", "description": "AAAA-MM, mes de inicio"},
                "hasta": {"type": "string", "description": "AAAA-MM, mes de fin"},
            },
        },
    },
    {
        "name": "alimentacion_conversion",
        "description": (
            "Eficiencia de conversión (kg de sólidos por kg de materia seca) por grupo de "
            "alimentación, para los últimos N días (28 por defecto). Es una medida de GRUPO, "
            "no de vaca individual — sin comederos individuales, ordenar por conversión "
            "dentro de un grupo es ordenar por sólidos con otro nombre. Para '¿qué rodeo "
            "convierte mejor/peor?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dias": {"type": "integer", "description": "Ventana en días, máximo 120. Vacío = 28."},
            },
        },
    },
    {
        "name": "ficha_animal",
        "description": (
            "Ficha completa de UNA vaca puntual por su número visible (RP): datos generales, "
            "pedigrí, historial de eventos, producción diaria reciente, condición corporal "
            "(BCS), test de leche. Para '¿cómo está la vaca 351?', '¿qué eventos tuvo la "
            "vaca X?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rp": {"type": "integer", "description": "Número de la vaca (RP), el que se ve en el caravaneo."},
            },
            "required": ["rp"],
        },
    },
    {
        "name": "sql_libre",
        "description": (
            "ÚLTIMO RECURSO. Traduce una pregunta puntual a una consulta SELECT contra la "
            "base DDM cuando NINGUNA otra herramienta cubre lo que se pregunta. El número que "
            "devuelve NO pasó por ninguna pantalla verificada del sistema: usarlo solo si es "
            "imprescindible, y decirlo con todas las letras en la respuesta final."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pregunta": {"type": "string",
                            "description": "La pregunta puntual a traducir a SQL, en lenguaje natural y en español."},
            },
            "required": ["pregunta"],
        },
    },
]


# --- Gráficos ------------------------------------------------------------------
# `graficar` NO consulta nada: dibuja con números que el modelo ya recibió de las
# otras herramientas. Es una herramienta de PRESENTACIÓN, así que no abre ninguna
# vía nueva a la base (las tres capas de solo-lectura del docstring siguen igual).
#
# El riesgo propio de un gráfico es que muestre un número que ninguna herramienta
# devolvió (el modelo "redondea para que cierre", o arma una serie a ojo). Un
# número en un texto se lee con desconfianza; el mismo número en un gráfico se lee
# como un dato. Por eso cada valor se busca entre los números que de verdad
# devolvieron las herramientas de ESTA pregunta, y el gráfico sale marcado si
# alguno no aparece. No es una prueba de que el valor esté bien —un cálculo
# legítimo del modelo, como un promedio, también queda marcado—, pero obliga a
# decirlo en vez de dejar pasar un número sin origen.
MAX_GRAFICOS = 6
MAX_PUNTOS = 60
MAX_SERIES = 6
_TIPOS_GRAFICO = ("line", "bar", "pie")

_TOOL_GRAFICAR = {
    "name": "graficar",
    "description": (
        "Dibuja un gráfico en la pantalla del usuario con datos que YA te devolvieron las demás "
        "herramientas. No consulta nada. Usalo cuando los datos son una evolución en el tiempo "
        "(line), una comparación entre categorías (bar: rodeos, tramos, períodos) o una "
        "composición de pocas partes (pie, hasta 8). Los valores tienen que ser los que "
        "devolvió la herramienta — NO inventes, NO interpoles ni completes puntos que faltan "
        "(poné null). Si calculás un valor vos, el gráfico queda marcado como no verificado. "
        "Devuelve un `marcador` como [[g1]]: ponelo en una línea sola dentro de tu respuesta "
        "donde querés que aparezca el gráfico."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tipo": {"type": "string", "enum": list(_TIPOS_GRAFICO)},
            "titulo": {"type": "string", "description": "Qué muestra, con el período. Ej: 'RCS promedio por control, jul–oct 2026'."},
            "etiquetas": {"type": "array", "items": {"type": "string"},
                          "description": "Eje X: fechas, rodeos, tramos. Máximo 60."},
            "series": {
                "type": "array",
                "description": "Una o más series (máximo 6). Cada una tiene un valor por etiqueta, en el mismo orden.",
                "items": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "valores": {"type": "array", "items": {"type": ["number", "null"]}},
                    },
                    "required": ["nombre", "valores"],
                },
            },
            "unidad": {"type": "string", "description": "Unidad del eje Y: '%', 'kg/día', 'cél/ml', '$'. Opcional."},
        },
        "required": ["tipo", "titulo", "etiquetas", "series"],
    },
}


def _numeros_de(obj, destino: list) -> None:
    """Junta todos los números que aparecen en un resultado de herramienta."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        destino.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numeros_de(v, destino)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numeros_de(v, destino)


def _esta_entre(valor: float, ordenados: list) -> bool:
    """¿Hay algún número devuelto por una herramienta que coincida con `valor`?
    Tolerancia: lo que se pierde al redondear a un decimal (0,06) o 0,6% —un valor
    en miles que el modelo escribió abreviado—, lo que sea mayor."""
    import bisect
    tol = max(0.06, 0.006 * abs(valor))
    i = bisect.bisect_left(ordenados, valor - tol)
    return i < len(ordenados) and ordenados[i] <= valor + tol


def _validar_grafico(args: dict, numeros: list) -> tuple[dict | None, str | None]:
    """(gráfico, None) o (None, motivo). El motivo vuelve al modelo para que corrija."""
    tipo = args.get("tipo")
    if tipo not in _TIPOS_GRAFICO:
        return None, f"`tipo` tiene que ser uno de {list(_TIPOS_GRAFICO)}."
    etiquetas = args.get("etiquetas")
    if not isinstance(etiquetas, list) or not etiquetas or len(etiquetas) > MAX_PUNTOS:
        return None, f"`etiquetas` tiene que ser una lista de 1 a {MAX_PUNTOS} elementos."
    series = args.get("series")
    if not isinstance(series, list) or not series or len(series) > MAX_SERIES:
        return None, f"`series` tiene que tener de 1 a {MAX_SERIES} series."
    limpias = []
    for s in series:
        valores = s.get("valores") if isinstance(s, dict) else None
        if not isinstance(valores, list) or len(valores) != len(etiquetas):
            return None, ("Cada serie tiene que traer exactamente un valor por etiqueta "
                          f"({len(etiquetas)}); usá null donde no haya dato.")
        for v in valores:
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                return None, "Los valores tienen que ser números o null."
        limpias.append({"nombre": str(s.get("nombre") or "")[:60], "valores": valores})
    if tipo == "pie":
        if len(limpias) != 1 or len(etiquetas) > 8:
            return None, "Un gráfico de torta lleva UNA serie y hasta 8 categorías."
        if any(v is None or v < 0 for v in limpias[0]["valores"]):
            return None, "En una torta todos los valores tienen que ser números no negativos."
    elif len(limpias) > 1:
        # Medido en una corrida real: el modelo puso "RCS promedio (cél/ml)" con valores de
        # 92.312 a 157.743 y "% de vacas altas" con 6,9 a 12,3 en el MISMO eje. La segunda
        # serie queda como una línea pegada al cero que no se puede leer. Series con una
        # escala tan distinta son magnitudes distintas: un gráfico para cada una.
        tope = {s["nombre"] or f"serie {i + 1}": max((abs(v) for v in s["valores"] if v is not None), default=0)
                for i, s in enumerate(limpias)}
        con_valor = {n: m for n, m in tope.items() if m > 0}
        if len(con_valor) > 1 and max(con_valor.values()) / min(con_valor.values()) > 20:
            detalle = ", ".join(f"{n} llega a {m:g}" for n, m in con_valor.items())
            return None, ("Las series tienen escalas muy distintas en un mismo eje (" + detalle + "): la chica "
                          "queda aplastada contra el cero. Son magnitudes distintas —por ejemplo cél/ml y %—: "
                          "hacé un gráfico separado para cada una.")
    ordenados = sorted(numeros)
    sin_origen = sorted({v for s in limpias for v in s["valores"]
                         if v is not None and not _esta_entre(float(v), ordenados)})
    return {
        "tipo": tipo, "titulo": str(args.get("titulo") or "")[:140],
        "etiquetas": [str(e)[:40] for e in etiquetas], "series": limpias,
        "unidad": str(args.get("unidad") or "")[:20],
        "valores_sin_origen": sin_origen[:12],
    }, None


# --- Formato de salida que elige el usuario -------------------------------------
_INSTRUCCIONES_GRAFICOS = """

GRÁFICOS, TABLAS E INFORMES (la respuesta se ve en una pantalla, no por chat):
- Tenés la herramienta `graficar` para dibujar con datos que ya te devolvieron las \
otras herramientas. Para un dato suelto ("¿cuántas vacas hay?") contestá en una \
línea, sin gráfico. Graficá cuando hay una evolución en el tiempo (3 puntos o más) \
o una comparación entre rodeos/tramos/períodos.
- Un gráfico tiene UNA sola unidad: si querés mostrar dos magnitudes (cél/ml y %, kg y \
vacas), hacé un gráfico para cada una.
- Cada valor del gráfico tiene que ser uno que te devolvió una herramienta. No \
inventes, no interpoles, no completes puntos que faltan (null). Si tuviste que \
calcular un valor (un promedio, un total), decilo en el texto.
- El marcador [[gN]] solo existe después de que `graficar` te lo devolvió: no escribas \
uno sin haber llamado a la herramienta, ni inventes números de marcador.
- Escribís para un tambero, no para un programador: no nombres herramientas, campos ni \
parámetros internos (nada de `ref_max`, `salud_atencion`, `impacto_economico`...). Decí \
qué mirar en la pantalla o qué dato falta, con palabras del tambo.
- Las tablas van en markdown (| col | col |) con la fila de separación (|---|---|).
- PLATA: nunca estimes pesos con un precio que no te haya devuelto una herramienta. Si \
el resultado trae `impacto_economico.falta`, decí que falta cargar el precio de la \
leche en la planilla de precios y dá la pérdida en kg. Cuando des un valor en pesos, \
aclará el precio por litro usado, de dónde sale (`precio_origen`), y qué NO incluye (los \
resultados traen `no_incluye` y `supuestos`: copialos, no los resumas fuera de lugar).
- Si el resultado trae `AVISO_PRECIO_ESTIMADO` o `precio_estimado: true`, el precio de la \
leche es un valor de ejemplo, no el del tambo: poné ese aviso bien visible (en negrita, al \
lado de cada cifra en pesos y en la sección de impacto económico), y nunca presentes esos \
pesos como el costo real. Los kg de leche perdida SÍ son datos medidos.
"""

_FORMATO = {
    "auto": ("Formato: AUTOMÁTICO. Elegí la forma que mejor sirva a la pregunta: texto corto para un "
             "dato; gráfico si hay evolución o comparación; informe si el usuario lo pide o la pregunta "
             "es un análisis de un tema (por ejemplo \"cómo anduvo X estos meses y qué impacto tuvo\")."),
    "grafico": ("Formato elegido por el usuario: GRÁFICO. Respondé con al menos un gráfico (`graficar`) que "
                "muestre lo pedido, y debajo un texto corto de 2 a 4 líneas con lo que se ve y los números "
                "clave. Poné el marcador del gráfico en una línea sola."),
    "tabla": ("Formato elegido por el usuario: TABLA. Respondé con una tabla markdown con los datos pedidos "
              "(una fila por rodeo, período o vaca, según la pregunta), las unidades en los encabezados, y "
              "debajo una línea con lo más importante. No hace falta gráfico."),
    "informe": (
        "Formato elegido por el usuario: INFORME DETALLADO. Es un documento para leer e imprimir, no un chat. "
        "Primero consultá TODAS las herramientas que hagan falta (período pedido y, si la herramienta lo trae, "
        "el período anterior para comparar). Después escribí el informe en markdown con estas secciones, en "
        "este orden, salteando solo las que no tengan datos:\n"
        "## Resumen — 3 a 5 líneas: qué pasó, el número que más importa y la conclusión.\n"
        "## Cómo evolucionó — la tendencia en el tiempo, con un gráfico (`graficar`, marcador en una línea sola) "
        "y la comparación contra el período anterior.\n"
        "## Dónde está el problema — por rodeo / tramo / vaca: una tabla markdown y, si ayuda, un gráfico de "
        "barras.\n"
        "## Impacto económico — leche perdida y su valor en pesos con el precio usado, o la aclaración de que "
        "falta cargarlo; qué no incluye el número.\n"
        "## Qué conviene mirar — 3 a 5 acciones concretas que SALGAN de los datos (qué rodeo, qué tipo de "
        "vaca). Sin consejos genéricos.\n"
        "## Datos y límites — período, de dónde sale cada dato, cuántas vacas/controles hay detrás, qué "
        "es una estimación y qué no está incluido.\n"
        "Títulos cortos, frases directas, cada número con su unidad. Un informe no es una conversación: no "
        "preguntes nada al final."),
}


def _cliente_interno():
    # Import diferido: `app.py` importa este módulo para registrar el
    # endpoint, así que importarlo acá arriba en frío formaría un ciclo. Para
    # cuando se llama a esta función la app ya terminó de inicializar.
    import app as _app
    client = _app.app.test_client()
    with client.session_transaction() as s:
        s["usuario"] = "agente-ia"
        s["rol"] = "admin"
    return client


def _armar_query_string(args: dict) -> str:
    limpios = {k: v for k, v in args.items() if v not in (None, "")}
    return urlencode(limpios)


def _llamar_endpoint(ruta: str, args: dict, tambo: str, client) -> dict:
    """GET al endpoint, con reintento mientras el caché está calentando (202).
    Nunca lanza: un error de la app o una fecha inválida vuelve como
    {"error": "..."} para que el propio modelo lo lea y se lo explique al
    usuario, en vez de que la pregunta completa se caiga por una sola
    herramienta."""
    qs = _armar_query_string({**args, "tambo": tambo})
    url = f"{ruta}?{qs}" if qs else ruta
    for _ in range(CALENTANDO_REINTENTOS):
        try:
            r = client.get(url)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"No se pudo completar la consulta: {exc}"}
        data = r.get_json(silent=True)
        if data is None:
            return {"error": f"Respuesta inesperada del servidor (HTTP {r.status_code})."}
        if r.status_code == 202 or data.get("calentando"):
            time.sleep(CALENTANDO_ESPERA_S)
            continue
        return data
    return {"error": "El servidor está tardando en calcular esto (base de datos ocupada). "
                     "Probá de nuevo en un minuto, o preguntá algo más puntual."}


def _sql_libre(pregunta: str, tambo: str) -> dict:
    if not pregunta:
        return {"error": "Falta la pregunta a traducir."}
    # Mismo candado que ya tiene `/api/preguntar`: no se corre SQL generado por
    # IA contra una base de producción en vivo. Las herramientas de la lista
    # fija SÍ corren ahí (son las mismas consultas auditadas que usa cada
    # pantalla) — el riesgo es específico de SQL escrito por el modelo en el
    # momento, no de leer datos.
    if tambos.es_produccion(tambo):
        return {"error": "El SQL generado por IA está deshabilitado para bases de producción en "
                         "vivo, por seguridad. Esta pregunta puntual no se puede responder con las "
                         "demás herramientas — decilo así en la respuesta."}
    try:
        plan = ai.pregunta_a_sql(pregunta)
        data = db.run_query(plan["sql"], tambo=tambo)  # solo-lectura: ver el docstring del módulo
    except Exception as exc:  # noqa: BLE001
        return {"error": f"No se pudo traducir o ejecutar la consulta: {exc}"}
    return {
        "titulo": plan.get("titulo"), "supuestos": plan.get("supuestos"),
        "columnas": data["columns"], "filas": data["rows"][:80],
        "truncado": len(data["rows"]) > 80,
        "AVISO": "Esto NO pasó por ninguna pantalla verificada del sistema — es SQL generado "
                 "en el momento. Decilo en la respuesta.",
    }


def _recortar(nombre: str, data) -> dict:
    """Poda lo que se sabe que es pesado por diseño, y de respaldo trunca
    cualquier cosa que igual haya quedado enorme.

    `rutina_dia` y `rendimiento_sala` comparten el mismo campo pesado por el
    mismo motivo: `grupos` (o `retiradas_grupo_actual`, su variante) es un
    renglón por CADA bloque contiguo de rodeo, y en una sala convencional un
    rodeo puede entrar en 100-200 bloques por mezcla de vacas sueltas (ver
    CLAUDE.md) — mide 20.000+ caracteres por sesión, más que el resto de la
    respuesta junta. `rutina_dia` además trae `visitas`, un renglón por
    ordeño individual (300 a 2.400 según la sesión).

    Lo que sí queda entero es lo agregado — `detalle`/`hallazgos` en
    `rutina_dia`, las métricas de throughput en `rendimiento_sala` — que es
    lo mismo que necesitaría un humano leyendo la pantalla para explicar el
    día, sin la lista renglón por renglón."""
    PESADOS = {
        "rutina_dia": ("visitas", "grupos"),
        "rendimiento_sala": ("grupos", "retiradas_grupo_actual"),
    }
    if isinstance(data, dict) and nombre in PESADOS and "sesiones" in data:
        campos = PESADOS[nombre]
        data = dict(data)
        data["sesiones"] = [
            {k: v for k, v in s.items() if k not in campos} | {
                f"{campo}_omitido_por_extenso": len(s[campo])
                for campo in campos if campo in s
            }
            for s in data["sesiones"]
        ]
    if len(json.dumps(data, ensure_ascii=False, default=str)) <= _LIMITE_TOOL_RESULT:
        return data
    return _recorte_generico(data)


def _recorte_generico(data: dict) -> dict:
    """Respaldo para lo que no tiene una poda específica (arriba) y de todos
    modos superó el límite: acorta la lista MÁS PESADA del payload a sus
    primeros elementos, en vez de cortar el texto JSON a la mitad.

    Cortar el texto crudo es tentador —es una línea— pero larga bien la mitad
    de un objeto: el modelo recibiría JSON inválido y podría leer cualquier
    cosa del fragmento roto. Acortar una lista entera deja SIEMPRE JSON válido,
    y al ser la lista más pesada la que se acorta, es también la que más
    probablemente sea una tabla de filas repetidas (donde las primeras N ya
    dan la idea) y no un resumen agregado (que por chico ya entraba solo)."""
    if not isinstance(data, dict):
        return {"aviso": "El resultado es muy grande y no se pudo acortar automáticamente."}
    listas = {k: v for k, v in data.items() if isinstance(v, list) and v}
    if not listas:
        return {"aviso": "El resultado es muy grande incluso sin listas para acortar; "
                         "pedí algo más puntual."}
    clave = max(listas, key=lambda k: len(json.dumps(listas[k], ensure_ascii=False, default=str)))
    recortado = dict(data)
    original = len(listas[clave])
    n = original
    while n > 1:
        n = max(1, n // 2)
        recortado[clave] = listas[clave][:n]
        if len(json.dumps(recortado, ensure_ascii=False, default=str)) <= _LIMITE_TOOL_RESULT:
            break
    recortado[f"{clave}_aviso"] = f"se muestran {n} de {original} — pedí un rango más chico para ver el resto"
    return recortado


def _ejecutar_herramienta(nombre: str, args: dict, tambo: str, client) -> dict:
    if nombre == "sql_libre":
        resultado = _sql_libre(args.get("pregunta", ""), tambo)
    elif nombre in _ENDPOINTS:
        ruta, permitidos = _ENDPOINTS[nombre]
        filtrados = {k: args.get(k) for k in permitidos if k in args}
        resultado = _llamar_endpoint(ruta, filtrados, tambo, client)
    else:
        resultado = {"error": f"Herramienta desconocida: {nombre!r}."}
    return _recortar(nombre, resultado)


_SYSTEM = """Sos el analista de datos de {tambo}, un tambo lechero que usa LactIA \
para su gestión. Respondés preguntas en español, directo y sin vueltas — un \
tambero necesita el número y qué significa, no un informe.

Hoy es {hoy}. Cuando una herramienta pida una fecha, calculá vos mismo \
las fechas absolutas (AAAA-MM-DD) a partir de hoy.

CÓMO TRABAJAR:
- Preferí SIEMPRE una herramienta de la lista a inventar un cálculo. Cada una \
ya resuelve las trampas de este esquema (qué rodeo es de este tambo y cuál es \
de otro que comparte la base, cómo se mide cada indicador según el tipo de \
sala, qué umbral usar) — son las mismas que arma cada pantalla del sistema.
- Si hace falta más de un dato para responder, pedí varias herramientas antes \
de contestar. Mejor una respuesta completa un poco más tarde que una a medias \
ahora.
- Si una herramienta devuelve un error o dice que no hay datos, decilo tal \
cual — no lo completes ni lo redondees para que "cierre".
- Usá sql_libre solo cuando de verdad ninguna otra herramienta cubre la \
pregunta, y avisá en la respuesta que ese número no pasó por una pantalla \
verificada.
- Si no podés responder con lo que tenés, decilo derecho: "no tengo ese dato" \
es una respuesta válida, inventar uno parecido no lo es.
- Citá de qué rango de fechas sale cada número si no es obvio por el contexto \
de la pregunta.
- No sos un asistente general: si preguntan algo que no tiene que ver con \
este tambo, decilo y no respondas.
"""

# Addendum de tono para el chat de WhatsApp (ver app.py::webhook_whatsapp):
# la pantalla web tolera una respuesta con forma de informe, pero por chat
# suena a mensaje de error de un sistema, no a alguien contestando. Se separa
# del _SYSTEM base para no tocarle el tono a "Preguntale a IA" del dashboard.
_ESTILO_WHATSAPP = """

Estás respondiendo por WhatsApp, no en una pantalla con texto largo: escribí \
como le responderías a alguien por chat. Frases cortas, tono natural y \
cercano — nunca nombres herramientas ni campos internos ("calculando: true", \
"0 puntos calculados", "la herramienta X no devolvió"): contá lo que pasa \
con tus propias palabras. Si falta un dato, decilo simple ("ese número \
todavía no está calculado, probá de nuevo en un rato") en vez de describir \
el estado interno del sistema. Esto es de FORMA, no de fondo: seguís sin \
inventar números ni redondear para que cierre.
"""


def _mensaje_sistema(tambo: str, estilo: str | None = None, formato: str | None = None) -> str:
    base = _SYSTEM.format(tambo=tambos.nombre_de(tambo), hoy=datetime.date.today().isoformat())
    if estilo == "whatsapp":
        # Por WhatsApp no se dibuja nada: ni gráficos ni informes (ver `responder`).
        return base + _ESTILO_WHATSAPP
    return base + _INSTRUCCIONES_GRAFICOS + "\n" + _FORMATO[formato if formato in _FORMATO else "auto"]


def responder(pregunta: str, tambo: str, historial: list | None = None, estilo: str | None = None,
              formato: str | None = None) -> dict:
    """Responde una pregunta encadenando herramientas hasta `MAX_TURNOS`
    veces. Devuelve {"respuesta": str, "pasos": [{"herramienta", "args"}, ...],
    "mensajes": [...], "graficos": [...]} — `mensajes` sirve para seguir la
    conversación (pasarlo de vuelta como `historial` en la próxima pregunta).
    `estilo="whatsapp"` ajusta el tono de la respuesta final, no las herramientas
    ni la precisión, y desactiva `graficar` (un chat no dibuja).

    `formato` ("auto", "grafico", "informe", "tabla") es lo que eligió el usuario en
    la pantalla; solo cambia CÓMO se presenta la respuesta, nunca de dónde salen los
    datos. `graficos` son los gráficos que armó el modelo con `graficar`, listos
    para dibujar; el texto de la respuesta los ubica con marcadores [[g1]]."""
    client = _cliente_interno()
    mensajes = list(historial or []) + [{"role": "user", "content": pregunta}]
    pasos = []
    graficos: list[dict] = []
    numeros: list[float] = []          # todo número que devolvió una herramienta en ESTA pregunta
    con_graficos = estilo != "whatsapp"
    herramientas = _TOOLS + [_TOOL_GRAFICAR] if con_graficos else _TOOLS
    max_tokens = MAX_TOKENS_INFORME if (formato == "informe" and con_graficos) else MAX_TOKENS
    system = _mensaje_sistema(tambo, estilo, formato)
    # La MISMA herramienta con los MISMOS argumentos, dos veces en una sola
    # respuesta, no es una segunda medición — es la misma pregunta otra vez.
    # Medido: el modelo llamó `reproduccion_resultados({})` dos veces seguidas
    # sin necesidad. Contra un SQL Express lento y compartido (ver CLAUDE.md)
    # eso puede ser cien segundos tirados. Se memoiza por (nombre, argumentos)
    # durante ESTA pregunta — no entre preguntas, donde sí puede haber cambiado
    # algo real — así que la segunda vez ni siquiera toca la base.
    cache: dict[str, dict] = {}

    exigio_graficos = False
    for turno in range(MAX_TURNOS):
        resp = _client().messages.create(
            model=MODEL, max_tokens=max_tokens, system=system,
            tools=herramientas, messages=mensajes,
        )
        bloques_salida = [b.model_dump() for b in resp.content]
        mensajes.append({"role": "assistant", "content": bloques_salida})

        usos = [b for b in resp.content if b.type == "tool_use"]
        if not usos:
            texto = "".join(b.text for b in resp.content if b.type == "text")
            # Medido en una corrida real: con "Informe" el modelo escribió [[g1]] y [[g2]]
            # en el texto sin haber llamado nunca a `graficar` — marcadores que no apuntan
            # a nada. Si el usuario pidió gráfico o informe y no hay ninguno, se le pide
            # UNA vez que los arme (con una sola vuelta alcanza: ya tiene los datos). Si
            # de verdad no hay nada que graficar, la consigna le deja decirlo.
            if (con_graficos and formato in ("grafico", "informe") and not graficos
                    and not exigio_graficos and turno + 1 < MAX_TURNOS):
                exigio_graficos = True
                mensajes.append({"role": "user", "content": (
                    f"El usuario eligió el formato {formato.upper()} y todavía no armaste ningún gráfico: "
                    "los marcadores [[gN]] solo existen si antes llamaste a la herramienta `graficar`. "
                    "Llamala ahora con los datos que ya tenés (uno por cada gráfico que pensabas poner) y "
                    "después volvé a escribir la respuesta completa con los marcadores que ella te devuelva. "
                    "Si de verdad no hay datos para graficar, decilo en una línea y respondé sin marcadores.")})
                continue
            if resp.stop_reason == "max_tokens":
                # Un informe cortado a mitad de una frase parece terminado y no lo está.
                texto += ("\n\n_(La respuesta se cortó por largo. Pedí una sección puntual o un período "
                          "más corto para ver el resto.)_")
            texto = _sin_marcadores_huerfanos(texto, graficos)
            return {"respuesta": texto, "pasos": pasos, "mensajes": mensajes,
                    "graficos": _graficos_finales(texto, graficos)}

        resultados = []
        for bloque in usos:
            args = bloque.input or {}
            clave = f"{bloque.name}:{json.dumps(args, sort_keys=True, default=str)}"
            repetida = clave in cache
            if repetida:
                resultado = cache[clave]
            elif bloque.name == "graficar" and con_graficos:
                resultado = _armar_grafico(args, numeros, graficos)
                cache[clave] = resultado
            else:
                resultado = _ejecutar_herramienta(bloque.name, args, tambo, client)
                _numeros_de(resultado, numeros)
                cache[clave] = resultado
            pasos.append({"herramienta": bloque.name, "args": bloque.input, "repetida": repetida})
            resultados.append({
                "type": "tool_result", "tool_use_id": bloque.id,
                "content": json.dumps(resultado, ensure_ascii=False, default=str),
            })
        mensajes.append({"role": "user", "content": resultados})

    return {
        "respuesta": "No llegué a terminar de responder dentro del tiempo permitido — "
                     "probá con una pregunta más puntual.",
        "pasos": pasos, "mensajes": mensajes, "graficos": graficos,
    }


def _sin_marcadores_huerfanos(texto: str, graficos: list) -> str:
    """Saca los [[gN]] que no corresponden a ningún gráfico armado, y las líneas que
    quedan vacías: un marcador sin gráfico no es nada que el usuario pueda ver."""
    import re
    ids = {g["id"] for g in graficos}
    limpio = re.sub(r"\[\[(g\d+)\]\]", lambda m: m.group(0) if m.group(1) in ids else "", texto)
    return re.sub(r"\n{3,}", "\n\n", limpio).strip()


def _graficos_finales(texto: str, graficos: list) -> list:
    """Qué gráficos se muestran. Los que el texto ubica con su marcador, siempre; los
    que el modelo armó y no ubicó, solo si todos sus valores tienen origen — un gráfico
    marcado como no verificado que el texto ya no menciona es casi seguro uno que el
    modelo corrigió con otra llamada, y mostrar los dos dejaría a la vista el malo."""
    return [g for g in graficos
            if f"[[{g['id']}]]" in texto or not g["valores_sin_origen"]]


def _armar_grafico(args: dict, numeros: list, graficos: list) -> dict:
    """Ejecuta `graficar`: valida, anota el gráfico en `graficos` y devuelve lo que
    el modelo necesita saber (el marcador para ubicarlo, y si algún valor no tiene
    origen en lo que devolvieron las herramientas)."""
    if len(graficos) >= MAX_GRAFICOS:
        return {"error": f"Ya hay {MAX_GRAFICOS} gráficos en esta respuesta; no hacen falta más."}
    grafico, motivo = _validar_grafico(args, numeros)
    if motivo:
        return {"error": motivo}
    grafico["id"] = f"g{len(graficos) + 1}"
    graficos.append(grafico)
    res = {"ok": True, "id": grafico["id"], "marcador": f"[[{grafico['id']}]]"}
    if grafico["valores_sin_origen"]:
        res["aviso"] = ("Estos valores NO aparecen en lo que devolvieron las herramientas: "
                        f"{grafico['valores_sin_origen']}. Si son cálculos tuyos, decilo en el texto; si no, "
                        "corregí el gráfico con los números reales.")
    return res
