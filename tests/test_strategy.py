from core.api_client import Quote
from core.strategy import BREAKOUT_MODE_K_RANGE, BREAKOUT_MODE_PREV_HIGH, VolatilityBreakoutStrategy


def _quote(price: int, volume: int = 0, open_price: int = 69000) -> Quote:
    return Quote(
        symbol="005930",
        current_price=price,
        open_price=open_price,
        volume=volume,
        cum_value=0,
        prev_high=70500,
        prev_low=68000,
    )


def test_signal_on_breakout_regardless_of_volume():
    """페이스 게이트 재설계: 전략은 가격 돌파만 판정한다 (거래량 조건 없음)."""
    s = VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_K_RANGE)
    # breakout = open(69000) + (70500-68000) * 0.4 = 70000
    s.register("005930", prev_high=70500, prev_low=68000, breakout_k=0.4)

    assert s.on_quote(_quote(price=69500, volume=0)) is None

    signal = s.on_quote(_quote(price=70000, volume=0))
    assert signal is not None
    assert signal.symbol == "005930"
    assert signal.breakout_price == 70000
    assert signal.reason == "pace_gate_breakout"


def test_signal_on_first_observation_above_breakout():
    """구 '첫 관측 A&B 동시충족 스킵' 규칙 제거: 첫 관측이 돌파가 위여도 신호.
    과추격 방지는 게이트의 CHASE_LIMIT이 담당한다."""
    s = VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_K_RANGE)
    s.register("005930", prev_high=70500, prev_low=68000, breakout_k=0.4)

    signal = s.on_quote(_quote(price=70500, volume=999999))
    assert signal is not None


def test_no_signal_after_confirm_entry():
    s = VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_K_RANGE)
    s.register("005930", prev_high=70500, prev_low=68000, breakout_k=0.4)

    assert s.on_quote(_quote(price=70000)) is not None
    s.confirm_entry("005930")
    assert s.on_quote(_quote(price=71000)) is None


def test_signal_repeats_until_confirmed():
    """게이트 미달로 진입하지 못한 신호는 다음 폴링에서 재평가된다."""
    s = VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_K_RANGE)
    s.register("005930", prev_high=70500, prev_low=68000, breakout_k=0.4)

    assert s.on_quote(_quote(price=70000)) is not None
    assert s.on_quote(_quote(price=70100)) is not None


def test_prev_high_breakout_mode():
    """전일 고가 모드: 돌파가=prev_high, 시가 불필요."""
    s = VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_PREV_HIGH)
    s.register("005930", prev_high=70500, prev_low=68000, breakout_k=0.7)

    assert s.on_quote(_quote(price=70400, open_price=0)) is None
    signal = s.on_quote(_quote(price=70500, open_price=0))
    assert signal is not None
    assert signal.breakout_price == 70500
    assert signal.reason == "prev_high_breakout"
