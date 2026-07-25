# -*- coding: utf-8 -*-
"""Traducción de preguntas en lenguaje natural a SQL y análisis de resultados
usando la API de Claude (modelo claude-opus-4-8)."""
import json
import os

import anthropic

from schema_context import SCHEMA_DOC

MODEL = "claude-haiku-4-5"

_SYSTEM_SQL = (
    "Eres un analista experto en T-SQL para la base de datos DelPro (DDM) de un "
    "tambo lechero. Tu tarea: convertir la pregunta del usuario en UNA consulta "
    "SELECT de solo lectura y proponer la mejor visualización.\n\n"
    "Reglas estrictas:\n"
    "- Solo SELECT (o WITH ... SELECT). Nunca modifiques datos.\n"
    "- Sintaxis T-SQL de SQL Server (TOP, DATEADD, FORMAT, DATEFROMPARTS).\n"
    "- Filtra GCRecord IS NULL salvo que se pida lo contrario.\n"
    "- Máximo ~500 filas de resultado (TOP 500 cuando aplique).\n"
    "- Alias de columnas en español, en minúsculas y con guion_bajo.\n"
    "- Si la pregunta es ambigua, elige la interpretación más útil para gestión "
    "del tambo y explícala en 'supuestos'.\n\n"
    "Elección de gráfica: 'line' para series temporales, 'bar' para comparación "
    "entre categorías, 'pie' solo para composición con <=6 categorías, 'stat' "
    "para un único valor, 'table' cuando no tiene sentido graficar.\n\n"
    "ESQUEMA DE LA BASE:\n" + SCHEMA_DOC
)

_CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "Consulta T-SQL SELECT"},
        "titulo": {"type": "string"},
        "supuestos": {"type": "string", "description": "Supuestos asumidos, o cadena vacía"},
        "grafica": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["line", "bar", "pie", "stat", "table"]},
                "eje_x": {"type": "string", "description": "Nombre de la columna para el eje X (o vacío)"},
                "series": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columnas numéricas a graficar como series",
                },
            },
            "required": ["tipo", "eje_x", "series"],
            "additionalProperties": False,
        },
    },
    "required": ["sql", "titulo", "supuestos", "grafica"],
    "additionalProperties": False,
}


def api_disponible() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def pregunta_a_sql(pregunta: str) -> dict:
    """Devuelve {sql, titulo, supuestos, grafica:{tipo, eje_x, series}}."""
    response = _client().messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[{
            "type": "text",
            "text": _SYSTEM_SQL,
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={"format": {"type": "json_schema", "schema": _CHART_SCHEMA}},
        messages=[{"role": "user", "content": pregunta}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def analizar_resultados(pregunta: str, columns: list, rows: list) -> str:
    """Genera un análisis breve en español orientado a la toma de decisiones."""
    muestra = {"columnas": columns, "filas": rows[:60], "total_filas": len(rows)}
    response = _client().messages.create(
        model=MODEL,
        max_tokens=2000,
        system=(
            "Eres asesor de gestión de un tambo lechero. Recibes una pregunta y los "
            "datos que respondieron esa pregunta (obtenidos de DelPro). Escribe un "
            "análisis breve en español (3 a 6 oraciones): qué muestran los datos, "
            "qué destaca y qué decisión o acción concreta sugerirías. Sin listas ni "
            "encabezados, solo prosa clara. Si los datos son insuficientes, dilo."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Pregunta: {pregunta}\n\nDatos (JSON):\n"
                + json.dumps(muestra, ensure_ascii=False, default=str)
            ),
        }],
    )
    return next((b.text for b in response.content if b.type == "text"), "")
