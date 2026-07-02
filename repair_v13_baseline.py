"""repair_v13 표준(baseline) 백테스트 — point-in-time 시총 버전.

첨부 repair_v13.py 대비 유일한 차이:
  - 시총 필터를 '스냅샷(마지막 행) 정적 컷' → '매매 전날 기준 일별 cross-section 상위 10%'
    로 변경 (룩어헤드 제거).

조건 (표준):
  - K=0.7 돌파 · 거래대금 5일평균 대비 300% 이상 · 전일종가 > 전일 MA5
  - 매매 전날(D-1) 기준 시총 상위 10% (일별 cross-section)
  - 매도 익일 시가 · 비용 1%
  - 뱅크롤: 시작 1억 · 아침 시가 매도 후 ÷10 슬롯 · 일 10종 상한 · 랜덤(seed)

뱅크롤 시뮬레이션·결과 집계는 repair_v13_bankroll 의 함수를 그대로 재사용.
"""

from __future__ import annotations

import argparse
import os

import pandas as pd
from tqdm import tqdm

import repair_v13_bankroll as rv

DATA_DIR = "data/raw"
WARMUP_DAYS = 15

COL_MAP = {
    "날짜": "Date",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="repair_v13 baseline (point-in-time 시총)")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default="2026-05-31")
    p.add_argument("--data-dir", default=DATA_DIR)
    p.add_argument("--seed", type=int, default=rv.RANDOM_SEED)
    p.add_argument("--K", type=float, default=0.7)
    p.add_argument("--vol-mult", type=float, default=3.0, help="거래대금 배수 (3.0=300%%)")
    p.add_argument("--mcap-ratio", type=float, default=0.1, help="시총 상위 비율 (0.1=10%%)")
    p.add_argument("--mcap-ratio-hi", type=float, default=None,
                   help="시총 밴드 하단 비율 (예: 0.2 → 상위 mcap-ratio%%~mcap-ratio-hi%%)")
    p.add_argument("--sell-close", action="store_true", help="매도를 당일 종가로 (기본: 익일 시가)")
    p.add_argument("--vol-lag", type=int, default=0,
                   help="거래대금 폭발 기준 시점 (0=당일, 1=전일 시프트 PIT)")
    p.add_argument("--no-volume", action="store_true", help="거래대금 필터 제거")
    p.add_argument("--no-ma5", action="store_true", help="MA5 시장 필터 미적용")
    p.add_argument("--buy-close", action="store_true",
                   help="매수 체결가를 돌파가(target) 대신 당일 종가로")
    p.add_argument("--no-breakout", action="store_true",
                   help="고가≥시가+(전일고−저)×K 돌파 조건 미적용")
    p.add_argument("--mcap-bottom", action="store_true", help="시총 하위 mcap-ratio% (기본: 상위)")
    p.add_argument("--mcap-lag", type=int, default=1, help="시총 기준 시점 (1=전날)")
    p.add_argument("--cost", type=float, default=(1 - rv.COST_MULT) * 100,
                   help="거래비용 %% (기본 1)")
    p.add_argument("--bankroll", type=float, default=rv.INITIAL_BANKROLL, help="시작 뱅크롤(원)")
    p.add_argument("--slots", type=int, default=rv.MAX_DAILY_POSITIONS,
                   help="슬롯 수 = 일 최대 종목")
    p.add_argument("--budget", type=float, default=rv.BUDGET, help="고정모드 1회 매수액(원)")
    return p.parse_args()


def _load_frame(
    filename: str, data_dir: str, load_from: pd.Timestamp, end_ts: pd.Timestamp
) -> tuple[str, pd.DataFrame] | None:
    ticker = filename.replace(".csv", "")
    try:
        df = pd.read_csv(os.path.join(data_dir, filename), encoding="utf-8-sig")
        df.rename(columns=COL_MAP, inplace=True)
        if "MarketCap" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        cols = ["Open", "High", "Low", "Close", "Volume"]
        df[cols] = df[cols].apply(pd.to_numeric, errors="coerce")
        df["MarketCap"] = pd.to_numeric(df["MarketCap"], errors="coerce")
        df = df.dropna(subset=cols + ["Date"]).sort_values("Date").reset_index(drop=True)
        df = df[(df["Date"] >= load_from) & (df["Date"] <= end_ts)]
        if len(df) < 10:
            return None
        return ticker, df[["Date", "Open", "High", "Low", "Close", "Volume", "MarketCap"]]
    except Exception:
        return None


def _build_daily_cutoffs(
    frames: dict[str, pd.DataFrame],
    ratio: float,
    ratio_hi: float | None = None,
    *,
    side: str = "top",
) -> pd.DataFrame:
    """일별 시총 커트라인 산출.

    - side=top, ratio_hi is None: 상위 ratio% (mcap >= quantile(1-ratio)).
    - side=top, ratio_hi 지정: **밴드**. quantile(1-ratio_hi) <= mcap < quantile(1-ratio).
    - side=bottom: 하위 ratio% (mcap <= quantile(ratio)).
    반환: DataFrame(index=Date), 컬럼 mcap_cut_lo(포함 하한), mcap_cut_hi(제외 상한, 밴드 아닐 땐 NaN).
    """
    chunks = []
    for df in frames.values():
        sub = df[["Date", "MarketCap"]].dropna(subset=["MarketCap"])
        if not sub.empty:
            chunks.append(sub)
    if not chunks:
        return pd.DataFrame(columns=["mcap_cut_lo", "mcap_cut_hi"])
    allcaps = pd.concat(chunks, ignore_index=True)
    grp = allcaps.groupby("Date")["MarketCap"]
    if side == "bottom":
        bottom_bound = grp.quantile(ratio)  # 하위 ratio% 상한
        out = pd.DataFrame({"mcap_cut_lo": float("-inf"), "mcap_cut_hi": bottom_bound})
        return out
    top_bound = grp.quantile(1.0 - ratio)  # 상위 ratio% 경계
    if ratio_hi is None:
        out = pd.DataFrame({"mcap_cut_lo": top_bound})
        out["mcap_cut_hi"] = float("nan")
    else:
        wide_bound = grp.quantile(1.0 - ratio_hi)  # 상위 ratio_hi% 경계(더 낮은 시총)
        out = pd.DataFrame({"mcap_cut_lo": wide_bound, "mcap_cut_hi": top_bound})
    return out


def _extract_signals_pit(
    ticker: str,
    df: pd.DataFrame,
    cutoffs: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    *,
    K: float,
    vol_mult: float,
    use_ma5: bool,
    mcap_lag: int = 1,
    band: bool = False,
    vol_mult_hi: float | None = None,
    sell_mode: str = "next_open",
    vol_lag: int = 0,
    use_volume: bool = True,
    buy_price_mode: str = "target",
    require_breakout: bool = True,
    mcap_side: str = "top",
) -> list[dict]:
    df = df.copy()
    df["range"] = df["High"].shift(1) - df["Low"].shift(1)
    df["target"] = df["Open"] + df["range"] * K

    df["Value"] = df["Close"] * df["Volume"]
    df["Value_MA5"] = df["Value"].rolling(window=5).mean()
    if not use_volume:
        # 거래대금 필터 제거 (A2)
        df["vol_spike"] = True
    else:
        ref = df["Value_MA5"].shift(1)
        if vol_mult_hi is not None:
            # 거래대금 밴드: 5일평균 × lo <= 당일 < 5일평균 × hi
            spike = (df["Value"] >= ref * vol_mult) & (df["Value"] < ref * vol_mult_hi)
        else:
            spike = df["Value"] >= (ref * vol_mult)
        if vol_lag:
            # 조건 전체를 vol_lag일 시프트 → "전일 거래대금 폭발 + 당일 돌파" (PIT)
            spike = spike.shift(vol_lag).eq(True)
        df["vol_spike"] = spike

    df["MA5"] = df["Close"].rolling(window=5).mean()
    df["market_ok"] = df["Close"].shift(1) > df["MA5"].shift(1)

    df = df.merge(cutoffs.reset_index(), on="Date", how="left")
    # 당일 시총 자격 여부 → mcap_lag일 전(=매매 전날) 기준으로 사용
    if mcap_side == "bottom":
        mcap_ok_raw = df["MarketCap"].notna() & (df["MarketCap"] <= df["mcap_cut_hi"])
    elif band:
        # 밴드: 상위 ratio% 제외, 상위 ratio_hi%까지 포함 (lo <= mcap < hi)
        mcap_ok_raw = (
            df["MarketCap"].notna()
            & (df["MarketCap"] >= df["mcap_cut_lo"])
            & (df["MarketCap"] < df["mcap_cut_hi"])
        )
    else:
        mcap_ok_raw = df["MarketCap"].notna() & (df["MarketCap"] >= df["mcap_cut_lo"])
    # shift 시 NaN → object dtype 방지: bool 비교로 직접 캐스팅 (전날 기준 = D-1 자격)
    df["mcap_ok"] = mcap_ok_raw.shift(mcap_lag).eq(True)

    cond = df["mcap_ok"] & df["vol_spike"]
    if require_breakout:
        cond = cond & (df["High"] >= df["target"])
    if use_ma5:
        cond = cond & df["market_ok"]
    df["is_buy"] = cond

    in_period = (df["Date"] >= start_ts) & (df["Date"] <= end_ts)
    buys = df.loc[in_period & df["is_buy"]]

    signals: list[dict] = []
    for idx in buys.index:
        row = df.loc[idx]
        target = float(row["target"])
        close_px = float(row["Close"])
        if buy_price_mode == "close":
            buy_price = close_px
        else:
            buy_price = target
        if pd.isna(buy_price) or buy_price <= 0:
            continue
        if require_breakout and (pd.isna(target) or target <= 0):
            continue
        pos = df.index.get_loc(idx)
        # 익일 시가(오버나이트 매도용). 마지막 봉이면 없을 수 있음.
        next_open = float(df.iloc[pos + 1]["Open"]) if pos + 1 < len(df) else float("nan")
        if sell_mode == "same_close":
            sell_price = float(row["Close"])  # 당일 종가 매도
        else:
            if pos + 1 >= len(df) or pd.isna(next_open) or next_open <= 0:
                continue  # 익일 시가 매도 불가
            sell_price = next_open
        if pd.isna(sell_price) or sell_price <= 0:
            continue
        signals.append(
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "ticker": ticker,
                "target": round(target, 2) if pd.notna(target) else round(buy_price, 2),
                "buy_price": round(buy_price, 2),
                "next_open": round(next_open, 2) if pd.notna(next_open) else round(sell_price, 2),
                "sell_price": round(sell_price, 2),
            }
        )
    return signals


def load_frames(
    *,
    start: str = "2020-01-01",
    end: str = "2026-05-31",
    data_dir: str = DATA_DIR,
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    """raw CSV 를 한 번만 로드해 프레임 dict 반환 (여러 세트 실행 시 재사용)."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    load_from = start_ts - pd.Timedelta(days=WARMUP_DAYS + 14)

    file_list = [f for f in os.listdir(data_dir) if f.endswith(".csv")]
    if verbose:
        print(f"📌 raw 파일 로드: {len(file_list)}")

    frames: dict[str, pd.DataFrame] = {}
    iterator = tqdm(file_list, desc="load") if verbose else file_list
    for filename in iterator:
        loaded = _load_frame(filename, data_dir, load_from, end_ts)
        if loaded is not None:
            frames[loaded[0]] = loaded[1]

    if verbose:
        print(f"로드 완료: {len(frames)}종")
    return frames


def run_baseline(
    *,
    start: str = "2020-01-01",
    end: str = "2026-05-31",
    data_dir: str = DATA_DIR,
    K: float = 0.7,
    vol_mult: float = 3.0,
    vol_mult_hi: float | None = None,
    mcap_ratio: float = 0.1,
    mcap_ratio_hi: float | None = None,
    use_ma5: bool = True,
    mcap_lag: int = 1,
    sell_mode: str = "next_open",
    vol_lag: int = 0,
    use_volume: bool = True,
    buy_price_mode: str = "target",
    require_breakout: bool = True,
    mcap_side: str = "top",
    seed: int = rv.RANDOM_SEED,
    cost_mult: float = rv.COST_MULT,
    initial_bankroll: float = rv.INITIAL_BANKROLL,
    max_daily_positions: int = rv.MAX_DAILY_POSITIONS,
    budget: float = rv.BUDGET,
    frames: dict[str, pd.DataFrame] | None = None,
    verbose: bool = True,
) -> dict:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    band = mcap_ratio_hi is not None and mcap_side == "top"
    if mcap_side == "bottom":
        mcap_label = f"하위 {mcap_ratio*100:.0f}%"
    elif band:
        mcap_label = f"상위 {mcap_ratio*100:.0f}%~{mcap_ratio_hi*100:.0f}%"
    else:
        mcap_label = f"상위 {mcap_ratio*100:.0f}%"
    buy_label = "당일 종가" if buy_price_mode == "close" else "돌파가(target)"
    breakout_label = "적용" if require_breakout else "미적용"
    sell_label = "당일 종가" if sell_mode == "same_close" else "익일 시가"
    if not use_volume:
        vol_label = "거래대금 필터 제거"
    elif vol_mult_hi is not None:
        vol_label = f"{'전일' if vol_lag else '당일'} 거래대금 {vol_mult*100:.0f}%~{vol_mult_hi*100:.0f}%"
    else:
        vol_label = f"{'전일' if vol_lag else '당일'} 거래대금 ≥ {vol_mult*100:.0f}%"

    if verbose:
        print(f"🚀 repair_v13 baseline (point-in-time 시총) ({start} ~ {end})")
        print(f"📌 조건: K={K}, 돌파조건 {breakout_label}, 매수체결 {buy_label}, {vol_label}, MA5={use_ma5}, "
              f"매매 전날 기준 시총 {mcap_label}, 매도 {sell_label}, 비용 {(1-cost_mult)*100:.0f}%")
        print(f"📌 뱅크롤 {initial_bankroll:,.0f}원 · 슬롯 ÷{max_daily_positions} · 일 {max_daily_positions}종")

    if frames is None:
        frames = load_frames(start=start, end=end, data_dir=data_dir, verbose=verbose)

    cutoffs = _build_daily_cutoffs(
        frames, mcap_ratio, ratio_hi=mcap_ratio_hi, side=mcap_side,
    )
    if cutoffs.empty:
        if verbose:
            print("❌ 일별 시총 커트라인 산출 실패")
        return {}

    all_signals: list[dict] = []
    calendar: set[pd.Timestamp] = set()
    iterator = tqdm(frames.items(), desc="signal") if verbose else frames.items()
    for ticker, df in iterator:
        in_period = (df["Date"] >= start_ts) & (df["Date"] <= end_ts)
        calendar.update(df.loc[in_period, "Date"])
        all_signals.extend(
            _extract_signals_pit(
                ticker, df, cutoffs, start_ts, end_ts,
                K=K, vol_mult=vol_mult, vol_mult_hi=vol_mult_hi,
                use_ma5=use_ma5, mcap_lag=mcap_lag,
                band=band, sell_mode=sell_mode, vol_lag=vol_lag, use_volume=use_volume,
                buy_price_mode=buy_price_mode, require_breakout=require_breakout,
                mcap_side=mcap_side,
            )
        )

    if not all_signals:
        if verbose:
            print("❌ 조건에 부합하는 매매 신호가 없습니다.")
        return {}

    trading_days = sorted(calendar)
    fixed_df = pd.DataFrame(
        rv._signals_to_fixed_trades(all_signals, budget=budget, cost_mult=cost_mult)
    )
    executed, equity_curve, final_bankroll = rv._simulate_bankroll(
        all_signals, trading_days, seed=seed,
        initial_bankroll=initial_bankroll,
        max_daily_positions=max_daily_positions,
        cost_mult=cost_mult,
        sell_mode=sell_mode,
    )

    res = rv.assemble_results(
        fixed_df,
        executed,
        equity_curve,
        final_bankroll,
        start_ts=start_ts,
        end_ts=end_ts,
        seed=seed,
        initial_bankroll=initial_bankroll,
        max_daily_positions=max_daily_positions,
        cost_mult=cost_mult,
        budget=budget,
        params_extra={
            "start": start,
            "end": end,
            "cap_cutoff": None,  # 일별 cross-section이라 단일 컷 없음
            "symbol_count": len(frames),
            "mcap_mode": "point-in-time (전날 기준)",
            "use_ma5_filter": use_ma5,
            "K": K,
            "vol_spike_mult": vol_mult,
            "vol_mult_hi": vol_mult_hi,
            "mcap_top_ratio": mcap_ratio,
            "mcap_ratio_hi": mcap_ratio_hi,
            "mcap_lag": mcap_lag,
            "sell_mode": sell_mode,
            "vol_lag": vol_lag,
            "use_volume": use_volume,
            "buy_price_mode": buy_price_mode,
            "require_breakout": require_breakout,
            "mcap_side": mcap_side,
        },
    )

    # 신호 빈도: 하루 신호가 슬롯(max_daily_positions)을 초과한 날 비율
    from collections import Counter

    per_day = Counter(s["date"] for s in all_signals)
    signal_days = len(per_day)
    over_slot_days = sum(1 for c in per_day.values() if c > max_daily_positions)
    res["signal_freq"] = {
        "signal_days": signal_days,
        "over_slot_days": over_slot_days,
        "over_slot_pct": (over_slot_days / signal_days * 100) if signal_days else 0.0,
        "avg_signals_per_day": (len(all_signals) / signal_days) if signal_days else 0.0,
    }

    if verbose:
        b = res["bankroll"]
        fx = res["fixed"]
        print("\n" + "=" * 48)
        print("📊 [고정 1천만 원]")
        print(f"  매매 {fx['trades']:,}건 · 승률 {fx['win_rate']:.2f}% · "
              f"순손익 {fx['net_pnl']:+,.0f}원 · 매매당 {fx['avg_ret']:+.2f}%")
        print("📊 [뱅크롤 연동]")
        print(f"  체결 {b['trades']:,}건 · 승률 {b['win_rate']:.2f}%")
        print(f"  최종 뱅크롤 {b['final_bankroll']:,.0f}원 · 누적 {b['cum_ret']:+,.2f}%")
        print(f"  CAGR {b['cagr']:+,.2f}% · MDD {b['mdd']:.2f}%")
        print(f"  실제 종료일(데이터 캡): {res['params']['effective_end']}")
        print("--- 년도별 ---")
        for r in res["yearly"]:
            print(f"  {r['year']}: 매매 {r['trades']:,} · 승률 {r['win_rate']:.1f}% · "
                  f"수익률 {r['return_pct']:+,.1f}% · 뱅크롤 종료 {r['end_bankroll']:,.0f}")

    return res


def main() -> None:
    args = _parse_args()
    run_baseline(
        start=args.start,
        end=args.end,
        data_dir=args.data_dir,
        K=args.K,
        vol_mult=args.vol_mult,
        mcap_ratio=args.mcap_ratio,
        mcap_ratio_hi=args.mcap_ratio_hi,
        sell_mode="same_close" if args.sell_close else "next_open",
        vol_lag=args.vol_lag,
        use_volume=not args.no_volume,
        use_ma5=not args.no_ma5,
        buy_price_mode="close" if args.buy_close else "target",
        require_breakout=not args.no_breakout,
        mcap_side="bottom" if args.mcap_bottom else "top",
        mcap_lag=args.mcap_lag,
        seed=args.seed,
        cost_mult=1 - args.cost / 100,
        initial_bankroll=args.bankroll,
        max_daily_positions=args.slots,
        budget=args.budget,
    )


if __name__ == "__main__":
    main()
