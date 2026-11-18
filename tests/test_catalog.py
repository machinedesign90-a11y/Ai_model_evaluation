"""Step 2: catalog tools against the fictional fixture catalog."""

from __future__ import annotations

import psycopg
import pytest

from catalog import CatalogInputError, compare_models, get_model, search_models
from tests.fixtures import INJECTION_TEXT


def slugs(items):
    return [i.slug for i in items]


# --- search_models -----------------------------------------------------------------------------


async def test_search_match_by_array_containment(aconn):
    r = await search_models(aconn, "video", [{"name": "output_languages", "value": "hi"}])
    assert slugs(r.matches) == ["testco-alpha"]
    fact = r.matches[0].facts[0]
    assert fact.name == "output_languages" and fact.value == ["hi", "en"]
    assert fact.source_type == "official_docs" and fact.source_url.startswith("https://docs.testco.example")
    assert fact.verification_status == "verified" and fact.last_verified_at is not None
    assert r.searched_modality == "video" and r.total_models_in_modality == 3


async def test_search_reports_unrecorded_capability_as_unconfirmed(aconn):
    r = await search_models(aconn, "video", [{"name": "output_languages", "value": "hi"}])
    # Beta records ["en"]: contradicts, so dropped. Gamma has no row: unconfirmed, not silently dropped.
    assert slugs(r.unconfirmed) == ["testco-gamma"]
    assert r.unconfirmed[0].missing_capabilities == ["output_languages"]
    assert "testco-beta" not in slugs(r.matches) + slugs(r.unconfirmed)


async def test_search_multiple_requirements_and_unverified_facts(aconn):
    r = await search_models(
        aconn,
        "video",
        [{"name": "output_languages", "value": "en"}, {"name": "audio_generation", "value": True}],
    )
    assert slugs(r.matches) == ["testco-alpha", "testco-beta"]  # more verified facts first
    beta = r.matches[1]
    statuses = {f.name: f.verification_status for f in beta.facts}
    assert statuses == {"output_languages": "verified", "audio_generation": "unverified"}
    assert slugs(r.unconfirmed) == ["testco-gamma"]
    assert [f.name for f in r.unconfirmed[0].facts] == ["audio_generation"]


async def test_search_no_match(aconn):
    r = await search_models(aconn, "video", [{"name": "output_languages", "value": "fr"}])
    assert r.matches == [] and r.total_matches == 0
    assert slugs(r.unconfirmed) == ["testco-gamma"]


async def test_search_unknown_modality_lists_available(aconn):
    r = await search_models(aconn, "Videos", [])
    assert r.total_models_in_modality == 0 and r.matches == []
    assert r.available_modalities == ["text", "video"]


async def test_search_modality_is_case_insensitive_and_optional(aconn):
    assert (await search_models(aconn, " VIDEO ")).total_models_in_modality == 3
    r = await search_models(aconn, None, [{"name": "context_window", "value": 100000}])
    assert r.searched_modality is None and r.total_models_in_modality == 4
    assert slugs(r.matches) == ["testco-delta"]


async def test_search_flags_capability_names_nobody_has(aconn):
    r = await search_models(aconn, "video", [{"name": "voiceover_languages", "value": "hi"}])
    assert r.unknown_capability_names == ["voiceover_languages"]
    assert "output_languages" in r.known_capability_names
    known = await search_models(aconn, "video", [{"name": "output_languages", "value": "hi"}])
    assert known.unknown_capability_names == [] and known.known_capability_names == []


async def test_search_limit_is_clamped_and_totals_reported(aconn):
    r = await search_models(aconn, "video", [], limit=1)
    assert len(r.matches) == 1 and r.total_matches == 3
    assert len((await search_models(aconn, "video", [], limit=500)).matches) == 3


async def test_search_rejects_bad_input(aconn):
    with pytest.raises(CatalogInputError):
        await search_models(aconn, "video", [{"name": "", "value": 1}])
    with pytest.raises(CatalogInputError):
        await search_models(aconn, "video", [{"name": "a", "value": 1}] * 11)


# --- get_model ---------------------------------------------------------------------------------


async def test_get_model_returns_all_facts_with_provenance(aconn):
    r = await get_model(aconn, "TestCo-Alpha ")
    assert r.status == "found"
    m = r.model
    assert (m.name, m.provider, m.modality) == ("TestModel Alpha", "TestCo", "video")
    assert [f.name for f in m.facts] == [
        "api_available", "audio_generation", "max_resolution", "output_languages", "pricing",
    ]  # fmt: skip
    facts = {f.name: f for f in m.facts}
    assert facts["pricing"].value == {"currency": "USD", "unit": "second of video", "amount": 0.5}
    assert facts["pricing"].verification_status == "verified" and facts["pricing"].source_type == "official_docs"
    assert facts["api_available"].value is True
    res = next(f for f in m.facts if f.name == "max_resolution")
    assert res.verification_status == "unverified" and res.source_type == "scraped" and res.last_verified_at is None


async def test_get_model_unrecorded_pricing_and_api_are_simply_absent(aconn):
    m = (await get_model(aconn, "testco-gamma")).model
    assert [f.name for f in m.facts] == ["audio_generation"]
    assert m.display_description is None


async def test_get_model_unknown_slug_suggests(aconn):
    r = await get_model(aconn, "testco-alfa")
    assert r.status == "not_found" and r.model is None
    assert slugs(r.suggestions)[0] == "testco-alpha"
    assert (await get_model(aconn, "zzzzzzzzzz")).suggestions == []


# --- compare_models ----------------------------------------------------------------------------


async def test_compare_two_models_defaults_to_union_of_capabilities(aconn):
    r = await compare_models(aconn, ["testco-alpha", "testco-gamma"])
    assert r.capability_names == ["api_available", "audio_generation", "max_resolution", "output_languages", "pricing"]
    alpha, gamma = r.models
    assert alpha.not_recorded == []
    assert [f.name for f in gamma.facts] == ["audio_generation"]
    assert gamma.not_recorded == ["api_available", "max_resolution", "output_languages", "pricing"]


async def test_compare_three_models_with_named_capabilities_keeps_order(aconn):
    r = await compare_models(aconn, ["testco-gamma", "testco-alpha", "testco-beta"], ["output_languages", "fps"])
    assert slugs(r.models) == ["testco-gamma", "testco-alpha", "testco-beta"]
    assert r.capability_names == ["output_languages", "fps"]
    assert {m.slug: m.not_recorded for m in r.models} == {
        "testco-gamma": ["output_languages", "fps"],
        "testco-alpha": ["fps"],
        "testco-beta": ["fps"],
    }


async def test_compare_reports_invalid_slug(aconn):
    r = await compare_models(aconn, ["testco-alpha", "testco-betta"])
    assert slugs(r.models) == ["testco-alpha"]
    assert r.not_found[0].slug == "testco-betta" and slugs(r.not_found[0].suggestions)[0] == "testco-beta"


@pytest.mark.parametrize("bad", [["testco-alpha"], ["a", "b", "c", "d"], ["testco-alpha", "TESTCO-ALPHA"]])
async def test_compare_needs_two_or_three_distinct_slugs(aconn, bad):
    with pytest.raises(CatalogInputError):
        await compare_models(aconn, bad)


# --- untrusted text and injection-style inputs -------------------------------------------------


async def test_catalog_text_is_returned_verbatim_as_data(aconn):
    m = (await get_model(aconn, "testco-delta")).model
    assert m.display_description == INJECTION_TEXT
    assert next(f for f in m.facts if f.name == "notes").value == INJECTION_TEXT


INJECTIONS = [
    "x'; drop table models; --",
    "' or '1'='1",
    "testco-alpha' union select * from profiles --",
    "%",
    "_",
    "\\'; select pg_sleep(5); --",
]


@pytest.mark.parametrize("evil", INJECTIONS)
async def test_sql_injection_style_inputs_are_plain_data(aconn, catalog_dsn, evil):
    assert (await get_model(aconn, evil)).status == "not_found"
    r = await search_models(aconn, evil, [{"name": evil, "value": evil}])
    assert r.total_models_in_modality == 0
    r = await search_models(aconn, "video", [{"name": evil, "value": {"k": evil}}])
    assert r.matches == [] and r.unknown_capability_names == [evil.strip()]
    r = await compare_models(aconn, ["testco-alpha", evil], [evil])
    assert r.capability_names == [evil.strip()] and len(r.not_found) == 1
    with psycopg.connect(catalog_dsn) as conn:
        assert conn.execute("select count(*) from models").fetchone()[0] == 4


async def test_catalog_works_on_a_read_only_connection(catalog_dsn):
    """The MCP server uses read-only transactions; every tool must work under them."""
    async with await psycopg.AsyncConnection.connect(
        catalog_dsn, autocommit=True, options="-c default_transaction_read_only=on"
    ) as conn:
        await search_models(conn, "video", [{"name": "output_languages", "value": "hi"}])
        await get_model(conn, "testco-alpha")
        await compare_models(conn, ["testco-alpha", "testco-beta"])
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await conn.execute("delete from models")
