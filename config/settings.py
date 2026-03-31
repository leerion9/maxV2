from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_key: str = os.getenv("APP_KEY", "")
    app_secret: str = os.getenv("APP_SECRET", "")
    account_no: str = os.getenv("ACCOUNT_NO", "")
    account_prdt_cd: str = os.getenv("ACCOUNT_PRDT_CD", "01")

    is_paper_trading: bool = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
    base_url_paper: str = os.getenv(
        "BASE_URL_PAPER", "https://openapivts.koreainvestment.com:29443"
    )
    base_url_live: str = os.getenv(
        "BASE_URL_LIVE", "https://openapi.koreainvestment.com:9443"
    )

    top_market_cap_ratio: float = 0.1
    universe_source: str = os.getenv("UNIVERSE_SOURCE", "kis").strip().lower()
    max_positions: int = 5
    allocation_per_symbol: float = 0.2
    breakout_k: float = 0.7
    monitor_start_hhmm: str = "09:00"
    monitor_end_hhmm: str = "15:30"
    shutdown_hhmm: str = os.getenv("SHUTDOWN_HHMM", "15:40")
    liquidation_hhmm: str = "09:00"

    order_retry_count: int = 3
    request_timeout_sec: int = 8
    poll_interval_sec: int = 2
    heartbeat_sec: int = int(os.getenv("HEARTBEAT_SEC", "600") or "600")
    watchlist_sample_size: int = int(os.getenv("WATCHLIST_SAMPLE_SIZE", "10") or "10")
    market_holidays: str = os.getenv("MARKET_HOLIDAYS", "").strip()
    market_extra_open_days: str = os.getenv("MARKET_EXTRA_OPEN_DAYS", "").strip()
    require_local_kst: bool = os.getenv("REQUIRE_LOCAL_KST", "true").lower() == "true"

    fee_rate_buy: float = 0.00015
    fee_rate_sell: float = 0.00015
    tax_rate_sell: float = 0.0018

    log_dir: Path = ROOT_DIR / "data" / "logs"

    compare_universe_naver: bool = os.getenv("COMPARE_UNIVERSE_NAVER", "true").lower() == "true"
    naver_http_delay_sec: float = float(os.getenv("NAVER_HTTP_DELAY_SEC", "0.05") or "0.05")

    @property
    def base_url(self) -> str:
        return self.base_url_paper if self.is_paper_trading else self.base_url_live

    @property
    def cano(self) -> str:
        if "-" in self.account_no:
            return self.account_no.split("-", maxsplit=1)[0].strip()
        return self.account_no.strip()

    @property
    def acnt_prdt_cd(self) -> str:
        if "-" in self.account_no:
            tail = self.account_no.split("-", maxsplit=1)[1].strip()
            if tail:
                return tail
        return self.account_prdt_cd.strip()

    def validate(self) -> None:
        required = {
            "APP_KEY": self.app_key,
            "APP_SECRET": self.app_secret,
            "ACCOUNT_NO": self.account_no,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing .env values: {joined}")
        if len(self.cano) != 8 or not self.cano.isdigit():
            raise ValueError("ACCOUNT_NO 앞 8자리는 숫자 8자리여야 합니다. 예: 50177775-01")
        if len(self.acnt_prdt_cd) != 2 or not self.acnt_prdt_cd.isdigit():
            raise ValueError("계좌 상품코드는 숫자 2자리여야 합니다. 예: 01")
        if self.universe_source not in {"kis", "naver", "naver_then_kis"}:
            raise ValueError(
                "UNIVERSE_SOURCE는 kis|naver|naver_then_kis 중 하나여야 합니다."
            )
        if self.heartbeat_sec < 5:
            raise ValueError("HEARTBEAT_SEC는 5 이상이어야 합니다. 예: 60")
        if self.watchlist_sample_size < 0:
            raise ValueError("WATCHLIST_SAMPLE_SIZE는 0 이상이어야 합니다. 예: 10")
        if self.require_local_kst:
            offset = datetime_now_local_utc_offset()
            if offset != timedelta(hours=9):
                raise ValueError(
                    "현재 OS 로컬 시간대가 KST(+09:00)가 아닙니다. "
                    "실행 PC 시간대를 KST로 맞추거나 REQUIRE_LOCAL_KST=false로 설정하세요."
                )


settings = Settings()


def datetime_now_local_utc_offset() -> timedelta:
    from datetime import datetime

    return datetime.now().astimezone().utcoffset() or timedelta(0)
