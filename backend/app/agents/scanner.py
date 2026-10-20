"""Scanner agent: discover new tenders via the ingestion layer.

Uses ``app.ingestion.cppp.search_tenders`` (lazy import) once per keyword
in ``settings.SCAN_KEYWORDS`` (comma-separated). Dedupes against existing
rows by ``tender_id_external`` first, then by (title, organisation).
Inserts genuinely new tenders as ``Tender`` rows with the raw payload in
``raw_json``. Never invents tender data: if the ingestion module is
missing or a fetch fails, the error is recorded and reported — nothing is
fabricated. Every candidate is validated by :func:`looks_like_tender`
before insert so portal navigation text can never become a tender row.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ._common import to_datetime
from .base import Agent, AgentContext

# CPPP tender IDs look like 2026_NITK_931042_1 (year_org_serial_round).
_TENDER_ID_RE = re.compile(r"^\d{4}_[A-Za-z0-9]+_\d+_\d+$")

# Phrases that mark portal chrome rather than tender listings.
_NAV_HINTS = (
    "mis report", "tender by ", "important instruction", "announcement",
    "help", "sitemap", "login",
)


def looks_like_tender(r: dict) -> tuple[bool, str]:
    """Strict gate: is this dict plausibly a real tender listing?

    Returns (True, "") or (False, reason). A row passes when it carries a
    well-formed portal tender ID, or — without one — when the title is
    substantive AND at least two corroborating fields are present
    (organisation, bid-end date, estimated value, EMD, reference number).
    """
    if not isinstance(r, dict):
        return False, "not a dict"
    title = (r.get("title") or "").strip()
    low = title.lower()
    if any(h in low for h in _NAV_HINTS):
        return False, "portal navigation text"
    if len(title) < 15:
        return False, "title too short"
    ext = (r.get("tender_id_external") or r.get("tender_id") or "").strip()
    if ext and _TENDER_ID_RE.match(ext):
        return True, ""
    org = (r.get("organisation") or r.get("organization") or "").strip()
    signals = sum([
        bool(org and len(org) > 3),
        bool(r.get("bid_end_at")),
        bool(r.get("est_value")),
        bool(r.get("emd_amount")),
        bool(r.get("ref_no")),
    ])
    if signals >= 2:
        return True, ""
    return False, "no tender ID and insufficient corroborating fields"


class ScannerAgent(Agent):
    name = "scanner"

    def run(self, ctx: AgentContext) -> dict:
        from app.config import settings  # lazy: owned by another stream
        from app.db import Tender  # lazy: owned by another stream

        try:
            from app.ingestion import cppp  # lazy: owned by another stream
        except Exception as exc:
            return self.failed(
                f"ingestion.cppp unavailable: {exc}",
                {"found": 0, "added": 0, "errors": [str(exc)], "keywords": []},
            )

        keywords = [k.strip() for k in (settings.SCAN_KEYWORDS or "").split(",")
                    if k.strip()]
        found, added, skipped = 0, 0, 0
        errors: list = []

        # Existing identity sets for dedupe.
        existing_external = {
            row[0] for row in ctx.db.query(Tender.tender_id_external).all()
            if row[0]
        }
        existing_title_org = set()
        for t in ctx.db.query(Tender.id, Tender.title, Tender.organisation).all():
            existing_title_org.add(
                ((t.title or "").strip().lower(),
                 (t.organisation or "").strip().lower()))

        for kw in keywords:
            try:
                results = cppp.search_tenders(kw) or []
            except Exception as exc:
                errors.append(f"{kw}: {exc}")
                continue
            for r in results:
                if not isinstance(r, dict):
                    errors.append(f"{kw}: unexpected result shape, skipped")
                    continue
                ok, reason = looks_like_tender(r)
                if not ok:
                    skipped += 1
                    continue
                found += 1
                ext = (r.get("tender_id_external") or r.get("tender_id")
                       or r.get("ref_no"))
                title = (r.get("title") or "").strip()
                org = (r.get("organisation") or r.get("organization") or "").strip()
                if ext and ext in existing_external:
                    continue
                if (title.lower(), org.lower()) in existing_title_org:
                    continue
                try:
                    raw = {
                        k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
                        for k, v in r.items()
                    }
                    row = Tender(
                        tender_id_external=ext,
                        title=title,
                        organisation=org,
                        ref_no=r.get("ref_no"),
                        category=r.get("category") or "",
                        est_value=r.get("est_value"),
                        est_value_known=r.get("est_value_known", True),
                        emd_amount=r.get("emd_amount"),
                        emd_type=r.get("emd_type"),
                        emd_exemption_allowed=r.get("emd_exemption_allowed"),
                        published_at=to_datetime(r.get("published_at")),
                        bid_end_at=to_datetime(r.get("bid_end_at")),
                        bid_opening_at=to_datetime(r.get("bid_opening_at")),
                        bid_validity_days=r.get("bid_validity_days"),
                        source=r.get("source") or "cppp",
                        source_url=r.get("source_url"),
                        raw_json=raw,
                    )
                    ctx.db.add(row)
                    ctx.db.flush()
                    if ext:
                        existing_external.add(ext)
                    existing_title_org.add((title.lower(), org.lower()))
                    added += 1
                except Exception as exc:
                    ctx.db.rollback()
                    errors.append(f"{kw}: insert failed for {title[:60]!r}: {exc}")

        ctx.db.commit()
        summary = (f"Scanned {len(keywords)} keyword(s): {found} found, "
                   f"{added} new, {skipped} rejected, {len(errors)} error(s)")
        ctx.log(summary)
        return self.ok(summary, {
            "found": found, "added": added, "skipped": skipped,
            "errors": errors, "keywords": keywords,
        })
