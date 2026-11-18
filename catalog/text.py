"""Text normalisation shared by every lookup: case, punctuation, hyphens and spacing don't matter."""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Casefold, turn punctuation, symbols and underscores into spaces, collapse spaces.

    Letters and combining marks of any script are kept, so "हिन्दी" survives intact.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    out = []
    for ch in text:
        if ch == "_" or ch.isspace() or unicodedata.category(ch)[0] in "PSZ":
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())
