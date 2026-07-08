from __future__ import annotations

from dataclasses import dataclass

from config.pace_constants import PACE_PROFILE_KST


def _hhmm_to_minutes(hhmm: str) -> int:
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM: {hhmm!r}")
    h, m = int(parts[0]), int(parts[1])
    return h * 60 + m


_PROFILE_MINUTES = [(_hhmm_to_minutes(t), f) for t, f in PACE_PROFILE_KST]


def interpolate_f(hhmm: str) -> float:
    """Linear interpolation of f(t) between pre-registered KST profile knots."""
    t = _hhmm_to_minutes(hhmm)
    if t <= _PROFILE_MINUTES[0][0]:
        return _PROFILE_MINUTES[0][1]
    if t >= _PROFILE_MINUTES[-1][0]:
        return _PROFILE_MINUTES[-1][1]
    for i in range(len(_PROFILE_MINUTES) - 1):
        t0, f0 = _PROFILE_MINUTES[i]
        t1, f1 = _PROFILE_MINUTES[i + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return f0
            ratio = (t - t0) / (t1 - t0)
            return f0 + ratio * (f1 - f0)
    return _PROFILE_MINUTES[-1][1]


@dataclass(frozen=True)
class PaceGateEval:
    f_t: float
    projected_value: float
    pace_ratio: float
    gate_pass: bool
    block_reason: str


def evaluate_pace_gate(
    *,
    cum_value: int,
    value_ma5: int,
    now_hhmm: str,
    current_price: int,
    breakout_price: int,
    prev_close: int,
    pace_threshold: float,
    entry_start_hhmm: str,
    entry_end_hhmm: str,
    chase_limit_mult: float,
    upper_limit_mult: float,
) -> PaceGateEval:
    f_t = interpolate_f(now_hhmm)
    if f_t <= 0:
        projected = 0.0
        pace_ratio = 0.0
    else:
        projected = float(cum_value) / f_t
        if value_ma5 <= 0:
            pace_ratio = 0.0
        else:
            pace_ratio = projected / float(value_ma5)

    gate_pass = pace_ratio >= pace_threshold

    block_reason = ""
    t_min = _hhmm_to_minutes(now_hhmm)
    if t_min < _hhmm_to_minutes(entry_start_hhmm):
        gate_pass = False
        block_reason = "TOO_EARLY"
    elif t_min > _hhmm_to_minutes(entry_end_hhmm):
        gate_pass = False
        block_reason = "TOO_LATE"
    elif prev_close > 0 and current_price >= int(prev_close * upper_limit_mult):
        gate_pass = False
        block_reason = "NEAR_UPPER_LIMIT"
    elif breakout_price > 0 and current_price > int(breakout_price * chase_limit_mult):
        gate_pass = False
        block_reason = "CHASE_LIMIT"
    elif pace_ratio < pace_threshold:
        gate_pass = False
        if not block_reason:
            block_reason = "PACE_FAIL"

    if gate_pass:
        block_reason = ""

    return PaceGateEval(
        f_t=f_t,
        projected_value=projected,
        pace_ratio=pace_ratio,
        gate_pass=gate_pass,
        block_reason=block_reason,
    )


def entry_block_reason(
    *,
    gate: PaceGateEval,
    already_ordered: bool,
    full_cap: bool,
    high_price: bool,
) -> str:
    """
    Resolve final block_reason for gate CSV when breakout reached.
    Priority: already ordered today > gate verdict > portfolio caps.
    Never fabricates PACE_FAIL when the gate actually passed.
    """
    if already_ordered:
        return "ALREADY_ORDERED"
    if gate.block_reason:
        return gate.block_reason
    if full_cap:
        return "FULL_CAP"
    if high_price:
        return "HIGH_PRICE"
    return ""
