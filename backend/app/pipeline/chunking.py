"""Section-aware document chunking for Indian tender bid documents.

Dependency-free: stdlib only (no torch, no chroma, no network), so the
rules/chunking tests never pull heavy deps and this module imports safely
anywhere.

Sections are detected by heading regexes matched at line starts
(case-insensitive). ``SECTION_PATTERNS`` is the ordered list of
``(regex, canonical_section_name)`` pairs. ``chunk_document`` accepts
either ``[{"page", "text"}]`` dicts (preferred; 1-based page numbers) or
a plain list of page-text strings (legacy shape kept for older tests) and
returns chunks ``{"section", "page", "chunk_index", "text"}``. Every chunk
carries a section (default ``"General"``) and a page — the citation
requirement. Long blocks are split on sentence boundaries with a
~150-character overlap so retrieval never loses mid-clause context.
"""

from __future__ import annotations

import re

TARGET_CHUNK_CHARS = 1200
OVERLAP_CHARS = 150
MAX_CHUNK_CHARS = 1600  # absolute cap; a lone overlong sentence is hard-split
DEFAULT_SECTION = "General"

# Ordered (regex, canonical section name). Matched against stripped lines,
# at line starts, case-insensitively. More specific patterns come first so
# e.g. "EMD / Bid Security" wins before generic catch-alls, and the
# "Section <num> - <title>" pattern comes last so it can defer to the
# keyword patterns for its captured title.
SECTION_PATTERNS: list = [
    (re.compile(r"^\s*(notice\s+inviting\s+tender|nit)\b", re.IGNORECASE),
     "NIT / Notice Inviting Tender"),
    (re.compile(r"^\s*(instructions?\s+to\s+bidders?|\bitb\b)", re.IGNORECASE),
     "Instructions to Bidders (ITB)"),
    (re.compile(r"^\s*(general\s+conditions\s+of\s+contract|\bgcc\b)", re.IGNORECASE),
     "General Conditions of Contract (GCC)"),
    (re.compile(r"^\s*(special\s+conditions\s+of\s+contract|\bscc\b)", re.IGNORECASE),
     "Special Conditions of Contract (SCC)"),
    (re.compile(r"^\s*(scope\s+of\s+work|schedule\s+of\s+requirements?|\bsor\b)",
                re.IGNORECASE),
     "Scope of Work / Schedule of Requirements (SOR)"),
    (re.compile(r"^\s*technical\s+specifications?\b", re.IGNORECASE),
     "Technical Specifications"),
    (re.compile(r"^\s*(eligibility(\s+and|\s*/)?\s*qualification\s+criteria|"
                r"eligibility\s+criteria|qualification\s+criteria|"
                r"pre[-\s]?qualification)", re.IGNORECASE),
     "Eligibility / Qualification Criteria"),
    (re.compile(r"^\s*(emd|earnest\s+money|bid\s+security)\b", re.IGNORECASE),
     "EMD / Bid Security"),
    (re.compile(r"^\s*(evaluation\s+criteria|bid\s+evaluation|evaluation\s+methodology)",
                re.IGNORECASE),
     "Evaluation Criteria"),
    (re.compile(r"^\s*(bid\s+forms?|annexures?|annexes|technical\s+bid|"
                r"price\s+bid|financial\s+bid|bill\s+of\s+quantities|\bboq\b)",
                re.IGNORECASE),
     "Bid Forms / Annexures"),
    (re.compile(r"^\s*(payment\s+terms?|terms\s+of\s+payment)", re.IGNORECASE),
     "Payment Terms"),
    (re.compile(r"^\s*(debarment|blacklisting)", re.IGNORECASE),
     "Debarment"),
    (re.compile(r"^\s*section\s+[ivxlcdm0-9]+\s*[-–:—]\s*(.+)$", re.IGNORECASE),
     None),  # canonical name resolved from the captured title (see below)
]


def _canonical_section(line: str) -> str | None:
    """Map a heading line to its canonical section name, or None."""
    line = line.strip()
    if not line:
        return None
    for pattern, name in SECTION_PATTERNS:
        m = pattern.match(line)
        if not m:
            continue
        if name is None:
            # "Section <num> - <title>": resolve the captured title text
            # through the keyword patterns.
            title = m.group(1).strip()
            resolved = _canonical_section(title)
            return resolved if resolved is not None else DEFAULT_SECTION
        return name
    return None


MAX_HEADING_CHARS = 80  # longer matches are body sentences, not headings
MAX_HEADING_WORDS = 8


def _is_heading(line: str) -> bool:
    """True if the line looks like a section heading.

    Headings are short keyword lines matched at line starts; body sentences
    that happen to begin with a keyword (e.g. "Bid security of Rs. ..."
    continuing a paragraph) are excluded by length/word caps.
    """
    stripped = line.strip()
    return (bool(stripped)
            and len(stripped) <= MAX_HEADING_CHARS
            and len(stripped.split()) <= MAX_HEADING_WORDS
            and _canonical_section(stripped) is not None)


def detect_sections(text: str) -> list:
    """Return the heading lines detected in a block of tender text.

    Keeps the full original heading line (e.g. ``"Section II - Instructions
    to Bidders"``) so callers/tests can see exactly what was written; use
    the canonical names from ``SECTION_PATTERNS`` when chunking.
    """
    found: list = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _is_heading(stripped) and stripped not in found:
            found.append(stripped)
    return found


_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")
_ABBR_END = re.compile(r"\b(Rs|No|M/s|Sh|Smt|Dr|vs|etc|i\.e|e\.g)\.[.!?]*$")


def _sentence_spans(text: str) -> list:
    """Split text into (start, end) spans on sentence boundaries.

    Abbreviation-aware: a boundary right after "Rs." / "No." / "M/s" etc.
    is not a sentence end, so amounts like "Rs. 18,00,000" never split.
    """
    spans = []
    last = 0
    for m in _SENTENCE_END.finditer(text):
        end = m.end()
        if _ABBR_END.search(text[last:end].rstrip()):
            continue
        if text[last:end].strip():
            spans.append((last, end))
        last = end
    if text[last:].strip():
        spans.append((last, len(text)))
    if not spans and text.strip():
        spans.append((0, len(text)))
    return spans


def _hard_split_span(text: str, start: int, end: int) -> list:
    """Hard-split an overlong span (no sentence boundary) at word breaks."""
    pieces = []
    pos = start
    while pos < end:
        limit = min(pos + MAX_CHUNK_CHARS, end)
        if limit < end:
            cut = text.rfind(" ", pos, limit)
            if cut <= pos:
                cut = limit
            limit = cut
        pieces.append((pos, limit))
        pos = limit if limit > pos else end
    return pieces


def _chunk_block(text: str) -> list:
    """Split one section block into overlapping chunks on sentence bounds."""
    text = (text or "").strip()
    if not text:
        return []

    # Normalize spans: hard-split any span that exceeds the absolute cap.
    spans: list = []
    for start, end in _sentence_spans(text):
        if end - start > MAX_CHUNK_CHARS:
            spans.extend(_hard_split_span(text, start, end))
        else:
            spans.append((start, end))

    chunks = []
    i = 0
    n = len(spans)
    while i < n:
        j = i
        block_start = spans[i][0]
        while j < n:
            start, end = spans[j]
            size = end - block_start
            if j > i and size > TARGET_CHUNK_CHARS:
                break
            if size > MAX_CHUNK_CHARS:  # should not happen after hard-split
                break
            j += 1
        chunk_end = spans[j - 1][1]
        chunks.append(text[block_start:chunk_end].strip())

        if j >= n:
            break
        # Next chunk starts at the first sentence overlapping the tail of
        # this chunk by ~OVERLAP_CHARS; always make progress.
        overlap_from = chunk_end - OVERLAP_CHARS
        nxt = next((k for k in range(i + 1, j) if spans[k][0] >= overlap_from), j)
        i = nxt
    return [c for c in chunks if c]


def chunk_document(pages: list) -> list:
    """Chunk a tender document into section-aware, page-cited chunks.

    ``pages`` is ``[{"page": <1-based int>, "text": <str>}]``; a legacy
    list of plain page-text strings is also accepted (pages numbered
    from 1 in order).

    Returns ``[{"section", "page", "chunk_index", "text"}]`` where
    ``section`` is the canonical name from ``SECTION_PATTERNS``
    (``"General"`` when no heading was found), ``page`` is the source
    page, and ``chunk_index`` is a global sequential index. No chunk
    exceeds ``MAX_CHUNK_CHARS``; typical chunks are ~``TARGET_CHUNK_CHARS``
    characters with ~``OVERLAP_CHARS`` overlap.
    """
    # Normalize to [{"page", "text"}].
    norm_pages = []
    for idx, p in enumerate(pages or [], start=1):
        if isinstance(p, dict):
            norm_pages.append({"page": p.get("page", idx), "text": p.get("text") or ""})
        else:
            norm_pages.append({"page": idx, "text": p or ""})

    chunks = []
    chunk_index = 0
    for page in norm_pages:
        page_no = page["page"]
        text = page["text"]
        # Split the page into per-section blocks at heading lines.
        blocks: list = []  # (heading_line, block_text)
        current_heading = None
        current_lines: list = []
        for line in text.splitlines():
            if _is_heading(line):
                if current_lines:
                    blocks.append((current_heading, "\n".join(current_lines)))
                current_heading = line.strip()
                current_lines = [line.strip()]
            else:
                current_lines.append(line)
        if current_lines or current_heading is not None:
            blocks.append((current_heading, "\n".join(current_lines)))
        if not blocks:
            blocks.append((None, text))

        for heading, block_text in blocks:
            block_text = (block_text or "").strip()
            if not block_text:
                continue
            section = _canonical_section(heading) if heading else DEFAULT_SECTION
            if section is None:
                section = DEFAULT_SECTION
            for piece in _chunk_block(block_text):
                chunks.append({
                    "section": section,
                    "page": page_no,
                    "chunk_index": chunk_index,
                    "text": piece,
                })
                chunk_index += 1
    return chunks
