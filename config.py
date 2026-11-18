"""Settings read from the environment (or a local .env file).

Required values have no default, so a missing one fails at startup with a clear
pydantic error instead of failing later on first use.
"""

from __future__ import annotations

from functools import lru_cache

from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    # Database
    database_url: SecretStr

    # Supabase (agent API, step 5)
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None

    # Bedrock (step 4+). Blank until the model is chosen from the eval comparison.
    aws_region: str = "us-east-1"
    bedrock_model_id: str | None = None

    # Tier 1 and the semantic layer
    verified_query_floor: float = Field(default=0.15, ge=0, le=1)
    semantic_layer_ttl_seconds: int = Field(default=3600, ge=0)

    # Agent behaviour
    free_tier_message_limit: int = Field(default=4, ge=0)
    session_ttl_hours: int = Field(default=24, ge=1)
    max_tool_calls_per_turn: int = Field(default=6, ge=1, le=20)
    max_output_tokens: int = Field(default=1500, ge=1)

    # Agent API
    cors_origins: str = "http://localhost:3000"
    mock_agent: bool = False

    # MCP server. Phase one: not public (loopback only, no key). If MCP_PUBLIC is ever true, every HTTP
    # request must carry MCP_API_KEY as a bearer token.
    mcp_server_url: str = "http://localhost:8001/mcp"
    mcp_public: bool = False
    mcp_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _public_mcp_needs_key(self) -> Settings:
        key = self.mcp_api_key.get_secret_value() if self.mcp_api_key else ""
        if self.mcp_public and len(key) < 32:
            raise ValueError("MCP_PUBLIC=true requires MCP_API_KEY of at least 32 characters")
        return self

    @field_validator("database_url")
    @classmethod
    def _parseable_dsn(cls, v: SecretStr) -> SecretStr:
        # URL or key=value form, as libpq accepts; the error never echoes the secret.
        try:
            conninfo_to_dict(v.get_secret_value())
        except Exception:
            raise ValueError("DATABASE_URL is not a valid PostgreSQL connection string") from None
        return v

    def database_dsn(self) -> str:
        return self.database_url.get_secret_value()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
