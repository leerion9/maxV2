"""
Naver Finance HTML scrape for dev/compare: full market-cap universe vs KIS output.
Same MA5 rule as UniverseBuilder (newest-first closes, ref=close[0], MA5=mean([0:5])).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Tuple, TypedDict

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from datetime import datetime

_log = logging.getLogger("maxv")

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}
_MARKET_SUM_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
_DAY_URL = "https://finance.naver.com/item/sise_day.naver"
_MAX_PAGES_PER_MARKET = 120


class DailyBar(TypedDict):
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int


def passes_ma5_newest_first(closes: List[int]) -> bool:
    if len(closes) < 5:
        return False
    ref = closes[0]
    ma5 = sum(closes[0:5]) / 5
    return ref > ma5


def _today_yyyymmdd_dot_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d")


def _select_ref_index_latest_closed(bars: List[DailyBar], today_dot: str) -> int:
    """
    Naver day page can show today's (in-progress) bar as the first row during market hours.
    For strategy/universe prep we want the latest *closed* session bar.

    Returns:
        index of the bar to treat as "latest closed" (0 or 1 typically).
    """
    if not bars:
        return 0
    if bars[0].get("date", "") == today_dot and len(bars) >= 2:
        return 1
    return 0


def _parse_market_sum_page(html: str) -> List[Tuple[str, int]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.type_2")
    if not table:
        return []
    out: List[Tuple[str, int]] = []
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
        cap_txt = tds[6].get_text(strip=True).replace(",", "")
        if not cap_txt.isdigit():
            continue
        out.append((m.group(1), int(cap_txt)))
    return out


def _fetch_ranked_symbols_merged(session: requests.Session, delay_sec: float) -> List[str]:
    merged: Dict[str, int] = {}
    for sosok in (0, 1):
        for page in range(1, _MAX_PAGES_PER_MARKET + 1):
            resp = session.get(
                _MARKET_SUM_URL,
                params={"sosok": sosok, "page": page},
                timeout=15,
            )
            resp.encoding = "euc-kr"
            resp.raise_for_status()
            rows = _parse_market_sum_page(resp.text)
            if not rows:
                break
            for code, cap in rows:
                merged[code] = max(merged.get(code, 0), cap)
            time.sleep(delay_sec)
    ordered = [c for c, _ in sorted(merged.items(), key=lambda x: -x[1])]
    return ordered


def _fetch_daily_bars(session: requests.Session, symbol: str) -> List[DailyBar]:
    resp = session.get(_DAY_URL, params={"code": symbol, "page": 1}, timeout=15)
    resp.encoding = "euc-kr"
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.select_one("table.type2")
    if not table:
        return []
    bars: List[DailyBar] = []
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        d0 = tds[0].get_text(strip=True)
        if not re.match(r"\d{4}\.\d{2}\.\d{2}", d0):
            continue
        close_txt = tds[1].get_text(strip=True).replace(",", "")
        open_txt = tds[3].get_text(strip=True).replace(",", "")
        high_txt = tds[4].get_text(strip=True).replace(",", "")
        low_txt = tds[5].get_text(strip=True).replace(",", "")
        vol_txt = tds[6].get_text(strip=True).replace(",", "")
        if not (
            close_txt.isdigit()
            and open_txt.isdigit()
            and high_txt.isdigit()
            and low_txt.isdigit()
            and vol_txt.isdigit()
        ):
            continue
        bars.append(
            {
                "date": d0,
                "open": int(open_txt),
                "high": int(high_txt),
                "low": int(low_txt),
                "close": int(close_txt),
                "volume": int(vol_txt),
            }
        )
    return bars


def build_naver_universe(top_ratio: float, delay_sec: float) -> Tuple[List[str], Dict[str, int]]:
    """
    Returns (symbols_after_ma5, stats) using Naver market-cap ranking + MA5 filter.
    """
    session = requests.Session()
    session.headers.update(_UA)

    ranked = _fetch_ranked_symbols_merged(session, delay_sec=delay_sec)
    if not ranked:
        return [], {"naver_ranked": 0, "top_n": 0, "ma5_pass": 0, "daily_fail": 0}

    top_n = max(1, int(len(ranked) * top_ratio))
    candidates = ranked[:top_n]
    selected: List[str] = []
    daily_fail = 0
    today_dot = _today_yyyymmdd_dot_kst()
    for symbol in candidates:
        try:
            bars = _fetch_daily_bars(session, symbol)
            time.sleep(delay_sec)
            ref_i = _select_ref_index_latest_closed(bars, today_dot=today_dot)
            closes = [b["close"] for b in bars[ref_i : ref_i + 5]]
            if len(closes) < 5:
                daily_fail += 1
                continue
            if passes_ma5_newest_first(closes):
                selected.append(symbol)
        except Exception:  # noqa: BLE001
            daily_fail += 1
            continue

    stats = {
        "naver_ranked": len(ranked),
        "top_n": top_n,
        "ma5_pass": len(selected),
        "daily_fail": daily_fail,
    }
    _log.info(
        "Naver universe (compare): ranked=%s top_ratio=%s -> top_n=%s ma5_pass=%s daily_fail=%s",
        len(ranked),
        top_ratio,
        top_n,
        len(selected),
        daily_fail,
    )
    return selected, stats


def build_naver_universe_with_features(
    top_ratio: float, delay_sec: float
) -> Tuple[List[str], Dict[str, Dict[str, int]], Dict[str, int]]:
    """
    Universe + per-symbol features needed for strategy prep (no KIS daily dependency).

    Returns (symbols_after_ma5, features, stats).
    features[symbol] has avg_volume_5d, prev_high, prev_low.
    """
    session = requests.Session()
    session.headers.update(_UA)

    ranked = _fetch_ranked_symbols_merged(session, delay_sec=delay_sec)
    if not ranked:
        return [], {}, {"naver_ranked": 0, "top_n": 0, "ma5_pass": 0, "daily_fail": 0}

    top_n = max(1, int(len(ranked) * top_ratio))
    candidates = ranked[:top_n]

    selected: List[str] = []
    features: Dict[str, Dict[str, int]] = {}
    daily_fail = 0
    today_dot = _today_yyyymmdd_dot_kst()
    for symbol in candidates:
        try:
            bars = _fetch_daily_bars(session, symbol)
            time.sleep(delay_sec)
            ref_i = _select_ref_index_latest_closed(bars, today_dot=today_dot)
            if len(bars) < (ref_i + 5):
                daily_fail += 1
                continue
            closes = [b["close"] for b in bars[ref_i : ref_i + 5]]
            if not passes_ma5_newest_first(closes):
                continue

            vols = [b["volume"] for b in bars[ref_i : ref_i + 5]]
            avg_vol_5d = int(sum(vols) / 5)
            prev = bars[ref_i]
            selected.append(symbol)
            features[symbol] = {
                "avg_volume_5d": avg_vol_5d,
                "prev_high": int(prev["high"]),
                "prev_low": int(prev["low"]),
            }
        except Exception:  # noqa: BLE001
            daily_fail += 1
            continue

    stats = {
        "naver_ranked": len(ranked),
        "top_n": top_n,
        "ma5_pass": len(selected),
        "daily_fail": daily_fail,
    }
    _log.info(
        "Naver universe: ranked=%s top_ratio=%s -> top_n=%s ma5_pass=%s daily_fail=%s",
        len(ranked),
        top_ratio,
        top_n,
        len(selected),
        daily_fail,
    )
    return selected, features, stats


def format_symbol_diff(a: List[str], b: List[str], limit: int = 40) -> Tuple[str, str]:
    sa, sb = set(a), set(b)
    only_a = sorted(sa - sb)
    only_b = sorted(sb - sa)
    a_str = ",".join(only_a[:limit]) + ("..." if len(only_a) > limit else "")
    b_str = ",".join(only_b[:limit]) + ("..." if len(only_b) > limit else "")
    return a_str, b_str
