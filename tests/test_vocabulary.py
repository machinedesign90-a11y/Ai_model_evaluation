"""Migrations 2 to 4 and the draft seeds, against a real PostgreSQL."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from tests import pg
from tests.fixtures import add_user

MIGRATION_3 = next(p for p in pg.MIGRATIONS if p.name.startswith("20261118180000"))
CR001_SEED = pg.ROOT / "tests" / "sql" / "vocabulary_draft_cr001.sql"


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


def define(db, name, value_type, kind="filter", operators=("equals",), better=None, allowed=None):
    db.execute(
        "insert into capability_definitions (name, label, description, value_type, kind, operators, better,"
        " allowed_values) values (%s, %s, 'x', %s, %s, %s, %s, %s)",
        (name, name, value_type, kind, list(operators), better, Jsonb(allowed) if allowed else None),
    )


# --- vocabulary rules (migration 2) ------------------------------------------------------------


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
     ("speech_languages", []), ("price_usd_per_second", "0.5")],
)  # fmt: skip
def test_wrong_type_or_format_rejected(db, model_id, name, bad):
    with pytest.raises(errors.RaiseException):
        put(db, model_id, name, bad)


@pytest.mark.parametrize(
    ("name", "good"),
    [("speech_languages", ["hi", "en"]), ("api_available", False), ("max_clip_seconds", 8),
     ("max_resolution", "2160p"), ("price_usd_per_second", 0.05), ("pricing_notes", "tiered")],
)  # fmt: skip
def test_valid_fact_accepted(db, model_id, name, good):
    put(db, model_id, name, good)
    assert db.execute("select value from capabilities where name = %s", (name,)).fetchone()[0] == good


# --- semantic layer constraints (migration 3) --------------------------------------------------


def test_measure_on_boolean_rejected(db):
    with pytest.raises(errors.CheckViolation, match="measures_are_orderable"):
        define(db, "has_flag", "boolean", kind="measure")


def test_better_on_filter_rejected(db):
    with pytest.raises(errors.CheckViolation, match="better_only_for_measures"):
        define(db, "some_filter", "text", kind="filter", better="higher")


def test_unknown_operator_rejected(db):
    with pytest.raises(errors.CheckViolation):
        define(db, "some_measure", "number", kind="measure", operators=("equals", "near"))


def test_valid_measure_and_filter_accepted(db):
    define(db, "fps_max", "number", kind="measure", operators=("at_least", "at_most"), better="higher")
    define(db, "style", "enum", kind="filter", operators=("equals",), allowed=["a", "b"])


def test_pricing_definition_removed_and_price_measures_seeded(db):
    rows = dict(db.execute("select name, better from capability_definitions where name like 'price%%'").fetchall())
    assert "pricing" not in rows and rows["price_usd_per_second"] == "lower"
    assert (
        db.execute("select kind from capability_definitions where name = 'max_resolution'").fetchone()[0] == "measure"
    )


def test_shared_alias_allowed_across_capabilities_but_not_twice_for_one(db):
    n = db.execute("select count(*) from capability_aliases where lower(alias) = 'price'").fetchone()[0]
    assert n >= 2
    with pytest.raises(errors.UniqueViolation):
        db.execute("insert into capability_aliases (capability_name, alias) values ('price_usd_per_second', 'PRICE')")


# --- verified queries ---------------------------------------------------------------------------


def _vq(db, question="Which TestModel thing?", status="draft", by=None, at=None):
    return db.execute(
        "insert into verified_queries (title, question, query_spec, status, verified_by, verified_at)"
        " values ('t', %s, '{}', %s, %s, %s) returning id",
        (question, status, by, at),
    ).fetchone()[0]


def test_seeded_verified_queries_are_all_drafts(db):
    statuses = {r[0] for r in db.execute("select status from verified_queries")}
    assert statuses == {"draft"}
    assert db.execute("select count(*) from verified_query_phrases").fetchone()[0] >= 7


def test_verified_query_needs_verifier_and_date(db):
    with pytest.raises(errors.CheckViolation):
        _vq(db, status="verified")
    with pytest.raises(errors.CheckViolation):
        _vq(db, status="verified", by="someone")
    _vq(db, status="verified", by="someone", at="2026-11-18T00:00:00Z")


def test_canonical_question_is_unique(db):
    _vq(db, "Same question?")
    with pytest.raises(errors.UniqueViolation):
        _vq(db, "Same question?")


# --- query log, review queue, feedback ----------------------------------------------------------


def test_review_queue_groups_variants_and_keeps_devanagari(db):
    uid = add_user(db)
    for q in ["Best model for Tamil dubbing?", "best model for tamil dubbing", "  BEST model,  for Tamil dubbing! ",
              "हिंदी वॉइसओवर वाला मॉडल?", "हिंदी वॉइसओवर वाला मॉडल"]:  # fmt: skip
        db.execute("insert into queries (user_id, question, resolved_tier, answered) values (%s, %s, 'semantic', true)",
                   (uid, q))  # fmt: skip
    db.execute("insert into queries (user_id, question, resolved_tier) values (%s, 'answered by tier 1', "
               "'verified_query')", (uid,))  # fmt: skip
    rows = {r[0]: r[1] for r in db.execute("select question, times_asked from review_queue")}
    assert rows == {"best model for tamil dubbing": 3, "हिंदी वॉइसओवर वाला मॉडल": 2}


def test_feedback_accepts_only_minus_one_or_one(db):
    uid = add_user(db)
    cid = db.execute("insert into conversations (user_id) values (%s) returning id", (uid,)).fetchone()[0]
    mid = db.execute(
        "insert into messages (conversation_id, role, content) values (%s, 'assistant', '{}') returning id", (cid,)
    ).fetchone()[0]
    for ok in (-1, 1, None):
        db.execute("update messages set feedback = %s where id = %s", (ok, mid))
    for bad in (0, 2, -2):
        with pytest.raises(errors.CheckViolation):
            db.execute("update messages set feedback = %s where id = %s", (bad, mid))


def test_queries_log_columns(db):
    uid = add_user(db)
    db.execute(
        "insert into queries (user_id, question, resolved_tier, query_spec, ttft_ms) values (%s, 'q', 'semantic',"
        ' \'{"modality": "video"}\', 420)',
        (uid,),
    )
    with pytest.raises(errors.CheckViolation):
        db.execute("insert into queries (user_id, question, resolved_tier) values (%s, 'q', 'guessed')", (uid,))
    with pytest.raises(errors.CheckViolation):
        db.execute("insert into queries (user_id, question, ttft_ms) values (%s, 'q', -1)", (uid,))


# --- client access ------------------------------------------------------------------------------


def _as_client(db_dsn, uid):
    conn = psycopg.connect(db_dsn, autocommit=True)  # so `set role` survives a failed statement
    conn.execute("set role authenticated")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(uid),))
    return conn


def test_vocabulary_readable_but_not_writable_by_signed_in_client(db, db_dsn):
    uid = add_user(db)
    with _as_client(db_dsn, uid) as conn:
        for table in ("capability_definitions", "capability_aliases", "value_aliases"):
            assert conn.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(table))).fetchone()[0] > 0
            assert conn.execute(sql.SQL("delete from {}").format(sql.Identifier(table))).rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("insert into capability_aliases (capability_name, alias) values ('deprecated', 'x')")


def test_signed_in_client_cannot_read_verified_queries_or_review_queue(db, db_dsn):
    uid = add_user(db)
    _vq(db, status="verified", by="someone", at="2026-11-18T00:00:00Z")
    db.execute("insert into queries (user_id, question, resolved_tier) values (%s, 'private question', 'unanswered')",
               (uid,))  # fmt: skip
    with _as_client(db_dsn, uid) as conn:
        assert conn.execute("select count(*) from verified_queries").fetchone()[0] == 0
        assert conn.execute("select count(*) from verified_query_phrases").fetchone()[0] == 0
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("select question from review_queue")


def test_review_queue_runs_with_caller_rights(db):
    opts = db.execute("select reloptions from pg_class where relname = 'review_queue'").fetchone()[0]
    assert "security_invoker=true" in opts


# --- seeds --------------------------------------------------------------------------------------

TABLES = ("capability_definitions", "capability_aliases", "value_aliases", "verified_queries", "verified_query_phrases")


def _counts(db):
    return [db.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(t))).fetchone()[0] for t in TABLES]


def test_seeds_are_rerunnable(db):
    before = _counts(db)
    db.execute(pg.SEED_VOCABULARY.read_text())
    db.execute(pg.SEED_VERIFIED_QUERIES.read_text())
    assert _counts(db) == before


def test_data_standard_lists_every_seeded_capability(db):
    doc = (pg.ROOT / "docs" / "DATA_STANDARD.md").read_text()
    names = [r[0] for r in db.execute("select name from capability_definitions")]
    missing = [n for n in names if f"`{n}`" not in doc]
    assert missing == [], f"docs/DATA_STANDARD.md does not mention {missing}"


# --- migration 3 against older databases --------------------------------------------------------


@pytest.fixture
def before_migration_3(pg_admin_dsn):
    """A database at the CR 001 state: migrations 1 and 2 plus the CR 001 vocabulary seed."""
    name = f"md_upgrade_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    dsn = pg.dsn_for(pg_admin_dsn, name)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(pg.AUTH_STUB.read_text())
        for path in pg.MIGRATIONS:
            if path.name >= MIGRATION_3.name:
                break
            conn.execute(path.read_text())
        conn.execute(CR001_SEED.read_text())
    yield dsn
    pg.drop_database(pg_admin_dsn, name)


def test_migration_3_fails_and_rolls_back_when_a_pricing_fact_exists(before_migration_3):
    with psycopg.connect(before_migration_3, autocommit=True) as conn:
        mid = conn.execute(
            "insert into models (provider, name, slug, modality) values ('TestCo', 'T', 't', 'video') returning id"
        ).fetchone()[0]
        conn.execute(
            "insert into capabilities (model_id, name, value) values (%s, 'pricing', %s)",
            (mid, Jsonb({"unit": "per_second", "amount": 1, "currency": "USD"})),
        )
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(MIGRATION_3.read_text())
        conn.execute("rollback")  # the failed script leaves its own transaction aborted
        cols_sql = "select column_name from information_schema.columns where table_name = 'capability_definitions'"
        cols = {r[0] for r in conn.execute(cols_sql)}
        assert "kind" not in cols
        assert conn.execute("select to_regclass('verified_queries')").fetchone()[0] is None
        assert conn.execute("select count(*) from capability_definitions where name = 'pricing'").fetchone()[0] == 1
        assert conn.execute("select count(*) from capabilities where name = 'pricing'").fetchone()[0] == 1


def test_upgrade_path_from_cr001_seed(before_migration_3):
    with psycopg.connect(before_migration_3, autocommit=True) as conn:
        for path in pg.MIGRATIONS:
            if path.name >= MIGRATION_3.name:
                conn.execute(path.read_text())
        conn.execute(pg.SEED_VOCABULARY.read_text())
        conn.execute(pg.SEED_VERIFIED_QUERIES.read_text())
        defs = dict(conn.execute("select name, kind from capability_definitions").fetchall())
        assert "pricing" not in defs and defs["max_resolution"] == "measure" and defs["speech_languages"] == "filter"
        assert (
            conn.execute("select count(*) from capability_aliases where capability_name = 'pricing'").fetchone()[0] == 0
        )
        assert conn.execute("select count(*) from verified_queries").fetchone()[0] == 7
