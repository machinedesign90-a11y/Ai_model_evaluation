"""Turning people's words into canonical catalog terms.

Order for every lookup: exact canonical term, then exact alias, then trigram
similarity (pg_trgm) at or above a threshold, marked approximate. Two candidates
within AMBIGUITY_MARGIN of each other are reported as ambiguous rather than
guessed. Anything else is unresolved, with the closest suggestions.

Thresholds were measured against the draft vocabulary (supabase/seed/vocabulary_draft.sql)
and are pinned by tests/test_search.py:
- capability names: misspellings scored 0.47 to 0.74; unrelated phrases ("frame rate",
  "lip sync", "watermark", "batch api") at most 0.40. Threshold 0.45.
- values (searched within one capability only): "Hindhi" -> hi scored 0.44, "Englsh" 0.50,
  unrelated words ("Klingon") at most 0.10. Threshold 0.40.
- model names and slugs: 0.40.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from catalog.text import normalize

CAPABILITY_THRESHOLD = 0.45
VALUE_THRESHOLD = 0.40
MODEL_THRESHOLD = 0.40
AMBIGUITY_MARGIN = 0.05
SUGGESTION_FLOOR = 0.15

Confidence = Literal["exact", "alias", "approximate"]

TRUE_WORDS = {"true", "yes", "y", "supports", "supported", "support", "has", "available", "with", "on", "1"}
FALSE_WORDS = {"false", "no", "n", "not", "unsupported", "none", "without", "off", "0", "lacks"}


@dataclass(frozen=True)
class Definition:
    name: str
    label: str
    description: str
    value_type: str
    unit: str | None
    allowed_values: list[Any] | None
    modalities: list[str]

    @property
    def ordered(self) -> bool:
        return self.value_type in ("number", "enum")


@dataclass
class Vocabulary:
    definitions: dict[str, Definition]
    capability_terms: list[tuple[str, str, Literal["name", "alias"]]]  # (normalized term, capability, kind)
    value_terms: dict[str, list[tuple[str, Any, Literal["canonical", "alias"]]]]
    aliases: dict[str, list[str]] = field(default_factory=dict)  # capability -> raw aliases, for display


@dataclass
class Resolution:
    status: Literal["resolved", "ambiguous", "unresolved"]
    target: Any = None
    confidence: Confidence | None = None
    matched_term: str | None = None
    candidates: list[str] = field(default_factory=list)  # for ambiguous / unresolved: suggestions
    note: str | None = None


async def _rows(conn: AsyncConnection, query: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return await cur.fetchall()


async def load_vocabulary(conn: AsyncConnection) -> Vocabulary:
    defs = await _rows(
        conn,
        """select name, label, description, value_type, unit, allowed_values, modalities
           from capability_definitions order by name""",
    )
    definitions = {
        d["name"]: Definition(
            name=d["name"],
            label=d["label"],
            description=d["description"],
            value_type=d["value_type"],
            unit=d["unit"],
            allowed_values=d["allowed_values"],
            modalities=list(d["modalities"] or []),
        )
        for d in defs
    }
    terms: list[tuple[str, str, Literal["name", "alias"]]] = []
    for d in definitions.values():
        terms.append((normalize(d.name), d.name, "name"))
        terms.append((normalize(d.label), d.name, "alias"))
    aliases: dict[str, list[str]] = {}
    for a in await _rows(conn, "select capability_name, alias from capability_aliases order by alias"):
        terms.append((normalize(a["alias"]), a["capability_name"], "alias"))
        aliases.setdefault(a["capability_name"], []).append(a["alias"])

    values: dict[str, list[tuple[str, Any, Literal["canonical", "alias"]]]] = {}
    for d in definitions.values():
        for v in d.allowed_values or []:
            values.setdefault(d.name, []).append((normalize(str(v)), v, "canonical"))
    for a in await _rows(conn, "select capability_name, alias, canonical_value from value_aliases"):
        values.setdefault(a["capability_name"], []).append((normalize(a["alias"]), a["canonical_value"], "alias"))
    return Vocabulary(definitions=definitions, capability_terms=terms, value_terms=values, aliases=aliases)


async def similarities(conn: AsyncConnection, query: str, candidates: list[str]) -> list[float]:
    """pg_trgm similarity of `query` to each candidate, in order."""
    if not candidates:
        return []
    rows = await _rows(
        conn,
        "select similarity(%(q)s, c) as s from unnest(%(c)s::text[]) with ordinality as t(c, i) order by i",
        {"q": query, "c": candidates},
    )
    return [float(r["s"]) for r in rows]


async def _fuzzy(
    conn: AsyncConnection, query: str, entries: list[tuple[str, Any]], threshold: float, label=str
) -> Resolution:
    """Best target by trigram similarity, grouped by target; ambiguity and suggestions included."""
    scores = await similarities(conn, query, [term for term, _ in entries])
    best: dict[str, tuple[float, Any, str]] = {}
    for (term, target), score in zip(entries, scores, strict=True):
        key = label(target)
        if key not in best or score > best[key][0]:
            best[key] = (score, target, term)
    ranked = sorted(best.values(), key=lambda t: (-t[0], label(t[1])))
    suggestions = [label(t[1]) for t in ranked[:3] if t[0] >= SUGGESTION_FLOOR]
    if ranked and ranked[0][0] >= threshold:
        top, rest = ranked[0], [t for t in ranked[1:] if t[0] >= threshold]
        close = [t for t in rest if top[0] - t[0] < AMBIGUITY_MARGIN]
        if close:
            return Resolution("ambiguous", candidates=[label(top[1])] + [label(t[1]) for t in close])
        return Resolution("resolved", top[1], "approximate", matched_term=top[2])
    return Resolution("unresolved", candidates=suggestions)


async def resolve_capability(conn: AsyncConnection, vocab: Vocabulary, term: str) -> Resolution:
    norm = normalize(term)
    if not norm:
        return Resolution("unresolved", note="empty")
    for t, name, kind in vocab.capability_terms:
        if t == norm and kind == "name":
            return Resolution("resolved", name, "exact", matched_term=t)
    for t, name, _kind in vocab.capability_terms:
        if t == norm:
            return Resolution("resolved", name, "alias", matched_term=t)
    return await _fuzzy(conn, norm, [(t, n) for t, n, _ in vocab.capability_terms], CAPABILITY_THRESHOLD)


_NUMBER = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([a-z]*)\s*$")
_UNIT_FACTORS = {
    "seconds": {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600,
                "hours": 3600},
    "tokens": {"": 1, "token": 1, "tokens": 1, "k": 1_000, "m": 1_000_000, "million": 1_000_000},
}  # fmt: skip


def parse_number(text: str, unit: str | None) -> float | None:
    m = _NUMBER.match(text.casefold())
    if not m:
        return None
    number, suffix = float(m.group(1).replace(",", ".")), m.group(2)
    factors = _UNIT_FACTORS.get(unit or "", {"": 1})
    if suffix not in factors:
        return None
    value = number * factors[suffix]
    return int(value) if value.is_integer() else value


async def resolve_value(conn: AsyncConnection, vocab: Vocabulary, d: Definition, value: Any) -> Resolution:
    """Map a user's value onto the canonical value for definition `d`.

    `None` means "any recorded value" (for booleans: true). A resolved target of
    None therefore means "the fact just has to be recorded".
    """
    if value is None:
        if d.value_type == "boolean":
            return Resolution("resolved", True, "exact", note="no value given; read as true")
        return Resolution("resolved", None, "exact", note="no value given; any recorded value counts")

    if d.value_type == "json":
        return Resolution(
            "unresolved",
            note=f"values of {d.name} cannot be compared in search; leave the value out, then read the facts",
        )

    if d.value_type == "boolean":
        if isinstance(value, bool):
            return Resolution("resolved", value, "exact")
        norm = normalize(str(value))
        if norm in TRUE_WORDS:
            return Resolution("resolved", True, "alias", matched_term=norm)
        if norm in FALSE_WORDS:
            return Resolution("resolved", False, "alias", matched_term=norm)
        return Resolution("unresolved", candidates=["true", "false"], note="expected yes or no")

    if d.value_type == "number":
        if isinstance(value, int | float) and not isinstance(value, bool):
            return Resolution("resolved", value, "exact")
        parsed = parse_number(str(value), d.unit)
        if parsed is None:
            unit = f" in {d.unit}" if d.unit else ""
            return Resolution("unresolved", note=f"expected a number{unit}")
        return Resolution("resolved", parsed, "exact" if parsed == value else "alias", matched_term=str(value))

    if d.value_type == "text":
        return Resolution("resolved", str(value), "exact")

    # enum / enum_list
    norm = normalize(str(value))
    entries = vocab.value_terms.get(d.name, [])
    for t, canonical, kind in entries:
        if t == norm and kind == "canonical":
            return Resolution("resolved", canonical, "exact", matched_term=t)
    for t, canonical, _ in entries:
        if t == norm:
            return Resolution("resolved", canonical, "alias", matched_term=t)
    res = await _fuzzy(conn, norm, [(t, c) for t, c, _ in entries], VALUE_THRESHOLD)
    if res.status == "unresolved" and d.allowed_values:
        res.note = "allowed values: " + ", ".join(map(str, d.allowed_values))
    return res


async def resolve_modality(conn: AsyncConnection, modality: str | None) -> tuple[str | None, str | None]:
    """Return (modality to filter on or None for all, note for the caller)."""
    if modality is None or not normalize(modality):
        return None, None
    known = [r["m"] for r in await _rows(conn, "select distinct modality as m from models order by 1")]
    norm = normalize(modality)
    for m in known:
        if normalize(m) in (norm, norm.removesuffix("s")):
            return m, None if normalize(m) == norm else f"'{modality}' read as modality '{m}'"
    res = await _fuzzy(conn, norm, [(normalize(m), m) for m in known], CAPABILITY_THRESHOLD)
    if res.status == "resolved":
        return res.target, f"'{modality}' read as modality '{res.target}' (approximate)"
    listed = ", ".join(known) or "none yet"
    return None, f"modality '{modality}' is not recorded; searched all modalities (recorded: {listed})"


async def resolve_model(conn: AsyncConnection, term: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Find a model by slug, then name, then trigram. Returns (row, matched_by, suggestions)."""
    rows = await _rows(conn, "select id, slug, name, provider, modality, description from models")
    raw = term.strip().lower()
    for r in rows:
        if r["slug"] == raw:
            return r, "slug", []
    norm = normalize(term)
    for r in rows:
        if normalize(r["name"]) == norm or normalize(r["slug"]) == norm:
            return r, "name", []
    if not norm:
        return None, None, []
    entries = [(normalize(r["slug"]), r["slug"]) for r in rows] + [(normalize(r["name"]), r["slug"]) for r in rows]
    res = await _fuzzy(conn, norm, entries, MODEL_THRESHOLD)
    if res.status == "resolved":
        return next(r for r in rows if r["slug"] == res.target), "approximate", []
    return None, None, res.candidates
