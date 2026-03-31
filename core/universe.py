from __future__ import annotations

import logging
from typing import List

from core.api_client import KISApiClient

_log = logging.getLogger("maxv")


class UniverseBuilder:
    def __init__(self, api: KISApiClient, top_ratio: float = 0.1) -> None:
        self.api = api
        self.top_ratio = top_ratio

    def build(self) -> List[str]:
        symbols = self._get_top_market_cap_symbols()
        selected: List[str] = []
        failed_ma5 = 0
        failed_daily = 0
        for symbol in symbols:
            ok, reason = self._is_above_ma5(symbol)
            if ok:
                selected.append(symbol)
            elif reason == "ma5":
                failed_ma5 += 1
            else:
                failed_daily += 1
        _log.info(
            "Universe filter: top_cap=%s candidates, ma5_pass=%s, ma5_fail=%s, daily_fail=%s",
            len(symbols),
            len(selected),
            failed_ma5,
            failed_daily,
        )
        return selected

    def _get_top_market_cap_symbols(self) -> List[str]:
        ranked = self.api.get_market_cap_rankings()
        if not ranked:
            _log.info("Universe filter: market-cap ranking returned 0 symbols")
            return []
        top_n = max(1, int(len(ranked) * self.top_ratio))
        _log.info(
            "Universe filter: market-cap ranked=%s, top_ratio=%s -> top_n=%s",
            len(ranked),
            self.top_ratio,
            top_n,
        )
        return ranked[:top_n]

    def _is_above_ma5(self, symbol: str) -> tuple[bool, str]:
        """
        Daily bars from KIS are newest-first. For pre-open runs, rows[0] is the
        last session close ('yesterday'). MA5 is the mean of that bar and the
        prior four closes: rows[0:5]. Require close[0] > MA5 (strictly above).
        """
        try:
            rows = self.api.get_daily_prices(symbol=symbol, days=6)
        except Exception:  # noqa: BLE001
            return False, "daily"
        if len(rows) < 5:
            return False, "daily"
        closes = [r["close"] for r in rows]
        ref_close = closes[0]
        ma5 = sum(closes[0:5]) / 5
        if ref_close > ma5:
            return True, ""
        return False, "ma5"
