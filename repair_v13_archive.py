"""repair_v13 백테스트 — 00_archive (merged + features) 데이터 버전.

- OHLCV: data/naver_daily_archive/merged/{symbol}.json
- sidecar: data/naver_daily_archive/features/{symbol}.parquet
- 시총: 일별 cross-section 상위 10% (market_cap)
"""

from __future__ import annotations

import argparse
import json
import random
from math import floor
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

# --- 전략 파라미터 (첨부 repair_v13.py 와 동일) ---
K = 0.7
VOL_SPIKE_MULT = 3.0
MCAP_TOP_RATIO = 0.1  # 상위 10% → 90 percentile
COST_MULT = 0.99
BUDGET = 10_000_000
INITIAL_BANKROLL = 100_000_000
MAX_DAILY_POSITIONS = 10
RANDOM_SEED = 42

MCAP_EDGE_SKIP = {"301410", "422260", "461270", "550043"}

ARCHIVE_BASE = Path(r"c:\cursor\00_archive\data\naver_daily_archive")
MERGED_DIR = ARCHIVE_BASE / "merged"
FEATURES_DIR = ARCHIVE_BASE / "features"

WARMUP_DAYS = 10


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="repair_v13 archive backtest")
    p.add_argument("--start", default="2024-01-01", help="백테스트 시작 (YYYY-MM-DD)")
    p.add_argument("--end", default="2025-12-31", help="백테스트 종료 (YYYY-MM-DD)")
    p.add_argument(
        "--archive-base",
        default=str(ARCHIVE_BASE),
        help="00_archive data root (naver_daily_archive)",
    )
    p.add_argument(
        "--output",
        default="backtest_archive_trades.csv",
        help="매매明细 CSV (고정 1천만 원 모드)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=RANDOM_SEED,
        help="당일 10건 초과 시 랜덤 추출 시드",
    )
    return p.parse_args()


def _list_symbols(merged_dir: Path) -> list[str]:
    return sorted(p.stem for p in merged_dir.glob("*.json"))


def _load_symbol_frame(symbol: str, merged_dir: Path, features_dir: Path) -> pd.DataFrame | None:
    merged_path = merged_dir / f"{symbol}.json"
    feat_path = features_dir / f"{symbol}.parquet"
    if not merged_path.exists() or not feat_path.exists():
        return None

    payload = json.loads(merged_path.read_text(encoding="utf-8"))
    bars = payload.get("bars") or []
    if not bars:
        return None

    ohlcv = pd.DataFrame(bars)
    ohlcv = ohlcv.rename(
        columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    ohlcv["Date"] = pd.to_datetime(ohlcv["Date"], format="%Y%m%d")
    for c in ("Open", "High", "Low", "Close", "Volume"):
        ohlcv[c] = pd.to_numeric(ohlcv[c], errors="coerce")

    feat = pd.read_parquet(feat_path)
    if feat.index.name == "date":
        feat = feat.reset_index()
    feat["Date"] = pd.to_datetime(feat["date"].astype(str), format="%Y%m%d")
    feat = feat.drop(columns=["date"], errors="ignore")

    df = ohlcv.merge(feat, on="Date", how="inner")
    df["symbol"] = symbol
    return df.sort_values("Date").reset_index(drop=True)


def _build_daily_mcap_cutoffs(frames: dict[str, pd.DataFrame]) -> pd.Series:
    """일별 market_cap 90 percentile (상위 10% 커트라인)."""
    chunks: list[pd.DataFrame] = []
    for sym, df in frames.items():
        if sym in MCAP_EDGE_SKIP:
            continue
        sub = df[["Date", "market_cap"]].dropna(subset=["market_cap"])
        if not sub.empty:
            chunks.append(sub)

    if not chunks:
        return pd.Series(dtype=float)

    all_caps = pd.concat(chunks, ignore_index=True)
    q = 1.0 - MCAP_TOP_RATIO
    cutoffs = all_caps.groupby("Date")["market_cap"].quantile(q)
    cutoffs.name = "mcap_cutoff"
    return cutoffs


def _apply_signals(df: pd.DataFrame, cutoffs: pd.Series) -> pd.DataFrame:
    df = df.copy()
    df["range"] = df["High"].shift(1) - df["Low"].shift(1)
    df["target"] = df["Open"] + df["range"] * K

    # features sidecar (없으면 OHLCV로 fallback)
    if "trading_value" in df.columns:
        df["Value"] = df["trading_value"]
    else:
        df["Value"] = df["Close"] * df["Volume"]
    if "value_ma5" in df.columns:
        df["Value_MA5"] = df["value_ma5"]
    else:
        df["Value_MA5"] = df["Value"].rolling(window=5).mean()

    if "close_ma5" in df.columns:
        df["MA5"] = df["close_ma5"]
    else:
        df["MA5"] = df["Close"].rolling(window=5).mean()

    df["vol_spike"] = df["Value"] >= (df["Value_MA5"].shift(1) * VOL_SPIKE_MULT)
    df["market_ok"] = df["Close"].shift(1) > df["MA5"].shift(1)

    df = df.merge(cutoffs.reset_index(), on="Date", how="left")
    df["mcap_ok"] = df["market_cap"].notna() & (df["market_cap"] >= df["mcap_cutoff"])

    df["is_buy"] = (
        df["mcap_ok"]
        & (df["High"] >= df["target"])
        & df["vol_spike"]
        & df["market_ok"]
    )
    return df


def _extract_signals(df: pd.DataFrame, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    signals: list[dict] = []
    in_period = (df["Date"] >= start) & (df["Date"] <= end)
    buys = df.loc[in_period & df["is_buy"]]

    for idx in buys.index:
        row = df.loc[idx]
        target = float(row["target"])
        if target <= 0 or pd.isna(target):
            continue
        pos = df.index.get_loc(idx)
        if pos + 1 >= len(df):
            continue
        nxt = df.iloc[pos + 1]
        next_open = float(nxt["Open"])
        if pd.isna(next_open) or next_open <= 0:
            continue

        signals.append(
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "ticker": symbol,
                "target": round(target, 2),
                "next_open": round(next_open, 2),
                "market_cap": row.get("market_cap"),
                "mcap_cutoff": row.get("mcap_cutoff"),
            }
        )
    return signals


def _signals_to_fixed_trades(signals: list[dict]) -> list[dict]:
    trades: list[dict] = []
    for sig in signals:
        target = float(sig["target"])
        shares = int(floor(BUDGET / target))
        if shares < 1:
            continue
        buy_amt = shares * target
        sell_amt = shares * float(sig["next_open"]) * COST_MULT
        pnl = sell_amt - buy_amt
        ret = sell_amt / buy_amt - 1.0
        trades.append(
            {
                **sig,
                "shares": shares,
                "buy_amt": round(buy_amt, 0),
                "sell_amt": round(sell_amt, 0),
                "pnl": round(pnl, 0),
                "ret": ret,
            }
        )
    return trades


def _simulate_bankroll(
    signals: list[dict],
    trading_days: list[pd.Timestamp],
    *,
    seed: int,
) -> tuple[list[dict], list[dict], float]:
    """뱅크롤 연동 시뮬: 아침 시가 매도 후 평가 · 슬롯=뱅크롤/10 · 일 10종 상한."""
    by_date: dict[str, list[dict]] = {}
    for sig in signals:
        by_date.setdefault(sig["date"], []).append(sig)

    rng = random.Random(seed)
    bankroll = float(INITIAL_BANKROLL)
    open_positions: list[dict] = []
    executed: list[dict] = []
    equity_curve: list[dict] = []

    for day in trading_days:
        day_str = day.strftime("%Y-%m-%d")

        for pos in open_positions:
            sell_amt = pos["shares"] * pos["next_open"] * COST_MULT
            bankroll += sell_amt
            pnl = sell_amt - pos["buy_amt"]
            executed.append(
                {
                    **{k: pos[k] for k in ("date", "ticker", "target", "next_open")},
                    "shares": pos["shares"],
                    "buy_amt": round(pos["buy_amt"], 0),
                    "sell_amt": round(sell_amt, 0),
                    "pnl": round(pnl, 0),
                    "ret": sell_amt / pos["buy_amt"] - 1.0,
                    "slot_budget": round(pos["slot_budget"], 0),
                }
            )
        open_positions.clear()

        equity_curve.append({"date": day_str, "bankroll": round(bankroll, 0)})

        day_signals = list(by_date.get(day_str, []))
        if len(day_signals) > MAX_DAILY_POSITIONS:
            day_signals = rng.sample(day_signals, MAX_DAILY_POSITIONS)

        slot_budget = bankroll / MAX_DAILY_POSITIONS
        for sig in day_signals:
            target = float(sig["target"])
            shares = int(floor(slot_budget / target))
            if shares < 1:
                continue
            buy_amt = shares * target
            if buy_amt > bankroll:
                continue
            bankroll -= buy_amt
            open_positions.append(
                {
                    "date": day_str,
                    "ticker": sig["ticker"],
                    "target": target,
                    "next_open": float(sig["next_open"]),
                    "shares": shares,
                    "buy_amt": buy_amt,
                    "slot_budget": slot_budget,
                }
            )

    for pos in open_positions:
        sell_amt = pos["shares"] * pos["next_open"] * COST_MULT
        bankroll += sell_amt
        pnl = sell_amt - pos["buy_amt"]
        executed.append(
            {
                **{k: pos[k] for k in ("date", "ticker", "target", "next_open")},
                "shares": pos["shares"],
                "buy_amt": round(pos["buy_amt"], 0),
                "sell_amt": round(sell_amt, 0),
                "pnl": round(pnl, 0),
                "ret": sell_amt / pos["buy_amt"] - 1.0,
                "slot_budget": round(pos["slot_budget"], 0),
            }
        )

    return executed, equity_curve, bankroll


def _calc_mdd(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    peak = float(equity_curve[0]["bankroll"])
    mdd = 0.0
    for row in equity_curve:
        br = float(row["bankroll"])
        peak = max(peak, br)
        if peak > 0:
            mdd = max(mdd, (peak - br) / peak)
    return mdd


def _yearly_bankroll_stats(
    executed: list[dict],
    equity_curve: list[dict],
    *,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    final_bankroll: float,
) -> list[dict]:
    eq = {pd.Timestamp(r["date"]): float(r["bankroll"]) for r in equity_curve}
    days_sorted = sorted(eq.keys())
    edf = pd.DataFrame(executed)
    years = list(range(start_ts.year, end_ts.year + 1))
    rows: list[dict] = []

    for year in years:
        year_trades = edf[edf["date"].str.startswith(str(year))] if not edf.empty else edf
        trade_count = len(year_trades)
        if trade_count:
            wins = year_trades[year_trades["pnl"] > 0]
            win_rate = len(wins) / trade_count * 100
        else:
            win_rate = 0.0

        if year == start_ts.year:
            start_br = float(INITIAL_BANKROLL)
        else:
            first_day = next((d for d in days_sorted if d.year == year), None)
            start_br = eq[first_day] if first_day is not None else float(INITIAL_BANKROLL)

        if year == end_ts.year:
            end_br = final_bankroll
        else:
            first_next = next((d for d in days_sorted if d.year == year + 1), None)
            end_br = eq[first_next] if first_next is not None else final_bankroll

        year_ret = (end_br / start_br - 1.0) if start_br > 0 else 0.0
        rows.append(
            {
                "year": year,
                "trades": trade_count,
                "win_rate": win_rate,
                "return_pct": year_ret * 100,
                "start_bankroll": round(start_br, 0),
                "end_bankroll": round(end_br, 0),
            }
        )
    return rows


def _print_fixed_summary(tdf: pd.DataFrame, *, start: str, end: str, symbol_count: int) -> None:
    print("\n" + "=" * 48)
    print("📊 [고정 1천만 원] archive 백테스트 결과")
    print(f"   기간: {start} ~ {end}")
    print(f"   데이터: {ARCHIVE_BASE}")
    print(f"   시총: 일별 cross-section 상위 {MCAP_TOP_RATIO*100:.0f}%")
    print(f"   로드 종목: {symbol_count}")
    print("=" * 48)

    if tdf.empty:
        print("❌ 매매 없음")
        return

    wins = tdf[tdf["pnl"] > 0]
    daily = tdf.groupby("date").size()

    print(f"매매 건수: {len(tdf):,}건")
    print(f"매매일: {len(daily):,}일")
    print(f"순손익: {tdf.pnl.sum():,.0f}원")
    print(f"  이익: {wins.pnl.sum():,.0f}원 ({len(wins):,}건)")
    print(f"  손실: {tdf.pnl.sum() - wins.pnl.sum():,.0f}원 ({len(tdf) - len(wins):,}건)")
    print(f"승률: {len(wins) / len(tdf) * 100:.2f}%")
    print(f"매매당 평균 수익률: {tdf.ret.mean() * 100:.2f}%")
    print(f"일평균 매수 건수: {daily.mean():.2f}건/일")


def _print_bankroll_summary(
    executed: list[dict],
    equity_curve: list[dict],
    *,
    start: str,
    end: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    final_bankroll: float,
    seed: int,
) -> None:
    print("\n" + "=" * 48)
    print("📊 [뱅크롤 연동] archive 백테스트 결과")
    print(f"   시작 뱅크롤: {INITIAL_BANKROLL:,.0f}원")
    print(f"   슬롯: 뱅크롤 ÷ {MAX_DAILY_POSITIONS} · 일 최대 {MAX_DAILY_POSITIONS}종")
    print(f"   평가 시점: 매일 아침 시가 매도 후")
    print(f"   10건 초과: 랜덤 추출 (seed={seed})")
    print("=" * 48)

    if not executed:
        print("❌ 체결 매매 없음")
        return

    tdf = pd.DataFrame(executed)
    wins = tdf[tdf["pnl"] > 0]
    daily = tdf.groupby("date").size()
    cum_ret = final_bankroll / INITIAL_BANKROLL - 1.0
    years = max((end_ts - start_ts).days / 365.25, 1 / 365.25)
    cagr = (final_bankroll / INITIAL_BANKROLL) ** (1 / years) - 1.0
    mdd = _calc_mdd(equity_curve)
    yearly = _yearly_bankroll_stats(
        executed,
        equity_curve,
        start_ts=start_ts,
        end_ts=end_ts,
        final_bankroll=final_bankroll,
    )

    print(f"체결 매매 건수: {len(tdf):,}건")
    print(f"매매일: {len(daily):,}일")
    print(f"승률: {len(wins) / len(tdf) * 100:.2f}%")
    print(f"최종 뱅크롤: {final_bankroll:,.0f}원")
    print(f"최종 누적 수익률: {cum_ret * 100:.2f}%")
    print(f"년평균 수익률 (CAGR): {cagr * 100:.2f}%")
    print(f"MDD: {mdd * 100:.2f}%")
    print("\n--- 년도별 ---")
    for row in yearly:
        print(
            f"  {row['year']}: "
            f"매매 {row['trades']:,}건 · "
            f"승률 {row['win_rate']:.2f}% · "
            f"수익률 {row['return_pct']:+.2f}% · "
            f"뱅크롤 {row['start_bankroll']:,.0f} → {row['end_bankroll']:,.0f}원"
        )


def run_backtest(
    start: str,
    end: str,
    archive_base: Path,
    output: str,
    *,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    global ARCHIVE_BASE, MERGED_DIR, FEATURES_DIR
    ARCHIVE_BASE = archive_base
    MERGED_DIR = archive_base / "merged"
    FEATURES_DIR = archive_base / "features"

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    load_from = start_ts - pd.Timedelta(days=WARMUP_DAYS + 14)

    symbols = _list_symbols(MERGED_DIR)
    print(f"🚀 archive 백테스트 ({start} ~ {end})")
    print(f"📌 merged/features 종목: {len(symbols)}")
    print(f"📌 조건: K={K}, 거래대금 {VOL_SPIKE_MULT*100:.0f}%, MA5, 일별 시총 상위 10%, 비용 1%")

    frames: dict[str, pd.DataFrame] = {}
    for sym in tqdm(symbols, desc="load"):
        df = _load_symbol_frame(sym, MERGED_DIR, FEATURES_DIR)
        if df is None or df.empty:
            continue
        df = df[(df["Date"] >= load_from) & (df["Date"] <= end_ts)]
        if len(df) < 10:
            continue
        frames[sym] = df

    print(f"로드 완료: {len(frames)}종")
    cutoffs = _build_daily_mcap_cutoffs(frames)
    if cutoffs.empty:
        print("❌ 일별 시총 커트라인 산출 실패")
        return pd.DataFrame()

    all_signals: list[dict] = []
    for sym, df in tqdm(frames.items(), desc="signal"):
        if sym in MCAP_EDGE_SKIP:
            continue
        sig = _apply_signals(df, cutoffs)
        all_signals.extend(_extract_signals(sig, sym, start_ts, end_ts))

    fixed_trades = _signals_to_fixed_trades(all_signals)
    tdf = pd.DataFrame(fixed_trades)
    if not tdf.empty:
        tdf.to_csv(output, index=False, encoding="utf-8-sig")
        print(f"💾 저장: {output}")

    trading_days = sorted(
        d for d in cutoffs.index if start_ts <= pd.Timestamp(d) <= end_ts
    )
    br_trades, equity_curve, final_bankroll = _simulate_bankroll(
        all_signals, trading_days, seed=seed
    )

    _print_fixed_summary(tdf, start=start, end=end, symbol_count=len(frames))
    _print_bankroll_summary(
        br_trades,
        equity_curve,
        start=start,
        end=end,
        start_ts=start_ts,
        end_ts=end_ts,
        final_bankroll=final_bankroll,
        seed=seed,
    )
    return tdf


def main() -> None:
    args = _parse_args()
    run_backtest(
        start=args.start,
        end=args.end,
        archive_base=Path(args.archive_base),
        output=args.output,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
