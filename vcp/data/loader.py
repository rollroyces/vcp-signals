"""Data loading for VCP analysis."""
import yfinance as yf
import pandas as pd
from typing import Optional, List, Dict
from datetime import datetime, timedelta

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
    """Get list of tickers to analyze."""
    from vmaa.data.cache import cache_get_batch
    cached = cache_get_batch(None, "fundamentals")  # returns dict
    return list(cached.keys()) if cached else []

def cached_fetch(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Fetch price data for multiple tickers."""
    results = {}
    for t in tickers:
        hist = load_price_data(t)
        if hist is not None:
            results[t] = hist
    return results
