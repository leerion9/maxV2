"""
result_1.csv 초기 생성 / 재생성 스크립트.

수수료·세금 계산값을 포함한 순손익(net P&L) 버전으로, 지정 기간의 체결 내역
전체를 FIFO 처리해 result_1.csv를 새로 작성한다(기존 파일 덮어쓰기).

사용법:
  python -m scripts.build_result1 --from-date 20260413
  python -m scripts.build_result1 --from-date 20260413 --to-date 20260425
  python -m scripts.build_result1 --from-date 20260413 --output C:/path/to/result_1.csv

수수료·세금 계산 기준:
  매수 수수료 = 총매수금액 × fee_rate_buy  (기본 0.015%, 원 단위 절사)
  매도 수수료 = 총매도금액 × fee_rate_sell (기본 0.015%, 원 단위 절사)
  거래세(+농특세) = 총매도금액 × tax_rate_sell (기본 0.18%, 원 단위 절사)
  순손익 = 총매도금액 - 총매수금액 - 수수료 합계 - 거래세
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.result_csv import (
    build_all_rows_from_range,
    create_result1_from_scratch,
    kis_rows_to_execs,
    kis_rows_to_symbol_names,
)


def main() -> None:
    settings.validate()
    p = argparse.ArgumentParser(description="result_1.csv 초기 생성 (수수료·세금 포함 순손익)")
    p.add_argument(
        "--from-date",
        dest="from_ymd",
        required=True,
        help="집계 시작일 YYYYMMDD (해당 날짜 이후 매도 완료 또는 미청산 매수 포함)",
    )
    p.add_argument(
        "--to-date",
        dest="to_ymd",
        default=None,
        help="집계 종료일 YYYYMMDD (기본: 오늘 KST)",
    )
    p.add_argument(
        "--output",
        dest="output",
        default=None,
        help="출력 경로 (기본: settings.result1_csv_path)",
    )
    args = p.parse_args()

    from_ymd: str = args.from_ymd
    to_ymd: str = args.to_ymd or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    output_path = Path(args.output) if args.output else settings.result1_csv_path

    # from_date 보다 30일 앞부터 조회해 FIFO 매수분 누락 방지
    from_dt = datetime.strptime(from_ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
    lookback_start = (from_dt - timedelta(days=30)).strftime("%Y%m%d")

    api = KISApiClient(settings=settings)
    print(f"KIS 체결 조회: {lookback_start} ~ {to_ymd}")
    kis_rows = api.get_daily_order_executions(lookback_start, to_ymd)
    print(f"  → raw {len(kis_rows)}행")

    execs = kis_rows_to_execs(kis_rows)
    print(f"  → 유효 체결 {len(execs)}건")

    rows = build_all_rows_from_range(execs, from_ymd)
    closed = sum(1 for r in rows if r["kind"] == "CLOSED")
    open_ = sum(1 for r in rows if r["kind"] == "OPEN")
    print(f"  → {from_ymd} 이후: 청산완료 {closed}건, 미청산 {open_}건")

    names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=settings.symbol_master_auto_refresh,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )
    kis_names = kis_rows_to_symbol_names(kis_rows)

    create_result1_from_scratch(
        output_path,
        rows,
        names,
        fee_rate_buy=settings.fee_rate_buy,
        fee_rate_sell=settings.fee_rate_sell,
        tax_rate_sell=settings.tax_rate_sell,
        kis_symbol_names=kis_names,
    )
    print(
        f"\nresult_1.csv 생성 완료: {len(rows)}행 ({closed}건 청산 + {open_}건 미청산)\n"
        f"  수수료율: 매수 {settings.fee_rate_buy*100:.4f}% / 매도 {settings.fee_rate_sell*100:.4f}%\n"
        f"  거래세율: {settings.tax_rate_sell*100:.4f}%\n"
        f"  출력: {output_path}"
    )


if __name__ == "__main__":
    main()
