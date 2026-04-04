"""
네이버 시총 페이지에서 코스피·코스닥 종목코드·종목명 수집 후 JSON 저장 (주 1회 등).

  python -m scripts.update_symbol_master
"""

from __future__ import annotations

from config.settings import settings
from core.naver_symbol_master import fetch_kr_symbol_master, save_symbol_master


def main() -> None:
    settings.validate()
    m = fetch_kr_symbol_master(delay_sec=settings.naver_http_delay_sec)
    save_symbol_master(settings.symbol_master_path, m)
    print(f"saved {len(m)} symbols -> {settings.symbol_master_path}")


if __name__ == "__main__":
    main()
