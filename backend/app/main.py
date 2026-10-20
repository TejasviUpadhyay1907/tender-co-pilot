"""FastAPI application entrypoint for the Tender Co-Pilot backend."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import bidpack, deadlines, ingest, profiles, runs, tenders
from app.config import BASE_DIR, settings
from app.db import SessionLocal, Tender, init_db

log = logging.getLogger("tender-copilot")


def _seed_if_present(db) -> None:
    """Import the seed file if it exists and the tenders table is empty."""
    path = settings.SEED_PATH
    if not os.path.exists(path):
        log.info("No seed file at %s; skipping auto-seed", path)
        return
    try:
        from app.ingestion.cppp import import_seed

        result = import_seed(path, db)
        log.info("Auto-seed complete: %s", result)
    except Exception:
        log.exception("Auto-seed failed")
    _repair_profiles(db)


def _repair_profiles(db) -> None:
    """Repair profiles whose experience_json was stored as a bare list
    (an early seed bug): ProfileOut expects the dict shape
    {"items": [...], "max_single_value": N} and a list makes every
    /api/profiles read fail with a 500.

    No demo profile is seeded: the app is used with real company
    profiles that users create themselves."""
    from app.db import CompanyProfile

    try:
        rows = db.query(CompanyProfile).all()
        repaired = False
        for p in rows:
            if isinstance(p.experience_json, list):
                items = p.experience_json
                values = [i.get("value") for i in items if isinstance(i, dict)]
                values = [v for v in values if isinstance(v, (int, float))]
                p.experience_json = {
                    "items": items,
                    "max_single_value": max(values) if values else None,
                }
                repaired = True
        if repaired:
            db.commit()
            log.info("Repaired list-shaped experience_json on existing profiles")
    except Exception:
        db.rollback()
        log.exception("Default profile seed failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        if db.query(Tender).count() == 0:
            _seed_if_present(db)
        if settings.DISABLE_SCHEDULER != "1":
            from app.scheduler import start_scheduler

            start_scheduler(SessionLocal, settings)
            log.info("Scheduler started")
        else:
            log.info("Scheduler disabled (DISABLE_SCHEDULER=1)")
    finally:
        db.close()
    yield
    try:
        from app.scheduler import shutdown as shutdown_scheduler

        shutdown_scheduler()
        log.info("Scheduler stopped")
    except Exception:
        log.exception("Scheduler shutdown failed")


app = FastAPI(title="Tender Co-Pilot", lifespan=lifespan)

app.include_router(profiles.router)
app.include_router(tenders.router)
app.include_router(runs.router)
app.include_router(deadlines.router)
app.include_router(bidpack.router)
app.include_router(ingest.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# Frontend static assets (SPA). Registered last so /api routes match first.
_static_dir = BASE_DIR / "backend" / "app" / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
