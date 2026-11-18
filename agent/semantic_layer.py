"""The compact semantic layer placed in the system prompt, loaded from the database and cached.

One line per capability: name, label, kind, type and unit, operators, better direction, allowed values
and a few example aliases. Catalog text is data: it is rendered as a plain list, and labels and aliases
are quoted.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from psycopg import AsyncConnection

from catalog.vocabulary import Vocabulary, load_vocabulary


def render_semantic_layer(vocab: Vocabulary, max_aliases: int = 4) -> str:
    lines = []
    for kind in ("measure", "filter"):
        heading = "Measures (compare, threshold or sort):" if kind == "measure" else "Filters (include or exclude):"
        lines.append(heading)
        for d in sorted(vocab.definitions.values(), key=lambda x: x.name):
            if d.kind != kind:
                continue
            typ = d.value_type + (f" in {d.unit}" if d.unit else "")
            parts = [f"- {d.name} ({d.label!r}): {typ}"]
            if d.operators:
                parts.append("operators " + "/".join(d.operators))
            else:
                parts.append("display only, not searchable")
            if d.better:
                parts.append(f"{d.better} is better")
            if d.modalities:
                parts.append("for " + "/".join(d.modalities))
            if d.allowed_values:
                ordered = " (lowest first)" if d.value_type == "enum" and kind == "measure" else ""
                parts.append("values " + ", ".join(map(str, d.allowed_values)) + ordered)
            aliases = vocab.aliases.get(d.name, [])[:max_aliases]
            if aliases:
                parts.append("e.g. " + ", ".join(repr(a) for a in aliases))
            lines.append("; ".join(parts))
    return "\n".join(lines)


class SemanticLayerCache:
    """Reuse the rendered layer for `ttl_seconds` (SEMANTIC_LAYER_TTL_SECONDS); 0 disables caching."""

    def __init__(self, ttl_seconds: int = 3600, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self._clock = clock
        self._value: str | None = None
        self._loaded_at = 0.0

    async def get(self, connect: Callable[[], Awaitable[AsyncConnection]] | AsyncConnection) -> str:
        if self._value is not None and self.ttl > 0 and self._clock() - self._loaded_at < self.ttl:
            return self._value
        conn = connect if isinstance(connect, AsyncConnection) else await connect()
        self._value = render_semantic_layer(await load_vocabulary(conn))
        self._loaded_at = self._clock()
        return self._value

    def invalidate(self) -> None:
        self._value = None
