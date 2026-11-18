"""Step 3: an MCP client lists and calls each tool against the fixture catalog.

In-process for the detailed checks, plus the real stdio and streamable HTTP transports.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from mcp_server.server import create_server

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"search_models", "get_model", "compare_models"}


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content


@pytest.fixture
def server(catalog_dsn):
    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(
            catalog_dsn, autocommit=True, options="-c default_transaction_read_only=on"
        ) as conn:
            yield conn

    return create_server(connection=connect)


async def test_lists_the_three_read_only_tools(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.description and t.output_schema
        assert t.annotations.read_only_hint is True


async def test_calls_each_tool(server):
    async with Client(server) as client:
        s = payload(
            await client.call_tool(
                "search_models",
                {"modality": "video", "required_capabilities": [{"name": "output_languages", "value": "hi"}]},
            )
        )
        assert [m["slug"] for m in s["matches"]] == ["testco-alpha"]
        assert s["matches"][0]["facts"][0]["verification_status"] == "verified"
        assert [m["missing_capabilities"] for m in s["unconfirmed"]] == [["output_languages"]]

        g = payload(await client.call_tool("get_model", {"slug": "testco-beta"}))
        assert g["status"] == "found"
        assert {f["name"]: f["value"] for f in g["model"]["facts"]}["api_available"] is False

        c = payload(await client.call_tool("compare_models", {"slugs": ["testco-alpha", "testco-gamma"]}))
        assert c["models"][1]["not_recorded"] == ["api_available", "max_resolution", "output_languages", "pricing"]


async def test_invalid_arguments_return_a_tool_error(server):
    async with Client(server) as client:
        r = await client.call_tool("compare_models", {"slugs": ["testco-alpha"]})
    assert r.is_error
    assert "slugs" in r.content[0].text


def _env(dsn):
    return {**os.environ, "DATABASE_URL": dsn, "PYTHONPATH": str(ROOT)}


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


@pytest.fixture
def http_server(catalog_dsn):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server", "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
        env=_env(catalog_dsn),
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode())
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


async def test_streamable_http_transport(http_server):
    async with Client(http_server) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS
        c = payload(
            await client.call_tool("compare_models", {"slugs": ["testco-alpha", "testco-beta", "testco-gamma"]})
        )
    assert [m["slug"] for m in c["models"]] == ["testco-alpha", "testco-beta", "testco-gamma"]
    json.dumps(c)  # plain JSON all the way through
