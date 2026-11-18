"""Throwaway PostgreSQL for tests.

If TEST_DATABASE_URL is set, it must point at a server where that user is a
superuser (the harness creates roles and databases). Otherwise a temporary
cluster is started with the local PostgreSQL binaries (initdb / pg_ctl) and
removed afterwards.
"""

from __future__ import annotations

import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
AUTH_STUB = Path(__file__).resolve().parent / "sql" / "auth_stub.sql"
SEED_VOCABULARY = ROOT / "supabase" / "seed" / "vocabulary_draft.sql"
SEED_VERIFIED_QUERIES = ROOT / "supabase" / "seed" / "verified_queries_draft.sql"


def _find_bindir() -> Path | None:
    if shutil.which("pg_ctl"):
        return Path(shutil.which("pg_ctl")).parent
    if shutil.which("pg_config"):
        out = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, check=False)
        cand = Path(out.stdout.strip())
        if (cand / "pg_ctl").exists():
            return cand
    for base in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (base / "pg_ctl").exists():
            return base
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def temporary_cluster() -> Iterator[str]:
    """Start a private cluster and yield a superuser DSN."""
    bindir = _find_bindir()
    if bindir is None:
        raise RuntimeError("PostgreSQL binaries not found; install PostgreSQL 16 or set TEST_DATABASE_URL")

    # initdb refuses to run as root; fall back to the 'postgres' OS user.
    prefix: list[str] = []
    owner = None
    if os.geteuid() == 0:
        try:
            owner = pwd.getpwnam("postgres")
        except KeyError as exc:
            raise RuntimeError("running as root needs a 'postgres' OS user, or set TEST_DATABASE_URL") from exc
        prefix = ["runuser", "-u", "postgres", "--"]

    tmp = Path(tempfile.mkdtemp(prefix="md-pg-"))
    data = tmp / "data"
    if owner is not None:
        os.chown(tmp, owner.pw_uid, owner.pw_gid)
    port = _free_port()
    log = tmp / "server.log"
    run = lambda *a: subprocess.run([*prefix, *a], check=True, capture_output=True)  # noqa: E731
    try:
        run(str(bindir / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "--no-sync")
        run(
            str(bindir / "pg_ctl"), "-D", str(data), "-l", str(log), "-w", "-o",
            f"-p {port} -k {tmp} -c listen_addresses=127.0.0.1 -c fsync=off -c max_connections=100",
            "start",
        )  # fmt: skip
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [*prefix, str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def dsn_for(admin_dsn: str, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(admin_dsn, dbname=dbname)


def build_template(admin_dsn: str) -> str:
    """Create a database with the auth stub, every migration, then both draft seeds; return its name."""
    name = f"md_template_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for role in ("anon", "authenticated"):
            exists = conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    with psycopg.connect(dsn_for(admin_dsn, name), autocommit=True) as conn:
        conn.execute(AUTH_STUB.read_text())
        for path in MIGRATIONS:
            conn.execute(path.read_text())
        conn.execute(SEED_VOCABULARY.read_text())
        conn.execute(SEED_VERIFIED_QUERIES.read_text())
    return name


def create_from_template(admin_dsn: str, template: str) -> str:
    name = f"md_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {} template {}").format(sql.Identifier(name), sql.Identifier(template)))
    return name


def drop_database(admin_dsn: str, name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name)))
