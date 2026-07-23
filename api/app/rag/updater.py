"""
RAGUpdater: re-indexes Qdrant when policies are edited or cases are resolved.

Auto-improvement loop (Axis 6):
- Policy CRUD -> immediate re-indexing in Qdrant (no redeploy needed)
- New case resolved with Judge score >= threshold -> indexed as new precedent
"""

import logging

from ..data.db import Database
from ..domain.constants import JUDGE_AUTO_INDEX_THRESHOLD
from .indexer import QdrantIndexer

logger = logging.getLogger(__name__)


class RAGUpdater:
    def __init__(self, indexer: QdrantIndexer, db: Database, judge_threshold: float = JUDGE_AUTO_INDEX_THRESHOLD):
        self.indexer = indexer
        self.db = db
        self.judge_threshold = judge_threshold

    def on_policy_created(self, policy: dict) -> None:
        """Called after POST /api/policies: index new policy in Qdrant.

        Raises on failure so the caller can report the error to the API consumer.
        """
        self.indexer.index_single_policy(policy)
        logger.info("Policy %s indexed after creation", policy.get("code"))

    def on_policy_updated(self, policy: dict) -> None:
        """Called after PUT /api/policies/{code}: delete then re-insert in Qdrant.

        Raises on failure so the caller can report the error to the API consumer.
        """
        self.indexer.delete_policy(policy["code"])
        self.indexer.index_single_policy(policy)
        logger.info("Policy %s re-indexed after update", policy["code"])

    def on_policy_deleted(self, code: str) -> None:
        """Called after DELETE /api/policies/{code}: remove from Qdrant.

        Raises on failure so the caller can report the error to the API consumer.
        """
        self.indexer.delete_policy(code)
        logger.info("Policy %s removed from Qdrant", code)

    def on_case_resolved(self, case: dict, judge_score: float) -> bool:
        """Called from POST /api/feedback. If judge_score >= threshold, index as new precedent.

        Returns True if the case was indexed.
        """
        if judge_score >= self.judge_threshold:
            tx = self.db.get_transaction(case.get("transaction_id", ""))
            if tx:
                try:
                    self.indexer.index_single_case(case, tx)
                    logger.info("Case %s auto-indexed as precedent (judge_score=%.1f)", case.get("case_id"), judge_score)
                    return True
                except Exception as e:
                    logger.error("Failed to auto-index case %s: %s", case.get("case_id"), e)
                    return False
        return False
