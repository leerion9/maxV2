"""Unit tests for overnight VDU condensation scoring."""
from __future__ import annotations

from core.vdu_score import (
    score_atr_sq,
    score_bars,
    score_ma_conv,
    score_mfi_turn,
    score_obv_div,
    score_pocket_pivot,
    score_vdu,
    select_candidates,
    VduScoreBreakdown,
)
from core.vdu_score_logger import write_vdu_score_csv


def _bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _flat_series(n: int, close: int = 10000, vol: int = 1000):
    # Slight noise so ATR / MFI are defined.
    bars = []
    for i in range(n):
        c = close + (i % 3) - 1
        bars.append(_bar(c - 10, c + 20, c - 20, c, vol))
    return bars


def test_score_vdu_dryup():
    bars = _flat_series(25, vol=1000)
    # Last 20: mostly high volume, last 5 collapse.
    for i in range(len(bars) - 20, len(bars) - 5):
        bars[i]["volume"] = 5000
    for i in range(len(bars) - 5, len(bars)):
        bars[i]["volume"] = 100
    assert score_vdu(bars) == 25


def test_score_vdu_no_dryup():
    bars = _flat_series(25, vol=2000)
    assert score_vdu(bars) == 0


def test_score_ma_conv_tight():
    bars = []
    price = 10000
    for i in range(70):
        # Very tight range around flat so MAs converge.
        c = price + (1 if i % 2 == 0 else -1)
        bars.append(_bar(c, c + 5, c - 5, c, 1000))
    assert score_ma_conv(bars) == 15


def test_score_pocket_pivot():
    bars = _flat_series(15, vol=1000)
    # Make prior 10 days mostly down with max vol 2000.
    for i in range(len(bars) - 11, len(bars) - 1):
        bars[i]["open"] = 10100
        bars[i]["close"] = 9900
        bars[i]["volume"] = 1500 + (i % 5) * 100
    # Today up with bigger volume than any down day.
    bars[-1]["open"] = 9900
    bars[-1]["close"] = 10200
    bars[-1]["high"] = 10300
    bars[-1]["low"] = 9800
    bars[-1]["volume"] = 5000
    assert score_pocket_pivot(bars) == 15


def test_score_obv_div_quiet_price_strong_obv():
    bars = []
    for i in range(25):
        # Price almost flat, but volume on up ticks builds OBV.
        if i % 2 == 0:
            bars.append(_bar(10000, 10050, 9950, 10020, 5000))
        else:
            bars.append(_bar(10020, 10040, 9980, 10000, 500))
    assert score_obv_div(bars) == 15


def test_select_candidates_respects_cutoff_and_cap():
    scored = {
        "A": VduScoreBreakdown(25, 20, 15, 15, 0, 0),  # 75
        "B": VduScoreBreakdown(25, 20, 0, 0, 0, 0),  # 45
        "C": VduScoreBreakdown(25, 20, 15, 15, 15, 10),  # 100
    }
    pool = select_candidates(
        scored, score_min=70, max_candidates=2, symbol_order=["A", "B", "C"]
    )
    assert pool == ["C", "A"]


def test_score_bars_short_history_zero():
    bd = score_bars(_flat_series(10))
    assert bd.total == 0


def test_write_vdu_score_csv(tmp_path):
    scored = {
        "005930": VduScoreBreakdown(25, 20, 15, 0, 0, 10),
        "000660": VduScoreBreakdown(0, 0, 0, 0, 0, 0),
    }
    path = write_vdu_score_csv(
        log_dir=tmp_path,
        ymd="20260712",
        scored=scored,
        pool=["005930"],
        symbol_order=["005930", "000660"],
    )
    text = path.read_text(encoding="utf-8-sig")
    assert "total_score" in text
    assert "005930" in text
    assert "true" in text
    assert "false" in text


def test_atr_sq_and_mfi_smoke():
    # Shrinking ranges late in series.
    bars = []
    for i in range(80):
        width = 500 if i < 50 else 30
        c = 10000
        bars.append(_bar(c, c + width, c - width, c, 1000))
    # Not asserting exact ATR pass (percentile sensitive); just no crash + int score.
    assert score_atr_sq(bars) in (0, 20)
    assert score_mfi_turn(bars) in (0, 10)
