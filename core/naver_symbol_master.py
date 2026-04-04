"""
Naver 시총 페이지(sise_market_sum)에서 코스피/코스닥 전체 종목코드·종목명 수집.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

_log = logging.getLogger("maxv")

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}
_MARKET_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
_MAX_PAGES_PER_MARKET = 120


def _parse_market_sum_codes_names(html: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.type_2")
    if not table:
        return []
    out: List[Tuple[str, str]] = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        a = tr.select_one("a[href*='main.naver?code=']")
        if not a:
            continue
        m = re.search(r"code=(\d{6})", a.get("href", ""))
        if not m:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        out.append((m.group(1), name))
    return out


def fetch_kr_symbol_master(delay_sec: float = 0.05) -> Dict[str, str]:
    """
    코스피(sosok=0)·코스닥(sosok=1) 시총 페이지를 페이지네이션하며 code->name 맵을 구축.
    """
    session = requests.Session()
    session.headers.update(_UA)
    merged: Dict[str, str] = {}
    for sosok in (0, 1):
        for page in range(1, _MAX_PAGES_PER_MARKET + 1):
            resp = session.get(
                _MARKET_SUM_URL,
                params={"sosok": sosok, "page": page},
                timeout=20,
            )
            resp.encoding = "euc-kr"
            resp.raise_for_status()
            rows = _parse_market_sum_codes_names(resp.text)
            if not rows:
                break
            for code, name in rows:
                merged[code] = name
            time.sleep(delay_sec)
    _log.info("Naver symbol master: %s codes", len(merged))
    return merged


def save_symbol_master(path: Path, symbols: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "naver_sise_market_sum",
        "count": len(symbols),
        "symbols": dict(sorted(symbols.items(), key=lambda x: x[0])),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_symbol_master(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        syms = raw.get("symbols")
        if isinstance(syms, dict):
            return {str(k).strip(): str(v).strip() for k, v in syms.items() if str(k).strip()}
    except Exception:
        return {}
    return {}


def symbol_master_needs_refresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return True
    if max_age_days <= 0:
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        updated = str(raw.get("updated_at", "") or "").strip()
        if not updated:
            return True
        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        return age > timedelta(days=max_age_days)
    except Exception:
        return True


def load_or_refresh_symbol_master(
    path: Path,
    *,
    auto_refresh: bool,
    max_age_days: int,
    delay_sec: float,
) -> Dict[str, str]:
    """
    JSON 로드. auto_refresh이고 파일이 없거나 max_age_days보다 오래됐으면 네이버에서 받아 저장.
    """
    if auto_refresh and symbol_master_needs_refresh(path, max_age_days):
        merged = fetch_kr_symbol_master(delay_sec=delay_sec)
        save_symbol_master(path, merged)
    return load_symbol_master(path)
