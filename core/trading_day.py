from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import FrozenSet, Tuple

from config.settings import Settings

_log = logging.getLogger("maxv")


def _is_weekend_ymd(ymd: str) -> bool:
    wd = datetime.strptime(ymd, "%Y%m%d").weekday()
    return wd >= 5


def load_manual_holiday_set(path: Path) -> FrozenSet[str]:
    """파일이 없으면 빈 집합(평일 휴장일 없음). 주말만 차단."""
    if not path.is_file():
        _log.warning("휴장일 목록 파일이 없습니다. 평일 공휴일은 차단하지 않습니다: %s", path)
        return frozenset()

    out: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) == 8 and line.isdigit():
            out.add(line)
        else:
            _log.warning("휴장일 목록에서 무시한 줄: %r", raw[:80])
    return frozenset(out)


def prev_trading_day_ymd(ymd: str, holidays: FrozenSet[str], max_lookback_days: int = 30) -> str:
    """
    Most recent trading day strictly before `ymd` (weekends + manual holiday
    list). Returns "" if none found within `max_lookback_days`.
    """
    dt = datetime.strptime(ymd, "%Y%m%d")
    for _ in range(max_lookback_days):
        dt -= timedelta(days=1)
        cand = dt.strftime("%Y%m%d")
        if not _is_weekend_ymd(cand) and cand not in holidays:
            return cand
    return ""


def should_run_bot_today_kst(ymd: str, settings: Settings) -> Tuple[bool, str]:
    """
    토·일 및 수동 목록에 있는 날이면 실행하지 않습니다.
    목록에 없는 평일은 항상 실행(개장일로 간주) — 개장일 오판 차단 없음.
    """
    if _is_weekend_ymd(ymd):
        return False, "오늘은 휴장일입니다. 프로그램을 종료합니다."

    holidays = load_manual_holiday_set(settings.holiday_dates_path)
    if ymd in holidays:
        return False, "오늘은 휴장일입니다. 프로그램을 종료합니다."

    return True, ""
