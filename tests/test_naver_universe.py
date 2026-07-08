from core.naver_universe import is_excluded_instrument, passes_ma5_newest_first


def test_ma5_passes_when_latest_above_average_of_five():
    closes = [110, 100, 100, 100, 100, 100]
    assert passes_ma5_newest_first(closes) is True


def test_ma5_fails_when_flat():
    closes = [100, 100, 100, 100, 100]
    assert passes_ma5_newest_first(closes) is False


def test_ma5_fails_when_short():
    assert passes_ma5_newest_first([1, 2, 3]) is False


def test_excluded_etf_etn_fund_spac_reit():
    assert is_excluded_instrument("KODEX CD1년금리플러스액티브(합성)") is True
    assert is_excluded_instrument("TIGER 200") is True
    assert is_excluded_instrument("KB발해인프라") is True
    assert is_excluded_instrument("신한리츠") is True
    assert is_excluded_instrument("○○스팩") is True
    assert is_excluded_instrument("ABC ETN") is True


def test_preferred_stock_not_excluded():
    assert is_excluded_instrument("LG전자우") is False
    assert is_excluded_instrument("삼성전자") is False
    assert is_excluded_instrument("하이브") is False
