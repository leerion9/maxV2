from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.logger import TradeLogger
from core.order import OrderManager
from core.symbol_resolver import SymbolResolver
from core.strategy import VolatilityBreakoutStrategy
from core.universe import UniverseBuilder
from core.universe_cache import CachedSymbol, UniverseCache, cache_path, load_cache, save_cache, today_kst_yyyymmdd


_REPO_ROOT = Path(__file__).resolve().parent


@dataclass
class PendingOrder:
    created_ts: float
    symbol: str
    side: str  # BUY|SELL
    qty: int
    ord_no: str
    before_qty: int
    deadline_ts: float


class MaxVRunner:
    def __init__(self) -> None:
        settings.validate()
        self.api = KISApiClient(settings=settings)
        self.logger = TradeLogger(log_dir=settings.log_dir)
        self.order = OrderManager(api=self.api, settings=settings)
        self.universe_builder = UniverseBuilder(api=self.api, top_ratio=settings.top_market_cap_ratio)
        self.strategy = VolatilityBreakoutStrategy()
        self.symbols = SymbolResolver()

        # cwd와 무관하게 항상 이 프로젝트의 data/positions.json만 사용 (실행 위치에 따라 파일이 갈라지는 문제 방지)
        self.positions_file = _REPO_ROOT / "data" / "positions.json"
        self.today_universe: List[str] = []
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
        self._market_holiday_set = self._parse_yyyymmdd_list(settings.market_holidays)
        self._market_extra_open_set = self._parse_yyyymmdd_list(settings.market_extra_open_days)
        self._trading_day_cache: Dict[str, bool] = {}
        self._did_prepare_ymd: str = ""
        self._did_liquidate_ymd: str = ""
        self._did_close_ymd: str = ""
        self._last_monitor_ts: float = 0.0
        self._pending_orders: List[PendingOrder] = []

    def run(self) -> None:
        self._configure_console_utf8()
        self.logger.info("MaxV started.")
        self.logger.info(f"positions.json 절대경로: {self.positions_file.resolve()}")

        # Candidate list uses prior session OHLCV only; safe after the open.
        # Run once on startup on a weekday so a late start still gets today's watch list.
        if self._is_krx_weekday():
            self.prepare_universe()
            self._did_prepare_ymd = self._now_kst().strftime("%Y%m%d")
            self._sync_bought_symbols_from_positions()
            self._liquidate_non_today_holdings_on_startup_if_needed()
        else:
            self.logger.info("Weekend (KST); skip initial universe prep. Will run at next 08:30 on a weekday.")

        while not self._should_stop:
            self._tick()
            time.sleep(0.25)
        self.logger.info("MaxV stopped.")

    @staticmethod
    def _is_krx_weekday() -> bool:
        """Mon-Fri in Asia/Seoul. Does not exclude exchange holidays."""
        return datetime.now(ZoneInfo("Asia/Seoul")).weekday() < 5

    def _now_kst(self) -> datetime:
        try:
            return self.api.now_kst()
        except Exception:
            return datetime.now(ZoneInfo("Asia/Seoul"))

    def _tick(self) -> None:
        now = self._now_kst()
        ymd = now.strftime("%Y%m%d")
        hhmm = now.strftime("%H:%M")

        self._reconcile_pending_orders()

        if (
            self._is_krx_weekday()
            and hhmm >= "08:30"
            and hhmm <= "09:10"
            and self._did_prepare_ymd != ymd
        ):
            self.prepare_universe()
            self._did_prepare_ymd = ymd

        if (
            self._is_krx_weekday()
            and hhmm >= settings.liquidation_hhmm
            and hhmm <= "09:10"
            and self._did_liquidate_ymd != ymd
        ):
            self.liquidate_previous_positions()
            self._did_liquidate_ymd = ymd

        if hhmm >= "15:30" and self._did_close_ymd != ymd:
            self.on_close()
            self._did_close_ymd = ymd

        if hhmm >= settings.shutdown_hhmm:
            self.request_shutdown()
            return

        if self._is_krx_weekday():
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

    @staticmethod
    def _parse_yyyymmdd_list(raw: str) -> set[str]:
        out: set[str] = set()
        for token in raw.split(","):
            t = token.strip()
            if len(t) == 8 and t.isdigit():
                out.add(t)
        return out

    def _is_krx_trading_day(self, day: date | None = None) -> bool:
        now_day = day or self._now_kst().date()
        ymd = now_day.strftime("%Y%m%d")
        if ymd in self._market_extra_open_set:
            return True
        if ymd in self._market_holiday_set:
            return False
        cached = self._trading_day_cache.get(ymd)
        if cached is not None:
            return cached

        try:
            api_open = self.api.is_open_trading_day(base_date_yyyymmdd=ymd)
            if api_open is not None:
                self._trading_day_cache[ymd] = api_open
                return api_open
            try:
                rows = self.api.get_holiday_info(base_date_yyyymmdd=ymd)
                raw_preview = json.dumps(rows, ensure_ascii=False)[:1200]
                self.logger.error(
                    f"KIS holiday payload parse failed for {ymd}; "
                    f"raw_preview={raw_preview}; fallback to weekday rule."
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.error(
                    f"KIS holiday payload parse failed for {ymd}; also failed to fetch raw: {exc}; "
                    "fallback to weekday rule."
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"KIS holiday check failed for {ymd}: {exc}; fallback to weekday rule.")

        is_weekday = now_day.weekday() < 5
        self._trading_day_cache[ymd] = is_weekday
        return is_weekday

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

        cache_date = today_kst_yyyymmdd(self._now_kst())
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

        if not symbols and settings.universe_source in {"naver", "naver_then_kis"}:
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
                if settings.universe_source == "naver":
                    symbols = []
                    features = {}

        if not symbols and settings.universe_source in {"kis", "naver_then_kis"}:
            try:
                symbols = self.universe_builder.build()
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"KIS universe build failed: {exc}")
                symbols = []

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

        if settings.compare_universe_naver:
            try:
                from core.naver_universe import build_naver_universe, format_symbol_diff

                self.logger.info("Naver universe compare starting (scraping; may take ~1-2 min)...")
                naver_syms, _st = build_naver_universe(
                    top_ratio=settings.top_market_cap_ratio,
                    delay_sec=settings.naver_http_delay_sec,
                )
                ok, on = format_symbol_diff(symbols, naver_syms, limit=50)
                self.logger.info(
                    "Universe compare KIS vs Naver: "
                    f"KIS={len(symbols)} Naver={len(naver_syms)} "
                    f"only_KIS={len(set(symbols) - set(naver_syms))} "
                    f"only_Naver={len(set(naver_syms) - set(symbols))}"
                )
                if ok:
                    self.logger.info(f"only_KIS (sample): {ok}")
                if on:
                    self.logger.info(f"only_Naver (sample): {on}")
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Naver universe compare failed: {exc}")

    def liquidate_previous_positions(self) -> None:
        now_kst = self._now_kst()
        if not self._is_krx_trading_day(now_kst.date()):
            self.logger.info(
                f"Skip liquidation: non-trading day ({now_kst.strftime('%Y-%m-%d')} KST). "
                "Will retry at next trading day open."
            )
            return
        today = today_kst_yyyymmdd(now_kst)
        prior_session = self._prior_trading_session_kst_yyyymmdd(now_kst)
        payload = self._load_positions_payload()
        if payload.get("date_kst") != prior_session:
            self.logger.info(
                f"Liquidation skipped: positions date_kst={payload.get('date_kst','')} "
                f"(expected_prior_session={prior_session})"
            )
            return
        raw_positions = payload.get("positions", {}) if isinstance(payload.get("positions", {}), dict) else {}
        prev_syms = {str(sym) for sym, qty in raw_positions.items() if int(qty or 0) > 0}
        if not prev_syms:
            acct_n = self._get_holdings_count_with_cache()
            self.logger.warning(
                "청산 스킵: positions.json에 전일(직전 영업일) 매수 종목이 비어 있음 — "
                f"expected_prior_session={prior_session}, 계좌보유={acct_n}종목, "
                f"file={self.positions_file.resolve()} "
                "(실행 cwd가 바뀌면 예전에는 다른 경로의 JSON을 읽었을 수 있습니다)."
            )
            self._save_positions({"date_kst": today, "positions": {}})
            self._sync_bought_symbols_from_positions()
            return

        holdings = self._get_holdings_with_cache(force_refresh=True)
        holdings_by_symbol = {str(r.get("symbol", "")).strip(): r for r in holdings if isinstance(r, dict)}
        to_sell: List[Dict[str, object]] = []
        for sym in sorted(prev_syms):
            row = holdings_by_symbol.get(sym)
            if not row:
                continue
            qty = int(row.get("qty", 0) or 0)
            if qty <= 0:
                continue
            to_sell.append({"symbol": sym, "qty": qty, "name": row.get("name", "")})

        if not to_sell:
            self.logger.info("Liquidation skipped: no matching holdings for previous-day positions")
            self._save_positions({"date_kst": today, "positions": {}})
            self.bought_symbols.clear()
            return

        self.logger.info(
            f"Liquidating previous-day positions at {settings.liquidation_hhmm} (KST): "
            f"count={len(to_sell)}"
        )
        for row in to_sell:
            symbol = str(row["symbol"])
            qty = int(row["qty"])
            name = self.symbols.get_name(symbol, fetch=True) or str(row.get("name", "") or "").strip()
            before_qty = self._get_holding_qty(symbol)
            try:
                result = self.order.place_open_liquidation(symbol=symbol, qty=qty)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"SELL submit failed {symbol}: {exc}")
                continue
            ord_no = str(result.get("ord_no", "") or "")
            self._add_pending_order(symbol=symbol, side="SELL", qty=qty, ord_no=ord_no, before_qty=before_qty)
            cash_psbl = self._get_cash_with_cache(force_refresh=True)
            bal = self._get_balance_summary_safely()
            self.logger.log_trade(
                {
                    "symbol": symbol,
                    "symbol_name": name,
                    "side": "SELL",
                    "qty": qty,
                    "price": 0,
                    "reason": "next_day_0855_liquidation",
                    "fee": "",
                    "tax": "",
                    "order_id": ord_no,
                    "cash_psbl": cash_psbl,
                    **bal,
                }
            )
            self.logger.info(
                f"[매도요청] {name or ''}({symbol}) qty={qty} 시장가(08:55 청산) "
                f"ord_no={ord_no} cash_psbl={cash_psbl}"
            )
            time.sleep(0.55)

        self._save_positions({"date_kst": today, "positions": {}})
        self.bought_symbols.clear()

    def _sync_bought_symbols_from_positions(self) -> None:
        today = today_kst_yyyymmdd(self._now_kst())
        payload = self._load_positions_payload()
        if payload.get("date_kst") != today:
            self.bought_symbols = set()
            return
        positions = payload.get("positions", {}) if isinstance(payload.get("positions", {}), dict) else {}
        self.bought_symbols = {str(sym) for sym, qty in positions.items() if int(qty or 0) > 0}

    def _liquidate_non_today_holdings_on_startup_if_needed(self) -> None:
        now = self._now_kst()
        if not self._is_krx_trading_day(now.date()):
            return
        now_hhmm = now.strftime("%H:%M")
        if now_hhmm < settings.liquidation_hhmm:
            return

        today = today_kst_yyyymmdd(self._now_kst())
        payload = self._load_positions_payload()
        raw_positions = payload.get("positions", {}) if isinstance(payload.get("positions", {}), dict) else {}
        if payload.get("date_kst") == today:
            return
        if payload.get("date_kst") != self._prior_trading_session_kst_yyyymmdd(now):
            return
        prev_syms = {str(sym) for sym, qty in raw_positions.items() if int(qty or 0) > 0}
        if not prev_syms:
            return

        holdings = self._get_holdings_with_cache(force_refresh=True)
        to_sell: List[Dict[str, object]] = []
        for row in holdings:
            symbol = str(row.get("symbol", "")).strip()
            qty = int(row.get("qty", 0) or 0)
            if not symbol or qty <= 0:
                continue
            if symbol not in prev_syms:
                continue
            to_sell.append({"symbol": symbol, "qty": qty, "name": row.get("name", "")})

        if not to_sell:
            return

        self.logger.info(
            f"Startup liquidation triggered (now={now_hhmm} KST): "
            f"selling previous-day positions={len(to_sell)}"
        )
        for row in to_sell:
            symbol = str(row["symbol"])
            qty = int(row["qty"])
            name = self.symbols.get_name(symbol, fetch=True) or str(row.get("name", "") or "").strip()
            before_qty = self._get_holding_qty(symbol)
            try:
                result = self.order.place_open_liquidation(symbol=symbol, qty=qty)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"SELL submit failed {symbol}: {exc}")
                continue
            ord_no = str(result.get("ord_no", "") or "")
            self._add_pending_order(symbol=symbol, side="SELL", qty=qty, ord_no=ord_no, before_qty=before_qty)
            cash_psbl = self._get_cash_with_cache(force_refresh=True)
            bal = self._get_balance_summary_safely()
            self.logger.log_trade(
                {
                    "symbol": symbol,
                    "symbol_name": name,
                    "side": "SELL",
                    "qty": qty,
                    "price": 0,
                    "reason": "startup_previous_day_liquidation",
                    "fee": "",
                    "tax": "",
                    "order_id": ord_no,
                    "cash_psbl": cash_psbl,
                    **bal,
                }
            )
            self.logger.info(
                f"[매도요청] {name or ''}({symbol}) qty={qty} 시장가(시작즉시청산) "
                f"ord_no={ord_no} cash_psbl={cash_psbl}"
            )
            time.sleep(0.55)

        self._save_positions({"date_kst": today, "positions": {}})
        self._sync_bought_symbols_from_positions()

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
        now_kst = self._now_kst().strftime("%H:%M")
        if not self._is_krx_trading_day():
            self._maybe_heartbeat(
                "holiday",
                f"휴장일로 감시 스킵... now={now_kst} KST (감시대상={len(self.today_universe)})",
            )
            return
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
                            "symbol_name": self.symbols.get_name(symbol, fetch=False),
                            "breakout_price": state.breakout_price or 0,
                            "reason": state.skip_reason,
                            "action": "SKIP_INITIAL_AB",
                            "note": "첫 관측에서 A&B 동시충족 종목 스킵",
                        }
                    )
                    state.skip_reason = ""
                continue
            cycle_signals += 1
            symbol_name_cached = self.symbols.get_name(symbol, fetch=False)
            if not can_buy:
                self.logger.log_signal(
                    {
                        "symbol": symbol,
                        "symbol_name": symbol_name_cached,
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
                        "symbol_name": symbol_name_cached,
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
                    "symbol_name": symbol_name_cached,
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
            before_qty = self._get_holding_qty(symbol)
            ord_no = str(result.get("ord_no", "") or "")
            self._add_pending_order(symbol=symbol, side="BUY", qty=qty, ord_no=ord_no, before_qty=before_qty)
            self.bought_symbols.add(symbol)
            positions = self._load_positions()
            positions[symbol] = qty
            today = today_kst_yyyymmdd(self._now_kst())
            self._save_positions({"date_kst": today, "positions": positions})
            name = self.symbols.get_name(symbol, fetch=True)
            cash_psbl_after = self._get_cash_with_cache(force_refresh=True)
            bal = self._get_balance_summary_safely()
            self.logger.log_trade(
                {
                    "symbol": symbol,
                    "symbol_name": name,
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
                f"[매수] {name or ''}({symbol}) price={signal.breakout_price} qty={qty} "
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

        emitted = self._maybe_heartbeat(
            "monitoring",
            "계속 감시중... "
            f"now={now_kst} KST "
            f"watchlist={len(self.today_universe)} "
            f"holdings={self._get_holdings_count_with_cache()}/{settings.max_positions} "
            f"{'(5종목 매수 완료, 주문 스킵 없이 감시만 계속)' if len(self.bought_symbols) >= settings.max_positions else ''} "
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

    def _load_positions_payload(self) -> Dict[str, object]:
        if not self.positions_file.exists():
            return {"date_kst": "", "positions": {}}
        try:
            raw = json.loads(self.positions_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.logger.error(f"positions.json JSON 파싱 실패 ({self.positions_file}): {exc}")
            return {"date_kst": "", "positions": {}}
        if not isinstance(raw, dict):
            return {"date_kst": "", "positions": {}}
        if "positions" in raw:
            pos = raw.get("positions")
            if pos is None or not isinstance(pos, dict):
                pos = {}
            return {"date_kst": str(raw.get("date_kst", "") or ""), "positions": pos}
        return {"date_kst": "", "positions": dict(raw)}

    def _load_positions(self) -> Dict[str, int]:
        payload = self._load_positions_payload()
        positions = payload.get("positions", {})
        if isinstance(positions, dict):
            return {str(k): int(v) for k, v in positions.items()}
        return {}

    def _save_positions(self, positions_payload: Dict[str, object]) -> None:
        self.positions_file.parent.mkdir(parents=True, exist_ok=True)
        self.positions_file.write_text(
            json.dumps(positions_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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

    def _get_holding_qty(self, symbol: str) -> int:
        try:
            rows = self._get_holdings_with_cache(force_refresh=True)
            for r in rows:
                if str(r.get("symbol", "")).strip() == str(symbol).strip():
                    return int(r.get("qty", 0) or 0)
        except Exception:
            return 0
        return 0

    def _prior_trading_session_kst_yyyymmdd(self, now_kst: datetime) -> str:
        """Most recent KRX trading day strictly before now_kst's calendar date (KST)."""
        d = now_kst.date() - timedelta(days=1)
        for _ in range(40):
            if self._is_krx_trading_day(d):
                return d.strftime("%Y%m%d")
            d -= timedelta(days=1)
        fb = (now_kst.date() - timedelta(days=1)).strftime("%Y%m%d")
        self.logger.warning(
            f"prior_trading_session: no trading day in 40d lookback; fallback calendar-1={fb}"
        )
        return fb

    def _add_pending_order(self, *, symbol: str, side: str, qty: int, ord_no: str, before_qty: int) -> None:
        if not ord_no:
            self.logger.error(f"Order ACK missing ord_no: {side} {symbol} qty={qty}")
            return
        now_ts = time.time()
        self._pending_orders.append(
            PendingOrder(
                created_ts=now_ts,
                symbol=symbol,
                side=side,
                qty=int(qty or 0),
                ord_no=ord_no,
                before_qty=int(before_qty or 0),
                deadline_ts=now_ts + 30.0,
            )
        )

    def _reconcile_pending_orders(self) -> None:
        if not self._pending_orders:
            return
        now_ts = time.time()
        remaining: List[PendingOrder] = []
        for po in self._pending_orders:
            current_qty = self._get_holding_qty(po.symbol)
            confirmed = False
            if po.side == "BUY":
                confirmed = current_qty >= (po.before_qty + po.qty)
            elif po.side == "SELL":
                confirmed = current_qty <= max(0, po.before_qty - po.qty)

            if confirmed:
                self.logger.info(
                    f"[주문확인] {po.side} {po.symbol} ord_no={po.ord_no} "
                    f"before_qty={po.before_qty} now_qty={current_qty}"
                )
                continue

            if now_ts >= po.deadline_ts:
                self.logger.error(
                    f"[주문미확정] {po.side} {po.symbol} ord_no={po.ord_no} "
                    f"before_qty={po.before_qty} now_qty={current_qty} "
                    "잔고 반영으로 체결/미체결을 확정하지 못했습니다. KIS 주문/체결조회로 확인이 필요합니다."
                )
                continue

            remaining.append(po)
        self._pending_orders = remaining


if __name__ == "__main__":
    runner = MaxVRunner()
    runner.run()
