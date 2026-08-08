"""El canvas y el pipeline directo tienen que juntar el mismo contexto.

La orquestacion esta escrita dos veces: los nodos de `workflow_ciri_agent.json`
y `PipelineService`. Es una decision tomada a proposito —n8n es el entregable,
el pipeline directo es lo que permite probar sin montar n8n— pero deja una
invariante que hasta ahora vivia solo en un comentario: **los dos caminos tienen
que reunir las mismas fuentes antes de sintetizar.**

Ya divergieron. El canvas consultaba el SLA antes de `Sintetizar Resolucion` y
el pipeline directo no, asi que el mismo caso salia con compensacion por un lado
y sin ella por el otro. Se arreglo agregando `_sla_del_caso` y se dejo escrito
en un comentario de `_submit_context_futures`. Un comentario no falla.

Este test lee el workflow como especificacion: saca del grafo que nodos
alimentan al merge de contexto y verifica que el pipeline pida lo mismo. Agregar
una fuente al canvas y olvidarla en el pipeline —o al reves— lo pone en rojo.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.app.domain.models import AnalyzeRequest
from api.app.services.pipeline import PipelineService

RAIZ = Path(__file__).resolve().parents[2]
WORKFLOW = json.loads(
    (RAIZ / "n8n/workflow_ciri_agent.json").read_text(encoding="utf-8")
)
MERGE = "Merge — Contexto Paralelo"

# Que fuente del canvas cubre cada etiqueta del pipeline. Politicas y casos
# comparten etiqueta porque comparten llamada: `search_policies_and_cases`
# calcula el embedding una sola vez para las dos colecciones.
COBERTURA: dict[str, str] = {
    "Obtener Logs": "logs",
    "Buscar Políticas": "rag",
    "Buscar Casos Similares": "rag",
    "Riesgo del Comercio": "merchant",
    "Historial del Cliente": "client",
    "Verificar SLA": "sla",
}


def _fuentes_del_canvas() -> set[str]:
    """Los nodos cuya salida entra al merge de contexto, leidos del grafo."""
    fuentes = set()
    for origen, conns in WORKFLOW["connections"].items():
        for salidas in conns.get("main", []):
            for c in salidas or []:
                if c.get("node") == MERGE:
                    fuentes.add(origen)
    return fuentes


class _ExecutorQueAnota:
    """Un executor que no ejecuta: solo anota que se le pidio."""

    def submit(self, fn, *a, **kw):
        return MagicMock()


@pytest.fixture
def etiquetas_del_pipeline() -> set[str]:
    """Las fuentes que `_submit_context_futures` realmente pide."""
    servicio = PipelineService(
        db=MagicMock(), retriever=MagicMock(), analyzer=MagicMock(),
        resolution_svc=MagicMock(), report_gen=MagicMock(), sla=MagicMock(),
    )
    pedidas = servicio._submit_context_futures(
        _ExecutorQueAnota(),
        {"payment_method": "Cripto", "country": "ARG", "fraud_score": 8,
         "merchant": "X", "client_id": "C1", "channel": "web", "amount_usd": 100.0},
        AnalyzeRequest(transaction_id="TXN-00051", motivo="No reconoce la compra"),
    )
    return set(pedidas.values())


def test_el_canvas_no_tiene_una_fuente_que_el_pipeline_ignore(etiquetas_del_pipeline):
    """La direccion en la que ya fallo: n8n consultaba el SLA y el pipeline no."""
    huerfanas = []
    for nodo in sorted(_fuentes_del_canvas()):
        etiqueta = COBERTURA.get(nodo)
        if etiqueta is None:
            huerfanas.append(f"{nodo} (sin entrada en COBERTURA)")
        elif etiqueta not in etiquetas_del_pipeline:
            huerfanas.append(f"{nodo} -> '{etiqueta}', que el pipeline no pide")
    assert not huerfanas, (
        "el canvas reune contexto que el pipeline directo no: los dos caminos "
        f"pueden dar resultados distintos para el mismo caso — {huerfanas}"
    )


def test_el_pipeline_no_pide_algo_que_el_canvas_no_reune(etiquetas_del_pipeline):
    """La direccion inversa: el informe del panel se apoyaria en datos que el
    informe de n8n no tiene."""
    cubiertas = {COBERTURA[n] for n in _fuentes_del_canvas() if n in COBERTURA}
    sobrantes = etiquetas_del_pipeline - cubiertas
    assert not sobrantes, (
        f"el pipeline directo reune {sorted(sobrantes)} y el canvas no tiene "
        "un nodo equivalente"
    )


def test_la_tabla_de_cobertura_no_nombra_nodos_que_ya_no_existen():
    """Si el canvas renombra un nodo, la tabla queda apuntando al vacio y los
    dos tests de arriba pasan sin mirar nada."""
    nombres = {n["name"] for n in WORKFLOW["nodes"]}
    fantasmas = sorted(set(COBERTURA) - nombres)
    assert not fantasmas, f"COBERTURA nombra nodos inexistentes: {fantasmas}"
