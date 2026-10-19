"""Deadline agent: track the tender's key dates.

Upserts ``Deadline`` rows for the tender:

- "Bid submission deadline" (kind ``bid_end``)
- "Bid opening" (kind ``bid_opening``)
- "Bid validity expiry" — bid_end + validity days (kind ``validity``)
- "EMD / Bid Security Declaration due" — bid_end - 2 days (kind ``emd``)

Urgency for each row comes from ``rules.deadline_urgency``. Corrigendum
check: when ``raw_json`` carries ``corrigenda_seen``, the DB corrigendum
count is compared against it and new corrigenda raise an alert (the stored
count is then updated); otherwise the check is skipped.

Returns the upcoming deadline list.
"""

from __future__ import annotations

from datetime import timedelta

from ._common import to_datetime
from .base import Agent, AgentContext


class DeadlineAgent(Agent):
    name = "deadline"

    def run(self, ctx: AgentContext) -> dict:
        from app.db import Corrigendum, Deadline, Tender  # lazy: other stream
        from app.rules.engine import deadline_urgency  # lazy: parallel stream

        tender = ctx.tender or {}
        if not tender.get("id"):
            return self.failed("No tender in context", {"upcoming": []})

        db_tender = ctx.db.query(Tender).get(tender["id"])
        if db_tender is None:
            return self.failed(f"Tender {tender['id']} not found in db",
                               {"upcoming": []})

        bid_end = to_datetime(tender.get("bid_end_at"))
        bid_opening = to_datetime(tender.get("bid_opening_at"))
        validity_days = tender.get("bid_validity_days")

        rows = []  # (label, due_at, kind)
        if bid_end is not None:
            rows.append(("Bid submission deadline", bid_end, "bid_end"))
            rows.append(("EMD / Bid Security Declaration due",
                         bid_end - timedelta(days=2), "emd"))
            if validity_days:
                try:
                    rows.append(("Bid validity expiry",
                                 bid_end + timedelta(days=int(validity_days)),
                                 "validity"))
                except (TypeError, ValueError):
                    pass
        if bid_opening is not None:
            rows.append(("Bid opening", bid_opening, "bid_opening"))

        # Upsert: replace this tender's deadline rows.
        ctx.db.query(Deadline).filter(
            Deadline.tender_id == db_tender.id).delete()
        upcoming = []
        for label, due_at, kind in rows:
            ctx.db.add(Deadline(tender_id=db_tender.id, label=label,
                                due_at=due_at, kind=kind))
            upcoming.append({
                "label": label,
                "due_at": due_at.isoformat(),
                "kind": kind,
                "urgency": deadline_urgency(due_at),
            })

        # Corrigendum check (skipped unless the tender tracks a seen count).
        corrigendum_alert = None
        raw = dict(db_tender.raw_json) if isinstance(db_tender.raw_json, dict) else {}
        if "corrigenda_seen" in raw:
            db_count = ctx.db.query(Corrigendum).filter(
                Corrigendum.tender_id == db_tender.id).count()
            try:
                seen = int(raw.get("corrigenda_seen") or 0)
            except (TypeError, ValueError):
                seen = 0
            if db_count > seen:
                corrigendum_alert = {
                    "new_count": db_count - seen,
                    "message": (f"{db_count - seen} new corrigendum/corrigenda "
                                "since last check"),
                }
                raw["corrigenda_seen"] = db_count
                db_tender.raw_json = raw  # reassign for JSON change tracking

        ctx.db.commit()

        data = {"upcoming": upcoming}
        if corrigendum_alert:
            data["corrigendum_alert"] = corrigendum_alert
        summary = (f"{len(upcoming)} deadline(s) tracked"
                   + (f"; {corrigendum_alert['message']}"
                      if corrigendum_alert else ""))
        ctx.log(summary)
        return self.ok(summary, data)
