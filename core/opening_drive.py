from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.api_client import Quote
from core.pace_gate import interpolate_f


@dataclass
class OpeningDriveState:
    prev_close: int
    open_price: int = 0
    gap_pct: float = 0.0
    gap_ok: Optional[bool] = None
    observe_high: int = 0
    observe_done: bool = False
    volume_ok: Optional[bool] = None
    rejected: bool = False
    reject_reason: str = ""
    bought: bool = False
    entry_price: int = 0
    day_high_since_entry: int = 0


@dataclass
class OpeningDriveEntry:
    symbol: str
    entry_price: int
    trigger_price: int
    gap_pct: float
    observe_high: int
    pace_ratio: float
    reason: str = "opening_drive"


@dataclass
class OpeningDriveExit:
    symbol: str
    exit_price: int
    reason: str  # STOP | TRAIL | TIME | FORCE_CLOSE


@dataclass
class OpeningDriveStrategy:
    """
    Gap-up opening drive (paper):
      1) open gap in [gap_min, gap_max]
      2) observe high until observe_end_hhmm (no buys)
      3) after observe: require price advanced + pace_ratio >= min
      4) buy on break of observe high
      5) same-day exit: stop / trail / force time
    """

    gap_min: float = 0.015
    gap_max: float = 0.03
    observe_end_hhmm: str = "09:30"
    stop_pct: float = 0.02
    trail_pct: float = 0.02
    force_exit_hhmm: str = "11:00"
    min_pace_ratio: float = 1.5
    symbol_state: Dict[str, OpeningDriveState] = field(default_factory=dict)

    def register(self, symbol: str, prev_close: int) -> None:
        self.symbol_state[symbol] = OpeningDriveState(prev_close=int(prev_close or 0))

    def on_quote(
        self,
        quote: Quote,
        *,
        now_hhmm: str,
        value_ma5: int,
    ) -> tuple[Optional[OpeningDriveEntry], Optional[OpeningDriveExit]]:
        state = self.symbol_state.get(quote.symbol)
        if state is None or state.rejected:
            return None, None

        if state.bought:
            return None, self._check_exit(state, quote, now_hhmm)

        self._update_open_and_gap(state, quote)
        if state.rejected:
            return None, None

        if not state.observe_done:
            if quote.current_price > state.observe_high:
                state.observe_high = int(quote.current_price)
            if now_hhmm >= self.observe_end_hhmm:
                self._finalize_observe(state, quote, value_ma5=value_ma5, now_hhmm=now_hhmm)
            return None, None

        if state.rejected or state.volume_ok is False:
            return None, None

        trigger = int(state.observe_high)
        if trigger <= 0 or quote.current_price <= trigger:
            return None, None

        pace = self._pace_ratio(quote.cum_value, value_ma5, now_hhmm)
        entry = OpeningDriveEntry(
            symbol=quote.symbol,
            entry_price=int(quote.current_price),
            trigger_price=trigger,
            gap_pct=state.gap_pct,
            observe_high=trigger,
            pace_ratio=pace,
        )
        return entry, None

    def confirm_entry(self, symbol: str, entry_price: int) -> None:
        state = self.symbol_state.get(symbol)
        if state is None:
            return
        state.bought = True
        state.entry_price = int(entry_price)
        state.day_high_since_entry = int(entry_price)

    def _update_open_and_gap(self, state: OpeningDriveState, quote: Quote) -> None:
        if state.open_price <= 0 and quote.open_price > 0:
            state.open_price = int(quote.open_price)
            if state.prev_close > 0:
                state.gap_pct = state.open_price / state.prev_close - 1.0
            else:
                state.gap_pct = 0.0
            state.gap_ok = self.gap_min <= state.gap_pct <= self.gap_max
            if not state.gap_ok:
                state.rejected = True
                state.reject_reason = "GAP_FILTER"
                return
            state.observe_high = max(state.open_price, int(quote.current_price or 0))

    def _finalize_observe(
        self,
        state: OpeningDriveState,
        quote: Quote,
        *,
        value_ma5: int,
        now_hhmm: str,
    ) -> None:
        state.observe_done = True
        if state.observe_high <= 0:
            state.observe_high = int(quote.current_price or state.open_price or 0)
        # Price must have advanced above the open during the observe window.
        if state.open_price > 0 and state.observe_high <= state.open_price:
            state.rejected = True
            state.reject_reason = "OBSERVE_FLAT"
            state.volume_ok = False
            return
        pace = self._pace_ratio(quote.cum_value, value_ma5, now_hhmm)
        state.volume_ok = pace >= self.min_pace_ratio
        if not state.volume_ok:
            state.rejected = True
            state.reject_reason = "PACE_WEAK"

    def _check_exit(
        self, state: OpeningDriveState, quote: Quote, now_hhmm: str
    ) -> Optional[OpeningDriveExit]:
        px = int(quote.current_price)
        if px > state.day_high_since_entry:
            state.day_high_since_entry = px

        if state.entry_price > 0 and px <= int(state.entry_price * (1.0 - self.stop_pct)):
            return OpeningDriveExit(symbol=quote.symbol, exit_price=px, reason="STOP")

        if state.day_high_since_entry > 0 and px <= int(
            state.day_high_since_entry * (1.0 - self.trail_pct)
        ):
            return OpeningDriveExit(symbol=quote.symbol, exit_price=px, reason="TRAIL")

        if now_hhmm >= self.force_exit_hhmm:
            return OpeningDriveExit(symbol=quote.symbol, exit_price=px, reason="TIME")

        return None

    @staticmethod
    def _pace_ratio(cum_value: int, value_ma5: int, now_hhmm: str) -> float:
        f_t = interpolate_f(now_hhmm)
        if f_t <= 0 or value_ma5 <= 0:
            return 0.0
        return (float(cum_value) / f_t) / float(value_ma5)
