"""Unit tests for the backtest harness."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from vcp.backtest import (
    Backtester,
    PriceSource,
    SignalRecord,
    load_signals_from_json,
)


class FakePriceSource(PriceSource):
    """Return a deterministic trending series; configurable per-ticker drift."""

    def __init__(self, drift_per_day: float = 0.001, vol: float = 0.01, seed: int = 0):
        self.drift = drift_per_day
        self.vol = vol
        self.seed = seed

    def get(self, ticker, start, end):
        dates = pd.bdate_range(start, end)
        n = len(dates)
        if n == 0:
            return None
        rng = np.random.default_rng(self.seed + abs(hash(ticker)) % 10000)
        ret = rng.normal(self.drift, self.vol, n)
        prices = 100.0 * np.cumprod(1 + ret)
        return pd.DataFrame({
            "Open": prices, "High": prices * 1.005, "Low": prices * 0.995,
            "Close": prices, "Volume": np.ones(n) * 1e6,
        }, index=dates)


def test_signal_record_default_metadata_is_isolated():
    """Each SignalRecord must get its own metadata dict (no shared default trap)."""
    a = SignalRecord(ticker="A", signal_date=pd.Timestamp("2024-01-01"),
                     entry_price=10.0, score=80.0)
    b = SignalRecord(ticker="B", signal_date=pd.Timestamp("2024-01-01"),
                     entry_price=20.0, score=70.0)
    a.metadata["k"] = "v"
    assert "k" not in b.metadata


def test_backtester_with_fake_source_returns_outcomes():
    src = FakePriceSource(drift_per_day=0.002)
    today = pd.Timestamp("2024-06-01")
    signals = [
        SignalRecord("AAA", today, 100.0, score=80.0, is_signal=True),
        SignalRecord("BBB", today, 100.0, score=70.0, is_signal=True),
        SignalRecord("CCC", today, 100.0, score=50.0, is_signal=False),
    ]
    bt = Backtester(src, horizons=[5, 20], max_workers=1)
    result = bt.run(signals)
    assert len(result.outcomes) == 3
    for o in result.outcomes:
        assert 5 in o.returns and 20 in o.returns
        # With a deterministic source, returns should be finite
        for v in o.returns.values():
            assert v is not None


def test_summary_groups_split_correctly():
    src = FakePriceSource(drift_per_day=0.0, vol=0.0)  # all returns zero
    today = pd.Timestamp("2024-06-01")
    signals = [
        SignalRecord("AAA", today, 100.0, score=80.0, is_signal=True),
        SignalRecord("BBB", today, 100.0, score=70.0, is_signal=True),
        SignalRecord("CCC", today, 100.0, score=50.0, is_signal=False),
    ]
    bt = Backtester(src, horizons=[10])
    result = bt.run(signals)
    summary = result.summary(groups=["all", "signals", "non_signals"])
    # rows = 3 groups * 1 horizon = 3
    assert len(summary) == 3
    # Both signals group should have n=2
    assert int(summary[summary.group == "signals"].iloc[0]["n"]) == 2
    assert int(summary[summary.group == "non_signals"].iloc[0]["n"]) == 1


def test_simulated_portfolio_with_stop_loss_caps_drawdown():
    """A portfolio with a stop loss should never lose more than the stop per trade."""
    src = FakePriceSource(drift_per_day=-0.01, vol=0.02)
    today = pd.Timestamp("2024-06-01")
    signals = [
        SignalRecord(f"T{i:03d}", today, 100.0, score=50.0, is_signal=True)
        for i in range(10)
    ]
    bt = Backtester(src, horizons=[20])
    result = bt.run(signals)
    port = result.simulated_portfolio(horizon=20, stop_loss_pct=0.10)
    # Max drawdown should be bounded by the geometric worst case of 10 losses
    # of 10% each: (0.9)^10 - 1 ≈ -65%. Allow generous slack.
    assert port["max_drawdown_pct"] > -70.0


def test_load_signals_from_legacy_schema(tmp_path):
    """Legacy JSON (signals[] with price/score/is_signal) must round-trip."""
    payload = {
        "timestamp": "2026-05-20T09:30Z",
        "scanned": 2,
        "signals": [
            {"ticker": "AAPL", "price": 150.0, "score": 85.0, "is_signal": True},
            {"ticker": "MSFT", "price": 300.0, "score": 70.0, "is_signal": False},
        ],
    }
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(payload))
    sigs = load_signals_from_json(str(p))
    assert len(sigs) == 2
    assert sigs[0].ticker == "AAPL"
    assert sigs[0].entry_price == 150.0
    assert sigs[0].score == 85.0
    assert sigs[0].is_signal is True
    assert sigs[1].is_signal is False
    # Timestamp parsed (UTC ISO with Z)
    assert sigs[0].signal_date.year == 2026


def test_load_signals_from_current_schema(tmp_path):
    """Current run_scan.py schema (all_signals[] with vcp_quality/vcp_detected)."""
    payload = {
        "timestamp": "2026-09-01T12:00:00",
        "all_signals": [
            {"ticker": "AAPL", "pivot_price": 150.0, "vcp_quality": 0.85,
             "vcp_detected": True, "contractions": 3},
            {"ticker": "MSFT", "pivot_price": 300.0, "vcp_quality": 0.42,
             "vcp_detected": False, "contractions": 2},
        ],
    }
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(payload))
    sigs = load_signals_from_json(str(p))
    assert len(sigs) == 2
    assert sigs[0].is_signal is True
    assert sigs[0].score == pytest.approx(85.0, abs=1e-6)  # 0.85 * 100
    assert sigs[1].is_signal is False


def test_tz_aware_index_does_not_break_comparison():
    """yfinance returns tz-aware indices; the backtester must handle them."""
    dates = pd.date_range("2024-05-30", periods=20, freq="B", tz="America/New_York")
    prices = np.linspace(100, 110, 20)
    df = pd.DataFrame({"Close": prices}, index=dates)
    src = FakePriceSource(drift_per_day=0.001)
    # Override the fake to return a tz-aware df
    def fake_get(ticker, start, end):
        return df
    src.get = fake_get  # type: ignore[assignment]
    today = pd.Timestamp("2024-06-10")  # tz-naive
    signals = [SignalRecord("X", today, 105.0, score=80.0)]
    bt = Backtester(src, horizons=[5])
    out = bt._outcome_for(signals[0])
    assert 5 in out.returns
    assert out.returns[5] is not None
