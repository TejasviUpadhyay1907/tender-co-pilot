"""Normaliser agent: fill missing category / EMD type by inference.

Only touches tenders where ``category`` or ``emd_type`` is missing (NULL or
empty). Category is inferred from title keywords (works / goods /
services); ``emd_type`` becomes ``"none"`` when ``emd_amount`` is 0/None.
Known values are NEVER overwritten. Every inference is recorded in
``raw_json["inferred"]`` with its source so it stays auditable.
"""

from __future__ import annotations

from sqlalchemy import or_

from .base import Agent, AgentContext

CATEGORY_KEYWORDS = {
    "works": (
        "construction", "construct", "repair", "maintenance", "civil",
        "road", "bridge", "building", "renovation", "retrofitting",
        "pipeline", "drainage", "electrification", "painting",
    ),
    "goods": (
        "supply", "printer", "equipment", "procurement of", "computers",
        "laptop", "furniture", "vehicle", "machinery", "hardware",
        "stationery",
    ),
    "services": (
        "hiring", "consultancy", "consultant", "cleaning", "manpower",
        "facility management", "security services", "housekeeping",
        "catering", "transport service", "amc", "maintenance contract",
    ),
}


def infer_category(title: str) -> str | None:
    """Infer works/goods/services from title keywords; None if unsure."""
    low = (title or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in low for k in keywords):
            return category
    return None


class NormaliserAgent(Agent):
    name = "normaliser"

    def run(self, ctx: AgentContext) -> dict:
        from app.db import Tender  # lazy: owned by another stream

        rows = ctx.db.query(Tender).filter(or_(
            Tender.category.is_(None), Tender.category == "",
            Tender.emd_type.is_(None), Tender.emd_type == "",
        )).all()

        normalized = 0
        details: list = []
        for t in rows:
            raw = dict(t.raw_json) if isinstance(t.raw_json, dict) else {}
            inferred = dict(raw.get("inferred") or {})
            changed = False

            if not t.category:
                cat = infer_category(t.title or "")
                if cat:
                    t.category = cat
                    inferred["category"] = {"value": cat,
                                            "source": "title keywords"}
                    changed = True

            if not t.emd_type:
                if t.emd_amount in (None, 0):
                    t.emd_type = "none"
                    inferred["emd_type"] = {"value": "none",
                                            "source": "emd_amount is 0/None"}
                    changed = True

            if changed:
                raw["inferred"] = inferred
                t.raw_json = raw  # reassign: JSON column change tracking
                normalized += 1
                details.append({"tender_id": t.id, "inferred": inferred})

        ctx.db.commit()
        summary = f"Normalised {normalized} tender(s)"
        ctx.log(summary)
        return self.ok(summary, {"normalized": normalized, "details": details})
