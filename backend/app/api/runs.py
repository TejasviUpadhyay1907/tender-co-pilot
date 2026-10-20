"""Pipeline run endpoints (/api/runs).

POST /runs creates a PipelineRun row and then executes the orchestrator
(built by a parallel workstream) synchronously inside the request.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import CompanyProfile, PipelineRun, Tender, get_db

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunCreate(BaseModel):
    profile_id: Optional[int] = None
    tender_id: Optional[int] = None


class RunOut(BaseModel):
    id: int
    profile_id: Optional[int] = None
    tender_id: Optional[int] = None
    status: str
    steps_json: Optional[Any] = None
    result_json: Optional[dict] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    if payload.profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="No company profile selected. Create your company profile "
            "first — the pipeline analyses each tender for a specific company.",
        )
    profile = db.get(CompanyProfile, payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if payload.tender_id is not None and db.get(Tender, payload.tender_id) is None:
        raise HTTPException(status_code=404, detail="Tender not found")

    try:
        # Built by a parallel workstream; imported here (not at module top).
        from app.agents.llm import LLMClient
        from app.agents.orchestrator import run_pipeline
        from app.config import settings as _settings
        from app.rag.vectorstore import TenderVectorStore
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Pipeline components unavailable: {exc}"
        )

    store = TenderVectorStore(_settings.CHROMA_DIR)
    llm = LLMClient()
    try:
        # The orchestrator owns the PipelineRun record and returns a summary
        # that includes run_id.
        summary = run_pipeline(db, payload.profile_id, payload.tender_id, store, llm)
    except Exception as exc:  # keep the error on the API response, not a trace
        raise HTTPException(
            status_code=500, detail=f"Pipeline failed: {str(exc)[:500]}"
        )

    run = db.get(PipelineRun, (summary or {}).get("run_id"))
    if run is None:  # defensive: orchestrator contract changed
        run = (
            db.query(PipelineRun)
            .filter(PipelineRun.profile_id == payload.profile_id)
            .order_by(PipelineRun.id.desc())
            .first()
        )
    if run is None:
        raise HTTPException(status_code=500, detail="Pipeline ran but no run record found")
    return run


@router.get("", response_model=list[RunOut])
def list_runs(profile_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(PipelineRun)
    if profile_id is not None:
        q = q.filter(PipelineRun.profile_id == profile_id)
    return q.order_by(PipelineRun.id.desc()).limit(100).all()


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
