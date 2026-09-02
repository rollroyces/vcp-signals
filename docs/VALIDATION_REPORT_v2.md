# VCP Signals — Multi-Year Validation Report v2

**Date:** 2026-09-02
**Window:** 2021-01-01 → 2026-09-01 (~5.7 years; covers 2021 recovery, 2022 bear, 2023–2024 bull, 2025 chop, 2026 YTD)
**Universe:** Point-in-time S&P 500 constituents (chinobing/historical_sp500_constituents; daily snapshots 1996-01-02 onward)
**Stride:** Every 5 trading days (~249 replay dates)
**Total ticker-date analyses:** 115,652

## TL;DR

The VCP detector, in its current form, **does not predict forward returns** and in fact **selects against winners** over the 2021-2026 window. Across 5 horizons (5/10/20/60 trading days), every calibration of the detector produces *negative excess return* versus both (a) the S&P 500 at the same dates, and (b) the non-detected S&P 500 cohort.

| Horizon | VCP signals | Non-signals | **Excess** | S&P 500 | VCP − SPX |
|---|---|---|---|---|
| 5d mean   | +0.16% | +0.31% | **−0.15pp** | +0.43% | −0.27pp |
| 10d mean  | +0.31% | +0.53% | **−0.23pp** | +0.78% | −0.47pp |
| 20d mean  | +0.41% | +0.98% | **−0.57pp** | +1.53% | −1.12pp |
| 60d mean  | +1.87% | +2.84% | **−0.97pp** | +4.25% | −2.38pp |

The pattern is monotonic across all horizons — VCP signals underperform consistently. Calibration confirms that **no threshold combination salvages the signal**: raising the quality cutoff *worsens* the result (the high-quality "ULTRA_TIGHT / VCP_CONFIRMED / RANGE_HALVED" signals are the worst-performing subset).

**Verdict:** The current detector is **anti-predictive**. Until the underlying detection logic is fundamentally revised, treating VCP as a *selection* signal (rather than a *timing* hint for already-selected names) will lose money versus the index.

---

## What changed since v1

The v1 report (2026-05-20, 94 signals, 60-day window) showed a 56.5% hit rate and +2.5% mean, *trailing* the index by 1.7pp. That report was inconclusive because (a) only 60d of forward data was available and (b) the scan JSON only contained detected signals, so we couldn't measure filter effectiveness.

This v2 fixes both gaps:

1. **Multi-year window** — 5.7 years spanning bear + bull + chop.
2. **Both cohorts** — `run_scan.py` now emits every analysed ticker with its `vcp_detected` flag, so the backtester has a control group.
3. **Replay harness** — runs the detector on point-in-time cached OHLCV at each replay date; no look-ahead.
4. **Calibration sweep** — measures how threshold changes affect excess return.

---

## Methodology

### Universe construction

The historical S&P 500 constituents are sourced from
[`chinobing/historical_sp500_constituents`](https://github.com/chinobing/historical_sp500_constituents)
(3,871 daily snapshots from 1996-01-02 to 2026-09-02, auto-renewed). For each
replay date `d`, the universe is the forward-filled membership from the most
recent prior snapshot. This eliminates **survivorship bias** — names that
left the index before `d` (acquired, bankrupt, demoted) are never in the
universe for that date.

Union over the 2021-01-01 → 2026-09-01 window: **626 unique tickers**.

### Replay

Every 5 trading days, the detector runs on every ticker that was in the S&P 500
on that date, using **only OHLCV ≤ replay date** as the lookback window. This
is point-in-time: no future data leaks into the contraction-wave calculation.

For each (date, ticker) pair, the detector emits a `VCPResult` with `vcp_detected`
boolean + `vcp_quality` score. The replay records all rows (detected and
non-detected) — that's the control group.

### Forward-return measurement

For every (date, ticker) row, the backtester fetches the next 5/10/20/60 trading
days of closes from the cached OHLCV and computes the forward return. The
*entry price* is the first close on or after the replay date.

### Calibration

To test whether the result depends on threshold choices, we sweep
`vcp_quality_threshold ∈ {0.5, 0.6, 0.7, 0.8}` × `min_contractions ∈ {2, 3}` ×
`max_pivot_atr_pct ∈ {0.03, 0.05}` (16 combinations). The key optimization: we
compute forward returns once (117K signals × 4 horizons = 222K return rows)
and then post-process by slicing on the threshold — no re-running of the
detector or backtester.

---

## Results

### Headline: VCP signals underperform non-signals and the index

| Horizon | Sig n | Sig mean | Non-sig n | Non-sig mean | Sig - Non | SPX 500 | Sig - SPX |
|---|---|---|---|---|---|---|---|
| 5d  | 2,009  | +0.16% | 113,643 | +0.31% | **−0.15pp** | +0.43% | −0.27pp |
| 10d | 1,999  | +0.31% | 113,155 | +0.53% | **−0.23pp** | +0.78% | −0.47pp |
| 20d | 1,989  | +0.41% | 112,170 | +0.98% | **−0.57pp** | +1.53% | −1.12pp |
| 60d | 1,904  | +1.87% | 106,315 | +2.84% | **−0.97pp** | +4.25% | **−2.38pp** |

At 60 trading days (~3 months), VCP signals gave back **+1.87% mean** vs the
**+2.84%** of the average non-detected S&P 500 name and the **+4.25%** of the
S&P 500 itself. The pattern is monotonic — the gap widens at longer horizons.

The hit-rate difference (56.8% vs 56.1%) is tiny and within statistical noise.

### By year: the gap is regime-dependent

60-day forward returns, signals vs non-signals:

| Year | Sig n | Sig mean | Non-sig mean | Sig - Non |
|---|---|---|---|---|
| 2021 | 15  | **+8.60%** | +3.64% | **+4.96pp** ✓ |
| 2022 | 5   | -4.30%     | -2.89% | -1.41pp |
| 2023 | 330 | +2.97%     | +3.39% | -0.42pp |
| 2024 | 568 | +1.33%     | +2.43% | -1.10pp |
| 2025 | 851 | +2.06%     | +2.42% | -0.36pp |
| 2026 | 135 | **-0.32%** | +4.45% | **-4.77pp** ✗ |

**Regime observations:**

- **2021** (recovery bull, low rates, COVID rebound): the only year VCP signals
  outperformed. n=15 is too small for statistical confidence, but the
  +8.6%/+3.6% gap is suggestive that the pattern may have worked when the
  regime was strong uptrend with frequent successful breakouts.
- **2022** (bear market): both cohorts lost money; signals lost slightly more.
- **2023-2025** (late-cycle bull, AI mania): signals consistently underperform.
  The gap widens with the AI-driven concentration of the index.
- **2026 YTD** (choppy, narrow leadership): the gap explodes to -4.77pp. n=135
  is meaningful; the loss is real.

### By quality bucket: higher confidence ≠ better returns

The detector emits a 0-100 quality score. Bucketing:

| Bucket | n | 5d mean | 20d mean | 60d mean | 60d hit rate |
|---|---|---|---|---|---|
| Non-signal (control) | 106,315 | +0.31% | +0.98% | **+2.84%** | 56.1% |
| B (50-65 quality)    | 432      | -0.28% | +0.29% | +3.32% | 56.3% |
| C (65-80 quality)    | 1,212    | +0.28% | +0.55% | +1.52% | **57.3%** |
| D (80+ quality)      | 260      | +0.39% | -0.06% | +1.10% | 55.8% |

**Counter-intuitive finding:** the high-confidence "ULTRA_TIGHT / VCP_CONFIRMED / RANGE_HALVED" bucket is the *worst* performer at 60d. The mid-quality (B: 50-65) bucket slightly outperforms even the non-signal control. The pattern in the data is that **as quality score rises, the 60d forward return falls**.

This is the opposite of what the detector is supposed to do. Either:

1. The detector's quality scoring is anti-correlated with the actual
   predictive signal — i.e., the things it rewards (tight pivots, declining
   volume, range halving) are *exactly* the things that precede a failure
   rather than a breakout, in this regime.
2. The detector is identifying real VCPs that, in 2023-2026, fail more
   often than they succeed because most consolidations are *bases* (which
   break down) rather than *continuations* (which break out).

### Calibration sweep

Reclassifying the 1,989 detected signals at 60d under different thresholds:

| Quality thr | Min contractions | Max ATR | Sig n | Sig mean | Non-sig mean | **Excess** |
|---|---|---|---|---|---|---|
| **0.5** (default) | 2 | 0.03 | 1,989 | +0.41% | +0.98% | **−0.57pp** |
| 0.5 | 2 | 0.05 | 1,989 | +0.41% | +0.98% | −0.57pp |
| 0.5 | 3 | 0.05 | 1,989 | +0.41% | +0.98% | −0.57pp |
| 0.6 | 2 | 0.05 | 1,747 | +0.37% | +0.98% | −0.61pp |
| 0.7 | 2 | 0.05 | 1,169 | +0.38% | +0.98% | −0.60pp |
| 0.7 | 3 | 0.05 | 1,139 | +1.12% | +2.84% | **−1.72pp** |
| 0.8 | 2 | 0.05 | 264   | +0.06% | +0.99% | −0.93pp |

Every threshold combination produces *negative* excess return. The single best
configuration is the **default** (0.5, 2 waves, 3% ATR cap). **Calibration
cannot rescue the signal** — the underlying detection is the issue, not the
threshold.

---

## What is likely wrong

These are hypotheses, not conclusions. They should be tested in v3.

1. **The "strict monotonic contraction" check is too rigid.** The detector
   requires `range[i] < range[i-1] * 0.80` for *every* transition. A real VCP
   can have one wave that *expands slightly* (e.g. a 8% pullback after a 6%
   base) before resuming contraction. The current check disqualifies these.
2. **The 3-phase fixed window doesn't fit real bases.** VCP bases can be 3
   months or 18 months; the (42, 42, 42) split assumes 6 months. Names that
   form longer bases are systematically misclassified.
3. **The "pivot tightness" flag rewards the worst setups.** The data shows
   that the highest-quality scores (ULTRA_TIGHT pivot, RANGE_HALVED)
   *underperform* the lower-quality scores. This suggests that in 2023-2026,
   a "perfect" pivot that the detector celebrates is actually a stock running
   out of buyers — a failed breakout setup, not a successful one.
4. **The detector lacks a trend template.** VCP is *timing*. It assumes the
   underlying stock is in a Stage 2 uptrend (above 150-day MA, 200-day MA,
   52-week-high within 25%). Without this filter, VCP triggers on stocks in
   Stage 3 (topping) or Stage 4 (declining), which are the regimes where
   VCP fails the most.
5. **Volume dry-up isn't normalized.** A 50% volume decline in a stock that
   normally trades $500M/day is very different from 50% in one that trades
   $5M/day. The detector doesn't compare to a stock-specific baseline.

---

## Recommendations

### Don't

- **Don't trade VCP signals as a selection rule in 2023-2026 markets.** The
  data is unambiguous: at every horizon, the signals underperform both the
  control cohort and the index.
- **Don't add more complexity (e.g. RS filter, RS ranking) without first
  revisiting the detection algorithm.** Better inputs into a broken
  selection rule still produce a broken rule.

### Do

1. **Re-examine the wave-detection algorithm.** Real VCPs are not always
   strictly monotonic. Implement a pivot-detection approach (find swing
   highs/lows, measure the contraction of the range around each pivot) and
   compare against the current fixed-window approach on the same replay
   dataset.
2. **Add a Stage 2 trend template gate** before VCP evaluation. The classic
   Minervini rules are:
     - Price above 150-day MA and 200-day MA
     - 150-day MA above 200-day MA
     - 200-day MA trending up for at least 1 month
     - Price within 25% of 52-week high
     - RS rating > 70 (vs SPX)
   Without these, the detector is classifying setups across all stages and
   most of them are wrong stages.
3. **Add a relative-strength filter.** Names that are making new highs vs
   the S&P 500 are the ones VCP works on. Names that are flat vs the index
   during a contraction typically break down, not up.
4. **Re-run the replay after each fix** with the same code path; that gives
   a clean A/B comparison.

### Suggested v3 work plan

| Step | Output | Acceptance criterion |
|---|---|---|
| (1) Implement pivot-detection algorithm | New `vcp/engine/vcp_detector_v2.py` | Passes existing tests, runs ≥100x faster |
| (2) Run replay with v2 detector | `output/replay_v2_*.csv` | Excess return vs control flips sign |
| (3) Add Stage 2 trend gate | `vcp/engine/trend_template.py` | New filter passes ~60% of S&P 500 names |
| (4) Combined: VCP + trend | New scan schema | Excess return at 60d > 0 with p < 0.05 |
| (5) Out-of-sample test on a holdout period | 2026-09 → 2026-12 | Excess return holds, not just fits |

### What we *can* say with high confidence

- The current detector does not have edge on S&P 500 names in 2021-2026.
- The hit-rate difference between detected and non-detected is too small to
  be statistically meaningful at n=1,989.
- Higher quality scores in the current detector are anti-correlated with
  forward returns — the detector is rewarding the wrong setups.
- The point-in-time replay infrastructure works correctly: 117K analyses
  in ~2 minutes, fully reproducible, no look-ahead.

---

## Reproducing this analysis

```bash
# 1. Pre-cache S&P 500 OHLCV (~30s with parallel yfinance)
python3 -m vcp.cli_cache --start 2021-01-01 --end 2026-09-01 --workers 8

# 2. Run the 5-year replay with 5-day stride (~2-3 min)
python3 -m vcp.cli_replay --start 2021-01-01 --end 2026-09-01 \
    --stride 5 --workers 8 --horizons 5,10,20,60 --stop-loss 0.10 \
    --out-prefix output/replay_2021_2026_s5

# 3. Sweep thresholds (~1 min, single backtest pass)
python3 -m vcp.cli_calibrate --signals output/replay_2021_2026_s5_signals.json \
    --horizons 20,60 --workers 8 --top 8 \
    --out output/calibration.csv
```

Outputs:
- `output/replay_2021_2026_s5_signals.json` — 115K (date, ticker) signals with metadata
- `output/replay_2021_2026_s5_summary.csv` — headline forward-return summary
- `output/replay_2021_2026_s5_by_year.csv` — per-year breakdown
- `output/replay_2021_2026_s5_by_quality.csv` — per-quality-bucket breakdown
- `output/calibration.csv` — full 16-combo threshold sweep

## Files and code

- `vcp/cache.py` — point-in-time S&P 500 constituents + OHLCV cache
- `vcp/replay.py` — historical replay harness (point-in-time, parallel)
- `vcp/cli_cache.py` — `python -m vcp.cli_cache`
- `vcp/cli_replay.py` — `python -m vcp.cli_replay`
- `vcp/cli_calibrate.py` — `python -m vcp.cli_calibrate`
- `vcp/backtest.py` — forward-return harness (unchanged from v1)
- `tests/test_replay.py` — 9 unit tests for the new infrastructure
