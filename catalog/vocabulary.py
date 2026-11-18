"""Turning people's words into canonical catalog terms (the semantic layer).

Order for every lookup: exact canonical term, then exact alias, then trigram
similarity (pg_trgm) at or above a threshold, marked approximate. An alias shared by
several capabilities (for example "price") keeps those that fit the searched modality,
preferring capabilities tagged with that modality over ones that apply to every modality.
Two candidates still left, or within AMBIGUITY_MARGIN by similarity, are ambiguous.
Anything else is unresolved, with the closest suggestions.

Thresholds were measured against the draft vocabulary and are pinned by tests/test_search.py:
- capability names: misspellings scored 0.47 to 0.74; unrelated phrases ("frame rate",
  "lip sync", "watermark", "batch api") at most 0.40. Threshold 0.45.
- values (searched within one capability only): "Hindhi" -> hi scored 0.44, "Englsh" 0.50,
  unrelated words ("Klingon") at most 0.10. Threshold 0.40.
- model names and slugs: 0.40.
"""

from __future__ import annotations

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
    kind: Literal["measure", "filter"]
    operators: list[str]
    better: Literal["higher", "lower"] | None

    def applies_to(self, modality: str | None) -> bool:
        return modality is None or not self.modalities or modality in self.modalities

    @property
    def is_price(self) -> bool:
        return bool(self.unit) and self.unit.upper().startswith("USD")


@dataclass
class Vocabulary:
    definitions: dict[str, Definition]
    capability_terms: list[tuple[str, str, Literal["name", "alias"]]]  # (normalized term, capability, kind)
    value_terms: dict[str, list[tuple[str, Any, Literal["canonical", "alias"]]]]
    aliases: dict[str, list[str]] = field(default_factory=dict)  # capability -> raw aliases, for display

    def modalities(self) -> set[str]:
        return {m for d in self.definitions.values() for m in d.modalities}

    def prefer_for_modality(self, names: list[str], modality: str | None) -> list[str]:
        """Of several capabilities, keep those that fit the modality; tagged ones beat generic ones."""
        names = list(dict.fromkeys(names))
        if modality is None:
            return names
        fitting = [n for n in names if self.definitions[n].applies_to(modality)]
        tagged = [n for n in fitting if modality in self.definitions[n].modalities]
        return tagged or fitting or names


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
        """select name, label, description, value_type, unit, allowed_values, modalities, kind, operators, better
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
            kind=d["kind"],
            operators=list(d["operators"] or []),
            better=d["better"],
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


async def resolve_capability(
    conn: AsyncConnection, vocab: Vocabulary, term: str, modality: str | None = None
) -> Resolution:
    norm = normalize(term)
    if not norm:
        return Resolution("unresolved", note="empty")
    for t, name, kind in vocab.capability_terms:
        if t == norm and kind == "name":
            return Resolution("resolved", name, "exact", matched_term=t)
    hits = vocab.prefer_for_modality([name for t, name, _ in vocab.capability_terms if t == norm], modality)
    if len(hits) == 1:
        return Resolution("resolved", hits[0], "alias", matched_term=norm)
    if hits:
        return Resolution("ambiguous", candidates=hits, note="the word is used for several capabilities")
    fitting = [(t, n) for t, n, _ in vocab.capability_terms if vocab.definitions[n].applies_to(modality)]
    res = await _fuzzy(conn, norm, fitting, CAPABILITY_THRESHOLD)
    if res.status == "unresolved" and modality is not None:
        res = await _fuzzy(conn, norm, [(t, n) for t, n, _ in vocab.capability_terms], CAPABILITY_THRESHOLD)
    return res


def lookup_value_everywhere(vocab: Vocabulary, phrase: str, modality: str | None) -> list[tuple[str, Any]]:
    """Exact value or value alias across all capabilities ("Hindi" -> speech_languages "hi")."""
    norm = normalize(phrase)
    hits: dict[str, Any] = {}
    for cap, entries in vocab.value_terms.items():
        for t, canonical, _ in entries:
            if t == norm and cap not in hits:
                hits[cap] = canonical
    keep = vocab.prefer_for_modality(list(hits), modality) if hits else []
    return [(c, hits[c]) for c in keep]


_UNIT_FACTORS: dict[str, dict[str, float]] = {
    "seconds": {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600,
                "hours": 3600},
    "tokens": {"": 1, "token": 1, "tokens": 1, "k": 1_000, "m": 1_000_000, "million": 1_000_000},
    "megapixels": {"": 1, "mp": 1, "megapixel": 1, "megapixels": 1},
    "billion parameters": {"": 1, "b": 1, "billion": 1, "parameters": 1, "parameter": 1, "params": 1},
}  # fmt: skip
_USD = {"": 1, "usd": 1, "dollar": 1, "dollars": 1, "$": 1, "cents": 0.01, "cent": 0.01}


def unit_factor(unit_word: str, unit: str | None) -> float | None:
    if unit and unit.upper().startswith("USD"):
        return _USD.get(unit_word)
    return _UNIT_FACTORS.get(unit or "", {"": 1}).get(unit_word)


def convert_number(number: float, unit_word: str, unit: str | None) -> float | None:
    factor = unit_factor(unit_word, unit)
    if factor is None:
        return None
    value = number * factor
    return int(value) if float(value).is_integer() else round(value, 10)


def parse_number(text: str, unit: str | None) -> float | None:
    from catalog.phrases import take_number

    parsed = take_number(text)
    if parsed.number is None or parsed.words:
        return None
    return convert_number(parsed.number, parsed.unit_word, unit)


def units_matching(vocab: Vocabulary, unit_word: str, modality: str | None) -> list[str]:
    """Measures whose unit accepts this unit word ("seconds" -> max_clip_seconds)."""
    if not unit_word:
        return []
    names = [
        d.name
        for d in vocab.definitions.values()
        if d.kind == "measure" and d.value_type == "number" and unit_factor(unit_word, d.unit) is not None
    ]
    return vocab.prefer_for_modality(names, modality) if modality else names


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
        return Resolution("unresolved", note=f"values of {d.name} cannot be compared in search")

    if d.value_type == "boolean":
        if isinstance(value, bool):
            return Resolution("resolved", value, "exact")
        norm = normalize(str(value))
        for t, canonical, _ in vocab.value_terms.get(d.name, []):
            if t == norm:
                return Resolution("resolved", canonical, "alias", matched_term=norm)
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
        return Resolution("resolved", parsed, "alias", matched_term=str(value))

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


async def resolve_modality(
    conn: AsyncConnection, vocab: Vocabulary | None, modality: str | None
) -> tuple[str | None, str | None]:
    """Return (modality to filter on or None for all, note for the caller)."""
    if modality is None or not normalize(modality):
        return None, None
    known = {r["m"] for r in await _rows(conn, "select distinct modality as m from models")}
    if vocab is not None:
        known |= vocab.modalities()
    known_sorted = sorted(known)
    norm = normalize(modality)
    for m in known_sorted:
        if normalize(m) in (norm, norm.removesuffix("s")):
            return m, None if normalize(m) == norm else f"'{modality}' read as modality '{m}'"
    res = await _fuzzy(conn, norm, [(normalize(m), m) for m in known_sorted], CAPABILITY_THRESHOLD)
    if res.status == "resolved":
        return res.target, f"'{modality}' read as modality '{res.target}' (approximate)"
    listed = ", ".join(known_sorted) or "none yet"
    return None, f"modality '{modality}' is not known; searched all modalities (known: {listed})"


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
