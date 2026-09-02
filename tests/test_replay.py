"""Unit tests for the OHLCV cache + replay harness."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


@pytest.fixture
def fresh_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_load_sp500_constituents_returns_series(fresh_cache_dir):
    from vcp.cache import load_sp500_constituents
    s = load_sp500_constituents(refresh=True)
    assert isinstance(s, pd.Series)
    assert len(s) > 3000  # ~30 years of daily snapshots
    assert s.index[0] < pd.Timestamp("2000-01-01")
    # Sample membership
    sample = s.iloc[len(s) // 2]
    assert isinstance(sample, frozenset)
    assert len(sample) > 400  # S&P 500 has 500 names (plus a few share classes)


def test_get_universe_on_point_in_time(fresh_cache_dir):
    from vcp.cache import get_universe_on
    # AAPL is in the S&P 500 today
    today = pd.Timestamp("2024-06-03")
    universe = get_universe_on(today)
    assert "AAPL" in universe
    assert "MSFT" in universe


def test_get_universe_before_dataset_returns_empty(fresh_cache_dir):
    from vcp.cache import get_universe_on
    universe = get_universe_on(pd.Timestamp("1990-01-01"))
    assert universe == frozenset()


def test_union_universe_covers_window(fresh_cache_dir):
    from vcp.cache import union_universe
    u = union_universe(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-12-31"))
    assert "AAPL" in u
    # Newly added names within the window are also included
    # (this is the whole point of the union)


def test_load_cached_returns_none_when_missing(fresh_cache_dir):
    from vcp.cache import load_cached
    assert load_cached("ZZZZZZZ") is None


def test_load_cached_canonicalizes_columns(fresh_cache_dir):
    """A CSV with lowercase 'close' must be canonicalized to 'Close'."""
    df = pd.DataFrame({
        "Date": pd.bdate_range("2024-01-01", periods=20),
        "Open": [100.0] * 20, "High": [101.0] * 20, "Low": [99.0] * 20,
        "close": [100.5] * 20, "Volume": [1e6] * 20,
    })
    (fresh_cache_dir / "TEST.csv").write_text(df.to_csv(index=False))
    from vcp.cache import load_cached
    out = load_cached("TEST")
    assert out is not None
    assert "Close" in out.columns
    assert "close" not in out.columns


def test_cache_stats_handles_empty_dir(fresh_cache_dir):
    from vcp.cache import cache_stats
    stats = cache_stats()
    assert stats["ticker_count"] == 0


def test_replay_dates_returns_correct_stride():
    from vcp.replay import replay_dates
    dates = replay_dates(pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-10"),
                         stride_days=2)
    assert len(dates) >= 2  # At least a couple of dates
    # All dates are trading days (Mon-Fri)
    for d in dates:
        assert d.weekday() < 5


def test_replay_produces_signals_in_cache_only_env(tmp_path, monkeypatch):
    """End-to-end: cache a tiny OHLCV set, run a small replay, verify the
    harness completes without error and touches the cache.

    The exact VCP detection on synthetic data is tested elsewhere; here we
    just verify the replay path runs without crashing when fed a fresh
    cache directory.
    """
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    dates = pd.bdate_range(end=pd.Timestamp("2024-06-03"), periods=180)
    # Trending-up series (no VCP expected, but the harness should still
    # return one row per (date, ticker))
    close = pd.Series([100.0 + i * 0.05 for i in range(180)], index=dates)
    high = close * 1.005
    low = close * 0.995
    vol = pd.Series([1e6] * 180, index=dates)
    df = pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=dates)
    # Match the yfinance cache layout: Date column as header, not index
    df_with_date = df.copy()
    df_with_date.index.name = "Date"
    (tmp_path / "AAPL.csv").write_text(df_with_date.to_csv(index=True))

    # Build a constituents file in the test's cache dir, then patch the
    # replay module to load from it. The replay's load_sp500_constituents
    # already reads from VCP_CACHE_DIR via constituents_path(), so we just
    # need to drop the file there.
    from vcp.cache import constituents_path, load_sp500_constituents
    test_series = pd.Series([["AAPL"]],
                            index=pd.DatetimeIndex([pd.Timestamp("2024-06-03")]),
                            name="tickers")
    test_series.to_frame().to_parquet(constituents_path())
    # Force reload (don't use cached version from a prior test session)
    import vcp.cache as cache_mod
    cache_mod._cached_constituents = None
    # Sanity check that the load picks up our test file
    s = load_sp500_constituents(refresh=True)
    assert "AAPL" in s.iloc[0]

    from vcp.replay import replay
    sigs = replay(pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-03"),
                  stride_days=1, workers=1, progress=False)
    # One ticker, one date → exactly one SignalRecord (whether detected or not)
    assert len(sigs) == 1
    assert sigs[0].ticker == "AAPL"
    assert sigs[0].signal_date == pd.Timestamp("2024-06-03")
