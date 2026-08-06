"""
Dependency injection via FastAPI lifespan.

All services are initialized once at startup and stored in app.state.
Routes access them via dependency functions.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from qdrant_client import QdrantClient

from .analysis.analyzer import Analyzer
from .config import Settings
from .data.db import Database
from .data.loader import init_sqlite, load_excel
from .llm.client import AnthropicClient
from .observability.tracer import LangfuseTracer, NoOpTracer
from .rag.embedder import FastEmbedder
from .rag.indexer import QdrantIndexer
from .rag.retriever import QdrantRetriever
from .rag.updater import RAGUpdater
from .reports.generator import ReportGenerator
from .services.feedback import FeedbackService
from .services.langfuse_stats import LangfuseStatsService
from .services.pipeline import PipelineService
from .services.resolution import ResolutionService

logger = logging.getLogger(__name__)


def _preparar_sqlite(settings: Settings) -> Database:
    """SQLite es efimero en el free tier de Render: si no esta, se recrea del Excel."""
    db_path = settings.sqlite_path
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    if not os.path.exists(db_path):
        try:
            data = load_excel(settings.data_file_path)
            init_sqlite(db_path, data)
            del data
        except Exception:
            logger.error("Failed to initialize SQLite from Excel", exc_info=True)
            raise

    db = Database(db_path)
    db.ensure_report_cache_table()
    db.ensure_alerts_table()
    return db


def _conectar_servicios(settings: Settings) -> dict:
    """Clientes de los servicios externos: Qdrant, embeddings, trazas y modelo."""
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
    return {
        "qdrant": QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=30,
        ),
        "embedder": FastEmbedder(settings.embedding_model, api_key=settings.voyage_api_key),
        "tracer": tracer,
        "llm": llm,
        "llm_resolution": (
            AnthropicClient(
                api_key=settings.anthropic_api_key,
                model=settings.llm_model_resolution,
                tracer=tracer,
                max_retries=settings.llm_max_retries,
            )
            if settings.llm_model_resolution
            else llm
        ),
    }


def _indexar_si_hace_falta(indexer: QdrantIndexer, qdrant: QdrantClient, db: Database, settings: Settings) -> None:
    """Indexa en el primer arranque. Si Qdrant ya tiene datos, no hace nada."""
    indexer.ensure_collections()
    try:
        faltan_politicas = qdrant.get_collection(settings.qdrant_policies_collection).points_count == 0
        faltan_casos = qdrant.get_collection(settings.qdrant_cases_collection).points_count == 0
    except Exception as e:
        logger.warning("Could not check Qdrant collection counts, will re-index: %s", e)
        faltan_politicas = faltan_casos = True

    if not (faltan_politicas or faltan_casos):
        return

    logger.info("Qdrant collections empty — indexing from SQLite (first-run or reset)")
    if faltan_politicas:
        politicas = db.get_all_policies()
        if politicas:
            indexer.index_policies(politicas)
    if faltan_casos:
        casos, txns = db.get_all_cases(), db.get_all_transactions()
        if casos:
            indexer.index_historical_cases(casos, txns)


def _ya_cableado(app: FastAPI) -> bool:
    """Si alguien ya dejo los servicios en app.state, no hay nada que construir.

    Lo usan los tests, pero no es un atajo para tests: es el punto de extension
    para montar la app con dependencias propias sin tocar este modulo.
    """
    return hasattr(app.state, "db")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _ya_cableado(app):
        yield
        return

    settings = Settings()
    db = _preparar_sqlite(settings)
    externos = _conectar_servicios(settings)
    qdrant, embedder, tracer = externos["qdrant"], externos["embedder"], externos["tracer"]

    indexer = QdrantIndexer(
        qdrant, embedder,
        policies_collection=settings.qdrant_policies_collection,
        cases_collection=settings.qdrant_cases_collection,
    )
    retriever = QdrantRetriever(
        qdrant, embedder,
        policies_collection=settings.qdrant_policies_collection,
        cases_collection=settings.qdrant_cases_collection,
    )
    updater = RAGUpdater(indexer, db, judge_threshold=settings.judge_auto_index_threshold)
    _indexar_si_hace_falta(indexer, qdrant, db, settings)

    analyzer = Analyzer(db)
    resolution_service = ResolutionService(
        externos["llm"], tracer, llm_resolution=externos["llm_resolution"],
    )
    report_generator = ReportGenerator()

    app.state.settings = settings
    app.state.db = db
    app.state.qdrant = qdrant
    app.state.embedder = embedder
    app.state.llm = externos["llm"]
    app.state.indexer = indexer
    app.state.retriever = retriever
    app.state.updater = updater
    app.state.analyzer = analyzer
    app.state.tracer = tracer
    app.state.report_generator = report_generator
    app.state.resolution_service = resolution_service
    app.state.feedback_service = FeedbackService(db, updater, tracer)
    app.state.pipeline_service = PipelineService(
        db, retriever, analyzer, resolution_service, report_generator,
    )
    app.state.langfuse_stats_service = LangfuseStatsService(tracer, settings.llm_model)

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


def get_pipeline_service(request: Request) -> PipelineService:
    return request.app.state.pipeline_service


def get_langfuse_stats_service(request: Request) -> LangfuseStatsService:
    return request.app.state.langfuse_stats_service
