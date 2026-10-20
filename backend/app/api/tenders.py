"""Tender listing, detail, seeding, and deletion (/api/tenders)."""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import (
    BidPackItem,
    CompanyProfile,
    Deadline,
    EligibilityVerdict,
    PipelineRun,
    Tender,
    get_db,
)

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


class TenderOut(BaseModel):
    id: int
    tender_id_external: Optional[str] = None
    title: str
    organisation: str = ""
    ref_no: Optional[str] = None
    category: str = "works"
    est_value: Optional[float] = None
    est_value_known: bool = True
    emd_amount: Optional[float] = None
    emd_type: Optional[str] = None
    emd_exemption_allowed: Optional[bool] = None
    published_at: Optional[datetime] = None
    bid_end_at: Optional[datetime] = None
    bid_opening_at: Optional[datetime] = None
    bid_validity_days: Optional[int] = None
    source: str = ""
    source_url: Optional[str] = None
    capture_date: Optional[date] = None
    is_seed: bool = False

    class Config:
        from_attributes = True


def _urgency_str(due_at: Any) -> Optional[str]:
    """Urgency label via the rules engine, lowercased for the UI."""
    if due_at is None:
        return "unknown"
    try:
        from app.rules.engine import deadline_urgency

        return str(deadline_urgency(due_at)).lower()
    except Exception:
        return None


def _match_score(profile_row, tender_row) -> Optional[float]:
    """Deterministic match score via the MatcherAgent (0-100)."""
    try:
        from app.agents._common import profile_to_dict, tender_to_dict
        from app.agents.matcher import MatcherAgent

        res = MatcherAgent().score(profile_to_dict(profile_row), tender_to_dict(tender_row))
        return res.get("score") if isinstance(res, dict) else None
    except Exception:
        return None


def _latest_run(db: Session, tender_id: int, profile_id: Optional[int]):
    q = db.query(PipelineRun).filter(PipelineRun.tender_id == tender_id)
    if profile_id is not None:
        q = q.filter(PipelineRun.profile_id == profile_id)
    return q.order_by(PipelineRun.id.desc()).first()


def _run_verdicts(db: Session, run: Optional[PipelineRun]) -> list[dict]:
    if run is None:
        return []
    rows = (
        db.query(EligibilityVerdict)
        .filter(EligibilityVerdict.run_id == run.id)
        .order_by(EligibilityVerdict.id.asc())
        .all()
    )
    return [
        {
            "criterion": r.criterion,
            "status": r.verdict,
            "required": r.required,
            "actual": r.actual,
            "citation": r.citation or None,
            "note": r.note,
        }
        for r in rows
    ]


def _tender_deadlines(db: Session, tender_id: int) -> list[dict]:
    rows = (
        db.query(Deadline)
        .filter(Deadline.tender_id == tender_id)
        .order_by(Deadline.due_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "tender_id": r.tender_id,
            "label": r.label,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "kind": r.kind,
            "urgency": _urgency_str(r.due_at),
        }
        for r in rows
    ]


def _tender_bidpack(db: Session, tender_id: int) -> dict:
    rows = (
        db.query(BidPackItem)
        .filter(BidPackItem.tender_id == tender_id)
        .order_by(BidPackItem.id.asc())
        .all()
    )
    return {
        "items": [
            {"item": r.item, "status": r.status, "required": r.required} for r in rows
        ]
    }


@router.get("")
def list_tenders(
    profile_id: Optional[int] = None,
    verdict: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List tenders, optionally filtered by category and search text.

    When ``profile_id`` is given, each tender is enriched with a
    deterministic ``match_score`` (MatcherAgent) and the ``verdict`` of the
    latest pipeline run for that profile+tender (may be null when never run).
    """
    query = db.query(Tender)
    if category:
        query = query.filter(Tender.category == category)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            Tender.title.ilike(like)
            | Tender.organisation.ilike(like)
            | Tender.ref_no.ilike(like)
            | Tender.tender_id_external.ilike(like)
        )
    tenders = query.order_by(Tender.id.desc()).limit(500).all()

    if profile_id is None:
        return [TenderOut.model_validate(t).model_dump(mode="json") for t in tenders]

    profile = db.get(CompanyProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    out = []
    for t in tenders:
        item = TenderOut.model_validate(t).model_dump(mode="json")
        item["match_score"] = _match_score(profile, t)
        run = _latest_run(db, t.id, profile_id)
        item["verdict"] = (
            (run.result_json or {}).get("verdict") if run and run.result_json else None
        )
        if verdict and (item["verdict"] is None or str(item["verdict"]).upper() != verdict.upper()):
            continue
        out.append(item)
    out.sort(key=lambda d: (d["match_score"] is None, -(d["match_score"] or 0)))
    return out


@router.get("/{tender_id}")
def get_tender_detail(
    tender_id: int,
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Enriched tender detail: tender + brief + verdicts + deadlines + bid-pack.

    ``profile_id`` scopes the match score and verdicts to a company profile;
    without it, the latest run for this tender (any profile) is used and the
    verdict is labelled with that profile's name.
    """
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")

    item = TenderOut.model_validate(tender).model_dump(mode="json")
    item["raw_json"] = tender.raw_json

    profile = db.get(CompanyProfile, profile_id) if profile_id else None
    if profile_id and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    run = _latest_run(db, tender_id, profile_id)
    run_profile_name = None
    if run is not None and run.profile_id:
        rp = db.get(CompanyProfile, run.profile_id)
        run_profile_name = rp.name if rp else None

    item["match_score"] = _match_score(profile, tender) if profile else None
    item["verdict"] = (
        (run.result_json or {}).get("verdict") if run and run.result_json else None
    )
    item["verdict_profile"] = run_profile_name

    brief = (tender.raw_json or {}).get("brief")

    return {
        "tender": item,
        "brief": brief,
        "verdicts": _run_verdicts(db, run),
        "verdict_profile": run_profile_name,
        "deadlines": _tender_deadlines(db, tender_id),
        "bidpack": _tender_bidpack(db, tender_id),
    }


@router.post("/seed")
def seed_tenders(db: Session = Depends(get_db)):
    """Import the seed tender JSON file (deduped). Used at startup too."""
    from app.ingestion.cppp import import_seed

    if not os.path.exists(settings.SEED_PATH):
        raise HTTPException(
            status_code=404, detail=f"Seed file not found: {settings.SEED_PATH}"
        )
    try:
        return import_seed(settings.SEED_PATH, db)
    except Exception as exc:  # no stack traces leaked to clients
        raise HTTPException(status_code=500, detail=f"Seed import failed: {exc}")


@router.delete("/{tender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tender(tender_id: int, db: Session = Depends(get_db)):
    tender = db.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    db.delete(tender)
    db.commit()
    return None
