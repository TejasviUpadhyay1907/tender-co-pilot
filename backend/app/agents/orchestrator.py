"""Pipeline orchestrator: run the agents end to end.

``run_pipeline(db, profile_id, tender_id, store, llm)``:

1. Creates a ``PipelineRun`` (status ``running``) and loads the profile and
   tender as plain dicts.
2. If no ``tender_id`` is given: Scanner -> Normaliser -> Matcher over all
   open tenders, taking the top-8 scores and continuing with the top match.
   Otherwise scores the given tender directly.
3. DocIntel -> Eligibility -> Deadline -> BidPack for the chosen tender.
4. Each step runs in try/except and records ``{"agent", "status",
   "summary"}``; a failed step is marked but the pipeline continues where
   safe. Verdicts, deadlines and bid-pack rows are persisted by their
   agents.
5. Finishes the run (``done``/``failed``) with ``result_json`` as the
   summary ``{"tender_id", "match_score", "verdict", "deadline_count",
   "brief_facts"}``.

``run_match_all(db, profile_id, store, llm)`` scores every open tender for
the profile and returns the top 8 (used standalone and by ``run_pipeline``).
"""

from __future__ import annotations

from datetime import datetime

from ._common import profile_to_dict, tender_to_dict, to_datetime
from .base import AgentContext
from .bidpack import BidPackAgent
from .deadline import DeadlineAgent
from .docintel import DocIntelAgent
from .eligibility import EligibilityAgent
from .matcher import MatcherAgent
from .normaliser import NormaliserAgent
from .scanner import ScannerAgent

MAX_MATCHES = 8


def _open_tenders(db):
    """Tenders whose bid deadline is in the future (or unpublished)."""
    from app.db import Tender  # lazy: owned by another stream

    now = datetime.now()
    return [t for t in db.query(Tender).all()
            if (to_datetime(t.bid_end_at) or datetime.max) >= now]


def run_match_all(db, profile_id: int, store, llm) -> list:
    """Score all open tenders for the profile; return top 8 by score."""
    from app.db import CompanyProfile  # lazy: owned by another stream

    profile_row = db.query(CompanyProfile).get(profile_id)
    profile = profile_to_dict(profile_row)
    matcher = MatcherAgent()
    scored = []
    for t in _open_tenders(db):
        tender = tender_to_dict(t)
        result = matcher.score(profile, tender)
        scored.append({
            "tender_id": tender["id"],
            "title": tender["title"],
            "organisation": tender["organisation"],
            "score": result["score"],
            "reasons": result["reasons"],
        })
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:MAX_MATCHES]


def run_pipeline(db, profile_id: int, tender_id: int | None, store, llm) -> dict:
    """Run the full agent pipeline. Returns the result summary dict."""
    from app.db import CompanyProfile, PipelineRun, Tender  # lazy

    run = PipelineRun(profile_id=profile_id, tender_id=tender_id,
                      status="running", steps_json=[])
    db.add(run)
    db.commit()
    db.refresh(run)

    steps: list = []
    data: dict = {}

    def record(agent_name: str, result: dict | None):
        if result is None:
            steps.append({"agent": agent_name, "status": "failed",
                          "summary": "step raised; see error"})
            return None
        steps.append({"agent": result.get("agent", agent_name),
                      "status": result.get("status", "ok"),
                      "summary": result.get("summary", "")})
        return result

    def attempt(agent, ctx: AgentContext):
        try:
            return record(agent.name, agent.run(ctx))
        except Exception as exc:  # failed but continue if safe
            steps.append({"agent": agent.name, "status": "failed",
                          "summary": f"{type(exc).__name__}: {exc}"})
            return None

    profile = profile_to_dict(db.query(CompanyProfile).get(profile_id))
    tender = tender_to_dict(db.query(Tender).get(tender_id)) if tender_id else None
    ctx = AgentContext(db=db, profile=profile, tender=tender, store=store,
                       llm=llm, run_id=run.id)

    match_score = None
    try:
        if tender is None:
            # Discovery mode: scan, normalise, then match everything open.
            attempt(ScannerAgent(), ctx)
            attempt(NormaliserAgent(), ctx)
            try:
                matches = run_match_all(db, profile_id, store, llm)
            except Exception as exc:
                steps.append({"agent": "matcher", "status": "failed",
                              "summary": f"{type(exc).__name__}: {exc}"})
                matches = []
            data["matches"] = matches
            steps.append({"agent": "matcher", "status": "ok",
                          "summary": f"Scored open tenders; top {len(matches)} kept"})
            if matches:
                tender_id = matches[0]["tender_id"]
                match_score = matches[0]["score"]
                tender = tender_to_dict(db.query(Tender).get(tender_id))
                ctx.tender = tender
                data["match_score"] = match_score
        else:
            # Single-tender mode: score it directly.
            res = attempt(MatcherAgent(), ctx)
            if res:
                match_score = res["data"].get("score")
                data["match_score"] = match_score

        if tender:
            res = attempt(DocIntelAgent(), ctx)
            brief = (res["data"].get("brief") if res else None) or {}
            # Refresh tender dict: DocIntel may have saved the brief.
            tender = tender_to_dict(db.query(Tender).get(tender_id))
            ctx.tender = tender
            data["brief_facts"] = brief.get("facts", {})
            data["citation_count"] = len(brief.get("citations", []))

            res = attempt(EligibilityAgent(), ctx)
            if res:
                data["verdict"] = res["data"].get("verdict")

            res = attempt(DeadlineAgent(), ctx)
            if res:
                data["deadline_count"] = len(res["data"].get("upcoming", []))
                if res["data"].get("corrigendum_alert"):
                    data["corrigendum_alert"] = res["data"]["corrigendum_alert"]

            attempt(BidPackAgent(), ctx)
    except Exception as exc:  # last-resort guard; steps recorded so far
        steps.append({"agent": "orchestrator", "status": "failed",
                      "summary": f"{type(exc).__name__}: {exc}"})

    failed = any(s.get("status") == "failed" for s in steps)
    summary = {
        "run_id": run.id,
        "tender_id": tender_id,
        "match_score": match_score,
        "verdict": data.get("verdict", "UNKNOWN"),
        "deadline_count": data.get("deadline_count", 0),
        "brief_facts": data.get("brief_facts", {}),
    }

    run.tender_id = tender_id
    run.status = "failed" if failed else "done"
    run.steps_json = steps
    run.result_json = summary
    if failed:
        run.error = "; ".join(
            f"{s['agent']}: {s['summary']}"
            for s in steps if s.get("status") == "failed")
    run.finished_at = datetime.utcnow()
    db.commit()

    return summary
