"""Settings read from the environment (or a local .env file).

Required values have no default, so a missing one fails at startup with a clear
pydantic error instead of failing later on first use.
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: SecretStr

    # Supabase (agent API, step 5)
    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None

    # Bedrock (step 4+). Blank until the model is chosen from the eval comparison.
    aws_region: str = "us-east-1"
    bedrock_model_id: str | None = None

    # Agent behaviour
    free_tier_message_limit: int = Field(default=4, ge=0)
    session_ttl_hours: int = Field(default=24, ge=1)
    max_tool_calls_per_turn: int = Field(default=6, ge=1, le=20)
    max_output_tokens: int = Field(default=1500, ge=1)

    # Agent API
    cors_origins: str = "http://localhost:3000"
    mock_agent: bool = False

    # MCP server
    mcp_server_url: str = "http://localhost:8001/mcp"

    def database_dsn(self) -> str:
        # Validate the shape without keeping the secret in a non-Secret field.
        PostgresDsn(self.database_url.get_secret_value())
        return self.database_url.get_secret_value()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
