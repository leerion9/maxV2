from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CachedSymbol:
    avg_volume_5d: int
    prev_high: int
    prev_low: int


@dataclass(frozen=True)
class UniverseCache:
    date_kst: str  # YYYYMMDD
    source: str
    top_ratio: float
    breakout_k: float
    created_at_iso: str
    symbols: Dict[str, CachedSymbol]


def today_kst_yyyymmdd(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(ZoneInfo("Asia/Seoul"))
    return dt.strftime("%Y%m%d")


def cache_path(base_dir: Path, date_kst: str) -> Path:
    return base_dir / f"universe_cache_{date_kst}.json"


def load_cache(path: Path) -> Optional[UniverseCache]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    symbols_raw = data.get("symbols", {}) or {}
    symbols: Dict[str, CachedSymbol] = {}
    for sym, row in symbols_raw.items():
        try:
            symbols[str(sym)] = CachedSymbol(
                avg_volume_5d=int(row["avg_volume_5d"]),
                prev_high=int(row["prev_high"]),
                prev_low=int(row["prev_low"]),
            )
        except Exception:  # noqa: BLE001
            continue

    if not symbols:
        return None

    return UniverseCache(
        date_kst=str(data.get("date_kst", "")),
        source=str(data.get("source", "")),
        top_ratio=float(data.get("top_ratio", 0.0) or 0.0),
        breakout_k=float(data.get("breakout_k", 0.0) or 0.0),
        created_at_iso=str(data.get("created_at_iso", "")),
        symbols=symbols,
    )


def save_cache(path: Path, cache: UniverseCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date_kst": cache.date_kst,
        "source": cache.source,
        "top_ratio": cache.top_ratio,
        "breakout_k": cache.breakout_k,
        "created_at_iso": cache.created_at_iso,
        "symbols": {
            sym: {
                "avg_volume_5d": row.avg_volume_5d,
                "prev_high": row.prev_high,
                "prev_low": row.prev_low,
            }
            for sym, row in cache.symbols.items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

