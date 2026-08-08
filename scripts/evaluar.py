"""Mide la calidad del agente sobre una muestra del dataset, y deja el resultado escrito.

Por que existe
--------------
El score del Juez que figura en el README —9.1/10— salio de corridas manuales
durante el desarrollo: casos elegidos a mano, de a uno, sin registrar cuantos ni
volcar los resultados a ningun lado. El numero es real y la configuracion esta
documentada, pero **no es reproducible desde el paquete**, y un numero que no se
puede auditar vale menos que uno mas bajo que si.

Este script es la respuesta a eso. Corre N casos del dataset por el pipeline
completo, junta los scores del Juez y escribe un JSON versionable con el detalle
caso por caso, la configuracion usada y la fecha. El numero que se publique
despues sale de ese archivo, y cualquiera puede volver a generarlo.

Que cuesta
----------
Tres llamadas por caso, una por paso del pipeline, con el modelo que cada paso
tenga configurado —lo que el panel edita en «Modelo por paso»—.

Con Anthropic son unos USD 0.037 por caso: veinte casos rondan los USD 0.75. El
script imprime el costo acumulado mientras corre y corta si se pasa del tope.

**Puede salir cero.** Groq, Gemini, OpenRouter, Cerebras y GitHub Models tienen
free tier y hablan el protocolo de OpenAI. Poniendo los tres pasos en uno de
ellos —desde el panel o con `CB_LLM_PROVIDER`— el dataset completo se mide sin
pagar nada. Ojo con la lectura: los prompts estan afinados para Claude y la
configuracion documentada es Haiku + Sonnet, asi que **un score medido con otro
proveedor no es el score del sistema entregado**. Sirve para verificar que el
pipeline es independiente del proveedor y como punto de comparacion.

Uso
---
    python scripts/evaluar.py                      # 20 casos al azar, semilla fija
    python scripts/evaluar.py --n 50               # 50 casos
    python scripts/evaluar.py --todos              # las 100 transacciones
    python scripts/evaluar.py --casos TXN-00051,TXN-00042
    python scripts/evaluar.py --n 30 --tope-usd 1.5
    python scripts/evaluar.py --n 20 --salida docs/evaluaciones/2026-08-07.json

Necesita la clave del proveedor configurado —`CB_ANTHROPIC_API_KEY`, o
`CB_LLM_API_KEY` para cualquier otro—, `CB_VOYAGE_API_KEY` para los embeddings,
y Qdrant en pie con las colecciones indexadas.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from api.app.analysis.analyzer import Analyzer  # noqa: E402
from api.app.analysis.sla import CalculadoraDeSLA  # noqa: E402
from api.app.config import Settings  # noqa: E402
from api.app.data import esquema  # noqa: E402
from api.app.data.db import Database  # noqa: E402
from api.app.llm.pricing import estimar_costo_usd  # noqa: E402
from api.app.observability.tracer import NoOpTracer  # noqa: E402
from api.app.rag.embedder import FastEmbedder  # noqa: E402
from api.app.rag.retriever import QdrantRetriever  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("evaluar")

SEMILLA = 20260807  # fija, para que «al azar» siga siendo reproducible
MOTIVO_POR_DEFECTO = "No reconoce la compra"
CRITERIOS = (
    "policy_consistency", "justification_quality",
    "precedent_usage", "risk_assessment", "actionability",
)


def _servicios(settings: Settings):
    from qdrant_client import QdrantClient

    from api.app.llm.manager import LLMManager
    from api.app.services.modelos import ModelosService

    db = Database(settings.sqlite_path)
    # El mismo preparado que corre la app al arrancar. Antes se llamaba a mano a
    # `db.ensure_modelos_table()`, que dejo de existir cuando el DDL salio del
    # gateway: el script quedo sin arrancar y nada lo noto, porque ningun test
    # lo ejecutaba. Pedir el preparado completo evita volver a elegir a mano
    # cual de las cinco tablas hace falta.
    esquema.preparar(db)
    qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=30)
    retriever = QdrantRetriever(
        qdrant, FastEmbedder(settings.embedding_model, api_key=settings.voyage_api_key),
        policies_collection=settings.qdrant_policies_collection,
        cases_collection=settings.qdrant_cases_collection,
    )
    tracer = NoOpTracer()
    # Se mide la configuracion que el sistema tiene puesta —la que edita el
    # panel—, no una fija: medir algo distinto de lo que corre no sirve de nada.
    # Y el servicio se pide, no se arma: la fabrica es una sola.
    modelos = ModelosService(db, settings, LLMManager(settings, tracer))
    return db, retriever, Analyzer(db), modelos.servicio(), modelos


def _contexto(db: Database, retriever: QdrantRetriever, analyzer: Analyzer, tx: dict, motivo: str):
    from api.app.domain.context import CaseContext

    caso = db.get_case_for_transaction(tx["id"]) or {}
    politicas, similares = retriever.search_policies_and_cases(
        motivo=motivo, channel=tx.get("channel", ""),
        payment_method=tx.get("payment_method", ""),
        fraud_score=int(tx.get("fraud_score", 0)), country=tx.get("country", ""),
        merchant=tx.get("merchant", ""), amount=float(tx.get("amount_usd", 0)),
    )
    return CaseContext(
        transaction=tx, motivo=motivo, cliente_vip=False,
        logs=db.get_logs_for_transaction(tx["id"]),
        policies=politicas, similar_cases=similares,
        merchant_risk=analyzer.merchant_risk_profile(tx.get("merchant", "")),
        client_history=analyzer.client_flags(tx.get("client_id", "")),
        # Sin fecha de apertura del reclamo NO se cae a la fecha de compra: entre
        # las dos pueden pasar meses y contarlas como plazo inventa un
        # incumplimiento. `check_sla` devuelve `within_sla=None` y el caso se
        # mide igual, sin afirmar lo que nadie puede sostener.
        sla=CalculadoraDeSLA(db).check_sla(
            case_open_date=caso.get("open_date") or "",
            country=tx.get("country", ""),
            case_close_date=caso.get("close_date") or None,
        ),
    )


def _elegir(db: Database, args) -> list[dict]:
    todas = sorted(db.get_all_transactions(), key=lambda t: t["id"])
    if args.casos:
        pedidos = {c.strip().upper() for c in args.casos.split(",") if c.strip()}
        elegidas = [t for t in todas if t["id"].upper() in pedidos]
        faltan = pedidos - {t["id"].upper() for t in elegidas}
        if faltan:
            sys.exit(f"no estan en el dataset: {sorted(faltan)}")
        return elegidas
    if args.todos:
        return todas
    random.Random(args.semilla).shuffle(todas)
    return todas[: args.n]


def _costo(resolucion: dict, juicio: dict, config: dict) -> float:
    """Cotiza cada llamada con el modelo de SU paso.

    Un proveedor con free tier no tiene tarifa cargada y cuenta cero, que es lo
    correcto: el punto de correrlo asi es justamente que no cuesta.
    """
    from api.app.domain.constants import PASO_JUEZ, PASO_POLITICAS, PASO_RESOLUCION

    r, j = resolucion.get("_usage", {}), juicio.get("_usage", {})

    def cotizar(paso: str, entrada: int, salida: int) -> float:
        cfg = config[paso]
        if cfg["proveedor"] != "anthropic":
            return 0.0   # free tier: no hay tarifa que aplicar
        return estimar_costo_usd(cfg["modelo"], entrada, salida)

    # `resolve` cuenta dos llamadas juntas —politicas y sintesis—: se reparten.
    mitad_in, mitad_out = r.get("input_tokens", 0) // 2, r.get("output_tokens", 0) // 2
    return (
        cotizar(PASO_POLITICAS, mitad_in, mitad_out)
        + cotizar(PASO_RESOLUCION, mitad_in, mitad_out)
        + cotizar(PASO_JUEZ, j.get("input_tokens", 0), j.get("output_tokens", 0))
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=20, help="cuantos casos al azar (por defecto 20)")
    p.add_argument("--todos", action="store_true", help="todas las transacciones del dataset")
    p.add_argument("--casos", default="", help="lista separada por comas de TXN-XXXXX")
    p.add_argument("--motivo", default=MOTIVO_POR_DEFECTO)
    p.add_argument("--semilla", type=int, default=SEMILLA)
    p.add_argument("--tope-usd", type=float, default=2.0, help="aborta si el costo lo supera")
    p.add_argument("--salida", default="", help="por defecto docs/evaluaciones/<fecha>.json")
    args = p.parse_args()

    settings = Settings()
    if not (settings.anthropic_api_key or settings.llm_api_key):
        sys.exit(
            "falta la clave del modelo: CB_ANTHROPIC_API_KEY, o CB_LLM_API_KEY si usas "
            "un proveedor con free tier (groq, gemini, openrouter, cerebras, github)"
        )

    db, retriever, analyzer, servicio, modelos = _servicios(settings)
    config_modelos = modelos.vigente()
    casos = _elegir(db, args)
    print(f"Evaluando {len(casos)} casos · tope USD {args.tope_usd} · motivo: «{args.motivo}»\n")

    filas: list[dict] = []
    gastado = 0.0
    for i, tx in enumerate(casos, 1):
        if gastado > args.tope_usd:
            print(f"\nCorte por tope de costo tras {i - 1} casos (USD {gastado:.3f}).")
            break
        arranque = time.monotonic()
        try:
            ctx = _contexto(db, retriever, analyzer, tx, args.motivo)
            resolucion = servicio.resolve(ctx)
            juicio = servicio.judge(resolution=resolucion, full_context=ctx.para_el_juez())
        except Exception as e:
            print(f"  {tx['id']}  ERROR  {type(e).__name__}: {e}")
            filas.append({"transaction_id": tx["id"], "error": f"{type(e).__name__}: {e}"})
            continue

        gastado += _costo(resolucion, juicio, config_modelos)
        score = float(juicio.get("overall_score", 0.0))
        filas.append({
            "transaction_id": tx["id"],
            "overall_score": score,
            "criteria": juicio.get("criteria", {}),
            "approved": juicio.get("approved", False),
            "recommended_action": resolucion.get("recommended_action"),
            "risk_level": resolucion.get("risk_level"),
            "propuesta_del_modelo": resolucion.get("_propuesta_del_modelo", {}),
            "guardrail_warnings": resolucion.get("guardrail_warnings", []),
            "segundos": round(time.monotonic() - arranque, 1),
        })
        print(
            f"  [{i:>3}/{len(casos)}] {tx['id']}  score {score:>4}  "
            f"{resolucion.get('risk_level', ''):<7}  USD {gastado:.3f}"
        )

    scores = [f["overall_score"] for f in filas if "overall_score" in f]
    if not scores:
        sys.exit("ningun caso se pudo evaluar")

    por_criterio = {
        c: round(statistics.fmean(
            [f["criteria"][c] for f in filas if f.get("criteria", {}).get(c) is not None]
        ), 2)
        for c in CRITERIOS
        if any(f.get("criteria", {}).get(c) is not None for f in filas)
    }
    resumen = {
        "generado": datetime.now(UTC).isoformat(timespec="seconds"),
        "casos_evaluados": len(scores),
        "casos_con_error": len(filas) - len(scores),
        "promedio": round(statistics.fmean(scores), 2),
        "mediana": round(statistics.median(scores), 2),
        "desvio": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0,
        "minimo": min(scores),
        "maximo": max(scores),
        "aprobados": sum(1 for f in filas if f.get("approved")),
        "promedio_por_criterio": por_criterio,
        "costo_usd": round(gastado, 4),
        "configuracion": {
            "modelos_por_paso": {
                paso: {"proveedor": cfg["proveedor"], "modelo": cfg["modelo"]}
                for paso, cfg in modelos.vigente().items()
            },
            "temperatura": settings.llm_temperature,
            "prompts": _versiones_de_prompt(),
            "muestreo": (
                f"{len(casos)} casos, semilla {args.semilla}" if not args.casos and not args.todos
                else ("dataset completo" if args.todos else "lista explicita")
            ),
        },
    }

    salida = Path(args.salida) if args.salida else (
        RAIZ / "docs" / "evaluaciones" / f"{datetime.now(UTC):%Y-%m-%d_%H%M}.json"
    )
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(
        json.dumps({"resumen": resumen, "casos": filas}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\n{'=' * 58}")
    print(f"  Promedio    {resumen['promedio']}/10   (mediana {resumen['mediana']}, sigma {resumen['desvio']})")
    print(f"  Rango       {resumen['minimo']} – {resumen['maximo']}")
    print(f"  Aprobados   {resumen['aprobados']}/{resumen['casos_evaluados']}")
    for criterio, valor in por_criterio.items():
        print(f"    {criterio:<22} {valor}")
    print(f"  Costo       USD {resumen['costo_usd']}")
    # `--salida` acepta cualquier ruta, tambien fuera del repo: pedir la
    # relativa reventaba justo despues de haber gastado la corrida entera.
    try:
        destino = salida.relative_to(RAIZ)
    except ValueError:
        destino = salida
    print(f"\n  {destino}")
    print("  Ese archivo es la evidencia: versionalo y citalo junto al badge.")
    return 0


def _versiones_de_prompt() -> dict[str, str]:
    """La cabecera `# PROMPT VERSION: vX.Y` de cada prompt, leida del archivo."""
    import re

    versiones = {}
    for archivo in sorted((RAIZ / "api" / "app" / "llm" / "prompts").glob("v1_*.py")):
        m = re.search(r"PROMPT VERSION:\s*(v[\d.]+)", archivo.read_text(encoding="utf-8"))
        if m:
            versiones[archivo.stem.removeprefix("v1_")] = m.group(1)
    return versiones


if __name__ == "__main__":
    raise SystemExit(main())
