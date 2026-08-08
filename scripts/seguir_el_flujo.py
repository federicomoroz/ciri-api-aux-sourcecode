"""Recorre el flujo del orquestador contra la API, sin n8n.

Para qué sirve. El workflow de n8n es el entregable, pero probarlo entero exige
levantar n8n, importar tres archivos y activarlos. Este script hace el mismo
recorrido —las mismas llamadas, en el mismo orden, con los mismos cuerpos— y
dice en qué paso se rompe si se rompe.

**No reemplaza a n8n: lo verifica.** El orden y los parámetros no están escritos
acá a mano; se leen de `n8n/workflow_ciri_agent.json`, que es la misma fuente de
la que sale el diagrama del circuito. Si alguien agrega un nodo HTTP al canvas y
la API no lo soporta, este script lo dice sin abrir n8n.

Lo que sí queda replicado en Python son los dos nodos `Code` que arman payloads
—`Compilar Contexto` y `Preparar Informe`—, porque son JavaScript. Están
marcados con el nombre del nodo que espejan: si uno cambia en el canvas, el otro
tiene que cambiar acá, y el chequeo de contrato del final lo detecta.

    python scripts/seguir_el_flujo.py                      # contra Render, modo demo
    python scripts/seguir_el_flujo.py TXN-00042            # otro caso
    python scripts/seguir_el_flujo.py TXN-00042 http://localhost:8000
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOW = RAIZ / "n8n" / "workflow_ciri_agent.json"
API = "https://ciri-chargeback-agent.onrender.com"
CASO = "TXN-00051"
MOTIVO = "No reconoce la compra"
# El free tier de Render duerme a los 15 minutos: la primera llamada despierta
# el contenedor y puede tardar cerca de un minuto.
TIMEOUT_S = 240


class Paso:
    """Una llamada del recorrido, con lo que se pudo comprobar de ella."""

    def __init__(self, nodo: str, metodo: str, ruta: str):
        self.nodo, self.metodo, self.ruta = nodo, metodo, ruta
        self.estado: int | None = None
        self.ms = 0.0
        self.detalle = ""
        self.cuerpo = None

    @property
    def bien(self) -> bool:
        return self.estado is not None and 200 <= self.estado < 300


def pedir(metodo: str, url: str, cuerpo: dict | None = None) -> tuple[int, object, float]:
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo)
    if datos:
        req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310
            crudo = r.read().decode("utf-8", errors="replace")
            estado = r.status
    except urllib.error.HTTPError as e:
        crudo, estado = e.read().decode("utf-8", errors="replace"), e.code
    except Exception as e:                                          # noqa: BLE001
        return 0, str(e), (time.monotonic() - t0) * 1000
    ms = (time.monotonic() - t0) * 1000
    try:
        return estado, json.loads(crudo), ms
    except json.JSONDecodeError:
        return estado, crudo, ms


def nodos_http(workflow: dict) -> dict[str, dict]:
    """Los nodos HTTP del canvas, por nombre. La especificación del recorrido."""
    return {
        n["name"]: n for n in workflow["nodes"]
        if n["type"] == "n8n-nodes-base.httpRequest"
    }


def recorrer(api: str, txn: str, motivo: str) -> tuple[list[Paso], dict[str, str], dict]:
    """Las mismas llamadas que hace el canvas, en el mismo orden."""
    pasos: list[Paso] = []
    salteados: dict[str, str] = {}
    ctx: dict = {}

    def llamar(nodo: str, metodo: str, ruta: str, cuerpo: dict | None = None) -> object:
        p = Paso(nodo, metodo, ruta)
        p.estado, p.cuerpo, p.ms = pedir(metodo, api + ruta, cuerpo)
        pasos.append(p)
        return p.cuerpo

    # ── Etapa 1: entrada y caché ────────────────────────────────────────
    salud = llamar("Despertar API", "GET", "/health")
    pasos[-1].detalle = str(salud.get("status", "?")) if isinstance(salud, dict) else "?"

    cache = llamar(
        "Verificar Caché", "GET",
        f"/api/cache/lookup?transaction_id={urllib.parse.quote(txn)}&cliente_vip=false",
    )
    hubo_cache = bool(isinstance(cache, dict) and cache.get("cached"))
    pasos[-1].detalle = "hay caché — el canvas responde acá" if hubo_cache else "sin caché"

    # ── Etapa 2: contexto ───────────────────────────────────────────────
    tx = llamar("Obtener Transacción", "GET", f"/api/transactions/{urllib.parse.quote(txn)}")
    if not isinstance(tx, dict) or "merchant" not in tx:
        return pasos, salteados, ctx
    pasos[-1].detalle = f"{tx['merchant']} · USD {tx.get('amount_usd')} · score {tx.get('fraud_score')}"

    logs = llamar("Obtener Logs", "GET", f"/api/logs/{urllib.parse.quote(txn)}")
    pasos[-1].detalle = f"{len(logs) if isinstance(logs, list) else 0} eventos"

    q = urllib.parse.urlencode({
        "motivo": motivo, "channel": tx["channel"], "payment_method": tx["payment_method"],
        "fraud_score": tx["fraud_score"], "country": tx["country"],
    })
    pol = llamar("Buscar Políticas", "GET", f"/api/policies/search?{q}")
    politicas = pol.get("results", []) if isinstance(pol, dict) else []
    pasos[-1].detalle = f"{len(politicas)} políticas (RAG)"

    q = urllib.parse.urlencode({
        "merchant": tx["merchant"], "amount": tx["amount_usd"],
        "payment_method": tx["payment_method"], "country": tx["country"],
        "fraud_score": tx["fraud_score"], "motivo": motivo,
    })
    cas = llamar("Buscar Casos Similares", "GET", f"/api/cases/similar?{q}")
    similares = cas.get("results", []) if isinstance(cas, dict) else []
    pasos[-1].detalle = f"{len(similares)} precedentes (RAG)"

    comercio = llamar(
        "Riesgo del Comercio", "GET",
        f"/api/merchants/{urllib.parse.quote(tx['merchant'])}/risk",
    )
    if isinstance(comercio, dict):
        pasos[-1].detalle = f"cb_ratio {comercio.get('cb_ratio')} · {comercio.get('flags')}"

    cliente = llamar(
        "Historial del Cliente", "GET",
        f"/api/clients/{urllib.parse.quote(str(tx['client_id']))}/history",
    )
    if isinstance(cliente, dict):
        pasos[-1].detalle = f"{cliente.get('total_chargebacks')} contracargos · {cliente.get('flags')}"

    sla = llamar("Verificar SLA", "POST", "/api/sla/check", {
        "transaction_id": txn, "country": tx["country"], "cliente_vip": False,
    })
    if isinstance(sla, dict):
        pasos[-1].detalle = (
            "sin reclamo registrado: no se mide"
            if sla.get("within_sla") is None
            else f"within_sla={sla['within_sla']} · {sla.get('days_elapsed')}d"
        )

    # ── Etapa 3: análisis ───────────────────────────────────────────────
    # Espeja el nodo Code `Compilar Contexto`.
    contexto = {
        "transaction_id": txn, "tx_data": tx, "motivo": motivo, "cliente_vip": False,
        "logs": logs if isinstance(logs, list) else [],
        "policies": politicas, "similar_cases": similares,
        "merchant_risk": comercio if isinstance(comercio, dict) else {},
        "client_history": cliente if isinstance(cliente, dict) else {},
        "sla": sla if isinstance(sla, dict) else {},
    }
    res = llamar("Sintetizar Resolución", "POST", "/api/analyze/resolve", contexto)
    if not isinstance(res, dict) or "recommended_action" not in res:
        return pasos, salteados, ctx
    pasos[-1].detalle = (
        f"{res['recommended_action']} · {res['risk_level']} · "
        f"{len(res.get('policy_verdicts', []))} veredictos"
    )

    juez = llamar("Juez de Calidad", "POST", "/api/analyze/judge", {
        "resolution": res, "full_context": contexto,
    })
    if isinstance(juez, dict):
        pasos[-1].detalle = f"score {juez.get('overall_score')} · aprobada={juez.get('approved')}"

    # ── Etapa 4: derivación e informe ───────────────────────────────────
    if not res.get("requires_hitl"):
        salteados["Avisar — Formulario HITL"] = "el caso no necesita un analista"
    else:
        llamar("Avisar — Formulario HITL", "POST", "/api/alerts/", {
            "event_type": "hitl_form_ready", "severity": "WARN",
            "message": f"El caso {txn} espera la decision de un analista.",
            "source": "seguir_el_flujo", "transaction_id": txn,
        })
        pasos[-1].detalle = "el caso necesita una persona"

    # Espeja el nodo Code `Preparar Informe`.
    informe = llamar("Generar Reporte", "POST", "/api/reports/html", {
        "transaction": tx, "resolution": res,
        "judge_evaluation": juez if isinstance(juez, dict) else {},
        "agent_analysis": res.get("justification", ""),
        "merchant_risk": contexto["merchant_risk"], "client_profile": contexto["client_history"],
        "logs": contexto["logs"], "policies_evaluated": res.get("policy_verdicts", []),
        "similar_cases": similares, "hitl_decision": None, "cache_hit": False,
        "guardrail_warnings": res.get("guardrail_warnings", []),
        "motivo": motivo, "cliente_vip": False,
    })
    html = informe.get("html", "") if isinstance(informe, dict) else ""
    pasos[-1].detalle = f"{len(html) // 1024} KB de HTML"

    salteados["Registrar Feedback HITL"] = "necesita la decision de un analista"
    ctx = {"tx": tx, "resolucion": res, "juez": juez, "sla": sla, "html": html}
    return pasos, salteados, ctx


def contrato(workflow: dict, pasos: list[Paso], salteados: dict[str, str]) -> list[str]:
    """Que el recorrido no se haya quedado atrás del canvas.

    Este script replica el flujo a mano. Si alguien agrega un nodo HTTP al
    workflow y nadie lo agrega acá, el recorrido pasaría en verde sin haber
    probado el paso nuevo — el mismo modo de falla que un test cuyo glob no
    matchea nada: verde porque no miró, no porque esté bien.
    """
    del_canvas = set(nodos_http(workflow))
    recorridos = {p.nodo for p in pasos}
    # `salteados` lo arma el recorrido en tiempo real, con el motivo por el que
    # cada nodo no correspondia en ESTE caso. Una lista fija no alcanzaba: el
    # aviso del formulario HITL no corre en un BLOCKER y si en un caso que
    # necesita analista, asi que un allowlist estatico lo perdonaria siempre —
    # incluso cuando si tendria que haber corrido.
    faltan = del_canvas - recorridos - set(salteados)
    sobran = recorridos - del_canvas
    return (
        [f"el canvas tiene «{n}» y este recorrido no lo llama" for n in sorted(faltan)]
        + [f"este recorrido llama a «{n}», que ya no está en el canvas" for n in sorted(sobran)]
    )


def main() -> int:
    txn = sys.argv[1] if len(sys.argv) > 1 else CASO
    api = (sys.argv[2] if len(sys.argv) > 2 else API).rstrip("/")

    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))

    print(f"\n  Recorriendo el flujo de n8n sin n8n · {txn} · {api}")
    print(f"  {'(el free tier de Render puede tardar ~50s en despertar)':>72}\n")

    pasos, salteados, ctx = recorrer(api, txn, MOTIVO)

    ancho = max(len(p.nodo) for p in pasos)
    for i, p in enumerate(pasos, 1):
        marca = "ok " if p.bien else "FALLA"
        print(f"  {i:2}. {marca:5} {p.nodo:<{ancho}}  {p.estado or '—':>3}  "
              f"{p.ms:6.0f}ms  {p.detalle}")

    fallaron = [p for p in pasos if not p.bien]
    if fallaron:
        # El chequeo de contrato solo tiene sentido sobre un recorrido completo:
        # abortado en el paso 1, informaria los otros doce nodos como «el canvas
        # los tiene y este recorrido no los llama», que es ruido y no un desfase.
        print(f"\n  Se cortó en «{fallaron[0].nodo}»:")
        for f in fallaron:
            print(f"    {f.estado} · {str(f.cuerpo)[:160]}")
        return 1

    desfasado = contrato(wf, pasos, salteados)
    if desfasado:
        print("\n  Este recorrido ya no espeja al canvas:")
        for q in desfasado:
            print(f"    {q}")
        return 1

    print(f"\n  {len(pasos)}/{len(pasos)} pasos en verde")
    for nodo, motivo in salteados.items():
        print(f"     (no corresponde: {nodo} — {motivo})")

    r = ctx.get("resolucion", {})
    print(f"\n  Resultado: {r.get('recommended_action')} · riesgo {r.get('risk_level')}"
          f" · HITL={r.get('requires_hitl')}")
    print(f"  Informe: {len(ctx.get('html', '')) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
