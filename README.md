# VCP Signals 🔍

Volatility Contraction Pattern (VCP) signal detection, analysis, and forward-return
validation — based on Mark Minervini's VCP methodology.

This package ships two complementary components:

1. **`vcp.run_scan` / `vcp.engine.vcp_detector`** — the VCP detector itself.
   Reads 1 year of OHLCV per ticker, identifies contraction waves, scores the
   pattern, and emits a quality-ranked JSON.
2. **`vcp.backtest`** — a strategy-agnostic forward-return harness. Reads a scan
   JSON, fetches forward prices from yfinance (or a local CSV cache), and reports
   per-horizon hit-rate, mean / median / std return, and a simulated equal-weight
   portfolio with optional stop-loss.

## Status

The full validation history: v1 (60d window, no control group) → v2 (5.7-year replay, no edge) → **v3 (A/B with Stage-2 trend gate, edge found)**. Current report is v3.

A 5.7-year historical replay (2021-01 → 2026-09) shows:

- **VCP detector alone:** anti-predictive. Per-trade mean +1.87% at 60d, hit 56.8%, Sharpe 0.13, excess vs S&P 500 = -2.4pp. 28 trades lost ≥30%. Not tradeable.
- **Stage-2 trend gate + VCP:** first positive result. Per-trade mean **+3.30%** at 60d, hit **60.0%**, Sharpe **0.21**, excess vs S&P 500 = **+0.6pp**. **0 trades lost ≥30%.** Tradeable at ~48 trades/year.

See `docs/VALIDATION_REPORT_v3.md` for the full A/B analysis and `docs/VALIDATION_REPORT_v2.md` for the baseline measurement.

| Stage          | Status                                  |
|----------------|-----------------------------------------|
| Detector       | ✅ Working — emits JSON daily           |
| Schema fix     | ✅ Both cohorts now in scan JSON        |
| OHLCV cache    | ✅ 564 S&P 500 tickers + SPY, point-in-time |
| Replay harness | ✅ 87K signals in ~10 min (with trend gate) |
| Stage-2 trend gate | ✅ Built, integrated, validated      |
| Calibration    | ✅ 16-combo sweep                        |
| **Edge**       | ✅ **Found at 60d with trend gate (+0.6pp vs SPX)** |

## Quick Start

```bash
# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Scan specific tickers
python3 vcp/run_scan.py --tickers AAPL,MSFT,NVDA

# Scan with no ticker list (falls back to a small built-in S&P 500 sample)
python3 vcp/run_scan.py --all

# Use a local ticker list — drop tickers.txt in ~/.cache/vcp-signals/
echo "AAPL\nMSFT\nNVDA" > ~/.cache/vcp-signals/tickers.txt
python3 vcp/run_scan.py --all

# Validate a previous scan against forward returns
python3 vcp/backtest.py --signals output/vcp_signals_latest.json \
                        --horizons 5,10,20,60 --workers 6 \
                        --stop-loss 0.10 \
                        --out output/validation.csv
```

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

## How the Backtester Works

```
Scan JSON → SignalRecord list → Price source (yfinance / CSV cache)
                                      ↓
                              For each signal:
                                fetch OHLCV [signal_date, signal_date + horizon]
                                compute forward return at each horizon
                                      ↓
                              Group by signal/non-signal/score bucket
                                → summary table + portfolio simulation
```

Key design decisions:

- **Strategy-agnostic core.** The backtester knows nothing about VCP. It consumes
  `SignalRecord(ticker, signal_date, entry_price, score, is_signal, metadata)`;
  any detector that emits those rows plugs in.
- **Schema-tolerant loader.** `load_signals_from_json` reads both the legacy
  `{signals: [{ticker, price, score, is_signal}]}` schema and the current
  `{all_signals: [{ticker, pivot_price, vcp_quality, vcp_detected}]}` schema.
- **Swappable price sources.** `YahooPriceSource` for live validation;
  `CsvPriceSource` for offline replay against locally cached OHLCV.

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

## Architecture

```
vcp/
├── __init__.py
├── run_scan.py         # CLI: live scan → JSON (single date)
├── cli_cache.py        # CLI: pre-cache S&P 500 OHLCV from yfinance
├── cli_replay.py       # CLI: historical replay (point-in-time)
├── cli_calibrate.py    # CLI: threshold sweep against replay signals
├── engine/
│   ├── vcp_detector.py # VCP detection algorithm (canonical)
│   └── config.py       # Backwards-compat re-export of VC
├── data/
│   ├── loader.py       # Live / cached OHLCV loader (canonicalized columns)
│   └── __init__.py
├── cache.py            # S&P 500 historical constituents + OHLCV cache
├── replay.py           # Point-in-time replay harness
├── backtest.py         # Forward-return validation harness
└── output/             # Scan results, replay outputs (gitignored)

tests/
├── conftest.py         # Shared fixtures (synthetic VCP series)
├── test_detector.py    # Detector unit tests
├── test_backtest.py    # Backtester unit tests
├── test_loader.py      # Data loader tests
├── test_replay.py      # Cache + replay tests
└── test_schema.py      # Scan JSON schema tests (detected + non-detected cohorts)

docs/
├── VALIDATION_REPORT.md     # v1: 60d forward return on 94 legacy signals
└── VALIDATION_REPORT_v2.md  # v2: 5.7-year replay (117K signals) — no edge found
```

## VCP Detection Criteria

- **Minimum 2 contraction waves** over last 3–12 months
- **Range contraction** of at least 20% between waves (strict monotonic)
- **Volume dry-up** of at least 20% from wave 1 to last wave
- **Pivot volatility** under 4% (tight final consolidation; < 2% = ultra-tight)
- **Minimum 126 trading days** of price history

## Relationship to VMAA

VCP Signals operates as a **standalone VCP scanner** that can:

- Run on any ticker list (no VMAA dependency)
- Export results for downstream selection (RS, trend template, …)
- Validate itself against forward returns via `vcp/backtest.py`

The VCP pattern is a *timing* filter; for the selection layer (Stage 2 in VMAA's
pipeline) you still need a separate trend template + relative-strength gate. The
detector does not check those.

## Development

```bash
source .venv/bin/activate
pytest tests/ -v                # 36 tests
ruff check vcp tests            # lint
mypy vcp                        # type check (11 source files)
pytest --cov=vcp tests/         # coverage report
```

## Validation

```bash
# Pre-cache S&P 500 OHLCV (~30s)
python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8

# Run the 5-year replay (~2-3 min)
python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 \
    --stride 5 --workers 8 --horizons 5,10,20,60 --stop-loss 0.10 \
    --out-prefix output/replay_2021_2026_s5

# Sweep thresholds (~1 min)
python3 -m vcp.cli_calibrate --signals output/replay_2021_2026_s5_signals.json \
    --horizons 20,60 --workers 8 --top 8 \
    --out output/calibration.csv
```

## Known limitations

- **The current detector has no edge.** The 5.7-year replay shows VCP signals
  trail the S&P 500 by 2.4pp at 60d and underperform the non-detected cohort
  at every horizon. See `docs/VALIDATION_REPORT_v2.md` for the full data.
- **No multi-stage gate.** VCP is a *timing* signal, not a *selection* one.
  The detector doesn't check Stage 2 trend template (price > 150/200-day MA,
  52w-high proximity) or relative strength — so it flags setups across
  failing trends, which dilutes the signal.
- **Hardcoded 3-phase windows.** The (42, 42, 42) split assumes a 6-month
  base. Real VCPs vary from 3 to 18 months; this misses longer bases and
  flags shorter ones falsely.
- **Calibration is one-pass.** The current sweep varies quality-threshold
  and structural gates but doesn't revisit the wave-detection algorithm
  itself. v3 work plan in `VALIDATION_REPORT_v2.md`.

## License

MIT
