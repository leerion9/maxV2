"""Overnight condensation score (VDU / VCP-style) for the vdu_score paper arm.

Bars must be chronological ascending (oldest -> newest). Last bar = latest
closed session (D-1 when scored before/at open of day D).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence


Bar = Dict[str, int]  # open?, high, low, close, volume


@dataclass(frozen=True)
class VduScoreBreakdown:
    vdu: int
    atr_sq: int
    ma_conv: int
    pp: int
    obv_div: int
    mfi_turn: int

    @property
    def total(self) -> int:
        return (
            self.vdu
            + self.atr_sq
            + self.ma_conv
            + self.pp
            + self.obv_div
            + self.mfi_turn
        )


def _closes(bars: Sequence[Bar]) -> List[float]:
    return [float(b["close"]) for b in bars]


def _volumes(bars: Sequence[Bar]) -> List[float]:
    return [float(b["volume"]) for b in bars]


def _sma(values: Sequence[float], n: int) -> float | None:
    if len(values) < n or n <= 0:
        return None
    window = values[-n:]
    return sum(window) / float(n)


def _true_ranges(bars: Sequence[Bar]) -> List[float]:
    out: List[float] = []
    prev_close: float | None = None
    for b in bars:
        high = float(b["high"])
        low = float(b["low"])
        close = float(b["close"])
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        out.append(tr)
        prev_close = close
    return out


def _atr(bars: Sequence[Bar], n: int = 14) -> float | None:
    trs = _true_ranges(bars)
    return _sma(trs, n)


def _obv_series(bars: Sequence[Bar]) -> List[float]:
    series: List[float] = []
    obv = 0.0
    prev_close: float | None = None
    for b in bars:
        close = float(b["close"])
        vol = float(b["volume"])
        if prev_close is None:
            obv = 0.0
        elif close > prev_close:
            obv += vol
        elif close < prev_close:
            obv -= vol
        series.append(obv)
        prev_close = close
    return series


def _typical_price(b: Bar) -> float:
    return (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0


def _mfi_series(bars: Sequence[Bar], n: int = 14) -> List[float | None]:
    """Money Flow Index; values before n bars are None."""
    out: List[float | None] = [None] * len(bars)
    if len(bars) < n + 1:
        return out
    raw_mf: List[float] = []
    prev_tp: float | None = None
    for b in bars:
        tp = _typical_price(b)
        rmf = tp * float(b["volume"])
        if prev_tp is None:
            raw_mf.append(0.0)
        elif tp > prev_tp:
            raw_mf.append(rmf)
        elif tp < prev_tp:
            raw_mf.append(-rmf)
        else:
            raw_mf.append(0.0)
        prev_tp = tp

    for i in range(n, len(bars)):
        window = raw_mf[i - n + 1 : i + 1]
        pos = sum(x for x in window if x > 0)
        neg = -sum(x for x in window if x < 0)
        if neg <= 0:
            out[i] = 100.0
        else:
            ratio = pos / neg
            out[i] = 100.0 - (100.0 / (1.0 + ratio))
    return out


def _is_up_day(bars: Sequence[Bar], i: int) -> bool:
    b = bars[i]
    if "open" in b and int(b["open"]) > 0:
        return int(b["close"]) > int(b["open"])
    if i <= 0:
        return False
    return float(b["close"]) > float(bars[i - 1]["close"])


def _is_down_day(bars: Sequence[Bar], i: int) -> bool:
    b = bars[i]
    if "open" in b and int(b["open"]) > 0:
        return int(b["close"]) < int(b["open"])
    if i <= 0:
        return False
    return float(b["close"]) < float(bars[i - 1]["close"])


def score_vdu(bars: Sequence[Bar]) -> int:
    if len(bars) < 20:
        return 0
    vols = _volumes(bars)
    ma5 = _sma(vols, 5)
    ma20 = _sma(vols, 20)
    if ma5 is None or ma20 is None or ma20 <= 0:
        return 0
    if ma5 / ma20 > 0.50:
        return 0
    last5 = vols[-5:]
    low20 = min(vols[-20:])
    if any(abs(v - low20) < 1e-9 for v in last5):
        return 25
    return 0


def score_atr_sq(bars: Sequence[Bar]) -> int:
    need = 14 + 5
    if len(bars) < need:
        return 0
    lookback = min(60, len(bars) - 14)
    if lookback < 10:
        return 0
    ratios: List[float] = []
    for end in range(len(bars) - lookback, len(bars) + 1):
        window = bars[:end]
        atr = _atr(window, 14)
        close = float(window[-1]["close"])
        if atr is None or close <= 0:
            continue
        ratios.append(atr / close)
    if len(ratios) < 10:
        return 0
    current = ratios[-1]
    ranked = sorted(ratios)
    cutoff = ranked[max(0, int(len(ranked) * 0.20) - 1)]
    return 20 if current <= cutoff else 0


def score_ma_conv(bars: Sequence[Bar]) -> int:
    closes = _closes(bars)
    if len(closes) < 60:
        return 0
    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    close = closes[-1]
    if None in (ma5, ma20, ma60) or close <= 0:
        return 0
    spread = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / close
    return 15 if spread <= 0.03 else 0


def score_pocket_pivot(bars: Sequence[Bar]) -> int:
    if len(bars) < 11:
        return 0
    i = len(bars) - 1
    if not _is_up_day(bars, i):
        return 0
    closes = _closes(bars)
    ma10 = _sma(closes, 10)
    if ma10 is None or closes[-1] < ma10:
        return 0
    down_vols = [
        float(bars[j]["volume"])
        for j in range(i - 10, i)
        if _is_down_day(bars, j)
    ]
    if not down_vols:
        return 0
    if float(bars[i]["volume"]) > max(down_vols):
        return 15
    return 0


def score_obv_div(bars: Sequence[Bar]) -> int:
    if len(bars) < 20:
        return 0
    window = bars[-20:]
    closes = _closes(window)
    obv = _obv_series(window)
    ret = closes[-1] / closes[0] - 1.0 if closes[0] > 0 else 0.0
    price_quiet = -0.05 <= ret <= 0.05
    price_ll = closes[-1] <= min(closes[:-1]) if len(closes) > 1 else False
    if not (price_quiet or price_ll):
        return 0
    obv_sorted = sorted(obv)
    p95 = obv_sorted[max(0, int(len(obv_sorted) * 0.95) - 1)]
    near_high = obv[-1] >= p95
    prior_peak = max(obv[:-1]) if len(obv) > 1 else obv[-1]
    broke_high = obv[-1] > prior_peak
    return 15 if (near_high or broke_high) else 0


def score_mfi_turn(bars: Sequence[Bar]) -> int:
    mfi = _mfi_series(bars, 14)
    if len(mfi) < 2:
        return 0
    last = mfi[-1]
    prev = mfi[-2]
    if last is None or prev is None:
        return 0
    # Within last 5 sessions: touched <=20, then lifted.
    recent = mfi[-5:]
    touched_oversold = any(x is not None and x <= 20.0 for x in recent)
    if not touched_oversold:
        return 0
    if last > prev + 5.0:
        return 10
    return 0


def score_bars(bars: Sequence[Bar]) -> VduScoreBreakdown:
    """Score one symbol from ascending OHLCV bars. Short history -> zeros."""
    if len(bars) < 20:
        return VduScoreBreakdown(0, 0, 0, 0, 0, 0)
    return VduScoreBreakdown(
        vdu=score_vdu(bars),
        atr_sq=score_atr_sq(bars),
        ma_conv=score_ma_conv(bars),
        pp=score_pocket_pivot(bars),
        obv_div=score_obv_div(bars),
        mfi_turn=score_mfi_turn(bars),
    )


def select_candidates(
    scored: Dict[str, VduScoreBreakdown],
    *,
    score_min: int,
    max_candidates: int,
    symbol_order: Sequence[str] | None = None,
) -> List[str]:
    """Pick symbols with total >= score_min. Tie-break: VDU+ATR, then order."""
    order_index = {s: i for i, s in enumerate(symbol_order or [])}

    def key(sym: str) -> tuple:
        bd = scored[sym]
        return (
            -(bd.vdu + bd.atr_sq),
            -bd.total,
            order_index.get(sym, 10**9),
            sym,
        )

    eligible = [s for s, bd in scored.items() if bd.total >= score_min]
    eligible.sort(key=key)
    return eligible[: max(0, int(max_candidates))]
