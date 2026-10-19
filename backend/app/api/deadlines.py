"""Deadline endpoints (/api/deadlines).

GET /deadlines?days=30 returns upcoming deadlines sorted by due date,
each annotated with an urgency level computed by the rules engine
(workstream-owned) with a sane local fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import Deadline, get_db

router = APIRouter(prefix="/api/deadlines", tags=["deadlines"])


def _urgency_fallback(due_at: datetime, notified: bool = False) -> str:
    now = datetime.utcnow()
    delta_s = (due_at - now).total_seconds()
    if delta_s < 0:
        return "overdue"
    if delta_s < 3 * 86400:
        return "high"
    if delta_s < 7 * 86400:
        return "medium"
    return "low"


def _urgency(deadline: Deadline) -> str:
    """Prefer the rules-engine urgency; fall back to local heuristics."""
    try:
        # Built by a parallel workstream; import lazily.
        from app.rules.engine import deadline_urgency

        return str(deadline_urgency(deadline.due_at)).lower()
    except Exception:
        pass
    return _urgency_fallback(deadline.due_at, deadline.notified)


@router.get("")
def list_deadlines(
    days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)
):
    cutoff = datetime.utcnow() + timedelta(days=days)
    rows = (
        db.query(Deadline)
        .filter(Deadline.due_at <= cutoff)
        .order_by(Deadline.due_at.asc())
        .limit(500)
        .all()
    )
    return [
        {
            "id": d.id,
            "tender_id": d.tender_id,
            "label": d.label,
            "due_at": d.due_at.isoformat() if d.due_at else None,
            "kind": d.kind,
            "notified": d.notified,
            "urgency": _urgency(d),
        }
        for d in rows
    ]
