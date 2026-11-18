"""Everyday comparison words: "longer than 10 seconds", "under $0.10", "cheapest", "at least 4K".

These are grammar, not vocabulary: they pick an operator, a number with its unit, or a sort
direction. What the words refer to is still resolved through the vocabulary tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from catalog.text import normalize

AT_LEAST = ["at least", "no less than", "minimum of", "minimum", "more than", "greater than", "longer than",
            "bigger than", "larger than", "higher than", "better than", "or more", "or longer", "or above",
            "or higher", "or better", "or bigger", "over", "above", ">=", ">", "+"]  # fmt: skip
AT_MOST = ["at most", "no more than", "less than", "fewer than", "shorter than", "cheaper than", "lower than",
           "smaller than", "or less", "or fewer", "or shorter", "or under", "or lower", "or cheaper", "up to",
           "under", "below", "<=", "<"]  # fmt: skip

# Superlatives for sorting. "cheapest" also names the capability family (price).
BEST_WORDS = ["longest", "highest", "largest", "biggest", "best", "most", "top", "greatest", "maximum", "max"]
WORST_WORDS = ["shortest", "lowest", "smallest", "least", "worst", "minimum", "min"]
CHEAP_WORDS = ["cheapest", "least expensive", "lowest cost", "lowest price", "most affordable", "cheaper", "cheap"]
EXPENSIVE_WORDS = ["most expensive", "priciest", "highest price", "highest cost"]

_NUM = re.compile(
    r"(?<![\w.])(?P<cur>\$)?\s*(?P<n>\d+(?:[.,]\d+)?)(?![\d.,])"
    r"\s*(?:(?P<mult>k|m|million|b|billion)(?![a-z]))?"
    r"\s*(?:(?P<unit>mp|s|sec|secs|seconds?|mins?|minutes?|h|hrs?|hours?|tokens?|megapixels?|usd|dollars?|"
    r"cents?|parameters?|params)(?![a-z]))?"
)
_MULT = {"k": 1_000, "m": 1_000_000, "million": 1_000_000, "b": 1, "billion": 1}


@dataclass
class Parsed:
    words: str  # what is left: the capability words, normalized
    operator: str | None = None
    number: float | None = None
    unit_word: str = ""


def _take(text: str, options: list[str]) -> tuple[str, str | None]:
    for opt in sorted(options, key=len, reverse=True):
        # Whole words only, and not inside hyphenated words ("voice-over" is not "over").
        pattern = r"(?<![\w-])" + re.escape(opt) + r"(?![\w-])" if opt[0].isalnum() else re.escape(opt)
        m = re.search(pattern, text)
        if m:
            return (text[: m.start()] + " " + text[m.end() :]), opt
    return text, None


def take_operator(text: str) -> tuple[str, str | None]:
    """Remove an operator word; return (rest, 'at_least' | 'at_most' | None). The rest is casefolded, not normalized."""
    t = text.casefold()
    t, low = _take(t, AT_LEAST)
    if low:
        return t, "at_least"
    t, high = _take(t, AT_MOST)
    return t, "at_most" if high else None


def take_number(text: str) -> Parsed:
    """Pull a number with its unit out of a phrase: '128k tokens', '$0.10 per second', '2 minutes'."""
    t = text.casefold()
    m = _NUM.search(t)
    if not m:
        return Parsed(words=normalize(t))
    number = float(m.group("n").replace(",", "."))
    mult, unit = m.group("mult"), (m.group("unit") or "")
    if mult in ("b", "billion") and not unit:
        unit = "billion"
    elif mult:
        number *= _MULT[mult]
    if m.group("cur") and not unit:
        unit = "usd"
    rest = t[: m.start()] + " " + t[m.end() :]
    if unit in ("usd", "dollar", "dollars", "cent", "cents"):
        rest = re.sub(r"\bper\s+\w+", " ", rest)
    return Parsed(words=normalize(rest), number=number, unit_word=unit)


def parse_comparison(text: str) -> Parsed:
    rest, op = take_operator(text)
    parsed = take_number(rest)
    parsed.operator = op
    return parsed


def parse_sort(text: str) -> tuple[str, str | None, bool]:
    """Return (capability words, direction or None, is_price) from a sort phrase like 'cheapest'."""
    t = text.casefold()
    t, cheap = _take(t, CHEAP_WORDS)
    if cheap:
        return normalize(t), "best", True
    t, pricey = _take(t, EXPENSIVE_WORDS)
    if pricey:
        return normalize(t), "worst", True
    t, best = _take(t, BEST_WORDS)
    if best:
        return normalize(t), "desc", False
    t, worst = _take(t, WORST_WORDS)
    if worst:
        return normalize(t), "asc", False
    return normalize(t), None, False
