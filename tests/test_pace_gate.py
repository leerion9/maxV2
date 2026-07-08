from core.pace_gate import entry_block_reason, evaluate_pace_gate, interpolate_f


def test_interpolate_f_at_knots():
    assert interpolate_f("09:10") == 0.08
    assert interpolate_f("15:20") == 1.0


def test_interpolate_f_midpoint():
    # 09:10=0.08, 09:30=0.16 -> 09:20 midpoint = 0.12
    assert abs(interpolate_f("09:20") - 0.12) < 1e-9


def test_interpolate_f_before_first():
    assert interpolate_f("09:00") == 0.08


def test_interpolate_f_after_last():
    assert interpolate_f("15:30") == 1.0


def test_pace_gate_pass_at_threshold():
    gate = evaluate_pace_gate(
        cum_value=300_000_000,
        value_ma5=100_000_000,
        now_hhmm="10:00",
        current_price=49_000,
        breakout_price=49_000,
        prev_close=45_000,
        pace_threshold=3.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="15:20",
        chase_limit_mult=1.02,
        upper_limit_mult=1.25,
    )
    # f(10:00)=0.26 -> projected ~1.15B -> pace_ratio ~11.5
    assert gate.gate_pass is True
    assert gate.block_reason == ""


def test_pace_gate_too_early():
    gate = evaluate_pace_gate(
        cum_value=500_000_000,
        value_ma5=100_000_000,
        now_hhmm="09:05",
        current_price=50_000,
        breakout_price=49_000,
        prev_close=45_000,
        pace_threshold=3.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="15:20",
        chase_limit_mult=1.02,
        upper_limit_mult=1.25,
    )
    assert gate.gate_pass is False
    assert gate.block_reason == "TOO_EARLY"


def test_pace_gate_too_late():
    gate = evaluate_pace_gate(
        cum_value=500_000_000,
        value_ma5=100_000_000,
        now_hhmm="15:25",
        current_price=50_000,
        breakout_price=49_000,
        prev_close=45_000,
        pace_threshold=3.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="15:20",
        chase_limit_mult=1.02,
        upper_limit_mult=1.25,
    )
    assert gate.gate_pass is False
    assert gate.block_reason == "TOO_LATE"


def test_pace_gate_value_ma5_zero_defense():
    gate = evaluate_pace_gate(
        cum_value=500_000_000,
        value_ma5=0,
        now_hhmm="10:00",
        current_price=49_000,
        breakout_price=49_000,
        prev_close=45_000,
        pace_threshold=3.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="15:20",
        chase_limit_mult=1.02,
        upper_limit_mult=1.25,
    )
    assert gate.pace_ratio == 0.0
    assert gate.gate_pass is False
    assert gate.block_reason == "PACE_FAIL"


def _passing_gate():
    return evaluate_pace_gate(
        cum_value=300_000_000,
        value_ma5=100_000_000,
        now_hhmm="10:00",
        current_price=49_000,
        breakout_price=49_000,
        prev_close=45_000,
        pace_threshold=3.0,
        entry_start_hhmm="09:10",
        entry_end_hhmm="15:20",
        chase_limit_mult=1.02,
        upper_limit_mult=1.25,
    )


def test_entry_block_reason_no_pace_fail_pollution():
    """게이트 통과 + 진입 가능이면 빈 사유. PACE_FAIL 날조 금지 (검수 이슈 3)."""
    gate = _passing_gate()
    assert gate.gate_pass is True
    assert (
        entry_block_reason(gate=gate, already_ordered=False, full_cap=False, high_price=False)
        == ""
    )


def test_entry_block_reason_priority():
    gate = _passing_gate()
    assert (
        entry_block_reason(gate=gate, already_ordered=True, full_cap=True, high_price=True)
        == "ALREADY_ORDERED"
    )
    assert (
        entry_block_reason(gate=gate, already_ordered=False, full_cap=True, high_price=True)
        == "FULL_CAP"
    )
    assert (
        entry_block_reason(gate=gate, already_ordered=False, full_cap=False, high_price=True)
        == "HIGH_PRICE"
    )
