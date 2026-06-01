#!/usr/bin/env python3
"""
VCP Signal Scanner — standalone runner.
"""
import sys, json, time, logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vcp.scan")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path: sys.path.insert(0, str(_REPO))
_WORKSPACE = _REPO.parent.parent
if str(_WORKSPACE) not in sys.path: sys.path.insert(1, str(_WORKSPACE))

from vcp.engine.vcp_detector import analyze_vcp, VCPResult, VC
from vcp.data.loader import load_price_data, get_ticker_list

SCAN_LIMIT = 500

def scan_single(ticker: str) -> Optional[dict]:
    hist = load_price_data(ticker)
    if hist is None:
        return None
    current_price = hist['Close'].iloc[-1]
    result = analyze_vcp(ticker, hist, current_price, config=VC)
    if result and result.vcp_detected:
        return result.to_dict()
    return None

def scan_batch(tickers: List[str], workers: int = 12) -> List[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    done = 0
    total = len(tickers)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(scan_single, t): t for t in tickers}
        for f in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                elapsed = time.time() - t0
                logger.info(f"Progress: {done}/{total} ({done/elapsed:.0f}/s, {len(results)} VCP)")
            try:
                r = f.result()
                if r:
                    results.append(r)
            except Exception as e:
                pass
    results.sort(key=lambda r: r.get("vcp_quality", 0), reverse=True)
    return results

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
        tickers = tickers[:args.limit]
    
    t_start = time.time()
    logger.info(f"Scanning {len(tickers)} tickers for VCP patterns...")
    results = scan_batch(tickers, workers=args.workers)
    elapsed = time.time() - t_start
    
    logger.info(f"\n{'='*60}")
    logger.info(f"VCP SCAN COMPLETE")
    logger.info(f"  Scanned:  {len(tickers)}")
    logger.info(f"  VCP found: {len(results)} ({len(results)/max(len(tickers),1)*100:.1f}%)")
    logger.info(f"  Elapsed:  {elapsed:.1f}s")
    logger.info(f"{'='*60}")
    
    if results:
        logger.info(f"\nTop VCP Signals:")
        for i, r in enumerate(results[:20], 1):
            logger.info(f"  {i:2d}. {r['ticker']:6s} Q={r['vcp_quality']*100:.0f}%  "
                        f"Waves={r.get('contractions',0)}  "
                        f"Vol={r.get('volume_dry_up_ratio',0):.2f}  "
                        f"Pivot={r.get('pivot_volatility_pct',0):.1f}%  "
                        f"{r.get('rationale','')[:60]}")
    
    out_path = args.output or f"output/vcp_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path("output").mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "scanned": len(tickers),
            "vcp_found": len(results),
            "top_signals": results[:50],
            "all_signals": results,
        }, f, indent=2, default=str)
    logger.info(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
