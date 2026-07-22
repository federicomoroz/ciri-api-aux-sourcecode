"""
Service layer for feedback submission and auto-indexing.

Extracts orchestration logic from routes/feedback.py.
"""

from ..data.db import Database
from ..domain.constants import (
    FEEDBACK_AUTO_ANALYST_TAG,
    FEEDBACK_AUTO_RESOLUTION_DAYS,
    FEEDBACK_MOTIVO_MAX_CHARS,
    JUDGE_NEEDS_REVIEW_THRESHOLD,
)
from ..observability.tracer import Tracer
from ..rag.updater import RAGUpdater


class FeedbackService:
    def __init__(self, db: Database, updater: RAGUpdater, tracer: Tracer):
        self.db = db
        self.updater = updater
        self.tracer = tracer

    def submit(
        self,
        transaction_id: str,
        analyst_decision: str,
        analyst_notes: str | None,
        final_outcome: str | None,
        judge_score: float,
        resolution: dict | None,
    ) -> dict:
        """Record analyst feedback. Auto-indexes high-quality resolutions as new precedents."""
        feedback_id = self.db.save_feedback({
            "transaction_id": transaction_id,
            "analyst_decision": analyst_decision,
            "analyst_notes": analyst_notes,
            "final_outcome": final_outcome,
            "judge_score": judge_score,
        })

        auto_indexed = False
        if resolution:
            case_dict = {
                "case_id": f"FB-{feedback_id}",
                "transaction_id": transaction_id,
                "motivo": resolution.get("justification", "")[:FEEDBACK_MOTIVO_MAX_CHARS],
                "resolution": final_outcome,
                "resolution_days": FEEDBACK_AUTO_RESOLUTION_DAYS,
                "analyst": FEEDBACK_AUTO_ANALYST_TAG,
                "observations": analyst_notes,
                "open_date": "",
                "close_date": "",
            }
            auto_indexed = self.updater.on_case_resolved(case_dict, judge_score)

        needs_review = judge_score < JUDGE_NEEDS_REVIEW_THRESHOLD

        trace_id = self.tracer.trace(
            "analyst_feedback",
            input={"transaction_id": transaction_id, "analyst_decision": analyst_decision},
            output={"feedback_id": feedback_id, "auto_indexed": auto_indexed, "needs_review": needs_review},
            metadata={"judge_score": judge_score},
        )
        self.tracer.score(trace_id, "analyst_feedback_judge_score", judge_score)

        return {
            "status": "recorded",
            "feedback_id": feedback_id,
            "auto_indexed": auto_indexed,
            "needs_review": needs_review,
            "judge_score": judge_score,
        }
