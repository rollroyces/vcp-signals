"""OHLCV cache + S&P 500 historical constituents loader.

Caches daily OHLCV per ticker to ``$VCP_CACHE_DIR/<TICKER>.csv`` (default
``~/.cache/vcp-signals/``). Reads/writes are parallel-safe; once cached,
subsequent scans work offline.

Historical constituents for the S&P 500 come from
``chinobing/historical_sp500_constituents`` (daily snapshots from
1996-01-02 onward). The dataset is auto-updated; we fetch once on
demand and serialize to a parquet/csv in the cache dir for fast lookups.
"""
from __future__ import annotations

import io
import logging
import os
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

logger = logging.getLogger("vcp.cache")

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_CACHE_DIR = os.path.expanduser("~/.cache/vcp-signals")
_SP500_CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/chinobing/historical_sp500_constituents/"
    "main/sp_500_historical_components.csv"
)
# Older alternative if the primary goes away
_SP500_CONSTITUENTS_FALLBACK = (
    "https://raw.githubusercontent.com/fja05680/sp500/main/data/sp500_ticker_start_end.csv"
)


def cache_dir() -> str:
    return os.environ.get("VCP_CACHE_DIR", _DEFAULT_CACHE_DIR)


def ohlcv_path(ticker: str) -> str:
    return os.path.join(cache_dir(), f"{ticker.upper()}.csv")


def constituents_path() -> str:
    return os.path.join(cache_dir(), "_sp500_constituents.parquet")


# ──────────────────────────────────────────────────────────────────────────────
# Historical S&P 500 constituents
# ──────────────────────────────────────────────────────────────────────────────


def load_sp500_constituents(refresh: bool = False) -> pd.Series:
    """Return a Series: date → frozenset of tickers in the S&P 500 on that date.

    The source CSV has rows ``date,"TICKER1,TICKER2,..."``. We expand into a
    series of frozensets (efficient for membership tests) and cache as parquet
    so subsequent calls are O(N) lookup.

    For dates not explicitly in the file, we forward-fill from the most
    recent prior snapshot — standard practice for point-in-time data.

    The result is memoized in the module-level ``_cached_constituents`` so
    repeated calls (which happen many times during a replay) are free.
    """
    global _cached_constituents
    if not refresh and _cached_constituents is not None:
        return _cached_constituents

    path = constituents_path()
    # First try the local parquet — refresh=True forces a re-read but still
    # from disk if the file is there (refresh is meant to bypass the
    # in-memory memo, not the disk cache).
    if os.path.exists(path):
        try:
            series = pd.read_parquet(path)
            # Stored as list-of-strings; convert to frozensets
            series = series["tickers"].apply(frozenset)
            _cached_constituents = series
            return series
        except Exception as e:
            logger.warning(f"constituents parquet read failed ({e}); refetching")

    # Fetch raw CSV
    raw = _fetch_url(_SP500_CONSTITUENTS_URL)
    if raw is None:
        logger.warning("primary constituents URL failed; trying fallback")
        raw = _fetch_url(_SP500_CONSTITUENTS_FALLBACK)
    if raw is None:
        raise RuntimeError("could not fetch S&P 500 constituents from any source")

    # Parse: "date,tickers\nYYYY-MM-DD,\"A,B,C\""
    df = pd.read_csv(io.StringIO(raw))
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    # Tickers stored as comma-separated; split into sets (cheap, one-time).
    # We persist as a sorted list rather than frozenset because pyarrow
    # cannot serialize frozensets; the lookup path converts back to a
    # frozenset on demand.
    df["tickers"] = df["tickers"].str.split(",")
    df["tickers"] = df["tickers"].apply(
        lambda lst: sorted(t.strip() for t in lst if t.strip())
    )
    series = df["tickers"]

    os.makedirs(cache_dir(), exist_ok=True)
    try:
        series.to_frame(name="tickers").to_parquet(path)
    except Exception as e:
        logger.debug(f"could not write parquet ({e}); skipping cache")

    # Convert to frozensets at the API boundary so callers don't see lists.
    series = series.apply(frozenset)
    _cached_constituents = series
    return series


# Module-level memoization. Tests that need a fresh load (with a different
# constituents file) can monkeypatch this to None and call again.
_cached_constituents: pd.Series | None = None


def get_universe_on(date: pd.Timestamp) -> frozenset:
    """Point-in-time S&P 500 membership on a given date.

    The constituents dataset is forward-filled (a name appears in every
    snapshot between its add and remove dates). For dates before the
    dataset starts (1996-01-02) we return an empty set; the caller is
    responsible for stopping the replay window before that.
    """
    series = load_sp500_constituents()
    if date < series.index.min():
        return frozenset()
    # idxmax on values <= date gives the last snapshot <= date
    prior = series.index[series.index <= date]
    if len(prior) == 0:
        return frozenset()
    return series.loc[prior.max()]


def union_universe(start: pd.Timestamp, end: pd.Timestamp) -> set:
    """Return all tickers that were ever in the S&P 500 between two dates.

    Useful for pre-caching OHLCV: we need data for every ticker that
    could have appeared at any scan date in the window.
    """
    series = load_sp500_constituents()
    mask = (series.index >= start) & (series.index <= end)
    window = series.loc[mask]
    out: set = set()
    for s in window:
        out |= set(s)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# OHLCV fetch + cache
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_url(url: str, max_retries: int = 3) -> str | None:
    import urllib.request
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vcp-signals/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                return data.decode("utf-8")
        except Exception as e:
            logger.warning(f"fetch {url} attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    return None


def fetch_and_cache(
    ticker: str,
    period: str = "5y",
    force: bool = False,
) -> str | None:
    """Fetch OHLCV for one ticker, write to cache. Returns path or None.

    Cache hit: skip fetch entirely. This is the hot path during replay —
    we touch thousands of (ticker, date) pairs but only ~500 distinct
    tickers, so the second pass is fully offline.
    """
    path = ohlcv_path(ticker)
    if not force and os.path.exists(path):
        return path

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period, auto_adjust=False)
        if hist is None or len(hist) < 60:
            return None
        if getattr(hist.index, "tz", None) is not None:
            hist = hist.copy()
            hist.index = hist.index.tz_localize(None)
        os.makedirs(cache_dir(), exist_ok=True)
        hist.to_csv(path)
        return path
    except Exception as e:
        logger.debug(f"yfinance fetch {ticker} failed: {e}")
        return None


def fetch_many(
    tickers: Iterable[str],
    period: str = "5y",
    workers: int = 8,
    force: bool = False,
    progress: bool = True,
) -> dict[str, str | None]:
    """Parallel fetch + cache. Returns {ticker: path_or_None}."""
    ticker_list = [t.upper() for t in tickers]
    n = len(ticker_list)
    out: dict[str, str | None] = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_and_cache, t, period, force): t for t in ticker_list}
        for done, f in enumerate(as_completed(futures), 1):
            ticker = futures[f]
            if progress and (done % 50 == 0 or done == n):
                elapsed = time.time() - t0
                cached = sum(1 for v in out.values() if v)
                logger.info(
                    f"OHLCV cache: {done}/{n} "
                    f"({done / elapsed:.0f}/s, {cached} cached)"
                )
            try:
                out[ticker] = f.result()
            except Exception as e:
                logger.debug(f"fetch_many failed for {ticker}: {e}")
                out[ticker] = None
    return out


def load_cached(ticker: str) -> pd.DataFrame | None:
    """Read OHLCV from cache. Canonicalizes columns. Returns None if missing."""
    path = ohlcv_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
        df = df.sort_index()
        # yfinance column normalization (lowercase 'close' on 1.7+)
        cols = {c.lower(): c for c in df.columns}
        rename = {}
        for want in ("Open", "High", "Low", "Close", "Volume"):
            if want in df.columns:
                continue
            if want.lower() in cols:
                rename[cols[want.lower()]] = want
        if rename:
            df = df.rename(columns=rename)
        extras = [c for c in df.columns if c not in
                  ("Open", "High", "Low", "Close", "Volume")]
        if extras:
            df = df.drop(columns=extras)
        return df
    except Exception:
        return None


def cache_stats() -> dict:
    """Return counts and date range of cached OHLCV."""
    base = cache_dir()
    if not os.path.isdir(base):
        return {"ticker_count": 0, "oldest": None, "newest": None, "size_mb": 0.0}
    files = [f for f in os.listdir(base)
             if f.endswith(".csv") and not f.startswith("_")]
    if not files:
        return {"ticker_count": 0, "oldest": None, "newest": None, "size_mb": 0.0}
    sizes = sum(os.path.getsize(os.path.join(base, f)) for f in files)
    # Sample first/last dates from one cached file
    sample = pd.read_csv(
        os.path.join(base, files[0]),
        parse_dates=["Date"], index_col="Date",
        nrows=1,
    )
    last = pd.read_csv(
        os.path.join(base, files[0]),
        parse_dates=["Date"], index_col="Date",
    ).tail(1)
    return {
        "ticker_count": len(files),
        "oldest": str(sample.index[0].date()),
        "newest": str(last.index[0].date()),
        "size_mb": round(sizes / (1024 * 1024), 1),
    }
