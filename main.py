from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.logger import TradeLogger
from core.order import OrderManager
from core.strategy import VolatilityBreakoutStrategy
from core.universe_cache import CachedSymbol, UniverseCache, cache_path, load_cache, save_cache, today_kst_yyyymmdd


_REPO_ROOT = Path(__file__).resolve().parent


class MaxVRunner:
    def __init__(self) -> None:
        settings.validate()
        self.api = KISApiClient(settings=settings)
        self.logger = TradeLogger(log_dir=settings.log_dir)
        self.order = OrderManager(api=self.api, settings=settings)
        self.strategy = VolatilityBreakoutStrategy()

        self.today_universe: List[str] = []
        # Running set of symbols we should never buy again during this process.
        # On (re)start we seed it from current account holdings.
        self.bought_symbols: set[str] = set()
        self.cached_cash: int = 0
        self.cash_updated_at: float = 0.0
        self.cash_refresh_sec: int = 20
        self.cached_holdings_count: int = 0
        self.cached_holdings_rows: List[Dict[str, object]] = []
        self.holdings_updated_at: float = 0.0
        self.holdings_refresh_sec: int = 15
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

    def run(self) -> None:
        self._configure_console_utf8()
        self.logger.info("MaxV started.")
        self._seed_bought_symbols_from_holdings()

        now = self._now_kst_local()
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

        # Pre-open liquidation: sell all holdings once at 08:50.
        if hhmm >= settings.liquidation_hhmm and self._did_liquidate_ymd != ymd:
            self.liquidate_all_holdings_at_open()
            self._did_liquidate_ymd = ymd

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

    def _seed_bought_symbols_from_holdings(self) -> None:
        """
        On restart, we must not re-buy symbols already held.
        We seed bought_symbols from current account holdings once at startup.
        """
        try:
            rows = self._get_holdings_with_cache(force_refresh=True)
            syms = {str(r.get("symbol", "")).strip() for r in rows if isinstance(r, dict)}
            self.bought_symbols = {s for s in syms if s}
            if self.bought_symbols:
                self.logger.info(
                    f"보유 종목 감지: {len(self.bought_symbols)}종목 (재매수 방지용)"
                )
        except Exception:
            self.bought_symbols = set()

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
                f"Universe cache hit: {ucache_path.name} ({len(symbols)} symbols)"
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
                    )
                    for sym, row in feat_raw.items()
                }
                self.logger.info(
                    f"Naver universe ready: {len(naver_syms)} symbols "
                    f"(ranked={st.get('naver_ranked', 0)} top_n={st.get('top_n', 0)} "
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
        self.strategy = VolatilityBreakoutStrategy()
        self._reset_heartbeat_counters()

        # Strategy prep: prefer cached/naver features, avoid KIS daily calls when available.
        for symbol in symbols:
            feat = features.get(symbol)
            if feat is not None:
                self.strategy.register(
                    symbol=symbol,
                    avg_volume_5d=feat.avg_volume_5d,
                    prev_high=feat.prev_high,
                    prev_low=feat.prev_low,
                    breakout_k=settings.breakout_k,
                )
                continue

            try:
                rows = self.api.get_daily_prices(symbol=symbol, days=6)
                if len(rows) < 2:
                    continue
                avg_volume_5d = int(sum(r["volume"] for r in rows[:5]) / 5)
                prev_high = int(rows[0]["high"])
                prev_low = int(rows[0]["low"])
                self.strategy.register(
                    symbol=symbol,
                    avg_volume_5d=avg_volume_5d,
                    prev_high=prev_high,
                    prev_low=prev_low,
                    breakout_k=settings.breakout_k,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Strategy prep failed {symbol}: {exc}")
                continue

        self.logger.info(f"Universe ready: {len(self.today_universe)} symbols")
        if self.today_universe and settings.watchlist_sample_size > 0:
            sample_n = min(len(self.today_universe), settings.watchlist_sample_size)
            sample = ",".join(self.today_universe[:sample_n])
            self.logger.info(f"감시대상 샘플 ({sample_n}/{len(self.today_universe)}): {sample}")
        self.logger.info(
            "감시 준비 완료. "
            f"{settings.monitor_start_hhmm}~{settings.monitor_end_hhmm} (KST) 구간에서 감시합니다. "
            f"(poll={settings.poll_interval_sec}s, heartbeat={settings.heartbeat_sec}s)"
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

        holdings = self._get_holdings_with_cache(force_refresh=True)
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
            cash_psbl = self._get_cash_with_cache(force_refresh=True)
            bal = self._get_balance_summary_safely()
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
                    "cash_psbl": cash_psbl,
                    **bal,
                }
            )
            self.logger.info(
                f"[매도요청] {symbol} qty={qty} 시장가(08:50 보유전량청산) "
                f"ord_no={ord_no} cash_psbl={cash_psbl}"
            )
            time.sleep(0.55)

        try:
            time.sleep(0.35)
            rows_h = self._get_holdings_with_cache(force_refresh=True)
            syms = {str(r.get("symbol", "")).strip() for r in rows_h if isinstance(r, dict)}
            self.bought_symbols = {s for s in syms if s}
            self.logger.info(
                f"08:50 청산 후 재매수 방지 목록 재동기화: {len(self.bought_symbols)}종목 "
                "(잔고=0이면 장중 신규 매수 가능)"
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"08:50 청산 후 잔고 동기화 실패: {exc}")

    def _get_balance_summary_safely(self) -> Dict[str, object]:
        try:
            out = self.api.get_domestic_balance_summary()
            o2 = out.get("output2", {}) if isinstance(out, dict) else {}
            raw = out.get("raw", {}) if isinstance(out, dict) else {}
            tot_asset = str(o2.get("tot_asst_amt", "")).strip()
            dnca = str(o2.get("dnca_tot_amt", "")).strip()
            return {
                "balance_tot_asset": tot_asset,
                "balance_dnca": dnca,
                "balance_json": json.dumps(raw, ensure_ascii=False),
            }
        except Exception:
            return {"balance_tot_asset": "", "balance_dnca": "", "balance_json": ""}

    def monitor_intraday(self) -> None:
        now_kst = self._now_kst_local().strftime("%H:%M")
        if now_kst < settings.monitor_start_hhmm or now_kst > settings.monitor_end_hhmm:
            if self.today_universe:
                holdings_cnt = self._get_holdings_count_with_cache()
                self._maybe_heartbeat(
                    "waiting",
                    f"감시 대기중... now={now_kst} KST "
                    f"(감시대상={len(self.today_universe)}, 보유={holdings_cnt}/{settings.max_positions})",
                )
            return
        if not self.today_universe:
            self._maybe_heartbeat("no_watchlist", f"감시대상이 아직 없습니다. 대기중... now={now_kst} KST")
            return
        can_buy = len(self.bought_symbols) < settings.max_positions

        cash = self._get_cash_with_cache()
        if cash <= 0:
            self.logger.error("Cash unavailable. Skip this monitor cycle.")
            return

        self._hb_cycles += 1
        cycle_scanned = 0
        cycle_quote_ok = 0
        cycle_quote_err = 0
        cycle_signals = 0
        cycle_buys = 0
        cycle_skipped_signals = 0
        for symbol in self.today_universe:
            if symbol in self.bought_symbols:
                continue

            cycle_scanned += 1
            try:
                quote = self.api.get_quote(symbol)
                cycle_quote_ok += 1
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"QUOTE failed {symbol}: {exc}")
                cycle_quote_err += 1
                continue
            signal = self.strategy.on_quote(quote)
            if signal is None:
                state = self.strategy.symbol_state.get(symbol)
                if state is not None and state.skip and state.skip_reason:
                    self.logger.log_signal(
                        {
                            "symbol": symbol,
                            "breakout_price": state.breakout_price or 0,
                            "reason": state.skip_reason,
                            "action": "SKIP_INITIAL_AB",
                            "note": "첫 관측에서 A&B 동시충족 종목 스킵",
                        }
                    )
                    state.skip_reason = ""
                continue
            cycle_signals += 1
            if not can_buy:
                self.logger.log_signal(
                    {
                        "symbol": symbol,
                        "breakout_price": signal.breakout_price,
                        "reason": signal.reason,
                        "action": "SKIP_FULL_CAP",
                        "note": "최대 보유 도달(주문 스킵)",
                    }
                )
                cycle_skipped_signals += 1
                continue

            per_symbol_budget = int(cash * settings.allocation_per_symbol)
            if signal.breakout_price > per_symbol_budget:
                self.logger.log_signal(
                    {
                        "symbol": symbol,
                        "breakout_price": signal.breakout_price,
                        "reason": signal.reason,
                        "action": "SKIP_HIGH_PRICE",
                        "note": "주가 고가(1주가 예산 초과)",
                    }
                )
                cycle_skipped_signals += 1
                continue

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
                result = self.order.place_breakout_buy(
                    symbol=symbol,
                    cash=cash,
                    breakout_price=signal.breakout_price,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"BUY failed {symbol}: {exc}")
                continue

            qty = self.order.calc_buy_qty(cash=cash, breakout_price=signal.breakout_price)
            ord_no = str(result.get("ord_no", "") or "")
            self.bought_symbols.add(symbol)
            cash_psbl_after = self._get_cash_with_cache(force_refresh=True)
            bal = self._get_balance_summary_safely()
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
                    "cash_psbl": cash_psbl_after,
                    **bal,
                }
            )
            self.logger.info(
                f"[매수] {symbol} price={signal.breakout_price} qty={qty} "
                f"ord_no={ord_no} cash_psbl={cash_psbl_after}"
            )
            cash = self._get_cash_with_cache(force_refresh=True)
            cycle_buys += 1
            if len(self.bought_symbols) >= settings.max_positions:
                can_buy = False
            time.sleep(0.15)

        self._hb_scanned += cycle_scanned
        self._hb_quote_ok += cycle_quote_ok
        self._hb_quote_err += cycle_quote_err
        self._hb_signals += cycle_signals
        self._hb_buys += cycle_buys
        self._hb_skipped_signals += cycle_skipped_signals

        hb_cap = ""
        if len(self.bought_symbols) >= settings.max_positions:
            hb_cap = (
                f" (재매수 방지 {len(self.bought_symbols)}종목 ≥ {settings.max_positions}, "
                "당일 신규 매수 한도 도달·감시만)"
            )
        emitted = self._maybe_heartbeat(
            "monitoring",
            "계속 감시중... "
            f"now={now_kst} KST "
            f"watchlist={len(self.today_universe)} "
            f"holdings={self._get_holdings_count_with_cache()}/{settings.max_positions} "
            f"block={len(self.bought_symbols)}/{settings.max_positions}"
            f"{hb_cap} "
            f"cash_cached={'Y' if (time.time() - self.cash_updated_at) < self.cash_refresh_sec else 'N'} "
            f"(cycles={self._hb_cycles}, scanned={self._hb_scanned}, quote_ok={self._hb_quote_ok}, "
            f"quote_err={self._hb_quote_err}, signals={self._hb_signals}, buys={self._hb_buys}, "
            f"skip_full={self._hb_skipped_signals})",
        )
        if emitted:
            self._reset_heartbeat_counters()

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
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"result.csv 실패: {exc}")

    def _get_cash_with_cache(self, force_refresh: bool = False) -> int:
        now_ts = time.time()
        if (
            not force_refresh
            and self.cached_cash > 0
            and now_ts - self.cash_updated_at < self.cash_refresh_sec
        ):
            return self.cached_cash
        try:
            cash = self.api.get_cash_balance()
            self.cached_cash = cash
            self.cash_updated_at = now_ts
            return cash
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"CASH failed: {exc}")
            return self.cached_cash

    def _get_holdings_with_cache(self, force_refresh: bool = False) -> List[Dict[str, object]]:
        now_ts = time.time()
        if not force_refresh and now_ts - self.holdings_updated_at < self.holdings_refresh_sec:
            return list(self.cached_holdings_rows)
        try:
            rows = self.api.get_domestic_balance_positions()
            self.cached_holdings_rows = list(rows)
            self.cached_holdings_count = len(rows)
            self.holdings_updated_at = now_ts
            return rows
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"HOLDINGS failed: {exc}")
            return list(self.cached_holdings_rows)

    def _get_holdings_count_with_cache(self) -> int:
        _ = self._get_holdings_with_cache()
        return int(self.cached_holdings_count or 0)


if __name__ == "__main__":
    runner = MaxVRunner()
    runner.run()
