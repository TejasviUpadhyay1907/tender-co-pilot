"""Background jobs: periodic CPPP scans and corrigendum checks.

Jobs run in an APScheduler BackgroundScheduler. Each job is wrapped in
try/except with logging so a single failure never kills the scheduler.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("tender-copilot.scheduler")

_scheduler: Optional[BackgroundScheduler] = None


def _invoke_scanner(scanner: Any, keyword: str, max_results: int = 100) -> Any:
    run = getattr(scanner, "run", None)
    if not callable(run):
        raise AttributeError("ScannerAgent has no callable run() method")
    try:
        return run(keyword=keyword, max_results=max_results)
    except TypeError:
        return run(keyword, max_results)


def _finish_run(db, run, status: str, result: Optional[dict] = None, error: Optional[str] = None):
    run.status = status
    run.result_json = result
    run.error = error
    run.finished_at = datetime.utcnow()
    db.commit()
    db.close()


def _ingest_job(db_session_factory, settings) -> None:
    """Scheduled scan: run ScannerAgent over each configured keyword."""
    from app.db import PipelineRun

    db = db_session_factory()
    run = PipelineRun(
        profile_id=None,
        tender_id=None,
        status="running",
        steps_json={"job": "ingest_scan"},
    )
    db.add(run)
    db.commit()

    try:
        # Built by a parallel workstream; import at call time.
        from app.agents.scanner import ScannerAgent
    except Exception as exc:
        log.exception("ingest job: ScannerAgent unavailable")
        _finish_run(db, run, "failed", error=f"ScannerAgent unavailable: {exc}")
        return

    total_found, total_added = 0, 0
    errors: list[str] = []
    keywords = [
        k.strip()
        for k in str(getattr(settings, "SCAN_KEYWORDS", "") or "").split(",")
        if k.strip()
    ]
    try:
        for kw in keywords:
            try:
                scanner = ScannerAgent()
                res = _invoke_scanner(scanner, kw)
                if isinstance(res, dict):
                    total_found += int(res.get("found") or 0)
                    total_added += int(res.get("added") or 0)
                    if res.get("error"):
                        errors.append(f"{kw}: {res['error']}")
            except Exception as exc:
                log.exception("ingest job failed for keyword %r", kw)
                errors.append(f"{kw}: {exc}")
        _finish_run(
            db,
            run,
            "failed" if errors and total_added == 0 else "done",
            result={"found": total_found, "added": total_added, "keywords": keywords},
            error="; ".join(errors)[:2000] if errors else None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("ingest job crashed")
        try:
            _finish_run(db, run, "failed", error=str(exc)[:2000])
        except Exception:
            db.close()


def _corrigendum_job(db_session_factory) -> None:
    """Re-fetch tender detail pages and record corrigenda on field changes."""
    from app.db import Corrigendum, Tender

    try:
        # Own module, but import at call time so scheduler import stays light.
        from app.ingestion.cppp import IngestError, get_tender_detail
    except Exception:
        log.exception("corrigendum job: cppp module unavailable")
        return

    db = db_session_factory()
    try:
        tenders = db.query(Tender).filter(Tender.source_url.isnot(None)).all()
        for t in tenders:
            try:
                detail = get_tender_detail(t.source_url or "")
            except IngestError as exc:
                log.warning("corrigendum: detail fetch failed for tender %s: %s", t.id, exc)
                continue
            except Exception:
                log.exception("corrigendum: unexpected error for tender %s", t.id)
                continue
            changes: list[str] = []
            for field in ("title", "published_at", "bid_end_at", "bid_opening_at"):
                new = detail.get(field)
                if new is None:
                    continue
                old = getattr(t, field, None)
                if str(new) != str(old):
                    changes.append(f"{field}: {old} -> {new}")
                    setattr(t, field, new)
            if changes:
                db.add(
                    Corrigendum(
                        tender_id=t.id,
                        published_at=datetime.utcnow(),
                        summary="; ".join(changes)[:2000],
                        raw_json={
                            k: (v.isoformat() if hasattr(v, "isoformat") else v)
                            for k, v in detail.items()
                            if v is not None
                        },
                    )
                )
        db.commit()
    except Exception:
        log.exception("corrigendum job failed")
        db.rollback()
    finally:
        db.close()


def start_scheduler(db_session_factory, settings) -> BackgroundScheduler:
    """Start the background scheduler (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="Asia/Kolkata")
    try:
        interval = int(getattr(settings, "INGEST_INTERVAL_HOURS", 6) or 6)
    except (TypeError, ValueError):
        interval = 6
    sched.add_job(
        lambda: _ingest_job(db_session_factory, settings),
        "interval",
        hours=interval,
        id="ingest_job",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        lambda: _corrigendum_job(db_session_factory),
        "interval",
        hours=6,
        id="corrigendum_job",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    log.info("Scheduler started: ingest every %sh, corrigendum every 6h", interval)
    return sched


def shutdown() -> None:
    """Stop the background scheduler if running."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            log.exception("scheduler shutdown error")
        _scheduler = None
