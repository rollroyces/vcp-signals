# VCP Signals 🔍

VMAA Volatility Contraction Pattern (VCP) signal detection & analysis engine.

Based on Mark Minervini's VCP methodology — identifies stocks experiencing
volatility contractions (narrowing price ranges on declining volume) that
precede significant breakouts.

## Quick Start

```bash
# Install deps
pip install -r requirements.txt

# Scan VMAA quality pool for VCP patterns
python3 vcp/run_scan.py

# Scan specific tickers
python3 vcp/run_scan.py --tickers AAPL,MSFT,NVDA

# Scan ALL cached fundamentals
python3 vcp/run_scan.py --all

# Save to file
python3 vcp/run_scan.py --all --output results.json
```

## How It Works

```
Price History → Wave Detection → Range Contraction → Volume Analysis → VCP Score
```

The engine analyzes 1-year price history for:
1. **Contraction Waves**: Multiple rounds of narrowing price ranges
2. **Volume Decline**: Trading volume drying up through each contraction
3. **Pivot Tightness**: Final pivot showing minimal volatility
4. **Structural Integrity**: Progressive contraction pattern (50% → 30% → 15% → 5%)

## Output

```json
{
  "ticker": "AAPL",
  "vcp_detected": true,
  "vcp_quality": 0.85,
  "contractions": 3,
  "pivot_volatility_pct": 0.56,
  "volume_dry_up_ratio": 0.38,
  "vcp_stop": 148.50,
  "rationale": "3-wave VCP with 72% range contraction and 62% volume dry-up"
}
```

## Architecture

```
vcp/
├── run_scan.py         # CLI entry point
├── engine/
│   ├── vcp_detector.py # VCP detection algorithm
│   └── config.py       # Detection parameters
├── data/
│   ├── loader.py       # Price data fetching
│   └── __init__.py
├── output/             # Scan results (gitignored)
└── README.md
```

## VCP Detection Criteria

- **Minimum 2 contraction waves** over last 3-12 months
- **Range contraction** of at least 50% between waves
- **Volume dry-up** of at least 30% from wave 1 to last wave
- **Pivot volatility** under 3% (tight final consolidation)
- **Minimum 60 trading days** of price history

## Relationship to VMAA

VCP Signals operates as a **standalone VCP scanner** that can:
- Scan results from VMAA's quality pool
- Run independently on any ticker list
- Export results for manual review or further analysis

The VCP pattern acts as Stage 2.5 in VMAA's pipeline — refining MAGNA entry signals with precision timing.
