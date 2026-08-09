"""
Core analysis routes: /resolve and /judge.

Thin HTTP handlers — all orchestration logic lives in ResolutionService.
"""

import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..data.precomputados import analisis_demo, caso_mas_cercano_en_riesgo
from ..dependencies import get_modelos_service, get_resolution_service, get_settings
from ..domain.models import JudgeRequest, JudgeResponse, ResolveRequest, ResolveResponse
from ..services.modelos import ModelosService
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


def _servicio_efectivo(
    settings: Settings, modelos: ModelosService, base: ResolutionService,
) -> tuple[ResolutionService, bool]:
    """El servicio que corresponde a este modo, y si corrio en modo demo.

    **Esta es la puerta por la que entra n8n**, que llama a la API directamente y
    no pasa por el panel. El modo produccion vive en el panel y dura lo que dura
    la sesion; el default del servidor —`CB_DEMO_MODE`, que arranca en true— es
    el que decide que le toca a todo lo demas.

    El servicio lo fabrica `ModelosService` y no esta funcion: quien necesita
    hablarle al modelo lo pide, nadie ensambla clientes por su cuenta.
    """
    if not settings.demo_mode:
        return base, False
    demo = modelos.servicio(demo=True)
    return (demo, True) if demo is not None else (base, False)


def _demo_de(
    settings: Settings, tx_id: str, parte: str, tx_data: dict | None = None
) -> dict | None:
    """Lo que el modelo respondio para ese caso, o para el mas parecido en riesgo.

    Solo aplica en modo demo. Sirve para que el workflow de n8n se pueda correr
    entero sin gastar: el contexto y el informe se hacen de verdad, y esta es la
    unica pieza pregrabada.

    Si el caso pedido no esta guardado, se responde con el mas cercano en riesgo
    y se marca cual era el pedido, para que el informe no mezcle los dos.
    """
    if not settings.demo_mode:
        return None
    carpeta = settings.demo_reports_path

    guardado = analisis_demo(carpeta, tx_id)
    if guardado and parte in guardado:
        logger.warning(
            "MODO DEMO: %s de %s se responde con el analisis guardado. No se llamo al modelo.",
            parte, tx_id,
        )
        return {**guardado[parte], "demo": True}

    ejemplo = caso_mas_cercano_en_riesgo(carpeta, tx_data or {})
    guardado = analisis_demo(carpeta, ejemplo) if ejemplo else None
    if not guardado or parte not in guardado:
        return None
    logger.warning(
        "MODO DEMO: %s no esta guardado; %s se responde con el de %s, el caso de "
        "ejemplo mas cercano en riesgo.", tx_id, parte, ejemplo,
    )
    return {**guardado[parte], "demo": True, "demo_ejemplo_de": tx_id}


@router.post("/resolve", status_code=200)
def resolve(
    req: ResolveRequest,
    service: ResolutionService = Depends(get_resolution_service),
    settings: Settings = Depends(get_settings),
    modelos: ModelosService = Depends(get_modelos_service),
) -> ResolveResponse:
    """Full resolution pipeline: policy eval -> log summary -> resolution synthesis -> guardrails."""
    tx_id = req.transaction_id or (req.tx_data.get("id", "") if req.tx_data else "")

    servicio, en_demo = _servicio_efectivo(settings, modelos, service)
    if not en_demo:
        # Sin modelo de demo configurado, el modo demo recita en vez de correr.
        guardado = _demo_de(settings, tx_id, "resolution", req.tx_data)
        if guardado is not None:
            return guardado

    result = servicio.resolve(req.to_context())
    if en_demo:
        # Que corrio de verdad no lo vuelve la configuracion documentada: quien
        # lo consuma tiene que poder distinguirlo. Y con que modelo corrio viaja
        # con el resultado, porque el informe lo declara y no todos los que lo
        # piden pasan por el panel — n8n llama derecho a la API.
        result = {**result, "demo": True, "demo_modelo": modelos.modelo_demo()}
    return result


@router.post("/judge", status_code=200)
def judge(
    req: JudgeRequest,
    service: ResolutionService = Depends(get_resolution_service),
    settings: Settings = Depends(get_settings),
    modelos: ModelosService = Depends(get_modelos_service),
) -> JudgeResponse:
    """LLM-as-Judge: evaluate resolution quality across 5 criteria."""
    tx_id = (req.resolution or {}).get("transaction_id", "")
    servicio, en_demo = _servicio_efectivo(settings, modelos, service)
    if not en_demo:
        guardado = _demo_de(settings, tx_id, "judge")
        if guardado is not None:
            return guardado

    resultado = servicio.judge(
        resolution=req.resolution,
        full_context=req.full_context,
    )
    return {**resultado, "demo": True} if en_demo else resultado
