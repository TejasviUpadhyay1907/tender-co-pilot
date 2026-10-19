"""Citation-grounded extraction for the tender RAG pipeline.

``ground_quote`` is the grounding primitive (kept here, stdlib-only):
every citation ``quote`` in this module must be a verbatim substring of a
retrieved chunk — paraphrases are never allowed inside a quote.

``extract_brief`` pulls the standard fact set for a tender out of the
vector store, using the LLM when one is configured and a deterministic
regex/keyword fallback otherwise. Both paths funnel through
``_ground_citations`` so the verbatim rule holds either way.
"""

from __future__ import annotations

import json
import re


# ---------------------------------------------------------------------------
# Grounding primitive (stdlib-only; used by tests)
# ---------------------------------------------------------------------------
def ground_quote(quote: str, chunks: list) -> str | None:
    """Return the quote if it is a verbatim substring of any chunk, else None.

    chunks: list of dicts with a "text" key. Comparison is whitespace-
    normalized so incidental formatting differences do not break grounding.
    """
    norm_quote = " ".join((quote or "").split())
    if not norm_quote:
        return None
    for chunk in chunks or []:
        text = " ".join((chunk.get("text") or "").split())
        if norm_quote in text:
            return quote
    return None


# ---------------------------------------------------------------------------
# Deterministic extraction primitives
# ---------------------------------------------------------------------------
MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*\d[\d,]*(?:\.\d{1,2})?")

_DATE_RES = [
    re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"),
    re.compile(
        r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\w*\s+\d{4}\b",
        re.IGNORECASE,
    ),
]

_CRIT_KEYWORDS = (
    "turnover", "experience", "certificate", "eligible", "eligibility",
    "qualified", "qualification", "shall have", "must have", "should have",
    "minimum", "at least", "similar work", "completed work",
)

# Retrieval queries per field group.
FIELD_QUERIES = {
    "emd_amount_text": "EMD bid security amount exemption",
    "emd_exemption_text": "EMD exemption MSE startup Udyam",
    "bid_end_text": "bid submission last date due date",
    "bid_opening_text": "bid opening date time",
    "bid_validity_text": "bid validity days",
    "eligibility_criteria": "eligibility criteria turnover experience certificates",
    "evaluation_method": "evaluation L1 QCBS lowest bidder",
    "performance_security_text": "performance security",
    "scope_summary": "scope of work description schedule of requirements",
    "local_content_text": "Make in India local content",
    "corrigendum": "corrigendum amendment clarification",
}

FACT_KEYS = [
    "emd_amount_text", "emd_exemption_text", "bid_end_text", "bid_opening_text",
    "bid_validity_text", "eligibility_criteria", "evaluation_method",
    "performance_security_text", "scope_summary", "local_content_text",
]

UNKNOWN = "UNKNOWN"

_SENT_SPLIT = re.compile(r"(?<=[.!?])[ \t\n\r\f\v]+")
# NOTE: plain " " chars above, NOT \s — \s would also match the \u00a0
# used for abbreviation protection and defeat it.

# Abbreviations whose trailing period must not end a sentence.
_ABBR_RE = re.compile(r"\b(Rs|No|M/s|Sh|Smt|Dr|vs|etc|i\.e|e\.g)\.\s+")


def _sentences(text: str) -> list:
    """Split text into sentence strings (verbatim substrings of text).

    Abbreviation-aware: "Rs. 18,00,000" stays one sentence so money and
    date regexes see the full amount.
    """
    protected = _ABBR_RE.sub(lambda m: m.group(0).replace(" ", " "), text or "")
    return [s.replace(" ", " ").strip()
            for s in _SENT_SPLIT.split(protected) if s.strip()]


def _find_chunks(chunks: list, *needles: str) -> list:
    """Chunks whose text contains any needle (case-insensitive)."""
    out = []
    for c in chunks or []:
        low = (c.get("text") or "").lower()
        if any(n.lower() in low for n in needles):
            out.append(c)
    return out


def _first_sentence_with(chunks: list, *needles: str) -> tuple:
    """Return (sentence, chunk) for the first sentence containing a needle."""
    for c in chunks or []:
        for s in _sentences(c.get("text") or ""):
            low = s.lower()
            if any(n.lower() in low for n in needles):
                return s, c
    return None, None


def _money_sentence(chunks: list, *needles: str) -> tuple:
    """First sentence containing both a money amount and a needle."""
    for c in chunks or []:
        for s in _sentences(c.get("text") or ""):
            if MONEY_RE.search(s) and any(n.lower() in s.lower() for n in needles):
                return s, c
    return None, None


def _date_sentence(chunks: list, *needles: str) -> tuple:
    """First sentence containing both a date and a needle."""
    for c in chunks or []:
        for s in _sentences(c.get("text") or ""):
            if any(rx.search(s) for rx in _DATE_RES) and any(
                n.lower() in s.lower() for n in needles
            ):
                return s, c
    return None, None


def _deterministic_brief(chunks: list) -> tuple:
    """Extract the fact set with regex/keyword rules. No LLM, no invention.

    Returns (facts, citations). Unknown fields are marked "UNKNOWN" and get
    no citation.
    """
    facts = {k: UNKNOWN for k in FACT_KEYS}
    facts["eligibility_criteria"] = []
    citations: list = []

    def cite(chunk, quote):
        if chunk is not None and ground_quote(quote, [chunk]):
            citations.append({
                "section": chunk.get("section", ""),
                "page": chunk.get("page", 0),
                "quote": quote,
            })

    emd_chunks = _find_chunks(chunks, "emd", "bid security", "earnest money")

    s, c = _money_sentence(emd_chunks, "emd", "bid security", "earnest money")
    if s:
        facts["emd_amount_text"] = s
        cite(c, s)

    s, c = _first_sentence_with(
        emd_chunks, "exempt", "exemption", "udyam", "mse", "startup"
    )
    if s:
        facts["emd_exemption_text"] = s
        cite(c, s)

    s, c = _date_sentence(
        chunks, "last date", "due date", "submission of bid", "bid due",
        "submit the bid", "closing date",
    )
    if s:
        facts["bid_end_text"] = s
        cite(c, s)

    s, c = _date_sentence(chunks, "opening of bid", "bid opening", "bids will be opened",
                          "technical bid opening")
    if s:
        facts["bid_opening_text"] = s
        cite(c, s)

    s, c = _first_sentence_with(chunks, "validity")
    if s and re.search(r"\b\d+\s*days?\b", s, re.IGNORECASE):
        facts["bid_validity_text"] = s
        cite(c, s)

    crit_chunks = []
    for c in chunks or []:
        sec = (c.get("section") or "").lower()
        txt = (c.get("text") or "").lower()
        if ("eligib" in sec or "qualif" in sec
                or any(k in txt for k in ("eligib", "qualif", "criteria"))):
            crit_chunks.append(c)
    seen = set()
    for c in crit_chunks:
        for s in _sentences(c.get("text") or ""):
            low = s.lower()
            if any(k in low for k in _CRIT_KEYWORDS) and s not in seen:
                seen.add(s)
                short = s if len(s) <= 200 else s[:197] + "..."
                facts["eligibility_criteria"].append(short)
                if len(facts["eligibility_criteria"]) <= 8:
                    cite(c, s)
            if len(facts["eligibility_criteria"]) >= 10:
                break
        if len(facts["eligibility_criteria"]) >= 10:
            break
    if not facts["eligibility_criteria"]:
        facts["eligibility_criteria"] = [UNKNOWN]

    eval_chunks = _find_chunks(chunks, "evaluation", "l1", "qcbs", "lowest")
    method = UNKNOWN
    method_chunk = None
    for c in eval_chunks:
        low = (c.get("text") or "").lower()
        for s in _sentences(c.get("text") or ""):
            slow = s.lower()
            if "qcbs" in slow:
                method, method_chunk = "QCBS", c
                break
            if re.search(r"\bl1\b", slow) or "lowest" in slow:
                method, method_chunk = "L1 (lowest evaluated bidder)", c
                break
        if method != UNKNOWN:
            break
    facts["evaluation_method"] = method
    if method != UNKNOWN and method_chunk is not None:
        s, _ = _first_sentence_with([method_chunk], "qcbs", "l1", "lowest")
        if s:
            cite(method_chunk, s)

    s, c = _first_sentence_with(chunks, "performance security")
    if s:
        facts["performance_security_text"] = s
        cite(c, s)

    scope_chunks = [c for c in chunks
                    if "scope" in (c.get("section") or "").lower()] or \
        _find_chunks(chunks, "scope of work")
    scope_sents = []
    for c in scope_chunks:
        scope_sents.extend(_sentences(c.get("text") or ""))
        if len(scope_sents) >= 2:
            break
    if scope_sents:
        summary = " ".join(scope_sents[:2])
        facts["scope_summary"] = summary[:600]
        cite(scope_chunks[0], scope_sents[0])

    s, c = _first_sentence_with(chunks, "local content", "make in india")
    if s:
        facts["local_content_text"] = s
        cite(c, s)

    return facts, citations


# ---------------------------------------------------------------------------
# LLM-assisted extraction (falls back to deterministic on any failure)
# ---------------------------------------------------------------------------
_LLM_GROUPS = [
    (["emd_amount_text", "emd_exemption_text"],
     "EMD / bid security amount and any EMD exemption clause"),
    (["bid_end_text", "bid_opening_text", "bid_validity_text"],
     "bid submission last date, bid opening date/time, bid validity period"),
    (["eligibility_criteria"],
     "eligibility / qualification criteria (list each criterion separately)"),
    (["evaluation_method"],
     "evaluation method (L1, QCBS, etc.)"),
    (["performance_security_text"],
     "performance security requirement"),
    (["scope_summary"],
     "scope of work summary"),
    (["local_content_text"],
     "Make in India / local content requirement"),
]


def _llm_extract_group(llm, fields: list, topic: str, chunks: list) -> dict:
    """One structured LLM call for a field group; returns {field: value}."""
    excerpts = []
    for i, c in enumerate(chunks or []):
        excerpt = (c.get("text") or "")[:1500]
        excerpts.append(
            f"[Excerpt {i + 1} | section: {c.get('section', '')} | page: {c.get('page', 0)}]\n{excerpt}"
        )
    system = (
        "You extract facts from Indian government tender documents. "
        "Use ONLY the provided excerpts. If a fact is not present in the "
        "excerpts, write exactly UNKNOWN. Never invent values, dates, or "
        "amounts."
    )
    user = (
        f"Topic: {topic}\n\n"
        "Excerpts:\n" + "\n\n".join(excerpts) + "\n\n"
        "Return a JSON object with exactly these keys: "
        + ", ".join(f'"{f}"' for f in fields) + ".\n"
        'Each value must be either a verbatim quote from the excerpts (or a '
        '" | "-joined list of verbatim quotes for eligibility_criteria) or '
        'exactly "UNKNOWN".'
    )
    raw = llm.chat(system, user, max_tokens=1200)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError("LLM did not return JSON")
    data = json.loads(m.group(0))
    return {f: data.get(f, UNKNOWN) for f in fields}


def _ground_citations(raw_citations: list, chunks: list) -> list:
    """Enforce verbatim grounding: keep only quotes that appear in chunks.

    A quote that fails ``ground_quote`` is truncated to the longest
    whitespace-token prefix that IS a verbatim substring; if nothing
    grounds, the citation is dropped (never paraphrased).
    """
    grounded = []
    for cit in raw_citations or []:
        quote = (cit or {}).get("quote") or ""
        if ground_quote(quote, chunks):
            grounded.append({
                "section": cit.get("section", ""),
                "page": cit.get("page", 0),
                "quote": quote,
            })
            continue
        # Truncate to the longest verbatim token-prefix.
        tokens = quote.split()
        kept = None
        for n in range(len(tokens), 0, -1):
            cand = " ".join(tokens[:n])
            if ground_quote(cand, chunks):
                kept = cand
                break
        if kept:
            grounded.append({
                "section": cit.get("section", ""),
                "page": cit.get("page", 0),
                "quote": kept,
            })
    return grounded


def _find_chunk_for_fact(value: str, chunks: list):
    """Return the first chunk verbatim-containing the fact value."""
    if not value or value == UNKNOWN:
        return None
    for c in chunks or []:
        if ground_quote(value, [c]):
            return c
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _retrieve_chunks(tender_id, store) -> list:
    """Run the per-field retrieval queries and pool unique chunks."""
    pooled: list = []
    seen = set()
    for query in FIELD_QUERIES.values():
        try:
            hits = store.query(tender_id, query, k=6) or []
        except Exception:
            hits = []
        for h in hits:
            key = (h.get("section"), h.get("page"), (h.get("text") or "")[:80])
            if key not in seen:
                seen.add(key)
                pooled.append(h)
    return pooled


def extract_brief(tender: dict, store, llm) -> dict:
    """Extract the standard fact set for a tender, citation-grounded.

    Returns ``{"facts": {...}, "citations": [{"section","page","quote"}]}``.
    Uses the LLM when one is available; otherwise the deterministic
    regex/keyword extractor. Every citation quote is a verbatim substring
    of a retrieved chunk. Never invents data: missing facts are "UNKNOWN".
    """
    tender = tender or {}
    tender_id = tender.get("id")

    chunks = []
    if store is not None and tender_id is not None:
        try:
            chunks = _retrieve_chunks(tender_id, store)
        except Exception:
            chunks = []

    use_llm = llm is not None and bool(getattr(llm, "available", False))
    if use_llm and chunks:
        try:
            facts = {k: UNKNOWN for k in FACT_KEYS}
            facts["eligibility_criteria"] = []
            raw_citations: list = []
            for fields, topic in _LLM_GROUPS:
                group_chunks = []
                seen = set()
                for f in fields:
                    for h in _retrieve_chunks_by_query(tender_id, store, FIELD_QUERIES.get(f, topic)):
                        key = (h.get("section"), h.get("page"), (h.get("text") or "")[:80])
                        if key not in seen:
                            seen.add(key)
                            group_chunks.append(h)
                values = _llm_extract_group(llm, fields, topic, group_chunks[:8])
                for f in fields:
                    v = values.get(f, UNKNOWN)
                    if f == "eligibility_criteria":
                        items = [x.strip() for x in str(v).split(" | ") if x.strip()]
                        facts[f] = items or [UNKNOWN]
                        for item in facts[f]:
                            c = _find_chunk_for_fact(item, chunks)
                            if c:
                                raw_citations.append({
                                    "section": c.get("section", ""),
                                    "page": c.get("page", 0),
                                    "quote": item,
                                })
                    else:
                        facts[f] = v if v else UNKNOWN
                        c = _find_chunk_for_fact(str(v), chunks)
                        if c:
                            raw_citations.append({
                                "section": c.get("section", ""),
                                "page": c.get("page", 0),
                                "quote": str(v),
                            })
            return {"facts": facts,
                    "citations": _ground_citations(raw_citations, chunks)}
        except Exception:
            pass  # degrade to deterministic below

    facts, citations = _deterministic_brief(chunks)
    return {"facts": facts, "citations": citations}


def _retrieve_chunks_by_query(tender_id, store, query: str) -> list:
    try:
        return store.query(tender_id, query, k=6) or []
    except Exception:
        return []


def answer_with_citations(question: str, tender_id, store, llm, k: int = 6) -> dict:
    """Answer a question about a tender with verbatim-grounded citations.

    Returns ``{"answer": str, "citations": [{"section","page","quote"}]}``.
    """
    chunks = _retrieve_chunks_by_query(tender_id, store, question or "")[:k]

    use_llm = llm is not None and bool(getattr(llm, "available", False))
    if use_llm and chunks:
        try:
            excerpts = []
            for i, c in enumerate(chunks):
                excerpts.append(
                    f"[Excerpt {i + 1} | section: {c.get('section', '')} | "
                    f"page: {c.get('page', 0)}]\n{(c.get('text') or '')[:1500]}"
                )
            system = (
                "Answer the question using ONLY the provided excerpts. "
                "If the excerpts do not contain the answer, say so. "
                "Quote verbatim from the excerpts to support each claim."
            )
            answer = llm.chat(system,
                              f"Question: {question}\n\n" + "\n\n".join(excerpts),
                              max_tokens=800)
            raw_citations = []
            for m in re.finditer(r'"([^"]{12,400})"', answer):
                raw_citations.append({
                    "section": "", "page": 0, "quote": m.group(1),
                })
            citations = _ground_citations(raw_citations, chunks)
            # Attach section/page from the grounding chunk when recoverable.
            fixed = []
            for cit in citations:
                c = _find_chunk_for_fact(cit["quote"], chunks)
                fixed.append({
                    "section": c.get("section", "") if c else "",
                    "page": c.get("page", 0) if c else 0,
                    "quote": cit["quote"],
                })
            return {"answer": answer, "citations": fixed}
        except Exception:
            pass

    # Deterministic fallback: answer from the top retrieved sentences.
    sents = []
    for c in chunks:
        for s in _sentences(c.get("text") or ""):
            sents.append((s, c))
            if len(sents) >= 4:
                break
        if len(sents) >= 4:
            break
    if not sents:
        return {"answer": UNKNOWN, "citations": []}
    answer = " ".join(s for s, _ in sents)
    citations = [
        {"section": c.get("section", ""), "page": c.get("page", 0), "quote": s}
        for s, c in sents
    ]
    return {"answer": answer, "citations": citations}
