from core.api_client import Quote
from core.strategy import VolatilityBreakoutStrategy


def test_entry_only_when_a_then_b():
    s = VolatilityBreakoutStrategy()
    # breakout = open(69000) + (prev_high-prev_low=2500) * k(0.4) = 70000
    s.register("005930", avg_volume_5d=1000, prev_high=70500, prev_low=68000, breakout_k=0.4)

    # B 먼저 만족 (A 미충족): 진입 금지
    signal = s.on_quote(
        Quote(
            symbol="005930",
            current_price=71000,
            open_price=69000,
            volume=900,
            prev_high=70500,
            prev_low=68000,
        )
    )
    assert signal is None

    # A 충족
    signal = s.on_quote(
        Quote(
            symbol="005930",
            current_price=69500,
            open_price=69000,
            volume=1000,
            prev_high=70500,
            prev_low=68000,
        )
    )
    assert signal is None

    # A 이후 B 충족: 진입
    signal = s.on_quote(
        Quote(
            symbol="005930",
            current_price=70000,
            open_price=69000,
            volume=1100,
            prev_high=70500,
            prev_low=68000,
        )
    )
    assert signal is not None
    assert signal.symbol == "005930"


def test_skip_when_initial_quote_meets_a_and_b():
    s = VolatilityBreakoutStrategy()
    s.register("005930", avg_volume_5d=1000, prev_high=70500, prev_low=68000, breakout_k=0.4)

    # 첫 관측에서 A(volume)와 B(price>=breakout) 동시 충족이면 스킵
    signal = s.on_quote(
        Quote(
            symbol="005930",
            current_price=70500,
            open_price=69000,
            volume=2000,
            prev_high=70500,
            prev_low=68000,
        )
    )
    assert signal is None

    # 이후 조건이 계속 충족되어도 매수 시그널이 나오면 안 됨
    signal = s.on_quote(
        Quote(
            symbol="005930",
            current_price=71000,
            open_price=69000,
            volume=3000,
            prev_high=70500,
            prev_low=68000,
        )
    )
    assert signal is None
