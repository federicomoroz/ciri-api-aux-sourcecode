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
from .llm.manager import LLMManager
from .observability.contacto_n8n import ContactoN8n
from .observability.tracer import LangfuseTracer, NoOpTracer
from .rag.embedder import FastEmbedder
from .rag.indexer import QdrantIndexer
from .rag.retriever import QdrantRetriever
from .rag.updater import RAGUpdater
from .reports.generator import ReportGenerator
from .services.feedback import FeedbackService
from .services.langfuse_stats import LangfuseStatsService
from .services.modelos import ModelosService
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
    db.ensure_policies_semantics()
    db.ensure_modelos_table()
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
    manager = LLMManager(settings, tracer)
    llm = manager.cliente(settings.llm_provider or "anthropic", settings.llm_model)
    return {
        "qdrant": QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=30,
        ),
        "embedder": FastEmbedder(settings.embedding_model, api_key=settings.voyage_api_key),
        "tracer": tracer,
        "llm": llm,
        "manager": manager,
        "llm_resolution": (
            manager.cliente(settings.llm_provider or "anthropic", settings.llm_model_resolution)
            if settings.llm_model_resolution else llm
        ),
    }


def _indice_de_politicas_viejo(qdrant: QdrantClient, coleccion: str) -> bool:
    """Si los puntos indexados no traen la semantica de la politica, estan viejos.

    `puede_bloquear` y `sla_dias` se agregaron despues de que las colecciones ya
    existieran. El guardrail tiene un respaldo para ese caso, pero mientras el
    indice no se actualice «la politica decide si puede bloquear» sigue siendo
    cierto sólo a medias. Reindexar cuesta una llamada de embeddings sobre un
    corpus de 17 documentos: es mas barato que la ambiguedad.
    """
    try:
        puntos, _ = qdrant.scroll(coleccion, limit=1, with_payload=True)
    except Exception as e:
        logger.warning("No se pudo inspeccionar %s: %s", coleccion, e)
        return False
    if not puntos:
        return False
    return "puede_bloquear" not in (puntos[0].payload or {})


def _indexar_si_hace_falta(indexer: QdrantIndexer, qdrant: QdrantClient, db: Database, settings: Settings) -> None:
    """Indexa en el primer arranque, o cuando el indice quedo desactualizado."""
    indexer.ensure_collections()
    try:
        faltan_politicas = qdrant.get_collection(settings.qdrant_policies_collection).points_count == 0
        faltan_casos = qdrant.get_collection(settings.qdrant_cases_collection).points_count == 0
    except Exception as e:
        logger.warning("Could not check Qdrant collection counts, will re-index: %s", e)
        faltan_politicas = faltan_casos = True

    if not faltan_politicas and _indice_de_politicas_viejo(
        qdrant, settings.qdrant_policies_collection
    ):
        logger.info("Las politicas indexadas no traen `puede_bloquear`: se reindexan")
        faltan_politicas = True

    if not (faltan_politicas or faltan_casos):
        return

    logger.info("Indexando desde SQLite (primer arranque, reset, o indice desactualizado)")
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
    # Qdrant caido no puede impedir que la API arranque. El free tier suspende
    # los clusters tras una semana sin uso, y esta app corre en un Render que
    # reinicia el contenedor cada vez que despierta: sin esto, una suspension
    # deja el servicio entero sin levantar. Los caminos que no dependen del
    # vector store —entre ellos el modo demo— tienen que seguir respondiendo,
    # y los que si dependen fallan solos, cada uno con su error.
    try:
        _indexar_si_hace_falta(indexer, qdrant, db, settings)
    except Exception as e:
        logger.error(
            "Qdrant no responde al arrancar (%s): la API levanta igual, pero las "
            "busquedas semanticas van a fallar hasta que vuelva", e,
        )

    analyzer = Analyzer(db)
    # La eleccion de modelo por paso vive en SQLite y el panel la edita; este
    # servicio la traduce en clientes y los renueva cuando cambia.
    modelos = ModelosService(db, settings, externos["manager"])
    # Se le pasa el servicio y no los clientes: asi `/api/analyze/*` —lo que
    # llama n8n— toma el modelo vigente en cada peticion, y cambiarlo desde el
    # panel no exige reiniciar.
    resolution_service = ResolutionService(tracer=tracer, modelos=modelos)
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
    app.state.modelos_service = modelos
    app.state.langfuse_stats_service = LangfuseStatsService(tracer, settings.llm_model)
    app.state.contacto_n8n = ContactoN8n()

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


def get_modelos_service(request: Request) -> ModelosService:
    return request.app.state.modelos_service


def get_langfuse_stats_service(request: Request) -> LangfuseStatsService:
    return request.app.state.langfuse_stats_service
