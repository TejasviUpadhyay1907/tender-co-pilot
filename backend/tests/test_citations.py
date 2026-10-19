"""Tests for citation grounding."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.extract import ground_quote


def _chunks():
    return [
        {"text": "The EMD for this work is Rs. 1,87,044 (Rupees one lakh "
                 "eighty seven thousand forty four only).",
         "section": "EMD / Bid Security", "page": 3},
        {"text": "Performance security shall be 3% of the contract value "
                 "as per DoE OM dated 12.11.2020.",
         "section": "General Conditions", "page": 12},
    ]


def test_ground_quote_exact():
    quote = "The EMD for this work is Rs. 1,87,044"
    assert ground_quote(quote, _chunks()) == quote


def test_ground_quote_paraphrase_rejected():
    paraphrase = "This tender requires about one lakh eighty thousand as EMD"
    assert ground_quote(paraphrase, _chunks()) is None


def test_ground_quote_missing_rejected():
    assert ground_quote("Something never written anywhere", _chunks()) is None
