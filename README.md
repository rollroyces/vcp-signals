# VCP Signals 🔍

VMAA Volatility Contraction Pattern (VCP) signal detection & analysis engine.

Based on Mark Minervini's VCP methodology — identifies stocks experiencing
volatility contractions (narrowing price ranges on declining volume) that
precede significant breakouts.

## Features

- **VCP Detection**: Multi-wave contraction identification
- **Quality Scoring**: 0.0-1.0 rating of pattern textbook-ness
- **Volume Analysis**: Dry-up volume confirmation
- **Pivot Detection**: Optimal entry price identification
- **Stop Suggestion**: Tightened stop-loss based on pivot structure

## How It Works

```
Price → Wave Detection → Contraction Verification → Volume Analysis → Quality Score
```

The engine analyzes price history for contraction waves (narrowing ranges)
with declining volume, then computes a quality score based on:
- Range contraction percentage between waves
- Volume decline trend
- Pivot tightness
- Structural integrity

## Output Format

```json
{
  "ticker": "AAPL",
  "vcp_detected": true,
  "vcp_quality": 0.85,
  "contractions": 3,
  "pivot_volatility_pct": 1.2,
  "volume_dry_up_ratio": 0.45,
  "rationale": "3-wave VCP with 65% range contraction and 55% volume dry-up"
}
```
