"""VCP Configuration — canonical source lives in :mod:`vcp.engine.vcp_detector`.

This module exists for backwards compatibility; new code should import
``VC`` (the singleton :class:`vcp.engine.vcp_detector.VCPConfig`) from
``vcp.engine.vcp_detector``.
"""
from vcp.engine.vcp_detector import VC, VCPConfig

__all__ = ["VC", "VCPConfig"]
