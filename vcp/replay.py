"""Historical replay harness for VCP validation.

Runs the VCP detector on the cached OHLCV at multiple historical dates and
emits SignalRecord rows for the backtester to measure forward returns.

Design:
  • Periodic replay: every N trading days (default 5), run the detector
    on each ticker that was in the S&P 500 on that date.
  • Point-in-time: only OHLCV up to the replay date is used as the
    detector's lookback window — no look-ahead.
  • Emits BOTH detected and non-detected signals so the backtester has a
    control group.

For a 5-year window with 5-day stride and ~500 tickers per date, this is
~180 dates x ~500 tickers = ~90K detector runs. Fast (~= 2 minutes in
single-threaded mode; ~20 seconds with ThreadPoolExecutor).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from vcp.trend import TrendConfig

from vcp.backtest import SignalRecord
from vcp.cache import (
    load_cached,
    load_sp500_constituents,
)
from vcp.engine.vcp_detector import VC, VCPConfig, analyze_vcp

logger = logging.getLogger("vcp.replay")


def _trading_days(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Return all trading days (Mon-Fri) between start and end (inclusive).

    The S&P 500 constituents dataset has occasional weekend dates (it
    tracks changes, not trading sessions), so we filter Mon-Fri explicitly.
    """
    series = load_sp500_constituents()
    mask = (series.index >= start) & (series.index <= end)
    days = list(series.index[mask])
    return [d for d in days if d.weekday() < 5]


def replay_dates(
    start: pd.Timestamp,
    end: pd.Timestamp,
    stride_days: int = 5,
) -> list[pd.Timestamp]:
    """Pick a periodic schedule of replay dates between start and end."""
    days = _trading_days(start, end)
    if not days:
        return []
    return days[::stride_days]


def _analyze_one(
    ticker: str,
    as_of: pd.Timestamp,
    config: VCPConfig = VC,
    trend_config: TrendConfig | None = None,
) -> SignalRecord | None:
    """Run VCP analysis on cached OHLCV for ticker, treating as_of as 'today'.

    If ``trend_config`` is provided (and enabled), the trend-template gate is
    applied as a *pre-filter*: stocks that fail the trend template are
    recorded as `is_signal=False` with a `trend_blocked=True` marker in
    metadata. This lets the replay emit rows for both VCP-detected names
    and trend-template rejects, so downstream analysis can compare the two
    cohorts.

    For stocks that pass the trend template but fail VCP, the resulting
    `is_signal` is False with `trend_passed=True, vcp_passed=False`.
    """
    from vcp.trend import evaluate as evaluate_trend
    hist_full = load_cached(ticker)
    if hist_full is None or len(hist_full) < 60:
        return None
    # Point-in-time: only use rows with index <= as_of
    hist = hist_full[hist_full.index <= as_of]
    if len(hist) < 60:
        return None
    # Trend-template gate (optional)
    trend_meta: dict = {}
    if trend_config is not None and trend_config.enabled:
        tr = evaluate_trend(ticker, as_of, config=trend_config)
        if tr is None:
            return None  # insufficient history for trend template
        trend_meta = {
            "trend_passed": tr.passes,
            "trend_failures": tr.failures,
            "trend_rs_avg": tr.rs_avg,
            "trend_sma_200_slope": tr.sma_trend_slope,
            "trend_pct_from_high": tr.pct_from_52w_high,
        }
        if not tr.passes:
            # Emit a non-signal row tagged with trend_blocked=True
            return SignalRecord(
                ticker=ticker, signal_date=as_of, entry_price=float(hist["Close"].iloc[-1]),
                score=0.0, is_signal=False,
                metadata={"trend_blocked": True, "trend_failures": tr.failures,
                          "trend_rs_avg": tr.rs_avg, **trend_meta},
            )
    # The detector's `current_price` should be the close on as_of (or the
    # last available close on/before as_of).
    try:
        current_price = float(hist["Close"].iloc[-1])
    except Exception:
        return None
    result = analyze_vcp(ticker, hist, current_price, config=config)
    if result is None:
        return None
    d = result.to_dict()
    if trend_meta:
        d.update(trend_meta)
        d["trend_blocked"] = False
    return SignalRecord(
        ticker=ticker,
        signal_date=as_of,
        entry_price=current_price,
        score=float(d.get("vcp_quality", 0.0)) * 100.0,
        is_signal=bool(d.get("vcp_detected", False)),
        metadata=d,
    )


def replay(
    start: pd.Timestamp,
    end: pd.Timestamp,
    stride_days: int = 5,
    config: VCPConfig = VC,
    workers: int = 8,
    progress: bool = True,
    universe_filter: Callable[[pd.Timestamp, frozenset], frozenset] | None = None,
    trend_config: TrendConfig | None = None,
) -> list[SignalRecord]:
    """Run a full historical replay.

    Args:
        start: first replay date (inclusive).
        end: last replay date (inclusive).
        stride_days: re-analyze every N trading days (1 = daily).
        config: VCPConfig override.
        workers: parallel workers.
        progress: log progress every 5 dates.
        universe_filter: optional ``(as_of, universe) -> universe`` callable
            (e.g. to drop illiquid names).

    Returns:
        List of SignalRecord rows (one per (date, ticker) analysed). Both
        detected and non-detected rows are included.
    """
    dates = replay_dates(start, end, stride_days)
    if not dates:
        logger.warning("No replay dates in window")
        return []

    # Pre-compute universe per date. We load the constituents series ONCE
    # and slice for each date — get_universe_on() reads parquet on every
    # call, which is prohibitively slow when called thousands of times.
    constituents = load_sp500_constituents()
    if start < constituents.index.min():
        logger.warning(f"start {start.date()} is before constituents dataset starts "
                       f"({constituents.index.min().date()}); clipping")
    universes: dict[pd.Timestamp, frozenset] = {}
    for d in dates:
        prior_idx = constituents.index[constituents.index <= d]
        if len(prior_idx) == 0:
            universes[d] = frozenset()
        else:
            universes[d] = constituents.loc[prior_idx.max()]

    # Build the full (date, ticker) job list, then run in parallel.
    jobs: list[tuple[pd.Timestamp, str]] = []
    for d in dates:
        u = universes[d]
        if universe_filter is not None:
            u = universe_filter(d, u)
        for t in u:
            jobs.append((d, t))

    logger.info(
        f"Replay {start.date()} → {end.date()} ({len(dates)} dates, stride {stride_days}): "
        f"{len(jobs)} (date, ticker) jobs"
        f"{', trend gate ON' if trend_config is not None and trend_config.enabled else ''}"
    )

    signals: list[SignalRecord] = []
    n = len(jobs)
    t0 = datetime.now()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_analyze_one, t, d, config, trend_config): (d, t) for d, t in jobs}
        for done, f in enumerate(as_completed(futures), 1):
            d, t = futures[f]
            if progress and (done % 1000 == 0 or done == n):
                elapsed = (datetime.now() - t0).total_seconds()
                sigs = sum(1 for s in signals if s.is_signal)
                blocked = sum(1 for s in signals
                              if s.metadata.get("trend_blocked"))
                logger.info(
                    f"Replay: {done}/{n} ({done / max(elapsed, 0.001):.0f}/s, "
                    f"{sigs} signals, {blocked} trend-blocked)"
                )
            try:
                r = f.result()
                if r is not None:
                    signals.append(r)
            except Exception as e:
                logger.debug(f"replay {d.date()} {t} failed: {e}")

    elapsed = (datetime.now() - t0).total_seconds()
    detected = sum(1 for s in signals if s.is_signal)
    blocked = sum(1 for s in signals if s.metadata.get("trend_blocked"))
    logger.info(
        f"Replay complete: {len(signals)} ticker-dates in {elapsed:.1f}s "
        f"({detected} VCP-detected = {detected / max(len(signals), 1) * 100:.2f}%, "
        f"{blocked} trend-blocked = {blocked / max(len(signals), 1) * 100:.2f}%)"
    )
    return signals


def to_backtest_inputs(
    signals: list[SignalRecord],
    cache_root: str | None = None,
) -> list[SignalRecord]:
    """Convert replay outputs into SignalRecord objects usable by the backtester.

    The replay function already produces SignalRecord rows; this helper exists
    so future replayers (e.g. multi-strategy) can share a single conversion
    path. The CsvPriceSource should be used with the same cache_root that
    contains the OHLCV files.
    """
    return list(signals)
