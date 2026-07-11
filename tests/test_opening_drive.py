from core.api_client import Quote
from core.opening_drive import OpeningDriveStrategy


def _q(price: int, open_price: int, cum_value: int = 0) -> Quote:
    return Quote(
        symbol="005930",
        current_price=price,
        open_price=open_price,
        volume=0,
        cum_value=cum_value,
        prev_high=0,
        prev_low=0,
    )


def test_opening_drive_gap_and_break():
    s = OpeningDriveStrategy(
        gap_min=0.015,
        gap_max=0.03,
        observe_end_hhmm="09:30",
        stop_pct=0.02,
        trail_pct=0.02,
        force_exit_hhmm="11:00",
        min_pace_ratio=0.0,  # disable volume filter for unit test
    )
    s.register("005930", prev_close=100_000)

    # Gap +2%: open=102000
    assert s.on_quote(_q(102500, 102_000), now_hhmm="09:10", value_ma5=1) == (None, None)
    assert s.on_quote(_q(103000, 102_000), now_hhmm="09:20", value_ma5=1) == (None, None)
    # Observe ends; high=103000
    assert s.on_quote(_q(102800, 102_000), now_hhmm="09:30", value_ma5=1) == (None, None)
    # Break observe high
    entry, exit_ = s.on_quote(_q(103100, 102_000), now_hhmm="09:35", value_ma5=1)
    assert exit_ is None
    assert entry is not None
    assert entry.trigger_price == 103000
    s.confirm_entry("005930", entry.entry_price)

    # Stop -2%
    _, exit_ = s.on_quote(_q(101000, 102_000), now_hhmm="10:00", value_ma5=1)
    assert exit_ is not None
    assert exit_.reason == "STOP"


def test_opening_drive_rejects_bad_gap():
    s = OpeningDriveStrategy(gap_min=0.015, gap_max=0.03, min_pace_ratio=0.0)
    s.register("005930", prev_close=100_000)
    # Gap +5% too large
    entry, exit_ = s.on_quote(_q(105000, 105_000), now_hhmm="09:10", value_ma5=1)
    assert entry is None and exit_ is None
    st = s.symbol_state["005930"]
    assert st.rejected
    assert st.reject_reason == "GAP_FILTER"
