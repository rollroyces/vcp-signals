"""Data loading for VCP analysis."""
import yfinance as yf
import pandas as pd
from typing import Optional, List, Dict
from datetime import datetime, timedelta

VN = "/home/node/.openclaw/workspace/vmaa/data"

def load_price_data(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Load price history for VCP analysis."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist is not None and len(hist) >= 60:
            return hist
    except Exception:
        pass
    return None

def get_ticker_list(source: str = "cache") -> List[str]:
    """Get list of tickers to analyze. Falls back to file-based list."""
    try:
        from vmaa.data.cache import cache_get_batch
        cached = cache_get_batch(None, "fundamentals")
        if cached:
            return list(cached.keys())
    except Exception:
        pass
    # Fallback: read from ticker files
    tickers = []
    import os
    for fn in ["us_all_tickers.txt", "cn_tickers.txt"]:
        fp = os.path.join(VN, fn)
        if os.path.exists(fp):
            with open(fp) as f:
                for line in f:
                    t = line.strip()
                    if t and not t[0].isdigit() and len(t) <= 5:
                        tickers.append(t)
    return tickers

def cached_fetch(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Fetch price data for multiple tickers."""
    results = {}
    for t in tickers:
        hist = load_price_data(t)
        if hist is not None:
            results[t] = hist
    return results
