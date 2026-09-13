# VCP Signals 🔍

Volatility Contraction Pattern (VCP) signal detection, analysis, and forward-return
validation — based on Mark Minervini's VCP methodology.

This package ships four complementary components:

1. **`vcp.run_scan` / `vcp.engine.vcp_detector`** — the VCP detector itself.
   Reads 1 year of OHLCV per ticker, identifies contraction waves, scores the
   pattern, and emits a quality-ranked JSON.
2. **`vcp.trend`** — Stage-2 trend template (Minervini) with RS filter, applied
   as a pre-filter on VCP signals. Closes the structural gap that
   made the bare VCP detector anti-predictive (see v3 report).
3. **`vcp.backtest`** — strategy-agnostic forward-return harness. Reads a
   signals JSON, fetches forward prices from yfinance / CSV cache / parquet
   cache, and reports per-horizon hit-rate, mean / median / std return, and a
   simulated equal-weight portfolio with optional intra-window stop-loss.
4. **`vcp.replay` + `vcp.cache` + `vcp.cli_cache` / `cli_replay` /
   `cli_calibrate`** — point-in-time historical replay harness over the
   S&P 500 with the `chinobing/historical_sp500_constituents` dataset
   (1996-present, survivorship-bias-free).

## Status

The full validation history:

- **v1** (2026-09-02) — 60-day forward return on 94 legacy signals; no
  control group, inconclusive. → `docs/VALIDATION_REPORT.md`
- **v2** (2026-09-02) — 5.7-year replay (2021-2026, 117K ticker-dates);
  bare VCP detector is **anti-predictive** (mean +1.87%, hit 56.8%,
  Sharpe 0.13, excess vs S&P 500 = -2.4pp). → `docs/VALIDATION_REPORT_v2.md`
- **v3** (2026-09-03) — A/B test adding the Stage-2 trend gate; **edge
  found**: per-trade mean **+3.30%** at 60d, hit **60.0%**, Sharpe
  **0.21**, excess vs S&P 500 = **+0.6pp**, **0 trades losing ≥30%**
  (vs 28 in baseline). ~48 trades/year. → `docs/VALIDATION_REPORT_v3.md`
- **v4** (2026-09-13) — parquet + xarray backtest layer; per-file 2x
  faster read, 2.2x smaller disk, parity verified vs CSV.
  → `docs/PARQUET_XARRAY_REPORT.md`

| Stage                    | Status                                  |
|--------------------------|-----------------------------------------|
| Detector                 | ✅ Working — emits JSON daily           |
| Schema fix               | ✅ Both cohorts now in scan JSON        |
| OHLCV cache              | ✅ 564 S&P 500 tickers + SPY, parquet mirrors available |
| Replay harness           | ✅ 87K signals in ~10 min (with trend gate) |
| Stage-2 trend gate       | ✅ Built, integrated, validated         |
| Calibration              | ✅ 16-combo sweep                        |
| **Edge**                 | ✅ **Found at 60d with trend gate (+0.6pp vs SPX)** |
| Parquet + xarray layer   | ✅ Parity verified, infrastructure for future scale |

## Quick Start

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
# Base install — everything you need for scan + backtest + tests
pip install -e ".[dev,backtest]"

# Optional: parquet-accelerated backtests for very large cohorts (1M+ signals)
pip install -e ".[parquet]"

# 1. Pre-cache S&P 500 OHLCV (~30s for ~560 tickers, 5y lookback)
python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8
#    Add --parquet to also write parquet mirrors (40 MB vs 91 MB on disk)

# 2. Scan specific tickers (live, no cache needed)
python3 vcp/run_scan.py --tickers AAPL,MSFT,NVDA

# 3. Scan with no ticker list — falls back to a small built-in S&P 500 sample
python3 vcp/run_scan.py --all

# 4. Use a local ticker list — drop tickers.txt in ~/.cache/vcp-signals/
echo "AAPL\nMSFT\nNVDA" > ~/.cache/vcp-signals/tickers.txt
python3 vcp/run_scan.py --all

# 5. Historical replay with Stage-2 trend gate + parquet backtest (~10 min)
python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 \
    --stride 5 --workers 8 --horizons 5,10,20,60 --stop-loss 0.10 \
    --trend-gate --parquet \
    --out-prefix output/replay_2021_2026_s5_trend

# 6. Validate a scan JSON against forward returns (CLI alternative to replay)
python3 vcp/backtest.py --signals output/replay_signals.json \
    --cache-dir ~/.cache/vcp-signals --parquet \
    --horizons 5,10,20,60 --stop-loss 0.10 --workers 8
```

### Optional dependency groups

| Group | Installs | When to use |
|---|---|---|
| `dev` | `ruff==0.16.5`, `mypy==2.3.1`, `pytest`, `pytest-cov`, `matplotlib` | Testing and CI; pinned to the local + CI matrix |
| `backtest` | `matplotlib`, `xarray>=2024.1`, `pyarrow>=15.0` | xarray aggregation (`BacktestResult.to_xarray()`) and columnar backtest outputs |
| `parquet` | `xarray>=2024.1`, `pyarrow>=15.0`, `dask>=2024.1` | Parquet price source for >1M-signal cohorts; convert CSVs first with `vcp.cli_cache --convert-only` |

Default `dependencies` already includes `pyarrow>=15.0` (used by the S&P 500 constituents parquet cache); `parquet` adds `dask` for parallel IO.

**Parquet is faster per-read (2x) and smaller on disk (2.2x) than CSV**, but at the current scale (87K signals / 500 unique tickers / 165 KB per file), the per-file open overhead makes it ~5% slower in wall time. The parquet path is **infrastructure for future scale** — switch to it when cohorts exceed ~1M signals or files exceed ~10 MB. See `docs/PARQUET_XARRAY_REPORT.md` for benchmarks and migration instructions.

## How the Detector Works

```
Price History → Wave Detection → Range Contraction → Volume Analysis → VCP Score
```

The engine analyzes ~1 year of price history for:

1. **Contraction Waves** — three fixed phase windows (default 42 days each) sliced
   over the lookback. Each wave has a (range, volume, mean_price) fingerprint.
2. **Range Contraction** — each wave must be ≤ 80% of the prior wave's range
   (the strict monotonic check that disqualified even my own textbook synthetic
   VCP on first run; see validation report for the implications).
3. **Volume Decline** — last-wave average volume must be at least 20% below
   first-wave average.
4. **Pivot Tightness** — final-pivot ATR (14-day) must be below 4% of price;
   below 2% is the "ultra-tight" flag.
5. **VCP Quality Score** — weighted composite of contraction (30%), volume
   (20%), tightness (25%), ATR compression (15%), contraction count (10%).

```json
{
  "ticker": "AAPL",
  "vcp_detected": true,
  "vcp_quality": 0.85,
  "contractions": 3,
  "pivot_volatility_pct": 0.56,
  "volume_dry_up_ratio": 0.38,
  "range_contraction_ratio": 0.35,
  "stop_suggestion": 148.50,
  "stop_pct": 0.06,
  "signals": ["VCP_3c", "ULTRA_TIGHT", "VOL_DRY_UP", "RANGE_HALVED", "VCP_CONFIRMED"],
  "rationale": "VCP ✓ Q=85% waves=3 pivot_ATR=2.0% vol_dry=38% range_ratio=35%"
}
```

## How the Trend Gate Works (v3)

The detector fires on contraction patterns, but **VCP itself is a timing signal, not a selection signal**. The v2 replay showed the bare detector firing on consolidations in *declining trends* — and most of those break down, not up. The v3 trend gate is the structural fix.

```
OHLCV (cached) ──► Stage-2 Trend Template (Minervini)
                          │
                          ├── Price > SMA(50) > SMA(150) > SMA(200)
                          ├── SMA(200) slope positive (20d)
                          ├── Within 25% of 52-week high
                          ├── ≥30% above 52-week low
                          └── RS vs SPY > 0 over 3M / 6M / 12M
                          │
                          ▼
                  Passes? ─── no ──► reject with trend_blocked=True
                          │
                         yes
                          │
                          ▼
                   VCP Detector (the rest as before)
```

The gate runs **before** the VCP detector: tickers failing the trend template never reach the detector. Each replayed (date, ticker) gets a `trend_blocked` flag in metadata so the backtester can A/B compare the trend-passed cohort vs the trend-rejected cohort.

```python
# CLI: enable the trend gate
python3 -m vcp.cli_replay ... --trend-gate
python3 -m vcp.cli_replay ... --trend-gate --no-rs-check   # ablation: SMA-only
```

See `docs/VALIDATION_REPORT_v3.md` for the full A/B numbers.

## How the Backtester Works

```
Signals JSON → SignalRecord list → Price source
                                  (yfinance / CSV cache / parquet cache)
                                              ↓
                                      For each signal:
                                        fetch OHLCV [signal_date, signal_date + horizon]
                                        compute forward return at each horizon
                                        track min_in_window for stop-loss
                                              ↓
                                      Group by signal/non-signal/score bucket
                                        → summary table + portfolio simulation
                                              ↓
                                   to_xarray() (optional)
                                     labelled Dataset for downstream analysis
```

Key design decisions:

- **Strategy-agnostic core.** The backtester knows nothing about VCP. It consumes
  `SignalRecord(ticker, signal_date, entry_price, score, is_signal, metadata)`;
  any detector that emits those rows plugs in.
- **Schema-tolerant loader.** `load_signals_from_json` reads both the legacy
  `{signals: [{ticker, price, score, is_signal}]}` schema and the current
  `{all_signals: [{ticker, pivot_price, vcp_quality, vcp_detected}]}` schema,
  plus the trend-gate fields (`trend_blocked`, `trend_passed`, `trend_rs_avg`).
- **Swappable price sources.** `YahooPriceSource` for live validation;
  `CsvPriceSource` for offline replay against locally cached OHLCV;
  `ParquetPriceSource` (with optional dask backend) for fast reads on
  large cohorts. Parquet falls back to CSV automatically.
- **Intra-window stop-loss.** When you pass `--stop-loss 0.10`, the per-trade
  return is `min(horizon_return, -0.10)` if the *worst* close in the
  [entry, entry+horizon] window hit -10% — i.e. we treat the stop as a
  forced exit, not a post-hoc cap.
- **xarray aggregation.** `BacktestResult.to_xarray()` returns a labelled
  `xr.Dataset` with cohort flags (is_signal, trend_passed, trend_blocked) as
  data variables and per-horizon returns, ready for downstream multi-strategy
  analysis (slice by horizon, filter by trend cohort, etc).

## Configuration

The detector uses a frozen `VCPConfig` dataclass — modify `VC` in
`vcp/engine/vcp_detector.py` and re-run. Key knobs:

| Field | Default | Meaning |
|---|---|---|
| `min_history_days` | 126 | Minimum ~6 months of data |
| `min_contractions` | 2 | Minimum wave count |
| `max_pivot_atr_pct` | 0.04 | Max 14-day ATR % at the pivot |
| `ideal_pivot_atr_pct` | 0.02 | ATR % below which we flag "ULTRA_TIGHT" |
| `volume_dry_up_threshold` | 0.50 | Current vol / base-vol must be below this |
| `contraction_ratio_threshold` | 0.80 | Each wave ≤ 80% of prior |
| `vcp_quality_threshold` | 0.50 | Minimum composite quality to flag `vcp_detected=True` |
| `phase_windows` | (42, 42, 42) | P1, P2, P3 size in trading days |

The trend gate uses a frozen `TrendConfig` dataclass in `vcp/trend.py`. Key knobs:

| Field | Default | Meaning |
|---|---|---|
| `sma_fast` / `sma_slow` / `sma_trend` | 50 / 150 / 200 | SMA windows in trading days |
| `slope_lookback` | 20 | Days for the 200d SMA slope check |
| `high_lookback` | 252 | 52-week (1y) window |
| `high_proximity_pct` | 0.25 | Must be within 25% of 52w high |
| `low_clearance_pct` | 0.30 | Must be 30%+ above 52w low |
| `rs_periods` | (63, 126, 252) | 3M / 6M / 12M RS windows vs SPY |
| `rs_min_avg` | 0.0 | Average RS must be > this |
| `enabled` | True | Master switch |

## Architecture

```
vcp/
├── __init__.py
├── run_scan.py            # CLI: live scan → JSON (single date)
├── cli_cache.py           # CLI: pre-cache S&P 500 OHLCV (--parquet, --convert-only)
├── cli_replay.py          # CLI: historical replay (--trend-gate, --parquet, --dask)
├── cli_calibrate.py       # CLI: threshold sweep against replay signals
├── engine/
│   ├── vcp_detector.py    # VCP detection algorithm (canonical)
│   └── config.py          # Backwards-compat re-export of VC
├── trend.py               # Stage-2 trend template (Minervini) + RS gate (v3)
├── data/
│   ├── loader.py          # Live / cached OHLCV loader (canonicalized columns)
│   └── __init__.py
├── cache.py               # S&P 500 historical constituents + OHLCV cache (CSV + parquet)
├── replay.py              # Point-in-time replay harness (accepts trend_config)
├── backtest.py            # Forward-return validation harness (Csv/Parquet/Yahoo + to_xarray)
└── output/                # Scan results, replay outputs (gitignored)

tests/
├── conftest.py            # Shared fixtures (synthetic VCP series)
├── test_detector.py       # 10 tests: detector unit
├── test_backtest.py       # 9 tests: backtester unit + intra-window stop
├── test_loader.py         # 7 tests: data loader + canonicalization
├── test_replay.py         # 9 tests: cache + replay
├── test_schema.py         # 3 tests: dual-cohort scan JSON schema
├── test_trend.py          # 7 tests: trend template (pass/fail/RS/etc.)
└── test_parquet_xarray.py # 9 tests: parquet parity + xarray shape (v4)

docs/
├── VALIDATION_REPORT.md        # v1: 60d forward return on 94 legacy signals (preliminary)
├── VALIDATION_REPORT_v2.md     # v2: 5.7-year replay (117K signals) — bare VCP has no edge
├── VALIDATION_REPORT_v3.md     # v3: A/B with Stage-2 trend gate — edge found (+3.30% mean, Sharpe 0.21)
└── PARQUET_XARRAY_REPORT.md   # v4: parquet price source, xarray aggregation, parity verified
```

## VCP Detection Criteria

- **Minimum 2 contraction waves** over last 3–12 months
- **Range contraction** of at least 20% between waves (strict monotonic)
- **Volume dry-up** of at least 20% from wave 1 to last wave
- **Pivot volatility** under 4% (tight final consolidation; < 2% = ultra-tight)
- **Minimum 126 trading days** of price history

## Trend Template Criteria (when `--trend-gate` is enabled)

- Price > 50-day SMA > 150-day SMA > 200-day SMA (alignment)
- 200-day SMA slope positive over last 20 days
- Price within 25% of 52-week high
- Price at least 30% above 52-week low
- Relative strength vs SPY > 0 over average of 3M / 6M / 12M windows

## Relationship to VMAA

VCP Signals operates as a **standalone VCP scanner** that can:

- Run on any ticker list (no VMAA dependency)
- Run with or without the trend-template gate
- Export results for downstream selection (RS, fundamental, …)
- Validate itself against forward returns via `vcp/backtest.py`

The VCP pattern is a *timing* filter; with the trend-template gate enabled
(v3), the detector is also a coarse *selection* filter (only Stage-2 uptrends
are eligible). For VMAA's full pipeline you may still want additional
layers (RS ranking, fundamental quality) on top.

## Development

```bash
source .venv/bin/activate
pytest tests/ -v                # 53 tests (44 unit + 9 parquet/xarray)
ruff check vcp tests            # lint
mypy vcp                        # type check (12 source files)
pytest --cov=vcp tests/         # coverage report
```

## Validation

```bash
# 0. One-time: pre-cache S&P 500 OHLCV with parquet mirrors (~30s)
python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8 --parquet
#    Or, if you already have CSV cache:
#    python3 -m vcp.cli_cache --convert-only --workers 8

# 1. Run the 5-year trend-gated replay with parquet backtest (~10 min)
python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 \
    --stride 5 --workers 8 --horizons 5,10,20,60 --stop-loss 0.10 \
    --trend-gate --parquet \
    --out-prefix output/replay_2021_2026_s5_trend

# 2. Sweep thresholds against the replay (~50s, single backtest + slicing)
python3 -m vcp.cli_calibrate --signals output/replay_2021_2026_s5_trend_signals.json \
    --horizons 20,60 --workers 8 --top 8 \
    --out output/calibration.csv

# 3. Forward-validate a scan JSON directly (no replay)
python3 vcp/backtest.py --signals output/vcp_signals_latest.json \
    --cache-dir ~/.cache/vcp-signals --parquet \
    --horizons 5,10,20,60 --stop-loss 0.10 \
    --out output/validation.csv
```

Outputs:
- `output/replay_2021_2026_s5_trend_signals.json` — 87K rows (all date-ticker combinations, with `trend_blocked` flag)
- `output/replay_2021_2026_s5_trend_summary.csv` — headline forward-return summary (signals vs non-signals per horizon)
- `output/replay_2021_2026_s5_trend_by_year.csv` — per-year breakdown
- `output/replay_2021_2026_s5_trend_by_quality.csv` — per-quality-bucket breakdown
- `output/calibration.csv` — full 16-combo threshold sweep

## Known limitations

- **Trend gate isn't a full Stage-2 trend template.** It uses the canonical
  Minervini rules but doesn't include all refinements (e.g. earnings proximity,
  sector rotation). Acceptable for v3; could be tightened later.
- **2024 was a flat year for the trend-gated cohort** (mean +0.07%, hit 52%).
  The gate doesn't save us in choppy regimes — it eliminates the worst-case
  *failing-trend* setups but doesn't pick winners when nothing's working.
- **Out-of-sample test still required.** The v3 result is in-sample (2021-2026).
  Replay on a different window (e.g. 2016-2020 bear-market years) before
  committing real capital.
- **Calibration is one-pass.** The v3 sweep varies quality-threshold but
  doesn't revisit the wave-detection algorithm itself. A pivot-detection
  rewrite is in the v4 backlog.
- **Hardcoded 3-phase windows.** The (42, 42, 42) split assumes a 6-month
  base. Real VCPs vary from 3 to 18 months; this misses longer bases and
  flags shorter ones falsely.
- **Parquet is not a wall-time win at the current scale.** Per-file 2x faster
  read and 2.2x smaller disk, but the 87K-signal replay is ~5% slower than
  CSV because per-file open overhead dominates. Switch to parquet when
  cohorts exceed ~1M signals or files exceed ~10 MB.
- **Position-sizing is not modelled.** The simulated portfolio compounds
  equal-weight forever — a real strategy would size to a fixed capital pool,
  with materially different drawdown characteristics.

## License

MIT
