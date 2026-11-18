"""Regenerate the vocabulary table in docs/DATA_STANDARD.md from capability_definitions.

python scripts/data_standard.py           # rewrite the doc from DATABASE_URL
python scripts/data_standard.py --check   # exit 1 if the doc is out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from catalog.vocabulary import fetch_definitions, render, replace_section  # noqa: E402
from config import get_settings  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "docs" / "DATA_STANDARD.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with psycopg.connect(get_settings().database_dsn()) as conn:
        section = render(fetch_definitions(conn))
    current = DOC.read_text()
    updated = replace_section(current, section)
    if args.check:
        if updated != current:
            print("docs/DATA_STANDARD.md is out of date; run scripts/data_standard.py", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(updated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
