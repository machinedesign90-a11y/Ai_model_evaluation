"""MCP server: an MCP client lists and calls each tool against the fixture catalog.

In-process for the detailed checks, plus the real stdio and streamable HTTP transports,
and the MCP_PUBLIC / MCP_API_KEY gate.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx2
import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from mcp_server.server import create_server

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"find_verified_query", "run_verified_query", "search_models", "list_capabilities", "get_model",
         "compare_models"}  # fmt: skip
API_KEY = "k" * 40


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content


@pytest.fixture
def server(catalog_dsn, db):
    from tests.fixtures import verify_all_draft_queries

    verify_all_draft_queries(db)  # test database only, so tier 1 can be exercised

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(
            catalog_dsn, autocommit=True, options="-c default_transaction_read_only=on"
        ) as conn:
            yield conn

    return create_server(connection=connect)


async def test_lists_the_six_read_only_tools(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS  # save_context and offer_followups stay internal to the agent
    for t in tools.values():
        assert t.description and t.output_schema
        assert t.annotations.read_only_hint is True


async def test_calls_each_tool(server):
    async with Client(server) as client:
        s = payload(
            await client.call_tool(
                "search_models",
                {
                    "modality": "video",
                    "filters": [{"capability": "Hindi voiceover"}],
                    "sort": {"capability": "cheapest"},
                },
            )
        )
        assert [m["slug"] for m in s["full_matches"]] == ["testco-alpha"]
        assert s["interpretation"][0]["capability"] == "speech_languages"
        assert {m["slug"] for m in s["close_matches"]} == {"testco-gamma", "testco-epsilon"}

        caps = payload(await client.call_tool("list_capabilities", {"modality": "video"}))
        assert "speech_languages" in {c["name"] for c in caps["capabilities"]}

        g = payload(await client.call_tool("get_model", {"slug": "TestModel Beta"}))
        assert g["status"] == "found" and g["matched_by"] == "name"
        assert {f["name"]: f["value"] for f in g["model"]["facts"]}["api_available"] is False
        assert "kind" in caps["capabilities"][0]

        c = payload(
            await client.call_tool(
                "compare_models", {"slugs": ["testco-alpha", "testco-gamma"], "capability_names": ["price"]}
            )
        )
        assert c["models"][1]["not_recorded"] == ["price_usd_per_second"]

        f = payload(await client.call_tool("find_verified_query", {"question": "cheapest ai video"}))
        top = f["candidates"][0]
        assert top["title"] == "Cheapest video models"
        r = payload(await client.call_tool("run_verified_query", {"id": top["id"]}))
        assert [m["slug"] for m in r["full_matches"]][:2] == ["testco-beta", "testco-alpha"]
        assert r["verified_query"]["title"] == "Cheapest video models"


async def test_invalid_arguments_return_a_tool_error(server):
    async with Client(server) as client:
        r = await client.call_tool("compare_models", {"slugs": ["testco-alpha"]})
    assert r.is_error
    assert "slugs" in r.content[0].text


def _env(dsn, **extra):
    env = {**os.environ, "DATABASE_URL": dsn, "PYTHONPATH": str(ROOT), "MCP_PUBLIC": "false"}
    env.pop("MCP_API_KEY", None)
    return {**env, **extra}


async def test_stdio_transport(catalog_dsn):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server", "--transport", "stdio"],
        env=_env(catalog_dsn),
        cwd=str(ROOT),
    )
    async with Client(params) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS
        g = payload(await client.call_tool("get_model", {"slug": "testco-alpha"}))
    assert g["model"]["name"] == "TestModel Alpha"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def run_http(env):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server", "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode())
            time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_streamable_http_transport(catalog_dsn):
    with run_http(_env(catalog_dsn)) as url:
        async with Client(url) as client:
            assert {t.name for t in (await client.list_tools()).tools} == TOOLS
            c = payload(
                await client.call_tool("compare_models", {"slugs": ["testco-alpha", "testco-beta", "testco-gamma"]})
            )
    assert [m["slug"] for m in c["models"]] == ["testco-alpha", "testco-beta", "testco-gamma"]


# --- MCP_PUBLIC / MCP_API_KEY ------------------------------------------------------------------


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.mark.parametrize("auth", [None, "Bearer wrong-key-wrong-key-wrong-key-wrong", f"Basic {API_KEY}", "Bearer"])
async def test_public_http_rejects_requests_without_the_key(catalog_dsn, auth):
    with run_http(_env(catalog_dsn, MCP_PUBLIC="true", MCP_API_KEY=API_KEY)) as url:
        headers = HEADERS | ({"authorization": auth} if auth else {})
        async with httpx2.AsyncClient() as http:
            r = await http.post(url, json=INIT, headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized" and API_KEY not in r.text


async def test_public_http_accepts_the_key(catalog_dsn):
    with run_http(_env(catalog_dsn, MCP_PUBLIC="true", MCP_API_KEY=API_KEY)) as url:
        http = create_mcp_http_client(headers={"authorization": f"Bearer {API_KEY}"})
        async with Client(streamable_http_client(url, http_client=http)) as client:
            assert {t.name for t in (await client.list_tools()).tools} == TOOLS


def test_public_without_a_key_refuses_to_start(catalog_dsn):
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_server", "--transport", "http", "--host", "0.0.0.0", "--port", "1"],
        env=_env(catalog_dsn, MCP_PUBLIC="true"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0 and "MCP_API_KEY" in proc.stderr


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "example.com"])
def test_private_http_refuses_non_loopback_hosts(catalog_dsn, host):
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_server", "--transport", "http", "--host", host, "--port", "1"],
        env=_env(catalog_dsn),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0 and "MCP_PUBLIC is false" in proc.stderr


def test_loopback_hosts_allowed():
    from mcp_server.__main__ import is_loopback

    assert all(is_loopback(h) for h in ("localhost", "127.0.0.1", "::1"))


async def test_mcp_run_verified_query_does_not_write(server, catalog_dsn):
    """The MCP server is read-only: running a verified query there does not touch usage counts."""
    async with Client(server) as client:
        f = payload(await client.call_tool("find_verified_query", {"question": "longest video generation"}))
        payload(await client.call_tool("run_verified_query", {"id": f["candidates"][0]["id"]}))
    with psycopg.connect(catalog_dsn) as conn:
        assert conn.execute("select sum(times_matched) from verified_queries").fetchone()[0] == 0
