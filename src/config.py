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
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )
    
    #Anthropic Claude
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    claude_model: str = Field(default="claude-sonnet-4-20250514")
    llm_temperature: float = Field(default=0.0)
    llm_max_tokens: int = Field(default=4096)
    llm_retry_attempts: int = Field(default=3)
    llm_timeout: int = Field(default=60)
    
    #Neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="supplychainkg")
    neo4j_database: str = Field(default="neo4j")
    
    #API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_reload: bool = Field(default=True)
    api_log_level: str = Field(default="info")
    api_key: str = Field(default="dev-api-key")
    secret_key: str = Field(default="change-me-in-production")
    
    #Application
    app_name: str = Field(default="Supply Chain Knowledge Graph")
    app_version: str = Field(default="0.1.0")
    debug: bool = Field(default=True)
    
    #Graph
    graph_batch_size: int = Field(default=100)
    graph_query_timeout: int = Field(default=30)
    
    #Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    
    #Optional
    redis_host: Optional[str] = Field(default=None)
    redis_port: Optional[int] = Field(default=None)
    redis_db: Optional[int] = Field(default=0)

    #DigiKey API (optional — only needed for catalog ingestion)
    digikey_client_id: Optional[str] = Field(default=None)
    digikey_client_secret: Optional[str] = Field(default=None)

    #OAuth2 / JWT (added by auth system)
    jwt_secret_key: Optional[str] = Field(default=None)
    jwt_algorithm: str = Field(default="HS256")
    jwt_expire_minutes: int = Field(default=60)
    refresh_expire_days: int = Field(default=7)
    oauth2_client_id: Optional[str] = Field(default=None)
    oauth2_client_secret_hash: Optional[str] = Field(default=None)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
