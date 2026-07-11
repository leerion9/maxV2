# -*- coding: utf-8 -*-
"""Theme-map follower strategy (paper): hot theme -> laggard day-high break."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.api_client import Quote
from core.naver_theme import load_theme_map_csv
from core.pace_gate import interpolate_f


@dataclass
class ThemeInfo:
    theme_id: str
    theme_name: str
    symbols: List[str]
    n_members: int
    eligible: bool


@dataclass
class ThemeStockState:
    theme_id: str
    prev_close: int
    day_high: int = 0
    day_ret: float = 0.0
    bought: bool = False
    entry_price: int = 0
    day_high_since_entry: int = 0


@dataclass
class ThemeMapEntry:
    symbol: str
    theme_id: str
    theme_name: str
    entry_price: int
    trigger_price: int
    stock_ret: float
    theme_median_ret: float
    theme_score: float
    pace_ratio: float
    role: str = "follower"


@dataclass
class ThemeMapExit:
    symbol: str
    exit_price: int
    reason: str  # STOP | TRAIL | TIME | THEME_COLLAPSE | FORCE_CLOSE


@dataclass
class ThemeMapRegistry:
    """Loaded weekly CSV: theme_id -> members; symbol -> primary theme."""

    themes: Dict[str, ThemeInfo] = field(default_factory=dict)
    primary_theme: Dict[str, str] = field(default_factory=dict)
    updated_ymd: str = ""

    @classmethod
    def from_csv(
        cls,
        path: Path,
        *,
        max_members: int = 12,
        min_members: int = 4,
    ) -> "ThemeMapRegistry":
        rows = load_theme_map_csv(path)
        by_theme: Dict[str, ThemeInfo] = {}
        updated = ""
        for row in rows:
            tid = str(row.get("theme_id", "")).strip()
            sym = str(row.get("symbol", "")).strip().zfill(6)
            if not tid or not sym.isdigit():
                continue
            updated = str(row.get("updated_ymd", "")).strip() or updated
            n = int(float(row.get("n_members", 0) or 0))
            # Prefer file flag; recompute guard with max_members.
            eligible_flag = str(row.get("eligible", "0")).strip() in ("1", "true", "True")
            eligible = eligible_flag and (min_members <= n <= max_members)
            info = by_theme.get(tid)
            if info is None:
                info = ThemeInfo(
                    theme_id=tid,
                    theme_name=str(row.get("theme_name", "")).strip(),
                    symbols=[],
                    n_members=n,
                    eligible=eligible,
                )
                by_theme[tid] = info
            if sym not in info.symbols:
                info.symbols.append(sym)
            info.n_members = max(info.n_members, n, len(info.symbols))
            info.eligible = eligible and (min_members <= info.n_members <= max_members)

        # Primary theme: among eligible themes containing the symbol, pick smallest n_members.
        membership: Dict[str, List[str]] = {}
        for tid, info in by_theme.items():
            if not info.eligible:
                continue
            for sym in info.symbols:
                membership.setdefault(sym, []).append(tid)

        primary: Dict[str, str] = {}
        for sym, tids in membership.items():
            tids_sorted = sorted(
                tids,
                key=lambda t: (by_theme[t].n_members, by_theme[t].theme_id),
            )
            primary[sym] = tids_sorted[0]

        return cls(themes=by_theme, primary_theme=primary, updated_ymd=updated)

    def eligible_themes(self) -> List[ThemeInfo]:
        return [t for t in self.themes.values() if t.eligible]

    def watch_symbols(self) -> List[str]:
        return sorted(self.primary_theme.keys())


@dataclass
class ThemeMapStrategy:
    """
    Intraday theme contagion (paper):
      1) score eligible themes (rise ratio / median ret)
      2) hot if rise_ratio >= hot_ratio among ret >= hot_ret
      3) buy laggard (ret < median) on day-high break + optional pace
      4) same-day exit: stop / trail / time / theme collapse
    """

    registry: ThemeMapRegistry
    hot_ret: float = 0.02
    hot_ratio: float = 0.50
    min_members: int = 4
    entry_start_hhmm: str = "09:10"
    entry_end_hhmm: str = "14:30"
    stop_pct: float = 0.02
    trail_pct: float = 0.02
    force_exit_hhmm: str = "14:50"
    min_pace_ratio: float = 1.5
    max_themes: int = 2
    upper_limit_ret: float = 0.25
    symbol_state: Dict[str, ThemeStockState] = field(default_factory=dict)
    hot_themes: Set[str] = field(default_factory=set)
    active_theme_ids: Set[str] = field(default_factory=set)

    def register(self, symbol: str, prev_close: int) -> None:
        tid = self.registry.primary_theme.get(symbol)
        if not tid:
            return
        self.symbol_state[symbol] = ThemeStockState(
            theme_id=tid,
            prev_close=int(prev_close or 0),
        )

    def on_quote(
        self,
        quote: Quote,
        *,
        now_hhmm: str,
        value_ma5: int,
    ) -> Tuple[Optional[ThemeMapEntry], Optional[ThemeMapExit]]:
        state = self.symbol_state.get(quote.symbol)
        if state is None:
            return None, None

        px = int(quote.current_price or 0)
        if px <= 0:
            return None, None

        if state.prev_close > 0:
            state.day_ret = px / state.prev_close - 1.0
        else:
            state.day_ret = 0.0

        if state.bought:
            return None, self._check_exit(state, quote, now_hhmm)

        # Track day high for breakout trigger (before updating high this tick).
        prev_high = int(state.day_high)
        if px > state.day_high:
            state.day_high = px

        if now_hhmm < self.entry_start_hhmm or now_hhmm > self.entry_end_hhmm:
            return None, None

        self._refresh_theme_scores()

        tid = state.theme_id
        if tid not in self.hot_themes:
            return None, None
        if (
            tid not in self.active_theme_ids
            and len(self.active_theme_ids) >= self.max_themes
        ):
            return None, None

        med = self._theme_median_ret(tid)
        if state.day_ret >= med:
            return None, None  # not a laggard
        if state.day_ret >= self.upper_limit_ret:
            return None, None

        trigger = prev_high if prev_high > 0 else 0
        if trigger <= 0 or px <= trigger:
            return None, None

        pace = self._pace_ratio(quote.cum_value, value_ma5, now_hhmm)
        if pace < self.min_pace_ratio:
            return None, None

        theme = self.registry.themes.get(tid)
        score = self._theme_rise_ratio(tid)
        entry = ThemeMapEntry(
            symbol=quote.symbol,
            theme_id=tid,
            theme_name=theme.theme_name if theme else "",
            entry_price=px,
            trigger_price=trigger,
            stock_ret=state.day_ret,
            theme_median_ret=med,
            theme_score=score,
            pace_ratio=pace,
        )
        return entry, None

    def confirm_entry(self, symbol: str, entry_price: int) -> None:
        state = self.symbol_state.get(symbol)
        if state is None:
            return
        state.bought = True
        state.entry_price = int(entry_price)
        state.day_high_since_entry = int(entry_price)
        self.active_theme_ids.add(state.theme_id)

    def release_theme_if_flat(self, theme_id: str) -> None:
        still = any(
            s.bought and s.theme_id == theme_id for s in self.symbol_state.values()
        )
        if not still:
            self.active_theme_ids.discard(theme_id)

    def _check_exit(
        self, state: ThemeStockState, quote: Quote, now_hhmm: str
    ) -> Optional[ThemeMapExit]:
        px = int(quote.current_price)
        if px > state.day_high_since_entry:
            state.day_high_since_entry = px

        if state.entry_price > 0 and px <= int(state.entry_price * (1.0 - self.stop_pct)):
            return ThemeMapExit(symbol=quote.symbol, exit_price=px, reason="STOP")

        if state.day_high_since_entry > 0 and px <= int(
            state.day_high_since_entry * (1.0 - self.trail_pct)
        ):
            return ThemeMapExit(symbol=quote.symbol, exit_price=px, reason="TRAIL")

        if now_hhmm >= self.force_exit_hhmm:
            return ThemeMapExit(symbol=quote.symbol, exit_price=px, reason="TIME")

        # Theme collapse: was hot, now rise ratio weak / median negative.
        self._refresh_theme_scores()
        if state.theme_id not in self.hot_themes:
            med = self._theme_median_ret(state.theme_id)
            if med < 0 or self._theme_rise_ratio(state.theme_id) < self.hot_ratio * 0.5:
                return ThemeMapExit(
                    symbol=quote.symbol, exit_price=px, reason="THEME_COLLAPSE"
                )

        return None

    def mark_exited(self, symbol: str) -> None:
        state = self.symbol_state.get(symbol)
        if state is None:
            return
        tid = state.theme_id
        state.bought = False
        self.release_theme_if_flat(tid)

    def _refresh_theme_scores(self) -> None:
        hot: Set[str] = set()
        for info in self.registry.eligible_themes():
            rets = [
                self.symbol_state[s].day_ret
                for s in info.symbols
                if s in self.symbol_state and self.symbol_state[s].prev_close > 0
            ]
            if len(rets) < self.min_members:
                continue
            rise = sum(1 for r in rets if r >= self.hot_ret) / float(len(rets))
            if rise >= self.hot_ratio:
                hot.add(info.theme_id)
        self.hot_themes = hot

    def _theme_rets(self, theme_id: str) -> List[float]:
        info = self.registry.themes.get(theme_id)
        if info is None:
            return []
        return [
            self.symbol_state[s].day_ret
            for s in info.symbols
            if s in self.symbol_state and self.symbol_state[s].prev_close > 0
        ]

    def _theme_median_ret(self, theme_id: str) -> float:
        rets = sorted(self._theme_rets(theme_id))
        if not rets:
            return 0.0
        mid = len(rets) // 2
        if len(rets) % 2 == 1:
            return rets[mid]
        return 0.5 * (rets[mid - 1] + rets[mid])

    def _theme_rise_ratio(self, theme_id: str) -> float:
        rets = self._theme_rets(theme_id)
        if not rets:
            return 0.0
        return sum(1 for r in rets if r >= self.hot_ret) / float(len(rets))

    @staticmethod
    def _pace_ratio(cum_value: int, value_ma5: int, now_hhmm: str) -> float:
        f_t = interpolate_f(now_hhmm)
        if f_t <= 0 or value_ma5 <= 0:
            return 0.0
        return (float(cum_value) / f_t) / float(value_ma5)
