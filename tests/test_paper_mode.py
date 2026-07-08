import pytest
from types import SimpleNamespace

from core.order import OrderManager


def test_order_manager_blocks_when_paper_mode():
    api = SimpleNamespace(
        place_limit_buy=lambda **kwargs: {"ord_no": "X"},
        place_market_sell=lambda **kwargs: {"ord_no": "Y"},
    )
    settings = SimpleNamespace(paper_mode=True)
    om = OrderManager(api=api, settings=settings)
    with pytest.raises(RuntimeError, match="paper_mode"):
        om.place_breakout_buy_with_budget(
            symbol="005930", per_symbol_budget=1_000_000, breakout_price=70_000
        )
    with pytest.raises(RuntimeError, match="paper_mode"):
        om.place_open_liquidation(symbol="005930", qty=1)
