from pydantic_settings import BaseSettings

from .domain.constants import (
    EMBEDDING_DIM,
    JUDGE_AUTO_INDEX_THRESHOLD,
    LLM_DEFAULT_MAX_RETRIES,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_DEFAULT_TEMPERATURE,
    SEMANTIC_CACHE_THRESHOLD,
)


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"
    llm_temperature: float = LLM_DEFAULT_TEMPERATURE
    llm_max_tokens: int = LLM_DEFAULT_MAX_TOKENS
    llm_max_retries: int = LLM_DEFAULT_MAX_RETRIES

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_policies_collection: str = "policies"
    qdrant_cases_collection: str = "historical_cases"
    qdrant_cache_collection: str = "_semantic_cache"

    # Embeddings (Voyage AI)
    voyage_api_key: str = ""
    embedding_model: str = "voyage-multilingual-2"
    embedding_dim: int = EMBEDDING_DIM

    # SQLite
    sqlite_path: str = "data/chargeback.db"
    data_file_path: str = "data/Similación_dataset_contracargos_.xlsx"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = False

    # Semantic cache
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = SEMANTIC_CACHE_THRESHOLD

    # Judge
    judge_auto_index_threshold: float = JUDGE_AUTO_INDEX_THRESHOLD

    # n8n
    n8n_base_url: str = "http://n8n:5678"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_prefix": "CB_", "extra": "ignore"}


def create_settings() -> Settings:
    """Factory for Settings. Named differently from dependencies.get_settings to avoid confusion."""
    return Settings()
