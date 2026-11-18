"""Tier 2: search_models over the semantic layer. People's words in, canonical reading reported, never empty."""

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
        sorted(slugs(r.full_matches)),
        sorted(slugs(r.close_matches)),
        sorted(slugs(r.partial_matches)),
        [(i.capability, i.operator, i.value) for i in r.interpretation],
    )


async def search(aconn, modality="video", **kw):
    return await search_models(aconn, modality, **kw)


CANONICAL = {"filters": [{"capability": "speech_languages", "value": "hi"}]}
WORDINGS = [
    {"filters": [{"capability": "Hindi voiceover"}]},
    {"filters": [{"capability": "Hindi voiceover", "value": "Hindi"}]},
    {"filters": [{"capability": "dubbing", "value": "Hindi"}]},
    {"filters": [{"capability": "dubbing in Hindi"}]},
    {"filters": [{"capability": "speech_languages hi"}]},
    {"filters": [{"capability": "voiceovr", "value": "Hindhi"}]},
    {"filters": [{"value": "Hindi"}]},
    {"filters": [{"capability": "Voice-Over", "value": "हिन्दी"}]},
]


@pytest.mark.parametrize("spec", WORDINGS, ids=lambda s: str(s["filters"][0]))
async def test_informal_and_canonical_wording_give_the_same_result(aconn, spec):
    canonical = await search(aconn, **CANONICAL)
    informal = await search(aconn, **spec)
    assert shape(informal) == shape(canonical)
    assert slugs(canonical.full_matches) == ["testco-alpha"]
    assert informal.unresolved == [] and informal.ambiguous == []
    assert informal.interpretation[0].note.startswith("'")


async def test_interpretation_reports_how_words_were_read(aconn):
    exact = (await search(aconn, **CANONICAL)).interpretation[0]
    alias = (await search(aconn, filters=[{"capability": "dubbing", "value": "Hindi"}])).interpretation[0]
    typo = (await search(aconn, filters=[{"capability": "voiceovr", "value": "Hindhi"}])).interpretation[0]
    assert (exact.confidence, alias.confidence, typo.confidence) == ("exact", "alias", "approximate")
    assert "alias match" in alias.note and "approximate" in typo.note
    assert exact.kind == "filter" and exact.capability_label == "Spoken languages"


# --- the translations the spec names -----------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "capability", "operator", "value"),
    [
        ({"capability": "closed source"}, "open_weights", "equals", False),
        ({"capability": "open source"}, "open_weights", "equals", True),
        ({"capability": "proprietary"}, "open_weights", "equals", False),
        ({"capability": "not deprecated"}, "deprecated", "equals", False),
        ({"capability": "background music", "value": "yes"}, "background_audio", "equals", True),
        ({"capability": "at least 4K"}, "max_resolution", "at_least", "2160p"),
        ({"capability": "resolution", "value": "4K"}, "max_resolution", "at_least", "2160p"),
        ({"value": "full hd"}, "max_resolution", "at_least", "1080p"),
        ({"capability": "longer than 10 seconds"}, "max_clip_seconds", "at_least", 10),
        ({"capability": "clip length", "value": "more than 15s"}, "max_clip_seconds", "at_least", 15),
        ({"capability": "video length", "value": "2 minutes or more"}, "max_clip_seconds", "at_least", 120),
        ({"capability": "under $0.30 per second"}, "price_usd_per_second", "at_most", 0.3),
        ({"capability": "price", "value": 0.3}, "price_usd_per_second", "at_most", 0.3),
    ],
)
async def test_translations_to_filters_measures_and_operators(aconn, entry, capability, operator, value):
    r = await search(aconn, measures=[entry])
    (i,) = r.interpretation
    assert (i.status, i.capability, i.operator, i.value) == ("resolved", capability, operator, value), i.note


async def test_cheapest_and_longest_sort_with_unrecorded_last(aconn):
    cheap = await search(aconn, sort={"capability": "cheapest"})
    assert (cheap.sort.capability, cheap.sort.direction) == ("price_usd_per_second", "asc")
    assert slugs(cheap.full_matches)[:2] == ["testco-beta", "testco-alpha"]
    tail = cheap.full_matches[2:]
    assert {m.slug for m in tail} == {"testco-gamma", "testco-epsilon"} and all(m.sort_fact is None for m in tail)
    assert "not recorded values last" in cheap.sort.note
    longest = await search(aconn, sort={"capability": "longest"})
    assert (longest.sort.capability, longest.sort.direction) == ("max_clip_seconds", "desc")
    assert slugs(longest.full_matches)[:2] == ["testco-beta", "testco-alpha"]
    priciest = await search(aconn, sort={"capability": "price", "direction": "worst"})
    assert slugs(priciest.full_matches)[:2] == ["testco-alpha", "testco-beta"]


async def test_sort_combines_with_filters(aconn):
    r = await search(aconn, filters=[{"capability": "audio"}], sort={"capability": "cheapest"})
    assert slugs(r.full_matches) == ["testco-beta", "testco-alpha", "testco-gamma"]
    assert r.full_matches[-1].sort_fact is None


async def test_sort_by_a_filter_is_reported(aconn):
    r = await search(aconn, sort={"capability": "speech languages"})
    assert r.sort is None and r.unresolved[0].part == "sort"


async def test_price_is_modality_aware(aconn):
    image = await search(aconn, "image", measures=[{"capability": "price", "value": 0.1}])
    assert image.interpretation[0].capability == "price_usd_per_image"
    text = await search(aconn, "text", sort={"capability": "cheapest"})
    assert text.sort is None and set(text.ambiguous[0].candidates) == {
        "price_usd_per_1m_input_tokens", "price_usd_per_1m_output_tokens",
    }  # fmt: skip
    anywhere = await search(aconn, None, sort={"capability": "price"})
    assert len(anywhere.ambiguous[0].candidates) == 4


async def test_a_measure_under_filters_is_moved_and_reported(aconn):
    r = await search(aconn, filters=[{"capability": "max_clip_seconds", "value": 15}])
    (i,) = r.interpretation
    assert i.kind == "measure" and "moved from filters to measures" in i.note
    assert slugs(r.full_matches) == ["testco-beta"]


# --- statuses and grouping ---------------------------------------------------------------------


async def test_not_recorded_is_cant_tell_not_no(aconn):
    r = await search(aconn, **CANONICAL)
    close = {m.slug: m for m in r.close_matches}
    assert set(close) == {"testco-gamma", "testco-epsilon"}
    assert close["testco-gamma"].not_recorded == [HINDI_SPEECH] and close["testco-gamma"].missed == []
    assert close["testco-gamma"].results[0].status == "not_recorded"


async def test_partial_match_lists_what_it_meets_and_misses(aconn):
    r = await search(
        aconn, filters=[{"capability": "speech_languages", "value": "hi"}],
        measures=[{"capability": "max_clip_seconds", "value": 15}],
    )  # fmt: skip
    assert r.full_matches == []
    beta = next(m for m in r.partial_matches if m.slug == "testco-beta")
    assert beta.met == ["max_clip_seconds at least 15 seconds"] and beta.missed == [HINDI_SPEECH]
    missed = next(x for x in beta.results if x.status == "not_satisfied").fact
    assert missed.value == ["en"] and missed.source_url
    assert set(slugs(r.partial_matches)) == {"testco-alpha", "testco-beta"}
    assert sorted(slugs(r.close_matches)) == ["testco-epsilon", "testco-gamma"]


@pytest.mark.parametrize(
    ("measure", "full", "partial"),
    [
        ({"capability": "resolution", "value": "4K"}, ["testco-beta"], ["testco-alpha", "testco-gamma"]),
        ({"capability": "max_resolution", "value": "1080p"}, ["testco-alpha", "testco-beta"], ["testco-gamma"]),
        ({"capability": "clip length", "value": "15 seconds"}, ["testco-beta"], ["testco-alpha"]),
        ({"capability": "max_clip_seconds", "value": "0.25 min"}, ["testco-beta"], ["testco-alpha"]),
        ({"capability": "max_clip_seconds", "value": 15, "operator": "at_most"}, ["testco-alpha"], ["testco-beta"]),
        ({"capability": "max_resolution", "value": "720p", "operator": "equals"}, ["testco-gamma"],
         ["testco-alpha", "testco-beta"]),
    ],
)  # fmt: skip
async def test_at_least_and_at_most_on_numbers_and_ordered_enums(aconn, measure, full, partial):
    r = await search(aconn, measures=[measure])
    assert sorted(slugs(r.full_matches)) == sorted(full)
    assert sorted(slugs(r.partial_matches)) == sorted(partial)


async def test_partial_matches_rank_nearer_misses_first(aconn):
    r = await search(aconn, measures=[{"capability": "resolution", "value": "4K"}])
    assert slugs(r.partial_matches) == ["testco-alpha", "testco-gamma"]


async def test_full_matches_rank_verified_evidence_first(aconn):
    r = await search(aconn, measures=[{"capability": "max_resolution", "value": "1080p"}])
    assert slugs(r.full_matches) == ["testco-beta", "testco-alpha"]


async def test_nice_requirements_rank_but_do_not_exclude(aconn):
    r = await search(
        aconn, filters=[{"capability": "audio", "value": True}],
        measures=[{"capability": "max_resolution", "value": "4K", "importance": "nice"}],
    )  # fmt: skip
    assert slugs(r.full_matches) == ["testco-beta", "testco-alpha", "testco-gamma"]


# --- honest failure modes ----------------------------------------------------------------------


async def test_ambiguous_term_is_reported_with_its_readings(aconn):
    r = await search(aconn, filters=[{"capability": "modalities", "value": "video"}])
    assert r.interpretation[0].status == "ambiguous"
    assert set(r.ambiguous[0].candidates) == {"input_modalities", "output_modalities"}


async def test_unresolved_term_is_reported_not_dropped(aconn):
    r = await search(
        aconn, filters=[{"capability": "lip sync"}], measures=[{"capability": "resolution", "value": "1080p"}]
    )
    assert [u.term for u in r.unresolved] == ["lip sync"]
    assert [i.status for i in r.interpretation] == ["unresolved", "resolved"]
    assert sorted(slugs(r.full_matches)) == ["testco-alpha", "testco-beta"]


async def test_unresolved_value_and_wrong_unit_are_reported(aconn):
    r = await search(aconn, filters=[{"capability": "speech_languages", "value": "Klingon"}])
    assert r.unresolved[0].part == "value" and "allowed values" in r.unresolved[0].reason
    r = await search(aconn, measures=[{"capability": "clip length", "value": "100 tokens"}])
    assert r.unresolved[0].part == "value"


async def test_operator_that_does_not_fit_is_reported(aconn):
    r = await search(aconn, filters=[{"capability": "speech_languages", "value": "hi", "operator": "at_least"}])
    assert r.unresolved[0].part == "operator"


async def test_display_only_capability_is_reported(aconn):
    r = await search(aconn, filters=[{"capability": "pricing notes", "value": "tiered"}])
    assert r.unresolved[0].part == "operator" and "display only" in r.unresolved[0].reason


async def test_never_empty_when_the_modality_has_models(aconn):
    r = await search(aconn, filters=[{"capability": "lip sync"}])
    assert r.full_matches == r.close_matches == r.partial_matches == []
    assert len(r.other_models) == 4 and r.unresolved
    r = await search(aconn, filters=[{"capability": "speech languages", "value": "ta"}])
    assert len(r.full_matches) + len(r.close_matches) + len(r.partial_matches) == 4


async def test_modality_is_forgiving_and_completeness_is_reported(aconn):
    r = await search(aconn, "Videos")
    assert r.searched_modality == "video" and "read as modality" in r.modality_note
    assert r.total_models_in_modality == 4 and r.models_with_any_recorded_fact == 3
    r = await search(aconn, "hologram")
    assert r.searched_modality is None and r.unresolved[0].part == "modality"
    assert r.total_models_in_modality == 6


async def test_results_carry_provenance(aconn):
    alpha = (await search(aconn, **CANONICAL)).full_matches[0]
    f = alpha.results[0].fact
    assert (f.verification_status, f.source_type) == ("verified", "official_docs") and f.last_verified_at


# --- thresholds, pinned against the draft vocabulary -------------------------------------------


@pytest.mark.parametrize(
    "term", ["voiceovr", "dubing", "resolutin", "backgroud music", "clip lenght", "contxt window", "open sorce"]
)
async def test_capability_misspellings_resolve(aconn, term):
    r = await search_models(aconn, None, filters=[{"capability": term}])
    assert r.interpretation[0].status == "resolved", r.interpretation[0].note


@pytest.mark.parametrize(
    "term", ["lip sync", "watermark", "frame rate", "camera control", "nsfw filter", "fine tuning", "aspect ratio"]
)
async def test_unrelated_phrases_stay_unresolved(aconn, term):
    r = await search_models(aconn, None, filters=[{"capability": term}])
    assert r.interpretation[0].status == "unresolved"


async def test_threshold_margins(aconn):
    """Guard the measured gap: weakest wanted match above, strongest unwanted match below."""
    (hindhi,) = await similarities(aconn, "hindhi", ["hindi"])
    (batch,) = await similarities(aconn, "batch api", ["api"])
    assert hindhi >= VALUE_THRESHOLD and batch < CAPABILITY_THRESHOLD
