"""Daily VDU score CSV logger (overnight condensation scores)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List

from core.pace_collectors import CSV_ENCODING, _ensure_header
from core.vdu_score import VduScoreBreakdown

VDU_SCORE_FIELDS = [
    "date",
    "symbol",
    "vdu",
    "atr_sq",
    "ma_conv",
    "pp",
    "obv_div",
    "mfi_turn",
    "total_score",
    "in_pool",
]

VDU_SCORE_DESC_ROW = {
    "date": "#점수산출일(YYYYMMDD, 전일봉 기준)",
    "symbol": "종목코드",
    "vdu": "거래량 고갈(+25/0)",
    "atr_sq": "ATR 수축(+20/0)",
    "ma_conv": "이평 수렴(+15/0)",
    "pp": "Pocket Pivot(+15/0)",
    "obv_div": "OBV 다이버전스(+15/0)",
    "mfi_turn": "MFI 침체이탈(+10/0)",
    "total_score": "합계(컷 기본 70)",
    "in_pool": "당일 감시 후보 여부(true/false)",
}


def write_vdu_score_csv(
    *,
    log_dir: Path,
    ymd: str,
    scored: Dict[str, VduScoreBreakdown],
    pool: Iterable[str],
    symbol_order: List[str] | None = None,
) -> Path:
    path = log_dir / f"vdu_score_{ymd}.csv"
    log_dir.mkdir(parents=True, exist_ok=True)
    pool_set = {str(s) for s in pool}
    order = list(symbol_order) if symbol_order else sorted(scored.keys())
    # Ensure every scored symbol appears once; append any missing at end.
    seen = set(order)
    for s in scored:
        if s not in seen:
            order.append(s)

    _ensure_header(path, VDU_SCORE_FIELDS)
    # Rewrite fresh each prepare (idempotent for the day).
    with path.open("w", newline="", encoding=CSV_ENCODING) as fp:
        writer = csv.DictWriter(fp, fieldnames=VDU_SCORE_FIELDS)
        writer.writeheader()
        writer.writerow(VDU_SCORE_DESC_ROW)
        for sym in order:
            bd = scored.get(sym)
            if bd is None:
                continue
            writer.writerow(
                {
                    "date": ymd,
                    "symbol": sym,
                    "vdu": bd.vdu,
                    "atr_sq": bd.atr_sq,
                    "ma_conv": bd.ma_conv,
                    "pp": bd.pp,
                    "obv_div": bd.obv_div,
                    "mfi_turn": bd.mfi_turn,
                    "total_score": bd.total,
                    "in_pool": str(sym in pool_set).lower(),
                }
            )
    return path
