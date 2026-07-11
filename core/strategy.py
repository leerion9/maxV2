from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.api_client import Quote

# Breakout trigger price modes (pace gate still applies on top).
BREAKOUT_MODE_K_RANGE = "k_range"  # open + (prev_high - prev_low) * K
BREAKOUT_MODE_PREV_HIGH = "prev_high"  # previous session high


@dataclass
class SymbolState:
    prev_high: int
    prev_low: int
    breakout_k: float
    breakout_price: Optional[int] = None
    bought: bool = False


@dataclass
class EntrySignal:
    symbol: str
    breakout_price: int
    reason: str = "pace_gate_breakout"


@dataclass
class VolatilityBreakoutStrategy:
    """
    Price-only breakout state machine.

    2026-07-06 pace-gate redesign: the legacy condition A (cumulative volume >=
    5-day average volume, required to occur BEFORE the breakout) and the
    "initial quote meets A&B -> permanent skip" rule were removed. The realtime
    pace gate (core/pace_gate.py) is now the ONLY volume condition, evaluated
    by the runner on top of this price signal.

    2026-07-11: breakout_mode=prev_high uses yesterday's high as the trigger
    (and chase reference). k_range keeps the classic Williams formula.
    """

    breakout_mode: str = BREAKOUT_MODE_PREV_HIGH
    symbol_state: Dict[str, SymbolState] = field(default_factory=dict)

    def register(self, symbol: str, prev_high: int, prev_low: int, breakout_k: float) -> None:
        self.symbol_state[symbol] = SymbolState(
            prev_high=prev_high,
            prev_low=prev_low,
            breakout_k=breakout_k,
        )

    def _resolve_breakout_price(self, state: SymbolState, quote: Quote) -> Optional[int]:
        mode = (self.breakout_mode or BREAKOUT_MODE_PREV_HIGH).strip().lower()
        if mode == BREAKOUT_MODE_PREV_HIGH:
            if state.prev_high > 0:
                return int(state.prev_high)
            return None
        # Default / k_range: needs today's open.
        if quote.open_price > 0:
            return int(
                quote.open_price + (state.prev_high - state.prev_low) * state.breakout_k
            )
        return None

    def _signal_reason(self) -> str:
        mode = (self.breakout_mode or BREAKOUT_MODE_PREV_HIGH).strip().lower()
        if mode == BREAKOUT_MODE_PREV_HIGH:
            return "prev_high_breakout"
        return "pace_gate_breakout"

    def on_quote(self, quote: Quote) -> Optional[EntrySignal]:
        state = self.symbol_state.get(quote.symbol)
        if state is None or state.bought:
            return None

        if state.breakout_price is None:
            state.breakout_price = self._resolve_breakout_price(state, quote)

        if state.breakout_price is not None and quote.current_price >= state.breakout_price:
            return EntrySignal(
                symbol=quote.symbol,
                breakout_price=state.breakout_price,
                reason=self._signal_reason(),
            )

        return None

    def confirm_entry(self, symbol: str) -> None:
        state = self.symbol_state.get(symbol)
        if state is not None:
            state.bought = True
