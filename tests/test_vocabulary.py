"""Migration 2 (provenance and vocabulary) and the draft vocabulary seed, against a real PostgreSQL."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

from tests import pg
from tests.fixtures import add_user


@pytest.fixture
def model_id(db):
    return db.execute(
        "insert into models (provider, name, slug, modality) values ('TestCo', 'TestModel V', 'testco-v', 'video')"
        " returning id"
    ).fetchone()[0]


def put(db, model_id, name, value):
    db.execute(
        "insert into capabilities (model_id, name, value) values (%s, %s, %s)"
        " on conflict (model_id, name) do update set value = excluded.value",
        (model_id, name, Jsonb(value)),
    )


def test_pricing_and_api_columns_gone_from_models(db):
    cols = {r[0] for r in db.execute("select column_name from information_schema.columns where table_name = 'models'")}
    assert {"pricing", "api_available"}.isdisjoint(cols) and "description" in cols


def test_unknown_capability_name_rejected(db, model_id):
    with pytest.raises((errors.RaiseException, errors.ForeignKeyViolation), match="voiceover_languages"):
        put(db, model_id, "voiceover_languages", ["hi"])


def test_language_name_rejected_where_code_expected(db, model_id):
    with pytest.raises(errors.RaiseException, match="outside the allowed list"):
        put(db, model_id, "speech_languages", ["Hindi"])


@pytest.mark.parametrize(
    ("name", "bad"),
    [("api_available", "yes"), ("max_clip_seconds", "10s"), ("max_resolution", "4K"), ("speech_languages", "hi"),
     ("speech_languages", [])],
)  # fmt: skip
def test_wrong_type_or_format_rejected(db, model_id, name, bad):
    with pytest.raises(errors.RaiseException):
        put(db, model_id, name, bad)


@pytest.mark.parametrize(
    ("name", "good"),
    [("speech_languages", ["hi", "en"]), ("api_available", False), ("max_clip_seconds", 8),
     ("max_resolution", "2160p"), ("pricing", {"unit": "per_second", "amount": 1, "currency": "USD"})],
)  # fmt: skip
def test_valid_fact_accepted(db, model_id, name, good):
    put(db, model_id, name, good)
    assert db.execute("select value from capabilities where name = %s", (name,)).fetchone()[0] == good


def test_vocabulary_readable_but_not_writable_by_signed_in_client(db, db_dsn):
    uid = add_user(db)
    with psycopg.connect(db_dsn) as conn, conn.transaction():
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(uid),))
        for table in ("capability_definitions", "capability_aliases", "value_aliases"):
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] > 0, table  # noqa: S608
            assert conn.execute(f"delete from {table}").rowcount == 0, table  # noqa: S608
        conn.execute("update capability_definitions set allowed_values = '[]'")
    assert db.execute("select allowed_values from capability_definitions where name = 'max_resolution'").fetchone()[0]
    for table in ("capability_definitions", "capability_aliases", "value_aliases"):
        with pytest.raises(errors.InsufficientPrivilege):
            with psycopg.connect(db_dsn) as conn, conn.transaction():
                conn.execute("set local role authenticated")
                conn.execute(
                    f"insert into {table} select * from {table} limit 1"  # noqa: S608
                )


def test_seed_is_rerunnable(db):
    before = [db.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608
              for t in ("capability_definitions", "capability_aliases", "value_aliases")]  # fmt: skip
    db.execute(pg.SEED_VOCABULARY.read_text())
    after = [db.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608
             for t in ("capability_definitions", "capability_aliases", "value_aliases")]  # fmt: skip
    assert before == after and before[0] == 12


def test_data_standard_lists_every_seeded_capability(db):
    doc = (pg.ROOT / "docs" / "DATA_STANDARD.md").read_text()
    names = [r[0] for r in db.execute("select name from capability_definitions")]
    missing = [n for n in names if f"`{n}`" not in doc]
    assert missing == [], f"docs/DATA_STANDARD.md does not mention {missing}"
