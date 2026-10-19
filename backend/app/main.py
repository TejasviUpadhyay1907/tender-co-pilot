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
    _seed_default_profile(db)


def _seed_default_profile(db) -> None:
    """Create one demo company profile if none exists, so the pipeline
    (which requires a profile) works out of the box on a fresh deploy."""
    from app.db import CompanyProfile

    try:
        if db.query(CompanyProfile).count() > 0:
            return
        db.add(
            CompanyProfile(
                name="Demo Roads MSME",
                sector="roads",
                registration_type="MSME",
                udyam_registered=True,
                dpiit_startup=False,
                turnover_avg_3y=80000000,
                local_content_pct=60,
                certifications_json=["GST", "PAN"],
                experience_json=[
                    {"type": "road maintenance", "value": 45000000, "year": 2025}
                ],
                notes="Demo profile seeded for first-run preview",
            )
        )
        db.commit()
        log.info("Seeded default company profile")
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
