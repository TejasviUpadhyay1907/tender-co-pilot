"""Tests for section-aware document chunking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.chunking import chunk_document, detect_sections


def test_detect_sections():
    text = (
        "Section II - Instructions to Bidders\n"
        "Some content here.\n"
        "EMD / Bid Security\n"
        "More content.\n"
    )
    found = detect_sections(text)
    assert "Section II - Instructions to Bidders" in found
    assert "EMD / Bid Security" in found


def _sample_pages():
    return [
        (
            "NIT\n"
            "National Highway work - inviting bids.\n"
            "Estimated cost: Rs. 93,52,204.\n"
        ),
        (
            "Section II - Instructions to Bidders\n"
            "1. Bidders shall submit offers in two envelopes.\n"
            "2. Technical and financial bids shall be separate.\n"
        ),
        (
            "EMD / Bid Security\n"
            "Bid security of Rs. 1,87,044 shall accompany the technical bid.\n"
        ),
    ]


def test_chunk_document_sections_and_pages():
    chunks = chunk_document(_sample_pages())
    assert len(chunks) >= 3
    for c in chunks:
        assert c["section"]  # non-empty section
        assert c["page"] in (1, 2, 3)  # correct page numbers exist
    pages = {c["page"] for c in chunks}
    assert pages == {1, 2, 3}
    sections = [c["section"] for c in chunks]
    assert any("Instructions to Bidders" in s for s in sections)
    assert any("EMD" in s for s in sections)


def test_chunk_document_max_length():
    pages = ["Section X - Test\n" + ("x" * 200 + "\n\n") * 10]
    chunks = chunk_document(pages)
    assert chunks
    assert all(len(c["text"]) <= 1600 for c in chunks)
