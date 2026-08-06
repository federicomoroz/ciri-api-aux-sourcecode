"""
End-to-end tests against a REAL running API — no mocks.

Run:
    CB_E2E_BASE_URL=https://ciri-chargeback-agent.onrender.com pytest tests/e2e/ -v

These tests make real LLM calls (Anthropic API) and real Qdrant queries.
They verify structure, status codes, and business invariants — not exact LLM output.
"""

import json

import httpx
import pytest

from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel, Severity, VerdictType

# ──────────────────────────────────────────────────────────────
# Health & infrastructure
# ──────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client: httpx.Client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert data["sqlite"] == "ok"
        assert data["qdrant"] == "ok"

    def test_qdrant_collections_present(self, client: httpx.Client):
        data = client.get("/health").json()
        cols = data["collections"]
        assert "policies" in cols
        assert "historical_cases" in cols
        assert cols["policies"] >= 17


# ──────────────────────────────────────────────────────────────
# Transactions & logs (SQLite)
# ──────────────────────────────────────────────────────────────


class TestTransactions:
    def test_list_transactions(self, client: httpx.Client):
        r = client.get("/api/transactions")
        assert r.status_code == 200
        data = r.json()
        assert len(data["transactions"]) >= 100

    def test_get_transaction_by_id(self, client: httpx.Client):
        r = client.get("/api/transactions/TXN-00051")
        assert r.status_code == 200
        tx = r.json()
        assert tx["id"] == "TXN-00051"
        assert tx["payment_method"] == PaymentMethod.CRYPTO
        assert int(tx["fraud_score"]) == 8

    def test_get_transaction_not_found(self, client: httpx.Client):
        r = client.get("/api/transactions/TXN-XXXXX")
        assert r.status_code == 404

    def test_get_logs(self, client: httpx.Client):
        r = client.get("/api/logs/TXN-00051")
        assert r.status_code == 200
        data = r.json()
        assert data["log_count"] >= 1
        assert len(data["logs"]) >= 1


# ──────────────────────────────────────────────────────────────
# Policies (CRUD + RAG search)
# ──────────────────────────────────────────────────────────────


class TestPolicies:
    def test_list_all_policies(self, client: httpx.Client):
        r = client.get("/api/policies/")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 17

    def test_get_policy_by_code(self, client: httpx.Client):
        r = client.get("/api/policies/POL-EXC-003")
        assert r.status_code == 200
        policy = r.json()
        assert policy["code"] == "POL-EXC-003"
        assert "cripto" in policy.get("description", "").lower() or "crypto" in policy.get("name", "").lower()

    def test_search_policies_rag(self, client: httpx.Client):
        """Qdrant semantic search returns relevant policies."""
        r = client.get("/api/policies/search", params={
            "motivo": "fraude con tarjeta",
            "channel": "Web",
            "payment_method": PaymentMethod.CREDIT_VISA,
            "fraud_score": 8,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        for p in data["results"]:
            assert "code" in p
            assert "description" in p or "name" in p


# ──────────────────────────────────────────────────────────────
# Cases (RAG search)
# ──────────────────────────────────────────────────────────────


class TestCases:
    def test_similar_cases(self, client: httpx.Client):
        r = client.get("/api/cases/similar", params={
            "motivo": "No reconoce la compra",
            "merchant": "Airbnb",
            "amount": 2095.9,
            "payment_method": PaymentMethod.CRYPTO,
            "country": "COL",
            "fraud_score": 8,
        })
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert isinstance(data["results"], list)


# ──────────────────────────────────────────────────────────────
# Analysis endpoints (merchant, client, SLA)
# ──────────────────────────────────────────────────────────────


class TestAnalysis:
    def test_merchant_risk(self, client: httpx.Client):
        r = client.get("/api/merchants/Airbnb/risk")
        assert r.status_code == 200
        data = r.json()
        assert "cb_ratio" in data
        assert "total_chargebacks" in data
        assert "flags" in data

    def test_client_history(self, client: httpx.Client):
        r = client.get("/api/clients/CLI-0003/history")
        assert r.status_code == 200
        data = r.json()
        assert "total_chargebacks" in data
        assert data["client_id"] == "CLI-0003"

    def test_sla_check_latam(self, client: httpx.Client):
        r = client.post("/api/sla/check", json={
            "case_open_date": "2025-07-20",
            "country": "ARG",
            "cliente_vip": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["sla_type"] in ("standard", "extended", "vip")
        assert "days_elapsed" in data
        assert "within_sla" in data


# ──────────────────────────────────────────────────────────────
# LLM: Full pipeline via streaming (real Anthropic calls)
# ──────────────────────────────────────────────────────────────


class TestFullPipeline:
    """Full pipeline via SSE — gathers context, resolves, judges, renders."""

    @pytest.fixture(scope="class")
    def pipeline_events(self, client: httpx.Client) -> list[dict]:
        """Run full pipeline for TXN-00051 via SSE, collect all events."""
        from tests.e2e.conftest import ANTHROPIC_KEY
        if not ANTHROPIC_KEY:
            pytest.skip("CB_ANTHROPIC_API_KEY required for streaming pipeline tests")
        events = []
        with client.stream(
            "POST",
            "/api/panel/analyze-stream",
            json={
                "transaction_id": "TXN-00051",
                "motivo": "No reconoce la compra",
                "cliente_vip": False,
                "api_key": ANTHROPIC_KEY,
            },
            timeout=120.0,
        ) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    def test_stream_returns_sse_events(self, pipeline_events: list[dict]):
        steps = [e["step"] for e in pipeline_events]
        # Minimum: start + done (cache hit) or start + many steps + done (fresh)
        assert len(steps) >= 2
        assert steps[0] == "start"
        assert steps[-1] == "done"

    def test_has_intermediate_steps_or_cache_hit(self, pipeline_events: list[dict]):
        """Either we get full pipeline steps, or a cache hit with immediate done."""
        steps = [e["step"] for e in pipeline_events]
        done_event = [e for e in pipeline_events if e["step"] == "done"][0]
        is_cache_hit = done_event.get("usage", {}).get("cache_hit", False)
        if not is_cache_hit:
            assert "transaction" in steps
            assert "resolved" in steps or "resolving" in steps
            assert "judged" in steps or "judging" in steps

    def test_resolved_is_reject_blocker(self, pipeline_events: list[dict]):
        """TXN-00051 is crypto with fraud_score=8 — must be REJECT/BLOCKER."""
        resolved = [e for e in pipeline_events if e["step"] == "resolved"]
        if resolved:
            assert resolved[0]["action"] == ResolutionOutcome.REJECT
            assert resolved[0]["risk_level"] == RiskLevel.BLOCKER

    def test_judged_score_above_7(self, pipeline_events: list[dict]):
        """A correct BLOCKER resolution should score well."""
        judged = [e for e in pipeline_events if e["step"] == "judged"]
        if judged:
            assert judged[0]["score"] >= 7.0

    def test_done_has_html(self, pipeline_events: list[dict]):
        done = [e for e in pipeline_events if e["step"] == "done"]
        assert len(done) == 1
        assert "html" in done[0]
        html = done[0]["html"]
        assert "TXN-00051" in html

    def test_done_has_usage(self, pipeline_events: list[dict]):
        done = [e for e in pipeline_events if e["step"] == "done"]
        assert "usage" in done[0]


# ──────────────────────────────────────────────────────────────
# Resolve + Judge (direct endpoints with pre-gathered context)
# ──────────────────────────────────────────────────────────────


class TestResolveEndpoint:
    """Test /api/analyze/resolve with pre-gathered context (like n8n does)."""

    @pytest.fixture(scope="class")
    def context(self, client: httpx.Client) -> dict:
        """Gather all context for TXN-00051 via individual endpoints."""
        tx = client.get("/api/transactions/TXN-00051").json()
        logs_data = client.get("/api/logs/TXN-00051").json()
        policies_data = client.get("/api/policies/search", params={
            "motivo": "No reconoce la compra",
            "payment_method": tx["payment_method"],
            "fraud_score": tx["fraud_score"],
            "country": tx["country"],
        }).json()
        cases_data = client.get("/api/cases/similar", params={
            "merchant": tx["merchant"],
            "amount": tx["amount_usd"],
            "payment_method": tx["payment_method"],
            "country": tx["country"],
            "fraud_score": tx["fraud_score"],
            "motivo": "No reconoce la compra",
        }).json()
        merchant_risk = client.get(f"/api/merchants/{tx['merchant']}/risk").json()
        client_history = client.get(f"/api/clients/{tx['client_id']}/history").json()
        return {
            "tx": tx,
            "logs": logs_data["logs"],
            "policies": policies_data.get("results", []),
            "cases": cases_data.get("results", []),
            "merchant_risk": merchant_risk,
            "client_history": client_history,
        }

    @pytest.fixture(scope="class")
    def resolution(self, client: httpx.Client, context: dict) -> dict:
        """Call resolve with full context."""
        r = client.post("/api/analyze/resolve", json={
            "transaction_id": "TXN-00051",
            "agent_analysis": "E2E test: crypto transaction with high fraud score",
            "tx_data": context["tx"],
            "policies": context["policies"],
            "similar_cases": context["cases"],
            "logs": context["logs"],
            "merchant_risk": context["merchant_risk"],
            "client_history": context["client_history"],
            "motivo": "No reconoce la compra",
            "cliente_vip": False,
        })
        assert r.status_code == 200
        return r.json()

    def test_resolve_returns_required_fields(self, resolution: dict):
        required = [
            "recommended_action", "risk_level", "justification",
            "confidence", "policy_verdicts",
        ]
        for field in required:
            assert field in resolution, f"Missing field: {field}"

    def test_blocker_crypto_is_reject(self, resolution: dict):
        """TXN-00051 is crypto with fraud_score=8 — must be REJECT."""
        assert resolution["recommended_action"] == ResolutionOutcome.REJECT

    def test_blocker_risk_level(self, resolution: dict):
        assert resolution["risk_level"] == RiskLevel.BLOCKER

    def test_policy_verdicts_not_empty(self, resolution: dict):
        verdicts = resolution["policy_verdicts"]
        assert len(verdicts) >= 1
        for v in verdicts:
            assert "policy_code" in v
            assert "verdict" in v

    def test_has_blocker_verdict(self, resolution: dict):
        """At least one BLOCKER verdict (POL-EXC-003 for crypto)."""
        verdicts = resolution["policy_verdicts"]
        blockers = [v for v in verdicts if v["verdict"] == VerdictType.BLOCKER]
        assert len(blockers) >= 1

    def test_guardrail_warnings_is_list(self, resolution: dict):
        warnings = resolution.get("guardrail_warnings", [])
        assert isinstance(warnings, list)


class TestJudgeEndpoint:
    """Test /api/analyze/judge with a real resolution + full context."""

    @pytest.fixture(scope="class")
    def judge_result(self, client: httpx.Client) -> dict:
        """Gather context, resolve, then judge — full chain."""
        tx = client.get("/api/transactions/TXN-00051").json()
        logs_data = client.get("/api/logs/TXN-00051").json()
        policies_data = client.get("/api/policies/search", params={
            "motivo": "No reconoce la compra",
            "payment_method": tx["payment_method"],
            "fraud_score": tx["fraud_score"],
            "country": tx["country"],
        }).json()
        cases_data = client.get("/api/cases/similar", params={
            "merchant": tx["merchant"],
            "amount": tx["amount_usd"],
            "payment_method": tx["payment_method"],
            "country": tx["country"],
            "fraud_score": tx["fraud_score"],
            "motivo": "No reconoce la compra",
        }).json()
        merchant_risk = client.get(f"/api/merchants/{tx['merchant']}/risk").json()
        client_history = client.get(f"/api/clients/{tx['client_id']}/history").json()

        policies = policies_data.get("results", [])
        cases = cases_data.get("results", [])

        # Resolve
        resolve_r = client.post("/api/analyze/resolve", json={
            "transaction_id": "TXN-00051",
            "agent_analysis": "E2E test: crypto transaction with high fraud score",
            "tx_data": tx,
            "policies": policies,
            "similar_cases": cases,
            "logs": logs_data["logs"],
            "merchant_risk": merchant_risk,
            "client_history": client_history,
            "motivo": "No reconoce la compra",
            "cliente_vip": False,
        })
        resolution = resolve_r.json()

        # Judge with full context
        r = client.post("/api/analyze/judge", json={
            "resolution": resolution,
            "full_context": {
                "transaction": tx,
                "motivo": "No reconoce la compra",
                "policies": policies,
                "similar_cases": cases,
                "merchant_risk": merchant_risk,
                "client_history": client_history,
                "logs": logs_data["logs"],
            },
        })
        assert r.status_code == 200
        return r.json()

    def test_judge_returns_score(self, judge_result: dict):
        assert "overall_score" in judge_result
        score = judge_result["overall_score"]
        assert 1.0 <= score <= 10.0

    def test_judge_score_above_7(self, judge_result: dict):
        """A correct BLOCKER resolution should score well."""
        assert judge_result["overall_score"] >= 7.0

    def test_judge_has_criteria(self, judge_result: dict):
        """Judge evaluates multiple criteria."""
        criteria = judge_result.get("criteria", judge_result.get("criteria_scores", {}))
        assert len(criteria) >= 1

    def test_judge_has_approved_field(self, judge_result: dict):
        assert "approved" in judge_result
        assert isinstance(judge_result["approved"], bool)


# ──────────────────────────────────────────────────────────────
# HTML report generation
# ──────────────────────────────────────────────────────────────


class TestReport:
    def test_generate_html_report(self, client: httpx.Client):
        r = client.post("/api/reports/html", json={
            "transaction": {
                "id": "TXN-00051",
                "client_id": "CLI-0003",
                "merchant": "Airbnb",
                "amount_usd": 2095.9,
                "date": "2024-09-23",
                "payment_method": PaymentMethod.CRYPTO,
                "country": "COL",
                "channel": "POS",
                "device": "Firefox/Mac",
                "fraud_score": 8,
                "status": "Contracargo iniciado",
                "notes": "Alto riesgo detectado",
            },
            "resolution": {
                "transaction_id": "TXN-00051",
                "recommended_action": ResolutionOutcome.REJECT,
                "risk_level": RiskLevel.BLOCKER,
                "justification": "Crypto payments are irreversible — POL-EXC-003 blocks this.",
                "confidence": 0.95,
                "policy_verdicts": [
                    {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": "Crypto"},
                ],
                "precedent_summary": "No similar precedents.",
                "log_summary": "1 ERROR: High fraud score detected",
                "compensation_applicable": False,
                "compensation_amount_usd": 0,
                "next_steps": ["Auto-reject", "Notify client"],
                "requires_hitl": False,
                "hitl_reason": None,
            },
            "judge_evaluation": {
                "overall_score": 9.0,
                "criteria": {"policy_compliance": 9.5, "risk_assessment": 9.0},
                "approved": True,
                "strengths": ["Clear policy reference"],
                "weaknesses": [],
            },
            "agent_analysis": "E2E test — crypto transaction with high fraud score.",
            "merchant_risk": {
                "merchant": "Airbnb",
                "total_transactions": 10,
                "total_chargebacks": 1,
                "cb_ratio": 0.10,
                "total_volume_usd": 12500.0,
                "avg_transaction_usd": 1250.0,
                "flags": [],
                "is_strategic": False,
            },
            "client_profile": {
                "client_id": "CLI-0003",
                "total_transactions": 1,
                "total_chargebacks": 0,
                "rejected_transactions": 0,
                "countries_used": ["COL"],
                "payment_methods_used": [PaymentMethod.CRYPTO],
                "flags": [],
            },
            "logs": [
                {
                    "id": 1,
                    "timestamp": "2024-09-23T10:00:00Z",
                    "transaction_id": "TXN-00051",
                    "event": "FRAUD_ALERT",
                    "service": "fraud_detection",
                    "code": "FRAUD_001",
                    "detail": "High fraud score detected",
                    "severity": Severity.ERROR,
                },
            ],
            "policies_evaluated": [
                {
                    "code": "POL-EXC-003",
                    "name": "Exclusion Cripto",
                    "category": "exclusion",
                    "description": "Crypto payments cannot be chargebacked.",
                    "reference": "Risk: irreversible",
                },
            ],
            "similar_cases": [],
            "hitl_decision": None,
            "cache_hit": False,
            "guardrail_warnings": [],
            "motivo": "No reconoce la compra",
            "cliente_vip": False,
        })
        assert r.status_code == 200
        data = r.json()
        html = data["html"]
        assert "TXN-00051" in html
        assert "Airbnb" in html


# ──────────────────────────────────────────────────────────────
# Alerts
# ──────────────────────────────────────────────────────────────


class TestAlerts:
    def test_post_alert(self, client: httpx.Client):
        r = client.post("/api/alerts/", json={
            "event_type": "e2e_test",
            "severity": Severity.INFO,
            "message": "E2E test alert",
            "source": "pytest",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["event_type"] == "e2e_test"
        assert data["id"] >= 1

    def test_get_alerts(self, client: httpx.Client):
        r = client.get("/api/alerts/", params={"limit": 5})
        assert r.status_code == 200
        alerts = r.json()
        assert isinstance(alerts, list)


# ──────────────────────────────────────────────────────────────
# Panel
# ──────────────────────────────────────────────────────────────


class TestPanel:
    def test_panel_serves_html(self, client: httpx.Client):
        r = client.get("/panel")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "CIRI" in r.text
        assert "Federico Palatnik Moroz" in r.text
