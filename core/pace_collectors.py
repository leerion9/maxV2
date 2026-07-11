from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime
from math import floor
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

from config.settings import Settings
from core.pace_gate import PaceGateEval

# Excel(Windows) opens CSV as CP949 unless a UTF-8 BOM is present.
CSV_ENCODING = "utf-8-sig"


GATE_FIELDS = [
    "ts",
    "strategy",
    "symbol",
    "breakout_price",
    "current_price",
    "cum_value",
    "f_t",
    "projected_value",
    "value_ma5",
    "pace_ratio",
    "gate_pass",
    "entered",
    "block_reason",
]

PROFILE_FIELDS = [
    "ts",
    "symbol",
    "current_price",
    "cum_volume",
    "cum_value",
    "prev_close",
]

LEDGER_FIELDS = [
    "date",
    "symbol",
    "entry_ts",
    "entry_price",
    "breakout_price",
    "qty",
    # exit_open_date: the session whose open price filled exit_open_next.
    # Lets analysts detect fills that are NOT the immediate next trading day
    # (e.g. trading halt, or a missed backfill day).
    "exit_open_date",
    "exit_open_next",
    "pnl_open_next_bp",
    "exit_close_same",
    "pnl_close_same_bp",
    "pace_ratio_at_entry",
    "fees_bp",
    "net_pnl_open_next_bp",
]

# Human-readable description row written right below the header.
# The first cell starts with "#" so readers (_read_all) can skip it as a comment.
LEDGER_DESC_ROW = {
    "date": "#진입일(YYYYMMDD)",
    "symbol": "종목코드",
    "entry_ts": "가상 매수 시각(돌파+게이트 통과 판정 순간)",
    "entry_price": "가상 매수가=판정 순간 현재가",
    "breakout_price": "돌파가(모드별), 매수가와의 차이=슬리피지",
    "qty": "수량=종목당 예산/매수가 내림",
    "exit_open_date": "청산 시가의 소속 날짜(진입 익거래일이 아니면 이례 건)",
    "exit_open_next": "주 청산가=익일 시가",
    "pnl_open_next_bp": "익일 시가 청산 총손익(bp, 비용 차감 전. 100bp=1%)",
    "exit_close_same": "비교용 당일 종가(15:20 직후 현재가 근사, 매매 미관여)",
    "pnl_close_same_bp": "당일 종가 청산 가정 총손익(bp)",
    "pace_ratio_at_entry": "진입 순간 페이스 비율(>=3.0)",
    "fees_bp": "왕복 수수료+거래세(bp)",
    "net_pnl_open_next_bp": "최종 성적=익일 시가 총손익-비용(bp)",
}

LEDGER_DESC_K_RANGE = dict(LEDGER_DESC_ROW)
LEDGER_DESC_K_RANGE["breakout_price"] = (
    "돌파가(시가+전일 고저폭xK), 매수가와의 차이=슬리피지"
)

LEDGER_DESC_PREV_HIGH = dict(LEDGER_DESC_ROW)
LEDGER_DESC_PREV_HIGH["breakout_price"] = (
    "돌파가(전일 고가), 매수가와의 차이=슬리피지"
)

# Opening Drive: same-day round trip (no next-open exit columns).
OD_LEDGER_FIELDS = [
    "date",
    "symbol",
    "entry_ts",
    "entry_price",
    "trigger_price",
    "qty",
    "gap_pct",
    "observe_high",
    "exit_ts",
    "exit_price",
    "exit_reason",
    "pnl_bp",
    "fees_bp",
    "net_pnl_bp",
    "pace_ratio_at_entry",
]

OD_LEDGER_DESC_ROW = {
    "date": "#진입일(YYYYMMDD)",
    "symbol": "종목코드",
    "entry_ts": "가상 매수 시각(초반 고점 돌파 판정 순간)",
    "entry_price": "가상 매수가=판정 순간 현재가",
    "trigger_price": "관찰구간 고점(돌파 트리거)",
    "qty": "수량=종목당 예산/매수가 내림",
    "gap_pct": "시가 갭%(전일종가 대비, 소수 아닌 %포인트)",
    "observe_high": "09:00~관찰종료 구간의 고가",
    "exit_ts": "가상 매도 시각",
    "exit_price": "가상 매도가=청산 판정 순간 현재가",
    "exit_reason": "청산사유(STOP/TRAIL/TIME/FORCE_CLOSE)",
    "pnl_bp": "총손익(bp, 비용 차감 전. 100bp=1%)",
    "fees_bp": "왕복 수수료+거래세(bp)",
    "net_pnl_bp": "최종 성적=총손익-비용(bp)",
    "pace_ratio_at_entry": "진입 순간 대금 페이스 비율(활력 필터)",
}


def _ensure_header(path: Path, fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding=CSV_ENCODING) as fp:
            csv.DictWriter(fp, fieldnames=fields).writeheader()


def _migrate_header_if_needed(path: Path, fields: List[str]) -> None:
    """
    If an existing CSV has an older/different header (e.g. schema gained a
    column), rewrite it in the new schema, preserving rows and filling
    missing columns with "". Prevents misaligned appends.
    """
    if not path.exists():
        return
    with path.open("r", newline="", encoding=CSV_ENCODING) as fp:
        reader = csv.reader(fp)
        try:
            header = next(reader)
        except StopIteration:
            header = []
    if header == fields:
        return
    with path.open("r", newline="", encoding=CSV_ENCODING) as fp:
        rows = list(csv.DictReader(fp))
    with path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


@dataclass
class GateCsvLogger:
    log_dir: Path
    min_interval_sec: int
    _last_log_ts: Dict[str, float] = field(default_factory=dict)
    _ymd: str = ""

    def _path_for(self, ymd: str) -> Path:
        return self.log_dir / f"gate_{ymd}.csv"

    def maybe_log(
        self,
        *,
        ymd: str,
        symbol: str,
        breakout_price: int,
        current_price: int,
        cum_value: int,
        value_ma5: int,
        gate: PaceGateEval,
        entered: bool,
        block_reason: str,
        strategy: str = "",
    ) -> None:
        # Entry moments must ALWAYS be recorded — the per-symbol throttle only
        # applies to repeated non-entry evaluations. (Review issue 2026-07-08:
        # 2 of 4 first-day entries were missing from the gate CSV.)
        now_ts = time.time()
        throttle_key = f"{strategy}:{symbol}" if strategy else symbol
        if not entered:
            last = self._last_log_ts.get(throttle_key, 0.0)
            if now_ts - last < self.min_interval_sec:
                return
        self._last_log_ts[throttle_key] = now_ts

        path = self._path_for(ymd)
        _ensure_header(path, GATE_FIELDS)
        _migrate_header_if_needed(path, GATE_FIELDS)
        row = {
            "ts": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
            "strategy": strategy,
            "symbol": symbol,
            "breakout_price": breakout_price,
            "current_price": current_price,
            "cum_value": cum_value,
            "f_t": f"{gate.f_t:.6f}",
            "projected_value": f"{gate.projected_value:.0f}",
            "value_ma5": value_ma5,
            "pace_ratio": f"{gate.pace_ratio:.4f}",
            "gate_pass": str(gate.gate_pass).lower(),
            "entered": str(entered).lower(),
            "block_reason": block_reason,
        }
        with path.open("a", newline="", encoding=CSV_ENCODING) as fp:
            csv.DictWriter(fp, fieldnames=GATE_FIELDS).writerow(row)


@dataclass
class ValueProfileLogger:
    log_dir: Path
    interval_sec: int
    _last_snapshot_ts: float = 0.0

    def _path_for(self, ymd: str) -> Path:
        return self.log_dir / f"value_profile_{ymd}.csv"

    def should_snapshot(self) -> bool:
        return time.time() - self._last_snapshot_ts >= self.interval_sec

    def log_snapshot(
        self,
        *,
        ymd: str,
        rows: List[Dict[str, object]],
    ) -> None:
        """
        Each row should carry its own "ts" (quote-time). A full universe scan
        takes tens of seconds under the KIS rate limit, so a single batch
        timestamp would skew the f(t) calibration data.
        """
        if not rows:
            return
        self._last_snapshot_ts = time.time()
        path = self._path_for(ymd)
        _ensure_header(path, PROFILE_FIELDS)
        fallback_ts = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        with path.open("a", newline="", encoding=CSV_ENCODING) as fp:
            writer = csv.DictWriter(fp, fieldnames=PROFILE_FIELDS)
            for row in rows:
                writer.writerow({"ts": fallback_ts, **row})


@dataclass
class PaperLedger:
    path: Path
    settings: Settings
    desc_row: Dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.desc_row is None:
            self.desc_row = dict(LEDGER_DESC_ROW)
        _migrate_header_if_needed(self.path, LEDGER_FIELDS)
        _ensure_header(self.path, LEDGER_FIELDS)
        self._ensure_desc_row()
        self._ensure_excel_bom()

    @staticmethod
    def _is_desc_row(row: Dict[str, str]) -> bool:
        return str(row.get("date", "")).startswith("#")

    def _ensure_desc_row(self) -> None:
        """Insert the Korean description row right below the header (idempotent)."""
        with self.path.open("r", newline="", encoding=CSV_ENCODING) as fp:
            rows = list(csv.DictReader(fp))
        if rows and self._is_desc_row(rows[0]):
            # Refresh description text if schema/desc changed.
            if rows[0].get("breakout_price") != self.desc_row.get("breakout_price"):
                data_rows = [r for r in rows if not self._is_desc_row(r)]
                self._rewrite(data_rows)
            return
        data_rows = [r for r in rows if not self._is_desc_row(r)]
        self._rewrite(data_rows)

    def _ensure_excel_bom(self) -> None:
        """Rewrite legacy UTF-8 (no BOM) files so Excel on Windows shows Korean."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        if self.path.read_bytes()[:3] == b"\xef\xbb\xbf":
            return
        self._rewrite(self._read_all())

    def append_entry(
        self,
        *,
        ymd: str,
        symbol: str,
        entry_ts: str,
        entry_price: int,
        breakout_price: int,
        qty: int,
        pace_ratio_at_entry: float,
    ) -> None:
        row = {
            "date": ymd,
            "symbol": symbol,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "breakout_price": breakout_price,
            "qty": qty,
            "exit_open_date": "",
            "exit_open_next": "",
            "pnl_open_next_bp": "",
            "exit_close_same": "",
            "pnl_close_same_bp": "",
            "pace_ratio_at_entry": f"{pace_ratio_at_entry:.4f}",
            "fees_bp": "",
            "net_pnl_open_next_bp": "",
        }
        with self.path.open("a", newline="", encoding=CSV_ENCODING) as fp:
            csv.DictWriter(fp, fieldnames=LEDGER_FIELDS).writerow(row)

    def symbols_pending_same_day_close(self, *, ymd: str) -> List[str]:
        """Entries made today whose same-day close column is still empty.
        Derived from the CSV (not memory) so an intraday restart loses nothing."""
        out: List[str] = []
        for row in self._read_all():
            if (
                str(row.get("date", "")).strip() == ymd
                and not str(row.get("exit_close_same", "")).strip()
            ):
                sym = str(row.get("symbol", "")).strip()
                if sym and sym not in out:
                    out.append(sym)
        return out

    def symbols_pending_open_exit(self, *, before_ymd: str) -> List[str]:
        """Entries from sessions before `before_ymd` still lacking exit_open_next."""
        out: List[str] = []
        for row in self._read_all():
            entry_date = str(row.get("date", "")).strip()
            if (
                entry_date
                and entry_date < before_ymd
                and not str(row.get("exit_open_next", "")).strip()
            ):
                sym = str(row.get("symbol", "")).strip()
                if sym and sym not in out:
                    out.append(sym)
        return out

    def fill_same_day_close(self, *, ymd: str, symbol: str, exit_close: int) -> None:
        rows = self._read_all()
        updated = False
        for row in rows:
            if (
                row.get("date") == ymd
                and row.get("symbol") == symbol
                and not str(row.get("exit_close_same", "")).strip()
            ):
                entry_price = int(float(row.get("entry_price", 0) or 0))
                qty = int(float(row.get("qty", 0) or 0))
                if entry_price <= 0 or qty <= 0:
                    continue
                pnl_bp = self._gross_pnl_bp(entry_price, exit_close)
                row["exit_close_same"] = str(exit_close)
                row["pnl_close_same_bp"] = f"{pnl_bp:.2f}"
                updated = True
        if updated:
            self._rewrite(rows)

    def fill_next_open_exits(
        self,
        *,
        exit_ymd: str,
        symbol_opens: Dict[str, int],
        expected_entry_ymd: str = "",
    ) -> Tuple[int, List[str]]:
        """
        Backfill exit_open_next for entries from prior sessions with `exit_ymd`'s
        open price, and stamp exit_open_date=exit_ymd.

        Returns (filled_count, anomalies). When `expected_entry_ymd` (the trading
        day right before exit_ymd) is given, rows whose entry date is older are
        still filled but reported as anomalies — their exit is NOT the entry's
        immediate next-day open (halt or missed backfill), so they must be
        excluded or handled separately in performance analysis.
        """
        rows = self._read_all()
        count = 0
        anomalies: List[str] = []
        for row in rows:
            if str(row.get("exit_open_next", "")).strip():
                continue
            sym = str(row.get("symbol", "")).strip()
            if sym not in symbol_opens:
                continue
            entry_date = str(row.get("date", "")).strip()
            if not entry_date or entry_date >= exit_ymd:
                continue
            entry_price = int(float(row.get("entry_price", 0) or 0))
            qty = int(float(row.get("qty", 0) or 0))
            exit_open = int(symbol_opens[sym])
            if entry_price <= 0 or qty <= 0 or exit_open <= 0:
                continue
            gross_bp = self._gross_pnl_bp(entry_price, exit_open)
            fees_bp = self._round_trip_fees_bp(entry_price, exit_open, qty)
            net_bp = gross_bp - fees_bp
            row["exit_open_date"] = exit_ymd
            row["exit_open_next"] = str(exit_open)
            row["pnl_open_next_bp"] = f"{gross_bp:.2f}"
            row["fees_bp"] = f"{fees_bp:.2f}"
            row["net_pnl_open_next_bp"] = f"{net_bp:.2f}"
            count += 1
            if expected_entry_ymd and entry_date != expected_entry_ymd:
                anomalies.append(f"{sym}(entry={entry_date}, exit_open={exit_ymd})")
        if count:
            self._rewrite(rows)
        return count, anomalies

    def _gross_pnl_bp(self, entry: int, exit_px: int) -> float:
        if entry <= 0:
            return 0.0
        return (exit_px / entry - 1.0) * 10000.0

    def _round_trip_fees_bp(self, entry: int, exit_px: int, qty: int) -> float:
        buy_amt = entry * qty
        sell_amt = exit_px * qty
        fee = buy_amt * self.settings.fee_rate_buy + sell_amt * self.settings.fee_rate_sell
        tax = sell_amt * self.settings.tax_rate_sell
        if buy_amt <= 0:
            return 0.0
        return (fee + tax) / buy_amt * 10000.0

    def _read_all(self) -> List[Dict[str, str]]:
        """Data rows only — the '#'-prefixed description row is skipped."""
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding=CSV_ENCODING) as fp:
            return [r for r in csv.DictReader(fp) if not self._is_desc_row(r)]

    def _rewrite(self, rows: List[Dict[str, str]]) -> None:
        with self.path.open("w", newline="", encoding=CSV_ENCODING) as fp:
            writer = csv.DictWriter(fp, fieldnames=LEDGER_FIELDS)
            writer.writeheader()
            writer.writerow(self.desc_row or LEDGER_DESC_ROW)
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in LEDGER_FIELDS})


@dataclass
class OpeningDriveLedger:
    """Same-day paper ledger for Opening Drive (separate bankroll/results)."""

    path: Path
    settings: Settings

    def __post_init__(self) -> None:
        _migrate_header_if_needed(self.path, OD_LEDGER_FIELDS)
        _ensure_header(self.path, OD_LEDGER_FIELDS)
        self._ensure_desc_row()
        self._ensure_excel_bom()

    @staticmethod
    def _is_desc_row(row: Dict[str, str]) -> bool:
        return str(row.get("date", "")).startswith("#")

    def _ensure_desc_row(self) -> None:
        with self.path.open("r", newline="", encoding=CSV_ENCODING) as fp:
            rows = list(csv.DictReader(fp))
        if rows and self._is_desc_row(rows[0]):
            return
        data_rows = [r for r in rows if not self._is_desc_row(r)]
        self._rewrite(data_rows)

    def _ensure_excel_bom(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        if self.path.read_bytes()[:3] == b"\xef\xbb\xbf":
            return
        self._rewrite(self._read_all())

    def append_entry(
        self,
        *,
        ymd: str,
        symbol: str,
        entry_ts: str,
        entry_price: int,
        trigger_price: int,
        qty: int,
        gap_pct: float,
        observe_high: int,
        pace_ratio_at_entry: float,
    ) -> None:
        row = {
            "date": ymd,
            "symbol": symbol,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "trigger_price": trigger_price,
            "qty": qty,
            "gap_pct": f"{gap_pct:.4f}",
            "observe_high": str(observe_high),
            "exit_ts": "",
            "exit_price": "",
            "exit_reason": "",
            "pnl_bp": "",
            "fees_bp": "",
            "net_pnl_bp": "",
            "pace_ratio_at_entry": f"{pace_ratio_at_entry:.4f}",
        }
        with self.path.open("a", newline="", encoding=CSV_ENCODING) as fp:
            csv.DictWriter(fp, fieldnames=OD_LEDGER_FIELDS).writerow(row)

    def symbols_open(self, *, ymd: str) -> List[str]:
        out: List[str] = []
        for row in self._read_all():
            if (
                str(row.get("date", "")).strip() == ymd
                and not str(row.get("exit_price", "")).strip()
            ):
                sym = str(row.get("symbol", "")).strip()
                if sym and sym not in out:
                    out.append(sym)
        return out

    def fill_exit(
        self,
        *,
        ymd: str,
        symbol: str,
        exit_ts: str,
        exit_price: int,
        exit_reason: str,
    ) -> bool:
        rows = self._read_all()
        updated = False
        for row in rows:
            if (
                row.get("date") == ymd
                and row.get("symbol") == symbol
                and not str(row.get("exit_price", "")).strip()
            ):
                entry_price = int(float(row.get("entry_price", 0) or 0))
                qty = int(float(row.get("qty", 0) or 0))
                if entry_price <= 0 or qty <= 0 or exit_price <= 0:
                    continue
                gross_bp = (exit_price / entry_price - 1.0) * 10000.0
                fees_bp = self._round_trip_fees_bp(entry_price, exit_price, qty)
                row["exit_ts"] = exit_ts
                row["exit_price"] = str(exit_price)
                row["exit_reason"] = exit_reason
                row["pnl_bp"] = f"{gross_bp:.2f}"
                row["fees_bp"] = f"{fees_bp:.2f}"
                row["net_pnl_bp"] = f"{gross_bp - fees_bp:.2f}"
                updated = True
        if updated:
            self._rewrite(rows)
        return updated

    def _round_trip_fees_bp(self, entry: int, exit_px: int, qty: int) -> float:
        buy_amt = entry * qty
        sell_amt = exit_px * qty
        fee = buy_amt * self.settings.fee_rate_buy + sell_amt * self.settings.fee_rate_sell
        tax = sell_amt * self.settings.tax_rate_sell
        if buy_amt <= 0:
            return 0.0
        return (fee + tax) / buy_amt * 10000.0

    def _read_all(self) -> List[Dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding=CSV_ENCODING) as fp:
            return [r for r in csv.DictReader(fp) if not self._is_desc_row(r)]

    def _rewrite(self, rows: List[Dict[str, str]]) -> None:
        with self.path.open("w", newline="", encoding=CSV_ENCODING) as fp:
            writer = csv.DictWriter(fp, fieldnames=OD_LEDGER_FIELDS)
            writer.writeheader()
            writer.writerow(OD_LEDGER_DESC_ROW)
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in OD_LEDGER_FIELDS})


def calc_paper_qty(per_symbol_budget: int, entry_price: int) -> int:
    if entry_price <= 0:
        return 0
    return floor(int(per_symbol_budget) / int(entry_price))
