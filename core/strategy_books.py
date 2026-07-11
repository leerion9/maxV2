from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

from core.pace_collectors import PaperLedger
from core.strategy import VolatilityBreakoutStrategy


@dataclass
class BreakoutBook:
    """One volatility-breakout arm with its own bankroll counters + ledger."""

    name: str
    strategy: VolatilityBreakoutStrategy
    ledger: PaperLedger
    max_positions: int = 5
    ordered_symbols_today: Set[str] = field(default_factory=set)
    buy_orders_today: int = 0
    per_symbol_budget: Optional[int] = None

    def reset_day_counters(self) -> None:
        self.ordered_symbols_today = set()
        self.buy_orders_today = 0

    def can_buy(self) -> bool:
        return self.buy_orders_today < self.max_positions

    def budget(self) -> int:
        return int(self.per_symbol_budget or 0)
