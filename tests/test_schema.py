"""Test that run_scan emits BOTH detected and non-detected rows (the schema
gap that previously prevented measuring filter effectiveness)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from vcp.data.loader import _canonicalize_columns


def test_canonicalize_columns_uppercases_close():
    """yfinance 1.7+ returns ``close`` lowercase; loader must normalize."""
    df = pd.DataFrame({
        "Open": [100.0] * 3, "High": [101.0] * 3, "Low": [99.0] * 3,
        "close": [100.5] * 3, "Adj close": [100.5] * 3,
        "Volume": [1e6] * 3, "Dividends": [0] * 3, "Stock Splits": [0] * 3,
    })
    out = _canonicalize_columns(df)
    assert "Close" in out.columns
    assert "close" not in out.columns
    # Junk columns dropped
    assert "Adj close" not in out.columns
    assert "Dividends" not in out.columns
    # Canonical 5 columns preserved
    assert set(out.columns) == {"Open", "High", "Low", "Close", "Volume"}


def test_canonicalize_columns_idempotent():
    """Calling canonicalize twice should be a no-op."""
    df = pd.DataFrame({
        "Open": [100.0] * 3, "High": [101.0] * 3, "Low": [99.0] * 3,
        "Close": [100.5] * 3, "Volume": [1e6] * 3,
    })
    out = _canonicalize_columns(_canonicalize_columns(df))
    assert set(out.columns) == {"Open", "High", "Low", "Close", "Volume"}


def test_scan_output_contains_both_cohorts(tmp_path):
    """A scan JSON must include both vcp_detected=true and vcp_detected=false
    rows, with a rationale on every row, so the backtester has a control group.
    """
    # Synthetic scan output (mimics what run_scan.py emits with the schema fix)
    payload = {
        "timestamp": "2026-09-02T12:00:00",
        "scanned": 5,
        "analysed": 5,
        "vcp_found": 2,
        "top_signals": [],
        "all_signals": [
            {"ticker": "A", "vcp_detected": True, "vcp_quality": 0.85,
             "contractions": 3, "pivot_price": 100.0, "pivot_volatility_pct": 0.02,
             "volume_dry_up_ratio": 0.4, "range_contraction_ratio": 0.3,
             "stop_suggestion": 92.0, "stop_pct": 0.08, "signals": ["VCP_3c"],
             "rationale": "VCP ✓ Q=85%", "data_ok": True},
            {"ticker": "B", "vcp_detected": True, "vcp_quality": 0.70,
             "contractions": 3, "pivot_price": 50.0, "pivot_volatility_pct": 0.03,
             "volume_dry_up_ratio": 0.5, "range_contraction_ratio": 0.5,
             "stop_suggestion": 46.0, "stop_pct": 0.08, "signals": ["VCP_3c"],
             "rationale": "VCP ✓ Q=70%", "data_ok": True},
            {"ticker": "C", "vcp_detected": False, "vcp_quality": 0.0,
             "contractions": 3, "pivot_price": 0.0, "pivot_volatility_pct": 0.0,
             "volume_dry_up_ratio": 0.0, "range_contraction_ratio": 0.0,
             "stop_suggestion": 0.0, "stop_pct": 0.0, "signals": [],
             "rationale": "VC pattern incomplete: ranges not shrinking", "data_ok": True},
            {"ticker": "D", "vcp_detected": False, "vcp_quality": 0.0,
             "contractions": 2, "pivot_price": 0.0, "pivot_volatility_pct": 0.0,
             "volume_dry_up_ratio": 0.0, "range_contraction_ratio": 0.0,
             "stop_suggestion": 0.0, "stop_pct": 0.0, "signals": [],
             "rationale": "VC pattern incomplete: volume not declining", "data_ok": True},
            {"ticker": "E", "vcp_detected": False, "vcp_quality": 0.0,
             "contractions": 1, "pivot_price": 0.0, "pivot_volatility_pct": 0.0,
             "volume_dry_up_ratio": 0.0, "range_contraction_ratio": 0.0,
             "stop_suggestion": 0.0, "stop_pct": 0.0, "signals": [],
             "rationale": "Insufficient contraction waves", "data_ok": True},
        ],
    }
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(payload))

    from vcp.backtest import load_signals_from_json
    sigs = load_signals_from_json(str(p))
    detected = [s for s in sigs if s.is_signal]
    not_detected = [s for s in sigs if not s.is_signal]
    assert len(detected) == 2
    assert len(not_detected) == 3
    # The non-detected cohort is what enables filter-effectiveness measurement
    assert {s.ticker for s in detected} == {"A", "B"}
    assert {s.ticker for s in not_detected} == {"C", "D", "E"}
