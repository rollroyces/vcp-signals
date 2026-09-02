#!/usr/bin/env python3
"""
CLI: pre-cache S&P 500 OHLCV for the replay window.

Examples:
    python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8
    python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --tickers AAPL,MSFT
    python3 -m vcp.cli_cache --status
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from vcp.cache import (
    cache_dir,
    cache_stats,
    fetch_many,
    union_universe,
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("vcp.cli_cache")

    parser = argparse.ArgumentParser(description="Pre-cache OHLCV for replay")
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument("--end", type=str, default="2026-09-01")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--force", action="store_true", help="Re-fetch even if cached")
    parser.add_argument("--tickers", type=str, default="",
                        help="Comma-separated explicit tickers (overrides universe)")
    parser.add_argument("--status", action="store_true", help="Print cache stats and exit")
    args = parser.parse_args()

    if args.status:
        stats = cache_stats()
        print(f"Cache directory: {cache_dir()}")
        print(f"  Tickers cached: {stats['ticker_count']}")
        print(f"  Date range:     {stats['oldest']} → {stats['newest']}")
        print(f"  Size:           {stats['size_mb']} MB")
        return 0

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        logger.info(f"Using explicit ticker list: {len(tickers)} names")
    else:
        logger.info("Loading S&P 500 historical constituents…")
        union = union_universe(start, end)
        tickers = sorted(union)
        logger.info(f"Union of S&P 500 between {args.start} and {args.end}: {len(tickers)} tickers")

    if not tickers:
        logger.error("No tickers to cache")
        return 1

    # Period must cover start - 1y (detector lookback) plus forward window
    span_days = (end - start).days + 365
    period = f"{max(span_days // 365, 2)}y"

    logger.info(f"Fetching OHLCV ({period} lookback) for {len(tickers)} tickers, "
                f"{args.workers} workers")
    result = fetch_many(tickers, period=period, workers=args.workers, force=args.force)
    cached = sum(1 for v in result.values() if v)
    failed = [t for t, v in result.items() if not v]
    logger.info(f"Done: {cached} cached, {len(failed)} failed")
    if failed:
        logger.info(f"Failed tickers (sample): {failed[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
