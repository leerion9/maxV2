import csv
import time
from pathlib import Path

from core.pace_gate import PaceGateEval
from core.pace_collectors import GateCsvLogger


def _gate(*, gate_pass: bool = True) -> PaceGateEval:
    return PaceGateEval(
        f_t=0.26,
        projected_value=1_000_000_000.0,
        pace_ratio=3.5,
        gate_pass=gate_pass,
        block_reason="" if gate_pass else "PACE_FAIL",
    )


def test_entry_row_always_logged_despite_throttle(tmp_path: Path):
    logger = GateCsvLogger(log_dir=tmp_path, min_interval_sec=60)
    ymd = "20260708"
    common = dict(
        ymd=ymd,
        symbol="005930",
        breakout_price=70_000,
        current_price=70_100,
        cum_value=500_000_000,
        value_ma5=100_000_000,
        gate=_gate(),
    )
    logger.maybe_log(**common, entered=False, block_reason="PACE_FAIL")
    logger.maybe_log(**common, entered=True, block_reason="")
    path = tmp_path / f"gate_{ymd}.csv"
    with path.open("r", newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 2
    assert rows[0]["entered"] == "false"
    assert rows[1]["entered"] == "true"


def test_non_entry_respects_throttle(tmp_path: Path):
    logger = GateCsvLogger(log_dir=tmp_path, min_interval_sec=3600)
    ymd = "20260708"
    common = dict(
        ymd=ymd,
        symbol="005930",
        breakout_price=70_000,
        current_price=70_100,
        cum_value=500_000_000,
        value_ma5=100_000_000,
        gate=_gate(gate_pass=False),
    )
    logger.maybe_log(**common, entered=False, block_reason="PACE_FAIL")
    logger._last_log_ts["005930"] = time.time()
    logger.maybe_log(**common, entered=False, block_reason="PACE_FAIL")
    path = tmp_path / f"gate_{ymd}.csv"
    with path.open("r", newline="", encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 1
