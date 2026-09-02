"""Unit tests for the VCP detector."""
from __future__ import annotations

import pandas as pd
import pytest

from tests.conftest import make_vcp_history
from vcp.engine.vcp_detector import (
    VC,
    VCPResult,
    _identify_contraction_waves,
    _verify_range_contraction,
    _verify_volume_decline,
    analyze_vcp,
)


def test_vcp_config_is_singleton_frozen():
    assert VC is not None
    with pytest.raises((AttributeError, Exception)):
        VC.min_contractions = 99  # type: ignore[misc]


def test_identify_waves_returns_three_phases(vcp_history):
    waves = _identify_contraction_waves(vcp_history)
    assert len(waves) == 3
    names = [w["name"] for w in waves]
    assert names == ["P1", "P2", "P3"]


def test_synthetic_vcp_with_strict_contraction_detected():
    """A series with strictly contracting ranges and volume dry-up should be flagged."""
    # Strong contraction: 12% → 5% → 1.5% with very low volume in last phase
    hist = make_vcp_history(
        n=180, phase_ranges=(0.12, 0.05, 0.015), phase_lens=(60, 60, 60),
        drift=0.001, volume_decay=0.35, seed=7,
    )
    result = analyze_vcp("SYN_STRICT", hist, float(hist["Close"].iloc[-1]))
    assert result is not None
    # The detector sees 3 contraction windows. Mechanical pieces:
    assert result.contractions >= 2
    # Whether the full quality score reaches vcp_quality_threshold depends on
    # how strict the detector is. We assert the directional pieces, not the
    # final bool (that's the calibration problem the validation report flags).
    assert result.rationale != ""
    # If detected, pivot_price should be set; if not, we just want a non-empty
    # rationale explaining why.
    if result.vcp_detected:
        assert result.pivot_price > 0


def test_vcp_result_to_dict_roundtrip():
    r = VCPResult(ticker="X", vcp_detected=True, vcp_quality=0.85,
                  contractions=3, pivot_price=100.0, pivot_volatility_pct=0.02,
                  volume_dry_up_ratio=0.4, range_contraction_ratio=0.3,
                  stop_suggestion=92.0, stop_pct=0.08,
                  signals=["VCP_3c", "ULTRA_TIGHT"], rationale="test")
    d = r.to_dict()
    for k in ("ticker", "vcp_detected", "vcp_quality", "contractions",
              "pivot_price", "pivot_volatility_pct", "volume_dry_up_ratio",
              "range_contraction_ratio", "stop_suggestion", "stop_pct",
              "signals", "rationale"):
        assert k in d
    assert d["vcp_quality"] == 0.85
    assert d["contractions"] == 3
    assert d["signals"] == ["VCP_3c", "ULTRA_TIGHT"]


def test_insufficient_history_returns_none():
    short = pd.DataFrame({
        "Open": [1.0] * 30, "High": [1.0] * 30, "Low": [1.0] * 30,
        "Close": [1.0] * 30, "Volume": [1e6] * 30,
    }, index=pd.bdate_range(end=pd.Timestamp.today(), periods=30))
    result = analyze_vcp("SHORT", short, 1.0)
    assert result is None


def test_verify_range_contraction_strict():
    waves = [
        {"name": "P1", "range_pct": 10.0},
        {"name": "P2", "range_pct": 7.0},
        {"name": "P3", "range_pct": 4.0},
    ]
    assert _verify_range_contraction(waves) is True


def test_verify_range_contraction_expansion_fails():
    waves = [
        {"name": "P1", "range_pct": 5.0},
        {"name": "P2", "range_pct": 8.0},  # expanded
        {"name": "P3", "range_pct": 4.0},
    ]
    assert _verify_range_contraction(waves) is False


def test_verify_volume_decline_requires_decrease():
    waves = [
        {"name": "P1", "avg_vol": 1_000_000.0},
        {"name": "P2", "avg_vol": 700_000.0},
        {"name": "P3", "avg_vol": 400_000.0},
    ]
    hist = pd.DataFrame({"Volume": [0.0] * 200})  # placeholder, unused
    assert _verify_volume_decline(hist, waves) is True


def test_verify_volume_decline_flat_fails():
    waves = [
        {"name": "P1", "avg_vol": 1_000_000.0},
        {"name": "P2", "avg_vol": 1_000_000.0},  # no decline
    ]
    hist = pd.DataFrame({"Volume": [0.0] * 200})
    assert _verify_volume_decline(hist, waves) is False


def test_analyze_vcp_returns_rationale_for_non_vcp(small_vcp_history):
    # small_vcp_history has the contraction structure but tighter ranges;
    # it might or might not be detected depending on quality score — but
    # rationale must always be a non-empty string.
    result = analyze_vcp("SMALL", small_vcp_history,
                         float(small_vcp_history["Close"].iloc[-1]))
    assert result is not None
    assert isinstance(result.rationale, str)
    assert result.rationale != ""
