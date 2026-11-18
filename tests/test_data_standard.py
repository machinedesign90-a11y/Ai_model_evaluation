"""docs/DATA_STANDARD.md must list exactly the vocabulary the migrations define."""

from pathlib import Path

from catalog.vocabulary import fetch_definitions, render, replace_section

DOC = Path(__file__).resolve().parent.parent / "docs" / "DATA_STANDARD.md"


def test_data_standard_matches_migrations(db):
    current = DOC.read_text()
    assert replace_section(current, render(fetch_definitions(db))) == current, (
        "docs/DATA_STANDARD.md is out of date: run scripts/data_standard.py against a freshly migrated database"
    )
