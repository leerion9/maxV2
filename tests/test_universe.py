from core.universe import UniverseBuilder


class DummyApi:
    def get_market_cap_rankings(self):
        return ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

    def get_daily_prices(self, symbol: str, days: int = 6):
        # Newest-first: rows[0] is ref close; MA5 = mean(rows[0:5]); pass if ref > MA5.
        if symbol in {"A"}:
            return [
                {"close": 110, "high": 120, "low": 100, "volume": 1000},
                {"close": 100, "high": 105, "low": 95, "volume": 1000},
                {"close": 100, "high": 105, "low": 95, "volume": 1000},
                {"close": 100, "high": 105, "low": 95, "volume": 1000},
                {"close": 100, "high": 105, "low": 95, "volume": 1000},
                {"close": 100, "high": 105, "low": 95, "volume": 1000},
            ]
        flat = {"close": 100, "high": 110, "low": 90, "volume": 1000}
        return [dict(flat) for _ in range(6)]


def test_top_10_percent_and_ma5_filter():
    builder = UniverseBuilder(api=DummyApi(), top_ratio=0.1)
    result = builder.build()
    assert result == ["A"]
