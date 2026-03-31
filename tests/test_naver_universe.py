from core.naver_universe import passes_ma5_newest_first


def test_ma5_passes_when_latest_above_average_of_five():
    closes = [110, 100, 100, 100, 100, 100]
    assert passes_ma5_newest_first(closes) is True


def test_ma5_fails_when_flat():
    closes = [100, 100, 100, 100, 100]
    assert passes_ma5_newest_first(closes) is False


def test_ma5_fails_when_short():
    assert passes_ma5_newest_first([1, 2, 3]) is False
