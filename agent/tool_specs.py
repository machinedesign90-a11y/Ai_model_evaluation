"""Bedrock Converse tool specs for the agent: flat, hand-written JSON schemas.

Deliberately not generated from the pydantic models: those produce `$ref`/`$defs`
and a recursive JsonValue type, which Bedrock may reject. Each schema here is
plain, with no references or recursion (arrays of inline objects are fine);
tests/test_tool_specs.py keeps it that way and checks each schema against the
catalog's own input validation. The real Bedrock smoke test (build step 6)
confirms Bedrock accepts them.
"""

from __future__ import annotations

from typing import Any

from catalog import tool_docs
from catalog.schemas import MAX_COMPARE_CAPABILITIES, MAX_REQUIREMENTS, MAX_SEARCH_LIMIT

SCALAR = ["string", "number", "boolean"]


def _entry_schema(what: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": f"The {what} in the user's words, e.g. 'Hindi voiceover', 'closed source', "
                "'longer than 10 seconds'. May be left out when the value alone says it ('Hindi', '4K').",
            },
            "value": {"type": SCALAR, "description": "The wanted value in the user's words, e.g. 'Hindi', '4K', 15."},
            "operator": {
                "type": "string",
                "enum": ["contains", "equals", "at_least", "at_most"],
                "description": "Optional; a sensible default is used.",
            },
            "importance": {"type": "string", "enum": ["must", "nice"], "description": "must (default) or nice."},
        },
        "required": [],
    }


FIND_VERIFIED_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string", "description": "The user's question, in their own words."}},
    "required": ["question"],
}

RUN_VERIFIED_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Verified query id from find_verified_query."},
        "slots": {
            "type": "object",
            "description": 'Slot name to value, in the user\'s words, e.g. {"language": "Hindi"}.',
            "additionalProperties": {"type": SCALAR},
        },
    },
    "required": ["id"],
}

SEARCH_MODELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "modality": {"type": "string", "description": "Output modality such as 'video', 'text', 'image'."},
        "filters": {
            "type": "array",
            "maxItems": MAX_REQUIREMENTS,
            "description": "Attributes to include or exclude.",
            "items": _entry_schema("filter"),
        },
        "measures": {
            "type": "array",
            "maxItems": MAX_REQUIREMENTS,
            "description": "Quantities to compare or threshold.",
            "items": _entry_schema("measure"),
        },
        "sort": {
            "type": "object",
            "description": "Order by a measure.",
            "properties": {
                "capability": {"type": "string", "description": "The measure in the user's words, e.g. 'cheapest'."},
                "direction": {"type": "string", "enum": ["best", "worst", "asc", "desc"]},
            },
            "required": ["capability"],
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_LIMIT,
            "description": f"Max models per result group (1 to {MAX_SEARCH_LIMIT}). Default 10.",
        },
    },
    "required": [],
}

LIST_CAPABILITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"modality": {"type": "string", "description": "Only capabilities for this modality. Omit for all."}},
    "required": [],
}

GET_MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slug": {"type": "string", "description": "Model slug or name."}},
    "required": ["slug"],
}

COMPARE_MODELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slugs": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 3,
            "description": "2 or 3 distinct model slugs or names.",
        },
        "capability_names": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_COMPARE_CAPABILITIES,
            "description": "Capabilities to compare, in any wording. Omit to compare everything recorded.",
        },
    },
    "required": ["slugs"],
}

SAVE_CONTEXT_DESCRIPTION = """\
Record what you have learned about the user's need, so it is kept with the conversation. Call it whenever \
you learn the use case or constraints, or settle on a shortlist. Each call replaces the stored values for \
the fields you pass."""

SAVE_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "use_case": {"type": "string", "description": "One or two sentences: what the user wants to do."},
        "constraints": {
            "type": "object",
            "description": (
                "The user's requirements, keyed by canonical capability names where one fits "
                "(e.g. speech_languages, max_resolution, price_usd_per_second), plus modality."
            ),
        },
        "shortlist": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "Slugs of the models you are recommending.",
        },
    },
    "required": [],
}

OFFER_FOLLOWUPS_DESCRIPTION = """\
End every answer with this: up to two short follow-up questions the user is likely to want next (for example \
"Which of these is cheapest?"). They are shown as tappable suggestions; each one costs the user a free message, \
so make them worth it."""

OFFER_FOLLOWUPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "maxItems": 2,
            "description": "Up to two follow-up questions, in the user's voice.",
        }
    },
    "required": ["suggestions"],
}

CATALOG_TOOLS = {
    "find_verified_query": (tool_docs.FIND_VERIFIED_QUERY, FIND_VERIFIED_QUERY_SCHEMA),
    "run_verified_query": (tool_docs.RUN_VERIFIED_QUERY, RUN_VERIFIED_QUERY_SCHEMA),
    "search_models": (tool_docs.SEARCH_MODELS, SEARCH_MODELS_SCHEMA),
    "list_capabilities": (tool_docs.LIST_CAPABILITIES, LIST_CAPABILITIES_SCHEMA),
    "get_model": (tool_docs.GET_MODEL, GET_MODEL_SCHEMA),
    "compare_models": (tool_docs.COMPARE_MODELS, COMPARE_MODELS_SCHEMA),
}
INTERNAL_TOOLS = {
    "save_context": (SAVE_CONTEXT_DESCRIPTION, SAVE_CONTEXT_SCHEMA),
    "offer_followups": (OFFER_FOLLOWUPS_DESCRIPTION, OFFER_FOLLOWUPS_SCHEMA),
}


def tool_config() -> dict[str, Any]:
    """The `toolConfig` argument for Bedrock Converse / ConverseStream."""
    tools = {**CATALOG_TOOLS, **INTERNAL_TOOLS}
    return {
        "tools": [
            {"toolSpec": {"name": name, "description": desc, "inputSchema": {"json": schema}}}
            for name, (desc, schema) in tools.items()
        ]
    }
