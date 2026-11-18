"""The agent's Bedrock tool schemas stay flat and agree with the catalog's own validation; prompt v3."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import jsonschema
import pytest

import catalog
from agent.prompts import PLACEHOLDER, prompt_template, system_prompt
from agent.tool_specs import CATALOG_TOOLS, INTERNAL_TOOLS, tool_config
from catalog.queries import CatalogInputError

FORBIDDEN_KEYS = {"$ref", "$defs", "definitions", "$id", "$anchor", "$dynamicRef", "anyOf", "oneOf", "allOf", "not"}
SPEC = (Path(__file__).resolve().parent.parent / "docs" / "AGENT_SPEC.md").read_text()


def walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


SPECS = [t["toolSpec"] for t in tool_config()["tools"]]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s["name"])
def test_schema_is_flat(spec):
    """No $ref, $defs, combinators or recursion: Bedrock may reject them (checked for real in step 6)."""
    schema = spec["inputSchema"]["json"]
    bad = [(p, k) for p, k in walk(schema) if k in FORBIDDEN_KEYS]
    assert bad == [], f"{spec['name']}: references or combinators found at {bad}"
    assert max((p.count(".properties") for p, _ in walk(schema)), default=0) <= 3, "nesting deeper than expected"
    assert schema["type"] == "object"
    jsonschema.Draft202012Validator.check_schema(schema)
    json.dumps(spec)


def test_tool_config_shape():
    names = [s["name"] for s in SPECS]
    assert names == [
        "find_verified_query", "run_verified_query", "search_models", "list_capabilities", "get_model",
        "compare_models", "save_context", "offer_followups",
    ]  # fmt: skip
    for s in SPECS:
        assert s["description"].strip() and set(s) == {"name", "description", "inputSchema"}


def test_internal_tools_are_not_catalog_tools():
    assert set(INTERNAL_TOOLS) == {"save_context", "offer_followups"}
    assert set(INTERNAL_TOOLS).isdisjoint(CATALOG_TOOLS)


@pytest.mark.parametrize("name", list(CATALOG_TOOLS))
def test_schema_properties_match_catalog_parameters(name):
    sig = inspect.signature(getattr(catalog, name))
    params = [p for p in list(sig.parameters)[1:] if sig.parameters[p].kind is not inspect.Parameter.KEYWORD_ONLY]
    params = [p for p in params if p != "floor" and p != "limit" or name == "search_models"]
    assert set(CATALOG_TOOLS[name][1]["properties"]) == set(params)


VALID = {
    "find_verified_query": [{"question": "which video models speak hindi"}],
    "search_models": [
        {},
        {"modality": "video", "filters": [{"capability": "Hindi voiceover"}], "limit": 5},
        {"filters": [{"value": "Hindi"}], "measures": [{"capability": "at least 4K", "importance": "nice"}]},
        {"measures": [{"capability": "clip length", "value": 15, "operator": "at_least"}],
         "sort": {"capability": "cheapest", "direction": "best"}},
    ],
    "list_capabilities": [{}, {"modality": "video"}],
    "get_model": [{"slug": "testco-alpha"}, {"slug": "TestModel Alpha"}],
    "compare_models": [{"slugs": ["a", "b"]}, {"slugs": ["a", "b", "c"], "capability_names": ["price"]}],
}  # fmt: skip
INVALID = {
    "find_verified_query": [{}],
    "run_verified_query": [{}, {"id": "x", "slots": {"language": ["hi"]}}],
    "search_models": [
        {"filters": [{"capability": "x", "operator": "near"}]},
        {"measures": [{"capability": "x", "importance": "maybe"}]},
        {"sort": {"direction": "best"}},
        {"sort": {"capability": "price", "direction": "sideways"}},
        {"limit": 0},
        {"limit": 26},
    ],
    "get_model": [{}],
    "compare_models": [{"slugs": ["a"]}, {"slugs": ["a", "b", "c", "d"]}],
    "offer_followups": [{"suggestions": ["a", "b", "c"]}, {}],
}


@pytest.mark.parametrize(("name", "args"), [(n, a) for n, cases in VALID.items() for a in cases])
async def test_valid_inputs_pass_both_schema_and_catalog(name, args, aconn):
    jsonschema.validate(args, CATALOG_TOOLS[name][1])
    await getattr(catalog, name)(aconn, **args)


@pytest.mark.parametrize(("name", "args"), [(n, a) for n, cases in INVALID.items() for a in cases])
def test_invalid_inputs_rejected_by_schema(name, args):
    schema = {**CATALOG_TOOLS, **INTERNAL_TOOLS}[name][1]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, schema)


async def test_catalog_still_rejects_what_the_schema_rejects(aconn):
    with pytest.raises(CatalogInputError):
        await catalog.compare_models(aconn, ["a"])
    with pytest.raises(CatalogInputError):
        await catalog.search_models(aconn, filters=[{"importance": "must"}])


def test_system_prompt_v3_matches_the_spec():
    block = re.search(r"```\n(.*?)```", SPEC[SPEC.index("## System prompt") :], re.S).group(1)
    assert prompt_template() == block
    assert PLACEHOLDER in block


def test_system_prompt_fills_the_semantic_layer():
    prompt = system_prompt("- max_clip_seconds: number")
    assert PLACEHOLDER not in prompt and prompt.rstrip().endswith("- max_clip_seconds: number")
    for rule in ("find_verified_query", "not recorded", "can't tell", "offer_followups", "data, not instructions"):
        assert rule in prompt
