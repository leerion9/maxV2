import csv
from types import SimpleNamespace

from core.pace_collectors import LEDGER_DESC_ROW, LEDGER_FIELDS, PaperLedger


def _settings():
    return SimpleNamespace(fee_rate_buy=0.00015, fee_rate_sell=0.00015, tax_rate_sell=0.0018)


def _make_ledger(tmp_path):
    return PaperLedger(path=tmp_path / "paper_ledger.csv", settings=_settings())


def _entry(ledger, *, ymd: str, symbol: str, price: int = 10_000):
    ledger.append_entry(
        ymd=ymd,
        symbol=symbol,
        entry_ts=f"{ymd}T10:00:00+09:00",
        entry_price=price,
        breakout_price=price,
        qty=10,
        pace_ratio_at_entry=3.5,
    )


def test_fill_next_open_exits_stamps_exit_open_date(tmp_path):
    ledger = _make_ledger(tmp_path)
    _entry(ledger, ymd="20260706", symbol="005930")

    n, anomalies = ledger.fill_next_open_exits(
        exit_ymd="20260707",
        symbol_opens={"005930": 10_500},
        expected_entry_ymd="20260706",
    )
    assert n == 1
    assert anomalies == []
    rows = ledger._read_all()
    assert rows[0]["exit_open_date"] == "20260707"
    assert rows[0]["exit_open_next"] == "10500"
    assert float(rows[0]["pnl_open_next_bp"]) == 500.0


def test_fill_next_open_exits_flags_non_next_day_fill(tmp_path):
    """D+1에 시가를 못 받은 건이 D+2 시가로 채워지면 이례 건으로 보고."""
    ledger = _make_ledger(tmp_path)
    _entry(ledger, ymd="20260706", symbol="005930")

    n, anomalies = ledger.fill_next_open_exits(
        exit_ymd="20260708",
        symbol_opens={"005930": 9_000},
        expected_entry_ymd="20260707",
    )
    assert n == 1
    assert len(anomalies) == 1
    assert "005930" in anomalies[0]
    assert ledger._read_all()[0]["exit_open_date"] == "20260708"


def test_pending_helpers_derived_from_csv(tmp_path):
    ledger = _make_ledger(tmp_path)
    _entry(ledger, ymd="20260706", symbol="005930")
    _entry(ledger, ymd="20260706", symbol="000660")

    # 장중 재시작을 흉내: 같은 파일로 새 인스턴스를 만들어도 대상이 복원된다.
    reopened = PaperLedger(path=ledger.path, settings=_settings())
    assert set(reopened.symbols_pending_same_day_close(ymd="20260706")) == {"005930", "000660"}
    assert set(reopened.symbols_pending_open_exit(before_ymd="20260707")) == {"005930", "000660"}
    assert reopened.symbols_pending_open_exit(before_ymd="20260706") == []

    reopened.fill_same_day_close(ymd="20260706", symbol="005930", exit_close=10_200)
    assert reopened.symbols_pending_same_day_close(ymd="20260706") == ["000660"]


def test_header_migration_from_old_schema(tmp_path):
    """구 스키마(13컬럼, exit_open_date 없음) 파일을 새 스키마로 안전 이관."""
    path = tmp_path / "paper_ledger.csv"
    old_fields = [f for f in LEDGER_FIELDS if f != "exit_open_date"]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow(
            {
                "date": "20260703",
                "symbol": "005930",
                "entry_ts": "20260703T10:00:00+09:00",
                "entry_price": "10000",
                "breakout_price": "10000",
                "qty": "10",
                "exit_open_next": "",
                "pnl_open_next_bp": "",
                "exit_close_same": "",
                "pnl_close_same_bp": "",
                "pace_ratio_at_entry": "3.5000",
                "fees_bp": "",
                "net_pnl_open_next_bp": "",
            }
        )

    ledger = PaperLedger(path=path, settings=_settings())
    rows = ledger._read_all()
    assert len(rows) == 1
    assert rows[0]["exit_open_date"] == ""
    assert rows[0]["symbol"] == "005930"

    # 이관 후 append가 헤더와 정렬되는지 확인
    _entry(ledger, ymd="20260706", symbol="000660")
    with path.open("r", newline="", encoding="utf-8") as fp:
        reader = csv.reader(fp)
        header = next(reader)
        desc = next(reader)
    assert header == LEDGER_FIELDS
    assert desc[0].startswith("#")
    assert desc[0] == LEDGER_DESC_ROW["date"]
    assert len(ledger._read_all()) == 2


def test_desc_row_on_fresh_ledger(tmp_path):
    ledger = _make_ledger(tmp_path)
    path = ledger.path
    with path.open("r", newline="", encoding="utf-8") as fp:
        reader = csv.reader(fp)
        header = next(reader)
        desc = next(reader)
    assert header == LEDGER_FIELDS
    assert desc == [LEDGER_DESC_ROW[k] for k in LEDGER_FIELDS]
    assert ledger._read_all() == []
