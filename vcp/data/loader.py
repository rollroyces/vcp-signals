"""Data loading for VCP analysis.

Loaders work without any external cache. Priority order:

  1. yfinance (live) — default; works on any platform with internet access.
  2. Local CSV cache — read from $VCP_CACHE_DIR or ~/.cache/vcp-signals/<TICKER>.csv
     to allow offline / replay backtests.
  3. Fallback ticker list — read `tickers.txt` from the same cache directory, or
     a built-in S&P 500 sample if nothing is available.

The hardcoded Linux path that used to live here (`/home/node/.openclaw/...`)
was removed: it was an artefact of the original VMAA sibling-repo setup and
broke on macOS.
"""
import os

import pandas as pd

_DEFAULT_CACHE_DIR = os.path.expanduser("~/.cache/vcp-signals")


def load_price_data(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """Load OHLCV history for one ticker.

    Returns a DataFrame with columns [Open, High, Low, Close, Volume] indexed by
    date, or None if data could not be retrieved.
    """
    # 1. Local cache
    cached = _load_from_cache(ticker)
    if cached is not None and len(cached) >= 60:
        return cached

    # 2. yfinance
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=False)
        if hist is not None and len(hist) >= 60:
            # Normalize tz-aware → tz-naive for downstream comparisons
            if getattr(hist.index, "tz", None) is not None:
                hist = hist.copy()
                hist.index = hist.index.tz_localize(None)
            return hist
    except Exception:
        pass
    return None


def _cache_dir() -> str:
    return os.environ.get("VCP_CACHE_DIR", _DEFAULT_CACHE_DIR)


def _load_from_cache(ticker: str) -> pd.DataFrame | None:
    base = _cache_dir()
    for ext in (".csv", ".parquet"):
        path = os.path.join(base, f"{ticker.upper()}{ext}")
        if os.path.exists(path):
            try:
                if ext == ".csv":
                    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
                else:
                    df = pd.read_parquet(path)
                df = df.sort_index()
                cols = {c.lower(): c for c in df.columns}
                rename = {}
                for want in ("Open", "High", "Low", "Close", "Volume"):
                    if want in df.columns:
                        continue
                    if want.lower() in cols:
                        rename[cols[want.lower()]] = want
                if rename:
                    df = df.rename(columns=rename)
                return df[["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                return None
    return None


def get_ticker_list(source: str = "auto") -> list[str]:
    """Get the list of tickers to analyze.

    Args:
        source: "auto" tries (cache dir ticker files, then built-in S&P 500 sample),
            "cache" reads from cache dir only, "sp500_sample" returns a small
            built-in list (used when no list is available at all).
    """
    candidates: list[str] = []

    if source in ("auto", "cache"):
        base = _cache_dir()
        for fn in ("us_all_tickers.txt", "cn_tickers.txt", "tickers.txt", "tickers.csv"):
            path = os.path.join(base, fn)
            if os.path.exists(path):
                candidates.extend(_read_ticker_file(path))

    if not candidates and source == "auto":
        candidates = _SP500_SAMPLE

    # Deduplicate preserving order
    seen = set()
    out: list[str] = []
    for t in candidates:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _read_ticker_file(path: str) -> list[str]:
    """Read tickers from a text file (one per line) or CSV (first column)."""
    out: list[str] = []
    try:
        if path.endswith(".csv"):
            df = pd.read_csv(path)
            if df.empty:
                return out
            first_col = df.columns[0]
            for v in df[first_col].astype(str):
                v = v.strip()
                if v and not v[0].isdigit() and len(v) <= 6:
                    out.append(v)
        else:
            with open(path) as f:
                for line in f:
                    t = line.strip()
                    if t and not t[0].isdigit() and len(t) <= 6:
                        out.append(t)
    except Exception:
        pass
    return out


def cached_fetch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch price data for multiple tickers (sequential; safe for thread pools)."""
    results: dict[str, pd.DataFrame] = {}
    for t in tickers:
        hist = load_price_data(t)
        if hist is not None:
            results[t] = hist
    return results


# Built-in small S&P 500 sample. Used when no ticker list is available locally.
# Intentionally small (~30 names) so the scanner remains runnable in dev with
# zero setup. The daily cron should override via the cache directory.
_SP500_SAMPLE: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "BRK-B",
    "JPM", "V", "JNJ", "WMT", "PG", "HD", "MA", "XOM", "CVX", "LLY",
    "ABBV", "MRK", "PEP", "KO", "AVGO", "COST", "DIS", "MCD", "CSCO",
    "TMO", "ABT", "ACN",
]
