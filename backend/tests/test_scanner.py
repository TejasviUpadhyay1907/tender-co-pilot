"""Tests for the scanner's honesty gate: looks_like_tender."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.scanner import looks_like_tender


def test_valid_cppp_id_passes():
    ok, _ = looks_like_tender({
        "title": "Supply and installation of printers with buy-back",
        "organisation": "IIT Tirupati",
        "tender_id_external": "2026_IITTP_930844_1",
    })
    assert ok


def test_nav_text_rejected():
    ok, reason = looks_like_tender({
        "title": "MIS Reports Tenders by Location Tenders by Organisation",
        "organisation": "",
    })
    assert not ok


def test_announcement_rejected():
    ok, _ = looks_like_tender({
        "title": "Announcement title - Important instructions for Bid Openers",
        "organisation": "CPPP",
    })
    assert not ok


def test_short_title_rejected():
    ok, _ = looks_like_tender({"title": "Tenders", "organisation": "X"})
    assert not ok


def test_no_id_but_corroborated_passes():
    ok, _ = looks_like_tender({
        "title": "Construction of rural road including cross drainage works",
        "organisation": "PWD Division IV",
        "bid_end_at": "2026-11-01T15:00:00",
        "emd_amount": 50000,
    })
    assert ok


def test_no_id_no_corroboration_rejected():
    ok, reason = looks_like_tender({
        "title": "Some random page heading that is long enough here",
    })
    assert not ok
    assert "corroborating" in reason


def test_non_dict_rejected():
    ok, _ = looks_like_tender("not a dict")
    assert not ok
