"""Matcher agent: score a tender against the company profile (0-100).

Fully deterministic — no LLM involved. Scoring rules:

- sector/category match: +30
- turnover_avg_3y >= est_value: +25 (or >= 50% of it: +12)
- EMD affordable (emd_amount <= 5% of turnover, or no EMD): +15
- title keyword overlap with sector: +10
- bid_end in the future: +10
- estimated value unknown/unpublished: cap total at 70

Returns ``{"score": int, "reasons": [str]}``. Clamped to 0-100.
"""

from __future__ import annotations

from datetime import datetime

from ._common import to_datetime
from .base import Agent, AgentContext


class MatcherAgent(Agent):
    name = "matcher"

    # ------------------------------------------------------------------
    def score(self, profile: dict, tender: dict) -> dict:
        profile = profile or {}
        tender = tender or {}
        score = 0
        reasons: list = []

        sector = (profile.get("sector") or "").strip().lower()
        category = (tender.get("category") or "").strip().lower()
        title = (tender.get("title") or "").strip().lower()

        if sector and category and (
            sector == category or sector in category or category in sector
        ):
            score += 30
            reasons.append(f"Sector/category match ({sector}/{category}): +30")

        est = tender.get("est_value")
        est_known = bool(tender.get("est_value_known", True)) and est not in (None, 0)
        turnover = profile.get("turnover_avg_3y")
        if est_known and turnover:
            if turnover >= est:
                score += 25
                reasons.append("Turnover covers estimated value: +25")
            elif turnover >= 0.5 * est:
                score += 12
                reasons.append("Turnover covers >=50% of estimated value: +12")
            else:
                reasons.append("Turnover below 50% of estimated value: +0")
        elif est_known:
            reasons.append("Profile turnover unknown: +0")

        emd = tender.get("emd_amount")
        if emd in (None, 0):
            score += 15
            reasons.append("No EMD required: +15")
        elif turnover and emd <= 0.05 * turnover:
            score += 15
            reasons.append("EMD affordable (<=5% of turnover): +15")
        else:
            reasons.append("EMD burden high or turnover unknown: +0")

        if sector:
            sector_words = [w for w in sector.split() if len(w) > 2]
            if any(w in title for w in sector_words):
                score += 10
                reasons.append("Title keyword overlap with sector: +10")

        bid_end = to_datetime(tender.get("bid_end_at"))
        if bid_end is not None and bid_end >= datetime.now():
            score += 10
            reasons.append("Bid deadline is in the future: +10")
        elif bid_end is not None:
            reasons.append("Bid deadline has passed: +0")

        if not est_known:
            reasons.append("Estimated value not published: cap at 70")
            score = min(score, 70)

        return {"score": max(0, min(100, int(score))), "reasons": reasons}

    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext) -> dict:
        result = self.score(ctx.profile, ctx.tender)
        summary = (f"Match score {result['score']}/100 "
                   f"({len(result['reasons'])} reason(s))")
        ctx.log(summary)
        return self.ok(summary, result)
