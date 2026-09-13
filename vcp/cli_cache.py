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
    parser.add_argument("--parquet", action="store_true",
                        help="Also write parquet mirrors for ParquetPriceSource (faster)")
    parser.add_argument("--convert-only", action="store_true",
                        help="Skip yfinance fetch; only convert existing CSVs to parquet")
    args = parser.parse_args()

    if args.status:
        stats = cache_stats()
        print(f"Cache directory: {cache_dir()}")
        print(f"  Tickers cached (CSV):     {stats['ticker_count']}")
        print(f"  Parquet mirrors:          {stats.get('parquet_count', 0)}")
        print(f"  Date range:               {stats['oldest']} → {stats['newest']}")
        print(f"  CSV size:                 {stats['size_mb']} MB")
        print(f"  Parquet size:              {stats.get('parquet_mb', 0)} MB")
        return 0

    if args.convert_only:
        from vcp.cache import convert_csv_to_parquet
        logger.info("Converting existing CSV cache to parquet mirrors…")
        result = convert_csv_to_parquet(workers=args.workers)
        n_ok = sum(1 for v in result.values() if v)
        logger.info(f"Done: {n_ok}/{len(result)} converted")
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
    fetch_result: dict[str, str | None] = fetch_many(
        tickers, period=period, workers=args.workers,
        force=args.force, write_parquet=args.parquet,
    )
    cached = sum(1 for v in fetch_result.values() if v)
    failed = [t for t, v in fetch_result.items() if not v]
    logger.info(f"Done: {cached} cached, {len(failed)} failed")
    if failed:
        logger.info(f"Failed tickers (sample): {failed[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
