#!/usr/bin/env python3
"""
CLI: parameter sweep over VCPConfig thresholds against the replay dataset.

Reads an existing replay signals JSON (from ``cli_replay``), re-runs the
backtest under different parameter combinations, and reports which
threshold setting maximizes risk-adjusted forward return.

Examples:
    python3 -m vcp.cli_calibrate --signals output/replay_2021_2026_s5_signals.json
    python3 -m vcp.cli_calibrate --signals output/replay_2021_2026_s5_signals.json \
        --horizons 20 --workers 8 --top 5
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys

import pandas as pd

from vcp.backtest import Backtester, CsvPriceSource, SignalRecord
from vcp.cache import cache_dir
from vcp.engine.vcp_detector import VCPConfig

# Threshold candidates. Sensible ranges given the defaults (min=2, q=0.50,
# atr=0.04, vol=0.50, ratio=0.80). Reduced from the full 4x2x3x3x2=144 grid
# to the 24 most informative combinations — calibrating against the 117K-
# signal replay is expensive enough that we don't want a redundant sweep.
_PARAM_GRID: dict[str, list[float]] = {
    "vcp_quality_threshold": [0.50, 0.60, 0.70, 0.80],
    "min_contractions": [2, 3],
    "max_pivot_atr_pct": [0.03, 0.05],
    "volume_dry_up_threshold": [0.50],
    "contraction_ratio_threshold": [0.80],
}


def _classify(score: float, is_signal: bool, cfg: VCPConfig) -> bool:
    """Mimic the detector's classification logic from the stored metadata.

    We don't re-run the detector — we already have vcp_detected + vcp_quality
    per signal in the metadata. The sweep only varies the threshold values
    that gate the boolean.
    """
    if not is_signal:
        return False
    return score >= cfg.vcp_quality_threshold * 100.0


def _load_signals(path: str) -> list[SignalRecord]:
    with open(path) as f:
        rows = json.load(f)
    out: list[SignalRecord] = []
    for r in rows:
        out.append(SignalRecord(
            ticker=r["ticker"],
            signal_date=pd.Timestamp(r["signal_date"]),
            entry_price=float(r["entry_price"]),
            score=float(r.get("score", 0.0)),
            is_signal=bool(r.get("is_signal", False)),
            metadata=r.get("metadata", {}),
        ))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("vcp.cli_calibrate")

    parser = argparse.ArgumentParser(description="VCPConfig threshold sweep")
    parser.add_argument("--signals", required=True,
                        help="Path to replay signals JSON (output of cli_replay)")
    parser.add_argument("--horizons", default="20")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--stop-loss", type=float, default=0.10)
    parser.add_argument("--top", type=int, default=5,
                        help="Show top-N parameter combinations by excess return")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    logger.info(f"Loading signals from {args.signals}…")
    signals = _load_signals(args.signals)
    logger.info(f"Loaded {len(signals)} signals "
                f"({sum(1 for s in signals if s.is_signal)} detected)")

    # The key optimization: compute forward returns ONCE per (signal, horizon)
    # by running the backtester once. Each calibration combo then just slices
    # those returns — no re-fetch, no re-detect. This turns a 16x slow sweep
    # into a single backtest + cheap post-processing.
    logger.info(f"Computing forward returns once (cache: {cache_dir()})…")
    bt = Backtester(CsvPriceSource(cache_dir()), horizons=horizons, max_workers=args.workers)
    result = bt.run(signals, progress=True)

    # Build a flat table: one row per (signal, horizon), with score + return.
    rows: list[dict] = []
    for o in result.outcomes:
        for h in horizons:
            r = o.returns.get(h)
            if r is None:
                continue
            rows.append({
                "ticker": o.ticker,
                "signal_date": str(o.signal_date),
                "score": o.score,
                "is_signal_orig": o.is_signal,
                "horizon": h,
                "return": r,
            })
    flat = pd.DataFrame(rows)
    logger.info(f"Computed {len(flat)} (signal, horizon) return rows")

    # Now sweep: for each threshold combo, reclassify and aggregate
    keys = list(_PARAM_GRID.keys())
    combos = list(itertools.product(*[_PARAM_GRID[k] for k in keys]))
    logger.info(f"Parameter grid: {len(combos)} combinations x {len(horizons)} horizons")

    results: list[dict] = []
    for i, values in enumerate(combos, 1):
        cfg_dict = dict(zip(keys, values, strict=True))
        # Reclassify under new quality threshold
        q_thr = cfg_dict["vcp_quality_threshold"] * 100.0  # score is on 0-100 scale
        flat2 = flat.copy()
        # A signal is "detected" under the new config iff:
        # - it was originally detected (we only re-rank, not re-detect from scratch)
        # - its score >= new quality threshold
        # - AND the structural gates (min_contractions, max_pivot_atr_pct,
        #   volume_dry_up_threshold, contraction_ratio_threshold) are met.
        # We don't have the raw structural fields in the signals JSON, so we
        # use score as the primary threshold lever and treat the others as
        # informational in the output.
        flat2["is_signal_new"] = flat2["is_signal_orig"] & (flat2["score"] >= q_thr)
        grouped = flat2.groupby(["horizon", "is_signal_new"]).agg(
            n=("return", "count"),
            mean=("return", "mean"),
            median=("return", "median"),
            hit_rate=("return", lambda x: (x > 0).mean()),
        ).reset_index()

        out = {"combo_id": i, **cfg_dict}
        for h in horizons:
            sig_row = grouped[(grouped.horizon == h) & (grouped.is_signal_new)]
            non_row = grouped[(grouped.horizon == h) & (~grouped.is_signal_new)]
            sig_mean = float(sig_row["mean"].iloc[0]) if len(sig_row) else 0.0
            non_mean = float(non_row["mean"].iloc[0]) if len(non_row) else 0.0
            sig_hit = float(sig_row["hit_rate"].iloc[0]) if len(sig_row) else 0.0
            non_hit = float(non_row["hit_rate"].iloc[0]) if len(non_row) else 0.0
            sig_n = int(sig_row["n"].iloc[0]) if len(sig_row) else 0
            out[f"sig_n_h{h}"] = sig_n
            out[f"sig_mean_h{h}"] = sig_mean
            out[f"sig_hit_h{h}"] = sig_hit
            out[f"non_mean_h{h}"] = non_mean
            out[f"non_hit_h{h}"] = non_hit
            out[f"excess_mean_h{h}"] = sig_mean - non_mean
            out[f"excess_hit_h{h}"] = sig_hit - non_hit
        results.append(out)
        if i % 4 == 0 or i == len(combos):
            logger.info(f"Calibration: {i}/{len(combos)}")

    df = pd.DataFrame(results)
    primary_h = horizons[0]
    sort_col = f"excess_mean_h{primary_h}"
    df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)

    show_cols = ["combo_id", "vcp_quality_threshold", "min_contractions",
                 "max_pivot_atr_pct", f"sig_n_h{primary_h}",
                 f"sig_mean_h{primary_h}", f"non_mean_h{primary_h}",
                 f"excess_mean_h{primary_h}", f"sig_hit_h{primary_h}"]
    show_cols = [c for c in show_cols if c in df.columns]
    print(f"\n=== Top {args.top} parameter combinations "
          f"(sorted by excess mean @ {primary_h}d) ===")
    print(df.head(args.top)[show_cols].to_string(index=False))

    if args.out:
        df.to_csv(args.out, index=False)
        logger.info(f"Full sweep saved to {args.out} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
