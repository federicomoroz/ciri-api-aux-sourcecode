"""
Dependency injection via FastAPI lifespan.

All services are initialized once at startup and stored in app.state.
Routes access them via dependency functions.
"""

import logging
import os
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from qdrant_client import QdrantClient
from .rag.embedder import FastEmbedder

from .analysis.analyzer import Analyzer
from .config import Settings
from .data.db import Database
from .data.loader import init_sqlite, load_excel
from .llm.client import AnthropicClient, LLMClient
from .observability.tracer import LangfuseTracer, NoOpTracer, Tracer
from .rag.indexer import QdrantIndexer
from .rag.retriever import QdrantRetriever
from .rag.updater import RAGUpdater
from .reports.generator import ReportGenerator
from .services.feedback import FeedbackService
from .services.resolution import ResolutionService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests pre-populate app.state before TestClient starts — skip auto-init
    if hasattr(app.state, "db"):
        yield
        return

    settings = Settings()

    # SQLite: init and seed if not exists
    db_path = settings.sqlite_path
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    if not os.path.exists(db_path):
        data = load_excel(settings.data_file_path)
        init_sqlite(db_path, data)

    db = Database(db_path)

    # Qdrant client
    qdrant = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    # Embedding model (loaded once, ~25MB, shared across requests)
    embedder = FastEmbedder(settings.embedding_model)

    # Tracer
    tracer = (
        LangfuseTracer(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        if settings.langfuse_enabled
        else NoOpTracer()
    )

    # LLM client
    llm = AnthropicClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        tracer=tracer,
    )

    # RAG components
    indexer = QdrantIndexer(
        qdrant,
        embedder,
        policies_collection=settings.qdrant_policies_collection,
        cases_collection=settings.qdrant_cases_collection,
        cache_collection=settings.qdrant_cache_collection,
    )
    retriever = QdrantRetriever(
        qdrant,
        embedder,
        policies_collection=settings.qdrant_policies_collection,
        cases_collection=settings.qdrant_cases_collection,
        cache_collection=settings.qdrant_cache_collection,
    )
    updater = RAGUpdater(indexer, db, judge_threshold=settings.judge_auto_index_threshold)

    # Ensure Qdrant collections exist and are indexed
    indexer.ensure_collections()
    all_policies = db.get_all_policies()
    all_cases = db.get_all_cases()
    all_txns = db.get_all_transactions()

    # Index only if collections are empty
    try:
        policies_count = qdrant.get_collection(settings.qdrant_policies_collection).points_count
        if policies_count == 0 and all_policies:
            indexer.index_policies(all_policies)
    except Exception as e:
        logger.warning("Could not check policies collection count, re-indexing: %s", e)
        if all_policies:
            indexer.index_policies(all_policies)

    try:
        cases_count = qdrant.get_collection(settings.qdrant_cases_collection).points_count
        if cases_count == 0 and all_cases:
            indexer.index_historical_cases(all_cases, all_txns)
    except Exception as e:
        logger.warning("Could not check cases collection count, re-indexing: %s", e)
        if all_cases:
            indexer.index_historical_cases(all_cases, all_txns)

    # Analyzer
    analyzer = Analyzer(db)

    # Service layer
    resolution_service = ResolutionService(llm, tracer)
    feedback_service = FeedbackService(db, updater, tracer)

    # Report generator
    report_generator = ReportGenerator()

    # Store everything in app.state
    app.state.settings = settings
    app.state.db = db
    app.state.qdrant = qdrant
    app.state.embedder = embedder
    app.state.llm = llm
    app.state.indexer = indexer
    app.state.retriever = retriever
    app.state.updater = updater
    app.state.analyzer = analyzer
    app.state.tracer = tracer
    app.state.report_generator = report_generator
    app.state.resolution_service = resolution_service
    app.state.feedback_service = feedback_service

    yield

    qdrant.close()


# --- Dependency functions ---

def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_qdrant(request: Request) -> QdrantClient:
    return request.app.state.qdrant


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_retriever(request: Request) -> QdrantRetriever:
    return request.app.state.retriever


def get_indexer(request: Request) -> QdrantIndexer:
    return request.app.state.indexer


def get_updater(request: Request) -> RAGUpdater:
    return request.app.state.updater


def get_analyzer(request: Request) -> Analyzer:
    return request.app.state.analyzer


def get_tracer(request: Request) -> Tracer:
    return request.app.state.tracer


def get_report_generator(request: Request) -> ReportGenerator:
    return request.app.state.report_generator


def get_resolution_service(request: Request) -> ResolutionService:
    return request.app.state.resolution_service


def get_feedback_service(request: Request) -> FeedbackService:
    return request.app.state.feedback_service
