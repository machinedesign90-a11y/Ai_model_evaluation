"""Step 1: the migration's guarantees, checked against a real PostgreSQL."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import errors

from tests.fixtures import VERIFIED_AT, add_user


@contextmanager
def as_user(dsn: str, user_id: uuid.UUID | None, role: str = "authenticated"):
    """A transaction acting as a browser client: the given role plus a JWT subject."""
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute(f"set local role {role}")
            conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(user_id or ""),))
            yield conn


def consume(conn, user_id):
    return conn.execute("select allowed, remaining from consume_message(%s)", (user_id,)).fetchone()


# --- profiles -----------------------------------------------------------------------------------


def test_profile_created_on_signup(db):
    uid = add_user(db, "someone@example.test")
    row = db.execute("select email, messages_used, message_limit from profiles where id = %s", (uid,)).fetchone()
    assert row == ("someone@example.test", 0, 4)


# --- free-tier counter --------------------------------------------------------------------------


def test_consume_message_allows_exactly_the_limit(db):
    uid = add_user(db)
    results = [consume(db, uid) for _ in range(6)]
    assert results == [(True, 3), (True, 2), (True, 1), (True, 0), (False, 0), (False, 0)]
    assert db.execute("select messages_used from profiles where id = %s", (uid,)).fetchone()[0] == 4


def test_consume_message_respects_per_user_limit(db):
    uid = add_user(db)
    db.execute("update profiles set message_limit = 1 where id = %s", (uid,))
    assert [consume(db, uid) for _ in range(2)] == [(True, 0), (False, 0)]


def test_consume_message_blocks_unknown_user(db):
    assert consume(db, uuid.uuid4()) == (False, 0)


def test_consume_message_under_concurrency(db, db_dsn):
    uid = add_user(db)
    n = 24
    barrier = threading.Barrier(n)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        with psycopg.connect(db_dsn, autocommit=True) as conn:
            barrier.wait()
            allowed, _ = consume(conn, uid)
            with lock:
                results.append(allowed)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == n
    assert results.count(True) == 4
    assert db.execute("select messages_used from profiles where id = %s", (uid,)).fetchone()[0] == 4


# --- verified facts need a date -----------------------------------------------------------------


def _model(db) -> uuid.UUID:
    return db.execute(
        "insert into models (provider, name, slug, modality) values ('TestCo', 'TestModel X', 'testco-x', 'video')"
        " returning id"
    ).fetchone()[0]


def test_verified_fact_without_date_rejected(db):
    mid = _model(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "insert into capabilities (model_id, name, value, verification_status)"
            " values (%s, 'api_available', 'true', 'verified')",
            (mid,),
        )


def test_verified_fact_with_date_and_unverified_without_date_accepted(db):
    mid = _model(db)
    db.execute(
        "insert into capabilities (model_id, name, value, verification_status, last_verified_at)"
        " values (%s, 'api_available', 'true', 'verified', %s)",
        (mid, VERIFIED_AT),
    )
    mid2 = db.execute(
        "insert into models (provider, name, slug, modality) values ('TestCo', 'TestModel Y', 'testco-y', 'video')"
        " returning id"
    ).fetchone()[0]
    db.execute("insert into capabilities (model_id, name, value) values (%s, 'api_available', 'true')", (mid2,))
    assert db.execute("select count(*) from capabilities").fetchone()[0] == 2


# --- row-level security -------------------------------------------------------------------------


@pytest.fixture
def two_users_with_chats(db):
    a, b = add_user(db), add_user(db)
    convs = {}
    for uid in (a, b):
        cid = db.execute("insert into conversations (user_id) values (%s) returning id", (uid,)).fetchone()[0]
        db.execute(
            "insert into messages (conversation_id, role, content) values (%s, 'user', %s)",
            (cid, psycopg.types.json.Jsonb({"text": f"hello from {uid}"})),
        )
        convs[uid] = cid
    return a, b, convs


def test_users_see_only_their_own_profile(db_dsn, two_users_with_chats):
    a, b, _ = two_users_with_chats
    with as_user(db_dsn, a) as conn:
        ids = [r[0] for r in conn.execute("select id from profiles")]
    assert ids == [a]


def test_users_cannot_read_other_users_conversations_or_messages(db_dsn, two_users_with_chats):
    a, b, convs = two_users_with_chats
    with as_user(db_dsn, a) as conn:
        assert [r[0] for r in conn.execute("select id from conversations")] == [convs[a]]
        assert conn.execute("select count(*) from conversations where id = %s", (convs[b],)).fetchone()[0] == 0
        msg_convs = {r[0] for r in conn.execute("select conversation_id from messages")}
    assert msg_convs == {convs[a]}


def test_signed_in_user_can_read_catalog_but_not_write_it(db_dsn, db):
    _model(db)
    uid = add_user(db)
    with as_user(db_dsn, uid) as conn:
        assert conn.execute("select count(*) from models").fetchone()[0] == 1
    # Blocked by RLS (no write policy), whatever the table grants are.
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(db_dsn, uid) as conn:
            conn.execute("insert into models (provider, name, slug, modality) values ('x', 'y', 'z', 'video')")
    with as_user(db_dsn, uid) as conn:
        assert conn.execute("update models set name = 'hijacked'").rowcount == 0
        assert conn.execute("delete from capabilities").rowcount == 0
    assert db.execute("select name from models").fetchone()[0] == "TestModel X"


def test_signed_in_user_cannot_call_consume_message(db_dsn, db):
    uid = add_user(db)
    with pytest.raises(errors.InsufficientPrivilege):
        with as_user(db_dsn, uid) as conn:
            consume(conn, uid)
    assert db.execute("select messages_used from profiles where id = %s", (uid,)).fetchone()[0] == 0


def test_signed_in_user_cannot_raise_own_limit(db_dsn, db):
    uid = add_user(db)
    with as_user(db_dsn, uid) as conn:
        conn.execute("update profiles set messages_used = 0, message_limit = 1000 where id = %s", (uid,))
    assert db.execute("select message_limit from profiles where id = %s", (uid,)).fetchone()[0] == 4


def test_queries_log_hidden_from_clients(db_dsn, db):
    uid = add_user(db)
    db.execute("insert into queries (user_id, question) values (%s, 'q')", (uid,))
    with as_user(db_dsn, uid) as conn:
        assert conn.execute("select count(*) from queries").fetchone()[0] == 0


def test_anonymous_role_reads_nothing(db_dsn, db):
    _model(db)
    add_user(db)
    with as_user(db_dsn, None, role="anon") as conn:
        for table in ("models", "capabilities", "sources", "profiles", "conversations", "messages", "queries"):
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0, table  # noqa: S608
