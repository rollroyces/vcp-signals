# VCP Signals — Initial Validation Report

**Date:** 2026-09-02
**Author:** Hermes (quantitative review)
**Scope:** Forward-return validation of the vcp-signals scanner against the only dataset we have enough forward history for: the 94 S&P-500 signals in `vcp_signals_latest.json` (signal date 2026-05-20, ~110 calendar days elapsed since).

---

## TL;DR

The scanner has been producing daily JSON for ~2 months. **Its forward-return edge has not been validated.** In the one window where we have ~60 trading days of forward data, the 94-ticker VCP set returned **+2.5% mean / +0.6% median / 56.5% win rate**, **trailing the S&P 500 by ~1.7 percentage points** over the same window. There is no non-signal comparison group in the current output schema, so we cannot measure filter effectiveness vs. a random S&P 500 sample.

**Verdict:** No statistical evidence of alpha yet. The next step is to (a) extend the JSON schema to include the non-passing cohort, (b) replay the detector against historical OHLCV with a structured backtest, and (c) only after those produce evidence of edge, tune the quality threshold / wave windows.

---

## What was measured

94 tickers, all dated 2026-05-20 09:30 UTC, all `is_signal: true` in the legacy schema.

Forward returns at 5 / 10 / 20 / 60 trading days, computed via `vcp/backtest.py` against yfinance OHLCV. Entry = first close on or after the signal date. No stop, no sizing.

| Horizon | n   | mean    | median  | stdev  | hit-rate | min      | max      |
|---------|-----|---------|---------|--------|----------|----------|----------|
| 5d      | 93  | +2.01%  | -0.61%  | 10.23% | 44.1%    | -9.37%   | +66.50%  |
| 10d     | 93  | +1.12%  | -0.64%  | 10.11% | 45.2%    | -21.26%  | +56.01%  |
| 20d     | 93  | +0.95%  | -1.09%  | 15.00% | 43.0%    | -21.85%  | +65.63%  |
| 60d     | 92  | **+2.52%** | **+0.57%** | 17.02% | **56.5%** | -45.05%  | +85.38%  |
| 110d    | 0   | —       | —       | —      | —        | —        | —        |  *(insufficient forward history)* |

One ticker (`CTRA`) was delisted / not found on Yahoo; 92 of 94 produced 60d returns. 1 ticker failed 60d due to data gaps.

Simulated equal-weight portfolio with a 10% stop-loss:

| Horizon | n  | mean per signal | total compounded | max DD   | win rate |
|---------|----|-----------------|------------------|----------|----------|
| 5d      | 93 | +2.01%          | +329.8%          | -24.1%   | 44.1%    |
| 10d     | 93 | +1.12%          | +122.5%          | -28.7%   | 45.2%    |
| 20d     | 93 | +0.95%          | +126.6%          | -44.7%   | 43.0%    |
| 60d     | 92 | +2.52%          | +1501.1%         | -35.3%   | 56.5%    |

(The compounded totals are arithmetic artifacts of equal-weighting across 90+ names; **the only honest number is per-signal mean and the win rate**, both of which are barely positive at 60d.)

## Benchmark: S&P 500 over the same window

```
^GSPC 2026-05-20 close: 7432.97
^GSPC 2026-05-20 + 60 trading days: 7745.06  →  +4.20%
^GSPC 2026-09-02 close: 7631.47  →  +2.67% (only 109 trading days elapsed since signal)
```

So at 60d the VCP set returned **+2.52% mean / +0.57% median vs. +4.20% for the index**. The cohort underperformed the index by ~1.7pp at the same horizon. Hit rate at 56.5% is only 6.5pp above coin-flip.

## What we *cannot* conclude

- **Filter effectiveness.** `vcp_signals_latest.json` only contains the 94 tickers that the old engine flagged as signals. We have no comparison cohort of S&P 500 names that *failed* the VCP filter on the same date. To measure whether the filter adds edge over "buy the S&P 500," we need the scanner to emit both groups, or we need to replay the detector over the full S&P 500 OHLCV and recompute.
- **Win rate stability.** 92 observations is not enough for any claim about hit-rate. The 95% binomial confidence interval on 56.5% is roughly ±10pp — i.e. anywhere from 46% to 67%.
- **Long-horizon behaviour.** The 110d horizon returned 0 outcomes because today is 2026-09-02, exactly ~110 calendar days post-signal. To check the classic "VC breakout in 1-3 months" claim we need to wait or replay historical data.

## What the validation framework does NOT measure yet

- **Stage-2 trend template** (price > 150-day MA, 200-day MA, etc). VCP is a *timing* filter, not a *selection* filter; without a separate trend gate, the cohort is contaminated by names that are mid-decline.
- **Relative strength** vs. the index. The base requirement should probably include RS > 0 over the lookback window.
- **Volume dry-up quality.** The detector counts volume declining across waves but does not test for an *expansion* on the breakout bar, which is what Minervini's setup actually requires.
- **Pivot breakout event.** We measure forward return from `signal_date` (a scan date), not from the actual breakout close. This is fine for scan-date entries but understates the true edge of a pivot-confirmed entry.

## Recommended next steps (in order)

1. **Schema fix** — change `run_scan.py` to emit **all scanned tickers with their vcp_detected flag**, not just the detected ones. This is the single most important fix; without it we cannot measure filter effectiveness.
2. **Historical replay** — run the detector over S&P 500 OHLCV from a multi-year window (e.g. 2020-2025), generate signals at each daily scan point, then measure forward returns. ~1 year of code work for the replay harness if we cache OHLCV locally.
3. **Calibrate** — once we have replay data, sweep the `VCPConfig` parameters (`min_contractions`, `contraction_ratio_threshold`, `vcp_quality_threshold`, `max_pivot_atr_pct`) for the best risk-adjusted forward return. Today's defaults are not data-calibrated.
4. **Production hardening** — see `/Users/hermes/vcp-signals` issues list:
   - hardcoded `/home/node/.openclaw/workspace/vmaa/data` in `data/loader.py`
   - imports of nonexistent `data.yahoo_direct`
   - duplicate `VCPConfig` in `engine/config.py`
   - no tests, no CI, no ruff/mypy

## Caveats and how to read these numbers

- The "94 signals" were generated by the **legacy engine**, not the current `vcp_detector.py`. The new engine may produce a different cohort — but until we replay both against the same OHLCV, we don't know.
- The 60d window (~May–Sep 2026) overlaps a strong S&P 500 advance. A mean-reverting sample in a strong tape can look OK on absolute terms and still be alpha-negative on risk-adjusted terms.
- The 5d / 10d / 20d hit rates (43-45%) being *below* 50% is the most concerning single observation. If the filter is supposed to find imminent breakouts, the first 5-20 days should show positive mean and >50% hit rate. They don't.
