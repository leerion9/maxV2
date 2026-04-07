from __future__ import annotations

from dataclasses import dataclass
from math import floor

from config.settings import Settings
from core.api_client import KISApiClient


def price_tick(price: int) -> int:
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_to_tick(price: int) -> int:
    tick = price_tick(price)
    return max(tick, (price // tick) * tick)


@dataclass
class OrderManager:
    api: KISApiClient
    settings: Settings

    def calc_buy_qty(self, cash: int, breakout_price: int) -> int:
        per_symbol_budget = int(cash * self.settings.allocation_per_symbol)
        rounded_price = round_to_tick(breakout_price)
        if rounded_price <= 0:
            return 0
        return floor(per_symbol_budget / rounded_price)

    def calc_buy_qty_with_budget(self, per_symbol_budget: int, breakout_price: int) -> int:
        rounded_price = round_to_tick(breakout_price)
        if rounded_price <= 0:
            return 0
        return floor(int(per_symbol_budget) / rounded_price)

    def place_breakout_buy(self, symbol: str, cash: int, breakout_price: int) -> dict:
        price = round_to_tick(breakout_price)
        qty = self.calc_buy_qty(cash=cash, breakout_price=price)
        if qty <= 0:
            raise ValueError(f"insufficient cash for {symbol}")
        return self.api.place_limit_buy(symbol=symbol, qty=qty, price=price)

    def place_breakout_buy_with_budget(
        self, symbol: str, per_symbol_budget: int, breakout_price: int
    ) -> dict:
        price = round_to_tick(breakout_price)
        qty = self.calc_buy_qty_with_budget(per_symbol_budget=per_symbol_budget, breakout_price=price)
        if qty <= 0:
            raise ValueError(f"insufficient cash for {symbol}")
        return self.api.place_limit_buy(symbol=symbol, qty=qty, price=price)

    def place_open_liquidation(self, symbol: str, qty: int) -> dict:
        return self.api.place_market_sell(symbol=symbol, qty=qty)

    def estimate_sell_cost(self, amount: int) -> tuple[float, float]:
        fee = amount * self.settings.fee_rate_sell
        tax = amount * self.settings.tax_rate_sell
        return fee, tax
