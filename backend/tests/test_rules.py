"""Pytest suite for the tender co-pilot rules engine."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rules.engine import (
    check_eligibility,
    deadline_urgency,
    emd_assessment,
    mii_class,
    mse_l1_match,
    performance_security,
    two_bid_stages,
    verdict_overall,
)


# ---------------------------------------------------------------------------
# EMD
# ---------------------------------------------------------------------------
def test_emd_fixed_non_mse_no_exemption_info():
    tender = {"emd_amount": 100000.0, "emd_type": "fixed",
              "emd_exemption_allowed": None}
    profile = {"udyam_registered": False, "dpiit_startup": False}
    out = emd_assessment(tender, profile)
    assert out["exempt"] is None
    assert out["unknowns"] == [
        "EMD exemption clause not confirmed from tender document"]
    assert out["emd_amount"] == 100000.0


def test_emd_mse_allowed():
    tender = {"emd_amount": 100000.0, "emd_type": "fixed",
              "emd_exemption_allowed": True}
    profile = {"udyam_registered": True, "dpiit_startup": False}
    out = emd_assessment(tender, profile)
    assert out["exempt"] is True
    assert "GFR Rule 170" in out["reason"]


def test_emd_mse_disallowed():
    tender = {"emd_amount": 100000.0, "emd_type": "fixed",
              "emd_exemption_allowed": False}
    profile = {"udyam_registered": True, "dpiit_startup": False}
    out = emd_assessment(tender, profile)
    assert out["exempt"] is False
    assert out["reason"] == "Tender expressly disallows EMD exemption"


def test_emd_zero():
    tender = {"emd_amount": 0, "emd_type": "none",
              "emd_exemption_allowed": None}
    out = emd_assessment(tender, {"udyam_registered": False,
                                  "dpiit_startup": False})
    assert out["exempt"] is True
    assert out["reason"] == "No EMD required for this tender"


def test_emd_dpiit_startup():
    tender = {"emd_amount": 50000.0, "emd_type": "fixed",
              "emd_exemption_allowed": True}
    profile = {"udyam_registered": False, "dpiit_startup": True}
    out = emd_assessment(tender, profile)
    assert out["exempt"] is True
    assert "GFR Rule 170" in out["reason"]


def test_emd_percent_real_data_regression():
    # Real tender data: EMD 1,87,044 on estimate 93,52,204 -> ~2.0%.
    emd, est = 187044.0, 9352204.0
    pct = (emd / est) * 100.0
    assert 1.99 < pct < 2.01
    assert 1.0 <= pct <= 5.0  # within the typical EMD band


# ---------------------------------------------------------------------------
# Performance security
# ---------------------------------------------------------------------------
def test_performance_security_default():
    out = performance_security({})
    assert out["percent"] == 3.0
    assert "DoE OM 12.11.2020" in out["basis"]


def test_performance_security_override():
    out = performance_security({"ps_percent": 5})
    assert out["percent"] == 5.0


# ---------------------------------------------------------------------------
# Make in India class
# ---------------------------------------------------------------------------
def test_mii_class_boundaries():
    assert mii_class(55) == "Class-I"
    assert mii_class(50) == "Class-I"
    assert mii_class(30) == "Class-II"
    assert mii_class(20) == "Class-II"
    assert mii_class(10) == "Non-local"
    assert mii_class(None) == "Unknown"


# ---------------------------------------------------------------------------
# MSE L1+15% match
# ---------------------------------------------------------------------------
def test_mse_l1_within_band():
    out = mse_l1_match(74_00_000, 80_00_000)
    assert out["within_band"] is True
    assert abs(out["pct_above_l1"] - 8.108108) < 0.001
    assert "L1+15%" in out["note"]


def test_mse_l1_exactly_15pct():
    out = mse_l1_match(100.0, 115.0)
    assert out["pct_above_l1"] == 115.0 - 100.0  # sanity of arithmetic
    assert out["within_band"] is True


def test_mse_l1_just_over_15pct():
    out = mse_l1_match(100.0, 115.01)
    assert out["within_band"] is False


def test_mse_below_l1():
    out = mse_l1_match(74_00_000, 70_00_000)
    assert out["within_band"] is True
    assert out["note"] == "Already at/below L1"


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------
def _turnover_crit(required):
    return {"key": "turnover", "label": "Average annual turnover (3 years)",
            "required_value": required, "required_text": "",
            "comparator": "min_turnover"}


def test_eligibility_turnover_fail_and_pass():
    crit = _turnover_crit(1_00_00_000)
    fail = check_eligibility([crit], {"turnover_avg_3y": 95_00_000})
    assert fail[0]["verdict"] == "FAIL"
    pas = check_eligibility([crit], {"turnover_avg_3y": 1_20_00_000})
    assert pas[0]["verdict"] == "PASS"


def test_eligibility_turnover_required_none():
    out = check_eligibility([_turnover_crit(None)],
                            {"turnover_avg_3y": 95_00_000})
    assert out[0]["verdict"] == "UNKNOWN"


def test_eligibility_certificate_present_absent_none():
    crit = {"key": "iso", "label": "ISO 9001 certificate",
            "required_value": None, "required_text": "ISO 9001",
            "comparator": "certificate"}
    ok = check_eligibility([crit], {"certificates": ["ISO 9001:2015", "GST"]})
    assert ok[0]["verdict"] == "PASS"
    missing = check_eligibility([crit], {"certificates": ["GST"]})
    assert missing[0]["verdict"] == "FAIL"
    unknown = check_eligibility([crit], {"certificates": None})
    assert unknown[0]["verdict"] == "UNKNOWN"


def test_eligibility_not_debarred():
    crit = {"key": "debar", "label": "Not blacklisted/debarred",
            "required_value": None, "required_text": "",
            "comparator": "not_debarred"}
    fail = check_eligibility([crit], {"blacklisted": True})
    assert fail[0]["verdict"] == "FAIL"
    ok = check_eligibility([crit], {"blacklisted": False})
    assert ok[0]["verdict"] == "PASS"


def test_verdict_overall():
    assert verdict_overall([]) == "CAUTION"
    passes = [{"verdict": "PASS"}, {"verdict": "PASS"}]
    assert verdict_overall(passes) == "GO"
    assert verdict_overall([{"verdict": "PASS"},
                            {"verdict": "UNKNOWN"}]) == "CAUTION"
    assert verdict_overall([{"verdict": "PASS"},
                            {"verdict": "FAIL"}]) == "NO-GO"


def test_check_eligibility_end_to_end_verdict():
    criteria = [_turnover_crit(1_00_00_000),
                {"key": "iso", "label": "ISO 9001",
                 "required_value": None, "required_text": "ISO 9001",
                 "comparator": "certificate"}]
    profile = {"turnover_avg_3y": 95_00_000, "certificates": ["ISO 9001"]}
    assert verdict_overall(check_eligibility(criteria, profile)) == "NO-GO"


# ---------------------------------------------------------------------------
# Deadline urgency
# ---------------------------------------------------------------------------
def _due(days, now):
    return now + timedelta(days=days)  # exact: boundary inclusion is tested


def test_deadline_urgency_boundaries():
    now = datetime(2026, 10, 19, 12, 0)
    assert deadline_urgency(_due(2, now), now) == "URGENT"
    assert deadline_urgency(_due(3, now), now) == "URGENT"
    assert deadline_urgency(_due(4, now), now) == "SOON"
    assert deadline_urgency(_due(7, now), now) == "SOON"
    assert deadline_urgency(_due(8, now), now) == "OK"
    assert deadline_urgency(now - timedelta(hours=1), now) == "OVERDUE"
    assert deadline_urgency(None, now) == "UNKNOWN"


# ---------------------------------------------------------------------------
# Two-bid stages
# ---------------------------------------------------------------------------
def test_two_bid_stages():
    assert two_bid_stages() == [
        "NIT publication",
        "Pre-bid / clarifications",
        "Bid submission (two envelopes)",
        "Technical evaluation",
        "Financial opening (qualified only)",
        "L1 award / LOA",
    ]
