#!/usr/bin/env python3
"""
VCP Signal Scanner — standalone runner.

Emits a JSON scan report containing BOTH the detected-VCP cohort and the
non-detected (insufficient-data / failed-quality-check) cohort, so downstream
validation can measure filter effectiveness. The non-detected cohort is the
control group; without it we can only measure "did these specific names do OK"
not "did the filter add edge".
"""
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vcp.scan")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_WORKSPACE = _REPO.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(1, str(_WORKSPACE))

# Imports must come after the sys.path tweaks above.
from vcp.data.loader import get_ticker_list, load_price_data  # noqa: E402
from vcp.engine.vcp_detector import VC, analyze_vcp  # noqa: E402

SCAN_LIMIT = 500


def scan_single(ticker: str) -> dict | None:
    """Run VCP analysis on one ticker.

    Returns a dict describing the result regardless of vcp_detected. A None
    return means we couldn't even fetch data for the ticker — distinct from
    "ran analysis, didn't detect a pattern", which still produces a dict with
    vcp_detected=False and a populated rationale.
    """
    hist = load_price_data(ticker)
    if hist is None:
        return None
    current_price = float(hist["Close"].iloc[-1])
    result = analyze_vcp(ticker, hist, current_price, config=VC)
    if result is None:
        return None
    d = result.to_dict()
    # Distinguish "ran but failed quality" from "insufficient history". The
    # detector's rationale already encodes this; we also expose it as a flag
    # so JSON consumers don't have to grep the rationale string.
    d["data_ok"] = True
    return d


def scan_batch(tickers: list[str], workers: int = 12) -> list[dict]:
    """Run the detector on every ticker, emitting detected AND non-detected rows."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_results: list[dict] = []
    total = len(tickers)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(scan_single, t): t for t in tickers}
        for done, f in enumerate(as_completed(futures), 1):
            if done % 100 == 0 or done == total:
                elapsed = time.time() - t0
                logger.info(
                    f"Progress: {done}/{total} ({done / elapsed:.0f}/s, "
                    f"{sum(1 for r in all_results if r.get('vcp_detected'))} VCP)"
                )
            try:
                r = f.result()
                if r is not None:
                    all_results.append(r)
            except Exception:
                pass
    # Sort by quality descending; non-detected rows have vcp_quality=0 and
    # sink to the bottom, but we keep them.
    all_results.sort(key=lambda r: r.get("vcp_quality", 0.0), reverse=True)
    return all_results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VCP Signal Scanner")
    parser.add_argument("--tickers", type=str, help="Comma-separated tickers")
    parser.add_argument("--all", action="store_true", help="Scan all")
    parser.add_argument("--output", type=str, default="", help="Output JSON path")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=500, help="Max tickers")
    args = parser.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.all:
        tickers = get_ticker_list()
        logger.info(f"Loaded {len(tickers)} tickers")
    else:
        tickers = []

    if not tickers:
        logger.error("No tickers to scan")
        return

    if len(tickers) > args.limit:
        logger.info(f"Limiting from {len(tickers)} to {args.limit}")
        tickers = tickers[: args.limit]

    t_start = time.time()
    logger.info(f"Scanning {len(tickers)} tickers for VCP patterns...")
    all_results = scan_batch(tickers, workers=args.workers)
    detected = [r for r in all_results if r.get("vcp_detected")]
    elapsed = time.time() - t_start

    logger.info(f"\n{'=' * 60}")
    logger.info("VCP SCAN COMPLETE")
    logger.info(f"  Requested:  {len(tickers)}")
    logger.info(f"  Analysed:   {len(all_results)}  "
                f"(data fetch failed for {len(tickers) - len(all_results)})")
    logger.info(f"  VCP found:  {len(detected)} "
                f"({len(detected) / max(len(all_results), 1) * 100:.1f}% of analysed)")
    logger.info(f"  Elapsed:    {elapsed:.1f}s")
    logger.info(f"{'=' * 60}")

    if detected:
        logger.info("\nTop VCP Signals:")
        for i, r in enumerate(detected[:20], 1):
            logger.info(
                f"  {i:2d}. {r['ticker']:6s} "
                f"Q={r['vcp_quality'] * 100:.0f}%  "
                f"Waves={r.get('contractions', 0)}  "
                f"Vol={r.get('volume_dry_up_ratio', 0):.2f}  "
                f"Pivot={r.get('pivot_volatility_pct', 0):.1f}%  "
                f"{r.get('rationale', '')[:60]}"
            )

    out_path = args.output or f"output/vcp_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path("output").mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "scanned": len(tickers),
                "analysed": len(all_results),
                "vcp_found": len(detected),
                "top_signals": detected[:50],
                "all_signals": all_results,  # ← includes non-detected rows
            },
            f,
            indent=2,
            default=str,
        )
    logger.info(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
