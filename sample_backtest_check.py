"""수정 직후 샘플 검증 — 전체 백테스트 전 10종목 신호 스팟체크.

사용:
    cd c:\\cursor\\02_maxV2
    $env:PYTHONIOENCODING='utf-8'
    python sample_backtest_check.py
    python sample_backtest_check.py --symbols 005930,000660,035420
"""

from __future__ import annotations

import argparse
import random

import pandas as pd

import repair_v13_baseline as bl

COMMON = dict(
    start="2020-01-01",
    end="2026-05-31",
    archive_base=bl.ARCHIVE_BASE,
)

CHECKS = [
    ("기준", {}),
    ("전일고가", {"target_mode": "prev_high"}),
    ("전일고가+거래대금X", {"target_mode": "prev_high", "use_volume": False}),
    ("시총필터X", {"use_mcap": False}),
]


def _pick_symbols(frames: dict[str, pd.DataFrame], n: int, seed: int) -> list[str]:
    pool = sorted(frames.keys())
    rng = random.Random(seed)
    return rng.sample(pool, min(n, len(pool)))


def _count_signals(
    symbols: list[str],
    frames: dict[str, pd.DataFrame],
    over: dict,
) -> tuple[int, list[dict]]:
    start_ts = pd.Timestamp(COMMON["start"])
    end_ts = pd.Timestamp(COMMON["end"])
    cutoffs = bl._build_daily_cutoffs(frames, 0.1, ratio_hi=None, side="top")
    total = 0
    samples: list[dict] = []
    for sym in symbols:
        df = frames[sym]
        sigs = bl._extract_signals_pit(
            sym, df, cutoffs, start_ts, end_ts,
            K=0.7, vol_mult=3.0, use_ma5=True,
            **over,
        )
        total += len(sigs)
        if sigs and len(samples) < 3:
            samples.append(sigs[0])
    return total, samples


def main() -> None:
    ap = argparse.ArgumentParser(description="백테스트 수정 후 샘플 검증")
    ap.add_argument("--count", type=int, default=10, help="검증 종목 수 (default 10)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--symbols", default="", help="쉼표 구분 종목코드 (미지정 시 랜덤)")
    args = ap.parse_args()

    print("▶ archive 로드...")
    frames = bl.load_frames(**COMMON, verbose=False)
    if args.symbols.strip():
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        symbols = [s for s in symbols if s in frames]
    else:
        symbols = _pick_symbols(frames, args.count, args.seed)

    print(f"▶ 샘플 종목 {len(symbols)}개: {', '.join(symbols)}\n")
    ok = True
    for label, over in CHECKS:
        total, samples = _count_signals(symbols, frames, over)
        print(f"[{label}] 신호 {total:,}건 (샘플 {len(symbols)}종)")
        for s in samples[:2]:
            print(
                f"  예: {s['date']} {s['ticker']} "
                f"target={s['target']:,.0f} buy={s['buy_price']:,.0f} "
                f"sell={s['sell_price']:,.0f}"
            )
        if total == 0 and label == "기준":
            print("  ⚠ 기준 세트 신호 0건 — 데이터·조건 확인 필요")
            ok = False
        print()

    if ok:
        print("✅ 샘플 검증 통과 — 전체 백테스트 진행 가능")
        print("   python build_backtest_ppt_sets.py --preset prev_high_buy")
    else:
        print("❌ 샘플 검증 실패 — 전체 백테스트 전 원인 확인 필요")


if __name__ == "__main__":
    main()
