"""repair_v13 백테스트 — 스냅샷 시총(data/raw) 버전 + 뱅크롤 복리.

- OHLCV: data/raw/{ticker}.csv (네이버 sise_day, 컬럼 한글)
- 시총: 스냅샷(각 파일 마지막 행 MarketCap) 상위 10% 정적 컷 — 첨부 repair_v13.py 와 동일
- 전략: K=0.7 돌파 · 거래대금 300% 폭발 · MA5 · 비용 1%

두 모드를 한 번에 출력:
  1) [고정 1천만 원]  매 건 1,000만 원 고정 (뱅크롤 연동 없음)
  2) [뱅크롤 연동]    시작 1억 · 아침 시가 매도 후 뱅크롤÷10 슬롯 · 일 10종 상한 · 랜덤(seed)
"""

from __future__ import annotations

import argparse
import os
import random
from math import floor

import numpy as np
import pandas as pd
from tqdm import tqdm

# --- 전략 파라미터 (첨부 repair_v13.py 와 동일) ---
K = 0.7
VOL_SPIKE_MULT = 3.0
MCAP_TOP_RATIO = 0.1  # 상위 10% → 90 percentile
COST_MULT = 0.99
BUDGET = 10_000_000

# --- 뱅크롤 복리 파라미터 (지난 repair_v13_archive.py 와 동일) ---
INITIAL_BANKROLL = 100_000_000
MAX_DAILY_POSITIONS = 10
RANDOM_SEED = 42

DATA_DIR = "data/raw"

COL_MAP = {
    "날짜": "Date",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="repair_v13 snapshot backtest + bankroll")
    p.add_argument("--start", default="2024-01-01", help="백테스트 시작 (YYYY-MM-DD)")
    p.add_argument("--end", default="2025-12-31", help="백테스트 종료 (YYYY-MM-DD)")
    p.add_argument("--data-dir", default=DATA_DIR, help="raw CSV 폴더")
    p.add_argument(
        "--output",
        default="backtest_v13_snapshot_trades.csv",
        help="뱅크롤 연동 체결 CSV",
    )
    p.add_argument("--seed", type=int, default=RANDOM_SEED, help="10건 초과 시 랜덤 추출 시드")
    return p.parse_args()


def _build_snapshot_cutoff(file_list: list[str], data_dir: str) -> float:
    """각 종목 마지막 행 MarketCap → 상위 10% 커트라인 (스냅샷 시총)."""
    caps: list[float] = []
    for filename in file_list:
        try:
            temp = pd.read_csv(
                os.path.join(data_dir, filename), encoding="utf-8-sig"
            ).iloc[-1:]
            if "MarketCap" in temp.columns:
                caps.append(temp["MarketCap"].iloc[0])
        except Exception:
            continue
    return float(np.percentile(caps, 90)) if caps else 0.0


def _extract_signals(
    filename: str,
    data_dir: str,
    cap_cutoff: float,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> tuple[list[dict], list[pd.Timestamp]]:
    """한 종목의 매수 신호(date, ticker, target, next_open)와 기간 내 거래일 추출.

    첨부 repair_v13.py 와 동일한 지표·필터. 시총은 스냅샷(마지막 행) 정적 컷.
    """
    ticker = filename.replace(".csv", "")
    try:
        df = pd.read_csv(os.path.join(data_dir, filename), encoding="utf-8-sig")
        df.rename(columns=COL_MAP, inplace=True)

        # [필터 1] 스냅샷 시총 (마지막 행 기준 상위 10%)
        if "MarketCap" not in df.columns or df["MarketCap"].iloc[-1] < cap_cutoff:
            return [], []

        df["Date"] = pd.to_datetime(df["Date"])
        cols = ["Open", "High", "Low", "Close", "Volume"]
        df[cols] = df[cols].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=cols).sort_values("Date").reset_index(drop=True)

        # --- 지표 (warmup 위해 전체 기간으로 계산 후 신호만 기간 필터) ---
        df["range"] = df["High"].shift(1) - df["Low"].shift(1)
        df["target"] = df["Open"] + df["range"] * K

        df["Value"] = df["Close"] * df["Volume"]
        df["Value_MA5"] = df["Value"].rolling(window=5).mean()
        df["vol_spike"] = df["Value"] >= (df["Value_MA5"].shift(1) * VOL_SPIKE_MULT)

        df["MA5"] = df["Close"].rolling(window=5).mean()
        df["market_ok"] = df["Close"].shift(1) > df["MA5"].shift(1)

        df["is_buy"] = (df["High"] >= df["target"]) & df["vol_spike"] & df["market_ok"]

        in_period = (df["Date"] >= start_ts) & (df["Date"] <= end_ts)
        period_days = list(df.loc[in_period, "Date"])
        buys = df.loc[in_period & df["is_buy"]]

        signals: list[dict] = []
        for idx in buys.index:
            row = df.loc[idx]
            target = float(row["target"])
            if pd.isna(target) or target <= 0:
                continue
            pos = df.index.get_loc(idx)
            if pos + 1 >= len(df):
                continue  # 마지막 봉은 익일 시가 매도 불가
            next_open = float(df.iloc[pos + 1]["Open"])
            if pd.isna(next_open) or next_open <= 0:
                continue
            signals.append(
                {
                    "date": row["Date"].strftime("%Y-%m-%d"),
                    "ticker": ticker,
                    "target": round(target, 2),
                    "next_open": round(next_open, 2),
                }
            )
        return signals, period_days
    except Exception:
        return [], []


def _sig_buy_price(sig: dict) -> float:
    return float(sig.get("buy_price", sig["target"]))


def _signals_to_fixed_trades(
    signals: list[dict], *, budget: float = BUDGET, cost_mult: float = COST_MULT
) -> list[dict]:
    """[고정 매수액] 매 신호를 budget(기본 1,000만 원) 고정 매수."""
    trades: list[dict] = []
    for sig in signals:
        buy_price = _sig_buy_price(sig)
        shares = int(floor(budget / buy_price))
        if shares < 1:
            continue
        buy_amt = shares * buy_price
        sell_price = float(sig.get("sell_price", sig["next_open"]))
        sell_amt = shares * sell_price * cost_mult
        pnl = sell_amt - buy_amt
        trades.append(
            {
                **sig,
                "shares": shares,
                "buy_amt": round(buy_amt, 0),
                "sell_amt": round(sell_amt, 0),
                "pnl": round(pnl, 0),
                "ret": sell_amt / buy_amt - 1.0,
            }
        )
    return trades


def _simulate_bankroll(
    signals: list[dict],
    trading_days: list[pd.Timestamp],
    *,
    seed: int,
    initial_bankroll: float = INITIAL_BANKROLL,
    max_daily_positions: int = MAX_DAILY_POSITIONS,
    cost_mult: float = COST_MULT,
    sell_mode: str = "next_open",
) -> tuple[list[dict], list[dict], float]:
    """[뱅크롤 연동] 슬롯=뱅크롤/N · 일 N종 상한.

    - sell_mode="next_open": 익일 시가 매도(오버나이트 1일 보유). 아침에 전일 매수분 청산 후 평가.
    - sell_mode="same_close": 당일 종가 매도(오버나이트 미보유). 매수·매도 당일 완결.
    """
    same_close = sell_mode == "same_close"
    by_date: dict[str, list[dict]] = {}
    for sig in signals:
        by_date.setdefault(sig["date"], []).append(sig)

    rng = random.Random(seed)
    bankroll = float(initial_bankroll)
    open_positions: list[dict] = []
    executed: list[dict] = []
    equity_curve: list[dict] = []

    def _record(pos: dict, sell_amt: float) -> None:
        executed.append(
            {
                **{k: pos[k] for k in ("date", "ticker", "target", "next_open")},
                "shares": pos["shares"],
                "buy_amt": round(pos["buy_amt"], 0),
                "sell_amt": round(sell_amt, 0),
                "pnl": round(sell_amt - pos["buy_amt"], 0),
                "ret": sell_amt / pos["buy_amt"] - 1.0,
                "slot_budget": round(pos["slot_budget"], 0),
            }
        )

    for day in trading_days:
        day_str = day.strftime("%Y-%m-%d")

        # 1) (익일 시가 모드) 아침: 전일 매수분 시가 매도
        if not same_close:
            for pos in open_positions:
                sell_amt = pos["shares"] * pos["sell_price"] * cost_mult
                bankroll += sell_amt
                _record(pos, sell_amt)
            open_positions.clear()

        # 2) 뱅크롤 평가 (MDD·equity curve 기준) — 당일 시작 시 전량 현금
        equity_curve.append({"date": day_str, "bankroll": round(bankroll, 0)})

        # 3) 당일 신규 매수 (N건 초과 시 랜덤)
        day_signals = list(by_date.get(day_str, []))
        if len(day_signals) > max_daily_positions:
            day_signals = rng.sample(day_signals, max_daily_positions)

        slot_budget = bankroll / max_daily_positions
        for sig in day_signals:
            buy_price = _sig_buy_price(sig)
            shares = int(floor(slot_budget / buy_price))
            if shares < 1:
                continue
            buy_amt = shares * buy_price
            if buy_amt > bankroll:
                continue
            bankroll -= buy_amt
            sell_price = float(sig.get("sell_price", sig["next_open"]))
            pos = {
                "date": day_str,
                "ticker": sig["ticker"],
                "target": float(sig["target"]),
                "buy_price": buy_price,
                "next_open": float(sig["next_open"]),
                "sell_price": sell_price,
                "shares": shares,
                "buy_amt": buy_amt,
                "slot_budget": slot_budget,
            }
            if same_close:
                # 당일 종가 매도 즉시 청산 (오버나이트 미보유)
                sell_amt = shares * sell_price * cost_mult
                bankroll += sell_amt
                _record(pos, sell_amt)
            else:
                open_positions.append(pos)

    # 남은 포지션 청산 (익일 시가 모드에서 마지막 거래일 분)
    for pos in open_positions:
        sell_amt = pos["shares"] * pos["sell_price"] * cost_mult
        bankroll += sell_amt
        _record(pos, sell_amt)

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
    initial_bankroll: float = INITIAL_BANKROLL,
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
            start_br = float(initial_bankroll)
        else:
            first_day = next((d for d in days_sorted if d.year == year), None)
            start_br = eq[first_day] if first_day is not None else float(initial_bankroll)

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
    print("📊 [고정 1천만 원] repair_v13 스냅샷 백테스트 결과")
    print(f"   기간: {start} ~ {end}")
    print(f"   시총: 스냅샷(마지막 행) 상위 {MCAP_TOP_RATIO*100:.0f}%")
    print(f"   대상 종목: {symbol_count}")
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
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    final_bankroll: float,
    seed: int,
    initial_bankroll: float = INITIAL_BANKROLL,
    max_daily_positions: int = MAX_DAILY_POSITIONS,
) -> None:
    print("\n" + "=" * 48)
    print("📊 [뱅크롤 연동] repair_v13 스냅샷 백테스트 결과")
    print(f"   시작 뱅크롤: {initial_bankroll:,.0f}원")
    print(f"   슬롯: 뱅크롤 ÷ {max_daily_positions} · 일 최대 {max_daily_positions}종")
    print(f"   평가 시점: 매일 아침 시가 매도 후")
    print(f"   {max_daily_positions}건 초과: 랜덤 추출 (seed={seed})")
    print("=" * 48)

    if not executed:
        print("❌ 체결 매매 없음")
        return

    tdf = pd.DataFrame(executed)
    wins = tdf[tdf["pnl"] > 0]
    daily = tdf.groupby("date").size()
    cum_ret = final_bankroll / initial_bankroll - 1.0
    years = max((end_ts - start_ts).days / 365.25, 1 / 365.25)
    cagr = (final_bankroll / initial_bankroll) ** (1 / years) - 1.0
    mdd = _calc_mdd(equity_curve)
    yearly = _yearly_bankroll_stats(
        executed,
        equity_curve,
        start_ts=start_ts,
        end_ts=end_ts,
        final_bankroll=final_bankroll,
        initial_bankroll=initial_bankroll,
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
    *,
    start: str,
    end: str,
    data_dir: str,
    output: str,
    seed: int = RANDOM_SEED,
    budget: float = BUDGET,
    cost_mult: float = COST_MULT,
    initial_bankroll: float = INITIAL_BANKROLL,
    max_daily_positions: int = MAX_DAILY_POSITIONS,
    verbose: bool = True,
) -> dict:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    file_list = [f for f in os.listdir(data_dir) if f.endswith(".csv")]
    if verbose:
        print(f"🚀 repair_v13 스냅샷 백테스트 ({start} ~ {end})")
        print(f"📌 조건: K={K}, 거래대금 {VOL_SPIKE_MULT*100:.0f}%, MA5, 스냅샷 시총 상위 10%, 비용 1%")
        print(f"📌 raw 파일: {len(file_list)}")

    cap_cutoff = _build_snapshot_cutoff(file_list, data_dir)
    if verbose:
        print(f"📌 시총 커트라인(90p): {cap_cutoff:,.0f}원")

    all_signals: list[dict] = []
    passed_symbols: set[str] = set()
    calendar: set[pd.Timestamp] = set()
    iterator = tqdm(file_list, desc="signal") if verbose else file_list
    for filename in iterator:
        sigs, period_days = _extract_signals(
            filename, data_dir, cap_cutoff, start_ts, end_ts
        )
        if period_days:
            passed_symbols.add(filename)
            calendar.update(period_days)
        if sigs:
            all_signals.extend(sigs)

    if not all_signals:
        if verbose:
            print("❌ 조건에 부합하는 매매 신호가 없습니다.")
        return {}

    # 거래 캘린더: 시총 통과 종목 전체 거래일 합집합 (매일 아침 정산 기준)
    trading_days = sorted(calendar)

    # [고정 매수액]
    fixed_trades = _signals_to_fixed_trades(all_signals, budget=budget, cost_mult=cost_mult)
    fixed_df = pd.DataFrame(fixed_trades)

    # [뱅크롤 연동]
    executed, equity_curve, final_bankroll = _simulate_bankroll(
        all_signals, trading_days, seed=seed,
        initial_bankroll=initial_bankroll,
        max_daily_positions=max_daily_positions,
        cost_mult=cost_mult,
    )
    if executed and output:
        pd.DataFrame(executed).to_csv(output, index=False, encoding="utf-8-sig")
        if verbose:
            print(f"💾 뱅크롤 체결 내역 저장: {output}")

    if verbose:
        _print_fixed_summary(
            fixed_df, start=start, end=end, symbol_count=len(passed_symbols)
        )
        _print_bankroll_summary(
            executed,
            equity_curve,
            start_ts=start_ts,
            end_ts=end_ts,
            final_bankroll=final_bankroll,
            seed=seed,
            initial_bankroll=initial_bankroll,
            max_daily_positions=max_daily_positions,
        )

    return assemble_results(
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
            "cap_cutoff": cap_cutoff,
            "symbol_count": len(passed_symbols),
            "mcap_mode": "snapshot",
            "use_ma5_filter": True,
        },
    )


def assemble_results(
    fixed_df: pd.DataFrame,
    executed: list[dict],
    equity_curve: list[dict],
    final_bankroll: float,
    *,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    seed: int,
    params_extra: dict,
    initial_bankroll: float = INITIAL_BANKROLL,
    max_daily_positions: int = MAX_DAILY_POSITIONS,
    cost_mult: float = COST_MULT,
    budget: float = BUDGET,
) -> dict:
    """백테스트 산출물 → 리포트/PPT 공용 결과 dict (snapshot·PIT 공용)."""
    # 실제 데이터 종료일(마지막 equity 날짜) 기준으로 CAGR 계산
    if equity_curve:
        effective_end = pd.Timestamp(equity_curve[-1]["date"])
    else:
        effective_end = end_ts
    cum_ret = final_bankroll / initial_bankroll - 1.0
    years = max((effective_end - start_ts).days / 365.25, 1 / 365.25)
    cagr = (final_bankroll / initial_bankroll) ** (1 / years) - 1.0
    mdd = _calc_mdd(equity_curve)
    yearly = _yearly_bankroll_stats(
        executed,
        equity_curve,
        start_ts=start_ts,
        end_ts=effective_end,
        final_bankroll=final_bankroll,
        initial_bankroll=initial_bankroll,
    )

    fixed_summary: dict = {}
    if fixed_df is not None and not fixed_df.empty:
        wins = fixed_df[fixed_df["pnl"] > 0]
        daily = fixed_df.groupby("date").size()
        fixed_summary = {
            "trades": int(len(fixed_df)),
            "trade_days": int(len(daily)),
            "net_pnl": float(fixed_df["pnl"].sum()),
            "profit": float(wins["pnl"].sum()),
            "profit_cnt": int(len(wins)),
            "loss": float(fixed_df["pnl"].sum() - wins["pnl"].sum()),
            "loss_cnt": int(len(fixed_df) - len(wins)),
            "win_rate": float(len(wins) / len(fixed_df) * 100),
            "avg_ret": float(fixed_df["ret"].mean() * 100),
            "avg_daily": float(daily.mean()),
        }

    edf = pd.DataFrame(executed)
    bank_summary: dict = {}
    if not edf.empty:
        bwins = edf[edf["pnl"] > 0]
        bdaily = edf.groupby("date").size()
        bank_summary = {
            "trades": int(len(edf)),
            "trade_days": int(len(bdaily)),
            "win_rate": float(len(bwins) / len(edf) * 100),
            "final_bankroll": float(final_bankroll),
            "cum_ret": float(cum_ret * 100),
            "cagr": float(cagr * 100),
            "mdd": float(mdd * 100),
        }

    params = {
        "K": K,
        "vol_spike_mult": VOL_SPIKE_MULT,
        "mcap_top_ratio": MCAP_TOP_RATIO,
        "cost_mult": cost_mult,
        "initial_bankroll": initial_bankroll,
        "max_daily_positions": max_daily_positions,
        "budget": budget,
        "seed": seed,
        "effective_end": effective_end.strftime("%Y-%m-%d"),
    }
    params.update(params_extra)

    return {
        "params": params,
        "fixed": fixed_summary,
        "bankroll": bank_summary,
        "yearly": yearly,
        "equity_curve": equity_curve,
        "final_bankroll": float(final_bankroll),
    }


def main() -> None:
    args = _parse_args()
    run_backtest(
        start=args.start,
        end=args.end,
        data_dir=args.data_dir,
        output=args.output,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
