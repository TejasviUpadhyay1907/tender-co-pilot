"""Eligibility agent: evaluate the bidder profile against tender criteria.

Builds a criteria list from the DocIntel brief facts plus tender columns
(turnover minimum, experience, certificates, local content, not-debarred,
EMD payable via the rules engine), runs ``rules.check_eligibility`` on it,
appends an EMD-payable check from ``rules.emd_assessment``, rolls up with
``rules.verdict_overall``, and persists ``EligibilityVerdict`` rows for the
current run (old rows for that run are deleted first).

Verdicts are PASS / FAIL / UNKNOWN only — anything unconfirmed stays
UNKNOWN, never guessed. Returns ``{"verdict": overall, "checks": [...]}``.
"""

from __future__ import annotations

import re

from .base import Agent, AgentContext

_MONEY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(lakh|lakh?s|lacs?|crore|crores|cr)\b",
    re.IGNORECASE,
)
_LAKH_CRORE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|crore|crores)",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

_KNOWN_CERTS = (
    "PAN", "GST", "Udyam", "ISO", "EPF", "ESI", "NSIC",
    "electrical licence", "electrical license", "contractor licence",
    "contractor license", "OEM", "BIS",
)


def parse_money_inr(text: str) -> float | None:
    """Parse the first INR amount in text; handles lakh/crore units."""
    if not text:
        return None
    m = _MONEY_RE.search(text)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        unit = (m.group(2) or "").lower()
        if unit.startswith("lac") or unit.startswith("lakh"):
            val *= 100000.0
        elif unit.startswith("crore") or unit == "cr":
            val *= 10000000.0
        return val
    m = _LAKH_CRORE_RE.search(text)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        return val * (100000.0 if m.group(2).lower().startswith("lakh") else 10000000.0)
    return None


def parse_percent(text: str) -> float | None:
    m = _PCT_RE.search(text or "")
    return float(m.group(1)) if m else None


class EligibilityAgent(Agent):
    name = "eligibility"

    # ------------------------------------------------------------------
    def _build_criteria(self, tender: dict, facts: dict) -> list:
        criteria = []
        crit_texts = [c for c in facts.get("eligibility_criteria", [])
                      if c and c != "UNKNOWN"]
        blob = " | ".join(crit_texts)

        # Minimum average annual turnover.
        turnover_req = None
        for c in crit_texts:
            if "turnover" in c.lower():
                turnover_req = parse_money_inr(c)
                if turnover_req:
                    break
        criteria.append({
            "key": "turnover_min",
            "label": "Minimum average annual turnover (last 3 years)",
            "required_value": turnover_req,
            "required_text": (f"Rs. {turnover_req:,.0f}" if turnover_req
                              else (blob[:160] or "UNKNOWN")),
            "comparator": "min_turnover",
        })

        # Experience: completed similar work of a minimum value.
        exp_req = None
        for c in crit_texts:
            low = c.lower()
            if "experience" in low or "similar work" in low or "completed" in low:
                exp_req = parse_money_inr(c)
                if exp_req:
                    break
        criteria.append({
            "key": "experience",
            "label": "Experience of similar completed work",
            "required_value": exp_req,
            "required_text": (f"Rs. {exp_req:,.0f}" if exp_req else "UNKNOWN"),
            "comparator": "min_experience_value",
        })

        # Certificates mentioned in the criteria.
        found_certs = [cert for cert in _KNOWN_CERTS
                       if cert.lower() in blob.lower()]
        for cert in found_certs:
            criteria.append({
                "key": f"cert_{cert}",
                "label": f"Certificate: {cert}",
                "required_value": None,
                "required_text": cert,
                "comparator": "certificate",
            })
        if crit_texts and not found_certs:
            criteria.append({
                "key": "cert_general",
                "label": "Certificates / registrations as per NIT",
                "required_value": None,
                "required_text": "as per NIT",
                "comparator": "custom",
            })

        # Local content (Make in India).
        local_req = parse_percent(facts.get("local_content_text") or "")
        criteria.append({
            "key": "local_content",
            "label": "Minimum local content (Make in India)",
            "required_value": local_req,
            "required_text": (f"{local_req}%" if local_req is not None
                              else "UNKNOWN"),
            "comparator": "min_local_content",
        })

        # Not debarred.
        criteria.append({
            "key": "not_debarred",
            "label": "Not debarred / blacklisted",
            "required_value": None,
            "required_text": "Bidder must not be debarred/blacklisted",
            "comparator": "not_debarred",
        })
        return criteria

    # ------------------------------------------------------------------
    @staticmethod
    def _rules_profile(profile: dict) -> dict:
        return {
            "turnover_avg_3y": profile.get("turnover_avg_3y"),
            "experience_max_single_value": profile.get("experience_max_single_value"),
            "local_content_pct": profile.get("local_content_pct"),
            "certificates": profile.get("certificates") or [],
            "blacklisted": bool(profile.get("blacklisted")),
            "udyam_registered": bool(profile.get("udyam_registered")),
            "dpiit_startup": bool(profile.get("dpiit_startup")),
        }

    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext) -> dict:
        from app.db import EligibilityVerdict  # lazy: owned by another stream
        from app.rules.engine import (  # lazy: built in parallel
            check_eligibility, emd_assessment, verdict_overall,
        )

        tender = ctx.tender or {}
        profile = ctx.profile or {}
        if not tender.get("id"):
            return self.failed("No tender in context",
                               {"verdict": "CAUTION", "checks": []})

        brief = (tender.get("raw_json") or {}).get("brief") or {}
        facts = brief.get("facts") or {}

        criteria = self._build_criteria(tender, facts)
        rules_profile = self._rules_profile(profile)
        checks = check_eligibility(criteria, rules_profile)

        # EMD payable check from the rules engine (never guessed).
        emd_info = emd_assessment(tender, rules_profile)
        exempt = emd_info.get("exempt")
        checks.append({
            "criterion": "EMD / Bid Security Declaration payable",
            "required": emd_info.get("reason") or "UNKNOWN",
            "actual": emd_info.get("emd_amount"),
            "verdict": ("PASS" if exempt is True
                        else "UNKNOWN" if exempt is None else "UNKNOWN"),
            "note": ("Exempt from EMD" if exempt is True
                     else "EMD payable / status unconfirmed — manual review"),
        })

        overall = verdict_overall(checks)

        # Persist verdict rows for this run (replace old ones).
        if ctx.run_id is not None:
            ctx.db.query(EligibilityVerdict).filter(
                EligibilityVerdict.run_id == ctx.run_id).delete()
            for chk in checks:
                ctx.db.add(EligibilityVerdict(
                    run_id=ctx.run_id,
                    criterion=str(chk.get("criterion") or ""),
                    required=str(chk.get("required") or ""),
                    actual=str(chk.get("actual") or ""),
                    verdict=str(chk.get("verdict") or "UNKNOWN"),
                    note=str(chk.get("note") or ""),
                    citation="",
                ))
            ctx.db.commit()

        summary = f"Eligibility verdict: {overall} ({len(checks)} check(s))"
        ctx.log(summary)
        return self.ok(summary, {"verdict": overall, "checks": checks})
