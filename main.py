from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.logger import TradeLogger
from core.opening_drive import OpeningDriveStrategy
from core.order import OrderManager
from core.pace_collectors import (
    GateCsvLogger,
    LEDGER_DESC_K_RANGE,
    LEDGER_DESC_PREV_HIGH,
    OpeningDriveLedger,
    PaperLedger,
    ThemeMapLedger,
    ValueProfileLogger,
    calc_paper_qty,
)
from core.pace_gate import entry_block_reason, evaluate_pace_gate
from core.strategy import (
    BREAKOUT_MODE_K_RANGE,
    BREAKOUT_MODE_PREV_HIGH,
    VolatilityBreakoutStrategy,
)
from core.strategy_books import BreakoutBook
from core.theme_map import ThemeMapRegistry, ThemeMapStrategy
from core.trading_day import load_manual_holiday_set, prev_trading_day_ymd, should_run_bot_today_kst
from core.universe_cache import CachedSymbol, UniverseCache, cache_path, load_cache, save_cache, today_kst_yyyymmdd


_REPO_ROOT = Path(__file__).resolve().parent


class MaxVRunner:
    def __init__(self) -> None:
        settings.validate()
        self.api = KISApiClient(settings=settings)
        self.logger = TradeLogger(log_dir=settings.log_dir)
        self.order = OrderManager(api=self.api, settings=settings)
        self.symbol_features: Dict[str, CachedSymbol] = {}
        self.gate_logger = GateCsvLogger(
            log_dir=settings.pace_log_dir,
            min_interval_sec=settings.gate_log_min_interval_sec,
        )
        self.profile_logger = ValueProfileLogger(
            log_dir=settings.pace_log_dir,
            interval_sec=settings.value_profile_interval_sec,
        )

        # Parallel breakout arms (separate bankroll + ledger each).
        self.breakout_books: List[BreakoutBook] = []
        if settings.enable_k_range:
            self.breakout_books.append(
                BreakoutBook(
                    name="k_range",
                    strategy=VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_K_RANGE),
                    ledger=PaperLedger(
                        path=settings.pace_log_dir / settings.paper_ledger_k_name,
                        settings=settings,
                        desc_row=dict(LEDGER_DESC_K_RANGE),
                    ),
                    max_positions=settings.max_positions,
                )
            )
        if settings.enable_prev_high:
            self.breakout_books.append(
                BreakoutBook(
                    name="prev_high",
                    strategy=VolatilityBreakoutStrategy(breakout_mode=BREAKOUT_MODE_PREV_HIGH),
                    ledger=PaperLedger(
                        path=settings.pace_log_dir / settings.paper_ledger_prev_high_name,
                        settings=settings,
                        desc_row=dict(LEDGER_DESC_PREV_HIGH),
                    ),
                    max_positions=settings.max_positions,
                )
            )

        self.od_strategy = OpeningDriveStrategy(
            gap_min=settings.od_gap_min,
            gap_max=settings.od_gap_max,
            observe_end_hhmm=settings.od_observe_end_hhmm,
            stop_pct=settings.od_stop_pct,
            trail_pct=settings.od_trail_pct,
            force_exit_hhmm=settings.od_force_exit_hhmm,
            min_pace_ratio=settings.od_min_pace_ratio,
        )
        self.od_ledger = OpeningDriveLedger(
            path=settings.pace_log_dir / settings.paper_ledger_od_name,
            settings=settings,
        )
        self.od_ordered_symbols_today: set[str] = set()
        self.od_buy_orders_today: int = 0
        self.od_per_symbol_budget: int | None = None
        self.od_enabled = bool(settings.enable_opening_drive)

        self.theme_enabled = bool(settings.enable_theme_map)
        self.theme_registry = ThemeMapRegistry()
        self.theme_strategy = ThemeMapStrategy(registry=self.theme_registry)
        self.theme_ledger = ThemeMapLedger(
            path=settings.pace_log_dir / settings.paper_ledger_theme_name,
            settings=settings,
        )
        self.theme_watch_symbols: list[str] = []
        self.theme_ordered_symbols_today: set[str] = set()
        self.theme_buy_orders_today: int = 0
        self.theme_per_symbol_budget: int | None = None
        self._theme_force_flat_ymd: str = ""

        # Live-mode single-arm counters (paper uses per-book counters).
        self.ordered_symbols_today: set[str] = set()
        self.buy_orders_today: int = 0
        self.per_symbol_budget: int | None = None

        self.today_universe: List[str] = []

        # One-time cash snapshot at 09:00:05(KST).
        self.cash_snapshot_total: int | None = None
        self.cash_snapshot_done: bool = False
        self.cash_snapshot_failed: bool = False
        self._next_cash_retry_ts: float = 0.0
        # Retry interval: keep it gentle to avoid API rejection.
        self.cash_retry_interval_sec: float = 7.0
        self._last_heartbeat_ts: float = 0.0
        self._hb_cycles: int = 0
        self._hb_scanned: int = 0
        self._hb_quote_ok: int = 0
        self._hb_quote_err: int = 0
        self._hb_signals: int = 0
        self._hb_buys: int = 0
        self._hb_skipped_signals: int = 0
        self._should_stop: bool = False
        self._did_prepare_ymd: str = ""
        self._did_liquidate_ymd: str = ""
        self._last_monitor_ts: float = 0.0
        self._did_result_csv_ymd: str = ""
        self._did_same_day_close_ymd: str = ""
        # Paper mode: next-open exit backfill must run AFTER the 09:00 opening
        # auction (open price does not exist at 08:50). Retried until success
        # or the deadline; symbols still missing (e.g. trading halt) stay
        # unfilled and are picked up on a later session with exit_open_date
        # revealing the gap.
        self._open_exit_fill_done_ymd: str = ""
        self._next_open_exit_retry_ts: float = 0.0
        self._od_force_flat_ymd: str = ""
    def run(self) -> None:
        self._configure_console_utf8()

        now = self._now_kst_local()
        ymd_check = now.strftime("%Y%m%d")
        ok_day, holiday_msg = should_run_bot_today_kst(ymd_check, settings)
        if not ok_day:
            print(holiday_msg)
            self.logger.info(holiday_msg)
            return

        self.logger.info("MaxV started.")
        if settings.paper_mode:
            arms = [b.name for b in self.breakout_books]
            if self.od_enabled:
                arms.append("opening_drive")
            if self.theme_enabled:
                arms.append("theme_map")
            self.logger.info(
                "paper_mode=ON: KIS 주문 API 차단, 가상 체결·원장 운용 "
                f"(paper_capital/전략={settings.paper_capital:,} KRW, arms={arms})"
            )

        hhmm = now.strftime("%H:%M")
        if hhmm >= "15:30":
            print("장 종료 이후 시간입니다.")
            self.logger.info("장 종료 이후 시간입니다.")
            return

        # 3-phase startup:
        # - 00:00~08:49: universe prep, then wait; at 08:50 liquidate all holdings.
        # - 08:50~15:30: no auto-sell; resume monitoring (load cache if exists; otherwise build).
        if hhmm < settings.liquidation_hhmm:
            self.prepare_universe()
            self._did_prepare_ymd = now.strftime("%Y%m%d")
        else:
            # Intraday start: try cache first, otherwise build.
            self.prepare_universe()
            self._did_prepare_ymd = now.strftime("%Y%m%d")

        while not self._should_stop:
            self._tick()
            time.sleep(0.25)
        self.logger.info("MaxV stopped.")

    @staticmethod
    def _now_kst_local() -> datetime:
        # User operates this bot on KST local time.
        return datetime.now(ZoneInfo("Asia/Seoul"))

    def _tick(self) -> None:
        now = self._now_kst_local()
        ymd = now.strftime("%Y%m%d")
        hhmm = now.strftime("%H:%M")

        if settings.paper_mode:
            # 익일 시가 소급 기입: 시가는 09:00 동시호가 이후에만 존재하므로
            # 09:01부터 시도하고, 실패 시 60초 간격으로 마감시한까지 재시도.
            if (
                self._open_exit_fill_done_ymd != ymd
                and hhmm >= settings.paper_open_exit_fill_start_hhmm
            ):
                now_ts0 = time.time()
                if now_ts0 >= self._next_open_exit_retry_ts:
                    self._next_open_exit_retry_ts = now_ts0 + 60.0
                    all_filled = self._fill_paper_open_exits(ymd)
                    if all_filled:
                        self._open_exit_fill_done_ymd = ymd
                    elif hhmm >= settings.paper_open_exit_fill_deadline_hhmm:
                        self.logger.error(
                            "paper_mode: 익일 시가 소급 기입 마감시한 초과. "
                            "미기입 건은 다음 거래 재개일 시가로 채워지며 exit_open_date로 식별됩니다."
                        )
                        self._open_exit_fill_done_ymd = ymd
        elif hhmm >= settings.liquidation_hhmm and self._did_liquidate_ymd != ymd:
            self.liquidate_all_holdings_at_open()
            self._did_liquidate_ymd = ymd

        if hhmm >= settings.pace_entry_end_hhmm:
            if settings.paper_mode and self._did_same_day_close_ymd != ymd:
                self._fill_paper_same_day_close(ymd)
                self._did_same_day_close_ymd = ymd

        # Opening Drive: force-flat any still-open paper positions at force_exit time.
        if (
            settings.paper_mode
            and self.od_enabled
            and hhmm >= settings.od_force_exit_hhmm
            and self._od_force_flat_ymd != ymd
        ):
            self._od_force_flat_open(ymd)
            self._od_force_flat_ymd = ymd

        # Theme map: force-flat remaining opens at theme force-exit time.
        if (
            settings.paper_mode
            and self.theme_enabled
            and hhmm >= settings.theme_force_exit_hhmm
            and self._theme_force_flat_ymd != ymd
        ):
            self._theme_force_flat_open(ymd)
            self._theme_force_flat_ymd = ymd

        if hhmm >= settings.shutdown_hhmm:
            if self._did_result_csv_ymd != ymd:
                self._write_daily_result_csv(ymd)
                self._did_result_csv_ymd = ymd
            self.request_shutdown()
            return

        now_ts = time.time()
        if now_ts - self._last_monitor_ts >= settings.poll_interval_sec:
            self._last_monitor_ts = now_ts
            self.monitor_intraday()

    @staticmethod
    def _configure_console_utf8() -> None:
        try:
            import sys
            import os
            import platform

            os.environ.setdefault("PYTHONIOENCODING", "utf-8")

            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")

            if platform.system().lower() == "windows":
                try:
                    import ctypes

                    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
                    ctypes.windll.kernel32.SetConsoleCP(65001)
                except Exception:
                    pass
        except Exception:
            return

    def _maybe_heartbeat(self, phase: str, message: str) -> bool:
        now_ts = time.time()
        if now_ts - self._last_heartbeat_ts < settings.heartbeat_sec:
            return False
        self._last_heartbeat_ts = now_ts
        self.logger.info(message)
        return True

    def _reset_heartbeat_counters(self) -> None:
        self._hb_cycles = 0
        self._hb_scanned = 0
        self._hb_quote_ok = 0
        self._hb_quote_err = 0
        self._hb_signals = 0
        self._hb_buys = 0
        self._hb_skipped_signals = 0

    def prepare_universe(self) -> None:
        self.logger.info("Preparing universe...")

        cache_date = today_kst_yyyymmdd(self._now_kst_local())
        ucache_path = cache_path(Path("data"), cache_date)
        cache = load_cache(ucache_path)

        symbols: List[str] = []
        features: Dict[str, CachedSymbol] = {}

        if (
            cache is not None
            and cache.date_kst == cache_date
            and abs(cache.top_ratio - settings.top_market_cap_ratio) < 1e-9
            and abs(cache.breakout_k - settings.breakout_k) < 1e-9
        ):
            symbols = list(cache.symbols.keys())
            features = cache.symbols
            self.logger.info(
                f"Universe cache hit: {ucache_path.name} "
                f"({len(symbols)} symbols, v{cache.cache_version})"
            )

        if not symbols:
            try:
                from core.naver_universe import build_naver_universe_with_features

                self.logger.info(
                    "Building universe from Naver (scraping; may take ~1-2 min)..."
                )
                naver_syms, feat_raw, st = build_naver_universe_with_features(
                    top_ratio=settings.top_market_cap_ratio,
                    delay_sec=settings.naver_http_delay_sec,
                )
                symbols = naver_syms
                features = {
                    sym: CachedSymbol(
                        avg_volume_5d=int(row["avg_volume_5d"]),
                        prev_high=int(row["prev_high"]),
                        prev_low=int(row["prev_low"]),
                        value_ma5=int(row["value_ma5"]),
                        prev_close=int(row["prev_close"]),
                    )
                    for sym, row in feat_raw.items()
                }
                self.logger.info(
                    "value_ma5 method: sum(close*volume)/5 over latest 5 closed Naver sessions (KRW)"
                )
                self.logger.info(
                    f"Naver universe ready: {len(naver_syms)} symbols "
                    f"(ranked={st.get('naver_ranked', 0)} excluded_name={st.get('excluded', 0)} "
                    f"top_n={st.get('top_n', 0)} "
                    f"ma5_pass={st.get('ma5_pass', 0)} daily_fail={st.get('daily_fail', 0)})"
                )

                save_cache(
                    ucache_path,
                    UniverseCache(
                        date_kst=cache_date,
                        source="naver",
                        top_ratio=settings.top_market_cap_ratio,
                        breakout_k=settings.breakout_k,
                        created_at_iso=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
                            timespec="seconds"
                        ),
                        symbols=features,
                    ),
                )
                self.logger.info(f"Universe cache saved: {ucache_path.name}")
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Naver universe build failed: {exc}")
                symbols = []
                features = {}

        self.today_universe = symbols
        self.symbol_features = dict(features)
        for book in self.breakout_books:
            book.strategy = VolatilityBreakoutStrategy(breakout_mode=book.strategy.breakout_mode)
            book.reset_day_counters()
        self.od_strategy = OpeningDriveStrategy(
            gap_min=settings.od_gap_min,
            gap_max=settings.od_gap_max,
            observe_end_hhmm=settings.od_observe_end_hhmm,
            stop_pct=settings.od_stop_pct,
            trail_pct=settings.od_trail_pct,
            force_exit_hhmm=settings.od_force_exit_hhmm,
            min_pace_ratio=settings.od_min_pace_ratio,
        )
        self.od_ordered_symbols_today = set()
        self.od_buy_orders_today = 0
        self.ordered_symbols_today = set()
        self.buy_orders_today = 0
        self._reset_heartbeat_counters()
        self._prepare_theme_map()

        # Strategy prep: prefer cached/naver features, avoid KIS daily calls when available.
        # (pace-gate redesign: the strategy is price-only; volume screening is
        #  done exclusively by the realtime pace gate using value_ma5.)
        for symbol in symbols:
            feat = features.get(symbol)
            if feat is not None:
                for book in self.breakout_books:
                    book.strategy.register(
                        symbol=symbol,
                        prev_high=feat.prev_high,
                        prev_low=feat.prev_low,
                        breakout_k=settings.breakout_k,
                    )
                if self.od_enabled:
                    self.od_strategy.register(symbol=symbol, prev_close=feat.prev_close)
                continue

            try:
                rows = self.api.get_daily_prices(symbol=symbol, days=6)
                if len(rows) < 2:
                    continue
                avg_volume_5d = int(sum(r["volume"] for r in rows[:5]) / 5)
                prev_high = int(rows[0]["high"])
                prev_low = int(rows[0]["low"])
                prev_close = int(rows[0]["close"])
                value_ma5 = int(
                    sum(r["close"] * r["volume"] for r in rows[:5]) / 5
                )
                self.symbol_features[symbol] = CachedSymbol(
                    avg_volume_5d=avg_volume_5d,
                    prev_high=prev_high,
                    prev_low=prev_low,
                    value_ma5=value_ma5,
                    prev_close=prev_close,
                )
                for book in self.breakout_books:
                    book.strategy.register(
                        symbol=symbol,
                        prev_high=prev_high,
                        prev_low=prev_low,
                        breakout_k=settings.breakout_k,
                    )
                if self.od_enabled:
                    self.od_strategy.register(symbol=symbol, prev_close=prev_close)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Strategy prep failed {symbol}: {exc}")
                continue

        self.logger.info(f"Universe ready: {len(self.today_universe)} symbols")
        if self.today_universe and settings.watchlist_sample_size > 0:
            sample_n = min(len(self.today_universe), settings.watchlist_sample_size)
            sample = ",".join(self.today_universe[:sample_n])
            self.logger.info(f"감시대상 샘플 ({sample_n}/{len(self.today_universe)}): {sample}")
        arms = [b.name for b in self.breakout_books] + (
            ["opening_drive"] if self.od_enabled else []
        )
        if self.theme_enabled:
            arms.append("theme_map")
        self.logger.info(
            "감시 준비 완료. "
            f"{settings.monitor_start_hhmm}~{settings.monitor_end_hhmm} (KST) 구간에서 감시합니다. "
            f"(poll={settings.poll_interval_sec}s, heartbeat={settings.heartbeat_sec}s, arms={arms})"
        )

    def _prepare_theme_map(self) -> None:
        """Load weekly theme CSV and register watch symbols (eligible themes only)."""
        self.theme_ordered_symbols_today = set()
        self.theme_buy_orders_today = 0
        self.theme_watch_symbols = []
        if not self.theme_enabled:
            return
        path = settings.theme_map_path
        if not path.exists():
            self.logger.error(
                f"theme_map CSV 없음: {path} — "
                "금요일 장후 `python -m scripts.update_theme_map` 실행 필요. theme_map 비활성."
            )
            self.theme_enabled = False
            return
        self.theme_registry = ThemeMapRegistry.from_csv(
            path,
            max_members=settings.theme_max_members,
            min_members=settings.theme_min_members,
        )
        self.theme_strategy = ThemeMapStrategy(
            registry=self.theme_registry,
            hot_ret=settings.theme_hot_ret,
            hot_ratio=settings.theme_hot_ratio,
            min_members=settings.theme_min_members,
            entry_start_hhmm=settings.theme_entry_start_hhmm,
            entry_end_hhmm=settings.theme_entry_end_hhmm,
            stop_pct=settings.theme_stop_pct,
            trail_pct=settings.theme_trail_pct,
            force_exit_hhmm=settings.theme_force_exit_hhmm,
            min_pace_ratio=settings.theme_min_pace_ratio,
            max_themes=settings.theme_max_themes,
        )
        self.theme_watch_symbols = self.theme_registry.watch_symbols()
        # Ensure prev_close / value_ma5 for theme-only symbols.
        for symbol in self.theme_watch_symbols:
            feat = self.symbol_features.get(symbol)
            if feat is not None:
                self.theme_strategy.register(symbol, prev_close=feat.prev_close)
                continue
            try:
                rows = self.api.get_daily_prices(symbol=symbol, days=6)
                if len(rows) < 2:
                    continue
                prev_close = int(rows[0]["close"])
                value_ma5 = int(sum(r["close"] * r["volume"] for r in rows[:5]) / 5)
                avg_volume_5d = int(sum(r["volume"] for r in rows[:5]) / 5)
                self.symbol_features[symbol] = CachedSymbol(
                    avg_volume_5d=avg_volume_5d,
                    prev_high=int(rows[0]["high"]),
                    prev_low=int(rows[0]["low"]),
                    value_ma5=value_ma5,
                    prev_close=prev_close,
                )
                self.theme_strategy.register(symbol, prev_close=prev_close)
                time.sleep(0.05)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"theme prep failed {symbol}: {exc}")
        n_elig = len(self.theme_registry.eligible_themes())
        self.logger.info(
            f"theme_map ready: updated={self.theme_registry.updated_ymd} "
            f"eligible_themes={n_elig} watch={len(self.theme_watch_symbols)} "
            f"path={path.name}"
        )
    def liquidate_all_holdings_at_open(self) -> None:
        """
        Pre-open liquidation at 08:50 (KST): sell all current holdings.
        We intentionally do NOT check "previous-day buy" or holidays.
        """
        now = self._now_kst_local()
        now_hhmm = now.strftime("%H:%M")
        if now_hhmm < settings.liquidation_hhmm:
            return

        holdings = self._get_holdings_safely()
        to_sell: List[Dict[str, object]] = []
        for row in holdings:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).strip()
            qty = int(row.get("qty", 0) or 0)
            if not symbol or qty <= 0:
                continue
            to_sell.append({"symbol": symbol, "qty": qty})

        if not to_sell:
            self.logger.info(f"08:50 청산: 보유 종목 없음 (now={now_hhmm} KST)")
            return

        self.logger.info(
            f"08:50 청산(보유 전량): now={now_hhmm} KST count={len(to_sell)}"
        )
        for row in to_sell:
            symbol = str(row["symbol"])
            qty = int(row["qty"])
            try:
                result = self.order.place_open_liquidation(symbol=symbol, qty=qty)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"SELL submit failed {symbol}: {exc}")
                continue
            ord_no = str(result.get("ord_no", "") or "")
            self.logger.log_trade(
                {
                    "symbol": symbol,
                    "side": "SELL",
                    "qty": qty,
                    "price": 0,
                    "reason": "open_0850_liquidation_all",
                    "fee": "",
                    "tax": "",
                    "order_id": ord_no,
                    "cash_psbl": "",
                    "balance_tot_asset": "",
                    "balance_dnca": "",
                    "balance_json": "",
                }
            )
            self.logger.info(
                f"[매도요청] {symbol} qty={qty} 시장가(08:50 보유전량청산) "
                f"ord_no={ord_no}"
            )
            time.sleep(0.55)
        self.logger.info("08:50 청산 완료(주문발송). 이후 보유=0으로 가정합니다.")

    def monitor_intraday(self) -> None:
        now_dt = self._now_kst_local()
        now_kst = now_dt.strftime("%H:%M")
        ymd = now_dt.strftime("%Y%m%d")
        if now_kst < settings.monitor_start_hhmm or now_kst > settings.monitor_end_hhmm:
            if self.today_universe:
                self._maybe_heartbeat(
                    "waiting",
                    f"대기중... now={now_kst} KST watchlist={len(self.today_universe)} "
                    f"cash_snapshot={'Y' if self.cash_snapshot_done else 'N'}",
                )
            return
        if not self.today_universe:
            self._maybe_heartbeat("no_watchlist", f"감시대상이 아직 없습니다. 대기중... now={now_kst} KST")
            return

        if not self._ensure_cash_snapshot_for_today():
            hhmmss = now_dt.strftime("%H:%M:%S")
            snap_state = "WAIT" if hhmmss < "09:00:05" else "RETRY"
            self._maybe_heartbeat(
                "cash_snapshot_wait",
                f"감시중... now={now_kst} KST watchlist={len(self.today_universe)} "
                f"cash_snapshot={snap_state}(09:00:05~09:05 재시도)",
            )
            return

        if settings.paper_mode:
            if any(b.budget() <= 0 for b in self.breakout_books) or (
                self.od_enabled and int(self.od_per_symbol_budget or 0) <= 0
            ) or (
                self.theme_enabled and int(self.theme_per_symbol_budget or 0) <= 0
            ):
                self.logger.error("Per-symbol budget invalid. Keep monitoring only.")
                return
        else:
            budget = int(self.per_symbol_budget or 0)
            if budget <= 0:
                self.logger.error("Per-symbol budget invalid. Keep monitoring only.")
                return

        snapshot_profile = self.profile_logger.should_snapshot()
        profile_rows: List[Dict[str, object]] = []

        self._hb_cycles += 1
        cycle_scanned = 0
        cycle_quote_ok = 0
        cycle_quote_err = 0
        cycle_signals = 0
        cycle_buys = 0
        cycle_skipped_signals = 0

        poll_symbols = list(self.today_universe)
        if self.theme_enabled and self.theme_watch_symbols:
            seen = set(poll_symbols)
            for sym in self.theme_watch_symbols:
                if sym not in seen:
                    poll_symbols.append(sym)
                    seen.add(sym)

        for symbol in poll_symbols:
            cycle_scanned += 1
            try:
                quote = self.api.get_quote(symbol)
                cycle_quote_ok += 1
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"QUOTE failed {symbol}: {exc}")
                cycle_quote_err += 1
                continue

            quote_dt = self._now_kst_local()
            quote_hhmm = quote_dt.strftime("%H:%M")
            feat = self.symbol_features.get(symbol)
            if snapshot_profile and feat is not None and symbol in self.today_universe:
                profile_rows.append(
                    {
                        "ts": quote_dt.isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "current_price": quote.current_price,
                        "cum_volume": quote.volume,
                        "cum_value": quote.cum_value,
                        "prev_close": feat.prev_close,
                    }
                )

            # --- Theme map (paper only; same-day) ---
            if settings.paper_mode and self.theme_enabled and feat is not None:
                th_entry, th_exit = self.theme_strategy.on_quote(
                    quote, now_hhmm=quote_hhmm, value_ma5=feat.value_ma5
                )
                if th_exit is not None:
                    self._theme_paper_exit(
                        ymd=ymd,
                        symbol=symbol,
                        exit_price=th_exit.exit_price,
                        exit_reason=th_exit.reason,
                        exit_ts=quote_dt.isoformat(timespec="seconds"),
                    )
                elif th_entry is not None:
                    entered_th = self._theme_paper_buy(
                        ymd=ymd,
                        symbol=symbol,
                        entry=th_entry,
                        entry_ts=quote_dt.isoformat(timespec="seconds"),
                    )
                    if entered_th:
                        cycle_signals += 1
                        cycle_buys += 1

            if feat is None:
                continue
            if symbol not in self.today_universe:
                continue

            # --- Opening Drive (paper only; same-day) ---
            if settings.paper_mode and self.od_enabled:
                od_entry, od_exit = self.od_strategy.on_quote(
                    quote, now_hhmm=quote_hhmm, value_ma5=feat.value_ma5
                )
                if od_exit is not None:
                    self._od_paper_exit(
                        ymd=ymd,
                        symbol=symbol,
                        exit_price=od_exit.exit_price,
                        exit_reason=od_exit.reason,
                        exit_ts=quote_dt.isoformat(timespec="seconds"),
                    )
                elif od_entry is not None:
                    entered_od = self._od_paper_buy(
                        ymd=ymd,
                        symbol=symbol,
                        entry=od_entry,
                        entry_ts=quote_dt.isoformat(timespec="seconds"),
                    )
                    if entered_od:
                        cycle_signals += 1
                        cycle_buys += 1

            # --- Breakout arms (K + prev_high) ---
            for book in self.breakout_books:
                signal = book.strategy.on_quote(quote)
                state = book.strategy.symbol_state.get(symbol)
                breakout_price = int(state.breakout_price or 0) if state else 0
                if breakout_price <= 0 or quote.current_price < breakout_price:
                    continue

                gate = evaluate_pace_gate(
                    cum_value=quote.cum_value,
                    value_ma5=feat.value_ma5,
                    now_hhmm=quote_hhmm,
                    current_price=quote.current_price,
                    breakout_price=breakout_price,
                    prev_close=feat.prev_close,
                    pace_threshold=settings.pace_threshold,
                    entry_start_hhmm=settings.pace_entry_start_hhmm,
                    entry_end_hhmm=settings.pace_entry_end_hhmm,
                    chase_limit_mult=settings.pace_chase_limit_mult,
                    upper_limit_mult=settings.pace_upper_limit_mult,
                )

                if settings.paper_mode:
                    already_ordered = symbol in book.ordered_symbols_today
                    full_cap = not book.can_buy()
                    budget = book.budget()
                else:
                    # Live: only the first enabled breakout arm places real orders.
                    if book is not self.breakout_books[0]:
                        continue
                    already_ordered = symbol in self.ordered_symbols_today
                    full_cap = self.buy_orders_today >= settings.max_positions
                    budget = int(self.per_symbol_budget or 0)

                high_price = breakout_price > budget
                entered = False

                if (
                    signal is not None
                    and gate.gate_pass
                    and not already_ordered
                    and not full_cap
                    and not high_price
                ):
                    cycle_signals += 1
                    if settings.paper_mode:
                        entered = self._paper_virtual_buy(
                            book=book,
                            ymd=ymd,
                            symbol=symbol,
                            quote=quote,
                            breakout_price=breakout_price,
                            budget=budget,
                            pace_ratio=gate.pace_ratio,
                            reason=signal.reason,
                        )
                    else:
                        entered = self._live_buy(
                            symbol=symbol,
                            signal=signal,
                            budget=budget,
                        )
                    if entered:
                        book.strategy.confirm_entry(symbol)
                        cycle_buys += 1
                elif signal is not None and gate.gate_pass and not already_ordered:
                    cycle_signals += 1
                    cycle_skipped_signals += 1
                    action = "SKIP_FULL_CAP" if full_cap else "SKIP_HIGH_PRICE"
                    self.logger.log_signal(
                        {
                            "symbol": symbol,
                            "breakout_price": breakout_price,
                            "reason": f"{book.name}:{signal.reason}",
                            "action": action,
                            "note": book.name,
                        }
                    )

                block = entry_block_reason(
                    gate=gate,
                    already_ordered=already_ordered,
                    full_cap=full_cap,
                    high_price=high_price,
                )
                self.gate_logger.maybe_log(
                    ymd=ymd,
                    symbol=symbol,
                    breakout_price=breakout_price,
                    current_price=quote.current_price,
                    cum_value=quote.cum_value,
                    value_ma5=feat.value_ma5,
                    gate=gate,
                    entered=entered,
                    block_reason="" if entered else block,
                    strategy=book.name,
                )
                if entered:
                    time.sleep(0.15)

        if snapshot_profile and profile_rows:
            self.profile_logger.log_snapshot(ymd=ymd, rows=profile_rows)

        self._hb_scanned += cycle_scanned
        self._hb_quote_ok += cycle_quote_ok
        self._hb_quote_err += cycle_quote_err
        self._hb_signals += cycle_signals
        self._hb_buys += cycle_buys
        self._hb_skipped_signals += cycle_skipped_signals

        book_status = ",".join(
            f"{b.name}:{b.buy_orders_today}/{b.max_positions}" for b in self.breakout_books
        )
        if self.od_enabled:
            book_status += f",od:{self.od_buy_orders_today}/{settings.od_max_positions}"
        if self.theme_enabled:
            book_status += (
                f",theme:{self.theme_buy_orders_today}/{settings.theme_max_positions}"
            )
        emitted = self._maybe_heartbeat(
            "monitoring",
            "감시중... "
            f"now={now_kst} KST watchlist={len(self.today_universe)} "
            f"theme_watch={len(self.theme_watch_symbols)} "
            f"arms=[{book_status}] "
            f"paper={'Y' if settings.paper_mode else 'N'} "
            f"(cycles={self._hb_cycles}, quote_err={self._hb_quote_err}, "
            f"signals={self._hb_signals}, buys={self._hb_buys}, skip={self._hb_skipped_signals})",
        )
        if emitted:
            self._reset_heartbeat_counters()

    def _live_buy(self, *, symbol: str, signal, budget: int) -> bool:
        self.logger.log_signal(
            {
                "symbol": symbol,
                "breakout_price": signal.breakout_price,
                "reason": signal.reason,
                "action": "BUY_ATTEMPT",
                "note": "",
            }
        )
        try:
            result = self.order.place_breakout_buy_with_budget(
                symbol=symbol,
                per_symbol_budget=budget,
                breakout_price=signal.breakout_price,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"BUY failed {symbol}: {exc}")
            return False

        ord_no = str(result.get("ord_no", "") or "")
        if not ord_no:
            self.logger.error(f"BUY ack missing ord_no: {symbol}. Not counted.")
            return False

        qty = self.order.calc_buy_qty_with_budget(
            per_symbol_budget=budget, breakout_price=signal.breakout_price
        )
        self.ordered_symbols_today.add(symbol)
        self.buy_orders_today += 1
        self.logger.log_trade(
            {
                "symbol": symbol,
                "side": "BUY",
                "qty": qty,
                "price": signal.breakout_price,
                "reason": signal.reason,
                "fee": "",
                "tax": "",
                "order_id": ord_no,
                "cash_psbl": "",
                "balance_tot_asset": "",
                "balance_dnca": "",
                "balance_json": "",
            }
        )
        self.logger.info(
            f"[매수] {symbol} price={signal.breakout_price} qty={qty} ord_no={ord_no}"
        )
        return True

    def _paper_virtual_buy(
        self,
        *,
        book: BreakoutBook,
        ymd: str,
        symbol: str,
        quote,
        breakout_price: int,
        budget: int,
        pace_ratio: float,
        reason: str,
    ) -> bool:
        entry_price = int(quote.current_price)
        qty = calc_paper_qty(budget, entry_price)
        if qty <= 0:
            self.logger.error(f"PAPER BUY qty=0 {book.name} {symbol} entry={entry_price}")
            return False

        entry_ts = self._now_kst_local().isoformat(timespec="seconds")
        book.ledger.append_entry(
            ymd=ymd,
            symbol=symbol,
            entry_ts=entry_ts,
            entry_price=entry_price,
            breakout_price=breakout_price,
            qty=qty,
            pace_ratio_at_entry=pace_ratio,
        )
        book.ordered_symbols_today.add(symbol)
        book.buy_orders_today += 1
        slip_bp = 0.0
        if breakout_price > 0:
            slip_bp = (entry_price / breakout_price - 1.0) * 10000.0
        self.logger.log_signal(
            {
                "symbol": symbol,
                "breakout_price": breakout_price,
                "reason": f"{book.name}:{reason}",
                "action": "PAPER_BUY",
                "note": f"entry={entry_price} slip_bp={slip_bp:.1f}",
            }
        )
        self.logger.log_trade(
            {
                "symbol": symbol,
                "side": "BUY",
                "qty": qty,
                "price": entry_price,
                "reason": f"paper_virtual:{book.name}:{reason}",
                "fee": "",
                "tax": "",
                "order_id": "PAPER",
                "cash_psbl": "",
                "balance_tot_asset": "",
                "balance_dnca": "",
                "balance_json": "",
            }
        )
        self.logger.info(
            f"[페이퍼매수/{book.name}] {symbol} entry={entry_price} breakout={breakout_price} "
            f"qty={qty} pace_ratio={pace_ratio:.2f} slip_bp={slip_bp:.1f}"
        )
        return True

    def _od_paper_buy(self, *, ymd: str, symbol: str, entry, entry_ts: str) -> bool:
        if symbol in self.od_ordered_symbols_today:
            return False
        if self.od_buy_orders_today >= settings.od_max_positions:
            return False
        budget = int(self.od_per_symbol_budget or 0)
        qty = calc_paper_qty(budget, entry.entry_price)
        if qty <= 0:
            self.logger.error(f"OD PAPER BUY qty=0 {symbol} entry={entry.entry_price}")
            return False
        self.od_ledger.append_entry(
            ymd=ymd,
            symbol=symbol,
            entry_ts=entry_ts,
            entry_price=int(entry.entry_price),
            trigger_price=int(entry.trigger_price),
            qty=qty,
            gap_pct=float(entry.gap_pct) * 100.0,
            observe_high=int(entry.observe_high),
            pace_ratio_at_entry=float(entry.pace_ratio),
        )
        self.od_strategy.confirm_entry(symbol, entry.entry_price)
        self.od_ordered_symbols_today.add(symbol)
        self.od_buy_orders_today += 1
        self.logger.log_signal(
            {
                "symbol": symbol,
                "breakout_price": entry.trigger_price,
                "reason": "opening_drive",
                "action": "PAPER_BUY",
                "note": f"gap_pct={entry.gap_pct*100:.2f} observe_high={entry.observe_high}",
            }
        )
        self.logger.info(
            f"[페이퍼매수/opening_drive] {symbol} entry={entry.entry_price} "
            f"trigger={entry.trigger_price} qty={qty} gap={entry.gap_pct*100:.2f}% "
            f"pace={entry.pace_ratio:.2f}"
        )
        return True

    def _od_paper_exit(
        self,
        *,
        ymd: str,
        symbol: str,
        exit_price: int,
        exit_reason: str,
        exit_ts: str,
    ) -> None:
        ok = self.od_ledger.fill_exit(
            ymd=ymd,
            symbol=symbol,
            exit_ts=exit_ts,
            exit_price=exit_price,
            exit_reason=exit_reason,
        )
        if ok:
            self.logger.info(
                f"[페이퍼매도/opening_drive] {symbol} exit={exit_price} reason={exit_reason}"
            )

    def _od_force_flat_open(self, ymd: str) -> None:
        open_syms = self.od_ledger.symbols_open(ymd=ymd)
        if not open_syms:
            return
        exit_ts = self._now_kst_local().isoformat(timespec="seconds")
        for symbol in open_syms:
            try:
                quote = self.api.get_quote(symbol)
                px = int(quote.current_price or 0)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"OD force-flat quote failed {symbol}: {exc}")
                continue
            if px <= 0:
                continue
            self._od_paper_exit(
                ymd=ymd,
                symbol=symbol,
                exit_price=px,
                exit_reason="FORCE_CLOSE",
                exit_ts=exit_ts,
            )
            time.sleep(0.15)

    def _theme_paper_buy(self, *, ymd: str, symbol: str, entry, entry_ts: str) -> bool:
        if symbol in self.theme_ordered_symbols_today:
            return False
        if self.theme_buy_orders_today >= settings.theme_max_positions:
            return False
        budget = int(self.theme_per_symbol_budget or 0)
        qty = calc_paper_qty(budget, entry.entry_price)
        if qty <= 0:
            self.logger.error(
                f"THEME PAPER BUY qty=0 {symbol} entry={entry.entry_price}"
            )
            return False
        self.theme_ledger.append_entry(
            ymd=ymd,
            theme_id=str(entry.theme_id),
            theme_name=str(entry.theme_name),
            symbol=symbol,
            role=str(entry.role),
            entry_ts=entry_ts,
            entry_price=int(entry.entry_price),
            trigger_price=int(entry.trigger_price),
            qty=qty,
            theme_score_at_entry=float(entry.theme_score),
            stock_ret_at_entry=float(entry.stock_ret),
            theme_median_ret=float(entry.theme_median_ret),
            pace_ratio_at_entry=float(entry.pace_ratio),
        )
        self.theme_strategy.confirm_entry(symbol, entry.entry_price)
        self.theme_ordered_symbols_today.add(symbol)
        self.theme_buy_orders_today += 1
        self.logger.log_signal(
            {
                "symbol": symbol,
                "breakout_price": entry.trigger_price,
                "reason": f"theme_map:{entry.theme_id}",
                "action": "PAPER_BUY",
                "note": (
                    f"score={entry.theme_score:.2f} stock_ret={entry.stock_ret:.3f} "
                    f"median={entry.theme_median_ret:.3f}"
                ),
            }
        )
        self.logger.info(
            f"[페이퍼매수/theme_map] {symbol} theme={entry.theme_id}:{entry.theme_name} "
            f"entry={entry.entry_price} trigger={entry.trigger_price} qty={qty} "
            f"score={entry.theme_score:.2f} pace={entry.pace_ratio:.2f}"
        )
        return True

    def _theme_paper_exit(
        self,
        *,
        ymd: str,
        symbol: str,
        exit_price: int,
        exit_reason: str,
        exit_ts: str,
    ) -> None:
        ok = self.theme_ledger.fill_exit(
            ymd=ymd,
            symbol=symbol,
            exit_ts=exit_ts,
            exit_price=exit_price,
            exit_reason=exit_reason,
        )
        if ok:
            self.theme_strategy.mark_exited(symbol)
            self.logger.info(
                f"[페이퍼매도/theme_map] {symbol} exit={exit_price} reason={exit_reason}"
            )

    def _theme_force_flat_open(self, ymd: str) -> None:
        open_syms = self.theme_ledger.symbols_open(ymd=ymd)
        if not open_syms:
            return
        exit_ts = self._now_kst_local().isoformat(timespec="seconds")
        for symbol in open_syms:
            try:
                quote = self.api.get_quote(symbol)
                px = int(quote.current_price or 0)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"theme force-flat quote failed {symbol}: {exc}")
                continue
            if px <= 0:
                continue
            self._theme_paper_exit(
                ymd=ymd,
                symbol=symbol,
                exit_price=px,
                exit_reason="FORCE_CLOSE",
                exit_ts=exit_ts,
            )
            time.sleep(0.15)

    def _fill_paper_open_exits(self, ymd: str) -> bool:
        """
        당일 시가로 이전 세션 진입분의 exit_open_next를 소급 기입한다.
        K / prev_high 원장 모두 채운다 (Opening Drive는 당일청산이라 대상 아님).
        """
        ledgers = [b.ledger for b in self.breakout_books]
        symbols_needed: set[str] = set()
        for led in ledgers:
            symbols_needed.update(led.symbols_pending_open_exit(before_ymd=ymd))
        if not symbols_needed:
            self.logger.info(f"paper_mode: 익일 시가 청산 대상 없음 (ymd={ymd})")
            return True

        opens: Dict[str, int] = {}
        for symbol in sorted(symbols_needed):
            try:
                quote = self.api.get_quote(symbol)
                if quote.open_price > 0:
                    opens[symbol] = int(quote.open_price)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"PAPER open quote failed {symbol}: {exc}")
            time.sleep(0.15)

        holidays = load_manual_holiday_set(settings.holiday_dates_path)
        expected_entry = prev_trading_day_ymd(ymd, holidays)
        total_n = 0
        for led in ledgers:
            n, anomalies = led.fill_next_open_exits(
                exit_ymd=ymd, symbol_opens=opens, expected_entry_ymd=expected_entry
            )
            total_n += n
            for msg in anomalies:
                self.logger.error(
                    f"paper_mode: 시가 청산 이례 건 — 직전 거래일 진입이 아님"
                    f"(거래정지/기입 누락 의심) [{led.path.name}]: {msg}"
                )
        self.logger.info(
            f"paper_mode: 익일 시가 청산 기록 {total_n}건 "
            f"(ymd={ymd}, expected_entry={expected_entry})"
        )

        missing = [s for s in symbols_needed if s not in opens]
        if missing:
            self.logger.error(
                f"paper_mode: 시가 미확보 {len(missing)}종목 (재시도 예정): {','.join(missing)}"
            )
            return False
        return True

    def _fill_paper_same_day_close(self, ymd: str) -> None:
        """
        당일 종가 비교 청산(기록 전용) — breakout 원장들.
        Opening Drive는 이미 당일 청산되므로 여기 대상이 아님.
        """
        for book in self.breakout_books:
            pending_symbols = book.ledger.symbols_pending_same_day_close(ymd=ymd)
            if not pending_symbols:
                continue
            for symbol in pending_symbols:
                try:
                    quote = self.api.get_quote(symbol)
                    book.ledger.fill_same_day_close(
                        ymd=ymd, symbol=symbol, exit_close=int(quote.current_price)
                    )
                except Exception as exc:  # noqa: BLE001
                    self.logger.error(
                        f"PAPER same-day close failed [{book.name}] {symbol}: {exc}"
                    )
                time.sleep(0.15)
            self.logger.info(
                f"paper_mode: 당일 종가 비교 청산 기록 완료 "
                f"[{book.name}] ({len(pending_symbols)}종목, ymd={ymd})"
            )

    def on_close(self) -> None:
        self.logger.info("Market monitoring closed at 15:30.")

    def request_shutdown(self) -> None:
        self._should_stop = True
        self.logger.info(f"Auto shutdown at {settings.shutdown_hhmm} (KST).")

    def _write_daily_result_csv(self, ymd: str) -> None:
        if not settings.result_csv_on_shutdown:
            return
        try:
            # 잔고 API는 쓰지 않음. KIS 일별체결(매매내역)만 사용.
            from core.naver_symbol_master import load_or_refresh_symbol_master
            from core.result_csv import (
                append_result_rows,
                append_result1_rows,
                build_daily_rows_from_kis_range,
                kis_rows_to_execs,
                kis_rows_to_symbol_names,
            )

            lookback = max(1, min(90, int(settings.result_csv_kis_lookback_days)))
            end_dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
            start_ymd = (end_dt - timedelta(days=lookback)).strftime("%Y%m%d")
            kis_rows = self.api.get_daily_order_executions(start_ymd, ymd)
            execs = kis_rows_to_execs(kis_rows)
            daily_rows = build_daily_rows_from_kis_range(execs, ymd)
            names = load_or_refresh_symbol_master(
                settings.symbol_master_path,
                auto_refresh=settings.symbol_master_auto_refresh,
                max_age_days=settings.symbol_master_max_age_days,
                delay_sec=settings.naver_http_delay_sec,
            )
            kis_names = kis_rows_to_symbol_names(kis_rows)
            append_result_rows(
                settings.result_csv_path, daily_rows, names, kis_symbol_names=kis_names
            )
            self.logger.info(f"result.csv 갱신: {ymd} ({len(daily_rows)}건, KIS조회 {start_ymd}~{ymd})")
            append_result1_rows(
                settings.result1_csv_path,
                daily_rows,
                names,
                fee_rate_buy=settings.fee_rate_buy,
                fee_rate_sell=settings.fee_rate_sell,
                tax_rate_sell=settings.tax_rate_sell,
                kis_symbol_names=kis_names,
            )
            self.logger.info(f"result_1.csv 갱신: {ymd} ({len(daily_rows)}건, 수수료·세금 포함)")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"result.csv 실패: {exc}")

    def _get_holdings_safely(self) -> List[Dict[str, object]]:
        try:
            rows = self.api.get_domestic_balance_positions()
            return list(rows)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"HOLDINGS failed: {exc}")
            return []

    def _ensure_cash_snapshot_for_today(self) -> bool:
        """
        09:00:05(KST)에 주문가능현금을 1회 조회해 고정 예산(5등분)을 만든다.
        paper_mode이면 KIS 조회 없이 전략별 paper_capital을 각각 5등분한다.
        """
        if settings.paper_mode:
            if self.cash_snapshot_done:
                return True
            now = self._now_kst_local()
            if now.strftime("%H:%M:%S") < "09:00:05":
                return False
            capital = int(settings.paper_capital)
            if capital <= 0:
                self.cash_snapshot_failed = True
                self.logger.error("paper_capital invalid. No buys today.")
                return False
            self.cash_snapshot_total = capital
            per = int(capital // int(settings.max_positions))
            for book in self.breakout_books:
                book.per_symbol_budget = int(capital // int(book.max_positions))
            self.od_per_symbol_budget = int(capital // int(settings.od_max_positions))
            self.theme_per_symbol_budget = int(
                capital // int(settings.theme_max_positions)
            )
            self.per_symbol_budget = per
            self.cash_snapshot_done = True
            self.logger.info(
                "페이퍼 예산 스냅샷 완료(전략별 독립). "
                f"paper_capital/전략={capital} "
                f"breakout_budget={per} od_budget={self.od_per_symbol_budget} "
                f"theme_budget={self.theme_per_symbol_budget} "
                f"orders_cap={settings.max_positions}/{settings.od_max_positions}/"
                f"{settings.theme_max_positions}"
            )
            return True

        if self.cash_snapshot_done:
            return True
        if self.cash_snapshot_failed:
            return False

        now = self._now_kst_local()
        hhmmss = now.strftime("%H:%M:%S")
        if hhmmss < "09:00:05":
            return False
        if hhmmss > "09:05:00":
            self.cash_snapshot_failed = True
            self.logger.error("현금 스냅샷 조회 실패(09:05 초과). 오늘은 매수 없이 감시만 진행합니다.")
            return False

        now_ts = time.time()
        if now_ts < self._next_cash_retry_ts:
            return False
        self._next_cash_retry_ts = now_ts + float(self.cash_retry_interval_sec)

        try:
            cash = int(self.api.get_cash_balance() or 0)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"CASH snapshot failed: {exc}")
            return False

        if cash <= 0:
            self.logger.error(f"CASH snapshot returned invalid cash={cash}. Retry.")
            return False

        self.cash_snapshot_total = cash
        self.per_symbol_budget = int(cash // int(settings.max_positions))
        self.cash_snapshot_done = True
        self.logger.info(
            "현금 스냅샷 완료. "
            f"at={hhmmss} KST "
            f"cash_total={self.cash_snapshot_total} "
            f"budget={self.per_symbol_budget} "
            f"orders_cap={settings.max_positions} "
            "retry_deadline=09:05:00"
        )
        return True


if __name__ == "__main__":
    runner = MaxVRunner()
    runner.run()
