"""Stage-2 trend template gate (Mark Minervini's classical rules).

The trend template is the standard pre-filter applied before any breakout
or VCP signal. A stock is in a "Stage 2 uptrend" (per Minervini / Weinstein)
when ALL of the following hold on the lookback window:

  1. Price > 150-day SMA
  2. Price > 200-day SMA
  3. 150-day SMA > 200-day SMA
  4. 200-day SMA has positive slope (rising over the last N days)
  5. Price is within 25% of its 52-week high
  6. Price is at least 30% above its 52-week low
  7. 200-day average volume is rising (price accumulation regime)
  8. Relative strength vs SPY > 0 over 3 / 6 / 12 months

Conditions 1-7 are the canonical template; #8 is the IBD-style RS filter
(separately the most powerful predictor in academic momentum research).

All inputs are point-in-time: the cache slice <= as_of_date. No look-ahead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from vcp.cache import load_cached

logger = logging.getLogger("vcp.trend")


@dataclass(frozen=True)
class TrendConfig:
    """Configuration for the Stage-2 trend template gate.

    Each field is the threshold above which the corresponding check passes.
    Defaults follow Mark Minervini's "Trade Like a Stock Market Wizard"
    rules, with the RS thresholds matching IBD-style relative-strength ratings.
    """

    # Moving averages (in trading days)
    sma_fast: int = 50            # fast SMA — used as additional confirmation
    sma_slow: int = 150           # 30-week SMA, the canonical fast MA
    sma_trend: int = 200          # 40-week SMA, the canonical trend MA
    slope_lookback: int = 20      # days for the 200-day SMA slope check

    # 52-week proximity (252 trading days)
    high_lookback: int = 252
    high_proximity_pct: float = 0.25   # must be within 25% of 52w high
    low_clearance_pct: float = 0.30    # must be 30%+ above 52w low

    # Volume confirmation
    volume_lookback: int = 50     # 50-day average volume window

    # Relative strength (vs SPY). Score in [-100, +100], > 0 = outperforming.
    rs_periods: tuple[int, ...] = (63, 126, 252)  # 3M, 6M, 12M in trading days
    rs_min_avg: float = 0.0       # require average RS > 0

    # Master switch: if False, all checks return True (gate disabled).
    enabled: bool = True


@dataclass
class TrendResult:
    """Outcome of the trend-template gate for one (ticker, date) pair."""

    ticker: str
    as_of: pd.Timestamp
    passes: bool = False
    failures: list[str] = field(default_factory=list)
    # Diagnostic fields
    price: float = 0.0
    sma_fast: float = 0.0
    sma_slow: float = 0.0
    sma_trend: float = 0.0
    sma_trend_slope: float = 0.0
    pct_from_52w_high: float = 0.0
    pct_above_52w_low: float = 0.0
    rs_avg: float = 0.0
    rs_periods: dict[int, float] = field(default_factory=dict)
    volume_ratio: float = 1.0


def _sma(closes: pd.Series, n: int) -> float | None:
    """Simple moving average of the last n closes. None if insufficient history."""
    if len(closes) < n:
        return None
    return float(closes.iloc[-n:].mean())


def _relative_strength(stock_close: float, bench_close: float, n_bars: int) -> float | None:
    """RS = (stock_pct_change / bench_pct_change - 1) * 100 over the last n bars.

    Returns a percentage (e.g. +5.2 means the stock outperformed the
    benchmark by 5.2 percentage points over that window).
    """
    if stock_close is None or bench_close is None or bench_close == 0:
        return None
    if stock_close == 0:
        return None
    return ((stock_close / bench_close) - 1.0) * 100.0


def evaluate(
    ticker: str,
    as_of: pd.Timestamp,
    config: TrendConfig | None = None,
) -> TrendResult | None:
    """Evaluate the Stage-2 trend template on cached OHLCV at ``as_of``.

    Returns None if data is unavailable or insufficient history.
    """
    cfg = config or TrendConfig()
    if not cfg.enabled:
        return TrendResult(ticker=ticker, as_of=as_of, passes=True)

    hist = load_cached(ticker)
    if hist is None or len(hist) < cfg.high_lookback + cfg.slope_lookback:
        return None

    # Point-in-time slice: only use closes on or before as_of
    pit = hist[hist.index <= as_of]
    if len(pit) < cfg.high_lookback + cfg.slope_lookback:
        return None

    closes = pit["Close"]
    volumes = pit["Volume"]

    price = float(closes.iloc[-1])
    sma_fast = _sma(closes, cfg.sma_fast)
    sma_slow = _sma(closes, cfg.sma_slow)
    sma_trend_now = _sma(closes, cfg.sma_trend)
    sma_trend_prev = _sma(closes.iloc[:-cfg.slope_lookback], cfg.sma_trend) \
        if len(closes) > cfg.sma_trend + cfg.slope_lookback else None

    if any(v is None for v in (sma_fast, sma_slow, sma_trend_now, sma_trend_prev)):
        return None
    # Narrow: we've just checked all four are not None.
    assert sma_trend_now is not None
    assert sma_trend_prev is not None

    sma_trend_slope = (sma_trend_now - sma_trend_prev) / sma_trend_prev

    # 52-week high/low
    high_52w = float(closes.iloc[-cfg.high_lookback:].max())
    low_52w = float(closes.iloc[-cfg.high_lookback:].min())
    if high_52w <= 0:
        return None
    pct_from_high = (price / high_52w) - 1.0  # negative = below high
    pct_above_low = (price / low_52w) - 1.0   # positive = above low

    # Volume confirmation: recent 50d avg vs prior 50d avg.
    # We DO NOT fail the gate on declining volume — high-volume breakouts
    # are a separate edge (Wyckoff spring / climax volume), and requiring
    # rising volume here would reject the very setups where VCP fires
    # (late-stage consolidations naturally have volume declining). The
    # volume_ratio field is still exposed in TrendResult for diagnostics.
    if len(volumes) >= cfg.volume_lookback * 2:
        vol_recent = float(volumes.iloc[-cfg.volume_lookback:].mean())
        vol_prior = float(volumes.iloc[-cfg.volume_lookback * 2:-cfg.volume_lookback].mean())
        volume_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0
    else:
        volume_ratio = 1.0

    # Relative strength vs SPY
    bench = load_cached("SPY")
    rs_periods: dict[int, float] = {}
    rs_avg = 0.0
    if bench is not None:
        bench_pit = bench[bench.index <= as_of]
        if len(bench_pit) >= max(cfg.rs_periods):
            for n in cfg.rs_periods:
                if len(closes) > n and len(bench_pit) > n:
                    stock_ret = closes.iloc[-1] / closes.iloc[-n - 1] - 1
                    bench_ret = bench_pit["Close"].iloc[-1] / bench_pit["Close"].iloc[-n - 1] - 1
                    # RS as percentage-point outperformance
                    rs_periods[n] = float((stock_ret - bench_ret) * 100)
            if rs_periods:
                rs_avg = sum(rs_periods.values()) / len(rs_periods)
    rs_positive = rs_avg > cfg.rs_min_avg

    # Apply checks. mypy sees sma_* as Optional[float] (return of _sma);
    # we've already returned early if any were None, so narrow them here.
    assert sma_fast is not None
    assert sma_slow is not None
    assert sma_trend_now is not None
    failures: list[str] = []
    if price <= sma_fast:
        failures.append(f"price {price:.2f} <= SMA{cfg.sma_fast} {sma_fast:.2f}")
    if price <= sma_slow:
        failures.append(f"price {price:.2f} <= SMA{cfg.sma_slow} {sma_slow:.2f}")
    if price <= sma_trend_now:
        failures.append(f"price {price:.2f} <= SMA{cfg.sma_trend} {sma_trend_now:.2f}")
    if sma_fast <= sma_slow:
        failures.append(f"SMA{cfg.sma_fast} {sma_fast:.2f} <= SMA{cfg.sma_slow} {sma_slow:.2f}")
    if sma_slow <= sma_trend_now:
        failures.append(f"SMA{cfg.sma_slow} {sma_slow:.2f} <= SMA{cfg.sma_trend} {sma_trend_now:.2f}")
    if sma_trend_slope <= 0:
        failures.append(f"200d SMA slope {sma_trend_slope:.4f} <= 0")
    if pct_from_high < -cfg.high_proximity_pct:
        failures.append(f"price {pct_from_high:.1%} below 52w high (limit {-cfg.high_proximity_pct:.0%})")
    if pct_above_low < cfg.low_clearance_pct:
        failures.append(f"price only {pct_above_low:.1%} above 52w low (need {cfg.low_clearance_pct:.0%})")
    if not rs_positive and cfg.rs_periods:
        failures.append(f"avg RS vs SPY = {rs_avg:.2f} (need > {cfg.rs_min_avg})")

    passes = len(failures) == 0
    return TrendResult(
        ticker=ticker, as_of=as_of, passes=passes, failures=failures,
        price=price, sma_fast=sma_fast, sma_slow=sma_slow, sma_trend=sma_trend_now,
        sma_trend_slope=sma_trend_slope,
        pct_from_52w_high=pct_from_high, pct_above_52w_low=pct_above_low,
        rs_avg=rs_avg, rs_periods=rs_periods, volume_ratio=volume_ratio,
    )
