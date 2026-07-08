"""
Pace gate pre-registered constants (WORK_ORDER §2.2).
Do not modify f(t) table until §7 calibration procedure.
"""

from __future__ import annotations

from typing import List, Tuple

# (KST HH:MM, cumulative day-fraction f(t))
PACE_PROFILE_KST: List[Tuple[str, float]] = [
    ("09:10", 0.08),
    ("09:30", 0.16),
    ("10:00", 0.26),
    ("10:30", 0.33),
    ("11:00", 0.39),
    ("11:30", 0.44),
    ("12:00", 0.48),
    ("12:30", 0.52),
    ("13:00", 0.56),
    ("13:30", 0.60),
    ("14:00", 0.66),
    ("14:30", 0.74),
    ("15:00", 0.84),
    ("15:20", 1.00),
]
