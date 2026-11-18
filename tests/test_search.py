"""Forgiving search (change request 001): people's words in, canonical reading reported, never empty."""

from __future__ import annotations

import pytest

from catalog import search_models
from catalog.vocabulary import CAPABILITY_THRESHOLD, VALUE_THRESHOLD, similarities

HINDI_SPEECH = "speech_languages contains hi"


def slugs(items):
    return [i.slug for i in items]


def shape(r):
    """The parts of a result that wording must not change."""
    return (
        slugs(r.full_matches),
        sorted(slugs(r.close_matches)),
        slugs(r.partial_matches),
        [(i.capability, i.operator, i.value) for i in r.interpretation],
    )


CANONICAL = [{"capability": "speech_languages", "value": "hi"}]
WORDINGS = [
    [{"capability": "Hindi voiceover"}],
    [{"capability": "Hindi voiceover", "value": "Hindi"}],
    [{"capability": "dubbing", "value": "Hindi"}],
    [{"capability": "dubbing in Hindi"}],
    [{"capability": "speech_languages hi"}],
    [{"capability": "Voice-Over", "value": "हिन्दी"}],
    [{"capability": "SPEECH LANGUAGES", "value": "HI"}],
]


@pytest.mark.parametrize("reqs", WORDINGS, ids=lambda r: str(r[0]))
async def test_informal_and_canonical_wording_give_the_same_result(aconn, reqs):
    canonical = await search_models(aconn, "video", CANONICAL)
    informal = await search_models(aconn, "video", reqs)
    assert shape(informal) == shape(canonical)
    assert slugs(canonical.full_matches) == ["testco-alpha"]
    assert informal.unresolved == []
    assert informal.interpretation[0].note.startswith("'")  # a sentence the agent can repeat


async def test_interpretation_reports_confidence(aconn):
    exact = (await search_models(aconn, "video", CANONICAL)).interpretation[0]
    alias = (await search_models(aconn, "video", [{"capability": "dubbing", "value": "Hindi"}])).interpretation[0]
    assert (exact.confidence, alias.confidence) == ("exact", "alias")
    assert "alias match" in alias.note and exact.capability_label == "Spoken languages"


async def test_misspellings_resolve_as_approximate(aconn):
    r = await search_models(aconn, "video", [{"capability": "voiceovr", "value": "Hindhi"}])
    assert shape(r)[:3] == shape(await search_models(aconn, "video", CANONICAL))[:3]
    i = r.interpretation[0]
    assert (i.capability, i.value, i.confidence) == ("speech_languages", "hi", "approximate")
    assert "approximate" in i.note


async def test_ambiguous_term_is_reported_with_both_readings(aconn):
    r = await search_models(aconn, "video", [{"capability": "modalities", "value": "video"}])
    assert r.interpretation[0].status == "ambiguous"
    (u,) = r.unresolved
    assert u.part == "capability" and set(u.suggestions) == {"input_modalities", "output_modalities"}


async def test_unresolved_term_is_reported_not_dropped(aconn):
    r = await search_models(
        aconn, "video", [{"capability": "lip sync"}, {"capability": "resolution", "value": "1080p"}]
    )
    assert [u.term for u in r.unresolved] == ["lip sync"]
    assert [i.status for i in r.interpretation] == ["unresolved", "resolved"]
    assert sorted(slugs(r.full_matches)) == ["testco-alpha", "testco-beta"]  # the readable requirement still applies


async def test_unresolved_value_is_reported(aconn):
    r = await search_models(aconn, "video", [{"capability": "speech_languages", "value": "Klingon"}])
    (u,) = r.unresolved
    assert u.part == "value" and "allowed values" in u.reason


async def test_not_recorded_is_cant_tell_not_no(aconn):
    r = await search_models(aconn, "video", CANONICAL)
    close = {m.slug: m for m in r.close_matches}
    assert set(close) == {"testco-gamma", "testco-epsilon"}
    assert close["testco-gamma"].not_recorded == [HINDI_SPEECH] and close["testco-gamma"].missed == []
    assert close["testco-gamma"].results[0].status == "not_recorded"
    assert "testco-gamma" not in slugs(r.partial_matches)


async def test_partial_match_lists_what_it_meets_and_misses(aconn):
    r = await search_models(
        aconn,
        "video",
        [{"capability": "speech_languages", "value": "hi"}, {"capability": "max_clip_seconds", "value": 15}],
    )
    assert r.full_matches == []
    beta = next(m for m in r.partial_matches if m.slug == "testco-beta")
    assert beta.met == ["max_clip_seconds at least 15 seconds"] and beta.missed == [HINDI_SPEECH]
    missed_fact = next(x for x in beta.results if x.status == "not_satisfied").fact
    assert missed_fact.value == ["en"] and missed_fact.source_url  # evidence for the "no"
    # Alpha has Hindi but a 10 s clip; Gamma has neither recorded, so it stays a close match.
    assert set(slugs(r.partial_matches)) == {"testco-alpha", "testco-beta"}
    assert sorted(slugs(r.close_matches)) == ["testco-epsilon", "testco-gamma"]


@pytest.mark.parametrize(
    ("reqs", "full", "partial"),
    [
        ([{"capability": "resolution", "value": "4K"}], ["testco-beta"], ["testco-alpha", "testco-gamma"]),
        ([{"capability": "max_resolution", "value": "1080p"}], ["testco-alpha", "testco-beta"], ["testco-gamma"]),
        ([{"capability": "clip length", "value": "15 seconds"}], ["testco-beta"], ["testco-alpha"]),
        ([{"capability": "max_clip_seconds", "value": "0.25 min"}], ["testco-beta"], ["testco-alpha"]),
        ([{"capability": "max_clip_seconds", "value": 15, "operator": "at_most"}], ["testco-alpha"], ["testco-beta"]),
        ([{"capability": "max_resolution", "value": "720p", "operator": "equals"}], ["testco-gamma"],
         ["testco-alpha", "testco-beta"]),
    ],
)  # fmt: skip
async def test_at_least_and_at_most_on_ordered_capabilities(aconn, reqs, full, partial):
    r = await search_models(aconn, "video", reqs)
    assert sorted(slugs(r.full_matches)) == sorted(full)
    assert sorted(slugs(r.partial_matches)) == sorted(partial)
    if reqs[0].get("operator") is None:
        assert r.interpretation[0].operator == "at_least" and "minimum" in r.interpretation[0].note


async def test_partial_matches_rank_nearer_misses_first(aconn):
    r = await search_models(aconn, "video", [{"capability": "resolution", "value": "4K"}])
    assert slugs(r.partial_matches) == ["testco-alpha", "testco-gamma"]  # 1080p is nearer 2160p than 720p


@pytest.mark.parametrize(
    ("value", "full", "partial"),
    [("yes", ["testco-alpha"], ["testco-beta"]), (None, ["testco-alpha"], ["testco-beta"]),
     ("no", ["testco-beta"], ["testco-alpha"]), (False, ["testco-beta"], ["testco-alpha"])],
)  # fmt: skip
async def test_boolean_words(aconn, value, full, partial):
    r = await search_models(aconn, "video", [{"capability": "background music", "value": value}])
    assert slugs(r.full_matches) == full and slugs(r.partial_matches) == partial
    assert sorted(slugs(r.close_matches)) == ["testco-epsilon", "testco-gamma"]


async def test_nice_requirements_rank_but_do_not_exclude(aconn):
    r = await search_models(
        aconn,
        "video",
        [{"capability": "audio", "value": True}, {"capability": "max_resolution", "value": "4K", "importance": "nice"}],
    )
    assert slugs(r.full_matches) == ["testco-beta", "testco-alpha", "testco-gamma"]


async def test_never_empty_when_the_modality_has_models(aconn):
    r = await search_models(aconn, "video", [{"capability": "lip sync"}])
    assert r.full_matches == r.close_matches == r.partial_matches == []
    assert len(r.other_models) == 4 and r.unresolved
    r = await search_models(aconn, "video", [{"capability": "speech languages", "value": "ta"}])
    assert len(r.full_matches) + len(r.close_matches) + len(r.partial_matches) == 4


async def test_pricing_is_searchable_for_presence_only(aconn):
    r = await search_models(aconn, "video", [{"capability": "price"}])
    assert slugs(r.full_matches) == ["testco-alpha"]
    assert r.full_matches[0].results[0].fact.source_type == "official_docs"
    r = await search_models(aconn, "video", [{"capability": "price", "value": "cheap"}])
    assert r.unresolved[0].part == "value"


async def test_operator_that_does_not_fit_is_reported(aconn):
    r = await search_models(aconn, "video", [{"capability": "speech_languages", "value": "hi", "operator": "at_least"}])
    assert r.unresolved[0].part == "operator" and r.interpretation[0].status == "unresolved"


async def test_modality_is_forgiving_and_completeness_is_reported(aconn):
    r = await search_models(aconn, "Videos", [])
    assert r.searched_modality == "video" and "read as modality" in r.modality_note
    assert r.total_models_in_modality == 4 and r.models_with_any_recorded_fact == 3
    r = await search_models(aconn, "hologram", [])
    assert r.searched_modality is None and r.unresolved[0].part == "modality"
    assert r.total_models_in_modality == 5


async def test_full_matches_rank_verified_evidence_first(aconn):
    r = await search_models(aconn, "video", [{"capability": "max_resolution", "value": "1080p"}])
    assert slugs(r.full_matches) == ["testco-beta", "testco-alpha"]  # Beta's resolution is verified, Alpha's not


async def test_results_carry_provenance(aconn):
    alpha = (await search_models(aconn, "video", CANONICAL)).full_matches[0]
    f = alpha.results[0].fact
    assert (f.verification_status, f.source_type) == ("verified", "official_docs") and f.last_verified_at


# --- thresholds, pinned against the draft vocabulary -------------------------------------------


@pytest.mark.parametrize(
    "term",
    ["voiceovr", "dubing", "resolutin", "backgroud music", "clip lenght", "contxt window", "pricng", "open sorce"],
)
async def test_capability_misspellings_resolve(aconn, term):
    r = await search_models(aconn, None, [{"capability": term}])
    assert r.interpretation[0].status == "resolved", r.interpretation[0].note


@pytest.mark.parametrize(
    "term", ["lip sync", "watermark", "frame rate", "camera control", "nsfw filter", "fine tuning", "aspect ratio"]
)
async def test_unrelated_phrases_stay_unresolved(aconn, term):
    r = await search_models(aconn, None, [{"capability": term}])
    assert r.interpretation[0].status == "unresolved"


async def test_threshold_margins(aconn):
    """Guard the measured gap: weakest wanted match above, strongest unwanted match below."""
    (hindhi,) = await similarities(aconn, "hindhi", ["hindi"])
    (batch,) = await similarities(aconn, "batch api", ["api"])
    assert hindhi >= VALUE_THRESHOLD and batch < CAPABILITY_THRESHOLD
