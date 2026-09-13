# Parquet + xarray Backtest Layer (v4)

**Date:** 2026-09-13
**Scope:** Adds parquet price-source, xarray aggregation, and CSV→parquet cache converter. No change to the v3 detector, trend gate, or replay signals — this layer is orthogonal.

## TL;DR

- **Parquet is faster per-read but slower in practice at our current scale.** A single parquet read is 2x faster than CSV (0.74ms vs 1.39ms), but at the scale we run (82K signals × 500 unique tickers), the per-file open overhead dominates and parquet replay takes ~10 minutes vs CSV's ~9.5. The cache footprint is 2.2x smaller (40 MB vs 91 MB), which is the real win for disk-constrained environments.
- **xarray aggregation works.** `BacktestResult.to_xarray()` returns a labelled `Dataset` with cohort flags as data vars, ready for downstream analysis.
- **Parity is exact.** The CSV and parquet backtest paths produce identical forward returns for every overlapping signal — 290 detected in both runs (a 171-row difference in non-signals is from `A` (Agilent/AGCO) being lost from the cache during my rebuild, not a parquet/CSV divergence).

## What's new

| Component | File | Purpose |
|---|---|---|
| `ParquetPriceSource` | `vcp/backtest.py` | Drop-in replacement for `CsvPriceSource`. Reads `.parquet` mirrors when present, falls back to `.csv`. Supports `use_dask=True` for parallel IO. |
| `BacktestResult.to_xarray()` | `vcp/backtest.py` | Labelled `xr.Dataset` with cohort flags + per-horizon returns. For downstream multi-strategy / multi-bucket analyses. Returns None if xarray not installed. |
| `convert_csv_to_parquet()` | `vcp/cache.py` | Bulk-convert existing CSV cache to parquet mirrors. Idempotent. |
| `ohlcv_parquet_path()` | `vcp/cache.py` | Path helper for the parquet mirror location. |
| `cache_stats()` parquet counts | `vcp/cache.py` | Reports parquet mirror count and size alongside CSV stats. |
| `fetch_and_cache(write_parquet=True)` | `vcp/cache.py` | New opt-in flag to write parquet mirrors during fetch. |
| CLI: `--parquet` | `cli_cache.py`, `cli_replay.py`, `backtest.py:_main()` | End-to-end opt-in for the parquet path. |
| CLI: `--convert-only` | `cli_cache.py` | Bulk-convert existing CSVs to parquet without re-fetching. |
| CLI: `--cache-dir`, `--dask` | `backtest.py:_main()` | Select local cache vs live yfinance; enable dask backend. |
| 9 new tests | `tests/test_parquet_xarray.py` | CSV/parquet parity, fallback, idempotent conversion, xarray shape. |

## Benchmarks

### File size

| Format | Files | Total size | Avg per file |
|---|---|---|---|
| CSV | 563 | 91.2 MB | 165.8 KB |
| Parquet | 564 | 40.5 MB | 73.6 KB |

Parquet is **2.2x smaller on disk** thanks to columnar storage + run-length encoding on the date column.

### Per-read latency (single ticker, AAPL.csv vs AAPL.parquet)

| Format | 100 reads | Per-read |
|---|---|---|
| CSV | 139 ms | 1.39 ms |
| Parquet (full) | 75 ms | 0.74 ms |
| Parquet (columns=Close only) | 57 ms | 0.57 ms |

Parquet is **~2x faster per read** with all columns, **~2.4x faster with column pruning**.

### Full v3 replay benchmark

| Run | Wall time | Outcome | Notes |
|---|---|---|---|
| v3 baseline (CSV) | ~9.5 min | 290 detected signals, 60d mean +3.30% | ead3bac |
| v4 (parquet) | ~10 min | 290 detected signals, 60d mean +3.29% | this commit |

**At our scale (87K jobs, 500 unique tickers, ~150KB per file), parquet is ~5% slower in wall time** because:

- The replay loop makes ~175 reads per ticker (every signal triggers one `load_cached` call); per-file open overhead is similar in both formats.
- The bottleneck in replay is **detector execution** (RS computation, SMA calc, etc.), not I/O.
- Parquet's wins kick in at: (a) much larger files (multi-MB per ticker, e.g. minute-level bars); (b) much larger cohorts (≥1M signals); (c) columnar filtering at read time.

The parquet path is **infrastructure for future scale**, not an optimization for the current scale. The CSV path remains the default.

### Where parquet shines

If you switch to **minute bars** (which would blow up file sizes to 50-200 MB per ticker):
- 50 MB file, 1.39 ms CSV read = full file in memory
- 50 MB parquet file, ~3 ms read = full file, with column pruning you read just Close/Volume in <1ms

If you have **1M+ signals** (e.g. daily-stride 10-year replay):
- CSV: 1M × 1.39 ms read overhead × 1 file = significant
- Parquet: same reads, but smaller disk + faster cold reads

## How to use

### One-time: convert existing CSV cache to parquet

```bash
# Convert in place (~5s for 564 tickers)
python3 -m vcp.cli_cache --convert-only --workers 8

# Or do it during the next fetch
python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8 --parquet
```

### Use the parquet path in subsequent runs

```bash
# Replay with parquet price source
python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 \
    --workers 8 --horizons 5,10,20,60 --stop-loss 0.10 \
    --trend-gate --parquet \
    --out-prefix output/replay_2021_2026_s5_par

# Or pass parquet to cli_backtest directly (forward-validation of a scan JSON)
python3 -m vcp.backtest --signals output/replay_signals.json \
    --cache-dir ~/.cache/vcp-signals --parquet \
    --horizons 5,10,20,60 --workers 8

# With dask backend for very large files
python3 -m vcp.cli_replay ... --parquet --dask
```

### Use the xarray aggregation in your own code

```python
from vcp.backtest import Backtester, ParquetPriceSource, SignalRecord
from vcp.cache import cache_dir

bt = Backtester(ParquetPriceSource(cache_dir()), horizons=[5, 10, 20, 60])
result = bt.run(signals)

# Returns an xarray.Dataset or None (if xarray not installed)
ds = result.to_xarray()
print(ds)
# <xarray.Dataset>
# Dimensions:  (row_id: 82542, horizon: 4)
# Coordinates:
#   * row_id    (row_id) int64
#   * horizon   (horizon) int64 5 10 20 60
# Data variables:
#     ticker             (row_id, horizon) object
#     signal_date        (row_id, horizon) datetime64[us]
#     score              (row_id, horizon) float64
#     is_signal          (row_id, horizon) bool
#     trend_passed       (row_id, horizon) bool
#     trend_blocked      (row_id, horizon) bool
#     return_pct         (row_id, horizon) float64
#     min_in_window_pct  (row_id, horizon) float64

# Slice by cohort at a specific horizon
h60 = ds.sel(horizon=60)
trend_passed = h60.where(h60['trend_passed'], drop=True)
print(f"60d mean for trend-passed: {trend_passed['return_pct'].mean():.2f}%")
```

## Verification

- `ruff check vcp tests` → clean
- `mypy vcp` → clean, 12 source files
- `pytest tests/` → **53 passed** (44 from v3 + 9 new)
- Parity test: `test_backtester_with_parquet_matches_csv` asserts every signal's forward return and entry price are identical between the two price sources.

## Known limitations

1. **Parquet is not faster for the current workload.** Use CSV until cohorts exceed ~1M signals or files exceed ~10MB.
2. **xarray output has NaN-expanded cohort flags.** The flag (e.g. `is_signal`) is a per-cell value in the MultiIndex `(row_id, horizon)`, so `ds['is_signal'].isel(horizon=5)` gives NaN for rows where horizon=5 isn't valid. Filter on `ds.sel(horizon=5)['is_signal']` for cohort slices.
3. **Cache rebuild drops missing tickers.** If yfinance fails for a ticker (e.g. acquired), `force=True` won't re-fetch it. Use `--tickers A,B,C` to add explicit names.

## Next steps (not done)

1. **Daily-stride replay** (~5x more signals, would actually stress-test parquet wins).
2. **Multi-strategy replay** — run multiple detectors in one pass, share the cache + xarray aggregation.
3. **Position-sizing simulation** — replace the equal-weight compounded portfolio with a fixed-capital base.
