from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from core.api_client import Quote


@dataclass
class SymbolState:
    avg_volume_5d: int
    prev_high: int
    prev_low: int
    breakout_k: float
    breakout_price: Optional[int] = None
    a_met: bool = False
    bought: bool = False
    seen: bool = False
    skip: bool = False
    skip_reason: str = ""


@dataclass
class EntrySignal:
    symbol: str
    breakout_price: int
    reason: str = "A_then_B_breakout"


@dataclass
class SkipSignal:
    symbol: str
    reason: str = "skip_initial_AB_met"


@dataclass
class VolatilityBreakoutStrategy:
    symbol_state: Dict[str, SymbolState] = field(default_factory=dict)

    def register(self, symbol: str, avg_volume_5d: int, prev_high: int, prev_low: int, breakout_k: float) -> None:
        self.symbol_state[symbol] = SymbolState(
            avg_volume_5d=avg_volume_5d,
            prev_high=prev_high,
            prev_low=prev_low,
            breakout_k=breakout_k,
        )

    def on_quote(self, quote: Quote) -> Optional[EntrySignal]:
        state = self.symbol_state.get(quote.symbol)
        if state is None or state.bought or state.skip:
            return None

        if state.breakout_price is None and quote.open_price > 0:
            state.breakout_price = int(
                quote.open_price + (state.prev_high - state.prev_low) * state.breakout_k
            )

        if not state.seen:
            state.seen = True
            if (
                state.breakout_price is not None
                and quote.volume >= state.avg_volume_5d
                and quote.current_price >= state.breakout_price
            ):
                state.skip = True
                state.skip_reason = "initial_quote_meets_A_and_B"
                return None

        if (not state.a_met) and quote.volume >= state.avg_volume_5d:
            state.a_met = True
            return None

        if state.a_met and state.breakout_price is not None and quote.current_price >= state.breakout_price:
            state.bought = True
            return EntrySignal(symbol=quote.symbol, breakout_price=state.breakout_price)

        return None
