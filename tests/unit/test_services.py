"""
Unit tests for ResolutionService and FeedbackService.

Covers:
- Axis 7 (Observability): tracer.trace() and tracer.score() are called correctly
- Service orchestration: LLM calls, log summarization, guardrails
"""

from unittest.mock import MagicMock, call

import pytest

from api.app.domain.constants import (
    FALLBACK_TX_ID,
    FEEDBACK_CASE_ID_PREFIX,
    FEEDBACK_STATUS_RECORDED,
    JUDGE_APPROVAL_THRESHOLD,
    JUDGE_NEEDS_REVIEW_THRESHOLD,
    TRACE_FEEDBACK,
    TRACE_FEEDBACK_SCORE,
    TRACE_JUDGE,
    TRACE_RESOLVE,
)
from api.app.services.feedback import FeedbackService
from api.app.services.resolution import ResolutionService


# ---- Fixtures ----

@pytest.fixture
def mock_tracer():
    tracer = MagicMock()
    tracer.trace.return_value = "trace-123"
    return tracer


@pytest.fixture
def mock_llm_resolve():
    """MockLLM that returns valid JSON for both policy eval and resolution."""
    llm = MagicMock()
    llm.complete.side_effect = [
        # First call: policy eval
        '[{"policy_code":"POL-EXC-003","verdict":"BLOCKER","reasoning":"Cripto","requires_human_review":false}]',
        # Second call: resolution synthesis
        '{"transaction_id":"TXN-00051","recommended_action":"REJECT","confidence":0.99,'
        '"justification":"BLOCKER cripto","policy_verdicts":[],'
        '"precedent_summary":"","log_summary":"","risk_level":"BLOCKER",'
        '"compensation_applicable":false,"compensation_amount_usd":0.0,'
        '"next_steps":["Notificar"],"requires_hitl":false,"hitl_reason":null}',
    ]
    return llm


@pytest.fixture
def mock_llm_judge():
    """MockLLM that returns valid judge evaluation JSON."""
    llm = MagicMock()
    llm.complete.return_value = (
        '{"overall_score":9.2,"criteria":{"policy_consistency":10.0,'
        '"justification_quality":9.0,"precedent_usage":8.0,'
        '"risk_assessment":9.5,"actionability":9.5},'
        '"approved":true,"strengths":["Correcto"],"weaknesses":[]}'
    )
    return llm


@pytest.fixture
def resolution_service(mock_llm_resolve, mock_tracer):
    return ResolutionService(mock_llm_resolve, mock_tracer)


@pytest.fixture
def judge_service(mock_llm_judge, mock_tracer):
    return ResolutionService(mock_llm_judge, mock_tracer)


# ---- ResolutionService.resolve() ----

class TestResolutionServiceResolve:

    def test_resolve_calls_tracer_trace(self, resolution_service, mock_tracer):
        """Axis 7: resolve() must create a trace with TRACE_RESOLVE name."""
        resolution_service.resolve(
            tx_data={"id": "TXN-00051", "merchant": "Airbnb", "amount_usd": 2095.90},
            policies=[],
            similar_cases=[],
            logs=[],
            merchant_risk={},
            client_history={},
            motivo="No reconoce la compra",
            cliente_vip=False,
        )
        mock_tracer.trace.assert_called_once()
        args = mock_tracer.trace.call_args
        assert args[0][0] == TRACE_RESOLVE
        assert args[1]["input"]["transaction_id"] == "TXN-00051"

    def test_resolve_returns_trace_id(self, resolution_service):
        """resolve() must include trace_id in the response."""
        result = resolution_service.resolve(
            tx_data={"id": "TXN-00051"},
            policies=[], similar_cases=[], logs=[],
            merchant_risk={}, client_history={},
            motivo=None, cliente_vip=False,
        )
        assert result["trace_id"] == "trace-123"

    def test_resolve_calls_llm_twice(self, resolution_service, mock_llm_resolve):
        """resolve() should call LLM twice: policy eval + resolution synthesis."""
        resolution_service.resolve(
            tx_data={"id": "TXN-00051"},
            policies=[], similar_cases=[], logs=[],
            merchant_risk={}, client_history={},
            motivo=None, cliente_vip=False,
        )
        assert mock_llm_resolve.complete.call_count == 2

    def test_resolve_includes_guardrail_warnings(self, resolution_service):
        """resolve() must include guardrail_warnings key in response."""
        result = resolution_service.resolve(
            tx_data={"id": "TXN-00051"},
            policies=[], similar_cases=[], logs=[],
            merchant_risk={}, client_history={},
            motivo=None, cliente_vip=False,
        )
        assert "guardrail_warnings" in result

    def test_resolve_uses_fallback_tx_id(self, resolution_service, mock_tracer):
        """resolve() should use FALLBACK_TX_ID when tx_data has no id."""
        resolution_service.resolve(
            tx_data={},
            policies=[], similar_cases=[], logs=[],
            merchant_risk={}, client_history={},
            motivo=None, cliente_vip=False,
        )
        args = mock_tracer.trace.call_args
        assert args[1]["input"]["transaction_id"] == FALLBACK_TX_ID


# ---- ResolutionService.judge() ----

class TestResolutionServiceJudge:

    def test_judge_calls_tracer_trace(self, judge_service, mock_tracer):
        """Axis 7: judge() must create a trace with TRACE_JUDGE name."""
        judge_service.judge(
            resolution={"recommended_action": "REJECT", "confidence": 0.99},
            full_context={"transaction": {"id": "TXN-00051"}},
        )
        mock_tracer.trace.assert_called_once()
        args = mock_tracer.trace.call_args
        assert args[0][0] == TRACE_JUDGE

    def test_judge_calls_tracer_score(self, judge_service, mock_tracer):
        """Axis 7: judge() must call tracer.score() with the judge score."""
        judge_service.judge(
            resolution={"recommended_action": "REJECT", "confidence": 0.99},
            full_context={"transaction": {"id": "TXN-00051"}},
        )
        mock_tracer.score.assert_called_once_with("trace-123", "judge_score", 9.2)

    def test_judge_returns_overall_score(self, judge_service):
        """judge() must return overall_score and approved fields."""
        result = judge_service.judge(
            resolution={"recommended_action": "REJECT"},
            full_context={"transaction": {"id": "TXN-00051"}},
        )
        assert result["overall_score"] == 9.2
        assert result["approved"] is True

    def test_judge_computes_approved_from_threshold(self, mock_tracer):
        """judge() should compute approved from JUDGE_APPROVAL_THRESHOLD when not in response."""
        llm = MagicMock()
        llm.complete.return_value = (
            '{"overall_score":6.5,"criteria":{"policy_consistency":7.0,'
            '"justification_quality":6.0,"precedent_usage":6.0,'
            '"risk_assessment":7.0,"actionability":6.5},'
            '"strengths":[],"weaknesses":[]}'
        )
        service = ResolutionService(llm, mock_tracer)
        result = service.judge(
            resolution={"recommended_action": "PENDING_HITL"},
            full_context={"transaction": {"id": "TXN-00042"}},
        )
        assert result["overall_score"] == 6.5
        assert result["approved"] is False  # 6.5 < 7.0


# ---- ResolutionService._summarize_logs() ----

class TestSummarizeLogs:

    def test_empty_logs_summary(self):
        """Empty logs should produce a summary with zero counts."""
        text = ResolutionService._summarize_logs([])
        assert "Total: 0 eventos" in text
        assert "ERROR: 0" in text

    def test_logs_with_errors_included(self, sample_logs):
        """Critical logs (ERROR/WARN) should appear in summary."""
        text = ResolutionService._summarize_logs(sample_logs)
        assert "MERCHANT_NO_RESPONSE" in text
        assert "FRAUD_ALERT" in text


# ---- FeedbackService ----

class TestFeedbackService:

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.save_feedback.return_value = 42
        return db

    @pytest.fixture
    def mock_updater(self):
        updater = MagicMock()
        updater.on_case_resolved.return_value = False
        return updater

    @pytest.fixture
    def feedback_service(self, mock_db, mock_updater, mock_tracer):
        return FeedbackService(mock_db, mock_updater, mock_tracer)

    def test_submit_records_feedback(self, feedback_service, mock_db):
        """submit() must save feedback to DB and return recorded status."""
        result = feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="APPROVED",
            analyst_notes="Verified.",
            final_outcome="REJECT",
            judge_score=9.0,
            resolution=None,
        )
        mock_db.save_feedback.assert_called_once()
        assert result["status"] == FEEDBACK_STATUS_RECORDED
        assert result["feedback_id"] == 42

    def test_submit_calls_tracer(self, feedback_service, mock_tracer):
        """Axis 7: submit() must call tracer.trace() and tracer.score()."""
        feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="APPROVED",
            analyst_notes=None,
            final_outcome="REJECT",
            judge_score=8.5,
            resolution=None,
        )
        mock_tracer.trace.assert_called_once()
        args = mock_tracer.trace.call_args
        assert args[0][0] == TRACE_FEEDBACK
        mock_tracer.score.assert_called_once_with("trace-123", TRACE_FEEDBACK_SCORE, 8.5)

    def test_submit_with_resolution_calls_updater(self, feedback_service, mock_updater):
        """Axis 6: submit() with resolution should call updater.on_case_resolved()."""
        feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="APPROVED",
            analyst_notes="Good case",
            final_outcome="REJECT",
            judge_score=9.0,
            resolution={"justification": "BLOCKER cripto"},
        )
        mock_updater.on_case_resolved.assert_called_once()
        case_dict = mock_updater.on_case_resolved.call_args[0][0]
        assert case_dict["transaction_id"] == "TXN-00051"
        assert case_dict["case_id"].startswith(FEEDBACK_CASE_ID_PREFIX)

    def test_submit_without_resolution_skips_updater(self, feedback_service, mock_updater):
        """submit() without resolution should NOT call updater."""
        feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="REJECTED",
            analyst_notes=None,
            final_outcome="REJECT",
            judge_score=3.0,
            resolution=None,
        )
        mock_updater.on_case_resolved.assert_not_called()

    def test_submit_low_score_sets_needs_review(self, feedback_service):
        """submit() with judge_score < JUDGE_NEEDS_REVIEW_THRESHOLD should set needs_review=True."""
        result = feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="REJECTED",
            analyst_notes=None,
            final_outcome="REJECT",
            judge_score=4.0,
            resolution=None,
        )
        assert result["needs_review"] is True

    def test_submit_high_score_no_needs_review(self, feedback_service):
        """submit() with judge_score >= JUDGE_NEEDS_REVIEW_THRESHOLD should set needs_review=False."""
        result = feedback_service.submit(
            transaction_id="TXN-00051",
            analyst_decision="APPROVED",
            analyst_notes=None,
            final_outcome="REJECT",
            judge_score=8.0,
            resolution=None,
        )
        assert result["needs_review"] is False
