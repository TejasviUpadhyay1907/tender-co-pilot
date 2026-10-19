"""Shared private helpers for the agent modules.

Row -> plain-dict converters and small parsing utilities. Not part of the
public agent API; agents import from here instead of duplicating logic.
"""

from __future__ import annotations

from datetime import date, datetime


def to_datetime(value) -> datetime | None:
    """Coerce datetime/date/ISO-string to ``datetime``; else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d",
                    "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def tender_to_dict(t) -> dict:
    """SQLAlchemy Tender row -> plain dict the agents work with."""
    if t is None:
        return {}
    raw = t.raw_json if isinstance(getattr(t, "raw_json", None), dict) else {}
    return {
        "id": t.id,
        "tender_id_external": t.tender_id_external,
        "title": t.title or "",
        "organisation": t.organisation or "",
        "ref_no": t.ref_no,
        "category": t.category or "",
        "est_value": t.est_value,
        "est_value_known": bool(t.est_value_known),
        "emd_amount": t.emd_amount,
        "emd_type": t.emd_type,
        "emd_exemption_allowed": t.emd_exemption_allowed,
        "published_at": t.published_at,
        "bid_end_at": t.bid_end_at,
        "bid_opening_at": t.bid_opening_at,
        "bid_validity_days": t.bid_validity_days,
        "source": t.source or "",
        "source_url": t.source_url,
        "is_seed": bool(t.is_seed),
        "raw_json": raw,
    }


def profile_to_dict(p) -> dict:
    """SQLAlchemy CompanyProfile row -> plain dict the agents work with."""
    if p is None:
        return {}
    exp = p.experience_json if isinstance(getattr(p, "experience_json", None), dict) else {}
    return {
        "id": p.id,
        "name": p.name or "",
        "sector": p.sector or "",
        "registration_type": p.registration_type or "",
        "udyam_registered": bool(p.udyam_registered),
        "dpiit_startup": bool(p.dpiit_startup),
        "turnover_avg_3y": p.turnover_avg_3y,
        "turnover_yearly": p.turnover_yearly,
        "experience_json": exp,
        "experience_max_single_value": exp.get("max_single_value"),
        "local_content_pct": p.local_content_pct,
        "certificates": list(p.certifications_json or []),
        "blacklisted": bool(p.blacklisted),
        "notes": p.notes,
    }
