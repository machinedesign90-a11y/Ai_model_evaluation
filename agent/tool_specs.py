"""Bedrock Converse tool specs for the agent: flat, hand-written JSON schemas.

Deliberately not generated from the pydantic models: those produce `$ref`/`$defs`
and a recursive JsonValue type, which Bedrock may reject. Each schema here is
plain, with no references or recursion; tests/test_tool_specs.py keeps it that
way and checks each schema against the catalog's own input validation. The real
Bedrock smoke test (build step 6) confirms Bedrock accepts them.
"""

from __future__ import annotations

from typing import Any

from catalog import tool_docs
from catalog.schemas import MAX_COMPARE_CAPABILITIES, MAX_REQUIREMENTS, MAX_SEARCH_LIMIT

SAVE_CONTEXT_DESCRIPTION = """\
Record what you have learned about the user's need, so it is kept with the conversation. Call it whenever \
you learn the use case or constraints, or settle on a shortlist. Each call replaces the stored values for \
the fields you pass."""

SEARCH_MODELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "modality": {
            "type": "string",
            "description": "Output modality such as 'video', 'text', 'image', 'speech'. Omit for all.",
        },
        "required_capabilities": {
            "type": "array",
            "description": f"Every requirement must hold (AND). Up to {MAX_REQUIREMENTS}.",
            "maxItems": MAX_REQUIREMENTS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Capability name, e.g. 'output_languages'."},
                    "value": {
                        "type": ["string", "number", "boolean"],
                        "description": (
                            'Required value. For a list capability give one item ("hi" matches ["hi", "en"]); '
                            "for a yes/no capability give true or false."
                        ),
                    },
                },
                "required": ["name", "value"],
            },
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_LIMIT,
            "description": f"Max matches and max unconfirmed returned (1 to {MAX_SEARCH_LIMIT}). Default 10.",
        },
    },
    "required": [],
}

GET_MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slug": {"type": "string", "description": "Model slug from a previous search."}},
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
            "description": "2 or 3 distinct model slugs.",
        },
        "capability_names": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_COMPARE_CAPABILITIES,
            "description": "Capabilities to compare. Omit to compare every capability any of them has.",
        },
    },
    "required": ["slugs"],
}

SAVE_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "use_case": {"type": "string", "description": "One or two sentences: what the user wants to do."},
        "constraints": {
            "type": "object",
            "description": "The user's requirements. Prefer these keys; add others only if none fits.",
            "properties": {
                "modality": {"type": "string"},
                "languages": {"type": "array", "items": {"type": "string"}, "description": "ISO 639-1 codes."},
                "needs_audio": {"type": "boolean"},
                "max_price_per_unit": {"type": "number"},
                "api_required": {"type": "boolean"},
            },
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

CATALOG_TOOLS = {
    "search_models": (tool_docs.SEARCH_MODELS, SEARCH_MODELS_SCHEMA),
    "get_model": (tool_docs.GET_MODEL, GET_MODEL_SCHEMA),
    "compare_models": (tool_docs.COMPARE_MODELS, COMPARE_MODELS_SCHEMA),
}
INTERNAL_TOOLS = {"save_context": (SAVE_CONTEXT_DESCRIPTION, SAVE_CONTEXT_SCHEMA)}


def tool_config() -> dict[str, Any]:
    """The `toolConfig` argument for Bedrock Converse / ConverseStream."""
    tools = {**CATALOG_TOOLS, **INTERNAL_TOOLS}
    return {
        "tools": [
            {"toolSpec": {"name": name, "description": desc, "inputSchema": {"json": schema}}}
            for name, (desc, schema) in tools.items()
        ]
    }
