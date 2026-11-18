import pytest
from pydantic import ValidationError

from config import Settings


def test_missing_database_url_fails_fast(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_bad_database_url_rejected_without_leaking_it(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:supersecret@h:notaport/db x=")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "supersecret" not in str(exc.value)


def test_defaults_and_cors_list(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")
    s = Settings(_env_file=None)
    assert (s.free_tier_message_limit, s.session_ttl_hours, s.max_tool_calls_per_turn, s.max_output_tokens) == (
        4, 24, 6, 1500,
    )  # fmt: skip
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]
    assert "p@" not in repr(s)


def test_mcp_public_defaults_off_and_needs_a_long_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("MCP_PUBLIC", raising=False)
    assert Settings(_env_file=None).mcp_public is False
    monkeypatch.setenv("MCP_PUBLIC", "true")
    with pytest.raises(ValidationError, match="MCP_API_KEY"):
        Settings(_env_file=None)
    monkeypatch.setenv("MCP_API_KEY", "short")
    with pytest.raises(ValidationError, match="MCP_API_KEY"):
        Settings(_env_file=None)
    monkeypatch.setenv("MCP_API_KEY", "x" * 32)
    assert Settings(_env_file=None).mcp_public is True
