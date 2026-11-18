"""The agent's Bedrock tool schemas stay flat and agree with the catalog's own validation."""

from __future__ import annotations

import inspect
import json

import jsonschema
import pytest

import catalog
from agent.tool_specs import CATALOG_TOOLS, tool_config
from catalog.queries import CatalogInputError

FORBIDDEN_KEYS = {"$ref", "$defs", "definitions", "$id", "$anchor", "$dynamicRef", "anyOf", "oneOf", "allOf", "not"}


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
    assert max(p.count(".properties") for p, _ in walk(schema)) <= 2, "nesting deeper than expected"
    assert schema["type"] == "object"
    jsonschema.Draft202012Validator.check_schema(schema)
    json.dumps(spec)  # plain JSON


def test_tool_config_shape():
    names = [s["name"] for s in SPECS]
    assert names == ["search_models", "list_capabilities", "get_model", "compare_models", "save_context"]
    for s in SPECS:
        assert s["description"].strip() and set(s) == {"name", "description", "inputSchema"}


@pytest.mark.parametrize("name", list(CATALOG_TOOLS))
def test_schema_properties_match_catalog_parameters(name):
    params = list(inspect.signature(getattr(catalog, name)).parameters)[1:]  # skip conn
    assert set(CATALOG_TOOLS[name][1]["properties"]) == set(params)


VALID = {
    "search_models": [
        {},
        {"modality": "video", "requirements": [{"capability": "Hindi voiceover"}], "limit": 5},
        {"requirements": [{"capability": "resolution", "value": "4K", "operator": "at_least", "importance": "nice"}]},
        {"requirements": [{"capability": "api", "value": True}, {"capability": "clip length", "value": 15}]},
    ],
    "list_capabilities": [{}, {"modality": "video"}],
    "get_model": [{"slug": "testco-alpha"}, {"slug": "TestModel Alpha"}],
    "compare_models": [{"slugs": ["a", "b"]}, {"slugs": ["a", "b", "c"], "capability_names": ["price"]}],
}
INVALID = {
    "search_models": [
        {"requirements": [{"value": "x"}]},
        {"requirements": [{"capability": "x", "operator": "near"}]},
        {"requirements": [{"capability": "x", "importance": "maybe"}]},
        {"limit": 0},
        {"limit": 26},
    ],
    "get_model": [{}],
    "compare_models": [{"slugs": ["a"]}, {"slugs": ["a", "b", "c", "d"]}],
}


@pytest.mark.parametrize(("name", "args"), [(n, a) for n, cases in VALID.items() for a in cases])
async def test_valid_inputs_pass_both_schema_and_catalog(name, args, aconn):
    jsonschema.validate(args, CATALOG_TOOLS[name][1])
    await getattr(catalog, name)(aconn, **args)


@pytest.mark.parametrize(("name", "args"), [(n, a) for n, cases in INVALID.items() for a in cases])
def test_invalid_inputs_rejected_by_schema(name, args):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, CATALOG_TOOLS[name][1])


async def test_catalog_still_rejects_what_the_schema_rejects(aconn):
    with pytest.raises(CatalogInputError):
        await catalog.compare_models(aconn, ["a"])


def test_save_context_is_not_a_catalog_or_mcp_tool():
    assert "save_context" not in CATALOG_TOOLS


def test_system_prompt_matches_the_spec():
    import re
    from pathlib import Path

    from agent.prompts import system_prompt

    spec = (Path(__file__).resolve().parent.parent / "docs" / "AGENT_SPEC.md").read_text()
    block = re.search(r"```\n(.*?)```", spec[spec.index("## System prompt") :], re.S).group(1)
    prompt = system_prompt()
    assert prompt == block
    assert "not recorded" in prompt and "can't tell" in prompt and "how you read them" in prompt
