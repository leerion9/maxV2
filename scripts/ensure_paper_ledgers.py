# -*- coding: utf-8 -*-
"""Create empty paper ledgers with UTF-8 BOM + Korean description rows."""
from __future__ import annotations

from pathlib import Path

from config.settings import settings
from core.pace_collectors import (
    LEDGER_DESC_ROW,
    LEDGER_FIELDS,
    PaperLedger,
    OD_LEDGER_DESC_ROW,
    OD_LEDGER_FIELDS,
    OpeningDriveLedger,
)


def _desc_for_mode(mode: str) -> dict:
    row = dict(LEDGER_DESC_ROW)
    if mode == "k_range":
        row["breakout_price"] = (
            "돌파가(시가+전일 고저폭xK), 매수가와의 차이=슬리피지"
        )
    else:
        row["breakout_price"] = (
            "돌파가(전일 고가), 매수가와의 차이=슬리피지"
        )
    return row


def main() -> None:
    log_dir = settings.pace_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    k_path = log_dir / "paper_ledger.csv"
    ph_path = log_dir / "paper_ledger_prev_high.csv"
    od_path = log_dir / "paper_ledger_opening_drive.csv"

    # Ensure K ledger keeps schema (already exists with trades).
    PaperLedger(path=k_path, settings=settings, desc_row=_desc_for_mode("k_range"))
    PaperLedger(path=ph_path, settings=settings, desc_row=_desc_for_mode("prev_high"))
    OpeningDriveLedger(path=od_path, settings=settings)

    for p in (k_path, ph_path, od_path):
        raw = p.read_bytes()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        text = raw.decode("utf-8-sig")
        lines = text.splitlines()
        print(f"=== {p.name} ===")
        print(f"bom={has_bom} size={p.stat().st_size}")
        for i, line in enumerate(lines[:3]):
            print(f"L{i+1}: {line[:120]}")
        # Korean sanity: description row must contain Hangul
        if len(lines) >= 2 and any("\uac00" <= ch <= "\ud7a3" for ch in lines[1]):
            print("korean_ok=True")
        else:
            print("korean_ok=False")


if __name__ == "__main__":
    main()
