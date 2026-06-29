"""뱅크롤 연동 체결·매매 이벤트를 시간순 CSV로 내보냄 (수동 검증용)."""

from __future__ import annotations

import argparse
import importlib.util
import random
from math import floor
from pathlib import Path

import pandas as pd


def _load_rv():
    spec = importlib.util.spec_from_file_location("rv", Path(__file__).with_name("repair_v13_archive.py"))
    rv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rv)
    return rv


def export_bankroll_trades(
    *,
    start: str,
    end: str,
    output: Path,
    archive_base: Path,
    seed: int,
) -> pd.DataFrame:
    rv = _load_rv()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    load_from = start_ts - pd.Timedelta(days=rv.WARMUP_DAYS + 14)

    merged_dir = archive_base / "merged"
    features_dir = archive_base / "features"

    frames: dict[str, pd.DataFrame] = {}
    for sym in rv._list_symbols(merged_dir):
        df = rv._load_symbol_frame(sym, merged_dir, features_dir)
        if df is None or df.empty:
            continue
        df = df[(df["Date"] >= load_from) & (df["Date"] <= end_ts)]
        if len(df) < 10:
            continue
        frames[sym] = df

    cutoffs = rv._build_daily_mcap_cutoffs(frames)
    signals: list[dict] = []
    for sym, df in frames.items():
        if sym in rv.MCAP_EDGE_SKIP:
            continue
        sig = rv._apply_signals(df, cutoffs)
        signals.extend(rv._extract_signals(sig, sym, start_ts, end_ts))

    trading_days = sorted(d for d in cutoffs.index if start_ts <= pd.Timestamp(d) <= end_ts)
    day_index = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(trading_days)}

    by_date: dict[str, list[dict]] = {}
    for sig in signals:
        by_date.setdefault(sig["date"], []).append(sig)

    rng = random.Random(seed)
    bankroll = float(rv.INITIAL_BANKROLL)
    open_positions: list[dict] = []
    rows: list[dict] = []
    seq = 0

    for day in trading_days:
        day_str = day.strftime("%Y-%m-%d")
        morning_br = bankroll

        if open_positions:
            running = morning_br
            for pos in open_positions:
                sell_amt = pos["shares"] * pos["next_open"] * rv.COST_MULT
                pnl = sell_amt - pos["buy_amt"]
                ret = sell_amt / pos["buy_amt"] - 1.0
                running += sell_amt
                seq += 1
                rows.append(
                    {
                        "seq": seq,
                        "event": "sell",
                        "buy_date": pos["date"],
                        "sell_date": day_str,
                        "ticker": pos["ticker"],
                        "target": pos["target"],
                        "next_open": pos["next_open"],
                        "shares": pos["shares"],
                        "buy_amt": round(pos["buy_amt"], 0),
                        "sell_amt": round(sell_amt, 0),
                        "pnl": round(pnl, 0),
                        "ret_pct": round(ret * 100, 4),
                        "slot_budget": round(pos["slot_budget"], 0),
                        "morning_bankroll": round(morning_br, 0),
                        "bankroll_after_event": round(running, 0),
                    }
                )
            bankroll = running
            open_positions.clear()

        morning_after_sell = bankroll
        day_signals = list(by_date.get(day_str, []))
        if len(day_signals) > rv.MAX_DAILY_POSITIONS:
            day_signals = rng.sample(day_signals, rv.MAX_DAILY_POSITIONS)

        slot_budget = bankroll / rv.MAX_DAILY_POSITIONS
        for sig in day_signals:
            target = float(sig["target"])
            shares = int(floor(slot_budget / target))
            if shares < 1:
                continue
            buy_amt = shares * target
            if buy_amt > bankroll:
                continue
            bankroll -= buy_amt
            sell_idx = day_index[day_str] + 1
            sell_date = (
                trading_days[sell_idx].strftime("%Y-%m-%d")
                if sell_idx < len(trading_days)
                else "liquidate"
            )
            seq += 1
            rows.append(
                {
                    "seq": seq,
                    "event": "buy",
                    "buy_date": day_str,
                    "sell_date": sell_date,
                    "ticker": sig["ticker"],
                    "target": target,
                    "next_open": float(sig["next_open"]),
                    "shares": shares,
                    "buy_amt": round(buy_amt, 0),
                    "sell_amt": "",
                    "pnl": "",
                    "ret_pct": "",
                    "slot_budget": round(slot_budget, 0),
                    "morning_bankroll": round(morning_after_sell, 0),
                    "bankroll_after_event": round(bankroll, 0),
                }
            )
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
        sell_amt = pos["shares"] * pos["next_open"] * rv.COST_MULT
        pnl = sell_amt - pos["buy_amt"]
        ret = sell_amt / pos["buy_amt"] - 1.0
        bankroll += sell_amt
        seq += 1
        rows.append(
            {
                "seq": seq,
                "event": "sell",
                "buy_date": pos["date"],
                "sell_date": "liquidate",
                "ticker": pos["ticker"],
                "target": pos["target"],
                "next_open": pos["next_open"],
                "shares": pos["shares"],
                "buy_amt": round(pos["buy_amt"], 0),
                "sell_amt": round(sell_amt, 0),
                "pnl": round(pnl, 0),
                "ret_pct": round(ret * 100, 4),
                "slot_budget": round(pos["slot_budget"], 0),
                "morning_bankroll": "",
                "bankroll_after_event": round(bankroll, 0),
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(output, index=False, encoding="utf-8-sig")
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="뱅크롤 연동 매매 이벤트 CSV export")
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-05-31")
    p.add_argument("--output", default="backtest_archive_2026_bankroll_trades.csv")
    p.add_argument(
        "--archive-base",
        default=r"c:\cursor\00_archive\data\naver_daily_archive",
    )
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    df = export_bankroll_trades(
        start=args.start,
        end=args.end,
        output=Path(args.output),
        archive_base=Path(args.archive_base),
        seed=args.seed,
    )
    sells = df[df["event"] == "sell"]
    print(f"저장: {Path(args.output).resolve()}")
    print(f"행 수: {len(df):,} (buy {len(df) - len(sells):,}, sell {len(sells):,})")
    print(f"매도 PnL 합: {sells['pnl'].astype(float).sum():,.0f}원")
    print(f"최종 bankroll_after_event: {float(df.iloc[-1]['bankroll_after_event']):,.0f}원")


if __name__ == "__main__":
    main()
