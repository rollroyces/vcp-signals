"""Tests for the Stage-2 trend template gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from vcp.trend import TrendConfig, evaluate


def test_evaluate_returns_none_when_disabled():
    """With config.enabled=False, evaluate should still work but always pass."""
    cfg = TrendConfig(enabled=False)
    r = evaluate("AAPL", pd.Timestamp("2024-06-03"), config=cfg)
    # Disabled gate should pass everything; result may be None if no data
    if r is not None:
        assert r.passes is True
        assert r.failures == []


def test_evaluate_passes_for_strong_uptrend():
    """A stock making new highs with rising SMAs and positive RS should pass."""
    cfg = TrendConfig(enabled=True)
    # NVDA during the AI rally was the strongest uptrend in the universe
    r = evaluate("NVDA", pd.Timestamp("2024-06-03"), config=cfg)
    assert r is not None
    assert r.passes, f"NVDA 2024-06-03 should pass trend template, got failures: {r.failures}"
    assert r.price > r.sma_trend
    assert r.sma_fast > r.sma_slow > r.sma_trend
    assert r.sma_trend_slope > 0
    assert r.rs_avg > 0


def test_evaluate_fails_for_downtrend():
    """A stock in a clear downtrend should fail multiple SMA checks."""
    cfg = TrendConfig(enabled=True)
    # TSLA in mid-2022 was in a major drawdown
    r = evaluate("TSLA", pd.Timestamp("2022-06-01"), config=cfg)
    assert r is not None
    assert not r.passes
    # Must fail at least one SMA check
    " ".join(r.failures)
    assert any("SMA" in f for f in r.failures), f"expected SMA failures, got: {r.failures}"


def test_evaluate_fails_when_price_below_sma200():
    """A stock below its 200-day SMA fails the trend template."""
    cfg = TrendConfig(enabled=True)
    # NVDA late 2025 was below its short-term SMA after pulling back
    r = evaluate("NVDA", pd.Timestamp("2025-12-01"), config=cfg)
    assert r is not None
    assert not r.passes
    assert any("price" in f and "SMA" in f for f in r.failures)


def test_evaluate_returns_none_when_insufficient_history():
    """With a date too close to cache start, return None (not an error)."""
    cfg = TrendConfig(enabled=True)
    # First cache date is 2020-09-02; asking for 2019 should fail
    r = evaluate("AAPL", pd.Timestamp("2019-01-01"), config=cfg)
    assert r is None


def test_evaluate_fails_with_negative_relative_strength():
    """AAPL in mid-2024 had negative RS vs SPY — should fail the RS check."""
    cfg = TrendConfig(enabled=True)
    r = evaluate("AAPL", pd.Timestamp("2024-06-03"), config=cfg)
    if r is None:
        pytest.skip("AAPL history doesn't extend far enough")
    assert r.rs_avg < 0  # Apple lagged SPY in this period
    # Should fail at least the RS check
    assert any("RS" in f for f in r.failures), f"expected RS failure, got: {r.failures}"


def test_trend_config_is_frozen():
    """TrendConfig should be immutable."""
    cfg = TrendConfig()
    with pytest.raises((AttributeError, Exception)):
        cfg.rs_min_avg = 5.0  # type: ignore[misc]
