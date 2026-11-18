"""Read-only catalog queries. Every value from the caller goes in as a bound parameter."""

from __future__ import annotations

import difflib
import json
from collections import defaultdict
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, ValidationError

from catalog.schemas import (
    CapabilityRequirement,
    ComparedModel,
    CompareInput,
    CompareResult,
    Fact,
    GetModelResult,
    ModelDetail,
    ModelSummary,
    NotFound,
    SearchInput,
    SearchMatch,
    SearchResult,
    Slug,
    UnconfirmedCandidate,
)


class _SlugInput(BaseModel):
    slug: Slug


class CatalogInputError(ValueError):
    """The caller's arguments were invalid. The message is safe to show to the model."""


def _validate(model: type, **kwargs: Any):
    try:
        return model(**kwargs)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        raise CatalogInputError(problems) from None


def _fact(row: dict[str, Any]) -> Fact:
    return Fact(
        name=row["fact_name"],
        value=row["fact_value"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        verification_status=row["verification_status"],
        last_verified_at=row["last_verified_at"],
    )


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("slug", "name", "provider", "modality")}


async def _fetch(conn: AsyncConnection, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


# --- search_models -----------------------------------------------------------------------------


async def search_models(
    conn: AsyncConnection,
    modality: str | None = None,
    required_capabilities: list[CapabilityRequirement | dict[str, Any]] | None = None,
    limit: int = 10,
) -> SearchResult:
    """Models whose recorded facts satisfy every requirement, plus candidates with gaps.

    A model is a match when every requirement has a recorded fact that contains the
    required value. It is "unconfirmed" when no recorded fact contradicts a requirement
    but at least one required capability has no row at all (not recorded). Models with a
    recorded fact that fails a requirement are neither.
    """
    args = _validate(SearchInput, modality=modality, required_capabilities=required_capabilities or [], limit=limit)
    reqs: list[CapabilityRequirement] = args.required_capabilities

    models = await _fetch(
        conn,
        """select id, slug, name, provider, modality from models
           where %(modality)s::text is null or lower(modality) = %(modality)s
           order by name, slug""",
        {"modality": args.modality},
    )

    # One row per (model, requirement): whether a fact is recorded and whether it satisfies.
    evaluations: dict[Any, dict[int, dict[str, Any]]] = defaultdict(dict)
    if reqs and models:
        req_json = json.dumps([{"idx": i, "name": r.name, "value": r.value} for i, r in enumerate(reqs)])
        rows = await _fetch(
            conn,
            """with req as (
                    select r.idx, r.name, r.value
                    from jsonb_to_recordset(%(reqs)s::jsonb) as r(idx int, name text, value jsonb)
                )
                select m.id as model_id, req.idx, c.id is not null as recorded,
                       coalesce(c.value @> req.value, false) as satisfied,
                       c.name as fact_name, c.value as fact_value, c.verification_status, c.last_verified_at,
                       s.source_type, s.url as source_url
                from models m
                cross join req
                left join capabilities c on c.model_id = m.id and c.name = req.name
                left join sources s on s.id = c.source_id
                where m.id = any(%(ids)s)""",
            {"reqs": req_json, "ids": [m["id"] for m in models]},
        )
        for row in rows:
            evaluations[row["model_id"]][row["idx"]] = row

    matches: list[SearchMatch] = []
    unconfirmed: list[UnconfirmedCandidate] = []
    for m in models:
        ev = evaluations.get(m["id"], {})
        rows = [ev[i] for i in range(len(reqs))]
        if any(r["recorded"] and not r["satisfied"] for r in rows):
            continue
        facts = _dedupe([_fact(r) for r in rows if r["satisfied"]])
        missing = list(dict.fromkeys(r_req.name for r, r_req in zip(rows, reqs, strict=True) if not r["recorded"]))
        if missing:
            unconfirmed.append(UnconfirmedCandidate(**_summary(m), facts=facts, missing_capabilities=missing))
        else:
            matches.append(SearchMatch(**_summary(m), facts=facts))

    verified = lambda item: -sum(f.verification_status == "verified" for f in item.facts)  # noqa: E731
    matches.sort(key=verified)
    unconfirmed.sort(key=lambda item: (len(item.missing_capabilities), verified(item)))

    names = [r["name"] for r in await _fetch(conn, "select distinct name from capabilities order by name")]
    unknown = sorted({r.name for r in reqs} - set(names))
    modalities = [r["modality"] for r in await _fetch(conn, "select distinct modality from models order by 1")]

    return SearchResult(
        searched_modality=args.modality,
        total_models_in_modality=len(models),
        matches=matches[: args.limit],
        total_matches=len(matches),
        unconfirmed=unconfirmed[: args.limit],
        total_unconfirmed=len(unconfirmed),
        unknown_capability_names=unknown,
        known_capability_names=names if unknown else [],
        available_modalities=modalities,
    )


def _dedupe(facts: list[Fact]) -> list[Fact]:
    seen, out = set(), []
    for f in facts:
        if f.name not in seen:
            seen.add(f.name)
            out.append(f)
    return out


# --- get_model ---------------------------------------------------------------------------------


async def _suggest(conn: AsyncConnection, slug: str, n: int = 3) -> list[ModelSummary]:
    rows = await _fetch(conn, "select slug, name, provider, modality from models")
    key = slug.lower()
    scored = []
    for r in rows:
        candidates = [r["slug"].lower(), r["name"].lower()]
        ratio = max(difflib.SequenceMatcher(None, key, c).ratio() for c in candidates)
        if any(key in c or c in key for c in candidates):
            ratio = max(ratio, 0.9)
        if ratio >= 0.5:
            scored.append((ratio, r))
    scored.sort(key=lambda t: (-t[0], t[1]["slug"]))
    return [ModelSummary(**_summary(r)) for _, r in scored[:n]]


async def _facts_for(conn: AsyncConnection, model_ids: list[Any], names: list[str] | None) -> dict[Any, list[Fact]]:
    rows = await _fetch(
        conn,
        """select c.model_id,
                   c.name as fact_name, c.value as fact_value, c.verification_status, c.last_verified_at,
                   s.source_type, s.url as source_url
            from capabilities c left join sources s on s.id = c.source_id
            where c.model_id = any(%(ids)s) and (%(names)s::text[] is null or c.name = any(%(names)s))
            order by c.name""",
        {"ids": model_ids, "names": names},
    )
    out: dict[Any, list[Fact]] = defaultdict(list)
    for r in rows:
        out[r["model_id"]].append(_fact(r))
    return out


async def get_model(conn: AsyncConnection, slug: str) -> GetModelResult:
    """One model with every recorded fact and its provenance, or not_found with suggestions."""
    clean = _validate(_SlugInput, slug=slug).slug
    rows = await _fetch(
        conn,
        """select id, slug, name, provider, modality, description, pricing, api_available
           from models where slug = %(slug)s""",
        {"slug": clean},
    )
    if not rows:
        return GetModelResult(status="not_found", requested_slug=clean, suggestions=await _suggest(conn, clean))
    m = rows[0]
    facts = (await _facts_for(conn, [m["id"]], None)).get(m["id"], [])
    detail = ModelDetail(
        **_summary(m),
        description=m["description"],
        pricing=m["pricing"] or None,  # {} is the column default: not recorded
        api_available=m["api_available"],
        facts=facts,
    )
    return GetModelResult(status="found", model=detail, requested_slug=clean)


# --- compare_models ----------------------------------------------------------------------------


async def compare_models(
    conn: AsyncConnection, slugs: list[str], capability_names: list[str] | None = None
) -> CompareResult:
    """Side by side facts for 2 to 3 models. Requested capabilities with no row are listed as not_recorded.

    Without capability_names, compares every capability recorded for any of the models.
    """
    args = _validate(CompareInput, slugs=slugs, capability_names=capability_names)
    rows = await _fetch(
        conn,
        "select id, slug, name, provider, modality from models where slug = any(%(slugs)s)",
        {"slugs": args.slugs},
    )
    by_slug = {r["slug"]: r for r in rows}
    found = [by_slug[s] for s in args.slugs if s in by_slug]
    not_found = [NotFound(slug=s, suggestions=await _suggest(conn, s)) for s in args.slugs if s not in by_slug]

    names_filter = list(dict.fromkeys(args.capability_names)) if args.capability_names else None
    facts = await _facts_for(conn, [m["id"] for m in found], names_filter)
    names = names_filter or sorted({f.name for fs in facts.values() for f in fs})

    compared = []
    for m in found:
        recorded = {f.name: f for f in facts.get(m["id"], [])}
        compared.append(
            ComparedModel(
                **_summary(m),
                facts=[recorded[n] for n in names if n in recorded],
                not_recorded=[n for n in names if n not in recorded],
            )
        )
    return CompareResult(capability_names=names, models=compared, not_found=not_found)
