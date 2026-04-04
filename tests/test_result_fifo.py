from core.result_csv import (
    build_daily_rows_from_kis_range,
    build_round_rows_from_kis,
    fifo_sell_to_round_trips,
    kis_rows_to_execs,
    kis_rows_to_symbol_names,
)


def test_kis_rows_to_execs_buy_sell():
    rows = [
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260403",
            "ord_tmd": "100000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1000000",
        },
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "01",
            "ord_dt": "20260403",
            "ord_tmd": "110000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1100000",
        },
    ]
    execs = kis_rows_to_execs(rows)
    assert len(execs) == 2
    assert execs[0].side == "BUY"
    assert execs[1].side == "SELL"


def test_fifo_round_trip_profit():
    rows = [
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260403",
            "ord_tmd": "100000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1000000",
        },
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "01",
            "ord_dt": "20260403",
            "ord_tmd": "110000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1100000",
        },
    ]
    out, _rem = fifo_sell_to_round_trips(kis_rows_to_execs(rows))
    assert len(out) == 1
    assert int(out[0]["pnl"]) == 100000


def test_build_daily_open_and_closed():
    rows = [
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260407",
            "ord_tmd": "100000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1000000",
        },
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "01",
            "ord_dt": "20260408",
            "ord_tmd": "085000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1100000",
        },
        {
            "pdno": "000003",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260408",
            "ord_tmd": "140000",
            "tot_ccld_qty": "5",
            "tot_ccld_amt": "500000",
        },
    ]
    execs = kis_rows_to_execs(rows)
    daily = build_daily_rows_from_kis_range(execs, "20260408")
    kinds = {str(x.get("kind")) for x in daily}
    assert "CLOSED" in kinds
    assert "OPEN" in kinds


def test_pdno_zfill_and_kis_symbol_name():
    rows = [
        {
            "pdno": "1",
            "prdt_name": "테스트종목",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260403",
            "ord_tmd": "100000",
            "tot_ccld_qty": "1",
            "tot_ccld_amt": "1000",
        },
    ]
    assert kis_rows_to_symbol_names(rows)["000001"] == "테스트종목"
    execs = kis_rows_to_execs(rows)
    assert len(execs) == 1
    assert execs[0].symbol == "000001"


def test_build_round_rows_from_kis():
    rows = [
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "02",
            "ord_dt": "20260403",
            "ord_tmd": "100000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1000000",
        },
        {
            "pdno": "000001",
            "sll_buy_dvsn_cd": "01",
            "ord_dt": "20260403",
            "ord_tmd": "110000",
            "tot_ccld_qty": "10",
            "tot_ccld_amt": "1100000",
        },
    ]
    r = build_round_rows_from_kis(rows)
    assert len(r) == 1
    assert r[0]["symbol"] == "000001"
