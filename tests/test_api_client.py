from types import SimpleNamespace

from core.api_client import KISApiClient


def test_is_open_trading_day_true_false_none():
    client = KISApiClient(settings=SimpleNamespace())

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": "Y"}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260327") is True

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": "N"}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260328") is False

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": ""}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260329") is None
