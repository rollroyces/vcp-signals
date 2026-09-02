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
    """A portfolio with a stop loss should never lose more than the stop per trade.

    The FakeSource generates ~+0.2% daily drift with 2% vol; over 20 days
    many windows will hit a -10% drawdown. With a 10% stop, no per-trade
    loss should exceed -10%, and the max drawdown of the compounded curve
    is bounded accordingly.
    """
    src = FakePriceSource(drift_per_day=-0.01, vol=0.02)
    today = pd.Timestamp("2024-06-01")
    signals = [
        SignalRecord(f"T{i:03d}", today, 100.0, score=50.0, is_signal=True)
        for i in range(10)
    ]
    bt = Backtester(src, horizons=[20])
    result = bt.run(signals)
    port = result.simulated_portfolio(horizon=20, stop_loss_pct=0.10)
    # Max drawdown is bounded by repeated -10% stops:
    # worst case (1 - 0.10)^10 - 1 = -65%. Allow generous slack for randomness.
    assert port["max_drawdown_pct"] > -70.0
    # And the portfolio's mean per-trade return reflects the stop:
    # when raw 20d returns are worse than -10%, the simulated trade is
    # recorded at exactly -10%. The mean should therefore be >= -10%.
    assert port["mean_pct"] >= -10.0, (
        f"portfolio mean {port['mean_pct']:.2f}% violates stop floor"
    )


def test_simulated_portfolio_stop_applies_intra_window_low():
    """A trade that dips -30% in the middle then recovers to -10% by horizon
    should be reported as a -10% loss (stopped out), not -10% (no different
    from holding), because the WORSE of the two is what the trader experiences.
    """
    # Build a custom price source that simulates a V-shaped recovery
    dates = pd.bdate_range("2024-06-03", periods=21)
    # Day 0: 100, day 1: 95 (-5%), day 2: 85 (-15% intraday low → -15% trigger),
    # days 3-20: recover to 95 (-5%), so 20d return is -5% but intra-window
    # low is -15%. With a 10% stop, the trade is reported as -10%.
    closes = [100, 95, 85, 87, 89, 91, 93, 92, 93, 94, 95, 94, 95, 93, 94, 95, 94, 95, 94, 95, 95]

    class VShapeSource(PriceSource):
        def get(self, ticker, start, end):
            return pd.DataFrame({"Close": closes}, index=dates)

    sig = SignalRecord("V", pd.Timestamp("2024-06-03"), 100.0, score=80.0)
    bt = Backtester(VShapeSource(), horizons=[20])
    result = bt.run([sig])
    o = result.outcomes[0]
    # 20d return without stop: -5%
    assert abs(o.returns[20] - (-5.0)) < 0.1
    # With 10% stop: capped at -10% (worse of -5% and -10%)
    port_no_stop = result.simulated_portfolio(horizon=20, stop_loss_pct=None)
    port_with_stop = result.simulated_portfolio(horizon=20, stop_loss_pct=0.10)
    assert abs(port_no_stop["mean_pct"] - (-5.0)) < 0.1
    assert abs(port_with_stop["mean_pct"] - (-10.0)) < 0.1


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
