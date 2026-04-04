"""
KIS 일별 주문·체결 조회(매매내역)만 사용해 result.csv를 채운다.
잔고/보유 API는 결제일(T+2 등)과 어긋날 수 있으므로 result.csv에는 사용하지 않는다.
체결 시각·수량·금액은 주문일시(ord_dt/ord_tmd) 기준으로 집계한다.
"""

from __future__ import annotations

import csv
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# Excel(Windows)에서 한글 헤더가 깨지지 않도록 BOM 포함 UTF-8
RESULT_CSV_ENCODING = "utf-8-sig"

RESULT_FIELDS = [
    "번호",
    "매수날짜",
    "매수시간",
    "매도날짜",
    "매도시간",
    "종목코드",
    "종목명",
    "매수단가",
    "매수수량",
    "총매수금액",
    "매도단가",
    "매도수량",
    "총매도금액",
    "손익",
    "수익률",
    "세금",
    "수수료",
    "누적손익",
]


def _gv(row: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] is not None and str(row[k]).strip() != "":
            return row[k]
    for want in keys:
        lw = want.lower()
        for k, v in row.items():
            if str(k).lower() == lw and v is not None and str(v).strip() != "":
                return v
    return None


def _norm_symbol_6(s: object) -> str:
    t = str(s).strip()
    if t.isdigit() and len(t) <= 6:
        return t.zfill(6)
    return t


def _to_int(v: object) -> int:
    try:
        s = str(v).replace(",", "").strip()
        if not s:
            return 0
        return int(float(s))
    except Exception:
        return 0


def _to_float(v: object) -> float:
    try:
        s = str(v).replace(",", "").strip()
        if not s:
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _side_from_row(r: Dict[str, Any]) -> str:
    v = str(_gv(r, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD") or "").strip()
    if v in ("01", "1"):
        return "SELL"
    if v in ("02", "2"):
        return "BUY"
    # 일부 응답은 코드 대신 이름만 준다 (예: sll_buy_dvsn_cd_name)
    name = str(
        _gv(r, "sll_buy_dvsn_cd_name", "SLL_BUY_DVSN_CD_NAME", "sll_buy_dvsn_name") or ""
    ).strip()
    if "매도" in name:
        return "SELL"
    if "매수" in name:
        return "BUY"
    return ""


def _symbol_from_row(r: Dict[str, Any]) -> str:
    s = str(_gv(r, "pdno", "PDNO", "prdt_code", "PRDT_CODE") or "").strip()
    return s


def _order_dt(r: Dict[str, Any]) -> Optional[datetime]:
    raw_d = str(
        _gv(r, "ord_dt", "ORD_DT", "ccld_dt", "CCLD_DT", "ord_date", "ORD_DATE") or ""
    ).strip()
    d = raw_d if len(raw_d) == 8 and raw_d.isdigit() else None
    if d is None:
        dig = "".join(c for c in raw_d if c.isdigit())
        if len(dig) >= 8:
            d = dig[:8]
    if d is None or len(d) != 8 or not d.isdigit():
        return None
    raw_tm = str(
        _gv(
            r,
            "ord_tmd",
            "ORD_TMD",
            "ord_tm",
            "ORD_TM",
            "ccld_tmd",
            "CCLD_TMD",
            "ord_time",
            "ORD_TIME",
        )
        or ""
    ).strip()
    tm = "".join(ch for ch in raw_tm if ch.isdigit())
    if not tm:
        tm = "000000"
    tm = (tm + "000000")[:6]
    try:
        return datetime.strptime(d + tm, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except Exception:
        return None


def _qty(r: Dict[str, Any]) -> int:
    for k in ("tot_ccld_qty", "TOT_CCLD_QTY", "ccld_qty", "ord_qty", "ORD_QTY"):
        v = _gv(r, k)
        if v is not None:
            q = _to_int(v)
            if q != 0:
                return abs(q)
    return 0


def _amt(r: Dict[str, Any]) -> float:
    for k in ("tot_ccld_amt", "TOT_CCLD_AMT", "ccld_amt", "ord_amt", "ORD_AMT"):
        v = _gv(r, k)
        if v is not None:
            a = abs(_to_float(v))
            if a > 0:
                return a
    return 0.0


def _is_cancelled_row(r: Dict[str, Any]) -> bool:
    c = str(_gv(r, "cncl_yn", "CNCL_YN", "cncl_yn1") or "").strip().upper()
    return c == "Y"


def _estimate_amt_from_px_qty(r: Dict[str, Any]) -> float:
    q = _qty(r)
    if q <= 0:
        return 0.0
    for k in ("ccld_avg_unpr", "avg_prvs", "AVG_PRVS", "ord_unpr", "ORD_UNPR"):
        v = _gv(r, k)
        if v is not None:
            px = _to_float(v)
            if px > 0:
                return abs(float(q) * px)
    return 0.0


def _fee(r: Dict[str, Any]) -> float:
    for k in (
        "fees",
        "FEES",
        "ovrs_fees",
        "stck_fees",
        "fee",
        "tot_fees",
        "tot_fee",
    ):
        v = _gv(r, k)
        if v is not None:
            return abs(_to_float(v))
    return 0.0


def _tax(r: Dict[str, Any]) -> float:
    for k in ("tax", "TAX", "stck_tax", "tot_tax", "tr_tax"):
        v = _gv(r, k)
        if v is not None:
            return abs(_to_float(v))
    return 0.0


def _avg_px(r: Dict[str, Any]) -> float:
    for k in ("ccld_avg_unpr", "avg_prvs", "ord_unpr", "ORD_UNPR"):
        v = _gv(r, k)
        if v is not None:
            p = _to_float(v)
            if p > 0:
                return p
    q = _qty(r)
    a = _amt(r)
    if q > 0 and a > 0:
        return a / q
    return 0.0


@dataclass
class Exec:
    ts: datetime
    side: str
    symbol: str
    qty: int
    amount: float
    fee: float
    tax: float
    avg_px: float


def kis_rows_to_symbol_names(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """KIS output1에 포함된 한글 종목명(있으면) — 마스터 미스 시 폴백."""
    out: Dict[str, str] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = _norm_symbol_6(_symbol_from_row(r))
        if len(sym) != 6 or not sym.isdigit():
            continue
        nm = str(
            _gv(
                r,
                "prdt_name",
                "PRDT_NAME",
                "prdt_abrv_name",
                "PRDT_ABRV_NAME",
                "hts_kor_isnm",
                "HTS_KOR_ISNM",
                "prdt_eng_name",
                "PRDT_ENG_NAME",
            )
            or ""
        ).strip()
        if nm:
            out[sym] = nm
    return out


def kis_rows_to_execs(rows: List[Dict[str, Any]]) -> List[Exec]:
    out: List[Exec] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if _is_cancelled_row(r):
            continue
        side = _side_from_row(r)
        sym = _norm_symbol_6(_symbol_from_row(r))
        if side not in ("BUY", "SELL") or len(sym) != 6 or not sym.isdigit():
            continue
        q = _qty(r)
        if q <= 0:
            continue
        ts = _order_dt(r)
        if ts is None:
            continue
        amt = _amt(r)
        if amt <= 0:
            amt = _estimate_amt_from_px_qty(r)
        if amt <= 0:
            continue
        avg = _avg_px(r)
        if avg <= 0 and q > 0 and amt > 0:
            avg = amt / q
        out.append(
            Exec(
                ts=ts,
                side=side,
                symbol=sym,
                qty=q,
                amount=amt,
                fee=_fee(r),
                tax=_tax(r),
                avg_px=avg,
            )
        )
    out.sort(key=lambda e: (e.ts, 0 if e.side == "BUY" else 1))
    return out


@dataclass
class BuyLot:
    ts: datetime
    qty: int
    amount: float
    fee: float
    tax: float


def fifo_sell_to_round_trips(
    exec_list: List[Exec],
) -> Tuple[List[Dict[str, Any]], Dict[str, Deque[BuyLot]]]:
    """
    종목별 FIFO: 매도 1건(또는 부분)마다 매칭된 매수 비용을 합산해 한 줄 생성.
    반환: (청산 완료 라운드, 미청산 매수 잔량)
    """
    buys: Dict[str, Deque[BuyLot]] = defaultdict(deque)
    rounds: List[Dict[str, Any]] = []
    for ex in exec_list:
        if ex.side == "BUY":
            buys[ex.symbol].append(
                BuyLot(ts=ex.ts, qty=ex.qty, amount=ex.amount, fee=ex.fee, tax=ex.tax)
            )
            continue
        need = ex.qty
        b_amt = 0.0
        b_fee = 0.0
        b_tax = 0.0
        b_qty = 0
        first_buy_ts: Optional[datetime] = None
        last_buy_ts: Optional[datetime] = None
        while need > 0 and buys[ex.symbol]:
            lot = buys[ex.symbol][0]
            take = min(need, lot.qty)
            ratio = take / lot.qty if lot.qty else 0.0
            b_amt += lot.amount * ratio
            b_fee += lot.fee * ratio
            b_tax += lot.tax * ratio
            b_qty += take
            if first_buy_ts is None:
                first_buy_ts = lot.ts
            last_buy_ts = lot.ts
            lot.qty -= take
            need -= take
            if lot.qty <= 0:
                buys[ex.symbol].popleft()
        if need > 0:
            continue
        if b_qty <= 0:
            continue
        buy_avg = b_amt / b_qty if b_qty else 0.0
        sell_avg = ex.amount / ex.qty if ex.qty else 0.0
        gross = ex.amount - b_amt
        fee_sum = b_fee + ex.fee
        tax_sum = b_tax + ex.tax
        pnl = gross
        pnl_pct = (pnl / b_amt * 100.0) if b_amt > 0 else 0.0
        rounds.append(
            {
                "buy_ts_first": first_buy_ts or ex.ts,
                "buy_ts_last": last_buy_ts or ex.ts,
                "sell_ts": ex.ts,
                "symbol": ex.symbol,
                "buy_avg": buy_avg,
                "buy_qty": b_qty,
                "buy_amt": b_amt,
                "sell_avg": sell_avg,
                "sell_qty": ex.qty,
                "sell_amt": ex.amount,
                "fee": fee_sum,
                "tax": tax_sum,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )
    return rounds, buys


def _ymd_kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y%m%d")


def build_daily_rows_from_kis_range(execs: List[Exec], as_of_ymd: str) -> List[Dict[str, Any]]:
    """
    as_of_ymd 거래일 기준 (데이터는 전부 KIS 일별체결 매매내역 execs):
    - 매도 체결이 그날인 완료 라운드(전일 등 이전 매수와 짝)
    - 매수만 있고 아직 매도 체결이 없는 분(당일 매수분, 미청산 OPEN)
    """
    rounds, remaining = fifo_sell_to_round_trips(execs)
    out: List[Dict[str, Any]] = []
    for r in rounds:
        if _ymd_kst(r["sell_ts"]) == as_of_ymd:
            out.append({**r, "kind": "CLOSED"})
    for sym, dq in remaining.items():
        for lot in list(dq):
            if _ymd_kst(lot.ts) == as_of_ymd:
                buy_avg = lot.amount / lot.qty if lot.qty else 0.0
                out.append(
                    {
                        "kind": "OPEN",
                        "symbol": sym,
                        "buy_ts_last": lot.ts,
                        "buy_avg": buy_avg,
                        "buy_qty": lot.qty,
                        "buy_amt": lot.amount,
                        "fee": lot.fee,
                        "tax": lot.tax,
                    }
                )
    return out


def _fmt_date(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d")


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%H:%M:%S")


def read_last_cumulative_and_max_no(path: Path) -> Tuple[float, int]:
    if not path.exists():
        return 0.0, 0
    try:
        with path.open("r", newline="", encoding=RESULT_CSV_ENCODING) as fp:
            reader = csv.DictReader(fp)
            rows = list(reader)
        # Excel 등으로 생긴 빈 줄(번호 없음)은 누적/번호 계산에서 제외
        rows = [r for r in rows if str(r.get("번호", "") or "").strip().isdigit()]
        if not rows:
            return 0.0, 0
        last = rows[-1]
        mx = 0
        for r in rows:
            try:
                mx = max(mx, int(str(r.get("번호", "0") or "0").strip() or "0"))
            except Exception:
                continue
        cum = _to_float(last.get("누적손익", "0"))
        return cum, mx
    except Exception:
        return 0.0, 0


def ensure_result_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=RESULT_CSV_ENCODING) as fp:
        w = csv.DictWriter(fp, fieldnames=RESULT_FIELDS)
        w.writeheader()


def append_result_rows(
    path: Path,
    rows: List[Dict[str, Any]],
    symbol_names: Dict[str, str],
    kis_symbol_names: Optional[Dict[str, str]] = None,
) -> None:
    ensure_result_header(path)
    prev_cum, max_no = read_last_cumulative_and_max_no(path)
    cum = prev_cum
    kis_nm = kis_symbol_names or {}
    with path.open("a", newline="", encoding=RESULT_CSV_ENCODING) as fp:
        w = csv.DictWriter(fp, fieldnames=RESULT_FIELDS)
        for i, r in enumerate(rows):
            no = max_no + i + 1
            sym = _norm_symbol_6(r["symbol"])
            name = (symbol_names.get(sym, "") or kis_nm.get(sym, "")).strip()
            kind = str(r.get("kind", "CLOSED"))
            buy_ts = r.get("buy_ts_first") or r.get("buy_ts_last")
            if kind == "OPEN":
                row = {
                    "번호": str(no),
                    "매수날짜": _fmt_date(buy_ts),
                    "매수시간": _fmt_time(buy_ts),
                    "매도날짜": "",
                    "매도시간": "",
                    "종목코드": sym,
                    "종목명": name,
                    "매수단가": str(int(round(float(r["buy_avg"])))),
                    "매수수량": str(int(r["buy_qty"])),
                    "총매수금액": str(int(round(float(r["buy_amt"])))),
                    "매도단가": "",
                    "매도수량": "",
                    "총매도금액": "",
                    "손익": "",
                    "수익률": "",
                    "세금": str(int(round(float(r["tax"])))),
                    "수수료": str(int(round(float(r["fee"])))),
                    "누적손익": str(int(round(cum))),
                }
                w.writerow(row)
                continue
            sell_ts = r["sell_ts"]
            pnl = float(r["pnl"])
            cum += pnl
            row = {
                "번호": str(no),
                "매수날짜": _fmt_date(buy_ts),
                "매수시간": _fmt_time(buy_ts),
                "매도날짜": _fmt_date(sell_ts),
                "매도시간": _fmt_time(sell_ts),
                "종목코드": sym,
                "종목명": name,
                "매수단가": str(int(round(float(r["buy_avg"])))),
                "매수수량": str(int(r["buy_qty"])),
                "총매수금액": str(int(round(float(r["buy_amt"])))),
                "매도단가": str(int(round(float(r["sell_avg"])))),
                "매도수량": str(int(r["sell_qty"])),
                "총매도금액": str(int(round(float(r["sell_amt"])))),
                "손익": str(int(round(pnl))),
                "수익률": f"{float(r['pnl_pct']):.4f}",
                "세금": str(int(round(float(r["tax"])))),
                "수수료": str(int(round(float(r["fee"])))),
                "누적손익": str(int(round(cum))),
            }
            w.writerow(row)


def append_round_trips(
    path: Path,
    rounds: List[Dict[str, Any]],
    symbol_names: Dict[str, str],
    kis_symbol_names: Optional[Dict[str, str]] = None,
) -> None:
    """하위 호환: CLOSED 라운드만 있는 리스트."""
    append_result_rows(path, rounds, symbol_names, kis_symbol_names=kis_symbol_names)


def build_round_rows_from_kis(kis_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    execs = kis_rows_to_execs(kis_rows)
    r, _ = fifo_sell_to_round_trips(execs)
    return r
