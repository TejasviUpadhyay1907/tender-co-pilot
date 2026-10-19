"""Manual ingest trigger and ingest status (/api/ingest)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import PipelineRun, get_db

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class IngestRunIn(BaseModel):
    keyword: Optional[str] = None
    max_results: int = 50


def _invoke_scanner(keyword: Optional[str], max_results: int, db: Session) -> Any:
    """Run ScannerAgent with a proper AgentContext.

    The scanner reads its keywords from settings.SCAN_KEYWORDS; a supplied
    ``keyword`` temporarily overrides it for this one run.
    """
    from app.agents.base import AgentContext
    from app.agents.scanner import ScannerAgent
    from app.config import settings as _settings

    logs: list = []
    ctx = AgentContext(
        db=db, profile=None, tender=None, store=None, llm=None,
        log=lambda *a: logs.append(" ".join(str(x) for x in a)),
    )
    if keyword:
        original = _settings.SCAN_KEYWORDS
        _settings.SCAN_KEYWORDS = keyword
        try:
            return ScannerAgent().run(ctx)
        finally:
            _settings.SCAN_KEYWORDS = original
    return ScannerAgent().run(ctx)


def _coerce_result(res: Any) -> dict:
    if isinstance(res, dict):
        return res
    return {"result": res}


@router.post("/run")
def run_ingest(payload: IngestRunIn, db: Session = Depends(get_db)):
    """Trigger a one-off portal scan via ScannerAgent (parallel workstream)."""
    try:
        # Imported here because the scanner module is built by a parallel
        # workstream and may only exist at call time.
        from app.agents.scanner import ScannerAgent
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"ScannerAgent is not available yet: {exc}"
        )

    try:
        result = _coerce_result(_invoke_scanner(payload.keyword, payload.max_results, db))
    except HTTPException:
        raise
    except Exception as exc:  # no stack traces leaked to clients
        raise HTTPException(status_code=500, detail=f"Ingest run failed: {exc}")

    data = result if isinstance(result, dict) else {}
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    return {
        "found": payload.get("found", 0),
        "added": payload.get("added", 0),
        "skipped": payload.get("skipped", 0),
        "errors": payload.get("errors", []),
    }


@router.get("/status")
def ingest_status(db: Session = Depends(get_db)):
    """Return the most recent scheduled/manual ingest run, if any."""
    last = (
        db.query(PipelineRun)
        .filter(PipelineRun.profile_id.is_(None), PipelineRun.tender_id.is_(None))
        .order_by(PipelineRun.id.desc())
        .first()
    )
    if last is None:
        return {"last_run": None}
    return {
        "last_run": {
            "id": last.id,
            "status": last.status,
            "steps": last.steps_json,
            "result": last.result_json,
            "error": last.error,
            "created_at": last.created_at.isoformat() if last.created_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
        }
    }
