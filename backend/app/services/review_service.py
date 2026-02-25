"""Review state helpers for compliance v2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import ReviewState


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewService:
    def create_initial_state(self, review_required: bool) -> ReviewState:
        if review_required:
            return ReviewState(state="pending_review", updated_at=_now_iso_utc())
        return ReviewState(state="approved", reviewer="system", note="Auto-approved", updated_at=_now_iso_utc())

    def submit(self, decision: str, reviewer: Optional[str] = None, note: Optional[str] = None) -> ReviewState:
        normalized = str(decision or "").strip().lower()
        if normalized in {"approved", "approve", "pass"}:
            state = "approved"
        elif normalized in {"rejected", "reject", "fail"}:
            state = "rejected"
        else:
            state = "pending_review"

        return ReviewState(
            state=state,
            reviewer=(reviewer or "").strip() or None,
            note=(note or "").strip() or None,
            updated_at=_now_iso_utc(),
        )


review_service = ReviewService()
