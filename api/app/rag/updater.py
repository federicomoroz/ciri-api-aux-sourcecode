"""
RAGUpdater: re-indexes Qdrant when policies are edited or cases are resolved.

Auto-improvement loop (Axis 6):
- Policy CRUD → immediate re-indexing in Qdrant (no redeploy needed)
- New case resolved with Judge score >= threshold → indexed as new precedent
"""

from ..data.db import Database
from ..domain.constants import JUDGE_AUTO_INDEX_THRESHOLD
from .indexer import QdrantIndexer


class RAGUpdater:
    def __init__(self, indexer: QdrantIndexer, db: Database, judge_threshold: float = JUDGE_AUTO_INDEX_THRESHOLD):
        self.indexer = indexer
        self.db = db
        self.judge_threshold = judge_threshold

    def on_policy_created(self, policy: dict) -> None:
        """Called after POST /api/policies: index new policy in Qdrant."""
        self.indexer.index_single_policy(policy)

    def on_policy_updated(self, policy: dict) -> None:
        """Called after PUT /api/policies/{code}: delete then re-insert in Qdrant."""
        self.indexer.delete_policy(policy["code"])
        self.indexer.index_single_policy(policy)

    def on_policy_deleted(self, code: str) -> None:
        """Called after DELETE /api/policies/{code}: remove from Qdrant."""
        self.indexer.delete_policy(code)

    def on_case_resolved(self, case: dict, judge_score: float) -> bool:
        """Called from POST /api/feedback. If judge_score >= threshold, index as new precedent.

        Returns True if the case was indexed.
        """
        if judge_score >= self.judge_threshold:
            tx = self.db.get_transaction(case.get("transaction_id", ""))
            if tx:
                self.indexer.index_single_case(case, tx)
                return True
        return False
