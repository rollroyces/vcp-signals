"""Unit tests for the data loader."""
from __future__ import annotations

import os

import pandas as pd

from vcp.data.loader import (
    _cache_dir,
    _load_from_cache,
    _read_ticker_file,
    get_ticker_list,
)


def test_cache_dir_default_is_user_local(monkeypatch, tmp_path):
    monkeypatch.delenv("VCP_CACHE_DIR", raising=False)
    monkeypatch.setattr("vcp.data.loader._DEFAULT_CACHE_DIR", str(tmp_path))
    assert _cache_dir() == str(tmp_path)


def test_cache_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    assert _cache_dir() == str(tmp_path)


def test_read_ticker_file_filters_but_does_not_dedupe():
    """The file reader filters by ticker shape; dedupe happens upstream in
    get_ticker_list. We assert the filter behaviour only."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("AAPL\nAAPL\n12345\nLONGTICKER\nMSFT\n")
        path = f.name
    try:
        tickers = _read_ticker_file(path)
        # Numeric-leading entries (12345) and over-long names (LONGTICKER) are
        # filtered. Duplicates are preserved — the caller's job to dedupe.
        assert tickers == ["AAPL", "AAPL", "MSFT"]
    finally:
        os.unlink(path)


def test_get_ticker_list_falls_back_to_sp500_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("vcp.data.loader._DEFAULT_CACHE_DIR", str(tmp_path))
    tickers = get_ticker_list()
    assert len(tickers) >= 10  # the built-in S&P 500 sample has 30 names
    assert "AAPL" in tickers


def test_get_ticker_list_reads_from_cache_dir(monkeypatch, tmp_path):
    (tmp_path / "tickers.txt").write_text("AAPL\nMSFT\nNVDA\n")
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    tickers = get_ticker_list()
    assert set(tickers[:3]) >= {"AAPL", "MSFT", "NVDA"}


def test_load_from_cache_reads_csv(monkeypatch, tmp_path):
    df = pd.DataFrame({
        "Date": pd.bdate_range("2024-01-01", periods=10),
        "Open": [100.0] * 10, "High": [101.0] * 10, "Low": [99.0] * 10,
        "Close": [100.5] * 10, "Volume": [1e6] * 10,
    })
    (tmp_path / "AAPL.csv").write_text(df.to_csv(index=False))
    monkeypatch.setenv("VCP_CACHE_DIR", str(tmp_path))
    out = _load_from_cache("AAPL")
    assert out is not None
    assert "Close" in out.columns
    assert len(out) == 10


def test_load_price_data_falls_back_to_yfinance(monkeypatch):
    """If no cache, falls through to yfinance (which we stub to return data)."""
    import numpy as np
    import pandas as pd

    from vcp.data import loader as L

    class FakeYF:
        class Ticker:
            def __init__(self, t): self.t = t
            def history(self, period="1y", auto_adjust=False):
                dates = pd.bdate_range(end=pd.Timestamp.today(), periods=120)
                prices = np.linspace(100, 110, 120)
                return pd.DataFrame({
                    "Open": prices, "High": prices * 1.005, "Low": prices * 0.995,
                    "Close": prices, "Volume": np.ones(120) * 1e6,
                }, index=dates)
    monkeypatch.setattr(L, "_load_from_cache", lambda t: None)
    monkeypatch.setattr("yfinance.Ticker", FakeYF.Ticker)
    hist = L.load_price_data("AAPL")
    assert hist is not None
    assert len(hist) >= 60
