#!/usr/bin/env python3
"""Test configuration and shared fixtures for vcp-signals."""
from __future__ import annotations

import sys
from pathlib import Path

# Make repo root importable regardless of pytest invocation directory.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import numpy as np
import pandas as pd
import pytest


def make_vcp_history(
    n: int = 180,
    phase_ranges: tuple[float, ...] = (0.06, 0.03, 0.012),
    phase_lens: tuple[int, ...] = (60, 60, 60),
    drift: float = 0.001,
    volume_decay: float = 0.45,
    seed: int = 42,
) -> pd.DataFrame:
    """Synthesize a price series that exhibits N contraction waves.

    Each phase has a fixed oscillation band; volume declines in the final phase
    by ``volume_decay`` (< 1.0 means quieter last phase). Mirrors what the
    detector expects to see in a real VCP.
    """
    rng = np.random.default_rng(seed)
    assert sum(phase_lens) == n, "phase lengths must sum to n"
    prices = np.zeros(n)
    base = 100.0
    boundaries = np.cumsum(phase_lens)
    for i in range(n):
        phase_idx = int(np.searchsorted(boundaries, i, side="right"))
        band = phase_ranges[min(phase_idx, len(phase_ranges) - 1)]
        base = base * (1 + drift * 0.5 ** phase_idx)
        osc = rng.normal(0, band * 0.4)
        prices[i] = base * (1 + np.clip(osc, -band, band))
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
    close = pd.Series(prices, index=dates)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    vol = pd.Series(np.exp(rng.normal(15, 0.3, n)), index=dates)
    last_phase_start = n - phase_lens[-1]
    vol.iloc[last_phase_start:] *= volume_decay
    return pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=dates)


@pytest.fixture
def vcp_history() -> pd.DataFrame:
    return make_vcp_history()


@pytest.fixture
def small_vcp_history() -> pd.DataFrame:
    # Shorter history that still has a visible contraction.
    return make_vcp_history(n=126, phase_ranges=(0.08, 0.04, 0.02), phase_lens=(42, 42, 42))
