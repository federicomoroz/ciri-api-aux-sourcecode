from pydantic_settings import BaseSettings

from .domain.constants import (
    EMBEDDING_DIM,
    JUDGE_AUTO_INDEX_THRESHOLD,
    LLM_DEFAULT_MAX_RETRIES,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_DEFAULT_TEMPERATURE,
)


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_model_resolution: str = ""  # optional: stronger model for synthesis (call 2)
    llm_temperature: float = LLM_DEFAULT_TEMPERATURE
    llm_max_tokens: int = LLM_DEFAULT_MAX_TOKENS
    llm_max_retries: int = LLM_DEFAULT_MAX_RETRIES

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_policies_collection: str = "policies"
    qdrant_cases_collection: str = "historical_cases"

    # Embeddings (Voyage AI)
    voyage_api_key: str = ""
    embedding_model: str = "voyage-multilingual-2"
    embedding_dim: int = EMBEDDING_DIM

    # SQLite
    sqlite_path: str = "data/chargeback.db"
    data_file_path: str = "data/Similación_dataset_contracargos_.xlsx"
    # Modo demo: los casos de ejemplo se sirven ya resueltos y NO se llama al
    # modelo, para que evaluar el sistema no consuma la cuenta de nadie. Quien
    # trae su propia API key corre el pipeline completo igual.
    demo_mode: bool = True
    demo_reports_path: str = "data/informes_demo"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = False

    # Semantic cache
    report_cache_enabled: bool = True

    # Judge
    judge_auto_index_threshold: float = JUDGE_AUTO_INDEX_THRESHOLD

    # n8n
    # Vacio = no hay instancia asociada. El panel pide una en vez de
    # apuntar a un host que solo existe dentro de docker-compose.
    n8n_base_url: str = ""
    n8n_form_path: str = ""

    # Security
    admin_api_key: str = ""  # protects /api/* endpoints (except /api/panel/*)

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_prefix": "CB_", "extra": "ignore"}

