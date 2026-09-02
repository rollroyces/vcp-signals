#!/usr/bin/env python3
"""
Strategy-agnostic backtest harness for VCP Signals.

Given a set of (signal_date, ticker, ...) rows and a price source, compute:

  • Per-signal forward returns at configurable horizons (default: 5/10/20/60 trading days)
  • Hit-rate (fraction of signals with positive return at each horizon)
  • Mean / median / std of returns, grouped by detector output (e.g. vcp_detected vs not)
  • Equal-weight simulated portfolio: enter at signal price, exit at horizon or stop, with
    stop-loss support
  • Drawdown, win/loss ratio, expectancy

Designed to be reused: any future signal type (RS/RW, Stage 2 template, …) just plugs in
a callable that turns (ticker, signal_date) into a SignalRecord.

Quick start
-----------
    from vcp.backtest import Backtester, SignalRecord, YahooPriceSource

    bt = Backtester(YahooPriceSource(), horizons=[5, 10, 20, 60])
    results = bt.run(signals, progress=True)
    results.summary()
"""
from __future__ import annotations

import logging
import statistics
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd

logger = logging.getLogger("vcp.backtest")

# ──────────────────────────────────────────────────────────────────────────────
# Data Model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SignalRecord:
    """One row in the input signal ledger.

    `score` is opaque to the backtester — it's whatever the detector produced
    (vcp_quality, composite score, etc). Group-by-score slicing lives in the
    analyzer, not here.
    """

    ticker: str
    signal_date: pd.Timestamp
    entry_price: float
    score: float = 0.0
    is_signal: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass
class SignalOutcome:
    """Forward-return measurements for one signal."""

    ticker: str
    signal_date: pd.Timestamp
    entry_price: float
    score: float
    is_signal: bool
    metadata: dict
    # horizon_days → return_pct (None if not enough history)
    returns: dict[int, float | None]
    # horizon_days → exit_price (None if not enough history)
    exit_prices: dict[int, float | None]
    # horizon_days → minimum close in [entry, entry+horizon] for stop-loss checks
    min_in_window: dict[int, float | None] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Aggregated backtest results."""

    outcomes: list[SignalOutcome]
    horizons: Sequence[int]
    n_signals: int

    def _grouped_returns(self, key: str, horizon: int) -> list[float]:
        out = []
        for o in self.outcomes:
            r = o.returns.get(horizon)
            if r is None:
                continue
            if key == "all" or (key == "signals" and o.is_signal) or (key == "non_signals" and not o.is_signal):
                out.append(r)
            elif key.startswith("score>="):
                thr = float(key.split(">=")[1])
                if o.score >= thr:
                    out.append(r)
            elif key.startswith("score<"):
                thr = float(key.split("<")[1])
                if o.score < thr:
                    out.append(r)
        return out

    def _grouped_rows(
        self, key: str, horizon: int,
    ) -> list[tuple[float, float, SignalOutcome]]:
        """Return (entry_price, horizon_return_fraction, outcome) tuples
        for outcomes matching ``key``. The horizon return is the simple
        (exit/entry - 1); callers apply stops via min_in_window as needed.
        """
        out: list[tuple[float, float, SignalOutcome]] = []
        for o in self.outcomes:
            if o.entry_price is None or o.entry_price <= 0:
                continue
            r = o.returns.get(horizon)
            if r is None:
                continue
            in_group = (
                key == "all"
                or (key == "signals" and o.is_signal)
                or (key == "non_signals" and not o.is_signal)
            )
            if not in_group:
                continue
            out.append((float(o.entry_price), r / 100.0, o))
        return out

    @staticmethod
    def _stats(rs: list[float]) -> dict[str, float]:
        if not rs:
            return {"n": 0, "mean": 0.0, "median": 0.0, "stdev": 0.0,
                    "hit_rate": 0.0, "min": 0.0, "max": 0.0, "total": 0.0}
        wins = sum(1 for r in rs if r > 0)
        return {
            "n": len(rs),
            "mean": statistics.fmean(rs),
            "median": statistics.median(rs),
            "stdev": statistics.stdev(rs) if len(rs) > 1 else 0.0,
            "hit_rate": wins / len(rs),
            "min": min(rs),
            "max": max(rs),
            "total": sum(rs),
        }

    def summary(self, groups: list[str] | None = None) -> pd.DataFrame:
        """Return a stats DataFrame: rows = (group, horizon), cols = n/mean/median/stdev/hit_rate."""
        if groups is None:
            groups = ["all", "signals", "non_signals"]
        rows = []
        for g in groups:
            for h in self.horizons:
                rs = self._grouped_returns(g, h)
                s = self._stats(rs)
                s["group"] = g  # type: ignore[assignment]
                s["horizon_days"] = h
                rows.append(s)
        df = pd.DataFrame(rows)
        cols = ["group", "horizon_days", "n", "mean", "median", "stdev",
                "hit_rate", "min", "max", "total"]
        return df[cols]

    def simulated_portfolio(
        self,
        horizon: int = 20,
        stop_loss_pct: float | None = None,
        group: str = "signals",
    ) -> dict[str, float]:
        """Equal-weight portfolio return on a per-signal basis.

        Stop-loss semantics:
          * If ``stop_loss_pct`` is None → use the raw horizon return.
          * Otherwise → take the WORSE of (a) the horizon return and
            (b) the stop-loss level. We approximate (b) by checking
            whether ``min_in_window`` is at or below the stop level; if
            so, we cap the per-trade return at ``-stop_loss_pct``. This
            is realistic for a strategy that exits on an intraday/close
            stop but otherwise holds to the horizon.

        Caveat: this is *not* a true time-series simulation — it does not
        move cash from stopped trades into subsequent signals. For a
        real portfolio with N concurrent positions, you'd need a
        position-scheduler on top of this. This function answers the
        simpler question: "what's the per-signal expected return if I
        follow every signal with a stop?"
        """
        rows = self._grouped_rows(group, horizon)
        if not rows:
            return {"n": 0, "mean_pct": 0.0, "total_return_pct": 0.0,
                    "max_drawdown_pct": 0.0, "win_rate": 0.0}
        # Apply stop-loss at the per-trade level: if the worst close in
        # [entry, entry+horizon] is <= (1 - stop) * entry, the trade would
        # have been stopped out and the realized return is -stop.
        adjusted_rets: list[float] = []
        for entry, horizon_ret_frac, outcome in rows:
            r = horizon_ret_frac
            if stop_loss_pct is not None:
                min_close = outcome.min_in_window.get(horizon)
                if min_close is not None and entry > 0:
                    worst_frac = float(min_close) / entry - 1.0
                    if worst_frac <= -abs(stop_loss_pct):
                        r = -abs(stop_loss_pct)
            adjusted_rets.append(r)
        # Geometric compounding
        eq = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in adjusted_rets:
            eq *= (1.0 + r)
            peak = max(peak, eq)
            dd = (eq - peak) / peak
            max_dd = min(max_dd, dd)
        return {
            "n": len(adjusted_rets),
            "mean_pct": statistics.fmean(adjusted_rets) * 100.0,
            "total_return_pct": (eq - 1.0) * 100.0,
            "max_drawdown_pct": max_dd * 100.0,
            "win_rate": sum(1 for r in adjusted_rets if r > 0) / len(adjusted_rets),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Price Sources
# ──────────────────────────────────────────────────────────────────────────────


class PriceSource:
    """Abstract: return a price series for a ticker covering [start, end]."""

    def get(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
        raise NotImplementedError


class YahooPriceSource(PriceSource):
    """Live yfinance source. Add buffer days before/after to handle holidays."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self._yf = None

    def _client(self):
        if self._yf is None:
            import yfinance as yf
            self._yf = yf
        return self._yf

    def get(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
        try:
            yf = self._client()
            # Buffer: yfinance `start`/`end` are inclusive of start, exclusive of end
            buf_start = start - timedelta(days=10)
            buf_end = end + timedelta(days=10)
            t = yf.Ticker(ticker)
            hist = t.history(start=buf_start.strftime("%Y-%m-%d"),
                             end=buf_end.strftime("%Y-%m-%d"),
                             auto_adjust=False)
            if hist is None or len(hist) < 5:
                return None
            if self.delay > 0:
                import time
                time.sleep(self.delay)
            return hist
        except Exception as e:
            logger.debug(f"{ticker}: price fetch failed: {e}")
            return None


class CsvPriceSource(PriceSource):
    """File-based source for offline backtests. Expected CSV: Date, Open, High, Low, Close, Volume."""

    def __init__(self, root_dir: str, date_col: str = "Date"):
        self.root_dir = root_dir
        self.date_col = date_col

    def get(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
        import os
        candidates = [
            f"{self.root_dir}/{ticker}.csv",
            f"{self.root_dir}/{ticker.upper()}.csv",
            f"{self.root_dir}/{ticker.lower()}.csv",
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    df = pd.read_csv(path, parse_dates=[self.date_col], index_col=self.date_col)
                    df = df.sort_index()
                    return df.loc[start - timedelta(days=10): end + timedelta(days=10)]
                except Exception as e:
                    logger.debug(f"{ticker}: csv read failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Backtester
# ──────────────────────────────────────────────────────────────────────────────


class Backtester:
    """Compute forward returns for each signal using a price source."""

    TRADING_DAYS_PER_YEAR = 252

    def __init__(
        self,
        price_source: PriceSource,
        horizons: Sequence[int] = (5, 10, 20, 60),
        max_workers: int = 8,
    ):
        self.price_source = price_source
        self.horizons = list(horizons)
        self.max_workers = max_workers

    def _outcome_for(self, signal: SignalRecord) -> SignalOutcome:
        # Add calendar buffer: max horizon + holidays + weekends
        max_horizon = max(self.horizons)
        end = signal.signal_date + timedelta(days=int(max_horizon * 1.6) + 30)
        hist = self.price_source.get(signal.ticker, signal.signal_date, end)
        returns: dict[int, float | None] = {}
        exit_prices: dict[int, float | None] = {}
        min_in_window: dict[int, float | None] = {}
        if hist is None or len(hist) < 5:
            for h in self.horizons:
                returns[h] = None
                exit_prices[h] = None
                min_in_window[h] = None
            return SignalOutcome(
                ticker=signal.ticker,
                signal_date=signal.signal_date,
                entry_price=signal.entry_price,
                score=signal.score,
                is_signal=signal.is_signal,
                metadata=signal.metadata,
                returns=returns,
                exit_prices=exit_prices,
                min_in_window=min_in_window,
            )
        # Build a calendar-indexed series of closes
        closes = hist["Close"].copy()
        # Trading-day offset: take the Nth row after the signal_date
        # yfinance may return rows in either order; ensure ascending
        if not closes.index.is_monotonic_increasing:
            closes = closes.sort_index()
        # Normalize timezones: yfinance returns tz-aware (America/New_York);
        # signal_date is tz-naive (UTC). Strip tz from index so everything
        # downstream compares cleanly.
        if getattr(closes.index, "tz", None) is not None:
            closes.index = closes.index.tz_localize(None)
        signal_date = signal.signal_date
        if getattr(signal_date, "tz", None) is not None:
            signal_date = signal_date.tz_localize(None)
        # Find the first close on or after signal_date (the "entry bar")
        valid = closes[closes.index >= signal_date]
        if valid.empty:
            entry_close = float(closes.iloc[-1])
            entry_idx = len(closes) - 1
        else:
            entry_close = float(valid.iloc[0])
            entry_idx = closes.index.get_loc(valid.index[0])
        for h in self.horizons:
            target_idx = entry_idx + h
            if target_idx >= len(closes):
                returns[h] = None
                exit_prices[h] = None
                min_in_window[h] = None
            else:
                exit_close = float(closes.iloc[target_idx])
                exit_prices[h] = exit_close
                if entry_close > 0:
                    returns[h] = (exit_close / entry_close - 1.0) * 100.0
                else:
                    returns[h] = None
                # Intra-window minimum for stop-loss checks
                window = closes.iloc[entry_idx:target_idx + 1]
                min_in_window[h] = float(window.min()) if len(window) else None
        return SignalOutcome(
            ticker=signal.ticker,
            signal_date=signal.signal_date,
            entry_price=entry_close,
            score=signal.score,
            is_signal=signal.is_signal,
            metadata=signal.metadata,
            returns=returns,
            exit_prices=exit_prices,
            min_in_window=min_in_window,
        )

    def run(
        self,
        signals: Iterable[SignalRecord],
        progress: bool = False,
    ) -> BacktestResult:
        sig_list = list(signals)
        n = len(sig_list)
        outcomes: list[SignalOutcome] = []
        if self.max_workers <= 1:
            for i, s in enumerate(sig_list, 1):
                if progress and (i % 50 == 0 or i == n):
                    logger.info(f"Backtest: {i}/{n}")
                outcomes.append(self._outcome_for(s))
        else:
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futures = {ex.submit(self._outcome_for, s): s for s in sig_list}
                for done, f in enumerate(as_completed(futures), 1):
                    if progress and (done % 50 == 0 or done == n):
                        logger.info(f"Backtest: {done}/{n}")
                    try:
                        outcomes.append(f.result())
                    except Exception as e:
                        logger.debug(f"outcome failed: {e}")
        return BacktestResult(outcomes=outcomes, horizons=self.horizons, n_signals=n)


# ──────────────────────────────────────────────────────────────────────────────
# Loaders — turn JSON scan outputs into SignalRecord lists
# ──────────────────────────────────────────────────────────────────────────────


def load_signals_from_json(path: str, signal_date_field: str | None = None) -> list[SignalRecord]:
    """Read signals from a VCP scan JSON file.

    Supports two on-disk schemas:

      A) `{"signals": [{"ticker","price","score","is_signal",...}, ...], ...}`
         (the legacy schema)
      B) `{"all_signals": [{"ticker","pivot_price","vcp_quality","vcp_detected",...}, ...], ...}`
         (the current run_scan.py schema)

    If `signal_date_field` is None, we default to the file's `timestamp` key,
    falling back to today.
    """
    import json
    with open(path) as f:
        d = json.load(f)
    # Resolve signal date
    if signal_date_field and signal_date_field in d:
        dt = pd.Timestamp(d[signal_date_field])
    elif "timestamp" in d:
        ts = d["timestamp"]
        # Normalize "2026-05-20T09:30Z" → pd.Timestamp; may be tz-naive or aware.
        if isinstance(ts, str):
            ts = ts.replace("Z", "+00:00")
        dt = pd.Timestamp(ts)
        if getattr(dt, "tz", None) is not None:
            dt = dt.tz_convert(None)
    else:
        dt = pd.Timestamp.today().normalize()
    # Resolve signals list
    raw = d.get("signals") or d.get("all_signals") or d.get("top_signals") or []
    out: list[SignalRecord] = []
    for r in raw:
        ticker = r.get("ticker")
        if not ticker:
            continue
        # Schema A: price, score
        # Schema B: pivot_price, vcp_quality, vcp_detected
        entry = r.get("price") or r.get("pivot_price") or 0.0
        score = r.get("score")
        if score is None:
            # Use vcp_quality as score (0-1), scale to 0-100 for consistency
            score = float(r.get("vcp_quality", 0.0)) * 100.0
        is_signal = bool(r.get("is_signal", r.get("vcp_detected", True)))
        out.append(SignalRecord(
            ticker=ticker,
            signal_date=dt,
            entry_price=float(entry),
            score=float(score),
            is_signal=is_signal,
            metadata=r,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="VCP backtester — validate scan results vs forward returns")
    parser.add_argument("--signals", required=True, help="Path to scan JSON")
    parser.add_argument("--horizons", default="5,10,20,60", help="Comma-separated horizons (trading days)")
    parser.add_argument("--out", default="", help="Optional output CSV path for the summary table")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stop-loss", type=float, default=None, help="e.g. 0.10 = 10%% downside cap")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    signals = load_signals_from_json(args.signals)
    logger.info(f"Loaded {len(signals)} signals from {args.signals}")
    if not signals:
        logger.error("No signals to backtest")
        return

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    bt = Backtester(YahooPriceSource(), horizons=horizons, max_workers=args.workers)
    result = bt.run(signals, progress=True)
    summary = result.summary()
    print("\n=== Forward-return summary ===")
    print(summary.to_string(index=False))

    if args.stop_loss is not None:
        print(f"\n=== Simulated portfolio (stop_loss={args.stop_loss:.0%}) ===")
        for h in horizons:
            port = result.simulated_portfolio(horizon=h, stop_loss_pct=args.stop_loss)
            print(f"  {h:>3}d: {port}")

    if args.out:
        summary.to_csv(args.out, index=False)
        logger.info(f"Summary saved to {args.out}")


if __name__ == "__main__":
    _main()
