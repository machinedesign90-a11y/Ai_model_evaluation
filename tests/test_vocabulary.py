"""Second migration: controlled capability vocabulary and provenance for pricing / API availability."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg.types.json import Jsonb

from tests import pg
from tests.fixtures import add_user, define_test_capabilities


@pytest.fixture
def model_id(db):
    define_test_capabilities(db)
    return db.execute(
        "insert into models (provider, name, slug, modality) values ('TestCo', 'TestModel V', 'testco-v', 'video')"
        " returning id"
    ).fetchone()[0]


def put(db, model_id, name, value):
    db.execute(
        "insert into capabilities (model_id, name, value) values (%s, %s, %s) on conflict (model_id, name)"
        " do update set value = excluded.value",
        (model_id, name, Jsonb(value)),
    )


def test_models_no_longer_have_pricing_or_api_columns(db):
    cols = {r[0] for r in db.execute("select column_name from information_schema.columns where table_name = 'models'")}
    assert "pricing" not in cols and "api_available" not in cols and "description" in cols


def test_migration_defines_only_pricing_and_api_available(db):
    names = [r[0] for r in db.execute("select name from capability_definitions order by name")]
    assert names == ["api_available", "pricing"]


def test_language_codes_are_iso_639_1(db):
    assert db.execute("select count(*) from language_codes").fetchone()[0] == 184
    assert db.execute("select name from language_codes where code = 'hi'").fetchone()[0] == "Hindi"


def test_undefined_capability_name_rejected(db, model_id):
    with pytest.raises(errors.CheckViolation, match="not defined"):
        put(db, model_id, "voiceover_languages", ["hi"])


@pytest.mark.parametrize(
    ("name", "good", "bad"),
    [
        ("api_available", True, "yes"),
        ("context_window", 128000, "128k"),
        ("pricing", {"currency": "USD", "unit": "image", "amount": 0.02}, 0.02),
        ("notes", "anything", ["a"]),
        ("output_languages", ["hi", "en"], "hi"),
    ],
)
def test_value_type_enforced(db, model_id, name, good, bad):
    put(db, model_id, name, good)
    with pytest.raises(errors.CheckViolation):
        put(db, model_id, name, bad)


@pytest.mark.parametrize("bad", [["Hindi"], ["HI"], ["xx"], ["hi", 3], [], ["hi", "hi"]])
def test_language_list_must_be_distinct_iso_codes(db, model_id, bad):
    with pytest.raises(errors.CheckViolation):
        put(db, model_id, "output_languages", bad)


def test_allowed_values_enforced(db, model_id):
    put(db, model_id, "max_resolution", "1080p")
    with pytest.raises(errors.CheckViolation, match="allowed values"):
        put(db, model_id, "max_resolution", "1080P")


def test_definition_change_that_breaks_existing_facts_rejected(db, model_id):
    put(db, model_id, "max_resolution", "4k")
    with pytest.raises(errors.CheckViolation, match="no longer fits"):
        db.execute(
            "update capability_definitions set allowed_values = %s where name = 'max_resolution'",
            (Jsonb(["720p", "1080p"]),),
        )
    db.execute(
        "update capability_definitions set allowed_values = %s where name = 'max_resolution'",
        (Jsonb(["720p", "1080p", "4k", "8k"]),),
    )


def test_renaming_a_definition_cascades_to_facts(db, model_id):
    put(db, model_id, "context_window", 1000)
    db.execute("update capability_definitions set name = 'max_input_tokens' where name = 'context_window'")
    assert db.execute("select name from capabilities").fetchone()[0] == "max_input_tokens"


def test_definition_shape_rules(db):
    with pytest.raises(errors.CheckViolation):  # name format
        db.execute(
            "insert into capability_definitions (name, value_type, description) values ('Bad Name', 'text', 'x')"
        )
    with pytest.raises(errors.CheckViolation):  # allowed values only for text types
        db.execute(
            "insert into capability_definitions (name, value_type, description, allowed_values)"
            " values ('flag', 'boolean', 'x', '[\"a\"]')"
        )


def test_clients_can_read_but_not_change_vocabulary(db, db_dsn):
    uid = add_user(db)
    with psycopg.connect(db_dsn) as conn, conn.transaction():
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(uid),))
        assert conn.execute("select count(*) from capability_definitions").fetchone()[0] == 2
        assert conn.execute("select count(*) from language_codes").fetchone()[0] == 184
        assert conn.execute("delete from capability_definitions").rowcount == 0
        assert conn.execute("update language_codes set name = 'x'").rowcount == 0


# --- the data-loss guard, against a database that has only the first migration ----------------


@pytest.fixture
def first_migration_only(pg_admin_dsn):
    name = f"md_guard_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("create database {}").format(psycopg.sql.Identifier(name)))
    dsn = pg.dsn_for(pg_admin_dsn, name)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(pg.AUTH_STUB.read_text())
        conn.execute(pg.MIGRATIONS[0].read_text())
    yield dsn
    pg.drop_database(pg_admin_dsn, name)


def apply_second(dsn):
    with psycopg.connect(dsn) as conn:  # one transaction: all or nothing
        conn.execute(pg.MIGRATIONS[1].read_text())


def test_guard_refuses_when_pricing_data_exists(first_migration_only):
    with psycopg.connect(first_migration_only, autocommit=True) as conn:
        conn.execute(
            "insert into models (provider, name, slug, modality, pricing)"
            " values ('TestCo', 'T', 't', 'video', '{\"a\":1}')"
        )
    with pytest.raises(errors.RaiseException, match="holds data"):
        apply_second(first_migration_only)
    with psycopg.connect(first_migration_only) as conn:  # nothing was dropped
        assert conn.execute("select pricing from models").fetchone()[0] == {"a": 1}


def test_guard_passes_on_empty_catalog(first_migration_only):
    apply_second(first_migration_only)
