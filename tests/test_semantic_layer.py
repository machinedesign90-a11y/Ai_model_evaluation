"""The semantic layer text placed in the system prompt, and its cache."""

from __future__ import annotations

from agent.prompts import system_prompt
from agent.semantic_layer import SemanticLayerCache, render_semantic_layer
from catalog.vocabulary import load_vocabulary


async def test_render_lists_measures_then_filters_with_their_rules(aconn):
    text = render_semantic_layer(await load_vocabulary(aconn))
    measures, filters = text.split("Filters (include or exclude):")
    assert measures.startswith("Measures")
    assert "- price_usd_per_second ('Price per second'): number in USD per second" in measures
    assert "lower is better" in measures and "higher is better" in measures
    assert "values 480p, 720p, 1080p, 1440p, 2160p (lowest first)" in measures
    assert "- open_weights ('Open weights'): boolean; operators equals" in filters
    assert "pricing_notes" in filters and "display only" in filters
    assert "'voiceover'" in filters or "'dubbing'" in filters
    assert "speech_languages" not in measures


async def test_prompt_with_layer(aconn):
    prompt = system_prompt(render_semantic_layer(await load_vocabulary(aconn)))
    assert "Measures and filters:\nMeasures (compare" in prompt


async def test_cache_reuses_until_ttl_then_reloads(aconn):
    now = [0.0]
    cache = SemanticLayerCache(ttl_seconds=3600, clock=lambda: now[0])
    first = await cache.get(aconn)
    await aconn.execute("update capability_definitions set label = 'Clip length cap' where name = 'max_clip_seconds'")
    now[0] = 3599
    assert await cache.get(aconn) == first  # still cached
    now[0] = 3601
    assert "'Clip length cap'" in await cache.get(aconn)  # reloaded after the TTL


async def test_cache_disabled_with_zero_ttl(aconn):
    cache = SemanticLayerCache(ttl_seconds=0)
    await cache.get(aconn)
    await aconn.execute("update capability_definitions set label = 'Clip length cap' where name = 'max_clip_seconds'")
    assert "'Clip length cap'" in await cache.get(aconn)
