# -*- coding: utf-8 -*-
"""Naver Finance theme list + member scrape (weekly map refresh)."""

from __future__ import annotations

import csv
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup

_log = logging.getLogger("maxv")

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}
_THEME_LIST_URL = "https://finance.naver.com/sise/theme.naver"
_THEME_DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver"
_CODE_RE = re.compile(r"code=(\d{6})")

THEME_MAP_FIELDS = [
    "theme_id",
    "theme_name",
    "symbol",
    "name",
    "n_members",
    "eligible",
    "updated_ymd",
]


@dataclass(frozen=True)
class ThemeMember:
    theme_id: str
    theme_name: str
    symbol: str
    name: str


def _get_html(url: str, *, params: Optional[dict] = None, timeout: float = 15.0) -> str:
    resp = requests.get(url, headers=_UA, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "euc-kr"
    return resp.text


def fetch_theme_index(*, max_pages: int = 8, delay_sec: float = 0.05) -> List[Tuple[str, str]]:
    """Return [(theme_id, theme_name), ...] from Naver theme pages."""
    out: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        html = _get_html(_THEME_LIST_URL, params={"page": page})
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.type_1.theme")
        if table is None:
            break
        page_hits = 0
        for a in table.select("td.col_type1 a"):
            href = a.get("href") or ""
            m = re.search(r"no=(\d+)", href)
            if not m:
                continue
            tid = m.group(1)
            name = a.get_text(strip=True)
            if not name or tid in seen:
                continue
            seen.add(tid)
            out.append((tid, name))
            page_hits += 1
        if page_hits == 0:
            break
        time.sleep(max(0.0, delay_sec))
    return out


def fetch_theme_members(theme_id: str, theme_name: str = "") -> List[ThemeMember]:
    html = _get_html(_THEME_DETAIL_URL, params={"type": "theme", "no": theme_id})
    soup = BeautifulSoup(html, "html.parser")
    members: List[ThemeMember] = []
    seen: set[str] = set()
    for a in soup.select("div#contentarea table a"):
        href = a.get("href") or ""
        m = _CODE_RE.search(href)
        if not m:
            continue
        symbol = m.group(1)
        name = a.get_text(strip=True)
        if not name or symbol in seen:
            continue
        seen.add(symbol)
        members.append(
            ThemeMember(
                theme_id=str(theme_id),
                theme_name=theme_name,
                symbol=symbol,
                name=name,
            )
        )
    return members


def build_theme_map_rows(
    *,
    max_members: int = 12,
    max_pages: int = 8,
    delay_sec: float = 0.05,
    updated_ymd: str,
) -> List[Dict[str, str]]:
    """Scrape all themes; mark eligible when 1 <= n_members <= max_members."""
    index = fetch_theme_index(max_pages=max_pages, delay_sec=delay_sec)
    rows: List[Dict[str, str]] = []
    for tid, tname in index:
        members = fetch_theme_members(tid, tname)
        n = len(members)
        eligible = "1" if 1 <= n <= int(max_members) else "0"
        for m in members:
            rows.append(
                {
                    "theme_id": m.theme_id,
                    "theme_name": m.theme_name,
                    "symbol": m.symbol,
                    "name": m.name,
                    "n_members": str(n),
                    "eligible": eligible,
                    "updated_ymd": updated_ymd,
                }
            )
        time.sleep(max(0.0, delay_sec))
        _log.info("theme %s (%s): members=%d eligible=%s", tid, tname, n, eligible)
    return rows


def write_theme_map_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.DictWriter(fp, fieldnames=THEME_MAP_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in THEME_MAP_FIELDS})


def load_theme_map_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as fp:
        return [dict(r) for r in csv.DictReader(fp)]
