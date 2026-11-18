"""get_model, compare_models, list_capabilities, and untrusted/injection-style input, on fictional data."""

from __future__ import annotations

import psycopg
import pytest

from catalog import CatalogInputError, compare_models, get_model, list_capabilities, search_models
from tests.fixtures import INJECTION_TEXT

# --- get_model ---------------------------------------------------------------------------------


async def test_get_model_returns_all_facts_with_provenance_including_price_and_api(aconn):
    r = await get_model(aconn, "testco-alpha")
    assert (r.status, r.matched_by) == ("found", "slug")
    facts = {f.name: f for f in r.model.facts}
    assert list(facts) == sorted(facts)
    assert facts["pricing"].value == {"unit": "per_second", "amount": 0.5, "currency": "USD"}
    assert facts["pricing"].verification_status == "verified" and facts["pricing"].source_url
    assert facts["api_available"].value is True and facts["api_available"].source_type == "official_docs"
    assert (
        facts["max_resolution"].verification_status == "unverified" and facts["max_resolution"].last_verified_at is None
    )


@pytest.mark.parametrize(
    ("term", "matched_by"),
    [("TestModel Alpha", "name"), ("testmodel alpha", "name"), ("TESTCO alpha", "name"),
     ("TestModel Alfa", "approximate"), (" testco-alpha ", "slug")],
)  # fmt: skip
async def test_get_model_by_name_and_misspelling(aconn, term, matched_by):
    r = await get_model(aconn, term)
    assert r.model.slug == "testco-alpha" and r.matched_by == matched_by


async def test_get_model_not_found_suggests(aconn):
    r = await get_model(aconn, "zzzz qqqq")
    assert r.status == "not_found" and r.model is None
    r = await get_model(aconn, "testmodel")  # several equally close: not guessed
    assert r.status == "not_found" and len(r.suggestions) >= 2


async def test_unrecorded_price_and_api_are_simply_absent(aconn):
    m = (await get_model(aconn, "testco-gamma")).model
    assert {f.name for f in m.facts} == {"audio_generation", "max_resolution"}
    assert m.display_description is None


# --- compare_models ----------------------------------------------------------------------------


async def test_compare_defaults_to_union_and_reports_not_recorded(aconn):
    r = await compare_models(aconn, ["testco-alpha", "testco-gamma"])
    assert "pricing" in r.capability_names and "api_available" in r.capability_names
    alpha, gamma = r.models
    assert alpha.not_recorded == []
    assert "speech_languages" in gamma.not_recorded and "pricing" in gamma.not_recorded


async def test_compare_accepts_names_and_everyday_capability_words(aconn):
    r = await compare_models(
        aconn, ["TestModel Alpha", "testco-beta", "TestModel Gama"], ["price", "resolution", "dubbing", "lip sync"]
    )
    assert [(m.slug, m.matched_by) for m in r.models] == [
        ("testco-alpha", "name"), ("testco-beta", "slug"), ("testco-gamma", "approximate"),
    ]  # fmt: skip
    assert r.capability_names == ["pricing", "max_resolution", "speech_languages"]
    assert [(c.input, c.capability, c.confidence) for c in r.capability_interpretation] == [
        ("price", "pricing", "alias"),
        ("resolution", "max_resolution", "alias"),
        ("dubbing", "speech_languages", "alias"),
    ]
    assert [(u.part, u.term) for u in r.unresolved] == [("capability", "lip sync")]
    beta = r.models[1]
    assert beta.not_recorded == ["pricing"]


async def test_compare_reports_unknown_and_duplicate_models(aconn):
    r = await compare_models(aconn, ["testco-alpha", "zzzz qqqq", "TestModel Alpha"])
    assert [m.slug for m in r.models] == ["testco-alpha"]
    assert [u.term for u in r.unresolved] == ["zzzz qqqq", "TestModel Alpha"]


@pytest.mark.parametrize("bad", [["testco-alpha"], ["a", "b", "c", "d"], ["testco-alpha", "TESTCO-ALPHA"]])
async def test_compare_needs_two_or_three_distinct_entries(aconn, bad):
    with pytest.raises(CatalogInputError):
        await compare_models(aconn, bad)


# --- list_capabilities -------------------------------------------------------------------------


async def test_list_capabilities_filters_by_modality(aconn):
    video = await list_capabilities(aconn, "Videos")
    names = {c.name for c in video.capabilities}
    assert video.modality == "video"
    assert {"speech_languages", "max_resolution", "pricing", "api_available"} <= names
    assert "context_window_tokens" not in names
    speech = next(c for c in video.capabilities if c.name == "speech_languages")
    assert speech.value_type == "enum_list" and "hi" in speech.allowed_values and speech.example_aliases
    assert len((await list_capabilities(aconn)).capabilities) == 12


async def test_list_capabilities_for_a_modality_with_no_models_yet(aconn):
    r = await list_capabilities(aconn, "speech")
    assert r.modality == "speech" and "speech_languages" in {c.name for c in r.capabilities}


# --- untrusted text and injection-style inputs -------------------------------------------------


async def test_catalog_text_is_returned_verbatim_as_data(aconn):
    m = (await get_model(aconn, "testco-delta")).model
    assert m.display_description == INJECTION_TEXT


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
    r = await get_model(aconn, evil)
    assert r.status == "not_found" or r.model.slug == "testco-alpha"  # the union string contains the slug
    r = await search_models(aconn, evil, [{"capability": evil, "value": evil}])
    assert r.searched_modality is None
    await compare_models(aconn, ["testco-alpha", evil], [evil])
    await list_capabilities(aconn, evil[:50])
    with psycopg.connect(catalog_dsn) as conn:
        assert conn.execute("select count(*) from models").fetchone()[0] == 5


async def test_catalog_works_on_a_read_only_connection(catalog_dsn):
    """The MCP server uses read-only transactions; every tool must work under them."""
    async with await psycopg.AsyncConnection.connect(
        catalog_dsn, autocommit=True, options="-c default_transaction_read_only=on"
    ) as conn:
        await search_models(conn, "video", [{"capability": "Hindi voiceover"}])
        await get_model(conn, "TestModel Alfa")
        await compare_models(conn, ["testco-alpha", "testco-beta"], ["price"])
        await list_capabilities(conn, "video")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await conn.execute("delete from models")
