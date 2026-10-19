"""Bid-pack builder: the document checklist for submitting a bid.

``build_bid_pack(tender, brief)`` returns ``[{"item", "required", "status"}]``:
the standard Indian-procurement items plus extras parsed from the brief's
eligibility criteria (ISO, EPF/ESI, contractor licence, ...). Every item
starts at ``status="pending"``. Nothing here is tender-specific invention —
items come from the standard list, the tender category, or the brief's own
criteria text.

A thin ``BidPackAgent`` wrapper is provided so the orchestrator can treat
this step like the other agents.
"""

from __future__ import annotations

from .base import Agent, AgentContext

STANDARD_ITEMS = [
    "Bid Security Declaration / EMD proof",
    "PAN card copy",
    "GST registration certificate",
    "Udyam registration certificate",
    "Audited financial statements (last 3 years)",
    "Work orders + completion certificates (similar works)",
    "Technical bid forms / Annexures as per NIT",
    "Declaration of non-debarment",
]

GOODS_ITEMS = [
    "Manufacturer authorisation (OEM)",
    "Product datasheets / OEM literature",
]

# keyword in criteria text -> extra checklist item
_CRITERION_ITEM_MAP = [
    ("iso", "ISO certificate"),
    ("epf", "EPF registration proof"),
    ("esi", "ESI registration proof"),
    ("contractor licence", "Contractor licence"),
    ("contractor license", "Contractor licence"),
    ("electrical licence", "Electrical licence"),
    ("electrical license", "Electrical licence"),
    ("nsic", "NSIC registration"),
    ("bis", "BIS certification / product marking"),
    ("oem", "OEM authorisation letter"),
    ("turnover", "CA-certified turnover certificate"),
    ("solvency", "Bank solvency certificate"),
]


def build_bid_pack(tender: dict, brief: dict) -> list:
    """Build the bid-pack checklist for a tender.

    tender: plain tender dict (uses ``category``). brief: DocIntel brief
    (uses ``facts.eligibility_criteria``). Returns a list of
    ``{"item", "required", "status"}`` dicts, all ``"pending"``.
    """
    tender = tender or {}
    brief = brief or {}
    facts = brief.get("facts") or {}

    items: list = []
    seen = set()

    def add(item: str, required: bool = True):
        key = item.lower()
        if key not in seen:
            seen.add(key)
            items.append({"item": item, "required": required,
                          "status": "pending"})

    for s in STANDARD_ITEMS:
        add(s, required=True)

    if (tender.get("category") or "").strip().lower() == "goods":
        for s in GOODS_ITEMS:
            add(s, required=True)

    for crit in facts.get("eligibility_criteria") or []:
        low = str(crit).lower()
        for keyword, item in _CRITERION_ITEM_MAP:
            if keyword in low and keyword not in seen:
                # Avoid duplicating standard items already listed.
                add(item, required=True)

    return items


class BidPackAgent(Agent):
    name = "bidpack"

    def run(self, ctx: AgentContext) -> dict:
        from app.db import BidPackItem, Tender  # lazy: owned by another stream

        tender = ctx.tender or {}
        if not tender.get("id"):
            return self.failed("No tender in context", {"items": []})

        brief = (tender.get("raw_json") or {}).get("brief") or {}
        items = build_bid_pack(tender, brief)

        # Persist: replace this tender's bid-pack rows.
        ctx.db.query(BidPackItem).filter(
            BidPackItem.tender_id == tender["id"]).delete()
        for it in items:
            ctx.db.add(BidPackItem(tender_id=tender["id"], item=it["item"],
                                   required=bool(it["required"]),
                                   status=it["status"]))
        ctx.db.commit()

        summary = f"Bid pack: {len(items)} item(s), all pending"
        ctx.log(summary)
        return self.ok(summary, {"items": items})
