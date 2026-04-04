"""
KIS 일별 주문·체결 조회(매매내역)만 사용 — 잔고/보유는 사용하지 않음(T+2 등과 불일치 가능).

  python -m scripts.build_result
  python -m scripts.build_result --date 20260403
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.result_csv import (
    append_result_rows,
    build_daily_rows_from_kis_range,
    kis_rows_to_execs,
    kis_rows_to_symbol_names,
)


def main() -> None:
    settings.validate()
    p = argparse.ArgumentParser(description="result.csv 일별 매매사이클 반영")
    p.add_argument("--date", dest="ymd", help="YYYYMMDD (기본: 오늘 KST)")
    args = p.parse_args()
    if args.ymd:
        ymd = args.ymd
    else:
        ymd = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    lookback = max(1, min(90, int(settings.result_csv_kis_lookback_days)))
    end_dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
    start_ymd = (end_dt - timedelta(days=lookback)).strftime("%Y%m%d")
    api = KISApiClient(settings=settings)
    kis_rows = api.get_daily_order_executions(start_ymd, ymd)
    execs = kis_rows_to_execs(kis_rows)
    daily_rows = build_daily_rows_from_kis_range(execs, ymd)
    names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=settings.symbol_master_auto_refresh,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )
    kis_names = kis_rows_to_symbol_names(kis_rows)
    append_result_rows(
        settings.result_csv_path, daily_rows, names, kis_symbol_names=kis_names
    )
    print(
        f"result.csv: {ymd} {len(daily_rows)}건 추가 (KIS {start_ymd}~{ymd}) "
        f"[KIS raw {len(kis_rows)}행 → exec {len(execs)}건] -> {settings.result_csv_path}"
    )


if __name__ == "__main__":
    main()
