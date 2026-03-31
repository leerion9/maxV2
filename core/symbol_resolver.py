from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup


@dataclass
class SymbolResolver:
    """
    Resolve symbol -> Korean name using Naver Finance, with a persistent cache.

    - Designed to be safe in a hot loop: fetching is throttled and optional.
    - If name is unknown and fetch is disabled, returns "".
    """

    cache_path: Path = Path("data") / "symbol_names.json"
    min_fetch_interval_sec: float = 1.5
    request_timeout_sec: float = 8.0

    def __post_init__(self) -> None:
        self._cache: Dict[str, str] = {}
        self._last_fetch_ts: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            }
        )
        self._load()

    def _load(self) -> None:
        try:
            if self.cache_path.exists():
                raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._cache = {str(k): str(v) for k, v in raw.items() if str(v).strip()}
        except Exception:
            self._cache = {}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_cached(self, symbol: str) -> str:
        return self._cache.get(symbol, "")

    def get_name(self, symbol: str, *, fetch: bool = False) -> str:
        symbol = str(symbol).strip()
        if not symbol:
            return ""
        cached = self._cache.get(symbol, "")
        if cached:
            return cached
        if not fetch:
            return ""
        return self._fetch_and_cache(symbol) or ""

    def _fetch_and_cache(self, symbol: str) -> Optional[str]:
        now = time.time()
        if now - self._last_fetch_ts < self.min_fetch_interval_sec:
            return None
        self._last_fetch_ts = now

        url = "https://finance.naver.com/item/main.naver"
        try:
            resp = self._session.get(
                url,
                params={"code": symbol},
                timeout=self.request_timeout_sec,
            )
            resp.encoding = "euc-kr"
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            h2 = soup.select_one("div.wrap_company h2 a")
            name = (h2.get_text(strip=True) if h2 else "").strip()
            if not name:
                title = soup.select_one("title")
                t = (title.get_text(strip=True) if title else "").strip()
                if t:
                    name = t.split(" : ", maxsplit=1)[0].strip()
            if not name:
                return None
            self._cache[symbol] = name
            self._save()
            return name
        except Exception:
            return None

