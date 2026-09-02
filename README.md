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

The detector has been running as a daily scan since May 2026. **The forward-return
edge has not yet been validated.** See `docs/VALIDATION_REPORT.md` for the
honest measurement and the prioritized next steps.

| Stage          | Status                                  |
|----------------|-----------------------------------------|
| Detector       | ✅ Working — emits JSON daily           |
| Validation     | ⚠️  Partial — only 60d forward window so far; result trails the S&P 500 |
| Backtester     | ✅ Working — covers horizons 5–60 days |
| Tests          | ✅ 24 tests passing, 55% line coverage  |
| Lint / types   | ✅ ruff + mypy clean                    |
| CI             | ✅ GitHub Actions (3.10 / 3.11 / 3.12)  |

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
├── run_scan.py         # CLI entry point (python3 vcp/run_scan.py)
├── engine/
│   ├── vcp_detector.py # VCP detection algorithm (canonical)
│   └── config.py       # Backwards-compat re-export of VC
├── data/
│   ├── loader.py       # Price data: yfinance / CSV cache
│   └── __init__.py
├── backtest.py         # Forward-return validation harness
└── output/             # Scan results (gitignored)

tests/
├── conftest.py         # Shared fixtures (synthetic VCP series)
├── test_detector.py    # Detector unit tests
├── test_backtest.py    # Backtester unit tests
└── test_loader.py      # Data loader tests

docs/
└── VALIDATION_REPORT.md  # Measured forward-return edge (or lack thereof)
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
pytest tests/ -v                # 24 tests
ruff check vcp tests            # lint
mypy vcp                        # type check
pytest --cov=vcp tests/         # coverage report
```

## Known limitations

- **No non-signal comparison group** — the daily scan JSON only emits detected
  tickers, so we cannot measure filter effectiveness vs. a random sample. The
  single biggest gap. Fix in `run_scan.py` (emit `all_signals` with both
  detected and non-detected rows).
- **Detector calibration is not data-driven.** The quality-threshold weights
  are Minervini-tradition, not fit to historical breakouts.
- **No multi-year historical replay.** Validation only covers one 60-day forward
  window so far. The next deliverable should be a rolling-window replay harness
  against local OHLCV cache.
- **Hardcoded 3-phase windows.** A more robust implementation would detect
  contraction pivots dynamically rather than slicing the lookback into thirds.

## License

MIT
