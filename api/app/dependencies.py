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
from .llm.client import AnthropicClient
from .observability.tracer import LangfuseTracer, NoOpTracer
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

    # --- Phase 1: SQLite (ephemeral on Render free tier, recreated from Excel if missing) ---
    db_path = settings.sqlite_path
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    if not os.path.exists(db_path):
        data = load_excel(settings.data_file_path)
        init_sqlite(db_path, data)
        del data

    db = Database(db_path)
    db.ensure_report_cache_table()

    # --- Phase 2: Connect external services ---
    qdrant = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )
    embedder = FastEmbedder(settings.embedding_model)
    tracer = (
        LangfuseTracer(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        if settings.langfuse_enabled
        else NoOpTracer()
    )
    llm = AnthropicClient(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        tracer=tracer,
        max_retries=settings.llm_max_retries,
    )

    # --- Phase 3: RAG setup ---
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

    # --- Phase 4: Conditional indexing ---
    indexer.ensure_collections()
    try:
        needs_policies = qdrant.get_collection(settings.qdrant_policies_collection).points_count == 0
        needs_cases = qdrant.get_collection(settings.qdrant_cases_collection).points_count == 0
    except Exception as e:
        logger.warning("Could not check Qdrant collection counts, will re-index: %s", e)
        needs_policies = needs_cases = True

    if needs_policies or needs_cases:
        logger.info("Qdrant collections empty — indexing from SQLite (first-run or reset)")
        policies = db.get_all_policies() if needs_policies else []
        cases = db.get_all_cases() if needs_cases else []
        txns = db.get_all_transactions() if needs_cases else []
        if needs_policies and policies:
            indexer.index_policies(policies)
        if needs_cases and cases:
            indexer.index_historical_cases(cases, txns)
        del policies, cases, txns

    # Service layer
    analyzer = Analyzer(db)
    resolution_service = ResolutionService(llm, tracer)
    feedback_service = FeedbackService(db, updater, tracer)
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


# --- Dependency functions (only those actually used by routes) ---

def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_qdrant(request: Request) -> QdrantClient:
    return request.app.state.qdrant


def get_retriever(request: Request) -> QdrantRetriever:
    return request.app.state.retriever


def get_updater(request: Request) -> RAGUpdater:
    return request.app.state.updater


def get_analyzer(request: Request) -> Analyzer:
    return request.app.state.analyzer


def get_report_generator(request: Request) -> ReportGenerator:
    return request.app.state.report_generator


def get_resolution_service(request: Request) -> ResolutionService:
    return request.app.state.resolution_service


def get_feedback_service(request: Request) -> FeedbackService:
    return request.app.state.feedback_service
