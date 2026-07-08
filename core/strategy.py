from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.api_client import Quote


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
    """

    symbol_state: Dict[str, SymbolState] = field(default_factory=dict)

    def register(self, symbol: str, prev_high: int, prev_low: int, breakout_k: float) -> None:
        self.symbol_state[symbol] = SymbolState(
            prev_high=prev_high,
            prev_low=prev_low,
            breakout_k=breakout_k,
        )

    def on_quote(self, quote: Quote) -> Optional[EntrySignal]:
        state = self.symbol_state.get(quote.symbol)
        if state is None or state.bought:
            return None

        if state.breakout_price is None and quote.open_price > 0:
            state.breakout_price = int(
                quote.open_price + (state.prev_high - state.prev_low) * state.breakout_k
            )

        if state.breakout_price is not None and quote.current_price >= state.breakout_price:
            return EntrySignal(symbol=quote.symbol, breakout_price=state.breakout_price)

        return None

    def confirm_entry(self, symbol: str) -> None:
        state = self.symbol_state.get(symbol)
        if state is not None:
            state.bought = True
