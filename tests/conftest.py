from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from tests import pg
from tests.fixtures import seed_catalog


@pytest.fixture(scope="session")
def pg_admin_dsn() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    with pg.temporary_cluster() as dsn:
        yield dsn


@pytest.fixture(scope="session")
def pg_template(pg_admin_dsn: str) -> Iterator[str]:
    name = pg.build_template(pg_admin_dsn)
    yield name
    pg.drop_database(pg_admin_dsn, name)


@pytest.fixture
def db_dsn(pg_admin_dsn: str, pg_template: str) -> Iterator[str]:
    """A fresh database per test: stub auth schema + all migrations, nothing else."""
    name = pg.create_from_template(pg_admin_dsn, pg_template)
    yield pg.dsn_for(pg_admin_dsn, name)
    pg.drop_database(pg_admin_dsn, name)


@pytest.fixture
def db(db_dsn: str) -> Iterator[psycopg.Connection]:
    """Superuser connection (stands in for the service role), autocommit."""
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def catalog_dsn(db_dsn: str, db: psycopg.Connection) -> str:
    """A fresh database with the fictional catalog loaded."""
    seed_catalog(db)
    return db_dsn


@pytest.fixture
async def aconn(catalog_dsn: str):
    """Async connection to a database holding the fictional catalog."""
    async with await psycopg.AsyncConnection.connect(catalog_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
async def aconn_tier1(catalog_dsn: str, db: psycopg.Connection):
    """Fictional catalog with the draft verified queries marked verified (test databases only)."""
    from tests.fixtures import verify_all_draft_queries

    verify_all_draft_queries(db)
    async with await psycopg.AsyncConnection.connect(catalog_dsn, autocommit=True) as conn:
        yield conn
