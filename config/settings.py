from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() == "true"


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
    max_positions: int = 5
    allocation_per_symbol: float = 1.0 / 5
    breakout_k: float = 0.7

    # Parallel paper strategies (each has its own bankroll + ledger).
    enable_k_range: bool = _env_bool("ENABLE_K_RANGE", "true")
    enable_prev_high: bool = _env_bool("ENABLE_PREV_HIGH", "true")
    enable_opening_drive: bool = _env_bool("ENABLE_OPENING_DRIVE", "true")

    # Pace gate + paper observation mode (WORK_ORDER pace_gate)
    paper_mode: bool = os.getenv("PAPER_MODE", "true").lower() == "true"
    # Per-strategy paper capital (K / prev_high / OD each get this amount).
    paper_capital: int = int(os.getenv("PAPER_CAPITAL", "10000000") or "10000000")
    pace_threshold: float = float(os.getenv("PACE_THRESHOLD", "3.0") or "3.0")
    pace_entry_start_hhmm: str = os.getenv("PACE_ENTRY_START_HHMM", "09:10")
    pace_entry_end_hhmm: str = os.getenv("PACE_ENTRY_END_HHMM", "15:20")
    pace_chase_limit_mult: float = float(os.getenv("PACE_CHASE_LIMIT_MULT", "1.02") or "1.02")
    pace_upper_limit_mult: float = float(os.getenv("PACE_UPPER_LIMIT_MULT", "1.25") or "1.25")
    gate_log_min_interval_sec: int = int(os.getenv("GATE_LOG_MIN_INTERVAL_SEC", "60") or "60")
    value_profile_interval_sec: int = int(os.getenv("VALUE_PROFILE_INTERVAL_SEC", "300") or "300")
    # 익일 시가 소급 기입: 09:00 동시호가 이후에만 시가가 존재.
    paper_open_exit_fill_start_hhmm: str = os.getenv("PAPER_OPEN_EXIT_FILL_START_HHMM", "09:01")
    paper_open_exit_fill_deadline_hhmm: str = os.getenv(
        "PAPER_OPEN_EXIT_FILL_DEADLINE_HHMM", "09:30"
    )
    pace_log_dir: Path = ROOT_DIR / "logs"
    paper_ledger_k_name: str = "paper_ledger.csv"
    paper_ledger_prev_high_name: str = "paper_ledger_prev_high.csv"
    paper_ledger_od_name: str = "paper_ledger_opening_drive.csv"

    # Opening Drive (fixed mock set from HANDOFF)
    od_gap_min: float = float(os.getenv("OD_GAP_MIN", "0.015") or "0.015")
    od_gap_max: float = float(os.getenv("OD_GAP_MAX", "0.03") or "0.03")
    od_observe_end_hhmm: str = os.getenv("OD_OBSERVE_END_HHMM", "09:30")
    od_stop_pct: float = float(os.getenv("OD_STOP_PCT", "0.02") or "0.02")
    od_trail_pct: float = float(os.getenv("OD_TRAIL_PCT", "0.02") or "0.02")
    od_force_exit_hhmm: str = os.getenv("OD_FORCE_EXIT_HHMM", "11:00")
    od_min_pace_ratio: float = float(os.getenv("OD_MIN_PACE_RATIO", "1.5") or "1.5")
    od_max_positions: int = int(os.getenv("OD_MAX_POSITIONS", "5") or "5")

    monitor_start_hhmm: str = "09:00"
    monitor_end_hhmm: str = "15:30"
    shutdown_hhmm: str = os.getenv("SHUTDOWN_HHMM", "15:40")
    liquidation_hhmm: str = "08:50"

    order_retry_count: int = 3
    # KIS OpenAPI: burst 호출 시 "초당 거래건수 초과"(EGW00201) 방지
    kis_min_request_interval_sec: float = float(
        os.getenv("KIS_MIN_REQUEST_INTERVAL_SEC", "0.15") or "0.15"
    )
    kis_rate_limit_retry_sleep_sec: float = float(
        os.getenv("KIS_RATE_LIMIT_RETRY_SLEEP_SEC", "1.0") or "1.0"
    )
    kis_api_retry_max: int = int(os.getenv("KIS_API_RETRY_MAX", "8") or "8")
    request_timeout_sec: int = 8
    poll_interval_sec: int = 2
    heartbeat_sec: int = int(os.getenv("HEARTBEAT_SEC", "600") or "600")
    watchlist_sample_size: int = int(os.getenv("WATCHLIST_SAMPLE_SIZE", "10") or "10")

    fee_rate_buy: float = 0.00015
    fee_rate_sell: float = 0.00015
    tax_rate_sell: float = 0.0018

    # 모의/실전 모드별로 로그·result.csv를 물리적으로 분리한다.
    log_root_dir: Path = ROOT_DIR / "data" / "logs"
    symbol_master_path: Path = ROOT_DIR / "data" / "kr_symbol_master.json"
    # result.csv·봇 종료 시 마스터가 없거나 오래됐으면 네이버에서 갱신 (주 1회 등)
    symbol_master_auto_refresh: bool = (
        os.getenv("SYMBOL_MASTER_AUTO_REFRESH", "true").lower() == "true"
    )
    symbol_master_max_age_days: int = int(os.getenv("SYMBOL_MASTER_MAX_AGE_DAYS", "7") or "7")
    result_csv_on_shutdown: bool = os.getenv("RESULT_CSV_ON_SHUTDOWN", "true").lower() == "true"
    # KIS 일별체결 FIFO(전일 매수·당일 매도 짝)용 조회 시작일: 종료일 기준 N일 전 (3개월 이내 API 한도)
    result_csv_kis_lookback_days: int = int(os.getenv("RESULT_CSV_KIS_LOOKBACK_DAYS", "30") or "30")

    naver_http_delay_sec: float = float(os.getenv("NAVER_HTTP_DELAY_SEC", "0.05") or "0.05")

    # 평일 공휴일 등: 한 줄에 YYYYMMDD 하나. 토·일은 코드에서 별도 처리.
    holiday_dates_path: Path = Path(
        os.getenv("HOLIDAY_DATES_PATH", str(ROOT_DIR / "config" / "korea_market_holidays.txt"))
    )

    @property
    def base_url(self) -> str:
        return self.base_url_paper if self.is_paper_trading else self.base_url_live

    @property
    def mode_name(self) -> str:
        return "paper" if self.is_paper_trading else "live"

    @property
    def log_dir(self) -> Path:
        return self.log_root_dir / self.mode_name

    @property
    def result_csv_path(self) -> Path:
        return self.log_dir / "result.csv"

    @property
    def result1_csv_path(self) -> Path:
        return self.log_dir / "result_1.csv"

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
        if self.heartbeat_sec < 5:
            raise ValueError("HEARTBEAT_SEC는 5 이상이어야 합니다. 예: 60")
        if self.watchlist_sample_size < 0:
            raise ValueError("WATCHLIST_SAMPLE_SIZE는 0 이상이어야 합니다. 예: 10")
        if self.result_csv_kis_lookback_days < 1 or self.result_csv_kis_lookback_days > 90:
            raise ValueError("RESULT_CSV_KIS_LOOKBACK_DAYS는 1~90(3개월 이내)이어야 합니다.")
        if not (self.enable_k_range or self.enable_prev_high or self.enable_opening_drive):
            raise ValueError("ENABLE_K_RANGE / ENABLE_PREV_HIGH / ENABLE_OPENING_DRIVE 중 하나 이상 true")
        if self.od_gap_min >= self.od_gap_max:
            raise ValueError("OD_GAP_MIN must be < OD_GAP_MAX")


settings = Settings()
