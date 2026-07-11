from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

# v4 (2026-07-11): ETF 재편입(시총 상위 10%+MA5). ETN/리츠/스팩/인프라만 이름 제외.
# v3 (2026-07-08): ETF/ETN/펀드/스팩/리츠/인프라 제외 필터 도입 — 필터 이전 캐시 무효화.
CACHE_VERSION = 4


@dataclass(frozen=True)
class CachedSymbol:
    avg_volume_5d: int
    prev_high: int
    prev_low: int
    value_ma5: int
    prev_close: int


@dataclass(frozen=True)
class UniverseCache:
    date_kst: str  # YYYYMMDD
    source: str
    top_ratio: float
    breakout_k: float
    created_at_iso: str
    symbols: Dict[str, CachedSymbol]
    cache_version: int = CACHE_VERSION


def today_kst_yyyymmdd(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(ZoneInfo("Asia/Seoul"))
    return dt.strftime("%Y%m%d")


def cache_path(base_dir: Path, date_kst: str) -> Path:
    return base_dir / f"universe_cache_{date_kst}.json"


def _parse_symbol_row(sym: str, row: dict) -> Optional[CachedSymbol]:
    try:
        avg_volume_5d = int(row["avg_volume_5d"])
        prev_high = int(row["prev_high"])
        prev_low = int(row["prev_low"])
        if "value_ma5" not in row or "prev_close" not in row:
            return None
        value_ma5 = int(row["value_ma5"])
        prev_close = int(row["prev_close"])
        return CachedSymbol(
            avg_volume_5d=avg_volume_5d,
            prev_high=prev_high,
            prev_low=prev_low,
            value_ma5=value_ma5,
            prev_close=prev_close,
        )
    except Exception:  # noqa: BLE001
        return None


def load_cache(path: Path) -> Optional[UniverseCache]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("cache_version", 0) or 0)
    if version < CACHE_VERSION:
        return None

    symbols_raw = data.get("symbols", {}) or {}
    symbols: Dict[str, CachedSymbol] = {}
    for sym, row in symbols_raw.items():
        parsed = _parse_symbol_row(str(sym), row if isinstance(row, dict) else {})
        if parsed is None:
            return None
        symbols[str(sym)] = parsed

    if not symbols:
        return None

    return UniverseCache(
        date_kst=str(data.get("date_kst", "")),
        source=str(data.get("source", "")),
        top_ratio=float(data.get("top_ratio", 0.0) or 0.0),
        breakout_k=float(data.get("breakout_k", 0.0) or 0.0),
        created_at_iso=str(data.get("created_at_iso", "")),
        symbols=symbols,
        cache_version=version,
    )


def save_cache(path: Path, cache: UniverseCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "date_kst": cache.date_kst,
        "source": cache.source,
        "top_ratio": cache.top_ratio,
        "breakout_k": cache.breakout_k,
        "created_at_iso": cache.created_at_iso,
        "value_ma5_method": "sum(close*volume)/5 over latest 5 closed sessions (KRW)",
        "symbols": {
            sym: {
                "avg_volume_5d": row.avg_volume_5d,
                "prev_high": row.prev_high,
                "prev_low": row.prev_low,
                "value_ma5": row.value_ma5,
                "prev_close": row.prev_close,
            }
            for sym, row in cache.symbols.items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
