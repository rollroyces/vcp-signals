"""VCP Configuration"""
from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class VCPConfig:
    """VCP detection parameters."""
    # Wave detection
    wave_min_days: int = 15
    wave_max_days: int = 120
    contraction_min_pct: float = 0.05
    
    # Quality thresholds
    quality_threshold: float = 0.50
    min_contractions: int = 2
    
    # Volume
    volume_dry_up_min: float = 0.30
    volume_confirm_ratio: float = 0.70
    
    # Pivot
    pivot_max_days_since: int = 10
    pivot_volatility_threshold: float = 0.03

VC = VCPConfig()
