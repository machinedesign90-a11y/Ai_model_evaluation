"""Catalog tools. Strict data, forgiving search: people's words are translated into canonical terms
(see catalog/vocabulary.py), every translation is reported, and nothing unrecognised is dropped.

Every value from the caller reaches SQL as a bound parameter; the SQL text is static.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import ValidationError

from catalog.phrases import parse_sort, take_number, take_operator
from catalog.schemas import (
    Ambiguous,
    CapabilityInfo,
    CapabilityReading,
    ComparedModel,
    CompareInput,
    CompareResult,
    Fact,
    FindVerifiedQueryResult,
    GetModelResult,
    Interpretation,
    ListCapabilitiesResult,
    ModelDetail,
    ModelMatch,
    ModelSummary,
    QuerySpec,
    RequirementResult,
    RunVerifiedQueryResult,
    SearchResult,
    SlotInfo,
    SlotUse,
    SortReading,
    SortSpec,
    SpecEntry,
    Unresolved,
    VerifiedQueryCandidate,
    VerifiedQueryRef,
)
from catalog.text import normalize
from catalog.vocabulary import (
    Definition,
    Resolution,
    Vocabulary,
    convert_number,
    load_vocabulary,
    lookup_value_everywhere,
    resolve_capability,
    resolve_modality,
    resolve_model,
    resolve_value,
    units_matching,
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


# --- reading one filter or measure ------------------------------------------------------------

_EDGE_WORDS = {"in", "with", "for", "of", "and", "a", "an", "the", "to", "that", "which", "can", "do", "does"}
_HOW = {"exact": "", "alias": " (alias match)", "approximate": " (approximate match)"}
_WEAKEST = {"exact": 0, "alias": 1, "approximate": 2}


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


async def _split_embedded_value(
    conn: AsyncConnection, vocab: Vocabulary, phrase: str, modality: str | None
) -> tuple[str, Any, str | None]:
    """Take a value written into the capability phrase ("Hindi voiceover", "speech_languages hi"): a word that is
    a known value of the capability the rest of the phrase names. Returns (phrase, value or None, note)."""
    tokens = normalize(phrase).split()
    for n in (3, 2, 1):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            owners = {cap for cap, entries in vocab.value_terms.items() if any(t == gram for t, _, _ in entries)}
            if not owners:
                continue
            rest = _strip_edges(tokens[:i] + tokens[i + n :])
            if not rest:
                continue
            res = await resolve_capability(conn, vocab, " ".join(rest), modality)
            if res.status == "resolved" and res.target in owners:
                return " ".join(rest), gram, f"took '{gram}' as the value"
    return phrase, None, None


def _describe(d: Definition, operator: str | None, value: Any) -> str:
    if value is None:
        return f"{d.name} recorded"
    unit = f" {d.unit}" if d.unit and d.value_type == "number" else ""
    shown = value if not isinstance(value, bool) else str(value).lower()
    verb = "is" if operator == "equals" else (operator or "contains").replace("_", " ")
    return f"{d.name} {verb} {shown}{unit}"


def _default_operator(d: Definition) -> str:
    if d.kind == "measure":
        return "at_most" if d.better == "lower" else "at_least"
    return "contains" if d.value_type == "enum_list" else "equals"


class _Reading:
    """Collects the outcome of reading one entry."""

    def __init__(self, ref: str, entry: SpecEntry) -> None:
        self.ref, self.entry = ref, entry
        self.notes: list[str] = []
        self.unresolved: list[Unresolved] = []
        self.ambiguous: list[Ambiguous] = []

    def words(self) -> str:
        parts = [f"'{x}'" for x in (self.entry.capability, self.entry.value) if x is not None]
        return " / ".join(parts)

    def interp(self, status, d: Definition | None = None, operator=None, value=None, confidence=None, note=""):
        return Interpretation(
            requirement=self.ref, input_capability=self.entry.capability, input_value=self.entry.value,
            status=status, kind=d.kind if d else None, capability=d.name if d else None,
            capability_label=d.label if d else None, operator=operator, value=value,
            importance=self.entry.importance, confidence=confidence, note=note,
        )  # fmt: skip

    def fail(self, part: str, term: str, reason: str, suggestions=(), d: Definition | None = None):
        self.unresolved.append(Unresolved(requirement=self.ref, term=term, part=part, reason=reason,
                                          suggestions=[str(x) for x in suggestions]))  # fmt: skip
        return self.interp("unresolved", d, note=reason), None

    def unclear(self, part: str, term: str, candidates: list[str]):
        reason = f"'{term}' could mean " + " or ".join(candidates)
        self.ambiguous.append(Ambiguous(requirement=self.ref, term=term, part=part, candidates=candidates))
        return self.interp("ambiguous", note=reason), None


async def _read_entry(
    conn: AsyncConnection, vocab: Vocabulary, ref: str, listed_as: str, entry: SpecEntry, modality: str | None
) -> tuple[Interpretation, dict[str, Any] | None, _Reading]:
    """Interpret one filter or measure. Returns (interpretation, executable plan or None, reading)."""
    r = _Reading(ref, entry)
    operator = entry.operator
    value: Any = entry.value
    number: tuple[float, str] | None = None  # (number, unit word) parsed from the user's words
    cap: Resolution | None = None
    d: Definition | None = None

    # Comparison words and numbers can sit in either field: "longer than 10 seconds", value "at least 4K".
    if isinstance(value, str):
        rest, op = take_operator(value)
        operator = operator or op
        value = rest.strip() or None
    phrase = entry.capability
    raw_phrase = phrase  # kept un-normalized for number parsing ("$0.30" needs its "." and "$")
    if phrase is not None:
        raw_phrase, op = take_operator(phrase)
        operator = operator or op
        phrase = normalize(raw_phrase)

    if phrase:
        split_note = None
        if value is None:
            # Order: an exact capability word ("audio", "closed source") beats an exact value word ("4K",
            # "Hindi", "not deprecated"), which beats a number ("10 seconds"), which beats a value inside the
            # phrase ("Hindi voiceover").
            exact_cap = await resolve_capability(conn, vocab, phrase, modality)
            if exact_cap.status == "resolved" and exact_cap.confidence in ("exact", "alias"):
                pass
            elif hits := lookup_value_everywhere(vocab, phrase, modality):
                if len(hits) > 1:
                    return (*r.unclear("capability", entry.capability, [h[0] for h in hits]), r)
                cap = Resolution("resolved", hits[0][0], "alias")
                value = hits[0][1]
                r.notes.append(f"'{entry.capability}' read as a value of {hits[0][0]}")
                phrase = ""
            elif (parsed := take_number(raw_phrase)).number is not None:
                number, phrase = (parsed.number, parsed.unit_word), parsed.words
            else:
                phrase, value, split_note = await _split_embedded_value(conn, vocab, phrase, modality)
        elif isinstance(value, str):
            tokens = _remove_run(phrase.split(), normalize(value).split())
            if tokens and _strip_edges(tokens):
                phrase = " ".join(_strip_edges(tokens))
        if split_note:
            r.notes.append(split_note)
        if phrase:
            cap = await resolve_capability(conn, vocab, phrase, modality)
            if cap.status == "resolved" and value is None and number is None:
                # A phrase that is both a capability and a value: "closed source" -> open_weights = false.
                own = [c for t, c, _ in vocab.value_terms.get(cap.target, []) if t == phrase]
                if own:
                    value = own[0]
                    r.notes.append(f"'{entry.capability}' also gives the value")
            elif cap.status != "resolved" and value is None and number is None:
                hits = lookup_value_everywhere(vocab, phrase, modality)
                if len(hits) == 1:
                    cap = Resolution("resolved", hits[0][0], "alias")
                    value = hits[0][1]
                    r.notes.append(f"'{entry.capability}' read as a value of {hits[0][0]}")
                elif len(hits) > 1:
                    return (*r.unclear("capability", entry.capability, [h[0] for h in hits]), r)
    elif value is not None:
        # No capability given: find which capability the value belongs to ("Hindi", "4K", "20 seconds").
        hits = lookup_value_everywhere(vocab, str(value), modality) if not isinstance(value, bool) else []
        if len(hits) == 1:
            cap = Resolution("resolved", hits[0][0], "alias")
            value = hits[0][1]
            r.notes.append(f"'{entry.value}' read as a value of {hits[0][0]}")
        elif len(hits) > 1:
            return (*r.unclear("value", str(entry.value), [h[0] for h in hits]), r)
        elif not isinstance(value, bool):
            parsed = take_number(str(value))
            if parsed.number is not None and not parsed.words:
                number, value = (parsed.number, parsed.unit_word), None

    if cap is None and number is not None:
        names = units_matching(vocab, number[1], modality)
        if len(names) == 1:
            cap = Resolution("resolved", names[0], "alias")
            r.notes.append(f"capability taken from the unit '{number[1]}'")
        elif len(names) > 1:
            return (*r.unclear("capability", r.words() or str(number[0]), names), r)

    if cap is None or cap.status == "unresolved":
        term = entry.capability or str(entry.value)
        suggestions = cap.candidates if cap else []
        return (*r.fail("capability", term, f"'{term}' does not match any recorded capability", suggestions), r)
    if cap.status == "ambiguous":
        return (*r.unclear("capability", entry.capability or str(entry.value), cap.candidates), r)

    d = vocab.definitions[cap.target]
    if number is not None:
        converted = convert_number(number[0], number[1], d.unit) if d.value_type == "number" else None
        if converted is None:
            unit = f"{number[0]:g} {number[1]}".strip()
            return (*r.fail("value", unit, f"'{unit}' does not fit {d.name} (unit: {d.unit or 'none'})", d=d), r)
        val = Resolution("resolved", converted, "alias")
    else:
        val = await resolve_value(conn, vocab, d, value)
    if val.status == "ambiguous":
        return (*r.unclear("value", str(value), [str(c) for c in val.candidates]), r)
    if val.status != "resolved":
        reason = val.note or f"'{value}' is not a recorded value of {d.name}"
        return (*r.fail("value", str(value), reason, val.candidates, d=d), r)

    if d.kind != listed_as.removesuffix("s"):
        r.notes.append(f"moved from {listed_as} to {d.kind}s")
    if val.target is None:
        operator = None
    else:
        if not d.operators:
            return (*r.fail("operator", d.name, f"{d.name} is display only and cannot be searched", d=d), r)
        operator = operator or _default_operator(d)
        if operator == "contains" and d.value_type != "enum_list" and "equals" in d.operators:
            operator = "equals"
        elif operator == "equals" and d.value_type == "enum_list" and "contains" in d.operators:
            operator = "contains"
        if operator not in d.operators:
            reason = f"{operator} does not apply to {d.name}; use " + " or ".join(d.operators)
            return (*r.fail("operator", operator, reason, d.operators, d=d), r)
        if entry.operator is None and operator in ("at_least", "at_most"):
            r.notes.append("read as a minimum" if operator == "at_least" else "read as a maximum")
    if val.note:
        r.notes.append(val.note)

    confidence = max((c for c in (cap.confidence, val.confidence) if c), key=_WEAKEST.__getitem__)
    label = _describe(d, operator, val.target)
    note = f"{r.words()} read as {label}{_HOW[confidence]}" + ("; " + "; ".join(r.notes) if r.notes else "")
    interp = r.interp("resolved", d, operator, val.target, confidence, note)
    plan = {"ref": ref, "definition": d, "operator": operator, "value": val.target,
            "importance": entry.importance, "label": label}  # fmt: skip
    return interp, plan, r


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
        have_k, want_k = _order_key(d, have), _order_key(d, want)
        if have_k is None or want_k is None:
            return "not_satisfied"
        ok = have_k >= want_k if op == "at_least" else have_k <= want_k
    else:
        ok = have == want
    return "satisfied" if ok else "not_satisfied"


def _order_key(d: Definition, value: Any) -> float | None:
    if d.value_type == "enum":
        order = d.allowed_values or []
        return float(order.index(value)) if value in order else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _closeness(plan: dict[str, Any], fact: Fact | None) -> float:
    """For a missed at_least / at_most requirement: how near the recorded value came (0 to 1)."""
    if fact is None or plan["operator"] not in ("at_least", "at_most"):
        return 0.0
    d: Definition = plan["definition"]
    have, want = _order_key(d, fact.value), _order_key(d, plan["value"])
    if have is None or want is None:
        return 0.0
    if d.value_type == "enum":
        have, want = have + 1, want + 1
    if have <= 0 or want <= 0:
        return 0.0
    ratio = have / want if plan["operator"] == "at_least" else want / have
    return max(0.0, min(ratio, 1.0))


# --- reading the sort --------------------------------------------------------------------------


async def _read_sort(
    conn: AsyncConnection, vocab: Vocabulary, sort: SortSpec, modality: str | None
) -> tuple[SortReading | None, Definition | None, list[Unresolved], list[Ambiguous]]:
    words, word_direction, is_price = parse_sort(sort.capability)
    res: Resolution
    if is_price:
        prices = [d.name for d in vocab.definitions.values() if d.kind == "measure" and d.is_price]
        if words:
            narrowed = await resolve_capability(conn, vocab, words, modality)
            if narrowed.status == "resolved" and narrowed.target in prices:
                prices = [narrowed.target]
        picked = vocab.prefer_for_modality(prices, modality)
        if len(picked) == 1:
            res = Resolution("resolved", picked[0], "alias")
        else:
            res = Resolution("ambiguous", candidates=picked)
    else:
        res = await resolve_capability(conn, vocab, sort.capability, modality)
        if res.status != "resolved" and words and words != normalize(sort.capability):
            res = await resolve_capability(conn, vocab, words, modality)
        if res.status != "resolved" and not words and word_direction:
            seconds = units_matching(vocab, "seconds", modality)
            if normalize(sort.capability) in ("longest", "shortest") and len(seconds) == 1:
                res = Resolution("resolved", seconds[0], "alias")
    if res.status == "ambiguous":
        return None, None, [], [Ambiguous(requirement="sort", term=sort.capability, part="capability",
                                          candidates=res.candidates)]  # fmt: skip
    if res.status != "resolved":
        return None, None, [Unresolved(requirement="sort", term=sort.capability, part="sort",
                                       reason=f"'{sort.capability}' does not match a measure to sort by",
                                       suggestions=res.candidates)], []  # fmt: skip
    d = vocab.definitions[res.target]
    if d.kind != "measure":
        return None, None, [Unresolved(requirement="sort", term=sort.capability, part="sort",
                                       reason=f"{d.name} is a filter; only measures can be sorted",
                                       suggestions=[])], []  # fmt: skip
    wanted = sort.direction if sort.direction != "best" or word_direction is None else word_direction
    note_extra = ""
    if wanted in ("best", "worst"):
        if d.better is None:
            direction, note_extra = "desc", f"; {d.name} has no better direction, so highest first"
        else:
            best_is_desc = d.better == "higher"
            direction = "desc" if best_is_desc == (wanted == "best") else "asc"
    else:
        direction = wanted
    order = "highest first" if direction == "desc" else "lowest first"
    note = f"'{sort.capability}' read as sort by {d.name}, {order}; not recorded values last{note_extra}"
    return SortReading(capability=d.name, direction=direction, note=note), d, [], []


# --- search_models -----------------------------------------------------------------------------


async def search_models(
    conn: AsyncConnection,
    modality: str | None = None,
    filters: list[SpecEntry | dict[str, Any]] | None = None,
    measures: list[SpecEntry | dict[str, Any]] | None = None,
    sort: SortSpec | dict[str, Any] | None = None,
    limit: int = 10,
) -> SearchResult:
    """Run a query spec over the catalog. Terms may be the user's own words.

    Each filter and measure is translated into canonical terms and reported in `interpretation` (an entry in
    the wrong list is moved and noted). Per model and requirement the status is satisfied, not_satisfied or
    not_recorded (absence of a fact is never a "no"). Models are grouped by their 'must' requirements: full
    (all satisfied), close (none contradicted, some not recorded), partial (some not satisfied). 'nice'
    requirements only affect ranking; among partial matches, nearer misses rank first. With a sort, models
    with no recorded value for the sort measure come last. Never empty when the modality has models.
    """
    spec = _validate(QuerySpec, modality=modality, filters=filters or [], measures=measures or [], sort=sort,
                     limit=limit)  # fmt: skip
    vocab = await load_vocabulary(conn)
    searched, modality_note = await resolve_modality(conn, vocab, spec.modality)
    unresolved: list[Unresolved] = []
    ambiguous: list[Ambiguous] = []
    if modality_note and searched is None:
        unresolved.append(Unresolved(requirement=None, term=spec.modality, part="modality", reason=modality_note,
                                     suggestions=sorted(vocab.modalities())))  # fmt: skip

    interpretation, plans = [], []
    entries = [("filters", i, e) for i, e in enumerate(spec.filters)]
    entries += [("measures", i, e) for i, e in enumerate(spec.measures)]
    for listed_as, i, entry in entries:
        interp, plan, reading = await _read_entry(conn, vocab, f"{listed_as}[{i}]", listed_as, entry, searched)
        interpretation.append(interp)
        unresolved.extend(reading.unresolved)
        ambiguous.extend(reading.ambiguous)
        if plan:
            plans.append(plan)

    sort_reading, sort_def = None, None
    if spec.sort is not None:
        sort_reading, sort_def, su, sa = await _read_sort(conn, vocab, spec.sort, searched)
        unresolved.extend(su)
        ambiguous.extend(sa)

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
    names = sorted({p["definition"].name for p in plans} | ({sort_def.name} if sort_def else set()))
    facts = await _facts_for(conn, ids, names) if names else {}

    full, close, partial = [], [], []
    for m in models:
        by_name = {f.name: f for f in facts.get(m["id"], [])}
        results, met, missed, unknown = [], [], [], []
        score = [0, 0, 0, 0]  # must met, nice met, -must missed, -must not recorded
        nearness = 0.0
        for p in plans:
            fact = by_name.get(p["definition"].name)
            status = _evaluate(p, fact)
            results.append(RequirementResult(requirement=p["ref"], capability=p["definition"].name,
                                             status=status, fact=fact))  # fmt: skip
            must = p["importance"] == "must"
            {"satisfied": met, "not_satisfied": missed, "not_recorded": unknown}[status].append(p["label"])
            if status == "satisfied":
                score[0 if must else 1] += 1
            elif must:
                score[2 if status == "not_satisfied" else 3] -= 1
            if status == "not_satisfied":
                nearness += _closeness(p, fact)
        verified = sum(x.fact is not None and x.fact.verification_status == "verified" for x in results)
        sort_fact = by_name.get(sort_def.name) if sort_def else None
        sort_key: tuple = ()
        if sort_def:
            k = _order_key(sort_def, sort_fact.value) if sort_fact else None
            sort_key = (k is None, 0.0 if k is None else (-k if sort_reading.direction == "desc" else k))
        rank = (-score[0], -score[1], -score[2], -score[3], -nearness, -verified, m["name"])
        entry = ModelMatch(**_summary(m), results=results, met=met, missed=missed, not_recorded=unknown,
                           sort_fact=sort_fact)  # fmt: skip
        if score[2] == 0 and score[3] == 0:
            full.append((sort_key + rank, entry))
        elif score[2] == 0:
            close.append((sort_key + rank, entry))
        else:
            partial.append((rank[:5] + sort_key + rank[5:], entry))

    def top(items):
        return [e for _, e in sorted(items, key=lambda t: t[0])[: spec.limit]]

    asked = bool(spec.filters or spec.measures)
    nothing_read = asked and not plans and sort_def is None
    return SearchResult(
        searched_modality=searched,
        modality_note=modality_note,
        total_models_in_modality=len(models),
        models_with_any_recorded_fact=with_facts[0]["n"],
        interpretation=interpretation,
        unresolved=unresolved,
        ambiguous=ambiguous,
        sort=sort_reading,
        full_matches=[] if nothing_read else top(full),
        close_matches=top(close),
        partial_matches=top(partial),
        total_full_matches=0 if nothing_read else len(full),
        total_close_matches=len(close),
        total_partial_matches=len(partial),
        other_models=[ModelSummary(**_summary(m)) for m in models[: spec.limit]] if nothing_read else [],
    )


# --- verified queries (tier 1) -----------------------------------------------------------------

_SLOT = re.compile(r"^\{\{\s*([a-z][a-z0-9_]*)\s*\}\}$")


def _slot_name(value: Any) -> str | None:
    m = _SLOT.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def _valid_canonical(d: Definition, value: Any) -> bool:
    if d.value_type == "boolean":
        return isinstance(value, bool)
    if d.value_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if d.value_type in ("enum", "enum_list"):
        return value in (d.allowed_values or [])
    if d.value_type == "text":
        return isinstance(value, str)
    return False


def validate_verified_spec(vocab: Vocabulary, spec: Any, slots: Any) -> list[str]:
    """Problems with a verified query's spec against the current vocabulary (empty list means valid).

    Stricter than search: capabilities must be canonical names, operators allowed for them, entries in the
    right list, values canonical or a declared "{{slot}}", and every declared slot used.
    """
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["query_spec must be an object"]
    if not isinstance(slots, list):
        return ["slots must be a list"]
    extra = set(spec) - {"modality", "filters", "measures", "sort", "limit"}
    if extra:
        problems.append(f"unknown keys: {sorted(extra)}")
    declared = {}
    for s in slots:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            problems.append(f"bad slot: {s!r}")
            continue
        declared[s["name"]] = s
        cap = s.get("capability")
        if cap is not None and cap not in vocab.definitions:
            problems.append(f"slot {s['name']}: unknown capability {cap}")
        elif (
            cap is not None
            and s.get("default") is not None
            and not _valid_canonical(vocab.definitions[cap], s["default"])
        ):
            problems.append(f"slot {s['name']}: default {s['default']!r} is not a valid {cap} value")
    used: set[str] = set()

    modality = spec.get("modality")
    if (name := _slot_name(modality)) is not None:
        used.add(name)
        default = declared.get(name, {}).get("default")
        if default is not None and default not in vocab.modalities():
            problems.append(f"slot {name}: default modality {default!r} is not known")
    elif modality is not None and modality not in vocab.modalities():
        problems.append(f"unknown modality {modality!r}")

    for listed_as in ("filters", "measures"):
        entries = spec.get(listed_as, [])
        if not isinstance(entries, list):
            problems.append(f"{listed_as} must be a list")
            continue
        for i, e in enumerate(entries):
            ref = f"{listed_as}[{i}]"
            if not isinstance(e, dict):
                problems.append(f"{ref}: must be an object")
                continue
            d = vocab.definitions.get(e.get("capability"))
            if d is None:
                problems.append(f"{ref}: unknown capability {e.get('capability')!r}")
                continue
            if d.kind != listed_as.removesuffix("s"):
                problems.append(f"{ref}: {d.name} is a {d.kind}, not a {listed_as.removesuffix('s')}")
            if e.get("operator") not in d.operators:
                problems.append(f"{ref}: operator {e.get('operator')!r} not allowed for {d.name} ({d.operators})")
            if e.get("importance", "must") not in ("must", "nice"):
                problems.append(f"{ref}: importance must be must or nice")
            value = e.get("value")
            if (name := _slot_name(value)) is not None:
                used.add(name)
                slot = declared.get(name)
                if slot is not None and slot.get("capability") not in (None, d.name):
                    problems.append(f"{ref}: slot {name} is for {slot.get('capability')}, not {d.name}")
            elif not _valid_canonical(d, value):
                problems.append(f"{ref}: {value!r} is not a valid {d.name} value")

    sort = spec.get("sort")
    if sort is not None:
        d = vocab.definitions.get(sort.get("capability")) if isinstance(sort, dict) else None
        if d is None or d.kind != "measure":
            problems.append(f"sort: {sort!r} must name a measure")
        if isinstance(sort, dict) and sort.get("direction", "best") not in ("best", "worst", "asc", "desc"):
            problems.append(f"sort: bad direction {sort.get('direction')!r}")
    limit = spec.get("limit", 10)
    if not isinstance(limit, int) or not 1 <= limit <= 25:
        problems.append("limit must be 1 to 25")

    for name in sorted(used - set(declared)):
        problems.append(f"slot {name} is used but not declared")
    for name in sorted(set(declared) - used):
        problems.append(f"slot {name} is declared but not used")
    return problems


def _slot_infos(slots: list[dict[str, Any]]) -> list[SlotInfo]:
    return [
        SlotInfo(
            name=s["name"],
            capability=s.get("capability"),
            required=bool(s.get("required", False)),
            default=s.get("default"),
            description=s.get("description"),
        )  # fmt: skip
        for s in slots
    ]


async def find_verified_query(
    conn: AsyncConnection, question: str, floor: float = 0.15, limit: int = 3
) -> FindVerifiedQueryResult:
    """Up to 3 verified queries whose question or paraphrases look like the user's question, best first.

    Score: the mean of pg_trgm similarity and word_similarity against each paraphrase and the canonical
    question, best text per entry. Measured on tests/tier1_paraphrases.py (34 paraphrases): the mean put the
    right entry first 28 times and in the top 3 every time; "the higher of the two" got 23 and 31 because
    word_similarity saturates on common words such as "video model". Scores are candidates for the model to
    judge, not a decision: never auto-run. Drafts and retired entries are never returned.
    """
    if not isinstance(question, str) or len(question) > 2000:
        raise CatalogInputError("question: up to 2000 characters")
    if not normalize(question):
        return FindVerifiedQueryResult(candidates=[])
    rows = await _fetch(
        conn,
        """with q as (select %(q)s::text as q),
           texts as (
             select v.id, lower(v.question) as t from verified_queries v where v.status = 'verified'
             union all
             select v.id, lower(p.phrase) from verified_queries v
               join verified_query_phrases p on p.verified_query_id = v.id
              where v.status = 'verified'
           ),
           scored as (
             select t.id, max((similarity(q.q, t.t) + word_similarity(q.q, t.t)) / 2) as score
               from texts t cross join q group by t.id
           )
           select v.id::text as id, v.title, v.question, v.slots, s.score
             from scored s join verified_queries v on v.id = s.id
            where s.score >= %(floor)s
            order by s.score desc, v.title
            limit %(limit)s""",
        {"q": normalize(question), "floor": floor, "limit": limit},
    )
    return FindVerifiedQueryResult(
        candidates=[
            VerifiedQueryCandidate(
                id=r["id"],
                title=r["title"],
                question=r["question"],
                score=round(float(r["score"]), 3),
                slots=_slot_infos(r["slots"]),
            )  # fmt: skip
            for r in rows
        ]
    )


def _substitute(node: Any, values: dict[str, Any], missing: set[str]) -> Any:
    if isinstance(node, dict):
        return {k: _substitute(v, values, missing) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values, missing) for v in node]
    name = _slot_name(node)
    if name is None:
        return node
    if name in values:
        return values[name]
    missing.add(name)
    return node


def _uses_missing(entry: dict[str, Any], missing: set[str]) -> bool:
    return _slot_name(entry.get("value")) in missing


async def run_verified_query(
    conn: AsyncConnection, id: str, slots: dict[str, Any] | None = None, *, record_match: bool = True
) -> RunVerifiedQueryResult:
    """Run a verified query with its slots filled from the user's words (resolved like any search term).

    A slot left empty takes its default. A required slot with no value and no default is reported in
    `unresolved` and its requirement is left out, so the answer is never a dead end. Only status 'verified'
    runs. `record_match` updates times_matched (skip it on read-only connections).
    """
    try:
        qid = uuid.UUID(str(id))
    except ValueError:
        raise CatalogInputError("id: not a verified query id") from None
    if slots is not None and (not isinstance(slots, dict) or len(slots) > 20):
        raise CatalogInputError("slots: an object of slot name to value")
    rows = await _fetch(
        conn,
        """select id::text as id, title, query_spec, slots from verified_queries
           where id = %(id)s and status = 'verified'""",
        {"id": qid},
    )
    if not rows:
        raise CatalogInputError("id: no verified query with that id")
    row = rows[0]
    vocab = await load_vocabulary(conn)
    problems = validate_verified_spec(vocab, row["query_spec"], row["slots"])
    if problems:
        raise CatalogInputError("this verified query no longer fits the vocabulary: " + "; ".join(problems))

    given = {k: v for k, v in (slots or {}).items() if v not in (None, "")}
    declared = {s["name"]: s for s in row["slots"]}
    values, used, extra_unresolved = {}, [], []
    for name in sorted(set(given) - set(declared)):
        extra_unresolved.append(Unresolved(requirement=None, term=name, part="slot",
                                           reason=f"this query has no slot '{name}'",
                                           suggestions=sorted(declared)))  # fmt: skip
    for name, s in declared.items():
        if name in given:
            values[name] = given[name]
            used.append(SlotUse(name=name, value=given[name], source="user"))
        elif s.get("default") is not None:
            values[name] = s["default"]
            used.append(SlotUse(name=name, value=s["default"], source="default"))
        else:
            used.append(SlotUse(name=name, value=None, source="missing"))
            extra_unresolved.append(Unresolved(requirement=None, term=name, part="slot",
                                               reason=f"no value for '{name}' ({s.get('description') or 'required'})",
                                               suggestions=[]))  # fmt: skip
    missing: set[str] = set()
    spec = dict(row["query_spec"])
    spec["filters"] = [e for e in spec.get("filters", []) if not _uses_missing(e, set(declared) - set(values))]
    spec["measures"] = [e for e in spec.get("measures", []) if not _uses_missing(e, set(declared) - set(values))]
    spec = _substitute(spec, values, missing)
    if _slot_name(spec.get("modality")):
        spec["modality"] = None

    result = await search_models(conn, **spec)
    if record_match:
        await conn.execute(
            "update verified_queries set times_matched = times_matched + 1, last_matched_at = now() where id = %s",
            (qid,),
        )
    data = result.model_dump()
    data["unresolved"] = [u.model_dump() for u in extra_unresolved] + data["unresolved"]
    return RunVerifiedQueryResult(**data, verified_query=VerifiedQueryRef(id=row["id"], title=row["title"]),
                                  slots_used=used)  # fmt: skip


# --- list_capabilities -------------------------------------------------------------------------


async def list_capabilities(conn: AsyncConnection, modality: str | None = None) -> ListCapabilitiesResult:
    """The semantic layer: every measure and filter, with operators, better direction, unit, allowed values and
    example wording."""
    if modality is not None and len(modality) > 50:
        raise CatalogInputError("modality: at most 50 characters")
    vocab = await load_vocabulary(conn)
    searched, note = await resolve_modality(conn, vocab, modality)
    caps = [
        CapabilityInfo(
            name=d.name,
            label=d.label,
            description=d.description,
            kind=d.kind,
            value_type=d.value_type,
            operators=d.operators,
            better=d.better,
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
    recorded fact (prices and api_available included) and its provenance. The description is display
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
        model_modalities = {r["modality"] for _, r, _ in found}
        modality = next(iter(model_modalities)) if len(model_modalities) == 1 else None
        names = []
        for term in args.capability_names:
            res = await resolve_capability(conn, vocab, term, modality)
            if res.status == "ambiguous" and res.note:  # a shared alias ("price"): show each fitting capability
                fitting = [
                    c for c in res.candidates if any(vocab.definitions[c].applies_to(m) for m in model_modalities)
                ]
                res = Resolution("resolved", fitting or res.candidates, "alias")
            if res.status == "resolved":
                for target in res.target if isinstance(res.target, list) else [res.target]:
                    readings.append(CapabilityReading(input=term, capability=target, confidence=res.confidence))
                    if target not in names:
                        names.append(target)
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
