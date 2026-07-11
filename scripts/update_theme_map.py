# -*- coding: utf-8 -*-
"""Weekly Friday post-close: refresh Naver theme map CSV."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import ROOT_DIR, settings
from core.naver_theme import build_theme_map_rows, write_theme_map_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Update Naver theme map CSV")
    parser.add_argument(
        "--out",
        type=str,
        default=str(settings.theme_map_path),
        help="Output CSV path",
    )
    parser.add_argument(
        "--max-members",
        type=int,
        default=int(settings.theme_max_members),
        help="Themes with more members are stored but marked eligible=0",
    )
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument(
        "--delay",
        type=float,
        default=float(settings.naver_http_delay_sec),
    )
    args = parser.parse_args()

    ymd = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT_DIR / out

    print(f"Fetching Naver themes → {out}")
    rows = build_theme_map_rows(
        max_members=args.max_members,
        max_pages=args.max_pages,
        delay_sec=args.delay,
        updated_ymd=ymd,
    )
    if not rows:
        raise SystemExit("No theme rows scraped — abort (keep previous CSV)")
    write_theme_map_csv(out, rows)
    n_themes = len({r["theme_id"] for r in rows})
    n_elig = len({r["theme_id"] for r in rows if r.get("eligible") == "1"})
    n_sym = len({r["symbol"] for r in rows})
    print(
        f"OK updated_ymd={ymd} themes={n_themes} eligible_themes={n_elig} "
        f"symbol_rows={len(rows)} unique_symbols={n_sym}"
    )


if __name__ == "__main__":
    main()
