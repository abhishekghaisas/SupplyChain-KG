"""
Configuration management using Pydantic settings.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    # ── Anthropic Claude ──────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    claude_model: str = Field(default="claude-haiku-4-5-20251001")
    llm_temperature: float = Field(default=0.0)
    llm_max_tokens: int = Field(default=4096)
    llm_retry_attempts: int = Field(default=3)
    llm_timeout: int = Field(default=60)

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="supplychainkg")
    neo4j_database: str = Field(default="neo4j")

    # ── API server ────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_reload: bool = Field(default=True)
    api_log_level: str = Field(default="info")

    # ── OAuth2 / JWT ──────────────────────────────────────────────────────────
    # JWT signing secret — generate with: openssl rand -hex 32
    # Never use the default in production.
    jwt_secret_key: str = Field(
        default="change-me-in-production-use-openssl-rand-hex-32",
        description="HS256 signing secret for JWT tokens",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_expire_minutes: int = Field(default=60, description="Access token lifetime in minutes")
    refresh_expire_days: int = Field(default=7, description="Refresh token lifetime in days")

    # OAuth2 client credentials
    # client_secret_hash is a bcrypt hash of the actual secret.
    # Generate with: python -c "import bcrypt; print(bcrypt.hashpw(b'secret', bcrypt.gensalt()).decode())"  # noqa: E501
    oauth2_client_id: str = Field(
        default="supply-chain-api",
        description="OAuth2 client_id accepted at POST /auth/token",
    )
    oauth2_client_secret_hash: str = Field(
        default="$2b$12$placeholder.hash.replace.before.deploying.xxxxxxxxxxxxx",
        description="bcrypt hash of the client secret — never store the plaintext",
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = Field(default="Supply Chain Knowledge Graph")
    app_version: str = Field(default="0.1.0")
    debug: bool = Field(default=True)

    # ── Graph ─────────────────────────────────────────────────────────────────
    graph_batch_size: int = Field(default=100)
    graph_query_timeout: int = Field(default=30)

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # ── Optional ──────────────────────────────────────────────────────────────
    redis_host: Optional[str] = Field(default=None)
    redis_port: Optional[int] = Field(default=None)
    redis_db: Optional[int] = Field(default=0)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
