"""DocIntel agent: turn a tender's bid document into a cited brief.

If ``raw_json["pdf_path"]`` exists on disk (and a vector store is wired
up): extract pages -> chunk -> upsert into the store -> replace
``TenderChunk`` rows -> ``extract_brief`` -> save the brief into
``raw_json["brief"]``.

Otherwise: reuse an existing ``raw_json["brief"]`` (or build one from
``raw_json["criteria"]``), and as a last resort build a brief from the
tender's own NIT fields with every unconfirmed fact marked UNKNOWN.

Returns the brief plus a citation count. Never invents facts.
"""

from __future__ import annotations

import os

from ._common import tender_to_dict
from .base import Agent, AgentContext


def _blank_brief() -> dict:
    return {
        "facts": {
            "emd_amount_text": "UNKNOWN",
            "emd_exemption_text": "UNKNOWN",
            "bid_end_text": "UNKNOWN",
            "bid_opening_text": "UNKNOWN",
            "bid_validity_text": "UNKNOWN",
            "eligibility_criteria": ["UNKNOWN"],
            "evaluation_method": "UNKNOWN",
            "performance_security_text": "UNKNOWN",
            "scope_summary": "UNKNOWN",
            "local_content_text": "UNKNOWN",
        },
        "citations": [],
    }


class DocIntelAgent(Agent):
    name = "docintel"

    # ------------------------------------------------------------------
    def _brief_from_nit_fields(self, tender: dict) -> dict:
        """Last-resort brief built only from the tender's own columns."""
        brief = _blank_brief()
        facts = brief["facts"]
        if tender.get("emd_amount"):
            facts["emd_amount_text"] = f"Rs. {tender['emd_amount']:,.0f} (from NIT fields)"
        if tender.get("bid_end_at"):
            facts["bid_end_text"] = str(tender["bid_end_at"]) + " (from NIT fields)"
        if tender.get("bid_opening_at"):
            facts["bid_opening_text"] = str(tender["bid_opening_at"]) + " (from NIT fields)"
        if tender.get("bid_validity_days"):
            facts["bid_validity_text"] = (
                f"{tender['bid_validity_days']} days (from NIT fields)")
        if tender.get("title"):
            facts["scope_summary"] = tender["title"] + " (from NIT title)"
        return brief

    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext) -> dict:
        from app.db import Tender, TenderChunk  # lazy: owned by another stream

        tender = ctx.tender or {}
        if not tender.get("id"):
            return self.failed("No tender in context", {"brief": _blank_brief()})

        db_tender = ctx.db.query(Tender).get(tender["id"])
        if db_tender is None:
            return self.failed(f"Tender {tender['id']} not found in db",
                               {"brief": _blank_brief()})

        raw = dict(db_tender.raw_json) if isinstance(db_tender.raw_json, dict) else {}
        pdf_path = raw.get("pdf_path")

        # -- Full pipeline when a PDF is available -----------------------
        if pdf_path and os.path.exists(pdf_path) and ctx.store is not None:
            try:
                from app.pipeline.pdf_extract import extract_pages
                from app.pipeline.chunking import chunk_document
                from app.rag.extract import extract_brief
            except Exception as exc:
                return self.failed(f"Pipeline modules unavailable: {exc}",
                                   {"brief": _blank_brief()})

            pages = extract_pages(pdf_path)
            chunks = chunk_document(pages)
            ctx.store.upsert(db_tender.id, chunks)

            ctx.db.query(TenderChunk).filter(
                TenderChunk.tender_id == db_tender.id).delete()
            for c in chunks:
                ctx.db.add(TenderChunk(
                    tender_id=db_tender.id,
                    section=c.get("section", ""),
                    page=c.get("page"),
                    chunk_index=c.get("chunk_index", 0),
                    text=c.get("text") or "",
                ))

            brief = extract_brief(tender_to_dict(db_tender), ctx.store, ctx.llm)
            raw["brief"] = brief
            db_tender.raw_json = raw  # reassign for JSON change tracking
            ctx.db.commit()

            n_cit = len(brief.get("citations", []))
            summary = (f"Processed {len(chunks)} chunk(s) from "
                       f"{len(pages)} page(s); brief with {n_cit} citation(s)")
            ctx.log(summary)
            return self.ok(summary, {"brief": brief, "chunks": len(chunks),
                                     "citations": n_cit})

        # -- Reuse paths ---------------------------------------------------
        if isinstance(raw.get("brief"), dict) and raw["brief"].get("facts"):
            brief = raw["brief"]
            summary = "Reused existing brief from raw_json"
            ctx.log(summary)
            return self.ok(summary, {"brief": brief,
                                     "citations": len(brief.get("citations", []))})

        if raw.get("criteria"):
            brief = _blank_brief()
            crit = raw["criteria"]
            items = crit if isinstance(crit, list) else [str(crit)]
            labels = []
            for x in items:
                if isinstance(x, dict):
                    labels.append(str(x.get("label") or x.get("text") or ""))
                else:
                    labels.append(str(x))
            brief["facts"]["eligibility_criteria"] = [
                lab[:200] for lab in labels if lab.strip()
            ] or ["UNKNOWN"]
            raw["brief"] = brief
            db_tender.raw_json = raw  # reassign for JSON change tracking
            ctx.db.commit()
            summary = "Built brief from raw_json criteria (no PDF indexed)"
            ctx.log(summary)
            return self.ok(summary, {"brief": brief, "citations": 0})

        # -- Last resort: NIT columns only, unknowns marked ----------------
        brief = self._brief_from_nit_fields(tender_to_dict(db_tender))
        raw["brief"] = brief
        db_tender.raw_json = raw  # reassign for JSON change tracking
        ctx.db.commit()
        summary = "Built brief from NIT fields only; unconfirmed facts marked UNKNOWN"
        ctx.log(summary)
        return self.ok(summary, {"brief": brief, "citations": 0})
