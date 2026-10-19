"""Rules engine for the Indian MSME tender co-pilot.

Pure functions only: no I/O, no network, no invented values.
All inputs are plain dicts and are never mutated.
Domain facts:
- EMD typically 1-5% of estimated value; GFR Rule 170 exempts Udyam MSEs
  and DPIIT startups from EMD where the tender allows it.
- Performance security: 3% per DoE OM dated 12.11.2020 (reduced from 5-10%);
  the tender document overrides.
- Make in India classes: Class-I >= 50% local content, Class-II >= 20%,
  otherwise non-local.
- MSE L1+15% price-match: an MSE within 15% above L1 may be invited to match
  the L1 price, typically for 25% of quantity on divisible items.
- Two-bid system: technical evaluation first, then financial opening for
  technically qualified bidders only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# EMD assessment
# ---------------------------------------------------------------------------
def emd_assessment(tender: dict, profile: dict) -> dict:
    """Assess EMD requirement and exemption for a bidder profile.

    tender keys: emd_amount (float|None), emd_type
    ("fixed"/"percent"/"none"/"unknown"/None), emd_exemption_allowed
    (True|False|None).
    profile keys: udyam_registered (bool), dpiit_startup (bool).

    Returns {"emd_amount", "emd_type", "exempt": True|False|None,
    "reason": str, "unknowns": [str]}.
    """
    emd_amount = tender.get("emd_amount")
    emd_type = tender.get("emd_type")
    exemption_allowed = tender.get("emd_exemption_allowed")

    out = {
        "emd_amount": emd_amount,
        "emd_type": emd_type,
        "exempt": False,
        "reason": "",
        "unknowns": [],
    }

    # No EMD required at all.
    if emd_amount in (None, 0) or emd_type == "none":
        out["exempt"] = True
        out["reason"] = "No EMD required for this tender"
        return out

    exemption_eligible = bool(profile.get("udyam_registered")) or bool(
        profile.get("dpiit_startup")
    )

    if exemption_eligible and exemption_allowed is True:
        out["exempt"] = True
        out["reason"] = (
            "GFR Rule 170 MSE/Startup EMD exemption (as permitted by this tender)"
        )
        return out

    if exemption_eligible and exemption_allowed is False:
        out["exempt"] = False
        out["reason"] = "Tender expressly disallows EMD exemption"
        return out

    if exemption_allowed is None:
        out["exempt"] = None
        out["unknowns"] = ["EMD exemption clause not confirmed from tender document"]
        out["reason"] = "EMD exemption status unconfirmed"
        return out

    # Not MSE/startup-eligible, or no useful exemption info: EMD stands.
    if emd_type == "fixed":
        out["reason"] = f"EMD of Rs.{emd_amount:,.0f} required (fixed)"
    elif emd_type == "percent":
        out["reason"] = f"EMD of {emd_amount}% required"
    else:
        out["reason"] = f"EMD required: {emd_amount} ({emd_type})"
    return out


# ---------------------------------------------------------------------------
# Performance security
# ---------------------------------------------------------------------------
def performance_security(tender: dict) -> dict:
    """Return performance-security percent and its basis."""
    return {
        "percent": float(tender.get("ps_percent") or 3.0),
        "basis": (
            "DoE OM 12.11.2020 (reduced from 5-10%); tender document overrides"
        ),
    }


# ---------------------------------------------------------------------------
# Make in India class
# ---------------------------------------------------------------------------
def mii_class(local_content_pct: Optional[float]) -> str:
    """Classify Make-in-India class from local-content percentage."""
    if local_content_pct is None:
        return "Unknown"
    if local_content_pct >= 50:
        return "Class-I"
    if local_content_pct >= 20:
        return "Class-II"
    return "Non-local"


# ---------------------------------------------------------------------------
# MSE L1+15% price match
# ---------------------------------------------------------------------------
def mse_l1_match(l1_price: float, mse_price: float) -> dict:
    """Check whether an MSE price falls within the L1+15% band."""
    if mse_price <= l1_price:
        return {"pct_above_l1": 0.0, "within_band": True,
                "note": "Already at/below L1"}
    pct_above_l1 = ((mse_price - l1_price) / l1_price) * 100.0
    within = 0 < pct_above_l1 <= 15
    if within:
        note = (
            "Within L1+15%: MSE may be invited to match L1 price, "
            "typically for 25% of quantity (divisible items)"
        )
    else:
        note = "Outside L1+15% band: price-match benefit not available"
    return {"pct_above_l1": pct_above_l1, "within_band": within, "note": note}


# ---------------------------------------------------------------------------
# Eligibility checking
# ---------------------------------------------------------------------------
def check_eligibility(criteria: list, profile: dict) -> list:
    """Evaluate each criterion against the bidder profile.

    criterion item: {"key", "label", "required_value" (number|None),
    "required_text" (str), "comparator" one of "min_turnover",
    "min_experience_value", "min_local_content", "certificate",
    "not_debarred", "custom"}.
    profile keys: turnover_avg_3y, experience_max_single_value,
    local_content_pct, certificates (list of names), blacklisted.

    Returns list of {"criterion","required","actual","verdict":
    "PASS"/"FAIL"/"UNKNOWN","note"}.
    """
    results = []
    for c in criteria:
        comparator = c.get("comparator")
        result = {
            "criterion": c.get("label", c.get("key")),
            "required": c.get("required_value")
            if c.get("required_value") is not None
            else c.get("required_text"),
            "actual": None,
            "verdict": "UNKNOWN",
            "note": "",
        }

        if comparator in ("min_turnover", "min_experience_value", "min_local_content"):
            profile_key = {
                "min_turnover": "turnover_avg_3y",
                "min_experience_value": "experience_max_single_value",
                "min_local_content": "local_content_pct",
            }[comparator]
            actual = profile.get(profile_key)
            required = c.get("required_value")
            result["actual"] = actual
            if actual is None or required is None:
                result["verdict"] = "UNKNOWN"
                result["note"] = "Value not confirmed"
            elif actual >= required:
                result["verdict"] = "PASS"
                result["note"] = "Requirement met"
            else:
                result["verdict"] = "FAIL"
                result["note"] = "Below required value"
        elif comparator == "certificate":
            required_text = (c.get("required_text") or "").lower()
            certs = profile.get("certificates")
            result["actual"] = certs
            if certs is None:
                result["verdict"] = "UNKNOWN"
                result["note"] = "Certificate list not available"
            elif any(required_text in (name or "").lower() for name in certs):
                result["verdict"] = "PASS"
                result["note"] = "Certificate held"
            else:
                result["verdict"] = "FAIL"
                result["note"] = f"Required certificate not found: {c.get('required_text')}"
        elif comparator == "not_debarred":
            blacklisted = profile.get("blacklisted")
            result["actual"] = "blacklisted" if blacklisted else "not blacklisted"
            if blacklisted is True:
                result["verdict"] = "FAIL"
                result["note"] = "Bidder is blacklisted/debarred"
            else:
                result["verdict"] = "PASS"
                result["note"] = "Not debarred"
        elif comparator == "custom":
            result["verdict"] = "UNKNOWN"
            result["note"] = "manual review"
        else:
            result["verdict"] = "UNKNOWN"
            result["note"] = f"Unknown comparator: {comparator}"

        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Deadline urgency
# ---------------------------------------------------------------------------
def deadline_urgency(
    due_at: Optional[datetime], now: Optional[datetime] = None
) -> str:
    """Classify bid-deadline urgency. Timezone-naive datetimes assumed IST."""
    if due_at is None:
        return "UNKNOWN"
    now = now or datetime.now()
    delta = due_at - now
    if delta.total_seconds() < 0:
        return "OVERDUE"
    days = delta / timedelta(days=1)
    if days <= 3:
        return "URGENT"
    if days <= 7:
        return "SOON"
    return "OK"


# ---------------------------------------------------------------------------
# Overall verdict
# ---------------------------------------------------------------------------
def verdict_overall(checks: list) -> str:
    """Roll individual check verdicts into GO / CAUTION / NO-GO."""
    if not checks:
        return "CAUTION"
    verdicts = [c.get("verdict") for c in checks]
    if any(v == "FAIL" for v in verdicts):
        return "NO-GO"
    if any(v == "UNKNOWN" for v in verdicts):
        return "CAUTION"
    return "GO"


# ---------------------------------------------------------------------------
# Two-bid system stages
# ---------------------------------------------------------------------------
def two_bid_stages() -> list:
    """Standard two-bid system procurement stages."""
    return [
        "NIT publication",
        "Pre-bid / clarifications",
        "Bid submission (two envelopes)",
        "Technical evaluation",
        "Financial opening (qualified only)",
        "L1 award / LOA",
    ]
