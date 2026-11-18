"""Catalog tools. Strict data, forgiving search: people's words are translated into canonical terms
(see catalog/vocabulary.py), every translation is reported, and nothing unrecognised is dropped.

Every value from the caller reaches SQL as a bound parameter; the SQL text is static.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import ValidationError

from catalog.schemas import (
    CapabilityInfo,
    CapabilityReading,
    ComparedModel,
    CompareInput,
    CompareResult,
    Fact,
    GetModelResult,
    Interpretation,
    ListCapabilitiesResult,
    ModelDetail,
    ModelMatch,
    ModelSummary,
    Requirement,
    RequirementResult,
    SearchInput,
    SearchResult,
    Unresolved,
)
from catalog.text import normalize
from catalog.vocabulary import (
    Definition,
    Resolution,
    Vocabulary,
    load_vocabulary,
    resolve_capability,
    resolve_modality,
    resolve_model,
    resolve_value,
)


class CatalogInputError(ValueError):
    """The caller's arguments were invalid. The message is safe to show to the model."""


def _validate(model: type, **kwargs: Any):
    try:
        return model(**kwargs)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        raise CatalogInputError(problems) from None


async def _fetch(conn: AsyncConnection, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("slug", "name", "provider", "modality")}


async def _facts_for(conn: AsyncConnection, model_ids: list[Any], names: list[str] | None) -> dict[Any, list[Fact]]:
    rows = await _fetch(
        conn,
        """select c.model_id, c.name, c.value, c.verification_status, c.last_verified_at,
                  s.source_type, s.url as source_url
           from capabilities c left join sources s on s.id = c.source_id
           where c.model_id = any(%(ids)s) and (%(names)s::text[] is null or c.name = any(%(names)s))
           order by c.name""",
        {"ids": model_ids, "names": names},
    )
    out: dict[Any, list[Fact]] = defaultdict(list)
    for r in rows:
        out[r["model_id"]].append(
            Fact(
                name=r["name"],
                value=r["value"],
                source_type=r["source_type"],
                source_url=r["source_url"],
                verification_status=r["verification_status"],
                last_verified_at=r["last_verified_at"],
            )
        )
    return out


# --- reading one requirement -------------------------------------------------------------------

_EDGE_WORDS = {"in", "with", "for", "of", "and", "a", "an", "the", "to"}


def _strip_edges(tokens: list[str]) -> list[str]:
    while tokens and tokens[0] in _EDGE_WORDS:
        tokens = tokens[1:]
    while tokens and tokens[-1] in _EDGE_WORDS:
        tokens = tokens[:-1]
    return tokens


def _remove_run(tokens: list[str], run: list[str]) -> list[str] | None:
    for i in range(len(tokens) - len(run) + 1):
        if tokens[i : i + len(run)] == run:
            return tokens[:i] + tokens[i + len(run) :]
    return None


async def _split_capability_and_value(
    conn: AsyncConnection, vocab: Vocabulary, capability: str, value: Any
) -> tuple[str, Any, str | None]:
    """Handle a value written into the capability phrase ("Hindi voiceover", "speech_languages hi").

    With a value given, drop its words from the phrase. Without one, take a word that is a known value
    of the capability the rest of the phrase names. Returns (capability phrase, value, note).
    """
    tokens = normalize(capability).split()
    if value is not None:
        rest = _remove_run(tokens, normalize(str(value)).split()) if isinstance(value, str) else None
        rest = _strip_edges(rest) if rest is not None else None
        return (" ".join(rest), value, None) if rest else (capability, value, None)

    for n in (3, 2, 1):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            owners = {cap for cap, entries in vocab.value_terms.items() if any(t == gram for t, _, _ in entries)}
            if not owners:
                continue
            rest = _strip_edges(tokens[:i] + tokens[i + n :])
            if not rest:
                continue
            res = await resolve_capability(conn, vocab, " ".join(rest))
            if res.status == "resolved" and res.target in owners:
                return " ".join(rest), gram, f"took '{gram}' from '{capability}' as the value"
    return capability, None, None


def _describe(d: Definition, operator: str, value: Any) -> str:
    if value is None:
        return f"{d.name} recorded"
    unit = f" {d.unit}" if d.unit and d.value_type == "number" else ""
    shown = value if not isinstance(value, bool) else str(value).lower()
    verb = "is" if operator == "equals" else operator.replace("_", " ")
    return f"{d.name} {verb} {shown}{unit}"


_WEAKEST = {"exact": 0, "alias": 1, "approximate": 2}


async def _read_requirement(
    conn: AsyncConnection, vocab: Vocabulary, idx: int, req: Requirement
) -> tuple[Interpretation, list[Unresolved], dict[str, Any] | None]:
    """Interpret one requirement. Returns (interpretation, unresolved items, executable form or None)."""
    phrase, value, split_note = await _split_capability_and_value(conn, vocab, req.capability, req.value)
    base = dict(requirement=idx, input_capability=req.capability, input_value=req.value, importance=req.importance)
    cap = await resolve_capability(conn, vocab, phrase)

    if cap.status != "resolved":
        reason = (
            f"'{req.capability}' could mean " + " or ".join(cap.candidates)
            if cap.status == "ambiguous"
            else f"'{req.capability}' does not match any recorded capability"
        )
        interp = Interpretation(
            **base, status=cap.status, capability=None, capability_label=None, operator=None, value=None,
            confidence=None, note=reason,
        )  # fmt: skip
        return interp, [Unresolved(requirement=idx, term=req.capability, part="capability", reason=reason,
                                   suggestions=cap.candidates)], None  # fmt: skip

    d = vocab.definitions[cap.target]
    val: Resolution = await resolve_value(conn, vocab, d, value)
    operator = req.operator
    problems: list[Unresolved] = []
    if val.status != "resolved":
        reason = (
            f"'{value}' could mean " + " or ".join(map(str, val.candidates))
            if val.status == "ambiguous"
            else val.note or f"'{value}' is not a recorded value of {d.name}"
        )
        problems.append(Unresolved(requirement=idx, term=str(value), part="value", reason=reason,
                                   suggestions=[str(c) for c in val.candidates]))  # fmt: skip
    elif val.target is not None:
        if operator is None:
            operator = "at_least" if d.ordered else "equals" if d.value_type == "boolean" else "contains"
        if operator in ("at_least", "at_most") and not d.ordered:
            problems.append(Unresolved(
                requirement=idx, term=operator, part="operator",
                reason=f"{operator} needs a number or an ordered list; {d.name} is {d.value_type}",
                suggestions=["contains", "equals"],
            ))  # fmt: skip
    else:
        operator = None

    confidence = max((c for c in (cap.confidence, val.confidence) if c), key=_WEAKEST.__getitem__)
    how = {"exact": "", "alias": " (alias match)", "approximate": " (approximate match)"}
    if problems:
        note = f"'{req.capability}' read as {d.name}{how[cap.confidence]}, but " + problems[0].reason
        interp = Interpretation(
            **base, status="unresolved", capability=d.name, capability_label=d.label, operator=None, value=None,
            confidence=cap.confidence, note=note,
        )  # fmt: skip
        return interp, problems, None

    words = f"'{req.capability}'" + (f" / '{req.value}'" if req.value is not None else "")
    note = f"{words} read as {_describe(d, operator or 'contains', val.target)}{how[confidence]}"
    extras = [n for n in (split_note, val.note) if n]
    if req.operator is None and operator == "at_least":
        extras.append("read as a minimum")
    if extras:
        note += "; " + "; ".join(extras)
    interp = Interpretation(
        **base, status="resolved", capability=d.name, capability_label=d.label, operator=operator,
        value=val.target, confidence=confidence, note=note,
    )  # fmt: skip
    plan = {"index": idx, "definition": d, "operator": operator, "value": val.target, "importance": req.importance,
            "label": _describe(d, operator or "contains", val.target)}  # fmt: skip
    return interp, [], plan


def _closeness(plan: dict[str, Any], fact: Fact | None) -> float:
    """For a missed at_least / at_most requirement: how near the recorded value came (0 to 1)."""
    if fact is None or plan["operator"] not in ("at_least", "at_most"):
        return 0.0
    d: Definition = plan["definition"]
    have, want = fact.value, plan["value"]
    if d.value_type == "enum":
        order = d.allowed_values or []
        if have not in order or want not in order:
            return 0.0
        have, want = order.index(have) + 1, order.index(want) + 1
    if not isinstance(have, int | float) or isinstance(have, bool) or not have or not want:
        return 0.0
    ratio = have / want if plan["operator"] == "at_least" else want / have
    return max(0.0, min(ratio, 1.0))


def _evaluate(plan: dict[str, Any], fact: Fact | None) -> str:
    if fact is None:
        return "not_recorded"
    d: Definition = plan["definition"]
    want, op, have = plan["value"], plan["operator"], fact.value
    if want is None:
        return "satisfied"
    if d.value_type == "enum_list":
        ok = isinstance(have, list) and (want in have if op == "contains" else have == [want])
    elif d.value_type == "text":
        ok = isinstance(have, str) and (
            normalize(want) in normalize(have) if op == "contains" else normalize(want) == normalize(have)
        )
    elif op in ("at_least", "at_most"):
        if d.value_type == "enum":
            order = d.allowed_values or []
            if have not in order or want not in order:
                return "not_satisfied"
            have, want = order.index(have), order.index(want)
        if not isinstance(have, int | float) or isinstance(have, bool):
            return "not_satisfied"
        ok = have >= want if op == "at_least" else have <= want
    else:
        ok = have == want
    return "satisfied" if ok else "not_satisfied"


# --- search_models -----------------------------------------------------------------------------


async def search_models(
    conn: AsyncConnection,
    modality: str | None = None,
    requirements: list[Requirement | dict[str, Any]] | None = None,
    limit: int = 10,
) -> SearchResult:
    """Find models for a need described in everyday words.

    Each requirement's capability and value are translated into canonical terms and reported in
    `interpretation`. Per model and requirement the status is satisfied, not_satisfied or not_recorded
    (absence of a fact is never a "no"). Models are grouped by their 'must' requirements:
    full (all satisfied), close (none contradicted, some not recorded), partial (some not satisfied).
    'nice' requirements only affect ranking; among partial matches, nearer misses on at-least/at-most rank
    first. When the modality has models the result is never empty.
    """
    args = _validate(SearchInput, modality=modality, requirements=requirements or [], limit=limit)
    vocab = await load_vocabulary(conn)
    searched, modality_note = await resolve_modality(conn, args.modality)
    unresolved: list[Unresolved] = []
    if modality_note and searched is None:
        unresolved.append(Unresolved(requirement=None, term=args.modality, part="modality", reason=modality_note,
                                     suggestions=[]))  # fmt: skip

    interpretation, plans = [], []
    for idx, req in enumerate(args.requirements):
        interp, problems, plan = await _read_requirement(conn, vocab, idx, req)
        interpretation.append(interp)
        unresolved.extend(problems)
        if plan:
            plans.append(plan)

    models = await _fetch(
        conn,
        """select id, slug, name, provider, modality from models
           where %(m)s::text is null or modality = %(m)s order by name, slug""",
        {"m": searched},
    )
    ids = [m["id"] for m in models]
    with_facts = await _fetch(
        conn, "select count(distinct model_id) as n from capabilities where model_id = any(%(ids)s)", {"ids": ids}
    )
    names = sorted({p["definition"].name for p in plans})
    facts = await _facts_for(conn, ids, names) if plans else {}

    full, close, partial = [], [], []
    for m in models:
        by_name = {f.name: f for f in facts.get(m["id"], [])}
        results, met, missed, unknown = [], [], [], []
        score = [0, 0, 0, 0]  # must met, nice met, -must missed, -must not recorded
        nearness = 0.0  # how close the misses came, for ranking partial matches
        for p in plans:
            fact = by_name.get(p["definition"].name)
            status = _evaluate(p, fact)
            results.append(RequirementResult(requirement=p["index"], capability=p["definition"].name,
                                             status=status, fact=fact))  # fmt: skip
            must = p["importance"] == "must"
            {"satisfied": met, "not_satisfied": missed, "not_recorded": unknown}[status].append(p["label"])
            if status == "satisfied":
                score[0 if must else 1] += 1
            elif must:
                score[2 if status == "not_satisfied" else 3] -= 1
            if status == "not_satisfied":
                nearness += _closeness(p, fact)
        verified = sum(r.fact is not None and r.fact.verification_status == "verified" for r in results)
        entry = ModelMatch(**_summary(m), results=results, met=met, missed=missed, not_recorded=unknown)
        bucket = full if score[2] == 0 and score[3] == 0 else close if score[2] == 0 else partial
        bucket.append(((-score[0], -score[1], -score[2], -score[3], -nearness, -verified, m["name"]), entry))

    def top(items):
        return [e for _, e in sorted(items, key=lambda t: t[0])[: args.limit]]

    nothing_read = bool(args.requirements) and not plans
    return SearchResult(
        searched_modality=searched,
        modality_note=modality_note,
        total_models_in_modality=len(models),
        models_with_any_recorded_fact=with_facts[0]["n"],
        interpretation=interpretation,
        unresolved=unresolved,
        full_matches=[] if nothing_read else top(full),
        close_matches=top(close),
        partial_matches=top(partial),
        total_full_matches=0 if nothing_read else len(full),
        total_close_matches=len(close),
        total_partial_matches=len(partial),
        other_models=[ModelSummary(**_summary(m)) for m in models[: args.limit]] if nothing_read else [],
    )


# --- list_capabilities -------------------------------------------------------------------------


async def list_capabilities(conn: AsyncConnection, modality: str | None = None) -> ListCapabilitiesResult:
    """The capability vocabulary: what can be asked about, with value formats and example wording."""
    if modality is not None and len(modality) > 50:
        raise CatalogInputError("modality: at most 50 characters")
    vocab = await load_vocabulary(conn)
    searched, note = await resolve_modality(conn, modality)
    if searched is None and modality and normalize(modality):
        # Not a modality any model has yet; still filter by the vocabulary's own modality tags.
        tagged = {m for d in vocab.definitions.values() for m in d.modalities}
        hit = next((m for m in tagged if normalize(m) in (normalize(modality), normalize(modality).removesuffix("s"))),
                   None)  # fmt: skip
        if hit:
            searched, note = hit, None
    caps = [
        CapabilityInfo(
            name=d.name,
            label=d.label,
            description=d.description,
            value_type=d.value_type,
            unit=d.unit,
            allowed_values=d.allowed_values,
            modalities=d.modalities,
            example_aliases=vocab.aliases.get(d.name, [])[:5],
        )  # fmt: skip
        for d in vocab.definitions.values()
        if searched is None or not d.modalities or searched in d.modalities
    ]
    return ListCapabilitiesResult(modality=searched, modality_note=note, capabilities=caps)


# --- get_model ---------------------------------------------------------------------------------


async def get_model(conn: AsyncConnection, slug: str) -> GetModelResult:
    """One model, found by slug or name (exact, then case-insensitive name, then approximate), with every
    recorded fact (pricing and api_available included) and its provenance. The description is display
    text, not a fact."""
    if not isinstance(slug, str) or not slug.strip() or len(slug) > 200:
        raise CatalogInputError("slug: give a model slug or name (1 to 200 characters)")
    row, matched_by, suggestions = await resolve_model(conn, slug)
    if row is None:
        return GetModelResult(status="not_found", requested=slug, suggestions=suggestions)
    facts = (await _facts_for(conn, [row["id"]], None)).get(row["id"], [])
    detail = ModelDetail(**_summary(row), display_description=row["description"], facts=facts)
    return GetModelResult(status="found", requested=slug, matched_by=matched_by, model=detail)


# --- compare_models ----------------------------------------------------------------------------


async def compare_models(
    conn: AsyncConnection, slugs: list[str], capability_names: list[str] | None = None
) -> CompareResult:
    """Side-by-side facts for 2 to 3 models (slugs or names). Capability names may be everyday words;
    each reading is reported. Without capability_names, compares every capability any of them has."""
    args = _validate(CompareInput, slugs=slugs, capability_names=capability_names)
    unresolved: list[Unresolved] = []
    found: list[tuple[str, dict[str, Any], str]] = []
    for term in args.slugs:
        row, matched_by, suggestions = await resolve_model(conn, term)
        if row is None:
            unresolved.append(Unresolved(requirement=None, term=term, part="model",
                                         reason=f"no model matches '{term}'", suggestions=suggestions))  # fmt: skip
        elif any(r["id"] == row["id"] for _, r, _ in found):
            unresolved.append(Unresolved(requirement=None, term=term, part="model",
                                         reason=f"'{term}' is the same model as another entry ({row['slug']})",
                                         suggestions=[]))  # fmt: skip
        else:
            found.append((term, row, matched_by))

    readings: list[CapabilityReading] = []
    names: list[str] | None = None
    if args.capability_names:
        vocab = await load_vocabulary(conn)
        names = []
        for term in args.capability_names:
            res = await resolve_capability(conn, vocab, term)
            if res.status == "resolved":
                readings.append(CapabilityReading(input=term, capability=res.target, confidence=res.confidence))
                if res.target not in names:
                    names.append(res.target)
            else:
                reason = (f"'{term}' could mean " + " or ".join(res.candidates) if res.status == "ambiguous"
                          else f"'{term}' does not match any recorded capability")  # fmt: skip
                unresolved.append(Unresolved(requirement=None, term=term, part="capability", reason=reason,
                                             suggestions=res.candidates))  # fmt: skip

    facts = await _facts_for(conn, [r["id"] for _, r, _ in found], names)
    if names is None:
        names = sorted({f.name for fs in facts.values() for f in fs})
    compared = []
    for term, row, matched_by in found:
        recorded = {f.name: f for f in facts.get(row["id"], [])}
        compared.append(ComparedModel(
            **_summary(row), requested=term, matched_by=matched_by,
            facts=[recorded[n] for n in names if n in recorded],
            not_recorded=[n for n in names if n not in recorded],
        ))  # fmt: skip
    return CompareResult(capability_names=names, capability_interpretation=readings, models=compared,
                         unresolved=unresolved)  # fmt: skip
