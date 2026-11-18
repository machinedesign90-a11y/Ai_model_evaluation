"""Tier 1: verified queries. Retrieval quality, slot filling, drafts never used, and the CI spec check."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from catalog import CatalogInputError, find_verified_query, run_verified_query, validate_verified_spec
from catalog.vocabulary import load_vocabulary
from tests.tier1_paraphrases import PARAPHRASES


async def _id(conn, title):
    return str((await (await conn.execute("select id from verified_queries where title = %s", (title,))).fetchone())[0])


# --- retrieval ----------------------------------------------------------------------------------


async def test_drafts_are_never_returned(aconn):
    """The seed is all drafts: tier 1 must find nothing until a person verifies an entry."""
    r = await find_verified_query(aconn, "which video models can speak hindi")
    assert r.candidates == []


async def test_draft_cannot_be_run(aconn):
    with pytest.raises(CatalogInputError, match="no verified query"):
        await run_verified_query(aconn, await _id(aconn, "Models that speak a language"), {"language": "Hindi"})


async def test_retired_entries_are_not_returned(aconn_tier1):
    await aconn_tier1.execute("update verified_queries set status = 'retired' where title = 'Cheapest video models'")
    r = await find_verified_query(aconn_tier1, "cheapest video generation model")
    assert "Cheapest video models" not in [c.title for c in r.candidates]


async def test_find_returns_up_to_three_candidates_with_slots(aconn_tier1):
    r = await find_verified_query(aconn_tier1, "hindi voiceover with background music")
    assert 1 <= len(r.candidates) <= 3
    top = r.candidates[0]
    assert top.title == "Voiceover in a language with background audio"
    assert [(s.name, s.required, s.capability) for s in top.slots] == [("language", True, "speech_languages")]
    assert r.candidates == sorted(r.candidates, key=lambda c: -c.score)


async def test_floor_limits_candidates(aconn_tier1):
    assert (await find_verified_query(aconn_tier1, "what is the weather today", floor=0.9)).candidates == []


async def test_paraphrase_hit_rates(aconn_tier1):
    """Report and pin top-1 / top-3 hit rates on tests/tier1_paraphrases.py."""
    top1 = top3 = 0
    positives = [(q, want) for q, want in PARAPHRASES if want is not None]
    misses = []
    for question, want in positives:
        titles = [c.title for c in (await find_verified_query(aconn_tier1, question)).candidates]
        top1 += bool(titles) and titles[0] == want
        top3 += want in titles
        if want not in titles:
            misses.append(question)
    n = len(positives)
    print(f"\ntier 1 paraphrase hit rates: top-1 {top1}/{n} ({top1 / n:.0%}), top-3 {top3}/{n} ({top3 / n:.0%})")
    assert top3 == n, f"missed from top 3: {misses}"
    assert top1 / n >= 0.8


async def test_non_matching_questions_score_low(aconn_tier1):
    for question, want in PARAPHRASES:
        if want is None:
            r = await find_verified_query(aconn_tier1, question)
            assert all(c.score < 0.5 for c in r.candidates), (question, r.candidates)


# --- running ------------------------------------------------------------------------------------


async def test_run_fills_slots_from_the_users_words(aconn_tier1):
    qid = await _id(aconn_tier1, "Voiceover in a language with background audio")
    r = await run_verified_query(aconn_tier1, qid, {"language": "Hindi"})
    assert r.verified_query.title == "Voiceover in a language with background audio"
    assert [m.slug for m in r.full_matches] == ["testco-alpha"]
    assert r.interpretation[0].value == "hi" and r.interpretation[0].confidence == "alias"
    assert [(s.name, s.value, s.source) for s in r.slots_used] == [("language", "Hindi", "user")]
    facts = r.full_matches[0].results
    assert all(x.fact.source_url for x in facts)


async def test_run_uses_slot_defaults(aconn_tier1):
    qid = await _id(aconn_tier1, "Minimum video resolution")
    r = await run_verified_query(aconn_tier1, qid, {})
    assert [(s.name, s.value, s.source) for s in r.slots_used] == [("resolution", "1080p", "default")]
    assert sorted(m.slug for m in r.full_matches) == ["testco-alpha", "testco-beta"]
    r = await run_verified_query(aconn_tier1, qid, {"resolution": "4K"})
    assert [m.slug for m in r.full_matches] == ["testco-beta"]


async def test_run_with_modality_slot(aconn_tier1):
    qid = await _id(aconn_tier1, "Models with an API")
    r = await run_verified_query(aconn_tier1, qid, {"modality": "videos"})
    assert r.searched_modality == "video" and [m.slug for m in r.full_matches] == ["testco-alpha"]


async def test_missing_required_slot_is_reported_not_a_dead_end(aconn_tier1):
    qid = await _id(aconn_tier1, "Voiceover in a language with background audio")
    r = await run_verified_query(aconn_tier1, qid, {"colour": "blue"})
    parts = [(u.part, u.term) for u in r.unresolved]
    assert ("slot", "language") in parts and ("slot", "colour") in parts
    assert [m.slug for m in r.full_matches] == ["testco-alpha"]  # background audio still applied


async def test_run_records_usage_only_when_asked(aconn_tier1):
    qid = await _id(aconn_tier1, "Cheapest video models")
    await run_verified_query(aconn_tier1, qid, record_match=False)
    await run_verified_query(aconn_tier1, qid)
    row = await (await aconn_tier1.execute(
        "select times_matched, last_matched_at from verified_queries where id = %s", (qid,))).fetchone()  # fmt: skip
    assert row[0] == 1 and row[1] is not None


async def test_cheapest_verified_query_sorts_with_unrecorded_last(aconn_tier1):
    r = await run_verified_query(aconn_tier1, await _id(aconn_tier1, "Cheapest video models"))
    assert [m.slug for m in r.full_matches][:2] == ["testco-beta", "testco-alpha"]
    assert all(m.sort_fact is None for m in r.full_matches[2:])


@pytest.mark.parametrize("bad", ["not-a-uuid", str(uuid.uuid4())])
async def test_bad_ids_are_rejected(aconn_tier1, bad):
    with pytest.raises(CatalogInputError):
        await run_verified_query(aconn_tier1, bad)


async def test_run_refuses_a_verified_query_that_no_longer_validates(aconn_tier1):
    qid = await _id(aconn_tier1, "Longest video clips")
    await aconn_tier1.execute(
        "update verified_queries set query_spec = %s where id = %s",
        (Jsonb({"modality": "video", "sort": {"capability": "clip_seconds_renamed", "direction": "best"}}), qid),
    )
    with pytest.raises(CatalogInputError, match="no longer fits"):
        await run_verified_query(aconn_tier1, qid)


# --- CI check: every verified query's spec fits the current vocabulary --------------------------


async def test_every_seeded_query_spec_validates(aconn):
    """Fails when a capability a verified query uses is renamed, removed or changes kind."""
    vocab = await load_vocabulary(aconn)
    rows = await (await aconn.execute("select title, query_spec, slots from verified_queries")).fetchall()
    assert rows
    problems = {title: validate_verified_spec(vocab, spec, slots) for title, spec, slots in rows}
    assert {t: p for t, p in problems.items() if p} == {}


async def test_validator_catches_a_renamed_capability(catalog_dsn):
    with psycopg.connect(catalog_dsn, autocommit=True) as conn:
        conn.execute("update capability_definitions set name = 'clip_seconds_max' where name = 'max_clip_seconds'")
    async with await psycopg.AsyncConnection.connect(catalog_dsn) as conn:
        vocab = await load_vocabulary(conn)
        rows = await (await conn.execute("select title, query_spec, slots from verified_queries")).fetchall()
    broken = [t for t, spec, slots in rows if validate_verified_spec(vocab, spec, slots)]
    assert broken == ["Longest video clips"]


@pytest.mark.parametrize(
    ("spec", "slots", "fragment"),
    [
        ({"filters": [{"capability": "speech_languages", "operator": "contains", "value": "Hindi"}]}, [],
         "not a valid speech_languages value"),
        ({"filters": [{"capability": "max_clip_seconds", "operator": "at_least", "value": 5}]}, [], "is a measure"),
        ({"measures": [{"capability": "max_clip_seconds", "operator": "contains", "value": 5}]}, [], "operator"),
        ({"measures": [{"capability": "voiceover", "operator": "equals", "value": 5}]}, [], "unknown capability"),
        ({"sort": {"capability": "open_weights"}}, [], "must name a measure"),
        ({"filters": [{"capability": "speech_languages", "operator": "contains", "value": "{{lang}}"}]}, [],
         "used but not declared"),
        ({}, [{"name": "lang", "capability": "speech_languages", "required": True}], "declared but not used"),
        ({"modality": "hologram"}, [], "unknown modality"),
        ({"limit": 99}, [], "limit"),
        ({"filters": [], "extra": 1}, [], "unknown keys"),
    ],
)  # fmt: skip
async def test_validator_rejects(aconn, spec, slots, fragment):
    problems = validate_verified_spec(await load_vocabulary(aconn), spec, slots)
    assert any(fragment in p for p in problems), problems
