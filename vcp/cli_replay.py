#!/usr/bin/env python3
"""
CLI: historical VCP replay → forward-return backtest.

Runs the detector on cached OHLCV at periodic replay dates, then feeds the
resulting SignalRecord list to the backtester for forward-return measurement.

Examples:
    # 5-year replay, 5-day stride, default thresholds
    python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 --stride 5

    # Daily stride (slow but maximum data)
    python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 --stride 1

    # Tighter thresholds, only test if changes help
    python3 -m vcp.cli_replay --start 2024-01-01 --end 2026-09-01 \
        --min-contractions 3 --quality-threshold 0.65
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from vcp.backtest import (
    Backtester,
    CsvPriceSource,
)
from vcp.cache import cache_dir
from vcp.engine.vcp_detector import VC, VCPConfig
from vcp.replay import replay


def _apply_config_overrides(args: argparse.Namespace) -> VCPConfig:
    """Build a VCPConfig with CLI overrides applied to the singleton defaults."""
    base = VC
    overrides = {}
    if args.min_contractions is not None:
        overrides["min_contractions"] = args.min_contractions
    if args.quality_threshold is not None:
        overrides["vcp_quality_threshold"] = args.quality_threshold
    if args.max_pivot_atr_pct is not None:
        overrides["max_pivot_atr_pct"] = args.max_pivot_atr_pct
    if args.ideal_pivot_atr_pct is not None:
        overrides["ideal_pivot_atr_pct"] = args.ideal_pivot_atr_pct
    if args.volume_dry_up_threshold is not None:
        overrides["volume_dry_up_threshold"] = args.volume_dry_up_threshold
    if args.contraction_ratio_threshold is not None:
        overrides["contraction_ratio_threshold"] = args.contraction_ratio_threshold
    if not overrides:
        return base
    # VCPConfig is frozen; build a new one with overrides
    from dataclasses import replace
    return replace(base, **overrides)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("vcp.cli_replay")

    parser = argparse.ArgumentParser(description="Historical VCP replay + backtest")
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument("--end", type=str, default="2026-09-01")
    parser.add_argument("--stride", type=int, default=5, help="Replay every N trading days")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--horizons", type=str, default="5,10,20,60")
    parser.add_argument("--stop-loss", type=float, default=0.10)
    parser.add_argument("--out-prefix", type=str, default="output/replay",
                        help="Output prefix; will write <prefix>_signals.json and <prefix>_summary.csv")
    parser.add_argument("--min-contractions", type=int, default=None)
    parser.add_argument("--quality-threshold", type=float, default=None)
    parser.add_argument("--max-pivot-atr-pct", type=float, default=None)
    parser.add_argument("--ideal-pivot-atr-pct", type=float, default=None)
    parser.add_argument("--volume-dry-up-threshold", type=float, default=None)
    parser.add_argument("--contraction-ratio-threshold", type=float, default=None)
    args = parser.parse_args()

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    config = _apply_config_overrides(args)
    logger.info(f"Using VCPConfig: min_contractions={config.min_contractions}, "
                f"quality_threshold={config.vcp_quality_threshold}, "
                f"max_pivot_atr={config.max_pivot_atr_pct}, "
                f"contraction_ratio={config.contraction_ratio_threshold}")

    # 1. Replay: generate signal list
    signals = replay(start, end, stride_days=args.stride,
                     config=config, workers=args.workers, progress=True)
    if not signals:
        logger.error("No signals from replay")
        return 1

    Path(args.out_prefix).parent.mkdir(parents=True, exist_ok=True)

    # 2. Save raw signals for reproducibility / ad-hoc analysis
    sig_path = f"{args.out_prefix}_signals.json"
    with open(sig_path, "w") as f:
        json.dump([asdict(s) | {"signal_date": str(s.signal_date)}
                   for s in signals], f, default=str, indent=2)
    logger.info(f"Signals saved to {sig_path}")

    # 3. Backtest: feed signals into the forward-return harness, using the
    #    same cache directory as the price source (zero network calls).
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    bt = Backtester(CsvPriceSource(cache_dir()), horizons=horizons,
                    max_workers=args.workers)
    result = bt.run(signals, progress=True)

    # 4. Summary
    summary = result.summary()
    print("\n=== Forward-return summary (all replay dates) ===")
    print(summary.to_string(index=False))

    print(f"\n=== Simulated portfolio (stop_loss={args.stop_loss:.0%}) ===")
    for h in horizons:
        port = result.simulated_portfolio(horizon=h, stop_loss_pct=args.stop_loss)
        print(f"  {h:>3}d: {port}")

    # 5. By-year breakdown
    by_year = _breakdown_by_year(result.outcomes, horizons)
    print("\n=== By year ===")
    print(by_year.to_string(index=False))

    # 6. By-quality bucket
    by_quality = _breakdown_by_quality(result.outcomes, horizons)
    print("\n=== By quality bucket ===")
    print(by_quality.to_string(index=False))

    # 7. Save
    summary.to_csv(f"{args.out_prefix}_summary.csv", index=False)
    by_year.to_csv(f"{args.out_prefix}_by_year.csv", index=False)
    by_quality.to_csv(f"{args.out_prefix}_by_quality.csv", index=False)
    logger.info(f"Reports saved to {args.out_prefix}_*.csv")

    return 0


def _breakdown_by_year(outcomes, horizons) -> pd.DataFrame:
    import pandas as pd
    rows = []
    for o in outcomes:
        year = pd.Timestamp(o.signal_date).year
        for h in horizons:
            r = o.returns.get(h)
            if r is None:
                continue
            rows.append({"year": year, "horizon": h, "is_signal": o.is_signal, "return": r})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    grouped = df.groupby(["year", "horizon", "is_signal"]).agg(
        n=("return", "count"),
        mean=("return", "mean"),
        median=("return", "median"),
        hit_rate=("return", lambda x: (x > 0).mean()),
    ).reset_index()
    return grouped


def _breakdown_by_quality(outcomes, horizons) -> pd.DataFrame:
    """Bucket by quality score: 0 (non-signal), 0-50, 50-65, 65-80, 80+."""
    import pandas as pd
    def bucket(score: float, is_signal: bool) -> str:
        if not is_signal:
            return "00_non_signal"
        if score >= 80:
            return "D_80+"
        if score >= 65:
            return "C_65-80"
        if score >= 50:
            return "B_50-65"
        return "A_below_50"

    rows = []
    for o in outcomes:
        for h in horizons:
            r = o.returns.get(h)
            if r is None:
                continue
            rows.append({
                "bucket": bucket(o.score, o.is_signal),
                "horizon": h,
                "return": r,
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    grouped = df.groupby(["bucket", "horizon"]).agg(
        n=("return", "count"),
        mean=("return", "mean"),
        median=("return", "median"),
        hit_rate=("return", lambda x: (x > 0).mean()),
    ).reset_index()
    return grouped


if __name__ == "__main__":
    sys.exit(main())
